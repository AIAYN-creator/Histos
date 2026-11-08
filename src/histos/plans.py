"""A plan: the whole card table proposed at once -- cards, dependencies and a "done when" line
for each -- reviewed by the human as one piece, and only then created on the board.

    proposals/plan.yaml                     the pending plan (one at a time)
    proposals/.plan_feedback.json           why the last plan was rejected
    approved/plans/<YYYYMMDD-HHMMSS>.yaml   every approved plan, as history

The format, in YAML because Histos has to read it to create the cards:

    summary: How the work is cut and why.
    cards:
      - id: dec-format
        title: Decide the format
        description: optional, one line
        done_when: The format is approved and cod-format can implement it without questions.
        depends_on: [an-existing-card]      # optional: cards in this plan or already on the board

No tier: a card is classified right before it's worked, because its tier depends on the
approved content of its dependencies -- which doesn't exist yet while planning.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

from . import canvas, decisions, events, headings, operations
from .errors import HistosError
from .fsutil import write_text_atomic
from .lock import locked

PLAN_FILENAME = "plan.yaml"
_FEEDBACK_FILENAME = ".plan_feedback.json"
_ARCHIVE_SUBDIR = "plans"
_TOP_KEYS = {"summary", "cards"}
_CARD_KEYS = {"id", "title", "description", "done_when", "depends_on"}
_REQUIRED_CARD_KEYS = ("id", "title", "done_when")

# Thresholds of the warnings (plans.warnings)
MAX_DEPENDENCIES = 5
MIN_DONE_WHEN_WORDS = 5
_EMPTY_PHRASES = {headings.normalize(p) for p in (
    "está hecho", "hecho", "terminado", "funciona", "listo", "completado",
    "done", "it works", "works", "finished", "complete")}


@dataclass
class PlanCard:
    id: str
    title: str
    done_when: str
    description: str | None = None
    depends_on: list[str] = field(default_factory=list)


@dataclass
class Plan:
    summary: str
    cards: list[PlanCard]


def plan_path(vault_root: Path) -> Path:
    return operations._proposal_path(vault_root, "x").parent / PLAN_FILENAME


def _feedback_path(vault_root: Path) -> Path:
    return operations._proposal_path(vault_root, "x").parent / _FEEDBACK_FILENAME


def _archive_dir(vault_root: Path) -> Path:
    return operations._approved_path(vault_root, "x").parent / _ARCHIVE_SUBDIR


def pending(vault_root: Path) -> Plan | None:
    path = plan_path(vault_root)
    return parse(path.read_text(encoding="utf-8")) if path.exists() else None


def last_feedback(vault_root: Path) -> str | None:
    try:
        data = json.loads(_feedback_path(vault_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    text = data.get("feedback") if isinstance(data, dict) else None
    return text if isinstance(text, str) and text else None


def parse(text: str) -> Plan:
    """The plan in `text`, or HistosError listing everything wrong with its shape at once."""
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise HistosError(f"the plan can't be parsed as YAML: {e}") from None
    if not isinstance(raw, dict):
        raise HistosError("the plan must be a mapping with 'summary' and 'cards'")
    errors = [f"unknown key '{k}' (allowed: summary, cards)" for k in raw if k not in _TOP_KEYS]
    summary = raw.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        errors.append("'summary' is required: how the work is cut and why")
    raw_cards = raw.get("cards")
    if not isinstance(raw_cards, list) or not raw_cards:
        errors.append("'cards' must be a non-empty list")
        raw_cards = []
    cards = []
    for i, item in enumerate(raw_cards, start=1):
        where = f"card {i}" + (f" ({item.get('id')})" if isinstance(item, dict) and item.get("id") else "")
        if not isinstance(item, dict):
            errors.append(f"{where}: must be a mapping with id, title and done_when")
            continue
        card_errors = [f"{where}: unknown key '{k}' (allowed: {', '.join(sorted(_CARD_KEYS))})"
                       for k in item if k not in _CARD_KEYS]
        for key in _REQUIRED_CARD_KEYS:
            if not isinstance(item.get(key), str) or not item[key].strip():
                card_errors.append(f"{where}: '{key}' is required and must be text")
        description = item.get("description")
        if description is not None and not isinstance(description, str):
            card_errors.append(f"{where}: 'description' must be text")
        depends_on = item.get("depends_on") or []
        if not isinstance(depends_on, list) or not all(isinstance(d, str) for d in depends_on):
            card_errors.append(f"{where}: 'depends_on' must be a list of card ids")
            depends_on = []
        errors += card_errors
        if not card_errors:
            cards.append(PlanCard(id=item["id"].strip(), title=item["title"].strip(),
                                  done_when=item["done_when"].strip(),
                                  description=description.strip() if description else None,
                                  depends_on=[d.strip() for d in depends_on]))
    if errors:
        raise HistosError("the plan is not valid:\n" + "\n".join(f"  - {e}" for e in errors))
    return Plan(summary=summary.strip(), cards=cards)


def structural_errors(plan: Plan, data: dict) -> list[str]:
    """What would make creating the plan's cards unsafe or wrong -- blocks propose and approve."""
    existing = {c["id"] for c in canvas.cards(data)}
    errors, seen = [], set()
    for card in plan.cards:
        if not canvas.is_valid_card_id(card.id):
            errors.append(f"'{card.id}': not a valid id -- only letters, numbers, '_' and '-'")
        if card.id in seen:
            errors.append(f"'{card.id}': appears twice in the plan")
        elif card.id in existing:
            errors.append(f"'{card.id}': a card with that id already exists")
        seen.add(card.id)
    planned = {c.id for c in plan.cards}
    for card in plan.cards:
        for dep in card.depends_on:
            if dep == card.id:
                errors.append(f"'{card.id}': can't depend on itself")
            elif dep not in planned and dep not in existing:
                errors.append(f"'{card.id}': depends on '{dep}', which is neither in the plan nor on the board")
    if not errors:
        cycle = _cycle(plan)
        if cycle:
            errors.append(f"dependency cycle: {' -> '.join(cycle)}")
    return errors


def topological_order(plan: Plan) -> list[PlanCard]:
    """Dependencies first. Dependencies on cards already on the board don't count -- they exist."""
    by_id = {c.id: c for c in plan.cards}
    pending_deps = {c.id: {d for d in c.depends_on if d in by_id} for c in plan.cards}
    order: list[PlanCard] = []
    ready = [c.id for c in plan.cards if not pending_deps[c.id]]
    while ready:
        current = ready.pop(0)
        order.append(by_id[current])
        for card in plan.cards:
            if current in pending_deps[card.id]:
                pending_deps[card.id].discard(current)
                if not pending_deps[card.id]:
                    ready.append(card.id)
    return order


def levels(plan: Plan, data: dict) -> list[list[str]]:
    """The plan by levels: level 1 can start once the plan is approved (no dependencies, or only
    on approved board cards), level N+1 when level N is done. Plan order inside a level. A card
    that depends on an unapproved board card is marked 'id (waits for other)': it can't start yet."""
    approved = {c["id"] for c in canvas.cards(data) if c["color"] == canvas.APPROVED}
    on_board = {c["id"] for c in canvas.cards(data)}
    level_of: dict[str, int] = {}
    for card in topological_order(plan):
        in_plan = [level_of[d] for d in card.depends_on if d in level_of]
        level_of[card.id] = 1 + max(in_plan, default=0)
    result: list[list[str]] = []
    for card in plan.cards:
        if card.id not in level_of:  # part of a cycle: structural_errors refuses these earlier
            continue
        waits = [d for d in card.depends_on if d in on_board and d not in approved]
        label = f"{card.id} (waits for {', '.join(waits)})" if waits else card.id
        while len(result) < level_of[card.id]:
            result.append([])
        result[level_of[card.id] - 1].append(label)
    return result


def _plain(text: str) -> str:
    return headings.normalize(text).rstrip(".").strip()


def warnings(plan: Plan, data: dict) -> list[str]:
    """Things that are usually an oversight in a plan. They never block: the human decides."""
    found: list[str] = []
    depended_on = {d for c in plan.cards for d in c.depends_on}
    for card in plan.cards:
        if len(plan.cards) > 1 and not card.depends_on and card.id not in depended_on:
            found.append(f"warning: {card.id} is loose: it depends on nothing and nothing depends on it")
        if len(card.depends_on) > MAX_DEPENDENCIES:
            found.append(f"warning: {card.id} depends on {len(card.depends_on)} cards")
        if len(card.done_when.split()) < MIN_DONE_WHEN_WORDS or _plain(card.done_when) in _EMPTY_PHRASES:
            found.append(f"warning: {card.id}: done_when \"{card.done_when}\" can't be checked")
    return found


def _cycle(plan: Plan) -> list[str] | None:
    if len(topological_order(plan)) == len(plan.cards):
        return None
    graph = {"nodes": [{"id": c.id} for c in plan.cards],
             "edges": [{"fromNode": d, "toNode": c.id} for c in plan.cards for d in c.depends_on]}
    return canvas.detect_cycle(graph)


def _set_feedback(vault_root: Path, text: str | None) -> None:
    path = _feedback_path(vault_root)
    if text:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(path, json.dumps({"feedback": text}, ensure_ascii=False, indent=2) + "\n")
    else:
        path.unlink(missing_ok=True)


@dataclass
class ProposeResult:
    cards: int
    replaced_pending: bool


@locked("plan propose", actor="agent")
def propose(vault_root: Path, text: str) -> ProposeResult:
    plan = parse(text)
    errors = structural_errors(plan, operations._load_valid(vault_root))
    if errors:
        raise HistosError("the plan can't be created as it is:\n" + "\n".join(f"  - {e}" for e in errors))
    path = plan_path(vault_root)
    replaced = path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(path, text)
    _set_feedback(vault_root, None)  # a new plan answers the old feedback
    events.emit("plan_proposed", cards=len(plan.cards))
    return ProposeResult(cards=len(plan.cards), replaced_pending=replaced)


@dataclass
class ApproveResult:
    created: list[dict]  # the new canvas nodes, in creation order
    archived: Path


@locked("plan approve")
def approve(vault_root: Path) -> ApproveResult:
    """(Human) creates every card of the pending plan, all or nothing, with one canvas save.
    Approving the plan is the human's authorization for its dependencies."""
    path = plan_path(vault_root)
    if not path.exists():
        raise HistosError("there's no pending plan")
    text = path.read_text(encoding="utf-8")
    plan = parse(text)
    data = operations._load_valid(vault_root)
    errors = structural_errors(plan, data)  # again: the board may have changed since propose
    if errors:
        raise HistosError("the plan no longer fits the board -- ask for a new one:\n"
                          + "\n".join(f"  - {e}" for e in errors))

    created, written = [], []
    try:
        for card in topological_order(plan):
            md_path = canvas.card_file_path(vault_root, {"file": f"content/{card.id}.md"})
            if md_path.exists():  # a stray file with that name: never overwrite it
                raise HistosError(f"content/{card.id}.md already exists without a card -- move it away first")
            written.append(md_path)
            created.append(operations.create_card(
                vault_root, data, card.id, card.title, description=card.description,
                depends_on=card.depends_on, done_when=card.done_when,
            ))
        canvas.recompute_blocked(data)
        canvas.save(vault_root, data)
    except BaseException:
        for md_path in written:  # the canvas wasn't saved: no card exists, so no file either
            md_path.unlink(missing_ok=True)
        raise

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    archived = _archive_dir(vault_root) / f"{stamp}.yaml"
    archived.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(archived, text)
    path.unlink()
    events.emit("plan_approved", cards=len(created))
    return ApproveResult(created=created, archived=archived)


@locked("plan reject")
def reject(vault_root: Path, feedback: str | None = None) -> int:
    """(Human) discards the pending plan; returns how many cards it had."""
    path = plan_path(vault_root)
    if not path.exists():
        raise HistosError("there's no pending plan")
    try:
        cards = len(parse(path.read_text(encoding="utf-8")).cards)
    except HistosError:
        cards = 0
    path.unlink()
    _set_feedback(vault_root, feedback)
    recorded = decisions.record(vault_root, "plan rejected", feedback)
    events.emit("plan_rejected", cards=cards, feedback=feedback, recorded_in=recorded)
    return cards
