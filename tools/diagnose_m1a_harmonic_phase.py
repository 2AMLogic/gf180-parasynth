#!/usr/bin/env python3
"""Is M1A's harmonic-shape error PERSISTENT or PHASE-DEPENDENT?

A diagnosis instrument, not a fix. It changes no patch, engine, reference,
estimator or scorer. It reads the frozen Mini V3 reference and isolated
controls, the committed model audio, and (for the full-path test) renders the
model with ONE diagnostic knob the engine already has as state: oscillator 2's
starting phase. Every render first proves the committed audio reproduces.

The method, in three steps, each on the complete nonlinear path:

1. **Reference relative phase** (isolated controls; a PHASE measurement, not a
   level inference). At each event window, the phase of oscillator 2's
   partial j against oscillator 1's partial 2j, projected at the shared
   frequency 2j*f1 and referenced to the window centre. If the relative phase
   is one number psi per event, theta_j = j*psi for every j. The free-running
   hypothesis predicts psi at later events from psi at the first event and the
   independently measured frequencies alone; the reset-at-note-on hypothesis
   predicts the same psi for repeated notes.

2. **Phase-matched model**. Render the model with oscillator 2's starting
   phase chosen so the model's relative phase at an event centre equals the
   reference's measured psi there (verified by rendering the model's own
   isolated oscillators and measuring them with the same estimator). The
   difference between the scored error and the matched residual is the part of
   the error the relative-phase state accounts for, measured through the
   model's complete path -- not inferred from summed isolated recordings.

3. **Classification** per harmonic over the same-note repeat pair (MIDI 36 at
   0.1 s and 4.1 s, where note, patch and window are identical and only
   oscillator phase state can differ), with a contributor-energy check that
   separates interference (same partial energies, different relative phase)
   from a genuine partial-amplitude change. ``UNRESOLVED`` is a first-class
   outcome.

The known-answer control is ``synthetic_control``: synthetic phrases whose
answer is known by construction -- a persistent partial deficit, pure
interference, and a genuine amplitude change that produces the same mixed-
signal swing as interference -- run through the same classifier.
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

import mono_m1a_score as bass

ROOT, SR = bass.ROOT, bass.SR
lead, am = bass.lead, bass.lead.am
EVENTS = bass.reference.EVENTS
OUT = ROOT / "docs/scorecard/mono-m1a-miniv3/harmonic-diagnosis"
RECORD = ROOT / "docs/scorecard/results/M1A.json"
MODEL_WAV = ROOT / "docs/scorecard/mono-m1a-miniv3/m1a-model.wav"
DETUNE_DIR = ROOT / "docs/scorecard/mono-m1a-miniv3/volume-mapping"
WINDOW = (.3, .55)          # the scorer's harmonic window (mono_m1a_score.compare_audio)
KMAX = 12
JMAX = KMAX // 2            # oscillator-2 partials that land on the scored grid
TOL = 1.0                   # the scorer's per-partial screening limit, dB
ENERGY_TOL = .5             # contributor-energy change that counts as an amplitude change, dB
PHASE_FIT_TOL = 2.0         # theta_j must equal j*psi to this, degrees, or psi is refused
REPEAT = (0, 2)             # the same-note repeat pair
CYCLE = 1 << 24             # model phase accumulator (dsp.PHASE_BITS)
SWEEP = 16                  # oscillator-2 starting phases in the static-phase sweep


class Refused(bass.Refused):
    pass


# ---------------------------------------------------------------- estimators
def window(event):
    return round((event["on_s"] + WINDOW[0]) * SR), round((event["on_s"] + WINDOW[1]) * SR)


def centre_s(event):
    start, stop = window(event)
    return (start + stop - 1) / 2 / SR


def project(x, hz, start, stop):
    """Complex amplitude of `hz` over x[start:stop], Blackman-Harris weighted,
    phase referenced to the window CENTRE. A sine of amplitude A and phase phi
    at the centre returns A*exp(i*phi)."""
    seg = np.asarray(x[start:stop], dtype=np.float64)
    if len(seg) * hz / SR < 12:
        raise Refused("projection needs 12 periods in the window")
    w = am._bh4(len(seg))
    t = (np.arange(len(seg)) - (len(seg) - 1) / 2) / SR
    return complex(2 * np.sum(w * seg * np.exp(-2j * np.pi * hz * t)) / np.sum(w))


def wrap(deg):
    return float((np.asarray(deg) + 180.) % 360. - 180.)


def fit_psi(theta):
    """theta[j-1] for j = 1..J -> (psi, worst |theta_j - j*psi|).

    psi is ambiguous by 360/j only if theta_1 is ignored; it is not: psi is
    theta_1, refined by the others through the unwrapped least squares."""
    theta = np.asarray(theta, dtype=np.float64)
    j = np.arange(1, len(theta) + 1)
    psi = theta[0]
    for _ in range(2):
        unwrapped = j * psi + np.array([wrap(t - k * psi) for t, k in zip(theta, j)])
        psi = float(np.sum(j * unwrapped) / np.sum(j * j))
    residual = max(abs(wrap(t - k * psi)) for t, k in zip(theta, j))
    return wrap(psi), residual


def pitch(x, event, octave=0):
    start, stop = window(event)
    estimate = am.refine_f0(np.asarray(x[start:stop], dtype=np.float64),
                            lead.vf.note_hz(event["note"] + octave), SR)
    if not estimate.ok:
        raise Refused(f"pitch refused at MIDI {event['note']} @{event['on_s']:g}s")
    return float(estimate.value)


def partials(x, event):
    """The scorer's own partial measurement at one event: ratios from
    `harmonic_signature` (the scored quantity) and absolute amplitudes from the
    same `windowed_tone_amplitude` primitive it uses."""
    start, stop = window(event)
    clip = np.asarray(x[start:stop], dtype=np.float64)
    f0 = pitch(x, event)
    signature = am.harmonic_signature(clip, SR, f0=f0, kmax=KMAX)
    absolute = {}
    for k in range(1, KMAX + 1):
        amp = am.windowed_tone_amplitude(clip, k * f0, SR)
        absolute[f"h{k}"] = 20 * math.log10(amp.value) if amp.ok and amp.value > 0 else None
    return {"f0": f0, "ratio_db": {f"h{k}": signature.get(f"h{k}") for k in range(2, KMAX + 1)},
            "absolute_dbfs": absolute}


def relative_phase(osc1, osc2, event):
    """Oscillator 2 partial j vs oscillator 1 partial 2j at the shared
    frequency 2j*f1, plus each contributor's own level (absolute, dBFS)."""
    start, stop = window(event)
    f1, f2 = pitch(osc1, event), pitch(osc2, event, 12)
    theta, level1, level2 = [], {}, {}
    for j in range(1, JMAX + 1):
        a, b = project(osc1, 2 * j * f1, start, stop), project(osc2, 2 * j * f1, start, stop)
        theta.append(float(np.angle(b / a, deg=True)))
        level2[f"h{2 * j}"] = 20 * math.log10(abs(b))
    for k in range(1, KMAX + 1):
        level1[f"h{k}"] = 20 * math.log10(abs(project(osc1, k * f1, start, stop)))
    psi, residual = fit_psi(theta)
    if residual > PHASE_FIT_TOL:
        raise Refused(f"relative phase is not one number: theta_j departs from j*psi by {residual:.2f} deg")
    return {"f1_hz": f1, "f2_hz": f2, "drift_hz": f2 - 2 * f1,
            "octave_offset_cents": 1200 * math.log2(f2 / (2 * f1)),
            "theta_deg": theta, "psi_deg": psi, "psi_fit_residual_deg": residual,
            "osc1_dbfs": level1, "osc2_dbfs_at_mix_partial": level2}


def free_running_prediction(psi_first, drift_by_note, events=EVENTS):
    """psi at each event centre if both oscillators run continuously and change
    pitch at note-on, from psi at the first event and per-note drift f2-2*f1."""
    out, psi = [psi_first], psi_first
    for prev, nxt in zip(events, events[1:]):
        t0, t_on, t1 = centre_s(prev), nxt["on_s"], centre_s(nxt)
        cycles = drift_by_note[prev["note"]] * (t_on - t0) + drift_by_note[nxt["note"]] * (t1 - t_on)
        psi = wrap(psi + 360 * cycles)
        out.append(psi)
    return out


def reset_prediction(psi_by_event, events=EVENTS):
    """Reset at note-on: a repeated note has the same psi at the same offset."""
    first = {}
    return [first.setdefault(e["note"], p) for e, p in zip(events, psi_by_event)]


# -------------------------------------------------------------- classifier
def classify(scored, matched, energy_change_db, tol=TOL, energy_tol=ENERGY_TOL):
    """One partial over the same-note repeat instances.

    scored  -- model-minus-reference error at each instance (dB)
    matched -- the same error with the model's relative oscillator phase set to
               the reference's at that instance (None when unavailable)
    energy_change_db -- the largest change of any contributor's OWN level
               between the instances (isolated oscillators)"""
    scored = [float(s) for s in scored]
    if max(abs(s) for s in scored) <= tol:
        return "within tolerance"
    if matched is None:
        return "unresolved"
    matched = [float(m) for m in matched]
    phase_term = [s - m for s, m in zip(scored, matched)]
    if energy_change_db > energy_tol:
        return "amplitude change"
    if max(abs(m) for m in matched) <= tol:
        return "phase-dependent"
    same_sign = all(m > tol for m in matched) or all(m < -tol for m in matched)
    if same_sign and max(matched) - min(matched) <= 2 * tol:
        return "persistent" if max(abs(p) for p in phase_term) <= tol else "mixed"
    return "unresolved"


LABELS = {"within tolerance": "within tolerance",
          "phase-dependent": "phase-sensitive in a controlled diagnostic",
          "persistent": "persistent over the observed events",
          "mixed": "phase-sensitive in a controlled diagnostic, with a persistent remainder",
          "amplitude change": "a contributor's own amplitude changed",
          "unresolved": "unresolved"}


def lives_in(abs_error_db, tol=TOL):
    """Where a ratio-to-fundamental error lives, from ABSOLUTE model-minus-
    reference partial errors {hk: dB} at one instance. The ratio error is
    exactly abs(hk) - abs(h1); this says which term carries it. A common
    output-gain offset cancels in the ratio and so reads 'within tolerance'.
    Returns {hk: 'partial' | 'fundamental' | 'both' | 'within tolerance'}."""
    fundamental = abs_error_db["h1"]
    out = {}
    for key, partial in abs_error_db.items():
        if key == "h1":
            continue
        if abs(partial - fundamental) <= tol:
            out[key] = "within tolerance"
        elif abs(fundamental) <= tol:
            out[key] = "partial"
        elif abs(partial) <= tol:
            out[key] = "fundamental"
        else:
            out[key] = "both"
    return out


def classify_table(scored, matched, energy):
    """scored/matched: {hk: [e_instance...]}; energy: {hk: dB}."""
    return {k: classify(scored[k], matched.get(k) if matched else None, energy.get(k, 0.))
            for k in scored}


# ------------------------------------------------------- synthetic control
def synth_phrase(psi_first_deg, drift_by_note, osc2_gain_db=None, filter_db=None,
                 locked=False, seconds=bass.reference.SECONDS, osc1_gain_db=None):
    """Two ideal saw oscillators (12 partials each) through a per-partial linear
    gain -- the known answer. Returns (osc1, osc2): the mix is their sum.

    locked=True holds oscillator 2 at psi_first exactly twice oscillator 1
    (the model's phase state). osc2_gain_db: {(event_index, j): dB} changes
    oscillator 2's own partial energy inside that event only; osc1_gain_db
    {(event_index, k): dB} does the same for oscillator 1 (k=1 is the
    fundamental: the changing-fundamental case)."""
    n = round(seconds * SR)
    t = np.arange(n) / SR
    f1 = np.zeros(n)
    note = np.full(n, EVENTS[0]["note"])
    for e in EVENTS:
        note[round(e["on_s"] * SR):] = e["note"]
    f1 = 440 * 2 ** ((note - 69) / 12)
    drift = np.zeros(n) if locked else np.vectorize(drift_by_note.get)(note)
    ph1 = np.cumsum(f1) / SR
    # choose the constant so psi at the first event centre is psi_first
    ph2_raw = np.cumsum(2 * f1 + drift) / SR
    c = round(centre_s(EVENTS[0]) * SR)
    ph2 = ph2_raw - (ph2_raw[c] - 2 * ph1[c]) + psi_first_deg / 360
    gate = np.zeros(n)
    for e in EVENTS:
        gate[round(e["on_s"] * SR):round((e["on_s"] + e["gate_s"]) * SR)] = 1.
    filter_db = filter_db or {}
    osc1, osc2 = np.zeros(n), np.zeros(n)
    for k in range(1, KMAX + 1):
        g = np.full(n, 10 ** (filter_db.get(k, 0.) / 20))
        for (i, kk), change in (osc1_gain_db or {}).items():
            if kk == k:
                e = EVENTS[i]
                g[round(e["on_s"] * SR):round((e["on_s"] + e["gate_s"]) * SR)] *= 10 ** (change / 20)
        osc1 += g * .05 / k * np.sin(2 * np.pi * k * ph1)
    for j in range(1, JMAX + 1):
        g = np.full(n, 10 ** (filter_db.get(2 * j, 0.) / 20))
        for (i, jj), change in (osc2_gain_db or {}).items():
            if jj == j:
                e = EVENTS[i]
                g[round(e["on_s"] * SR):round((e["on_s"] + e["gate_s"]) * SR)] *= 10 ** (change / 20)
        osc2 += g * .05 * .535 / j * np.sin(2 * np.pi * j * ph2)
    return osc1 * gate, osc2 * gate


def _repeat_errors(model, reference):
    out = {}
    for k in range(2, KMAX + 1):
        key = f"h{k}"
        out[key] = [model[i]["ratio_db"][key] - reference[i]["ratio_db"][key] for i in REPEAT]
    return out


def _energy_change(ref_phase_rows):
    out = {}
    for k in range(2, KMAX + 1):
        a, b = ref_phase_rows[REPEAT[0]], ref_phase_rows[REPEAT[1]]
        # a ratio-to-fundamental moves when EITHER its partial or the
        # fundamental moves, so the fundamental's own change counts too
        change = max(abs(b["osc1_dbfs"][f"h{k}"] - a["osc1_dbfs"][f"h{k}"]),
                     abs(b["osc1_dbfs"]["h1"] - a["osc1_dbfs"]["h1"]))
        if k % 2 == 0:
            change = max(change, abs(b["osc2_dbfs_at_mix_partial"][f"h{k}"]
                                     - a["osc2_dbfs_at_mix_partial"][f"h{k}"]))
        out[f"h{k}"] = change
    return out


def separate(reference_mix, ref_osc1, ref_osc2, model_mix, model_at_reference_phase):
    """Run steps 1-3. Returns the per-harmonic classification and its inputs."""
    phase_rows = [relative_phase(ref_osc1, ref_osc2, e) for e in EVENTS]
    reference = [partials(reference_mix, e) for e in EVENTS]
    model = [partials(model_mix, e) for e in EVENTS]
    psi = [r["psi_deg"] for r in phase_rows]
    matched_signals = model_at_reference_phase(psi)
    matched = [partials(signal, e) for signal, e in zip(matched_signals, EVENTS)]
    scored = _repeat_errors(model, reference)
    residual = _repeat_errors(matched, reference)
    energy = _energy_change(phase_rows)
    where = [lives_in({k: m["absolute_dbfs"][k] - r["absolute_dbfs"][k] for k in m["absolute_dbfs"]})
             for m, r in zip(model, reference)]
    return {"classification": classify_table(scored, residual, energy), "lives_in": where,
            "scored": scored, "matched_residual": residual, "energy_change_db": energy,
            "phase_rows": phase_rows, "reference": reference, "model": model, "matched": matched}


def synthetic_control():
    """Known answers. Reference oscillators free-run with the measured drift;
    the 'model' is locked unless stated. Filter is a gentle known tilt."""
    drift = {36: -.2634, 43: -.3946}
    tilt = {k: -.6 * (k - 1) for k in range(1, KMAX + 1)}
    psi0 = 156.3

    def ref(**kw):
        return synth_phrase(psi0, drift, filter_db=tilt, **kw)

    def matched_from(filter_db):
        def at(psi_by_event):
            out = []
            for i, psi in enumerate(psi_by_event):
                # a model whose relative phase at event i equals the reference's
                shift = psi - free_running_prediction(psi0, drift)[i]
                o1, o2 = synth_phrase(wrap(psi0 + shift), drift, filter_db=filter_db)
                out.append(o1 + o2)
            return out
        return at

    darker = {**tilt, 5: tilt[5] - 6., 7: tilt[7] - 6.}
    cases = {}
    # A: persistent -- same phase trajectory, h5/h7 6 dB low in every instance
    r1, r2 = ref()
    m1, m2 = synth_phrase(psi0, drift, filter_db=darker)
    cases["persistent partial deficit"] = (
        separate(r1 + r2, r1, r2, m1 + m2, matched_from(darker)),
        {"h5": "persistent", "h7": "persistent", "h8": "within tolerance"})
    # B: interference -- identical partial energies, model locked in phase
    m1, m2 = synth_phrase(0., drift, filter_db=tilt, locked=True)
    cases["interference only"] = (
        separate(r1 + r2, r1, r2, m1 + m2, matched_from(tilt)),
        {"h5": "within tolerance", "h2": "phase-dependent", "h8": "phase-dependent"})
    # C: genuine amplitude change -- oscillator 2's partial 4 (mix h8) loses
    # 12 dB in the repeat only; the model has the same phase trajectory. The
    # mixed-signal h8 swing looks like B's; the contributor energy does not.
    c1, c2 = ref(osc2_gain_db={(2, 4): -12.})
    m1, m2 = synth_phrase(psi0, drift, filter_db=tilt)
    cases["genuine amplitude change"] = (
        separate(c1 + c2, c1, c2, m1 + m2, matched_from(tilt)),
        {"h8": "amplitude change", "h5": "within tolerance"})
    # D: changing fundamental -- oscillator 1's FUNDAMENTAL loses 3 dB in the
    # repeat; every other partial's absolute level is unchanged, yet every
    # ratio moves by 3 dB. Must be attributed to the fundamental.
    f1, f2 = ref(osc1_gain_db={(2, 1): -3.})
    cases["changing fundamental"] = (
        separate(f1 + f2, f1, f2, r1 + r2, matched_from(tilt)),
        {"h5": "amplitude change", "h8": "amplitude change"})
    where_expected = {"persistent partial deficit": (2, "h5", "partial"),
                      "interference only": (0, "h8", "partial"),
                      "genuine amplitude change": (2, "h8", "partial"),
                      "changing fundamental": (2, "h5", "fundamental")}
    report = {}
    for name, (result, expected) in cases.items():
        got = {k: result["classification"][k] for k in expected}
        i, key, place = where_expected[name]
        where = result["lives_in"][i][key]
        report[name] = {"expected": expected, "got": got, "ok": got == expected and where == place,
                        "lives_in_expected": {f"event {i} {key}": place},
                        "lives_in_got": {f"event {i} {key}": where},
                        "scored_h5": result["scored"]["h5"],
                        "scored_h8": result["scored"]["h8"],
                        "matched_residual_h8": result["matched_residual"]["h8"]}
    return report


# ------------------------------------------------------------- the model
def _patch(detune2=12.):
    manifest, _ = bass.load_reference()
    patch = bass.patch_for_reference(manifest)
    detune = list(patch["detune"])
    detune[1] = detune2
    return {**patch, "detune": tuple(detune)}


def render(patch, osc2_phase=0, mix=None):
    """`voice_fx.render_mono_fx`, inlined only so oscillator 2's accumulator
    can start at `osc2_phase` after reset. osc2_phase=0 must reproduce it."""
    vf = lead.vf
    patch = dict(patch) if mix is None else {**patch, "mix": mix}
    seq = [(e["on_s"], e["note"], e["gate_s"], {**patch, "gate": e["gate_s"]}) for e in EVENTS]
    voice = lead._voice_for_engine(lead.engine_configuration("selected"))
    n = int(bass.reference.SECONDS * SR)
    kw0 = dict(seq[0][3])
    kw0.pop("blep", None), kw0.pop("glide", None), kw0.pop("gate", None)
    regs = vf.VoiceFx.patch_regs(**kw0)
    host = vf.KeyHost(glide="off")
    events = []
    for start, note, d, kw in seq:
        on = int(start * SR)
        events += [(on, "on", note), (min(n - 1, on + max(1, int(kw["gate"] * SR))), "off", note)]
    voice.reset()
    voice.oscs[1].phase = int(osc2_phase) % CYCLE
    voice._os2_phase[1] = int(osc2_phase) % CYCLE
    return voice.play(regs, host.writes(events, regs), n)


def render_with_mixer_peak(patch, osc2_phase=0):
    """render(), also returning the largest |mixer sum| (Q1.15 units) -- the
    level the ladder's tanh input stage sees. Diagnostic spy only: the mixer
    function is wrapped for the call and restored."""
    vf, peaks = lead.vf, []
    real = vf.mix_fx

    def spy(signals, weights, q=15):
        acc = np.zeros_like(signals[0], dtype=np.int64)
        for sig, w in zip(signals, weights):
            acc += sig * int(w)
        peaks.append(int(np.max(np.abs(acc >> q))))
        return real(signals, weights, q)
    vf.mix_fx = spy
    try:
        pcm = render(patch, osc2_phase)
    finally:
        vf.mix_fx = real
    return pcm, max(peaks)


def pcm_sha(pcm):
    return hashlib.sha256(np.asarray(pcm).astype("<i2").tobytes()).hexdigest()


def model_psi(patch, osc2_phase, t_s):
    """Relative phase of the model's audible (2x) oscillators at t_s, from the
    integer accumulators: each advances (inc//2)*2 per base sample."""
    incs = {e["note"]: lead.vf.VoiceFx.note_incs(e["note"], patch["detune"]) for e in EVENTS}
    acc, first_on = 0, EVENTS[0]["on_s"]
    for i, e in enumerate(EVENTS):
        seg_end = EVENTS[i + 1]["on_s"] if i + 1 < len(EVENTS) else math.inf
        lo, hi = max(e["on_s"], first_on), min(seg_end, t_s)
        if hi <= lo:
            continue
        i1, i2 = incs[e["note"]][0] // 2 * 2, incs[e["note"]][1] // 2 * 2
        acc += (i2 - 2 * i1) * (hi - lo) * SR
    return wrap(360 * (osc2_phase + acc) / CYCLE)


def phase_for(patch, event, psi_target):
    base = model_psi(patch, 0, centre_s(event))
    return round((wrap(psi_target - base) % 360) / 360 * CYCLE) % CYCLE


def reference_audio():
    manifest, reference = bass.load_reference()
    controls = {}
    for name in ("osc1_open", "osc2_open"):
        path = bass.MANIFEST.parent / manifest["controls"][name]["file"]
        rate, audio = wavfile.read(path)
        if rate != SR or audio.dtype != np.float32:
            raise Refused(f"{name} format differs (rate {rate}, {audio.dtype})")
        controls[name] = audio.astype(np.float64)
    return manifest, reference.astype(np.float64), controls


def annotate(report):
    """Everything derived from measured numbers already in the report: the
    absolute-versus-ratio attribution, the repeat-pair classification and the
    known-answer control. Needs no render, so `--reanalyse` can refresh it."""
    for i, e in enumerate(EVENTS):
        cells = [c for c in report["table"] if c["on_s"] == e["on_s"]]
        where = lives_in({f"h{c['k']}": c["abs_model"] - c["abs_reference"] for c in cells})
        for c in cells:
            c["lives_in"] = where.get(f"h{c['k']}", "fundamental" if c["k"] == 1 else None)
    pair = report["repeat_pair"]
    pair["energy_change_db"] = _energy_change(report["reference_phase"]["rows"])
    pair["classification"] = classify_table(pair["scored"], pair["matched_residual"], pair["energy_change_db"])
    pair["label"] = {k: LABELS[v] for k, v in pair["classification"].items()}
    report["synthetic_control"] = synthetic_control()
    report["conventions"] = {
        "level": "20*log10 of the Blackman-Harris coherent-projection AMPLITUDE of one partial "
                 "(windowed_tone_amplitude, the scorer's primitive); a full-scale sine reads 0 dBFS",
        "ratio": "the scorer's harmonic_signature ratio, partial amplitude over fundamental amplitude, dB",
        "power_mean": "sweep averages are means of amplitude squared, reported on the same dB scale",
        "phase": "psi = oscillator-2 phase minus twice oscillator-1 phase, degrees of oscillator 2; "
                 "theta_j = j*psi at mix partial 2j; referenced to the window centre",
        "window": "on+0.30 s to on+0.55 s, the scorer's window; history: event 0 from reset, event 1 "
                  "MIDI 43 after MIDI 36 released, event 2 MIDI 36 after MIDI 43 released 1.4 s earlier"}
    return report


def main():
    if "--reanalyse" in sys.argv:
        report = annotate(json.loads((OUT / "report.json").read_text()))
        report["reanalysis_source_sha256"] = bass.sha(ROOT / "tools/diagnose_m1a_harmonic_phase.py")
        (OUT / "report.json").write_text(json.dumps(report, indent=1) + "\n")
        ok = all(v["ok"] for v in report["synthetic_control"].values())
        print(json.dumps({"classification": report["repeat_pair"]["classification"], "control_ok": ok}, indent=1))
        sys.exit(0 if ok else 1)
    manifest, ref, controls = reference_audio()     # float32 full scale 1.0
    n_ref = len(ref)
    record = json.loads(RECORD.read_text())
    selected, detuned = _patch(), _patch(json.loads((DETUNE_DIR / "detune-diagnostic.json")
                                                    .read_text())["patch"]["detune"][1])
    renders = {}

    def pcm_of(name, patch, osc2_phase=0, mix=None):
        pcm = render(patch, osc2_phase, mix)[:n_ref]
        renders[name] = {"osc2_phase": int(osc2_phase), "detune2": patch["detune"][1],
                         "mix": list(mix or patch["mix"]), "pcm_sha256": pcm_sha(pcm)}
        return pcm.astype(np.float64) / 32768

    # --- reproduction preconditions: refuse unless committed audio reproduces
    rate, committed = wavfile.read(MODEL_WAV)
    if rate != SR or committed.dtype != np.int16:
        raise Refused("committed model audio format differs")
    model = pcm_of("selected", selected)
    if renders["selected"]["pcm_sha256"] != pcm_sha(committed):
        raise Refused("selected-patch render does not reproduce the committed model audio")
    rate, committed_detune = wavfile.read(DETUNE_DIR / "detune-diagnostic.wav")
    pcm_of("detuned", detuned)
    if renders["detuned"]["pcm_sha256"] != pcm_sha(committed_detune[:n_ref]):
        raise Refused("detune-diagnostic render does not reproduce its committed audio")
    record_scored = {i: record["diagnostics"]["events"][i]["harmonic_error_db_model_minus_reference"]
                     for i in range(3)}

    # --- step 1: reference phase behaviour from the isolated controls
    phase_rows = [relative_phase(controls["osc1_open"], controls["osc2_open"], e) for e in EVENTS]
    psi_ref = [r["psi_deg"] for r in phase_rows]
    drift = {e["note"]: r["drift_hz"] for e, r in zip(EVENTS, phase_rows)}
    free = free_running_prediction(psi_ref[0], drift)
    reset = reset_prediction(psi_ref)

    # --- the model's own phase state: integers, checked by isolated renders
    # Isolated model oscillators are measured with the filter OPEN, as the
    # reference controls were. Through the patch's 1056 Hz ladder the two
    # isolated phases scatter by up to 14.6 degrees from j*psi (the ladder's
    # phase is level-dependent), and fit_psi refuses them -- recorded as
    # wrong-then-right, and as evidence that the model filter is nonlinear here.
    m = selected["mix"][1]
    open_filter = lambda patch: {**patch, "cutoff": (20000, 20000)}
    model_iso = [relative_phase(pcm_of("selected osc1 only, open", open_filter(selected), 0, (1., 0., 0.)),
                                pcm_of("selected osc2 only, open", open_filter(selected), 0, (0., m, 0.)), e)
                 for e in EVENTS]
    model_psi_int = [model_psi(selected, 0, centre_s(e)) for e in EVENTS]

    # --- step 2: phase-matched model (detuned so in-window rotation matches too)
    matched, matched_check = [], []
    for i, e in enumerate(EVENTS):
        p = phase_for(detuned, e, psi_ref[i])
        mix = pcm_of(f"matched event {i}", detuned, p)
        iso = relative_phase(pcm_of(f"matched event {i} osc1 only, open", open_filter(detuned), p, (1., 0., 0.)),
                             pcm_of(f"matched event {i} osc2 only, open", open_filter(detuned), p, (0., m, 0.)), e)
        matched.append(partials(mix, e))
        matched_check.append({"target_psi_deg": psi_ref[i], "measured_psi_deg": iso["psi_deg"],
                              "error_deg": wrap(iso["psi_deg"] - psi_ref[i])})
        if abs(wrap(iso["psi_deg"] - psi_ref[i])) > 5.:
            raise Refused(f"matched render missed the reference phase at event {i}")

    # --- static-phase sweep of the selected model (sensitivity; phase average)
    sweep = []
    for s in range(SWEEP):
        p = s * CYCLE // SWEEP
        pcm, peak = render_with_mixer_peak(selected, p)
        pcm = pcm[:n_ref]
        renders[f"sweep {s}"] = {"osc2_phase": p, "detune2": selected["detune"][1],
                                 "mix": list(selected["mix"]), "pcm_sha256": pcm_sha(pcm)}
        sweep.append({"osc2_phase": p, "psi_deg": [model_psi(selected, p, centre_s(e)) for e in EVENTS],
                      "mixer_peak_q15": peak,
                      "events": [partials(pcm.astype(np.float64) / 32768, e) for e in EVENTS]})

    reference = [partials(ref, e) for e in EVENTS]
    model_rows = [partials(model, e) for e in EVENTS]
    # the scored ratios must be exactly the record's
    for i in range(3):
        for k, v in record_scored[i].items():
            mine = model_rows[i]["ratio_db"][k] - reference[i]["ratio_db"][k]
            if abs(mine - v) > 1e-6:
                raise Refused(f"scored error does not reproduce the record at event {i} {k}")

    table = []
    for i, e in enumerate(EVENTS):
        for k in range(1, KMAX + 1):
            key = f"h{k}"
            levels = [row["events"][i]["absolute_dbfs"][key] for row in sweep]
            ratios = ([row["events"][i]["ratio_db"][key] for row in sweep] if k > 1 else [0.])
            power_avg = lambda xs: 10 * math.log10(np.mean([10 ** (x / 10) for x in xs]))
            cell = {"note": e["note"], "on_s": e["on_s"], "k": k,
                    "abs_model": model_rows[i]["absolute_dbfs"][key],
                    "abs_reference": reference[i]["absolute_dbfs"][key],
                    "abs_matched": matched[i]["absolute_dbfs"][key],
                    "ratio_model": model_rows[i]["ratio_db"].get(key, 0.),
                    "ratio_reference": reference[i]["ratio_db"].get(key, 0.),
                    "ratio_matched": matched[i]["ratio_db"].get(key, 0.),
                    "model_sweep_ratio_range_db": [min(ratios), max(ratios)],
                    "model_sweep_ratio_power_mean_db": power_avg(ratios) if k > 1 else 0.,
                    "model_sweep_abs_range_db": [min(levels), max(levels)]}
            if k > 1:
                cell["scored_error"] = cell["ratio_model"] - cell["ratio_reference"]
                cell["matched_residual"] = cell["ratio_matched"] - cell["ratio_reference"]
                cell["phase_term"] = cell["scored_error"] - cell["matched_residual"]
            cell["abs_error"] = cell["abs_model"] - cell["abs_reference"]
            table.append(cell)
    scored = _repeat_errors(model_rows, reference)
    residual = _repeat_errors(matched, reference)
    energy = _energy_change(phase_rows)
    classes = classify_table(scored, residual, energy)
    control = synthetic_control()
    report = {
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "source_sha256": {f: bass.sha(ROOT / f) for f in (
            "tools/diagnose_m1a_harmonic_phase.py", "tools/mono_m1a_score.py", "model/audio_measure.py",
            "model/voice_fx.py")},
        "inputs_sha256": {"manifest": bass.sha(bass.MANIFEST), "model_wav": bass.sha(MODEL_WAV),
                          "record": bass.sha(RECORD),
                          "detune_wav": bass.sha(DETUNE_DIR / "detune-diagnostic.wav"),
                          **{n: bass.sha(bass.MANIFEST.parent / manifest["controls"][n]["file"])
                             for n in ("osc1_open", "osc2_open")}},
        "reproduction": {"selected_pcm_matches_committed": True, "detuned_pcm_matches_committed": True,
                         "scored_errors_match_record": True},
        "renders": renders,
        "reference_phase": {"rows": phase_rows, "psi_deg": psi_ref,
                            "free_running_prediction_deg": free,
                            "free_running_error_deg": [wrap(a - b) for a, b in zip(psi_ref, free)],
                            "reset_prediction_deg": reset,
                            "reset_error_deg": [wrap(a - b) for a, b in zip(psi_ref, reset)]},
        "model_phase": {"integer_psi_deg": model_psi_int,
                        "isolated_render_psi_deg": [r["psi_deg"] for r in model_iso],
                        "isolated_rows": model_iso,
                        "increments": {e["note"]: lead.vf.VoiceFx.note_incs(e["note"], selected["detune"])
                                       for e in EVENTS[:2]}},
        "matched_phase_check": matched_check,
        "table": table,
        "repeat_pair": {"scored": scored, "matched_residual": residual,
                        "energy_change_db": energy, "classification": classes},
        "synthetic_control": control,
        "model_phase_sweep": [{"osc2_phase": row["osc2_phase"], "psi_deg": row["psi_deg"],
                               "mixer_peak_q15": row["mixer_peak_q15"],
                               "ratio_db": [ev["ratio_db"] for ev in row["events"]],
                               "absolute_dbfs": [ev["absolute_dbfs"] for ev in row["events"]]}
                              for row in sweep],
        "scope": "diagnosis only; no patch, engine, reference, estimator or scorer change. "
                 "Renders are diagnostic (oscillator-2 starting phase only) and each is hash-recorded.",
    }
    report = annotate(report)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps({"classification": classes, "control_ok": {k: v["ok"] for k, v in control.items()},
                      "free_running_error_deg": report["reference_phase"]["free_running_error_deg"],
                      "reset_error_deg": report["reference_phase"]["reset_error_deg"],
                      "model_psi": model_psi_int,
                      "model_iso_psi": report["model_phase"]["isolated_render_psi_deg"],
                      "matched_check": matched_check}, indent=1))
    if not all(v["ok"] for v in control.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
