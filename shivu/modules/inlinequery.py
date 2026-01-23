import re
import time
from html import escape
from cachetools import TTLCache
from pymongo import MongoClient, ASCENDING

from telegram import Update, InlineQueryResultPhoto
from telegram.ext import InlineQueryHandler, CallbackContext, CommandHandler 
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest

from shivu import user_collection, collection, application, db


# collection
db.characters.create_index([('id', ASCENDING)])
db.characters.create_index([('anime', ASCENDING)])
db.characters.create_index([('img_url', ASCENDING)])

# user_collection
db.user_collection.create_index([('characters.id', ASCENDING)])
db.user_collection.create_index([('characters.name', ASCENDING)])
db.user_collection.create_index([('characters.img_url', ASCENDING)])

all_characters_cache = TTLCache(maxsize=10000, ttl=36000)
user_collection_cache = TTLCache(maxsize=10000, ttl=60)

def is_valid_inline_photo_url(img_url: str) -> bool:
    """Check if image URL is valid for Telegram inline mode with STRICT validation."""
    if not img_url or not isinstance(img_url, str):
        return False
    
    # Skip Telegram file_ids (starts with 'Ag')
    if img_url.startswith('Ag'):
        return False
    
    # Must be a valid HTTP/HTTPS URL
    if not img_url.startswith(('http://', 'https://')):
        return False
    
    # Telegram inline mode ONLY accepts direct image URLs with specific extensions
    # Check for image file extensions (case-insensitive)
    valid_extensions = ('.jpg', '.jpeg', '.png', '.webp')
    img_url_lower = img_url.lower()
    
    # Check if URL ends with valid image extension
    if not img_url_lower.endswith(valid_extensions):
        return False
    
    # Additional check to ensure it's a direct image URL, not a page
    # Skip URLs with query parameters that might redirect
    if '?' in img_url:
        base_url = img_url.split('?')[0]
        if not base_url.lower().endswith(valid_extensions):
            return False
    
    return True

async def inlinequery(update: Update, context: CallbackContext) -> None:
    query = update.inline_query.query
    offset = int(update.inline_query.offset) if update.inline_query.offset else 0

    if query.startswith('collection.'):
        user_id, *search_terms = query.split(' ')[0].split('.')[1], ' '.join(query.split(' ')[1:])
        if user_id.isdigit():
            if user_id in user_collection_cache:
                user = user_collection_cache[user_id]
            else:
                user = await user_collection.find_one({'id': int(user_id)})
                user_collection_cache[user_id] = user

            if user:
                all_characters = list({v['id']:v for v in user['characters']}.values())
                if search_terms:
                    regex = re.compile(' '.join(search_terms), re.IGNORECASE)
                    all_characters = [character for character in all_characters if regex.search(character['name']) or regex.search(character['anime'])]
            else:
                all_characters = []
        else:
            all_characters = []
    else:
        if query:
            regex = re.compile(query, re.IGNORECASE)
            all_characters = list(await collection.find({"$or": [{"name": regex}, {"anime": regex}]}).to_list(length=None))
        else:
            if 'all_characters' in all_characters_cache:
                all_characters = all_characters_cache['all_characters']
            else:
                all_characters = list(await collection.find({}).to_list(length=None))
                all_characters_cache['all_characters'] = all_characters

    # STRICT FILTERING: Only include characters with valid inline image URLs
    valid_characters = []
    for character in all_characters:
        if is_valid_inline_photo_url(character.get('img_url')):
            valid_characters.append(character)
    
    # Calculate pagination based on VALID characters only
    total_valid = len(valid_characters)
    characters = valid_characters[offset:offset+50]
    
    if len(characters) > 50:
        characters = characters[:50]
        next_offset = str(offset + 50) if offset + 50 < total_valid else ""
    else:
        next_offset = str(offset + len(characters)) if offset + len(characters) < total_valid else ""

    results = []
    for character in characters:
        # Double-check URL validity (should already be filtered)
        img_url = character.get('img_url')
        if not is_valid_inline_photo_url(img_url):
            continue
            
        global_count = await user_collection.count_documents({'characters.id': character['id']})
        anime_characters = await collection.count_documents({'anime': character['anime']})

        if query.startswith('collection.'):
            user_character_count = sum(c['id'] == character['id'] for c in user['characters'])
            user_anime_characters = sum(c['anime'] == character['anime'] for c in user['characters'])
            caption = f"<b> Look At <a href='tg://user?id={user['id']}'>{(escape(user.get('first_name', user['id'])))}</a>'s Character</b>\n\n🌸: <b>{character['name']} (x{user_character_count})</b>\n🏖️: <b>{character['anime']} ({user_anime_characters}/{anime_characters})</b>\n<b>{character['rarity']}</b>\n\n<b>🆔️:</b> {character['id']}"
        else:
            caption = f"<b>Look At This Character !!</b>\n\n🌸:<b> {character['name']}</b>\n🏖️: <b>{character['anime']}</b>\n<b>{character['rarity']}</b>\n🆔️: <b>{character['id']}</b>\n\n<b>Globally Guessed {global_count} Times...</b>"
        
        results.append(
            InlineQueryResultPhoto(
                thumbnail_url=img_url,
                id=f"{character['id']}_{time.time()}",
                photo_url=img_url,
                caption=caption,
                parse_mode='HTML'
            )
        )

    try:
        # Only send results if we have valid ones
        if results:
            await update.inline_query.answer(results, next_offset=next_offset, cache_time=5)
        else:
            # Send empty result if no valid images
            await update.inline_query.answer([], next_offset="", cache_time=5)
    except BadRequest as e:
        error_message = str(e).lower()
        if "photo_invalid" in error_message:
            # Final safety net: Send empty results if Telegram still rejects
            await update.inline_query.answer([], next_offset="", cache_time=5)
        else:
            # Re-raise other errors
            raise

application.add_handler(InlineQueryHandler(inlinequery, block=False))