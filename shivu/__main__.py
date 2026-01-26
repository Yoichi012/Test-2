import importlib
import time
import random
import re
import asyncio
import logging
from html import escape
from typing import Dict, Any, Optional, List

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CommandHandler, MessageHandler, filters, ContextTypes

from shivu import (
    collection,
    top_global_groups_collection,
    group_user_totals_collection,
    user_collection,
    user_totals_collection,
    shivuu,
)
from shivu import application, SUPPORT_CHAT, UPDATE_CHAT, db, LOGGER
from shivu.modules import ALL_MODULES

# Import all modules
for module_name in ALL_MODULES:
    importlib.import_module("shivu.modules." + module_name)

# --- CONFIGURATION START ---
SPAM_REPEAT_THRESHOLD = 10        
SPAM_IGNORE_SECONDS = 10 * 60    
DEFAULT_MESSAGE_FREQUENCY = 100  

# 🔥 UPDATED RARITY MAP (Aapki List) 🔥
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
# --- CONFIGURATION END ---

# In-memory runtime state
locks: Dict[str, asyncio.Lock] = {}
message_counters: Dict[str, int] = {}
sent_characters: Dict[int, List[str]] = {}
last_characters: Dict[int, Dict[str, Any]] = {}
first_correct_guesses: Dict[int, int] = {}
last_user: Dict[str, Dict[str, Any]] = {}
warned_users: Dict[int, float] = {}

# Helper utilities
_escape_markdown_re = re.compile(r'([\\*_`~>#+=\\-|{}.!])')
def escape_markdown(text: str) -> str:
    return _escape_markdown_re.sub(r'\\\1', text or '')

async def _get_chat_lock(chat_id: str) -> asyncio.Lock:
    if chat_id not in locks:
        locks[chat_id] = asyncio.Lock()
    return locks[chat_id]

async def _update_user_info(user_id: int, tg_user: Update.effective_user) -> None:
    try:
        user = await user_collection.find_one({'id': user_id})
        update_fields = {}
        if hasattr(tg_user, 'username') and tg_user.username and (not user or tg_user.username != user.get('username')):
            update_fields['username'] = tg_user.username
        if tg_user.first_name and (not user or tg_user.first_name != user.get('first_name')):
            update_fields['first_name'] = tg_user.first_name
        if user:
            if update_fields:
                await user_collection.update_one({'id': user_id}, {'$set': update_fields})
        else:
            base = {
                'id': user_id,
                'username': getattr(tg_user, 'username', None),
                'first_name': tg_user.first_name,
                'characters': [],
            }
            if update_fields:
                base.update(update_fields)
            await user_collection.insert_one(base)
    except Exception as e:
        LOGGER.exception("Failed to update/insert user info: %s", e)

async def _update_group_user_totals(user_id: int, chat_id: int, tg_user: Update.effective_user) -> None:
    try:
        existing = await group_user_totals_collection.find_one({'user_id': user_id, 'group_id': chat_id})
        update_fields = {}
        if existing:
            if hasattr(tg_user, 'username') and tg_user.username and tg_user.username != existing.get('username'):
                update_fields['username'] = tg_user.username
            if tg_user.first_name and tg_user.first_name != existing.get('first_name'):
                update_fields['first_name'] = tg_user.first_name
            if update_fields:
                await group_user_totals_collection.update_one({'user_id': user_id, 'group_id': chat_id}, {'$set': update_fields})
            await group_user_totals_collection.update_one({'user_id': user_id, 'group_id': chat_id}, {'$inc': {'count': 1}})
        else:
            await group_user_totals_collection.insert_one({
                'user_id': user_id,
                'group_id': chat_id,
                'username': getattr(tg_user, 'username', None),
                'first_name': tg_user.first_name,
                'count': 1,
            })
    except Exception as e:
        LOGGER.exception("Failed to update group_user_totals: %s", e)

async def _update_top_global_groups(chat_id: int, chat_title: Optional[str]) -> None:
    try:
        group_info = await top_global_groups_collection.find_one({'group_id': chat_id})
        if group_info:
            update_fields = {}
            if chat_title and chat_title != group_info.get('group_name'):
                update_fields['group_name'] = chat_title
            if update_fields:
                await top_global_groups_collection.update_one({'group_id': chat_id}, {'$set': update_fields})
            await top_global_groups_collection.update_one({'group_id': chat_id}, {'$inc': {'count': 1}})
        else:
            await top_global_groups_collection.insert_one({
                'group_id': chat_id,
                'group_name': chat_title or '',
                'count': 1,
            })
    except Exception as e:
        LOGGER.exception("Failed to update top_global_groups: %s", e)

# Spawning Handlers
async def message_counter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.effective_user:
        return

    chat_id_str = str(update.effective_chat.id)
    user_id = update.effective_user.id
    lock = await _get_chat_lock(chat_id_str)

    async with lock:
        try:
            chat_frequency = await user_totals_collection.find_one({'chat_id': chat_id_str})
            message_frequency = chat_frequency.get('message_frequency', DEFAULT_MESSAGE_FREQUENCY) if chat_frequency else DEFAULT_MESSAGE_FREQUENCY
        except Exception:
            message_frequency = DEFAULT_MESSAGE_FREQUENCY

        last = last_user.get(chat_id_str)
        if last and last.get('user_id') == user_id:
            last['count'] += 1
            if last['count'] >= SPAM_REPEAT_THRESHOLD:
                last_time = warned_users.get(user_id)
                if last_time and (time.time() - last_time) < SPAM_IGNORE_SECONDS:
                    return
                try:
                    user_first_name = str(update.effective_user.first_name)
                    await update.message.reply_text(
                        f"⚠️ Don't spam, {escape(user_first_name)}. Your messages will be ignored for 10 minutes."
                    )
                except Exception:
                    pass
                warned_users[user_id] = time.time()
                return
        else:
            last_user[chat_id_str] = {'user_id': user_id, 'count': 1}

        message_counters.setdefault(chat_id_str, 0)
        message_counters[chat_id_str] += 1

        if message_counters[chat_id_str] >= message_frequency:
            message_counters[chat_id_str] = 0
            await send_image(update, context)

async def send_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    try:
        all_characters = await collection.find({}).to_list(length=None)
    except Exception:
        all_characters = []

    if not all_characters:
        return

    sent_characters.setdefault(chat_id, [])
    if len(sent_characters[chat_id]) >= len(all_characters):
        sent_characters[chat_id] = []

    choices = [c for c in all_characters if c.get('id') not in sent_characters[chat_id]]
    character = random.choice(choices if choices else all_characters)

    sent_characters[chat_id].append(character.get('id'))
    last_characters[chat_id] = character
    first_correct_guesses.pop(chat_id, None)

    # 🔥 FIX: Map Numeric Rarity to Custom Emoji/Text
    try:
        raw_rarity = int(character.get('rarity'))
    except (ValueError, TypeError):
        raw_rarity = 'Unknown'

    rarity_text = RARITY_MAP.get(raw_rarity, str(raw_rarity))

    caption = (
        f"A new {escape(rarity_text)} character appeared!\n"
        f"Guess the character name with /guess <name> to add them to your harem."
    )

    try:
        await context.bot.send_photo(chat_id=chat_id, photo=character.get('img_url'), caption=caption)
    except Exception:
        pass

async def guess(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.effective_user:
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if chat_id not in last_characters or chat_id in first_correct_guesses:
        return

    guess_text = ' '.join(context.args).strip().lower() if context.args else ''
    if not guess_text:
        await update.message.reply_text("Please provide a guess.")
        return

    character = last_characters.get(chat_id)
    name_parts = (character.get('name') or '').lower().split()

    if sorted(name_parts) == sorted(guess_text.split()) or any(part == guess_text for part in name_parts):
        first_correct_guesses[chat_id] = user_id
        await _update_user_info(user_id, update.effective_user)
        
        # --- 🔥 FIX START: Database Saving Logic 🔥 ---
        # 1. Create a copy so we don't mess up the original object
        character_data = character.copy()
        
        # 2. Remove the '_id' field if it exists (This solves the saving error)
        if '_id' in character_data:
            del character_data['_id']
            
        # 3. Save the clean copy to the user's collection
        await user_collection.update_one(
            {'id': user_id}, 
            {'$addToSet': {'characters': character_data}}
        )
        # --- 🔥 FIX END 🔥 ---

        await _update_group_user_totals(user_id, chat_id, update.effective_user)
        await _update_top_global_groups(chat_id, update.effective_chat.title)

        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("See Harem", switch_inline_query_current_chat=f"collection.{user_id}")]])

        safe_name = escape(str(update.effective_user.first_name or ""))

        # 🔥 FIX: Mapping Logic for Guess Reply
        try:
            raw_rarity = int(character.get('rarity'))
        except (ValueError, TypeError):
            raw_rarity = 'Unknown'

        rarity_text = RARITY_MAP.get(raw_rarity, str(raw_rarity))

        char_name = escape(str(character.get("name", "Unknown")))

        reply_text = (
            f'<b><a href="tg://user?id={user_id}">{safe_name}</a></b> you guessed a new character ✅\n\n'
            f'NAME: <b>{char_name}</b>\n'
            f'RARITY: <b>{escape(rarity_text)}</b>'
        )
        await update.message.reply_text(reply_text, reply_markup=keyboard, parse_mode='HTML')
    else:
        await update.message.reply_text("Please write the correct character name. ❌")

def main() -> None:
    # Register commands
    application.add_handler(CommandHandler(["guess", "protecc", "collect", "grab", "hunt"], guess, block=False))
    application.add_handler(MessageHandler(filters.ALL, message_counter, block=False))

    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    shivuu.start()
    LOGGER.info("Sᴇɴᴘᴀɪ Wᴀɪғᴜ Bᴏᴛ ɪs Bᴀᴄᴋ Bᴀʙᴇ")
    main()
