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
def vault_lock(vault_root: Path, command: str) -> Iterator[None]:
    path = lock_path(vault_root)
    # The holder info is written into this file (and truncated on release): if it were a symlink,
    # that would clobber whatever it points to. O_NOFOLLOW closes the race where it exists (POSIX).
    if path.is_symlink():
        raise HistosError(f"{path} is a symbolic link -- remove it; histos only uses a plain file there")
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        raise HistosError(f"can't find the folder {vault_root}") from None
    except OSError as e:
        if path.is_symlink():
            raise HistosError(f"{path} is a symbolic link -- remove it; histos only uses a plain file there") from None
        raise HistosError(f"can't open {path}: {e}") from None
    try:
        deadline = time.monotonic() + _WAIT_TIMEOUT
        while not _try_lock(fd):
            if time.monotonic() >= deadline:
                raise HistosError(f"the vault is busy ({_describe(read_holder(vault_root))}) -- try again")
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


def locked(command: str) -> Callable:
    """For operations.py functions taking vault_root first: `@locked("approve")`."""
    def decorate(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(vault_root: Path, *args, **kwargs):
            label = f"{command} {args[0]}" if args and isinstance(args[0], str) else command
            with vault_lock(vault_root, label):
                return func(vault_root, *args, **kwargs)
        return wrapper
    return decorate


def read_holder(vault_root: Path) -> dict | None:
    """Who holds the lock right now, as far as the file says -- only for messages."""
    try:
        with open(lock_path(vault_root), "rb") as f:
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
