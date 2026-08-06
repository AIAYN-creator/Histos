from trellis import validation


def test_valid_minimal_canvas_passes():
    data = {
        "nodes": [{"id": "a", "type": "file", "x": 0, "y": 0, "width": 250, "height": 100,
                   "file": "content/a.md", "color": "4"}],
        "edges": [],
    }
    assert validation.validate_all(data) == []


def test_rejects_link_node():
    data = {
        "nodes": [{"id": "x", "type": "link", "x": 0, "y": 0, "width": 100, "height": 100, "url": "https://x"}],
        "edges": [],
    }
    assert validation.validate_schema(data)


def test_rejects_card_without_color():
    data = {
        "nodes": [{"id": "x", "type": "file", "x": 0, "y": 0, "width": 100, "height": 100,
                   "file": "content/x.md"}],
        "edges": [],
    }
    assert validation.validate_schema(data)


def test_rejects_color_outside_legend():
    data = {
        "nodes": [{"id": "x", "type": "file", "x": 0, "y": 0, "width": 100, "height": 100,
                   "file": "content/x.md", "color": "7"}],
        "edges": [],
    }
    assert validation.validate_schema(data)


def test_rejects_file_outside_content_dir():
    data = {
        "nodes": [{"id": "x", "type": "file", "x": 0, "y": 0, "width": 100, "height": 100,
                   "file": "x.md", "color": "1"}],
        "edges": [],
    }
    assert validation.validate_schema(data)


def test_accepts_text_node_for_legend():
    data = {
        "nodes": [{"id": "legend", "type": "text", "x": 0, "y": -300, "width": 500, "height": 250,
                   "text": "## Leyenda\n\n..."}],
        "edges": [],
    }
    assert validation.validate_schema(data) == []


def test_rejects_dangling_edge_reference():
    data = {
        "nodes": [{"id": "a", "type": "file", "x": 0, "y": 0, "width": 100, "height": 100,
                   "file": "content/a.md", "color": "4"}],
        "edges": [{"id": "e1", "fromNode": "a", "toNode": "no-existe"}],
    }
    errors = validation.validate_all(data)
    assert any("referencia" in e for e in errors)


def test_rejects_cycle():
    nodes = [
        {"id": n, "type": "file", "x": 0, "y": 0, "width": 100, "height": 100,
         "file": f"content/{n}.md", "color": "6"}
        for n in ("a", "b")
    ]
    data = {
        "nodes": nodes,
        "edges": [
            {"id": "e1", "fromNode": "a", "toNode": "b"},
            {"id": "e2", "fromNode": "b", "toNode": "a"},
        ],
    }
    errors = validation.validate_all(data)
    assert any("ciclo" in e for e in errors)
