"""histos plan: propose / diff / approve / reject (cod-plan)."""
import threading

import pytest
from conftest import run

from histos import canvas, events, frontmatter, plans
from histos.errors import HistosError

PLAN = """\
summary: Two branches that meet at the release.
cards:
  - id: dec-a
    title: Decide A
    done_when: A is decided and written down.
  - id: cod-a
    title: Build A
    description: The code for A.
    done_when: A's tests pass.
    depends_on: [dec-a]
  - id: rel
    title: Release
    done_when: The release is published.
    depends_on: [cod-a, old]
"""


@pytest.fixture
def vault(initialized_vault, monkeypatch):
    run(monkeypatch, initialized_vault, "add-card", "old", "--title", "Old")
    return initialized_vault


def write(vault, text, name="plan-draft.yaml"):
    path = vault.parent / f"{vault.name}-{name}"
    path.write_text(text, encoding="utf-8")
    return str(path)


def colors(vault):
    return {c["id"]: c["color"] for c in canvas.cards(canvas.load(vault))}


def test_the_full_cycle(vault, monkeypatch, capsys):
    assert run(monkeypatch, vault, "plan", "propose", "--file", write(vault, PLAN)) == 0
    assert "plan with 3 cards pending review" in capsys.readouterr().out
    assert set(colors(vault)) == {"old"}  # nothing created yet

    run(monkeypatch, vault, "status")
    assert "Plan pending review (3 cards)" in capsys.readouterr().out

    run(monkeypatch, vault, "plan", "diff")
    out = capsys.readouterr().out
    assert out.startswith("Plan: 3 cards\n\nTwo branches that meet at the release.")
    assert "cod-a  Build A   dec-a       A's tests pass." in out

    assert run(monkeypatch, vault, "plan", "approve") == 0
    out = capsys.readouterr().out
    assert "card 'dec-a' created (Backlog)" in out
    assert "plan approved: 3 cards created (1 Backlog, 2 Blocked)" in out
    assert colors(vault) == {"old": canvas.BACKLOG, "dec-a": canvas.BACKLOG,
                             "cod-a": canvas.BLOCKED, "rel": canvas.BLOCKED}
    edges = {(e["fromNode"], e["toNode"]) for e in canvas.load(vault)["edges"]}
    assert {("dec-a", "cod-a"), ("cod-a", "rel"), ("old", "rel")} <= edges

    meta, body = frontmatter.read(vault / "content" / "cod-a.md")
    assert meta["done_when"] == "A's tests pass." and meta["description"] == "The code for A."
    assert "**Done when:** A's tests pass." in body
    assert not plans.plan_path(vault).exists()
    [archived] = (vault / "approved" / "plans").iterdir()
    assert archived.read_text(encoding="utf-8") == PLAN
    assert run(monkeypatch, vault, "validate") == 0


def test_approving_records_events_as_the_human(vault, monkeypatch):
    run(monkeypatch, vault, "plan", "propose", "--file", write(vault, PLAN))
    run(monkeypatch, vault, "plan", "approve")
    log = events.read(vault)
    assert (log[-5]["event"], log[-5]["actor"], log[-5]["data"]) == ("plan_proposed", "agent", {"cards": 3})
    assert [e["event"] for e in log[-4:]] == ["card_created"] * 3 + ["plan_approved"]
    assert {e["actor"] for e in log[-4:]} == {"human"}
    assert len(log[-1]["transitions"]) == 3


def test_a_plan_on_cards_already_approved_starts_in_backlog(vault, monkeypatch):
    data = canvas.load(vault)
    canvas.find_card(data, "old")["color"] = canvas.APPROVED
    canvas.save(vault, data)
    plan = "summary: s\ncards:\n  - {id: n, title: N, done_when: done here, depends_on: [old]}\n"
    run(monkeypatch, vault, "plan", "propose", "--file", write(vault, plan))
    run(monkeypatch, vault, "plan", "approve")
    assert colors(vault)["n"] == canvas.BACKLOG


@pytest.mark.parametrize("text, fragment", [
    ("- just a list\n", "must be a mapping"),
    ("cards: []\n", "'summary' is required"),
    ("summary: s\n", "'cards' must be a non-empty list"),
    ("summary: s\nextra: 1\ncards: [{id: a, title: A, done_when: d}]\n", "unknown key 'extra'"),
    ("summary: s\ncards: [{id: a, title: A}]\n", "card 1 (a): 'done_when' is required"),
    ("summary: s\ncards: [{id: a, title: A, done_when: d, depend_on: [x]}]\n", "unknown key 'depend_on'"),
    ("summary: s\ncards: [{id: a, title: A, done_when: d, depends_on: x}]\n", "'depends_on' must be a list"),
    ("summary: s\ncards: [{id: a.b, title: A, done_when: d}]\n", "'a.b': not a valid id"),
    ("summary: s\ncards: [{id: a, title: A, done_when: d}, {id: a, title: B, done_when: d}]\n", "appears twice"),
    ("summary: s\ncards: [{id: old, title: A, done_when: d}]\n", "'old': a card with that id already exists"),
    ("summary: s\ncards: [{id: a, title: A, done_when: d, depends_on: [ghost]}]\n", "depends on 'ghost'"),
    ("summary: s\ncards: [{id: a, title: A, done_when: d, depends_on: [a]}]\n", "can't depend on itself"),
    ("summary: s\ncards:\n  - {id: a, title: A, done_when: d, depends_on: [b]}\n"
     "  - {id: b, title: B, done_when: d, depends_on: [a]}\n", "dependency cycle: "),
    ("summary: [unclosed\n", "can't be parsed as YAML"),
])
def test_invalid_plans_never_reach_the_human(vault, monkeypatch, capsys, text, fragment):
    assert run(monkeypatch, vault, "plan", "propose", "--file", write(vault, text)) == 1
    assert fragment in capsys.readouterr().err
    assert not plans.plan_path(vault).exists()


def test_all_shape_errors_come_at_once(vault, monkeypatch, capsys):
    text = "summary: s\ncards: [{id: a}, {title: B, done_when: d}]\n"
    run(monkeypatch, vault, "plan", "propose", "--file", write(vault, text))
    err = capsys.readouterr().err
    assert err.count("is required") == 3


def test_approve_rechecks_against_the_board(vault, monkeypatch, capsys):
    run(monkeypatch, vault, "plan", "propose", "--file", write(vault, PLAN))
    run(monkeypatch, vault, "add-card", "cod-a", "--title", "Someone made it meanwhile")
    capsys.readouterr()
    assert run(monkeypatch, vault, "plan", "approve") == 1
    assert "no longer fits the board" in capsys.readouterr().err
    assert set(colors(vault)) == {"old", "cod-a"} and plans.plan_path(vault).exists()


def test_a_failure_halfway_leaves_nothing_behind(vault, monkeypatch):
    run(monkeypatch, vault, "plan", "propose", "--file", write(vault, PLAN))
    real_save = canvas.save

    def broken_save(vault_root, data):
        raise OSError("disk full")
    monkeypatch.setattr(canvas, "save", broken_save)
    with pytest.raises(OSError):
        plans.approve(vault)
    monkeypatch.setattr(canvas, "save", real_save)
    assert set(colors(vault)) == {"old"}
    assert sorted(p.name for p in (vault / "content").iterdir()) == ["old.md"]
    assert plans.plan_path(vault).exists()


def test_a_stray_file_is_never_overwritten(vault, monkeypatch, capsys):
    (vault / "content" / "rel.md").write_text("mine\n", encoding="utf-8")
    run(monkeypatch, vault, "plan", "propose", "--file", write(vault, PLAN))
    assert run(monkeypatch, vault, "plan", "approve") == 1
    assert "content/rel.md already exists" in capsys.readouterr().err
    assert (vault / "content" / "rel.md").read_text(encoding="utf-8") == "mine\n"
    assert set(colors(vault)) == {"old"} and not (vault / "content" / "dec-a.md").exists()


def test_reject_keeps_the_feedback_until_the_next_plan(vault, monkeypatch, capsys):
    run(monkeypatch, vault, "plan", "propose", "--file", write(vault, PLAN))
    assert run(monkeypatch, vault, "plan", "reject", "--feedback", "Split cod-a in two") == 0
    assert "plan discarded (3 cards, none created)" in capsys.readouterr().out
    run(monkeypatch, vault, "plan", "diff")
    assert capsys.readouterr().out == "no pending plan\nFeedback on the last rejected plan: Split cod-a in two\n"
    assert events.read(vault)[-1]["data"] == {"cards": 3, "feedback": "Split cod-a in two"}
    run(monkeypatch, vault, "plan", "propose", "--file", write(vault, PLAN))
    assert plans.last_feedback(vault) is None


def test_proposing_again_replaces_and_warns(vault, monkeypatch, capsys):
    run(monkeypatch, vault, "plan", "propose", "--file", write(vault, PLAN))
    capsys.readouterr()
    run(monkeypatch, vault, "plan", "propose", "--file", write(vault, PLAN))
    assert "replaced the plan" in capsys.readouterr().err


def test_nothing_pending(vault, monkeypatch, capsys):
    assert run(monkeypatch, vault, "plan", "approve") == 1
    assert run(monkeypatch, vault, "plan", "reject") == 1
    assert capsys.readouterr().err.count("there's no pending plan") == 2


def test_two_approvals_at_once_create_the_cards_once(vault, monkeypatch):
    run(monkeypatch, vault, "plan", "propose", "--file", write(vault, PLAN))
    results = []

    def approve():
        try:
            results.append(len(plans.approve(vault).created))
        except HistosError as e:
            results.append(str(e))
    threads = [threading.Thread(target=approve) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results, key=str) == [3, "there's no pending plan"]
    assert len(colors(vault)) == 4


def test_topological_order_puts_dependencies_first():
    plan = plans.parse(PLAN.replace("  - id: dec-a\n    title: Decide A\n    done_when: A is decided and written down.\n",
                                    "") + "  - id: dec-a\n    title: Decide A\n    done_when: decided.\n")
    assert [c.id for c in plans.topological_order(plan)] == ["dec-a", "cod-a", "rel"]
