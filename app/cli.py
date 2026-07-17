"""CLI interface for managing the Instagram monitoring system.

Design Decisions:
    - argparse over click/typer — zero dependencies, sufficient for this use case.
    - Each command is an async function — keeps the CLI non-blocking.
    - The CLI initializes its own database connection per invocation — no long-lived
      connections for one-off commands.
    - asyncio.run() as the entry point — clean event loop lifecycle.
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
    """Initialize database components and return the repository.

    Returns:
        Tuple of (UsernameRepository, DatabaseManager) for cleanup.
    """
    settings = get_settings()
    db_manager = DatabaseManager(settings.database_url)
    await db_manager.init_db()
    repo = UsernameRepository(db_manager.session_factory)
    return repo, db_manager


async def cmd_add(username: str) -> None:
    """Add a username to the monitoring list.

    Args:
        username: Instagram username to monitor.
    """
    repo, db = await _get_repo()
    try:
        existing = await repo.get_by_username(username)
        if existing:
            if existing.is_active:
                print(f"⚠️  Username '{username}' is already being monitored.")
            else:
                # Reactivate a previously removed username
                from sqlalchemy.ext.asyncio import AsyncSession
                async with db.session_factory() as session:
                    from sqlalchemy import select
                    from app.models.username import MonitoredUsername
                    stmt = select(MonitoredUsername).where(
                        MonitoredUsername.username == username.lower().strip()
                    )
                    result = await session.execute(stmt)
                    record = result.scalar_one()
                    record.is_active = True
                    await session.commit()
                print(f"✅ Username '{username}' reactivated for monitoring.")
        else:
            await repo.add_username(username)
            print(f"✅ Username '{username}' added to monitoring list.")
    except Exception as e:
        print(f"❌ Error adding '{username}': {e}")
        logger.error("CLI add_username failed: {}", e)
    finally:
        await db.close()


async def cmd_remove(username: str) -> None:
    """Remove a username from the monitoring list.

    Args:
        username: Instagram username to deactivate.
    """
    repo, db = await _get_repo()
    try:
        removed = await repo.remove_username(username)
        if removed:
            print(f"✅ Username '{username}' removed from monitoring.")
        else:
            print(f"⚠️  Username '{username}' not found.")
    except Exception as e:
        print(f"❌ Error removing '{username}': {e}")
        logger.error("CLI remove_username failed: {}", e)
    finally:
        await db.close()


async def cmd_list() -> None:
    """List all monitored usernames."""
    repo, db = await _get_repo()
    try:
        usernames = await repo.get_all_usernames()
        if not usernames:
            print("📋 No usernames are being monitored.")
            return

        print(f"\n📋 Monitored Usernames ({len(usernames)}):\n")
        print(f"  {'Username':<20} {'Status':<15} {'Active':<10} {'Last Checked'}")
        print(f"  {'─' * 20} {'─' * 15} {'─' * 10} {'─' * 25}")

        for u in usernames:
            status_emoji = {"active": "🟢", "available": "🔵", "unavailable": "🔴"}.get(
                u.current_status, "⚪"
            )
            active_str = "✅" if u.is_active else "❌"
            last_checked = (
                u.last_checked_at.strftime("%Y-%m-%d %H:%M:%S")
                if u.last_checked_at
                else "Never"
            )
            print(
                f"  {u.username:<20} {status_emoji} {u.current_status:<12} {active_str:<10} {last_checked}"
            )
        print()
    except Exception as e:
        print(f"❌ Error listing usernames: {e}")
        logger.error("CLI list failed: {}", e)
    finally:
        await db.close()


async def cmd_history(username: str, limit: int = 20) -> None:
    """Show status change history for a username.

    Args:
        username: Instagram username to show history for.
        limit: Number of history entries to display.
    """
    repo, db = await _get_repo()
    try:
        history = await repo.get_history(username, limit=limit)
        if not history:
            print(f"📊 No history found for '{username}'.")
            return

        print(f"\n📊 Status History for @{username} (last {limit}):\n")
        print(f"  {'Time':<22} {'Old Status':<15} {'New Status':<15} {'HTTP':<6} {'Response'}")
        print(f"  {'─' * 22} {'─' * 15} {'─' * 15} {'─' * 6} {'─' * 10}")

        for h in history:
            time_str = h.checked_at.strftime("%Y-%m-%d %H:%M:%S") if h.checked_at else "?"
            old = h.old_status or "—"
            http = str(h.http_status_code) if h.http_status_code else "—"
            response = f"{h.response_time_ms:.0f}ms" if h.response_time_ms else "—"
            print(f"  {time_str:<22} {old:<15} {h.new_status:<15} {http:<6} {response}")
        print()
    except Exception as e:
        print(f"❌ Error fetching history: {e}")
        logger.error("CLI history failed: {}", e)
    finally:
        await db.close()


async def cmd_check(username: Optional[str] = None) -> None:
    """Run a one-time check (single username or all active).

    Args:
        username: Specific username to check, or None to check all active.
    """
    from app.checker.instagram import InstagramChecker
    from app.notifier.telegram import TelegramNotifier
    from app.monitor.scheduler import MonitorScheduler

    settings = get_settings()
    repo, db = await _get_repo()

    checker = InstagramChecker(check_delay=settings.check_delay_seconds)
    notifier = TelegramNotifier(
        bot_token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
    )

    try:
        if username:
            # Check a specific username
            result = await checker.check_username(username)
            status_emoji = {"active": "🟢", "available": "🔵", "unavailable": "🔴"}.get(
                result.status, "⚪"
            )
            print(f"\n{status_emoji} @{result.username}: {result.status.upper()}")
            if result.http_status_code:
                print(f"   HTTP: {result.http_status_code} | Response: {result.response_time_ms:.0f}ms")
            if result.error:
                print(f"   Error: {result.error}")
            print()
        else:
            # Run a full monitoring cycle
            monitor = MonitorScheduler(
                repository=repo,
                checker=checker,
                notifier=notifier,
                check_interval=settings.check_interval_seconds,
                max_concurrent=settings.max_concurrent_checks,
            )
            print("🔄 Running monitoring cycle...")
            await monitor.run_once()
            print("✅ Monitoring cycle complete.")
    except Exception as e:
        print(f"❌ Check failed: {e}")
        logger.error("CLI check failed: {}", e)
    finally:
        await checker.close()
        await db.close()


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser.

    Returns:
        Configured ArgumentParser with all subcommands.
    """
    parser = argparse.ArgumentParser(
        prog="igmonitor",
        description="Instagram Username Monitoring System",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Add command
    add_parser = subparsers.add_parser("add", help="Add a username to monitor")
    add_parser.add_argument("username", help="Instagram username to monitor")

    # Remove command
    remove_parser = subparsers.add_parser("remove", help="Remove a username from monitoring")
    remove_parser.add_argument("username", help="Instagram username to remove")

    # List command
    subparsers.add_parser("list", help="List all monitored usernames")

    # History command
    history_parser = subparsers.add_parser("history", help="Show status history for a username")
    history_parser.add_argument("username", help="Instagram username")
    history_parser.add_argument("--limit", type=int, default=20, help="Number of entries (default: 20)")

    # Check command
    check_parser = subparsers.add_parser("check", help="Run a one-time check")
    check_parser.add_argument("username", nargs="?", help="Username to check (or all active)")

    # Run command (start the monitor)
    subparsers.add_parser("run", help="Start the continuous monitoring service")

    return parser


def main() -> None:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Initialize logging
    try:
        settings = get_settings()
        setup_logging(settings)
    except Exception as e:
        print(f"❌ Configuration error: {e}")
        print("   Make sure you have a .env file with the required settings.")
        print("   See .env.example for reference.")
        sys.exit(1)

    # Route to command handler
    if args.command == "add":
        asyncio.run(cmd_add(args.username))
    elif args.command == "remove":
        asyncio.run(cmd_remove(args.username))
    elif args.command == "list":
        asyncio.run(cmd_list())
    elif args.command == "history":
        asyncio.run(cmd_history(args.username, args.limit))
    elif args.command == "check":
        asyncio.run(cmd_check(args.username))
    elif args.command == "run":
        # Import and run the main service loop
        from app.main import run_service
        asyncio.run(run_service())
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
