# Installing Histos

Histos has two front ends, and this page covers both — pick one, or install both and switch anytime (they do exactly the same things to your project; see [docs/usage.md](docs/usage.md) for how they compare day to day):

- **The desktop app** (`histos-gui`) — a window with buttons: pick a project, review what's pending, approve or reject. Just download and run it, no Python or terminal needed.
- **The terminal tool** (`histos`) — what an AI agent uses on your behalf, and works for you too if you're comfortable in a terminal.

## 1. Install Obsidian

Histos writes to an Obsidian vault — it doesn't replace or bundle Obsidian, and you need it either way to actually see your project board. Download it free from [obsidian.md](https://obsidian.md) and install it like any other program; no account needed. You'll open your Histos project folder in it later with "Open folder as vault" — see [docs/usage.md](docs/usage.md).

## 2. Just want the desktop app?

**[Download `histos-gui.exe`](https://github.com/AIAYN-creator/Histos/releases/latest/download/histos-gui.exe)** (Windows) from the [latest release](https://github.com/AIAYN-creator/Histos/releases/latest). One file — save it anywhere, double-click to run it, nothing else to install. Windows SmartScreen may warn you the first time ("Windows protected your PC") because the file isn't signed with a paid certificate — click **More info → Run anyway** if you got it from the link above.

That's it — skip ahead to [docs/usage.md](docs/usage.md). The rest of this page is for the terminal tool, or for building the app yourself from source.

## 3. Terminal tool: install Python

The terminal tool (and building the desktop app yourself, see the end of this page) needs Python 3.10 or later. Get it from [python.org](https://www.python.org/downloads/) if you don't already have it. On the first install screen, tick **"Add python.exe to PATH"** — easy to miss, and without it the next steps won't find the `python`/`pip` commands.

## 4. Get Histos and install the terminal tool

Not published on PyPI yet, so install it from the source code. Open a terminal (Windows: search "Terminal" or "PowerShell" in the Start menu) and run:

```bash
git clone https://github.com/AIAYN-creator/Histos.git
cd Histos
pip install -e .
```

No `git` installed? Download the ZIP instead — the green **Code** button on the [GitHub page](https://github.com/AIAYN-creator/Histos) → **Download ZIP** — unzip it, then open a terminal in that unzipped folder before running `pip install -e .`.

Check it worked:

```bash
histos --help
```

If that prints a list of commands, you're done.

## Running the desktop app from source instead of the downloaded `.exe`

```bash
pip install -e ".[gui]"
histos-gui
```

Useful if you're developing Histos itself; for normal use, the downloaded `.exe` from step 2 is simpler.

## Building a standalone `.exe` yourself

This is what produces the file offered in step 2 — useful if you want to build it from a specific commit, or verify it yourself instead of trusting the published one:

```bash
pip install -e ".[gui,packaging]"
pyinstaller --distpath packaging/dist --workpath packaging/build packaging/histos-gui.spec
```

The result is a single file: `packaging/dist/histos-gui.exe`.

## Something not working?

- **`histos`/`histos-gui`/`pyinstaller` not found** (terminal-tool path) — usually means Python's `Scripts` folder isn't on your `PATH`. Reinstalling Python with "Add python.exe to PATH" ticked (step 3) fixes this for good; closing and reopening the terminal after installing also matters, since `PATH` changes don't reach already-open terminals.
- **The downloaded `.exe` won't run / SmartScreen blocks it entirely** — see step 2's note. If "Run anyway" isn't offered at all, your organization's security policy may be blocking unsigned executables outright — check with whoever manages the machine, or use the terminal-tool path instead.
- **Everything else** — [docs/usage.md](docs/usage.md) for day-to-day use, or the [README](README.md) for how Histos actually works.
