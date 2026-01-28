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
            f"<b>Character Name:</b> {self.name}\n"
            f"<b>Anime Name:</b> {self.anime}\n"
            f"<b>Rarity:</b> {display_name}\n"
            f"<b>ID:</b> {self.character_id}\n"
            f"{action} by <a href='tg://user?id={self.uploader_id}'>{self.uploader_name}</a>"
        )


@dataclass
class UploadResult:
    """Result of upload operation"""
    success: bool
    message: str
    character_id: Optional[str] = None
    character: Optional[Character] = None
    error: Optional[Exception] = None
    retry_count: int = 0


# ===================== SESSION MANAGEMENT =====================

class SessionManager:
    """Manages aiohttp sessions"""
    _session: Optional[ClientSession] = None
    _lock = asyncio.Lock()

    @classmethod
    @asynccontextmanager
    async def get_session(cls):
        """Get or create aiohttp session"""
        async with cls._lock:
            if cls._session is None or cls._session.closed:
                connector = TCPConnector(
                    limit=BotConfig.CONNECTION_LIMIT,
                    limit_per_host=30,
                    ttl_dns_cache=300,
                    enable_cleanup_closed=True
                )
                timeout = aiohttp.ClientTimeout(
                    total=BotConfig.DOWNLOAD_TIMEOUT,
                    connect=60,
                    sock_read=60
                )
                cls._session = ClientSession(
                    connector=connector,
                    timeout=timeout,
                    raise_for_status=False
                )
        
        try:
            yield cls._session
        finally:
            pass

    @classmethod
    async def close(cls):
        """Close the session"""
        async with cls._lock:
            if cls._session and not cls._session.closed:
                await cls._session.close()
                cls._session = None


# ===================== RETRY DECORATOR =====================

def retry_on_failure(max_attempts: int = 3, delay: float = 1.0):
    """Decorator for retrying failed operations"""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt < max_attempts - 1:
                        await asyncio.sleep(delay * (attempt + 1))
                    continue
            raise last_exception
        return wrapper
    return decorator


# ===================== SEQUENCE GENERATOR =====================

class SequenceGenerator:
    """Generates sequential IDs for characters with integrity checks"""
    
    @staticmethod
    async def get_next_id(sequence_name: str = 'character_id') -> str:
        """Get next sequential ID with max existing ID check"""
        # First, check the highest existing ID in collection
        existing_max = await collection.find_one(
            sort=[("id", -1)],  # Sort by ID descending
            projection={"id": 1}
        )
        
        sequence_collection = db.sequences
        current_sequence = await sequence_collection.find_one({'_id': sequence_name})
        
        if existing_max:
            existing_id = int(existing_max['id'])
            # If sequence exists, ensure it's not lower than existing max
            if current_sequence:
                current_value = current_sequence.get('sequence_value', 0)
                new_value = max(current_value, existing_id) + 1
            else:
                new_value = existing_id + 1
        else:
            # No existing IDs, start from 1 or continue sequence
            new_value = 1 if not current_sequence else current_sequence.get('sequence_value', 0) + 1
        
        # Update or create sequence document
        await sequence_collection.update_one(
            {'_id': sequence_name},
            {'$set': {'sequence_value': new_value}},
            upsert=True
        )
        
        return str(new_value)


# ===================== MEDIA HANDLERS =====================

class MediaHandler:
    """Handles media extraction and validation with efficient memory usage"""
    
    @staticmethod
    async def extract_from_reply(reply_message) -> Optional[MediaFile]:
        """Extract media from replied message using streaming"""
        media_type = MediaType.from_telegram_message(reply_message)
        
        if media_type == MediaType.VIDEO:
            raise ValueError("❌ Videos are not allowed! Please send only photos or image documents.")
        elif media_type == MediaType.ANIMATION:
            raise ValueError("❌ GIFs/Animations are not allowed! Please send only photos or image documents.")
        
        if not media_type or media_type not in [MediaType.PHOTO, MediaType.DOCUMENT]:
            return None
        
        try:
            # Create temporary file
            with tempfile.NamedTemporaryFile(delete=False, suffix='.image') as tmp_file:
                file_path = tmp_file.name
            
            try:
                if media_type == MediaType.PHOTO:
                    file = await reply_message.photo[-1].get_file()
                    filename = f"photo_{reply_message.photo[-1].file_unique_id}.jpg"
                    mime_type = 'image/jpeg'
                else:  # DOCUMENT
                    file = await reply_message.document.get_file()
                    filename = reply_message.document.file_name or f"document_{reply_message.document.file_unique_id}"
                    mime_type = reply_message.document.mime_type or ''
                    
                    if not mime_type.startswith('image/'):
                        raise ValueError("❌ Only image files are allowed! The document must be an image file.")
                
                # Stream download to temporary file
                await file.download_to_drive(file_path)
                
                # Get file size
                import os
                size = os.path.getsize(file_path)
                
                return MediaFile(
                    file_path=file_path,
                    media_type=media_type,
                    filename=filename,
                    mime_type=mime_type,
                    size=size,
                    telegram_file_id=file.file_id
                )
                
            except Exception as e:
                # Clean up temp file on error
                import os
                if os.path.exists(file_path):
                    os.unlink(file_path)
                raise
                
        except Exception as e:
            raise ValueError(f"❌ Failed to process media: {str(e)}")


class CatboxUploader:
    """Handles uploads to Catbox with streaming"""
    
    @staticmethod
    @retry_on_failure(max_attempts=BotConfig.MAX_RETRIES, delay=BotConfig.RETRY_DELAY)
    async def upload(file_path: str, filename: str) -> Optional[str]:
        """Upload file to Catbox using streaming"""
        async with SessionManager.get_session() as session:
            data = aiohttp.FormData()
            
            # Open file in binary mode and stream it
            with open(file_path, 'rb') as f:
                data.add_field('reqtype', 'fileupload')
                data.add_field(
                    'fileToUpload',
                    f,
                    filename=filename,
                    content_type='application/octet-stream'
                )
                
                async with session.post(BotConfig.CATBOX_API, data=data) as response:
                    if response.status == 200:
                        result = (await response.text()).strip()
                        if result.startswith('http'):
                            return result
            return None


# ===================== PROGRESS TRACKER =====================

class ProgressTracker:
    """Tracks and displays upload/download progress"""
    
    def __init__(self, message: Message):
        self.message = message
        self.last_update = 0
        self.update_interval = 1.0
        
    async def update(self, current: int, total: int):
        """Update progress message with throttling"""
        import time
        now = time.time()
        
        if now - self.last_update < self.update_interval and current < total:
            return
        
        self.last_update = now
        percent = (current / total * 100) if total > 0 else 0
        
        progress_bar = self._create_progress_bar(percent)
        
        size_mb = current / (1024 * 1024)
        total_mb = total / (1024 * 1024) if total > 0 else 0
        
        try:
            await self.message.edit_text(
                f"🔄 **Processing...**\n"
                f"📊 {progress_bar} {percent:.1f}%\n"
                f"📁 {size_mb:.2f} MB / {total_mb:.2f} MB"
            )
        except Exception:
            pass
    
    @staticmethod
    def _create_progress_bar(percent: float, length: int = 10) -> str:
        """Create ASCII progress bar"""
        filled = int(length * percent / 100)
        empty = length - filled
        return "█" * filled + "░" * empty


# ===================== CHARACTER FACTORY =====================

class CharacterFactory:
    """Creates Character objects"""
    
    @staticmethod
    def format_name(name: str) -> str:
        """Format character/anime name (Title Case)"""
        return name.strip().title()
    
    @staticmethod
    async def create_from_input(
        character_name: str,
        anime_name: str,
        rarity_num: int,
        media_file: MediaFile,
        user_id: int,
        user_name: str
    ) -> Optional[Character]:
        """Create a Character from input data"""
        # Validate rarity
        rarity = RarityLevel.from_number(rarity_num)
        if not rarity:
            raise ValueError(f"Invalid rarity number: {rarity_num}. Must be between 1-15.")
        
        # Generate ID
        char_id = await SequenceGenerator.get_next_id()
        
        # Format names
        formatted_name = CharacterFactory.format_name(character_name)
        formatted_anime = CharacterFactory.format_name(anime_name)
        
        # Create timestamp
        from datetime import datetime
        timestamp = datetime.utcnow().isoformat()
        
        return Character(
            character_id=char_id,
            name=formatted_name,
            anime=formatted_anime,
            rarity=rarity_num,  # Store as integer
            media_file=media_file,
            uploader_id=user_id,
            uploader_name=user_name,
            created_at=timestamp,
            updated_at=timestamp
        )


# ===================== TELEGRAM UPLOADER =====================

class TelegramUploader:
    """Handles uploading to Telegram channel"""
    
    @staticmethod
    async def upload_to_channel(
        character: Character,
        context: ContextTypes.DEFAULT_TYPE,
        telegram_file_id: str,
        is_update: bool = False
    ) -> Optional[int]:
        """Upload character to channel using file_id for instant posting"""
        try:
            caption = character.get_caption("Updated" if is_update else "Added")
            
            # Use telegram_file_id for instant posting
            if character.media_file.media_type == MediaType.PHOTO:
                message = await context.bot.send_photo(
                    chat_id=CHARA_CHANNEL_ID,
                    photo=telegram_file_id,
                    caption=caption,
                    parse_mode='HTML'
                )
            else:  # DOCUMENT
                message = await context.bot.send_document(
                    chat_id=CHARA_CHANNEL_ID,
                    document=telegram_file_id,
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
        """Update existing channel message with new media"""
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
            
            # Try to edit the media (photo or document)
            try:
                if character.media_file.media_type == MediaType.PHOTO:
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
                else:  # DOCUMENT
                    media = InputMediaDocument(
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
                else:
                    # For other BadRequest errors, try to at least update the caption
                    try:
                        await context.bot.edit_message_caption(
                            chat_id=CHARA_CHANNEL_ID,
                            message_id=old_message_id,
                            caption=caption,
                            parse_mode='HTML'
                        )
                        return old_message_id
                    except:
                        # If caption update also fails, send new message
                        return await TelegramUploader.upload_to_channel(
                            character, 
                            context, 
                            character.media_file.catbox_url or character.media_file.telegram_file_id, 
                            True
                        )
                
        except Exception as e:
            # If any other error occurs, send new message
            return await TelegramUploader.upload_to_channel(
                character, 
                context, 
                character.media_file.catbox_url or character.media_file.telegram_file_id, 
                True
            )


# ===================== COMMAND HANDLERS =====================

class UploadHandler:
    """Handles /upload command"""
    
    WRONG_FORMAT_TEXT = """❌ ɪɴᴄᴏʀʀᴇᴄᴛ ꜰᴏʀᴍᴀᴛ!

📌 ʜᴏᴡ ᴛᴏ ᴜꜱᴇ /upload:

ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴘʜᴏᴛᴏ

ꜱᴇɴᴅ ᴛʜᴇ ᴄᴏᴍᴍᴀɴᴅ /upload
ɪɴᴄʟᴜᴅᴇ 3 ʟɪɴᴇꜱ ɪɴ ʏᴏᴜʀ ᴍᴇꜱꜱᴀɢᴇ:

ᴄʜᴀʀᴀᴄᴛᴇʀ ɴᴀᴍᴇ 
ᴀɴɪᴍᴇ ɴᴀᴍᴇ 
ʀᴀʀɪᴛʏ (1-15)

✨ ᴇxᴀᴍᴘʟᴇ:
/upload 
ɴᴇᴢᴜᴋᴏ ᴋᴀᴍᴀᴅᴏ 
ᴅᴇᴍᴏɴ ꜱʟᴀʏᴇʀ 
4

📊 ʀᴀʀɪᴛʏ ᴍᴀᴘ (1-15):

• 1 ⚪ ᴄᴏᴍᴍᴏɴ 
• 2 🔵 ʀᴀʀᴇ 
• 3 🟡 ʟᴇɢᴇɴᴅᴀʀʏ 
• 4 💮 ꜱᴘᴇᴄɪᴀʟ 
• 5 👹 ᴀɴᴄɪᴇɴᴛ 
• 6 🎐 ᴄᴇʟᴇꜱᴛɪᴀʟ 
• 7 🔮 ᴇᴘɪᴄ 
• 8 🪐 ᴄᴏꜱᴍɪᴄ 
• 9 ⚰️ ɴɪɢʜᴛᴍᴀʀᴇ 
• 10 🌬️ ꜰʀᴏꜱᴛʙᴏʀɴ 
• 11 💝 ᴠᴀʟᴇɴᴛɪɴᴇ 
• 12 🌸 ꜱᴘʀɪɴɢ 
• 13 🏖️ ᴛʀᴏᴘɪᴄᴀʟ 
• 14 🍭 ᴋᴀᴡᴀɪɪ 
• 15 🧬 ʜʏʙʀɪᴅ"""
    
    @staticmethod
    def parse_input(text_content: str) -> Optional[Tuple[str, str, int]]:
        """Parse the 3-line input format from Code A"""
        lines = [line.strip() for line in text_content.split('\n') if line.strip()]
        
        if lines and lines[0].startswith('/upload'):
            lines = lines[1:]
        
        if len(lines) != 3:
            return None
        
        char_raw, anime_raw, rarity_raw = lines
        
        try:
            rarity_num = int(rarity_raw.strip())
            if not (1 <= rarity_num <= 15):
                return None
        except ValueError:
            return None
        
        return char_raw, anime_raw, rarity_num
    
    @staticmethod
    async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /upload command with parallel execution"""
        if update.effective_user.id not in Config.SUDO_USERS:
            await update.message.reply_text('🔒 ᴀꜱᴋ ᴍʏ ᴏᴡɴᴇʀ...')
            return
        
        if not update.message.reply_to_message:
            await update.message.reply_text(
                "📸 ʀᴇᴘʟʏ ʀᴇǫᴜɪʀᴇᴅ!\n\nʏᴏᴜ ᴍᴜꜱᴛ ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴘʜᴏᴛᴏ ᴏʀ ɪᴍᴀɢᴇ ᴅᴏᴄᴜᴍᴇɴᴛ ᴡɪᴛʜ ᴛʜᴇ /upload ᴄᴏᴍᴍᴀɴᴅ."
            )
            return
        
        text_content = update.message.text or update.message.caption or ""
        parsed = UploadHandler.parse_input(text_content)
        
        if not parsed:
            await update.message.reply_text(UploadHandler.WRONG_FORMAT_TEXT)
            return
        
        character_name, anime_name, rarity_num = parsed
        
        processing_msg = await update.message.reply_text("🔄 **Extracting media...**")
        
        try:
            # Extract media
            await processing_msg.edit_text("🔄 **Downloading from Telegram...**")
            media_file = await MediaHandler.extract_from_reply(update.message.reply_to_message)
            
            if not media_file or not media_file.is_valid_image:
                await processing_msg.edit_text("❌ Invalid media! Only photos and image documents are allowed.")
                return
            
            if not media_file.is_valid_size:
                await processing_msg.edit_text(
                    f"❌ File too large! Maximum size: {BotConfig.MAX_FILE_SIZE / (1024 * 1024):.1f} MB"
                )
                return
            
            # Create character object (without Catbox URL yet)
            await processing_msg.edit_text("🔄 **Preparing character...**")
            character = await CharacterFactory.create_from_input(
                character_name,
                anime_name,
                rarity_num,
                media_file,
                update.effective_user.id,
                update.effective_user.first_name
            )
            
            # FIXED: Use coroutines directly with asyncio.gather instead of creating tasks first
            await processing_msg.edit_text("🔄 **Uploading to Catbox and posting to channel...**")
            
            # Run both operations concurrently using gather with coroutines
            catbox_url, message_id = await asyncio.gather(
                CatboxUploader.upload(media_file.file_path, media_file.filename),
                TelegramUploader.upload_to_channel(
                    character, 
                    context, 
                    media_file.telegram_file_id, 
                    is_update=False
                )
            )
            
            if not catbox_url:
                await processing_msg.edit_text("❌ Failed to upload to Catbox. Please try again.")
                # Try to delete the channel post if it succeeded
                if message_id:
                    try:
                        await context.bot.delete_message(CHARA_CHANNEL_ID, message_id)
                    except:
                        pass
                return
            
            if not message_id:
                await processing_msg.edit_text("❌ Failed to post to channel. Please try again.")
                return
            
            # Update character with URLs and message ID
            media_file.catbox_url = catbox_url
            character.message_id = message_id
            
            # Save to database (only after both operations succeed)
            await collection.insert_one(character.to_dict())
            
            # Clean up temporary file
            media_file.cleanup()
            
            # Success message
            rarity_obj = RarityLevel.from_number(character.rarity)
            display_name = rarity_obj.display_name if rarity_obj else f"Level {character.rarity}"
            
            success_text = (
                f"✅ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴀᴅᴅᴇᴅ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ!\n\n"
                f"ɴᴀᴍᴇ: {character.name}\n"
                f"ᴀɴɪᴍᴇ: {character.anime}\n"
                f"ʀᴀʀɪᴛʏ: {display_name}\n"
                f"ɪᴅ: {character.character_id}"
            )
            await processing_msg.edit_text(success_text)
            
        except ValueError as e:
            await processing_msg.edit_text(str(e))
        except Exception as e:
            error_msg = f"❌ ᴜᴘʟᴏᴀᴅ ꜰᴀɪʟᴇᴅ!\n\nᴇʀʀᴏʀ: {str(e)[:200]}"
            if SUPPORT_CHAT:
                error_msg += f"\n\nɪꜰ ᴛʜɪꜱ ᴇʀʀᴏʀ ᴘᴇʀꜱɪꜱᴛꜱ, ᴄᴏɴᴛᴀᴄᴛ: {SUPPORT_CHAT}"
            await processing_msg.edit_text(error_msg)


class DeleteHandler:
    """Handles /delete command"""
    
    @staticmethod
    async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /delete command"""
        if update.effective_user.id not in Config.SUDO_USERS:
            await update.message.reply_text('🔒 ᴀꜱᴋ ᴍʏ ᴏᴡɴᴇʀ...')
            return
        
        if not context.args or len(context.args) != 1:
            await update.message.reply_text('❌ ɪɴᴄᴏʀʀᴇᴄᴛ ꜰᴏʀᴍᴀᴛ... ᴘʟᴇᴀꜱᴇ ᴜꜱᴇ: /delete ID')
            return
        
        character_id = context.args[0]
        
        character = await collection.find_one_and_delete({'id': character_id})
        
        if not character:
            await update.message.reply_text('❌ ᴄʜᴀʀᴀᴄᴛᴇʀ ɴᴏᴛ ꜰᴏᴜɴᴅ ɪɴ ᴅᴀᴛᴀʙᴀꜱᴇ.')
            return
        
        try:
            if 'message_id' in character:
                await context.bot.delete_message(
                    chat_id=CHARA_CHANNEL_ID,
                    message_id=character['message_id']
                )
                await update.message.reply_text('✅ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴅᴇʟᴇᴛᴇᴅ ꜰʀᴏᴍ ᴅᴀᴛᴀʙᴀꜱᴇ ᴀɴᴅ ᴄʜᴀɴɴᴇʟ.')
            else:
                await update.message.reply_text('✅ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴅᴇʟᴇᴛᴇᴅ ꜰʀᴏᴍ ᴅᴀᴛᴀʙᴀꜱᴇ (ɴᴏ ᴄʜᴀɴɴᴇʟ ᴍᴇꜱꜱᴀɢᴇ ꜰᴏᴜɴᴅ).')
        except BadRequest as e:
            error_msg = str(e).lower()
            if "message to delete not found" in error_msg:
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