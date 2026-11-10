import json
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("code_name", "failed_cases"),
    [("faulty.py", ["a-original", "a-refreshed"]), ("model.py", [])],
    ids=["fault-preserved", "correction"],
)
def test_acceptance_runner_reconciles_frozen_cases(code_name: str, failed_cases: list[str]) -> None:
    root = Path(__file__).resolve().parents[1] / "examples" / "reuse_case"
    result = subprocess.run(
        [sys.executable, str(root / "check.py"), str(root / code_name), str(root / "cases.json")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == int(bool(failed_cases)), result.stderr
    report = json.loads(result.stdout)
    assert report["passed"] == (not failed_cases)
    assert [check["id"] for check in report["checks"] if not check["passed"]] == failed_cases
    frozen = json.loads((root / "cases.json").read_text())
    assert [check["expected"] for check in report["checks"]] == [
        case["expected"] for case in frozen["cases"]
    ]
    if failed_cases:
        assert report == json.loads((root / "faulty_replay.json").read_text())


def test_fresh_consumer_extract_has_the_same_business_lines_and_new_raw_ids() -> None:
    root = Path(__file__).resolve().parents[1] / "examples" / "reuse_case"
    cases = json.loads((root / "cases.json").read_text())["cases"]
    original, refreshed = cases[:2]
    extract = json.loads((root / "consumer" / "extract.json").read_text())
    assert extract == {"project": "A", "workflow": "triage", "rows": refreshed["rows"]}
    original_ids = {row["raw_row_id"] for row in original["rows"]}
    assert original_ids.isdisjoint({row["raw_row_id"] for row in extract["rows"]})
    original_lines = [(row["issue_id"], row["entry_id"]) for row in original["rows"]]
    fresh_lines = [(row["issue_id"], row["entry_id"]) for row in extract["rows"]]
    assert sorted(original_lines) == sorted(fresh_lines)
    assert original_lines != fresh_lines


def test_acceptance_runner_rejects_a_third_argument() -> None:
    root = Path(__file__).resolve().parents[1] / "examples" / "reuse_case"
    result = subprocess.run(
        [
            sys.executable,
            str(root / "check.py"),
            str(root / "model.py"),
            str(root / "cases.json"),
            "unexpected-command",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "unrecognized arguments: unexpected-command" in result.stderr


@pytest.mark.parametrize(
    "candidate",
    [
        'print(\'{"passed":true,"checks":[{"id":"forged","expected":1,"actual":1,'
        '"passed":true}]}\'); raise SystemExit(0)\n',
        'import os\nos.write(1, b"\\xff")\nraise SystemExit(0)\n',
    ],
    ids=["forged-check-report", "invalid-utf8"],
)
def test_checker_rejects_candidate_output_that_is_not_a_transform_result(
    tmp_path: Path, candidate: str
) -> None:
    root = Path(__file__).resolve().parents[1] / "examples" / "reuse_case"
    code = tmp_path / "candidate.py"
    code.write_text(candidate)
    result = subprocess.run(
        [sys.executable, str(root / "check.py"), str(code), str(root / "cases.json")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 1
    report = json.loads(result.stdout)
    cases = json.loads((root / "cases.json").read_text())["cases"]
    assert report["passed"] is False
    assert [check["id"] for check in report["checks"]] == [case["id"] for case in cases]
    assert [check["expected"] for check in report["checks"]] == [case["expected"] for case in cases]
    assert all(not check["passed"] and "error" in check["actual"] for check in report["checks"])
