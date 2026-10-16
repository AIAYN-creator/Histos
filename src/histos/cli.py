"""Histos CLI. Each subcommand is a cmd_xxx(args) -> int, testable without going through argparse."""
from __future__ import annotations

import argparse
import difflib
import functools
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from . import canvas, config, context, evals, frontmatter, guides, heuristics, lock, operations, tiers, validation
from .fsutil import write_text_atomic

_SETTINGS_PATH = ".claude/settings.json"
# Kept in sync with templates/.claude/settings.json -- see _sync_permissions_deny.
_BASE_DENY_RULES = [
    "Write(content/**)",
    "Edit(content/**)",
    "Write(project.canvas)",
    "Edit(project.canvas)",
    "Write(PROJECT.md)",
    "Edit(PROJECT.md)",
    "Write(ARCHITECTURE.md)",
    "Edit(ARCHITECTURE.md)",
    "Write(histos.config.yaml)",
    "Edit(histos.config.yaml)",
    "Write(evals/**)",
    "Edit(evals/**)",
]


def _permission_path(path: Path) -> str:
    """Converts an absolute filesystem path to Claude Code's `//...` absolute-path
    permission-rule syntax (settings.json permissions.deny). Windows paths are normalized
    to POSIX form with a lowercase drive segment, matching how Claude Code itself
    normalizes paths before matching (C:\\Users\\a -> /c/Users/a). Glob-special characters
    that could occur in a real file name ([, ], *, ?, !) are escaped so the rule matches
    this file literally instead of accidentally behaving as a pattern.
    """
    posix = path.resolve().as_posix()
    if len(posix) >= 2 and posix[1] == ":":
        posix = f"/{posix[0].lower()}{posix[2:]}"
    escaped = re.sub(r"([\[\]*?!])", r"\\\1", posix)
    return f"/{escaped}"


def _all_registered_sources(vault_root: Path, data: dict) -> list[Path]:
    paths = []
    for card in canvas.cards(data):
        md_path = canvas.card_file_path(vault_root, card)
        if not md_path.exists():
            continue
        meta, _ = frontmatter.read(md_path)
        for src in meta.get("sources") or []:
            paths.append(Path(src).expanduser())
    return paths


def _sync_permissions_deny(vault_root: Path, source_paths: list[Path]) -> None:
    """Best-effort, Claude-Code-specific hardening (see Trust model in the README): keeps
    .claude/settings.json's permissions.deny covering the base vault paths plus every
    currently registered source file, so the agent can't write to reference material
    either. Add-only -- never removes a rule, so a vault whose settings.json predates this
    (or one where the user added their own rules by hand) never gets silently rewritten
    out from under them. Does nothing if the vault has no .claude/settings.json at all
    (not a Claude Code vault, or the user deleted it on purpose).
    """
    settings_path = vault_root / _SETTINGS_PATH
    if not settings_path.exists():
        return
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return

    deny = settings.setdefault("permissions", {}).setdefault("deny", [])
    wanted = list(_BASE_DENY_RULES)
    for path in source_paths:
        rule = _permission_path(path)
        wanted += [f"Write({rule})", f"Edit({rule})"]

    changed = False
    for rule in wanted:
        if rule not in deny:
            deny.append(rule)
            changed = True

    if changed:
        try:
            write_text_atomic(settings_path, json.dumps(settings, indent=2, ensure_ascii=False) + "\n")
        except (OSError, canvas.HistosError):
            pass  # best-effort -- must never fail the describe command that triggered this


def _vault_root() -> Path:
    return Path.cwd()


def _with_vault_lock(func):
    """For the agent-side commands that load and save the vault themselves (the human-side
    ones lock inside operations.py, so the desktop app gets the same protection)."""
    @functools.wraps(func)
    def wrapper(args: argparse.Namespace) -> int:
        if getattr(args, "dry_run", False):  # writes nothing, so it waits for nobody
            return func(args)
        target = getattr(args, "id", None) or " ".join(getattr(args, "ids", None) or [])
        try:
            with lock.vault_lock(_vault_root(), f"{args.command} {target}".strip()):
                return func(args)
        except canvas.HistosError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
    return wrapper


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

    all_approved = all(by_id[dep]["color"] == canvas.APROBADA for dep in depends_on)
    initial_color = canvas.BACKLOG if (not depends_on or all_approved) else canvas.BLOQUEADA

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
        if card["color"] == canvas.BLOQUEADA:
            print(f"error: '{card_id}' is Blocked, can't be assigned yet", file=sys.stderr)
            return 1
        if card["color"] == canvas.EN_PROGRESO and not args.takeover:
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
        previous = meta.get(operations.WORKER_KEY) if card["color"] == canvas.EN_PROGRESO else None
        if previous is not None and previous == args.worker:
            print(f"'{card_id}' is already yours ({args.worker}) -- nothing to do")
            continue
        card["color"] = canvas.EN_PROGRESO
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
    if card["color"] != canvas.EN_PROGRESO:
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

    card["color"] = canvas.PROPUESTA_PENDIENTE
    canvas.save(vault_root, data)
    print(f"'{args.id}' -> Proposal pending review ('histos diff {args.id}' to review it)")
    return 0


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
        targets = [c for c in canvas.cards(data) if c["color"] != canvas.APROBADA]
    else:
        targets = []
        for card_id in args.ids:
            card = canvas.find_card(data, card_id)
            if card is None:
                print(f"error: card '{card_id}' doesn't exist", file=sys.stderr)
                return 1
            if card["color"] == canvas.APROBADA:
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
        if card["color"] == canvas.APROBADA:
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

    if card["color"] != canvas.EN_PROGRESO:
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

    open_cards = [c for g in result.groups if g.color != canvas.APROBADA for c in g.cards]
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="histos", description="Histos CLI (see docs/canvas-schema.md)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="creates project.canvas + content/ in the current directory")
    p.add_argument(
        "--add-config", action="store_true",
        help="existing vault: only add histos.config.yaml and evals/ if they're missing, touch nothing else",
    )
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("add-card", help="creates a new card")
    p.add_argument("id")
    p.add_argument("--title", required=True)
    p.add_argument("--description", default=None, help="one line, goes into the frontmatter and the initial body")
    p.add_argument("--depends-on", nargs="+", default=[], metavar="ID")
    p.add_argument(
        "--authorized", action="store_true",
        help="confirms a human authorized touching the dependency graph",
    )
    p.set_defaults(func=cmd_add_card)

    p = sub.add_parser("link", help="adds dependencies to an already existing card")
    p.add_argument("id")
    p.add_argument("--depends-on", nargs="+", required=True, metavar="ID")
    p.add_argument(
        "--authorized", action="store_true",
        help="confirms a human authorized touching the dependency graph",
    )
    p.set_defaults(func=cmd_link)

    p = sub.add_parser("assign", help="moves one or more cards to In progress")
    p.add_argument("ids", nargs="+", metavar="ID")
    p.add_argument("--by", choices=["agent", "human"], default="agent")
    p.add_argument("--worker", help="who works it, e.g. 'parent' or 'sub-cap3' -- tells parent and subagents apart")
    p.add_argument(
        "--takeover", action="store_true",
        help="take a card that's already In progress for someone else (e.g. its subagent died)",
    )
    p.set_defaults(func=cmd_assign)

    p = sub.add_parser("describe", help="updates description and/or sources (frontmatter) of an existing card")
    p.add_argument("id")
    p.add_argument("--text", default=None, help="one-line description")
    p.add_argument(
        "--sources", nargs="+", default=None, metavar="PATH",
        help="paths to external files (txt/md/tex/docx) relevant to this card -- replaces the previous list entirely",
    )
    p.set_defaults(func=cmd_describe)

    p = sub.add_parser("propose", help="uploads a content proposal for review")
    p.add_argument("id")
    p.add_argument("--file", required=True, help="path to the draft with the proposed content")
    p.set_defaults(func=cmd_propose)

    p = sub.add_parser("diff", help="shows the diff between the current content and the pending proposal")
    p.add_argument("id")
    p.add_argument("--no-evals", action="store_true", help="only the diff, without the evals summary on top")
    p.set_defaults(func=cmd_diff)

    p = sub.add_parser("classify", help="assigns a tier (fast/exec/reasoning) to cards from their context, with the reason")
    p.add_argument("ids", nargs="*", metavar="ID")
    p.add_argument("--all", action="store_true", help="every card that isn't Approved")
    p.add_argument("--dry-run", action="store_true", help="print tiers and a summary, write nothing")
    p.set_defaults(func=cmd_classify)

    p = sub.add_parser("model", help="records the concrete model for cards: --proposed (agent) or --approved (human's yes)")
    p.add_argument("ids", nargs="+", metavar="ID")
    p.add_argument("--proposed", metavar="MODEL", help="the model the agent proposes, e.g. 'Sonnet 5.5'")
    p.add_argument("--approved", metavar="MODEL", help="the model the human approved (may differ from the proposed one)")
    p.add_argument("--authorized", action="store_true", help="the human said yes to --approved in this conversation")
    p.add_argument("--clear", action="store_true", help="remove both models")
    p.set_defaults(func=cmd_model)

    p = sub.add_parser(
        "eval",
        help="runs the vault's evals on a draft without proposing it -- no state change, no attempt spent",
    )
    p.add_argument("id")
    p.add_argument("--file", required=True, help="the draft to check")
    p.set_defaults(func=cmd_eval)

    p = sub.add_parser("context", help="bundles description+approved dependencies+sources+PROJECT.md+ARCHITECTURE.md to work the card")
    p.add_argument("id")
    p.add_argument(
        "--for-subagent", action="store_true",
        help="self-contained brief for a subagent that starts from zero: adds AGENTS.md and what to do when done",
    )
    p.set_defaults(func=cmd_context)

    p = sub.add_parser("approve", help="applies the pending proposal to the real .md")
    p.add_argument("id")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("reject", help="discards the pending proposal")
    p.add_argument("id")
    p.add_argument("--feedback", default=None)
    p.set_defaults(func=cmd_reject)

    p = sub.add_parser(
        "retry",
        help=f"(human) give a card whose proposals failed the evals {context.MAX_ATTEMPTS} times a fresh set of attempts",
    )
    p.add_argument("id")
    p.set_defaults(func=cmd_retry)

    p = sub.add_parser(
        "accept-edit",
        help="(human) accept your own hand edits to content/<id>.md, so the content_untouched eval stops flagging them",
    )
    p.add_argument("ids", nargs="+", metavar="ID")
    p.set_defaults(func=cmd_accept_edit)

    p = sub.add_parser("status", help="lists cards grouped by status")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser(
        "guide",
        help="changes to PROJECT.md / ARCHITECTURE.md: the agent proposes, the human approves (like a card)",
    )
    guide_sub = p.add_subparsers(dest="guide_action", required=True)
    g = guide_sub.add_parser("propose", help="the agent proposes a new version of the file")
    g.add_argument("name", help="PROJECT.md or ARCHITECTURE.md")
    g.add_argument("--file", required=True, help="draft with the whole new file")
    g = guide_sub.add_parser("diff", help="what the pending change would change")
    g.add_argument("name", help="PROJECT.md or ARCHITECTURE.md")
    g = guide_sub.add_parser("approve", help="(human) write the pending change; the old version is kept in approved/guides/")
    g.add_argument("name", help="PROJECT.md or ARCHITECTURE.md")
    g = guide_sub.add_parser("reject", help="(human) discard the pending change")
    g.add_argument("name", help="PROJECT.md or ARCHITECTURE.md")
    g.add_argument("--feedback", help="why -- the agent sees it in 'histos context'")
    p.set_defaults(func=cmd_guide)

    p = sub.add_parser("validate", help="validates project.canvas against the formal schema")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser(
        "arrange",
        help="re-lays out the whole board, moving every card -- only when the human asks for it",
    )
    p.add_argument(
        "--set-aside-done", action=argparse.BooleanOptionalAction, default=True,
        help="moves approved cards with nothing pending depending on them into a separate 'Done' group (default: on)",
    )
    p.add_argument(
        "--prune-redundant", action=argparse.BooleanOptionalAction, default=True,
        help="removes arrows already implied by a longer path, keeping them in the card's "
             "implied_dependencies so 'context' still sees them (default: on)",
    )
    p.set_defaults(func=cmd_arrange)

    return parser


def main(argv: list[str] | None = None) -> int:
    # Card content is arbitrary UTF-8, but Windows encodes piped output (how agents read it) as cp1252.
    if (sys.stdout.encoding or "").lower().replace("-", "") != "utf8" and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
