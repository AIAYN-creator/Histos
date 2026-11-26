"""Tests for opt-in external executors (`dec-executors-optin`, `cod-executors-cli`)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml
from conftest import run

from histos import config, estimates, events, frontmatter, history, metrics, operations
from histos.errors import HistosError

BASE_CONFIG = """\
tiers:
  - name: reasoning
    heuristics:
      - "architecture_terms_present"
      - "token_count >= 2000"
  - name: exec
    heuristics: ["code_blocks_present"]
  - name: fast
    match: all
    heuristics: ["token_count < 200", "no_code_blocks"]
"""


def _write_cfg(vault: Path, extra: str = "") -> None:
    (vault / config.CONFIG_FILENAME).write_text(BASE_CONFIG + extra, encoding="utf-8")


def _sample(
    card: str,
    *,
    agent_hours: float,
    review_hours: float = 0.5,
    tier: str = "exec",
    model: str | None = "Sonnet 5.5",
    executor: str | None = None,
    deps: int = 0,
    day: int = 1,
) -> history.Sample:
    return history.Sample(
        vault="v1",
        card=card,
        approved_at=datetime(2026, 11, day, 12, 0, tzinfo=timezone.utc),
        agent_hours=agent_hours,
        review_hours=review_hours,
        review_active_hours=review_hours,
        rounds=1,
        rejections=0,
        tier=tier,
        model=model,
        dependencies=deps,
        worker_kind="parent",
        approximate=False,
        executor=executor,
    )


def _draft(vault: Path, name: str, text: str = "Approved content.\n") -> str:
    path = vault.parent / f"{vault.name}-{name}.md"
    path.write_text(text, encoding="utf-8")
    return str(path)


# --- Config & schema validation -------------------------------------------------------------

def test_executors_disabled_by_default(tmp_path):
    _write_cfg(tmp_path)
    cfg = config.load(tmp_path)
    assert cfg is not None
    assert cfg.executors.enabled is False
    assert cfg.executors.allowed == []
    assert cfg.executors.find("gemini-cli") is None


def test_executors_valid_config_loads(tmp_path):
    _write_cfg(
        tmp_path,
        """\
executors:
  enabled: true
  allowed:
    - name: gemini-cli
      provider: google
      pricing_tier: free
    - name: claude-code
      provider: anthropic
      pricing_tier: paid
""",
    )
    cfg = config.load(tmp_path)
    assert cfg.executors.enabled is True
    assert [e.name for e in cfg.executors.allowed] == ["gemini-cli", "claude-code"]
    assert cfg.executors.find("gemini-cli").pricing_tier == "free"
    assert cfg.executors.find("claude-code").provider == "anthropic"


@pytest.mark.parametrize("snippet,frag", [
    ("executors:\n  enabled: true\n", "allowed"),
    ("executors:\n  enabled: true\n  allowed: []\n", "allowed"),
    ("executors:\n  enabled: false\n  unknown_key: 1\n", "Additional properties"),
    ("executors:\n  enabled: true\n  allowed:\n    - name: bad name\n      pricing_tier: free\n", "does not match"),
    ("executors:\n  enabled: true\n  allowed:\n    - name: cli\n      pricing_tier: enterprise\n", "is not one of"),
    (
        "executors:\n  enabled: true\n  allowed:\n"
        "    - name: cli\n      pricing_tier: free\n"
        "    - name: cli\n      pricing_tier: paid\n",
        "duplicate executor name",
    ),
])
def test_executors_invalid_config_rejected(tmp_path, snippet, frag):
    _write_cfg(tmp_path, snippet)
    errors = config.validate(tmp_path)
    assert any(frag in e for e in errors), errors
    with pytest.raises(HistosError):
        config.load(tmp_path)


# --- CLI `histos model` & opt-in guard ------------------------------------------------------

def test_model_executor_blocked_when_disabled_or_unconfigured(initialized_vault, monkeypatch, capsys):
    run(monkeypatch, initialized_vault, "add-card", "cod-1", "--title", "Cod 1")
    capsys.readouterr()

    # No config / executors disabled by default
    assert run(monkeypatch, initialized_vault, "model", "cod-1", "--proposed", "Sonnet 5.5", "--executor", "gemini-cli") == 1
    assert "executors are disabled" in capsys.readouterr().err

    _write_cfg(
        initialized_vault,
        "executors:\n  enabled: false\n  allowed:\n    - name: gemini-cli\n      pricing_tier: free\n",
    )
    assert run(monkeypatch, initialized_vault, "model", "cod-1", "--proposed-executor", "gemini-cli") == 1
    assert "executors are disabled" in capsys.readouterr().err


def test_model_executor_rejects_unlisted_and_requires_authorized(initialized_vault, monkeypatch, capsys):
    _write_cfg(
        initialized_vault,
        "executors:\n  enabled: true\n  allowed:\n"
        "    - name: gemini-cli\n      provider: google\n      pricing_tier: free\n"
        "    - name: claude-code\n      provider: anthropic\n      pricing_tier: paid\n",
    )
    run(monkeypatch, initialized_vault, "add-card", "cod-1", "--title", "Cod 1")
    capsys.readouterr()

    assert run(monkeypatch, initialized_vault, "model", "cod-1", "--proposed-executor", "unknown-cli") == 1
    assert "not in executors.allowed" in capsys.readouterr().err

    assert run(monkeypatch, initialized_vault, "model", "cod-1", "--approved-executor", "claude-code") == 1
    assert "needs explicit authorization" in capsys.readouterr().err

    assert run(monkeypatch, initialized_vault, "model", "cod-1", "--executor", "claude-code") == 1
    assert "must be paired" in capsys.readouterr().err


def test_model_executor_propose_approve_warning_clear_and_undo(initialized_vault, monkeypatch, capsys):
    _write_cfg(
        initialized_vault,
        "executors:\n  enabled: true\n  allowed:\n"
        "    - name: gemini-cli\n      provider: google\n      pricing_tier: free\n"
        "    - name: claude-code\n      provider: anthropic\n      pricing_tier: paid\n",
    )
    run(monkeypatch, initialized_vault, "add-card", "cod-1", "--title", "Cod 1")
    capsys.readouterr()

    # Propose model + free executor -> emits both events and free-tier warning
    assert run(
        monkeypatch, initialized_vault,
        "model", "cod-1", "--proposed", "Gemini 3.8 Flash (High)", "--executor", "gemini-cli",
    ) == 0
    io = capsys.readouterr()
    assert "proposed_model = Gemini 3.8 Flash (High)" in io.out
    assert "proposed_executor = gemini-cli" in io.out
    assert "free pricing tier" in io.err

    # Approve paid executor -> no privacy warning
    assert run(
        monkeypatch, initialized_vault,
        "model", "cod-1", "--approved", "Sonnet 5.5", "--executor", "claude-code", "--authorized",
    ) == 0
    io = capsys.readouterr()
    assert "approved_executor = claude-code" in io.out
    assert "free pricing tier" not in io.err

    meta = frontmatter.read(initialized_vault / "content" / "cod-1.md")[0]
    assert meta["proposed_executor"] == "gemini-cli"
    assert meta["approved_executor"] == "claude-code"

    ev_names = [e["event"] for e in events.read(initialized_vault)]
    assert "executor_proposed" in ev_names
    assert "executor_approved" in ev_names

    # Clear executor + undo restores it
    assert run(monkeypatch, initialized_vault, "model", "cod-1", "--clear-executor") == 0
    capsys.readouterr()
    meta_cleared = frontmatter.read(initialized_vault / "content" / "cod-1.md")[0]
    assert "proposed_executor" not in meta_cleared
    assert "approved_executor" not in meta_cleared
    assert events.read(initialized_vault)[-1]["event"] == "executor_cleared"

    assert run(monkeypatch, initialized_vault, "undo") == 0
    capsys.readouterr()
    meta_restored = frontmatter.read(initialized_vault / "content" / "cod-1.md")[0]
    assert meta_restored["approved_executor"] == "claude-code"


def test_classify_tier_change_clears_and_logs_executor_cleared(initialized_vault, monkeypatch, capsys):
    _write_cfg(
        initialized_vault,
        "executors:\n  enabled: true\n  allowed:\n    - name: claude-code\n      pricing_tier: paid\n",
    )
    run(monkeypatch, initialized_vault, "add-card", "cod-1", "--title", "Cod 1", "--description", "Quick note.")
    assert run(monkeypatch, initialized_vault, "classify", "cod-1") == 0
    assert run(monkeypatch, initialized_vault, "model", "cod-1", "--approved-executor", "claude-code", "--authorized") == 0
    # Change card text so classify shifts fast -> reasoning
    md_path = initialized_vault / "content" / "cod-1.md"
    m, _ = frontmatter.read(md_path)
    m["description"] = "Architecture redesign after traceback."
    frontmatter.write(md_path, m, "# Architecture redesign\n\n```python\nprint(1)\n```\n")
    capsys.readouterr()

    assert run(monkeypatch, initialized_vault, "classify", "cod-1") == 0
    err = capsys.readouterr().err
    assert "cleared" in err
    m2 = frontmatter.read(md_path)[0]
    assert "approved_executor" not in m2
    assert any(e["event"] == "executor_cleared" and e.get("data", {}).get("executor") == "claude-code"
               for e in events.read(initialized_vault))


# --- History reconstruction -----------------------------------------------------------------

def test_history_reconstructs_executor_from_events(initialized_vault, monkeypatch, capsys):
    _write_cfg(
        initialized_vault,
        "executors:\n  enabled: true\n  allowed:\n    - name: gemini-cli\n      pricing_tier: free\n",
    )
    run(monkeypatch, initialized_vault, "add-card", "cod-1", "--title", "Cod 1")
    run(monkeypatch, initialized_vault, "assign", "cod-1", "--worker", "parent")
    run(monkeypatch, initialized_vault, "model", "cod-1", "--approved", "Sonnet 5.5", "--executor", "gemini-cli", "--authorized")
    run(monkeypatch, initialized_vault, "propose", "cod-1", "--file", _draft(initialized_vault, "cod-1"))
    run(monkeypatch, initialized_vault, "approve", "cod-1")
    capsys.readouterr()

    [sample] = history.vault_samples(initialized_vault)
    assert sample.executor == "gemini-cli"


# --- Estimates cascade ----------------------------------------------------------------------

def test_estimates_cascade_executor_model_then_executor_then_model_then_global():
    # (claude-code, Sonnet 5.5) -> 5 cards around 1.0h
    # (claude-code, Opus 5.5) -> 3 cards around 3.0h (< 5, so claude-code combined = 8 cards)
    # (gemini-cli, Opus 5.5) -> 5 cards around 2.0h
    # (other / None, Haiku 5.5) -> 5 cards around 5.0h
    samples = [
        *(_sample(f"cod-{i}", agent_hours=1.0, model="Sonnet 5.5", executor="claude-code", day=i + 1) for i in range(5)),
        *(_sample(f"cod-o{i}", agent_hours=3.0, model="Opus 5.5", executor="claude-code", day=i + 6) for i in range(3)),
        *(_sample(f"cod-g{i}", agent_hours=2.0, model="Opus 5.5", executor="gemini-cli", day=i + 9) for i in range(5)),
        *(_sample(f"cod-h{i}", agent_hours=5.0, model="Haiku 5.5", executor=None, day=i + 14) for i in range(5)),
    ]
    rows = estimates.Rows(estimates.Row(s, "cod") for s in samples)

    # 1. Exact (executor, model) reaches MIN_SAMPLES=5
    e1 = estimates.for_card(rows, "exec", "cod", 0, model="Sonnet 5.5", executor="claude-code")
    assert isinstance(e1.agent, estimates.Estimate)
    assert e1.agent.median == pytest.approx(1.0)
    assert "claude-code" in e1.agent.basis and "Sonnet 5.5" in e1.agent.basis

    # 2. (claude-code, Opus 5.5) only has 3 -> falls back to executor='claude-code' (8 cards: median of [1,1,1,1,1,3,3,3] = 1.0)
    e2 = estimates.for_card(rows, "exec", "cod", 0, model="Opus 5.5", executor="claude-code")
    assert isinstance(e2.agent, estimates.Estimate)
    assert e2.agent.basis.startswith("claude-code · exec")

    # 3. Unknown executor 'codex' has <5 cards -> falls back to model='Haiku 5.5'
    e3 = estimates.for_card(rows, "exec", "cod", 0, model="Haiku 5.5", executor="codex")
    assert isinstance(e3.agent, estimates.Estimate)
    assert e3.agent.median == pytest.approx(5.0)
    assert e3.agent.basis.startswith("Haiku 5.5 · exec")

    # 4. Neither executor nor model reaches 5 -> falls back to global tier/type
    e4 = estimates.for_card(rows, "exec", "cod", 0, model="GPT-5", executor="codex")
    assert isinstance(e4.agent, estimates.Estimate)
    assert e4.agent.basis.startswith("exec · cod")


# --- Status, coverage, metrics, report ------------------------------------------------------

def test_coverage_metrics_and_report_include_executors_and_stay_compatible(initialized_vault, monkeypatch, capsys):
    _write_cfg(
        initialized_vault,
        "executors:\n  enabled: true\n  allowed:\n    - name: gemini-cli\n      provider: google\n      pricing_tier: free\n",
    )
    for i in range(1, 6):
        cid = f"cod-{i}"
        run(monkeypatch, initialized_vault, "add-card", cid, "--title", f"Cod {i}", "--description", "Edit x.py.")
        run(monkeypatch, initialized_vault, "classify", cid)
        run(monkeypatch, initialized_vault, "assign", cid, "--worker", "parent")
        run(monkeypatch, initialized_vault, "model", cid, "--approved", "Sonnet 5.5", "--executor", "gemini-cli", "--authorized")
        run(monkeypatch, initialized_vault, "propose", cid, "--file", _draft(initialized_vault, cid))
        run(monkeypatch, initialized_vault, "approve", cid)

    run(monkeypatch, initialized_vault, "add-card", "cod-open", "--title", "Open", "--description", "Edit y.py.")
    run(monkeypatch, initialized_vault, "classify", "cod-open")
    run(monkeypatch, initialized_vault, "model", "cod-open", "--approved", "Sonnet 5.5", "--executor", "gemini-cli", "--authorized")
    capsys.readouterr()

    assert run(monkeypatch, initialized_vault, "status") == 0
    status_out = capsys.readouterr().out
    assert "executor gemini-cli" in status_out
    assert "gemini-cli · Sonnet 5.5" in status_out or "gemini-cli" in status_out

    assert run(monkeypatch, initialized_vault, "estimate", "--coverage") == 0
    cov_out = capsys.readouterr().out
    assert "Agent time by executor" in cov_out
    assert "gemini-cli" in cov_out

    proj_metrics = metrics.from_vault(initialized_vault)
    assert [g.key for g in proj_metrics.by_executor] == ["gemini-cli"]
    assert proj_metrics.by_executor[0].approved == 5

    assert run(monkeypatch, initialized_vault, "report") == 0
    capsys.readouterr()
    rep = (initialized_vault / "REPORT.md").read_text(encoding="utf-8")
    assert "### By Executor" in rep
    assert "| gemini-cli | 5 | 0 | 0.0% |" in rep
