"""Measure how a tier config would split real vaults -- read-only, writes nothing anywhere.

Every card is measured as it was right before being worked: its title and description as card
text, its dependencies' approved content and its sources as context, never its own approved
content. (Cards still waiting have almost no context yet, so they alone would calibrate
nothing.) This is how the default rules in templates/histos.config.yaml were chosen.

    python scripts/calibrate_tiers.py VAULT [VAULT...] [--config my.config.yaml] [--samples 6]
    python scripts/calibrate_tiers.py --search "C:/Users/me/Documents"   # every vault below it
"""
from __future__ import annotations

import argparse
import random
import shutil
import sys
import tempfile
from collections import Counter
from importlib import resources
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from histos import canvas, config, context, heuristics, tiers, validation  # noqa: E402
from histos.heuristics import Inputs  # noqa: E402


def measure(vault: Path) -> list[tuple[str, Inputs]]:
    try:
        data = canvas.load(vault)
    except canvas.HistosError as e:
        print(f"skipping {vault}: {e}", file=sys.stderr)
        return []
    if validation.validate_all(data):
        print(f"skipping {vault}: canvas not valid", file=sys.stderr)
        return []
    rows = []
    for card in canvas.cards(data):
        meta, body = context.read_card(vault, card)
        title = next((line for line in body.splitlines() if line.startswith("# ")), "")
        card_text = "\n".join(x for x in (title, meta.get("description"), meta.get("status_note")) if x)
        before = dict(card, color=canvas.IN_PROGRESS)  # leaves its own approved content out
        full = context.join(context.card_sections(vault, data, before))
        deps = len(context.dependencies(vault, data, card))
        rows.append((card["id"], Inputs(card_text, full, deps, len(meta.get("sources") or []))))
    return rows


def load_config(path: Path | None) -> config.Config:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / config.CONFIG_FILENAME
        if path:
            shutil.copyfile(path, target)
        else:
            target.write_text(
                resources.files("histos").joinpath("templates", config.CONFIG_FILENAME).read_text(encoding="utf-8"),
                encoding="utf-8",
            )
        return config.load(Path(tmp))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("vaults", nargs="*", type=Path)
    parser.add_argument("--search", type=Path, action="append", default=[], help="find every vault below this folder")
    parser.add_argument("--config", type=Path, help="config to try (default: the bundled template)")
    parser.add_argument("--samples", type=int, default=6, help="example cards shown per tier")
    args = parser.parse_args()

    vaults = list(args.vaults)
    for root in args.search:
        vaults += [p.parent for p in root.rglob(canvas.CANVAS_FILENAME)]
    if not vaults:
        parser.error("pass vault folders or --search")

    cfg = load_config(args.config)
    results = []
    for vault in vaults:
        for card_id, inputs in measure(vault):
            results.append((vault.name, card_id, inputs, tiers.classify(cfg, inputs)))
    if not results:
        print("no cards found")
        return 1

    tokens = sorted(heuristics.MEASURES["token_count"](r[2]) for r in results)
    pick = lambda p: tokens[int(p * (len(tokens) - 1))]  # noqa: E731
    print(f"{len(results)} cards in {len(vaults)} vaults")
    print(f"context tokens: p50 {pick(.5)} · p90 {pick(.9)} · p95 {pick(.95)} · max {tokens[-1]}")
    for name in heuristics.CHECKS:
        hits = sum(heuristics.parse(name).evaluate(r[2], cfg.terms).holds for r in results)
        print(f"  {name:30s} {hits}")

    total = Counter(r[3].tier for r in results)
    by_default = sum(r[3].by_default for r in results)
    print(f"\nfast {total['fast']} · exec {total['exec']} ({by_default} by default) · reasoning {total['reasoning']}")
    per_vault: dict[str, Counter] = {}
    for vault_name, _, _, c in results:
        per_vault.setdefault(vault_name, Counter())[c.tier] += 1
    for vault_name, counts in sorted(per_vault.items()):
        print(f"  {vault_name[:30]:30s} fast {counts['fast']:3d}  exec {counts['exec']:3d}  reasoning {counts['reasoning']:3d}")

    random.seed(1)
    for tier in tiers.PRECEDENCE:
        pool = [r for r in results if r[3].tier == tier]
        if not pool:
            continue
        print(f"\nsample {tier}:")
        for vault_name, card_id, inputs, c in random.sample(pool, min(args.samples, len(pool))):
            last = c.traces[-1]
            why = last.details[0] if last.matched else "default"
            first_line = (inputs.card_text.splitlines() or [""])[0]
            print(f"  {vault_name[:14]:14s} {card_id[:26]:26s} {first_line[:48]:48s} {why[:40]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
