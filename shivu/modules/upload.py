import asyncio
from typing import Dict, Any, Optional, List
import aiohttp
from pymongo import ReturnDocument
from telegram import Update, PhotoSize
from telegram.ext import CommandHandler, ContextTypes
from telegram.error import BadRequest
from telegram.ext import Application

from shivu.config import Config
from shivu import application, collection, db, CHARA_CHANNEL_ID, SUPPORT_CHAT

SESSION: Optional[aiohttp.ClientSession] = None

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

RARITY_MAP = {
    1: "⚪ ᴄᴏᴍᴍᴏɴ",
    2: "🔵 ʀᴀʀᴇ",
    3: "🟡 ʟᴇɢᴇɴᴅᴀʀʏ",
    4: "💮 ꜱᴘᴇᴄɪᴀʟ",
    5: "👹 ᴀɴᴄɪᴇɴᴛ",
    6: "🎐 ᴄᴇʟᴇꜱᴛɪᴀʟ",
    7: "🔮 ᴇᴘɪᴄ",
    8: "🪐 ᴄᴏꜱᴍɪᴄ",
    9: "⚰️ ɴɪɢʜᴛᴍᴀʀᴇ",
    10: "🌬️ ꜰʀᴏꜱᴛʙᴏʀɴ",
    11: "💝 ᴠᴀʟᴇɴᴛɪɴᴇ",
    12: "🌸 ꜱᴘʀɪɴɢ",
    13: "🏖️ ᴛʀᴏᴘɪᴄᴀʟ",
    14: "🍭 ᴋᴀᴡᴀɪɪ",
    15: "🧬 ʜʏʙʀɪᴅ"
}

VALID_FIELDS = ['img_url', 'name', 'anime', 'rarity']

def format_character_id(sequence_number: int) -> str:
    return str(sequence_number)

def format_update_help(fields: list) -> str:
    """Format update command help message (small-caps UI)."""

    help_text = (
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

    return help_text

async def get_session() -> aiohttp.ClientSession:
    global SESSION
    if SESSION is None or SESSION.closed:
        timeout = aiohttp.ClientTimeout(total=10)
        SESSION = aiohttp.ClientSession(timeout=timeout)
    return SESSION

async def validate_image_url(url: str) -> bool:
    if url.startswith('Ag'):
        return True

    session = await get_session()
    try:
        async with session.head(url, allow_redirects=True) as response:
            if response.status != 200:
                return False

            content_type = response.headers.get('Content-Type', '').lower()
            return content_type.startswith('image/')
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return False
    finally:
        pass

async def get_next_sequence_number(sequence_name: str) -> int:
    sequence_collection = db.sequences
    sequence_document = await sequence_collection.find_one_and_update(
        {'_id': sequence_name},
        {'$inc': {'sequence_value': 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER
    )
    return sequence_document['sequence_value']

def get_best_photo_file_id(photo_sizes: List[PhotoSize]) -> str:
    return photo_sizes[-1].file_id

async def send_channel_message(
    context: ContextTypes.DEFAULT_TYPE, 
    character: Dict[str, Any], 
    user_id: int, 
    user_name: str,
    action: str = "Added"
) -> Optional[int]:
    try:
        caption = (
            f"<b>Character Name:</b> {character['name']}\n"
            f"<b>Anime Name:</b> {character['anime']}\n"
            f"<b>Rarity:</b> {character['rarity']}\n"
            f"<b>ID:</b> {character['id']}\n"
            f"{action} by <a href='tg://user?id={user_id}'>{user_name}</a>"
        )

        bot = context.bot

        if action == "Added" or 'message_id' not in character:
            message = await bot.send_photo(
                chat_id=CHARA_CHANNEL_ID,
                photo=character['img_url'],
                caption=caption,
                parse_mode='HTML'
            )
            return message.message_id
        else:
            await bot.edit_message_caption(
                chat_id=CHARA_CHANNEL_ID,
                message_id=character['message_id'],
                caption=caption,
                parse_mode='HTML'
            )
            return character['message_id']
    except BadRequest as e:
        error_msg = str(e).lower()
        if "not found" in error_msg or "message to edit not found" in error_msg:
            bot = context.bot
            message = await bot.send_photo(
                chat_id=CHARA_CHANNEL_ID,
                photo=character['img_url'],
                caption=caption,
                parse_mode='HTML'
            )
            return message.message_id
        raise

async def upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id not in Config.SUDO_USERS:
        await update.message.reply_text('🔒 ᴀꜱᴋ ᴍʏ ᴏᴡɴᴇʀ...')
        return

    if not (update.message.reply_to_message and update.message.reply_to_message.photo):
        await update.message.reply_text(
            "📸 ᴘʜᴏᴛᴏ ʀᴇǫᴜɪʀᴇᴅ!\n\nʏᴏᴜ ᴍᴜꜱᴛ ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴘʜᴏᴛᴏ ᴡɪᴛʜ ᴛʜᴇ /upload ᴄᴏᴍᴍᴀɴᴅ.\n\n📝 ꜰᴏʀᴍᴀᴛ:\n\nʀᴇᴘʟʏ ᴛᴏ ᴀ ᴘʜᴏᴛᴏ\n\nꜱᴇɴᴅ: /upload\n\nɪɴᴄʟᴜᴅᴇ 3 ʟɪɴᴇꜱ:\n\n• ᴄʜᴀʀᴀᴄᴛᴇʀ ɴᴀᴍᴇ • ᴀɴɪᴍᴇ ɴᴀᴍᴇ • ʀᴀʀɪᴛʏ (1-15)"
        )
        return

    try:
        text_content = update.message.text or update.message.caption or ""
        
        lines = [line.strip() for line in text_content.split('\n') if line.strip()]
        
        if lines and lines[0].startswith('/upload'):
            lines = lines[1:]
        
        if len(lines) != 3:
            await update.message.reply_text(WRONG_FORMAT_TEXT)
            return

        char_raw, anime_raw, rarity_raw = lines

        photo_sizes = update.message.reply_to_message.photo
        img_file_id = get_best_photo_file_id(photo_sizes)
        img_url = img_file_id

        try:
            rarity_num = int(rarity_raw.strip())
            if rarity_num not in RARITY_MAP:
                await update.message.reply_text(
                    f'❌ ɪɴᴠᴀʟɪᴅ ʀᴀʀɪᴛʏ ɴᴜᴍʙᴇʀ!\n\nᴘʟᴇᴀꜱᴇ ᴜꜱᴇ ᴀ ɴᴜᴍʙᴇʀ ʙᴇᴛᴡᴇᴇɴ 1 ᴀɴᴅ 15.\n\nʏᴏᴜ ᴇɴᴛᴇʀᴇᴅ: {rarity_raw}'
                )
                return
            rarity = RARITY_MAP[rarity_num]
        except ValueError:
            await update.message.reply_text(
                f'❌ ʀᴀʀɪᴛʏ ᴍᴜꜱᴛ ʙᴇ ᴀ ɴᴜᴍʙᴇʀ!\n\nʏᴏᴜ ᴇɴᴛᴇʀᴇᴅ: "{rarity_raw}"\n\nᴇxᴘᴇᴄᴛᴇᴅ ꜰᴏʀᴍᴀᴛ: 1-15'
            )
            return

        character = {
            'img_url': img_url,
            'name': char_raw.title(),
            'anime': anime_raw.title(),
            'rarity': rarity,
            'id': format_character_id(await get_next_sequence_number('character_id'))
        }

        message_id = await send_channel_message(
            context, character, 
            update.effective_user.id, 
            update.effective_user.first_name,
            "Added"
        )
        character['message_id'] = message_id

        await collection.insert_one(character)
        
        await update.message.reply_text(
            f'✅ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴀᴅᴅᴇᴅ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ!\n\nɴᴀᴍᴇ: {character["name"]} ᴀɴɪᴍᴇ: {character["anime"]} ʀᴀʀɪᴛʏ: {character["rarity"]} ɪᴅ: {character["id"]}'
        )

    except Exception as e:
        error_msg = str(e).lower()
        
        if 'character' in locals():
            try:
                await collection.insert_one(character)
                await update.message.reply_text(
                    "⚠️ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴀᴅᴅᴇᴅ ᴛᴏ ᴅᴀᴛᴀʙᴀꜱᴇ ʙᴜᴛ ꜰᴀɪʟᴇᴅ ᴛᴏ ꜱᴇɴᴅ ᴛᴏ ᴄʜᴀɴɴᴇʟ.\n\nᴛʜᴇ ʙᴏᴛ ᴍɪɢʜᴛ ɴᴏᴛ ʜᴀᴠᴇ ᴘᴇʀᴍɪꜱꜱɪᴏɴ ᴛᴏ ᴘᴏꜱᴛ ɪɴ ᴛʜᴇ ᴄʜᴀɴɴᴇʟ."
                )
                return
            except Exception as db_error:
                pass
        
        await update.message.reply_text(
            f'❌ ᴜᴘʟᴏᴀᴅ ꜰᴀɪʟᴇᴅ!\n\nᴇʀʀᴏʀ: {str(e)[:200]}\n\nɪꜰ ᴛʜɪꜱ ᴇʀʀᴏʀ ᴘᴇʀꜱɪꜱᴛꜱ, ᴄᴏɴᴛᴀᴄᴛ: {SUPPORT_CHAT}'
        )

async def delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id not in Config.SUDO_USERS:
        await update.message.reply_text('ᴀꜱᴋ ᴍʏ ᴏᴡɴᴇʀ ᴛᴏ ᴜꜱᴇ ᴛʜɪꜱ ᴄᴏᴍᴍᴀɴᴅ...')
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

async def update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id not in Config.SUDO_USERS:
        await update.message.reply_text('ʏᴏᴜ ᴅᴏ ɴᴏᴛ ʜᴀᴠᴇ ᴘᴇʀᴍɪꜱꜱɪᴏɴ ᴛᴏ ᴜꜱᴇ ᴛʜɪꜱ ᴄᴏᴍᴍᴀɴᴅ.')
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            format_update_help(VALID_FIELDS),
            parse_mode='Markdown'
        )
        return

    char_id = context.args[0]
    field = context.args[1]

    if field not in VALID_FIELDS:
        await update.message.reply_text(
            f'❌ ɪɴᴠᴀʟɪᴅ ꜰɪᴇʟᴅ. ᴠᴀʟɪᴅ ꜰɪᴇʟᴅꜱ: {", ".join(VALID_FIELDS)}'
        )
        return

    character = await collection.find_one({'id': char_id})
    if not character:
        await update.message.reply_text('❌ ᴄʜᴀʀᴀᴄᴛᴇʀ ɴᴏᴛ ꜰᴏᴜɴᴅ.')
        return

    if field == 'img_url':
        if len(context.args) == 2:
            if not (update.message.reply_to_message and update.message.reply_to_message.photo):
                await update.message.reply_text(
                    '📸 ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴘʜᴏᴛᴏ ʀᴇǫᴜɪʀᴇᴅ!\n\nʀᴇᴘʟʏ ᴛᴏ ᴀ ᴘʜᴏᴛᴏ ᴀɴᴅ ᴜꜱᴇ: /update id img_url reply image'
                )
                return
            
            photo_sizes = update.message.reply_to_message.photo
            new_value = get_best_photo_file_id(photo_sizes)
            update_data = {'img_url': new_value}
            
        else:
            new_value = context.args[2]
            
            if not new_value.startswith('Ag'):
                is_valid_url = await validate_image_url(new_value)
                if not is_valid_url:
                    await update.message.reply_text(
                        '❌ ɪɴᴠᴀʟɪᴅ ɪᴍᴀɢᴇ ᴜʀʟ!\n\nᴛʜᴇ ᴜʀʟ ᴍᴜꜱᴛ: • ʙᴇ ᴘᴜʙʟɪᴄʟʏ ᴀᴄᴄᴇꜱꜱɪʙʟᴇ • ᴘᴏɪɴᴛ ᴅɪʀᴇᴄᴛʟʏ ᴛᴏ ᴀɴ ɪᴍᴀɢᴇ ꜰɪʟᴇ • ʀᴇᴛᴜʀɴ ʜᴛᴛᴘ ꜱᴛᴀᴛᴜꜱ 200\n\nᴛɪᴘ: ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴘʜᴏᴛᴏ ᴏʀ ᴜꜱᴇ ᴀ ᴛᴇʟᴇɢʀᴀᴍ file_id (ꜱᴛᴀʀᴛꜱ ᴡɪᴛʜ "Ag")'
                    )
                    return
            
            update_data = {'img_url': new_value}
        
    elif field in ['name', 'anime']:
        if len(context.args) != 3:
            await update.message.reply_text(
                f'❌ ᴍɪꜱꜱɪɴɢ ᴠᴀʟᴜᴇ. ᴜꜱᴀɢᴇ: /update id field new_value'
            )
            return
        
        new_value = context.args[2]
        update_data = {field: new_value.replace('-', ' ').title()}
        
    elif field == 'rarity':
        if len(context.args) != 3:
            await update.message.reply_text(
                f'❌ ᴍɪꜱꜱɪɴɢ ʀᴀʀɪᴛʏ ᴠᴀʟᴜᴇ. ᴜꜱᴀɢᴇ: /update id rarity 1-15'
            )
            return
        
        new_value = context.args[2]
        try:
            rarity_num = int(new_value)
            if rarity_num not in RARITY_MAP:
                await update.message.reply_text(
                    f'❌ ɪɴᴠᴀʟɪᴅ ʀᴀʀɪᴛʏ. ᴘʟᴇᴀꜱᴇ ᴜꜱᴇ ᴀ ɴᴜᴍʙᴇʀ ʙᴇᴛᴡᴇᴇɴ 1 ᴀɴᴅ 15.'
                )
                return
            update_data = {'rarity': RARITY_MAP[rarity_num]}
        except ValueError:
            await update.message.reply_text(f'❌ ʀᴀʀɪᴛʏ ᴍᴜꜱᴛ ʙᴇ ᴀ ɴᴜᴍʙᴇʀ (1-15).')
            return
    else:
        await update.message.reply_text(f'❌ ᴜɴᴋɴᴏᴡɴ ꜰɪᴇʟᴅ.')
        return

    updated_character = await collection.find_one_and_update(
        {'id': char_id},
        {'$set': update_data},
        return_document=ReturnDocument.AFTER
    )

    if not updated_character:
        await update.message.reply_text('❌ ꜰᴀɪʟᴇᴅ ᴛᴏ ᴜᴘᴅᴀᴛᴇ ᴄʜᴀʀᴀᴄᴛᴇʀ ɪɴ ᴅᴀᴛᴀʙᴀꜱᴇ.')
        return

    try:
        if field == 'img_url':
            if 'message_id' in updated_character:
                try:
                    await context.bot.delete_message(
                        chat_id=CHARA_CHANNEL_ID,
                        message_id=updated_character['message_id']
                    )
                except BadRequest:
                    pass

            new_message_id = await send_channel_message(
                context, updated_character,
                update.effective_user.id,
                update.effective_user.first_name,
                "Updated"
            )

            await collection.update_one(
                {'id': char_id},
                {'$set': {'message_id': new_message_id}}
            )

        elif 'message_id' in updated_character:
            await send_channel_message(
                context, updated_character,
                update.effective_user.id,
                update.effective_user.first_name,
                "Updated"
            )

        await update.message.reply_text('✅ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴜᴘᴅᴀᴛᴇᴅ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ!')

    except BadRequest as e:
        error_msg = str(e).lower()
        if "not found" in error_msg or "message to edit not found" in error_msg:
            new_message_id = await send_channel_message(
                context, updated_character,
                update.effective_user.id,
                update.effective_user.first_name,
                "Updated"
            )
            await collection.update_one(
                {'id': char_id},
                {'$set': {'message_id': new_message_id}}
            )
            await update.message.reply_text('✅ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴜᴘᴅᴀᴛᴇᴅ! (ʀᴇᴄʀᴇᴀᴛᴇᴅ ᴄʜᴀɴɴᴇʟ ᴍᴇꜱꜱᴀɢᴇ)')
        else:
            await update.message.reply_text(
                f'✅ ᴅᴀᴛᴀʙᴀꜱᴇ ᴜᴘᴅᴀᴛᴇᴅ ʙᴜᴛ ᴄʜᴀɴɴᴇʟ ᴜᴘᴅᴀᴛᴇ ꜰᴀɪʟᴇᴅ: {str(e)}'
            )
    except Exception as e:
        await update.message.reply_text(
            f'✅ ᴅᴀᴛᴀʙᴀꜱᴇ ᴜᴘᴅᴀᴛᴇᴅ ʙᴜᴛ ᴄʜᴀɴɴᴇʟ ᴜᴘᴅᴀᴛᴇ ꜰᴀɪʟᴇᴅ: {str(e)}'
        )

application.add_handler(CommandHandler("upload", upload))
application.add_handler(CommandHandler("delete", delete))
application.add_handler(CommandHandler("update", update))

async def cleanup_session() -> None:
    global SESSION
    if SESSION and not SESSION.closed:
        await SESSION.close()