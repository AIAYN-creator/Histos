# Roadmap

What's coming, what shipped, and the ideas and questions behind it. Every release in detail: [CHANGELOG.md](../CHANGELOG.md).

## Next

### v3.2 — in development: estimates and the critical path

Each user trains their own Histos: estimates come from *your* history — recorded by v3.1 in every vault — never from the agent's guess (models are bad at estimating their own work), and nothing is shared. The design is decided (planned and approved in a Histos vault with v3.1's interview and plans); it ships once your own data is enough.

- **Two estimates per card, kept apart:** the agent's working time and the time it waits for your review. They're grouped differently — the agent's by tier, card type and number of dependencies; the review's by card type only, because review speed depends on you, not on the card. The median is the estimate and the 80th percentile the "if it goes badly", from the 30 most recent similar cards, with at least 5 of them; below that it says "no data" and how many are missing. Shown in `histos status` and in the card's `estimated_duration_hours`; `actual_duration_hours` gets the real figure on approval.
- **Measured fairly:** an agent works one card at a time, so a batch assigned before you leave isn't counted as parallel work; time waiting for your `retry` counts as review, not as the agent's. Card types come from id prefixes only in vaults that really use them.
- **The critical path on the arrows**, in magenta — off the status palette, so it can't be mistaken for Blocked. It's the remaining chain that takes longest, repainted after every command that changes the board; with no data at all, it's the longest chain of cards. Histos keeps track of the arrows it painted and never touches one you colored yourself. It can be turned off in `histos.config.yaml`.
- **`histos estimate --coverage`** shows how many closed cards each group has and which are enough — the measure of when there's enough data to release.

### v3.3 — planned: project metrics and the final report

- **`histos report`** writes a Markdown report into the vault when a project closes. It includes a Gantt chart drawn by Obsidian from a [Mermaid](https://mermaid.js.org/) block (no plugins, no images) and a three-point PERT whose estimates come from the history's percentiles.
- **Metrics:** actual vs. estimated time, rejection rate per tier and per model, eval attempts, time waiting for review, and how far the critical path drifted. This also gives the tier heuristics real outcomes to be calibrated against. New metrics ship as patch releases (v3.3.x).

### v4.0 — planned: yolo mode

Unattended work from backlog to merged-ready branch. It's a major version because it changes the trust model: for the first time someone other than the human approves.

- **Only a human starts it**, typing `histos yolo start` in a terminal, with the same protections as `approve`. It stays on until the backlog is empty, with no card or round limits.
- **Three roles:** an orchestrator picks and approves a model for each backlog card and assigns it, a builder writes the proposal, and a reviewer approves or rejects it on its own judgment. Histos refuses to let the reviewer be the agent that built the card. Approvals are marked as an agent's (`approved_by: agent:…`).
- **Never on `main`:** approved work is committed and pushed to a parallel `yolo` branch of the project's code repository. If the human doesn't like it, they don't merge it.
- **A token budget** is the stop condition, so a finished project never comes with an empty quota. Histos can't see tokens itself; how to measure them is still being decided (a Claude Code hook reading the session transcript, agent-reported usage, or the weekly usage percentage if the platform exposes it).
- **Iteration is the point:** failing evals don't stop the loop. Only a card that can never pass gets parked for the human, and the loop moves on to the others.
- **Resumable** after a dropped connection or a dead battery: the yolo state lives on disk, `histos yolo resume` picks up where it stopped, and cards left In progress are recovered with `--takeover`.
- **Off limits:** yolo never touches `PROJECT.md`, `ARCHITECTURE.md`, `histos.config.yaml` or `evals/`.

### v2.2 — whenever it's needed

- Addressing GUI bugs and implementing minor features identified during beta testing.

## Future directions

Ideas for later versions, none committed yet — roughly from most to least likely:

- **The desktop app catches up with v3:** each card's tier, model and evals on the overview; approving the models of a queue and the guide changes from the app; `retry` and `accept-edit` as buttons.
- **More eval checks**, still deterministic: citation keys that resolve against a `references.bib`, links and images that exist, a maximum length per section, and — opt-in, for code projects — running the project's own test command on a proposal.
- **Permissions for other agents.** Generate the equivalent of the Claude Code deny list for Codex CLI and Gemini CLI from the same rules.
- **An MCP server** exposing the Histos commands as structured tools, so agents call them directly instead of through a shell — clearer for the agent, and a narrower surface than shell access.
- **Project presets:** `histos init --preset thesis|paper|code` with a matching card table template, `histos.config.yaml` terms and evals.
- **The cyan state, for real:** a `histos link --request` that records a dependency change request on the board, for the human to accept, instead of only asking in the conversation.
- **Publishing on PyPI** (`pip install histos-cli`) and desktop app builds for macOS and Linux.
- **A read-only Obsidian plugin** that shows tier, model and eval badges on the canvas cards (Histos keeps writing the native Canvas).
- **Several human reviewers** with roles — who can approve which cards.
- Still open from earlier versions: git as an optional history backend for the content, calendar/date-based scheduling, pulling external context automatically (e.g. Overleaf's `.tex` via its Git integration), `content/` subfolder conventions, and a translated desktop app.

## Shipped

### v3.1 ([v3.1.0](https://github.com/AIAYN-creator/Histos/releases/tag/v3.1.0))

A stricter start, and history recorded from day one — projects went back and forth because the idea never got across at the start, and the estimates and reports planned next need data that only exists once something records it.

- **The interview:** on an empty vault the agent asks in short rounds, pushes back with reasons and alternatives, and writes a `PROJECT.md` with a required structure — including the project in its own words — that the evals check before it reaches the human.
- **Plans:** `histos plan propose/diff/approve/reject` — the whole card table reviewed as one, with levels and warnings in `plan diff`, a checkable `done_when` per card, and nothing created until it's approved.
- **Decisions:** rejection reasons for plans and guides are kept, dated, in `PROJECT.md`, so no agent proposes the same thing again.
- **Event log:** every change to the vault, from the terminal or the desktop app, recorded in `.histos/events.jsonl` with every status change it caused — the history v3.2 and v3.3 are built from.

### v3 ([v3.0.0](https://github.com/AIAYN-creator/Histos/releases/tag/v3.0.0))

Model routing by tier, subagents with an isolated context, and deterministic evals as a filter before human review — the agent still proposes, the human still approves. Planned, specified and reviewed card by card in a Histos vault.

- **Tiers and model approval:** `classify` (heuristic, explained, calibrated on ~400 real cards), `model --proposed` / `--approved --authorized`, `histos.config.yaml`.
- **Subagents:** `context --for-subagent`, `assign --worker`/`--takeover`, and safe concurrency — an OS file lock around every command that changes the vault, atomic writes, verified by a stress test (8 agents × 20 rounds, nothing lost) — v2.1.1 lost 2–4 of 8 simultaneous `assign`s.
- **Evals:** `evals/` with four checks, run on `propose`, three attempts, `retry`, `eval` as a dry run, results in `diff`.
- **Direct-write detection** with `content_hash`, and `accept-edit` for the human's own edits.
- **Guidance files:** `ARCHITECTURE.md`, and `guide propose/approve/reject` for it and `PROJECT.md`.
- **Upgrading:** `init --add-config` for existing vaults.
- **Polish and audit:** CI on Windows and Linux, the code and its comments in English, `cli.py` split into `commands/`, a sample vault, docs rewritten, and a security and test audit: the Windows path-traversal fix below, the vault lock refusing symbolic links, the desktop app only ever opening folders, and test coverage from 93 % to 97 %.

### v2.1 ([v2.1.0](https://github.com/AIAYN-creator/Histos/releases/tag/v2.1.0), [v2.1.1](https://github.com/AIAYN-creator/Histos/releases/tag/v2.1.1))

- **`histos arrange`.** Born from a real 110-card board that had become unreadable: cards piled up in the first column, arrows frozen pointing up and down, and 643 cases of an arrow drawn across a card. It re-lays out the whole board on request — Sugiyama-style dependency columns, free corridors computed from the exact curve Obsidian draws (643 → 0 on that board, and 0 on every other real vault it was tried on), "Not connected" and "Done" groups, redundant arrows moved into `implied_dependencies`. Around it: arrows always go right side → left side, and new cards land next to their dependencies.
- **v2.1.1.** `histos context` shows the card's own rejection feedback; docs corrected to match the code.

### v2 ([v2.0.0](https://github.com/AIAYN-creator/Histos/releases/tag/v2.0.0))

1. **Desktop app for non-technical use.** A `pywebview` window over `src/histos/operations.py` (a structured, non-printing extraction of the human-facing commands), packaged with PyInstaller (`packaging/histos-gui.spec`). The terminal isn't going anywhere — the desktop app is a second front end onto the same `operations.py`. Screens: pick a project folder (remembered) → offer to start one if it isn't a Histos project yet → a status overview (all 6 states, with the cards waiting for review front and center) → approve/reject with a plain-language before/after comparison. Buttons to open the vault in Obsidian (best-effort — Obsidian's own `open?path=` URI handling has [longstanding](https://forum.obsidian.md/t/starting-obsidian-from-command-line-with-uri-open-path-is-not-working-under-windows/107025) [reliability](https://forum.obsidian.md/t/obsidian-uri-doesnt-work-with-absolute-path-when-running-from-chrome-or-windows-10/73480) issues on Windows) or in the file explorer (always reliable). It doesn't launch or manage agent sessions, so Histos stays agent-agnostic.
2. **Broader sandboxing convention** (tool-level, see the [trust model](trust-model.md)): Claude Code's `permissions.deny` also covers `project.canvas`, and `describe --sources` auto-registers each source's absolute path.
3. **Security hardening pass.** Dependency scan (`pip-audit`, clean), no `subprocess`/`shell=True`/`eval`/`pickle`/unsafe YAML anywhere, the `js_api` bridge reviewed, and one risk deliberately left open — limit 3 in the [trust model](trust-model.md#three-honest-limits). *Correction found in the v3 audit:* this pass stated that the schema's `^content/[^/]+\.md$` kept a card from pointing outside `content/`. On Windows it didn't — a backslash is a separator there too, so a hand-edited canvas could make `approve` write outside the vault. Fixed in 3.0.0.
4. **Stable card layout.** `add-card`, `link` and `describe` never move or resize existing cards; a new card is placed by collision-avoidance.

### v1

- Formal `.canvas` schema with the color and node-type conventions
- Frontmatter in the `.md` files for metadata Obsidian doesn't natively interpret
- Minimal CLI (Python, agent-agnostic): `init`, `add-card`, `link`, `describe`, `assign`, `context`, `propose`, `diff`, `approve`, `reject`, `status`, `validate`
- Non-interactive CLI mode for AFK work — solved: no command uses interactive prompts, no special flag needed
- Agent instructions documenting the schema, color legend, and the two loops' authorization rules — [`AGENTS.md`](../src/histos/templates/AGENTS.md) (single source, agent-agnostic) + `CLAUDE.md` (one line, `@AGENTS.md`), which `histos init` copies into every new vault
- Real-write approval flow via diff before touching a canonical `.md`
- Dogfooding on a real thesis project

## Open questions

- **How much signal the tier heuristics can get from one-line card descriptions.** The calibration shows a reasonable split with visible misses; richer descriptions help, and calibrating from real outcomes (see [v3.3](#v33--planned-project-metrics-and-the-final-report)) is the likely next step.
- **Project brief format — mostly resolved:** `PROJECT.md` (what) and `ARCHITECTURE.md` (how) change only through `histos guide`, and since v3.1 `PROJECT.md` has a required structure; whether `ARCHITECTURE.md` needs one too is still open.
- **Folder structure — partially resolved:** cards live in `content/<id>.md` (see [canvas-schema.md](canvas-schema.md)); additional subfolders like `content/drafts/` remain open.

## Scope and prior art

**What this repo publishes** is the **tool** (CLI, desktop app, vault format, agent instructions, documentation) as an open-source project, with a ready-to-run `.exe` of the desktop app on the [Releases](https://github.com/AIAYN-creator/Histos/releases) page. Git/GitHub is not the version-history backend for each user's project content — that's outside Histos's scope and is each user's own decision.

- **Kanvas (XMihura)** — direct architectural reference. Same pattern but code-oriented: a prompt + Python CLI the agent uses to touch the canvas, plus the `.canvas` itself. No SaaS, no build step, agent-agnostic. Histos started from that pattern for writing projects, and v3 brings it back to code too.
- **claude-canvas (AgriciDaniel)** — reference only for the auto-layout algorithm (`dagre`) and the idea of visual zones/groups. Not the base architecture. Histos's own `histos arrange` ended up in that same family (a Sugiyama-style layered layout), written from scratch in plain Python so it adds no dependency.
- **JSON Canvas spec (jsoncanvas.org)** — the `.canvas` format is the open JSON Canvas standard, not Obsidian-proprietary. Available node types: `text` (embedded markdown), `file` (path to a real vault file), `link` (URL), `group` (a purely geometric container — a node "belongs" to a group only if its coordinates fall inside the group's rectangle; there's no `parent_id` in the data).
