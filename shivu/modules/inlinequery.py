import re
import time
from html import escape
from typing import List, Dict, Any, Tuple
from cachetools import TTLCache
from pymongo import MongoClient, ASCENDING
from telegram import Update, InlineQueryResultPhoto
from telegram.ext import InlineQueryHandler, CallbackContext
from shivu import user_collection, collection, application, db


# Create indexes for optimal query performance
db.characters.create_index([('id', ASCENDING)])
db.characters.create_index([('anime', ASCENDING)])
db.characters.create_index([('name', ASCENDING)])
db.characters.create_index([('img_url', ASCENDING)])

db.user_collection.create_index([('id', ASCENDING)])
db.user_collection.create_index([('characters.id', ASCENDING)])
db.user_collection.create_index([('characters.name', ASCENDING)])
db.user_collection.create_index([('characters.img_url', ASCENDING)])

# Caches with appropriate TTLs
user_collection_cache = TTLCache(maxsize=10000, ttl=60)


async def get_character_counts(character_ids: List[int]) -> Dict[int, int]:
    """Get global usage counts for multiple characters in a single query."""
    if not character_ids:
        return {}
    
    pipeline = [
        {"$unwind": "$characters"},
        {"$match": {"characters.id": {"$in": character_ids}}},
        {"$group": {"_id": "$characters.id", "count": {"$sum": 1}}}
    ]
    
    counts = {}
    async for result in user_collection.aggregate(pipeline):
        counts[result["_id"]] = result["count"]
    
    return counts


async def get_anime_counts(anime_names: List[str]) -> Dict[str, int]:
    """Get total character counts per anime in a single query."""
    if not anime_names:
        return {}
    
    pipeline = [
        {"$match": {"anime": {"$in": anime_names}}},
        {"$group": {"_id": "$anime", "count": {"$sum": 1}}}
    ]
    
    counts = {}
    async for result in collection.aggregate(pipeline):
        counts[result["_id"]] = result["count"]
    
    return counts


async def get_user_character_counts(user_characters: List[Dict]) -> Tuple[Dict[int, int], Dict[str, int]]:
    """Count user's characters by ID and anime locally from user data."""
    char_counts = {}
    anime_counts = {}
    
    for char in user_characters:
        char_id = char.get('id')
        anime = char.get('anime')
        
        if char_id:
            char_counts[char_id] = char_counts.get(char_id, 0) + 1
        
        if anime:
            anime_counts[anime] = anime_counts.get(anime, 0) + 1
    
    return char_counts, anime_counts


async def search_characters(
    query: str = "", 
    offset: int = 0, 
    limit: int = 50,
    user_id: int = None
) -> Tuple[List[Dict], str]:
    """
    Search characters with pagination and proper query optimization.
    Returns: (characters_list, next_offset)
    """
    if user_id is not None:
        # User collection search
        user = await user_collection.find_one({'id': user_id})
        if not user or 'characters' not in user:
            return [], ""
        
        # Remove duplicates by ID while preserving order
        unique_chars = []
        seen_ids = set()
        for char in user['characters']:
            char_id = char.get('id')
            if char_id and char_id not in seen_ids:
                seen_ids.add(char_id)
                unique_chars.append(char)
        
        # Apply search filter if provided
        if query:
            escaped_query = re.escape(query)
            regex = re.compile(escaped_query, re.IGNORECASE)
            filtered_chars = [
                char for char in unique_chars
                if regex.search(char.get('name', '')) or regex.search(char.get('anime', ''))
            ]
        else:
            filtered_chars = unique_chars
        
        # Apply pagination
        total = len(filtered_chars)
        start = offset
        end = offset + limit
        characters = filtered_chars[start:end]
        
        # Determine next offset
        next_offset = str(offset + len(characters)) if offset + len(characters) < total else ""
        
        return characters, next_offset
    else:
        # Global character search
        find_filter = {}
        if query:
            escaped_query = re.escape(query)
            regex = re.compile(escaped_query, re.IGNORECASE)
            find_filter = {"$or": [{"name": regex}, {"anime": regex}]}
        
        # Get paginated results directly from database
        cursor = collection.find(find_filter).skip(offset).limit(limit + 1)
        characters = await cursor.to_list(length=limit + 1)
        
        # Determine if there are more results
        if len(characters) > limit:
            characters = characters[:limit]
            next_offset = str(offset + limit)
        else:
            next_offset = ""
        
        return characters, next_offset


async def inlinequery(update: Update, context: CallbackContext) -> None:
    """Handle inline queries with optimized database queries."""
    query = update.inline_query.query.strip()
    offset = int(update.inline_query.offset) if update.inline_query.offset else 0
    
    user_id = None
    search_terms = ""
    user_data = None
    
    # Parse query for user collection searches
    if query.startswith('collection.'):
        parts = query.split(' ', 1)
        user_part = parts[0]
        search_terms = parts[1] if len(parts) > 1 else ""
        
        user_id_str = user_part.split('.')[1] if len(user_part.split('.')) > 1 else ""
        
        if user_id_str.isdigit():
            user_id = int(user_id_str)
            
            # Check cache first
            cache_key = f"user_{user_id}"
            if cache_key in user_collection_cache:
                user_data = user_collection_cache[cache_key]
            else:
                user_data = await user_collection.find_one({'id': user_id})
                if user_data:
                    user_collection_cache[cache_key] = user_data
    
    # Get paginated character results
    characters, next_offset = await search_characters(
        query=search_terms,
        offset=offset,
        limit=50,
        user_id=user_id
    )
    
    if not characters:
        await update.inline_query.answer([], next_offset="", cache_time=1)
        return
    
    # Pre-fetch counts in batches (optimized for database performance)
    character_ids = [char['id'] for char in characters]
    anime_names = [char['anime'] for char in characters]
    
    # Get counts in parallel (if your MongoDB driver supports it)
    global_counts = await get_character_counts(character_ids)
    anime_counts = await get_anime_counts(anime_names)
    
    # For user collection searches, also get user-specific counts
    if user_id and user_data:
        user_char_counts, user_anime_counts = await get_user_character_counts(
            user_data.get('characters', [])
        )
    
    # Build results
    results = []
    current_time = time.time()
    
    for character in characters:
        char_id = character['id']
        anime = character['anime']
        
        # Get pre-computed counts
        global_count = global_counts.get(char_id, 0)
        total_anime_chars = anime_counts.get(anime, 0)
        
        # Build caption based on search type
        if user_id and user_data:
            user_char_count = user_char_counts.get(char_id, 0)
            user_anime_count = user_anime_counts.get(anime, 0)
            
            # Escape all user-provided text for HTML safety
            user_name = escape(user_data.get('first_name', str(user_id)))
            char_name = escape(character.get('name', ''))
            char_anime = escape(character.get('anime', ''))
            char_rarity = escape(character.get('rarity', ''))
            
            caption = (
                f"<b> Look At <a href='tg://user?id={user_id}'>{user_name}</a>'s Character</b>\n\n"
                f"🌸: <b>{char_name} (x{user_char_count})</b>\n"
                f"🏖️: <b>{char_anime} ({user_anime_count}/{total_anime_chars})</b>\n"
                f"<b>{char_rarity}</b>\n\n"
                f"<b>🆔️:</b> {char_id}"
            )
        else:
            # Escape all character data for HTML safety
            char_name = escape(character.get('name', ''))
            char_anime = escape(character.get('anime', ''))
            char_rarity = escape(character.get('rarity', ''))
            
            caption = (
                f"<b>Look At This Character !!</b>\n\n"
                f"🌸: <b>{char_name}</b>\n"
                f"🏖️: <b>{char_anime}</b>\n"
                f"<b>{char_rarity}</b>\n"
                f"🆔️: <b>{char_id}</b>\n\n"
                f"<b>Globally Guessed {global_count} Times...</b>"
            )
        
        # Create inline result
        results.append(
            InlineQueryResultPhoto(
                thumbnail_url=character['img_url'],
                id=f"{char_id}_{current_time}_{offset}",
                photo_url=character['img_url'],
                caption=caption,
                parse_mode='HTML'
            )
        )
    
    # Return results with appropriate pagination
    await update.inline_query.answer(
        results, 
        next_offset=next_offset if next_offset else "", 
        cache_time=5
    )


application.add_handler(InlineQueryHandler(inlinequery, block=False))