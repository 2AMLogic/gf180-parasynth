#!/usr/bin/env python3
"""render / analyse / accept as three separately-identified stages, demonstrated
on the bass drum's decay -- the metric a real measurement bug in this repository
moved by 101 ms while the sound did not change at all.

    tools/stage_case.py demo                       the end-to-end demonstration
    tools/stage_case.py controls                   every injected control
    tools/stage_case.py render  --voice BD
    tools/stage_case.py analyse --render <id> --analyser decay-v2-rms-per-voice
    tools/stage_case.py accept  --analysis <id>
    tools/stage_case.py compare --before <id> --after <id>

WHAT THE DEMONSTRATION ACTUALLY DEMONSTRATES
--------------------------------------------
Two of this project's own historical defects, replayed on the same case:

  the MEASUREMENT defect   a 5 ms moving-average envelope read as the decay of a
                           49 Hz kick -- 0.28 of a cycle, so it measures its own
                           ripple (`audio_measure.py` rule 1). Already a
                           permanent control as `sound_report.py --inject
                           bd-ma-envelope`. It reads BD T20 as 207 ms.
  the SOUND defect         the kick's body Q 40 % low, so it really does decay
                           faster (`sound_report.py --inject bd-decay-short`).
                           It reads BD T20 as 186 ms.

**From the number alone, -101 ms and -122 ms are the same kind of thing.** One
is a wrong ruler on a correct sound; the other is a correct ruler on a wrong
sound. That is the snare's 8.8 -> 7.6 -> 3.4 in miniature, and it is why
`demo` re-analyses the RETAINED audio rather than re-rendering: the second
analysis reads the same bytes off disk, so the render id is identical by
construction and the delta can only be the estimator.

The third comparison is the one that matters most: the v1 analysis of the clean
render against the v2 analysis of the broken render differ in BOTH stages, and
`classify_delta` REFUSES to attribute that delta. A tool that answered it would
be guessing.

WHAT GOES INTO AN ID, AND WHAT DELIBERATELY DOES NOT
----------------------------------------------------
The render id is a hash of the code that determines the AUDIO (`model/drums_fx.py`,
the render function's own source, and the variant injection's source when one is
used) plus the case and config. The analysis id is a hash of the ANALYSER --
its declared version and method, the source of the estimator function that runs,
`model/drum_verify.py`, `model/audio_measure.py` -- plus the render id and the
sha256 of the exact audio file it read.

`tools/stage_case.py`'s own hash is in neither identity; it is in `provenance`.
Putting it in both would make every edit to this file's argument parsing look
like a change of sound AND a change of method, which would destroy the only
property the scheme has. The estimator source hashes are there instead, so a
change to a measurement still moves the analysis id.

The trade-off is deliberately one-sided: hashing a function's whole source
means a docstring edit reads as a change. That direction is the safe one -- a
spurious "this changed" costs a re-run, a missed "this changed" is the failure
this file exists to prevent.

NO EXTERNAL RECORDING IS USED HERE, and the records say so. The two `target`
bounds cite `docs/tr808-reference.md` by hash as a `document` reference; the two
`lock` bounds cite `model/sound_report.py`'s `LOCKS` at commit ce400a6 as a
`model-lock`. `measurement_manifest.validate_measurement` requires a
`recording` reference to state the control settings it was captured at -- the
field whose absence let a study drive our snare with the wrong TONE law -- and
refusing to claim a recording we did not read is the same rule from the other
side.
"""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "audition"))
sys.path.insert(0, str(ROOT / "tools"))

import numpy as np                                                   # noqa: E402
from scipy.io import wavfile                                         # noqa: E402

import audio_measure as am                                           # noqa: E402
import drum_verify as dv                                             # noqa: E402
import drums_fx as dx                                                # noqa: E402
import measurement_manifest as mm                                    # noqa: E402
import sound_report as sr                                            # noqa: E402

SOLO_SECONDS = 2.2
ACCENT = 1.0
BUS_GAIN = 0.45


def _sha(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()[:32]


def _file(rel: str) -> str:
    return mm.sha256_file(ROOT / rel)


# ===========================================================================
# the bounds, and the record of when each one moved and why
# ===========================================================================
# The values and rationales are READ from `model/sound_report.py`'s property
# table, not copied here: a second bound table would be a second thing to
# forget to update, which is the failure this file is about. The history is the
# part `sound_report.py` had nowhere to put -- `LOCK_CHANGELOG` now holds it for
# the `lock` bounds, and `TARGET_HISTORY` below holds it for the `target`
# bounds, whose values live in `model/drum_verify.py`'s SPEC.
#
# Each entry below is a real commit, recovered from this repository's history,
# not a placeholder: `git log -L` on the SPEC row is how it was found.
TARGET_HISTORY = {
    ("BD", "decay tau"): [
        {"at": "7be1490", "from": 127.0, "to": 144.0,
         "reason": "contract revision 6 (DR 0009), issue #14: the BD's f0 is the "
                   "circuit's 49.4 Hz, not the chart's 56, and tau follows it -- "
                   "144 ms is reference 2's own table. The 127 ms it replaced was "
                   "derived from the withdrawn 56 Hz."},
    ],
    ("BD", "fundamental"): [
        {"at": "7be1490", "from": 56.0, "to": 49.4,
         "reason": "contract revision 6 (DR 0009), issue #14: 49.4 Hz is the "
                   "bridged-T circuit's computed resonance; 56 Hz was Roland's "
                   "chart figure and is withdrawn."},
    ],
}


def bound_for(voice: str, metric: str) -> dict:
    """One bound, assembled from the table that already owns its value.

    Refuses rather than invents: a metric `sound_report.py` does not declare
    gets no bound here, because a bound this file made up would be a bound with
    no rationale, which is the thing `validate_bound` exists to reject."""
    props = [p for p in sr.build_properties() if p.voice == voice and p.name == metric]
    if not props:
        raise KeyError(f"model/sound_report.py declares no {metric!r} property for "
                       f"{voice}, so there is no bound to apply and nothing here "
                       f"may invent one")
    p = props[0]
    if p.kind == "lock":
        value = sr.LOCKS.get((voice, metric))
        history = sr.lock_history(voice, metric)
        if value is None:
            raise KeyError(f"({voice}, {metric}) is a lock with no locked value")
    else:
        value = p.value
        history = TARGET_HISTORY.get((voice, metric), [])
    return {"value": value, "tolerance": p.tol, "units": p.unit, "kind": p.kind,
            "rationale": p.source, "history": history,
            "declared_in": "model/sound_report.py build_properties()"}


# ===========================================================================
# render: source + config -> raw WAV and internal traces
# ===========================================================================
def render_voice(voice: str, variant: str | None) -> dict:
    """One hit of one drum sound through the register interface, here and now.

    `variant` names an entry in `model/sound_report.py`'s INJECTIONS -- a
    SOUND defect, patched into the model the way a design change would arrive.
    Reusing that table rather than inventing a defect matters: those are the
    bugs this project actually made, which is the one source of defects
    guaranteed not to come from our own imagination
    (docs/verification-rules.md 5)."""
    ctx = {"_patches": []}
    if variant:
        if variant not in sr.INJECTIONS:
            raise KeyError(f"{variant!r} is not one of model/sound_report.py's "
                           f"injections: {', '.join(sorted(sr.INJECTIONS))}")
        sr.INJECTIONS[variant][2](ctx)
    try:
        n = int(SOLO_SECONDS * dx.SR)
        d = dx.DrumsFx()
        kit = dx.kit_with_sounds(voice)
        writes = dx.hit_writes([(int(0.01 * dx.SR), dx.SOUND_STOP[voice], ACCENT)], kit)
        bus_dm, bus_bd = d.play(writes, n)
        g = dx.accent_reg(BUS_GAIN)
        out = dx.output_fx(np.zeros(n), 0, bus_dm, g, bus_bd, g)
    finally:
        for mod, attr, orig in ctx["_patches"]:
            setattr(mod, attr, orig)
    return {"audio": np.asarray(out, dtype=np.float32) / 32768.0,
            "sr": dx.SR,
            "bus_dm": np.asarray(bus_dm, dtype=np.float32),
            "bus_bd": np.asarray(bus_bd, dtype=np.float32),
            "writes": len(writes), "kit_words": len(kit)}


def do_render(root, voice: str, variant: str | None, retention: str) -> dict:
    code = {
        "renderer": "model/drums_fx.py via tools/stage_case.py render_voice",
        "drums_fx": _file("model/drums_fx.py"),
        "render_function": _sha(inspect.getsource(render_voice)),
        "n_stops": dx.N_STOPS, "sample_rate_hz": dx.SR,
    }
    if variant:
        code["variant_source"] = _sha(inspect.getsource(sr.INJECTIONS[variant][2]))
    config = {"voice": voice, "variant": variant,
              "variant_means": sr.INJECTIONS[variant][0] if variant else "the kit as it ships",
              "seconds": SOLO_SECONDS, "accent": ACCENT, "bus_gain": BUS_GAIN,
              "hit_at_s": 0.01, "hits": 1,
              "audio_format": "WAV float32 mono, NOT int16: the retained artefact "
                              "must be bit-identical to what the model produced, or "
                              "a re-analysis cannot tell a quantisation difference "
                              "from a sound change"}
    inputs = {"model/drums_fx.py": _file("model/drums_fx.py"),
              "model/sound_report.py": _file("model/sound_report.py")}
    rec = mm.make_render(case=f"{voice}-solo", code_version=code, config=config,
                         inputs=inputs, retention_class=retention)
    r = render_voice(voice, variant)
    d = mm.stage_dir(root, rec)
    (d / "audio").mkdir(parents=True, exist_ok=True)
    (d / "traces").mkdir(parents=True, exist_ok=True)
    wav = d / "audio" / f"{voice}.wav"
    wavfile.write(wav, r["sr"], r["audio"])
    traces = d / "traces" / "buses.npz"
    np.savez_compressed(traces, bus_dm=r["bus_dm"], bus_bd=r["bus_bd"])
    rec["artefacts"] = {
        "audio": {"path": f"audio/{voice}.wav", "sha256": mm.sha256_file(wav),
                  "samples": int(len(r["audio"])), "sample_rate_hz": r["sr"],
                  "dtype": "float32", "channels": 1,
                  "peak_fs": round(float(np.abs(r["audio"]).max()), 8)},
        "traces": {"path": "traces/buses.npz", "sha256": mm.sha256_file(traces),
                   "contains": "bus_dm, bus_bd -- the two drum buses BEFORE "
                               "output_fx, so a mix-stage question can be answered "
                               "without re-rendering",
                   "samples": int(len(r["bus_dm"]))},
    }
    rec["diagnostics"] = {"register_writes": r["writes"], "kit_words": r["kit_words"],
                          "rms_fs": round(float(np.sqrt(np.mean(
                              np.square(r["audio"].astype(np.float64))))), 8)}
    existing = d / "manifest.json"
    if existing.exists():
        old = json.loads(existing.read_text())
        was = (old.get("artefacts") or {}).get("audio", {}).get("sha256")
        if was and was != rec["artefacts"]["audio"]["sha256"]:
            rec["outcome"] = mm.REFUSED
            rec["why"] = (f"this render id already exists with audio {was} and this "
                          f"render produced {rec['artefacts']['audio']['sha256']}. The "
                          f"id is a hash of everything that should determine the "
                          f"audio, so an input is not being recorded -- and every "
                          f"attribution that uses this id is unsound until it is")
    mm.write_stage(root, rec)
    return rec


# ===========================================================================
# analyse: retained WAV + reference -> measurements
# ===========================================================================
def _fit_region(env, sr_hz: int, lo_db: float = -3.0, hi_db: float = -30.0) -> dict:
    """The samples `drum_verify.decay_fit` actually fits, and the fit's own
    uncertainty -- neither of which `decay_fit` returns.

    This repeats `decay_fit`'s selection rule, which is a fork unless it is
    checked, so `analyse_one` asserts the tau recomputed here equals
    `decay_fit`'s to 1e-6 and REFUSES otherwise."""
    pk = int(np.argmax(env))
    ref = float(env[pk])
    if ref <= 0:
        return {"ok": False, "why": "the envelope peak is zero: a silent render"}
    tail = env[pk:]
    db = 20 * np.log10(np.maximum(tail, ref * 1e-7) / ref)
    stop = len(db)
    below = np.nonzero(db <= hi_db)[0]
    if len(below):
        stop = below[0] + 1
    sel = np.nonzero((db[:stop] <= lo_db) & (db[:stop] > hi_db))[0]
    if len(sel) < 8:
        return {"ok": False, "why": f"only {len(sel)} samples between {lo_db} and "
                                    f"{hi_db} dB: too few to fit"}
    t = sel / sr_hz
    y = db[sel]
    A = np.vstack([t, np.ones_like(t)]).T
    slope, icept = np.linalg.lstsq(A, y, rcond=None)[0]
    resid = y - (slope * t + icept)
    dof = max(1, len(sel) - 2)
    s2 = float(np.sum(resid ** 2) / dof)
    cov = s2 * np.linalg.inv(A.T @ A)
    sigma_slope = float(np.sqrt(cov[0, 0]))
    tau_s = -20.0 / np.log(10.0) / slope if slope < 0 else float("nan")
    # tau = -k/slope, so |dtau/dslope| = k/slope^2
    sigma_tau_ms = float(20.0 / np.log(10.0) / slope ** 2 * sigma_slope * 1e3)
    floor = float(np.sqrt(np.mean(np.square(env[-int(0.05 * sr_hz):].astype(np.float64)))))
    floor_db = 20 * np.log10(max(floor, 1e-12) / ref)
    return {"ok": True, "peak_index": pk,
            "t0_s": float((pk + sel[0]) / sr_hz), "t1_s": float((pk + sel[-1]) / sr_hz),
            "points": int(len(sel)), "span_db": float(y.max() - y.min()),
            "tau_ms": float(tau_s * 1e3), "sigma_tau_ms": sigma_tau_ms,
            "noise_floor_db": float(floor_db), "hi_db": hi_db, "lo_db": lo_db,
            "db": db.astype(np.float32), "sel": sel.astype(np.int64)}


def env_v1(x, sr_hz: int, voice: str):
    """THE HISTORICAL DEFECT. A 5 ms moving average used as an envelope --
    0.28 of a cycle at 56 Hz (`audio_measure.py` rule 1), one window for every
    voice regardless of its fundamental. This shipped, and everything it
    measured had to be withdrawn."""
    return np.abs(am.moving_average_envelope(x, 5.0, sr_hz))


def env_v2(x, sr_hz: int, voice: str):
    """The corrected method: a moving RMS whose window is several periods of
    the voice's OWN fundamental (`drum_verify.ENV_WIN_MS`), so it does not
    ripple at 2*f0."""
    return dv.envelope(x, sr_hz, win_ms=dv.ENV_WIN_MS[voice])


ANALYSERS = {
    "decay-v1-moving-average-5ms": {
        "name": "drum decay and attack from a 5 ms moving-average envelope",
        "version": "v1-withdrawn",
        "method": "drum_verify.trim_onset -> audio_measure.moving_average_envelope("
                  "x, 5.0 ms) -> drum_verify.decay_fit over -3..-30 dB of the "
                  "envelope peak; attack is the 2 %-of-peak crossing to the peak",
        "status": "WITHDRAWN. A 5 ms window spans 0.28 of a cycle at 56 Hz, so on "
                  "the kick the envelope ripples at 2*f0 and the fit reads its own "
                  "ripple. Kept runnable on purpose, as the historical method, and "
                  "kept red permanently as sound_report.py --inject bd-ma-envelope "
                  "(docs/verification-rules.md 5).",
        "envelope": env_v1,
    },
    "decay-v2-rms-per-voice": {
        "name": "drum decay and attack from a per-voice moving-RMS envelope",
        "version": "v2",
        "method": "drum_verify.trim_onset -> drum_verify.envelope (moving RMS, "
                  "window from drum_verify.ENV_WIN_MS, several periods of the "
                  "voice's own fundamental) -> drum_verify.decay_fit over "
                  "-3..-30 dB; attack is the 2 %-of-peak crossing to the peak",
        "status": "current",
        "envelope": env_v2,
    },
}

# The injections. Each is a way a manifest looks complete and is not, and each
# MUST come back REFUSED -- never a pass, and never a fail either, because a
# record we cannot read is no evidence rather than a bad result.
INJECTIONS = {
    "MEASUREMENT_NO_UNITS": "drop `units` from the T20 record: `decay = 127.4` with "
                            "no statement of whether that is tau, T20 or a threshold",
    "UNITS_CATEGORY_ERROR": "measure T20 in ms and bound it in register LSB -- the "
                            "`CP decay tau 47 ms` error, which was the E_CPTAIL "
                            "REGISTER read as the voice's decay",
    "FFT_PLACEHOLDER": "fill the fundamental's window field with \"n/a\": a field "
                       "that is present and says nothing",
    "BOUND_NO_RATIONALE": "blank the rationale on the T20 bound: a number somebody "
                          "chose, with no record of why",
    "BOUND_SILENT_CHANGE": "move the T20 bound to 207 ms -- exactly the value the "
                           "withdrawn estimator reports -- without appending to the "
                           "changelog. Without the changelog check this turns the "
                           "v1 defect from a fail into a pass",
    "NO_MATCHED_SETTINGS": "claim a `recording` reference and state no control "
                           "settings for it: the shape of the wrong-TONE-law study",
    "TAMPER_RETAINED_AUDIO": "flip one sample of the retained WAV before analysing: "
                             "the analysis must refuse on the hash, not measure the "
                             "file it was handed",
}

REFUSED_AT = {"TAMPER_RETAINED_AUDIO": "analyse"}


def _spectral_none(what: str) -> dict:
    """The FFT block for a metric that takes no FFT.

    `None` here is what the first run of this file wrote, and
    `validate_measurement` refused all three records for it -- correctly: a null
    where a transform's size belongs is indistinguishable from a transform
    nobody recorded. Each field says why it is inapplicable instead, and
    `window` is then filled in with the ENVELOPE's window, which is the
    smoothing that actually sets these metrics' resolution."""
    return {"transform": f"not applicable: {what} is a time-domain envelope "
                         f"measurement and takes no transform",
            "window": None,
            "nfft": f"not applicable: no transform is taken for {what}",
            "hop_samples": "not applicable: the envelope is evaluated at every "
                           "sample, so there is no hop"}


def analyse_one(root, render: dict, analyser_id: str, inject: str = "") -> dict:
    """Measure the RETAINED audio. Nothing here renders anything."""
    spec = ANALYSERS[analyser_id]
    voice = render["config"]["voice"]
    path, sha = mm.bind_to_retained(root, render, "audio")
    sr_hz, x = wavfile.read(path)
    x = np.asarray(x, dtype=np.float64)
    if x.ndim != 1:
        raise ValueError(f"{path} has {x.ndim} channels; this analyser is mono")

    trimmed = dv.trim_onset(x, sr_hz)
    onset_s = float((len(x) - len(trimmed)) / sr_hz)
    env = spec["envelope"](trimmed, sr_hz, voice)
    fit = dv.decay_fit(env, sr_hz)
    region = _fit_region(env, sr_hz)
    if region["ok"] and np.isfinite(fit["tau_ms"]):
        if abs(region["tau_ms"] - float(fit["tau_ms"])) > 1e-6:
            raise ValueError(
                f"REFUSED: this file's fit-region reconstruction gives tau "
                f"{region['tau_ms']:.6f} ms and drum_verify.decay_fit gives "
                f"{fit['tau_ms']:.6f} ms. They are supposed to select the same "
                f"samples; until they do, the interval this record reports is not "
                f"the interval the number came from")
    attack = float(dv.attack_ms(env, sr_hz))
    pk = int(np.argmax(env))
    thr_idx = np.nonzero(env[:pk + 1] > 0.02 * env[pk])[0]
    attack_t0 = float((thr_idx[0] if len(thr_idx) else 0) / sr_hz)
    lo_hz, hi_hz = dv.BAND[voice]
    nfft = 1 << 18
    n_spec = min(len(trimmed), int(0.5 * sr_hz))
    freqs, mag = dv.spectrum(trimmed[:n_spec], sr_hz, nfft=nfft)
    f0 = float(dv.peak_hz(freqs, mag, lo_hz, hi_hz))
    band = (freqs >= lo_hz) & (freqs <= hi_hz)
    in_band_peak = float(mag[band].max())
    above = freqs > hi_hz
    out_band_db = 20 * np.log10(max(float(mag[above].mean()), 1e-20) / in_band_peak)
    one_sample_ms = 1e3 / sr_hz
    bin_hz = sr_hz / nfft

    doc = {"kind": "document",
           "identity": "docs/tr808-reference.md 12/14, carried by "
                       "model/drum_verify.py SPEC['BD'] (source: "
                       f"{dv.SPEC[voice]['source']})",
           "sha256": _file("docs/tr808-reference.md"),
           "also": {"model/drum_verify.py": _file("model/drum_verify.py")},
           "matched_settings": (
               f"the documented reference setting for {voice}, "
               f"{dv.REF_MAIN[voice][1]} ({dv.REF_MAIN[voice][0]}); our render is "
               f"one hit at accent {ACCENT} with both drum buses at {BUS_GAIN}. "
               f"No recording is read here -- the comparand is the document's "
               f"figure, which is why `kind` is document and not recording")}
    lock = {"kind": "model-lock",
            "identity": f"model/sound_report.py LOCKS, measured at commit {sr.LOCK}",
            "sha256": _file("model/sound_report.py"),
            "matched_settings": (
                f"the same solo render sound_report.py measures: one {voice} hit at "
                f"accent {ACCENT} through the whole drum path at {dx.SR} Hz, "
                f"trim_onset applied before the envelope")}

    cond = {"channel": "mono: the drum buses summed through drums_fx.output_fx",
            "sample_rate_hz": int(sr_hz),
            "resampled_from_hz": f"not applicable: rendered and analysed at "
                                 f"{sr_hz} Hz, no resampling was performed",
            "filter": "none on the envelope path: drum_verify.envelope is a moving "
                      "RMS of the full-band signal. drum_verify.BAND is applied to "
                      "the SPECTRUM rows only, and is stated there",
            "normalisation": "none: absolute full scale. The decay is measured "
                             "relative to the envelope's own peak, so a gain change "
                             "cannot move it"}

    env_window = (f"moving RMS, {dv.ENV_WIN_MS[voice]} ms "
                  f"(drum_verify.ENV_WIN_MS[{voice!r}])"
                  if spec["envelope"] is env_v2 else
                  "moving average, 5.0 ms, the same window for every voice "
                  "(audio_measure.moving_average_envelope)")

    def decay_quality(extra_uncertainty=None, treatment=None):
        return {"fit": {"r2": round(float(fit["r2"]), 6),
                        "span_db": round(float(fit["span_db"]), 4),
                        "points": region.get("points"),
                        "model": "least squares on the log-envelope, dB vs s"},
                "uncertainty": extra_uncertainty,
                "uncertainty_basis": treatment,
                "noise_floor_db": round(region["noise_floor_db"], 3)
                if region["ok"] else None,
                "noise_floor_treatment":
                    f"the fit stops at the first sample {region.get('hi_db')} dB "
                    f"below the envelope peak; the last 50 ms of the render sits at "
                    f"{region['noise_floor_db']:.1f} dB, so no sample inside the "
                    f"floor enters the fit" if region["ok"] else None}

    measurements = []
    measurements.append({
        "metric": "fundamental", "units": "Hz",
        "valid": bool(np.isfinite(f0)),
        "value": round(f0, 4) if np.isfinite(f0) else None,
        "analyser": {"name": spec["name"], "version": spec["version"],
                     "method": "drum_verify.spectrum (Hann over the first "
                               f"{n_spec / sr_hz:.3f} s, {nfft}-point rfft) -> "
                               f"parabolic-interpolated maximum inside "
                               f"{lo_hz}-{hi_hz} Hz"},
        "selection": {"hit_index": 0, "onset_s": round(onset_s, 6),
                      "interval_s": [0.0, round(n_spec / sr_hz, 6)],
                      "interval_basis": "from the trimmed onset, capped at 0.5 s so "
                                        "a long tail does not dilute the transform "
                                        "(drum_verify.measure's own rule)"},
        "conditioning": dict(cond, filter=f"band {lo_hz}-{hi_hz} Hz "
                                          f"(drum_verify.BAND[{voice!r}]) restricts "
                                          f"where the maximum may be found; the "
                                          f"signal itself is not filtered"),
        "spectral": {"transform": "real FFT of a Hann-windowed segment",
                     "window": f"Hann, {n_spec} samples "
                               f"({n_spec / sr_hz * 1e3:.1f} ms)",
                     "nfft": nfft,
                     "hop_samples": "not applicable: one transform of one segment, "
                                    "not a spectrogram"},
        "quality": {"fit": "parabolic interpolation of the three bins around the "
                           "maximum",
                    "uncertainty": round(bin_hz / 2.0, 4),
                    "uncertainty_basis": f"half the {bin_hz:.4f} Hz bin spacing of "
                                         f"the {nfft}-point transform, before the "
                                         f"parabolic interpolation improves it",
                    "noise_floor_db": round(out_band_db, 3),
                    "noise_floor_treatment": f"the maximum is taken inside "
                                             f"{lo_hz}-{hi_hz} Hz, so the "
                                             f"{out_band_db:.1f} dB mean level above "
                                             f"the band cannot become the peak"},
        "reference": doc,
        "bound": bound_for(voice, "fundamental"),
    })
    measurements.append({
        "metric": "decay tau", "units": "ms",
        "valid": bool(np.isfinite(fit["tau_ms"])),
        "value": round(float(fit["tau_ms"]), 4) if np.isfinite(fit["tau_ms"]) else None,
        "why": "" if np.isfinite(fit["tau_ms"]) else (region.get("why") or "the fit refused"),
        "analyser": {"name": spec["name"], "version": spec["version"],
                     "method": spec["method"]},
        "selection": {"hit_index": 0, "onset_s": round(onset_s, 6),
                      "interval_s": [round(region["t0_s"], 6), round(region["t1_s"], 6)]
                      if region["ok"] else None,
                      "interval_basis": f"the samples between {region.get('lo_db')} "
                                        f"and {region.get('hi_db')} dB below the "
                                        f"envelope peak, which is what decay_fit "
                                        f"fits -- not the whole tail"},
        "conditioning": cond,
        "spectral": dict(_spectral_none("an exponential decay fit"),
                         window=env_window),
        "quality": decay_quality(
            round(region["sigma_tau_ms"], 4) if region["ok"] else None,
            "1 sigma on tau, propagated from the least-squares covariance of the "
            "log-envelope slope" if region["ok"] else None),
        "reference": doc,
        "bound": bound_for(voice, "decay tau"),
    })
    measurements.append({
        "metric": "T20", "units": "ms",
        "valid": bool(np.isfinite(fit["t20_ms"])),
        "value": round(float(fit["t20_ms"]), 4) if np.isfinite(fit["t20_ms"]) else None,
        "why": "" if np.isfinite(fit["t20_ms"]) else "no sample reached -20 dB",
        "analyser": {"name": spec["name"], "version": spec["version"],
                     "method": "the first sample of the envelope at or below -20 dB "
                               "of its peak, in ms from the peak -- a threshold "
                               "crossing, not a fit"},
        "selection": {"hit_index": 0, "onset_s": round(onset_s, 6),
                      "interval_s": [round(region["peak_index"] / sr_hz, 6),
                                     round(region["peak_index"] / sr_hz
                                           + float(fit["t20_ms"]) * 1e-3, 6)]
                      if region["ok"] and np.isfinite(fit["t20_ms"]) else None,
                      "interval_basis": "the envelope peak to its first -20 dB "
                                        "crossing"},
        "conditioning": cond,
        "spectral": dict(_spectral_none("a -20 dB threshold crossing"),
                         window=env_window),
        "quality": {"fit": "not applicable: a threshold crossing is located, not "
                           "fitted",
                    "uncertainty": round(one_sample_ms, 4),
                    "uncertainty_basis": f"+/-1 sample ({one_sample_ms:.4f} ms); the "
                                         f"envelope's own smoothing window is the "
                                         f"larger term and is stated in "
                                         f"spectral.window",
                    "noise_floor_db": round(region["noise_floor_db"], 3)
                    if region["ok"] else None,
                    "noise_floor_treatment": "-20 dB is far above the render's floor, "
                                             "so the crossing is on signal; a floor "
                                             "above -20 dB would make this metric "
                                             "meaningless and is reported here for "
                                             "exactly that check"},
        "reference": lock,
        "bound": bound_for(voice, "T20"),
    })
    measurements.append({
        "metric": "attack", "units": "ms",
        "valid": bool(np.isfinite(attack)),
        "value": round(attack, 4) if np.isfinite(attack) else None,
        "why": "" if np.isfinite(attack) else "no envelope peak",
        "analyser": {"name": spec["name"], "version": spec["version"],
                     "method": "drum_verify.attack_ms: the 2 %-of-peak crossing of "
                               "the envelope to the envelope's peak"},
        "selection": {"hit_index": 0, "onset_s": round(onset_s, 6),
                      "interval_s": [round(attack_t0, 6),
                                     round(pk / sr_hz, 6)] if pk / sr_hz > attack_t0
                      else None,
                      "interval_basis": "the 2 %-of-peak crossing to the peak"},
        "conditioning": cond,
        "spectral": dict(_spectral_none("an onset-to-peak time"), window=env_window),
        "quality": {"fit": "not applicable: two threshold/extremum locations, no fit",
                    "uncertainty": round(one_sample_ms, 4),
                    "uncertainty_basis": f"+/-1 sample ({one_sample_ms:.4f} ms) on "
                                         f"each end; the envelope window "
                                         f"({env_window}) sets the real resolution "
                                         f"and is the reason this metric moves when "
                                         f"the envelope method changes",
                    "noise_floor_db": round(region["noise_floor_db"], 3)
                    if region["ok"] else None,
                    "noise_floor_treatment": "the 2 % threshold is 34 dB below the "
                                             "peak; the measured floor above is the "
                                             "check that it is above the floor"},
        "reference": lock,
        "bound": bound_for(voice, "attack"),
    })

    if inject:
        measurements = apply_injection(inject, measurements)

    analyser_identity = {
        "id": analyser_id, "name": spec["name"], "version": spec["version"],
        "method": spec["method"], "status": spec["status"],
        "envelope_source": _sha(inspect.getsource(spec["envelope"])),
        "drum_verify": _file("model/drum_verify.py"),
        "audio_measure": _file("model/audio_measure.py"),
    }
    rec = mm.make_analysis(render=render, analyser=analyser_identity,
                           measurements=measurements,
                           retention_class=render.get("retention_class", "smoke"))
    d = mm.stage_dir(root, rec)
    (d / "traces").mkdir(parents=True, exist_ok=True)
    tp = d / "traces" / "envelope.npz"
    np.savez_compressed(tp, envelope=env.astype(np.float32),
                        envelope_db=region.get("db", np.zeros(0, dtype=np.float32)),
                        fit_indices=region.get("sel", np.zeros(0, dtype=np.int64)),
                        spectrum_hz=freqs[band].astype(np.float32),
                        spectrum_mag=mag[band].astype(np.float32))
    rec["artefacts"] = {"traces": {
        "path": "traces/envelope.npz", "sha256": mm.sha256_file(tp),
        "contains": "envelope, envelope_db, fit_indices, in-band spectrum -- the "
                    "numerical traces the verdict rests on, so the next estimator "
                    "can be argued against these and not only against the summary"}}
    rec["read"] = {"audio": str(path.relative_to(pathlib.Path(root))),
                   "sha256": sha,
                   "note": "read off disk. No render ran in this stage, which is "
                           "what makes the attribution valid"}
    if inject:
        rec["INJECTED_CONTROL"] = inject
        rec["retention_class"] = "smoke"
        rec["retention_days"] = mm.RETENTION_DAYS["smoke"]
    mm.write_stage(root, rec)
    return rec


def apply_injection(inject: str, ms: list) -> list:
    """Break the RECORD, not the sound and not the estimator.

    Each of these leaves a manifest that looks complete to a reader and is not,
    which is the failure mode a schema has: the cheap way to satisfy one is to
    fill it in with words that mean nothing."""
    ms = json.loads(json.dumps(ms))
    by = {m["metric"]: m for m in ms}
    if inject == "MEASUREMENT_NO_UNITS":
        del by["T20"]["units"]
    elif inject == "UNITS_CATEGORY_ERROR":
        by["T20"]["units"] = "register LSB"
    elif inject == "FFT_PLACEHOLDER":
        by["fundamental"]["spectral"]["window"] = "n/a"
    elif inject == "BOUND_NO_RATIONALE":
        by["T20"]["bound"]["rationale"] = ""
    elif inject == "BOUND_SILENT_CHANGE":
        by["T20"]["bound"]["value"] = 207.0
    elif inject == "NO_MATCHED_SETTINGS":
        by["T20"]["reference"] = dict(by["T20"]["reference"], kind="recording",
                                      matched_settings="")
    elif inject in REFUSED_AT:
        pass
    else:
        raise KeyError(f"{inject!r} is not one of {sorted(INJECTIONS)}")
    return ms


def tamper(root, render: dict) -> None:
    """Flip one sample of the retained WAV. `bind_to_retained` must catch it."""
    p = pathlib.Path(root) / "runs" / render["id"] / render["artefacts"]["audio"]["path"]
    b = bytearray(p.read_bytes())
    b[-3] ^= 0x01
    p.write_bytes(bytes(b))


# ===========================================================================
# accept
# ===========================================================================
CRITERIA_ID = "bd-decay-criteria-1"
CRITERIA_RATIONALE = (
    "Two target bounds from docs/tr808-reference.md (outside them the model is "
    "WRONG) and two lock bounds from model/sound_report.py LOCKS at commit "
    f"{sr.LOCK} (outside them the model CHANGED, which may be the point of the "
    "commit). The two kinds are never mixed in one verdict, and every bound "
    "carries the record of when it last moved and why -- a verdict that went "
    "green because a tolerance moved is not a verdict that passed.")


def do_accept(root, analysis: dict) -> dict:
    v = mm.accept(analysis, criteria_id=CRITERIA_ID,
                  criteria_rationale=CRITERIA_RATIONALE)
    mm.write_stage(root, v)
    return v


# ===========================================================================
# the demonstration
# ===========================================================================
def print_measurements(rec: dict) -> None:
    print(f"  analysis {rec['id']}  analyser {rec['analyser']['id']} "
          f"({rec['analyser']['version']})")
    print(f"  of render {rec['of_render']}  audio {list(rec['render_audio'].values())}")
    for m in rec["measurements"]:
        b = m["bound"]
        v = m.get("value")
        print(f"    {m['metric']:<14} {v!s:>10} {m.get('units','?'):<4} "
              f"bound {b['value']!s:>9} +/-{b['tolerance']:<6} {b['kind']:<6} "
              f"r2/quality {str(m['quality'].get('fit'))[:46]}")


def demo(root, out: pathlib.Path | None) -> int:
    print("=" * 94)
    print("render / analyse / accept -- BD decay, two of this project's own defects")
    print("=" * 94)

    r_clean = do_render(root, "BD", None, "reference-fixture")
    print(f"\n[render 1] clean, id {r_clean['id']}")
    print(f"           audio {r_clean['artefacts']['audio']['sha256']} "
          f"({r_clean['artefacts']['audio']['samples']} samples float32), retained "
          f"{r_clean['retention_days']} days as {r_clean['retention_class']}")

    a_v1 = analyse_one(root, r_clean, "decay-v1-moving-average-5ms")
    print("\n[analyse 1] the WITHDRAWN estimator on that audio")
    print_measurements(a_v1)
    v_v1 = do_accept(root, a_v1)
    print(f"  accept {v_v1['id']} -> {v_v1['outcome']} (code {v_v1['outcome_code']})")
    for n, row in v_v1["metrics"].items():
        print(f"    {n:<14} {row['state']:<10} {row['why']}")

    a_v2 = analyse_one(root, r_clean, "decay-v2-rms-per-voice")
    print("\n[analyse 2] the CORRECTED estimator, re-reading the SAME retained WAV")
    print(f"            read {a_v2['read']['audio']} {a_v2['read']['sha256']} "
          f"-- no render ran")
    print_measurements(a_v2)
    v_v2 = do_accept(root, a_v2)
    print(f"  accept {v_v2['id']} -> {v_v2['outcome']} (code {v_v2['outcome_code']})")
    for n, row in v_v2["metrics"].items():
        print(f"    {n:<14} {row['state']:<10} {row['why']}")

    r_broken = do_render(root, "BD", "bd-decay-short", "smoke")
    a_v2b = analyse_one(root, r_broken, "decay-v2-rms-per-voice")
    v_v2b = do_accept(root, a_v2b)
    print(f"\n[render 2] the SOUND defect '{r_broken['config']['variant']}': "
          f"{r_broken['config']['variant_means']}")
    print(f"           id {r_broken['id']}, audio "
          f"{r_broken['artefacts']['audio']['sha256']}")
    print("[analyse 3] the CORRECTED estimator on the broken render")
    print_measurements(a_v2b)
    print(f"  accept {v_v2b['id']} -> {v_v2b['outcome']} "
          f"(code {v_v2b['outcome_code']})")
    for n, row in v_v2b["metrics"].items():
        print(f"    {n:<14} {row['state']:<10} {row['why']}")

    deltas = [("v1 -> v2 on the SAME retained audio", mm.classify_delta(a_v1, a_v2)),
              ("v2 on clean -> v2 on the broken render", mm.classify_delta(a_v2, a_v2b)),
              ("v1 on clean -> v2 on the broken render", mm.classify_delta(a_v1, a_v2b))]
    print("\n" + "=" * 94)
    print("ATTRIBUTION -- the question this repository could not ask")
    print("=" * 94)
    for title, d in deltas:
        print(f"\n  {title}")
        print(f"    render changed   {d['render_changed']}")
        print(f"    analyser changed {d['analyser_changed']}")
        print(f"    => {d['attribution']}")
        for n, row in d["metrics"].items():
            if "before" in row:
                print(f"       {n:<14} {row['before']!s:>10} -> {row['after']!s:<10} "
                      f"delta {row.get('delta')!s:>10}  tol "
                      f"{row.get('tolerance')!s:>6}  {row.get('saw')}")

    ok = (v_v2["outcome"] == "pass" and v_v1["outcome"] == "fail"
          and v_v2b["outcome"] == "fail"
          and deltas[0][1]["attribution"] == mm.MEASUREMENT_ONLY
          and deltas[1][1]["attribution"] == mm.SOUND_ONLY
          and not deltas[2][1]["attributable"])
    print("\n" + "-" * 94)
    print(f"  the corrected estimator on the clean render     {v_v2['outcome']}")
    print(f"  the withdrawn estimator on the SAME audio       {v_v1['outcome']}  "
          f"<- the measurement was wrong, not the sound")
    print(f"  the corrected estimator on the broken render    {v_v2b['outcome']}  "
          f"<- the sound was wrong, not the measurement")
    print(f"  the two together                               "
          f"{'REFUSED' if not deltas[2][1]['attributable'] else 'ATTRIBUTED (WRONG)'}"
          f"  <- unattributable, and said so")
    print("-" * 94)

    if out:
        out = pathlib.Path(out)
        out.mkdir(parents=True, exist_ok=True)
        for name, rec in (("render-clean", r_clean), ("render-bd-decay-short", r_broken),
                          ("analysis-v1-on-clean", a_v1),
                          ("analysis-v2-on-clean", a_v2),
                          ("analysis-v2-on-broken", a_v2b),
                          ("verdict-v1-on-clean", v_v1),
                          ("verdict-v2-on-clean", v_v2),
                          ("verdict-v2-on-broken", v_v2b)):
            (out / f"{name}.json").write_text(json.dumps(rec, indent=1, default=str) + "\n")
        for slug, (title, d) in zip(("measurement-only", "sound-only", "unattributable"),
                                    deltas):
            (out / f"delta-{slug}.json").write_text(
                json.dumps(dict(d, comparison=title), indent=1, default=str) + "\n")
        print(f"  manifests written to {out}")
    print(f"\n{'PASS' if ok else 'FAIL'}: the demonstration "
          f"{'held' if ok else 'DID NOT hold'}")
    return 0 if ok else 1


# ===========================================================================
# the controls
# ===========================================================================
CHECK_NAMES = {
    "no units field": "absent field",
    "not stated": "unstated field",
    "bounded in": "units mismatch",
    "moved with no reason recorded": "silent bound",
    "no matched control settings": "unmatched ref",
    "hashes": "artefact hash",
}


def which_checks(problems: list[str]) -> list[str]:
    fired = []
    for key, label in CHECK_NAMES.items():
        if any(key in p for p in problems):
            fired.append(label)
    return fired


def controls(root) -> int:
    """Every injection, and which check caught it.

    The three conditions (docs/verification-rules.md 5): the clean run must
    pass, the mutant must actually activate, and the intended assertion must be
    the one that fails. A control that cannot run looks exactly like one that
    works, so the clean run is asserted here and not assumed."""
    render = do_render(root, "BD", None, "smoke")
    clean = do_accept(root, analyse_one(root, render, "decay-v2-rms-per-voice"))
    print(f"clean: accept -> {clean['outcome']} (code {clean['outcome_code']})")
    if clean["outcome"] != "pass":
        print("NO VERDICT: the clean run does not pass, so no injection below can be "
              "distinguished from the apparatus being broken")
        return 2

    rows, bad = [], []
    labels = sorted(set(CHECK_NAMES.values()))
    for inject, what in sorted(INJECTIONS.items()):
        if REFUSED_AT.get(inject) == "analyse":
            # The tamper control gets its OWN root. The render id is content-
            # addressed, so a tampered render lands in the same directory as the
            # clean one and would corrupt every control after it -- which is what
            # the first run of this did, and it is the same class of mistake as
            # the control that mutated a function signature into invalid Python.
            sub = pathlib.Path(root) / "control-tamper"
            sub.mkdir(parents=True, exist_ok=True)
            r2 = do_render(sub, "BD", None, "smoke")
            tamper(sub, r2)
            try:
                analyse_one(sub, r2, "decay-v2-rms-per-voice")
                outcome, problems = "measured", []
            except (ValueError, FileNotFoundError) as e:
                outcome, problems = mm.REFUSED, [str(e)]
            stage = "analyse"
        else:
            a = analyse_one(root, render, "decay-v2-rms-per-voice", inject=inject)
            v = do_accept(root, a)
            outcome, problems, stage = v["outcome"], v["schema_problems"], "accept"
        fired = which_checks(problems)
        caught = outcome == mm.REFUSED and bool(fired)
        rows.append((inject, stage, outcome, fired, what))
        if not caught:
            bad.append(f"{inject}: {stage} came back {outcome!r} with "
                       f"{len(problems)} problem(s) and checks {fired} -- an "
                       f"injection that is not refused is a hole in the schema")

    print("\ninjected control              stage    outcome   "
          + " ".join(f"{l:<15}" for l in labels))
    for inject, stage, outcome, fired, _ in rows:
        cells = " ".join(f"{('CAUGHT' if l in fired else 'BLIND'):<15}" for l in labels)
        print(f"{inject:<28} {stage:<8} {outcome:<9} {cells}")
    print("\nwhat each one is:")
    for inject, _, _, _, what in rows:
        print(f"  {inject:<26} {what}")
    for b in bad:
        print(f"\n  {b}")
    print(f"\n{len(rows) - len(bad)}/{len(rows)} injected controls were REFUSED by the "
          f"stage that should refuse them")
    return 1 if bad else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["render", "analyse", "accept", "compare",
                                    "demo", "controls", "list"])
    ap.add_argument("--root", default=str(ROOT / "build" / "provenance"),
                    help="where runs/ and jobs/ live (plain files, no service)")
    ap.add_argument("--voice", default="BD")
    ap.add_argument("--variant", default=None,
                    help="a model/sound_report.py injection: a SOUND change")
    ap.add_argument("--retention", default="smoke", choices=sorted(mm.RETENTION_DAYS))
    ap.add_argument("--render", default="")
    ap.add_argument("--analyser", default="decay-v2-rms-per-voice",
                    choices=sorted(ANALYSERS))
    ap.add_argument("--analysis", default="")
    ap.add_argument("--before", default="")
    ap.add_argument("--after", default="")
    ap.add_argument("--inject", default="", help=f"one of {sorted(INJECTIONS)}")
    ap.add_argument("--out", default=None, help="also write the manifests here")
    a = ap.parse_args(argv)
    root = pathlib.Path(a.root)
    root.mkdir(parents=True, exist_ok=True)

    if a.cmd == "list":
        print("analysers:")
        for k, v in sorted(ANALYSERS.items()):
            print(f"  {k:<30} {v['version']:<14} {v['status'][:60]}")
        print("sound variants (model/sound_report.py injections):")
        for k, (d, voices, _) in sorted(sr.INJECTIONS.items()):
            print(f"  {k:<30} {','.join(voices):<10} {d[:60]}")
        print("record injections:")
        for k, v in sorted(INJECTIONS.items()):
            print(f"  {k:<30} {v[:80]}")
        return 0
    if a.cmd == "demo":
        return demo(root, a.out)
    if a.cmd == "controls":
        return controls(root)
    if a.cmd == "render":
        rec = do_render(root, a.voice, a.variant, a.retention)
        print(json.dumps({k: rec[k] for k in ("id", "case", "outcome", "artefacts",
                                              "retention_class", "retention_days")},
                         indent=1))
        return 0 if rec["outcome"] == "produced" else 2
    if a.cmd == "analyse":
        if not a.render:
            ap.error("--render <id> is required: analyse reads a retained render")
        render = mm.read_stage(root, "render", a.render)
        if a.inject == "TAMPER_RETAINED_AUDIO":
            tamper(root, render)
        try:
            rec = analyse_one(root, render, a.analyser,
                              inject="" if a.inject in REFUSED_AT else a.inject)
        except (ValueError, FileNotFoundError) as e:
            print(f"{mm.REFUSED}: {e}")
            return 2
        print(json.dumps({"id": rec["id"], "outcome": rec["outcome"],
                          "of_render": rec["of_render"], "read": rec["read"],
                          "schema_problems": rec["schema_problems"]}, indent=1))
        return 0 if rec["outcome"] != mm.REFUSED else 2
    if a.cmd == "accept":
        if not a.analysis:
            ap.error("--analysis <id> is required")
        v = do_accept(root, mm.read_stage(root, "analyse", a.analysis))
        print(json.dumps({k: v[k] for k in ("id", "outcome", "outcome_code", "why",
                                            "metrics", "schema_problems")}, indent=1))
        return v["outcome_code"]
    if a.cmd == "compare":
        if not (a.before and a.after):
            ap.error("--before and --after are both required")
        d = mm.classify_delta(mm.read_stage(root, "analyse", a.before),
                              mm.read_stage(root, "analyse", a.after))
        print(json.dumps(d, indent=1))
        if a.out:
            pathlib.Path(a.out).write_text(json.dumps(d, indent=1) + "\n")
        return 0 if d["attributable"] else 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
