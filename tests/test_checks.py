import pytest
from conftest import run

from histos import canvas, checks, evals, frontmatter


def check(name, draft, params=None, vault=None):
    data = canvas.load(vault) if vault else {"nodes": [], "edges": []}
    card = {"id": "cap1"}
    return checks.RUNNERS[name](checks.CheckInput(draft, params or {}, vault, data, card))


def test_every_catalog_check_has_an_implementation():
    assert set(evals.CATALOG) == set(checks.RUNNERS)


# ---- word_count ----

@pytest.mark.parametrize("params, words, passed", [
    ({"min": 3}, 3, True), ({"min": 3}, 2, False),
    ({"max": 3}, 3, True), ({"max": 3}, 4, False),
    ({"min": 2, "max": 4}, 3, True), ({"min": 2, "max": 4}, 5, False),
])
def test_word_count_edges(params, words, passed):
    outcome = check("word_count", " ".join(["palabra"] * words), params)
    assert outcome.passed is passed
    assert outcome.detail.startswith(f"{words} words")


def test_word_count_message():
    assert check("word_count", "dos palabras", {"min": 300}).message == "the draft has 2 words, at least 300 are needed"


# ---- no_placeholders ----

@pytest.mark.parametrize("draft", ["Falta esto: TODO", "Ver [...] arriba", "Lorem ipsum dolor", "Dato [cita requerida]"])
def test_default_placeholders_are_caught(draft):
    assert not check("no_placeholders", draft).passed


@pytest.mark.parametrize("draft", ["Todo el capitulo esta listo.", "todos los datos", "TODOS LOS DATOS"])
def test_ordinary_words_are_not_placeholders(draft):
    assert check("no_placeholders", draft).passed


def test_placeholder_detail_names_the_line():
    assert check("no_placeholders", "uno\ndos\nFIXME tres").detail == '"FIXME" at line 3'


def test_own_patterns_replace_the_defaults():
    assert check("no_placeholders", "TODO.txt es un archivo", {"patterns": ["PENDIENTE"]}).passed
    assert not check("no_placeholders", "PENDIENTE de revisar", {"patterns": ["PENDIENTE"]}).passed


# ---- required_headings ----

def test_required_headings_any_level_and_spacing():
    draft = "# Introduccion\n\n### resultados  \ntexto\n"
    assert check("required_headings", draft, {"headings": ["## Introduccion", "Resultados"]}).passed


def test_missing_heading_is_named():
    outcome = check("required_headings", "# Introduccion\n", {"headings": ["Introduccion", "## Discusion"]})
    assert not outcome.passed and outcome.detail == 'missing: "## Discusion"'


# ---- content_untouched ----

@pytest.fixture
def vault(tmp_path, monkeypatch):
    run(monkeypatch, tmp_path, "init")
    run(monkeypatch, tmp_path, "add-card", "cap1", "--title", "Uno", "--description", "El primero")
    run(monkeypatch, tmp_path, "add-card", "cap2", "--title", "Dos")
    return tmp_path


def untouched(vault):
    return check("content_untouched", "", vault=vault)


def edit_body(vault, card_id, new_body):
    md = vault / "content" / f"{card_id}.md"
    meta, _ = frontmatter.read(md)
    frontmatter.write(md, meta, new_body)


def approve(vault, monkeypatch, card_id, text):
    draft = vault / "d.md"
    draft.write_text(text, encoding="utf-8")
    run(monkeypatch, vault, "assign", card_id)
    run(monkeypatch, vault, "propose", card_id, "--file", str(draft))
    run(monkeypatch, vault, "approve", card_id)


def test_a_fresh_vault_passes(vault):
    assert untouched(vault).passed


def test_after_approving_it_still_passes(vault, monkeypatch):
    approve(vault, monkeypatch, "cap1", "Contenido aprobado.\n")
    assert untouched(vault).passed


def test_a_hand_edit_fails_and_suggests_accept_edit(vault, monkeypatch):
    approve(vault, monkeypatch, "cap1", "Contenido aprobado.\n")
    edit_body(vault, "cap1", "Contenido aprobado, con una errata corregida.\n")
    outcome = untouched(vault)
    assert not outcome.passed
    assert "content/cap1.md" in outcome.message and "histos accept-edit cap1" in outcome.message
    assert run(monkeypatch, vault, "accept-edit", "cap1") == 0
    assert untouched(vault).passed


def test_a_direct_write_to_an_unapproved_card_is_caught(vault):
    edit_body(vault, "cap2", "# Dos\n\nEscrito directamente por el agente.\n")
    assert "content/cap2.md" in untouched(vault).detail


def test_frontmatter_changes_dont_count(vault, monkeypatch):
    run(monkeypatch, vault, "describe", "cap1", "--text", "Otra descripcion")
    run(monkeypatch, vault, "assign", "cap2", "--worker", "sub-1")
    assert untouched(vault).passed


def _drop_hash(vault, card_id):
    md = vault / "content" / f"{card_id}.md"
    meta, body = frontmatter.read(md)
    meta.pop(frontmatter.CONTENT_HASH_KEY)
    frontmatter.write(md, meta, body)


def test_pre_v3_card_compares_with_its_approved_copy(vault, monkeypatch):
    approve(vault, monkeypatch, "cap1", "Aprobado.\n")
    _drop_hash(vault, "cap1")
    assert untouched(vault).passed
    edit_body(vault, "cap1", "Cambiado.\n")
    assert not untouched(vault).passed


def test_pre_v3_card_without_baseline_passes_and_says_so(vault):
    _drop_hash(vault, "cap2")
    edit_body(vault, "cap2", "Lo que sea.\n")
    outcome = untouched(vault)
    assert outcome.passed and "1 with no baseline" in outcome.detail


def test_accept_edit_unknown_card(vault, monkeypatch, capsys):
    assert run(monkeypatch, vault, "accept-edit", "nope") == 1
    assert "not found: nope" in capsys.readouterr().err


def test_new_cards_get_a_valid_hash(vault):
    from histos import validation
    assert validation.validate_frontmatter(vault, canvas.load(vault)) == []
    meta, body = frontmatter.read(vault / "content" / "cap1.md")
    assert meta[frontmatter.CONTENT_HASH_KEY] == frontmatter.body_hash(body)
