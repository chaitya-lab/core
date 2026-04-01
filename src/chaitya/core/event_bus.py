"""SQLite-backed Event Bus — default EventBusProtocol implementation.

Routes JSON-line events between adapters. Persists all events to SQLite.
Manages in-memory subscriptions with filter matching. Supports history queries.

Reference: PRD §3.4, §6, §13
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import aiosqlite

from chaitya.core.types import Event, EventFilter, Subscription

logger = logging.getLogger("chaitya.core.event_bus")


class _SubscriptionEntry:
    """Internal: a subscription with its filter and handler."""

    __slots__ = ("subscription", "handler")

    def __init__(self, subscription: Subscription, handler: Callable[[Event], Any]) -> None:
        self.subscription = subscription
        self.handler = handler


def _event_matches_filter(event: Event, ef: EventFilter) -> bool:
    """Check if an event matches an EventFilter."""
    if ef.event_types and event.type not in ef.event_types:
        return False
    if ef.source_adapter and event.source_adapter != ef.source_adapter:
        return False
    if ef.session_id and event.session_id != ef.session_id:
        return False
    if ef.request_id and event.request_id != ef.request_id:
        return False
    return True


class SqliteEventBus:
    """SQLite-backed pub/sub event bus.

    - All emitted events are persisted to an SQLite events table.
    - In-memory subscriptions are matched on emit; handlers are invoked async.
    - Rate limiting via max_events_per_second (default 1000).
    - History queries use EventFilter for structured lookups.
    """

    def __init__(
        self,
        db_path: str | Path = ":memory:",
        *,
        max_events_per_second: int = 1000,
    ) -> None:
        self._db_path = str(db_path)
        self._max_eps = max_events_per_second
        self._db: aiosqlite.Connection | None = None
        self._subscriptions: dict[str, _SubscriptionEntry] = {}
        self._lock = asyncio.Lock()
        # Rate-limit state
        self._window_start: float = 0.0
        self._window_count: int = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def open(self) -> None:
        """Open the database and create the events table."""
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                event_id       TEXT PRIMARY KEY,
                type           TEXT NOT NULL DEFAULT '',
                source_adapter TEXT NOT NULL DEFAULT '',
                timestamp      TEXT NOT NULL,
                session_id     TEXT,
                exit_code      INTEGER,
                duration_ms    INTEGER,
                payload        TEXT NOT NULL DEFAULT '{}',
                parent_event_id TEXT,
                request_id     TEXT
            )
            """
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_type ON events (type)"
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_session ON events (session_id)"
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events (timestamp)"
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_request_id ON events (request_id)"
        )
        await self._db.commit()
        logger.info("Event bus opened (db=%s)", self._db_path)

    async def close(self) -> None:
        """Close the database connection."""
        if self._db:
            await self._db.close()
            self._db = None
        self._subscriptions.clear()
        logger.info("Event bus closed")

    # ------------------------------------------------------------------
    # EventBusProtocol implementation
    # ------------------------------------------------------------------

    async def emit(self, event: Event) -> None:
        """Publish an event: persist to SQLite, then deliver to subscribers."""
        if self._db is None:
            raise RuntimeError("Event bus not open — call open() first")

        # Rate limiting
        now = time.monotonic()
        if now - self._window_start >= 1.0:
            self._window_start = now
            self._window_count = 0
        self._window_count += 1
        if self._window_count > self._max_eps:
            logger.warning(
                "Rate limit exceeded (%d events/s), dropping event %s",
                self._max_eps,
                event.event_id,
            )
            return

        # Persist
        payload_json = json.dumps(event.payload, default=str)
        await self._db.execute(
            """
            INSERT OR IGNORE INTO events
                (event_id, type, source_adapter, timestamp, session_id,
                 exit_code, duration_ms, payload, parent_event_id, request_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id, event.type, event.source_adapter,
                event.timestamp, event.session_id, event.exit_code,
                event.duration_ms, payload_json, event.parent_event_id,
                event.request_id,
            ),
        )
        await self._db.commit()

        # Deliver to matching subscribers (fire-and-forget)
        for entry in list(self._subscriptions.values()):
            if not entry.subscription.active:
                continue
            if _event_matches_filter(event, entry.subscription.filter):
                try:
                    result = entry.handler(event)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:
                    logger.exception(
                        "Subscriber %s handler error",
                        entry.subscription.subscription_id,
                    )

    async def subscribe(
        self,
        event_filter: EventFilter,
        handler: Callable[[Event], Any],
    ) -> Subscription:
        """Subscribe to events matching the filter."""
        sub = Subscription(filter=event_filter)
        self._subscriptions[sub.subscription_id] = _SubscriptionEntry(sub, handler)
        logger.debug("Subscription %s added", sub.subscription_id)
        return sub

    async def unsubscribe(self, subscription: Subscription) -> None:
        """Remove a subscription."""
        subscription.active = False
        self._subscriptions.pop(subscription.subscription_id, None)
        logger.debug("Subscription %s removed", subscription.subscription_id)

    async def history(self, event_filter: EventFilter) -> list[Event]:
        """Query historical events matching the filter."""
        if self._db is None:
            raise RuntimeError("Event bus not open — call open() first")

        query = "SELECT * FROM events WHERE 1=1"
        params: list[Any] = []

        if event_filter.event_types:
            placeholders = ",".join("?" for _ in event_filter.event_types)
            query += f" AND type IN ({placeholders})"
            params.extend(event_filter.event_types)

        if event_filter.source_adapter:
            query += " AND source_adapter = ?"
            params.append(event_filter.source_adapter)

        if event_filter.session_id:
            query += " AND session_id = ?"
            params.append(event_filter.session_id)

        if event_filter.request_id:
            query += " AND request_id = ?"
            params.append(event_filter.request_id)

        if event_filter.since:
            query += " AND timestamp >= ?"
            params.append(event_filter.since.isoformat())

        if event_filter.until:
            query += " AND timestamp <= ?"
            params.append(event_filter.until.isoformat())

        query += " ORDER BY timestamp ASC"

        if event_filter.limit:
            query += " LIMIT ?"
            params.append(event_filter.limit)

        events: list[Event] = []
        async with self._db.execute(query, params) as cursor:
            async for row in cursor:
                events.append(
                    Event(
                        event_id=row[0],
                        type=row[1],
                        source_adapter=row[2],
                        timestamp=row[3],
                        session_id=row[4],
                        exit_code=row[5],
                        duration_ms=row[6],
                        payload=json.loads(row[7]) if row[7] else {},
                        parent_event_id=row[8],
                        request_id=row[9],
                    )
                )
        return events

    async def event_count(self) -> int:
        """Return total number of persisted events (for diagnostics)."""
        if self._db is None:
            return 0
        async with self._db.execute("SELECT COUNT(*) FROM events") as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0
