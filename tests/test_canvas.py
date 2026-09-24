import copy

from histos import canvas, validation


def _card(id_, color):
    return {
        "id": id_, "type": "file", "x": 0, "y": 0, "width": 250, "height": 100,
        "file": f"content/{id_}.md", "color": color,
    }


def _edge(from_id, to_id):
    return {"id": f"{from_id}->{to_id}", "fromNode": from_id, "toNode": to_id}


def _overlap(a, b):
    return (
        a["x"] < b["x"] + b["width"] and b["x"] < a["x"] + a["width"]
        and a["y"] < b["y"] + b["height"] and b["y"] < a["y"] + a["height"]
    )


def _inside(node, group):
    return (
        group["x"] <= node["x"] and node["x"] + node["width"] <= group["x"] + group["width"]
        and group["y"] <= node["y"] and node["y"] + node["height"] <= group["y"] + group["height"]
    )


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


def test_recompute_blocked_demotes_en_progreso_when_dep_not_approved():
    # p.ej. 'histos link' anade una dependencia sin aprobar a una tarjeta ya asignada:
    # Bloqueada es derivada del grafo, no algo que solo se fija al crear/asignar.
    data = {
        "nodes": [_card("a", canvas.BACKLOG), _card("b", canvas.EN_PROGRESO)],
        "edges": [{"id": "e1", "fromNode": "a", "toNode": "b"}],
    }
    assert canvas.recompute_blocked(data) is True
    assert canvas.find_card(data, "b")["color"] == canvas.BLOQUEADA


def test_recompute_blocked_leaves_en_progreso_alone_when_deps_satisfied():
    data = {
        "nodes": [_card("a", canvas.APROBADA), _card("b", canvas.EN_PROGRESO)],
        "edges": [{"id": "e1", "fromNode": "a", "toNode": "b"}],
    }
    assert canvas.recompute_blocked(data) is False
    assert canvas.find_card(data, "b")["color"] == canvas.EN_PROGRESO


def test_recompute_blocked_does_not_auto_restore_en_progreso():
    # Una tarjeta degradada a Bloqueada no vuelve sola a En progreso al aprobarse la
    # dependencia -- aterriza en Backlog, hace falta 'assign' de nuevo para retomarla.
    data = {
        "nodes": [_card("a", canvas.BACKLOG), _card("b", canvas.BLOQUEADA)],
        "edges": [{"id": "e1", "fromNode": "a", "toNode": "b"}],
    }
    canvas.find_card(data, "a")["color"] = canvas.APROBADA
    assert canvas.recompute_blocked(data) is True
    assert canvas.find_card(data, "b")["color"] == canvas.BACKLOG


def test_recompute_blocked_does_not_touch_review_or_approved_states():
    data = {
        "nodes": [
            _card("a", canvas.BACKLOG),
            _card("b", canvas.PROPUESTA_PENDIENTE),
            _card("c", canvas.APROBADA),
        ],
        "edges": [
            {"id": "e1", "fromNode": "a", "toNode": "b"},
            {"id": "e2", "fromNode": "a", "toNode": "c"},
        ],
    }
    assert canvas.recompute_blocked(data) is False
    assert canvas.find_card(data, "b")["color"] == canvas.PROPUESTA_PENDIENTE
    assert canvas.find_card(data, "c")["color"] == canvas.APROBADA


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


def test_assign_columns_linear_chain():
    columns = canvas._assign_columns({"a", "b", "c"}, [("a", "b"), ("b", "c")])
    assert columns == {"a": 0, "b": 1, "c": 2}


def test_assign_columns_slides_a_lone_dependency_up_to_its_dependent():
    # c only feeds d, so it sits right before d instead of far left at column 0
    columns = canvas._assign_columns({"a", "b", "c", "d"}, [("a", "b"), ("b", "d"), ("c", "d")])
    assert columns == {"a": 0, "b": 1, "c": 1, "d": 2}


def test_place_new_card_stacks_same_rank_vertically():
    data = {"nodes": [], "edges": []}
    a = _card("a", canvas.BACKLOG)
    data["nodes"].append(a)
    canvas.place_new_card(data, a)

    b = _card("b", canvas.BACKLOG)
    data["nodes"].append(b)
    canvas.place_new_card(data, b)

    assert a["x"] == b["x"]
    assert a["y"] != b["y"]
    assert not _overlap(a, b)


def test_place_new_card_separates_columns_by_rank():
    data = {"nodes": [], "edges": []}
    a = _card("a", canvas.BACKLOG)
    data["nodes"].append(a)
    canvas.place_new_card(data, a)

    b = _card("b", canvas.BACKLOG)
    data["nodes"].append(b)
    data["edges"].append({"id": "e1", "fromNode": "a", "toNode": "b"})
    canvas.place_new_card(data, b)

    assert b["x"] > a["x"]


def test_place_new_card_never_moves_existing_cards():
    data = {"nodes": [], "edges": []}
    a = _card("a", canvas.BACKLOG)
    data["nodes"].append(a)
    canvas.place_new_card(data, a)
    a["x"], a["y"] = 999, 777  # simulates the user having dragged it by hand in Obsidian

    b = _card("b", canvas.BACKLOG)
    data["nodes"].append(b)
    canvas.place_new_card(data, b)

    assert (a["x"], a["y"]) == (999, 777)


def test_place_new_card_avoids_overlap_even_off_grid():
    # After manual moves the board is no longer a clean rank-based grid -- a new card at
    # the same rank as an existing one must still dodge it wherever it actually sits.
    data = {"nodes": [], "edges": []}
    a = _card("a", canvas.BACKLOG)
    a["x"], a["y"] = 0, 500
    data["nodes"].append(a)

    b = _card("b", canvas.BACKLOG)
    data["nodes"].append(b)
    canvas.place_new_card(data, b)

    assert not _overlap(a, b)


def test_add_edge_connects_right_side_to_left_side():
    data = {"nodes": [_card("a", canvas.BACKLOG), _card("b", canvas.BACKLOG)], "edges": []}
    canvas.add_edge(data, "a", "b")
    edge = data["edges"][0]
    assert (edge["fromSide"], edge["toSide"]) == ("right", "left")


def test_save_fixes_the_sides_obsidian_froze_on_dependency_arrows(tmp_path):
    note = {"id": "note", "type": "text", "x": 0, "y": -300, "width": 100, "height": 50, "text": "hi"}
    data = {
        "nodes": [_card("a", canvas.BACKLOG), _card("b", canvas.BACKLOG), note],
        "edges": [
            dict(_edge("a", "b"), fromSide="top", toSide="bottom"),
            dict(_edge("note", "a"), fromSide="bottom", toSide="top"),
        ],
    }
    canvas.save(tmp_path, data)

    dependency, annotation = canvas.load(tmp_path)["edges"]
    assert (dependency["fromSide"], dependency["toSide"]) == ("right", "left")
    assert (annotation["fromSide"], annotation["toSide"]) == ("bottom", "top")  # not a dependency: untouched


def test_place_new_card_goes_right_of_its_dependencies_at_their_height():
    a = dict(_card("a", canvas.APROBADA), x=0, y=200)
    b = dict(_card("b", canvas.APROBADA), x=600, y=600)
    new = _card("new", canvas.BACKLOG)
    data = {"nodes": [a, b, new], "edges": [_edge("a", "new"), _edge("b", "new")]}

    canvas.place_new_card(data, new)

    assert new["x"] == b["x"] + b["width"] + canvas.COLUMN_GAP
    assert new["y"] == 400  # centered between a (center 250) and b (center 650)


def test_place_new_card_does_not_land_on_an_arrow_crossing_its_column():
    # a -> c runs level at y=450, straight through the column where 'new' goes
    a = dict(_card("a", canvas.BACKLOG), x=-600, y=400)
    c = dict(_card("c", canvas.BACKLOG), x=1200, y=400)
    dep = dict(_card("dep", canvas.BACKLOG), x=0, y=400)
    new = _card("new", canvas.BACKLOG)
    data = {"nodes": [a, c, dep, new], "edges": [_edge("a", "c"), _edge("dep", "new")]}

    canvas.place_new_card(data, new)

    assert new["x"] == dep["x"] + dep["width"] + canvas.COLUMN_GAP
    crossing = canvas._arrow_ys_within(a, c, new["x"], new["x"] + new["width"])
    assert crossing
    assert all(y < new["y"] or y > new["y"] + new["height"] for y in crossing)


def test_prune_redundant_edges_removes_only_arrows_a_longer_path_implies():
    data = {
        "nodes": [_card(n, canvas.BACKLOG) for n in "abcd"],
        "edges": [_edge("a", "b"), _edge("b", "c"), _edge("a", "c"), _edge("c", "d"), _edge("a", "d")],
    }
    assert sorted(canvas.prune_redundant_edges(data)) == [("a", "c"), ("a", "d")]
    assert {(e["fromNode"], e["toNode"]) for e in data["edges"]} == {("a", "b"), ("b", "c"), ("c", "d")}


def test_prune_redundant_edges_keeps_parallel_paths():
    data = {
        "nodes": [_card(n, canvas.BACKLOG) for n in "abcd"],
        "edges": [_edge("a", "b"), _edge("a", "c"), _edge("b", "d"), _edge("c", "d")],
    }
    assert canvas.prune_redundant_edges(data) == []
    assert len(data["edges"]) == 4


def test_pack_in_order_moves_items_as_little_as_possible():
    assert canvas._pack_in_order([0, 500], [1, 1], [100, 100]) == [0, 500]
    assert canvas._pack_in_order([0, 0], [1, 1], [100, 100]) == [-70, 70]
    assert canvas._pack_in_order([0, 0], [3, 1], [100, 100]) == [-35, 105]
    assert canvas._pack_in_order([0, 0], [1, 1], [100, 100], gaps=[10, 0]) == [-55, 55]


def _project():
    """A spine of work, a side branch, finished history, and two cards with no arrows at all."""
    colors = {
        "old-a": canvas.APROBADA, "old-b": canvas.APROBADA, "spec": canvas.APROBADA,
        "design": canvas.APROBADA, "build": canvas.EN_PROGRESO, "test": canvas.BLOQUEADA,
        "docs": canvas.BACKLOG, "release": canvas.BLOQUEADA, "idea": canvas.BACKLOG,
        "archived": canvas.APROBADA,
    }
    edges = [
        ("old-a", "old-b"), ("old-b", "design"), ("spec", "design"), ("design", "build"), ("build", "test"),
        ("test", "release"), ("spec", "docs"), ("docs", "release"), ("spec", "release"),
    ]
    return {
        "nodes": [canvas.build_legend_node()] + [_card(cid, color) for cid, color in colors.items()],
        "edges": [_edge(u, v) for u, v in edges],
    }


def _cards_by_id(data):
    return {n["id"]: n for n in data["nodes"] if n["type"] == "file"}


def _node(data, node_id):
    return next((n for n in data["nodes"] if n["id"] == node_id), None)


def test_arrange_makes_every_arrow_flow_left_to_right():
    data = _project()
    canvas.arrange(data)
    by_id = _cards_by_id(data)
    for e in data["edges"]:
        source, target = by_id[e["fromNode"]], by_id[e["toNode"]]
        assert target["x"] > source["x"] + source["width"], e["id"]
        assert (e["fromSide"], e["toSide"]) == ("right", "left")


def test_arrange_never_overlaps_cards():
    data = _project()
    canvas.arrange(data, set_aside_done=True, prune_redundant=True)
    cards = list(_cards_by_id(data).values())
    assert not any(_overlap(a, b) for i, a in enumerate(cards) for b in cards[i + 1:])


def test_arrange_leaves_no_arrow_across_a_card():
    data = _project()
    # crowd the columns that design -> release and docs -> release have to skip over
    data["nodes"] += [_card(f"extra{i}", canvas.BACKLOG) for i in range(6)]
    data["edges"] += [_edge("design", f"extra{i}") for i in range(6)] + [_edge("design", "release")]
    canvas.arrange(data)

    by_id = _cards_by_id(data)
    for e in data["edges"]:
        source, target = by_id[e["fromNode"]], by_id[e["toNode"]]
        for card in by_id.values():
            if source["x"] + source["width"] < card["x"] and card["x"] + card["width"] < target["x"]:
                ys = canvas._arrow_ys_within(source, target, card["x"], card["x"] + card["width"])
                assert all(y <= card["y"] or y >= card["y"] + card["height"] for y in ys), (e["id"], card["id"])


def test_arrange_puts_cards_without_arrows_in_their_own_group():
    data = _project()
    summary = canvas.arrange(data)
    group = _node(data, canvas.UNCONNECTED_GROUP_ID)
    by_id = _cards_by_id(data)
    assert summary.unconnected == 2
    assert _inside(by_id["idea"], group) and _inside(by_id["archived"], group)
    assert not _inside(by_id["spec"], group)


def test_arrange_sets_aside_finished_work_only_when_asked():
    data = _project()
    summary = canvas.arrange(data, set_aside_done=True)
    done = _node(data, canvas.DONE_GROUP_ID)
    by_id = _cards_by_id(data)
    assert summary.set_aside_done == 3
    assert all(_inside(by_id[c], done) for c in ("old-a", "old-b", "archived"))
    # approved, but something still pending depends on them
    assert not any(_inside(by_id[c], done) for c in ("spec", "design"))

    plain = _project()
    canvas.arrange(plain)
    assert _node(plain, canvas.DONE_GROUP_ID) is None


def test_arrange_draws_its_groups_behind_cards_and_replaces_only_its_own():
    data = _project()
    chapter = {"id": "chapter-3", "type": "group", "x": -2000, "y": -2000, "width": 300, "height": 200}
    data["nodes"].append(chapter)
    canvas.arrange(data, set_aside_done=True)
    canvas.arrange(data, set_aside_done=True)

    ids = [n["id"] for n in data["nodes"]]
    assert ids.count(canvas.DONE_GROUP_ID) == 1 and ids.count(canvas.UNCONNECTED_GROUP_ID) == 1
    first_card = next(i for i, n in enumerate(data["nodes"]) if n["type"] == "file")
    assert ids.index(canvas.DONE_GROUP_ID) < first_card and ids.index(canvas.UNCONNECTED_GROUP_ID) < first_card
    assert chapter in data["nodes"] and (chapter["x"], chapter["y"]) == (-2000, -2000)


def test_arrange_leaves_the_legend_alone_and_the_canvas_valid():
    data = _project()
    legend_before = dict(_node(data, canvas.LEGEND_ID))
    canvas.arrange(data, set_aside_done=True, prune_redundant=True)
    assert _node(data, canvas.LEGEND_ID) == legend_before
    assert validation.validate_all(data) == []


def test_arrange_prunes_redundant_arrows_only_when_asked():
    kept = _project()
    assert canvas.arrange(kept).pruned == []
    assert any(e["id"] == "spec->release" for e in kept["edges"])

    pruned = _project()
    assert canvas.arrange(pruned, prune_redundant=True).pruned == [("spec", "release")]
    assert not any(e["id"] == "spec->release" for e in pruned["edges"])


def test_arrange_is_deterministic():
    first, second = _project(), _project()
    canvas.arrange(first, set_aside_done=True, prune_redundant=True)
    canvas.arrange(second, set_aside_done=True, prune_redundant=True)
    assert first == second


def _without_position(card):
    return {k: v for k, v in card.items() if k not in ("x", "y")}


def test_arrange_only_moves_cards():
    original = _project()
    arranged = copy.deepcopy(original)
    canvas.arrange(arranged, set_aside_done=True, prune_redundant=True)
    assert (
        {cid: _without_position(c) for cid, c in _cards_by_id(arranged).items()}
        == {cid: _without_position(c) for cid, c in _cards_by_id(original).items()}
    )


def test_arrange_handles_a_board_without_cards():
    data = {"nodes": [canvas.build_legend_node()], "edges": []}
    summary = canvas.arrange(data, set_aside_done=True, prune_redundant=True)
    assert summary.columns == 0
    assert [n["id"] for n in data["nodes"]] == [canvas.LEGEND_ID]
