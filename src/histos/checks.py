"""What each check in the evals catalog measures (evals.py holds the format and the catalog).

Every check gets the draft text, its parameters and the vault, and returns whether it passes,
what it measured, and a default message for when the eval doesn't set its own `error`.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from . import canvas, frontmatter, operations


@dataclass
class CheckInput:
    draft: str
    params: dict
    vault_root: Path
    data: dict
    card: dict | None  # None for a guide (PROJECT.md / ARCHITECTURE.md) proposal


@dataclass
class CheckOutcome:
    passed: bool
    detail: str
    message: str  # default error message, used when the eval sets no `error`


def word_count(i: CheckInput) -> CheckOutcome:
    count = len(re.findall(r"\w+", i.draft))
    low, high = i.params.get("min"), i.params.get("max")
    bounds = ", ".join(f"{k} {v}" for k, v in (("min", low), ("max", high)) if v is not None)
    detail = f"{count} words ({bounds})"
    if low is not None and count < low:
        return CheckOutcome(False, detail, f"the draft has {count} words, at least {low} are needed")
    if high is not None and count > high:
        return CheckOutcome(False, detail, f"the draft has {count} words, at most {high} are allowed")
    return CheckOutcome(True, detail, "")


DEFAULT_PLACEHOLDERS = ["TODO", "TBD", "FIXME", "XXX", "lorem ipsum", "[...]", "[citation needed]", "[cita requerida]"]


def _placeholder_regex(pattern: str) -> re.Pattern:
    # ALL-CAPS markers match case-sensitively as whole words: "todo"/"Todo" is ordinary Spanish,
    # "TODO" is a marker. Anything else matches anywhere, ignoring case.
    if re.fullmatch(r"[A-Z0-9_]+", pattern):
        return re.compile(rf"(?<!\w){re.escape(pattern)}(?!\w)")
    return re.compile(re.escape(pattern), re.IGNORECASE)


def no_placeholders(i: CheckInput) -> CheckOutcome:
    for pattern in i.params.get("patterns") or DEFAULT_PLACEHOLDERS:
        regex = _placeholder_regex(pattern)
        for line_no, line in enumerate(i.draft.splitlines(), start=1):
            if regex.search(line):
                detail = f'"{pattern}" at line {line_no}'
                return CheckOutcome(False, detail, f"the draft still has a placeholder: {detail}")
    return CheckOutcome(True, "no placeholders", "")


_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")


def _normalize_heading(text: str) -> str:
    """'## Qué no es' and 'que NO es' compare equal: level, case, spacing and accents don't count."""
    text = " ".join(text.strip().lstrip("#").split()).lower()
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def _alternatives(heading: str | list[str]) -> list[str]:
    """A required heading is a text, or a list of acceptable spellings (e.g. one per language)."""
    return [heading] if isinstance(heading, str) else list(heading)


def _headings_in(draft: str) -> set[str]:
    return {_normalize_heading(m.group(1)) for line in draft.splitlines() if (m := _HEADING_RE.match(line))}


def required_headings(i: CheckInput) -> CheckOutcome:
    present = _headings_in(i.draft)
    missing = [_alternatives(h)[0] for h in i.params["headings"]
               if not any(_normalize_heading(alt) in present for alt in _alternatives(h))]
    if missing:
        detail = "missing: " + ", ".join(f'"{h}"' for h in missing)
        return CheckOutcome(False, detail, f"the draft lacks required headings -- {detail}")
    return CheckOutcome(True, f"all {len(i.params['headings'])} headings present", "")


def _section_words(draft: str, heading: str | list[str]) -> int | None:
    """Words between the heading and the next heading of the same or a higher level; None if absent."""
    wanted = {_normalize_heading(alt) for alt in _alternatives(heading)}
    lines = draft.splitlines()
    for n, line in enumerate(lines):
        m = _HEADING_RE.match(line)
        if not m or _normalize_heading(m.group(1)) not in wanted:
            continue
        level = len(line.strip()) - len(line.strip().lstrip("#"))
        body = []
        for following in lines[n + 1:]:
            nm = _HEADING_RE.match(following)
            if nm and len(following.strip()) - len(following.strip().lstrip("#")) <= level:
                break
            body.append(following)
        return len(re.findall(r"\w+", "\n".join(body)))
    return None


def min_section_words(i: CheckInput) -> CheckOutcome:
    short, details = [], []
    for section in i.params["sections"]:
        name = _alternatives(section["heading"])[0].lstrip("#").strip()
        count = _section_words(i.draft, section["heading"]) or 0
        details.append(f'"{name}": {count} of {section["min"]} words')
        if count < section["min"]:
            short.append(details[-1])
    if short:
        detail = "; ".join(short)
        return CheckOutcome(False, detail, f"some sections are too short -- {detail}")
    return CheckOutcome(True, "; ".join(details), "")


def content_untouched(i: CheckInput) -> CheckOutcome:
    if i.card is None:  # a guide proposal: there's no card content to compare
        return CheckOutcome(True, "not a card", "")
    changed, unknown = [], 0
    for card in canvas.cards(i.data):
        md_path = canvas.card_file_path(i.vault_root, card)
        if not md_path.exists():
            continue
        meta, body = frontmatter.read(md_path)
        expected = meta.get(frontmatter.CONTENT_HASH_KEY)
        if expected is not None:
            if frontmatter.body_hash(body) != expected:
                changed.append(card["id"])
            continue
        archived = operations._approved_path(i.vault_root, card["id"])
        if archived.exists():  # cards from before v3 had no hash: compare with the approved copy
            if frontmatter.body_hash(body) != frontmatter.body_hash(archived.read_text(encoding="utf-8")):
                changed.append(card["id"])
        else:
            unknown += 1
    if changed:
        files = ", ".join(f"content/{c}.md" for c in changed)
        detail = f"changed outside histos: {files}"
        return CheckOutcome(False, detail, (
            f"{files} changed outside histos (by hand in Obsidian?) -- "
            f"if that was you, run 'histos accept-edit {' '.join(changed)}'"
        ))
    detail = "content/ untouched" + (f" ({unknown} with no baseline, created before v3)" if unknown else "")
    return CheckOutcome(True, detail, "")


RUNNERS = {
    "word_count": word_count,
    "no_placeholders": no_placeholders,
    "required_headings": required_headings,
    "min_section_words": min_section_words,
    "content_untouched": content_untouched,
}
