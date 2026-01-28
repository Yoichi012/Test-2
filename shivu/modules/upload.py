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
        """Generate caption for channel post - New Format with Small Caps"""
        rarity_obj = RarityLevel.from_number(self.rarity)
        display_name = rarity_obj.display_name if rarity_obj else f"Level {self.rarity}"

        return (
            f"✦ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴅᴇᴛᴀɪʟ ✦\n\n"
            f"✧ 🌸  ɴᴀᴍᴇ     : {self.name}\n"
            f"✧ 📺  ᴀɴɪᴍᴇ    : {self.anime}\n"
            f"✧ 💫  ʀᴀʀɪᴛʏ   : {display_name}\n"
            f"✧  🆔  ɪᴅ       : {self.character_id}\n\n"
            f"✦   ᴍᴀᴅᴇ ʙʏ : {self.uploader_name}"
        )


# ===================== UTILITIES =====================

class SessionManager:
    """Manages aiohttp session with connection pooling"""
    _session: Optional[ClientSession] = None

    @classmethod
    async def get_session(cls) -> ClientSession:
        """Get or create aiohttp session"""
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
        """Close the session"""
        if cls._session and not cls._session.closed:
            await cls._session.close()


def retry_on_failure(max_retries: int = BotConfig.MAX_RETRIES):
    """Decorator for retry logic"""
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


# ===================== MEDIA HANDLER =====================

class MediaHandler:
    """Handles media file extraction and validation"""

    @staticmethod
    async def extract_from_reply(message: Message, force_photo: bool = False) -> Optional[MediaFile]:
        """Extract media file from Telegram message reply with optional force_photo"""
        media_type = MediaType.from_telegram_message(message)
        if not media_type:
            return None

        try:
            # Determine which file object to use
            if media_type == MediaType.PHOTO:
                file_obj = message.photo[-1]
                filename = f"photo_{file_obj.file_unique_id}.jpg"
                mime_type = "image/jpeg"
            elif media_type == MediaType.DOCUMENT:
                file_obj = message.document
                filename = file_obj.file_name or f"document_{file_obj.file_unique_id}"
                mime_type = file_obj.mime_type
            elif media_type == MediaType.VIDEO:
                file_obj = message.video
                filename = f"video_{file_obj.file_unique_id}.mp4"
                mime_type = "video/mp4"
            elif media_type == MediaType.ANIMATION:
                file_obj = message.animation
                filename = f"animation_{file_obj.file_unique_id}.mp4"
                mime_type = "video/mp4"
            else:
                return None

            # Download file
            file = await file_obj.get_file()
            with tempfile.NamedTemporaryFile(delete=False, suffix=f"_{filename}") as temp_file:
                await file.download_to_drive(temp_file.name)
                temp_path = temp_file.name

            # ✨ Force conversion to PHOTO type if requested (for img_url updates)
            if force_photo and media_type == MediaType.DOCUMENT:
                media_type = MediaType.PHOTO

            return MediaFile(
                file_path=temp_path,
                media_type=media_type,
                filename=filename,
                mime_type=mime_type,
                size=file_obj.file_size or 0,
                telegram_file_id=file_obj.file_id
            )

        except Exception as e:
            print(f"Error extracting media: {e}")
            return None

    @staticmethod
    def validate_media(media_file: MediaFile) -> Tuple[bool, str]:
        """Validate media file"""
        if not media_file.is_valid_image:
            return False, "❌ ᴏɴʟʏ ɪᴍᴀɢᴇꜱ ᴀʀᴇ ᴀʟʟᴏᴡᴇᴅ!"

        if not media_file.is_valid_size:
            size_mb = media_file.size / (1024 * 1024)
            max_mb = BotConfig.MAX_FILE_SIZE / (1024 * 1024)
            return False, f"❌ ꜰɪʟᴇ ᴛᴏᴏ ʟᴀʀɢᴇ! ({size_mb:.1f}ᴍʙ > {max_mb}ᴍʙ)"

        return True, "✅ ᴠᴀʟɪᴅ"


# ===================== CATBOX UPLOADER =====================

class CatboxUploader:
    """Handles Catbox.moe uploads with retry logic"""

    @staticmethod
    @retry_on_failure()
    async def upload(file_path: str, filename: str) -> Optional[str]:
        """Upload file to Catbox"""
        session = await SessionManager.get_session()

        try:
            with open(file_path, 'rb') as f:
                form = aiohttp.FormData()
                form.add_field('reqtype', 'fileupload')
                form.add_field('fileToUpload', f, filename=filename)

                async with session.post(BotConfig.CATBOX_API, data=form) as response:
                    if response.status == 200:
                        url = await response.text()
                        return url.strip() if url else None
                    return None
        except Exception as e:
            print(f"Catbox upload error: {e}")
            return None


# ===================== TELEGRAM UPLOADER =====================

class TelegramUploader:
    """Handles Telegram channel uploads"""

    @staticmethod
    @retry_on_failure()
    async def send_to_channel(character: Character, context: ContextTypes.DEFAULT_TYPE) -> Optional[int]:
        """Send character to channel and return message_id"""
        try:
            if character.media_file.file_path:
                with open(character.media_file.file_path, 'rb') as f:
                    message = await context.bot.send_photo(
                        chat_id=CHARA_CHANNEL_ID,
                        photo=f,
                        caption=character.get_caption("Added"),
                        parse_mode=None
                    )
            elif character.media_file.catbox_url:
                message = await context.bot.send_photo(
                    chat_id=CHARA_CHANNEL_ID,
                    photo=character.media_file.catbox_url,
                    caption=character.get_caption("Added"),
                    parse_mode=None
                )
            else:
                return None

            return message.message_id
        except Exception as e:
            print(f"Failed to send to channel: {e}")
            return None

    @staticmethod
    @retry_on_failure()
    async def update_channel_message(
        character: Character,
        context: ContextTypes.DEFAULT_TYPE,
        old_message_id: Optional[int]
    ) -> Optional[int]:
        """Update existing channel message by editing media and caption"""
        try:
            if not old_message_id:
                # If no old message, create new one
                return await TelegramUploader.send_to_channel(character, context)

            # Edit the existing message with new photo and caption
            if character.media_file.file_path:
                # Use local file
                with open(character.media_file.file_path, 'rb') as f:
                    media = InputMediaPhoto(
                        media=f,
                        caption=character.get_caption("Updated"),
                        parse_mode=None
                    )
                    await context.bot.edit_message_media(
                        chat_id=CHARA_CHANNEL_ID,
                        message_id=old_message_id,
                        media=media
                    )
            elif character.media_file.catbox_url:
                # Use catbox URL
                media = InputMediaPhoto(
                    media=character.media_file.catbox_url,
                    caption=character.get_caption("Updated"),
                    parse_mode=None
                )
                await context.bot.edit_message_media(
                    chat_id=CHARA_CHANNEL_ID,
                    message_id=old_message_id,
                    media=media
                )
            else:
                # Only update caption if no new image
                await context.bot.edit_message_caption(
                    chat_id=CHARA_CHANNEL_ID,
                    message_id=old_message_id,
                    caption=character.get_caption("Updated"),
                    parse_mode=None
                )

            return old_message_id  # Return same message_id since we edited it
        except Exception as e:
            print(f"Failed to update channel message: {e}")
            # If edit fails, try creating new message
            return await TelegramUploader.send_to_channel(character, context)


# ===================== CHARACTER FACTORY =====================

class CharacterFactory:
    """Factory for creating Character objects"""

    @staticmethod
    def format_name(text: str) -> str:
        """Format name/anime with proper capitalization"""
        return ' '.join(word.capitalize() for word in text.split())

    @staticmethod
    async def check_duplicate(file_hash: str, character_id: str) -> Optional[Dict]:
        """Check for duplicate by hash, excluding current character_id"""
        return await collection.find_one({
            'file_hash': file_hash,
            'id': {'$ne': character_id}
        })

    @staticmethod
    async def get_next_id() -> str:
        """Get next available character ID"""
        try:
            last_char = await collection.find_one(
                sort=[('id', -1)],
                projection={'id': 1}
            )
            if last_char and last_char.get('id'):
                try:
                    last_id = int(last_char['id'])
                    return str(last_id + 1)
                except ValueError:
                    pass
            return "1"
        except Exception:
            return "1"

    @staticmethod
    async def create_from_upload(
        name: str,
        anime: str,
        rarity: int,
        media_file: MediaFile,
        uploader_id: int,
        uploader_name: str
    ) -> Character:
        """Create character from upload data"""
        from datetime import datetime

        character_id = await CharacterFactory.get_next_id()

        return Character(
            character_id=character_id,
            name=CharacterFactory.format_name(name),
            anime=CharacterFactory.format_name(anime),
            rarity=rarity,
            media_file=media_file,
            uploader_id=uploader_id,
            uploader_name=uploader_name,
            created_at=datetime.utcnow().isoformat()
        )


# ===================== UPLOAD HANDLER =====================

class UploadHandler:
    """Handles /upload command"""

    @staticmethod
    def format_upload_help() -> str:
        """Format upload command help message"""
        rarities = RarityLevel.get_all()
        rarity_list = '\n'.join([f"{num}. {name}" for num, name in rarities.items()])

        return (
            "📤 ᴜᴘʟᴏᴀᴅ ᴄᴏᴍᴍᴀɴᴅ ᴜꜱᴀɢᴇ:\n\n"
            "ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴘʜᴏᴛᴏ ᴡɪᴛʜ:\n"
            "/upload ɴᴀᴍᴇ & ᴀɴɪᴍᴇ & ʀᴀʀɪᴛʏ\n\n"
            "ᴇxᴀᴍᴘʟᴇ:\n"
            "/upload ɴᴇᴢᴜᴋᴏ ᴋᴀᴍᴀᴅᴏ & ᴅᴇᴍᴏɴ ꜱʟᴀʏᴇʀ & 5\n\n"
            f"ʀᴀʀɪᴛʏ ʟᴇᴠᴇʟꜱ:\n{rarity_list}"
        )

    @staticmethod
    async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /upload command"""
        if update.effective_user.id not in Config.SUDO_USERS:
            await update.message.reply_text('🔒 ᴀꜱᴋ ᴍʏ ᴏᴡɴᴇʀ...')
            return

        if not update.message.reply_to_message:
            await update.message.reply_text(UploadHandler.format_upload_help())
            return

        if not context.args:
            await update.message.reply_text(UploadHandler.format_upload_help())
            return

        # Parse input
        input_text = ' '.join(context.args)
        parts = [p.strip() for p in input_text.split('&')]

        if len(parts) != 3:
            await update.message.reply_text(
                '❌ ɪɴᴠᴀʟɪᴅ ꜰᴏʀᴍᴀᴛ!\n\n'
                'ᴜꜱᴇ: /upload ɴᴀᴍᴇ & ᴀɴɪᴍᴇ & ʀᴀʀɪᴛʏ'
            )
            return

        name, anime, rarity_str = parts

        # Validate rarity
        try:
            rarity_num = int(rarity_str)
            rarity = RarityLevel.from_number(rarity_num)
            if not rarity:
                await update.message.reply_text(
                    f'❌ ɪɴᴠᴀʟɪᴅ ʀᴀʀɪᴛʏ: {rarity_num}\n\n'
                    'ᴘʟᴇᴀꜱᴇ ᴜꜱᴇ ᴀ ɴᴜᴍʙᴇʀ ʙᴇᴛᴡᴇᴇɴ 1-15.\n'
                    'ᴜꜱᴇ /upload ᴛᴏ ꜱᴇᴇ ᴀʟʟ ʀᴀʀɪᴛʏ ʟᴇᴠᴇʟꜱ.'
                )
                return
        except ValueError:
            await update.message.reply_text('❌ ʀᴀʀɪᴛʏ ᴍᴜꜱᴛ ʙᴇ ᴀ ɴᴜᴍʙᴇʀ (1-15).')
            return

        # Extract media
        processing_msg = await update.message.reply_text("🔄 ᴘʀᴏᴄᴇꜱꜱɪɴɢ ᴜᴘʟᴏᴀᴅ...")

        media_file = await MediaHandler.extract_from_reply(update.message.reply_to_message)
        if not media_file:
            await processing_msg.edit_text("❌ ɴᴏ ᴠᴀʟɪᴅ ᴍᴇᴅɪᴀ ꜰᴏᴜɴᴅ!")
            return

        # Validate media
        is_valid, message = MediaHandler.validate_media(media_file)
        if not is_valid:
            media_file.cleanup()
            await processing_msg.edit_text(message)
            return

        # Create character
        character = await CharacterFactory.create_from_upload(
            name=name,
            anime=anime,
            rarity=rarity_num,
            media_file=media_file,
            uploader_id=update.effective_user.id,
            uploader_name=update.effective_user.first_name
        )

        # Check for duplicates
        duplicate = await CharacterFactory.check_duplicate(media_file.hash, character.character_id)
        if duplicate:
            media_file.cleanup()
            await processing_msg.edit_text(
                f'❌ ᴅᴜᴘʟɪᴄᴀᴛᴇ ɪᴍᴀɢᴇ!\n\n'
                f'ᴀʟʀᴇᴀᴅʏ ᴇxɪꜱᴛꜱ ᴀꜱ:\n'
                f'ɴᴀᴍᴇ: {duplicate["name"]}\n'
                f'ɪᴅ: {duplicate["id"]}'
            )
            return

        # Parallel upload to Catbox and Telegram
        await processing_msg.edit_text("📤 ᴜᴘʟᴏᴀᴅɪɴɢ ᴛᴏ ᴄᴀᴛʙᴏx ᴀɴᴅ ᴄʜᴀɴɴᴇʟ...")

        catbox_url, message_id = await asyncio.gather(
            CatboxUploader.upload(media_file.file_path, media_file.filename),
            TelegramUploader.send_to_channel(character, context)
        )

        if not catbox_url:
            media_file.cleanup()
            await processing_msg.edit_text("❌ ꜰᴀɪʟᴇᴅ ᴛᴏ ᴜᴘʟᴏᴀᴅ ᴛᴏ ᴄᴀᴛʙᴏx!")
            return

        # Update character with URLs
        character.media_file.catbox_url = catbox_url
        character.message_id = message_id

        # Save to database
        try:
            await collection.insert_one(character.to_dict())
            media_file.cleanup()
            await processing_msg.edit_text(
                f'✅ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴀᴅᴅᴇᴅ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ!\n\n'
                f'ɴᴀᴍᴇ: {character.name}\n'
                f'ᴀɴɪᴍᴇ: {character.anime}\n'
                f'ʀᴀʀɪᴛʏ: {rarity.display_name}\n'
                f'ɪᴅ: {character.character_id}'
            )
        except Exception as e:
            media_file.cleanup()
            await processing_msg.edit_text(f'❌ ᴅᴀᴛᴀʙᴀꜱᴇ ᴇʀʀᴏʀ: {str(e)}')


# ===================== DELETE HANDLER =====================

class DeleteHandler:
    """Handles /delete command"""

    @staticmethod
    async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /delete command"""
        if update.effective_user.id not in Config.SUDO_USERS:
            await update.message.reply_text('🔒 ᴀꜱᴋ ᴍʏ ᴏᴡɴᴇʀ...')
            return

        if not context.args:
            await update.message.reply_text(
                '❌ ᴘʟᴇᴀꜱᴇ ᴘʀᴏᴠɪᴅᴇ ᴄʜᴀʀᴀᴄᴛᴇʀ ɪᴅ.\n\n'
                'ᴜꜱᴀɢᴇ: /delete <ɪᴅ>'
            )
            return

        char_id = context.args[0]

        # Find character
        character = await collection.find_one({'id': char_id})
        if not character:
            await update.message.reply_text('❌ ᴄʜᴀʀᴀᴄᴛᴇʀ ɴᴏᴛ ꜰᴏᴜɴᴅ.')
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
                    # ✨ FIX: Force document to photo conversion
                    media_file = await MediaHandler.extract_from_reply(
                        update.message.reply_to_message, 
                        force_photo=True
                    )

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
