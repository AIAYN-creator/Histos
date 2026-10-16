import os
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from histos import canvas, lock, operations
from histos.cli import build_parser
from histos.errors import HistosError

SRC = str(Path(__file__).resolve().parents[1] / "src")


def run(monkeypatch, tmp_path, *argv):
    monkeypatch.chdir(tmp_path)
    args = build_parser().parse_args(list(argv))
    return args.func(args)


@contextmanager
def held_elsewhere(vault, command="assign cap9"):
    """The lock held by another handle, as another process would (OS locks are per handle)."""
    ready, release = threading.Event(), threading.Event()

    def hold():
        with lock.vault_lock(vault, command):
            ready.set()
            release.wait(10)

    t = threading.Thread(target=hold)
    t.start()
    ready.wait(10)
    try:
        yield release
    finally:
        release.set()
        t.join()


def test_lock_is_released_after_a_command(initialized_vault, monkeypatch):
    run(monkeypatch, initialized_vault, "add-card", "cap1", "--title", "Uno")
    with lock.vault_lock(initialized_vault, "check"):
        pass  # would wait 10 s and fail if the command had kept it
    assert lock.read_holder(initialized_vault) is None


def test_lock_is_released_when_the_command_fails(initialized_vault, monkeypatch):
    with pytest.raises(RuntimeError):
        with lock.vault_lock(initialized_vault, "boom"):
            raise RuntimeError("x")
    monkeypatch.setattr(lock, "_WAIT_TIMEOUT", 0.1)
    with lock.vault_lock(initialized_vault, "next"):
        pass


def test_a_held_lock_makes_the_command_wait(initialized_vault, monkeypatch):
    with held_elsewhere(initialized_vault) as release:
        threading.Timer(0.3, release.set).start()
        start = time.monotonic()
        assert run(monkeypatch, initialized_vault, "add-card", "cap1", "--title", "Uno") == 0
        assert time.monotonic() - start >= 0.25
    assert canvas.find_card(canvas.load(initialized_vault), "cap1") is not None


def test_giving_up_names_who_holds_it(initialized_vault, monkeypatch, capsys):
    monkeypatch.setattr(lock, "_WAIT_TIMEOUT", 0.2)
    with held_elsewhere(initialized_vault, "propose cap3"):
        assert run(monkeypatch, initialized_vault, "add-card", "cap1", "--title", "Uno") == 1
    err = capsys.readouterr().err
    assert "the vault is busy" in err and "propose cap3" in err


def test_a_killed_holder_never_leaves_the_vault_locked(initialized_vault, monkeypatch):
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import sys, time; from pathlib import Path; from histos import lock\n"
         "with lock.vault_lock(Path(sys.argv[1]), 'crashy'):\n"
         "    print('locked', flush=True); time.sleep(60)",
         str(initialized_vault)],
        env=dict(os.environ, PYTHONPATH=SRC), stdout=subprocess.PIPE,
    )
    assert holder.stdout.readline().strip() == b"locked"
    assert lock.read_holder(initialized_vault)["command"] == "crashy"
    holder.kill()
    holder.wait()
    monkeypatch.setattr(lock, "_WAIT_TIMEOUT", 2.0)
    assert run(monkeypatch, initialized_vault, "add-card", "cap1", "--title", "Uno") == 0


def test_read_only_commands_ignore_the_lock(initialized_vault, monkeypatch):
    run(monkeypatch, initialized_vault, "add-card", "cap1", "--title", "Uno")
    monkeypatch.setattr(lock, "_WAIT_TIMEOUT", 0.1)
    with held_elsewhere(initialized_vault):
        assert run(monkeypatch, initialized_vault, "context", "cap1") == 0
        assert run(monkeypatch, initialized_vault, "validate") == 0


def test_operations_lock_too_so_the_desktop_app_is_covered(initialized_vault, monkeypatch):
    monkeypatch.setattr(lock, "_WAIT_TIMEOUT", 0.1)
    with held_elsewhere(initialized_vault):
        with pytest.raises(HistosError, match="busy"):
            operations.get_status(initialized_vault)


def test_parallel_processes_never_lose_each_others_changes(initialized_vault, monkeypatch):
    ids = [f"cap{i}" for i in range(8)]
    for card_id in ids:
        run(monkeypatch, initialized_vault, "add-card", card_id, "--title", card_id)

    env = dict(os.environ, PYTHONPATH=SRC)
    procs = [
        subprocess.Popen([sys.executable, "-m", "histos", "assign", card_id], cwd=initialized_vault,
                         env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for card_id in ids
    ]
    for p in procs:
        _, err = p.communicate(timeout=60)
        assert p.returncode == 0, err.decode()

    data = canvas.load(initialized_vault)
    assert [canvas.find_card(data, i)["color"] for i in ids] == [canvas.IN_PROGRESS] * len(ids)
    assert lock.read_holder(initialized_vault) is None
