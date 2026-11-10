import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Annotated

import typer

from simple_framework.admin import Administration
from simple_framework.auth import authenticate
from simple_framework.demo import setup_demo
from simple_framework.domain import (
    DEFAULT_PAGE_SIZE,
    Caller,
    Dependency,
    HarnessError,
    InvalidRequest,
)
from simple_framework.harness import Harness
from simple_framework.local_validation import run_case
from simple_framework.reuse import packet_json
from simple_framework.sqlite import SQLiteRepository

app = typer.Typer(help="Share selected work between local pilot sessions.", no_args_is_help=True)
admin_app = typer.Typer(help="Provision the pilot as the trusted database operator.")
app.add_typer(admin_app, name="admin")
demo_app = typer.Typer(help="Create a synthetic workspace for the local pilot.")
app.add_typer(demo_app, name="demo")


@app.callback()
def configure(
    ctx: typer.Context,
    database: Annotated[Path, typer.Option("--db", envvar="SIMPLE_DB")] = Path(
        ".simple/workspace.sqlite3"
    ),
) -> None:
    """Select the local database for this command."""
    ctx.obj = database


@contextmanager
def _repository(ctx: typer.Context) -> Iterator[SQLiteRepository]:
    try:
        with SQLiteRepository(ctx.obj) as repository:
            yield repository
    except (HarnessError, sqlite3.Error, OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e


def _caller(repository: SQLiteRepository) -> Caller:
    return authenticate(repository, os.environ.get("SIMPLE_TOKEN", ""))


def _emit(value: object) -> None:
    payload = asdict(value) if is_dataclass(value) and not isinstance(value, type) else value
    typer.echo(json.dumps(payload, indent=2))


def _sources(sources: list[str] | None, no_sources: bool) -> list[str]:
    if bool(sources) == no_sources:
        raise InvalidRequest("Use --source for each source, or --no-sources for original work")
    return sources or []


def _dependencies(references: list[str] | None) -> list[Dependency]:
    result: list[Dependency] = []
    for reference in references or []:
        update_id, separator, revision = reference.rpartition("@")
        if not separator or not update_id or not revision.isascii() or not revision.isdecimal():
            raise InvalidRequest("Use --derived-from UPDATE_ID@REVISION")
        result.append({"update_id": update_id, "revision": int(revision)})
    return result


@app.command("open")
def open_session(
    ctx: typer.Context,
    workspace: str,
    task: str,
    request_id: Annotated[str, typer.Option()],
    session: Annotated[str | None, typer.Option(help="Resume an owned session.")] = None,
) -> None:
    """Open a session without history; retrieve task context separately."""
    with _repository(ctx) as repository:
        _emit(
            Harness(repository).open_session(
                _caller(repository), workspace, task, request_id, session
            )
        )


@app.command("join")
def join(
    ctx: typer.Context,
    workspace: str,
    task: str,
    request_id: Annotated[str, typer.Option()],
    session: Annotated[str | None, typer.Option(help="Resume an owned session.")] = None,
    limit: Annotated[int, typer.Option(min=1, max=100)] = DEFAULT_PAGE_SIZE,
) -> None:
    """Join a workspace task and return a session, snapshot and cursor."""
    with _repository(ctx) as repository:
        _emit(
            Harness(repository).join_workspace(
                _caller(repository), workspace, task, request_id, session, limit
            )
        )


@app.command("read")
def read(
    ctx: typer.Context,
    workspace: str,
    task: Annotated[
        str | None, typer.Option(help="Filter a fresh read; omitted inherits cursor scope.")
    ] = None,
    cursor: Annotated[str | None, typer.Option(help="Continue a snapshot page.")] = None,
    limit: Annotated[int, typer.Option(min=1, max=100)] = DEFAULT_PAGE_SIZE,
) -> None:
    """Read authorised tasks and contributions from a workspace snapshot."""
    with _repository(ctx) as repository:
        _emit(
            Harness(repository).read_workspace(_caller(repository), workspace, task, cursor, limit)
        )


@app.command("post")
def post(
    ctx: typer.Context,
    session: str,
    kind: str,
    text: str,
    request_id: Annotated[str, typer.Option()],
    source: Annotated[list[str] | None, typer.Option()] = None,
    no_sources: Annotated[bool, typer.Option()] = False,
    derived_from: Annotated[
        list[str] | None, typer.Option(help="UPDATE_ID@REVISION, repeatable.")
    ] = None,
    update: Annotated[str | None, typer.Option(help="Amend this update.")] = None,
    revision: Annotated[int | None, typer.Option(help="Expected current revision.")] = None,
    correction: Annotated[
        Path | None, typer.Option(help="Structured correction JSON file.")
    ] = None,
    goal: Annotated[list[str] | None, typer.Option(help="Exact goal tag, repeatable.")] = None,
) -> None:
    """Share a contribution or amend an exact revision using a retry key."""
    with _repository(ctx) as repository:
        sources = _sources(source, no_sources)
        _emit(
            Harness(repository).post_update(
                _caller(repository),
                session,
                kind,
                text,
                request_id,
                sources,
                _dependencies(derived_from),
                update,
                revision,
                json.loads(correction.read_text()) if correction else None,
                goal,
            )
        )


@app.command("context")
def context(
    ctx: typer.Context,
    workspace: str,
    task: str,
    goal: str,
    facts: Annotated[str, typer.Option(help="JSON object of known applicability facts.")],
    byte_budget: Annotated[int, typer.Option(min=512, max=65536)] = 16384,
) -> None:
    """Prepare complete permitted task context within a UTF-8 JSON byte budget."""
    with _repository(ctx) as repository:
        packet = Harness(repository).prepare_context(
            _caller(repository),
            workspace,
            task,
            goal,
            json.loads(facts),
            byte_budget,
        )
        typer.echo(packet_json(packet))


@app.command("updates")
def updates(
    ctx: typer.Context,
    workspace: str,
    cursor: str,
    task: Annotated[
        str | None, typer.Option(help="Narrow a workspace cursor; omitted inherits its scope.")
    ] = None,
    limit: Annotated[int, typer.Option(min=1, max=100)] = DEFAULT_PAGE_SIZE,
) -> None:
    """Fetch allowed changes. Follow the returned cursor while has_more is true."""
    with _repository(ctx) as repository:
        _emit(Harness(repository).get_updates(_caller(repository), workspace, cursor, task, limit))


@app.command("end")
def end(
    ctx: typer.Context,
    session: str,
    request_id: Annotated[str, typer.Option()],
    handover: Annotated[str | None, typer.Option()] = None,
    source: Annotated[list[str] | None, typer.Option()] = None,
    no_sources: Annotated[bool, typer.Option()] = False,
    derived_from: Annotated[
        list[str] | None, typer.Option(help="UPDATE_ID@REVISION, repeatable.")
    ] = None,
) -> None:
    """End a session and optionally save a handover atomically."""
    with _repository(ctx) as repository:
        sources = (
            _sources(source, no_sources) if handover is not None or source or no_sources else None
        )
        _emit(
            Harness(repository).end_session(
                _caller(repository),
                session,
                request_id,
                handover,
                sources,
                _dependencies(derived_from),
            )
        )


@app.command("review")
def review(
    ctx: typer.Context,
    workspace: str,
    update: str,
    status: str,
    revision: Annotated[int, typer.Option(min=1)],
    request_id: Annotated[str, typer.Option()],
    approve_reuse: Annotated[
        bool, typer.Option(help="Approve tested content AND its declared scope.")
    ] = False,
) -> None:
    """Accept or reject another user's contribution with reviewer credentials."""
    with _repository(ctx) as repository:
        _emit(
            Harness(repository).review_update(
                _caller(repository),
                workspace,
                update,
                revision,
                status,
                request_id,
                approve_reuse,
            )
        )


@app.command("validate")
def validate(
    ctx: typer.Context,
    workspace: str,
    update: str,
    manifest: Annotated[
        Path, typer.Option(help="Trusted local case manifest; never retrieved commands.")
    ],
    evidence_directory: Annotated[
        Path, typer.Option(help="Keep this directory for exact retries.")
    ],
    request_id: Annotated[str, typer.Option()],
) -> None:
    """Replay an operator-selected local case using validator credentials."""
    with _repository(ctx) as repository:
        _emit(
            run_case(
                Harness(repository),
                _caller(repository),
                workspace,
                update,
                manifest,
                evidence_directory,
                request_id,
            )
        )


@admin_app.command("workspace")
def create_workspace(ctx: typer.Context, workspace: str, name: str) -> None:
    """Create a workspace."""
    with _repository(ctx) as repository:
        _emit(Administration(repository).create_workspace(workspace, name))


@admin_app.command("task")
def create_task(ctx: typer.Context, workspace: str, task: str, title: str) -> None:
    """Create a source-free workspace task."""
    with _repository(ctx) as repository:
        _emit(Administration(repository).create_task(workspace, task, title))


@admin_app.command("client")
def register_client(
    ctx: typer.Context,
    user: str,
    client: str,
    destination: Annotated[str, typer.Option(help="Operator-approved destination profile.")],
    role: Annotated[str, typer.Option()] = "contributor",
) -> None:
    """Register a client and print its token once."""
    with _repository(ctx) as repository:
        token = Administration(repository).register_client(user, client, destination, role)
        _emit({"user": user, "client": client, "token": token})


@admin_app.command("membership")
def membership(
    ctx: typer.Context,
    workspace: str,
    user: str,
    active: Annotated[bool, typer.Option("--active/--inactive")] = True,
) -> None:
    """Grant or revoke workspace membership."""
    with _repository(ctx) as repository:
        Administration(repository).set_membership(workspace, user, active)
        _emit({"user": user, "active": active})


@admin_app.command("client-access")
def client_access(
    ctx: typer.Context,
    client: str,
    active: Annotated[bool, typer.Option("--active/--inactive")] = True,
) -> None:
    """Enable or revoke a client's access."""
    with _repository(ctx) as repository:
        Administration(repository).set_client_active(client, active)
        _emit({"client": client, "active": active})


@admin_app.command("user-access")
def user_access(
    ctx: typer.Context,
    user: str,
    active: Annotated[bool, typer.Option("--active/--inactive")] = True,
) -> None:
    """Enable or revoke a user's access."""
    with _repository(ctx) as repository:
        Administration(repository).set_user_active(user, active)
        _emit({"user": user, "active": active})


@admin_app.command("revoke-token")
def revoke_token(ctx: typer.Context) -> None:
    """Revoke the token supplied through SIMPLE_TOKEN."""
    with _repository(ctx) as repository:
        Administration(repository).revoke_credential(os.environ.get("SIMPLE_TOKEN", ""))
        _emit({"revoked": True})


@admin_app.command("source")
def register_source(
    ctx: typer.Context,
    workspace: str,
    source: str,
    reference: str,
    user: Annotated[list[str], typer.Option()],
    client: Annotated[list[str], typer.Option()],
) -> None:
    """Register a source reference with explicit user and client grants."""
    with _repository(ctx) as repository:
        _emit(
            Administration(repository).register_source(workspace, source, reference, user, client)
        )


@admin_app.command("source-access")
def source_access(
    ctx: typer.Context,
    workspace: str,
    source: str,
    user: Annotated[list[str] | None, typer.Option()] = None,
    client: Annotated[list[str] | None, typer.Option()] = None,
) -> None:
    """Replace a source's grants; omitted lists revoke all access."""
    with _repository(ctx) as repository:
        Administration(repository).set_source_access(workspace, source, user or [], client or [])
        _emit({"source": source, "updated": True})


@demo_app.command("setup")
def create_demo(
    directory: Annotated[Path, typer.Option()] = Path(".simple/demo"),
) -> None:
    """Create a demo database and private token files without overwriting existing data."""
    try:
        demo = setup_demo(directory)
    except (HarnessError, OSError, sqlite3.Error) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e
    _emit(
        {
            "directory": str(demo.directory),
            "database": str(demo.database),
            "workspace": demo.workspace_id,
            "task": demo.task_id,
            "token_files": {
                name: str(demo.directory / f"{name}.token")
                for name in (
                    "alice",
                    "bob",
                    "reviewer",
                )
            },
        }
    )


def main() -> None:
    """Run the local harness CLI."""
    app()
