#!/usr/bin/env python3
r"""Does each of eighteen WORK_LOG records appear exactly once, under its own day?

WHY THIS EXISTS. PR #1941 (a Guide document-maintenance tick) appended 261
records to WORK_LOG.md. Eighteen of them landed in the `### 2026-11-25`
section, and fourteen of those eighteen were already on `origin/main`, because
the same record has two spellings in the file:

    A   - **Issue #1302 (closed)**: <title>      what main carried, 14 bullets
    B   - **Issue #1302** (closed): <title>      what the tick appended

Guide's presence check (`work_log_has_issue()` in `guide.md`) is
`grep -qE "^- \*\*Issue #N\*\* "`, which matches B and cannot match A, so it
reported the fourteen absent and the tick re-added them. Four records (#1317,
#1326, #1222, #1227) were genuinely missing and are correct. The original
report that the eighteen were filed under the wrong day was NOT reproduced at
head `91cf3598`: all eighteen sit under `### 2026-11-25`. This script is the
per-number evidence for issue #2029 and the detector for its regression.

A record bullet is any line matching `^[ \t]*- \*\*(Issue|PR) #N\b`; the
spelling after the number does not matter, and neither does the kind. The
eighteen numbers are fixed on purpose.

WHAT IT IS BLIND TO, so a green run is not over-read (`docs/verification-rules.md`
rule 8):

- any number outside the eighteen, including a duplicate of one;
- a record's title, kind and state -- only the number is compared;
- `work_log/WORK_LOG_*.md`, where rotation moves sections older than 60 days;
- a record bullet that is not a `- **...` list item.

Exit 0 when each of the eighteen has exactly one record bullet under
`### 2026-11-25`; 1 when any is absent, duplicated, or under another day;
2 (REFUSED) when the input could not be read as a WORK_LOG -- this
repository's convention, where 2 means no evidence rather than no problem.
"""
from __future__ import annotations

import argparse
import collections
import re
import sys

SECTION = "2026-11-25"
NUMBERS = (1317, 1326, 1222, 1227, 1302, 1329, 1319, 1289, 1338, 1306, 1331, 1325,
           1323, 1297, 1301, 1315, 1221, 1263)
_DAY = re.compile(r"^### (\d{4}-\d{2}-\d{2})")
_RECORD = re.compile(r"^[ \t]*- \*\*(?:Issue|PR) #(\d+)\b")


def scan(text: str) -> dict[int, list[str | None]]:
    """The day section each record bullet sits under, per number, in file order."""
    found: dict[int, list[str | None]] = {n: [] for n in NUMBERS}
    day: str | None = None
    for line in text.splitlines():
        m = _DAY.match(line)
        if m:
            day = m.group(1)
        m = _RECORD.match(line)
        if m and int(m.group(1)) in found:
            found[int(m.group(1))].append(day)
    return found


def verdicts(found: dict[int, list[str | None]]) -> list[tuple[str, int, list[str | None]]]:
    bad: list[tuple[str, int, list[str | None]]] = []
    for n in NUMBERS:
        where = found[n]
        if not where:
            bad.append(("absent", n, where))
        elif len(where) > 1:
            bad.append(("duplicated", n, where))
        elif where != [SECTION]:
            bad.append(("misfiled", n, where))
    return bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", help="WORK_LOG.md to scan, or '-' for stdin")
    a = ap.parse_args(argv)
    src = "stdin" if a.path == "-" else a.path
    try:
        if a.path == "-":
            text = sys.stdin.read()
        else:
            with open(a.path, encoding="utf-8") as fh:
                text = fh.read()
    except (OSError, UnicodeDecodeError, ValueError) as e:
        print(f"REFUSED: cannot read {src}: {e}", file=sys.stderr)
        return 2
    if not any(_DAY.match(line) for line in text.splitlines()):
        print(f"REFUSED: {src} has no '### YYYY-MM-DD' day section, so it is not a "
              "WORK_LOG and nothing was checked", file=sys.stderr)
        return 2

    bad = verdicts(scan(text))
    for kind, n, where in bad:
        detail = " (no record bullet)" if kind == "absent" else f" under {where}"
        print(f"FAIL {kind} #{n}{detail}")
    counts = collections.Counter(kind for kind, _, _ in bad)
    ok = len(NUMBERS) - len(bad)
    if bad:
        print(f"FAIL: {counts['duplicated']} duplicated, {counts['misfiled']} misfiled, "
              f"{counts['absent']} absent, {ok} ok -- {len(NUMBERS)} numbers in {src}")
        return 1
    print(f"OK: {ok}/{len(NUMBERS)} numbers have exactly one record bullet "
          f"under ### {SECTION}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
