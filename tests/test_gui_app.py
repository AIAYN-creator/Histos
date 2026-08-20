import pytest

webview = pytest.importorskip("webview")

from histos import canvas, frontmatter, operations  # noqa: E402
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
    node = canvas.add_card_node(data, card_id, canvas.PROPUESTA_PENDIENTE, width=width, height=height)
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
    pending = next(g for g in result["data"]["groups"] if g["color"] == canvas.PROPUESTA_PENDIENTE)
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


def test_open_folder_calls_startfile_with_raw_path(monkeypatch):
    calls = []
    monkeypatch.setattr(app.os, "startfile", lambda path: calls.append(path))
    result = app.Api().open_folder("C:\\some\\vault")
    assert result == {"ok": True}
    assert calls == ["C:\\some\\vault"]


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
