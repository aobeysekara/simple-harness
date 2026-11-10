import json
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

from simple_framework.admin import Administration
from simple_framework.auth import authenticate
from simple_framework.domain import WorkspaceView
from simple_framework.harness import Harness
from simple_framework.sqlite import SQLiteRepository


def reading_group(database: Path) -> WorkspaceView:
    """Run a two-user discussion and recover its handover from a fresh connection."""
    with SQLiteRepository(database) as repository:
        admin = Administration(repository)
        admin.create_workspace("reading", "Reading group")
        admin.create_task("reading", "first-week", "Choose the first week's reading")
        alice_token = admin.register_client("alice", "alice-agent", "local", "contributor")
        bob_token = admin.register_client("bob", "bob-agent", "local", "contributor")
        reviewer_token = admin.register_client("bob", "bob-review", "local", "reviewer")
        admin.set_membership("reading", "alice", True)
        admin.set_membership("reading", "bob", True)
        harness = Harness(repository)
        alice = authenticate(repository, alice_token)
        bob = authenticate(repository, bob_token)
        reviewer = authenticate(repository, reviewer_token)
        alice_session = harness.join_workspace(alice, "reading", "first-week", "alice-join")
        bob_session = harness.join_workspace(bob, "reading", "first-week", "bob-join")
        proposal = harness.post_update(
            alice,
            alice_session.session.id,
            "proposal",
            "Start with a short introductory article, then discuss one research paper.",
            "proposal",
            [],
        )
        harness.post_update(
            bob,
            bob_session.session.id,
            "question",
            "Who will choose the research paper?",
            "question",
            [],
        )
        accepted = harness.review_update(reviewer, "reading", proposal.id, 1, "accepted", "review")
        harness.end_session(
            alice,
            alice_session.session.id,
            "handover",
            "The order is agreed. Bob still needs to choose the research paper.",
            [],
            derived_from=[{"update_id": accepted.id, "revision": accepted.revision}],
        )
    with SQLiteRepository(database) as repository:
        bob = authenticate(repository, bob_token)
        return Harness(repository).join_workspace(bob, "reading", "first-week", "fresh").view


def main() -> None:
    """Print the recovered workspace using temporary, synthetic data."""
    with TemporaryDirectory(prefix="simple-reading-") as directory:
        view = reading_group(Path(directory) / "workspace.sqlite3")
        print(json.dumps(asdict(view), indent=2))


if __name__ == "__main__":
    main()
