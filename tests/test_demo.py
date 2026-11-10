import json
import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

from simple_framework.auth import authenticate
from simple_framework.cli import app
from simple_framework.demo import load_demo, setup_demo
from simple_framework.domain import Client, InvalidRequest, Task
from simple_framework.sqlite import SQLiteRepository


def test_demo_setup_saves_separate_credentials_without_printing_them(tmp_path: Path) -> None:
    directory = tmp_path / "demo"
    result = CliRunner().invoke(app, ["demo", "setup", "--directory", str(directory)])
    assert result.exit_code == 0, result.output
    demo = load_demo(directory)
    assert json.loads(result.stdout)["database"] == str(demo.database)
    tokens = [demo.alice_token, demo.bob_token, demo.reviewer_token]
    assert len(set(tokens)) == 3
    for token in tokens:
        assert token not in result.output
        assert token not in repr(demo)
    for path in directory.glob("*.token"):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with SQLiteRepository(demo.database) as repository:
        assert repository.get(Task, demo.task_id, demo.workspace_id) is not None
        for token, user, role in [
            (demo.alice_token, "alice", "contributor"),
            (demo.bob_token, "bob", "contributor"),
            (demo.reviewer_token, "bob", "reviewer"),
        ]:
            caller = authenticate(repository, token)
            client = repository.get(Client, caller.client)
            assert caller.user == user
            assert client is not None and client.role == role


def test_demo_setup_refuses_to_overwrite_existing_files(tmp_path: Path) -> None:
    directory = tmp_path / "existing"
    directory.mkdir()
    marker = directory / "alice.token"
    marker.write_text("keep this file")
    with pytest.raises(InvalidRequest, match="already exists"):
        setup_demo(directory)
    assert marker.read_text() == "keep this file"
    assert not (directory / "workspace.sqlite3").exists()


def test_missing_demo_points_to_the_setup_command(tmp_path: Path) -> None:
    with pytest.raises(InvalidRequest, match="simple demo setup"):
        load_demo(tmp_path / "missing")
