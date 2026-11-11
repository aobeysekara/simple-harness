import json
from pathlib import Path

from typer.testing import CliRunner

from examples.reuse_comparison import compare
from examples.reuse_loop import FIXTURE, run_demo
from simple_framework.cli import app


def test_complete_reuse_demo_keeps_failures_and_prepares_equivalent_conditions(
    tmp_path: Path,
) -> None:
    root = tmp_path / "demo"
    report = run_demo(root)
    assert report["packet_bytes"] <= 16384
    assert report["session_bytes"] < 512
    assert [row["points"] for row in report["scripted_fresh_result"]] == [15, 7]
    packet = json.loads((root / "operator/packet.json").read_text())
    assert packet["status"] == "ready" and len(packet["items"]) == 2
    markdown = (root / "markdown/HANDOVER.md").read_text()
    for item in packet["items"]:
        assert item["update"]["text"] in markdown
        assert item["update"]["id"] in markdown
        for run in item["validation"]:
            assert run["evidence"]["sha256"] in markdown
    assert not list((root / "simple").glob("*.token"))
    assert "cancelled_issues" not in (root / "simple/BRIEF.md").read_text()
    for condition in ("simple", "markdown", "ablation"):
        assert (root / condition / "API.md").read_bytes() == (
            FIXTURE / "consumer/API.md"
        ).read_bytes()
    assert "Scripted consumer" in report["limits"]
    stages = compare(root)
    assert [stage["simple_guidance"] for stage in stages] == [2, 0, 0, 2]
    assert [stage["simple_status"] for stage in stages] == [
        "ready",
        "insufficient_context",
        "insufficient_context",
        "ready",
    ]
    assert [stage["maintained_markdown_guidance"] for stage in stages] == [2, 0, 0, 2]
    before = json.loads((root / "operator/before-contract-change.json").read_text())
    stale = json.loads((root / "operator/stale.json").read_text())
    assert {issue["update_id"] for issue in stale["issues"]} == {
        item["update"]["id"] for item in before["items"]
    }
    assert {issue["reason"] for issue in stale["issues"]} == {"needs_revalidation"}
    initial_markdown = (root / "comparison/01-approved.md").read_text()
    assert initial_markdown == (root / "comparison/unchanged-snapshot.md").read_text()
    assert (root / "comparison/04-reapproved.md").read_text() == markdown
    for name in ("02-dependency-changed", "03-replayed-not-approved"):
        maintained = (root / f"comparison/{name}.md").read_text()
        assert "WITHDRAWN" in maintained
        for item in before["items"]:
            assert item["update"]["text"] in initial_markdown
            assert item["update"]["text"] not in maintained
    result = CliRunner().invoke(
        app,
        [
            "--db",
            str(root / "operator/workspace.sqlite3"),
            "validate",
            "reuse",
            packet["items"][0]["update"]["id"],
            "--manifest",
            str(root / "operator/cases/fixed.json"),
            "--evidence-directory",
            str(root / "operator/runs"),
            "--request-id",
            "cli-replay",
        ],
        env={"SIMPLE_TOKEN": (root / "operator/validator.token").read_text()},
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["outcome"] == "passed"
