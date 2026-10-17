"""Renders docs/assets/histos-demo.gif: the v3 cycle on a sample paper project -- a terminal on
the left, the board on the right, recoloring as each command changes it (needs matplotlib).

Nothing in the terminal is typed by hand: the script builds a sample vault in a temp folder and
really runs every `histos` command, so the GIF shows exactly what Histos prints today. Only the
typing rhythm is simulated. Re-run it whenever a command's output changes.

    python scripts/make_demo_gif.py
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyBboxPatch, PathPatch, Polygon  # noqa: E402
from matplotlib.path import Path as CurvePath  # noqa: E402
from PIL import Image  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from histos import canvas, frontmatter  # noqa: E402
from make_readme_gif import PRESET, _blend, _curve  # noqa: E402  (same colors and arrows as the arrange GIF)

OUTPUT = ROOT / "docs" / "assets" / "histos-demo.gif"
WIDTH, HEIGHT, DPI = 1200, 640, 100
TERM_W = 700  # px of the terminal pane; the board takes the rest
CAPTION_BAND = 54
TERM_BG, TERM_FG, PROMPT, DIM = "#0d1117", "#e6edf3", "#3fb950", "#8b949e"
WRAP = 64  # terminal columns that fit the pane
MAX_LINES = 24

# A sample paper project -- public, made up: never data from a real vault.
PROJECT = [
    ("outline", "Outline", "Sections and the argument they build", []),
    ("related", "Related work", "Compare the three main approaches and their trade-offs", ["outline"]),
    ("intro", "Introduction", "Motivate the problem and state the contributions", ["outline"]),
    ("results", "Results", "What the experiments show", ["related", "intro"]),
]
TITLES = {cid: title for cid, title, _, _ in PROJECT}
DONE_TEXT = "A short, finished section of the sample paper, approved earlier.\n"
GOOD_DRAFT = ("# Related work\n\nThree families of methods address this problem: rule-based systems, "
              "statistical models and neural approaches. Each trades interpretability for coverage "
              "in a different way, and the paper positions itself between the last two.\n")
BAD_DRAFT = GOOD_DRAFT.replace("in a different way", "in a different way. TODO: add the 2024 survey")


class Vault:
    def __init__(self, path: Path):
        self.path = path
        self.env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONIOENCODING="utf-8", COLUMNS="200")

    def run(self, *argv: str) -> str:
        result = subprocess.run([sys.executable, "-m", "histos", *argv], cwd=self.path, env=self.env,
                                capture_output=True, text=True, encoding="utf-8")
        return (result.stdout + result.stderr).rstrip("\n")

    def write(self, name: str, text: str) -> str:
        path = self.path.parent / name
        path.write_text(text, encoding="utf-8")
        return str(path)

    def board(self) -> list[dict]:
        """Cards with their routing label ('reasoning · Opus 5.5'), read from their frontmatter."""
        cards = []
        for card in canvas.cards(canvas.load(self.path)):
            meta, _ = frontmatter.read(canvas.card_file_path(self.path, card))
            label = " · ".join(x for x in (meta.get("proposed_tier"), meta.get("approved_model")) if x)
            cards.append(dict(card, label=label))
        return cards


def build_sample(root: Path) -> Vault:
    vault = Vault(root / "sample-paper")
    vault.path.mkdir()
    vault.run("init")
    for cid, title, description, deps in PROJECT:
        argv = ["add-card", cid, "--title", title, "--description", description]
        vault.run(*argv, *(["--depends-on", *deps, "--authorized"] if deps else []))
    done = vault.write("done.md", DONE_TEXT)
    for cid in ("outline",):
        vault.run("assign", cid)
        vault.run("propose", cid, "--file", done)
        vault.run("approve", cid)
    vault.run("arrange")
    return vault


# ---- drawing ----------------------------------------------------------------------------------

def render(term_lines: list[tuple[str, str]], cards: list[dict], caption: str, cursor: bool) -> Image.Image:
    fig = plt.figure(figsize=(WIDTH / DPI, HEIGHT / DPI), dpi=DPI)
    fig.patch.set_facecolor("white")
    body_h = HEIGHT - CAPTION_BAND

    term = fig.add_axes((0, CAPTION_BAND / HEIGHT, TERM_W / WIDTH, body_h / HEIGHT))
    term.set_xlim(0, 1)
    term.set_ylim(0, 1)
    term.axis("off")
    term.add_patch(FancyBboxPatch((0.015, 0.03), 0.97, 0.94, boxstyle="round,pad=0,rounding_size=0.015",
                                  facecolor=TERM_BG, edgecolor="none", transform=term.transAxes))
    for i, color in enumerate(("#ff5f56", "#ffbd2e", "#27c93f")):
        term.add_patch(plt.Circle((0.045 + i * 0.03, 0.935), 0.009, color=color, transform=term.transAxes))
    line_h = 0.86 / MAX_LINES
    shown = term_lines[-MAX_LINES:]
    for i, (text, kind) in enumerate(shown):
        y = 0.885 - i * line_h
        color = {"cmd": TERM_FG, "out": TERM_FG, "dim": DIM, "fail": "#ff7b72", "pass": "#3fb950"}[kind]
        if kind == "cmd":
            term.text(0.04, y, "$", family="monospace", fontsize=11.5, color=PROMPT, va="top", weight="bold")
            text = text + ("▌" if cursor and i == len(shown) - 1 else "")
            term.text(0.065, y, text, family="monospace", fontsize=11.5, color=TERM_FG, va="top", weight="bold")
        else:
            term.text(0.04, y, text, family="monospace", fontsize=11.5, color=color, va="top")

    board = fig.add_axes((TERM_W / WIDTH, CAPTION_BAND / HEIGHT, 1 - TERM_W / WIDTH, body_h / HEIGHT))
    board.axis("off")
    x1 = min(c["x"] for c in cards) - 60
    y1 = min(c["y"] for c in cards) - 60
    x2 = max(c["x"] + c["width"] for c in cards) + 60
    y2 = max(c["y"] + c["height"] for c in cards) + 60
    pane_w, pane_h = WIDTH - TERM_W, body_h
    scale = min(pane_w / (x2 - x1), pane_h / (y2 - y1))
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    board.set_xlim(cx - pane_w / scale / 2, cx + pane_w / scale / 2)
    board.set_ylim(cy + pane_h / scale / 2, cy - pane_h / scale / 2)
    pt = 72 / DPI * scale
    by_id = {c["id"]: c for c in cards}
    for cid, _, _, deps in PROJECT:
        for dep in deps:
            points, (tx, ty, (nx, ny)) = _curve(by_id[dep], by_id[cid], "right", "left")
            board.add_patch(PathPatch(CurvePath(points, [CurvePath.MOVETO] + [CurvePath.CURVE4] * 3), fill=False,
                                      edgecolor="#a3a3a3", linewidth=max(0.6, 3.2 * pt), zorder=2))
            size = 18
            bx, by = tx + nx * (7 + size), ty + ny * (7 + size)
            board.add_patch(Polygon([(tx + nx * 2, ty + ny * 2), (bx - ny * size * 0.55, by + nx * size * 0.55),
                                     (bx + ny * size * 0.55, by - nx * size * 0.55)], closed=True,
                                    facecolor="#a3a3a3", edgecolor="none", zorder=2))
    for card in cards:
        color = PRESET[card["color"]]
        board.add_patch(FancyBboxPatch((card["x"], card["y"]), card["width"], card["height"],
                                       boxstyle="round,pad=0,rounding_size=14", facecolor=_blend(color, 0.16),
                                       edgecolor=color, linewidth=max(1.0, 5 * pt), zorder=3))
        title_y = card["y"] + card["height"] * (0.36 if card["label"] else 0.5)
        board.text(card["x"] + card["width"] / 2, title_y, TITLES[card["id"]], ha="center", va="center",
                   fontsize=max(5, 40 * pt), color="#222222", zorder=4)
        if card["label"]:  # inside the card: below it would run into the next one
            board.text(card["x"] + card["width"] / 2, card["y"] + card["height"] * 0.72, card["label"],
                       ha="center", va="center", fontsize=max(5, 24 * pt), color="#57606a", zorder=4)

    fig.text(0.02, CAPTION_BAND / HEIGHT / 2, caption, va="center", ha="left", fontsize=14, color="#222222")
    fig.canvas.draw()
    image = Image.frombuffer("RGBA", fig.canvas.get_width_height(), fig.canvas.buffer_rgba()).convert("RGB")
    plt.close(fig)
    return image


def classify_line(line: str) -> str:
    summary = re.match(r"evals: (\d+) of (\d+) passed", line)
    if summary:
        return "pass" if summary.group(1) == summary.group(2) else "fail"
    if "FAIL" in line or line.startswith("error"):
        return "fail"
    return "pass" if "PASS" in line else "out"


def output_lines(text: str, keep: int | None = None) -> list[tuple[str, str]]:
    lines = []
    for raw in text.splitlines():
        for piece in textwrap.wrap(raw, WRAP, subsequent_indent="    ", drop_whitespace=False) or [""]:
            lines.append((piece, classify_line(raw)))
    return lines[:keep] if keep else lines


# ---- the story ---------------------------------------------------------------------------------

class Film:
    def __init__(self, vault: Vault):
        self.vault = vault
        self.frames: list[tuple[Image.Image, int]] = []
        self.term: list[tuple[str, str]] = []
        self.caption = ""

    def scene(self, caption: str) -> None:
        self.caption = caption
        self.term = []

    def command(self, display: str, argv: list[str], keep: int | None = None, filt=None, hold: int = 1600) -> str:
        """Types `display`, really runs `argv`, shows its output (optionally filtered/trimmed)."""
        cards = self.vault.board()
        self.term.append(("", "cmd"))
        for n in (len(display) // 3, 2 * len(display) // 3, len(display)):
            self.term[-1] = (display[:n], "cmd")
            self.frames.append((render(self.term, cards, self.caption, cursor=True), 90))
        out = self.vault.run(*argv)
        shown = filt(out) if filt else out
        self.term += output_lines(shown, keep)
        self.frames.append((render(self.term, self.vault.board(), self.caption, cursor=False), hold))
        return out

    def hold(self, ms: int) -> None:
        self.frames.append((render(self.term, self.vault.board(), self.caption, cursor=False), ms))


def in_progress_only(status: str) -> str:
    lines = status.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("In progress"))
    end = next(i for i in range(start + 1, len(lines)) if not lines[i].startswith("  "))
    trimmed = [line.split("  -- ")[0] for line in lines[start:end]]
    return "\n".join(trimmed)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        vault = build_sample(Path(tmp))
        # Off-screen, for real: the subagent's card already has its tier and approved model.
        vault.run("classify", "intro")
        vault.run("model", "intro", "--proposed", "Haiku 5.5")
        vault.run("model", "intro", "--approved", "Haiku 5.5", "--authorized")
        good, bad = vault.write("related.md", GOOD_DRAFT), vault.write("related-v1.md", BAD_DRAFT)

        film = Film(vault)
        film.scene("Each card gets a tier from its context, with the reason")
        film.command("histos assign related --worker parent", ["assign", "related", "--worker", "parent"], hold=700)
        film.command("histos classify related", ["classify", "related"], hold=2600)

        film.scene("The agent proposes a model; nothing runs until you say yes")
        film.command('histos model related --proposed "Opus 5.5"', ["model", "related", "--proposed", "Opus 5.5"],
                     hold=1200)
        film.command('histos model related --approved "Opus 5.5" --authorized',
                     ["model", "related", "--approved", "Opus 5.5", "--authorized"], hold=2000)

        film.scene("A subagent takes another card, with a brief of its own")
        film.command("histos assign intro --worker sub-intro", ["assign", "intro", "--worker", "sub-intro"], hold=700)
        film.command("histos context intro --for-subagent", ["context", "intro", "--for-subagent"], keep=4, hold=1800)
        film.command("histos status", ["status"], filt=in_progress_only, hold=2600)

        film.scene("Evals filter obvious junk before it reaches you")
        film.command("histos propose related --file related.md", ["propose", "related", "--file", bad], hold=2600)
        film.command("histos propose related --file related.md", ["propose", "related", "--file", good], hold=2200)

        film.scene("You review it -- the evals on top -- and decide")
        film.command("histos diff related", ["diff", "related"], keep=16, hold=3000)
        film.command("histos approve related", ["approve", "related"], hold=3500)

    frames = film.frames
    # The palette has to see every board color (yellow is only on screen for a moment), so it is
    # built from all the frames that are held on screen.
    held = [image for image, ms in frames if ms >= 700]
    sample = Image.new("RGB", (WIDTH * len(held), HEIGHT))
    for i, image in enumerate(held):
        sample.paste(image, (WIDTH * i, 0))
    # Thin card borders are too few pixels for median cut to keep them apart (yellow came out
    # orange), so the exact state colors and their fills are added to the palette by hand.
    exact = [PRESET[c] for c in PRESET] + ["#%02x%02x%02x" % tuple(round(v * 255) for v in _blend(PRESET[c], 0.16))
                                          for c in PRESET] + [TERM_BG, TERM_FG, PROMPT, DIM, "#ff7b72", "#57606a"]
    base = sample.quantize(colors=256 - len(exact), method=Image.Quantize.MEDIANCUT).getpalette()
    base = base[:(256 - len(exact)) * 3]
    for color in exact:
        base += [int(color[i:i + 2], 16) for i in (1, 3, 5)]
    palette = Image.new("P", (1, 1))
    palette.putpalette(base)
    images = [image.quantize(palette=palette, dither=Image.Dither.NONE) for image, _ in frames]
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    images[0].save(OUTPUT, save_all=True, append_images=images[1:], duration=[d for _, d in frames], loop=0,
                   optimize=True)
    print(f"wrote {OUTPUT.relative_to(ROOT)} ({OUTPUT.stat().st_size // 1024} KB, {len(images)} frames, "
          f"{sum(d for _, d in frames) / 1000:.1f} s)")


if __name__ == "__main__":
    main()
