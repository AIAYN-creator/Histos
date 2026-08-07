"""Lectura/escritura del frontmatter YAML de las tarjetas -- ver docs/canvas-schema.md."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)

DEFAULT_META = {
    "description": None,
    "estimated_duration_hours": None,
    "actual_duration_hours": None,
    "assigned_to": None,
    "status_note": None,
}


def read(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    meta = yaml.safe_load(m.group(1)) or {}
    return meta, text[m.end():]


def write(path: Path, meta: dict, body: str) -> None:
    yaml_block = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True)
    path.write_text(f"---\n{yaml_block}---\n{body}", encoding="utf-8")
