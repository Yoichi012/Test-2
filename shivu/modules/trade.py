from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from datetime import datetime
import time
import asyncio
import logging

from shivu import user_collection, shivuu

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Storage for pending operations
pending_trades = {}  # {(sender_id, receiver_id): {'chars': (s_char_id, r_char_id), 'timestamp': time}}
pending_gifts = {}   # {(sender_id, receiver_id): {'character': char, 'receiver_info': {...}, 'timestamp': time}}

# User locks to prevent concurrent operations
user_locks = {}

# Cooldown tracking
last_trade_time = {}
last_gift_time = {}

# Configuration
TRADE_COOLDOWN = 60  # 60 seconds
GIFT_COOLDOWN = 30   # 30 seconds
PENDING_EXPIRY = 300  # 5 minutes
GIFT_CONFIRM_TIMEOUT = 30  # 30 seconds - gift confirmation timeout

# Rarity mapping with small caps
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
    15: "🧬 ʜʏʙʀɪᴅ"
}

# Small caps conversion map for premium UI
SMALL_CAPS_MAP = {
    'a': 'ᴀ', 'b': 'ʙ', 'c': 'ᴄ', 'd': 'ᴅ', 'e': 'ᴇ', 'f': 'ꜰ', 'g': 'ɢ', 'h': 'ʜ',
    'i': 'ɪ', 'j': 'ᴊ', 'k': 'ᴋ', 'l': 'ʟ', 'm': 'ᴍ', 'n': 'ɴ', 'o': 'ᴏ', 'p': 'ᴘ',
    'q': 'ǫ', 'r': 'ʀ', 's': 'ꜱ', 't': 'ᴛ', 'u': 'ᴜ', 'v': 'ᴠ', 'w': 'ᴡ', 'x': 'x',
    'y': 'ʏ', 'z': 'ᴢ',
    'A': 'ᴀ', 'B': 'ʙ', 'C': 'ᴄ', 'D': 'ᴅ', 'E': 'ᴇ', 'F': 'ꜰ', 'G': 'ɢ', 'H': 'ʜ',
    'I': 'ɪ', 'J': 'ᴊ', 'K': 'ᴋ', 'L': 'ʟ', 'M': 'ᴍ', 'N': 'ɴ', 'O': 'ᴏ', 'P': 'ᴘ',
    'Q': 'ǫ', 'R': 'ʀ', 'S': 'ꜱ', 'T': 'ᴛ', 'U': 'ᴜ', 'V': 'ᴠ', 'W': 'ᴡ', 'X': 'x',
    'Y': 'ʏ', 'Z': 'ᴢ'
}

def to_small_caps(text):
    """Convert text to small caps for premium UI - handles any data type"""
    # Convert to string first to handle integers and other types
    text = str(text) if text is not None else 'Unknown'
    return ''.join(SMALL_CAPS_MAP.get(c, c) for c in text)

def get_user_lock(user_id):
    """Get or create a lock for a specific user"""
    if user_id not in user_locks:
        user_locks[user_id] = asyncio.Lock()
    return user_locks[user_id]

async def cleanup_expired_operations():
    """Clean up expired pending trades and gifts"""
    current_time = time.time()
    
    # Clean expired trades (5 minutes)
    expired_trades = [k for k, v in pending_trades.items() 
                      if current_time - v['timestamp'] > PENDING_EXPIRY]
    for key in expired_trades:
        del pending_trades[key]
        logger.info(f"Cleaned expired trade: {key}")
    
    # Clean expired gifts (30 seconds for confirmation timeout)
    expired_gifts = [k for k, v in pending_gifts.items() 
                     if current_time - v['timestamp'] > GIFT_CONFIRM_TIMEOUT]
    for key in expired_gifts:
        # Remove from cooldown to allow new gift
        sender_id = key[0]
        if sender_id in last_gift_time:
            del last_gift_time[sender_id]
        del pending_gifts[key]
        logger.info(f"Cleaned expired gift: {key} and removed cooldown")

async def auto_cleanup_task():
    """Background task to auto-cleanup expired operations every 60 seconds"""
    while True:
        try:
            await asyncio.sleep(60)
            await cleanup_expired_operations()
        except Exception as e:
            logger.error(f"Error in auto cleanup task: {e}")

# Background task reference
cleanup_task = None

async def start_cleanup_task():
    """Initialize the background cleanup task"""
    global cleanup_task
    if cleanup_task is None:
        cleanup_task = asyncio.create_task(auto_cleanup_task())
        logger.info("Background cleanup task started")

def check_cooldown(user_id, cooldown_dict, cooldown_time):
    """Check if user is on cooldown"""
    current_time = time.time()
    if user_id in cooldown_dict:
        time_passed = current_time - cooldown_dict[user_id]
        if time_passed < cooldown_time:
            remaining = int(cooldown_time - time_passed)
            return False, remaining
    return True, 0

def format_character_info(character):
    """Format character information for display"""
    name = character.get('name', 'Unknown')
    rarity = character.get('rarity', 'Unknown')
    anime = character.get('anime', 'Unknown')
    return f"**{name}**\n⭐ Rarity: {rarity}\n📺 Anime: {anime}"

def format_premium_gift_card(character, sender_name):
    """Format character as premium gift card with small caps"""
    name = character.get('name', 'Unknown')
    anime = character.get('anime', 'Unknown')
    char_id = character.get('id', 'Unknown')
    rarity = character.get('rarity', 'Unknown')
    
    # Get rarity from RARITY_MAP if it's a number
    if isinstance(rarity, int) and rarity in RARITY_MAP:
        rarity_display = RARITY_MAP[rarity]
    elif isinstance(rarity, str):
        # If it's already a string, convert to small caps
        rarity_display = to_small_caps(rarity)
    else:
        rarity_display = to_small_caps(str(rarity))
    
    # Convert to small caps for premium look
    name_sc = to_small_caps(name)
    anime_sc = to_small_caps(anime)
    char_id_sc = to_small_caps(char_id)
    
    card = (
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🎁 {to_small_caps('gift card')}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"✨ {to_small_caps('name')}   : **{name_sc}**\n"
        f"🎬 {to_small_caps('anime')}  : **{anime_sc}**\n"
        f"🆔 {to_small_caps('id')}     : `{char_id_sc}`\n"
        f"⭐ {to_small_caps('rarity')} : {rarity_display}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💎 {to_small_caps('premium gift from')} **{sender_name}**"
    )
    return card


@shivuu.on_message(filters.command("trade"))
async def trade(client, message):
    """Handle trade command"""
    # Start cleanup task if not already running
    await start_cleanup_task()
    
    sender_id = message.from_user.id
    
    # Clean expired operations
    await cleanup_expired_operations()
    
    # Check if replying to a message
    if not message.reply_to_message:
        await message.reply_text("❌ You need to reply to a user's message to trade a character!")
        return
    
    receiver_id = message.reply_to_message.from_user.id
    receiver_mention = message.reply_to_message.from_user.mention
    
    # Check if trading with self
    if sender_id == receiver_id:
        await message.reply_text("❌ You can't trade a character with yourself!")
        return
    
    # Check cooldown
    can_trade, remaining = check_cooldown(sender_id, last_trade_time, TRADE_COOLDOWN)
    if not can_trade:
        await message.reply_text(f"⏳ Please wait {remaining} seconds before trading again!")
        return
    
    # Validate command format
    if len(message.command) != 3:
        await message.reply_text(
            "❌ **Invalid Format!**\n\n"
            "**Usage:** `/trade [Your Character ID] [Other User Character ID]`\n"
            "**Example:** `/trade char123 char456`"
        )
        return
    
    sender_character_id = message.command[1]
    receiver_character_id = message.command[2]
    
    try:
        # DEADLOCK SAFE: Always acquire locks in sorted order
        first_id, second_id = sorted([sender_id, receiver_id])
        async with get_user_lock(first_id):
            async with get_user_lock(second_id):
                # Fetch user data
                sender = await user_collection.find_one({'id': sender_id})
                receiver = await user_collection.find_one({'id': receiver_id})
                
                # Check if users exist
                if not sender:
                    await message.reply_text("❌ You don't have any characters yet!")
                    return
                
                if not receiver:
                    await message.reply_text("❌ The other user doesn't have any characters yet!")
                    return
                
                # Ensure characters field exists and is a list for both users
                sender_characters = sender.get('characters', [])
                receiver_characters = receiver.get('characters', [])
                
                if not isinstance(sender_characters, list):
                    await message.reply_text("❌ Your character data is corrupted. Please contact admin!")
                    logger.error(f"User {sender_id} has non-list characters: {type(sender_characters)}")
                    return
                
                if not isinstance(receiver_characters, list):
                    await message.reply_text("❌ The other user's character data is corrupted!")
                    logger.error(f"User {receiver_id} has non-list characters: {type(receiver_characters)}")
                    return
                
                # Find characters
                sender_character = next(
                    (char for char in sender_characters if char.get('id') == sender_character_id), 
                    None
                )
                receiver_character = next(
                    (char for char in receiver_characters if char.get('id') == receiver_character_id), 
                    None
                )
                
                # Validate characters exist
                if not sender_character:
                    await message.reply_text(
                        f"❌ You don't have character with ID: `{sender_character_id}`\n\n"
                        "Use `/collection` to view your characters!"
                    )
                    return
                
                if not receiver_character:
                    await message.reply_text(
                        f"❌ The other user doesn't have character with ID: `{receiver_character_id}`!"
                    )
                    return
                
                # Check if already in a pending trade
                if (sender_id, receiver_id) in pending_trades or (receiver_id, sender_id) in pending_trades:
                    await message.reply_text("❌ You already have a pending trade with this user!")
                    return
                
                # Store pending trade
                pending_trades[(sender_id, receiver_id)] = {
                    'chars': (sender_character_id, receiver_character_id),
                    'timestamp': time.time(),
                    'sender_character': sender_character,
                    'receiver_character': receiver_character
                }
                
                # Create USER-SPECIFIC keyboard with IDs in callback_data
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Confirm Trade", callback_data=f"confirm_trade:{sender_id}:{receiver_id}")],
                    [InlineKeyboardButton("❌ Cancel Trade", callback_data=f"cancel_trade:{sender_id}:{receiver_id}")]
                ])
                
                # Send trade proposal
                trade_msg = (
                    f"📊 **Trade Proposal**\n\n"
                    f"**{message.from_user.first_name}** wants to trade:\n\n"
                    f"**They Give:**\n{format_character_info(sender_character)}\n\n"
                    f"**They Get:**\n{format_character_info(receiver_character)}\n\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"{receiver_mention}, do you accept this trade?"
                )
                
                await message.reply_text(trade_msg, reply_markup=keyboard)
                
                # Update cooldown
                last_trade_time[sender_id] = time.time()
            
    except Exception as e:
        logger.error(f"Error in trade command: {e}")
        await message.reply_text("❌ An error occurred while processing the trade. Please try again!")


@shivuu.on_callback_query(filters.regex(r"^(confirm_trade|cancel_trade):(\d+):(\d+)$"))
async def on_trade_callback(client, callback_query):
    """Handle trade confirmation/cancellation with USER-SPECIFIC verification"""
    data_parts = callback_query.data.split(":")
    action = data_parts[0]
    sender_id = int(data_parts[1])
    receiver_id = int(data_parts[2])
    
    clicker_id = callback_query.from_user.id
    
    # SECURITY: Only receiver can interact with trade buttons
    if clicker_id != receiver_id:
        await callback_query.answer("❌ This action is not for you!", show_alert=True)
        return
    
    trade_key = (sender_id, receiver_id)
    
    # Check if trade exists
    if trade_key not in pending_trades:
        await callback_query.answer("❌ This trade has expired or doesn't exist!", show_alert=True)
        return
    
    trade_data = pending_trades[trade_key]
    
    # Check if trade expired
    if time.time() - trade_data['timestamp'] > PENDING_EXPIRY:
        del pending_trades[trade_key]
        await callback_query.message.edit_text("❌ This trade has expired!")
        return
    
    if action == "confirm_trade":
        try:
            sender_character_id, receiver_character_id = trade_data['chars']
            
            # DEADLOCK SAFE: Always acquire locks in sorted order
            first_id, second_id = sorted([sender_id, receiver_id])
            async with get_user_lock(first_id):
                async with get_user_lock(second_id):
                    # Re-fetch user data to ensure consistency
                    sender = await user_collection.find_one({'id': sender_id})
                    receiver = await user_collection.find_one({'id': receiver_id})
                    
                    # Verify characters still exist
                    sender_character = next(
                        (char for char in sender.get('characters', []) if char.get('id') == sender_character_id), 
                        None
                    )
                    receiver_character = next(
                        (char for char in receiver.get('characters', []) if char.get('id') == receiver_character_id), 
                        None
                    )
                    
                    if not sender_character or not receiver_character:
                        await callback_query.message.edit_text(
                            "❌ Trade failed! One of the characters no longer exists in the collections."
                        )
                        del pending_trades[trade_key]
                        return
                    
                    # Remove characters from original owners
                    sender['characters'].remove(sender_character)
                    receiver['characters'].remove(receiver_character)
                    
                    # Add characters to new owners
                    sender['characters'].append(receiver_character)
                    receiver['characters'].append(sender_character)
                    
                    # Update database
                    await user_collection.update_one(
                        {'id': sender_id}, 
                        {'$set': {'characters': sender['characters']}}
                    )
                    await user_collection.update_one(
                        {'id': receiver_id}, 
                        {'$set': {'characters': receiver['characters']}}
                    )
                    
                    # Remove from pending
                    del pending_trades[trade_key]
                    
                    # Success message
                    success_msg = (
                        f"✅ **Trade Successful!**\n\n"
                        f"**{callback_query.from_user.first_name}** and their trade partner "
                        f"have successfully exchanged characters!\n\n"
                        f"🎉 Enjoy your new characters!"
                    )
                    
                    await callback_query.message.edit_text(success_msg)
                    await callback_query.answer("✅ Trade completed successfully!", show_alert=True)
                    
                    logger.info(f"Trade completed: {sender_id} <-> {receiver_id}")
                
        except Exception as e:
            logger.error(f"Error confirming trade: {e}")
            await callback_query.answer("❌ Error processing trade!", show_alert=True)
            if trade_key in pending_trades:
                del pending_trades[trade_key]
    
    elif action == "cancel_trade":
        # Remove from pending
        del pending_trades[trade_key]
        
        await callback_query.message.edit_text(
            "❌ **Trade Cancelled**\n\n"
            "The trade has been cancelled by the receiver."
        )
        await callback_query.answer("Trade cancelled!", show_alert=False)
        
        logger.info(f"Trade cancelled: {sender_id} <-> {receiver_id}")


@shivuu.on_message(filters.command("gift"))
async def gift(client, message):
    """Handle gift command with PREMIUM UI"""
    # Start cleanup task if not already running
    await start_cleanup_task()
    
    sender_id = message.from_user.id
    sender_name = message.from_user.first_name
    
    # Clean expired operations
    await cleanup_expired_operations()
    
    # Check if replying to a message
    if not message.reply_to_message:
        await message.reply_text("❌ You need to reply to a user's message to gift a character!")
        return
    
    receiver_id = message.reply_to_message.from_user.id
    receiver_username = message.reply_to_message.from_user.username
    receiver_first_name = message.reply_to_message.from_user.first_name
    receiver_mention = message.reply_to_message.from_user.mention
    
    # Check if gifting to self
    if sender_id == receiver_id:
        await message.reply_text("❌ You can't gift a character to yourself!")
        return
    
    # Check cooldown
    can_gift, remaining = check_cooldown(sender_id, last_gift_time, GIFT_COOLDOWN)
    if not can_gift:
        await message.reply_text(f"⏳ Please wait {remaining} seconds before gifting again!")
        return
    
    # Validate command format
    if len(message.command) != 2:
        await message.reply_text(
            "❌ **Invalid Format!**\n\n"
            "**Usage:** `/gift [Character ID]`\n"
            "**Example:** `/gift char123`"
        )
        return
    
    character_id = message.command[1]
    
    try:
        # Acquire lock for sender
        async with get_user_lock(sender_id):
            # Fetch sender data
            sender = await user_collection.find_one({'id': sender_id})
            
            if not sender:
                await message.reply_text("❌ You don't have any characters yet!")
                return
            
            # Ensure characters field exists and is a list
            characters = sender.get('characters', [])
            if not isinstance(characters, list):
                await message.reply_text("❌ Your character data is corrupted. Please contact admin!")
                logger.error(f"User {sender_id} has non-list characters: {type(characters)}")
                return
            
            # Find character
            character = next(
                (char for char in characters if char.get('id') == character_id), 
                None
            )
            
            if not character:
                await message.reply_text(
                    f"❌ You don't have character with ID: `{character_id}`\n\n"
                    "Use `/collection` to view your characters!"
                )
                return
            
            # Check if already in a pending gift
            if (sender_id, receiver_id) in pending_gifts:
                await message.reply_text("❌ You already have a pending gift for this user!")
                return
            
            # Store pending gift
            pending_gifts[(sender_id, receiver_id)] = {
                'character': character,
                'receiver_username': receiver_username,
                'receiver_first_name': receiver_first_name,
                'timestamp': time.time()
            }
            
            # Create USER-SPECIFIC keyboard with IDs in callback_data
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Confirm Gift", callback_data=f"confirm_gift:{sender_id}:{receiver_id}")],
                [InlineKeyboardButton("❌ Cancel Gift", callback_data=f"cancel_gift:{sender_id}:{receiver_id}")]
            ])
            
            # PREMIUM GIFT CARD UI with small caps
            gift_card = format_premium_gift_card(character, sender_name)
            
            gift_msg = (
                f"{gift_card}\n\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"Are you sure you want to gift this to {receiver_mention}?\n\n"
                f"⏰ {to_small_caps('you have 30 seconds to confirm')}"
            )
            
            await message.reply_text(gift_msg, reply_markup=keyboard)
            
            # Update cooldown
            last_gift_time[sender_id] = time.time()
            
    except Exception as e:
        logger.error(f"Error in gift command: {e}")
        await message.reply_text("❌ An error occurred while processing the gift. Please try again!")


@shivuu.on_callback_query(filters.regex(r"^(confirm_gift|cancel_gift):(\d+):(\d+)$"))
async def on_gift_callback(client, callback_query):
    """Handle gift confirmation/cancellation with USER-SPECIFIC verification"""
    data_parts = callback_query.data.split(":")
    action = data_parts[0]
    sender_id = int(data_parts[1])
    receiver_id = int(data_parts[2])
    
    clicker_id = callback_query.from_user.id
    
    # SECURITY: Only sender can interact with gift buttons
    if clicker_id != sender_id:
        await callback_query.answer("❌ This action is not for you!", show_alert=True)
        return
    
    gift_key = (sender_id, receiver_id)
    
    # Check if gift exists
    if gift_key not in pending_gifts:
        await callback_query.answer("❌ This gift has expired or doesn't exist!", show_alert=True)
        return
    
    gift_data = pending_gifts[gift_key]
    
    # Check if gift expired (30 seconds timeout)
    if time.time() - gift_data['timestamp'] > GIFT_CONFIRM_TIMEOUT:
        del pending_gifts[gift_key]
        # Remove cooldown to allow new gift
        if sender_id in last_gift_time:
            del last_gift_time[sender_id]
        await callback_query.message.edit_text(
            "❌ This gift request has expired!\n\n"
            "You can now send a new gift."
        )
        return
    
    if action == "confirm_gift":
        try:
            character = gift_data['character']
            
            # DEADLOCK SAFE: Always acquire locks in sorted order
            first_id, second_id = sorted([sender_id, receiver_id])
            async with get_user_lock(first_id):
                async with get_user_lock(second_id):
                    # Re-fetch sender data
                    sender = await user_collection.find_one({'id': sender_id})
                    
                    # Verify character still exists
                    sender_character = next(
                        (char for char in sender.get('characters', []) if char.get('id') == character['id']), 
                        None
                    )
                    
                    if not sender_character:
                        await callback_query.message.edit_text(
                            "❌ Gift failed! The character no longer exists in your collection."
                        )
                        del pending_gifts[gift_key]
                        return
                    
                    # Remove character from sender
                    sender['characters'].remove(sender_character)
                    await user_collection.update_one(
                        {'id': sender_id}, 
                        {'$set': {'characters': sender['characters']}}
                    )
                    
                    # Add character to receiver
                    receiver = await user_collection.find_one({'id': receiver_id})
                    
                    if receiver:
                        # Receiver exists, add to their collection
                        await user_collection.update_one(
                            {'id': receiver_id}, 
                            {'$push': {'characters': character}}
                        )
                    else:
                        # Create new user document for receiver
                        await user_collection.insert_one({
                            'id': receiver_id,
                            'username': gift_data['receiver_username'],
                            'first_name': gift_data['receiver_first_name'],
                            'characters': [character],
                        })
                    
                    # Remove from pending
                    del pending_gifts[gift_key]
                    
                    # PREMIUM SUCCESS MESSAGE with small caps
                    char_name = character.get('name', 'Unknown')
                    char_name_sc = to_small_caps(char_name)
                    
                    success_msg = (
                        f"🎉 **{to_small_caps('gift successful')}**\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"💝 **{char_name_sc}** {to_small_caps('has been sent')}\n"
                        f"{to_small_caps('to')} **{gift_data['receiver_first_name']}**\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"✨ {to_small_caps('thank you for being generous')}"
                    )
                    
                    await callback_query.message.edit_text(success_msg)
                    await callback_query.answer("✅ Gift sent successfully!", show_alert=True)
                    
                    logger.info(f"Gift completed: {sender_id} -> {receiver_id}")
                
        except Exception as e:
            logger.error(f"Error confirming gift: {e}")
            await callback_query.answer("❌ Error processing gift!", show_alert=True)
            if gift_key in pending_gifts:
                del pending_gifts[gift_key]
    
    elif action == "cancel_gift":
        # Remove from pending
        del pending_gifts[gift_key]
        
        await callback_query.message.edit_text(
            "❌ **Gift Cancelled**\n\n"
            "The gift has been cancelled."
        )
        await callback_query.answer("Gift cancelled!", show_alert=False)
        
        logger.info(f"Gift cancelled: {sender_id} -> {receiver_id}")


# Optional: Command to check pending trades/gifts
@shivuu.on_message(filters.command("pending"))
async def check_pending(client, message):
    """Check user's pending trades and gifts"""
    user_id = message.from_user.id
    
    await cleanup_expired_operations()
    
    # Find user's pending operations
    user_trades = []
    user_gifts = []
    
    for (sender_id, receiver_id), data in pending_trades.items():
        if sender_id == user_id:
            user_trades.append(f"• Trade as sender (waiting for receiver)")
        elif receiver_id == user_id:
            user_trades.append(f"• Trade as receiver (pending your confirmation)")
    
    for (sender_id, receiver_id), data in pending_gifts.items():
        if sender_id == user_id:
            user_gifts.append(f"• Gift (pending your confirmation)")
    
    if not user_trades and not user_gifts:
        await message.reply_text("✅ You have no pending trades or gifts!")
        return
    
    msg = "📋 **Your Pending Operations:**\n\n"
    
    if user_trades:
        msg += "**Trades:**\n" + "\n".join(user_trades) + "\n\n"
    
    if user_gifts:
        msg += "**Gifts:**\n" + "\n".join(user_gifts)
    
    await message.reply_text(msg)


# Optional: Admin command to clear all pending operations
# Replace ADMIN_USER_IDS with actual admin user IDs in list format
ADMIN_USER_IDS = [123456789, 987654321]  # Add your admin user IDs here

@shivuu.on_message(filters.command("clearpending") & filters.user(ADMIN_USER_IDS))
async def clear_pending(client, message):
    """Clear all pending trades and gifts (Admin only)"""
    pending_trades.clear()
    pending_gifts.clear()
    last_trade_time.clear()
    last_gift_time.clear()
    await message.reply_text("✅ All pending operations and cooldowns have been cleared!")
