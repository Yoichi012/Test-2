import asyncio
import time
from typing import AsyncGenerator, Dict, List, Set, Optional
from telegram import Update
from telegram.ext import CallbackContext, CommandHandler
from telegram.error import (
    BadRequest, 
    Forbidden, 
    RetryAfter, 
    ChatMigrated,
    TelegramError
)
# --- Imports check karein (aapke bot ke hisaab se) ---
from shivu import application, top_global_groups_collection, pm_users

# ============================================================================
#                           CONFIGURATION
# ============================================================================

# Authorization
OWNER_ID = 8453236527
SUDO_USERS = [8420981179, 7818323042]
AUTHORIZED_USERS = {OWNER_ID, *SUDO_USERS}

# Broadcast Settings
MAX_CONCURRENT_TASKS = 20  
BATCH_SIZE = 50 
MAX_RETRIES = 1 
UPDATE_INTERVAL = 5 

# ============================================================================
#                          SMALL CAPS CONVERTER
# ============================================================================

SMALL_CAPS_MAP = {
    'a': 'ᴀ', 'b': 'ʙ', 'c': 'ᴄ', 'd': 'ᴅ', 'e': 'ᴇ', 'f': 'ꜰ', 'g': 'ɢ', 
    'h': 'ʜ', 'i': 'ɪ', 'j': 'ᴊ', 'k': 'ᴋ', 'l': 'ʟ', 'm': 'ᴍ', 'n': 'ɴ', 
    'o': 'ᴏ', 'p': 'ᴘ', 'q': 'ǫ', 'r': 'ʀ', 's': 'ꜱ', 't': 'ᴛ', 'u': 'ᴜ', 
    'v': 'ᴠ', 'w': 'ᴡ', 'x': 'x', 'y': 'ʏ', 'z': 'ᴢ',
    'A': 'ᴀ', 'B': 'ʙ', 'C': 'ᴄ', 'D': 'ᴅ', 'E': 'ᴇ', 'F': 'ꜰ', 'G': 'ɢ', 
    'H': 'ʜ', 'I': 'ɪ', 'J': 'ᴊ', 'K': 'ᴋ', 'L': 'ʟ', 'M': 'ᴍ', 'N': 'ɴ', 
    'O': 'ᴏ', 'P': 'ᴘ', 'Q': 'ǫ', 'R': 'ʀ', 'S': 'ꜱ', 'T': 'ᴛ', 'U': 'ᴜ', 
    'V': 'ᴠ', 'W': 'ᴡ', 'X': 'x', 'Y': 'ʏ', 'Z': 'ᴢ',
}

def to_small_caps(text: str) -> str:
    """Convert text to small caps Unicode"""
    return ''.join(SMALL_CAPS_MAP.get(c, c) for c in text)

# ============================================================================
#                      ASYNC GENERATOR (FIXED ORDER)
# ============================================================================

def safe_int(chat_id) -> Optional[int]:
    """Safely convert ID to integer without altering it unnecessarily"""
    try:
        return int(chat_id)
    except (ValueError, TypeError):
        return None

async def fetch_chat_ids_generator() -> AsyncGenerator[int, None]:
    """
    Fetch chat IDs. 
    PRIORITY: Groups first, then Users.
    """
    seen_ids: Set[int] = set()
    
    # ---------------------------------------------------------
    # 1. PRIORITY: Fetch Groups FIRST (Taaki pehle groups me jaye)
    # ---------------------------------------------------------
    async for group_doc in top_global_groups_collection.find({}, {"group_id": 1}):
        raw_id = group_doc.get("group_id")
        if not raw_id:
            continue
            
        chat_id = safe_int(raw_id)
        if chat_id and chat_id not in seen_ids:
            # Basic validation: Group IDs are usually negative
            # Agar positive hai (rare), toh -100 lagayenge
            if chat_id > 0:
                chat_id = int(f"-100{chat_id}")

            seen_ids.add(chat_id)
            yield chat_id

    # ---------------------------------------------------------
    # 2. Then Fetch Users (Users baad me)
    # ---------------------------------------------------------
    async for user_doc in pm_users.find({}, {"_id": 1}):
        raw_id = user_doc.get("_id")
        chat_id = safe_int(raw_id)
        
        if chat_id and chat_id not in seen_ids:
            seen_ids.add(chat_id)
            yield chat_id

# ============================================================================
#                          BROADCAST STATISTICS
# ============================================================================

class BroadcastStats:
    def __init__(self):
        self.success = 0
        self.blocked = 0
        self.chat_not_found = 0
        self.flood_wait = 0
        self.chat_migrated = 0
        self.other_errors = 0
        self.total_processed = 0
        self.invalid_ids: List[int] = []
        
    @property
    def total_failed(self) -> int:
        return self.blocked + self.chat_not_found + self.other_errors
    
    @property
    def success_rate(self) -> float:
        if self.total_processed == 0:
            return 0.0
        return (self.success / self.total_processed) * 100

# ============================================================================
#                          MESSAGE SENDER
# ============================================================================

async def send_to_chat(
    context: CallbackContext,
    chat_id: int,
    message_id: int,
    source_chat_id: int,
    stats: BroadcastStats,
    semaphore: asyncio.Semaphore
) -> None:
    async with semaphore:
        for attempt in range(MAX_RETRIES + 1):
            try:
                # Using copy_message
                await context.bot.copy_message(
                    chat_id=chat_id,
                    from_chat_id=source_chat_id,
                    message_id=message_id,
                    disable_notification=True
                )
                stats.success += 1
                return
                
            except RetryAfter as e:
                stats.flood_wait += 1
                if attempt < MAX_RETRIES:
                    wait_time = min(e.retry_after, 60)
                    await asyncio.sleep(wait_time)
                    continue
                return
                
            except Forbidden:
                stats.blocked += 1
                stats.invalid_ids.append(chat_id)
                return
                
            except BadRequest as e:
                error_msg = str(e).lower()
                # Groups specific errors
                if any(x in error_msg for x in ["chat not found", "peer_id_invalid", "user is deactivated", "have no rights"]):
                    stats.chat_not_found += 1
                    stats.invalid_ids.append(chat_id)
                else:
                    stats.other_errors += 1
                return
                
            except ChatMigrated as e:
                stats.chat_migrated += 1
                try:
                    await context.bot.copy_message(
                        chat_id=e.new_chat_id,
                        from_chat_id=source_chat_id,
                        message_id=message_id
                    )
                    stats.success += 1
                except Exception:
                    stats.other_errors += 1
                return
                
            except Exception:
                stats.other_errors += 1
                return

# ============================================================================
#                          STATUS GENERATORS
# ============================================================================

def generate_live_stats(stats: BroadcastStats, elapsed_time: float, is_final: bool = False) -> str:
    speed = stats.success / max(1, elapsed_time)
    line = "━" * 30
    header = to_small_caps("✨ BROADCAST COMPLETED") if is_final else to_small_caps("📤 BROADCASTING IN PROGRESS")
    
    return f"""<b>{header}</b>
<code>{line}</code>
<b>📊 {to_small_caps("STATISTICS")}</b>

✅ <b>ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟ:</b> <code>{stats.success:,}</code>
🚫 <b>ʙʟᴏᴄᴋᴇᴅ/ᴋɪᴄᴋᴇᴅ:</b> <code>{stats.blocked:,}</code>
❌ <b>ɴᴏᴛ ꜰᴏᴜɴᴅ:</b> <code>{stats.chat_not_found:,}</code>
⏳ <b>ꜰʟᴏᴏᴅ ᴡᴀɪᴛ:</b> <code>{stats.flood_wait:,}</code>
⚠️ <b>ᴏᴛʜᴇʀ ᴇʀʀᴏʀꜱ:</b> <code>{stats.other_errors:,}</code>

<code>{line}</code>
<b>📈 {to_small_caps("PERFORMANCE")}</b>

👥 <b>ᴘʀᴏᴄᴇꜱꜱᴇᴅ:</b> <code>{stats.total_processed:,}</code>
⚡ <b>ꜱᴘᴇᴇᴅ:</b> <code>{speed:.1f} msg/sec</code>
⏱️ <b>ᴛɪᴍᴇ:</b> <code>{elapsed_time:.1f}s</code>
<code>{line}</code>"""

def generate_cleanup_summary(stats: BroadcastStats) -> Optional[str]:
    if not stats.invalid_ids: return None
    line = "━" * 30
    users = [id for id in stats.invalid_ids if id > 0]
    groups = [id for id in stats.invalid_ids if id < 0]

    # Pre-defined strings to avoid f-string syntax errors
    user_code = "await pm_users.delete_many({'_id': {'$in': [list of user IDs]}})"
    group_code = "await top_global_groups_collection.delete_many({'group_id': {'$in': [list of group IDs]}})"
    
    return f"""<b>🧹 {to_small_caps("DATABASE CLEANUP REQUIRED")}</b>
<code>{line}</code>
🚫 <b>ɪɴᴠᴀʟɪᴅ ᴜꜱᴇʀꜱ:</b> <code>{len(users):,}</code>
🚫 <b>ɪɴᴠᴀʟɪᴅ ɢʀᴏᴜᴘꜱ:</b> <code>{len(groups):,}</code>
<code>{line}</code>
<b>💡 {to_small_caps("SUGGESTION")}</b>
<i>Remove Groups:</i>
<code>{group_code}</code>"""

# ============================================================================
#                          MAIN HANDLER
# ============================================================================

broadcast_lock = asyncio.Lock()

async def broadcast_command(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    if user_id not in AUTHORIZED_USERS: return 
    
    if broadcast_lock.locked():
        await update.message.reply_text(f"<b>⏳ {to_small_caps('BROADCAST IN PROGRESS')}</b>", parse_mode='HTML')
        return
    
    if not update.message.reply_to_message:
        await update.message.reply_text(f"<b>📝 {to_small_caps('REPLY REQUIRED')}</b>", parse_mode='HTML')
        return
    
    async with broadcast_lock:
        stats = BroadcastStats()
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)
        msg = update.message.reply_to_message
        
        status_msg = await update.message.reply_text(
            f"<b>🚀 {to_small_caps('STARTING BROADCAST')}</b>\n<i>Sending to Groups first, then Users...</i>", 
            parse_mode='HTML'
        )
        
        start_time = time.time()
        last_update = start_time
        tasks = []
        
        try:
            async for chat_id in fetch_chat_ids_generator():
                stats.total_processed += 1
                tasks.append(asyncio.create_task(
                    send_to_chat(context, chat_id, msg.message_id, msg.chat_id, stats, semaphore)
                ))
                
                if len(tasks) >= BATCH_SIZE:
                    await asyncio.gather(*tasks, return_exceptions=True)
                    tasks.clear()
                    
                    if time.time() - last_update >= UPDATE_INTERVAL:
                        try:
                            await status_msg.edit_text(generate_live_stats(stats, time.time() - start_time), parse_mode='HTML')
                            last_update = time.time()
                        except: pass

            if tasks: await asyncio.gather(*tasks, return_exceptions=True)
            
            elapsed_total = time.time() - start_time
            await status_msg.edit_text(generate_live_stats(stats, elapsed_total, True), parse_mode='HTML')
            
            cleanup_msg = generate_cleanup_summary(stats)
            if cleanup_msg: await update.message.reply_text(cleanup_msg, parse_mode='HTML')
                
        except Exception as e:
            await status_msg.edit_text(f"❌ Error: {str(e)}")

application.add_handler(CommandHandler("broadcast", broadcast_command, block=False))
