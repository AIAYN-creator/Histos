"""Lectura/escritura del frontmatter YAML de las tarjetas -- ver docs/canvas-schema.md."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import yaml

from .fsutil import write_text_atomic

_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)

DEFAULT_META = {
    "description": None,
    "sources": [],
    "estimated_duration_hours": None,
    "actual_duration_hours": None,
    "assigned_to": None,
    "status_note": None,
}


# Fingerprint of the body as Histos last wrote it legitimately (add-card, approve, accept-edit).
# The content_untouched eval compares against it to catch direct writes to content/.
CONTENT_HASH_KEY = "content_hash"


def body_hash(body: str) -> str:
    return hashlib.sha256(body.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def read(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    meta = yaml.safe_load(m.group(1)) or {}
    return meta, text[m.end():]


def write(path: Path, meta: dict, body: str) -> None:
    yaml_block = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True)
    write_text_atomic(path, f"---\n{yaml_block}---\n{body}")
