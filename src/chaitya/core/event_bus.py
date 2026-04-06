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


# Event types that should never be persisted to SQLite.
# These are high-frequency streaming events that would cause DB lock contention.
# They are still delivered to in-memory subscribers but not written to disk.
_STREAM_ONLY_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "stdout_chunk",
        "stderr_chunk",
        "progress_update",
        "file_changed",
    }
)


class SqliteEventBus:
    """SQLite-backed pub/sub event bus.

    - All emitted events are persisted to an SQLite events table (except
      high-frequency stream events: stdout_chunk, stderr_chunk, etc.).
    - In-memory subscriptions are matched on emit; handlers are invoked async.
    - Rate limiting via max_events_per_second (default 1000).
    - Writes are batched: events are buffered in memory and flushed to SQLite
      every ``batch_flush_seconds`` (default 0.5s) or when the buffer reaches
      ``batch_size`` (default 100 events).  This avoids per-event commits and
      reduces SQLite lock contention under high load.
    - History queries use EventFilter for structured lookups.
    """

    def __init__(
        self,
        db_path: str | Path = ":memory:",
        *,
        max_events_per_second: int = 1000,
        batch_size: int = 100,
        batch_flush_seconds: float = 0.5,
        max_log_size_bytes: int = 1_073_741_824,
    ) -> None:
        self._db_path = str(db_path)
        self._max_eps = max_events_per_second
        self._batch_size = batch_size
        self._batch_flush_seconds = batch_flush_seconds
        self._max_log_size = max_log_size_bytes
        self._db: aiosqlite.Connection | None = None
        self._subscriptions: dict[str, _SubscriptionEntry] = {}
        self._lock = asyncio.Lock()
        # Rate-limit state
        self._window_start: float = 0.0
        self._window_count: int = 0
        # Batch-write state
        self._pending_rows: list[tuple[Any, ...]] = []
        self._flush_task: asyncio.Task[None] | None = None
        self._flush_lock = asyncio.Lock()
        # Rolling deletion: check size every N flushes
        self._flush_count_since_size_check: int = 0
        self._size_check_interval: int = 10

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def open(self) -> None:
        """Open the database, create the events table, and start the flush task."""
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
        await self._db.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON events (type)")
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
        self._flush_task = asyncio.create_task(self._flush_loop())
        logger.info(
            "Event bus opened (db=%s, batch=%d, flush=%.1fs)",
            self._db_path,
            self._batch_size,
            self._batch_flush_seconds,
        )

    async def close(self) -> None:
        """Flush pending events and close the database connection."""
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
            self._flush_task = None
        # Final flush of any remaining events
        await self._flush_to_db()
        if self._db:
            await self._db.close()
            self._db = None
        self._subscriptions.clear()
        self._pending_rows.clear()
        logger.info("Event bus closed")

    # ------------------------------------------------------------------
    # EventBusProtocol implementation
    # ------------------------------------------------------------------

    async def emit(self, event: Event) -> None:
        """Publish an event: batch-persist to SQLite (except stream events), deliver to subscribers."""
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

        # Persist to SQLite only if NOT a stream-only event type.
        # Stream events (stdout_chunk, stderr_chunk, etc.) are high-frequency
        # and would cause SQLite lock contention. They are still delivered
        # to in-memory subscribers but not written to disk.
        if event.type not in _STREAM_ONLY_EVENT_TYPES:
            payload_json = json.dumps(event.payload, default=str)
            row = (
                event.event_id,
                event.type,
                event.source_adapter,
                event.timestamp,
                event.session_id,
                event.exit_code,
                event.duration_ms,
                payload_json,
                event.parent_event_id,
                event.request_id,
            )
            async with self._flush_lock:
                self._pending_rows.append(row)
                if len(self._pending_rows) >= self._batch_size:
                    await self._flush_to_db_unlocked()

        # Deliver to matching subscribers immediately.
        # Async handlers are awaited inline (they should be fast).
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

    async def _flush_to_db(self) -> None:
        """Flush all pending rows to SQLite (called with lock held)."""
        async with self._flush_lock:
            await self._flush_to_db_unlocked()

    async def _flush_to_db_unlocked(self) -> None:
        """Flush pending rows to SQLite. Must be called with _flush_lock held."""
        if not self._pending_rows or self._db is None:
            return
        rows = self._pending_rows
        self._pending_rows = []
        try:
            await self._db.executemany(
                """
                INSERT OR IGNORE INTO events
                    (event_id, type, source_adapter, timestamp, session_id,
                     exit_code, duration_ms, payload, parent_event_id, request_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            await self._db.commit()
            logger.debug("Flushed %d events to SQLite", len(rows))
        except Exception as exc:
            logger.error("Failed to flush %d events to SQLite: %s", len(rows), exc)
            # Put rows back on failure (best-effort)
            self._pending_rows = rows + self._pending_rows

        # Rolling deletion: check size periodically, delete oldest events when over limit
        self._flush_count_since_size_check += 1
        if self._flush_count_since_size_check >= self._size_check_interval:
            self._flush_count_since_size_check = 0
            await self._enforce_log_size()

    async def _flush_loop(self) -> None:
        """Background task: flush pending rows to SQLite periodically."""
        while True:
            await asyncio.sleep(self._batch_flush_seconds)
            await self._flush_to_db()

    async def _enforce_log_size(self) -> None:
        """Delete oldest events when the database exceeds max_log_size_bytes.

        PRD §3.7: rolling deletion of oldest events when max_log_size_bytes is approached.
        Deletes down to 80% of the limit to avoid constant small deletions.
        """
        if self._max_log_size <= 0 or self._db is None:
            return
        try:
            db_size = Path(self._db_path).stat().st_size
            if db_size <= self._max_log_size:
                return
            # Target: delete down to 80% of the limit
            target_size = int(self._max_log_size * 0.8)
            over_by = db_size - target_size

            # Estimate how many rows to delete (avg row ~500 bytes)
            avg_row_bytes = 500
            rows_to_delete = max(1, over_by // avg_row_bytes)

            cursor = await self._db.execute(
                "SELECT event_id FROM events ORDER BY timestamp ASC LIMIT ?",
                (rows_to_delete,),
            )
            ids = [row[0] for row in await cursor.fetchall()]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                await self._db.execute(
                    f"DELETE FROM events WHERE event_id IN ({placeholders})", ids
                )
                await self._db.commit()
                new_size = Path(self._db_path).stat().st_size
                logger.warning(
                    "Event log rolling deletion: removed %d oldest events (%.1fMB -> %.1fMB, limit=%.1fMB)",
                    len(ids),
                    db_size / 1_048_576,
                    new_size / 1_048_576,
                    self._max_log_size / 1_048_576,
                )
        except Exception as exc:
            logger.error("Rolling deletion failed: %s", exc)

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
        """Query historical events matching the filter.

        Flushes pending batched rows before querying so that recent events
        not yet written to SQLite are included in results.
        """
        if self._db is None:
            raise RuntimeError("Event bus not open — call open() first")

        # Ensure any pending batched events are written to SQLite first
        await self._flush_to_db()

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
        await self._flush_to_db()
        async with self._db.execute("SELECT COUNT(*) FROM events") as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0
