#!/usr/bin/env python3
"""Lead ladder-drive question on the phase-free Mini V3 references (#337).

Rules are pre-registered in docs/scorecard/mono-m5-drive-337/README.md
(committed before any confirmation number existed).

    .venv/bin/python tools/lead_drive_337.py

Development: M5A.  Confirmation: M5B MIDI 72.  Candidates drive 0.50, 0.25
against baseline 0.75, all on the selected engine through
`mono_m5a_score.measure`.  Exit 2 = REFUSED (a precondition failed).
Nothing is promoted; no engine, patch, scorer or tolerance is changed.
"""
from __future__ import annotations

import hashlib
import json
import math
import pathlib
import subprocess
import sys
import tempfile

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import mono_m5a_score as sc  # noqa: E402

ROOT = sc.ROOT
OUT = ROOT / "docs/scorecard/mono-m5-drive-337"
BASE_DRIVE = 0.75
CANDIDATES = (0.50, 0.25)
WAVES = ("saw", "pulse")
CONFIRM_MIDI = 72
TOL = dict(max_gain=0.5, bright=0.5, level=0.5, alias=0.5, pitch=0.05)
RECORD_TOL = 5e-3
VOL_BASE_KEY = "vol"


class Refused(RuntimeError):
    pass


# ------------------------------------------------------------- pure rules
def summarise(events):
    """events: list of dicts with errors{h:dB}, gain_model, alias, pitch."""
    cells = [v for e in events for v in e["errors"].values() if v is not None]
    if len(cells) < 4 or not all(math.isfinite(v) for v in cells):
        raise Refused("too few or non-finite harmonic cells")
    a = np.asarray(cells, float)
    return {
        "n_cells": len(cells),
        "max_abs": float(np.max(np.abs(a))),
        "rms": float(np.sqrt(np.mean(a ** 2))),
        "mean_signed": float(np.mean(a)),
        "alias_max": float(max(e["alias"] for e in events)),
        "level_mean": float(np.mean([e["gain_model"] for e in events])),
        "pitch_abs": float(max(abs(e["pitch"]) for e in events)),
    }


def compensation_db(base_levels, unc_levels):
    """Pre-registered: mean over events of baseline minus uncompensated."""
    return float(np.mean(np.subtract(base_levels, unc_levels)))


def rule_failures(base, cand, *, reachable=True):
    """Rules 1-7 (selection) == rules 2-7 (confirmation). Returns failed names."""
    fails = []
    if not reachable:
        fails.append("1 unreachable")
    if not (cand["max_abs"] <= base["max_abs"] - TOL["max_gain"]):
        fails.append("2 max error not 0.5 dB lower")
    if not (cand["rms"] <= base["rms"]):
        fails.append("3 rms above baseline")
    if not (cand["mean_signed"] >= min(base["mean_signed"], 0.0) - TOL["bright"]):
        fails.append("4 darker than baseline and reference")
    if not (abs(cand["level_mean"] - base["level_mean"]) <= TOL["level"]):
        fails.append("5 level not held")
    if not (cand["alias_max"] <= base["alias_max"] + TOL["alias"]):
        fails.append("6 alias above baseline")
    if not (cand["pitch_abs"] <= base["pitch_abs"] + TOL["pitch"]):
        fails.append("7 pitch worse")
    return fails


def choose(base, cands):
    """cands: {drive: (summary, reachable)}. Lowest max_abs among eligible."""
    ok = {d: s for d, (s, r) in cands.items()
          if not rule_failures(base, s, reachable=r)}
    return min(ok, key=lambda d: ok[d]["max_abs"]) if ok else None


# --------------------------------------------------------------- renderer
def _events(result, wave, midi=None):
    out = []
    for e in result["event_diagnostics"]:
        if e["wave"] != wave or (midi is not None and e["midi"] != midi):
            continue
        out.append({"midi": e["midi"],
                    "errors": e["harmonic_error_db_model_minus_reference"],
                    "gain_model": e["gain_dbfs"]["model"],
                    "alias": e["foldback_db"]["excess_over_reference_db"],
                    "pitch": e["pitch_cents_from_midi"]["model_minus_reference"]})
    if not out:
        raise Refused(f"no {wave} events (midi={midi}) in render")
    return out


def vol_effective(wave, vol_db, vol_base=0.45):
    """Volume the engine will see for `wave` (saw also gets the engine's own
    fixed correction inside measure)."""
    v = vol_base * 10 ** (vol_db / 20)
    if wave == "saw":
        v *= 10 ** (sc.engine_configuration("selected")["saw_volume_correction_db"] / 20)
    return v


def render(case, drive, vol_db=0.0, vol_wave=None):
    """Selected-engine render with ONLY drive (both waves) and vol (for
    `vol_wave` only) changed in the patch."""
    orig_voice, orig_wave = sc._voice_patch, sc._patch_for_wave

    def patched_voice(manifest):
        return {**orig_voice(manifest), "drive": drive}

    def patched_wave(patch, wave, *a, **k):
        q = orig_wave(patch, wave, *a, **k)
        if wave == vol_wave:
            q = {**q, VOL_BASE_KEY: q[VOL_BASE_KEY] * 10 ** (vol_db / 20)}
        return q
    sc._voice_patch, sc._patch_for_wave = patched_voice, patched_wave
    try:
        with tempfile.TemporaryDirectory() as td:
            wav = pathlib.Path(td) / "x.wav"
            r = sc.measure(case_id=case, output_path=wav)
            r["_sha"] = hashlib.sha256(wav.read_bytes()).hexdigest()
        return r
    except sc.Refused as exc:
        raise Refused(str(exc)) from exc
    finally:
        sc._voice_patch, sc._patch_for_wave = orig_voice, orig_wave


def _load_record(case):
    return json.loads((ROOT / f"docs/scorecard/results/{case}.json").read_text())["metrics"]


def check_record(case, result, load_record=_load_record):
    rec = load_record(case)
    for k in ("Harmonic shape", "Foldback energy", "Gain", "Pitch"):
        # `not (<= tol)`, not `> tol`: NaN must be refused, not pass
        if not abs(result["metrics"][k]["value"] - rec[k]["value"]) <= RECORD_TOL:
            raise Refused(f"baseline {case} {k} {result['metrics'][k]['value']} != record {rec[k]['value']}")


def officials(result):
    return {k: round(v["value"], 4) for k, v in result["metrics"].items()}


def establish_preconditions(base_m5a, base_m5b, probe, load_record=_load_record):
    """Apparatus preconditions; raises Refused (never returns data) on failure.
    Injectable inputs so tests can feed the exact defeating cases."""
    check_record("M5A", base_m5a, load_record)
    check_record("M5B", base_m5b, load_record)
    if probe["_sha"] == base_m5a["_sha"]:
        raise Refused("drive change did not change the audio: apparatus does not apply drive")
    return {"baseline_matches_records": True, "drive_changes_audio": True,
            "baseline_sha": base_m5a["_sha"], "drive0.5_sha": probe["_sha"]}


def main():
    run = {"preconditions": {}, "development": {}, "confirmation": {}}
    base_m5a, base_m5b = render("M5A", BASE_DRIVE), render("M5B", BASE_DRIVE)
    probe = render("M5A", 0.5)
    run["preconditions"] = establish_preconditions(base_m5a, base_m5b, probe)
    run["development"]["baseline"] = {w: summarise(_events(base_m5a, w)) for w in WAVES}
    run["development"]["baseline_official"] = officials(base_m5a)
    run["confirmation"]["baseline"] = {w: summarise(_events(base_m5b, w, CONFIRM_MIDI)) for w in WAVES}
    run["confirmation"]["baseline_official"] = officials(base_m5b)
    run["confirmation"]["midi84_duplicate_note"] = {
        w: [e["gain_model"] for e in _events(base_m5b, w, 84)] for w in WAVES}

    comp, cand_dev, chosen = {}, {}, {}
    for d in CANDIDATES:
        unc = render("M5A", d)
        for w in WAVES:
            delta = compensation_db([e["gain_model"] for e in _events(base_m5a, w)],
                                    [e["gain_model"] for e in _events(unc, w)])
            reach = vol_effective(w, delta) <= 1.0
            comp[(w, d)] = {"delta_db": delta, "vol_effective": vol_effective(w, delta),
                            "reachable": reach}
            if reach:
                r = render("M5A", d, delta, w)
                cand_dev[(w, d)] = (summarise(_events(r, w)), True, officials(r))
    for w in WAVES:
        cands = {d: cand_dev[(w, d)][:2] for d in CANDIDATES if (w, d) in cand_dev}
        base = run["development"]["baseline"][w]
        chosen[w] = choose(base, cands)
        run["development"][w] = {
            "chosen_drive": chosen[w],
            "candidates": {str(d): {"summary": cand_dev[(w, d)][0],
                                    "failures": rule_failures(base, cand_dev[(w, d)][0],
                                                              reachable=cand_dev[(w, d)][1]),
                                    "compensation": comp[(w, d)]}
                           for d in CANDIDATES if (w, d) in cand_dev},
            "unreachable": [d for d in CANDIDATES if (w, d) not in cand_dev]}
    for w in WAVES:
        d = chosen[w]
        if d is None:
            run["confirmation"][w] = {"verdict": "NOT RUN: no eligible candidate"}
            continue
        r = render("M5B", d, comp[(w, d)]["delta_db"], w)
        s = summarise(_events(r, w, CONFIRM_MIDI))
        f = rule_failures(run["confirmation"]["baseline"][w], s)
        run["confirmation"][w] = {"drive": d, "summary": s, "failures": f,
                                  "official": officials(r),
                                  "verdict": "CONFIRMED" if not f else "NOT CONFIRMED"}
    run["source_commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    run["worktree_dirty"] = bool(subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                                                cwd=ROOT, capture_output=True, text=True).stdout.strip())
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(run, indent=1, sort_keys=True, default=float) + "\n")
    print(json.dumps({w: (run["development"][w]["chosen_drive"], run["confirmation"][w]["verdict"])
                      for w in WAVES}))


if __name__ == "__main__":
    try:
        main()
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        sys.exit(2)
