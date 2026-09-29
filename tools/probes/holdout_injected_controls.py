#!/usr/bin/env python3
"""Start red: three injected defects, each of which MUST turn
`tools/test_holdout.py` red, and the list of tests that caught each one.

    python3 tools/probes/holdout_injected_controls.py

A suite that passes the first time it is run has told you nothing about what it
would have caught (CLAUDE.md: start red, carry injected-bug controls). The
sealing mechanism has three assertions that a reader has to take on trust
otherwise, so each one is deleted here in turn, in a copy of the tree, and the
suite is re-run against it:

    GATE_OFF       tools/run_case.py stops asking holdout.assert_readable
    CLEAN_OFF      holdout.seal_git_state stops checking `git diff HEAD`
    STALE_OFF      holdout.check stops comparing the ledger's seal hash

Recorded result (2026-09-28, 21 tests):

    GATE_OFF    4 caught it: test_a_sealed_holdout_whose_reference_is_not_frozen
                _is_a_stated_no_verdict, test_an_unsealed_holdout_case_is_refused
                _and_carries_no_distance, test_an_unsealed_seal_directory_makes
                _f1d_refuse_for_the_seal_and_not_the_clip, test_the_runner
                _records_a_read_with_the_seal_hash_and_the_model_state
    CLEAN_OFF   1 caught it: test_a_seal_with_uncommitted_modifications_is_refused
    STALE_OFF   1 caught it: test_a_seal_edited_after_it_was_read_is_reported_stale

`test_an_injected_control_never_consumes_a_sealed_reading` deliberately does NOT
catch GATE_OFF, and that is right: with the gate gone nothing is recorded at all,
so its assertion (an empty ledger) still holds. A control whose absence is
explained is worth more than one counted as coverage it does not provide.

Each injection is deleted in a COPY of the worktree, never in place: a control
that can leave the tree broken behind it is a control nobody runs twice.
"""
from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent

INJECTS = {
    "GATE_OFF": ("tools/run_case.py",
                 r"    if is_holdout\(case\):",
                 "    if False and is_holdout(case):"),
    "CLEAN_OFF": ("tools/holdout.py",
                  r'    if _git\("diff", "--name-only", "HEAD", "--", rel\)\.strip\(\):',
                  '    if False:'),
    "STALE_OFF": ("tools/holdout.py",
                  r'        if e\.get\("generation"\) == seal\["generation"\] and \\',
                  '        if False and \\'),
}


def run(name: str) -> tuple[int, list[str]]:
    rel, pattern, replacement = INJECTS[name]
    with tempfile.TemporaryDirectory() as tmp:
        work = pathlib.Path(tmp) / "tree"
        shutil.copytree(ROOT, work, symlinks=True,
                        ignore=shutil.ignore_patterns("build", ".venv", "__pycache__"))
        target = work / rel
        text = target.read_text()
        # A lambda, not a template: a replacement containing a backslash is not
        # a regex escape here and must not be parsed as one.
        new, n = re.subn(pattern, lambda _m: replacement, text, count=1)
        if n != 1:
            print(f"{name}: REFUSED -- the injection point is gone from {rel}; "
                  f"this control cannot fire and must be repaired, not skipped")
            return 2, []
        target.write_text(new)
        out = subprocess.run([sys.executable, "-m", "pytest", "tools/test_holdout.py",
                              "-q", "--no-header", "-p", "no:cacheprovider"],
                             cwd=work, capture_output=True, text=True)
        failed = sorted({ln.split("::")[1].split()[0]
                         for ln in out.stdout.splitlines()
                         if ln.startswith("FAILED tools/test_holdout.py::")})
        return out.returncode, failed


def main() -> int:
    bad = 0
    for name in INJECTS:
        code, failed = run(name)
        if code == 0:
            print(f"{name}: CONTROL DID NOT FIRE -- the suite stayed green with the "
                  f"assertion deleted")
            bad += 1
            continue
        if code == 2:
            bad += 1
            continue
        print(f"{name}: red, {len(failed)} test(s) caught it")
        for f in failed:
            print(f"    {f}")
    print("every control fired" if not bad else f"{bad} control(s) did not fire")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
