"""Carga, mutacion y guardado del project.canvas -- ver docs/canvas-schema.md."""
from __future__ import annotations

import json
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

# Solo estos dos colores se recalculan automaticamente en funcion del grafo;
# el resto son estados activos que solo cambian por accion explicita.
_DERIVED_COLORS = {BLOQUEADA, BACKLOG}

CARD_WIDTH = 250
CARD_HEIGHT = 100
CARD_ROW_Y = 200
CARD_X_STEP = 320


class TrellisError(Exception):
    """Errores esperables (vault no encontrado, id duplicado, etc.) -- mensaje ya listo para el usuario."""


def vault_canvas_path(vault_root: Path) -> Path:
    return vault_root / CANVAS_FILENAME


def load(vault_root: Path) -> dict:
    path = vault_canvas_path(vault_root)
    if not path.exists():
        raise TrellisError(
            f"no encuentro {CANVAS_FILENAME} en {vault_root} -- ejecuta 'trellis init' primero"
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


def add_card_node(data: dict, card_id: str, color: str) -> dict:
    n = len(cards(data))
    node = {
        "id": card_id,
        "type": "file",
        "x": CARD_X_STEP * n,
        "y": CARD_ROW_Y,
        "width": CARD_WIDTH,
        "height": CARD_HEIGHT,
        "file": f"content/{card_id}.md",
        "color": color,
    }
    data.setdefault("nodes", []).append(node)
    return node


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
    """Cards no arrancadas (Bloqueada/Backlog): pasan a Bloqueada si les falta
    alguna dependencia por Aprobar, o a Backlog si ya pueden empezar.
    Cards en un estado activo (En progreso/Propuesta/Solicitud/Aprobada) no se tocan.
    Devuelve True si cambio algo.
    """
    changed = False
    by_id = {c["id"]: c for c in cards(data)}
    for card in cards(data):
        if card.get("color") not in _DERIVED_COLORS:
            continue
        deps = incoming_edges(data, card["id"])
        all_approved = all(
            by_id.get(e["fromNode"], {}).get("color") == APROBADA for e in deps
        )
        target = BACKLOG if all_approved else BLOQUEADA
        if card.get("color") != target:
            card["color"] = target
            changed = True
    return changed
