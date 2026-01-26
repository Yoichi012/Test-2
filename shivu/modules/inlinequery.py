import re
import time
from html import escape
from cachetools import TTLCache
from pymongo import ASCENDING

from telegram import Update, InlineQueryResultPhoto
from telegram.ext import InlineQueryHandler, CallbackContext 
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from shivu import user_collection, collection, application, db

# --- Rarity Mapping ---
RARITY_MAP = {
    1: "⚪ ᴄᴏᴍᴍᴏɴ", 2: "🔵 ʀᴀʀᴇ", 3: "🟡 ʟᴇɢᴇɴᴅᴀʀʏ", 4: "💮 ꜱᴘᴇᴄɪᴀʟ",
    5: "👹 ᴀɴᴄɪᴇɴᴛ", 6: "🎐 ᴄᴇʟᴇꜱᴛɪᴀʟ", 7: "🔮 ᴇᴘɪᴄ", 8: "🪐 ᴄᴏꜱᴍɪᴄ",
    9: "⚰️ ɴɪɢʜᴛᴍᴀʀᴇ", 10: "🌬️ ꜰʀᴏꜱᴛʙᴏʀɴ", 11: "💝 ᴠᴀʟᴇɴᴛɪɴᴇ",
    12: "🌸 ꜱᴘʀɪɴɢ", 13: "🏖️ ᴛʀᴏᴘɪᴄᴀʟ", 14: "🍭 ᴋᴀᴡᴀɪɪ", 15: "🧬 ʜʏʙʀɪᴅ"
}

# --- Small Caps Helper Function ---
def to_small_caps(text):
    """Convert any string to Unicode Small Caps"""
    if not text:
        return ""
    
    small_caps_map = {
        'A': 'ᴀ', 'B': 'ʙ', 'C': 'ᴄ', 'D': 'ᴅ', 'E': 'ᴇ', 'F': 'ꜰ', 'G': 'ɢ', 'H': 'ʜ',
        'I': 'ɪ', 'J': 'ᴊ', 'K': 'ᴋ', 'L': 'ʟ', 'M': 'ᴍ', 'N': 'ɴ', 'O': 'ᴏ', 'P': 'ᴘ',
        'Q': 'ǫ', 'R': 'ʀ', 'S': 'ꜱ', 'T': 'ᴛ', 'U': 'ᴜ', 'V': 'ᴠ', 'W': 'ᴡ', 'X': 'x',
        'Y': 'ʏ', 'Z': 'ᴢ',
        'a': 'ᴀ', 'b': 'ʙ', 'c': 'ᴄ', 'd': 'ᴅ', 'e': 'ᴇ', 'f': 'ꜰ', 'g': 'ɢ', 'h': 'ʜ',
        'i': 'ɪ', 'j': 'ᴊ', 'k': 'ᴋ', 'l': 'ʟ', 'm': 'ᴍ', 'n': 'ɴ', 'o': 'ᴏ', 'p': 'ᴘ',
        'q': 'ǫ', 'r': 'ʀ', 's': 'ꜱ', 't': 'ᴛ', 'u': 'ᴜ', 'v': 'ᴠ', 'w': 'ᴡ', 'x': 'x',
        'y': 'ʏ', 'z': 'ᴢ'
    }
    return ''.join(small_caps_map.get(ch, ch) for ch in str(text))

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
        
        # --- Rarity Display Logic ---
        rarity_value = character.get('rarity')
        rarity_display = to_small_caps("ɴ/ᴀ")  # Default value
        
        if rarity_value is not None:
            try:
                # Check if rarity is an integer
                if isinstance(rarity_value, int) or (isinstance(rarity_value, str) and rarity_value.isdigit()):
                    rarity_int = int(rarity_value)
                    if rarity_int in RARITY_MAP:
                        # Mapping se string nikalo (already small caps format mein hai)
                        rarity_display = RARITY_MAP[rarity_int]
                    else:
                        # Integer hai but map mein nahi hai
                        rarity_display = to_small_caps(str(rarity_value))
                else:
                    # String rarity value hai, directly small caps mein convert karo
                    rarity_display = to_small_caps(str(rarity_value))
            except (ValueError, TypeError):
                # Koi error aaye to default use karo
                rarity_display = to_small_caps("ɴ/ᴀ")
        
        if query.startswith('collection.'):
            user_character_count = sum(1 for c in user['characters'] if c['id'] == character['id'])
            user_anime_characters = sum(1 for c in user['characters'] if c['anime'] == character['anime'])
            
            # User name ko bhi small caps mein convert karo
            user_first_name = user.get('first_name', str(user['id']))
            
            caption = f"✨ {to_small_caps('look at')} {to_small_caps(user_first_name)}'s {to_small_caps('character')}\n\n"
            caption += f"🌸{to_small_caps('name')} : <b>{to_small_caps(character['name'])} (x{user_character_count})</b>\n"
            caption += f"🏖️{to_small_caps('anime')} : <b>{to_small_caps(character['anime'])} ({user_anime_characters}/{anime_characters})</b>\n"
            caption += f"🏵️ {to_small_caps('rarity')} : <b>{rarity_display}</b>\n"
            caption += f"🆔️ {to_small_caps('id')} : <b>{character['id']}</b>"
        else:
            caption = f"✨ {to_small_caps('look at this character !!')}\n\n"
            caption += f"🌸{to_small_caps('name')} : <b>{to_small_caps(character['name'])}</b>\n"
            caption += f"🏖️{to_small_caps('anime')} : <b>{to_small_caps(character['anime'])}</b>\n"
            caption += f"🏵️ {to_small_caps('rarity')} : <b>{rarity_display}</b>\n"
            caption += f"🆔️ {to_small_caps('id')} : <b>{character['id']}</b>\n\n"
            caption += f"{to_small_caps('globally guessed')} {global_count} {to_small_caps('times...')}"

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