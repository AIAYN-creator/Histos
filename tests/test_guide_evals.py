"""Evals on guide proposals, and the PROJECT.md structure template (cod-guia-evals)."""
import pytest
from conftest import run

from histos import checks, config, evals, guides

FULL_ES = """# Mi proyecto

## Objetivo
Un objetivo claro.

## Entregable
Un PDF.

## Para quién
El tribunal.

## Qué no es
No es un libro.

## Terminado cuando
Está entregado.

## Restricciones
Plantilla de la universidad.

## Lo que he entendido
{understood}

## Lo que cambiaría y por qué
{change}

## Decisiones
"""

LONG = " ".join(["palabra"] * 70)


def project(understood=LONG, change=LONG):
    return FULL_ES.format(understood=understood, change=change)


@pytest.fixture
def vault(initialized_vault):
    return initialized_vault


def draft(vault, text, name="borrador.md"):
    path = vault / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def check(name, text, params):
    return checks.RUNNERS[name](checks.CheckInput(text, params, None, {"nodes": []}, None))


# ---- which evals reach a guide ----

def test_evals_without_only_never_apply_to_guides(vault):
    loaded = {e.name: e for e in evals.load(vault, config.load(vault))}
    assert not loaded["no_placeholders"].applies_to("PROJECT.md", "guide")
    assert loaded["no_placeholders"].applies_to("cap1")
    assert loaded["project_structure"].applies_to("PROJECT.md", "guide")
    assert not loaded["project_structure"].applies_to("ARCHITECTURE.md", "guide")
    assert not loaded["project_structure"].applies_to("cap1")
    assert loaded["no_placeholders_in_guides"].applies_to("ARCHITECTURE.md", "guide")


def test_only_and_except_accept_guide_names(vault):
    (vault / "evals" / "mine.yaml").write_text(
        "- {name: arch, check: word_count, min: 1, only: [ARCHITECTURE.md]}\n", encoding="utf-8")
    assert evals.validate(vault, config.load(vault)) == []


def test_content_untouched_passes_on_a_guide():
    assert check("content_untouched", "x", {}).detail == "not a card"


# ---- guide propose ----

def test_a_project_md_missing_sections_is_refused(vault, monkeypatch, capsys):
    text = project().replace("## Qué no es", "## Otra cosa")
    assert run(monkeypatch, vault, "guide", "propose", "PROJECT.md", "--file", draft(vault, text)) == 1
    out = capsys.readouterr()
    assert "FAIL  project_structure" in out.out and '"## Objetivo"' not in out.out
    assert '"## Qué no es"' in out.out and "wasn't proposed" in out.err
    assert guides.pending(vault) == []


def test_a_complete_project_md_is_proposed_and_diff_shows_the_evals(vault, monkeypatch, capsys):
    assert run(monkeypatch, vault, "guide", "propose", "PROJECT.md", "--file", draft(vault, project())) == 0
    assert guides.pending(vault) == ["PROJECT.md"]
    capsys.readouterr()
    run(monkeypatch, vault, "guide", "diff", "PROJECT.md")
    out = capsys.readouterr().out
    assert out.startswith("evals: 3 of 3 passed") and "+## Objetivo" in out


def test_english_and_mixed_headings_pass(vault, monkeypatch):
    text = (project().replace("## Objetivo", "## Goal").replace("## Lo que he entendido", "## What I understood")
            .replace("## Qué no es", "## Que no es"))  # and without the accent
    assert run(monkeypatch, vault, "guide", "propose", "PROJECT.md", "--file", draft(vault, text)) == 0


def test_the_agents_sections_need_real_text(vault, monkeypatch, capsys):
    text = project(understood="Lo entiendo.", change=" ".join(["x"] * 30))
    assert run(monkeypatch, vault, "guide", "propose", "PROJECT.md", "--file", draft(vault, text)) == 1
    out = capsys.readouterr().out
    assert '"Lo que he entendido": 2 of 60 words' in out
    assert "Lo que cambiaría y por qué\": 30" not in out.split("FAIL")[1].split("\n")[0]


def test_a_refused_draft_keeps_the_pending_one(vault, monkeypatch):
    run(monkeypatch, vault, "guide", "propose", "PROJECT.md", "--file", draft(vault, project()))
    run(monkeypatch, vault, "guide", "propose", "PROJECT.md", "--file", draft(vault, "## Objetivo\nNada\n"))
    assert "## Lo que he entendido" in guides.get_diff(vault, "PROJECT.md").proposed


def test_architecture_md_only_gets_the_placeholder_check(vault, monkeypatch):
    assert run(monkeypatch, vault, "guide", "propose", "ARCHITECTURE.md", "--file", draft(vault, "Python 3.12.\n")) == 0
    assert run(monkeypatch, vault, "guide", "propose", "ARCHITECTURE.md", "--file", draft(vault, "TODO\n")) == 1


def test_no_attempt_cap_for_guides(vault, monkeypatch):
    for _ in range(5):
        assert run(monkeypatch, vault, "guide", "propose", "PROJECT.md", "--file", draft(vault, "TODO")) == 1
    assert run(monkeypatch, vault, "guide", "propose", "PROJECT.md", "--file", draft(vault, project())) == 0


def test_approve_and_reject_clear_the_stored_evals(vault, monkeypatch):
    run(monkeypatch, vault, "guide", "propose", "PROJECT.md", "--file", draft(vault, project()))
    assert guides.stored_evals(vault, "PROJECT.md")["passed"] is True
    run(monkeypatch, vault, "guide", "reject", "PROJECT.md")
    assert guides.stored_evals(vault, "PROJECT.md") is None


def test_a_vault_without_config_works_as_before(vault, monkeypatch):
    (vault / "histos.config.yaml").unlink()
    assert run(monkeypatch, vault, "guide", "propose", "PROJECT.md", "--file", draft(vault, "Anything.\n")) == 0


# ---- histos eval on a guide ----

def test_eval_dry_runs_a_guide_draft(vault, monkeypatch, capsys):
    assert run(monkeypatch, vault, "eval", "project.md", "--file", draft(vault, project())) == 0
    assert capsys.readouterr().out.startswith("evals: 3 of 3 passed")
    assert run(monkeypatch, vault, "eval", "PROJECT.md", "--file", draft(vault, "x")) == 1
    assert guides.pending(vault) == []


# ---- the checks ----

def test_required_headings_alternatives_and_accents():
    params = {"headings": [["## Objetivo", "## Goal"], "## Qué no es"]}
    assert check("required_headings", "## Goal\n## que NO es\n", params).passed
    outcome = check("required_headings", "## Goal\n", params)
    assert outcome.detail == 'missing: "## Qué no es"'


def test_min_section_words_counts_until_the_next_heading_of_the_same_level():
    text = "## A\nuno dos\n### Sub\ntres\n## B\ncuatro\n"
    params = {"sections": [{"heading": "## A", "min": 3}, {"heading": ["## B", "## Bee"], "min": 1}]}
    outcome = check("min_section_words", text, params)
    assert outcome.passed and outcome.detail == '"A": 4 of 3 words; "B": 1 of 1 words'


def test_a_missing_section_counts_as_zero():
    outcome = check("min_section_words", "## A\nuno\n", {"sections": [{"heading": "## Z", "min": 1}]})
    assert not outcome.passed and outcome.detail == '"Z": 0 of 1 words'


@pytest.mark.parametrize("text, fragment", [
    ("- {name: x, check: min_section_words}\n", "needs 'sections'"),
    ("- {name: x, check: min_section_words, sections: [{heading: '## A'}]}\n", "'sections' must be"),
    ("- {name: x, check: min_section_words, sections: [{heading: '## A', min: -1}]}\n", "'sections' must be"),
    ("- {name: x, check: required_headings, headings: [[]]}\n", "'headings' must be"),
])
def test_invalid_new_parameters(vault, text, fragment):
    (vault / "evals" / "bad.yaml").write_text(text, encoding="utf-8")
    errors = evals.validate(vault, config.load(vault))
    assert any(fragment in e for e in errors), errors
