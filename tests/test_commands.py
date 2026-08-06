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


def test_propose_uses_visible_propuestas_folder(tmp_path, monkeypatch):
    run(monkeypatch, tmp_path, "init")
    assert (tmp_path / "propuestas").is_dir()
    assert not (tmp_path / ".trellis").exists()

    run(monkeypatch, tmp_path, "add-card", "cap1", "--title", "Uno")
    run(monkeypatch, tmp_path, "assign", "cap1")
    draft = tmp_path / "borrador.md"
    draft.write_text("contenido\n", encoding="utf-8")
    run(monkeypatch, tmp_path, "propose", "cap1", "--file", str(draft))
    assert (tmp_path / "propuestas" / "cap1.md").exists()

    run(monkeypatch, tmp_path, "approve", "cap1")
    assert not (tmp_path / "propuestas" / "cap1.md").exists()


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
    assert not (tmp_path / "propuestas" / "cap1.md").exists()


def test_validate_command_on_example_canvas(tmp_path, monkeypatch):
    repo_root = Path(__file__).resolve().parents[1]
    shutil.copy(repo_root / "examples" / "example.canvas", tmp_path / "project.canvas")
    assert run(monkeypatch, tmp_path, "validate") == 0


def test_init_installs_agent_templates(tmp_path, monkeypatch):
    assert run(monkeypatch, tmp_path, "init") == 0

    claude_md = tmp_path / "CLAUDE.md"
    agents_md = tmp_path / "AGENTS.md"
    assert claude_md.read_text(encoding="utf-8").strip() == "@AGENTS.md"
    assert "--authorized" in agents_md.read_text(encoding="utf-8")


def test_init_does_not_overwrite_existing_agent_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "AGENTS.md").write_text("contenido del usuario, no tocar\n", encoding="utf-8")

    assert run(monkeypatch, tmp_path, "init") == 0

    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == "contenido del usuario, no tocar\n"
    assert (tmp_path / "CLAUDE.md").exists()


def test_init_adds_legend_text_node(tmp_path, monkeypatch):
    assert run(monkeypatch, tmp_path, "init") == 0

    data = canvas.load(tmp_path)
    legend = [n for n in data["nodes"] if n["id"] == canvas.LEGEND_ID]
    assert len(legend) == 1
    assert legend[0]["type"] == "text"
    assert "Backlog" in legend[0]["text"]
    # el legend no cuenta como tarjeta para nada de la logica de estado
    assert canvas.cards(data) == []


def test_add_card_description_goes_to_frontmatter_and_body(tmp_path, monkeypatch):
    run(monkeypatch, tmp_path, "init")
    assert run(
        monkeypatch, tmp_path, "add-card", "cap1", "--title", "Intro",
        "--description", "contexto y estado del arte",
    ) == 0

    meta, body = frontmatter.read(tmp_path / "content" / "cap1.md")
    assert meta["description"] == "contexto y estado del arte"
    assert "contexto y estado del arte" in body


def test_link_adds_dependency_to_existing_card(tmp_path, monkeypatch):
    run(monkeypatch, tmp_path, "init")
    run(monkeypatch, tmp_path, "add-card", "cap1", "--title", "Intro")
    run(monkeypatch, tmp_path, "add-card", "cap2", "--title", "Cap 2")
    assert canvas.find_card(canvas.load(tmp_path), "cap2")["color"] == canvas.BACKLOG

    assert run(monkeypatch, tmp_path, "link", "cap2", "--depends-on", "cap1", "--authorized") == 0

    data = canvas.load(tmp_path)
    assert canvas.find_card(data, "cap2")["color"] == canvas.BLOQUEADA
    assert any(e["fromNode"] == "cap1" and e["toNode"] == "cap2" for e in data["edges"])


def test_link_without_authorized_fails(tmp_path, monkeypatch):
    run(monkeypatch, tmp_path, "init")
    run(monkeypatch, tmp_path, "add-card", "cap1", "--title", "Intro")
    run(monkeypatch, tmp_path, "add-card", "cap2", "--title", "Cap 2")

    result = run(monkeypatch, tmp_path, "link", "cap2", "--depends-on", "cap1")
    assert result != 0
    assert canvas.load(tmp_path)["edges"] == []


def test_link_rejects_cycle(tmp_path, monkeypatch):
    run(monkeypatch, tmp_path, "init")
    run(monkeypatch, tmp_path, "add-card", "cap1", "--title", "Uno")
    run(monkeypatch, tmp_path, "add-card", "cap2", "--title", "Dos", "--depends-on", "cap1", "--authorized")

    result = run(monkeypatch, tmp_path, "link", "cap1", "--depends-on", "cap2", "--authorized")
    assert result != 0
    edges = canvas.load(tmp_path)["edges"]
    assert not any(e["fromNode"] == "cap2" and e["toNode"] == "cap1" for e in edges)


def test_add_card_size_reflects_description_length(tmp_path, monkeypatch):
    run(monkeypatch, tmp_path, "init")
    run(monkeypatch, tmp_path, "add-card", "corto", "--title", "Corto", "--description", "poco texto")
    run(monkeypatch, tmp_path, "add-card", "largo", "--title", "Largo", "--description", "x" * 200)

    data = canvas.load(tmp_path)
    assert canvas.find_card(data, "largo")["height"] > canvas.find_card(data, "corto")["height"]


def test_link_triggers_relayout(tmp_path, monkeypatch):
    run(monkeypatch, tmp_path, "init")
    run(monkeypatch, tmp_path, "add-card", "cap1", "--title", "Uno")
    run(monkeypatch, tmp_path, "add-card", "cap2", "--title", "Dos")
    before_x = canvas.find_card(canvas.load(tmp_path), "cap2")["x"]

    run(monkeypatch, tmp_path, "link", "cap2", "--depends-on", "cap1", "--authorized")

    after_x = canvas.find_card(canvas.load(tmp_path), "cap2")["x"]
    assert after_x != before_x


def test_describe_updates_frontmatter_only(tmp_path, monkeypatch):
    run(monkeypatch, tmp_path, "init")
    run(monkeypatch, tmp_path, "add-card", "cap1", "--title", "Intro")
    _, before_body = frontmatter.read(tmp_path / "content" / "cap1.md")

    assert run(monkeypatch, tmp_path, "describe", "cap1", "--text", "nueva descripcion") == 0

    meta, body = frontmatter.read(tmp_path / "content" / "cap1.md")
    assert meta["description"] == "nueva descripcion"
    assert body == before_body
