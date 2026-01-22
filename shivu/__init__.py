import logging  
import os
from pyrogram import Client 
from telegram.ext import Application
from motor.motor_asyncio import AsyncIOMotorClient

# ========================
# LOGGING CONFIGURATION
# ========================
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    handlers=[logging.FileHandler("log.txt"), logging.StreamHandler()],
    level=logging.INFO,
)

logging.getLogger("apscheduler").setLevel(logging.ERROR)
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger("pyrate_limiter").setLevel(logging.ERROR)

LOGGER = logging.getLogger(__name__)

# ========================
# IMPORT CONFIG CLASS
# ========================
try:
    from shivu.config import Development as Config
    LOGGER.info("✅ Config loaded successfully")
except ImportError as e:
    LOGGER.error(f"❌ Failed to import Config: {e}")
    LOGGER.error("Please ensure shivu/config.py exists with Development class")
    raise

# ========================
# CONFIGURATION VARIABLES
# ========================
API_ID = Config.API_ID
API_HASH = Config.API_HASH
TOKEN = Config.TOKEN
MONGO_URL = Config.MONGO_URL
OWNER_ID = Config.OWNER_ID
SUDO_USERS = Config.SUDO_USERS
GROUP_ID = Config.GROUP_ID
CHARA_CHANNEL_ID = Config.CHARA_CHANNEL_ID
VIDEO_URL = Config.VIDEO_URL
SUPPORT_CHAT = Config.SUPPORT_CHAT
UPDATE_CHAT = Config.UPDATE_CHAT
BOT_USERNAME = Config.BOT_USERNAME

# Validate critical config
if not TOKEN:
    LOGGER.error("❌ TOKEN not found in Config")
    raise ValueError("TOKEN is required in config.py")

if not MONGO_URL:
    LOGGER.error("❌ MONGO_URL not found in Config")
    raise ValueError("MONGO_URL is required in config.py")

# ========================
# INITIALIZE TELEGRAM APPLICATION
# ========================
try:
    application = Application.builder().token(TOKEN).build()
    LOGGER.info("✅ Telegram Application initialized")
except Exception as e:
    LOGGER.error(f"❌ Failed to initialize Telegram Application: {e}")
    raise

# ========================
# INITIALIZE PYROGRAM CLIENT
# ========================
shivuu = None
try:
    shivuu = Client("Shivu", API_ID, API_HASH, bot_token=TOKEN)
    LOGGER.info("✅ Pyrogram Client initialized")
except Exception as e:
    LOGGER.warning(f"⚠️ Failed to initialize Pyrogram Client: {e}")
    LOGGER.warning("Bot will continue without Pyrogram features")

# ========================
# INITIALIZE MONGODB
# ========================
try:
    lol = AsyncIOMotorClient(MONGO_URL)
    db = lol['Character_catcher']
    
    # Collections
    collection = db['anime_characters_lol']
    user_totals_collection = db['user_totals_lmaoooo']
    user_collection = db["user_collection_lmaoooo"]
    group_user_totals_collection = db['group_user_totalsssssss']
    top_global_groups_collection = db['top_global_groups']
    pm_users = db['total_pm_users']
    
    LOGGER.info("✅ MongoDB connected successfully")
except Exception as e:
    LOGGER.error(f"❌ Failed to connect to MongoDB: {e}")
    raise

# ========================
# BACKWARD COMPATIBILITY ALIASES
# ========================
# For old imports in modules (lowercase variables)
sudo_users = SUDO_USERS
api_id = API_ID
api_hash = API_HASH
mongo_url = MONGO_URL
owner_id = OWNER_ID
bot_token = TOKEN

# ========================
# MODULE LIST (AUTO-DISCOVERED)
# ========================
ALL_MODULES = []

# ========================
# EXPORT ALL COMPONENTS
# ========================
__all__ = [
    # Logging
    'LOGGER',
    
    # Config Variables (Uppercase - Primary)
    'API_ID',
    'API_HASH',
    'TOKEN',
    'MONGO_URL',
    'OWNER_ID',
    'SUDO_USERS',
    'GROUP_ID',
    'CHARA_CHANNEL_ID',
    'VIDEO_URL',
    'SUPPORT_CHAT',
    'UPDATE_CHAT',
    'BOT_USERNAME',
    
    # Backward Compatibility (Lowercase)
    'sudo_users',
    'api_id',
    'api_hash',
    'mongo_url',
    'owner_id',
    'bot_token',
    
    # Database
    'lol',
    'db',
    'collection',
    'user_totals_collection',
    'user_collection',
    'group_user_totals_collection',
    'top_global_groups_collection',
    'pm_users',
    
    # Telegram Clients
    'application',
    'shivuu',
    
    # Modules
    'ALL_MODULES'
]

LOGGER.info("✅ shivu package initialized successfully")
LOGGER.info(f"👑 Owner ID: {OWNER_ID}")
LOGGER.info(f"🤖 Bot Username: {BOT_USERNAME}")