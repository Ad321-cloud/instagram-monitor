"""CLI entry point for the Instagram monitoring system.

The primary interface is the Telegram bot. This CLI provides:
- `run` — Start the service (bot + monitor)
- `add/remove/list` — Quick admin commands
- `check` — One-time status check

For interactive control, use the Telegram bot commands:
/monitor, /demonitor, /status, /list, /check
"""

import argparse
import asyncio
import sys
from typing import Optional

from loguru import logger

from app.config.settings import get_settings
from app.database.engine import DatabaseManager
from app.database.repository import UsernameRepository
from app.utils.logging import setup_logging


async def _get_repo() -> tuple[UsernameRepository, DatabaseManager]:
    """Initialize database components.

    Returns:
        Tuple of (UsernameRepository, DatabaseManager).
    """
    settings = get_settings()
    db_manager = DatabaseManager(settings.database_url)
    await db_manager.init_db()
    repo = UsernameRepository(db_manager.session_factory)
    return repo, db_manager


async def cmd_add(username: str) -> None:
    """Add a username to the monitoring list."""
    repo, db = await _get_repo()
    try:
        existing = await repo.get_by_username(username)
        if existing:
            if existing.is_active:
                print(f"⚠️  '{username}' is already being monitored.")
            else:
                await repo.reactivate_username(username)
                print(f"✅ '{username}' reactivated for monitoring.")
        else:
            await repo.add_username(username)
            print(f"✅ '{username}' added to monitoring list.")
    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        await db.close()


async def cmd_remove(username: str) -> None:
    """Remove a username from monitoring."""
    repo, db = await _get_repo()
    try:
        if await repo.remove_username(username):
            print(f"✅ '{username}' removed from monitoring.")
        else:
            print(f"⚠️  '{username}' not found.")
    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        await db.close()


async def cmd_list() -> None:
    """List all monitored usernames."""
    repo, db = await _get_repo()
    try:
        usernames = await repo.get_all_usernames()
        if not usernames:
            print("📋 No usernames being monitored.")
            print("   Tip: Use the Telegram bot — /monitor username")
            return

        print(f"\n📋 Monitored Usernames ({len(usernames)}):\n")
        print(f"  {'Username':<20} {'Status':<15} {'Followers':<12} {'Active':<8} {'Last Checked'}")
        print(f"  {'─' * 20} {'─' * 15} {'─' * 12} {'─' * 8} {'─' * 22}")

        emoji_map = {"active": "🟢", "available": "🔵", "unavailable": "🔴"}
        for u in usernames:
            emoji = emoji_map.get(u.current_status, "⚪")
            active = "✅" if u.is_active else "❌"
            followers = str(u.follower_count) if u.follower_count else "N/A"
            checked = u.last_checked_at.strftime("%Y-%m-%d %H:%M") if u.last_checked_at else "Never"
            print(f"  {u.username:<20} {emoji} {u.current_status:<12} {followers:<12} {active:<8} {checked}")

            if u.disabled_at:
                print(f"     🔴 Disabled: {u.disabled_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
            if u.returned_at:
                print(f"     🟢 Returned: {u.returned_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        print()
    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        await db.close()


async def cmd_check(username: Optional[str] = None) -> None:
    """Run a one-time check."""
    from app.checker.instagram import InstagramChecker

    settings = get_settings()
    repo, db = await _get_repo()
    checker = InstagramChecker(check_delay=settings.check_delay_seconds)

    try:
        if username:
            result = await checker.check_username(username)
            emoji = {"active": "🟢", "available": "🔵", "unavailable": "🔴"}.get(result.status, "⚪")
            print(f"\n{emoji} @{result.username}: {result.status.upper()}")
            if result.follower_count:
                print(f"   Followers: {result.follower_count:,}")
            if result.http_status_code:
                print(f"   HTTP: {result.http_status_code} | Response: {result.response_time_ms:.0f}ms")
            if result.error:
                print(f"   Error: {result.error}")
            print()
        else:
            usernames = await repo.get_all_active()
            if not usernames:
                print("📋 No active usernames to check.")
                return
            print(f"🔄 Checking {len(usernames)} usernames...")
            results = await checker.check_many([u.username for u in usernames])
            for r in results:
                emoji = {"active": "🟢", "available": "🔵", "unavailable": "🔴"}.get(r.status, "⚪")
                followers = f" | 👥 {r.follower_count:,}" if r.follower_count else ""
                print(f"  {emoji} @{r.username}: {r.status.upper()}{followers}")
            print("✅ Done.")
    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        await checker.close()
        await db.close()


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="igmonitor",
        description="Instagram Username Monitoring System\n\nPrimary interface: Telegram bot (/monitor, /demonitor, /status)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    subparsers.add_parser("run", help="Start the monitoring service (bot + scheduler)")

    add_p = subparsers.add_parser("add", help="Add a username to monitor")
    add_p.add_argument("username", help="Instagram username")

    rem_p = subparsers.add_parser("remove", help="Remove a username")
    rem_p.add_argument("username", help="Instagram username")

    subparsers.add_parser("list", help="List all monitored usernames")

    chk_p = subparsers.add_parser("check", help="Run a one-time check")
    chk_p.add_argument("username", nargs="?", help="Username (or all)")

    return parser


def main() -> None:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    try:
        settings = get_settings()
        setup_logging(settings)
    except Exception as e:
        print(f"❌ Configuration error: {e}")
        print("   Make sure you have a .env file. See .env.example")
        sys.exit(1)

    if args.command == "run":
        from app.main import run_service
        asyncio.run(run_service())
    elif args.command == "add":
        asyncio.run(cmd_add(args.username))
    elif args.command == "remove":
        asyncio.run(cmd_remove(args.username))
    elif args.command == "list":
        asyncio.run(cmd_list())
    elif args.command == "check":
        asyncio.run(cmd_check(getattr(args, "username", None)))


if __name__ == "__main__":
    main()
