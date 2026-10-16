"""What each check in the evals catalog measures (evals.py holds the format and the catalog).

Every check gets the draft text, its parameters and the vault, and returns whether it passes,
what it measured, and a default message for when the eval doesn't set its own `error`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import canvas, frontmatter, operations


@dataclass
class CheckInput:
    draft: str
    params: dict
    vault_root: Path
    data: dict
    card: dict


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
    return " ".join(text.strip().lstrip("#").split()).lower()


def required_headings(i: CheckInput) -> CheckOutcome:
    present = {_normalize_heading(m.group(1)) for line in i.draft.splitlines() if (m := _HEADING_RE.match(line))}
    missing = [h for h in i.params["headings"] if _normalize_heading(h) not in present]
    if missing:
        detail = "missing: " + ", ".join(f'"{h}"' for h in missing)
        return CheckOutcome(False, detail, f"the draft lacks required headings -- {detail}")
    return CheckOutcome(True, f"all {len(i.params['headings'])} headings present", "")


def content_untouched(i: CheckInput) -> CheckOutcome:
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
    "content_untouched": content_untouched,
}
