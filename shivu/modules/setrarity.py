import logging
from typing import Optional, List, Dict, Any
from html import escape

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from shivu import application, db, collection, LOGGER
from shivu import shivuu

# Collections
rarity_settings_collection = db.rarity_settings
locked_characters_collection = db.locked_characters

# Config se owner aur sudo data load karna
try:
    from config import Config
    OWNER_ID = Config.OWNER_ID
    SUDO_USERS = Config.SUDO_USERS
except ImportError:
    try:
        # Fallback: try direct import
        from config import OWNER_ID, SUDO_USERS
    except ImportError:
        LOGGER.error("❌ Config import failed. Make sure config.py exists with OWNER_ID and SUDO_USERS")
        OWNER_ID = None
        SUDO_USERS = []

# Small caps conversion function (same as main.py)
def to_small_caps(text: str) -> str:
    mapping = {
        'a': 'ᴀ', 'b': 'ʙ', 'c': 'ᴄ', 'd': 'ᴅ', 'e': 'ᴇ', 'f': 'ꜰ', 'g': 'ɢ', 'h': 'ʜ', 'i': 'ɪ', 
        'j': 'ᴊ', 'k': 'ᴋ', 'l': 'ʟ', 'm': 'ᴍ', 'n': 'ɴ', 'o': 'ᴏ', 'p': 'ᴘ', 'q': 'ǫ', 'r': 'ʀ', 
        's': 'ꜱ', 't': 'ᴛ', 'u': 'ᴜ', 'v': 'ᴠ', 'w': 'ᴡ', 'x': 'x', 'y': 'ʏ', 'z': 'ᴢ',
        'A': 'ᴀ', 'B': 'ʙ', 'C': 'ᴄ', 'D': 'ᴅ', 'E': 'ᴇ', 'F': 'ꜰ', 'G': 'ɢ', 'H': 'ʜ', 'I': 'ɪ',
        'J': 'ᴊ', 'K': 'ᴋ', 'L': 'ʟ', 'M': 'ᴍ', 'N': 'ɴ', 'O': 'ᴏ', 'P': 'ᴘ', 'Q': 'ǫ', 'R': 'ʀ',
        'S': 'ꜱ', 'T': 'ᴛ', 'U': 'ᴜ', 'V': 'ᴠ', 'W': 'ᴡ', 'X': 'x', 'Y': 'ʏ', 'Z': 'ᴢ',
        '0': '0', '1': '1', '2': '2', '3': '3', '4': '4', '5': '5', '6': '6', '7': '7', '8': '8', '9': '9',
        ' ': ' ', '!': '!', ':': ':', '.': '.', ',': ',', "'": "'", '"': '"', '?': '?', 
        '(': '(', ')': ')', '[': '[', ']': ']', '{': '{', '}': '}', '-': '-', '_': '_'
    }
    result = []
    for char in text:
        if char in mapping:
            result.append(mapping[char])
        else:
            result.append(char)
    return ''.join(result)

# Rarity display mapping (same as main.py)
RARITY_MAP = {
    1: "⚪ ᴄᴏᴍᴍᴏɴ",
    2: "🔵 ʀᴀʀᴇ",
    3: "🟡 ʟᴇɢᴇɴᴅᴀʀʏ",
    4: "💮 ꜱᴘᴇᴄɪᴀʟ",
    5: "👹 ᴀɴᴄɪᴇɴᴛ",
    6: "🎐 ᴄᴇʟᴇꜱᴛɪᴀʟ",
    7: "🔮 ᴇᴘɪᴄ",
    8: "🪐 ᴄᴏꜱᴍɪᴄ",
    9: "⚰️ ɴɪɢʜᴛᴍᴀʀᴇ",
    10: "🌬️ ꜰʀᴏꜱᴛʙᴏʀɴ",
    11: "💝 ᴠᴀʟᴇɴᴛɪɴᴇ",
    12: "🌸 ꜱᴘʀɪɴɢ",
    13: "🏖️ ᴛʀᴏᴘɪᴄᴀʟ",
    14: "🍭 ᴋᴀᴡᴀɪɪ",
    15: "🧬 ʜʏʙʀɪᴅ",
}

# Authorization check
def is_authorized(user_id: int) -> bool:
    """Check if user is owner or sudo"""
    if OWNER_ID is None:
        return False
    return user_id == OWNER_ID or user_id in SUDO_USERS

async def get_chat_rarity_settings(chat_id: int) -> Dict[str, Any]:
    """Get rarity settings for a specific chat"""
    settings = await rarity_settings_collection.find_one({'chat_id': chat_id})
    if not settings:
        # Default: all rarities enabled
        settings = {
            'chat_id': chat_id,
            'disabled_rarities': []
        }
        await rarity_settings_collection.insert_one(settings)
    return settings

async def is_character_locked(character_id: str) -> bool:
    """Check if a character is locked"""
    locked = await locked_characters_collection.find_one({'character_id': character_id})
    return locked is not None

async def is_rarity_enabled(chat_id: int, rarity: int) -> bool:
    """Check if a rarity is enabled in a chat"""
    settings = await get_chat_rarity_settings(chat_id)
    return rarity not in settings.get('disabled_rarities', [])

# Command 1: set_on - Enable a rarity
async def set_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Enable a rarity for spawning in the group"""
    if not update.effective_user or not update.effective_chat:
        return
    
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # Authorization check
    if not is_authorized(user_id):
        await update.message.reply_text(
            to_small_caps("⛔ You are not authorized to use this command. Only owner and sudo users can use it.")
        )
        return
    
    # Check if rarity number is provided
    if not context.args:
        rarity_list = "\n".join([f"{k}: {v}" for k, v in RARITY_MAP.items()])
        await update.message.reply_text(
            to_small_caps(f"❌ Please provide a rarity number.\n\nUsage: /set_on <rarity_number>\n\nAvailable Rarities:\n{rarity_list}")
        )
        return
    
    try:
        rarity_num = int(context.args[0])
    except ValueError:
        await update.message.reply_text(to_small_caps("❌ Please provide a valid rarity number."))
        return
    
    # Validate rarity number
    if rarity_num not in RARITY_MAP:
        await update.message.reply_text(
            to_small_caps(f"❌ Invalid rarity number. Please choose from 1-{len(RARITY_MAP)}.")
        )
        return
    
    try:
        # Remove from disabled list
        settings = await get_chat_rarity_settings(chat_id)
        disabled_rarities = settings.get('disabled_rarities', [])
        
        if rarity_num not in disabled_rarities:
            await update.message.reply_text(
                to_small_caps(f"✅ Rarity {RARITY_MAP[rarity_num]} is already enabled!")
            )
            return
        
        disabled_rarities.remove(rarity_num)
        
        await rarity_settings_collection.update_one(
            {'chat_id': chat_id},
            {'$set': {'disabled_rarities': disabled_rarities}},
            upsert=True
        )
        
        await update.message.reply_text(
            to_small_caps(f"✅ Rarity {RARITY_MAP[rarity_num]} has been enabled for spawning in this group!")
        )
        LOGGER.info(f"User {user_id} enabled rarity {rarity_num} in chat {chat_id}")
        
    except Exception as e:
        LOGGER.exception(f"Error in set_on command: {e}")
        await update.message.reply_text(to_small_caps("❌ An error occurred. Please try again."))

# Command 2: set_off - Disable a rarity
async def set_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Disable a rarity from spawning in the group"""
    if not update.effective_user or not update.effective_chat:
        return
    
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # Authorization check
    if not is_authorized(user_id):
        await update.message.reply_text(
            to_small_caps("⛔ You are not authorized to use this command. Only owner and sudo users can use it.")
        )
        return
    
    # Check if rarity number is provided
    if not context.args:
        rarity_list = "\n".join([f"{k}: {v}" for k, v in RARITY_MAP.items()])
        await update.message.reply_text(
            to_small_caps(f"❌ Please provide a rarity number.\n\nUsage: /set_off <rarity_number>\n\nAvailable Rarities:\n{rarity_list}")
        )
        return
    
    try:
        rarity_num = int(context.args[0])
    except ValueError:
        await update.message.reply_text(to_small_caps("❌ Please provide a valid rarity number."))
        return
    
    # Validate rarity number
    if rarity_num not in RARITY_MAP:
        await update.message.reply_text(
            to_small_caps(f"❌ Invalid rarity number. Please choose from 1-{len(RARITY_MAP)}.")
        )
        return
    
    try:
        # Add to disabled list
        settings = await get_chat_rarity_settings(chat_id)
        disabled_rarities = settings.get('disabled_rarities', [])
        
        if rarity_num in disabled_rarities:
            await update.message.reply_text(
                to_small_caps(f"✅ Rarity {RARITY_MAP[rarity_num]} is already disabled!")
            )
            return
        
        disabled_rarities.append(rarity_num)
        
        await rarity_settings_collection.update_one(
            {'chat_id': chat_id},
            {'$set': {'disabled_rarities': disabled_rarities}},
            upsert=True
        )
        
        await update.message.reply_text(
            to_small_caps(f"🚫 Rarity {RARITY_MAP[rarity_num]} has been disabled for spawning in this group!")
        )
        LOGGER.info(f"User {user_id} disabled rarity {rarity_num} in chat {chat_id}")
        
    except Exception as e:
        LOGGER.exception(f"Error in set_off command: {e}")
        await update.message.reply_text(to_small_caps("❌ An error occurred. Please try again."))

# Command 3: lock - Lock a character from spawning
async def lock(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Lock a character from spawning in all groups"""
    if not update.effective_user:
        return
    
    user_id = update.effective_user.id
    
    # Authorization check
    if not is_authorized(user_id):
        await update.message.reply_text(
            to_small_caps("⛔ You are not authorized to use this command. Only owner and sudo users can use it.")
        )
        return
    
    # Check if character ID is provided
    if not context.args:
        await update.message.reply_text(
            to_small_caps("❌ Please provide a character ID.\n\nUsage: /lock <character_id> <reason>")
        )
        return
    
    character_id = context.args[0]
    
    # Get lock reason (optional)
    reason = " ".join(context.args[1:]) if len(context.args) > 1 else "No reason provided"
    
    try:
        # Check if character exists
        character = await collection.find_one({'id': character_id})
        if not character:
            await update.message.reply_text(
                to_small_caps(f"❌ Character with ID {character_id} not found in database.")
            )
            return
        
        # Check if already locked
        if await is_character_locked(character_id):
            await update.message.reply_text(
                to_small_caps(f"🔒 Character {escape(character.get('name', 'Unknown'))} is already locked!")
            )
            return
        
        # Lock the character
        lock_data = {
            'character_id': character_id,
            'character_name': character.get('name', 'Unknown'),
            'locked_by_id': user_id,
            'locked_by_name': update.effective_user.first_name,
            'reason': reason,
            'locked_at': update.message.date
        }
        
        await locked_characters_collection.insert_one(lock_data)
        
        await update.message.reply_text(
            to_small_caps(
                f"🔒 Character locked successfully!\n\n"
                f"👤 Name: {escape(character.get('name', 'Unknown'))}\n"
                f"🆔 ID: {character_id}\n"
                f"📝 Reason: {escape(reason)}\n"
                f"🔐 Locked by: {escape(update.effective_user.first_name)}"
            )
        )
        LOGGER.info(f"User {user_id} locked character {character_id}")
        
    except Exception as e:
        LOGGER.exception(f"Error in lock command: {e}")
        await update.message.reply_text(to_small_caps("❌ An error occurred. Please try again."))

# Command 4: unlock - Unlock a character
async def unlock(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Unlock a character to allow spawning again"""
    if not update.effective_user:
        return
    
    user_id = update.effective_user.id
    
    # Authorization check
    if not is_authorized(user_id):
        await update.message.reply_text(
            to_small_caps("⛔ You are not authorized to use this command. Only owner and sudo users can use it.")
        )
        return
    
    # Check if character ID is provided
    if not context.args:
        await update.message.reply_text(
            to_small_caps("❌ Please provide a character ID.\n\nUsage: /unlock <character_id>")
        )
        return
    
    character_id = context.args[0]
    
    try:
        # Check if character is locked
        locked_char = await locked_characters_collection.find_one({'character_id': character_id})
        if not locked_char:
            await update.message.reply_text(
                to_small_caps(f"❌ Character with ID {character_id} is not locked.")
            )
            return
        
        # Unlock the character
        await locked_characters_collection.delete_one({'character_id': character_id})
        
        await update.message.reply_text(
            to_small_caps(
                f"🔓 Character unlocked successfully!\n\n"
                f"👤 Name: {escape(locked_char.get('character_name', 'Unknown'))}\n"
                f"🆔 ID: {character_id}\n"
                f"✅ The character can now spawn in groups!"
            )
        )
        LOGGER.info(f"User {user_id} unlocked character {character_id}")
        
    except Exception as e:
        LOGGER.exception(f"Error in unlock command: {e}")
        await update.message.reply_text(to_small_caps("❌ An error occurred. Please try again."))

# Command 5: locklist - Show all locked characters
async def locklist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show list of all locked characters"""
    if not update.effective_user:
        return
    
    user_id = update.effective_user.id
    
    # Authorization check
    if not is_authorized(user_id):
        await update.message.reply_text(
            to_small_caps("⛔ You are not authorized to use this command. Only owner and sudo users can use it.")
        )
        return
    
    try:
        # Get all locked characters
        locked_chars = await locked_characters_collection.find().to_list(length=None)
        
        if not locked_chars:
            await update.message.reply_text(
                to_small_caps("✅ No characters are currently locked!")
            )
            return
        
        # Build the list message
        message = to_small_caps("🔒 Locked Characters List:\n\n")
        
        for idx, char in enumerate(locked_chars, 1):
            message += to_small_caps(
                f"{idx}. 👤 Name: {escape(char.get('character_name', 'Unknown'))}\n"
                f"   🆔 ID: {char.get('character_id', 'Unknown')}\n"
                f"   📝 Reason: {escape(char.get('reason', 'No reason'))}\n"
                f"   🔐 Locked by: {escape(char.get('locked_by_name', 'Unknown'))}\n\n"
            )
        
        message += to_small_caps(f"Total locked characters: {len(locked_chars)}")
        
        # Send message (split if too long)
        if len(message) > 4000:
            # Split message
            for i in range(0, len(message), 4000):
                await update.message.reply_text(message[i:i+4000])
        else:
            await update.message.reply_text(message)
        
        LOGGER.info(f"User {user_id} viewed locked characters list")
        
    except Exception as e:
        LOGGER.exception(f"Error in locklist command: {e}")
        await update.message.reply_text(to_small_caps("❌ An error occurred. Please try again."))

# Helper function to check spawn eligibility (to be used in main.py)
async def can_character_spawn(character_id: str, rarity: int, chat_id: int) -> tuple[bool, Optional[str]]:
    """
    Check if a character can spawn in a chat
    Returns: (can_spawn: bool, reason: Optional[str])
    """
    # Check if character is locked
    if await is_character_locked(character_id):
        return False, "Character is locked"
    
    # Check if rarity is enabled
    if not await is_rarity_enabled(chat_id, rarity):
        return False, f"Rarity {RARITY_MAP.get(rarity, rarity)} is disabled in this chat"
    
    return True, None

def setup_handlers():
    """Setup command handlers - to be called from main.py"""
    application.add_handler(CommandHandler("set_on", set_on, block=False))
    application.add_handler(CommandHandler("set_off", set_off, block=False))
    application.add_handler(CommandHandler("lock", lock, block=False))
    application.add_handler(CommandHandler("unlock", unlock, block=False))
    application.add_handler(CommandHandler("locklist", locklist, block=False))
    LOGGER.info("✅ Rarity management commands registered successfully!")
