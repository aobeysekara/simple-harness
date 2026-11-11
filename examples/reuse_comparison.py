"""Compare enforced context withdrawal with explicitly maintained Markdown.

This is a scripted mechanism demonstration, not a benchmark of people or agents.
"""

import argparse
import json
from pathlib import Path
from typing import TypedDict

from examples.reuse_loop import run_demo, write_json


class Stage(TypedDict):
    name: str
    simple_status: str
    simple_guidance: int
    maintained_markdown_guidance: int
    unchanged_markdown_guidance: int


def compare(directory: Path) -> list[Stage]:
    """Pair observed harness states with a scripted Markdown maintainer's actions."""
    operator = directory / "operator"
    output = directory / "comparison"
    output.mkdir()
    before = (operator / "before-contract-change.md").read_text()
    after = (directory / "markdown/HANDOVER.md").read_text()
    withdrawn = (
        "# Maintained handover\n\n"
        "WITHDRAWN: do not reuse the cancellation correction or its dependent procedure.\n\n"
        "The declared contract version changed. The maintainer has withdrawn both items "
        "until their dependencies are updated, checks pass and content and scope are "
        "reviewed again. A passing replay alone is insufficient.\n\n"
        "The old handover remains in 01-approved.md for historical reference only.\n"
    )
    stages: list[Stage] = []
    for name, snapshot, maintained, eligible in (
        ("01-approved", "before-contract-change.json", before, 2),
        ("02-dependency-changed", "stale.json", withdrawn, 0),
        ("03-replayed-not-approved", "awaiting-review.json", withdrawn, 0),
        ("04-reapproved", "packet.json", after, 2),
    ):
        packet = json.loads((operator / snapshot).read_text())
        stages.append(
            {
                "name": name,
                "simple_status": packet["status"],
                "simple_guidance": len(packet["items"]),
                # These counts describe the scripted maintainer's declared decisions.
                # They are not produced by an independent Markdown policy engine.
                "maintained_markdown_guidance": eligible,
                "unchanged_markdown_guidance": 2,
            }
        )
        (output / f"{name}.md").write_text(maintained)
    (output / "unchanged-snapshot.md").write_text(before)
    write_json(
        output / "results.json",
        {
            "kind": "Scripted mechanism comparison; no people or model consumers evaluated",
            "stages": stages,
            "markdown_method": (
                "Initial and final handovers export the same permitted harness content. "
                "At the known change point, this script acts as a Markdown maintainer, "
                "explicitly withdrawing both items until replay and review complete. "
                "The unchanged snapshot is a separate control, not the maintained baseline."
            ),
            "limits": (
                "The fixture changes a declared contract version, not calculation semantics. "
                "SIMple does not discover external file changes or recall context already read. "
                "No independent workflow, productivity, coordination-time or token benchmark."
            ),
        },
    )
    return stages


def main() -> None:
    """Run the existing replay loop and print the four observable reuse states."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True, help="New output directory")
    args = parser.parse_args()
    directory = args.directory.resolve()
    run_demo(directory)
    stages = compare(directory)
    print("Guidance items (scripted demonstration)")
    print("Stage                         SIMple  Maintained Markdown  Unchanged snapshot")
    print("                              allowed allowed              still shown")
    for stage in stages:
        print(
            f"{stage['name']:<30}{stage['simple_guidance']:<8}"
            f"{stage['maintained_markdown_guidance']:<21}"
            f"{stage['unchanged_markdown_guidance']}"
        )
    print("Markdown withdrawal is scripted maintenance, not automatic detection.")
    print("No live human or agent comparison; no productivity claim.")
    print(f"Inspect {directory / 'comparison/results.json'} and the adjacent handovers.")


if __name__ == "__main__":
    main()
