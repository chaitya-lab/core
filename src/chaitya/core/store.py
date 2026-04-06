"""SQLite Persistent Store — default StoreProtocol implementation.

One database with two logical sections: session records and event log.
Cross-section queries are possible. Event log is append-only with
full-text search.

Reference: PRD §3.7, §13, §14
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import aiosqlite

from chaitya.core.event_bus import SqliteEventBus
from chaitya.core.types import (
    Event,
    EventFilter,
    SessionIdentity,
    SessionRecord,
    SessionState,
)

logger = logging.getLogger("chaitya.core.store")


class SqliteStore:
    """SQLite-backed persistent store.

    Wraps a single database containing:
      - sessions table  — session records
      - events table    — event log (managed by SqliteEventBus)

    The store owns the database connection and shares it with the event bus
    for cross-section queries.
    """

    def __init__(
        self,
        db_path: str | Path = ":memory:",
        *,
        max_events_per_second: int = 1000,
        max_log_size_bytes: int = 1_073_741_824,  # 1 GB
        event_bus: SqliteEventBus | None = None,
    ) -> None:
        self._db_path = str(db_path)
        self._max_log_size = max_log_size_bytes
        self._db: aiosqlite.Connection | None = None
        # Use provided event bus, or create one sharing this database
        self._event_bus = event_bus or SqliteEventBus(
            db_path=db_path,
            max_events_per_second=max_events_per_second,
            max_log_size_bytes=max_log_size_bytes,
        )

    @property
    def event_bus(self) -> SqliteEventBus:
        """Access the event bus (may share the same database)."""
        return self._event_bus

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def open(self) -> None:
        """Open the store and initialise all tables."""
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                name           TEXT PRIMARY KEY,
                state          TEXT NOT NULL DEFAULT 'idle',
                template       TEXT,
                identity_json  TEXT NOT NULL DEFAULT '{}',
                metadata_json  TEXT NOT NULL DEFAULT '{}',
                exec_mode      TEXT NOT NULL DEFAULT 'enabled',
                created_at     TEXT NOT NULL,
                last_activity  TEXT NOT NULL
            )
            """
        )
        await self._ensure_exec_mode_column()
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_inputs (
                request_id        TEXT PRIMARY KEY,
                adapter_name      TEXT NOT NULL,
                session_id        TEXT,
                spec_json         TEXT NOT NULL,
                args_json         TEXT NOT NULL,
                ctx_env_json      TEXT NOT NULL,
                input_stream_json TEXT NOT NULL,
                created_at        TEXT NOT NULL
            )
            """
        )
        await self._db.commit()
        # Open the event bus with its own connection to same DB
        await self._event_bus.open()
        logger.info("Store opened (db=%s)", self._db_path)

    async def close(self) -> None:
        """Close the store and event bus."""
        await self._event_bus.close()
        if self._db:
            await self._db.close()
            self._db = None
        logger.info("Store closed")

    # ------------------------------------------------------------------
    # Session Records
    # ------------------------------------------------------------------

    async def save_session(self, record: SessionRecord) -> None:
        """Create or update a session record."""
        if self._db is None:
            raise RuntimeError("Store not open")
        identity_json = json.dumps(
            {
                "env_vars": record.identity.env_vars,
                "working_dir": record.identity.working_dir,
                "browser_profile": record.identity.browser_profile,
            }
        )
        metadata_json = json.dumps(record.metadata, default=str)
        await self._db.execute(
            """
            INSERT INTO sessions
                (name, state, template, identity_json, metadata_json, exec_mode,
                 created_at, last_activity)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                state = excluded.state,
                template = excluded.template,
                identity_json = excluded.identity_json,
                metadata_json = excluded.metadata_json,
                exec_mode = excluded.exec_mode,
                last_activity = excluded.last_activity
            """,
            (
                record.name,
                record.state.value,
                record.template,
                identity_json,
                metadata_json,
                record.exec_mode,
                record.created_at,
                record.last_activity,
            ),
        )
        await self._db.commit()

    async def get_session(self, name: str) -> SessionRecord | None:
        """Retrieve a session record by name."""
        if self._db is None:
            raise RuntimeError("Store not open")
        async with self._db.execute("SELECT * FROM sessions WHERE name = ?", (name,)) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return None
            return self._row_to_session(row)

    async def list_sessions(self) -> list[SessionRecord]:
        """List all session records."""
        if self._db is None:
            raise RuntimeError("Store not open")
        sessions: list[SessionRecord] = []
        async with self._db.execute("SELECT * FROM sessions ORDER BY created_at") as cursor:
            async for row in cursor:
                sessions.append(self._row_to_session(row))
        return sessions

    async def delete_session(self, name: str) -> None:
        """Remove a session record."""
        if self._db is None:
            raise RuntimeError("Store not open")
        await self._db.execute("DELETE FROM sessions WHERE name = ?", (name,))
        await self._db.commit()

    # ------------------------------------------------------------------
    # Pending Suspension Requests
    # ------------------------------------------------------------------

    async def save_pending_input(
        self,
        *,
        request_id: str,
        adapter_name: str,
        session_id: str | None,
        spec: dict[str, Any],
        args: dict[str, Any],
        ctx_env: dict[str, Any],
        input_stream: dict[str, Any],
        created_at: str,
    ) -> None:
        if self._db is None:
            raise RuntimeError("Store not open")
        await self._db.execute(
            """
            INSERT INTO pending_inputs
                (request_id, adapter_name, session_id, spec_json, args_json,
                 ctx_env_json, input_stream_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(request_id) DO UPDATE SET
                adapter_name = excluded.adapter_name,
                session_id = excluded.session_id,
                spec_json = excluded.spec_json,
                args_json = excluded.args_json,
                ctx_env_json = excluded.ctx_env_json,
                input_stream_json = excluded.input_stream_json,
                created_at = excluded.created_at
            """,
            (
                request_id,
                adapter_name,
                session_id,
                json.dumps(spec),
                json.dumps(args),
                json.dumps(ctx_env),
                json.dumps(input_stream),
                created_at,
            ),
        )
        await self._db.commit()

    async def list_pending_inputs(self) -> list[dict[str, Any]]:
        if self._db is None:
            raise RuntimeError("Store not open")
        items: list[dict[str, Any]] = []
        async with self._db.execute(
            """
            SELECT request_id, adapter_name, session_id, spec_json, args_json,
                   ctx_env_json, input_stream_json, created_at
            FROM pending_inputs
            ORDER BY created_at
            """
        ) as cursor:
            async for row in cursor:
                items.append(
                    {
                        "request_id": row[0],
                        "adapter_name": row[1],
                        "session_id": row[2],
                        "spec": json.loads(row[3]),
                        "args": json.loads(row[4]),
                        "ctx_env": json.loads(row[5]),
                        "input_stream": json.loads(row[6]),
                        "created_at": row[7],
                    }
                )
        return items

    async def delete_pending_input(self, request_id: str) -> None:
        if self._db is None:
            raise RuntimeError("Store not open")
        await self._db.execute(
            "DELETE FROM pending_inputs WHERE request_id = ?",
            (request_id,),
        )
        await self._db.commit()

    # ------------------------------------------------------------------
    # Event Log (delegates to event bus)
    # ------------------------------------------------------------------

    async def append_event(self, event: Event) -> None:
        """Append an event to the event log."""
        await self._event_bus.emit(event)

    async def search_events(
        self, query: str, event_filter: EventFilter | None = None
    ) -> list[Event]:
        """Full-text search on event payloads.

        Searches the payload JSON for the query string.
        Optionally filters by EventFilter criteria.
        """
        if self._event_bus._db is None:
            raise RuntimeError("Store not open")

        # Flush pending batched events so search includes recent events
        await self._event_bus._flush_to_db()

        sql = "SELECT * FROM events WHERE payload LIKE ?"
        params: list[Any] = [f"%{query}%"]

        if event_filter:
            if event_filter.event_types:
                placeholders = ",".join("?" for _ in event_filter.event_types)
                sql += f" AND type IN ({placeholders})"
                params.extend(event_filter.event_types)
            if event_filter.source_adapter:
                sql += " AND source_adapter = ?"
                params.append(event_filter.source_adapter)
            if event_filter.session_id:
                sql += " AND session_id = ?"
                params.append(event_filter.session_id)
            if event_filter.limit:
                sql += " LIMIT ?"
                params.append(event_filter.limit)

        sql += " ORDER BY timestamp ASC"

        events: list[Event] = []
        async with self._event_bus._db.execute(sql, params) as cursor:
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

    async def get_events(self, event_filter: EventFilter) -> list[Event]:
        """Query events by structured filter criteria."""
        return await self._event_bus.history(event_filter)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_session(row: Any) -> SessionRecord:
        """Convert a database row to a SessionRecord."""
        identity_data = json.loads(row[3]) if row[3] else {}
        metadata = json.loads(row[4]) if row[4] else {}
        return SessionRecord(
            name=row[0],
            state=SessionState(row[1]),
            template=row[2],
            identity=SessionIdentity(
                env_vars=identity_data.get("env_vars", {}),
                working_dir=identity_data.get("working_dir"),
                browser_profile=identity_data.get("browser_profile"),
            ),
            metadata=metadata,
            exec_mode=row[5] or "enabled",
            created_at=row[6],
            last_activity=row[7],
        )

    async def _ensure_exec_mode_column(self) -> None:
        """Backfill the sessions.exec_mode column for pre-existing databases."""
        if self._db is None:
            raise RuntimeError("Store not open")
        async with self._db.execute("PRAGMA table_info(sessions)") as cursor:
            columns = [row[1] async for row in cursor]
        if "exec_mode" in columns:
            return
        await self._db.execute(
            "ALTER TABLE sessions ADD COLUMN exec_mode TEXT NOT NULL DEFAULT 'enabled'"
        )
        await self._db.commit()
