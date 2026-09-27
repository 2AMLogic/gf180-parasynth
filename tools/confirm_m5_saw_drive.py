#!/usr/bin/env python3
"""Fresh-condition confirmation of the M5 saw operating point (saw drive 0.35, #333).

The candidate was chosen by disclosed engineering judgement (the frozen rule
tied; see docs/scorecard/m5-artifacts-333/saw-drive/). Before it becomes a
preset, it is checked on conditions NOT inspected in that experiment:

  notes      MIDI 42, 54, 66, 78, 90, 102, 114 (the experiment's probe used
             36/48/60/72/84/96/108/120; the phrases used 72/84/96)
  cutoffs    20 kHz (the preset) and 8 kHz, the live CC74's ceiling
  resonance  0 (the preset) and 0.5 (a CC71 midpoint)

Rule, frozen here before any render. Against drive 0.75 at the same volume
calibration, at EVERY fresh point:
  * output relative unwanted energy not worse by > 1.0 dB;
  * upper wanted power (5-20 kHz, relative) not lower by > 0.1 dB;
  * the ladder's harmonic deficit against an ideal saw (mean over h2..h6
    below 20 kHz) not worse -- the mechanism the candidate targets;
  * intended (harmonic) level within +-1.5 dB of the baseline -- the volume
    was calibrated at one note; drive changes the tanh's level law, so this
    bounds how far the calibration drifts across the range;
  * zero output rail samples.
Pulse invariance holds by construction (a saw-only preset); it was asserted
on the phrases in the experiment and is not re-measured here.
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "model"), str(ROOT / "tools")]
import mono_artifact_probe as mp  # noqa: E402
import measure_m5_operating_level as ol  # noqa: E402

BASE, CAND = 0.75, 0.35
NOTES = (42, 54, 66, 78, 90, 102, 114)
CUTOFFS = (20000, 8000)
RESONANCES = (0.0, 0.5)
SAW_VOL = 0.45 * 10 ** (-0.45428 / 20)


def point(drive, vol, note, cut, q):
    with ol.engine_ctx("pulse2x"):
        r = mp.measure_point("pulse2x", note, mp.held_patch(waves=("saw",) * 3, drive=drive, vol=vol,
                                                             cutoff=(cut, cut), q=q))
    o = r["stages"]["output"]
    h = r["stages"]["ladder_out"]["harmonics_rel_h1_db"]
    f0 = r["f0_hz"]
    ks = [k for k in range(2, 7) if f"h{k}" in h and k * f0 < 20000]
    deficit = (sum(h[f"h{k}"] + 20 * math.log10(k) for k in ks) / len(ks)) if ks else None
    return {"unwanted_rel_db": o["unwanted_rel_db"], "unwanted_dbfs": o["unwanted_dbfs"],
            "upper_wanted_rel_db": o["upper_wanted_rel_db"], "intended_dbfs": o["intended_dbfs"],
            "ladder_deficit_db": None if deficit is None else round(deficit, 3),
            "output_rail": r["clip"]["output_rail_samples"]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=pathlib.Path, required=True)
    a = ap.parse_args(argv)
    f = ol.calibrate("pulse2x", CAND)
    rows, fails = [], []
    for cut in CUTOFFS:
        for q in RESONANCES:
            for nt in NOTES:
                b = point(BASE, SAW_VOL, nt, cut, q)
                c = point(CAND, SAW_VOL * f, nt, cut, q)
                why = []
                if c["unwanted_rel_db"] - b["unwanted_rel_db"] > 1.0:
                    why.append("unwanted")
                if b["upper_wanted_rel_db"] is not None and c["upper_wanted_rel_db"] < b["upper_wanted_rel_db"] - 0.1:
                    why.append("darker")
                if b["ladder_deficit_db"] is not None and c["ladder_deficit_db"] < b["ladder_deficit_db"]:
                    why.append("deficit worse")
                if abs(c["intended_dbfs"] - b["intended_dbfs"]) > 1.5:
                    why.append("level drift")
                if c["output_rail"]:
                    why.append("rail")
                row = {"note": nt, "cutoff": cut, "q": q, "baseline": b, "candidate": c, "fails": why}
                rows.append(row)
                if why:
                    fails.append(row)
                print(f"MIDI {nt:3d} cut {cut:5d} q {q:.1f}: unwanted rel {b['unwanted_rel_db']:7.2f} -> "
                      f"{c['unwanted_rel_db']:7.2f}; upper {b['upper_wanted_rel_db']} -> {c['upper_wanted_rel_db']}; "
                      f"deficit {b['ladder_deficit_db']} -> {c['ladder_deficit_db']}; level "
                      f"{c['intended_dbfs'] - b['intended_dbfs']:+.2f} dB; {'OK' if not why else why}", flush=True)
    res = {"candidate_drive": CAND, "vol_factor": round(f, 5), "rule": __doc__.split("Rule, frozen")[1],
           "points": len(rows), "failing_points": len(fails), "confirmed": not fails, "rows": rows,
           "commit": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                                    text=True).stdout.strip(),
           "sources_dirty": subprocess.run(["git", "diff", "--quiet", "HEAD", "--", "tools/confirm_m5_saw_drive.py",
                                            "tools/mono_artifact_probe.py", "model/voice_fx.py"],
                                           cwd=ROOT).returncode != 0}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, indent=1) + "\n")
    print(f"{len(rows) - len(fails)}/{len(rows)} fresh points pass; confirmed={res['confirmed']}; wrote {a.out}")
    return 0 if res["confirmed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
