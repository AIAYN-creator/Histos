# Changelog

All notable changes to Histos. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/). Each released version has its [GitHub Release](https://github.com/AIAYN-creator/Histos/releases) with the desktop app attached.

## [3.2.0] - 2026-11-08

Each person trains their own Histos: time estimates learned from *your* closed cards -- never the agent's guess -- and the critical path drawn on the canvas arrows. Planned, specified and approved card by card in a Histos vault, the first one built with v3.1's interview and `plan propose`.

### Added

- **Estimates in `histos status`**, two per open card, kept apart: the agent's working time and the time the card waits for your review -- your *active* time, without the hours you were paused. `agent ~25 min (≤50 min) · review ~3 h (≤1 d)`: the median, and the 80th percentile as the "if it goes badly". They come from your closed cards in all your vaults: the agent's grouped by tier, card type and number of dependencies, the review's by card type, falling back to broader groups; at least 5 cards, the 30 most recent. Below that, "no data (N more needed)" -- never an invented number. A card's type is its id prefix, only in vaults that really use prefixes.
- **Measured fairly** (`history.py`): an agent works one card at a time, so a batch assigned before you leave isn't counted as parallel work; subagents are; a takeover doesn't stop the clock; waiting for your `retry` is review time; time in Backlog or Blocked counts for neither. Three figures per closed card: agent, review on the calendar, and active review.
- **In the frontmatter**: `estimate` (both estimates and what they're based on) and `estimated_duration_hours` on open cards; `actual_duration_hours` filled on approval with what the card really took (agent + active review) -- the two fields reserved since v1.
- **`histos estimate --coverage`**: how many closed cards each group has and how many it still needs, and how many of your open cards would get an estimate right now. Read-only.
- **The critical path on the canvas arrows**, in magenta (off the six status colors), repainted after every command that changes the board, and named on the last line of `histos status`. Each card weighs what it still needs; with no history yet it's the longest chain of cards. Histos notes the arrows it paints (`.histos/critical-path.json`) and never touches one you colored. `histos.config.yaml` gets an optional `critical_path` section: `enabled`, `color`, and `review: active | calendar`. The canvas legend gains a line for it; a legend you edited is left alone.
- `scripts/make_history.py`: synthetic histories with known times, to try estimates and the critical path without waiting for real data -- it refuses to write anywhere but an empty folder.

### Changed

- `AGENTS.md`: the agent never estimates time itself (`histos status` has the estimates, or says there's no data yet) and never touches arrow colors.
- `histos status` shows each open card's estimates; approving a card fills `actual_duration_hours`; every command that changes the canvas may repaint arrows on the critical path.

### Notes

- **Built for starting with no data.** Every new user starts with an empty history: estimates say "no data (N more needed)" instead of guessing, fill in by themselves as cards close, and fall back to broader groups to appear sooner; the critical path works from day one by counting cards; `--coverage` shows how close each group is. Released with 11 closed cards in its author's history -- enough for review estimates and for `exec` agent estimates; checking how accurate the estimates are on real data comes with use.
- **Caught before release by the scale tests:** with 2000 closed cards in 5 vaults, `histos status` took 18 s and `--coverage` 6 s. Now under 1 s: each estimate group's cards are sorted once, pauses are looked up by bisection, the history is read once per command, and frontmatter is parsed with PyYAML's C loader when available.
- **What the first numbers showed:** in a code project, a card's measured agent time is the *proposal* (the spec), not the implementation, which happens after approval -- so the agent's times there are minutes. In writing projects, where the approved text is the result, they measure the real work. A measured implementation phase for code cards is an idea for v3.3.

## [3.1.2] - 2026-11-08

### Added

- **`histos pause`: you're away.** Going to sleep, onto something else, leaving the agent an AFK queue -- run it once, from any vault. Until your next decision (`approve`, `reject`, `retry`, `accept-edit`, a guide's or a plan's approve/reject, `arrange`, from the terminal or the desktop app), the time cards wait for your review isn't your reviewing time, in any vault. There's no resume command: deciding something means you're back. Commands the agent runs too (`status`, `context`...) never end a pause, and the agent's own time keeps counting. The pauses are kept in `~/.histos/pauses.jsonl`, next to the vault registry, so the estimates coming in 3.2 can tell the hours you reviewed from the hours you were away. `histos status` shows when you're paused; `AGENTS.md` makes `pause` a human-only command and has the agent remind you of it before an AFK queue.

## [3.1.1] - 2026-11-08

### Fixed

- **`histos model <id> --approved ... --authorized` now works on a card that's already Approved, as long as it has no approved model yet.** The human often says yes to the model and approves the card in the same message, so the card could turn green before the agent recorded that yes -- and the model was lost. Now it's recorded (with its `model_approved` event), so the history v3.2 learns from knows which model did each card. Once a card has an approved model, it stays as it is; `--proposed` and `--clear` are still refused on approved cards. `AGENTS.md` says so in step 5 of "Working a card".

## [3.1.0] - 2026-11-08

A stricter start to every project, and history recorded from day one. Projects went back and forth because the idea never got across well at the start: the agent now interviews like a consultant, the brief has a required structure, and the cards are proposed as one plan you approve or reject as a whole. And the estimates, critical path and reports planned for the next versions need history, so every change to the vault is now recorded.

### Added

- **Event log.** Every command that changes the vault -- from the terminal or the desktop app -- appends what it did to `.histos/events.jsonl`: who, when, which card, and every status change it caused in *any* card (approving a card records the dependents it unblocked). Colors changed by hand in Obsidian are recorded by the next command, with the time Histos saw them. One JSON object per line, written inside the vault lock with `fsync`; the format is in `histos-events.schema.json` and `docs/canvas-schema.md`, and `histos validate` checks it. Read-only commands record nothing.
- **Plans.** `histos plan propose --file plan.yaml` proposes the whole card table at once -- a summary, and per card its title, dependencies and a `done_when` -- and no card exists until the human runs `histos plan approve`, which creates them all in one canvas save, all or nothing. `histos plan diff` shows the table, the plan by levels (what can run in parallel) and warnings: loose cards, a card depending on more than 5, a `done_when` nobody could check. Plans that can't be created -- cycles, unknown dependencies, repeated or existing ids, a typo in a key -- are refused on propose with every error at once. `histos plan reject --feedback "..."` sends it back.
- **`done_when`**, a checkable criterion for when a card is done: in the plan, or `add-card --done-when "..."`. It goes into the card's frontmatter and initial body, and `histos context` shows it.
- **Evals on guide proposals.** `histos guide propose` runs the evals whose `only` names the file (`only: ["PROJECT.md"]`); a failing draft isn't saved, and `guide diff` shows what the evals said. `histos eval PROJECT.md --file <draft>` is the dry run. Evals without `only` never apply to a guide, so existing vaults behave the same.
- **`evals/guides.yaml`**: the required sections of `PROJECT.md` -- goal, deliverable, audience, out of scope, done when, constraints, *what I understood*, *what I'd change and why*, decisions -- in Spanish or English, with a minimum of words in the two sections the agent writes itself.
- **The `min_section_words` check**, and alternatives in `required_headings` (`["## Objetivo", "## Goal"]`, any one counts). Headings now compare without accents too.
- **The decisions log.** Rejecting a plan or a guide change *with* `--feedback` appends a dated line to the decisions section of `PROJECT.md` -- only the human's own text, only appended, the previous version kept in `approved/guides/` -- so no agent proposes again what was already turned down. Without that section, it goes to `.histos/decisions.md`, which `histos context` shows until the agent carries it into `PROJECT.md`.
- A test that fails if `AGENTS.md` or the command reference name a command or flag that doesn't exist, and the concurrency stress test now checks the event log too.

### Changed

- **`AGENTS.md`: the interview.** On an empty vault the agent listens, asks in rounds of at most five questions (the ones that could sink the project first), pushes back on the approach, pipeline, stack or scope with a reason and a concrete alternative for each objection, writes `PROJECT.md` for the human to approve, and only then proposes the plan. `plan approve` and `plan reject` join the human-only commands. `init` never overwrites an existing `AGENTS.md`: copy the new one into older vaults by hand.
- `histos guide propose` can now refuse a draft that fails the evals.
- `histos init --add-config` adds `evals/guides.yaml` to an `evals/` folder that already exists -- it only applies to the guides, so it can't change how any card is checked.
- The README is now a short front page; the detail moved to `docs/how-it-works.md`, `docs/cli.md`, `docs/trust-model.md` and `docs/roadmap.md`.

### Notes

- **What Histos writes outside the vault:** `~/.histos/vaults.json`, the list of vaults that recorded events, so later versions can learn from all your projects. Set `HISTOS_HOME` to keep it elsewhere. Nothing leaves your machine. If a vault is in git, commit its `.histos/` folder: it's the project's history.
- Concurrency stress test with the new event-log invariants, on Windows: 8 agents × 20 rounds, 160 commands, nothing lost, duplicated or interleaved, and the logged transitions rebuild the final board exactly.
- The tests on Linux caught a bug before it shipped: on Linux, recording a decision would have rewritten a `PROJECT.md` made on Windows with Unix line endings. `PROJECT.md` is now edited byte for byte on every OS. And the end-to-end test of the whole start cycle -- brief, rejections, plans, approval -- caught `histos context` showing a decision twice once the agent had carried it into `PROJECT.md`; fixed before release too. And the packaged desktop app's selftest caught a circular import that only appeared when `histos.config` was imported first, as the app does; every module is now tested importing on its own.
- Like v3, this release was specified, reviewed and approved card by card in a Histos vault.

## [3.0.0] - 2026-10-17

Model routing by tier, subagents with an isolated context, and deterministic evals as a filter before human review. The core rule doesn't change: the agent proposes, the human approves.

### Added

- **Tiers.** `histos classify <id>` puts a card in one of three fixed tiers (`fast`, `exec`, `reasoning`) from its context -- its description, dependencies and sources, never a draft -- and says why, rule by rule. The rules live in a new per-vault `histos.config.yaml`: a closed catalog of named conditions (`token_count >= 3000`, `code_blocks_present`, `architecture_terms_present`...), no `eval()`. The defaults were calibrated on ~400 real cards, writing and code. `--dry-run` measures without writing.
- **Model approval.** `histos model <id> --proposed "..."` records the agent's proposal; `--approved "..." --authorized` records the human's yes, like `--authorized` for dependencies. Several cards at once, to approve a whole queue before leaving the agent alone. Histos never knows which models exist: the agent translates tier to model for its platform.
- **Subagents.** `histos context <id> --for-subagent` gives a self-contained brief for a subagent that inherits nothing from its parent: what to do and when to stop, `AGENTS.md`, the card, its approved dependencies, and the guidance files. `histos assign <id> --worker <name>` records who works a card.
- **Evals.** YAML files in `evals/` run on every `propose` before a card reaches review: `word_count`, `no_placeholders`, `required_headings` and `content_untouched`, each with `only`/`except` card id patterns. A failed eval keeps the card In progress with the reason; three failed attempts and it waits for the human (`histos retry <id>` gives three more). `histos eval <id> --file <draft>` checks a draft without spending an attempt, and `histos diff` shows which evals the proposal passed.
- **Direct-write detection.** Every legitimate write of a card's body (`add-card`, `approve`) records a `content_hash`; the `content_untouched` eval catches anything that changed `content/` behind Histos's back, whatever the agent. `histos accept-edit <id>` accepts the human's own hand edits.
- **`ARCHITECTURE.md`**, an optional guidance file next to `PROJECT.md`, included in `histos context`.
- **`histos guide propose|diff|approve|reject`** for `PROJECT.md` and `ARCHITECTURE.md`: the agent proposes changes, the human approves them, and each replaced version is kept in `approved/guides/`.
- **`histos init --add-config`** brings an existing vault to v3 (adds `histos.config.yaml` and `evals/`, touches nothing else).
- `histos status` shows each card's tier and model, and lists the models still waiting for approval.
- `histos validate` also checks the cards' frontmatter, `histos.config.yaml` and `evals/`.
- A complete sample vault in `examples/sample-paper/`, a v3 demo GIF in the README, a logo, and `docs/workflow.md`.
- Scripts: `make_example_vault.py`, `make_demo_gif.py`, `make_logo.py`, `calibrate_tiers.py`, `stress_parallel.py`, `check_links.py`.
- CI: the tests run on Windows and Linux, Python 3.10 and 3.12, on every push and pull request.

### Changed

- **Concurrent agents are safe.** Every command that changes the vault holds an OS file lock (`.histos.lock`) from before it reads to after it saves, so a parent and a subagent working at the same time never overwrite each other's changes; the lock is released by the OS even if a process dies. Every file is written atomically (temp file + replace).
- `histos assign` refuses a card that is already In progress for someone else; `--takeover` takes it over explicitly.
- `AGENTS.md` describes the v3 workflow; `histos.config.yaml`, `evals/`, `PROJECT.md` and `ARCHITECTURE.md` are added to Claude Code's deny list. `init` never overwrites an existing `AGENTS.md`: copy the new one by hand into older vaults.
- The command implementations moved from `cli.py` into `histos/commands/`; the code and its comments are now English throughout.

- `histos --version`; the version now lives only in `histos/__init__.py` (it said `0.1.0` there since v1).
- `histos validate` reports cards whose `.md` file is missing; a missing or malformed card file is a clear error in every command, never a traceback.

### Fixed

- Term conditions now match plurals ("trade-offs" for "trade-off").
- A failed eval no longer repeats its detail twice on the same line.

### Security

Found in the v3 audit (dependency scan, static analysis, manual review of every path and file the CLI touches):

- **A card's file could point outside the vault on Windows.** The schema only forbade `/` in the file name, but a backslash is a separator on Windows too: a hand-edited canvas with `content/..\..\x.md` passed `validate`, and `approve` wrote the draft there. The schema now refuses `/`, `\` and `:`, and every command checks that a card's resolved path is directly inside `content/`.
- **The vault lock file is never followed through a symbolic link** — a symlinked `.histos.lock` would have had its target truncated and overwritten. It's refused, and opened with `O_NOFOLLOW` where available.
- **The desktop app only ever passes folders to `os.startfile`**, which would run a file if given one. (The app inserts all agent-written text with `textContent`, never as HTML, so there was no injection path to it; this is defense in depth.)
- Dependencies: no known vulnerabilities (`pip-audit`).

## [2.1.1] - 2026-10-15

### Added

- `histos context` shows the card's own rejection feedback (`status_note`).

### Changed

- Docs match the code: the cyan "Dependency change request" state and the two duration fields are reserved, nothing sets them yet.

## [2.1.0] - 2026-09-24

### Added

- `histos arrange` re-lays out the whole board on request: dependency columns ordered to minimize crossings, a free corridor wherever an arrow skips a column (no arrow is ever drawn across a card), "Not connected" and "Done" groups, and redundant arrows removed into `implied_dependencies`. The previous layout is saved as `project.canvas.bak`.
- `AGENTS.md` rule: never run `arrange` unless asked.

### Changed

- Arrows always leave a card's right side and enter the next card's left side.
- New cards land next to their dependencies instead of in the first free slot.

### Fixed

- `histos context` no longer crashes on Windows when its output is piped and a card has characters outside cp1252.

## [2.0.0] - 2026-08-20

### Added

- Desktop app (`histos-gui`, downloadable `.exe`) for the human side: pick a vault, start one, a status overview, approve or reject with a plain before/after comparison. Built on `operations.py`, the same code the CLI calls.

### Changed

- Adding, linking or describing a card never moves the cards already on the board.
- Claude Code's deny list also covers `project.canvas` and every registered source file.
- Code, docs and agent templates translated to English; new vaults use `proposals/` and `approved/` (older ones keep their folder names).

### Security

- Dependency scan, path-traversal review, and no `eval`/`pickle`/unsafe YAML anywhere; one risk deliberately left open and documented in the README's Trust model.

## 1.0 - 2026-08-06 (not tagged)

First version: the formal `.canvas` schema, card frontmatter, the CLI (`init`, `add-card`, `link`, `describe`, `assign`, `context`, `propose`, `diff`, `approve`, `reject`, `status`, `validate`) and the agent instructions (`AGENTS.md`/`CLAUDE.md`).

[3.2.0]: https://github.com/AIAYN-creator/Histos/releases/tag/v3.2.0
[3.1.2]: https://github.com/AIAYN-creator/Histos/releases/tag/v3.1.2
[3.1.1]: https://github.com/AIAYN-creator/Histos/releases/tag/v3.1.1
[3.1.0]: https://github.com/AIAYN-creator/Histos/releases/tag/v3.1.0
[3.0.0]: https://github.com/AIAYN-creator/Histos/releases/tag/v3.0.0
[2.1.1]: https://github.com/AIAYN-creator/Histos/releases/tag/v2.1.1
[2.1.0]: https://github.com/AIAYN-creator/Histos/releases/tag/v2.1.0
[2.0.0]: https://github.com/AIAYN-creator/Histos/releases/tag/v2.0.0
