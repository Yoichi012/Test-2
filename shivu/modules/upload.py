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
from telegram import Update, InputFile, Message, PhotoSize, Document, InputMediaPhoto, InputMediaDocument
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
    """Allowed media types"""
    PHOTO = "photo"
    DOCUMENT = "document"
    VIDEO = "video"
    ANIMATION = "animation"

    @classmethod
    def from_telegram_message(cls, message) -> Optional['MediaType']:
        """Detect media type from Telegram message"""
        if message.photo:
            return cls.PHOTO
        elif message.document:
            mime_type = message.document.mime_type or ''
            if mime_type.startswith('image/'):
                return cls.DOCUMENT
        elif message.video:
            return cls.VIDEO
        elif message.animation:
            return cls.ANIMATION
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

    def __init__(self, level: int, display: str):
        self._level = level
        self._display = display

    @property
    def level(self) -> int:
        return self._level

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
        """Get all rarity levels as dict (matching Code A format)"""
        return {rarity.level: rarity.display_name for rarity in cls}


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
        if self.media_type in [MediaType.VIDEO, MediaType.ANIMATION]:
            return False
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
            'name': self.name,
            'anime': self.anime,
            'rarity': self.rarity,  # Store as integer
            'img_url': self.media_file.catbox_url,
            'message_id': self.message_id,
            'uploader_id': self.uploader_id,
            'uploader_name': self.uploader_name,
            'file_hash': self.media_file.hash,
            'created_at': self.created_at,
            'updated_at': self.updated_at
        }

    def get_caption(self, action: str = "Added") -> str:
        """Generate caption for channel post"""
        rarity_obj = RarityLevel.from_number(self.rarity)
        display_name = rarity_obj.display_name if rarity_obj else f"Level {self.rarity}"
        
        return (
            f"<b>{action}✨</b>\n\n"
            f"<b>🆔 ɪᴅ:</b> <code>{self.character_id}</code>\n"
            f"<b>🏷️ ɴᴀᴍᴇ:</b> <code>{self.name}</code>\n"
            f"<b>📺 ᴀɴɪᴍᴇ:</b> <code>{self.anime}</code>\n"
            f"<b>💎 ʀᴀʀɪᴛʏ:</b> <code>{display_name}</code>\n"
            f"<b>👤 ᴜᴘʟᴏᴀᴅᴇʀ:</b> <code>{self.uploader_name}</code>"
        )


# ===================== SESSION MANAGER =====================

class SessionManager:
    """Manages HTTP session with connection pooling"""
    _session: Optional[ClientSession] = None
    _lock = asyncio.Lock()

    @classmethod
    async def get_session(cls) -> ClientSession:
        """Get or create HTTP session"""
        if cls._session is None or cls._session.closed:
            async with cls._lock:
                if cls._session is None or cls._session.closed:
                    connector = TCPConnector(
                        limit=BotConfig.CONNECTION_LIMIT,
                        limit_per_host=30,
                        ttl_dns_cache=300
                    )
                    cls._session = ClientSession(
                        connector=connector,
                        timeout=aiohttp.ClientTimeout(total=BotConfig.UPLOAD_TIMEOUT)
                    )
        return cls._session

    @classmethod
    async def close(cls):
        """Close HTTP session"""
        if cls._session and not cls._session.closed:
            await cls._session.close()
            cls._session = None


# ===================== MEDIA HANDLER =====================

class MediaHandler:
    """Handles media extraction and validation"""
    
    @staticmethod
    async def extract_from_reply(message: Message) -> Optional[MediaFile]:
        """Extract media from replied message"""
        media_type = MediaType.from_telegram_message(message)
        
        if not media_type:
            return None
        
        try:
            if media_type == MediaType.PHOTO:
                file_obj = message.photo[-1]
                file_id = file_obj.file_id
                filename = f"photo_{file_obj.file_unique_id}.jpg"
                mime_type = "image/jpeg"
            elif media_type == MediaType.DOCUMENT:
                file_obj = message.document
                file_id = file_obj.file_id
                filename = file_obj.file_name or f"document_{file_obj.file_unique_id}"
                mime_type = file_obj.mime_type
            else:
                return None
            
            # Download file
            file = await file_obj.get_file()
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=f"_{filename}")
            file_path = temp_file.name
            temp_file.close()
            
            await file.download_to_drive(file_path)
            
            return MediaFile(
                file_path=file_path,
                media_type=media_type,
                filename=filename,
                mime_type=mime_type,
                telegram_file_id=file_id
            )
            
        except Exception as e:
            raise ValueError(f"Failed to extract media: {str(e)}")


# ===================== CATBOX UPLOADER =====================

class CatboxUploader:
    """Handles Catbox uploads with retry logic"""
    
    @staticmethod
    async def upload(file_path: str, filename: str) -> Optional[str]:
        """Upload file to Catbox with retry"""
        for attempt in range(BotConfig.MAX_RETRIES):
            try:
                session = await SessionManager.get_session()
                
                with open(file_path, 'rb') as f:
                    form_data = aiohttp.FormData()
                    form_data.add_field('reqtype', 'fileupload')
                    form_data.add_field('fileToUpload', f, filename=filename)
                    
                    async with session.post(BotConfig.CATBOX_API, data=form_data) as response:
                        if response.status == 200:
                            url = await response.text()
                            if url.startswith('http'):
                                return url.strip()
                        
            except Exception as e:
                if attempt < BotConfig.MAX_RETRIES - 1:
                    await asyncio.sleep(BotConfig.RETRY_DELAY * (attempt + 1))
                    continue
                    
        return None


# ===================== TELEGRAM UPLOADER =====================

class TelegramUploader:
    """Handles Telegram channel uploads"""
    
    @staticmethod
    async def upload_to_channel(
        character: Character,
        context: ContextTypes.DEFAULT_TYPE,
        telegram_file_id: str,
        is_update: bool = False
    ) -> Optional[int]:
        """
        ✨ MODIFIED: Upload character to channel ALWAYS AS PHOTO
        
        Changes:
        - Removes document condition
        - Always uses send_photo regardless of original media type
        - Converts documents to photos automatically
        """
        try:
            caption = character.get_caption("Updated" if is_update else "Added")
            
            # ✨ MAIN CHANGE: Always send as PHOTO (not document)
            message = await context.bot.send_photo(
                chat_id=CHARA_CHANNEL_ID,
                photo=telegram_file_id,
                caption=caption,
                parse_mode='HTML'
            )
            
            return message.message_id
            
        except BadRequest as e:
            error_msg = str(e).lower()
            if "not found" in error_msg or "message to edit not found" in error_msg:
                return await TelegramUploader.upload_to_channel(character, context, telegram_file_id, is_update)
            raise
        except Exception as e:
            raise ValueError(f"Failed to upload to channel: {str(e)}")

    @staticmethod
    async def update_channel_message(
        character: Character,
        context: ContextTypes.DEFAULT_TYPE,
        old_message_id: Optional[int] = None
    ) -> Optional[int]:
        """
        ✨ MODIFIED: Update existing channel message ALWAYS AS PHOTO
        
        Changes:
        - Removes document condition in edit_message_media
        - Always uses InputMediaPhoto
        """
        try:
            if not old_message_id:
                # No existing message, send new one
                return await TelegramUploader.upload_to_channel(
                    character, 
                    context, 
                    character.media_file.telegram_file_id or character.media_file.catbox_url, 
                    True
                )
            
            caption = character.get_caption("Updated")
            
            # Try to edit the media
            try:
                # ✨ MAIN CHANGE: Always use InputMediaPhoto (not InputMediaDocument)
                media = InputMediaPhoto(
                    media=character.media_file.catbox_url or character.media_file.telegram_file_id,
                    caption=caption,
                    parse_mode='HTML'
                )
                await context.bot.edit_message_media(
                    chat_id=CHARA_CHANNEL_ID,
                    message_id=old_message_id,
                    media=media
                )
                return old_message_id
                
            except BadRequest as e:
                error_msg = str(e).lower()
                # If edit_message_media fails (message too old, not found, etc.), send new message
                if "message not found" in error_msg or "message to edit not found" in error_msg or "message can't be edited" in error_msg:
                    # Send new message and return new message_id
                    return await TelegramUploader.upload_to_channel(
                        character, 
                        context, 
                        character.media_file.catbox_url or character.media_file.telegram_file_id, 
                        True
                    )
                raise
                
        except Exception as e:
            raise ValueError(f"Failed to update channel message: {str(e)}")


# ===================== CHARACTER FACTORY =====================

class CharacterFactory:
    """Creates character objects from user input"""
    
    @staticmethod
    def format_name(name: str) -> str:
        """Format character/anime name"""
        return ' '.join(word.capitalize() for word in name.split())
    
    @staticmethod
    async def create_from_command(
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        args: List[str]
    ) -> Optional[Character]:
        """Create character from /upload command"""
        if len(args) < 4:
            return None
        
        try:
            char_id = args[0]
            rarity_num = int(args[1])
            name = args[2]
            anime = ' '.join(args[3:])
            
            # Validate rarity
            rarity = RarityLevel.from_number(rarity_num)
            if not rarity:
                raise ValueError(f"Invalid rarity: {rarity_num}")
            
            # Extract media
            media_file = await MediaHandler.extract_from_reply(update.message.reply_to_message)
            if not media_file:
                raise ValueError("No valid media found")
            
            if not media_file.is_valid_image:
                raise ValueError("Only image files are allowed")
            
            if not media_file.is_valid_size:
                raise ValueError(f"File too large (max {BotConfig.MAX_FILE_SIZE / 1024 / 1024}MB)")
            
            # Create character
            from datetime import datetime
            return Character(
                character_id=char_id,
                name=CharacterFactory.format_name(name),
                anime=CharacterFactory.format_name(anime),
                rarity=rarity_num,
                media_file=media_file,
                uploader_id=update.effective_user.id,
                uploader_name=update.effective_user.first_name,
                created_at=datetime.utcnow().isoformat()
            )
            
        except (ValueError, IndexError) as e:
            raise ValueError(f"Invalid command format: {str(e)}")


# ===================== DATABASE MANAGER =====================

class DatabaseManager:
    """Handles database operations"""
    
    @staticmethod
    async def character_exists(char_id: str) -> bool:
        """Check if character exists"""
        return await collection.find_one({'id': char_id}) is not None
    
    @staticmethod
    async def duplicate_hash_exists(file_hash: str) -> Optional[Dict]:
        """Check for duplicate file hash"""
        return await collection.find_one({'file_hash': file_hash})
    
    @staticmethod
    async def save_character(character: Character) -> bool:
        """Save character to database"""
        try:
            await collection.insert_one(character.to_dict())
            return True
        except Exception as e:
            raise ValueError(f"Database error: {str(e)}")
    
    @staticmethod
    async def delete_character(char_id: str) -> Optional[Dict]:
        """Delete character from database"""
        return await collection.find_one_and_delete({'id': char_id})


# ===================== UPLOAD HANDLER =====================

class UploadHandler:
    """Handles /upload command"""
    
    @staticmethod
    def format_upload_help() -> str:
        """Format upload command help message"""
        rarities = RarityLevel.get_all()
        rarity_list = '\n'.join([f"{level}. {name}" for level, name in rarities.items()])
        
        return (
            "📤 <b>ᴜᴘʟᴏᴀᴅ ᴄᴏᴍᴍᴀɴᴅ ᴜꜱᴀɢᴇ</b>\n\n"
            "ʀᴇᴘʟʏ ᴛᴏ ᴀɴ ɪᴍᴀɢᴇ ᴡɪᴛʜ:\n"
            "<code>/upload ID RARITY NAME ANIME</code>\n\n"
            "<b>ᴇxᴀᴍᴘʟᴇ:</b>\n"
            "<code>/upload 69 5 Nezuko Demon Slayer</code>\n\n"
            f"<b>ʀᴀʀɪᴛʏ ʟᴇᴠᴇʟꜱ:</b>\n{rarity_list}"
        )
    
    @staticmethod
    async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /upload command with parallel processing"""
        if update.effective_user.id not in Config.SUDO_USERS:
            await update.message.reply_text('🔒 ᴀꜱᴋ ᴍʏ ᴏᴡɴᴇʀ...')
            return
        
        if not update.message.reply_to_message:
            await update.message.reply_text('❌ ʀᴇᴘʟʏ ᴛᴏ ᴀɴ ɪᴍᴀɢᴇ!')
            return
        
        if not context.args or len(context.args) < 4:
            await update.message.reply_text(UploadHandler.format_upload_help(), parse_mode='HTML')
            return
        
        processing_msg = await update.message.reply_text('🔄 <b>ᴘʀᴏᴄᴇꜱꜱɪɴɢ...</b>', parse_mode='HTML')
        
        try:
            # Create character object
            character = await CharacterFactory.create_from_command(update, context, context.args)
            
            if not character:
                await processing_msg.edit_text('❌ ɪɴᴠᴀʟɪᴅ ᴄᴏᴍᴍᴀɴᴅ ꜰᴏʀᴍᴀᴛ!')
                return
            
            # Check for duplicates
            if await DatabaseManager.character_exists(character.character_id):
                await processing_msg.edit_text(f'❌ ᴄʜᴀʀᴀᴄᴛᴇʀ ɪᴅ <code>{character.character_id}</code> ᴀʟʀᴇᴀᴅʏ ᴇxɪꜱᴛꜱ!', parse_mode='HTML')
                character.media_file.cleanup()
                return
            
            duplicate = await DatabaseManager.duplicate_hash_exists(character.media_file.hash)
            if duplicate:
                await processing_msg.edit_text(
                    f'⚠️ ᴅᴜᴘʟɪᴄᴀᴛᴇ ɪᴍᴀɢᴇ!\n\n'
                    f'ᴀʟʀᴇᴀᴅʏ ᴜꜱᴇᴅ ʙʏ:\n'
                    f'🆔 <code>{duplicate["id"]}</code>\n'
                    f'🏷️ <code>{duplicate["name"]}</code>\n'
                    f'📺 <code>{duplicate["anime"]}</code>',
                    parse_mode='HTML'
                )
                character.media_file.cleanup()
                return
            
            # Update progress
            await processing_msg.edit_text('⬆️ <b>ᴜᴘʟᴏᴀᴅɪɴɢ ᴛᴏ ᴄᴀᴛʙᴏx ᴀɴᴅ ᴄʜᴀɴɴᴇʟ...</b>', parse_mode='HTML')
            
            # Parallel upload to Catbox and Telegram channel
            catbox_url, message_id = await asyncio.gather(
                CatboxUploader.upload(character.media_file.file_path, character.media_file.filename),
                TelegramUploader.upload_to_channel(character, context, character.media_file.telegram_file_id)
            )
            
            if not catbox_url:
                await processing_msg.edit_text('❌ ꜰᴀɪʟᴇᴅ ᴛᴏ ᴜᴘʟᴏᴀᴅ ᴛᴏ ᴄᴀᴛʙᴏx!')
                character.media_file.cleanup()
                return
            
            if not message_id:
                await processing_msg.edit_text('❌ ꜰᴀɪʟᴇᴅ ᴛᴏ ᴜᴘʟᴏᴀᴅ ᴛᴏ ᴄʜᴀɴɴᴇʟ!')
                character.media_file.cleanup()
                return
            
            # Update character with URLs and message ID
            character.media_file.catbox_url = catbox_url
            character.message_id = message_id
            
            # Save to database
            await DatabaseManager.save_character(character)
            
            # Cleanup
            character.media_file.cleanup()
            
            # Success message
            rarity_obj = RarityLevel.from_number(character.rarity)
            await processing_msg.edit_text(
                f'✅ <b>ᴄʜᴀʀᴀᴄᴛᴇʀ ᴀᴅᴅᴇᴅ!</b>\n\n'
                f'🆔 <code>{character.character_id}</code>\n'
                f'🏷️ <code>{character.name}</code>\n'
                f'📺 <code>{character.anime}</code>\n'
                f'💎 <code>{rarity_obj.display_name}</code>\n'
                f'🔗 <a href="{catbox_url}">ɪᴍᴀɢᴇ ʟɪɴᴋ</a>',
                parse_mode='HTML'
            )
            
        except ValueError as e:
            await processing_msg.edit_text(f'❌ {str(e)}')
        except Exception as e:
            await processing_msg.edit_text(f'❌ ᴜɴᴇxᴘᴇᴄᴛᴇᴅ ᴇʀʀᴏʀ: {str(e)}')


# ===================== DELETE HANDLER =====================

class DeleteHandler:
    """Handles /delete command"""
    
    @staticmethod
    async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /delete command"""
        if update.effective_user.id not in Config.SUDO_USERS:
            await update.message.reply_text('🔒 ᴀꜱᴋ ᴍʏ ᴏᴡɴᴇʀ...')
            return
        
        if not context.args or len(context.args) != 1:
            await update.message.reply_text(
                '📝 ᴜꜱᴀɢᴇ:\n<code>/delete CHARACTER_ID</code>\n\n'
                'ᴇxᴀᴍᴘʟᴇ:\n<code>/delete 69</code>',
                parse_mode='HTML'
            )
            return
        
        char_id = context.args[0]
        
        # Delete from database
        character = await DatabaseManager.delete_character(char_id)
        
        if not character:
            await update.message.reply_text(f'❌ ᴄʜᴀʀᴀᴄᴛᴇʀ <code>{char_id}</code> ɴᴏᴛ ꜰᴏᴜɴᴅ.', parse_mode='HTML')
            return
        
        # Try to delete from channel
        if 'message_id' in character:
            try:
                await context.bot.delete_message(
                    chat_id=CHARA_CHANNEL_ID,
                    message_id=character['message_id']
                )
                await update.message.reply_text('✅ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴅᴇʟᴇᴛᴇᴅ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ!')
            except BadRequest as e:
                if "message to delete not found" in str(e).lower():
                    await update.message.reply_text('✅ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴅᴇʟᴇᴛᴇᴅ ꜰʀᴏᴍ ᴅᴀᴛᴀʙᴀꜱᴇ (ᴄʜᴀɴɴᴇʟ ᴍᴇꜱꜱᴀɢᴇ ᴡᴀꜱ ᴀʟʀᴇᴀᴅʏ ɢᴏɴᴇ).')
                else:
                    await update.message.reply_text(
                        f'✅ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴅᴇʟᴇᴛᴇᴅ ꜰʀᴏᴍ ᴅᴀᴛᴀʙᴀꜱᴇ.\n\n⚠️ ᴄᴏᴜʟᴅ ɴᴏᴛ ᴅᴇʟᴇᴛᴇ ꜰʀᴏᴍ ᴄʜᴀɴɴᴇʟ: {str(e)}'
                    )
            except Exception as e:
                await update.message.reply_text(
                    f'✅ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴅᴇʟᴇᴛᴇᴅ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ ꜰʀᴏᴍ ᴅᴀᴛᴀʙᴀꜱᴇ.'
                )


class UpdateHandler:
    """Handles /update command"""
    
    VALID_FIELDS = ['img_url', 'name', 'anime', 'rarity']
    
    @staticmethod
    def format_update_help() -> str:
        """Format update command help message"""
        return (
            "📝 ᴜᴘᴅᴀᴛᴇ ᴄᴏᴍᴍᴀɴᴅ ᴜꜱᴀɢᴇ:\n\n"
            "ᴜᴘᴅᴀᴛᴇ ᴡɪᴛʜ ᴠᴀʟᴜᴇ:\n"
            "/update ɪᴅ ꜰɪᴇʟᴅ ɴᴇᴡᴠᴀʟᴜᴇ\n\n"
            "ᴜᴘᴅᴀᴛᴇ ɪᴍᴀɢᴇ (ʀᴇᴘʟʏ ᴛᴏ ᴘʜᴏᴛᴏ):\n"
            "/update ɪᴅ ɪᴍɢ_ᴜʀʟ\n\n"
            "ᴠᴀʟɪᴅ ꜰɪᴇʟᴅꜱ:\n"
            "ɪᴍɢ_ᴜʀʟ, ɴᴀᴍᴇ, ᴀɴɪᴍᴇ, ʀᴀʀɪᴛʏ\n\n"
            "ᴇxᴀᴍᴘʟᴇꜱ:\n"
            "/update 12 ɴᴀᴍᴇ ɴᴇᴢᴜᴋᴏ ᴋᴀᴍᴀᴅᴏ\n"
            "/update 12 ᴀɴɪᴍᴇ ᴅᴇᴍᴏɴ ꜱʟᴀʏᴇʀ\n"
            "/update 12 ʀᴀʀɪᴛʏ 5\n"
            "/update 12 ɪᴍɢ_ᴜʀʟ ʀᴇᴘʟʏ_ɪᴍɢ"
        )
    
    @staticmethod
    async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /update command with validation fixes"""
        if update.effective_user.id not in Config.SUDO_USERS:
            await update.message.reply_text('🔒 ᴀꜱᴋ ᴍʏ ᴏᴡɴᴇʀ...')
            return
        
        if not context.args or len(context.args) < 2:
            await update.message.reply_text(UpdateHandler.format_update_help())
            return
        
        char_id = context.args[0]
        field = context.args[1]
        
        if field not in UpdateHandler.VALID_FIELDS:
            await update.message.reply_text(
                f'❌ ɪɴᴠᴀʟɪᴅ ꜰɪᴇʟᴅ. ᴠᴀʟɪᴅ ꜰɪᴇʟᴅꜱ: {", ".join(UpdateHandler.VALID_FIELDS)}'
            )
            return
        
        character = await collection.find_one({'id': char_id})
        if not character:
            await update.message.reply_text('❌ ᴄʜᴀʀᴀᴄᴛᴇʀ ɴᴏᴛ ꜰᴏᴜɴᴅ.')
            return
        
        update_data = {}
        
        if field == 'img_url':
            if len(context.args) == 2:
                if not (update.message.reply_to_message and 
                       (update.message.reply_to_message.photo or 
                        update.message.reply_to_message.document)):
                    await update.message.reply_text(
                        '📸 ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴘʜᴏᴛᴏ ʀᴇǫᴜɪʀᴇᴅ!\n\nʀᴇᴘʟʏ ᴛᴏ ᴀ ᴘʜᴏᴛᴏ ᴀɴᴅ ᴜꜱᴇ: /update id img_url'
                    )
                    return
                
                processing_msg = await update.message.reply_text("🔄 **Processing new image...**")
                
                try:
                    media_file = await MediaHandler.extract_from_reply(update.message.reply_to_message)
                    
                    if not media_file or not media_file.is_valid_image:
                        await processing_msg.edit_text("❌ Invalid media! Only photos and image documents are allowed.")
                        return
                    
                    # Create character for parallel upload
                    char_for_upload = Character(
                        character_id=character['id'],
                        name=character['name'],
                        anime=character['anime'],
                        rarity=character['rarity'],  # Already integer
                        media_file=media_file,
                        uploader_id=update.effective_user.id,
                        uploader_name=update.effective_user.first_name
                    )
                    
                    # FIXED: Use coroutines directly with asyncio.gather
                    await processing_msg.edit_text("🔄 **Uploading new image and updating channel...**")
                    
                    # Run both operations concurrently
                    catbox_url, new_message_id = await asyncio.gather(
                        CatboxUploader.upload(media_file.file_path, media_file.filename),
                        TelegramUploader.update_channel_message(
                            char_for_upload, 
                            context, 
                            character.get('message_id')
                        )
                    )
                    
                    if not catbox_url:
                        await processing_msg.edit_text("❌ Failed to upload to Catbox.")
                        media_file.cleanup()
                        return
                    
                    update_data['img_url'] = catbox_url
                    update_data['file_hash'] = media_file.hash
                    update_data['message_id'] = new_message_id
                    
                    media_file.cleanup()
                    await processing_msg.edit_text('✅ ɪᴍᴀɢᴇ ᴜᴘᴅᴀᴛᴇᴅ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ!')
                    
                except Exception as e:
                    await update.message.reply_text(f'❌ Failed to update image: {str(e)}')
                    return
                
            else:
                # Fix: Validate context.args length before accessing
                if len(context.args) < 3:
                    await update.message.reply_text('❌ Missing image URL. Usage: /update id img_url URL')
                    return
                    
                new_value = context.args[2]
                update_data['img_url'] = new_value
        
        elif field in ['name', 'anime']:
            # Fix: Validate context.args length
            if len(context.args) < 3:
                await update.message.reply_text(
                    f'❌ Missing value. Usage: /update id {field} new_value'
                )
                return
            
            new_value = context.args[2]
            update_data[field] = CharacterFactory.format_name(new_value)
            
        elif field == 'rarity':
            # Fix: Validate context.args length
            if len(context.args) < 3:
                await update.message.reply_text(
                    f'❌ Missing rarity value. Usage: /update id rarity 1-15'
                )
                return
            
            new_value = context.args[2]
            try:
                rarity_num = int(new_value)
                rarity = RarityLevel.from_number(rarity_num)
                if not rarity:
                    await update.message.reply_text(
                        f'❌ Invalid rarity. Please use a number between 1 and 15.'
                    )
                    return
                update_data['rarity'] = rarity_num  # Store as integer
            except ValueError:
                await update.message.reply_text(f'❌ Rarity must be a number (1-15).')
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
            await update.message.reply_text('❌ Failed to update character in database.')
            return
        
        # Update channel message (if not img_url which was already handled)
        if field != 'img_url' and 'message_id' in updated_character:
            try:
                # Create character object for channel update
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
                pass  # Channel update is optional
        
        await update.message.reply_text('✅ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴜᴘᴅᴀᴛᴇᴅ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ!')


# ===================== APPLICATION SETUP =====================

# Register command handlers with non-blocking option
application.add_handler(CommandHandler("upload", UploadHandler.handle, block=False))
application.add_handler(CommandHandler("delete", DeleteHandler.handle, block=False))
application.add_handler(CommandHandler("update", UpdateHandler.handle, block=False))


# ===================== CLEANUP =====================

async def cleanup():
    """Cleanup on shutdown"""
    await SessionManager.close()
