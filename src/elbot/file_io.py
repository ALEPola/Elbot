"""Atomic replacement of small runtime files."""

import os
import stat
import tempfile
from pathlib import Path


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # mkstemp starts at 0600. Restore the destination's access mask before
    # replacing it so inherited service ACLs survive atomic updates.
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o640
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
