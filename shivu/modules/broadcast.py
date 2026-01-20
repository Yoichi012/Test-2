import asyncio
import time
from typing import Set, Dict, List, Tuple
from datetime import datetime, timedelta
from telegram import Update, Message
from telegram.ext import CallbackContext, CommandHandler
from telegram.error import BadRequest, RetryAfter, TelegramError
from motor.motor_asyncio import AsyncIOMotorCollection
from shivu import application, top_global_groups_collection, pm_users

# --- Configuration ---
# Replace these with your actual IDs
OWNER_ID = 8453236527  # Replace with your owner ID
SUDO_USERS = [8420981179, 7818323042]  # Replace with your sudo users

# Create authorized users list (Owner + Sudo Users)
AUTHORIZED_USERS = [OWNER_ID] + SUDO_USERS

# Broadcast settings
MAX_CONCURRENT_TASKS = 100  # Maximum parallel sends
BATCH_SIZE = 35  # Chunk size for processing
MAX_RETRIES = 2  # Retry attempts for temporary failures
TTL_HOURS = 12  # Cache duration for failed users
FLOOD_WAIT_BASE = 1  # Base wait time for flood control

# --- Small Caps Font Converter ---
SMALL_CAPS_MAP = {
    'a': 'ᴀ', 'b': 'ʙ', 'c': 'ᴄ', 'd': 'ᴅ', 'e': 'ᴇ',
    'f': 'ꜰ', 'g': 'ɢ', 'h': 'ʜ', 'i': 'ɪ', 'j': 'ᴊ',
    'k': 'ᴋ', 'l': 'ʟ', 'm': 'ᴍ', 'n': 'ɴ', 'o': 'ᴏ',
    'p': 'ᴘ', 'q': 'ǫ', 'r': 'ʀ', 's': 'ꜱ', 't': 'ᴛ',
    'u': 'ᴜ', 'v': 'ᴠ', 'w': 'ᴡ', 'x': 'x', 'y': 'ʏ',
    'z': 'ᴢ',
    'A': 'ᴀ', 'B': 'ʙ', 'C': 'ᴄ', 'D': 'ᴅ', 'E': 'ᴇ',
    'F': 'ꜰ', 'G': 'ɢ', 'H': 'ʜ', 'I': 'ɪ', 'J': 'ᴊ',
    'K': 'ᴋ', 'L': 'ʟ', 'M': 'ᴍ', 'N': 'ɴ', 'O': 'ᴏ',
    'P': 'ᴘ', 'Q': 'ǫ', 'R': 'ʀ', 'S': 'ꜱ', 'T': 'ᴛ',
    'U': 'ᴜ', 'V': 'ᴠ', 'W': 'ᴡ', 'X': 'x', 'Y': 'ʏ',
    'Z': 'ᴢ',
    ' ': ' ', '!': '!', '?': '?', '.': '.', ',': ',',
    ':': ':', ';': ';', '-': '-', '_': '_', '(': '(',
    ')': ')', '[': '[', ']': ']', '{': '{', '}': '}',
    '0': '0', '1': '1', '2': '2', '3': '3', '4': '4',
    '5': '5', '6': '6', '7': '7', '8': '8', '9': '9'
}

def to_small_caps(text: str) -> str:
    """Convert text to small caps Unicode font"""
    result = []
    for char in text:
        result.append(SMALL_CAPS_MAP.get(char, char))
    return ''.join(result)

# --- Style Constants (using Small Caps) ---
class Style:
    HEADER = to_small_caps("📢 BROADCAST SYSTEM")
    STATUS = to_small_caps("📊 BROADCAST STATUS")
    COMPLETE = to_small_caps("✨ BROADCAST COMPLETED")
    STARTING = to_small_caps("🚀 STARTING BROADCAST")
    IN_PROGRESS = to_small_caps("📤 BROADCASTING")
    LIVE_STATS = to_small_caps("📈 LIVE STATISTICS")
    LINE = "━" * 30

# --- Global Broadcast Lock ---
is_broadcasting = False
broadcast_lock = asyncio.Lock()

# --- Temporary Failure Cache (per broadcast) ---
class TemporaryFailureCache:
    def __init__(self):
        self.failed_users: Set[int] = set()
        self.flood_waits: Dict[int, float] = {}
    
    def add_failed(self, user_id: int, retry_after: float = 0):
        """Add user to temporary failure cache"""
        self.failed_users.add(user_id)
        if retry_after > 0:
            self.flood_waits[user_id] = time.time() + retry_after
    
    def should_retry(self, user_id: int) -> bool:
        """Check if user should be retried"""
        if user_id not in self.flood_waits:
            return True
        return time.time() >= self.flood_waits[user_id]
    
    def get_retryable(self) -> List[int]:
        """Get users ready for retry"""
        now = time.time()
        retryable = [
            uid for uid in self.failed_users 
            if uid not in self.flood_waits or now >= self.flood_waits[uid]
        ]
        return retryable

# --- MongoDB TTL Cache Setup (Optional) ---
async def setup_ttl_cache():
    """Setup MongoDB TTL index if not exists"""
    # This should be called once during bot startup
    # Implementation depends on your MongoDB setup
    pass

async def add_to_ttl_cache(user_id: int, ttl_hours: int = TTL_HOURS):
    """Add user to TTL cache"""
    # This is for future broadcasts
    # Implement based on your MongoDB setup
    pass

async def is_in_ttl_cache(user_id: int) -> bool:
    """Check if user is in TTL cache"""
    # Implement based on your MongoDB setup
    return False

# --- Optimized Message Sender ---
async def send_message_batch(
    context: CallbackContext,
    message: Message,
    chat_ids: List[int],
    semaphore: asyncio.Semaphore,
    failed_cache: TemporaryFailureCache,
    stats: Dict[str, int],
    invalid_chats: List[int]
) -> None:
    """Send message to a batch of users with optimal concurrency"""
    
    async def send_single(chat_id: int):
        """Send to single user with retry logic"""
        # Skip if in TTL cache
        if await is_in_ttl_cache(chat_id):
            stats["cached"] += 1
            return
        
        async with semaphore:
            for attempt in range(MAX_RETRIES):
                try:
                    # Use copy_message instead of forward_message (faster)
                    await context.bot.copy_message(
                        chat_id=chat_id,
                        from_chat_id=message.chat_id,
                        message_id=message.message_id,
                        disable_notification=True
                    )
                    stats["success"] += 1
                    return
                    
                except RetryAfter as e:
                    # Handle flood control
                    wait_time = e.retry_after + FLOOD_WAIT_BASE
                    if attempt < MAX_RETRIES - 1:
                        await asyncio.sleep(wait_time)
                        continue
                    else:
                        failed_cache.add_failed(chat_id, wait_time)
                        stats["flood"] += 1
                        return
                        
                except BadRequest as e:
                    # Permanent failures
                    error_msg = str(e).lower()
                    permanent_errors = [
                        "chat not found",
                        "bot was blocked",
                        "user is deactivated",
                        "peer_id_invalid",
                        "forbidden"
                    ]
                    
                    if any(err in error_msg for err in permanent_errors):
                        stats["invalid"] += 1
                        # Add to invalid_chats for cleanup
                        invalid_chats.append(chat_id)
                        # Add to TTL cache for future broadcasts
                        await add_to_ttl_cache(chat_id)
                    else:
                        failed_cache.add_failed(chat_id)
                        stats["failed"] += 1
                    return
                    
                except Exception as e:
                    # Other temporary errors
                    if attempt < MAX_RETRIES - 1:
                        await asyncio.sleep(0.5)
                        continue
                    else:
                        failed_cache.add_failed(chat_id)
                        stats["failed"] += 1
                        return
    
    # Create and execute tasks for this batch
    tasks = [send_single(chat_id) for chat_id in chat_ids]
    await asyncio.gather(*tasks, return_exceptions=True)

# --- Premium Styled Report Generator ---
def generate_premium_report(stats: Dict[str, int], total_targets: int, elapsed_time: float) -> str:
    """Generate a premium styled report with small caps and bold formatting"""
    
    users_per_second = stats["success"] / max(1, elapsed_time)
    success_rate = (stats["success"] / total_targets * 100) if total_targets > 0 else 0
    
    # Format numbers with commas for thousands
    def format_num(num: int) -> str:
        return f"{num:,}"
    
    report_lines = [
        f"<b>{Style.STATUS}</b>",
        f"<code>{Style.LINE}</code>",
        f"<b>✅ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟ:</b> <code>{format_num(stats['success'])}</code>",
        f"<b>🔄 ᴛᴇᴍᴘᴏʀᴀʀʏ ꜰᴀɪʟꜱ:</b> <code>{format_num(stats['failed'])}</code>",
        f"<b>🚫 ᴘᴇʀᴍᴀɴᴇɴᴛ ꜰᴀɪʟꜱ:</b> <code>{format_num(stats['invalid'])}</code>",
        f"<b>⏳ ꜰʟᴏᴏᴅ ʟɪᴍɪᴛᴇᴅ:</b> <code>{format_num(stats['flood'])}</code>",
        f"<b>📦 ꜰʀᴏᴍ ᴄᴀᴄʜᴇ:</b> <code>{format_num(stats['cached'])}</code>",
        f"<b>👥 ᴛᴏᴛᴀʟ ᴛᴀʀɢᴇᴛᴇᴅ:</b> <code>{format_num(total_targets)}</code>",
        f"<code>{Style.LINE}</code>",
        f"<b>⏱️ ᴛᴏᴛᴀʟ ᴛɪᴍᴇ:</b> <code>{elapsed_time:.1f}s</code>",
        f"<b>⚡ ꜱᴘᴇᴇᴅ:</b> <code>{users_per_second:.1f} users/sec</code>",
        f"<b>📈 ꜱᴜᴄᴄᴇꜱꜱ ʀᴀᴛᴇ:</b> <code>{success_rate:.1f}%</code>",
        f"<code>{Style.LINE}</code>",
        f"<b>{Style.COMPLETE}</b>"
    ]
    
    return "\n".join(report_lines)

# --- Live Statistics Generator ---
def generate_live_stats(
    stats: Dict[str, int], 
    current_chunk: int, 
    total_chunks: int, 
    elapsed_time: float
) -> str:
    """Generate live statistics for progress updates"""
    
    progress_percent = ((current_chunk + 1) / total_chunks * 100) if total_chunks > 0 else 0
    users_per_second = stats["success"] / max(1, elapsed_time)
    
    return (
        f"<b>{Style.IN_PROGRESS}</b>\n"
        f"<code>{Style.LINE}</code>\n"
        f"📊 <b>ᴘʀᴏɢʀᴇꜱꜱ:</b> <code>{current_chunk + 1}/{total_chunks} chunks</code>\n"
        f"📈 <b>ᴄᴏᴍᴘʟᴇᴛᴇᴅ:</b> <code>{progress_percent:.1f}%</code>\n"
        f"<code>{Style.LINE}</code>\n"
        f"<b>{Style.LIVE_STATS}</b>\n"
        f"✅ <b>ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟ:</b> <code>{stats['success']:,}</code>\n"
        f"🔄 <b>ᴛᴇᴍᴘ ᴇʀʀᴏʀꜱ:</b> <code>{stats['failed']:,}</code>\n"
        f"🚫 <b>ᴘᴇʀᴍ ᴇʀʀᴏʀꜱ:</b> <code>{stats['invalid']:,}</code>\n"
        f"⏳ <b>ꜰʟᴏᴏᴅᴇᴅ:</b> <code>{stats['flood']:,}</code>\n"
        f"📦 <b>ᴄᴀᴄʜᴇᴅ:</b> <code>{stats['cached']:,}</code>\n"
        f"<code>{Style.LINE}</code>\n"
        f"⚡ <b>ꜱᴘᴇᴇᴅ:</b> <code>{users_per_second:.1f} users/sec</code>\n"
        f"⏱️ <b>ᴇʟᴀᴘꜱᴇᴅ:</b> <code>{elapsed_time:.1f}s</code>"
    )

# --- Fixed Group ID Formatter ---
def format_group_id(group_id: int) -> int:
    """Format group ID to Telegram's supergroup format"""
    # If group_id is positive, convert to negative with -100 prefix
    if isinstance(group_id, int) and group_id > 0:
        # Convert to string, add -100 prefix, then back to int
        return int(f"-100{group_id}")
    return group_id

# --- Main Broadcast Function ---
async def broadcast(update: Update, context: CallbackContext) -> None:
    """Premium broadcast system with multi-user access control"""
    
    global is_broadcasting
    
    # Multi-User Authorization Check
    user_id = update.effective_user.id
    if user_id not in AUTHORIZED_USERS:
        await update.message.reply_text(
            f"<b>❌ {to_small_caps('ACCESS DENIED')}</b>\n"
            f"<i>You are not authorized to use this command.</i>",
            parse_mode='HTML'
        )
        return
    
    # Check for overlapping broadcasts
    async with broadcast_lock:
        if is_broadcasting:
            await update.message.reply_text(
                f"<b>⏳ {to_small_caps('BROADCAST IN PROGRESS')}</b>\n"
                f"<i>Please wait for the current broadcast to complete.</i>",
                parse_mode='HTML'
            )
            return
        
        # Set broadcast flag
        is_broadcasting = True
    
    # Initialize variables for cleanup
    invalid_chats = []  # List of chat_ids for cleanup
    status_msg = None
    
    try:
        # Get message to broadcast
        message_to_broadcast = update.message.reply_to_message
        if message_to_broadcast is None:
            await update.message.reply_text(
                f"<b>📝 {to_small_caps('REPLY REQUIRED')}</b>\n"
                f"<i>Please reply to a message to broadcast.</i>",
                parse_mode='HTML'
            )
            is_broadcasting = False
            return
        
        start_time = time.time()
        
        # Initial status message
        status_msg = await update.message.reply_text(
            f"<b>{Style.STARTING}</b>\n"
            f"<i>Preparing broadcast...</i>",
            parse_mode='HTML'
        )
        
        # --- FIXED: Fetch targets with group ID formatting ---
        async def fetch_targets() -> List[int]:
            """Fetch all targets with proper group ID formatting"""
            
            # Fetch groups and users concurrently
            chats_task = top_global_groups_collection.distinct("group_id")
            users_task = pm_users.distinct("_id")
            raw_chats, raw_users = await asyncio.gather(chats_task, users_task)
            
            # Format group IDs
            formatted_targets = []
            
            # Process groups with ID formatting
            for group_id in raw_chats:
                if group_id:  # Skip None or empty values
                    formatted_id = format_group_id(group_id)
                    formatted_targets.append(formatted_id)
            
            # Process users (no formatting needed)
            for user_id in raw_users:
                if user_id:  # Skip None or empty values
                    formatted_targets.append(user_id)
            
            # Remove duplicates
            unique_targets = list(set(formatted_targets))
            return unique_targets
        
        try:
            all_targets = await fetch_targets()
        except Exception as e:
            await status_msg.edit_text(
                f"<b>❌ {to_small_caps('DATABASE ERROR')}</b>\n"
                f"<i>Failed to fetch targets: {str(e)}</i>",
                parse_mode='HTML'
            )
            is_broadcasting = False
            return
        
        total_targets = len(all_targets)
        
        # Update status with target count
        await status_msg.edit_text(
            f"<b>{Style.STARTING}</b>\n"
            f"<i>Targets loaded: {total_targets:,} users & groups</i>\n"
            f"<code>{Style.LINE}</code>\n"
            f"⚡ <i>Starting broadcast with {MAX_CONCURRENT_TASKS} concurrent workers...</i>",
            parse_mode='HTML'
        )
        
        # Initialize stats and cache
        stats = {"success": 0, "failed": 0, "invalid": 0, "flood": 0, "cached": 0}
        failed_cache = TemporaryFailureCache()
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)
        
        # Process in optimized chunks
        chunks = [all_targets[i:i + BATCH_SIZE] for i in range(0, len(all_targets), BATCH_SIZE)]
        
        for i, chunk in enumerate(chunks):
            await send_message_batch(
                context, 
                message_to_broadcast, 
                chunk, 
                semaphore, 
                failed_cache, 
                stats,
                invalid_chats
            )
            
            # Update live stats every 10 chunks
            if i % 10 == 0 or i == len(chunks) - 1:
                elapsed = time.time() - start_time
                live_stats = generate_live_stats(stats, i, len(chunks), elapsed)
                await status_msg.edit_text(live_stats, parse_mode='HTML')
        
        # Retry temporary failures
        retryable = failed_cache.get_retryable()
        if retryable:
            retry_chunks = [retryable[i:i + BATCH_SIZE] for i in range(0, len(retryable), BATCH_SIZE)]
            
            await status_msg.edit_text(
                f"<b>🔄 {to_small_caps('RETRYING FAILED USERS')}</b>\n"
                f"<i>Retrying {len(retryable)} temporarily failed users...</i>",
                parse_mode='HTML'
            )
            
            for chunk in retry_chunks:
                await send_message_batch(
                    context, 
                    message_to_broadcast, 
                    chunk, 
                    semaphore, 
                    TemporaryFailureCache(),
                    stats,
                    invalid_chats
                )
        
        # Final statistics
        elapsed_total = time.time() - start_time
        
        # Generate premium report
        final_report = generate_premium_report(stats, total_targets, elapsed_total)
        
        # Send final report
        await status_msg.edit_text(final_report, parse_mode='HTML')
        
        # Also send a copy to the command issuer
        await update.message.reply_text(
            f"<b>🎯 {to_small_caps('BROADCAST SUMMARY')}</b>\n"
            f"<i>Initiated by: {update.effective_user.first_name}</i>\n"
            f"<code>{Style.LINE}</code>\n"
            f"✅ <b>Delivered to:</b> {stats['success']:,} users\n"
            f"⏱️ <b>Total time:</b> {elapsed_total:.1f}s\n"
            f"<code>{Style.LINE}</code>\n"
            f"<i>Broadcast completed successfully!</i>",
            parse_mode='HTML'
        )
        
    except Exception as e:
        # Handle any unexpected errors
        error_msg = f"<b>❌ {to_small_caps('BROADCAST ERROR')}</b>\n<code>Error: {str(e)}</code>"
        
        if status_msg:
            await status_msg.edit_text(error_msg, parse_mode='HTML')
        else:
            await update.message.reply_text(error_msg, parse_mode='HTML')
        
        # Log the error
        print(f"Broadcast error: {e}")
        
    finally:
        # Database Cleanup Suggestions
        if invalid_chats:
            # Group invalid chats by type
            invalid_groups = []
            invalid_users = []
            
            for chat_id in invalid_chats:
                if str(chat_id).startswith("-100"):
                    # This is a group
                    invalid_groups.append(chat_id)
                else:
                    # This is a user
                    invalid_users.append(chat_id)
            
            cleanup_suggestions = []
            
            if invalid_groups:
                # Suggest cleanup for groups
                group_ids_str = ", ".join(map(str, invalid_groups[:10]))
                if len(invalid_groups) > 10:
                    group_ids_str += f" ... and {len(invalid_groups) - 10} more"
                
                cleanup_suggestions.append(
                    f"<b>🗑️ {to_small_caps('INVALID GROUPS')} ({len(invalid_groups)})</b>\n"
                    f"<code>IDs: {group_ids_str}</code>\n"
                    f"<i>Remove with:</i>\n"
                    f"<code>for group_id in {invalid_groups[:5]}:</code>\n"
                    f"<code>    await top_global_groups_collection.delete_one({{'group_id': group_id}})</code>"
                )
            
            if invalid_users:
                # Suggest cleanup for users
                user_ids_str = ", ".join(map(str, invalid_users[:10]))
                if len(invalid_users) > 10:
                    user_ids_str += f" ... and {len(invalid_users) - 10} more"
                
                cleanup_suggestions.append(
                    f"<b>🗑️ {to_small_caps('INVALID USERS')} ({len(invalid_users)})</b>\n"
                    f"<code>IDs: {user_ids_str}</code>\n"
                    f"<i>Remove with:</i>\n"
                    f"<code>for user_id in {invalid_users[:5]}:</code>\n"
                    f"<code>    await pm_users.delete_one({{'_id': user_id}})</code>"
                )
            
            if cleanup_suggestions:
                cleanup_message = "\n\n".join(cleanup_suggestions)
                await update.message.reply_text(
                    f"<b>🧹 {to_small_caps('DATABASE CLEANUP SUGGESTIONS')}</b>\n"
                    f"{cleanup_message}",
                    parse_mode='HTML'
                )
        
        # Always reset the broadcast flag
        is_broadcasting = False

# --- Broadcast Command Registration ---
application.add_handler(CommandHandler("broadcast", broadcast, block=False))

# --- Optional: Auto-setup on startup ---
async def setup_broadcast_system():
    """Setup TTL indexes and prepare cache on bot startup"""
    try:
        await setup_ttl_cache()
        print("Broadcast system initialized successfully.")
    except Exception as e:
        print(f"Failed to initialize broadcast system: {e}")

# Call this during your bot's startup
# asyncio.create_task(setup_broadcast_system())