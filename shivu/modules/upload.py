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
WRONG_FORMAT_TEXT = """❌ Incorrect Format!

📌 **How to use /upload:**
1. Reply to a photo
2. Send the command `/upload`
3. Include 3 lines in your message:

**Character Name**
**Anime Name**
**Rarity (1-15)**

✨ **Example:**
nezuko kamado
demon slayer 
4

📊 **Rarity Map (1-15):**
• 1  ⚪ Common
• 2  🔵 Rare
• 3  🟡 Legendary
• 4  💮 Special
• 5  👹 Ancient
• 6  🎐 Celestial
• 7  🔮 Epic
• 8  🪐 Cosmic
• 9  ⚰️ Nightmare
• 10 🌬️ Frostborn
• 11 💝 Valentine
• 12 🌸 Spring
• 13 🏖️ Tropical
• 14 🍭 Kawaii
• 15 🧬 Hybrid"""

RARITY_MAP = {
    1: "⚪ Common",
    2: "🔵 Rare",
    3: "🟡 Legendary",
    4: "💮 Special",
    5: "👹 Ancient",
    6: "🎐 Celestial",
    7: "🔮 Epic",
    8: "🪐 Cosmic",
    9: "⚰️ Nightmare",
    10: "🌬️ Frostborn",
    11: "💝 Valentine",
    12: "🌸 Spring",
    13: "🏖️ Tropical",
    14: "🍭 Kawaii",
    15: "🧬 Hybrid"
}

VALID_FIELDS = ['img_url', 'name', 'anime', 'rarity']

def format_character_id(sequence_number: int) -> str:
    """Format character ID as sequential human-readable number."""
    return str(sequence_number)

def format_update_help(fields: list) -> str:
    """Format update command help message."""
    help_text = "📝 **Update Command Usage:**\n\n"
    
    help_text += "1️⃣ **Update with value:**\n"
    help_text += "   `/update id field new_value`\n\n"
    
    help_text += "2️⃣ **Update image (reply to photo):**\n"
    help_text += "   `/update id img_url`\n"
    help_text += "   (Reply to a photo with this command)\n\n"
    
    help_text += f"**Valid fields:** {', '.join(fields)}\n\n"
    
    help_text += "✨ **Examples:**\n"
    help_text += "• `/update 12 name Nezuko Kamado`\n"
    help_text += "• `/update 12 anime Demon Slayer`\n"
    help_text += "• `/update 12 rarity 5`\n"
    help_text += "• `/update 12 img_url` (reply to photo)\n"
    help_text += "• `/update 12 img_url AgABCD1234` (file_id)\n"
    help_text += "• `/update 12 img_url https://example.com/image.jpg`"
    
    return help_text

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
        async with session.head(url, allow_redirects=True) as response:
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
    """Handle character upload command - REPLY TO PHOTO ONLY."""
    if update.effective_user.id not in Config.SUDO_USERS:
        await update.message.reply_text('🔒 Ask My Owner...')
        return

    # Check if message is a reply to a photo
    if not (update.message.reply_to_message and update.message.reply_to_message.photo):
        await update.message.reply_text(
            "📸 **Photo Required!**\n\n"
            "You must reply to a photo with the /upload command.\n\n"
            "📝 **Format:**\n"
            "1. Reply to a photo\n"
            "2. Send: `/upload`\n"
            "3. Include 3 lines:\n"
            "   • Character Name\n"
            "   • Anime Name\n"
            "   • Rarity (1-15)"
        )
        return

    try:
        # Get the text content (either from message or caption)
        text_content = update.message.text or update.message.caption or ""
        
        # Remove the /upload command and strip whitespace
        lines = [line.strip() for line in text_content.split('\n') if line.strip()]
        
        # Skip the /upload command line if present
        if lines and lines[0].startswith('/upload'):
            lines = lines[1:]
        
        # Check if we have exactly 3 lines
        if len(lines) != 3:
            await update.message.reply_text(WRONG_FORMAT_TEXT)
            return

        # Parse the 3 lines
        char_raw, anime_raw, rarity_raw = lines

        # Get photo file_id from replied message
        photo_sizes = update.message.reply_to_message.photo
        img_file_id = get_best_photo_file_id(photo_sizes)
        img_url = img_file_id  # Use Telegram file_id directly

        # Validate rarity
        try:
            rarity_num = int(rarity_raw.strip())
            if rarity_num not in RARITY_MAP:
                await update.message.reply_text(
                    f'❌ Invalid rarity number!\n'
                    f'Please use a number between 1 and {max(RARITY_MAP.keys())}.\n\n'
                    f'You entered: {rarity_raw}'
                )
                return
            rarity = RARITY_MAP[rarity_num]
        except ValueError:
            await update.message.reply_text(
                f'❌ Rarity must be a number!\n\n'
                f'You entered: "{rarity_raw}"\n'
                f'Expected format: 1-{max(RARITY_MAP.keys())}'
            )
            return

        # Create character document
        character = {
            'img_url': img_url,
            'name': char_raw.title(),
            'anime': anime_raw.title(),
            'rarity': rarity,
            'id': format_character_id(await get_next_sequence_number('character_id'))
        }

        # Send to channel and get message ID
        message_id = await send_channel_message(
            context, character, 
            update.effective_user.id, 
            update.effective_user.first_name,
            "Added"
        )
        character['message_id'] = message_id

        # Insert into database
        await collection.insert_one(character)
        
        await update.message.reply_text(
            f'✅ **Character Added Successfully!**\n\n'
            f'**Name:** {character["name"]}\n'
            f'**Anime:** {character["anime"]}\n'
            f'**Rarity:** {character["rarity"]}\n'
            f'**ID:** {character["id"]}'
        )

    except Exception as e:
        error_msg = str(e).lower()
        
        if 'character' in locals():
            try:
                await collection.insert_one(character)
                await update.message.reply_text(
                    "⚠️ **Character added to database but failed to send to channel.**\n"
                    "The bot might not have permission to post in the channel."
                )
                return
            except Exception as db_error:
                pass
        
        await update.message.reply_text(
            f'❌ **Upload Failed!**\n\n'
            f'Error: {str(e)[:200]}\n\n'
            f'If this error persists, contact: {SUPPORT_CHAT}'
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

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            format_update_help(VALID_FIELDS),
            parse_mode='Markdown'
        )
        return

    char_id = context.args[0]
    field = context.args[1]

    if field not in VALID_FIELDS:
        await update.message.reply_text(
            f'❌ Invalid field. Valid fields: {", ".join(VALID_FIELDS)}'
        )
        return

    character = await collection.find_one({'id': char_id})
    if not character:
        await update.message.reply_text('❌ Character not found.')
        return

    # Handle img_url field with optional value
    if field == 'img_url':
        # Check if user wants to use replied photo
        if len(context.args) == 2:
            # User used: /update id img_url (without value)
            if not (update.message.reply_to_message and update.message.reply_to_message.photo):
                await update.message.reply_text(
                    '📸 **Reply to a Photo Required!**\n\n'
                    'To update image by replying:\n'
                    '1. Reply to a photo\n'
                    '2. Use: `/update id img_url`\n\n'
                    '**OR** provide a valid image link:\n'
                    '`/update id img_url https://example.com/image.jpg`'
                )
                return
            
            # Extract file_id from replied photo
            photo_sizes = update.message.reply_to_message.photo
            new_value = get_best_photo_file_id(photo_sizes)
            update_data = {'img_url': new_value}
            
        else:
            # User used: /update id img_url <value>
            new_value = context.args[2]
            
            # Validate the value
            if not new_value.startswith('Ag'):
                # For external URLs, perform validation
                is_valid_url = await validate_image_url(new_value)
                if not is_valid_url:
                    await update.message.reply_text(
                        '❌ **Invalid Image URL!**\n\n'
                        'The URL must:\n'
                        '• Be publicly accessible\n'
                        '• Point directly to an image file\n'
                        '• Return HTTP status 200\n\n'
                        f'**Tip:** You can also:\n'
                        f'1. Reply to a photo and use `/update {char_id} img_url`\n'
                        f'2. Use a Telegram file_id (starts with "Ag")'
                    )
                    return
            
            update_data = {'img_url': new_value}
        
    elif field in ['name', 'anime']:
        if len(context.args) != 3:
            await update.message.reply_text(
                f'❌ Missing value for {field}.\n'
                f'Usage: /update {char_id} {field} new_value'
            )
            return
        
        new_value = context.args[2]
        update_data = {field: new_value.replace('-', ' ').title()}
        
    elif field == 'rarity':
        if len(context.args) != 3:
            await update.message.reply_text(
                f'❌ Missing rarity value.\n'
                f'Usage: /update {char_id} rarity 1-15'
            )
            return
        
        new_value = context.args[2]
        try:
            rarity_num = int(new_value)
            if rarity_num not in RARITY_MAP:
                await update.message.reply_text(
                    f'❌ Invalid rarity. Please use a number between 1 and {max(RARITY_MAP.keys())}.'
                )
                return
            update_data = {'rarity': RARITY_MAP[rarity_num]}
        except ValueError:
            await update.message.reply_text(f'❌ Rarity must be a number (1-{max(RARITY_MAP.keys())}).')
            return
    else:
        # This should not happen since we validated fields earlier
        await update.message.reply_text(f'❌ Unknown field: {field}')
        return

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