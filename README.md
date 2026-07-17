# Instagram Username Monitor 🔍

A production-grade monitoring system that tracks Instagram username availability and sends instant Telegram notifications when states change.

## Features

- 🔄 **Continuous Monitoring** — Checks usernames on configurable intervals (24/7)
- 📱 **Telegram Notifications** — Instant alerts when a username becomes available, goes active, or changes state
- 🗄️ **Supabase (PostgreSQL)** — Full status history with state change tracking
- ⚡ **Async Everything** — Built on `asyncio` + `aiohttp` for non-blocking concurrent checks
- 🛡️ **Production Hardened** — Retry logic, exponential backoff, graceful shutdown, structured logging
- 🎯 **Rate Limit Aware** — User-Agent rotation, jittered delays, configurable concurrency

## Quick Start

### 1. Clone & Install

```bash
git clone <your-repo-url>
cd instagram-monitor

python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env with your Supabase and Telegram credentials
```

See [Supabase Setup](docs/SUPABASE_SETUP.md) and [Telegram Setup](docs/TELEGRAM_SETUP.md) for detailed guides.

### 3. Add Usernames & Run

```bash
# Add usernames to monitor
python -m app.cli add target_username
python -m app.cli add another_username

# Run a one-time check
python -m app.cli check

# Start continuous monitoring
python -m app.cli run
```

## CLI Commands

| Command | Description |
|---------|-------------|
| `python -m app.cli add <username>` | Add a username to monitor |
| `python -m app.cli remove <username>` | Stop monitoring a username |
| `python -m app.cli list` | List all monitored usernames |
| `python -m app.cli history <username>` | Show status change history |
| `python -m app.cli check [username]` | Run a one-time check |
| `python -m app.cli run` | Start continuous monitoring |

## Architecture

```
┌─────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   CLI /      │────▶│  Monitor         │────▶│  Instagram      │
│   Main       │     │  Scheduler       │     │  Checker        │
└─────────────┘     └──────────────────┘     └─────────────────┘
                           │                         │
                           ▼                         ▼
                    ┌──────────────────┐     ┌─────────────────┐
                    │  Supabase        │     │  Telegram       │
                    │  (PostgreSQL)    │     │  Notifier       │
                    └──────────────────┘     └─────────────────┘
```

## Status Detection

| HTTP Code | Status | Meaning |
|-----------|--------|---------|
| 200 | 🟢 Active | Profile exists and is public |
| 404 | 🔵 Available | Username is not taken |
| 301/302 | 🔴 Unavailable | Private, suspended, or blocked |
| 429 | ⚪ Unknown | Rate limited |

## Project Structure

```
instagram-monitor/
├── app/
│   ├── checker/          # Instagram HTTP checker
│   │   └── instagram.py
│   ├── config/           # Pydantic Settings
│   │   └── settings.py
│   ├── database/         # SQLAlchemy async engine & repository
│   │   ├── engine.py
│   │   └── repository.py
│   ├── models/           # ORM models
│   │   └── username.py
│   ├── monitor/          # Scheduling & orchestration
│   │   └── scheduler.py
│   ├── notifier/         # Telegram notifications
│   │   └── telegram.py
│   ├── utils/            # Logging
│   │   └── logging.py
│   ├── cli.py            # CLI commands
│   └── main.py           # Entry point
├── tests/                # Test suite
├── docs/                 # Setup guides
├── requirements.txt
├── pyproject.toml
└── .env.example
```

## Deployment

See [Deployment Guide](docs/DEPLOYMENT.md) for full Oracle Cloud + systemd setup.

## Running Tests

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

## License

MIT
