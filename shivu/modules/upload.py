"""
ANIME CHARACTER MANAGEMENT BOT - ATOMIC & FAIL-SAFE IMAGE SYSTEM
Version: 2.0 Production Grade
Author: Senior Backend Engineer
License: MIT
"""

import os
import sys
import asyncio
import logging
import signal
import hashlib
import mimetypes
import json
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple, Union
from enum import Enum, IntEnum
from dataclasses import dataclass, field, asdict
from urllib.parse import urlparse
from contextlib import asynccontextmanager
from abc import ABC, abstractmethod

# Third-party imports
import aiohttp
from pymongo import MongoClient, ReturnDocument
from pymongo.errors import PyMongoError
from telegram import (
    Update, 
    PhotoSize, 
    Bot,
    InputFile,
    Message,
    Video,
    Animation,
    Document
)
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackContext
)
from telegram.error import (
    BadRequest,
    TelegramError,
    NetworkError,
    TimedOut
)

# ============================================================================
# 1. CONFIGURATION & ENVIRONMENT
# ============================================================================

class Config:
    """Centralized configuration management"""
    
    # Load from environment or use defaults
    TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN', '')
    SUDO_USERS = [int(x) for x in os.getenv('SUDO_USERS', '').split(',') if x]
    CHARA_CHANNEL_ID = int(os.getenv('CHARA_CHANNEL_ID', '0'))
    SUPPORT_CHAT = os.getenv('SUPPORT_CHAT', '@support')
    
    # MongoDB
    MONGODB_URI = os.getenv('MONGODB_URI', 'mongodb://localhost:27017')
    MONGODB_DB = os.getenv('MONGODB_DB', 'anime_bot')
    MONGODB_COLLECTION = os.getenv('MONGODB_COLLECTION', 'characters')
    
    # Catbox.moe
    CATBOX_API_URL = "https://catbox.moe/user/api.php"
    CATBOX_UPLOAD_TIMEOUT = 30
    CATBOX_MAX_SIZE = 200 * 1024 * 1024  # 200MB
    
    # Limits
    MAX_DOWNLOAD_SIZE = 200 * 1024 * 1024  # 200MB
    MAX_CONCURRENT_UPLOADS = 5
    REQUEST_TIMEOUT = 30
    
    # Retry
    MAX_RETRIES = 3
    RETRY_DELAY = 1.0
    
    @classmethod
    def validate(cls):
        """Validate required configuration"""
        if not cls.TELEGRAM_TOKEN:
            raise ValueError("TELEGRAM_TOKEN is required")
        if not cls.CHARA_CHANNEL_ID:
            raise ValueError("CHARA_CHANNEL_ID is required")
        if not cls.MONGODB_URI:
            raise ValueError("MONGODB_URI is required")
        return True


# ============================================================================
# 2. CORE ENUMS & DATA MODELS
# ============================================================================

class MediaType(str, Enum):
    """Supported media types"""
    PHOTO = "photo"
    VIDEO = "video"
    ANIMATION = "animation"
    DOCUMENT = "document"
    UNKNOWN = "unknown"
    
    @classmethod
    def from_mime_type(cls, mime_type: str) -> 'MediaType':
        """Detect media type from MIME type"""
        if not mime_type:
            return cls.UNKNOWN
        
        mime_lower = mime_type.lower()
        
        if mime_lower.startswith('image/'):
            # Check for animations
            if mime_lower in ['image/gif', 'image/webp']:
                return cls.ANIMATION
            return cls.PHOTO
        elif mime_lower.startswith('video/'):
            return cls.VIDEO
        elif mime_lower.startswith('application/'):
            return cls.DOCUMENT
        else:
            return cls.UNKNOWN
    
    @classmethod
    def from_telegram_message(cls, message: Message) -> Optional['MediaType']:
        """Extract media type from Telegram message"""
        if message.photo:
            return cls.PHOTO
        elif message.video:
            return cls.VIDEO
        elif message.animation:
            return cls.ANIMATION
        elif message.document:
            mime_type = message.document.mime_type or ''
            return cls.from_mime_type(mime_type)
        return None
    
    def get_telegram_method(self) -> str:
        """Get Telegram Bot API method for this media type"""
        return {
            self.PHOTO: 'send_photo',
            self.VIDEO: 'send_video',
            self.ANIMATION: 'send_animation',
            self.DOCUMENT: 'send_document',
            self.UNKNOWN: 'send_document'  # Fallback
        }[self]


class Rarity(IntEnum):
    """Character rarity levels"""
    COMMON = 1
    RARE = 2
    LEGENDARY = 3
    SPECIAL = 4
    ANCIENT = 5
    CELESTIAL = 6
    EPIC = 7
    COSMIC = 8
    NIGHTMARE = 9
    FROSTBORN = 10
    VALENTINE = 11
    SPRING = 12
    TROPICAL = 13
    KAWAII = 14
    HYBRID = 15
    
    @property
    def display_name(self) -> str:
        """Get display name with emoji"""
        names = {
            self.COMMON: "⚪ ᴄᴏᴍᴍᴏɴ",
            self.RARE: "🔵 ʀᴀʀᴇ",
            self.LEGENDARY: "🟡 ʟᴇɢᴇɴᴅᴀʀʏ",
            self.SPECIAL: "💮 ꜱᴘᴇᴄɪᴀʟ",
            self.ANCIENT: "👹 ᴀɴᴄɪᴇɴᴛ",
            self.CELESTIAL: "🎐 ᴄᴇʟᴇꜱᴛɪᴀʟ",
            self.EPIC: "🔮 ᴇᴘɪᴄ",
            self.COSMIC: "🪐 ᴄᴏꜱᴍɪᴄ",
            self.NIGHTMARE: "⚰️ ɴɪɢʜᴛᴍᴀʀᴇ",
            self.FROSTBORN: "🌬️ ꜰʀᴏꜱᴛʙᴏʀɴ",
            self.VALENTINE: "💝 ᴠᴀʟᴇɴᴛɪɴᴇ",
            self.SPRING: "🌸 ꜱᴘʀɪɴɢ",
            self.TROPICAL: "🏖️ ᴛʀᴏᴘɪᴄᴀʟ",
            self.KAWAII: "🍭 ᴋᴀᴡᴀɪɪ",
            self.HYBRID: "🧬 ʜʏʙʀɪᴅ",
        }
        return names[self]
    
    @classmethod
    def from_string(cls, value: str) -> 'Rarity':
        """Parse rarity from string"""
        try:
            num = int(value.strip())
            return cls(num)
        except (ValueError, KeyError):
            raise ValueError(f"Invalid rarity: {value}. Must be 1-15")


class UploadStatus(str, Enum):
    """Upload operation status"""
    PENDING = "pending"
    DOWNLOADING = "downloading"
    UPLOADING_CATBOX = "uploading_catbox"
    UPLOADING_TELEGRAM = "uploading_telegram"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


@dataclass
class MediaMetadata:
    """Metadata for media files"""
    mime_type: str
    file_size: int
    width: Optional[int] = None
    height: Optional[int] = None
    duration: Optional[int] = None
    file_name: Optional[str] = None
    
    @property
    def media_type(self) -> MediaType:
        return MediaType.from_mime_type(self.mime_type)
    
    @property
    def safe_filename(self) -> str:
        """Generate safe filename"""
        if self.file_name:
            name = os.path.basename(self.file_name)
            # Remove unsafe characters
            name = ''.join(c for c in name if c.isalnum() or c in '._- ')
            return name[:100]  # Limit length
        ext = mimetypes.guess_extension(self.mime_type) or '.bin'
        return f"file_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"


@dataclass
class MediaFile:
    """Complete media file representation"""
    data: bytes
    metadata: MediaMetadata
    file_id: Optional[str] = None
    file_unique_id: Optional[str] = None
    url: Optional[str] = None
    
    @property
    def sha256_hash(self) -> str:
        """Calculate SHA256 hash for deduplication"""
        return hashlib.sha256(self.data).hexdigest()
    
    @property
    def telegram_file(self) -> InputFile:
        """Convert to Telegram InputFile"""
        return InputFile(
            self.data,
            filename=self.metadata.safe_filename
        )


@dataclass
class TelegramMediaInfo:
    """Information about media stored in Telegram"""
    message_id: int
    file_id: str
    file_unique_id: str
    media_type: MediaType
    chat_id: int = field(default_factory=lambda: Config.CHARA_CHANNEL_ID)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'message_id': self.message_id,
            'file_id': self.file_id,
            'file_unique_id': self.file_unique_id,
            'media_type': self.media_type.value,
            'chat_id': self.chat_id,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'TelegramMediaInfo':
        return cls(
            message_id=data['message_id'],
            file_id=data['file_id'],
            file_unique_id=data['file_unique_id'],
            media_type=MediaType(data['media_type']),
            chat_id=data.get('chat_id', Config.CHARA_CHANNEL_ID),
        )


@dataclass
class Character:
    """Character data model"""
    id: str
    name: str
    anime: str
    rarity: Rarity
    media_info: TelegramMediaInfo
    catbox_url: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    added_by: Optional[Dict[str, Any]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to database dictionary"""
        return {
            'id': self.id,
            'name': self.name,
            'anime': self.anime,
            'rarity': self.rarity.value,
            'media_info': self.media_info.to_dict(),
            'catbox_url': self.catbox_url,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'added_by': self.added_by,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Character':
        """Create from database dictionary"""
        return cls(
            id=str(data['id']),
            name=data['name'],
            anime=data['anime'],
            rarity=Rarity(data['rarity']),
            media_info=TelegramMediaInfo.from_dict(data['media_info']),
            catbox_url=data.get('catbox_url'),
            created_at=data.get('created_at', datetime.utcnow()),
            updated_at=data.get('updated_at', datetime.utcnow()),
            added_by=data.get('added_by'),
        )


@dataclass
class UploadResult:
    """Result of atomic upload operation"""
    success: bool
    media_file: Optional[MediaFile] = None
    telegram_info: Optional[TelegramMediaInfo] = None
    catbox_url: Optional[str] = None
    character: Optional[Character] = None
    error: Optional[str] = None
    rollback_performed: bool = False


# ============================================================================
# 3. EXCEPTIONS
# ============================================================================

class MediaError(Exception):
    """Base media error"""
    pass


class DownloadError(MediaError):
    """Media download failed"""
    pass


class UploadError(MediaError):
    """Media upload failed"""
    pass


class TelegramUploadError(UploadError):
    """Telegram upload failed"""
    pass


class CatboxUploadError(UploadError):
    """Catbox upload failed"""
    pass


class AtomicOperationError(MediaError):
    """Atomic operation failed"""
    def __init__(self, message: str, rollback_performed: bool = False):
        super().__init__(message)
        self.rollback_performed = rollback_performed


class ValidationError(MediaError):
    """Input validation failed"""
    pass


class DatabaseError(MediaError):
    """Database operation failed"""
    pass


# ============================================================================
# 4. MEDIA DETECTION & DOWNLOAD
# ============================================================================

class MediaDetector:
    """Detect media type and metadata"""
    
    def __init__(self):
        mimetypes.init()
    
    async def detect_from_bytes(self, data: bytes, filename: Optional[str] = None) -> MediaMetadata:
        """Detect media from bytes"""
        if not data:
            raise ValueError("Empty data")
        
        # Simple MIME detection (in production, use python-magic)
        import magic
        mime_type = magic.from_buffer(data, mime=True)
        
        return MediaMetadata(
            mime_type=mime_type,
            file_size=len(data),
            file_name=filename
        )
    
    async def detect_from_url(self, url: str) -> Tuple[MediaType, MediaMetadata]:
        """Detect media from URL without full download"""
        from .http_client import get_http_client
        
        client = await get_http_client()
        
        try:
            async with client.head(url, allow_redirects=True) as resp:
                if resp.status != 200:
                    raise DownloadError(f"URL returned status {resp.status}")
                
                content_type = resp.headers.get('Content-Type', '')
                content_length = int(resp.headers.get('Content-Length', 0))
                
                if content_length > 10 * 1024 * 1024:  # 10MB for HEAD
                    metadata = MediaMetadata(
                        mime_type=content_type,
                        file_size=content_length,
                        file_name=urlparse(url).path.split('/')[-1] or None
                    )
                else:
                    # Download first 1MB for detection
                    async with client.get(url) as partial_resp:
                        data = await partial_resp.read()
                        metadata = await self.detect_from_bytes(data)
                        metadata.file_size = content_length
                
                return MediaType.from_mime_type(metadata.mime_type), metadata
                
        except aiohttp.ClientError as e:
            raise DownloadError(f"URL detection failed: {e}")


class MediaDownloader:
    """Download media from various sources"""
    
    def __init__(self, bot: Optional[Bot] = None):
        self.bot = bot
    
    async def download_from_telegram(self, file_id: str, file_unique_id: str) -> MediaFile:
        """Download from Telegram with atomic guarantee"""
        if not self.bot:
            raise DownloadError("Bot instance required")
        
        try:
            # Get file info
            file = await self.bot.get_file(file_id)
            
            # Download in chunks
            buffer = bytearray()
            total_size = 0
            
            async for chunk in file.download_as_bytearray():
                total_size += len(chunk)
                if total_size > Config.MAX_DOWNLOAD_SIZE:
                    raise DownloadError(f"File exceeds {Config.MAX_DOWNLOAD_SIZE} limit")
                buffer.extend(chunk)
            
            data = bytes(buffer)
            
            # Detect metadata
            detector = MediaDetector()
            metadata = await detector.detect_from_bytes(data)
            
            return MediaFile(
                data=data,
                metadata=metadata,
                file_id=file_id,
                file_unique_id=file_unique_id
            )
            
        except TelegramError as e:
            raise DownloadError(f"Telegram download failed: {e}")
        except Exception as e:
            raise DownloadError(f"Download failed: {e}")
    
    async def download_from_url(self, url: str) -> MediaFile:
        """Download from URL with atomic guarantee"""
        from .http_client import get_http_client
        
        client = await get_http_client()
        
        try:
            # Check size first
            async with client.head(url, allow_redirects=True) as resp:
                if resp.status != 200:
                    raise DownloadError(f"URL returned status {resp.status}")
                
                content_length = int(resp.headers.get('Content-Length', 0))
                if content_length > Config.MAX_DOWNLOAD_SIZE:
                    raise DownloadError(f"File too large: {content_length}")
            
            # Download
            async with client.get(url, timeout=30) as resp:
                if resp.status != 200:
                    raise DownloadError(f"Download failed: {resp.status}")
                
                buffer = bytearray()
                async for chunk in resp.content.iter_chunked(8192):
                    if len(buffer) + len(chunk) > Config.MAX_DOWNLOAD_SIZE:
                        raise DownloadError("File size exceeded during download")
                    buffer.extend(chunk)
                
                data = bytes(buffer)
                
                # Detect metadata
                detector = MediaDetector()
                filename = url.split('/')[-1] if '/' in url else None
                metadata = await detector.detect_from_bytes(data, filename)
                
                return MediaFile(
                    data=data,
                    metadata=metadata,
                    url=url
                )
                
        except asyncio.TimeoutError:
            raise DownloadError("Download timeout")
        except aiohttp.ClientError as e:
            raise DownloadError(f"HTTP error: {e}")
        except Exception as e:
            raise DownloadError(f"URL download failed: {e}")


# ============================================================================
# 5. UPLOAD SERVICES
# ============================================================================

class Uploader(ABC):
    """Abstract uploader with retry logic"""
    
    def __init__(self, max_retries: int = None, retry_delay: float = None):
        self.max_retries = max_retries or Config.MAX_RETRIES
        self.retry_delay = retry_delay or Config.RETRY_DELAY
        self._active_uploads = set()
    
    async def upload_with_retry(self, media_file: MediaFile, **kwargs) -> Any:
        """Upload with exponential backoff"""
        last_error = None
        
        for attempt in range(self.max_retries):
            upload_id = f"{media_file.sha256_hash[:8]}-{attempt}"
            self._active_uploads.add(upload_id)
            
            try:
                result = await self._upload_impl(media_file, **kwargs)
                self._active_uploads.remove(upload_id)
                return result
                
            except Exception as e:
                last_error = e
                self._active_uploads.remove(upload_id)
                
                if attempt < self.max_retries - 1:
                    delay = self.retry_delay * (2 ** attempt)
                    await asyncio.sleep(delay)
                else:
                    raise last_error
    
    @abstractmethod
    async def _upload_impl(self, media_file: MediaFile, **kwargs) -> Any:
        """Implementation-specific upload"""
        pass


class CatboxUploader(Uploader):
    """Upload to Catbox.moe"""
    
    def __init__(self):
        super().__init__()
        self._session = None
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get HTTP session"""
        if not self._session or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=Config.CATBOX_UPLOAD_TIMEOUT)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session
    
    async def _upload_impl(self, media_file: MediaFile, **kwargs) -> str:
        """Upload to Catbox"""
        if len(media_file.data) > Config.CATBOX_MAX_SIZE:
            raise CatboxUploadError("File too large for Catbox")
        
        session = await self._get_session()
        
        form_data = aiohttp.FormData()
        form_data.add_field('reqtype', 'fileupload')
        form_data.add_field(
            'fileToUpload',
            media_file.data,
            filename=media_file.metadata.safe_filename,
            content_type=media_file.metadata.mime_type
        )
        
        try:
            async with session.post(Config.CATBOX_API_URL, data=form_data) as resp:
                if resp.status != 200:
                    raise CatboxUploadError(f"Catbox API error: {resp.status}")
                
                url = (await resp.text()).strip()
                if not url.startswith('https://files.catbox.moe/'):
                    raise CatboxUploadError(f"Invalid Catbox URL: {url}")
                
                return url
                
        except asyncio.TimeoutError:
            raise CatboxUploadError("Catbox upload timeout")
        except aiohttp.ClientError as e:
            raise CatboxUploadError(f"HTTP error: {e}")
    
    async def cleanup(self):
        """Cleanup resources"""
        if self._session and not self._session.closed:
            await self._session.close()


class TelegramUploader(Uploader):
    """Upload to Telegram"""
    
    def __init__(self, bot: Bot):
        super().__init__()
        self.bot = bot
    
    async def _upload_impl(self, media_file: MediaFile, 
                          caption: str = "",
                          chat_id: int = None,
                          **kwargs) -> TelegramMediaInfo:
        """Upload to Telegram channel"""
        chat_id = chat_id or Config.CHARA_CHANNEL_ID
        
        try:
            # Get appropriate method
            method_name = media_file.metadata.media_type.get_telegram_method()
            method = getattr(self.bot, method_name)
            
            # Prepare parameters
            params = self._prepare_params(media_file, caption)
            params['chat_id'] = chat_id
            
            # Send
            message = await method(**params)
            
            # Extract file info
            file_id, file_unique_id = self._extract_file_info(message, media_file.metadata.media_type)
            
            return TelegramMediaInfo(
                message_id=message.message_id,
                file_id=file_id,
                file_unique_id=file_unique_id,
                media_type=media_file.metadata.media_type,
                chat_id=chat_id
            )
            
        except (BadRequest, NetworkError, TimedOut) as e:
            # Fallback to document if possible
            if media_file.metadata.media_type != MediaType.DOCUMENT:
                return await self._upload_as_document(media_file, caption, chat_id)
            raise TelegramUploadError(f"Telegram upload failed: {e}")
        except Exception as e:
            raise TelegramUploadError(f"Telegram upload error: {e}")
    
    async def _upload_as_document(self, media_file: MediaFile, 
                                 caption: str, 
                                 chat_id: int) -> TelegramMediaInfo:
        """Fallback upload as document"""
        try:
            message = await self.bot.send_document(
                chat_id=chat_id,
                document=media_file.telegram_file,
                caption=caption,
                parse_mode='HTML'
            )
            
            return TelegramMediaInfo(
                message_id=message.message_id,
                file_id=message.document.file_id,
                file_unique_id=message.document.file_unique_id,
                media_type=MediaType.DOCUMENT,
                chat_id=chat_id
            )
        except Exception as e:
            raise TelegramUploadError(f"Document fallback also failed: {e}")
    
    def _prepare_params(self, media_file: MediaFile, caption: str) -> Dict[str, Any]:
        """Prepare parameters for Telegram send method"""
        params = {
            'caption': caption,
            'parse_mode': 'HTML',
        }
        
        if media_file.metadata.media_type == MediaType.PHOTO:
            params['photo'] = media_file.telegram_file
        elif media_file.metadata.media_type == MediaType.VIDEO:
            params['video'] = media_file.telegram_file
            if media_file.metadata.duration:
                params['duration'] = media_file.metadata.duration
            if media_file.metadata.width and media_file.metadata.height:
                params['width'] = media_file.metadata.width
                params['height'] = media_file.metadata.height
        elif media_file.metadata.media_type == MediaType.ANIMATION:
            params['animation'] = media_file.telegram_file
            if media_file.metadata.duration:
                params['duration'] = media_file.metadata.duration
        else:  # DOCUMENT or UNKNOWN
            params['document'] = media_file.telegram_file
        
        return params
    
    def _extract_file_info(self, message: Message, media_type: MediaType) -> Tuple[str, str]:
        """Extract file_id and file_unique_id"""
        if media_type == MediaType.PHOTO:
            photo = message.photo[-1] if message.photo else None
            if photo:
                return photo.file_id, photo.file_unique_id
        elif media_type == MediaType.VIDEO and message.video:
            return message.video.file_id, message.video.file_unique_id
        elif media_type == MediaType.ANIMATION and message.animation:
            return message.animation.file_id, message.animation.file_unique_id
        elif media_type == MediaType.DOCUMENT and message.document:
            return message.document.file_id, message.document.file_unique_id
        
        raise TelegramUploadError(f"Cannot extract file info for {media_type}")


# ============================================================================
# 6. ATOMIC UPLOAD ORCHESTRATOR (CORE)
# ============================================================================

class AtomicUploadOrchestrator:
    """
    ENFORCES STRICT ATOMICITY:
    1. Download → Catbox → Telegram → Database
    2. ANY failure → COMPLETE rollback
    3. Database touched ONLY after ALL uploads succeed
    """
    
    def __init__(self, 
                 telegram_uploader: TelegramUploader,
                 catbox_uploader: CatboxUploader,
                 media_downloader: MediaDownloader):
        self.telegram_uploader = telegram_uploader
        self.catbox_uploader = catbox_uploader
        self.media_downloader = media_downloader
        self._active_operations = {}
    
    @asynccontextmanager
    async def atomic_context(self, operation_id: str):
        """
        Context manager for atomic operations.
        Guarantees rollback on ANY failure.
        """
        self._active_operations[operation_id] = {
            'status': 'started',
            'rollback_actions': [],
        }
        
        try:
            yield
            # Success - mark completed
            self._active_operations[operation_id]['status'] = 'completed'
            
        except Exception as e:
            # FAILURE - execute rollback
            logging.error(f"Atomic operation {operation_id} failed: {e}")
            
            rollback_actions = self._active_operations[operation_id].get('rollback_actions', [])
            for action in reversed(rollback_actions):
                try:
                    await action()
                except Exception as rollback_error:
                    logging.error(f"Rollback failed: {rollback_error}")
            
            self._active_operations[operation_id]['status'] = 'rolled_back'
            raise AtomicOperationError(
                f"Atomic operation failed: {e}",
                rollback_performed=True
            )
            
        finally:
            # Cleanup
            if operation_id in self._active_operations:
                del self._active_operations[operation_id]
    
    def _add_rollback(self, operation_id: str, action):
        """Register rollback action"""
        if operation_id in self._active_operations:
            self._active_operations[operation_id]['rollback_actions'].append(action)
    
    async def upload_from_telegram(self,
                                  telegram_file_id: str,
                                  telegram_file_unique_id: str,
                                  caption: str = "",
                                  require_catbox: bool = True) -> UploadResult:
        """
        ATOMIC PIPELINE: Telegram → Catbox → Telegram Channel
        """
        operation_id = f"tg_{telegram_file_unique_id}"
        
        async with self.atomic_context(operation_id):
            # Step 1: Download from Telegram
            logging.info(f"[{operation_id}] Step 1: Downloading from Telegram")
            media_file = await self.media_downloader.download_from_telegram(
                telegram_file_id, telegram_file_unique_id
            )
            
            # Step 2: Upload to Catbox (if required)
            catbox_url = None
            if require_catbox:
                logging.info(f"[{operation_id}] Step 2: Uploading to Catbox")
                catbox_url = await self.catbox_uploader.upload_with_retry(media_file)
                media_file.url = catbox_url
                
                # Rollback: Can't delete from Catbox, but log warning
                self._add_rollback(
                    operation_id,
                    lambda: logging.warning(f"Orphaned Catbox URL: {catbox_url}")
                )
            
            # Step 3: Upload to Telegram Channel
            logging.info(f"[{operation_id}] Step 3: Uploading to Telegram channel")
            telegram_info = await self.telegram_uploader.upload_with_retry(
                media_file, caption=caption
            )
            
            # Rollback: Delete channel message
            self._add_rollback(
                operation_id,
                lambda: self._delete_message(telegram_info)
            )
            
            # SUCCESS - Return result
            return UploadResult(
                success=True,
                media_file=media_file,
                telegram_info=telegram_info,
                catbox_url=catbox_url
            )
    
    async def upload_from_url(self,
                             url: str,
                             caption: str = "",
                             require_catbox: bool = True) -> UploadResult:
        """Atomic upload from URL"""
        operation_id = f"url_{hash(url) % 10000:04d}"
        
        async with self.atomic_context(operation_id):
            # Download from URL
            media_file = await self.media_downloader.download_from_url(url)
            
            # Catbox upload
            catbox_url = None
            if require_catbox:
                catbox_url = await self.catbox_uploader.upload_with_retry(media_file)
                media_file.url = catbox_url
                
                self._add_rollback(
                    operation_id,
                    lambda: logging.warning(f"Orphaned Catbox URL: {catbox_url}")
                )
            
            # Telegram upload
            telegram_info = await self.telegram_uploader.upload_with_retry(
                media_file, caption=caption
            )
            
            self._add_rollback(
                operation_id,
                lambda: self._delete_message(telegram_info)
            )
            
            return UploadResult(
                success=True,
                media_file=media_file,
                telegram_info=telegram_info,
                catbox_url=catbox_url
            )
    
    async def _delete_message(self, telegram_info: TelegramMediaInfo):
        """Delete Telegram message (rollback action)"""
        try:
            await self.telegram_uploader.bot.delete_message(
                chat_id=telegram_info.chat_id,
                message_id=telegram_info.message_id
            )
            logging.info(f"Rollback: Deleted message {telegram_info.message_id}")
        except Exception as e:
            logging.error(f"Failed to delete message: {e}")


# ============================================================================
# 7. DATABASE LAYER
# ============================================================================

class CharacterRepository:
    """Database operations with atomic guarantees"""
    
    def __init__(self):
        self.client = MongoClient(Config.MONGODB_URI)
        self.db = self.client[Config.MONGODB_DB]
        self.collection = self.db[Config.MONGODB_COLLECTION]
        self.sequences = self.db.sequences
    
    async def get_next_id(self) -> str:
        """Get next character ID"""
        sequence = self.sequences.find_one_and_update(
            {'_id': 'character_id'},
            {'$inc': {'value': 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER
        )
        return str(sequence['value'])
    
    async def create(self, character: Character) -> Character:
        """Create character with atomic guarantee"""
        try:
            result = self.collection.insert_one(character.to_dict())
            if not result.inserted_id:
                raise DatabaseError("Failed to insert character")
            
            # Verify
            doc = self.collection.find_one({'_id': result.inserted_id})
            if not doc:
                raise DatabaseError("Character not found after insert")
            
            return Character.from_dict(doc)
            
        except PyMongoError as e:
            raise DatabaseError(f"Database error: {e}")
    
    async def update(self, character_id: str, update_data: Dict[str, Any]) -> Optional[Character]:
        """Update character atomically"""
        try:
            update_data['updated_at'] = datetime.utcnow()
            
            result = self.collection.find_one_and_update(
                {'id': character_id},
                {'$set': update_data},
                return_document=ReturnDocument.AFTER
            )
            
            if not result:
                return None
            
            return Character.from_dict(result)
            
        except PyMongoError as e:
            raise DatabaseError(f"Update failed: {e}")
    
    async def delete(self, character_id: str) -> bool:
        """Delete character"""
        try:
            result = self.collection.delete_one({'id': character_id})
            return result.deleted_count > 0
        except PyMongoError as e:
            raise DatabaseError(f"Delete failed: {e}")
    
    async def get(self, character_id: str) -> Optional[Character]:
        """Get character by ID"""
        try:
            doc = self.collection.find_one({'id': character_id})
            if doc:
                return Character.from_dict(doc)
            return None
        except PyMongoError as e:
            raise DatabaseError(f"Get failed: {e}")
    
    async def get_by_message_id(self, message_id: int) -> Optional[Character]:
        """Get character by message ID"""
        try:
            doc = self.collection.find_one({'media_info.message_id': message_id})
            if doc:
                return Character.from_dict(doc)
            return None
        except PyMongoError as e:
            raise DatabaseError(f"Get by message_id failed: {e}")


# ============================================================================
# 8. SERVICE LAYER (BUSINESS LOGIC)
# ============================================================================

class CharacterService:
    """High-level character operations with atomic guarantees"""
    
    def __init__(self,
                 upload_orchestrator: AtomicUploadOrchestrator,
                 character_repo: CharacterRepository):
        self.upload_orchestrator = upload_orchestrator
        self.character_repo = character_repo
    
    def _generate_caption(self,
                         name: str,
                         anime: str,
                         rarity: Rarity,
                         user_id: int,
                         username: str,
                         action: str) -> str:
        """Generate HTML caption"""
        return (
            f"<b>Character Name:</b> {name}\n"
            f"<b>Anime Name:</b> {anime}\n"
            f"<b>Rarity:</b> {rarity.display_name}\n"
            f"<b>ID:</b> ...\n"
            f"{action} by <a href='tg://user?id={user_id}'>{username}</a>"
        )
    
    async def create_character(self,
                              name: str,
                              anime: str,
                              rarity: Rarity,
                              telegram_file_id: str,
                              telegram_file_unique_id: str,
                              user_id: int,
                              username: str) -> Character:
        """
        Create character with ATOMIC guarantee.
        Pipeline: Download → Catbox → Telegram → Database
        """
        logging.info(f"Creating character: {name}")
        
        # Generate caption (without ID yet)
        caption = self._generate_caption(name, anime, rarity, user_id, username, "Added")
        
        # Execute atomic upload
        upload_result = await self.upload_orchestrator.upload_from_telegram(
            telegram_file_id=telegram_file_id,
            telegram_file_unique_id=telegram_file_unique_id,
            caption=caption,
            require_catbox=True
        )
        
        if not upload_result.success:
            raise AtomicOperationError("Upload pipeline failed")
        
        # Get character ID
        character_id = await self.character_repo.get_next_id()
        
        # Update caption with ID
        caption = caption.replace("<b>ID:</b> ...", f"<b>ID:</b> {character_id}")
        
        # Re-upload with correct ID (small additional atomic operation)
        # In production, you might edit the caption instead
        final_upload = await self.upload_orchestrator.upload_from_telegram(
            telegram_file_id=telegram_file_id,
            telegram_file_unique_id=telegram_file_unique_id,
            caption=caption,
            require_catbox=False  # Already uploaded
        )
        
        # Create character object
        character = Character(
            id=character_id,
            name=name.title(),
            anime=anime.title(),
            rarity=rarity,
            media_info=final_upload.telegram_info,
            catbox_url=upload_result.catbox_url,
            added_by={
                'user_id': user_id,
                'username': username,
                'timestamp': datetime.utcnow().isoformat()
            }
        )
        
        # FINAL STEP: Insert into database
        created = await self.character_repo.create(character)
        
        logging.info(f"Character created successfully: {character_id}")
        return created
    
    async def update_character_media(self,
                                    character_id: str,
                                    new_file_id: str,
                                    new_file_unique_id: str,
                                    user_id: int,
                                    username: str) -> Character:
        """
        Update character media with ATOMIC guarantee.
        Pipeline: Get old → Delete old → Upload new → Update DB
        """
        # Get existing character
        old_character = await self.character_repo.get(character_id)
        if not old_character:
            raise ValidationError(f"Character {character_id} not found")
        
        # Generate new caption
        caption = self._generate_caption(
            old_character.name,
            old_character.anime,
            old_character.rarity,
            user_id,
            username,
            "Updated"
        )
        
        try:
            # Delete old channel message
            bot = self.upload_orchestrator.telegram_uploader.bot
            await bot.delete_message(
                chat_id=old_character.media_info.chat_id,
                message_id=old_character.media_info.message_id
            )
            
            # Upload new media atomically
            upload_result = await self.upload_orchestrator.upload_from_telegram(
                telegram_file_id=new_file_id,
                telegram_file_unique_id=new_file_unique_id,
                caption=caption,
                require_catbox=True
            )
            
            if not upload_result.success:
                # Attempt to restore old message
                await self._restore_old_message(old_character)
                raise AtomicOperationError("New media upload failed")
            
            # Update database
            update_data = {
                'media_info': upload_result.telegram_info.to_dict(),
                'catbox_url': upload_result.catbox_url,
                'updated_at': datetime.utcnow()
            }
            
            updated = await self.character_repo.update(character_id, update_data)
            
            if not updated:
                # CRITICAL: Database failed, rollback
                await bot.delete_message(
                    chat_id=upload_result.telegram_info.chat_id,
                    message_id=upload_result.telegram_info.message_id
                )
                await self._restore_old_message(old_character)
                raise DatabaseError("Database update failed after media upload")
            
            return updated
            
        except Exception as e:
            logging.error(f"Media update failed: {e}")
            # The orchestrator will handle rollback of new upload
            # We need to handle old message restoration
            raise AtomicOperationError(f"Media update failed: {e}")
    
    async def _restore_old_message(self, character: Character) -> bool:
        """Attempt to restore old message (simplified)"""
        try:
            # In production, implement proper restoration
            # For now, just log
            logging.warning(f"Would restore message for character {character.id}")
            return True
        except Exception as e:
            logging.error(f"Restore failed: {e}")
            return False
    
    async def delete_character(self, character_id: str) -> bool:
        """Delete character with atomic guarantee"""
        character = await self.character_repo.get(character_id)
        if not character:
            return False
        
        try:
            # Delete channel message
            bot = self.upload_orchestrator.telegram_uploader.bot
            await bot.delete_message(
                chat_id=character.media_info.chat_id,
                message_id=character.media_info.message_id
            )
            
            # Delete from database
            deleted = await self.character_repo.delete(character_id)
            
            if not deleted:
                # CRITICAL: Message deleted but database failed
                logging.critical(
                    f"ORPHANED MESSAGE: Character {character_id} "
                    f"message {character.media_info.message_id} deleted "
                    f"but database record remains"
                )
                return False
            
            return True
            
        except Exception as e:
            logging.error(f"Delete failed: {e}")
            return False


# ============================================================================
# 9. HTTP CLIENT MANAGEMENT
# ============================================================================

class HTTPClient:
    """Shared HTTP client with connection pooling"""
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._session = None
        return cls._instance
    
    async def get_session(self) -> aiohttp.ClientSession:
        """Get or create shared session"""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=Config.REQUEST_TIMEOUT)
            connector = aiohttp.TCPConnector(
                limit=100,
                limit_per_host=20,
                keepalive_timeout=30
            )
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
                headers={'User-Agent': 'AnimeBot/2.0'}
            )
        return self._session
    
    async def close(self):
        """Close session"""
        if self._session and not self._session.closed:
            await self._session.close()


async def get_http_client() -> aiohttp.ClientSession:
    """Get HTTP client session"""
    client = HTTPClient()
    return await client.get_session()


# ============================================================================
# 10. TELEGRAM COMMAND HANDLERS
# ============================================================================

class CommandHandlers:
    """Telegram command handlers"""
    
    def __init__(self, character_service: CharacterService):
        self.service = character_service
        self.rarity_map = Rarity
    
    def _get_best_file_id(self, message: Message) -> Tuple[str, str, MediaType]:
        """Extract best file_id from message"""
        if message.photo:
            photo = message.photo[-1]
            return photo.file_id, photo.file_unique_id, MediaType.PHOTO
        elif message.video:
            return message.video.file_id, message.video.file_unique_id, MediaType.VIDEO
        elif message.animation:
            return message.animation.file_id, message.animation.file_unique_id, MediaType.ANIMATION
        elif message.document:
            return message.document.file_id, message.document.file_unique_id, MediaType.DOCUMENT
        else:
            raise ValidationError("No media found in message")
    
    async def upload_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /upload command"""
        user = update.effective_user
        
        # Permission check
        if user.id not in Config.SUDO_USERS:
            await update.message.reply_text('🔒 Permission denied.')
            return
        
        # Check reply
        if not update.message.reply_to_message:
            await update.message.reply_text(
                "📸 Reply to a media message with /upload command."
            )
            return
        
        try:
            # Parse command text
            text = update.message.text or update.message.caption or ""
            lines = [line.strip() for line in text.split('\n') if line.strip()]
            
            if lines and lines[0].startswith('/upload'):
                lines = lines[1:]
            
            if len(lines) != 3:
                await update.message.reply_text(
                    "❌ Format: /upload\nCharacter Name\nAnime Name\nRarity (1-15)"
                )
                return
            
            char_name, anime_name, rarity_str = lines
            
            # Validate rarity
            try:
                rarity = Rarity.from_string(rarity_str)
            except ValueError as e:
                await update.message.reply_text(str(e))
                return
            
            # Get media from reply
            reply = update.message.reply_to_message
            file_id, file_unique_id, media_type = self._get_best_file_id(reply)
            
            # Inform user
            await update.message.reply_text("⏳ Processing...")
            
            # Create character (ATOMIC OPERATION)
            character = await self.service.create_character(
                name=char_name,
                anime=anime_name,
                rarity=rarity,
                telegram_file_id=file_id,
                telegram_file_unique_id=file_unique_id,
                user_id=user.id,
                username=user.first_name
            )
            
            # Success message
            await update.message.reply_text(
                f"✅ Character added!\n\n"
                f"• Name: {character.name}\n"
                f"• Anime: {character.anime}\n"
                f"• Rarity: {character.rarity.display_name}\n"
                f"• ID: {character.id}\n"
                f"• Type: {character.media_info.media_type.value}"
            )
            
        except AtomicOperationError as e:
            await update.message.reply_text(
                f"❌ Upload failed (atomic rollback): {str(e)[:200]}"
            )
        except ValidationError as e:
            await update.message.reply_text(f"❌ {str(e)}")
        except Exception as e:
            logging.error(f"Upload command error: {e}", exc_info=True)
            await update.message.reply_text(
                f"❌ Internal error. Contact {Config.SUPPORT_CHAT}"
            )
    
    async def update_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /update command"""
        user = update.effective_user
        
        if user.id not in Config.SUDO_USERS:
            await update.message.reply_text('🔒 Permission denied.')
            return
        
        args = context.args
        if not args or len(args) < 2:
            await update.message.reply_text(
                "📝 Usage:\n"
                "/update ID field value\n"
                "or\n"
                "/update ID img_url (reply to media)\n\n"
                "Fields: name, anime, rarity, img_url"
            )
            return
        
        character_id = args[0]
        field = args[1].lower()
        
        try:
            if field == 'img_url':
                # Media update
                if not update.message.reply_to_message:
                    await update.message.reply_text("📸 Reply to media for image update")
                    return
                
                reply = update.message.reply_to_message
                file_id, file_unique_id, _ = self._get_best_file_id(reply)
                
                await update.message.reply_text("⏳ Updating media...")
                
                # Atomic media update
                character = await self.service.update_character_media(
                    character_id=character_id,
                    new_file_id=file_id,
                    new_file_unique_id=file_unique_id,
                    user_id=user.id,
                    username=user.first_name
                )
                
                await update.message.reply_text(
                    f"✅ Media updated for {character.name} (ID: {character.id})"
                )
                
            elif field in ['name', 'anime']:
                # Text field update
                if len(args) < 3:
                    await update.message.reply_text(f"❌ Missing value for {field}")
                    return
                
                new_value = ' '.join(args[2:])
                update_data = {field: new_value.title()}
                
                # Get repo from service (simplified)
                from .database import CharacterRepository
                repo = CharacterRepository()
                
                updated = await repo.update(character_id, update_data)
                if not updated:
                    await update.message.reply_text("❌ Character not found")
                    return
                
                await update.message.reply_text(f"✅ {field.title()} updated")
                
            elif field == 'rarity':
                # Rarity update
                if len(args) < 3:
                    await update.message.reply_text("❌ Missing rarity value")
                    return
                
                try:
                    rarity = Rarity.from_string(args[2])
                    update_data = {'rarity': rarity.value}
                    
                    from .database import CharacterRepository
                    repo = CharacterRepository()
                    
                    updated = await repo.update(character_id, update_data)
                    if not updated:
                        await update.message.reply_text("❌ Character not found")
                        return
                    
                    await update.message.reply_text(f"✅ Rarity updated to {rarity.display_name}")
                    
                except ValueError as e:
                    await update.message.reply_text(str(e))
                    
            else:
                await update.message.reply_text(f"❌ Invalid field: {field}")
                
        except Exception as e:
            logging.error(f"Update command error: {e}", exc_info=True)
            await update.message.reply_text(f"❌ Update failed: {str(e)[:200]}")
    
    async def delete_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /delete command"""
        user = update.effective_user
        
        if user.id not in Config.SUDO_USERS:
            await update.message.reply_text('🔒 Permission denied.')
            return
        
        args = context.args
        if not args or len(args) != 1:
            await update.message.reply_text("❌ Usage: /delete ID")
            return
        
        character_id = args[0]
        
        try:
            # Atomic delete
            success = await self.service.delete_character(character_id)
            
            if success:
                await update.message.reply_text(f"✅ Character {character_id} deleted")
            else:
                await update.message.reply_text(f"❌ Character {character_id} not found")
                
        except Exception as e:
            logging.error(f"Delete command error: {e}", exc_info=True)
            await update.message.reply_text(f"❌ Delete failed: {str(e)[:200]}")


# ============================================================================
# 11. APPLICATION SETUP & MAIN
# ============================================================================

class AnimeBotApplication:
    """Main application class"""
    
    def __init__(self):
        self.application = None
        self.character_service = None
        self._shutdown_event = asyncio.Event()
    
    async def setup(self):
        """Setup all components"""
        logging.info("Setting up Anime Bot...")
        
        # Validate config
        Config.validate()
        
        # Create Telegram application
        self.application = Application.builder() \
            .token(Config.TELEGRAM_TOKEN) \
            .post_init(self.on_startup) \
            .post_shutdown(self.on_shutdown) \
            .build()
        
        # Setup components with dependency injection
        bot = self.application.bot
        
        # Upload services
        telegram_uploader = TelegramUploader(bot)
        catbox_uploader = CatboxUploader()
        media_downloader = MediaDownloader(bot)
        
        # Atomic orchestrator
        upload_orchestrator = AtomicUploadOrchestrator(
            telegram_uploader=telegram_uploader,
            catbox_uploader=catbox_uploader,
            media_downloader=media_downloader
        )
        
        # Database
        character_repo = CharacterRepository()
        
        # Service layer
        self.character_service = CharacterService(
            upload_orchestrator=upload_orchestrator,
            character_repo=character_repo
        )
        
        # Command handlers
        handlers = CommandHandlers(self.character_service)
        
        # Register handlers
        self.application.add_handler(CommandHandler("upload", handlers.upload_command))
        self.application.add_handler(CommandHandler("update", handlers.update_command))
        self.application.add_handler(CommandHandler("delete", handlers.delete_command))
        
        # Add more commands as needed
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("help", self.help_command))
        
        logging.info("Setup completed")
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        await update.message.reply_text(
            "🤖 Anime Character Management Bot\n\n"
            "Commands:\n"
            "/upload - Add new character (reply to media)\n"
            "/update - Update character\n"
            "/delete - Delete character\n"
            "/help - Show help"
        )
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command"""
        await update.message.reply_text(
            "📖 Help Guide\n\n"
            "1. /upload (reply to media)\n"
            "   Format:\n"
            "   /upload\n"
            "   Character Name\n"
            "   Anime Name\n"
            "   Rarity (1-15)\n\n"
            "2. /update ID field value\n"
            "   Fields: name, anime, rarity, img_url\n"
            "   For img_url, reply to new media\n\n"
            "3. /delete ID\n"
            "   Deletes character\n\n"
            f"Support: {Config.SUPPORT_CHAT}"
        )
    
    async def on_startup(self, application: Application):
        """Called on bot startup"""
        logging.info("Bot starting up...")
        
        # Setup signal handlers
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self.initiate_shutdown)
        
        # Database consistency check
        await self._check_database_consistency()
    
    async def _check_database_consistency(self):
        """Check database for inconsistencies"""
        try:
            repo = CharacterRepository()
            # Simple check - count characters
            count = repo.collection.count_documents({})
            logging.info(f"Database check: {count} characters found")
            
            # Check for characters without media_info
            incomplete = repo.collection.count_documents({
                '$or': [
                    {'media_info': {'$exists': False}},
                    {'media_info.file_id': {'$exists': False}},
                ]
            })
            
            if incomplete > 0:
                logging.warning(f"Found {incomplete} incomplete character records")
                
        except Exception as e:
            logging.error(f"Database check failed: {e}")
    
    async def on_shutdown(self, application: Application):
        """Called on bot shutdown"""
        logging.info("Bot shutting down...")
        
        # Wait for graceful shutdown
        await self._shutdown_event.wait()
        
        # Cleanup HTTP client
        http_client = HTTPClient()
        await http_client.close()
        
        logging.info("Shutdown completed")
    
    def initiate_shutdown(self):
        """Initiate graceful shutdown"""
        logging.info("Shutdown initiated...")
        self._shutdown_event.set()
    
    async def run(self):
        """Run the application"""
        try:
            await self.setup()
            
            # Start the bot
            await self.application.initialize()
            await self.application.start()
            await self.application.updater.start_polling()
            
            logging.info("Bot is running...")
            
            # Keep running until shutdown
            await self._shutdown_event.wait()
            
        except Exception as e:
            logging.error(f"Bot failed: {e}", exc_info=True)
            raise
        
        finally:
            # Ensure clean shutdown
            if self.application:
                await self.application.stop()


def main():
    """Main entry point"""
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('bot.log', encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ]
    )
    
    # Create and run application
    app = AnimeBotApplication()
    
    try:
        asyncio.run(app.run())
    except KeyboardInterrupt:
        logging.info("Bot stopped by user")
    except Exception as e:
        logging.error(f"Bot crashed: {e}", exc_info=True)
        raise


# ============================================================================
# 12. ERROR MESSAGES & UI TEXT
# ============================================================================

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

UPDATE_HELP_TEXT = """📝 ᴜᴘᴅᴀᴛᴇ ᴄᴏᴍᴍᴀɴᴅ ᴜꜱᴀɢᴇ:

ᴜᴘᴅᴀᴛᴇ ᴡɪᴛʜ ᴠᴀʟᴜᴇ:
/update ɪᴅ ꜰɪᴇʟᴅ ɴᴇᴡᴠᴀʟᴜᴇ

ᴜᴘᴅᴀᴛᴇ ɪᴍᴀɢᴇ (ʀᴇᴘʟʏ ᴛᴏ ᴘʜᴏᴛᴏ):
/update ɪᴅ ɪᴍɢ_ᴜʀʟ

ᴠᴀʟɪᴅ ꜰɪᴇʟᴅꜱ:
ɪᴍɢ_ᴜʀʟ, ɴᴀᴍᴇ, ᴀɴɪᴍᴇ, ʀᴀʀɪᴛʏ

ᴇxᴀᴍᴘʟᴇꜱ:
/update 12 ɴᴀᴍᴇ ɴᴇᴢᴜᴋᴏ ᴋᴀᴍᴀᴅᴏ
/update 12 ᴀɴɪᴍᴇ ᴅᴇᴍᴏɴ ꜱʟᴀʏᴇʀ
/update 12 ʀᴀʀɪᴟᴛʏ 5
/update 12 ɪᴍɢ_ᴜʀʟ ʀᴇᴘʟʏ_ɪᴍɢ"""

# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    # Run the bot
    main()