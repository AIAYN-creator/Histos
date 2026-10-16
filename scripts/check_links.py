"""Checks every relative link in the README and docs/: the target file exists and, for a
#fragment, the heading exists too (GitHub's anchor rules). External http(s) links are skipped.

    python scripts/check_links.py          # prints the broken ones, exits 1 if there are any
"""
from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINK_RE = re.compile(r"\]\(([^)\s]+)\)|(?:src|srcset|href)=\"([^\"]+)\"")
HEADING_RE = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$", re.M)
FENCE_RE = re.compile(r"^```.*?^```", re.M | re.S)


def markdown_files() -> list[Path]:
    return [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]


def anchor(heading: str) -> str:
    """GitHub's slug: lowercase, drop punctuation except - and _, spaces to dashes."""
    text = re.sub(r"`|\*\*|\[([^\]]*)\]\([^)]*\)", r"\1", heading).strip().lower()
    text = "".join(c for c in text if c.isalnum() or c in " -_" or unicodedata.category(c).startswith("L"))
    return text.replace(" ", "-")


def anchors(path: Path) -> set[str]:
    text = FENCE_RE.sub("", path.read_text(encoding="utf-8"))
    return {anchor(h) for h in HEADING_RE.findall(text)}


def broken_links() -> list[str]:
    problems = []
    for md in markdown_files():
        text = FENCE_RE.sub("", md.read_text(encoding="utf-8"))
        for match in LINK_RE.finditer(text):
            target = match.group(1) or match.group(2)
            if re.match(r"[a-z]+:", target) or target.startswith("mailto:"):
                continue
            path_part, _, fragment = target.partition("#")
            dest = (md.parent / path_part).resolve() if path_part else md
            where = f"{md.relative_to(ROOT).as_posix()}: {target}"
            if not dest.exists():
                problems.append(f"{where} -> no such file")
            elif fragment and dest.suffix == ".md" and fragment not in anchors(dest):
                problems.append(f"{where} -> no heading '#{fragment}'")
    return problems


def main() -> int:
    problems = broken_links()
    for problem in problems:
        print(problem)
    print(f"{len(problems)} broken link(s)" if problems else "all links OK")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
