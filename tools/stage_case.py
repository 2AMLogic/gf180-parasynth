#!/usr/bin/env python3
"""One REAL case through `tools/manifest.py`'s render / analyse / accept stages,
and the CI artifact plumbing for what it retains. Issue #259, the two halves
deliberately deferred out of #68's phase-1 PR (#260).

    tools/stage_case.py demo          the end-to-end demonstration
    tools/stage_case.py controls      every injected control
    tools/stage_case.py render  --voice BD
    tools/stage_case.py analyse --render <render id> --analyser <id>
    tools/stage_case.py accept  --analysis <job id>
    tools/stage_case.py compare --before <job id> --after <job id>
    tools/stage_case.py list

**The scheme is `tools/manifest.py` and is not re-implemented here.** `render()`,
`load_render()`, `measurement()`, `analyse()`, `accept()` and `diagnose()` all
come from there, including every refusal: the non-finite/silence/clipping
preconditions on render, the retained-WAV hash re-check on load, the
missing-field refusal on a measurement record, the no-rationale refusal on a
bound, and `diagnose()`'s refusal to attribute a delta when both the render and
the analyser moved. This file supplies the case, the estimators, the bounds and
the retention classes.

WHAT MAKES THIS A REAL CASE RATHER THAN A STAND-IN
--------------------------------------------------
#260's demonstration is a synthetic exponential decay, which #68's acceptance
criterion 4 explicitly allows. This one is the bass drum out of
`model/drums_fx.py`, through the register interface, and both of the defects it
replays are defects this project actually shipped and already keeps as permanent
controls (`docs/verification-rules.md` rule 5):

  the MEASUREMENT defect   a 5 ms moving-average envelope read as the decay of a
                           49 Hz kick -- 0.28 of a cycle, so it measures its own
                           ripple (`model/audio_measure.py` rule 1). Already
                           `sound_report.py --inject bd-ma-envelope`. It reads
                           BD T20 as 207 ms against a locked 308.
  the SOUND defect         the kick's body Q 40 % low, so it really does decay
                           faster. Already `sound_report.py --inject
                           bd-decay-short`. It reads BD T20 as 186 ms.

**From the number alone, -101 ms and -122 ms are the same kind of thing.** One is
a wrong ruler on a correct sound, the other a correct ruler on a wrong sound.
`demo` re-analyses the RETAINED WAV for the first, so the render is identical by
construction and `diagnose()` can only say `measurement changed`; it renders
again for the second, so `diagnose()` can only say `sound changed`; and it puts
the two together, where `diagnose()` refuses. That third leg is the important
one, because on T20 the two defects PARTLY CANCEL: -101 and -122 leave -21 ms,
inside T20's own 36 ms tolerance. A comparison that attributed that pair would
report the metric as unmoved while both halves of it were broken.

THE BOUNDS ARE NOT COPIED HERE
------------------------------
`criteria_for` reads value, tolerance, units, kind and rationale out of
`model/sound_report.py`'s `build_properties()`, whose numbers come from
`model/drum_verify.py`'s `SPEC` (targets -- a document states them, outside them
the model is WRONG) and its `LOCKS` table (locks -- this model at commit
ce400a6, outside them the model CHANGED). A second bound table would be a second
thing to forget to update. A metric `sound_report.py` does not declare gets no
bound here at all, because a bound this file invented would be a bound with no
rationale.

Each rationale carries the record of when that bound last moved and why --
`sound_report.LOCK_CHANGELOG` for the locks, `TARGET_HISTORY` below for the
targets. That is the half of #68's acceptance criterion 2 that #260's
`(case, metric)` ledger does not cover: the ledger detects a bound moving
between two `accept()` runs; the changelog says why the bound has the value it
has today, which is the gap #68 names in `LOCKS` by name.

WHERE THE BOUND LEDGER LIVES -- a deliberate decision, per #259
---------------------------------------------------------------
`manifest.accept()` warns that its ledger is a read-modify-write with no
locking, that `make verify` runs jobs in parallel, and that the first two
callers to use the default tracked ledger must serialise or pass distinct
paths. This is the first caller, so:

  * the ledger used at run time is a SCRATCH copy under the store root, SEEDED
    from the tracked `spec/acceptance-bounds-history.json`. Seeded, so a bound
    change is still detected on a fresh checkout or in CI -- which is the whole
    point of the tracked file. Scratch, so two parallel jobs cannot lose each
    other's update, and so a `make verify` run does not dirty a tracked file
    with a new `accepted_at` on every invocation;
  * `--record-bounds` copies the scratch ledger back over the tracked one. That
    is the only thing that moves the committed record, and it is a deliberate
    act that shows up as a diff in review.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "audition"))
sys.path.insert(0, str(ROOT / "tools"))

import numpy as np                                                   # noqa: E402

import audio_measure as am                                           # noqa: E402
import drum_verify as dv                                             # noqa: E402
import drums_fx as dx                                                # noqa: E402
import manifest                                                      # noqa: E402
import provenance                                                    # noqa: E402
import provenance_retention as pr                                    # noqa: E402
import sound_report as sr                                            # noqa: E402

SOLO_SECONDS = 2.2
ACCENT = 1.0
BUS_GAIN = 0.45
BOUNDS_TRACKED = ROOT / "spec" / "acceptance-bounds-history.json"


# ===========================================================================
# the bounds and their history
# ===========================================================================
# Recovered with `git log -L` on the SPEC row, not from memory: commit 7be1490
# (contract revision 6, DR 0009, issue #14) replaced the chart's 56 Hz with the
# bridged-T circuit's computed 49.4 Hz and moved tau with it.
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

METRICS = ("fundamental", "decay tau", "T20", "attack")


def bound_for(voice: str, metric: str) -> dict:
    """One bound, from the table that already owns its value, with the record of
    when it last moved and why folded into the rationale."""
    props = [p for p in sr.build_properties() if p.voice == voice and p.name == metric]
    if not props:
        raise manifest.Refused(
            f"model/sound_report.py declares no {metric!r} property for {voice}, so "
            f"there is no bound to apply -- and nothing here may invent one, "
            f"because an invented bound is a bound with no rationale")
    p = props[0]
    if p.kind == "lock":
        value, history = sr.LOCKS.get((voice, metric)), sr.lock_history(voice, metric)
        if value is None:
            raise manifest.Refused(f"({voice}, {metric}) is a lock with no value")
    else:
        value, history = p.value, TARGET_HISTORY.get((voice, metric), [])
    if not history:
        raise manifest.Refused(
            f"({voice}, {metric}) has no recorded change. A bound whose history is "
            f"empty cannot answer 'did this go green because the model improved or "
            f"because the bound moved?', which is what this stage is for")
    last = history[-1]
    kind_means = ("a figure a document states: outside it the model is WRONG"
                  if p.kind == "target" else
                  f"what this model measured at commit {sr.LOCK}: outside it the "
                  f"model CHANGED, which may be the point of the commit")
    return {
        "lo": float(value) - float(p.tol), "hi": float(value) + float(p.tol),
        "value": float(value), "tolerance": float(p.tol), "units": p.unit,
        "kind": p.kind,
        "rationale": (f"[{p.kind}] {p.source} | {kind_means} | source of record: "
                      f"model/sound_report.py build_properties() | last changed at "
                      f"{last['at']}"
                      + (f" (recorded by {last['recorded_by']})"
                         if last.get("recorded_by") else "")
                      + f": {last['from']!r} -> {last['to']!r} because "
                      + last["reason"]),
    }


def criteria_for(voice: str, inject: str = "") -> dict:
    c = {m: bound_for(voice, m) for m in METRICS}
    if inject == "BOUND_NO_RATIONALE":
        c["T20"]["rationale"] = ""
    elif inject == "BOUND_SILENT_CHANGE":
        # 207.0 is exactly what the withdrawn estimator reports, so this turns
        # the v1 defect from a fail into a pass without touching a line of
        # measurement code. manifest.accept() must record the move.
        c["T20"].update(lo=207.0 - 1.0, hi=207.0 + 1.0, value=207.0, tolerance=1.0)
    return c


# ===========================================================================
# render
# ===========================================================================
def render_voice(voice: str, variant: str | None, silent: bool = False) -> tuple:
    """One hit of one drum sound through the register interface, here and now.

    `variant` names an entry in `model/sound_report.py`'s INJECTIONS -- a SOUND
    defect, patched into the model the way a design change would arrive. Reusing
    that table rather than inventing a defect is `docs/verification-rules.md`
    rule 5: those are the bugs this project actually made, which is the one
    source of defects guaranteed not to come from our own imagination."""
    ctx = {"_patches": []}
    if variant:
        if variant not in sr.INJECTIONS:
            raise manifest.Refused(
                f"{variant!r} is not one of model/sound_report.py's injections: "
                f"{', '.join(sorted(sr.INJECTIONS))}")
        sr.INJECTIONS[variant][2](ctx)
    try:
        n = int(SOLO_SECONDS * dx.SR)
        d = dx.DrumsFx()
        kit = dx.kit_with_sounds(voice)
        writes = dx.hit_writes([(int(0.01 * dx.SR), dx.SOUND_STOP[voice], ACCENT)], kit)
        bus_dm, bus_bd = d.play(writes, n)
        g = dx.accent_reg(BUS_GAIN)
        out = np.asarray(dx.output_fx(np.zeros(n), 0, bus_dm, g, bus_bd, g),
                         dtype=np.float64) / 32768.0
    finally:
        for mod, attr, orig in ctx["_patches"]:
            setattr(mod, attr, orig)
    if silent:
        out = np.zeros_like(out)
    traces = {
        # Per-millisecond peak of each drum bus BEFORE output_fx. Not the
        # full-rate bus: that is 105600 numbers per bus per push as JSON, and
        # the question a retained bus answers -- did the mix stage move, or the
        # voice -- is answered by the envelope of each. Stated rather than
        # silently decimated.
        "bus_dm_peak_per_ms": _peak_per_ms(bus_dm, dx.SR),
        "bus_bd_peak_per_ms": _peak_per_ms(bus_bd, dx.SR),
    }
    return out, dx.SR, traces


def _peak_per_ms(x, sr_hz: int):
    k = sr_hz // 1000
    x = np.abs(np.asarray(x, dtype=np.float64))[:len(x) // k * k].reshape(-1, k)
    return np.round(x.max(axis=1), 3)


def do_render(root, voice: str, variant: str | None, retention: str,
              silent: bool = False) -> dict:
    config = {
        "voice": voice, "variant": variant,
        "variant_means": (sr.INJECTIONS[variant][0] if variant
                          else "the kit as it ships"),
        "renderer": "model/drums_fx.py via tools/stage_case.py render_voice",
        "drums_fx_sha256": provenance.file_sha(ROOT / "model" / "drums_fx.py"),
        "sound_report_sha256": provenance.file_sha(ROOT / "model" / "sound_report.py"),
        "seconds": SOLO_SECONDS, "accent": ACCENT, "bus_gain": BUS_GAIN,
        "hit_at_s": 0.01, "hits": 1, "n_stops": dx.N_STOPS,
        "sample_rate_hz": dx.SR,
    }
    # render_fn is called inside manifest.render, so the traces have to be
    # collected by the closure and handed over after -- manifest.render writes
    # traces it is given, and does not introspect render_fn.
    x, sr_hz, traces = render_voice(voice, variant, silent=silent)
    m = manifest.render(f"{voice}-solo", config, lambda: (x, sr_hz),
                        runs_dir=pathlib.Path(root) / "runs", traces=traces,
                        allow_silence=False)
    pr.mark(pathlib.Path(root) / "runs" / m["render_id"], retention)
    return m


# ===========================================================================
# analyse
# ===========================================================================
def env_v1(x, sr_hz: int, voice: str):
    """THE HISTORICAL DEFECT: a 5 ms moving average used as an envelope, one
    window for every voice regardless of its fundamental -- 0.28 of a cycle at
    56 Hz (`model/audio_measure.py` rule 1). Everything it measured had to be
    withdrawn."""
    return np.abs(am.moving_average_envelope(x, 5.0, sr_hz))


def env_v2(x, sr_hz: int, voice: str):
    """The corrected method: a moving RMS whose window is several periods of the
    voice's OWN fundamental (`drum_verify.ENV_WIN_MS`), so it does not ripple at
    2*f0."""
    return dv.envelope(x, sr_hz, win_ms=dv.ENV_WIN_MS[voice])


ANALYSERS = {
    "drum-decay-moving-average": {
        "version": "v1-withdrawn",
        "envelope": env_v1,
        "envelope_window": "moving average, 5.0 ms, the same window for every "
                           "voice (audio_measure.moving_average_envelope)",
        "status": "WITHDRAWN. A 5 ms window spans 0.28 of a cycle at 56 Hz, so on "
                  "the kick the envelope ripples at 2*f0 and the fit reads its own "
                  "ripple. Kept runnable as the historical method, and kept "
                  "permanently red as sound_report.py --inject bd-ma-envelope.",
    },
    "drum-decay-rms": {
        "version": "v2",
        "envelope": env_v2,
        "envelope_window": "moving RMS, drum_verify.ENV_WIN_MS[voice] ms "
                           "(several periods of the voice's own fundamental)",
        "status": "current",
    },
}

INJECTIONS = {
    "MEASUREMENT_NO_UNITS": ("drop `units` from the T20 record: `decay = 127.4` "
                             "with no statement of whether that is tau, T20 or a "
                             "threshold duration", "analyse"),
    "BOUND_NO_RATIONALE": ("blank the rationale on the T20 bound: a number "
                           "somebody chose, with no record of why", "accept"),
    "BOUND_SILENT_CHANGE": ("move the T20 bound onto 207 ms -- exactly what the "
                            "withdrawn estimator reports -- so the v1 defect goes "
                            "green with no measurement code touched", "accept"),
    "TAMPER_RETAINED_AUDIO": ("flip one sample of the retained WAV: the analysis "
                              "must refuse on the hash, not measure the file it "
                              "was handed", "analyse"),
    "RENDER_SILENCE": ("render exact silence: the apparatus in a wrong state, "
                       "which CLAUDE.md names as a Model D rendering silence",
                       "render"),
}


def _fit_region(env, sr_hz: int, lo_db: float = -3.0, hi_db: float = -30.0) -> dict:
    """The samples `drum_verify.decay_fit` actually fits, plus the fit's own
    uncertainty -- neither of which `decay_fit` returns, and both of which a
    measurement record has to state.

    This repeats `decay_fit`'s selection rule, which is a fork unless it is
    checked, so `measure` asserts the tau recomputed here equals `decay_fit`'s
    to 1e-6 and REFUSES otherwise."""
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
                                    f"{hi_db} dB below the peak: too few to fit"}
    t = sel / sr_hz
    y = db[sel]
    A = np.vstack([t, np.ones_like(t)]).T
    slope, icept = np.linalg.lstsq(A, y, rcond=None)[0]
    resid = y - (slope * t + icept)
    s2 = float(np.sum(resid ** 2) / max(1, len(sel) - 2))
    sigma_slope = float(np.sqrt(s2 * np.linalg.inv(A.T @ A)[0, 0]))
    tau_s = -20.0 / np.log(10.0) / slope if slope < 0 else float("nan")
    floor = float(np.sqrt(np.mean(np.square(env[-int(0.05 * sr_hz):]))))
    return {"ok": True, "peak_index": pk, "peak_s": pk / sr_hz,
            "t0_s": float((pk + sel[0]) / sr_hz),
            "t1_s": float((pk + sel[-1]) / sr_hz),
            "points": int(len(sel)), "span_db": float(y.max() - y.min()),
            "tau_ms": float(tau_s * 1e3),
            # tau = -k/slope, so |dtau/dslope| = k/slope^2
            "sigma_tau_ms": float(20.0 / np.log(10.0) / slope ** 2 * sigma_slope * 1e3),
            "noise_floor_db": float(20 * np.log10(max(floor, 1e-12) / ref)),
            "lo_db": lo_db, "hi_db": hi_db,
            "fit_region_t_s": np.round((pk + sel) / sr_hz, 6),
            "fit_region_db": np.round(y, 4),
            "envelope_db_per_ms": np.round(db[::sr_hz // 1000], 3)}


def measure(x, sr_hz: int, voice: str, analyser_id: str) -> tuple:
    """Four measurement records off one retained WAV, and the traces the fit
    ran on. Every record is built with `manifest.measurement`, whose keywords
    have no defaults -- an omitted field is a TypeError here, not a key quietly
    absent from a JSON file six months from now."""
    spec = ANALYSERS[analyser_id]
    trimmed = dv.trim_onset(x, sr_hz)
    onset_s = float((len(x) - len(trimmed)) / sr_hz)
    env = spec["envelope"](trimmed, sr_hz, voice)
    fit = dv.decay_fit(env, sr_hz)
    region = _fit_region(env, sr_hz)
    if region["ok"] and np.isfinite(fit["tau_ms"]):
        if abs(region["tau_ms"] - float(fit["tau_ms"])) > 1e-6:
            raise manifest.Refused(
                f"this file's fit-region reconstruction gives tau "
                f"{region['tau_ms']:.6f} ms and drum_verify.decay_fit gives "
                f"{fit['tau_ms']:.6f} ms. They are supposed to select the same "
                f"samples; until they do, the interval these records report is not "
                f"the interval the numbers came from")
    attack = float(dv.attack_ms(env, sr_hz))
    pk = int(np.argmax(env))
    crossed = np.nonzero(env[:pk + 1] > 0.02 * env[pk])[0]
    attack_t0 = float((crossed[0] if len(crossed) else 0) / sr_hz)
    lo_hz, hi_hz = dv.BAND[voice]
    nfft = 1 << 18
    n_spec = min(len(trimmed), int(0.5 * sr_hz))
    freqs, mag = dv.spectrum(trimmed[:n_spec], sr_hz, nfft=nfft)
    f0 = float(dv.peak_hz(freqs, mag, lo_hz, hi_hz))
    band = (freqs >= lo_hz) & (freqs <= hi_hz)
    out_of_band_db = 20 * np.log10(max(float(mag[freqs > hi_hz].mean()), 1e-20)
                                   / float(mag[band].max()))
    one_sample_ms = 1e3 / sr_hz
    bin_hz = sr_hz / nfft

    doc_ref = (f"document: docs/tr808-reference.md 12/14 carried by "
               f"model/drum_verify.py SPEC[{voice!r}] "
               f"(source: {dv.SPEC[voice]['source']}), "
               f"{provenance.file_sha(ROOT / 'docs' / 'tr808-reference.md')}; the "
               f"documented reference setting is {dv.REF_MAIN[voice][1]} "
               f"({dv.REF_MAIN[voice][0]}) and ours is one hit at accent {ACCENT} "
               f"with both drum buses at {BUS_GAIN}. NO RECORDING IS READ: the "
               f"comparand is the document's figure, so this is a document "
               f"identity and not a recording one")
    lock_ref = (f"model-lock: model/sound_report.py LOCKS measured at commit "
                f"{sr.LOCK}, "
                f"{provenance.file_sha(ROOT / 'model' / 'sound_report.py')}; matched "
                f"to the same solo render sound_report.py measures -- one {voice} "
                f"hit at accent {ACCENT} through the whole drum path at {dx.SR} Hz "
                f"with trim_onset applied before the envelope")
    channel = "mono: the drum buses summed through drums_fx.output_fx"
    resample = f"none: rendered and analysed at {sr_hz} Hz"
    normalisation = ("none (absolute full scale); the decay is measured relative to "
                     "the envelope's own peak, so a gain change cannot move it")
    env_filter = (f"none on the envelope path -- the envelope is a {spec['envelope_window']} "
                  f"of the full-band signal; drum_verify.BAND is applied to the "
                  f"spectrum rows only")

    def fit_quality(uncertainty, basis):
        return {"r2": round(float(fit["r2"]), 6),
                "span_db": round(float(fit["span_db"]), 4),
                "points": region.get("points"),
                "model": "least squares on the log-envelope, dB vs s",
                "uncertainty": uncertainty, "uncertainty_basis": basis,
                "noise_floor_db": (round(region["noise_floor_db"], 3)
                                   if region["ok"] else None),
                "noise_floor_treatment":
                    (f"the fit stops at the first sample {region['hi_db']} dB below "
                     f"the envelope peak; the render's last 50 ms sits at "
                     f"{region['noise_floor_db']:.1f} dB, so no sample inside the "
                     f"floor enters the fit") if region["ok"] else "the fit refused"}

    ms = [
        manifest.measurement(
            "fundamental", "Hz", round(f0, 4) if np.isfinite(f0) else None,
            analyser=analyser_id, analyser_version=spec["version"],
            method=(f"drum_verify.spectrum (Hann over the first "
                    f"{n_spec / sr_hz:.3f} s, {nfft}-point rfft) -> "
                    f"parabolic-interpolated maximum inside {lo_hz}-{hi_hz} Hz"),
            interval_s=[0.0, round(n_spec / sr_hz, 6)],
            channel=channel, resample_hz=resample,
            filter=(f"the maximum is restricted to drum_verify.BAND[{voice!r}] = "
                    f"{lo_hz}-{hi_hz} Hz; the signal itself is not filtered"),
            normalisation="none: a frequency, unaffected by level",
            fft={"transform": "real FFT of a Hann-windowed segment",
                 "window": f"Hann, {n_spec} samples ({n_spec / sr_hz * 1e3:.1f} ms)",
                 "nfft": nfft, "bin_hz": round(bin_hz, 6),
                 "hop": "one transform of one segment, not a spectrogram"},
            fit_quality={"model": "parabolic interpolation of the three bins around "
                                  "the maximum",
                         "uncertainty": round(bin_hz / 2.0, 4),
                         "uncertainty_basis": f"half the {bin_hz:.4f} Hz bin spacing "
                                              f"before interpolation improves it",
                         "noise_floor_db": round(out_of_band_db, 3),
                         "noise_floor_treatment": f"the maximum is taken inside the "
                                                  f"band, so the {out_of_band_db:.1f} "
                                                  f"dB mean level above it cannot "
                                                  f"become the peak"},
            reference_identity=doc_ref,
            why=None if np.isfinite(f0) else "no spectral maximum in the band",
            selection={"hit_index": 0, "onset_s": round(onset_s, 6),
                       "interval_basis": "from the trimmed onset, capped at 0.5 s so "
                                         "a long tail does not dilute the transform "
                                         "(drum_verify.measure's own rule)"}),
        manifest.measurement(
            "decay tau", "ms",
            round(float(fit["tau_ms"]), 4) if np.isfinite(fit["tau_ms"]) else None,
            analyser=analyser_id, analyser_version=spec["version"],
            method=(f"drum_verify.trim_onset -> {spec['envelope_window']} -> "
                    f"drum_verify.decay_fit, least squares on the log-envelope "
                    f"between -3 and -30 dB of its peak"),
            interval_s=([round(region["t0_s"], 6), round(region["t1_s"], 6)]
                        if region["ok"] else [0.0, 0.0]),
            channel=channel, resample_hz=resample, filter=env_filter,
            normalisation=normalisation,
            fft={"transform": "none: a time-domain envelope fit",
                 "window": spec["envelope_window"],
                 "nfft": "none: no transform is taken",
                 "hop": "none: the envelope is evaluated at every sample"},
            fit_quality=fit_quality(
                round(region["sigma_tau_ms"], 4) if region["ok"] else None,
                "1 sigma on tau, propagated from the least-squares covariance of "
                "the log-envelope slope" if region["ok"] else "the fit refused"),
            reference_identity=doc_ref,
            why=None if np.isfinite(fit["tau_ms"]) else region.get("why", "no fit"),
            selection={"hit_index": 0, "onset_s": round(onset_s, 6),
                       "interval_basis": "the samples between -3 and -30 dB below "
                                         "the envelope peak, which is what "
                                         "decay_fit fits -- not the whole tail"}),
        manifest.measurement(
            "T20", "ms",
            round(float(fit["t20_ms"]), 4) if np.isfinite(fit["t20_ms"]) else None,
            analyser=analyser_id, analyser_version=spec["version"],
            method=(f"drum_verify.trim_onset -> {spec['envelope_window']} -> the "
                    f"first sample at or below -20 dB of the envelope peak, in ms "
                    f"from the peak. A threshold crossing, not a fit"),
            interval_s=([round(region["peak_s"], 6),
                         round(region["peak_s"] + float(fit["t20_ms"]) * 1e-3, 6)]
                        if region["ok"] and np.isfinite(fit["t20_ms"]) else [0.0, 0.0]),
            channel=channel, resample_hz=resample, filter=env_filter,
            normalisation=normalisation,
            fft={"transform": "none: a -20 dB threshold crossing",
                 "window": spec["envelope_window"],
                 "nfft": "none: no transform is taken",
                 "hop": "none: the envelope is evaluated at every sample"},
            fit_quality={"model": "none: a crossing is located, not fitted",
                         "uncertainty": round(one_sample_ms, 4),
                         "uncertainty_basis": f"+/-1 sample ({one_sample_ms:.4f} ms); "
                                              f"the envelope's own smoothing window "
                                              f"is the larger term and is stated in "
                                              f"fft.window",
                         "noise_floor_db": (round(region["noise_floor_db"], 3)
                                            if region["ok"] else None),
                         "noise_floor_treatment": "-20 dB is far above this render's "
                                                  "floor, so the crossing is on "
                                                  "signal; a floor above -20 dB "
                                                  "would make this metric meaningless "
                                                  "and is recorded for that check"},
            reference_identity=lock_ref,
            why=None if np.isfinite(fit["t20_ms"]) else "no sample reached -20 dB",
            selection={"hit_index": 0, "onset_s": round(onset_s, 6),
                       "interval_basis": "the envelope peak to its first -20 dB "
                                         "crossing"}),
        manifest.measurement(
            "attack", "ms", round(attack, 4) if np.isfinite(attack) else None,
            analyser=analyser_id, analyser_version=spec["version"],
            method=(f"drum_verify.trim_onset -> {spec['envelope_window']} -> "
                    f"drum_verify.attack_ms: the 2 %-of-peak crossing of the "
                    f"envelope to the envelope's peak"),
            interval_s=[round(attack_t0, 6), round(pk / sr_hz, 6)],
            channel=channel, resample_hz=resample, filter=env_filter,
            normalisation=normalisation,
            fft={"transform": "none: an onset-to-peak time",
                 "window": spec["envelope_window"],
                 "nfft": "none: no transform is taken",
                 "hop": "none: the envelope is evaluated at every sample"},
            fit_quality={"model": "none: two locations, no fit",
                         "uncertainty": round(one_sample_ms, 4),
                         "uncertainty_basis": f"+/-1 sample ({one_sample_ms:.4f} ms) "
                                              f"on each end; the envelope window is "
                                              f"what sets the real resolution, and "
                                              f"is why this metric moves when the "
                                              f"envelope method changes",
                         "noise_floor_db": (round(region["noise_floor_db"], 3)
                                            if region["ok"] else None),
                         "noise_floor_treatment": "the 2 % threshold is 34 dB below "
                                                  "the peak; the measured floor is "
                                                  "the check that it is above it"},
            reference_identity=lock_ref,
            why=None if np.isfinite(attack) else "no envelope peak",
            selection={"hit_index": 0, "onset_s": round(onset_s, 6),
                       "interval_basis": "the 2 %-of-peak crossing to the peak"}),
    ]
    traces = {}
    if region["ok"]:
        # The numbers the verdict rests on, not a summary of them. The full-rate
        # envelope is deliberately NOT retained: it is re-derivable from the
        # retained WAV by the recorded method, whereas the fit REGION depends on
        # the estimator and is what the next estimator has to be argued against.
        traces = {"fit_region_t_s": region["fit_region_t_s"],
                  "fit_region_db": region["fit_region_db"],
                  "envelope_db_per_ms": region["envelope_db_per_ms"]}
    return ms, traces


def do_analyse(root, render_manifest: dict, analyser_id: str,
               inject: str = "") -> dict:
    root = pathlib.Path(root)
    m, x, sr_hz = manifest.load_render(render_manifest["render_id"],
                                       runs_dir=root / "runs")
    ms, traces = measure(x, sr_hz, m["config"]["voice"], analyser_id)
    if inject == "MEASUREMENT_NO_UNITS":
        ms = [dict(r) for r in ms]
        del next(r for r in ms if r["name"] == "T20")["units"]
    rec = manifest.analyse(m, ms, analyser=analyser_id,
                           analyser_version=ANALYSERS[analyser_id]["version"],
                           config={"status": ANALYSERS[analyser_id]["status"],
                                   "drum_verify_sha256":
                                       provenance.file_sha(ROOT / "model" / "drum_verify.py"),
                                   "audio_measure_sha256":
                                       provenance.file_sha(ROOT / "model" / "audio_measure.py"),
                                   "read": m["wav_path"],
                                   "read_sha256": m["wav_sha256"]},
                           jobs_dir=root / "jobs", traces=traces)
    pr.mark(root / "jobs" / rec["job_id"], _retention_of(root, m["render_id"]))
    return rec


def _retention_of(root, render_id: str) -> str:
    return pr.read_mark(pathlib.Path(root) / "runs" / render_id) or "smoke"


# ===========================================================================
# accept
# ===========================================================================
def ledger_for(root, seed: bool = True) -> pathlib.Path:
    """A scratch ledger, seeded from the tracked one. See the module docstring:
    seeded so a bound change is still detected on a fresh checkout, scratch so
    parallel jobs cannot lose each other's update and a verification run does
    not dirty a tracked file."""
    p = pathlib.Path(root) / "bounds-history.json"
    if seed and not p.exists() and BOUNDS_TRACKED.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(BOUNDS_TRACKED, p)
    return p


def do_accept(root, analysis: dict, inject: str = "") -> dict:
    root = pathlib.Path(root)
    voice = analysis["case_id"].split("-")[0]
    rec = manifest.accept(analysis, criteria_for(voice, inject),
                          jobs_dir=root / "jobs", history_path=ledger_for(root))
    pr.mark(root / "jobs" / rec["job_id"], _retention_of(root, rec["render_id"]))
    return rec


def outcome_of(verdict: dict) -> tuple:
    """(state, exit code). `manifest.accept` reports per metric; the run's own
    outcome is the worst of them, and a recorded bound change is its own
    non-zero: a verdict that moved because the ruler moved is not a verdict
    that passed, even when every metric is inside its bound."""
    order = {"pass": 0, "fail": 1, "no verdict": 2}
    worst = "pass"
    for row in verdict["verdicts"].values():
        if order.get(row["state"], 2) > order[worst]:
            worst = row["state"]
    if verdict["bound_changes"]:
        return f"{worst} (BOUND MOVED)", max(1, order[worst])
    return worst, order[worst]


# ===========================================================================
# attribution, across every metric
# ===========================================================================
def _raw_delta(a: dict, b: dict, name: str, tol: float) -> dict:
    """The arithmetic `diagnose()` refused to attribute, with `saw` computed
    against the metric's own tolerance. Empty when either side has no value --
    a refusal has no distance, not a zero one (`tools/run_case.py`'s rule)."""
    va = a["measurements"].get(name, {}).get("value")
    vb = b["measurements"].get(name, {}).get("value")
    if not isinstance(va, (int, float)) or not isinstance(vb, (int, float)):
        return {}
    delta = vb - va
    return {"value_a": va, "value_b": vb, "delta": delta,
            "saw": "MOVED" if abs(delta) > tol else "BLIND"}


def attribute(a: dict, b: dict) -> dict:
    """`manifest.diagnose` per metric, plus MOVED/BLIND against each metric's
    own tolerance.

    Why the per-metric column: a metric that never moves for any defect is
    decoration wearing the costume of coverage (docs/verification-rules.md 4),
    and "the measurement changed" is a different claim when one of four metrics
    saw it than when all four did."""
    voice = a["case_id"].split("-")[0]
    rows, verdicts, refusals = {}, set(), []
    for name in METRICS:
        tol = bound_for(voice, name)["tolerance"]
        try:
            d = manifest.diagnose(a, b, name)
        except manifest.Refused as e:
            refusals.append(str(e))
            # REFUSED is about ATTRIBUTION, not about arithmetic. The delta is
            # still reported -- unattributed, and marked so -- because the most
            # important fact about the unattributable pair here is that the two
            # defects PARTLY CANCEL on T20: -101 ms of wrong ruler and -122 ms
            # of wrong sound leave -21 ms, inside T20's own 36 ms tolerance.
            # Suppressing the number would hide the very thing that makes
            # refusing the right answer rather than a cop-out.
            rows[name] = dict({"metric": name, "verdict": "REFUSED",
                               "why": str(e), "tolerance": tol,
                               "attributable": False},
                              **_raw_delta(a, b, name, tol))
            continue
        d["tolerance"] = tol
        d["saw"] = "MOVED" if abs(d["delta"]) > tol else "BLIND"
        d["attributable"] = True
        verdicts.add(d["verdict"])
        rows[name] = d
    attribution = (sorted(verdicts)[0] if len(verdicts) == 1
                   else ("REFUSED: " + refusals[0] if refusals
                         else "inconsistent across metrics: " + ", ".join(sorted(verdicts))))
    return {"before": a["job_id"], "after": b["job_id"],
            "renders": [a["render_id"], b["render_id"]],
            "analysers": [f"{a['analyser']}@{a['analyser_version']}",
                          f"{b['analyser']}@{b['analyser_version']}"],
            "attributable": not refusals,
            "attribution": attribution, "metrics": rows,
            "recorded_at": provenance.now()}


# ===========================================================================
# the demonstration
# ===========================================================================
def _print_analysis(rec: dict, voice: str) -> None:
    print(f"  analysis {rec['job_id']}")
    print(f"  analyser {rec['analyser']}@{rec['analyser_version']}  "
          f"render {rec['render_id']}")
    for name in METRICS:
        m = rec["measurements"][name]
        b = bound_for(voice, name)
        print(f"    {name:<12} {m['value']!s:>10} {m['units']:<3} bound "
              f"{b['value']!s:>9} +/-{b['tolerance']:<6} {b['kind']:<6} "
              f"q {str(m['fit_quality'].get('r2', m['fit_quality']['model']))[:34]}")


def _print_verdict(v: dict) -> None:
    state, code = outcome_of(v)
    print(f"  accept -> {state} (code {code})")
    for name, row in v["verdicts"].items():
        extra = (f"{row.get('value')} in [{row['bounds']['lo']:.4g}, "
                 f"{row['bounds']['hi']:.4g}]" if "value" in row else row.get("why", ""))
        print(f"    {name:<12} {row['state']:<10} {extra}")
    for ch in v["bound_changes"]:
        print(f"    BOUND MOVED  {ch['metric']}: "
              f"[{ch['previous']['lo']:.4g}, {ch['previous']['hi']:.4g}] -> "
              f"[{ch['new']['lo']:.4g}, {ch['new']['hi']:.4g}]")


def demo(root, out) -> int:
    print("=" * 94)
    print("render / analyse / accept on a REAL case -- BD decay (issues #68, #259)")
    print("=" * 94)
    clean = do_render(root, "BD", None, "reference-fixture")
    print(f"\n[render 1] the kit as it ships")
    print(f"           {clean['render_id']}")
    print(f"           raw.wav {clean['wav_sha256']}, {clean['n_samples']} samples, "
          f"peak {clean['requested_peak']:.6f}, clipped {clean['clipped_samples']}")
    print(f"           retained {pr.RETENTION_DAYS['reference-fixture']} days as "
          f"reference-fixture")

    v1 = do_analyse(root, clean, "drum-decay-moving-average")
    print("\n[analyse 1] the WITHDRAWN estimator")
    _print_analysis(v1, "BD")
    a1 = do_accept(root, v1)
    _print_verdict(a1)

    v2 = do_analyse(root, clean, "drum-decay-rms")
    print("\n[analyse 2] the CORRECTED estimator, re-reading the SAME retained WAV")
    print(f"            manifest.load_render re-verified {clean['wav_sha256']} "
          f"before any estimator ran; no render happened")
    _print_analysis(v2, "BD")
    a2 = do_accept(root, v2)
    _print_verdict(a2)

    broken = do_render(root, "BD", "bd-decay-short", "smoke")
    v2b = do_analyse(root, broken, "drum-decay-rms")
    print(f"\n[render 2] the SOUND defect 'bd-decay-short': "
          f"{broken['config']['variant_means']}")
    print(f"           {broken['render_id']}")
    print("[analyse 3] the CORRECTED estimator on the broken render")
    _print_analysis(v2b, "BD")
    a2b = do_accept(root, v2b)
    _print_verdict(a2b)

    deltas = [("v1 -> v2 on the SAME retained audio", attribute(v1, v2)),
              ("v2 on clean -> v2 on the broken render", attribute(v2, v2b)),
              ("v1 on clean -> v2 on the broken render", attribute(v1, v2b))]
    print("\n" + "=" * 94)
    print("ATTRIBUTION -- did the sound change, or only the measurement?")
    print("=" * 94)
    for title, d in deltas:
        print(f"\n  {title}")
        print(f"    => {d['attribution'][:150]}")
        for name, row in d["metrics"].items():
            if "delta" in row:
                print(f"       {name:<12} {row['value_a']!s:>10} -> "
                      f"{row['value_b']!s:<10} delta {row['delta']:>10.4f}  tol "
                      f"{row['tolerance']:>6}  {row['saw']}"
                      f"{'' if row['attributable'] else '  (delta only; not attributed)'}")

    ok = (outcome_of(a2)[0] == "pass" and outcome_of(a1)[0] == "fail"
          and outcome_of(a2b)[0] == "fail"
          and deltas[0][1]["attribution"] == "measurement changed"
          and deltas[1][1]["attribution"] == "sound changed"
          and not deltas[2][1]["attributable"])
    print("\n" + "-" * 94)
    print(f"  the corrected estimator on the clean render   {outcome_of(a2)[0]}")
    print(f"  the withdrawn estimator on the SAME audio     {outcome_of(a1)[0]}"
          f"    <- the measurement was wrong, not the sound")
    print(f"  the corrected estimator on the broken render  {outcome_of(a2b)[0]}"
          f"    <- the sound was wrong, not the measurement")
    print(f"  the two together                             "
          f"{'REFUSED' if not deltas[2][1]['attributable'] else 'ATTRIBUTED (WRONG)'}"
          f" <- unattributable, and said so")
    print(f"  T20 under both defects at once: delta "
          f"{deltas[2][1]['metrics']['T20'].get('delta', float('nan')):.2f} ms, "
          f"{deltas[2][1]['metrics']['T20'].get('saw', 'REFUSED')} -- "
          f"they partly cancel")
    print("-" * 94)

    if out:
        out = pathlib.Path(out)
        out.mkdir(parents=True, exist_ok=True)
        for name, rec in (("render-clean", clean), ("render-bd-decay-short", broken),
                          ("analysis-v1-on-clean", v1), ("analysis-v2-on-clean", v2),
                          ("analysis-v2-on-broken", v2b),
                          ("verdict-v1-on-clean", a1), ("verdict-v2-on-clean", a2),
                          ("verdict-v2-on-broken", a2b)):
            (out / f"{name}.json").write_text(json.dumps(rec, indent=1, default=str)
                                              + "\n")
        for slug, (title, d) in zip(("measurement-only", "sound-only",
                                     "unattributable"), deltas):
            (out / f"delta-{slug}.json").write_text(
                json.dumps(dict(d, comparison=title), indent=1, default=str) + "\n")
        # The raw WAV goes with the manifests, not just its hash. "Retroactive
        # analysis is the whole point -- when the next estimator turns out to be
        # wrong, we re-run it on the old audio instead of losing the history"
        # (issue #68), and an evidence directory of nothing but JSON is summary
        # statistics wearing a manifest. 211 kB for the clean render; the broken
        # one is reproducible from its manifest's `variant` and is not kept.
        shutil.copyfile(
            pathlib.Path(root) / "runs" / clean["render_id"] / clean["wav_path"],
            out / "raw-clean.wav")
        print(f"  manifests and raw-clean.wav written to {out}")
    print(f"\n{'PASS' if ok else 'FAIL'}: the demonstration "
          f"{'held' if ok else 'DID NOT hold'}")
    return 0 if ok else 1


# ===========================================================================
# the controls
# ===========================================================================
GUARDS = {
    "missing required field": "measurement field",
    "no rationale": "bound rationale",
    "does not match the hash": "retained WAV hash",
    "exact silence": "render precondition",
    "BOUND MOVED": "bound-change ledger",
}


def which_guards(text: str) -> list:
    return [label for key, label in GUARDS.items() if key in text]


def controls(root) -> int:
    """Every injection, the stage that must refuse it, and which guard caught it.

    The three conditions (docs/verification-rules.md 5): the clean run must
    pass, the mutant must actually activate, and the intended assertion must be
    the one that fails. The clean run is asserted here, not assumed -- a control
    that cannot run looks exactly like one that works."""
    root = pathlib.Path(root)
    clean_render = do_render(root, "BD", None, "smoke")
    clean = do_accept(root, do_analyse(root, clean_render, "drum-decay-rms"))
    state, code = outcome_of(clean)
    print(f"clean: accept -> {state} (code {code})")
    if state != "pass":
        print("NO VERDICT: the clean run does not pass, so no injection below can "
              "be told apart from the apparatus being broken")
        return 2

    rows, bad = [], []
    for inject, (what, stage) in sorted(INJECTIONS.items()):
        sub = root / f"control-{inject.lower()}"
        outcome, detail = "not refused", ""
        try:
            if inject == "RENDER_SILENCE":
                do_render(sub, "BD", None, "smoke", silent=True)
            elif inject == "TAMPER_RETAINED_AUDIO":
                r = do_render(sub, "BD", None, "smoke")
                tamper(sub, r)
                do_analyse(sub, r, "drum-decay-rms")
            else:
                r = do_render(sub, "BD", None, "smoke")
                a = do_analyse(sub, r, "drum-decay-moving-average", inject=inject)
                if inject == "BOUND_SILENT_CHANGE":
                    # The whitewash is only a whitewash against a bound that was
                    # already there, so the baseline is ESTABLISHED here rather
                    # than assumed present. Condition 2 of the three
                    # (docs/verification-rules.md 5): the mutant has to actually
                    # activate. Run in a fresh root with no prior bound, this
                    # injection moved nothing and the control proved nothing --
                    # which is how it read green for a while.
                    base = do_accept(sub, a)
                    if outcome_of(base)[0] != "fail":
                        raise manifest.Refused(
                            f"the baseline accept came back {outcome_of(base)[0]!r}, "
                            f"not 'fail' -- the withdrawn estimator is supposed to "
                            f"be outside T20's bound before the bound is moved onto "
                            f"it, so there is nothing for the whitewash to whitewash")
                v = do_accept(sub, a, inject=inject)
                s, c = outcome_of(v)
                if v["bound_changes"]:
                    outcome, detail = "REFUSED", "BOUND MOVED: " + json.dumps(
                        v["bound_changes"][0]["previous"]) + " -> " + json.dumps(
                        v["bound_changes"][0]["new"])
                    if v["verdicts"]["T20"]["state"] != "pass":
                        raise manifest.Refused(
                            f"the whitewashed T20 came back "
                            f"{v['verdicts']['T20']['state']!r} rather than 'pass' -- "
                            f"a bound move that does not turn the defect green does "
                            f"not demonstrate the thing this control is for")
                else:
                    outcome, detail = f"accepted: {s}", ""
        except manifest.Refused as e:
            outcome, detail = "REFUSED", str(e)
        fired = which_guards(detail)
        rows.append((inject, stage, outcome, fired, what))
        if outcome != "REFUSED" or not fired:
            bad.append(f"{inject}: {stage} came back {outcome!r} and guards {fired} "
                       f"-- an injection that is not refused is a hole in the scheme")

    labels = sorted(set(GUARDS.values()))
    print("\ninjected control          stage    outcome   "
          + " ".join(f"{l:<19}" for l in labels))
    for inject, stage, outcome, fired, _ in rows:
        cells = " ".join(f"{('CAUGHT' if l in fired else 'BLIND'):<19}" for l in labels)
        print(f"{inject:<25} {stage:<8} {outcome[:9]:<9} {cells}")
    print("\nwhat each one is:")
    for inject, _, _, _, what in rows:
        print(f"  {inject:<24} {what}")
    for b in bad:
        print(f"\n  {b}")
    print(f"\n{len(rows) - len(bad)}/{len(rows)} injected controls were REFUSED by "
          f"the stage that owns them")
    return 1 if bad else 0


def tamper(root, render_manifest: dict) -> None:
    p = (pathlib.Path(root) / "runs" / render_manifest["render_id"]
         / render_manifest["wav_path"])
    b = bytearray(p.read_bytes())
    b[-3] ^= 0x01
    p.write_bytes(bytes(b))


# ===========================================================================
def _read(root, kind: str, ident: str) -> dict:
    name = {"render": ("runs", "manifest.json"), "analyse": ("jobs", "analysis.json"),
            "accept": ("jobs", "accept.json")}[kind]
    p = pathlib.Path(root) / name[0] / ident / name[1]
    if not p.exists():
        raise manifest.Refused(f"no {kind} record at {p}")
    return json.loads(p.read_text())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["render", "analyse", "accept", "compare",
                                    "demo", "controls", "list"])
    ap.add_argument("--root", default=str(ROOT / "build" / "provenance"),
                    help="where runs/ and jobs/ live. Plain files; no service.")
    ap.add_argument("--voice", default="BD")
    ap.add_argument("--variant", default=None,
                    help="a model/sound_report.py injection: a SOUND change")
    ap.add_argument("--retention", default="smoke", choices=sorted(pr.RETENTION_DAYS))
    ap.add_argument("--render", default="")
    ap.add_argument("--analyser", default="drum-decay-rms", choices=sorted(ANALYSERS))
    ap.add_argument("--analysis", default="")
    ap.add_argument("--before", default="")
    ap.add_argument("--after", default="")
    ap.add_argument("--inject", default="", choices=[""] + sorted(INJECTIONS))
    ap.add_argument("--out", default=None, help="also write the records here")
    ap.add_argument("--record-bounds", action="store_true",
                    help="copy this run's bound ledger over the tracked "
                         "spec/acceptance-bounds-history.json. The only thing that "
                         "moves the committed record; it shows up as a diff.")
    a = ap.parse_args(argv)
    root = pathlib.Path(a.root)
    root.mkdir(parents=True, exist_ok=True)

    try:
        rc = _dispatch(a, root, ap)
    except manifest.Refused as e:
        print(f"REFUSED: {e}")
        return 2
    if a.record_bounds:
        led = ledger_for(root, seed=False)
        if led.exists():
            BOUNDS_TRACKED.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(led, BOUNDS_TRACKED)
            print(f"recorded {led} -> {BOUNDS_TRACKED.relative_to(ROOT)}")
    return rc


def _dispatch(a, root, ap) -> int:
    if a.cmd == "list":
        print("analysers:")
        for k, v in sorted(ANALYSERS.items()):
            print(f"  {k:<28} {v['version']:<14} {v['status'][:56]}")
        print("sound variants (model/sound_report.py injections):")
        for k, (d, voices, _) in sorted(sr.INJECTIONS.items()):
            print(f"  {k:<28} {','.join(voices):<10} {d[:56]}")
        print("record/bound/apparatus injections:")
        for k, (d, stage) in sorted(INJECTIONS.items()):
            print(f"  {k:<28} {stage:<8} {d[:70]}")
        print("retention classes:")
        for k, v in sorted(pr.RETENTION_DAYS.items()):
            print(f"  {k:<28} {v:>3} days   {pr.WHY[k]}")
        return 0
    if a.cmd == "demo":
        return demo(root, a.out)
    if a.cmd == "controls":
        return controls(root)
    if a.cmd == "render":
        m = do_render(root, a.voice, a.variant, a.retention,
                      silent=a.inject == "RENDER_SILENCE")
        print(json.dumps({k: m[k] for k in ("render_id", "case_id", "wav_sha256",
                                            "n_samples", "clipped_samples",
                                            "trace_paths")}, indent=1))
        return 0
    if a.cmd == "analyse":
        if not a.render:
            ap.error("--render <render id> is required: analyse reads a retained render")
        m = _read(root, "render", a.render)
        if a.inject == "TAMPER_RETAINED_AUDIO":
            tamper(root, m)
        rec = do_analyse(root, m, a.analyser,
                         inject="" if a.inject == "TAMPER_RETAINED_AUDIO" else a.inject)
        print(json.dumps({"job_id": rec["job_id"], "render_id": rec["render_id"],
                          "render_wav_sha256": rec["render_wav_sha256"],
                          "analyser": f"{rec['analyser']}@{rec['analyser_version']}",
                          "values": {k: v["value"]
                                     for k, v in rec["measurements"].items()}},
                         indent=1))
        return 0
    if a.cmd == "accept":
        if not a.analysis:
            ap.error("--analysis <job id> is required")
        v = do_accept(root, _read(root, "analyse", a.analysis), inject=a.inject)
        _print_verdict(v)
        return outcome_of(v)[1]
    if a.cmd == "compare":
        if not (a.before and a.after):
            ap.error("--before and --after are both required")
        d = attribute(_read(root, "analyse", a.before), _read(root, "analyse", a.after))
        print(json.dumps(d, indent=1, default=str))
        if a.out:
            pathlib.Path(a.out).write_text(json.dumps(d, indent=1, default=str) + "\n")
        return 0 if d["attributable"] else 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
