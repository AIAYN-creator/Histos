import hashlib

import pytest

from histos import canvas, checks, config, evals, validation
from histos.cli import build_parser

CONFIG = """\
tiers:
  - name: reasoning
    heuristics: []
  - name: exec
    heuristics: []
  - name: fast
    heuristics: []
evals:
  enabled: true
  path: evals/
"""


def run(monkeypatch, tmp_path, *argv):
    monkeypatch.chdir(tmp_path)
    args = build_parser().parse_args(list(argv))
    return args.func(args)


@pytest.fixture
def vault(tmp_path, monkeypatch):
    run(monkeypatch, tmp_path, "init")
    run(monkeypatch, tmp_path, "add-card", "cap3", "--title", "Tres")
    run(monkeypatch, tmp_path, "add-card", "cod-lock", "--title", "Codigo")
    (tmp_path / config.CONFIG_FILENAME).write_text(CONFIG, encoding="utf-8")
    (tmp_path / "evals").mkdir()
    (tmp_path / "evals" / "a.yaml").write_text(
        "- {name: no_direct_writes, check: content_untouched}\n"
        "- {name: min_word_count, check: word_count, min: 5, error: Al menos 5 palabras, only: ['cap*']}\n",
        encoding="utf-8",
    )
    (tmp_path / "evals" / "b.yaml").write_text("- {name: no_placeholders, check: no_placeholders}\n", encoding="utf-8")
    return tmp_path


def run_all(vault, card_id, draft):
    data = canvas.load(vault)
    return evals.run_all(vault, config.load(vault), data, canvas.find_card(data, card_id), draft)


def test_all_pass(vault):
    result = run_all(vault, "cap3", "uno dos tres cuatro cinco")
    assert result.passed and [c["name"] for c in result.checks] == ["no_direct_writes", "min_word_count", "no_placeholders"]


def test_every_eval_runs_even_after_a_failure(vault):
    result = run_all(vault, "cap3", "corto TODO")
    assert not result.passed
    assert [(c["name"], c["passed"]) for c in result.checks] == [
        ("no_direct_writes", True), ("min_word_count", False), ("no_placeholders", False),
    ]


def test_own_error_beats_the_default_message(vault):
    by_name = {c["name"]: c for c in run_all(vault, "cap3", "corto TODO").checks}
    assert by_name["min_word_count"]["error"] == "Al menos 5 palabras"
    assert by_name["min_word_count"]["detail"] == "2 words (min 5)"
    assert by_name["no_placeholders"]["error"].startswith("the draft still has a placeholder")


def test_evals_that_dont_apply_are_counted_not_listed(vault):
    result = run_all(vault, "cod-lock", "x")
    assert "min_word_count" not in [c["name"] for c in result.checks]
    assert result.skipped == 1
    assert "(1 eval doesn't apply to cod-lock)" in result.render("cod-lock")


def test_a_crashing_check_is_a_failure(vault, monkeypatch):
    def boom(i):
        raise OSError("disk unreadable")
    monkeypatch.setitem(checks.RUNNERS, "content_untouched", boom)
    by_name = {c["name"]: c for c in run_all(vault, "cap3", "uno dos tres cuatro cinco").checks}
    assert by_name["no_direct_writes"]["passed"] is False
    assert by_name["no_direct_writes"]["detail"] == "check crashed: disk unreadable"


def test_no_evals_means_pass(vault):
    (vault / config.CONFIG_FILENAME).unlink()
    result = run_all(vault, "cap3", "x")
    assert result.passed and result.checks == [] and result.render("cap3") == "evals: none apply"


def test_invalid_folder_is_an_error(vault):
    (vault / "evals" / "c.yaml").write_text("- {name: x, check: nope}\n", encoding="utf-8")
    with pytest.raises(canvas.HistosError):
        run_all(vault, "cap3", "x")


def test_results_fit_the_frontmatter_schema(vault):
    from jsonschema import Draft202012Validator
    schema = validation._load_named_schema("histos-frontmatter.schema.json")
    result = run_all(vault, "cap3", "corto TODO")
    eval_results = {"passed": result.passed, "attempt": 1, "at": "2026-10-16T12:00:00+02:00", "checks": result.checks}
    assert list(Draft202012Validator(schema).iter_errors({"eval_results": eval_results})) == []


def _tree_hash(vault):
    h = hashlib.sha256()
    for p in sorted(vault.rglob("*")):
        if p.is_file() and p.name != ".histos.lock":
            h.update(str(p.relative_to(vault)).encode() + p.read_bytes())
    return h.hexdigest()


def test_eval_command_is_read_only_and_exits_1_on_failure(vault, monkeypatch, capsys):
    draft = vault.parent / "draft.md"
    draft.write_text("corto TODO", encoding="utf-8")
    before = _tree_hash(vault)
    capsys.readouterr()
    assert run(monkeypatch, vault, "eval", "cap3", "--file", str(draft)) == 1
    out = capsys.readouterr().out
    assert out.startswith("evals: 1 of 3 passed")
    assert "FAIL  min_word_count" in out and "Al menos 5 palabras" in out
    assert _tree_hash(vault) == before

    draft.write_text("uno dos tres cuatro cinco", encoding="utf-8")
    assert run(monkeypatch, vault, "eval", "cap3", "--file", str(draft)) == 0
