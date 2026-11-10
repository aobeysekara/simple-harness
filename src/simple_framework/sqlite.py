import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from types import TracebackType
from typing import Self

from simple_framework.domain import Change, InvalidRequest, Record, Update


class SQLiteRepository:
    """Persist core records and revision history in one local database."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        self.connection = sqlite3.connect(path, isolation_level=None, timeout=10)
        initialised = False
        try:
            self._initialise()
            initialised = True
        finally:
            if not initialised:
                self.connection.close()

    def _initialise(self) -> None:
        self.connection.execute("PRAGMA foreign_keys = ON")
        with self.transaction():
            version = self.connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise InvalidRequest("Unsupported database schema version")
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS records ("
                "record_type TEXT NOT NULL, id TEXT NOT NULL, workspace_id TEXT NOT NULL, "
                "payload TEXT NOT NULL, PRIMARY KEY (record_type, workspace_id, id))"
            )
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS changes ("
                "sequence INTEGER PRIMARY KEY AUTOINCREMENT, workspace_id TEXT NOT NULL, "
                "payload TEXT NOT NULL, actor TEXT NOT NULL, client TEXT NOT NULL)"
            )
            self.connection.execute(
                "CREATE INDEX IF NOT EXISTS changes_workspace ON changes(workspace_id, sequence)"
            )
            self.connection.execute("PRAGMA user_version = 1")

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.connection.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Hold one writer lock through policy checks and response preparation."""
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.connection.commit()
        finally:
            if self.connection.in_transaction:
                self.connection.rollback()

    def get[T: Record](
        self,
        record_type: type[T],
        record_id: str,
        workspace_id: str | None = None,
    ) -> T | None:
        """Read one stored record."""
        query = "SELECT payload FROM records WHERE record_type = ? AND id = ?"
        values = [record_type.__name__, record_id]
        if workspace_id is not None:
            query += " AND workspace_id = ?"
            values.append(workspace_id)
        row = self.connection.execute(query, values).fetchone()
        return record_type(**json.loads(row[0])) if row else None

    def records[T: Record](self, record_type: type[T], workspace_id: str | None = None) -> list[T]:
        """Read records in stable ID order."""
        query = "SELECT payload FROM records WHERE record_type = ?"
        values = [record_type.__name__]
        if workspace_id is not None:
            query += " AND workspace_id = ?"
            values.append(workspace_id)
        rows = self.connection.execute(query + " ORDER BY id", values).fetchall()
        return [record_type(**json.loads(row[0])) for row in rows]

    def save(self, record: Record) -> None:
        """Insert or replace the current record within an explicit transaction."""
        if not self.connection.in_transaction:
            raise RuntimeError("Writes require an active transaction")
        self.connection.execute(
            "INSERT INTO records VALUES (?, ?, ?, ?) "
            "ON CONFLICT (record_type, workspace_id, id) DO UPDATE SET payload = excluded.payload",
            (type(record).__name__, record.id, record.workspace_id, json.dumps(asdict(record))),
        )

    def append_change(self, update: Update, actor: str, client: str) -> None:
        """Append an immutable update revision within an explicit transaction."""
        if not self.connection.in_transaction:
            raise RuntimeError("Writes require an active transaction")
        self.connection.execute(
            "INSERT INTO changes (workspace_id, payload, actor, client) VALUES (?, ?, ?, ?)",
            (update.workspace_id, json.dumps(asdict(update)), actor, client),
        )

    def changes(self, workspace_id: str, after: int = 0) -> list[Change]:
        """Read workspace changes after the exclusive sequence position."""
        rows = self.connection.execute(
            "SELECT sequence, payload, actor, client FROM changes "
            "WHERE workspace_id = ? AND sequence > ? ORDER BY sequence",
            (workspace_id, after),
        ).fetchall()
        return [Change(row[0], Update(**json.loads(row[1])), row[2], row[3]) for row in rows]
