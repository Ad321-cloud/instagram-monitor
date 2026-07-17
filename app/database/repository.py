"""Repository pattern for database operations.

Design Decisions:
    - Repository pattern decouples business logic from data access — the monitor
      doesn't need to know SQL, just call repo.update_status().
    - Each method creates its own session via the factory — no session leaks,
      no shared mutable state between operations.
    - Status history is only created when the status actually changes — avoids
      flooding the history table with redundant "still active" entries.
    - Soft delete (is_active=False) preserves history for deactivated usernames.
"""

from typing import Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql import func

from app.models.username import MonitoredUsername, StatusHistory, UsernameStatus


class UsernameRepository:
    """Handles all database operations for monitored usernames and their history.

    Encapsulates CRUD operations and status tracking behind a clean async interface.
    Each method manages its own session lifecycle — no transaction leaks.

    Attributes:
        _session_factory: SQLAlchemy async session factory for creating sessions.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Initialize the repository.

        Args:
            session_factory: Async session factory from DatabaseManager.
        """
        self._session_factory = session_factory

    async def add_username(self, username: str) -> MonitoredUsername:
        """Add a new username to the monitoring list.

        Args:
            username: Instagram username to monitor (without @).

        Returns:
            The newly created MonitoredUsername record.

        Raises:
            sqlalchemy.exc.IntegrityError: If the username already exists.
        """
        logger.info("Adding username to monitoring: {}", username)
        async with self._session_factory() as session:
            new_username = MonitoredUsername(username=username.lower().strip())
            session.add(new_username)
            await session.commit()
            await session.refresh(new_username)
            logger.info("Username added successfully: {} (id={})", username, new_username.id)
            return new_username

    async def remove_username(self, username: str) -> bool:
        """Soft-delete a username by setting is_active=False.

        Preserves all history data. The username can be reactivated later.

        Args:
            username: The username to deactivate.

        Returns:
            True if the username was found and deactivated, False if not found.
        """
        logger.info("Soft-deleting username: {}", username)
        async with self._session_factory() as session:
            stmt = select(MonitoredUsername).where(
                MonitoredUsername.username == username.lower().strip()
            )
            result = await session.execute(stmt)
            db_username = result.scalar_one_or_none()

            if db_username:
                db_username.is_active = False
                await session.commit()
                logger.info("Username deactivated: {}", username)
                return True

            logger.warning("Username not found for removal: {}", username)
            return False

    async def get_all_active(self) -> list[MonitoredUsername]:
        """Get all usernames that are actively being monitored.

        Returns:
            List of active MonitoredUsername records.
        """
        async with self._session_factory() as session:
            stmt = select(MonitoredUsername).where(
                MonitoredUsername.is_active.is_(True)
            )
            result = await session.execute(stmt)
            usernames = list(result.scalars().all())
            logger.debug("Retrieved {} active usernames", len(usernames))
            return usernames

    async def get_all_usernames(self) -> list[MonitoredUsername]:
        """Get all usernames, including deactivated ones.

        Returns:
            List of all MonitoredUsername records.
        """
        async with self._session_factory() as session:
            stmt = select(MonitoredUsername).order_by(MonitoredUsername.created_at.desc())
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_by_username(self, username: str) -> Optional[MonitoredUsername]:
        """Look up a specific username.

        Args:
            username: The username to search for.

        Returns:
            The MonitoredUsername record if found, None otherwise.
        """
        async with self._session_factory() as session:
            stmt = select(MonitoredUsername).where(
                MonitoredUsername.username == username.lower().strip()
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def update_status(
        self,
        username: str,
        new_status: UsernameStatus,
        http_code: Optional[int] = None,
        response_time: Optional[float] = None,
        error: Optional[str] = None,
    ) -> Optional[StatusHistory]:
        """Update username status and create a history entry if status changed.

        Always updates last_checked_at. Only creates a StatusHistory entry
        when the status actually changes — this prevents the history table
        from filling up with redundant "still active" entries.

        Args:
            username: The username that was checked.
            new_status: The newly observed status.
            http_code: HTTP response code from the check (for diagnostics).
            response_time: Response time in milliseconds (for diagnostics).
            error: Error message if the check failed.

        Returns:
            The StatusHistory entry if a state change occurred, None otherwise.
            Also returns None if the username wasn't found in the database.
        """
        logger.debug("Updating status for {} to {}", username, new_status.value)
        async with self._session_factory() as session:
            stmt = select(MonitoredUsername).where(
                MonitoredUsername.username == username.lower().strip()
            )
            result = await session.execute(stmt)
            db_username = result.scalar_one_or_none()

            if not db_username:
                logger.error("Cannot update status: username not found: {}", username)
                return None

            old_status = db_username.current_status
            history_entry: Optional[StatusHistory] = None

            # Only create history entry if status actually changed
            if old_status != new_status.value:
                logger.info(
                    "Status CHANGED for {}: {} -> {}",
                    username,
                    old_status,
                    new_status.value,
                )
                history_entry = StatusHistory(
                    username_id=db_username.id,
                    old_status=old_status if old_status != UsernameStatus.UNKNOWN.value else None,
                    new_status=new_status.value,
                    http_status_code=http_code,
                    response_time_ms=response_time,
                    error_message=error,
                )
                session.add(history_entry)
                db_username.current_status = new_status.value

            # Always update the last checked timestamp
            db_username.last_checked_at = func.now()

            await session.commit()

            if history_entry:
                await session.refresh(history_entry)

            return history_entry

    async def get_history(
        self, username: str, limit: int = 50
    ) -> list[StatusHistory]:
        """Get the status change history for a username.

        Args:
            username: The username to retrieve history for.
            limit: Maximum number of entries to return (default: 50).

        Returns:
            List of StatusHistory entries, newest first.
        """
        async with self._session_factory() as session:
            stmt = (
                select(StatusHistory)
                .join(
                    MonitoredUsername,
                    StatusHistory.username_id == MonitoredUsername.id,
                )
                .where(MonitoredUsername.username == username.lower().strip())
                .order_by(StatusHistory.checked_at.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())
