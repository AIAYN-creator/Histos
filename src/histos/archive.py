"""Archive closed cards from `project.canvas` into a separate canvas (`dec-archive-spec`,
`cod-archive-cmd`).

Leaves `content/<id>.md`, `approved/<id>.md`, and `.histos/events.jsonl` untouched so history,
estimates, and `histos report` keep every sample intact while keeping `project.canvas` lean.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from . import canvas, events, frontmatter, operations
from .errors import HistosError
from .fsutil import write_text_atomic

DEFAULT_ARCHIVE_NAME = "archive.canvas"


@dataclass(frozen=True)
class ArchiveResult:
    archived: list[str]
    target: Path
    edges_moved: int
    dry_run: bool


def _resolve_target(vault_root: Path, target_name: str) -> Path:
    name = (target_name or "").strip()
    if not name:
        raise HistosError("archive file name can't be empty")
    if Path(name).is_absolute():
        raise HistosError(f"archive path must be relative to the vault: '{name}'")
    if not name.lower().endswith(".canvas"):
        raise HistosError(f"archive file '{name}' must end with '.canvas'")
    root_resolved = vault_root.resolve()
    target = (root_resolved / name).resolve()
    try:
        rel = target.relative_to(root_resolved)
    except ValueError:
        raise HistosError(f"archive path escapes the vault: '{name}'") from None
    if rel.as_posix().lower() == canvas.CANVAS_FILENAME.lower():
        raise HistosError(f"can't archive into {canvas.CANVAS_FILENAME} itself")
    if target.is_symlink():
        raise HistosError(f"archive target '{name}' is a symbolic link")
    return target


def _open_dependents(vault_root: Path, data: dict) -> dict[str, set[str]]:
    """dependency_id -> non-Approved card ids that depend on it (direct or implied)."""
    by_id = {c["id"]: c for c in canvas.cards(data)}
    dependents: dict[str, set[str]] = {}
    for edge in canvas.dependency_edges(data):
        dep, child = edge["fromNode"], edge["toNode"]
        if child in by_id and by_id[child].get("color") != canvas.APPROVED:
            dependents.setdefault(dep, set()).add(child)
    for card in by_id.values():
        if card.get("color") == canvas.APPROVED:
            continue
        md_path = canvas.card_file_path(vault_root, card)
        if not md_path.exists():
            continue
        meta, _ = frontmatter.read(md_path)
        for dep in meta.get(operations.IMPLIED_DEPENDENCIES_KEY) or []:
            dependents.setdefault(dep, set()).add(card["id"])
    return dependents


def eligible_cards(data: dict, vault_root: Path) -> list[dict]:
    """Approved cards with no open (non-Approved) dependents."""
    open_deps = _open_dependents(vault_root, data)
    return [c for c in canvas.cards(data) if c.get("color") == canvas.APPROVED and not open_deps.get(c["id"])]


def _load_target_canvas(target: Path) -> dict:
    if not target.exists():
        return {"nodes": [], "edges": []}
    try:
        loaded = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise HistosError(f"can't read existing archive canvas '{target.name}': {e}") from None
    if not isinstance(loaded, dict):
        raise HistosError(f"archive canvas '{target.name}' must be a JSON object")
    loaded.setdefault("nodes", [])
    loaded.setdefault("edges", [])
    return loaded


def _remember_staying_dependencies(
    vault_root: Path, data: dict, moving_ids: set[str], target_node_ids: set[str],
) -> None:
    """When archiving removes an edge between an archived card and one staying in project.canvas,
    keep that dependency in the dependent card's `implied_dependencies`."""
    by_id = {c["id"]: c for c in canvas.cards(data)}
    archived_scope = moving_ids | target_node_ids
    cross_deps: dict[str, list[str]] = {}
    for edge in canvas.dependency_edges(data):
        u, v = edge["fromNode"], edge["toNode"]
        if u in by_id and v in by_id and (u in moving_ids or v in moving_ids):
            if not (u in archived_scope and v in archived_scope):
                cross_deps.setdefault(v, []).append(u)
    for card_id, deps in cross_deps.items():
        md_path = canvas.card_file_path(vault_root, by_id[card_id])
        if not md_path.exists():
            continue
        meta, body = frontmatter.read(md_path)
        implied = list(meta.get(operations.IMPLIED_DEPENDENCIES_KEY) or [])
        implied += [d for d in deps if d not in implied]
        meta[operations.IMPLIED_DEPENDENCIES_KEY] = implied
        frontmatter.write(md_path, meta, body)


def archive_cards(
    vault_root: Path,
    card_ids: list[str] | None = None,
    *,
    all_done: bool = False,
    target_name: str = DEFAULT_ARCHIVE_NAME,
    dry_run: bool = False,
) -> ArchiveResult:
    ids = list(card_ids or [])
    if bool(ids) == bool(all_done):
        raise HistosError("pass card ids or --all-done (not both)")

    target = _resolve_target(vault_root, target_name)
    data = operations._load_valid(vault_root)
    open_deps = _open_dependents(vault_root, data)

    if all_done:
        selected = eligible_cards(data, vault_root)
    else:
        seen: set[str] = set()
        selected = []
        for cid in ids:
            if cid in seen:
                continue
            seen.add(cid)
            card = canvas.find_card(data, cid)
            if card is None:
                raise HistosError(f"card '{cid}' doesn't exist")
            if card.get("color") != canvas.APPROVED:
                raise HistosError(
                    f"'{cid}' isn't Approved (current state: {operations.STATE_NAMES[card['color']]})"
                )
            blockers = sorted(open_deps.get(cid) or ())
            if blockers:
                raise HistosError(f"'{cid}' still has open dependent(s): {', '.join(blockers)}")
            selected.append(card)

    if not selected:
        return ArchiveResult(archived=[], target=target, edges_moved=0, dry_run=dry_run)

    target_data = _load_target_canvas(target)
    existing_target_nodes = {n.get("id") for n in target_data["nodes"] if isinstance(n, dict)}
    existing_target_edges = {e.get("id") for e in target_data["edges"] if isinstance(e, dict)}
    moving_ids = {c["id"] for c in selected}
    archived_scope = moving_ids | {nid for nid in existing_target_nodes if isinstance(nid, str)}

    edges_to_move = []
    remaining_edges = []
    for edge in data.get("edges", []):
        u, v = edge.get("fromNode"), edge.get("toNode")
        if u in moving_ids or v in moving_ids:
            if u in archived_scope and v in archived_scope and edge.get("id") not in existing_target_edges:
                edges_to_move.append(edge)
        else:
            remaining_edges.append(edge)

    target_pairs = {(e.get("fromNode"), e.get("toNode")) for e in target_data["edges"] + edges_to_move}
    for card in selected:
        md_path = canvas.card_file_path(vault_root, card)
        if not md_path.exists():
            continue
        meta, _ = frontmatter.read(md_path)
        for dep in meta.get(operations.IMPLIED_DEPENDENCIES_KEY) or []:
            if dep in existing_target_nodes and (dep, card["id"]) not in target_pairs:
                edges_to_move.append({"id": f"archive:{dep}->{card['id']}", "fromNode": dep, "toNode": card["id"]})
                target_pairs.add((dep, card["id"]))

    if not dry_run:
        _remember_staying_dependencies(vault_root, data, moving_ids, {n for n in existing_target_nodes if isinstance(n, str)})
        data["nodes"] = [n for n in data.get("nodes", []) if n.get("id") not in moving_ids]
        data["edges"] = remaining_edges
        for card in selected:
            if card["id"] not in existing_target_nodes:
                target_data["nodes"].append(card)
        target_data["edges"].extend(edges_to_move)
        canvas.normalize_edge_sides(target_data)
        target.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(target, json.dumps(target_data, indent=2, ensure_ascii=False) + "\n")
        canvas.save(vault_root, data)
        rel = target.relative_to(vault_root.resolve()).as_posix()
        events.emit("archived", cards=[c["id"] for c in selected], target=rel)

    return ArchiveResult(
        archived=[c["id"] for c in selected],
        target=target,
        edges_moved=len(edges_to_move),
        dry_run=dry_run,
    )
