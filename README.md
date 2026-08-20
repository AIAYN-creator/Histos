# Histos

Open-source, agent-agnostic tool that uses **Obsidian Canvas** as a shared human-AI board for managing written-production projects: theses, dissertations, papers, blog posts, reports.

The agent proposes the workflow and drafts the tasks; the human approves real changes before they're applied to the content.

> **State:** v1 MVP shipped — formal schema ([`src/histos/schema/`](src/histos/schema/)) and CLI ([`src/histos/`](src/histos/), 12 commands, `pytest` green), actively dogfooded on a real thesis. v2 is in progress — stable card layout, broadened sandboxing, and a security hardening pass are done; the desktop app is the one piece still ahead — see [Roadmap](#roadmap). There is no Obsidian plugin, and v2 isn't planning to build one either (see below).

## Core idea

Strict separation between the **project map** (`canvas.json`) and the **actual content** (`.md` files). The canvas never contains prose — only file references, status metadata, and the dependency graph.

Everything lives locally, in a normal Obsidian vault (a folder with an `.obsidian/` subfolder). There's no server or remote backend of its own — the only step that leaves local is the call to the agent/LLM.

## The two loops

### Loop 1 — Planning (the agent has near-total freedom)

The agent can freely:
- Create, edit, and move cards
- Change a card's status/color
- Edit a card's text/description
- Visually group cards (geometric groups; no rigid hierarchy in the data)

Requires explicit user authorization:
- Creating, deleting, or redirecting a **dependency edge** — because it redefines what unblocks and in what order, it's not a purely cosmetic change

The agent decides whether to group cards based on the project's scale (a blog post probably doesn't need groups; a thesis probably does). No hierarchy is forced in v1.

In a freshly initialized vault (no cards yet), Loop 1 starts with a short interview — what the project is about, what type of work it is, whether there's a mandatory structure, whether review checkpoints should be modeled as cards — before proposing the initial card index. Full protocol in [`AGENTS.md`](src/histos/templates/AGENTS.md).

### Loop 2 — Execution (the agent works, the human decides)

1. The user assigns one or more cards to the agent (by id)
2. The agent receives as context: that card's `.md`, the content of upstream (dependency) cards, and the general project brief
3. The agent drafts a content proposal
4. The agent **never** writes directly to the canonical `.md` — it can only propose
5. The card moves to "proposal pending review"
6. The user reviews when they can (no need to be present while the agent works) and sees a before/after diff
7. The user approves (the real change gets applied, card → "approved") or rejects (the `.md` isn't touched, the card goes back to backlog or is flagged for retry with feedback)

**Key property — AFK-mode safety:** since the agent can only propose and never write directly, it's safe to leave it processing a queue of cards unsupervised *as long as the agent follows the rules in `AGENTS.md`*. The worst case with an agent that follows them is finding several yellow cards waiting for review when you get back — never real content written without authorization. Careful: this is a convention the agent complies with, not a technical barrier that enforces it — see [Trust model](#trust-model).

### Dependency graph

Represented via the canvas edges as a DAG (directed acyclic graph). The agent respects topological order when choosing which card to work on next. If an action would break or ignore an existing dependency, the agent must flag it and ask for authorization **before** proceeding, never after.

## Trust model

The rule "the agent never writes directly to `content/*.md`" lives in [`AGENTS.md`](src/histos/templates/AGENTS.md) as a **prose convention** — it depends on the agent choosing to follow it. There's no filesystem permission, intermediate process, or OS-level git hook enforcing it on its own for tools other than Claude Code.

**Claude-Code-specific enforcement:** `histos init` also installs [`.claude/settings.json`](src/histos/templates/.claude/settings.json) with `permissions.deny` covering `content/**` and `project.canvas` (`Write` and `Edit`, both). Verified live: it blocks `Edit`, `Write`, and even `Bash` when the command references a denied path ("File is in a directory that is denied by your permission settings"). `describe --sources` extends this further: every time a source file is registered, `histos` also adds `Write`/`Edit` deny rules for that exact file's absolute path, so registered reference material (a Word doc, a `.tex` in Overleaf) gets the same protection as `content/`. The sync is add-only — it only ever adds rules, never removes one, so it can't silently undo something you added to `.claude/settings.json` by hand. Vaults created before this existed pick up the `project.canvas` rule the next time any `describe` runs there; a source registered before this existed gets covered the next time its card's `describe --sources` runs again — neither is retroactive on its own.

Three honest limits: (1) it's Claude-Code-specific — it doesn't protect you if the agent is another tool. Codex CLI has its own sandbox/permission system (`config.toml` profiles like `:workspace`), and Gemini CLI is moving toward a policy-engine deny mechanism — both fundamentally different formats from Claude Code's `settings.json`, and neither is wired up by Histos yet (the v2 pass was a best-effort look, not an implementation — see [Roadmap](#roadmap)). (2) it's still tool-level, not OS-level — see below. (3) `describe --sources` itself still doesn't require human authorization, by design, same as v1 (see [`AGENTS.md`](src/histos/templates/AGENTS.md) rule 3) — so a misdirected agent could still register a sensitive file as a "source" and then read its content via `histos context`. Deliberately not changed in this pass: closing it would mean requiring authorization for `describe --sources`, a workflow change bigger than a security patch.

This is a deliberate decision, not an oversight: the current use case is a single user with a trusted agent that reads and follows instructions. It stops being sufficient if the agent isn't trustworthy (injected instructions, a misaligned model) or if several users/agents with different interests share the same vault.

A real, agent-agnostic guarantee (not just for Claude Code) would require separating "who has write permission on `content/`" from "the agent" at the OS level — e.g. a container (Docker) where `content/` is mounted read-only for the agent. On a single-user desktop this isn't trivial (the agent and the CLI run as the same OS user without a container in between). Real, OS-level sandboxing is explicit future work, not implemented yet — see [Roadmap](#roadmap) (v3).

## Canvas conventions

> Formal, machine-checkable schema: [`src/histos/schema/histos-canvas.schema.json`](src/histos/schema/histos-canvas.schema.json) — full detail in [`docs/canvas-schema.md`](docs/canvas-schema.md).

- **Node type:** `file` — each card points to a real `.md` in the vault, it never contains embedded text. (`text` is used only for the decorative color legend that `histos init` generates; the CLI ignores it entirely.)
- **Layout:** reads like a Gantt chart but without calendar dates — a new card's column (x) is the dependency rank (longest path from a root); its row (y) is the first spot, scanning down, that doesn't overlap any other node already on the board. `add-card` places only the card it creates this way; `describe` may resize a card to fit a new description but never repositions it; `link` never touches position or size at all. Once a card has a position, only the user (by hand, in Obsidian) moves it again — see [Roadmap](#roadmap) v2. Not a full dagre implementation (no edge-crossing minimization), but it covers the real use case.

### Color legend

| Status | Color | Preset | Meaning |
|---|---|---|---|
| Backlog | purple | `"6"` | Pending task, not started yet |
| In progress | orange | `"2"` | The agent or the user is actively working on it |
| Blocked | red | `"1"` | Can't start because it depends on a card that isn't closed yet (derived status, computed by the CLI) |
| Proposal pending review | yellow | `"3"` | The agent proposed content and is waiting for approval (loop 2) |
| Dependency change request | cyan | `"5"` | The agent wants to modify the dependency graph and is waiting for authorization (loop 1) |
| Approved | green | `"4"` | Change accepted by the user and applied to the real content |

## Metadata

Whatever Obsidian/JSON Canvas doesn't natively interpret (estimated duration, actual duration, who a task is assigned to, notes on why it's blocked) isn't forced into the `.canvas` — it's stored as **YAML frontmatter** at the top of each `.md`, a format Obsidian already supports natively. This keeps the `.canvas` 100% compatible with standard Obsidian.

Fields: `estimated_duration_hours`, `actual_duration_hours`, `assigned_to`, `status_note`. Tracking estimated vs. actual over time will make it possible to calibrate how reliable the agent's estimates are for this kind of task.

## CLI

Concrete, clearly named commands instead of having the agent hand-edit the canvas JSON, to avoid breaking the format and to limit the agent to a known set of safe operations (like an API). Agent-agnostic: works with any tool that can run shell commands and read/write files (Claude Code, Codex, Gemini CLI...).

Installation (editable, for development):

```bash
pip install -e .
```

Every command operates on the current directory, which must be the root of a Histos vault (`histos init` creates it). No command blocks on an interactive prompt, so a queue of cards can be processed in AFK mode with a simple loop in the agent that invokes the CLI — no special flag or mode needed.

```bash
histos init                                            # creates project.canvas (with color legend) + content/
histos add-card cap1 --title "Intro" [--description "..."]
                                                          # standalone card -> Backlog
histos add-card cap2 --title "Cap 2" --depends-on cap1 --authorized
                                                          # --authorized is required as soon as the dependency
                                                          # graph is touched (Loop 1) -- no prompt: a flag the
                                                          # agent only sets after asking for permission in chat
histos link cap1 --depends-on cap0 --authorized         # adds a dependency to an ALREADY existing card
histos describe cap1 --text "..." [--sources f1 f2]     # description and/or external sources (frontmatter, no permission needed)
histos assign cap1 [--by agent|human]                    # -> In progress
histos context cap1                                     # bundles description+approved dependencies+sources+PROJECT.md
histos propose cap1 --file draft.md                     # -> Proposal pending review
histos diff cap1                                        # diff between content/cap1.md and the pending proposal
histos approve cap1                                     # applies the proposal to the real .md -> Approved
histos reject cap1 [--feedback "..."]                   # discards the proposal -> Backlog, feedback in status_note
histos status                                           # cards grouped by status (recalculates Blocked/Backlog)
histos validate                                         # validates project.canvas against the formal schema
```

### Opening the vault in Obsidian

`histos init` creates `project.canvas` in the current directory — that folder, **exactly that one and nothing above it**, is what you need to open as a vault in Obsidian (`Open folder as vault`). Canvas is a native Obsidian feature; no plugin needed.

Why this matters so much: cards reference their `.md` files with paths relative to the vault (`content/cap1.md`). If you open a folder above the one containing `project.canvas` (e.g. the parent directory instead of the vault itself), those paths no longer resolve and Obsidian shows cards as "Create new note" / "Swap file..." instead of with content — it's not broken, it's the wrong folder. If this happens after you already had the vault open correctly (e.g. because `histos` created files while Obsidian was already open), reload with `Ctrl+R` before suspecting anything else.

Code in [`src/histos/`](src/histos/), tests in [`tests/`](tests/) (`pytest`). Practical day-to-day usage guide (for humans, not agents): [`docs/usage.md`](docs/usage.md).

## Roadmap

### v1 — shipped

- Formal `.canvas` schema with the color and node-type conventions above
- Frontmatter in the `.md` files for metadata Obsidian doesn't natively interpret
- Minimal CLI (Python, agent-agnostic): `init`, `add-card`, `link`, `describe`, `assign`, `context`, `propose`, `diff`, `approve`, `reject`, `status`, `validate` — implemented and tested ([`src/histos/`](src/histos/), [`tests/`](tests/))
- Non-interactive CLI mode for AFK work — solved: no command uses interactive prompts, no special flag needed
- Agent instructions documenting the schema, color legend, and the two loops' authorization rules — implemented as [`AGENTS.md`](src/histos/templates/AGENTS.md) (single source, agent-agnostic) + `CLAUDE.md` (one line, `@AGENTS.md`), which `histos init` copies into every new vault
- Real-write approval flow via diff before touching a canonical `.md`
- Dogfooding on a real thesis project

### v2 — in progress

1. **Desktop app for non-technical use.** A thin Python wrapper around the existing CLI (likely `pywebview`, packaged into a single `.exe` with PyInstaller) — no terminal required. Obsidian stays the visual canvas, installed separately (not bundled). Planned screens: a project-init wizard, a status overview, and the core review loop (diff → approve/reject) that today requires the terminal, plus a button to open the vault in Obsidian. Out of scope: launching or managing agent sessions — the user still runs their agent of choice (Claude Code, Codex...) separately, so Histos stays agent-agnostic.
2. **Broaden the sandboxing convention — done** (still tool-level, not OS-level — see [Trust model](#trust-model)). Claude Code's `permissions.deny` now also covers `project.canvas`, and `describe --sources` auto-registers each source's absolute path into the deny list too, add-only so it can't clobber a rule you added by hand. Best-effort look at Codex CLI and Gemini CLI's own permission systems, documented in [Trust model](#trust-model) — neither is wired up by Histos, since both use a fundamentally different mechanism from Claude Code's `settings.json`.
3. **Security hardening pass — done.** Dependency vulnerability scan (`pip-audit`, scoped to Histos's actual dependency tree, not the whole environment): clean, no known vulnerabilities. Path traversal reviewed beyond the id check in `add-card`: the JSON Schema itself constrains `cardNode.file` to `^content/[^/]+\.md$`, so even a hand-edited `project.canvas` can't point a card outside `content/` — `histos validate`/`_load_valid` rejects it before any command touches a file. Confirmed no `subprocess`/`shell=True`/`eval`/`pickle`/unsafe YAML loading anywhere in the codebase. One risk found and *deliberately left open*, not silently fixed: see limit (3) in [Trust model](#trust-model). The desktop-app surface (once built) will need its own pass, since a local `pywebview`-backed process is new attack surface v1's CLI-only design didn't have.
4. **Stable card layout — done.** `add-card`, `link`, and `describe` no longer recompute every card's position and size on every call. A new card is placed via collision-avoidance so it doesn't overlap anything already on the board; once a card exists, the CLI never moves or resizes it again — regardless of whether the user repositioned it by hand in Obsidian.

### v3 — future, not scoped yet

- **Multi-agent support** (several agents working the same vault in parallel). Blocked on real, OS-level sandboxing (a container with `content/` mounted read-only for the agent, or similar) — v2 only broadens the tool-level convention, it doesn't attempt this.

### Unscheduled ideas (not committed to any version)

- Native Obsidian plugin — superseded for now: v2 deliberately keeps using Obsidian's native Canvas view instead of building one (see v2 above).
- Git as a version-history backend for user content
- Real calendar/date-based Gantt scheduling
- Automatically pulling in external context (e.g. Overleaf's `.tex` via its paid Git integration) for `histos context` — see [Open questions](#open-questions)
- Additional `content/` subfolder conventions (e.g. `content/drafts/`)

## Distribution scope

What's published in this repo is the **tool** (CLI, schema, prompt, documentation) as an open-source project. Git/GitHub is not the version-history backend for each user's project content — that's outside v1's scope and is each user's own decision.

## Prior art

- **Kanvas (XMihura)** — direct architectural reference. Same pattern but code-oriented: a prompt + Python CLI the agent uses to touch the canvas, plus the `.canvas` itself. No SaaS, no build step, agent-agnostic. Histos adapts that pattern to writing projects instead of code.
- **claude-canvas (AgriciDaniel)** — reference only for the auto-layout algorithm (`dagre`) and the idea of visual zones/groups. Not the base architecture.
- **JSON Canvas spec (jsoncanvas.org)** — the `.canvas` format is the open JSON Canvas standard, not Obsidian-proprietary. Available node types: `text` (embedded markdown), `file` (path to a real vault file), `link` (URL), `group` (a purely geometric container — a node "belongs" to a group only if its coordinates fall inside the group's rectangle; there's no `parent_id` in the data).

## Open questions

- Exact format of the general project brief — **partially resolved:** `histos context <id>` already bundles description + approved dependency content + external `sources` (`.txt`/`.md`/`.tex`/`.docx`, registered via `describe --sources`) + `PROJECT.md` if present. Still open: `PROJECT.md` itself has no defined format yet, it's simply included as-is if present
- Folder structure — **partially resolved:** cards live in `content/<slug>.md` (see [`docs/canvas-schema.md`](docs/canvas-schema.md)); additional subfolders like `content/drafts/` remain open
