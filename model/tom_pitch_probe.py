#!/usr/bin/env python3
"""The TR-808 toms' diode pitch drop, measured from a recording.

`spec/NUMERIC-CONTRACT.md` 15.7.1 ships the drop as a coefficient sequence:
f0 starts above the small-signal value and relaxes over **60 ms**, the excess
scaled by accent. When this module was written 15.7.1 shipped that excess at
**x1.7** and `docs/tr808-reference.md` 4 marked the magnitude **[inferred]** --
the service notes verify that the drop exists, not how big it is. This module
measured it (x1.06 / x1.14 / x1.24 by accent, #110); #154 shipped the
correction, and 4 now carries the measured figure with the same tags. The
x1.7 references below are the DEFECT this instrument was pointed at, kept
because recovering it to 0.41 % is one of the probe's own validation cases.

WHAT IT MEASURES, and why this way
----------------------------------
The 808 tom is a bridged-T resonator (Q ~ 25) kicked by a ~1 ms pulse. With
the germanium diodes conducting the foot resistance collapses and f0 rises;
as the ring decays the diodes stop conducting and f0 relaxes back. So the
signal is a decaying near-sinusoid whose frequency sweeps down at the start.

Frequency comes from **interpolated same-direction zero crossings**, one
estimate per full period. Same-direction (rise-to-rise and fall-to-fall,
tracked as two interleaved series) because a full period cancels a DC offset
and any even-order asymmetry to first order; a half-period estimator does not,
and on these files it produces an alternating long/short artefact that reads
exactly like a pitch drop.

An exponential envelope does NOT move the zeros of a sinusoid, so the decay
itself introduces no bias. A second additive component does move them, which
is the confound this file is most careful about -- see `accent_is_a_gain`
below and `test_tom_pitch_probe.py`.

WHAT IT REFUSES
---------------
  * a file whose settled window holds fewer than `MIN_SETTLED` clean periods
  * a measured excess smaller than `SNR_SIGMA` x the estimator's own scatter
    in that file's settled window (the floor is measured per file, not quoted
    as a constant -- issue #92)
  * periods overlapping the excitation pulse, or outside a generous
    [0.45, 2.4] x f_settled sanity band; both are counted and reported

REFUSED is a first-class outcome and is not a statement about the recording.
"""
from __future__ import annotations
import argparse, json, math, pathlib, sys, warnings
import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import audio_measure as am                                            # noqa: E402

SR_EXPECTED = 44100
MIN_SETTLED = 8          # clean periods needed in the settled window
SNR_SIGMA = 3.0          # excess must beat this many sigma of the settled scatter
F_BAND = (0.45, 2.4)     # sanity band on a period's implied frequency, x f_settled
HYST_FRAC = 0.20         # Schmitt hysteresis, fraction of the local envelope
EXTRAP_MAX = 4.0         # fitted onset excess / largest observed excess, max
SETTLED_HEADROOM_DB = 40.0   # a settled period must sit this far above the file's floor
BP_BAND = (0.75, 2.05)   # band-pass edges, x f_coarse. The upper edge clears x1.7
SETTLED_XCHECK = 0.02    # settled f0 must agree with the spectral peak to this fraction
# A competing line is REPORTED, not gated. A -30 dB gate measured over the
# onset is UNSATISFIABLE: a genuine x1.7 sweep spreads its own energy and reads
# as a -20 dB neighbour, so the gate refuses the very thing it exists to
# protect (CLAUDE.md: an unsatisfiable gate is worse than no gate). The
# neighbour level is therefore measured over the SETTLED window, where a sweep
# contributes nothing, and reported. The artefact bound that actually governs
# this measurement is empirical and comes from the accent-A recordings.


#: This probe's validated domain, as data rather than as the table in
#: `docs/tom-pitch-drop-measurement.md` (#115). The third of the three ad-hoc
#: validation measurements that issue asks to relocate INTO the estimator: the
#: 0.41 % figure was reachable only by reading a docstring paragraph and a row
#: of a markdown table.
#:
#: `measure()` returns a dict with its own `verdict`/`why`, not an `Estimate`,
#: so this declaration is inspectable evidence rather than a gate -- every
#: bound below is already enforced inside `measure()` by the named constant,
#: and duplicating the check here would be a second place for it to drift.
PITCH_DROP_DOMAIN = am.register_domain(am.ValidatedDomain(
    estimator="tom_pitch_probe.measure",
    axes=(
        am.DomainAxis(am.AXIS_SIGNAL_CLASS, values=("decaying",),
                      basis="a 2-pole resonator's free ring. The measurement IS "
                            "the frequency trajectory of a decay; a stationary "
                            "tone has no drop to recover"),
        am.DomainAxis(am.AXIS_SNR, lo=SETTLED_HEADROOM_DB,
                      units="dB above THIS file's own measured noise floor",
                      basis="a settled period must clear the file's own floor "
                            "(noise_floor_dbfs) by this much, and the excess must "
                            "additionally beat SNR_SIGMA = 3 sigma of the "
                            "per-period scatter measured in that same file's "
                            "settled window -- 0.25-0.75 Hz on LT/MT, 1.9-2.1 Hz "
                            "on HT. The floor is measured per file, never quoted "
                            "(#92). A -20 dB pink rumble, worse than any measured, "
                            "does not fake a drop"),
        am.DomainAxis(am.AXIS_RECORD_LENGTH, lo=float(MIN_SETTLED),
                      units="clean settled periods",
                      basis="fewer, and there is no settled f0 to measure the "
                            "excess against; refused, with the count"),
        am.DomainAxis(am.AXIS_PARTIAL_SEPARATION, enforced=False,
                      basis="a competing line is REPORTED, not gated, and "
                            "deliberately: a -30 dB gate over the onset is "
                            "UNSATISFIABLE, because a genuine x1.7 sweep spreads "
                            "its own energy and reads as a -20 dB neighbour. "
                            "Measured cost when one is present: 5 of 12 refused, "
                            "worst survivor +-0.045 of the ratio"),
        am.DomainAxis(am.AXIS_DETUNING, lo=-SETTLED_XCHECK, hi=SETTLED_XCHECK,
                      units="fraction the settled f0 may differ from the spectral "
                            "peak",
                      basis="the settled f0 is cross-checked against an independent "
                            "spectral estimate on the same window; disagreement "
                            "past this refuses rather than picking one"),
        am.DomainAxis(am.AXIS_DECAY_RATE, lo=1.0, units="carrier periods per "
                      "amplitude tau",
                      enforced=False,
                      basis="measured, not assumed: the x1.05-x1.40 recoveries hold "
                            "to 0.46 % over 60 ms drops and degrade to 5.6 % over "
                            "25 ms drops, whose worst corner is LT with tau = "
                            "8.3 ms -- SHORTER than one period of its 90 Hz "
                            "carrier. That corner is the bound"),
    ),
    worst_error="0.41 % of the excess recovering the contract's own x1.7 / 60 ms "
                "drop; <= 0.46 % over x1.05-x1.40 at 60 ms; |R-1| < 0.0002 on a "
                "null, i.e. it invents no drop",
    evidence=("model/tom_pitch_probe.py `synth_tom` gate cases",
              "model/test_tom_pitch_probe.py",
              "docs/tom-pitch-drop-measurement.md 'The gate: recover a known "
              "drop before measuring an unknown one'"),
    notes="The x1.7 in this file's own references is the DEFECT the instrument "
          "was pointed at (15.7.1 shipped it; #110 measured x1.06/x1.14/x1.24 "
          "and #154 corrected the contract). Recovering x1.7 to 0.41 % is a "
          "validation case, not a claim about the machine.",
))


# ------------------------------------------------------------------ io ------

def read_wav(path: str) -> tuple[int, np.ndarray]:
    """WAV to float in [-1, 1). 24-bit arrives from scipy as int32 (left
    aligned), 16-bit as int16; stereo is averaged. Nothing is resampled."""
    from scipy.io import wavfile
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        sr, raw = wavfile.read(path)
    if raw.dtype == np.int32:
        x = raw.astype(np.float64) / 2.0 ** 31
    elif raw.dtype == np.int16:
        x = raw.astype(np.float64) / 2.0 ** 15
    elif raw.dtype in (np.float32, np.float64):
        x = raw.astype(np.float64)
    else:
        raise ValueError(f"{path}: unsupported sample format {raw.dtype}")
    if x.ndim > 1:
        x = x.mean(axis=1)
    return int(sr), x


# ------------------------------------------------------- period tracking ----

def bandpass(x: np.ndarray, sr: int, f0: float, lo: float = BP_BAND[0],
             hi: float = BP_BAND[1], order: int = 2) -> np.ndarray:
    """Zero-phase Butterworth band-pass around f0. Zero-phase so the sweep is
    not biased by a frequency-dependent group delay; the cost is pre-ringing
    before the onset, which `test_tom_pitch_probe.py` measures rather than
    assumes. The upper edge clears x1.7 so the band never truncates the thing
    being measured."""
    from scipy.signal import butter, filtfilt
    nyq = sr / 2.0
    lo_hz, hi_hz = max(lo * f0, 1.0), min(hi * f0, 0.95 * nyq)
    if hi_hz <= lo_hz:
        return x
    b, a = butter(order, [lo_hz / nyq, hi_hz / nyq], btype="band")
    pad = min(len(x) - 1, 3 * max(len(a), len(b)) * 10)
    return filtfilt(b, a, x, padlen=pad)


def spectral_f0(x: np.ndarray, sr: int, t0: float, t1: float,
                lo_hz: float = 40.0, hi_hz: float = 700.0) -> float:
    """Strongest spectral line in [lo_hz, hi_hz] over [t0, t1), parabolically
    interpolated. Independent of the zero-crossing path, so a disagreement
    between the two is a reason to refuse."""
    i0, i1 = int(t0 * sr), int(t1 * sr)
    seg = x[max(i0, 0):min(i1, len(x))]
    if len(seg) < 64:
        return float("nan")
    nfft = 1 << 18
    w = np.hanning(len(seg))
    S = np.abs(np.fft.rfft(seg * w, nfft))
    fr = np.fft.rfftfreq(nfft, 1.0 / sr)
    m = (fr >= lo_hz) & (fr <= hi_hz)
    if not m.any():
        return float("nan")
    k = int(np.argmax(np.where(m, S, 0.0)))
    if 0 < k < len(S) - 1:
        a, b, c = np.log(S[k - 1] + 1e-30), np.log(S[k] + 1e-30), np.log(S[k + 1] + 1e-30)
        d = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) != 0 else 0.0
        return float(fr[k] + d * (fr[1] - fr[0]))
    return float(fr[k])


def local_envelope(x: np.ndarray, sr: int, f0: float) -> np.ndarray:
    """Sliding max of |x| over ~1.5 periods -- the reference for the hysteresis."""
    from scipy.ndimage import maximum_filter1d
    w = max(3, int(1.5 * sr / max(f0, 1.0)))
    return maximum_filter1d(np.abs(x), size=w, mode="nearest")


def crossings(x: np.ndarray, rising: bool, sr: int | None = None,
              f0: float | None = None, hyst: float = HYST_FRAC) -> np.ndarray:
    """Fractional sample indices of same-direction zero crossings, behind a
    Schmitt trigger.

    Without the hysteresis, dither riding on the signal near a zero produces
    THREE crossings where there is one, and the resulting short "period" reads
    as 2 x f0 -- which a [0.45, 2.4] sanity band happily admits, because the
    band has to stay open to a genuine x1.7. That defect put 180 Hz rows in a
    90 Hz tom's settled window and inflated its scatter to 20 Hz.

    The trigger is a SEQUENTIAL state machine, not a windowed test: after a
    rising transition the signal must fall below -h before another rising
    transition is accepted, so crossings strictly alternate and a glitch triple
    collapses to one. h is `hyst` x the local envelope. The time recorded is
    still the interpolated zero, so the estimate itself is unbiased -- only the
    spurious crossings are gone.
    """
    t_up, t_dn = _raw_crossings(x)
    if sr is None or f0 is None or hyst <= 0:
        return t_up if rising else t_dn
    up, dn = _schmitt(x, sr, f0, hyst, t_up, t_dn)
    return up if rising else dn


def _raw_crossings(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    s = np.sign(x)
    s[s == 0] = 1.0
    d = np.diff(s)
    out = []
    for sel in (d > 0, d < 0):
        i = np.nonzero(sel)[0]
        if len(i) == 0:
            out.append(np.array([]))
            continue
        den = x[i + 1] - x[i]
        den = np.where(den == 0, np.inf, den)
        out.append(i + (-x[i]) / den)
    return out[0], out[1]


def _schmitt(x: np.ndarray, sr: int, f0: float, hyst: float,
             t_up: np.ndarray, t_dn: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    env = local_envelope(x, sr, f0)
    h = hyst * env
    up, dn = [], []
    state = 1 if x[0] > 0 else -1
    for n in range(len(x)):
        hn = h[n]
        if hn <= 0:
            continue
        if state < 0 and x[n] > hn:
            k = np.searchsorted(t_up, n, side="right") - 1
            if k >= 0 and (not up or t_up[k] > up[-1]):
                up.append(float(t_up[k]))
            state = 1
        elif state > 0 and x[n] < -hn:
            k = np.searchsorted(t_dn, n, side="right") - 1
            if k >= 0 and (not dn or t_dn[k] > dn[-1]):
                dn.append(float(t_dn[k]))
            state = -1
    return np.array(up), np.array(dn)


def onset_index(x: np.ndarray, frac: float = 0.01) -> int:
    pk = np.abs(x).max()
    if pk <= 0:
        return 0
    nz = np.nonzero(np.abs(x) > frac * pk)[0]
    return int(nz[0]) if len(nz) else 0


def noise_floor_dbfs(x: np.ndarray, sr: int, tail_s: float = 0.03) -> float:
    n = max(int(tail_s * sr), 64)
    tail = x[-n:]
    return 20.0 * math.log10(max(float(np.sqrt(np.mean(tail ** 2))), 1e-20))


def raw_periods(x: np.ndarray, sr: int, f0: float | None = None) -> list[dict]:
    """Every full period from both crossing directions, unfiltered."""
    out = []
    for rising in (True, False):
        t = crossings(x, rising, sr, f0)
        if len(t) < 2:
            continue
        for k in range(len(t) - 1):
            a, b = t[k], t[k + 1]
            per = (b - a) / sr
            if per <= 0:
                continue
            lo, hi = int(math.ceil(a)), int(math.floor(b))
            amp = float(np.abs(x[lo:hi + 1]).max()) if hi > lo else 0.0
            out.append({"t_start": a / sr, "t_mid": (a + b) / 2 / sr,
                        "f": 1.0 / per, "amp": amp, "dir": "rise" if rising else "fall"})
    out.sort(key=lambda d: d["t_mid"])
    return out


# ------------------------------------------------------------- measure ------

def measure(x: np.ndarray, sr: int, *, settled_from_s: float | None = None,
            label: str = "", band: bool = False) -> dict:
    """Frequency trajectory, settled f0, per-file floor, and the drop."""
    r: dict = {"label": label, "sr": sr, "n": int(len(x))}
    if sr != SR_EXPECTED:
        r["verdict"] = "REFUSED"
        r["why"] = f"sample rate {sr}, expected {SR_EXPECTED}; resampling is a decision to record"
        return r
    pk = float(np.abs(x).max())
    r["peak_dbfs"] = 20 * math.log10(pk) if pk > 0 else -999.0
    if pk <= 0:
        r["verdict"], r["why"] = "REFUSED", "silent file"
        return r
    if np.abs(x).max() >= 1.0 - 1e-9 or int(np.sum(np.abs(x) > 0.999)) > 2:
        r["verdict"], r["why"] = "REFUSED", "clipped: level-crushed files cannot carry a pitch measurement"
        return r

    i0 = onset_index(x)
    r["onset_s"] = i0 / sr
    r["pre_onset_samples"] = i0
    r["floor_dbfs"] = noise_floor_dbfs(x, sr)
    ipk = int(np.argmax(np.abs(x)))
    r["t_peak_s"] = ipk / sr

    per = raw_periods(x, sr)
    if len(per) < 12:
        r["verdict"], r["why"] = "REFUSED", f"only {len(per)} periods in the file"
        return r

    # coarse f0: median of periods in the middle of the ring, used only to set
    # the sanity band. Taken late enough to be past the drop and early enough
    # to be well above the floor.
    mid = [d["f"] for d in per if d["t_start"] > r["t_peak_s"]
           and 20 * math.log10(max(d["amp"], 1e-20)) > r["floor_dbfs"] + SETTLED_HEADROOM_DB]
    if len(mid) < MIN_SETTLED:
        r["verdict"], r["why"] = "REFUSED", (
            f"only {len(mid)} periods sit {SETTLED_HEADROOM_DB:.0f} dB above this file's "
            f"floor ({r['floor_dbfs']:.1f} dBFS): the ring does not outlive the floor here")
        return r
    f_coarse = float(np.median(mid))

    # Band-limit around the fundamental for the trajectory. The band never
    # truncates the measurement (upper edge 2.05 x f0 clears x1.7) and it is
    # what suppresses a coherent neighbour -- the one confound the synthetic
    # null controls showed can fake a drop.
    r["band"] = bool(band)   # default OFF: see test_tom_pitch_probe.py, band-limiting
                             # costs up to 55 % of the excess and biases the null low
    if band:
        xb = bandpass(x, sr, f_coarse)
        per = raw_periods(xb, sr, f_coarse)
        if len(per) < 12:
            r["verdict"], r["why"] = "REFUSED", "band-limiting left too few periods"
            return r
        xa = xb
    else:
        xa = x
        per = raw_periods(x, sr, f_coarse)   # re-run with the hysteresis now that f0 is known
        if len(per) < 12:
            r["verdict"], r["why"] = "REFUSED", "too few periods after the hysteresis"
            return r

    # clean periods: past the excitation pulse, inside the sanity band
    dropped_pulse = dropped_band = 0
    clean = []
    for d in per:
        if d["t_start"] < r["t_peak_s"]:
            dropped_pulse += 1
            continue
        if not (F_BAND[0] * f_coarse <= d["f"] <= F_BAND[1] * f_coarse):
            dropped_band += 1
            continue
        d = dict(d)
        d["amp_dbfs"] = 20 * math.log10(max(d["amp"], 1e-20))
        clean.append(d)
    r["dropped_pulse"] = dropped_pulse
    r["dropped_band"] = dropped_band
    if len(clean) < MIN_SETTLED + 4:
        r["verdict"], r["why"] = "REFUSED", f"only {len(clean)} clean periods after the pulse"
        return r

    # settled window: high-SNR periods, late enough to be past any drop.
    t_first = clean[0]["t_mid"]
    auto_from = (settled_from_s if settled_from_s is not None
                 else max(0.120, t_first + 12.0 / f_coarse))
    usable = [d for d in clean if d["amp_dbfs"] > r["floor_dbfs"] + SETTLED_HEADROOM_DB]
    settled = [d for d in usable if d["t_mid"] >= auto_from]
    if len(settled) < MIN_SETTLED:
        # fall back to the latest MIN_SETTLED usable periods, and say so
        if len(usable) < MIN_SETTLED + 4:
            r["verdict"], r["why"] = "REFUSED", (
                f"only {len(usable)} periods clear floor+{SETTLED_HEADROOM_DB:.0f} dB; "
                "no settled window exists above this file's floor")
            return r
        settled = usable[-MIN_SETTLED:]
        r["settled_fallback"] = True
    r["settled_from_s"] = float(settled[0]["t_mid"])
    r["settled_to_s"] = float(settled[-1]["t_mid"])
    r["n_settled"] = len(settled)
    fs = np.array([d["f"] for d in settled])
    f_settled = float(np.median(fs))
    # THE FLOOR: the estimator's own scatter on THIS file, in this file's units.
    floor_hz = float(np.std(fs, ddof=1))
    # cross-check: an independent spectral estimate over the same window.
    f_spec = spectral_f0(xa, sr, r["settled_from_s"], r["settled_to_s"] + 2.0 / f_coarse)
    r["f_settled_spectral_hz"] = f_spec
    if not (f_spec == f_spec) or abs(f_spec - f_settled) / f_settled > SETTLED_XCHECK:
        r["verdict"] = "REFUSED"
        r["why"] = (f"settled f0 disagrees with the spectral peak over the same window: "
                    f"{f_settled:.2f} Hz vs {f_spec:.2f} Hz (> {100*SETTLED_XCHECK:.0f} %); "
                    "two estimators that disagree cannot both be right")
        return r
    r["f_settled_hz"] = f_settled
    r["floor_hz"] = floor_hz
    r["floor_frac"] = floor_hz / f_settled
    r["settled_sem_hz"] = floor_hz / math.sqrt(len(fs))

    # purity of the analysis band over the measurement span, reported so the
    # reader can see how close this file sits to the confound the controls flag.
    r["out_of_band_db"] = _out_of_band_db(x, sr, f_settled, r["onset_s"])
    r["neighbour_db"] = neighbour_db(x, sr, f_settled, r["settled_from_s"],
                                     span_s=max(0.060, r["settled_to_s"] - r["settled_from_s"]))

    traj = [(d["t_mid"] - r["onset_s"], d["f"], d["amp_dbfs"], d["dir"]) for d in clean]
    r["traj"] = [[float(a), float(b), float(c), d] for a, b, c, d in traj]

    # --- the drop -----------------------------------------------------------
    tt = np.array([a for a, _, _, _ in traj])
    ff = np.array([b for _, b, _, _ in traj])
    ok = tt > 0
    tt, ff = tt[ok], ff[ok]
    exc = ff / f_settled - 1.0

    r["f_first_hz"] = float(ff[0])
    r["t_first_s"] = float(tt[0])
    r["ratio_first_period"] = float(ff[0] / f_settled)

    fit = _fit_exponential(tt, exc)
    r.update({f"fit_{k}": v for k, v in fit.items()})
    if fit["e0"] is not None:
        r["ratio_at_onset"] = 1.0 + fit["e0"]

    # The SAME fit with the first retained period dropped. That period begins
    # immediately after the excitation pulse and is the one the pulse can still
    # contaminate; a drop that survives its removal is a relaxation the
    # resonator actually performed, not an onset artefact. Both are reported
    # because neither alone is honest: dropping it throws away the largest
    # genuine excess, keeping it lets one point drive the extrapolation.
    if len(tt) > 7:
        fit2 = _fit_exponential(tt[1:], ff[1:] / f_settled - 1.0)
        r.update({f"fit2_{k}": v for k, v in fit2.items()})
        if fit2["e0"] is not None and not fit2.get("at_bound"):
            # extrapolated back to the onset, i.e. t = 0, not to tt[1]
            r["ratio_at_onset_nofirst"] = 1.0 + fit2["e0"]
        r["ratio_second_period"] = float(ff[1] / f_settled)

    # detectability against this file's own floor
    peak_exc_hz = float(np.max(ff[:6]) - f_settled) if len(ff) >= 6 else float(ff[0] - f_settled)
    r["peak_excess_hz"] = peak_exc_hz
    r["excess_sigma"] = peak_exc_hz / floor_hz if floor_hz > 0 else float("inf")
    if r["excess_sigma"] >= SNR_SIGMA and fit.get("at_bound"):
        r["verdict"] = "REFUSED"
        r["why"] = ("the exponential fit is not a measurement while the excess is "
                    f"{r['excess_sigma']:.1f} sigma above the floor: e0={fit['e0']}, "
                    f"tau={fit['tau_ms']} ms, extrapolation gain "
                    f"{fit.get('extrap_gain')}, tau below one period="
                    f"{fit.get('tau_unresolvable')}")
        return r
    if r["excess_sigma"] < SNR_SIGMA:
        r["verdict"] = "NO-DROP-ABOVE-FLOOR"
        r["why"] = (f"peak excess {peak_exc_hz:+.2f} Hz is {r['excess_sigma']:.1f} sigma of this "
                    f"file's settled scatter ({floor_hz:.2f} Hz); below {SNR_SIGMA} sigma nothing "
                    "is claimed in either direction")
    else:
        r["verdict"] = "OK"
    return r


def neighbour_db(x: np.ndarray, sr: int, f0: float, t0: float,
                 span_s: float = 0.150, frac_oct: float = 1.0 / 6.0) -> float:
    """Level of the loudest competing spectral line, in dB relative to the
    fundamental, over [t0, t0+span_s). Fractional-octave bands rather than raw
    bins so that broadband noise and the decaying line's own skirt do not read
    as a competitor; bands within [0.8, 1.3] x f0 are the fundamental's own.

    Reported as a diagnostic, measured over the SETTLED window. The null
    controls show a coherent neighbour at -25 dB can fake a x1.33 drop, so a
    file whose settled neighbour is high deserves distrust -- but this is not a
    gate, because measured at the onset the metric cannot tell a competitor from
    the sweep itself."""
    i0 = int(t0 * sr)
    seg = x[i0:i0 + int(span_s * sr)]
    if len(seg) < 256:
        return float("nan")
    nfft = 1 << 17
    S = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), nfft)) ** 2
    fr = np.fft.rfftfreq(nfft, 1.0 / sr)
    edges = 20.0 * 2.0 ** (np.arange(0, int(np.log2(8000.0 / 20.0) / frac_oct) + 1) * frac_oct)
    fund = worst = 0.0
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (fr >= lo) & (fr < hi)
        if not m.any():
            continue
        e = float(S[m].sum())
        c = math.sqrt(lo * hi)
        if 0.8 * f0 <= c <= 1.3 * f0:
            fund = max(fund, e)
        else:
            worst = max(worst, e)
    if fund <= 0:
        return float("nan")
    return 10 * math.log10(max(worst, 1e-30) / fund)


def _out_of_band_db(x: np.ndarray, sr: int, f0: float, onset: float,
                    span_s: float = 0.100) -> float:
    """Energy outside [0.8, 1.25] x f0 over the first span_s, in dB."""
    """Energy outside [0.8, 1.25] x f0 relative to energy inside, over the first
    `span_s` after the onset, in dB. The synthetic controls in
    test_tom_pitch_probe.py tie this number to an error bound."""
    i0 = int(onset * sr)
    seg = x[i0:i0 + int(span_s * sr)]
    if len(seg) < 64:
        return float("nan")
    nfft = 1 << 17
    S = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), nfft)) ** 2
    fr = np.fft.rfftfreq(nfft, 1.0 / sr)
    inb = (fr >= 0.8 * f0) & (fr <= 1.25 * f0)
    out = (fr > 1.25 * f0) & (fr <= 8000.0) | ((fr < 0.8 * f0) & (fr >= 20.0))
    ei, eo = float(S[inb].sum()), float(S[out].sum())
    return 10 * math.log10(max(eo, 1e-30) / max(ei, 1e-30))


def _fit_exponential(t: np.ndarray, exc: np.ndarray) -> dict:
    """excess(t) = e0 * exp(-t / tau), fitted on the early span. Returns e0
    (the excess extrapolated to the onset), tau, and the residual, plus a
    linear-shape comparison so the SHAPE question is answered by residuals
    rather than asserted."""
    out = {"e0": None, "tau_ms": None, "rms_exp": None, "rms_lin": None,
           "n_fit": 0, "shape": None, "at_bound": False,
           "e0_at_bound": False, "tau_at_bound": False, "extrap_gain": None,
           "tau_unresolvable": False}
    # fit over the span where the excess is still resolvable: from the first
    # point to where it has fallen to 10 % of its start, or 150 ms, whichever
    # is first; always at least 6 points.
    if len(t) < 6:
        return out
    e_start = float(np.max(exc[:3]))
    lim = len(t)
    for k in range(3, len(t)):
        if t[k] > 0.150 or (e_start > 0 and exc[k] < 0.10 * e_start and k >= 6):
            lim = k
            break
    lim = max(lim, 6)
    tf, ef = t[:lim], exc[:lim]
    out["n_fit"] = int(lim)
    from scipy.optimize import curve_fit
    # Only curve_fit's own failures are a "no fit" (#600): RuntimeError when it
    # exhausts maxfev, ValueError for a degenerate input (LinAlgError is one).
    # Anything else is a bug and propagates, rather than returning the same
    # all-None dict a legitimate non-convergence does.
    FIT_FAILED = (RuntimeError, ValueError)
    try:
        p, _ = curve_fit(lambda tt, e0, tau: e0 * np.exp(-tt / max(tau, 1e-5)), tf, ef,
                         p0=[max(e_start, 1e-4), 0.025],
                         bounds=([-1.0, 1e-3], [3.0, 1.0]), maxfev=20000)
    except FIT_FAILED:
        return out
    out["e0"], out["tau_ms"] = float(p[0]), float(p[1] * 1e3)
    # e0 at a bound is a failed measurement. tau at a bound with e0 ~ 0 is
    # not: it is the degenerate fit of an exponential to a flat trajectory,
    # which is what a tom with NO drop correctly produces, and refusing it
    # would refuse the null control.
    out["e0_at_bound"] = bool(abs(p[0] + 1.0) < 1e-3 or abs(p[0] - 3.0) < 1e-3)
    out["tau_at_bound"] = bool(abs(p[1] - 1e-3) < 1e-6 or abs(p[1] - 1.0) < 1e-6)
    # An extrapolation that runs far past the data is not a measurement.
    # Every degenerate fit seen on this corpus has the same shape: one high
    # first period, the rest flat, explained by e0 -> the bound with a
    # tau of ~1.8 ms. Guard on the gain from the largest OBSERVED excess.
    obs = float(np.max(np.abs(ef))) if len(ef) else 0.0
    out["extrap_gain"] = float(abs(p[0]) / obs) if obs > 0 else float("inf")
    # A relaxation shorter than one period of the carrier is below what a
    # per-period estimator can resolve; the fit is then describing the single
    # first point, not a trajectory.
    span = float(tf[-1] - tf[0]) if len(tf) > 1 else 0.0
    one_period = span / max(len(tf) - 1, 1)
    out["tau_unresolvable"] = bool(p[1] < one_period)
    out["at_bound"] = bool(out["e0_at_bound"] or out["extrap_gain"] > EXTRAP_MAX
                           or out["tau_unresolvable"])
    out["rms_exp"] = float(np.sqrt(np.mean((ef - p[0] * np.exp(-tf / p[1])) ** 2)))
    try:
        q, _ = curve_fit(lambda tt, e0, T: e0 * np.maximum(0.0, 1.0 - tt / max(T, 1e-5)), tf, ef,
                         p0=[max(e_start, 1e-4), 0.060],
                         bounds=([-1.0, 1e-3], [3.0, 1.0]), maxfev=20000)
    except FIT_FAILED:
        q = None
    if q is not None:
        out["rms_lin"] = float(np.sqrt(np.mean(
            (ef - q[0] * np.maximum(0.0, 1.0 - tf / q[1])) ** 2)))
        out["lin_T_ms"] = float(q[1] * 1e3)
        out["lin_e0"] = float(q[0])
    if out["rms_exp"] is not None and out["rms_lin"] is not None:
        out["shape"] = "exponential" if out["rms_exp"] < out["rms_lin"] else "linear"
    return out


# ------------------------------------------------- synthetic ground truth ----

def synth_tom(f_inf: float, q: float, tau_amp_s: float, *, ratio: float = 1.7,
              drop_ms: float = 60.0, shape: str = "exp", sr: int = SR_EXPECTED,
              dur_s: float = 0.75, pulse_ms: float = 1.0, amp: float = 0.37,
              noise_dbfs: float = -88.0, bits: int = 24, trim: bool = True,
              seed: int = 0, k_exp: float = 3.0) -> np.ndarray:
    """A tom with a KNOWN pitch drop: a 2-pole resonator whose f0 is retuned
    every sample, kicked by a rectangular pulse. `ratio` is f0 at the onset over
    f0 settled; `drop_ms` with shape 'exp' means f0 excess x exp(-k_exp t/drop_ms)
    (the contract's own law, k_exp = 3), with shape 'lin' a ramp to zero at
    drop_ms, with shape 'none' no drop at all.

    This is the instrument's ground truth: it carries the same excitation-pulse
    onset, the same dither floor, the same 24-bit quantisation and the same hard
    trim as the recordings, so validating on it exercises the real failure mode
    rather than a clean sinusoid.
    """
    n = int(dur_s * sr)
    t = np.arange(n) / sr
    if shape == "none":
        f0 = np.full(n, f_inf)
    elif shape == "exp":
        f0 = f_inf * (1.0 + (ratio - 1.0) * np.exp(-k_exp * t / (drop_ms * 1e-3)))
    elif shape == "lin":
        f0 = f_inf * (1.0 + (ratio - 1.0) * np.maximum(0.0, 1.0 - t / (drop_ms * 1e-3)))
    else:
        raise ValueError(shape)
    r = np.exp(-1.0 / (tau_amp_s * sr))          # amplitude pole radius
    w = 2 * np.pi * f0 / sr
    a1, a2 = 2 * r * np.cos(w), -(r ** 2)
    npulse = max(1, int(pulse_ms * 1e-3 * sr))
    y = np.zeros(n)
    y1 = y2 = 0.0
    for i in range(n):
        u = 1.0 if i < npulse else 0.0
        yi = a1[i] * y1 + a2 * y2 + u
        y2, y1 = y1, yi
        y[i] = yi
    m = np.abs(y).max()
    if m > 0:
        y = y / m * amp
    if q is not None and q > 0:
        pass  # Q enters through tau_amp_s; kept in the signature for the caller's record
    rng = np.random.default_rng(seed)
    y = y + rng.normal(0.0, 10 ** (noise_dbfs / 20.0), n)
    if bits:
        step = 2.0 ** -(bits - 1)
        y = np.round(y / step) * step
    if trim:
        i0 = onset_index(y, 0.01)
        y = y[max(0, i0 - 4):]
    return y


def tau_from_q(f0: float, q: float) -> float:
    """Amplitude 1/e time of a 2-pole resonator at f0, Q."""
    return q / (np.pi * f0)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="measure one recording's tom pitch drop")
    ap.add_argument("wav", nargs="+")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--traj", action="store_true", help="print the frequency trajectory")
    ap.add_argument("--no-band", action="store_true", help="skip the band-pass (validation only)")
    a = ap.parse_args(argv)
    rows = []
    for p in a.wav:
        sr, x = read_wav(p)
        r = measure(x, sr, label=pathlib.Path(p).name, band=not a.no_band)
        rows.append(r)
        if a.json:
            continue
        print(f"{r['label']}  {r['verdict']}")
        if r["verdict"] == "REFUSED":
            print(f"    {r['why']}")
            continue
        print(f"    settled {r['f_settled_hz']:.2f} Hz  floor {r['floor_hz']:.3f} Hz "
              f"({100*r['floor_frac']:.2f} %)  n={r['n_settled']}  file floor {r['floor_dbfs']:.1f} dBFS")
        print(f"    first period {r['f_first_hz']:.2f} Hz at {1e3*r['t_first_s']:.1f} ms "
              f"-> x{r['ratio_first_period']:.4f};  excess {r['peak_excess_hz']:+.2f} Hz "
              f"= {r['excess_sigma']:.1f} sigma")
        if r.get("ratio_at_onset"):
            print(f"    fit: onset x{r['ratio_at_onset']:.4f}  tau {r['fit_tau_ms']:.1f} ms  "
                  f"shape {r['fit_shape']} (rms exp {r['fit_rms_exp']:.5f} vs lin {r['fit_rms_lin']:.5f})")
        if a.traj:
            for tt, ff, dd, dr in r["traj"][:20]:
                print(f"      {1e3*tt:7.2f} ms  {ff:8.2f} Hz  {dd:7.1f} dBFS  {dr}")
    if a.json:
        print(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
