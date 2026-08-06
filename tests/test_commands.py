import shutil
from pathlib import Path

from trellis import canvas, frontmatter
from trellis.cli import build_parser


def run(monkeypatch, tmp_path, *argv):
    monkeypatch.chdir(tmp_path)
    parser = build_parser()
    args = parser.parse_args(list(argv))
    return args.func(args)


def test_full_loop(tmp_path, monkeypatch):
    assert run(monkeypatch, tmp_path, "init") == 0

    assert run(monkeypatch, tmp_path, "add-card", "cap1", "--title", "Intro") == 0
    assert run(
        monkeypatch, tmp_path, "add-card", "cap2", "--title", "Cap 2",
        "--depends-on", "cap1", "--authorized",
    ) == 0

    data = canvas.load(tmp_path)
    assert canvas.find_card(data, "cap1")["color"] == canvas.BACKLOG
    assert canvas.find_card(data, "cap2")["color"] == canvas.BLOQUEADA

    assert run(monkeypatch, tmp_path, "assign", "cap1") == 0
    assert canvas.find_card(canvas.load(tmp_path), "cap1")["color"] == canvas.EN_PROGRESO

    draft = tmp_path / "borrador.md"
    draft.write_text("# Intro\n\nContenido propuesto.\n", encoding="utf-8")
    assert run(monkeypatch, tmp_path, "propose", "cap1", "--file", str(draft)) == 0
    assert canvas.find_card(canvas.load(tmp_path), "cap1")["color"] == canvas.PROPUESTA_PENDIENTE

    assert run(monkeypatch, tmp_path, "diff", "cap1") == 0

    assert run(monkeypatch, tmp_path, "approve", "cap1") == 0
    data = canvas.load(tmp_path)
    assert canvas.find_card(data, "cap1")["color"] == canvas.APROBADA
    assert canvas.find_card(data, "cap2")["color"] == canvas.BACKLOG  # se desbloqueo sola

    body = (tmp_path / "content" / "cap1.md").read_text(encoding="utf-8")
    assert "Contenido propuesto." in body


def test_add_card_without_authorized_fails(tmp_path, monkeypatch):
    run(monkeypatch, tmp_path, "init")
    run(monkeypatch, tmp_path, "add-card", "cap1", "--title", "Intro")
    result = run(monkeypatch, tmp_path, "add-card", "cap2", "--title", "Cap 2", "--depends-on", "cap1")
    assert result != 0
    assert canvas.find_card(canvas.load(tmp_path), "cap2") is None


def test_reject_records_feedback_and_returns_to_backlog(tmp_path, monkeypatch):
    run(monkeypatch, tmp_path, "init")
    run(monkeypatch, tmp_path, "add-card", "cap1", "--title", "Intro")
    run(monkeypatch, tmp_path, "assign", "cap1")
    draft = tmp_path / "borrador.md"
    draft.write_text("contenido\n", encoding="utf-8")
    run(monkeypatch, tmp_path, "propose", "cap1", "--file", str(draft))

    assert run(monkeypatch, tmp_path, "reject", "cap1", "--feedback", "falta contexto") == 0

    assert canvas.find_card(canvas.load(tmp_path), "cap1")["color"] == canvas.BACKLOG
    meta, _ = frontmatter.read(tmp_path / "content" / "cap1.md")
    assert meta["status_note"] == "falta contexto"
    assert not (tmp_path / ".trellis" / "proposals" / "cap1.md").exists()


def test_validate_command_on_example_canvas(tmp_path, monkeypatch):
    repo_root = Path(__file__).resolve().parents[1]
    shutil.copy(repo_root / "examples" / "example.canvas", tmp_path / "project.canvas")
    assert run(monkeypatch, tmp_path, "validate") == 0
