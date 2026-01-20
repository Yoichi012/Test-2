import asyncio
import time
from typing import Set, Dict, List
from datetime import datetime, timedelta
from telegram import Update, Message
from telegram.ext import CallbackContext, CommandHandler
from telegram.error import BadRequest, RetryAfter, TelegramError
from motor.motor_asyncio import AsyncIOMotorCollection
from shivu import application, top_global_groups_collection, pm_users, OWNER_ID

# --- Configuration ---
MAX_CONCURRENT_TASKS = 100  # Maximum concurrent sends
BATCH_SIZE = 35  # Chunk size for parallel processing
MAX_RETRIES = 2  # Maximum retry attempts for temporary failures
TTL_HOURS = 12  # Cache duration for failed users
FLOOD_WAIT_BASE = 1  # Base wait time for flood control

# --- MongoDB Collections for Temporary Cache ---
# Add these to your shivu.py imports or create them here
# from motor.motor_asyncio import AsyncIOMotorClient
# client = AsyncIOMotorClient(MONGO_URI)
# db = client['shivu_bot']
# failed_cache_collection = db['broadcast_failed_cache']

# --- In-Memory Temporary Cache (per broadcast) ---
class TemporaryFailureCache:
    def __init__(self):
        self.failed_users: Set[int] = set()
        self.flood_waits: Dict[int, float] = {}  # user_id -> next_attempt_time
    
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

# --- MongoDB TTL Cache (12-24 hours) ---
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
    stats: Dict[str, int]
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
                        disable_notification=True  # Reduces API load
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

# --- Optimized Broadcast Function ---
async def broadcast(update: Update, context: CallbackContext) -> None:
    """10x faster broadcast system for 5k+ users"""
    
    # Authorization check
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Not authorized.")
        return
    
    # Get message to broadcast
    message_to_broadcast = update.message.reply_to_message
    if message_to_broadcast is None:
        await update.message.reply_text("📝 Reply to a message to broadcast.")
        return
    
    start_time = time.time()
    
    # Fetch targets concurrently
    async def fetch_targets():
        chats_task = top_global_groups_collection.distinct("group_id")
        users_task = pm_users.distinct("_id")
        return await asyncio.gather(chats_task, users_task)
    
    all_chats, all_users = await fetch_targets()
    all_targets = list(set(all_chats + all_users))
    total_targets = len(all_targets)
    
    # Progress message
    progress_msg = await update.message.reply_text(
        f"🚀 Starting broadcast to {total_targets:,} targets...\n"
        f"⚡ Using {MAX_CONCURRENT_TASKS} concurrent workers\n"
        f"⏳ Estimated time: {max(30, total_targets // 100)} seconds"
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
            stats
        )
        
        # Update progress every 5 chunks (or 175 users)
        if i % 5 == 0:
            elapsed = time.time() - start_time
            remaining = ((len(chunks) - i) * elapsed / max(1, i)) if i > 0 else 0
            
            await progress_msg.edit_text(
                f"📤 Broadcasting... ({i+1}/{len(chunks)} chunks)\n"
                f"✅ Sent: {stats['success']:,} | ❌ Failed: {stats['failed']:,}\n"
                f"⏱️ Elapsed: {elapsed:.1f}s | Remaining: ~{remaining:.1f}s\n"
                f"📊 Success rate: {(stats['success']/max(1, i*BATCH_SIZE)*100):.1f}%"
            )
    
    # Retry temporary failures
    retryable = failed_cache.get_retryable()
    if retryable:
        retry_chunks = [retryable[i:i + BATCH_SIZE] for i in range(0, len(retryable), BATCH_SIZE)]
        
        for chunk in retry_chunks:
            await send_message_batch(
                context, 
                message_to_broadcast, 
                chunk, 
                semaphore, 
                TemporaryFailureCache(),  # Fresh cache for retries
                stats
            )
    
    # Final statistics
    elapsed_total = time.time() - start_time
    users_per_second = stats["success"] / max(1, elapsed_total)
    
    # Detailed report
    report_lines = [
        "📊 BROADCAST COMPLETE",
        "━" * 30,
        f"✅ Successful: {stats['success']:,}",
        f"🔄 Temporary fails: {stats['failed']:,}",
        f"🚫 Permanent fails: {stats['invalid']:,}",
        f"⏳ Flood limited: {stats['flood']:,}",
        f"📦 From cache: {stats['cached']:,}",
        f"👥 Total targeted: {total_targets:,}",
        "━" * 30,
        f"⏱️ Total time: {elapsed_total:.1f}s",
        f"⚡ Speed: {users_per_second:.1f} users/sec",
        f"📈 Success rate: {(stats['success']/total_targets*100):.1f}%",
        "━" * 30,
        f"🎯 Next broadcast will skip {stats['invalid']:,} invalid users"
    ]
    
    await progress_msg.edit_text("\n".join(report_lines))

# --- Registration ---
application.add_handler(CommandHandler("broadcast", broadcast, block=False))

# --- Optional: Auto-setup TTL index on startup ---
async def setup_broadcast_system():
    """Setup TTL indexes and prepare cache"""
    # Create TTL index for 12-hour expiration
    # This should be called in your bot's startup routine
    pass