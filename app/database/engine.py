"""Async database engine and session management.

Design Decisions:
    - DatabaseManager class encapsulates engine + session factory — no global state.
    - pool_pre_ping=True detects stale connections before use (critical for long-running
      services where Supabase may close idle connections).
    - expire_on_commit=False prevents lazy-load errors when accessing model attributes
      after commit (common async SQLAlchemy pitfall).
    - The get_session() context manager handles commit/rollback/close lifecycle — callers
      never need to manage transactions manually.
"""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from loguru import logger
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.models.username import Base


class DatabaseManager:
    """Manages async database connections and session lifecycle.

    Provides a clean interface for creating sessions, initializing the schema,
    and shutting down connections. Designed for dependency injection — pass
    the database_url at construction, not import-time.

    Attributes:
        _engine: The async SQLAlchemy engine.
        _session_factory: Factory for creating new async sessions.
    """

    def __init__(self, database_url: str) -> None:
        """Initialize the database manager.

        Args:
            database_url: Async-compatible connection string
                          (e.g., 'postgresql+asyncpg://user:pass@host:5432/db').
        """
        logger.info("Initializing database engine")

        self._engine: AsyncEngine = create_async_engine(
            database_url,
            pool_size=5,        # Base pool connections
            max_overflow=10,    # Extra connections under load
            pool_pre_ping=True, # Verify connections before use
            echo=False,         # Set True for SQL query logging (debug only)
        )

        self._session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            bind=self._engine,
            class_=AsyncSession,
            expire_on_commit=False,  # Prevent lazy-load errors in async context
            autocommit=False,
            autoflush=False,
        )

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        """Expose the session factory for repository injection.

        Returns:
            The configured async_sessionmaker instance.
        """
        return self._session_factory

    async def init_db(self) -> None:
        """Create all tables defined in ORM models.

        Uses run_sync to execute DDL in the async context. Safe to call
        multiple times — CREATE TABLE IF NOT EXISTS semantics.

        This is for development/startup convenience. In production,
        use Alembic migrations for schema changes.
        """
        logger.info("Creating database tables")
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables created successfully")

    @asynccontextmanager
    async def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        """Async context manager for database sessions.

        Handles the full session lifecycle:
        - Creates a new session
        - Yields it for use
        - Commits on success
        - Rolls back on exception
        - Always closes the session

        Yields:
            An async database session.

        Raises:
            Any exception from the session operations (after rollback).
        """
        session: AsyncSession = self._session_factory()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    async def close(self) -> None:
        """Dispose the engine pool and close all connections.

        Call this during application shutdown to clean up resources.
        """
        logger.info("Closing database engine")
        await self._engine.dispose()
