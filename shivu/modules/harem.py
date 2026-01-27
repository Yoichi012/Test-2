from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, CallbackContext, CallbackQueryHandler
from html import escape
import math
from itertools import groupby
from typing import Dict, List, Tuple
import asyncio

from shivu import collection, user_collection, application


# Small Caps Conversion Utility
def to_small_caps(text: str) -> str:
    """Convert standard text to Small Caps font."""
    small_caps_mapping = {
        'a': 'ᴀ', 'b': 'ʙ', 'c': 'ᴄ', 'd': 'ᴅ', 'e': 'ᴇ',
        'f': 'ꜰ', 'g': 'ɢ', 'h': 'ʜ', 'i': 'ɪ', 'j': 'ᴊ',
        'k': 'ᴋ', 'l': 'ʟ', 'm': 'ᴍ', 'n': 'ɴ', 'o': 'ᴏ',
        'p': 'ᴘ', 'q': 'ǫ', 'r': 'ʀ', 's': 's', 't': 'ᴛ',
        'u': 'ᴜ', 'v': 'ᴠ', 'w': 'ᴡ', 'x': 'x', 'y': 'ʏ',
        'z': 'ᴢ',
        'A': 'ᴀ', 'B': 'ʙ', 'C': 'ᴄ', 'D': 'ᴅ', 'E': 'ᴇ',
        'F': 'ꜰ', 'G': 'ɢ', 'H': 'ʜ', 'I': 'ɪ', 'J': 'ᴊ',
        'K': 'ᴋ', 'L': 'ʟ', 'M': 'ᴍ', 'N': 'ɴ', 'O': 'ᴏ',
        'P': 'ᴘ', 'Q': 'ǫ', 'R': 'ʀ', 'S': 's', 'T': 'ᴛ',
        'U': 'ᴜ', 'V': 'ᴠ', 'W': 'ᴡ', 'X': 'x', 'Y': 'ʏ',
        'Z': 'ᴢ',
        ' ': ' ', '-': '-', '/': '/', '(': '(', ')': ')',
        '[': '[', ']': ']', '{': '{', '}': '}',
        '0': '0', '1': '1', '2': '2', '3': '3', '4': '4',
        '5': '5', '6': '6', '7': '7', '8': '8', '9': '9'
    }

    return ''.join(small_caps_mapping.get(char, char) for char in str(text))


# Rarity Emoji Mapping
RARITY_EMOJIS = {
    1: '⚪', 2: '🔵', 3: '🟡', 4: '💮', 5: '👹',
    6: '🎐', 7: '🔮', 8: '🪐', 9: '⚰️', 10: '🌬️',
    11: '💝', 12: '🌸', 13: '🏖️', 14: '🍭', 15: '🧬'
}

# Rarity Names
RARITY_NAMES = {
    1: "Common", 2: "Rare", 3: "Legendary", 4: "Special", 5: "Ancient",
    6: "Celestial", 7: "Epic", 8: "Cosmic", 9: "Nightmare", 10: "Frostborn",
    11: "Valentine", 12: "Spring", 13: "Tropical", 14: "Kawaii", 15: "Hybrid"
}


class HaremManager:
    """Manages harem data and operations efficiently."""

    @staticmethod
    async def get_user_data_with_valid_characters(user_id: int, rarity_filter=None) -> Tuple[dict, List[dict]]:
        """
        Fetch user data and characters directly from user_collection.
        Now with optional rarity filtering support.
        """
        # Fetch user document directly
        user = await user_collection.find_one({'id': user_id})

        if not user:
            return None, None

        # Get user's characters array
        characters = user.get('characters', [])

        if not characters:
            return user, []

        # Apply rarity filter if specified
        if rarity_filter is not None:
            characters = [char for char in characters if char.get('rarity') == rarity_filter]

        # Get unique character IDs from user's collection
        character_ids = [char.get('id') for char in characters if char.get('id')]

        if not character_ids:
            return user, []

        # Fetch valid characters from main collection in batch
        valid_characters = []
        async for char in collection.find({'id': {'$in': character_ids}}):
            valid_characters.append(char)

        # Create mapping of character ID to character data
        valid_char_map = {char['id']: char for char in valid_characters}

        # Count duplicates in user's collection
        char_counts = {}
        user_valid_characters = []

        for user_char in characters:
            char_id = user_char.get('id')
            if char_id and char_id in valid_char_map:
                # Use character data from main collection
                char_data = valid_char_map[char_id].copy()
                char_counts[char_id] = char_counts.get(char_id, 0) + 1
                user_valid_characters.append(char_data)

        # Sort characters by anime and id
        user_valid_characters = sorted(user_valid_characters, key=lambda x: (x.get('anime', ''), x.get('id', '')))

        return user, user_valid_characters

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
    """Display user's harem with pagination and rarity filtering."""
    user_id = update.effective_user.id

    # Import smode functions
    try:
        from shivu.modules.smode import get_user_sort_preference, RARITY_OPTIONS
        rarity_filter = await get_user_sort_preference(user_id)
    except ImportError:
        # If smode module not available, disable filtering
        rarity_filter = None
        RARITY_OPTIONS = {}

    # Get user data with ONLY valid characters (with optional rarity filter)
    user, characters = await HaremManager.get_user_data_with_valid_characters(user_id, rarity_filter)
    
    if not user:
        message = 'You Have Not Guessed any Characters Yet..'
        if update.message:
            await update.message.reply_text(message)
        else:
            await update.callback_query.edit_message_text(message)
        return

    # Get total character count (without filter) for display
    total_user_data = await user_collection.find_one({'id': user_id})
    total_characters_count = len(total_user_data.get('characters', [])) if total_user_data else 0

    if not characters:
        # No characters after filtering
        if rarity_filter is not None:
            filter_name = RARITY_OPTIONS.get(str(rarity_filter), {}).get('name', 'this rarity')
            message = f'You Have No Characters of {filter_name}!\n\nUse /smode to change filter or select "All Rarities".'
        else:
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

    # Build message with Small Caps formatting
    safe_name = escape(str(update.effective_user.first_name))

    # Header in Small Caps with filter info
    header_text = to_small_caps(f"{safe_name}'S HAREM - PAGE {page + 1}/{total_pages}")
    harem_message = f"<b>{header_text}</b>\n"
    
    # Add filter info if active
    if rarity_filter is not None:
        filter_name = RARITY_OPTIONS.get(str(rarity_filter), {}).get('name', 'Unknown')
        filter_text = to_small_caps(f"Filter: {filter_name} ({len(characters)}/{total_characters_count})")
        harem_message += f"<b>🔍 {filter_text}</b>\n"
    
    harem_message += "\n"

    # Group characters by anime for display
    current_chars.sort(key=lambda x: x['anime'])
    grouped_chars = {k: list(v) for k, v in groupby(current_chars, key=lambda x: x['anime'])}

    for anime, chars in grouped_chars.items():
        # Safe escaping and Small Caps conversion
        safe_anime = escape(str(anime))
        anime_small_caps = to_small_caps(safe_anime)
        total_anime_chars = anime_counts.get(anime, 0)

        # Anime header with Small Caps
        harem_message += f"<b>𖤍 {anime_small_caps} {{{len(chars)}/{total_anime_chars}}}</b>\n"
        harem_message += f"--------------------\n"

        for char in chars:
            safe_char_name = escape(str(char['name']))
            char_small_caps = to_small_caps(safe_char_name)
            count = char_counts[char['id']]

            # Get rarity emoji
            rarity_level = char.get('rarity', 1)
            rarity_emoji = RARITY_EMOJIS.get(rarity_level, '⚪')

            # Character line with Small Caps
            harem_message += f"✶ {char['id']} [ {rarity_emoji} ] {char_small_caps} x{count}\n"

        harem_message += f"--------------------\n\n"

    # Build keyboard
    total_count = len(characters)
    keyboard = [[
        InlineKeyboardButton(
            to_small_caps(f"See Collection ({total_count})"), 
            switch_inline_query_current_chat=f"collection.{user_id}"
        )
    ]]
    
    # Add smode button
    keyboard.append([
        InlineKeyboardButton(
            "⚙️ " + to_small_caps("Sorting Mode"),
            callback_data=f"open_smode:{user_id}"
        )
    ])

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
