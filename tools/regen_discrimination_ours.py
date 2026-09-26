#!/usr/bin/env python3
"""Re-measure the `ours`-prefixed devices for `docs/discrimination.md` section 8,
reusing the frozen reference-side data rather than re-rendering it.

    .venv/bin/python tools/regen_discrimination_ours.py --out /tmp/refcmp-239

Why this script exists, rather than a bare `reference_compare.py --stage all`
invocation. Section 8.4 and 8.6's `ours` rows were measured before DR 0011
(2026-09-18) and are stale -- issue #239. The reference/comparison side
(`surge-huov`, `surge-rk`, `diva`, `miniv3`) is frozen in
`docs/reference-compare-results.json` and must NOT be re-rendered: most hosts
in this fleet have neither the plugins nor `dawdreamer`
(`model/reference_compare.py`'s own module docstring), so a "reference" that
is re-rendered on demand is not a reference. Only the `ours`-prefixed keys
need a fresh run against the current `model/reference_rigs.py`.

**A precondition this script asserts and REFUSES on, rather than guessing
past** (docs/verification-rules.md; issue #239's own acceptance criterion 6):
`docs/reference-compare-results.json`'s existing `ours`-prefixed keys are NOT
simply "stale data to overwrite". `model/ladder_headroom.py`'s
`frozen_reference_tracking("ours")` reads `tracking-ours` from that exact file
and binds it to the name `ours_rev8`, using it as the deliberate historical
anchor for "drift_before_dr_0011_pp" -- and
`model/test_ladder_headroom.py::test_the_frozen_surge_row_is_not_a_like_for_like_limit_cycle`
hard-asserts that row's spread is 7.92 pp (revision 8) as a locked value. If
this script overwrote `docs/reference-compare-results.json`'s `ours`-prefixed
keys in place, it would silently turn that anchor into a duplicate of "now"
and break the locked test -- a second, unrelated regression this issue's own
scope does not ask for and should not cause as a side effect.

So instead: `docs/reference-compare-results.json` is read-only here, used only
for its non-`ours` (reference) keys. The freshly measured `ours`-prefixed keys
are written to a SEPARATE, clearly named file --
`docs/reference-compare-results-shipped.json` -- "shipped" as in
"post-DR-0011, what actually ships", to contrast with the original file's
`ours` keys, which stay revision-8 on purpose. `docs/discrimination.md`
section 8.4/8.6 are regenerated from the merger of the two.

Refuses (exit 2, prints `REFUSED:`) rather than reporting if:
  * `docs/reference-compare-results.json` is missing or unreadable, or is
    missing a device key this script depends on for the reference side.
  * a subprocess invocation of `model/reference_compare.py` exits non-zero.
  * a device this script rebuilds fails to construct against the current
    `model/reference_rigs.OurLadder` signature (e.g. a future incompatible
    change to `g_rom` / `cut_skew` / `cfg`).
  * a stage/device pair the 8.4/8.6 tables are built from produced **no
    answer** -- either no file at all, or a file containing only
    `reference_compare.py`'s `guard()` refusal stub
    (`[{"ok": false, "not_answerable": "..."}]`). A file is not an answer:
    asserting presence rather than content is exactly the "tool answers when
    it cannot" failure CLAUDE.md names as worse than the tool being absent,
    and it is what `answer_refusal_reason()` below exists to prevent.
    `tools/test_regen_discrimination_ours.py` exercises each of these
    branches, including the one that must NOT fire: a *non-required*
    device's refusal (a control or probe that has nothing to say) is
    reported and carried into the output file, not escalated -- and neither
    is `selfosc-ours-2pole`, whose rows answer `ok: true` with a null
    fingerprint because the 2-pole defect genuinely does not self-oscillate.

Scope, and why it is narrower than "every stage for every ours device": the
docs tables this issue must regenerate use exactly these (stage, device)
pairs, cross-checked directly against `model/reference_compare.py`'s
`report()` (which stages/devices it prints) and against
`docs/discrimination.md` section 8.4/8.6's own prose and tables:
  * `tracking`  for `ours`                         (8.4's tracking table)
  * `response`  for `ours`, `ours-2pole`           (8.6's slope/corner prose)
  * `peakdrive` for `ours`, `ours-tanh256`, `ours-2pole`  (8.6's peak table)
This script also runs `selfosc` for every `ours`-prefixed device and
`response`/`tracking` for the remaining probes/controls, matching the issue's
literal "re-run ... ours, ours-1tanh, ours-2pole, ours-skew30, ours-tanh256,
ours-huovtune" instruction and this repo's "start red, carry every control"
rule -- section 8.5 (which those extra rows would feed) is explicitly out of
this issue's rescoped acceptance criteria and is left untouched, but the
control rows are measured anyway so a defect that should read red on ANY
`ours`-prefixed row is not hidden by only measuring the two devices the
current 8.4/8.6 prose happens to quote.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MODEL = os.path.join(ROOT, "model")
FROZEN = os.path.join(ROOT, "docs", "reference-compare-results.json")
SHIPPED = os.path.join(ROOT, "docs", "reference-compare-results-shipped.json")

sys.path.insert(0, MODEL)
import reference_compare as rc                                      # noqa: E402

OURS_DEVICES = rc.OURS + rc.CONTROLS + rc.PROBES
REF_DEVICES = rc.REFS

# The cheap stages (selfosc, response, tracking) run for every ours-prefixed
# device, so a defect anywhere among them still starts red; "drive" is skipped
# because report() never reads it (no `_load(out, "drive", ...)` call exists
# in `reference_compare.py`'s report()) and "peakdrive" is scoped to only the
# three devices the 8.6 peak table actually shows.
STAGE_ALL_DEVICES = OURS_DEVICES
PEAKDRIVE_DEVICES = ["ours", "ours-tanh256", "ours-2pole"]
# 8.6's "input-referred saturation threshold (-6.2 dBFS) agrees with Mini
# V3's" sentence is read from `bigdrive-ours` (report() section 5), which is
# also pre-DR-0011 in the frozen file -- so it is re-measured too, for `ours`
# only: it is the only ours-prefixed device that sentence names.
BIGDRIVE_DEVICES = ["ours"]

# The stage/device pairs docs/discrimination.md 8.4 and 8.6 are actually built
# from. A refusal in any of these is a REFUSED outcome for the whole script
# (`required_gate`); a refusal anywhere else is announced and carried.
REQUIRED_KEYS = ["tracking-ours", "response-ours", "response-ours-2pole",
                 "peakdrive-ours", "peakdrive-ours-tanh256",
                 "peakdrive-ours-2pole", "bigdrive-ours"]


class Refused(RuntimeError):
    """A precondition of this measurement is unmet. Distinct from a failed
    re-run: this script did not attempt to measure past it."""


def run_stage(python, stage, devices, out_dir):
    cmd = [python, os.path.join(MODEL, "reference_compare.py"),
          "--stage", stage, "--devices", ",".join(devices), "--out", out_dir]
    print(f"$ {' '.join(cmd)}", flush=True)
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
        raise Refused(f"model/reference_compare.py --stage {stage} exited "
                      f"{r.returncode} for devices {devices}")


def load_frozen():
    if not os.path.exists(FROZEN):
        raise Refused(f"{FROZEN} does not exist -- the frozen reference side "
                      "this script depends on is missing.")
    with open(FROZEN) as f:
        frozen = json.load(f)
    missing = [f"{stage}-{dev}" for dev in REF_DEVICES
              for stage in ("tracking", "response", "peakdrive")
              if f"{stage}-{dev}" not in frozen]
    if missing:
        raise Refused("frozen reference file is missing keys this script's "
                      f"merge depends on: {missing}")
    return frozen


def stage_devices_present(out_dir, stage, devices):
    """Which of `devices` actually produced a `{stage}-{device}.json` file.

    This is a FILE-level question only, and is deliberately NOT the gate for
    the rows the docs tables are built from: a device that REFUSED at the
    point of use (guard() in reference_compare.py writes a `not_answerable`
    stub) still writes a file, so presence here means "there is something to
    collect", never "there is an answer". `answer_refusal_reason()` is what
    decides the latter, and `REQUIRED_KEYS` is checked with it in
    `required_gate()`."""
    return [d for d in devices
            if os.path.exists(os.path.join(out_dir, f"{stage}-{d}.json"))]


def answer_refusal_reason(rows):
    """Why `rows` (the payload of one `{stage}-{device}.json`) is a REFUSAL
    rather than an answer, or None if it carries an answer.

    Three shapes count as a refusal, and one deliberately does not:

      * no rows at all (`[]`, or a payload that is not a list) -- nothing was
        measured;
      * any row carrying a non-null `not_answerable` -- `guard()` in
        `model/reference_compare.py` writes exactly
        `[{"device": ..., "ok": false, "not_answerable": "<why>"}]` when a
        stage raises `NotImplementedError`, i.e. when the estimator refused at
        the point of use. One such row is enough: a stage that refused for
        part of its sweep did not answer for that sweep;
      * every row that *reports a status* reports `ok: false` -- the stage ran
        and nothing in it succeeded, so there is nothing for `report()` to read
        even though the file exists.

    Two things are NOT refusals, and getting either wrong is worse than the
    presence-only gate this replaces:

      * a row that answers `ok: true` and reports a null *measurement* because
        the answer is legitimately "none" -- e.g. `selfosc-ours-2pole`, whose
        `f0_zc_ok` is false and whose `h2..h9` are null because the injected
        2-pole defect does not self-oscillate at all. `report()` prints that as
        `none`, which is the correct answer to the question asked, and this
        repo's "carry every control" rule requires that row to exist;
      * a stage whose rows carry no `ok` field at all. **Wrong-then-right, and
        the reason the test file exists**: the first version of this predicate
        refused unless some row had a truthy `ok`, which made the gate
        unsatisfiable -- only the harmonic stages (`selfosc`, `bigdrive`) write
        `ok`; `tracking`, `response` and `peakdrive` rows have no such field
        (verified against the committed
        `docs/reference-compare-results-shipped.json`), so six of the seven
        `required` keys REFUSED on the very data this script had already
        produced and the docs are built from. `tools/test_regen_discrimination_ours.py`
        caught it on its first run. An unsatisfiable gate is worse than no gate
        (CLAUDE.md), so a status is only read where one is reported."""
    if not isinstance(rows, list) or not rows:
        return "no rows were written"
    for r in rows:
        if isinstance(r, dict) and r.get("not_answerable") is not None:
            return f"not answerable: {r['not_answerable']}"
    statused = [r for r in rows if isinstance(r, dict) and "ok" in r]
    if statused and not any(r.get("ok") for r in statused):
        return "every row that reports a status reports ok: false"
    return None


def announce_refusals(shipped, required=None):
    """Print every refusal in `shipped`, blocking or not, and return them.

    A refusal is worth SAYING even where it does not block: a control or probe
    that refused is a fact about the run, and a silently-dropped one is how a
    control stops being carried."""
    required = REQUIRED_KEYS if required is None else required
    found = {}
    for key in sorted(shipped):
        why = answer_refusal_reason(shipped[key])
        if why:
            found[key] = why
            tag = "blocking" if key in required else "not required for 8.4/8.6"
            print(f"  refused ({tag}): {key} -- {why}", flush=True)
    return found


def required_gate(shipped, required=None):
    """Raise `Refused` unless every key the 8.4/8.6 tables are built from
    carries an ANSWER -- not merely a file.

    `guard()` in `reference_compare.py` writes a `not_answerable` stub on a
    refusal and that stub is a perfectly well-formed file, so a
    presence-only check (which is what this script shipped with, and what
    `stage_devices_present` still answers) would let it print a report over a
    measurement that never happened: "a tool that answers when it cannot is
    worse than one that is absent, because its output looks exactly like data"
    (CLAUDE.md; issue #239 acceptance criterion 6).

    Only `REQUIRED_KEYS` block. A refused control or probe is announced and
    carried -- `announce_refusals` -- because a refusal in a device no table
    quotes is information, not a reason to withhold the tables. (In practice
    `reference_compare.py`'s own `report()` is not stub-tolerant for any device
    in its `ORDER`, so a non-required stub still ends this script in REFUSED,
    via a non-zero `--report` exit rather than via this gate. That fails in the
    safe direction; the distinction this function draws is still the one that
    matters, because it is the one that decides whether a *report* is printed
    at all.)"""
    required = REQUIRED_KEYS if required is None else required
    missing = [k for k in required if k not in shipped]
    if missing:
        raise Refused(f"the fresh run did not produce: {missing} -- "
                      "cannot regenerate the 8.4/8.6 tables from this")
    refused = [f"{k} ({answer_refusal_reason(shipped[k])})" for k in required
              if answer_refusal_reason(shipped[k])]
    if refused:
        raise Refused(
            "a measurement the 8.4/8.6 tables are built from REFUSED rather "
            f"than answering: {refused} -- not writing "
            f"{os.path.basename(SHIPPED)} and not printing a report, because "
            "a report built over a refusal stub reads exactly like one built "
            "over data")
    return required


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="/tmp/refcmp-239",
                    help="working directory for the fresh ours-prefixed run")
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--skip-run", action="store_true",
                    help="reuse an already-populated --out directory (for "
                        "iterating on the merge/report step without paying "
                        "for the simulation again)")
    a = ap.parse_args(argv)

    try:
        frozen = load_frozen()

        os.makedirs(a.out, exist_ok=True)
        if not a.skip_run:
            # `--stage all` also pays for reference_compare.py's "drive" stage,
            # which report() never reads (verified: no `_load(out, "drive", ...)`
            # call anywhere in report()) -- so the three stages that feed the
            # docs tables are run individually instead of via "all", to avoid
            # burning simulation time report() would throw away.
            for stage in ("selfosc", "response", "tracking"):
                run_stage(a.python, stage, STAGE_ALL_DEVICES, a.out)
            run_stage(a.python, "peakdrive", PEAKDRIVE_DEVICES, a.out)
            run_stage(a.python, "bigdrive", BIGDRIVE_DEVICES, a.out)

        # Collect the freshly written ours-prefixed keys.
        shipped = {}
        for stage in ("selfosc", "response", "tracking", "peakdrive", "bigdrive"):
            for dev in stage_devices_present(a.out, stage, OURS_DEVICES):
                with open(os.path.join(a.out, f"{stage}-{dev}.json")) as f:
                    shipped[f"{stage}-{dev}"] = json.load(f)

        announce_refusals(shipped)
        # PRECONDITION: the rows this issue's tables are actually built from
        # must carry an ANSWER, not merely a file (see required_gate).
        required_gate(shipped)

        # Build a merged --out directory for `reference_compare.py --report`:
        # fresh ours-prefixed files (already in a.out) + copies of the frozen
        # non-ours reference keys the report also reads.
        merged = os.path.join(a.out, "_merged-for-report")
        os.makedirs(merged, exist_ok=True)
        for stage in ("selfosc", "response", "tracking", "peakdrive", "bigdrive", "drive"):
            for dev in REF_DEVICES:
                key = f"{stage}-{dev}"
                if key in frozen:
                    with open(os.path.join(merged, f"{key}.json"), "w") as f:
                        json.dump(frozen[key], f, indent=1, default=str)
        for key, rows in shipped.items():
            with open(os.path.join(merged, f"{key}.json"), "w") as f:
                json.dump(rows, f, indent=1, default=str)

        report_path = os.path.join(a.out, "report-shipped.txt")
        r = subprocess.run([a.python, os.path.join(MODEL, "reference_compare.py"),
                           "--report", "--out", merged],
                          cwd=ROOT, capture_output=True, text=True)
        if r.returncode != 0:
            raise Refused(f"--report exited {r.returncode}: {r.stderr}")

        # Both artefacts are written only HERE, after a report was actually
        # produced: `docs/reference-compare-results-shipped.json` is the file
        # the docs cite, and a REFUSED run must leave no artefact a later
        # reader could mistake for the product of a complete one.
        with open(SHIPPED, "w") as f:
            json.dump(shipped, f, indent=1, default=str)
        print(f"wrote {SHIPPED} ({len(shipped)} keys)", flush=True)
        with open(report_path, "w") as f:
            f.write(r.stdout)
        print(r.stdout)
        print(f"wrote {report_path}", flush=True)
        return 0
    except Refused as e:
        print(f"REFUSED: {e}", flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
