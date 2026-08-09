from histos import canvas


def _card(id_, color):
    return {
        "id": id_, "type": "file", "x": 0, "y": 0, "width": 250, "height": 100,
        "file": f"content/{id_}.md", "color": color,
    }


def test_is_valid_card_id_accepts_slug():
    assert canvas.is_valid_card_id("cap1") is True
    assert canvas.is_valid_card_id("cap-1_intro") is True


def test_is_valid_card_id_rejects_path_traversal():
    assert canvas.is_valid_card_id("../evil") is False
    assert canvas.is_valid_card_id("..\\evil") is False
    assert canvas.is_valid_card_id("a/b") is False
    assert canvas.is_valid_card_id("") is False


def test_load_missing_raises(tmp_path):
    try:
        canvas.load(tmp_path)
        assert False, "esperaba HistosError"
    except canvas.HistosError:
        pass


def test_save_and_load_roundtrip(tmp_path):
    data = {"nodes": [_card("a", canvas.APROBADA)], "edges": []}
    canvas.save(tmp_path, data)
    assert canvas.load(tmp_path) == data


def test_recompute_blocked_marks_bloqueada_when_dep_not_approved():
    data = {
        "nodes": [_card("a", canvas.EN_PROGRESO), _card("b", canvas.BACKLOG)],
        "edges": [{"id": "e1", "fromNode": "a", "toNode": "b"}],
    }
    assert canvas.recompute_blocked(data) is True
    assert canvas.find_card(data, "b")["color"] == canvas.BLOQUEADA


def test_recompute_blocked_unblocks_when_dep_approved():
    data = {
        "nodes": [_card("a", canvas.APROBADA), _card("b", canvas.BLOQUEADA)],
        "edges": [{"id": "e1", "fromNode": "a", "toNode": "b"}],
    }
    assert canvas.recompute_blocked(data) is True
    assert canvas.find_card(data, "b")["color"] == canvas.BACKLOG


def test_recompute_blocked_does_not_touch_active_states():
    data = {
        "nodes": [_card("a", canvas.BACKLOG), _card("b", canvas.EN_PROGRESO)],
        "edges": [{"id": "e1", "fromNode": "a", "toNode": "b"}],
    }
    assert canvas.recompute_blocked(data) is False
    assert canvas.find_card(data, "b")["color"] == canvas.EN_PROGRESO


def test_detect_cycle_finds_cycle():
    data = {
        "nodes": [_card(n, canvas.BACKLOG) for n in ("a", "b", "c")],
        "edges": [
            {"id": "e1", "fromNode": "a", "toNode": "b"},
            {"id": "e2", "fromNode": "b", "toNode": "c"},
            {"id": "e3", "fromNode": "c", "toNode": "a"},
        ],
    }
    cycle = canvas.detect_cycle(data)
    assert cycle is not None
    assert cycle[0] == cycle[-1]


def test_detect_cycle_none_for_dag():
    data = {
        "nodes": [_card("a", canvas.BACKLOG), _card("b", canvas.BACKLOG)],
        "edges": [{"id": "e1", "fromNode": "a", "toNode": "b"}],
    }
    assert canvas.detect_cycle(data) is None


def test_estimate_card_size_scales_with_description_length():
    _, short_h = canvas.estimate_card_size("corto")
    _, long_h = canvas.estimate_card_size("x" * 200)
    assert long_h > short_h


def test_estimate_card_size_no_description_is_minimum():
    width, height = canvas.estimate_card_size(None)
    assert width == canvas.CARD_WIDTH
    assert height == canvas.CARD_HEIGHT


def test_compute_ranks_linear_chain():
    data = {
        "nodes": [_card(n, canvas.BACKLOG) for n in ("a", "b", "c")],
        "edges": [
            {"id": "e1", "fromNode": "a", "toNode": "b"},
            {"id": "e2", "fromNode": "b", "toNode": "c"},
        ],
    }
    assert canvas.compute_ranks(data) == {"a": 0, "b": 1, "c": 2}


def test_compute_ranks_takes_longest_incoming_path():
    # d depende de b (rank 1) y de c (rank 0) -> rank(d) = max(1, 0) + 1 = 2
    data = {
        "nodes": [_card(n, canvas.BACKLOG) for n in ("a", "b", "c", "d")],
        "edges": [
            {"id": "e1", "fromNode": "a", "toNode": "b"},
            {"id": "e2", "fromNode": "b", "toNode": "d"},
            {"id": "e3", "fromNode": "c", "toNode": "d"},
        ],
    }
    assert canvas.compute_ranks(data)["d"] == 2


def test_relayout_stacks_same_rank_vertically():
    data = {"nodes": [_card("a", canvas.BACKLOG), _card("b", canvas.BACKLOG)], "edges": []}
    canvas.relayout(data)
    a, b = canvas.find_card(data, "a"), canvas.find_card(data, "b")
    assert a["x"] == b["x"]
    assert a["y"] != b["y"]


def test_relayout_separates_columns_by_rank():
    data = {
        "nodes": [_card("a", canvas.BACKLOG), _card("b", canvas.BACKLOG)],
        "edges": [{"id": "e1", "fromNode": "a", "toNode": "b"}],
    }
    canvas.relayout(data)
    a, b = canvas.find_card(data, "a"), canvas.find_card(data, "b")
    assert b["x"] > a["x"]
