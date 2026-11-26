"""Tests for atomic `histos undo` (`src/histos/undo.py`, `dec-undo-spec`)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import run

from histos import canvas, events, frontmatter, operations, pauses, undo
from histos.cli import build_parser


def _draft(vault: Path, name: str = "draft.md", text: str = "# Card\n\nReal approved-quality content.\n") -> str:
    p = vault.parent / f"{vault.name}-{name}"
    p.write_text(text, encoding="utf-8")
    return str(p)


def _log_lines(vault: Path) -> list[str]:
    path = events.log_path(vault)
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def _assert_append_only(before_lines: list[str], vault: Path, expected_reverted: str) -> dict:
    after_lines = _log_lines(vault)
    assert len(after_lines) == len(before_lines) + 1
    assert after_lines[:-1] == before_lines  # strictly append-only: prior lines untouched
    last = json.loads(after_lines[-1])
    assert last["event"] == "undone"
    assert last["data"]["reverted_event"] == expected_reverted
    assert last["data"]["reverted_ts"]
    assert events.check_log(vault) == ([], [])
    return last


def test_cli_has_no_force_flag():
    parser = build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["undo", "--force"])
    assert exc.value.code != 0


def test_undo_add_card_removes_node_and_file_and_validates(initialized_vault, monkeypatch, capsys):
    v = initialized_vault
    assert run(monkeypatch, v, "add-card", "cap1", "--title", "Cap 1", "--description", "First card") == 0
    assert (v / "content" / "cap1.md").exists()
    before_lines = _log_lines(v)
    capsys.readouterr()

    assert run(monkeypatch, v, "undo") == 0
    out = capsys.readouterr().out
    assert "undone card_created" in out
    assert "cap1" in out

    assert canvas.find_card(canvas.load(v), "cap1") is None
    assert not (v / "content" / "cap1.md").exists()
    assert not undo.snapshot_path(v).exists()
    last = _assert_append_only(before_lines, v, "card_created")
    assert last["card"] == "cap1"
    assert last["transitions"] == [{"card": "cap1", "from": "Backlog", "to": None}]
    assert run(monkeypatch, v, "validate") == 0


def test_undo_link_unblocks_card_and_removes_edge(initialized_vault, monkeypatch):
    v = initialized_vault
    run(monkeypatch, v, "add-card", "a", "--title", "A")
    run(monkeypatch, v, "add-card", "b", "--title", "B")
    run(monkeypatch, v, "assign", "b")
    assert canvas.find_card(canvas.load(v), "b")["color"] == canvas.IN_PROGRESS

    assert run(monkeypatch, v, "link", "b", "--depends-on", "a", "--authorized") == 0
    assert canvas.find_card(canvas.load(v), "b")["color"] == canvas.BLOCKED
    before_lines = _log_lines(v)

    assert run(monkeypatch, v, "undo") == 0
    data = canvas.load(v)
    assert canvas.find_card(data, "b")["color"] == canvas.IN_PROGRESS
    assert canvas.dependency_edges(data) == []
    _assert_append_only(before_lines, v, "dependency_added")
    assert run(monkeypatch, v, "validate") == 0


def test_undo_describe_assign_classify_and_model(initialized_vault, monkeypatch, tmp_path):
    v = initialized_vault
    src = tmp_path / "notes.md"
    src.write_text("source material\n", encoding="utf-8")
    run(monkeypatch, v, "add-card", "a", "--title", "A")
    run(monkeypatch, v, "add-card", "b", "--title", "B")

    # describe
    before_card = dict(canvas.find_card(canvas.load(v), "a"))
    assert run(monkeypatch, v, "describe", "a", "--text", "X" * 180, "--sources", str(src)) == 0
    assert canvas.find_card(canvas.load(v), "a")["height"] > before_card["height"]
    before_lines = _log_lines(v)
    assert run(monkeypatch, v, "undo") == 0
    _assert_append_only(before_lines, v, "described")
    meta, _ = frontmatter.read(v / "content" / "a.md")
    assert meta["description"] is None
    assert meta["sources"] == []
    assert canvas.find_card(canvas.load(v), "a")["height"] == before_card["height"]

    # assign (multi-card)
    assert run(monkeypatch, v, "assign", "a", "b", "--worker", "sub-1") == 0
    before_lines = _log_lines(v)
    assert run(monkeypatch, v, "undo") == 0
    _assert_append_only(before_lines, v, "assigned")
    for cid in ("a", "b"):
        assert canvas.find_card(canvas.load(v), cid)["color"] == canvas.BACKLOG
        m, _ = frontmatter.read(v / "content" / f"{cid}.md")
        assert m.get("worker") is None
        assert m.get("assigned_to") is None

    # classify
    assert run(monkeypatch, v, "classify", "a") == 0
    m, _ = frontmatter.read(v / "content" / "a.md")
    assert m.get("proposed_tier") in ("fast", "exec", "reasoning")
    before_lines = _log_lines(v)
    assert run(monkeypatch, v, "undo") == 0
    _assert_append_only(before_lines, v, "classified")
    m, _ = frontmatter.read(v / "content" / "a.md")
    assert "proposed_tier" not in m

    # model --proposed / --approved / --clear
    run(monkeypatch, v, "classify", "a")
    assert run(monkeypatch, v, "model", "a", "--proposed", "Sonnet 5.5") == 0
    before_lines = _log_lines(v)
    assert run(monkeypatch, v, "undo") == 0
    _assert_append_only(before_lines, v, "model_proposed")
    assert "proposed_model" not in frontmatter.read(v / "content" / "a.md")[0]

    assert run(monkeypatch, v, "model", "a", "--approved", "Opus 5.5", "--authorized") == 0
    before_lines = _log_lines(v)
    assert run(monkeypatch, v, "undo") == 0
    _assert_append_only(before_lines, v, "model_approved")
    assert "approved_model" not in frontmatter.read(v / "content" / "a.md")[0]

    run(monkeypatch, v, "model", "a", "--proposed", "Sonnet 5.5")
    assert run(monkeypatch, v, "model", "a", "--clear") == 0
    before_lines = _log_lines(v)
    assert run(monkeypatch, v, "undo") == 0
    _assert_append_only(before_lines, v, "model_cleared")
    assert frontmatter.read(v / "content" / "a.md")[0].get("proposed_model") == "Sonnet 5.5"
    assert run(monkeypatch, v, "validate") == 0


def test_undo_propose_and_proposal_refused_and_accept_edit_and_implement(initialized_vault, monkeypatch):
    v = initialized_vault
    run(monkeypatch, v, "add-card", "a", "--title", "A")
    run(monkeypatch, v, "assign", "a")

    # proposal_refused (failed evals) -> undo reverts eval_results attempt
    assert run(monkeypatch, v, "propose", "a", "--file", _draft(v, "bad.md", "# A\n\nTODO\n")) == 1
    meta, _ = frontmatter.read(v / "content" / "a.md")
    assert meta["eval_results"]["attempt"] == 1
    before_lines = _log_lines(v)
    assert run(monkeypatch, v, "undo") == 0
    _assert_append_only(before_lines, v, "proposal_refused")
    meta, _ = frontmatter.read(v / "content" / "a.md")
    assert "eval_results" not in meta

    # proposed -> undo removes proposal file and restores In progress
    assert run(monkeypatch, v, "propose", "a", "--file", _draft(v, "good.md")) == 0
    assert (v / "proposals" / "a.md").exists()
    assert canvas.find_card(canvas.load(v), "a")["color"] == canvas.PENDING_REVIEW
    before_lines = _log_lines(v)
    assert run(monkeypatch, v, "undo") == 0
    _assert_append_only(before_lines, v, "proposed")
    assert not (v / "proposals" / "a.md").exists()
    assert canvas.find_card(canvas.load(v), "a")["color"] == canvas.IN_PROGRESS

    # accept-edit -> undo restores old content_hash
    md_path = v / "content" / "a.md"
    old_meta, body = frontmatter.read(md_path)
    old_hash = old_meta[frontmatter.CONTENT_HASH_KEY]
    frontmatter.write(md_path, old_meta, body + "\nManual edit.\n")
    assert run(monkeypatch, v, "accept-edit", "a") == 0
    assert frontmatter.read(md_path)[0][frontmatter.CONTENT_HASH_KEY] != old_hash
    before_lines = _log_lines(v)
    assert run(monkeypatch, v, "undo") == 0
    _assert_append_only(before_lines, v, "edit_accepted")
    assert frontmatter.read(md_path)[0][frontmatter.CONTENT_HASH_KEY] == old_hash

    # Re-accept manual edit so evals pass, then approve & test implement undo
    assert run(monkeypatch, v, "accept-edit", "a") == 0
    assert run(monkeypatch, v, "propose", "a", "--file", _draft(v, "good2.md")) == 0
    assert run(monkeypatch, v, "approve", "a") == 0
    assert run(monkeypatch, v, "implement", "a") == 0
    before_lines = _log_lines(v)
    assert run(monkeypatch, v, "undo") == 0
    _assert_append_only(before_lines, v, "implementation_started")

    assert run(monkeypatch, v, "implement", "a") == 0
    assert run(monkeypatch, v, "implement", "a", "--done") == 0
    before_lines = _log_lines(v)
    assert run(monkeypatch, v, "undo") == 0
    _assert_append_only(before_lines, v, "implementation_done")
    assert run(monkeypatch, v, "validate") == 0


def test_undo_guide_propose_and_plan_propose(initialized_vault, monkeypatch):
    v = initialized_vault
    (v / "evals" / "guides.yaml").unlink()
    assert run(monkeypatch, v, "guide", "propose", "PROJECT.md", "--file", _draft(v, "g.md", "# P\n\nBody.\n")) == 0
    assert (v / "proposals" / "guides" / "PROJECT.md").exists()
    before_lines = _log_lines(v)
    assert run(monkeypatch, v, "undo") == 0
    _assert_append_only(before_lines, v, "guide_proposed")
    assert not (v / "proposals" / "guides" / "PROJECT.md").exists()

    plan_file = v.parent / f"{v.name}-plan.yaml"
    plan_file.write_text(
        "summary: Test plan\ncards:\n  - id: dec-1\n    title: First\n    done_when: First card is reviewed and approved.\n",
        encoding="utf-8",
    )
    assert run(monkeypatch, v, "plan", "propose", "--file", str(plan_file)) == 0
    assert (v / "proposals" / "plan.yaml").exists()
    before_lines = _log_lines(v)
    assert run(monkeypatch, v, "undo") == 0
    _assert_append_only(before_lines, v, "plan_proposed")
    assert not (v / "proposals" / "plan.yaml").exists()
    assert run(monkeypatch, v, "validate") == 0


def test_undo_arrange_and_archive(initialized_vault, monkeypatch):
    v = initialized_vault
    run(monkeypatch, v, "add-card", "a", "--title", "A")
    run(monkeypatch, v, "add-card", "b", "--title", "B", "--depends-on", "a", "--authorized")
    run(monkeypatch, v, "add-card", "c", "--title", "C", "--depends-on", "a", "--authorized")
    run(monkeypatch, v, "link", "c", "--depends-on", "b", "--authorized")  # a->c is redundant

    canvas_before = (v / "project.canvas").read_text(encoding="utf-8")
    assert run(monkeypatch, v, "arrange") == 0
    assert (v / "project.canvas.bak").exists()
    meta_c, _ = frontmatter.read(v / "content" / "c.md")
    assert "a" in (meta_c.get("implied_dependencies") or [])

    before_lines = _log_lines(v)
    assert run(monkeypatch, v, "undo") == 0
    _assert_append_only(before_lines, v, "arranged")
    assert (v / "project.canvas").read_text(encoding="utf-8") == canvas_before
    assert not (v / "project.canvas.bak").exists()
    meta_c, _ = frontmatter.read(v / "content" / "c.md")
    assert not (meta_c.get("implied_dependencies") or [])
    assert run(monkeypatch, v, "validate") == 0

    # Approve a, b, c so a has no open dependents, then archive a while b & c stay on project.canvas
    for cid in ("a", "b", "c"):
        run(monkeypatch, v, "assign", cid)
        run(monkeypatch, v, "propose", cid, "--file", _draft(v, f"{cid}.md"))
        run(monkeypatch, v, "approve", cid)

    canvas_before_arch = (v / "project.canvas").read_text(encoding="utf-8")
    assert run(monkeypatch, v, "archive", "a") == 0
    assert (v / "archive.canvas").exists()
    assert canvas.find_card(canvas.load(v), "a") is None
    assert "a" in (frontmatter.read(v / "content" / "b.md")[0].get("implied_dependencies") or [])

    before_lines = _log_lines(v)
    assert run(monkeypatch, v, "undo") == 0
    _assert_append_only(before_lines, v, "archived")
    assert not (v / "archive.canvas").exists()
    assert (v / "project.canvas").read_text(encoding="utf-8") == canvas_before_arch
    assert "a" not in (frontmatter.read(v / "content" / "b.md")[0].get("implied_dependencies") or [])
    assert run(monkeypatch, v, "validate") == 0


def test_hard_refusal_on_human_decisions_and_double_undo(initialized_vault, monkeypatch, capsys):
    v = initialized_vault
    (v / "evals" / "guides.yaml").unlink()

    # Empty / freshly initialized vault (vault_created)
    assert run(monkeypatch, v, "undo") == 1
    assert "vault_created" in capsys.readouterr().err

    # Card approve cannot be undone
    run(monkeypatch, v, "add-card", "a", "--title", "A")
    run(monkeypatch, v, "assign", "a")
    run(monkeypatch, v, "propose", "a", "--file", _draft(v, "a.md"))
    assert undo.snapshot_path(v).exists()
    assert run(monkeypatch, v, "approve", "a") == 0
    assert not undo.snapshot_path(v).exists()
    before_lines = _log_lines(v)
    capsys.readouterr()
    assert run(monkeypatch, v, "undo") == 1
    assert "approved" in capsys.readouterr().err
    assert _log_lines(v) == before_lines
    assert canvas.find_card(canvas.load(v), "a")["color"] == canvas.APPROVED

    # Card reject cannot be undone
    run(monkeypatch, v, "add-card", "b", "--title", "B")
    run(monkeypatch, v, "assign", "b")
    run(monkeypatch, v, "propose", "b", "--file", _draft(v, "b.md"))
    assert run(monkeypatch, v, "reject", "b", "--feedback", "nope") == 0
    assert not undo.snapshot_path(v).exists()
    capsys.readouterr()
    assert run(monkeypatch, v, "undo") == 1
    assert "rejected" in capsys.readouterr().err

    # Guide approve & guide reject cannot be undone
    run(monkeypatch, v, "guide", "propose", "PROJECT.md", "--file", _draft(v, "g1.md", "# P1\n\nBody.\n"))
    assert run(monkeypatch, v, "guide", "approve", "PROJECT.md") == 0
    capsys.readouterr()
    assert run(monkeypatch, v, "undo") == 1
    assert "guide_approved" in capsys.readouterr().err

    run(monkeypatch, v, "guide", "propose", "PROJECT.md", "--file", _draft(v, "g2.md", "# P2\n\nBody.\n"))
    assert run(monkeypatch, v, "guide", "reject", "PROJECT.md", "--feedback", "no") == 0
    capsys.readouterr()
    assert run(monkeypatch, v, "undo") == 1
    assert "guide_rejected" in capsys.readouterr().err

    # Plan approve & plan reject cannot be undone
    plan_file = v.parent / f"{v.name}-p.yaml"
    plan_file.write_text(
        "summary: Plan\ncards:\n  - id: p-1\n    title: P1\n    done_when: Plan card one is reviewed and done.\n",
        encoding="utf-8",
    )
    run(monkeypatch, v, "plan", "propose", "--file", str(plan_file))
    assert run(monkeypatch, v, "plan", "approve") == 0
    capsys.readouterr()
    assert run(monkeypatch, v, "undo") == 1
    assert "plan_approved" in capsys.readouterr().err

    plan_file.write_text(
        "summary: Plan 2\ncards:\n  - id: p-2\n    title: P2\n    done_when: Plan card two is reviewed and done.\n",
        encoding="utf-8",
    )
    run(monkeypatch, v, "plan", "propose", "--file", str(plan_file))
    assert run(monkeypatch, v, "plan", "reject", "--feedback", "skip") == 0
    capsys.readouterr()
    assert run(monkeypatch, v, "undo") == 1
    assert "plan_rejected" in capsys.readouterr().err

    # Double undo is refused
    run(monkeypatch, v, "add-card", "c", "--title", "C")
    assert run(monkeypatch, v, "undo") == 0
    before_lines = _log_lines(v)
    capsys.readouterr()
    assert run(monkeypatch, v, "undo") == 1
    assert "undone" in capsys.readouterr().err
    assert _log_lines(v) == before_lines


def test_refuses_manual_obsidian_color_drift_and_state_recomputed(initialized_vault, monkeypatch, capsys):
    v = initialized_vault
    run(monkeypatch, v, "add-card", "a", "--title", "A")
    assert undo.snapshot_path(v).exists()

    # Manual edit in Obsidian changing color directly -> immediate undo refuses
    data = canvas.load(v)
    canvas.find_card(data, "a")["color"] = canvas.APPROVED
    canvas.save(v, data)
    before_lines = _log_lines(v)
    capsys.readouterr()
    assert run(monkeypatch, v, "undo") == 1
    assert "outside histos" in capsys.readouterr().err
    assert _log_lines(v) == before_lines
    assert canvas.find_card(canvas.load(v), "a")["color"] == canvas.APPROVED

    # After `status` sees the drift and records `state_recomputed` -> undo still refuses
    assert run(monkeypatch, v, "status") == 0
    assert events.read(v)[-1]["event"] == "state_recomputed"
    capsys.readouterr()
    assert run(monkeypatch, v, "undo") == 1
    assert "state_recomputed" in capsys.readouterr().err


def test_refuses_empty_or_uninitialized_or_stale_and_preserves_across_readonly(tmp_path, initialized_vault, monkeypatch, capsys):
    # Uninitialized directory
    empty_dir = tmp_path / "empty-dir"
    empty_dir.mkdir()
    capsys.readouterr()
    assert run(monkeypatch, empty_dir, "undo") == 1
    assert "project.canvas" in capsys.readouterr().err

    # Empty event log
    v = initialized_vault
    events.log_path(v).unlink()
    undo._clear_snapshot(v)
    capsys.readouterr()
    assert run(monkeypatch, v, "undo") == 1
    assert "nothing to undo" in capsys.readouterr().err

    # Read-only commands and failed commands preserve the snapshot
    run(monkeypatch, v, "add-card", "a", "--title", "A")
    assert run(monkeypatch, v, "status") == 0
    assert run(monkeypatch, v, "validate") == 0
    assert run(monkeypatch, v, "context", "a") == 0
    assert run(monkeypatch, v, "add-card", "b", "--title", "B", "--depends-on", "missing", "--authorized") == 1
    assert run(monkeypatch, v, "undo") == 0
    assert canvas.find_card(canvas.load(v), "a") is None

    # Stale snapshot (target_ts mismatch) is refused
    run(monkeypatch, v, "add-card", "a", "--title", "A")
    snap = json.loads(undo.snapshot_path(v).read_text(encoding="utf-8"))
    snap["target_ts"] = "1999-01-01T00:00:00.000Z"
    undo.snapshot_path(v).write_text(json.dumps(snap), encoding="utf-8")
    capsys.readouterr()
    assert run(monkeypatch, v, "undo") == 1
    assert "stale" in capsys.readouterr().err


def test_undo_does_not_end_active_pause(initialized_vault, monkeypatch):
    v = initialized_vault
    run(monkeypatch, v, "pause")
    assert pauses.current() is not None
    run(monkeypatch, v, "add-card", "a", "--title", "A")
    assert run(monkeypatch, v, "undo") == 0
    assert pauses.current() is not None
