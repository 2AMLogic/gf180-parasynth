"""Read one Makefile target's recipe text -- shared by suites that must pin
their own wiring ("check that the thing you are testing is the thing that
ships", CLAUDE.md).

`tools/test_manifest.py` uses it to assert its suite is enumerated in
`make verify-fast`, the target CI actually runs; `tools/test_sensitivity.py`
uses it to assert every injected control is enumerated in `make controls`
with the `--expect` token its exit code implies (issue #836). One parser,
not one per suite: a copied Makefile parser is exactly the wiring-drift
defect those assertions exist to catch.
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]


def makefile_recipe(target: str) -> str:
    """The recipe lines of one Makefile target, joined. Used to assert that a
    check is enumerated where CI will actually reach it."""
    lines = (ROOT / "Makefile").read_text().split("\n")
    out, inside = [], False
    for line in lines:
        if re.match(rf"^{re.escape(target)}\s*:", line):
            inside = True
            continue
        if inside:
            if line and not line[0].isspace():
                break
            out.append(line)
    assert out, f"no recipe found for Makefile target {target!r}"
    return "\n".join(out)
