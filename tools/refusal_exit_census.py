#!/usr/bin/env python3
"""Census of the exit status this repository uses to mean REFUSED, and which
of those `tools/run_all.py` reports truthfully.

WHY THIS EXISTS.  `tools/run_all.py` is the batch runner `CLAUDE.md` mandates
for all known-independent work, and its own docstring states the contract it
enforces:

    0  match          the comparison ran and agreed
    1  mismatch       the comparison ran and disagreed  -- a RESULT
    2  did not run    simulator missing, compile failed -- NO EVIDENCE

It implements that with ONE integer, `VERIFIER_NO_EVIDENCE = 2`.  Every other
non-zero status becomes `FAIL`.  But the repository does not use one integer
for a refusal: it uses three, each deliberately and each documented locally,
so a refusal from the 2 family is reported `NO-VERDICT` while an identical
refusal from the 1 or 3 family is reported `FAIL` -- "an unknown rendered in
the place where a result belongs", which is the exact defect `run_all.py` was
written to prevent.

This script does not judge which integer is right.  It counts, so the
divergence is a number rather than an impression, and prints which side of
`run_against_run_all()` each family lands on.

    python tools/refusal_exit_census.py            # the census
    python tools/refusal_exit_census.py --check    # self-test (known answers)

PRECONDITIONS, ASSERTED AT THE POINT OF USE, REFUSED (exit 2) WHEN UNMET:

  * `git ls-files` must run from this repository, and must list at least 300
    Python files.  A census over a partial tree would under-report the
    divergence and read exactly like a census over the whole one.
  * `tools/run_all.py` must parse and must define `VERIFIER_NO_EVIDENCE`.  The
    "which side does run_all land on" column is read from that module's source,
    never retyped here; if the name is gone, this tool cannot answer.

WHAT IT CANNOT TELL YOU.  A handler that returns a refusal status through a
variable or a named constant (`return REFUSED`) is NOT counted by the
exception-handler pass -- `pnr/shuttle/` does exactly that -- so the constant
pass below covers them separately.  The two passes are reported separately
rather than summed, because a sum would hide which detector found what.
"""

from __future__ import annotations

import argparse
import ast
import collections
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

MIN_PY_FILES = 300

# Exception classes that mean "a precondition is not met, so nothing was
# measured".  Taken from the classes actually raised in this tree; a class
# missing here under-counts, which is why --check pins the totals.
REFUSAL_CLASSES = frozenset({
    "Refused", "SourceUnavailable", "ScanRefused", "KitRefused",
    "MidiPortRefused", "ControlRefused", "StaleBase",
    "Infeasible", "CalibrationError", "REFUSED_TYPES",
})

# WRONG BEFORE IT WAS RIGHT, recorded because the first table this tool printed
# was quotable and wrong.  `Rejected` was in the set above, which made
# `tools/check_f1_rtl_record.py:153` read as a refusal reported as exit 1 --
# the headline defect this census exists to count.  It is not one.  There are
# TWO unrelated `Rejected` classes in this tree and they mean opposite things:
#
#   tools/check_f1_rtl_record.py:48   a VERDICT.  "REJECTED <record> -- ..."
#                                     exits 1 because the record was read and
#                                     found wanting.  Exit 1 is correct there.
#   fpga/release/qualified_domain.py:96  a REFUSAL.  fpga/uart_host.py:1993
#                                     prints "REFUSED -- outside the qualified
#                                     release domain" and exits 2.
#
# A detector keyed on the class NAME cannot separate them, so the name is
# excluded and the uart_host site is not counted.  That under-counts the exit-2
# family by one and costs nothing in the column that matters: the site was
# already on the side the runner reports truthfully.  The exit-1 family went
# from 4 sites to 3 when this was fixed.


class Refused(Exception):
    """A precondition failed.  REFUSED is a verdict, distinct from a value."""


def tracked_python() -> list[str]:
    """Every tracked `.py` path.  REFUSES if git cannot answer or the tree is
    too small to be the whole repository."""
    try:
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                             text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise Refused(f"git ls-files could not run in {ROOT}: {exc}") from exc
    files = [f for f in out.split() if f.endswith(".py")]
    if len(files) < MIN_PY_FILES:
        raise Refused(
            f"git ls-files found {len(files)} Python files, under the {MIN_PY_FILES} "
            f"this repository has; a partial tree under-reports the census")
    return files


def run_all_no_evidence_code() -> int:
    """The single status `tools/run_all.py` treats as NO-VERDICT, read from its
    source.  REFUSES rather than assuming 2, so this census cannot keep
    claiming a divergence after the runner is repaired."""
    p = ROOT / "tools" / "run_all.py"
    try:
        tree = ast.parse(p.read_text())
    except (OSError, SyntaxError) as exc:
        raise Refused(f"cannot read {p}: {exc}") from exc
    for st in tree.body:
        if isinstance(st, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "VERIFIER_NO_EVIDENCE" for t in st.targets):
            if isinstance(st.value, ast.Constant) and isinstance(st.value.value, int):
                return st.value.value
            raise Refused(f"{p}: VERIFIER_NO_EVIDENCE is not an int literal; "
                          f"the runner's contract cannot be read statically")
    raise Refused(f"{p} defines no VERIFIER_NO_EVIDENCE; the runner's "
                  f"no-evidence status cannot be read")


def _handled_classes(node: ast.ExceptHandler) -> list[str]:
    t = node.type
    if t is None:
        return []
    elts = t.elts if isinstance(t, ast.Tuple) else [t]
    names = []
    for e in elts:
        if isinstance(e, ast.Name):
            names.append(e.id)
        elif isinstance(e, ast.Attribute):
            names.append(e.attr)
    return names


def handler_census(files: list[str]) -> dict[int, list[tuple[str, int]]]:
    """Per exit status, the `except <refusal>: ... return <int>` sites."""
    by: dict[int, list[tuple[str, int]]] = collections.defaultdict(list)
    for f in files:
        if pathlib.Path(f).name.startswith("test_"):
            continue
        try:
            tree = ast.parse((ROOT / f).read_text())
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            if not (set(_handled_classes(node)) & REFUSAL_CLASSES):
                continue
            for st in ast.walk(node):
                if (isinstance(st, ast.Return) and isinstance(st.value, ast.Constant)
                        and isinstance(st.value.value, int)
                        and not isinstance(st.value.value, bool)):
                    by[st.value.value].append((f, st.lineno))
                    break
    return dict(by)


def constant_census(files: list[str]) -> dict[int, list[tuple[str, int]]]:
    """Per exit status, the module-level `REFUSED = <int>` declarations.  These
    are invisible to `handler_census`, which looks for a literal return."""
    by: dict[int, list[tuple[str, int]]] = collections.defaultdict(list)
    for f in files:
        if pathlib.Path(f).name.startswith("test_"):
            continue
        try:
            tree = ast.parse((ROOT / f).read_text())
        except (OSError, SyntaxError):
            continue
        for st in tree.body:
            if not isinstance(st, ast.Assign):
                continue
            if not any(isinstance(t, ast.Name) and t.id == "REFUSED" for t in st.targets):
                continue
            if isinstance(st.value, ast.Constant) and isinstance(st.value.value, int) \
                    and not isinstance(st.value.value, bool):
                by[st.value.value].append((f, st.lineno))
    return dict(by)


def census() -> dict:
    files = tracked_python()
    code = run_all_no_evidence_code()
    handlers, constants = handler_census(files), constant_census(files)
    codes = sorted(set(handlers) | set(constants))
    return {
        "n_python_files": len(files),
        "run_all_no_evidence_code": code,
        "codes": {
            c: {
                "handler_sites": sorted(handlers.get(c, [])),
                "handler_files": sorted({f for f, _ in handlers.get(c, [])}),
                "constant_sites": sorted(constants.get(c, [])),
                # the column that matters: how run_all.py renders this family
                "run_all_state": "NO-VERDICT" if c == code else ("PASS" if c == 0 else "FAIL"),
            }
            for c in codes
        },
    }


def render(res: dict) -> str:
    lines = [
        f"{res['n_python_files']} tracked Python files; tools/run_all.py treats exit "
        f"{res['run_all_no_evidence_code']} and only that as NO-VERDICT.",
        "",
        f"{'exit':>4}  {'handler sites':>13}  {'files':>5}  {'REFUSED= consts':>15}  run_all reports",
    ]
    for c, d in res["codes"].items():
        lines.append(f"{c:>4}  {len(d['handler_sites']):>13}  {len(d['handler_files']):>5}  "
                     f"{len(d['constant_sites']):>15}  {d['run_all_state']}")
    misreported = [c for c, d in res["codes"].items()
                   if c != 0 and d["run_all_state"] == "FAIL"]
    lines += ["", "Refusal families run_all.py renders as FAIL (a RESULT, per its own "
                  "docstring), not as NO-VERDICT:"]
    for c in misreported:
        d = res["codes"][c]
        for f, ln in d["handler_sites"]:
            lines.append(f"  exit {c}  {f}:{ln}")
        for f, ln in d["constant_sites"]:
            lines.append(f"  exit {c}  {f}:{ln}  (REFUSED = {c})")
    if not misreported:
        lines.append("  none -- every refusal family is reported NO-VERDICT")
    return "\n".join(lines)


# Known answers, measured on 344ed6e7 (origin/main, 2026-11-14).  These are
# pinned so a repair shows up as a FAILING self-test rather than as a quietly
# different table that still looks plausible.
EXPECTED = {
    "run_all_no_evidence_code": 2,
    "handler_files": {1: 2, 2: 82, 3: 10},
    "handler_sites": {1: 3, 2: 93, 3: 12},
    "constant_sites": {3: 4},
}


def check() -> tuple[bool, list[str]]:
    res = census()
    out, ok = [], True

    def cmp(label, got, want):
        nonlocal ok
        good = got == want
        ok = ok and good
        out.append(f"  {'ok ' if good else 'RED'}  {label}: got {got}, pinned {want}")

    cmp("run_all NO-VERDICT status", res["run_all_no_evidence_code"],
        EXPECTED["run_all_no_evidence_code"])
    for c, want in EXPECTED["handler_sites"].items():
        cmp(f"exit {c} handler sites", len(res["codes"].get(c, {}).get("handler_sites", [])), want)
    for c, want in EXPECTED["handler_files"].items():
        cmp(f"exit {c} handler files", len(res["codes"].get(c, {}).get("handler_files", [])), want)
    for c, want in EXPECTED["constant_sites"].items():
        cmp(f"exit {c} REFUSED= constants", len(res["codes"].get(c, {}).get("constant_sites", [])), want)

    # The control that makes the census mean something: at least one refusal
    # family must NOT be the runner's no-evidence status, or there is no
    # divergence to report and this tool should be deleted.
    fams = [c for c in res["codes"] if c != 0]
    diverging = [c for c in fams if c != res["run_all_no_evidence_code"]]
    good = len(diverging) > 0
    ok = ok and good
    out.append(f"  {'ok ' if good else 'RED'}  at least one refusal family diverges from the "
               f"runner's status: {diverging}")
    return ok, out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="self-test against pinned answers")
    a = ap.parse_args(argv)
    try:
        if a.check:
            ok, lines = check()
            print("\n".join(lines))
            return 0 if ok else 1
        print(render(census()))
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2            # the dominant family, and the one run_all.py honours
    return 0


if __name__ == "__main__":
    sys.exit(main())
