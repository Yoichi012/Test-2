import asyncio
import hashlib
import io
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, Any, List, Tuple
from functools import wraps
from contextlib import asynccontextmanager

import aiohttp
from aiohttp import ClientSession, TCPConnector
from pymongo import ReturnDocument
from telegram import Update, InputFile, Message, PhotoSize, Document
from telegram.ext import CommandHandler, ContextTypes
from telegram.error import TelegramError, NetworkError, TimedOut, BadRequest

from shivu import application, collection, db, CHARA_CHANNEL_ID, SUPPORT_CHAT
from shivu.config import Config


# ===================== ENUMS =====================

class MediaType(Enum):
    """Allowed media types"""
    PHOTO = "photo"  # Compressed images sent as photos
    DOCUMENT = "document"  # Uncompressed images sent as documents
    VIDEO = "video"  # For rejection
    ANIMATION = "animation"  # For rejection

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
    MAX_FILE_SIZE: int = 20 * 1024 * 1024  # 20MB
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
    """Represents a media file"""
    file_bytes: Optional[bytes] = None
    media_type: Optional[MediaType] = None
    filename: str = field(default="")
    mime_type: Optional[str] = None
    size: int = 0
    hash: str = field(default="")
    catbox_url: Optional[str] = None
    telegram_file_id: Optional[str] = None

    def __post_init__(self):
        if self.file_bytes and not self.hash:
            object.__setattr__(self, 'hash', self._compute_hash())
        if self.file_bytes and not self.size:
            object.__setattr__(self, 'size', len(self.file_bytes))

    def _compute_hash(self) -> str:
        """Compute SHA256 hash of file bytes"""
        return hashlib.sha256(self.file_bytes).hexdigest()

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


@dataclass
class Character:
    """Represents a character entry"""
    character_id: str
    name: str
    anime: str
    rarity: RarityLevel
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
            'rarity': self.rarity.display_name,
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
        return (
            f"<b>Character Name:</b> {self.name}\n"
            f"<b>Anime Name:</b> {self.anime}\n"
            f"<b>Rarity:</b> {self.rarity.display_name}\n"
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
    """Generates sequential IDs for characters"""
    @staticmethod
    async def get_next_id(sequence_name: str = 'character_id') -> str:
        """Get next sequential ID"""
        sequence_collection = db.sequences
        sequence_document = await sequence_collection.find_one_and_update(
            {'_id': sequence_name},
            {'$inc': {'sequence_value': 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER
        )
        return str(sequence_document['sequence_value'])


# ===================== MEDIA HANDLERS =====================

class MediaHandler:
    """Handles media extraction and validation"""
    
    @staticmethod
    async def extract_from_reply(reply_message) -> Optional[MediaFile]:
        """Extract media from replied message"""
        media_type = MediaType.from_telegram_message(reply_message)
        
        # Reject videos and GIFs with specific error message
        if media_type == MediaType.VIDEO:
            raise ValueError("❌ Videos are not allowed! Please send only photos or image documents.")
        elif media_type == MediaType.ANIMATION:
            raise ValueError("❌ GIFs/Animations are not allowed! Please send only photos or image documents.")
        
        if not media_type or media_type not in [MediaType.PHOTO, MediaType.DOCUMENT]:
            return None
        
        try:
            if media_type == MediaType.PHOTO:
                file = await reply_message.photo[-1].get_file()
                filename = f"photo_{reply_message.photo[-1].file_unique_id}.jpg"
                mime_type = 'image/jpeg'
            else:  # DOCUMENT
                file = await reply_message.document.get_file()
                filename = reply_message.document.file_name or f"document_{reply_message.document.file_unique_id}"
                mime_type = reply_message.document.mime_type or ''
                
                # Verify it's an image even if sent as document
                if not mime_type.startswith('image/'):
                    raise ValueError("❌ Only image files are allowed! The document must be an image file.")
            
            # Download file
            file_bytes = bytes(await file.download_as_bytearray())
            
            return MediaFile(
                file_bytes=file_bytes,
                media_type=media_type,
                filename=filename,
                mime_type=mime_type,
                size=len(file_bytes),
                telegram_file_id=file.file_id
            )
            
        except Exception as e:
            raise ValueError(f"❌ Failed to process media: {str(e)}")

    @staticmethod
    def validate_mime_type(mime_type: str) -> bool:
        """Validate MIME type is an image"""
        return mime_type and mime_type.startswith('image/')


class CatboxUploader:
    """Handles uploads to Catbox"""
    
    @staticmethod
    @retry_on_failure(max_attempts=BotConfig.MAX_RETRIES, delay=BotConfig.RETRY_DELAY)
    async def upload(file_bytes: bytes, filename: str) -> Optional[str]:
        """Upload file to Catbox"""
        async with SessionManager.get_session() as session:
            data = aiohttp.FormData()
            data.add_field('reqtype', 'fileupload')
            data.add_field(
                'fileToUpload',
                file_bytes,
                filename=filename,
                content_type='application/octet-stream'
            )
            
            async with session.post(BotConfig.CATBOX_API, data=data) as response:
                if response.status == 200:
                    result = (await response.text()).strip()
                    if result.startswith('http'):
                        return result
                return None

    @staticmethod
    async def upload_with_progress(file_bytes: bytes, filename: str, progress_callback=None) -> Optional[str]:
        """Upload with progress tracking"""
        if progress_callback:
            await progress_callback(0, len(file_bytes))
        
        result = await CatboxUploader.upload(file_bytes, filename)
        
        if progress_callback:
            await progress_callback(len(file_bytes), len(file_bytes))
        
        return result


# ===================== PROGRESS TRACKER =====================

class ProgressTracker:
    """Tracks and displays upload/download progress"""
    
    def __init__(self, message: Message):
        self.message = message
        self.last_update = 0
        self.update_interval = 1.0  # Update every second
        
    async def update(self, current: int, total: int):
        """Update progress message"""
        import time
        now = time.time()
        
        # Throttle updates
        if now - self.last_update < self.update_interval and current < total:
            return
        
        self.last_update = now
        percent = (current / total * 100) if total > 0 else 0
        
        # Create progress bar
        progress_bar = self._create_progress_bar(percent)
        
        # Format sizes
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
            rarity=rarity,
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
        is_update: bool = False
    ) -> Optional[int]:
        """Upload character to channel and return message ID"""
        try:
            caption = character.get_caption("Updated" if is_update else "Added")
            
            if character.media_file.media_type == MediaType.PHOTO:
                message = await context.bot.send_photo(
                    chat_id=CHARA_CHANNEL_ID,
                    photo=character.media_file.catbox_url,
                    caption=caption,
                    parse_mode='HTML'
                )
            else:  # DOCUMENT
                message = await context.bot.send_document(
                    chat_id=CHARA_CHANNEL_ID,
                    document=character.media_file.catbox_url,
                    caption=caption,
                    parse_mode='HTML'
                )
            
            return message.message_id
            
        except BadRequest as e:
            error_msg = str(e).lower()
            if "not found" in error_msg or "message to edit not found" in error_msg:
                # Re-upload if message not found
                return await TelegramUploader.upload_to_channel(character, context, is_update)
            raise
        except Exception as e:
            raise ValueError(f"Failed to upload to channel: {str(e)}")

    @staticmethod
    async def update_channel_message(
        character: Character,
        context: ContextTypes.DEFAULT_TYPE,
        old_message_id: Optional[int] = None
    ) -> Optional[int]:
        """Update existing channel message"""
        try:
            # Try to edit caption
            if old_message_id:
                caption = character.get_caption("Updated")
                await context.bot.edit_message_caption(
                    chat_id=CHARA_CHANNEL_ID,
                    message_id=old_message_id,
                    caption=caption,
                    parse_mode='HTML'
                )
                return old_message_id
            else:
                # Upload new message if old one doesn't exist
                return await TelegramUploader.upload_to_channel(character, context, True)
                
        except BadRequest as e:
            error_msg = str(e).lower()
            if "not found" in error_msg or "message to edit not found" in error_msg:
                # Upload new message
                return await TelegramUploader.upload_to_channel(character, context, True)
            raise


# ===================== COMMAND HANDLERS =====================

class UploadHandler:
    """Handles /upload command"""
    
    # Format text (matching Code A)
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
        
        # Remove /upload command if present
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
        """Handle /upload command"""
        # Check sudo access
        if update.effective_user.id not in Config.SUDO_USERS:
            await update.message.reply_text('🔒 ᴀꜱᴋ ᴍʏ ᴏᴡɴᴇʀ...')
            return
        
        # Check if replying to a message
        if not update.message.reply_to_message:
            await update.message.reply_text(
                "📸 ʀᴇᴘʟʏ ʀᴇǫᴜɪʀᴇᴅ!\n\nʏᴏᴜ ᴍᴜꜱᴛ ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴘʜᴏᴛᴏ ᴏʀ ɪᴍᴀɢᴇ ᴅᴏᴄᴜᴍᴇɴᴛ ᴡɪᴛʜ ᴛʜᴇ /upload ᴄᴏᴍᴍᴀɴᴅ."
            )
            return
        
        # Parse input
        text_content = update.message.text or update.message.caption or ""
        parsed = UploadHandler.parse_input(text_content)
        
        if not parsed:
            await update.message.reply_text(UploadHandler.WRONG_FORMAT_TEXT)
            return
        
        character_name, anime_name, rarity_num = parsed
        
        # Start processing
        processing_msg = await update.message.reply_text("🔄 **Extracting media...**")
        
        try:
            # Extract media from reply
            progress_tracker = ProgressTracker(processing_msg)
            
            await processing_msg.edit_text("🔄 **Downloading from Telegram...**")
            media_file = await MediaHandler.extract_from_reply(update.message.reply_to_message)
            
            if not media_file or not media_file.is_valid_image:
                await processing_msg.edit_text("❌ Invalid media! Only photos and image documents are allowed.")
                return
            
            # Check file size
            if not media_file.is_valid_size:
                await processing_msg.edit_text(
                    f"❌ File too large! Maximum size: {BotConfig.MAX_FILE_SIZE / (1024 * 1024):.1f} MB"
                )
                return
            
            # Upload to Catbox
            await processing_msg.edit_text("🔄 **Uploading to Catbox...**")
            catbox_url = await CatboxUploader.upload_with_progress(
                media_file.file_bytes,
                media_file.filename,
                progress_tracker.update
            )
            
            if not catbox_url:
                await processing_msg.edit_text("❌ Failed to upload to Catbox. Please try again.")
                return
            
            media_file.catbox_url = catbox_url
            
            # Create character
            await processing_msg.edit_text("🔄 **Creating character entry...**")
            character = await CharacterFactory.create_from_input(
                character_name,
                anime_name,
                rarity_num,
                media_file,
                update.effective_user.id,
                update.effective_user.first_name
            )
            
            # Upload to channel
            await processing_msg.edit_text("🔄 **Posting to channel...**")
            message_id = await TelegramUploader.upload_to_channel(character, context)
            character.message_id = message_id
            
            # Save to database
            await collection.insert_one(character.to_dict())
            
            # Success message
            success_text = (
                f"✅ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴀᴅᴅᴇᴅ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ!\n\n"
                f"ɴᴀᴍᴇ: {character.name}\n"
                f"ᴀɴɪᴍᴇ: {character.anime}\n"
                f"ʀᴀʀɪᴛʏ: {character.rarity.display_name}\n"
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
        # Check sudo access
        if update.effective_user.id not in Config.SUDO_USERS:
            await update.message.reply_text('🔒 ᴀꜱᴋ ᴍʏ ᴏᴡɴᴇʀ...')
            return
        
        # Check arguments
        if not context.args or len(context.args) != 1:
            await update.message.reply_text('❌ ɪɴᴄᴏʀʀᴇᴄᴛ ꜰᴏʀᴍᴀᴛ... ᴘʟᴇᴀꜱᴇ ᴜꜱᴇ: /delete ID')
            return
        
        character_id = context.args[0]
        
        # Find and delete character
        character = await collection.find_one_and_delete({'id': character_id})
        
        if not character:
            await update.message.reply_text('❌ ᴄʜᴀʀᴀᴄᴛᴇʀ ɴᴏᴛ ꜰᴏᴜɴᴅ ɪɴ ᴅᴀᴛᴀʙᴀꜱᴇ.')
            return
        
        # Try to delete from channel
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
                f'✅ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴅᴇʟᴇᴛᴇᴅ ꜰʀᴏᴍ ᴅᴀᴛᴀʙᴀꜱᴇ.\n\n⚠️ ᴄʜᴀɴɴᴇʟ ᴅᴇʟᴇᴛɪᴏɴ ᴇʀʀᴏʀ: {str(e)}'
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
    async def validate_image_url(url: str) -> bool:
        """Validate image URL"""
        if url.startswith('http'):
            async with SessionManager.get_session() as session:
                try:
                    async with session.head(url, allow_redirects=True) as response:
                        if response.status != 200:
                            return False
                        content_type = response.headers.get('Content-Type', '').lower()
                        return content_type.startswith('image/')
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    return False
        return True
    
    @staticmethod
    async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /update command"""
        # Check sudo access
        if update.effective_user.id not in Config.SUDO_USERS:
            await update.message.reply_text('🔒 ᴀꜱᴋ ᴍʏ ᴏᴡɴᴇʀ...')
            return
        
        # Check arguments
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
        
        # Find character
        character = await collection.find_one({'id': char_id})
        if not character:
            await update.message.reply_text('❌ ᴄʜᴀʀᴀᴄᴛᴇʀ ɴᴏᴛ ꜰᴏᴜɴᴅ.')
            return
        
        # Process update based on field
        update_data = {}
        
        if field == 'img_url':
            if len(context.args) == 2:
                # Reply to photo required
                if not (update.message.reply_to_message and 
                       (update.message.reply_to_message.photo or 
                        update.message.reply_to_message.document)):
                    await update.message.reply_text(
                        '📸 ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴘʜᴏᴛᴏ ʀᴇǫᴜɪʀᴇᴅ!\n\nʀᴇᴘʟʏ ᴛᴏ ᴀ ᴘʜᴏᴛᴏ ᴀɴᴅ ᴜꜱᴇ: /update id img_url'
                    )
                    return
                
                processing_msg = await update.message.reply_text("🔄 **Processing new image...**")
                
                try:
                    # Extract and validate new image
                    progress_tracker = ProgressTracker(processing_msg)
                    media_file = await MediaHandler.extract_from_reply(update.message.reply_to_message)
                    
                    if not media_file or not media_file.is_valid_image:
                        await processing_msg.edit_text("❌ Invalid media! Only photos and image documents are allowed.")
                        return
                    
                    # Upload to Catbox
                    await processing_msg.edit_text("🔄 **Uploading to Catbox...**")
                    catbox_url = await CatboxUploader.upload_with_progress(
                        media_file.file_bytes,
                        media_file.filename,
                        progress_tracker.update
                    )
                    
                    if not catbox_url:
                        await processing_msg.edit_text("❌ Failed to upload to Catbox.")
                        return
                    
                    update_data['img_url'] = catbox_url
                    update_data['file_hash'] = media_file.hash
                    
                    # Update channel message
                    character['img_url'] = catbox_url
                    new_message_id = await TelegramUploader.update_channel_message(
                        Character(
                            character_id=character['id'],
                            name=character['name'],
                            anime=character['anime'],
                            rarity=RarityLevel.from_number(
                                next(k for k, v in RarityLevel.get_all().items() if v == character['rarity'])
                            ),
                            media_file=MediaFile(catbox_url=catbox_url),
                            uploader_id=update.effective_user.id,
                            uploader_name=update.effective_user.first_name
                        ),
                        context,
                        character.get('message_id')
                    )
                    
                    update_data['message_id'] = new_message_id
                    await processing_msg.edit_text('✅ ɪᴍᴀɢᴇ ᴜᴘᴅᴀᴛᴇᴅ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ!')
                    
                except Exception as e:
                    await update.message.reply_text(f'❌ Failed to update image: {str(e)}')
                    return
                
            else:
                new_value = context.args[2]
                if not await UpdateHandler.validate_image_url(new_value):
                    await update.message.reply_text(
                        '❌ ɪɴᴠᴀʟɪᴅ ɪᴍᴀɢᴇ ᴜʀʟ!\n\nᴛʜᴇ ᴜʀʟ ᴍᴜꜱᴛ ʙᴇ ᴀ ᴠᴀʟɪᴅ ɪᴍᴀɢᴇ ᴜʀʟ.'
                    )
                    return
                update_data['img_url'] = new_value
        
        elif field in ['name', 'anime']:
            if len(context.args) != 3:
                await update.message.reply_text(
                    f'❌ ᴍɪꜱꜱɪɴɢ ᴠᴀʟᴜᴇ. ᴜꜱᴀɢᴇ: /update id field new_value'
                )
                return
            
            new_value = context.args[2]
            update_data[field] = CharacterFactory.format_name(new_value)
            
        elif field == 'rarity':
            if len(context.args) != 3:
                await update.message.reply_text(
                    f'❌ ᴍɪꜱꜱɪɴɢ ʀᴀʀɪᴛʏ ᴠᴀʟᴜᴇ. ᴜꜱᴀɢᴇ: /update id rarity 1-15'
                )
                return
            
            new_value = context.args[2]
            try:
                rarity_num = int(new_value)
                rarity = RarityLevel.from_number(rarity_num)
                if not rarity:
                    await update.message.reply_text(
                        f'❌ ɪɴᴠᴀʟɪᴅ ʀᴀʀɪᴛʏ. ᴘʟᴇᴀꜱᴇ ᴜꜱᴇ ᴀ ɴᴜᴍʙᴇʀ ʙᴇᴛᴡᴇᴇɴ 1 ᴀɴᴅ 15.'
                    )
                    return
                update_data['rarity'] = rarity.display_name
            except ValueError:
                await update.message.reply_text(f'❌ ʀᴀʀɪᴛʏ ᴍᴜꜱᴛ ʙᴇ ᴀ ɴᴜᴍʙᴇʀ (1-15).')
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
            await update.message.reply_text('❌ ꜰᴀɪʟᴇᴅ ᴛᴏ ᴜᴘᴅᴀᴛᴇ ᴄʜᴀʀᴀᴄᴛᴇʀ ɪɴ ᴅᴀᴛᴀʙᴀꜱᴇ.')
            return
        
        # Update channel message (if not img_url which was already handled)
        if field != 'img_url' and 'message_id' in updated_character:
            try:
                await TelegramUploader.update_channel_message(
                    Character(
                        character_id=updated_character['id'],
                        name=updated_character['name'],
                        anime=updated_character['anime'],
                        rarity=RarityLevel.from_number(
                            next(k for k, v in RarityLevel.get_all().items() if v == updated_character['rarity'])
                        ),
                        media_file=MediaFile(catbox_url=updated_character['img_url']),
                        uploader_id=update.effective_user.id,
                        uploader_name=update.effective_user.first_name
                    ),
                    context,
                    updated_character['message_id']
                )
            except Exception:
                pass  # Channel update is optional
        
        await update.message.reply_text('✅ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴜᴘᴅᴀᴛᴇᴅ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ!')


# ===================== APPLICATION SETUP =====================

# Register command handlers
application.add_handler(CommandHandler("upload", UploadHandler.handle))
application.add_handler(CommandHandler("delete", DeleteHandler.handle))
application.add_handler(CommandHandler("update", UpdateHandler.handle))


# ===================== CLEANUP =====================

async def cleanup():
    """Cleanup on shutdown"""
    await SessionManager.close()


# Register cleanup (you can use application.post_shutdown in your main.py)
# Example in main.py: application.post_shutdown(cleanup)