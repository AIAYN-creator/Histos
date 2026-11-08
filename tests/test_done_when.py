"""done_when: a checkable criterion for when a card is done (cod-plan)."""
from conftest import run

from histos import canvas, frontmatter


def test_add_card_records_done_when(initialized_vault, monkeypatch, capsys):
    v = initialized_vault
    assert run(monkeypatch, v, "add-card", "a", "--title", "A", "--description", "Una cosa.",
               "--done-when", "Los tests de A pasan.") == 0
    meta, body = frontmatter.read(v / "content" / "a.md")
    assert meta["done_when"] == "Los tests de A pasan."
    assert body == "# A\n\nUna cosa.\n\n**Done when:** Los tests de A pasan.\n\n"
    assert meta["content_hash"] == frontmatter.body_hash(body)
    capsys.readouterr()
    run(monkeypatch, v, "context", "a")
    out = capsys.readouterr().out
    assert "Description: Una cosa.\n\nDone when: Los tests de A pasan." in out
    assert run(monkeypatch, v, "validate") == 0


def test_without_done_when_nothing_changes(initialized_vault, monkeypatch):
    run(monkeypatch, initialized_vault, "add-card", "a", "--title", "A")
    meta, body = frontmatter.read(initialized_vault / "content" / "a.md")
    assert "done_when" not in meta and body == "# A\n\n"


def test_validate_checks_its_type(initialized_vault, monkeypatch, capsys):
    run(monkeypatch, initialized_vault, "add-card", "a", "--title", "A")
    path = initialized_vault / "content" / "a.md"
    meta, body = frontmatter.read(path)
    meta["done_when"] = 3
    frontmatter.write(path, meta, body)
    assert run(monkeypatch, initialized_vault, "validate") == 1
    assert "done_when" in capsys.readouterr().out


def test_the_subagent_brief_has_it_too(initialized_vault, monkeypatch, capsys):
    run(monkeypatch, initialized_vault, "add-card", "a", "--title", "A", "--done-when", "Hecho.")
    run(monkeypatch, initialized_vault, "assign", "a")
    capsys.readouterr()
    run(monkeypatch, initialized_vault, "context", "a", "--for-subagent")
    assert "Done when: Hecho." in capsys.readouterr().out
    assert canvas.find_card(canvas.load(initialized_vault), "a")["color"] == canvas.IN_PROGRESS
