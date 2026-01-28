from telegram import Update
from telegram.ext import CommandHandler, ContextTypes
import asyncio

# Import from shivu - same as main.py
from shivu import application, collection, db, LOGGER, OWNER_ID, SUDO_USERS

# MongoDB collections
rarity_collection = db['rarity_settings']
locked_chars_collection = db['locked_characters']

# Rarity Map - Main.py se same
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

# Check if user is owner or sudo
def is_authorized(user_id):
    return user_id in OWNER_ID or user_id in SUDO_USERS


# ============================================
# COMMAND 1: SET_ON - Rarity Enable
# ============================================
async def set_rarity_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        return
    
    user_id = update.effective_user.id
    
    # Check authorization - agar authorized nahi hai to koi response nahi
    if not is_authorized(user_id):
        return
    
    try:
        # Get rarity number from command
        if not context.args or len(context.args) < 1:
            await update.message.reply_text(
                "❌ **Usage:** `/set_on <rarity_number>`\n\n"
                "**Example:** `/set_on 3`\n\n"
                "**Available Rarities:**\n"
                "1 = ⚪ Common\n"
                "2 = 🔵 Rare\n"
                "3 = 🟡 Legendary\n"
                "4 = 💮 Special\n"
                "5 = 👹 Ancient\n"
                "6 = 🎐 Celestial\n"
                "7 = 🔮 Epic\n"
                "8 = 🪐 Cosmic\n"
                "9 = ⚰️ Nightmare\n"
                "10 = 🌬️ Frostborn\n"
                "11 = 💝 Valentine\n"
                "12 = 🌸 Spring\n"
                "13 = 🏖️ Tropical\n"
                "14 = 🍭 Kawaii\n"
                "15 = 🧬 Hybrid"
            )
            return
        
        rarity_num = int(context.args[0])
        
        # Validate rarity number
        if rarity_num not in RARITY_MAP:
            await update.message.reply_text(
                f"❌ **Invalid rarity number!**\n\n"
                f"Please use a number between 1-15."
            )
            return
        
        # Rarity ko database me enable karna
        await rarity_collection.update_one(
            {"rarity": rarity_num},
            {"$set": {"enabled": True}},
            upsert=True
        )
        
        rarity_name = RARITY_MAP.get(rarity_num, f"Rarity {rarity_num}")
        
        await update.message.reply_text(
            f"✅ **{rarity_name} Successfully Enabled!**\n\n"
            f"Is rarity ke characters ab spawn honge."
        )
        
        LOGGER.info(f"Rarity {rarity_num} enabled by user {user_id}")
    
    except ValueError:
        await update.message.reply_text("❌ **Invalid rarity number!**\n\nPlease enter a valid number (1-15).")
    except Exception as e:
        LOGGER.exception(f"Error in set_rarity_on: {e}")
        await update.message.reply_text(f"❌ **Error:** {str(e)}")


# ============================================
# COMMAND 2: SET_OFF - Rarity Disable
# ============================================
async def set_rarity_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        return
    
    user_id = update.effective_user.id
    
    # Check authorization - agar authorized nahi hai to koi response nahi
    if not is_authorized(user_id):
        return
    
    try:
        # Get rarity number from command
        if not context.args or len(context.args) < 1:
            await update.message.reply_text(
                "❌ **Usage:** `/set_off <rarity_number>`\n\n"
                "**Example:** `/set_off 3`"
            )
            return
        
        rarity_num = int(context.args[0])
        
        # Validate rarity number
        if rarity_num not in RARITY_MAP:
            await update.message.reply_text(
                f"❌ **Invalid rarity number!**\n\n"
                f"Please use a number between 1-15."
            )
            return
        
        # Rarity ko database me disable karna
        await rarity_collection.update_one(
            {"rarity": rarity_num},
            {"$set": {"enabled": False}},
            upsert=True
        )
        
        rarity_name = RARITY_MAP.get(rarity_num, f"Rarity {rarity_num}")
        
        await update.message.reply_text(
            f"✅ **{rarity_name} Successfully Disabled!**\n\n"
            f"Is rarity ke characters ab spawn **NAHI** honge."
        )
        
        LOGGER.info(f"Rarity {rarity_num} disabled by user {user_id}")
    
    except ValueError:
        await update.message.reply_text("❌ **Invalid rarity number!**\n\nPlease enter a valid number (1-15).")
    except Exception as e:
        LOGGER.exception(f"Error in set_rarity_off: {e}")
        await update.message.reply_text(f"❌ **Error:** {str(e)}")


# ============================================
# COMMAND 3: LOCK - Character Lock
# ============================================
async def lock_character(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        return
    
    user_id = update.effective_user.id
    
    # Check authorization - agar authorized nahi hai to koi response nahi
    if not is_authorized(user_id):
        return
    
    try:
        # Get character ID and optional reason
        if not context.args or len(context.args) < 1:
            await update.message.reply_text(
                "❌ **Usage:** `/lock <character_id> [reason]`\n\n"
                "**Examples:**\n"
                "`/lock 12345 Too OP`\n"
                "`/lock 67890`"
            )
            return
        
        char_id = context.args[0]
        
        # Lock reason (optional)
        lock_reason = " ".join(context.args[1:]) if len(context.args) > 1 else "No reason provided"
        
        # Check if character exists in collection
        character = await collection.find_one({"id": char_id})
        if not character:
            await update.message.reply_text(
                f"⚠️ **Character ID `{char_id}` not found in database!**\n\n"
                f"Please check the ID and try again."
            )
            return
        
        # Check if character already locked
        existing = await locked_chars_collection.find_one({"character_id": char_id})
        if existing:
            locked_by = existing.get('locked_by', 'Unknown')
            reason = existing.get('lock_reason', 'No reason')
            await update.message.reply_text(
                f"⚠️ **Character Already Locked!**\n\n"
                f"**ID:** `{char_id}`\n"
                f"**Name:** {character.get('name', 'Unknown')}\n"
                f"**Locked By:** {locked_by}\n"
                f"**Reason:** {reason}"
            )
            return
        
        # Character ko lock karna
        lock_data = {
            "character_id": char_id,
            "character_name": character.get('name', 'Unknown'),
            "locked_by": update.effective_user.first_name,
            "locked_by_id": user_id,
            "lock_reason": lock_reason,
        }
        
        await locked_chars_collection.insert_one(lock_data)
        
        rarity_num = character.get('rarity', 'Unknown')
        rarity_display = RARITY_MAP.get(rarity_num, str(rarity_num))
        
        await update.message.reply_text(
            f"🔒 **Character Locked Successfully!**\n\n"
            f"**Character ID:** `{char_id}`\n"
            f"**Name:** {character.get('name', 'Unknown')}\n"
            f"**Anime:** {character.get('anime', 'Unknown')}\n"
            f"**Rarity:** {rarity_display}\n"
            f"**Locked By:** {update.effective_user.first_name}\n"
            f"**Reason:** {lock_reason}\n\n"
            f"✅ Ye character ab spawn **NAHI** hoga."
        )
        
        LOGGER.info(f"Character {char_id} locked by user {user_id}")
    
    except Exception as e:
        LOGGER.exception(f"Error in lock_character: {e}")
        await update.message.reply_text(f"❌ **Error:** {str(e)}")


# ============================================
# COMMAND 4: UNLOCK - Character Unlock
# ============================================
async def unlock_character(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        return
    
    user_id = update.effective_user.id
    
    # Check authorization - agar authorized nahi hai to koi response nahi
    if not is_authorized(user_id):
        return
    
    try:
        # Get character ID
        if not context.args or len(context.args) < 1:
            await update.message.reply_text(
                "❌ **Usage:** `/unlock <character_id>`\n\n"
                "**Example:** `/unlock 12345`"
            )
            return
        
        char_id = context.args[0]
        
        # Check if character is locked
        existing = await locked_chars_collection.find_one({"character_id": char_id})
        if not existing:
            await update.message.reply_text(
                f"⚠️ **Character ID `{char_id}` is NOT locked!**\n\n"
                f"Use `/locklist` to see all locked characters."
            )
            return
        
        # Get character info from main collection
        character = await collection.find_one({"id": char_id})
        char_name = character.get('name', 'Unknown') if character else existing.get('character_name', 'Unknown')
        
        # Character ko unlock karna
        await locked_chars_collection.delete_one({"character_id": char_id})
        
        await update.message.reply_text(
            f"🔓 **Character Unlocked Successfully!**\n\n"
            f"**Character ID:** `{char_id}`\n"
            f"**Name:** {char_name}\n"
            f"**Unlocked By:** {update.effective_user.first_name}\n\n"
            f"✅ Ye character ab wapas spawn ho sakta hai."
        )
        
        LOGGER.info(f"Character {char_id} unlocked by user {user_id}")
    
    except Exception as e:
        LOGGER.exception(f"Error in unlock_character: {e}")
        await update.message.reply_text(f"❌ **Error:** {str(e)}")


# ============================================
# COMMAND 5: LOCKLIST - Show Locked Characters
# ============================================
async def locked_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        return
    
    user_id = update.effective_user.id
    
    # Check authorization - agar authorized nahi hai to koi response nahi
    if not is_authorized(user_id):
        return
    
    try:
        # Get all locked characters
        locked_chars = await locked_chars_collection.find().to_list(length=None)
        
        if not locked_chars:
            await update.message.reply_text(
                "📝 **No Locked Characters Found!**\n\n"
                "Sabhi characters unlock hain aur spawn ho sakte hain."
            )
            return
        
        # Create list message
        list_msg = "🔒 **Locked Characters List:**\n"
        list_msg += f"**Total Locked:** {len(locked_chars)}\n\n"
        list_msg += "━━━━━━━━━━━━━━━━━━\n\n"
        
        for idx, char in enumerate(locked_chars, 1):
            char_id = char.get('character_id', 'Unknown')
            char_name = char.get('character_name', 'Unknown')
            locked_by = char.get('locked_by', 'Unknown')
            reason = char.get('lock_reason', 'No reason')
            
            list_msg += (
                f"**{idx}.** **{char_name}**\n"
                f"   📋 **ID:** `{char_id}`\n"
                f"   👤 **Locked By:** {locked_by}\n"
                f"   📝 **Reason:** {reason}\n\n"
            )
        
        list_msg += "━━━━━━━━━━━━━━━━━━\n"
        list_msg += f"Use `/unlock <id>` to unlock any character."
        
        await update.message.reply_text(list_msg)
    
    except Exception as e:
        LOGGER.exception(f"Error in locked_list: {e}")
        await update.message.reply_text(f"❌ **Error:** {str(e)}")


# ============================================
# HELPER FUNCTIONS - Main.py me use karne ke liye
# ============================================

async def is_rarity_enabled(rarity_num):
    """
    Check if a rarity is enabled in database
    Main.py ke spawn function me use karna hai
    
    Returns:
        bool: True if enabled (or not in DB), False if disabled
    """
    try:
        rarity_data = await rarity_collection.find_one({"rarity": rarity_num})
        if rarity_data:
            return rarity_data.get("enabled", True)  # Default: enabled
        return True  # Agar database me nahi hai to enabled mana jayega
    except Exception as e:
        LOGGER.exception(f"Error checking rarity status: {e}")
        return True  # Error ke case me spawn hone do


async def is_character_locked(char_id):
    """
    Check if a character is locked in database
    Main.py ke spawn function me use karna hai
    
    Returns:
        bool: True if locked, False if not locked
    """
    try:
        locked = await locked_chars_collection.find_one({"character_id": str(char_id)})
        return locked is not None
    except Exception as e:
        LOGGER.exception(f"Error checking character lock status: {e}")
        return False  # Error ke case me spawn hone do


async def can_character_spawn(character):
    """
    Complete check - Rarity aur Lock dono check karta hai
    Main.py me direct use kar sakte ho
    
    Args:
        character: Character dict from collection (main.py se same)
    
    Returns:
        bool: True if can spawn, False if cannot spawn
    """
    try:
        # Check 1: Rarity enabled hai?
        rarity_num = character.get('rarity')
        if rarity_num:
            rarity_ok = await is_rarity_enabled(rarity_num)
            if not rarity_ok:
                LOGGER.debug(f"Character {character.get('id')} blocked: Rarity {rarity_num} disabled")
                return False
        
        # Check 2: Character locked to nahi?
        char_id = character.get('id')
        if char_id:
            is_locked = await is_character_locked(char_id)
            if is_locked:
                LOGGER.debug(f"Character {char_id} blocked: Character is locked")
                return False
        
        return True  # Dono checks pass
    except Exception as e:
        LOGGER.exception(f"Error in can_character_spawn: {e}")
        return True  # Error ke case me spawn hone do


# ============================================
# REGISTER COMMANDS
# ============================================
def setup_handlers():
    """
    Commands ko register karna
    Main.py me isko call karna hai
    """
    application.add_handler(CommandHandler("set_on", set_rarity_on, block=False))
    application.add_handler(CommandHandler("set_off", set_rarity_off, block=False))
    application.add_handler(CommandHandler("lock", lock_character, block=False))
    application.add_handler(CommandHandler("unlock", unlock_character, block=False))
    application.add_handler(CommandHandler("locklist", locked_list, block=False))
    
    LOGGER.info("✅ Setrarity commands registered successfully")


# Export functions for main.py
__all__ = [
    'is_rarity_enabled', 
    'is_character_locked', 
    'can_character_spawn',
    'setup_handlers'
]
