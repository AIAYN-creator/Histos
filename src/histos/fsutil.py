"""Atomic file writes: a reader (another histos process, Obsidian) sees either the old file
or the new one, never a half-written one -- and a crash mid-write leaves the old one intact.
"""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

from .errors import HistosError

# os.replace fails on Windows while another process has the target open (Obsidian reading it,
# an antivirus scan); it's usually a matter of milliseconds, so retry with growing waits (~1.5 s).
_REPLACE_RETRY_WAITS = (0.05, 0.1, 0.2, 0.4, 0.8)


def write_text_atomic(path: Path, text: str) -> None:
    """Same bytes as `path.write_text(text, encoding="utf-8")` (text mode, so CRLF on Windows),
    written to a temp file in the same folder and swapped in with os.replace."""
    _write_atomic(path, text, "w", "utf-8")


def copy_file_atomic(src: Path, dst: Path) -> None:
    """Byte-for-byte copy, swapped in the same way."""
    _write_atomic(dst, src.read_bytes(), "wb", None)


def _write_atomic(path: Path, content: str | bytes, mode: str, encoding: str | None) -> None:
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, mode, encoding=encoding) as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        _replace_with_retry(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _replace_with_retry(tmp: Path, path: Path) -> None:
    for wait in (*_REPLACE_RETRY_WAITS, None):
        try:
            os.replace(tmp, path)
            return
        except PermissionError as e:
            if wait is None:
                raise HistosError(
                    f"couldn't replace {path} -- another program kept it open ({e}); "
                    "the previous version is untouched, try again"
                ) from e
            time.sleep(wait)
