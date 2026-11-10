import json
import re
from dataclasses import asdict, replace
from hashlib import sha256

from simple_framework.domain import (
    Artifact,
    ContextIssue,
    ContextItem,
    ContextPacket,
    Correction,
    InvalidRequest,
    Update,
)


def semantic_digest(update: Update) -> str:
    """Hash declared content without review status, execution results or timestamps."""
    content = {
        key: value
        for key, value in asdict(update).items()
        if key
        not in {
            "revision",
            "status",
            "reviewed_by",
            "changed_at",
            "created_at",
            "session_id",
            "reuse_approval",
        }
    }
    return sha256(json.dumps(content, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_artifact(artifact: Artifact) -> None:
    """Require a reference and a lowercase SHA-256 digest."""
    if (
        not isinstance(artifact, dict)
        or set(artifact) != {"reference", "sha256"}
        or not isinstance(artifact["reference"], str)
        or not 1 <= len(artifact["reference"]) <= 500
        or not isinstance(artifact["sha256"], str)
        or re.fullmatch(r"[0-9a-f]{64}", artifact["sha256"]) is None
    ):
        raise InvalidRequest("Artifacts require a reference and SHA-256 digest")


def validate_facts(facts: dict[str, str]) -> None:
    """Require small explicit fact names and values."""
    if (
        not isinstance(facts, dict)
        or len(facts) > 32
        or any(
            not isinstance(k, str)
            or not isinstance(v, str)
            or not k.strip()
            or not v.strip()
            or len(k) > 128
            or len(v) > 128
            for k, v in facts.items()
        )
    ):
        raise InvalidRequest("Facts require at most 32 nonempty names and values of 128 characters")


def validate_goals(goals: list[str]) -> None:
    """Require bounded explicit goal tags."""
    if (
        not isinstance(goals, list)
        or len(goals) > 10
        or any(not isinstance(g, str) or not g.strip() or len(g) > 128 for g in goals)
        or len(set(goals)) != len(goals)
    ):
        raise InvalidRequest("Use at most ten distinct goal tags")


def validate_correction(value: Correction, dependencies: dict[str, int], goals: list[str]) -> None:
    """Require explicit correction content, scope, exact facts and versioned acceptance cases."""
    if not isinstance(value, dict) or set(value) != {
        "category",
        "corrects",
        "mistake",
        "rationale",
        "scope",
        "exclusions",
        "required_facts",
        "facts",
        "cases",
        "propose_reuse",
    }:
        raise InvalidRequest("Supply all correction fields")
    if (
        not isinstance(value["category"], str)
        or value["category"] not in {"knowledge", "procedure"}
        or type(value["propose_reuse"]) is not bool
    ):
        raise InvalidRequest("Declare knowledge or procedure and explicit reuse intent")
    for text in (value["mistake"], value["rationale"]):
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise InvalidRequest("Correction mistake and rationale need 1-2000 characters")
    validate_facts(value["scope"])
    if not value["scope"] or not goals:
        raise InvalidRequest("Corrections require nonempty scope and goals")
    if not isinstance(value["exclusions"], list) or len(value["exclusions"]) > 10:
        raise InvalidRequest("Use at most ten exclusions")
    for excluded in value["exclusions"]:
        validate_facts(excluded)
        if not excluded:
            raise InvalidRequest("Exclusions must name facts")
    validate_goals(value["required_facts"])
    if not isinstance(value["facts"], list) or len(value["facts"]) > 20:
        raise InvalidRequest("Use at most twenty fact references")
    if value["category"] == "procedure" and not value["facts"]:
        raise InvalidRequest("Procedures require facts as exact references")
    for reference in [value["corrects"], *value["facts"]]:
        if (
            not isinstance(reference, dict)
            or set(reference) != {"update_id", "revision"}
            or not isinstance(reference["update_id"], str)
            or type(reference["revision"]) is not int
            or dependencies.get(reference["update_id"]) != reference["revision"]
        ):
            raise InvalidRequest("Correction references must also be declared exact dependencies")
    if not isinstance(value["cases"], list) or not 1 <= len(value["cases"]) <= 5:
        raise InvalidRequest("Corrections require one to five acceptance cases")
    names = set()
    for case in value["cases"]:
        if (
            not isinstance(case, dict)
            or set(case) != {"id", "code", "inputs", "test"}
            or not isinstance(case["id"], str)
            or not 1 <= len(case["id"]) <= 128
            or case["id"] in names
        ):
            raise InvalidRequest(
                "Acceptance cases need distinct IDs and code, inputs and test artifacts"
            )
        names.add(case["id"])
        for artifact in (case["code"], case["inputs"], case["test"]):
            validate_artifact(artifact)


def applicability(correction: Correction, facts: dict[str, str]) -> tuple[str, list[str]]:
    """Match exact facts conservatively, including unknown exclusion facts."""
    if any(key in facts and facts[key] != value for key, value in correction["scope"].items()):
        return "out_of_scope", []
    missing = set(correction["scope"]) | set(correction["required_facts"])
    missing -= facts.keys()
    for excluded in correction["exclusions"]:
        if any(key in facts and facts[key] != value for key, value in excluded.items()):
            continue
        if all(key in facts for key in excluded):
            return "out_of_scope", []
        missing.update(excluded.keys() - facts.keys())
    return ("unknown_applicability", sorted(missing)) if missing else ("match", [])


def packet_json(packet: ContextPacket) -> str:
    """Encode the packet and its envelope for the UTF-8 byte contract."""
    return json.dumps(asdict(packet), ensure_ascii=False, separators=(",", ":"))


def bounded_packet(
    workspace_id: str,
    task_id: str,
    goal: str,
    budget: int,
    items: list[ContextItem],
    issues: list[ContextIssue],
) -> ContextPacket:
    """Select complete items and report any omitted content caused by the byte limit."""
    packet = ContextPacket(workspace_id, task_id, goal, budget, "insufficient_context", [], [], [])
    if len(packet_json(packet).encode()) > budget:
        raise InvalidRequest("Budget too small for the packet envelope")
    omitted = False
    for item in items:
        records = {r["update_id"]: r["revision"] for r in packet.manifest}
        records.update({u.id: u.revision for u in [item.update, *item.support]})
        candidate = replace(
            packet,
            items=[*packet.items, item],
            status="ready",
            manifest=[{"update_id": key, "revision": rev} for key, rev in sorted(records.items())],
        )
        if len(packet_json(candidate).encode()) <= budget - 32:
            packet = candidate
        else:
            omitted = True
    for issue in issues:
        candidate = replace(packet, issues=[*packet.issues, issue])
        if len(packet_json(candidate).encode()) <= budget - 32:
            packet = candidate
        else:
            omitted = True
    if omitted:
        return replace(packet, status="insufficient_budget")
    return replace(packet, status="insufficient_context") if issues else packet
