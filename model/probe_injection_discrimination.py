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

A red is a kill only if the intended assertion failed (issue #1400) -- the
third condition, which this probe was not making. It read any nonzero pytest
status as `RED (good)`, so a suite that raised `ImportError` at collection
under every mutant printed three kills and exited 0 with no mutant having
reached an assertion: the cocotb shape docs/verification-rules.md 1 records,
inside the probe whose job is to hunt it. The decision is therefore not made
here. `verdict()` in `tools/probes/holdout_injected_controls.py` is imported
and applied to each mutant, with this probe's suite and recorded names kept
local:

  * exit 0 after a clean, completed, executed suite run is NO_FIRE -- the suite
    stayed green with the injection neutered, so that mutant SURVIVED and is a
    hole in the test;
  * exit 1 with a failure at a recorded assertion in its test is FIRED;
  * everything else is REFUSED, with no kill credit: a collection error (2),
    the path gone (4), nothing collected (5), zero tests executed (e.g.
    `--collect-only`), any phase error (setup/teardown/collection), an
    ERROR-only run, or a red at an apparatus check or outside the recorded
    tests (#1136).

`EXPECTED` holds the test and assertion that caught each mutant on the real suite.
Overlap is enough: a superset still fires and its extras are printed as
information, so a new test does not make the probe noisy, while a renamed-away
or empty record refuses rather than passing silently.

Re-run 2026-11-26 (`4b4c4935` with this change applied, Python 3.12.3, pytest
9.2.0, 22 tests) -- all three killed, each by its recorded tests:

    RED (good)   envelope patch is a pass-through: 3 test(s) caught it
    RED (good)   centroid injection sets nothing:  1 test(s) caught it
    RED (good)   brightness lock removed:          3 test(s) caught it

Exit 0 if every mutant was killed, 1 if any survived, or 2 if any refused
and none survived. A survivor is a finding about the tree and outranks a refusal, as in
the holdout probe: a mix exits 1 and names both. Exit 2 is REFUSED when a
mutant's search text no longer matches (the probe is measuring a file that has
moved under it), when the unmutated suite is red (issue #1130), or when any
mutant refused and none survived -- the honest answer in each case, not a green.
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
TARGET = ROOT / "model" / "sound_report.py"
TESTS = "model/test_sound_report.py"

sys.path.insert(0, str(ROOT / "tools" / "probes"))
import holdout_injected_controls as hic  # noqa: E402

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

# The failed condition, independent of the test's line number or diagnostic.
EXPECTED = {
    "envelope patch is a pass-through": {
        "test_the_moving_average_injection_really_replaces_drum_verify_envelope":
            hic.Site("assert np.allclose(got, want)"),
        "test_the_moving_average_injection_ripples_at_the_carrier_it_cannot_resolve":
            hic.Site("assert broken_db > clean_db + 3.0"),
        "test_the_moving_average_injection_shortens_the_decay_it_is_asked_to_measure":
            hic.Site('assert broken["t20_ms"] < 0.8 * clean["t20_ms"]'),
    },
    "centroid injection sets nothing": {
        "test_the_centroid_injection_switches_which_field_the_brightness_property_reads":
            hic.Site("assert amplitude > 2.0 * power"),
    },
    "brightness lock removed": {
        "test_the_brightness_property_is_locked_and_the_lock_is_the_power_weighted_one":
            hic.Site("assert key in sr.LOCKS"),
        "test_the_lock_changelog_agrees_with_the_live_table":
            hic.Site("assert problems == []"),
        "test_check_locks_exits_nonzero_when_the_record_disagrees":
            hic.Site('assert sr.main(["--check-locks"]) == 0'),
    },
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

    base = hic.run_baseline(ROOT, TESTS)
    if base.outcome == hic.REFUSED:
        print(f"probe REFUSED -- baseline `pytest {TESTS}`: {base.reason}. No mutant was run.")
        print(base.tail.rstrip())
        return 2

    survivors = []
    refused = 0    # nothing was measured for that mutant: the apparatus, not the tree
    for name, (old, new) in MUTANTS.items():
        TARGET.write_text(original.replace(old, new, 1))
        try:
            rc, report, _ = hic.run_suite(ROOT, TESTS)
        finally:
            TARGET.write_text(original)
        res = hic.verdict(rc, report, EXPECTED.get(name, {}), suite=TESTS)
        if res.outcome == hic.REFUSED:
            refused += 1
            print(f"{'REFUSED':24s}  {name}: {res.reason}")
        elif res.outcome == hic.NO_FIRE:
            survivors.append(name)
            print(f"{'SURVIVED -- NOT COVERED':24s}  {name}: {res.reason}")
        else:
            print(f"{'RED (good)':24s}  {name}: {len(res.failed)} test(s) caught it")
            for test in res.failed:
                print(f"    {test}")
            if res.other:
                print(f"    (other failures, information only: {', '.join(res.other)})")

    # A survivor -- a hole in the test -- outranks a refusal, as in the holdout
    # probe: 1 when any mutant survived (a mix included), else 2 when any
    # refused, else 0. Exit 2 is the runner's NO-VERDICT channel
    # (tools/exit_contract.py).
    if survivors:
        print(f"\n{len(survivors)} mutant(s) survived; those injections are not "
              f"actually covered." + (f" {refused} refused." if refused else ""))
        return 1
    if refused:
        print(f"\n{refused} mutant(s) REFUSED -- the reasons are above; nothing was "
              "measured for them, and no kill is claimed.")
        return 2
    print(f"\nall {len(MUTANTS)} mutants killed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
