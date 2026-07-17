"""SQLAlchemy 2.0 ORM models for the Instagram monitoring system.

Design Decisions:
    - SQLAlchemy 2.0 Mapped[] annotations for full type safety.
    - str Enum (UsernameStatus) for easy serialization and database storage.
    - Soft delete pattern (is_active flag) instead of hard deletes — preserves history.
    - server_default=func.now() for timestamps — delegated to the DB for consistency.
    - cascade="all, delete-orphan" on history relationship — if a username is hard-deleted,
      its history goes with it. Soft deletes preserve everything.
"""

from datetime import datetime
from enum import Enum
from typing import List, Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func


class UsernameStatus(str, Enum):
    """Possible states of an Instagram username.

    Using str mixin so enum values serialize cleanly to/from the database
    and JSON without explicit .value calls everywhere.

    States:
        ACTIVE: Profile exists and is publicly accessible (HTTP 200).
        AVAILABLE: Username is not taken (HTTP 404).
        UNAVAILABLE: Profile is private, suspended, or blocked (HTTP 301/302).
        UNKNOWN: Could not determine status (rate limited, network error, etc).
    """

    ACTIVE = "active"
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy ORM models."""

    pass


class MonitoredUsername(Base):
    """An Instagram username being monitored for state changes.

    Tracks the current status, monitoring configuration (active/inactive),
    and links to the full status change history.

    Attributes:
        id: Auto-incrementing primary key.
        username: Instagram username (max 30 chars per IG rules), unique and indexed.
        current_status: Most recently observed status.
        is_active: Whether this username is actively being monitored.
        created_at: When this username was added to monitoring.
        updated_at: When this record was last modified.
        last_checked_at: When the last check was performed.
        history: All status change records for this username.
    """

    __tablename__ = "monitored_usernames"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(
        String(30), unique=True, nullable=False, index=True
    )
    current_status: Mapped[str] = mapped_column(
        String(20), default=UsernameStatus.UNKNOWN.value
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )
    last_checked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # One-to-many: a username has many status history entries
    history: Mapped[List["StatusHistory"]] = relationship(
        "StatusHistory",
        back_populates="monitored_username",
        cascade="all, delete-orphan",
        order_by="StatusHistory.checked_at.desc()",
    )

    def __repr__(self) -> str:
        """Human-readable representation for debugging."""
        return (
            f"<MonitoredUsername(username='{self.username}', "
            f"status='{self.current_status}', active={self.is_active})>"
        )


class StatusHistory(Base):
    """A single status check record for a monitored username.

    Created whenever a username's status changes (or on first check).
    Stores both the old and new status, plus diagnostic metadata like
    HTTP status code and response time for debugging.

    Attributes:
        id: Auto-incrementing primary key.
        username_id: Foreign key to the monitored username.
        old_status: Previous status (None for first check).
        new_status: Newly observed status.
        checked_at: When this check was performed.
        http_status_code: HTTP response code from Instagram.
        response_time_ms: Round-trip time in milliseconds.
        error_message: Error details if the check failed.
        monitored_username: Back-reference to the parent username.
    """

    __tablename__ = "status_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("monitored_usernames.id"), nullable=False, index=True
    )

    old_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    new_status: Mapped[str] = mapped_column(String(20), nullable=False)

    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    http_status_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    response_time_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Many-to-one: each history entry belongs to one username
    monitored_username: Mapped["MonitoredUsername"] = relationship(
        "MonitoredUsername", back_populates="history"
    )

    def __repr__(self) -> str:
        """Human-readable representation for debugging."""
        return (
            f"<StatusHistory(username_id={self.username_id}, "
            f"'{self.old_status}' -> '{self.new_status}')>"
        )
