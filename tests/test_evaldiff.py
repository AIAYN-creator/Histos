import re

import pytest
from conftest import run

from histos import config


@pytest.fixture
def vault(tmp_path, monkeypatch):
    run(monkeypatch, tmp_path, "init")
    (tmp_path / "evals" / "len.yaml").write_text("- {name: min_word_count, check: word_count, min: 3}\n", encoding="utf-8")
    run(monkeypatch, tmp_path, "add-card", "cap3", "--title", "Tres")
    run(monkeypatch, tmp_path, "assign", "cap3")
    return tmp_path


def propose(vault, monkeypatch, text):
    draft = vault.parent / "draft.md"
    draft.write_text(text, encoding="utf-8")
    return run(monkeypatch, vault, "propose", "cap3", "--file", str(draft))


def diff(vault, monkeypatch, capsys, *extra):
    capsys.readouterr()
    run(monkeypatch, vault, "diff", "cap3", *extra)
    return capsys.readouterr().out


def test_the_evals_come_before_the_diff(vault, monkeypatch, capsys):
    propose(vault, monkeypatch, "corto")
    propose(vault, monkeypatch, "uno dos tres cuatro")
    out = diff(vault, monkeypatch, capsys)
    first = out.splitlines()[0]
    assert re.fullmatch(r"evals: 3 of 3 passed  \(attempt 2 of 3, \d{4}-\d\d-\d\d \d\d:\d\d\)", first), first
    assert "PASS  min_word_count" in out
    assert out.index("PASS  min_word_count") < out.index("--- content/cap3.md (current)")


def test_no_evals_flag_gives_only_the_diff(vault, monkeypatch, capsys):
    propose(vault, monkeypatch, "uno dos tres cuatro")
    assert diff(vault, monkeypatch, capsys, "--no-evals").startswith("--- content/cap3.md (current)")


def test_without_eval_results_the_diff_is_unchanged(vault, monkeypatch, capsys):
    (vault / config.CONFIG_FILENAME).unlink()
    propose(vault, monkeypatch, "uno")
    assert diff(vault, monkeypatch, capsys) == diff(vault, monkeypatch, capsys, "--no-evals")
    assert diff(vault, monkeypatch, capsys).startswith("--- content/cap3.md (current)")
