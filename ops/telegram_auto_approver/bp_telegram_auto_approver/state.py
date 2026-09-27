from __future__ import annotations

import os
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY,
    telegram_chat_id INTEGER NOT NULL,
    telegram_message_id INTEGER NOT NULL,
    nonce TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN (
            'reserved',
            'clicked',
            'would_approve',
            'failed_or_unknown',
            'rejected'
        )
    ),
    reason TEXT,
    side TEXT,
    limit_price TEXT,
    shares TEXT,
    maximum_spend TEXT,
    time_remaining_seconds TEXT,
    deadline_at TEXT,
    received_at TEXT NOT NULL,
    approval_at TEXT,
    result TEXT,
    failure_reason TEXT,
    intent_id TEXT,
    prediction_id TEXT,
    request_sha256 TEXT,
    listener_decision_at TEXT,
    listener_edit_observed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (telegram_chat_id, telegram_message_id),
    UNIQUE (nonce)
);
"""

_INSERT_STATUSES = frozenset({"reserved", "would_approve", "rejected"})
_RESERVED_TRANSITIONS = frozenset({"clicked", "failed_or_unknown"})


class StateError(RuntimeError):
    pass


@dataclass(frozen=True)
class NewApproval:
    telegram_chat_id: int
    telegram_message_id: int
    nonce: str
    status: str
    reason: str | None
    side: str
    limit_price: str
    shares: str
    maximum_spend: str
    time_remaining_seconds: str
    deadline_at: str
    received_at: str
    approval_at: str | None
    result: str
    failure_reason: str | None


@dataclass(frozen=True)
class InsertResult:
    created: bool
    row_id: int | None


class ApprovalStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            path,
            isolation_level=None,
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        self._require_schema()
        self._tighten()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def insert(self, row: NewApproval) -> InsertResult:
        if row.status not in _INSERT_STATUSES:
            raise StateError("insert status is not reservable")
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                cursor = self._conn.execute(
                    """
                    INSERT INTO approvals (
                        telegram_chat_id, telegram_message_id, nonce, status, reason,
                        side, limit_price, shares, maximum_spend,
                        time_remaining_seconds, deadline_at, received_at, approval_at,
                        result, failure_reason, intent_id, prediction_id, request_sha256,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?)
                    """,
                    (
                        row.telegram_chat_id,
                        row.telegram_message_id,
                        row.nonce,
                        row.status,
                        row.reason,
                        row.side,
                        row.limit_price,
                        row.shares,
                        row.maximum_spend,
                        row.time_remaining_seconds,
                        row.deadline_at,
                        row.received_at,
                        row.approval_at,
                        row.result,
                        row.failure_reason,
                        row.received_at,
                        row.received_at,
                    ),
                )
                self._conn.execute("COMMIT")
            except sqlite3.IntegrityError:
                self._rollback()
                return InsertResult(created=False, row_id=None)
            except Exception:
                self._rollback()
                raise
            self._tighten()
            return InsertResult(created=True, row_id=int(cursor.lastrowid))

    def transition_reserved(
        self,
        row_id: int,
        status: str,
        *,
        updated_at: str,
        approval_at: str | None,
        result: str,
        failure_reason: str | None,
    ) -> bool:
        if status not in _RESERVED_TRANSITIONS:
            raise StateError("reserved transition is invalid")
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = self._conn.execute(
                    """
                    UPDATE approvals
                    SET status=?, approval_at=?, result=?, failure_reason=?, updated_at=?
                    WHERE id=? AND status='reserved'
                    """,
                    (status, approval_at, result, failure_reason, updated_at, row_id),
                )
                changed = cursor.rowcount == 1
                self._conn.execute("COMMIT")
            except Exception:
                self._rollback()
                raise
            return changed

    def sweep_unconfirmed(self, updated_at: str) -> int:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = self._conn.execute(
                    """
                    UPDATE approvals
                    SET status='failed_or_unknown',
                        result='failed_or_unknown',
                        failure_reason='startup_found_unconfirmed_reservation',
                        updated_at=?
                    WHERE status='reserved'
                    """,
                    (updated_at,),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._rollback()
                raise
            return int(cursor.rowcount)

    def record_listener_edit(
        self,
        *,
        telegram_chat_id: int,
        telegram_message_id: int,
        intent_id: str,
        listener_decision_at: str,
        observed_at: str,
    ) -> str:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                current = self._conn.execute(
                    """
                    SELECT intent_id FROM approvals
                    WHERE telegram_chat_id=? AND telegram_message_id=?
                    """,
                    (telegram_chat_id, telegram_message_id),
                ).fetchone()
                if current is None:
                    self._conn.execute("COMMIT")
                    return "missing"
                existing = current["intent_id"]
                if existing and existing != intent_id:
                    self._conn.execute("COMMIT")
                    return "conflict"
                self._conn.execute(
                    """
                    UPDATE approvals
                    SET intent_id=?,
                        listener_decision_at=?,
                        listener_edit_observed_at=?,
                        updated_at=?
                    WHERE telegram_chat_id=? AND telegram_message_id=?
                    """,
                    (
                        intent_id,
                        listener_decision_at,
                        observed_at,
                        observed_at,
                        telegram_chat_id,
                        telegram_message_id,
                    ),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._rollback()
                raise
            return "recorded"

    def get_by_message(self, telegram_chat_id: int, telegram_message_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM approvals
                WHERE telegram_chat_id=? AND telegram_message_id=?
                """,
                (telegram_chat_id, telegram_message_id),
            ).fetchone()
        return dict(row) if row is not None else None

    def get_by_nonce(self, nonce: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM approvals WHERE nonce=?",
                (nonce,),
            ).fetchone()
        return dict(row) if row is not None else None

    def _require_schema(self) -> None:
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()
        if row is None:
            self._conn.execute(
                "INSERT INTO meta (key, value) VALUES ('schema_version', '1')"
            )
            return
        if row["value"] != "1":
            raise StateError("unsupported approval state schema")

    def _rollback(self) -> None:
        try:
            self._conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass

    def _tighten(self) -> None:
        for extra in ("", "-wal", "-shm"):
            candidate = Path(f"{self.path}{extra}")
            if candidate.exists():
                os.chmod(candidate, 0o600)
