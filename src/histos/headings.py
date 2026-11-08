"""Markdown headings, compared the way a human reads them: '## Qué no es' and 'que NO es' are the
same heading -- level, case, spacing and accents don't count. Used by the evals' heading checks
and by the decisions log, which looks for PROJECT.md's decisions section.
"""
from __future__ import annotations

import re
import unicodedata

HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")


def normalize(text: str) -> str:
    text = " ".join(text.strip().lstrip("#").split()).lower()
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def alternatives(heading: str | list[str]) -> list[str]:
    """A heading is a text, or a list of acceptable spellings (e.g. one per language)."""
    return [heading] if isinstance(heading, str) else list(heading)


def parse(line: str) -> tuple[int, str] | None:
    """(level, normalized text) if the line is a heading."""
    m = HEADING_RE.match(line)
    return (len(m.group(1)), normalize(m.group(2))) if m else None


def present(text: str) -> set[str]:
    return {h[1] for line in text.splitlines() if (h := parse(line))}


def section_span(lines: list[str], heading: str | list[str]) -> tuple[int, int] | None:
    """(index of the heading line, index where the section ends -- the next heading of the same
    or a higher level, or len(lines)) for the first heading matching any alternative."""
    wanted = {normalize(alt) for alt in alternatives(heading)}
    for n, line in enumerate(lines):
        h = parse(line)
        if not h or h[1] not in wanted:
            continue
        level = h[0]
        for end in range(n + 1, len(lines)):
            following = parse(lines[end])
            if following and following[0] <= level:
                return n, end
        return n, len(lines)
    return None
