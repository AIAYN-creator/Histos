# Contributing to Histos

Thanks for using Histos and taking the time to contribute!

Histos stays deliberately small, local, and auditable. Before writing code, take a quick look at our design boundaries so your pull request fits cleanly.

## Core principles

1. **The agent proposes, the human approves.** Never bypass `--authorized` guards, eval filters, or `approve` / `reject` separation ([docs/trust-model.md](docs/trust-model.md)).
2. **Local-first and agent-agnostic.** State lives in standard JSON Canvas (`project.canvas`), Markdown frontmatter (`content/*.md`), and append-only logs (`.histos/events.jsonl`). Histos does **not** run a remote server, call model APIs, or lock you into one AI tool.
3. **Deterministic core.** Keep shared vault logic inside `src/histos/operations.py` (structured dataclasses, no `print()`, no `argparse`, no `Path.cwd()`), using `@locked` and atomic writes (`write_text_atomic` / `copy_file_atomic`) for every vault mutation.
4. **No circular imports.** Every module under `src/histos/` must import cleanly on its own in a fresh interpreter (enforced by `tests/test_imports.py`).

## Ways to contribute

- **Bug reports:** open an issue with your OS, Python version, `histos --version`, the command or GUI step you ran, and a minimal set of steps to reproduce.
- **Docs & examples:** typo fixes, clearer explanations, and schema clarifications in [docs/](docs/) are always welcome.
- **Code & tests:** bug fixes and roadmap features aligned with [docs/roadmap.md](docs/roadmap.md). For larger features or schema changes, open an issue first to align on design before writing a big patch.

## Local development setup

Histos targets **Python >= 3.10** (CI runs Python 3.10 and 3.12 on Windows and Linux).

```bash
git clone https://github.com/AIAYN-creator/Histos.git
cd Histos
pip install -e ".[dev]"
```

If you are working on the desktop app (`histos-gui`):

```bash
pip install -e ".[dev,gui]"
```

## Running checks

Run these locally before opening a pull request:

```bash
# Fast test suite (skips slow multi-process stress tests)
pytest -m "not slow"

# Full test suite
pytest

# Check all relative links and heading anchors in README + docs/
python scripts/check_links.py

# Headless smoke test for the desktop app (if touching gui/)
HISTOS_GUI_SELFTEST=1 python -m histos.gui.app
```

## Pull request guidelines

- **Keep changes focused:** one clear bug fix or feature per PR, with code and comments in English inside `src/histos/` and `tests/`.
- **Tests required:** add deterministic unit/CLI tests in `tests/` for every behavior change or bug fix.
- **Schemas and docs in sync:** if you change `histos.config.yaml`, frontmatter, canvas, or `.histos/events.jsonl`, update the corresponding JSON Schema in `src/histos/schema/` and document it in [docs/canvas-schema.md](docs/canvas-schema.md).
- **Generated files:** do not hand-edit [`examples/sample-paper/`](examples/sample-paper/) — use [`scripts/make_example_vault.py`](scripts/make_example_vault.py) if example updates are needed.
- **Agent-assisted PRs:** feel free to include `Supervised-by:` / `Coordinated-by:` trailers if your workflow uses them, and verify every test run yourself before submitting.

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
