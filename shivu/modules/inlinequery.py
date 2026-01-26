import re
import time
from html import escape
from cachetools import TTLCache
from pymongo import ASCENDING

from telegram import Update, InlineQueryResultPhoto
from telegram.ext import InlineQueryHandler, CallbackContext 
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from shivu import user_collection, collection, application, db

# --- Indexing ---
db.characters.create_index([('id', ASCENDING)])
db.characters.create_index([('anime', ASCENDING)])
db.characters.create_index([('img_url', ASCENDING)])

db.user_collection.create_index([('characters.id', ASCENDING)])
db.user_collection.create_index([('characters.name', ASCENDING)])
db.user_collection.create_index([('characters.img_url', ASCENDING)])

# --- Cache Fixing ---
# Global cache ko 10 ghante se ghata kar 2 minute (120s) kar diya taaki deleted characters jaldi update hon
all_characters_cache = TTLCache(maxsize=10000, ttl=120)
# User cache ko 5 second kar diya taaki collection update turant dikhe
user_collection_cache = TTLCache(maxsize=10000, ttl=5)

async def inlinequery(update: Update, context: CallbackContext) -> None:
    query = update.inline_query.query
    offset = int(update.inline_query.offset) if update.inline_query.offset else 0

    if query.startswith('collection.'):
        user_id_str = query.split(' ')[0].split('.')[1]
        search_terms = ' '.join(query.split(' ')[1:])
        
        if user_id_str.isdigit():
            user_id = int(user_id_str)
            # Fresh check in cache
            if user_id_str in user_collection_cache:
                user = user_collection_cache[user_id_str]
            else:
                user = await user_collection.find_one({'id': user_id})
                if user:
                    user_collection_cache[user_id_str] = user

            if user and 'characters' in user:
                # Sirf unique ids nikalna
                all_characters = list({v['id']:v for v in user['characters']}.values())
                if search_terms:
                    # re.escape use kiya taaki special characters search crash na karein
                    regex = re.compile(re.escape(search_terms), re.IGNORECASE)
                    all_characters = [character for character in all_characters if regex.search(character['name']) or regex.search(character['anime'])]
            else:
                all_characters = []
        else:
            all_characters = []
    else:
        if query:
            regex = re.compile(re.escape(query), re.IGNORECASE)
            all_characters = list(await collection.find({"$or": [{"name": regex}, {"anime": regex}]}).to_list(length=None))
        else:
            if 'all_characters' in all_characters_cache:
                all_characters = all_characters_cache['all_characters']
            else:
                all_characters = list(await collection.find({}).to_list(length=None))
                all_characters_cache['all_characters'] = all_characters

    # Pagination Logic
    characters = all_characters[offset:offset+50]
    next_offset = str(offset + 50) if len(all_characters) > offset + 50 else ""

    results = []
    for character in characters:
        # Extra safety: Check if character still exists in global DB
        db_char = await collection.find_one({'id': character['id']})
        if not db_char:
            continue

        global_count = await user_collection.count_documents({'characters.id': character['id']})
        anime_characters = await collection.count_documents({'anime': character['anime']})

        if query.startswith('collection.'):
            user_character_count = sum(1 for c in user['characters'] if c['id'] == character['id'])
            user_anime_characters = sum(1 for c in user['characters'] if c['anime'] == character['anime'])
            caption = f"<b> Look At <a href='tg://user?id={user['id']}'>{(escape(user.get('first_name', str(user['id']))))}</a>'s Character</b>\n\n🌸: <b>{character['name']} (x{user_character_count})</b>\n🏖️: <b>{character['anime']} ({user_anime_characters}/{anime_characters})</b>\n<b>{character.get('rarity', 'N/A')}</b>\n\n<b>🆔️:</b> {character['id']}"
        else:
            caption = f"<b>Look At This Character !!</b>\n\n🌸:<b> {character['name']}</b>\n🏖️: <b>{character['anime']}</b>\n<b>{character.get('rarity', 'N/A')}</b>\n🆔️: <b>{character['id']}</b>\n\n<b>Globally Guessed {global_count} Times...</b>"
        
        results.append(
            InlineQueryResultPhoto(
                thumbnail_url=character['img_url'],
                id=f"{character['id']}_{time.time()}",
                photo_url=character['img_url'],
                caption=caption,
                parse_mode='HTML'
            )
        )

    # cache_time=0 set kiya hai taaki Telegram purane results store na kare
    await update.inline_query.answer(results, next_offset=next_offset, cache_time=0)

application.add_handler(InlineQueryHandler(inlinequery, block=False))
