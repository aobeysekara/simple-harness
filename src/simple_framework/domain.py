from dataclasses import dataclass, field
from typing import Literal, TypedDict

ContextStatus = Literal["ready", "insufficient_context", "insufficient_budget"]
ContextIssueReason = Literal["unknown_applicability", "needs_revalidation"]

DEFAULT_PAGE_SIZE = 10


class Dependency(TypedDict):
    """Identify the contribution revision used by a new mutation."""

    update_id: str
    revision: int


class Artifact(TypedDict):
    """Identify declared bytes without claiming their truth or availability."""

    reference: str
    sha256: str


class AcceptanceCase(TypedDict):
    """Identify the code, input and test bytes required for a replay."""

    id: str
    code: Artifact
    inputs: Artifact
    test: Artifact


class Correction(TypedDict):
    """Declare a correction and the limits of its proposed reuse."""

    category: Literal["knowledge", "procedure"]
    corrects: Dependency
    mistake: str
    rationale: str
    scope: dict[str, str]
    exclusions: list[dict[str, str]]
    required_facts: list[str]
    facts: list[Dependency]
    cases: list[AcceptanceCase]
    propose_reuse: bool


class ReuseApproval(TypedDict):
    """Bind independent scope approval to tested semantic content."""

    subject_digest: str
    reviewer: str
    client: str
    validation_ids: list[str]


class HarnessError(ValueError):
    """Report a rejected harness operation."""


class AccessDenied(HarnessError):
    """Reject access without exposing whether a record exists."""

    def __init__(self) -> None:
        super().__init__("Access denied")


class Conflict(HarnessError):
    """Reject a stale revision, cursor or conflicting retry."""


class InvalidRequest(HarnessError):
    """Reject an invalid request."""


@dataclass(frozen=True, kw_only=True)
class Record:
    """Identify a stored core record."""

    id: str
    workspace_id: str = ""


@dataclass(frozen=True, kw_only=True)
class Workspace(Record):
    """Name a shared workspace."""

    name: str


@dataclass(frozen=True, kw_only=True)
class User(Record):
    """Record whether a user can access the harness."""

    active: bool = True


@dataclass(frozen=True, kw_only=True)
class Client(Record):
    """Bind a registered client to its user, destination and role."""

    user: str
    destination: str
    role: str
    active: bool = True


@dataclass(frozen=True, kw_only=True)
class Credential(Record):
    """Bind a token digest to a registered client."""

    user: str
    client: str
    active: bool = True


@dataclass(frozen=True, kw_only=True)
class Membership(Record):
    """Record whether a user belongs to a workspace."""

    active: bool = True


@dataclass(frozen=True, kw_only=True)
class Policy(Record):
    """Version the current access policy."""

    revision: int = 1


@dataclass(frozen=True, kw_only=True)
class Task(Record):
    """Describe an ordinary workspace task."""

    title: str
    status: str = "open"


@dataclass(frozen=True, kw_only=True)
class Session(Record):
    """Bind work to one user, client and task."""

    user: str
    client: str
    task_id: str
    started_at: str
    last_seen_at: str
    ended_at: str | None = None


@dataclass(frozen=True, kw_only=True)
class Source(Record):
    """Record a source reference and its permitted users and clients."""

    reference: str
    users: list[str]
    clients: list[str]


@dataclass(frozen=True, kw_only=True)
class Update(Record):
    """Preserve an attributed contribution and its review state."""

    session_id: str
    task_id: str
    author: str
    client: str
    kind: str
    text: str
    sources: list[str]
    derived_from: dict[str, int]
    created_at: str
    changed_at: str
    revision: int = 1
    status: str = "unreviewed"
    reviewed_by: str | None = None
    correction: Correction | None = None
    goals: list[str] = field(default_factory=list)
    reuse_approval: ReuseApproval | None = None


@dataclass(frozen=True, kw_only=True)
class ValidationRun(Record):
    """Record a trusted validator's reported execution, including failures."""

    update_id: str
    subject_digest: str
    case_id: str
    artifacts: dict[str, Artifact]
    outcome: str
    evidence: Artifact
    user: str
    client: str
    at: str
    number: int
    method: str = "machine"


@dataclass(frozen=True)
class ContextItem:
    """Return a complete contribution with supporting records and selection reasons."""

    update: Update
    reason: str
    support: list[Update]
    validation: list[ValidationRun]


@dataclass(frozen=True)
class ContextIssue:
    """Identify visible work that cannot currently supply reusable guidance."""

    update_id: str
    reason: ContextIssueReason
    missing_facts: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ContextPacket:
    """Return complete task context within the requested JSON byte budget."""

    workspace_id: str
    task_id: str
    goal: str
    byte_budget: int
    status: ContextStatus
    items: list[ContextItem]
    issues: list[ContextIssue]
    manifest: list[Dependency]


@dataclass(frozen=True, kw_only=True)
class Cursor(Record):
    """Bind a read position to its caller, filter and policy."""

    user: str
    client: str
    policy_revision: int
    task_id: str | None
    mode: str
    position: int
    offset: int = 0


@dataclass(frozen=True, kw_only=True)
class Receipt(Record):
    """Preserve a write result for an exact request retry."""

    fingerprint: str
    result_id: str
    revision: int = 0


@dataclass(frozen=True, kw_only=True)
class Audit(Record):
    """Record an authorised action without copying its content."""

    user: str
    client: str
    action: str
    at: str
    policy_revision: int
    target_id: str | None = None


@dataclass(frozen=True)
class Caller:
    """Carry identity established by the authentication adapter."""

    user: str
    client: str
    credential: str


@dataclass(frozen=True)
class Change:
    """Preserve the update revision released by a change event."""

    sequence: int
    update: Update
    actor: str
    client: str


@dataclass(frozen=True)
class Revision:
    """Return an attributed revision without exposing hidden event counts."""

    update: Update
    actor: str
    client: str


@dataclass(frozen=True)
class SourceReference:
    """Return a source reference without its access policy."""

    id: str
    reference: str


@dataclass(frozen=True)
class WorkspaceView:
    """Return one snapshot page and the matching change cursor."""

    workspace: Workspace
    tasks: list[Task]
    updates: list[Update]
    sources: list[SourceReference]
    cursor: str
    next_page: str | None
    task_id: str | None = None


@dataclass(frozen=True)
class Joined:
    """Return a durable session and its current workspace view."""

    session: Session
    view: WorkspaceView


@dataclass(frozen=True)
class ChangePage:
    """Return ordered changes and the next polling cursor."""

    cursor: str
    changes: list[Revision] = field(default_factory=list)
    task_id: str | None = None
    has_more: bool = False
