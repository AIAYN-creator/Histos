"""Builds examples/sample-paper/: a small but complete Histos vault, made by really running the
`histos` commands -- so every file in it (canvas, frontmatter, config, evals, agent templates)
is exactly what Histos writes today. Re-run it whenever a format changes.

It shows each state of the cycle: approved cards, one in review that passed its evals, one in
progress for a subagent with its tier and approved model, a classified card in the backlog,
and blocked ones waiting on them.

    python scripts/make_example_vault.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "examples" / "sample-paper"

PROJECT_MD = """# A sample paper

A short methods paper comparing three approaches to the same problem. Made up, to show what a
Histos vault looks like -- not a real project.
"""
ARCHITECTURE_MD = """# How this paper is written

- British English, first person plural.
- Citations as [@key], resolved from references.bib.
- Every section ends with one sentence linking to the next.
"""
CARDS = [
    ("scope", "Scope", "Who the paper is for and what it leaves out", []),
    ("outline", "Outline", "Sections and the argument they build", ["scope"]),
    ("related", "Related work", "Compare the three main approaches and their trade-offs", ["outline"]),
    ("intro", "Introduction", "Motivate the problem and state the contributions", ["outline"]),
    ("method", "Method", "The experimental setup, step by step", ["outline"]),
    ("results", "Results", "What the experiments show", ["related", "intro", "method"]),
    ("abstract", "Abstract", "The paper in 200 words", ["results"]),
]
TEXT = {
    "scope": "# Scope\n\nThe paper is for practitioners choosing between the three approaches; it leaves out "
             "training cost.\n",
    "outline": "# Outline\n\n1. Introduction\n2. Related work\n3. Method\n4. Results\n5. Discussion\n",
    "related": "# Related work\n\nThree families of methods address this problem: rule-based systems, statistical "
               "models and neural approaches [@smith2021]. Each trades interpretability for coverage in a "
               "different way.\n",
}


def histos(vault: Path, *argv: str) -> str:
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONIOENCODING="utf-8")
    result = subprocess.run([sys.executable, "-m", "histos", *argv], cwd=vault, env=env,
                            capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        raise SystemExit(f"histos {' '.join(argv)} failed:\n{result.stdout}{result.stderr}")
    return result.stdout


def build(vault: Path, drafts: Path) -> None:
    histos(vault, "init")
    (vault / "PROJECT.md").write_text(PROJECT_MD, encoding="utf-8")
    (vault / "ARCHITECTURE.md").write_text(ARCHITECTURE_MD, encoding="utf-8")
    for cid, title, description, deps in CARDS:
        extra = ["--depends-on", *deps, "--authorized"] if deps else []
        histos(vault, "add-card", cid, "--title", title, "--description", description, *extra)

    def draft(cid: str) -> str:
        path = drafts / f"{cid}.md"
        path.write_text(TEXT[cid], encoding="utf-8")
        return str(path)

    for cid in ("scope", "outline"):  # approved
        histos(vault, "assign", cid, "--worker", "parent")
        histos(vault, "propose", cid, "--file", draft(cid))
        histos(vault, "approve", cid)
    # in review: classified, model approved, evals passed
    histos(vault, "assign", "related", "--worker", "parent")
    histos(vault, "classify", "related")
    histos(vault, "model", "related", "--proposed", "Opus 5.5")
    histos(vault, "model", "related", "--approved", "Opus 5.5", "--authorized")
    histos(vault, "propose", "related", "--file", draft("related"))
    # in progress, handed to a subagent
    histos(vault, "assign", "intro", "--worker", "sub-intro")
    histos(vault, "classify", "intro")
    histos(vault, "model", "intro", "--proposed", "Haiku 5.5")
    histos(vault, "model", "intro", "--approved", "Haiku 5.5", "--authorized")
    # backlog: classified, its model still waiting for the human
    histos(vault, "classify", "method")
    histos(vault, "model", "method", "--proposed", "Sonnet 5.5")
    histos(vault, "arrange")
    for leftover in (".histos.lock", "project.canvas.bak"):
        (vault / leftover).unlink(missing_ok=True)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        vault, drafts = Path(tmp) / "sample-paper", Path(tmp) / "drafts"
        vault.mkdir()
        drafts.mkdir()
        build(vault, drafts)
        status = histos(vault, "status")
        (vault / ".histos.lock").unlink(missing_ok=True)
        if TARGET.exists():
            shutil.rmtree(TARGET)
        shutil.copytree(vault, TARGET)
    print(f"wrote {TARGET.relative_to(ROOT)}\n{status}")


if __name__ == "__main__":
    main()
