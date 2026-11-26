"""Deterministic tests for model name normalization and aliases (cod-alias-modelos).

Tests cover:
- normalize_model pure function behavior: whitespace, casing, vendor prefixes, explicit aliases.
- Rows.of_model and agent_model_coverage aggregation.
- Coverage, status, and report aggregation into ONE canonical model when history mixes
  'Opus 5.5' and 'Claude Opus 5.5'.
- Custom model_aliases in histos.config.yaml.
- Preserving raw original model strings in events.jsonl and content/<id>.md frontmatter byte-for-byte.
"""
from __future__ import annotations

import json
from argparse import Namespace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from conftest import run
from histos import config, estimates, events, frontmatter, metrics, operations, report
from histos.commands.board import cmd_status
from histos.commands.estimate import cmd_estimate
from histos.estimates import Row
from histos.history import Sample
from test_history import A, B, P, R, ev, write_log

T0 = datetime(2026, 12, 1, 10, 0, tzinfo=timezone.utc)


def iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def make_sample(n: int, card: str, model: str | None, agent: float = 1.0, review: float = 0.5, tier: str = "exec", deps: int = 0) -> Sample:
    return Sample(
        vault="v1",
        card=card,
        approved_at=T0 + timedelta(hours=n),
        agent_hours=agent,
        review_hours=review,
        review_active_hours=review,
        rounds=1,
        rejections=0,
        tier=tier,
        model=model,
        dependencies=deps,
        worker_kind="parent",
        approximate=False,
    )


def closed_card_log(n: int, card: str, tier: str, model: str, agent_min: int = 60, review_min: int = 30) -> list[dict]:
    day = T0 + timedelta(days=n)
    proposed = day + timedelta(minutes=agent_min)
    approved = proposed + timedelta(minutes=review_min)
    return [
        ev(iso(day - timedelta(hours=1)), "card_created", card, data={"title": card, "depends_on": []},
           transitions=[(card, None, B)]),
        ev(iso(day - timedelta(minutes=45)), "classified", card, data={"tier": tier}),
        ev(iso(day - timedelta(minutes=30)), "model_approved", card, data={"model": model}),
        ev(iso(day), "assigned", card, "parent", {"by": "agent", "takeover": False, "previous_worker": None},
           [(card, B, P)]),
        ev(iso(proposed), "proposed", card, "parent", {"attempt": 1, "words": 10, "lines": 2}, [(card, P, R)]),
        ev(iso(approved), "approved", card, None, {"words": 10, "lines": 2}, [(card, R, A)]),
    ]


def register_vault(vault: Path, vault_id: str = "hist_vault") -> None:
    home = events.home()
    home.mkdir(parents=True, exist_ok=True)
    reg_path = home / "vaults.json"
    known = json.loads(reg_path.read_text(encoding="utf-8"))["vaults"] if reg_path.exists() else []
    known.append({"id": vault_id, "path": str(vault)})
    reg_path.write_text(json.dumps({"vaults": known}), encoding="utf-8")


def set_card_meta(vault: Path, card_id: str, **fields) -> None:
    md = vault / "content" / f"{card_id}.md"
    meta, body = frontmatter.read(md)
    meta.update(fields)
    frontmatter.write(md, meta, body)


# --- Unit tests: normalize_model -----------------------------------------------------------

def test_normalize_model_pure():
    assert estimates.normalize_model(None) is None
    assert estimates.normalize_model("") is None
    assert estimates.normalize_model("   ") is None
    assert estimates.normalize_model(123) is None  # type: ignore[arg-type]

    # Collapses whitespace
    assert estimates.normalize_model("  Claude   Opus   5.5  ") == "Opus 5.5"

    # Case insensitivity
    assert estimates.normalize_model("opus 5.5") == "Opus 5.5"
    assert estimates.normalize_model("OPUS 5.5") == "Opus 5.5"
    assert estimates.normalize_model("claude opus 5.5") == "Opus 5.5"

    # Vendor prefix stripping
    assert estimates.normalize_model("Claude Opus 5.5") == "Opus 5.5"
    assert estimates.normalize_model("anthropic/opus 5.5") == "Opus 5.5"
    assert estimates.normalize_model("anthropic opus 5.5") == "Opus 5.5"
    assert estimates.normalize_model("anthropic/claude opus 5.5") == "Opus 5.5"
    assert estimates.normalize_model("openai/gpt-4o") == "GPT-4o"
    assert estimates.normalize_model("openai gpt-4o") == "GPT-4o"
    assert estimates.normalize_model("google/gemini-2.5-pro") == "Gemini-2.5-pro"

    # Distinguishable models without stripped slash
    assert estimates.normalize_model("Gemini 4 Hydrogen (High)") == "Gemini 4 Hydrogen (High)"
    assert estimates.normalize_model("gemini 4 hydrogen (high)") == "Gemini 4 Hydrogen (High)"

    # Claude Sonnet vs Sonnet 4.6
    assert estimates.normalize_model("Claude Sonnet 4.6") == "Sonnet 4.6"
    assert estimates.normalize_model("Sonnet 4.6") == "Sonnet 4.6"
    assert estimates.normalize_model("sonnet 4.6") == "Sonnet 4.6"

    # Explicit aliases map (case/whitespace-insensitive lookup)
    aliases = {
        "legacy-heavy": "Opus 5.5",
        "  custom-fast  ": "Haiku 5.5",
        "chatgpt": "openai/gpt-4o",
    }
    assert estimates.normalize_model("legacy-heavy", aliases) == "Opus 5.5"
    assert estimates.normalize_model("LEGACY-HEAVY", aliases) == "Opus 5.5"
    assert estimates.normalize_model("  legacy-heavy  ", aliases) == "Opus 5.5"
    assert estimates.normalize_model("CUSTOM-FAST", aliases) == "Haiku 5.5"
    assert estimates.normalize_model("chatgpt", aliases) == "GPT-4o"
    assert estimates.normalize_model("unrelated", aliases) == "Unrelated"


# --- Unit tests: Rows.of_model and agent_model_coverage -----------------------------------

def test_rows_of_model_and_agent_model_coverage():
    samples = [
        make_sample(0, "c0", "Opus 5.5"),
        make_sample(1, "c1", "Claude Opus 5.5"),
        make_sample(2, "c2", "opus 5.5"),
        make_sample(3, "c3", "Claude Sonnet 4.6"),
        make_sample(4, "c4", None),
    ]
    data = estimates.Rows([Row(s, "cod") for s in samples])

    # All three Opus variants resolve to the same 3 rows
    opus_rows = data.of_model("Opus 5.5")
    assert len(opus_rows) == 3
    assert set(r.sample.card for r in opus_rows) == {"c0", "c1", "c2"}

    assert data.of_model("Claude Opus 5.5") is opus_rows
    assert data.of_model("opus 5.5") is opus_rows

    sonnet_rows = data.of_model("Sonnet 4.6")
    assert len(sonnet_rows) == 1
    assert sonnet_rows[0].sample.card == "c3"
    assert data.of_model("Claude Sonnet 4.6") is sonnet_rows

    # Coverage aggregates into distinct canonical model names
    coverage = estimates.agent_model_coverage(data)
    assert coverage == [("Opus 5.5", 3), ("Sonnet 4.6", 1)]


# --- Integration: Coverage, status, report aggregate mixed models into ONE ----------------

def test_history_mixing_opus_and_claude_opus(tmp_path_factory, monkeypatch, capsys):
    # 1. Setup history vault with 3 "Opus 5.5" and 2 "Claude Opus 5.5" cards (total 5 cards)
    hist_vault = tmp_path_factory.mktemp("hist_mixed")
    log = []
    for n in range(3):
        log += closed_card_log(n, f"cod-h{n}", "exec", "Opus 5.5", agent_min=60, review_min=30)
    for n in range(3, 5):
        log += closed_card_log(n, f"cod-h{n}", "exec", "Claude Opus 5.5", agent_min=60, review_min=30)
    write_log(hist_vault, log, vault_id="mixed_hist")
    register_vault(hist_vault, vault_id="mixed_hist")

    # 2. Setup active vault
    active_vault = tmp_path_factory.mktemp("active_vault")
    run(monkeypatch, active_vault, "init")
    run(monkeypatch, active_vault, "add-card", "cod-open1", "--title", "open card 1")
    set_card_meta(active_vault, "cod-open1", proposed_tier="exec")
    run(monkeypatch, active_vault, "model", "cod-open1", "--approved", "Claude Opus 5.5", "--authorized")

    # 3. Test histos estimate --coverage:
    # 3 "Opus 5.5" + 2 "Claude Opus 5.5" = 5 cards, meeting MIN_SAMPLES 5 under ONE canonical name
    monkeypatch.chdir(active_vault)
    capsys.readouterr()
    assert cmd_estimate(Namespace(coverage=True)) == 0
    coverage_out = capsys.readouterr().out
    assert "Agent time by model" in coverage_out
    assert "Opus 5.5" in coverage_out
    assert "yes" in coverage_out  # 5 cards >= 5 is enough ("yes", not "X more")
    assert "Claude Opus 5.5" not in [line.split()[0:3] for line in coverage_out.splitlines() if "Claude Opus 5.5" in line]
    # Ensure there is only 1 row for Opus 5.5 in the table
    model_table_lines = [
        line.strip() for line in coverage_out.split("Agent time by model")[1].split("Review time")[0].splitlines()
        if line.strip() and not line.strip().startswith("cards")
    ]
    assert len(model_table_lines) == 1
    assert "Opus 5.5" in model_table_lines[0] and "5" in model_table_lines[0]

    # 4. Test histos status:
    # Status estimates the open card from all 5 mixed samples
    capsys.readouterr()
    assert cmd_status(Namespace()) == 0
    status_out = capsys.readouterr().out
    assert "cod-open1" in status_out
    assert "agent ~1 h (≤1 h)" in status_out

    # Check that open card's stored estimate basis unified the 5 cards into Opus 5.5
    open_meta, _ = frontmatter.read(active_vault / "content" / "cod-open1.md")
    assert open_meta["estimate"]["agent_hours"]["basis"] == "Opus 5.5 · exec · 5 cards"
    # BUT the open card's frontmatter preserved raw "Claude Opus 5.5"
    assert open_meta["approved_model"] == "Claude Opus 5.5"

    # 5. Approve cards in active vault with mixed models and verify report
    run(monkeypatch, active_vault, "add-card", "cod-c1", "--title", "card 1")
    set_card_meta(active_vault, "cod-c1", proposed_tier="exec")
    run(monkeypatch, active_vault, "model", "cod-c1", "--approved", "Claude Opus 5.5", "--authorized")
    run(monkeypatch, active_vault, "assign", "cod-c1")
    draft1 = active_vault / "draft1.md"
    draft1.write_text("proposal body 1", encoding="utf-8")
    run(monkeypatch, active_vault, "propose", "cod-c1", "--file", str(draft1))
    run(monkeypatch, active_vault, "approve", "cod-c1")

    run(monkeypatch, active_vault, "add-card", "cod-c2", "--title", "card 2")
    set_card_meta(active_vault, "cod-c2", proposed_tier="exec")
    run(monkeypatch, active_vault, "model", "cod-c2", "--approved", "Opus 5.5", "--authorized")
    run(monkeypatch, active_vault, "assign", "cod-c2")
    draft2 = active_vault / "draft2.md"
    draft2.write_text("proposal body 2", encoding="utf-8")
    run(monkeypatch, active_vault, "propose", "cod-c2", "--file", str(draft2))
    run(monkeypatch, active_vault, "approve", "cod-c2")

    # Generate report
    rep_res = report.write_report(active_vault)
    report_text = rep_res.output.read_text(encoding="utf-8")
    assert "### By Model" in report_text
    # Report by_model table has ONE row for Opus 5.5 aggregating both cards (approved: 2)
    model_section = report_text.split("### By Model")[1].split("## 4. Critical Path Drift")[0]
    model_rows = [line for line in model_section.splitlines() if line.startswith("|") and not line.startswith("| Model") and not line.startswith("|---")]
    assert len(model_rows) == 1
    assert "| Opus 5.5 | 2 | 0 | 0.0% |" in model_rows[0]

    # 6. Explicitly assert events.jsonl and content/<id>.md frontmatter preserve the raw original strings byte-for-byte!
    events_log = (active_vault / ".histos" / "events.jsonl").read_text(encoding="utf-8")
    assert '"model": "Claude Opus 5.5"' in events_log
    assert '"model": "Opus 5.5"' in events_log

    meta_c1, _ = frontmatter.read(active_vault / "content" / "cod-c1.md")
    assert meta_c1["approved_model"] == "Claude Opus 5.5"

    meta_c2, _ = frontmatter.read(active_vault / "content" / "cod-c2.md")
    assert meta_c2["approved_model"] == "Opus 5.5"

    meta_open, _ = frontmatter.read(active_vault / "content" / "cod-open1.md")
    assert meta_open["approved_model"] == "Claude Opus 5.5"

    # In history vault too:
    hist_events_log = (hist_vault / ".histos" / "events.jsonl").read_text(encoding="utf-8")
    assert '"model": "Claude Opus 5.5"' in hist_events_log
    assert '"model": "Opus 5.5"' in hist_events_log


# --- Custom model_aliases in histos.config.yaml -------------------------------------------

def test_custom_model_aliases_in_config(tmp_path, monkeypatch, capsys):
    run(monkeypatch, tmp_path, "init")

    # Write custom model_aliases into histos.config.yaml
    cfg_path = tmp_path / "histos.config.yaml"
    cfg_data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    cfg_data["model_aliases"] = {
        "fast-worker": "Haiku 5.5",
        "heavy-thinker": "Opus 5.5",
    }
    cfg_path.write_text(yaml.dump(cfg_data), encoding="utf-8")

    # Validate config
    assert run(monkeypatch, tmp_path, "validate") == 0
    loaded_cfg = config.load(tmp_path)
    assert loaded_cfg is not None
    assert loaded_cfg.model_aliases == {
        "fast-worker": "Haiku 5.5",
        "heavy-thinker": "Opus 5.5",
    }

    # Verify metrics and estimates use the aliases
    samples = [
        make_sample(0, "c0", "fast-worker"),
        make_sample(1, "c1", "Haiku 5.5"),
        make_sample(2, "c2", "heavy-thinker"),
    ]
    rows = estimates.Rows([Row(s, "cod") for s in samples])
    cov = estimates.agent_model_coverage(rows, aliases=loaded_cfg.model_aliases)
    assert cov == [("Haiku 5.5", 2), ("Opus 5.5", 1)]

    # Metrics by_model grouping with custom aliases
    cards_data = {
        "nodes": [
            {"id": "c0", "type": "file", "color": "4", "file": "content/c0.md"},
            {"id": "c1", "type": "file", "color": "4", "file": "content/c1.md"},
        ],
        "edges": [],
    }
    cards_meta = {
        "c0": {"approved_model": "fast-worker"},
        "c1": {"approved_model": "Haiku 5.5"},
    }
    proj_metrics = metrics.compute(
        "test_vault",
        cards_data,
        cards_meta,
        samples[:2],
        rows,
        [],
        aliases=loaded_cfg.model_aliases,
    )
    assert len(proj_metrics.by_model) == 1
    assert proj_metrics.by_model[0].key == "Haiku 5.5"
    assert proj_metrics.by_model[0].approved == 2
