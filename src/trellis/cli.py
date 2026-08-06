"""CLI de Trellis. Cada subcomando es un cmd_xxx(args) -> int, testeable sin pasar por argparse."""
from __future__ import annotations

import argparse
import difflib
import sys
from importlib import resources
from pathlib import Path

from . import canvas, frontmatter, validation

STATE_NAMES = {
    canvas.BLOQUEADA: "Bloqueada",
    canvas.EN_PROGRESO: "En progreso",
    canvas.PROPUESTA_PENDIENTE: "Propuesta pendiente de revision",
    canvas.APROBADA: "Aprobada",
    canvas.SOLICITUD_CAMBIO_DEPENDENCIA: "Solicitud cambio de dependencia",
    canvas.BACKLOG: "Backlog",
}

PROPOSALS_DIR = ".trellis/proposals"
AGENT_TEMPLATES = ["AGENTS.md", "CLAUDE.md"]


def _install_agent_templates(vault_root: Path) -> None:
    for name in AGENT_TEMPLATES:
        target = vault_root / name
        if target.exists():
            print(f"aviso: ya existe {name}, no lo toco -- copia el contenido de referencia a mano si quieres", file=sys.stderr)
            continue
        ref = resources.files("trellis").joinpath("templates", name)
        target.write_text(ref.read_text(encoding="utf-8"), encoding="utf-8")


def _vault_root() -> Path:
    return Path.cwd()


def _proposal_path(vault_root: Path, card_id: str) -> Path:
    return vault_root / PROPOSALS_DIR / f"{card_id}.md"


def _load_valid(vault_root: Path) -> dict:
    data = canvas.load(vault_root)
    errors = validation.validate_all(data)
    if errors:
        raise canvas.TrellisError(
            "el canvas no es valido -- corrigelo antes de continuar ('trellis validate' para detalle):\n"
            + "\n".join(f"  - {e}" for e in errors)
        )
    return data


def cmd_init(args: argparse.Namespace) -> int:
    vault_root = _vault_root()
    canvas_path = canvas.vault_canvas_path(vault_root)
    if canvas_path.exists():
        print(f"error: ya existe {canvas_path}", file=sys.stderr)
        return 1
    (vault_root / "content").mkdir(parents=True, exist_ok=True)
    (vault_root / PROPOSALS_DIR).mkdir(parents=True, exist_ok=True)
    canvas.save(vault_root, {"nodes": [canvas.build_legend_node()], "edges": []})
    _install_agent_templates(vault_root)
    print(f"vault inicializado en {vault_root}")
    return 0


def cmd_add_card(args: argparse.Namespace) -> int:
    vault_root = _vault_root()
    try:
        data = _load_valid(vault_root)
    except canvas.TrellisError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if canvas.find_card(data, args.id) is not None:
        print(f"error: ya existe una tarjeta con id '{args.id}'", file=sys.stderr)
        return 1

    depends_on = args.depends_on or []
    if depends_on and not args.authorized:
        print(
            "error: --depends-on toca el grafo de dependencias y necesita autorizacion humana "
            "explicita -- anade --authorized solo si ya la obtuviste en la conversacion",
            file=sys.stderr,
        )
        return 1

    by_id = {c["id"]: c for c in canvas.cards(data)}
    for dep in depends_on:
        if dep not in by_id:
            print(f"error: la dependencia '{dep}' no existe", file=sys.stderr)
            return 1

    all_approved = all(by_id[dep]["color"] == canvas.APROBADA for dep in depends_on)
    initial_color = canvas.BACKLOG if (not depends_on or all_approved) else canvas.BLOQUEADA

    node = canvas.add_card_node(data, args.id, initial_color)
    for dep in depends_on:
        canvas.add_edge(data, dep, args.id)

    cycle = canvas.detect_cycle(data)
    if cycle:
        print(f"error: esa dependencia formaria un ciclo: {' -> '.join(cycle)}", file=sys.stderr)
        return 1

    canvas.save(vault_root, data)

    md_path = canvas.card_file_path(vault_root, node)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    meta = dict(frontmatter.DEFAULT_META)
    body = f"# {args.title}\n\n"
    if args.description:
        meta["description"] = args.description
        body += f"{args.description}\n\n"
    frontmatter.write(md_path, meta, body)

    print(f"tarjeta '{args.id}' creada ({STATE_NAMES[initial_color]}) -> {node['file']}")
    return 0


def cmd_link(args: argparse.Namespace) -> int:
    """Anade dependencias a una tarjeta YA existente -- add-card --depends-on solo cubre
    la creacion; esto cubre el caso de descubrir una dependencia despues de crear la tarjeta.
    """
    vault_root = _vault_root()
    try:
        data = _load_valid(vault_root)
    except canvas.TrellisError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if canvas.find_card(data, args.id) is None:
        print(f"error: no existe la tarjeta '{args.id}'", file=sys.stderr)
        return 1

    if not args.authorized:
        print(
            "error: 'link' toca el grafo de dependencias y necesita autorizacion humana "
            "explicita -- anade --authorized solo si ya la obtuviste en la conversacion",
            file=sys.stderr,
        )
        return 1

    by_id = {c["id"]: c for c in canvas.cards(data)}
    for dep in args.depends_on:
        if dep == args.id:
            print(f"error: '{args.id}' no puede depender de si misma", file=sys.stderr)
            return 1
        if dep not in by_id:
            print(f"error: la dependencia '{dep}' no existe", file=sys.stderr)
            return 1

    for dep in args.depends_on:
        canvas.add_edge(data, dep, args.id)

    cycle = canvas.detect_cycle(data)
    if cycle:
        print(f"error: esa dependencia formaria un ciclo: {' -> '.join(cycle)}", file=sys.stderr)
        return 1

    canvas.recompute_blocked(data)
    canvas.save(vault_root, data)
    print(f"'{args.id}' ahora depende de: {', '.join(args.depends_on)}")
    return 0


def cmd_assign(args: argparse.Namespace) -> int:
    vault_root = _vault_root()
    try:
        data = _load_valid(vault_root)
    except canvas.TrellisError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    canvas.recompute_blocked(data)

    for card_id in args.ids:
        card = canvas.find_card(data, card_id)
        if card is None:
            print(f"error: no existe la tarjeta '{card_id}'", file=sys.stderr)
            return 1
        if card["color"] == canvas.BLOQUEADA:
            print(f"error: '{card_id}' esta Bloqueada, no se puede asignar todavia", file=sys.stderr)
            return 1

    for card_id in args.ids:
        card = canvas.find_card(data, card_id)
        card["color"] = canvas.EN_PROGRESO
        md_path = canvas.card_file_path(vault_root, card)
        meta, body = frontmatter.read(md_path)
        meta["assigned_to"] = args.by
        frontmatter.write(md_path, meta, body)
        print(f"'{card_id}' -> En progreso (assigned_to={args.by})")

    canvas.save(vault_root, data)
    return 0


def cmd_describe(args: argparse.Namespace) -> int:
    """Actualiza solo el campo description del frontmatter -- nunca toca el cuerpo, asi que
    no hace falta pasar por propose/approve (es metadato, igual que assigned_to en 'assign').
    """
    vault_root = _vault_root()
    try:
        data = _load_valid(vault_root)
    except canvas.TrellisError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    card = canvas.find_card(data, args.id)
    if card is None:
        print(f"error: no existe la tarjeta '{args.id}'", file=sys.stderr)
        return 1

    md_path = canvas.card_file_path(vault_root, card)
    if not md_path.exists():
        print(f"error: no encuentro {md_path}", file=sys.stderr)
        return 1

    meta, body = frontmatter.read(md_path)
    meta["description"] = args.text
    frontmatter.write(md_path, meta, body)
    print(f"'{args.id}': descripcion actualizada")
    return 0


def cmd_propose(args: argparse.Namespace) -> int:
    vault_root = _vault_root()
    try:
        data = _load_valid(vault_root)
    except canvas.TrellisError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    card = canvas.find_card(data, args.id)
    if card is None:
        print(f"error: no existe la tarjeta '{args.id}'", file=sys.stderr)
        return 1
    if card["color"] != canvas.EN_PROGRESO:
        print(
            f"error: '{args.id}' no esta En progreso (estado actual: {STATE_NAMES[card['color']]}) "
            "-- usa 'trellis assign' primero",
            file=sys.stderr,
        )
        return 1

    draft_path = Path(args.file)
    if not draft_path.exists():
        print(f"error: no encuentro el borrador '{draft_path}'", file=sys.stderr)
        return 1

    proposal_path = _proposal_path(vault_root, args.id)
    proposal_path.parent.mkdir(parents=True, exist_ok=True)
    proposal_path.write_text(draft_path.read_text(encoding="utf-8"), encoding="utf-8")

    card["color"] = canvas.PROPUESTA_PENDIENTE
    canvas.save(vault_root, data)
    print(f"'{args.id}' -> Propuesta pendiente de revision ('trellis diff {args.id}' para revisarla)")
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    vault_root = _vault_root()
    try:
        data = _load_valid(vault_root)
    except canvas.TrellisError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    card = canvas.find_card(data, args.id)
    if card is None:
        print(f"error: no existe la tarjeta '{args.id}'", file=sys.stderr)
        return 1

    proposal_path = _proposal_path(vault_root, args.id)
    if not proposal_path.exists():
        print(f"error: no hay propuesta pendiente para '{args.id}'", file=sys.stderr)
        return 1

    md_path = canvas.card_file_path(vault_root, card)
    _, current_body = frontmatter.read(md_path) if md_path.exists() else ({}, "")
    proposed_body = proposal_path.read_text(encoding="utf-8")

    diff = difflib.unified_diff(
        current_body.splitlines(keepends=True),
        proposed_body.splitlines(keepends=True),
        fromfile=f"content/{args.id}.md (actual)",
        tofile=f"propuesta/{args.id}.md (propuesta)",
    )
    sys.stdout.writelines(diff)
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    vault_root = _vault_root()
    try:
        data = _load_valid(vault_root)
    except canvas.TrellisError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    card = canvas.find_card(data, args.id)
    if card is None:
        print(f"error: no existe la tarjeta '{args.id}'", file=sys.stderr)
        return 1
    if card["color"] != canvas.PROPUESTA_PENDIENTE:
        print(
            f"error: '{args.id}' no tiene una propuesta pendiente (estado actual: {STATE_NAMES[card['color']]})",
            file=sys.stderr,
        )
        return 1

    proposal_path = _proposal_path(vault_root, args.id)
    if not proposal_path.exists():
        print(f"error: no encuentro el fichero de propuesta para '{args.id}'", file=sys.stderr)
        return 1

    md_path = canvas.card_file_path(vault_root, card)
    if md_path.exists():
        meta, _ = frontmatter.read(md_path)
    else:
        meta = dict(frontmatter.DEFAULT_META)
    frontmatter.write(md_path, meta, proposal_path.read_text(encoding="utf-8"))
    proposal_path.unlink()

    card["color"] = canvas.APROBADA
    canvas.recompute_blocked(data)
    canvas.save(vault_root, data)
    print(f"'{args.id}' -> Aprobada")
    return 0


def cmd_reject(args: argparse.Namespace) -> int:
    vault_root = _vault_root()
    try:
        data = _load_valid(vault_root)
    except canvas.TrellisError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    card = canvas.find_card(data, args.id)
    if card is None:
        print(f"error: no existe la tarjeta '{args.id}'", file=sys.stderr)
        return 1
    if card["color"] != canvas.PROPUESTA_PENDIENTE:
        print(
            f"error: '{args.id}' no tiene una propuesta pendiente (estado actual: {STATE_NAMES[card['color']]})",
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
    print(f"'{args.id}' -> Backlog (propuesta descartada)")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    vault_root = _vault_root()
    try:
        data = _load_valid(vault_root)
    except canvas.TrellisError as e:
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
    except canvas.TrellisError as e:
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
    parser = argparse.ArgumentParser(prog="trellis", description="CLI de Trellis (ver docs/canvas-schema.md)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="crea project.canvas + content/ en el directorio actual")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("add-card", help="crea una tarjeta nueva")
    p.add_argument("id")
    p.add_argument("--title", required=True)
    p.add_argument("--description", default=None, help="una linea, va al frontmatter y al cuerpo inicial")
    p.add_argument("--depends-on", nargs="+", default=[], metavar="ID")
    p.add_argument(
        "--authorized", action="store_true",
        help="confirma que un humano autorizo tocar el grafo de dependencias",
    )
    p.set_defaults(func=cmd_add_card)

    p = sub.add_parser("link", help="anade dependencias a una tarjeta ya existente")
    p.add_argument("id")
    p.add_argument("--depends-on", nargs="+", required=True, metavar="ID")
    p.add_argument(
        "--authorized", action="store_true",
        help="confirma que un humano autorizo tocar el grafo de dependencias",
    )
    p.set_defaults(func=cmd_link)

    p = sub.add_parser("assign", help="pasa una o mas tarjetas a En progreso")
    p.add_argument("ids", nargs="+", metavar="ID")
    p.add_argument("--by", choices=["agent", "human"], default="agent")
    p.set_defaults(func=cmd_assign)

    p = sub.add_parser("describe", help="actualiza la descripcion (frontmatter) de una tarjeta existente")
    p.add_argument("id")
    p.add_argument("--text", required=True)
    p.set_defaults(func=cmd_describe)

    p = sub.add_parser("propose", help="sube una propuesta de contenido para revision")
    p.add_argument("id")
    p.add_argument("--file", required=True, help="ruta al borrador con el contenido propuesto")
    p.set_defaults(func=cmd_propose)

    p = sub.add_parser("diff", help="muestra el diff entre el contenido actual y la propuesta pendiente")
    p.add_argument("id")
    p.set_defaults(func=cmd_diff)

    p = sub.add_parser("approve", help="aplica la propuesta pendiente al .md real")
    p.add_argument("id")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("reject", help="descarta la propuesta pendiente")
    p.add_argument("id")
    p.add_argument("--feedback", default=None)
    p.set_defaults(func=cmd_reject)

    p = sub.add_parser("status", help="lista las tarjetas agrupadas por estado")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("validate", help="valida project.canvas contra el schema formal")
    p.set_defaults(func=cmd_validate)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
