# Changelog

All notable changes to Histos. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/). Each released version has its [GitHub Release](https://github.com/AIAYN-creator/Histos/releases) with the desktop app attached.

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

[3.0.0]: https://github.com/AIAYN-creator/Histos/releases/tag/v3.0.0
[2.1.1]: https://github.com/AIAYN-creator/Histos/releases/tag/v2.1.1
[2.1.0]: https://github.com/AIAYN-creator/Histos/releases/tag/v2.1.0
[2.0.0]: https://github.com/AIAYN-creator/Histos/releases/tag/v2.0.0
