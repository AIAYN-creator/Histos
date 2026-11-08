"""The decisions log: rejection feedback on plans and guides, kept in PROJECT.md (cod-decisiones)."""
from datetime import datetime

import pytest
from conftest import run

from histos import decisions, events, guides

TODAY = datetime.now().strftime("%Y-%m-%d")
PLAN = "summary: s\ncards:\n  - {id: a, title: A, done_when: the tests of a pass}\n"

PROJECT_ES = """# Proyecto

## Objetivo
Algo.

## Decisiones
- 2026-11-01 · una decisión anterior

## Otra sección
Texto.
"""


@pytest.fixture
def vault(initialized_vault):
    (initialized_vault / "evals" / "guides.yaml").unlink()  # any guide draft will do here
    return initialized_vault


def write(vault, name, text):
    path = vault.parent / f"{vault.name}-{name}"
    path.write_text(text, encoding="utf-8")
    return str(path)


def reject_a_plan(vault, monkeypatch, feedback="Split it in two"):
    run(monkeypatch, vault, "plan", "propose", "--file", write(vault, "plan.yaml", PLAN))
    args = ["plan", "reject"] + (["--feedback", feedback] if feedback else [])
    assert run(monkeypatch, vault, *args) == 0


def test_a_plan_rejection_goes_to_the_end_of_the_section(vault, monkeypatch):
    (vault / "PROJECT.md").write_text(PROJECT_ES, encoding="utf-8")
    reject_a_plan(vault, monkeypatch, "Split  it\nin two")
    expected = PROJECT_ES.replace(
        "- 2026-11-01 · una decisión anterior\n",
        f"- 2026-11-01 · una decisión anterior\n- {TODAY} · plan rejected: Split it in two\n")
    assert (vault / "PROJECT.md").read_text(encoding="utf-8") == expected
    [kept] = (vault / "approved" / "guides").iterdir()
    assert kept.read_text(encoding="utf-8") == PROJECT_ES
    assert events.read(vault)[-1]["data"]["recorded_in"] == "PROJECT.md"


@pytest.mark.parametrize("heading", ["## Decisions", "### decisiones", "## DECISIONES"])
def test_any_spelling_and_level_of_the_section(vault, monkeypatch, heading):
    (vault / "PROJECT.md").write_text(f"# P\n\n{heading}\n\n## Next\n", encoding="utf-8")
    reject_a_plan(vault, monkeypatch)
    assert (vault / "PROJECT.md").read_text(encoding="utf-8") == (
        f"# P\n\n{heading}\n\n- {TODAY} · plan rejected: Split it in two\n\n## Next\n")


def test_a_section_at_the_end_without_a_final_newline(vault, monkeypatch):
    (vault / "PROJECT.md").write_text("# P\n\n## Decisions\n- old", encoding="utf-8")
    reject_a_plan(vault, monkeypatch)
    assert (vault / "PROJECT.md").read_text(encoding="utf-8") == (
        f"# P\n\n## Decisions\n- old\n- {TODAY} · plan rejected: Split it in two\n")


def test_crlf_files_stay_crlf(vault, monkeypatch):
    (vault / "PROJECT.md").write_bytes(PROJECT_ES.replace("\n", "\r\n").encode("utf-8"))
    reject_a_plan(vault, monkeypatch)
    raw = (vault / "PROJECT.md").read_bytes().decode("utf-8")
    assert "\n" not in raw.replace("\r\n", "") and f"plan rejected: Split it in two\r\n" in raw


def test_a_pending_project_md_gets_it_too(vault, monkeypatch):
    (vault / "PROJECT.md").write_text(PROJECT_ES, encoding="utf-8")
    run(monkeypatch, vault, "guide", "propose", "PROJECT.md",
        "--file", write(vault, "p.md", PROJECT_ES.replace("Algo.", "Algo mejor.")))
    reject_a_plan(vault, monkeypatch)
    assert events.read(vault)[-1]["data"]["recorded_in"] == "PROJECT.md+pending"
    run(monkeypatch, vault, "guide", "approve", "PROJECT.md")
    text = (vault / "PROJECT.md").read_text(encoding="utf-8")
    assert "Algo mejor." in text and "plan rejected: Split it in two" in text


def test_without_project_md_it_goes_to_the_fallback_and_context_shows_it(vault, monkeypatch, capsys):
    reject_a_plan(vault, monkeypatch, "First reason")
    reject_a_plan(vault, monkeypatch, "Second reason")
    assert not (vault / "PROJECT.md").exists()
    text = (vault / ".histos" / "decisions.md").read_text(encoding="utf-8")
    assert text == (f"# Decisions not yet in PROJECT.md\n\n- {TODAY} · plan rejected: First reason\n"
                    f"- {TODAY} · plan rejected: Second reason\n")
    assert events.read(vault)[-1]["data"]["recorded_in"] == ".histos/decisions.md"
    run(monkeypatch, vault, "add-card", "c", "--title", "C")
    capsys.readouterr()
    run(monkeypatch, vault, "context", "c")
    out = capsys.readouterr().out
    assert "## Decisions not yet in PROJECT.md" in out and "plan rejected: Second reason" in out


def test_a_project_md_without_the_section_is_left_alone(vault, monkeypatch):
    (vault / "PROJECT.md").write_text("# Old style brief\n", encoding="utf-8")
    reject_a_plan(vault, monkeypatch)
    assert (vault / "PROJECT.md").read_text(encoding="utf-8") == "# Old style brief\n"
    assert (vault / ".histos" / "decisions.md").exists()
    assert not (vault / "approved" / "guides").exists()


def test_a_rejected_project_md_change_goes_to_the_approved_one(vault, monkeypatch):
    (vault / "PROJECT.md").write_text(PROJECT_ES, encoding="utf-8")
    run(monkeypatch, vault, "guide", "propose", "PROJECT.md", "--file", write(vault, "p.md", "# Otra cosa\n"))
    run(monkeypatch, vault, "guide", "reject", "PROJECT.md", "--feedback", "No cambies el alcance")
    assert f"- {TODAY} · PROJECT.md change rejected: No cambies el alcance\n" in (
        vault / "PROJECT.md").read_text(encoding="utf-8")
    assert guides.pending(vault) == []


def test_architecture_md_rejections_are_kept_too(vault, monkeypatch):
    (vault / "PROJECT.md").write_text(PROJECT_ES, encoding="utf-8")
    run(monkeypatch, vault, "guide", "propose", "ARCHITECTURE.md", "--file", write(vault, "a.md", "Go.\n"))
    run(monkeypatch, vault, "guide", "reject", "ARCHITECTURE.md", "--feedback", "Python, not Go")
    assert "ARCHITECTURE.md change rejected: Python, not Go" in (vault / "PROJECT.md").read_text(encoding="utf-8")


def test_no_feedback_records_nothing(vault, monkeypatch):
    (vault / "PROJECT.md").write_text(PROJECT_ES, encoding="utf-8")
    reject_a_plan(vault, monkeypatch, feedback=None)
    assert (vault / "PROJECT.md").read_text(encoding="utf-8") == PROJECT_ES
    assert not (vault / ".histos" / "decisions.md").exists()
    assert events.read(vault)[-1]["data"]["recorded_in"] is None


def test_the_subagent_brief_includes_them(vault, monkeypatch, capsys):
    (vault / "PROJECT.md").write_text(PROJECT_ES, encoding="utf-8")
    reject_a_plan(vault, monkeypatch)
    run(monkeypatch, vault, "add-card", "c", "--title", "C")
    run(monkeypatch, vault, "assign", "c")
    capsys.readouterr()
    run(monkeypatch, vault, "context", "c", "--for-subagent")
    assert "plan rejected: Split it in two" in capsys.readouterr().out


def test_empty_section_gets_a_blank_line_under_the_heading():
    assert decisions._append_to_section("## Decisions\n## Next\n", "- x") == "## Decisions\n\n- x\n## Next\n"
