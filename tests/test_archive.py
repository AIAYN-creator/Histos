"""Tests for `histos archive` (`src/histos/archive.py`)."""
from __future__ import annotations

import json
from pathlib import Path

from conftest import run

from histos import archive, canvas, events, frontmatter, history


def _draft(vault: Path, name: str, text: str = "Approved content body.\n") -> str:
    p = vault.parent / f"{vault.name}-{name}.md"
    p.write_text(text, encoding="utf-8")
    return str(p)


def _approve_card(monkeypatch, vault: Path, card_id: str, title: str, *, depends_on: list[str] | None = None):
    args = ["add-card", card_id, "--title", title, "--done-when", f"{card_id} reviewed and accepted."]
    if depends_on:
        args += ["--depends-on", *depends_on, "--authorized"]
    assert run(monkeypatch, vault, *args) == 0
    assert run(monkeypatch, vault, "assign", card_id, "--worker", "parent") == 0
    assert run(monkeypatch, vault, "propose", card_id, "--file", _draft(vault, card_id)) == 0
    assert run(monkeypatch, vault, "approve", card_id) == 0


def test_archive_eligibility_and_refusal_on_open_dependents(initialized_vault, monkeypatch, capsys):
    _approve_card(monkeypatch, initialized_vault, "dec-1", "Decision")
    _approve_card(monkeypatch, initialized_vault, "cod-1", "Done code", depends_on=["dec-1"])
    assert run(monkeypatch, initialized_vault, "add-card", "cod-2", "--title", "Open",
               "--done-when", "Still open.", "--depends-on", "cod-1", "--authorized") == 0
    capsys.readouterr()

    # dec-1 has only Approved cod-1 depending on it -> eligible; cod-1 has open cod-2 -> NOT eligible
    elig = [c["id"] for c in archive.eligible_cards(canvas.load(initialized_vault), initialized_vault)]
    assert elig == ["dec-1"]

    # Refuses archiving cod-1 explicitly because cod-2 is open
    assert run(monkeypatch, initialized_vault, "archive", "cod-1") == 1
    assert "open dependent(s): cod-2" in capsys.readouterr().err

    # Refuses archiving open card cod-2
    assert run(monkeypatch, initialized_vault, "archive", "cod-2") == 1
    assert "isn't Approved" in capsys.readouterr().err


def test_archive_dry_run_and_incremental_archive_preserve_history_and_validate(initialized_vault, monkeypatch, capsys):
    _approve_card(monkeypatch, initialized_vault, "dec-1", "Decision")
    _approve_card(monkeypatch, initialized_vault, "cod-1", "Impl", depends_on=["dec-1"])
    samples_before = history.vault_samples(initialized_vault)
    assert len(samples_before) == 2

    # Dry run changes nothing
    capsys.readouterr()
    assert run(monkeypatch, initialized_vault, "archive", "--all-done", "--dry-run") == 0
    assert "dry run -- would archive 2 card(s)" in capsys.readouterr().out
    assert not (initialized_vault / "archive.canvas").exists()
    assert len(canvas.cards(canvas.load(initialized_vault))) == 2

    # Archive dec-1 first, then cod-1 incrementally into the same archive.canvas
    assert run(monkeypatch, initialized_vault, "archive", "dec-1") == 0
    assert run(monkeypatch, initialized_vault, "archive", "cod-1") == 0
    assert run(monkeypatch, initialized_vault, "validate") == 0

    main_data = canvas.load(initialized_vault)
    assert canvas.cards(main_data) == []

    arch_data = json.loads((initialized_vault / "archive.canvas").read_text(encoding="utf-8"))
    assert {n["id"] for n in arch_data["nodes"]} == {"dec-1", "cod-1"}
    assert len(arch_data["edges"]) == 1
    assert arch_data["edges"][0]["fromNode"] == "dec-1" and arch_data["edges"][0]["toNode"] == "cod-1"

    # cod-1 kept dec-1 in implied_dependencies when dec-1 was archived first
    meta, _ = frontmatter.read(initialized_vault / "content" / "cod-1.md")
    assert "dec-1" in (meta.get("implied_dependencies") or [])

    # Content files, approved copies, event log and history samples stay intact
    assert (initialized_vault / "content" / "dec-1.md").exists()
    assert (initialized_vault / "approved" / "cod-1.md").exists()
    assert history.vault_samples(initialized_vault) == samples_before
    assert events.check_log(initialized_vault)[0] == []


def test_archive_path_traversal_and_bad_args_rejected(initialized_vault, monkeypatch, capsys):
    _approve_card(monkeypatch, initialized_vault, "dec-1", "Decision")
    capsys.readouterr()

    for bad_name in ("../escape.canvas", "project.canvas", "notes.md", ""):
        assert run(monkeypatch, initialized_vault, "archive", "dec-1", "--name", bad_name) == 1
        capsys.readouterr()

    # Passing neither ids nor --all-done is refused
    assert run(monkeypatch, initialized_vault, "archive") == 1
    assert "pass card ids or --all-done" in capsys.readouterr().err
