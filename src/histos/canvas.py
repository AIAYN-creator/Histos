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
_LEGEND_TEXT = """## Leyenda de estados (Histos)

| Color | Estado |
|---|---|
| \U0001F7E3 morado | Backlog |
| \U0001F7E0 naranja | En progreso |
| \U0001F534 rojo | Bloqueada (derivado, no se asigna a mano) |
| \U0001F7E1 amarillo | Propuesta pendiente de revision |
| \U0001F535 cian | Solicitud cambio de dependencia |
| \U0001F7E2 verde | Aprobada |

Detalle: docs/canvas-schema.md en el repo de Histos.
"""


class HistosError(Exception):
    """Errores esperables (vault no encontrado, id duplicado, etc.) -- mensaje ya listo para el usuario."""


def vault_canvas_path(vault_root: Path) -> Path:
    return vault_root / CANVAS_FILENAME


def load(vault_root: Path) -> dict:
    path = vault_canvas_path(vault_root)
    if not path.exists():
        raise HistosError(
            f"no encuentro {CANVAS_FILENAME} en {vault_root} -- ejecuta 'histos init' primero"
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
    las columnas del relayout queden alineadas). Aproximado -- Obsidian decide el wrap real --
    pero muchisimo mejor que un 250x100 fijo para toda tarjeta sea cual sea su contenido.
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


def relayout(data: dict) -> None:
    """Reposiciona todas las tarjetas: columna = rango de dependencia, apiladas en vertical
    dentro de cada columna (usa el width/height que ya tenga cada una). No toca group/text.
    """
    ranks = compute_ranks(data)
    by_rank: dict[int, list[dict]] = {}
    for card in cards(data):
        by_rank.setdefault(ranks[card["id"]], []).append(card)

    for r, group in by_rank.items():
        y = CARD_ROW_Y
        for card in sorted(group, key=lambda c: c["id"]):
            card["x"] = r * CARD_X_STEP
            card["y"] = y
            y += card["height"] + CARD_Y_GAP


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
