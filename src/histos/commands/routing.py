"""v3 routing and context: classify, model, context (and --for-subagent), guide."""
from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

from .. import canvas, config, context, frontmatter, guides, heuristics, operations, tiers
from ._shared import _vault_root, _with_vault_lock


@_with_vault_lock
def cmd_classify(args: argparse.Namespace) -> int:
    """Classifies cards into a tier from their context (never a draft) and writes proposed_tier."""
    vault_root = _vault_root()
    if bool(args.ids) == args.all:
        print("error: pass card ids or --all (not both)", file=sys.stderr)
        return 1
    try:
        cfg = config.load(vault_root)
        if cfg is None:
            raise canvas.HistosError(f"this vault has no {config.CONFIG_FILENAME} -- classify needs one")
        data = operations._load_valid(vault_root)
    except canvas.HistosError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if args.all:
        targets = [c for c in canvas.cards(data) if c["color"] != canvas.APPROVED]
    else:
        targets = []
        for card_id in args.ids:
            card = canvas.find_card(data, card_id)
            if card is None:
                print(f"error: card '{card_id}' doesn't exist", file=sys.stderr)
                return 1
            if card["color"] == canvas.APPROVED:
                print(f"error: '{card_id}' is already Approved -- nothing to classify", file=sys.stderr)
                return 1
            targets.append(card)

    counts = {name: 0 for name in tiers.PRECEDENCE}
    defaults = 0
    for card in targets:
        result = tiers.classify(cfg, heuristics.gather(vault_root, data, card))
        counts[result.tier] += 1
        defaults += result.by_default
        print(result.render(card["id"]))
        if args.dry_run:
            continue
        md_path = canvas.card_file_path(vault_root, card)
        meta, body = frontmatter.read(md_path)
        previous = meta.get("proposed_tier")
        if previous and previous != result.tier:
            cleared = [f"{k} '{meta.pop(k)}'" for k in ("proposed_model", "approved_model") if k in meta]
            if cleared:
                print(
                    f"warning: {card['id']}: tier changed {previous} -> {result.tier}, cleared "
                    f"{' and '.join(cleared)} -- propose a model again",
                    file=sys.stderr,
                )
        meta["proposed_tier"] = result.tier
        frontmatter.write(md_path, meta, body)

    if len(targets) > 1 or args.dry_run:
        summary = " · ".join(f"{name} {counts[name]}" for name in ("fast", "exec", "reasoning"))
        print(f"\n{summary}" + (f" ({defaults} by default)" if defaults else "")
              + (" -- dry run, nothing written" if args.dry_run else ""))
    return 0


@_with_vault_lock
def cmd_model(args: argparse.Namespace) -> int:
    """Records the concrete model for cards: proposed by the agent, approved by the human."""
    vault_root = _vault_root()
    chosen = [flag for flag in ("proposed", "approved") if getattr(args, flag)] + (["clear"] if args.clear else [])
    if len(chosen) != 1:
        print("error: pass exactly one of --proposed, --approved or --clear", file=sys.stderr)
        return 1
    if args.approved and not args.authorized:
        print(
            "error: --approved records the human's choice and needs explicit authorization -- "
            "only add --authorized if they said yes to this model in the conversation",
            file=sys.stderr,
        )
        return 1
    try:
        data = operations._load_valid(vault_root)
    except canvas.HistosError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    cards = []
    for card_id in args.ids:
        card = canvas.find_card(data, card_id)
        if card is None:
            print(f"error: card '{card_id}' doesn't exist", file=sys.stderr)
            return 1
        if card["color"] == canvas.APPROVED:
            print(f"error: '{card_id}' is already Approved -- its model stays as a record", file=sys.stderr)
            return 1
        cards.append(card)

    for card in cards:
        md_path = canvas.card_file_path(vault_root, card)
        meta, body = frontmatter.read(md_path)
        if args.clear:
            meta.pop("proposed_model", None)
            meta.pop("approved_model", None)
        else:
            if not meta.get("proposed_tier"):
                print(f"warning: {card['id']} has no proposed_tier -- run 'histos classify {card['id']}' first?",
                      file=sys.stderr)
            meta["proposed_model" if args.proposed else "approved_model"] = args.proposed or args.approved
        frontmatter.write(md_path, meta, body)
        if args.clear:
            print(f"{card['id']}: models cleared")
            continue
        notes = [f"{k}: {meta[v]}" for k, v in (("proposed", "proposed_model"), ("tier", "proposed_tier"))
                 if args.approved and meta.get(v)]
        field_name = "proposed_model" if args.proposed else "approved_model"
        print(f"{card['id']}: {field_name} = {meta[field_name]}" + (f" ({', '.join(notes)})" if notes else ""))
    return 0


def cmd_context(args: argparse.Namespace) -> int:
    """Bundles description+approved content+sources for the card and its dependencies,
    the feedback from a rejected proposal of the card itself, and PROJECT.md/ARCHITECTURE.md
    if they exist -- everything an agent needs to start working the card without gathering it by hand.
    """
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

    if not args.for_subagent:
        print(context.build(vault_root, data, card))
        return 0

    if card["color"] != canvas.IN_PROGRESS:
        print(
            f"error: '{args.id}' isn't In progress (current state: {operations.STATE_NAMES[card['color']]}) "
            "-- assign it before handing it to a subagent",
            file=sys.stderr,
        )
        return 1
    brief = context.build_for_subagent(vault_root, data, card)
    if brief.approved_model is None:
        print(f"warning: '{args.id}' has no approved_model recorded", file=sys.stderr)
    print(brief.text)
    return 0


def cmd_guide(args: argparse.Namespace) -> int:
    """PROJECT.md / ARCHITECTURE.md changes: the agent proposes, the human approves."""
    vault_root = _vault_root()
    try:
        if args.guide_action == "propose":
            draft = Path(args.file)
            if not draft.exists():
                print(f"error: can't find the draft '{draft}'", file=sys.stderr)
                return 1
            result = guides.propose(vault_root, args.name, draft.read_text(encoding="utf-8"))
            if result.replaced_pending:
                print(f"warning: replaced the change to {result.name} that was already pending", file=sys.stderr)
            print(f"{result.name}: change pending review ('histos guide diff {result.name}' to review it)")
        elif args.guide_action == "diff":
            result = guides.get_diff(vault_root, args.name)
            sys.stdout.writelines(difflib.unified_diff(
                result.current.splitlines(keepends=True),
                result.proposed.splitlines(keepends=True),
                fromfile=f"{result.name} (current)",
                tofile=f"{result.name} (proposed)",
            ))
        elif args.guide_action == "approve":
            result = guides.approve(vault_root, args.name)
            kept = f" -- previous version kept in {result.archived_previous.relative_to(vault_root).as_posix()}" \
                if result.archived_previous else ""
            print(f"{result.name}: updated{kept}")
        else:
            name = guides.reject(vault_root, args.name, args.feedback)
            print(f"{name}: change discarded")
    except canvas.HistosError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0
