"""Full validation of a vault: the canvas schema plus what a schema can't see on its own
(edges pointing to existing nodes, an acyclic graph), and the cards' frontmatter.
See docs/canvas-schema.md.
"""
from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

from jsonschema import Draft202012Validator

from . import canvas, frontmatter

_SCHEMA_CACHE: dict[str, dict] = {}


def _load_named_schema(filename: str) -> dict:
    if filename not in _SCHEMA_CACHE:
        ref = resources.files("histos").joinpath("schema", filename)
        _SCHEMA_CACHE[filename] = json.loads(ref.read_text(encoding="utf-8"))
    return _SCHEMA_CACHE[filename]


def load_schema() -> dict:
    return _load_named_schema("histos-canvas.schema.json")


def validate_schema(data: dict) -> list[str]:
    validator = Draft202012Validator(load_schema())
    errors = []
    for e in validator.iter_errors(data):
        where = "/".join(str(p) for p in e.path) or "(root)"
        errors.append(f"schema: {where}: {e.message}")
    return errors


def validate_references(data: dict) -> list[str]:
    node_ids = {n["id"] for n in data.get("nodes", [])}
    errors = []
    for e in data.get("edges", []):
        for key in ("fromNode", "toNode"):
            if e.get(key) not in node_ids:
                errors.append(
                    f"reference: edge {e.get('id')} points to {key}={e.get(key)!r}, no such node id exists"
                )
    return errors


def validate_acyclic(data: dict) -> list[str]:
    cycle = canvas.detect_cycle(data)
    if cycle:
        return [f"cycle: circular dependency between {' -> '.join(cycle)}"]
    return []


def validate_frontmatter(vault_root: Path, data: dict) -> list[str]:
    """Field types in each card's frontmatter. Reported by `histos validate` only -- unlike a
    broken canvas, one odd card never stops the other commands."""
    validator = Draft202012Validator(_load_named_schema("histos-frontmatter.schema.json"))
    errors = []
    for card in canvas.cards(data):
        try:
            md_path = canvas.card_file_path(vault_root, card)
            if not md_path.exists():
                errors.append(f"frontmatter: {card['id']}: its file {card['file']} doesn't exist")
                continue
            meta, _ = frontmatter.read(md_path)
        except canvas.HistosError as e:
            errors.append(f"frontmatter: {card['id']}: {e}")
            continue
        for e in validator.iter_errors(meta):
            where = "/".join(str(p) for p in e.path) or "(root)"
            errors.append(f"frontmatter: {card['id']}: {where}: {e.message}")
    return errors


def validate_all(data: dict) -> list[str]:
    errors = validate_schema(data)
    # the graph checks assume edges and ids are already well formed
    if not errors:
        errors += validate_references(data)
        errors += validate_acyclic(data)
    return errors
