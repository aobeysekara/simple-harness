import argparse
import json
import shutil
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from typing import Any

from examples.reuse_case.model import transform
from simple_framework.admin import Administration
from simple_framework.auth import authenticate
from simple_framework.domain import (
    AcceptanceCase,
    Artifact,
    Caller,
    ContextPacket,
    Correction,
    Update,
)
from simple_framework.harness import Harness
from simple_framework.local_validation import run_case
from simple_framework.reuse import packet_json
from simple_framework.sqlite import SQLiteRepository

FIXTURE = Path(__file__).parent / "reuse_case"
FACTS = {"project": "A", "workflow": "triage", "identity": "issue-entry", "purpose": "analysis"}


def write_json(path: Path, value: object) -> None:
    """Write synthetic demonstration records without credentials."""
    path.write_text(json.dumps(value, indent=2) + "\n")


def artifact(directory: Path, name: str) -> Artifact:
    """Identify the exact fixture bytes selected for replay."""
    return {"reference": name, "sha256": sha256((directory / name).read_bytes()).hexdigest()}


def acceptance_case(directory: Path, code: str) -> AcceptanceCase:
    """Declare the fixture's code, inputs and trusted checker."""
    return {
        "id": "workload",
        "code": artifact(directory, code),
        "inputs": artifact(directory, "cases.json"),
        "test": artifact(directory, "check.py"),
    }


def metadata(fault: Update, fact: Update, case: AcceptanceCase, category: str) -> Correction:
    """Declare a project-scoped correction or a procedure that references its facts."""
    return {
        "category": "knowledge" if category == "knowledge" else "procedure",
        "corrects": {"update_id": fault.id, "revision": fault.revision},
        "mistake": "Cancelled workload counted"
        if category == "knowledge"
        else "Raw IDs treated as stable",
        "rationale": "Replay checks the contract, duplicate lines and reordered issue entries",
        "scope": {"project": "A", "workflow": "triage"},
        "exclusions": [{"purpose": "production"}],
        "required_facts": ["identity"],
        "facts": [{"update_id": fact.id, "revision": fact.revision}],
        "cases": [case],
        "propose_reuse": True,
    }


def review(
    harness: Harness, caller: Caller, update: Update, key: str, reuse: bool = False
) -> Update:
    """Record a simulated approval under the separate reviewer identity."""
    return harness.review_update(
        caller, "reuse", update.id, update.revision, "accepted", key, approve_reuse=reuse
    )


def markdown(packet: ContextPacket) -> str:
    """Export the same permitted content with maintained review and evidence labels."""
    sections = [
        "# Maintained handover",
        "Synthetic project A triage workload.",
        "Review is simulated operator approval, not independent verification.",
        "Use current exact references below. Scope and exclusions remain binding.",
        f"Context status: {packet.status}. Eligible guidance: {len(packet.items)}.",
    ]
    for item in packet.items:
        update = item.update
        sections.extend(
            [
                f"## {update.id}@{update.revision}: {update.status}",
                update.text,
                "Correction and applicability:",
                "```json\n" + json.dumps(update.correction, indent=2) + "\n```",
                "Approval of content and scope:",
                "```json\n" + json.dumps(update.reuse_approval, indent=2) + "\n```",
            ]
        )
        for supporting in item.support:
            sections.extend(
                [
                    f"### Source {supporting.id}@{supporting.revision}: {supporting.status}",
                    supporting.text,
                ]
            )
        sections.extend(
            [
                "Machine replay evidence (specific cases only):",
                "```json\n" + json.dumps([asdict(r) for r in item.validation], indent=2) + "\n```",
            ]
        )
    sections.extend(
        ["## Exact manifest", "```json\n" + json.dumps(packet.manifest, indent=2) + "\n```"]
    )
    return "\n\n".join(sections) + "\n"


def run_demo(directory: Path) -> dict[str, Any]:
    """Run the synthetic correction loop and prepare equivalent fresh-consumer conditions."""
    directory.mkdir(parents=True, mode=0o700)
    operator = directory / "operator"
    operator.mkdir(mode=0o700)
    cases = operator / "cases"
    cases.mkdir()
    for name in ("model.py", "faulty.py", "cases.json", "check.py"):
        shutil.copyfile(FIXTURE / name, cases / name)
    for label, code in (("initial", "faulty.py"), ("fixed", "model.py")):
        write_json(
            cases / f"{label}.json",
            {"id": "workload", "code": code, "inputs": "cases.json", "test": "check.py"},
        )
    database = operator / "workspace.sqlite3"
    with SQLiteRepository(database) as repository:
        admin = Administration(repository)
        admin.create_workspace("reuse", "Synthetic workload corrections")
        for name in ("investigation", "fresh", "continuation"):
            admin.create_task("reuse", name, name)
        callers = {}
        for name, role in (
            ("alice", "contributor"),
            ("reviewer", "reviewer"),
            ("validator", "validator"),
            ("bob", "contributor"),
            ("markdown", "contributor"),
            ("ablation", "contributor"),
        ):
            token = admin.register_client(name, name, "synthetic-local", role)
            admin.set_membership("reuse", name, True)
            token_path = operator / f"{name}.token"
            token_path.write_text(token)
            token_path.chmod(0o600)
            callers[name] = authenticate(repository, token)
        admin.register_source(
            "reuse",
            "public-contract",
            "Synthetic checked-in contract",
            list(callers),
            list(callers),
        )
        admin.register_source(
            "reuse", "private", "Restricted synthetic addendum", ["alice"], ["alice"]
        )
        harness = Harness(repository)
        session = harness.open_session(callers["alice"], "reuse", "investigation", "alice")
        contract_value = json.loads((FIXTURE / "cases.json").read_text())["cases"][0]["contract"]
        contract = harness.post_update(
            callers["alice"],
            session.id,
            "decision",
            json.dumps({**contract_value, "declared_version": 1}),
            "contract",
            ["public-contract"],
        )
        contract = review(harness, callers["reviewer"], contract, "contract-review")
        fault = harness.post_update(
            callers["alice"],
            session.id,
            "proposal",
            "All lines counted, producing TYPE-A workload 20",
            "fault",
            ["public-contract"],
        )
        fault = harness.review_update(
            callers["reviewer"], "reuse", fault.id, 1, "rejected", "fault-review"
        )
        initial = metadata(fault, contract, acceptance_case(cases, "faulty.py"), "knowledge")
        knowledge = harness.post_update(
            callers["alice"],
            session.id,
            "finding",
            "For project A triage workload, exclude cancelled lines. "
            "Do not apply this rule to other project workflows.",
            "knowledge",
            ["public-contract"],
            [initial["corrects"], *initial["facts"]],
            correction=initial,
            goals=["workload"],
        )
        failed = run_case(
            harness,
            callers["validator"],
            "reuse",
            knowledge.id,
            cases / "initial.json",
            operator / "runs",
            "initial-run",
        )
        assert failed.outcome == "failed"
        fixed = metadata(fault, contract, acceptance_case(cases, "model.py"), "knowledge")
        knowledge = harness.post_update(
            callers["alice"],
            session.id,
            "finding",
            knowledge.text,
            "fix",
            ["public-contract"],
            update_id=knowledge.id,
            expected_revision=knowledge.revision,
            correction=fixed,
        )
        passed = run_case(
            harness,
            callers["validator"],
            "reuse",
            knowledge.id,
            cases / "fixed.json",
            operator / "runs",
            "fixed-run",
        )
        assert passed.outcome == "passed"
        knowledge = review(harness, callers["reviewer"], knowledge, "reuse-approval", True)
        identity_fault = harness.post_update(
            callers["alice"],
            session.id,
            "proposal",
            "Join different extracts using raw row IDs",
            "identity-fault",
            ["public-contract"],
        )
        identity_fault = harness.review_update(
            callers["reviewer"], "reuse", identity_fault.id, 1, "rejected", "identity-reject"
        )
        procedure_data = metadata(
            identity_fault, knowledge, acceptance_case(cases, "model.py"), "procedure"
        )
        procedure = harness.post_update(
            callers["alice"],
            session.id,
            "finding",
            "Resolve the record identity from the referenced scoped facts. "
            "Compare workload by that identity within its project/workflow. "
            "Reorder rows and regenerate file-local raw IDs; totals must remain reconciled. "
            "Stop if the identity is missing or contradictory.",
            "procedure",
            ["public-contract"],
            [procedure_data["corrects"], *procedure_data["facts"]],
            correction=procedure_data,
            goals=["workload"],
        )
        run_case(
            harness,
            callers["validator"],
            "reuse",
            procedure.id,
            cases / "fixed.json",
            operator / "runs",
            "procedure-run",
        )
        procedure = review(harness, callers["reviewer"], procedure, "procedure-approval", True)
        for index in range(96):
            harness.post_update(
                callers["alice"],
                session.id,
                "progress",
                "Unrelated release note. " * 20,
                f"noise-{index}",
                [],
            )
        harness.post_update(
            callers["alice"],
            session.id,
            "question",
            "RESTRICTED-CANARY",
            "private",
            ["private"],
            goals=["workload"],
        )
        harness.open_session(callers["bob"], "reuse", "fresh", "bob")
        initial_packet = harness.prepare_context(
            callers["bob"], "reuse", "fresh", "workload", FACTS
        )
        assert initial_packet.status == "ready" and len(initial_packet.items) == 2
        write_json(operator / "before-contract-change.json", asdict(initial_packet))
        (operator / "before-contract-change.md").write_text(markdown(initial_packet))
        contract = harness.post_update(
            callers["alice"],
            session.id,
            "decision",
            json.dumps({**contract_value, "declared_version": 2}),
            "contract-v2",
            ["public-contract"],
            update_id=contract.id,
            expected_revision=contract.revision,
        )
        contract = review(harness, callers["reviewer"], contract, "contract-v2-review")
        stale = harness.prepare_context(callers["bob"], "reuse", "fresh", "workload", FACTS)
        assert not stale.items and {i.update_id for i in stale.issues} == {
            knowledge.id,
            procedure.id,
        }
        write_json(operator / "stale.json", asdict(stale))
        fixed["facts"] = [{"update_id": contract.id, "revision": contract.revision}]
        knowledge = harness.post_update(
            callers["alice"],
            session.id,
            "finding",
            knowledge.text,
            "reconsider",
            ["public-contract"],
            fixed["facts"],
            update_id=knowledge.id,
            expected_revision=knowledge.revision,
            correction=fixed,
        )
        run_case(
            harness,
            callers["validator"],
            "reuse",
            knowledge.id,
            cases / "fixed.json",
            operator / "runs",
            "revalidated",
        )
        awaiting_review = harness.prepare_context(
            callers["bob"], "reuse", "fresh", "workload", FACTS
        )
        assert not awaiting_review.items
        write_json(operator / "awaiting-review.json", asdict(awaiting_review))
        knowledge = review(harness, callers["reviewer"], knowledge, "reapproved", True)
        procedure_data["facts"] = [{"update_id": knowledge.id, "revision": knowledge.revision}]
        procedure = harness.post_update(
            callers["alice"],
            session.id,
            "finding",
            procedure.text,
            "procedure-reconsider",
            ["public-contract"],
            procedure_data["facts"],
            update_id=procedure.id,
            expected_revision=procedure.revision,
            correction=procedure_data,
        )
        run_case(
            harness,
            callers["validator"],
            "reuse",
            procedure.id,
            cases / "fixed.json",
            operator / "runs",
            "procedure-revalidated",
        )
        procedure = review(harness, callers["reviewer"], procedure, "procedure-reapproved", True)
        harness.end_session(
            callers["alice"],
            session.id,
            "end",
            "Scoped correction and procedure are ready for review-aware reuse.",
            ["public-contract"],
            [
                {"update_id": knowledge.id, "revision": knowledge.revision},
                {"update_id": procedure.id, "revision": procedure.revision},
            ],
        )
    with SQLiteRepository(database) as repository:
        harness = Harness(repository)
        opened = harness.open_session(callers["bob"], "reuse", "fresh", "after-restart")
        write_json(operator / "opened-session.json", asdict(opened))
        packet = harness.prepare_context(callers["bob"], "reuse", "fresh", "workload", FACTS)
        other = harness.prepare_context(
            callers["bob"], "reuse", "fresh", "workload", {**FACTS, "project": "B"}
        )
        missing = harness.prepare_context(
            callers["bob"], "reuse", "fresh", "workload", {"workflow": "triage"}
        )
        assert packet.status == "ready" and not other.items and not missing.items
        assert all(i.reason == "unknown_applicability" for i in missing.issues)
        assert "RESTRICTED-CANARY" not in packet_json(packet)
        write_json(operator / "packet.json", asdict(packet))
        write_json(operator / "other-project.json", asdict(other))
        write_json(operator / "unknown.json", asdict(missing))
    brief = (
        (FIXTURE / "consumer/BRIEF.md").read_text()
        + "\nThe goal tag is workload. Known facts: "
        + json.dumps(FACTS)
        + ".\n"
    )
    for condition in ("simple", "markdown", "ablation"):
        participant = directory / condition
        participant.mkdir()
        shutil.copyfile(FIXTURE / "consumer/extract.json", participant / "extract.json")
        shutil.copyfile(FIXTURE / "consumer/API.md", participant / "API.md")
        context_note = (
            "Use the configured SIMple MCP server, workspace reuse, task fresh. "
            "Open a session without history, then prepare task context. "
            "Use a context budget of 16384 bytes. Discover operation schemas "
            "through the native client. "
            "Record your outcomes through the harness using exact contribution references."
        )
        if condition == "markdown":
            context_note = "Use HANDOVER.md as the maintained shared context."
            (participant / "HANDOVER.md").write_text(markdown(packet))
        elif condition == "ablation":
            context_note = (
                "Use CONTRACT.json as the supplied contract. "
                "No promoted correction is supplied in this condition."
            )
            write_json(participant / "CONTRACT.json", {**contract_value, "declared_version": 2})
        (participant / "BRIEF.md").write_text(brief + "\n" + context_note + "\n")
    extract = json.loads((FIXTURE / "consumer/extract.json").read_text())
    result = transform(extract["rows"], contract_value)
    report = {
        "fixture": "Synthetic issue-triage fixture",
        "status": "Passed deterministic integration; live consumer comparison not run",
        "review": "Simulated operator using a separate reviewer identity",
        "packet_bytes": len(packet_json(packet).encode()),
        "session_bytes": len(json.dumps(asdict(opened), separators=(",", ":")).encode()),
        "budget": 16384,
        "rejected_proposals": [fault.id, identity_fault.id],
        "failed_run": failed.id,
        "initial_passed_run": passed.id,
        "current_guidance": packet.manifest,
        "scripted_fresh_result": result,
        "conditions": {
            "simple": "Native MCP context",
            "markdown": "Same permitted guidance, facts and evidence",
            "ablation": "Same contract and extract, promoted corrections absent",
        },
        "limits": (
            "Scripted consumer is not evidence that guidance influenced an agent. "
            "No productivity claim."
        ),
    }
    write_json(directory / "results.json", report)
    return report


def main() -> None:
    """Run a deterministic correction loop without model calls."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run_demo(args.directory.resolve()), indent=2))


if __name__ == "__main__":
    main()
