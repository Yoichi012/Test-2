import re
import time
from html import escape
from cachetools import TTLCache
from pymongo import ASCENDING
from telegram import Update, InlineQueryResultPhoto
from telegram.ext import InlineQueryHandler, CallbackContext 
from shivu import user_collection, collection, application, db

# --- Small Caps Helper ---
def to_small_caps(text):
    if not text: return ""
    small_caps_map = {
        'a': 'ᴀ', 'b': 'ʙ', 'c': 'ᴄ', 'd': 'ᴅ', 'e': 'ᴇ', 'f': 'ꜰ', 'g': 'ɢ', 'h': 'ʜ',
        'i': 'ɪ', 'j': 'ᴊ', 'k': 'ᴋ', 'l': 'ʟ', 'm': 'ᴍ', 'n': 'ɴ', 'o': 'ᴏ', 'p': 'ᴘ',
        'q': 'ǫ', 'r': 'ʀ', 's': 'ꜱ', 't': 'ᴛ', 'u': 'ᴜ', 'v': 'ᴠ', 'w': 'ᴡ', 'x': 'x',
        'y': 'ʏ', 'z': 'ᴢ',
        'A': 'ᴀ', 'B': 'ʙ', 'C': 'ᴄ', 'D': 'ᴅ', 'E': 'ᴇ', 'F': 'ꜰ', 'G': 'ɢ', 'H': 'ʜ',
        'I': 'ɪ', 'J': 'ᴊ', 'K': 'ᴋ', 'L': 'ʟ', 'M': 'ᴍ', 'N': 'ɴ', 'O': 'ᴏ', 'P': 'ᴘ',
        'Q': 'ǫ', 'R': 'ʀ', 'S': 'ꜱ', 'T': 'ᴛ', 'U': 'ᴜ', 'V': 'ᴠ', 'W': 'ᴡ', 'X': 'x',
        'Y': 'ʏ', 'Z': 'ᴢ'
    }
    return ''.join(small_caps_map.get(ch, ch) for ch in str(text))

# --- Cache ---
all_characters_cache = TTLCache(maxsize=10000, ttl=120)
user_collection_cache = TTLCache(maxsize=10000, ttl=5)

async def inlinequery(update: Update, context: CallbackContext) -> None:
    query = update.inline_query.query
    offset = int(update.inline_query.offset) if update.inline_query.offset else 0
    results = []

    if query.startswith('collection.'):
        # ... (User collection logic same rahega)
        user_id_str = query.split(' ')[0].split('.')[1]
        search_terms = ' '.join(query.split(' ')[1:])
        if user_id_str.isdigit():
            user_id = int(user_id_str)
            user = await user_collection.find_one({'id': user_id})
            if user and 'characters' in user:
                all_characters = list({v['id']:v for v in user['characters']}.values())
                if search_terms:
                    regex = re.compile(re.escape(search_terms), re.IGNORECASE)
                    all_characters = [c for c in all_characters if regex.search(c['name']) or regex.search(c['anime'])]
            else: all_characters = []
        else: all_characters = []
    else:
        # Global Search Logic
        if query:
            regex = re.compile(re.escape(query), re.IGNORECASE)
            all_characters = await collection.find({"$or": [{"name": regex}, {"anime": regex}]}).to_list(length=None)
        else:
            if 'all_characters' in all_characters_cache:
                all_characters = all_characters_cache['all_characters']
            else:
                all_characters = await collection.find({}).to_list(length=None)
                all_characters_cache['all_characters'] = all_characters

    # Pagination
    characters = all_characters[offset:offset+50]
    next_offset = str(offset + 50) if len(all_characters) > offset + 50 else ""

    for character in characters:
        # optimization: Loop ke andar DB calls kam kar diye hain
        name = to_small_caps(character.get('name', 'Unknown'))
        anime = to_small_caps(character.get('anime', 'Unknown'))
        
        # Rarity cleaning
        r_field = character.get('rarity', 'N/A')
        r_parts = r_field.split(' ', 1)
        r_emoji = r_parts[0] if len(r_parts) > 1 else "🏵️"
        r_name = to_small_caps(r_parts[1] if len(r_parts) > 1 else r_parts[0])

        if query.startswith('collection.'):
            u_name = to_small_caps(user.get('first_name', 'User'))
            caption = (
                f"✨ {to_small_caps('look at')} <b>{u_name}'s</b> {to_small_caps('character')}\n\n"
                f"🌸{to_small_caps('name')} : <b>{name}</b>\n"
                f"🏖️{to_small_caps('anime')} : <b>{anime}</b>\n"
                f"🏵️ {to_small_caps('rarity')} : {r_emoji} <b>{r_name}</b>\n"
                f"🆔️ {to_small_caps('id')} : <b>{character['id']}</b>"
            )
        else:
            caption = (
                f"✨ {to_small_caps('look at this character !!')}\n\n"
                f"🌸{to_small_caps('name')} : <b>{name}</b>\n"
                f"🏖️{to_small_caps('anime')} : <b>{anime}</b>\n"
                f"🏵️ {to_small_caps('rarity')} : {r_emoji} <b>{r_name}</b>\n"
                f"🆔️ {to_small_caps('id')} : <b>{character['id']}</b>"
            )

        results.append(
            InlineQueryResultPhoto(
                thumbnail_url=character['img_url'],
                id=f"{character['id']}_{time.time()}",
                photo_url=character['img_url'],
                caption=caption,
                parse_mode='HTML'
            )
        )

    await update.inline_query.answer(results, next_offset=next_offset, cache_time=0)
