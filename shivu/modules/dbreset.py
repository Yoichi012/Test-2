import time
import uuid

from pyrogram import Client, filters
from pyrogram.types import Message
from motor.motor_asyncio import AsyncIOMotorClient

from shivu import shivuu
from shivu.config import Config


# =========================
# HARD SECURITY
# =========================
OWNER_ID = 7818323042
RESET_PASSWORD = "@Piyush"
CONFIRM_TIMEOUT = 60  # seconds
DB_NAME = "shivu_db"

_pending = {}


# =========================
# MongoDB client
# =========================
mongo_client = AsyncIOMotorClient(Config.MONGO_URL)


# =========================
# STEP 1: /dbreset <password>
# =========================
@shivuu.on_message(filters.command("dbreset"))
async def dbreset_step_one(client: Client, message: Message):

    if not message.from_user:
        return

    # silent ignore for non-owner
    if message.from_user.id != OWNER_ID:
        return

    args = message.command

    # password check
    if len(args) != 2 or args[1] != RESET_PASSWORD:
        await message.reply_text(
            "Invalid or missing password.\n\n"
            "Usage:\n"
            "/dbreset <password>",
            parse_mode=None
        )
        return

    # generate token
    token = uuid.uuid4().hex[:6].upper()
    _pending[OWNER_ID] = {
        "token": token,
        "time": time.time()
    }

    await message.reply_text(
        "FULL DATABASE RESET - STEP 1\n\n"
        "This will DELETE THE ENTIRE DATABASE.\n"
        "All collections, data and indexes will be removed.\n\n"
        "To confirm, run this command:\n"
        f"/dbreset confirm {token}\n\n"
        "Token valid for 60 seconds.",
        parse_mode=None
    )


# =========================
# STEP 2: /dbreset confirm <TOKEN>
# =========================
@shivuu.on_message(filters.command("dbreset") & filters.regex(r"^/dbreset confirm"))
async def dbreset_step_two(client: Client, message: Message):

    if not message.from_user:
        return

    # silent ignore for non-owner
    if message.from_user.id != OWNER_ID:
        return

    args = message.command
    if len(args) != 3:
        return

    data = _pending.get(OWNER_ID)
    if not data:
        await message.reply_text(
            "No active reset request found.",
            parse_mode=None
        )
        return

    # token expiry check
    if time.time() - data["time"] > CONFIRM_TIMEOUT:
        _pending.pop(OWNER_ID, None)
        await message.reply_text(
            "Token expired. Run /dbreset <password> again.",
            parse_mode=None
        )
        return

    # token match check
    if args[2].upper() != data["token"]:
        await message.reply_text(
            "Invalid confirmation token.",
            parse_mode=None
        )
        return

    # =========================
    # DROP DATABASE
    # =========================
    try:
        await mongo_client.drop_database(DB_NAME)
        _pending.pop(OWNER_ID, None)

        await message.reply_text(
            "DATABASE RESET COMPLETE\n\n"
            f"Dropped database: {DB_NAME}\n"
            "MongoDB will start fresh on next use.",
            parse_mode=None
        )

    except Exception as e:
        await message.reply_text(
            f"Database reset failed:\n{e}",
            parse_mode=None
        )