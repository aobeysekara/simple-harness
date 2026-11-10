import json
import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from types import ModuleType
from typing import Any, cast

from simple_framework.domain import (
    Artifact,
    Caller,
    Conflict,
    InvalidRequest,
    Update,
    ValidationRun,
)
from simple_framework.harness import Harness
from simple_framework.reuse import semantic_digest
from simple_framework.validation import require_text

fcntl: ModuleType | None
try:
    import fcntl
except ModuleNotFoundError:
    fcntl = None


def _manifest(path: Path) -> tuple[dict[str, str], bytes]:
    raw = path.read_bytes()
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise InvalidRequest("Local case manifest must contain valid JSON") from e
    if (
        not isinstance(value, dict)
        or set(value) != {"id", "code", "inputs", "test"}
        or any(not isinstance(item, str) or not item for item in value.values())
    ):
        raise InvalidRequest("Local case manifest requires id, code, inputs and test strings")
    return cast(dict[str, str], value), raw


def _selected_files(
    root: Path, selected: dict[str, str], update: Update
) -> tuple[dict[str, Artifact], dict[str, bytes]]:
    assert update.correction is not None
    case = next((case for case in update.correction["cases"] if case["id"] == selected["id"]), None)
    if case is None:
        raise Conflict("Selected case does not match the correction")
    artifacts = {"code": case["code"], "inputs": case["inputs"], "test": case["test"]}
    contents = {}
    for name, artifact in artifacts.items():
        relative = Path(selected[name])
        path = (root / relative).resolve()
        if relative.is_absolute() or not path.is_relative_to(root):
            raise InvalidRequest("Selected files must stay inside the manifest directory")
        if artifact["reference"] != selected[name]:
            raise Conflict(f"Selected {name} reference does not match the declared artifact")
        raw = path.read_bytes()
        if sha256(raw).hexdigest() != artifact["sha256"]:
            raise Conflict(f"Selected {name} digest does not match the declared artifact")
        contents[name] = raw
    return artifacts, contents


def _outcome(returncode: int, stdout: str) -> str:
    if returncode != 0:
        return "failed"
    try:
        report = json.loads(stdout)
    except json.JSONDecodeError:
        return "error"
    if (
        not isinstance(report, dict)
        or set(report) != {"passed", "checks"}
        or type(report["passed"]) is not bool
        or not isinstance(report["checks"], list)
        or not report["checks"]
    ):
        return "error"
    for check in report["checks"]:
        if (
            not isinstance(check, dict)
            or set(check) != {"id", "expected", "actual", "passed"}
            or not isinstance(check["id"], str)
            or type(check["passed"]) is not bool
            or check["passed"] != (check["expected"] == check["actual"])
        ):
            return "error"
    if report["passed"] != all(check["passed"] for check in report["checks"]):
        return "error"
    return "passed" if report["passed"] else "failed"


def _output(value: str | bytes | None) -> str:
    return value.decode(errors="replace") if isinstance(value, bytes) else value or ""


@contextmanager
def _request_lock(evidence_path: Path) -> Iterator[None]:
    assert fcntl is not None
    descriptor = os.open(evidence_path.with_suffix(".lock"), os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(descriptor, "w") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            raise Conflict("Validation is running; retry same request ID") from e
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _execute(directory: Path) -> dict[str, Any]:
    command = [
        sys.executable,
        "-I",
        str(directory / "test.py"),
        str(directory / "code.py"),
        str(directory / "inputs.json"),
    ]
    try:
        result = subprocess.run(
            command,
            cwd=directory,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=30,
        )
    except subprocess.TimeoutExpired as e:
        return {
            "returncode": None,
            "stdout": _output(e.stdout),
            "stderr": _output(e.stderr),
            "outcome": "error",
            "error": "Validation exceeded 30 seconds",
        }
    except OSError as e:
        return {"returncode": None, "stdout": "", "stderr": str(e), "outcome": "error"}
    return {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "outcome": _outcome(result.returncode, result.stdout),
    }


def run_case(
    harness: Harness,
    caller: Caller,
    workspace_id: str,
    update_id: str,
    manifest: Path,
    evidence_directory: Path,
    request_id: str,
) -> ValidationRun:
    """Run an operator-selected local case and retain its immutable execution evidence."""
    if fcntl is None:
        raise InvalidRequest("Local replay requires Unix file locking (fcntl); unavailable here")
    update = harness.validation_subject(caller, workspace_id, update_id)
    require_text(request_id, "Request ID")
    manifest = manifest.resolve()
    selected, manifest_bytes = _manifest(manifest)
    selection = {
        "caller": {"user": caller.user, "client": caller.client},
        "workspace_id": workspace_id,
        "update_id": update_id,
        "manifest": str(manifest),
        "manifest_sha256": sha256(manifest_bytes).hexdigest(),
        "case_id": selected["id"],
    }
    evidence_directory = evidence_directory.resolve()
    evidence_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    evidence_path = evidence_directory / f"{sha256(request_id.encode()).hexdigest()}.json"
    with _request_lock(evidence_path):
        if evidence_path.exists():
            raw_evidence = evidence_path.read_bytes()
            report = json.loads(raw_evidence)
            if report["selection"] != selection:
                raise Conflict("Request ID was already used with a different local selection")
        else:
            artifacts, contents = _selected_files(manifest.parent, selected, update)
            digest = semantic_digest(update)
            with TemporaryDirectory(prefix="simple-validation-") as directory:
                snapshot = Path(directory)
                for name, filename in (
                    ("code", "code.py"),
                    ("inputs", "inputs.json"),
                    ("test", "test.py"),
                ):
                    (snapshot / filename).write_bytes(contents[name])
                current = harness.validation_subject(caller, workspace_id, update_id)
                if semantic_digest(current) != digest:
                    raise Conflict("Validation subject changed before execution")
                result = _execute(snapshot)
            report = {
                "selection": selection,
                "subject_digest": digest,
                "artifacts": artifacts,
                "result": result,
            }
            raw_evidence = json.dumps(report, sort_keys=True).encode() + b"\n"
            with NamedTemporaryFile(dir=evidence_directory, prefix=".evidence-") as pending:
                pending.write(raw_evidence)
                pending.flush()
                os.fchmod(pending.fileno(), 0o400)
                os.link(pending.name, evidence_path)
        evidence: Artifact = {
            "reference": str(evidence_path),
            "sha256": sha256(raw_evidence).hexdigest(),
        }
        return harness.record_validation(
            caller,
            workspace_id,
            update_id,
            report["subject_digest"],
            selected["id"],
            report["artifacts"],
            report["result"]["outcome"],
            evidence,
            request_id,
        )
