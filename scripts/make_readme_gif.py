"""Renders docs/assets/histos-arrange.gif: a sample board before and after `histos arrange` (needs matplotlib)."""
from __future__ import annotations

import copy
import math
import sys
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
from histos import canvas  # noqa: E402

OUTPUT = ROOT / "docs" / "assets" / "histos-arrange.gif"
WIDTH, HEIGHT, DPI = 1000, 600, 100
CAPTION_BAND, LEGEND_BAND = 58, 46
TRANSITION_FRAMES = 22

# Obsidian's light-theme canvas color presets.
PRESET = {"1": "#e93147", "2": "#ec7500", "3": "#e0ac00", "4": "#08b94e", "5": "#00bfbc", "6": "#7852ee"}
LEGEND = [
    ("6", "Backlog"), ("2", "In progress"), ("1", "Blocked"),
    ("3", "Proposal pending review"), ("5", "Dependency change request"), ("4", "Approved"),
]

# A sample paper project: (id, title, status color, dependencies), in the order the cards were created.
PROJECT = [
    ("scope", "Scope", "4", []),
    ("venue", "Pick a journal", "4", []),
    ("outline", "Outline", "4", ["scope", "venue"]),
    ("related", "Related work", "4", ["scope"]),
    ("method", "Method", "4", ["outline"]),
    ("intro", "Introduction", "3", ["outline", "related"]),
    ("dataset", "Dataset", "4", ["method"]),
    ("experiments", "Experiments", "2", ["method", "outline"]),
    ("results", "Results", "1", ["experiments", "dataset"]),
    ("figures", "Figures", "5", ["experiments"]),
    ("abstract", "Abstract", "1", ["intro", "results", "outline"]),
    ("submit", "Submit", "1", ["abstract", "figures", "venue"]),
    ("template", "LaTeX template", "4", []),
    ("cover", "Cover letter", "6", []),
    ("reviewers", "Suggest reviewers", "6", []),
]
# Created on their own first and linked to their dependencies later -- linking never moves a card.
LINKED_LATER = {"related", "dataset", "figures", "abstract"}
TITLES = {cid: title for cid, title, _, _ in PROJECT}

OLD_COLUMN_STEP = 340  # the pre-arrange placement: one column per longest-path rank, first free slot down
NORMAL = {"right": (1, 0), "left": (-1, 0), "top": (0, -1), "bottom": (0, 1)}


def _edge(source: str, target: str) -> dict:
    return {"id": f"{source}->{target}", "fromNode": source, "toNode": target}


def _old_rank(data: dict, cid: str) -> int:
    parents = [e["fromNode"] for e in data["edges"] if e["toNode"] == cid]
    return 1 + max(_old_rank(data, p) for p in parents) if parents else 0


def _overlaps(a: dict, b: dict) -> bool:
    return (a["x"] < b["x"] + b["width"] and b["x"] < a["x"] + a["width"]
            and a["y"] < b["y"] + b["height"] and b["y"] < a["y"] + a["height"])


def _frozen_sides(source: dict, target: dict) -> tuple[str, str]:
    """Obsidian's guess for a side-less edge, fixed in the file the first time it saves."""
    dx = (target["x"] + target["width"] / 2) - (source["x"] + source["width"] / 2)
    dy = (target["y"] + target["height"] / 2) - (source["y"] + source["height"] / 2)
    if abs(dx) > abs(dy):
        return ("right", "left") if dx > 0 else ("left", "right")
    return ("bottom", "top") if dy > 0 else ("top", "bottom")


def before_board() -> dict:
    data: dict = {"nodes": [], "edges": []}
    linked_later = []
    for cid, _, color, deps in PROJECT:
        card = {"id": cid, "type": "file", "x": 0, "y": 0, "width": 280, "height": 100,
                "file": f"content/{cid}.md", "color": color}
        data["nodes"].append(card)
        if cid in LINKED_LATER:
            linked_later += [_edge(d, cid) for d in deps]
        else:
            data["edges"] += [_edge(d, cid) for d in deps]
        card["x"] = _old_rank(data, cid) * OLD_COLUMN_STEP
        card["y"] = canvas.CARD_ROW_Y
        while blocking := [n for n in data["nodes"] if n is not card and _overlaps(card, n)]:
            card["y"] = max(n["y"] + n["height"] for n in blocking) + canvas.CARD_Y_GAP
    data["edges"] += linked_later
    by_id = {n["id"]: n for n in data["nodes"]}
    for e in data["edges"]:
        e["fromSide"], e["toSide"] = _frozen_sides(by_id[e["fromNode"]], by_id[e["toNode"]])
    return data


def _anchor(card: dict, side: str) -> tuple[float, float]:
    x, y, w, h = card["x"], card["y"], card["width"], card["height"]
    return {"right": (x + w, y + h / 2), "left": (x, y + h / 2), "top": (x + w / 2, y), "bottom": (x + w / 2, y + h)}[side]


def _curve(source: dict, target: dict, from_side: str, to_side: str) -> tuple[list, tuple]:
    """Obsidian's own edge curve (see canvas._arrow_points), for any pair of sides."""
    (x1, y1), (x2, y2) = _anchor(source, from_side), _anchor(target, to_side)
    n1, n2 = NORMAL[from_side], NORMAL[to_side]
    p1 = (x1 + n1[0] * 7, y1 + n1[1] * 7)
    p2 = (x2 + n2[0] * 7, y2 + n2[1] * 7)
    handle = min(max(math.dist(p1, p2) / 2, 70), 150)
    c1 = (p1[0] + n1[0] * handle, p1[1] + n1[1] * handle)
    c2 = (p2[0] + n2[0] * handle, p2[1] + n2[1] * handle)
    return [p1, c1, c2, p2], (x2, y2, n2)


def _blend(color: str, amount: float) -> tuple[float, float, float]:
    r, g, b = (int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return (1 - (1 - r) * amount, 1 - (1 - g) * amount, 1 - (1 - b) * amount)


def _bbox(nodes: list[dict]) -> tuple[float, float, float, float]:
    margin = 90
    return (min(n["x"] for n in nodes) - margin, min(n["y"] for n in nodes) - margin - 40,
            max(n["x"] + n["width"] for n in nodes) + margin, max(n["y"] + n["height"] for n in nodes) + margin)


def _render(cards: list[dict], edges: list[tuple], groups: list[tuple], view: tuple, caption: str,
            mono: bool) -> Image.Image:
    fig = plt.figure(figsize=(WIDTH / DPI, HEIGHT / DPI), dpi=DPI)
    fig.patch.set_facecolor("white")
    board_h = HEIGHT - CAPTION_BAND - LEGEND_BAND
    ax = fig.add_axes((0, LEGEND_BAND / HEIGHT, 1, board_h / HEIGHT))
    ax.axis("off")

    x1, y1, x2, y2 = view
    scale = min(WIDTH / (x2 - x1), board_h / (y2 - y1))
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    half_w, half_h = WIDTH / scale / 2, board_h / scale / 2
    ax.set_xlim(cx - half_w, cx + half_w)
    ax.set_ylim(cy + half_h, cy - half_h)
    pt = 72 / DPI * scale  # points per canvas unit

    step = 60
    gx = range(int((cx - half_w) // step) * step, int(cx + half_w) + step, step)
    gy = range(int((cy - half_h) // step) * step, int(cy + half_h) + step, step)
    ax.scatter([x for x in gx for _ in gy], [y for _ in gx for y in gy], s=max(0.4, 3 * scale), c="#e4e4e4",
               linewidths=0, zorder=0)

    for group, alpha in groups:
        ax.add_patch(FancyBboxPatch((group["x"], group["y"]), group["width"], group["height"],
                                    boxstyle="round,pad=0,rounding_size=16", facecolor="#f4f4f4",
                                    edgecolor="#cfcfcf", linewidth=max(0.6, 3 * pt), alpha=alpha, zorder=1))
        ax.text(group["x"] + 8, group["y"] - 16, group["label"], fontsize=max(6, 44 * pt), color="#5f5f5f",
                va="bottom", alpha=alpha, zorder=1)

    by_id = {c["id"]: c for c in cards}
    for source, target, from_side, to_side, alpha in edges:
        if alpha <= 0:
            continue
        points, (tx, ty, normal) = _curve(by_id[source], by_id[target], from_side, to_side)
        path = CurvePath(points, [CurvePath.MOVETO, CurvePath.CURVE4, CurvePath.CURVE4, CurvePath.CURVE4])
        ax.add_patch(PathPatch(path, fill=False, edgecolor="#a3a3a3", linewidth=max(0.5, 3.2 * pt),
                               alpha=alpha, zorder=2))
        size, (nx, ny) = 18, normal
        base_x, base_y = tx + nx * (7 + size), ty + ny * (7 + size)
        ax.add_patch(Polygon([(tx + nx * 2, ty + ny * 2), (base_x - ny * size * 0.55, base_y + nx * size * 0.55),
                              (base_x + ny * size * 0.55, base_y - nx * size * 0.55)],
                             closed=True, facecolor="#a3a3a3", edgecolor="none", alpha=alpha, zorder=2))

    for card in cards:
        color = PRESET[card["color"]]
        ax.add_patch(FancyBboxPatch((card["x"], card["y"]), card["width"], card["height"],
                                    boxstyle="round,pad=0,rounding_size=14", facecolor=_blend(color, 0.13),
                                    edgecolor=color, linewidth=max(0.8, 4 * pt), zorder=3))
        ax.text(card["x"] + card["width"] / 2, card["y"] + card["height"] / 2,
                "\n".join(textwrap.wrap(TITLES[card["id"]], 14)), ha="center", va="center",
                fontsize=max(4, 38 * pt), color="#222222", linespacing=1.1, zorder=4)

    fig.text(0.02, 1 - CAPTION_BAND / HEIGHT / 2, caption, va="center", ha="left",
             fontsize=15 if mono else 13.5, family="monospace" if mono else "sans-serif",
             weight="bold" if mono else "normal", color="#222222")
    x = 0.02
    for color, label in LEGEND:
        fig.patches.append(FancyBboxPatch((x, 0.5 * LEGEND_BAND / HEIGHT - 0.011), 0.016, 0.022,
                                          boxstyle="round,pad=0,rounding_size=0.004", transform=fig.transFigure,
                                          facecolor=_blend(PRESET[color], 0.25), edgecolor=PRESET[color],
                                          linewidth=1.2, figure=fig))
        text = fig.text(x + 0.022, 0.5 * LEGEND_BAND / HEIGHT, label, va="center", fontsize=9.5, color="#444444")
        fig.canvas.draw()
        x = text.get_window_extent().x1 / WIDTH + 0.022

    fig.canvas.draw()
    image = Image.frombuffer("RGBA", fig.canvas.get_width_height(), fig.canvas.buffer_rgba()).convert("RGB")
    plt.close(fig)
    return image


def _ease(t: float) -> float:
    return t * t * (3 - 2 * t)


def main() -> None:
    before = before_board()
    after = copy.deepcopy(before)
    canvas.arrange(after, set_aside_done=True, prune_redundant=True)

    start = {c["id"]: c for c in before["nodes"]}
    end = {n["id"]: n for n in after["nodes"] if n["type"] == "file"}
    groups = [n for n in after["nodes"] if n["type"] == "group"]
    kept = {(e["fromNode"], e["toNode"]) for e in after["edges"]}
    old_edges = [(e["fromNode"], e["toNode"], e["fromSide"], e["toSide"]) for e in before["edges"]]
    view_start, view_end = _bbox(before["nodes"]), _bbox(after["nodes"])

    frames = [
        (_render(before["nodes"], [(*e, 1.0) for e in old_edges], [], view_start,
                 "A board after a few weeks of work", False), 2400),
        (_render(before["nodes"], [(*e, 1.0) for e in old_edges], [], view_start, "$ histos arrange", True), 700),
    ]
    for step in range(1, TRANSITION_FRAMES + 1):
        t = step / TRANSITION_FRAMES
        s = _ease(t)
        cards = []
        for cid, card in start.items():
            moved = dict(card)
            moved["x"] = card["x"] + (end[cid]["x"] - card["x"]) * s
            moved["y"] = card["y"] + (end[cid]["y"] - card["y"]) * s
            cards.append(moved)
        fade_in = min(1.0, t / 0.35)
        edges = []
        for source, target, from_side, to_side in old_edges:
            edges.append((source, target, from_side, to_side, max(0.0, 1 - t / 0.5) if (source, target) not in kept
                          else 1 - fade_in))
            if (source, target) in kept:
                edges.append((source, target, "right", "left", fade_in))
        view = tuple(a + (b - a) * s for a, b in zip(view_start, view_end))
        group_alpha = min(1.0, max(0.0, (s - 0.6) / 0.4))
        frames.append((_render(cards, edges, [(g, group_alpha) for g in groups], view, "$ histos arrange", True),
                       50))
    frames.append((_render(list(end.values()), [(u, v, "right", "left", 1.0) for u, v in kept],
                           [(g, 1.0) for g in groups], view_end,
                           "Dependencies flow left to right · finished work set aside · no arrow crosses a card",
                           False), 4000))

    palette = Image.new("RGB", (WIDTH * 2, HEIGHT))
    palette.paste(frames[0][0], (0, 0))
    palette.paste(frames[-1][0], (WIDTH, 0))
    palette = palette.quantize(colors=128, method=Image.Quantize.MEDIANCUT)
    images = [image.quantize(palette=palette, dither=Image.Dither.NONE) for image, _ in frames]
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    images[0].save(OUTPUT, save_all=True, append_images=images[1:], duration=[d for _, d in frames], loop=0,
                   optimize=True)
    print(f"wrote {OUTPUT.relative_to(ROOT)} ({OUTPUT.stat().st_size // 1024} KB, {len(images)} frames)")


if __name__ == "__main__":
    main()
