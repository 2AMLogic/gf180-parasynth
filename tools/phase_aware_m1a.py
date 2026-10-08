#!/usr/bin/env python3
"""M1A as a phase-aware question (#337). The rules were declared, and committed,
before this ran: docs/scorecard/mono-m1a-miniv3/phase-aware-337/README.md.

    .venv/bin/python tools/phase_aware_m1a.py            # development, selection, confirmation
    .venv/bin/python tools/phase_aware_m1a.py --control  # known-answer control only (no render)

ONE mechanism, the ladder input drive. Baseline 0.75 is the selected patch, and
the candidates are {0.50, 0.25}, the grid ../drive-experiment declared. Each one
is judged where the relative oscillator phase psi is EQUAL on both sides:

* development: over the whole psi cycle of the #219 MIDI 36 capture. For each
  partial, the error is the RMS over 18 psi bins of the model ratio curve minus
  the reference ratio curve.
* confirmation, for the chosen candidate only: the frozen M1A phrase. The model
  is rendered at each event's measured reference psi (#218 method). Notch cells
  are excluded by a reference-only conditioning criterion.

Every precondition REFUSES (exit 2) rather than reports. Exit 0 means the rules
ran to a verdict, whatever that verdict is.
"""
from __future__ import annotations

import json
import math
import subprocess
import sys

import numpy as np

import analyse_m1a_phase_cycle as ap
import diagnose_m1a_harmonic_phase as d
import measure_m1a_drive as dx
import mono_m1a_score as bass

ROOT, SR, lead = bass.ROOT, bass.SR, bass.lead
OUT = ROOT / "docs/scorecard/mono-m1a-miniv3/phase-aware-337"
PHASE_REPORT = ROOT / "docs/scorecard/mono-m1a-miniv3/phase-cycle-capture/report.json"
DIAG_REPORT = ROOT / "docs/scorecard/mono-m1a-miniv3/harmonic-diagnosis/report.json"
RECORD = ROOT / "docs/scorecard/results/M1A.json"

BASELINE = .75
CANDIDATES = (.50, .25)
PARTIALS = tuple(f"h{k}" for k in range(2, 13))
MIN_GAIN_DB = .5          # selection 1 and confirmation 1
BRIGHT_TOL_DB = .5        # selection 3 and confirmation 4
NOTCH_DB = -6.            # conditioning below this excludes an even cell
PSI_MATCH_TOL_DEG = 5.
CELL_TOL_DB = 1.          # the scorer's per-partial screening limit
MIDI43_EVENT = 1


class Refused(bass.Refused):
    pass


# ---------------------------------------------------------------- pure rules
def finite(what, value):
    """A measurement that is not a finite real number REFUSES: every comparison
    below is False against NaN, so an unvalidated NaN passes whichever guard
    asks 'is it worse than'."""
    if isinstance(value, bool) or not isinstance(value, (int, float, np.integer, np.floating)) \
            or not math.isfinite(value):
        raise Refused(f"{what} is {value!r}, not a finite number")
    return float(value)


def ratio_curves(summary):
    """{hk: 18-bin ratio curve in dB}; summary has partials.hk.binned_dbfs."""
    p = summary["partials"]
    h1 = np.asarray(p["h1"]["binned_dbfs"], dtype=np.float64)
    out = {}
    for h in PARTIALS:
        curve = np.asarray(p[h]["binned_dbfs"], dtype=np.float64) - h1
        if curve.shape != (ap.NBINS,) or not np.isfinite(curve).all():
            raise Refused(f"{h}: ratio curve is not {ap.NBINS} finite bins")
        out[h] = curve
    return out


def phase_aware_errors(model_summary, reference_summary):
    """E_k = RMS over psi bins of (model ratio curve - reference ratio curve)."""
    m, r = ratio_curves(model_summary), ratio_curves(reference_summary)
    return {h: float(np.sqrt(np.mean((m[h] - r[h]) ** 2))) for h in PARTIALS}


def brightness_db(levels_dbfs):
    """Power of h2..h12 relative to h1, dB. levels_dbfs: {hk: dBFS} with h1."""
    if any(levels_dbfs.get(h) is None or not math.isfinite(levels_dbfs[h]) for h in ("h1", *PARTIALS)):
        raise Refused("brightness needs finite h1..h12 levels")
    upper = sum(10 ** (levels_dbfs[h] / 10) for h in PARTIALS)
    return 10 * math.log10(upper) - levels_dbfs["h1"]


def mean_brightness_db(per_event_levels):
    """Power mean over events of the per-event brightness ratio."""
    return 10 * math.log10(float(np.mean([10 ** (brightness_db(e) / 10) for e in per_event_levels])))


def brightness_ok(candidate, baseline, reference, tol=BRIGHT_TOL_DB):
    """A candidate may not end up darker than BOTH the baseline and the
    reference by more than tol."""
    return not (candidate < baseline - tol and candidate < reference - tol)


def conditioning_db(a, b):
    """20 log10(|a+b| / (|a|+|b|)) for two complex contributors; -inf when they
    cancel exactly."""
    den = abs(a) + abs(b)
    if den <= 0:
        raise Refused("conditioning: both contributors are zero")
    num = abs(a + b)
    return -math.inf if num == 0 else 20 * math.log10(num / den)


def included(h, conditioning_by_even):
    """Odd partials are always included. An even partial is included only if
    its reference conditioning is >= NOTCH_DB."""
    k = int(h[1:])
    return True if k % 2 else conditioning_by_even[h] >= NOTCH_DB


def rms(values):
    values = list(values)
    if not values:
        raise Refused("RMS over an empty cell set")
    return float(np.sqrt(np.mean(np.square(values))))


def preservation_violations(base_props, cand_props):
    """Every baseline-passing graded property must still pass; validity of
    every property must not change."""
    out = []
    for name, prop in base_props.items():
        cand = cand_props[name]
        if name in dx.GRADED:
            for side, p in (("baseline", prop), ("candidate", cand)):
                if p.get("valid"):
                    finite(f"{side} {name} error", p.get("error"))
                    finite(f"{side} {name} tolerance", p.get("tolerance"))
        if bool(prop.get("valid")) != bool(cand.get("valid")):
            out.append(f"{name}: validity {prop.get('valid')} -> {cand.get('valid')}")
        elif name in dx.GRADED and dx.passes(prop) and not dx.passes(cand):
            out.append(f"{name}: pass -> fail ({prop['error']:+.3f} -> {cand['error']:+.3f})")
    return out


def select(rows, baseline_drive=BASELINE):
    """rows: {drive: {'max_error_db', 'violations', 'brightness_db'}} plus
    'reference_brightness_db' on every row. Returns (choice or None, reasons)."""
    for drive, row in rows.items():
        for key in ("max_error_db", "brightness_db", "reference_brightness_db"):
            finite(f"drive {drive} {key}", row.get(key))
    base = rows[baseline_drive]
    reasons, eligible = {}, {}
    for drive, row in rows.items():
        if drive == baseline_drive:
            continue
        why = []
        if base["max_error_db"] - row["max_error_db"] < MIN_GAIN_DB:
            why.append(f"max phase-aware error {row['max_error_db']:.3f} dB is not >= {MIN_GAIN_DB} dB "
                       f"below baseline {base['max_error_db']:.3f}")
        if row["violations"]:
            why.append("preservation: " + "; ".join(row["violations"]))
        if not brightness_ok(row["brightness_db"], base["brightness_db"], row["reference_brightness_db"]):
            why.append(f"darker than baseline and reference by > {BRIGHT_TOL_DB} dB")
        reasons[drive] = why
        if not why:
            eligible[drive] = row["max_error_db"]
    return (min(eligible, key=eligible.get) if eligible else None), reasons


def confirmation_stats(errors, include):
    """errors/include: list over events of {hk: value}/{hk: bool}."""
    cells = [(i, h) for i, e in enumerate(errors) for h in PARTIALS if include[i][h]]
    return {"n_included": len(cells),
            "e_conf_db": rms(errors[i][h] for i, h in cells),
            "e_43_db": rms(errors[MIDI43_EVENT][h] for i, h in cells if i == MIDI43_EVENT),
            "cells_within_tol": sum(abs(errors[i][h]) <= CELL_TOL_DB for i, h in cells),
            "max_abs_db": max(abs(errors[i][h]) for i, h in cells)}


def confirm(base, cand, ref_brightness):
    """base/cand: confirmation_stats + 'brightness_db'. Returns verdict, failures."""
    for side, st in (("baseline", base), ("candidate", cand)):
        for key in ("e_conf_db", "e_43_db", "brightness_db"):
            finite(f"confirmation {side} {key}", st.get(key))
        if finite(f"confirmation {side} cells_within_tol", st.get("cells_within_tol")) < 0:
            raise Refused(f"confirmation {side} cells_within_tol is negative")
        if st.get("n_included", 1) < 1:
            raise Refused(f"confirmation {side} has no included cells")
    finite("confirmation reference brightness", ref_brightness)
    failures = []
    if cand["e_conf_db"] > base["e_conf_db"] - MIN_GAIN_DB:
        failures.append(f"E_conf {cand['e_conf_db']:.3f} dB is not <= baseline {base['e_conf_db']:.3f} - {MIN_GAIN_DB}")
    if not cand["e_43_db"] < base["e_43_db"]:
        failures.append(f"E_43 {cand['e_43_db']:.3f} dB is not < baseline {base['e_43_db']:.3f}")
    if cand["cells_within_tol"] < base["cells_within_tol"]:
        failures.append(f"cells within {CELL_TOL_DB} dB fell {base['cells_within_tol']} -> {cand['cells_within_tol']}")
    if not brightness_ok(cand["brightness_db"], base["brightness_db"], ref_brightness):
        failures.append(f"darker than baseline and reference by > {BRIGHT_TOL_DB} dB")
    return ("CONFIRMED" if not failures else "NOT CONFIRMED"), failures


def check_baseline_record(measured_props, record_props):
    """The current engine must reproduce the committed M1A record. REFUSES on a
    property that is missing, whose validity flag differs, or whose recorded or
    measured value is not finite (a NaN makes every difference test False)."""
    for name, prop in record_props.items():
        if name not in measured_props:
            raise Refused(f"baseline has no property {name} that results/M1A.json records")
        got = measured_props[name]
        if not isinstance(prop.get("valid"), bool) or not isinstance(got.get("valid"), bool):
            raise Refused(f"baseline {name}: validity flag is not a bool")
        if prop["valid"] != got["valid"]:
            raise Refused(f"baseline {name} validity {got['valid']} does not reproduce results/M1A.json's "
                          f"{prop['valid']}")
        if prop["valid"]:
            want = finite(f"results/M1A.json {name} value", prop.get("value"))
            have = finite(f"baseline {name} value", got.get("value"))
            if abs(have - want) > 1e-6:
                raise Refused(f"baseline {name} does not reproduce results/M1A.json")


# ------------------------------------------------------ known-answer control
def _synthetic_summary(osc1_db=None, osc2_db=None, psi_shift_deg=0.):
    """An analytic psi-binned summary for two ideal saws (osc1 1/k, osc2 0.535/j
    at even partial 2j) at the 18 bin centres. Optional persistent dB offsets per
    partial on either contributor, and a psi relabelling, give KNOWN errors."""
    osc1_db, osc2_db = osc1_db or {}, osc2_db or {}
    centres = np.radians([ap.bin_centre(i) + psi_shift_deg for i in range(ap.NBINS)])
    partials = {}
    for k in range(1, ap.KMAX + 1):
        a = 1 / k * 10 ** (osc1_db.get(f"h{k}", 0.) / 20)
        if k % 2:
            amp = np.full(ap.NBINS, a)
        else:
            j = k // 2
            b = .535 / j * 10 ** (osc2_db.get(f"h{k}", 0.) / 20)
            amp = np.abs(a + b * np.exp(1j * j * centres))
        partials[f"h{k}"] = {"binned_dbfs": (20 * np.log10(np.maximum(amp, 1e-12))).tolist()}
    return {"partials": partials}


def synthetic_control():
    """Known answers for the phase-aware metric and the conditioning criterion."""
    ref = _synthetic_summary()
    cases = {}
    same = phase_aware_errors(ref, ref)
    cases["identical -> 0 on every partial"] = (max(same.values()), 0., 1e-12)
    odd = phase_aware_errors(_synthetic_summary(osc1_db={"h7": 2.}), ref)
    cases["persistent +2 dB on odd h7 -> E_h7 = 2"] = (odd["h7"], 2., 1e-9)
    cases["persistent +2 dB on odd h7 leaves h5 at 0"] = (odd["h5"], 0., 1e-12)
    h1 = phase_aware_errors(_synthetic_summary(osc1_db={"h1": -1.}), ref)
    cases["h1 -1 dB -> every odd ratio +1"] = (h1["h3"], 1., 1e-9)
    # psi relabelled by 60 deg: power means unchanged, curves misaligned -> the
    # metric must see it (a phase-blind power mean would not)
    shifted = phase_aware_errors(_synthetic_summary(psi_shift_deg=60.), ref)
    cases["psi relabelled 60 deg -> h2 error > 3 dB"] = (float(shifted["h2"] > 3.), 1., 0.)
    cases["psi relabelled 60 deg leaves odd h3 at 0"] = (shifted["h3"], 0., 1e-12)
    # conditioning: equal contributors at 0, 90 and 180 degrees
    cases["conditioning in phase = 0 dB"] = (conditioning_db(1 + 0j, 1 + 0j), 0., 1e-12)
    cases["conditioning quadrature = -3.0103 dB"] = (conditioning_db(1 + 0j, 1j), 20 * math.log10(math.sqrt(.5)), 1e-9)
    cases["conditioning antiphase = -inf (excluded)"] = (float(not included("h4", {"h4": conditioning_db(1 + 0j, -1 + 0j)})), 1., 0.)
    # brightness: a single partial h2 at -6 dB relative to h1 reads -6
    lv = {"h1": 0., **{h: -300. for h in PARTIALS}}
    lv["h2"] = -6.
    cases["brightness of one partial at -6 dB"] = (brightness_db(lv), -6., 1e-6)
    report = {name: {"expected": exp, "got": got, "ok": abs(got - exp) <= tol}
              for name, (got, exp, tol) in cases.items()}
    return report


# ------------------------------------------------------------- rendering
def matched_patch(drive, vol):
    """#218's detuned diagnostic patch, with only drive and vol changed."""
    det = json.loads((d.DETUNE_DIR / "detune-diagnostic.json").read_text())["patch"]["detune"][1]
    return {**d._patch(det), "drive": drive, "vol": vol}


def reference_confirmation():
    """Per event: reference psi, reference partials and the conditioning of
    every even partial, all from the frozen phrase and its open controls."""
    manifest, ref, controls = d.reference_audio()
    rows = []
    for e in d.EVENTS:
        phase = d.relative_phase(controls["osc1_open"], controls["osc2_open"], e)
        start, stop = d.window(e)
        f1 = phase["f1_hz"]
        cond = {}
        for j in range(1, d.JMAX + 1):
            a = d.project(controls["osc1_open"], 2 * j * f1, start, stop)
            b = d.project(controls["osc2_open"], 2 * j * f1, start, stop)
            cond[f"h{2 * j}"] = conditioning_db(a, b)
        rows.append({"psi_deg": phase["psi_deg"], "conditioning_db": cond,
                     "include": {h: included(h, cond) for h in PARTIALS},
                     "partials": d.partials(ref, e)})
    return manifest, ref, rows


def matched_render(drive, vol, ref_rows):
    """The model at each event's reference psi. REFUSES on a > 5 deg miss."""
    patch = matched_patch(drive, vol)
    m = patch["mix"][1]
    open_p = {**patch, "cutoff": (20000, 20000)}
    n = len(bass.load_reference()[1])
    out = []
    for i, (e, r) in enumerate(zip(d.EVENTS, ref_rows)):
        p = d.phase_for(patch, e, r["psi_deg"])
        mix = d.render(patch, p)[:n]
        iso = d.relative_phase(d.render(open_p, p, (1., 0., 0.))[:n].astype(np.float64) / 32768,
                               d.render(open_p, p, (0., m, 0.))[:n].astype(np.float64) / 32768, e)
        err = d.wrap(iso["psi_deg"] - r["psi_deg"])
        if abs(err) > PSI_MATCH_TOL_DEG:
            raise Refused(f"drive {drive}: matched render missed the reference psi at event {i} by {err:.2f} deg")
        out.append({"osc2_phase": int(p), "psi_error_deg": err, "pcm_sha256": d.pcm_sha(mix),
                    "partials": d.partials(mix.astype(np.float64) / 32768, e)})
    return out


def confirmation_side(rows, ref_rows):
    errors = [{h: r["partials"]["ratio_db"][h] - rr["partials"]["ratio_db"][h] for h in PARTIALS}
              for r, rr in zip(rows, ref_rows)]
    stats = confirmation_stats(errors, [rr["include"] for rr in ref_rows])
    stats["brightness_db"] = mean_brightness_db([r["partials"]["absolute_dbfs"] for r in rows])
    stats["errors_db"] = errors
    return stats


def development_row(manifest, reference, drive, base_patch, base_rms, cents, ref_summary, ref_bright):
    if drive == BASELINE:
        vol, delta = base_patch["vol"], 0.
    else:
        unc = bass.compare_audio(dx.render_phrase(dx.phrase_patch(manifest, drive), len(reference))
                                 .astype(np.float64) / 32768, reference)
        delta = dx.compensation_db(base_rms, [e["rms_dbfs"]["model"] for e in unc["events"]])
        vol = base_patch["vol"] * 10 ** (delta / 20)
    patch = dx.phrase_patch(manifest, drive, vol)
    pcm = dx.render_phrase(patch, len(reference))
    measured = bass.compare_audio(pcm.astype(np.float64) / 32768, reference)
    mp = dx.moving_phase(patch, cents)
    errors = phase_aware_errors(mp, ref_summary)
    return {"drive": drive, "vol": vol, "vol_register_q15": int(round(vol * 32768)), "compensation_db": delta,
            "ladder_gain_register": lead.vf.LadderFx(**lead.vf.LADDER_CFG).regs(patch["q"], drive)[1],
            "phrase_pcm_sha256": dx.pcm_sha(pcm), "phrase_clipping": dx.clipping(pcm),
            "moving_phase_pcm_sha256": mp["pcm_sha256"], "moving_phase_drift_hz": mp["drift_hz"],
            "moving_phase_refused_windows": mp["refused_windows"],
            "phase_aware_error_db": errors, "max_error_db": max(errors.values()),
            "mean_error_db": float(np.mean(list(errors.values()))),
            "worst_partial": max(errors, key=errors.get),
            "odd_range_mean_db": mp["odd_range_mean_db"],
            "psi_mean_ratio_db": {h: mp["partials"][h]["ratio_to_h1_db"] for h in PARTIALS},
            "brightness_db": brightness_db({h: mp["partials"][h]["mean_dbfs"] for h in ("h1", *PARTIALS)}),
            "reference_brightness_db": ref_bright,
            "properties": {k: {kk: v.get(kk) for kk in ("valid", "value", "reference", "error", "tolerance")}
                           for k, v in measured["properties"].items()},
            "harmonic_shape_db_official": measured["properties"]["Harmonic shape"]["value"],
            "_measured": measured, "_mp": mp}


def main():
    control = synthetic_control()
    if "--control" in sys.argv:
        print(json.dumps(control, indent=1))
        return 0 if all(v["ok"] for v in control.values()) else 1
    try:
        if not all(v["ok"] for v in control.values()):
            raise Refused(f"known-answer control failed: {[k for k, v in control.items() if not v['ok']]}")
        manifest, reference = bass.load_reference()
        record = json.loads(RECORD.read_text())
        phase = json.loads(PHASE_REPORT.read_text())
        if bass.sha(ap.CAPTURE / "capture.json") != phase["capture_sha256"]:
            raise Refused("phase-cycle report does not describe the committed capture")
        ref_summary = phase["summaries"]["pooled"]
        ref_bright = brightness_db({h: ref_summary["partials"][h]["mean_dbfs"] for h in ("h1", *PARTIALS)})
        cents = float(np.mean([v["octave_offset_cents"] for v in phase["reference"].values()]))
        noise = phase_aware_errors(phase["summaries"]["A"], phase["summaries"]["B"])

        # ---- baseline preconditions: the current engine is the measured one
        base_patch = dx.phrase_patch(manifest, BASELINE)
        rate, committed = dx.wavfile.read(bass.MANIFEST.parent / "m1a-model.wav")
        base_pcm = dx.render_phrase(base_patch, len(reference))
        if dx.pcm_sha(base_pcm) != dx.pcm_sha(committed):
            raise Refused("baseline render does not reproduce the committed m1a-model.wav")
        base_measured = bass.compare_audio(base_pcm.astype(np.float64) / 32768, reference)
        check_baseline_record(base_measured["properties"], record["diagnostics"]["properties"])
        base_rms = [e["rms_dbfs"]["model"] for e in base_measured["events"]]

        # ---- development + selection
        rows = {}
        for drive in (BASELINE, *CANDIDATES):
            rows[drive] = development_row(manifest, reference, drive, base_patch, base_rms, cents,
                                          ref_summary, ref_bright)
            print(f"dev drive {drive}: max E {rows[drive]['max_error_db']:.3f} ({rows[drive]['worst_partial']}), "
                  f"mean {rows[drive]['mean_error_db']:.3f}, odd range {rows[drive]['odd_range_mean_db']:.2f}", flush=True)
        for h in ap.ODD:
            if abs(rows[BASELINE]["_mp"]["partials"][h]["range_db"]
                   - phase["summaries"]["model"]["partials"][h]["range_db"]) > 1e-9:
                raise Refused("baseline moving-phase render does not reproduce #219's model curves")
        for drive, row in rows.items():
            row["violations"] = ([] if drive == BASELINE else
                                 preservation_violations(base_measured["properties"], row["_measured"]["properties"]))
        choice, reasons = select(rows)
        print(f"selection: {choice}; reasons {reasons}", flush=True)

        # ---- confirmation (chosen candidate only)
        confirmation = None
        if choice is not None:
            _, _, ref_rows = reference_confirmation()
            diag = json.loads(DIAG_REPORT.read_text())
            base_rows = matched_render(BASELINE, base_patch["vol"], ref_rows)
            for i, r in enumerate(base_rows):
                if r["pcm_sha256"] != diag["renders"][f"matched event {i}"]["pcm_sha256"]:
                    raise Refused(f"baseline matched render at event {i} does not reproduce #218's")
            cand_rows = matched_render(choice, rows[choice]["vol"], ref_rows)
            ref_bright_conf = mean_brightness_db([rr["partials"]["absolute_dbfs"] for rr in ref_rows])
            b, c = confirmation_side(base_rows, ref_rows), confirmation_side(cand_rows, ref_rows)
            verdict, failures = confirm(b, c, ref_bright_conf)
            confirmation = {
                "verdict": verdict, "failures": failures,
                "reference": [{"psi_deg": rr["psi_deg"], "conditioning_db": rr["conditioning_db"],
                               "include": rr["include"]} for rr in ref_rows],
                "reference_brightness_db": ref_bright_conf,
                "baseline": {**b, "renders": [{k: r[k] for k in ("osc2_phase", "psi_error_deg", "pcm_sha256")}
                                              for r in base_rows]},
                "candidate": {**c, "drive": choice,
                              "renders": [{k: r[k] for k in ("osc2_phase", "psi_error_deg", "pcm_sha256")}
                                          for r in cand_rows]}}
            print(f"confirmation: {verdict} {failures}", flush=True)

        report = {
            "schema": "m1a-phase-aware-337-v1",
            "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "worktree_dirty": bool(subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                                                  cwd=ROOT, capture_output=True, text=True).stdout.strip()),
            "source_sha256": {f: bass.sha(ROOT / f) for f in (
                "tools/phase_aware_m1a.py", "tools/measure_m1a_drive.py", "tools/analyse_m1a_phase_cycle.py",
                "tools/diagnose_m1a_harmonic_phase.py", "tools/mono_m1a_score.py",
                "model/voice_fx.py", "model/fixed.py", "model/audio_measure.py")},
            "preconditions": {"baseline_reproduces_committed_audio": True, "baseline_reproduces_record": True,
                              "baseline_reproduces_phase_cycle_model": True,
                              "baseline_matched_reproduces_218": True if confirmation is not None else None},
            "data_status": {"development": "#219 MIDI 36 phase cycle; inspected; selection is a fit by construction",
                            "confirmation": "frozen M1A phrase, phase-matched; not used for selection; not "
                                            "pristine (baseline matched errors and candidates' locked-phase "
                                            "errors were published earlier)"},
            "reference_take_noise_db": noise,
            "reference_brightness_db": ref_bright,
            "development": {str(k): {kk: vv for kk, vv in v.items() if not kk.startswith("_")}
                            for k, v in rows.items()},
            "selection": {"choice": None if choice is None else str(choice),
                          "ineligible_because": {str(k): v for k, v in reasons.items()}},
            "confirmation": confirmation,
            "synthetic_control": control,
        }
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "report.json").write_text(json.dumps(report, indent=1) + "\n")
        return 0
    except Refused as exc:
        print(f"REFUSED: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
