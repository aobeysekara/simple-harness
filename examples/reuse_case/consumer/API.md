# Transformation interface

Implement `transform(rows, contract)` in `model.py`. Return a list sorted by TYPE. Each output dictionary contains exactly `project`, `workflow`, `category` and integer `points`.

Use the contract supplied to each call. Support both `include` and `exclude` cancellation policies without assuming which project uses either policy. Retrieve the policy and record identity for the assigned task from its supplied context.

The contract requires `project`, `workflow`, `record_identity` and `cancelled_issues`. A missing field raises `ValueError("Missing contract field: FIELD")`. Check those fields in that order.

The record identity is an ordered list of field names. A missing identity field in a row raises `ValueError("Missing row field: FIELD")`.

Identical copies of a issue entry count once. Conflicting copies raise `ValueError("Conflicting record identity: IDENTITY")`, with identity values joined by `/` in their declared order.

Every row must belong to the supplied project and workflow. Otherwise raise `ValueError("Row outside contract scope: CUSTOMER/WORKFLOW")`, using the row's values. Do not silently discard such rows.

The evaluator checks this interface as well as the assigned extract. It supplies contracts for different project scopes and checks missing or contradictory inputs. Expected totals and the assigned project's cancellation policy are not part of this interface document.
