"""Pure, deterministic project metrics computed from `.histos/events.jsonl`, frontmatter, and
closed-card history (`dec-report-spec`, `cod-metrics-calc`).

Nothing in `compute()` touches disk; `from_vault()` handles loading a vault cleanly.
"""
from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass
from pathlib import Path

from . import canvas, critical, estimates, events, frontmatter, history, tokens


@dataclass(frozen=True)
class CardAccuracy:
    card: str
    estimated_hours: float | None
    actual_hours: float | None
    error_hours: float | None
    ratio: float | None
    within_p80: bool | None


@dataclass(frozen=True)
class AccuracySummary:
    compared: int
    median_error_hours: float | None
    pct_within_p80: float | None
    cards: list[CardAccuracy]


@dataclass(frozen=True)
class GroupStats:
    key: str
    approved: int
    rejections: int
    rejection_rate: float | None
    avg_attempts: float | None


@dataclass(frozen=True)
class CriticalDrift:
    initial: list[str]
    final: list[str]
    added: list[str]
    removed: list[str]
    slipped_outside_initial: list[str]


@dataclass(frozen=True)
class PertEstimate:
    card: str
    optimistic: float | None
    most_likely: float | None
    pessimistic: float | None
    expected: float | None
    std_dev: float | None


@dataclass(frozen=True)
class ProjectMetrics:
    vault_name: str
    total_cards: int
    approved_cards: int
    open_cards: int
    total_agent_hours: float
    total_review_active_hours: float
    total_review_calendar_hours: float
    accuracy: AccuracySummary
    by_tier: list[GroupStats]
    by_model: list[GroupStats]
    drift: CriticalDrift
    pert_open: list[PertEstimate]
    pert_critical_expected: float | None
    pert_critical_sd: float | None
    tokens: tokens.ProjectTokenSummary | None = None


def _as_float(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _percentile20(values: list[float]) -> float:
    """Nearest rank 20th percentile, matching `estimates.percentile80`."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.2 * len(ordered)) - 1)]


def _group_values(rows: list[estimates.Row], groups: list[estimates.Group], extractor) -> list[float] | None:
    for group in groups:
        members = estimates._members_of(group, rows)
        if len(members) >= estimates.MIN_SAMPLES:
            recent = members[-estimates.RECENT:]
            return [extractor(r.sample) for r in recent]
    return None


def _pert_for_card(
    card_id: str,
    meta: dict,
    dependencies: int,
    rows: estimates.Rows,
    types_by_card: dict[str, str | None],
) -> PertEstimate:
    tier = meta.get("proposed_tier")
    type_ = types_by_card.get(card_id)
    agent_vals = _group_values(rows, estimates.agent_groups(tier, type_, dependencies), estimates.agent_value)
    review_vals = _group_values(rows, estimates.review_groups(type_), estimates.review_value)
    if not agent_vals or not review_vals:
        return PertEstimate(card=card_id, optimistic=None, most_likely=None, pessimistic=None, expected=None, std_dev=None)

    optimistic = _percentile20(agent_vals) + _percentile20(review_vals)
    most_likely = statistics.median(agent_vals) + statistics.median(review_vals)
    pessimistic = estimates.percentile80(agent_vals) + estimates.percentile80(review_vals)
    expected = (optimistic + 4.0 * most_likely + pessimistic) / 6.0
    std_dev = (pessimistic - optimistic) / 6.0
    return PertEstimate(
        card=card_id,
        optimistic=round(optimistic, 4),
        most_likely=round(most_likely, 4),
        pessimistic=round(pessimistic, 4),
        expected=round(expected, 4),
        std_dev=round(std_dev, 4),
    )


def _dependency_counts(data: dict, cards_meta: dict[str, dict]) -> dict[str, int]:
    counts = {c["id"]: 0 for c in canvas.cards(data)}
    direct: dict[str, set[str]] = {cid: set() for cid in counts}
    for e in canvas.dependency_edges(data):
        if e["toNode"] in direct:
            direct[e["toNode"]].add(e["fromNode"])
    for cid in counts:
        implied = set((cards_meta.get(cid) or {}).get("implied_dependencies") or [])
        counts[cid] = len(direct[cid] | implied)
    return counts


def _longest_full_chain(data: dict, cards_meta: dict[str, dict]) -> list[str]:
    """Initial structural critical path across every card on the board (unit weights)."""
    order = [c["id"] for c in canvas.cards(data)]
    if not order:
        return []
    position = {cid: idx for idx, cid in enumerate(order)}
    deps: dict[str, set[str]] = {cid: set() for cid in order}
    for e in canvas.dependency_edges(data):
        if e["fromNode"] in deps and e["toNode"] in deps:
            deps[e["toNode"]].add(e["fromNode"])
    for cid in order:
        implied = (cards_meta.get(cid) or {}).get("implied_dependencies") or []
        deps[cid] |= {d for d in implied if d in deps}

    best: dict[str, int] = {}
    previous: dict[str, str | None] = {}

    def visit(cid: str, stack: tuple[str, ...] = ()) -> int:
        if cid in best:
            return best[cid]
        candidates = [(visit(d, stack + (cid,)), -position[d], d) for d in deps[cid] if d not in stack]
        if candidates:
            length, _, chosen = max(candidates)
        else:
            length, chosen = 0, None
        best[cid] = length + 1
        previous[cid] = chosen
        return best[cid]

    for cid in order:
        visit(cid)
    end = max(order, key=lambda cid: (best[cid], -position[cid]))
    chain: list[str] = []
    cur: str | None = end
    while cur is not None:
        chain.append(cur)
        cur = previous[cur]
    chain.reverse()
    return chain


def _group_stats(
    approved_cards: list[dict],
    cards_meta: dict[str, dict],
    rejections: dict[str, int],
    attempts: dict[str, int],
    key_fn,
) -> list[GroupStats]:
    buckets: dict[str, list[str]] = {}
    for card in approved_cards:
        cid = card["id"]
        key = key_fn(cards_meta.get(cid) or {})
        buckets.setdefault(key, []).append(cid)

    stats: list[GroupStats] = []
    for key in sorted(buckets):
        cids = buckets[key]
        approved = len(cids)
        rej = sum(rejections.get(cid, 0) for cid in cids)
        denom = approved + rej
        rate = round(rej / denom, 4) if denom > 0 else None
        att_vals = [attempts[cid] for cid in cids if cid in attempts]
        avg_att = round(sum(att_vals) / len(att_vals), 2) if att_vals else None
        stats.append(GroupStats(key=key, approved=approved, rejections=rej, rejection_rate=rate, avg_attempts=avg_att))
    return stats


def _extract_event_counts(log_events: list[dict], samples: list[history.Sample], cards_meta: dict[str, dict]) -> tuple[dict[str, int], dict[str, int]]:
    rejections: dict[str, int] = {}
    attempts: dict[str, int] = {}
    for e in log_events:
        cid = e.get("card")
        if not cid:
            continue
        ev = e.get("event")
        if ev == "rejected":
            rejections[cid] = rejections.get(cid, 0) + 1
        elif ev in ("proposed", "proposal_refused"):
            attempts[cid] = attempts.get(cid, 0) + 1
    for s in samples:
        rejections[s.card] = max(rejections.get(s.card, 0), s.rejections)
    for cid, meta in cards_meta.items():
        if cid not in attempts:
            ev_res = meta.get("eval_results")
            if isinstance(ev_res, dict) and isinstance(ev_res.get("attempt"), int):
                attempts[cid] = ev_res["attempt"]
    return rejections, attempts


def compute(
    vault_name: str,
    data: dict,
    cards_meta: dict[str, dict],
    samples: list[history.Sample],
    rows: estimates.Rows,
    log_events: list[dict],
    initial_path: list[str] | None = None,
    tokens_summary: tokens.ProjectTokenSummary | None = None,
) -> ProjectMetrics:
    all_cards = canvas.cards(data)
    approved = [c for c in all_cards if c.get("color") == canvas.APPROVED]
    open_cards = [c for c in all_cards if c.get("color") != canvas.APPROVED]

    accuracy_cards: list[CardAccuracy] = []
    abs_errors: list[float] = []
    p80_flags: list[bool] = []
    slipped: list[str] = []

    init_chain = list(initial_path) if initial_path is not None else _longest_full_chain(data, cards_meta)
    init_set = set(init_chain)

    for card in approved:
        cid = card["id"]
        meta = cards_meta.get(cid) or {}
        est_h = _as_float(meta.get("estimated_duration_hours"))
        act_h = _as_float(meta.get("actual_duration_hours"))
        err = round(act_h - est_h, 4) if est_h is not None and act_h is not None else None
        ratio = round(act_h / est_h, 4) if est_h is not None and est_h > 0 and act_h is not None else None

        within_p80: bool | None = None
        est_block = meta.get("estimate")
        if act_h is not None and isinstance(est_block, dict):
            a_p80 = _as_float((est_block.get("agent_hours") or {}).get("p80") if isinstance(est_block.get("agent_hours"), dict) else None)
            r_p80 = _as_float((est_block.get("review_hours") or {}).get("p80") if isinstance(est_block.get("review_hours"), dict) else None)
            if a_p80 is not None and r_p80 is not None:
                within_p80 = act_h <= (a_p80 + r_p80)
                p80_flags.append(within_p80)

        if err is not None:
            abs_errors.append(abs(err))
            if cid not in init_set and err > 0:
                slipped.append(cid)
        accuracy_cards.append(CardAccuracy(card=cid, estimated_hours=est_h, actual_hours=act_h,
                                           error_hours=err, ratio=ratio, within_p80=within_p80))

    accuracy = AccuracySummary(
        compared=len(abs_errors),
        median_error_hours=round(statistics.median(abs_errors), 4) if abs_errors else None,
        pct_within_p80=round(100.0 * sum(p80_flags) / len(p80_flags), 2) if p80_flags else None,
        cards=accuracy_cards,
    )

    rejections, attempts = _extract_event_counts(log_events, samples, cards_meta)
    by_tier = _group_stats(approved, cards_meta, rejections, attempts, lambda m: str(m.get("proposed_tier") or "unclassified"))
    by_model = _group_stats(approved, cards_meta, rejections, attempts, lambda m: str(m.get("approved_model") or "unrecorded"))

    dep_counts = _dependency_counts(data, cards_meta)
    types_by_card = estimates.card_types([c["id"] for c in all_cards])
    pert_open: list[PertEstimate] = []
    open_weights: dict[str, float] = {}
    for card in open_cards:
        cid = card["id"]
        pert = _pert_for_card(cid, cards_meta.get(cid) or {}, dep_counts.get(cid, 0), rows, types_by_card)
        pert_open.append(pert)
        if pert.most_likely is not None:
            open_weights[cid] = pert.most_likely

    by_count = len(open_weights) < len(open_cards)
    final_cp = critical.compute(data, open_weights if not by_count else {}, by_count=by_count)
    final_set = set(final_cp.cards)
    drift = CriticalDrift(
        initial=init_chain,
        final=final_cp.cards,
        added=[c for c in final_cp.cards if c not in init_set],
        removed=[c for c in init_chain if c not in final_set],
        slipped_outside_initial=slipped,
    )

    pert_by_id = {p.card: p for p in pert_open}
    cp_perts = [pert_by_id[cid] for cid in final_cp.cards if cid in pert_by_id]
    if cp_perts and all(p.expected is not None and p.std_dev is not None for p in cp_perts):
        cp_expected = round(sum(p.expected for p in cp_perts if p.expected is not None), 4)
        cp_sd = round(math.sqrt(sum((p.std_dev or 0.0) ** 2 for p in cp_perts)), 4)
    else:
        cp_expected, cp_sd = None, None

    return ProjectMetrics(
        vault_name=vault_name,
        total_cards=len(all_cards),
        approved_cards=len(approved),
        open_cards=len(open_cards),
        total_agent_hours=round(sum(s.agent_hours for s in samples), 4),
        total_review_active_hours=round(sum(s.review_active_hours for s in samples), 4),
        total_review_calendar_hours=round(sum(s.review_hours for s in samples), 4),
        accuracy=accuracy,
        by_tier=by_tier,
        by_model=by_model,
        drift=drift,
        pert_open=pert_open,
        pert_critical_expected=cp_expected,
        pert_critical_sd=cp_sd,
        tokens=tokens_summary,
    )


def _merge_archived_canvas(vault_root: Path, data: dict) -> dict:
    """Includes nodes/edges from any `*archive*.canvas` in the vault root when requested."""
    nodes = list(data.get("nodes", []))
    edges = list(data.get("edges", []))
    seen_nodes = {n.get("id") for n in nodes}
    seen_edges = {e.get("id") for e in edges}
    for path in sorted(vault_root.glob("*.canvas")):
        if path.name == canvas.CANVAS_FILENAME:
            continue
        try:
            extra = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for node in extra.get("nodes") or []:
            if isinstance(node, dict) and node.get("id") not in seen_nodes:
                nodes.append(node)
                seen_nodes.add(node.get("id"))
        for edge in extra.get("edges") or []:
            if isinstance(edge, dict) and edge.get("id") not in seen_edges:
                edges.append(edge)
                seen_edges.add(edge.get("id"))
    return {"nodes": nodes, "edges": edges}


def from_vault(vault_root: Path, include_archived: bool = False) -> ProjectMetrics:
    data = canvas.load(vault_root)
    if include_archived:
        data = _merge_archived_canvas(vault_root, data)
    cards_meta: dict[str, dict] = {}
    for card in canvas.cards(data):
        md_path = canvas.card_file_path(vault_root, card)
        if md_path.exists():
            cards_meta[card["id"]] = frontmatter.read(md_path)[0]
    vault_samples = history.vault_samples(vault_root)
    all_samples, ids_by_vault = history.read_all()
    rows = estimates.typed(all_samples, ids_by_vault)
    log_events = events.read(vault_root)
    tokens_summary = tokens.summarize_vault(
        vault_root,
        card_ids=[c["id"] for c in canvas.cards(data)],
        log_events=log_events,
    )
    return compute(
        vault_root.resolve().name, data, cards_meta, vault_samples, rows, log_events,
        tokens_summary=tokens_summary,
    )
