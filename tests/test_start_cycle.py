"""The whole start of a project, in the order a real one uses it (pru-plan, part 1).

Each piece has its own tests; this one drives the real CLI on a fresh `histos init` vault (guide
evals included), alternating what the agent and the human would do, because the failures that
matter are in the seams: a rejected plan whose decision doesn't reach the next `context`, a
pending PROJECT.md that loses a decision when it's approved...

    1. a bad brief is refused by the evals          5. a PROJECT.md change pending while a plan is rejected
    2. a complete brief, rejected with feedback     6. plan approve, and `context` of a card
    3. the corrected brief carries the decision     7. the event log tells it all, in order
    4. a first plan, with warnings, rejected
"""
from datetime import datetime

from conftest import run

from histos import canvas, events, frontmatter, guides, operations, plans

TODAY = datetime.now().strftime("%Y-%m-%d")
SCOPE_DECISION = "El alcance no incluye la app"
DOCS_DECISION = "Une las dos tarjetas de docs"
TESTS_DECISION = "Falta una tarjeta de pruebas"

BRIEF = """# Gestor de referencias

## Objetivo
{goal}

## Entregable
Una herramienta de línea de comandos.

## Para quién
Quien escribe una tesis y cita a mano.

## Qué no es
No es una aplicación de escritorio ni un servicio en la nube.

## Terminado cuando
Una bibliografía completa se genera desde un solo archivo.

## Restricciones
Funciona sin conexión a internet.

## Lo que he entendido
{understood}

## Lo que cambiaría y por qué
{change}

## Decisiones
{decisions}"""

UNDERSTOOD = " ".join(["Entiendo que se trata de una herramienta pequeña que ordena las citas."] * 8)
CHANGE = " ".join(["Empezaría por el formato de entrada antes de tocar nada más."] * 4)


def brief(goal="Generar bibliografías sin errores.", understood=UNDERSTOOD, change=CHANGE, decisions_text=""):
    return BRIEF.format(goal=goal, understood=understood, change=change, decisions=decisions_text)


def plan_text(summary, cards):
    lines = [f"summary: {summary}", "cards:"]
    for card_id, deps, done_when in cards:
        lines.append(f"  - id: {card_id}")
        lines.append(f"    title: Tarjeta {card_id}")
        lines.append(f"    done_when: {done_when}")
        if deps:
            lines.append(f"    depends_on: [{', '.join(deps)}]")
    return "\n".join(lines) + "\n"


FORMAT_OK = "El formato de entrada queda decidido y escrito."
PARSER_OK = "El lector de entrada pasa todos sus tests."
OUTPUT_OK = "La salida coincide con el ejemplo del brief."
RELEASE_OK = "La versión se publica con sus notas de cambios."
DOCS_OK = "El README explica cómo usar la herramienta."

# First plan: docs-readme is loose and parser's done_when can't be checked.
FIRST_PLAN = plan_text("PRIMER-PLAN-MARCA se corta por formato, lector y salida.", [
    ("dec-formato", [], FORMAT_OK),
    ("cod-lector", ["dec-formato"], "Funciona."),
    ("cod-salida", ["dec-formato"], OUTPUT_OK),
    ("rel-v1", ["cod-lector", "cod-salida"], RELEASE_OK),
    ("docs-readme", [], DOCS_OK),
])

# The corrected one: the README hangs off the release, so nothing is loose.
GOOD_PLAN = plan_text("Se corta por formato, lector y salida; la documentación cierra.", [
    ("dec-formato", [], FORMAT_OK),
    ("cod-lector", ["dec-formato"], PARSER_OK),
    ("cod-salida", ["dec-formato"], OUTPUT_OK),
    ("docs-readme", ["cod-lector", "cod-salida"], DOCS_OK),
    ("rel-v1", ["docs-readme"], RELEASE_OK),
])

# A third, intermediate plan that the human turns down while a PROJECT.md change is pending.
THIRD_PLAN = plan_text("Un intento intermedio, sin tarjeta de pruebas.", [
    ("dec-formato", [], FORMAT_OK),
    ("rel-v1", ["dec-formato"], RELEASE_OK),
])


def draft(vault, name, text):
    path = vault.parent / f"{vault.name}-{name}"
    path.write_text(text, encoding="utf-8")
    return str(path)


def names(vault):
    return [e["event"] for e in events.read(vault)]


def colors(vault):
    return {c["id"]: c["color"] for c in canvas.cards(canvas.load(vault))}


def project_md(vault):
    return (vault / "PROJECT.md").read_text(encoding="utf-8")


def test_the_whole_start_cycle(initialized_vault, monkeypatch, capsys):
    vault = initialized_vault
    assert (vault / "evals" / "guides.yaml").exists(), "setup: init must leave the guide evals in place"

    def cli(*argv):
        return run(monkeypatch, vault, *argv)

    def out_of(*argv):
        capsys.readouterr()
        code = cli(*argv)
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    # ---- 1. a bad brief: no "Qué no es", one line of understanding ----------------------------
    bad = brief(understood="Lo entiendo.").replace("## Qué no es\nNo es una aplicación de escritorio "
                                                   "ni un servicio en la nube.\n\n", "")
    code, out, _ = out_of("guide", "propose", "PROJECT.md", "--file", draft(vault, "bad.md", bad))
    assert code == 1, "step 1: the evals must refuse the bad brief with exit code 1"
    assert out.count("FAIL") == 2, f"step 1: expected exactly two FAIL lines, got:\n{out}"
    assert "FAIL  project_structure" in out and "FAIL  understanding_written" in out, f"step 1: {out}"
    assert guides.pending(vault) == [], "step 1: a refused brief must not be left pending"
    assert not (vault / "PROJECT.md").exists(), "step 1: nothing is written to PROJECT.md"
    assert "guide_proposed" not in names(vault), "step 1: a refused brief isn't an event"

    # ---- 2. a complete brief, rejected by the human --------------------------------------------
    first = brief()
    code, out, _ = out_of("guide", "propose", "PROJECT.md", "--file", draft(vault, "brief1.md", first))
    assert code == 0 and guides.pending(vault) == ["PROJECT.md"], f"step 2: the complete brief must pass:\n{out}"
    assert out.count("FAIL") == 0, f"step 2: {out}"
    assert cli("add-card", "arranque", "--title", "Arranque") == 0, "step 2: a card to read context from"
    assert cli("guide", "reject", "PROJECT.md", "--feedback", SCOPE_DECISION) == 0, "step 2: reject"
    assert guides.pending(vault) == [], "step 2: the rejected brief is gone"
    assert not (vault / "PROJECT.md").exists(), "step 2: there is still no PROJECT.md"
    fallback = (vault / ".histos" / "decisions.md").read_text(encoding="utf-8")
    assert SCOPE_DECISION in fallback, f"step 2: the decision must go to .histos/decisions.md:\n{fallback}"
    scope_line = next(line for line in fallback.splitlines() if SCOPE_DECISION in line)
    assert scope_line.startswith(f"- {TODAY} · "), f"step 2: a dated line, got {scope_line!r}"
    code, out, _ = out_of("context", "arranque")
    assert code == 0 and scope_line in out, f"step 2: context must show the decision:\n{out}"
    assert "Decisions not yet in PROJECT.md" in out.split(scope_line)[0], \
        f"step 2: under the heading for decisions outside PROJECT.md:\n{out}"

    # ---- 3. the corrected brief carries the decision into its Decisiones section ---------------
    second = brief(goal="Generar bibliografías sin errores, solo por línea de comandos.",
                   decisions_text=scope_line + "\n")
    assert cli("guide", "propose", "PROJECT.md", "--file", draft(vault, "brief2.md", second)) == 0, "step 3: propose"
    assert cli("guide", "approve", "PROJECT.md") == 0, "step 3: approve"
    assert project_md(vault) == second, "step 3: PROJECT.md is exactly the approved brief"
    assert (vault / ".histos" / "decisions.md").exists(), "step 3: Histos never deletes its own file"
    assert not (vault / "approved" / "guides").exists(), "step 3: there was no previous PROJECT.md to keep"
    code, out, _ = out_of("context", "arranque")
    assert out.count(SCOPE_DECISION) == 1, f"step 3: the decision must appear once, not twice:\n{out}"
    assert "Decisions not yet in PROJECT.md" not in out, f"step 3: nothing is left outside PROJECT.md:\n{out}"

    # ---- 4. the first plan: levels, two warnings, rejected --------------------------------------
    assert cli("plan", "propose", "--file", draft(vault, "plan1.yaml", FIRST_PLAN)) == 0, "step 4: propose"
    code, out, _ = out_of("plan", "diff")
    assert code == 0, "step 4: diff"
    assert "Levels:\n  1  dec-formato, docs-readme\n  2  cod-lector, cod-salida\n  3  rel-v1\n" in out, \
        f"step 4: the levels:\n{out}"
    assert "warning: docs-readme is loose" in out, f"step 4: the loose card must be flagged:\n{out}"
    assert 'warning: cod-lector: done_when "Funciona." can\'t be checked' in out, f"step 4:\n{out}"
    assert out.count("warning:") == 2 and "No warnings." not in out, f"step 4: exactly two warnings:\n{out}"
    before_reject = project_md(vault)
    assert cli("plan", "reject", "--feedback", DOCS_DECISION) == 0, "step 4: reject"
    assert plans.pending(vault) is None, "step 4: the rejected plan is gone"
    after_reject = project_md(vault)
    plan_line = f"- {TODAY} · plan rejected: {DOCS_DECISION}"
    assert after_reject == before_reject.rstrip("\n") + "\n" + plan_line + "\n", \
        f"step 4: one dated line at the end of Decisiones, nothing else changed:\n{after_reject}"
    assert after_reject.index(scope_line) < after_reject.index(plan_line), "step 4: after the earlier decision"
    [kept] = (vault / "approved" / "guides").iterdir()
    assert kept.read_text(encoding="utf-8") == before_reject, "step 4: the previous version is kept"
    assert plans.last_feedback(vault) == DOCS_DECISION, "step 4: the agent can read the feedback"

    # ---- 5. a PROJECT.md change pending while a plan is rejected --------------------------------
    assert cli("plan", "propose", "--file", draft(vault, "plan2.yaml", GOOD_PLAN)) == 0, "step 5: second plan"
    _, out, _ = out_of("plan", "diff")
    assert out.rstrip().endswith("No warnings."), f"step 5: the corrected plan has no warnings:\n{out}"
    changed = project_md(vault).replace("sin errores, solo", "sin errores y sin red, solo")
    assert changed != project_md(vault), "step 5: setup: the change must differ"
    assert cli("guide", "propose", "PROJECT.md", "--file", draft(vault, "brief3.md", changed)) == 0, "step 5: guide"
    assert cli("plan", "propose", "--file", draft(vault, "plan3.yaml", THIRD_PLAN)) == 0, "step 5: third plan"
    assert cli("plan", "reject", "--feedback", TESTS_DECISION) == 0, "step 5: reject the third plan"
    tests_line = f"- {TODAY} · plan rejected: {TESTS_DECISION}"
    assert tests_line in project_md(vault), "step 5: the decision reached the approved PROJECT.md"
    pending_text = guides.proposal_path(vault, "PROJECT.md").read_text(encoding="utf-8")
    assert tests_line in pending_text and plan_line in pending_text, \
        "step 5: and the pending PROJECT.md, which already had the older decision"
    assert "sin red" in pending_text, "step 5: without touching the agent's change"
    assert cli("guide", "approve", "PROJECT.md") == 0, "step 5: approve the pending PROJECT.md"
    final_project = project_md(vault)
    assert "sin errores y sin red" in final_project, "step 5: the agent's change is in"
    for line in (scope_line, plan_line, tests_line):
        assert line in final_project, f"step 5: approving the pending one lost a decision: {line}"
    assert final_project.index(plan_line) < final_project.index(tests_line), "step 5: decisions keep their order"

    # ---- 6. plan approve, and what a card sees --------------------------------------------------
    assert cli("plan", "propose", "--file", draft(vault, "plan2b.yaml", GOOD_PLAN)) == 0, "step 6: re-propose"
    assert cli("plan", "approve") == 0, "step 6: approve"
    assert plans.pending(vault) is None, "step 6: nothing pending afterwards"
    assert colors(vault) == {
        "arranque": canvas.BACKLOG, "dec-formato": canvas.BACKLOG, "cod-lector": canvas.BLOCKED,
        "cod-salida": canvas.BLOCKED, "docs-readme": canvas.BLOCKED, "rel-v1": canvas.BLOCKED,
    }, "step 6: only the card without dependencies can start"
    edges = {(e["fromNode"], e["toNode"]) for e in canvas.load(vault)["edges"]}
    assert edges == {("dec-formato", "cod-lector"), ("dec-formato", "cod-salida"),
                     ("cod-lector", "docs-readme"), ("cod-salida", "docs-readme"),
                     ("docs-readme", "rel-v1")}, f"step 6: the plan's dependencies: {edges}"
    meta, _ = frontmatter.read(vault / "content" / "cod-lector.md")
    assert meta["done_when"] == PARSER_OK, "step 6: done_when is in the card's frontmatter"
    code, out, _ = out_of("context", "docs-readme")
    assert code == 0, "step 6: context"
    assert f"Done when: {DOCS_OK}" in out, f"step 6: the card's own done_when:\n{out}"
    assert f"Done when: {PARSER_OK}" in out and f"Done when: {OUTPUT_OK}" in out, \
        f"step 6: and its dependencies':\n{out}"
    assert "## Project brief (PROJECT.md)" in out, "step 6: PROJECT.md is in the context"
    for line in (scope_line, plan_line, tests_line):
        assert out.count(line) == 1, f"step 6: every decision exactly once in context: {line}\n{out}"
    assert "Decisions not yet in PROJECT.md" not in out, "step 6: nothing outside PROJECT.md"
    assert "PRIMER-PLAN-MARCA" not in out and "Funciona." not in out, "step 6: no old plans in context"
    [archived] = (vault / "approved" / "plans").iterdir()
    assert archived.read_text(encoding="utf-8") == GOOD_PLAN, "step 6: the approved plan is archived"

    # ---- 7. the event log tells the whole story, in order ---------------------------------------
    log = events.read(vault)
    story = [e["event"] for e in log]
    plan_cards = ["card_created"] * 5
    assert story[0] == "vault_created", f"step 7: it starts with the vault: {story}"
    assert [n for n in story if n != "config_added"] == [
        "vault_created",
        "guide_proposed", "card_created",                   # step 2 (the card is "arranque")
        "guide_rejected",
        "guide_proposed", "guide_approved",                 # step 3
        "plan_proposed", "plan_rejected",                   # step 4
        "plan_proposed", "guide_proposed", "plan_proposed", "plan_rejected",   # step 5
        "guide_approved",
        "plan_proposed", *plan_cards, "plan_approved",      # step 6
    ], f"step 7: the story, in order: {story}"
    assert not {"proposal_refused", "state_recomputed"} & set(story), f"step 7: nothing outside histos: {story}"

    recorded = [(e["event"], e["data"]["recorded_in"]) for e in log if "recorded_in" in e.get("data", {})]
    assert recorded == [
        ("guide_rejected", ".histos/decisions.md"),
        ("plan_rejected", "PROJECT.md"),
        ("plan_rejected", "PROJECT.md+pending"),
    ], f"step 7: where each decision went: {recorded}"
    rejected = [e for e in log if e["event"] in ("guide_rejected", "plan_rejected")]
    assert [e["data"]["feedback"] for e in rejected] == [SCOPE_DECISION, DOCS_DECISION, TESTS_DECISION], \
        "step 7: the feedback is in the log"
    created = [e["card"] for e in log if e["event"] == "card_created"]
    assert created[0] == "arranque" and sorted(created[1:]) == sorted(
        ["dec-formato", "cod-lector", "cod-salida", "docs-readme", "rel-v1"]), f"step 7: card_created: {created}"
    assert [e["data"]["cards"] for e in log if e["event"] == "plan_proposed"] == [5, 5, 2, 5], "step 7: plan sizes"
    assert log[-1]["data"] == {"cards": 5}, "step 7: it ends with the plan approved"
    assert {e["actor"] for e in log if e["event"] in ("guide_proposed", "plan_proposed")} == {"agent"}, "step 7"
    assert {e["actor"] for e in log if e["event"] in (
        "guide_approved", "guide_rejected", "plan_approved", "plan_rejected")} == {"human"}, "step 7"

    board: dict[str, str] = {}  # replaying every transition from an empty board
    for e in log:
        for t in e.get("transitions", []):
            assert board.get(t["card"]) == t["from"], f"step 7: transition out of sync with the replay: {t}"
            if t["to"] is None:
                board.pop(t["card"])
            else:
                board[t["card"]] = t["to"]
    assert board == {card: operations.STATE_NAMES[color] for card, color in colors(vault).items()}, \
        f"step 7: replaying the transitions must rebuild the final canvas colors: {board}"

    assert cli("validate") == 0, "step 7: the vault validates"
