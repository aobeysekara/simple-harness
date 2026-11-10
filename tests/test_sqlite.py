import json
import sqlite3
import stat
from pathlib import Path

import pytest

from simple_framework.domain import Update
from simple_framework.sqlite import SQLiteRepository


def test_new_database_is_private_to_the_local_operator(tmp_path: Path) -> None:
    path = tmp_path / "workspace.sqlite3"
    with SQLiteRepository(path):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_unsupported_schema_is_rejected_without_changing_it(tmp_path: Path) -> None:
    path = tmp_path / "future.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version = 999")
    with pytest.raises(ValueError, match="Unsupported database schema"):
        SQLiteRepository(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 999


@pytest.mark.parametrize("operation", ["save", "append_change"])
def test_writes_require_an_explicit_transaction(tmp_path: Path, operation: str) -> None:
    with SQLiteRepository(tmp_path / "guard.sqlite3") as repository:
        update = Update(
            id="update",
            workspace_id="team",
            session_id="session",
            task_id="task",
            author="alice",
            client="agent",
            kind="finding",
            text="Result",
            sources=[],
            derived_from={},
            created_at="now",
            changed_at="now",
        )
        with pytest.raises(RuntimeError, match="transaction"):
            if operation == "save":
                repository.save(update)
            else:
                repository.append_change(update, "alice", "agent")
        assert repository.records(Update) == []
        assert repository.changes("team") == []


def test_change_query_starts_after_the_requested_sequence(tmp_path: Path) -> None:
    with SQLiteRepository(tmp_path / "history.sqlite3") as repository:
        update = Update(
            id="update",
            workspace_id="team",
            session_id="session",
            task_id="task",
            author="alice",
            client="agent",
            kind="finding",
            text="Result",
            sources=[],
            derived_from={},
            created_at="now",
            changed_at="now",
        )
        with repository.transaction():
            repository.append_change(update, "alice", "agent")
            repository.append_change(update, "alice", "agent")
        first, second = repository.changes("team")
        assert repository.changes("team", after=first.sequence) == [second]
        assert repository.changes("team", after=second.sequence) == []


def test_legacy_dependency_history_remains_readable(tmp_path: Path) -> None:
    payload = json.dumps(
        {
            "id": "legacy",
            "workspace_id": "team",
            "session_id": "old-session",
            "task_id": "task",
            "author": "alice",
            "client": "agent",
            "kind": "finding",
            "text": "Previously recorded",
            "sources": [],
            "derived_from": {"earlier": 3},
            "created_at": "then",
            "changed_at": "then",
            "revision": 1,
            "status": "proposed",
            "reviewed_by": None,
        }
    )
    path = tmp_path / "legacy.sqlite3"
    with SQLiteRepository(path) as repository, repository.transaction():
        repository.connection.execute(
            "INSERT INTO records VALUES (?, ?, ?, ?)", ("Update", "legacy", "team", payload)
        )
        repository.connection.execute(
            "INSERT INTO changes (workspace_id, payload, actor, client) VALUES (?, ?, ?, ?)",
            ("team", payload, "alice", "agent"),
        )
    with SQLiteRepository(path) as reopened:
        update = reopened.get(Update, "legacy", "team")
        assert update is not None
        assert update.derived_from == {"earlier": 3}
        assert reopened.changes("team")[0].update == update
