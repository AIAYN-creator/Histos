"""plans.levels and plans.warnings: the plan by levels, and the three warnings (cod-plan-checks)."""
import pytest
from conftest import run

from histos import canvas, plans

REAL_PLAN = """\
summary: The v3.1 plan, twelve cards.
cards:
  - {id: dec-eventos, title: T, done_when: The event log format is decided and written down.}
  - {id: dec-entrevista, title: T, done_when: The interview format is decided and written down.}
  - {id: dec-plan, title: T, done_when: The plan format is decided and written down.}
  - {id: cod-eventos, title: T, done_when: Every command records its events with tests., depends_on: [dec-eventos]}
  - {id: cod-guia-evals, title: T, done_when: The eval guide is generated for every project., depends_on: [dec-entrevista]}
  - {id: cod-plan, title: T, done_when: The plan is proposed and approved from the CLI., depends_on: [dec-plan]}
  - {id: pru-eventos, title: T, done_when: The event log tests pass on every platform., depends_on: [cod-eventos]}
  - {id: cod-plan-checks, title: T, done_when: Los tests de cod-plan-checks pasan en CI., depends_on: [cod-plan]}
  - {id: cod-decisiones, title: T, done_when: Decisions are recorded with their reasons and dates., depends_on: [cod-plan, cod-guia-evals]}
  - {id: doc-entrevista, title: T, done_when: The interview guide is written and linked from the README., depends_on: [cod-plan, cod-guia-evals]}
  - {id: pru-plan, title: T, done_when: The plan tests pass on a clean vault., depends_on: [cod-plan-checks, cod-decisiones]}
  - {id: rel-v31, title: T, done_when: The release is published with its notes., depends_on: [pru-eventos, pru-plan, doc-entrevista]}
"""


def plan_of(*cards):
    """cards: (id, depends_on, done_when) tuples -> Plan."""
    return plans.Plan(summary="s", cards=[
        plans.PlanCard(id=i, title=i, done_when=dw, depends_on=list(deps)) for i, deps, dw in cards])


OK = "This sentence is long enough"


@pytest.fixture
def data(initialized_vault, monkeypatch):
    run(monkeypatch, initialized_vault, "add-card", "old", "--title", "Old")
    return canvas.load(initialized_vault)


# --- warnings -----------------------------------------------------------------------------------

def test_a_loose_card_warns(data):
    plan = plan_of(("a", [], OK), ("b", ["a"], OK), ("lost", [], OK))
    assert plans.warnings(plan, data) == [
        "warning: lost is loose: it depends on nothing and nothing depends on it"]


def test_a_single_card_plan_is_not_loose(data):
    assert plans.warnings(plan_of(("a", [], OK)), data) == []


def test_a_card_depending_on_the_board_is_not_loose(data):
    assert plans.warnings(plan_of(("a", ["old"], OK), ("b", [], OK), ("c", ["b"], OK)), data) == []


def test_too_many_dependencies_warns(data):
    deps = [f"d{i}" for i in range(6)]
    plan = plan_of(*[(d, [], OK) for d in deps], ("big", deps, OK))
    assert plans.warnings(plan, data) == ["warning: big depends on 6 cards"]


def test_five_dependencies_do_not_warn(data):
    deps = [f"d{i}" for i in range(5)]
    assert plans.warnings(plan_of(*[(d, [], OK) for d in deps], ("big", deps, OK)), data) == []


@pytest.mark.parametrize("done_when", ["Funciona.", "Está hecho", "ESTA HECHO.", "It works", "Done", "tests pass fine"])
def test_a_vague_done_when_warns(data, done_when):
    plan = plan_of(("a", [], OK), ("b", ["a"], done_when))
    assert plans.warnings(plan, data) == [f'warning: b: done_when "{done_when}" can\'t be checked']


def test_a_real_sentence_does_not_warn(data):
    plan = plan_of(("a", [], OK), ("b", ["a"], "Los tests de cod-plan pasan en CI"))
    assert plans.warnings(plan, data) == []


def test_a_clean_plan_has_no_warnings(data):
    assert plans.warnings(plan_of(("a", [], OK), ("b", ["a"], OK), ("c", ["a", "old"], OK)), data) == []


# --- levels -------------------------------------------------------------------------------------

def test_levels_in_plan_order(data):
    plan = plan_of(("c", ["b"], OK), ("b", ["a"], OK), ("a", [], OK), ("x", [], OK))
    assert plans.levels(plan, data) == [["a", "x"], ["b"], ["c"]]


def test_a_dependency_on_an_approved_board_card_is_level_one(data):
    canvas.find_card(data, "old")["color"] = canvas.APPROVED
    assert plans.levels(plan_of(("a", ["old"], OK), ("b", ["a"], OK)), data) == [["a"], ["b"]]


def test_a_dependency_on_an_unapproved_board_card_waits(data):
    plan = plan_of(("a", ["old"], OK), ("b", ["a"], OK))
    assert plans.levels(plan, data) == [["a (waits for old)"], ["b"]]


def test_the_real_v31_plan(data):
    plan = plans.parse(REAL_PLAN)
    assert plans.levels(plan, data) == [
        ["dec-eventos", "dec-entrevista", "dec-plan"],
        ["cod-eventos", "cod-guia-evals", "cod-plan"],
        ["pru-eventos", "cod-plan-checks", "cod-decisiones", "doc-entrevista"],
        ["pru-plan"],
        ["rel-v31"],
    ]
    assert plans.warnings(plan, data) == []
