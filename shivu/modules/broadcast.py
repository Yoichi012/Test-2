import asyncio
import time
from datetime import timedelta
from typing import Set, Tuple
from telegram import Update
from telegram.error import Forbidden, BadRequest, RetryAfter, TelegramError
from telegram.ext import CallbackContext, CommandHandler
from shivu import application, top_global_groups_collection, pm_users, OWNER_ID


def create_progress_bar(percentage: float, width: int = 10) -> str:
    """Create a visual progress bar."""
    filled = int(width * percentage / 100)
    empty = width - filled
    return f"[{'█' * filled}{'░' * empty}] {percentage:.1f}%"


def format_time(seconds: float) -> str:
    """Format seconds into human-readable time."""
    delta = timedelta(seconds=int(seconds))
    parts = []
    
    if delta.days > 0:
        parts.append(f"{delta.days}d")
    
    hours = delta.seconds // 3600
    if hours > 0:
        parts.append(f"{hours}h")
    
    minutes = (delta.seconds % 3600) // 60
    if minutes > 0:
        parts.append(f"{minutes}m")
    
    secs = delta.seconds % 60
    if secs > 0 or not parts:
        parts.append(f"{secs}s")
    
    return " ".join(parts)


async def get_all_recipients() -> Tuple[Set[int], int]:
    """Fetch all unique recipients from both collections."""
    try:
        # Use async for to fetch all group IDs
        all_chats = set()
        async for doc in top_global_groups_collection.find({}, {"group_id": 1}):
            if "group_id" in doc:
                all_chats.add(doc["group_id"])
        
        # Use async for to fetch all user IDs
        all_users = set()
        async for doc in pm_users.find({}, {"_id": 1}):
            if "_id" in doc:
                all_users.add(doc["_id"])
        
        # Combine and deduplicate
        all_recipients = all_chats.union(all_users)
        return all_recipients, len(all_recipients)
        
    except Exception as e:
        raise Exception(f"Failed to fetch recipients: {str(e)}")


async def broadcast(update: Update, context: CallbackContext) -> None:
    """Premium broadcast command for owner only."""
    
    # Authorization check - CRUCIAL
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text(
            "⛔ **Access Denied**\n\n"
            "This command is restricted to the bot owner only."
        )
        return

    # Check if message is replied to
    message_to_broadcast = update.effective_message.reply_to_message
    if not message_to_broadcast:
        await update.message.reply_text(
            "📤 **How to use:**\n\n"
            "1. Reply to any message (text, photo, video, etc.)\n"
            "2. Type `/broadcast`\n\n"
            "The message will be sent to all users and groups without 'Forwarded' tag."
        )
        return

    # Acknowledge command
    processing_msg = await update.message.reply_text(
        "🔄 **Processing...**\n"
        "Fetching recipient list from database..."
    )

    # Get all recipients
    try:
        all_recipients, total_recipients = await get_all_recipients()
        
        if total_recipients == 0:
            await processing_msg.edit_text("❌ **No recipients found in database.**")
            return
            
        await processing_msg.edit_text(
            f"✅ **Found {total_recipients:,} recipients**\n"
            "Starting broadcast in 2 seconds..."
        )
        await asyncio.sleep(2)
        await processing_msg.delete()
        
    except Exception as e:
        await update.message.reply_text(f"❌ **Database Error:**\n{str(e)}")
        return

    # Send initial status message
    status_msg = await update.message.reply_text(
        f"🚀 **Broadcast Started**\n\n"
        f"📊 **Progress:** [░░░░░░░░░░] 0.0%\n"
        f"✅ **Sent:** 0/{total_recipients:,}\n"
        f"⛔ **Blocked:** 0\n"
        f"❌ **Failed:** 0\n"
        f"⏱️ **Elapsed:** 0s\n"
        f"⏳ **ETA:** Calculating..."
    )

    # Statistics tracking
    stats = {
        'sent': 0,
        'blocked': 0,
        'failed': 0,
        'start_time': time.time(),
        'last_update_time': time.time(),
        'last_update_count': 0,
        'current_index': 0
    }

    async def update_status():
        """Update the status message with current progress."""
        current_time = time.time()
        elapsed = current_time - stats['start_time']
        
        processed = stats['sent'] + stats['blocked'] + stats['failed']
        if processed > 0 and elapsed > 0:
            progress_percent = (processed / total_recipients) * 100
            
            # Calculate ETA
            items_per_second = processed / elapsed
            if items_per_second > 0:
                remaining = total_recipients - processed
                eta_seconds = remaining / items_per_second
                eta_str = format_time(eta_seconds)
            else:
                eta_str = "∞"
            
            # Create status text
            status_text = (
                f"🚀 **Broadcast in Progress**\n\n"
                f"📊 **Progress:** {create_progress_bar(progress_percent)}\n"
                f"✅ **Sent:** {stats['sent']:,}/{total_recipients:,}\n"
                f"⛔ **Blocked:** {stats['blocked']:,}\n"
                f"❌ **Failed:** {stats['failed']:,}\n"
                f"⏱️ **Elapsed:** {format_time(elapsed)}\n"
                f"⏳ **ETA:** {eta_str}"
            )
            
            try:
                await status_msg.edit_text(status_text)
                stats['last_update_time'] = current_time
                stats['last_update_count'] = processed
            except Exception as e:
                # Silently fail if we can't edit (message deleted, etc.)
                pass

    # Process each recipient
    recipients_list = list(all_recipients)
    
    for index, chat_id in enumerate(recipients_list, 1):
        stats['current_index'] = index
        
        # Update status every 20 recipients or 5 seconds
        if (index - stats['last_update_count'] >= 20 or 
            time.time() - stats['last_update_time'] >= 5):
            await update_status()
        
        # Try to send message with retry logic
        max_retries = 3
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                # Use copy_message instead of forward_message for clean appearance
                await context.bot.copy_message(
                    chat_id=chat_id,
                    from_chat_id=message_to_broadcast.chat_id,
                    message_id=message_to_broadcast.message_id
                )
                stats['sent'] += 1
                break  # Success, break out of retry loop
                
            except RetryAfter as e:
                # FloodWait - sleep and retry
                stats['failed'] += 1
                if retry_count < max_retries - 1:
                    wait_time = e.retry_after
                    await status_msg.edit_text(
                        f"⏳ **Rate Limited**\n"
                        f"Waiting {wait_time} seconds before continuing..."
                    )
                    await asyncio.sleep(wait_time)
                    retry_count += 1
                    stats['failed'] -= 1  # Don't count retry as failure yet
                    continue
                break
                
            except Forbidden:
                # User blocked the bot or bot was removed from group
                stats['blocked'] += 1
                break
                
            except BadRequest as e:
                # Deleted account or invalid chat ID
                if "chat not found" in str(e).lower() or "user not found" in str(e).lower():
                    stats['failed'] += 1
                else:
                    # Other BadRequest errors
                    stats['failed'] += 1
                break
                
            except TelegramError:
                # Other Telegram API errors
                stats['failed'] += 1
                break
                
            except Exception:
                # Any other unexpected errors
                stats['failed'] += 1
                break

        # Small delay to avoid hitting API limits
        if index % 10 == 0:
            await asyncio.sleep(0.1)

    # Final update and summary
    final_elapsed = time.time() - stats['start_time']
    
    success_rate = (stats['sent'] / total_recipients) * 100 if total_recipients > 0 else 0
    
    summary = (
        f"🎉 **Broadcast Complete!**\n\n"
        f"📊 **Summary**\n"
        f"├ Total Recipients: {total_recipients:,}\n"
        f"├ ✅ Successfully Sent: {stats['sent']:,} ({success_rate:.1f}%)\n"
        f"├ ⛔ Blocked/Removed: {stats['blocked']:,}\n"
        f"├ ❌ Failed: {stats['failed']:,}\n"
        f"├ ⏱️ Total Time: {format_time(final_elapsed)}\n"
        f"└ 🚀 Speed: {stats['sent']/final_elapsed:.1f} messages/sec\n\n"
        f"💡 **Tip:** Failed messages may be due to:\n"
        f"• Deleted accounts/chats\n• Network issues\n• Invalid chat IDs"
    )
    
    try:
        await status_msg.edit_text(summary)
    except:
        # If status message was deleted, send new one
        await update.message.reply_text(summary)


# Add handler to application
application.add_handler(CommandHandler("broadcast", broadcast, block=False))
