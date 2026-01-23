import time
import uuid
import re
from html import escape
from typing import Optional, Dict, Any

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, User, Chat
from telegram.ext import CommandHandler, CallbackQueryHandler, ContextTypes

from pymongo import ReturnDocument

from shivu import application, db, LOGGER, OWNER_ID, SUDO_USERS

# ---------- Premium Styling Helpers ----------

# Small Caps Unicode Mapping (preserving HTML tags)
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
    '+': '+', '*': '*', '/': '/', '\\': '\\', '|': '|', '_': '_', '"': '"',
    "'": "'", '`': '`', '~': '~', '@': '@', '#': '#', '$': '$', '%': '%',
    '^': '^', '&': '&', ';': ';', '<': '<', '>': '>', '0': '0', '1': '1',
    '2': '2', '3': '3', '4': '4', '5': '5', '6': '6', '7': '7', '8': '8',
    '9': '9'
}

def safe_small_caps(text: str) -> str:
    """Convert text to small caps Unicode characters while preserving HTML tags."""
    # First, protect HTML tags by replacing them with placeholders
    html_pattern = r'(<[^>]+>)'
    html_tags = re.findall(html_pattern, text)
    
    # Replace HTML tags with placeholders
    for i, tag in enumerate(html_tags):
        text = text.replace(tag, f'__HTML_TAG_{i}__')
    
    # Convert remaining text to small caps
    result = ''.join(SMALL_CAPS_MAP.get(char, char) for char in text)
    
    # Restore HTML tags
    for i, tag in enumerate(html_tags):
        result = result.replace(f'__HTML_TAG_{i}__', tag)
    
    return result

# Premium Emoji Mapping
PREMIUM_EMOJIS = {
    # Standard emojis to premium replacements
    '💰': '💰',  # Money bag to diamond
    '💵': '💠',  # Dollar banknote to gem
    '💳': '⚜️',  # Credit card to fleur-de-lis
    '💸': '🪽',  # Money with wings to winged emoji
    '✅': '✓',  # Check mark to heavy check
    '❌': '✘',  # Cross mark to heavy multiplication
    '⚠️': '❗',   # Warning to exclamation
    '⏳': '⏱️',   # Hourglass to stopwatch
}

def premium_format(text: str) -> str:
    """Apply premium styling to text with emoji replacements and small caps for specific words."""
    # First replace emojis
    for key, value in PREMIUM_EMOJIS.items():
        text = text.replace(key, value)
    
    # Apply small caps to specific standalone words (not inside HTML)
    words_to_convert = ['Balance', 'Payment', 'Confirm', 'Cancel', 'Coins', 
                       'Transaction', 'Success', 'Failed', 'Error', 'Usage']
    
    # Process text line by line
    lines = text.split('\n')
    processed_lines = []
    
    for line in lines:
        # Skip lines that are mostly HTML tags
        if re.search(r'<[^>]+>.*<[^>]+>', line):
            # This line has HTML tags, process carefully
            parts = re.split(r'(<[^>]+>)', line)
            processed_parts = []
            
            for part in parts:
                if part.startswith('<') and part.endswith('>'):
                    # This is an HTML tag, keep as is
                    processed_parts.append(part)
                else:
                    # This is text, apply transformations
                    for word in words_to_convert:
                        part = re.sub(r'\b' + re.escape(word) + r'\b', safe_small_caps(word), part)
                    processed_parts.append(part)
            
            processed_lines.append(''.join(processed_parts))
        else:
            # Simple line without complex HTML
            for word in words_to_convert:
                line = re.sub(r'\b' + re.escape(word) + r'\b', safe_small_caps(word), line)
            processed_lines.append(line)
    
    return '\n'.join(processed_lines)

# Collections
user_balance_coll = db.get_collection("user_balance")

# In-memory pending payments and cooldowns
pending_payments: Dict[str, Dict[str, Any]] = {}
pay_cooldowns: Dict[int, float] = {}

# Configuration
PENDING_EXPIRY_SECONDS = 5 * 60
PAY_COOLDOWN_SECONDS = 60

# ---------- Enhanced Validation ----------
async def validate_payment_target(target_id: int, context: ContextTypes.DEFAULT_TYPE) -> tuple[bool, Optional[str]]:
    """Validate if target is a regular user (not bot, channel, or group)."""
    try:
        target_chat = await context.bot.get_chat(target_id)
        
        # Check if it's a bot
        if hasattr(target_chat, 'is_bot') and target_chat.is_bot:
            return False, "✘ ʏᴏᴜ ᴄᴀɴɴᴏᴛ ᴘᴀʏ ᴛᴏ ʙᴏᴛs ᴏʀ ᴄʜᴀɴɴᴇʟs."
        
        # Check if it's a channel or group
        if target_chat.type in ['channel', 'group', 'supergroup']:
            return False, "✘ ʏᴏᴜ ᴄᴀɴɴᴏᴛ ᴘᴀʏ ᴛᴏ ʙᴏᴛs ᴏʀ ᴄʜᴀɴɴᴇʟs."
        
        return True, None
    except Exception as e:
        LOGGER.error(f"Error validating payment target {target_id}: {e}")
        return False, "✘ ɪɴᴠᴀʟɪᴅ ᴛᴀʀɢᴇᴛ ᴜꜱᴇʀ."

# ---------- Helpers ----------
async def _ensure_balance_doc(user_id: int) -> Dict[str, Any]:
    """Ensure a balance document exists for the user and return it."""
    try:
        await user_balance_coll.update_one(
            {"user_id": user_id},
            {"$setOnInsert": {"user_id": user_id, "balance": 0}},
            upsert=True,
        )
        doc = await user_balance_coll.find_one({"user_id": user_id})
        return doc or {"user_id": user_id, "balance": 0}
    except Exception:
        LOGGER.exception("Error ensuring balance doc for %s", user_id)
        return {"user_id": user_id, "balance": 0}

async def get_balance(user_id: int) -> int:
    """Return integer balance for a user."""
    doc = await _ensure_balance_doc(user_id)
    return int(doc.get("balance", 0))

async def change_balance(user_id: int, amount: int) -> int:
    """Atomically change balance by `amount`. Returns the new balance after change."""
    if amount == 0:
        return await get_balance(user_id)

    try:
        await user_balance_coll.update_one({"user_id": user_id}, {"$inc": {"balance": int(amount)}}, upsert=True)
        doc = await user_balance_coll.find_one({"user_id": user_id})
        return int(doc.get("balance", 0)) if doc else 0
    except Exception:
        LOGGER.exception("Failed to change balance for %s by %s", user_id, amount)
        raise

async def _atomic_transfer(sender_id: int, receiver_id: int, amount: int) -> bool:
    """Atomically transfer coins from sender -> receiver."""
    if amount <= 0:
        return False

    try:
        sender_after = await user_balance_coll.find_one_and_update(
            {"user_id": sender_id, "balance": {"$gte": amount}},
            {"$inc": {"balance": -amount}},
            return_document=ReturnDocument.AFTER,
        )
    except Exception:
        LOGGER.exception("Error decrementing balance for sender %s", sender_id)
        return False

    if sender_after is None:
        return False

    try:
        await user_balance_coll.update_one({"user_id": receiver_id}, {"$inc": {"balance": amount}}, upsert=True)
        return True
    except Exception:
        LOGGER.exception("Failed to increment receiver %s; attempting rollback to sender %s", receiver_id, sender_id)
        try:
            await user_balance_coll.update_one({"user_id": sender_id}, {"$inc": {"balance": amount}}, upsert=True)
        except Exception:
            LOGGER.exception("Rollback failed for sender %s after transfer failure", sender_id)
        return False

# ---------- Command handlers ----------
async def balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/balance [@username|id] or reply - Show balance."""
    target = update.effective_user
    if context.args:
        arg = context.args[0]
        if arg.isdigit():
            try:
                target = await context.bot.get_chat(int(arg))
            except Exception:
                target = update.effective_user
        elif arg.startswith("@"):
            try:
                target = await context.bot.get_chat(arg)
            except Exception:
                target = update.effective_user
    elif update.message and update.message.reply_to_message:
        target = update.message.reply_to_message.from_user

    user_id = getattr(target, "id", update.effective_user.id)
    bal = await get_balance(user_id)
    name = escape(getattr(target, "first_name", str(user_id)))
    
    # Fixed: Proper HTML structure with preserved tags
    message = f"💰 <b>{name}</b>'s {safe_small_caps('Balance')}: <b>{bal:,}</b> ᴄᴏɪɴs"
    await update.message.reply_text(message, parse_mode="HTML")

async def pay_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/pay <user_id|@username|reply> <amount> - Initiate payment."""
    if not context.args and not update.message.reply_to_message:
        usage_text = premium_format("Usage: /pay <amount>")
        await update.message.reply_text(usage_text)
        return

    sender = update.effective_user

    # Check cooldown
    now = time.time()
    next_allowed = pay_cooldowns.get(sender.id, 0)
    if now < next_allowed:
        remaining = int(next_allowed - now)
        await update.message.reply_text(premium_format(f"⏱️ ʏᴏᴜ ᴍᴜsᴛ ᴡᴀɪᴛ {remaining}s ʙᴇғᴏʀᴇ sᴛᴀʀᴛɪɴɢ ᴀɴᴏᴛʜᴇʀ ᴘᴀʏᴍᴇɴᴛ."))
        return

    # Resolve target and amount
    target_id: Optional[int] = None
    amount_str: Optional[str] = None

    if update.message.reply_to_message and len(context.args) == 1:
        target_id = update.message.reply_to_message.from_user.id
        amount_str = context.args[0]
    else:
        if len(context.args) < 2:
            await update.message.reply_text(premium_format("Usage: /pay <reply> <amount>"))
            return
        raw_target = context.args[0]
        amount_str = context.args[1]
        if raw_target.isdigit():
            target_id = int(raw_target)
        elif raw_target.startswith("@"):
            try:
                chat = await context.bot.get_chat(raw_target)
                target_id = chat.id
            except Exception:
                target_id = None

    if not target_id:
        await update.message.reply_text(premium_format("✘ ᴄᴏᴜʟᴅ ɴᴏᴛ ʀᴇsᴏʟᴠᴇ ᴛᴀʀɢᴇᴛ ᴜsᴇʀ. ᴜsᴇ ᴜsᴇʀ ɪᴅ, @ᴜsᴇʀɴᴀᴍᴇ ᴏʀ ʀᴇᴘʟʏ ᴛᴏ ᴛʜᴇɪʀ ᴍᴇssᴀɢᴇ."))
        return

    if target_id == sender.id:
        await update.message.reply_text(premium_format("✓ ʏᴏᴜ ᴄᴀɴɴᴏᴛ ᴘᴀʏ ʏᴏᴜʀsᴇʟғ."))
        return

    # Enhanced validation
    is_valid, error_msg = await validate_payment_target(target_id, context)
    if not is_valid:
        await update.message.reply_text(premium_format(error_msg))
        return

    # Parse amount
    try:
        amount = int(amount_str)
    except Exception:
        await update.message.reply_text(premium_format("✘ ɪɴᴠᴀʟɪᴅ ᴀᴍᴏᴜɴᴛ. ᴜsᴇ ᴀ ᴘᴏsɪᴛɪᴠᴇ ɪɴᴛᴇɢᴇʀ."))
        return

    if amount <= 0:
        await update.message.reply_text(premium_format("✘ ᴀᴍᴏᴜɴᴛ ᴍᴜsᴛ ʙᴇ ɢʀᴇᴀᴛᴇʀ ᴛʜᴀɴ ᴢᴇʀᴏ."))
        return

    # Check sender balance
    bal = await get_balance(sender.id)
    if bal < amount:
        await update.message.reply_text(premium_format(f"✘ ʏᴏᴜ ᴅᴏɴ'ᴛ ʜᴀᴠᴇ ᴇɴᴏᴜɢʜ ᴄᴏɪɴs. ʏᴏᴜʀ ʙᴀʟᴀɴᴄᴇ: {bal:,}"))
        return

    # Create pending payment
    token = uuid.uuid4().hex
    created_at = time.time()
    pending_payments[token] = {
        "sender_id": sender.id,
        "target_id": target_id,
        "amount": amount,
        "created_at": created_at,
        "chat_id": update.effective_chat.id,
    }

    # Fetch names
    try:
        target_chat = await context.bot.get_chat(target_id)
        target_name = escape(getattr(target_chat, "first_name", str(target_id)))
    except Exception:
        target_name = str(target_id)

    sender_name = escape(getattr(sender, "first_name", str(sender.id)))
    
    # Create message with proper HTML
    text = f"❗ <b>ᴘᴀʏᴍᴇɴᴛ ᴄᴏɴғɪʀᴍᴀᴛɪᴏɴ</b>\n\n" \
           f"sᴇɴᴅᴇʀ: <a href='tg://user?id={sender.id}'>{sender_name}</a>\n" \
           f"ʀᴇᴄɪᴘɪᴇɴᴛ: <a href='tg://user?id={target_id}'>{target_name}</a>\n" \
           f"ᴀᴍᴏᴜɴᴛ: <b>{amount:,}</b> ᴄᴏɪɴs\n\n" \
           f"ᴀʀᴇ ʏᴏᴜ sᴜʀᴇ ʏᴏᴜ ᴡᴀɴᴛ ᴛᴏ ᴘʀᴏᴄᴇᴇᴅ?"

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✓ ᴄᴏɴғɪʀᴍ", callback_data=f"pay_confirm:{token}"),
            InlineKeyboardButton("✘ ᴄᴀɴᴄᴇʟ", callback_data=f"pay_cancel:{token}")
        ]
    ])

    msg = await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)
    pending_payments[token]["message_id"] = msg.message_id

async def pay_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle callback queries for payment confirmation."""
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    if not data.startswith("pay_confirm:") and not data.startswith("pay_cancel:"):
        return

    action, token = data.split(":", 1)
    pending = pending_payments.get(token)
    if not pending:
        try:
            await query.edit_message_text(premium_format("✖️ ᴛʜɪs ᴘᴀʏᴍᴇɴᴛ ʀᴇǫᴜᴇsᴛ ʜᴀs ᴇxᴘɪʀᴇᴅ ᴏʀ ɪs ɪɴᴠᴀʟɪᴅ."))
        except Exception:
            pass
        return

    sender_id = pending["sender_id"]
    target_id = pending["target_id"]
    amount = pending["amount"]
    created_at = pending["created_at"]

    # Only sender can confirm/cancel
    user_who_clicked = query.from_user.id
    if user_who_clicked != sender_id:
        await query.answer("ᴏɴʟʏ ᴛʜᴇ ᴘᴀʏᴍᴇɴᴛ ɪɴɪᴛɪᴀᴛᴏʀ ᴄᴀɴ ᴄᴏɴғɪʀᴍ ᴏʀ ᴄᴀɴᴄᴇʟ ᴛʜɪs ᴘᴀʏᴍᴇɴᴛ.", show_alert=True)
        return

    # Check expiry
    if time.time() - created_at > PENDING_EXPIRY_SECONDS:
        try:
            await query.edit_message_text(premium_format("⏱️ ᴛʜɪs ᴘᴀʏᴍᴇɴᴛ ʀᴇǫᴜᴇsᴛ ʜᴀs ᴇxᴘɪʀᴇᴅ."))
        except Exception:
            pass
        pending_payments.pop(token, None)
        return

    if action == "pay_cancel":
        try:
            await query.edit_message_text(premium_format("✘ ᴘᴀʏᴍᴇɴᴛ ᴄᴀɴᴄᴇʟʟᴇᴅ ʙʏ sᴇɴᴅᴇʀ."))
        except Exception:
            pass
        pending_payments.pop(token, None)
        return

    # action == pay_confirm
    now = time.time()
    next_allowed = pay_cooldowns.get(sender_id, 0)
    if now < next_allowed:
        remaining = int(next_allowed - now)
        await query.edit_message_text(premium_format(f"⏱️ ʏᴏᴜ ᴍᴜsᴛ ᴡᴀɪᴛ {remaining}s ʙᴇғᴏʀᴇ ᴍᴀᴋɪɴɢ ᴀɴᴏᴛʜᴇʀ ᴘᴀʏᴍᴇɴᴛ."))
        pending_payments.pop(token, None)
        return

    # Perform atomic transfer
    success = await _atomic_transfer(sender_id, target_id, amount)
    if not success:
        try:
            await query.edit_message_text(premium_format("✘ ᴛʀᴀɴsᴀᴄᴛɪᴏɴ ғᴀɪʟᴇᴅ: ɪɴsᴜғғɪᴄɪᴇɴᴛ ғᴜɴᴅs ᴏʀ ɪɴᴛᴇʀɴᴀʟ ᴇʀʀᴏʀ."))
        except Exception:
            pass
        pending_payments.pop(token, None)
        return

    # Success: set cooldown
    pay_cooldowns[sender_id] = time.time() + PAY_COOLDOWN_SECONDS

    # Edit message to show success
    try:
        sender_name = escape(getattr(query.from_user, "first_name", str(sender_id)))
        target_chat = await context.bot.get_chat(target_id)
        target_name = escape(getattr(target_chat, "first_name", str(target_id)))
        confirmed_text = f"✓ <b>ᴘᴀʏᴍᴇɴᴛ sᴜᴄᴄᴇssғᴜʟ</b>\n\n" \
                         f"sᴇɴᴅᴇʀ: <a href='tg://user?id={sender_id}'>{sender_name}</a>\n" \
                         f"ʀᴇᴄɪᴘɪᴇɴᴛ: <a href='tg://user?id={target_id}'>{target_name}</a>\n" \
                         f"ᴀᴍᴏᴜɴᴛ: <b>{amount:,}</b> ᴄᴏɪɴs\n\n" \
                         f"ɴᴇxᴛ ᴘᴀʏᴍᴇɴᴛ ᴀʟʟᴏᴡᴇᴅ ᴀғᴛᴇʀ {PAY_COOLDOWN_SECONDS} sᴇᴄᴏɴᴅs."
        await query.edit_message_text(confirmed_text, parse_mode="HTML")
    except Exception:
        pass

    pending_payments.pop(token, None)

async def admin_addbal_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/addbal <user_id> <amount> - admin-only adjust balance."""
    user_id = update.effective_user.id
    if user_id != OWNER_ID and user_id not in SUDO_USERS:
        await update.message.reply_text(premium_format("✘ ɴᴏᴛ ᴀᴜᴛʜᴏʀɪᴢᴇᴅ."))
        return

    if len(context.args) < 2:
        await update.message.reply_text(premium_format("Usage: /addbal <user_id> <amount>"))
        return

    try:
        target = int(context.args[0])
        amount = int(context.args[1])
    except ValueError:
        await update.message.reply_text(premium_format("✘ ɪɴᴠᴀʟɪᴅ ᴀʀɢᴜᴍᴇɴᴛs."))
        return

    try:
        new_bal = await change_balance(target, amount)
        message = f"✓ ᴜᴘᴅᴀᴛᴇᴅ ʙᴀʟᴀɴᴄᴇ ғᴏʀ <a href='tg://user?id={target}'>ᴜsᴇʀ</a>: <b>{new_bal:,}</b>"
        await update.message.reply_text(message, parse_mode="HTML")
    except Exception:
        await update.message.reply_text(premium_format("✘ ғᴀɪʟᴇᴅ ᴛᴏ ᴜᴘᴅᴀᴛᴇ ʙᴀʟᴀɴᴄᴇ."))

# Register handlers
application.add_handler(CommandHandler(["balance", "bal"], balance_cmd, block=False))
application.add_handler(CommandHandler("pay", pay_cmd, block=False))
application.add_handler(CallbackQueryHandler(pay_callback, pattern=r"^pay_", block=False))
application.add_handler(CommandHandler("addbal", admin_addbal_cmd, block=False))