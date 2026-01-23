from pyrogram import Client, filters
from pyrogram.types import Message
from motor.motor_asyncio import AsyncIOMotorClient

from shivu import shivuu
from shivu.config import Config


OWNER_ID = 7818323042
DB_NAME = "shivu_db"

mongo_client = AsyncIOMotorClient(Config.MONGO_URL)
db = mongo_client.get_database(DB_NAME)


@shivuu.on_message(filters.command("dbreset"))
async def dbreset(client: Client, message: Message):

    if not message.from_user:
        return

    # silent ignore for non-owner
    if message.from_user.id != OWNER_ID:
        return

    try:
        collections = await db.list_collection_names()

        for name in collections:
            await db[name].drop()

        await message.reply_text(
            "DATABASE RESET COMPLETE\n\n"
            "All collections dropped successfully.\n"
            "Database is now clean and ready.",
            parse_mode=None
        )

    except Exception as e:
        await message.reply_text(
            f"Database reset failed:\n{e}",
            parse_mode=None
        )