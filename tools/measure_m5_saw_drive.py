#!/usr/bin/env python3
"""M5 saw-only operating level: lower ladder drive on the m5a-saw preset only (#333).

The shared-drive experiment (operating-level/) was a negative result because
saw and pulse want opposite things: the saw brightened monotonically as drive
fell while the pulse, at its 14,073 Hz cutoff, darkened. R1 already ships the
saw and pulse as separate presets, and the M5 phrase already overrides cutoff
and volume on saw segments only, so this candidate lowers drive on SAW
segments only, with the saw volume raised to restore level. Pulse segments are
untouched, and that is asserted: every pulse event must be bit-identical to the
baseline's measurements.

Candidate budget, fixed before any render: saw drive 0.5, 0.35, 0.3 (0.3 is
the lowest whose calibrated volume can fit the host's 0..1 range, per the
shared-drive run's factor at 0.35 of 1.92 on a 0.44 preset volume).
Volume calibration: once per drive, on the development held saw at MIDI 84.

Selection rule (frozen): on M5A only (MIDI 84/96), the candidate with the
lowest Harmonic shape error such that
  * |Gain error| stays within 0.5 dB of the baseline's,
  * Foldback <= 3 dB, Clipping 0, Envelope attack error not worse by > 0.5 ms,
  * saw upper wanted power (probe, MIDI 84 and 96) not below the baseline's,
  * every pulse event identical to the baseline (the invariance control).
A candidate that does not improve on the baseline is not a winner.
Confirmation: M5B (its MIDI 72 saw was not used to select) and probe points at
untouched notes (36, 48, 60, 72, 108, 120): relative unwanted energy not worse
by > 1 dB, no output rail samples.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "model"), str(ROOT / "tools"), str(ROOT / "fpga"), str(ROOT / "fpga/release")]
import mono_m5a_score as score  # noqa: E402
import mono_artifact_probe as mp  # noqa: E402
import measure_m5_operating_level as ol  # noqa: E402

BASE_DRIVE = 0.75
CANDIDATES = (0.5, 0.35, 0.3)
SAW_VOL = 0.45 * 10 ** (-0.45428 / 20)
UNTOUCHED = (36, 48, 60, 72, 108, 120)
DEV = (84, 96)


@contextlib.contextmanager
def saw_only(drive: float, vol_factor: float):
    orig = score._patch_for_wave

    def patch_for_wave(patch, wave, pulse_shape=score.M5A_PULSE_WAVE):
        p = orig(patch, wave, pulse_shape)
        if wave == "saw":
            p = {**p, "drive": drive, "vol": p["vol"] * vol_factor}
        return p
    score._patch_for_wave = patch_for_wave
    try:
        yield
    finally:
        score._patch_for_wave = orig


def phrase(engine, case, drive, f):
    with ol.engine_ctx(engine), saw_only(drive, f):
        m = score.measure(case_id=case, voice_factory=lambda: ol.voice(engine),
                          model_label=f"saw-drive {drive}",
                          output_path=ROOT / f"build/sawdrive/{engine}-{case}-d{drive}.wav")
    return {"errors": {k: v["error"] for k, v in m["metrics"].items()},
            "events": [{"wave": e["wave"], "midi": e["midi"],
                        "harmonic_error_db": e["harmonic_error_db_model_minus_reference"],
                        "foldback_excess_db": e["foldback_db"]["excess_over_reference_db"],
                        "gain_dbfs": e["gain_dbfs"], "envelope_ms": e["envelope_ms"]}
                       for e in m["event_diagnostics"]]}


def probe(engine, drive, f):
    out = {}
    for nt in DEV + UNTOUCHED:
        with ol.engine_ctx(engine):
            r = mp.measure_point(engine, nt, mp.held_patch(waves=("saw",) * 3, drive=drive,
                                                           vol=SAW_VOL * f))
        o = r["stages"]["output"]
        out[str(nt)] = {"unwanted_rel_db": o["unwanted_rel_db"], "unwanted_dbfs": o["unwanted_dbfs"],
                        "upper_wanted_rel_db": o["upper_wanted_rel_db"], "intended_dbfs": o["intended_dbfs"],
                        "output_rail": r["clip"]["output_rail_samples"]}
    return out


def r1_binding(drive: float, f: float) -> dict:
    """Would the m5a-saw preset's command bytes change? R1 pins its image sha
    in fpga/release/r1-candidate.json; recompute it with the candidate patch."""
    import release_manifest as rm
    import selected_preset
    pinned = json.loads((ROOT / "fpga/release/r1-candidate.json").read_text())["presets"]["m5a-saw"]["image_sha256"]
    orig = selected_preset.definition

    def definition(name):
        d = orig(name)
        if name == "m5a-saw":
            p = {**d["patch"], "drive": drive, "vol": d["patch"]["vol"] * f}
            d = {**d, "patch": p, "registers": selected_preset.vf.VoiceFx.patch_regs(**p)}
        return d
    now = rm.presets()["m5a-saw"]["image_sha256"]
    selected_preset.definition = definition
    try:
        cand = rm.presets()["m5a-saw"]["image_sha256"]
    finally:
        selected_preset.definition = orig
    return {"pinned": pinned, "current_tree": now, "candidate": cand,
            "tree_matches_pin": now == pinned, "candidate_changes_r1_bytes": cand != pinned}


def select(rows):
    b = rows[str(BASE_DRIVE)]
    be = b["M5A"]["errors"]
    verdicts = {}
    for d in CANDIDATES:
        r = rows.get(str(d))
        if r is None or "refused" in r:
            verdicts[str(d)] = {"admissible": False, "reasons": [f"REFUSED: {r and r.get('refused')}"]}
            continue
        c = r["M5A"]["errors"]
        why = []
        if abs(abs(c["Gain"]) - abs(be["Gain"])) > 0.5:
            why.append(f"gain {c['Gain']:+.3f} vs {be['Gain']:+.3f}")
        if c["Foldback energy"] > 3.0:
            why.append("foldback")
        if c["Clipping"] > 0:
            why.append("clipping")
        if c["Envelope attack"] - be["Envelope attack"] > 0.5:
            why.append(f"attack {c['Envelope attack']} vs {be['Envelope attack']}")
        for nt in DEV:
            if r["probe"][str(nt)]["upper_wanted_rel_db"] < b["probe"][str(nt)]["upper_wanted_rel_db"] - 0.05:
                why.append(f"saw {nt} darker")
        for case in ("M5A", "M5B"):
            pb = [e for e in b[case]["events"] if e["wave"] == "pulse"]
            pc = [e for e in r[case]["events"] if e["wave"] == "pulse"]
            if pb != pc:
                why.append(f"{case} pulse events changed (invariance control)")
        verdicts[str(d)] = {"harmonic_shape": c["Harmonic shape"], "admissible": not why, "reasons": why}
    ok = sorted((v["harmonic_shape"], d) for d, v in verdicts.items() if v["admissible"])
    chosen = ok[0][1] if ok and ok[0][0] < be["Harmonic shape"] else None
    conf = None
    if chosen:
        c = rows[chosen]
        conf = {"M5B": c["M5B"]["errors"], "M5B_baseline": b["M5B"]["errors"],
                "untouched": {nt: {"d_unwanted_rel_db": round(c["probe"][str(nt)]["unwanted_rel_db"]
                                                              - b["probe"][str(nt)]["unwanted_rel_db"], 3),
                                   "d_upper_wanted_rel_db": round(c["probe"][str(nt)]["upper_wanted_rel_db"]
                                                                  - b["probe"][str(nt)]["upper_wanted_rel_db"], 3),
                                   "output_rail": c["probe"][str(nt)]["output_rail"]} for nt in UNTOUCHED}}
        conf["passes"] = (all(v["d_unwanted_rel_db"] <= 1.0 and v["output_rail"] == 0
                              for v in conf["untouched"].values())
                          and conf["M5B"]["Harmonic shape"] <= conf["M5B_baseline"]["Harmonic shape"]
                          and conf["M5B"]["Foldback energy"] <= 3.0 and conf["M5B"]["Clipping"] == 0)
    return {"baseline_harmonic_shape": be["Harmonic shape"], "verdicts": verdicts,
            "chosen": chosen, "confirmation": conf}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--engine", choices=("pulse2x", "r1"), default="pulse2x")
    ap.add_argument("--drives", type=float, nargs="+")
    ap.add_argument("--merge", type=pathlib.Path, nargs="+")
    ap.add_argument("--out", type=pathlib.Path, required=True)
    a = ap.parse_args(argv)
    if a.merge:
        rows = {}
        for p in a.merge:
            rows.update(json.loads(p.read_text())["rows"])
        res = {"engine": a.engine, "rows": rows, "selection": select(rows)}
        print(json.dumps(res["selection"], indent=1))
    else:
        drives = a.drives or (BASE_DRIVE,) + CANDIDATES
        if any(d != BASE_DRIVE and d not in CANDIDATES for d in drives):
            raise SystemExit("REFUSED: outside the frozen three-candidate budget")
        rows = {}
        for d in drives:
            f = 1.0 if d == BASE_DRIVE else ol.calibrate(a.engine, d)
            try:
                rows[str(d)] = {"drive": d, "vol_factor": round(f, 5), "saw_vol": round(SAW_VOL * f, 5),
                                "M5A": phrase(a.engine, "M5A", d, f), "M5B": phrase(a.engine, "M5B", d, f),
                                "probe": probe(a.engine, d, f), "r1_binding": r1_binding(d, f)}
            except score.Refused as e:
                rows[str(d)] = {"drive": d, "vol_factor": round(f, 5), "refused": str(e)}
                print(f"drive {d}: REFUSED {e}", flush=True)
                continue
            print(f"saw drive {d}: vol x{f:.4f} ({SAW_VOL * f:.4f}); M5A {rows[str(d)]['M5A']['errors']}; "
                  f"M5B {rows[str(d)]['M5B']['errors']}; r1 bytes change "
                  f"{rows[str(d)]['r1_binding']['candidate_changes_r1_bytes']}", flush=True)
        res = {"engine": a.engine, "rows": rows}
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", "tools/measure_m5_saw_drive.py",
                            "tools/measure_m5_operating_level.py", "tools/mono_m5a_score.py",
                            "model/voice_fx.py"], cwd=ROOT).returncode != 0
    res.update(commit=head, sources_dirty=dirty)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, indent=1) + "\n")
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
