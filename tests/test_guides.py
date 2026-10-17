import pytest
from conftest import run


@pytest.fixture
def vault(tmp_path, monkeypatch):
    run(monkeypatch, tmp_path, "init")
    run(monkeypatch, tmp_path, "add-card", "cap1", "--title", "Uno")
    (tmp_path / "PROJECT.md").write_text("Version uno.\n", encoding="utf-8")
    return tmp_path


def draft(vault, text):
    path = vault / "borrador-guia.md"
    path.write_text(text, encoding="utf-8")
    return str(path)


def out_of(capsys):
    return capsys.readouterr()


def test_full_cycle_writes_the_file_and_keeps_the_old_version(vault, monkeypatch, capsys):
    assert run(monkeypatch, vault, "guide", "propose", "PROJECT.md", "--file", draft(vault, "Version dos.\n")) == 0
    assert (vault / "PROJECT.md").read_text(encoding="utf-8") == "Version uno.\n"  # nothing written yet

    out_of(capsys)
    assert run(monkeypatch, vault, "guide", "diff", "PROJECT.md") == 0
    diff = out_of(capsys).out
    assert "-Version uno." in diff and "+Version dos." in diff

    assert run(monkeypatch, vault, "guide", "approve", "PROJECT.md") == 0
    assert (vault / "PROJECT.md").read_text(encoding="utf-8") == "Version dos.\n"
    archived = list((vault / "approved" / "guides").glob("PROJECT-*.md"))
    assert len(archived) == 1 and archived[0].read_text(encoding="utf-8") == "Version uno.\n"
    assert not (vault / "proposals" / "guides" / "PROJECT.md").exists()


def test_reject_discards_and_the_agent_sees_the_feedback(vault, monkeypatch, capsys):
    run(monkeypatch, vault, "guide", "propose", "PROJECT.md", "--file", draft(vault, "Otra cosa.\n"))
    assert run(monkeypatch, vault, "guide", "reject", "PROJECT.md", "--feedback", "No cambies el alcance") == 0
    assert (vault / "PROJECT.md").read_text(encoding="utf-8") == "Version uno.\n"
    assert not (vault / "proposals" / "guides" / "PROJECT.md").exists()

    out_of(capsys)
    run(monkeypatch, vault, "context", "cap1")
    assert "Feedback on a rejected PROJECT.md change: No cambies el alcance" in out_of(capsys).out

    # the next proposal answers it, so it stops showing
    run(monkeypatch, vault, "guide", "propose", "PROJECT.md", "--file", draft(vault, "Mejor.\n"))
    out_of(capsys)
    run(monkeypatch, vault, "context", "cap1")
    assert "Feedback on a rejected" not in out_of(capsys).out


def test_create_architecture_md_from_scratch(vault, monkeypatch, capsys):
    run(monkeypatch, vault, "guide", "propose", "architecture.md", "--file", draft(vault, "Citas en APA.\n"))
    out_of(capsys)
    run(monkeypatch, vault, "guide", "diff", "ARCHITECTURE.md")
    assert "+Citas en APA." in out_of(capsys).out
    assert run(monkeypatch, vault, "guide", "approve", "ARCHITECTURE.md") == 0
    assert (vault / "ARCHITECTURE.md").read_text(encoding="utf-8") == "Citas en APA.\n"
    assert not (vault / "approved" / "guides").exists()  # nothing to keep


def test_only_the_two_guide_files(vault, monkeypatch, capsys):
    assert run(monkeypatch, vault, "guide", "propose", "notas.md", "--file", draft(vault, "x\n")) == 1
    assert "only PROJECT.md and ARCHITECTURE.md" in out_of(capsys).err
    assert run(monkeypatch, vault, "guide", "propose", "../PROJECT.md", "--file", draft(vault, "x\n")) == 1


def test_approve_without_a_pending_change(vault, monkeypatch, capsys):
    assert run(monkeypatch, vault, "guide", "approve", "PROJECT.md") == 1
    assert "no pending change for PROJECT.md" in out_of(capsys).err


def test_proposing_again_replaces_and_warns(vault, monkeypatch, capsys):
    run(monkeypatch, vault, "guide", "propose", "PROJECT.md", "--file", draft(vault, "A\n"))
    out_of(capsys)
    run(monkeypatch, vault, "guide", "propose", "PROJECT.md", "--file", draft(vault, "B\n"))
    assert "replaced the change to PROJECT.md" in out_of(capsys).err
    assert (vault / "proposals" / "guides" / "PROJECT.md").read_text(encoding="utf-8") == "B\n"


def test_status_lists_pending_guides(vault, monkeypatch, capsys):
    out_of(capsys)
    run(monkeypatch, vault, "status")
    assert "Guides pending review" not in out_of(capsys).out
    run(monkeypatch, vault, "guide", "propose", "PROJECT.md", "--file", draft(vault, "B\n"))
    out_of(capsys)
    run(monkeypatch, vault, "status")
    assert "Guides pending review: PROJECT.md" in out_of(capsys).out
