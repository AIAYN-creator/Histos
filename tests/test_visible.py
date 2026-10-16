import pytest
from conftest import run

from histos import frontmatter


@pytest.fixture
def vault(tmp_path, monkeypatch):
    run(monkeypatch, tmp_path, "init")
    run(monkeypatch, tmp_path, "add-card", "base", "--title", "Base")
    for card_id in ("cap3", "cap4", "cap5", "cap6"):
        run(monkeypatch, tmp_path, "add-card", card_id, "--title", card_id)
    return tmp_path


def set_meta(vault, card_id, **fields):
    md = vault / "content" / f"{card_id}.md"
    meta, body = frontmatter.read(md)
    meta.update(fields)
    frontmatter.write(md, meta, body)


def status(vault, monkeypatch, capsys):
    capsys.readouterr()
    run(monkeypatch, vault, "status")
    return capsys.readouterr().out


def test_nothing_classified_looks_like_v2(vault, monkeypatch, capsys):
    out = status(vault, monkeypatch, capsys)
    assert "{" not in out and "Tiers:" not in out and "Waiting for your model approval" not in out


def test_the_four_routing_labels(vault, monkeypatch, capsys):
    set_meta(vault, "cap3", proposed_tier="exec", proposed_model="Sonnet 5.5", approved_model="Sonnet 5.5")
    set_meta(vault, "cap4", proposed_tier="reasoning", proposed_model="Opus 5.5")
    set_meta(vault, "cap5", proposed_tier="fast")
    out = status(vault, monkeypatch, capsys)
    assert "  - cap3  (content/cap3.md)  {exec · Sonnet 5.5}" in out
    assert "  - cap4  (content/cap4.md)  {reasoning · proposed Opus 5.5?}" in out
    assert "  - cap5  (content/cap5.md)  {fast}" in out
    assert "  - cap6  (content/cap6.md)\n" in out


def test_summary_lines(vault, monkeypatch, capsys):
    set_meta(vault, "cap3", proposed_tier="exec", approved_model="Sonnet 5.5")
    set_meta(vault, "cap4", proposed_tier="reasoning", proposed_model="Opus 5.5")
    out = status(vault, monkeypatch, capsys)
    assert "Tiers: fast 0 · exec 1 · reasoning 1 · unclassified 3" in out
    assert "Waiting for your model approval: cap4 (reasoning, proposed Opus 5.5)" in out


def context(vault, monkeypatch, capsys, card_id):
    capsys.readouterr()
    run(monkeypatch, vault, "context", card_id)
    return capsys.readouterr().out


def test_context_line_variants(vault, monkeypatch, capsys):
    set_meta(vault, "cap3", proposed_tier="exec", approved_model="Sonnet 5.5")
    assert "Tier: exec · Approved model: Sonnet 5.5" in context(vault, monkeypatch, capsys, "cap3")
    set_meta(vault, "cap4", proposed_tier="reasoning", proposed_model="Opus 5.5")
    assert "Tier: reasoning · Proposed model: Opus 5.5 (not approved yet)" in context(vault, monkeypatch, capsys, "cap4")
    assert "Tier:" not in context(vault, monkeypatch, capsys, "cap5")


def test_dependencies_dont_show_it(vault, monkeypatch, capsys):
    set_meta(vault, "base", proposed_tier="reasoning", approved_model="Opus 5.5")
    run(monkeypatch, vault, "link", "cap3", "--depends-on", "base", "--authorized")
    out = context(vault, monkeypatch, capsys, "cap3")
    assert "## Dependency: base" in out and "Opus 5.5" not in out
