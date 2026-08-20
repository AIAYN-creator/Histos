# `.canvas` schema

This document formalizes Histos's conventions on top of the [JSON Canvas 1.0](https://jsoncanvas.org/spec/1.0/) format. The machine-checkable validator lives in [`src/histos/schema/histos-canvas.schema.json`](../src/histos/schema/histos-canvas.schema.json) (packaged alongside the CLI); here's the why.

What the JSON Schema **can't** check on its own — that each edge's `fromNode`/`toNode` point to an `id` that exists, and that the dependency graph is acyclic — is the CLI's responsibility (`histos status` / `histos validate`), not this schema's.

## Folder structure

```
<vault>/
├── project.canvas
├── content/
│   └── <slug>.md        # one card = one file (current canonical version)
├── proposals/
│   └── <slug>.md        # draft pending approval/rejection
└── approved/
    └── <slug>.md        # copy of the draft exactly as it was approved (history; rejected drafts are NOT kept)
```

Vaults created before Histos's English rename use `propuestas/`/`aprobados/` instead of `proposals/`/`approved/` — same folders, same behavior, just the old names; the CLI detects whichever one a given vault already has and keeps using it, so existing vaults are never migrated automatically.

Additional subfolders inside `content/` (e.g. `drafts/`) remain open — not part of this convention yet.

## Node types

Histos uses three of JSON Canvas's four node types:

- **`file`** — a task card. Points to a real `.md` in `content/`; never contains embedded text. A loose `link` node is not allowed.
- **`group`** — a purely geometric grouping (e.g. "chapter 3"). A node "belongs" to a group only if its coordinates fall inside the group's rectangle — there's no `parent_id` in the data. Carries no status.
- **`text`** — only for decorative/documentation content (the color legend that `histos init` places on every new canvas). Not a card: the CLI ignores it entirely — all status/dependency logic explicitly filters by `type == "file"`. Nothing stops you from adding more text notes by hand in Obsidian; Histos simply doesn't touch them.

## Id convention

A card's `id` is the same slug as its file name: id `cap3` → `content/cap3.md`. This keeps the CLI's own examples (`histos assign cap3`, `--depends-on cap2`) directly readable. `group` nodes only need to be unique, no further convention.

## Color → status

A card's color (the `color` field, preset `"1"`–`"6"`) **is** its status — never duplicated anywhere else:

| Preset | Color | Status |
|---|---|---|
| `"1"` | red | Blocked |
| `"2"` | orange | In progress |
| `"3"` | yellow | Proposal pending review |
| `"4"` | green | Approved |
| `"5"` | cyan | Dependency change request |
| `"6"` | purple | Backlog |

A `file` card without `color` isn't managed by Histos (added by hand in Obsidian, or corrupted data) — the schema rejects it because `color` is required on `cardNode`, and that's intentional: the schema validation itself is the detection mechanism.

**Blocked is a derived status**, not something the agent or the human assigns by hand: the CLI computes it by checking whether *all* of a card's incoming edges point to nodes already Approved (color `"4"`). Nothing stops you from setting color `"1"` manually, but the CLI should treat that as a signal to recompute, not as a source of truth.

## Edges — dependency semantics

`fromNode → toNode` means **"toNode depends on fromNode"**: fromNode must reach Approved before toNode can leave Blocked. This matches the canvas's left-to-right, Gantt-style layout and the spec's default (`toEnd` = `"arrow"` points at toNode, i.e. at whatever gets unblocked).

## `.md` frontmatter

Whatever Obsidian/JSON Canvas doesn't interpret natively lives as YAML frontmatter on each card — never in the `.canvas`:

| Field | Type | Notes |
|---|---|---|
| `description` | string | one line, summarizes the card; set via `add-card --description` or updated later with `histos describe` — never touches the body, so it doesn't go through `propose`/`approve` |
| `sources` | list of strings | paths to external files (`.txt`, `.md`, `.tex`, `.docx`) with reference material for this card — `describe --sources` replaces the whole list, it doesn't append. `histos context <id>` reads them and includes their text |
| `estimated_duration_hours` | number | filled in by the agent when accepting/starting the task |
| `actual_duration_hours` | number | filled in on completion, to compare against the estimate |
| `assigned_to` | `"agent"` \| `"human"` | |
| `status_note` | string | free text, e.g. the reason it's blocked |

`status` is deliberately not here: it lives as `color` on the canvas so the same data doesn't have two sources of truth.

## Minimal example

```jsonc
{
  "nodes": [
    { "id": "cap1", "type": "file", "x": 0,    "y": 0, "width": 250, "height": 100,
      "file": "content/cap1.md", "color": "4" },        // Approved
    { "id": "cap2", "type": "file", "x": 320,  "y": 0, "width": 250, "height": 100,
      "file": "content/cap2.md", "color": "3" },        // Proposal pending review
    { "id": "cap3", "type": "file", "x": 640,  "y": 0, "width": 250, "height": 100,
      "file": "content/cap3.md", "color": "1" }         // Blocked (depends on cap2)
  ],
  "edges": [
    { "id": "e1", "fromNode": "cap1", "toNode": "cap2" },
    { "id": "e2", "fromNode": "cap2", "toNode": "cap3" }
  ]
}
```

See [`examples/example.canvas`](../examples/example.canvas) for a complete one, with a `group`.
