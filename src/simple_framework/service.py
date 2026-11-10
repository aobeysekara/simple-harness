import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from simple_framework.auth import authenticate
from simple_framework.domain import (
    DEFAULT_PAGE_SIZE,
    AccessDenied,
    Caller,
    ChangePage,
    Conflict,
    ContextPacket,
    Correction,
    Dependency,
    InvalidRequest,
    Joined,
    Policy,
    Session,
    Update,
    WorkspaceView,
)
from simple_framework.harness import Harness
from simple_framework.sqlite import SQLiteRepository


class Input(BaseModel):
    """Reject unexpected fields and type coercion at the service boundary."""

    model_config = ConfigDict(extra="forbid", strict=True)


class OpenInput(Input):
    """Describe session opening or resumption without history."""

    workspace_id: str
    task_id: str
    request_id: str
    session_id: str | None = None


class JoinInput(OpenInput):
    """Describe a workspace join with a snapshot."""

    limit: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=100)


class ReadInput(Input):
    """Select a workspace snapshot page."""

    workspace_id: str
    task_id: str | None = None
    cursor: str | None = None
    limit: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=100)


class ChangesInput(Input):
    """Select a workspace change page."""

    workspace_id: str
    cursor: str
    task_id: str | None = None
    limit: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=100)


class PostInput(Input):
    """Describe a contribution with explicit direct sources."""

    session_id: str
    kind: str
    text: str = Field(min_length=1, max_length=20000)
    request_id: str
    sources: list[str]
    derived_from: list[Dependency] | None = None
    update_id: str | None = None
    expected_revision: int | None = None
    correction: Correction | None = None
    goals: list[str] | None = None


class ContextInput(Input):
    """Select bounded task context using explicit goal and applicability facts."""

    workspace_id: str
    task_id: str
    goal: str
    facts: dict[str, str]
    byte_budget: int = Field(default=16384, ge=512, le=65536)


class EndInput(Input):
    """Describe session closure and an optional sourced handover."""

    session_id: str
    request_id: str
    handover: str | None = None
    sources: list[str] | None = None
    derived_from: list[Dependency] | None = None


class ReviewInput(Input):
    """Select an exact contribution revision for human review."""

    workspace_id: str
    update_id: str
    expected_revision: int
    status: str
    request_id: str
    approve_reuse: bool = False


CORE_OPERATIONS = (
    "open_session",
    "join_workspace",
    "read_workspace",
    "post_update",
    "get_updates",
    "end_session",
    "prepare_context",
)
INPUTS: dict[str, type[Input]] = {
    "open_session": OpenInput,
    "join_workspace": JoinInput,
    "read_workspace": ReadInput,
    "post_update": PostInput,
    "get_updates": ChangesInput,
    "end_session": EndInput,
    "review_update": ReviewInput,
    "prepare_context": ContextInput,
}


def dispatch(
    harness: Harness, caller: Caller, request: Input
) -> Session | Joined | WorkspaceView | Update | ChangePage | ContextPacket:
    """Map validated requests to explicit, statically checked application calls."""
    # JoinInput inherits OpenInput and must be handled first.
    if isinstance(request, JoinInput):
        return harness.join_workspace(
            caller,
            workspace_id=request.workspace_id,
            task_id=request.task_id,
            request_id=request.request_id,
            session_id=request.session_id,
            limit=request.limit,
        )
    if isinstance(request, OpenInput):
        return harness.open_session(
            caller,
            workspace_id=request.workspace_id,
            task_id=request.task_id,
            request_id=request.request_id,
            session_id=request.session_id,
        )
    if isinstance(request, ReadInput):
        return harness.read_workspace(
            caller,
            workspace_id=request.workspace_id,
            task_id=request.task_id,
            cursor=request.cursor,
            limit=request.limit,
        )
    if isinstance(request, ChangesInput):
        return harness.get_updates(
            caller,
            workspace_id=request.workspace_id,
            cursor=request.cursor,
            task_id=request.task_id,
            limit=request.limit,
        )
    if isinstance(request, PostInput):
        return harness.post_update(
            caller,
            session_id=request.session_id,
            kind=request.kind,
            text=request.text,
            request_id=request.request_id,
            sources=request.sources,
            derived_from=request.derived_from,
            update_id=request.update_id,
            expected_revision=request.expected_revision,
            correction=request.correction,
            goals=request.goals,
        )
    if isinstance(request, EndInput):
        return harness.end_session(
            caller,
            session_id=request.session_id,
            request_id=request.request_id,
            handover=request.handover,
            sources=request.sources,
            derived_from=request.derived_from,
        )
    if isinstance(request, ReviewInput):
        return harness.review_update(
            caller,
            workspace_id=request.workspace_id,
            update_id=request.update_id,
            expected_revision=request.expected_revision,
            status=request.status,
            request_id=request.request_id,
            approve_reuse=request.approve_reuse,
        )
    if isinstance(request, ContextInput):
        return harness.prepare_context(
            caller,
            workspace_id=request.workspace_id,
            task_id=request.task_id,
            goal=request.goal,
            facts=request.facts,
            byte_budget=request.byte_budget,
        )
    raise InvalidRequest("Unknown operation")


class Unauthenticated(AccessDenied):
    """Reject a missing, invalid or revoked credential."""


class ServiceUnavailable(InvalidRequest):
    """Report a storage failure without exposing internal paths or data."""


class Service:
    """Run registered harness operations with a request-owned database connection."""

    def __init__(self, database: Path) -> None:
        self.database = database

    def authorise(self, token: str, policy_revision: int | None = None) -> int:
        """Check current credentials and optionally reject a changed release policy."""
        try:
            with SQLiteRepository(self.database) as repository:
                try:
                    authenticate(repository, token)
                except AccessDenied as e:
                    raise Unauthenticated() from e
                policy = repository.get(Policy, "access")
                revision = policy.revision if policy else 0
                if policy_revision is not None and revision != policy_revision:
                    raise Conflict("Permissions changed; read the workspace again")
                return revision
        except (sqlite3.Error, OSError) as e:
            raise ServiceUnavailable("Storage unavailable; retry with the same request ID") from e

    def execute(self, operation: str, arguments: dict[str, Any], token: str) -> dict[str, Any]:
        """Validate input and invoke the matching core application operation."""
        model = INPUTS.get(operation)
        if model is None:
            raise InvalidRequest("Unknown operation")
        request = model.model_validate(arguments)
        try:
            with SQLiteRepository(self.database) as repository:
                caller = authenticate(repository, token)
                result = dispatch(Harness(repository), caller, request)
                return asdict(result)
        except (sqlite3.Error, OSError) as e:
            raise ServiceUnavailable("Storage unavailable; retry with the same request ID") from e
