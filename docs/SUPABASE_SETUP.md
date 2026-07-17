# Supabase Setup Guide

## Step 1: Create a Supabase Account & Project

1. Go to [https://supabase.com](https://supabase.com) and sign up (free).
2. Click **"New Project"**.
3. Fill in:
   - **Organization**: Select or create one.
   - **Name**: `instagram-monitor`
   - **Database Password**: Generate a strong password and **save it** — you'll need it for `.env`.
   - **Region**: Choose the closest to your Oracle Cloud server.
4. Click **"Create new project"** and wait ~2 minutes for provisioning.

## Step 2: Get Your Database Credentials

1. Go to **Project Settings** → **Database**.
2. Under **Connection string**, select **URI** and note:
   - **Host**: `db.xxxxxxxxxxxx.supabase.co`
   - **Port**: `5432` (default, use `6543` for connection pooling via pgBouncer)
   - **Database name**: `postgres`
   - **User**: `postgres`
   - **Password**: The password you set in Step 1

3. Copy these into your `.env` file:
   ```env
   DB_HOST=db.xxxxxxxxxxxx.supabase.co
   DB_PORT=5432
   DB_NAME=postgres
   DB_USER=postgres
   DB_PASSWORD=your-database-password
   ```

## Step 3: Configure SSL (Important for Production)

Supabase requires SSL for external connections. The connection string should include SSL mode. Our `asyncpg` driver handles this automatically with Supabase's default config.

If you hit SSL errors, you can set the full URL with SSL parameters:
```env
DB_URL=postgresql+asyncpg://postgres:your-password@db.xxxx.supabase.co:5432/postgres?ssl=require
```

## Step 4: Verify Connection

After setting up your `.env` file, test the connection:
```bash
python -c "
import asyncio
from app.config.settings import get_settings
from app.database.engine import DatabaseManager

async def test():
    settings = get_settings()
    db = DatabaseManager(settings.database_url)
    await db.init_db()
    print('✅ Database connected and tables created!')
    await db.close()

asyncio.run(test())
"
```

## Step 5: Verify Tables in Supabase Dashboard

1. Go to **Table Editor** in your Supabase dashboard.
2. You should see two tables:
   - `monitored_usernames`
   - `status_history`
3. Click on each to verify the columns match the ORM models.

## Connection Pooling (Optional, Recommended for Production)

For production use with multiple connections, use Supabase's built-in pgBouncer:

1. Go to **Project Settings** → **Database** → **Connection Pooling**.
2. Enable connection pooling.
3. Use port `6543` instead of `5432` in your `.env`:
   ```env
   DB_PORT=6543
   ```

## Free Tier Limits

Supabase free tier includes:
- **500 MB** database storage
- **2 GB** bandwidth per month
- **2 active projects**
- **50,000** monthly active users (not relevant for us)

For our use case (monitoring usernames), even 100 usernames checked every 5 minutes for a year would use < 50 MB. The free tier is more than sufficient.
