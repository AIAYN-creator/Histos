"""histos estimate --coverage: how many closed cards each estimate group has. Read only."""
from __future__ import annotations

import argparse
import sys

from .. import estimates, operations

NAME_WIDTH = 34


def cmd_estimate(args: argparse.Namespace) -> int:
    if not args.coverage:
        print(
            "error: 'histos estimate' only exists for --coverage in this version "
            "(how many closed cards each estimate group has). Each card's estimates are in 'histos status'.",
            file=sys.stderr,
        )
        return 1
    report = operations.coverage()
    if not report.samples:
        print("no closed cards yet: estimates start after the first approvals with histos 3.1+")
        for entry in report.skipped:
            print(f"skipped: {entry}")
        return 0

    vaults = len(report.vault_names)
    approximate = sum(s.approximate for s in report.samples)
    skipped = f" -- {len(report.skipped)} vault{'' if len(report.skipped) == 1 else 's'} skipped" if report.skipped else ""
    print(f"History: {len(report.samples)} closed cards in {vaults} vault{'' if vaults == 1 else 's'} "
          f"({approximate} approximate){skipped}")
    for entry in report.skipped:
        print(f"  skipped: {entry}")

    _table("Agent time", report.agent)
    _table("Review time", report.review)
    print(f"\nOpen cards with an estimate right now: agent {report.open_with_agent} of {report.open_cards}, "
          f"review {report.open_with_review} of {report.open_cards}")

    if report.outliers:
        print(f"\nAgent time over {estimates.OUTLIER_AGENT_HOURS // 24} days (forgotten In progress?)")
        for s in report.outliers:
            vault = report.vault_names.get(s.vault, s.vault)
            print(f"  {vault} · {s.card}: {s.agent_hours / 24:.1f} days")
    return 0


def _table(title: str, groups: list[tuple]) -> None:
    print(f"\n{title:<{NAME_WIDTH + 2}}{'cards':>5}   enough?")
    for group, count in groups:
        missing = estimates.still_needed(count)
        enough = "yes" if not missing else f"{missing} more"
        print(f"  {estimates.group_label(group):<{NAME_WIDTH}}{count:>5}   {enough}")
