#!/usr/bin/env python3
"""Census of the prose figures `tools/probes/*.py` quotes as literals.

A probe print whose text hard-codes a number with a measurement unit --
`"earliest 11 ms, latest 41 ms"` -- is prose no check reads: when the same
run also measures that quantity live, the two can disagree silently, which
is how #988's CONCLUSION understated its own run by 12.9 ms.  #988 and #990
repaired the confirmed sites by deriving every such figure from the run's
own measurements, and both classified the rest of the class with a scanner
-- a scanner that was never committed.  Its counts (139 flagged print lines
at #988's tree; 159 flagged literal segments on the #993 base falling to
156 on the #993 branch, the delta being exactly the repaired literals) and
its "no third site" conclusion therefore rested on code in no tree.  This
file is that scanner, committed, so the counts are re-derivable:

    python tools/prose_literal_audit.py          # the table and the totals
    python tools/prose_literal_audit.py --list   # every flagged site

WHAT IT COUNTS.  Two numbers, because #988 and #990 quoted different units:

  * SITES -- `print(...)` / `w(...)` calls carrying at least one flagged
    segment (`w` is the local `w = lambda s="": print(s, file=out)` alias
    probes use for writing a report block).  This is the unit of #988's
    "flagged 139 print lines", measured there by the same unrecoverable
    filter; see HISTORY.
  * SEGMENTS -- regex matches inside the call's literal text.  This is the
    unit of #990's closing comment ("159 flagged literal segments ->
    156"), measured there by a filter that is no longer recoverable; see
    HISTORY for what THIS tool reads on the same trees.  One site can
    carry several segments ("0.01 dB to 16.6 dB" is two).

A literal segment is string-constant text printed by the call: plain
constants (adjacent literals already concatenated by the parser, so prose
continued across lines is one segment) and the constant parts of f-strings.
An f-string FIELD -- the `{...}` expression AND its format spec -- is
excluded entirely, so `f"{t:.1f} ms"` contributes only the ` ms` literal,
which carries no digit and is not flagged: a figure the run derives is the
#988/#990 repair class, correctly invisible here.  Excluding the whole
field also keeps `{'tau':>9s}` from flagging: its format spec contains
`9s`, which the unit regex would otherwise read as `9 s` of prose.

The match regex is the one #998 specified verbatim, no word boundaries
added:

    ~?\\d[\\d.]*\\s?(ms|dB|Hz|kHz|s|%)

Documented consequence of running it as written: the alternation's bare
`s` would also accept a word starting with s after the number -- "12
samples" matches "12 s".  Measured, not assumed: at the #993 base, 0 of
the 211 segments ended mid-word, so the concern is theoretical on this
tree today; the verbatim regex is kept anyway because it is the
specification, and a boundary would silently redefine every count this
tool produces.

PRECONDITIONS, ASSERTED AT THE POINT OF USE, REFUSED (exit 2) WHEN UNMET:

  * `git ls-files -- tools/probes/*.py` must run from this repository and
    list at least MIN_PROBE_FILES files.  A census over a partial tree
    under-reports and reads exactly like one over the whole tree.
  * every tracked probes file must parse.  A file that does not parse
    would contribute zero flags while looking counted -- an unknown
    rendered in the place where a result belongs.

WHY THERE IS NO --check AND NO PIN.  The per-file and total counts are
REPORTED, never pinned.  A pin would red on every unrelated merge that
touches a probes prose line -- the exact trained-away gate the census
precedent (tools/refusal_exit_census.py) documented and removed -- and
#998 is an on-demand audit: #990's curator rejected a generic prose-lint
gate, so this is deliberately wired into nothing, not `make verify`.

WHAT IT CANNOT TELL YOU (documented blind spots, not zeros):

  * FLAGGED IS NOT GUILTY.  The scanner cannot read prose.  A flagged
    literal may be the defect (a measured figure quoted beside the same
    run's live measurement), or a design constant (`455 Hz`), or a
    closed-form consequence of constants the same function derives
    (tools/probes/balance_line_shape.py's prose block quotes the range of
    the drop1 column printed by the loop above it).  The count is sites
    to review, not defects; #988's classification pass, not the count,
    found the third sites.
  * a print through an alias other than `w` is invisible; so are numbers
    inside `%`-format templates (the digit is not in the literal) and
    nested f-strings inside a field (computed, not prose).
  * prints outside `tools/probes/` are out of scope by specification.

HISTORY.  Measured with this file as shipped (the regex above, print/w
call names), recorded so the #990 closing comment's "origin/main 159 ->
branch 156" is at least re-runnable and honestly explained where it does
not reproduce -- run commands are in the PR that introduced this tool:

  * #993 base (9e59062b, the line the 'origin/main' figure was recorded
    on): 163 sites / 211 segments.
  * #993 branch (d4f8461f): 160 sites / 206 segments.  Delta -5, and it
    decomposes into exactly the repairs: rs_mode_drive.py's quoted
    reference line lost `7.24 ms` and `3.85 dB` (-2, the line's design
    constant `455 Hz` remaining), and estimator_defects.py's WHY block
    lost three quoted figures to the derived prose (-3).  Every other
    change is pure line shift.
  * #993 merge result (dcc8ecb0) and the current tree at introduction
    (7a4f322e, 2026-11-18): 158 sites / 200 segments each -- this tool's
    standing baseline.

The historical pair on the same two trees read 159 -> 156 (-3): same
direction, same repaired sites, different netting.  The original scanner
was never committed and its filter is unrecoverable -- the defect #998
exists to repair -- so ABSOLUTE COUNTS ARE COMPARABLE ONLY WITHIN ONE
FILTER, and the numbers that matter for review are this tool's: 200
segments on the current tree, sites to read, not defects.
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import re
import subprocess
import sys

import refusal

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROBES_GLOB = "tools/probes/*.py"
# The tree holds 86 tracked probe files as of 2026-11-18 (7a4f322e).  A floor
# of 60 keeps the census from answering over a truncated tree while leaving
# ordinary churn alone.
MIN_PROBE_FILES = 60
PRINT_NAMES = frozenset({"print", "w"})
# As #998 specifies it, verbatim -- no right word boundary (see docstring).
UNIT_RE = re.compile(r"~?\d[\d.]*\s?(ms|dB|Hz|kHz|s|%)")


class Refused(refusal.Refused, Exception):
    """A precondition failed.  REFUSED is a verdict, distinct from a value."""


def probe_files() -> list[str]:
    """Every tracked `.py` under tools/probes, as repo-relative paths.
    REFUSES if git cannot answer or the tree is too small to be the whole
    probe directory."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "--", PROBES_GLOB],
            cwd=ROOT, capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise Refused(f"git ls-files could not run in {ROOT}: {exc}") from exc
    files = sorted({f for f in out.split() if f.endswith(".py")})
    if len(files) < MIN_PROBE_FILES:
        raise Refused(
            f"git ls-files found {len(files)} probe files, under the "
            f"{MIN_PROBE_FILES} this repository carries; a partial tree "
            f"under-reports the census")
    return files


def _literal_strings(node: ast.AST, out: list[tuple[str, int]]) -> None:
    """(text, lineno) for every literal string this expression prints: str
    Constants anywhere in the expression, and the Constant parts of
    f-strings.  A FormattedValue -- the {...} field AND its format spec --
    is excluded entirely, including any f-string nested inside the field:
    that text is computed, not prose, and a format spec like `9s` is not a
    number-with-unit despite what the regex would say."""
    if isinstance(node, ast.FormattedValue):
        return
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        out.append((node.value, node.lineno))
        return
    if isinstance(node, ast.JoinedStr):
        for part in node.values:
            _literal_strings(part, out)
        return
    for child in ast.iter_child_nodes(node):
        _literal_strings(child, out)


def _print_calls(tree: ast.Module) -> list[ast.Call]:
    """Every `print(` / `w(` call, in source order.  Nested calls (a print
    argument that itself calls print) are distinct sites and each is kept."""
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id in PRINT_NAMES]


def audit_files(files: list[str]) -> dict:
    """The census over repo-relative `files`.  REFUSES on a file that does
    not parse: its sites would be invisible while the totals read as a full
    scan."""
    per: dict[str, dict] = {}
    calls_scanned = 0
    for rel in sorted(files):
        try:
            tree = ast.parse((ROOT / rel).read_text())
        except (OSError, SyntaxError) as exc:
            raise Refused(f"cannot parse {rel}: {exc}; its print sites would "
                          f"be invisible and the count would read like a "
                          f"full scan") from exc
        calls_scanned += len(_print_calls(tree))
        sites: list[tuple[int, list[str]]] = []
        for call in _print_calls(tree):
            segs: list[tuple[str, int]] = []
            _literal_strings(call, segs)
            matches = [m.group(0) for text, _ in segs
                       for m in UNIT_RE.finditer(text)]
            if matches:
                sites.append((call.lineno, matches))
        if sites:
            sites.sort(key=lambda s: s[0])
            per[rel] = {
                "sites": sites,
                "segments": sum(len(ms) for _, ms in sites),
            }
    return {
        "n_files": len(files),
        "calls_scanned": calls_scanned,
        "files": per,
        "total_sites": sum(len(d["sites"]) for d in per.values()),
        "total_segments": sum(d["segments"] for d in per.values()),
    }


def audit() -> dict:
    return audit_files(probe_files())


def render(res: dict, list_sites: bool = False) -> str:
    lines = [
        f"{res['n_files']} tracked probe files, {res['calls_scanned']} "
        f"print/w calls scanned under tools/probes.",
        f"{res['total_sites']} sites quote a literal number-with-unit, "
        f"{res['total_segments']} segments overall.",
        "FLAGGED IS NOT GUILTY: a design constant (`455 Hz`) or a closed-form",
        "consequence of same-function constants is flagged too -- sites to",
        "review, not defects (see the module docstring).",
        "",
        f"{'file':<52} {'sites':>5} {'segments':>8}",
    ]
    for rel in sorted(res["files"]):
        d = res["files"][rel]
        lines.append(f"{rel:<52} {len(d['sites']):>5} {d['segments']:>8}")
    lines.append(f"{'total':<52} {res['total_sites']:>5} "
                 f"{res['total_segments']:>8}")
    if list_sites:
        lines += ["", "Every flagged segment:"]
        for rel in sorted(res["files"]):
            for ln, matches in res["files"][rel]["sites"]:
                lines.append(f"  {rel}:{ln}")
                for text in matches:
                    lines.append(f"    {text!r}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--list", action="store_true",
                    help="list every flagged site after the table")
    args = ap.parse_args(argv)
    try:
        print(render(audit(), list_sites=args.list))
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2            # the dominant family, and the one run_all.py honours
    return 0


if __name__ == "__main__":
    sys.exit(main())
