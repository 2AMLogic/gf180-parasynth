"""Confine a report path to the repository, the way the filesystem would.

``Path.resolve()`` (non-strict) discards a ``..`` that follows a component it
cannot traverse, so ``take.wav/../summary.json`` collapses to ``summary.json``
even when ``take.wav`` is a regular file -- a path the OS itself refuses
(``NotADirectoryError``).  A measurement tool that then writes there overwrites
a report it was never asked to touch and exits 0 (#1584 review).

``confine`` therefore checks every component that a ``..`` would step out of:
it must resolve to an existing directory.  Non-existent *new* output
directories without ``..`` stay allowed.  Any path-resolution error (a symlink
loop, an unreadable component) is a refusal, never an exception, so callers
can return their declared refusal code before measuring anything.
"""

import os
import pathlib


def confine(raw, root, *, replace_leaf=False):
    """Return the absolute destination for ``raw`` inside ``root``, else None.

    ``replace_leaf`` is for writers that replace the directory entry they are
    given (a leaf symlink is replaced, not followed): the parent is resolved and
    the leaf name kept, and both the entry and where it points must stay inside
    ``root``.  Otherwise the fully resolved path is returned.
    """
    root = pathlib.Path(root).resolve()
    path = pathlib.Path(raw)
    if not path.is_absolute():
        path = root / path
    try:
        cur = pathlib.Path(path.anchor)
        for part in path.parts[1:]:
            if part == "..":
                real = cur.resolve()
                if not real.is_dir():
                    return None
                cur = real.parent
            elif part != ".":
                cur = cur / part
        if replace_leaf:
            if cur.name in ("", ".", ".."):
                return None
            entry = cur.parent.resolve() / cur.name
            ok = entry.is_relative_to(root) and entry.resolve().is_relative_to(root)
            return entry if ok else None
        final = cur.resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    return final if final.is_relative_to(root) else None
