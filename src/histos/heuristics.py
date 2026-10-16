"""The closed catalog of conditions usable in histos.config.yaml's `heuristics:`, their tiny
syntax, and how each is measured on a card. No eval(): every condition is a named function,
and anything outside the catalog is an error.

Syntax -- exactly three forms:
    "code_blocks_present"            a yes/no condition
    "not debugging_terms_present"    its negation
    "token_count >= 2000"            a measure, a comparator, a number

Two scopes, because measuring everything on the same text gives absurd results:
- card text: title, description, status_note and current body of the card itself. Terms are
  searched here, so a dependency mentioning "debugging" doesn't make this card `reasoning`.
- full context: what `histos context` bundles for the card (dependencies, approved content,
  sources) minus PROJECT.md/ARCHITECTURE.md, which are the same for every card and would only
  inflate every measure alike. Size, code and maths are measured here.
"""
from __future__ import annotations

import math
import operator
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import context


@dataclass
class Inputs:
    card_text: str
    full_text: str
    dependency_count: int
    source_count: int


@dataclass
class Outcome:
    holds: bool
    detail: str  # what was measured or what triggered it -- for the tier's explanation


@dataclass
class Condition:
    source: str  # as written in the config
    name: str
    negated: bool = False
    op: str | None = None
    number: float | None = None

    def evaluate(self, inputs: Inputs, terms: dict[str, list[str]] | None = None) -> Outcome:
        if self.op is not None:
            value = MEASURES[self.name](inputs)
            return Outcome(_COMPARATORS[self.op](value, self.number), f"{self.name} = {value}")
        holds, detail = CHECKS[self.name](inputs, terms or {})
        if self.negated:
            return Outcome(not holds, detail if holds else f"no {self.name.removesuffix('_present')}")
        return Outcome(holds, detail)


def gather(vault_root: Path, data: dict, card: dict) -> Inputs:
    meta, body = context.read_card(vault_root, card)
    card_text = "\n".join(str(x) for x in (meta.get("description"), meta.get("status_note"), body) if x)
    return Inputs(
        card_text=card_text,
        full_text=context.join(context.card_sections(vault_root, data, card)),
        dependency_count=len(context.dependencies(vault_root, data, card)),
        source_count=len(meta.get("sources") or []),
    )


def parse(expr: str) -> Condition:
    """Raises ValueError with a message meant for `histos validate`."""
    text = " ".join(str(expr).split())
    m = re.fullmatch(r"([a-z_]+)\s*(<=|>=|==|<|>)\s*(-?\d+(?:\.\d+)?)", text)
    if m:
        name, op, number = m.groups()
        if name not in MEASURES:
            raise ValueError(_unknown(name, "measure", MEASURES))
        return Condition(expr, name, op=op, number=float(number))
    negated = text.startswith("not ")
    name = text[4:].strip() if negated else text
    if name in _ALIASES:
        if negated:
            raise ValueError(f"'{expr}': '{name}' is already a negation -- use '{_ALIASES[name]}'")
        name, negated = _ALIASES[name], True
    if name in MEASURES:
        raise ValueError(f"'{expr}': '{name}' is a measure -- compare it, e.g. '{name} < 200'")
    if name not in CHECKS:
        raise ValueError(_unknown(name, "condition", CHECKS))
    return Condition(expr, name, negated=negated)


def _unknown(name: str, kind: str, known: dict) -> str:
    return f"unknown {kind} '{name}' (known: {', '.join(sorted(known))})"


# ---- measures --------------------------------------------------------------------------

MEASURES: dict[str, Callable[[Inputs], int]] = {
    # Approximate on purpose: Histos doesn't know which model runs the card, and each counts
    # tokens its own way. chars/4 is deterministic and needs no tokenizer.
    "token_count": lambda i: math.ceil(len(i.full_text) / 4),
    "word_count": lambda i: len(re.findall(r"\w+", i.full_text)),
    "dependency_count": lambda i: i.dependency_count,
    "source_count": lambda i: i.source_count,
}

_COMPARATORS = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge, "==": operator.eq}


# ---- yes/no conditions -------------------------------------------------------------------

_FENCE_RE = re.compile(r"^\s*(```|~~~)", re.MULTILINE)
_LIST_MARKER_RE = re.compile(r"^\s*([-*+]|\d+[.)])\s")
_MATH_RE = re.compile(
    r"\$[^$\n]+\$"
    r"|\\(frac|sum|int|sqrt|alpha|beta|gamma|delta|theta|lambda|mu|sigma|pi|partial|infty|cdot|times|leq|geq|neq|approx)\b"
    r"|[∑∫√≤≥≠≈∞∂∆πσμλ±×÷]"
)


def _code_blocks(i: Inputs, terms: dict) -> tuple[bool, str]:
    if _FENCE_RE.search(i.full_text):
        return True, "fenced code block"
    lines = i.full_text.splitlines()
    for prev, line in zip([""] + lines, lines):
        if (line.startswith("    ") or line.startswith("\t")) and line.strip() and not prev.strip() \
                and not _LIST_MARKER_RE.match(line):
            return True, "indented code block"
    return False, "no code blocks"


def _math(i: Inputs, terms: dict) -> tuple[bool, str]:
    m = _MATH_RE.search(i.full_text)
    return (True, f"math: {m.group(0)!r}") if m else (False, "no math symbols")


def _terms_check(kind: str) -> Callable[[Inputs, dict], tuple[bool, str]]:
    def check(i: Inputs, terms: dict) -> tuple[bool, str]:
        hit = _find_term(i.card_text, DEFAULT_TERMS[kind] + list(terms.get(kind, [])))
        return (True, f'{kind} term "{hit}"') if hit else (False, f"no {kind} terms")
    return check


_SEQUENCE_CONNECTORS = [
    "first", "then", "next", "afterwards", "finally", "lastly",
    "primero", "luego", "despues", "a continuacion", "finalmente", "por ultimo",
]


def _multi_step(i: Inputs, terms: dict) -> tuple[bool, str]:
    items = sum(1 for line in i.card_text.splitlines() if _LIST_MARKER_RE.match(line))
    if items >= 3:
        return True, f"{items} listed steps"
    folded = _fold(i.card_text)
    connectors = [c for c in _SEQUENCE_CONNECTORS if re.search(rf"(?<!\w){re.escape(c)}(?!\w)", folded)]
    if len(connectors) >= 2:
        return True, f"sequence words: {', '.join(connectors)}"
    return False, "no multi-step wording"


CHECKS: dict[str, Callable[[Inputs, dict], tuple[bool, str]]] = {
    "code_blocks_present": _code_blocks,
    "math_symbols_present": _math,
    "file_operations_mentioned": _terms_check("file_operations"),
    "architecture_terms_present": _terms_check("architecture"),
    "debugging_terms_present": _terms_check("debugging"),
    "multi_step_reasoning_implied": _multi_step,
}

# The spec's own names, kept so its example config works untouched.
_ALIASES = {
    "no_code_blocks": "code_blocks_present",
    "no_math_symbols": "math_symbols_present",
}

# A first, reasonable version -- dec-defaults tunes these against real vaults. English and
# Spanish, writing and code. Extended (never replaced) by the config's `terms:` section.
DEFAULT_TERMS: dict[str, list[str]] = {
    "architecture": [
        "architecture", "architectural", "design decision", "redesign", "refactor", "refactoring",
        "schema", "data model", "interface", "api", "framework", "methodology", "theoretical framework",
        "trade-off", "tradeoff",
        "arquitectura", "decision de diseno", "redisenar", "refactorizar", "refactorizacion",
        "esquema", "modelo de datos", "interfaz", "metodologia", "marco teorico",
        # Decisions and synthesis (dec-defaults): without these, "DECIDIR: stack de hosting" or
        # "Conclusiones" came out fast on the real vaults.
        "decidir", "decision", "decide", "elegir", "choose", "comparar alternativas",
        "discusion", "discussion", "sintesis", "synthesis",
    ],
    "debugging": [
        "bug", "bugs", "debug", "debugging", "traceback", "stack trace", "exception", "crash",
        "regression", "failing test", "root cause",
        "depurar", "depuracion", "fallo", "fallos", "excepcion", "no funciona", "causa raiz",
    ],
    "file_operations": [
        # Nouns and formats only: verbs like "write"/"escribir" or "save" describe every writing
        # task, not file handling.
        "read file", "write file", "file path", "directory", "folder", "csv", "json", "yaml",
        "archivo", "archivos", "fichero", "ficheros", "carpeta", "directorio",  # not "ruta": metabolic routes
        ".py", ".csv", ".json", ".yaml", ".txt", ".tex", ".docx",
    ],
}
TERM_KINDS = tuple(DEFAULT_TERMS)


def _fold(text: str) -> str:
    """Lowercase, accents dropped: "Depuración" and "depuracion" match the same term."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _find_term(text: str, terms: list[str]) -> str | None:
    folded = _fold(text)
    for term in terms:
        t = _fold(term)
        # Whole words, plurals included: "trade-off" matches "trade-offs", "decision" matches
        # "decisiones", but "design" doesn't match "designated". ".py" needs a file name before it.
        if t.startswith("."):
            pattern = rf"\w{re.escape(t)}(?!\w)"
        else:
            pattern = rf"(?<!\w){re.escape(t)}(?:s|es)?(?!\w)"
        if re.search(pattern, folded):
            return term
    return None
