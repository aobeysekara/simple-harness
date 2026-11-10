from typing import Any


def _required(record: dict[str, Any], field: str, kind: str) -> Any:
    if field not in record or record[field] in (None, "", []):
        raise ValueError(f"Missing {kind} field: {field}")
    return record[field]


def transform(rows: list[dict[str, Any]], contract: dict[str, Any]) -> list[dict[str, Any]]:
    """Return workload by TYPE for one project's triage workflow."""
    project = _required(contract, "project", "contract")
    workflow = _required(contract, "workflow", "contract")
    identity_fields = _required(contract, "record_identity", "contract")
    cancellation = _required(contract, "cancelled_issues", "contract")
    if identity_fields != ["project", "workflow", "issue_id", "entry_id"]:
        raise ValueError("Record identity must be project, workflow, issue_id, entry_id")
    if cancellation not in ("include", "exclude"):
        raise ValueError("cancelled_issues must be include or exclude")
    quantities: dict[str, int] = {}
    seen: dict[tuple[str, ...], tuple[str, int, str]] = {}
    for row in rows:
        identity = tuple(str(_required(row, field, "row")) for field in identity_fields)
        if (row["project"], row["workflow"]) != (project, workflow):
            raise ValueError(f"Row outside contract scope: {row['project']}/{row['workflow']}")
        category = _required(row, "category", "row")
        points = _required(row, "points", "row")
        status = _required(row, "status", "row")
        if not isinstance(points, int) or isinstance(points, bool) or points < 0:
            raise ValueError("Row points must be a non-negative integer")
        workload = (category, points, status)
        if identity in seen:
            if seen[identity] != workload:
                raise ValueError(f"Conflicting record identity: {'/'.join(identity)}")
            continue
        seen[identity] = workload
        if status == "cancelled" and cancellation == "exclude":
            continue
        quantities[category] = quantities.get(category, 0) + points
    return [
        {
            "project": project,
            "workflow": workflow,
            "category": category,
            "points": quantities[category],
        }
        for category in sorted(quantities)
    ]
