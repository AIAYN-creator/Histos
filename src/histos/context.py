"""What `histos context` bundles for a card, as reusable pieces: the CLI prints it, the tier
heuristics measure it, and `--for-subagent` wraps it in a brief.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import canvas, frontmatter, guides, operations

# Proposals per cycle when evals keep failing -- so an unattended agent doesn't burn tokens forever.
MAX_ATTEMPTS = 3

# Vault-wide guidance files, in the order they're appended after the cards.
GUIDANCE_FILES = [
    ("PROJECT.md", "Project brief"),
    ("ARCHITECTURE.md", "Architecture"),
]


def read_source_text(path: Path) -> str:
    if path.suffix.lower() == ".docx":
        try:
            import docx
        except ImportError:
            return f"[couldn't read {path}: missing the python-docx dependency]"
        try:
            doc = docx.Document(str(path))
            return "\n".join(p.text for p in doc.paragraphs)
        except Exception as e:
            return f"[couldn't read {path}: {e}]"
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"[couldn't read {path}: {e}]"


def read_card(vault_root: Path, card: dict) -> tuple[dict, str]:
    md_path = canvas.card_file_path(vault_root, card)
    return frontmatter.read(md_path) if md_path.exists() else ({}, "")


def render_card(vault_root: Path, card: dict, label: str, with_feedback: bool = False) -> str:
    meta, body = read_card(vault_root, card)

    parts = [f"## {label}: {card['id']} ({operations.STATE_NAMES[card['color']]})"]
    if meta.get("description"):
        parts.append(f"Description: {meta['description']}")
    routing = _routing_line(meta) if with_feedback else None  # only the card itself, not its dependencies
    if routing:
        parts.append(routing)
    if with_feedback and meta.get("status_note"):
        parts.append(f"Feedback on a rejected proposal: {meta['status_note']}")
    if card["color"] == canvas.APROBADA and body.strip():
        parts.append(f"Approved content:\n{body.strip()}")
    for src in meta.get("sources") or []:
        src_path = Path(src).expanduser()
        if not src_path.exists():
            parts.append(f"[source not found: {src}]")
            continue
        parts.append(f"Source ({src}):\n{read_source_text(src_path)}")
    return "\n\n".join(parts)


def _routing_line(meta: dict) -> str | None:
    pieces = [f"Tier: {meta['proposed_tier']}"] if meta.get("proposed_tier") else []
    if meta.get("approved_model"):
        pieces.append(f"Approved model: {meta['approved_model']}")
    elif meta.get("proposed_model"):
        pieces.append(f"Proposed model: {meta['proposed_model']} (not approved yet)")
    return " · ".join(pieces) or None


def dependencies(vault_root: Path, data: dict, card: dict) -> list[tuple[dict, str]]:
    """(dependency card, section label): direct ones, then the implied ones arrange stopped drawing."""
    by_id = {c["id"]: c for c in canvas.cards(data)}
    direct = [e["fromNode"] for e in canvas.incoming_edges(data, card["id"])]
    result = [(by_id[d], "Dependency") for d in direct if d in by_id]
    meta, _ = read_card(vault_root, card)
    for dep_id in meta.get(operations.IMPLIED_DEPENDENCIES_KEY) or []:
        if dep_id in by_id and dep_id not in direct:
            result.append((by_id[dep_id], "Dependency (implied -- arrow not drawn)"))
    return result


def card_sections(vault_root: Path, data: dict, card: dict) -> list[str]:
    sections = [render_card(vault_root, card, "Card", with_feedback=True)]
    for dep, label in dependencies(vault_root, data, card):
        sections.append(render_card(vault_root, dep, label))
    return sections


def guidance_sections(vault_root: Path) -> list[str]:
    rejected = guides.feedback(vault_root)
    sections = []
    for filename, label in GUIDANCE_FILES:
        path = vault_root / filename
        parts = [path.read_text(encoding="utf-8").strip()] if path.exists() else []
        if filename in rejected:
            parts.append(f"Feedback on a rejected {filename} change: {rejected[filename]}")
        if parts:
            sections.append(f"## {label} ({filename})\n\n" + "\n\n".join(parts))
    return sections


def build(vault_root: Path, data: dict, card: dict) -> str:
    return join(card_sections(vault_root, data, card) + guidance_sections(vault_root))


def join(sections: list[str]) -> str:
    return "\n\n---\n\n".join(sections)


@dataclass
class SubagentBrief:
    text: str
    approved_model: str | None


def build_for_subagent(vault_root: Path, data: dict, card: dict) -> SubagentBrief:
    """Everything a subagent that inherits nothing from its parent needs: a header with what
    to do and when to stop, AGENTS.md (the hard rules hold for it too), then the same card,
    dependency and guidance sections as `histos context` -- built by the same functions, so
    parent and child never see two versions of one card.
    """
    meta, _ = read_card(vault_root, card)
    model = meta.get("approved_model")
    tier = meta.get("proposed_tier")
    model_line = f"Approved model: {model}" if model else "Approved model: none recorded"
    if tier:
        model_line += f" (tier: {tier})"
    card_id = card["id"]
    worker = meta.get(operations.WORKER_KEY)
    you = f"You are {worker}, working" if worker else "You are working"
    header = "\n".join([
        f"# Subagent brief: {card_id}",
        "",
        f"{you} one Histos card in isolation. {model_line}.",
        f"When your draft is ready, run:   histos propose {card_id} --file <draft.md>",
        f"If evals fail, fix the draft and propose again ({MAX_ATTEMPTS} attempts in total at most).",
        "Never approve, reject, assign, link or arrange. Your final message to the parent is a short summary:",
        "what you did, the evals result, and anything the human should look at.",
    ])

    sections = [header]
    agents_md = vault_root / "AGENTS.md"
    if agents_md.exists():
        sections.append("## Vault rules (AGENTS.md)\n\n" + agents_md.read_text(encoding="utf-8").strip())
    sections += card_sections(vault_root, data, card) + guidance_sections(vault_root)
    return SubagentBrief(text=join(sections), approved_model=model)
