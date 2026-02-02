import urllib.request
import logging
from pymongo import ReturnDocument
from telegram import Update, InputMediaPhoto
from telegram.ext import CommandHandler, CallbackContext
from telegram.error import TelegramError

from shivu import application, sudo_users, collection, db, CHARA_CHANNEL_ID, SUPPORT_CHAT

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

RARITY_MAP = {
    1: (1, "⚪ ᴄᴏᴍᴍᴏɴ"),
    2: (2, "🔵 ʀᴀʀᴇ"),
    3: (3, "🟡 ʟᴇɢᴇɴᴅᴀʀʏ"),
    4: (4, "💮 ꜱᴘᴇᴄɪᴀʟ"),
    5: (5, "👹 ᴀɴᴄɪᴇɴᴛ"),
    6: (6, "🎐 ᴄᴇʟᴇꜱᴛɪᴀʟ"),
    7: (7, "🔮 ᴇᴘɪᴄ"),
    8: (8, "🪐 ᴄᴏꜱᴍɪᴄ"),
    9: (9, "⚰️ ɴɪɢʜᴛᴍᴀʀᴇ"),
    10: (10, "🌬️ ꜰʀᴏꜱᴛʙᴏʀɴ"),
    11: (11, "💝 ᴠᴀʟᴇɴᴛɪɴᴇ"),
    12: (12, "🌸 ꜱᴘʀɪɴɢ"),
    13: (13, "🏖️ ᴛʀᴏᴘɪᴄᴀʟ"),
    14: (14, "🍭 ᴋᴀᴡᴀɪɪ"),
    15: (15, "🧬 ʜʏʙʀɪᴅ"),
}

WRONG_FORMAT_TEXT = """Wrong ❌️ format...  eg. /upload Img_url muzan-kibutsuji Demon-slayer 3

img_url character-name anime-name rarity-number

use rarity number accordingly rarity Map

""" + "\n".join([f"{k}: {v[1]}" for k, v in RARITY_MAP.items()])

def is_sudo(user_id: int) -> bool:
    return str(user_id) in sudo_users

def validate_rarity(rarity_input: str) -> tuple:
    try:
        rarity_num = int(rarity_input)
        if rarity_num not in RARITY_MAP:
            error_msg = "Invalid rarity! Use numbers 1-15.\n\n" + "\n".join([f"{k}: {v[1]}" for k, v in RARITY_MAP.items()])
            return None, error_msg
        return RARITY_MAP[rarity_num], None
    except ValueError:
        error_msg = "Rarity must be a number! Use 1-15.\n\n" + "\n".join([f"{k}: {v[1]}" for k, v in RARITY_MAP.items()])
        return None, error_msg

def validate_image_url(url: str) -> bool:
    if not url:
        return False
    try:
        urllib.request.urlopen(url)
        return True
    except:
        return False

def build_caption(char_id: str, char_name: str, anime: str, rarity_display: str, uploader_id: int, uploader_name: str) -> str:
    emoji = rarity_display.split()[0]
    rarity_text = rarity_display.split()[1]
    uploader_link = f'<a href="tg://user?id={uploader_id}">{uploader_name}</a>'
    return (
        f"{char_id}: {char_name}\n"
        f"{char_name} ({anime})\n\n"
        f"{emoji} 𝙍𝘼𝙍𝙄𝙏𝙔: {rarity_text}\n\n"
        f"𝑵𝒂𝒅𝒆 𝑩𝒚 ➥ 参┊ＹＯＩＣＨＩ→ ＩＳＡＧＩ"
    )

async def get_next_sequence_number(sequence_name):
    sequence_collection = db.sequences
    sequence_document = await sequence_collection.find_one_and_update(
        {'_id': sequence_name},
        {'$inc': {'sequence_value': 1}},
        return_document=ReturnDocument.AFTER
    )
    if not sequence_document:
        await sequence_collection.insert_one({'_id': sequence_name, 'sequence_value': 0})
        return 0
    return sequence_document['sequence_value']

async def upload(update: Update, context: CallbackContext) -> None:
    if not is_sudo(update.effective_user.id):
        await update.message.reply_text('Ask My Owner...')
        return

    try:
        args = context.args
        if len(args) != 4:
            await update.message.reply_text(WRONG_FORMAT_TEXT)
            return

        img_url = args[0]
        character_name = args[1].replace('-', ' ').title()
        anime = args[2].replace('-', ' ').title()

        if not validate_image_url(img_url):
            await update.message.reply_text('Invalid URL.')
            return

        rarity_data, error_msg = validate_rarity(args[3])
        if error_msg:
            await update.message.reply_text(error_msg)
            return

        rarity_num, rarity_display = rarity_data
        char_id = str(await get_next_sequence_number('character_id')).zfill(2)

        character = {
            'img_url': img_url,
            'name': character_name,
            'anime': anime,
            'rarity': rarity_display,
            'id': char_id
        }

        caption = build_caption(
            char_id,
            character_name,
            anime,
            rarity_display,
            update.effective_user.id,
            update.effective_user.first_name
        )

        try:
            message = await context.bot.send_photo(
                chat_id=CHARA_CHANNEL_ID,
                photo=img_url,
                caption=caption,
                parse_mode='HTML'
            )
            character['message_id'] = message.message_id
            await collection.insert_one(character)
            await update.message.reply_text('CHARACTER ADDED....')
        except Exception as channel_error:
            logger.error(f"Channel upload error: {channel_error}")
            await collection.insert_one(character)
            await update.message.reply_text("Character Added but no Database Channel Found, Consider adding one.")

    except Exception as e:
        logger.error(f"Upload error: {e}")
        await update.message.reply_text(f'Character Upload Unsuccessful. Error: {str(e)}\nIf you think this is a source error, forward to: {SUPPORT_CHAT}')

async def delete(update: Update, context: CallbackContext) -> None:
    if not is_sudo(update.effective_user.id):
        await update.message.reply_text('Ask my Owner to use this Command...')
        return

    try:
        args = context.args
        if len(args) != 1:
            await update.message.reply_text('Incorrect format... Please use: /delete ID')
            return

        character = await collection.find_one_and_delete({'id': args[0]})

        if not character:
            await update.message.reply_text('Character not found.')
            return

        try:
            if 'message_id' in character:
                await context.bot.delete_message(
                    chat_id=CHARA_CHANNEL_ID,
                    message_id=character['message_id']
                )
            await update.message.reply_text('DONE')
        except TelegramError as e:
            logger.warning(f"Channel message deletion failed: {e}")
            await update.message.reply_text('Deleted Successfully from db, but character not found In Channel')

    except Exception as e:
        logger.error(f"Delete error: {e}")
        await update.message.reply_text(f'{str(e)}')

async def update(update: Update, context: CallbackContext) -> None:
    if not is_sudo(update.effective_user.id):
        await update.message.reply_text('You do not have permission to use this command.')
        return

    try:
        args = context.args
        if len(args) != 3:
            await update.message.reply_text('Incorrect format. Please use: /update id field new_value')
            return

        character = await collection.find_one({'id': args[0]})
        if not character:
            await update.message.reply_text('Character not found.')
            return

        valid_fields = ['img_url', 'name', 'anime', 'rarity']
        if args[1] not in valid_fields:
            await update.message.reply_text(f'Invalid field. Please use one of the following: {", ".join(valid_fields)}')
            return

        if args[1] in ['name', 'anime']:
            new_value = args[2].replace('-', ' ').title()
        elif args[1] == 'rarity':
            rarity_data, error_msg = validate_rarity(args[2])
            if error_msg:
                await update.message.reply_text(error_msg)
                return
            rarity_num, rarity_display = rarity_data
            new_value = rarity_display
        else:
            new_value = args[2]

        await collection.find_one_and_update({'id': args[0]}, {'$set': {args[1]: new_value}})

        updated_character = await collection.find_one({'id': args[0]})

        caption = build_caption(
            updated_character['id'],
            updated_character['name'],
            updated_character['anime'],
            updated_character['rarity'],
            update.effective_user.id,
            update.effective_user.first_name
        )

        if args[1] == 'img_url':
            try:
                if 'message_id' in character:
                    await context.bot.edit_message_media(
                        chat_id=CHARA_CHANNEL_ID,
                        message_id=character['message_id'],
                        media=InputMediaPhoto(media=new_value, caption=caption, parse_mode='HTML')
                    )
                else:
                    message = await context.bot.send_photo(
                        chat_id=CHARA_CHANNEL_ID,
                        photo=new_value,
                        caption=caption,
                        parse_mode='HTML'
                    )
                    await collection.find_one_and_update({'id': args[0]}, {'$set': {'message_id': message.message_id}})
            except Exception as media_error:
                logger.error(f"Media update error: {media_error}")
        else:
            try:
                if 'message_id' in character:
                    await context.bot.edit_message_caption(
                        chat_id=CHARA_CHANNEL_ID,
                        message_id=character['message_id'],
                        caption=caption,
                        parse_mode='HTML'
                    )
            except Exception as caption_error:
                logger.error(f"Caption update error: {caption_error}")

        await update.message.reply_text('Updated Done in Database.... But sometimes it Takes Time to edit Caption in Your Channel..So wait..')

    except Exception as e:
        logger.error(f"Update error: {e}")
        await update.message.reply_text(f'I guess did not added bot in channel.. or character uploaded Long time ago.. Or character not exits.. orr Wrong id')

UPLOAD_HANDLER = CommandHandler('upload', upload, block=False)
application.add_handler(UPLOAD_HANDLER)
DELETE_HANDLER = CommandHandler('delete', delete, block=False)
application.add_handler(DELETE_HANDLER)
UPDATE_HANDLER = CommandHandler('update', update, block=False)
application.add_handler(UPDATE_HANDLER)