#!/usr/bin/env python3
"""Would `model/test_sound_report.py` notice if an injection stopped injecting?

    .venv/bin/python model/probe_injection_discrimination.py

`test_sound_report.py` exists to assert condition 2 of the three-condition
rule (docs/verification-rules.md 5): that each historical-bug injection
**builds, activates and actually executes.** That is a claim about a test, and
a test that cannot fail is the thing this repository keeps shipping -- so the
claim needs its own evidence rather than an argument.

This probe neuters each injection in `model/sound_report.py` in turn, runs the
test file against the neutered tree, restores it, and reports whether the
suite went red. A mutant that SURVIVES is a hole: the test looked like it
covered that injection and did not.

The three mutants are chosen to be the failures that would actually happen,
not arbitrary edits:

  * **the envelope patch becomes a pass-through** -- the module attribute is
    still rebound, so a naive "did the attribute change?" assertion passes,
    but `dv.envelope` computes exactly what it computed before. This is the
    shape of the negative control this repository once shipped that mutated a
    signature into invalid Python and "passed";
  * **the centroid injection sets nothing** -- `make(ctx)` runs, raises
    nothing, and changes no behaviour;
  * **the brightness lock is removed** -- the property then reports "no lock
    recorded yet" and passes whatever it measures, so the injection becomes
    unsatisfiable however far it moves the number.

Result on this tree, 2026-09-26 -- all three killed, no survivors:

    RED (good)   envelope patch is a pass-through: 3 failed, 4 passed
    RED (good)   centroid injection sets nothing:  1 failed, 6 passed
    RED (good)   brightness lock removed:          1 failed, 6 passed

Exit 0 if every mutant was killed, 1 if any survived, 2 if the probe could not
run at all (a mutant's search text no longer matches -- which means the probe
is measuring a file that has moved under it, and REFUSED is the honest answer,
not a green).
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
TARGET = ROOT / "model" / "sound_report.py"
TESTS = "model/test_sound_report.py"

MUTANTS: dict[str, tuple[str, str]] = {
    "envelope patch is a pass-through": (
        '            if kind == "moving_average":\n'
        '                return np.abs(am.moving_average_envelope(x, 5.0, sr))\n'
        '            return orig(x, sr, win_ms)',
        '            return orig(x, sr, win_ms)'),
    "centroid injection sets nothing": (
        '        ctx["centroid_weight"] = weight',
        '        pass'),
    "brightness lock removed": (
        '    ("SD", "brightness (power centroid)"): 1918.08,\n',
        ''),
}


def main(argv=None) -> int:
    original = TARGET.read_text()

    missing = [name for name, (old, _) in MUTANTS.items() if old not in original]
    if missing:
        print("probe REFUSED -- these mutants no longer match model/sound_report.py:")
        for name in missing:
            print(f"    {name}")
        print("The file has changed under the probe. Update the mutants; do not read this as a pass.")
        return 2

    survivors = []
    for name, (old, new) in MUTANTS.items():
        TARGET.write_text(original.replace(old, new, 1))
        try:
            r = subprocess.run([sys.executable, "-m", "pytest", TESTS,
                                "-q", "--no-header", "--tb=no"],
                               cwd=ROOT, capture_output=True, text=True)
        finally:
            TARGET.write_text(original)
        tally = next((l for l in reversed(r.stdout.splitlines())
                      if "passed" in l or "failed" in l or "error" in l), r.stdout[-200:])
        if r.returncode == 0:
            survivors.append(name)
            print(f"{'SURVIVED -- NOT COVERED':24s}  {name}: {tally}")
        else:
            print(f"{'RED (good)':24s}  {name}: {tally}")

    if survivors:
        print(f"\n{len(survivors)} mutant(s) survived; those injections are not actually covered.")
        return 1
    print(f"\nall {len(MUTANTS)} mutants killed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
