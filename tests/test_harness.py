import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier

import pytest
from pytest_mock import MockerFixture

from simple_framework.admin import Administration
from simple_framework.auth import authenticate
from simple_framework.domain import (
    AccessDenied,
    Audit,
    Conflict,
    Cursor,
    Dependency,
    InvalidRequest,
    Joined,
    Policy,
    Session,
    Update,
)
from simple_framework.harness import Harness
from simple_framework.sqlite import SQLiteRepository

type Pilot = tuple[Administration, Harness, str, str, str]


def fixed_clock() -> str:
    return "2026-11-03T12:00:00+00:00"


@pytest.fixture
def repository(tmp_path: Path) -> Iterator[SQLiteRepository]:
    with SQLiteRepository(tmp_path / "workspace.sqlite3") as repository:
        yield repository


@pytest.fixture
def pilot(repository: SQLiteRepository) -> Pilot:
    admin = Administration(repository, clock=fixed_clock)
    admin.create_workspace("team", "Reading group")
    admin.create_task("team", "plan", "Choose a reading list")
    alice_token = admin.register_client("alice", "alice-agent", "local", "contributor")
    bob_token = admin.register_client("bob", "bob-agent", "local", "contributor")
    review_token = admin.register_client("bob", "bob-review", "local", "reviewer")
    admin.set_membership("team", "alice", True)
    admin.set_membership("team", "bob", True)
    return admin, Harness(repository, clock=fixed_clock), alice_token, bob_token, review_token


def join(
    repository: SQLiteRepository,
    harness: Harness,
    token: str,
    request_id: str = "join",
) -> Joined:
    return harness.join_workspace(authenticate(repository, token), "team", "plan", request_id)


def post(
    repository: SQLiteRepository,
    harness: Harness,
    token: str,
    session: Session,
    text: str = "Read the short paper first",
    *,
    request_id: str = "post",
    sources: list[str] | tuple[()] | None = (),
    derived_from: list[Dependency] | None = None,
    update_id: str | None = None,
    expected_revision: int | None = None,
) -> Update:
    return harness.post_update(
        authenticate(repository, token),
        session.id,
        "proposal",
        text,
        request_id,
        list(sources) if sources is not None else None,
        derived_from,
        update_id,
        expected_revision,
    )


def test_open_session_skips_history_and_keeps_ownership_retries_and_access(
    repository: SQLiteRepository, pilot: Pilot, mocker: MockerFixture
) -> None:
    admin, harness, alice, bob, _ = pilot
    original = join(repository, harness, alice)
    for index in range(96):
        post(repository, harness, alice, original.session, request_id=f"noise-{index}")
    caller = authenticate(repository, alice)
    history = mocker.patch.object(repository, "changes", side_effect=AssertionError("History read"))
    records = mocker.patch.object(repository, "records", side_effect=AssertionError("Records read"))
    opened = harness.open_session(caller, "team", "plan", "open")
    assert opened.user == "alice" and opened.task_id == "plan"
    assert harness.open_session(caller, "team", "plan", "open") == opened
    assert harness.open_session(caller, "team", "plan", "resume", opened.id) == opened
    history.assert_not_called()
    records.assert_not_called()
    admin.create_task("team", "other", "Other")
    before = list(repository.connection.iterdump())
    with pytest.raises(Conflict, match="different content"):
        harness.open_session(caller, "team", "other", "open")
    with pytest.raises(AccessDenied):
        harness.open_session(authenticate(repository, bob), "team", "plan", "borrow", opened.id)
    assert list(repository.connection.iterdump()) == before
    admin.set_membership("team", "alice", False)
    before = list(repository.connection.iterdump())
    with pytest.raises(AccessDenied):
        harness.open_session(caller, "team", "plan", "open")
    assert list(repository.connection.iterdump()) == before


@pytest.mark.parametrize(
    "stage,actor,required",
    [
        (
            "a-initial",
            "alice",
            {
                "r_example_finding": 1,
                "r_example_restricted": 1,
            },
        ),
        (
            "c",
            "bob",
            {
                "r_example_finding": 3,
                "r_example_result": 2,
            },
        ),
    ],
)
def test_handover_omissions_require_exact_declarations_atomically(
    repository: SQLiteRepository, pilot: Pilot, stage: str, actor: str, required: dict[str, int]
) -> None:
    fixture_path = Path(__file__).parent / "fixtures" / "handovers" / f"{stage}.json"
    fixture = json.loads(fixture_path.read_text())
    arguments = fixture["arguments"]
    admin, harness, alice, bob, _ = pilot
    admin.register_source(
        "team", "operating-spec", "Synthetic", ["alice", "bob"], ["alice-agent", "bob-agent"]
    )
    admin.register_source("team", "notes-private", "Synthetic", ["alice"], ["alice-agent"])
    with repository.transaction():
        for dependency in fixture["dependencies"]:
            repository.save(
                Update(
                    **dependency,
                    workspace_id="team",
                    task_id="plan",
                    session_id="synthetic-session",
                    author="synthetic-author",
                    client="synthetic-client",
                    kind="finding",
                    text="Synthetic dependency",
                    derived_from={},
                    created_at=fixed_clock(),
                    changed_at=fixed_clock(),
                )
            )
    token = alice if actor == "alice" else bob
    joined = join(repository, harness, token)
    caller = authenticate(repository, token)
    before = list(repository.connection.iterdump())
    with pytest.raises(InvalidRequest, match="Declare explicitly referenced contributions"):
        harness.end_session(
            caller,
            joined.session.id,
            arguments["request_id"],
            arguments["handover"],
            arguments["sources"],
            arguments.get("derived_from"),
        )
    assert list(repository.connection.iterdump()) == before
    ended = harness.end_session(
        caller,
        joined.session.id,
        arguments["request_id"],
        arguments["handover"],
        arguments["sources"],
        [
            {"update_id": update_id, "revision": revision}
            for update_id, revision in required.items()
        ],
    )
    handover = repository.changes("team")[-1].update
    assert handover.derived_from == required
    assert ("notes-private" in handover.sources) == (actor == "alice")
    assert ended.ended_at is not None


@pytest.mark.parametrize("amend", [False, True], ids=["new", "amendment"])
def test_explicit_references_require_declarations_before_any_write(
    repository: SQLiteRepository, pilot: Pilot, amend: bool
) -> None:
    _, harness, token, _, _ = pilot
    joined = join(repository, harness, token)
    original = post(repository, harness, token, joined.session)
    dependency = replace(original, id="legacy_reference-without-prefix")
    with repository.transaction():
        repository.save(dependency)
    update_id = original.id if amend else None
    revision = original.revision if amend else None
    text = f"Use `/updates/{dependency.id}@1`."
    before = list(repository.connection.iterdump())
    with pytest.raises(InvalidRequest, match="Declare explicitly referenced contributions"):
        post(
            repository,
            harness,
            token,
            joined.session,
            text,
            request_id="result",
            update_id=update_id,
            expected_revision=revision,
        )
    assert list(repository.connection.iterdump()) == before
    result = post(
        repository,
        harness,
        token,
        joined.session,
        text,
        request_id="result",
        update_id=update_id,
        expected_revision=revision,
        derived_from=[{"update_id": dependency.id, "revision": 1}],
    )
    assert result.derived_from == {dependency.id: 1}


def test_only_complete_accessible_workspace_contribution_ids_require_declarations(
    repository: SQLiteRepository, pilot: Pilot
) -> None:
    admin, harness, alice_token, bob_token, _ = pilot
    admin.register_source("team", "private", "Synthetic", ["alice"], ["alice-agent"])
    alice = join(repository, harness, alice_token)
    bob = join(repository, harness, bob_token)
    secret = post(repository, harness, alice_token, alice.session, sources=["private"])
    public = post(repository, harness, alice_token, alice.session, request_id="public")
    foreign = replace(public, id="r_foreign", workspace_id="elsewhere")
    with repository.transaction():
        repository.save(foreign)
    text = " ".join(
        [
            bob.session.id,
            bob.view.cursor,
            "r_unknown",
            secret.id,
            foreign.id,
            f"prefix_{public.id}",
            f"{public.id}-suffix",
            f"é{public.id}",
        ]
    )
    result = post(repository, harness, bob_token, bob.session, text)
    assert result.sources == []
    assert result.derived_from == {}
    assert result.text == text


def test_amendment_can_name_itself_without_adding_a_dependency(
    repository: SQLiteRepository, pilot: Pilot
) -> None:
    _, harness, token, _, _ = pilot
    joined = join(repository, harness, token)
    original = post(repository, harness, token, joined.session)
    text = f"Correction to {original.id}"
    amended = post(
        repository,
        harness,
        token,
        joined.session,
        text,
        request_id="amend",
        update_id=original.id,
        expected_revision=1,
    )
    assert amended.derived_from == {}
    explicit = post(
        repository,
        harness,
        token,
        joined.session,
        text,
        request_id="explicit",
        update_id=original.id,
        expected_revision=2,
        derived_from=[{"update_id": original.id, "revision": 2}],
    )
    assert explicit.derived_from == {original.id: 2}


@pytest.mark.parametrize("operation", ["post", "end"])
def test_committed_retry_does_not_revalidate_mentions_but_checks_identity_and_content(
    repository: SQLiteRepository, pilot: Pilot, operation: str
) -> None:
    admin, harness, token, _, _ = pilot
    joined = join(repository, harness, token)
    original = post(repository, harness, token, joined.session)
    caller = authenticate(repository, token)
    text = "See r_future"
    result: Update | Session
    if operation == "post":
        result = harness.post_update(caller, joined.session.id, "progress", text, "result", [])
    else:
        result = harness.end_session(caller, joined.session.id, "result", text, [])
    with repository.transaction():
        repository.save(replace(original, id="r_future"))
    with SQLiteRepository(repository.path) as reopened:
        fresh = Harness(reopened)
        retry: Update | Session
        if operation == "post":
            retry = fresh.post_update(caller, joined.session.id, "progress", text, "result", [])
        else:
            retry = fresh.end_session(caller, joined.session.id, "result", text, [])
        assert retry == result
    before = list(repository.connection.iterdump())
    with pytest.raises(Conflict, match="Request ID"):
        if operation == "post":
            harness.post_update(caller, joined.session.id, "progress", text + ".", "result", [])
        else:
            harness.end_session(caller, joined.session.id, "result", text + ".", [])
    assert list(repository.connection.iterdump()) == before
    new_session = join(repository, harness, token, "new").session
    before = list(repository.connection.iterdump())
    with pytest.raises(InvalidRequest, match="Declare explicitly referenced contributions"):
        if operation == "post":
            harness.post_update(caller, new_session.id, "progress", text, "new", [])
        else:
            harness.end_session(caller, new_session.id, "new", text, [])
    assert list(repository.connection.iterdump()) == before
    admin.revoke_credential(token)
    before = list(repository.connection.iterdump())
    with pytest.raises(AccessDenied, match="^Access denied$"):
        if operation == "post":
            harness.post_update(caller, joined.session.id, "progress", text, "result", [])
        else:
            harness.end_session(caller, joined.session.id, "result", text, [])
    assert list(repository.connection.iterdump()) == before


def test_two_users_review_and_recover_a_handover_after_restart(
    repository: SQLiteRepository, pilot: Pilot
) -> None:
    admin, harness, alice_token, bob_token, review_token = pilot
    alice = join(repository, harness, alice_token)
    bob = join(repository, harness, bob_token)
    proposal = post(repository, harness, alice_token, alice.session)
    question = harness.post_update(
        authenticate(repository, bob_token),
        bob.session.id,
        "question",
        "Should we include a beginner guide?",
        "question",
        [],
    )
    accepted = harness.review_update(
        authenticate(repository, review_token),
        "team",
        proposal.id,
        1,
        "accepted",
        "review",
    )
    assert accepted.revision == 2
    assert accepted.reviewed_by == "bob"
    assert accepted.author == "alice"
    harness.end_session(
        authenticate(repository, alice_token),
        alice.session.id,
        "end",
        "Add the beginner guide next",
        [],
    )
    with SQLiteRepository(repository.path) as reopened:
        fresh = join(reopened, Harness(reopened), bob_token, "fresh")
        updates = fresh.view.updates
        assert {u.kind for u in updates} == {"proposal", "question", "handover"}
        assert next(u for u in updates if u.id == proposal.id).status == "accepted"
        assert next(u for u in updates if u.id == question.id).author == "bob"
        assert fresh.view.tasks[0].status == "open"
        assert len({alice.session.id, bob.session.id, fresh.session.id}) == 3


@pytest.mark.parametrize("change", ["membership", "client", "credential", "user"])
def test_revocation_blocks_existing_sessions_and_cursors(
    repository: SQLiteRepository, pilot: Pilot, change: str
) -> None:
    admin, harness, alice_token, _, _ = pilot
    caller = authenticate(repository, alice_token)
    joined = join(repository, harness, alice_token)
    if change == "membership":
        admin.set_membership("team", "alice", False)
    elif change == "client":
        admin.set_client_active("alice-agent", False)
    elif change == "credential":
        admin.revoke_credential(alice_token)
    else:
        admin.set_user_active("alice", False)
    with pytest.raises(AccessDenied):
        harness.get_updates(caller, "team", joined.view.cursor)
    with pytest.raises(AccessDenied):
        harness.post_update(caller, joined.session.id, "progress", "Done", "post", [])


def test_known_ids_do_not_allow_another_user_or_client_to_write(
    repository: SQLiteRepository, pilot: Pilot
) -> None:
    admin, harness, alice_token, bob_token, _ = pilot
    joined = join(repository, harness, alice_token)
    outsider_token = admin.register_client("eve", "eve-agent", "local", "contributor")
    with pytest.raises(AccessDenied):
        join(repository, harness, outsider_token)
    second_client = admin.register_client("alice", "alice-other", "local", "contributor")
    for token in [bob_token, second_client]:
        with pytest.raises(AccessDenied):
            post(repository, harness, token, joined.session)


def test_retries_and_stale_amendments_preserve_one_revision(
    repository: SQLiteRepository, pilot: Pilot
) -> None:
    _, harness, alice_token, _, _ = pilot
    joined = join(repository, harness, alice_token)
    assert join(repository, harness, alice_token).session.id == joined.session.id
    original = post(repository, harness, alice_token, joined.session)
    assert post(repository, harness, alice_token, joined.session) == original
    with pytest.raises(Conflict):
        post(repository, harness, alice_token, joined.session, "Different text")
    changed = post(
        repository,
        harness,
        alice_token,
        joined.session,
        "Start with the guide",
        request_id="amend",
        update_id=original.id,
        expected_revision=1,
    )
    assert changed.revision == 2
    with pytest.raises(Conflict):
        post(
            repository,
            harness,
            alice_token,
            joined.session,
            "Stale change",
            request_id="stale",
            update_id=original.id,
            expected_revision=1,
        )
    changes = harness.get_updates(
        authenticate(repository, alice_token),
        "team",
        joined.view.cursor,
    )
    assert [(c.update.id, c.update.revision) for c in changes.changes] == [
        (original.id, 1),
        (original.id, 2),
    ]


def test_review_needs_separate_credentials_and_exact_revision(
    repository: SQLiteRepository, pilot: Pilot
) -> None:
    admin, harness, alice_token, bob_token, review_token = pilot
    alice_review = admin.register_client("alice", "alice-review", "local", "reviewer")
    joined = join(repository, harness, alice_token)
    proposal = post(repository, harness, alice_token, joined.session)
    for token in [alice_token, bob_token, alice_review]:
        with pytest.raises(AccessDenied):
            harness.review_update(
                authenticate(repository, token),
                "team",
                proposal.id,
                1,
                "accepted",
                "review",
            )
    reviewer = authenticate(repository, review_token)
    accepted = harness.review_update(reviewer, "team", proposal.id, 1, "accepted", "review")
    assert harness.review_update(reviewer, "team", proposal.id, 1, "accepted", "review") == accepted
    with pytest.raises(Conflict):
        harness.review_update(reviewer, "team", proposal.id, 1, "rejected", "other")
    amended = post(
        repository,
        harness,
        alice_token,
        joined.session,
        "New proposal",
        request_id="amend",
        update_id=proposal.id,
        expected_revision=2,
    )
    assert amended.status == "proposed"
    assert amended.reviewed_by is None


def test_sources_remain_restricted_through_derivation_and_amendment(
    repository: SQLiteRepository, pilot: Pilot
) -> None:
    admin, harness, alice_token, bob_token, _ = pilot
    admin.register_source("team", "paper", "https://example.org/paper", ["alice"], ["alice-agent"])
    alice = join(repository, harness, alice_token)
    bob = join(repository, harness, bob_token)
    original = post(repository, harness, alice_token, alice.session, sources=["paper"])
    derived = post(
        repository,
        harness,
        alice_token,
        alice.session,
        "Summary",
        request_id="derived",
        derived_from=[{"update_id": original.id, "revision": original.revision}],
    )
    post(
        repository,
        harness,
        alice_token,
        alice.session,
        "Reworded",
        request_id="amend",
        update_id=derived.id,
        expected_revision=1,
    )
    visible = harness.read_workspace(authenticate(repository, bob_token), "team")
    assert visible.updates == []
    assert visible.sources == []
    assert (
        harness.get_updates(
            authenticate(repository, bob_token),
            "team",
            bob.view.cursor,
        ).changes
        == []
    )
    admin.set_source_access("team", "paper", ["alice", "bob"], ["alice-agent", "bob-agent"])
    visible = harness.read_workspace(authenticate(repository, bob_token), "team", limit=1)
    assert len(visible.updates) <= 1
    admin.set_source_access("team", "paper", ["alice"], ["alice-agent"])
    with pytest.raises(Conflict):
        harness.get_updates(authenticate(repository, bob_token), "team", visible.cursor)
    assert harness.read_workspace(authenticate(repository, bob_token), "team").updates == []


@pytest.mark.parametrize("sources", [None, ["unknown"]])
def test_unknown_sources_cannot_be_shared(
    repository: SQLiteRepository, pilot: Pilot, sources: list[str] | None
) -> None:
    _, harness, alice_token, _, _ = pilot
    joined = join(repository, harness, alice_token)
    with pytest.raises((AccessDenied, InvalidRequest)):
        post(repository, harness, alice_token, joined.session, sources=sources)
    assert harness.read_workspace(authenticate(repository, alice_token), "team").updates == []


def test_snapshot_pages_and_change_pages_do_not_lose_interleaved_writes(
    repository: SQLiteRepository, pilot: Pilot
) -> None:
    _, harness, alice_token, bob_token, _ = pilot
    alice = join(repository, harness, alice_token)
    caller = authenticate(repository, bob_token)
    first = post(repository, harness, alice_token, alice.session)
    view = harness.read_workspace(caller, "team", limit=1)
    second = post(repository, harness, alice_token, alice.session, "Next", request_id="next")
    snapshot_updates = list(view.updates)
    page = view
    while page.next_page:
        page = harness.read_workspace(caller, "team", cursor=page.next_page, limit=1)
        snapshot_updates.extend(page.updates)
    assert [u.id for u in snapshot_updates] == [first.id]
    changes = harness.get_updates(caller, "team", view.cursor, limit=1)
    assert [c.update.id for c in changes.changes] == [second.id]
    assert harness.get_updates(caller, "team", changes.cursor).changes == []
    with pytest.raises(AccessDenied):
        harness.get_updates(authenticate(repository, alice_token), "team", view.cursor)
    narrowed = harness.get_updates(caller, "team", view.cursor, task_id="plan")
    assert [c.update.id for c in narrowed.changes] == [second.id]


def test_end_session_is_atomic_and_retryable(
    repository: SQLiteRepository, pilot: Pilot, mocker: MockerFixture
) -> None:
    _, harness, alice_token, _, _ = pilot
    joined = join(repository, harness, alice_token)
    caller = authenticate(repository, alice_token)
    mocker.patch.object(repository, "append_change", side_effect=OSError("disk full"))
    with pytest.raises(OSError):
        harness.end_session(caller, joined.session.id, "end", "Next steps", [])
    mocker.stopall()
    assert harness.read_workspace(caller, "team").updates == []
    ended = harness.end_session(caller, joined.session.id, "end", "Next steps", [])
    assert ended.ended_at is not None
    assert harness.end_session(caller, joined.session.id, "end", "Next steps", []) == ended
    with pytest.raises(Conflict):
        post(repository, harness, alice_token, joined.session)
    updates = harness.read_workspace(caller, "team").updates
    assert len(updates) == 1
    assert updates[0].kind == "handover"


def test_derived_records_pin_revisions_without_disclosing_source_grants(
    repository: SQLiteRepository, pilot: Pilot
) -> None:
    admin, harness, alice_token, _, _ = pilot
    admin.register_source("team", "paper", "https://example.org/paper", ["alice"], ["alice-agent"])
    alice = join(repository, harness, alice_token)
    original = post(repository, harness, alice_token, alice.session, sources=["paper"])
    derived = post(
        repository,
        harness,
        alice_token,
        alice.session,
        "Summary",
        request_id="derived",
        derived_from=[{"update_id": original.id, "revision": original.revision}],
    )
    assert derived.derived_from == {original.id: 1}
    view = harness.read_workspace(authenticate(repository, alice_token), "team")
    assert view.sources[0].reference == "https://example.org/paper"
    assert not hasattr(view.sources[0], "users")
    assert not hasattr(view.sources[0], "clients")


def test_policy_changes_are_audited_without_credentials(
    repository: SQLiteRepository, pilot: Pilot
) -> None:
    admin, harness, alice_token, _, _ = pilot
    admin.set_membership("team", "alice", False)
    policy = repository.get(Policy, "access")
    events = repository.records(Audit)
    event = next(e for e in events if e.action == "membership.revoke")
    assert event.target_id == "alice"
    assert event.workspace_id == "team"
    assert event.user == "local-operator"
    assert policy is not None
    assert event.policy_revision == policy.revision
    assert alice_token not in repr(events)


def amend_from_connection(
    path: Path,
    token: str,
    session_id: str,
    update_id: str,
    request_id: str,
    barrier: Barrier,
) -> str:
    with SQLiteRepository(path) as repository:
        caller = authenticate(repository, token)
        barrier.wait(timeout=10)
        try:
            Harness(repository).post_update(
                caller,
                session_id,
                "proposal",
                request_id,
                request_id,
                [],
                update_id=update_id,
                expected_revision=1,
            )
        except Conflict:
            return "conflict"
        return "saved"


def test_concurrent_amendments_cannot_overwrite_each_other(
    repository: SQLiteRepository, pilot: Pilot
) -> None:
    _, harness, alice_token, _, _ = pilot
    alice = join(repository, harness, alice_token)
    update = post(repository, harness, alice_token, alice.session)
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                amend_from_connection,
                repository.path,
                alice_token,
                alice.session.id,
                update.id,
                request_id,
                barrier,
            )
            for request_id in ["first", "second"]
        ]
        assert sorted(f.result(timeout=15) for f in futures) == ["conflict", "saved"]
    assert [c.update.revision for c in repository.changes("team")] == [1, 2]


@pytest.mark.parametrize("operation", ["post", "review", "end"])
def test_audit_failure_rolls_back_records_history_and_receipts(
    repository: SQLiteRepository, pilot: Pilot, mocker: MockerFixture, operation: str
) -> None:
    _, harness, token, _, review_token = pilot
    joined = join(repository, harness, token)
    original = post(repository, harness, token, joined.session)
    before = repository.connection.iterdump()
    expected = list(before)
    mocker.patch.object(harness, "_audit", side_effect=OSError("disk full"))
    with pytest.raises(OSError):
        if operation == "post":
            post(repository, harness, token, joined.session, "More", request_id="more")
        elif operation == "review":
            harness.review_update(
                authenticate(repository, review_token), "team", original.id, 1, "accepted", "review"
            )
        else:
            harness.end_session(
                authenticate(repository, token), joined.session.id, "end", "Next", []
            )
    with SQLiteRepository(repository.path) as reopened:
        assert list(reopened.connection.iterdump()) == expected


def test_reopened_snapshot_and_retry_keep_original_revision(
    repository: SQLiteRepository, pilot: Pilot
) -> None:
    admin, harness, token, bob_token, _ = pilot
    admin.register_source("team", "paper", "paper:v1", ["alice"], ["alice-agent"])
    joined = join(repository, harness, token)
    original = post(repository, harness, token, joined.session, sources=["paper"])
    caller = authenticate(repository, token)
    snapshot = harness.read_workspace(caller, "team", limit=1)
    amended = post(
        repository,
        harness,
        token,
        joined.session,
        "Changed",
        request_id="amend",
        sources=[],
        update_id=original.id,
        expected_revision=1,
    )
    with SQLiteRepository(repository.path) as reopened:
        fresh = Harness(reopened)
        assert post(reopened, fresh, token, joined.session, sources=["paper"]) == original
        page = fresh.read_workspace(caller, "team", cursor=snapshot.next_page, limit=1)
        assert page.updates == [original]
        assert fresh.get_updates(caller, "team", snapshot.cursor).changes[0].update == amended
        page.updates[0].sources.clear()
        assert reopened.changes("team")[0].update.sources == ["paper"]
    admin.set_source_access("team", "paper", [], [])
    with pytest.raises(AccessDenied):
        post(repository, harness, token, joined.session, sources=["paper"])
    assert harness.read_workspace(authenticate(repository, bob_token), "team").updates == []


def test_other_workspace_ids_cannot_be_used_as_sources_or_sessions(
    repository: SQLiteRepository, pilot: Pilot
) -> None:
    admin, harness, token, bob_token, _ = pilot
    admin.create_workspace("private", "Private")
    admin.create_task("private", "plan", "Private task")
    admin.set_membership("private", "alice", True)
    alice = authenticate(repository, token)
    private = harness.join_workspace(alice, "private", "plan", "private")
    secret = harness.post_update(alice, private.session.id, "finding", "Secret", "secret", [])
    public = join(repository, harness, token)
    with pytest.raises(AccessDenied):
        post(
            repository,
            harness,
            token,
            public.session,
            derived_from=[{"update_id": secret.id, "revision": secret.revision}],
        )
    bob = authenticate(repository, bob_token)
    with pytest.raises(AccessDenied):
        harness.read_workspace(bob, "private")
    with pytest.raises(AccessDenied):
        harness.join_workspace(bob, "team", "plan", "impersonate", public.session.id)


def test_snapshot_orders_first_contributions_and_keeps_amendments_in_place(
    repository: SQLiteRepository, pilot: Pilot, mocker: MockerFixture
) -> None:
    _, harness, token, _, _ = pilot
    joined = join(repository, harness, token)
    mocker.patch.object(
        harness, "identifiers", side_effect=["z-first", "audit1", "a-next", "audit2"]
    )
    first = post(repository, harness, token, joined.session)
    second = post(repository, harness, token, joined.session, "Next", request_id="next")
    mocker.stopall()
    caller = authenticate(repository, token)
    snapshot = harness.read_workspace(caller, "team", limit=2)
    amended = post(
        repository,
        harness,
        token,
        joined.session,
        "Amended",
        request_id="amend-order",
        update_id=first.id,
        expected_revision=1,
    )
    assert snapshot.updates == [first]
    assert harness.read_workspace(caller, "team", cursor=snapshot.next_page).updates == [second]
    assert harness.read_workspace(caller, "team").updates == [amended, second]


def test_change_cursor_inherits_scope_and_requires_fresh_read_to_switch_tasks(
    repository: SQLiteRepository, pilot: Pilot
) -> None:
    admin, harness, token, _, _ = pilot
    admin.create_task("team", "other", "Other task")
    caller = authenticate(repository, token)
    joined = join(repository, harness, token)
    other = harness.join_workspace(caller, "team", "other", "other-join")
    first = post(repository, harness, token, joined.session)
    harness.post_update(caller, other.session.id, "progress", "Elsewhere", "other-post", [])
    second = post(repository, harness, token, joined.session, "Next", request_id="next")
    page = harness.get_updates(caller, "team", joined.view.cursor, task_id="plan", limit=1)
    assert [r.update for r in page.changes] == [first]
    assert page.has_more and page.task_id == "plan"
    next_page = harness.get_updates(caller, "team", page.cursor, limit=1)
    assert [r.update for r in next_page.changes] == [second]
    assert not next_page.has_more and next_page.task_id == "plan"
    harness.post_update(caller, other.session.id, "progress", "Later elsewhere", "later", [])
    with SQLiteRepository(repository.path) as reopened:
        resumed = Harness(reopened).get_updates(caller, "team", next_page.cursor)
    assert resumed.changes == [] and not resumed.has_more and resumed.task_id == "plan"
    with pytest.raises(Conflict):
        harness.get_updates(caller, "team", resumed.cursor, task_id="other")
    snapshot = harness.read_workspace(caller, "team", limit=1)
    assert snapshot.task_id is None
    with pytest.raises(Conflict):
        harness.read_workspace(caller, "team", cursor=snapshot.next_page, task_id="plan")
    fresh = harness.read_workspace(caller, "team")
    assert {u.task_id for u in fresh.updates} == {"plan", "other"}


def test_snapshot_cursor_inherits_scope_without_widening(
    repository: SQLiteRepository, pilot: Pilot
) -> None:
    admin, harness, token, _, _ = pilot
    admin.create_task("team", "other", "Other task")
    caller = authenticate(repository, token)
    joined = join(repository, harness, token)
    other = harness.join_workspace(caller, "team", "other", "other-join")
    first = post(repository, harness, token, joined.session)
    harness.post_update(caller, other.session.id, "progress", "Elsewhere", "other-post", [])
    page = harness.read_workspace(caller, "team", task_id="plan", limit=1)
    assert page.task_id == "plan" and page.next_page is not None
    second = post(repository, harness, token, joined.session, "Next", request_id="next")
    continued = harness.read_workspace(caller, "team", cursor=page.next_page)
    assert continued.task_id == "plan" and continued.next_page is None
    assert continued.updates == [first]
    changes = harness.get_updates(caller, "team", continued.cursor)
    assert changes.task_id == "plan" and [r.update for r in changes.changes] == [second]
    with pytest.raises(Conflict):
        harness.read_workspace(caller, "team", cursor=page.next_page, task_id="other")


@pytest.mark.parametrize("limit", [0, 101])
def test_join_page_limit_does_not_change_session_identity_or_leave_partial_records(
    repository: SQLiteRepository, pilot: Pilot, limit: int
) -> None:
    _, harness, token, _, _ = pilot
    caller = authenticate(repository, token)
    before = list(repository.connection.iterdump())
    with pytest.raises(InvalidRequest, match="Page size"):
        harness.join_workspace(caller, "team", "plan", "join", limit=limit)
    assert list(repository.connection.iterdump()) == before
    joined = harness.join_workspace(caller, "team", "plan", "join", limit=1)
    update = post(repository, harness, token, joined.session)
    retry = harness.join_workspace(caller, "team", "plan", "join", limit=100)
    assert retry.session.id == joined.session.id
    assert retry.view.updates == [update]
    assert len(repository.records(Session, "team")) == 1


def test_legacy_snapshot_cursor_requires_a_fresh_read(
    repository: SQLiteRepository, pilot: Pilot
) -> None:
    _, harness, token, _, _ = pilot
    joined = join(repository, harness, token)
    post(repository, harness, token, joined.session)
    caller = authenticate(repository, token)
    page = harness.read_workspace(caller, "team", limit=1)
    assert page.next_page is not None
    cursor = repository.get(Cursor, page.next_page, "team")
    assert cursor is not None
    with repository.transaction():
        repository.save(replace(cursor, mode="snapshot"))
    with pytest.raises(Conflict, match="read the workspace again"):
        harness.read_workspace(caller, "team", cursor=page.next_page)
    assert harness.get_updates(caller, "team", page.cursor).changes == []


@pytest.mark.parametrize("operation", ["post", "end"])
def test_stale_dependency_rejects_the_whole_mutation(
    repository: SQLiteRepository, pilot: Pilot, operation: str
) -> None:
    _, harness, alice_token, bob_token, _ = pilot
    alice = join(repository, harness, alice_token)
    bob = join(repository, harness, bob_token)
    first = post(repository, harness, alice_token, alice.session)
    second = post(repository, harness, alice_token, alice.session, request_id="second")
    post(
        repository,
        harness,
        alice_token,
        alice.session,
        "Corrected",
        request_id="correct",
        update_id=second.id,
        expected_revision=1,
    )
    references: list[Dependency] = [
        {"update_id": first.id, "revision": 1},
        {"update_id": second.id, "revision": 1},
    ]
    caller = authenticate(repository, bob_token)
    before = list(repository.connection.iterdump())
    with pytest.raises(Conflict, match="refresh and reconsider"):
        if operation == "post":
            harness.post_update(
                caller, bob.session.id, "progress", "Result", "result", [], references
            )
        else:
            harness.end_session(caller, bob.session.id, "result", "Result", [], references)
    assert list(repository.connection.iterdump()) == before
    references[1]["revision"] = 2
    if operation == "post":
        result = harness.post_update(
            caller, bob.session.id, "progress", "Result", "result", [], references
        )
    else:
        harness.end_session(caller, bob.session.id, "result", "Result", [], references)
        result = repository.changes("team")[-1].update
    assert result.derived_from == {first.id: 1, second.id: 2}


def test_id_only_dependencies_are_rejected(repository: SQLiteRepository, pilot: Pilot) -> None:
    _, harness, token, _, _ = pilot
    joined = join(repository, harness, token)
    original = post(repository, harness, token, joined.session)
    with pytest.raises(InvalidRequest, match="update_id and revision"):
        post(
            repository,
            harness,
            token,
            joined.session,
            request_id="derived",
            derived_from=[original.id],  # type: ignore[list-item]
        )


@pytest.mark.parametrize("change", ["content", "review"])
def test_dependency_retry_and_inherited_amendment_keep_the_original_revision(
    repository: SQLiteRepository, pilot: Pilot, change: str
) -> None:
    _, harness, token, _, reviewer = pilot
    joined = join(repository, harness, token)
    original = post(repository, harness, token, joined.session)
    text = f"Based on {original.id}"
    references: list[Dependency] = [{"update_id": original.id, "revision": 1}]
    derived = post(
        repository,
        harness,
        token,
        joined.session,
        text,
        request_id="derived",
        derived_from=references,
    )
    if change == "content":
        post(
            repository,
            harness,
            token,
            joined.session,
            "Correction",
            request_id="correct",
            update_id=original.id,
            expected_revision=1,
        )
    else:
        harness.review_update(
            authenticate(repository, reviewer), "team", original.id, 1, "accepted", "review"
        )
    with SQLiteRepository(repository.path) as reopened:
        retry = post(
            reopened,
            Harness(reopened),
            token,
            joined.session,
            text,
            request_id="derived",
            derived_from=references,
        )
        assert retry == derived
    with pytest.raises(Conflict, match="Request ID"):
        post(
            repository,
            harness,
            token,
            joined.session,
            text,
            request_id="derived",
            derived_from=[{"update_id": original.id, "revision": 2}],
        )
    amended = post(
        repository,
        harness,
        token,
        joined.session,
        f"Expanded: {text}",
        request_id="expand",
        update_id=derived.id,
        expected_revision=1,
    )
    assert amended.derived_from == {original.id: 1}
    with pytest.raises(Conflict, match="refresh and reconsider"):
        post(
            repository,
            harness,
            token,
            joined.session,
            text,
            request_id="reassert",
            update_id=derived.id,
            expected_revision=2,
            derived_from=references,
        )


@pytest.mark.parametrize("revocation", ["source", "credential"])
def test_dependency_retry_rechecks_current_access(
    repository: SQLiteRepository, pilot: Pilot, revocation: str
) -> None:
    admin, harness, token, _, _ = pilot
    admin.register_source("team", "paper", "synthetic", ["alice"], ["alice-agent"])
    joined = join(repository, harness, token)
    original = post(repository, harness, token, joined.session, sources=["paper"])
    caller = authenticate(repository, token)
    references: list[Dependency] = [{"update_id": original.id, "revision": 1}]
    harness.post_update(caller, joined.session.id, "progress", "Result", "result", [], references)
    if revocation == "source":
        admin.set_source_access("team", "paper", [], [])
    else:
        admin.revoke_credential(token)
    with pytest.raises(AccessDenied, match="^Access denied$"):
        harness.post_update(
            caller, joined.session.id, "progress", "Result", "result", [], references
        )


@pytest.mark.parametrize("revision", [1, 2])
def test_duplicate_dependency_references_are_rejected(
    repository: SQLiteRepository, pilot: Pilot, revision: int
) -> None:
    _, harness, token, _, _ = pilot
    joined = join(repository, harness, token)
    original = post(repository, harness, token, joined.session)
    with pytest.raises(InvalidRequest, match="Duplicate dependency"):
        post(
            repository,
            harness,
            token,
            joined.session,
            request_id="duplicate",
            derived_from=[
                {"update_id": original.id, "revision": 1},
                {"update_id": original.id, "revision": revision},
            ],
        )


def test_restricted_dependency_does_not_disclose_its_revision(
    repository: SQLiteRepository, pilot: Pilot
) -> None:
    admin, harness, alice_token, bob_token, _ = pilot
    admin.register_source("team", "private", "synthetic", ["alice"], ["alice-agent"])
    alice = join(repository, harness, alice_token)
    bob = join(repository, harness, bob_token)
    secret = post(repository, harness, alice_token, alice.session, sources=["private"])
    with pytest.raises(AccessDenied, match="^Access denied$"):
        post(
            repository,
            harness,
            bob_token,
            bob.session,
            request_id="secret",
            derived_from=[{"update_id": secret.id, "revision": 99}],
        )


@pytest.mark.parametrize("read", ["snapshot", "changes"])
@pytest.mark.parametrize("legacy", [False, True])
def test_added_source_restriction_protects_earlier_revisions(
    repository: SQLiteRepository, pilot: Pilot, read: str, legacy: bool
) -> None:
    admin, harness, alice_token, bob_token, _ = pilot
    admin.register_source("team", "private", "synthetic", ["alice"], ["alice-agent"])
    alice = join(repository, harness, alice_token)
    bob = join(repository, harness, bob_token)
    original = post(repository, harness, alice_token, alice.session, "Restricted disclosure")
    caller = authenticate(repository, bob_token)
    snapshot = harness.read_workspace(caller, "team", limit=1)
    assert snapshot.next_page is not None
    if legacy:
        with repository.transaction():
            amended = replace(original, sources=["private"], revision=2)
            repository.save(amended)
            repository.append_change(amended, "alice", "alice-agent")
    else:
        post(
            repository,
            harness,
            alice_token,
            alice.session,
            "Restricted disclosure",
            request_id="correct-sources",
            sources=["private"],
            update_id=original.id,
            expected_revision=1,
        )
    if read == "snapshot":
        with pytest.raises(Conflict, match="read the workspace again"):
            harness.read_workspace(caller, "team", cursor=snapshot.next_page)
    elif not legacy:
        with pytest.raises(Conflict, match="read the workspace again"):
            harness.get_updates(caller, "team", bob.view.cursor)
    else:
        assert harness.get_updates(caller, "team", bob.view.cursor).changes == []
    assert harness.read_workspace(caller, "team").updates == []
