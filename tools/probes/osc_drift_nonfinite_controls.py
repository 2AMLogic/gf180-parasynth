#!/usr/bin/env python3
"""Mutation controls for the non-finite guards of `model/osc_drift_probe.py`.

`model/test_osc_drift_probe.py` section 7 holds the controls for the
NONFINITE_INPUT precondition and the strict JSON export (#1406). A passing run
cannot say whether they CAN fail (docs/verification-rules.md rule 2), so this
builds six mutants of the two guards and records which tests each reddens.

A mutant counts as caught only when all three hold; anything else is NO
VERDICT, which is red and is not a fail (rule 5):

  1. the unmutated copy is green;
  2. the mutant builds: its pattern is found exactly once in the source;
  3. at least one EXPECTED test fails, nothing outside that set fails, and
     there is no collection error.

M0 is the known answer. It removes the guard altogether, which is the defect
as shipped, so a run that reports anything but "caught" for M0 says the
harness is broken, not the tests.

    .venv/bin/python tools/probes/osc_drift_nonfinite_controls.py

Exit 0 when every mutant is caught, 1 otherwise. The working tree is read and
never written: everything happens under a temporary directory.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = Path("model/osc_drift_probe.py")
TESTS = Path("model/test_osc_drift_probe.py")

GUARD = '''        finite = np.isfinite(x)
        if not finite.all():
            bad = np.flatnonzero(~finite)
            raise Refusal(
                "NONFINITE_INPUT",
                f"{len(bad)} of {len(x)} samples are not finite (first: "
                f"{float(x[bad[0]]):g} at sample {bad[0]}): a corrupt "
                f"recording, not a silent oscillator")
'''

# (name, source to replace, replacement, regex every reddened test must match)
MUTANTS = [
    ("M0 known answer: NONFINITE_INPUT guard removed", GUARD, "",
     r"nonfinite_sample|wherever_it_sits|corrupt_wav|selected_window"),
    ("M1 guard sees NaN only (isfinite -> ~isnan)",
     "finite = np.isfinite(x)", "finite = ~np.isnan(x)",
     r"nonfinite_sample|wherever_it_sits"),
    ("M2 encoder floor dropped (allow_nan=False removed)",
     "indent=1, sort_keys=True,\n                      allow_nan=False)",
     "indent=1, sort_keys=True)",
     r"strict_encoder_alone"),
    ("M3 allowlist loses repeat_r",
     '"wobble_cycles", "repeat_r", "acorr_time_s", "taus_in_window",',
     '"wobble_cycles", "acorr_time_s", "taus_in_window",',
     r"unavailable_diagnostic"),
    ("M4 known diagnostic: an infinity is nulled too",
     "if key in OPTIONAL_DIAGNOSTICS and math.isnan(value):",
     "if key in OPTIONAL_DIAGNOSTICS:",
     r"unexpected_non_finite"),
    ("M5 writer opens the destination BEFORE serializing",
     '''    text = json.dumps(_for_export(payload), indent=1, sort_keys=True,
                      allow_nan=False)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)''',
     '''    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(json.dumps(_for_export(payload), indent=1, sort_keys=True,
                            allow_nan=False))''',
     r"unexpected_non_finite|across_note_block|strict_encoder_alone"),
]


def copy_sources(tree: Path) -> None:
    for folder in ("model", "tools"):
        (tree / folder).mkdir(parents=True, exist_ok=True)
        for source in (ROOT / folder).glob("*.py"):
            shutil.copy(source, tree / folder / source.name)
    shutil.copy(ROOT / "pytest.ini", tree / "pytest.ini")


def run_tests(tree: Path):
    """(exit status, failed test names without their parameters, collection
    errors, last output line) of the whole test file under `tree`."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--tb=no",
         str(TESTS)], cwd=tree, capture_output=True, text=True, check=False)
    out = proc.stdout + proc.stderr
    failed = Counter(re.sub(r"\[.*$", "", name) for name in
                     re.findall(r"^FAILED \S+::(\S+)", out, re.MULTILINE))
    errors = re.findall(r"^ERROR \S+.*$", out, re.MULTILINE)
    lines = out.strip().splitlines()
    tail = lines[-1] if lines else "(no output)"
    return proc.returncode, failed, errors, tail


def is_a_verdict(rc: int, tail: str) -> bool:
    return rc in (0, 1) and ("passed" in tail or "failed" in tail or "no tests ran" in tail)


def main() -> int:
    uncaught = 0
    with tempfile.TemporaryDirectory(prefix="osc-drift-controls-") as tmp:
        tree = Path(tmp)
        copy_sources(tree)
        target = tree / TARGET
        pristine = target.read_text()
        rc, failed, errors, tail = run_tests(tree)
        print(f"unmutated copy: rc={rc}  {tail}  failed={sum(failed.values())} "
              f"errors={len(errors)}")
        if not is_a_verdict(rc, tail) or rc != 0 or failed or errors:
            print("NO VERDICT: the unmutated copy is not green, so there is no baseline")
            return 1

        for name, old, new, expect in MUTANTS:
            found = pristine.count(old)
            print(f"\n{name}")
            if found != 1:
                print(f"  NO VERDICT: pattern found {found} times, so the mutant "
                      f"was not built")
                uncaught += 1
                continue
            target.write_text(pristine.replace(old, new))
            rc, failed, errors, tail = run_tests(tree)
            target.write_text(pristine)
            print(f"  rc={rc}  {tail}  errors={len(errors)}")
            for test, n in sorted(failed.items()):
                print(f"    MOVED  {test} x{n}")
            outside = sorted(t for t in failed if not re.search(expect, t))
            if not is_a_verdict(rc, tail) or errors:
                print("  ==> NO VERDICT: the run did not produce a test result")
                uncaught += 1
            elif not failed:
                print("  ==> SURVIVED: no test went red")
                uncaught += 1
            elif outside:
                print(f"  ==> NOT CAUGHT: tests outside the expectation went red: {outside}")
                uncaught += 1
            else:
                print("  ==> caught by the intended tests only")

    print(f"\n{len(MUTANTS) - uncaught} of {len(MUTANTS)} mutants caught")
    return 1 if uncaught else 0


if __name__ == "__main__":
    sys.exit(main())
