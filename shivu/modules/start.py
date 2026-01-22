import random
from html import escape 
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, CallbackQueryHandler, CommandHandler
from pymongo.results import UpdateResult

from shivu import application, PHOTO_URL, SUPPORT_CHAT, UPDATE_CHAT, BOT_USERNAME, db, GROUP_ID
from shivu import pm_users as collection

def small_caps(text: str) -> str:
    mapping = {
        'a': 'ᴀ', 'b': 'ʙ', 'c': 'ᴄ', 'd': 'ᴅ', 'e': 'ᴇ', 'f': 'ғ', 'g': 'ɢ',
        'h': 'ʜ', 'i': 'ɪ', 'j': 'ᴊ', 'k': 'ᴋ', 'l': 'ʟ', 'm': 'ᴍ', 'n': 'ɴ',
        'o': 'ᴏ', 'p': 'ᴘ', 'q': 'ǫ', 'r': 'ʀ', 's': 'ꜱ', 't': 'ᴛ', 'u': 'ᴜ',
        'v': 'ᴠ', 'w': 'ᴡ', 'x': 'x', 'y': 'ʏ', 'z': 'ᴢ',
        'A': 'ᴀ', 'B': 'ʙ', 'C': 'ᴄ', 'D': 'ᴅ', 'E': 'ᴇ', 'F': 'ғ', 'G': 'ɢ',
        'H': 'ʜ', 'I': 'ɪ', 'J': 'ᴊ', 'K': 'ᴋ', 'L': 'ʟ', 'M': 'ᴍ', 'N': 'ɴ',
        'O': 'ᴏ', 'P': 'ᴘ', 'Q': 'ǫ', 'R': 'ʀ', 'S': 'ꜱ', 'T': 'ᴛ', 'U': 'ᴜ',
        'V': 'ᴠ', 'W': 'ᴡ', 'X': 'X', 'Y': 'ʏ', 'Z': 'ᴢ',
        '0': '𝟶', '1': '𝟷', '2': '𝟸', '3': '𝟹', '4': '𝟺', '5': '𝟻',
        '6': '𝟼', '7': '𝟽', '8': '𝟾', '9': '𝟿'
    }
    return ''.join(mapping.get(ch, ch) for ch in text)

def get_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("ᴀᴅᴅ ᴍᴇ ʙᴀʙʏ", url=f'http://t.me/{BOT_USERNAME}?startgroup=new')],
        [
            InlineKeyboardButton("ꜱᴜᴘᴘᴏʀᴛ", url=f'https://t.me/{SUPPORT_CHAT}'),
            InlineKeyboardButton("ᴜᴘᴅᴀᴛᴇꜱ", url=f'https://t.me/{UPDATE_CHAT}')
        ],
        [InlineKeyboardButton("ʜᴇʟᴘ", callback_data='help')]
    ]
    return InlineKeyboardMarkup(keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    user_id = user.id
    first_name = user.first_name
    username = user.username

    pm_text = f"""
<b>{small_caps('welcome to senpai waifu bot')}</b>

<i>an elite character catcher bot designed for ultimate collectors</i>
"""

    start_text = f"""
<b>{small_caps('senpai waifu bot')} is alive</b>

<i>connect with me in private for exclusive features</i>
"""

    # Check if it's a private chat or group
    if update.effective_chat.type == "private":
        # Store user in database
        await collection.update_one(
            {'id': user_id},
            {"$set": {'id': user_id, 'first_name': first_name, 'username': username}},
            upsert=True
        )
        
        # Send photo with welcome message
        await update.message.reply_photo(
            photo=PHOTO_URL,
            caption=pm_text,
            parse_mode='HTML',
            reply_markup=get_keyboard()
        )
    else:
        # Group chat response
        await update.message.reply_text(
            text=start_text,
            parse_mode='HTML',
            reply_markup=get_keyboard()
        )

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    help_text = f"""
<b>{small_caps('senpai waifu bot help guide')}</b>

<b>game commands</b>
<code>/guess</code> - catch a spawned character (group only)
<code>/harem</code> - view your collection
<code>/fav</code> - add characters to favorites
<code>/trade</code> - trade characters with others

<b>utility commands</b>
<code>/gift</code> - gift characters to users (groups)
<code>/changetime</code> - change spawn time (group admins)

<b>statistics commands</b>
<code>/top</code> - top users globally
<code>/ctop</code> - top users in this chat
<code>/topgroups</code> - top active groups
"""

    welcome_text = f"""
<b>{small_caps('welcome to senpai waifu bot')}</b>

<i>an elite character catcher bot designed for ultimate collectors</i>
"""

    if query.data == 'help':
        await query.edit_message_caption(
            caption=help_text,
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ʙᴀᴄᴋ", callback_data='back')]])
        )
    elif query.data == 'back':
        await query.edit_message_caption(
            caption=welcome_text,
            parse_mode='HTML',
            reply_markup=get_keyboard()
        )

# Add handlers
application.add_handler(CallbackQueryHandler(button, pattern='^help$|^back$'))
start_handler = CommandHandler('start', start)
application.add_handler(start_handler)