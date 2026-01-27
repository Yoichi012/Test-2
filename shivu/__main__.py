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
    user_balance_coll,
    change_balance,
)
from shivu import application, SUPPORT_CHAT, UPDATE_CHAT, db, LOGGER
from shivu.modules import ALL_MODULES
from shivu.modules.leaderboard import update_daily_user_guess, update_daily_group_guess

# Import all modules declared in ALL_MODULES (same as original behavior)
for module_name in ALL_MODULES:
    importlib.import_module("shivu.modules." + module_name)

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

# Constants (tweak as needed)
SPAM_REPEAT_THRESHOLD = 10        # number of repeated messages to consider spam
SPAM_IGNORE_SECONDS = 10 * 60    # ignore duration in seconds (10 minutes)
DEFAULT_MESSAGE_FREQUENCY = 100  # fallback message frequency if none stored

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
    """Escape Markdown-ish characters (kept for legacy usage)."""
    return _escape_markdown_re.sub(r'\\\1', text or '')

def to_small_caps(text: str) -> str:
    """
    Convert normal text to small caps unicode characters
    """
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

    # Convert emojis and special characters properly
    result = []
    for char in text:
        if char in mapping:
            result.append(mapping[char])
        else:
            # Keep emojis and unsupported characters as-is
            result.append(char)

    return ''.join(result)

def get_rarity_display(character: Dict[str, Any]) -> str:
    """
    Convert character rarity to safe display string.
    Handles integer rarity from DB and provides safe fallback.
    """
    rarity_raw = character.get('rarity', 'Unknown')
    rarity_text = RARITY_MAP.get(rarity_raw, str(rarity_raw))
    return str(rarity_text)  # Ensure always string for html.escape()

async def _get_chat_lock(chat_id: str) -> asyncio.Lock:
    """Return a per-chat asyncio.Lock, creating it if necessary."""
    if chat_id not in locks:
        locks[chat_id] = asyncio.Lock()
    return locks[chat_id]

async def _update_user_info(user_id: int, tg_user: Update.effective_user) -> None:
    """Ensure the user document exists and has updated username/first_name."""
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
                'balance': 0,  # Initialize balance
            }
            if update_fields:
                base.update(update_fields)
            await user_collection.insert_one(base)
    except Exception as e:
        LOGGER.exception("Failed to update/insert user info: %s", e)

async def _update_group_user_totals(user_id: int, chat_id: int, tg_user: Update.effective_user) -> None:
    """Increment or insert group_user_totals entry for (user, group)."""
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
    """Increment or insert top_global_groups for the chat."""
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

# Handlers
async def message_counter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Count messages and occasionally send a character image."""
    if not update.effective_chat or not update.effective_user:
        return

    chat_id_str = str(update.effective_chat.id)
    user_id = update.effective_user.id
    lock = await _get_chat_lock(chat_id_str)

    async with lock:
        # load message_frequency for chat from DB (fallback to default)
        try:
            chat_frequency = await user_totals_collection.find_one({'chat_id': chat_id_str})
            message_frequency = chat_frequency.get('message_frequency', DEFAULT_MESSAGE_FREQUENCY) if chat_frequency else DEFAULT_MESSAGE_FREQUENCY
        except Exception:
            message_frequency = DEFAULT_MESSAGE_FREQUENCY
            LOGGER.exception("Error fetching message_frequency; using default")

        # spam detection: repeated messages from same user
        last = last_user.get(chat_id_str)
        if last and last.get('user_id') == user_id:
            last['count'] += 1
            if last['count'] >= SPAM_REPEAT_THRESHOLD:
                last_time = warned_users.get(user_id)
                if last_time and (time.time() - last_time) < SPAM_IGNORE_SECONDS:
                    return
                # warn and throttle
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

        # count messages and trigger send_image when threshold met
        message_counters.setdefault(chat_id_str, 0)
        message_counters[chat_id_str] += 1

        if message_counters[chat_id_str] >= message_frequency:
            # reset counter and send image
            message_counters[chat_id_str] = 0
            await send_image(update, context)

async def send_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a random character image to the chat and prepare for guesses."""
    chat_id = update.effective_chat.id

    try:
        all_characters = await collection.find({}).to_list(length=None)
    except Exception:
        LOGGER.exception("Failed to fetch characters from DB")
        all_characters = []

    if not all_characters:
        try:
            await context.bot.send_message(chat_id=chat_id, text=to_small_caps("No characters available right now."))
        except Exception:
            LOGGER.exception("Failed to notify about empty collection")
        return

    # ensure sent_characters list exists for this chat
    sent_characters.setdefault(chat_id, [])

    # reset when we've exhausted all characters
    if len(sent_characters[chat_id]) >= len(all_characters):
        sent_characters[chat_id] = []

    # pick a random character not already sent to this chat
    choices = [c for c in all_characters if c.get('id') not in sent_characters[chat_id]]
    if not choices:
        choices = all_characters
    character = random.choice(choices)
    if not character:
        LOGGER.error("No character chosen from collection")
        return

    # track character and clear any previous first-correct-guess
    sent_characters[chat_id].append(character.get('id'))
    last_characters[chat_id] = character
    first_correct_guesses.pop(chat_id, None)

    rarity_display = get_rarity_display(character)
    caption = to_small_caps(
        f"✨ A new {escape(rarity_display)} character appeared!\n"
        f"✨ Guess the character name with /guess <name> to add them to your harem."
    )

    try:
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=character.get('img_url'),
            caption=caption,
        )
    except Exception:
        # fallback to sending text if photo fails
        LOGGER.exception("Failed to send photo for character; sending text instead")
        try:
            await context.bot.send_message(chat_id=chat_id, text=caption)
        except Exception:
            LOGGER.exception("Failed to send fallback text message")

async def guess(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /guess command to allow users to collect characters."""
    if not update.effective_chat or not update.effective_user:
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if chat_id not in last_characters:
        # nothing to guess for
        return

    if chat_id in first_correct_guesses:
        await update.message.reply_text(to_small_caps("❌ Already guessed by someone. Try next time."))
        return

    # combine args into a lowercase guess string
    guess_text = ' '.join(context.args).strip().lower() if context.args else ''
    if not guess_text:
        # Command text ko normal font mein rakhein
        await update.message.reply_text("Please provide a guess, e.g. /guess Alice")
        return

    # disallow suspicious characters
    if "()" in guess_text or "&" in guess_text:
        await update.message.reply_text(to_small_caps("You can't use these characters in your guess."))
        return

    character = last_characters.get(chat_id)
    name_parts = (character.get('name') or '').lower().split()

    # exact-equality or same-word-set matching (improves original behavior)
    if sorted(name_parts) == sorted(guess_text.split()) or any(part == guess_text for part in name_parts):
        # mark first correct guess
        first_correct_guesses[chat_id] = user_id

        # Create a clean character object without _id
        character_to_store = character.copy()
        # Remove _id field to avoid MongoDB errors
        character_to_store.pop('_id', None)

        # STEP 1: Update user's balance with 100 coins
        try:
            # Use change_balance helper function
            await change_balance(user_id, 100)
        except Exception as e:
            LOGGER.exception(f"Failed to update user balance: {e}")

        # update/create user doc and append character to their collection atomically
        try:
            await _update_user_info(user_id, update.effective_user)
            # Use $push to allow duplicates in user's collection
            await user_collection.update_one(
                {'id': user_id}, 
                {'$push': {'characters': character_to_store}}
            )
        except Exception as e:
            LOGGER.exception(f"Failed updating user character collection: {e}")
            await update.message.reply_text(to_small_caps("Failed to add character to your collection. Please try again."))
            return

        # update group & global stats
        try:
            await _update_group_user_totals(user_id, chat_id, update.effective_user)
            await _update_top_global_groups(chat_id, update.effective_chat.title)
        except Exception:
            LOGGER.exception("Failed updating group/global stats")

        # 🔥 DAILY TRACKING UPDATE - FIXED WITH SAFE DATA HANDLING 🔥
        # Update daily user guess count
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

        # Update daily group guess count (only for groups/supergroups)
        if update.effective_chat.type in ['group', 'supergroup']:
            try:
                safe_group_name = update.effective_chat.title if update.effective_chat.title else "Unknown Group"

                await update_daily_group_guess(
                    group_id=chat_id,
                    group_name=safe_group_name
                )
            except Exception as e:
                LOGGER.exception(f"❌ Failed to update daily group guess: {e}")
        # 🔥 END OF DAILY TRACKING UPDATE 🔥

        # STEP 2: Coin Alert Message (with reaction)
        coin_alert_msg = await update.message.reply_text(
            to_small_caps("✨ ᴄᴏɴɢʀᴀᴛᴜʟᴀᴛɪᴏɴꜱ 🎉  ʏᴏᴜ ɢᴜᴇꜱꜱᴇᴅ ɪᴛ ʀɪɢʜᴛ! ᴀꜱ ᴀ ʀᴇᴡᴀʀᴅ, 100 ᴄᴏɪɴꜱ ʜᴀᴠᴇ ʙᴇᴇɴ ᴀᴅᴅᴇᴅ ᴛᴏ ʏᴏᴜʀ ʙᴀʟᴀɴᴄᴇ.."),
            parse_mode='HTML'
        )

        # Set reaction on the coin alert message
        try:
            await coin_alert_msg.set_reaction("🎉")
        except Exception as e:
            LOGGER.exception(f"Failed to set reaction: {e}")

        # STEP 3: Character Reveal Message
        safe_name = escape(update.effective_user.first_name or "")
        character_name = escape(character.get('name', 'Unknown'))
        anime_name = escape(character.get('anime', 'Unknown'))
        rarity_display = get_rarity_display(character)
        safe_rarity = escape(rarity_display)
        character_id = escape(str(character.get('id', 'Unknown')))

        # Create character reveal message with specified formatting
        reveal_message = to_small_caps(f"✨ ᴄᴏɴɢʀᴀᴛᴜʟᴀᴛɪᴏɴꜱ 🎊 {safe_name} ᴛʜɪꜱ ᴄʜᴀʀᴀᴄᴛᴇʀ ʜᴀꜱ ʙᴇᴇɴ ᴀᴅᴅᴇᴅ ᴛᴏ ʏᴏᴜʀ.\n\n"
                                       f"👤 ɴᴀᴍᴇ: {character_name}\n"
                                       f"🎬 ᴀɴɪᴍᴇ: {anime_name}\n"
                                       f"✨ ʀᴀʀɪᴛʏ: {safe_rarity}\n"
                                       f"🆔 ɪᴅ: {character_id}\n\n"
                                       f"✅ ꜱᴜᴄᴄᴇꜱꜱ ꜰᴜʟʟ ᴀᴅᴅ ʜᴀʀᴇᴍ.")

        # FIXED: keyboard that shows inline query for the user's collection
        # Changed from "collection.{user_id}" to just "{user_id}" to match your inline query handler
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
            # fallback plain text
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
    """Mark one of the user's collected characters as favorite using /fav <character_id>."""
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

    # check if character is present in user's collection
    character = next((c for c in user['characters'] if c.get('id') == character_id), None)
    if not character:
        await update.message.reply_text(to_small_caps("That character is not in your collection."))
        return

    # add to favorites (use $addToSet to avoid duplicates)
    try:
        await user_collection.update_one({'id': user_id}, {'$addToSet': {'favorites': character_id}})
        await update.message.reply_text(to_small_caps(f'Character {character.get("name")} has been added to your favorites.'))
    except Exception:
        LOGGER.exception("Failed to set favorite character")
        await update.message.reply_text(to_small_caps("Failed to mark favorite. Please try again later."))

def main() -> None:
    """Run the bot - register handlers and start polling."""
    # Register commands
    # Keep block=False to allow concurrency where Application was created with appropriate executor
    application.add_handler(CommandHandler(["guess", "protecc", "collect", "grab", "hunt"], guess, block=False))
    application.add_handler(CommandHandler("fav", fav, block=False))
    application.add_handler(MessageHandler(filters.ALL, message_counter, block=False))

    # Start polling (drop pending updates by default)
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    shivuu.start()
    LOGGER.info("Sᴇɴᴘᴀɪ Wᴀɪғᴜ Bᴏᴛ ɪs Bᴀᴄᴋ Bᴀʙᴇ")
    main()
