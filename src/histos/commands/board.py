"""Commands that shape the board: init, add-card, link, assign, describe, status, arrange, validate."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .. import canvas, config, evals, frontmatter, guides, operations, validation
from ._shared import _all_registered_sources, _sync_permissions_deny, _vault_root, _with_vault_lock


def cmd_init(args: argparse.Namespace) -> int:
    vault_root = _vault_root()
    if args.add_config:
        try:
            warnings = operations.add_config(vault_root)
        except canvas.HistosError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        for warning in warnings:
            print(f"warning: {warning}", file=sys.stderr)
        try:
            sources = _all_registered_sources(vault_root, operations._load_valid(vault_root))
        except canvas.HistosError:
            sources = []  # the deny rules for config/evals don't depend on a valid canvas
        _sync_permissions_deny(vault_root, sources)
        print("v3 config checked: histos.config.yaml and evals/ are in place")
        return 0
    try:
        result = operations.init_vault(vault_root)
    except canvas.HistosError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    for warning in result.template_warnings:
        print(f"warning: {warning}", file=sys.stderr)
    print(f"vault initialized at {result.vault_root}")
    return 0


@_with_vault_lock
def cmd_add_card(args: argparse.Namespace) -> int:
    if not canvas.is_valid_card_id(args.id):
        print(
            f"error: id '{args.id}' is not valid -- only letters, numbers, '_' and '-' "
            "(used as-is as the file name; can't contain '/', '\\', or '..')",
            file=sys.stderr,
        )
        return 1

    vault_root = _vault_root()
    try:
        data = operations._load_valid(vault_root)
    except canvas.HistosError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if canvas.find_card(data, args.id) is not None:
        print(f"error: a card with id '{args.id}' already exists", file=sys.stderr)
        return 1

    depends_on = args.depends_on or []
    if depends_on and not args.authorized:
        print(
            "error: --depends-on touches the dependency graph and needs explicit human "
            "authorization -- only add --authorized if you already got it in the conversation",
            file=sys.stderr,
        )
        return 1

    by_id = {c["id"]: c for c in canvas.cards(data)}
    for dep in depends_on:
        if dep not in by_id:
            print(f"error: dependency '{dep}' doesn't exist", file=sys.stderr)
            return 1

    all_approved = all(by_id[dep]["color"] == canvas.APPROVED for dep in depends_on)
    initial_color = canvas.BACKLOG if (not depends_on or all_approved) else canvas.BLOCKED

    width, height = canvas.estimate_card_size(args.description)
    node = canvas.add_card_node(data, args.id, initial_color, width=width, height=height)
    for dep in depends_on:
        canvas.add_edge(data, dep, args.id)

    cycle = canvas.detect_cycle(data)
    if cycle:
        print(f"error: that dependency would form a cycle: {' -> '.join(cycle)}", file=sys.stderr)
        return 1

    md_path = canvas.card_file_path(vault_root, node)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    meta = dict(frontmatter.DEFAULT_META)
    body = f"# {args.title}\n\n"
    if args.description:
        meta["description"] = args.description
        body += f"{args.description}\n\n"
    meta[frontmatter.CONTENT_HASH_KEY] = frontmatter.body_hash(body)
    frontmatter.write(md_path, meta, body)

    canvas.place_new_card(data, node)
    canvas.save(vault_root, data)

    print(f"card '{args.id}' created ({operations.STATE_NAMES[initial_color]}) -> {node['file']}")
    return 0


@_with_vault_lock
def cmd_link(args: argparse.Namespace) -> int:
    """Adds dependencies to an ALREADY existing card -- add-card --depends-on only covers
    creation; this covers discovering a dependency after the card was already created.
    """
    vault_root = _vault_root()
    try:
        data = operations._load_valid(vault_root)
    except canvas.HistosError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if canvas.find_card(data, args.id) is None:
        print(f"error: card '{args.id}' doesn't exist", file=sys.stderr)
        return 1

    if not args.authorized:
        print(
            "error: 'link' touches the dependency graph and needs explicit human "
            "authorization -- only add --authorized if you already got it in the conversation",
            file=sys.stderr,
        )
        return 1

    by_id = {c["id"]: c for c in canvas.cards(data)}
    for dep in args.depends_on:
        if dep == args.id:
            print(f"error: '{args.id}' can't depend on itself", file=sys.stderr)
            return 1
        if dep not in by_id:
            print(f"error: dependency '{dep}' doesn't exist", file=sys.stderr)
            return 1

    for dep in args.depends_on:
        canvas.add_edge(data, dep, args.id)

    cycle = canvas.detect_cycle(data)
    if cycle:
        print(f"error: that dependency would form a cycle: {' -> '.join(cycle)}", file=sys.stderr)
        return 1

    canvas.recompute_blocked(data)
    canvas.save(vault_root, data)
    print(f"'{args.id}' now depends on: {', '.join(args.depends_on)}")
    return 0


@_with_vault_lock
def cmd_assign(args: argparse.Namespace) -> int:
    vault_root = _vault_root()
    try:
        data = operations._load_valid(vault_root)
    except canvas.HistosError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    canvas.recompute_blocked(data)

    for card_id in args.ids:
        card = canvas.find_card(data, card_id)
        if card is None:
            print(f"error: card '{card_id}' doesn't exist", file=sys.stderr)
            return 1
        if card["color"] == canvas.BLOCKED:
            print(f"error: '{card_id}' is Blocked, can't be assigned yet", file=sys.stderr)
            return 1
        if card["color"] == canvas.IN_PROGRESS and not args.takeover:
            holder = frontmatter.read(canvas.card_file_path(vault_root, card))[0].get(operations.WORKER_KEY)
            if args.worker is None or holder != args.worker:
                who = f"worker: {holder}" if holder else "no worker recorded"
                print(
                    f"error: '{card_id}' is already In progress ({who}) -- "
                    "'--takeover' if it's really free (e.g. its subagent died)",
                    file=sys.stderr,
                )
                return 1

    # Checked and written under the same vault lock: of two simultaneous assigns, exactly one wins.
    for card_id in args.ids:
        card = canvas.find_card(data, card_id)
        md_path = canvas.card_file_path(vault_root, card)
        meta, body = frontmatter.read(md_path)
        previous = meta.get(operations.WORKER_KEY) if card["color"] == canvas.IN_PROGRESS else None
        if previous is not None and previous == args.worker:
            print(f"'{card_id}' is already yours ({args.worker}) -- nothing to do")
            continue
        card["color"] = canvas.IN_PROGRESS
        meta["assigned_to"] = args.by
        if args.worker:
            meta[operations.WORKER_KEY] = args.worker
        else:
            meta.pop(operations.WORKER_KEY, None)
        frontmatter.write(md_path, meta, body)
        worker = f", worker={args.worker}" if args.worker else ""
        taken = f" -- taken over from {previous}" if previous else ""
        print(f"'{card_id}' -> In progress (assigned_to={args.by}{worker}){taken}")

    canvas.save(vault_root, data)
    return 0


@_with_vault_lock
def cmd_describe(args: argparse.Namespace) -> int:
    """Updates description and/or sources in the frontmatter -- never touches the body, so
    it doesn't need to go through propose/approve (it's metadata, like assigned_to in 'assign').
    """
    if args.text is None and args.sources is None:
        print("error: pass --text, --sources, or both", file=sys.stderr)
        return 1

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

    md_path = canvas.card_file_path(vault_root, card)
    if not md_path.exists():
        print(f"error: can't find {md_path}", file=sys.stderr)
        return 1

    if args.sources is not None:
        missing = [s for s in args.sources if not Path(s).expanduser().exists()]
        if missing:
            print(f"error: can't find these source files: {', '.join(missing)}", file=sys.stderr)
            return 1

    meta, body = frontmatter.read(md_path)
    if args.text is not None:
        meta["description"] = args.text
    if args.sources is not None:
        meta["sources"] = args.sources
    frontmatter.write(md_path, meta, body)

    card["width"], card["height"] = canvas.estimate_card_size(meta.get("description"))
    canvas.save(vault_root, data)
    _sync_permissions_deny(vault_root, _all_registered_sources(vault_root, data))

    print(f"'{args.id}': updated")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    vault_root = _vault_root()
    try:
        result = operations.get_status(vault_root)
    except canvas.HistosError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    for group in result.groups:
        print(f"{group.label} ({len(group.cards)})")
        for c in group.cards:
            desc = f"  -- {c.description}" if c.description else ""
            worker = f" [{c.worker}]" if c.worker else ""
            routing = _routing_label(c)
            routing = f"  {{{routing}}}" if routing else ""
            print(f"  - {c.id}{worker}  ({c.file}){routing}{desc}")

    open_cards = [c for g in result.groups if g.color != canvas.APPROVED for c in g.cards]
    if any(c.tier for c in open_cards):
        counts = {t: sum(c.tier == t for c in open_cards) for t in ("fast", "exec", "reasoning")}
        unclassified = sum(c.tier is None for c in open_cards)
        print("Tiers: " + " · ".join(f"{t} {n}" for t, n in counts.items()) + f" · unclassified {unclassified}")
    awaiting = [c for c in open_cards if c.proposed_model and not c.approved_model]
    if awaiting:
        listed = ", ".join(f"{c.id} ({c.tier + ', ' if c.tier else ''}proposed {c.proposed_model})" for c in awaiting)
        print(f"Waiting for your model approval: {listed}")
    waiting = guides.pending(vault_root)
    if waiting:
        print(f"Guides pending review: {', '.join(waiting)}  ('histos guide diff <file>' to review)")
    return 0


def _routing_label(c: operations.CardSummary) -> str:
    """'exec · Sonnet 5.5', 'reasoning · proposed Opus 5.5?' (no yes from the human yet), 'fast', or ''."""
    parts = [c.tier] if c.tier else []
    if c.approved_model:
        parts.append(c.approved_model)
    elif c.proposed_model:
        parts.append(f"proposed {c.proposed_model}?")
    return " · ".join(parts)


def cmd_arrange(args: argparse.Namespace) -> int:
    vault_root = _vault_root()
    try:
        result = operations.arrange(
            vault_root, set_aside_done=args.set_aside_done, prune_redundant=args.prune_redundant,
        )
    except canvas.HistosError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    details = [f"{result.unconnected} not connected"]
    if args.set_aside_done:
        details.append(f"{result.set_aside_done} set aside as done")
    if args.prune_redundant:
        details.append(f"{result.pruned} redundant {'arrow' if result.pruned == 1 else 'arrows'} removed")
    print(f"board rearranged: {result.cards} cards in {result.columns} columns ({', '.join(details)})")
    print(f"previous layout saved to {result.backup_path.name} -- reload the canvas in Obsidian (Ctrl+R) to see it")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    vault_root = _vault_root()
    try:
        data = canvas.load(vault_root)
    except canvas.HistosError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    errors = validation.validate_all(data)
    if not errors:  # card paths are only trustworthy once the canvas itself is valid
        errors += validation.validate_frontmatter(vault_root, data)
    config_errors = config.validate(vault_root)
    errors += [f"config: {e}" for e in config_errors]
    if not config_errors:  # where evals/ lives comes from the config
        errors += [f"evals: {e}" for e in evals.validate(vault_root, config.load(vault_root))]
    if errors:
        for e in errors:
            print(f"  - {e}")
        return 1
    print("OK")
    return 0
