import asyncio
import hashlib
import io
import tempfile
import random
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, Any, List, Tuple
from functools import wraps
from contextlib import asynccontextmanager

import aiohttp
from aiohttp import ClientSession, TCPConnector, ClientError
from pymongo import ReturnDocument, ASCENDING
from telegram import Update, InputFile, Message, PhotoSize, Document, InputMediaPhoto, InputMediaDocument
from telegram.ext import CommandHandler, ContextTypes
from telegram.error import TelegramError, NetworkError, TimedOut, BadRequest

from shivu import application, collection, db, CHARA_CHANNEL_ID, SUPPORT_CHAT
from shivu.config import Config

# Setup logger
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# ===================== SETUP FUNCTION =====================
async def setup_database_indexes():
    """Create database indexes for optimal performance"""
    try:
        # Unique index on character ID
        await collection.create_index([("id", ASCENDING)], unique=True, background=True)

        # Regular index on file_hash for fast lookups and uniqueness to prevent duplicates
        await collection.create_index([("file_hash", ASCENDING)], unique=True, background=True)

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
        if getattr(message, "photo", None):
            return cls.PHOTO
        elif getattr(message, "document", None):
            mime_type = (message.document.mime_type or '') if message.document else ''
            if mime_type.startswith('image/'):
                return cls.DOCUMENT
        elif getattr(message, "video", None):
            return cls.VIDEO
        elif getattr(message, "animation", None):
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
    MAX_RETRIES: int = 5
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
            try:
                object.__setattr__(self, 'size', os.path.getsize(self.file_path))
            except Exception:
                object.__setattr__(self, 'size', 0)

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
                if os.path.exists(self.file_path):
                    os.unlink(self.file_path)
            except Exception as e:
                logger.debug(f"Cleanup failed for {self.file_path}: {e}")


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
        # Keep caption simple and safe (escape not included here — telegram parse_mode='HTML' used by caller)
        return (
            f"{self.character_id}: {self.name}\n"
            f"{self.anime}\n"
            f"{display_name}\n\n"
            f"𝑴𝒂𝒅𝒆 𝑩𝒚 ➥ <a href='tg://user?id={self.uploader_id}'>{self.uploader_name}</a>"
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
                    total=max(BotConfig.DOWNLOAD_TIMEOUT, BotConfig.UPLOAD_TIMEOUT),
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
            # Keep session open for reuse; close on application shutdown via cleanup()
            pass

    @classmethod
    async def close(cls):
        """Close the session"""
        async with cls._lock:
            if cls._session and not cls._session.closed:
                await cls._session.close()
                cls._session = None


# ===================== RETRY DECORATOR =====================

def retry_on_failure(max_attempts: int = 3, base_delay: float = 1.0, retry_exceptions: Tuple = (Exception,)):
    """Decorator for retrying failed operations with exponential backoff + jitter."""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except retry_exceptions as e:
                    last_exception = e
                    if attempt == max_attempts:
                        break
                    delay = base_delay * (2 ** (attempt - 1))
                    jitter = random.uniform(0, delay * 0.3)
                    sleep_for = delay + jitter
                    logger.info(f"Retryable error in {func.__name__}: {e}. Retrying in {sleep_for:.1f}s (attempt {attempt}/{max_attempts})")
                    await asyncio.sleep(sleep_for)
                except Exception as e:
                    # Non-retryable, re-raise
                    raise
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
            try:
                existing_id = int(existing_max['id'])
            except Exception:
                existing_id = 0
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

        file_path = None
        try:
            # Create temporary file
            with tempfile.NamedTemporaryFile(delete=False, suffix='.image') as tmp_file:
                file_path = tmp_file.name

            if media_type == MediaType.PHOTO:
                file = await reply_message.photo[-1].get_file()
                filename = f"photo_{reply_message.photo[-1].file_unique_id}.jpg"
                mime_type = 'image/jpeg'
                telegram_file_id = file.file_id
            else:  # DOCUMENT
                file = await reply_message.document.get_file()
                filename = reply_message.document.file_name or f"document_{reply_message.document.file_unique_id}"
                mime_type = reply_message.document.mime_type or ''
                telegram_file_id = file.file_id

                if not mime_type.startswith('image/'):
                    raise ValueError("❌ Only image files are allowed! The document must be an image file.")

            # Stream download to temporary file
            await file.download_to_drive(file_path)

            # Get file size
            import os
            size = os.path.getsize(file_path)

            media = MediaFile(
                file_path=file_path,
                media_type=media_type,
                filename=filename,
                mime_type=mime_type,
                size=size,
                telegram_file_id=telegram_file_id
            )
            return media

        except Exception as e:
            # Clean up temp file on error
            try:
                if file_path:
                    import os
                    if os.path.exists(file_path):
                        os.unlink(file_path)
            except Exception:
                pass
            raise ValueError(f"❌ Failed to process media: {str(e)}")


class CatboxUploader:
    """Handles uploads to Catbox with streaming and verification"""

    @staticmethod
    @retry_on_failure(max_attempts=BotConfig.MAX_RETRIES, base_delay=BotConfig.RETRY_DELAY, retry_exceptions=(ClientError, asyncio.TimeoutError))
    async def upload(file_path: str, filename: str, content_type: Optional[str] = None) -> Optional[str]:
        """Upload file to Catbox using streaming and verify the returned URL is reachable"""
        async with SessionManager.get_session() as session:
            data = aiohttp.FormData()
            data.add_field('reqtype', 'fileupload')

            # stream file
            with open(file_path, 'rb') as f:
                data.add_field(
                    'fileToUpload',
                    f,
                    filename=filename,
                    content_type=content_type or 'application/octet-stream'
                )

                try:
                    async with session.post(BotConfig.CATBOX_API, data=data, timeout=BotConfig.UPLOAD_TIMEOUT) as response:
                        text = (await response.text()).strip()
                        if 200 <= response.status < 300 and text.startswith('http'):
                            url = text
                            # Verify the URL is reachable (HEAD then GET fallback)
                            try:
                                async with session.head(url, timeout=30) as head_resp:
                                    if 200 <= head_resp.status < 400:
                                        return url
                            except Exception:
                                try:
                                    async with session.get(url, timeout=30) as get_resp:
                                        if 200 <= get_resp.status < 400:
                                            return url
                                except Exception as ve:
                                    logger.info(f"Verification failed for catbox url {url}: {ve}")
                                    raise ClientError(f"Uploaded but verification failed for {url}")
                        # Non-success or unexpected body -> raise to trigger retry
                        raise ClientError(f"Catbox upload failed: status={response.status} body={text!r}")
                except Exception:
                    raise


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
        media_source: str,
        is_update: bool = False
    ) -> Optional[int]:
        """Upload character to channel using media_source which can be a file_id or a public URL"""
        try:
            caption = character.get_caption("Updated" if is_update else "Added")

            # If the media is image (photo/document with image mime), send as photo using URL/file_id
            if character.media_file.media_type == MediaType.DOCUMENT and character.media_file.mime_type and character.media_file.mime_type.startswith('image/'):
                message = await context.bot.send_photo(
                    chat_id=CHARA_CHANNEL_ID,
                    photo=media_source,
                    caption=caption,
                    parse_mode='HTML'
                )
            elif character.media_file.media_type == MediaType.PHOTO:
                # For photos, media_source (URL) is preferred (guaranteed persistent)
                message = await context.bot.send_photo(
                    chat_id=CHARA_CHANNEL_ID,
                    photo=media_source,
                    caption=caption,
                    parse_mode='HTML'
                )
            else:  # fallback to document
                message = await context.bot.send_document(
                    chat_id=CHARA_CHANNEL_ID,
                    document=media_source,
                    caption=caption,
                    parse_mode='HTML'
                )

            return message.message_id

        except BadRequest as e:
            error_msg = str(e).lower()
            if "not found" in error_msg or "message to edit not found" in error_msg:
                # retry once
                return await TelegramUploader.upload_to_channel(character, context, media_source, is_update)
            raise
        except Exception as e:
            raise ValueError(f"Failed to upload to channel: {str(e)}")

    @staticmethod
    async def update_channel_message(
        character: Character,
        context: ContextTypes.DEFAULT_TYPE,
        old_message_id: Optional[int] = None
    ) -> Optional[int]:
        """Update existing channel message with new media if possible, otherwise send new message"""
        try:
            if not old_message_id:
                # No existing message, send new one
                return await TelegramUploader.upload_to_channel(
                    character,
                    context,
                    character.media_file.catbox_url or character.media_file.telegram_file_id,
                    True
                )

            caption = character.get_caption("Updated")

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
                else:
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
                # If edit_message_media fails (message too old or can't be edited), send new message
                if "message not found" in error_msg or "message to edit not found" in error_msg or "message can't be edited" in error_msg:
                    return await TelegramUploader.upload_to_channel(
                        character,
                        context,
                        character.media_file.catbox_url or character.media_file.telegram_file_id,
                        True
                    )
                else:
                    # Try to at least update caption
                    try:
                        await context.bot.edit_message_caption(
                            chat_id=CHARA_CHANNEL_ID,
                            message_id=old_message_id,
                            caption=caption,
                            parse_mode='HTML'
                        )
                        return old_message_id
                    except Exception:
                        return await TelegramUploader.upload_to_channel(
                            character,
                            context,
                            character.media_file.catbox_url or character.media_file.telegram_file_id,
                            True
                        )

        except Exception as e:
            # For any failure, send a new message
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
ɴᴇᴢᴜ��ᴏ ᴋᴀᴍᴀᴅᴏ 
ᴅᴇᴍᴏɴ ꜱʟᴀʏᴇʀ 
4
"""

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
        """Handle /upload command with deterministic flow:
           Download → Catbox Upload → Telegram Upload → Save to DB
        """
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

        media_file = None
        try:
            # 1) Download
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

            # 2) Upload to Catbox
            await processing_msg.edit_text("🔄 **Uploading to Catbox (ensuring persistent URL)...**")
            try:
                catbox_url = await CatboxUploader.upload(media_file.file_path, media_file.filename, content_type=media_file.mime_type)
            except Exception as e:
                logger.exception("Catbox upload failed")
                await processing_msg.edit_text(f"❌ Failed to upload to Catbox: {str(e)}")
                return

            if not catbox_url:
                await processing_msg.edit_text("❌ Failed to upload to Catbox. Please try again.")
                return

            media_file.catbox_url = catbox_url

            # 3) Telegram Upload (use persistent URL to ensure durability)
            await processing_msg.edit_text("🔄 **Posting to channel...**")

            # Create character (ID will be assigned next)
            character = await CharacterFactory.create_from_input(
                character_name,
                anime_name,
                rarity_num,
                media_file,
                update.effective_user.id,
                update.effective_user.first_name
            )

            try:
                message_id = await TelegramUploader.upload_to_channel(character, context, media_source=media_file.catbox_url, is_update=False)
            except Exception as e:
                logger.exception("Telegram post failed after Catbox upload")
                await processing_msg.edit_text(f"❌ Failed to post to channel after Catbox upload: {str(e)}")
                # Optionally insert a 'pending' record for background retry. For now, inform and abort.
                return

            if not message_id:
                await processing_msg.edit_text("❌ Failed to post to channel. Please try again.")
                return

            # 4) Save to DB (idempotent)
            character.message_id = message_id
            character.media_file.catbox_url = catbox_url

            try:
                await collection.update_one(
                    {'file_hash': media_file.hash},
                    {'$setOnInsert': character.to_dict()},
                    upsert=True
                )
            except Exception as e:
                logger.exception("DB insert failed")
                await processing_msg.edit_text(f"❌ Failed to save to database: {str(e)}")
                # We already posted to channel; consider scheduling reconciliation or manual cleanup.
                return

            # Cleanup temp file
            media_file.cleanup()

            # Success
            await processing_msg.edit_text("✅ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴀᴅᴅᴇᴅ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ!")

        except ValueError as e:
            await processing_msg.edit_text(str(e))
        except Exception as e:
            logger.exception("Unexpected upload error")
            error_msg = f"❌ ᴜᴘʟᴏᴀᴅ ꜰᴀɪʟᴇᴅ!\n\nᴇʀʀᴏʀ: {str(e)[:200]}"
            if SUPPORT_CHAT:
                error_msg += f"\n\nɪꜰ ᴛʜɪꜱ ᴇʀʀᴏʀ ᴘᴇʀꜱɪꜱᴛꜱ, ᴄᴏɴᴛᴀᴄᴛ: {SUPPORT_CHAT}"
            await processing_msg.edit_text(error_msg)
        finally:
            # Ensure cleanup of temp file if anything left
            try:
                if media_file:
                    media_file.cleanup()
            except Exception:
                pass


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
            if 'message_id' in character and character.get('message_id'):
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
                await update.message.reply_text('✅ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴅᴇʟᴇᴛᴇᴅ ꜰʀᴏᴍ ᴅᴀᴛᴀʙᴀꜱᴇ (ᴄʜᴀɴɴᴇʟ ᴍᴇꜱꜱᴀɢᴇ ᴡᴀꜱ ᴀʟʀᴇᴀᴅʏ ɢᴏne).')
            else:
                await update.message.reply_text(
                    f'✅ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴅᴇʟᴇᴛᴇᴅ ꜰʀᴏᴍ ᴅᴀᴛᴀʙᴀꜱᴇ.\n\n⚠️ ᴄᴏᴜʟᴅ ɴᴏᴛ ᴅᴇʟᴇᴛᴇ ꜰʀᴏᴍ ᴄʜᴀɴɴᴇʟ: {str(e)}'
                )
        except Exception as e:
            await update.message.reply_text(
                f'✅ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴅᴇʟᴇᴛᴇᴅ ꜱᴜᴄᴄᴇꜱꜱ꜠ʟʟʏ ꜰʀᴏᴍ ᴅᴀᴛᴀʙᴀꜱᴇ.'
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
        """Handle /update command with validation fixes and deterministic image update flow"""
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
                # Expect reply with image to update image via same flow
                if not (update.message.reply_to_message and 
                       (update.message.reply_to_message.photo or 
                        update.message.reply_to_message.document)):
                    await update.message.reply_text(
                        '📸 ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴘʜᴏᴛᴏ ʀᴇǫᴜɪʀᴇᴅ!\n\nʀᴇᴘʟʏ ᴛᴏ ᴀ ᴘʜᴏᴛᴏ ᴀɴᴅ ᴜꜱᴇ: /update id img_url'
                    )
                    return

                processing_msg = await update.message.reply_text("🔄 **Processing new image...**")
                media_file = None
                try:
                    # Download new media
                    media_file = await MediaHandler.extract_from_reply(update.message.reply_to_message)

                    if not media_file or not media_file.is_valid_image:
                        await processing_msg.edit_text("❌ Invalid media! Only photos and image documents are allowed.")
                        return

                    if not media_file.is_valid_size:
                        await processing_msg.edit_text(
                            f"❌ File too large! Maximum size: {BotConfig.MAX_FILE_SIZE / (1024 * 1024):.1f} MB"
                        )
                        return

                    # Upload to Catbox
                    await processing_msg.edit_text("🔄 **Uploading new image to Catbox...**")
                    try:
                        catbox_url = await CatboxUploader.upload(media_file.file_path, media_file.filename, content_type=media_file.mime_type)
                    except Exception as e:
                        logger.exception("Catbox upload failed (update)")
                        await processing_msg.edit_text(f"❌ Failed to upload to Catbox: {str(e)}")
                        return

                    if not catbox_url:
                        await processing_msg.edit_text("❌ Failed to upload to Catbox.")
                        return

                    media_file.catbox_url = catbox_url

                    # Prepare a Character instance for posting/updating
                    char_for_upload = Character(
                        character_id=character['id'],
                        name=character['name'],
                        anime=character['anime'],
                        rarity=character['rarity'],
                        media_file=media_file,
                        uploader_id=update.effective_user.id,
                        uploader_name=update.effective_user.first_name
                    )

                    # Update channel message (attempt to edit, otherwise post new)
                    await processing_msg.edit_text("🔄 **Updating channel message...**")
                    new_message_id = await TelegramUploader.update_channel_message(
                        char_for_upload,
                        context,
                        character.get('message_id')
                    )

                    # Save changes to DB
                    update_data['img_url'] = catbox_url
                    update_data['file_hash'] = media_file.hash
                    update_data['message_id'] = new_message_id
                    from datetime import datetime
                    update_data['updated_at'] = datetime.utcnow().isoformat()

                    await collection.find_one_and_update(
                        {'id': char_id},
                        {'$set': update_data},
                        return_document=ReturnDocument.AFTER
                    )

                    await processing_msg.edit_text('✅ ɪᴍᴀɢᴇ ᴜᴘᴅᴀᴛᴇᴅ ꜱᴜᴄᴄᴇꜱꜱ꜠ʟʟʏ!')

                except Exception as e:
                    logger.exception("Image update failed")
                    await update.message.reply_text(f'❌ Failed to update image: {str(e)}')
                    return
                finally:
                    try:
                        if media_file:
                            media_file.cleanup()
                    except Exception:
                        pass

            else:
                # Update provided URL in args
                if len(context.args) < 3:
                    await update.message.reply_text('❌ Missing image URL. Usage: /update id img_url URL')
                    return

                new_value = context.args[2]
                update_data['img_url'] = new_value

        elif field in ['name', 'anime']:
            if len(context.args) < 3:
                await update.message.reply_text(
                    f'❌ Missing value. Usage: /update id {field} new_value'
                )
                return

            new_value = context.args[2]
            update_data[field] = CharacterFactory.format_name(new_value)

        elif field == 'rarity':
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

        # If any non-img fields were changed, apply update and refresh channel message caption
        if update_data:
            from datetime import datetime
            update_data['updated_at'] = datetime.utcnow().isoformat()

            updated_character = await collection.find_one_and_update(
                {'id': char_id},
                {'$set': update_data},
                return_document=ReturnDocument.AFTER
            ) if False else await collection.find_one_and_update(
                {'id': char_id},
                {'$set': update_data},
                return_document=ReturnDocument.AFTER
            )

            if not updated_character:
                await update.message.reply_text('❌ Failed to update character in database.')
                return

            # If not img_url (which was handled above), update channel message
            if field != 'img_url' and updated_character.get('message_id'):
                try:
                    channel_char = Character(
                        character_id=updated_character['id'],
                        name=updated_character['name'],
                        anime=updated_character['anime'],
                        rarity=updated_character['rarity'],
                        media_file=MediaFile(catbox_url=updated_character.get('img_url', None)),
                        uploader_id=update.effective_user.id,
                        uploader_name=update.effective_user.first_name
                    )
                    await TelegramUploader.update_channel_message(
                        channel_char,
                        context,
                        updated_character['message_id']
                    )
                except Exception:
                    pass  # optional

            await update.message.reply_text('✅ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴜᴘᴅᴀᴛᴇᴅ ꜱᴜᴄᴄᴇꜱꜱ꜠ʟʟʏ!')


# ===================== APPLICATION SETUP =====================

# Register command handlers with non-blocking option
application.add_handler(CommandHandler("upload", UploadHandler.handle, block=False))
application.add_handler(CommandHandler("delete", DeleteHandler.handle, block=False))
application.add_handler(CommandHandler("update", UpdateHandler.handle, block=False))


# ===================== CLEANUP =====================

async def cleanup():
    """Cleanup on shutdown"""
    await SessionManager.close()
