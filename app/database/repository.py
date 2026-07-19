"""Repository pattern for database operations.

Design Decisions:
    - Repository pattern decouples business logic from data access.
    - Each method creates its own session — no session leaks.
    - Status history only created when status changes — avoids flooding.
    - disabled_at/returned_at tracked with exact timestamps for user-facing reports.
    - follower_count stored on both current record and history entries.
"""

from datetime import datetime, timezone
from typing import Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql import func

from app.models.username import MonitoredUsername, StatusHistory, UsernameStatus


class UsernameRepository:
    """Handles all database operations for monitored usernames and their history.

    Attributes:
        _session_factory: SQLAlchemy async session factory.
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
        clean = username.lower().strip().lstrip("@")
        logger.info("Adding username to monitoring: {}", clean)
        async with self._session_factory() as session:
            new_username = MonitoredUsername(username=clean)
            session.add(new_username)
            await session.commit()
            await session.refresh(new_username)
            logger.info("Username added: {} (id={})", clean, new_username.id)
            return new_username

    async def remove_username(self, username: str) -> bool:
        """Soft-delete a username by setting is_active=False.

        Args:
            username: The username to deactivate.

        Returns:
            True if found and deactivated, False if not found.
        """
        clean = username.lower().strip().lstrip("@")
        logger.info("Soft-deleting username: {}", clean)
        async with self._session_factory() as session:
            stmt = select(MonitoredUsername).where(
                MonitoredUsername.username == clean
            )
            result = await session.execute(stmt)
            db_username = result.scalar_one_or_none()

            if db_username:
                db_username.is_active = False
                await session.commit()
                logger.info("Username deactivated: {}", clean)
                return True

            logger.warning("Username not found for removal: {}", clean)
            return False

    async def reactivate_username(self, username: str) -> bool:
        """Reactivate a previously deactivated username.

        Args:
            username: The username to reactivate.

        Returns:
            True if found and reactivated, False if not found.
        """
        clean = username.lower().strip().lstrip("@")
        async with self._session_factory() as session:
            stmt = select(MonitoredUsername).where(
                MonitoredUsername.username == clean
            )
            result = await session.execute(stmt)
            db_username = result.scalar_one_or_none()

            if db_username and not db_username.is_active:
                db_username.is_active = True
                db_username.current_status = UsernameStatus.UNKNOWN.value
                await session.commit()
                logger.info("Username reactivated: {}", clean)
                return True
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
        clean = username.lower().strip().lstrip("@")
        async with self._session_factory() as session:
            stmt = select(MonitoredUsername).where(
                MonitoredUsername.username == clean
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def update_status(
        self,
        username: str,
        new_status: UsernameStatus,
        http_code: Optional[int] = None,
        response_time: Optional[float] = None,
        follower_count: Optional[int] = None,
        error: Optional[str] = None,
    ) -> Optional[StatusHistory]:
        """Update username status and create a history entry if status changed.

        Also tracks disabled_at and returned_at timestamps:
        - When status changes TO unavailable → sets disabled_at
        - When status changes FROM unavailable TO active → sets returned_at

        Args:
            username: The username that was checked.
            new_status: The newly observed status.
            http_code: HTTP response code.
            response_time: Response time in milliseconds.
            follower_count: Follower count if available.
            error: Error message if the check failed.

        Returns:
            StatusHistory entry if state changed, None otherwise.
        """
        clean = username.lower().strip().lstrip("@")
        logger.debug("Updating status for {} to {}", clean, new_status.value)

        async with self._session_factory() as session:
            stmt = select(MonitoredUsername).where(
                MonitoredUsername.username == clean
            )
            result = await session.execute(stmt)
            db_username = result.scalar_one_or_none()

            if not db_username:
                logger.error("Cannot update status: username not found: {}", clean)
                return None

            old_status = db_username.current_status
            now = datetime.now(timezone.utc)
            history_entry: Optional[StatusHistory] = None

            # Update follower count if we got one
            if follower_count is not None:
                db_username.follower_count = follower_count

            # Instagram frequently blocks cloud IPs with login/challenge pages.
            # An unknown observation must not erase the last reliable state.
            if new_status == UsernameStatus.UNKNOWN:
                db_username.last_checked_at = now
                await session.commit()
                return None

            # Only create history entry if status actually changed
            if old_status != new_status.value:
                logger.info(
                    "Status CHANGED for {}: {} -> {}",
                    clean, old_status, new_status.value,
                )

                # Track exact disable/return timestamps
                if new_status == UsernameStatus.UNAVAILABLE:
                    db_username.disabled_at = now
                    logger.warning("Profile DISABLED: {} at {}", clean, now.isoformat())
                elif (
                    old_status == UsernameStatus.UNAVAILABLE.value
                    and new_status == UsernameStatus.ACTIVE
                ):
                    db_username.returned_at = now
                    logger.info("Profile RETURNED: {} at {}", clean, now.isoformat())

                history_entry = StatusHistory(
                    username_id=db_username.id,
                    old_status=old_status if old_status != UsernameStatus.UNKNOWN.value else None,
                    new_status=new_status.value,
                    follower_count=follower_count,
                    http_status_code=http_code,
                    response_time_ms=response_time,
                    error_message=error,
                )
                session.add(history_entry)
                db_username.current_status = new_status.value

            # Always update last checked timestamp
            db_username.last_checked_at = now

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
        clean = username.lower().strip().lstrip("@")
        async with self._session_factory() as session:
            stmt = (
                select(StatusHistory)
                .join(
                    MonitoredUsername,
                    StatusHistory.username_id == MonitoredUsername.id,
                )
                .where(MonitoredUsername.username == clean)
                .order_by(StatusHistory.checked_at.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())
