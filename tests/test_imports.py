"""Every module imports on its own, in a fresh interpreter. The desktop app imports histos.config
first; a circular import that only shows up in that order slipped past the rest of the suite
(which imports through histos.cli) and broke the packaged app's selftest before 3.1.0."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
MODULES = sorted(
    ".".join(p.relative_to(SRC).with_suffix("").parts).removesuffix(".__init__")
    for p in (SRC / "histos").rglob("*.py")
    if p.name != "__main__.py" and "gui" not in p.relative_to(SRC).parts
)


@pytest.mark.slow
def test_every_module_imports_alone():
    env = dict(os.environ, PYTHONPATH=str(SRC))
    broken = []
    for module in MODULES:
        result = subprocess.run([sys.executable, "-c", f"import {module}"], env=env,
                                capture_output=True, text=True)
        if result.returncode != 0:
            broken.append(f"{module}: {result.stderr.strip().splitlines()[-1]}")
    assert broken == []
