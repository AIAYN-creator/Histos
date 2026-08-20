"""Histos CLI. Each subcommand is a cmd_xxx(args) -> int, testable without going through argparse."""
from __future__ import annotations

import argparse
import difflib
import sys
from importlib import resources
from pathlib import Path

from . import canvas, frontmatter, validation

STATE_NAMES = {
    canvas.BLOQUEADA: "Blocked",
    canvas.EN_PROGRESO: "In progress",
    canvas.PROPUESTA_PENDIENTE: "Proposal pending review",
    canvas.APROBADA: "Approved",
    canvas.SOLICITUD_CAMBIO_DEPENDENCIA: "Dependency change request",
    canvas.BACKLOG: "Backlog",
}

PROPOSALS_DIR = "proposals"
APPROVED_DIR = "approved"
# Vaults created before this English rename have these on disk instead. Never created anew --
# only used to keep an existing vault working with the folder names it already has.
_LEGACY_PROPOSALS_DIR = "propuestas"
_LEGACY_APPROVED_DIR = "aprobados"
AGENT_TEMPLATES = ["AGENTS.md", "CLAUDE.md", ".claude/settings.json"]


def _install_agent_templates(vault_root: Path) -> None:
    for rel_path in AGENT_TEMPLATES:
        target = vault_root / rel_path
        if target.exists():
            print(f"warning: {rel_path} already exists, leaving it alone -- copy the reference content by hand if you want it", file=sys.stderr)
            continue
        ref = resources.files("histos").joinpath("templates", *rel_path.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(ref.read_text(encoding="utf-8"), encoding="utf-8")


def _vault_root() -> Path:
    return Path.cwd()


def _existing_dirname(vault_root: Path, current: str, legacy: str) -> str:
    """A vault only has the legacy folder if it was created before the rename -- new vaults
    never do, so this always resolves to `current` for them. Keeps old vaults on their
    original folder instead of fragmenting into a second, differently-named one.
    """
    if (vault_root / legacy).is_dir() and not (vault_root / current).is_dir():
        return legacy
    return current


def _proposal_path(vault_root: Path, card_id: str) -> Path:
    dirname = _existing_dirname(vault_root, PROPOSALS_DIR, _LEGACY_PROPOSALS_DIR)
    return vault_root / dirname / f"{card_id}.md"


def _approved_path(vault_root: Path, card_id: str) -> Path:
    dirname = _existing_dirname(vault_root, APPROVED_DIR, _LEGACY_APPROVED_DIR)
    return vault_root / dirname / f"{card_id}.md"


def _load_valid(vault_root: Path) -> dict:
    data = canvas.load(vault_root)
    errors = validation.validate_all(data)
    if errors:
        raise canvas.HistosError(
            "the canvas is not valid -- fix it before continuing ('histos validate' for detail):\n"
            + "\n".join(f"  - {e}" for e in errors)
        )
    return data


def _resync_sizes_and_layout(vault_root: Path, data: dict) -> None:
    """Recomputes size (from each card's real description) and position (by dependency
    rank). Called after any change that affects the graph or a description.
    """
    for card in canvas.cards(data):
        md_path = canvas.card_file_path(vault_root, card)
        if md_path.exists():
            meta, _ = frontmatter.read(md_path)
            card["width"], card["height"] = canvas.estimate_card_size(meta.get("description"))
    canvas.relayout(data)


def cmd_init(args: argparse.Namespace) -> int:
    vault_root = _vault_root()
    canvas_path = canvas.vault_canvas_path(vault_root)
    if canvas_path.exists():
        print(f"error: {canvas_path} already exists", file=sys.stderr)
        return 1
    (vault_root / "content").mkdir(parents=True, exist_ok=True)
    (vault_root / PROPOSALS_DIR).mkdir(parents=True, exist_ok=True)
    (vault_root / APPROVED_DIR).mkdir(parents=True, exist_ok=True)
    canvas.save(vault_root, {"nodes": [canvas.build_legend_node()], "edges": []})
    _install_agent_templates(vault_root)
    print(f"vault initialized at {vault_root}")
    return 0


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
        data = _load_valid(vault_root)
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
    frontmatter.write(md_path, meta, body)

    _resync_sizes_and_layout(vault_root, data)
    canvas.save(vault_root, data)

    print(f"card '{args.id}' created ({STATE_NAMES[initial_color]}) -> {node['file']}")
    return 0


def cmd_link(args: argparse.Namespace) -> int:
    """Adds dependencies to an ALREADY existing card -- add-card --depends-on only covers
    creation; this covers discovering a dependency after the card was already created.
    """
    vault_root = _vault_root()
    try:
        data = _load_valid(vault_root)
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
    _resync_sizes_and_layout(vault_root, data)
    canvas.save(vault_root, data)
    print(f"'{args.id}' now depends on: {', '.join(args.depends_on)}")
    return 0


def cmd_assign(args: argparse.Namespace) -> int:
    vault_root = _vault_root()
    try:
        data = _load_valid(vault_root)
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

    for card_id in args.ids:
        card = canvas.find_card(data, card_id)
        card["color"] = canvas.EN_PROGRESO
        md_path = canvas.card_file_path(vault_root, card)
        meta, body = frontmatter.read(md_path)
        meta["assigned_to"] = args.by
        frontmatter.write(md_path, meta, body)
        print(f"'{card_id}' -> In progress (assigned_to={args.by})")

    canvas.save(vault_root, data)
    return 0


def cmd_describe(args: argparse.Namespace) -> int:
    """Updates description and/or sources in the frontmatter -- never touches the body, so
    it doesn't need to go through propose/approve (it's metadata, like assigned_to in 'assign').
    """
    if args.text is None and args.sources is None:
        print("error: pass --text, --sources, or both", file=sys.stderr)
        return 1

    vault_root = _vault_root()
    try:
        data = _load_valid(vault_root)
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

    _resync_sizes_and_layout(vault_root, data)
    canvas.save(vault_root, data)

    print(f"'{args.id}': updated")
    return 0


def cmd_propose(args: argparse.Namespace) -> int:
    vault_root = _vault_root()
    try:
        data = _load_valid(vault_root)
    except canvas.HistosError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    card = canvas.find_card(data, args.id)
    if card is None:
        print(f"error: card '{args.id}' doesn't exist", file=sys.stderr)
        return 1
    if card["color"] != canvas.EN_PROGRESO:
        print(
            f"error: '{args.id}' isn't In progress (current state: {STATE_NAMES[card['color']]}) "
            "-- use 'histos assign' first",
            file=sys.stderr,
        )
        return 1

    draft_path = Path(args.file)
    if not draft_path.exists():
        print(f"error: can't find the draft '{draft_path}'", file=sys.stderr)
        return 1

    proposal_path = _proposal_path(vault_root, args.id)
    proposal_path.parent.mkdir(parents=True, exist_ok=True)
    proposal_path.write_text(draft_path.read_text(encoding="utf-8"), encoding="utf-8")

    card["color"] = canvas.PROPUESTA_PENDIENTE
    canvas.save(vault_root, data)
    print(f"'{args.id}' -> Proposal pending review ('histos diff {args.id}' to review it)")
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    vault_root = _vault_root()
    try:
        data = _load_valid(vault_root)
    except canvas.HistosError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    card = canvas.find_card(data, args.id)
    if card is None:
        print(f"error: card '{args.id}' doesn't exist", file=sys.stderr)
        return 1

    proposal_path = _proposal_path(vault_root, args.id)
    if not proposal_path.exists():
        print(f"error: no pending proposal for '{args.id}'", file=sys.stderr)
        return 1

    md_path = canvas.card_file_path(vault_root, card)
    _, current_body = frontmatter.read(md_path) if md_path.exists() else ({}, "")
    proposed_body = proposal_path.read_text(encoding="utf-8")

    diff = difflib.unified_diff(
        current_body.splitlines(keepends=True),
        proposed_body.splitlines(keepends=True),
        fromfile=f"content/{args.id}.md (current)",
        tofile=f"proposal/{args.id}.md (proposed)",
    )
    sys.stdout.writelines(diff)
    return 0


def _read_source_text(path: Path) -> str:
    if path.suffix.lower() == ".docx":
        try:
            import docx
        except ImportError:
            return f"[couldn't read {path}: missing the python-docx dependency]"
        try:
            doc = docx.Document(str(path))
            return "\n".join(p.text for p in doc.paragraphs)
        except Exception as e:
            return f"[couldn't read {path}: {e}]"
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"[couldn't read {path}: {e}]"


def _render_card_context(vault_root: Path, card: dict, label: str) -> str:
    md_path = canvas.card_file_path(vault_root, card)
    meta, body = frontmatter.read(md_path) if md_path.exists() else ({}, "")

    parts = [f"## {label}: {card['id']} ({STATE_NAMES[card['color']]})"]
    if meta.get("description"):
        parts.append(f"Description: {meta['description']}")
    if card["color"] == canvas.APROBADA and body.strip():
        parts.append(f"Approved content:\n{body.strip()}")
    for src in meta.get("sources") or []:
        src_path = Path(src).expanduser()
        if not src_path.exists():
            parts.append(f"[source not found: {src}]")
            continue
        parts.append(f"Source ({src}):\n{_read_source_text(src_path)}")
    return "\n\n".join(parts)


def cmd_context(args: argparse.Namespace) -> int:
    """Bundles description+approved content+sources for the card and its direct
    dependencies, plus PROJECT.md if it exists -- everything an agent needs to start
    working the card without having to gather it by hand.
    """
    vault_root = _vault_root()
    try:
        data = _load_valid(vault_root)
    except canvas.HistosError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    card = canvas.find_card(data, args.id)
    if card is None:
        print(f"error: card '{args.id}' doesn't exist", file=sys.stderr)
        return 1

    sections = [_render_card_context(vault_root, card, "Card")]

    by_id = {c["id"]: c for c in canvas.cards(data)}
    for e in canvas.incoming_edges(data, args.id):
        dep_card = by_id.get(e["fromNode"])
        if dep_card:
            sections.append(_render_card_context(vault_root, dep_card, "Dependency"))

    project_md = vault_root / "PROJECT.md"
    if project_md.exists():
        sections.append("## Project brief (PROJECT.md)\n\n" + project_md.read_text(encoding="utf-8").strip())

    print("\n\n---\n\n".join(sections))
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    vault_root = _vault_root()
    try:
        data = _load_valid(vault_root)
    except canvas.HistosError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    card = canvas.find_card(data, args.id)
    if card is None:
        print(f"error: card '{args.id}' doesn't exist", file=sys.stderr)
        return 1
    if card["color"] != canvas.PROPUESTA_PENDIENTE:
        print(
            f"error: '{args.id}' doesn't have a pending proposal (current state: {STATE_NAMES[card['color']]})",
            file=sys.stderr,
        )
        return 1

    proposal_path = _proposal_path(vault_root, args.id)
    if not proposal_path.exists():
        print(f"error: can't find the proposal file for '{args.id}'", file=sys.stderr)
        return 1

    md_path = canvas.card_file_path(vault_root, card)
    if md_path.exists():
        meta, _ = frontmatter.read(md_path)
    else:
        meta = dict(frontmatter.DEFAULT_META)
    frontmatter.write(md_path, meta, proposal_path.read_text(encoding="utf-8"))

    approved_path = _approved_path(vault_root, args.id)
    approved_path.parent.mkdir(parents=True, exist_ok=True)
    proposal_path.replace(approved_path)

    card["color"] = canvas.APROBADA
    canvas.recompute_blocked(data)
    canvas.save(vault_root, data)
    print(f"'{args.id}' -> Approved")
    return 0


def cmd_reject(args: argparse.Namespace) -> int:
    vault_root = _vault_root()
    try:
        data = _load_valid(vault_root)
    except canvas.HistosError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    card = canvas.find_card(data, args.id)
    if card is None:
        print(f"error: card '{args.id}' doesn't exist", file=sys.stderr)
        return 1
    if card["color"] != canvas.PROPUESTA_PENDIENTE:
        print(
            f"error: '{args.id}' doesn't have a pending proposal (current state: {STATE_NAMES[card['color']]})",
            file=sys.stderr,
        )
        return 1

    proposal_path = _proposal_path(vault_root, args.id)
    if proposal_path.exists():
        proposal_path.unlink()

    card["color"] = canvas.BACKLOG
    if args.feedback:
        md_path = canvas.card_file_path(vault_root, card)
        meta, body = frontmatter.read(md_path)
        meta["status_note"] = args.feedback
        frontmatter.write(md_path, meta, body)

    canvas.recompute_blocked(data)
    canvas.save(vault_root, data)
    print(f"'{args.id}' -> Backlog (proposal discarded)")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    vault_root = _vault_root()
    try:
        data = _load_valid(vault_root)
    except canvas.HistosError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if canvas.recompute_blocked(data):
        canvas.save(vault_root, data)

    by_state: dict[str, list[dict]] = {c: [] for c in STATE_NAMES}
    for card in canvas.cards(data):
        by_state.setdefault(card["color"], []).append(card)

    order = [
        canvas.BACKLOG, canvas.BLOQUEADA, canvas.EN_PROGRESO,
        canvas.PROPUESTA_PENDIENTE, canvas.SOLICITUD_CAMBIO_DEPENDENCIA, canvas.APROBADA,
    ]
    for state in order:
        entries = by_state.get(state, [])
        print(f"{STATE_NAMES[state]} ({len(entries)})")
        for card in entries:
            desc = ""
            md_path = canvas.card_file_path(vault_root, card)
            if md_path.exists():
                meta, _ = frontmatter.read(md_path)
                if meta.get("description"):
                    desc = f"  -- {meta['description']}"
            print(f"  - {card['id']}  ({card['file']}){desc}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    vault_root = _vault_root()
    try:
        data = canvas.load(vault_root)
    except canvas.HistosError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    errors = validation.validate_all(data)
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
    p.set_defaults(func=cmd_diff)

    p = sub.add_parser("context", help="bundles description+approved dependencies+sources+PROJECT.md to work the card")
    p.add_argument("id")
    p.set_defaults(func=cmd_context)

    p = sub.add_parser("approve", help="applies the pending proposal to the real .md")
    p.add_argument("id")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("reject", help="discards the pending proposal")
    p.add_argument("id")
    p.add_argument("--feedback", default=None)
    p.set_defaults(func=cmd_reject)

    p = sub.add_parser("status", help="lists cards grouped by status")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("validate", help="validates project.canvas against the formal schema")
    p.set_defaults(func=cmd_validate)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
