from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, CallbackContext, CallbackQueryHandler
from html import escape
import math
from itertools import groupby
from typing import Dict, List, Tuple
import asyncio

from shivu import collection, user_collection, application


class HaremManager:
    """Manages harem data and operations efficiently."""
    
    @staticmethod
    async def get_user_data_with_valid_characters(user_id: int) -> Tuple[dict, List[dict]]:
        """
        Fetch user data and ONLY characters that exist in main collection.
        Uses MongoDB aggregation with $lookup for single database call.
        """
        pipeline = [
            # Match the specific user
            {"$match": {"id": user_id}},
            
            # Unwind the characters array to work with individual characters
            {"$unwind": "$characters"},
            
            # Lookup each character in the main collection
            {
                "$lookup": {
                    "from": "collection",  # Main collection name
                    "localField": "characters.id",
                    "foreignField": "id",
                    "as": "char_exists"
                }
            },
            
            # Filter out characters that don't exist in main collection
            {"$match": {"char_exists": {"$ne": []}}},
            
            # Group back to reconstruct user document with only valid characters
            {
                "$group": {
                    "_id": "$_id",
                    "id": {"$first": "$id"},
                    "favorites": {"$first": "$favorites"},
                    "characters": {"$push": "$characters"}
                }
            }
        ]
        
        # Execute aggregation
        result = None
        async for doc in user_collection.aggregate(pipeline):
            result = doc
            break
        
        if not result:
            # Check if user exists but has no valid characters
            user = await user_collection.find_one({'id': user_id})
            if user:
                return user, []
            return None, None
        
        # Sort characters by anime and id
        characters = sorted(result['characters'], key=lambda x: (x['anime'], x['id']))
        return result, characters
    
    @staticmethod
    async def get_anime_counts(anime_list: List[str]) -> Dict[str, int]:
        """Get anime counts in a single batch query."""
        if not anime_list:
            return {}
        
        # Use aggregation to get counts for all animes at once
        pipeline = [
            {"$match": {"anime": {"$in": anime_list}}},
            {"$group": {"_id": "$anime", "count": {"$sum": 1}}}
        ]
        
        counts = {}
        async for result in collection.aggregate(pipeline):
            counts[result["_id"]] = result["count"]
        
        return counts
    
    @staticmethod
    def get_character_counts(characters: List[dict]) -> Dict[int, int]:
        """Calculate character counts efficiently."""
        char_counts = {}
        for character in characters:
            char_id = character['id']
            char_counts[char_id] = char_counts.get(char_id, 0) + 1
        return char_counts
    
    @staticmethod
    def get_unique_characters(characters: List[dict]) -> List[dict]:
        """Get unique characters preserving order."""
        seen = {}
        unique_chars = []
        
        for char in characters:
            if char['id'] not in seen:
                seen[char['id']] = True
                unique_chars.append(char)
        
        return unique_chars
    
    @staticmethod
    def get_consistent_photo(user: dict, characters: List[dict]) -> str:
        """Get a consistent photo for the user's harem."""
        # Try favorites first
        if user.get('favorites'):
            fav_id = user['favorites'][0]
            for char in characters:
                if char['id'] == fav_id:
                    return char.get('img_url')
        
        # Fall back to first character's photo
        if characters:
            return characters[0].get('img_url')
        
        return None


async def harem(update: Update, context: CallbackContext, page: int = 0) -> None:
    """Display user's harem with pagination."""
    user_id = update.effective_user.id
    
    # Get user data with ONLY valid characters (single DB call with $lookup)
    user, characters = await HaremManager.get_user_data_with_valid_characters(user_id)
    if not user:
        message = 'You Have Not Guessed any Characters Yet..'
        if update.message:
            await update.message.reply_text(message)
        else:
            await update.callback_query.edit_message_text(message)
        return
    
    # Process data efficiently
    char_counts = HaremManager.get_character_counts(characters)
    unique_chars = HaremManager.get_unique_characters(characters)
    
    # Pagination
    page_size = 15
    total_pages = max(1, math.ceil(len(unique_chars) / page_size))
    page = max(0, min(page, total_pages - 1))
    
    # Get current page characters
    start_idx = page * page_size
    end_idx = start_idx + page_size
    current_chars = unique_chars[start_idx:end_idx]
    
    # Get unique animes for this page and fetch their counts
    page_animes = list({char['anime'] for char in current_chars})
    anime_counts = await HaremManager.get_anime_counts(page_animes)
    
    # Build message
    safe_name = escape(str(update.effective_user.first_name))
    harem_message = f"<b>{safe_name}'s Harem - Page {page + 1}/{total_pages}</b>\n"
    
    # Group characters by anime for display
    current_chars.sort(key=lambda x: x['anime'])
    grouped_chars = {k: list(v) for k, v in groupby(current_chars, key=lambda x: x['anime'])}
    
    for anime, chars in grouped_chars.items():
        # Safe escaping
        safe_anime = escape(str(anime))
        total_anime_chars = anime_counts.get(anime, 0)
        
        harem_message += f'\n<b>{safe_anime} {len(chars)}/{total_anime_chars}</b>\n'
        
        for char in chars:
            safe_char_name = escape(str(char['name']))
            count = char_counts[char['id']]
            harem_message += f'{char["id"]} {safe_char_name} ×{count}\n'
    
    # Build keyboard
    total_count = len(characters)
    keyboard = [[
        InlineKeyboardButton(
            f"See Collection ({total_count})", 
            switch_inline_query_current_chat=f"collection.{user_id}"
        )
    ]]
    
    if total_pages > 1:
        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton(
                "⬅️", 
                callback_data=f"harem:{page - 1}:{user_id}"
            ))
        if page < total_pages - 1:
            nav_buttons.append(InlineKeyboardButton(
                "➡️", 
                callback_data=f"harem:{page + 1}:{user_id}"
            ))
        keyboard.append(nav_buttons)
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Get consistent photo
    photo_url = HaremManager.get_consistent_photo(user, characters)
    
    # Send or update message
    if photo_url:
        if update.message:
            await update.message.reply_photo(
                photo=photo_url,
                caption=harem_message,
                parse_mode='HTML',
                reply_markup=reply_markup
            )
        else:
            # Ensure we only update if content changed
            current_caption = update.callback_query.message.caption or ""
            if current_caption != harem_message:
                await update.callback_query.edit_message_caption(
                    caption=harem_message,
                    reply_markup=reply_markup,
                    parse_mode='HTML'
                )
    else:
        if update.message:
            await update.message.reply_text(
                harem_message,
                parse_mode='HTML',
                reply_markup=reply_markup
            )
        else:
            current_text = update.callback_query.message.text or ""
            if current_text != harem_message:
                await update.callback_query.edit_message_text(
                    harem_message,
                    parse_mode='HTML',
                    reply_markup=reply_markup
                )


async def harem_callback(update: Update, context: CallbackContext) -> None:
    """Handle harem navigation callbacks."""
    query = update.callback_query
    data = query.data
    
    # Parse callback data
    try:
        _, page, user_id = data.split(':')
        page = int(page)
        user_id = int(user_id)
    except (ValueError, TypeError):
        await query.answer("Invalid request", show_alert=True)
        return
    
    # Check if user owns this harem
    if query.from_user.id != user_id:
        await query.answer("It's Not Your Harem", show_alert=True)
        return
    
    # Process harem update
    await harem(update, context, page)
    await query.answer()


# Register handlers
application.add_handler(CommandHandler(["harem", "collection"], harem, block=False))
harem_handler = CallbackQueryHandler(harem_callback, pattern='^harem', block=False)
application.add_handler(harem_handler)