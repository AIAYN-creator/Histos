"""Structured, non-printing core for the 5 human-facing commands (init, status, diff,
approve, reject). Each function takes an explicit vault_root, returns a small dataclass on
success, and raises canvas.HistosError on failure -- no print(), no argparse, no Path.cwd().

This is what both the CLI (src/histos/cli.py, which wraps these in try/except and prints
the same text it always has) and the future desktop app call, so there is exactly one
implementation of what each action does. See the "Histos desktop app" plan for why.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Optional

from . import canvas, frontmatter, validation

STATE_NAMES = {
    canvas.BLOQUEADA: "Blocked",
    canvas.EN_PROGRESO: "In progress",
    canvas.PROPUESTA_PENDIENTE: "Proposal pending review",
    canvas.APROBADA: "Approved",
    canvas.SOLICITUD_CAMBIO_DEPENDENCIA: "Dependency change request",
    canvas.BACKLOG: "Backlog",
}

_STATUS_ORDER = [
    canvas.BACKLOG, canvas.BLOQUEADA, canvas.EN_PROGRESO,
    canvas.PROPUESTA_PENDIENTE, canvas.SOLICITUD_CAMBIO_DEPENDENCIA, canvas.APROBADA,
]

PROPOSALS_DIR = "proposals"
APPROVED_DIR = "approved"
# Vaults created before this English rename have these on disk instead. Never created anew --
# only used to keep an existing vault working with the folder names it already has.
_LEGACY_PROPOSALS_DIR = "propuestas"
_LEGACY_APPROVED_DIR = "aprobados"
AGENT_TEMPLATES = ["AGENTS.md", "CLAUDE.md", ".claude/settings.json"]


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


def _install_agent_templates(vault_root: Path) -> list[str]:
    """Copies the bundled AGENTS.md/CLAUDE.md/.claude/settings.json into the vault, skipping
    any that already exist. Returns one warning string per file skipped this way -- the
    caller decides how to surface that (cmd_init prints it; a GUI could show a toast).
    """
    warnings = []
    for rel_path in AGENT_TEMPLATES:
        target = vault_root / rel_path
        if target.exists():
            warnings.append(
                f"{rel_path} already exists, leaving it alone -- copy the reference content by hand if you want it"
            )
            continue
        ref = resources.files("histos").joinpath("templates", *rel_path.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(ref.read_text(encoding="utf-8"), encoding="utf-8")
    return warnings


@dataclass
class InitResult:
    vault_root: Path
    template_warnings: list[str]


def init_vault(vault_root: Path) -> InitResult:
    canvas_path = canvas.vault_canvas_path(vault_root)
    if canvas_path.exists():
        raise canvas.HistosError(f"{canvas_path} already exists")
    (vault_root / "content").mkdir(parents=True, exist_ok=True)
    (vault_root / PROPOSALS_DIR).mkdir(parents=True, exist_ok=True)
    (vault_root / APPROVED_DIR).mkdir(parents=True, exist_ok=True)
    canvas.save(vault_root, {"nodes": [canvas.build_legend_node()], "edges": []})
    warnings = _install_agent_templates(vault_root)
    return InitResult(vault_root=vault_root, template_warnings=warnings)


@dataclass
class CardSummary:
    id: str
    file: str
    description: Optional[str]


@dataclass
class StatusGroup:
    color: str  # raw canvas color code, e.g. canvas.BACKLOG
    label: str  # human-readable status name, e.g. "Backlog" -- STATE_NAMES[color]
    cards: list[CardSummary]


@dataclass
class StatusResult:
    groups: list[StatusGroup] = field(default_factory=list)  # in the fixed display order


def get_status(vault_root: Path) -> StatusResult:
    data = _load_valid(vault_root)
    if canvas.recompute_blocked(data):
        canvas.save(vault_root, data)

    by_color: dict[str, list[dict]] = {c: [] for c in STATE_NAMES}
    for card in canvas.cards(data):
        by_color.setdefault(card["color"], []).append(card)

    groups = []
    for color in _STATUS_ORDER:
        summaries = []
        for card in by_color.get(color, []):
            description = None
            md_path = canvas.card_file_path(vault_root, card)
            if md_path.exists():
                meta, _ = frontmatter.read(md_path)
                description = meta.get("description")
            summaries.append(CardSummary(id=card["id"], file=card["file"], description=description))
        groups.append(StatusGroup(color=color, label=STATE_NAMES[color], cards=summaries))
    return StatusResult(groups=groups)


@dataclass
class DiffResult:
    card_id: str
    current_body: str
    proposed_body: str


def get_diff(vault_root: Path, card_id: str) -> DiffResult:
    data = _load_valid(vault_root)
    card = canvas.find_card(data, card_id)
    if card is None:
        raise canvas.HistosError(f"card '{card_id}' doesn't exist")

    proposal_path = _proposal_path(vault_root, card_id)
    if not proposal_path.exists():
        raise canvas.HistosError(f"no pending proposal for '{card_id}'")

    md_path = canvas.card_file_path(vault_root, card)
    _, current_body = frontmatter.read(md_path) if md_path.exists() else ({}, "")
    proposed_body = proposal_path.read_text(encoding="utf-8")
    return DiffResult(card_id=card_id, current_body=current_body, proposed_body=proposed_body)


@dataclass
class ApproveResult:
    card_id: str


def approve(vault_root: Path, card_id: str) -> ApproveResult:
    data = _load_valid(vault_root)
    card = canvas.find_card(data, card_id)
    if card is None:
        raise canvas.HistosError(f"card '{card_id}' doesn't exist")
    if card["color"] != canvas.PROPUESTA_PENDIENTE:
        raise canvas.HistosError(
            f"'{card_id}' doesn't have a pending proposal (current state: {STATE_NAMES[card['color']]})"
        )

    proposal_path = _proposal_path(vault_root, card_id)
    if not proposal_path.exists():
        raise canvas.HistosError(f"can't find the proposal file for '{card_id}'")

    md_path = canvas.card_file_path(vault_root, card)
    if md_path.exists():
        meta, _ = frontmatter.read(md_path)
    else:
        meta = dict(frontmatter.DEFAULT_META)
    frontmatter.write(md_path, meta, proposal_path.read_text(encoding="utf-8"))

    approved_path = _approved_path(vault_root, card_id)
    approved_path.parent.mkdir(parents=True, exist_ok=True)
    proposal_path.replace(approved_path)

    card["color"] = canvas.APROBADA
    canvas.recompute_blocked(data)
    canvas.save(vault_root, data)
    return ApproveResult(card_id=card_id)


@dataclass
class RejectResult:
    card_id: str


def reject(vault_root: Path, card_id: str, feedback: Optional[str] = None) -> RejectResult:
    data = _load_valid(vault_root)
    card = canvas.find_card(data, card_id)
    if card is None:
        raise canvas.HistosError(f"card '{card_id}' doesn't exist")
    if card["color"] != canvas.PROPUESTA_PENDIENTE:
        raise canvas.HistosError(
            f"'{card_id}' doesn't have a pending proposal (current state: {STATE_NAMES[card['color']]})"
        )

    proposal_path = _proposal_path(vault_root, card_id)
    if proposal_path.exists():
        proposal_path.unlink()

    card["color"] = canvas.BACKLOG
    if feedback:
        md_path = canvas.card_file_path(vault_root, card)
        meta, body = frontmatter.read(md_path)
        meta["status_note"] = feedback
        frontmatter.write(md_path, meta, body)

    canvas.recompute_blocked(data)
    canvas.save(vault_root, data)
    return RejectResult(card_id=card_id)
