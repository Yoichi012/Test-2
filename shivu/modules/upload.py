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

# Global aiohttp session for reuse
SESSION: Optional[aiohttp.ClientSession] = None

# Constants
WRONG_FORMAT_TEXT = """❌ Wrong format!

You have two options:

1️⃣ **Reply to a photo**: Reply to any photo and use:
   `/upload character-name anime-name rarity-number`

2️⃣ **With image URL**: Use:
   `/upload image-url character-name anime-name rarity-number`

📊 Rarity Map:
• 1 ⚪ Common
• 2 🟣 Rare
• 3 🟡 Legendary
• 4 🟢 Medium
• 5 💮 Special Edition"""

RARITY_MAP = {
    1: "⚪ Common",
    2: "🟣 Rare", 
    3: "🟡 Legendary",
    4: "🟢 Medium",
    5: "💮 Special Edition"
}

VALID_FIELDS = ['img_url', 'name', 'anime', 'rarity']

async def get_session() -> aiohttp.ClientSession:
    """Get or create global aiohttp session."""
    global SESSION
    if SESSION is None or SESSION.closed:
        timeout = aiohttp.ClientTimeout(total=10)
        SESSION = aiohttp.ClientSession(timeout=timeout)
    return SESSION

async def validate_image_url(url: str) -> bool:
    """Validate if URL is accessible and points to an image."""
    # Check if it's a Telegram file_id (starts with 'Ag')
    if url.startswith('Ag'):
        return True
    
    session = await get_session()
    try:
        async with session.head(url) as response:
            if response.status != 200:
                return False
            
            # Check if content type is image
            content_type = response.headers.get('Content-Type', '').lower()
            return content_type.startswith('image/')
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return False
    finally:
        # Don't close session, keep it open for reuse
        pass

async def get_next_sequence_number(sequence_name: str) -> int:
    """Get next sequence number for character IDs."""
    sequence_collection = db.sequences
    sequence_document = await sequence_collection.find_one_and_update(
        {'_id': sequence_name},
        {'$inc': {'sequence_value': 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER
    )
    return sequence_document['sequence_value']

def get_best_photo_file_id(photo_sizes: List[PhotoSize]) -> str:
    """Get the file_id of the highest quality photo."""
    # Telegram sends multiple sizes, the last one is usually the largest
    return photo_sizes[-1].file_id

async def send_channel_message(
    context: ContextTypes.DEFAULT_TYPE, 
    character: Dict[str, Any], 
    user_id: int, 
    user_name: str,
    action: str = "Added"
) -> Optional[int]:
    """Send or edit character message in channel."""
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
            # Message was deleted from channel, send new one
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
    """Handle character upload command with both reply-to-photo and URL methods."""
    if update.effective_user.id not in Config.SUDO_USERS:
        await update.message.reply_text('Ask My Owner...')
        return

    try:
        # Check if message is a reply to a photo
        if update.message.reply_to_message and update.message.reply_to_message.photo:
            if not context.args or len(context.args) != 3:
                await update.message.reply_text(
                    "❌ When replying to a photo, use: /upload character-name anime-name rarity-number\n"
                    "Example: /upload nezuko demon-slayer 4"
                )
                return
            
            char_raw, anime_raw, rarity_raw = context.args
            photo_sizes = update.message.reply_to_message.photo
            img_file_id = get_best_photo_file_id(photo_sizes)
            img_url = img_file_id
            
        else:
            if not context.args or len(context.args) != 4:
                await update.message.reply_text(WRONG_FORMAT_TEXT)
                return
            
            img_url, char_raw, anime_raw, rarity_raw = context.args
            
            if not img_url.startswith('Ag') and not await validate_image_url(img_url):
                await update.message.reply_text(
                    '❌ Invalid or inaccessible image URL.\n'
                    'Make sure the URL is public and points to an image file.'
                )
                return

        try:
            rarity_num = int(rarity_raw)
            if rarity_num not in RARITY_MAP:
                await update.message.reply_text(
                    '❌ Invalid rarity. Please use 1, 2, 3, 4, or 5.'
                )
                return
            rarity = RARITY_MAP[rarity_num]
        except ValueError:
            await update.message.reply_text('❌ Rarity must be a number (1-5).')
            return

        character = {
            'img_url': img_url,
            'name': char_raw.replace('-', ' ').title(),
            'anime': anime_raw.replace('-', ' ').title(),
            'rarity': rarity,
            'id': str(await get_next_sequence_number('character_id')).zfill(6)
        }

        message_id = await send_channel_message(
            context, character, 
            update.effective_user.id, 
            update.effective_user.first_name,
            "Added"
        )
        character['message_id'] = message_id
        
        await collection.insert_one(character)
        await update.message.reply_text('✅ CHARACTER ADDED SUCCESSFULLY!')
        
    except Exception as e:
        try:
            if 'character' in locals():
                await collection.insert_one(character)
                await update.message.reply_text(
                    "⚠️ Character added to database but failed to send to channel.\n"
                    "Bot might not have permission to post in the channel."
                )
            else:
                raise
        except Exception as db_error:
            await update.message.reply_text(
                f'❌ Character upload failed.\n'
                f'Error: {str(db_error)}\n'
                f'If you think this is a source error, forward to: {SUPPORT_CHAT}'
            )

async def delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle character deletion command."""
    if update.effective_user.id not in Config.SUDO_USERS:
        await update.message.reply_text('Ask my Owner to use this Command...')
        return

    if not context.args or len(context.args) != 1:
        await update.message.reply_text('❌ Incorrect format... Please use: /delete ID')
        return

    character_id = context.args[0]
    
    character = await collection.find_one_and_delete({'id': character_id})
    
    if not character:
        await update.message.reply_text('❌ Character not found in database.')
        return

    try:
        if 'message_id' in character:
            await context.bot.delete_message(
                chat_id=CHARA_CHANNEL_ID,
                message_id=character['message_id']
            )
            await update.message.reply_text('✅ Character deleted from database and channel.')
        else:
            await update.message.reply_text('✅ Character deleted from database (no channel message found).')
    except BadRequest as e:
        error_msg = str(e).lower()
        if "message to delete not found" in error_msg:
            await update.message.reply_text('✅ Character deleted from database (channel message was already gone).')
        else:
            await update.message.reply_text(
                f'✅ Character deleted from database.\n'
                f'⚠️ Could not delete from channel: {str(e)}'
            )
    except Exception as e:
        await update.message.reply_text(
            f'✅ Character deleted from database.\n'
            f'⚠️ Channel deletion error: {str(e)}'
        )

async def update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle character update command."""
    if update.effective_user.id not in Config.SUDO_USERS:
        await update.message.reply_text('You do not have permission to use this command.')
        return

    if not context.args or len(context.args) != 3:
        await update.message.reply_text(
            '❌ Incorrect format. Please use: /update id field new_value\n'
            f'Valid fields: {", ".join(VALID_FIELDS)}'
        )
        return

    char_id, field, new_value = context.args

    if field not in VALID_FIELDS:
        await update.message.reply_text(
            f'❌ Invalid field. Valid fields: {", ".join(VALID_FIELDS)}'
        )
        return

    character = await collection.find_one({'id': char_id})
    if not character:
        await update.message.reply_text('❌ Character not found.')
        return

    update_data = {}
    if field in ['name', 'anime']:
        update_data[field] = new_value.replace('-', ' ').title()
    elif field == 'rarity':
        try:
            rarity_num = int(new_value)
            if rarity_num not in RARITY_MAP:
                await update.message.reply_text('❌ Invalid rarity. Please use 1, 2, 3, 4, or 5.')
                return
            update_data[field] = RARITY_MAP[rarity_num]
        except ValueError:
            await update.message.reply_text('❌ Rarity must be a number (1-5).')
            return
    else:
        if not new_value.startswith('Ag') and not await validate_image_url(new_value):
            await update.message.reply_text('❌ Invalid or inaccessible image URL.')
            return
        update_data[field] = new_value

    updated_character = await collection.find_one_and_update(
        {'id': char_id},
        {'$set': update_data},
        return_document=ReturnDocument.AFTER
    )

    if not updated_character:
        await update.message.reply_text('❌ Failed to update character in database.')
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
        
        await update.message.reply_text('✅ Character updated successfully!')
        
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
            await update.message.reply_text('✅ Character updated! (Recreated channel message)')
        else:
            await update.message.reply_text(
                f'✅ Database updated but channel update failed: {str(e)}'
            )
    except Exception as e:
        await update.message.reply_text(
            f'✅ Database updated but channel update failed: {str(e)}'
        )

# Register handlers
application.add_handler(CommandHandler("upload", upload))
application.add_handler(CommandHandler("delete", delete))
application.add_handler(CommandHandler("update", update))

async def cleanup_session() -> None:
    """Cleanup global session on shutdown."""
    global SESSION
    if SESSION and not SESSION.closed:
        await SESSION.close()
