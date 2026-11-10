import os
from dataclasses import dataclass, field
from pathlib import Path

from simple_framework.admin import Administration
from simple_framework.domain import InvalidRequest
from simple_framework.sqlite import SQLiteRepository


@dataclass(frozen=True)
class Demo:
    """Locate a reading-group demo and its separate client credentials."""

    directory: Path
    alice_token: str = field(repr=False)
    bob_token: str = field(repr=False)
    reviewer_token: str = field(repr=False)
    workspace_id: str = "reading"
    task_id: str = "first-week"

    @property
    def database(self) -> Path:
        """Return the demo's database path."""
        return self.directory / "workspace.sqlite3"


def setup_demo(directory: Path) -> Demo:
    """Create a new synthetic workspace and save owner-only client tokens."""
    directory = directory.resolve()
    try:
        directory.mkdir(parents=True, mode=0o700)
    except FileExistsError as e:
        raise InvalidRequest("Demo directory already exists; choose a new directory") from e
    with SQLiteRepository(directory / "workspace.sqlite3") as repository:
        admin = Administration(repository)
        admin.create_workspace("reading", "Reading group")
        admin.create_task("reading", "first-week", "Choose the first week's reading")
        alice = admin.register_client("alice", "alice-agent", "local", "contributor")
        bob = admin.register_client("bob", "bob-agent", "local", "contributor")
        reviewer = admin.register_client("bob", "bob-review", "local", "reviewer")
        admin.set_membership("reading", "alice", True)
        admin.set_membership("reading", "bob", True)
    for name, token in [("alice", alice), ("bob", bob), ("reviewer", reviewer)]:
        descriptor = os.open(
            directory / f"{name}.token", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
        with os.fdopen(descriptor, "w") as file:
            file.write(token + "\n")
    return Demo(directory, alice, bob, reviewer)


def load_demo(directory: Path) -> Demo:
    """Read credentials from a previously provisioned demo directory."""
    directory = directory.resolve()
    if not (directory / "workspace.sqlite3").is_file():
        raise InvalidRequest("Demo not found; run simple demo setup --directory PATH first")
    try:
        tokens = [
            (directory / f"{name}.token").read_text().strip()
            for name in (
                "alice",
                "bob",
                "reviewer",
            )
        ]
    except FileNotFoundError as e:
        raise InvalidRequest(
            "Demo tokens are missing; run simple demo setup in a new directory"
        ) from e
    if not all(tokens):
        raise InvalidRequest("Demo tokens are empty; run simple demo setup in a new directory")
    return Demo(directory, *tokens)
