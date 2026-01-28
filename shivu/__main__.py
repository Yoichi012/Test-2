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
from shivu.modules.leaderboard import update_daily_user_guess, update_daily_group_guess

# Import all modules declared in ALL_MODULES (same as original behavior)
for module_name in ALL_MODULES:
    importlib.import_module("shivu.modules." + module_name)

# 🔥 NEW: Import setrarity module for rarity and character lock management
import shivu.modules.setrarity as setrarity

# Rarity display mapping (presentation layer only - DB still stores integers)
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
    15: "🧬 ʜʏʙʀɪᴅ",
}

# Constants
SPAM_REPEAT_THRESHOLD = 10
SPAM_IGNORE_SECONDS = 10 * 60
DEFAULT_MESSAGE_FREQUENCY = 100
MAX_SPAWN_ATTEMPTS = 10  # 🔥 NEW: Maximum attempts to find a spawnable character

# In-memory runtime state
locks: Dict[str, asyncio.Lock] = {}
message_counters: Dict[str, int] = {}
sent_characters: Dict[int, List[str]] = {}
last_characters: Dict[int, Dict[str, Any]] = {}
first_correct_guesses: Dict[int, int] = {}
last_user: Dict[str, Dict[str, Any]] = {}
warned_users: Dict[int, float] = {}

_escape_markdown_re = re.compile(r'([\\*_`~>#+=\\-|{}.!])')
def escape_markdown(text: str) -> str:
    return _escape_markdown_re.sub(r'\\\1', text or '')

def to_small_caps(text: str) -> str:
    mapping = {
        'a': 'ᴀ', 'b': 'ʙ', 'c': 'ᴄ', 'd': 'ᴅ', 'e': 'ᴇ', 'f': 'ꜰ', 'g': 'ɢ', 'h': 'ʜ', 'i': 'ɪ', 
        'j': 'ᴊ', 'k': 'ᴋ', 'l': 'ʟ', 'm': 'ᴍ', 'n': 'ɴ', 'o': 'ᴏ', 'p': 'ᴘ', 'q': 'ǫ', 'r': 'ʀ', 
        's': 'ꜱ', 't': 'ᴛ', 'u': 'ᴜ', 'v': 'ᴠ', 'w': 'ᴡ', 'x': 'x', 'y': 'ʏ', 'z': 'ᴢ',
        'A': 'ᴀ', 'B': 'ʙ', 'C': 'ᴄ', 'D': 'ᴅ', 'E': 'ᴇ', 'F': 'ꜰ', 'G': 'ɢ', 'H': 'ʜ', 'I': 'ɪ',
        'J': 'ᴊ', 'K': 'ᴋ', 'L': 'ʟ', 'M': 'ᴍ', 'N': 'ɴ', 'O': 'ᴏ', 'P': 'ᴘ', 'Q': 'ǫ', 'R': 'ʀ',
        'S': 'ꜱ', 'T': 'ᴛ', 'U': 'ᴜ', 'V': 'ᴠ', 'W': 'ᴡ', 'X': 'x', 'Y': 'ʏ', 'Z': 'ᴢ',
        '0': '0', '1': '1', '2': '2', '3': '3', '4': '4', '5': '5', '6': '6', '7': '7', '8': '8', '9': '9',
        ' ': ' ', '!': '!', ':': ':', '.': '.', ',': ',', "'": "'", '"': '"', '?': '?', 
        '(': '(', ')': ')', '[': '[', ']': ']', '{': '{', '}': '}', '-': '-', '_': '_'
    }
    result = []
    for char in text:
        if char in mapping:
            result.append(mapping[char])
        else:
            result.append(char)
    return ''.join(result)

def get_rarity_display(character: Dict[str, Any]) -> str:
    rarity_raw = character.get('rarity', 'Unknown')
    rarity_text = RARITY_MAP.get(rarity_raw, str(rarity_raw))
    return str(rarity_text)

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
                'balance': 0,
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
            LOGGER.exception("Error fetching message_frequency; using default")

        last = last_user.get(chat_id_str)
        if last and last.get('user_id') == user_id:
            last['count'] += 1
            if last['count'] >= SPAM_REPEAT_THRESHOLD:
                last_time = warned_users.get(user_id)
                if last_time and (time.time() - last_time) < SPAM_IGNORE_SECONDS:
                    return
                try:
                    await update.message.reply_text(
                        to_small_caps(f"⚠️ Don't spam, {escape(update.effective_user.first_name)}.\nYour messages will be ignored for {SPAM_IGNORE_SECONDS // 60} minutes.")
                    )
                except Exception:
                    LOGGER.exception("Failed to send spam warning")
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

    # 🔥 NEW: Get chat's disabled rarities FIRST
    try:
        disabled_rarities = await setrarity.get_disabled_rarities(chat_id)
    except Exception as e:
        LOGGER.exception(f"Failed to get disabled rarities: {e}")
        disabled_rarities = []

    # 🔥 NEW: Get locked character IDs
    try:
        locked_character_ids = await setrarity.get_locked_character_ids()
    except Exception as e:
        LOGGER.exception(f"Failed to get locked characters: {e}")
        locked_character_ids = []

    try:
        # 🔥 OPTIMIZED: Fetch only characters with ENABLED rarities and NOT locked
        query = {}

        # Exclude disabled rarities
        if disabled_rarities:
            query['rarity'] = {'$nin': disabled_rarities}

        # Exclude locked characters
        if locked_character_ids:
            if 'id' in query:
                query['$and'] = [
                    {'id': {'$nin': locked_character_ids}},
                    query
                ]
            else:
                query['id'] = {'$nin': locked_character_ids}

        all_characters = await collection.find(query).to_list(length=None)

        if disabled_rarities or locked_character_ids:
            LOGGER.info(f"📊 Filtered characters: disabled_rarities={disabled_rarities}, locked_chars={len(locked_character_ids)}, available={len(all_characters)}")
    except Exception:
        LOGGER.exception("Failed to fetch characters from DB")
        all_characters = []

    if not all_characters:
        try:
            await context.bot.send_message(
                chat_id=chat_id, 
                text=to_small_caps("No characters available right now. All rarities may be disabled or characters locked.")
            )
        except Exception:
            LOGGER.exception("Failed to notify about empty collection")
        return

    sent_characters.setdefault(chat_id, [])

    if len(sent_characters[chat_id]) >= len(all_characters):
        sent_characters[chat_id] = []

    # Select from unsent characters
    choices = [c for c in all_characters if c.get('id') not in sent_characters[chat_id]]
    if not choices:
        choices = all_characters
        sent_characters[chat_id] = []  # Reset sent list

    # Select random character (already filtered for enabled rarity + not locked)
    character = random.choice(choices)
    LOGGER.info(f"✅ Character selected: ID={character.get('id')}, Rarity={character.get('rarity', 1)}")

    sent_characters[chat_id].append(character.get('id'))
    last_characters[chat_id] = character
    first_correct_guesses.pop(chat_id, None)

    rarity_display = get_rarity_display(character)
    # Create caption with /guess not converted to small caps
    line1 = to_small_caps(f"✨ A new {escape(rarity_display)} character appeared!")
    line2 = to_small_caps("✨ Guess the character name with ") + "/guess" + to_small_caps(" to add them to your harem.")
    caption = f"{line1}\n{line2}"

    try:
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=character.get('img_url'),
            caption=caption,
        )
    except Exception:
        LOGGER.exception("Failed to send photo for character; sending text instead")
        try:
            await context.bot.send_message(chat_id=chat_id, text=caption)
        except Exception:
            LOGGER.exception("Failed to send fallback text message")

async def guess(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.effective_user:
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if chat_id not in last_characters:
        return

    if chat_id in first_correct_guesses:
        await update.message.reply_text(to_small_caps("❌ Already guessed by someone. Try next time."))
        return

    guess_text = ' '.join(context.args).strip().lower() if context.args else ''
    if not guess_text:
        await update.message.reply_text("Please provide a guess, e.g. /guess Alice")
        return

    if "()" in guess_text or "&" in guess_text:
        await update.message.reply_text(to_small_caps("You can't use these characters in your guess."))
        return

    character = last_characters.get(chat_id)
    name_parts = (character.get('name') or '').lower().split()

    if sorted(name_parts) == sorted(guess_text.split()) or any(part == guess_text for part in name_parts):
        first_correct_guesses[chat_id] = user_id

        character_to_store = character.copy()
        character_to_store.pop('_id', None)

        # 🔥 FIXED: Update balance in user_collection directly
        try:
            await _update_user_info(user_id, update.effective_user)

            await user_collection.update_one(
                {'id': user_id},
                {'$inc': {'balance': 100}},
                upsert=True
            )
            LOGGER.info(f"✅ Added 100 coins to user {user_id} balance")
        except Exception as e:
            LOGGER.exception(f"❌ Failed to update user balance: {e}")

        try:
            await user_collection.update_one(
                {'id': user_id}, 
                {'$push': {'characters': character_to_store}}
            )
        except Exception as e:
            LOGGER.exception(f"Failed updating user character collection: {e}")
            await update.message.reply_text(to_small_caps("Failed to add character to your collection. Please try again."))
            return

        try:
            await _update_group_user_totals(user_id, chat_id, update.effective_user)
            await _update_top_global_groups(chat_id, update.effective_chat.title)
        except Exception:
            LOGGER.exception("Failed updating group/global stats")

        try:
            safe_username = update.effective_user.username if update.effective_user.username else ""
            safe_first_name = update.effective_user.first_name if update.effective_user.first_name else "Unknown"

            await update_daily_user_guess(
                user_id=user_id,
                username=safe_username,
                first_name=safe_first_name
            )
        except Exception as e:
            LOGGER.exception(f"❌ Failed to update daily user guess: {e}")

        if update.effective_chat.type in ['group', 'supergroup']:
            try:
                safe_group_name = update.effective_chat.title if update.effective_chat.title else "Unknown Group"

                await update_daily_group_guess(
                    group_id=chat_id,
                    group_name=safe_group_name
                )
            except Exception as e:
                LOGGER.exception(f"❌ Failed to update daily group guess: {e}")

        coin_alert_msg = await update.message.reply_text(
            to_small_caps("✨ ᴄᴏɴɢʀᴀᴛᴜʟᴀᴛɪᴏɴꜱ 🎉  ʏᴏᴜ ɢᴜᴇꜱꜱᴇᴅ ɪᴛ ʀɪɢʜᴛ! ᴀꜱ ᴀ ʀᴇᴡᴀʀᴅ, 100 ᴄᴏɪɴꜱ ʜᴀᴠᴇ ʙᴇᴇɴ ᴀᴅᴅᴇᴅ ᴛᴏ ʏᴏᴜʀ ʙᴀʟᴀɴᴄᴇ.."),
            parse_mode='HTML'
        )

        try:
            await coin_alert_msg.set_reaction("🎉")
        except Exception as e:
            LOGGER.exception(f"Failed to set reaction: {e}")

        safe_name = escape(update.effective_user.first_name or "")
        character_name = escape(character.get('name', 'Unknown'))
        anime_name = escape(character.get('anime', 'Unknown'))
        rarity_display = get_rarity_display(character)
        safe_rarity = escape(rarity_display)
        character_id = escape(str(character.get('id', 'Unknown')))

        reveal_message = to_small_caps(f"✨ ᴄᴏɴɢʀᴀᴛᴜʟᴀᴛɪᴏɴꜱ 🎊 {safe_name} ᴛʜɪꜱ ᴄʜᴀʀᴀᴄᴛᴇʀ ʜᴀꜱ ʙᴇᴇɴ ᴀᴅᴅᴇᴅ ᴛᴏ ʏᴏᴜʀ.\n\n"
                                       f"👤 ɴᴀᴍᴇ: {character_name}\n"
                                       f"🎬 ᴀɴɪᴍᴇ: {anime_name}\n"
                                       f"✨ ʀᴀʀɪᴛʏ: {safe_rarity}\n"
                                       f"🆔 ɪᴅ: {character_id}\n\n"
                                       f"✅ ꜱᴜᴄᴄᴇꜱꜱ ꜰᴜʟʟ ᴀᴅᴅ ʜᴀʀᴇᴍ.")

        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton(
                "ꜱᴇᴇ ʜᴀʀᴇᴍ",
                switch_inline_query_current_chat=str(user_id)
            )]]
        )

        try:
            await update.message.reply_text(
                reveal_message, 
                reply_markup=keyboard, 
                parse_mode='HTML'
            )
        except Exception:
            LOGGER.exception("Failed to send character reveal reply")
            try:
                await update.message.reply_text(
                    to_small_caps(f"You guessed {character.get('name', 'a character')} ✅")
                )
            except Exception:
                LOGGER.exception("Failed fallback reply")
    else:
        await update.message.reply_text(
            to_small_caps("Please write the correct character name. ❌")
        )

async def fav(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user:
        return

    user_id = update.effective_user.id
    args = context.args or []
    if not args:
        await update.message.reply_text(to_small_caps("Please provide a character id: /fav <id>"))
        return

    character_id = args[0]

    try:
        user = await user_collection.find_one({'id': user_id})
    except Exception:
        LOGGER.exception("Failed to fetch user for fav")
        user = None

    if not user or not user.get('characters'):
        await update.message.reply_text(to_small_caps("You have not collected any characters yet."))
        return

    character = next((c for c in user['characters'] if c.get('id') == character_id), None)
    if not character:
        await update.message.reply_text(to_small_caps("That character is not in your collection."))
        return

    try:
        await user_collection.update_one({'id': user_id}, {'$addToSet': {'favorites': character_id}})
        await update.message.reply_text(to_small_caps(f'Character {character.get("name")} has been added to your favorites.'))
    except Exception:
        LOGGER.exception("Failed to set favorite character")
        await update.message.reply_text(to_small_caps("Failed to mark favorite. Please try again later."))

def main() -> None:
    # 🔥 NEW: Setup setrarity command handlers
    setrarity.setup_handlers()

    # Existing handlers
    application.add_handler(CommandHandler(["guess", "protecc", "collect", "grab", "hunt"], guess, block=False))
    application.add_handler(CommandHandler("fav", fav, block=False))
    application.add_handler(MessageHandler(filters.ALL, message_counter, block=False))
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    shivuu.start()
    LOGGER.info("Sᴇɴᴘᴀɪ Wᴀɪғᴜ Bᴏᴛ ɪs Bᴀᴄᴋ Bᴀʙᴇ")
    main()