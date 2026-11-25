"""Tests for `histos report` (`src/histos/report.py`)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from conftest import run

from histos import frontmatter, report


def _draft(vault: Path, name: str, text: str = "Approved content body.\n") -> str:
    p = vault.parent / f"{vault.name}-{name}.md"
    p.write_text(text, encoding="utf-8")
    return str(p)


def _approve_card(monkeypatch, vault: Path, card_id: str, title: str):
    assert run(monkeypatch, vault, "add-card", card_id, "--title", title,
               "--description", "Edit module.py for this feature.",
               "--done-when", f"{card_id} reviewed and approved.") == 0
    assert run(monkeypatch, vault, "classify", card_id) == 0
    assert run(monkeypatch, vault, "assign", card_id, "--worker", "parent") == 0
    assert run(monkeypatch, vault, "propose", card_id, "--file", _draft(vault, card_id)) == 0
    assert run(monkeypatch, vault, "approve", card_id) == 0


def test_report_empty_vault_creates_report_without_lock_or_events(initialized_vault, monkeypatch, capsys):
    events_before = (initialized_vault / ".histos" / "events.jsonl").read_text(encoding="utf-8")
    lock_before = (initialized_vault / ".histos.lock").exists()

    rc = run(monkeypatch, initialized_vault, "report")
    out = capsys.readouterr().out
    assert rc == 0
    assert "REPORT.md" in out

    text = (initialized_vault / "REPORT.md").read_text(encoding="utf-8")
    assert "## 1. Executive Summary" in text
    assert "No approved cards with timeline timestamps yet." in text
    assert (initialized_vault / ".histos" / "events.jsonl").read_text(encoding="utf-8") == events_before
    assert (initialized_vault / ".histos.lock").exists() == lock_before


def test_report_with_closed_cards_gantt_pert_and_include_archived(initialized_vault, monkeypatch, capsys):
    for i in range(1, 6):
        _approve_card(monkeypatch, initialized_vault, f"cod-{i}", f"Code {i}")
    assert run(monkeypatch, initialized_vault, "add-card", "cod-6", "--title", "Open 6",
               "--description", "Edit module.py for open 6.",
               "--done-when", "Open 6 checked and ready.") == 0
    assert run(monkeypatch, initialized_vault, "classify", "cod-6") == 0

    assert run(monkeypatch, initialized_vault, "archive", "cod-1", "cod-2") == 0
    capsys.readouterr()

    # Without --include-archived: only 4 cards on project.canvas (3 approved + 1 open)
    assert run(monkeypatch, initialized_vault, "report", "--output", "reports/partial.md") == 0
    assert "3/4 approved" in capsys.readouterr().out
    partial = (initialized_vault / "reports" / "partial.md").read_text(encoding="utf-8")
    assert "```mermaid\ngantt" in partial
    assert "cod-3 :done, cod-3," in partial
    assert "cod-1 :done, cod-1," not in partial

    # With --include-archived: all 6 cards (5 approved + 1 open), so PERT has MIN_SAMPLES=5!
    assert run(monkeypatch, initialized_vault, "report", "--include-archived") == 0
    assert "5/6 approved" in capsys.readouterr().out
    full = (initialized_vault / "REPORT.md").read_text(encoding="utf-8")
    assert "cod-1 :done, cod-1," in full
    assert "| cod-6 |" in full
    assert "**Remaining critical path PERT:**" in full
    assert "no data (insufficient" not in full


def test_report_rejects_unsafe_or_protected_outputs(initialized_vault, monkeypatch, capsys):
    for bad in ("../escape.md", "content/cod-1.md", "project.canvas", "PROJECT.md", "evals/out.md", ""):
        assert run(monkeypatch, initialized_vault, "report", "--output", bad) == 1
        capsys.readouterr()
