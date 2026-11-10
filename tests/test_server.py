import json
from collections.abc import Callable, Iterator
from functools import partial
from pathlib import Path
from typing import Any, cast

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from pytest_mock import MockerFixture
from starlette.testclient import TestClient
from typer.testing import CliRunner

from simple_framework.admin import Administration
from simple_framework.auth import authenticate
from simple_framework.cli import app as cli_app
from simple_framework.domain import Session
from simple_framework.harness import Harness
from simple_framework.server import ServiceBoundary, create_app
from simple_framework.service import Service
from simple_framework.sqlite import SQLiteRepository

type Pilot = tuple[Path, str, str, str]


@pytest.fixture
def pilot(tmp_path: Path) -> Pilot:
    database = tmp_path / "service.sqlite3"
    with SQLiteRepository(database) as repository:
        admin = Administration(repository)
        admin.create_workspace("reading", "Reading group")
        admin.create_task("reading", "first", "Choose an article")
        alice = admin.register_client("alice", "codex", "pilot", "contributor")
        bob = admin.register_client("bob", "claude", "pilot", "contributor")
        reviewer = admin.register_client("bob", "review", "pilot", "reviewer")
        admin.set_membership("reading", "alice", True)
        admin.set_membership("reading", "bob", True)
    return database, alice, bob, reviewer


@pytest.fixture
def client(pilot: Pilot) -> Iterator[TestClient]:
    with TestClient(create_app(pilot[0]), base_url="http://127.0.0.1:8000") as client:
        yield client


def headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2025-11-25",
    }


def rpc(client: TestClient, token: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
    response = client.post(
        "/mcp",
        headers=headers(token),
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
    )
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def tool(client: TestClient, token: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = rpc(client, token, "tools/call", {"name": name, "arguments": arguments})["result"]
    assert not result.get("isError"), result
    return cast(dict[str, Any], result["structuredContent"])


def join(client: TestClient, token: str, request_id: str = "join") -> dict[str, Any]:
    return tool(
        client,
        token,
        "join_workspace",
        {
            "workspace_id": "reading",
            "task_id": "first",
            "request_id": request_id,
        },
    )


def test_two_mcp_clients_share_review_and_reconnect(pilot: Pilot, client: TestClient) -> None:
    database, alice, bob, reviewer = pilot
    for token, name in [(alice, "codex"), (bob, "claude")]:
        initialised = rpc(
            client,
            token,
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": name, "version": "test"},
            },
        )
        assert initialised["result"]["serverInfo"]["name"] == "SIMple"
    available = rpc(client, alice, "tools/list", {})["result"]["tools"]
    assert {t["name"] for t in available} == {
        "open_session",
        "join_workspace",
        "read_workspace",
        "post_update",
        "get_updates",
        "end_session",
        "prepare_context",
    }
    alice_session = join(client, alice)
    bob_session = join(client, bob)
    proposal = tool(
        client,
        alice,
        "post_update",
        {
            "session_id": alice_session["session"]["id"],
            "kind": "proposal",
            "text": "Read the guide",
            "request_id": "proposal",
            "sources": [],
        },
    )
    tool(
        client,
        bob,
        "post_update",
        {
            "session_id": bob_session["session"]["id"],
            "kind": "question",
            "text": "Which chapter?",
            "request_id": "question",
            "sources": [],
        },
    )
    review = client.post(
        "/api/review_update",
        headers=headers(reviewer),
        json={
            "workspace_id": "reading",
            "update_id": proposal["id"],
            "expected_revision": 1,
            "status": "accepted",
            "request_id": "review",
        },
    )
    assert review.status_code == 200
    tool(
        client,
        alice,
        "end_session",
        {
            "session_id": alice_session["session"]["id"],
            "request_id": "end",
            "handover": "Choose a chapter next",
            "sources": [],
            "derived_from": [{"update_id": proposal["id"], "revision": review.json()["revision"]}],
        },
    )
    with TestClient(create_app(database), base_url="http://127.0.0.1:8000") as reconnected:
        resumed = tool(
            reconnected,
            bob,
            "join_workspace",
            {
                "workspace_id": "reading",
                "task_id": "first",
                "request_id": "resume",
                "session_id": bob_session["session"]["id"],
            },
        )
        assert resumed["session"]["id"] == bob_session["session"]["id"]
        assert {u["kind"] for u in resumed["view"]["updates"]} == {
            "proposal",
            "question",
            "handover",
        }
        changes = tool(
            reconnected,
            bob,
            "get_updates",
            {
                "workspace_id": "reading",
                "cursor": bob_session["view"]["cursor"],
            },
        )
        assert len(changes["changes"]) == 4
        assert changes["changes"][2]["update"]["status"] == "accepted"


@pytest.mark.parametrize("endpoint", ["/mcp", "/api/read_workspace"])
def test_every_transport_request_requires_current_credentials(
    pilot: Pilot,
    client: TestClient,
    endpoint: str,
) -> None:
    database, alice, _, _ = pilot
    assert client.post(endpoint, json={}).status_code == 401
    join(client, alice)
    with SQLiteRepository(database) as repository:
        Administration(repository).revoke_credential(alice)
    response = client.post(endpoint, headers=headers(alice), json={})
    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store"
    assert alice not in response.text


def test_remote_requests_cannot_spoof_identity_or_use_admin_routes(
    pilot: Pilot,
    client: TestClient,
) -> None:
    _, alice, bob, _ = pilot
    session = join(client, alice)["session"]["id"]
    payload = {
        "session_id": session,
        "kind": "progress",
        "text": "Done",
        "sources": [],
        "request_id": "post",
    }
    denied = client.post("/api/post_update", headers=headers(bob), json=payload)
    assert denied.status_code == 403
    spoofed = client.post(
        "/api/post_update", headers=headers(bob), json={**payload, "caller": "alice"}
    )
    assert spoofed.status_code == 422
    assert client.post("/api/register_client", headers=headers(alice), json={}).status_code == 404
    denied_tool = rpc(client, bob, "tools/call", {"name": "post_update", "arguments": payload})
    assert denied_tool["result"]["isError"]


def revoke_before_release(
    execute: Callable[..., dict[str, Any]],
    database: Path,
    token: str,
    revoke_source: bool,
    *args: Any,
    **kwargs: Any,
) -> dict[str, Any]:
    result = execute(*args, **kwargs)
    with SQLiteRepository(database) as repository:
        admin = Administration(repository)
        if revoke_source:
            admin.set_source_access("reading", "guide", [], [])
        else:
            admin.revoke_credential(token)
    return result


@pytest.mark.parametrize("transport", ["http", "mcp"])
@pytest.mark.parametrize("revoke_source", [False, True])
def test_revocation_during_response_preparation_prevents_release(
    pilot: Pilot,
    client: TestClient,
    mocker: MockerFixture,
    transport: str,
    revoke_source: bool,
) -> None:
    database, alice, _, _ = pilot
    with SQLiteRepository(database) as repository:
        Administration(repository).register_source(
            "reading",
            "guide",
            "https://example.org/guide",
            ["alice"],
            ["codex"],
        )
    joined = join(client, alice)
    tool(
        client,
        alice,
        "post_update",
        {
            "session_id": joined["session"]["id"],
            "kind": "progress",
            "text": "private result",
            "sources": ["guide"],
            "request_id": "post",
        },
    )
    mocker.patch.object(
        Service,
        "execute",
        autospec=True,
        side_effect=partial(revoke_before_release, Service.execute, database, alice, revoke_source),
    )
    if transport == "http":
        response = client.post(
            "/api/read_workspace", headers=headers(alice), json={"workspace_id": "reading"}
        )
    else:
        response = client.post(
            "/mcp",
            headers=headers(alice),
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "read_workspace", "arguments": {"workspace_id": "reading"}},
            },
        )
    if transport == "mcp":
        assert response.status_code == 200
        assert response.json()["result"]["isError"]
        assert "structuredContent" not in response.json()["result"]
    else:
        assert response.status_code == (409 if revoke_source else 401)
    assert "private result" not in response.text


@pytest.mark.parametrize(
    "extra_headers,status",
    [
        ({"Host": "untrusted.example"}, 421),
        ({"Origin": "https://untrusted.example"}, 403),
    ],
)
def test_service_rejects_unapproved_hosts_and_origins(
    pilot: Pilot,
    client: TestClient,
    extra_headers: dict[str, str],
    status: int,
) -> None:
    response = client.post(
        "/api/read_workspace",
        headers={**headers(pilot[1]), **extra_headers},
        json={"workspace_id": "reading"},
    )
    assert response.status_code == status


def test_remote_urls_require_https() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        create_app(Path("unused.sqlite3"), public_url="http://shared.example")


async def sdk_exchange(app: ServiceBoundary, token: str) -> None:
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app),
        headers={"Authorization": f"Bearer {token}"},
    ) as http:
        transport = streamable_http_client("http://127.0.0.1:8000/mcp", http_client=http)
        async with Client(transport) as client:
            tools = await client.list_tools()
            assert len(tools.tools) == 7
            joined = await client.call_tool(
                "join_workspace",
                {
                    "workspace_id": "reading",
                    "task_id": "first",
                    "request_id": "sdk-join",
                },
            )
            assert not joined.is_error
            assert joined.structured_content is not None
            assert joined.structured_content["session"]["user"] == "alice"


def test_official_mcp_client_negotiates_and_calls_the_service(pilot: Pilot) -> None:
    app = create_app(pilot[0])
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        assert client.portal is not None
        client.portal.call(sdk_exchange, app, pilot[1])


def test_invalid_request_and_oversized_body_have_clear_errors(
    pilot: Pilot, client: TestClient
) -> None:
    joined = join(client, pilot[1])
    invalid = client.post(
        "/api/post_update",
        headers=headers(pilot[1]),
        json={
            "session_id": joined["session"]["id"],
            "kind": "unknown",
            "text": "Text",
            "request_id": "invalid",
            "sources": [],
        },
    )
    assert invalid.status_code == 422
    malformed = client.post(
        "/api/read_workspace",
        headers=headers(pilot[1]),
        content=b"\xff",
    )
    assert malformed.status_code == 422
    oversized = client.post("/api/read_workspace", headers=headers(pilot[1]), content=b"x" * 262145)
    assert oversized.status_code == 413


@pytest.mark.parametrize("failure", ["kind", "filter", "access"])
def test_mcp_domain_errors_match_http_without_disclosing_private_details(
    pilot: Pilot, client: TestClient, failure: str
) -> None:
    _, alice, bob, _ = pilot
    joined = join(client, alice)
    token = alice
    if failure == "filter":
        operation = "get_updates"
        narrowed = tool(
            client,
            alice,
            "get_updates",
            {
                "workspace_id": "reading",
                "cursor": joined["view"]["cursor"],
                "task_id": "first",
            },
        )
        arguments = {"workspace_id": "reading", "cursor": narrowed["cursor"], "task_id": "other"}
        expected = "Cursor policy, filter or operation changed"
    else:
        operation = "post_update"
        arguments = {
            "session_id": joined["session"]["id"],
            "kind": "outcome",
            "text": "Result",
            "request_id": "outcome",
            "sources": [],
        }
        expected = "Unknown update kind"
        if failure == "access":
            token = bob
            arguments["kind"] = "finding"
            expected = "Access denied"
    http = client.post(f"/api/{operation}", headers=headers(token), json=arguments)
    mcp = rpc(client, token, "tools/call", {"name": operation, "arguments": arguments})["result"]
    assert expected in http.json()["error"]
    assert mcp["isError"]
    assert expected in mcp["content"][0]["text"]
    assert alice not in str(mcp) and bob not in str(mcp)


@pytest.mark.parametrize("transport", ["http", "mcp"])
def test_small_pages_and_inherited_task_scope_through_transports(
    pilot: Pilot, client: TestClient, transport: str
) -> None:
    database, alice, _, _ = pilot

    def call(operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if transport == "mcp":
            return tool(client, alice, operation, arguments)
        response = client.post(f"/api/{operation}", headers=headers(alice), json=arguments)
        assert response.status_code == 200, response.text
        return cast(dict[str, Any], response.json())

    with SQLiteRepository(database) as repository:
        Administration(repository).create_task("reading", "other", "Other task")
    joined = call(
        "join_workspace",
        {"workspace_id": "reading", "task_id": "first", "request_id": "join", "limit": 1},
    )
    assert len(joined["view"]["tasks"]) == 1 and joined["view"]["next_page"]
    assert joined["view"]["task_id"] is None
    with SQLiteRepository(database) as repository:
        harness = Harness(repository)
        caller = authenticate(repository, alice)
        other = harness.join_workspace(caller, "reading", "other", "other")
        for index in range(12):
            harness.post_update(
                caller, joined["session"]["id"], "progress", str(index), str(index), []
            )
        harness.post_update(caller, other.session.id, "progress", "Elsewhere", "elsewhere", [])
    retry = call(
        "join_workspace", {"workspace_id": "reading", "task_id": "first", "request_id": "join"}
    )
    assert retry["session"]["id"] == joined["session"]["id"]
    assert len(retry["view"]["tasks"]) + len(retry["view"]["updates"]) == 10
    snapshot = call("read_workspace", {"workspace_id": "reading", "task_id": "first"})
    assert len(snapshot["tasks"]) + len(snapshot["updates"]) == 10
    continued = call("read_workspace", {"workspace_id": "reading", "cursor": snapshot["next_page"]})
    assert continued["task_id"] == "first" and continued["next_page"] is None
    assert [u["text"] for u in continued["updates"]] == ["9", "10", "11"]
    page = call(
        "get_updates",
        {"workspace_id": "reading", "cursor": joined["view"]["cursor"], "task_id": "first"},
    )
    assert len(page["changes"]) == 10 and page["has_more"] and page["task_id"] == "first"
    last = call("get_updates", {"workspace_id": "reading", "cursor": page["cursor"]})
    assert not last["has_more"] and last["task_id"] == "first"
    assert [r["update"]["text"] for r in last["changes"]] == ["10", "11"]


def change_policy_after_execution(
    execute: Callable[..., dict[str, Any]], database: Path, *args: Any, **kwargs: Any
) -> dict[str, Any]:
    result = execute(*args, **kwargs)
    with SQLiteRepository(database) as repository:
        Administration(repository).create_workspace("unrelated", "Unrelated")
    return result


@pytest.mark.parametrize("transport", ["http", "mcp", "sdk"])
@pytest.mark.parametrize("operation", ["post_update", "open_session"])
def test_withheld_committed_write_requires_exact_retry(
    pilot: Pilot, client: TestClient, mocker: MockerFixture, transport: str, operation: str
) -> None:
    database, alice, _, _ = pilot
    joined = join(client, alice)
    arguments = {
        "session_id": joined["session"]["id"],
        "kind": "progress",
        "text": "Saved once",
        "request_id": "write",
        "sources": [],
    }
    if operation == "open_session":
        arguments = {"workspace_id": "reading", "task_id": "first", "request_id": "write"}
    mocker.patch.object(
        Service,
        "execute",
        autospec=True,
        side_effect=partial(change_policy_after_execution, Service.execute, database),
    )
    if transport == "http":
        response = client.post(f"/api/{operation}", headers=headers(alice), json=arguments)
        assert response.status_code == 503
        assert response.json()["code"] == "write_result_withheld"
        message = response.json()["error"]
    else:
        if transport == "mcp":
            result = rpc(
                client,
                alice,
                "tools/call",
                {
                    "name": operation,
                    "arguments": arguments,
                },
            )["result"]
        else:
            app = create_app(database)
            with TestClient(app, base_url="http://127.0.0.1:8000") as sdk_host:
                assert sdk_host.portal is not None
                result = sdk_host.portal.call(sdk_write, app, alice, arguments, operation)
        assert result["isError"]
        message = result["content"][0]["text"]
    assert "may have committed" in message
    assert "same request ID" in message
    mocker.stopall()
    retried = tool(client, alice, operation, arguments)
    with SQLiteRepository(database) as repository:
        if operation == "open_session":
            assert retried["task_id"] == "first"
            assert len(repository.records(Session, "reading")) == 2
            assert not repository.changes("reading")
        else:
            assert retried["revision"] == 1
            assert len(repository.changes("reading")) == 1


async def sdk_write(
    app: ServiceBoundary, token: str, arguments: dict[str, Any], operation: str
) -> dict[str, Any]:
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app), headers={"Authorization": f"Bearer {token}"}
    ) as http:
        async with Client(
            streamable_http_client("http://127.0.0.1:8000/mcp", http_client=http)
        ) as client:
            result = await client.call_tool(operation, arguments)
            return result.model_dump(by_alias=True)


@pytest.mark.parametrize("transport", ["http", "mcp"])
@pytest.mark.parametrize("reference", ["id-only", "stale", "missing"])
def test_dependency_contract_is_enforced_by_both_transports(
    pilot: Pilot, client: TestClient, transport: str, reference: str
) -> None:
    _, alice, _, _ = pilot
    joined = join(client, alice)
    arguments = {
        "session_id": joined["session"]["id"],
        "kind": "finding",
        "text": "Initial",
        "request_id": "initial",
        "sources": [],
    }
    finding = tool(client, alice, "post_update", arguments)
    arguments.update(
        text="Correction", request_id="correction", update_id=finding["id"], expected_revision=1
    )
    tool(client, alice, "post_update", arguments)
    references = {
        "id-only": [finding["id"]],
        "stale": [{"update_id": finding["id"], "revision": 1}],
        "missing": [],
    }[reference]
    arguments = {
        "session_id": joined["session"]["id"],
        "kind": "progress",
        "text": f"Result based on {finding['id']}",
        "request_id": "result",
        "sources": [],
        "derived_from": references,
    }
    if transport == "http":
        response = client.post("/api/post_update", headers=headers(alice), json=arguments)
        assert response.status_code == (409 if reference == "stale" else 422)
    else:
        response_body = rpc(
            client, alice, "tools/call", {"name": "post_update", "arguments": arguments}
        )
        assert response_body["result"]["isError"]
    view = tool(client, alice, "read_workspace", {"workspace_id": "reading"})
    assert len(view["updates"]) == 1


def test_adding_restrictions_withholds_the_write_result_and_allows_an_exact_retry(
    pilot: Pilot, client: TestClient
) -> None:
    database, alice, _, _ = pilot
    with SQLiteRepository(database) as repository:
        Administration(repository).register_source(
            "reading", "private", "synthetic", ["alice"], ["codex"]
        )
    joined = join(client, alice)
    arguments = {
        "session_id": joined["session"]["id"],
        "kind": "finding",
        "text": "Disclosure",
        "request_id": "original",
        "sources": [],
    }
    original = tool(client, alice, "post_update", arguments)
    arguments.update(
        sources=["private"], update_id=original["id"], expected_revision=1, request_id="restrict"
    )
    response = client.post("/api/post_update", headers=headers(alice), json=arguments)
    assert response.status_code == 503
    assert "same request ID" in response.text
    assert "Disclosure" not in response.text
    retry = client.post("/api/post_update", headers=headers(alice), json=arguments)
    assert retry.status_code == 200
    assert retry.json()["revision"] == 2
    assert retry.json()["sources"] == ["private"]


def test_open_session_schema_and_response_match_cli_http_and_mcp(
    pilot: Pilot, client: TestClient
) -> None:
    database, alice, _, _ = pilot
    schema = next(
        t["inputSchema"]
        for t in rpc(client, alice, "tools/list", {})["result"]["tools"]
        if t["name"] == "open_session"
    )
    assert set(schema["properties"]) == {"workspace_id", "task_id", "request_id", "session_id"}
    arguments = {"workspace_id": "reading", "task_id": "first", "request_id": "open"}
    response = client.post("/api/open_session", headers=headers(alice), json=arguments)
    assert response.status_code == 200
    opened = response.json()
    mcp = tool(client, alice, "open_session", arguments)
    command = CliRunner().invoke(
        cli_app,
        ["--db", str(database), "open", "reading", "first", "--request-id", "open"],
        env={"SIMPLE_TOKEN": alice},
    )
    assert command.exit_code == 0, command.output
    for result in (opened, mcp, json.loads(command.stdout)):
        assert result["id"] == opened["id"]
        assert set(result) == {
            "id",
            "workspace_id",
            "user",
            "client",
            "task_id",
            "started_at",
            "last_seen_at",
            "ended_at",
        }
    assert join(client, alice, "open")["session"]["id"] == opened["id"]


def test_context_schema_and_response_match_cli_http_and_mcp(
    pilot: Pilot, client: TestClient
) -> None:
    database, alice, _, _ = pilot
    schema = next(
        t["inputSchema"]
        for t in rpc(client, alice, "tools/list", {})["result"]["tools"]
        if t["name"] == "prepare_context"
    )
    assert set(schema["properties"]) == {"workspace_id", "task_id", "goal", "facts", "byte_budget"}
    arguments = {
        "workspace_id": "reading",
        "task_id": "first",
        "goal": "workload",
        "facts": {"project": "A"},
        "byte_budget": 512,
    }
    http = client.post("/api/prepare_context", headers=headers(alice), json=arguments)
    assert http.status_code == 200
    mcp = tool(client, alice, "prepare_context", arguments)
    command = CliRunner().invoke(
        cli_app,
        [
            "--db",
            str(database),
            "context",
            "reading",
            "first",
            "workload",
            "--facts",
            '{"project":"A"}',
            "--byte-budget",
            "512",
        ],
        env={"SIMPLE_TOKEN": alice},
    )
    assert command.exit_code == 0, command.output
    assert json.loads(command.stdout) == http.json() == mcp
    assert len(command.stdout.strip().encode()) <= 512 and len(http.content) <= 512
    assert mcp["status"] == "insufficient_context"


def test_remote_contributors_cannot_submit_validation_results(
    pilot: Pilot, client: TestClient
) -> None:
    _, alice, _, _ = pilot
    response = client.post("/api/record_validation", headers=headers(alice), json={"passed": True})
    assert response.status_code == 404
    assert "record_validation" not in {
        t["name"] for t in rpc(client, alice, "tools/list", {})["result"]["tools"]
    }


def test_unknown_service_operation_is_a_domain_error(tmp_path: Path) -> None:
    from simple_framework.domain import InvalidRequest
    from simple_framework.service import Service

    database = tmp_path / "unused.sqlite3"
    with pytest.raises(InvalidRequest, match="Unknown operation"):
        Service(database).execute("__class__", {}, "invalid-token")
    assert not database.exists()
