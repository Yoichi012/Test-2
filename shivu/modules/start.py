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


def get_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("✦ ᴀᴅᴅ ᴍᴇ ʙᴀʙʏ ✦", url=f'http://t.me/{BOT_USERNAME}?startgroup=new')],
        [
            InlineKeyboardButton("❖ sᴜᴘᴘᴏʀᴛ", url=f'https://t.me/{SUPPORT_CHAT}'),
            InlineKeyboardButton("❖ ᴜᴘᴅᴀᴛᴇs", url=f'https://t.me/{UPDATE_CHAT}')
        ],
        [InlineKeyboardButton("✧ ʜᴇʟᴘ", callback_data='help')]
    ]
    return InlineKeyboardMarkup(keyboard)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    user_id = user.id
    first_name = user.first_name
    username = user.username

    try:
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

        if result.upserted_id is not None:
            await context.bot.send_message(
                chat_id=GROUP_ID,
                text=f"<b>❖ ɴᴇᴡ ᴜsᴇʀ ʀᴇɢɪsᴛᴇʀᴇᴅ</b>\n"
                     f"<b>ɴᴀᴍᴇ</b> ⟡ <a href='tg://user?id={user_id}'>{escape(first_name or 'User')}</a>\n"
                     f"<b>ɪᴅ</b> ⟡ <code>{user_id}</code>",
                parse_mode='HTML'
            )

    except Exception as e:
        print(f"Database error in /start: {e}")

    photo_url = random.choice(PHOTO_URL)
    keyboard = get_keyboard()

    if update.effective_chat.type == "private":
        caption = f"""
<b>✦ {small_caps('senpai waifu bot')} ✦</b>

<i>ᴀ ʟᴜxᴜʀʏ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴄᴏʟʟᴇᴄᴛɪᴏɴ sʏsᴛᴇᴍ ᴄʀᴀғᴛᴇᴅ ғᴏʀ ᴇʟɪᴛᴇ ᴄᴏʟʟᴇᴄᴛᴏʀs</i>

<b>❖ ʜᴏᴡ ᴛᴏ sᴛᴀʀᴛ</b>
⟡ ᴀᴅᴅ ᴍᴇ ᴛᴏ ʏᴏᴜʀ ɢʀᴏᴜᴘ
⟡ ᴄʜᴀʀᴀᴄᴛᴇʀs sᴘᴀᴡɴ ᴀᴜᴛᴏᴍᴀᴛɪᴄᴀʟʟʏ
⟡ ᴜsᴇ <code>/guess</code> ᴛᴏ ᴄᴀᴘᴛᴜʀᴇ
⟡ ᴠɪᴇᴡ ʏᴏᴜʀ ᴄᴏʟʟᴇᴄᴛɪᴏɴ ᴡɪᴛʜ <code>/harem</code>

<b>❖ ᴘʀᴇᴍɪᴜᴍ sʏsᴛᴇᴍ</b>
⟢ ᴇxᴄʟᴜsɪᴠᴇ ᴄʜᴀʀᴀᴄᴛᴇʀs
⟢ ʀᴇᴀʟ-ᴛɪᴍᴇ ᴜᴘᴅᴀᴛᴇs
⟢ ᴀᴅᴠᴀɴᴄᴇᴅ sᴛᴀᴛɪsᴛɪᴄs
⟢ sᴇᴄᴜʀᴇ ᴛʀᴀᴅɪɴɢ
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
<b>✦ {small_caps('senpai waifu bot')} ✦</b>

<i>ᴄᴏɴɴᴇᴄᴛ ɪɴ ᴘʀɪᴠᴀᴛᴇ ғᴏʀ ғᴜʟʟ ᴀᴄᴄᴇss ᴀɴᴅ ɢᴜɪᴅᴇ</i>

<b>❖ ǫᴜɪᴄᴋ ᴏᴠᴇʀᴠɪᴇᴡ</b>
⟡ ᴀᴅᴅ ᴍᴇ ᴛᴏ ʏᴏᴜʀ ɢʀᴏᴜᴘ
⟡ ᴄʜᴀʀᴀᴄᴛᴇʀs sᴘᴀᴡɴ ᴀᴜᴛᴏᴍᴀᴛɪᴄᴀʟʟʏ
⟡ ᴜsᴇ <code>/guess</code> ᴛᴏ ᴄᴀᴘᴛᴜʀᴇ
"""
        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=photo_url,
            caption=caption,
            reply_markup=keyboard,
            parse_mode='HTML'
        )


async def button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if query.data == 'help':
        help_text = f"""
<b>✦ {small_caps('help guide')} ✦</b>

<b>❖ ɢᴀᴍᴇ ᴄᴏᴍᴍᴀɴᴅs</b>
<code>/guess</code> ⟡ ᴄᴀᴘᴛᴜʀᴇ ᴄʜᴀʀᴀᴄᴛᴇʀ
<code>/harem</code> ⟡ ᴠɪᴇᴡ ᴄᴏʟʟᴇᴄᴛɪᴏɴ
<code>/fav</code> ⟡ sᴀᴠᴇ ғᴀᴠᴏʀɪᴛᴇs
<code>/trade</code> ⟡ ᴛʀᴀᴅᴇ ᴄʜᴀʀᴀᴄᴛᴇʀs

<b>❖ ᴜᴛɪʟɪᴛʏ</b>
<code>/gift</code> ⟡ ɢɪғᴛ ᴄʜᴀʀᴀᴄᴛᴇʀs
<code>/changetime</code> ⟡ sᴘᴀᴡɴ sᴇᴛᴛɪɴɢ

<b>❖ sᴛᴀᴛɪsᴛɪᴄs</b>
<code>/top</code> ⟡ ɢʟᴏʙᴀʟ ʀᴀɴᴋɪɴɢ
<code>/ctop</code> ⟡ ᴄʜᴀᴛ ʀᴀɴᴋɪɴɢ
<code>/topgroups</code> ⟡ ɢʀᴏᴜᴘ ʀᴀɴᴋɪɴɢ
"""
        reply_markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton("⟡ ʙᴀᴄᴋ ᴛᴏ ᴍᴀɪɴ", callback_data='back')]]
        )

        await query.edit_message_caption(
            caption=help_text,
            reply_markup=reply_markup,
            parse_mode='HTML'
        )

    elif query.data == 'back':
        caption = f"""
<b>✦ {small_caps('senpai waifu bot')} ✦</b>

<i>ᴀ ʟᴜxᴜʀʏ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴄᴏʟʟᴇᴄᴛɪᴏɴ sʏsᴛᴇᴍ ᴄʀᴀғᴛᴇᴅ ғᴏʀ ᴇʟɪᴛᴇ ᴄᴏʟʟᴇᴄᴛᴏʀs</i>
"""
        await query.edit_message_caption(
            caption=caption,
            reply_markup=get_keyboard(),
            parse_mode='HTML'
        )


application.add_handler(CallbackQueryHandler(button, pattern='^help$|^back$'))
application.add_handler(CommandHandler('start', start))