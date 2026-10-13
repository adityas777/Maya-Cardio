"""
store.py — Durable SQLite store (§3.2, §6).

Uses WAL mode + synchronous=FULL for crash safety.
All writes inside transactions; INSERT OR IGNORE for deduplication.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=FULL;

CREATE TABLE IF NOT EXISTS sessions (
    session_id   TEXT PRIMARY KEY,
    started_mono REAL NOT NULL,
    ended        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS samples (
    session_id TEXT    NOT NULL,
    sensor     INTEGER NOT NULL,   -- 1 or 2
    idx        INTEGER NOT NULL,   -- sample index from 0
    ch1        INTEGER NOT NULL,
    ch2        INTEGER NOT NULL,
    ch3        INTEGER,            -- NULL for sensor 2
    PRIMARY KEY (session_id, sensor, idx)
);

CREATE TABLE IF NOT EXISTS losses (
    session_id TEXT    NOT NULL,
    sensor     INTEGER NOT NULL,
    from_index INTEGER NOT NULL,
    to_index   INTEGER,            -- NULL when end unknown
    reason     TEXT    NOT NULL,
    PRIMARY KEY (session_id, sensor, from_index)
);

CREATE TABLE IF NOT EXISTS cursors (
    session_id TEXT    NOT NULL,
    sensor     INTEGER NOT NULL,
    next_index INTEGER NOT NULL,   -- next index the reader expects
    cloud_ack  INTEGER NOT NULL,   -- all indexes < cloud_ack are in cloud
    PRIMARY KEY (session_id, sensor)
);

CREATE TABLE IF NOT EXISTS results (
    session_id      TEXT    NOT NULL,
    window          INTEGER NOT NULL,
    mode            TEXT    NOT NULL,   -- 'online' or 'offline'
    result_json     TEXT    NOT NULL,
    result_time_ms  INTEGER NOT NULL,
    PRIMARY KEY (session_id, window, mode)
);
"""


# ---------------------------------------------------------------------------
# Store class
# ---------------------------------------------------------------------------

class Store:
    """
    Thread-safe SQLite durable store.

    Each public method acquires _lock so that the reader threads (one per
    sensor) and the uploader can call concurrently without data races.
    """

    def __init__(self, db_path: str) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        self._open()

    # ── Lifecycle ────────────────────────────────────────────────────────

    def _open(self) -> None:
        self._conn = sqlite3.connect(
            str(self._path),
            check_same_thread=False,
            isolation_level=None,   # autocommit off; we manage transactions
            timeout=30,
        )
        self._conn.executescript(_SCHEMA)

    def integrity_check(self) -> bool:
        """Run PRAGMA integrity_check. Returns True if OK."""
        with self._lock:
            result = self._conn.execute("PRAGMA integrity_check").fetchone()
            return result is not None and result[0] == "ok"

    def close(self) -> None:
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None

    # ── Session management ───────────────────────────────────────────────

    def open_session(self, session_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO sessions (session_id, started_mono) VALUES (?, ?)",
                (session_id, time.monotonic()),
            )
            self._conn.commit()

    def close_session(self, session_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET ended=1 WHERE session_id=?",
                (session_id,),
            )
            self._conn.commit()

    def get_open_session(self) -> Optional[str]:
        """Return the session_id of any non-ended session, or None."""
        with self._lock:
            row = self._conn.execute(
                "SELECT session_id FROM sessions WHERE ended=0 LIMIT 1"
            ).fetchone()
            return row[0] if row else None

    def list_sessions(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT session_id FROM sessions ORDER BY started_mono"
            ).fetchall()
            return [r[0] for r in rows]

    # ── Cursor management ────────────────────────────────────────────────

    def get_cursor(self, session_id: str, sensor: int) -> tuple[int, int]:
        """Return (next_index, cloud_ack)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT next_index, cloud_ack FROM cursors WHERE session_id=? AND sensor=?",
                (session_id, sensor),
            ).fetchone()
            return (row[0], row[1]) if row else (0, 0)

    def _upsert_cursor_nolock(
        self, session_id: str, sensor: int, next_index: int, cloud_ack: Optional[int] = None
    ) -> None:
        """Must be called with _lock held."""
        existing = self._conn.execute(
            "SELECT next_index, cloud_ack FROM cursors WHERE session_id=? AND sensor=?",
            (session_id, sensor),
        ).fetchone()
        if existing:
            cur_cloud_ack = cloud_ack if cloud_ack is not None else existing[1]
            self._conn.execute(
                "UPDATE cursors SET next_index=?, cloud_ack=? WHERE session_id=? AND sensor=?",
                (next_index, cur_cloud_ack, session_id, sensor),
            )
        else:
            self._conn.execute(
                "INSERT INTO cursors (session_id, sensor, next_index, cloud_ack) VALUES (?,?,?,?)",
                (session_id, sensor, next_index, cloud_ack or 0),
            )

    def advance_cloud_ack(self, session_id: str, sensor: int, cloud_ack: int) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO cursors (session_id, sensor, next_index, cloud_ack) VALUES (?,?,?,?)
                   ON CONFLICT(session_id, sensor) DO UPDATE SET cloud_ack=MAX(cloud_ack, excluded.cloud_ack)""",
                (session_id, sensor, cloud_ack, cloud_ack),
            )
            self._conn.commit()

    # ── Sample writes ────────────────────────────────────────────────────

    def write_batch(
        self,
        session_id: str,
        sensor: int,
        samples: list[tuple[int, list[int]]],    # (idx, values)
        losses: list[tuple[int, Optional[int], str]],  # (from, to, reason)
        new_next_index: int,
    ) -> None:
        """
        Atomically commit a batch of samples + losses + updated cursor.
        Uses INSERT OR IGNORE for deduplication (PK enforcement).
        """
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                for idx, vals in samples:
                    ch3 = vals[2] if len(vals) > 2 else None
                    self._conn.execute(
                        "INSERT OR IGNORE INTO samples (session_id, sensor, idx, ch1, ch2, ch3) "
                        "VALUES (?,?,?,?,?,?)",
                        (session_id, sensor, idx, vals[0], vals[1], ch3),
                    )
                for from_idx, to_idx, reason in losses:
                    self._conn.execute(
                        "INSERT OR IGNORE INTO losses (session_id, sensor, from_index, to_index, reason) "
                        "VALUES (?,?,?,?,?)",
                        (session_id, sensor, from_idx, to_idx, reason),
                    )
                self._upsert_cursor_nolock(session_id, sensor, new_next_index)
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    # ── Sample reads ─────────────────────────────────────────────────────

    def get_samples(
        self,
        session_id: str,
        sensor: int,
        from_index: int,
        to_index: int,
    ) -> list[tuple[int, list[int]]]:
        """Return available samples in range, as (idx, values) list."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT idx, ch1, ch2, ch3 FROM samples "
                "WHERE session_id=? AND sensor=? AND idx BETWEEN ? AND ? "
                "ORDER BY idx",
                (session_id, sensor, from_index, to_index),
            ).fetchall()
        result = []
        for row in rows:
            idx, ch1, ch2, ch3 = row
            vals = [ch1, ch2] if ch3 is None else [ch1, ch2, ch3]
            result.append((idx, vals))
        return result

    def get_losses(
        self,
        session_id: str,
        sensor: int,
        from_index: int,
        to_index: int,
    ) -> list[tuple[int, Optional[int], str]]:
        """Return loss records overlapping [from_index, to_index]."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT from_index, to_index, reason FROM losses "
                "WHERE session_id=? AND sensor=? "
                "AND from_index <= ? AND (to_index IS NULL OR to_index >= ?) "
                "ORDER BY from_index",
                (session_id, sensor, to_index, from_index),
            ).fetchall()
        return [(r[0], r[1], r[2]) for r in rows]

    def get_all_losses(self, session_id: str, sensor: int) -> list[tuple[int, Optional[int], str]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT from_index, to_index, reason FROM losses "
                "WHERE session_id=? AND sensor=? ORDER BY from_index",
                (session_id, sensor),
            ).fetchall()
        return [(r[0], r[1], r[2]) for r in rows]

    # ── Unacknowledged samples for uploader ──────────────────────────────

    def get_unuploaded_samples(
        self,
        session_id: str,
        sensor: int,
        batch_size: int = 400,
    ) -> list[tuple[int, list[int]]]:
        """Return up to batch_size samples past cloud_ack cursor."""
        _, cloud_ack = self.get_cursor(session_id, sensor)
        with self._lock:
            rows = self._conn.execute(
                "SELECT idx, ch1, ch2, ch3 FROM samples "
                "WHERE session_id=? AND sensor=? AND idx >= ? "
                "ORDER BY idx LIMIT ?",
                (session_id, sensor, cloud_ack, batch_size),
            ).fetchall()
        result = []
        for row in rows:
            idx, ch1, ch2, ch3 = row
            vals = [ch1, ch2] if ch3 is None else [ch1, ch2, ch3]
            result.append((idx, vals))
        return result

    def get_unuploaded_losses(
        self,
        session_id: str,
        sensor: int,
    ) -> list[tuple[int, Optional[int], str]]:
        """Return losses where from_index >= cloud_ack."""
        _, cloud_ack = self.get_cursor(session_id, sensor)
        with self._lock:
            rows = self._conn.execute(
                "SELECT from_index, to_index, reason FROM losses "
                "WHERE session_id=? AND sensor=? AND from_index >= ? "
                "ORDER BY from_index",
                (session_id, sensor, cloud_ack),
            ).fetchall()
        return [(r[0], r[1], r[2]) for r in rows]

    # ── Results ──────────────────────────────────────────────────────────

    def write_result(
        self,
        session_id: str,
        window: int,
        mode: str,
        result_json: str,
        result_time_ms: int,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO results (session_id, window, mode, result_json, result_time_ms) "
                "VALUES (?,?,?,?,?)",
                (session_id, window, mode, result_json, result_time_ms),
            )
            self._conn.commit()

    def get_results(self, session_id: str) -> list[str]:
        """Return result JSON strings ordered by window, mode."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT result_json FROM results WHERE session_id=? ORDER BY window, mode",
                (session_id,),
            ).fetchall()
        return [r[0] for r in rows]

    # ── Status helpers ───────────────────────────────────────────────────

    def count_samples(self, session_id: str) -> int:
        """Total sample rows across both sensors."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM samples WHERE session_id=?", (session_id,)
            ).fetchone()
            return row[0] if row else 0

    def max_cloud_ack(self, session_id: str) -> int:
        """Max cloud_ack across both sensors (approx samples in cloud)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(cloud_ack) FROM cursors WHERE session_id=?", (session_id,)
            ).fetchone()
            return row[0] or 0

    def backlog_size(self, session_id: str) -> int:
        """Samples saved but not yet acknowledged by cloud."""
        with self._lock:
            row = self._conn.execute(
                """
                SELECT COALESCE(SUM(s.cnt - c.cloud_ack), 0)
                FROM (
                    SELECT session_id, sensor, COUNT(*) as cnt
                    FROM samples WHERE session_id=? GROUP BY session_id, sensor
                ) s
                JOIN cursors c ON c.session_id=s.session_id AND c.sensor=s.sensor
                """,
                (session_id,),
            ).fetchone()
            return row[0] if row else 0
