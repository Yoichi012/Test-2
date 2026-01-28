from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, CallbackQueryHandler, ContextTypes

from shivu import application, user_collection, LOGGER, db

# MongoDB collection for storing user sort preferences
sort_preferences = db.sort_preferences

# Image URL for smode menu
SMODE_IMAGE_URL = "https://files.catbox.moe/g3rxr1.jpg"


# ---------- Small Caps Utility ----------
SMALL_CAPS_MAP = {
    'a': 'ᴀ', 'b': 'ʙ', 'c': 'ᴄ', 'd': 'ᴅ', 'e': 'ᴇ', 'f': 'ғ', 'g': 'ɢ',
    'h': 'ʜ', 'i': 'ɪ', 'j': 'ᴊ', 'k': 'ᴋ', 'l': 'ʟ', 'm': 'ᴍ', 'n': 'ɴ',
    'o': 'ᴏ', 'p': 'ᴘ', 'q': 'ǫ', 'r': 'ʀ', 's': 'ꜱ', 't': 'ᴛ', 'u': 'ᴜ',
    'v': 'ᴠ', 'w': 'ᴡ', 'x': 'x', 'y': 'ʏ', 'z': 'ᴢ',
    'A': 'ᴀ', 'B': 'ʙ', 'C': 'ᴄ', 'D': 'ᴅ', 'E': 'ᴇ', 'F': 'ғ', 'G': 'ɢ',
    'H': 'ʜ', 'I': 'ɪ', 'J': 'ᴊ', 'K': 'ᴋ', 'L': 'ʟ', 'M': 'ᴍ', 'N': 'ɴ',
    'O': 'ᴏ', 'P': 'ᴘ', 'Q': 'ǫ', 'R': 'ʀ', 'S': 'ꜱ', 'T': 'ᴛ', 'U': 'ᴜ',
    'V': 'ᴠ', 'W': 'ᴡ', 'X': 'x', 'Y': 'ʏ', 'Z': 'ᴢ',
    ' ': ' ', ':': ':', '!': '!', '?': '?', '.': '.', ',': ',', '-': '-',
    '(': '(', ')': ')', '[': '[', ']': ']', '{': '{', '}': '}', '=': '=',
    '+': '+', '*': '*', '/': '/', '\\': '\\', '|': '|', '_': '_',
    '0': '0', '1': '1', '2': '2', '3': '3', '4': '4', '5': '5', 
    '6': '6', '7': '7', '8': '8', '9': '9'
}


def to_small_caps(text: str) -> str:
    """Convert text to small caps Unicode characters."""
    return ''.join(SMALL_CAPS_MAP.get(char, char) for char in str(text))


# ---------- Rarity Configuration ----------
RARITY_OPTIONS = {
    "all": {"name": "🍃 default", "value": None},
    "1": {"name": "⚪ Common", "value": 1},
    "2": {"name": "🔵 Rare", "value": 2},
    "3": {"name": "🟡 Legendary", "value": 3},
    "4": {"name": "💮 Special", "value": 4},
    "5": {"name": "👹 Ancient", "value": 5},
    "6": {"name": "🎐 Celestial", "value": 6},
    "7": {"name": "🔮 Epic", "value": 7},
    "8": {"name": "🪐 Cosmic", "value": 8},
    "9": {"name": "⚰️ Nightmare", "value": 9},
    "10": {"name": "🌬️ Frostborn", "value": 10},
    "11": {"name": "💝 Valentine", "value": 11},
    "12": {"name": "🌸 Spring", "value": 12},
    "13": {"name": "🏖️ Tropical", "value": 13},
    "14": {"name": "🍭 Kawaii", "value": 14},
    "15": {"name": "🧬 Hybrid", "value": 15}
}


# ---------- Database Functions ----------
async def get_user_sort_preference(user_id: int):
    """Get user's current sorting preference."""
    pref = await sort_preferences.find_one({"user_id": user_id})
    if pref:
        return pref.get("rarity_filter")
    return None  # None means show all


async def set_user_sort_preference(user_id: int, rarity_filter):
    """Set user's sorting preference."""
    await sort_preferences.update_one(
        {"user_id": user_id},
        {"$set": {"rarity_filter": rarity_filter}},
        upsert=True
    )
    LOGGER.info(f"User {user_id} set sort preference to rarity: {rarity_filter}")


async def get_filtered_characters(user_id: int):
    """
    Get user's characters filtered by their sort preference.
    Returns: (filtered_characters, rarity_filter, total_count)
    """
    # Get user's collection
    user_data = await user_collection.find_one({"id": user_id})
    if not user_data or "characters" not in user_data:
        return [], None, 0

    all_characters = user_data.get("characters", [])
    total_count = len(all_characters)

    # Get user's filter preference
    rarity_filter = await get_user_sort_preference(user_id)

    # If no filter, return all
    if rarity_filter is None:
        return all_characters, None, total_count

    # Filter by rarity
    filtered = [char for char in all_characters if char.get("rarity") == rarity_filter]

    return filtered, rarity_filter, total_count


# ---------- Helper Function to Create Main Menu Keyboard ----------
def create_smode_keyboard(current_pref):
    """Create the main smode menu keyboard with all rarity options."""
    keyboard = []
    row = []

    # Add "All Rarities" button first (DEFAULT)
    row.append(InlineKeyboardButton(
        to_small_caps("🍃 default") + (" ✓" if current_pref is None else ""),
        callback_data="smode_all"
    ))
    keyboard.append(row)

    # Add rarity buttons (ALL IN SMALL CAPS)
    row = []
    for i, (key, data) in enumerate(list(RARITY_OPTIONS.items())[1:], 1):  # Skip "all"
        is_selected = (current_pref == data["value"])
        # Convert button text to small caps
        button_text = to_small_caps(data["name"]) + (" ✓" if is_selected else "")

        row.append(InlineKeyboardButton(
            button_text,
            callback_data=f"smode_{key}"
        ))

        # 3 buttons per row
        if i % 3 == 0:
            keyboard.append(row)
            row = []

    # Add remaining buttons
    if row:
        keyboard.append(row)

    # Add Cancel button at the end
    keyboard.append([
        InlineKeyboardButton(
            "❌ " + to_small_caps("Cancel"),
            callback_data="smode_cancel"
        )
    ])

    return keyboard


# ---------- Helper Function to Create Confirmation Keyboard ----------
def create_confirmation_keyboard(user_id: int):
    """Create keyboard with only Back to Menu button after selection."""
    keyboard = [
        [
            InlineKeyboardButton(
                "🔙 " + to_small_caps("Back to Menu"),
                callback_data=f"smode_backmenu:{user_id}"
            )
        ]
    ]
    return keyboard


# ---------- Command Handlers ----------
async def smode_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /smode command - Opens sorting mode menu with rarity options
    """
    user_id = update.effective_user.id

    # Get current preference
    current_pref = await get_user_sort_preference(user_id)

    # Determine current selection text
    if current_pref is None:
        current_text = " " + to_small_caps("All Rarities")
    else:
        rarity_info = RARITY_OPTIONS.get(str(current_pref), {})
        current_text = rarity_info.get("name", "Unknown")

    # Create message with premium emojis
    caption = (
        f"<b>✨ {to_small_caps('SMODE')}</b>\n\n"
        f"🎯 {to_small_caps('Current Model:')} <b>{current_text}</b>\n"
    )

    # Create keyboard
    keyboard = create_smode_keyboard(current_pref)
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Try to send image, fallback to text if fails
    try:
        await update.message.reply_photo(
            photo=SMODE_IMAGE_URL,
            caption=caption,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
    except Exception as e:
        LOGGER.warning(f"Failed to send smode image, using text fallback: {e}")
        # Fallback to text message
        await update.message.reply_text(
            caption,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )


async def smode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle rarity selection button callbacks"""
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data

    # Handle cancel button
    if data == "smode_cancel":
        await query.answer("🚫 Cancelled", show_alert=False)
        await query.message.delete()
        return

    # Handle back to menu button
    if data.startswith("smode_backmenu:"):
        try:
            _, callback_user_id = data.split(':')
            callback_user_id = int(callback_user_id)
            
            # Verify it's the same user
            if user_id != callback_user_id:
                await query.answer("This is not your menu!", show_alert=True)
                return
                
            await query.answer()
            
            # Get current preference
            current_pref = await get_user_sort_preference(user_id)
            
            # Determine current selection text
            if current_pref is None:
                current_text = "🍃 " + to_small_caps("default")
            else:
                rarity_info = RARITY_OPTIONS.get(str(current_pref), {})
                current_text = rarity_info.get("name", "Unknown")
            
            # Create message
            message_text = (
                f"<b>✨ {to_small_caps('SMODE')}</b>\n\n"
                f"🎯 {to_small_caps('Current Model:')} <b>{current_text}</b>\n"
            )
            
            # Create keyboard with all options
            keyboard = create_smode_keyboard(current_pref)
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Update the message
            try:
                if query.message.photo:
                    await query.edit_message_caption(
                        caption=message_text,
                        reply_markup=reply_markup,
                        parse_mode="HTML"
                    )
                else:
                    await query.edit_message_text(
                        text=message_text,
                        reply_markup=reply_markup,
                        parse_mode="HTML"
                    )
            except Exception as e:
                LOGGER.error(f"Failed to update smode message: {e}")
                
            return
            
        except (ValueError, IndexError):
            await query.answer("Invalid request", show_alert=True)
            return

    await query.answer()

    # Extract rarity from callback data
    if data == "smode_all":
        rarity_filter = None
        selected_text = "🍃 " + to_small_caps("default")
    else:
        rarity_key = data.replace("smode_", "")
        rarity_info = RARITY_OPTIONS.get(rarity_key)

        if not rarity_info:
            await query.answer("❌ Invalid selection!", show_alert=True)
            return

        rarity_filter = rarity_info["value"]
        selected_text = rarity_info["name"]

    # Save preference
    await set_user_sort_preference(user_id, rarity_filter)

    # Create confirmation message
    message_text = (
        f"<b>✨ {to_small_caps('SMODE')}</b>\n\n"
        f"✅ {to_small_caps('Filter Applied!')}\n\n"
        f"🎯 {to_small_caps('Selected:')} <b>{selected_text}</b>\n\n"
        f"💡 {to_small_caps('Your harem will now show only')} <b>{selected_text}</b> {to_small_caps('characters.')}\n"
    )

    # Create keyboard with only Back to Menu button
    keyboard = create_confirmation_keyboard(user_id)
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Update the message
    try:
        if query.message.photo:
            await query.edit_message_caption(
                caption=message_text,
                reply_markup=reply_markup,
                parse_mode="HTML"
            )
        else:
            await query.edit_message_text(
                text=message_text,
                reply_markup=reply_markup,
                parse_mode="HTML"
            )
    except Exception as e:
        LOGGER.error(f"Failed to update smode message: {e}")

    # Show notification
    await query.answer(f"✅ Filter set to: {selected_text}", show_alert=False)


async def open_smode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle opening smode from harem page"""
    query = update.callback_query

    # Parse user_id from callback data
    try:
        _, user_id = query.data.split(':')
        user_id = int(user_id)
    except (ValueError, TypeError):
        await query.answer("Invalid request", show_alert=True)
        return

    # Check if user owns this harem
    if query.from_user.id != user_id:
        await query.answer("This is not your harem!", show_alert=True)
        return

    await query.answer()

    # Get current preference
    current_pref = await get_user_sort_preference(user_id)

    # Determine current selection text
    if current_pref is None:
        current_text = "🍃 " + to_small_caps("default")
    else:
        rarity_info = RARITY_OPTIONS.get(str(current_pref), {})
        current_text = rarity_info.get("name", "Unknown")

    # Create message with premium emojis
    caption = (
        f"<b>✨ {to_small_caps('SMODE')}</b>\n\n"
        f"🎯 {to_small_caps('Current Model:')} <b>{current_text}</b>\n"
    )

    # Create keyboard
    keyboard = create_smode_keyboard(current_pref)
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Try to edit message with new photo and caption
    try:
        # Delete old message
        await query.message.delete()

        # Send new message with smode image
        await context.bot.send_photo(
            chat_id=query.message.chat_id,
            photo=SMODE_IMAGE_URL,
            caption=caption,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
    except Exception as e:
        LOGGER.error(f"Failed to open smode from harem: {e}")
        await query.answer("Failed to open sorting mode", show_alert=True)


# ---------- Helper Function for Other Modules ----------
async def apply_rarity_filter(user_id: int, characters: list):
    """
    Apply user's rarity filter to a character list.
    This function should be called from harem/collection commands.
    
    Args:
        user_id: User ID
        characters: List of all user characters
    
    Returns:
        Filtered list of characters based on user's preference
    """
    rarity_filter = await get_user_sort_preference(user_id)

    # If no filter, return all
    if rarity_filter is None:
        return characters

    # Filter by rarity
    filtered = [char for char in characters if char.get("rarity") == rarity_filter]

    return filtered


# ---------- Handler Registration ----------
def register_handlers():
    """Register sorting mode handlers with the application."""
    application.add_handler(CommandHandler("smode", smode_command, block=False))
    application.add_handler(CallbackQueryHandler(smode_callback, pattern="^smode_", block=False))
    application.add_handler(CallbackQueryHandler(open_smode_callback, pattern="^open_smode:", block=False))
    LOGGER.info("Sorting mode handlers registered successfully")


# Auto-register handlers when module is imported
register_handlers()
