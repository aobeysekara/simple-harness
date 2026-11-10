"""Internal reuse policy evaluated inside the caller's repository transaction.

Harness owns access checks, transaction boundaries and audit writes. This policy
only reads records; it never caches permissions or opens a nested transaction.
"""

from simple_framework.domain import ContextIssue, ContextItem, ContextPacket, Update, ValidationRun
from simple_framework.ports import Repository
from simple_framework.reuse import applicability, bounded_packet, semantic_digest


class ReusePolicy:
    """Evaluate validation, dependency freshness and complete context selection."""

    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    def passing_runs(self, update: Update) -> list[ValidationRun]:
        """Return the latest matching machine passes, or none if any case fails."""
        if not update.correction:
            return []
        digest = semantic_digest(update)
        runs = sorted(
            self.repository.records(ValidationRun, update.workspace_id), key=lambda r: r.number
        )
        latest = {
            r.case_id: r for r in runs if r.update_id == update.id and r.subject_digest == digest
        }
        selected = []
        for case in update.correction["cases"]:
            run = latest.get(case["id"])
            if (
                run is None
                or run.method != "machine"
                or run.outcome != "passed"
                or run.artifacts
                != {"code": case["code"], "inputs": case["inputs"], "test": case["test"]}
            ):
                return []
            selected.append(run)
        return selected

    def stale(self, update: Update, history: dict[tuple[str, int], Update]) -> bool:
        """Detect changed or unavailable dependencies through exact historical references."""
        pending = list(update.derived_from.items())
        visited: set[tuple[str, int]] = set()
        while pending:
            reference = pending.pop()
            if reference in visited:
                continue
            visited.add(reference)
            if len(visited) > 256:
                return True
            original = history.get(reference)
            current = self.repository.get(Update, reference[0], update.workspace_id)
            if (
                original is None
                or current is None
                or semantic_digest(original) != semantic_digest(current)
                or (current.status == "rejected" and original.status != "rejected")
            ):
                return True
            pending.extend(current.derived_from.items())
        return False

    def support(self, update: Update) -> list[Update]:
        """Collect all current supporting records in deterministic order."""
        pending = list(update.derived_from)
        records: dict[str, Update] = {}
        while pending:
            update_id = pending.pop()
            if update_id in records or update_id == update.id:
                continue
            record = self.repository.get(Update, update_id, update.workspace_id)
            if record is not None:
                records[update_id] = record
                pending.extend(record.derived_from)
        return [records[key] for key in sorted(records)]

    def requirements(self, update: Update) -> list[Update]:
        """Collect prerequisites, excluding a correction target unless also a fact."""
        records = {update.id: update}
        pending = [update]
        while pending:
            current = pending.pop()
            references = set(current.derived_from)
            if current.correction:
                corrected = current.correction["corrects"]["update_id"]
                if corrected not in {r["update_id"] for r in current.correction["facts"]}:
                    references.discard(corrected)
            for update_id in references - records.keys():
                dependency = self.repository.get(Update, update_id, update.workspace_id)
                if dependency is not None:
                    records[update_id] = dependency
                    pending.append(dependency)
        return [record for key, record in records.items() if key != update.id]

    def eligible(self, update: Update) -> bool:
        """Require acceptance, content-bound scope approval and the current passing runs."""
        approval = update.reuse_approval
        if (
            update.status != "accepted"
            or approval is None
            or approval["subject_digest"] != semantic_digest(update)
        ):
            return False
        runs = self.passing_runs(update)
        return bool(runs) and [r.id for r in runs] == approval["validation_ids"]

    def select_context(
        self,
        workspace_id: str,
        task_id: str,
        goal: str,
        facts: dict[str, str],
        byte_budget: int,
        visible_updates: list[Update],
    ) -> ContextPacket:
        """Select from updates whose full source/dependency access Harness has checked."""
        history = {
            (c.update.id, c.update.revision): c.update
            for c in self.repository.changes(workspace_id)
        }
        items = []
        issues = []
        for update in visible_updates:
            if (update.correction and goal not in update.goals) or (
                update.correction is None and update.task_id != task_id
            ):
                continue
            requirements = self.requirements(update)
            matches = [
                applicability(r.correction, facts) for r in [update, *requirements] if r.correction
            ]
            if any(match == "out_of_scope" for match, _ in matches):
                continue
            missing = sorted({key for _, keys in matches for key in keys})
            if missing:
                issues.append(ContextIssue(update.id, "unknown_applicability", missing))
                continue
            if self.stale(update, history):
                issues.append(ContextIssue(update.id, "needs_revalidation"))
                continue
            if any(r.correction and not self.eligible(r) for r in requirements):
                issues.append(ContextIssue(update.id, "needs_revalidation"))
                continue
            evidence = {
                run.id: run
                for record in [update, *requirements]
                for run in self.passing_runs(record)
            }
            validations = [evidence[key] for key in sorted(evidence)]
            if update.correction:
                if not self.eligible(update):
                    if any(h.id == update.id and h.reuse_approval for h in history.values()):
                        issues.append(ContextIssue(update.id, "needs_revalidation"))
                    continue
                items.append(
                    ContextItem(
                        update,
                        "approved_reuse; exact_scope_match",
                        self.support(update),
                        validations,
                    )
                )
            elif update.task_id == task_id and (
                update.kind == "question"
                or (update.kind == "decision" and update.status == "accepted")
            ):
                items.append(
                    ContextItem(update, "declared_goal_or_task", self.support(update), validations)
                )
        return bounded_packet(workspace_id, task_id, goal, byte_budget, items, issues)
