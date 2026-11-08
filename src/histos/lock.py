"""Vault lock: every command that modifies the vault holds it from before reading to after
saving, so a parent agent and a subagent working at the same time can't overwrite each
other's changes (both load, both modify, the last save silently wins). Obsidian doesn't
take part -- it knows nothing about this file.

It's an OS file lock (msvcrt on Windows, flock elsewhere) on a hidden file that stays in the
vault: the OS releases it the moment the holder exits, even on a crash or Ctrl+C, so there's
never a stale lock to detect or break -- breaking one safely is itself a race.
"""
from __future__ import annotations

import functools
import json
import os
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator

from . import events
from .errors import HistosError

LOCK_FILENAME = ".histos.lock"
_WAIT_TIMEOUT = 10.0  # histos commands take milliseconds; 10 s of waiting means something is wrong
_POLL_INTERVAL = 0.05
# Windows byte-range locks are mandatory: lock one byte far past the holder info written at the
# start of the file, so waiting processes can still read who holds it.
_LOCK_OFFSET = 1 << 30

if sys.platform == "win32":
    import msvcrt

    def _try_lock(fd: int) -> bool:
        os.lseek(fd, _LOCK_OFFSET, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        os.lseek(fd, _LOCK_OFFSET, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


def lock_path(vault_root: Path) -> Path:
    return vault_root / LOCK_FILENAME


@contextmanager
def vault_lock(vault_root: Path, command: str, actor: str = "agent") -> Iterator[None]:
    """The vault lock, plus the event log session that records what the command did (events.py).
    `actor` is who runs the command: 'agent', or 'human' for the human's commands. If the command
    changed the canvas, the critical path is repainted before the lock is released (critical.py)."""
    with file_lock(lock_path(vault_root), command):
        before = _canvas_bytes(vault_root)
        with events.recording(vault_root, actor):
            yield
        if _canvas_bytes(vault_root) != before:
            from . import critical  # imported late: critical reaches the command layer

            critical.refresh_quietly(vault_root)


def _canvas_bytes(vault_root: Path) -> bytes | None:
    try:
        return (vault_root / "project.canvas").read_bytes()
    except OSError:
        return None


@contextmanager
def file_lock(path: Path, command: str) -> Iterator[None]:
    """The bare OS lock on `path`, without event recording -- also used for ~/.histos/vaults.json."""
    # The holder info is written into this file (and truncated on release): if it were a symlink,
    # that would clobber whatever it points to. O_NOFOLLOW closes the race where it exists (POSIX).
    if path.is_symlink():
        raise HistosError(f"{path} is a symbolic link -- remove it; histos only uses a plain file there")
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        raise HistosError(f"can't find the folder {path.parent}") from None
    except OSError as e:
        if path.is_symlink():
            raise HistosError(f"{path} is a symbolic link -- remove it; histos only uses a plain file there") from None
        raise HistosError(f"can't open {path}: {e}") from None
    try:
        deadline = time.monotonic() + _WAIT_TIMEOUT
        while not _try_lock(fd):
            if time.monotonic() >= deadline:
                raise HistosError(f"the vault is busy ({_describe(_read_holder_file(path))}) -- try again")
            time.sleep(_POLL_INTERVAL)
        try:
            _write_holder(fd, {
                "pid": os.getpid(),
                "since": datetime.now().astimezone().isoformat(timespec="seconds"),
                "command": command,
            })
            yield
        finally:
            _write_holder(fd, None)
            _unlock(fd)
    finally:
        os.close(fd)


def locked(command: str, actor: str = "human") -> Callable:
    """For operations.py functions taking vault_root first: `@locked("approve")`. They're the
    human's commands (and the desktop app's), so the actor defaults to 'human'."""
    def decorate(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(vault_root: Path, *args, **kwargs):
            label = f"{command} {args[0]}" if args and isinstance(args[0], str) else command
            with vault_lock(vault_root, label, actor=actor):
                result = func(vault_root, *args, **kwargs)
            if actor == "human":
                from . import pauses  # imported late: pauses uses this module

                pauses.resume_on(command)  # a decision means the human is back
            return result
        return wrapper
    return decorate


def read_holder(vault_root: Path) -> dict | None:
    """Who holds the lock right now, as far as the file says -- only for messages."""
    return _read_holder_file(lock_path(vault_root))


def _read_holder_file(path: Path) -> dict | None:
    try:
        with open(path, "rb") as f:
            holder = json.loads(f.read(4096).decode("utf-8") or "null")
    except (OSError, ValueError):
        return None
    return holder if isinstance(holder, dict) else None


def _write_holder(fd: int, holder: dict | None) -> None:
    os.lseek(fd, 0, os.SEEK_SET)
    os.ftruncate(fd, 0)
    if holder is not None:
        os.write(fd, json.dumps(holder).encode("utf-8"))


def _describe(holder: dict | None) -> str:
    if not holder:
        return "held by another histos process"
    return f"'{holder.get('command')}', pid {holder.get('pid')}, since {holder.get('since')}"
