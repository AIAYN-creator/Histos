# Roadmap

What's coming, what shipped, and the ideas and questions behind it. Every release in detail: [CHANGELOG.md](../CHANGELOG.md).

## Next

### v3.5: other model providers, opt-in per vault

- The agent can propose another provider's executor for a card, for example the Gemini CLI on its free tier for drawings or for reading a whole repository at once. As always, the human approves.
- **Off by default, enabled per vault** in `histos.config.yaml`. Free tiers usually pay with your data, so it's meant for public projects, never private vaults.
- Histos still only proposes, records and measures, never calls a model itself. Estimates are grouped by executor too, so whether a provider pays off is answered by your own history.
- Out of scope: local models (they need hardware most laptops don't have) and driving web chat interfaces (against providers' terms and fragile).

### v4.0: yolo mode

Unattended work from backlog to merged-ready branch. It's a major version because it changes the trust model: for the first time someone other than the human approves.

- **Only a human starts it**, typing `histos yolo start` in a terminal, with the same protections as `approve`. It stays on until the backlog is empty, with no card or round limits.
- **Three roles:**
  - an orchestrator picks and approves a model for each backlog card and assigns it;
  - a builder writes the proposal;
  - a reviewer approves or rejects it on its own judgment.

  Histos refuses to let the reviewer be the agent that built the card. Approvals are marked as an agent's (`approved_by: agent:…`).
- **Never on `main`:** approved work is committed and pushed to a parallel `yolo` branch of the project's code repository. If the human doesn't like it, they don't merge it.
- **A token budget** is the stop condition, so a finished project never comes with an empty quota. It's measured by v3.4's token accounting.
- **Iteration is the point:** failing evals don't stop the loop. Only a card that can never pass gets parked for the human, and the loop moves on to the others.
- **Resumable** after a dropped connection or a dead battery: the yolo state lives on disk, `histos yolo resume` picks up where it stopped, and cards left In progress are recovered with `--takeover`.
- **Off limits:** yolo never touches `PROJECT.md`, `ARCHITECTURE.md`, `histos.config.yaml` or `evals/`.

## Future directions

Ideas for later versions, none committed yet — roughly from most to least likely:

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

### v3.4.0 ([CHANGELOG](../CHANGELOG.md#340---2026-11-25))

- **Token usage & USD cost (`src/histos/tokens.py`):** extensible local transcript reader (`ClaudeCodeSource` + opt-in `GenericJsonlSource`) cross-referenced against `events.jsonl` windows, configurable `token_pricing`/`token_logs`, and summaries in `histos status` and `histos report` (clean `no data` without transcripts).
- **Notifications with full muting (`histos notify`):** non-blocking desktop alerts on `Proposal pending review` and exhausted eval attempts, easily muted globally (`histos notify --off`, `$HISTOS_HOME/notify.json`), per-vault (`notifications.enabled: false`), or via `HISTOS_NO_NOTIFY=1`.
- **Atomic rollback (`histos undo`):** reverts the last reversible vault mutation inside `.histos.lock` while logging an append-only `"undone"` event and strictly blocking any rollback of human approvals/rejections.
- **Model-aware estimates & alias normalization:** estimates agent duration per `approved_model` first (falling back `< 5` samples), unifies variants like `'Opus 5.5'` and `'Claude Opus 5.5'` via `normalize_model` + `model_aliases` without mutating raw history, and adds `"Agent time by model"` to `--coverage`.
- **Desktop app v3.4 (`histos-gui`):** exposes `pause`, critical path, estimates, tier/model/evals/tamper status, individual and batch model approval, guides, plans, `retry`, and `accept-edit`.

### v3.3.0 ([CHANGELOG](../CHANGELOG.md#330---2026-11-25))

- **`histos report [--output REPORT.md] [--include-archived]`:** writes a Markdown project report with executive totals, actual-vs-estimated accuracy, rejection rate and eval attempts per tier/model, critical path drift, a native Obsidian [Mermaid](https://mermaid.js.org/) Gantt timeline, and a three-point PERT (`p20`/`p50`/`p80`).
- **Deterministic project metrics (`histos.metrics`):** actual vs. estimated deviations, `p80` coverage, per-tier and per-model rejection/eval stats, and critical chain drift computed straight from `events.jsonl` and frontmatter.
- **Archiving finished work (`histos archive`):** moves eligible Done cards (and internal edges) off `project.canvas` into `archive.canvas` while preserving cross-canvas dependencies (`implied_dependencies`), `content/`, `approved/`, history, and estimates intact.
- **Commit attribution trailers in `AGENTS.md`:** documents keeping the human as sole author (never `Co-Authored-By`) with `Supervised-by:` and `Coordinated-by: Histos` trailers when committing code.

### v3.2.2 ([CHANGELOG](../CHANGELOG.md#322---2026-11-25))

- **`histos doctor [--json]`:** read-only health check covering 11 vault and `$HISTOS_HOME` checks (`vault_valid`, `v3_config`, `agents_md`, `legend`, `unclassified_cards`, `content_untouched`, `in_progress_model`, `done_when_missing`, `project_brief`, `histos_home`, `pause_active`) with exact repair commands.
- **`AGENTS.md` version stamp:** template carries `<!-- histos-template-version: ... -->` so `doctor` detects stale vault instructions.
- **Antigravity workflow documented:** parent wrapper + sibling code repo layout and native `AGENTS.md` discovery verified and added to the docs.

### v3.2.1 ([CHANGELOG](../CHANGELOG.md#321---2026-11-20))

- **A slimmer `AGENTS.md`:** roughly half the words (1779 → ~850), keeping the same rules so every session uses fewer tokens.
- **Agent estimates for cards without a tier:** untiered cards fall back to broader groups across all tiers (`estimates.py`).
- **`histos implement <id>` / `--done`:** records post-approval implementation time on code cards as part of the card's agent time in history and estimates.

### v3.2 ([v3.2.0](https://github.com/AIAYN-creator/Histos/releases/tag/v3.2.0))

Each person trains their own Histos — the first version planned with v3.1's interview and `plan propose`.

- **Estimates from your own history:** the agent's time and your active review time (minus `histos pause`), each grouped its own way, median and 80th percentile, in `histos status` and the card's frontmatter; "no data" until 5 similar cards are closed. `actual_duration_hours` on approval.
- **Measured fairly:** one card at a time per worker, so AFK batches aren't parallel work; three figures per closed card (agent, calendar review, active review).
- **`histos estimate --coverage`:** how close each group is to having enough data.
- **The critical path on the arrows**, in magenta, repainted on every change, never over your own colors; configurable in `histos.config.yaml`.

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

- **How much signal the tier heuristics can get from one-line card descriptions.** The calibration shows a reasonable split with visible misses; richer descriptions help, and calibrating from real outcomes (metrics shipped in [v3.3.0](#v330-changelog)) is the likely next step.
- **Project brief format — mostly resolved:** `PROJECT.md` (what) and `ARCHITECTURE.md` (how) change only through `histos guide`, and since v3.1 `PROJECT.md` has a required structure; whether `ARCHITECTURE.md` needs one too is still open.
- **Folder structure — partially resolved:** cards live in `content/<id>.md` (see [canvas-schema.md](canvas-schema.md)); additional subfolders like `content/drafts/` remain open.

## Scope and prior art

**What this repo publishes** is the **tool** (CLI, desktop app, vault format, agent instructions, documentation) as an open-source project, with a ready-to-run `.exe` of the desktop app on the [Releases](https://github.com/AIAYN-creator/Histos/releases) page. Git/GitHub is not the version-history backend for each user's project content — that's outside Histos's scope and is each user's own decision.

- **Kanvas (XMihura)** — direct architectural reference. Same pattern but code-oriented: a prompt + Python CLI the agent uses to touch the canvas, plus the `.canvas` itself. No SaaS, no build step, agent-agnostic. Histos started from that pattern for writing projects, and v3 brings it back to code too.
- **claude-canvas (AgriciDaniel)** — reference only for the auto-layout algorithm (`dagre`) and the idea of visual zones/groups. Not the base architecture. Histos's own `histos arrange` ended up in that same family (a Sugiyama-style layered layout), written from scratch in plain Python so it adds no dependency.
- **JSON Canvas spec (jsoncanvas.org)** — the `.canvas` format is the open JSON Canvas standard, not Obsidian-proprietary. Available node types: `text` (embedded markdown), `file` (path to a real vault file), `link` (URL), `group` (a purely geometric container — a node "belongs" to a group only if its coordinates fall inside the group's rectangle; there's no `parent_id` in the data).
