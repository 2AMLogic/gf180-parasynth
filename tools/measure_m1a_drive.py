#!/usr/bin/env python3
"""One bounded M1A filter-drive experiment; the rules are pre-declared in
docs/scorecard/mono-m1a-miniv3/drive-experiment/README.md.

    .venv/bin/python tools/measure_m1a_drive.py

Candidates: drive 0.75 (the selected patch), 0.50, 0.25. Output volume is
compensated IN THE PATCH by the pre-declared rule (one uncompensated render of
the phrase; mean Gain-window RMS difference from baseline). Each candidate is
measured (1) on the #219 moving-phase condition, with psi from its own
isolated open-filter renders, and (2) on the complete original M1A phrase
through the scorer's `compare_audio`, exactly as `mono_m1a_score.run` renders
it. Nothing is promoted; the patch, scorer and rubric are unchanged.
"""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
import sys

import numpy as np
from scipy.io import wavfile

import analyse_m1a_phase_cycle as ap
import mono_m1a_score as bass

ROOT, SR, lead = bass.ROOT, bass.SR, bass.lead
OUT = ROOT / "docs/scorecard/mono-m1a-miniv3/drive-experiment"
RECORD = ROOT / "docs/scorecard/results/M1A.json"
PHASE_REPORT = ROOT / "docs/scorecard/mono-m1a-miniv3/phase-cycle-capture/report.json"
BASELINE_DRIVE = .75
DRIVES = (.75, .50, .25)
GRADED = ("Pitch", "Envelope release", "Gain", "Clipping")
HARMONIC_TOL = 1.0
MIN_HARMONIC_GAIN_DB = .5
ODD = ap.ODD


class Refused(bass.Refused):
    pass


# ------------------------------------------------------------- pure rules
def compensation_db(baseline_rms, uncompensated_rms):
    """Pre-declared: mean over events of baseline minus uncompensated
    Gain-window RMS (dBFS)."""
    return float(np.mean(np.subtract(baseline_rms, uncompensated_rms)))


def passes(prop):
    return bool(prop.get("valid")) and abs(prop["error"]) <= prop["tolerance"]


def cells(measured):
    """{(event_index, 'hk'): signed model-minus-reference error dB}."""
    return {(i, h): err for i, e in enumerate(measured["events"])
            for h, err in e["harmonic_error_db_model_minus_reference"].items() if err is not None}


def regressions(baseline, candidate):
    """Every preservation-policy violation, as readable strings."""
    out = []
    for name, prop in baseline["properties"].items():
        cand = candidate["properties"][name]
        if bool(prop.get("valid")) != bool(cand.get("valid")):
            out.append(f"{name}: validity {prop.get('valid')} -> {cand.get('valid')}")
        elif name in GRADED and passes(prop) and not passes(cand):
            out.append(f"{name}: pass -> fail ({prop['error']:+.3f} -> {cand['error']:+.3f})")
    base_cells, cand_cells = cells(baseline), cells(candidate)
    for key, err in base_cells.items():
        new = cand_cells.get(key)
        if abs(err) <= HARMONIC_TOL and (new is None or abs(new) > HARMONIC_TOL):
            out.append(f"MIDI {baseline['events'][key[0]]['note']} @{baseline['events'][key[0]]['on_s']:g}s "
                       f"{key[1]}: pass -> fail ({err:+.2f} -> {new if new is None else f'{new:+.2f}'} dB)")
    return out


def choose(rows, baseline_row):
    """Pre-declared choice rule. rows: {drive: row}; each row has
    'regressions', 'harmonic_shape_db', 'odd_range_mean_db'."""
    eligible = {}
    for drive, row in rows.items():
        if drive == BASELINE_DRIVE:
            continue
        why = []
        if row["regressions"]:
            why.append(f"{len(row['regressions'])} regressions")
        if baseline_row["harmonic_shape_db"] - row["harmonic_shape_db"] < MIN_HARMONIC_GAIN_DB:
            why.append(f"harmonic shape {row['harmonic_shape_db']:.3f} dB is not >= {MIN_HARMONIC_GAIN_DB} dB "
                       f"below baseline {baseline_row['harmonic_shape_db']:.3f}")
        if row["odd_range_mean_db"] >= baseline_row["odd_range_mean_db"]:
            why.append("moving-phase odd range not reduced")
        row["ineligible_because"] = why
        if not why:
            eligible[drive] = baseline_row["harmonic_shape_db"] - row["harmonic_shape_db"]
    if not eligible:
        return None
    return max(eligible, key=eligible.get)


# ------------------------------------------------------------- rendering
def phrase_patch(manifest, drive, vol=None):
    patch = bass.patch_for_reference(manifest)
    return {**patch, "drive": drive, "vol": patch["vol"] if vol is None else vol}


def render_phrase(patch, n):
    seq = [(e["on_s"], e["note"], e["gate_s"], {**patch, "gate": e["gate_s"]}) for e in bass.reference.EVENTS]
    voice = lead._voice_for_engine(lead.engine_configuration("selected"))
    return lead.vf.render_mono_fx(seq, bass.reference.SECONDS, voice)[:n]


def pcm_sha(pcm):
    return hashlib.sha256(np.asarray(pcm).astype("<i2").tobytes()).hexdigest()


def clipping(pcm):
    pcm = np.asarray(pcm)
    return {"peak_dbfs": 20 * math.log10(max(int(np.max(np.abs(pcm.astype(np.int32)))), 1) / 32768),
            "rail_fraction_pct": 100 * float(np.count_nonzero(np.abs(pcm.astype(np.int32)) >= 32767)) / len(pcm)}


def moving_phase(patch, cents):
    """The #219 model condition with this candidate's drive and vol."""
    detune = list(patch["detune"])
    detune[1] = 12. + cents / 100
    p = {**patch, "detune": tuple(detune)}
    events = ({"note": ap.NOTE, "on_s": ap.MODEL_ON_S, "gate_s": ap.MODEL_GATE_S},)
    open_p = {**p, "cutoff": (20000, 20000)}
    full = ap.render_events(p, events, ap.MODEL_SECONDS)
    o1 = ap.render_events(open_p, events, ap.MODEL_SECONDS, (1., 0., 0.))
    o2 = ap.render_events(open_p, events, ap.MODEL_SECONDS, (0., p["mix"][1], 0.))
    m = ap.measure(*(x.astype(np.float64) / 32768 for x in (full, o1, o2)), ap.MODEL_ON_S, ap.MODEL_GATE_S)
    s = ap.summarise(m["windows"])
    h1 = s["partials"]["h1"]["mean_dbfs"]
    return {"drift_hz": m["drift_hz"], "refused_windows": m["refused_windows"],
            "clipping": clipping(full), "pcm_sha256": pcm_sha(full),
            "partials": {h: {"range_db": v["range_db"], "dark_db": v["dark_db"], "mean_dbfs": v["mean_dbfs"],
                             "ratio_to_h1_db": v["mean_dbfs"] - h1, "binned_dbfs": v["binned_dbfs"],
                             "darkest_bin_deg": v["darkest_bin_deg"]}
                         for h, v in s["partials"].items()},
            "odd_range_mean_db": float(np.mean([s["partials"][h]["range_db"] for h in ODD]))}


def main():
    try:
        manifest, reference = bass.load_reference()
        record = json.loads(RECORD.read_text())
        phase = json.loads(PHASE_REPORT.read_text())
        cents = float(np.mean([v["octave_offset_cents"] for v in phase["reference"].values()]))
        n = len(reference)
        rows, measured_by = {}, {}

        base_patch = phrase_patch(manifest, BASELINE_DRIVE)
        base_pcm = render_phrase(base_patch, n)
        rate, committed = wavfile.read(bass.MANIFEST.parent / "m1a-model.wav")
        if pcm_sha(base_pcm) != pcm_sha(committed):
            raise Refused("baseline render does not reproduce the committed m1a-model.wav")
        base = bass.compare_audio(base_pcm.astype(np.float64) / 32768, reference)
        for name, prop in record["diagnostics"]["properties"].items():
            if prop.get("valid") and abs(base["properties"][name]["value"] - prop["value"]) > 1e-6:
                raise Refused(f"baseline {name} does not reproduce results/M1A.json")
        base_rms = [e["rms_dbfs"]["model"] for e in base["events"]]

        for drive in DRIVES:
            if drive == BASELINE_DRIVE:
                vol, delta, unc = base_patch["vol"], 0., None
                pcm, measured = base_pcm, base
            else:
                unc_pcm = render_phrase(phrase_patch(manifest, drive), n)
                unc = bass.compare_audio(unc_pcm.astype(np.float64) / 32768, reference)
                delta = compensation_db(base_rms, [e["rms_dbfs"]["model"] for e in unc["events"]])
                vol = base_patch["vol"] * 10 ** (delta / 20)
                pcm = render_phrase(phrase_patch(manifest, drive, vol), n)
                measured = bass.compare_audio(pcm.astype(np.float64) / 32768, reference)
            patch = phrase_patch(manifest, drive, vol)
            measured_by[drive] = measured
            mp = moving_phase(patch, cents)
            if drive == BASELINE_DRIVE:
                # the #219 model condition must reproduce exactly
                for h in ODD:
                    if abs(mp["partials"][h]["range_db"] - phase["summaries"]["model"]["partials"][h]["range_db"]) > 1e-9:
                        raise Refused("baseline moving-phase render does not reproduce #219's model curves")
            props = measured["properties"]
            rows[drive] = {
                "drive": drive, "vol": vol, "vol_register_q15": int(round(vol * 32768)),
                "compensation_db": delta, "analytic_small_signal_db": 20 * math.log10(BASELINE_DRIVE / drive),
                "uncompensated_gain_error_db": None if unc is None else unc["properties"]["Gain"]["error"],
                "ladder_gain_register": lead.vf.LadderFx(**lead.vf.LADDER_CFG).regs(patch["q"], drive)[1],
                "phrase_pcm_sha256": pcm_sha(pcm), "phrase_clipping": clipping(pcm),
                "harmonic_shape_db": props["Harmonic shape"]["value"],
                "properties": {k: {kk: v.get(kk) for kk in ("valid", "value", "reference", "error", "tolerance")}
                               for k, v in props.items()},
                "property_passes": {k: passes(v) for k, v in props.items() if v.get("valid")},
                "event_rms_dbfs": [e["rms_dbfs"]["model"] for e in measured["events"]],
                "harmonic_errors": [{"note": e["note"], "on_s": e["on_s"],
                                     "error_db": e["harmonic_error_db_model_minus_reference"]}
                                    for e in measured["events"]],
                "harmonic_change_vs_baseline_db": [
                    {h: (e["harmonic_error_db_model_minus_reference"][h] - b["harmonic_error_db_model_minus_reference"][h])
                     for h in e["harmonic_error_db_model_minus_reference"]}
                    for e, b in zip(measured["events"], base["events"])],
                "harmonic_cells_passing": sum(abs(v) <= HARMONIC_TOL for v in cells(measured).values()),
                "regressions": regressions(base, measured),
                "moving_phase": mp, "odd_range_mean_db": mp["odd_range_mean_db"]}
            print(f"drive {drive}: vol {vol:.4f} (comp {delta:+.2f} dB), harmonic {rows[drive]['harmonic_shape_db']:.3f}, "
                  f"odd range {mp['odd_range_mean_db']:.2f}, regressions {len(rows[drive]['regressions'])}", flush=True)
        choice = choose(rows, rows[BASELINE_DRIVE])
        ref_s = phase["summaries"]["pooled"]["partials"]
        report = {
            "schema": "m1a-drive-experiment-v1",
            "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "source_sha256": {f: bass.sha(ROOT / f) for f in ("tools/measure_m1a_drive.py",
                              "tools/analyse_m1a_phase_cycle.py", "tools/mono_m1a_score.py",
                              "model/voice_fx.py", "model/fixed.py")},
            "baseline_reproduces_record": True, "baseline_reproduces_phase_cycle_model": True,
            "data_status": "#219 captures used as DEVELOPMENT data (already inspected); not unseen confirmation",
            "reference_moving_phase": {h: {"range_db": ref_s[h]["range_db"], "dark_db": ref_s[h]["dark_db"],
                                           "mean_dbfs": ref_s[h]["mean_dbfs"],
                                           "ratio_to_h1_db": ref_s[h]["mean_dbfs"] - ref_s["h1"]["mean_dbfs"]}
                                       for h in ref_s},
            "candidates": {str(k): v for k, v in rows.items()},
            "choice": None if choice is None else str(choice),
        }
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "report.json").write_text(json.dumps(report, indent=1) + "\n")
        print(json.dumps({"choice": report["choice"],
                          "ineligible": {k: v.get("ineligible_because") for k, v in rows.items()}}, indent=1))
        return 0
    except Refused as exc:
        print(f"REFUSED: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
