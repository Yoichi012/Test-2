import importlib
import importlib.util
import os
import sys
import time
import random
import asyncio
from pathlib import Path
from html import escape

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, CallbackContext, MessageHandler, filters, ContextTypes

from shivu import (
    collection, 
    top_global_groups_collection, 
    group_user_totals_collection, 
    user_collection, 
    user_totals_collection, 
    shivuu, 
    application, 
    db, 
    LOGGER,
    OWNER_ID,
    SUDO_USERS,
    MONGO_URL
)

# ========================
# GLOBAL STATE
# ========================
locks = {}
last_user = {}
warned_users = {}
message_counts = {}
last_characters = {}
sent_characters = {}
first_correct_guesses = {}
loaded_modules = {}

# ========================
# SMALL CAPS CONVERTER
# ========================
SMALL_CAPS_MAP = {
    'a': 'ᴀ', 'b': 'ʙ', 'c': 'ᴄ', 'd': 'ᴅ', 'e': 'ᴇ', 'f': 'ғ', 'g': 'ɢ', 'h': 'ʜ',
    'i': 'ɪ', 'j': 'ᴊ', 'k': 'ᴋ', 'l': 'ʟ', 'm': 'ᴍ', 'n': 'ɴ', 'o': 'ᴏ', 'p': 'ᴘ',
    'q': 'ǫ', 'r': 'ʀ', 's': 's', 't': 'ᴛ', 'u': 'ᴜ', 'v': 'ᴠ', 'w': 'ᴡ', 'x': 'x',
    'y': 'ʏ', 'z': 'ᴢ'
}

def to_small_caps(text: str) -> str:
    """Convert text to small caps aesthetic"""
    return ''.join(SMALL_CAPS_MAP.get(c.lower(), c) for c in text)

# ========================
# MODULE LOADER
# ========================
def load_module(module_path: str, module_name: str) -> bool:
    """Dynamically load a single module"""
    try:
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            loaded_modules[module_name] = module
            LOGGER.info(f"✅ Loaded module: {module_name}")
            return True
    except Exception as e:
        LOGGER.error(f"❌ Failed to load {module_name}: {e}")
    return False

def auto_load_modules():
    """Auto-discover and load all modules from shivu/modules/"""
    modules_dir = Path(__file__).parent / "shivu" / "modules"
    
    if not modules_dir.exists():
        LOGGER.warning(f"Modules directory not found: {modules_dir}")
        return
    
    LOGGER.info(f"🔍 Scanning for modules in: {modules_dir}")
    
    for root, dirs, files in os.walk(modules_dir):
        for file in files:
            if file.endswith(".py") and not file.startswith("_"):
                module_path = os.path.join(root, file)
                module_name = file[:-3]  # Remove .py
                relative_path = os.path.relpath(module_path, modules_dir.parent)
                full_module_name = relative_path.replace(os.sep, ".")[:-3]
                
                load_module(module_path, full_module_name)
    
    LOGGER.info(f"✅ Loaded {len(loaded_modules)} modules")

# ========================
# ANTI-SPAM SYSTEM
# ========================
async def check_spam(chat_id: int, user_id: int) -> bool:
    """Returns True if user should be ignored (is spamming)"""
    current_time = time.time()
    
    # Check if user is currently warned
    if user_id in warned_users:
        if current_time - warned_users[user_id] < 600:  # 10 minutes
            return True
        else:
            del warned_users[user_id]
    
    # Track consecutive messages
    if chat_id in last_user and last_user[chat_id]['user_id'] == user_id:
        time_diff = current_time - last_user[chat_id]['timestamp']
        
        if time_diff < 20:  # Within 20 seconds
            last_user[chat_id]['count'] += 1
            
            if last_user[chat_id]['count'] >= 10:
                warned_users[user_id] = current_time
                return True
        else:
            # Reset if more than 20 seconds passed
            last_user[chat_id] = {'user_id': user_id, 'count': 1, 'timestamp': current_time}
    else:
        last_user[chat_id] = {'user_id': user_id, 'count': 1, 'timestamp': current_time}
    
    return False

# ========================
# MESSAGE COUNTER
# ========================
async def message_counter(update: Update, context: CallbackContext) -> None:
    """Count messages and spawn characters"""
    if not update.effective_chat or not update.effective_user:
        return
    
    chat_id = update.effective_chat.id  # Keep as INTEGER
    user_id = update.effective_user.id
    
    # Initialize lock
    if chat_id not in locks:
        locks[chat_id] = asyncio.Lock()
    
    async with locks[chat_id]:
        # Anti-spam check
        if await check_spam(chat_id, user_id):
            # Only show warning once when first triggered
            if user_id not in warned_users or time.time() - warned_users[user_id] > 590:
                await update.message.reply_text(
                    f"⚠️ {to_small_caps('dont spam')} {escape(update.effective_user.first_name)}...\n"
                    f"{to_small_caps('your messages will be ignored for 10 minutes')}..."
                )
            return
        
        # Get message frequency
        chat_frequency = await user_totals_collection.find_one({'chat_id': chat_id})
        message_frequency = chat_frequency.get('message_frequency', 100) if chat_frequency else 100
        
        # Increment counter
        message_counts[chat_id] = message_counts.get(chat_id, 0) + 1
        
        # Spawn character
        if message_counts[chat_id] >= message_frequency:
            await send_image(update, context)
            message_counts[chat_id] = 0

# ========================
# CHARACTER SPAWN
# ========================
async def send_image(update: Update, context: CallbackContext) -> None:
    """Spawn a new character"""
    chat_id = update.effective_chat.id  # Keep as INTEGER
    
    all_characters = list(await collection.find({}).to_list(length=None))
    
    if not all_characters:
        return
    
    if chat_id not in sent_characters:
        sent_characters[chat_id] = []
    
    if len(sent_characters[chat_id]) >= len(all_characters):
        sent_characters[chat_id] = []
    
    available = [c for c in all_characters if c['id'] not in sent_characters[chat_id]]
    character = random.choice(available)
    
    sent_characters[chat_id].append(character['id'])
    last_characters[chat_id] = character
    
    if chat_id in first_correct_guesses:
        del first_correct_guesses[chat_id]
    
    rarity_text = to_small_caps(f"a new {character['rarity']} character appeared")
    guess_text = to_small_caps("guess character name and add to your harem")
    
    await context.bot.send_photo(
        chat_id=chat_id,
        photo=character['img_url'],
        caption=f"{rarity_text}...\n/{guess_text}",
        parse_mode='HTML'
    )

# ========================
# GUESS COMMAND
# ========================
async def guess(update: Update, context: CallbackContext) -> None:
    """Handle character guessing"""
    chat_id = update.effective_chat.id  # Keep as INTEGER
    user_id = update.effective_user.id
    
    if chat_id not in last_characters:
        return
    
    if chat_id in first_correct_guesses:
        await update.message.reply_text(f'❌ {to_small_caps("already guessed by someone")}... {to_small_caps("try next time")}')
        return
    
    guess = ' '.join(context.args).lower() if context.args else ''
    
    if "()" in guess or "&" in guess:
        await update.message.reply_text(f"❌ {to_small_caps('you cannot use these types of words in your guess')}")
        return
    
    name_parts = last_characters[chat_id]['name'].lower().split()
    
    if sorted(name_parts) == sorted(guess.split()) or any(part == guess for part in name_parts):
        first_correct_guesses[chat_id] = user_id
        character = last_characters[chat_id]
        
        # Step 1: Congratulations message with coins
        congrats_msg = await update.message.reply_text(
            f"🎉 {to_small_caps('congratulations')} <b>{escape(update.effective_user.first_name)}</b>! +100 {to_small_caps('coins')} 🎉",
            parse_mode='HTML'
        )
        
        # Step 2: React with emoji
        try:
            await congrats_msg.set_reaction("🎉")
        except Exception as e:
            LOGGER.debug(f"Reaction failed: {e}")
        
        # Update user in database
        user = await user_collection.find_one({'id': user_id})
        if user:
            update_fields = {}
            if hasattr(update.effective_user, 'username') and update.effective_user.username != user.get('username'):
                update_fields['username'] = update.effective_user.username
            if update.effective_user.first_name != user.get('first_name'):
                update_fields['first_name'] = update.effective_user.first_name
            if update_fields:
                await user_collection.update_one({'id': user_id}, {'$set': update_fields})
            
            await user_collection.update_one(
                {'id': user_id}, 
                {
                    '$push': {'characters': character},
                    '$inc': {'coins': 100}
                }
            )
        else:
            await user_collection.insert_one({
                'id': user_id,
                'username': getattr(update.effective_user, 'username', None),
                'first_name': update.effective_user.first_name,
                'characters': [character],
                'coins': 100
            })
        
        # Update group stats (chat_id as INTEGER)
        group_user_total = await group_user_totals_collection.find_one({'user_id': user_id, 'group_id': chat_id})
        if group_user_total:
            await group_user_totals_collection.update_one(
                {'user_id': user_id, 'group_id': chat_id}, 
                {'$inc': {'count': 1}}
            )
        else:
            await group_user_totals_collection.insert_one({
                'user_id': user_id,
                'group_id': chat_id,
                'username': getattr(update.effective_user, 'username', None),
                'first_name': update.effective_user.first_name,
                'count': 1
            })
        
        # Update global group stats (chat_id as INTEGER)
        await top_global_groups_collection.update_one(
            {'group_id': chat_id},
            {
                '$set': {'group_name': update.effective_chat.title},
                '$inc': {'count': 1}
            },
            upsert=True
        )
        
        # Step 3: Character card
        keyboard = [[InlineKeyboardButton("ʜᴀʀᴇᴍ", switch_inline_query_current_chat=f"collection.{user_id}")]]
        
        await update.message.reply_text(
            f'<b>ɴᴀᴍᴇ:</b> {character["name"]}\n'
            f'<b>ᴀɴɪᴍᴇ:</b> {character["anime"]}\n'
            f'<b>ʀᴀʀɪᴛʏ:</b> {character["rarity"]}\n\n'
            f'{to_small_caps("successfully added to your harem")} ✅',
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await update.message.reply_text(f'❌ {to_small_caps("please write correct character name")}')

# ========================
# FAVORITE COMMAND
# ========================
async def fav(update: Update, context: CallbackContext) -> None:
    """Set favorite character"""
    user_id = update.effective_user.id
    
    if not context.args:
        await update.message.reply_text(to_small_caps('please provide character id'))
        return
    
    character_id = context.args[0]
    user = await user_collection.find_one({'id': user_id})
    
    if not user:
        await update.message.reply_text(to_small_caps('you have not guessed any characters yet'))
        return
    
    character = next((c for c in user.get('characters', []) if c['id'] == character_id), None)
    
    if not character:
        await update.message.reply_text(to_small_caps('this character is not in your collection'))
        return
    
    await user_collection.update_one({'id': user_id}, {'$set': {'favorites': [character_id]}})
    await update.message.reply_text(f'{to_small_caps("character")} {character["name"]} {to_small_caps("has been added to your favorite")}')

# ========================
# FREQUENCY COMMANDS
# ========================
async def setfrequency(update: Update, context: CallbackContext) -> None:
    """Set spawn frequency for current chat (Admin only)"""
    chat_id = update.effective_chat.id  # Keep as INTEGER
    user_id = update.effective_user.id
    
    # Check if user is admin
    try:
        chat_member = await context.bot.get_chat_member(chat_id, user_id)
        if chat_member.status not in ['creator', 'administrator']:
            await update.message.reply_text(f"❌ {to_small_caps('only admins can use this command')}")
            return
    except Exception as e:
        LOGGER.error(f"Failed to check admin status: {e}")
        return
    
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(f"{to_small_caps('usage')}: /setfrequency [number]")
        return
    
    frequency = int(context.args[0])
    
    if frequency < 10:
        await update.message.reply_text(f"❌ {to_small_caps('minimum frequency is 10 messages')}")
        return
    
    await user_totals_collection.update_one(
        {'chat_id': chat_id},
        {'$set': {'message_frequency': frequency}},
        upsert=True
    )
    
    await update.message.reply_text(
        f"✅ {to_small_caps('spawn frequency set to')} {frequency} {to_small_caps('messages')}"
    )

async def setfrequencyall(update: Update, context: CallbackContext) -> None:
    """Set global default spawn frequency (Owner only)"""
    user_id = update.effective_user.id
    
    if user_id != OWNER_ID:
        await update.message.reply_text(f"❌ {to_small_caps('only owner can use this command')}")
        return
    
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(f"{to_small_caps('usage')}: /setfrequencyall [number]")
        return
    
    frequency = int(context.args[0])
    
    if frequency < 10:
        await update.message.reply_text(f"❌ {to_small_caps('minimum frequency is 10 messages')}")
        return
    
    # Update all chats without custom frequency
    result = await user_totals_collection.update_many(
        {},
        {'$set': {'message_frequency': frequency}}
    )
    
    await update.message.reply_text(
        f"✅ {to_small_caps('global spawn frequency set to')} {frequency} {to_small_caps('messages')}\n"
        f"{to_small_caps('updated')} {result.modified_count} {to_small_caps('chats')}"
    )

# ========================
# HOT-LOAD COMMAND
# ========================
async def connect(update: Update, context: CallbackContext) -> None:
    """Hot-load a module (Owner only)"""
    user_id = update.effective_user.id
    
    if user_id != OWNER_ID:
        await update.message.reply_text(f"❌ {to_small_caps('only owner can use this command')}")
        return
    
    if not context.args:
        await update.message.reply_text(f"{to_small_caps('usage')}: /connect <module_name>")
        return
    
    module_name = context.args[0]
    modules_dir = Path(__file__).parent / "shivu" / "modules"
    module_path = modules_dir / f"{module_name}.py"
    
    if not module_path.exists():
        await update.message.reply_text(f"❌ {to_small_caps('module not found')}: {module_name}")
        return
    
    # Reload if already loaded
    if module_name in loaded_modules:
        try:
            importlib.reload(loaded_modules[module_name])
            await update.message.reply_text(f"🔄 {to_small_caps('reloaded module')}: {module_name}")
        except Exception as e:
            await update.message.reply_text(f"❌ {to_small_caps('reload failed')}: {e}")
        return
    
    # Load new module
    if load_module(str(module_path), module_name):
        await update.message.reply_text(f"✅ {to_small_caps('loaded module')}: {module_name}")
    else:
        await update.message.reply_text(f"❌ {to_small_caps('failed to load')}: {module_name}")

# ========================
# MAIN FUNCTION
# ========================
def main() -> None:
    """Initialize and run the bot"""
    LOGGER.info("🚀 Starting Future-Proof Modular Bot...")
    
    # Auto-load all modules
    auto_load_modules()
    
    # Register core handlers
    application.add_handler(CommandHandler(["guess", "protecc", "collect", "grab", "hunt"], guess, block=False))
    application.add_handler(CommandHandler("fav", fav, block=False))
    application.add_handler(CommandHandler("setfrequency", setfrequency, block=False))
    application.add_handler(CommandHandler("setfrequencyall", setfrequencyall, block=False))
    application.add_handler(CommandHandler("connect", connect, block=False))
    application.add_handler(MessageHandler(filters.ALL, message_counter, block=False))
    
    LOGGER.info("✅ All handlers registered")
    LOGGER.info(f"📊 Database: Connected to MongoDB")
    LOGGER.info(f"👑 Owner ID: {OWNER_ID}")
    
    # Run bot
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    # Gracefully start Pyrogram client
    try:
        if shivuu:
            shivuu.start()
            LOGGER.info("✅ Pyrogram client started successfully")
    except Exception as e:
        LOGGER.warning(f"⚠️ Pyrogram client failed to start: {e}")
        LOGGER.info("Continuing without Pyrogram client...")
    
    LOGGER.info("✅ Bot initialization complete")
    main()