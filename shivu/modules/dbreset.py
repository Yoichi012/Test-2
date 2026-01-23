import time
import uuid

from pyrogram import Client, filters
from pyrogram.types import Message
from motor.motor_asyncio import AsyncIOMotorClient

from shivu import shivuu
from shivu.config import Config


# =========================
# HARD SECURITY CONFIG
# =========================
OWNER_ID = 7818323042
RESET_PASSWORD = "@Piyush"     # 🔑 REQUIRED PASSWORD
CONFIRM_TIMEOUT = 60           # seconds

_pending = {}


# =========================
# MongoDB connection
# =========================
mongo_client = AsyncIOMotorClient(Config.MONGO_URL)
db = mongo_client.get_default_database()


# =========================
# STEP 1: /dbreset <password>
# =========================
@shivuu.on_message(filters.command("dbreset"))
async def dbreset_step_one(client: Client, message: Message):

    if not message.from_user:
        return

    # 🔕 silent ignore for non-owner
    if message.from_user.id != OWNER_ID:
        return

    args = message.command

    # password missing or wrong
    if len(args) != 2 or args[1] != RESET_PASSWORD:
        await message.reply_text(
            "❌ **Invalid or Missing Password**\n\n"
            "Usage:\n"
            "`/dbreset <password>`"
        )
        return

    # generate token
    token = uuid.uuid4().hex[:6].upper()
    _pending[OWNER_ID] = {
        "token": token,
        "time": time.time()
    }

    await message.reply_text(
        "⚠️ **DATABASE RESET – STEP 1** ⚠️\n\n"
        "This will **DELETE ALL MongoDB DATA**.\n"
        "This action is **IRREVERSIBLE**.\n\n"
        "To confirm, run:\n"
        f"`/dbreset confirm {token}`\n\n"
        "⏱ Token valid for **60 seconds**."
    )


# =========================
# STEP 2: /dbreset confirm <TOKEN>
# =========================
@shivuu.on_message(filters.command("dbreset") & filters.regex(r"^/dbreset confirm"))
async def dbreset_step_two(client: Client, message: Message):

    if not message.from_user:
        return

    # 🔕 silent ignore for non-owner
    if message.from_user.id != OWNER_ID:
        return

    args = message.command
    if len(args) != 3:
        return

    data = _pending.get(OWNER_ID)
    if not data:
        await message.reply_text("❌ No active reset request found.")
        return

    # token expired
    if time.time() - data["time"] > CONFIRM_TIMEOUT:
        _pending.pop(OWNER_ID, None)
        await message.reply_text("⏱ Token expired. Run `/dbreset <password>` again.")
        return

    # token mismatch
    if args[2].upper() != data["token"]:
        await message.reply_text("❌ Invalid confirmation token.")
        return

    # =========================
    # 🔥 RESET DATABASE
    # =========================
    try:
        collections = await db.list_collection_names()
        total_deleted = 0

        for name in collections:
            result = await db[name].delete_many({})
            total_deleted += result.deleted_count

        _pending.pop(OWNER_ID, None)

        await message.reply_text(
            "✅ **DATABASE RESET SUCCESSFUL**\n\n"
            f"🗑 Documents Deleted: `{total_deleted}`\n"
            f"📂 Collections Cleared: `{len(collections)}`"
        )

    except Exception as e:
        await message.reply_text(f"❌ Reset failed:\n`{e}`")