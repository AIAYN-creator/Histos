import pytest

from histos import canvas, config, frontmatter, validation
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
    run(monkeypatch, tmp_path, "assign", "cap3")
    (tmp_path / config.CONFIG_FILENAME).write_text(CONFIG, encoding="utf-8")
    (tmp_path / "evals").mkdir()
    (tmp_path / "evals" / "e.yaml").write_text(
        "- {name: min_word_count, check: word_count, min: 5}\n- {name: no_direct_writes, check: content_untouched}\n",
        encoding="utf-8",
    )
    return tmp_path


GOOD = "uno dos tres cuatro cinco"
BAD = "corto"


def propose(vault, monkeypatch, text):
    draft = vault.parent / "draft.md"
    draft.write_text(text, encoding="utf-8")
    return run(monkeypatch, vault, "propose", "cap3", "--file", str(draft))


def color(vault):
    return canvas.find_card(canvas.load(vault), "cap3")["color"]


def results(vault):
    return frontmatter.read(vault / "content" / "cap3.md")[0].get("eval_results")


def test_passing_goes_to_review(vault, monkeypatch, capsys):
    capsys.readouterr()
    assert propose(vault, monkeypatch, GOOD) == 0
    assert "evals: 2 of 2 passed  (attempt 1 of 3)" in capsys.readouterr().out
    assert color(vault) == canvas.PROPUESTA_PENDIENTE
    r = results(vault)
    assert r["passed"] is True and r["attempt"] == 1 and len(r["checks"]) == 2
    assert validation.validate_frontmatter(vault, canvas.load(vault)) == []


def test_failing_stays_in_progress(vault, monkeypatch, capsys):
    capsys.readouterr()
    assert propose(vault, monkeypatch, BAD) == 1
    out = capsys.readouterr()
    assert "FAIL  min_word_count" in out.out and "2 attempts left" in out.err
    assert color(vault) == canvas.EN_PROGRESO
    assert not (vault / "proposals" / "cap3.md").exists()
    assert results(vault)["passed"] is False


def test_attempts_count_up_and_a_pass_ends_the_cycle(vault, monkeypatch):
    propose(vault, monkeypatch, BAD)
    propose(vault, monkeypatch, BAD)
    assert results(vault)["attempt"] == 2
    assert propose(vault, monkeypatch, GOOD) == 0
    assert results(vault)["attempt"] == 3 and color(vault) == canvas.PROPUESTA_PENDIENTE


def test_three_failures_stop_it_until_the_human_retries(vault, monkeypatch, capsys):
    for _ in range(3):
        propose(vault, monkeypatch, BAD)
    capsys.readouterr()
    assert propose(vault, monkeypatch, GOOD) == 1
    err = capsys.readouterr().err
    assert "used its 3 attempts" in err and "histos retry cap3" in err
    assert results(vault)["attempt"] == 3  # the evals didn't even run

    assert run(monkeypatch, vault, "retry", "cap3") == 0
    assert propose(vault, monkeypatch, GOOD) == 0
    assert results(vault)["attempt"] == 1


def test_retry_needs_failed_evals(vault, monkeypatch, capsys):
    assert run(monkeypatch, vault, "retry", "cap3") == 1
    assert "no failed evals" in capsys.readouterr().err


def test_after_a_reject_the_count_starts_over(vault, monkeypatch):
    propose(vault, monkeypatch, BAD)
    propose(vault, monkeypatch, GOOD)
    run(monkeypatch, vault, "reject", "cap3", "--feedback", "otra cosa")
    run(monkeypatch, vault, "assign", "cap3")
    propose(vault, monkeypatch, BAD)
    assert results(vault)["attempt"] == 1


def test_without_config_it_is_v2(vault, monkeypatch):
    (vault / config.CONFIG_FILENAME).unlink()
    assert propose(vault, monkeypatch, BAD) == 0
    assert color(vault) == canvas.PROPUESTA_PENDIENTE and results(vault) is None


def test_evals_disabled_is_v2(vault, monkeypatch):
    (vault / config.CONFIG_FILENAME).write_text(CONFIG.replace("enabled: true", "enabled: false"), encoding="utf-8")
    assert propose(vault, monkeypatch, BAD) == 0 and results(vault) is None


def test_no_applicable_evals_passes_with_an_empty_list(vault, monkeypatch):
    for f in (vault / "evals").iterdir():
        f.unlink()
    assert propose(vault, monkeypatch, BAD) == 0
    assert results(vault)["checks"] == [] and results(vault)["passed"] is True


@pytest.mark.parametrize("breakage", ["config", "evals"])
def test_broken_config_or_evals_block_without_spending_an_attempt(vault, monkeypatch, capsys, breakage):
    propose(vault, monkeypatch, BAD)
    if breakage == "config":
        (vault / config.CONFIG_FILENAME).write_text("tiers: [unclosed\n", encoding="utf-8")
    else:
        (vault / "evals" / "roto.yaml").write_text("- {name: x, check: nope}\n", encoding="utf-8")
    capsys.readouterr()
    assert propose(vault, monkeypatch, GOOD) == 1
    assert "not valid" in capsys.readouterr().err
    assert results(vault)["attempt"] == 1 and color(vault) == canvas.EN_PROGRESO
