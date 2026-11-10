import json
from collections.abc import Iterator
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import pytest

from simple_framework.admin import Administration
from simple_framework.auth import authenticate
from simple_framework.domain import (
    AccessDenied,
    Artifact,
    Caller,
    Conflict,
    Correction,
    InvalidRequest,
    Session,
    Update,
    ValidationRun,
)
from simple_framework.harness import Harness
from simple_framework.reuse import semantic_digest
from simple_framework.sqlite import SQLiteRepository


@dataclass
class Loop:
    repository: SQLiteRepository
    harness: Harness
    admin: Administration
    author: Caller
    reviewer: Caller
    validator: Caller
    consumer: Caller
    session: Session
    contract: Update
    fault: Update


@pytest.fixture
def loop(tmp_path: Path) -> Iterator[Loop]:
    with SQLiteRepository(tmp_path / "reuse.sqlite3") as repository:
        admin = Administration(repository)
        admin.create_workspace("team", "Synthetic")
        for task in ("investigate", "fresh"):
            admin.create_task("team", task, task)
        callers = {}
        for name, role in (
            ("author", "contributor"),
            ("reviewer", "reviewer"),
            ("validator", "validator"),
            ("consumer", "contributor"),
        ):
            token = admin.register_client(name, name, "local", role)
            admin.set_membership("team", name, True)
            callers[name] = authenticate(repository, token)
        harness = Harness(repository)
        session = harness.join_workspace(callers["author"], "team", "investigate", "join").session
        contract = harness.post_update(
            callers["author"],
            session.id,
            "decision",
            "Project A contract v1",
            "contract",
            [],
            goals=["workload"],
        )
        fault = harness.post_update(
            callers["author"],
            session.id,
            "proposal",
            "All lines count",
            "fault",
            [],
        )
        fault = harness.review_update(
            callers["reviewer"],
            "team",
            fault.id,
            fault.revision,
            "rejected",
            "reject",
        )
        yield Loop(
            repository,
            harness,
            admin,
            callers["author"],
            callers["reviewer"],
            callers["validator"],
            callers["consumer"],
            session,
            contract,
            fault,
        )


def artifact(name: str, digest: str = "a" * 64) -> Artifact:
    return {"reference": name, "sha256": digest}


def correction(loop: Loop) -> Correction:
    return {
        "category": "knowledge",
        "corrects": {"update_id": loop.fault.id, "revision": loop.fault.revision},
        "mistake": "Cancelled lines counted",
        "rationale": "The declared project contract excludes them",
        "scope": {"project": "A", "workflow": "triage"},
        "exclusions": [{"mode": "returns"}],
        "required_facts": ["identity"],
        "facts": [{"update_id": loop.contract.id, "revision": loop.contract.revision}],
        "cases": [
            {
                "id": "workload",
                "code": artifact("code.py"),
                "inputs": artifact("cases.json"),
                "test": artifact("check.py"),
            }
        ],
        "propose_reuse": True,
    }


def propose(loop: Loop, metadata: Correction | None = None) -> Update:
    return loop.harness.post_update(
        loop.author,
        loop.session.id,
        "finding",
        "Exclude cancelled lines for this scope",
        "correction",
        [],
        [
            {"update_id": loop.fault.id, "revision": loop.fault.revision},
            {"update_id": loop.contract.id, "revision": loop.contract.revision},
        ],
        correction=metadata or correction(loop),
        goals=["workload"],
    )


def record(
    loop: Loop, update: Update, outcome: str = "passed", request: str = "run"
) -> ValidationRun:
    assert update.correction is not None
    case = update.correction["cases"][0]
    return loop.harness.record_validation(
        loop.validator,
        "team",
        update.id,
        semantic_digest(update),
        case["id"],
        {name: case[name] for name in ("code", "inputs", "test")},  # type: ignore[literal-required]
        outcome,
        artifact("runs/result.json"),
        request,
    )


def approve(loop: Loop, update: Update, request: str = "approve") -> Update:
    return loop.harness.review_update(
        loop.reviewer,
        "team",
        update.id,
        update.revision,
        "accepted",
        request,
        approve_reuse=True,
    )


def facts() -> dict[str, str]:
    return {"project": "A", "workflow": "triage", "identity": "issue-entry", "mode": "normal"}


def test_review_replay_and_fresh_session_reuse_survive_restart(loop: Loop) -> None:
    update = propose(loop)
    failed = record(loop, update, "failed", "failed-run")
    with pytest.raises(Conflict, match="validation"):
        approve(loop, update)
    passed = record(loop, update)
    accepted = approve(loop, update)
    assert semantic_digest(accepted) == semantic_digest(update)
    assert accepted.revision == update.revision + 1
    with SQLiteRepository(loop.repository.path) as reopened:
        harness = Harness(reopened)
        harness.join_workspace(loop.consumer, "team", "fresh", "fresh")
        packet = harness.prepare_context(loop.consumer, "team", "fresh", "workload", facts())
    assert packet.status == "ready"
    assert [item.update.id for item in packet.items] == [accepted.id]
    assert [run.id for run in packet.items[0].validation] == [passed.id]
    assert {r["update_id"]: r["revision"] for r in packet.manifest}[
        accepted.id
    ] == accepted.revision
    assert failed in loop.repository.records(ValidationRun, "team")
    assert loop.repository.get(Update, loop.fault.id, "team").status == "rejected"  # type: ignore[union-attr]


def test_self_review_and_contributor_forgery_do_not_promote(loop: Loop) -> None:
    update = propose(loop)
    token = loop.admin.register_client("author", "self-review", "local", "reviewer")
    before = list(loop.repository.connection.iterdump())
    with pytest.raises(AccessDenied):
        loop.harness.review_update(
            authenticate(loop.repository, token),
            "team",
            update.id,
            1,
            "accepted",
            "self",
            approve_reuse=True,
        )
    case = correction(loop)["cases"][0]
    with pytest.raises(AccessDenied):
        loop.harness.record_validation(
            loop.author,
            "team",
            update.id,
            semantic_digest(update),
            "workload",
            {"code": case["code"], "inputs": case["inputs"], "test": case["test"]},
            "passed",
            artifact("forged"),
            "forged",
        )
    assert list(loop.repository.connection.iterdump()) == before
    ordinary = loop.harness.review_update(
        loop.reviewer, "team", update.id, 1, "accepted", "ordinary"
    )
    assert ordinary.reuse_approval is None
    packet = loop.harness.prepare_context(loop.consumer, "team", "fresh", "workload", facts())
    assert not packet.items


@pytest.mark.parametrize("change", ["text", "scope", "artifact"])
def test_content_changes_require_revalidation_and_reapproval(loop: Loop, change: str) -> None:
    original = propose(loop)
    record(loop, original)
    accepted = approve(loop, original)
    metadata = correction(loop)
    text = original.text
    if change == "text":
        text = "New meaning"
    elif change == "scope":
        metadata["scope"]["project"] = "B"
    else:
        metadata["cases"][0]["code"]["sha256"] = "b" * 64
    amended = loop.harness.post_update(
        loop.author,
        loop.session.id,
        "finding",
        text,
        "amend",
        [],
        update_id=accepted.id,
        expected_revision=accepted.revision,
        correction=metadata,
    )
    assert amended.reuse_approval is None and amended.status == "proposed"
    with pytest.raises(Conflict, match="validation"):
        approve(loop, amended, "new-approval")
    record(loop, amended, request="new-run")
    assert approve(loop, amended, "new-approval").reuse_approval is not None


@pytest.mark.parametrize("change", ["subject", "artifact", "case"])
def test_mismatched_validation_rolls_back(loop: Loop, change: str) -> None:
    update = propose(loop)
    case = correction(loop)["cases"][0]
    artifacts = {"code": case["code"], "inputs": case["inputs"], "test": case["test"]}
    digest = semantic_digest(update)
    case_id = "workload"
    if change == "subject":
        digest = "b" * 64
    elif change == "artifact":
        artifacts["code"] = artifact("different.py")
    else:
        case_id = "unknown"
    before = list(loop.repository.connection.iterdump())
    with pytest.raises(Conflict):
        loop.harness.record_validation(
            loop.validator,
            "team",
            update.id,
            digest,
            case_id,
            artifacts,
            "passed",
            artifact("result"),
            "run",
        )
    assert list(loop.repository.connection.iterdump()) == before


def test_upstream_change_marks_consumers_stale_until_replay_and_review(loop: Loop) -> None:
    update = propose(loop)
    record(loop, update)
    accepted = approve(loop, update)
    downstream = loop.harness.post_update(
        loop.consumer,
        loop.harness.join_workspace(loop.consumer, "team", "fresh", "fresh").session.id,
        "progress",
        "Applied scoped rule",
        "result",
        [],
        [{"update_id": accepted.id, "revision": accepted.revision}],
        goals=["workload"],
    )
    revised = loop.harness.post_update(
        loop.author,
        loop.session.id,
        "decision",
        "Contract v2",
        "change-contract",
        [],
        update_id=loop.contract.id,
        expected_revision=loop.contract.revision,
    )
    packet = loop.harness.prepare_context(loop.consumer, "team", "fresh", "workload", facts())
    assert not packet.items
    assert {i.update_id for i in packet.issues} == {accepted.id, downstream.id, revised.id} - {
        revised.id
    }
    assert all(i.reason == "needs_revalidation" for i in packet.issues)
    metadata = correction(loop)
    metadata["facts"] = [{"update_id": revised.id, "revision": revised.revision}]
    renewed = loop.harness.post_update(
        loop.author,
        loop.session.id,
        "finding",
        update.text,
        "renew",
        [],
        metadata["facts"],
        update_id=accepted.id,
        expected_revision=accepted.revision,
        correction=metadata,
    )
    record(loop, renewed, request="new-run")
    approved = approve(loop, renewed, "new-approval")
    packet = loop.harness.prepare_context(loop.consumer, "team", "fresh", "workload", facts())
    assert [i.update.id for i in packet.items] == [approved.id]


@pytest.mark.parametrize(
    "scope,reason",
    [
        ({"project": "B", "workflow": "triage", "identity": "key", "mode": "normal"}, None),
        ({"project": "A", "workflow": "triage", "mode": "normal"}, "unknown_applicability"),
        ({"project": "A", "workflow": "triage", "identity": "key"}, "unknown_applicability"),
        ({"project": "A", "workflow": "triage", "identity": "key", "mode": "returns"}, None),
    ],
)
def test_scope_never_assumes_missing_facts(
    loop: Loop, scope: dict[str, str], reason: str | None
) -> None:
    update = propose(loop)
    record(loop, update)
    approve(loop, update)
    loop.harness.review_update(
        loop.reviewer, "team", loop.contract.id, loop.contract.revision, "accepted", "accept-fact"
    )
    packet = loop.harness.prepare_context(loop.consumer, "team", "fresh", "workload", scope)
    assert not packet.items and packet.status == "insufficient_context"
    assert [i.reason for i in packet.issues] == ([reason] if reason else [])


def test_bounded_packet_ignores_noise_and_never_clips_qualifications(loop: Loop) -> None:
    for index in range(120):
        loop.harness.post_update(
            loop.author, loop.session.id, "progress", "Irrelevant " * 100, f"noise-{index}", []
        )
    update = propose(loop)
    record(loop, update)
    approved = approve(loop, update)
    packet = loop.harness.prepare_context(
        loop.consumer, "team", "fresh", "workload", facts(), 16384
    )
    assert packet.status == "ready" and len(packet.items) == 1
    encoded = json.dumps(asdict(packet), ensure_ascii=False, separators=(",", ":")).encode()
    assert len(encoded) <= 16384 and b"Irrelevant" not in encoded
    assert packet.items[0].update.correction == approved.correction
    tiny = loop.harness.prepare_context(loop.consumer, "team", "fresh", "workload", facts(), 512)
    assert tiny.status == "insufficient_budget" and not tiny.items
    assert len(json.dumps(asdict(tiny), ensure_ascii=False, separators=(",", ":")).encode()) <= 512


def test_revoked_dependency_hides_packets_history_and_committed_retries(loop: Loop) -> None:
    update = propose(loop)
    run = record(loop, update)
    accepted = approve(loop, update)
    loop.admin.register_source(
        "team",
        "private",
        "SECRET",
        ["author", "reviewer", "validator"],
        ["author", "reviewer", "validator"],
    )
    loop.harness.post_update(
        loop.author,
        loop.session.id,
        "decision",
        loop.contract.text,
        "restrict",
        ["private"],
        update_id=loop.contract.id,
        expected_revision=loop.contract.revision,
    )
    packet = loop.harness.prepare_context(loop.consumer, "team", "fresh", "workload", facts())
    text = json.dumps(asdict(packet))
    assert all(value not in text for value in (accepted.id, run.id, loop.contract.id, "SECRET"))
    assert not packet.items and not packet.issues
    loop.admin.set_source_access("team", "private", [], [])
    with pytest.raises(AccessDenied):
        approve(loop, update)
    with pytest.raises(AccessDenied):
        record(loop, update)


def test_validation_retry_preserves_original_and_rejects_changed_arguments(loop: Loop) -> None:
    update = propose(loop)
    first = record(loop, update)
    approved = approve(loop, update)
    assert record(loop, update) == first
    with pytest.raises(Conflict, match="Request ID"):
        record(loop, update, "failed")
    amended = loop.harness.post_update(
        loop.author,
        loop.session.id,
        "finding",
        "Changed",
        "edit",
        [],
        update_id=approved.id,
        expected_revision=approved.revision,
    )
    assert record(loop, update) == first
    with pytest.raises(Conflict, match="subject"):
        record(loop, update, request="stale-run")
    assert semantic_digest(amended) != semantic_digest(update)


def test_dependency_cycles_and_missing_records_cannot_leak(loop: Loop) -> None:
    update = propose(loop)
    with loop.repository.transaction():
        loop.repository.save(replace(loop.contract, derived_from={update.id: 1}))
    packet = loop.harness.prepare_context(loop.consumer, "team", "fresh", "workload", facts())
    assert not packet.items
    with loop.repository.transaction():
        loop.repository.save(replace(loop.contract, derived_from={"unknown-private": 1}))
    packet = loop.harness.prepare_context(loop.consumer, "team", "fresh", "workload", facts())
    assert update.id not in json.dumps(asdict(packet))


def test_old_database_rows_remain_readable(loop: Loop) -> None:
    with loop.repository.transaction():
        payload = asdict(loop.contract)
        for key in ("correction", "goals", "reuse_approval"):
            payload.pop(key)
        loop.repository.connection.execute(
            "UPDATE records SET payload=? WHERE id=? AND record_type='Update'",
            (json.dumps(payload), loop.contract.id),
        )
    with SQLiteRepository(loop.repository.path) as reopened:
        restored = reopened.get(Update, loop.contract.id, "team")
    assert restored is not None and restored.correction is None and restored.goals == []


def test_procedure_requires_explicit_fact_references(loop: Loop) -> None:
    metadata = correction(loop)
    metadata["category"] = "procedure"
    metadata["facts"] = []
    with pytest.raises(InvalidRequest, match="facts"):
        propose(loop, metadata)


def test_later_failed_replay_withdraws_guidance_without_erasing_approval(loop: Loop) -> None:
    update = propose(loop)
    record(loop, update)
    accepted = approve(loop, update)
    record(loop, accepted, "failed", "rerun")
    packet = loop.harness.prepare_context(loop.consumer, "team", "fresh", "workload", facts())
    assert not packet.items and packet.issues[0].reason == "needs_revalidation"
    stored = loop.repository.get(Update, accepted.id, "team")
    assert stored is not None and stored.status == "accepted" and stored.reuse_approval is not None


def dependent_procedure(loop: Loop, knowledge: Update) -> Update:
    metadata = correction(loop)
    metadata["category"] = "procedure"
    metadata["scope"] = {"workflow": "triage"}
    metadata["facts"] = [{"update_id": knowledge.id, "revision": knowledge.revision}]
    proposal = loop.harness.post_update(
        loop.author,
        loop.session.id,
        "finding",
        "Check the referenced facts before reconciling",
        "procedure",
        [],
        [metadata["corrects"], *metadata["facts"]],
        correction=metadata,
        goals=["workload"],
    )
    record(loop, proposal, request="procedure-run")
    return approve(loop, proposal, "procedure-review")


@pytest.mark.parametrize("project", ["B", None])
def test_procedure_inherits_fact_applicability(loop: Loop, project: str | None) -> None:
    proposal = propose(loop)
    record(loop, proposal)
    knowledge = approve(loop, proposal)
    procedure = dependent_procedure(loop, knowledge)
    declared = facts()
    if project:
        declared["project"] = project
    else:
        del declared["project"]
    packet = loop.harness.prepare_context(loop.consumer, "team", "fresh", "workload", declared)
    assert not packet.items
    assert packet.status == "insufficient_context"
    if project is None:
        assert {i.update_id for i in packet.issues} == {knowledge.id, procedure.id}
        assert all(i.missing_facts == ["project"] for i in packet.issues)


@pytest.mark.parametrize("withdrawal", ["failed_replay", "scope_approval"])
def test_consumers_need_revalidation_after_fact_eligibility_changes(
    loop: Loop, withdrawal: str
) -> None:
    proposal = propose(loop)
    record(loop, proposal)
    knowledge = approve(loop, proposal)
    procedure = dependent_procedure(loop, knowledge)
    decision = loop.harness.post_update(
        loop.author,
        loop.session.id,
        "decision",
        "Use the declared fact",
        "decision",
        [],
        [{"update_id": knowledge.id, "revision": knowledge.revision}],
    )
    decision = loop.harness.review_update(
        loop.reviewer, "team", decision.id, 1, "accepted", "decision-review"
    )
    earlier = loop.harness.prepare_context(loop.consumer, "team", "investigate", "other", facts())
    assert [item.update.id for item in earlier.items] == [decision.id]
    assert {run.update_id for run in earlier.items[0].validation} == {knowledge.id}
    if withdrawal == "failed_replay":
        record(loop, knowledge, "failed", "failed-rerun")
    else:
        loop.harness.review_update(
            loop.reviewer, "team", knowledge.id, knowledge.revision, "accepted", "ordinary-review"
        )
    packet = loop.harness.prepare_context(loop.consumer, "team", "investigate", "workload", facts())
    assert not packet.items
    assert {i.update_id for i in packet.issues} == {knowledge.id, procedure.id, decision.id}
    assert packet.status == "insufficient_context"


def test_task_membership_does_not_bypass_correction_goal(loop: Loop) -> None:
    proposal = propose(loop)
    record(loop, proposal)
    approve(loop, proposal)
    packet = loop.harness.prepare_context(
        loop.consumer, "team", "investigate", "unrelated", facts()
    )
    assert not packet.items
