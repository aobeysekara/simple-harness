import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

CHILD_CODE = """
import json
import runpy
import sys

payload = json.loads(sys.stdin.read())
try:
    transform = runpy.run_path(sys.argv[1])["transform"]
    actual = {"output": transform(payload["rows"], payload["contract"])}
except ValueError as e:
    actual = {"error": str(e)}
print(json.dumps(actual))
"""


def _actual(
    code_file: Path, rows: list[dict[str, Any]], contract: dict[str, Any]
) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-c", CHILD_CODE, str(code_file.resolve())],
            input=json.dumps({"rows": rows, "contract": contract}),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=2,
        )
    except subprocess.TimeoutExpired:
        return {"error": "Candidate exceeded two seconds"}
    except OSError as e:
        return {"error": str(e)}
    if result.returncode != 0:
        return {
            "error": f"Candidate exited with status {result.returncode}",
            "stderr": result.stderr,
        }
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"error": "Candidate output is not valid JSON", "stdout": result.stdout}
    if isinstance(value, dict) and (
        (set(value) == {"output"} and isinstance(value["output"], list))
        or (set(value) == {"error"} and isinstance(value["error"], str))
    ):
        return cast(dict[str, Any], value)
    return {"error": "Candidate output must contain a transform output or error"}


def check(code_file: Path, input_file: Path) -> dict[str, Any]:
    """Check a transform against the frozen cases in a JSON file."""
    specification = json.loads(input_file.read_text())
    cases = specification["cases"]
    checks = []
    for case in cases:
        actual = _actual(code_file, case["rows"], case["contract"])
        checks.append(
            {
                "id": case["id"],
                "expected": case["expected"],
                "actual": actual,
                "passed": actual == case["expected"]
                and (
                    not specification.get("strict_types", False)
                    or json.dumps(actual, sort_keys=True)
                    == json.dumps(case["expected"], sort_keys=True)
                ),
            }
        )
    return {"passed": all(result["passed"] for result in checks), "checks": checks}


def main() -> int:
    """Run the acceptance checks for the supplied code and input files."""
    parser = argparse.ArgumentParser()
    parser.add_argument("code_file", type=Path)
    parser.add_argument("input_file", type=Path)
    arguments = parser.parse_args()
    report = check(arguments.code_file, arguments.input_file)
    print(json.dumps(report, sort_keys=True))
    return int(not report["passed"])


if __name__ == "__main__":
    raise SystemExit(main())
