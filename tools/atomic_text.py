"""Atomic publication of plain-text experiment reports."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory


def write_text_atomic(path: str, text: str) -> None:
    """Publish a complete report, preserving the old copy on a write failure."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=target.parent) as staging:
        pending = Path(staging) / "report"
        with pending.open("w") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(pending, target)
