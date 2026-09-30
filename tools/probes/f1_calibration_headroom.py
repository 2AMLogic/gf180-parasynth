#!/usr/bin/env python3
"""Headroom / resonance sanity check of surge-type2-clean-v1's reciprocal output
compensation (plan074 D, last bullet). NOT a filter-research sweep.

    tools/probes/f1_calibration_headroom.py --json docs/scorecard/f1-calibrated/headroom.json

The calibration scales the ladder's input by 1/4 and its output by 4 (ogain =
2Vt/(vpu*s)*(1+2*res)). At res 0 and small signal that is level-neutral by
construction. It is NOT neutral where the tanh stages were compressing: the
resonant peak and self-oscillation are limited by the tanh, whose output is
then multiplied by 4x the legacy ogain. So the question is concrete: does the
calibrated setting put the ladder's 19-bit output word (Q4.15, +-8.0) or the
final 16-bit output on a rail where the legacy words do not?

Conditions (the selected Mono voice, `mono_m5a_score._voice_for_engine`, one
saw note 45 at mixer weight 1.0 through a fixed 1 kHz cutoff, drive 1.0, the
F1 drive; 0.4 s): res 0, 0.5, 1.0 and 1.1 (past self-oscillation onset), each
with the legacy conversion and with the calibration. Plus res 1.1 with the
input at mixer weight 0.01 (set in the weight register; the mixer peak is
asserted), where the self-oscillation level is set by the loop, not the input.

WRONG-THEN-RIGHT: the first run built the 0.01 input through `mix=`, which
`mix_weights` normalises back to full scale; that row was byte-identical to
the full-level row. Caught by reading the table, now asserted in code.

Pre-declared screen, committed with this file before it was first run:
  SCREEN  the calibration adds no ladder-output rail samples and no 16-bit
          output rail samples in any condition where the legacy words have
          none. Levels are REPORTED (dB, calibrated minus legacy), not judged:
          a louder resonance is a property to know, not a defect by itself.

Exit: 0 screen passed, 1 screen failed (a measured result), 2 refused.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
for _p in ("model", "tools"):
    sys.path.insert(0, str(ROOT / _p))

import voice_fx as vf                # noqa: E402
import mono_m5a_score as m5          # noqa: E402

CAL = "surge-type2-clean-v1"
CUT = 1000
SECONDS = 0.4
CONDITIONS = [(0.0, 1.0), (0.5, 1.0), (1.0, 1.0), (1.1, 1.0), (1.1, 0.01)]   # (res, mix)
LADDER_RAIL = (1 << (vf.LADDER_OUT_BITS - 1)) - 1


def db(x: float) -> float:
    return 20 * math.log10(max(x, 1e-12))


def render(res: float, mix: float, cal: str | None) -> dict:
    prof = m5.engine_configuration("selected")
    v = m5._voice_for_engine(prof)
    kw = {} if cal is None else {"filter_calibration": cal}
    note = v.note_on(45, SECONDS, gate=SECONDS, waves=("saw", "saw", "saw"),
                     detune=(0.0, 0.0, 0.0), mix=(mix, 0.0, 0.0), cutoff=(CUT, CUT), q=res,
                     drive=1.0, track=0.0, amp=(0.001, 0.25, 1.0, 0.05), **kw)
    if mix != 1.0:
        # `mix_weights` floor-normalises (a lone oscillator at 0.01 is weight
        # 1.0), so a small input is set in the WEIGHT register directly --
        # and asserted below, because the first run of this probe did not.
        note["regs"]["weights"] = [int(round(mix * 32768)), 0, 0, 0]
    v.reset()
    y = np.asarray(v.run(note), dtype=np.int64)
    mixed_peak = int(np.max(np.abs(np.asarray(v.trace["mixed"], dtype=np.int64))))
    if abs(mixed_peak / 32768 - mix) > 0.25 * mix:
        raise vf.CalibrationError(f"precondition: mixer peak {mixed_peak} is not the requested "
                                  f"input level {mix}")
    lad = np.asarray(v.trace["ladder"], dtype=np.int64)
    tail = lad[len(lad) // 2:]                      # steady state: second half
    recon = dict(getattr(v.ladder, "last_reconstruction", None) or {})
    return {"k": note["regs"]["k"], "gain": note["regs"]["gain"], "ogain": note["regs"]["ogain"],
            "calibration": note["regs"].get("filter_calibration"),
            "mixer_peak_q15": mixed_peak,
            "ladder_peak": int(np.max(np.abs(lad))),
            "ladder_peak_dbfs_q15": round(db(float(np.max(np.abs(lad))) / 32768), 3),
            "ladder_rail_samples": int(np.count_nonzero(np.abs(lad) >= LADDER_RAIL)),
            "ladder_rms_dbfs_q15_steady": round(db(float(np.sqrt(np.mean(tail.astype(float) ** 2)))
                                                   / 32768), 3),
            "output_peak": int(np.max(np.abs(y))),
            "output_rail_samples": int(np.count_nonzero((y >= 32767) | (y <= -32768))),
            "reconstruction_would_clip": recon.get("would_clip_count"),
            "output_sha256": hashlib.sha256(y.tobytes()).hexdigest()[:16]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)
    rows, fails = [], []
    for res, mix in CONDITIONS:
        try:
            leg, cal = render(res, mix, None), render(res, mix, CAL)
        except vf.CalibrationError as e:
            print(f"REFUSED  res {res}: {e}")
            return 2
        if cal["calibration"] != CAL or (cal["gain"], cal["ogain"]) == (leg["gain"], leg["ogain"]):
            print(f"REFUSED  res {res}: the calibrated render did not carry its words")
            return 2
        row = {"res": res, "mix": mix, "cutoff_hz": CUT, "drive": 1.0, "legacy": leg,
               "calibrated": cal,
               "steady_level_change_db": round(cal["ladder_rms_dbfs_q15_steady"]
                                               - leg["ladder_rms_dbfs_q15_steady"], 3),
               "peak_change_db": round(cal["ladder_peak_dbfs_q15"] - leg["ladder_peak_dbfs_q15"], 3)}
        new_rail = ((cal["ladder_rail_samples"] > 0 and leg["ladder_rail_samples"] == 0)
                    or (cal["output_rail_samples"] > 0 and leg["output_rail_samples"] == 0))
        row["screen"] = "fail" if new_rail else "pass"
        if new_rail:
            fails.append(f"res {res} mix {mix}")
        rows.append(row)
        print(f"res {res:4.2f} mix {mix:4.2f} | legacy g/og {leg['gain']}/{leg['ogain']} "
              f"peak {leg['ladder_peak']:6d} rms {leg['ladder_rms_dbfs_q15_steady']:+7.2f} "
              f"rail {leg['ladder_rail_samples']}/{leg['output_rail_samples']} | "
              f"cal g/og {cal['gain']}/{cal['ogain']} peak {cal['ladder_peak']:6d} "
              f"rms {cal['ladder_rms_dbfs_q15_steady']:+7.2f} "
              f"rail {cal['ladder_rail_samples']}/{cal['output_rail_samples']} | "
              f"d level {row['steady_level_change_db']:+6.2f} dB  {row['screen']}")
    if a.json:
        doc = {"what": "surge-type2-clean-v1 headroom/resonance sanity check (plan074 D)",
               "screen": "no new ladder-output (19-bit) or 16-bit output rail samples vs legacy",
               "ladder_rail": LADDER_RAIL, "seconds": SECONDS,
               "engine_profile": m5.engine_configuration("selected")["name"],
               "inputs": {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest()[:16] for p in (
                   "model/voice_fx.py", "model/fixed.py", "model/filter_rate_chain.py",
                   "tools/probes/f1_calibration_headroom.py")},
               "rows": rows, "screen_failures": fails}
        p = pathlib.Path(a.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(doc, indent=1) + "\n")
        print(f"wrote {p}")
    if fails:
        print(f"SCREEN FAILED  new rail samples under the calibration: {fails}")
        return 1
    print("SCREEN PASSED  no new rail samples under the calibration")
    return 0


if __name__ == "__main__":
    sys.exit(main())
