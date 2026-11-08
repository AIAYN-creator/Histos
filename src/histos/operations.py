"""Structured, non-printing core for the human-facing commands (init, status, diff, approve,
reject, arrange). Each function takes an explicit vault_root, returns a small dataclass on
success, and raises canvas.HistosError on failure -- no print(), no argparse, no Path.cwd().

This is what both the CLI (src/histos/cli.py, which wraps these in try/except and prints)
and the desktop app call, so there is exactly one implementation of what each action does.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import Optional

from . import __version__, canvas, estimates, events, frontmatter, history, validation
from .fsutil import copy_file_atomic, write_text_atomic
from .lock import locked

STATE_NAMES = {
    canvas.BLOCKED: "Blocked",
    canvas.IN_PROGRESS: "In progress",
    canvas.PENDING_REVIEW: "Proposal pending review",
    canvas.APPROVED: "Approved",
    canvas.DEPENDENCY_CHANGE_REQUEST: "Dependency change request",
    canvas.BACKLOG: "Backlog",
}

_STATUS_ORDER = [
    canvas.BACKLOG, canvas.BLOCKED, canvas.IN_PROGRESS,
    canvas.PENDING_REVIEW, canvas.DEPENDENCY_CHANGE_REQUEST, canvas.APPROVED,
]

PROPOSALS_DIR = "proposals"
APPROVED_DIR = "approved"
# Vaults created before this English rename have these on disk instead. Never created anew --
# only used to keep an existing vault working with the folder names it already has.
_LEGACY_PROPOSALS_DIR = "propuestas"
_LEGACY_APPROVED_DIR = "aprobados"
# Who works an In progress card ('parent', 'sub-cap3'...), set by `assign --worker`.
WORKER_KEY = "worker"
# Last evals run on a proposal for the card -- written by propose, see the frontmatter schema.
EVAL_RESULTS_KEY = "eval_results"
V3_CONFIG_TEMPLATE = "histos.config.yaml"
V3_EVALS_TEMPLATES = ["evals/basics.yaml", "evals/examples.yaml"]
# Only applies to the guide files (its evals' `only` names them), so unlike the two above it's
# added to an evals/ folder that already exists: it can't change how any card is checked.
GUIDE_EVALS_TEMPLATE = "evals/guides.yaml"
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
        write_text_atomic(target, ref.read_text(encoding="utf-8"))
    return warnings


@dataclass
class InitResult:
    vault_root: Path
    template_warnings: list[str]


@locked("init")
def init_vault(vault_root: Path) -> InitResult:
    canvas_path = canvas.vault_canvas_path(vault_root)
    if canvas_path.exists():
        raise canvas.HistosError(f"{canvas_path} already exists")
    (vault_root / "content").mkdir(parents=True, exist_ok=True)
    (vault_root / PROPOSALS_DIR).mkdir(parents=True, exist_ok=True)
    (vault_root / APPROVED_DIR).mkdir(parents=True, exist_ok=True)
    canvas.save(vault_root, {"nodes": [canvas.build_legend_node()], "edges": []})
    warnings = _install_agent_templates(vault_root) + _install_v3_config(vault_root)
    events.emit("vault_created", version=__version__)
    return InitResult(vault_root=vault_root, template_warnings=warnings)


def _install_v3_config(vault_root: Path) -> list[str]:
    """histos.config.yaml and an evals/ folder, each only if it isn't there yet. The bundled
    evals go in only when there's no evals/ folder at all, so they never add active evals
    next to ones the human already wrote."""
    to_install = []
    warnings = []
    if (vault_root / V3_CONFIG_TEMPLATE).exists():
        warnings.append(f"{V3_CONFIG_TEMPLATE} already exists, leaving it alone")
    else:
        to_install.append(V3_CONFIG_TEMPLATE)
    if (vault_root / "evals").exists():
        warnings.append("evals/ already exists, leaving its files alone")
    else:
        to_install += V3_EVALS_TEMPLATES
    if not (vault_root / GUIDE_EVALS_TEMPLATE).exists():
        to_install.append(GUIDE_EVALS_TEMPLATE)
    for rel_path in to_install:
        ref = resources.files("histos").joinpath("templates", *rel_path.split("/"))
        target = vault_root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(target, ref.read_text(encoding="utf-8"))
    return warnings


@locked("init --add-config")
def add_config(vault_root: Path) -> list[str]:
    """Brings an existing vault to v3: adds histos.config.yaml and evals/ if missing, nothing else."""
    if not canvas.vault_canvas_path(vault_root).exists():
        raise canvas.HistosError(f"no {canvas.CANVAS_FILENAME} here -- use plain 'histos init' for a new vault")
    templates = [V3_CONFIG_TEMPLATE, *V3_EVALS_TEMPLATES, GUIDE_EVALS_TEMPLATE]
    missing = [t for t in templates if not (vault_root / t).exists()]
    warnings = _install_v3_config(vault_root)
    added = [t for t in missing if (vault_root / t).exists()]
    if added:
        events.emit("config_added", files=added)
    return warnings


DONE_WHEN_KEY = "done_when"


def create_card(vault_root: Path, data: dict, card_id: str, title: str, *, description: Optional[str] = None,
                depends_on: tuple[str, ...] | list[str] = (), done_when: Optional[str] = None) -> dict:
    """Adds one card to `data` and writes its content/<id>.md -- without saving the canvas, so a
    caller can create several and save once. The caller checks the id and that the
    dependencies exist; a cycle raises HistosError before any file is written."""
    by_id = {c["id"]: c for c in canvas.cards(data)}
    all_approved = all(by_id[dep]["color"] == canvas.APPROVED for dep in depends_on)
    initial_color = canvas.BACKLOG if (not depends_on or all_approved) else canvas.BLOCKED

    width, height = canvas.estimate_card_size(description)
    node = canvas.add_card_node(data, card_id, initial_color, width=width, height=height)
    for dep in depends_on:
        canvas.add_edge(data, dep, card_id)

    cycle = canvas.detect_cycle(data)
    if cycle:
        raise canvas.HistosError(f"that dependency would form a cycle: {' -> '.join(cycle)}")

    md_path = canvas.card_file_path(vault_root, node)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    meta = dict(frontmatter.DEFAULT_META)
    body = f"# {title}\n\n"
    if description:
        meta["description"] = description
        body += f"{description}\n\n"
    if done_when:
        meta[DONE_WHEN_KEY] = done_when
        body += f"**Done when:** {done_when}\n\n"
    meta[frontmatter.CONTENT_HASH_KEY] = frontmatter.body_hash(body)
    frontmatter.write(md_path, meta, body)

    canvas.place_new_card(data, node)
    events.emit("card_created", card=card_id, title=title, depends_on=list(depends_on))
    return node


@dataclass
class CardSummary:
    id: str
    file: str
    description: Optional[str]
    worker: Optional[str] = None
    tier: Optional[str] = None  # proposed_tier
    approved_model: Optional[str] = None
    proposed_model: Optional[str] = None
    estimate: Optional[estimates.CardEstimate] = None  # None for an approved card


@dataclass
class StatusGroup:
    color: str  # raw canvas color code, e.g. canvas.BACKLOG
    label: str  # human-readable status name, e.g. "Backlog" -- STATE_NAMES[color]
    cards: list[CardSummary]


@dataclass
class StatusResult:
    groups: list[StatusGroup] = field(default_factory=list)  # in the fixed display order


ESTIMATE_KEY = "estimate"
ESTIMATED_KEY = "estimated_duration_hours"
ACTUAL_KEY = "actual_duration_hours"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def refresh_estimates(vault_root: Path, data: dict) -> dict[str, estimates.CardEstimate]:
    """Estimates every open card from the history of all the registered vaults (read once) and
    keeps each card's frontmatter up to date: `estimate` (the detail) and `estimated_duration_hours`
    (agent median + review median, null if either is missing). A file is only written when the
    rounded numbers changed. The caller holds the vault lock.

    The review half is built from the human's ACTIVE review hours (calendar time minus their
    `histos pause` periods), but it is named `review_hours` in the frontmatter, as dec-estimador says."""
    try:
        found, card_ids = history.read_all()
    except (OSError, ValueError, KeyError):
        found, card_ids = [], {}  # no history is never an error: every card just has no data
    rows = estimates.typed(found, card_ids)

    result = {}
    for card, md_path, meta, body, card_estimate in estimate_open_cards(vault_root, data, rows):
        result[card["id"]] = card_estimate
        if md_path.exists():
            _store_estimate(md_path, meta, body, card_estimate)
    return result


def estimate_open_cards(vault_root: Path, data: dict, rows: list[estimates.Row]) -> list[tuple]:
    """Read-only: for each open (not Approved) card, `(card, md_path, meta, body, estimate)` --
    the same figures `status` shows. Nothing is written; refresh_estimates stores them, and the
    coverage report only counts them."""
    types = estimates.card_types([c["id"] for c in canvas.cards(data)])
    declared: dict[str, set[str]] = {}
    for edge in canvas.dependency_edges(data):
        declared.setdefault(edge["toNode"], set()).add(edge["fromNode"])

    result = []
    for card in canvas.cards(data):
        if card["color"] == canvas.APPROVED:
            continue
        md_path = canvas.card_file_path(vault_root, card)
        meta, body = frontmatter.read(md_path) if md_path.exists() else ({}, "")
        dependencies = declared.get(card["id"], set()) | set(meta.get(IMPLIED_DEPENDENCIES_KEY) or [])
        card_estimate = estimates.for_card(rows, meta.get("proposed_tier"), types.get(card["id"]), len(dependencies))
        result.append((card, md_path, meta, body, card_estimate))
    return result


@dataclass
class Coverage:
    samples: list[history.Sample]
    skipped: list[str]
    vault_names: dict[str, str]          # vault id -> folder name, to show where a sample came from
    agent: list[tuple]                   # [(group, cards)] -- estimates.agent_coverage
    review: list[tuple]
    open_cards: int = 0
    open_with_agent: int = 0
    open_with_review: int = 0
    outliers: list[history.Sample] = field(default_factory=list)


def coverage(home: Optional[Path] = None) -> Coverage:
    """Read-only: how many closed cards each estimate group has, and how many open cards of ALL the
    registered vaults would get an estimate right now. Takes no lock and writes nothing. Vaults
    whose canvas can't be read are left out of the open-card count."""
    skipped: list[str] = []
    found, card_ids = history.read_all(home, skipped)
    rows = estimates.typed(found, card_ids)
    report = Coverage(samples=found, skipped=skipped, vault_names=history.vault_names(home),
                      agent=estimates.agent_coverage(rows), review=estimates.review_coverage(rows),
                      outliers=estimates.outliers(found))
    seen: set[Path] = set()
    for vault_root in history.registered_vaults(home):
        if vault_root.resolve() in seen:
            continue
        seen.add(vault_root.resolve())
        try:
            data = canvas.load(vault_root)
            cards = estimate_open_cards(vault_root, data, rows)
        except (canvas.HistosError, OSError, ValueError, KeyError):
            continue
        for *_, card_estimate in cards:
            report.open_cards += 1
            report.open_with_agent += isinstance(card_estimate.agent, estimates.Estimate)
            report.open_with_review += isinstance(card_estimate.review, estimates.Estimate)
    return report


def _estimate_part(e) -> Optional[dict]:
    if not isinstance(e, estimates.Estimate):
        return None
    return {"median": round(e.median, 2), "p80": round(e.p80, 2), "basis": e.basis}


def _numbers(part) -> tuple:
    if not isinstance(part, dict):
        return (None, None)
    return (part.get("median"), part.get("p80"))


def _store_estimate(md_path: Path, meta: dict, body: str, e: estimates.CardEstimate) -> None:
    agent, review = _estimate_part(e.agent), _estimate_part(e.review)
    total = round(e.agent.median + e.review.median, 2) if agent and review else None
    old = meta.get(ESTIMATE_KEY) if isinstance(meta.get(ESTIMATE_KEY), dict) else {}
    unchanged = (_numbers(old.get("agent_hours")) == _numbers(agent)
                 and _numbers(old.get("review_hours")) == _numbers(review)
                 and meta.get(ESTIMATED_KEY) == total)
    if unchanged:
        return
    meta[ESTIMATE_KEY] = {"agent_hours": agent, "review_hours": review, "at": _now().date().isoformat()}
    meta[ESTIMATED_KEY] = total
    frontmatter.write(md_path, meta, body)


@locked("status")  # recomputing Blocked can save the canvas
def get_status(vault_root: Path) -> StatusResult:
    data = _load_valid(vault_root)
    if canvas.recompute_blocked(data):
        canvas.save(vault_root, data)
    open_estimates = refresh_estimates(vault_root, data)

    by_color: dict[str, list[dict]] = {c: [] for c in STATE_NAMES}
    for card in canvas.cards(data):
        by_color.setdefault(card["color"], []).append(card)

    groups = []
    for color in _STATUS_ORDER:
        summaries = []
        for card in by_color.get(color, []):
            meta = {}
            md_path = canvas.card_file_path(vault_root, card)
            if md_path.exists():
                meta, _ = frontmatter.read(md_path)
            worker = meta.get(WORKER_KEY) if color == canvas.IN_PROGRESS else None
            summaries.append(CardSummary(
                id=card["id"], file=card["file"], description=meta.get("description"), worker=worker,
                tier=meta.get("proposed_tier"), approved_model=meta.get("approved_model"),
                proposed_model=meta.get("proposed_model"), estimate=open_estimates.get(card["id"]),
            ))
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


def _actual_hours(vault_root: Path, card_id: str) -> Optional[float]:
    """What the card really took: its agent time + its ACTIVE review time (calendar minus the
    human's pauses). The `approved` event is only written to the log when the lock is released,
    so the card still shows as waiting for review there: that stretch is closed at this very
    moment, which is when the approval happens. None if the log can't tell the whole story."""
    try:
        progress = history.open_progress(vault_root, _now()).get(card_id)
    except (OSError, ValueError, KeyError):
        return None
    if progress is None or progress.incomplete or progress.state != STATE_NAMES[canvas.PENDING_REVIEW]:
        return None
    return round(progress.agent_hours + progress.review_active_hours, 2)


@dataclass
class ApproveResult:
    card_id: str


@locked("approve")
def approve(vault_root: Path, card_id: str) -> ApproveResult:
    data = _load_valid(vault_root)
    card = canvas.find_card(data, card_id)
    if card is None:
        raise canvas.HistosError(f"card '{card_id}' doesn't exist")
    if card["color"] != canvas.PENDING_REVIEW:
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
    body = proposal_path.read_text(encoding="utf-8")
    meta[frontmatter.CONTENT_HASH_KEY] = frontmatter.body_hash(body)
    actual = _actual_hours(vault_root, card_id)
    if actual is not None:
        meta[ACTUAL_KEY] = actual
    frontmatter.write(md_path, meta, body)

    approved_path = _approved_path(vault_root, card_id)
    approved_path.parent.mkdir(parents=True, exist_ok=True)
    proposal_path.replace(approved_path)

    card["color"] = canvas.APPROVED
    canvas.recompute_blocked(data)
    canvas.save(vault_root, data)
    events.emit("approved", card=card_id, **events.size_of(body))
    return ApproveResult(card_id=card_id)


@locked("accept-edit")
def accept_edit(vault_root: Path, card_ids: list[str]) -> list[str]:
    """(Human) bless hand edits to content/<id>.md: the current body becomes the legitimate
    one, so the content_untouched eval stops flagging it."""
    data = _load_valid(vault_root)
    missing = [i for i in card_ids if canvas.find_card(data, i) is None]
    if missing:
        raise canvas.HistosError(f"card(s) not found: {', '.join(missing)}")
    for card_id in card_ids:
        md_path = canvas.card_file_path(vault_root, canvas.find_card(data, card_id))
        meta, body = frontmatter.read(md_path)
        meta[frontmatter.CONTENT_HASH_KEY] = frontmatter.body_hash(body)
        frontmatter.write(md_path, meta, body)
        events.emit("edit_accepted", card=card_id)
    return card_ids


@locked("retry")
def retry(vault_root: Path, card_id: str) -> None:
    """(Human) give a card whose proposals kept failing the evals a fresh set of attempts."""
    data = _load_valid(vault_root)
    card = canvas.find_card(data, card_id)
    if card is None:
        raise canvas.HistosError(f"card '{card_id}' doesn't exist")
    md_path = canvas.card_file_path(vault_root, card)
    meta, body = frontmatter.read(md_path)
    last = meta.get(EVAL_RESULTS_KEY) or {}
    if card["color"] != canvas.IN_PROGRESS or last.get("passed") is not False:
        raise canvas.HistosError(f"'{card_id}' has no failed evals to retry")
    del meta[EVAL_RESULTS_KEY]
    frontmatter.write(md_path, meta, body)
    events.emit("retry", card=card_id)


@dataclass
class RejectResult:
    card_id: str


@locked("reject")
def reject(vault_root: Path, card_id: str, feedback: Optional[str] = None) -> RejectResult:
    data = _load_valid(vault_root)
    card = canvas.find_card(data, card_id)
    if card is None:
        raise canvas.HistosError(f"card '{card_id}' doesn't exist")
    if card["color"] != canvas.PENDING_REVIEW:
        raise canvas.HistosError(
            f"'{card_id}' doesn't have a pending proposal (current state: {STATE_NAMES[card['color']]})"
        )

    proposal_path = _proposal_path(vault_root, card_id)
    if proposal_path.exists():
        proposal_path.unlink()

    events.emit("rejected", card=card_id, feedback=feedback)  # before the worker is cleared below
    card["color"] = canvas.BACKLOG
    md_path = canvas.card_file_path(vault_root, card)
    if md_path.exists():
        meta, body = frontmatter.read(md_path)
        if feedback or WORKER_KEY in meta:
            if feedback:
                meta["status_note"] = feedback
            meta.pop(WORKER_KEY, None)  # back in the backlog: anyone can take it next
            frontmatter.write(md_path, meta, body)

    canvas.recompute_blocked(data)
    canvas.save(vault_root, data)
    return RejectResult(card_id=card_id)


IMPLIED_DEPENDENCIES_KEY = "implied_dependencies"


@dataclass
class ArrangeResult:
    cards: int
    columns: int
    unconnected: int
    set_aside_done: int
    pruned: int
    backup_path: Path


@locked("arrange")
def arrange(vault_root: Path, *, set_aside_done: bool, prune_redundant: bool) -> ArrangeResult:
    data = _load_valid(vault_root)
    canvas_path = canvas.vault_canvas_path(vault_root)
    backup_path = canvas_path.with_name(canvas_path.name + ".bak")
    copy_file_atomic(canvas_path, backup_path)

    summary = canvas.arrange(data, set_aside_done=set_aside_done, prune_redundant=prune_redundant)
    _remember_pruned_dependencies(vault_root, data, summary.pruned)
    canvas.save(vault_root, data)
    events.emit("arranged", pruned=len(summary.pruned))
    return ArrangeResult(
        cards=len(canvas.cards(data)),
        columns=summary.columns,
        unconnected=summary.unconnected,
        set_aside_done=summary.set_aside_done,
        pruned=len(summary.pruned),
        backup_path=backup_path,
    )


def _remember_pruned_dependencies(vault_root: Path, data: dict, pruned: list[tuple[str, str]]) -> None:
    """A pruned arrow is still a dependency the card declared -- kept in its frontmatter for 'histos context'."""
    by_dependent: dict[str, list[str]] = {}
    for dependency, dependent in pruned:
        by_dependent.setdefault(dependent, []).append(dependency)
    for dependent, dependencies in by_dependent.items():
        md_path = canvas.card_file_path(vault_root, canvas.find_card(data, dependent))
        if not md_path.exists():
            continue
        meta, body = frontmatter.read(md_path)
        implied = list(meta.get(IMPLIED_DEPENDENCIES_KEY) or [])
        implied += [d for d in dependencies if d not in implied]
        meta[IMPLIED_DEPENDENCIES_KEY] = implied
        frontmatter.write(md_path, meta, body)
