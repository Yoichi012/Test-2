"""
Redeem System for Telegram Bot
Supports coin and character redeem codes with usage limits

Database: Character_catcher
Main Collection: anime_characters_lol (character data source)
User Collection: user_collection_lmaoooo (user character storage)
"""

import secrets
import string
from typing import Optional, Dict, Any
from html import escape

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from shivu import application, user_collection, collection, db, LOGGER, OWNER_ID, SUDO_USERS

# MongoDB setup - using the same db as other modules
redeem_codes_collection = db.redeem_codes


# ---------- Small Caps Utility (matching your existing style) ----------
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


# ---------- Code Generation ----------
def generate_unique_code(length: int = 12) -> str:
    """Generate a unique alphanumeric redeem code."""
    # Use uppercase letters and digits for readability
    alphabet = string.ascii_uppercase + string.digits
    # Remove confusing characters: 0, O, I, 1
    alphabet = alphabet.replace('0', '').replace('O', '').replace('I', '').replace('1', '')
    code = ''.join(secrets.choice(alphabet) for _ in range(length))
    return code


# ---------- Database Operations ----------
async def create_coin_code(amount: int, max_uses: int, created_by: int) -> Optional[str]:
    """Create a coin redeem code in the database."""
    if redeem_codes_collection is None:
        LOGGER.error("Redeem codes collection not initialized")
        return None
    
    try:
        # Generate unique code
        code = generate_unique_code()
        
        # Ensure code is unique
        while await redeem_codes_collection.find_one({"code": code}):
            code = generate_unique_code()
        
        # Create document
        document = {
            "code": code,
            "type": "coin",
            "amount": int(amount),
            "max_uses": int(max_uses),
            "used_by": [],
            "is_active": True,
            "created_by": int(created_by)
        }
        
        await redeem_codes_collection.insert_one(document)
        LOGGER.info(f"Created coin code: {code} for {amount} coins, max uses: {max_uses}")
        return code
    except Exception as e:
        LOGGER.error(f"Failed to create coin code: {e}")
        return None


async def create_character_code(character_id: int, max_uses: int, created_by: int) -> Optional[str]:
    """
    Create a character redeem code in the database.
    Validates character exists in anime_characters_lol collection.
    """
    if redeem_codes_collection is None:
        LOGGER.error("Redeem codes collection not initialized")
        return None
    
    try:
        # Verify character exists in main collection (anime_characters_lol)
        # Try both integer and string format
        character = await collection.find_one({"id": character_id})
        if not character:
            character = await collection.find_one({"id": str(character_id)})
        
        if not character:
            LOGGER.warning(f"Character ID {character_id} not found in anime_characters_lol collection")
            return None
        
        # Generate unique code
        code = generate_unique_code()
        
        # Ensure code is unique
        while await redeem_codes_collection.find_one({"code": code}):
            code = generate_unique_code()
        
        # Create document
        document = {
            "code": code,
            "type": "character",
            "character_id": int(character_id),
            "max_uses": int(max_uses),
            "used_by": [],
            "is_active": True,
            "created_by": int(created_by)
        }
        
        await redeem_codes_collection.insert_one(document)
        LOGGER.info(f"Created character code: {code} for character {character_id}, max uses: {max_uses}")
        return code
    except Exception as e:
        LOGGER.error(f"Failed to create character code: {e}")
        return None


async def redeem_code(code: str, user_id: int) -> Dict[str, Any]:
    """
    Redeem a code for a user.
    Returns dict with 'success', 'message', and optional 'data' keys.
    
    For character codes:
    - Fetches full character from anime_characters_lol (id, name, anime, rarity, img_url)
    - Adds to user_collection_lmaoooo.characters array
    - Handles duplicates gracefully
    """
    if redeem_codes_collection is None:
        return {"success": False, "message": "System error: database not available"}
    
    try:
        # Find the code
        code_doc = await redeem_codes_collection.find_one({"code": code.upper()})
        
        if not code_doc:
            return {
                "success": False, 
                "message": "⚠️ ɪɴᴠᴀʟɪᴅ ᴄᴏᴅᴇ.\nᴛʜɪs ᴄᴏᴅᴇ ᴅᴏᴇs ɴᴏᴛ ᴇxɪsᴛ.",
                "show_alert": True
            }
        
        # Check if code is active
        if not code_doc.get("is_active", False):
            return {
                "success": False,
                "message": "⚠️ ᴛʜɪs ᴄᴏᴅᴇ ʜᴀs ᴀʟʀᴇᴀᴅʏ ʙᴇᴇɴ ʀᴇᴅᴇᴇᴍᴇᴅ.\nʙᴇᴛᴛᴇʀ ʟᴜᴄᴋ ɴᴇxᴛ ᴛɪᴍᴇ!",
                "show_alert": True
            }
        
        # Check if user already redeemed this code
        used_by = code_doc.get("used_by", [])
        if user_id in used_by:
            return {
                "success": False,
                "message": "⚠️ ʏᴏᴜ ʜᴀᴠᴇ ᴀʟʀᴇᴀᴅʏ ʀᴇᴅᴇᴇᴍᴇᴅ ᴛʜɪs ᴄᴏᴅᴇ.",
                "show_alert": True
            }
        
        # Check if max uses reached
        max_uses = code_doc.get("max_uses", 1)
        current_uses = len(used_by)
        
        if current_uses >= max_uses:
            # Deactivate the code
            await redeem_codes_collection.update_one(
                {"code": code.upper()},
                {"$set": {"is_active": False}}
            )
            return {
                "success": False,
                "message": "⚠️ ᴛʜɪs ᴄᴏᴅᴇ ʜᴀs ᴀʟʀᴇᴀᴅʏ ʙᴇᴇɴ ʀᴇᴅᴇᴇᴍᴇᴅ.\nʙᴇᴛᴛᴇʀ ʟᴜᴄᴋ ɴᴇxᴛ ᴛɪᴍᴇ!",
                "show_alert": True
            }
        
        # Process redemption based on type
        code_type = code_doc.get("type")
        
        if code_type == "coin":
            # Add coins to user balance
            amount = code_doc.get("amount", 0)
            
            # Update user balance
            await user_collection.update_one(
                {"id": user_id},
                {
                    "$inc": {"balance": int(amount)},
                    "$setOnInsert": {"id": user_id, "characters": [], "favorites": []}
                },
                upsert=True
            )
            
            # Update code document
            await redeem_codes_collection.update_one(
                {"code": code.upper()},
                {"$push": {"used_by": user_id}}
            )
            
            # Check if we should deactivate
            if current_uses + 1 >= max_uses:
                await redeem_codes_collection.update_one(
                    {"code": code.upper()},
                    {"$set": {"is_active": False}}
                )
            
            LOGGER.info(f"User {user_id} redeemed coin code {code} for {amount} coins")
            
            return {
                "success": True,
                "message": f"✓ ᴄᴏᴅᴇ ʀᴇᴅᴇᴇᴍᴇᴅ sᴜᴄᴄᴇssғᴜʟʟʏ!\n\n💰 ʏᴏᴜ ʀᴇᴄᴇɪᴠᴇᴅ <b>{amount:,}</b> ᴄᴏɪɴs!",
                "data": {"type": "coin", "amount": amount}
            }
        
        elif code_type == "character":
            # Get character ID from code
            character_id = code_doc.get("character_id")
            
            # Fetch FULL character details from anime_characters_lol (main collection)
            # Try both integer and string format
            character = await collection.find_one({"id": character_id})
            if not character:
                character = await collection.find_one({"id": str(character_id)})
            
            if not character:
                LOGGER.error(f"Character {character_id} not found in anime_characters_lol during redeem")
                return {
                    "success": False,
                    "message": "✘ ᴄʜᴀʀᴀᴄᴛᴇʀ ɴᴏ ʟᴏɴɢᴇʀ ᴇxɪsᴛs ɪɴ ᴅᴀᴛᴀʙᴀsᴇ.",
                    "show_alert": True
                }
            
            # Extract required fields (id, name, anime, rarity, img_url)
            character_entry = {
                "id": character.get("id"),
                "name": character.get("name"),
                "anime": character.get("anime"),
                "rarity": character.get("rarity"),
                "img_url": character.get("img_url")
            }
            
            # Check if user already has this character (duplicate detection)
            user_doc = await user_collection.find_one({"id": user_id})
            is_duplicate = False
            
            if user_doc:
                user_characters = user_doc.get("characters", [])
                # Check if character ID already exists
                is_duplicate = any(char.get("id") == character_id for char in user_characters)
            
            # Add character to user's collection (even if duplicate)
            await user_collection.update_one(
                {"id": user_id},
                {
                    "$push": {"characters": character_entry},
                    "$setOnInsert": {"id": user_id, "balance": 0, "favorites": []}
                },
                upsert=True
            )
            
            # Mark code as used
            await redeem_codes_collection.update_one(
                {"code": code.upper()},
                {"$push": {"used_by": user_id}}
            )
            
            # Check if we should deactivate
            if current_uses + 1 >= max_uses:
                await redeem_codes_collection.update_one(
                    {"code": code.upper()},
                    {"$set": {"is_active": False}}
                )
            
            LOGGER.info(f"User {user_id} redeemed character code {code} for character {character_id} (duplicate: {is_duplicate})")
            
            character_name = character.get("name", "Unknown")
            anime_name = character.get("anime", "Unknown")
            
            # Build success message
            if is_duplicate:
                message = (
                    f"✓ ᴄᴏᴅᴇ ʀᴇᴅᴇᴇᴍᴇᴅ sᴜᴄᴄᴇssғᴜʟʟʏ!\n\n"
                    f"🎉 ʏᴏᴜ ʀᴇᴄᴇɪᴠᴇᴅ:\n"
                    f"<b>{escape(character_name)}</b>\n"
                    f"ғʀᴏᴍ <i>{escape(anime_name)}</i>\n\n"
                    f"ℹ️ ʏᴏᴜ ᴀʟʀᴇᴀᴅʏ ʜᴀᴅ ᴛʜɪs ᴄʜᴀʀᴀᴄᴛᴇʀ.\nᴅᴜᴘʟɪᴄᴀᴛᴇ ᴀᴅᴅᴇᴅ ᴛᴏ ʏᴏᴜʀ ᴄᴏʟʟᴇᴄᴛɪᴏɴ!"
                )
            else:
                message = (
                    f"✓ ᴄᴏᴅᴇ ʀᴇᴅᴇᴇᴍᴇᴅ sᴜᴄᴄᴇssғᴜʟʟʏ!\n\n"
                    f"🎉 ʏᴏᴜ ʀᴇᴄᴇɪᴠᴇᴅ:\n"
                    f"<b>{escape(character_name)}</b>\n"
                    f"ғʀᴏᴍ <i>{escape(anime_name)}</i>"
                )
            
            return {
                "success": True,
                "message": message,
                "data": {
                    "type": "character",
                    "character_id": character_id,
                    "character_name": character_name,
                    "is_duplicate": is_duplicate
                }
            }
        
        else:
            return {
                "success": False,
                "message": "✘ ᴜɴᴋɴᴏᴡɴ ᴄᴏᴅᴇ ᴛʏᴘᴇ.",
                "show_alert": True
            }
    
    except Exception as e:
        LOGGER.error(f"Failed to redeem code {code} for user {user_id}: {e}")
        return {
            "success": False,
            "message": "✘ sʏsᴛᴇᴍ ᴇʀʀᴏʀ. ᴘʟᴇᴀsᴇ ᴛʀʏ ᴀɢᴀɪɴ ʟᴀᴛᴇʀ.",
            "show_alert": True
        }


# ---------- Command Handlers ----------
async def gen_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /gen <amount> <max_users>
    Generate a coin redeem code. Admin only.
    """
    user_id = update.effective_user.id
    
    # Check permissions
    if user_id != OWNER_ID and user_id not in SUDO_USERS:
        await update.message.reply_text(to_small_caps("✘ You are not authorized to use this command."))
        return
    
    # Validate arguments
    if len(context.args) < 2:
        usage_msg = (
            f"<b>{to_small_caps('COIN CODE GENERATOR')}</b>\n\n"
            f"{to_small_caps('Usage:')} <code>/gen &lt;amount&gt; &lt;max_users&gt;</code>\n\n"
            f"{to_small_caps('Example:')} <code>/gen 100 10</code>\n"
            f"{to_small_caps('This creates a code for 100 coins that can be redeemed by 10 users.')}"
        )
        await update.message.reply_text(usage_msg, parse_mode="HTML")
        return
    
    try:
        amount = int(context.args[0])
        max_uses = int(context.args[1])
    except ValueError:
        await update.message.reply_text(
            f"{to_small_caps('✘ Invalid arguments. Amount and max users must be positive integers.')}"
        )
        return
    
    if amount <= 0:
        await update.message.reply_text(to_small_caps("✘ Amount must be greater than 0."))
        return
    
    if max_uses <= 0:
        await update.message.reply_text(to_small_caps("✘ Max users must be greater than 0."))
        return
    
    # Create code
    code = await create_coin_code(amount, max_uses, user_id)
    
    if code:
        response = (
            f"<b>{to_small_caps('✓ COIN CODE GENERATED')}</b>\n\n"
            f"<b>{to_small_caps('Code:')}</b> <code>{code}</code>\n"
            f"<b>{to_small_caps('Type:')}</b> {to_small_caps('Coins')}\n"
            f"<b>{to_small_caps('Amount:')}</b> {amount:,} {to_small_caps('coins')}\n"
            f"<b>{to_small_caps('Max Uses:')}</b> {max_uses}\n\n"
            f"{to_small_caps('Users can redeem with:')} <code>/redeem {code}</code>"
        )
        await update.message.reply_text(response, parse_mode="HTML")
    else:
        await update.message.reply_text(
            f"{to_small_caps('✘ Failed to generate code. Please try again.')}"
        )


async def debug_db_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /debugdb - Check database connection and collection info (Admin only)
    """
    user_id = update.effective_user.id
    
    # Check permissions
    if user_id != OWNER_ID and user_id not in SUDO_USERS:
        await update.message.reply_text("✘ Not authorized")
        return
    
    try:
        # Check collection name
        collection_name = collection.name
        db_name = collection.database.name
        
        # Count documents
        total_chars = await collection.count_documents({})
        
        # Get some sample characters with ALL fields
        sample_chars = await collection.find({}).limit(3).to_list(length=3)
        
        # Build debug info
        debug_info = (
            f"<b>📊 DATABASE DEBUG INFO</b>\n\n"
            f"<b>Database:</b> {db_name}\n"
            f"<b>Collection:</b> {collection_name}\n"
            f"<b>Total Characters:</b> {total_chars}\n\n"
        )
        
        if sample_chars:
            debug_info += "<b>Sample Characters (with field names):</b>\n"
            for i, char in enumerate(sample_chars, 1):
                debug_info += f"\n<b>Character {i}:</b>\n"
                # Show all fields
                for key, value in char.items():
                    if key != '_id':  # Skip MongoDB internal ID
                        debug_info += f"  • {key}: {str(value)[:50]}\n"
        else:
            debug_info += "⚠️ No characters found in collection!\n"
        
        # Check user_collection too
        user_coll_name = user_collection.name
        total_users = await user_collection.count_documents({})
        debug_info += f"\n<b>User Collection:</b> {user_coll_name}\n"
        debug_info += f"<b>Total Users:</b> {total_users}\n"
        
        await update.message.reply_text(debug_info, parse_mode="HTML")
        
    except Exception as e:
        LOGGER.error(f"Debug command error: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")


async def sgen_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /sgen <character_id> <max_users>
    Generate a character redeem code. Admin only.
    Fetches character data from anime_characters_lol (main collection).
    """
    user_id = update.effective_user.id
    
    # Check permissions
    if user_id != OWNER_ID and user_id not in SUDO_USERS:
        await update.message.reply_text(to_small_caps("✘ You are not authorized to use this command."))
        return
    
    # Validate arguments
    if len(context.args) < 2:
        usage_msg = (
            f"<b>{to_small_caps('CHARACTER CODE GENERATOR')}</b>\n\n"
            f"{to_small_caps('Usage:')} <code>/sgen &lt;character_id&gt; &lt;max_users&gt;</code>\n\n"
            f"{to_small_caps('Example:')} <code>/sgen 25 10</code>\n"
            f"{to_small_caps('This creates a code for character ID 25 that can be redeemed by 10 users.')}"
        )
        await update.message.reply_text(usage_msg, parse_mode="HTML")
        return
    
    try:
        character_id = int(context.args[0])
        max_uses = int(context.args[1])
    except ValueError:
        await update.message.reply_text(
            f"{to_small_caps('✘ Invalid arguments. Character ID and max users must be positive integers.')}"
        )
        return
    
    if character_id <= 0:
        await update.message.reply_text(to_small_caps("✘ Character ID must be greater than 0."))
        return
    
    if max_uses <= 0:
        await update.message.reply_text(to_small_caps("✘ Max users must be greater than 0."))
        return
    
    # Fetch character from anime_characters_lol (main collection)
    # Try both integer and string format for ID
    LOGGER.info(f"Searching for character with ID: {character_id} (type: {type(character_id).__name__})")
    
    character = await collection.find_one({"id": character_id})
    LOGGER.info(f"Integer search result: {character is not None}")
    
    # If not found with integer, try string
    if not character:
        LOGGER.info(f"Trying string format: '{str(character_id)}'")
        character = await collection.find_one({"id": str(character_id)})
        LOGGER.info(f"String search result: {character is not None}")
    
    if not character:
        # Get helpful database info with actual available IDs
        try:
            # Get all character IDs
            all_chars = await collection.find({}, {"id": 1, "name": 1}).sort("id", 1).to_list(length=None)
            total_chars = len(all_chars)
            
            if total_chars > 0:
                # Get available IDs
                available_ids = [char['id'] for char in all_chars]
                min_id = min(available_ids)
                max_id = max(available_ids)
                
                # Show first 10 available IDs as examples
                example_ids = ", ".join(str(id) for id in available_ids[:10])
                if len(available_ids) > 10:
                    example_ids += "..."
                
                error_msg = (
                    f"<b>{to_small_caps('✘ Character Not Found')}</b>\n\n"
                    f"{to_small_caps(f'Character ID {character_id} does not exist in database.')}\n\n"
                    f"<b>{to_small_caps('Database Info:')}</b>\n"
                    f"{to_small_caps(f'• Total Characters: {total_chars}')}\n"
                    f"{to_small_caps(f'• ID Range: {min_id} - {max_id}')}\n"
                    f"{to_small_caps(f'• Available IDs: {example_ids}')}\n\n"
                    f"{to_small_caps('Tip: Try one of the available IDs listed above!')}"
                )
            else:
                error_msg = f"{to_small_caps('✘ No characters found in database!')}"
        except Exception as e:
            LOGGER.error(f"Error fetching database stats: {e}")
            error_msg = f"{to_small_caps(f'✘ Character ID {character_id} not found in database.')}"
        
        await update.message.reply_text(error_msg, parse_mode="HTML")
        return
    
    # Create code (validated character exists)
    code = await create_character_code(character_id, max_uses, user_id)
    
    if code:
        character_name = character.get("name", "Unknown")
        anime_name = character.get("anime", "Unknown")
        rarity = character.get("rarity", 1)
        
        response = (
            f"<b>{to_small_caps('✓ CHARACTER CODE GENERATED')}</b>\n\n"
            f"<b>{to_small_caps('Code:')}</b> <code>{code}</code>\n"
            f"<b>{to_small_caps('Type:')}</b> {to_small_caps('Character')}\n"
            f"<b>{to_small_caps('Character:')}</b> {escape(character_name)}\n"
            f"<b>{to_small_caps('Anime:')}</b> {escape(anime_name)}\n"
            f"<b>{to_small_caps('ID:')}</b> {character_id}\n"
            f"<b>{to_small_caps('Rarity:')}</b> {rarity}\n"
            f"<b>{to_small_caps('Max Uses:')}</b> {max_uses}\n\n"
            f"{to_small_caps('Users can redeem with:')} <code>/redeem {code}</code>"
        )
        await update.message.reply_text(response, parse_mode="HTML")
        
        LOGGER.info(f"Generated character code {code} for ID {character_id} ({character_name}) by user {user_id}")
    else:
        await update.message.reply_text(
            f"{to_small_caps('✘ Failed to generate code. Please try again.')}"
        )


async def redeem_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /redeem <code>
    Redeem a coin or character code.
    """
    user_id = update.effective_user.id
    
    # Validate arguments
    if len(context.args) < 1:
        usage_msg = (
            f"<b>{to_small_caps('REDEEM CODE')}</b>\n\n"
            f"{to_small_caps('Usage:')} <code>/redeem &lt;CODE&gt;</code>\n\n"
            f"{to_small_caps('Example:')} <code>/redeem ABC123XYZ789</code>\n"
            f"{to_small_caps('Redeem codes can give you coins or characters!')}"
        )
        await update.message.reply_text(usage_msg, parse_mode="HTML")
        return
    
    code = context.args[0].upper()
    
    # Process redemption
    result = await redeem_code(code, user_id)
    
    if result["success"]:
        await update.message.reply_text(result["message"], parse_mode="HTML")
    else:
        # Show alert/popup style message
        await update.message.reply_text(result["message"], parse_mode="HTML")


# ---------- Handler Registration ----------
def register_handlers():
    """Register all redeem system handlers with the application."""
    application.add_handler(CommandHandler("gen", gen_command, block=False))
    application.add_handler(CommandHandler("sgen", sgen_command, block=False))
    application.add_handler(CommandHandler("redeem", redeem_command, block=False))
    application.add_handler(CommandHandler("debugdb", debug_db_command, block=False))
    LOGGER.info("Redeem system handlers registered successfully")


# Auto-register handlers when module is imported
register_handlers()
