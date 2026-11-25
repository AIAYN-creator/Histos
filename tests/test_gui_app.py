import json
from datetime import datetime, timezone

import pytest

webview = pytest.importorskip("webview")

from histos import canvas, events, frontmatter, guides, operations, pauses, plans  # noqa: E402
from histos.gui import app  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    """Every test here redirects the GUI's config file into tmp_path, so tests never
    read or write the real user's ~/.histos/gui_config.json.
    """
    monkeypatch.setattr(app, "_CONFIG_PATH", tmp_path / "config.json")


def _add_pending_card(vault_root, card_id, *, description=None):
    """A card already in Proposal pending review, with a draft waiting -- built directly
    on canvas/frontmatter/operations since operations.py has no add-card (that stays
    CLI/agent-only, see the operations.py extraction plan).
    """
    data = canvas.load(vault_root)
    width, height = canvas.estimate_card_size(description)
    node = canvas.add_card_node(data, card_id, canvas.PENDING_REVIEW, width=width, height=height)
    canvas.place_new_card(data, node)
    meta = dict(frontmatter.DEFAULT_META)
    if description:
        meta["description"] = description
    frontmatter.write(canvas.card_file_path(vault_root, node), meta, f"# {card_id}\n\n")
    canvas.save(vault_root, data)

    proposal_path = operations._proposal_path(vault_root, card_id)
    proposal_path.parent.mkdir(parents=True, exist_ok=True)
    proposal_path.write_text("proposed body\n", encoding="utf-8")


class _FakeWindow:
    def __init__(self, selection):
        self._selection = selection

    def create_file_dialog(self, dialog_type):
        return self._selection


def test_config_round_trip(tmp_path):
    assert app._load_config() == {}
    app._save_config({"last_vault": str(tmp_path)})
    assert app._load_config() == {"last_vault": str(tmp_path)}


def test_get_last_vault_returns_none_when_nothing_saved():
    assert app.Api().get_last_vault() == {"ok": True, "path": None}


def test_get_last_vault_returns_saved_path_if_it_still_exists(tmp_path):
    app._save_config({"last_vault": str(tmp_path)})
    assert app.Api().get_last_vault() == {"ok": True, "path": str(tmp_path)}


def test_get_last_vault_ignores_a_stale_path(tmp_path):
    app._save_config({"last_vault": str(tmp_path / "moved-or-deleted")})
    assert app.Api().get_last_vault() == {"ok": True, "path": None}


def test_pick_vault_folder_saves_as_last_vault(tmp_path, monkeypatch):
    monkeypatch.setattr(app.webview, "windows", [_FakeWindow((str(tmp_path),))])
    assert app.Api().pick_vault_folder() == {"ok": True, "path": str(tmp_path)}
    assert app._load_config()["last_vault"] == str(tmp_path)


def test_pick_vault_folder_handles_no_selection(monkeypatch):
    monkeypatch.setattr(app.webview, "windows", [_FakeWindow(None)])
    assert app.Api().pick_vault_folder() == {"ok": False, "error": "No folder selected"}


def test_is_vault_false_for_a_plain_folder(tmp_path):
    assert app.Api().is_vault(str(tmp_path)) == {"ok": True, "is_vault": False}


def test_is_vault_true_once_initialized(tmp_path):
    operations.init_vault(tmp_path)
    assert app.Api().is_vault(str(tmp_path)) == {"ok": True, "is_vault": True}


def test_init_vault_success_shape(tmp_path):
    result = app.Api().init_vault(str(tmp_path))
    assert result == {
        "ok": True,
        "data": {"vault_root": str(tmp_path), "template_warnings": []},
    }
    assert (tmp_path / "AGENTS.md").exists()


def test_init_vault_error_shape_when_already_initialized(tmp_path):
    operations.init_vault(tmp_path)
    result = app.Api().init_vault(str(tmp_path))
    assert result["ok"] is False
    assert "error" in result


def test_get_status_returns_all_groups_in_order(tmp_path):
    operations.init_vault(tmp_path)
    _add_pending_card(tmp_path, "cap1", description="a thing to review")

    result = app.Api().get_status(str(tmp_path))
    assert result["ok"] is True
    labels = [g["label"] for g in result["data"]["groups"]]
    assert labels == [
        "Backlog", "Blocked", "In progress",
        "Proposal pending review", "Dependency change request", "Approved",
    ]
    pending = next(g for g in result["data"]["groups"] if g["color"] == canvas.PENDING_REVIEW)
    assert [c["id"] for c in pending["cards"]] == ["cap1"]
    assert pending["cards"][0]["description"] == "a thing to review"


def test_get_status_error_shape_for_bad_vault(tmp_path):
    result = app.Api().get_status(str(tmp_path))
    assert result["ok"] is False
    assert "error" in result


def test_open_in_obsidian_calls_startfile_with_encoded_uri(monkeypatch):
    calls = []
    monkeypatch.setattr(app.os, "startfile", lambda uri: calls.append(uri))
    result = app.Api().open_in_obsidian("C:\\a folder\\my vault")
    assert result == {"ok": True}
    assert calls == ["obsidian://open?path=C%3A%5Ca%20folder%5Cmy%20vault"]


def test_open_in_obsidian_error_shape_when_startfile_fails(monkeypatch):
    def _boom(uri):
        raise OSError("no application is associated")

    monkeypatch.setattr(app.os, "startfile", _boom)
    result = app.Api().open_in_obsidian("C:\\vault")
    assert result["ok"] is False
    assert "error" in result


def test_open_folder_calls_startfile_with_raw_path(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(app.os, "startfile", lambda path: calls.append(path))
    result = app.Api().open_folder(str(tmp_path))
    assert result == {"ok": True}
    assert calls == [str(tmp_path)]


def test_get_diff_success_and_error_shape(tmp_path):
    operations.init_vault(tmp_path)
    _add_pending_card(tmp_path, "cap1")

    ok = app.Api().get_diff(str(tmp_path), "cap1")
    assert ok == {
        "ok": True,
        "data": {"card_id": "cap1", "current_body": "# cap1\n\n", "proposed_body": "proposed body\n"},
    }

    bad = app.Api().get_diff(str(tmp_path), "nope")
    assert bad["ok"] is False
    assert "error" in bad


def test_approve_success_and_error_shape(tmp_path):
    operations.init_vault(tmp_path)
    _add_pending_card(tmp_path, "cap1")

    assert app.Api().approve(str(tmp_path), "cap1") == {"ok": True, "data": {"card_id": "cap1"}}

    bad = app.Api().approve(str(tmp_path), "cap1")  # already approved, no longer pending
    assert bad["ok"] is False
    assert "error" in bad


def test_reject_success_and_error_shape(tmp_path):
    operations.init_vault(tmp_path)
    _add_pending_card(tmp_path, "cap1")

    result = app.Api().reject(str(tmp_path), "cap1", feedback="needs more detail")
    assert result == {"ok": True, "data": {"card_id": "cap1"}}
    meta, _ = frontmatter.read(tmp_path / "content" / "cap1.md")
    assert meta["status_note"] == "needs more detail"

    bad = app.Api().reject(str(tmp_path), "cap1")  # back in Backlog, no longer pending
    assert bad["ok"] is False
    assert "error" in bad


def test_open_folder_never_starts_a_file(tmp_path, monkeypatch):
    started = []
    monkeypatch.setattr(app.os, "startfile", lambda p: started.append(p), raising=False)
    program = tmp_path / "not-a-folder.exe"
    program.write_bytes(b"")
    result = app.Api().open_folder(str(program))
    assert result["ok"] is False and "not a folder" in result["error"]
    assert started == []
    assert app.Api().open_folder(str(tmp_path))["ok"] is True
    assert started == [str(tmp_path)]


def test_the_packaged_selftest_passes(capsys):
    """What `HISTOS_GUI_SELFTEST=1 histos-gui.exe` runs to check a PyInstaller build."""
    assert app._selftest() == 0
    assert "SELFTEST PASS" in capsys.readouterr().out


def _add_card(vault_root, card_id, *, color=canvas.BACKLOG, depends_on=(), meta_extra=None, body=None):
    data = canvas.load(vault_root)
    width, height = canvas.estimate_card_size(None)
    node = canvas.add_card_node(data, card_id, color, width=width, height=height)
    for dep in depends_on:
        canvas.add_edge(data, dep, card_id)
    canvas.place_new_card(data, node)
    meta = dict(frontmatter.DEFAULT_META)
    text = body if body is not None else f"# {card_id}\n\n"
    meta[frontmatter.CONTENT_HASH_KEY] = frontmatter.body_hash(text)
    if meta_extra:
        meta.update(meta_extra)
    frontmatter.write(canvas.card_file_path(vault_root, node), meta, text)
    canvas.save(vault_root, data)


def test_get_status_enriches_v3_fields_and_is_json_serializable(tmp_path):
    operations.init_vault(tmp_path)
    # Card 'a': Approved, pre-v3 style without content_hash, tampered vs approved/a.md
    _add_card(tmp_path, "a", color=canvas.APPROVED, body="# a\n\napproved body\n")
    a_path = tmp_path / "content" / "a.md"
    a_meta, _ = frontmatter.read(a_path)
    a_meta.pop(frontmatter.CONTENT_HASH_KEY, None)
    frontmatter.write(a_path, a_meta, "# a\n\nedited by hand\n")
    approved_a = operations._approved_path(tmp_path, "a")
    approved_a.parent.mkdir(parents=True, exist_ok=True)
    approved_a.write_text("# a\n\napproved body\n", encoding="utf-8")

    # Card 'b': In progress with tier, models, and failed eval_results (including datetime value)
    _add_card(
        tmp_path,
        "b",
        color=canvas.IN_PROGRESS,
        meta_extra={
            "worker": "sub-b",
            "proposed_tier": "reasoning",
            "proposed_model": "Opus 5.5",
            "approved_model": "Opus 5.5",
            "eval_results": {
                "passed": False,
                "attempt": 3,
                "at": datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc),
                "checks": [{"name": "word_count", "passed": False, "error": "too short"}],
            },
        },
    )
    # Card 'c': depends on 'b' (Blocked), content_hash tampered
    _add_card(
        tmp_path,
        "c",
        color=canvas.BLOCKED,
        depends_on=("b",),
        meta_extra={"proposed_tier": "fast", "proposed_model": "Haiku 4.5"},
    )
    c_path = tmp_path / "content" / "c.md"
    c_meta, _ = frontmatter.read(c_path)
    frontmatter.write(c_path, c_meta, "# c\n\ntampered outside histos\n")

    res = app.Api().get_status(str(tmp_path))
    assert res["ok"] is True
    # Must serialize cleanly to JSON across the pywebview bridge
    encoded = json.dumps(res)
    assert encoded

    data = res["data"]
    assert data["paused"] is False
    assert data["critical_path"]["cards"] == ["b", "c"]

    by_id = {c["id"]: c for g in data["groups"] for c in g["cards"]}
    assert by_id["a"]["content_tampered"] is True
    assert by_id["b"]["tier"] == "reasoning"
    assert by_id["b"]["proposed_model"] == "Opus 5.5"
    assert by_id["b"]["approved_model"] == "Opus 5.5"
    assert by_id["b"]["worker"] == "sub-b"
    assert by_id["b"]["eval_results"]["passed"] is False
    assert by_id["b"]["eval_results"]["attempt"] == 3
    assert by_id["b"]["content_tampered"] is False
    assert by_id["b"]["estimate"]["agent"] == {"needed": 5}
    assert by_id["b"]["estimate"]["review"] == {"needed": 5}
    assert by_id["c"]["tier"] == "fast"
    assert by_id["c"]["content_tampered"] is True


def test_pause_and_status_paused_flag(tmp_path):
    operations.init_vault(tmp_path)
    first = app.Api().pause(str(tmp_path))
    assert first == {"ok": True, "data": {"paused": True, "new": True}}

    second = app.Api().pause(str(tmp_path))
    assert second == {"ok": True, "data": {"paused": True, "new": False}}

    status = app.Api().get_status(str(tmp_path))
    assert status["ok"] is True
    assert status["data"]["paused"] is True
    assert pauses.current() is not None


def test_approve_model_and_batch(tmp_path):
    operations.init_vault(tmp_path)
    _add_card(tmp_path, "c1", meta_extra={"proposed_tier": "exec", "proposed_model": "Sonnet 5.5"})
    _add_card(tmp_path, "c2", meta_extra={"proposed_tier": "reasoning", "proposed_model": "Opus 5.5"})
    _add_card(tmp_path, "c3", meta_extra={"proposed_tier": "fast"})

    # Single approve defaulting to proposed_model
    r1 = app.Api().approve_model(str(tmp_path), "c1")
    assert r1 == {"ok": True, "data": {"card_id": "c1", "model": "Sonnet 5.5"}}
    assert frontmatter.read(tmp_path / "content" / "c1.md")[0]["approved_model"] == "Sonnet 5.5"

    # Missing proposed_model & no model_name -> clear error
    bad = app.Api().approve_model(str(tmp_path), "c3")
    assert bad["ok"] is False
    assert "no proposed model" in bad["error"]

    # Batch validation is atomic: if one fails, none are written
    bad_batch = app.Api().approve_models_batch(str(tmp_path), [{"card_id": "c2"}, {"card_id": "c3"}])
    assert bad_batch["ok"] is False
    assert "approved_model" not in frontmatter.read(tmp_path / "content" / "c2.md")[0]

    # Batch approve with default and explicit override
    ok_batch = app.Api().approve_models_batch(
        str(tmp_path),
        [{"card_id": "c2"}, {"card_id": "c3", "model": "Haiku 4.5"}],
    )
    assert ok_batch == {
        "ok": True,
        "data": {
            "approved": [
                {"card_id": "c2", "model": "Opus 5.5"},
                {"card_id": "c3", "model": "Haiku 4.5"},
            ]
        },
    }
    assert frontmatter.read(tmp_path / "content" / "c2.md")[0]["approved_model"] == "Opus 5.5"
    assert frontmatter.read(tmp_path / "content" / "c3.md")[0]["approved_model"] == "Haiku 4.5"
    ev_names = [e["event"] for e in events.read(tmp_path)]
    assert ev_names.count("model_approved") == 3


def test_retry_clears_failed_evals_and_validates_state(tmp_path):
    operations.init_vault(tmp_path)
    _add_card(
        tmp_path,
        "c1",
        color=canvas.IN_PROGRESS,
        meta_extra={"eval_results": {"passed": False, "attempt": 3, "checks": []}},
    )
    ok = app.Api().retry(str(tmp_path), "c1")
    assert ok == {"ok": True, "data": {"card_id": "c1"}}
    assert "eval_results" not in frontmatter.read(tmp_path / "content" / "c1.md")[0]

    bad = app.Api().retry(str(tmp_path), "c1")
    assert bad["ok"] is False
    assert "no failed evals" in bad["error"]


def test_accept_edit_updates_hash_and_errors_cleanly(tmp_path):
    operations.init_vault(tmp_path)
    _add_card(tmp_path, "c1")
    c1_path = tmp_path / "content" / "c1.md"
    meta, _ = frontmatter.read(c1_path)
    frontmatter.write(c1_path, meta, "# c1\n\nhuman hand edit\n")

    assert app.Api().get_status(str(tmp_path))["data"]["groups"][0]["cards"][0]["content_tampered"] is True

    ok = app.Api().accept_edit(str(tmp_path), ["c1"])
    assert ok == {"ok": True, "data": {"card_ids": ["c1"]}}
    assert app.Api().get_status(str(tmp_path))["data"]["groups"][0]["cards"][0]["content_tampered"] is False

    assert app.Api().accept_edit(str(tmp_path), [])["ok"] is False
    assert app.Api().accept_edit(str(tmp_path), ["missing"])["ok"] is False


def test_guides_pending_approve_and_reject(tmp_path):
    operations.init_vault(tmp_path)
    (tmp_path / "PROJECT.md").write_text("# Project\n\nOld text.\n\n## Decisions\n\n", encoding="utf-8")
    understood = " ".join(["word"] * 65)
    change = " ".join(["reason"] * 35)
    valid_project = (
        "# Project\n\nNew text.\n\n"
        "## Goal\nShip it.\n"
        "## Deliverable\nDesktop app.\n"
        "## Audience\nAuthors.\n"
        "## Out of scope\nCloud sync.\n"
        "## Done when\nAll tests pass.\n"
        "## Constraints\nLocal only.\n"
        f"## What I understood\n{understood}\n"
        f"## What I'd change and why\n{change}\n"
        "## Decisions\n\n"
    )
    assert guides.propose(tmp_path, "PROJECT.md", valid_project).saved is True
    assert guides.propose(tmp_path, "ARCHITECTURE.md", "# Architecture\n\nClean layers.\n").saved is True

    pending = app.Api().get_pending_guides(str(tmp_path))
    assert pending["ok"] is True
    assert [g["name"] for g in pending["data"]["guides"]] == ["PROJECT.md", "ARCHITECTURE.md"]
    assert pending["data"]["guides"][0]["current"].startswith("# Project\n\nOld text.")
    assert "New text." in pending["data"]["guides"][0]["proposed"]
    assert pending["data"]["guides"][0]["eval_results"]["passed"] is True

    approved = app.Api().approve_guide(str(tmp_path), "PROJECT.md")
    assert approved["ok"] is True
    assert approved["data"]["name"] == "PROJECT.md"
    assert approved["data"]["archived_previous"] is not None
    assert "New text." in (tmp_path / "PROJECT.md").read_text(encoding="utf-8")

    rejected = app.Api().reject_guide(str(tmp_path), "ARCHITECTURE.md", feedback="needs diagram")
    assert rejected == {"ok": True, "data": {"name": "ARCHITECTURE.md"}}
    assert not (tmp_path / "ARCHITECTURE.md").exists()
    assert "needs diagram" in (tmp_path / "PROJECT.md").read_text(encoding="utf-8")

    assert app.Api().approve_guide(str(tmp_path), "PROJECT.md")["ok"] is False
    assert app.Api().reject_guide(str(tmp_path), "README.md")["ok"] is False


def test_plan_pending_reject_and_approve(tmp_path):
    operations.init_vault(tmp_path)
    empty = app.Api().get_pending_plan(str(tmp_path))
    assert empty == {"ok": True, "data": {"plan": None, "feedback": None}}

    plan_yaml = (
        "summary: Split into two steps.\n"
        "cards:\n"
        "  - id: dec-one\n"
        "    title: First decision\n"
        "    done_when: First decision is written down and clear.\n"
        "  - id: cod-two\n"
        "    title: Second step\n"
        "    done_when: Second step builds cleanly on the first.\n"
        "    depends_on: [dec-one]\n"
    )
    plans.propose(tmp_path, plan_yaml)

    pending = app.Api().get_pending_plan(str(tmp_path))
    assert pending["ok"] is True
    p = pending["data"]["plan"]
    assert p["summary"] == "Split into two steps."
    assert [c["id"] for c in p["cards"]] == ["dec-one", "cod-two"]
    assert p["levels"] == [["dec-one"], ["cod-two"]]
    assert p["errors"] == []

    rej = app.Api().reject_plan(str(tmp_path), feedback="add tests card")
    assert rej == {"ok": True, "data": {"cards": 2}}
    after_rej = app.Api().get_pending_plan(str(tmp_path))
    assert after_rej == {"ok": True, "data": {"plan": None, "feedback": "add tests card"}}

    plans.propose(tmp_path, plan_yaml)
    appr = app.Api().approve_plan(str(tmp_path))
    assert appr["ok"] is True
    assert appr["data"]["created"] == ["dec-one", "cod-two"]
    assert canvas.find_card(canvas.load(tmp_path), "cod-two")["color"] == canvas.BLOCKED

    assert app.Api().approve_plan(str(tmp_path))["ok"] is False
    assert app.Api().reject_plan(str(tmp_path))["ok"] is False

    # Invalid pending YAML surfaces an error shape
    plans.plan_path(tmp_path).write_text(":::bad yaml", encoding="utf-8")
    assert app.Api().get_pending_plan(str(tmp_path))["ok"] is False
