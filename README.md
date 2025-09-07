# Telegram Feedback Bot

A powerful Telegram bot designed to collect and manage feedback from users in groups. The bot tracks feedback messages tagged with `#feedback` and provides comprehensive statistics and management features.

## 🚀 Features

### Core Functionality
- **Feedback Collection**: Automatically tracks messages with `#feedback` hashtag that include media or replies to media
- **Database Storage**: Stores feedback with user details, timestamps, and message links
- **Statistics**: View feedback stats for the last 3 days
- **User Checking**: Check if specific users have submitted feedback
- **Auto Cleanup**: Automatically removes feedback older than 5 days
- **Reminders**: Set periodic reminders for group members
- **Anonymous Admin Support**: Full support for anonymous admins (GroupAnonymousBot)
- **Manual Authorization**: Owner can manually authorize users via `/addauth`
- **Feedback Forwarding**: Automatically forwards all feedback to a designated group
- **Daily Contest**: Tracks daily feedback contest with winner announcements

### Bot Commands

| Command | Permission | Description |
|---------|------------|-------------|
| `/start` | Everyone | Welcome message |
| `/addgroup [group_id]` | Owner Only | Authorize a group (in group or DM with ID) |
| `/removegroup <group_id>` | Owner Only | Remove group authorization (DM only) |
| `/addauth <user_id>` | Owner Only | Manually authorize a user to use admin commands |
| `/addplace <group_id>` | Owner Only | Set feedback forwarding group (DM only) |
| `/logs` | Owner Only | Download bot log file (DM only) |
| `/fb_stats [group_id]` | Admins Only / Owner | Show feedback statistics for last 3 days |
| `/check @user` | Admins Only | Check user's feedback (reply or mention) |
| `/fbcount` | Admins Only | Show feedback count statistics |
| `/fbcommands` | Admins Only | Show all available commands |
| `/cleardb` | Owner Only | Clear all feedback data |
| `/addreminder <text>` | Admins Only / Owner | Set periodic reminder message |

## 🛠️ Setup & Deployment

### Prerequisites
- Telegram Bot Token (from [@BotFather](https://t.me/botfather))
- Your Telegram User ID (Owner ID)
- Render account for deployment
- UptimeRobot account (optional, for keep-alive)

### Environment Variables
Set these in your Render dashboard:

```env
BOT_TOKEN=your_telegram_bot_token
OWNER_ID=your_telegram_user_id
REMINDER_INTERVAL=7200  # 2 hours in seconds (optional)
PORT=8080  # Port for Flask server (optional)
```

### Deploy to Render

1. **Fork/Clone this repository**
2. **Connect to Render**:
   - Go to [Render Dashboard](https://dashboard.render.com/)
   - Click "New" → "Web Service"
   - Connect your GitHub repository
3. **Configure Service**:
   - Name: `telegram-feedback-bot`
   - Environment: `Docker`
   - Plan: `Free`
   - Auto-Deploy: `Yes`
4. **Set Environment Variables**:
   - Add `BOT_TOKEN` and `OWNER_ID`
   - Other variables are optional
5. **Deploy**: Click "Create Web Service"

### UptimeRobot Setup (Prevent Sleeping)

1. **Sign up** at [UptimeRobot](https://uptimerobot.com/)
2. **Create Monitor**:
   - Monitor Type: `HTTP(s)`
   - Friendly Name: `Telegram Feedback Bot`
   - URL: `https://your-render-app.onrender.com/health`
   - Monitoring Interval: `5 minutes`
3. **Save Monitor**

This will ping your bot every 5 minutes to prevent Render's free tier from putting it to sleep.

## 📖 How to Use

### 1. Setup Bot in Group
1. Add bot to your Telegram group
2. Run `/addgroup` command (owner only)
3. Bot is now ready to track feedback

### 2. Submit Feedback
Users can submit feedback in two ways:
- **Direct**: Send message with `#feedback` and attach media (photo/video/document)
- **Reply**: Reply to any media message with `#feedback`

### 3. Check Statistics
- Run `/fb_stats` to see all feedback from last 3 days
- Reply to messages with `/check` to check user feedback history

### 4. Set Reminders
- Admins can use `/addreminder <text>` to set periodic reminders
- Reminders are sent 8 times daily at: 1 AM, 4 AM, 7 AM, 10 AM, 1 PM, 4 PM, 7 PM, 10 PM UTC

## 🏗️ Project Structure

```
telegram-feedback-bot/
├── bot.py              # Main bot application
├── Dockerfile          # Docker configuration
├── render.yaml         # Render deployment config
├── requirements.txt    # Python dependencies
└── README.md          # This file
```

## 🔧 Technical Details

### Database Schema
The bot uses SQLite with five main tables:
- **feedback**: Stores all feedback entries with media count
- **authorized_groups**: Groups allowed to use the bot
- **authorized_users**: Manually authorized users for admin commands
- **reminders**: Reminder messages for groups
- **daily_contest**: Daily feedback contest tracking

### Keep-Alive Mechanism
- Flask server runs on port 8080 with health endpoint
- UptimeRobot pings `/health` every 5 minutes
- Prevents Render free tier from sleeping

### Background Tasks
- **Cleanup Task**: Runs every 24 hours to remove old feedback
- **Reminder Task**: Sends reminders every 2 hours (configurable)
- **Contest Announcement**: Daily winner announcements at 2:30 PM UTC
- **Feedback Forwarding**: Automatic forwarding with 3-4 second delay

## 🚨 Important Notes

### Security
- Only the bot owner can authorize groups (`/addgroup`)
- Only the bot owner can manually authorize users (`/addauth`)
- Only the bot owner can set feedback forwarding (`/addplace`)
- Only the bot owner can clear database (`/cleardb`)
- Only group admins and authorized users can use admin commands
- Anonymous admins (GroupAnonymousBot) are automatically recognized
- Bot only works in authorized groups

### Limitations
- Render free tier has 750 hours/month limit
- UptimeRobot helps but doesn't guarantee 100% uptime
- SQLite database resets on each deployment (use persistent disk)

### Message Links
- Public groups: `https://t.me/groupname/messageid`
- Private groups: `https://t.me/c/groupid/messageid`

## 🐛 Troubleshooting

### Bot Not Responding
1. Check if group is authorized with `/addgroup`
2. Verify bot has necessary permissions in group
3. Check Render logs for errors

### Feedback Not Being Tracked
1. Ensure message contains `#feedback` hashtag
2. Message must include media OR be a reply to media
3. Group must be authorized

### Reminders Not Working
1. Check if reminder is set with `/addreminder`
2. Verify `REMINDER_INTERVAL` environment variable
3. Ensure bot has send message permissions

## 📝 License

This project is open source and available under the [MIT License](LICENSE).

## 🤝 Contributing

1. Fork the repository
2. Create your feature branch
3. Commit your changes
4. Push to the branch
5. Create a Pull Request

## 📞 Support

If you encounter any issues:
1. Check the troubleshooting section
2. Review Render deployment logs
3. Ensure all environment variables are set correctly
4. Verify bot permissions in Telegram groups

---

**Made with ❤️ for better feedback management in Telegram groups**
