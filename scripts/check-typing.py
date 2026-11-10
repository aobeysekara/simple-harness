"""Verify an external consumer sees types from the installed core-only wheel."""

import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

CONSUMER = """from typing import assert_type
from simple_framework import Administration, Harness, Repository, authenticate
from simple_framework.domain import Caller, ContextPacket, ContextStatus, Session

def use_library(repository: Repository, token: str) -> None:
    admin = Administration(repository)
    assert_type(admin.create_workspace("study", "Example").id, str)
    caller = authenticate(repository, token)
    assert_type(caller, Caller)
    harness = Harness(repository)
    session = harness.open_session(caller, "study", "task", request_id="open")
    assert_type(session, Session)
    packet = harness.prepare_context(caller, "study", "task", "review", {})
    assert_type(packet, ContextPacket)
    assert_type(packet.status, ContextStatus)
"""


def check(python: Path) -> None:
    """Check inferred results and a rejected argument, away from the source checkout."""
    environment = {
        key: value for key, value in os.environ.items() if key not in {"MYPYPATH", "PYTHONPATH"}
    }
    with TemporaryDirectory() as directory:
        candidate = Path(directory) / "consumer.py"
        config = Path(directory) / "mypy.ini"
        config.write_text("[mypy]\nstrict = True\n")
        command = [
            sys.executable,
            "-m",
            "mypy",
            "--python-executable",
            str(python.absolute()),
            "--config-file",
            str(config),
            "--no-incremental",
            str(candidate),
        ]
        candidate.write_text(CONSUMER)
        result = subprocess.run(
            command, cwd=directory, env=environment, capture_output=True, text=True, check=False
        )
        if result.returncode != 0:
            raise RuntimeError(
                "Installed wheel type check failed:\n" + result.stdout + result.stderr
            )
        candidate.write_text(
            CONSUMER + '    harness.open_session(caller, "study", "task", request_id=42)\n'
        )
        result = subprocess.run(
            command, cwd=directory, env=environment, capture_output=True, text=True, check=False
        )
        if result.returncode != 1 or result.stdout.count("[arg-type]") != 1:
            raise RuntimeError(
                "Invalid request ID was not rejected:\n" + result.stdout + result.stderr
            )
    print("PASS installed wheel: inferred public types and rejected invalid argument")


if __name__ == "__main__":
    check(Path(sys.argv[1]))
