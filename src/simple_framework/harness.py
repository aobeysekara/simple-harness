import json
import re
from collections.abc import Callable
from dataclasses import replace
from hashlib import sha256

from simple_framework._context import ReusePolicy
from simple_framework.auth import check_identity
from simple_framework.domain import (
    DEFAULT_PAGE_SIZE,
    AccessDenied,
    Artifact,
    Audit,
    Caller,
    ChangePage,
    Conflict,
    ContextPacket,
    Correction,
    Cursor,
    Dependency,
    InvalidRequest,
    Joined,
    Membership,
    Policy,
    Receipt,
    ReuseApproval,
    Revision,
    Session,
    Source,
    SourceReference,
    Task,
    Update,
    ValidationRun,
    Workspace,
    WorkspaceView,
)
from simple_framework.ports import Repository
from simple_framework.reuse import (
    semantic_digest,
    validate_artifact,
    validate_correction,
    validate_facts,
    validate_goals,
)
from simple_framework.runtime import new_id, utc_now
from simple_framework.validation import require_limit, require_text


def fingerprint(parts: list[object]) -> str:
    """Hash a canonical request or retry scope."""
    return sha256(json.dumps(parts, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class Harness:
    """Coordinate sessions, contributions and basic review without factory dependencies."""

    def __init__(
        self,
        repository: Repository,
        clock: Callable[[], str] = utc_now,
        identifiers: Callable[[], str] = new_id,
    ) -> None:
        self.repository = repository
        self.clock = clock
        self.identifiers = identifiers

    def _access(self, caller: Caller, workspace_id: str, role: str | None = None) -> Workspace:
        client = check_identity(self.repository, caller)
        workspace = self.repository.get(Workspace, workspace_id)
        membership = self.repository.get(Membership, caller.user, workspace_id)
        if (
            workspace is None
            or membership is None
            or not membership.active
            or (role is not None and client.role != role)
        ):
            raise AccessDenied()
        return workspace

    def _task(self, workspace_id: str, task_id: str) -> Task:
        task = self.repository.get(Task, task_id, workspace_id)
        if task is None:
            raise AccessDenied()
        return task

    def _session(self, caller: Caller, session_id: str, open_only: bool = True) -> Session:
        session = self.repository.get(Session, session_id)
        if session is None or (session.user, session.client) != (caller.user, caller.client):
            raise AccessDenied()
        self._access(caller, session.workspace_id, "contributor")
        if open_only and session.ended_at is not None:
            raise Conflict("Session has ended; join a new session")
        return session

    def _policy(self) -> int:
        policy = self.repository.get(Policy, "access")
        return policy.revision if policy else 0

    def _audit(self, caller: Caller, workspace_id: str, action: str) -> None:
        self.repository.save(
            Audit(
                id=self.identifiers(),
                workspace_id=workspace_id,
                user=caller.user,
                client=caller.client,
                action=action,
                at=self.clock(),
                policy_revision=self._policy(),
            )
        )

    def _receipt(
        self,
        caller: Caller,
        workspace_id: str,
        operation: str,
        scope: str,
        request_id: str,
        payload: list[object],
    ) -> tuple[str, str, Receipt | None]:
        require_text(request_id, "Request ID")
        receipt_id = fingerprint([caller.user, caller.client, operation, scope, request_id])
        digest = fingerprint(payload)
        receipt = self.repository.get(Receipt, receipt_id, workspace_id)
        if receipt is not None and receipt.fingerprint != digest:
            raise Conflict("Request ID was already used with different content")
        return receipt_id, digest, receipt

    def _open_session(
        self,
        caller: Caller,
        workspace_id: str,
        task_id: str,
        request_id: str,
        session_id: str | None,
    ) -> Session:
        self._access(caller, workspace_id, "contributor")
        self._task(workspace_id, task_id)
        key, digest, receipt = self._receipt(
            caller, workspace_id, "join", workspace_id, request_id, [task_id, session_id]
        )
        existing_id = receipt.result_id if receipt else session_id
        if existing_id is not None:
            session = self._session(caller, existing_id)
            if (session.workspace_id, session.task_id) != (workspace_id, task_id):
                raise Conflict("A session cannot change workspace or task")
            session = replace(session, last_seen_at=self.clock())
        else:
            now = self.clock()
            session = Session(
                id=self.identifiers(),
                workspace_id=workspace_id,
                user=caller.user,
                client=caller.client,
                task_id=task_id,
                started_at=now,
                last_seen_at=now,
            )
        self.repository.save(session)
        self.repository.save(
            Receipt(id=key, workspace_id=workspace_id, fingerprint=digest, result_id=session.id)
        )
        self._audit(caller, workspace_id, "join")
        return session

    def open_session(
        self,
        caller: Caller,
        workspace_id: str,
        task_id: str,
        request_id: str,
        session_id: str | None = None,
    ) -> Session:
        """Create or resume an owned session without retrieving workspace history."""
        with self.repository.transaction():
            return self._open_session(caller, workspace_id, task_id, request_id, session_id)

    def join_workspace(
        self,
        caller: Caller,
        workspace_id: str,
        task_id: str,
        request_id: str,
        session_id: str | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> Joined:
        """Create or resume an owned session and return an authorised snapshot."""
        with self.repository.transaction():
            session = self._open_session(caller, workspace_id, task_id, request_id, session_id)
            require_limit(limit)
            view = self._view(caller, workspace_id, None, None, limit)
            return Joined(session, view)

    def _visible(self, caller: Caller, update: Update) -> bool:
        if not self._allows_sources(caller, update.workspace_id, update.sources):
            return False
        pending = {update.id, *update.derived_from}
        visited: set[str] = set()
        while pending:
            update_id = pending.pop()
            if update_id in visited:
                continue
            visited.add(update_id)
            if len(visited) > 256:
                return False
            current = self.repository.get(Update, update_id, update.workspace_id)
            if current is None or not self._allows_sources(
                caller, update.workspace_id, current.sources
            ):
                return False
            pending.update(current.derived_from.keys() - visited)
        return True

    def _allows_sources(self, caller: Caller, workspace_id: str, source_ids: list[str]) -> bool:
        for source_id in source_ids:
            source = self.repository.get(Source, source_id, workspace_id)
            if (
                source is None
                or caller.user not in source.users
                or caller.client not in source.clients
            ):
                return False
        return True

    def _update(self, caller: Caller, workspace_id: str, update_id: str) -> Update:
        update = self.repository.get(Update, update_id, workspace_id)
        if update is None or not self._visible(caller, update):
            raise AccessDenied()
        return update

    def _cursor(
        self,
        caller: Caller,
        workspace_id: str,
        task_id: str | None,
        mode: str,
        position: int,
        offset: int = 0,
    ) -> str:
        cursor = Cursor(
            id=self.identifiers(),
            workspace_id=workspace_id,
            user=caller.user,
            client=caller.client,
            policy_revision=self._policy(),
            task_id=task_id,
            mode=mode,
            position=position,
            offset=offset,
        )
        self.repository.save(cursor)
        return cursor.id

    def _read_cursor(
        self,
        caller: Caller,
        workspace_id: str,
        cursor_id: str,
        task_id: str | None,
        mode: str,
    ) -> Cursor:
        cursor = self.repository.get(Cursor, cursor_id, workspace_id)
        if cursor is None or (cursor.user, cursor.client) != (caller.user, caller.client):
            raise AccessDenied()
        filter_matches = (
            task_id is None
            or cursor.task_id == task_id
            or (mode == "changes" and cursor.task_id is None)
        )
        if (cursor.policy_revision, cursor.mode) != (self._policy(), mode) or not filter_matches:
            raise Conflict("Cursor policy, filter or operation changed; read the workspace again")
        return cursor

    def _view(
        self,
        caller: Caller,
        workspace_id: str,
        task_id: str | None,
        cursor_id: str | None,
        limit: int,
    ) -> WorkspaceView:
        workspace = self._access(caller, workspace_id)
        require_limit(limit)
        history = self.repository.changes(workspace_id)
        cursor = (
            self._read_cursor(caller, workspace_id, cursor_id, task_id, "snapshot.v2")
            if cursor_id
            else None
        )
        if cursor and task_id is None:
            task_id = cursor.task_id
        if task_id is not None:
            self._task(workspace_id, task_id)
        position = cursor.position if cursor else (history[-1].sequence if history else 0)
        offset = cursor.offset if cursor else 0
        latest: dict[str, Update] = {}
        for change in history:
            if change.sequence <= position:
                latest[change.update.id] = change.update
        if cursor and any(
            (task_id is None or update.task_id == task_id)
            and self._allows_sources(caller, workspace_id, update.sources)
            and not self._visible(caller, update)
            for update in latest.values()
        ):
            raise Conflict("Snapshot source restrictions changed; read the workspace again")
        tasks = self.repository.records(Task, workspace_id)
        records: list[Task | Update] = [t for t in tasks if task_id is None or t.id == task_id]
        records.extend(
            u
            for u in latest.values()
            if (task_id is None or u.task_id == task_id) and self._visible(caller, u)
        )
        page = records[offset : offset + limit]
        updates = [r for r in page if isinstance(r, Update)]
        sources = [
            SourceReference(source.id, source.reference)
            for source in self.repository.records(Source, workspace_id)
            if source.id in {s for update in updates for s in update.sources}
        ]
        next_page = (
            self._cursor(caller, workspace_id, task_id, "snapshot.v2", position, offset + limit)
            if offset + limit < len(records)
            else None
        )
        return WorkspaceView(
            workspace,
            [r for r in page if isinstance(r, Task)],
            updates,
            sources,
            self._cursor(caller, workspace_id, task_id, "changes", position),
            next_page,
            task_id,
        )

    def read_workspace(
        self,
        caller: Caller,
        workspace_id: str,
        task_id: str | None = None,
        cursor: str | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> WorkspaceView:
        """Read a bounded snapshot page with current source permissions."""
        with self.repository.transaction():
            view = self._view(caller, workspace_id, task_id, cursor, limit)
            self._audit(caller, workspace_id, "read")
            return view

    def get_updates(
        self,
        caller: Caller,
        workspace_id: str,
        cursor: str,
        task_id: str | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> ChangePage:
        """Read a bounded page of allowed revisions across workspace tasks."""
        with self.repository.transaction():
            self._access(caller, workspace_id)
            require_limit(limit)
            start = self._read_cursor(caller, workspace_id, cursor, task_id, "changes")
            if task_id is None:
                task_id = start.task_id
            history = self.repository.changes(workspace_id, after=start.position)
            changes = [
                c
                for c in history
                if (task_id is None or c.update.task_id == task_id)
                and self._visible(caller, c.update)
            ]
            page = changes[:limit]
            position = (
                page[-1].sequence
                if len(changes) > limit
                else history[-1].sequence
                if history
                else start.position
            )
            next_cursor = self._cursor(caller, workspace_id, task_id, "changes", position)
            self._audit(caller, workspace_id, "changes")
            return ChangePage(
                next_cursor,
                [Revision(c.update, c.actor, c.client) for c in page],
                task_id,
                has_more=len(changes) > limit,
            )

    def _sources(
        self,
        caller: Caller,
        workspace_id: str,
        sources: list[str] | None,
        derived_from: list[Dependency],
        previous: Update | None,
        text: str,
    ) -> tuple[list[str], dict[str, int]]:
        if sources is None:
            raise InvalidRequest("Declare source IDs, or an explicit empty list for original work")
        if len(sources) + len(derived_from) > 100:
            raise InvalidRequest("At most 100 references are allowed")
        inherited = set(sources)
        dependencies = dict(previous.derived_from) if previous else {}
        if previous:
            inherited.update(previous.sources)
        for reference in derived_from:
            if (
                not isinstance(reference, dict)
                or set(reference) != {"update_id", "revision"}
                or not isinstance(reference["update_id"], str)
                or type(reference["revision"]) is not int
                or reference["revision"] < 1
            ):
                raise InvalidRequest(
                    "Dependencies require update_id and revision (a positive integer)"
                )
        updates = [self._update(caller, workspace_id, ref["update_id"]) for ref in derived_from]
        if len({ref["update_id"] for ref in derived_from}) != len(derived_from):
            raise InvalidRequest("Duplicate dependency references are not allowed")
        for reference, update in zip(derived_from, updates, strict=True):
            if reference["revision"] != update.revision:
                raise Conflict("Dependency revision changed; refresh and reconsider your work")
            inherited.update(update.sources)
            dependencies[update.id] = reference["revision"]
        for source_id in inherited:
            source = self.repository.get(Source, source_id, workspace_id)
            if (
                source is None
                or caller.user not in source.users
                or caller.client not in source.clients
            ):
                raise AccessDenied()
        if len(inherited) > 100 or len(dependencies) > 100:
            raise InvalidRequest(
                "A contribution can retain at most 100 sources and 100 dependencies"
            )
        mentioned = set(re.findall(r"[\w-]+", text))
        if previous:
            mentioned.discard(previous.id)
        for update_id in sorted(mentioned - dependencies.keys()):
            mentioned_update = self.repository.get(Update, update_id, workspace_id)
            if mentioned_update is not None and self._visible(caller, mentioned_update):
                raise InvalidRequest(
                    "Declare explicitly referenced contributions in derived_from "
                    "with the revisions you used"
                )
        return sorted(inherited), dependencies

    def _retried_update(self, caller: Caller, workspace_id: str, receipt: Receipt) -> Update:
        self._update(caller, workspace_id, receipt.result_id)
        for change in self.repository.changes(workspace_id):
            if (change.update.id, change.update.revision) == (receipt.result_id, receipt.revision):
                if not self._visible(caller, change.update):
                    raise AccessDenied()
                self._audit(caller, workspace_id, "update.retry")
                return change.update
        raise Conflict("Stored retry result is unavailable")

    def _save_update(self, caller: Caller, update: Update, key: str, digest: str) -> Update:
        self.repository.save(update)
        self.repository.append_change(update, caller.user, caller.client)
        self.repository.save(
            Receipt(
                id=key,
                workspace_id=update.workspace_id,
                fingerprint=digest,
                result_id=update.id,
                revision=update.revision,
            )
        )
        self._audit(caller, update.workspace_id, "update")
        return update

    def post_update(
        self,
        caller: Caller,
        session_id: str,
        kind: str,
        text: str,
        request_id: str,
        sources: list[str] | None,
        derived_from: list[Dependency] | None = None,
        update_id: str | None = None,
        expected_revision: int | None = None,
        correction: Correction | None = None,
        goals: list[str] | None = None,
    ) -> Update:
        """Create or amend a contribution with explicit sources and a retry key."""
        require_text(text, "Update text", 20000)
        if kind not in {"progress", "question", "proposal", "decision", "finding", "handover"}:
            raise InvalidRequest("Unknown update kind")
        if (update_id is None) != (expected_revision is None):
            raise InvalidRequest("Amendments need both an update ID and an expected revision")
        references = derived_from or []
        with self.repository.transaction():
            session = self._session(caller, session_id, open_only=False)
            payload: list[object] = [kind, text, sources, references, update_id, expected_revision]
            if correction is not None or goals is not None:
                payload.extend([correction, goals])
            key, digest, receipt = self._receipt(
                caller,
                session.workspace_id,
                "post",
                session_id,
                request_id,
                payload,
            )
            if receipt:
                return self._retried_update(caller, session.workspace_id, receipt)
            if session.ended_at is not None:
                raise Conflict("Session has ended; join a new session")
            previous = self._update(caller, session.workspace_id, update_id) if update_id else None
            if previous:
                if (previous.author, previous.client, previous.task_id) != (
                    caller.user,
                    caller.client,
                    session.task_id,
                ):
                    raise AccessDenied()
                if previous.revision != expected_revision:
                    raise Conflict("Update revision changed")
                if previous.kind != kind:
                    raise InvalidRequest("An amendment cannot change the update kind")
            metadata = (
                correction
                if correction is not None
                else (previous.correction if previous else None)
            )
            goal_tags = goals if goals is not None else (previous.goals if previous else [])
            validate_goals(goal_tags)
            source_ids, dependencies = self._sources(
                caller,
                session.workspace_id,
                sources,
                references,
                previous,
                text + (json.dumps(metadata) if metadata else ""),
            )
            if metadata is not None:
                if kind not in {"finding", "decision", "proposal"}:
                    raise InvalidRequest("Corrections require a reviewable contribution kind")
                validate_correction(metadata, dependencies, goal_tags)
            if previous and set(source_ids) - set(previous.sources):
                self.repository.save(Policy(id="access", revision=self._policy() + 1))
            now = self.clock()
            update = Update(
                id=previous.id if previous else self.identifiers(),
                workspace_id=session.workspace_id,
                session_id=session.id,
                task_id=session.task_id,
                author=caller.user,
                client=caller.client,
                kind=kind,
                text=text,
                sources=source_ids,
                derived_from=dependencies,
                created_at=previous.created_at if previous else now,
                changed_at=now,
                revision=previous.revision + 1 if previous else 1,
                status="proposed" if kind in {"proposal", "decision", "finding"} else "unreviewed",
                correction=metadata,
                goals=list(goal_tags),
            )
            self.repository.save(replace(session, last_seen_at=now))
            return self._save_update(caller, update, key, digest)

    def review_update(
        self,
        caller: Caller,
        workspace_id: str,
        update_id: str,
        expected_revision: int,
        status: str,
        request_id: str,
        approve_reuse: bool = False,
    ) -> Update:
        """Accept or reject another user's exact contribution revision."""
        if status not in {"accepted", "rejected"}:
            raise InvalidRequest("Review status must be accepted or rejected")
        with self.repository.transaction():
            self._access(caller, workspace_id, "reviewer")
            update = self._update(caller, workspace_id, update_id)
            if update.author == caller.user:
                raise AccessDenied()
            payload: list[object] = [expected_revision, status]
            if approve_reuse:
                payload.append(True)
            key, digest, receipt = self._receipt(
                caller,
                workspace_id,
                "review",
                update_id,
                request_id,
                payload,
            )
            if receipt:
                return self._retried_update(caller, workspace_id, receipt)
            if update.revision != expected_revision:
                raise Conflict("Update revision changed")
            if update.kind not in {"proposal", "decision", "finding"}:
                raise InvalidRequest("Only proposals, decisions and findings can be reviewed")
            approval: ReuseApproval | None = None
            if approve_reuse:
                if (
                    status != "accepted"
                    or not update.correction
                    or not update.correction["propose_reuse"]
                ):
                    raise InvalidRequest(
                        "Reuse requires an explicit correction proposal and scope approval"
                    )
                history = {
                    (c.update.id, c.update.revision): c.update
                    for c in self.repository.changes(workspace_id)
                }
                policy = ReusePolicy(self.repository)
                if policy.stale(update, history):
                    raise Conflict("Dependencies changed; refresh, revalidate and review")
                if any(
                    r.correction and not policy.eligible(r) for r in policy.requirements(update)
                ):
                    raise Conflict("Referenced correction needs revalidation and scope review")
                runs = policy.passing_runs(update)
                if not runs:
                    raise Conflict("Passing trusted validation is required for this content")
                approval = {
                    "subject_digest": semantic_digest(update),
                    "reviewer": caller.user,
                    "client": caller.client,
                    "validation_ids": [r.id for r in runs],
                }
            reviewed = replace(
                update,
                revision=update.revision + 1,
                status=status,
                reviewed_by=caller.user,
                changed_at=self.clock(),
                reuse_approval=approval,
            )
            return self._save_update(caller, reviewed, key, digest)

    def validation_subject(self, caller: Caller, workspace_id: str, update_id: str) -> Update:
        """Return a permitted correction to the trusted validator adapter."""
        with self.repository.transaction():
            self._access(caller, workspace_id, "validator")
            update = self._update(caller, workspace_id, update_id)
            if not update.correction:
                raise InvalidRequest("Validation requires a structured correction")
            return update

    def record_validation(
        self,
        caller: Caller,
        workspace_id: str,
        update_id: str,
        subject_digest: str,
        case_id: str,
        artifacts: dict[str, Artifact],
        outcome: str,
        evidence: Artifact,
        request_id: str,
    ) -> ValidationRun:
        """Record a trusted validator result; contributor claims cannot use this operation."""
        with self.repository.transaction():
            self._access(caller, workspace_id, "validator")
            update = self._update(caller, workspace_id, update_id)
            key, digest, receipt = self._receipt(
                caller,
                workspace_id,
                "validate",
                update_id,
                request_id,
                [subject_digest, case_id, artifacts, outcome, evidence],
            )
            if receipt:
                result = self.repository.get(ValidationRun, receipt.result_id, workspace_id)
                if result is None:
                    raise Conflict("Stored validation result is unavailable")
                return result
            if semantic_digest(update) != subject_digest:
                raise Conflict("Validation subject changed; replay current content")
            cases = update.correction["cases"] if update.correction else []
            case = next((c for c in cases if c["id"] == case_id), None)
            if case is None or artifacts != {
                "code": case["code"],
                "inputs": case["inputs"],
                "test": case["test"],
            }:
                raise Conflict("Validation artifacts do not match the declared case")
            validate_artifact(evidence)
            if outcome not in {"passed", "failed", "error"}:
                raise InvalidRequest("Validation outcome must be passed, failed or error")
            result = ValidationRun(
                id=self.identifiers(),
                workspace_id=workspace_id,
                update_id=update_id,
                subject_digest=subject_digest,
                case_id=case_id,
                artifacts=artifacts,
                outcome=outcome,
                evidence=evidence,
                user=caller.user,
                client=caller.client,
                at=self.clock(),
                number=len(self.repository.records(ValidationRun, workspace_id)) + 1,
            )
            self.repository.save(result)
            self.repository.save(
                Receipt(id=key, workspace_id=workspace_id, fingerprint=digest, result_id=result.id)
            )
            self._audit(caller, workspace_id, "validation")
            return result

    def prepare_context(
        self,
        caller: Caller,
        workspace_id: str,
        task_id: str,
        goal: str,
        facts: dict[str, str],
        byte_budget: int = 16384,
    ) -> ContextPacket:
        """Select complete permitted context by declared goal and exact applicability facts."""
        require_text(goal, "Goal")
        validate_facts(facts)
        if type(byte_budget) is not int or not 512 <= byte_budget <= 65536:
            raise InvalidRequest("Context byte budget must be between 512 and 65536")
        with self.repository.transaction():
            self._access(caller, workspace_id)
            self._task(workspace_id, task_id)
            visible_updates = [
                update
                for update in self.repository.records(Update, workspace_id)
                if self._visible(caller, update)
            ]
            packet = ReusePolicy(self.repository).select_context(
                workspace_id, task_id, goal, facts, byte_budget, visible_updates
            )
            self._audit(caller, workspace_id, "context")
            return packet

    def end_session(
        self,
        caller: Caller,
        session_id: str,
        request_id: str,
        handover: str | None = None,
        sources: list[str] | None = None,
        derived_from: list[Dependency] | None = None,
    ) -> Session:
        """Close a session and save its optional handover in the same transaction."""
        if handover is not None:
            require_text(handover, "Handover text", 20000)
        elif sources is not None or derived_from:
            raise InvalidRequest("Source references require handover text")
        references = derived_from or []
        with self.repository.transaction():
            session = self._session(caller, session_id, open_only=False)
            key, digest, receipt = self._receipt(
                caller,
                session.workspace_id,
                "end",
                session_id,
                request_id,
                [handover, sources, references],
            )
            if receipt:
                self._audit(caller, session.workspace_id, "end.retry")
                return session
            if session.ended_at is not None:
                raise Conflict("Session has already ended")
            now = self.clock()
            if handover is not None:
                source_ids, dependencies = self._sources(
                    caller,
                    session.workspace_id,
                    sources,
                    references,
                    None,
                    handover,
                )
                update = Update(
                    id=self.identifiers(),
                    workspace_id=session.workspace_id,
                    session_id=session.id,
                    task_id=session.task_id,
                    author=caller.user,
                    client=caller.client,
                    kind="handover",
                    text=handover,
                    sources=source_ids,
                    derived_from=dependencies,
                    created_at=now,
                    changed_at=now,
                )
                self.repository.save(update)
                self.repository.append_change(update, caller.user, caller.client)
            ended = replace(session, ended_at=now, last_seen_at=now)
            self.repository.save(ended)
            self.repository.save(
                Receipt(
                    id=key,
                    workspace_id=session.workspace_id,
                    fingerprint=digest,
                    result_id=session.id,
                )
            )
            self._audit(caller, session.workspace_id, "end")
            return ended
