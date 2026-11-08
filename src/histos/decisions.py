"""The decisions log: when the human rejects a plan or a guide change with feedback, that
feedback is kept as a decision, so no agent -- this one or a fresh one tomorrow -- proposes again
what the human already turned down, and why.

PROJECT.md is the human's file, so this is a narrow exception to "only `guide approve` writes it":
- only the human's own commands (`plan reject`, `guide reject`) call it, and only with the text
  the human wrote in --feedback -- Histos adds a dated line, it never words anything itself;
- it only ever appends one line at the end of the decisions section, touching nothing else;
- the version it replaces is kept in approved/guides/, like an approval does.

Where the line goes:
1. PROJECT.md's decisions section ("## Decisiones" / "## Decisions", any level);
2. also a pending PROJECT.md change that has the section -- otherwise approving it would wipe
   the decision just recorded;
3. if PROJECT.md doesn't exist or has no such section (vaults from before v3.1, or a first brief
   that got rejected): .histos/decisions.md, which `histos context` shows until the agent
   carries the decisions into PROJECT.md.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from . import guides, headings
from .fsutil import write_text_atomic

SECTION = ["## Decisiones", "## Decisions"]
FALLBACK_FILE = Path(".histos") / "decisions.md"
_FALLBACK_HEADER = "# Decisions not yet in PROJECT.md\n\n"


def record(vault_root: Path, what: str, feedback: str | None) -> str | None:
    """Records '<date> · <what>: <feedback>'. Returns where it went (for the event), or None
    when there's no feedback -- a rejection without a reason isn't a decision."""
    if not feedback or not feedback.strip():
        return None
    line = f"- {datetime.now().strftime('%Y-%m-%d')} · {what}: {' '.join(feedback.split())}"

    project = vault_root / "PROJECT.md"
    text = project.read_text(encoding="utf-8") if project.exists() else None
    updated = _append_to_section(text, line) if text is not None else None
    if updated is None:
        path = vault_root / FALLBACK_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        current = path.read_text(encoding="utf-8").rstrip("\n") + "\n" if path.exists() else _FALLBACK_HEADER
        write_text_atomic(path, current + line + "\n")
        return FALLBACK_FILE.as_posix()

    guides.archive_current(vault_root, "PROJECT.md")
    write_text_atomic(project, updated)
    where = "PROJECT.md"
    pending = guides.proposal_path(vault_root, "PROJECT.md")
    if pending.exists():
        updated_pending = _append_to_section(pending.read_text(encoding="utf-8"), line)
        if updated_pending is not None:
            write_text_atomic(pending, updated_pending)
            where += "+pending"
    return where


def pending_text(vault_root: Path) -> str | None:
    """The decisions recorded outside PROJECT.md, for `histos context`."""
    path = vault_root / FALLBACK_FILE
    if not path.exists():
        return None
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("- ")]
    return "\n".join(lines) or None


def _append_to_section(text: str, line: str) -> str | None:
    """`text` with `line` after the last non-blank line of its decisions section, or None if
    it has none. Line endings and everything else stay byte for byte."""
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    span = headings.section_span([raw.rstrip("\r\n") for raw in lines], SECTION)
    if span is None:
        return None
    start, end = span
    insert_at = end
    while insert_at - 1 > start and not lines[insert_at - 1].strip():
        insert_at -= 1
    if insert_at > 0 and not lines[insert_at - 1].endswith(("\n", "\r")):
        lines[insert_at - 1] += newline  # the section ends the file without a final newline
    if insert_at == start + 1:  # an empty section: keep a blank line under the heading
        lines.insert(insert_at, newline)
        insert_at += 1
    lines.insert(insert_at, line + newline)
    return "".join(lines)
