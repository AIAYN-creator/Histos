import pytest

from histos import frontmatter
from histos.cli import build_parser


def run(monkeypatch, tmp_path, *argv):
    monkeypatch.chdir(tmp_path)
    args = build_parser().parse_args(list(argv))
    return args.func(args)


@pytest.fixture
def vault(tmp_path, monkeypatch):
    run(monkeypatch, tmp_path, "init")
    run(monkeypatch, tmp_path, "add-card", "base", "--title", "Base", "--description", "Lo previo")
    run(monkeypatch, tmp_path, "assign", "base")
    draft = tmp_path / "d.md"
    draft.write_text("Contenido aprobado de base.\n", encoding="utf-8")
    run(monkeypatch, tmp_path, "propose", "base", "--file", str(draft))
    run(monkeypatch, tmp_path, "approve", "base")
    run(monkeypatch, tmp_path, "add-card", "cap3", "--title", "Tres", "--description", "El capitulo tres",
        "--depends-on", "base", "--authorized")
    (tmp_path / "PROJECT.md").write_text("Brief del proyecto.\n", encoding="utf-8")
    (tmp_path / "ARCHITECTURE.md").write_text("Citas en APA.\n", encoding="utf-8")
    return tmp_path


def set_meta(vault, card_id, **fields):
    md = vault / "content" / f"{card_id}.md"
    meta, body = frontmatter.read(md)
    meta.update(fields)
    frontmatter.write(md, meta, body)


def brief(vault, monkeypatch, capsys, card_id="cap3"):
    capsys.readouterr()
    code = run(monkeypatch, vault, "context", card_id, "--for-subagent")
    out = capsys.readouterr()
    return code, out.out, out.err


def test_brief_has_everything_in_order(vault, monkeypatch, capsys):
    run(monkeypatch, vault, "assign", "cap3")
    set_meta(vault, "cap3", approved_model="Sonnet 5.5", proposed_tier="exec")
    code, out, err = brief(vault, monkeypatch, capsys)
    assert code == 0 and err == ""

    markers = [
        "# Subagent brief: cap3",
        "Approved model: Sonnet 5.5 (tier: exec).",
        "histos propose cap3 --file <draft.md>",
        "## Vault rules (AGENTS.md)",
        "## Card: cap3",
        "## Dependency: base",
        "Contenido aprobado de base.",
        "## Project brief (PROJECT.md)",
        "## Architecture (ARCHITECTURE.md)",
    ]
    positions = [out.index(m) for m in markers]
    assert positions == sorted(positions)
    assert "Never approve, reject, assign, link or arrange" in out
    assert "3 attempts in total" in out


def test_rejection_feedback_is_in_the_card_section(vault, monkeypatch, capsys):
    run(monkeypatch, vault, "assign", "cap3")
    set_meta(vault, "cap3", status_note="Faltan ejemplos")
    _, out, _ = brief(vault, monkeypatch, capsys)
    card_section = out[out.index("## Card: cap3"):out.index("## Dependency: base")]
    assert "Feedback on a rejected proposal: Faltan ejemplos" in card_section


def test_a_card_that_isnt_in_progress_is_refused(vault, monkeypatch, capsys):
    code, out, err = brief(vault, monkeypatch, capsys)
    assert code == 1 and out == ""
    assert "isn't In progress" in err


def test_no_approved_model_warns_on_stderr_only(vault, monkeypatch, capsys):
    run(monkeypatch, vault, "assign", "cap3")
    code, out, err = brief(vault, monkeypatch, capsys)
    assert code == 0
    assert "Approved model: none recorded." in out
    assert "no approved_model recorded" in err and "warning" not in out


def test_plain_context_is_unchanged(vault, monkeypatch, capsys):
    run(monkeypatch, vault, "assign", "cap3")
    capsys.readouterr()
    run(monkeypatch, vault, "context", "cap3")
    out = capsys.readouterr().out
    assert out.startswith("## Card: cap3")
    assert "Subagent brief" not in out and "AGENTS.md" not in out
