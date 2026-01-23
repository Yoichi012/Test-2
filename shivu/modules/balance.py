import time
import uuid
from html import escape
from typing import Optional, Dict, Any

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, CallbackQueryHandler, ContextTypes

from pymongo import ReturnDocument

from shivu import application, db, LOGGER, OWNER_ID, SUDO_USERS

# Collections
user_balance_coll = db.get_collection("user_balance")  # documents: { user_id, balance, ... }

# In-memory pending payments and cooldowns
# pending_payments[token] = {"sender_id": int, "target_id": int, "amount": int, "created_at": float, "message_id": int, "chat_id": int}
pending_payments: Dict[str, Dict[str, Any]] = {}
# cooldowns[sender_id] = timestamp_when_next_pay_allowed
pay_cooldowns: Dict[int, float] = {}

# Configuration
PENDING_EXPIRY_SECONDS = 5 * 60   # pending confirmation expires after 5 minutes
PAY_COOLDOWN_SECONDS = 60         # sender must wait 60 seconds after a confirmed payment


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
    """
    Atomically change balance by `amount` (positive or negative).
    Returns the new balance after change.
    """
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
    """
    Atomically transfer coins from sender -> receiver using conditional decrement.
    Returns True on success, False on insufficient funds or error.
    """
    if amount <= 0:
        return False

    try:
        # decrement sender only if they have enough balance
        sender_after = await user_balance_coll.find_one_and_update(
            {"user_id": sender_id, "balance": {"$gte": amount}},
            {"$inc": {"balance": -amount}},
            return_document=ReturnDocument.AFTER,
        )
    except Exception:
        LOGGER.exception("Error decrementing balance for sender %s", sender_id)
        return False

    if sender_after is None:
        # insufficient funds
        return False

    try:
        # increment receiver
        await user_balance_coll.update_one({"user_id": receiver_id}, {"$inc": {"balance": amount}}, upsert=True)
        return True
    except Exception:
        LOGGER.exception("Failed to increment receiver %s; attempting rollback to sender %s", receiver_id, sender_id)
        # rollback: attempt to refund sender
        try:
            await user_balance_coll.update_one({"user_id": sender_id}, {"$inc": {"balance": amount}}, upsert=True)
        except Exception:
            LOGGER.exception("Rollback failed for sender %s after transfer failure", sender_id)
        return False


# ---------- Command handlers ----------
async def balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /balance [@username|id] or reply
    Show balance for yourself or given user.
    """
    target = update.effective_user
    # Try to resolve argument or reply target
    if context.args:
        arg = context.args[0]
        if arg.isdigit():
            try:
                # fetch chat to get display name
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
    
    # Premium balance display
    progress_fill = min(int((bal % 10000) / 10000 * 10), 10)  # Visual progress indicator
    progress_bar = "[" + "■" * progress_fill + "□" * (10 - progress_fill) + "]"
    
    await update.message.reply_text(
        f"◆  ᴡᴀʟʟᴇᴛ  sᴛᴀᴛᴜs  ━━━━━━━━━━━━━━━━━\n\n"
        f"• ᴜsᴇʀ: <b>{name}</b>\n"
        f"• ʙᴀʟᴀɴᴄᴇ: <b>{bal:,} ᴄᴏɪɴs</b>\n"
        f"• ᴠᴀᴜʟᴛ: {progress_bar}",
        parse_mode="HTML"
    )


async def pay_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /pay <user_id|@username|reply> <amount>
    Initiate a payment — creates a pending confirmation with Confirm/Cancel buttons.
    """
    if not context.args and not update.message.reply_to_message:
        await update.message.reply_text(
            "◆ ᴛʀᴀɴsғᴇʀ ɢᴜɪᴅᴇ ━━━━━━━━━━━━━━━━━\n\n"
            "• ᴜsᴀɢᴇ: /pay <ᴜsᴇʀ_ɪᴅ|@ᴜsᴇʀɴᴀᴍᴇ> <ᴀᴍᴏᴜɴᴛ>\n"
            "• ᴀʟᴛᴇʀɴᴀᴛᴇ: ʀᴇᴘʟʏ ᴡɪᴛʜ /pay <ᴀᴍᴏᴜɴᴛ>"
        )
        return

    sender = update.effective_user

    # Check cooldown for sender
    now = time.time()
    next_allowed = pay_cooldowns.get(sender.id, 0)
    if now < next_allowed:
        remaining = int(next_allowed - now)
        cooldown_bar = "[" + "■" * (10 - min(remaining, 10)) + "□" * min(remaining, 10) + "]"
        await update.message.reply_text(
            f"◆ ᴄᴏᴏʟᴅᴏᴡɴ ᴀᴄᴛɪᴠᴇ ━━━━━━━━━━━━━━━━━\n\n"
            f"• ᴡᴀɪᴛ ᴛɪᴍᴇ: {remaining}s\n"
            f"• ʀᴇᴍᴀɪɴɪɴɢ: {cooldown_bar}\n\n"
            f"ɴᴇxᴛ ᴛʀᴀɴsᴀᴄᴛɪᴏɴ ᴀᴠᴀɪʟᴀʙʟᴇ sᴏᴏɴ..."
        )
        return

    # Resolve target and amount
    target_id: Optional[int] = None
    amount_str: Optional[str] = None

    if update.message.reply_to_message and len(context.args) == 1:
        # /pay <amount> as a reply
        target_id = update.message.reply_to_message.from_user.id
        amount_str = context.args[0]
    else:
        # /pay <target> <amount>
        if len(context.args) < 2:
            await update.message.reply_text(
                "◆ ɪɴᴠᴀʟɪᴅ sʏɴᴛᴀx ━━━━━━━━━━━━━━━━━\n\n"
                "• ᴜsᴀɢᴇ: /pay <ᴜsᴇʀ_ɪᴅ|@ᴜsᴇʀɴᴀᴍᴇ|ʀᴇᴘʟʏ> <ᴀᴍᴏᴜɴᴛ>"
            )
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
        await update.message.reply_text(
            "◆ ᴜsᴇʀ ɴᴏᴛ ғᴏᴜɴᴅ ━━━━━━━━━━━━━━━━━\n\n"
            "• ᴜsᴇ ᴠᴀʟɪᴅ ᴜsᴇʀ ɪᴅ\n"
            "• ᴏʀ @ᴜsᴇʀɴᴀᴍᴇ\n"
            "• ᴏʀ ʀᴇᴘʟʏ ᴛᴏ ᴛʜᴇɪʀ ᴍᴇssᴀɢᴇ"
        )
        return

    if target_id == sender.id:
        await update.message.reply_text(
            "◆ sᴇʟғ-ᴛʀᴀɴsғᴇʀ ɴᴏᴛ ᴀʟʟᴏᴡᴇᴅ ━━━━━━━━━━━━━━━━━\n\n"
            "• ᴛʀᴀɴsᴀᴄᴛɪᴏɴs ᴛᴏ sᴇʟғ ᴀʀᴇ ʀᴇsᴛʀɪᴄᴛᴇᴅ\n"
            "• ᴜsᴇ ᴀɴᴏᴛʜᴇʀ ᴡᴀʟʟᴇᴛ ᴀᴅᴅʀᴇss"
        )
        return

    # parse amount
    try:
        amount = int(amount_str)
    except Exception:
        await update.message.reply_text(
            "◆ ɪɴᴠᴀʟɪᴅ ᴀᴍᴏᴜɴᴛ ━━━━━━━━━━━━━━━━━\n\n"
            "• ᴜsᴇ ᴀ ᴘᴏsɪᴛɪᴠᴇ ɪɴᴛᴇɢᴇʀ ᴠᴀʟᴜᴇ\n"
            "• ᴅᴇᴄɪᴍᴀʟs ɴᴏᴛ sᴜᴘᴘᴏʀᴛᴇᴅ"
        )
        return

    if amount <= 0:
        await update.message.reply_text(
            "◆ ᴍɪɴɪᴍᴜᴍ ᴀᴍᴏᴜɴᴛ ━━━━━━━━━━━━━━━━━\n\n"
            "• ᴛʀᴀɴsғᴇʀ ᴀᴍᴏᴜɴᴛ ᴍᴜsᴛ ʙᴇ > 𝟶\n"
            "• ᴍɪɴɪᴍᴜᴍ: 𝟷 ᴄᴏɪɴ"
        )
        return

    # Check sender balance quickly (best-effort)
    bal = await get_balance(sender.id)
    if bal < amount:
        await update.message.reply_text(
            f"◆ ɪɴsᴜғғɪᴄɪᴇɴᴛ ғᴜɴᴅs ━━━━━━━━━━━━━━━━━\n\n"
            f"• ʏᴏᴜʀ ʙᴀʟᴀɴᴄᴇ: <b>{bal:,} ᴄᴏɪɴs</b>\n"
            f"• ʀᴇǫᴜɪʀᴇᴅ: <b>{amount:,} ᴄᴏɪɴs</b>\n"
            f"• ᴅᴇғɪᴄɪᴛ: <b>{amount - bal:,} ᴄᴏɪɴs</b>",
            parse_mode="HTML"
        )
        return

    # Create pending payment
    token = uuid.uuid4().hex
    created_at = time.time()
    # store pending
    pending_payments[token] = {
        "sender_id": sender.id,
        "target_id": target_id,
        "amount": amount,
        "created_at": created_at,
        "chat_id": update.effective_chat.id,
    }

    # fetch friendly target name
    try:
        target_chat = await context.bot.get_chat(target_id)
        target_name = escape(getattr(target_chat, "first_name", str(target_id)))
    except Exception:
        target_name = str(target_id)

    sender_name = escape(getattr(sender, "first_name", str(sender.id)))
    text = (
        f"◆ ᴘᴇɴᴅɪɴɢ ᴛʀᴀɴsᴀᴄᴛɪᴏɴ ━━━━━━━━━━━━━━━━━\n\n"
        f"• sᴇɴᴅᴇʀ: <a href='tg://user?id={sender.id}'>{sender_name}</a>\n"
        f"• ʀᴇᴄɪᴘɪᴇɴᴛ: <a href='tg://user?id={target_id}'>{target_name}</a>\n"
        f"• ᴀᴍᴏᴜɴᴛ: <b>{amount:,} ᴄᴏɪɴs</b>\n"
        f"• ɪᴅ: <code>{token[:8]}...{token[-4:]}</code>\n\n"
        f"ᴄᴏɴғɪʀᴍ ᴛʜɪs ᴛʀᴀɴsғᴇʀ?"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✔ ᴄᴏɴғɪʀᴍ", callback_data=f"pay_confirm:{token}"),
            InlineKeyboardButton("✘ ᴄᴀɴᴄᴇʟ", callback_data=f"pay_cancel:{token}")
        ]
    ])

    msg = await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)
    # store message id for later edits
    pending_payments[token]["message_id"] = msg.message_id


async def pay_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle callback queries for pay_confirm:{token} and pay_cancel:{token}
    """
    query = update.callback_query
    await query.answer()  # acknowledge

    data = query.data or ""
    # expected formats: pay_confirm:<token> or pay_cancel:<token>
    if not data.startswith("pay_confirm:") and not data.startswith("pay_cancel:"):
        return

    action, token = data.split(":", 1)
    pending = pending_payments.get(token)
    if not pending:
        try:
            await query.edit_message_text(
                "◆ ᴛʀᴀɴsᴀᴄᴛɪᴏɴ ɪɴᴠᴀʟɪᴅ ━━━━━━━━━━━━━━━━━\n\n"
                "• ᴇxᴘɪʀᴇᴅ ᴏʀ ɪɴᴠᴀʟɪᴅ ʀᴇǫᴜᴇsᴛ\n"
                "• ɪɴɪᴛɪᴀᴛᴇ ɴᴇᴡ ᴛʀᴀɴsғᴇʀ"
            )
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
        # show alert
        await query.answer("Only the payment initiator can confirm or cancel this payment.", show_alert=True)
        return

    # Check expiry
    if time.time() - created_at > PENDING_EXPIRY_SECONDS:
        # expired
        try:
            await query.edit_message_text(
                "◆ ᴛʀᴀɴsᴀᴄᴛɪᴏɴ ᴇxᴘɪʀᴇᴅ ━━━━━━━━━━━━━━━━━\n\n"
                "• 5-ᴍɪɴᴜᴛᴇ ᴠᴀʟɪᴅɪᴛʏ ᴇxᴘɪʀᴇᴅ\n"
                "• ɪɴɪᴛɪᴀᴛᴇ ɴᴇᴡ ᴛʀᴀɴsғᴇʀ"
            )
        except Exception:
            pass
        pending_payments.pop(token, None)
        return

    if action == "pay_cancel":
        try:
            await query.edit_message_text(
                "◆ ᴛʀᴀɴsᴀᴄᴛɪᴏɴ ᴄᴀɴᴄᴇʟʟᴇᴅ ━━━━━━━━━━━━━━━━━\n\n"
                "• ʀᴇǫᴜᴇsᴛ ᴛᴇʀᴍɪɴᴀᴛᴇᴅ ʙʏ sᴇɴᴅᴇʀ\n"
                "• ғᴜɴᴅs ʀᴇᴍᴀɪɴ ᴜɴᴄʜᴀɴɢᴇᴅ"
            )
        except Exception:
            pass
        pending_payments.pop(token, None)
        return

    # action == pay_confirm
    # final check for cooldown (in case another pay occurred meanwhile)
    now = time.time()
    next_allowed = pay_cooldowns.get(sender_id, 0)
    if now < next_allowed:
        remaining = int(next_allowed - now)
        cooldown_bar = "[" + "□" * min(remaining, 10) + "■" * (10 - min(remaining, 10)) + "]"
        await query.edit_message_text(
            f"◆ ᴄᴏᴏʟᴅᴏᴡɴ ᴀᴄᴛɪᴠᴇ ━━━━━━━━━━━━━━━━━\n\n"
            f"• ᴡᴀɪᴛ ᴛɪᴍᴇ: {remaining}s\n"
            f"• sᴛᴀᴛᴜs: {cooldown_bar}\n\n"
            f"ᴛʀʏ ᴀɢᴀɪɴ sʜᴏʀᴛʟʏ..."
        )
        pending_payments.pop(token, None)
        return

    # Perform atomic transfer
    success = await _atomic_transfer(sender_id, target_id, amount)
    if not success:
        # likely insufficient funds or error
        try:
            await query.edit_message_text(
                "◆ ᴛʀᴀɴsᴀᴄᴛɪᴏɴ ғᴀɪʟᴇᴅ ━━━━━━━━━━━━━━━━━\n\n"
                "• ɪɴsᴜғғɪᴄɪᴇɴᴛ ғᴜɴᴅs\n"
                "• ᴏʀ ɪɴᴛᴇʀɴᴀʟ ᴇʀʀᴏʀ\n\n"
                "ᴄʜᴇᴄᴋ ʏᴏᴜʀ ʙᴀʟᴀɴᴄᴇ"
            )
        except Exception:
            pass
        pending_payments.pop(token, None)
        return

    # Success: set cooldown for sender
    pay_cooldowns[sender_id] = time.time() + PAY_COOLDOWN_SECONDS

    # Edit original message to show confirmed
    try:
        sender_name = escape(getattr(query.from_user, "first_name", str(sender_id)))
        target_chat = await context.bot.get_chat(target_id)
        target_name = escape(getattr(target_chat, "first_name", str(target_id)))
        confirmed_text = (
            f"◆ ᴛʀᴀɴsᴀᴄᴛɪᴏɴ sᴜᴄᴄᴇssғᴜʟ ━━━━━━━━━━━━━━━━━\n\n"
            f"• sᴇɴᴅᴇʀ: <a href='tg://user?id={sender_id}'>{sender_name}</a>\n"
            f"• ʀᴇᴄɪᴘɪᴇɴᴛ: <a href='tg://user?id={target_id}'>{target_name}</a>\n"
            f"• ᴀᴍᴏᴜɴᴛ: <b>{amount:,} ᴄᴏɪɴs</b>\n"
            f"• sᴛᴀᴛᴜs: ᴠᴇʀɪғɪᴇᴅ\n"
            f"• ᴛxɪᴅ: <code>{token[:12]}...</code>\n\n"
            f"ɴᴇxᴛ ᴛʀᴀɴsғᴇʀ ɪɴ {PAY_COOLDOWN_SECONDS}s"
        )
        await query.edit_message_text(confirmed_text, parse_mode="HTML")
    except Exception:
        pass

    pending_payments.pop(token, None)


async def admin_addbal_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /addbal <user_id> <amount> - admin-only adjust balance
    Restricted to OWNER_ID and SUDO_USERS.
    """
    user_id = update.effective_user.id
    if user_id != OWNER_ID and user_id not in SUDO_USERS:
        await update.message.reply_text(
            "◆ ᴀᴄᴄᴇss ᴅᴇɴɪᴇᴅ ━━━━━━━━━━━━━━━━━\n\n"
            "• ɪɴsᴜғғɪᴄɪᴇɴᴛ ᴘʀɪᴠɪʟᴇɢᴇs\n"
            "• ᴀᴅᴍɪɴ ᴄᴏᴍᴍᴀɴᴅ ᴏɴʟʏ"
        )
        return

    if len(context.args) < 2:
        await update.message.reply_text(
            "◆ ᴀᴅᴍɪɴ: ᴀᴅᴊᴜsᴛ ʙᴀʟᴀɴᴄᴇ ━━━━━━━━━━━━━━━━━\n\n"
            "• ᴜsᴀɢᴇ: /addbal <ᴜsᴇʀ_ɪᴅ> <ᴀᴍᴏᴜɴᴛ>\n"
            "• ᴘᴏsɪᴛɪᴠᴇ/ɴᴇɢᴀᴛɪᴠᴇ ᴠᴀʟᴜᴇs ᴀʟʟᴏᴡᴇᴅ"
        )
        return

    try:
        target = int(context.args[0])
        amount = int(context.args[1])
    except ValueError:
        await update.message.reply_text(
            "◆ ɪɴᴠᴀʟɪᴅ ᴘᴀʀᴀᴍᴇᴛᴇʀs ━━━━━━━━━━━━━━━━━\n\n"
            "• ᴜsᴇʀ_ɪᴅ ᴍᴜsᴛ ʙᴇ ɪɴᴛᴇɢᴇʀ\n"
            "• ᴀᴍᴏᴜɴᴛ ᴍᴜsᴛ ʙᴇ ɪɴᴛᴇɢᴇʀ"
        )
        return

    try:
        new_bal = await change_balance(target, amount)
        await update.message.reply_text(
            f"◆ ʙᴀʟᴀɴᴄᴇ ᴜᴘᴅᴀᴛᴇᴅ ━━━━━━━━━━━━━━━━━\n\n"
            f"• ᴜsᴇʀ: <a href='tg://user?id={target}'>ᴡᴀʟʟᴇᴛ</a>\n"
            f"• ᴀᴅᴊᴜsᴛᴍᴇɴᴛ: {amount:+,} ᴄᴏɪɴs\n"
            f"• ɴᴇᴡ ʙᴀʟᴀɴᴄᴇ: <b>{new_bal:,} ᴄᴏɪɴs</b>",
            parse_mode="HTML"
        )
    except Exception:
        await update.message.reply_text(
            "◆ ᴜᴘᴅᴀᴛᴇ ғᴀɪʟᴇᴅ ━━━━━━━━━━━━━━━━━\n\n"
            "• ᴅᴀᴛᴀʙᴀsᴇ ᴇʀʀᴏʀ\n"
            "• ᴛʀʏ ᴀɢᴀɪɴ ʟᴀᴛᴇʀ"
        )


# Register handlers
application.add_handler(CommandHandler(["balance", "bal"], balance_cmd, block=False))
application.add_handler(CommandHandler("pay", pay_cmd, block=False))
application.add_handler(CallbackQueryHandler(pay_callback, pattern=r"^pay_", block=False))
application.add_handler(CommandHandler("addbal", admin_addbal_cmd, block=False))