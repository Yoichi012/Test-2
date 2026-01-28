import asyncio
import hashlib
import io
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, Any, List, Tuple
from functools import wraps
from contextlib import asynccontextmanager

import aiohttp
from aiohttp import ClientSession, TCPConnector
from pymongo import ReturnDocument, ASCENDING
from telegram import Update, InputFile, Message, PhotoSize, Document, InputMediaPhoto
from telegram.ext import CommandHandler, ContextTypes
from telegram.error import TelegramError, NetworkError, TimedOut, BadRequest

from shivu import application, collection, db, CHARA_CHANNEL_ID, SUPPORT_CHAT
from shivu.config import Config


# ===================== SETUP FUNCTION =====================
async def setup_database_indexes():
    """Create database indexes for optimal performance"""
    try:
        # Unique index on character ID
        await collection.create_index([("id", ASCENDING)], unique=True, background=True)
        
        # Regular index on file_hash for fast lookups
        await collection.create_index([("file_hash", ASCENDING)], background=True)
        
        # Index on rarity for filtering
        await collection.create_index([("rarity", ASCENDING)], background=True)
        
        # Index on uploader_id for user queries
        await collection.create_index([("uploader_id", ASCENDING)], background=True)
        
        print("✅ Database indexes created successfully")
    except Exception as e:
        print(f"⚠️ Failed to create indexes: {e}")


# ===================== ENUMS =====================

class MediaType(Enum):
    """Allowed media types - only photo and document"""
    PHOTO = "photo"
    DOCUMENT = "document"

    @classmethod
    def from_telegram_message(cls, message) -> Optional['MediaType']:
        """Detect media type from Telegram message"""
        if message.photo:
            return cls.PHOTO
        elif message.document:
            mime_type = message.document.mime_type or ''
            if mime_type.startswith('image/'):
                return cls.DOCUMENT
        return None


class RarityLevel(Enum):
    """Rarity levels (1-15) matching Code A"""
    COMMON = (1, "⚪ ᴄᴏᴍᴍᴏɴ")
    RARE = (2, "🔵 ʀᴀʀᴇ")
    LEGENDARY = (3, "🟡 ʟᴇɢᴇɴᴅᴀʀʏ")
    SPECIAL = (4, "💮 ꜱᴘᴇᴄɪᴀʟ")
    ANCIENT = (5, "👹 ᴀɴᴄɪᴇɴᴛ")
    CELESTIAL = (6, "🎐 ᴄᴇʟᴇꜱᴛɪᴀʟ")
    EPIC = (7, "🔮 ᴇᴘɪᴄ")
    COSMIC = (8, "🪐 ᴄᴏꜱᴍɪᴄ")
    NIGHTMARE = (9, "⚰️ ɴɪɢʜᴛᴍᴀʀᴇ")
    FROSTBORN = (10, "🌬️ ꜰʀᴏꜱᴛʙᴏʀɴ")
    VALENTINE = (11, "💝 ᴠᴀʟᴇɴᴛɪɴᴇ")
    SPRING = (12, "🌸 ꜱᴘʀɪɴɢ")
    TROPICAL = (13, "🏖️ ᴛʀᴏᴘɪᴄᴀʟ")
    KAWAII = (14, "🍭 ᴋᴀᴡᴀɪɪ")
    HYBRID = (15, "🧬 ʜʏʙʀɪᴅ")

    def __init__(self, level: int, symbol: str, display: str):
        self._level = level
        self._symbol = symbol
        self._display = display

    @property
    def level(self) -> int:
        return self._level

    @property
    def symbol(self) -> str:
        return self._symbol

    @property
    def display_name(self) -> str:
        return self._display

    @classmethod
    def from_number(cls, num: int) -> Optional['RarityLevel']:
        for rarity in cls:
            if rarity.level == num:
                return rarity
        return None

    @classmethod
    def get_all(cls) -> Dict[int, str]:
        """Get all rarity levels as dict"""
        return {rarity.level: f"{rarity.symbol} {rarity.display_name}" for rarity in cls}


# ===================== DATACLASSES =====================

@dataclass(frozen=True)
class BotConfig:
    """Bot configuration"""
    MAX_FILE_SIZE: int = 20 * 1024 * 1024
    DOWNLOAD_TIMEOUT: int = 300
    UPLOAD_TIMEOUT: int = 300
    CHUNK_SIZE: int = 65536
    MAX_RETRIES: int = 3
    RETRY_DELAY: float = 1.0
    CONNECTION_LIMIT: int = 100
    CATBOX_API: str = "https://catbox.moe/user/api.php"
    ALLOWED_MIME_TYPES: Tuple[str, ...] = (
        'image/jpeg', 'image/png', 'image/webp', 'image/jpg'
    )


@dataclass
class MediaFile:
    """Represents a media file with efficient memory handling"""
    file_path: Optional[str] = None
    media_type: Optional[MediaType] = None
    filename: str = field(default="")
    mime_type: Optional[str] = None
    size: int = 0
    hash: str = field(default="")
    catbox_url: Optional[str] = None
    telegram_file_id: Optional[str] = None

    def __post_init__(self):
        if self.file_path and not self.hash:
            object.__setattr__(self, 'hash', self._compute_hash())
        if self.file_path and not self.size:
            import os
            object.__setattr__(self, 'size', os.path.getsize(self.file_path))

    def _compute_hash(self) -> str:
        """Compute SHA256 hash of file efficiently"""
        sha256_hash = hashlib.sha256()
        if self.file_path:
            with open(self.file_path, "rb") as f:
                for byte_block in iter(lambda: f.read(4096), b""):
                    sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()

    @property
    def is_valid_image(self) -> bool:
        """Check if media is a valid image"""
        if self.mime_type:
            return self.mime_type.startswith('image/')
        return self.media_type in [MediaType.PHOTO, MediaType.DOCUMENT]

    @property
    def is_valid_size(self) -> bool:
        """Check if file size is within limits"""
        return self.size <= BotConfig.MAX_FILE_SIZE

    def cleanup(self):
        """Clean up temporary file"""
        if self.file_path:
            try:
                import os
                os.unlink(self.file_path)
            except:
                pass


@dataclass
class Character:
    """Represents a character entry with integer rarity storage"""
    character_id: str
    name: str
    anime: str
    rarity: int  # Store as integer (1-15)
    media_file: MediaFile
    uploader_id: int
    uploader_name: str
    message_id: Optional[int] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for MongoDB storage"""
        return {
            'id': self.character_id,
            'name': self.name.replace('-', ' '),  # Remove hyphens for storage
            'anime': self.anime.replace('-', ' '),  # Remove hyphens for storage
            'rarity': self.rarity,  # Store as integer
            'img_url': self.media_file.catbox_url,
            'message_id': self.message_id,
            'uploader_id': self.uploader_id,
            'uploader_name': self.uploader_name,
            'file_hash': self.media_file.hash,
            'created_at': self.created_at,
            'updated_at': self.updated_at
        }

    def get_caption(self, uploader_username: str = None) -> str:
        """Generate caption for channel post"""
        rarity_obj = RarityLevel.from_number(self.rarity)
        rarity_display = f"{rarity_obj.symbol} 𝙍𝘼𝙍𝙄𝙏𝙔: {rarity_obj.display_name.lower()}" if rarity_obj else f"𝙍𝘼𝙍𝙄𝙏𝙔: {self.rarity}"
        
        # Format name and anime properly (with spaces, no hyphens)
        display_name = self.name.replace('-', ' ').title()
        display_anime = self.anime.replace('-', ' ').title()
        
        # Get uploader mention with link
        uploader_mention = f"[{self.uploader_name}](tg://user?id={self.uploader_id})"
        
        caption = (
            f"{self.character_id}: {display_name}\n"
            f"{display_anime}\n"
            f"{rarity_display}\n\n"
            f"𝑀𝑎𝑑𝑒 𝐵𝑦 ➥ {uploader_mention}"
        )
        
        return caption


# ===================== UTILITIES =====================

class SessionManager:
    """Manage aiohttp session lifecycle"""
    _session: Optional[ClientSession] = None
    _lock = asyncio.Lock()

    @classmethod
    async def get_session(cls) -> ClientSession:
        """Get or create aiohttp session"""
        if cls._session is None or cls._session.closed:
            async with cls._lock:
                if cls._session is None or cls._session.closed:
                    connector = TCPConnector(
                        limit=BotConfig.CONNECTION_LIMIT,
                        limit_per_host=30,
                        ttl_dns_cache=300
                    )
                    timeout = aiohttp.ClientTimeout(
                        total=BotConfig.DOWNLOAD_TIMEOUT,
                        connect=30,
                        sock_read=60
                    )
                    cls._session = ClientSession(
                        connector=connector,
                        timeout=timeout,
                        raise_for_status=False
                    )
        return cls._session

    @classmethod
    async def close(cls):
        """Close session"""
        if cls._session and not cls._session.closed:
            await cls._session.close()
            cls._session = None


def retry_async(max_retries: int = BotConfig.MAX_RETRIES):
    """Retry decorator for async functions"""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except (NetworkError, TimedOut, aiohttp.ClientError) as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        await asyncio.sleep(BotConfig.RETRY_DELAY * (attempt + 1))
                    continue
                except Exception as e:
                    raise e
            raise last_exception or Exception("Max retries exceeded")
        return wrapper
    return decorator


class CharacterFactory:
    """Factory for creating character objects"""
    
    @staticmethod
    def format_name(name: str) -> str:
        """Format character/anime name - keep hyphens as separators"""
        return name.strip()
    
    @staticmethod
    async def get_next_id() -> str:
        """Get next available character ID"""
        try:
            last_character = await collection.find_one(
                sort=[("id", -1)],
                projection={"id": 1}
            )
            
            if last_character and 'id' in last_character:
                try:
                    last_id = int(last_character['id'])
                    return str(last_id + 1).zfill(2)
                except ValueError:
                    pass
            
            count = await collection.count_documents({})
            return str(count + 1).zfill(2)
            
        except Exception:
            import random
            return str(random.randint(1000, 9999))


# ===================== MEDIA HANDLER =====================

class MediaHandler:
    """Handle media extraction and download"""
    
    @staticmethod
    async def extract_from_reply(message: Message) -> Optional[MediaFile]:
        """Extract media from reply message"""
        media_type = MediaType.from_telegram_message(message)
        
        if not media_type:
            return None
        
        try:
            if media_type == MediaType.PHOTO:
                photo = message.photo[-1]
                file = await message.get_bot().get_file(photo.file_id)
                filename = f"photo_{photo.file_unique_id}.jpg"
                mime_type = "image/jpeg"
                telegram_file_id = photo.file_id
                
            elif media_type == MediaType.DOCUMENT:
                doc = message.document
                if not doc.mime_type or not doc.mime_type.startswith('image/'):
                    return None
                file = await message.get_bot().get_file(doc.file_id)
                filename = doc.file_name or f"image_{doc.file_unique_id}"
                mime_type = doc.mime_type
                telegram_file_id = doc.file_id
            
            else:
                return None
            
            # Download file
            file_path = await MediaHandler._download_file(file)
            
            if not file_path:
                return None
            
            return MediaFile(
                file_path=file_path,
                media_type=media_type,
                filename=filename,
                mime_type=mime_type,
                telegram_file_id=telegram_file_id
            )
            
        except Exception as e:
            print(f"Media extraction error: {e}")
            return None
    
    @staticmethod
    async def _download_file(file) -> Optional[str]:
        """Download file to temporary location"""
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix='.tmp') as tmp_file:
                await file.download_to_drive(tmp_file.name)
                return tmp_file.name
        except Exception as e:
            print(f"Download error: {e}")
            return None


# ===================== CATBOX UPLOADER =====================

class CatboxUploader:
    """Handle Catbox uploads"""
    
    @staticmethod
    @retry_async(max_retries=3)
    async def upload(file_path: str, filename: str) -> Optional[str]:
        """Upload file to Catbox"""
        try:
            session = await SessionManager.get_session()
            
            data = aiohttp.FormData()
            data.add_field('reqtype', 'fileupload')
            data.add_field('fileToUpload', 
                          open(file_path, 'rb'),
                          filename=filename)
            
            async with session.post(BotConfig.CATBOX_API, data=data) as response:
                if response.status == 200:
                    url = await response.text()
                    return url.strip() if url else None
                return None
                
        except Exception as e:
            print(f"Catbox upload error: {e}")
            return None


# ===================== TELEGRAM UPLOADER =====================

class TelegramUploader:
    """Handle Telegram channel uploads"""
    
    @staticmethod
    async def upload_to_channel(character: Character, context: ContextTypes.DEFAULT_TYPE) -> Optional[int]:
        """Upload character to channel"""
        try:
            # Get uploader's username if available
            try:
                chat_member = await context.bot.get_chat(character.uploader_id)
                uploader_username = chat_member.username if hasattr(chat_member, 'username') else None
            except:
                uploader_username = None
            
            caption = character.get_caption(uploader_username)
            
            if character.media_file.catbox_url:
                message = await context.bot.send_photo(
                    chat_id=CHARA_CHANNEL_ID,
                    photo=character.media_file.catbox_url,
                    caption=caption,
                    parse_mode='Markdown'
                )
            elif character.media_file.telegram_file_id:
                message = await context.bot.send_photo(
                    chat_id=CHARA_CHANNEL_ID,
                    photo=character.media_file.telegram_file_id,
                    caption=caption,
                    parse_mode='Markdown'
                )
            else:
                return None
            
            return message.message_id
            
        except Exception as e:
            print(f"Channel upload error: {e}")
            return None
    
    @staticmethod
    async def update_channel_message(character: Character, context: ContextTypes.DEFAULT_TYPE, message_id: int) -> Optional[int]:
        """Update existing channel message with new image"""
        try:
            # Get uploader's username if available
            try:
                chat_member = await context.bot.get_chat(character.uploader_id)
                uploader_username = chat_member.username if hasattr(chat_member, 'username') else None
            except:
                uploader_username = None
            
            caption = character.get_caption(uploader_username)
            
            if character.media_file.catbox_url:
                # Edit media with new image
                media = InputMediaPhoto(
                    media=character.media_file.catbox_url,
                    caption=caption,
                    parse_mode='Markdown'
                )
                await context.bot.edit_message_media(
                    chat_id=CHARA_CHANNEL_ID,
                    message_id=message_id,
                    media=media
                )
            else:
                # Just update caption
                await context.bot.edit_message_caption(
                    chat_id=CHARA_CHANNEL_ID,
                    message_id=message_id,
                    caption=caption,
                    parse_mode='Markdown'
                )
            
            return message_id
            
        except Exception as e:
            print(f"Channel update error: {e}")
            return None


# ===================== COMMAND HANDLERS =====================

class UploadHandler:
    """Handles /upload command"""
    
    @staticmethod
    def format_upload_help() -> str:
        """Format upload command help message"""
        rarities_list = "\n".join([f"{k}: {v}" for k, v in RarityLevel.get_all().items()])
        
        return (
            "📤 upload command usage:\n\n"
            "format:\n"
            "/upload character-name anime-name rarity_number\n\n"
            "example:\n"
            "/upload naruto-uzumaki one-piece 4\n\n"
            "note: use hyphens (-) to separate words in names\n"
            "the hyphens will be removed when saved to database\n\n"
            "rarity levels:\n"
            f"{rarities_list}\n\n"
            "reply to a photo when using this command"
        )
    
    @staticmethod
    async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /upload command"""
        if update.effective_user.id not in Config.SUDO_USERS:
            await update.message.reply_text('🔒 ask my owner...')
            return
        
        if not update.message.reply_to_message:
            await update.message.reply_text('📸 reply to a photo required!')
            return
        
        if not context.args or len(context.args) < 3:
            await update.message.reply_text(UploadHandler.format_upload_help())
            return
        
        # Parse arguments
        char_name = context.args[0]  # e.g., "naruto-uzumaki"
        anime_name = context.args[1]  # e.g., "one-piece"
        
        try:
            rarity_num = int(context.args[2])
            rarity = RarityLevel.from_number(rarity_num)
            if not rarity:
                await update.message.reply_text(
                    f'❌ invalid rarity. please use a number between 1 and 15.\n\n'
                    f'available rarities:\n{chr(10).join([f"{k}: {v}" for k, v in RarityLevel.get_all().items()])}'
                )
                return
        except ValueError:
            await update.message.reply_text(f'❌ rarity must be a number (1-15).')
            return
        
        # Extract media
        processing_msg = await update.message.reply_text("🔄 processing...")
        
        media_file = await MediaHandler.extract_from_reply(update.message.reply_to_message)
        
        if not media_file:
            await processing_msg.edit_text("❌ invalid media! only photos and image documents are allowed.")
            return
        
        if not media_file.is_valid_image:
            await processing_msg.edit_text("❌ invalid image format!")
            media_file.cleanup()
            return
        
        if not media_file.is_valid_size:
            await processing_msg.edit_text(f"❌ file too large! max size: {BotConfig.MAX_FILE_SIZE // (1024*1024)}mb")
            media_file.cleanup()
            return
        
        # Check for duplicates
        existing = await collection.find_one({'file_hash': media_file.hash})
        if existing:
            await processing_msg.edit_text(
                f'❌ duplicate image detected!\n'
                f'this image is already uploaded as character id: {existing["id"]}'
            )
            media_file.cleanup()
            return
        
        # Get next ID
        char_id = await CharacterFactory.get_next_id()
        
        # Create character
        character = Character(
            character_id=char_id,
            name=CharacterFactory.format_name(char_name),
            anime=CharacterFactory.format_name(anime_name),
            rarity=rarity_num,
            media_file=media_file,
            uploader_id=update.effective_user.id,
            uploader_name=update.effective_user.first_name
        )
        
        # Upload to Catbox and Telegram in parallel
        await processing_msg.edit_text("🔄 uploading to catbox and channel...")
        
        catbox_url, message_id = await asyncio.gather(
            CatboxUploader.upload(media_file.file_path, media_file.filename),
            TelegramUploader.upload_to_channel(character, context)
        )
        
        if not catbox_url:
            await processing_msg.edit_text("❌ failed to upload to catbox.")
            media_file.cleanup()
            return
        
        # Update character with URLs
        character.media_file.catbox_url = catbox_url
        character.message_id = message_id
        
        # Add timestamps
        from datetime import datetime
        character.created_at = datetime.utcnow().isoformat()
        character.updated_at = character.created_at
        
        # Save to database
        try:
            await collection.insert_one(character.to_dict())
            await processing_msg.edit_text(
                f'✅ character uploaded successfully!\n\n'
                f'id: {char_id}\n'
                f'name: {character.name.replace("-", " ")}\n'
                f'anime: {character.anime.replace("-", " ")}\n'
                f'rarity: {rarity.display_name}'
            )
        except Exception as e:
            await processing_msg.edit_text(f'❌ database error: {str(e)}')
        finally:
            media_file.cleanup()


class DeleteHandler:
    """Handles /delete command"""
    
    @staticmethod
    async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /delete command"""
        if update.effective_user.id not in Config.SUDO_USERS:
            await update.message.reply_text('🔒 ask my owner...')
            return
        
        if not context.args:
            await update.message.reply_text('usage: /delete character_id')
            return
        
        char_id = context.args[0]
        
        character = await collection.find_one({'id': char_id})
        if not character:
            await update.message.reply_text('❌ character not found.')
            return
        
        # Delete from database
        await collection.delete_one({'id': char_id})
        
        # Try to delete from channel
        if 'message_id' in character and character['message_id']:
            try:
                await context.bot.delete_message(
                    chat_id=CHARA_CHANNEL_ID,
                    message_id=character['message_id']
                )
                await update.message.reply_text('✅ character deleted from database and channel.')
            except BadRequest as e:
                if "message to delete not found" in str(e).lower():
                    await update.message.reply_text('✅ character deleted from database (channel message was already gone).')
                else:
                    await update.message.reply_text(
                        f'✅ character deleted from database.\n\n⚠️ could not delete from channel: {str(e)}'
                    )
            except Exception as e:
                await update.message.reply_text(
                    f'✅ character deleted from database.\n\n⚠️ could not delete from channel: {str(e)}'
                )
        else:
            await update.message.reply_text('✅ character deleted successfully from database.')


class UpdateHandler:
    """Handles /update command"""
    
    VALID_FIELDS = ['img_url', 'name', 'anime', 'rarity']
    
    @staticmethod
    def format_update_help() -> str:
        """Format update command help message"""
        return (
            "📝 update command usage:\n\n"
            "update with value:\n"
            "/update id field newvalue\n\n"
            "update image (reply to photo):\n"
            "/update id img_url\n\n"
            "valid fields:\n"
            "img_url, name, anime, rarity\n\n"
            "examples:\n"
            "/update 12 name nezuko-kamado\n"
            "/update 12 anime demon-slayer\n"
            "/update 12 rarity 5\n"
            "/update 12 img_url (reply to image)\n\n"
            "note: use hyphens (-) for multi-word names"
        )
    
    @staticmethod
    async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /update command"""
        if update.effective_user.id not in Config.SUDO_USERS:
            await update.message.reply_text('🔒 ask my owner...')
            return
        
        if not context.args or len(context.args) < 2:
            await update.message.reply_text(UpdateHandler.format_update_help())
            return
        
        char_id = context.args[0]
        field = context.args[1]
        
        if field not in UpdateHandler.VALID_FIELDS:
            await update.message.reply_text(
                f'❌ invalid field. valid fields: {", ".join(UpdateHandler.VALID_FIELDS)}'
            )
            return
        
        character = await collection.find_one({'id': char_id})
        if not character:
            await update.message.reply_text('❌ character not found.')
            return
        
        update_data = {}
        
        if field == 'img_url':
            if len(context.args) == 2:
                if not (update.message.reply_to_message and 
                       (update.message.reply_to_message.photo or 
                        update.message.reply_to_message.document)):
                    await update.message.reply_text(
                        '📸 reply to a photo required!\n\nreply to a photo and use: /update id img_url'
                    )
                    return
                
                processing_msg = await update.message.reply_text("🔄 processing new image...")
                
                try:
                    media_file = await MediaHandler.extract_from_reply(update.message.reply_to_message)
                    
                    if not media_file or not media_file.is_valid_image:
                        await processing_msg.edit_text("❌ invalid media! only photos and image documents are allowed.")
                        return
                    
                    # Create character for channel update
                    char_for_upload = Character(
                        character_id=character['id'],
                        name=character['name'],
                        anime=character['anime'],
                        rarity=character['rarity'],
                        media_file=media_file,
                        uploader_id=update.effective_user.id,
                        uploader_name=update.effective_user.first_name
                    )
                    
                    await processing_msg.edit_text("🔄 uploading new image and updating channel...")
                    
                    # Upload to catbox and update channel
                    catbox_url, new_message_id = await asyncio.gather(
                        CatboxUploader.upload(media_file.file_path, media_file.filename),
                        TelegramUploader.update_channel_message(
                            char_for_upload, 
                            context, 
                            character.get('message_id')
                        )
                    )
                    
                    if not catbox_url:
                        await processing_msg.edit_text("❌ failed to upload to catbox.")
                        media_file.cleanup()
                        return
                    
                    update_data['img_url'] = catbox_url
                    update_data['file_hash'] = media_file.hash
                    update_data['message_id'] = new_message_id
                    
                    media_file.cleanup()
                    await processing_msg.edit_text('✅ image updated successfully!')
                    
                except Exception as e:
                    await update.message.reply_text(f'❌ failed to update image: {str(e)}')
                    return
                
            else:
                if len(context.args) < 3:
                    await update.message.reply_text('❌ missing image url. usage: /update id img_url url')
                    return
                    
                new_value = context.args[2]
                update_data['img_url'] = new_value
        
        elif field in ['name', 'anime']:
            if len(context.args) < 3:
                await update.message.reply_text(
                    f'❌ missing value. usage: /update id {field} new-value'
                )
                return
            
            new_value = context.args[2]
            # Keep hyphens in input, but remove them for storage
            update_data[field] = CharacterFactory.format_name(new_value)
            
        elif field == 'rarity':
            if len(context.args) < 3:
                await update.message.reply_text(
                    f'❌ missing rarity value. usage: /update id rarity 1-15'
                )
                return
            
            new_value = context.args[2]
            try:
                rarity_num = int(new_value)
                rarity = RarityLevel.from_number(rarity_num)
                if not rarity:
                    await update.message.reply_text(
                        f'❌ invalid rarity. please use a number between 1 and 15.'
                    )
                    return
                update_data['rarity'] = rarity_num
            except ValueError:
                await update.message.reply_text(f'❌ rarity must be a number (1-15).')
                return
        
        # Update timestamp
        from datetime import datetime
        update_data['updated_at'] = datetime.utcnow().isoformat()
        
        # Update in database
        updated_character = await collection.find_one_and_update(
            {'id': char_id},
            {'$set': update_data},
            return_document=ReturnDocument.AFTER
        )
        
        if not updated_character:
            await update.message.reply_text('❌ failed to update character in database.')
            return
        
        # Update channel message (if not img_url which was already handled)
        if field != 'img_url' and 'message_id' in updated_character:
            try:
                channel_char = Character(
                    character_id=updated_character['id'],
                    name=updated_character['name'],
                    anime=updated_character['anime'],
                    rarity=updated_character['rarity'],
                    media_file=MediaFile(catbox_url=updated_character['img_url']),
                    uploader_id=update.effective_user.id,
                    uploader_name=update.effective_user.first_name
                )
                
                await TelegramUploader.update_channel_message(
                    channel_char,
                    context,
                    updated_character['message_id']
                )
            except Exception:
                pass
        
        await update.message.reply_text('✅ character updated successfully!')


# ===================== APPLICATION SETUP =====================

# Register command handlers
application.add_handler(CommandHandler("upload", UploadHandler.handle, block=False))
application.add_handler(CommandHandler("delete", DeleteHandler.handle, block=False))
application.add_handler(CommandHandler("update", UpdateHandler.handle, block=False))


# ===================== CLEANUP =====================

async def cleanup():
    """Cleanup on shutdown"""
    await SessionManager.close()
