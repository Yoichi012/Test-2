import html
import random
from typing import Optional
from datetime import datetime
import pytz  # For IST timezone

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, CallbackContext, CallbackQueryHandler

from shivu import (
    application, VIDEO_URL, user_collection, top_global_groups_collection,
    group_user_totals_collection, LOGGER
)
from motor.motor_asyncio import AsyncIOMotorDatabase


def to_small_caps(text: str) -> str:
    """Convert text to small caps unicode characters."""
    if not text:
        return ""

    # Define mapping for lowercase letters to small caps
    small_caps_map = {
        'a': 'ᴀ', 'b': 'ʙ', 'c': 'ᴄ', 'd': 'ᴅ', 'e': 'ᴇ', 'f': 'ꜰ',
        'g': 'ɢ', 'h': 'ʜ', 'i': 'ɪ', 'j': 'ᴊ', 'k': 'ᴋ', 'l': 'ʟ',
        'm': 'ᴍ', 'n': 'ɴ', 'o': 'ᴏ', 'p': 'ᴘ', 'q': 'ǫ', 'r': 'ʀ',
        's': 'ꜱ', 't': 'ᴛ', 'u': 'ᴜ', 'v': 'ᴠ', 'w': 'ᴡ', 'x': 'x',
        'y': 'ʏ', 'z': 'ᴢ'
    }

    # Convert the text
    result = []
    for char in text:
        if char.lower() in small_caps_map:
            # Preserve original case by checking if uppercase
            if char.isupper():
                result.append(small_caps_map[char.lower()].upper())
            else:
                result.append(small_caps_map[char])
        else:
            result.append(char)

    return ''.join(result)


# ============================================================================
# IST TIMEZONE HELPER FUNCTIONS
# ============================================================================

def get_ist_date() -> str:
    """Get today's date in IST timezone (Asia/Kolkata)."""
    ist_tz = pytz.timezone('Asia/Kolkata')
    ist_now = datetime.now(ist_tz)
    return ist_now.strftime("%Y-%m-%d")


def get_ist_datetime() -> datetime:
    """Get current datetime in IST timezone."""
    ist_tz = pytz.timezone('Asia/Kolkata')
    return datetime.now(ist_tz)


# ============================================================================
# DAILY COLLECTIONS (IST-based)
# ============================================================================

# Use the same database instance as user_collection
_daily_db = user_collection.database

# Create new collections for daily tracking (IST-based)
daily_user_guesses_collection = _daily_db.get_collection('daily_user_guesses')
daily_group_guesses_collection = _daily_db.get_collection('daily_group_guesses')


# ============================================================================
# ATOMIC UPDATE FUNCTIONS (to be called after correct guess)
# ============================================================================

async def update_daily_user_guess(user_id: int, username: str = "", first_name: str = "") -> None:
    """
    Increment daily guess count for a user.
    Call this AFTER a correct guess succeeds in existing logic.
    """
    try:
        today = get_ist_date()

        # Safely handle None values
        safe_username = username if username else ""
        safe_first_name = first_name if first_name else "Unknown"

        await daily_user_guesses_collection.update_one(
            {
                "date": today,
                "user_id": user_id
            },
            {
                "$inc": {"count": 1},
                "$set": {
                    "username": safe_username,
                    "first_name": safe_first_name,
                    "last_updated": get_ist_datetime()
                },
                "$setOnInsert": {
                    "date": today,
                    "user_id": user_id
                }
            },
            upsert=True
        )
        LOGGER.info(f"✅ Daily user guess updated: user_id={user_id}, date={today}")
    except Exception as e:
        LOGGER.error(f"❌ Error updating daily user guess for user_id {user_id}: {e}")


async def update_daily_group_guess(group_id: int, group_name: str = "") -> None:
    """
    Increment daily guess count for a group.
    Call this AFTER a correct guess succeeds in existing logic.
    """
    try:
        today = get_ist_date()

        # Safely handle None values
        safe_group_name = group_name if group_name else "Unknown Group"

        await daily_group_guesses_collection.update_one(
            {
                "date": today,
                "group_id": group_id
            },
            {
                "$inc": {"count": 1},
                "$set": {
                    "group_name": safe_group_name,
                    "last_updated": get_ist_datetime()
                },
                "$setOnInsert": {
                    "date": today,
                    "group_id": group_id
                }
            },
            upsert=True
        )
        LOGGER.info(f"✅ Daily group guess updated: group_id={group_id}, date={today}")
    except Exception as e:
        LOGGER.error(f"❌ Error updating daily group guess for group_id {group_id}: {e}")


# ============================================================================
# LEADERBOARD DISPLAY FUNCTIONS
# ============================================================================

async def leaderboard_entry(update: Update, context: CallbackContext) -> None:
    """Main leaderboard entry point with inline buttons."""
    keyboard = [
        [
            InlineKeyboardButton("💠 ᴛᴏᴘ ᴄᴏʟʟᴇᴄᴛᴏʀs", callback_data="leaderboard_char"),
            InlineKeyboardButton("💸 ᴛᴏᴘ ʙᴀʟᴀɴᴄᴇ", callback_data="leaderboard_coin")
        ],
        [
            InlineKeyboardButton("⚡ ɢʀᴏᴜᴘ ᴛᴏᴘ", callback_data="leaderboard_group"),
            InlineKeyboardButton("🍃 ᴛᴏᴘ ᴜsᴇʀs", callback_data="leaderboard_group_user")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    video_url = random.choice(VIDEO_URL)
    caption = "📊 <b>ʟᴇᴀᴅᴇʀʙᴏᴀʀᴅ ᴍᴇɴᴜ</b>\n\nᴄʜᴏᴏꜱᴇ ᴀ ʀᴀɴᴋɪɴɢ ᴛᴏ ᴠɪᴇᴡ:"

    await update.message.reply_video(
        video=video_url,
        caption=caption,
        parse_mode='HTML',
        reply_markup=reply_markup
    )


async def show_char_top() -> str:
    """sʜᴏᴡ ᴛᴏᴘ 10 ᴜsᴇʀs ʙʏ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴄᴏᴜɴᴛ."""
    try:
        cursor = user_collection.aggregate([
            {
                "$project": {
                    "username": 1,
                    "first_name": 1,
                    "character_count": {"$size": "$characters"}
                }
            },
            {"$sort": {"character_count": -1}},
            {"$limit": 10}
        ])
        leaderboard_data = await cursor.to_list(length=10)

        message = "🏆 <b>ᴛᴏᴘ 10 ᴜsᴇʀs ᴡɪᴛʜ ᴍᴏsᴛ ᴄʜᴀʀᴀᴄᴛᴇʀs</b>\n\n"

        if not leaderboard_data:
            return message + "ɴᴏ ᴅᴀᴛᴀ ᴀᴠᴀɪʟᴀʙʟᴇ ʏᴇᴛ!"

        for i, user in enumerate(leaderboard_data, start=1):
            username = user.get('username', '')
            first_name = html.escape(user.get('first_name', 'Unknown'))

            # Convert to small caps
            display_name = to_small_caps(first_name)

            if len(display_name) > 15:
                display_name = display_name[:15] + '...'

            character_count = user['character_count']

            if username:
                message += f'{i}. <a href="https://t.me/{username}"><b>{display_name}</b></a> ➾ <b>{character_count}</b>\n'
            else:
                message += f'{i}. <b>{display_name}</b> ➾ <b>{character_count}</b>\n'

        return message
    except Exception as e:
        LOGGER.exception(f"Error in show_char_top: {e}")
        return "❌ <b>ᴇʀʀᴏʀ ʟᴏᴀᴅɪɴɢ ʟᴇᴀᴅᴇʀʙᴏᴀʀᴅ</b>"


async def show_coin_top() -> str:
    """
    ✅ CORRECT: Shows top 10 users by coin balance from user_collection.
    This function reads from the SAME collection where main.py now stores balance.
    """
    try:
        # ✅ CORRECT: Fetch top 10 users by balance from user_collection
        cursor = user_collection.aggregate([
            {"$sort": {"balance": -1}},
            {"$limit": 10}
        ])
        coin_data = await cursor.to_list(length=10)

        message = "💰 <b>ᴛᴏᴘ 10 ʀɪᴄʜᴇsᴛ ᴜsᴇʀs</b>\n\n"

        if not coin_data:
            return message + "ɴᴏ ᴅᴀᴛᴀ ᴀᴠᴀɪʟᴀʙʟᴇ ʏᴇᴛ!"

        for i, user_data in enumerate(coin_data, start=1):
            # ✅ CORRECT: Get balance field from user document
            balance = user_data.get('balance', 0)
            username = user_data.get('username', '')
            first_name = html.escape(user_data.get('first_name', 'Unknown'))
            display_name = to_small_caps(first_name)

            if len(display_name) > 15:
                display_name = display_name[:15] + '...'

            if username:
                message += f'{i}. <a href="https://t.me/{username}"><b>{display_name}</b></a> ➾ <b>{balance} coins</b>\n'
            else:
                message += f'{i}. <b>{display_name}</b> ➾ <b>{balance} coins</b>\n'

        return message
    except Exception as e:
        LOGGER.exception(f"Error in show_coin_top: {e}")
        return "❌ <b>ᴇʀʀᴏʀ ʟᴏᴀᴅɪɴɢ ʟᴇᴀᴅᴇʀʙᴏᴀʀᴅ</b>"


async def show_group_top() -> str:
    """sʜᴏᴡ ᴛᴏᴘ 10 ɢʀᴏᴜᴘs ʙʏ ᴄʜᴀʀᴀᴄᴛᴇʀ ɢᴜᴇssᴇs (TODAY - IST)."""
    try:
        today = get_ist_date()

        # Query daily group guesses for today
        cursor = daily_group_guesses_collection.aggregate([
            {"$match": {"date": today}},
            {"$sort": {"count": -1}},
            {"$limit": 10}
        ])

        daily_data = await cursor.to_list(length=10)

        if not daily_data:
            return f"👥 <b>ᴛᴏᴘ 10 ɢʀᴏᴜᴘs ʙʏ ᴄʜᴀʀᴀᴄᴛᴇʀ ɢᴜᴇssᴇs (ᴛᴏᴅᴀʏ)</b>\n📅 <i>{today}</i>\n\nɴᴏ ɢᴜᴇssᴇs ᴛᴏᴅᴀʏ ʏᴇᴛ!"

        message = f"👥 <b>ᴛᴏᴘ 10 ɢʀᴏᴜᴘs ʙʏ ᴄʜᴀʀᴀᴄᴛᴇʀ ɢᴜᴇssᴇs (ᴛᴏᴅᴀʏ)</b>\n📅 <i>{today}</i>\n\n"

        for i, group in enumerate(daily_data, start=1):
            group_name = html.escape(group.get('group_name', 'Unknown'))
            display_name = to_small_caps(group_name)

            if len(display_name) > 20:
                display_name = display_name[:20] + '...'

            count = group.get('count', 0)
            message += f'{i}. <b>{display_name}</b> ➾ <b>{count}</b>\n'

        return message
    except Exception as e:
        LOGGER.exception(f"Error in show_group_top: {e}")
        return "❌ <b>ᴇʀʀᴏʀ ʟᴏᴀᴅɪɴɢ ʟᴇᴀᴅᴇʀʙᴏᴀʀᴅ</b>"


async def show_group_user_top(chat_id: Optional[int] = None) -> str:
    """sʜᴏᴡ ᴛᴏᴘ 10 ᴜsᴇʀs ʙʏ ᴄᴏʀʀᴇᴄᴛ ɢᴜᴇssᴇs (TODAY - IST)."""
    try:
        today = get_ist_date()

        # Query daily user guesses for today
        cursor = daily_user_guesses_collection.aggregate([
            {"$match": {"date": today}},
            {"$sort": {"count": -1}},
            {"$limit": 10}
        ])

        daily_data = await cursor.to_list(length=10)

        if not daily_data:
            return f"⏳ <b>ᴛᴏᴘ 10 ᴜsᴇʀs ʙʏ ᴄᴏʀʀᴇᴄᴛ ɢᴜᴇssᴇs (ᴛᴏᴅᴀʏ)</b>\n📅 <i>{today}</i>\n\nɴᴏ ɢᴜᴇssᴇs ᴛᴏᴅᴀʏ ʏᴇᴛ!"

        message = f"⏳ <b>ᴛᴏᴘ 10 ᴜsᴇʀs ʙʏ ᴄᴏʀʀᴇᴄᴛ ɢᴜᴇssᴇs (ᴛᴏᴅᴀʏ)</b>\n📅 <i>{today}</i>\n\n"

        for i, user in enumerate(daily_data, start=1):
            username = user.get('username', '')
            first_name = html.escape(user.get('first_name', 'Unknown'))
            display_name = to_small_caps(first_name)

            if len(display_name) > 15:
                display_name = display_name[:15] + '...'

            count = user.get('count', 0)

            if username:
                message += f'{i}. <a href="https://t.me/{username}"><b>{display_name}</b></a> ➾ <b>{count}</b>\n'
            else:
                message += f'{i}. <b>{display_name}</b> ➾ <b>{count}</b>\n'

        return message
    except Exception as e:
        LOGGER.exception(f"Error in show_group_user_top: {e}")
        return "❌ <b>ᴇʀʀᴏʀ ʟᴏᴀᴅɪɴɢ ʟᴇᴀᴅᴇʀʙᴏᴀʀᴅ</b>"


async def leaderboard_callback(update: Update, context: CallbackContext) -> None:
    """Handle callback queries from leaderboard buttons."""
    query = update.callback_query
    await query.answer()

    data = query.data
    chat_id = query.message.chat_id

    # Main menu keyboard (for back button)
    main_keyboard = [
        [
            InlineKeyboardButton("💠 ᴛᴏᴘ ᴄᴏʟʟᴇᴄᴛᴏʀs", callback_data="leaderboard_char"),
            InlineKeyboardButton("💸 ᴛᴏᴘ ʙᴀʟᴀɴᴄᴇ", callback_data="leaderboard_coin")
        ],
        [
            InlineKeyboardButton("⚡ ɢʀᴏᴜᴘ ᴛᴏᴘ", callback_data="leaderboard_group"),
            InlineKeyboardButton("🍃 ᴛᴏᴘ ᴜsᴇʀs", callback_data="leaderboard_group_user")
        ]
    ]

    # Back button keyboard for individual views
    back_keyboard = [[InlineKeyboardButton("🔙 ʙᴀᴄᴋ", callback_data="leaderboard_main")]]

    try:
        if data == "leaderboard_main":
            # Return to main menu
            caption = "📊 <b>ʟᴇᴀᴅᴇʀʙᴏᴀʀᴅ ᴍᴇɴᴜ</b>\n\nᴄʜᴏᴏꜱᴇ ᴀ ʀᴀɴᴋɪɴɢ ᴛᴏ ᴠɪᴇᴡ:"
            reply_markup = InlineKeyboardMarkup(main_keyboard)
            await query.edit_message_caption(caption=caption, parse_mode='HTML', reply_markup=reply_markup)

        elif data == "leaderboard_char":
            message = await show_char_top()
            reply_markup = InlineKeyboardMarkup(back_keyboard)
            await query.edit_message_caption(caption=message, parse_mode='HTML', reply_markup=reply_markup)

        elif data == "leaderboard_coin":
            message = await show_coin_top()
            reply_markup = InlineKeyboardMarkup(back_keyboard)
            await query.edit_message_caption(caption=message, parse_mode='HTML', reply_markup=reply_markup)

        elif data == "leaderboard_group":
            message = await show_group_top()
            reply_markup = InlineKeyboardMarkup(back_keyboard)
            await query.edit_message_caption(caption=message, parse_mode='HTML', reply_markup=reply_markup)

        elif data == "leaderboard_group_user":
            # Note: The daily user leaderboard is now GLOBAL (not per group)
            # Always show global daily user guesses regardless of chat type
            message = await show_group_user_top()
            reply_markup = InlineKeyboardMarkup(back_keyboard)
            await query.edit_message_caption(caption=message, parse_mode='HTML', reply_markup=reply_markup)
    except Exception as e:
        LOGGER.exception(f"Error in leaderboard_callback: {e}")
        await query.answer("❌ Error loading leaderboard", show_alert=True)


# Add handlers
application.add_handler(CommandHandler('leaderboard', leaderboard_entry, block=False))
application.add_handler(CallbackQueryHandler(leaderboard_callback, pattern=r'^leaderboard_.*$', block=False))

# Optional: Keep old commands for backward compatibility with redirect
async def old_command_redirect(update: Update, context: CallbackContext, command: str) -> None:
    """Redirect old commands to the new leaderboard system."""
    await leaderboard_entry(update, context)

# Add redirect handlers for old commands
application.add_handler(CommandHandler('top', lambda u, c: old_command_redirect(u, c, 'top'), block=False))
application.add_handler(CommandHandler('ctop', lambda u, c: old_command_redirect(u, c, 'ctop'), block=False))
application.add_handler(CommandHandler('TopGroups', lambda u, c: old_command_redirect(u, c, 'TopGroups'), block=False))
