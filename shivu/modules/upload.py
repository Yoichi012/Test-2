import urllib.request
from pymongo import ReturnDocument
import os
from telegram import Update
from telegram.ext import CommandHandler, CallbackContext
import requests
from pyrogram import filters
from pyrogram.types import InputMediaPhoto
import os
from pyrogram import Client, filters
from pyrogram.types import Message
from pymongo import ReturnDocument, UpdateOne
import urllib.request
import random
import aiohttp
import asyncio
from shivu import UPDATE_CHAT, SUPPORT_CHAT, required_group_id, PHOTO_URL, OWNER_ID, PARTNER
from shivu import (
    collectionps as collection,
    top_global_groups_collectionps as top_global_groups_collection,
    group_user_totals_collectionps as group_user_totals_collection,
    user_collectionps as user_collection,
    user_totals_collectionps as user_totals_collection,
    shivuups as shivuu,
    shivuups as app,
    applicationps as application,
    SUPPORT_CHATps as SUPPORT,
    UPDATE_CHATps as UPDATE_CHAT,
    dbps as db,
    pmusersps as pmusers,
    ban_collectionps as ban_collection,
    user_countps as user_count, 
    chat_dataps as chat_data,
)

# Define filters if not already imported
def sudo_filter_func(_, __, message):
    """Filter for sudo users (owner and partners)"""
    if not message.from_user:
        return False
    sudo_users = [OWNER_ID] + (PARTNER if PARTNER else [])
    return message.from_user.id in sudo_users

def uploader_filter_func(_, __, message):
    """Filter for uploader users (same as sudo for now)"""
    if not message.from_user:
        return False
    sudo_users = [OWNER_ID] + (PARTNER if PARTNER else [])
    return message.from_user.id in sudo_users

sudo_filter = filters.create(sudo_filter_func)
uploader_filter = filters.create(uploader_filter_func)

# Channel ID for posting character information
CHARA_CHANNEL_ID = -1003046490021

# Your imgBB API Key
IMGBB_API_KEY = "6d52008ec9026912f9f50c8ca96a09c3"

# Define the wrong format message and rarity map
WRONG_FORMAT_TEXT = """Wrong ❌ format...  eg. /upload reply to photo muzan-kibutsuji Demon-slayer 3

format:- /upload reply character-name anime-name rarity-number

use rarity number accordingly rarity Map

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
"""

# Define the channel ID and RARITY_MAP
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


# Global set to keep track of active IDs and a lock for safe access
active_ids = set()
id_lock = asyncio.Lock()

async def upload_to_imgbb(file_path, api_key=IMGBB_API_KEY):
    """
    Upload image to imgBB (primary upload service)
    """
    url = "https://api.imgbb.com/1/upload"
    
    # Read the file
    with open(file_path, "rb") as file:
        file_data = file.read()
    
    # Create form data
    data = aiohttp.FormData()
    data.add_field('key', api_key)
    data.add_field('image', file_data, filename=os.path.basename(file_path))
    
    async with aiohttp.ClientSession() as session:
        async with session.post(url, data=data) as response:
            result = await response.json()
            
            if response.status == 200 and result.get("success"):
                return result["data"]["url"]
            else:
                error_msg = result.get('error', {}).get('message', 'Unknown error')
                raise Exception(f"ImgBB upload fbailed: {error_msg}")

async def upload_to_telegraph(file_path):
    """
    Upload image to Telegraph (fallback option)
    """
    try:
        # Use the synchronous telegraph upload function
        result = upload_file(file_path)
        if isinstance(result, list) and len(result) > 0:
            return f"https://telegra.ph{result[0]}"
        else:
            raise Exception("Telegraph upload failed")
    except Exception as e:
        raise Exception(f"Telegraph upload error: {str(e)}")

async def upload_to_catbox(file_path):
    """
    Upload image to Catbox (secondary fallback option)
    """
    url = "https://catbox.moe/user/api.php"
    
    # Read the file
    with open(file_path, "rb") as file:
        file_data = file.read()
    
    # Create form data
    data = aiohttp.FormData()
    data.add_field('reqtype', 'fileupload')
    data.add_field('fileToUpload', file_data, filename=os.path.basename(file_path))
    
    async with aiohttp.ClientSession() as session:
        async with session.post(url, data=data) as response:
            if response.status == 200:
                return (await response.text()).strip()
            else:
                raise Exception(f"Catbox upload failed with status {response.status}")

async def upload_image_with_fallback(file_path):
    """
    Try multiple image hosting services with fallback - imgBB as primary
    """
    services = [
        upload_to_imgbb,  # Primary - imgBB
        upload_to_telegraph,  # First fallback - Telegraph
        upload_to_catbox,  # Second fallback - Catbox
    ]
    
    last_error = None
    for service in services:
        try:
            print(f"Trying {service.__name__}...")
            url = await service(file_path)
            print(f"Success with {service.__name__}: {url}")
            return url
        except Exception as e:
            print(f"Failed with {service.__name__}: {str(e)}")
            last_error = e
            continue
    
    raise Exception(f"All image hosting services failed. Last error: {str(last_error)}")



def check_file_size(file_path, max_size_mb=30):
    """
    Check if file size is within limits
    """
    file_size = os.path.getsize(file_path)
    if file_size > max_size_mb * 1024 * 1024:
        raise Exception(f"File size ({file_size/1024/1024:.2f} MB) exceeds the {max_size_mb} MB limit.")
    return True

async def find_available_id():
    """
    Find the next available ID for a character
    """
    async with id_lock:
        cursor = collection.find().sort('id', 1)
        ids = [doc['id'] for doc in await cursor.to_list(length=None)]
        
        # Handle case where no documents exist
        if not ids:
            candidate_id = "01"
            active_ids.add(candidate_id)
            return candidate_id
        
        # Convert to integers for proper comparison
        int_ids = [int(id) for id in ids]
        
        for i in range(1, max(int_ids) + 2):
            candidate_id = str(i).zfill(2)
            if candidate_id not in ids and candidate_id not in active_ids:
                active_ids.add(candidate_id)
                return candidate_id
        return str(max(int_ids) + 1).zfill(2)

async def find_available_ids():
    """
    Find available IDs without reserving them
    """
    async with id_lock:
        cursor = collection.find().sort('id', 1)
        ids = [doc['id'] for doc in await cursor.to_list(length=None)]
        
        # Handle case where no documents exist
        if not ids:
            return "01"
        
        # Convert to integers for proper comparison
        int_ids = [int(id) for id in ids]
        
        for i in range(1, max(int_ids) + 2):
            candidate_id = str(i).zfill(2)
            if candidate_id not in ids and candidate_id not in active_ids:
                return candidate_id
        return str(max(int_ids) + 1).zfill(2)

@shivuu.on_message(filters.command(["uid"]) & uploader_filter)
async def ulo(client, message):
    """
    Command to get the next available ID
    """
    available_id = await find_available_ids()
    await client.send_message(chat_id=message.chat.id, text=f"{available_id}")

@shivuu.on_message(filters.command(["upload"]) & uploader_filter)
async def ul(client, message):
    """
    Command to upload character information
    """
    reply = message.reply_to_message
    if not reply or not (reply.photo or reply.document):
        await message.reply_text("Please reply to a photo or document.")
        return
        
    args = message.text.split()
    if len(args) != 4:
        await client.send_message(chat_id=message.chat.id, text=WRONG_FORMAT_TEXT)
        return
    
    # Extract character details from the command arguments
    character_name = args[1].replace('-', ' ').title()
    anime = args[2].replace('-', ' ').title()
    
    try:
        rarity = int(args[3])
    except ValueError:
        await message.reply_text("Rarity must be a number.")
        return
    
    # Validate rarity value
    if rarity not in RARITY_MAP:
        await message.reply_text("Invalid rarity value. Please use a valid rarity number.")
        return
    
    rarity_text = RARITY_MAP[rarity][1]  # Get the text from tuple
    available_id = None
    
    try:
        available_id = await find_available_id()
        processing_message = await message.reply("<ᴘʀᴏᴄᴇꜱꜱɪɴɢ>....")
        
        # Download the file
        path = await reply.download()
        
        # Check file size
        check_file_size(path)
        
        # Prepare character data
        character = {
            'name': character_name,
            'anime': anime,
            'rarity': rarity_text,
            'id': available_id,
            'slock': "false",
            'added': message.from_user.id
        }

        # Upload image with fallback (imgBB as primary)
        image_url = await upload_image_with_fallback(path)
        character['img_url'] = image_url
        
        # Insert character into the database
        await collection.insert_one(character)

        # Send character details to the channel
        caption = (
            f"🌟 **Character Detail** 🌟\n"
            f"\n━━━━━━━━━━━━━━━━━━\n"
            f"🔹 **Name:** {character_name}\n"
            f"🔸 **Anime:** {anime}\n"
            f"🔹 **ID:** {available_id}\n"
            f"🔸 **Rarity:** {rarity_text}\n"
            f"Added by [{message.from_user.first_name}](tg://user?id={message.from_user.id})\n"
            f"\n━━━━━━━━━━━━━━━━━━\n"
        )
        
        # Try to send with the uploaded URL first
        try:
            if path.lower().endswith(('.mp4', '.mov', '.avi', '.mkv', '.gif')):
                tempo = await client.send_video(
                    chat_id=CHARA_CHANNEL_ID,
                    video=image_url,
                    caption=caption,
                )
            else:
                tempo = await client.send_photo(
                    chat_id=CHARA_CHANNEL_ID,
                    photo=image_url,
                    caption=caption,
                )
        except:
            # Fallback to sending the local file if URL doesn't work
            if path.lower().endswith(('.mp4', '.mov', '.avi', '.mkv', '.gif')):
                tempo = await client.send_video(
                    chat_id=CHARA_CHANNEL_ID,
                    video=path,
                    caption=caption,
                )
            else:
                tempo = await client.send_photo(
                    chat_id=CHARA_CHANNEL_ID,
                    photo=path,
                    caption=caption,
                )
            
        await tempo.pin()
        
        await message.reply_text(f'✅ CHARACTER ADDED SUCCESSFULLY! ID: {available_id}')
        await client.send_message(chat_id=CHARA_CHANNEL_ID, text=f' @naruto_dev `/sendone {available_id}')
        
    except Exception as e:
        error_msg = f"❌ Character Upload Unsuccessful. Error: {str(e)}"
        await message.reply_text(error_msg)
        print(error_msg)  # Log the error for debugging
    
    finally:
        # Clean up
        if 'path' in locals() and os.path.exists(path):
            os.remove(path)
        if available_id:
            async with id_lock:
                active_ids.discard(available_id)

@app.on_message(filters.command('delete') & sudo_filter)
async def delete(client: Client, message: Message):
    args = message.text.split(maxsplit=1)[1:]
    if len(args) != 1:
        await message.reply_text('Incorrect format... Please use: /delete ID')
        return

    character_id = args[0]
    character = await collection.find_one_and_delete({'id': character_id})
   
    if character:
        bulk_operations = []
        async for user in user_collection.find():
            if 'characters' in user:
                user['characters'] = [char for char in user['characters'] if char['id'] != character_id]
                bulk_operations.append(
                    UpdateOne({'_id': user['_id']}, {'$set': {'characters': user['characters']}})
                )

        if bulk_operations:
            await user_collection.bulk_write(bulk_operations)

        await message.reply_text('Character deleted from database and all user collections.')
    else:
        await message.reply_text('Character not found in database.')

async def check_total_characters(update: Update, context: CallbackContext) -> None:
    try:
        total_characters = await collection.count_documents({})
        await update.message.reply_text(f"Total number of characters: {total_characters}")
    except Exception as e:
        await update.message.reply_text(f"Error occurred: {e}")

async def check(update: Update, context: CallbackContext) -> None:    
    try:
        args = context.args
        if len(context.args) != 1:
            await update.message.reply_text('Incorrect format. Please use: /check id')
            return
            
        character_id = context.args[0]
        character = await collection.find_one({'id': args[0]}) 
            
        if character:
            # If character found, send the information along with the image URL
            message = f"<b>Character Name:</b> {character['name']}\n" \
                      f"<b>Anime Name:</b> {character['anime']}\n" \
                      f"<b>Rarity:</b> {character['rarity']}\n" \
                      f"<b>ID:</b> {character['id']}\n"

            if 'img_url' in character:
                await context.bot.send_photo(chat_id=update.effective_chat.id,
                                             photo=character['img_url'],
                                             caption=message,
                                             parse_mode='HTML')
            elif 'vid_url' in character:
                await context.bot.send_video(chat_id=update.effective_chat.id,
                                             video=character['vid_url'],
                                             caption=message,
                                             parse_mode='HTML')
        else:
             await update.message.reply_text("Character not found.")
    except Exception as e:
        await update.message.reply_text(f"Error occurred: {e}")

application.add_handler(CommandHandler("total", check_total_characters))

@app.on_message(filters.command('update') & uploader_filter)
async def update(client: Client, message: Message):
    args = message.text.split(maxsplit=3)[1:]
    if len(args) != 3:
        await message.reply_text('Incorrect format. Please use: /update id field new_value')
        return

    character_id = args[0]
    field = args[1]
    new_value = args[2]

    character = await collection.find_one({'id': character_id})
    if not character:
        await message.reply_text('Character not found.')
        return

    valid_fields = ['img_url', 'name', 'anime', 'rarity']
    if field not in valid_fields:
        await message.reply_text(f'Invalid field. Please use one of the following: {", ".join(valid_fields)}')
        return

    if field in ['name', 'anime']:
        new_value = new_value.replace('-', ' ').title()
    elif field == 'rarity':
        try:
            new_value = RARITY_MAP[int(new_value)][1]  # Get the text from tuple
        except KeyError:
            await message.reply_text('Invalid rarity. Please use a number between 1 and 15.')
            return

    await collection.update_one({'id': character_id}, {'$set': {field: new_value}})
    
    bulk_operations = []
    async for user in user_collection.find():
        if 'characters' in user:
            for char in user['characters']:
                if char['id'] == character_id:
                    char[field] = new_value
            bulk_operations.append(
                UpdateOne({'_id': user['_id']}, {'$set': {'characters': user['characters']}})
            )

    if bulk_operations:
        await user_collection.bulk_write(bulk_operations)

    await message.reply_text('Update done in Database and all user collections.')

@app.on_message(filters.command('r') & sudo_filter)
async def update_rarity(client: Client, message: Message):
    args = message.text.split(maxsplit=2)[1:]
    if len(args) != 2:
        await message.reply_text('Incorrect format. Please use: /r id rarity')
        return

    character_id = args[0]
    new_rarity = args[1]

    character = await collection.find_one({'id': character_id})
    if not character:
        await message.reply_text('Character not found.')
        return

    try:
        new_rarity_value = RARITY_MAP[int(new_rarity)][1]  # Get the text from tuple
    except KeyError:
        await message.reply_text('Invalid rarity. Please use a number between 1 and 15.')
        return

    await collection.update_one({'id': character_id}, {'$set': {'rarity': new_rarity_value}})

    bulk_operations = []
    async for user in user_collection.find():
        if 'characters' in user:
            for char in user['characters']:
                if char['id'] == character_id:
                    char['rarity'] = new_rarity_value
            bulk_operations.append(
                UpdateOne({'_id': user['_id']}, {'$set': {'characters': user['characters']}})
            )

    if bulk_operations:
        await user_collection.bulk_write(bulk_operations)

    await message.reply_text('Rarity updated in Database and all user collections.')

@app.on_message(filters.command('arrange') & sudo_filter)
async def arrange_characters(client: Client, message: Message):
    characters = await collection.find().sort('id', 1).to_list(length=None)
    if not characters:
        await message.reply_text('No characters found in the database.')
        return

    old_to_new_id_map = {}
    new_id_counter = 1

    bulk_operations = []
    for character in characters:
        old_id = character['id']
        new_id = str(new_id_counter).zfill(2)
        old_to_new_id_map[old_id] = new_id

        if old_id != new_id:
            bulk_operations.append(
                UpdateOne({'_id': character['_id']}, {'$set': {'id': new_id}})
            )
        new_id_counter += 1

    if bulk_operations:
        await collection.bulk_write(bulk_operations)

    user_bulk_operations = []
    async for user in user_collection.find():
        if 'characters' in user:
            for char in user['characters']:
                if char['id'] in old_to_new_id_map:
                    char['id'] = old_to_new_id_map[char['id']]
            user_bulk_operations.append(
                UpdateOne({'_id': user['_id']}, {'$set': {'characters': user['characters']}})
            )

    if user_bulk_operations:
        await user_collection.bulk_write(user_bulk_operations)

    await message.reply_text('Characters have been rearranged and IDs updated successfully.')

CHECK_HANDLER = CommandHandler('f', check, block=False)
application.add_handler(CHECK_HANDLER)

@shivuu.on_message(filters.command("vadd") & uploader_filter)
async def upload_video_character(client, message):
    args = message.text.split(maxsplit=3)
    if len(args) != 4:
        await message.reply_text("Wrong format. Use: /vadd character-name anime-name video-url")
        return

    character_name = args[1].replace('-', ' ').title()
    anime = args[2].replace('-', ' ').title()
    vid_url = args[3]

    # Generate the next available ID
    available_id = await find_available_id()

    character = {
        'name': character_name,
        'anime': anime,
        'rarity': "🎗️ 𝘼𝙈𝙑 𝙀𝙙𝙞𝙩𝙞𝙤𝙣",
        'id': available_id,
        'vid_url': vid_url,
        'slock': "false",
        'added': message.from_user.id
    }

    try:
        # Send the video to the character channel
        await client.send_video(
            chat_id=-1003295207951,
            video=vid_url,
            caption=(
                f"🎥 **New Character Added** 🎥\n\n"
                f"Character Name: {character_name}\n"
                f"Anime Name: {anime}\n"
                f"Rarity: '🎗️ 𝘼𝙈𝙑 𝙀𝙙𝙞𝙩𝙞𝙤𝙣'\n"
                f"ID: {available_id}\n"
                f"Added by [{message.from_user.first_name}](tg://user?id={message.from_user.id})"
            ),
        )

        # Insert the character data into MongoDB
        await collection.insert_one(character)

        await message.reply_text("✅ Video character added successfully.")
    except Exception as e:
        await message.reply_text(f"❌ Failed to upload character. Error: {e}")



@shivuu.on_message(filters.command(["updateimg"]) & uploader_filter)
async def update_image(client, message):
    """
    Command to update character image by replying to a photo with the character ID
    Format: /updateimg [character_id]
    """
    reply = message.reply_to_message
    if not reply or not (reply.photo or reply.document):
        await message.reply_text("Please reply to a photo or document with this command.")
        return
        
    args = message.text.split()
    if len(args) != 2:
        await message.reply_text("Wrong format. Use: /updateimg [character_id] (reply to image)")
        return
    
    character_id = args[1]
    
    # Check if character exists
    character = await collection.find_one({'id': character_id})
    if not character:
        await message.reply_text(f"Character with ID {character_id} not found.")
        return
    
    try:
        processing_message = await message.reply("<ᴜᴘᴅᴀᴛɪɴɢ ɪᴍᴀɢᴇ...>")
        
        # Download the new image
        path = await reply.download()
        
        # Check file size
        check_file_size(path)
        
        # Upload image with fallback (imgBB as primary)
        image_url = await upload_image_with_fallback(path)
        
        # Update character in the database
        await collection.update_one(
            {'id': character_id}, 
            {'$set': {'img_url': image_url}}
        )
        
        # Update all user collections that have this character
        bulk_operations = []
        async for user in user_collection.find():
            if 'characters' in user:
                for char in user['characters']:
                    if char['id'] == character_id:
                        char['img_url'] = image_url
                bulk_operations.append(
                    UpdateOne({'_id': user['_id']}, {'$set': {'characters': user['characters']}})
                )

        if bulk_operations:
            await user_collection.bulk_write(bulk_operations)
        
        # Send confirmation message
        await message.reply_text(f'✅ Image updated successfully for character ID: {character_id}')
        
        # Send updated character info to channel
        caption = (
            f"🔄 **Character Image Updated** 🔄\n"
            f"\n━━━━━━━━━━━━━━━━━━\n"
            f"🔹 **Name:** {character['name']}\n"
            f"🔸 **Anime:** {character['anime']}\n"
            f"🔹 **ID:** {character_id}\n"
            f"🔸 **Rarity:** {character['rarity']}\n"
            f"Image updated by [{message.from_user.first_name}](tg://user?id={message.from_user.id})\n"
            f"\n━━━━━━━━━━━━━━━━━━\n"
        )
        
        # Try to send with the uploaded URL
        try:
            if path.lower().endswith(('.mp4', '.mov', '.avi', '.mkv', '.gif')):
                await client.send_video(
                    chat_id-1003295207951,
                    video=image_url,
                    caption=caption,
                )
            else:
                await client.send_photo(
                    chat_id=-1003295207951,
                    photo=image_url,
                    caption=caption,
                )
        except:
            # Fallback to sending the local file if URL doesn't work
            if path.lower().endswith(('.mp4', '.mov', '.avi', '.mkv', '.gif')):
                await client.send_video(
                    chat_id=-1003295207951,
                    video=path,
                    caption=caption,
                )
            else:
                await client.send_photo(
                    chat_id=-1003295207951,
                    photo=path,
                    caption=caption,
                )
                
    except Exception as e:
        error_msg = f"❌ Image update failed. Error: {str(e)}"
        await message.reply_text(error_msg)
        print(error_msg)  # Log the error for debugging
    
    finally:
        # Clean up
        if 'path' in locals() and os.path.exists(path):
            os.remove(path)
