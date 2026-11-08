"""The critical path: the remaining chain of cards that takes longest, drawn on the canvas arrows.

Decided in the v3.2 vault (dec-camino, cod-camino):
- Only what's left counts: approved cards are done. What a card still needs is its estimate
  (agent median + review median), minus what it already has; a card in review only needs review.
- A card with no estimate uses the median of every closed card; with no closed card at all,
  every card weighs 1 and the path is simply the longest chain of cards.
- Ties: more cards, then board order -- deterministic, so the path doesn't jump around.
- It's painted on the arrows between consecutive path cards (a card's color is its status), in
  a color off Obsidian's six status presets. **Histos never touches an arrow the human colored**:
  it notes the arrows it paints in .histos/critical-path.json and only ever un-paints those, and
  only while they still have its color.
- It's repainted by the vault lock after every command that changed the canvas, and by `status`.
- `histos.config.yaml` can turn it off, change its color, or switch the review clock from active
  (minus the human's pauses) to calendar. A vault without the config has no critical path.
"""
from __future__ import annotations

import json
import statistics
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import canvas, config, events, frontmatter

LEDGER_FILENAME = "critical-path.json"
_IN_PROGRESS = "In progress"
_REVIEW = "Proposal pending review"


@dataclass(frozen=True)
class CriticalPath:
    cards: list[str]
    hours: float | None  # None when it's counted in cards (no history yet)

    @property
    def by_count(self) -> bool:
        return self.hours is None


def ledger_path(vault_root: Path) -> Path:
    return vault_root / events.LOG_DIR / LEDGER_FILENAME


# ---- what's left of each card --------------------------------------------------------------

def remaining_hours(state: str | None, agent: float | None, review: float | None,
                    done_agent: float = 0.0, done_review: float = 0.0) -> float | None:
    """What a card still needs, from its estimate (medians) and what it already has. None when
    there's no estimate to go on."""
    if state == _REVIEW:
        return None if review is None else max(0.0, review - done_review)
    if agent is None or review is None:
        return None
    if state == _IN_PROGRESS:
        return max(review, agent + review - done_agent)  # never less than the whole review
    return agent + review


# ---- the longest chain ---------------------------------------------------------------------

def _dependencies(vault_root: Path | None, data: dict, open_ids: set[str]) -> dict[str, set[str]]:
    deps: dict[str, set[str]] = {cid: set() for cid in open_ids}
    for e in canvas.dependency_edges(data):
        if e["fromNode"] in open_ids and e["toNode"] in open_ids:
            deps[e["toNode"]].add(e["fromNode"])
    if vault_root is not None:  # arrows `arrange` stopped drawing are still dependencies
        for card in canvas.cards(data):
            if card["id"] not in open_ids:
                continue
            md_path = canvas.card_file_path(vault_root, card)
            if md_path.exists():
                implied = frontmatter.read(md_path)[0].get("implied_dependencies") or []
                deps[card["id"]] |= {d for d in implied if d in open_ids}
    return deps


def compute(data: dict, weights: dict[str, float], vault_root: Path | None = None,
            by_count: bool = False) -> CriticalPath:
    """The heaviest chain of open (not approved) cards. `weights` has every open card's hours."""
    order = [c["id"] for c in canvas.cards(data) if c["color"] != canvas.APPROVED]
    if not order:
        return CriticalPath(cards=[], hours=None if by_count else 0.0)
    position = {cid: n for n, cid in enumerate(order)}
    deps = _dependencies(vault_root, data, set(order))
    best: dict[str, tuple[float, int]] = {}
    previous: dict[str, str | None] = {}

    def visit(cid: str, stack: tuple[str, ...] = ()) -> tuple[float, int]:
        if cid in best:
            return best[cid]
        candidates = [(visit(d, stack + (cid,)), -position[d], d) for d in deps[cid] if d not in stack]
        if candidates:
            (hours, count), _, chosen = max(candidates)
        else:
            (hours, count), chosen = (0.0, 0), None
        best[cid] = (hours + weights.get(cid, 1.0), count + 1)
        previous[cid] = chosen
        return best[cid]

    for cid in order:
        visit(cid)
    end = max(order, key=lambda cid: (best[cid], -position[cid]))
    chain = []
    cursor: str | None = end
    while cursor is not None:
        chain.append(cursor)
        cursor = previous[cursor]
    chain.reverse()
    return CriticalPath(cards=chain, hours=None if by_count else round(best[end][0], 2))


# ---- painting the arrows -------------------------------------------------------------------

def _read_ledger(vault_root: Path) -> dict[str, str] | None:
    try:
        data = json.loads(ledger_path(vault_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return {k: v for k, v in data.items() if isinstance(v, str)} if isinstance(data, dict) else None


def paint(data: dict, path: list[str], ledger: dict[str, str] | None, color: str) -> tuple[bool, dict[str, str]]:
    """Recolors `data`'s arrows for `path`. (whether any arrow changed, the new ledger). The four
    rules: un-paint ours that left the path; an arrow of ours the human recolored is theirs now;
    never paint over the human's color; paint (and note) a bare arrow on the path."""
    edges = {e["id"]: e for e in data.get("edges", [])}
    if ledger is None:  # the ledger was lost: only arrows with exactly our color count as ours
        ledger = {eid: e["color"] for eid, e in edges.items() if (e.get("color") or "").upper() == color.upper()}
    wanted = set()
    by_pair = {(e["fromNode"], e["toNode"]): e["id"] for e in edges.values()}
    for a, b in zip(path, path[1:]):
        if (a, b) in by_pair:
            wanted.add(by_pair[(a, b)])

    changed = False
    new_ledger: dict[str, str] = {}
    for eid, painted in ledger.items():
        edge = edges.get(eid)
        if edge is None or (edge.get("color") or "").upper() != painted.upper():
            continue  # gone, or the human recolored it: theirs now
        if eid in wanted and painted.upper() == color.upper():
            new_ledger[eid] = painted
        else:
            edge.pop("color", None)
            changed = True
    for eid in sorted(wanted):
        if eid in new_ledger:
            continue
        edge = edges[eid]
        if edge.get("color"):
            continue  # the human's color: never painted over
        edge["color"] = color
        new_ledger[eid] = color
        changed = True
    return changed, new_ledger


def _update_legend(data: dict) -> bool:
    for node in data.get("nodes", []):
        if node.get("id") == canvas.LEGEND_ID and node.get("type") == "text":
            if node.get("text") in canvas.OLDER_LEGEND_TEXTS:
                node["text"] = canvas.LEGEND_TEXT
                node["height"] = max(node.get("height", 0), canvas.LEGEND_HEIGHT)
                return True
    return False


# ---- all together, inside the vault lock ---------------------------------------------------

def _weights(vault_root: Path, data: dict, review_clock: str) -> tuple[dict[str, float], bool]:
    from . import estimates, history, operations  # the command layer: imported only when painting

    found, card_ids = history.read_all()
    rows = estimates.typed(found, card_ids)
    if not rows:
        return {}, True
    progress = history.open_progress(vault_root, datetime.now(timezone.utc))
    calendar = review_clock == "calendar"
    review_of = (lambda s: s.review_hours) if calendar else estimates.review_value
    fallback = statistics.median([estimates.agent_value(r.sample) + review_of(r.sample) for r in rows])
    weights: dict[str, float] = {}
    for card, _md, _meta, _body, est in operations.estimate_open_cards(vault_root, data, rows):
        agent = est.agent.median if isinstance(est.agent, estimates.Estimate) else None
        review_est = est.review
        if calendar:
            groups = estimates.review_groups(estimates.card_types([card["id"]]).get(card["id"]))
            review_est = estimates.estimate(rows, groups, review_of)
        review = review_est.median if isinstance(review_est, estimates.Estimate) else None
        p = progress.get(card["id"])
        left = remaining_hours(
            p.state if p else None, agent, review,
            p.agent_hours if p else 0.0,
            (p.review_hours if calendar else p.review_active_hours) if p else 0.0,
        )
        weights[card["id"]] = fallback if left is None else left
    return weights, False


def refresh(vault_root: Path) -> CriticalPath | None:
    """Recomputes and repaints the critical path; saves the canvas only if an arrow changed.
    The caller holds the vault lock. None when the vault has no critical path (no config, or off)."""
    try:
        cfg = config.load(vault_root)
    except Exception:  # a broken config is reported by validate; it never blocks a command
        return None
    if cfg is None or not canvas.vault_canvas_path(vault_root).exists():
        return None
    data = canvas.load(vault_root)
    settings = cfg.critical_path
    path = CriticalPath(cards=[], hours=None)
    if settings.enabled:
        weights, by_count = _weights(vault_root, data, settings.review)
        path = compute(data, weights, vault_root, by_count=by_count)
    ledger = _read_ledger(vault_root)
    changed, new_ledger = paint(data, path.cards if settings.enabled else [], ledger, settings.color)
    if settings.enabled:
        changed = _update_legend(data) or changed
    if changed:
        canvas.save(vault_root, data)
    if changed or new_ledger != (ledger or {}):
        target = ledger_path(vault_root)
        target.parent.mkdir(exist_ok=True)
        from .fsutil import write_text_atomic

        write_text_atomic(target, json.dumps(new_ledger, indent=2, sort_keys=True) + "\n")
    return path if settings.enabled else None


def refresh_quietly(vault_root: Path) -> None:
    """For the vault lock: a failure to repaint never fails the command that changed the vault."""
    try:
        refresh(vault_root)
    except Exception as e:
        print(f"warning: couldn't repaint the critical path ({e})", file=sys.stderr)
