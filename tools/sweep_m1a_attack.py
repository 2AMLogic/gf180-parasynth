#!/usr/bin/env python3
"""Bounded M1A amp-attack sweep of the selected patch, on the committed grid.

    sweep_m1a_attack.py point 4.0       render + score one grid value
    sweep_m1a_attack.py summarize       gate every point, write report.json

Only `patch["amp"][0]` changes. Reference audio, estimator, engine,
tolerances, note sequence and every other patch field are those of
`mono_m1a_score.SELECTED_PATCH`. The grid, the attack-pass rule and the
preservation criteria are read from `attack-sweep/grid.json`, which must be
committed and unmodified: a grid stated after seeing results is not a grid.

The grid's 10 ms point is the selected patch itself and must reproduce the
promoted record's audio hash and property vector, or the summary REFUSES.

Repeated-note context follows PR #195's history contrast: the attack of a
note with history (event 3, MIDI 36 re-struck after 43) minus the same note
without it (event 1, MIDI 36 from silence), model and reference.

Exit 0 done, 2 refused.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from scipy.io import wavfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import mono_m1a_score as bass                                        # noqa: E402
import measure_m1a_volume_mapping as volume                           # noqa: E402

OUT = ROOT / "docs/scorecard/mono-m1a-miniv3/attack-sweep"
GRID = OUT / "grid.json"
RECORD = ROOT / "docs/scorecard/results/M1A.json"
AUDIO = ROOT / "build/m1a-attack-sweep"
LIMIT_MS = 5.0


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def load_grid():
    rel = str(GRID.relative_to(ROOT))
    if git("status", "--porcelain", "--", rel) or not git("ls-files", rel):
        raise bass.Refused("grid.json is not committed and clean; state the grid first")
    grid = json.loads(GRID.read_text())
    if grid["base_patch"] != bass.SELECTED_PATCH:
        raise bass.Refused("grid was stated for a different base patch")
    return grid


def point_name(ms):
    return f"attack-{ms:g}ms".replace(".", "p")


def passes(p):
    return bool(p.get("valid")) and abs(p["error"]) <= p["tolerance"]


def render_point(ms):
    grid = load_grid()
    if ms not in grid["grid_ms"]:
        raise bass.Refused(f"{ms} ms is not on the committed grid")
    manifest, reference = bass.load_reference()
    base = bass.patch_for_reference(manifest, bass.SELECTED_PATCH)
    patch = {**base, "amp": (ms / 1000, *base["amp"][1:])}
    engine = bass.lead.engine_configuration("selected")
    sequence = [(e["on_s"], e["note"], e["gate_s"], {**patch, "gate": e["gate_s"]})
                for e in bass.reference.EVENTS]
    pcm = bass.lead.vf.render_mono_fx(sequence, bass.reference.SECONDS,
                                     bass.lead._voice_for_engine(engine))[:len(reference)]
    AUDIO.mkdir(parents=True, exist_ok=True)
    audio = AUDIO / f"{point_name(ms)}.wav"
    wavfile.write(audio, bass.SR, pcm.astype('<i2'))
    measured = bass.compare_audio(pcm.astype(np.float64) / 32768, reference)
    row = {"attack_ms": ms, "patch_id": f"{bass.SELECTED_PATCH}+amp_attack={ms:g}ms",
           "patch": patch, "engine": engine, "source_commit": git("rev-parse", "HEAD"),
           "source_dirty": bool(git("status", "--porcelain", "--", "tools", "model", "audition")),
           "audio": str(audio.relative_to(ROOT)), "sha256": bass.sha(audio),
           "measurements": measured, "components": volume.counts(measured),
           "metrics": bass.required_metrics(measured)}
    (OUT / f"{point_name(ms)}.json").write_text(json.dumps(row, indent=2) + "\n")
    print(ms, {k: v.get("error") for k, v in measured["properties"].items()}, flush=True)
    return row


# attack_fit's KNOWN-SIGNAL domain (measure_mono_m1a_reference.attack_fit
# docstring): spans 0.5-20 ms, shapes p in {0.5, 1, 2}. The fit also searches
# p = 3, 4 and has a minimum span of 32 samples (coarse 1.0 ms grid, -16
# samples of refinement), so its smallest reportable 10-90% attack is
# 0.667 ms * (0.9**.25 - 0.1**.25) = 0.2745 ms -- a FLOOR, not a measurement.
QUALIFIED_SHAPES = (0.5, 1.0, 2.0)
QUALIFIED_RAMP_MS = (0.5, 20.0)
FIT_FLOOR_RAMP_MS = 32 * 1000 / 48000


def fit_in_domain(fit):
    return (fit["shape_p"] in QUALIFIED_SHAPES
            and QUALIFIED_RAMP_MS[0] <= fit["ramp_ms"] <= QUALIFIED_RAMP_MS[1]
            and fit["ramp_ms"] > FIT_FLOOR_RAMP_MS + 1e-9)


def nominal_vca_10_90_ms(attack_ms):
    """Known answer for the model's AMPLITUDE envelope alone: a linear ramp
    over max(1, int(a_s*48000)) frames, 10-90% = 0.8 of it. The output also
    carries the unchanged filter envelope, so this bounds plausibility; it is
    not the output's exact 10-90%."""
    return 0.8 * max(1, int(attack_ms / 1000 * 48000)) * 1000 / 48000


def evaluate(row, selected):
    """Attack pass, preservation against the selected patch, and context."""
    m, s = row["measurements"], selected["diagnostics"]
    events = m["events"]
    per_event = [{"note": e["note"], "on_s": e["on_s"],
                  "model_ms": e["attack_10_90_ms"]["model"],
                  "reference_ms": e["attack_10_90_ms"]["reference"],
                  "error_ms": e["attack_10_90_ms"]["model"] - e["attack_10_90_ms"]["reference"],
                  "fit_explained": e["attack_fit"]["model"]["explained_ratio"],
                  "model_fit": e["attack_fit"]["model"],
                  "model_fit_in_qualified_domain": fit_in_domain(e["attack_fit"]["model"]),
                  "reference_fit_in_qualified_domain": fit_in_domain(e["attack_fit"]["reference"]),
                  "model_minus_nominal_vca_ms": e["attack_10_90_ms"]["model"]
                                                - nominal_vca_10_90_ms(row["attack_ms"])}
                 for e in events]
    model_in_domain = all(e["model_fit_in_qualified_domain"] for e in per_event)
    lost_props = sorted(n for n, p in s["properties"].items()
                        if passes(p) and not passes(m["properties"][n]))
    lost_partials = sorted(volume.passed_partials(s) - volume.passed_partials(m))
    tol = m["properties"]["Gain"]["tolerance"]
    gains = [e["rms_dbfs"]["model"] - e["rms_dbfs"]["reference"] for e in events]
    first, repeat = per_event[0], per_event[2]
    assert first["note"] == repeat["note"] == 36
    attack_pass = all(abs(e["error_ms"]) <= LIMIT_MS for e in per_event)
    preserved = not lost_props and not lost_partials and all(abs(g) <= tol for g in gains)
    return {"attack_ms": row["attack_ms"], "patch_id": row["patch_id"], "sha256": row["sha256"],
            "per_event": per_event,
            "attack_property_error_ms": m["properties"]["Envelope attack"]["error"],
            "attack_pass": attack_pass,
            "history_effect_ms": {"with_history": "event 3: MIDI 36 after 43",
                                  "without_history": "event 1: MIDI 36 from silence",
                                  "model": repeat["model_ms"] - first["model_ms"],
                                  "reference": repeat["reference_ms"] - first["reference_ms"]},
            "properties": {n: {"error": p.get("error"), "state":
                               "unqualified" if not p.get("valid") else
                               "pass" if passes(p) else "fail"}
                           for n, p in m["properties"].items()},
            "lost_property_passes": lost_props,
            "lost_per_note_harmonic_passes": [list(x) for x in lost_partials],
            "gain_errors_db": gains, "preserved": preserved,
            "components": row["components"],
            "model_fits_in_qualified_domain": model_in_domain,
            "nominal_vca_10_90_ms": nominal_vca_10_90_ms(row["attack_ms"]),
            # a pass read from out-of-domain model fits is REFUSED, not a pass
            "candidate": attack_pass and preserved and model_in_domain,
            "refused_reason": (None if model_in_domain else
                               "model attack fit outside the estimator's qualified domain "
                               "(shape p in {0.5,1,2}, span 0.5-20 ms, above the 0.667 ms fit floor)")}


def summarize():
    grid = load_grid()
    selected = json.loads(RECORD.read_text())
    if selected["diagnostics"]["configuration"].get("patch_id") != bass.SELECTED_PATCH:
        raise bass.Refused("board record is not the selected patch")
    rows = {}
    for ms in grid["grid_ms"]:
        path = OUT / f"{point_name(ms)}.json"
        if not path.exists():
            raise bass.Refused(f"grid point {ms} ms was not evaluated")
        rows[ms] = json.loads(path.read_text())
        if rows[ms]["source_dirty"]:
            raise bass.Refused(f"grid point {ms} ms was rendered from a dirty tree")
    control = rows[grid["control"]["value_ms"]]
    if (control["sha256"] != selected["diagnostics"]["model_audio_sha256"]
            or json.loads(json.dumps(control["measurements"]["properties"]))
            != selected["diagnostics"]["properties"]):
        raise bass.Refused("control point does not reproduce the selected patch's record")
    table = [evaluate(rows[ms], selected) for ms in grid["grid_ms"]]
    candidates = [r for r in table if r["candidate"]]
    report = {"schema": "m1a-attack-sweep-v1", "grid_sha256": bass.sha(GRID),
              "grid_commit": git("log", "-1", "--format=%H", "--", str(GRID.relative_to(ROOT))),
              "source_commit": git("rev-parse", "HEAD"), "base_patch": bass.SELECTED_PATCH,
              "reference_sha256": bass.sha(bass.MANIFEST), "evidence_level": "model only",
              "control_reproduced": {"value_ms": grid["control"]["value_ms"],
                                     "sha256": control["sha256"]},
              "table": table,
              "conclusion": ("candidate" if candidates else "sensitivity"),
              "candidates": [r["patch_id"] for r in candidates],
              "scope": "patch-only; no envelope implementation change; harmonic shape "
                       "remains an independent failure, so no point is a whole-case pass"}
    (OUT / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    report["reference_fits_in_qualified_domain"] = [
        e["reference_fit_in_qualified_domain"] for e in table[0]["per_event"]]
    (OUT / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"{'attack':>7} {'ev1':>7} {'ev2':>7} {'ev3':>7} {'worst':>7} {'hist m/r':>12}  attack preserved domain lost")
    for r in table:
        e = [x["error_ms"] for x in r["per_event"]]
        h = r["history_effect_ms"]
        print(f"{r['attack_ms']:>7g} {e[0]:>+7.2f} {e[1]:>+7.2f} {e[2]:>+7.2f} "
              f"{r['attack_property_error_ms']:>+7.2f} {h['model']:>+6.2f}/{h['reference']:+.2f}  "
              f"{'PASS' if r['attack_pass'] else 'fail':<6} {'yes' if r['preserved'] else 'NO':<9} "
              f"{'in' if r['model_fits_in_qualified_domain'] else 'OUT':<6} "
              f"{r['lost_property_passes'] + r['lost_per_note_harmonic_passes']}")
    print("conclusion:", report["conclusion"], report["candidates"])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("point").add_argument("ms", type=float)
    sub.add_parser("summarize")
    a = ap.parse_args(argv)
    try:
        render_point(a.ms) if a.cmd == "point" else summarize()
        return 0
    except bass.Refused as exc:
        print(f"REFUSED: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
