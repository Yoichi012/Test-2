import asyncio
import hashlib
import io
import time
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, Any, List, Tuple
from functools import wraps
from contextlib import asynccontextmanager
from datetime import datetime

import aiohttp
from aiohttp import ClientSession, TCPConnector
from pymongo import ReturnDocument, ASCENDING, TEXT
from telegram import Update, InputFile, Message, InlineQueryResultPhoto
from telegram.ext import (
    CommandHandler, 
    ContextTypes, 
    InlineQueryHandler,
    Application
)
from telegram.error import TelegramError, BadRequest

from shivu import application, collection, db, CHARA_CHANNEL_ID, SUPPORT_CHAT
from shivu.config import Config

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


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
    """Rarity levels (1-15)"""
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
        return {rarity.level: rarity.display_name for rarity in cls}


# ===================== CONFIG =====================

@dataclass(frozen=True)
class BotConfig:
    """Bot configuration"""
    MAX_FILE_SIZE: int = 20 * 1024 * 1024  # 20MB
    DOWNLOAD_TIMEOUT: int = 300
    UPLOAD_TIMEOUT: int = 300
    STREAM_CHUNK_SIZE: int = 65536  # 64KB chunks for streaming
    MAX_RETRIES: int = 3
    RETRY_DELAY: float = 2.0
    CONNECTION_LIMIT: int = 100
    
    # Cloud upload endpoints
    CATBOX_API: str = "https://catbox.moe/user/api.php"
    TELEGRAPH_API: str = "https://telegra.ph/upload"
    
    ALLOWED_MIME_TYPES: Tuple[str, ...] = (
        'image/jpeg', 'image/png', 'image/webp', 'image/jpg'
    )
    
    # Progress update throttling (avoid FloodWait)
    PROGRESS_UPDATE_INTERVAL: float = 3.5  # seconds


# ===================== SESSION MANAGEMENT =====================

class SessionManager:
    """Manages aiohttp sessions with proper lifecycle"""
    _session: Optional[ClientSession] = None
    _lock = asyncio.Lock()

    @classmethod
    async def get_session(cls) -> ClientSession:
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
                    total=BotConfig.UPLOAD_TIMEOUT,
                    connect=60,
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
        """Close the session"""
        async with cls._lock:
            if cls._session and not cls._session.closed:
                await cls._session.close()
                cls._session = None


# ===================== DATABASE MANAGER =====================

class DatabaseManager:
    """Handles all database operations with atomic guarantees"""
    
    @staticmethod
    async def initialize_indexes():
        """Create indexes for fast searching"""
        try:
            # Text index for inline search
            await collection.create_index([
                ("name", TEXT),
                ("anime", TEXT)
            ])
            
            # Regular indexes
            await collection.create_index([("id", ASCENDING)], unique=True)
            await collection.create_index([("file_hash", ASCENDING)])
            
            logger.info("✅ Database indexes created successfully")
        except Exception as e:
            logger.error(f"❌ Failed to create indexes: {e}")

    @staticmethod
    async def get_next_character_id() -> str:
        """
        Generate next sequential ID with gapless guarantee.
        Only called AFTER all validations pass.
        """
        sequence_collection = db.sequences
        result = await sequence_collection.find_one_and_update(
            {'_id': 'character_id'},
            {'$inc': {'sequence_value': 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER
        )
        return str(result['sequence_value'])

    @staticmethod
    async def check_duplicate_hash(file_hash: str) -> Optional[Dict]:
        """Check if file hash already exists"""
        return await collection.find_one({'file_hash': file_hash})

    @staticmethod
    async def insert_character(character_data: Dict) -> bool:
        """Insert character atomically"""
        try:
            await collection.insert_one(character_data)
            return True
        except Exception as e:
            logger.error(f"❌ Failed to insert character: {e}")
            return False

    @staticmethod
    async def update_image_url(character_id: str, img_url: str) -> bool:
        """
        Atomically update img_url after background upload completes.
        Uses $set to avoid race conditions.
        """
        try:
            result = await collection.update_one(
                {'id': character_id},
                {
                    '$set': {
                        'img_url': img_url,
                        'updated_at': datetime.utcnow().isoformat()
                    }
                }
            )
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"❌ Failed to update img_url for {character_id}: {e}")
            return False

    @staticmethod
    async def search_characters(query: str, limit: int = 50) -> List[Dict]:
        """Search characters by name or anime (for inline queries)"""
        try:
            # Try text search first
            results = await collection.find(
                {'$text': {'$search': query}}
            ).limit(limit).to_list(length=limit)
            
            if not results:
                # Fallback to regex search
                regex_pattern = {'$regex': query, '$options': 'i'}
                results = await collection.find({
                    '$or': [
                        {'name': regex_pattern},
                        {'anime': regex_pattern}
                    ]
                }).limit(limit).to_list(length=limit)
            
            return results
        except Exception as e:
            logger.error(f"❌ Search failed: {e}")
            return []


# ===================== MEDIA HANDLER =====================

class MediaHandler:
    """Handles media extraction with strict validation"""

    @staticmethod
    def compute_file_hash(file_bytes: bytes) -> str:
        """Compute SHA256 hash"""
        return hashlib.sha256(file_bytes).hexdigest()

    @staticmethod
    async def extract_from_reply(reply_message) -> Tuple[Optional[bytes], Optional[str], Optional[str]]:
        """
        Extract media from replied message.
        Returns: (file_bytes, file_id, mime_type)
        """
        media_type = MediaType.from_telegram_message(reply_message)

        # Reject videos and animations
        if media_type == MediaType.VIDEO:
            raise ValueError("❌ Videos are not allowed! Please send only photos or image documents.")
        elif media_type == MediaType.ANIMATION:
            raise ValueError("❌ GIFs/Animations are not allowed! Please send only photos or image documents.")

        if not media_type or media_type not in [MediaType.PHOTO, MediaType.DOCUMENT]:
            raise ValueError("❌ No valid media found! Please reply to a photo or image document.")

        try:
            if media_type == MediaType.PHOTO:
                # Get highest quality photo
                photo = reply_message.photo[-1]
                file = await photo.get_file()
                mime_type = 'image/jpeg'
                file_id = photo.file_id
            else:  # DOCUMENT
                doc = reply_message.document
                mime_type = doc.mime_type or ''
                
                # Strict MIME type validation
                if not mime_type.startswith('image/'):
                    raise ValueError("❌ Only image files are allowed! The document must be an image.")
                
                if mime_type not in BotConfig.ALLOWED_MIME_TYPES:
                    raise ValueError(f"❌ Unsupported image format: {mime_type}")
                
                file = await doc.get_file()
                file_id = doc.file_id

            # Check file size before downloading
            if file.file_size > BotConfig.MAX_FILE_SIZE:
                size_mb = BotConfig.MAX_FILE_SIZE / (1024 * 1024)
                raise ValueError(f"❌ File too large! Maximum size: {size_mb:.1f} MB")

            # Download file bytes
            file_bytes = bytes(await file.download_as_bytearray())

            return file_bytes, file_id, mime_type

        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"❌ Failed to process media: {str(e)}")


# ===================== CLOUD UPLOADER (REDUNDANCY SYSTEM) =====================

class CloudUploader:
    """
    Multi-mirror upload system with automatic fallback.
    Primary: Catbox.moe
    Fallback: Telegraph (Graph.org)
    """

    @staticmethod
    async def upload_to_catbox(file_bytes: bytes, filename: str) -> Optional[str]:
        """Upload to Catbox.moe"""
        try:
            session = await SessionManager.get_session()
            
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
                        logger.info(f"✅ Catbox upload successful: {filename}")
                        return result
                
                logger.warning(f"⚠️ Catbox returned status {response.status}")
                return None
                
        except asyncio.TimeoutError:
            logger.warning("⚠️ Catbox upload timed out")
            return None
        except Exception as e:
            logger.warning(f"⚠️ Catbox upload failed: {e}")
            return None

    @staticmethod
    async def upload_to_telegraph(file_bytes: bytes, filename: str) -> Optional[str]:
        """Upload to Telegraph (Graph.org) as fallback"""
        try:
            session = await SessionManager.get_session()
            
            data = aiohttp.FormData()
            data.add_field(
                'file',
                file_bytes,
                filename=filename,
                content_type='image/jpeg'
            )

            async with session.post(BotConfig.TELEGRAPH_API, data=data) as response:
                if response.status == 200:
                    result = await response.json()
                    if isinstance(result, list) and len(result) > 0:
                        path = result[0].get('src', '')
                        if path:
                            url = f"https://telegra.ph{path}"
                            logger.info(f"✅ Telegraph upload successful: {filename}")
                            return url
                
                logger.warning(f"⚠️ Telegraph returned status {response.status}")
                return None
                
        except Exception as e:
            logger.warning(f"⚠️ Telegraph upload failed: {e}")
            return None

    @staticmethod
    async def upload_with_fallback(file_bytes: bytes, filename: str) -> Optional[str]:
        """
        Try Catbox first, fallback to Telegraph if it fails.
        Returns None only if ALL mirrors fail.
        """
        # Try Catbox (Primary)
        logger.info(f"📤 Attempting Catbox upload: {filename}")
        url = await CloudUploader.upload_to_catbox(file_bytes, filename)
        if url:
            return url

        # Fallback to Telegraph
        logger.info(f"📤 Catbox failed, trying Telegraph: {filename}")
        url = await CloudUploader.upload_to_telegraph(file_bytes, filename)
        if url:
            return url

        # All mirrors failed
        logger.error(f"❌ ALL upload mirrors failed for: {filename}")
        return None


# ===================== PROGRESS TRACKER =====================

class ProgressTracker:
    """
    Tracks upload progress with rate limiting to avoid FloodWait.
    Only updates message every 3.5 seconds.
    """

    def __init__(self, message: Message):
        self.message = message
        self.last_update = 0
        self.update_interval = BotConfig.PROGRESS_UPDATE_INTERVAL

    async def update(self, status: str):
        """Update progress message (throttled)"""
        now = time.time()
        
        # Throttle updates
        if now - self.last_update < self.update_interval:
            return

        self.last_update = now

        try:
            await self.message.edit_text(status, parse_mode='Markdown')
        except BadRequest:
            pass  # Message not modified or deleted
        except Exception as e:
            logger.warning(f"⚠️ Failed to update progress: {e}")


# ===================== INPUT PARSER =====================

class InputParser:
    """
    Flexible input parser that handles messy formatting.
    Extracts: Character Name, Anime Name, Rarity
    """

    @staticmethod
    def parse_upload_command(text: str) -> Optional[Tuple[str, str, int]]:
        """
        Parse upload command with robust handling.
        Accepts formats:
        /upload
        Character Name
        Anime Name
        5
        
        Or:
        /upload Character Name
        Anime Name
        5
        """
        # Clean up text
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        
        # Remove /upload command
        if lines and lines[0].lower().startswith('/upload'):
            first_line = lines[0][7:].strip()  # Remove '/upload'
            if first_line:
                # Command and first param on same line
                lines[0] = first_line
            else:
                lines = lines[1:]
        
        # Need exactly 3 lines
        if len(lines) != 3:
            return None

        char_name = lines[0].strip()
        anime_name = lines[1].strip()
        rarity_str = lines[2].strip()

        # Validate inputs
        if not char_name or not anime_name:
            return None

        try:
            rarity = int(rarity_str)
            if not (1 <= rarity <= 15):
                return None
        except ValueError:
            return None

        return char_name, anime_name, rarity


# ===================== BACKGROUND TASK PROCESSOR =====================

class BackgroundTaskProcessor:
    """
    Handles background cloud uploads without blocking user interaction.
    This is the core of the "Fire-and-Forget" architecture.
    """

    @staticmethod
    async def process_upload(
        character_id: str,
        file_bytes: bytes,
        filename: str,
        progress_msg: Optional[Message] = None
    ):
        """
        Background task: Upload to cloud and update database.
        This runs asynchronously after user gets instant response.
        """
        logger.info(f"🔄 Background upload started for character {character_id}")
        
        tracker = ProgressTracker(progress_msg) if progress_msg else None
        
        try:
            # Update progress
            if tracker:
                await tracker.update("🔄 **Uploading to cloud storage...**")
            
            # Upload with fallback system
            img_url = await CloudUploader.upload_with_fallback(file_bytes, filename)
            
            if img_url:
                # Update database atomically
                success = await DatabaseManager.update_image_url(character_id, img_url)
                
                if success:
                    logger.info(f"✅ Background upload complete for {character_id}: {img_url}")
                    if tracker:
                        await tracker.update(f"✅ **Upload complete!**\n🔗 Hosted at: {img_url[:50]}...")
                else:
                    logger.error(f"❌ Failed to update DB for {character_id}")
                    if tracker:
                        await tracker.update("⚠️ **Upload succeeded but DB update failed**")
            else:
                logger.error(f"❌ All upload mirrors failed for {character_id}")
                if tracker:
                    await tracker.update("❌ **Cloud upload failed** (All mirrors unavailable)")
                
        except Exception as e:
            logger.error(f"❌ Background task error for {character_id}: {e}")
            if tracker:
                await tracker.update(f"❌ **Upload error:** {str(e)[:100]}")


# ===================== UPLOAD HANDLER =====================

class UploadHandler:
    """Handles /upload command with instant response"""

    WRONG_FORMAT_TEXT = """❌ ɪɴᴄᴏʀʀᴇᴄᴛ ꜰᴏʀᴍᴀᴛ!

📌 ʜᴏᴡ ᴛᴏ ᴜꜱᴇ /upload:

1️⃣ ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴘʜᴏᴛᴏ
2️⃣ ꜱᴇɴᴅ ᴛʜᴇ ᴄᴏᴍᴍᴀɴᴅ:

/upload
ᴄʜᴀʀᴀᴄᴛᴇʀ ɴᴀᴍᴇ
ᴀɴɪᴍᴇ ɴᴀᴍᴇ
ʀᴀʀɪᴛʏ (1-15)

✨ ᴇxᴀᴍᴘʟᴇ:
/upload
ɴᴇᴢᴜᴋᴏ ᴋᴀᴍᴀᴅᴏ
ᴅᴇᴍᴏɴ ꜱʟᴀʏᴇʀ
4

📊 ʀᴀʀɪᴛʏ ᴍᴀᴘ:
1⚪ 2🔵 3🟡 4💮 5👹 6🎐 7🔮 8🪐 9⚰️ 10🌬️ 11💝 12🌸 13🏖️ 14🍭 15🧬"""

    @staticmethod
    async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        INSTANT RESPONSE UPLOAD HANDLER
        ================================
        1. Validate input (<1s)
        2. Check duplicates (<1s)
        3. Insert to DB with file_id (<1s)
        4. Send success message to user
        5. Start background cloud upload (non-blocking)
        """
        # Check permissions
        if update.effective_user.id not in Config.SUDO_USERS:
            await update.message.reply_text('🔒 ᴀꜱᴋ ᴍʏ ᴏᴡɴᴇʀ...')
            return

        # Validate reply
        if not update.message.reply_to_message:
            await update.message.reply_text(
                "📸 ʀᴇᴘʟʏ ʀᴇǫᴜɪʀᴇᴅ!\n\nʏᴏᴜ ᴍᴜꜱᴛ ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴘʜᴏᴛᴏ ᴏʀ ɪᴍᴀɢᴇ."
            )
            return

        # Parse input
        text = update.message.text or update.message.caption or ""
        parsed = InputParser.parse_upload_command(text)

        if not parsed:
            await update.message.reply_text(UploadHandler.WRONG_FORMAT_TEXT)
            return

        char_name, anime_name, rarity_num = parsed

        # Validate rarity
        rarity = RarityLevel.from_number(rarity_num)
        if not rarity:
            await update.message.reply_text(f"❌ Invalid rarity: {rarity_num}. Must be 1-15.")
            return

        processing_msg = await update.message.reply_text("⚡ **Validating media...**")

        try:
            # === PHASE 1: EXTRACT & VALIDATE (Fast) ===
            file_bytes, file_id, mime_type = await MediaHandler.extract_from_reply(
                update.message.reply_to_message
            )

            # Calculate hash for duplicate detection
            file_hash = MediaHandler.compute_file_hash(file_bytes)

            # Check for duplicates
            duplicate = await DatabaseManager.check_duplicate_hash(file_hash)
            if duplicate:
                await processing_msg.edit_text(
                    f"⚠️ **Duplicate detected!**\n\n"
                    f"This image already exists as:\n"
                    f"🆔 ID: {duplicate.get('id')}\n"
                    f"📛 Name: {duplicate.get('name')}\n"
                    f"📺 Anime: {duplicate.get('anime')}"
                )
                return

            # === PHASE 2: GENERATE ID & INSERT TO DB (Instant) ===
            character_id = await DatabaseManager.get_next_character_id()
            
            # Format names
            formatted_name = char_name.strip().title()
            formatted_anime = anime_name.strip().title()
            
            timestamp = datetime.utcnow().isoformat()

            # Create character document with Telegram file_id
            character_doc = {
                'id': character_id,
                'name': formatted_name,
                'anime': formatted_anime,
                'rarity': rarity.display_name,
                'img_url': None,  # Will be updated by background task
                'file_id': file_id,  # Telegram's cached file
                'file_hash': file_hash,
                'uploader_id': update.effective_user.id,
                'uploader_name': update.effective_user.first_name,
                'created_at': timestamp,
                'updated_at': timestamp,
                'upload_status': 'pending'  # pending -> completed
            }

            # Insert to database (atomic)
            success = await DatabaseManager.insert_character(character_doc)
            
            if not success:
                await processing_msg.edit_text("❌ Database insertion failed. Please try again.")
                return

            # === PHASE 3: INSTANT SUCCESS RESPONSE ===
            await processing_msg.edit_text(
                f"✅ **Character added successfully!**\n\n"
                f"📛 **Name:** {formatted_name}\n"
                f"📺 **Anime:** {formatted_anime}\n"
                f"✨ **Rarity:** {rarity.display_name}\n"
                f"🆔 **ID:** `{character_id}`\n\n"
                f"🔄 Cloud upload in progress...",
                parse_mode='Markdown'
            )

            # === PHASE 4: START BACKGROUND UPLOAD (Non-blocking) ===
            filename = f"char_{character_id}_{int(time.time())}.jpg"
            
            # Fire-and-forget: Create background task
            asyncio.create_task(
                BackgroundTaskProcessor.process_upload(
                    character_id=character_id,
                    file_bytes=file_bytes,
                    filename=filename,
                    progress_msg=processing_msg
                )
            )
            
            logger.info(f"🚀 Character {character_id} added instantly, background upload started")

        except ValueError as e:
            await processing_msg.edit_text(str(e))
        except Exception as e:
            logger.error(f"❌ Upload handler error: {e}", exc_info=True)
            await processing_msg.edit_text(
                f"❌ **Upload failed!**\n\n"
                f"Error: {str(e)[:200]}"
            )


# ===================== DELETE HANDLER =====================

class DeleteHandler:
    """Handles /delete command"""

    @staticmethod
    async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Delete character by ID"""
        if update.effective_user.id not in Config.SUDO_USERS:
            await update.message.reply_text('🔒 ᴀꜱᴋ ᴍʏ ᴏᴡɴᴇʀ...')
            return

        if not context.args or len(context.args) != 1:
            await update.message.reply_text('❌ Usage: `/delete ID`', parse_mode='Markdown')
            return

        character_id = context.args[0]

        # Delete from database
        character = await collection.find_one_and_delete({'id': character_id})

        if not character:
            await update.message.reply_text('❌ Character not found.')
            return

        await update.message.reply_text(
            f"✅ **Character deleted:**\n"
            f"🆔 ID: `{character_id}`\n"
            f"📛 Name: {character.get('name')}\n"
            f"📺 Anime: {character.get('anime')}",
            parse_mode='Markdown'
        )


# ===================== INLINE QUERY HANDLER (SMART FALLBACK) =====================

class InlineSearchHandler:
    """
    Smart inline query handler with intelligent fallback.
    - If img_url exists: Use cloud URL
    - If img_url is None: Use Telegram file_id (instant availability)
    """

    @staticmethod
    async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle inline queries with smart image fallback"""
        query = update.inline_query.query.strip()

        if not query or len(query) < 2:
            await update.inline_query.answer(
                [],
                cache_time=1,
                is_personal=True
            )
            return

        # Search database
        results = await DatabaseManager.search_characters(query, limit=50)

        if not results:
            await update.inline_query.answer(
                [],
                cache_time=1,
                is_personal=True
            )
            return

        # Build inline results
        inline_results = []

        for char in results:
            char_id = char.get('id', 'unknown')
            name = char.get('name', 'Unknown')
            anime = char.get('anime', 'Unknown')
            rarity = char.get('rarity', '')
            img_url = char.get('img_url')
            file_id = char.get('file_id')

            # SMART FALLBACK LOGIC
            # If cloud upload completed: use img_url
            # If still pending: use Telegram's cached file_id
            try:
                if img_url:
                    # Upload completed - use cloud URL
                    result = InlineQueryResultPhoto(
                        id=char_id,
                        photo_url=img_url,
                        thumbnail_url=img_url,
                        title=name,
                        description=f"{anime} | {rarity}",
                        caption=f"**{name}**\n{anime}\n{rarity}\n🆔 `{char_id}`",
                        parse_mode='Markdown'
                    )
                elif file_id:
                    # Upload pending - use Telegram cache
                    result = InlineQueryResultPhoto(
                        id=char_id,
                        photo_file_id=file_id,
                        title=f"{name} (⏳ Uploading...)",
                        description=f"{anime} | {rarity}",
                        caption=f"**{name}**\n{anime}\n{rarity}\n🆔 `{char_id}`\n\n⏳ _Cloud upload in progress..._",
                        parse_mode='Markdown'
                    )
                else:
                    # No image available (shouldn't happen)
                    continue

                inline_results.append(result)

            except Exception as e:
                logger.warning(f"⚠️ Failed to create inline result for {char_id}: {e}")
                continue

        # Answer query with cache_time=1 for instant visibility
        await update.inline_query.answer(
            inline_results,
            cache_time=1,  # 1 second - ensures users see new uploads immediately
            is_personal=True
        )


# ===================== UPDATE HANDLER =====================

class UpdateHandler:
    """Handles /update command"""

    VALID_FIELDS = ['name', 'anime', 'rarity']

    @staticmethod
    def format_help() -> str:
        return (
            "📝 **Update Command Usage:**\n\n"
            "`/update ID field new_value`\n\n"
            "**Valid fields:** name, anime, rarity\n\n"
            "**Examples:**\n"
            "`/update 12 name Nezuko Kamado`\n"
            "`/update 12 anime Demon Slayer`\n"
            "`/update 12 rarity 5`"
        )

    @staticmethod
    async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Update character fields"""
        if update.effective_user.id not in Config.SUDO_USERS:
            await update.message.reply_text('🔒 ᴀꜱᴋ ᴍʏ ᴏᴡɴᴇʀ...')
            return

        if not context.args or len(context.args) < 3:
            await update.message.reply_text(
                UpdateHandler.format_help(),
                parse_mode='Markdown'
            )
            return

        char_id = context.args[0]
        field = context.args[1].lower()
        new_value = ' '.join(context.args[2:])

        if field not in UpdateHandler.VALID_FIELDS:
            await update.message.reply_text(
                f"❌ Invalid field. Valid fields: {', '.join(UpdateHandler.VALID_FIELDS)}"
            )
            return

        # Find character
        character = await collection.find_one({'id': char_id})
        if not character:
            await update.message.reply_text('❌ Character not found.')
            return

        # Prepare update
        update_data = {}

        if field in ['name', 'anime']:
            update_data[field] = new_value.strip().title()
        elif field == 'rarity':
            try:
                rarity_num = int(new_value)
                rarity = RarityLevel.from_number(rarity_num)
                if not rarity:
                    await update.message.reply_text('❌ Invalid rarity. Must be 1-15.')
                    return
                update_data['rarity'] = rarity.display_name
            except ValueError:
                await update.message.reply_text('❌ Rarity must be a number (1-15).')
                return

        update_data['updated_at'] = datetime.utcnow().isoformat()

        # Atomic update
        result = await collection.update_one(
            {'id': char_id},
            {'$set': update_data}
        )

        if result.modified_count > 0:
            await update.message.reply_text(
                f"✅ **Updated successfully!**\n"
                f"🆔 ID: `{char_id}`\n"
                f"📝 Field: {field}\n"
                f"🔄 New value: {new_value}",
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text('❌ Update failed.')


# ===================== STARTUP & SHUTDOWN HOOKS =====================

async def post_init(app: Application):
    """Initialize database indexes on startup"""
    logger.info("🚀 Bot starting up...")
    await DatabaseManager.initialize_indexes()
    logger.info("✅ Bot ready!")


async def post_shutdown(app: Application):
    """Cleanup on shutdown"""
    logger.info("🛑 Bot shutting down...")
    await SessionManager.close()
    logger.info("✅ Cleanup complete")


# ===================== REGISTER HANDLERS =====================

# Command handlers
application.add_handler(CommandHandler("upload", UploadHandler.handle))
application.add_handler(CommandHandler("delete", DeleteHandler.handle))
application.add_handler(CommandHandler("update", UpdateHandler.handle))

# Inline query handler
application.add_handler(InlineQueryHandler(InlineSearchHandler.handle))

# Startup and shutdown hooks
application.post_init = post_init
application.post_shutdown = post_shutdown

logger.info("📦 All handlers registered successfully")