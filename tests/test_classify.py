import hashlib

import pytest

from histos import config, frontmatter
from histos.cli import build_parser

CONFIG = """\
tiers:
  - name: reasoning
    heuristics: ["debugging_terms_present"]
  - name: exec
    heuristics: ["code_blocks_present"]
  - name: fast
    match: all
    heuristics: ["token_count < 200", "no_code_blocks"]
"""


def run(monkeypatch, tmp_path, *argv):
    monkeypatch.chdir(tmp_path)
    args = build_parser().parse_args(list(argv))
    return args.func(args)


@pytest.fixture
def vault(tmp_path, monkeypatch):
    run(monkeypatch, tmp_path, "init")
    run(monkeypatch, tmp_path, "add-card", "corta", "--title", "Corta")
    run(monkeypatch, tmp_path, "add-card", "bug", "--title", "Incidencia", "--description", "Sale un traceback")
    run(monkeypatch, tmp_path, "add-card", "larga", "--title", "Larga", "--description", "palabra " * 150)
    (tmp_path / config.CONFIG_FILENAME).write_text(CONFIG, encoding="utf-8")
    return tmp_path


def tier_of(vault, card_id):
    return frontmatter.read(vault / "content" / f"{card_id}.md")[0].get("proposed_tier")


def set_meta(vault, card_id, **fields):
    md = vault / "content" / f"{card_id}.md"
    meta, body = frontmatter.read(md)
    meta.update(fields)
    frontmatter.write(md, meta, body)


def test_writes_the_tier_and_prints_the_reason(vault, monkeypatch, capsys):
    capsys.readouterr()
    assert run(monkeypatch, vault, "classify", "bug") == 0
    out = capsys.readouterr().out
    assert out.startswith("bug -> reasoning\n  reasoning (any): match -- debugging term \"traceback\"")
    assert tier_of(vault, "bug") == "reasoning"


def test_several_and_all(vault, monkeypatch, capsys):
    assert run(monkeypatch, vault, "classify", "corta", "bug") == 0
    assert (tier_of(vault, "corta"), tier_of(vault, "bug")) == ("fast", "reasoning")
    capsys.readouterr()
    assert run(monkeypatch, vault, "classify", "--all") == 0
    assert tier_of(vault, "larga") == "exec"
    assert "fast 1 · exec 1 · reasoning 1 (1 by default)" in capsys.readouterr().out


def test_all_skips_approved_and_an_approved_id_is_an_error(vault, monkeypatch, capsys):
    draft = vault / "d.md"
    draft.write_text("Hecho.\n", encoding="utf-8")
    run(monkeypatch, vault, "assign", "corta")
    run(monkeypatch, vault, "propose", "corta", "--file", str(draft))
    run(monkeypatch, vault, "approve", "corta")
    run(monkeypatch, vault, "classify", "--all")
    assert tier_of(vault, "corta") is None
    capsys.readouterr()
    assert run(monkeypatch, vault, "classify", "corta") == 1
    assert "already Approved" in capsys.readouterr().err


def test_a_tier_change_clears_the_models(vault, monkeypatch, capsys):
    set_meta(vault, "bug", proposed_tier="exec", proposed_model="Sonnet 5.5", approved_model="Sonnet 5.5")
    capsys.readouterr()
    run(monkeypatch, vault, "classify", "bug")
    err = capsys.readouterr().err
    assert "tier changed exec -> reasoning" in err and "approved_model 'Sonnet 5.5'" in err
    meta = frontmatter.read(vault / "content" / "bug.md")[0]
    assert "proposed_model" not in meta and "approved_model" not in meta


def test_same_tier_keeps_the_models(vault, monkeypatch):
    set_meta(vault, "bug", proposed_tier="reasoning", approved_model="Opus 5.5")
    run(monkeypatch, vault, "classify", "bug")
    assert frontmatter.read(vault / "content" / "bug.md")[0]["approved_model"] == "Opus 5.5"


def test_without_config_or_with_a_broken_one(vault, monkeypatch, capsys):
    (vault / config.CONFIG_FILENAME).unlink()
    assert run(monkeypatch, vault, "classify", "bug") == 1
    assert "no histos.config.yaml" in capsys.readouterr().err
    (vault / config.CONFIG_FILENAME).write_text("tiers: [unclosed\n", encoding="utf-8")
    assert run(monkeypatch, vault, "classify", "bug") == 1
    assert "is not valid" in capsys.readouterr().err


def test_ids_or_all_not_both_not_neither(vault, monkeypatch):
    assert run(monkeypatch, vault, "classify") == 1
    assert run(monkeypatch, vault, "classify", "bug", "--all") == 1


def _tree_hash(vault):
    h = hashlib.sha256()
    for p in sorted(vault.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(vault)).encode() + p.read_bytes())
    return h.hexdigest()


def test_dry_run_writes_nothing(vault, monkeypatch, capsys):
    before = _tree_hash(vault)
    capsys.readouterr()
    assert run(monkeypatch, vault, "classify", "--all", "--dry-run") == 0
    assert "-- dry run, nothing written" in capsys.readouterr().out
    assert _tree_hash(vault) == before
