# Installing Histos

Histos has two front ends, and this page covers both — pick one, or install both and switch anytime (they do exactly the same things to your project; see [docs/usage.md](docs/usage.md) for how they compare day to day):

- **The terminal tool** (`histos`) — what an AI agent uses on your behalf, and works for you too if you're comfortable in a terminal.
- **The desktop app** (`histos-gui`) — a window with buttons: pick a project, review what's pending, approve or reject. No terminal needed *to use it* — see the honest caveat about *installing* it below.

## 1. Install Obsidian

Histos writes to an Obsidian vault — it doesn't replace or bundle Obsidian, and you need it either way to actually see your project board. Download it free from [obsidian.md](https://obsidian.md) and install it like any other program; no account needed. You'll open your Histos project folder in it later with "Open folder as vault" — see [docs/usage.md](docs/usage.md).

## 2. Install Python

Both Histos front ends need Python 3.10 or later. Get it from [python.org](https://www.python.org/downloads/) if you don't already have it. On the first install screen, tick **"Add python.exe to PATH"** — easy to miss, and without it the next steps won't find the `python`/`pip` commands.

> **Honest caveat:** a plain double-click `.exe` install that skips Python entirely isn't published yet (see [Building a standalone `.exe`](#building-a-standalone-exe) below for what exists today, and the note in step 4). Until it is, this Python step is unavoidable for either front end — including the desktop app.

## 3. Get Histos and install the terminal tool

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

If that prints a list of commands, you're done with the terminal tool.

## 4. Want the desktop app too?

```bash
pip install -e ".[gui]"
histos-gui
```

This opens the app in a window. Each time you want it afterward, run `histos-gui` again the same way — today, *launching* the app still needs one terminal command, even though *using* it once open doesn't. That gap is exactly what a real `.exe` release would close (step 2's caveat); until then, whoever does this install step is doing it once on behalf of the person who'll actually use the app day to day, non-technical or not.

## Building a standalone `.exe`

For handing the app to someone who shouldn't need to touch Python or a terminal at all — this is also what a future GitHub Release would contain, zipped up:

```bash
pip install -e ".[gui,packaging]"
pyinstaller --distpath packaging/dist --workpath packaging/build packaging/histos-gui.spec
```

The result lands in `packaging/dist/histos-gui/` — `histos-gui.exe` inside that folder is the one to run or share, but the **whole folder** needs to travel together (it holds the Python runtime, WebView2 bridge, and Histos's own bundled files), not just the `.exe` by itself.

## Something not working?

- **`histos`/`histos-gui`/`pyinstaller` not found** — usually means Python's `Scripts` folder isn't on your `PATH`. Reinstalling Python with "Add python.exe to PATH" ticked (step 2) fixes this for good; closing and reopening the terminal after installing also matters, since `PATH` changes don't reach already-open terminals.
- **Everything else** — [docs/usage.md](docs/usage.md) for day-to-day use, or the [README](README.md) for how Histos actually works.
