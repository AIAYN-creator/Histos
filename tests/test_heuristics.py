import pytest
from conftest import run

from histos import canvas, frontmatter, heuristics
from histos.heuristics import Inputs


def inputs(card_text="", full_text=None, deps=0, sources=0):
    return Inputs(card_text, card_text if full_text is None else full_text, deps, sources)


def holds(expr, i, terms=None):
    return heuristics.parse(expr).evaluate(i, terms).holds


# ---- syntax ----

@pytest.mark.parametrize("expr", [
    "code_blocks_present", "not debugging_terms_present", "token_count >= 2000",
    "token_count<200", "  word_count   >  10 ", "dependency_count == 0", "no_code_blocks",
])
def test_valid_syntax(expr):
    heuristics.parse(expr)


@pytest.mark.parametrize("expr, message", [
    ("arquitecture_terms_present", "unknown condition"),
    ("token_count >= lots", "unknown condition"),
    ("token_count", "is a measure"),
    ("tokens > 5", "unknown measure"),
    ("not no_code_blocks", "already a negation"),
    ("code_blocks_present or math_symbols_present", "unknown condition"),
    ("__import__('os')", "unknown condition"),
])
def test_invalid_syntax(expr, message):
    with pytest.raises(ValueError, match=message):
        heuristics.parse(expr)


# ---- measures ----

def test_token_count_is_chars_over_four_rounded_up():
    i = inputs(full_text="x" * 801)
    assert heuristics.parse("token_count == 201").evaluate(i).holds
    assert heuristics.parse("token_count < 200").evaluate(i).detail == "token_count = 201"


def test_word_dependency_and_source_counts():
    i = inputs(full_text="uno dos tres", deps=2, sources=1)
    assert holds("word_count == 3", i)
    assert holds("dependency_count >= 2", i)
    assert holds("source_count == 1", i)


# ---- yes/no conditions ----

@pytest.mark.parametrize("expr, yes, no", [
    ("code_blocks_present", "Ver:\n```python\nx = 1\n```\n", "Solo texto."),
    ("code_blocks_present", "Texto.\n\n    x = 1\n", "- lista\n    - anidada\n"),
    ("math_symbols_present", "La energía $E = mc^2$.", "Cuesta 5 dólares."),
    ("math_symbols_present", "Si x ≤ 3 entonces...", "Sin fórmulas."),
    ("file_operations_mentioned", "Leer el CSV de la carpeta datos", "Escribir la introducción"),
    ("file_operations_mentioned", "Arreglar parser.py", "Un python cualquiera"),
    ("architecture_terms_present", "Decidir la arquitectura del módulo", "Redactar la conclusión"),
    ("architecture_terms_present", "Revisar el marco teórico", "Diseñado por terceros"),
    ("debugging_terms_present", "Sale un traceback al cargar", "Pulir el estilo"),
    ("debugging_terms_present", "La depuración del cálculo", "Un depurado texto"),
    ("multi_step_reasoning_implied", "1. medir\n2. ajustar\n3. comparar\n", "1. medir\n"),
    ("multi_step_reasoning_implied", "Primero se calibra y después se compara.", "Se calibra."),
])
def test_conditions(expr, yes, no):
    assert holds(expr, inputs(yes))
    assert not holds(expr, inputs(no))


def test_negation_and_spec_aliases():
    code = inputs("```\nx\n```")
    assert not holds("not code_blocks_present", code)
    assert not holds("no_code_blocks", code)
    assert holds("no_code_blocks", inputs("texto"))
    assert holds("no_math_symbols", inputs("texto"))


def test_terms_ignore_case_and_accents():
    assert holds("debugging_terms_present", inputs("DEPURACION urgente"))
    assert holds("architecture_terms_present", inputs("Metodología"))


def test_extra_terms_from_the_config_add_to_the_defaults():
    i = inputs("Definir la ontología")
    assert not holds("architecture_terms_present", i)
    assert holds("architecture_terms_present", i, {"architecture": ["ontología"]})
    assert holds("architecture_terms_present", inputs("la arquitectura"), {"architecture": ["ontología"]})


def test_the_explanation_names_what_triggered_it():
    outcome = heuristics.parse("debugging_terms_present").evaluate(inputs("Sale un traceback"))
    assert outcome.detail == 'debugging term "traceback"'


# ---- scopes, on a real vault ----

@pytest.fixture
def vault(tmp_path, monkeypatch):
    run(monkeypatch, tmp_path, "init")
    run(monkeypatch, tmp_path, "add-card", "base", "--title", "Base",
        "--description", "Aparece un traceback al depurar")
    run(monkeypatch, tmp_path, "add-card", "cap1", "--title", "Uno", "--description", "Redactar la conclusión",
        "--depends-on", "base", "--authorized")
    return tmp_path


def gather(vault, card_id):
    data = canvas.load(vault)
    return heuristics.gather(vault, data, canvas.find_card(data, card_id))


def test_a_dependencys_terms_dont_count_but_its_size_does(vault):
    i = gather(vault, "cap1")
    assert "traceback" in i.full_text and "traceback" not in i.card_text
    assert not holds("debugging_terms_present", i)
    assert i.dependency_count == 1


def test_project_md_doesnt_inflate_the_size(vault):
    before = heuristics.MEASURES["token_count"](gather(vault, "cap1"))
    (vault / "PROJECT.md").write_text("palabra " * 5000, encoding="utf-8")
    (vault / "ARCHITECTURE.md").write_text("palabra " * 5000, encoding="utf-8")
    assert heuristics.MEASURES["token_count"](gather(vault, "cap1")) == before


def test_card_text_includes_feedback_and_sources_count(vault):
    md = vault / "content" / "cap1.md"
    meta, body = frontmatter.read(md)
    src = vault / "notas.txt"
    src.write_text("```\ncodigo\n```\n", encoding="utf-8")
    meta.update(status_note="Falta la arquitectura", sources=[str(src)])
    frontmatter.write(md, meta, body)
    i = gather(vault, "cap1")
    assert holds("architecture_terms_present", i)
    assert holds("code_blocks_present", i)  # from the source text, in the full context
    assert i.source_count == 1


@pytest.mark.parametrize("text", ["Compare their trade-offs", "Las decisiones de diseno", "Two interfaces", "Varios fallos"])
def test_plurals_match(text):
    assert holds("architecture_terms_present", inputs(text)) or holds("debugging_terms_present", inputs(text))


def test_plurals_dont_open_other_words():
    assert not holds("architecture_terms_present", inputs("A designated reviewer"))
    assert not holds("debugging_terms_present", inputs("Debuggers are tools"))  # not a plural of "debug"


def test_negated_conditions_read_naturally():
    plain = heuristics.parse("no_code_blocks").evaluate(inputs("texto"))
    assert (plain.holds, plain.detail) == (True, "no code blocks")
    files = heuristics.parse("not file_operations_mentioned").evaluate(inputs("texto"))
    assert (files.holds, files.detail) == (True, "no file operations terms")
    code = heuristics.parse("no_code_blocks").evaluate(inputs("```\nx\n```"))
    assert (code.holds, code.detail) == (False, "fenced code block")
