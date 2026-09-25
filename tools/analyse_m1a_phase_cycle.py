#!/usr/bin/env python3
"""Does the Mini V3 darken near relative phase 0 the way the model does?

    .venv/bin/python tools/analyse_m1a_phase_cycle.py              # renders the model; ~2 min
    .venv/bin/python tools/analyse_m1a_phase_cycle.py --control    # known-answer control only

Reads the committed phase-cycle capture (`capture_m1a_phase_cycle.py`), renders
the model ONCE under the same moving-phase condition, analyses both with the
same code and windows, and applies the decision rule pre-registered in
docs/scorecard/mono-m1a-miniv3/phase-cycle-capture/README.md.

Changes nothing: not the frozen reference, the scorer, the patch, the engine
or any tolerance. The model render is diagnostic: oscillator 2 is detuned by
the reference's MEASURED octave offset so that the relative phase psi rotates
through each 250 ms window at the reference's rate (~24 degrees). Before it is
used, the same render function must reproduce the committed M1A model audio
bit-exactly with the selected (locked) patch.

Conventions
  level   20*log10 of one partial's AMPLITUDE (Blackman-Harris coherent
          projection, `windowed_tone_amplitude`, the scorer's primitive);
          a full-scale sine reads 0 dBFS
  means   bin and zone averages are power means (mean of amplitude squared)
  psi     oscillator-2 phase minus twice oscillator-1 phase, degrees of
          oscillator 2, at the window centre, from the isolated open-filter
          takes with the #218 estimator (theta_j fitted to j*psi)
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

import numpy as np
from scipy.io import wavfile

import diagnose_m1a_harmonic_phase as d

bass, lead, am, SR = d.bass, d.lead, d.am, d.SR
ROOT = d.ROOT
CAPTURE = ROOT / "docs/scorecard/mono-m1a-miniv3/phase-cycle-capture"
NOTE = 36
F36 = 440 * 2 ** ((NOTE - 69) / 12)
WIN = 12000                  # 250 ms, the scorer's harmonic window length
HOP = 2400                   # 50 ms
FROM_S = 1.0                 # after note-on
END_GUARD_S = .05            # before gate-off
KMAX = 12
JMAX = KMAX // 2
ODD = ("h5", "h7", "h9", "h11")
NBINS = 18                   # 20-degree bins centred on 0, +-20, ... 180
BIN_DEG = 360 / NBINS
NEAR_DEG, FAR_DEG = 20., 60.
MIN_PER_BIN = 3
PSI_FIT_TOL = 2.0
MAX_REFUSED = .10
DRIFT_TOL = .05
MODEL_ON_S, MODEL_GATE_S, MODEL_SECONDS = .1, 12., 13.1

# the pre-registered decision thresholds (README, "Decision rule")
AGREE_DARK_DB, AGREE_BIN_DB = 1.0, 1.5
PREMISE_DARK_DB, PREMISE_COUNT = -3.0, 3
FLAT_RANGE_DB, FLAT_DARK_DB = 1.0, 1.0
REDIRECT_FRACTION, REDIRECT_COUNT, REDIRECT_MIN_DEG = .5, 3, 40.


class Refused(d.Refused):
    pass


# ---------------------------------------------------------------- estimators
def window_starts(on_s, gate_s, n):
    a = round((on_s + FROM_S) * SR)
    b = min(n, round((on_s + gate_s - END_GUARD_S) * SR))
    return list(range(a, b - WIN + 1, HOP))


def _f0(x, start, nominal):
    est = am.refine_f0(np.asarray(x[start:start + WIN], dtype=np.float64), nominal, SR)
    if not est.ok:
        raise Refused(f"pitch refused at sample {start}: {est.reason}")
    return float(est.value)


def psi_at(osc1, osc2, start, psi_offset=0.):
    """psi at the window centre from isolated oscillators; None if theta_j is
    not j*psi within PSI_FIT_TOL (the #218 refusal)."""
    f1 = _f0(osc1, start, F36)
    theta = []
    for j in range(1, JMAX + 1):
        a = d.project(osc1, 2 * j * f1, start, start + WIN)
        b = d.project(osc2, 2 * j * f1, start, start + WIN)
        theta.append(float(np.angle(b / a, deg=True)))
    psi, residual = d.fit_psi(theta)
    return (d.wrap(psi + psi_offset) if residual <= PSI_FIT_TOL else None), residual, f1


def levels_at(mix, start):
    """h1..h12 dBFS of the mix in one window, at k * the mix's own f0."""
    f0 = _f0(mix, start, F36)
    seg = np.asarray(mix[start:start + WIN], dtype=np.float64)
    out = []
    for k in range(1, KMAX + 1):
        amp = am.windowed_tone_amplitude(seg, k * f0, SR)
        if not amp.ok or amp.value <= 0:
            raise Refused(f"h{k} unmeasurable at sample {start}")
        out.append(20 * math.log10(amp.value))
    return out


def measure(mix, osc1, osc2, on_s, gate_s, psi_offset=0.):
    """Per-window psi and levels for one take. Refuses when too many windows
    have no single relative phase."""
    starts = window_starts(on_s, gate_s, min(len(mix), len(osc1), len(osc2)))
    rows, refused = [], 0
    for s in starts:
        psi, residual, f1 = psi_at(osc1, osc2, s, psi_offset)
        if psi is None:
            refused += 1
            continue
        rows.append({"t_s": (s + (WIN - 1) / 2) / SR - on_s, "psi_deg": psi,
                     "psi_fit_residual_deg": residual, "f1_hz": f1,
                     "levels_dbfs": levels_at(mix, s)})
    if not starts or refused > MAX_REFUSED * len(starts):
        raise Refused(f"{refused} of {len(starts)} windows have no single relative phase")
    t = np.array([r["t_s"] for r in rows])
    psi = np.unwrap(np.radians([r["psi_deg"] for r in rows]))
    drift_hz = float(np.polyfit(t, psi, 1)[0] / (2 * np.pi))
    f1 = float(np.median([r["f1_hz"] for r in rows]))
    return {"windows": rows, "n_windows": len(starts), "refused_windows": refused,
            "drift_hz": drift_hz, "f1_hz": f1,
            "octave_offset_cents": 1200 * math.log2((2 * f1 + drift_hz) / (2 * f1)),
            "psi_rotation_per_window_deg": 360 * drift_hz * WIN / SR}


def _pmean(db):
    db = np.asarray(db, dtype=np.float64)
    return float(10 * np.log10(np.mean(10 ** (db / 10))))


def bin_of(psi):
    return int(round(d.wrap(psi) / BIN_DEG)) % NBINS


def bin_centre(i):
    return d.wrap(i * BIN_DEG)


def summarise(windows):
    """Per partial: power-mean level per psi bin, range, and dark = near-0
    zone minus far zone. Refuses a bin with fewer than MIN_PER_BIN windows."""
    psi = np.array([w["psi_deg"] for w in windows])
    lev = np.array([w["levels_dbfs"] for w in windows])
    idx = np.array([bin_of(p) for p in psi])
    counts = np.bincount(idx, minlength=NBINS)
    if counts.min() < MIN_PER_BIN:
        raise Refused(f"psi coverage: bin {bin_centre(int(np.argmin(counts))):+.0f} deg "
                      f"has {counts.min()} windows")
    near, far = np.abs(psi) <= NEAR_DEG, np.abs(psi) >= FAR_DEG
    out = {"bin_centres_deg": [bin_centre(i) for i in range(NBINS)], "bin_counts": counts.tolist(),
           "partials": {}}
    for k in range(KMAX):
        binned = [_pmean(lev[idx == i, k]) for i in range(NBINS)]
        darkest = int(np.argmin(binned))
        out["partials"][f"h{k + 1}"] = {
            "binned_dbfs": binned, "range_db": max(binned) - min(binned),
            "dark_db": _pmean(lev[near, k]) - _pmean(lev[far, k]),
            "darkest_bin_deg": bin_centre(darkest), "brightest_bin_deg": bin_centre(int(np.argmax(binned))),
            "mean_dbfs": _pmean(lev[:, k])}
    return out


# ------------------------------------------------------------ decision rule
def decide(ref_a, ref_b, ref_pooled, model, check_agreement=True):
    """The pre-registered rule. Inputs are `summarise` outputs."""
    rows, reasons = {}, []
    for h in ODD:
        a, b, p, m = (s["partials"][h] for s in (ref_a, ref_b, ref_pooled, model))
        rows[h] = {"dark_take_a": a["dark_db"], "dark_take_b": b["dark_db"],
                   "take_dark_diff_db": abs(a["dark_db"] - b["dark_db"]),
                   "take_max_bin_diff_db": float(np.max(np.abs(np.subtract(a["binned_dbfs"], b["binned_dbfs"])))),
                   "ref_range_db": p["range_db"], "ref_dark_db": p["dark_db"],
                   "ref_darkest_bin_deg": p["darkest_bin_deg"],
                   "model_range_db": m["range_db"], "model_dark_db": m["dark_db"],
                   "model_darkest_bin_deg": m["darkest_bin_deg"]}
    if check_agreement:
        bad = [h for h, r in rows.items() if r["take_dark_diff_db"] > AGREE_DARK_DB
               or r["take_max_bin_diff_db"] > AGREE_BIN_DB]
        if bad:
            return {"verdict": "INCONCLUSIVE", "step": 1, "rows": rows,
                    "reason": f"reference takes disagree on {bad}",
                    "resolve": "find what differs between takes (time since note-on, not psi, "
                               "drives the level) before any comparison"}
    premise = [h for h, r in rows.items() if r["model_dark_db"] <= PREMISE_DARK_DB]
    if len(premise) < PREMISE_COUNT:
        return {"verdict": "INCONCLUSIVE", "step": 2, "rows": rows,
                "reason": f"the moving-phase model darkens by <= {PREMISE_DARK_DB} dB on only {premise}",
                "resolve": "the model's static-phase darkening does not survive equivalent windowing; "
                           "re-examine the #218 premise before comparing"}
    if all(r["ref_range_db"] < FLAT_RANGE_DB and abs(r["ref_dark_db"]) <= FLAT_DARK_DB
           for r in rows.values()):
        return {"verdict": "SUPPORTS-DRIVE", "step": 3, "rows": rows,
                "reason": "the reference's odd partials are flat across the psi cycle while the "
                          "moving-phase model darkens near 0; supports investigating the model's "
                          "filter-input level/drive (does not uniquely prove it)"}
    similar = [h for h, r in rows.items()
               if r["ref_dark_db"] <= REDIRECT_FRACTION * r["model_dark_db"]
               and abs(r["ref_darkest_bin_deg"]) <= REDIRECT_MIN_DEG]
    if len(similar) >= REDIRECT_COUNT:
        return {"verdict": "REDIRECT-TO-PHASE", "step": 4, "rows": rows, "similar": similar,
                "reason": "the reference darkens near psi = 0 by at least half the model's amount; "
                          "the model's odd partials are phase-appropriate. This does NOT show that "
                          "permanently locked oscillators are appropriate: next, oscillator phase behaviour"}
    return {"verdict": "INCONCLUSIVE", "step": 5, "rows": rows, "similar": similar,
            "reason": "the reference neither stays flat nor darkens like the model near 0",
            "resolve": "see per-partial rows; a capture at other notes/levels or the Mini's own "
                       "drive stage would be needed"}


# ------------------------------------------------------ known-answer control
def synth_take(dip_db, psi0_deg, drift_hz, on_s=.1, gate_s=12., seconds=13.1,
               odd_gain_db=0., dip_width_deg=25.):
    """Two ideal saws (osc1: 12 partials at F36; osc2: 6 at 2*F36+drift) with a
    KNOWN psi-dependent gain on osc1's odd partials:
    g(psi) = dip_db * exp(-(psi/dip_width)^2) dB. Returns (mix, osc1, osc2,
    true_psi_deg per sample)."""
    n = round(seconds * SR)
    t = np.arange(n) / SR
    ph1 = F36 * t
    ph2 = (2 * F36 + drift_hz) * t + psi0_deg / 360
    psi = (((ph2 - 2 * ph1) * 360) + 180) % 360 - 180
    gain = 10 ** ((dip_db * np.exp(-(psi / dip_width_deg) ** 2) + odd_gain_db) / 20)
    gate = ((t >= on_s) & (t < on_s + gate_s)).astype(np.float64)
    o1_even = sum(.05 / k * np.sin(2 * np.pi * k * ph1) for k in range(2, KMAX + 1, 2))
    o1_odd = sum(.05 / k * np.sin(2 * np.pi * k * ph1) for k in range(1, KMAX + 1, 2))
    osc2 = sum(.05 * .535 / j * np.sin(2 * np.pi * j * ph2) for j in range(1, JMAX + 1))
    mix = (o1_even + o1_odd * gain + osc2) * gate
    return mix, (o1_even + o1_odd) * gate, osc2 * gate, psi


def expected_dark(dip_db, psi_true, windows, on_s, dip_width_deg=25.):
    """dark(h) implied by the construction alone: the known gain's power
    averaged over each window's true psi trajectory (Blackman-Harris weighted),
    zoned by the TRUE window-centre psi (not the measured one, so an estimator
    defect cannot move both sides of the comparison)."""
    w = am._bh4(WIN)
    per_window, centres = [], []
    for row in windows:
        s = round((row["t_s"] + on_s) * SR - (WIN - 1) / 2)
        p = psi_true[s:s + WIN]
        g = 10 ** (dip_db * np.exp(-(p / dip_width_deg) ** 2) / 20)
        # a coherent projection of a sinusoid with slowly varying amplitude
        # returns the window-weighted mean amplitude
        per_window.append(20 * math.log10(float(np.sum(w * g) / np.sum(w))))
        centres.append(float(psi_true[s + (WIN - 1) // 2]))
    per_window, centres = np.array(per_window), np.abs(np.array(centres))
    return _pmean(per_window[centres <= NEAR_DEG]) - _pmean(per_window[centres >= FAR_DEG])


def synthetic_control(defect=None):
    """Known answers through the complete analysis path. Each case's verdict
    is fixed by construction; `defect` injects a known analysis error that
    must turn the control red."""
    drift = -.2634
    offset = 180. if defect == "psi_offset_180" else 0.
    agree = defect != "ignore_take_agreement"

    def run(dip, psi0, **kw):
        mix, o1, o2, psi = synth_take(dip, psi0, drift, **kw)
        m = measure(mix, o1, o2, .1, 12., psi_offset=offset)
        return m, psi

    flat_a, _ = run(0., 156.)
    flat_b, _ = run(0., -24.)
    dip_a, psi_true = run(-10., 156.)
    dip_b, _ = run(-10., -24.)
    half_a, _ = run(-3., 156.)
    half_b, _ = run(-3., -24.)
    shifted_b, _ = run(0., -24., odd_gain_db=-3.)        # a take that disagrees
    s = {name: summarise(m["windows"]) for name, m in (
        ("flat_a", flat_a), ("flat_b", flat_b), ("dip_a", dip_a), ("dip_b", dip_b),
        ("half_a", half_a), ("half_b", half_b), ("shifted_b", shifted_b))}
    pooled = lambda a, b: summarise(a["windows"] + b["windows"])
    cases = {
        "flat reference, dipping model": (decide(s["flat_a"], s["flat_b"], pooled(flat_a, flat_b), s["dip_a"], agree),
                                          "SUPPORTS-DRIVE"),
        "reference dips like the model": (decide(s["dip_a"], s["dip_b"], pooled(dip_a, dip_b), s["dip_a"], agree),
                                          "REDIRECT-TO-PHASE"),
        "model does not dip": (decide(s["flat_a"], s["flat_b"], pooled(flat_a, flat_b), s["flat_a"], agree),
                               "INCONCLUSIVE"),
        "reference dips a third as much": (decide(s["half_a"], s["half_b"], pooled(half_a, half_b), s["dip_a"], agree),
                                           "INCONCLUSIVE"),
        "reference takes disagree": (decide(s["flat_a"], s["shifted_b"], pooled(flat_a, shifted_b), s["dip_a"], agree),
                                     "INCONCLUSIVE"),
    }
    want = expected_dark(-10., psi_true, dip_a["windows"], .1)
    got = s["dip_a"]["partials"]["h5"]["dark_db"]
    flat_range = max(s["flat_a"]["partials"][h]["range_db"] for h in ODD)
    report = {name: {"expected": exp, "got": res["verdict"], "ok": res["verdict"] == exp}
              for name, (res, exp) in cases.items()}
    report["known dark h5"] = {"expected": want, "got": got, "ok": abs(got - want) <= .3}
    report["flat odd partials beside beating even partials"] = {
        "expected": "range < 0.3 dB", "got": flat_range, "ok": flat_range < .3}
    report["measured drift"] = {"expected": drift, "got": flat_a["drift_hz"],
                                "ok": abs(flat_a["drift_hz"] - drift) < 1e-3}
    return report


# ------------------------------------------------------------- the model
def model_patch(detune2):
    manifest, _ = bass.load_reference()
    patch = bass.patch_for_reference(manifest)
    detune = list(patch["detune"])
    detune[1] = detune2
    return {**patch, "detune": tuple(detune)}


def render_events(patch, events, seconds, mix=None):
    """`diagnose_m1a_harmonic_phase.render` generalised to any event list (it
    is `voice_fx.render_mono_fx` inlined). Oscillator phases start at reset."""
    vf = lead.vf
    patch = dict(patch) if mix is None else {**patch, "mix": mix}
    seq = [(e["on_s"], e["note"], e["gate_s"], {**patch, "gate": e["gate_s"]}) for e in events]
    voice = lead._voice_for_engine(lead.engine_configuration("selected"))
    n = int(seconds * SR)
    kw0 = dict(seq[0][3])
    kw0.pop("blep", None), kw0.pop("glide", None), kw0.pop("gate", None)
    regs = vf.VoiceFx.patch_regs(**kw0)
    host = vf.KeyHost(glide="off")
    writes = []
    for start, note, _, kw in seq:
        on = int(start * SR)
        writes += [(on, "on", note), (min(n - 1, on + max(1, int(kw["gate"] * SR))), "off", note)]
    voice.reset()
    return voice.play(regs, host.writes(writes, regs), n)


def pcm_sha(pcm):
    return hashlib.sha256(np.asarray(pcm).astype("<i2").tobytes()).hexdigest()


# ------------------------------------------------------------------ main
def _read(path):
    rate, x = wavfile.read(path)
    if rate != SR or x.dtype != np.float32:
        raise Refused(f"{path.name}: format differs ({rate}, {x.dtype})")
    return x.astype(np.float64)


def load_capture():
    record = json.loads((CAPTURE / "capture.json").read_text())
    if record.get("state") != "CAPTURED":
        raise Refused("capture record is not CAPTURED")
    takes = {}
    for name, take in record["takes"].items():
        audio = {}
        for cond in ("full", "osc1_open", "osc2_open"):
            row = take["conditions"][cond]
            if bass.sha(CAPTURE / row["file"]) != row["sha256"]:
                raise Refused(f"{row['file']} hash differs from capture.json")
            audio[cond] = _read(CAPTURE / row["file"])
        takes[name] = (take, audio)
    return record, takes


def plot(ref_windows, model_windows, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(2, 2, figsize=(10, 7), sharex=True)
    for ax, h in zip(axes.flat, ODD):
        k = int(h[1:]) - 1
        for label, windows, colour in (("Mini V3 take A", ref_windows["A"], "#1f77b4"),
                                       ("Mini V3 take B", ref_windows["B"], "#17becf"),
                                       ("model, moving phase", model_windows, "#d62728")):
            psi = [w["psi_deg"] for w in windows]
            ratio = [w["levels_dbfs"][k] - w["levels_dbfs"][0] for w in windows]
            ax.scatter(psi, ratio, s=6, alpha=.6, color=colour, label=label)
        ax.axvspan(-NEAR_DEG, NEAR_DEG, color="#999", alpha=.15)
        ax.set_title(f"{h} relative to h1")
        ax.set_ylabel("dB")
    for ax in axes[1]:
        ax.set_xlabel("relative phase psi at window centre (deg)")
    axes[0, 0].legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=110)


def main():
    if "--control" in sys.argv:
        control = synthetic_control()
        print(json.dumps(control, indent=1))
        return 0 if all(v["ok"] for v in control.values()) else 1
    try:
        control = synthetic_control()
        if not all(v["ok"] for v in control.values()):
            raise Refused(f"known-answer control failed: {control}")
        record, takes = load_capture()
        ref = {}
        for name, (take, audio) in takes.items():
            ref[name] = measure(audio["full"], audio["osc1_open"], audio["osc2_open"],
                                take["note_on_s"], take["gate_s"])
        drift = float(np.mean([m["drift_hz"] for m in ref.values()]))
        cents = float(np.mean([m["octave_offset_cents"] for m in ref.values()]))

        # the render function must reproduce the committed model audio first
        rate, committed = wavfile.read(d.MODEL_WAV)
        locked = model_patch(12.)
        check = render_events(locked, bass.reference.EVENTS, bass.reference.SECONDS)[:len(committed)]
        if pcm_sha(check) != pcm_sha(committed):
            raise Refused("render_events does not reproduce the committed M1A model audio")
        patch = model_patch(12. + cents / 100)
        events = ({"note": NOTE, "on_s": MODEL_ON_S, "gate_s": MODEL_GATE_S},)
        open_patch = {**patch, "cutoff": (20000, 20000)}
        m2 = patch["mix"][1]
        renders = {"full": render_events(patch, events, MODEL_SECONDS),
                   "osc1_open": render_events(open_patch, events, MODEL_SECONDS, (1., 0., 0.)),
                   "osc2_open": render_events(open_patch, events, MODEL_SECONDS, (0., m2, 0.))}
        model = measure(*(renders[c].astype(np.float64) / 32768 for c in ("full", "osc1_open", "osc2_open")),
                        MODEL_ON_S, MODEL_GATE_S)
        if abs(model["drift_hz"] - drift) > DRIFT_TOL * abs(drift):
            raise Refused(f"model drift {model['drift_hz']:.4f} Hz differs from the reference's "
                          f"{drift:.4f} Hz by more than {DRIFT_TOL:.0%}")
        summaries = {"A": summarise(ref["A"]["windows"]), "B": summarise(ref["B"]["windows"]),
                     "pooled": summarise(ref["A"]["windows"] + ref["B"]["windows"]),
                     "model": summarise(model["windows"])}
        verdict = decide(summaries["A"], summaries["B"], summaries["pooled"], summaries["model"])
        plot({k: v["windows"] for k, v in ref.items()}, model["windows"], CAPTURE / "odd-partials-vs-psi.png")
        report = {
            "schema": "m1a-phase-cycle-analysis-v1",
            "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "source_sha256": {f: bass.sha(ROOT / f) for f in (
                "tools/analyse_m1a_phase_cycle.py", "tools/diagnose_m1a_harmonic_phase.py",
                "model/audio_measure.py", "model/voice_fx.py")},
            "capture_sha256": bass.sha(CAPTURE / "capture.json"),
            "conventions": {"level": "20*log10 partial amplitude, windowed_tone_amplitude, 0 dBFS = full-scale sine",
                            "means": "power means of amplitude squared, in dB",
                            "window": f"{WIN} samples ({WIN / SR * 1000:.0f} ms) Blackman-Harris, hop {HOP}, "
                                      f"from on+{FROM_S} s to gate-off-{END_GUARD_S} s",
                            "psi": "osc2 phase minus twice osc1 phase at window centre, isolated open-filter takes",
                            "dark": f"power mean over |psi|<={NEAR_DEG} minus over |psi|>={FAR_DEG}"},
            "reference": {k: {kk: vv for kk, vv in v.items() if kk != "windows"} for k, v in ref.items()},
            "model": {"patch_detune2_semitones": 12. + cents / 100, "reproduced_committed_model_audio": True,
                      "render_pcm_sha256": {k: pcm_sha(v) for k, v in renders.items()},
                      **{kk: vv for kk, vv in model.items() if kk != "windows"}},
            "summaries": summaries, "decision": verdict, "synthetic_control": control,
            "windows": {"A": ref["A"]["windows"], "B": ref["B"]["windows"], "model": model["windows"]},
        }
        (CAPTURE / "report.json").write_text(json.dumps(report, indent=1) + "\n")
        print(json.dumps({"verdict": verdict["verdict"], "step": verdict["step"], "reason": verdict["reason"],
                          "rows": verdict["rows"], "reference_drift_hz": drift, "cents": cents,
                          "model_drift_hz": model["drift_hz"]}, indent=1))
        return 0
    except Refused as exc:
        (CAPTURE / "analysis-refused.json").write_text(json.dumps({"state": "REFUSED", "reason": str(exc)}) + "\n")
        print(f"REFUSED: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
