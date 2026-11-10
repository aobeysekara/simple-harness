import argparse
import socket
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from secrets import token_urlsafe
from tempfile import TemporaryDirectory
from typing import Any, cast

import anyio
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError

from simple_framework.admin import Administration
from simple_framework.demo import Demo, load_demo, setup_demo
from simple_framework.domain import HarnessError
from simple_framework.sqlite import SQLiteRepository


class PilotFailed(RuntimeError):
    """Report a failed pilot step without exposing credentials."""


def check(condition: bool, label: str, report: Callable[[str], None]) -> None:
    """Report a successful check or stop the pilot on failure."""
    if not condition:
        raise PilotFailed(label)
    report(f"PASS {label}")


async def call(client: Client, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Call a collaboration tool and require a successful structured result."""
    result = await client.call_tool(operation, arguments)
    if result.is_error or result.structured_content is None:
        raise PilotFailed(f"{operation} returned a tool error")
    return cast(dict[str, Any], result.structured_content)


def _set_bob_active(demo: Demo, active: bool) -> None:
    with SQLiteRepository(demo.database) as repository:
        Administration(repository).set_client_active("bob-agent", active)


def _failure_message(error: BaseException) -> str:
    if isinstance(error, BaseExceptionGroup):
        return _failure_message(error.exceptions[0])
    if isinstance(error, PilotFailed):
        return str(error)
    return "Service request failed; check that the demo credentials are active"


async def _workflow(
    demo: Demo,
    url: str,
    alice_http: httpx2.AsyncClient,
    bob_http: httpx2.AsyncClient,
    review_http: httpx2.AsyncClient,
    report: Callable[[str], None],
) -> None:
    alice_http.headers["Authorization"] = f"Bearer {demo.alice_token}"
    bob_http.headers["Authorization"] = f"Bearer {demo.bob_token}"
    review_http.headers["Authorization"] = f"Bearer {demo.reviewer_token}"
    run_id = token_urlsafe(12)
    join_args = {
        "workspace_id": demo.workspace_id,
        "task_id": demo.task_id,
        "request_id": run_id,
    }
    async with (
        Client(
            streamable_http_client(url + "/mcp", http_client=alice_http), read_timeout_seconds=10
        ) as alice,
        Client(
            streamable_http_client(url + "/mcp", http_client=bob_http), read_timeout_seconds=10
        ) as bob,
    ):
        joined_alice = await call(alice, "join_workspace", join_args)
        joined_bob = await call(bob, "join_workspace", join_args)
        alice_session = joined_alice["session"]["id"]
        bob_session = joined_bob["session"]["id"]
        check(
            alice_session != bob_session
            and joined_alice["session"]["user"] == "alice"
            and joined_bob["session"]["user"] == "bob",
            "separate MCP client sessions",
            report,
        )
        proposal_args = {
            "session_id": alice_session,
            "kind": "proposal",
            "text": "Read the short guide first",
            "sources": [],
            "request_id": run_id + "-proposal",
        }
        proposal = await call(alice, "post_update", proposal_args)
        retry = await call(alice, "post_update", proposal_args)
        check(proposal["id"] == retry["id"], "retried proposal saved once", report)
        question = await call(
            bob,
            "post_update",
            {
                "session_id": bob_session,
                "kind": "question",
                "text": "Which chapter should we read?",
                "sources": [],
                "request_id": run_id + "-question",
            },
        )
        check(
            proposal["author"] == "alice" and question["author"] == "bob",
            "proposal and question attributed",
            report,
        )
        review = await review_http.post(
            url + "/api/review_update",
            json={
                "workspace_id": demo.workspace_id,
                "update_id": proposal["id"],
                "expected_revision": proposal["revision"],
                "status": "accepted",
                "request_id": run_id + "-review",
            },
        )
        review.raise_for_status()
        check(review.json()["status"] == "accepted", "separate reviewer accepted proposal", report)
        await call(
            alice,
            "end_session",
            {
                "session_id": alice_session,
                "request_id": run_id + "-end",
                "handover": "The reading order is agreed. Choose a chapter next.",
                "sources": [],
                "derived_from": [
                    {"update_id": proposal["id"], "revision": review.json()["revision"]}
                ],
            },
        )
    async with Client(
        streamable_http_client(url + "/mcp", http_client=bob_http), read_timeout_seconds=10
    ) as fresh:
        resumed = await call(
            fresh,
            "join_workspace",
            {
                **join_args,
                "request_id": run_id + "-resume",
                "session_id": bob_session,
            },
        )
        changes = await call(
            fresh,
            "get_updates",
            {
                "workspace_id": demo.workspace_id,
                "cursor": joined_bob["view"]["cursor"],
            },
        )
        updates = [change["update"] for change in changes["changes"]]
        check(
            resumed["session"]["id"] == bob_session
            and any(u["kind"] == "handover" and u["session_id"] == alice_session for u in updates)
            and any(u["id"] == proposal["id"] and u["status"] == "accepted" for u in updates),
            "handover recovered after reconnect",
            report,
        )
        await call(
            fresh,
            "end_session",
            {
                "session_id": bob_session,
                "request_id": run_id + "-bob-end",
            },
        )
    try:
        await anyio.to_thread.run_sync(_set_bob_active, demo, False)
        denied = await bob_http.post(
            url + "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {},
            },
        )
        check(denied.status_code == 401, "revoked client denied", report)
    finally:
        await anyio.to_thread.run_sync(_set_bob_active, demo, True)
    restored = await bob_http.post(
        url + "/api/read_workspace",
        json={
            "workspace_id": demo.workspace_id,
            "task_id": demo.task_id,
        },
    )
    check(restored.status_code == 200, "demo client access restored", report)


async def exercise(
    demo: Demo,
    url: str,
    alice_http: httpx2.AsyncClient,
    bob_http: httpx2.AsyncClient,
    review_http: httpx2.AsyncClient,
    report: Callable[[str], None] = print,
) -> None:
    """Run collaboration and revocation checks using separate MCP and reviewer clients."""
    try:
        await _workflow(demo, url, alice_http, bob_http, review_http, report)
    except* (PilotFailed, httpx2.HTTPError, MCPError) as e:
        raise PilotFailed(_failure_message(e)) from None


async def run_clients(demo: Demo, url: str) -> None:
    """Connect the pilot's three credential sets to its local service."""
    async with (
        httpx2.AsyncClient(timeout=10, trust_env=False) as alice,
        httpx2.AsyncClient(timeout=10, trust_env=False) as bob,
        httpx2.AsyncClient(timeout=10, trust_env=False) as reviewer,
    ):
        await exercise(demo, url, alice, bob, reviewer)


def wait_for_server(process: subprocess.Popen[bytes], url: str) -> None:
    """Wait briefly for the local service to require authentication."""
    deadline = time.monotonic() + 10
    with httpx2.Client(timeout=0.25, trust_env=False) as client:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise PilotFailed("Local service exited before startup")
            try:
                response = client.get(url + "/mcp")
                if response.status_code == 401:
                    return
            except (httpx2.ConnectError, httpx2.TimeoutException):
                pass
            time.sleep(0.05)
    raise PilotFailed("Local service did not start within ten seconds")


@contextmanager
def running_server(database: Path) -> Iterator[str]:
    """Start a temporary loopback service and stop it when the pilot exits."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "simple_framework.server",
            "--db",
            str(database),
            "--port",
            str(port),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_for_server(process, url)
        yield url
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def _run(demo: Demo) -> None:
    with running_server(demo.database) as url:
        print(f"Service: {url}", flush=True)
        anyio.run(run_clients, demo, url)
    print("PASS local collaboration pilot; service stopped")


def main(argv: list[str] | None = None) -> None:
    """Run a temporary pilot or reuse a separately provisioned demo directory."""
    parser = argparse.ArgumentParser(description="Run the local two-client collaboration pilot")
    parser.add_argument("--directory", type=Path, help="Reuse a directory from simple demo setup")
    args = parser.parse_args(argv)
    try:
        if args.directory is not None:
            _run(load_demo(args.directory))
        else:
            with TemporaryDirectory(prefix="simple-pilot-") as directory:
                _run(setup_demo(Path(directory) / "demo"))
    except (PilotFailed, HarnessError, OSError, sqlite3.Error) as e:
        print(f"FAIL {e}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
