import json
from pathlib import Path
from typing import Any, cast

from pytest_mock import MockerFixture
from typer.testing import CliRunner

from simple_framework.admin import Administration
from simple_framework.auth import authenticate
from simple_framework.cli import app
from simple_framework.harness import Harness
from simple_framework.sqlite import SQLiteRepository


def invoke(
    runner: CliRunner,
    database: Path,
    args: list[str],
    token: str | None = None,
) -> dict[str, Any]:
    result = runner.invoke(app, ["--db", str(database), *args], env={"SIMPLE_TOKEN": token or ""})
    assert result.exit_code == 0, result.output
    return cast(dict[str, Any], json.loads(result.stdout))


def test_cli_reading_group_workflow(tmp_path: Path, mocker: MockerFixture) -> None:
    mocker.patch(
        "simple_framework.runtime.token_urlsafe",
        side_effect=(f"-random-{index}" for index in range(1000)),
    )
    runner = CliRunner()
    database = tmp_path / "reading.sqlite3"
    invoke(runner, database, ["admin", "workspace", "reading", "Reading group"])
    invoke(runner, database, ["admin", "task", "reading", "list", "Choose the first article"])
    alice = invoke(
        runner, database, ["admin", "client", "alice", "alice-agent", "--destination", "local"]
    )
    bob = invoke(
        runner, database, ["admin", "client", "bob", "bob-agent", "--destination", "local"]
    )
    reviewer = invoke(
        runner,
        database,
        [
            "admin",
            "client",
            "bob",
            "bob-review",
            "--destination",
            "local",
            "--role",
            "reviewer",
        ],
    )
    for user in ["alice", "bob"]:
        invoke(runner, database, ["admin", "membership", "reading", user])
    joined = invoke(
        runner, database, ["join", "reading", "list", "--request-id", "join"], alice["token"]
    )
    session = joined["session"]["id"]
    proposal = invoke(
        runner,
        database,
        [
            "post",
            session,
            "proposal",
            "Read the short guide first",
            "--request-id",
            "proposal",
            "--no-sources",
        ],
        alice["token"],
    )
    invoke(
        runner,
        database,
        [
            "review",
            "reading",
            proposal["id"],
            "accepted",
            "--revision",
            "1",
            "--request-id",
            "review",
        ],
        reviewer["token"],
    )
    invoke(
        runner,
        database,
        [
            "end",
            session,
            "--request-id",
            "end",
            "--handover",
            "Choose a second article next",
            "--no-sources",
            "--derived-from",
            proposal["id"] + "@2",
        ],
        alice["token"],
    )
    fresh = invoke(
        runner, database, ["join", "reading", "list", "--request-id", "fresh"], bob["token"]
    )
    assert {(u["kind"], u["status"]) for u in fresh["view"]["updates"]} == {
        ("proposal", "accepted"),
        ("handover", "unreviewed"),
    }
    feed = invoke(
        runner, database, ["updates", "reading", joined["view"]["cursor"]], alice["token"]
    )
    assert feed["changes"][-1]["update"]["derived_from"] == {proposal["id"]: 2}
    assert len(feed["changes"]) == 3
    assert "sequence" not in feed["changes"][0]
    assert alice["token"].encode() not in database.read_bytes()


def test_cli_pages_and_continues_a_task_without_repeating_its_filter(tmp_path: Path) -> None:
    runner = CliRunner()
    database = tmp_path / "reading.sqlite3"
    with SQLiteRepository(database) as repository:
        admin = Administration(repository)
        admin.create_workspace("reading", "Reading group")
        admin.create_task("reading", "first", "First task")
        admin.create_task("reading", "other", "Other task")
        token = admin.register_client("alice", "agent", "local", "contributor")
        admin.set_membership("reading", "alice", True)
    joined = invoke(
        runner,
        database,
        ["join", "reading", "first", "--request-id", "join", "--limit", "1"],
        token,
    )
    assert len(joined["view"]["tasks"]) == 1 and joined["view"]["next_page"]
    with SQLiteRepository(database) as repository:
        harness = Harness(repository)
        caller = authenticate(repository, token)
        for index in range(12):
            harness.post_update(
                caller, joined["session"]["id"], "progress", str(index), str(index), []
            )
    retry = invoke(runner, database, ["join", "reading", "first", "--request-id", "join"], token)
    assert retry["session"]["id"] == joined["session"]["id"]
    assert len(retry["view"]["tasks"]) + len(retry["view"]["updates"]) == 10
    page = invoke(runner, database, ["read", "reading", "--task", "first"], token)
    assert len(page["tasks"]) + len(page["updates"]) == 10
    last = invoke(runner, database, ["read", "reading", "--cursor", page["next_page"]], token)
    assert last["task_id"] == "first" and last["next_page"] is None
    assert [u["text"] for u in last["updates"]] == ["9", "10", "11"]
    changes = invoke(
        runner, database, ["updates", "reading", joined["view"]["cursor"], "--task", "first"], token
    )
    assert len(changes["changes"]) == 10 and changes["has_more"]
    tail = invoke(runner, database, ["updates", "reading", changes["cursor"]], token)
    assert tail["task_id"] == "first" and not tail["has_more"]
    assert [r["update"]["text"] for r in tail["changes"]] == ["10", "11"]


def test_cli_requires_authentication_and_source_declaration(tmp_path: Path) -> None:
    runner = CliRunner()
    database = tmp_path / "reading.sqlite3"
    denied = runner.invoke(
        app, ["--db", str(database), "read", "reading"], env={"SIMPLE_TOKEN": ""}
    )
    assert denied.exit_code == 1
    assert "Access denied" in denied.output
    assert "Traceback" not in denied.output
    result = runner.invoke(
        app,
        [
            "--db",
            str(database),
            "post",
            "session",
            "proposal",
            "Text",
            "--request-id",
            "post",
        ],
    )
    assert result.exit_code == 1
    assert "--source" in result.output
