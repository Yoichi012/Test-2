import importlib
import time
import random
import re
import asyncio
from html import escape 
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, CallbackContext, MessageHandler, filters

from shivu import collection, top_global_groups_collection, group_user_totals_collection, user_collection, user_totals_collection, shivuu
from shivu import application, SUPPORT_CHAT, UPDATE_CHAT, db, LOGGER
from shivu.modules import ALL_MODULES

# Initialize new collections
group_config_collection = db["group_config"]
user_cooldown_collection = db["user_cooldown"]
character_cache_collection = db["character_cache"]
daily_leaderboard_collection = db["daily_leaderboard"]
weekly_leaderboard_collection = db["weekly_leaderboard"]
wrong_guesses_collection = db["wrong_guesses"]

# Constants
RARITY_COINS = {
    "Common": 100,
    "Rare": 250,
    "Epic": 500,
    "Legendary": 1000,
    "Mythic": 2000
}
CACHE_TTL = 300  # 5 minutes

# Global variables
locks = {}
message_counters = {}
spam_counters = {}
last_characters = {}
sent_characters = {}
first_correct_guesses = {}
message_counts = {}
character_cache = []
cache_refresh_time = 0
last_user = {}
warned_users = {}

# Import all modules
for module_name in ALL_MODULES:
    imported_module = importlib.import_module("shivu.modules." + module_name)

def escape_markdown(text):
    escape_chars = r'\*_`\\~>#+-=|{}.!'
    return re.sub(r'([%s])' % re.escape(escape_chars), r'\\\1', text)

# ========== HELPER FUNCTIONS ==========

async def get_cached_characters() -> List[Dict]:
    """Get characters from cache or refresh if needed"""
    global character_cache, cache_refresh_time
    
    current_time = time.time()
    if not character_cache or (current_time - cache_refresh_time) > CACHE_TTL:
        LOGGER.info("Refreshing character cache...")
        character_cache = await collection.find({}).to_list(length=None)
        cache_refresh_time = current_time
        
        # Update cache collection for persistence
        await character_cache_collection.delete_many({})
        if character_cache:
            await character_cache_collection.insert_many(
                [{**char, 'cached_at': current_time} for char in character_cache]
            )
        LOGGER.info(f"Cached {len(character_cache)} characters")
    
    return character_cache

async def get_group_config(chat_id: int) -> Dict:
    """Get or create group configuration"""
    config = await group_config_collection.find_one({"group_id": str(chat_id)})
    
    if not config:
        # Default configuration
        config = {
            "group_id": str(chat_id),
            "group_name": "",
            "enabled": True,
            "drop_frequency": 100,
            "drop_cooldown": 300,  # 5 minutes default
            "last_drop_time": 0,
            "paused": False
        }
        await group_config_collection.insert_one(config)
    
    return config

async def update_group_config(chat_id: int, update_fields: Dict):
    """Update group configuration"""
    await group_config_collection.update_one(
        {"group_id": str(chat_id)},
        {"$set": update_fields},
        upsert=True
    )

async def check_admin(update: Update, context: CallbackContext) -> bool:
    """Check if user is admin"""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        return member.status in ['administrator', 'creator']
    except Exception as e:
        LOGGER.error(f"Admin check failed: {e}")
        return False

async def update_leaderboards(user_id: int, coins: int):
    """Update all leaderboards"""
    try:
        current_time = time.time()
        
        # Daily leaderboard
        today = datetime.now().strftime("%Y-%m-%d")
        await daily_leaderboard_collection.update_one(
            {"user_id": user_id, "date": today},
            {"$inc": {"coins": coins}, "$set": {"last_updated": current_time}},
            upsert=True
        )
        
        # Weekly leaderboard
        week_num = datetime.now().strftime("%Y-W%W")
        await weekly_leaderboard_collection.update_one(
            {"user_id": user_id, "week": week_num},
            {"$inc": {"coins": coins}, "$set": {"last_updated": current_time}},
            upsert=True
        )
        
    except Exception as e:
        LOGGER.error(f"Leaderboard update error: {e}")

# ========== MESSAGE COUNTER WITH ALL FEATURES ==========

async def message_counter(update: Update, context: CallbackContext) -> None:
    chat_id = str(update.effective_chat.id)
    user_id = update.effective_user.id
    
    # Get group config
    config = await get_group_config(chat_id)
    
    # Check if game is enabled and not paused
    if not config.get("enabled", True) or config.get("paused", False):
        return
    
    # Check group drop cooldown
    current_time = time.time()
    last_drop = config.get("last_drop_time", 0)
    drop_cooldown = config.get("drop_cooldown", 300)
    
    if current_time - last_drop < drop_cooldown:
        return  # Cooldown active, no drops
    
    # Global spam detection
    message_text = update.message.text or ""
    if chat_id not in spam_counters:
        spam_counters[chat_id] = {}
    
    if user_id not in spam_counters[chat_id]:
        spam_counters[chat_id][user_id] = {"messages": [], "last_time": 0}
    
    user_spam = spam_counters[chat_id][user_id]
    
    # Clean old messages (older than 30 seconds)
    user_spam["messages"] = [
        msg for msg in user_spam["messages"] 
        if current_time - msg["time"] < 30
    ]
    
    # Check for repeated messages
    user_spam["messages"].append({
        "text": message_text,
        "time": current_time
    })
    
    # Count same messages
    same_count = 0
    for msg in user_spam["messages"]:
        if msg["text"] == message_text:
            same_count += 1
    
    # If same message sent 5+ times in 30 seconds, ignore
    if same_count >= 5:
        # Silent cooldown - just return without processing
        return
    
    # Original anti-spam logic (from existing code)
    if chat_id not in locks:
        locks[chat_id] = asyncio.Lock()
    lock = locks[chat_id]
    
    async with lock:
        # Get chat frequency from config
        message_frequency = config.get("drop_frequency", 100)
        
        # User spam detection (existing logic)
        if chat_id in last_user and last_user[chat_id]['user_id'] == user_id:
            last_user[chat_id]['count'] += 1
            if last_user[chat_id]['count'] >= 10:
                if user_id in warned_users and time.time() - warned_users[user_id] < 600:
                    return
                else:
                    await update.message.reply_text(
                        f"⚠️ Don't Spam {update.effective_user.first_name}...\n"
                        f"Your Messages Will be ignored for 10 Minutes..."
                    )
                    warned_users[user_id] = time.time()
                    return
        else:
            last_user[chat_id] = {'user_id': user_id, 'count': 1}
        
        # Initialize message count if not exists
        if chat_id not in message_counts:
            message_counts[chat_id] = 0
        
        message_counts[chat_id] += 1
        
        # Check if it's time for a character drop
        if message_counts[chat_id] >= message_frequency:
            if await send_image(update, context, config):
                message_counts[chat_id] = 0

# ========== CHARACTER DROP FUNCTION ==========

async def send_image(update: Update, context: CallbackContext, config: Dict) -> bool:
    """Send character image to chat, returns True if sent successfully"""
    chat_id = update.effective_chat.id
    
    try:
        # Get cached characters
        all_characters = await get_cached_characters()
        if not all_characters:
            LOGGER.error("No characters found in database")
            return False
        
        # Initialize sent characters list for this chat
        if str(chat_id) not in sent_characters:
            sent_characters[str(chat_id)] = []
        
        # Reset if all characters have been sent
        if len(sent_characters[str(chat_id)]) >= len(all_characters):
            sent_characters[str(chat_id)] = []
        
        # Filter out already sent characters
        available_chars = [c for c in all_characters if c['id'] not in sent_characters[str(chat_id)]]
        if not available_chars:
            available_chars = all_characters  # Reset if no available chars
        
        # Select random character
        character = random.choice(available_chars)
        
        # Update tracking
        sent_characters[str(chat_id)].append(character['id'])
        last_characters[chat_id] = character
        
        # Remove first correct guess for this chat
        if chat_id in first_correct_guesses:
            del first_correct_guesses[chat_id]
        
        # Update last drop time
        await update_group_config(chat_id, {
            "last_drop_time": time.time(),
            "group_name": update.effective_chat.title if update.effective_chat else ""
        })
        
        # Send character image
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=character['img_url'],
            caption=f"""🎮 A New {character['rarity']} Character Appeared!
💰 Guess Reward: {RARITY_COINS.get(character['rarity'], 100)} coins

/guess Character Name and add to Your Collection""",
            parse_mode='Markdown'
        )
        return True
        
    except Exception as e:
        LOGGER.error(f"Error in send_image: {e}")
        return False

# ========== GUESS FUNCTION WITH ALL FEATURES ==========

async def guess(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    # Check if character exists in this chat
    if chat_id not in last_characters:
        await update.message.reply_text("❌ No character to guess! Wait for one to appear.")
        return
    
    # Check if already guessed
    if chat_id in first_correct_guesses:
        guesser_id = first_correct_guesses[chat_id]
        if guesser_id == user_id:
            await update.message.reply_text("✅ You already guessed this character!")
        else:
            await update.message.reply_text("❌ This character was already guessed by someone else!")
        return
    
    # Check per-user guess cooldown (10 seconds)
    cooldown_key = f"{chat_id}:{user_id}"
    current_time = time.time()
    
    cooldown_data = await user_cooldown_collection.find_one({"key": cooldown_key})
    if cooldown_data:
        last_guess = cooldown_data.get("last_guess", 0)
        if current_time - last_guess < 10:
            remaining = int(10 - (current_time - last_guess))
            await update.message.reply_text(f"⏳ Please wait {remaining} seconds before guessing again!")
            return
    
    # Update guess cooldown
    await user_cooldown_collection.update_one(
        {"key": cooldown_key},
        {"$set": {"last_guess": current_time, "user_id": user_id, "chat_id": chat_id}},
        upsert=True
    )
    
    # Check wrong guesses limit for this character
    character_id = last_characters[chat_id]['id']
    wrong_data = await wrong_guesses_collection.find_one({
        "chat_id": chat_id,
        "character_id": character_id
    })
    
    if wrong_data and wrong_data.get("count", 0) >= 10:
        await update.message.reply_text("💨 This character disappeared due to too many wrong guesses!")
        # Remove character from current drop
        if chat_id in last_characters:
            del last_characters[chat_id]
        return
    
    # Get guess from command arguments
    if not context.args:
        await update.message.reply_text("❌ Please provide a guess! Example: /guess Naruto")
        return
    
    guess_text = ' '.join(context.args).lower().strip()
    
    # Validate guess
    if "()" in guess_text or "&" in guess_text.lower():
        await update.message.reply_text("❌ Invalid characters in guess!")
        return
    
    # Get correct character name parts
    character = last_characters[chat_id]
    correct_name = character['name'].lower()
    name_parts = correct_name.split()
    
    # Check if guess is correct
    is_correct = (
        guess_text == correct_name or
        sorted(name_parts) == sorted(guess_text.split()) or
        any(part == guess_text for part in name_parts)
    )
    
    if is_correct:
        # Mark as guessed
        first_correct_guesses[chat_id] = user_id
        
        # Calculate coins earned
        coins_earned = RARITY_COINS.get(character['rarity'], 100)
        
        # Update user data
        user = await user_collection.find_one({'id': user_id})
        if user:
            # Update existing user
            update_fields = {}
            if hasattr(update.effective_user, 'username') and update.effective_user.username != user.get('username'):
                update_fields['username'] = update.effective_user.username
            if update.effective_user.first_name != user.get('first_name'):
                update_fields['first_name'] = update.effective_user.first_name
            
            # Add character if not already in collection
            if character['id'] not in [c['id'] for c in user.get('characters', [])]:
                update_fields.setdefault('$push', {})['characters'] = character
            
            # Update coins
            update_fields.setdefault('$inc', {})['coins'] = coins_earned
            
            # Update user
            if '$push' in update_fields or '$inc' in update_fields:
                await user_collection.update_one({'id': user_id}, update_fields)
            elif update_fields:
                await user_collection.update_one({'id': user_id}, {'$set': update_fields})
        else:
            # Create new user
            await user_collection.insert_one({
                'id': user_id,
                'username': update.effective_user.username if hasattr(update.effective_user, 'username') else None,
                'first_name': update.effective_user.first_name,
                'characters': [character],
                'coins': coins_earned,
                'joined_date': datetime.now()
            })
        
        # Update group user totals
        group_user_total = await group_user_totals_collection.find_one({
            'user_id': user_id, 
            'group_id': chat_id
        })
        
        if group_user_total:
            await group_user_totals_collection.update_one(
                {'user_id': user_id, 'group_id': chat_id},
                {
                    '$inc': {'count': 1, 'coins': coins_earned},
                    '$set': {
                        'username': update.effective_user.username if hasattr(update.effective_user, 'username') else None,
                        'first_name': update.effective_user.first_name
                    }
                }
            )
        else:
            await group_user_totals_collection.insert_one({
                'user_id': user_id,
                'group_id': chat_id,
                'username': update.effective_user.username if hasattr(update.effective_user, 'username') else None,
                'first_name': update.effective_user.first_name,
                'count': 1,
                'coins': coins_earned
            })
        
        # Update global group stats
        group_info = await top_global_groups_collection.find_one({'group_id': chat_id})
        if group_info:
            await top_global_groups_collection.update_one(
                {'group_id': chat_id},
                {
                    '$inc': {'count': 1},
                    '$set': {'group_name': update.effective_chat.title}
                }
            )
        else:
            await top_global_groups_collection.insert_one({
                'group_id': chat_id,
                'group_name': update.effective_chat.title,
                'count': 1
            })
        
        # Update leaderboards
        await update_leaderboards(user_id, coins_earned)
        
        # Clear wrong guesses for this character
        await wrong_guesses_collection.delete_one({
            "chat_id": chat_id,
            "character_id": character_id
        })
        
        # Create inline keyboard
        keyboard = [[
            InlineKeyboardButton(
                "📜 See Collection", 
                switch_inline_query_current_chat=f"collection.{user_id}"
            ),
            InlineKeyboardButton(
                "🏆 Leaderboard", 
                callback_data=f"leaderboard_{chat_id}"
            )
        ]]
        
        # Send success message
        await update.message.reply_text(
            f'<b><a href="tg://user?id={user_id}">{escape(update.effective_user.first_name)}</a></b> '
            f'🎉 <b>Correct Guess!</b>\n\n'
            f'📛 <b>Name:</b> {character["name"]}\n'
            f'🎬 <b>Anime:</b> {character["anime"]}\n'
            f'⭐ <b>Rarity:</b> {character["rarity"]}\n'
            f'💰 <b>Coins Earned:</b> +{coins_earned}\n\n'
            f'✅ Added to your collection! Use /harem to view.',
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
    else:
        # Wrong guess - increment counter
        await wrong_guesses_collection.update_one(
            {
                "chat_id": chat_id,
                "character_id": character_id,
                "character_name": character['name']
            },
            {
                "$inc": {"count": 1},
                "$set": {"last_wrong": current_time}
            },
            upsert=True
        )
        
        # Get current wrong count
        wrong_data = await wrong_guesses_collection.find_one({
            "chat_id": chat_id,
            "character_id": character_id
        })
        
        wrongs = wrong_data.get("count", 1) if wrong_data else 1
        
        if wrongs >= 10:
            # Character disappears
            await update.message.reply_text(
                "💨 <b>The character disappeared!</b>\n"
                "Too many wrong guesses (10/10)",
                parse_mode='HTML'
            )
            # Remove character from current drop
            if chat_id in last_characters:
                del last_characters[chat_id]
        else:
            await update.message.reply_text(
                f'❌ <b>Wrong guess!</b>\n'
                f'Attempts: {wrongs}/10\n\n'
                f'💡 Hint: Try checking spelling or use full name.',
                parse_mode='HTML'
            )

# ========== LEADERBOARD FUNCTIONS ==========

async def topdaily(update: Update, context: CallbackContext):
    """Daily leaderboard"""
    today = datetime.now().strftime("%Y-%m-%d")
    
    try:
        pipeline = [
            {"$match": {"date": today}},
            {"$sort": {"coins": -1}},
            {"$limit": 10},
            {"$lookup": {
                "from": "user_collection",
                "localField": "user_id",
                "foreignField": "id",
                "as": "user_info"
            }}
        ]
        
        top_users = await daily_leaderboard_collection.aggregate(pipeline).to_list(length=10)
        
        if not top_users:
            await update.message.reply_text("📊 No daily stats yet! Start guessing characters!")
            return
        
        message = "🏆 *DAILY LEADERBOARD* 🏆\n"
        message += f"📅 {today}\n\n"
        
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
        
        for i, user in enumerate(top_users, 1):
            user_info = user.get("user_info", [{}])[0]
            username = user_info.get("first_name", f"User_{user['user_id']}")
            coins = user.get("coins", 0)
            
            medal = medals[i-1] if i <= 10 else f"{i}."
            message += f"{medal} {escape_markdown(username)}: *{coins}* coins\n"
        
        message += f"\nNext reset in: *24 hours*"
        await update.message.reply_text(message, parse_mode='Markdown')
        
    except Exception as e:
        LOGGER.error(f"Daily leaderboard error: {e}")
        await update.message.reply_text("❌ Error loading daily leaderboard")

async def topweekly(update: Update, context: CallbackContext):
    """Weekly leaderboard"""
    week_num = datetime.now().strftime("%Y-W%W")
    
    try:
        pipeline = [
            {"$match": {"week": week_num}},
            {"$sort": {"coins": -1}},
            {"$limit": 10},
            {"$lookup": {
                "from": "user_collection",
                "localField": "user_id",
                "foreignField": "id",
                "as": "user_info"
            }}
        ]
        
        top_users = await weekly_leaderboard_collection.aggregate(pipeline).to_list(length=10)
        
        if not top_users:
            await update.message.reply_text("📊 No weekly stats yet! Start guessing characters!")
            return
        
        message = "🏆 *WEEKLY LEADERBOARD* 🏆\n"
        message += f"📅 Week {week_num.split('-')[1]}\n\n"
        
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
        
        for i, user in enumerate(top_users, 1):
            user_info = user.get("user_info", [{}])[0]
            username = user_info.get("first_name", f"User_{user['user_id']}")
            coins = user.get("coins", 0)
            
            medal = medals[i-1] if i <= 10 else f"{i}."
            message += f"{medal} {escape_markdown(username)}: *{coins}* coins\n"
        
        # Calculate days until reset (next Monday)
        today = datetime.now()
        days_until_monday = (7 - today.weekday()) % 7
        if days_until_monday == 0:
            days_until_monday = 7
        
        message += f"\nNext reset in: *{days_until_monday} days*"
        await update.message.reply_text(message, parse_mode='Markdown')
        
    except Exception as e:
        LOGGER.error(f"Weekly leaderboard error: {e}")
        await update.message.reply_text("❌ Error loading weekly leaderboard")

async def topglobal(update: Update, context: CallbackContext):
    """Global all-time leaderboard"""
    try:
        pipeline = [
            {"$sort": {"coins": -1}},
            {"$limit": 10},
            {"$project": {
                "_id": 0,
                "id": 1,
                "first_name": 1,
                "username": 1,
                "coins": 1,
                "characters_count": {"$size": "$characters"}
            }}
        ]
        
        top_users = await user_collection.aggregate(pipeline).to_list(length=10)
        
        if not top_users:
            await update.message.reply_text("📊 No global stats yet!")
            return
        
        message = "🌍 *GLOBAL LEADERBOARD* 🌍\n\n"
        
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
        
        for i, user in enumerate(top_users, 1):
            username = user.get("first_name", f"User_{user['id']}")
            coins = user.get("coins", 0)
            count = user.get("characters_count", 0)
            
            medal = medals[i-1] if i <= 10 else f"{i}."
            message += f"{medal} *{escape_markdown(username)}*\n"
            message += f"   💰 {coins} coins | 👥 {count} characters\n\n"
        
        await update.message.reply_text(message, parse_mode='Markdown')
        
    except Exception as e:
        LOGGER.error(f"Global leaderboard error: {e}")
        await update.message.reply_text("❌ Error loading global leaderboard")

# ========== ADMIN COMMANDS ==========

async def setfrequency(update: Update, context: CallbackContext):
    """Set drop frequency"""
    if not await check_admin(update, context):
        await update.message.reply_text("❌ Admin only command!")
        return
    
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /setfrequency <number>\nExample: /setfrequency 50")
        return
    
    frequency = int(context.args[0])
    if frequency < 1:
        await update.message.reply_text("❌ Frequency must be at least 1")
        return
    
    chat_id = update.effective_chat.id
    await update_group_config(chat_id, {"drop_frequency": frequency})
    
    await update.message.reply_text(f"✅ Drop frequency set to *{frequency} messages*", parse_mode='Markdown')

async def setcooldown(update: Update, context: CallbackContext):
    """Set drop cooldown in seconds"""
    if not await check_admin(update, context):
        await update.message.reply_text("❌ Admin only command!")
        return
    
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /setcooldown <seconds>\nExample: /setcooldown 300")
        return
    
    cooldown = int(context.args[0])
    if cooldown < 0:
        await update.message.reply_text("❌ Cooldown must be positive")
        return
    
    chat_id = update.effective_chat.id
    await update_group_config(chat_id, {"drop_cooldown": cooldown})
    
    minutes = cooldown // 60
    seconds = cooldown % 60
    time_str = f"{minutes}m {seconds}s" if minutes > 0 else f"{seconds}s"
    
    await update.message.reply_text(f"✅ Drop cooldown set to *{time_str}*", parse_mode='Markdown')

async def pausegame(update: Update, context: CallbackContext):
    """Pause game in group"""
    if not await check_admin(update, context):
        await update.message.reply_text("❌ Admin only command!")
        return
    
    chat_id = update.effective_chat.id
    await update_group_config(chat_id, {"paused": True})
    
    await update.message.reply_text("⏸️ *Game paused* in this group\nUse /resumegame to resume", parse_mode='Markdown')

async def resumegame(update: Update, context: CallbackContext):
    """Resume game in group"""
    if not await check_admin(update, context):
        await update.message.reply_text("❌ Admin only command!")
        return
    
    chat_id = update.effective_chat.id
    await update_group_config(chat_id, {"paused": False})
    
    await update.message.reply_text("▶️ *Game resumed* in this group", parse_mode='Markdown')

async def forcedrop(update: Update, context: CallbackContext):
    """Force character drop"""
    if not await check_admin(update, context):
        await update.message.reply_text("❌ Admin only command!")
        return
    
    chat_id = update.effective_chat.id
    config = await get_group_config(chat_id)
    
    if await send_image(update, context, config):
        await update.message.reply_text("🎮 *Character drop forced!*", parse_mode='Markdown')
    else:
        await update.message.reply_text("❌ Failed to force drop")

async def gameinfo(update: Update, context: CallbackContext):
    """Show game settings"""
    chat_id = update.effective_chat.id
    config = await get_group_config(chat_id)
    
    # Calculate time until next drop
    current_time = time.time()
    last_drop = config.get("last_drop_time", 0)
    cooldown = config.get("drop_cooldown", 300)
    
    time_since_last = current_time - last_drop
    time_until_next = max(0, cooldown - time_since_last)
    
    minutes = int(time_until_next // 60)
    seconds = int(time_until_next % 60)
    
    message = (
        f"⚙️ *Game Settings*\n\n"
        f"• Status: {'⏸️ Paused' if config.get('paused') else '▶️ Active'}\n"
        f"• Drop Frequency: {config.get('drop_frequency', 100)} messages\n"
        f"• Drop Cooldown: {cooldown // 60}m {cooldown % 60}s\n"
        f"• Next drop in: {minutes}m {seconds}s\n"
        f"• Messages until drop: {config.get('drop_frequency', 100) - message_counts.get(str(chat_id), 0)}\n\n"
        f"*Admin Commands:*\n"
        f"/setfrequency <number>\n"
        f"/setcooldown <seconds>\n"
        f"/pausegame /resumegame\n"
        f"/forcedrop /gameinfo"
    )
    
    await update.message.reply_text(message, parse_mode='Markdown')

# ========== EXISTING FUNCTIONS (UPDATED) ==========

async def fav(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    
    if not context.args:
        await update.message.reply_text('Please provide Character id...\nExample: /fav 123')
        return
    
    character_id = context.args[0]
    
    user = await user_collection.find_one({'id': user_id})
    if not user:
        await update.message.reply_text('You have not collected any characters yet.')
        return
    
    # Find character in user's collection
    character = next((c for c in user.get('characters', []) if c['id'] == character_id), None)
    if not character:
        await update.message.reply_text('This character is not in your collection.')
        return
    
    # Update favorites
    await user_collection.update_one(
        {'id': user_id},
        {'$set': {'favorites': [character_id]}}
    )
    
    await update.message.reply_text(
        f'⭐ *{character["name"]}* has been added to your favorites!',
        parse_mode='Markdown'
    )

# ========== CLEANUP TASKS (FIXED - USING JOB QUEUE) ==========

async def cleanup_tasks(context: CallbackContext):
    """Periodic cleanup tasks - called by JobQueue"""
    try:
        # Clean old cooldowns (older than 1 hour)
        cutoff = time.time() - 3600
        deleted_cooldowns = await user_cooldown_collection.delete_many({
            "last_guess": {"$lt": cutoff}
        })
        
        # Clean old wrong guesses (older than 24 hours)
        cutoff_24h = time.time() - 86400
        deleted_wrong_guesses = await wrong_guesses_collection.delete_many({
            "last_wrong": {"$lt": cutoff_24h}
        })
        
        LOGGER.info(f"Cleanup completed: {deleted_cooldowns.deleted_count} cooldowns, "
                   f"{deleted_wrong_guesses.deleted_count} wrong guesses removed")
        
    except Exception as e:
        LOGGER.error(f"Cleanup error: {e}")

# ========== CACHE REFRESH FUNCTION ==========

async def refresh_character_cache(context: CallbackContext = None):
    """Refresh character cache - can be called manually or by JobQueue"""
    await get_cached_characters()

# ========== MAIN FUNCTION (PROPERLY FIXED) ==========

def main() -> None:
    """Run bot."""
    
    # Add admin commands
    application.add_handler(CommandHandler("setfrequency", setfrequency, block=False))
    application.add_handler(CommandHandler("setcooldown", setcooldown, block=False))
    application.add_handler(CommandHandler("pausegame", pausegame, block=False))
    application.add_handler(CommandHandler("resumegame", resumegame, block=False))
    application.add_handler(CommandHandler("forcedrop", forcedrop, block=False))
    application.add_handler(CommandHandler("gameinfo", gameinfo, block=False))
    
    # Add leaderboard commands
    application.add_handler(CommandHandler("topdaily", topdaily, block=False))
    application.add_handler(CommandHandler("topweekly", topweekly, block=False))
    application.add_handler(CommandHandler("topglobal", topglobal, block=False))
    application.add_handler(CommandHandler("top", topglobal, block=False))  # Alias
    
    # Existing handlers
    application.add_handler(CommandHandler(["guess", "protecc", "collect", "grab", "hunt"], guess, block=False))
    application.add_handler(CommandHandler("fav", fav, block=False))
    application.add_handler(MessageHandler(filters.ALL, message_counter, block=False))
    
    # ✅ CORRECT: Schedule periodic cleanup using JobQueue (production-safe)
    application.job_queue.run_repeating(
        cleanup_tasks,
        interval=3600,  # Run every hour (3600 seconds)
        first=10  # Start after 10 seconds
    )
    
    # ✅ CORRECT: Initialize character cache on startup using JobQueue
    application.job_queue.run_once(
        lambda context: asyncio.create_task(get_cached_characters()),
        when=0  # Run immediately
    )
    
    # ✅ CORRECT: Schedule cache refresh every 5 minutes (300 seconds)
    application.job_queue.run_repeating(
        lambda context: asyncio.create_task(get_cached_characters()),
        interval=300,  # Every 5 minutes
        first=60  # Start after 1 minute
    )
    
    # Start the bot
    LOGGER.info("Starting bot with JobQueue-based background tasks...")
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    shivuu.start()
    LOGGER.info("Bot started with upgraded features")
    main()
