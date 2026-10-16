import pytest

from histos import config, evals
from histos.cli import build_parser
from histos.errors import HistosError

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

GOOD = """\
- name: min_word_count
  check: word_count
  min: 300
  error: "Al menos 300 palabras"
  only: ["cap*"]
- name: no_direct_writes
  check: content_untouched
"""


def run(monkeypatch, tmp_path, *argv):
    monkeypatch.chdir(tmp_path)
    args = build_parser().parse_args(list(argv))
    return args.func(args)


@pytest.fixture
def vault(tmp_path):
    (tmp_path / config.CONFIG_FILENAME).write_text(CONFIG, encoding="utf-8")
    (tmp_path / "evals").mkdir()
    return tmp_path


def write_eval(vault, name, text):
    (vault / "evals" / name).write_text(text, encoding="utf-8")


def load(vault):
    return evals.load(vault, config.load(vault))


def test_a_valid_file_loads(vault):
    write_eval(vault, "longitud.yaml", GOOD)
    loaded = load(vault)
    assert [(e.name, e.check, e.params) for e in loaded] == [
        ("min_word_count", "word_count", {"min": 300}),
        ("no_direct_writes", "content_untouched", {}),
    ]
    assert loaded[0].error == "Al menos 300 palabras" and loaded[0].file == "longitud.yaml"


def test_no_config_or_evals_off_means_no_evals(tmp_path, vault):
    assert evals.load(tmp_path / "nowhere", None) == []
    (vault / config.CONFIG_FILENAME).write_text(CONFIG.replace("enabled: true", "enabled: false"), encoding="utf-8")
    write_eval(vault, "roto.yaml", "- [unclosed\n")
    assert load(vault) == [] and evals.validate(vault, config.load(vault)) == []


def test_files_load_alphabetically_and_non_yaml_is_ignored(vault):
    write_eval(vault, "b.yaml", "- {name: second, check: content_untouched}\n")
    write_eval(vault, "a.yml", "- {name: first, check: word_count, max: 10}\n")
    write_eval(vault, "README.md", "# Notas sobre las evals\n")
    assert [e.name for e in load(vault)] == ["first", "second"]


@pytest.mark.parametrize("text, fragment", [
    ("- {name: x, check: word_cuont, min: 3}\n", "unknown check 'word_cuont'"),
    ("- {name: x, check: word_count, minimum: 3}\n", "has no parameter 'minimum'"),
    ("- {name: x, check: word_count, min: trescientos}\n", "'min' must be a whole number"),
    ("- {name: x, check: word_count, min: -1}\n", "'min' must be a whole number"),
    ("- {name: x, check: word_count}\n", "needs 'min', 'max' or both"),
    ("- {name: x, check: word_count, min: 10, max: 5}\n", "'min' is greater than 'max'"),
    ("- {name: x, check: content_untouched, strict: true}\n", "allowed: none"),
    ("- {check: content_untouched}\n", "'name' is required"),
    ("- {name: x, check: content_untouched, only: cap1}\n", "'only' must be a non-empty list"),
    ("- {name: x, check: content_untouched, except: ['../etc']}\n", "is not a card id pattern"),
    ("- [unclosed\n", "can't parse it as YAML"),
    ("name: x\ncheck: content_untouched\n", "must be a list of evals"),
])
def test_invalid_evals(vault, text, fragment):
    write_eval(vault, "malas.yaml", text)
    errors = evals.validate(vault, config.load(vault))
    assert errors and any(fragment in e for e in errors), errors
    assert all(e.startswith("malas.yaml") for e in errors)
    with pytest.raises(HistosError, match="evals/ folder is not valid"):
        load(vault)


def test_names_are_unique_across_files(vault):
    write_eval(vault, "a.yaml", "- {name: dup, check: content_untouched}\n")
    write_eval(vault, "b.yaml", "- {name: dup, check: word_count, min: 1}\n")
    assert any("already used in a.yaml" in e for e in evals.validate(vault, config.load(vault)))


def test_missing_folder_is_reported(vault):
    (vault / "evals").rmdir()
    assert any("doesn't exist" in e for e in evals.validate(vault, config.load(vault)))


@pytest.mark.parametrize("only, except_, card_id, expected", [
    ([], [], "cod-lock", True),
    (["cap*"], [], "cap3", True),
    (["cap*"], [], "cod-lock", False),
    ([], ["cod-*", "dec-*"], "cod-lock", False),
    ([], ["cod-*", "dec-*"], "cap3", True),
    (["c*"], ["cod-*"], "cap3", True),
    (["c*"], ["cod-*"], "cod-lock", False),
])
def test_only_and_except(only, except_, card_id, expected):
    e = evals.Eval("x", "content_untouched", {}, None, only=only, except_=except_)
    assert e.applies_to(card_id) is expected


def test_validate_command_reports_evals_errors(vault, monkeypatch, capsys):
    run(monkeypatch, vault, "init")
    write_eval(vault, "malas.yaml", "- {name: x, check: word_cuont}\n")
    capsys.readouterr()
    assert run(monkeypatch, vault, "validate") == 1
    assert "evals: malas.yaml: 0: unknown check" in capsys.readouterr().out
