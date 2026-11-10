import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "check-dist.py"


@pytest.mark.parametrize(
    "name", ["package/.env", "package/data.sqlite3-wal", "package/data.sqlite-journal"]
)
def test_distribution_guards_survive_python_optimisation(name: str) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-O",
            "-c",
            "import runpy,sys; runpy.run_path(sys.argv[1])['check_members']([sys.argv[2]])",
            str(SCRIPT),
            name,
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert name in result.stderr


@pytest.mark.parametrize("bad_field", ["Version: 9.9.9", "License-Expression: Apache-2.0"])
def test_distribution_metadata_mismatch_is_rejected_under_optimisation(bad_field: str) -> None:
    metadata = (
        "Name: simple-framework\nVersion: 0.1.0a1\nLicense-Expression: MIT\n"
        "License-File: LICENSE\nClassifier: Development Status :: 3 - Alpha\n"
    )
    field = "Version: 0.1.0a1" if bad_field.startswith("Version:") else "License-Expression: MIT"
    result = subprocess.run(
        [
            sys.executable,
            "-O",
            "-c",
            "import runpy,sys; runpy.run_path(sys.argv[1])['check_metadata']"
            "(sys.argv[2].encode(), 'simple-framework', '0.1.0a1')",
            str(SCRIPT),
            metadata.replace(field, bad_field),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "Invalid distribution metadata" in result.stderr
