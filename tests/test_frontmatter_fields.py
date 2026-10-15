import pytest

from histos import canvas, frontmatter, validation
from histos.cli import build_parser

GOOD_EVALS = {
    "passed": False,
    "attempt": 2,
    "at": "2026-10-15T16:40:00+02:00",
    "checks": [
        {"name": "min_word_count", "passed": False, "error": "Too short", "detail": "212 words"},
        {"name": "content_untouched", "passed": True},
    ],
}


def run(monkeypatch, tmp_path, *argv):
    monkeypatch.chdir(tmp_path)
    args = build_parser().parse_args(list(argv))
    return args.func(args)


@pytest.fixture
def vault(tmp_path, monkeypatch):
    run(monkeypatch, tmp_path, "init")
    run(monkeypatch, tmp_path, "add-card", "cap1", "--title", "Uno")
    return tmp_path


def set_meta(vault, **fields):
    md = vault / "content" / "cap1.md"
    meta, body = frontmatter.read(md)
    meta.update(fields)
    frontmatter.write(md, meta, body)


def errors(vault):
    return validation.validate_frontmatter(vault, canvas.load(vault))


def test_new_cards_pass_and_dont_get_the_new_fields(vault):
    assert errors(vault) == []
    meta, _ = frontmatter.read(vault / "content" / "cap1.md")
    for field in ("proposed_tier", "proposed_model", "approved_model", "eval_results"):
        assert field not in meta


def test_all_four_fields_well_formed(vault):
    set_meta(vault, proposed_tier="exec", proposed_model="Sonnet 5.5", approved_model="Opus 5.5",
             eval_results=GOOD_EVALS)
    assert errors(vault) == []


@pytest.mark.parametrize("fields, fragment", [
    ({"proposed_tier": "medium"}, "proposed_tier"),
    ({"proposed_model": ""}, "proposed_model"),
    ({"approved_model": 5}, "approved_model"),
    ({"eval_results": {"checks": []}}, "'passed' is a required property"),
    ({"eval_results": {"passed": True, "checks": [{"passed": True}]}}, "'name' is a required property"),
    ({"eval_results": {"passed": True, "checks": [], "attempt": 0}}, "attempt"),
    ({"sources": "notes.docx"}, "sources"),
    ({"assigned_to": "robot"}, "assigned_to"),
    ({"implied_dependencies": ["../etc"]}, "implied_dependencies"),
])
def test_bad_fields_are_reported_with_the_card_id(vault, fields, fragment):
    set_meta(vault, **fields)
    errs = errors(vault)
    assert errs and all(e.startswith("frontmatter: cap1: ") for e in errs)
    assert any(fragment in e for e in errs), errs


def test_unknown_fields_are_yours(vault):
    set_meta(vault, mi_etiqueta="x", tags=["tesis"])
    assert errors(vault) == []


def test_broken_yaml_is_reported_not_raised(vault):
    md = vault / "content" / "cap1.md"
    md.write_text("---\ndescription: [unclosed\n---\nbody\n", encoding="utf-8")
    errs = errors(vault)
    assert len(errs) == 1 and "can't parse it as YAML" in errs[0]


def test_validate_command_reports_it(vault, monkeypatch, capsys):
    set_meta(vault, proposed_tier="medium")
    capsys.readouterr()
    assert run(monkeypatch, vault, "validate") == 1
    assert "frontmatter: cap1: proposed_tier" in capsys.readouterr().out


def test_odd_frontmatter_does_not_block_other_commands(vault, monkeypatch):
    set_meta(vault, proposed_tier="medium")
    assert run(monkeypatch, vault, "status") == 0
    assert run(monkeypatch, vault, "assign", "cap1") == 0
