import html
import random
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, CallbackContext, CallbackQueryHandler

from shivu import (
    application, VIDEO_URL, user_collection, top_global_groups_collection,
    group_user_totals_collection
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
        's': 's', 't': 'ᴛ', 'u': 'ᴜ', 'v': 'ᴠ', 'w': 'ᴡ', 'x': 'x',
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
    caption = "📊 <b>ʟᴇᴀᴅᴇʀʙᴏᴀʀᴅ ᴍᴇɴᴜ</b>\n\nᴄʜᴏᴏᴇ ᴀ ʀᴀɴᴋɪɴɢ ᴛᴏ ᴠɪᴇᴡ:"
    caption = "📊 <b>ʟᴇᴀᴅᴇʀʙᴏᴀʀᴅ ᴍᴇɴᴜ</b>\n\nᴄʜᴏᴏꜱᴇ ᴀ ʀᴀɴᴋɪɴɢ ᴛᴏ ᴠɪᴇᴡ:"

    await update.message.reply_video(
        video=video_url,
        caption=caption,
        parse_mode='HTML',
        reply_markup=reply_markup
    )


async def show_char_top() -> str:
    """sʜᴏᴡ ᴛᴏᴘ 10 ᴜsᴇʀs ʙʏ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴄᴏᴜɴᴛ."""
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


async def show_coin_top() -> str:
    """sʜᴏᴡ ᴛᴏᴘ 10 ᴜsᴇʀs ʙʏ ᴄᴏɪɴ ʙᴀʟᴀɴᴄᴇ."""
    # Get database instance (assuming it's available in context)
    db: AsyncIOMotorDatabase = user_collection.database
    user_balance_collection = db.get_collection('user_balance')

    # Aggregate to get top 10 users by balance
    cursor = user_balance_collection.aggregate([
        {"$sort": {"balance": -1}},
        {"$limit": 10}
    ])
    coin_data = await cursor.to_list(length=10)

    message = "💰 <b>ᴛᴏᴘ 10 ʀɪᴄʜᴇsᴛ ᴜsᴇʀs</b>\n\n"

    for i, coin_user in enumerate(coin_data, start=1):
        user_id = coin_user['user_id']
        balance = coin_user.get('balance', 0)

        # Fetch user details from user_collection
        user_data = await user_collection.find_one({"id": user_id})

        if user_data:
            username = user_data.get('username', '')
            first_name = html.escape(user_data.get('first_name', 'Unknown'))
            display_name = to_small_caps(first_name)

            if len(display_name) > 15:
                display_name = display_name[:15] + '...'

            if username:
                message += f'{i}. <a href="https://t.me/{username}"><b>{display_name}</b></a> ➾ <b>{balance} coins</b>\n'
            else:
                message += f'{i}. <b>{display_name}</b> ➾ <b>{balance} coins</b>\n'
        else:
            # Fallback if user not found
            display_name = to_small_caps(f"User {user_id}")
            message += f'{i}. <b>{display_name}</b> ➾ <b>{balance} coins</b>\n'

    return message


async def show_group_top() -> str:
    """sʜᴏᴡ ᴛᴏᴘ 10 ɢʀᴏᴜᴘs ʙʏ ᴄʜᴀʀᴀᴄᴛᴇʀ ɢᴜᴇssᴇs."""
    cursor = top_global_groups_collection.aggregate([
        {"$project": {"group_name": 1, "count": 1}},
        {"$sort": {"count": -1}},
        {"$limit": 10}
    ])
    leaderboard_data = await cursor.to_list(length=10)

    message = "👥 <b>ᴛᴏᴘ 10 ɢʀᴏᴜᴘs ʙʏ ᴄʜᴀʀᴀᴄᴛᴇʀ ɢᴜᴇssᴇs.</b>\n\n"

    for i, group in enumerate(leaderboard_data, start=1):
        group_name = html.escape(group.get('group_name', 'Unknown'))
        display_name = to_small_caps(group_name)

        if len(display_name) > 20:
            display_name = display_name[:20] + '...'

        count = group['count']
        message += f'{i}. <b>{display_name}</b> ➾ <b>{count}</b>\n'

    return message


async def show_group_user_top(chat_id: Optional[int] = None) -> str:
    """sʜᴏᴡ ᴛᴏᴘ 10 ᴜsᴇʀs ɪɴ ᴄᴜʀʀᴇɴᴛ ɢʀᴏᴜᴘ ᴏʀ ɢʟᴏʙᴀʟ ᴛᴏᴛᴀʟ ɢʀᴀʙs."""
    if chat_id:
        # Show top users in current group
        cursor = group_user_totals_collection.aggregate([
            {"$match": {"group_id": chat_id}},
            {"$project": {"username": 1, "first_name": 1, "character_count": "$count"}},
            {"$sort": {"character_count": -1}},
            {"$limit": 10}
        ])
        leaderboard_data = await cursor.to_list(length=10)

        message = "⏳ <b>ᴛᴏᴘ 10 ᴜsᴇʀs ɪɴ ᴛʜɪs ɢʀᴏᴜᴘ</b>\n\n"
    else:
        # Fallback: Show global user totals (from user_collection)
        cursor = user_collection.aggregate([
            {"$project": {
                "username": 1,
                "first_name": 1,
                "character_count": {"$size": "$characters"}
            }},
            {"$sort": {"character_count": -1}},
            {"$limit": 10}
        ])
        leaderboard_data = await cursor.to_list(length=10)

        message = "⏳ <b>ᴛᴏᴘ 10 ᴜsᴇʀs (ɢʟᴏʙᴀʟ ɢʀᴀʙs)</b>\n\n"

    for i, user in enumerate(leaderboard_data, start=1):
        username = user.get('username', '')
        first_name = html.escape(user.get('first_name', 'Unknown'))
        display_name = to_small_caps(first_name)

        if len(display_name) > 15:
            display_name = display_name[:15] + '...'

        character_count = user.get('character_count', user.get('count', 0))

        if username:
            message += f'{i}. <a href="https://t.me/{username}"><b>{display_name}</b></a> ➾ <b>{character_count}</b>\n'
        else:
            message += f'{i}. <b>{display_name}</b> ➾ <b>{character_count}</b>\n'

    return message


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
    back_keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="leaderboard_main")]]

    if data == "leaderboard_main":
        # Return to main menu
        caption = "📊 <b>Leaderboard Menu</b>\n\nChoose a ranking to view:"
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
        # Determine if in group or private chat
        chat_type = query.message.chat.type
        if chat_type in ['group', 'supergroup']:
            message = await show_group_user_top(chat_id)
        else:
            message = await show_group_user_top(None)  # Use global fallback
        reply_markup = InlineKeyboardMarkup(back_keyboard)
        await query.edit_message_caption(caption=message, parse_mode='HTML', reply_markup=reply_markup)


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