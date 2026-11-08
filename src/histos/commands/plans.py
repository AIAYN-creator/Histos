"""histos plan propose/diff/approve/reject: the whole card table as one reviewable plan."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .. import canvas, operations, plans
from ._shared import _vault_root


def cmd_plan(args: argparse.Namespace) -> int:
    vault_root = _vault_root()
    try:
        if args.plan_action == "propose":
            draft = Path(args.file)
            if not draft.exists():
                raise canvas.HistosError(f"can't find the draft '{draft}'")
            result = plans.propose(vault_root, draft.read_text(encoding="utf-8"))
            if result.replaced_pending:
                print("warning: replaced the plan that was already pending", file=sys.stderr)
            for line in plans.warnings(plans.pending(vault_root), operations._load_valid(vault_root)):
                print(line, file=sys.stderr)
            print(f"plan with {result.cards} cards pending review ('histos plan diff' to review it)")
        elif args.plan_action == "diff":
            return _diff(vault_root)
        elif args.plan_action == "approve":
            result = plans.approve(vault_root)
            for node in result.created:
                print(f"card '{node['id']}' created ({operations.STATE_NAMES[node['color']]}) -> {node['file']}")
            by_state: dict[str, int] = {}
            for node in result.created:
                state = operations.STATE_NAMES[node["color"]]
                by_state[state] = by_state.get(state, 0) + 1
            counts = ", ".join(f"{n} {state}" for state, n in by_state.items())
            print(f"plan approved: {len(result.created)} cards created ({counts}) "
                  f"-- kept in {result.archived.relative_to(vault_root).as_posix()}")
        else:
            cards = plans.reject(vault_root, args.feedback)
            print(f"plan discarded ({cards} cards, none created)")
    except canvas.HistosError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


def _diff(vault_root: Path) -> int:
    plan = plans.pending(vault_root)
    if plan is None:
        print("no pending plan")
        feedback = plans.last_feedback(vault_root)
        if feedback:
            print(f"Feedback on the last rejected plan: {feedback}")
        return 0
    print(f"Plan: {len(plan.cards)} cards\n\n{plan.summary}\n")
    rows = [("id", "title", "depends on", "done when")]
    rows += [(c.id, c.title, ", ".join(c.depends_on) or "-", c.done_when) for c in plan.cards]
    widths = [max(len(r[i]) for r in rows) for i in range(3)]
    for n, row in enumerate(rows):
        print("  ".join(cell.ljust(widths[i]) if i < 3 else cell for i, cell in enumerate(row)).rstrip())
        if n == 0:
            print("  ".join("-" * w for w in widths) + "  " + "-" * 9)
    data = operations._load_valid(vault_root)
    print("\nLevels:")
    for n, level in enumerate(plans.levels(plan, data), start=1):
        print(f"  {n}  {', '.join(level)}")
    found = plans.warnings(plan, data)
    print()
    print("\n".join(found) if found else "No warnings.")
    return 0
