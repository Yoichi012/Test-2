import logging  
import os
from pyrogram import Client 
from telegram.ext import Application
from motor.motor_asyncio import AsyncIOMotorClient

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    handlers=[logging.FileHandler("log.txt"), logging.StreamHandler()],
    level=logging.INFO,
)

logging.getLogger("apscheduler").setLevel(logging.ERROR)
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger("pyrate_limiter").setLevel(logging.ERROR)

LOGGER = logging.getLogger(__name__)

from shivu.config import Development as Config

# Configuration Variables
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

# Initialize Telegram Application
application = Application.builder().token(TOKEN).build()

# Initialize Pyrogram Client
shivuu = Client("Shivu", API_ID, API_HASH, bot_token=TOKEN)

# Initialize MongoDB
lol = AsyncIOMotorClient(MONGO_URL)
db = lol['Character_catcher']
collection = db['anime_characters_lol']
user_totals_collection = db['user_totals_lmaoooo']
user_collection = db["user_collection_lmaoooo"]
group_user_totals_collection = db['group_user_totalsssssss']
top_global_groups_collection = db['top_global_groups']
pm_users = db['total_pm_users']

# Backward compatibility aliases (for old imports in modules)
sudo_users = SUDO_USERS
api_id = API_ID
api_hash = API_HASH
mongo_url = MONGO_URL
