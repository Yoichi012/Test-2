import random
from html import escape 
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, CallbackQueryHandler, CommandHandler
from pymongo.results import UpdateResult

from shivu import application, PHOTO_URL, SUPPORT_CHAT, UPDATE_CHAT, BOT_USERNAME, db, GROUP_ID
from shivu import pm_users as collection

# Helper function for small caps text
def small_caps(text: str) -> str:
    """Convert text to small caps style"""
    mapping = {
        'a': 'ᴀ', 'b': 'ʙ', 'c': 'ᴄ', 'd': 'ᴅ', 'e': 'ᴇ', 'f': 'ғ', 'g': 'ɢ',
        'h': 'ʜ', 'i': 'ɪ', 'j': 'ᴊ', 'k': 'ᴋ', 'l': 'ʟ', 'm': 'ᴍ', 'n': 'ɴ',
        'o': 'ᴏ', 'p': 'ᴘ', 'q': 'ǫ', 'r': 'ʀ', 's': 's', 't': 'ᴛ', 'u': 'ᴜ',
        'v': 'ᴠ', 'w': 'ᴡ', 'x': 'x', 'y': 'ʏ', 'z': 'ᴢ',
        'A': 'ᴀ', 'B': 'ʙ', 'C': 'ᴄ', 'D': 'ᴅ', 'E': 'ᴇ', 'F': 'ғ', 'G': 'ɢ',
        'H': 'ʜ', 'I': 'ɪ', 'J': 'ᴊ', 'K': 'ᴋ', 'L': 'ʟ', 'M': 'ᴍ', 'N': 'ɴ',
        'O': 'ᴏ', 'P': 'ᴘ', 'Q': 'ǫ', 'R': 'ʀ', 'S': 'S', 'T': 'ᴛ', 'U': 'ᴜ',
        'V': 'ᴠ', 'W': 'ᴡ', 'X': 'X', 'Y': 'ʏ', 'Z': 'ᴢ',
        '0': '𝟶', '1': '𝟷', '2': '𝟸', '3': '𝟹', '4': '𝟺', '5': '𝟻', 
        '6': '𝟼', '7': '𝟽', '8': '𝟾', '9': '𝟿'
    }
    return ''.join(mapping.get(ch, ch) for ch in text)

# Helper function for keyboard layout
def get_keyboard() -> InlineKeyboardMarkup:
    """Return premium keyboard layout"""
    keyboard = [
        [InlineKeyboardButton("➕ ᴀᴅᴅ ᴍᴇ", url=f'http://t.me/{BOT_USERNAME}?startgroup=new')],
        [
            InlineKeyboardButton("💬 sᴜᴘᴘᴏʀᴛ", url=f'https://t.me/{SUPPORT_CHAT}'),
            InlineKeyboardButton("📣 ᴜᴘᴅᴀᴛᴇs", url=f'https://t.me/{UPDATE_CHAT}')
        ],
        [InlineKeyboardButton("❓ ʜᴇʟᴘ", callback_data='help')]
    ]
    return InlineKeyboardMarkup(keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command with premium UI and optimized database operations"""
    user = update.effective_user
    user_id = user.id
    first_name = user.first_name
    username = user.username
    
    try:
        # Single optimized database query with upsert=True
        result: UpdateResult = await collection.update_one(
            {"_id": user_id},
            {
                "$set": {
                    "first_name": first_name,
                    "username": username
                },
                "$setOnInsert": {
                    "started_at": update.message.date if update.message else None
                }
            },
            upsert=True
        )
        
        # Check if user was newly created
        if result.upserted_id is not None:
            await context.bot.send_message(
                chat_id=GROUP_ID,
                text=f"✨ <b>ɴᴇᴡ ᴜsᴇʀ ᴀʀʀɪᴠᴇᴅ!</b>\n"
                     f"👤 <a href='tg://user?id={user_id}'>{escape(first_name or 'User')}</a>\n"
                     f"🆔 <code>{user_id}</code>",
                parse_mode='HTML'
            )
    
    except Exception as e:
        print(f"Database error in /start: {e}")
    
    photo_url = random.choice(PHOTO_URL)
    keyboard = get_keyboard()
    
    if update.effective_chat.type == "private":
        caption = f"""
<b>✨ {small_caps('welcome to waifu catcher premium')} ✨</b>

<i>ɪ'ᴍ ᴀɴ ᴇʟɪᴛᴇ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴄᴀᴛᴄʜᴇʀ ʙᴏᴛ ᴅᴇsɪɢɴᴇᴅ ғᴏʀ ᴜʟᴛɪᴍᴀᴛᴇ ᴄᴏʟʟᴇᴄᴛᴏʀs! 🎴</i>

<b>🎮 ʜᴏᴡ ᴛᴏ ᴘʟᴀʏ:</b>
1️⃣ <b>ᴀᴅᴅ ᴍᴇ</b> ᴛᴏ ʏᴏᴜʀ ɢʀᴏᴜᴘ
2️⃣ ɪ'ʟʟ sᴘᴀᴡɴ <b>ʀᴀʀᴇ ᴄʜᴀʀᴀᴄᴛᴇʀs</b> ᴇᴠᴇʀʏ 𝟷𝟶𝟶 ᴍᴇssᴀɢᴇs
3️⃣ ᴜsᴇ <code>/guess</code> ᴛᴏ ᴄᴀᴛᴄʜ ᴛʜᴇᴍ
4️⃣ ʙᴜɪʟᴅ ʏᴏᴜʀ <b>ᴜʟᴛɪᴍᴀᴛᴇ ʜᴀʀᴇᴍ</b> ᴡɪᴛʜ <code>/harem</code>

<b>🌟 ᴘʀᴇᴍɪᴜᴍ ғᴇᴀᴛᴜʀᴇs:</b>
• 🎴 <b>Exclusive characters</b>
• ⚡ <b>Instant collection updates</b>
• 📊 <b>Advanced statistics</b>
• 🔄 <b>Real-time trading system</b>
"""
        
        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=photo_url,
            caption=caption,
            reply_markup=keyboard,
            parse_mode='HTML'
        )
    
    else:
        caption = f"""
<b>🎴 {small_caps('waifu catcher premium')} ɪs ᴀʟɪᴠᴇ! ⚡</b>

<i>ᴄᴏɴɴᴇᴄᴛ ᴡɪᴛʜ ᴍᴇ ɪɴ ᴘʀɪᴠᴀᴛᴇ ғᴏʀ ᴇxᴄʟᴜsɪᴠᴇ ғᴇᴀᴛᴜʀᴇs ᴀɴᴅ ɢᴀᴍᴇᴘʟᴀʏ ɢᴜɪᴅᴇ! ✨</i>

<b>⚡ ǫᴜɪᴄᴋ sᴛᴀʀᴛ:</b>
• ᴀᴅᴅ ᴍᴇ ᴛᴏ ʏᴏᴜʀ ɢʀᴏᴜᴘ
• ɪ'ʟʟ sᴘᴀᴡɴ ʀᴀʀᴇ ᴄʜᴀʀᴀᴄᴛᴇʀs
• ᴜsᴇ <code>/guess</code> ᴛᴏ ᴄᴀᴛᴄʜ ᴛʜᴇᴍ
"""
        
        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=photo_url,
            caption=caption,
            reply_markup=keyboard,
            parse_mode='HTML'
        )

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle button callbacks with premium UI"""
    query = update.callback_query
    await query.answer()
    
    if query.data == 'help':
        help_text = f"""
<b>🆘 {small_caps('premium help guide')} 🆘</b>

<b>🎮 ɢᴀᴍᴇ ᴄᴏᴍᴍᴀɴᴅs:</b>
<code>/guess</code> - ᴄᴀᴛᴄʜ ᴀ sᴘᴀᴡɴᴇᴅ ᴄʜᴀʀᴀᴄᴛᴇʀ (ɢʀᴏᴜᴘ ᴏɴʟʏ)
<code>/harem</code> - ᴠɪᴇᴡ ʏᴏᴜʀ ᴄᴏʟʟᴇᴄᴛɪᴏɴ
<code>/fav</code> - ᴀᴅᴅ ᴄʜᴀʀᴀᴄᴛᴇʀs ᴛᴏ ғᴀᴠᴏʀɪᴛᴇs
<code>/trade</code> - ᴛʀᴀᴅᴇ ᴄʜᴀʀᴀᴄᴛᴇʀs ᴡɪᴛʜ ᴏᴛʜᴇʀs

<b>⚡ ᴜᴛɪʟɪᴛʏ ᴄᴏᴍᴍᴀɴᴅs:</b>
<code>/gift</code> - ɢɪғᴛ ᴄʜᴀʀᴀᴄᴛᴇʀs ᴛᴏ ᴜsᴇʀs (ɢʀᴏᴜᴘs)
<code>/changetime</code> - ᴄʜᴀɴɢᴇ sᴘᴀᴡɴ ᴛɪᴍᴇ (ɢʀᴏᴜᴘ ᴀᴅᴍɪɴs)

<b>📊 sᴛᴀᴛɪsᴛɪᴄs ᴄᴏᴍᴍᴀɴᴅs:</b>
<code>/top</code> - ᴛᴏᴘ ᴜsᴇʀs ɢʟᴏʙᴀʟʟʏ
<code>/ctop</code> - ᴛᴏᴘ ᴜsᴇʀs ɪɴ ᴛʜɪs ᴄʜᴀᴛ
<code>/topgroups</code> - ᴛᴏᴘ ᴀᴄᴛɪᴠᴇ ɢʀᴏᴜᴘs

<b>💡 ᴛɪᴘ:</b> ᴜsᴇ ʙᴜᴛᴛᴏɴs ʙᴇʟᴏᴡ ғᴏʀ ǫᴜɪᴄᴋ ᴀᴄᴄᴇss ✨
"""
        
        help_keyboard = [[InlineKeyboardButton("🔙 ʙᴀᴄᴋ ᴛᴏ ᴍᴀɪɴ", callback_data='back')]]
        reply_markup = InlineKeyboardMarkup(help_keyboard)
        
        await query.edit_message_caption(
            caption=help_text,
            reply_markup=reply_markup,
            parse_mode='HTML'
        )
    
    elif query.data == 'back':
        caption = f"""
<b>✨ {small_caps('welcome to waifu catcher premium')} ✨</b>

<i>ɪ'ᴍ ᴀɴ ᴇʟɪᴛᴇ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴄᴀᴛᴄʜᴇʀ ʙᴏᴛ ᴅᴇsɪɢɴᴇᴅ ғᴏʀ ᴜʟᴛɪᴍᴀᴛᴇ ᴄᴏʟʟᴇᴄᴛᴏʀs! 🎴</i>

<b>🎮 ʜᴏᴡ ᴛᴏ ᴘʟᴀʏ:</b>
1️⃣ <b>ᴀᴅᴅ ᴍᴇ</b> ᴛᴏ ʏᴏᴜʀ ɢʀᴏᴜᴘ
2️⃣ ɪ'ʟʟ sᴘᴀᴡɴ <b>ʀᴀʀᴇ ᴄʜᴀʀᴀᴄᴛᴇʀs</b> ᴇᴠᴇʀʏ 𝟷𝟶𝟶 ᴍᴇssᴀɢᴇs
3️⃣ ᴜsᴇ <code>/guess</code> ᴛᴏ ᴄᴀᴛᴄʜ ᴛʜᴇᴍ
4️⃣ ʙᴜɪʟᴅ ʏᴏᴜʀ <b>ᴜʟᴛɪᴍᴀᴛᴇ ʜᴀʀᴇᴍ</b> ᴡɪᴛʜ <code>/harem</code>

<b>🌟 ᴘʀᴇᴍɪᴜᴍ ғᴇᴀᴛᴜʀᴇs:</b>
• 🎴 <b>Exclusive characters</b>
• ⚡ <b>Instant collection updates</b>
• 📊 <b>Advanced statistics</b>
• 🔄 <b>Real-time trading system</b>
"""
        
        keyboard = get_keyboard()
        await query.edit_message_caption(
            caption=caption,
            reply_markup=keyboard,
            parse_mode='HTML'
        )

# Register handlers
application.add_handler(CallbackQueryHandler(button, pattern='^help$|^back$'))
start_handler = CommandHandler('start', start)
application.add_handler(start_handler)