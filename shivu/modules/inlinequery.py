import re
import time
from html import escape
from cachetools import TTLCache
from pymongo import ASCENDING

from telegram import Update, InlineQueryResultPhoto, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import InlineQueryHandler, CallbackContext

from shivu import user_collection, collection, application, db

# --- Database Indexing ---
def setup_indexes():
    db.characters.create_index([('id', ASCENDING)])
    db.characters.create_index([('anime', ASCENDING)])
    db.characters.create_index([('img_url', ASCENDING)])

    db.user_collection.create_index([('characters.id', ASCENDING)])
    db.user_collection.create_index([('characters.name', ASCENDING)])
    db.user_collection.create_index([('characters.img_url', ASCENDING)])

setup_indexes()

# --- Caching ---
all_characters_cache = TTLCache(maxsize=10000, ttl=36000)
user_collection_cache = TTLCache(maxsize=10000, ttl=60)

async def inlinequery(update: Update, context: CallbackContext) -> None:
    query = update.inline_query.query
    offset = int(update.inline_query.offset) if update.inline_query.offset else 0
    limit = 50

    all_characters = []
    user = None

    # 1. Logic for 'collection.user_id'
    if query.startswith('collection.'):
        parts = query.split(' ')
        meta_data = parts[0].split('.')
        user_id_str = meta_data[1] if len(meta_data) > 1 else ""
        search_terms = ' '.join(parts[1:])

        if user_id_str.isdigit():
            user_id = int(user_id_str)
            # Cache check
            user = user_collection_cache.get(user_id_str)
            if not user:
                user = await user_collection.find_one({'id': user_id})
                if user:
                    user_collection_cache[user_id_str] = user

            if user and 'characters' in user:
                # Get unique characters based on ID
                unique_chars = {v['id']: v for v in user['characters']}.values()
                all_characters = list(unique_chars)
                
                if search_terms:
                    regex = re.compile(search_terms, re.IGNORECASE)
                    all_characters = [c for c in all_characters if regex.search(c['name']) or regex.search(c['anime'])]
    
    # 2. Global Search Logic
    else:
        if query:
            regex = re.compile(query, re.IGNORECASE)
            all_characters = await collection.find({
                "$or": [{"name": regex}, {"anime": regex}]
            }).to_list(length=None)
        else:
            if 'all_characters' in all_characters_cache:
                all_characters = all_characters_cache['all_characters']
            else:
                all_characters = await collection.find({}).to_list(length=None)
                all_characters_cache['all_characters'] = all_characters

    # --- Pagination ---
    characters_slice = all_characters[offset : offset + limit]
    next_offset = str(offset + limit) if len(all_characters) > offset + limit else ""

    results = []
    for character in characters_slice:
        # Note: These DB hits inside loop are heavy, but kept as per your logic
        global_count = await user_collection.count_documents({'characters.id': character['id']})
        anime_characters_total = await collection.count_documents({'anime': character['anime']})

        if query.startswith('collection.') and user:
            user_char_count = sum(1 for c in user['characters'] if c['id'] == character['id'])
            user_anime_count = sum(1 for c in user['characters'] if c['anime'] == character['anime'])
            
            caption = (
                f"<b> Look At <a href='tg://user?id={user['id']}'>{escape(user.get('first_name', str(user['id'])))}</a>'s Character</b>\n\n"
                f"🌸: <b>{character['name']} (x{user_char_count})</b>\n"
                f"🏖️: <b>{character['anime']} ({user_anime_count}/{anime_characters_total})</b>\n"
                f"<b>{character.get('rarity', 'Unknown')}</b>\n\n"
                f"<b>🆔️:</b> {character['id']}"
            )
        else:
            caption = (
                f"<b>Look At This Character !!</b>\n\n"
                f"🌸: <b>{character['name']}</b>\n"
                f"🏖️: <b>{character['anime']}</b>\n"
                f"<b>{character.get('rarity', 'Unknown')}</b>\n"
                f"🆔️: <b>{character['id']}</b>\n\n"
                f"<b>Globally Guessed {global_count} Times...</b>"
            )

        results.append(
            InlineQueryResultPhoto(
                id=f"{character['id']}_{time.time()}",
                photo_url=character['img_url'],
                thumbnail_url=character['img_url'],
                caption=caption,
                parse_mode='HTML'
            )
        )

    await update.inline_query.answer(results, next_offset=next_offset, cache_time=5)

application.add_handler(InlineQueryHandler(inlinequery, block=False))
