# Telegram Bot Setup Guide

## Step 1: Create a Telegram Bot

1. Open Telegram and search for **@BotFather**.
2. Send `/newbot`.
3. Follow the prompts:
   - **Name**: `Instagram Monitor` (or anything you like)
   - **Username**: `your_ig_monitor_bot` (must end in `bot`)
4. BotFather will give you a **token** like: `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`
5. **Save this token** — it goes in your `.env` as `TELEGRAM_BOT_TOKEN`.

## Step 2: Get Your Chat ID

### Method 1: Using @userinfobot
1. Open Telegram and search for **@userinfobot**.
2. Send `/start`.
3. It will reply with your **User ID** (a number like `987654321`).
4. This is your `TELEGRAM_CHAT_ID`.

### Method 2: Using @RawDataBot
1. Search for **@RawDataBot** on Telegram.
2. Send any message.
3. It replies with JSON — find the `"id"` field under `"from"`.

### Method 3: Using a Group Chat
If you want notifications in a group:
1. Add your bot to the group.
2. Send a message in the group.
3. Visit: `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
4. Find the `"chat"` → `"id"` field (group IDs are negative numbers like `-1001234567890`).

## Step 3: Test Your Bot

```bash
# Replace with your actual token and chat ID
curl -X POST "https://api.telegram.org/bot<YOUR_TOKEN>/sendMessage" \
  -d "chat_id=<YOUR_CHAT_ID>" \
  -d "text=✅ Bot is working!" \
  -d "parse_mode=HTML"
```

You should receive the message in Telegram.

## Step 4: Update Your .env

```env
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
TELEGRAM_CHAT_ID=987654321
```

## Step 5: Verify from the App

```bash
python -c "
import asyncio
from app.config.settings import get_settings
from app.notifier.telegram import TelegramNotifier

async def test():
    settings = get_settings()
    notifier = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
    result = await notifier.send_alert('Test message from Instagram Monitor!')
    print('✅ Message sent!' if result else '❌ Failed to send')

asyncio.run(test())
"
```

## Bot Commands (Optional Enhancement)

You can set up bot commands via BotFather for future interactivity:
1. Send `/setcommands` to @BotFather.
2. Select your bot.
3. Send:
   ```
   status - Show monitoring status
   list - List monitored usernames
   add - Add a username to monitor
   remove - Remove a username
   ```
