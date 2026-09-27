#!/usr/bin/env python3
"""#347: the M5 pulse segments' upper-partial deficit -- a bounded pulse-cutoff sweep.

After the saw-only drive change both M5 cases are limited by the PULSE
segments, 4.9-5.3 dB short of Mini V3 in their upper partials. They run at
the 14,073 Hz cutoff the frozen reference's self-oscillation calibration
gave; the saw was moved to 20 kHz by an earlier accepted intervention. One
mechanism is changed here: the pulse segments' cutoff. Nothing else moves.

Engine: the next image's -- pulse2x with rectangles at 0.74 (both inside the
2x chain) -- and the saw at its preset drive 0.75 (the saw-drive candidate
is a separate change and is NOT mixed in, so the pulse objective cannot be
moved by it and vice versa).

Candidates, fixed before any render: pulse cutoff 14,073 (baseline), 17,000,
20,000 Hz. All are register values a preset can load (CUT_MAX 21,600).

Objective (pulse-specific, not the phrase maximum, which a saw event can
set): the worst |partial error| over the M5A pulse events (MIDI 84, 96).
Selection: the lowest objective such that, on M5A,
  * pulse foldback excess <= 3 dB at both pulse events,
  * |Gain error| changes by at most 0.5 dB from the baseline (a permitted
    change, not a claim that the absolute gain error is under 0.5 dB),
  * Clipping 0, Pitch within its limit, pulse attack error not worse by
    > 0.5 ms (attack already fails its absolute tolerance and stays failing),
  * every saw event identical to the baseline (invariance control).
Tie-break, fixed now: within 0.1 dB of objective, the LOWER cutoff wins
(the smaller change). A candidate no better than the baseline is not a winner.
Confirmation: M5B's pulse events (MIDI 72 is untouched by selection) under the
same rule, and held pulse notes 60, 108 through the artifact probe: relative
unwanted energy not worse by > 1 dB, no output rail.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "model"), str(ROOT / "tools")]
import mono_m5a_score as score  # noqa: E402
import mono_artifact_probe as mp  # noqa: E402
import measure_m5_operating_level as ol  # noqa: E402

BASE_CUT = 14073
CANDIDATES = (17000, 20000)
PROBE_NOTES = (60, 108)


@contextlib.contextmanager
def pulse_cutoff(cut: int):
    orig = score._patch_for_wave

    def pfw(patch, wave, pulse_shape=score.M5A_PULSE_WAVE):
        p = orig(patch, wave, pulse_shape)
        if wave == "pulse":
            p = {**p, "cutoff": (cut, cut)}
        return p
    score._patch_for_wave = pfw
    try:
        yield
    finally:
        score._patch_for_wave = orig


def phrase(case, cut):
    with ol.engine_ctx("pulse2x"), pulse_cutoff(cut):
        m = score.measure(case_id=case, voice_factory=lambda: ol.voice("pulse2x"),
                          model_label=f"pulse cutoff {cut}",
                          output_path=ROOT / f"build/pulsecut/{case}-{cut}.wav")
    ev = [{"wave": e["wave"], "midi": e["midi"],
           "harmonic_error_db": e["harmonic_error_db_model_minus_reference"],
           "foldback_excess_db": e["foldback_db"]["excess_over_reference_db"],
           "gain_dbfs": e["gain_dbfs"], "envelope_ms": e["envelope_ms"],
           "pitch_cents": e["pitch_cents_from_midi"]["model_minus_reference"]}
          for e in m["event_diagnostics"]]
    pulse = [e for e in ev if e["wave"] == "pulse"]
    return {"errors": {k: v["error"] for k, v in m["metrics"].items()}, "events": ev,
            "pulse_objective_db": round(max(max(abs(x) for x in e["harmonic_error_db"].values())
                                            for e in pulse), 4)}


def probe(cut):
    out = {}
    for nt in PROBE_NOTES:
        with ol.engine_ctx("pulse2x"):
            r = mp.measure_point("pulse2x", nt, mp.held_patch(waves=("pulse29",) * 3, cutoff=(cut, cut)))
        o = r["stages"]["output"]
        out[str(nt)] = {"unwanted_rel_db": o["unwanted_rel_db"], "upper_wanted_rel_db": o["upper_wanted_rel_db"],
                        "output_rail": r["clip"]["output_rail_samples"]}
    return out


def verdict(b, c, case):
    why = []
    for eb, ec in zip(b[case]["events"], c[case]["events"]):
        if eb["wave"] == "saw":
            if eb != ec:
                why.append(f"{case} saw {eb['midi']} changed (invariance control)")
            continue
        if ec["foldback_excess_db"] > 3.0:
            why.append(f"{case} pulse {ec['midi']} foldback {ec['foldback_excess_db']}")
        da = abs(ec["envelope_ms"]["attack_model"] - ec["envelope_ms"]["attack_reference"]) - \
            abs(eb["envelope_ms"]["attack_model"] - eb["envelope_ms"]["attack_reference"])
        if da > 0.5:
            why.append(f"{case} pulse {ec['midi']} attack worse by {da:.2f} ms")
    be, ce = b[case]["errors"], c[case]["errors"]
    if abs(abs(ce["Gain"]) - abs(be["Gain"])) > 0.5:
        why.append(f"{case} gain {be['Gain']} -> {ce['Gain']}")
    if ce["Clipping"] > 0:
        why.append(f"{case} clipping")
    if abs(ce["Pitch"]) > 1.0:
        why.append(f"{case} pitch")
    return why


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=pathlib.Path, required=True)
    a = ap.parse_args(argv)
    rows = {}
    for cut in (BASE_CUT,) + CANDIDATES:
        rows[cut] = {"M5A": phrase("M5A", cut), "M5B": phrase("M5B", cut), "probe": probe(cut)}
        r = rows[cut]
        print(f"pulse cutoff {cut}: M5A pulse objective {r['M5A']['pulse_objective_db']} "
              f"(case {r['M5A']['errors']['Harmonic shape']}), M5B pulse objective "
              f"{r['M5B']['pulse_objective_db']} (case {r['M5B']['errors']['Harmonic shape']}); "
              f"foldback {r['M5A']['errors']['Foldback energy']}/{r['M5B']['errors']['Foldback energy']}; "
              f"gain {r['M5A']['errors']['Gain']}/{r['M5B']['errors']['Gain']}", flush=True)
    b = rows[BASE_CUT]
    sel = {}
    for cut in CANDIDATES:
        why = verdict(b, rows[cut], "M5A")
        sel[cut] = {"objective": rows[cut]["M5A"]["pulse_objective_db"], "admissible": not why, "reasons": why}
    ok = [(v["objective"], cut) for cut, v in sel.items() if v["admissible"]
          and v["objective"] < b["M5A"]["pulse_objective_db"]]
    chosen = None
    if ok:
        best = min(o for o, _ in ok)
        chosen = min(cut for o, cut in ok if o <= best + 0.1)          # tie-break: the lower cutoff
    conf = None
    if chosen:
        c = rows[chosen]
        why = verdict(b, c, "M5B")
        for nt in PROBE_NOTES:
            d = c["probe"][str(nt)]["unwanted_rel_db"] - b["probe"][str(nt)]["unwanted_rel_db"]
            if d > 1.0:
                why.append(f"held pulse {nt} unwanted +{d:.2f} dB")
            if c["probe"][str(nt)]["output_rail"]:
                why.append(f"held pulse {nt} rail")
        improved = c["M5B"]["pulse_objective_db"] < b["M5B"]["pulse_objective_db"]
        conf = {"reasons": why, "m5b_pulse_objective": [b["M5B"]["pulse_objective_db"], c["M5B"]["pulse_objective_db"]],
                "passes": not why and improved}
    res = {"rows": {str(k): v for k, v in rows.items()}, "selection": {str(k): v for k, v in sel.items()},
           "baseline_objective": b["M5A"]["pulse_objective_db"], "chosen": chosen, "confirmation": conf,
           "commit": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip(),
           "sources_dirty": subprocess.run(["git", "diff", "--quiet", "HEAD", "--", "tools/measure_m5_pulse_cutoff.py",
                                            "tools/mono_m5a_score.py", "model/voice_fx.py"], cwd=ROOT).returncode != 0}
    print(json.dumps({k: res[k] for k in ("selection", "baseline_objective", "chosen", "confirmation")}, indent=1))
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
