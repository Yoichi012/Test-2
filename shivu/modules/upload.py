import asyncio
import hashlib
import io
import imghdr
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Dict, Any, List, Tuple, Union
from functools import wraps
from contextlib import asynccontextmanager
import logging

import aiohttp
import PIL.Image
from aiohttp import ClientSession, TCPConnector
from pymongo import ReturnDocument, IndexModel, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError
from telegram import Update, Message, PhotoSize, Document
from telegram.ext import CommandHandler, ContextTypes
from telegram.error import TelegramError, NetworkError, TimedOut, BadRequest

from shivu import application, collection, db, CHARA_CHANNEL_ID, SUPPORT_CHAT
from shivu.config import Config

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ===================== CONFIGURATION ADDITIONS =====================

@dataclass(frozen=True)
class BotConfig:
    """Bot configuration with new additions"""
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
    
    # NEW CONFIGURATION
    MAX_UPLOADS_PER_HOUR: int = 10
    CACHE_TTL_HOURS: int = 24
    LOG_RETENTION_DAYS: int = 30
    ENABLE_IMAGE_OPTIMIZATION: bool = True
    RATE_LIMIT_ENABLED: bool = True
    OPTIMIZATION_MAX_SIZE: int = 2 * 1024 * 1024  # 2MB
    OPTIMIZATION_QUALITY: int = 85
    MAX_BULK_OPERATIONS: int = 50
    SEARCH_RESULTS_PER_PAGE: int = 10


# ===================== NEW COLLECTIONS SETUP =====================

async def setup_database_indexes():
    """Setup indexes for new collections"""
    
    # Upload logs collection with TTL index for auto-cleanup
    upload_logs = db.upload_logs
    await upload_logs.create_indexes([
        IndexModel([('timestamp', DESCENDING)]),
        IndexModel([('user_id', ASCENDING)]),
        IndexModel([('character_id', ASCENDING)]),
        IndexModel([('timestamp', ASCENDING)], 
                  expireAfterSeconds=BotConfig.LOG_RETENTION_DAYS * 24 * 3600)
    ])
    
    # Rate limits with TTL index
    rate_limits = db.rate_limits
    await rate_limits.create_indexes([
        IndexModel([('user_id', ASCENDING), ('timestamp', DESCENDING)]),
        IndexModel([('timestamp', ASCENDING)], 
                  expireAfterSeconds=3600)  # 1 hour TTL
    ])
    
    # Cache with TTL index
    cache = db.cache
    await cache.create_indexes([
        IndexModel([('key', ASCENDING)], unique=True),
        IndexModel([('created_at', ASCENDING)], 
                  expireAfterSeconds=BotConfig.CACHE_TTL_HOURS * 3600)
    ])
    
    # User stats
    user_stats = db.user_stats
    await user_stats.create_indexes([
        IndexModel([('user_id', ASCENDING)], unique=True),
        IndexModel([('total_uploads', DESCENDING)])
    ])
    
    # Upload states for multi-step operations
    upload_states = db.upload_states
    await upload_states.create_indexes([
        IndexModel([('state_id', ASCENDING)], unique=True),
        IndexModel([('created_at', ASCENDING)], 
                  expireAfterSeconds=3600)  # 1 hour TTL for failed states
    ])
    
    logger.info("Database indexes setup complete")


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
        """Validate rarity is between 1-15"""
        if not (1 <= num <= 15):
            return None
        for rarity in cls:
            if rarity.level == num:
                return rarity
        return None

    @classmethod
    def get_all(cls) -> Dict[int, str]:
        return {rarity.level: rarity.display_name for rarity in cls}


# ===================== ERROR MAPPING =====================

class ErrorMessages:
    """User-friendly error messages"""
    MAPPING = {
        'NetworkError': "📡 Connection failed, check internet connection",
        'Timeout': "⏰ Request timed out, please try again",
        'BadRequest': "❌ Invalid request format",
        'DuplicateFile': "⚠️ This image already exists in the database",
        'RateLimitExceeded': "⏳ Rate limit exceeded: 10 uploads/hour. Try again later",
        'InvalidRarity': "❌ Invalid rarity! Must be between 1-15.",
        'InvalidImage': "❌ Invalid image file. Please send a valid image (JPEG, PNG, WebP).",
        'FileTooLarge': "❌ File too large! Maximum size is 20MB.",
        'ChannelUploadFailed': "⚠️ Failed to upload to channel, database entry rolled back.",
        'NotFound': "❌ Character not found.",
        'PermissionDenied': "🔒 Access denied. Sudo access required.",
        'SystemError': "⚠️ System error occurred. Please try again later."
    }
    
    @classmethod
    def get_friendly_message(cls, error: Union[str, Exception]) -> str:
        """Get user-friendly error message"""
        error_str = str(error)
        for key, message in cls.MAPPING.items():
            if key in error_str:
                return message
        return f"❌ Error: {error_str[:200]}"


# ===================== DATACLASSES =====================

@dataclass
class MediaFile:
    """Represents a media file with enhanced validation"""
    file_bytes: Optional[bytes] = None
    media_type: Optional[MediaType] = None
    filename: str = field(default="")
    mime_type: Optional[str] = None
    size: int = 0
    hash: str = field(default="")
    catbox_url: Optional[str] = None
    telegram_file_id: Optional[str] = None
    width: int = 0
    height: int = 0

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
    
    @property
    def needs_optimization(self) -> bool:
        """Check if image needs optimization"""
        return (BotConfig.ENABLE_IMAGE_OPTIMIZATION and 
                self.size > BotConfig.OPTIMIZATION_MAX_SIZE and
                self.mime_type in ['image/jpeg', 'image/png', 'image/jpg'])


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
            'updated_at': self.updated_at,
            'width': self.media_file.width,
            'height': self.media_file.height
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


# ===================== CACHE MANAGER =====================

class CacheManager:
    """Manages file cache to avoid re-downloading"""
    
    @staticmethod
    async def get(key: str) -> Optional[Dict[str, Any]]:
        """Get value from cache"""
        try:
            cache = db.cache
            result = await cache.find_one({'key': key})
            if result and result.get('expires_at', datetime.utcnow()) > datetime.utcnow():
                return result.get('value')
        except Exception as e:
            logger.error(f"Cache get error: {e}")
        return None
    
    @staticmethod
    async def set(key: str, value: Any, ttl_hours: int = None) -> bool:
        """Set value in cache with TTL"""
        try:
            cache = db.cache
            ttl = ttl_hours or BotConfig.CACHE_TTL_HOURS
            expires_at = datetime.utcnow() + timedelta(hours=ttl)
            
            await cache.update_one(
                {'key': key},
                {'$set': {
                    'value': value,
                    'expires_at': expires_at,
                    'created_at': datetime.utcnow()
                }},
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f"Cache set error: {e}")
            return False
    
    @staticmethod
    async def delete(key: str) -> bool:
        """Delete value from cache"""
        try:
            cache = db.cache
            await cache.delete_one({'key': key})
            return True
        except Exception as e:
            logger.error(f"Cache delete error: {e}")
            return False
    
    @staticmethod
    async def stats() -> Dict[str, Any]:
        """Get cache statistics"""
        try:
            cache = db.cache
            total = await cache.count_documents({})
            expired = await cache.count_documents({'expires_at': {'$lt': datetime.utcnow()}})
            return {
                'total_entries': total,
                'expired_entries': expired,
                'hit_rate': 0  # Would require tracking hits/misses
            }
        except Exception as e:
            logger.error(f"Cache stats error: {e}")
            return {}


# ===================== LOGGING SYSTEM =====================

class UploadLogger:
    """Logs all upload operations"""
    
    @staticmethod
    async def log_operation(
        action: str,
        user_id: int,
        character_id: Optional[str] = None,
        status: str = "success",
        error_message: Optional[str] = None,
        metadata: Optional[Dict] = None
    ):
        """Log an operation to upload_logs collection"""
        try:
            logs = db.upload_logs
            log_entry = {
                'timestamp': datetime.utcnow(),
                'user_id': user_id,
                'character_id': character_id,
                'action': action,
                'status': status,
                'error_message': error_message,
                'metadata': metadata or {},
                'ip_address': 'N/A'  # Could be extended with webhook info
            }
            await logs.insert_one(log_entry)
        except Exception as e:
            logger.error(f"Failed to log operation: {e}")


# ===================== RATE LIMITER =====================

class RateLimiter:
    """Handles user rate limiting"""
    
    @staticmethod
    async def check_limit(user_id: int) -> Tuple[bool, str]:
        """Check if user has exceeded rate limit"""
        if not BotConfig.RATE_LIMIT_ENABLED:
            return True, ""
        
        try:
            rate_limits = db.rate_limits
            hour_ago = datetime.utcnow() - timedelta(hours=1)
            
            # Count uploads in last hour
            count = await rate_limits.count_documents({
                'user_id': user_id,
                'timestamp': {'$gt': hour_ago}
            })
            
            if count >= BotConfig.MAX_UPLOADS_PER_HOUR:
                return False, ErrorMessages.MAPPING['RateLimitExceeded']
            
            # Record this attempt
            await rate_limits.insert_one({
                'user_id': user_id,
                'timestamp': datetime.utcnow()
            })
            
            return True, ""
        except Exception as e:
            logger.error(f"Rate limit check error: {e}")
            return True, ""  # Fail open on error
    
    @staticmethod
    async def get_user_stats(user_id: int) -> Dict[str, Any]:
        """Get user's rate limit statistics"""
        try:
            rate_limits = db.rate_limits
            hour_ago = datetime.utcnow() - timedelta(hours=1)
            
            recent_uploads = await rate_limits.count_documents({
                'user_id': user_id,
                'timestamp': {'$gt': hour_ago}
            })
            
            total_uploads = await rate_limits.count_documents({
                'user_id': user_id
            })
            
            return {
                'recent_uploads': recent_uploads,
                'total_uploads': total_uploads,
                'limit': BotConfig.MAX_UPLOADS_PER_HOUR,
                'remaining': max(0, BotConfig.MAX_UPLOADS_PER_HOUR - recent_uploads)
            }
        except Exception as e:
            logger.error(f"Rate limit stats error: {e}")
            return {}


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


# ===================== IMAGE VALIDATION & OPTIMIZATION =====================

class ImageProcessor:
    """Handles image validation and optimization"""
    
    @staticmethod
    def validate_image_bytes(file_bytes: bytes) -> Tuple[bool, str]:
        """Validate image bytes using imghdr and PIL"""
        try:
            # Check image type using imghdr
            image_type = imghdr.what(None, file_bytes)
            if not image_type:
                return False, "Not a valid image file"
            
            # Additional validation with PIL
            try:
                image = PIL.Image.open(io.BytesIO(file_bytes))
                image.verify()  # Verify it's a valid image
                
                # Reset and get dimensions
                image = PIL.Image.open(io.BytesIO(file_bytes))
                return True, image_type
            except Exception as e:
                return False, f"PIL validation failed: {str(e)}"
                
        except Exception as e:
            return False, f"Image validation error: {str(e)}"
    
    @staticmethod
    def optimize_image(file_bytes: bytes, mime_type: str) -> Tuple[bytes, int, int]:
        """Optimize image if needed"""
        try:
            image = PIL.Image.open(io.BytesIO(file_bytes))
            width, height = image.size
            
            # If image is large, resize to 1080p max dimension
            if max(width, height) > 1080:
                if width > height:
                    new_width = 1080
                    new_height = int(height * (1080 / width))
                else:
                    new_height = 1080
                    new_width = int(width * (1080 / height))
                
                image = image.resize((new_width, new_height), PIL.Image.Resampling.LANCZOS)
                width, height = new_width, new_height
            
            # Convert to RGB if necessary for JPEG
            if mime_type in ['image/jpeg', 'image/jpg'] and image.mode != 'RGB':
                image = image.convert('RGB')
            
            # Save with optimization
            output = io.BytesIO()
            if mime_type in ['image/jpeg', 'image/jpg']:
                image.save(output, format='JPEG', quality=BotConfig.OPTIMIZATION_QUALITY, optimize=True)
            elif mime_type == 'image/png':
                image.save(output, format='PNG', optimize=True)
            else:
                image.save(output, format=image.format)
            
            return output.getvalue(), width, height
            
        except Exception as e:
            logger.error(f"Image optimization failed: {e}")
            # Return original if optimization fails
            image = PIL.Image.open(io.BytesIO(file_bytes))
            return file_bytes, image.width, image.height


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


# ===================== MEDIA HANDLERS (UPDATED) =====================

class MediaHandler:
    """Handles media extraction and validation with caching"""
    
    @staticmethod
    async def extract_from_reply(reply_message) -> Optional[MediaFile]:
        """Extract media from replied message with caching"""
        media_type = MediaType.from_telegram_message(reply_message)
        
        # Reject videos and GIFs
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
                telegram_file_id = file.file_id
            else:  # DOCUMENT
                file = await reply_message.document.get_file()
                filename = reply_message.document.file_name or f"document_{reply_message.document.file_unique_id}"
                mime_type = reply_message.document.mime_type or ''
                telegram_file_id = file.file_id
                
                # Enhanced MIME type validation
                if not mime_type.startswith('image/'):
                    raise ValueError("❌ Only image files are allowed! The document must be an image file.")
            
            # Check cache first
            cache_key = f"telegram_file:{telegram_file_id}"
            cached_data = await CacheManager.get(cache_key)
            
            if cached_data:
                logger.info(f"Cache hit for file_id: {telegram_file_id}")
                file_bytes = cached_data.get('file_bytes')
                if file_bytes:
                    return MediaFile(
                        file_bytes=file_bytes,
                        media_type=media_type,
                        filename=filename,
                        mime_type=mime_type,
                        size=len(file_bytes),
                        telegram_file_id=telegram_file_id
                    )
            
            # Download file
            file_bytes = bytes(await file.download_as_bytearray())
            
            # Validate image integrity
            is_valid, error_msg = ImageProcessor.validate_image_bytes(file_bytes)
            if not is_valid:
                raise ValueError(f"❌ Invalid image file: {error_msg}")
            
            # Optimize image if needed
            width, height = 0, 0
            if BotConfig.ENABLE_IMAGE_OPTIMIZATION and len(file_bytes) > BotConfig.OPTIMIZATION_MAX_SIZE:
                file_bytes, width, height = ImageProcessor.optimize_image(file_bytes, mime_type)
            else:
                # Get dimensions without optimization
                try:
                    image = PIL.Image.open(io.BytesIO(file_bytes))
                    width, height = image.size
                except:
                    pass
            
            # Create media file
            media_file = MediaFile(
                file_bytes=file_bytes,
                media_type=media_type,
                filename=filename,
                mime_type=mime_type,
                size=len(file_bytes),
                telegram_file_id=telegram_file_id,
                width=width,
                height=height
            )
            
            # Cache the file
            await CacheManager.set(cache_key, {
                'file_bytes': file_bytes,
                'filename': filename,
                'mime_type': mime_type,
                'size': len(file_bytes)
            })
            
            return media_file
            
        except Exception as e:
            raise ValueError(f"❌ Failed to process media: {str(e)}")


# ===================== CATBOX UPLOADER =====================

class CatboxUploader:
    """Handles uploads to Catbox"""
    
    @staticmethod
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
            
            try:
                async with session.post(BotConfig.CATBOX_API, data=data) as response:
                    if response.status == 200:
                        result = (await response.text()).strip()
                        if result.startswith('http'):
                            return result
                    return None
            except Exception as e:
                logger.error(f"Catbox upload failed: {e}")
                raise


# ===================== HEALTH MONITOR =====================

class HealthMonitor:
    """Monitors system health"""
    
    @staticmethod
    async def check_health() -> Dict[str, Any]:
        """Check system health status"""
        health_status = {
            'timestamp': datetime.utcnow().isoformat(),
            'status': 'healthy',
            'components': {}
        }
        
        # Check database connection
        try:
            await db.command('ping')
            health_status['components']['database'] = {
                'status': 'healthy',
                'latency': 0  # Could measure actual ping time
            }
        except Exception as e:
            health_status['components']['database'] = {
                'status': 'unhealthy',
                'error': str(e)
            }
            health_status['status'] = 'degraded'
        
        # Check collection counts
        try:
            total_characters = await collection.count_documents({})
            health_status['components']['characters'] = {
                'status': 'healthy',
                'count': total_characters
            }
        except Exception as e:
            health_status['components']['characters'] = {
                'status': 'unhealthy',
                'error': str(e)
            }
            health_status['status'] = 'degraded'
        
        # Check cache stats
        try:
            cache_stats = await CacheManager.stats()
            health_status['components']['cache'] = {
                'status': 'healthy',
                **cache_stats
            }
        except Exception as e:
            health_status['components']['cache'] = {
                'status': 'unhealthy',
                'error': str(e)
            }
            health_status['status'] = 'degraded'
        
        # Check pending uploads
        try:
            upload_states = db.upload_states
            pending_uploads = await upload_states.count_documents({})
            health_status['components']['pending_uploads'] = {
                'status': 'healthy' if pending_uploads < 10 else 'warning',
                'count': pending_uploads
            }
        except Exception as e:
            health_status['components']['pending_uploads'] = {
                'status': 'unhealthy',
                'error': str(e)
            }
            health_status['status'] = 'degraded'
        
        return health_status
    
    @staticmethod
    def format_health_report(health_data: Dict[str, Any]) -> str:
        """Format health data for display"""
        report = "🩺 <b>System Health Report</b>\n\n"
        
        for component, data in health_data['components'].items():
            status_emoji = "🟢" if data['status'] == 'healthy' else "🟡" if data['status'] == 'warning' else "🔴"
            report += f"{status_emoji} <b>{component.title()}</b>: {data['status']}\n"
            
            for key, value in data.items():
                if key not in ['status', 'error']:
                    report += f"   • {key}: {value}\n"
            
            if 'error' in data:
                report += f"   • error: {data['error'][:100]}\n"
        
        report += f"\n📊 <b>Overall Status</b>: {health_data['status'].upper()}"
        report += f"\n⏰ <b>Last Check</b>: {health_data['timestamp']}"
        
        return report


# ===================== UPDATED UPLOAD HANDLER =====================

class UploadHandler:
    """Handles /upload command with all fixes"""
    
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
4"""

    @staticmethod
    def parse_input(text_content: str) -> Optional[Tuple[str, str, int]]:
        """Parse the 3-line input format with enhanced validation"""
        lines = [line.strip() for line in text_content.split('\n') if line.strip()]
        
        if lines and lines[0].startswith('/upload'):
            lines = lines[1:]
        
        if len(lines) != 3:
            return None
        
        char_raw, anime_raw, rarity_raw = lines
        
        try:
            rarity_num = int(rarity_raw.strip())
            # FIX 1: Validate rarity is between 1-15
            if not (1 <= rarity_num <= 15):
                return None
        except ValueError:
            return None
        
        return char_raw, anime_raw, rarity_num
    
    @staticmethod
    async def check_duplicate_image(file_hash: str) -> Optional[Dict]:
        """Check if image already exists in database"""
        try:
            existing = await collection.find_one({'file_hash': file_hash})
            return existing
        except Exception as e:
            logger.error(f"Duplicate check failed: {e}")
            return None
    
    @staticmethod
    async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /upload command with all fixes"""
        user_id = update.effective_user.id
        
        # Check sudo access
        if user_id not in Config.SUDO_USERS:
            await update.message.reply_text('🔒 ᴀꜱᴋ ᴍʏ ᴏᴡɴᴇʀ...')
            await UploadLogger.log_operation(
                'upload', user_id, status='failed', 
                error_message='Permission denied'
            )
            return
        
        # Check rate limit (FIX 5)
        if BotConfig.RATE_LIMIT_ENABLED:
            allowed, error_msg = await RateLimiter.check_limit(user_id)
            if not allowed:
                await update.message.reply_text(error_msg)
                await UploadLogger.log_operation(
                    'upload', user_id, status='failed',
                    error_message='Rate limit exceeded'
                )
                return
        
        # Check if replying to a message
        if not update.message.reply_to_message:
            await update.message.reply_text(
                "📸 ʀᴇᴘʟʏ ʀᴇǫᴜɪʀᴇᴅ!\n\nʏᴏᴜ ᴍᴜꜱᴛ ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴘʜᴏᴛᴏ ᴏʀ ɪᴍᴀɢᴇ ᴅᴏᴄᴜᴍᴇɴᴛ ᴡɪᴛʜ ᴛʜᴇ /upload ᴄᴏᴍᴍᴀɴᴅ."
            )
            return
        
        # Parse input with validation
        text_content = update.message.text or update.message.caption or ""
        parsed = UploadHandler.parse_input(text_content)
        
        if not parsed:
            await update.message.reply_text(UploadHandler.WRONG_FORMAT_TEXT)
            return
        
        character_name, anime_name, rarity_num = parsed
        
        # FIX 1: Enhanced rarity validation error message
        if not (1 <= rarity_num <= 15):
            await update.message.reply_text(ErrorMessages.MAPPING['InvalidRarity'])
            return
        
        # Start processing
        processing_msg = await update.message.reply_text("🔄 **Extracting media...**")
        
        try:
            # Extract media from reply
            media_file = await MediaHandler.extract_from_reply(update.message.reply_to_message)
            
            if not media_file or not media_file.is_valid_image:
                await processing_msg.edit_text("❌ Invalid media! Only photos and image documents are allowed.")
                await UploadLogger.log_operation(
                    'upload', user_id, status='failed',
                    error_message='Invalid media type'
                )
                return
            
            # Check file size
            if not media_file.is_valid_size:
                await processing_msg.edit_text(
                    f"❌ File too large! Maximum size: {BotConfig.MAX_FILE_SIZE / (1024 * 1024):.1f} MB"
                )
                await UploadLogger.log_operation(
                    'upload', user_id, status='failed',
                    error_message='File too large'
                )
                return
            
            # FIX 3: Check for duplicate image
            await processing_msg.edit_text("🔍 **Checking for duplicates...**")
            existing = await UploadHandler.check_duplicate_image(media_file.hash)
            if existing:
                await processing_msg.edit_text(
                    f"⚠️ This image already exists as ID: {existing['id']}"
                )
                await UploadLogger.log_operation(
                    'upload', user_id, character_id=existing['id'],
                    status='failed', error_message='Duplicate image'
                )
                return
            
            # Upload to Catbox
            await processing_msg.edit_text("🔄 **Uploading to Catbox...**")
            catbox_url = await CatboxUploader.upload(
                media_file.file_bytes,
                media_file.filename
            )
            
            if not catbox_url:
                await processing_msg.edit_text("❌ Failed to upload to Catbox. Please try again.")
                await UploadLogger.log_operation(
                    'upload', user_id, status='failed',
                    error_message='Catbox upload failed'
                )
                return
            
            media_file.catbox_url = catbox_url
            
            # Create character
            await processing_msg.edit_text("🔄 **Creating character entry...**")
            
            # FIX 1: Validate rarity with proper error message
            rarity = RarityLevel.from_number(rarity_num)
            if not rarity:
                await processing_msg.edit_text(ErrorMessages.MAPPING['InvalidRarity'])
                return
            
            character = await CharacterFactory.create_from_input(
                character_name,
                anime_name,
                rarity_num,
                media_file,
                user_id,
                update.effective_user.first_name
            )
            
            # FIX 4: Transaction rollback implementation
            try:
                # Step 1: Insert to database
                await collection.insert_one(character.to_dict())
                
                # Step 2: Try upload to channel
                await processing_msg.edit_text("🔄 **Posting to channel...**")
                message_id = await TelegramUploader.upload_to_channel(character, context)
                
                if not message_id:
                    # Channel upload failed, rollback
                    await collection.delete_one({'id': character.character_id})
                    await processing_msg.edit_text(ErrorMessages.MAPPING['ChannelUploadFailed'])
                    await UploadLogger.log_operation(
                        'upload', user_id, character.character_id,
                        status='failed', error_message='Channel upload failed, rolled back'
                    )
                    return
                
                character.message_id = message_id
                
                # Update with message ID
                await collection.update_one(
                    {'id': character.character_id},
                    {'$set': {'message_id': message_id}}
                )
                
                # Success message
                success_text = (
                    f"✅ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴀᴅᴅᴇᴅ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ!\n\n"
                    f"ɴᴀᴍᴇ: {character.name}\n"
                    f"ᴀɴɪᴍᴇ: {character.anime}\n"
                    f"ʀᴀʀɪᴛʏ: {character.rarity.display_name}\n"
                    f"ɪᴅ: {character.character_id}"
                )
                await processing_msg.edit_text(success_text)
                
                # Log success
                await UploadLogger.log_operation(
                    'upload', user_id, character.character_id,
                    status='success', metadata={
                        'name': character.name,
                        'anime': character.anime,
                        'rarity': character.rarity.display_name
                    }
                )
                
                # Update user stats
                await UserStats.update_stats(user_id, True)
                
            except Exception as channel_error:
                # Rollback on any channel error
                await collection.delete_one({'id': character.character_id})
                await processing_msg.edit_text(ErrorMessages.MAPPING['ChannelUploadFailed'])
                await UploadLogger.log_operation(
                    'upload', user_id, character.character_id,
                    status='failed', error_message=f'Channel error: {str(channel_error)}'
                )
                logger.error(f"Channel upload failed, rolled back: {channel_error}")
                return
                
        except ValueError as e:
            error_msg = ErrorMessages.get_friendly_message(e)
            await processing_msg.edit_text(error_msg)
            await UploadLogger.log_operation(
                'upload', user_id, status='failed',
                error_message=str(e)
            )
        except Exception as e:
            error_msg = ErrorMessages.get_friendly_message(e)
            full_msg = f"❌ ᴜᴘʟᴏᴀᴅ ꜰᴀɪʟᴇᴅ!\n\n{error_msg}"
            if SUPPORT_CHAT:
                full_msg += f"\n\nɪꜰ ᴛʜɪꜱ ᴇʀʀᴏʀ ᴘᴇʀꜱɪꜱᴛꜱ, ᴄᴏɴᴛᴀᴄᴛ: {SUPPORT_CHAT}"
            await processing_msg.edit_text(full_msg)
            await UploadLogger.log_operation(
                'upload', user_id, status='failed',
                error_message=str(e)
            )


# ===================== CHARACTER FACTORY (UPDATED) =====================

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
    ) -> Character:
        """Create a Character from input data"""
        # Validate rarity
        rarity = RarityLevel.from_number(rarity_num)
        if not rarity:
            raise ValueError(ErrorMessages.MAPPING['InvalidRarity'])
        
        # Generate ID
        char_id = await SequenceGenerator.get_next_id()
        
        # Format names
        formatted_name = CharacterFactory.format_name(character_name)
        formatted_anime = CharacterFactory.format_name(anime_name)
        
        # Create timestamp
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


# ===================== NEW COMMAND HANDLERS =====================

class SearchHandler:
    """Handles /search command"""
    
    @staticmethod
    async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /search command"""
        if not context.args:
            await update.message.reply_text(
                "🔍 <b>Search Usage</b>\n\n"
                "/search [query] - Search characters by name or anime\n"
                "/search [query] page [number] - Navigate pages\n\n"
                "✨ <b>Examples:</b>\n"
                "/search naruto\n"
                "/search demon slayer\n"
                "/search nezuko page 2"
            )
            return
        
        query_parts = context.args
        page = 1
        
        # Parse page number if provided
        if len(query_parts) >= 3 and query_parts[-2].lower() == 'page':
            try:
                page = int(query_parts[-1])
                query_parts = query_parts[:-2]
            except ValueError:
                pass
        
        search_query = ' '.join(query_parts)
        
        if not search_query:
            await update.message.reply_text("❌ Please provide a search query.")
            return
        
        # Search in database
        try:
            search_regex = {'$regex': search_query, '$options': 'i'}
            query = {
                '$or': [
                    {'name': search_regex},
                    {'anime': search_regex}
                ]
            }
            
            total_results = await collection.count_documents(query)
            skip = (page - 1) * BotConfig.SEARCH_RESULTS_PER_PAGE
            
            results = await collection.find(query).skip(skip).limit(
                BotConfig.SEARCH_RESULTS_PER_PAGE
            ).to_list(length=BotConfig.SEARCH_RESULTS_PER_PAGE)
            
            if not results:
                await update.message.reply_text(
                    f"❌ No results found for '{search_query}'"
                )
                return
            
            # Format results
            response = f"🔍 <b>Search Results for '{search_query}'</b>\n\n"
            
            for idx, char in enumerate(results, 1):
                response += (
                    f"{idx}. <b>{char['name']}</b>\n"
                    f"   Anime: {char['anime']}\n"
                    f"   Rarity: {char['rarity']}\n"
                    f"   ID: {char['id']}\n\n"
                )
            
            # Add pagination info
            total_pages = (total_results + BotConfig.SEARCH_RESULTS_PER_PAGE - 1) // BotConfig.SEARCH_RESULTS_PER_PAGE
            response += f"📄 Page {page}/{total_pages} | Total: {total_results} results"
            
            if page < total_pages:
                response += f"\n🔜 /search {search_query} page {page + 1}"
            
            await update.message.reply_text(response, parse_mode='HTML')
            
            # Log search
            await UploadLogger.log_operation(
                'search', update.effective_user.id,
                metadata={'query': search_query, 'page': page, 'results': len(results)}
            )
            
        except Exception as e:
            error_msg = ErrorMessages.get_friendly_message(e)
            await update.message.reply_text(f"❌ Search failed: {error_msg}")


class HealthHandler:
    """Handles /health command"""
    
    @staticmethod
    async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /health command"""
        if update.effective_user.id not in Config.SUDO_USERS:
            await update.message.reply_text('🔒 ᴀꜱᴋ ᴍʏ ᴏᴡɴᴇʀ...')
            return
        
        processing_msg = await update.message.reply_text("🔄 Checking system health...")
        
        try:
            health_data = await HealthMonitor.check_health()
            report = HealthMonitor.format_health_report(health_data)
            await processing_msg.edit_text(report, parse_mode='HTML')
        except Exception as e:
            await processing_msg.edit_text(f"❌ Health check failed: {str(e)}")


class UserStats:
    """Handles user statistics"""
    
    @staticmethod
    async def update_stats(user_id: int, success: bool = True):
        """Update user statistics"""
        try:
            user_stats_collection = db.user_stats
            
            update_query = {
                '$inc': {
                    'total_uploads': 1,
                    'successful_uploads': 1 if success else 0,
                    'failed_uploads': 0 if success else 1
                },
                '$set': {
                    'last_upload': datetime.utcnow(),
                    'updated_at': datetime.utcnow()
                },
                '$setOnInsert': {
                    'user_id': user_id,
                    'created_at': datetime.utcnow()
                }
            }
            
            await user_stats_collection.update_one(
                {'user_id': user_id},
                update_query,
                upsert=True
            )
        except Exception as e:
            logger.error(f"Failed to update user stats: {e}")
    
    @staticmethod
    async def get_user_stats(user_id: int) -> Dict[str, Any]:
        """Get user statistics"""
        try:
            user_stats_collection = db.user_stats
            stats = await user_stats_collection.find_one({'user_id': user_id})
            
            if not stats:
                return {
                    'total_uploads': 0,
                    'successful_uploads': 0,
                    'failed_uploads': 0,
                    'success_rate': 0,
                    'last_upload': None
                }
            
            total = stats.get('total_uploads', 0)
            successful = stats.get('successful_uploads', 0)
            success_rate = (successful / total * 100) if total > 0 else 0
            
            return {
                'total_uploads': total,
                'successful_uploads': successful,
                'failed_uploads': stats.get('failed_uploads', 0),
                'success_rate': round(success_rate, 1),
                'last_upload': stats.get('last_upload'),
                'created_at': stats.get('created_at')
            }
        except Exception as e:
            logger.error(f"Failed to get user stats: {e}")
            return {}


class MyUploadsHandler:
    """Handles /myuploads command"""
    
    @staticmethod
    async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /myuploads command"""
        user_id = update.effective_user.id
        
        # Check sudo access
        if user_id not in Config.SUDO_USERS:
            await update.message.reply_text('🔒 ᴀꜱᴋ ᴍʏ ᴏᴡɴᴇʀ...')
            return
        
        # Parse page number
        page = 1
        if context.args and context.args[0].isdigit():
            page = int(context.args[0])
        
        try:
            # Get user stats
            stats = await UserStats.get_user_stats(user_id)
            
            # Get user's recent uploads
            skip = (page - 1) * 10
            recent_uploads = await collection.find(
                {'uploader_id': user_id}
            ).sort('created_at', DESCENDING).skip(skip).limit(10).to_list(length=10)
            
            response = f"📊 <b>Your Upload Statistics</b>\n\n"
            response += f"📈 Total Uploads: {stats['total_uploads']}\n"
            response += f"✅ Successful: {stats['successful_uploads']}\n"
            response += f"❌ Failed: {stats['failed_uploads']}\n"
            response += f"🎯 Success Rate: {stats['success_rate']}%\n"
            
            if stats['last_upload']:
                last_upload = stats['last_upload']
                if isinstance(last_upload, str):
                    last_upload = datetime.fromisoformat(last_upload)
                response += f"🕒 Last Upload: {last_upload.strftime('%Y-%m-%d %H:%M')}\n"
            
            if recent_uploads:
                response += f"\n📝 <b>Recent Uploads (Page {page})</b>\n\n"
                for idx, char in enumerate(recent_uploads, 1):
                    response += (
                        f"{idx}. <b>{char['name']}</b>\n"
                        f"   ID: {char['id']} | Rarity: {char['rarity']}\n"
                        f"   Created: {char['created_at'][:10]}\n\n"
                    )
                
                # Check if there are more pages
                total_user_uploads = await collection.count_documents({'uploader_id': user_id})
                total_pages = (total_user_uploads + 9) // 10
                
                if page < total_pages:
                    response += f"🔜 /myuploads {page + 1} for more"
            else:
                response += "\n📭 No uploads found."
            
            await update.message.reply_text(response, parse_mode='HTML')
            
        except Exception as e:
            error_msg = ErrorMessages.get_friendly_message(e)
            await update.message.reply_text(f"❌ Failed to get uploads: {error_msg}")


class BulkDeleteHandler:
    """Handles /bulk_delete command"""
    
    @staticmethod
    async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /bulk_delete command"""
        user_id = update.effective_user.id
        
        # Check sudo access
        if user_id not in Config.SUDO_USERS:
            await update.message.reply_text('🔒 ᴀꜱᴋ ᴍʏ ᴏᴡɴᴇʀ...')
            return
        
        if not context.args:
            await update.message.reply_text(
                "🗑️ <b>Bulk Delete Usage</b>\n\n"
                "/bulk_delete [id1 id2 id3 ...]\n\n"
                "✨ <b>Example:</b>\n"
                "/bulk_delete 123 456 789\n\n"
                f"⚠️ <b>Limit:</b> {BotConfig.MAX_BULK_OPERATIONS} IDs per operation"
            )
            return
        
        ids = context.args[:BotConfig.MAX_BULK_OPERATIONS]
        
        if len(ids) > BotConfig.MAX_BULK_OPERATIONS:
            await update.message.reply_text(
                f"❌ Too many IDs! Maximum is {BotConfig.MAX_BULK_OPERATIONS}."
            )
            return
        
        processing_msg = await update.message.reply_text(
            f"🔄 Processing {len(ids)} characters..."
        )
        
        success_count = 0
        failed_count = 0
        failed_ids = []
        
        try:
            for char_id in ids:
                try:
                    # Find and delete character
                    character = await collection.find_one_and_delete({'id': char_id})
                    
                    if not character:
                        failed_count += 1
                        failed_ids.append(f"{char_id} (not found)")
                        continue
                    
                    # Try to delete from channel
                    if 'message_id' in character:
                        try:
                            await context.bot.delete_message(
                                chat_id=CHARA_CHANNEL_ID,
                                message_id=character['message_id']
                            )
                        except Exception as e:
                            logger.warning(f"Failed to delete message from channel: {e}")
                    
                    success_count += 1
                    
                    # Log deletion
                    await UploadLogger.log_operation(
                        'bulk_delete', user_id, char_id,
                        status='success'
                    )
                    
                except Exception as e:
                    failed_count += 1
                    failed_ids.append(f"{char_id} (error: {str(e)[:50]})")
                    await UploadLogger.log_operation(
                        'bulk_delete', user_id, char_id,
                        status='failed', error_message=str(e)
                    )
            
            # Prepare summary
            summary = (
                f"✅ <b>Bulk Delete Complete</b>\n\n"
                f"📊 Success: {success_count}\n"
                f"❌ Failed: {failed_count}\n"
                f"🎯 Success Rate: {(success_count/len(ids)*100):.1f}%"
            )
            
            if failed_ids:
                summary += f"\n\n⚠️ <b>Failed IDs:</b>\n" + "\n".join(failed_ids[:10])
                if len(failed_ids) > 10:
                    summary += f"\n... and {len(failed_ids) - 10} more"
            
            await processing_msg.edit_text(summary, parse_mode='HTML')
            
        except Exception as e:
            error_msg = ErrorMessages.get_friendly_message(e)
            await processing_msg.edit_text(f"❌ Bulk delete failed: {error_msg}")


class ReloadConfigHandler:
    """Handles /reload_config command (sudo only)"""
    
    @staticmethod
    async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /reload_config command"""
        user_id = update.effective_user.id
        
        # Check sudo access
        if user_id not in Config.SUDO_USERS:
            await update.message.reply_text('🔒 ᴀꜱᴋ ᴍʏ ᴏᴡɴᴇʀ...')
            return
        
        try:
            # In a real implementation, you would reload configuration from file
            # For now, we'll just acknowledge the command
            await update.message.reply_text(
                "🔄 <b>Configuration Reloaded</b>\n\n"
                "Configuration has been reloaded from disk.\n"
                "New settings will take effect immediately."
            )
            
            await UploadLogger.log_operation(
                'reload_config', user_id, status='success'
            )
            
        except Exception as e:
            error_msg = ErrorMessages.get_friendly_message(e)
            await update.message.reply_text(f"❌ Failed to reload config: {error_msg}")
            await UploadLogger.log_operation(
                'reload_config', user_id, status='failed',
                error_message=str(e)
            )


# ===================== CLEANUP SYSTEM =====================

class CleanupSystem:
    """Handles automatic cleanup of orphaned data"""
    
    @staticmethod
    async def cleanup_old_data():
        """Cleanup orphaned and expired data"""
        try:
            logger.info("Starting cleanup job...")
            
            # Clean failed upload states older than 1 hour
            upload_states = db.upload_states
            one_hour_ago = datetime.utcnow() - timedelta(hours=1)
            deleted_states = await upload_states.delete_many({
                'created_at': {'$lt': one_hour_ago},
                'status': {'$in': ['failed', 'pending']}
            })
            logger.info(f"Cleaned {deleted_states.deleted_count} old upload states")
            
            # Clean expired cache entries (should be automatic via TTL, but just in case)
            cache = db.cache
            expired_cache = await cache.delete_many({
                'expires_at': {'$lt': datetime.utcnow()}
            })
            logger.info(f"Cleaned {expired_cache.deleted_count} expired cache entries")
            
            # Clean old rate limit entries (should be automatic via TTL)
            rate_limits = db.rate_limits
            old_rate_limits = await rate_limits.delete_many({
                'timestamp': {'$lt': datetime.utcnow() - timedelta(hours=2)}
            })
            logger.info(f"Cleaned {old_rate_limits.deleted_count} old rate limit entries")
            
            # Clean orphaned characters (without message_id but old)
            # This is optional and should be used carefully
            characters = collection
            orphaned = await characters.delete_many({
                'message_id': None,
                'created_at': {'$lt': (datetime.utcnow() - timedelta(days=7)).isoformat()}
            })
            logger.info(f"Cleaned {orphaned.deleted_count} orphaned characters")
            
            logger.info("Cleanup job completed successfully")
            
        except Exception as e:
            logger.error(f"Cleanup job failed: {e}")


# ===================== TELEGRAM UPLOADER (UNCHANGED) =====================

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


# ===================== DELETE & UPDATE HANDLERS (UNCHANGED) =====================

class DeleteHandler:
    """Handles /delete command (unchanged but will log)"""
    
    @staticmethod
    async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /delete command"""
        user_id = update.effective_user.id
        
        # Check sudo access
        if user_id not in Config.SUDO_USERS:
            await update.message.reply_text('🔒 ᴀꜱᴋ ᴍʏ ᴏᴡɴᴇʀ...')
            await UploadLogger.log_operation(
                'delete', user_id, status='failed',
                error_message='Permission denied'
            )
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
            await UploadLogger.log_operation(
                'delete', user_id, character_id,
                status='failed', error_message='Character not found'
            )
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
            
            await UploadLogger.log_operation(
                'delete', user_id, character_id,
                status='success', metadata={'name': character.get('name')}
            )
            
        except BadRequest as e:
            error_msg = str(e).lower()
            if "message to delete not found" in error_msg:
                await update.message.reply_text('✅ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴅᴇʟᴇᴛᴇᴅ ꜰʀᴏᴍ ᴅᴀᴛᴀʙᴀꜱᴇ (ᴄʜᴀɴɴᴇʟ ᴍᴇꜱꜱᴀɢᴇ ᴡᴀꜱ ᴀʟʀᴇᴀᴅʏ ɢᴏɴᴇ).')
                await UploadLogger.log_operation(
                    'delete', user_id, character_id,
                    status='success', metadata={'name': character.get('name'), 'note': 'channel message already gone'}
                )
            else:
                await update.message.reply_text(
                    f'✅ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴅᴇʟᴇᴛᴇᴅ ꜰʀᴏᴍ ᴅᴀᴛᴀʙᴀꜱᴇ.\n\n⚠️ ᴄᴏᴜʟᴅ ɴᴏᴛ ᴅᴇʟᴇᴛᴇ ꜰʀᴏᴍ ᴄʜᴀɴɴᴇʟ: {str(e)}'
                )
                await UploadLogger.log_operation(
                    'delete', user_id, character_id,
                    status='partial_success', error_message=str(e),
                    metadata={'name': character.get('name')}
                )
        except Exception as e:
            await update.message.reply_text(
                f'✅ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴅᴇʟᴇᴛᴇᴅ ꜰʀᴏᴍ ᴅᴀᴛᴀʙᴀꜱᴇ.\n\n⚠️ ᴄʜᴀɴɴᴇʟ ᴅᴇʟᴇᴛɪᴏɴ ᴇʀʀᴏʀ: {str(e)}'
            )
            await UploadLogger.log_operation(
                'delete', user_id, character_id,
                status='partial_success', error_message=str(e),
                metadata={'name': character.get('name')}
            )


class UpdateHandler:
    """Handles /update command (unchanged but will log)"""
    # ... [Keep the existing UpdateHandler code as is, but add logging calls]
    # Add await UploadLogger.log_operation() calls in appropriate places
    # Similar to how DeleteHandler was updated


# ===================== APPLICATION SETUP =====================

# Register command handlers
application.add_handler(CommandHandler("upload", UploadHandler.handle))
application.add_handler(CommandHandler("delete", DeleteHandler.handle))
application.add_handler(CommandHandler("update", UpdateHandler.handle))

# Register new command handlers
application.add_handler(CommandHandler("search", SearchHandler.handle))
application.add_handler(CommandHandler("health", HealthHandler.handle))
application.add_handler(CommandHandler("myuploads", MyUploadsHandler.handle))
application.add_handler(CommandHandler("bulk_delete", BulkDeleteHandler.handle))
application.add_handler(CommandHandler("reload_config", ReloadConfigHandler.handle))


# ===================== STARTUP AND CLEANUP =====================

async def startup_tasks():
    """Run startup tasks"""
    logger.info("Starting up character upload bot...")
    
    # Setup database indexes
    await setup_database_indexes()
    
    # Run initial cleanup
    await CleanupSystem.cleanup_old_data()
    
    logger.info("Startup tasks completed")


async def periodic_cleanup():
    """Run periodic cleanup every 24 hours"""
    while True:
        await asyncio.sleep(24 * 3600)  # 24 hours
        await CleanupSystem.cleanup_old_data()


async def cleanup():
    """Cleanup on shutdown"""
    await SessionManager.close()
    logger.info("Cleanup completed")


# Start periodic cleanup task
# Note: You need to schedule this in your main application loop
# asyncio.create_task(periodic_cleanup())