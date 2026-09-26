#!/usr/bin/env python3
"""Shark-tooth inharmonic energy, BLEP only against BLEP + BLAMP (issue #48).

    python3 tools/measure_shark_blamp.py                       # the table below
    python3 tools/measure_shark_blamp.py --out build/scorecard/shark-blamp.json

This is the instrument that produced DR 0017's numbers. It exists as a file
rather than as a paragraph because a number without the code that produced it
is a claim, not evidence.

WHAT IS AND IS NOT COMPARED HERE
--------------------------------
The two arms are ONE implementation, not two. `voice_fx.BLAMP_THIRD` is the
Q0.16 approximation of the ramp residual's 1/3; forcing it to zero makes every
BLAMP residual truncate to zero, so the "before" arm is byte-for-byte the
expression `main` shipped before #48 -- a BLEP-corrected saw mixed with a plain
triangle. No second copy of the old code is kept, and therefore none can drift.

Two estimators, both from `model/audio_measure.py` and both ground-truthed in
`model/test_audio_measure.py`, because they fail differently:

  inharmonic_fraction_db  energy OUTSIDE +-5 bins of every harmonic (DR 0001's
                          measure, so the numbers compare with that record)
  foldback_alias_db       energy AT the predicted image frequencies of the
                          harmonics above Nyquist, and nowhere else

THE PRECONDITION THIS TOOL REFUSES ON
-------------------------------------
`inharmonic_fraction_db` measures its own leakage floor per call. Issue #61 lost
a whole comment thread to a floor-limited estimator hiding a real 3 dB effect,
so a row whose EITHER arm sits within `--min-headroom` dB of that floor is
reported as **REFUSED**, not as a number. REFUSED is a first-class outcome here:
it means the apparatus could not answer, which is different from an answer of
zero. `foldback_alias_db` refuses on its own terms (image/harmonic collisions),
and its refusal is carried through the same way.

WHAT THIS TOOL DOES NOT DO
--------------------------
It does not compare against Surge XT or Mini V3. Those rigs
(`model/reference_rigs.py`) load macOS VST3 bundles from
`/Library/Audio/Plug-Ins/VST3`; on a host without them there is nothing to
measure and `--require-references` REFUSES rather than printing a table that
looks like a plugin comparison and is not one. The before/after question this
tool answers does not need a plugin: both arms are ours, at matched pitch, with
the same estimator, so the reference's own systematics cancel out of the DELTA
entirely. A reference is needed for the MARGIN (ours against theirs), which is
`model/reference_voice.py --stage osc`, not this.
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import subprocess
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "audition"))

import audio_measure as am                                          # noqa: E402
import dsp                                                          # noqa: E402
import voice_fx as vf                                               # noqa: E402

SR = dsp.SR
# A3 .. A7: four octaves, so the pitch dependence is a trend and not two points.
NOTES = [57, 69, 81, 93, 105]
REFERENCE_RIG_PATHS = ("/Library/Audio/Plug-Ins/VST3/Surge XT.vst3",
                       "/Library/Audio/Plug-Ins/VST3/Mini V3.vst3")


class Refused(RuntimeError):
    """A precondition this tool asserts at the point of use failed. Raised
    rather than returned when the whole run cannot be answered."""


def render(note: int, third: int, seconds: float = 0.5) -> tuple[np.ndarray, float]:
    """A shark-tooth with `vf.BLAMP_THIRD` forced to `third`, full scale."""
    old = vf.BLAMP_THIRD
    vf.BLAMP_THIRD = third
    try:
        inc = dsp.phase_inc(dsp.note_hz(note))
        y = vf.OscFx("shark", blep=True).render(int(seconds * SR), inc)
    finally:
        vf.BLAMP_THIRD = old
    return y.astype(np.float64) / 32768.0, inc * SR / (1 << 24)


def _inharmonic(x, f0, min_headroom):
    e = am.inharmonic_fraction_db(x, f0)
    if not e.ok:
        return None, f"estimator refused: {e.reason}", None
    head = float(e.detail["headroom_db"])
    if head < min_headroom:
        return None, (f"REFUSED: {e.value:.2f} dB is only {head:.1f} dB above the "
                      f"estimator's own measured floor ({e.detail['floor_db']:.1f} dB)"), head
    return float(e.value), None, head


def _foldback(x, f0):
    e = am.foldback_alias_db(x, f0)
    if not e.ok:
        return None, f"estimator refused: {e.reason}"
    return float(e.value), None


def measure(notes=None, *, min_headroom: float = 10.0, seconds: float = 0.5) -> dict:
    notes = list(notes or NOTES)
    if len(notes) < 3:
        raise Refused("at least three notes are needed for a pitch trend; "
                      f"{len(notes)} were asked for")
    if not (max(notes) - min(notes)) >= 24:
        raise Refused("the acceptance criterion is at least two octaves; "
                      f"notes {min(notes)}..{max(notes)} span "
                      f"{(max(notes) - min(notes)) / 12:.2f}")
    third = vf.BLAMP_THIRD
    if third == 0:
        raise Refused("voice_fx.BLAMP_THIRD is 0, so both arms are the same signal "
                      "and the delta would be identically zero")
    rows = []
    for note in notes:
        before, f0 = render(note, 0, seconds)
        after, _ = render(note, third, seconds)
        if np.array_equal(before, after):
            rows.append(dict(note=note, f0_hz=f0,
                             refused="the two arms are bit-identical: no BLAMP window "
                                     "opened at this pitch, so nothing is being measured"))
            continue
        a, a_why, a_head = _inharmonic(before, f0, min_headroom)
        b, b_why, b_head = _inharmonic(after, f0, min_headroom)
        fa, fa_why = _foldback(before, f0)
        fb, fb_why = _foldback(after, f0)
        row = dict(note=note, f0_hz=f0,
                   inharmonic_before_db=a, inharmonic_after_db=b,
                   inharmonic_headroom_before_db=a_head, inharmonic_headroom_after_db=b_head,
                   foldback_before_db=fa, foldback_after_db=fb,
                   peak_correction_lsb=int(np.abs(
                       np.round((after - before) * 32768.0)).max()))
        row["inharmonic_gain_db"] = None if (a is None or b is None) else a - b
        row["foldback_gain_db"] = None if (fa is None or fb is None) else fa - fb
        why = [w for w in (a_why, b_why, fa_why, fb_why) if w]
        if why:
            row["refused"] = "; ".join(sorted(set(why)))
        rows.append(row)
    answered = [r for r in rows if r.get("inharmonic_gain_db") is not None]
    if not answered:
        raise Refused("no row could be answered; see each row's `refused` field")
    return dict(
        tool="tools/measure_shark_blamp.py",
        issue=48,
        decision_record="spec/decision-records/0017-blamp-on-the-shark-tooths-corner.md",
        source_commit=_commit(),
        blamp_third=third,
        sr=SR,
        seconds=seconds,
        min_headroom_db=min_headroom,
        estimators=dict(
            inharmonic="audio_measure.inharmonic_fraction_db, Blackman-Harris-4, "
                       "+-5-bin harmonic guards, floor measured per call",
            foldback="audio_measure.foldback_alias_db, predicted image bins only"),
        before_arm="voice_fx.BLAMP_THIRD forced to 0 -- every BLAMP residual "
                   "truncates to zero, which is the pre-#48 expression exactly",
        reference_rigs_available=[p for p in REFERENCE_RIG_PATHS
                                  if pathlib.Path(p).exists()],
        rows=rows,
        trend=_trend(answered),
    )


def _trend(answered: list[dict]) -> dict:
    """The claim being defended is the DIRECTION and the pitch dependence, not a
    digit, so it is computed rather than eyeballed off the table."""
    gains = [r["inharmonic_gain_db"] for r in answered]
    return dict(
        rows_answered=len(answered),
        octaves=(max(r["note"] for r in answered)
                 - min(r["note"] for r in answered)) / 12.0,
        worst_regression_db=min(gains),
        best_gain_db=max(gains),
        monotone_in_pitch=all(y >= x - 0.35 for x, y in zip(gains, gains[1:])),
    )


def _commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
                              capture_output=True, text=True).stdout.strip()
    except Exception:                                        # pragma: no cover
        return "unknown"


def _cell(v, w=7, p=2):
    """A refusal renders as REFUSED, never as a number and never as a blank
    that could be read as zero."""
    return f"{'REFUSED':>{w}}" if v is None else f"{v:{w}.{p}f}"


def render_table(report: dict) -> str:
    out = [f"shark-tooth inharmonic energy, BLEP only -> BLEP + BLAMP "
           f"(BLAMP_THIRD = {report['blamp_third']})",
           "  note     f0 Hz    inharmonic dB       gain   headroom before/after   "
           "  foldback dB       gain   peak corr",
           "  " + "-" * 112]
    for r in report["rows"]:
        if "inharmonic_before_db" not in r:
            out.append(f"  {r['note']:4d}  {r['f0_hz']:8.1f}   "
                       f"REFUSED -- {r['refused']}")
            continue
        out.append(
            f"  {r['note']:4d}  {r['f0_hz']:8.1f}   "
            f"{_cell(r['inharmonic_before_db'])} -> {_cell(r['inharmonic_after_db'])}  "
            f"{_cell(r['inharmonic_gain_db'], 6)}   "
            f"{_cell(r['inharmonic_headroom_before_db'], 8, 1)} / "
            f"{_cell(r['inharmonic_headroom_after_db'], 8, 1)}   "
            f"{_cell(r['foldback_before_db'])} -> {_cell(r['foldback_after_db'])}  "
            f"{_cell(r['foldback_gain_db'], 6)}   "
            f"{r['peak_correction_lsb']:5d} LSB")
        if r.get("refused"):
            out.append(f"        why: {r['refused']}")
    t = report["trend"]
    out += ["",
            f"  {t['rows_answered']} rows answered over {t['octaves']:.2f} octaves; "
            f"worst {t['worst_regression_db']:+.2f} dB, best {t['best_gain_db']:+.2f} dB; "
            f"monotone in pitch: {t['monotone_in_pitch']}",
            "  A NEGATIVE gain would mean BLAMP made the aliasing worse. The reference-rig",
            "  arm (Surge XT / Mini V3) is "
            + (f"available: {report['reference_rigs_available']}"
               if report["reference_rigs_available"]
               else "NOT available on this host; see the module docstring.")]
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--note", type=int, nargs="+", default=NOTES,
                    help="MIDI notes to measure (default A3..A7, four octaves)")
    ap.add_argument("--seconds", type=float, default=0.5)
    ap.add_argument("--min-headroom", type=float, default=10.0,
                    help="dB a reading must clear the estimator's own measured floor by "
                         "before it is reported as a number rather than REFUSED")
    ap.add_argument("--require-references", action="store_true",
                    help="refuse unless the Surge XT / Mini V3 rigs are installed")
    ap.add_argument("--out", default=None, help="also write the report as JSON")
    args = ap.parse_args(argv)
    if args.require_references:
        missing = [p for p in REFERENCE_RIG_PATHS if not pathlib.Path(p).exists()]
        if missing:
            print("REFUSED: --require-references was asked for and these rigs are "
                  "not installed on this host:", file=sys.stderr)
            for p in missing:
                print(f"  {p}", file=sys.stderr)
            return 3
    try:
        report = measure(args.note, min_headroom=args.min_headroom, seconds=args.seconds)
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 3
    print(render_table(report))
    if args.out:
        out = ROOT / args.out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2) + "\n")
        print(f"\n  report: {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
