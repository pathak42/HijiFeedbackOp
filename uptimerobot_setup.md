# UptimeRobot Setup Guide

This guide will help you set up UptimeRobot to keep your Telegram bot alive on Render's free tier.

## Why UptimeRobot?

Render's free tier puts services to sleep after 15 minutes of inactivity. UptimeRobot prevents this by pinging your bot regularly.

## Step-by-Step Setup

### 1. Create UptimeRobot Account
- Go to [UptimeRobot.com](https://uptimerobot.com/)
- Click "Sign Up" and create a free account
- Verify your email address

### 2. Get Your Bot's URL
After deploying to Render, you'll get a URL like:
```
https://your-app-name.onrender.com
```

### 3. Create Monitor
1. **Login** to UptimeRobot dashboard
2. **Click** "Add New Monitor"
3. **Configure Monitor**:
   - **Monitor Type**: HTTP(s)
   - **Friendly Name**: `Telegram Feedback Bot`
   - **URL**: `https://your-app-name.onrender.com/health`
   - **Monitoring Interval**: `5 minutes`
   - **Monitor Timeout**: `30 seconds`
4. **Click** "Create Monitor"

### 4. Verify Setup
- Monitor should show "Up" status within a few minutes
- Check your bot is responding to Telegram commands
- Monitor will ping every 5 minutes to keep bot awake

## Monitor Settings Explained

| Setting | Value | Why |
|---------|-------|-----|
| Monitor Type | HTTP(s) | Checks web endpoint |
| URL | `/health` endpoint | Returns bot status |
| Interval | 5 minutes | Frequent enough to prevent sleep |
| Timeout | 30 seconds | Reasonable response time |

## Troubleshooting

### Monitor Shows "Down"
- Check if your Render app is deployed successfully
- Verify the URL is correct
- Ensure `/health` endpoint is working

### Bot Still Going to Sleep
- Confirm monitor is actively pinging
- Check Render logs for any errors
- Verify Flask server is running on correct port

### Free Tier Limitations
- UptimeRobot free: 50 monitors, 5-minute intervals
- Render free: 750 hours/month (about 31 days)
- Bot may still sleep occasionally due to Render limits

## Alternative Keep-Alive Methods

### 1. Cron Jobs
Use services like:
- [cron-job.org](https://cron-job.org/)
- [EasyCron](https://www.easycron.com/)

### 2. GitHub Actions
Create a workflow to ping your bot periodically:

```yaml
name: Keep Bot Alive
on:
  schedule:
    - cron: '*/5 * * * *'  # Every 5 minutes
jobs:
  ping:
    runs-on: ubuntu-latest
    steps:
      - name: Ping Bot
        run: curl https://your-app-name.onrender.com/health
```

### 3. Other Monitoring Services
- [Pingdom](https://www.pingdom.com/)
- [StatusCake](https://www.statuscake.com/)
- [Freshping](https://www.freshworks.com/website-monitoring/)

## Best Practices

1. **Monitor Multiple Endpoints**: Add both `/` and `/health`
2. **Set Alerts**: Get notified when bot goes down
3. **Regular Checks**: Manually verify bot functionality
4. **Backup Plan**: Have alternative hosting ready

## Cost Considerations

### Free Tier Limits
- **UptimeRobot**: 50 monitors, 5-minute checks
- **Render**: 750 hours/month
- **Combination**: Should keep bot running ~31 days/month

### Paid Upgrades
If you need 100% uptime:
- **Render Starter**: $7/month for always-on service
- **UptimeRobot Pro**: $5/month for 1-minute checks

---

**Note**: This setup provides good uptime for a free solution but isn't guaranteed to be 100% reliable. For production use, consider paid hosting.
