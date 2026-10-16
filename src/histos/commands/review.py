"""Commands around a proposal: propose (with its evals gate), eval, diff, approve, reject, retry, accept-edit."""
from __future__ import annotations

import argparse
import difflib
import sys
from datetime import datetime
from pathlib import Path

from .. import canvas, config, context, evals, frontmatter, operations
from ..fsutil import write_text_atomic
from ._shared import _vault_root, _with_vault_lock


def _gate_on_evals(vault_root: Path, data: dict, card: dict, draft: str) -> bool:
    """Runs the vault's evals on the draft and records eval_results. True if the proposal may
    go ahead. Without a config or with evals off, does nothing (v2 behaviour). Raises
    HistosError on a broken config or evals/ folder, before counting an attempt."""
    cfg = config.load(vault_root)
    if cfg is None or not cfg.evals.enabled:
        return True

    md_path = canvas.card_file_path(vault_root, card)
    meta, body = frontmatter.read(md_path)
    last = meta.get(operations.EVAL_RESULTS_KEY) or {}
    failed_before = last.get("passed") is False
    used = last.get("attempt", 0) if failed_before else 0
    if used >= context.MAX_ATTEMPTS:
        print(
            f"error: '{card['id']}' used its {context.MAX_ATTEMPTS} attempts -- it stays In progress for the human\n"
            f"(last failures in its eval_results; 'histos retry {card['id']}' gives it {context.MAX_ATTEMPTS} more)",
            file=sys.stderr,
        )
        return False

    result = evals.run_all(vault_root, cfg, data, card, draft)
    attempt = used + 1
    meta[operations.EVAL_RESULTS_KEY] = {
        "passed": result.passed,
        "attempt": attempt,
        "at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "checks": result.checks,
    }
    frontmatter.write(md_path, meta, body)

    lines = result.render(card["id"]).split("\n")
    lines[0] += f"  (attempt {attempt} of {context.MAX_ATTEMPTS})"
    print("\n".join(lines))
    if result.passed:
        return True
    left = context.MAX_ATTEMPTS - attempt
    hint = f"fix the draft and propose again ({left} attempt{'s' if left != 1 else ''} left)" if left \
        else "no attempts left -- it stays In progress for the human"
    print(f"error: '{card['id']}' not proposed -- {hint}", file=sys.stderr)
    return False


@_with_vault_lock
def cmd_propose(args: argparse.Namespace) -> int:
    vault_root = _vault_root()
    try:
        data = operations._load_valid(vault_root)
    except canvas.HistosError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    card = canvas.find_card(data, args.id)
    if card is None:
        print(f"error: card '{args.id}' doesn't exist", file=sys.stderr)
        return 1
    if card["color"] != canvas.IN_PROGRESS:
        print(
            f"error: '{args.id}' isn't In progress (current state: {operations.STATE_NAMES[card['color']]}) "
            "-- use 'histos assign' first",
            file=sys.stderr,
        )
        return 1

    draft_path = Path(args.file)
    if not draft_path.exists():
        print(f"error: can't find the draft '{draft_path}'", file=sys.stderr)
        return 1
    draft = draft_path.read_text(encoding="utf-8")

    try:
        passed = _gate_on_evals(vault_root, data, card, draft)
    except canvas.HistosError as e:  # broken config/evals: not the draft's fault, no attempt spent
        print(f"error: {e}", file=sys.stderr)
        return 1
    if not passed:
        return 1

    proposal_path = operations._proposal_path(vault_root, args.id)
    proposal_path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(proposal_path, draft)

    card["color"] = canvas.PENDING_REVIEW
    canvas.save(vault_root, data)
    print(f"'{args.id}' -> Proposal pending review ('histos diff {args.id}' to review it)")
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    """Read-only dry run of what propose's evals would say about a draft."""
    vault_root = _vault_root()
    draft_path = Path(args.file)
    try:
        data = operations._load_valid(vault_root)
        card = canvas.find_card(data, args.id)
        if card is None:
            raise canvas.HistosError(f"card '{args.id}' doesn't exist")
        if not draft_path.exists():
            raise canvas.HistosError(f"can't find the draft '{draft_path}'")
        result = evals.run_all(vault_root, config.load(vault_root), data, card, draft_path.read_text(encoding="utf-8"))
    except canvas.HistosError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(result.render(args.id))
    return 0 if result.passed else 1


def cmd_diff(args: argparse.Namespace) -> int:
    vault_root = _vault_root()
    try:
        result = operations.get_diff(vault_root, args.id)
    except canvas.HistosError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if not args.no_evals:
        header = _stored_evals_summary(vault_root, args.id)
        if header:
            print(header + "\n")
    diff = difflib.unified_diff(
        result.current_body.splitlines(keepends=True),
        result.proposed_body.splitlines(keepends=True),
        fromfile=f"content/{args.id}.md (current)",
        tofile=f"proposal/{args.id}.md (proposed)",
    )
    sys.stdout.writelines(diff)
    return 0


def _stored_evals_summary(vault_root: Path, card_id: str) -> str | None:
    """What the evals said when this pending proposal was made -- written by that same propose,
    so it always matches. Not re-run: 'histos eval' does that."""
    card = canvas.find_card(canvas.load(vault_root), card_id)
    meta, _ = frontmatter.read(canvas.card_file_path(vault_root, card))
    stored = meta.get(operations.EVAL_RESULTS_KEY)
    if not isinstance(stored, dict) or "checks" not in stored:
        return None
    run = evals.EvalRun(passed=bool(stored.get("passed")), checks=stored["checks"], skipped=0)
    lines = run.render(card_id).split("\n")
    when = ""
    try:
        when = ", " + datetime.fromisoformat(str(stored["at"])).strftime("%Y-%m-%d %H:%M")
    except (KeyError, ValueError):
        pass
    lines[0] += f"  (attempt {stored.get('attempt', 1)} of {context.MAX_ATTEMPTS}{when})"
    return "\n".join(lines)


def cmd_approve(args: argparse.Namespace) -> int:
    vault_root = _vault_root()
    try:
        result = operations.approve(vault_root, args.id)
    except canvas.HistosError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"'{result.card_id}' -> Approved")
    return 0


def cmd_accept_edit(args: argparse.Namespace) -> int:
    vault_root = _vault_root()
    try:
        accepted = operations.accept_edit(vault_root, args.ids)
    except canvas.HistosError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    for card_id in accepted:
        print(f"'{card_id}': current content accepted as yours")
    return 0


def cmd_retry(args: argparse.Namespace) -> int:
    vault_root = _vault_root()
    try:
        operations.retry(vault_root, args.id)
    except canvas.HistosError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"'{args.id}': {context.MAX_ATTEMPTS} new attempts -- the last eval results were cleared")
    return 0


def cmd_reject(args: argparse.Namespace) -> int:
    vault_root = _vault_root()
    try:
        result = operations.reject(vault_root, args.id, feedback=args.feedback)
    except canvas.HistosError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"'{result.card_id}' -> Backlog (proposal discarded)")
    return 0
