"""Validacion completa de un .canvas: schema + lo que el schema no puede ver por si solo
(integridad referencial de las edges, aciclicidad del grafo). Ver docs/canvas-schema.md.
"""
from __future__ import annotations

import json
from importlib import resources

from jsonschema import Draft202012Validator

from . import canvas

_SCHEMA_CACHE: dict | None = None


def load_schema() -> dict:
    global _SCHEMA_CACHE
    if _SCHEMA_CACHE is None:
        ref = resources.files("trellis").joinpath("schema", "trellis-canvas.schema.json")
        _SCHEMA_CACHE = json.loads(ref.read_text(encoding="utf-8"))
    return _SCHEMA_CACHE


def validate_schema(data: dict) -> list[str]:
    validator = Draft202012Validator(load_schema())
    errors = []
    for e in validator.iter_errors(data):
        where = "/".join(str(p) for p in e.path) or "(raiz)"
        errors.append(f"schema: {where}: {e.message}")
    return errors


def validate_references(data: dict) -> list[str]:
    node_ids = {n["id"] for n in data.get("nodes", [])}
    errors = []
    for e in data.get("edges", []):
        for key in ("fromNode", "toNode"):
            if e.get(key) not in node_ids:
                errors.append(
                    f"referencia: edge {e.get('id')} apunta a {key}={e.get(key)!r}, no existe como id de nodo"
                )
    return errors


def validate_acyclic(data: dict) -> list[str]:
    cycle = canvas.detect_cycle(data)
    if cycle:
        return [f"ciclo: dependencia circular entre {' -> '.join(cycle)}"]
    return []


def validate_all(data: dict) -> list[str]:
    errors = validate_schema(data)
    # las comprobaciones de grafo asumen que los edges/ids ya tienen forma valida
    if not errors:
        errors += validate_references(data)
        errors += validate_acyclic(data)
    return errors
