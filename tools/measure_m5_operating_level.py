#!/usr/bin/env python3
"""M5 harmonic-shape candidate: lower ladder drive, output volume restoring level (#333).

Localization (docs/scorecard/m5-artifacts-333) put the M5A/M5B harmonic-shape
failure in the ladder's tanh stages at drive 0.75: at drive 0.05 the ladder
matches its analytic 4-pole response within 0.2 dB, at 0.75 it loses 2-7 dB of
every upper partial. Drive is also level (gain = drive * vpu / 2Vt; ogain does
not depend on drive), so a lower drive needs the output `vol` register raised.
Both are preset registers: no RTL change is involved.

Candidate budget: three drives, fixed here before any is rendered.
The volume factor for each is calibrated ONCE, on a development condition only
(held saw, MIDI 84, M5 patch): the factor that returns that held note's output
RMS to its drive-0.75 value. The same factor is used for pulse segments.

Selection rule (frozen): on M5A only (MIDI 84/96), the candidate with the
lowest Harmonic shape error such that
  * |Gain error| stays within 0.5 dB of the baseline's,
  * Foldback <= 3 dB, Clipping 0, Envelope attack error not worse by > 0.5 ms,
  * no event's upper wanted power (probe) falls below the baseline's.
Confirmation: M5B (MIDI 72 is untouched by selection) and probe points at
untouched notes, plus the F1 cases, which are measured separately.

Engine: pulse2x with rectangles at 0.74 (the next image's configuration, see
pulse2x-headroom/), and R1 for reference. No result is R1-image sound unless
the engine says r1.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import math
import pathlib
import subprocess
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "model"), str(ROOT / "tools")]
import voice_fx as vf  # noqa: E402
import mono_m5a_score as score  # noqa: E402
import mono_artifact_probe as mp  # noqa: E402
import measure_pulse2x_headroom as hr  # noqa: E402

BASE_DRIVE = 0.75
CANDIDATE_DRIVES = (0.5, 0.35, 0.25)          # the whole budget
RECT_GAIN = hr.q15(0.74)
CAL_NOTE = 84
PROBE_NOTES = (36, 48, 60, 72, 84, 96, 108, 120)


def voice(engine: str) -> vf.VoiceFx:
    return mp.make_voice(engine)


@contextlib.contextmanager
def engine_ctx(engine: str):
    if engine == "pulse2x":
        with hr.candidate(RECT_GAIN, "rect"):
            yield
    elif engine == "r1":
        yield
    else:
        raise ValueError(engine)


@contextlib.contextmanager
def patched_patch(drive: float, vol_factor: float):
    """Replace the scorer's M5 patch drive and scale its volume."""
    orig = score._voice_patch

    def patch(manifest):
        p = orig(manifest)
        return {**p, "drive": drive, "vol": p["vol"] * vol_factor}
    score._voice_patch = patch
    try:
        yield
    finally:
        score._voice_patch = orig


def held_rms(engine: str, drive: float, vol: float, note=CAL_NOTE, wave="saw") -> float:
    with engine_ctx(engine):
        r = mp.render_held(engine, note, mp.held_patch(waves=(wave,) * 3, drive=drive, vol=vol))
    x = r["stages"]["output"][0]
    return float(np.sqrt(np.mean(x * x)))


def calibrate(engine: str, drive: float) -> float:
    """Volume factor restoring the development note's held-saw RMS. vol is a
    16-bit Q0.15 register (max ~2.0); a factor that would overflow it refuses."""
    vol0 = 0.45 * 10 ** (-0.45428 / 20)           # the m5a-saw preset's volume
    target = held_rms(engine, BASE_DRIVE, vol0)
    f = target / held_rms(engine, drive, vol0)
    f *= target / held_rms(engine, drive, vol0 * f)   # one refinement for the tanh
    if not 0 < vol0 * f < 65535 / 32768:
        raise RuntimeError(f"drive {drive}: vol {vol0 * f:.3f} does not fit the register")
    return f


def phrase(engine: str, case: str, drive: float, vol_factor: float) -> dict:
    with engine_ctx(engine), patched_patch(drive, vol_factor):
        m = score.measure(case_id=case, voice_factory=lambda: voice(engine),
                          model_label=f"operating-level d{drive}",
                          output_path=ROOT / f"build/oplevel/{engine}-{case}-d{drive}.wav")
    return {"errors": {k: v["error"] for k, v in m["metrics"].items()},
            "events": [{"wave": e["wave"], "midi": e["midi"],
                        "harmonic_error_db": e["harmonic_error_db_model_minus_reference"],
                        "foldback_excess_db": e["foldback_db"]["excess_over_reference_db"],
                        "gain_dbfs": e["gain_dbfs"]}
                       for e in m["event_diagnostics"]]}


def probe(engine: str, drive: float, vol_factor: float) -> dict:
    vol = 0.45 * vol_factor
    out = {}
    for wave in ("saw", "pulse29"):
        for nt in PROBE_NOTES:
            with engine_ctx(engine):
                r = mp.measure_point(engine, nt, mp.held_patch(waves=(wave,) * 3, drive=drive, vol=vol))
            o = r["stages"]["output"]
            out[f"{wave}/{nt}"] = {"unwanted_dbfs": o["unwanted_dbfs"], "unwanted_rel_db": o["unwanted_rel_db"],
                                   "upper_wanted_rel_db": o["upper_wanted_rel_db"],
                                   "intended_dbfs": o["intended_dbfs"],
                                   "output_rail": r["clip"]["output_rail_samples"],
                                   "osc_rail": r["clip"]["oscillator_rail_samples"]}
    return out


def select(rows: dict) -> dict:
    base = rows[str(BASE_DRIVE)]["M5A"]["errors"]
    verdicts = {}
    for d in CANDIDATE_DRIVES:
        if "refused" in rows[str(d)]:
            verdicts[str(d)] = {"harmonic_shape": None, "admissible": False,
                                "reasons": [f"REFUSED: {rows[str(d)]['refused']}"]}
            continue
        c = rows[str(d)]["M5A"]["errors"]
        why = []
        if abs(abs(c["Gain"]) - abs(base["Gain"])) > 0.5:
            why.append(f"gain {c['Gain']:+.2f} vs {base['Gain']:+.2f}")
        if c["Foldback energy"] > 3.0:
            why.append(f"foldback {c['Foldback energy']:.2f}")
        if c["Clipping"] > 0:
            why.append("clipping")
        if c["Envelope attack"] - base["Envelope attack"] > 0.5:
            why.append(f"attack {c['Envelope attack']:.2f} vs {base['Envelope attack']:.2f}")
        bp, cp = rows[str(BASE_DRIVE)]["probe"], rows[str(d)]["probe"]
        darker = [k for k in ("saw/84", "saw/96", "pulse29/84", "pulse29/96")
                  if (cp[k]["upper_wanted_rel_db"] or 0) < (bp[k]["upper_wanted_rel_db"] or 0) - 0.05]
        if darker:
            why.append(f"darker at {darker}")
        verdicts[str(d)] = {"harmonic_shape": c["Harmonic shape"], "admissible": not why, "reasons": why}
    ok = [(v["harmonic_shape"], d) for d, v in verdicts.items() if v["admissible"]]
    chosen = min(ok)[1] if ok else None
    if chosen is not None and verdicts[chosen]["harmonic_shape"] >= base["Harmonic shape"]:
        chosen = None                                   # no improvement is not a winner
    return {"rule": __doc__.split("Selection rule (frozen):")[1].split("Confirmation:")[0].strip(),
            "baseline_harmonic_shape": base["Harmonic shape"], "verdicts": verdicts, "chosen": chosen}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--engine", choices=("pulse2x", "r1"), default="pulse2x")
    ap.add_argument("--drives", type=float, nargs="+", default=None,
                    help="subset of baseline + candidates (for splitting across jobs)")
    ap.add_argument("--merge", type=pathlib.Path, nargs="+", default=None,
                    help="merge per-drive records and apply the selection rule")
    ap.add_argument("--out", type=pathlib.Path, required=True)
    a = ap.parse_args(argv)
    if a.merge:
        rows = {}
        for pth in a.merge:
            rows.update(json.loads(pth.read_text())["rows"])
        res = {"engine": a.engine, "rows": rows, "selection": select(rows)}
        print(json.dumps(res["selection"], indent=1))
    else:
        drives = a.drives or (BASE_DRIVE,) + CANDIDATE_DRIVES
        bad = [d for d in drives if d != BASE_DRIVE and d not in CANDIDATE_DRIVES]
        if bad:
            raise SystemExit(f"REFUSED: {bad} outside the frozen three-candidate budget")
        rows = {}
        for d in drives:
            f = 1.0 if d == BASE_DRIVE else calibrate(a.engine, d)
            try:
                m5a_row = phrase(a.engine, "M5A", d, f)
            except score.Refused as e:          # e.g. vol outside the host's 0..1 range
                rows[str(d)] = {"drive": d, "vol_factor": round(f, 5), "refused": str(e)}
                print(f"drive {d}: vol x{f:.4f}; REFUSED: {e}", flush=True)
                continue
            rows[str(d)] = {"drive": d, "vol_factor": round(f, 5),
                            "M5A": m5a_row, "M5B": phrase(a.engine, "M5B", d, f),
                            "probe": probe(a.engine, d, f)}
            print(f"drive {d}: vol x{f:.4f}; M5A {rows[str(d)]['M5A']['errors']}; "
                  f"M5B {rows[str(d)]['M5B']['errors']}", flush=True)
        res = {"engine": a.engine, "rows": rows}
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", "tools/measure_m5_operating_level.py",
                            "tools/mono_m5a_score.py", "model/voice_fx.py"], cwd=ROOT).returncode != 0
    res.update(commit=head, sources_dirty=dirty)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, indent=1) + "\n")
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
