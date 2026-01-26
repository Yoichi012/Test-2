import re
import time
from html import escape as html_escape
from typing import List, Dict, Any, Optional
from cachetools import TTLCache
from pymongo import ASCENDING
from telegram import Update, InlineQueryResultPhoto
from telegram.ext import InlineQueryHandler, CallbackContext
from shivu import user_collection, collection, application, db


def safe_escape(value: Any, default: str = "") -> str:
    """
    Safely escape any value for HTML.
    Converts integers to strings, handles None values.
    """
    if value is None:
        value = default
    # Convert to string regardless of type
    return html_escape(str(value))


# Create indexes for optimal query performance
db.characters.create_index([('id', ASCENDING)])
db.characters.create_index([('anime', ASCENDING)])
db.characters.create_index([('name', ASCENDING)])
db.characters.create_index([('img_url', ASCENDING)])
db.user_collection.create_index([('id', ASCENDING)])
db.user_collection.create_index([('characters.id', ASCENDING)])
db.user_collection.create_index([('characters.anime', ASCENDING)])

# Caches with appropriate TTLs
user_collection_cache = TTLCache(maxsize=10000, ttl=60)


async def build_global_search_pipeline(
    query: str = "",
    offset: int = 0,
    limit: int = 50
) -> List[Dict]:
    """Build aggregation pipeline for global character search."""
    pipeline = []
    
    # Stage 1: Match characters based on search query
    if query:
        escaped_query = re.escape(query)
        regex_filter = re.compile(escaped_query, re.IGNORECASE)
        pipeline.append({
            "$match": {
                "$or": [
                    {"name": regex_filter},
                    {"anime": regex_filter}
                ]
            }
        })
    
    # Stage 2: Join with user_collection to get global counts
    pipeline.extend([
        {
            "$lookup": {
                "from": "user_collection",
                "let": {"character_id": "$id"},
                "pipeline": [
                    {"$unwind": "$characters"},
                    {"$match": {"$expr": {"$eq": ["$characters.id", "$$character_id"]}}},
                    {"$count": "count"}
                ],
                "as": "global_count_array"
            }
        },
        {
            "$lookup": {
                "from": "characters",
                "let": {"anime_name": "$anime"},
                "pipeline": [
                    {"$match": {"$expr": {"$eq": ["$anime", "$$anime_name"]}}},
                    {"$count": "count"}
                ],
                "as": "anime_total_array"
            }
        },
        {
            "$addFields": {
                "global_count": {
                    "$cond": {
                        "if": {"$gt": [{"$size": "$global_count_array"}, 0]},
                        "then": {"$arrayElemAt": ["$global_count_array.count", 0]},
                        "else": 0
                    }
                },
                "anime_total": {
                    "$cond": {
                        "if": {"$gt": [{"$size": "$anime_total_array"}, 0]},
                        "then": {"$arrayElemAt": ["$anime_total_array.count", 0]},
                        "else": 0
                    }
                }
            }
        },
        {"$unset": ["global_count_array", "anime_total_array"]}
    ])
    
    # Stage 3: Pagination
    pipeline.extend([
        {"$skip": offset},
        {"$limit": limit + 1}  # +1 to check if there are more results
    ])
    
    return pipeline


async def build_user_collection_pipeline(
    user_id: int,
    search_terms: str = "",
    offset: int = 0,
    limit: int = 50
) -> List[Dict]:
    """Build aggregation pipeline for user collection search."""
    pipeline = []
    
    # Stage 1: Get the specific user
    pipeline.append({
        "$match": {"id": user_id}
    })
    
    # Stage 2: Unwind user's characters and deduplicate by character id
    pipeline.extend([
        {"$unwind": "$characters"},
        {
            "$group": {
                "_id": "$characters.id",
                "user": {"$first": "$$ROOT"},
                "character": {"$first": "$characters"},
                "user_character_count": {"$sum": 1}
            }
        },
        {"$replaceRoot": {"newRoot": {
            "user": "$user",
            "character": "$character",
            "user_character_count": "$user_character_count"
        }}}
    ])
    
    # Stage 3: Join with characters collection to get full character details
    pipeline.extend([
        {
            "$lookup": {
                "from": "characters",
                "localField": "character.id",
                "foreignField": "id",
                "as": "character_details"
            }
        },
        {"$unwind": "$character_details"},
        {
            "$addFields": {
                "character": {
                    "$mergeObjects": [
                        "$character",
                        {
                            "name": "$character_details.name",
                            "anime": "$character_details.anime",
                            "rarity": "$character_details.rarity",
                            "img_url": "$character_details.img_url"
                        }
                    ]
                }
            }
        },
        {"$unset": ["character_details"]}
    ])
    
    # Stage 4: Apply search filter if provided
    if search_terms:
        escaped_search = re.escape(search_terms)
        regex_filter = re.compile(escaped_search, re.IGNORECASE)
        pipeline.append({
            "$match": {
                "$or": [
                    {"character.name": regex_filter},
                    {"character.anime": regex_filter}
                ]
            }
        })
    
    # Stage 5: Calculate user's anime character count and global anime total
    pipeline.extend([
        {
            "$lookup": {
                "from": "user_collection",
                "let": {
                    "user_id": "$user.id",
                    "anime_name": "$character.anime"
                },
                "pipeline": [
                    {"$match": {"$expr": {"$eq": ["$id", "$$user_id"]}}},
                    {"$unwind": "$characters"},
                    {"$match": {"$expr": {"$eq": ["$characters.anime", "$$anime_name"]}}},
                    {"$count": "count"}
                ],
                "as": "user_anime_count_array"
            }
        },
        {
            "$lookup": {
                "from": "characters",
                "let": {"anime_name": "$character.anime"},
                "pipeline": [
                    {"$match": {"$expr": {"$eq": ["$anime", "$$anime_name"]}}},
                    {"$count": "count"}
                ],
                "as": "anime_total_array"
            }
        },
        {
            "$addFields": {
                "user_anime_count": {
                    "$cond": {
                        "if": {"$gt": [{"$size": "$user_anime_count_array"}, 0]},
                        "then": {"$arrayElemAt": ["$user_anime_count_array.count", 0]},
                        "else": 0
                    }
                },
                "anime_total": {
                    "$cond": {
                        "if": {"$gt": [{"$size": "$anime_total_array"}, 0]},
                        "then": {"$arrayElemAt": ["$anime_total_array.count", 0]},
                        "else": 0
                    }
                }
            }
        },
        {"$unset": ["user_anime_count_array", "anime_total_array", "user"]}
    ])
    
    # Stage 6: Pagination
    pipeline.extend([
        {"$skip": offset},
        {"$limit": limit + 1}  # +1 to check if there are more results
    ])
    
    return pipeline


async def execute_aggregation_pipeline(
    collection_name: str,
    pipeline: List[Dict],
    limit: int
) -> tuple[List[Dict], str]:
    """Execute aggregation pipeline and handle pagination."""
    cursor = collection_name.aggregate(pipeline, allowDiskUse=True)
    results = await cursor.to_list(length=limit + 1)
    
    # Determine next offset
    if len(results) > limit:
        results = results[:limit]
        next_offset = "has_more"
    else:
        next_offset = ""
    
    return results, next_offset


async def inlinequery(update: Update, context: CallbackContext) -> None:
    """Handle inline queries with optimized MongoDB aggregation pipelines."""
    query = update.inline_query.query.strip()
    offset = int(update.inline_query.offset) if update.inline_query.offset else 0
    
    user_id = None
    search_terms = ""
    results = []
    next_offset = ""
    
    # Parse query to determine search type
    if query.startswith('collection.'):
        # User collection search
        parts = query.split(' ', 1)
        user_part = parts[0]
        search_terms = parts[1] if len(parts) > 1 else ""
        
        user_id_str = user_part.split('.')[1] if len(user_part.split('.')) > 1 else ""
        
        if user_id_str.isdigit():
            user_id = int(user_id_str)
            
            # Check cache first
            cache_key = f"user_{user_id}"
            if cache_key in user_collection_cache:
                # Still need to execute aggregation for counts
                pass
            
            # Build and execute aggregation pipeline
            pipeline = await build_user_collection_pipeline(
                user_id=user_id,
                search_terms=search_terms,
                offset=offset,
                limit=50
            )
            
            aggregated_results, next_offset_flag = await execute_aggregation_pipeline(
                user_collection,
                pipeline,
                limit=50
            )
            
            if not aggregated_results:
                await update.inline_query.answer([], next_offset="", cache_time=1)
                return
            
            # Convert pipeline results to character format
            characters = []
            for agg_result in aggregated_results:
                character = agg_result["character"]
                character["user_character_count"] = agg_result.get("user_character_count", 0)
                character["user_anime_count"] = agg_result.get("user_anime_count", 0)
                character["anime_total"] = agg_result.get("anime_total", 0)
                characters.append(character)
            
            # Cache user data if not already cached
            if cache_key not in user_collection_cache:
                user_data = await user_collection.find_one({'id': user_id})
                if user_data:
                    user_collection_cache[cache_key] = user_data
            
            # Build results
            current_time = time.time()
            for character in characters:
                # Escape all user-provided text for HTML safety using safe_escape
                user_data = user_collection_cache.get(cache_key, {})
                user_name = safe_escape(user_data.get('first_name', str(user_id)))
                char_name = safe_escape(character.get('name', ''))
                char_anime = safe_escape(character.get('anime', ''))
                char_rarity = safe_escape(character.get('rarity', ''))
                
                caption = (
                    f"<b> Look At <a href='tg://user?id={user_id}'>{user_name}</a>'s Character</b>\n\n"
                    f"🌸: <b>{char_name} (x{character.get('user_character_count', 0)})</b>\n"
                    f"🏖️: <b>{char_anime} ({character.get('user_anime_count', 0)}/{character.get('anime_total', 0)})</b>\n"
                    f"<b>{char_rarity}</b>\n\n"
                    f"<b>🆔️:</b> {character['id']}"
                )
                
                results.append(
                    InlineQueryResultPhoto(
                        thumbnail_url=character['img_url'],
                        id=f"{character['id']}_{current_time}_{offset}",
                        photo_url=character['img_url'],
                        caption=caption,
                        parse_mode='HTML'
                    )
                )
            
            next_offset = str(offset + 50) if next_offset_flag else ""
    
    else:
        # Global character search
        pipeline = await build_global_search_pipeline(
            query=query,
            offset=offset,
            limit=50
        )
        
        aggregated_results, next_offset_flag = await execute_aggregation_pipeline(
            collection,
            pipeline,
            limit=50
        )
        
        if not aggregated_results:
            await update.inline_query.answer([], next_offset="", cache_time=1)
            return
        
        # Build results
        current_time = time.time()
        for character in aggregated_results:
            # Escape all character data for HTML safety using safe_escape
            char_name = safe_escape(character.get('name', ''))
            char_anime = safe_escape(character.get('anime', ''))
            char_rarity = safe_escape(character.get('rarity', ''))
            
            caption = (
                f"<b>Look At This Character !!</b>\n\n"
                f"🌸: <b>{char_name}</b>\n"
                f"🏖️: <b>{char_anime}</b>\n"
                f"<b>{char_rarity}</b>\n"
                f"🆔️: <b>{character['id']}</b>\n\n"
                f"<b>Globally Guessed {character.get('global_count', 0)} Times...</b>"
            )
            
            results.append(
                InlineQueryResultPhoto(
                    thumbnail_url=character['img_url'],
                    id=f"{character['id']}_{current_time}_{offset}",
                    photo_url=character['img_url'],
                    caption=caption,
                    parse_mode='HTML'
                )
            )
        
        next_offset = str(offset + 50) if next_offset_flag else ""
    
    # Return results with appropriate pagination
    await update.inline_query.answer(
        results,
        next_offset=next_offset,
        cache_time=5
    )


application.add_handler(InlineQueryHandler(inlinequery, block=False))