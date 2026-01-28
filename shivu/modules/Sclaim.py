import random
import secrets
import string
from datetime import datetime, timedelta
from typing import Optional
from html import escape

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler

from shivu import application, user_collection, collection, db, LOGGER

# MongoDB collections
claim_codes_collection = db.claim_codes

# Configuration
ALLOWED_GROUP_ID = -1003100468240
SUPPORT_GROUP = "https://t.me/THE_DRAGON_SUPPORT"
SUPPORT_CHANNEL = "https://t.me/Senpai_Updates"
SUPPORT_GROUP_ID = "@THE_DRAGON_SUPPORT"
SUPPORT_CHANNEL_ID = "@Senpai_Updates"

# Allowed rarities for sclaim (2=Rare, 3=Legendary, 4=Special)
ALLOWED_RARITIES = [2, 3, 4]

# Rarity Mapping
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

# Small Caps Map
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
    '0': '0', '1': '1', '2': '2', '3': '3', '4': '4', '5': '5',
    '6': '6', '7': '7', '8': '8', '9': '9'
}


def to_small_caps(text: str) -> str:
    """Convert text to small caps Unicode characters."""
    return ''.join(SMALL_CAPS_MAP.get(char, char) for char in str(text))


def get_rarity_display(rarity: int) -> str:
    """Get rarity display string with emoji and name."""
    return RARITY_MAP.get(rarity, f"⚪ ᴜɴᴋɴᴏᴡɴ ({rarity})")


def generate_coin_code(length: int = 8) -> str:
    """Generate a unique coin code"""
    alphabet = string.ascii_uppercase + string.digits
    alphabet = alphabet.replace('0', '').replace('O', '').replace('I', '').replace('L', '').replace('1', '')
    random_part = ''.join(secrets.choice(alphabet) for _ in range(length))
    return f"COIN-{random_part}"


# ---------- Helper Functions ----------
async def check_membership(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if user has joined support group and channel"""
    user_id = update.effective_user.id
    
    try:
        # Check support group membership
        group_member = await context.bot.get_chat_member(SUPPORT_GROUP_ID, user_id)
        if group_member.status in ['left', 'kicked']:
            return False
        
        # Check support channel membership
        channel_member = await context.bot.get_chat_member(SUPPORT_CHANNEL_ID, user_id)
        if channel_member.status in ['left', 'kicked']:
            return False
        
        return True
    except Exception as e:
        LOGGER.error(f"Error checking membership: {e}")
        return False


async def show_join_buttons(update: Update):
    """Show join buttons for support group and channel"""
    keyboard = [
        [InlineKeyboardButton("📢 Support Channel", url=SUPPORT_CHANNEL)],
        [InlineKeyboardButton("👥 Support Group", url=SUPPORT_GROUP)]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"<b>⚠️ {to_small_caps('JOIN REQUIRED')}</b>\n\n"
        f"🔒 {to_small_caps('You need to join our Support Group and Channel first!')}\n\n"
        f"📌 {to_small_caps('Please join both and try again:')}",
        reply_markup=reply_markup,
        parse_mode="HTML"
    )


async def check_cooldown(user_id: int, command_type: str) -> bool:
    """Check if user can use the command (24hr cooldown)"""
    user = await user_collection.find_one({"id": user_id})
    
    if not user:
        return True
    
    last_claim_time = user.get(f"last_{command_type}", None)
    
    if last_claim_time is None:
        return True
    
    time_diff = datetime.utcnow() - last_claim_time
    if time_diff >= timedelta(hours=24):
        return True
    
    return False


async def get_cooldown_time(user_id: int, command_type: str) -> Optional[str]:
    """Get remaining cooldown time"""
    user = await user_collection.find_one({"id": user_id})
    
    if not user or not user.get(f"last_{command_type}"):
        return None
    
    last_claim_time = user[f"last_{command_type}"]
    next_claim_time = last_claim_time + timedelta(hours=24)
    remaining = next_claim_time - datetime.utcnow()
    
    hours = int(remaining.total_seconds() // 3600)
    minutes = int((remaining.total_seconds() % 3600) // 60)
    
    return f"{hours}h {minutes}m"


# ---------- Command Handlers ----------
async def sclaim_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /sclaim - Claim a random character (Rare, Legendary, or Special only)
    Works only in allowed group with 24hr cooldown
    """
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    # Check if command is used in allowed group
    if chat_id != ALLOWED_GROUP_ID:
        await show_join_buttons(update)
        return
    
    # Check membership
    is_member = await check_membership(update, context)
    if not is_member:
        await show_join_buttons(update)
        return
    
    # Check cooldown
    can_claim = await check_cooldown(user_id, "sclaim")
    if not can_claim:
        remaining_time = await get_cooldown_time(user_id, "sclaim")
        await update.message.reply_text(
            f"<b>⏰ {to_small_caps('COOLDOWN ACTIVE')}</b>\n\n"
            f"⏳ {to_small_caps(f'You can use /sclaim again in:')} <b>{remaining_time}</b>\n\n"
            f"💡 {to_small_caps('Come back later!')}",
            parse_mode="HTML"
        )
        return
    
    # Get random character from allowed rarities (anime_characters_lol collection)
    pipeline = [
        {"$match": {"rarity": {"$in": ALLOWED_RARITIES}}},
        {"$sample": {"size": 1}}
    ]
    
    characters = await collection.aggregate(pipeline).to_list(1)
    
    if not characters:
        await update.message.reply_text(
            f"❌ {to_small_caps('No characters available at the moment!')}"
        )
        return
    
    character = characters[0]
    character_id = character.get("id")
    character_name = character.get("name", "Unknown")
    anime_name = character.get("anime", "Unknown")
    rarity = character.get("rarity", 1)
    img_url = character.get("img_url", "")
    
    # Add character to user's collection (user_collection_lmaoooo.characters)
    await user_collection.update_one(
        {"id": user_id},
        {
            "$push": {
                "characters": {
                    "id": character_id,
                    "name": character_name,
                    "anime": anime_name,
                    "rarity": rarity,
                    "img_url": img_url
                }
            },
            "$set": {"last_sclaim": datetime.utcnow()}
        },
        upsert=True
    )
    
    rarity_display = get_rarity_display(rarity)
    
    # Send character with image
    message = (
        f"<b>🎉 {to_small_caps('CONGRATULATIONS!')}</b>\n\n"
        f"🎴 <b>{to_small_caps('Character:')}</b> {escape(character_name)}\n"
        f"📺 <b>{to_small_caps('Anime:')}</b> {escape(anime_name)}\n"
        f"⭐ <b>{to_small_caps('Rarity:')}</b> {rarity_display}\n"
        f"🆔 <b>{to_small_caps('ID:')}</b> {character_id}\n\n"
        f"✅ {to_small_caps('Character has been added to your collection!')}"
    )
    
    if img_url:
        try:
            await update.message.reply_photo(
                photo=img_url,
                caption=message,
                parse_mode="HTML"
            )
        except Exception as e:
            LOGGER.error(f"Failed to send image: {e}")
            await update.message.reply_text(message, parse_mode="HTML")
    else:
        await update.message.reply_text(message, parse_mode="HTML")
    
    LOGGER.info(f"User {user_id} claimed character {character_id} ({character_name}) via /sclaim")


async def claim_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /claim - Generate a coin code (1000-3000 coins)
    Works only in allowed group with 24hr cooldown
    """
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    # Check if command is used in allowed group
    if chat_id != ALLOWED_GROUP_ID:
        await show_join_buttons(update)
        return
    
    # Check membership
    is_member = await check_membership(update, context)
    if not is_member:
        await show_join_buttons(update)
        return
    
    # Check cooldown
    can_claim = await check_cooldown(user_id, "claim")
    if not can_claim:
        remaining_time = await get_cooldown_time(user_id, "claim")
        await update.message.reply_text(
            f"<b>⏰ {to_small_caps('COOLDOWN ACTIVE')}</b>\n\n"
            f"⏳ {to_small_caps(f'You can use /claim again in:')} <b>{remaining_time}</b>\n\n"
            f"💡 {to_small_caps('Come back later!')}",
            parse_mode="HTML"
        )
        return
    
    # Generate random coin amount (1000-3000)
    coin_amount = random.randint(1000, 3000)
    coin_code = generate_coin_code()
    
    # Ensure code is unique
    while await claim_codes_collection.find_one({"code": coin_code}):
        coin_code = generate_coin_code()
    
    # Store coin code in database
    await claim_codes_collection.insert_one({
        "code": coin_code,
        "user_id": user_id,
        "amount": coin_amount,
        "created_at": datetime.utcnow(),
        "is_redeemed": False
    })
    
    # Update user's last claim time
    await user_collection.update_one(
        {"id": user_id},
        {"$set": {"last_claim": datetime.utcnow()}},
        upsert=True
    )
    
    await update.message.reply_text(
        f"<b>💰 {to_small_caps('COIN CODE GENERATED!')}</b>\n\n"
        f"🎟️ <b>{to_small_caps('Your Code:')}</b> <code>{coin_code}</code>\n"
        f"💎 <b>{to_small_caps('Amount:')}</b> {coin_amount:,} {to_small_caps('coins')}\n\n"
        f"📌 {to_small_caps('Use')} <code>/redeem {coin_code}</code> {to_small_caps('to claim your coins!')}\n"
        f"⏰ {to_small_caps('Valid for 24 hours')}",
        parse_mode="HTML"
    )
    
    LOGGER.info(f"User {user_id} generated coin code {coin_code} for {coin_amount} coins")


async def redeem_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /redeem <code> - Redeem a coin code
    """
    user_id = update.effective_user.id
    
    # Validate arguments
    if len(context.args) < 1:
        usage_msg = (
            f"<b>🎁 {to_small_caps('REDEEM CODE')}</b>\n\n"
            f"📝 {to_small_caps('Usage:')} <code>/redeem &lt;CODE&gt;</code>\n\n"
            f"💡 {to_small_caps('Redeem your coin codes to add coins to your balance!')}"
        )
        await update.message.reply_text(usage_msg, parse_mode="HTML")
        return
    
    code = context.args[0].upper()
    
    # Find the coin code
    code_doc = await claim_codes_collection.find_one({
        "code": code,
        "user_id": user_id
    })
    
    if not code_doc:
        await update.message.reply_text(
            f"<b>❌ {to_small_caps('INVALID CODE')}</b>\n\n"
            f"⚠️ {to_small_caps('This code does not exist or does not belong to you.')}\n\n"
            f"💡 {to_small_caps('Use /claim to generate a new code!')}",
            parse_mode="HTML"
        )
        return
    
    # Check if already redeemed
    if code_doc.get("is_redeemed", False):
        await update.message.reply_text(
            f"<b>❌ {to_small_caps('CODE ALREADY REDEEMED')}</b>\n\n"
            f"⚠️ {to_small_caps('This code has already been used.')}\n\n"
            f"💡 {to_small_caps('Use /claim to generate a new code!')}",
            parse_mode="HTML"
        )
        return
    
    # Check if code is expired (24 hours)
    created_at = code_doc.get("created_at")
    if created_at:
        time_diff = datetime.utcnow() - created_at
        if time_diff > timedelta(hours=24):
            await update.message.reply_text(
                f"<b>❌ {to_small_caps('CODE EXPIRED')}</b>\n\n"
                f"⚠️ {to_small_caps('This code has expired (24 hours limit).')}\n\n"
                f"💡 {to_small_caps('Use /claim to generate a new code!')}",
                parse_mode="HTML"
            )
            return
    
    coin_amount = code_doc.get("amount", 0)
    
    # Add coins to user's balance (user_collection_lmaoooo.balance)
    await user_collection.update_one(
        {"id": user_id},
        {
            "$inc": {"balance": coin_amount},
            "$set": {"last_redeem": datetime.utcnow()}
        },
        upsert=True
    )
    
    # Mark code as redeemed
    await claim_codes_collection.update_one(
        {"code": code},
        {"$set": {"is_redeemed": True, "redeemed_at": datetime.utcnow()}}
    )
    
    # Get updated balance
    updated_user = await user_collection.find_one({"id": user_id})
    new_balance = updated_user.get("balance", 0) if updated_user else coin_amount
    
    await update.message.reply_text(
        f"<b>✅ {to_small_caps('CODE REDEEMED SUCCESSFULLY!')}</b>\n\n"
        f"💰 <b>{to_small_caps('Coins Added:')}</b> {coin_amount:,}\n"
        f"💎 <b>{to_small_caps('New Balance:')}</b> {new_balance:,} {to_small_caps('coins')}\n\n"
        f"🎉 {to_small_caps('Enjoy your coins!')}",
        parse_mode="HTML"
    )
    
    LOGGER.info(f"User {user_id} redeemed code {code} for {coin_amount} coins")


# ---------- Handler Registration ----------
def register_handlers():
    """Register all claim system handlers with the application."""
    application.add_handler(CommandHandler("sclaim", sclaim_command, block=False))
    application.add_handler(CommandHandler("claim", claim_command, block=False))
    application.add_handler(CommandHandler("redeem", redeem_command, block=False))
    LOGGER.info("Claim system handlers registered successfully")


# Auto-register handlers when module is imported
register_handlers()
