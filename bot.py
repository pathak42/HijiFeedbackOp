import os
import sqlite3
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Optional, List, Dict
import threading
import time

from telegram import Update, Message
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.error import TelegramError
import flask
from flask import Flask

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Environment variables
BOT_TOKEN = os.getenv('BOT_TOKEN')
OWNER_ID = int(os.getenv('OWNER_ID', '0'))
REMINDER_INTERVAL = int(os.getenv('REMINDER_INTERVAL', '7200'))  # 2 hours in seconds
PORT = int(os.getenv('PORT', '8080'))

# Database setup
DB_NAME = 'feedback_bot.db'

class FeedbackBot:
    def __init__(self):
        self.app = None
        self.authorized_groups = set()
        self.group_reminders = {}
        self.init_database()
        self.load_authorized_groups()
        
    def init_database(self):
        """Initialize SQLite database with required tables"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        # Feedback table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                display_name TEXT,
                group_id INTEGER NOT NULL,
                group_name TEXT,
                message_link TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                message_id INTEGER
            )
        ''')
        
        # Authorized groups table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS authorized_groups (
                group_id INTEGER PRIMARY KEY,
                group_name TEXT,
                added_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Reminders table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS reminders (
                group_id INTEGER PRIMARY KEY,
                reminder_text TEXT,
                added_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        conn.close()
        
    def load_authorized_groups(self):
        """Load authorized groups from database"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('SELECT group_id FROM authorized_groups')
        rows = cursor.fetchall()
        self.authorized_groups = {row[0] for row in rows}
        conn.close()
        
    def add_authorized_group(self, group_id: int, group_name: str):
        """Add a group to authorized groups"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute(
            'INSERT OR REPLACE INTO authorized_groups (group_id, group_name) VALUES (?, ?)',
            (group_id, group_name)
        )
        conn.commit()
        conn.close()
        self.authorized_groups.add(group_id)
        
    def is_group_authorized(self, group_id: int) -> bool:
        """Check if a group is authorized"""
        return group_id in self.authorized_groups
        
    def add_feedback(self, user_id: int, username: str, display_name: str, 
                    group_id: int, group_name: str, message_link: str, message_id: int):
        """Add feedback entry to database"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO feedback (user_id, username, display_name, group_id, 
                                group_name, message_link, message_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, username, display_name, group_id, group_name, message_link, message_id))
        conn.commit()
        conn.close()
        
    def get_recent_feedback(self, group_id: int, days: int = 3) -> List[Dict]:
        """Get feedback from last N days for a group"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cutoff_date = datetime.now() - timedelta(days=days)
        
        cursor.execute('''
            SELECT user_id, username, display_name, message_link, timestamp
            FROM feedback
            WHERE group_id = ? AND timestamp >= ?
            ORDER BY timestamp DESC
        ''', (group_id, cutoff_date))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [
            {
                'user_id': row[0],
                'username': row[1],
                'display_name': row[2],
                'message_link': row[3],
                'timestamp': row[4]
            }
            for row in rows
        ]
        
    def get_user_feedback(self, user_id: int, group_id: int, days: int = 3) -> List[Dict]:
        """Get specific user's feedback from last N days in a group"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cutoff_date = datetime.now() - timedelta(days=days)
        
        cursor.execute('''
            SELECT message_link, timestamp
            FROM feedback
            WHERE user_id = ? AND group_id = ? AND timestamp >= ?
            ORDER BY timestamp DESC
        ''', (user_id, group_id, cutoff_date))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [{'message_link': row[0], 'timestamp': row[1]} for row in rows]
        
    def cleanup_old_feedback(self):
        """Remove feedback older than 5 days"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cutoff_date = datetime.now() - timedelta(days=5)
        
        cursor.execute('DELETE FROM feedback WHERE timestamp < ?', (cutoff_date,))
        deleted_count = cursor.rowcount
        conn.commit()
        conn.close()
        
        logger.info(f"Cleaned up {deleted_count} old feedback entries")
        return deleted_count
        
    def clear_all_feedback(self):
        """Clear all feedback data"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM feedback')
        deleted_count = cursor.rowcount
        conn.commit()
        conn.close()
        
        logger.info(f"Cleared {deleted_count} feedback entries")
        return deleted_count
        
    def set_reminder(self, group_id: int, reminder_text: str):
        """Set reminder for a group"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute(
            'INSERT OR REPLACE INTO reminders (group_id, reminder_text) VALUES (?, ?)',
            (group_id, reminder_text)
        )
        conn.commit()
        conn.close()
        self.group_reminders[group_id] = reminder_text
        
    def get_reminder(self, group_id: int) -> Optional[str]:
        """Get reminder for a group"""
        if group_id in self.group_reminders:
            return self.group_reminders[group_id]
            
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('SELECT reminder_text FROM reminders WHERE group_id = ?', (group_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            self.group_reminders[group_id] = row[0]
            return row[0]
        return None

# Initialize bot instance
feedback_bot = FeedbackBot()

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    await update.message.reply_text("Welcome to the HIJI's Private Bot")

async def addgroup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /addgroup command - Owner only"""
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Only the bot owner can use this command.")
        return
        
    if update.effective_chat.type == 'private':
        await update.message.reply_text("❌ This command can only be used in groups.")
        return
        
    group_id = update.effective_chat.id
    group_name = update.effective_chat.title or "Unknown Group"
    
    feedback_bot.add_authorized_group(group_id, group_name)
    await update.message.reply_text(f"✅ Group '{group_name}' has been authorized to use the feedback bot!")

async def fb_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /fb_stats command"""
    if update.effective_chat.type == 'private':
        await update.message.reply_text("❌ This command can only be used in groups.")
        return
        
    group_id = update.effective_chat.id
    
    if not feedback_bot.is_group_authorized(group_id):
        await update.message.reply_text("❌ This group is not authorized. Ask the owner to run /addgroup first.")
        return
        
    feedback_list = feedback_bot.get_recent_feedback(group_id, 3)
    
    if not feedback_list:
        await update.message.reply_text("📊 No feedback received in the last 3 days.")
        return
        
    message = "📊 **Feedback Stats (Last 3 Days):**\n\n"
    
    for feedback in feedback_list:
        username = feedback['username'] or feedback['display_name'] or f"User {feedback['user_id']}"
        timestamp = datetime.fromisoformat(feedback['timestamp']).strftime("%Y-%m-%d %H:%M")
        message += f"👤 **{username}**\n"
        message += f"🕒 {timestamp}\n"
        message += f"🔗 [View Message]({feedback['message_link']})\n\n"
        
    await update.message.reply_text(message, parse_mode='Markdown', disable_web_page_preview=True)

async def check_user_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /check command - Check user feedback - Admins only"""
    if update.effective_chat.type == 'private':
        return
        
    if not update.message.reply_to_message:
        return
        
    # Check if user is admin
    chat_member = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
    if chat_member.status not in ['administrator', 'creator']:
        await update.message.reply_text("❌ Only group administrators can use this command.")
        return
        
    group_id = update.effective_chat.id
    
    if not feedback_bot.is_group_authorized(group_id):
        return
        
    target_user = update.message.reply_to_message.from_user
    user_feedback = feedback_bot.get_user_feedback(target_user.id, group_id, 3)
    
    if not user_feedback:
        await update.message.reply_text("❌ No feedback was received from him in the last 3 days")
        return
        
    username = target_user.username or target_user.full_name or f"User {target_user.id}"
    message = f"✅ **Feedback from {username} (Last 3 Days):**\n\n"
    
    for feedback in user_feedback:
        timestamp = datetime.fromisoformat(feedback['timestamp']).strftime("%Y-%m-%d %H:%M")
        message += f"🕒 {timestamp}\n"
        message += f"🔗 [View Message]({feedback['message_link']})\n\n"
        
    await update.message.reply_text(message, parse_mode='Markdown', disable_web_page_preview=True)

async def cleardb_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /cleardb command - Owner only"""
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Only the bot owner can use this command.")
        return
        
    deleted_count = feedback_bot.clear_all_feedback()
    await update.message.reply_text(f"🗑️ Cleared {deleted_count} feedback entries from the database.")

async def addreminder_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /addreminder command - Admins only"""
    if update.effective_chat.type == 'private':
        await update.message.reply_text("❌ This command can only be used in groups.")
        return
        
    # Check if user is admin
    chat_member = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
    if chat_member.status not in ['administrator', 'creator']:
        await update.message.reply_text("❌ Only group administrators can use this command.")
        return
        
    group_id = update.effective_chat.id
    
    if not feedback_bot.is_group_authorized(group_id):
        await update.message.reply_text("❌ This group is not authorized. Ask the owner to run /addgroup first.")
        return
        
    if not context.args:
        await update.message.reply_text("❌ Please provide reminder text. Usage: /addreminder <text>")
        return
        
    reminder_text = ' '.join(context.args)
    feedback_bot.set_reminder(group_id, reminder_text)
    
    await update.message.reply_text(f"✅ Reminder set! It will be sent every {REMINDER_INTERVAL//3600} hours.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle regular messages to detect feedback"""
    if update.effective_chat.type == 'private':
        return
        
    group_id = update.effective_chat.id
    
    if not feedback_bot.is_group_authorized(group_id):
        return
        
    message = update.message
    text = message.text or message.caption or ""
    
    # Check if message contains #feedback
    if '#feedback' not in text.lower():
        return
        
    # Check if message has media or is a reply to media with #feedback
    has_media = bool(message.photo or message.video or message.document or message.animation)
    is_reply_to_media = False
    
    if message.reply_to_message:
        reply_msg = message.reply_to_message
        is_reply_to_media = bool(reply_msg.photo or reply_msg.video or reply_msg.document or reply_msg.animation)
        
    if not (has_media or is_reply_to_media):
        return
        
    # Create message link
    if update.effective_chat.username:
        message_link = f"https://t.me/{update.effective_chat.username}/{message.message_id}"
    else:
        # For private groups, create a different format
        message_link = f"https://t.me/c/{str(group_id)[4:]}/{message.message_id}"
        
    user = message.from_user
    username = user.username
    display_name = user.full_name
    group_name = update.effective_chat.title or "Unknown Group"
    
    feedback_bot.add_feedback(
        user.id, username, display_name, group_id, 
        group_name, message_link, message.message_id
    )
    
    logger.info(f"Feedback logged from {username} in {group_name}")

def create_flask_app():
    """Create Flask app for keep-alive"""
    app = Flask(__name__)
    
    @app.route('/')
    def home():
        return "Telegram Feedback Bot is running!"
        
    @app.route('/health')
    def health():
        return {"status": "healthy", "timestamp": datetime.now().isoformat()}
        
    return app

def run_flask_app():
    """Run Flask app in a separate thread"""
    app = create_flask_app()
    app.run(host='0.0.0.0', port=PORT, debug=False)

async def cleanup_job(context):
    """Cleanup job for removing old feedback"""
    try:
        feedback_bot.cleanup_old_feedback()
    except Exception as e:
        logger.error(f"Error in cleanup job: {e}")

async def reminder_job(context):
    """Reminder job for sending periodic reminders"""
    try:
        # Send reminders to all groups that have them
        for group_id, reminder_text in feedback_bot.group_reminders.items():
            try:
                await context.bot.send_message(
                    chat_id=group_id,
                    text=f"🔔 **Reminder:** {reminder_text}",
                    parse_mode='Markdown'
                )
            except TelegramError as e:
                logger.error(f"Failed to send reminder to group {group_id}: {e}")
    except Exception as e:
        logger.error(f"Error in reminder job: {e}")

def main():
    """Main function to run the bot"""
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN environment variable is required!")
        return
        
    if OWNER_ID == 0:
        logger.error("OWNER_ID environment variable is required!")
        return
        
    # Start Flask app in background thread
    flask_thread = threading.Thread(target=run_flask_app, daemon=True)
    flask_thread.start()
    
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("addgroup", addgroup_command))
    application.add_handler(CommandHandler("fb_stats", fb_stats_command))
    application.add_handler(CommandHandler("check", check_user_feedback))
    application.add_handler(CommandHandler("cleardb", cleardb_command))
    application.add_handler(CommandHandler("addreminder", addreminder_command))
    application.add_handler(MessageHandler(filters.ALL, handle_message))
    
    # Add job queue for background tasks
    job_queue = application.job_queue
    
    # Schedule cleanup job to run daily
    job_queue.run_repeating(
        cleanup_job,
        interval=86400,  # 24 hours
        first=10  # Start after 10 seconds
    )
    
    # Schedule reminder job
    job_queue.run_repeating(
        reminder_job,
        interval=REMINDER_INTERVAL,
        first=30  # Start after 30 seconds
    )
    
    # Start the bot
    logger.info("Starting Telegram Feedback Bot...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
