"""Carga, mutacion y guardado del project.canvas -- ver docs/canvas-schema.md."""
from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

CANVAS_FILENAME = "project.canvas"

# Leyenda de estado (docs/canvas-schema.md)
BLOQUEADA = "1"
EN_PROGRESO = "2"
PROPUESTA_PENDIENTE = "3"
APROBADA = "4"
SOLICITUD_CAMBIO_DEPENDENCIA = "5"
BACKLOG = "6"

# Backlog/Bloqueada se recalculan libremente en las dos direcciones segun el grafo.
# En progreso solo se degrada a Bloqueada (nunca se auto-restaura desde aqui: una
# tarjeta desbloqueada aterriza en Backlog y hace falta 'assign' de nuevo para
# retomarla). Propuesta/Solicitud/Aprobada quedan fuera del todo: son estados con una
# accion humana de por medio, no derivables solo del grafo.
_FREELY_DERIVED_COLORS = {BLOQUEADA, BACKLOG}
_DEMOTABLE_TO_BLOQUEADA = _FREELY_DERIVED_COLORS | {EN_PROGRESO}

CARD_WIDTH = 280
CARD_HEIGHT = 100
CARD_ROW_Y = 200
CARD_X_STEP = 600
CARD_Y_GAP = 40
COLUMN_GAP = CARD_X_STEP - CARD_WIDTH
_CHARS_PER_LINE = 30
_LINE_HEIGHT = 28

EDGE_FROM_SIDE = "right"
EDGE_TO_SIDE = "left"

# ':' is not allowed in a card id, so these can never collide with one.
GROUP_ID_PREFIX = "histos:"
UNCONNECTED_GROUP_ID = "histos:unconnected"
DONE_GROUP_ID = "histos:done"
UNCONNECTED_GROUP_LABEL = "Not connected: no dependencies either way"
DONE_GROUP_LABEL = "Done: approved, and everything that depends on them too"
_GROUP_PADDING = 60
_GRID_GAP = 60
_BLOCK_GAP = 200
_ORDERING_SWEEPS = 24
_ALIGN_ITERATIONS = 30
_UP = -1
_DOWN = 1
_LONG_ARROW_WEIGHT = 3.0
_CORRIDOR_ITERATIONS = 4
_CORRIDOR_GAP = 8
_CLEARING_ROUNDS = 8
_ARROW_CLEARANCE = 16
_BAND_WEIGHT = 1000.0
# How Obsidian 1.13 draws an edge: ends pushed 7px off the card, handles clamp(distance / 2, 70, 150).
_ARROW_END_OFFSET = 7
_ARROW_HANDLE_MIN = 70
_ARROW_HANDLE_MAX = 150
_CURVE_SAMPLES = 40
_GRID_STATUS_ORDER = [EN_PROGRESO, PROPUESTA_PENDIENTE, SOLICITUD_CAMBIO_DEPENDENCIA, BLOQUEADA, BACKLOG, APROBADA]

LEGEND_ID = "legend"
_LEGEND_TEXT = """## Status legend (Histos)

| Color | Status |
|---|---|
| \U0001F7E3 purple | Backlog |
| \U0001F7E0 orange | In progress |
| \U0001F534 red | Blocked (derived, don't assign by hand) |
| \U0001F7E1 yellow | Proposal pending review |
| \U0001F535 cyan | Dependency change request |
| \U0001F7E2 green | Approved |

Detail: docs/canvas-schema.md in the Histos repo.
"""


class HistosError(Exception):
    """Errores esperables (vault no encontrado, id duplicado, etc.) -- mensaje ya listo para el usuario."""


def vault_canvas_path(vault_root: Path) -> Path:
    return vault_root / CANVAS_FILENAME


def load(vault_root: Path) -> dict:
    path = vault_canvas_path(vault_root)
    if not path.exists():
        raise HistosError(
            f"can't find {CANVAS_FILENAME} in {vault_root} -- run 'histos init' first"
        )
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save(vault_root: Path, data: dict) -> None:
    # Obsidian freezes whatever side it guesses for a side-less edge (often top/bottom) on its next save.
    normalize_edge_sides(data)
    path = vault_canvas_path(vault_root)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def cards(data: dict) -> list[dict]:
    return [n for n in data.get("nodes", []) if n.get("type") == "file"]


def find_card(data: dict, card_id: str) -> Optional[dict]:
    for n in cards(data):
        if n["id"] == card_id:
            return n
    return None


def incoming_edges(data: dict, card_id: str) -> list[dict]:
    return [e for e in data.get("edges", []) if e["toNode"] == card_id]


def card_file_path(vault_root: Path, card: dict) -> Path:
    return vault_root / card["file"]


_VALID_CARD_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def is_valid_card_id(card_id: str) -> bool:
    """El id se usa tal cual como nombre de fichero (content/{id}.md): sin esta
    restriccion, un id como '../../etc/algo' escribiria fuera del vault.
    """
    return bool(_VALID_CARD_ID_RE.match(card_id))


def add_card_node(data: dict, card_id: str, color: str, width: int = CARD_WIDTH, height: int = CARD_HEIGHT) -> dict:
    """Appends the card unpositioned -- place_new_card() gives it its real x/y once its edges exist."""
    node = {
        "id": card_id,
        "type": "file",
        "x": 0,
        "y": 0,
        "width": width,
        "height": height,
        "file": f"content/{card_id}.md",
        "color": color,
    }
    data.setdefault("nodes", []).append(node)
    return node


def estimate_card_size(description: Optional[str]) -> tuple[int, int]:
    """Heuristica de tamano a partir de la longitud de la descripcion (ancho fijo, para que
    las columnas queden alineadas). Aproximado -- Obsidian decide el wrap real -- pero
    muchisimo mejor que un 250x100 fijo para toda tarjeta sea cual sea su contenido.
    """
    desc = description or ""
    desc_lines = -(-len(desc) // _CHARS_PER_LINE) if desc else 0  # ceil division
    total_lines = 1 + desc_lines  # +1 por el titulo (heading)
    height = max(CARD_HEIGHT, 40 + total_lines * _LINE_HEIGHT + 20)
    return CARD_WIDTH, height


def place_new_card(data: dict, card: dict) -> None:
    """Positions only this card, next to its dependencies as they sit now -- never moves any other node."""
    by_id = {c["id"]: c for c in cards(data)}
    deps = [by_id[e["fromNode"]] for e in incoming_edges(data, card["id"]) if e["fromNode"] in by_id]
    if deps:
        card["x"] = max(d["x"] + d["width"] for d in deps) + COLUMN_GAP
        deps_center = sum(d["y"] + d["height"] / 2 for d in deps) / len(deps)
        ideal, downward_only = round(deps_center - card["height"] / 2), False
    else:
        card["x"] = 0
        ideal, downward_only = CARD_ROW_Y, True

    left, right = card["x"], card["x"] + card["width"]
    taken = [
        (n["y"], n["y"] + n["height"]) for n in data.get("nodes", [])
        if n is not card and n["x"] < right and left < n["x"] + n["width"]
    ]
    for e in dependency_edges(data):
        source, target = by_id[e["fromNode"]], by_id[e["toNode"]]
        if source["x"] + source["width"] < left and right < target["x"]:
            ys = _arrow_ys_within(source, target, left - _ARROW_CLEARANCE, right + _ARROW_CLEARANCE)
            if ys:
                taken.append((min(ys) - _ARROW_CLEARANCE, max(ys) + _ARROW_CLEARANCE))
    card["y"] = _free_y_near(taken, card["height"], ideal, downward_only)


def _free_y_near(taken: list[tuple[float, float]], height: int, ideal: int, downward_only: bool) -> int:
    """Nearest y to `ideal` where the card clears every taken (top, bottom) span in its column."""
    def fits(y: float) -> bool:
        return not any(y < bottom + CARD_Y_GAP and top - CARD_Y_GAP < y + height for top, bottom in taken)

    # The nearest free spot is either the ideal one or flush against something already there.
    candidates = [ideal] + [bottom + CARD_Y_GAP for _, bottom in taken]
    if downward_only:
        candidates = [y for y in candidates if y >= ideal]
    else:
        candidates += [top - CARD_Y_GAP - height for top, _ in taken]
    return math.ceil(min((y for y in candidates if fits(y)), key=lambda y: (abs(y - ideal), y)))


def dependency_edges(data: dict) -> list[dict]:
    ids = {c["id"] for c in cards(data)}
    return [e for e in data.get("edges", []) if e["fromNode"] in ids and e["toNode"] in ids]


def normalize_edge_sides(data: dict) -> None:
    for e in dependency_edges(data):
        e["fromSide"] = EDGE_FROM_SIDE
        e["toSide"] = EDGE_TO_SIDE


def prune_redundant_edges(data: dict) -> list[tuple[str, str]]:
    """Removes arrows already implied by a longer path (transitive reduction); returns them as (from, to)."""
    dep_edges = dependency_edges(data)
    children: dict[str, set[str]] = defaultdict(set)
    parents: dict[str, list[str]] = defaultdict(list)
    for e in dep_edges:
        children[e["fromNode"]].add(e["toNode"])
        parents[e["toNode"]].append(e["fromNode"])

    descendants: dict[str, set[str]] = {}
    ids = {cid for e in dep_edges for cid in (e["fromNode"], e["toNode"])}
    for cid in reversed(_topological_order(ids, children, parents)):
        reached: set[str] = set()
        for child in children[cid]:
            reached.add(child)
            reached |= descendants[child]
        descendants[cid] = reached

    redundant = [
        e for e in dep_edges
        if any(e["toNode"] in descendants[w] for w in children[e["fromNode"]] if w != e["toNode"])
    ]
    removed = {id(e) for e in redundant}
    data["edges"] = [e for e in data.get("edges", []) if id(e) not in removed]
    return [(e["fromNode"], e["toNode"]) for e in redundant]


@dataclass
class ArrangeSummary:
    columns: int
    unconnected: int
    set_aside_done: int
    pruned: list[tuple[str, str]]


def arrange(data: dict, set_aside_done: bool = False, prune_redundant: bool = False) -> ArrangeSummary:
    """Re-lays out every card (the one operation allowed to move existing cards -- only on explicit request)."""
    pruned = prune_redundant_edges(data) if prune_redundant else []
    by_id = {c["id"]: c for c in cards(data)}
    edges = [(e["fromNode"], e["toNode"]) for e in dependency_edges(data)]
    connected = {cid for edge in edges for cid in edge}

    done: set[str] = set()
    if set_aside_done:
        dependents = defaultdict(list)
        for u, v in edges:
            dependents[u].append(v)
        done = {
            cid for cid, c in by_id.items()
            if c["color"] == APROBADA and all(by_id[d]["color"] == APROBADA for d in dependents[cid])
        }
    loose_open = [c for cid, c in by_id.items() if cid not in connected and cid not in done]
    loose_done = [c for cid, c in by_id.items() if cid not in connected and cid in done]
    active = connected - done
    done_lane = connected & done

    nodes = [n for n in data.get("nodes", []) if not str(n.get("id", "")).startswith(GROUP_ID_PREFIX)]
    column = _assign_columns(connected, edges)
    board_columns = max(column.values(), default=-1) + 1
    board_right = max(board_columns, 1) * CARD_X_STEP - COLUMN_GAP
    groups = []

    legend = next((n for n in nodes if n.get("id") == LEGEND_ID), None)
    top = CARD_ROW_Y if legend is None else max(CARD_ROW_Y, legend["y"] + legend["height"] + _BLOCK_GAP)

    if loose_open:
        gx = legend["x"] + legend["width"] + _BLOCK_GAP if legend else 0
        gy = legend["y"] if legend else top
        _place_grid(_by_status(loose_open), gx + _GROUP_PADDING, gy + _GROUP_PADDING,
                    board_right - gx - 2 * _GROUP_PADDING)
        group = _group_around(UNCONNECTED_GROUP_ID, UNCONNECTED_GROUP_LABEL, loose_open)
        groups.append(group)
        top = max(top, group["y"] + group["height"] + _BLOCK_GAP)

    # Arrows between the two lanes get shortest when their ends face each other across the gap.
    across_active: dict[str, int] = defaultdict(int)
    across_done: dict[str, int] = defaultdict(int)
    for u, v in edges:
        if (u in active and v in done_lane) or (u in done_lane and v in active):
            across_active[u if u in active else v] += 1
            across_done[v if u in active else u] += 1

    lanes: list[tuple[set[str], dict[str, float]]] = []
    if active:
        lanes.append((active, _layout_lane(active, edges, column, by_id, across_active, _DOWN)))
    if done_lane:
        lanes.append((done_lane, _layout_lane(done_lane, edges, column, by_id, across_done, _UP)))
    for cid in connected:
        by_id[cid]["x"] = column[cid] * CARD_X_STEP

    def stack() -> int:
        y = top
        if active:
            y = _put_lane(lanes[0], by_id, y) + _BLOCK_GAP
        if done_lane or loose_done:
            y += _GROUP_PADDING
            if done_lane:
                y = _put_lane(lanes[-1], by_id, y) + 2 * CARD_Y_GAP
        return y

    _clear_arrow_paths(lanes, edges, column, by_id, stack)
    below_done_lane = stack()
    if loose_done:
        _place_grid(_by_status(loose_done), 0, below_done_lane, board_right)
    if done_lane or loose_done:
        groups.append(_group_around(DONE_GROUP_ID, DONE_GROUP_LABEL, [by_id[c] for c in done_lane] + loose_done))

    # JSON Canvas draws nodes in array order, so groups go first to stay behind the cards.
    data["nodes"] = groups + nodes
    normalize_edge_sides(data)
    return ArrangeSummary(
        columns=board_columns, unconnected=len(loose_open), set_aside_done=len(done), pruned=pruned,
    )


def _topological_order(ids: set[str], children: dict, parents: dict) -> list[str]:
    pending = {cid: len(parents[cid]) for cid in ids}
    queue = sorted(cid for cid, n in pending.items() if n == 0)
    topo = []
    while queue:
        cid = queue.pop()
        topo.append(cid)
        for child in sorted(children[cid]):
            pending[child] -= 1
            if pending[child] == 0:
                queue.append(child)
    return topo


def _assign_columns(ids: set[str], edges: list[tuple[str, str]]) -> dict[str, int]:
    parents: dict[str, list[str]] = defaultdict(list)
    children: dict[str, list[str]] = defaultdict(list)
    for u, v in edges:
        children[u].append(v)
        parents[v].append(u)
    topo = _topological_order(ids, children, parents)

    column = {}
    for cid in topo:
        column[cid] = max((column[p] + 1 for p in parents[cid]), default=0)

    # Slide each card toward whichever side has more arrows, as far as its dependencies allow:
    # every slide strictly shortens the total arrow length, so this always terminates.
    moved = True
    while moved:
        moved = False
        for cid in topo:
            pull = len(children[cid]) - len(parents[cid])
            if pull > 0 and children[cid]:
                target = min(column[c] for c in children[cid]) - 1
            elif pull < 0:
                target = max(column[p] for p in parents[cid]) + 1
            else:
                continue
            if target != column[cid]:
                column[cid] = target
                moved = True

    low = min(column.values(), default=0)
    return {cid: col - low for cid, col in column.items()}


def _layout_lane(
    ids: set[str], edges: list[tuple[str, str]], column: dict[str, int], by_id: dict[str, dict],
    pulls: dict[str, int], direction: int,
) -> dict[str, float]:
    """Lane-relative y per card. `pulls` counts each card's arrows to the other lane, which sits in `direction`."""
    lane_edges = [(u, v) for u, v in edges if u in ids and v in ids]
    order = _order_within_columns(ids, lane_edges, column, by_id, pulls, direction)
    return _align_vertically(order, lane_edges, column, by_id, pulls, direction)


def _put_lane(lane: tuple[set[str], dict[str, float]], by_id: dict[str, dict], top: int) -> int:
    ids, relative = lane
    bottom = top
    for cid in ids:
        card = by_id[cid]
        card["y"] = round(top + relative[cid])
        bottom = max(bottom, card["y"] + card["height"])
    return bottom


def _clear_arrow_paths(
    lanes: list[tuple[set[str], dict[str, float]]], edges: list[tuple[str, str]], column: dict[str, int],
    by_id: dict[str, dict], stack: Callable[[], int],
) -> None:
    """An arrow drawn across a card reads as if it connected to it, so push cards off such arrows' paths."""
    lane_of = {cid: i for i, (ids, _) in enumerate(lanes) for cid in ids}
    by_column: dict[int, list[str]] = defaultdict(list)
    for cid in lane_of:
        by_column[column[cid]].append(cid)

    best: Optional[tuple[int, list[dict[str, float]]]] = None
    for attempt in range(_CLEARING_ROUNDS + 1):
        stack()
        bands = _arrow_bands(edges, column, by_id)
        hits = sum(
            1 for col, band_list in bands.items() for cid in by_column[col]
            if any(_overlaps(by_id[cid], band) for band in band_list)
        )
        if best is None or hits < best[0]:
            best = (hits, [dict(relative) for _, relative in lanes])
        if hits == 0 or attempt == _CLEARING_ROUNDS:
            break
        for col, band_list in bands.items():
            members = by_column[col]
            merge_gap = max((by_id[c]["height"] for c in members), default=0) + 2 * CARD_Y_GAP
            merged = _merge_bands(band_list, merge_gap)
            for i, (_, relative) in enumerate(lanes):
                lane_members = [c for c in members if lane_of[c] == i]
                if not lane_members:
                    continue
                lane_top = by_id[lane_members[0]]["y"] - relative[lane_members[0]]
                items = [(by_id[c]["y"], by_id[c]["height"], 1.0, c) for c in lane_members]
                items += [(band_top, band_bottom - band_top, _BAND_WEIGHT, None) for band_top, band_bottom in merged]
                items.sort(key=lambda item: item[0] + item[1] / 2)
                placed = _pack_in_order([it[0] for it in items], [it[2] for it in items], [it[1] for it in items])
                for (_, _, _, cid), y in zip(items, placed):
                    if cid is not None:
                        relative[cid] = y - lane_top
        for _, relative in lanes:
            low = min(relative.values())
            for cid in relative:
                relative[cid] -= low

    for (_, relative), saved in zip(lanes, best[1]):
        relative.update(saved)


def _arrow_bands(edges: list[tuple[str, str]], column: dict[str, int], by_id: dict[str, dict]) -> dict[int, list[tuple[float, float]]]:
    """Vertical span each arrow covers while crossing the columns it skips over."""
    bands: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for u, v in edges:
        if column[v] - column[u] < 2:
            continue
        for col in range(column[u] + 1, column[v]):
            left = col * CARD_X_STEP - _ARROW_CLEARANCE
            right = col * CARD_X_STEP + CARD_WIDTH + _ARROW_CLEARANCE
            ys = _arrow_ys_within(by_id[u], by_id[v], left, right)
            if ys:
                bands[col].append((min(ys) - _ARROW_CLEARANCE, max(ys) + _ARROW_CLEARANCE))
    return bands


def _arrow_ys_within(source: dict, target: dict, left: float, right: float) -> list[float]:
    return _ys_within(_arrow_points(source, target), left, right)


def _arrow_points(source: dict, target: dict) -> list[tuple[float, float]]:
    """The right-to-left bezier Obsidian (1.13) draws between two cards, sampled."""
    x1, y1 = source["x"] + source["width"] + _ARROW_END_OFFSET, source["y"] + source["height"] / 2
    x2, y2 = target["x"] - _ARROW_END_OFFSET, target["y"] + target["height"] / 2
    handle = min(max(math.dist((x1, y1), (x2, y2)) / 2, _ARROW_HANDLE_MIN), _ARROW_HANDLE_MAX)
    c1, c2 = x1 + handle, x2 - handle
    points = []
    for i in range(_CURVE_SAMPLES + 1):
        t = i / _CURVE_SAMPLES
        s = 1 - t
        points.append((
            s ** 3 * x1 + 3 * s * s * t * c1 + 3 * s * t * t * c2 + t ** 3 * x2,
            s ** 3 * y1 + 3 * s * s * t * y1 + 3 * s * t * t * y2 + t ** 3 * y2,
        ))
    return points


def _ys_within(points: list[tuple[float, float]], left: float, right: float) -> list[float]:
    ys = []
    for (xa, ya), (xb, yb) in zip(points, points[1:]):
        lo, hi = max(min(xa, xb), left), min(max(xa, xb), right)
        if lo > hi:
            continue
        for x in (lo, hi):
            t = 0.0 if xb == xa else (x - xa) / (xb - xa)
            ys.append(ya + (yb - ya) * t)
    return ys


def _merge_bands(bands: list[tuple[float, float]], merge_gap: float) -> list[tuple[float, float]]:
    """Bands closer than a card can fit between are merged, so no card gets squeezed into the gap."""
    merged: list[tuple[float, float]] = []
    for top, bottom in sorted(bands):
        if merged and top - merged[-1][1] < merge_gap:
            merged[-1] = (merged[-1][0], max(merged[-1][1], bottom))
        else:
            merged.append((top, bottom))
    return merged


def _overlaps(card: dict, band: tuple[float, float]) -> bool:
    return card["y"] < band[1] and band[0] < card["y"] + card["height"]


def _order_within_columns(
    ids: set[str], lane_edges: list[tuple[str, str]], column: dict[str, int], by_id: dict[str, dict],
    pulls: dict[str, int], direction: int,
) -> list[list[str]]:
    """Barycenter heuristic (Sugiyama), with placeholder nodes where an arrow skips columns."""
    layers: dict[int, list] = defaultdict(list)
    for cid in sorted(ids, key=lambda c: (by_id[c]["y"], by_id[c]["x"], c)):
        layers[column[cid]].append(cid)
    up: dict = defaultdict(list)
    down: dict = defaultdict(list)
    for i, (u, v) in enumerate(lane_edges):
        prev = u
        for col in range(column[u] + 1, column[v]):
            placeholder = (i, col)
            layers[col].append(placeholder)
            down[prev].append(placeholder)
            up[placeholder].append(prev)
            prev = placeholder
        down[prev].append(v)
        up[v].append(prev)

    order = [layers[c] for c in sorted(layers)]
    index: dict = {}

    def reindex(layer: list) -> None:
        for i, n in enumerate(layer):
            index[n] = i

    def crossings() -> int:
        total = 0
        for layer in order:
            segments = [(index[u], index[v]) for u in layer for v in down[u]]
            for i, (a1, b1) in enumerate(segments):
                for a2, b2 in segments[i + 1:]:
                    if (a1 - a2) * (b1 - b2) < 0:
                        total += 1
        return total

    def barycenter(n, neighbors: dict, layer_size: int) -> tuple[float, int]:
        positions = [index[m] for m in neighbors[n]]
        positions += [-1 if direction == _UP else layer_size] * pulls.get(n, 0)
        return (_mean(positions) if positions else index[n], index[n])

    for layer in order:
        reindex(layer)
    best = (crossings(), [list(layer) for layer in order])
    for sweep in range(_ORDERING_SWEEPS):
        downward = sweep % 2 == 0
        neighbors = up if downward else down
        for layer in (order[1:] if downward else order[-2::-1]):
            layer.sort(key=lambda n: barycenter(n, neighbors, len(layer)))
            reindex(layer)
        count = crossings()
        if count < best[0]:
            best = (count, [list(layer) for layer in order])
    return [[n for n in layer if isinstance(n, str)] for layer in best[1]]


def _align_vertically(
    order: list[list[str]], lane_edges: list[tuple[str, str]], column: dict[str, int], by_id: dict[str, dict],
    pulls: dict[str, int], direction: int,
) -> dict[str, float]:
    """Each card drifts toward its neighbors' height, while making room for arrows that cross its column."""
    neighbors: dict[str, list[tuple[str, float]]] = defaultdict(list)
    long_edges = []
    for u, v in lane_edges:
        skips = column[v] - column[u] >= 2
        if skips:
            long_edges.append((u, v))
        # Heavier on arrows that skip columns: a level arrow only needs a thin free corridor.
        weight = _LONG_ARROW_WEIGHT if skips else 1.0
        neighbors[u].append((v, weight))
        neighbors[v].append((u, weight))

    y: dict[str, float] = {}
    for layer in order:
        cursor = 0.0
        for cid in layer:
            y[cid] = cursor
            cursor += by_id[cid]["height"] + CARD_Y_GAP
    # Fixed (not recomputed per iteration) so pulled cards can't drag the lane out indefinitely.
    stacked_bottom = max((y[c] + by_id[c]["height"] for c in y), default=0)
    other_lane = -_BLOCK_GAP if direction == _UP else stacked_bottom + _BLOCK_GAP

    def box(cid: str) -> dict:
        card = by_id[cid]
        return {"x": column[cid] * CARD_X_STEP, "y": y[cid], "width": card["width"], "height": card["height"]}

    for iteration in range(_ALIGN_ITERATIONS):
        # Corridors only once the lane has settled, so they carve room out of a compact layout.
        with_corridors = iteration >= _ALIGN_ITERATIONS - _CORRIDOR_ITERATIONS
        for layer in (order if iteration % 2 == 0 else order[::-1]):
            if not layer:
                continue
            col = column[layer[0]]
            items = []  # (current top, height, target top, weight, card id or None for an arrow corridor)
            for cid in layer:
                height = by_id[cid]["height"]
                total = float(pulls.get(cid, 0))
                weighted = total * other_lane
                for other, weight in neighbors[cid]:
                    weighted += weight * (y[other] + by_id[other]["height"] / 2)
                    total += weight
                target = weighted / total - height / 2 if total else y[cid]
                items.append((y[cid], height, target, total or 1.0, cid))
            corridors = []
            for u, v in (long_edges if with_corridors else []):
                if column[u] < col < column[v]:
                    ys = _arrow_ys_within(box(u), box(v), col * CARD_X_STEP - _ARROW_CLEARANCE,
                                          col * CARD_X_STEP + CARD_WIDTH + _ARROW_CLEARANCE)
                    if ys:
                        corridors.append((min(ys) - _ARROW_CLEARANCE, max(ys) + _ARROW_CLEARANCE))
            merge_gap = max(by_id[c]["height"] for c in layer) + 2 * CARD_Y_GAP
            for top, bottom in _merge_bands(corridors, merge_gap):
                items.append((top, bottom - top, top, _BAND_WEIGHT, None))
            items.sort(key=lambda item: item[0] + item[1] / 2)
            # A corridor already carries its own clearance, so it doesn't need the full card-to-card gap.
            gaps = [
                CARD_Y_GAP if a[4] is not None and b[4] is not None else _CORRIDOR_GAP
                for a, b in zip(items, items[1:])
            ] + [0.0]
            placed = _pack_in_order(
                [it[2] for it in items], [it[3] for it in items], [it[1] for it in items], gaps,
            )
            for item, value in zip(items, placed):
                if item[4] is not None:
                    y[item[4]] = value

    low = min(y.values(), default=0)
    return {cid: value - low for cid, value in y.items()}


def _pack_in_order(
    targets: list[float], weights: list[float], heights: list[float], gaps: Optional[list[float]] = None,
) -> list[float]:
    """Closest positions to `targets` (weighted least squares) keeping order, with gaps[i] after item i -- isotonic regression."""
    offsets, cursor = [], 0.0
    for i, h in enumerate(heights):
        offsets.append(cursor)
        cursor += h + (gaps[i] if gaps else CARD_Y_GAP)
    blocks: list[list[float]] = []  # [weighted sum, total weight, count]
    for target, offset, weight in zip(targets, offsets, weights):
        blocks.append([(target - offset) * weight, weight, 1])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
            s, w, n = blocks.pop()
            blocks[-1][0] += s
            blocks[-1][1] += w
            blocks[-1][2] += n
    shifts = [s / w for s, w, n in blocks for _ in range(int(n))]
    return [shift + offset for shift, offset in zip(shifts, offsets)]


def _place_grid(members: list[dict], x0: int, y0: int, available_width: int) -> None:
    # No arrows run between these cards, so the grid can be much tighter than the dependency columns.
    step = max(c["width"] for c in members) + _GRID_GAP
    per_row = max(3, (available_width + _GRID_GAP) // step)
    y = y0
    for start in range(0, len(members), per_row):
        row = members[start:start + per_row]
        for i, card in enumerate(row):
            card["x"] = x0 + i * step
            card["y"] = y
        y += max(c["height"] for c in row) + CARD_Y_GAP


def _by_status(members: list[dict]) -> list[dict]:
    rank = {color: i for i, color in enumerate(_GRID_STATUS_ORDER)}
    return sorted(members, key=lambda c: (rank.get(c["color"], len(rank)), c["id"]))


def _group_around(group_id: str, label: str, members: list[dict]) -> dict:
    x1 = min(c["x"] for c in members) - _GROUP_PADDING
    y1 = min(c["y"] for c in members) - _GROUP_PADDING
    x2 = max(c["x"] + c["width"] for c in members) + _GROUP_PADDING
    y2 = max(c["y"] + c["height"] for c in members) + _GROUP_PADDING
    return {"id": group_id, "type": "group", "x": x1, "y": y1, "width": x2 - x1, "height": y2 - y1, "label": label}


def _mean(values: Iterable[float]) -> float:
    values = list(values)
    return sum(values) / len(values)


def build_legend_node() -> dict:
    """Nodo de texto decorativo con la leyenda de colores. Ignorado por toda la logica
    de estado/dependencias (que filtra por type=='file'); solo sirve para que la leyenda
    sea visible directamente en el canvas al abrirlo en Obsidian.
    """
    return {
        "id": LEGEND_ID,
        "type": "text",
        "x": 0,
        "y": -320,
        "width": 520,
        "height": 260,
        "text": _LEGEND_TEXT,
    }


def add_edge(data: dict, from_id: str, to_id: str) -> None:
    edge_id = f"{from_id}->{to_id}"
    edges = data.setdefault("edges", [])
    if any(e["fromNode"] == from_id and e["toNode"] == to_id for e in edges):
        return
    edges.append({
        "id": edge_id, "fromNode": from_id, "toNode": to_id,
        "fromSide": EDGE_FROM_SIDE, "toSide": EDGE_TO_SIDE,
    })


def detect_cycle(data: dict) -> Optional[list[str]]:
    """DFS con pila de recursion. Devuelve la lista de ids del ciclo (cerrado, primero==ultimo) o None."""
    adjacency: dict[str, list[str]] = {}
    for e in data.get("edges", []):
        adjacency.setdefault(e["fromNode"], []).append(e["toNode"])

    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n["id"]: WHITE for n in data.get("nodes", [])}
    parent: dict[str, str] = {}
    cycle: Optional[list[str]] = None

    def dfs(u: str) -> None:
        nonlocal cycle
        color[u] = GRAY
        for v in adjacency.get(u, []):
            if cycle:
                return
            if color.get(v, WHITE) == WHITE:
                parent[v] = u
                dfs(v)
            elif color.get(v) == GRAY:
                path = [v]
                cur = u
                while cur != v:
                    path.append(cur)
                    cur = parent[cur]
                path.append(v)
                cycle = list(reversed(path))
        color[u] = BLACK

    for node_id in list(color.keys()):
        if cycle:
            break
        if color[node_id] == WHITE:
            dfs(node_id)
    return cycle


def recompute_blocked(data: dict) -> bool:
    """Bloqueada es un estado derivado del grafo (docs/canvas-schema.md): una tarjeta
    esta Bloqueada si le falta alguna dependencia por Aprobar, sea cual sea su estado
    previo -- incluida una ya asignada (En progreso), p.ej. tras 'histos link' anadirle
    una dependencia nueva sin aprobar todavia.
    Backlog/Bloqueada alternan libremente en las dos direcciones. En progreso solo se
    degrada a Bloqueada; nunca se auto-restaura desde aqui (una tarjeta que se
    desbloquea aterriza en Backlog, hace falta 'assign' de nuevo para retomarla).
    Propuesta/Solicitud/Aprobada no se tocan: son estados con una accion humana de por
    medio, no derivables solo del grafo.
    Devuelve True si cambio algo.
    """
    changed = False
    by_id = {c["id"]: c for c in cards(data)}
    for card in cards(data):
        color = card.get("color")
        if color not in _DEMOTABLE_TO_BLOQUEADA:
            continue
        deps = incoming_edges(data, card["id"])
        all_approved = all(
            by_id.get(e["fromNode"], {}).get("color") == APROBADA for e in deps
        )
        if not all_approved:
            target = BLOQUEADA
        elif color in _FREELY_DERIVED_COLORS:
            target = BACKLOG
        else:
            target = color
        if color != target:
            card["color"] = target
            changed = True
    return changed
