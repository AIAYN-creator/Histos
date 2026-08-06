from trellis import canvas


def _card(id_, color):
    return {
        "id": id_, "type": "file", "x": 0, "y": 0, "width": 250, "height": 100,
        "file": f"content/{id_}.md", "color": color,
    }


def test_load_missing_raises(tmp_path):
    try:
        canvas.load(tmp_path)
        assert False, "esperaba TrellisError"
    except canvas.TrellisError:
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
