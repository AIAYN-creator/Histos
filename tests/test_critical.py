"""critical.py: the critical path -- what's left, the heaviest chain, and painting the arrows
without ever touching the human's own colors (cod-camino)."""
import json

import pytest

from histos import canvas, critical

MAGENTA = "#E83E8C"


def board(*cards, edges=(), approved=()):
    data = {"nodes": [canvas.build_legend_node()], "edges": []}
    for cid in cards:
        canvas.add_card_node(data, cid, canvas.APPROVED if cid in approved else canvas.BACKLOG)
    for a, b in edges:
        canvas.add_edge(data, a, b)
    return data


def edge(data, a, b):
    return next(e for e in data["edges"] if (e["fromNode"], e["toNode"]) == (a, b))


# ---- what's left of a card ----

@pytest.mark.parametrize("state, done_agent, done_review, expected", [
    ("Backlog", 0, 0, 5.0),            # the whole estimate: 1 h agent + 4 h review
    ("Blocked", 0, 0, 5.0),
    ("In progress", 0.25, 0, 4.75),    # minus what the agent already put in
    ("In progress", 3.0, 0, 4.0),      # never less than the whole review
    ("Proposal pending review", 0, 1.5, 2.5),
    ("Proposal pending review", 0, 9.0, 0.0),
])
def test_remaining_hours(state, done_agent, done_review, expected):
    assert critical.remaining_hours(state, 1.0, 4.0, done_agent, done_review) == expected


def test_no_estimate_means_unknown():
    assert critical.remaining_hours("Backlog", None, 4.0) is None
    assert critical.remaining_hours("Proposal pending review", None, 4.0) == 4.0  # only the review matters


# ---- the heaviest chain ----

def test_with_no_history_it_is_the_longest_chain_of_cards():
    data = board("a", "b", "c", "x", edges=[("a", "b"), ("b", "c"), ("x", "c")])
    path = critical.compute(data, {}, by_count=True)
    assert (path.cards, path.by_count) == (["a", "b", "c"], True)


def test_hours_beat_card_count():
    data = board("a", "b", "c", "slow", "end", edges=[("a", "b"), ("b", "c"), ("c", "end"), ("slow", "end")])
    weights = {"a": 1, "b": 1, "c": 1, "slow": 10, "end": 1}
    path = critical.compute(data, weights)
    assert (path.cards, path.hours) == (["slow", "end"], 11.0)


def test_approved_cards_are_not_on_the_path():
    data = board("done", "a", "b", edges=[("done", "a"), ("a", "b")], approved={"done"})
    assert critical.compute(data, {"a": 1, "b": 1}).cards == ["a", "b"]


def test_ties_are_deterministic():
    data = board("a", "b", "c", "d", edges=[("a", "c"), ("b", "d")])
    paths = {tuple(critical.compute(data, {k: 1 for k in "abcd"}).cards) for _ in range(5)}
    assert paths == {("a", "c")}  # same weight and length: the first on the board wins


def test_implied_dependencies_count(tmp_path):
    data = board("a", "b", "c", edges=[("a", "b")])
    (tmp_path / "content").mkdir()
    for cid in "abc":
        (tmp_path / "content" / f"{cid}.md").write_text("---\n---\n", encoding="utf-8")
    (tmp_path / "content" / "c.md").write_text("---\nimplied_dependencies: [b]\n---\n", encoding="utf-8")
    assert critical.compute(data, {k: 1 for k in "abc"}, tmp_path).cards == ["a", "b", "c"]


def test_an_empty_board():
    assert critical.compute(board(), {}).cards == []


# ---- painting the arrows ----

def test_paint_colors_the_path_and_notes_it():
    data = board("a", "b", "c", edges=[("a", "b"), ("b", "c")])
    changed, ledger = critical.paint(data, ["a", "b", "c"], {}, MAGENTA)
    assert changed and set(ledger) == {"a->b", "b->c"}
    assert edge(data, "a", "b")["color"] == MAGENTA


def test_arrows_that_leave_the_path_lose_our_color():
    data = board("a", "b", "c", edges=[("a", "b"), ("b", "c")])
    _, ledger = critical.paint(data, ["a", "b", "c"], {}, MAGENTA)
    changed, ledger = critical.paint(data, ["b", "c"], ledger, MAGENTA)
    assert changed and "color" not in edge(data, "a", "b") and set(ledger) == {"b->c"}


def test_an_arrow_the_human_recolored_is_theirs():
    data = board("a", "b", edges=[("a", "b")])
    _, ledger = critical.paint(data, ["a", "b"], {}, MAGENTA)
    edge(data, "a", "b")["color"] = "4"  # the human, in Obsidian
    changed, ledger = critical.paint(data, [], ledger, MAGENTA)
    assert not changed and edge(data, "a", "b")["color"] == "4" and ledger == {}


def test_the_humans_color_on_the_path_is_never_painted_over():
    data = board("a", "b", edges=[("a", "b")])
    edge(data, "a", "b")["color"] = "#123456"
    changed, ledger = critical.paint(data, ["a", "b"], {}, MAGENTA)
    assert not changed and edge(data, "a", "b")["color"] == "#123456" and ledger == {}


def test_a_lost_ledger_only_claims_exactly_our_color():
    data = board("a", "b", "c", edges=[("a", "b"), ("b", "c")])
    edge(data, "a", "b")["color"] = MAGENTA
    edge(data, "b", "c")["color"] = "#00FF00"
    changed, ledger = critical.paint(data, [], None, MAGENTA)
    assert changed and "color" not in edge(data, "a", "b") and edge(data, "b", "c")["color"] == "#00FF00"


def test_painting_twice_changes_nothing():
    data = board("a", "b", edges=[("a", "b")])
    _, ledger = critical.paint(data, ["a", "b"], {}, MAGENTA)
    changed, again = critical.paint(data, ["a", "b"], ledger, MAGENTA)
    assert not changed and again == ledger


def test_a_dependency_without_an_arrow_is_simply_not_painted():
    data = board("a", "b", "c", edges=[("a", "b")])  # b -> c only implied
    changed, ledger = critical.paint(data, ["a", "b", "c"], {}, MAGENTA)
    assert set(ledger) == {"a->b"}


# ---- the legend ----

def test_a_histos_legend_is_upgraded_and_an_edited_one_left_alone():
    data = board("a")
    legend = data["nodes"][0]
    legend["text"] = next(iter(canvas.OLDER_LEGEND_TEXTS))
    assert critical._update_legend(data) and legend["text"] == canvas.LEGEND_TEXT
    legend["text"] = "my own notes"
    assert not critical._update_legend(data) and legend["text"] == "my own notes"


def test_new_vaults_get_the_new_legend():
    assert "critical path" in canvas.build_legend_node()["text"]
    assert canvas.build_legend_node()["text"] not in canvas.OLDER_LEGEND_TEXTS


def test_ledger_is_plain_json(tmp_path):
    critical.ledger_path(tmp_path).parent.mkdir()
    critical.ledger_path(tmp_path).write_text(json.dumps({"a->b": MAGENTA}), encoding="utf-8")
    assert critical._read_ledger(tmp_path) == {"a->b": MAGENTA}
    critical.ledger_path(tmp_path).write_text("not json", encoding="utf-8")
    assert critical._read_ledger(tmp_path) is None
