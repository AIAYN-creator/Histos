"""Draws the Histos logo as SVG: an "H" made of the board itself -- two columns of cards in
the state colors, joined by a dependency arrow -- next to the word "histos".

The word is converted to outlines (matplotlib's bundled DejaVu Sans Bold), so it looks the same
everywhere, with no font needed. Writes docs/assets/histos-logo-{light,dark}.svg (for light and
dark backgrounds; the README picks one with <picture>) and docs/assets/histos-mark.svg.

    python scripts/make_logo.py
"""
from __future__ import annotations

from pathlib import Path

from matplotlib.font_manager import FontProperties
from matplotlib.path import Path as MplPath
from matplotlib.textpath import TextPath

ASSETS = Path(__file__).resolve().parents[1] / "docs" / "assets"

# Obsidian canvas presets -- the same colors Histos uses for card states.
GREEN, YELLOW, ORANGE, PURPLE = "#08b94e", "#e0ac00", "#ec7500", "#7852ee"
INK = {"light": "#1f2328", "dark": "#e6edf3"}  # GitHub's text colors on each theme
ARROW = {"light": "#57606a", "dark": "#9198a1"}

CARD_W, CARD_H, RADIUS = 34, 31, 6
LEFT_X, RIGHT_X = 8, 78
ROWS = (8, 44.5, 81)  # small gaps: each column reads as one stroke of the H


def mark(theme: str) -> str:
    """The H: approved work on the left, work in flight on the right, the arrow between them."""
    left = [GREEN, GREEN, GREEN]
    right = [YELLOW, ORANGE, PURPLE]
    parts = []
    for x, colors in ((LEFT_X, left), (RIGHT_X, right)):
        for y, color in zip(ROWS, colors):
            parts.append(f'<rect x="{x}" y="{y}" width="{CARD_W}" height="{CARD_H}" rx="{RADIUS}" fill="{color}"/>')
    y_mid = ROWS[1] + CARD_H / 2
    x0, x1 = LEFT_X + CARD_W + 4, RIGHT_X - 4
    stroke = ARROW[theme]
    parts.append(f'<path d="M{x0} {y_mid} H{x1 - 7}" stroke="{stroke}" stroke-width="5" stroke-linecap="round" fill="none"/>')
    parts.append(f'<path d="M{x1 - 9} {y_mid - 8} L{x1} {y_mid} L{x1 - 9} {y_mid + 8} Z" fill="{stroke}" '
                 f'stroke="{stroke}" stroke-width="2" stroke-linejoin="round"/>')
    return "\n  ".join(parts)


def word_path(text: str, size: float) -> tuple[str, float, float]:
    """SVG path data for `text`, flipped to SVG coordinates; returns (d, width, height)."""
    tp = TextPath((0, 0), text, size=size, prop=FontProperties(family="DejaVu Sans", weight="bold"))
    ext = tp.get_extents()
    commands = []
    verts, codes = tp.vertices, tp.codes
    i = 0
    while i < len(verts):
        code = codes[i]
        x, y = verts[i][0] - ext.x0, ext.y1 - verts[i][1]
        if code == MplPath.MOVETO:
            commands.append(f"M{x:.2f} {y:.2f}")
        elif code == MplPath.LINETO:
            commands.append(f"L{x:.2f} {y:.2f}")
        elif code == MplPath.CURVE3:
            (cx, cy), (ex, ey) = verts[i], verts[i + 1]
            commands.append(f"Q{cx - ext.x0:.2f} {ext.y1 - cy:.2f} {ex - ext.x0:.2f} {ext.y1 - ey:.2f}")
            i += 1
        elif code == MplPath.CURVE4:
            (c1x, c1y), (c2x, c2y), (ex, ey) = verts[i], verts[i + 1], verts[i + 2]
            commands.append(f"C{c1x - ext.x0:.2f} {ext.y1 - c1y:.2f} {c2x - ext.x0:.2f} {ext.y1 - c2y:.2f} "
                            f"{ex - ext.x0:.2f} {ext.y1 - ey:.2f}")
            i += 2
        elif code == MplPath.CLOSEPOLY:
            commands.append("Z")
        i += 1
    return " ".join(commands), ext.width, ext.height


def logo(theme: str) -> str:
    d, w, h = word_path("histos", 64)
    mark_size = 120
    gap = 26
    text_x = mark_size + gap
    text_y = (mark_size - h) / 2 + 6  # optical centering: the word has ascenders, no descenders
    width = text_x + w + 8
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.0f} {mark_size}" '
        f'width="{width:.0f}" height="{mark_size}" role="img" aria-label="Histos">\n'
        f"  <title>Histos</title>\n"
        f"  {mark(theme)}\n"
        f'  <path transform="translate({text_x} {text_y:.2f})" fill="{INK[theme]}" d="{d}"/>\n'
        f"</svg>\n"
    )


def mark_only() -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120" width="120" height="120" '
        'role="img" aria-label="Histos">\n'
        "  <title>Histos</title>\n"
        f"  {mark('light')}\n"
        "</svg>\n"
    )


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    for theme in ("light", "dark"):
        (ASSETS / f"histos-logo-{theme}.svg").write_text(logo(theme), encoding="utf-8")
    (ASSETS / "histos-mark.svg").write_text(mark_only(), encoding="utf-8")
    print(f"wrote histos-logo-light.svg, histos-logo-dark.svg and histos-mark.svg to {ASSETS}")


if __name__ == "__main__":
    main()
