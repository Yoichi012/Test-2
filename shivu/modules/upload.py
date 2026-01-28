import asyncio
import os
from pymongo import ReturnDocument

from telegram import Update, InputMediaPhoto
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
    """
    Get next available sequence number for character ID
    Handles duplicate IDs by checking database and incrementing until unique ID found
    """
    sequence_collection = db.sequences
    max_attempts = 200

    for attempt in range(max_attempts):
        # Get or create sequence
        sequence_document = await sequence_collection.find_one_and_update(
            {'_id': sequence_name}, 
            {'$inc': {'sequence_value': 1}}, 
            return_document=ReturnDocument.AFTER
        )

        if not sequence_document:
            # Initialize sequence if not exists
            await sequence_collection.insert_one({'_id': sequence_name, 'sequence_value': 1})
            sequence_value = 1
        else:
            sequence_value = sequence_document['sequence_value']

        # Format ID with leading zeros
        new_id = str(sequence_value).zfill(2)

        # Check if this ID already exists in collection
        existing = await collection.find_one({'id': new_id})

        if not existing:
            # Found unique ID
            return sequence_value

        # ID exists, continue loop to try next number
        print(f"⚠️ ID {new_id} already exists, trying next...")

    # If we exhausted all attempts
    raise Exception(f"❌ Unable to generate unique character ID after {max_attempts} attempts. Database needs cleanup.")


async def upload_to_catbox(file_path):
    """
    Upload image to Catbox with improved error handling
    Ensures ClientSession is always properly closed
    """
    session = None
    try:
        import aiohttp
        import aiofiles

        async with aiofiles.open(file_path, 'rb') as f:
            file_data = await f.read()

        session = aiohttp.ClientSession()
        try:
            form = aiohttp.FormData()
            form.add_field('reqtype', 'fileupload')
            form.add_field('fileToUpload', file_data, filename='image.jpg')

            async with session.post(
                'https://catbox.moe/user/api.php', 
                data=form, 
                timeout=aiohttp.ClientTimeout(total=60)
            ) as resp:
                if resp.status == 200:
                    url = await resp.text()
                    if url.startswith('https://'):
                        print(f"✅ Catbox upload successful: {url}")
                        return url.strip()
            return None
        finally:
            await session.close()
    except asyncio.CancelledError:
        # Handle task cancellation gracefully
        if session and not session.closed:
            await session.close()
        print("⚠️ Catbox upload cancelled")
        raise
    except Exception as e:
        if session and not session.closed:
            await session.close()
        print(f"❌ Catbox upload error: {e}")
        return None


async def upload_to_telegraph(file_path):
    """
    Upload image to Telegraph with SSL fallback
    Ensures ClientSession is always properly closed
    """
    session = None
    try:
        import aiohttp
        import aiofiles

        async with aiofiles.open(file_path, 'rb') as f:
            file_data = await f.read()

        # Try with normal SSL first
        try:
            session = aiohttp.ClientSession()
            try:
                form = aiohttp.FormData()
                form.add_field('file', file_data, filename='image.jpg', content_type='image/jpeg')

                async with session.post(
                    'https://telegra.ph/upload', 
                    data=form, 
                    timeout=aiohttp.ClientTimeout(total=60)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if isinstance(data, list) and len(data) > 0:
                            url = f"https://telegra.ph{data[0]['src']}"
                            print(f"✅ Telegraph upload successful: {url}")
                            return url
            finally:
                await session.close()
                session = None
        except Exception as ssl_error:
            print(f"⚠️ Telegraph SSL error: {ssl_error}, trying without SSL verification...")

            # Fallback: Try without SSL verification
            try:
                connector = aiohttp.TCPConnector(ssl=False)
                session = aiohttp.ClientSession(connector=connector)
                try:
                    form = aiohttp.FormData()
                    form.add_field('file', file_data, filename='image.jpg', content_type='image/jpeg')

                    async with session.post(
                        'https://telegra.ph/upload', 
                        data=form, 
                        timeout=aiohttp.ClientTimeout(total=60)
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if isinstance(data, list) and len(data) > 0:
                                url = f"https://telegra.ph{data[0]['src']}"
                                print(f"✅ Telegraph upload successful (no SSL): {url}")
                                return url
                finally:
                    await session.close()
                    session = None
            except Exception as e:
                print(f"❌ Telegraph fallback also failed: {e}")

        return None
    except asyncio.CancelledError:
        # Handle task cancellation gracefully
        if session and not session.closed:
            await session.close()
        print("⚠️ Telegraph upload cancelled")
        raise
    except Exception as e:
        if session and not session.closed:
            await session.close()
        print(f"❌ Telegraph upload error: {e}")
        return None


async def upload_to_imgur(file_path):
    """
    Upload image to Imgur
    Ensures ClientSession is always properly closed
    """
    session = None
    try:
        import aiohttp
        import aiofiles
        import base64

        async with aiofiles.open(file_path, 'rb') as f:
            file_data = await f.read()

        b64_image = base64.b64encode(file_data).decode('utf-8')

        session = aiohttp.ClientSession()
        try:
            headers = {'Authorization': 'Client-ID 546c25a59c58ad7'}
            data = {'image': b64_image, 'type': 'base64'}

            async with session.post(
                'https://api.imgur.com/3/image', 
                headers=headers, 
                data=data, 
                timeout=aiohttp.ClientTimeout(total=60)
            ) as resp:
                if resp.status == 200:
                    json_data = await resp.json()
                    if json_data.get('success'):
                        url = json_data['data']['link']
                        print(f"✅ Imgur upload successful: {url}")
                        return url
            return None
        finally:
            await session.close()
    except asyncio.CancelledError:
        # Handle task cancellation gracefully
        if session and not session.closed:
            await session.close()
        print("⚠️ Imgur upload cancelled")
        raise
    except Exception as e:
        if session and not session.closed:
            await session.close()
        print(f"❌ Imgur upload error: {e}")
        return None


async def get_image_url_from_reply(message, context):
    """
    Get image URL from replied message using multiple upload services
    Uses asyncio.wait with FIRST_COMPLETED for race condition
    Properly handles task cancellation and cleanup
    """
    try:
        if not message.reply_to_message or not message.reply_to_message.photo:
            return None

        # Download photo
        photo = message.reply_to_message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        file_path = f"/tmp/{photo.file_id}.jpg"
        await file.download_to_drive(file_path)

        print("📤 Starting parallel uploads to multiple services...")

        # Create upload tasks - but don't use create_task yet
        # We'll let asyncio.wait handle them properly
        upload_tasks = {
            asyncio.create_task(upload_to_catbox(file_path)),
            asyncio.create_task(upload_to_telegraph(file_path)),
            asyncio.create_task(upload_to_imgur(file_path))
        }

        result_url = None

        try:
            # Wait for first successful completion
            while upload_tasks and result_url is None:
                done, pending = await asyncio.wait(
                    upload_tasks, 
                    return_when=asyncio.FIRST_COMPLETED
                )

                # Check completed tasks
                for task in done:
                    try:
                        url = task.result()
                        if url:
                            result_url = url
                            print(f"🎯 First successful upload: {result_url}")
                            break
                    except Exception as e:
                        print(f"⚠️ Upload task failed: {e}")

                # Update pending tasks set
                upload_tasks = pending

                # If we got a result, break
                if result_url:
                    break

            # Cancel all remaining tasks
            if upload_tasks:
                print(f"🛑 Cancelling {len(upload_tasks)} remaining upload tasks...")
                for task in upload_tasks:
                    task.cancel()

                # Wait for all cancelled tasks to complete cleanup
                if upload_tasks:
                    await asyncio.gather(*upload_tasks, return_exceptions=True)

        except Exception as e:
            print(f"❌ Error during parallel upload: {e}")
            # Cancel all tasks on error
            for task in upload_tasks:
                if not task.done():
                    task.cancel()
            # Wait for cancellation to complete
            if upload_tasks:
                await asyncio.gather(*upload_tasks, return_exceptions=True)
            raise

        # Clean up downloaded file
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception as e:
            print(f"⚠️ Failed to remove temp file: {e}")

        if result_url:
            return result_url
        else:
            print("❌ All upload services failed")
            return None

    except Exception as e:
        print(f"❌ Error in get_image_url_from_reply: {e}")
        return None


def format_channel_caption(char_id, name, anime, rarity, uploader_name, uploader_id):
    """Format caption for channel message"""
    return (
        f'<b>CHARACTER ADDED!</b>\n\n'
        f'🆔 <b>ID:</b> {char_id}\n'
        f'👤 <b>Name:</b> {name}\n'
        f'📺 <b>Anime:</b> {anime}\n'
        f'⭐ <b>Rarity:</b> {rarity}\n\n'
        f'➕ <b>Added by:</b> <a href="tg://user?id={uploader_id}">{uploader_name}</a>'
    )


async def upload(update: Update, context: CallbackContext) -> None:
    """Upload a new character (reply to image)"""
    if str(update.effective_user.id) not in sudo_users:
        await update.message.reply_text('❌ Ask my Owner to use this Command...')
        return

    try:
        args = context.args
        if len(args) != 3:
            await update.message.reply_text(WRONG_FORMAT_TEXT)
            return

        if not update.message.reply_to_message or not update.message.reply_to_message.photo:
            await update.message.reply_text('❌ Please reply to an image with the upload command.')
            return

        # Parse arguments
        character_name = args[0].replace('-', ' ').title()
        anime = args[1].replace('-', ' ').title()

        try:
            rarity_num = int(args[2])
            if rarity_num not in RARITY_MAP:
                await update.message.reply_text('❌ Invalid rarity. Please use 1-15.')
                return
            rarity = RARITY_MAP[rarity_num]
        except ValueError:
            await update.message.reply_text('❌ Invalid rarity. Please use 1-15.')
            return

        # Start processing
        processing_msg = await update.message.reply_text('⏳ Processing character upload...')

        # Get image URL
        await processing_msg.edit_text('⏳ Uploading image to cloud services...')
        img_url = await get_image_url_from_reply(update.message, context)

        if not img_url:
            await processing_msg.edit_text('❌ Failed to upload image to any cloud service. Please try again.')
            return

        # Get unique character ID
        await processing_msg.edit_text('⏳ Generating character ID...')
        sequence_value = await get_next_sequence_number('character_id')
        id = str(sequence_value).zfill(2)

        # Create character document
        character = {
            'img_url': img_url,
            'name': character_name,
            'anime': anime,
            'rarity': rarity,
            'id': id
        }

        # Get photo file_id for channel upload
        photo_file_id = update.message.reply_to_message.photo[-1].file_id

        try:
            await processing_msg.edit_text(f'✅ Character ID: {id}\n⏳ Uploading to channel...')

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

            # Insert into database
            await collection.insert_one(character)

            await processing_msg.edit_text(
                f'✅ <b>CHARACTER ADDED SUCCESSFULLY!</b>\n\n'
                f'🆔 <b>ID:</b> {id}\n'
                f'👤 <b>Name:</b> {character_name}\n'
                f'📺 <b>Anime:</b> {anime}\n'
                f'⭐ <b>Rarity:</b> {rarity}\n'
                f'🔗 <b>Image URL:</b> {img_url}',
                parse_mode='HTML'
            )

        except Exception as e:
            # If channel upload fails, still save to database
            await collection.insert_one(character)
            await processing_msg.edit_text(
                f'✅ Character Added to Database\n'
                f'❌ Channel upload failed: {str(e)}\n\n'
                f'🆔 ID: {id}\n'
                f'💡 Check if bot has permission to post in channel.'
            )

    except Exception as e:
        await update.message.reply_text(
            f'❌ <b>Character Upload Failed</b>\n\n'
            f'<b>Error:</b> {str(e)}\n\n'
            f'💡 If this persists, contact: @{SUPPORT_CHAT}',
            parse_mode='HTML'
        )


async def delete(update: Update, context: CallbackContext) -> None:
    """Delete character by ID"""
    if str(update.effective_user.id) not in sudo_users:
        await update.message.reply_text('❌ Ask my Owner to use this Command...')
        return

    try:
        args = context.args
        if len(args) != 1:
            await update.message.reply_text('❌ Incorrect format...\n\n✅ Use: /delete ID')
            return

        character = await collection.find_one_and_delete({'id': args[0]})

        if character:
            try:
                await context.bot.delete_message(chat_id=CHARA_CHANNEL_ID, message_id=character['message_id'])
                await update.message.reply_text(f'✅ Character ID {args[0]} deleted successfully from database and channel.')
            except:
                await update.message.reply_text(f'✅ Character ID {args[0]} deleted from database.\n⚠️ Could not delete from channel (message may already be deleted).')
        else:
            await update.message.reply_text(f'❌ Character ID {args[0]} not found in database.')

    except Exception as e:
        await update.message.reply_text(f'❌ Error: {str(e)}')


async def update(update: Update, context: CallbackContext) -> None:
    """Update character fields - supports reply to image for img_url updates"""
    if str(update.effective_user.id) not in sudo_users:
        await update.message.reply_text('❌ You do not have permission to use this command.')
        return

    try:
        args = context.args
        if len(args) != 3:
            await update.message.reply_text(
                '❌ Incorrect format.\n\n'
                '✅ Use: /update <id> <field> <new_value>\n\n'
                '📝 Fields: name, anime, rarity, img_url\n'
                '💡 For img_url: Reply to image with /update <id> img_url <value>'
            )
            return

        # Get character by ID
        character = await collection.find_one({'id': args[0]})
        if not character:
            await update.message.reply_text(f'❌ Character ID {args[0]} not found.')
            return

        # Validate field
        valid_fields = ['img_url', 'name', 'anime', 'rarity']
        if args[1] not in valid_fields:
            await update.message.reply_text(f'❌ Invalid field.\n\n✅ Valid fields: {", ".join(valid_fields)}')
            return

        # Process new value based on field type
        if args[1] in ['name', 'anime']:
            new_value = args[2].replace('-', ' ').title()

        elif args[1] == 'rarity':
            try:
                rarity_num = int(args[2])
                if rarity_num not in RARITY_MAP:
                    await update.message.reply_text('❌ Invalid rarity. Please use 1-15.')
                    return
                new_value = RARITY_MAP[rarity_num]
            except (ValueError, KeyError):
                await update.message.reply_text('❌ Invalid rarity. Please use 1-15.')
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
                # For image updates, use edit_message_media
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

            await update.message.reply_text(
                f'✅ <b>UPDATE SUCCESSFUL!</b>\n\n'
                f'🆔 <b>ID:</b> {args[0]}\n'
                f'📝 <b>Field:</b> {args[1]}\n'
                f'🔄 <b>New Value:</b> {new_value}\n\n'
                f'💾 Database and channel both updated.',
                parse_mode='HTML'
            )

        except Exception as e:
            await update.message.reply_text(
                f'✅ Database updated successfully.\n'
                f'⚠️ Channel update failed: {str(e)}\n\n'
                f'💡 The character may not exist in channel or bot lacks permissions.'
            )

    except Exception as e:
        await update.message.reply_text(f'❌ Update failed: {str(e)}')


# Register handlers with block=True for heavy commands (FIXED)
# This prevents event loop closure issues during shutdown
UPLOAD_HANDLER = CommandHandler('upload', upload, block=True)
application.add_handler(UPLOAD_HANDLER)

DELETE_HANDLER = CommandHandler('delete', delete, block=True)
application.add_handler(DELETE_HANDLER)

UPDATE_HANDLER = CommandHandler('update', update, block=True)
application.add_handler(UPDATE_HANDLER)
