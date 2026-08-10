import json
import shutil
from pathlib import Path

from histos import canvas, frontmatter
from histos.cli import build_parser


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


def test_add_card_rejects_path_traversal_id(tmp_path, monkeypatch):
    run(monkeypatch, tmp_path, "init")
    result = run(monkeypatch, tmp_path, "add-card", "../../evil", "--title", "x")
    assert result != 0
    assert canvas.find_card(canvas.load(tmp_path), "../../evil") is None
    assert not (tmp_path.parent / "evil.md").exists()


def test_add_card_rejects_backslash_id(tmp_path, monkeypatch):
    run(monkeypatch, tmp_path, "init")
    result = run(monkeypatch, tmp_path, "add-card", "..\\..\\evil", "--title", "x")
    assert result != 0


def test_propose_uses_visible_propuestas_folder(tmp_path, monkeypatch):
    run(monkeypatch, tmp_path, "init")
    assert (tmp_path / "propuestas").is_dir()

    run(monkeypatch, tmp_path, "add-card", "cap1", "--title", "Uno")
    run(monkeypatch, tmp_path, "assign", "cap1")
    draft = tmp_path / "borrador.md"
    draft.write_text("contenido\n", encoding="utf-8")
    run(monkeypatch, tmp_path, "propose", "cap1", "--file", str(draft))
    assert (tmp_path / "propuestas" / "cap1.md").exists()

    run(monkeypatch, tmp_path, "approve", "cap1")
    assert not (tmp_path / "propuestas" / "cap1.md").exists()


def test_approve_archives_proposal_instead_of_deleting(tmp_path, monkeypatch):
    run(monkeypatch, tmp_path, "init")
    run(monkeypatch, tmp_path, "add-card", "cap1", "--title", "Uno")
    run(monkeypatch, tmp_path, "assign", "cap1")
    draft = tmp_path / "borrador.md"
    draft.write_text("contenido aprobado\n", encoding="utf-8")
    run(monkeypatch, tmp_path, "propose", "cap1", "--file", str(draft))

    run(monkeypatch, tmp_path, "approve", "cap1")

    assert not (tmp_path / "propuestas" / "cap1.md").exists()
    archived = tmp_path / "aprobados" / "cap1.md"
    assert archived.exists()
    assert "contenido aprobado" in archived.read_text(encoding="utf-8")


def test_reject_does_not_archive(tmp_path, monkeypatch):
    run(monkeypatch, tmp_path, "init")
    run(monkeypatch, tmp_path, "add-card", "cap1", "--title", "Uno")
    run(monkeypatch, tmp_path, "assign", "cap1")
    draft = tmp_path / "borrador.md"
    draft.write_text("contenido rechazado\n", encoding="utf-8")
    run(monkeypatch, tmp_path, "propose", "cap1", "--file", str(draft))

    run(monkeypatch, tmp_path, "reject", "cap1")

    assert not (tmp_path / "propuestas" / "cap1.md").exists()
    assert not (tmp_path / "aprobados" / "cap1.md").exists()


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

    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert "Write(content/**)" in settings["permissions"]["deny"]
    assert "Edit(content/**)" in settings["permissions"]["deny"]


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


def test_link_blocks_already_assigned_card(tmp_path, monkeypatch, capsys):
    run(monkeypatch, tmp_path, "init")
    run(monkeypatch, tmp_path, "add-card", "fuentes-apis", "--title", "Fuentes APIs")
    run(monkeypatch, tmp_path, "add-card", "diseno-expansion", "--title", "Diseno expansion")

    run(monkeypatch, tmp_path, "assign", "diseno-expansion")
    assert canvas.find_card(canvas.load(tmp_path), "diseno-expansion")["color"] == canvas.EN_PROGRESO

    assert run(
        monkeypatch, tmp_path, "link", "diseno-expansion",
        "--depends-on", "fuentes-apis", "--authorized",
    ) == 0

    # el cambio de estado se persiste ya en el propio 'link'...
    data = canvas.load(tmp_path)
    assert canvas.find_card(data, "diseno-expansion")["color"] == canvas.BLOQUEADA

    # ...y 'status' lo confirma (era el sintoma original del bug: seguia listada
    # bajo "En progreso" en el siguiente 'histos status').
    capsys.readouterr()
    run(monkeypatch, tmp_path, "status")
    output = capsys.readouterr().out
    assert "Bloqueada (1)" in output
    assert "En progreso (0)" in output


def test_link_blocked_card_returns_to_backlog_once_dependency_approved(tmp_path, monkeypatch):
    run(monkeypatch, tmp_path, "init")
    run(monkeypatch, tmp_path, "add-card", "fuentes-apis", "--title", "Fuentes APIs")
    run(monkeypatch, tmp_path, "add-card", "diseno-expansion", "--title", "Diseno expansion")
    run(monkeypatch, tmp_path, "assign", "diseno-expansion")
    run(
        monkeypatch, tmp_path, "link", "diseno-expansion",
        "--depends-on", "fuentes-apis", "--authorized",
    )
    assert canvas.find_card(canvas.load(tmp_path), "diseno-expansion")["color"] == canvas.BLOQUEADA

    run(monkeypatch, tmp_path, "assign", "fuentes-apis")
    draft = tmp_path / "borrador.md"
    draft.write_text("contenido\n", encoding="utf-8")
    run(monkeypatch, tmp_path, "propose", "fuentes-apis", "--file", str(draft))
    assert run(monkeypatch, tmp_path, "approve", "fuentes-apis") == 0

    data = canvas.load(tmp_path)
    assert canvas.find_card(data, "fuentes-apis")["color"] == canvas.APROBADA
    # vuelve a un estado trabajable (Backlog) solo, sin que nadie mueva el color a mano
    assert canvas.find_card(data, "diseno-expansion")["color"] == canvas.BACKLOG


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


def test_describe_requires_text_or_sources(tmp_path, monkeypatch):
    run(monkeypatch, tmp_path, "init")
    run(monkeypatch, tmp_path, "add-card", "cap1", "--title", "Intro")

    assert run(monkeypatch, tmp_path, "describe", "cap1") != 0


def test_describe_sources_rejects_missing_file(tmp_path, monkeypatch):
    run(monkeypatch, tmp_path, "init")
    run(monkeypatch, tmp_path, "add-card", "cap1", "--title", "Intro")

    result = run(monkeypatch, tmp_path, "describe", "cap1", "--sources", str(tmp_path / "no-existe.txt"))
    assert result != 0


def test_describe_sources_updates_frontmatter(tmp_path, monkeypatch):
    run(monkeypatch, tmp_path, "init")
    run(monkeypatch, tmp_path, "add-card", "cap1", "--title", "Intro")
    source_file = tmp_path / "notas.txt"
    source_file.write_text("apuntes externos\n", encoding="utf-8")

    assert run(monkeypatch, tmp_path, "describe", "cap1", "--sources", str(source_file)) == 0

    meta, _ = frontmatter.read(tmp_path / "content" / "cap1.md")
    assert meta["sources"] == [str(source_file)]


def test_context_includes_dependency_content_and_sources(tmp_path, monkeypatch, capsys):
    run(monkeypatch, tmp_path, "init")
    run(monkeypatch, tmp_path, "add-card", "biblio", "--title", "Biblioteca")
    run(monkeypatch, tmp_path, "add-card", "intro", "--title", "Intro", "--depends-on", "biblio", "--authorized")

    source_file = tmp_path / "referencias.txt"
    source_file.write_text("Autor (2024) -- paper relevante\n", encoding="utf-8")
    run(monkeypatch, tmp_path, "describe", "biblio", "--sources", str(source_file))

    run(monkeypatch, tmp_path, "assign", "biblio")
    draft = tmp_path / "borrador.md"
    draft.write_text("Lista final de referencias.\n", encoding="utf-8")
    run(monkeypatch, tmp_path, "propose", "biblio", "--file", str(draft))
    run(monkeypatch, tmp_path, "approve", "biblio")

    capsys.readouterr()  # descarta el output de los comandos anteriores
    assert run(monkeypatch, tmp_path, "context", "intro") == 0
    output = capsys.readouterr().out

    assert "Lista final de referencias." in output
    assert "Autor (2024) -- paper relevante" in output


def test_context_includes_project_md(tmp_path, monkeypatch, capsys):
    run(monkeypatch, tmp_path, "init")
    run(monkeypatch, tmp_path, "add-card", "cap1", "--title", "Uno")
    (tmp_path / "PROJECT.md").write_text("Brief: un TFG sobre pruebas.\n", encoding="utf-8")

    capsys.readouterr()
    assert run(monkeypatch, tmp_path, "context", "cap1") == 0
    output = capsys.readouterr().out

    assert "Brief: un TFG sobre pruebas." in output
