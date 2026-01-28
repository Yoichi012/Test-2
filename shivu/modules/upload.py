import asyncio
import urllib.request
from pymongo import ReturnDocument

from telegram import Update
from telegram.ext import CommandHandler, CallbackContext

from shivu import application, collection, db
from shivu.config import Config

# Extract configuration values
sudo_users = [str(user_id) for user_id in Config.SUDO_USERS]
CHARA_CHANNEL_ID = Config.CHARA_CHANNEL_ID
SUPPORT_CHAT = Config.SUPPORT_CHAT

# Updated Rarity Map (1-15)
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
    15: "🧬 ʜʏʙʀɪᴅ"
}

WRONG_FORMAT_TEXT = """Wrong ❌️ format...  eg. /upload character-name anime-name rarity-number (reply to image)

Reply to an image with:
/upload character-name anime-name rarity-number

Rarity Map (1-15):
1: ⚪ ᴄᴏᴍᴍᴏɴ
2: 🔵 ʀᴀʀᴇ
3: 🟡 ʟᴇɢᴇɴᴅᴀʀʏ
4: 💮 ꜱᴘᴇᴄɪᴀʟ
5: 👹 ᴀɴᴄɪᴇɴᴛ
6: 🎐 ᴄᴇʟᴇꜱᴛɪᴀʟ
7: 🔮 ᴇᴘɪᴄ
8: 🪐 ᴄᴏꜱᴍɪᴄ
9: ⚰️ ɴɪɢʜᴛᴍᴀʀᴇ
10: 🌬️ ꜰʀᴏꜱᴛʙᴏʀɴ
11: 💝 ᴠᴀʟᴇɴᴛɪɴᴇ
12: 🌸 ꜱᴘʀɪɴɢ
13: 🏖️ ᴛʀᴏᴘɪᴄᴀʟ
14: 🍭 ᴋᴀᴡᴀɪɪ
15: 🧬 ʜʏʙʀɪᴅ"""


async def get_next_sequence_number(sequence_name):
    """Get next sequence number for character ID"""
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


async def upload_to_catbox(file_path):
    """Upload image to Catbox"""
    try:
        import aiohttp
        import aiofiles
        
        async with aiofiles.open(file_path, 'rb') as f:
            file_data = await f.read()
        
        async with aiohttp.ClientSession() as session:
            form = aiohttp.FormData()
            form.add_field('reqtype', 'fileupload')
            form.add_field('fileToUpload', file_data, filename='image.jpg')
            
            async with session.post('https://catbox.moe/user/api.php', data=form, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 200:
                    url = await resp.text()
                    if url.startswith('https://'):
                        return url.strip()
        return None
    except Exception as e:
        print(f"Catbox upload error: {e}")
        return None


async def upload_to_telegraph(file_path):
    """Upload image to Telegraph"""
    try:
        import aiohttp
        import aiofiles
        
        async with aiofiles.open(file_path, 'rb') as f:
            file_data = await f.read()
        
        async with aiohttp.ClientSession() as session:
            form = aiohttp.FormData()
            form.add_field('file', file_data, filename='image.jpg', content_type='image/jpeg')
            
            async with session.post('https://telegra.ph/upload', data=form, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, list) and len(data) > 0:
                        return f"https://telegra.ph{data[0]['src']}"
        return None
    except Exception as e:
        print(f"Telegraph upload error: {e}")
        return None


async def upload_to_imgur(file_path):
    """Upload image to Imgur"""
    try:
        import aiohttp
        import aiofiles
        import base64
        
        async with aiofiles.open(file_path, 'rb') as f:
            file_data = await f.read()
        
        b64_image = base64.b64encode(file_data).decode('utf-8')
        
        async with aiohttp.ClientSession() as session:
            headers = {'Authorization': 'Client-ID 546c25a59c58ad7'}
            data = {'image': b64_image, 'type': 'base64'}
            
            async with session.post('https://api.imgur.com/3/image', headers=headers, data=data, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 200:
                    json_data = await resp.json()
                    if json_data.get('success'):
                        return json_data['data']['link']
        return None
    except Exception as e:
        print(f"Imgur upload error: {e}")
        return None


async def get_image_url_from_reply(message, context):
    """
    Get image URL from replied message using multiple upload services
    Uses race condition - first successful upload wins, others are cancelled
    """
    if not message.reply_to_message or not message.reply_to_message.photo:
        return None
    
    try:
        # Get the largest photo
        photo = message.reply_to_message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        file_path = f"/tmp/{photo.file_id}.jpg"
        await file.download_to_drive(file_path)
        
        # Create tasks for all upload services
        tasks = [
            asyncio.create_task(upload_to_catbox(file_path)),
            asyncio.create_task(upload_to_telegraph(file_path)),
            asyncio.create_task(upload_to_imgur(file_path))
        ]
        
        # Wait for first successful upload
        img_url = None
        for task in asyncio.as_completed(tasks):
            result = await task
            if result:
                img_url = result
                # Cancel remaining tasks
                for t in tasks:
                    if not t.done():
                        t.cancel()
                break
        
        # Clean up temporary file
        try:
            import os
            os.remove(file_path)
        except:
            pass
        
        return img_url
        
    except Exception as e:
        print(f"Error getting image URL: {e}")
        return None


def format_channel_caption(character_id, character_name, anime_name, rarity, user_first_name, user_id):
    """Format caption for channel message"""
    # Extract rarity emoji and name
    rarity_parts = rarity.split(' ', 1)
    rarity_emoji = rarity_parts[0] if len(rarity_parts) > 0 else ""
    rarity_name = rarity_parts[1] if len(rarity_parts) > 1 else rarity
    
    caption = f"""<b>{character_id}:</b> {character_name}
{anime_name}
{rarity_emoji} 𝙍𝘼𝙍𝙄𝙏𝙔: {rarity_name}

𝑴𝒂𝒅𝒆 𝑩𝒚 ➥ <a href="tg://user?id={user_id}">{user_first_name}</a>"""
    
    return caption


async def upload(update: Update, context: CallbackContext) -> None:
    """Upload new character - Reply to image with character details"""
    if str(update.effective_user.id) not in sudo_users:
        await update.message.reply_text('Ask My Owner...')
        return

    try:
        args = context.args
        if len(args) != 3:
            await update.message.reply_text(WRONG_FORMAT_TEXT)
            return

        # Check if message is a reply to an image
        if not update.message.reply_to_message or not update.message.reply_to_message.photo:
            await update.message.reply_text('Please reply to an image with the upload command.')
            return

        character_name = args[0].replace('-', ' ').title()
        anime = args[1].replace('-', ' ').title()

        # Validate rarity
        try:
            rarity_num = int(args[2])
            if rarity_num not in RARITY_MAP:
                await update.message.reply_text(f'Invalid rarity. Please use 1-15.')
                return
            rarity = RARITY_MAP[rarity_num]
        except (ValueError, KeyError):
            await update.message.reply_text('Invalid rarity. Please use 1-15.')
            return

        # Get character ID
        id = str(await get_next_sequence_number('character_id')).zfill(2)

        # Send processing message
        processing_msg = await update.message.reply_text('⏳ Uploading image to cloud services...')

        # Get image URL from reply using multiple services
        img_url = await get_image_url_from_reply(update.message, context)
        
        if not img_url:
            await processing_msg.edit_text('❌ Failed to upload image to cloud services. Please try again.')
            return

        # Create character document
        character = {
            'img_url': img_url,
            'name': character_name,
            'anime': anime,
            'rarity': rarity,
            'id': id
        }

        try:
            # Use Telegram file_id for fast upload to channel
            photo_file_id = update.message.reply_to_message.photo[-1].file_id
            
            # Send to channel using file_id
            message = await context.bot.send_photo(
                chat_id=CHARA_CHANNEL_ID,
                photo=photo_file_id,
                caption=format_channel_caption(
                    id, 
                    character_name, 
                    anime, 
                    rarity, 
                    update.effective_user.first_name,
                    update.effective_user.id
                ),
                parse_mode='HTML'
            )
            
            character['message_id'] = message.message_id
            await collection.insert_one(character)
            
            await processing_msg.edit_text(f'✅ CHARACTER ADDED\n\n🆔 ID: {id}\n📦 Image URL: {img_url}')
            
        except Exception as e:
            await collection.insert_one(character)
            await processing_msg.edit_text(f"✅ Character Added to Database\n❌ Channel upload failed: {str(e)}\n\nConsider checking channel permissions.")

    except Exception as e:
        await update.message.reply_text(f'❌ Character Upload Unsuccessful.\n\nError: {str(e)}\n\nIf you think this is a source error, forward to: {SUPPORT_CHAT}')


async def delete(update: Update, context: CallbackContext) -> None:
    """Delete character by ID"""
    if str(update.effective_user.id) not in sudo_users:
        await update.message.reply_text('Ask my Owner to use this Command...')
        return

    try:
        args = context.args
        if len(args) != 1:
            await update.message.reply_text('Incorrect format... Please use: /delete ID')
            return

        character = await collection.find_one_and_delete({'id': args[0]})

        if character:
            try:
                await context.bot.delete_message(chat_id=CHARA_CHANNEL_ID, message_id=character['message_id'])
                await update.message.reply_text('✅ Character deleted successfully from database and channel.')
            except:
                await update.message.reply_text('✅ Character deleted from database.\n⚠️ Could not delete from channel (message may already be deleted).')
        else:
            await update.message.reply_text('❌ Character not found in database.')
            
    except Exception as e:
        await update.message.reply_text(f'❌ Error: {str(e)}')


async def update(update: Update, context: CallbackContext) -> None:
    """Update character fields - supports reply to image for img_url updates"""
    if str(update.effective_user.id) not in sudo_users:
        await update.message.reply_text('You do not have permission to use this command.')
        return

    try:
        args = context.args
        if len(args) != 3:
            await update.message.reply_text('Incorrect format. Please use: /update id field new_value\n\nFor img_url: Reply to image with /update id img_url')
            return

        # Get character by ID
        character = await collection.find_one({'id': args[0]})
        if not character:
            await update.message.reply_text('❌ Character not found.')
            return

        # Validate field
        valid_fields = ['img_url', 'name', 'anime', 'rarity']
        if args[1] not in valid_fields:
            await update.message.reply_text(f'Invalid field. Please use one of: {", ".join(valid_fields)}')
            return

        # Process new value based on field type
        if args[1] in ['name', 'anime']:
            new_value = args[2].replace('-', ' ').title()
            
        elif args[1] == 'rarity':
            try:
                rarity_num = int(args[2])
                if rarity_num not in RARITY_MAP:
                    await update.message.reply_text('Invalid rarity. Please use 1-15.')
                    return
                new_value = RARITY_MAP[rarity_num]
            except (ValueError, KeyError):
                await update.message.reply_text('Invalid rarity. Please use 1-15.')
                return
                
        elif args[1] == 'img_url':
            # Check if reply to image
            if update.message.reply_to_message and update.message.reply_to_message.photo:
                processing_msg = await update.message.reply_text('⏳ Uploading new image to cloud services...')
                new_value = await get_image_url_from_reply(update.message, context)
                
                if not new_value:
                    await processing_msg.edit_text('❌ Failed to upload image. Please try again.')
                    return
                    
                await processing_msg.edit_text(f'✅ Image uploaded: {new_value}')
            else:
                new_value = args[2]
        else:
            new_value = args[2]

        # Update in database
        await collection.find_one_and_update({'id': args[0]}, {'$set': {args[1]: new_value}})

        # Update channel message
        try:
            if args[1] == 'img_url':
                # For image updates, use edit_message_media to update both image and caption
                # This prevents character deletion and re-upload
                from telegram import InputMediaPhoto
                
                # Prepare updated values
                updated_name = character.get('name', 'Unknown')
                updated_anime = character.get('anime', 'Unknown')
                updated_rarity = character.get('rarity', '⚪ ᴄᴏᴍᴍᴏɴ')
                
                new_caption = format_channel_caption(
                    character['id'],
                    updated_name,
                    updated_anime,
                    updated_rarity,
                    update.effective_user.first_name,
                    update.effective_user.id
                )
                
                await context.bot.edit_message_media(
                    chat_id=CHARA_CHANNEL_ID,
                    message_id=character['message_id'],
                    media=InputMediaPhoto(
                        media=new_value,
                        caption=new_caption,
                        parse_mode='HTML'
                    )
                )
                
            else:
                # For other fields, just update caption
                updated_name = new_value if args[1] == 'name' else character.get('name', 'Unknown')
                updated_anime = new_value if args[1] == 'anime' else character.get('anime', 'Unknown')
                updated_rarity = new_value if args[1] == 'rarity' else character.get('rarity', '⚪ ᴄᴏᴍᴍᴏɴ')
                
                new_caption = format_channel_caption(
                    character['id'],
                    updated_name,
                    updated_anime,
                    updated_rarity,
                    update.effective_user.first_name,
                    update.effective_user.id
                )
                
                await context.bot.edit_message_caption(
                    chat_id=CHARA_CHANNEL_ID,
                    message_id=character['message_id'],
                    caption=new_caption,
                    parse_mode='HTML'
                )

            await update.message.reply_text('✅ Update completed successfully!\n\nDatabase and channel both updated.')
            
        except Exception as e:
            await update.message.reply_text(f'✅ Database updated successfully.\n⚠️ Channel update failed: {str(e)}\n\nThe character may not exist in channel or bot lacks permissions.')

    except Exception as e:
        await update.message.reply_text(f'❌ Update failed: {str(e)}')


# Register handlers
UPLOAD_HANDLER = CommandHandler('upload', upload, block=False)
application.add_handler(UPLOAD_HANDLER)

DELETE_HANDLER = CommandHandler('delete', delete, block=False)
application.add_handler(DELETE_HANDLER)

UPDATE_HANDLER = CommandHandler('update', update, block=False)
application.add_handler(UPDATE_HANDLER)
