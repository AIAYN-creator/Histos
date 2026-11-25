"""Tests for deterministic project metrics (`histos.metrics`)."""
from __future__ import annotations

import math
from datetime import datetime, timezone

from conftest import run

from histos import canvas, estimates, frontmatter, history, metrics


def _sample(card: str, agent_h: float, review_h: float, *, tier: str = "exec",
            model: str = "Sonnet", rejections: int = 0, deps: int = 0, day: int = 1) -> history.Sample:
    return history.Sample(
        vault="v1", card=card, approved_at=datetime(2026, 11, day, tzinfo=timezone.utc),
        agent_hours=agent_h, review_hours=review_h + 1.0, review_active_hours=review_h,
        rounds=1 + rejections, rejections=rejections, tier=tier, model=model,
        dependencies=deps, worker_kind="parent", approximate=False,
    )


def test_metrics_empty_and_insufficient_history():
    data = {"nodes": [{"id": "cod-a", "type": "file", "file": "content/cod-a.md", "color": canvas.BACKLOG}], "edges": []}
    res = metrics.compute("empty", data, {"cod-a": {"proposed_tier": "exec"}}, [], estimates.Rows([]), [])
    assert res.total_cards == 1 and res.approved_cards == 0 and res.open_cards == 1
    assert res.accuracy.compared == 0
    assert res.accuracy.median_error_hours is None
    assert res.accuracy.pct_within_p80 is None
    assert res.pert_open[0].expected is None
    assert res.pert_critical_expected is None
    assert res.pert_critical_sd is None


def test_accuracy_rejections_attempts_and_zero_estimate():
    data = {
        "nodes": [
            {"id": "dec-1", "type": "file", "file": "content/dec-1.md", "color": canvas.APPROVED},
            {"id": "cod-1", "type": "file", "file": "content/cod-1.md", "color": canvas.APPROVED},
            {"id": "cod-2", "type": "file", "file": "content/cod-2.md", "color": canvas.APPROVED},
            {"id": "cod-3", "type": "file", "file": "content/cod-3.md", "color": canvas.BACKLOG},
        ],
        "edges": [
            {"id": "e1", "fromNode": "dec-1", "toNode": "cod-1"},
            {"id": "e2", "fromNode": "cod-1", "toNode": "cod-3"},
        ],
    }
    cards_meta = {
        "dec-1": {
            "proposed_tier": "reasoning", "approved_model": "Opus",
            "estimated_duration_hours": 0.0, "actual_duration_hours": 1.5,
            "estimate": {"agent_hours": {"p80": 1.0}, "review_hours": {"p80": 1.0}},
            "eval_results": {"attempt": 1},
        },
        "cod-1": {
            "proposed_tier": "exec", "approved_model": "Sonnet",
            "estimated_duration_hours": 2.0, "actual_duration_hours": 3.0,
            "estimate": {"agent_hours": {"p80": 2.0}, "review_hours": {"p80": 2.0}},
        },
        "cod-2": {
            "proposed_tier": "exec", "approved_model": "Sonnet",
            "estimated_duration_hours": 4.0, "actual_duration_hours": 3.5,
            "estimate": {"agent_hours": {"p80": 1.0}, "review_hours": {"p80": 1.0}},
        },
        "cod-3": {"proposed_tier": "exec"},
    }
    samples = [
        _sample("dec-1", 1.0, 0.5, tier="reasoning", model="Opus"),
        _sample("cod-1", 2.0, 1.0, tier="exec", model="Sonnet", rejections=1),
        _sample("cod-2", 2.5, 1.0, tier="exec", model="Sonnet"),
    ]
    log_events = [
        {"event": "proposed", "card": "cod-1"},
        {"event": "rejected", "card": "cod-1"},
        {"event": "proposal_refused", "card": "cod-1"},
        {"event": "proposed", "card": "cod-1"},
        {"event": "proposed", "card": "cod-2"},
    ]
    res = metrics.compute("proj", data, cards_meta, samples, estimates.Rows([]), log_events,
                          initial_path=["dec-1", "cod-1", "cod-3"])
    assert res.accuracy.compared == 3
    assert res.accuracy.median_error_hours == 1.0  # sorted abs errors: [0.5, 1.0, 1.5]
    assert res.accuracy.pct_within_p80 == round(200 / 3, 2)  # dec-1 (1.5<=2), cod-1 (3<=4), cod-2 (3.5>2)
    by_card = {c.card: c for c in res.accuracy.cards}
    assert by_card["dec-1"].ratio is None  # zero estimated_duration_hours avoids division by zero
    assert by_card["cod-1"].ratio == 1.5

    tier_map = {t.key: t for t in res.by_tier}
    assert tier_map["exec"].approved == 2
    assert tier_map["exec"].rejections == 1
    assert tier_map["exec"].rejection_rate == round(1 / 3, 4)
    assert tier_map["exec"].avg_attempts == 2.0  # (3 attempts cod-1 + 1 cod-2) / 2

    assert res.drift.initial == ["dec-1", "cod-1", "cod-3"]
    assert res.drift.final == ["cod-3"]
    assert res.drift.removed == ["dec-1", "cod-1"]
    assert res.drift.slipped_outside_initial == []


def test_pert_three_point_and_critical_aggregation():
    # 5 closed cards in tier exec, type cod -> meets MIN_SAMPLES=5
    history_samples = [_sample(f"cod-{i}", float(i), float(i * 2), tier="exec", day=i) for i in range(1, 6)]
    # agent values: [1,2,3,4,5] -> p20=1, med=3, p80=4
    # review values: [2,4,6,8,10] -> p20=2, med=6, p80=8
    # per card: O=3, M=9, P=12 -> E=(3+36+12)/6 = 8.5, SD=(12-3)/6 = 1.5
    rows = estimates.typed(history_samples, {"v1": [f"cod-{i}" for i in range(1, 6)] + ["cod-a", "cod-b"]})
    data = {
        "nodes": [
            {"id": "cod-a", "type": "file", "file": "content/cod-a.md", "color": canvas.BACKLOG},
            {"id": "cod-b", "type": "file", "file": "content/cod-b.md", "color": canvas.BLOCKED},
        ],
        "edges": [{"id": "e1", "fromNode": "cod-a", "toNode": "cod-b"}],
    }
    res = metrics.compute("proj", data, {"cod-a": {"proposed_tier": "exec"}, "cod-b": {"proposed_tier": "exec"}},
                          [], rows, [])
    assert len(res.pert_open) == 2
    p = res.pert_open[0]
    assert (p.optimistic, p.most_likely, p.pessimistic, p.expected, p.std_dev) == (3.0, 9.0, 12.0, 8.5, 1.5)
    assert res.pert_critical_expected == 17.0
    assert res.pert_critical_sd == round(math.sqrt(1.5 ** 2 + 1.5 ** 2), 4)


def test_from_vault_reads_real_vault_without_mutating(initialized_vault, monkeypatch):
    assert run(monkeypatch, initialized_vault, "add-card", "cod-1", "--title", "One", "--done-when", "Done.") == 0
    md = initialized_vault / "content" / "cod-1.md"
    meta, body = frontmatter.read(md)
    meta["proposed_tier"] = "exec"
    frontmatter.write(md, meta, body)
    res = metrics.from_vault(initialized_vault)
    assert res.total_cards == 1
    assert res.open_cards == 1
    assert res.drift.initial == ["cod-1"]
