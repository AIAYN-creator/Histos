"""v3 routing and context: classify, model, context (and --for-subagent), guide."""
from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

from .. import canvas, config, context, evals, events, frontmatter, guides, heuristics, operations, tiers, undo
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
    if not args.dry_run:
        open_paths = [canvas.card_file_path(vault_root, c) for c in canvas.cards(data) if c["color"] != canvas.APPROVED]
        undo.stage(vault_root, [canvas.vault_canvas_path(vault_root), *open_paths])
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
        cleared = []
        cleared_executor = None
        if previous and previous != result.tier:
            cleared = [f"{k} '{meta.pop(k)}'" for k in ("proposed_model", "approved_model") if k in meta]
            prev_exec = meta.get("approved_executor") or meta.get("proposed_executor")
            exec_cleared = [f"{k} '{meta.pop(k)}'" for k in ("proposed_executor", "approved_executor") if k in meta]
            if exec_cleared:
                cleared_executor = prev_exec
                cleared.extend(exec_cleared)
            if cleared:
                print(
                    f"warning: {card['id']}: tier changed {previous} -> {result.tier}, cleared "
                    f"{' and '.join(cleared)} -- propose a model again",
                    file=sys.stderr,
                )
        meta["proposed_tier"] = result.tier
        frontmatter.write(md_path, meta, body)
        events.emit("classified", card=card["id"], tier=result.tier, previous_tier=previous,
                    model_cleared=bool(cleared))
        if cleared_executor:
            events.emit("executor_cleared", card=card["id"], executor=cleared_executor, reason="reclassified")
    if not args.dry_run:
        operations.refresh_estimates(vault_root, data)  # the tier changes which cards it is compared with

    if len(targets) > 1 or args.dry_run:
        summary = " · ".join(f"{name} {counts[name]}" for name in ("fast", "exec", "reasoning"))
        print(f"\n{summary}" + (f" ({defaults} by default)" if defaults else "")
              + (" -- dry run, nothing written" if args.dry_run else ""))
    return 0


def _resolve_model_cmd_inputs(args: argparse.Namespace) -> tuple[str | None, str | None, str | None, str | None, bool, bool]:
    """Validates mutually-exclusive flag combinations for `cmd_model` and returns
    `(proposed_model, approved_model, proposed_exec, approved_exec, clear_all, clear_exec)`."""
    prop_m = getattr(args, "proposed", None)
    appr_m = getattr(args, "approved", None)
    exec_pair = getattr(args, "executor", None)
    prop_e = getattr(args, "proposed_executor", None)
    appr_e = getattr(args, "approved_executor", None)
    clear_all = bool(getattr(args, "clear", False))
    clear_exec = bool(getattr(args, "clear_executor", False))

    if clear_all and any([prop_m, appr_m, exec_pair, prop_e, appr_e, clear_exec]):
        raise canvas.HistosError("--clear cannot be combined with other flags")
    if clear_exec and any([prop_m, appr_m, exec_pair, prop_e, appr_e]):
        raise canvas.HistosError("--clear-executor cannot be combined with propose/approve flags")
    if prop_m and appr_m:
        raise canvas.HistosError("pass at most one of --proposed or --approved")
    if prop_e and appr_e:
        raise canvas.HistosError("pass at most one of --proposed-executor or --approved-executor")
    if exec_pair:
        if prop_e or appr_e:
            raise canvas.HistosError("pass either --executor with --proposed/--approved, or --proposed-executor/--approved-executor")
        if prop_m:
            prop_e = exec_pair
        elif appr_m:
            appr_e = exec_pair
        else:
            raise canvas.HistosError("--executor must be paired with --proposed or --approved (or use --proposed-executor / --approved-executor)")
    if not any([prop_m, appr_m, prop_e, appr_e, clear_all, clear_exec]):
        raise canvas.HistosError("pass exactly one of --proposed, --approved, --proposed-executor, --approved-executor, --clear, or --clear-executor")
    return prop_m, appr_m, prop_e, appr_e, clear_all, clear_exec


@_with_vault_lock
def cmd_model(args: argparse.Namespace) -> int:
    """Records the concrete model and/or opt-in executor for cards."""
    vault_root = _vault_root()
    try:
        prop_m, appr_m, prop_e, appr_e, clear_all, clear_exec = _resolve_model_cmd_inputs(args)
    except canvas.HistosError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if (appr_m or appr_e) and not args.authorized:
        print(
            "error: --approved / --approved-executor records the human's choice and needs explicit authorization -- "
            "only add --authorized if they said yes in the conversation",
            file=sys.stderr,
        )
        return 1

    try:
        data = operations._load_valid(vault_root)
        chosen_exec = prop_e or appr_e
        exec_cfg = None
        if chosen_exec is not None:
            cfg = config.load(vault_root)
            if cfg is None or not cfg.executors.enabled:
                raise canvas.HistosError(
                    "executors are disabled in this vault -- enable 'executors.enabled: true' "
                    f"and list allowed entries in {config.CONFIG_FILENAME}"
                )
            exec_cfg = cfg.executors.find(chosen_exec)
            if exec_cfg is None:
                allowed = ", ".join(entry.name for entry in cfg.executors.allowed) or "(none)"
                raise canvas.HistosError(f"executor '{chosen_exec}' is not in executors.allowed ({allowed})")
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
            # The human often says yes and approves the card in one go, so the card can turn green
            # before the agent records that yes: --approved / --approved-executor may fill empty
            # fields, but never overwrite already recorded values.
            meta, _ = frontmatter.read(canvas.card_file_path(vault_root, card))
            if clear_all or clear_exec or prop_m or prop_e:
                print(f"error: '{card_id}' is already Approved -- its model/executor stays as a record", file=sys.stderr)
                return 1
            if (appr_m and meta.get("approved_model")) or (appr_e and meta.get("approved_executor")):
                print(f"error: '{card_id}' is already Approved -- its model/executor stays as a record", file=sys.stderr)
                return 1
        cards.append(card)

    if exec_cfg is not None and exec_cfg.pricing_tier == "free":
        print(
            f"warning: executor '{exec_cfg.name}' uses a free pricing tier -- "
            "providers may use data for training; do not use it in private vaults.",
            file=sys.stderr,
        )

    undo.stage(
        vault_root,
        [canvas.vault_canvas_path(vault_root), *(canvas.card_file_path(vault_root, c) for c in cards)],
    )
    for card in cards:
        md_path = canvas.card_file_path(vault_root, card)
        meta, body = frontmatter.read(md_path)
        if clear_all:
            meta.pop("proposed_model", None)
            meta.pop("approved_model", None)
            prev_exec = meta.get("approved_executor") or meta.get("proposed_executor")
            meta.pop("proposed_executor", None)
            meta.pop("approved_executor", None)
            frontmatter.write(md_path, meta, body)
            events.emit("model_cleared", card=card["id"])
            if prev_exec is not None:
                events.emit("executor_cleared", card=card["id"], executor=prev_exec)
            print(f"{card['id']}: models cleared")
            continue
        if clear_exec:
            prev_exec = meta.get("approved_executor") or meta.get("proposed_executor")
            meta.pop("proposed_executor", None)
            meta.pop("approved_executor", None)
            frontmatter.write(md_path, meta, body)
            events.emit("executor_cleared", card=card["id"], executor=prev_exec)
            print(f"{card['id']}: executors cleared")
            continue

        if not meta.get("proposed_tier"):
            print(f"warning: {card['id']} has no proposed_tier -- run 'histos classify {card['id']}' first?",
                  file=sys.stderr)
        outputs: list[str] = []
        if prop_m or appr_m:
            field_name = "proposed_model" if prop_m else "approved_model"
            meta[field_name] = prop_m or appr_m
            events.emit("model_proposed" if prop_m else "model_approved", card=card["id"], model=meta[field_name])
            notes = [f"{k}: {meta[v]}" for k, v in (("proposed", "proposed_model"), ("tier", "proposed_tier"))
                     if appr_m and meta.get(v)]
            outputs.append(f"{field_name} = {meta[field_name]}" + (f" ({', '.join(notes)})" if notes else ""))
        if exec_cfg is not None:
            ex_field = "proposed_executor" if prop_e else "approved_executor"
            meta[ex_field] = exec_cfg.name
            events.emit("executor_proposed" if prop_e else "executor_approved", card=card["id"], executor=exec_cfg.name)
            outputs.append(f"{ex_field} = {exec_cfg.name}")
        frontmatter.write(md_path, meta, body)
        print(f"{card['id']}: {', '.join(outputs)}")
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
            if result.evals is not None:
                print(result.evals.render(result.name))
            if not result.saved:
                print(f"error: the change to {result.name} wasn't proposed -- fix the draft and propose again",
                      file=sys.stderr)
                return 1
            if result.replaced_pending:
                print(f"warning: replaced the change to {result.name} that was already pending", file=sys.stderr)
            print(f"{result.name}: change pending review ('histos guide diff {result.name}' to review it)")
        elif args.guide_action == "diff":
            result = guides.get_diff(vault_root, args.name)
            stored = guides.stored_evals(vault_root, result.name)
            if stored and isinstance(stored.get("checks"), list):
                run = evals.EvalRun(passed=bool(stored.get("passed")), checks=stored["checks"], skipped=0)
                print(run.render(result.name) + "\n")
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


@_with_vault_lock
def cmd_implement(args: argparse.Namespace) -> int:
    """The implementation of an approved card -- in a code project, the work that happens after
    the proposal (the spec) is approved. Only recorded: its time counts as the card's agent time."""
    vault_root = _vault_root()
    data = operations._load_valid(vault_root)
    card = canvas.find_card(data, args.id)
    if card is None:
        print(f"error: card '{args.id}' doesn't exist", file=sys.stderr)
        return 1
    if card["color"] != canvas.APPROVED:
        print(f"error: '{args.id}' isn't Approved -- implement the approved proposal, not a draft",
              file=sys.stderr)
        return 1
    undo.stage(vault_root, [])
    if args.done:
        events.emit("implementation_done", card=args.id)
        print(f"{args.id}: implementation done, its time recorded")
    else:
        events.emit("implementation_started", card=args.id)
        print(f"{args.id}: implementation started -- run `histos implement {args.id} --done` when it's finished")
    return 0
