"""A small run of scripts/stress_parallel.py, so a regression in the vault lock shows up in any
normal test run -- not only when someone launches the long one."""
import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "stress_parallel.py"


@pytest.mark.slow
def test_parallel_agents_lose_nothing():
    spec = importlib.util.spec_from_file_location("stress_parallel", SCRIPT)
    stress = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = stress  # its dataclasses look their module up while being defined
    spec.loader.exec_module(stress)
    assert stress.run(workers=4, rounds=4, seed=7, quiet=True) == []
