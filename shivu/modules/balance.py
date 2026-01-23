from html import escape
from typing import Optional, Dict, Any

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from pymongo import ReturnDocument

from shivu import application, db, LOGGER, OWNER_ID, SUDO_USERS

# Minimal balance collection
user_balance_coll = db.get_collection("user_balance")  # documents: { user_id, balance, ... }

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
    await update.message.reply_text(f"💰 <b>{name}</b>'s Balance: <b>{bal:,}</b> coins", parse_mode="HTML")


async def pay_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /pay <user_id|@username|reply> <amount>
    Transfer coins to another user (atomic conditional decrement).
    """
    if not context.args and not update.message.reply_to_message:
        await update.message.reply_text("Usage: /pay <user_id|@username> <amount>  (or reply with /pay <amount>)")
        return

    sender = update.effective_user

    # Resolve target and amount
    target_id: Optional[int] = None
    amount_str: Optional[str] = None

    if update.message.reply_to_message and len(context.args) == 1:
        # /pay <amount> as a reply
        target_id = update.message.reply_to_message.from_user.id
        amount_str = context.args[0]
    else:
        if len(context.args) < 2:
            await update.message.reply_text("Usage: /pay <user_id|@username|reply> <amount>")
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
        await update.message.reply_text("Could not resolve target user. Use user id, @username or reply to their message.")
        return

    if target_id == sender.id:
        await update.message.reply_text("You cannot pay yourself.")
        return

    try:
        amount = int(amount_str)
    except Exception:
        await update.message.reply_text("Invalid amount. Use a positive integer.")
        return

    if amount <= 0:
        await update.message.reply_text("Amount must be greater than zero.")
        return

    success = await _atomic_transfer(sender.id, target_id, amount)
    if not success:
        await update.message.reply_text("❌ Transaction failed: insufficient funds or internal error.")
        return

    await update.message.reply_text(
        f"✅ Sent <b>{amount:,}</b> coins to <a href='tg://user?id={target_id}'>user</a>.",
        parse_mode="HTML"
    )


async def admin_addbal_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /addbal <user_id> <amount> - admin-only adjust balance
    Restricted to OWNER_ID and SUDO_USERS.
    """
    user_id = update.effective_user.id
    if user_id != OWNER_ID and user_id not in SUDO_USERS:
        await update.message.reply_text("Not authorized.")
        return

    if len(context.args) < 2:
        await update.message.reply_text("Usage: /addbal <user_id> <amount>")
        return

    try:
        target = int(context.args[0])
        amount = int(context.args[1])
    except ValueError:
        await update.message.reply_text("Invalid arguments.")
        return

    try:
        new_bal = await change_balance(target, amount)
        await update.message.reply_text(f"Updated balance for <a href='tg://user?id={target}'>user</a>: <b>{new_bal:,}</b>", parse_mode="HTML")
    except Exception:
        await update.message.reply_text("Failed to update balance.")


# Register handlers
application.add_handler(CommandHandler(["balance", "bal"], balance_cmd, block=False))
application.add_handler(CommandHandler("pay", pay_cmd, block=False))
application.add_handler(CommandHandler("addbal", admin_addbal_cmd, block=False))
