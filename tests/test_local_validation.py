import fcntl
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from simple_framework.admin import Administration
from simple_framework.auth import authenticate
from simple_framework.domain import (
    AccessDenied,
    Artifact,
    Caller,
    Conflict,
    Correction,
    InvalidRequest,
    Update,
    ValidationRun,
)
from simple_framework.harness import Harness
from simple_framework.local_validation import run_case
from simple_framework.sqlite import SQLiteRepository


@dataclass
class LocalCase:
    """Keep a local validator and its selected fixture files."""

    repository: SQLiteRepository
    harness: Harness
    admin: Administration
    author: Caller
    validator: Caller
    update: Update
    manifest: Path
    evidence: Path


def _artifact(root: Path, name: str) -> Artifact:
    return {"reference": name, "sha256": sha256((root / name).read_bytes()).hexdigest()}


@pytest.fixture
def local_case(tmp_path: Path, request: pytest.FixtureRequest) -> Iterator[LocalCase]:
    root = tmp_path / "case"
    root.mkdir()
    source = Path(__file__).resolve().parents[1] / "examples" / "reuse_case"
    code_name = getattr(request, "param", "model.py")
    (root / "model.py").write_bytes((source / code_name).read_bytes())
    for name in ("cases.json", "check.py"):
        (root / name).write_bytes((source / name).read_bytes())
    manifest = root / "local-case.json"
    manifest.write_text(
        json.dumps(
            {"id": "workload", "code": "model.py", "inputs": "cases.json", "test": "check.py"}
        )
    )
    with SQLiteRepository(tmp_path / "workspace.sqlite3") as repository:
        admin = Administration(repository)
        admin.create_workspace("team", "Synthetic")
        admin.create_task("team", "workload", "Workload")
        author_token = admin.register_client("author", "author", "local", "contributor")
        validator_token = admin.register_client("validator", "validator", "local", "validator")
        for user in ("author", "validator"):
            admin.set_membership("team", user, True)
        author = authenticate(repository, author_token)
        validator = authenticate(repository, validator_token)
        harness = Harness(repository)
        session = harness.join_workspace(author, "team", "workload", "join").session
        fault = harness.post_update(author, session.id, "proposal", "Count every line", "fault", [])
        correction: Correction = {
            "category": "knowledge",
            "corrects": {"update_id": fault.id, "revision": fault.revision},
            "mistake": "Cancelled lines count for A",
            "rationale": "Apply the explicit project contract",
            "scope": {"project": "A", "workflow": "triage"},
            "exclusions": [],
            "required_facts": ["identity"],
            "facts": [],
            "cases": [
                {
                    "id": "workload",
                    "code": _artifact(root, "model.py"),
                    "inputs": _artifact(root, "cases.json"),
                    "test": _artifact(root, "check.py"),
                }
            ],
            "propose_reuse": True,
        }
        update = harness.post_update(
            author,
            session.id,
            "finding",
            "Use the project contract",
            "correction",
            [],
            [{"update_id": fault.id, "revision": fault.revision}],
            correction=correction,
            goals=["workload"],
        )
        yield LocalCase(
            repository, harness, admin, author, validator, update, manifest, tmp_path / "evidence"
        )


def _run(case: LocalCase, request_id: str = "run") -> ValidationRun:
    return run_case(
        case.harness,
        case.validator,
        "team",
        case.update.id,
        case.manifest,
        case.evidence,
        request_id,
    )


def test_cli_without_fcntl_supports_reads_and_reports_unsupported_validation(
    local_case: LocalCase,
) -> None:
    token = local_case.admin.register_client("portable", "portable", "local", "validator")
    local_case.admin.set_membership("team", "portable", True)
    command = [
        sys.executable,
        "-c",
        "import sys; sys.modules['fcntl'] = None; from simple_framework.cli import main; main()",
        "--db",
        str(local_case.repository.path),
    ]
    for arguments in (["--help"], ["read", "team"]):
        result = subprocess.run(
            [*command, *arguments],
            env={**os.environ, "SIMPLE_TOKEN": token},
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0, result.stderr
    before = list(local_case.repository.connection.iterdump())
    result = subprocess.run(
        [
            *command,
            "validate",
            "team",
            local_case.update.id,
            "--manifest",
            str(local_case.manifest),
            "--evidence-directory",
            str(local_case.evidence),
            "--request-id",
            "unsupported",
        ],
        env={**os.environ, "SIMPLE_TOKEN": token},
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 1
    assert "Local replay requires Unix file locking (fcntl)" in result.stderr
    assert "Traceback" not in result.stderr
    assert not local_case.evidence.exists()
    assert list(local_case.repository.connection.iterdump()) == before


@pytest.mark.parametrize("local_case", ["faulty.py", "model.py"], indirect=True)
def test_actual_fixture_retains_success_and_failure_evidence(local_case: LocalCase) -> None:
    run = _run(local_case)
    evidence = Path(run.evidence["reference"])
    report = json.loads(evidence.read_text())
    result = report["result"]
    checks = json.loads(result["stdout"])
    assert run.outcome == ("passed" if checks["passed"] else "failed")
    assert result["returncode"] == int(not checks["passed"])
    assert run.evidence["sha256"] == sha256(evidence.read_bytes()).hexdigest()
    assert evidence.stat().st_mode & 0o222 == 0
    assert report["selection"]["caller"] == {
        "user": local_case.validator.user,
        "client": local_case.validator.client,
    }
    assert local_case.validator.credential not in evidence.read_text()
    assert local_case.repository.records(ValidationRun, "team") == [run]


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ("contributor", AccessDenied),
        ("digest", Conflict),
        ("reference", Conflict),
        ("escape", InvalidRequest),
        ("symlink", InvalidRequest),
    ],
)
def test_untrusted_selection_never_executes(
    local_case: LocalCase, mocker: MockerFixture, change: str, error: type[ValueError]
) -> None:
    execute = mocker.patch("simple_framework.local_validation.subprocess.run")
    selected = json.loads(local_case.manifest.read_text())
    caller = local_case.validator
    if change == "contributor":
        caller = local_case.author
    elif change == "digest":
        (local_case.manifest.parent / "model.py").write_text("raise RuntimeError('changed')\n")
    elif change == "reference":
        selected["code"] = "check.py"
    elif change == "escape":
        selected["code"] = "../outside.py"
    else:
        outside = local_case.manifest.parent.parent / "outside.py"
        outside.write_text("raise RuntimeError('outside')\n")
        (local_case.manifest.parent / "model.py").unlink()
        (local_case.manifest.parent / "model.py").symlink_to(outside)
    local_case.manifest.write_text(json.dumps(selected))
    with pytest.raises(error):
        run_case(
            local_case.harness,
            caller,
            "team",
            local_case.update.id,
            local_case.manifest,
            local_case.evidence,
            "run",
        )
    execute.assert_not_called()
    assert not local_case.repository.records(ValidationRun, "team")


def test_execution_uses_only_isolated_snapshots_and_fixed_arguments(
    local_case: LocalCase, mocker: MockerFixture
) -> None:
    execute = mocker.spy(subprocess, "run")
    assert _run(local_case).outcome == "passed"
    command = execute.call_args.args[0]
    assert command[:2] == [sys.executable, "-I"]
    assert [Path(path).name for path in command[2:]] == ["test.py", "code.py", "inputs.json"]
    assert len({Path(path).parent for path in command[2:]}) == 1
    snapshot = Path(command[2]).parent
    assert snapshot != local_case.manifest.parent
    assert not snapshot.exists()
    assert execute.call_args.kwargs == {
        "cwd": snapshot,
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "check": False,
        "timeout": 30,
    }


@pytest.mark.parametrize(
    "result_kind", ["timeout", "invalid-json", "invalid-utf8", "empty-checks", "false-success"]
)
def test_failed_or_invalid_execution_cannot_pass(
    local_case: LocalCase, mocker: MockerFixture, result_kind: str
) -> None:
    execute = mocker.patch("simple_framework.local_validation.subprocess.run")
    if result_kind == "timeout":
        execute.side_effect = subprocess.TimeoutExpired("test.py", 30, b"partial", b"unfinished")
    else:
        output = {
            "invalid-json": "not JSON",
            "invalid-utf8": "\ufffd",
            "empty-checks": '{"passed": true, "checks": []}',
            "false-success": '{"passed": false, "checks": [{"passed": false}]}',
        }[result_kind]
        execute.return_value = subprocess.CompletedProcess([], 0, output, "")
    run = _run(local_case)
    report = json.loads(Path(run.evidence["reference"]).read_text())
    assert run.outcome != "passed"
    if result_kind == "timeout":
        assert report["result"]["stdout"] == "partial"
        assert report["result"]["stderr"] == "unfinished"
        assert report["result"]["returncode"] is None
    elif result_kind == "invalid-utf8":
        assert report["result"]["stdout"] == "\ufffd"


def test_busy_request_does_not_execute_and_publishes_complete_evidence_on_retry(
    local_case: LocalCase, mocker: MockerFixture
) -> None:
    local_case.evidence.mkdir()
    name = sha256(b"run").hexdigest()
    evidence_path = local_case.evidence / f"{name}.json"
    execute = mocker.spy(subprocess, "run")
    with (local_case.evidence / f"{name}.lock").open("w") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(Conflict, match="running; retry same request ID"):
            _run(local_case)
        execute.assert_not_called()
        assert not evidence_path.exists()
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    original = _run(local_case)
    report = json.loads(evidence_path.read_text())
    assert report["result"]["outcome"] == "passed"
    assert _run(local_case) == original
    execute.assert_called_once()


def test_exact_retry_reuses_evidence_and_rechecks_permission(
    local_case: LocalCase, mocker: MockerFixture
) -> None:
    original = _run(local_case)
    evidence = Path(original.evidence["reference"]).read_bytes()
    execute = mocker.patch("simple_framework.local_validation.subprocess.run")
    assert _run(local_case) == original
    assert Path(original.evidence["reference"]).read_bytes() == evidence
    local_case.admin.set_membership("team", local_case.validator.user, False)
    with pytest.raises(AccessDenied):
        _run(local_case)
    execute.assert_not_called()


@pytest.mark.parametrize("change", ["subject", "code-bytes"])
def test_exact_retry_returns_the_committed_run_after_inputs_change(
    local_case: LocalCase, mocker: MockerFixture, change: str
) -> None:
    original = _run(local_case)
    evidence = Path(original.evidence["reference"]).read_bytes()
    if change == "subject":
        update = local_case.update
        local_case.harness.post_update(
            local_case.author,
            update.session_id,
            "finding",
            "Changed correction",
            "amend",
            [],
            [
                {"update_id": key, "revision": revision}
                for key, revision in update.derived_from.items()
            ],
            update_id=update.id,
            expected_revision=update.revision,
            correction=update.correction,
            goals=update.goals,
        )
    else:
        (local_case.manifest.parent / "model.py").write_text("raise RuntimeError('changed')\n")
    execute = mocker.patch("simple_framework.local_validation.subprocess.run")
    assert _run(local_case) == original
    assert Path(original.evidence["reference"]).read_bytes() == evidence
    if change == "code-bytes":
        with pytest.raises(Conflict, match="digest"):
            _run(local_case, "new-run")
    execute.assert_not_called()


@pytest.mark.parametrize("change", ["manifest-path", "manifest-bytes", "caller", "workspace"])
def test_a_request_cannot_change_its_selection(
    local_case: LocalCase, mocker: MockerFixture, change: str
) -> None:
    _run(local_case)
    execute = mocker.patch("simple_framework.local_validation.subprocess.run")
    manifest = local_case.manifest
    caller = local_case.validator
    workspace = "team"
    if change == "manifest-path":
        manifest = manifest.with_name("another.json")
        manifest.write_bytes(local_case.manifest.read_bytes())
    elif change == "manifest-bytes":
        manifest.write_text(manifest.read_text() + "\n")
    elif change == "caller":
        token = local_case.admin.register_client("second", "second", "local", "validator")
        local_case.admin.set_membership("team", "second", True)
        caller = authenticate(local_case.repository, token)
    else:
        workspace = "other"
        local_case.admin.create_workspace(workspace, "Other")
        local_case.admin.set_membership(workspace, caller.user, True)
        with local_case.repository.transaction():
            for update in local_case.repository.records(Update, "team"):
                local_case.repository.save(replace(update, workspace_id=workspace))
    with pytest.raises(Conflict, match="Request"):
        run_case(
            local_case.harness,
            caller,
            workspace,
            local_case.update.id,
            manifest,
            local_case.evidence,
            "run",
        )
    execute.assert_not_called()
