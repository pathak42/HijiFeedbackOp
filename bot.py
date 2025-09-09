import os
import sqlite3
import logging
import asyncio
from datetime import datetime, timedelta, time as dt_time
from typing import Optional, List, Dict
import threading
import time
import io

from telegram import Update, Message
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.error import TelegramError
import flask
from flask import Flask

# Configure logging with file handler
log_filename = 'bot.log'
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler(log_filename),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Environment variables
BOT_TOKEN = os.getenv('BOT_TOKEN')
OWNER_ID = int(os.getenv('OWNER_ID', '0'))
REMINDER_INTERVAL = int(os.getenv('REMINDER_INTERVAL', '7200'))  # 2 hours in seconds
PORT = int(os.getenv('PORT', '8080'))

# Hardcoded admin usernames (always treated as admins)
HARDCODED_ADMINS = {"GroupAnonymousBot"}

# Database setup
DB_NAME = 'feedback_bot.db'

class FeedbackBot:
    def __init__(self):
        self.app = None
        self.authorized_groups = set()
        self.group_reminders = {}
        self.media_groups = {}  # Track media groups: {media_group_id: {'messages': [], 'has_feedback': False, 'user_id': int, 'group_id': int}}
        self.forwarding_group_id = None  # Will be loaded from database or env
        self.init_database()
        self.load_authorized_groups()
        self.load_bot_settings()
        self.load_env_config()
        
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
                message_id INTEGER,
                media_count INTEGER DEFAULT 1
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
        
        # Authorized users table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS authorized_users (
                user_id INTEGER PRIMARY KEY,
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
        
        # Daily feedback contest table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS daily_feedback_contest (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                display_name TEXT,
                group_id INTEGER NOT NULL,
                contest_date TEXT NOT NULL,
                feedback_count INTEGER DEFAULT 0,
                UNIQUE(user_id, group_id, contest_date)
            )
        ''')
        
        # Authorized users table (for manual authorization)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS authorized_users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                display_name TEXT,
                added_by INTEGER,
                added_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Bot settings table for persistent configuration
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS bot_settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
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
        
    def load_bot_settings(self):
        """Load bot settings from database"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('SELECT key, value FROM bot_settings')
        settings = cursor.fetchall()
        
        for key, value in settings:
            if key == 'forwarding_group_id' and value:
                self.forwarding_group_id = int(value)
        
        conn.close()
        
    def load_env_config(self):
        """Load configuration from environment variables"""
        # Load authorized groups from environment variable
        env_groups = os.getenv('AUTHORIZED_GROUPS', '')
        if env_groups:
            try:
                # Format: "group_id1:group_name1,group_id2:group_name2"
                for group_info in env_groups.split(','):
                    if ':' in group_info:
                        group_id_str, group_name = group_info.strip().split(':', 1)
                        group_id = int(group_id_str.strip())
                        group_name = group_name.strip()
                        
                        # Add to authorized groups if not already present
                        if group_id not in self.authorized_groups:
                            self.add_authorized_group(group_id, group_name)
                            logger.info(f"Added authorized group from env: {group_name} ({group_id})")
            except Exception as e:
                logger.error(f"Error loading authorized groups from environment: {e}")
        
        # Load forwarding group from environment variable
        env_forwarding_group = os.getenv('FORWARDING_GROUP_ID', '')
        if env_forwarding_group and not self.forwarding_group_id:
            try:
                self.forwarding_group_id = int(env_forwarding_group)
                # Save to database for persistence
                self.set_forwarding_group(self.forwarding_group_id)
                logger.info(f"Set forwarding group from env: {self.forwarding_group_id}")
            except Exception as e:
                logger.error(f"Error loading forwarding group from environment: {e}")
        
    def save_bot_setting(self, key: str, value: str):
        """Save a bot setting to database"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO bot_settings (key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
        ''', (key, value))
        conn.commit()
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
        
    def remove_authorized_group(self, group_id: int):
        """Remove a group from authorized groups"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM authorized_groups WHERE group_id = ?', (group_id,))
        deleted_count = cursor.rowcount
        conn.commit()
        conn.close()
        self.authorized_groups.discard(group_id)
        return deleted_count
        
    def is_group_authorized(self, group_id: int) -> bool:
        """Check if a group is authorized"""
        return group_id in self.authorized_groups
        
    def add_feedback(self, user_id: int, username: str, display_name: str, 
                    group_id: int, group_name: str, message_link: str, message_id: int, media_count: int = 1):
        """Add feedback to database"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO feedback (user_id, username, display_name, group_id, group_name, message_link, message_id, media_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, username, display_name, group_id, group_name, message_link, message_id, media_count))
        
        conn.commit()
        conn.close()
        
    def get_recent_feedback(self, group_id: int, days: int = 3) -> List[Dict]:
        """Get feedback from last N days for a group"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cutoff_date = datetime.now() - timedelta(days=days)
        
        cursor.execute('''
            SELECT user_id, username, display_name, message_link, timestamp, media_count
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
                'timestamp': row[4],
                'media_count': row[5]
            }
            for row in rows
        ]
        
    def get_user_feedback(self, user_id: int, group_id: int, days: int = 3) -> List[Dict]:
        """Get specific user's feedback from last N days in a group"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cutoff_date = datetime.now() - timedelta(days=days)
        
        cursor.execute('''
            SELECT message_link, timestamp, media_count
            FROM feedback
            WHERE user_id = ? AND group_id = ? AND timestamp >= ?
            ORDER BY timestamp DESC
        ''', (user_id, group_id, cutoff_date))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [{'message_link': row[0], 'timestamp': row[1], 'media_count': row[2]} for row in rows]
        
    def get_feedback_count_stats(self, group_id: int, days: int = 3) -> Dict:
        """Get feedback count statistics for a group"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cutoff_date = datetime.now() - timedelta(days=days)
        
        # Get unique users count
        cursor.execute('''
            SELECT COUNT(DISTINCT user_id) as unique_users
            FROM feedback
            WHERE group_id = ? AND timestamp >= ?
        ''', (group_id, cutoff_date))
        unique_users = cursor.fetchone()[0]
        
        # Get total feedback count (sum of media_count)
        cursor.execute('''
            SELECT COALESCE(SUM(media_count), 0) as total_feedback
            FROM feedback
            WHERE group_id = ? AND timestamp >= ?
        ''', (group_id, cutoff_date))
        total_feedback = cursor.fetchone()[0]
        
        conn.close()
        
        return {
            'unique_users': unique_users,
            'total_feedback': total_feedback
        }
        
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
        
    def get_contest_date(self, timestamp=None):
        """Get contest date based on custom day (2PM UTC to 1:59PM UTC next day)"""
        if timestamp is None:
            timestamp = datetime.now()
        
        # If time is before 2PM UTC, it belongs to previous contest day
        if timestamp.hour < 14:  # Before 2PM UTC
            contest_date = (timestamp - timedelta(days=1)).date()
        else:  # 2PM UTC or later
            contest_date = timestamp.date()
            
        return contest_date
        
    def add_contest_feedback(self, user_id: int, username: str, display_name: str, 
                           group_id: int, feedback_count: int = 1):
        """Add or update daily contest feedback count"""
        contest_date = self.get_contest_date()
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        # Insert or update feedback count
        cursor.execute('''
            INSERT INTO daily_feedback_contest 
            (user_id, username, display_name, group_id, contest_date, feedback_count)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, group_id, contest_date) 
            DO UPDATE SET 
                feedback_count = feedback_count + ?,
                username = ?,
                display_name = ?
        ''', (user_id, username, display_name, group_id, contest_date, feedback_count,
              feedback_count, username, display_name))
        
        conn.commit()
        conn.close()
        
    def get_daily_contest_winners(self, group_id: int, contest_date=None):
        """Get winner and runner-up for a specific contest date"""
        if contest_date is None:
            contest_date = self.get_contest_date()
            
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT user_id, username, display_name, feedback_count
            FROM daily_feedback_contest
            WHERE group_id = ? AND contest_date = ?
            ORDER BY feedback_count DESC, user_id ASC
            LIMIT 2
        ''', (group_id, contest_date))
        
        results = cursor.fetchall()
        conn.close()
        
        winner = None
        runner_up = None
        
        if len(results) >= 1:
            winner = {
                'user_id': results[0][0],
                'username': results[0][1],
                'display_name': results[0][2],
                'feedback_count': results[0][3]
            }
            
        if len(results) >= 2 and results[1][3] > 0:  # Runner-up must have at least 1 feedback
            runner_up = {
                'user_id': results[1][0],
                'username': results[1][1],
                'display_name': results[1][2],
                'feedback_count': results[1][3]
            }
            
        return winner, runner_up
        
    def add_authorized_user(self, user_id: int, username: str, display_name: str, added_by: int):
        """Add authorized user to database"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT OR REPLACE INTO authorized_users (user_id, username, display_name, added_by)
            VALUES (?, ?, ?, ?)
        ''', (user_id, username, display_name, added_by))
        
        conn.commit()
        conn.close()
        
    def is_user_authorized(self, user_id):
        """Check if user is manually authorized"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('SELECT 1 FROM authorized_users WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        conn.close()
        return result is not None
    
    def set_forwarding_group(self, group_id):
        """Set the group ID for feedback forwarding"""
        self.forwarding_group_id = group_id
        # Save to database for persistence
        self.save_bot_setting('forwarding_group_id', str(group_id))
    
    def get_forwarding_group(self):
        """Get the current forwarding group ID"""
        return self.forwarding_group_id
        
    def process_media_group(self, media_group_id: str, user_id: int, username: str, 
                          display_name: str, group_id: int):
        """Process completed media group and count all items if #feedback found"""
        if media_group_id not in self.media_groups:
            return 0
            
        media_group_data = self.media_groups[media_group_id]
        
        # If #feedback was found in any message of the group, count all messages
        if media_group_data['has_feedback']:
            media_count = len(media_group_data['messages'])
            
            # Add to contest with the total count
            self.add_contest_feedback(user_id, username, display_name, group_id, media_count)
            
            # Clean up the media group data
            del self.media_groups[media_group_id]
            
            return media_count
        else:
            # Clean up if no feedback found
            del self.media_groups[media_group_id]
            return 0

async def is_admin_or_owner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if user is owner, admin, anonymous admin, or manually authorized"""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    username = update.effective_user.username
    
    # Owner can always use commands
    if user_id == OWNER_ID:
        return True
    
    # Check if user is manually authorized
    if feedback_bot.is_user_authorized(user_id):
        return True
    
    # Check if user is in hardcoded admin list (always treated as admin)
    if username in HARDCODED_ADMINS:
        logger.info(f"Hardcoded admin detected: {username} - granting admin access")
        return True
    
    # Debug logging
    logger.info(f"Checking admin status for user_id: {user_id}, username: {username}, chat_id: {chat_id}")
    
    # Check if user is admin or anonymous admin
    try:
        chat_member = await context.bot.get_chat_member(chat_id, user_id)
        logger.info(f"Chat member status: {chat_member.status}, is_anonymous: {getattr(chat_member, 'is_anonymous', False)}")
        
        # Check if user is in hardcoded admin list via chat_member (double check)
        if hasattr(chat_member.user, 'username') and chat_member.user.username in HARDCODED_ADMINS:
            logger.info(f"Hardcoded admin confirmed via chat_member: {chat_member.user.username} - granting admin access")
            return True
        
        # Regular admin or creator
        if chat_member.status in ['administrator', 'creator']:
            return True
            
        # Check for anonymous admin
        if hasattr(chat_member, 'is_anonymous') and chat_member.is_anonymous:
            return True
            
        # Check if the sender ID matches the current group ID (anonymous admin pattern)
        if user_id == chat_id:
            return True
            
        return False
        
    except Exception as e:
        logger.error(f"Error checking admin status for user {user_id}: {e}")
        # If there's an error and it's a hardcoded admin, still allow
        if username in HARDCODED_ADMINS:
            logger.info(f"Error occurred but user is hardcoded admin: {username} - granting admin access")
            return True
        return False

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
    
    # If used in private chat, expect group ID as parameter
    if update.effective_chat.type == 'private':
        if not context.args:
            await update.message.reply_text("❌ Please provide group ID. Usage: /addgroup -1002373349798")
            return
            
        try:
            group_id = int(context.args[0])
            # Try to get group info
            try:
                chat = await context.bot.get_chat(group_id)
                group_name = chat.title or "Unknown Group"
                feedback_bot.add_authorized_group(group_id, group_name)
                await update.message.reply_text(f"✅ Group '{group_name}' (ID: {group_id}) has been authorized to use the feedback bot!")
            except Exception as e:
                await update.message.reply_text(f"❌ Could not access group {group_id}. Make sure the bot is added to the group and the ID is correct.")
                logger.error(f"Error accessing group {group_id}: {e}")
        except ValueError:
            await update.message.reply_text("❌ Invalid group ID format. Usage: /addgroup -1002373349798")
        return
        
    # If used in group chat (original functionality)
    group_id = update.effective_chat.id
    group_name = update.effective_chat.title or "Unknown Group"
    
    feedback_bot.add_authorized_group(group_id, group_name)
    await update.message.reply_text(f"✅ Group '{group_name}' has been authorized to use the feedback bot!")

async def removegroup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /removegroup command - Owner only"""
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Only the bot owner can use this command.")
        return
        
    if not context.args:
        await update.message.reply_text("❌ Please provide group ID. Usage: /removegroup -1002373349798")
        return
        
    try:
        group_id = int(context.args[0])
        # Try to get group info for confirmation
        try:
            chat = await context.bot.get_chat(group_id)
            group_name = chat.title or "Unknown Group"
        except Exception:
            group_name = f"Group {group_id}"
            
        deleted_count = feedback_bot.remove_authorized_group(group_id)
        
        if deleted_count > 0:
            await update.message.reply_text(f"✅ Group '{group_name}' (ID: {group_id}) has been removed from authorized groups!")
        else:
            await update.message.reply_text(f"❌ Group {group_id} was not found in authorized groups.")
            
    except ValueError:
        await update.message.reply_text("❌ Invalid group ID format. Usage: /removegroup -1002373349798")

async def addauth_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /addauth command - Owner only (authorize specific users)"""
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Only the bot owner can use this command.")
        return
        
    if not context.args:
        await update.message.reply_text("❌ Please provide user ID. Usage: /addauth 123456789")
        return
        
    try:
        user_id = int(context.args[0])
        
        # Try to get user info
        try:
            chat_member = await context.bot.get_chat_member(user_id, user_id)
            user = chat_member.user
            username = user.username
            display_name = user.full_name
        except Exception:
            username = None
            display_name = f"User {user_id}"
            
        feedback_bot.add_authorized_user(user_id, username, display_name, OWNER_ID)
        await update.message.reply_text(f"✅ User {display_name} (ID: {user_id}) has been authorized to use admin commands!")
        
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID format. Usage: /addauth 123456789")

async def logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /logs command - Owner only (send log file)"""
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Only the bot owner can use this command.")
        return
        
    if update.effective_chat.type != 'private':
        await update.message.reply_text("❌ This command can only be used in private chat.")
        return
        
    try:
        # Check if log file exists
        if not os.path.exists(log_filename):
            await update.message.reply_text("❌ Log file not found.")
            return
            
        # Get file size
        file_size = os.path.getsize(log_filename)
        
        # Telegram file size limit is 50MB
        if file_size > 50 * 1024 * 1024:
            await update.message.reply_text("❌ Log file is too large (>50MB). Please check server logs directly.")
            return
            
        # Send the log file
        with open(log_filename, 'rb') as log_file:
            await update.message.reply_document(
                document=log_file,
                filename=f"bot_logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
                caption="📋 Bot Log File"
            )
            
    except Exception as e:
        logger.error(f"Error sending log file: {e}")
        await update.message.reply_text(f"❌ Error sending log file: {str(e)}")

async def addplace_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /addplace command - Owner only (set feedback forwarding group) - DM only"""
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Only the bot owner can use this command.")
        return
        
    if update.effective_chat.type != 'private':
        await update.message.reply_text("❌ This command can only be used in private chat.")
        return
        
    if not context.args:
        await update.message.reply_text("❌ Please provide group ID. Usage: /addplace -1002373349798")
        return
        
    try:
        group_id = int(context.args[0])
        
        # Try to get group info to verify the bot has access
        try:
            chat = await context.bot.get_chat(group_id)
            group_name = chat.title or f"Group {group_id}"
            
            # Set the forwarding group
            feedback_bot.set_forwarding_group(group_id)
            await update.message.reply_text(f"✅ Feedback forwarding set to '{group_name}' (ID: {group_id})")
            
        except Exception as e:
            await update.message.reply_text(f"❌ Could not access group {group_id}. Make sure the bot is added to the group and the ID is correct.")
            logger.error(f"Error accessing forwarding group {group_id}: {e}")
            
    except ValueError:
        await update.message.reply_text("❌ Invalid group ID format. Usage: /addplace -1002373349798")

async def fb_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /fb_stats command - Admins only"""
    # Owner can use this in private chat with group ID parameter
    if update.effective_chat.type == 'private':
        if update.effective_user.id == OWNER_ID:
            if not context.args:
                await update.message.reply_text("❌ Please provide group ID. Usage: /fb_stats -1002373349798")
                return
            try:
                group_id = int(context.args[0])
            except ValueError:
                await update.message.reply_text("❌ Invalid group ID format.")
                return
        else:
            await update.message.reply_text("❌ This command can only be used in groups.")
            return
    else:
        # Group chat - check admin permissions (including anonymous admins)
        if not await is_admin_or_owner(update, context):
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
        
    # Check if user is admin or owner (including anonymous admins)
    if not await is_admin_or_owner(update, context):
        return
        
    group_id = update.effective_chat.id
    
    if not feedback_bot.is_group_authorized(group_id):
        return
    
    target_user = None
    
    # Check if it's a reply to a message
    if update.message.reply_to_message:
        target_user = update.message.reply_to_message.from_user
    # Check if there's a mention in the command
    elif update.message.entities:
        for entity in update.message.entities:
            if entity.type == "mention":
                # Extract username from @mention
                username = update.message.text[entity.offset+1:entity.offset+entity.length]
                try:
                    # Try to get user info by username
                    chat_member = await context.bot.get_chat_member(group_id, f"@{username}")
                    target_user = chat_member.user
                    break
                except Exception as e:
                    logger.error(f"Could not find user @{username}: {e}")
                    continue
            elif entity.type == "text_mention":
                target_user = entity.user
                break
    
    if not target_user:
        await update.message.reply_text("❌ Please reply to a user's message or mention a user with /check @username")
        return
        
    user_feedback = feedback_bot.get_user_feedback(target_user.id, group_id, 3)
    
    if not user_feedback:
        username = target_user.username or target_user.full_name or f"User {target_user.id}"
        await update.message.reply_text(f"❌ No feedback was received from {username} in the last 3 days")
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
    # Owner can use this in private chat with group ID parameter
    if update.effective_chat.type == 'private':
        if update.effective_user.id == OWNER_ID:
            if len(context.args) < 2:
                await update.message.reply_text("❌ Please provide group ID and reminder text. Usage: /addreminder -1002373349798 Your reminder text here")
                return
            try:
                group_id = int(context.args[0])
                reminder_text = ' '.join(context.args[1:])
            except ValueError:
                await update.message.reply_text("❌ Invalid group ID format. Usage: /addreminder -1002373349798 Your reminder text here")
                return
        else:
            await update.message.reply_text("❌ This command can only be used in groups.")
            return
    else:
        # Check if user is admin or owner (including anonymous admins)
        if not await is_admin_or_owner(update, context):
            return
        
        group_id = update.effective_chat.id
        
        if not context.args:
            await update.message.reply_text("❌ Please provide reminder text. Usage: /addreminder <text>")
            return
            
        reminder_text = ' '.join(context.args)
    
    if not feedback_bot.is_group_authorized(group_id):
        await update.message.reply_text("❌ This group is not authorized. Ask the owner to run /addgroup first.")
        return
        
    feedback_bot.set_reminder(group_id, reminder_text)
    
    # Get group name for confirmation
    try:
        chat = await context.bot.get_chat(group_id)
        group_name = chat.title or "Unknown Group"
        await update.message.reply_text(f"✅ Reminder set for '{group_name}'! It will be sent 8 times daily at: 1 AM, 4 AM, 7 AM, 10 AM, 1 PM, 4 PM, 7 PM, 10 PM UTC.")
    except Exception:
        await update.message.reply_text("✅ Reminder set! It will be sent 8 times daily at: 1 AM, 4 AM, 7 AM, 10 AM, 1 PM, 4 PM, 7 PM, 10 PM UTC.")

async def fbcount_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /fbcount command - Admins only"""
    if update.effective_chat.type == 'private':
        await update.message.reply_text("❌ This command can only be used in groups.")
        return
        
    # Check if user is admin or owner
    # Check if user is admin or owner (including anonymous admins)
    if not await is_admin_or_owner(update, context):
        return
        
    group_id = update.effective_chat.id
    
    if not feedback_bot.is_group_authorized(group_id):
        await update.message.reply_text("❌ This group is not authorized. Ask the owner to run /addgroup first.")
        return
        
    stats = feedback_bot.get_feedback_count_stats(group_id, 3)
    
    message = f"📊 **Feedback Count (Last 3 Days):**\n\n"
    message += f"👥 **Unique Members:** {stats['unique_users']}\n"
    message += f"📝 **Total Feedback:** {stats['total_feedback']}\n"
    
    await update.message.reply_text(message, parse_mode='Markdown')

async def fbcommands_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /fbcommands command - Admins only"""
    if update.effective_chat.type == 'private':
        await update.message.reply_text("❌ This command can only be used in groups.")
        return
        
    # Check if user is admin or owner (including anonymous admins)
    if not await is_admin_or_owner(update, context):
        return
    
    message = "🤖 **Bot Commands:**\n\n"
    message += "**For Everyone:**\n"
    message += "• `/start` - Welcome message\n\n"
    
    message += "**For Admins:**\n"
    message += "• `/fb_stats` - Show feedback from last 3 days\n"
    message += "• `/check @user` - Check user's feedback (reply or mention)\n"
    message += "• `/fbcount` - Show feedback statistics\n"
    message += "• `/fbcommands` - Show this commands list\n"
    message += "• `/addreminder <text>` - Set periodic reminders\n\n"
    
    message += "**For Owner:**\n"
    message += "• `/addgroup` - Authorize group (in group or DM with ID)\n"
    message += "• `/removegroup` - Remove group authorization (DM only)\n"
    message += "• `/addauth <user_id>` - Manually authorize user for admin commands\n"
    message += "• `/addplace <group_id>` - Set feedback forwarding group (DM only)\n"
    message += "• `/logs` - Download bot log file (DM only)\n"
    message += "• `/addreminder` - Set reminders (in group or DM with ID)\n"
    message += "• `/cleardb` - Clear all feedback data\n\n"
    
    message += "**Feedback Submission:**\n"
    message += "• Send `#feedback` with media (photo/video/document)\n"
    message += "• Reply to media with `#feedback`"
    
    await update.message.reply_text(message, parse_mode='Markdown')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle regular messages to detect feedback"""
    if update.effective_chat.type == 'private':
        return
        
    group_id = update.effective_chat.id
    
    if not feedback_bot.is_group_authorized(group_id):
        return
        
    message = update.message
    text = message.text or message.caption or ""
    
    # Check if message has media or is a reply to media
    has_media = bool(message.photo or message.video or message.document or message.animation)
    is_reply_to_media = False
    reply_from_same_user = False
    
    if message.reply_to_message:
        reply_msg = message.reply_to_message
        is_reply_to_media = bool(reply_msg.photo or reply_msg.video or reply_msg.document or reply_msg.animation)
        # Check if the person replying is the same as who sent the original media
        reply_from_same_user = (message.from_user.id == reply_msg.from_user.id)
        
    # Only process if:
    # 1. Message has media with #feedback, OR
    # 2. Reply to media with #feedback from the SAME user who sent the media, OR
    # 3. Message with media replying to anything (to handle media reply with #feedback)
    if not (has_media or (is_reply_to_media and reply_from_same_user)):
        return
        
    user = message.from_user
    username = user.username
    display_name = user.full_name
    group_name = update.effective_chat.title or "Unknown Group"
    
    # Handle media groups
    if message.media_group_id:
        media_group_id = message.media_group_id
        
        # Check if any message in the media group has #feedback
        has_feedback = '#feedback' in text.lower()
        
        # Initialize media group tracking if not exists
        if media_group_id not in feedback_bot.media_groups:
            feedback_bot.media_groups[media_group_id] = {
                'messages': [],
                'has_feedback': has_feedback,
                'user_id': user.id,
                'username': username or '',
                'display_name': display_name or f"User {user.id}",
                'group_id': group_id,
                'group_name': group_name,
                'media_group_id': media_group_id,  # Store for cleanup
                'processing_scheduled': False  # Flag to track if processing is already scheduled
            }
        else:
            # Update has_feedback if this message has the tag
            if has_feedback:
                feedback_bot.media_groups[media_group_id]['has_feedback'] = True
        
        # Add this message to the media group if not already added
        message_exists = any(msg.get('message_id') == message.message_id for msg in feedback_bot.media_groups[media_group_id]['messages'])
        if not message_exists:
            feedback_bot.media_groups[media_group_id]['messages'].append({
                'message_id': message.message_id,
                'text': text,
                'has_media': has_media
            })
        
        # Schedule processing of media group after a delay (to collect all messages)
        # Only schedule if not already scheduled and if we have feedback
        media_group = feedback_bot.media_groups[media_group_id]
        if not media_group.get('processing_scheduled', False) and media_group['has_feedback']:
            media_group['processing_scheduled'] = True
            context.job_queue.run_once(
                lambda ctx, mg_id=media_group_id: process_media_group_delayed(ctx, mg_id),
                when=2.0  # 2 seconds delay
            )
        elif not media_group['has_feedback']:
            # If no feedback tag found, clean up after a while
            async def cleanup_media_group(context, mg_id):
                if mg_id in feedback_bot.media_groups:
                    del feedback_bot.media_groups[mg_id]
            
            context.job_queue.run_once(
                lambda ctx, mg_id=media_group_id: cleanup_media_group(ctx, mg_id),
                when=10800.0  # 3 hours (10800 seconds) delay before cleanup
            )
        
    else:
        # Handle single media message or reply to media
        if '#feedback' in text.lower():
            # Handle reply to media (from same user only)
            if is_reply_to_media and reply_from_same_user:
                # Handle reply to media (possibly media group)
                reply_msg = message.reply_to_message
                
                if reply_msg.media_group_id:
                    # Reply to media group - need to count all items in that group
                    await handle_reply_to_media_group(update, context, reply_msg)
                else:
                    # Reply to single media
                    await handle_reply_to_single_media(update, context, reply_msg)
            
            elif has_media:
                # Direct media with #feedback (including media replies to text/other messages)
                # Create message link
                if update.effective_chat.username:
                    message_link = f"https://t.me/{update.effective_chat.username}/{message.message_id}"
                else:
                    message_link = f"https://t.me/c/{str(group_id)[4:]}/{message.message_id}"
                
                feedback_bot.add_feedback(
                    user.id, username, display_name, group_id, 
                    group_name, message_link, message.message_id, 1
                )
                
                # Add to daily contest (single item)
                feedback_bot.add_contest_feedback(
                    user.id, username, display_name, group_id, 1
                )
                
                member_name = display_name or username or f"User {user.id}"
                await update.message.reply_text(f"✅ Feedback received! Thank you Group,\nCheck ur feedbacks here https://t.me/+388LvrCZuK9kZmE9")
                logger.info(f"Feedback received from {username} ({user.id}) in group {group_id}")
                
                # Schedule feedback forwarding after 3-4 seconds
                # Forward the current message (which has the media), not the replied-to message
                context.job_queue.run_once(
                    lambda context: forward_feedback_delayed(context, message, user, group_name),
                    when=3.5  # 3.5 seconds delay
                )
                
                logger.info(f"Feedback logged from {username} in {group_name} (single media)")
                
                # Add to daily contest (reply to media)
                feedback_bot.add_contest_feedback(
                    user.id, username, display_name, group_id, 1
                )
                
                # Schedule feedback forwarding after 3-4 seconds
                context.job_queue.run_once(
                    lambda ctx: forward_feedback_delayed(ctx, reply_msg, user, group_name),
                    when=3.5  # 3.5 seconds delay
                )

async def handle_reply_to_single_media(update: Update, context: ContextTypes.DEFAULT_TYPE, reply_msg):
    """Handle #feedback reply to a single media message"""
    group_id = update.effective_chat.id
    user = update.message.from_user
    username = user.username
    display_name = user.full_name
    group_name = update.effective_chat.title or "Unknown Group"
    
    # Create message link to the original media
    if update.effective_chat.username:
        message_link = f"https://t.me/{update.effective_chat.username}/{reply_msg.message_id}"
    else:
        message_link = f"https://t.me/c/{str(group_id)[4:]}/{reply_msg.message_id}"
    
    feedback_bot.add_feedback(
        user.id, username, display_name, group_id, 
        group_name, message_link, reply_msg.message_id, 1
    )
    
    # Add to daily contest (single item)
    feedback_bot.add_contest_feedback(
        user.id, username, display_name, group_id, 1
    )
    
    # Send confirmation message
    member_name = display_name or username or f"User {user.id}"
    await update.message.reply_text(f"✅ Feedback received! Thank you Group,\nCheck ur feedbacks here https://t.me/+388LvrCZuK9kZmE9")
    
    logger.info(f"Feedback logged from {username} in {group_name} (reply to single media)")
    
    # Schedule feedback forwarding after 3-4 seconds
    context.job_queue.run_once(
        lambda ctx: forward_feedback_delayed(ctx, reply_msg, user, group_name),
        when=3.5  # 3.5 seconds delay
    )

async def handle_reply_to_media_group(update: Update, context: ContextTypes.DEFAULT_TYPE, reply_msg):
    """Handle #feedback reply to a media group - forward entire group and count all items"""
    try:
        # Get the media group ID from the replied message
        if not hasattr(reply_msg, 'media_group_id') or not reply_msg.media_group_id:
            logger.warning("Replied message is not part of a media group")
            return
            
        media_group_id = reply_msg.media_group_id
        group_id = update.effective_chat.id
        user = update.effective_user
        username = user.username or user.full_name or f"User {user.id}"
        group_name = update.effective_chat.title or "Unknown Group"
        
        # Get the media group data if it exists
        media_group_data = feedback_bot.media_groups.get(media_group_id)
        if not media_group_data:
            logger.warning(f"No media group data found for ID: {media_group_id}")
            # Try to find the media group by searching recent messages
            await find_and_process_media_group(update, context, media_group_id, reply_msg.message_id)
            return
            
        # Since this is a reply with #feedback, we should process the media group
        # The #feedback tag is in the reply message, not necessarily in the original media group
            
        # Count all media in the group
        media_count = len(media_group_data['messages'])
        
        # Add feedback for each message in the group
        # Use the original media sender's info (since only they can reply with feedback)
        for msg_data in media_group_data['messages']:
            try:
                if update.effective_chat.username:
                    message_link = f"https://t.me/{update.effective_chat.username}/{msg_data['message_id']}"
                else:
                    message_link = f"https://t.me/c/{str(group_id).replace('-100', '')}/{msg_data['message_id']}"
                
                feedback_bot.add_feedback(
                    media_group_data['user_id'],
                    media_group_data['username'],
                    media_group_data['display_name'],
                    group_id,
                    group_name,
                    message_link,
                    msg_data['message_id'],
                    1  # Each message counts as 1 feedback
                )
            except Exception as e:
                logger.error(f"Error adding feedback for message {msg_data['message_id']}: {e}")
        
        # Add to contest with full media count
        # Use the original media sender's info for contest tracking
        feedback_bot.add_contest_feedback(
            media_group_data['user_id'],
            media_group_data['username'],
            media_group_data['display_name'],
            group_id,
            media_count
        )
        
        # Mark that this media group has been processed for feedback
        media_group_data['has_feedback'] = True
        
        # Send confirmation message
        member_name = media_group_data['display_name'] or media_group_data['username'] or f"User {media_group_data['user_id']}"
        
        try:
            await update.message.reply_text(
                f"✅ Feedback received! Thank you Group,\nCheck ur feedbacks here https://t.me/+388LvrCZuK9kZmE9"
            )
        except Exception as e:
            logger.error(f"Failed to send confirmation for media group reply: {e}")
            
        logger.info(f"Media group feedback logged from {media_group_data['username']} in {group_name} (count: {media_count})")
        
        # Schedule feedback forwarding for media group after a short delay
        if media_count > 0:
            context.job_queue.run_once(
                lambda ctx, mgd=media_group_data, mc=media_count: forward_media_group_delayed(ctx, mgd, mc),
                when=3.5  # 3.5 seconds delay before forwarding
            )
    except Exception as e:
        logger.error(f"Error in handle_reply_to_media_group: {e}")
        try:
            await update.message.reply_text("❌ An error occurred while processing your feedback. Please try again.")
        except Exception as e2:
            logger.error(f"Failed to send error message: {e2}")

async def find_and_process_media_group(update: Update, context: ContextTypes.DEFAULT_TYPE, media_group_id: str, message_id: int):
    """Try to find and process a media group by creating a fallback entry"""
    try:
        # Since we can't reliably get the full media group after it's been cleaned up,
        # we'll create a fallback entry for the single message we're replying to
        chat_id = update.effective_chat.id
        reply_msg = update.message.reply_to_message
        
        if not reply_msg:
            logger.error("No reply message found")
            return
            
        # Get user info from the original media sender
        original_user = reply_msg.from_user
        
        # Create a minimal media group data entry for the single message
        media_group_data = {
            'messages': [{
                'message_id': reply_msg.message_id,
                'text': reply_msg.caption or '',
                'has_media': True
            }],
            'has_feedback': True,
            'user_id': original_user.id,
            'username': original_user.username or '',
            'display_name': original_user.full_name or f"User {original_user.id}",
            'group_id': chat_id,
            'group_name': update.effective_chat.title or "Unknown Group",
            'media_group_id': media_group_id,
            'processed': False
        }
        
        # Store the media group data
        feedback_bot.media_groups[media_group_id] = media_group_data
        
        # Get the replying user info
        replying_user = update.effective_user
        replying_username = replying_user.username or ''
        replying_display_name = replying_user.full_name or f"User {replying_user.id}"
        
        # Add feedback for the message
        if update.effective_chat.username:
            message_link = f"https://t.me/{update.effective_chat.username}/{reply_msg.message_id}"
        else:
            message_link = f"https://t.me/c/{str(chat_id).replace('-100', '')}/{reply_msg.message_id}"
        
        feedback_bot.add_feedback(
            original_user.id,
            original_user.username or '',
            original_user.full_name or f"User {original_user.id}",
            chat_id,
            update.effective_chat.title or "Unknown Group",
            message_link,
            reply_msg.message_id,
            1
        )
        
        # Add to contest
        feedback_bot.add_contest_feedback(
            original_user.id,
            original_user.username or '',
            original_user.full_name or f"User {original_user.id}",
            chat_id,
            1
        )
        
        # Send confirmation message
        try:
            await update.message.reply_text(
                f"✅ Feedback received! Thank you Group,\nCheck ur feedbacks here https://t.me/+388LvrCZuK9kZmE9"
            )
        except Exception as e:
            logger.error(f"Failed to send confirmation: {e}")
            
        logger.info(f"Fallback media group feedback logged from {original_user.username} in {update.effective_chat.title}")
        
        # Schedule forwarding of the single message
        context.job_queue.run_once(
            lambda ctx: forward_feedback_delayed(ctx, reply_msg, original_user, update.effective_chat.title or "Unknown Group"),
            when=3.5
        )
                
    except Exception as e:
        logger.error(f"Error in find_and_process_media_group: {e}")

async def process_media_group_delayed(context, media_group_id: str):
    """Process media group after delay to ensure all messages are collected"""
    # Skip if media group already processed or doesn't exist
    if media_group_id not in feedback_bot.media_groups:
        return
        
    media_group_data = feedback_bot.media_groups[media_group_id]
    
    # Skip if already processed
    if media_group_data.get('processed', False):
        return
    
    # Mark as processed to prevent duplicate processing
    media_group_data['processed'] = True
    
    # Get group info from media group data
    group_id = media_group_data['group_id']
    group_name = media_group_data['group_name']
    
    try:
        # Process the media group
        media_count = len(media_group_data['messages'])
        first_message = media_group_data['messages'][0] if media_count > 0 else None
        
        if not first_message:
            logger.warning(f"No messages found in media group {media_group_id}")
            return
        
        # Check if any message in the group has #feedback
        has_feedback = any('#feedback' in (msg.get('text', '') or '').lower() 
                          for msg in media_group_data['messages'])
        
        if not has_feedback and not media_group_data.get('has_feedback', False):
            logger.info(f"No #feedback tag found in media group {media_group_id}, ignoring")
            return
        
        # Add feedback for each message in the group
        for msg_data in media_group_data['messages']:
            try:
                feedback_bot.add_feedback(
                    media_group_data['user_id'],
                    media_group_data['username'],
                    media_group_data['display_name'],
                    group_id,
                    group_name,
                    f"https://t.me/c/{str(group_id).replace('-100', '')}/{msg_data['message_id']}",
                    msg_data['message_id'],
                    1  # Each message counts as 1 feedback
                )
            except Exception as e:
                logger.error(f"Error adding feedback for message {msg_data['message_id']} in group {media_group_id}: {e}")
        
        # Add to contest with full media count
        feedback_bot.add_contest_feedback(
            media_group_data['user_id'],
            media_group_data['username'],
            media_group_data['display_name'],
            group_id,
            media_count
        )
        
        # Send confirmation message
        member_name = media_group_data['display_name'] or media_group_data['username'] or f"User {media_group_data['user_id']}"
        
        try:
            await context.bot.send_message(
                chat_id=group_id,
                text=f"✅ Feedback received! Thank you Group,\nCheck ur feedbacks here https://t.me/+388LvrCZuK9kZmE9"
            )
        except Exception as e:
            logger.error(f"Failed to send confirmation for media group: {e}")
            
        logger.info(f"Media group feedback logged from {media_group_data['username']} in {media_group_data['group_name']} (count: {media_count})")
        
        # Schedule feedback forwarding for media group after a short delay
        if media_count > 0:
            context.job_queue.run_once(
                lambda ctx, mgd=media_group_data, mc=media_count: forward_media_group_delayed(ctx, mgd, mc),
                when=1.5  # 1.5 seconds delay before forwarding
            )
    except Exception as e:
        logger.error(f"Error processing media group {media_group_id}: {e}")
    finally:
        # Clean up media group data after processing
        if media_group_id in feedback_bot.media_groups:
            del feedback_bot.media_groups[media_group_id]

async def forward_feedback_delayed(context, message, user, group_name):
    """Forward single feedback to the designated group after delay"""
    forwarding_group_id = feedback_bot.get_forwarding_group()
    if not forwarding_group_id:
        return
        
    try:
        username = user.username or user.full_name or f"User {user.id}"
        
        # Forward the original message only
        await context.bot.forward_message(
            chat_id=forwarding_group_id,
            from_chat_id=message.chat_id,
            message_id=message.message_id
        )
        
        logger.info(f"Feedback forwarded to group {forwarding_group_id} from {username}")
        
    except Exception as e:
        logger.error(f"Error forwarding feedback: {e}")

async def forward_media_group_delayed(context, media_group_data, media_count):
    """Forward media group feedback to the designated group after delay"""
    forwarding_group_id = feedback_bot.get_forwarding_group()
    if not forwarding_group_id:
        return
    
    # Check if this media group has already been forwarded
    if media_group_data.get('forwarded', False):
        logger.info(f"Media group already forwarded, skipping")
        return
    
    # Mark as forwarded to prevent duplicate forwarding
    media_group_data['forwarded'] = True
    
    try:
        group_id = media_group_data['group_id']
        user_id = media_group_data['user_id']
        username = media_group_data['username']
        display_name = media_group_data['display_name']
        
        # Sort messages by message_id to maintain original order
        messages = sorted(media_group_data['messages'], key=lambda x: x['message_id'])
        
        # Forward each message in the media group
        for msg_data in messages:
            try:
                await context.bot.forward_message(
                    chat_id=forwarding_group_id,
                    from_chat_id=group_id,
                    message_id=msg_data['message_id']
                )
                # Small delay between forwards to avoid rate limiting
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"Error forwarding message {msg_data['message_id']} from media group: {e}")
        
        logger.info(f"Media group feedback forwarded to group {forwarding_group_id} from {username}")
        
        # Send a summary message
        try:
            member_name = display_name or username or f"User {user_id}"
            await context.bot.send_message(
                chat_id=forwarding_group_id,
                text=f"📨 Forwarded media group feedback from {member_name} ({media_count} items)"
            )
        except Exception as e:
            logger.error(f"Error sending summary message: {e}")
        
    except Exception as e:
        logger.error(f"Error in forward_media_group_delayed: {e}")
    finally:
        # Clean up the media group data after forwarding is complete
        if 'media_group_id' in media_group_data:
            media_group_id = media_group_data.get('media_group_id')
            if media_group_id and media_group_id in feedback_bot.media_groups:
                del feedback_bot.media_groups[media_group_id]

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

async def contest_announcement_job(context):
    """Daily contest winner announcement job"""
    try:
        # Get previous contest date (since we announce at 2:30PM for the day that ended at 2PM)
        current_time = datetime.now()
        if current_time.hour >= 14:  # After 2PM UTC
            contest_date = current_time.date()
        else:  # Before 2PM UTC
            contest_date = (current_time - timedelta(days=1)).date()
            
        # Announce winners for all authorized groups
        for group_id in feedback_bot.authorized_groups:
            try:
                winner, runner_up = feedback_bot.get_daily_contest_winners(group_id, contest_date)
                
                if winner and winner['feedback_count'] > 0:
                    message = "🏆 **Daily Feedback Contest Results** 🏆\n\n"
                    
                    # Winner
                    winner_name = winner['display_name'] or "Unknown"
                    winner_username = f"@{winner['username']}" if winner['username'] else ""
                    message += f"**Winner of the Feedback Contest**\n"
                    message += f"{winner_name} {winner_username} `{winner['user_id']}`\n"
                    message += f"Total feedbacks sent today: **{winner['feedback_count']}**\n\n"
                    
                    # Runner-up
                    if runner_up and runner_up['feedback_count'] > 0:
                        runner_name = runner_up['display_name'] or "Unknown"
                        runner_username = f"@{runner_up['username']}" if runner_up['username'] else ""
                        message += f"**Runner-up of the Feedback Contest**\n"
                        message += f"{runner_name} {runner_username} `{runner_up['user_id']}`\n"
                        message += f"Total feedbacks sent today: **{runner_up['feedback_count']}**\n\n"
                    
                    message += "🎉 Congratulations to our feedback champions!"
                    
                    await context.bot.send_message(
                        chat_id=group_id,
                        text=message,
                        parse_mode='Markdown'
                    )
                    
            except TelegramError as e:
                logger.error(f"Failed to send contest announcement to group {group_id}: {e}")
                
    except Exception as e:
        logger.error(f"Error in contest announcement job: {e}")

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
    application.add_handler(CommandHandler("removegroup", removegroup_command))
    application.add_handler(CommandHandler("addauth", addauth_command))
    application.add_handler(CommandHandler("addplace", addplace_command))
    application.add_handler(CommandHandler("logs", logs_command))
    application.add_handler(CommandHandler("fb_stats", fb_stats_command))
    application.add_handler(CommandHandler("check", check_user_feedback))
    application.add_handler(CommandHandler("cleardb", cleardb_command))
    application.add_handler(CommandHandler("addreminder", addreminder_command))
    application.add_handler(CommandHandler("fbcount", fbcount_command))
    application.add_handler(CommandHandler("fbcommands", fbcommands_command))
    application.add_handler(MessageHandler(filters.ALL, handle_message))
    
    # Add job queue for background tasks (if available)
    job_queue = application.job_queue
    
    if job_queue:
        # Schedule cleanup job to run daily at 12 AM UTC
        job_queue.run_daily(
            cleanup_job,
            time=dt_time(hour=0, minute=0, second=0)  # 12:00 AM UTC
        )
        
        # Schedule reminder jobs at specific times (UTC)
        reminder_times = [
            dt_time(hour=1, minute=0),   # 1 AM UTC
            dt_time(hour=4, minute=0),   # 4 AM UTC
            dt_time(hour=7, minute=0),   # 7 AM UTC
            dt_time(hour=10, minute=0),  # 10 AM UTC
            dt_time(hour=13, minute=0),  # 1 PM UTC
            dt_time(hour=16, minute=0),  # 4 PM UTC
            dt_time(hour=19, minute=0),  # 7 PM UTC
            dt_time(hour=22, minute=0),  # 10 PM UTC
        ]
        
        for reminder_time in reminder_times:
            job_queue.run_daily(
                reminder_job,
                time=reminder_time
            )
            
        # Schedule contest announcement at 2:30 PM UTC daily
        job_queue.run_daily(
            contest_announcement_job,
            time=dt_time(hour=14, minute=30, second=0)  # 2:30 PM UTC
        )
        
        logger.info("Background jobs scheduled successfully")
    else:
        logger.warning("JobQueue not available - background tasks disabled")
    
    # Start the bot
    logger.info("Starting Telegram Feedback Bot...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
