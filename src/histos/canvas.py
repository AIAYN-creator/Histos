"""Carga, mutacion y guardado del project.canvas -- ver docs/canvas-schema.md."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

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
CARD_X_STEP = 340
CARD_Y_GAP = 40
_CHARS_PER_LINE = 30
_LINE_HEIGHT = 28

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
    n = len(cards(data))
    node = {
        "id": card_id,
        "type": "file",
        "x": CARD_X_STEP * n,
        "y": CARD_ROW_Y,
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


def compute_ranks(data: dict) -> dict[str, int]:
    """Rango de cada tarjeta = camino mas largo desde una raiz (tarjeta sin dependencias).
    Asume el grafo aciclico -- ya se comprueba en cada mutacion de edges antes de llegar aqui.
    """
    by_id = {c["id"]: c for c in cards(data)}
    incoming: dict[str, list[str]] = {cid: [] for cid in by_id}
    for e in data.get("edges", []):
        if e["fromNode"] in by_id and e["toNode"] in by_id:
            incoming[e["toNode"]].append(e["fromNode"])

    rank: dict[str, int] = {}

    def resolve(cid: str) -> int:
        if cid in rank:
            return rank[cid]
        deps = incoming[cid]
        rank[cid] = 0 if not deps else 1 + max(resolve(d) for d in deps)
        return rank[cid]

    for cid in by_id:
        resolve(cid)
    return rank


def _rects_overlap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def place_new_card(data: dict, card: dict) -> None:
    """Positions a single newly created card and nothing else: x by dependency rank (same
    left-to-right column reading as before), y found by scanning down for the first spot
    that doesn't overlap any other node already on the board (cards, groups, the legend).

    Deliberately never touches any other node's x/y/width/height. Once a card has a
    position, the CLI never moves or resizes it again -- only the user, by hand in
    Obsidian, does. This means the rank-based columns are only a placement heuristic for
    where a *new* card lands, not an invariant kept forever: after cards get dragged
    around, or after a later 'link' changes what a card's rank would have been, the board
    is no longer a clean grid. That's the accepted trade-off for never moving a card out
    from under the user.
    """
    rank = compute_ranks(data).get(card["id"], 0)
    x = rank * CARD_X_STEP
    others = [n for n in data.get("nodes", []) if n is not card]

    y = CARD_ROW_Y
    while True:
        blocking = [
            n for n in others
            if _rects_overlap((x, y, card["width"], card["height"]), (n["x"], n["y"], n["width"], n["height"]))
        ]
        if not blocking:
            break
        y = max(n["y"] + n["height"] for n in blocking) + CARD_Y_GAP

    card["x"] = x
    card["y"] = y


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
    edges.append({"id": edge_id, "fromNode": from_id, "toNode": to_id})


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
