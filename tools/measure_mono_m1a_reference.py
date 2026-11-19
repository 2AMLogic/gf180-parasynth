#!/usr/bin/env python3
"""Freeze one measured Mini V3 round-bass patch for M1A; no model verdict."""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
import subprocess
import sys
import numpy as np
from scipy.io import wavfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import measure_mono_m5a_reference as ref

EVENTS = ({"note": 36, "on_s": .1, "gate_s": .6},
          {"note": 43, "on_s": 2.1, "gate_s": .6},
          {"note": 36, "on_s": 4.1, "gate_s": .6})
SECONDS = 7.5
# At MIDI 36 the lead's 5 ms RMS window spans only 0.33 cycles and crosses
# release thresholds on carrier troughs. Qualify the bass basis separately.
ENVELOPE_WINDOW_MS = 40.0
PATCH = {
    "osc1_level": (15, "Level Osc1", .70),
    "osc2_level": (16, "Level Osc2", .30),
    "osc2_enable": (73, "Osc2", 1.0),
    "osc2_range": (46, "Range Osc2", .7417),
    "osc2_wave": (49, "Wave Osc2", .4083),
    "filter_cutoff": (23, "CutOff", .50),
    "filter_emphasis": (24, "Emphasis", .05),
    "filter_contour": (25, "Amount", .30),
    "filter_attack": (26, "VCF Attack", .005),
    "filter_decay": (27, "VCF Decay", .15),
    "filter_sustain": (28, "VCF Sustain", .0),
    "amp_attack": (29, "VCA Attack", .02),
    "amp_decay": (30, "VCA Decay", .25),
    "amp_sustain": (31, "VCA Sustain", .75),
}


def render(overrides=None, events=EVENTS, seconds=SECONDS):
    rig = ref.rr.MiniV3Rig(block=ref.BLOCK)
    ref._apply_patch(rig, "saw", decay=ref.PATCH["amp_decay"][2])
    settings = {**ref.PATCH, **PATCH}
    for key, value in (overrides or {}).items():
        i, name, _ = settings[key]; settings[key] = (i, name, value)
    for i, _, value in settings.values():
        rig.set(i, value)
    rig.pb.set_data(np.zeros((2, int(.05 * ref.SR)), dtype=np.float32))
    rig.p.clear_midi(); rig.eng.render(.05)

    def readbacks():
        result = {}
        for key, (i, name, value) in settings.items():
            expected = {"osc1_range": "8'", "osc2_range": "4'", "osc2_wave": "sawtooth"}.get(key, value)
            result[key] = ref._readback(rig.p, i, name, expected)
        bad = rig.check_pins()
        if bad:
            raise ref.Refused(f"reference pins changed: {bad}")
        return result

    before = readbacks()
    rig.pb.set_data(np.zeros((2, int(seconds * ref.SR)), dtype=np.float32))
    rig.p.clear_midi()
    for e in events:
        rig.p.add_midi_note(e["note"], ref.VELOCITY, e["on_s"], e["gate_s"])
    rig.eng.render(seconds)
    audio = np.asarray(rig.eng.get_audio()[0], dtype=np.float64)
    latency = int(rig.p.get_latency_samples())
    audio = audio[latency:] if latency else audio
    if len(audio) < int((seconds - .01) * ref.SR) or not np.isfinite(audio).all():
        raise ref.Refused("truncated or non-finite reference")
    peak = float(np.max(np.abs(audio)))
    if not 1e-5 < peak < .999:
        raise ref.Refused(f"silent or clipped reference: peak {peak}")
    return audio, {"before": before, "after": readbacks(), "latency_removed": latency,
                   "peak": peak, "settings": settings}


def event_measurements(audio):
    envelope = ref.am.rms_envelope(audio, ms=ENVELOPE_WINDOW_MS, sr=ref.SR)
    rows = []
    for e in EVENTS:
        hz = ref.vf.note_hz(e["note"])
        steady = audio[round((e["on_s"] + .3) * ref.SR):round((e["on_s"] + .55) * ref.SR)]
        pitch = ref.am.refine_f0(steady, hz, ref.SR)
        if not pitch.ok or abs(1200 * math.log2(pitch.value / hz)) > 5:
            raise ref.Refused(f"bass pitch did not follow MIDI {e['note']}")
        env = ref.envelope_timing(envelope, ref.SR, e["on_s"], e["on_s"] + e["gate_s"])
        if not env.get("valid") or not env.get("release_complete_40db"):
            raise ref.Refused(f"incomplete bass envelope: {env}")
        attack = attack_fit(audio, ref.SR, pitch.value, e["on_s"], e["on_s"] + e["gate_s"])
        env.update(attack_valid=attack["valid"], release_valid=True,
                   attack=attack, valid=True,
                   why="attack by waveform-domain fit (qualified against known "
                       "signals); release by the 40 ms RMS window")
        shape = ref.am.harmonic_signature(steady, ref.SR, f0=pitch.value, kmax=12)
        trajectory = []
        for offset in (.02, .16, .30):
            clip = audio[round((e["on_s"] + offset) * ref.SR):round((e["on_s"] + offset + .25) * ref.SR)]
            sig = ref.am.harmonic_signature(clip, ref.SR, f0=pitch.value, kmax=4)
            trajectory.append({"offset_s": offset, "h4_db": sig.get("h4")})
        rows.append({**e, "f0_hz": pitch.value, "pitch_cents": 1200 * math.log2(pitch.value / hz),
                     "harmonics": shape, "envelope": env,
                     "rms_dbfs": 20 * math.log10(ref.am.rms(steady)),
                     "h4_trajectory": trajectory})
    return rows



def saw_bass(t, hz, lp_hz=1056.0, nharm=48):
    """Band-limited saw through a 12 dB/oct low-pass: the known-signal carrier.

    This builds TEST SIGNALS only; it is not a model of either synthesizer."""
    x = np.zeros_like(t)
    k = 1
    while k * hz < min(ref.SR / 2, nharm * hz):
        r = k * hz / lp_hz
        x += np.sin(2 * np.pi * k * hz * t) / (k * (1 + r * r))
        k += 1
    return x


def fold_waveform(audio, sr, f0, a, b, min_folds=3, upsample=8):
    """One carrier period of the steady waveform, energy-normalized, at 8x
    upsampling.

    Folding at the INTEGER sample period near 1/f0 misaligns successive rows
    by the fractional remainder (0.19 samples/cycle at MIDI 43), and the row
    mean smears the template enough to bias the fit by half a millisecond
    (recorded in wrong-then-right). Upsampling the steady segment first
    shrinks the remainder 8x, to under 0.01 original samples."""
    n0_true = sr / f0
    n0_up = max(2, round(n0_true * upsample))
    seg = np.asarray(audio[a:b], dtype=np.float64)
    if len(seg) < min_folds * n0_true:
        raise ref.Refused(f"steady region too short to fold: "
                          f"{len(seg) / n0_true:.1f} carrier periods")
    from scipy.signal import resample_poly
    seg_up = resample_poly(seg, upsample, 1)
    nfold = len(seg_up) // n0_up
    s_up = seg_up[:nfold * n0_up].reshape(nfold, n0_up).mean(axis=0)
    energy = math.sqrt(float(np.sum(s_up * s_up)))
    if not math.isfinite(energy) or energy <= 0:
        raise ref.Refused("empty or non-finite waveform fold")
    return s_up / energy, n0_up, upsample


def aligned_template(s_up, n0_up, upsample, a_fold, start, nfit):
    """The fold template sampled at absolute time: s[(t - a_fold) mod 1/f0]
    for t = start .. start+nfit, linearly interpolated on the upsampled grid.
    The template is phase-locked to ABSOLUTE sample time: fold index k is the
    waveform at (t - a_fold) mod 1/f0. Getting this sign wrong twice is
    recorded in the session's wrong-then-right list."""
    t_up = (np.arange(nfit) + start - a_fold) * upsample
    k = np.mod(t_up, n0_up)
    lo = np.floor(k).astype(int)
    frac = k - lo
    return s_up[lo] * (1 - frac) + s_up[(lo + 1) % n0_up] * frac


#: The waveform-domain fit models the whole analysis window, so a wrong fit
#: cannot hide by explaining only the sustain. Below this fraction of window
#: energy explained, the periodic-carrier model does not describe the signal
#: (noise, drums, heavy modulation) and there is no attack estimate. Known
#: signals fit at >= 0.99; frozen reference and model audio measured
#: 0.79-0.98, so the threshold sits well under the worst legitimate case.
MIN_EXPLAINED_RATIO = 0.6


def _sliding_dot(y, w):
    """Valid-mode correlation of y with w, FFT-based (the T loop dominates
    the fit's cost; a direct convolution per candidate is ~10x slower).

    (y ⋆ w)[j] = sum_k y[k+j] w[k]; zero-padded to N >= len(y)+len(w) so the
    non-negative lags are wrap-free, valid output is full[0:len(y)-len(w)+1].
    The first version read the wrong offset and the known-signal suite caught
    it on the canonical 8 ms case (wrong-then-right)."""
    from numpy.fft import rfft, irfft
    n = 1 << int(math.ceil(math.log2(len(y) + len(w))))
    full = irfft(rfft(y, n) * np.conj(rfft(w, n)), n)
    return full[:len(y) - len(w) + 1]


def attack_fit(audio, sr, f0, on_s, off_s, *, fold_ms=280.0, guard_ms=20.0,
               fit_ms=150.0, pre_ms=2.0, t_max_ms=25.0, step_ms=0.25):
    """10-90% attack from a waveform-domain fit; resolves fast attacks on
    low carriers where no RMS window can.

    Why this shape: an 8 ms attack needs ~125 Hz of envelope bandwidth, which
    overlaps the 65 Hz harmonic spacing of the bass -- envelope and carrier
    ripple are entangled in ANY pointwise envelope (analytic included). But
    during the attack the signal is the steady waveform scaled by the
    envelope, so the fit can use the full bandwidth: fold the steady gate
    into a one-period template s, then least-squares
        x(t) = A * s_aligned(t) * (0 before t0; ((t-t0)/T)^p inside; 1 after)
    over the window. A short-T-in-sustain solution must leave the true
    transition unexplained, so the full-window residual cannot prefer it.

    The ramp length T is refined to single samples around the coarse winner:
    a 0.25 ms grid alone quantized T by 12 samples and cost up to 1.9 ms of
    span error on quadratic shapes (recorded in wrong-then-right).

    Known-signal qualification: spans 0.5-20 ms, shapes p in {0.5, 1, 2},
    MIDI 36/43, four carrier phases, dull/bright carriers and a bright
    attack transient. Demonstrated worst error 0.50 ms over 56 cases; the
    qualification refuses at 1.0 ms (see qualify_attack_basis)."""
    audio = np.asarray(audio, dtype=np.float64)
    if not np.isfinite(audio).all() or ref.am.is_silent(audio):
        raise ref.Refused("attack fit input is silent or non-finite")
    on, off = round(on_s * sr), round(off_s * sr)
    start = on - round(pre_ms * 1e-3 * sr)
    nfit = round(fit_ms * 1e-3 * sr)
    b_fold = off - round(guard_ms * 1e-3 * sr)
    if start < 0 or start + nfit > len(audio) or b_fold > len(audio):
        raise ref.Refused("attack fit window truncates the audio")
    a_fold = off - round((fold_ms + guard_ms) * 1e-3 * sr)
    s_up, n0_up, upsample = fold_waveform(audio, sr, f0, a_fold, b_fold)
    s_aligned = aligned_template(s_up, n0_up, upsample, a_fold, start, nfit)
    window = audio[start:start + nfit]
    y = window * s_aligned
    y2 = s_aligned * s_aligned
    x2 = float(np.sum(window * window))
    if x2 <= 0:
        raise ref.Refused("attack fit window is silent")
    suffix_y = np.concatenate(([0.], np.cumsum(y[::-1])))[::-1]
    suffix_y2 = np.concatenate(([0.], np.cumsum(y2[::-1])))[::-1]

    def best_over(p, n_spans):
        rows = []
        for n_span in n_spans:
            u = np.arange(n_span) / n_span
            ramp = u ** p
            bw = _sliding_dot(y, ramp)
            bw2 = _sliding_dot(y2, ramp * ramp)
            num = bw + suffix_y[np.arange(len(bw)) + n_span]
            den = bw2 + suffix_y2[np.arange(len(bw2)) + n_span]
            resid = x2 - num ** 2 / np.maximum(den, 1e-30)
            i = int(np.argmin(resid))
            rows.append((float(resid[i]), n_span, i))
        return min(rows)

    coarse = np.unique(np.maximum(2, np.round(
        np.arange(1.0, t_max_ms + step_ms, step_ms) * 1e-3 * sr))).astype(int)
    # Refine EVERY shape: a linear ramp is close enough to quadratic that the
    # coarse stage can hand the wrong p to a p-only refinement (recorded in
    # wrong-then-right as a -0.55 ms bias on MIDI 43 cases).
    best = (math.inf, None)
    for p in (0.5, 1.0, 2.0, 3.0, 4.0):
        resid, n_coarse, _ = best_over(p, coarse)
        fine = np.unique(np.maximum(2, np.arange(n_coarse - 16, n_coarse + 17))).astype(int)
        resid_f, n_span, i = best_over(p, fine)
        if resid_f < best[0]:
            best = (resid_f, (p, n_span, i))
    resid_best, (p_best, n_span, i) = best
    kfrac = 0.9 ** (1 / p_best) - 0.1 ** (1 / p_best)
    explained = 1.0 - resid_best / x2
    if not math.isfinite(explained) or explained < MIN_EXPLAINED_RATIO:
        raise ref.Refused(
            f"waveform-fit attack model explains only {explained:.2f} of the "
            f"window; the periodic-carrier model does not describe this audio "
            f"(threshold {MIN_EXPLAINED_RATIO})")
    return {"t0_ms": i * 1000 / sr - pre_ms, "ramp_ms": n_span * 1000 / sr,
            "shape_p": p_best, "attack_10_90_ms": n_span * kfrac * 1000 / sr,
            "explained_ratio": round(explained, 4), "valid": True}


def qualify_attack_basis():
    """Closed-form ground truth for the attack estimator, plus the control
    that keeps the rejected RMS-window basis red.

    Every signal here has an exactly known 10-90% attack span by
    construction -- independent of both synthesizers. The estimator may not
    gate a scorecard case unless this suite passes (docs/failure-modes.md)."""
    cases = []
    for note in (36, 43):
        hz = 440 * 2 ** ((note - 69) / 12)
        for phase in (0., .25, .5, .75):
            cases.append((note, hz, phase, 8., 1.0, 1056., 0.))     # canonical
        for phase in (0., .5):
            for span in (0.5, 1., 2., 4., 16., 20.):                # span sweep
                cases.append((note, hz, phase, span, 1.0, 1056., 0.))
            for shape in (0.5, 2.0):                                # shape sweep
                cases.append((note, hz, phase, 8., shape, 1056., 0.))
            for lp in (400., 3000.):                                # spectral
                cases.append((note, hz, phase, 8., 1.0, lp, 0.))
            for bright in (0.3, 0.6):                               # fenv stand-in
                cases.append((note, hz, phase, 8., 1.0, 1056., bright))

    def render_known(note, hz, phase, span_ms, shape, lp, bright):
        sr = ref.SR
        t = np.arange(round(1.8 * sr)) / sr
        on = .1
        kfrac = 0.9 ** (1 / shape) - 0.1 ** (1 / shape)
        env = np.clip((t - on) / ((span_ms / 1000.) / kfrac), 0, 1) ** shape
        off = on + .6
        rel = t >= off
        env[rel] = np.exp(-(t[rel] - off) / .1)
        car = saw_bass(t - phase / hz, hz, lp) + .535 * saw_bass(t - phase / hz, 2 * hz, lp)
        if bright:
            car = car + bright * np.sin(2 * np.pi * 20 * hz * t) * (t >= on) \
                * np.exp(-np.maximum(t - on, 0) / .010)
        return env * car

    observations = []
    for note, hz, phase, span_ms, shape, lp, bright in cases:
        audio = render_known(note, hz, phase, span_ms, shape, lp, bright)
        row = attack_fit(audio, ref.SR, hz, .1, .7)
        observations.append({"midi": note, "phase_cycles": phase,
                             "known_10_90_ms": span_ms, "shape_p": shape,
                             "carrier_lp_hz": lp, "bright_transient": bright,
                             "measured_ms": row["attack_10_90_ms"],
                             "error_ms": row["attack_10_90_ms"] - span_ms,
                             "explained_ratio": row["explained_ratio"]})
    max_known_error = max(abs(o["error_ms"]) for o in observations)
    # The gate sits at twice the demonstrated worst error (0.50 ms across the
    # 56 cases below, concentrated at MIDI 43 phase 0.5): above legitimate
    # variance, far below any broken estimator, fixed before model comparison.
    if max_known_error >= 1.0:
        raise ref.Refused(
            f"attack estimator failed its known-signal suite: worst error "
            f"{max_known_error:.2f} ms over {len(observations)} cases")

    # The rejected basis must stay red on the same canonical signals.
    window_rows = []
    for note, hz, phase, *_ in (c for c in cases if c[3] == 8. and c[4] == 1.0
                                and c[5] == 1056. and c[6] == 0.):
        audio = render_known(note, hz, phase, 8., 1.0, 1056., 0.)
        env = ref.am.rms_envelope(audio, ms=ENVELOPE_WINDOW_MS, sr=ref.SR)
        timing = ref.envelope_timing(env, ref.SR, .1, .7)
        if not timing.get("valid"):
            raise ref.Refused("40 ms window control produced no attack measurement")
        window_rows.append({"midi": note, "phase_cycles": phase,
                            "observed_ms": timing["attack_10_90_ms"],
                            "error_ms": timing["attack_10_90_ms"] - 8.})
    min_window_error = min(abs(r["error_ms"]) for r in window_rows)
    if min_window_error <= 5.0:
        raise ref.Refused(
            "the 40 ms RMS window control no longer fails the known attack; "
            "it cannot catch the defect it exists for")
    lead_rows = []
    for note, hz, phase, *_ in (c for c in cases if c[3] == 8. and c[4] == 1.0):
        audio = render_known(note, hz, phase, 8., 1.0, 1056., 0.)
        env = ref.am.rms_envelope(audio, ms=5.0, sr=ref.SR)
        timing = ref.envelope_timing(env, ref.SR, .1, .7)
        lead_rows.append({"midi": note, "phase_cycles": phase,
                          "observed_ms": timing.get("attack_10_90_ms"),
                          "error_ms": (timing.get("attack_10_90_ms") - 8.)
                          if timing.get("valid") else None})
    lead_errors = [abs(r["error_ms"]) for r in lead_rows if r["error_ms"] is not None]
    if lead_errors and max(lead_errors) <= 5.0:
        raise ref.Refused(
            "the 5 ms lead window measures the known bass attack within 5 ms "
            "on every phase; the two-window control premise is stale")
    return {"valid": True,
            "estimator": "waveform-domain fit: steady-period fold template, "
                         "piecewise power-ramp least squares over the note window",
            "known_signal_count": len(observations),
            "max_known_error_ms": round(max_known_error, 3),
            "min_explained_ratio": min(o["explained_ratio"] for o in observations),
            "observations": observations,
            "window_control": {"window_ms": ENVELOPE_WINDOW_MS,
                               "min_error_ms": round(min_window_error, 2),
                               "observations": window_rows,
                               "why": "the basis that made attack unqualified; "
                                      "must stay red on these signals"},
            "lead_window_observations": lead_rows,
            "refusals": ["silent or non-finite input",
                         "steady region under 3 carrier periods",
                         "fit window past end of audio",
                         f"explained energy under {MIN_EXPLAINED_RATIO}"],
            "scope": "10-90% span of the audio attack, both sides measured by "
                     "the same fit; not an internal VCA-time claim"}


def qualify_envelope_basis():
    """Independent mathematical release; retain the failed lead-window control."""
    t = np.arange(round(SECONDS * ref.SR)) / ref.SR
    audio = np.zeros_like(t)
    for event in EVENTS:
        dt = t - event["on_s"]
        envelope = np.clip(dt / .03, 0, 1)
        off = dt >= event["gate_s"]
        envelope[off] = np.exp(-(dt[off] - event["gate_s"]) / .1)
        hz = 440 * 2 ** ((event["note"] - 69) / 12)
        audio += .1 * envelope * np.sin(2 * np.pi * hz * t)
    rows = {}
    expected = 100 * math.log(10)
    for label, window in (("lead_window", 5.), ("bass_window", ENVELOPE_WINDOW_MS)):
        measured = ref.am.rms_envelope(audio, ms=window, sr=ref.SR)
        values = [ref.envelope_timing(measured, ref.SR, e["on_s"], e["on_s"] + e["gate_s"])["release_t20_ms"] for e in EVENTS]
        rows[label] = {"window_ms": window, "release_ms": values,
                       "max_error_ms": max(abs(v - expected) for v in values)}
    if rows["bass_window"]["max_error_ms"] >= 10 or rows["lead_window"]["max_error_ms"] <= 10:
        raise ref.Refused("bass envelope qualification or wrong-window control failed")
    return {"known_release_ms": expected, **rows, "spectral_window_s": .25,
            "scope": "40 ms RMS window qualified for release ONLY; attack is "
                     "measured by the waveform-domain fit, not by any RMS window",
            "attack": qualify_attack_basis(),
            "initial_failures": "100 ms spectral window refused below 12 periods; 5 ms envelope window failed known release; early h4 window could not resolve the short filter transient"}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=ROOT / "docs/scorecard/mono-m1a-miniv3")
    a = ap.parse_args(argv); a.out.mkdir(parents=True, exist_ok=True)
    try:
        if subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip():
            raise ref.Refused("commit instrument before capture")
        provenance = ref._source_provenance()
        provenance["files_sha256"]["tools/measure_mono_m1a_reference.py"] = ref.sha256(Path(__file__))
        qualification = qualify_envelope_basis()
        identity = ref._plugin_metadata()
        integrity = ref._qualify_reference_integrity()
        renders = []
        for repeat in range(3):
            audio, apparatus = render()
            rows = event_measurements(audio)
            path = a.out / f"m1a-repeat-{repeat}.wav"
            wavfile.write(path, ref.SR, np.asarray(audio, dtype=np.float32))
            renders.append({"file": path.name, "sha256": ref.sha256(path),
                            "apparatus": apparatus, "events": rows})
            print(f"M1A repeat {repeat}: measured three bass notes", flush=True)
        controls = {}
        for label, overrides, note in (
            ("osc1_open", {"osc2_level": 0., "filter_cutoff": 1., "filter_contour": 0.}, 36),
            ("osc2_open", {"osc1_level": 0., "filter_cutoff": 1., "filter_contour": 0.}, 48)):
            audio, apparatus = render(overrides)
            steady = audio[int(.3 * ref.SR):int(.6 * ref.SR)]
            measured = ref.rv.measure(steady, ref.vf.note_hz(note), label, "saw")
            if not measured.get("verified") or not measured.get("steady"):
                raise ref.Refused(f"{label} oscillator/pitch qualification failed: {measured}")
            path = a.out / f"control-{label}.wav"
            wavfile.write(path, ref.SR, np.asarray(audio, dtype=np.float32))
            controls[label] = {"file": path.name, "sha256": ref.sha256(path), "measurement": measured, "rms_dbfs": 20 * math.log10(ref.am.rms(steady)),
                               "apparatus": apparatus}
        if controls["osc2_open"]["rms_dbfs"] >= controls["osc1_open"]["rms_dbfs"] - 3:
            raise ref.Refused("octave oscillator is not measurably quieter")
        audio, apparatus = render({"filter_contour": 0.})
        controls["filter_envelope_disabled"] = {"events": event_measurements(audio), "apparatus": apparatus}
        path = a.out / "control-filter-envelope-disabled.wav"
        wavfile.write(path, ref.SR, np.asarray(audio, dtype=np.float32))
        disabled = controls["filter_envelope_disabled"]
        disabled.update(file=path.name, sha256=ref.sha256(path))
        deltas = []
        for event in range(len(EVENTS)):
            values = [r["events"][event]["h4_trajectory"][0]["h4_db"] for r in renders]
            off = disabled["events"][event]["h4_trajectory"][0]["h4_db"]
            if off is None or any(value is None for value in values):
                raise ref.Refused("filter-envelope control has an unmeasurable fourth partial")
            deltas.append({"midi": EVENTS[event]["note"], "baseline_median_db": float(np.median(values)),
                           "disabled_db": off, "difference_db": float(np.median(values) - off),
                           "repeat_range_db": float(np.ptp(values))})
        disabled["early_h4_comparison"] = deltas
        # A 250 ms harmonic window cannot resolve a very short filter attack.
        # Use the causal sample difference, with independently repeated clean
        # audio as its noise floor, and keep the insensitive spectral result.
        baseline = wavfile.read(a.out / renders[0]["file"])[1].astype(np.float64)
        comparison = np.asarray(audio, dtype=np.float32).astype(np.float64)
        repeat_peak = max(float(np.max(np.abs(baseline - wavfile.read(a.out / r["file"])[1]))) for r in renders)
        threshold = max(1e-5, 100 * repeat_peak)
        responses = []
        for event in EVENTS:
            start = round(event["on_s"] * ref.SR)
            end = round((event["on_s"] + event["gate_s"]) * ref.SR)
            difference = np.abs(baseline[start:end] - comparison[start:end])
            changed = np.flatnonzero(difference > threshold)
            if not len(changed):
                raise ref.Refused("disabled filter envelope caused no change above repeated-render variation")
            duration = float(changed[-1] * 1000 / ref.SR)
            if duration > 250:
                raise ref.Refused("filter-envelope audio effect did not settle within 250 ms")
            responses.append({"midi": event["note"], "peak_difference": float(difference.max()),
                              "last_difference_ms": duration, "threshold": threshold})
        disabled["audio_response"] = {"events": responses, "repeat_peak_difference": repeat_peak,
                                      "scope": "duration of audible-output difference, not internal cutoff-envelope timing"}
        rings = []
        for _ in range(3):
            rig = ref.rr.MiniV3Rig(block=ref.BLOCK)
            y = rig.ring(PATCH["filter_cutoff"][2], .98, seconds=1.2)
            rings.append(ref.am.dominant_frequency(y, 25, 15000, ref.SR).require("bass cutoff ring"))
        if np.ptp(rings) > 10:
            raise ref.Refused("bass cutoff ring is not repeatable")
        report = {"schema": "mono-m1a-reference-v1", "case_id": "M1A", "valid": True,
                  "scope": "frozen Mini V3 reference; no model comparison or Model D cross-check yet",
                  "identity": identity, "integrity": integrity, "provenance": provenance,
                  "sample_rate_hz": ref.SR, "block_size_samples": ref.BLOCK,
                  "envelope_window_ms": ENVELOPE_WINDOW_MS,
                  "measurement_qualification": qualification,
                  "midi_velocity": ref.VELOCITY, "events": EVENTS, "duration_s": SECONDS,
                  "filter_rest_ring_hz": rings, "renders": renders, "controls": controls,
                  "wrong_then_right": {"corrected_measurements": 0, "phrase_repeats": 3}}
        (a.out / "manifest.json").write_text(json.dumps(report, indent=2) + "\n")
        print("M1A reference captured; scorecard coverage unchanged")
        return 0
    except (ref.Refused, OSError, ValueError, RuntimeError) as exc:
        (a.out / "refused.json").write_text(json.dumps({"state": "REFUSED", "reason": str(exc)}, indent=2))
        print(f"REFUSED: {exc}"); return 2


if __name__ == "__main__":
    raise SystemExit(main())
