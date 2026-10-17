"""Every command fails with exit code 1 and an "error: ..." line -- never a traceback -- in the
usual wrong situations: no vault here, a card that doesn't exist, the wrong state, a missing
file. (Audit of v3: these were the least covered lines.)"""
import pytest
from conftest import run

NOT_A_VAULT = [
    ["add-card", "x", "--title", "X"], ["link", "x", "--depends-on", "y", "--authorized"], ["assign", "x"],
    ["describe", "x", "--text", "t"], ["propose", "x", "--file", "d.md"], ["diff", "x"], ["context", "x"],
    ["approve", "x"], ["reject", "x"], ["retry", "x"], ["accept-edit", "x"], ["status"], ["arrange"],
    ["validate"], ["classify", "x"], ["model", "x", "--proposed", "M"], ["eval", "x", "--file", "d.md"],
]


@pytest.mark.parametrize("argv", NOT_A_VAULT, ids=lambda a: a[0])
def test_outside_a_vault(tmp_path, monkeypatch, capsys, argv):
    assert run(monkeypatch, tmp_path, *argv) == 1
    assert "error:" in capsys.readouterr().err


UNKNOWN_CARD = [
    ["link", "nope", "--depends-on", "cap1", "--authorized"], ["link", "cap1", "--depends-on", "nope", "--authorized"],
    ["assign", "nope"], ["describe", "nope", "--text", "t"], ["propose", "nope", "--file", "draft.md"],
    ["diff", "nope"], ["context", "nope"], ["approve", "nope"], ["reject", "nope"], ["retry", "nope"],
    ["accept-edit", "nope"], ["classify", "nope"], ["model", "nope", "--proposed", "M"],
    ["eval", "nope", "--file", "draft.md"], ["add-card", "new", "--title", "N", "--depends-on", "nope", "--authorized"],
]


@pytest.fixture
def vault(tmp_path, monkeypatch):
    run(monkeypatch, tmp_path, "init")
    run(monkeypatch, tmp_path, "add-card", "cap1", "--title", "Uno")
    run(monkeypatch, tmp_path, "add-card", "cap2", "--title", "Dos", "--depends-on", "cap1", "--authorized")
    (tmp_path / "draft.md").write_text("Un borrador correcto y completo.\n", encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("argv", UNKNOWN_CARD, ids=lambda a: "-".join(a[:2]))
def test_a_card_that_doesnt_exist(vault, monkeypatch, capsys, argv):
    capsys.readouterr()
    assert run(monkeypatch, vault, *argv) == 1
    err = capsys.readouterr().err
    assert err.startswith("error:") and "nope" in err


WRONG_SITUATION = [
    (["add-card", "cap1", "--title", "Again"], "already exists"),
    (["add-card", "bad/id", "--title", "X"], "not valid"),
    (["add-card", "cap3", "--title", "X", "--depends-on", "cap1"], "authorization"),
    (["link", "cap2", "--depends-on", "cap1"], "authorization"),
    (["link", "cap1", "--depends-on", "cap1", "--authorized"], "itself"),
    (["link", "cap1", "--depends-on", "cap2", "--authorized"], "cycle"),
    (["assign", "cap2"], "Blocked"),
    (["describe", "cap1"], "--text, --sources"),
    (["describe", "cap1", "--sources", "missing.docx"], "can't find"),
    (["propose", "cap1", "--file", "draft.md"], "isn't In progress"),
    (["diff", "cap1"], "no pending proposal"),
    (["approve", "cap1"], "doesn't have a pending proposal"),
    (["reject", "cap1"], "doesn't have a pending proposal"),
    (["context", "cap1", "--for-subagent"], "isn't In progress"),
    (["classify"], "ids or --all"),
    (["model", "cap1"], "exactly one of"),
    (["guide", "diff", "PROJECT.md"], "no pending change"),
    (["guide", "reject", "PROJECT.md"], "no pending change"),
    (["guide", "propose", "PROJECT.md", "--file", "missing.md"], "can't find the draft"),
    (["eval", "cap1", "--file", "missing.md"], "can't find the draft"),
]


@pytest.mark.parametrize("argv, fragment", WRONG_SITUATION, ids=lambda a: " ".join(a) if isinstance(a, list) else "")
def test_the_wrong_situation(vault, monkeypatch, capsys, argv, fragment):
    capsys.readouterr()
    assert run(monkeypatch, vault, *argv) == 1
    err = capsys.readouterr().err
    assert err.startswith("error:") and fragment in err, err


def test_propose_with_a_missing_draft(vault, monkeypatch, capsys):
    run(monkeypatch, vault, "assign", "cap1")
    capsys.readouterr()
    assert run(monkeypatch, vault, "propose", "cap1", "--file", "missing.md") == 1
    assert "can't find the draft" in capsys.readouterr().err


def test_a_docx_source_reaches_the_context(vault, monkeypatch, capsys):
    docx = pytest.importorskip("docx")
    source = vault / "refs.docx"
    document = docx.Document()
    document.add_paragraph("Garcia 2020, the key reference.")
    document.save(str(source))
    run(monkeypatch, vault, "describe", "cap1", "--sources", str(source))
    capsys.readouterr()
    run(monkeypatch, vault, "context", "cap1")
    assert "Garcia 2020, the key reference." in capsys.readouterr().out


def test_an_unreadable_docx_source_is_reported_in_place(vault, monkeypatch, capsys):
    source = vault / "broken.docx"
    source.write_bytes(b"not really a docx")
    run(monkeypatch, vault, "describe", "cap1", "--sources", str(source))
    capsys.readouterr()
    assert run(monkeypatch, vault, "context", "cap1") == 0
    assert "couldn't read" in capsys.readouterr().out
