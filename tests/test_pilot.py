import subprocess
from pathlib import Path

import httpx2
import pytest
from pytest_mock import MockerFixture
from starlette.testclient import TestClient

from simple_framework.admin import Administration
from simple_framework.demo import Demo, setup_demo
from simple_framework.domain import Client, Update
from simple_framework.pilot import PilotFailed, exercise, main, running_server
from simple_framework.server import ServiceBoundary, create_app
from simple_framework.sqlite import SQLiteRepository


async def exercise_in_process(app: ServiceBoundary, demo: Demo, messages: list[str]) -> None:
    async with (
        httpx2.AsyncClient(transport=httpx2.ASGITransport(app), trust_env=False) as alice,
        httpx2.AsyncClient(transport=httpx2.ASGITransport(app), trust_env=False) as bob,
        httpx2.AsyncClient(transport=httpx2.ASGITransport(app), trust_env=False) as reviewer,
    ):
        await exercise(demo, "http://127.0.0.1:8000", alice, bob, reviewer, messages.append)


def test_pilot_runs_twice_and_restores_the_revoked_client(tmp_path: Path) -> None:
    demo = setup_demo(tmp_path / "demo")
    app = create_app(demo.database)
    messages: list[str] = []
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        assert client.portal is not None
        for _ in range(2):
            client.portal.call(exercise_in_process, app, demo, messages)
    assert messages.count("PASS handover recovered after reconnect") == 2
    assert messages.count("PASS revoked client denied") == 2
    assert all(message.startswith("PASS ") for message in messages)
    with SQLiteRepository(demo.database) as repository:
        bob = repository.get(Client, "bob-agent")
        assert bob is not None and bob.active
        updates = repository.records(Update, demo.workspace_id)
        assert len(updates) == 6
        assert len([u for u in updates if u.status == "accepted"]) == 2


def test_pilot_reports_failure_if_revocation_does_not_deny_access(
    tmp_path: Path,
    mocker: MockerFixture,
) -> None:
    demo = setup_demo(tmp_path / "demo")
    app = create_app(demo.database)
    messages: list[str] = []
    access = mocker.patch.object(Administration, "set_client_active")
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        assert client.portal is not None
        with pytest.raises(PilotFailed, match="revoked client denied"):
            client.portal.call(exercise_in_process, app, demo, messages)
    assert "PASS revoked client denied" not in messages
    access.assert_called_with("bob-agent", True)


def test_failed_startup_stops_its_service_process(tmp_path: Path, mocker: MockerFixture) -> None:
    listener = mocker.patch("simple_framework.pilot.socket.socket")
    listener.return_value.__enter__.return_value.getsockname.return_value = ("127.0.0.1", 8000)
    process = mocker.patch("simple_framework.pilot.subprocess.Popen").return_value
    process.poll.return_value = None
    process.wait.side_effect = [subprocess.TimeoutExpired("service", 5), 0]
    mocker.patch("simple_framework.pilot.wait_for_server", side_effect=PilotFailed("startup"))
    with pytest.raises(PilotFailed, match="startup"):
        with running_server(tmp_path / "workspace.sqlite3"):
            pytest.fail("Startup should fail before yielding a URL")
    process.terminate.assert_called_once()
    process.kill.assert_called_once()


def test_pilot_command_exits_nonzero_on_failure(
    tmp_path: Path,
    mocker: MockerFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    demo = setup_demo(tmp_path / "demo")
    server = mocker.patch("simple_framework.pilot.running_server")
    server.return_value.__enter__.return_value = "http://127.0.0.1:8000"
    mocker.patch("simple_framework.pilot.anyio.run", side_effect=PilotFailed("connection"))
    with pytest.raises(SystemExit) as error:
        main(["--directory", str(demo.directory)])
    assert error.value.code == 1
    output = capsys.readouterr()
    assert "FAIL" in output.err
    assert demo.alice_token not in output.out + output.err
