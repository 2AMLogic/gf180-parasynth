#!/usr/bin/env python3
"""Separating a struck instrument's damped modes from its noise, and fitting a
knob's transfer curve from the filename-encoded reference set.

`drum_verify.py` answers "what does this hit measure?". This module answers the
three questions that one could not, each of which had produced a wrong number:

  1. **How much of this hit is noise?** `noise_share` fits the damped sinusoids
     the body is made of and calls the residual noise. It is validated two
     ways -- against synthetic mixtures whose noise share is set by
     construction, and against our own renders, where the tonal path and the
     noise path can be rendered separately and the true share is exact.
  2. **Where on its knob does our kit sit?** `knob_curve` measures the noise
     share at every position of the knob the reference set encodes in its
     filenames, so a target is read off a curve instead of asserted from one
     file.
  3. **Is a rejection figure above the recording's own floor?** `spectral_floor`
     reports the median and 90th percentile of |X| in a stated band, so
     "N dB down" can be compared with something.

Why not a band split. The committed body/air split -- power above and below a
split frequency, from `drum_verify.spectrum` -- is **invalid on a decaying
one-shot** and its numbers are withdrawn (docs/drum-verification.md section 8).
It applies a Hann window across the whole analysis span, so on a 500 ms span a
30 ms hit is weighted 0.0039 at t = 10 ms against 1.0 at t = 250 ms: a 48 dB
tilt away from the attack and towards whichever component decays slowest. On
synthetic mixtures with a known share it under-reports by 5 to 9 times, and it
is what produced "our snare's noise is 1.2 % of its energy" for a render whose
noise is 18.5 % of its energy, measured exactly.

    .venv/bin/python model/drum_fit.py --self-test          # the validation, no audio needed
    .venv/bin/python model/drum_fit.py --refs /tmp/tr808-ref   # every measurement below

The reference set is CC0-1.0 and committed nowhere; see `drum_verify.py`'s
header for its provenance. Knob positions are encoded in the filename on a
0..10 dial at 00 / 25 / 50 / 75 / 10, tone or tuning before decay or snappy.
"""
from __future__ import annotations
import argparse, glob, math, os, sys, wave
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "audition"))
import numpy as np

# Knob position encoded by each two-character field of a reference filename.
KNOB = {"00": 0.0, "25": 2.5, "50": 5.0, "75": 7.5, "10": 10.0}

# Search bands for each voice's damped modes, and the analysis window.
# A mode is a bridged-T ring; anything decaying faster than MODE_TAU_MIN is the
# shaped pulse, not a mode, and letting the fit call it one is how the fit ran
# away on the files where one resonator is nearly off.
MODE_TAU_MIN = 5e-3
VOICE_MODES = {
    "SD": [(150.0, 200.0), (300.0, 400.0)],
    "BD": [(40.0, 90.0)],
    "LT": [(70.0, 120.0)],
    "HT": [(150.0, 240.0)],
}


# ---------------------------------------------------------------- io ----------
def read_wav(path: str) -> tuple[int, np.ndarray]:
    """16-bit PCM WAV to float in [-1, 1); stereo is averaged. Nothing is
    resampled anywhere in this module: every measure is rate-independent."""
    with wave.open(path, "rb") as w:
        sr, n, sw, ch = w.getframerate(), w.getnframes(), w.getsampwidth(), w.getnchannels()
        raw = w.readframes(n)
    if sw != 2:
        raise ValueError(f"{path}: {sw * 8}-bit, expected 16")
    x = np.frombuffer(raw, dtype="<i2").astype(np.float64)
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return sr, x / 32768.0


def trim_onset(x: np.ndarray, sr: int, frac: float = 0.02) -> np.ndarray:
    """Drop leading silence, backing up 0.5 ms so no attack is clipped."""
    pk = np.abs(x).max()
    if pk <= 0:
        return x
    idx = np.nonzero(np.abs(x) > frac * pk)[0]
    if not len(idx):
        return x
    return x[max(0, idx[0] - int(5e-4 * sr)):]


# ------------------------------------------------------ mode subtraction ------
def _seed_hz(x: np.ndarray, sr: int, lo: float, hi: float) -> float:
    """Strongest spectral line inside [lo, hi], as a starting point only."""
    n = 1 << int(math.ceil(math.log2(max(len(x), 2) * 4)))
    S = np.abs(np.fft.rfft(x * np.hanning(len(x)), n))
    f = np.fft.rfftfreq(n, 1.0 / sr)
    m = (f >= lo) & (f <= hi)
    return float(f[m][np.argmax(S[m])])


def _modes_signal(p: np.ndarray, t: np.ndarray, n: int) -> np.ndarray:
    y = np.zeros_like(t)
    for i in range(n):
        a, f, tau, ph = p[4 * i:4 * i + 4]
        y += a * np.exp(-t / max(tau, 1e-5)) * np.cos(2 * np.pi * f * t + ph)
    return y


def noise_share(x: np.ndarray, sr: int, bands, win_s: float = 0.25) -> dict:
    """Fit one damped sinusoid per entry of `bands` and call the residual noise.

    `bands` gives the search interval for each mode's frequency; every mode is
    free in amplitude, frequency (inside its band), decay (at or above
    MODE_TAU_MIN) and phase. The window is `win_s` seconds from the onset --
    STATED, and the same for every file compared, because the reference set
    trims a loud hit to 500 ms and a quiet one to 250 and a whole-file window
    would compare different amounts of tail.

    Returns `share` (residual energy / total energy over the window, in [0, 1]),
    the fitted `modes` sorted by frequency, and the `residual` itself.

    Every decay here is a **tau**: the time constant of a single exponential,
    fitted, not a T20 and not a duration to a threshold.
    """
    from scipy.optimize import least_squares
    x = trim_onset(np.asarray(x, dtype=np.float64), sr)
    x = x[:int(win_s * sr)]
    x = x - x.mean()
    # The result is a ratio, so normalise: callers pass anything from a
    # [-1, 1) float to a raw int16 bus, and the amplitude bounds below are
    # absolute. Without this a bus-scaled input puts the initial guess outside
    # its bound and the fit refuses to start.
    peak = float(np.abs(x).max())
    if peak > 0:
        x = x / peak
    t = np.arange(len(x)) / sr
    n = len(bands)
    p0, lb, ub = [], [], []
    for lo, hi in bands:
        p0 += [np.abs(x).max() * 0.5, _seed_hz(x, sr, lo, hi), 0.03, 0.0]
        lb += [0.0, lo, MODE_TAU_MIN, -10 * np.pi]
        ub += [10.0, hi, 2.0, 10 * np.pi]
    r = least_squares(lambda p: _modes_signal(p, t, n) - x, p0, bounds=(lb, ub), max_nfev=20000)
    res = x - _modes_signal(r.x, t, n)
    modes = sorted(({"amp": float(r.x[4 * i]), "hz": float(r.x[4 * i + 1]),
                     "tau_ms": float(r.x[4 * i + 2] * 1e3)} for i in range(n)),
                   key=lambda m: m["hz"])
    tot = float(x @ x)
    return dict(share=float(res @ res / tot) if tot else float("nan"),
                modes=modes, residual=res, windowed=x, sr=sr)


def band_shares(x: np.ndarray, sr: int, edges, nfft: int = 1 << 12) -> list:
    """Percent of `x`'s energy in each band of `edges`, by an averaged
    periodogram over short frames -- so no single window tilts the result the
    way a whole-span Hann does. Used on the residual, which is stationary-ish
    noise under an envelope, not on a decaying tone."""
    x = np.asarray(x, dtype=np.float64)
    n = min(nfft, len(x))
    if n < 8:
        return [float("nan")] * (len(edges) - 1)
    hop, acc, k = max(n // 2, 1), np.zeros(n // 2 + 1), 0
    w = np.hanning(n)
    for i in range(0, max(1, len(x) - n + 1), hop):
        acc += np.abs(np.fft.rfft(x[i:i + n] * w)) ** 2
        k += 1
    P, f = acc / max(k, 1), np.fft.rfftfreq(n, 1.0 / sr)
    out = [P[(f >= a) & (f < b)].sum() for a, b in zip(edges, edges[1:])]
    tot = sum(out)
    return [float(100 * v / tot) if tot else float("nan") for v in out]


def band_energy_interval(x: np.ndarray, sr: int, edges, t0: float, t1: float) -> list:
    """Percent of the energy **in the stated interval [t0, t1) after onset**
    that each band of `edges` carries, by filtering the whole signal and then
    integrating over the interval.

    This is the way to say "the attack is in this band" without a short-window
    FFT. Gating first and transforming after gives a frequency resolution of
    1/(t1-t0) -- 250 Hz over 4 ms -- and the apparent peak then tracks the bin
    spacing rather than the signal, which is the artefact that produced, and
    then withdrew, "the machine's first 4 ms peaks at 250 Hz". Filtering first
    has no such limit: the filters see the whole signal.
    """
    from scipy.signal import butter, sosfiltfilt
    x = np.asarray(x, dtype=np.float64)
    i0, i1 = int(t0 * sr), int(t1 * sr)
    out = []
    for a, b in zip(edges, edges[1:]):
        sos = butter(4, [a / (sr / 2), min(b, sr / 2 * 0.999) / (sr / 2)], btype="band", output="sos")
        y = sosfiltfilt(sos, x)[i0:i1]
        out.append(float(y @ y))
    tot = sum(out)
    return [float(100 * v / tot) if tot else float("nan") for v in out]


# ----------------------------------------------------------- spectrum ---------
def line_db(x: np.ndarray, sr: int, hz: float, tol: float = 8.0,
            win_s: float = 0.5, nfft: int = 1 << 18, ref_hz: float = None) -> float:
    """Level of the strongest line within +-tol of `hz`, in dB relative to the
    whole spectrum's maximum (or to the line near `ref_hz` if given). One
    stated window, one stated transform length: 2^18 over 0.5 s is 0.17 Hz of
    resolution, so a line is a line and not a bin."""
    x = trim_onset(np.asarray(x, dtype=np.float64), sr)[:int(win_s * sr)]
    S = np.abs(np.fft.rfft(x * np.hanning(len(x)), nfft))
    f = np.fft.rfftfreq(nfft, 1.0 / sr)
    ref = S.max() if ref_hz is None else S[(f > ref_hz - tol) & (f < ref_hz + tol)].max()
    m = (f > hz - tol) & (f < hz + tol)
    return float(20 * np.log10(S[m].max() / ref + 1e-30))


def spectral_floor(x: np.ndarray, sr: int, lo: float, hi: float,
                   win_s: float = 0.5, nfft: int = 1 << 18) -> dict:
    """The recording's own noise floor in [lo, hi], as dB relative to the
    spectrum's maximum: the median and the 90th percentile of |X| over the
    band's bins. A claim that some line sits N dB down is only meaningful if
    -N is above this."""
    x = trim_onset(np.asarray(x, dtype=np.float64), sr)[:int(win_s * sr)]
    S = np.abs(np.fft.rfft(x * np.hanning(len(x)), nfft))
    f = np.fft.rfftfreq(nfft, 1.0 / sr)
    db = 20 * np.log10(S / S.max() + 1e-30)
    m = (f >= lo) & (f <= hi)
    return dict(median_db=float(np.median(db[m])), p90_db=float(np.percentile(db[m], 90)),
                lo=lo, hi=hi)


# ------------------------------------------------------------ knob laws -------
def knob_curve(refdir: str, voice: str, win_s: float = 0.25) -> dict:
    """Noise share at every (first knob, second knob) the set encodes, and the
    curve of the second knob averaged over the first.

    Returns `share[(k1, k2)]`, and `law[k2] = (mean share, mean noise/tone
    amplitude ratio, its sd)`. The amplitude ratio is sqrt(s/(1-s)): it is the
    quantity a level control moves linearly in dB, and it is what a target
    must be read off, because a share saturates at 1 and a knob does not.
    """
    bands = VOICE_MODES[voice]
    share, mods = {}, {}
    pat = os.path.join(refdir, voice.lower() + "8", voice + "????.WAV")
    files = sorted(glob.glob(pat)) or sorted(glob.glob(pat.upper()))
    if not files:
        raise FileNotFoundError(pat)
    for p in files:
        stem = os.path.basename(p)[len(voice):len(voice) + 4]
        k1, k2 = KNOB[stem[:2]], KNOB[stem[2:]]
        sr, x = read_wav(p)
        r = noise_share(x, sr, bands, win_s=win_s)
        share[(k1, k2)] = r["share"]
        mods[(k1, k2)] = r["modes"]
    law = {}
    for k2 in sorted({k for _, k in share}):
        s = np.array([share[k] for k in share if k[1] == k2])
        ratio = np.sqrt(s / np.maximum(1 - s, 1e-12))
        law[k2] = (float(s.mean()), float(ratio.mean()), float(ratio.std()))
    return dict(share=share, law=law, modes=mods)


def knob_position(ratio: float, law: dict) -> float:
    """Where a measured noise/tone amplitude ratio sits on a knob's own curve,
    by linear interpolation in dB between the two positions that bracket it.
    Returns NaN below the curve's floor -- which is the honest answer when a
    signal is quieter than the machine is at knob 0, and the reason 'ours sits
    at 2.5 of 10' could not be read off a single file."""
    ks = sorted(law)
    rs = [law[k][1] for k in ks]
    if ratio <= min(rs):
        return float("nan")
    if ratio >= max(rs):
        return float(ks[-1])
    d = 20 * np.log10(np.maximum(rs, 1e-12))
    target = 20 * np.log10(ratio)
    for i in range(len(ks) - 1):
        if d[i] <= target <= d[i + 1]:
            if d[i + 1] == d[i]:
                return float(ks[i])
            return float(ks[i] + (ks[i + 1] - ks[i]) * (target - d[i]) / (d[i + 1] - d[i]))
    return float("nan")


# ------------------------------------------------------------ validation ------
def synthetic_mixture(share: float, sr: int = 44100, dur: float = 0.25,
                      tone_hz=(173.0, 336.0), tone_tau=(0.030, 0.010),
                      noise_tau: float = 0.015, noise_band=(3000.0, 12000.0),
                      seed: int = 3) -> np.ndarray:
    """Two damped sinusoids plus band-limited noise under its own envelope,
    scaled so the noise carries exactly `share` of the energy. The noise decays
    FASTER than the tone by default, which is our snare's case and the case a
    whole-span window gets most wrong."""
    from scipy.signal import butter, sosfiltfilt
    rng = np.random.default_rng(seed)
    t = np.arange(int(dur * sr)) / sr
    tone = (1.0 * np.exp(-t / tone_tau[0]) * np.cos(2 * np.pi * tone_hz[0] * t)
            + 0.5 * np.exp(-t / tone_tau[1]) * np.cos(2 * np.pi * tone_hz[1] * t + 1.0))
    if share <= 0:
        return tone
    sos = butter(2, [noise_band[0] / (sr / 2), noise_band[1] / (sr / 2)], btype="band", output="sos")
    nz = sosfiltfilt(sos, rng.standard_normal(len(t))) * np.exp(-t / noise_tau)
    a = math.sqrt(share / (1 - share) * float(tone @ tone) / float(nz @ nz))
    return tone + a * nz


# The one set of qualification targets and bounds. The CLI self-test and
# model/test_drum_fit.py both read these, so the two entry points cannot
# disagree about what "qualified" means.
SELF_TEST_TARGETS = (0.0, 0.011, 0.05, 0.1855, 0.2766, 0.50, 0.80, 0.90)


def share_bound(target: float) -> float:
    """The method's MEASURED accuracy (fraction): 0.6 pp up to a 30 % share,
    1.5 pp above. The estimate is biased slightly high, growing with noise."""
    return 0.006 if target <= 0.30 else 0.015


def share_failure(target: float, got: float):
    """None if `got` qualifies for `target`, else the reason. A nonfinite
    estimate fails (NaN compares False against every bound, so it would pass
    any `not (err > bound)` style check); so does an UNDER-estimate."""
    if not math.isfinite(got):
        return f"target {target}: nonfinite estimate {got!r}"
    if got < target - 0.002:
        return f"target {target}, got {got}: an UNDER-estimate is a new failure mode"
    if not abs(got - target) < share_bound(target):
        return f"target {target}, got {got}: outside {share_bound(target) * 100:.1f} pp"
    return None


def self_test(verbose: bool = True):
    """Recover a known noise share from synthetic mixtures. Returns
    (worst finite error in percentage points or None, list of failures)."""
    worst = None
    failures = []
    if verbose:
        print("  true      estimated   error")
    for target in SELF_TEST_TARGETS:
        x = synthetic_mixture(target)
        got = noise_share(x, 44100, VOICE_MODES["SD"])["share"]
        err = abs(got - target) * 100
        if math.isfinite(err):
            worst = err if worst is None else max(worst, err)
        why = share_failure(target, got)
        if why:
            failures.append(why)
        if verbose:
            print(f"  {target * 100:6.2f} %   {got * 100:7.2f} %   {err:5.2f} pp"
                  + ("   FAIL" if why else ""))
    return worst, failures


# ---------------------------------------------------------------- main --------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refs", help="the unpacked sounds-tr808-fischer tree")
    ap.add_argument("--self-test", action="store_true",
                    help="validate the separator against synthetic mixtures; no audio needed")
    a = ap.parse_args(argv)
    if a.self_test or not a.refs:
        print("separator validation -- synthetic mixtures, noise share set by construction:")
        worst, failures = self_test()
        if failures:
            for f in failures:
                print(f"  FAIL: {f}")
            print("  separator validation FAILED; not trusting it for the reference report")
            return 1
        print(f"  worst error {worst:.2f} percentage points; all targets within bound")
        if not a.refs:
            return 0

    print()
    print("SD -- the SNAPPY law, noise share by damped-mode subtraction, 250 ms window")
    c = knob_curve(a.refs, "SD")
    print("  SNAPPY   noise share        noise/tone amplitude")
    for k in sorted(c["law"]):
        s, r, sd = c["law"][k]
        print(f"  {k:5.1f}   {s * 100:6.2f} %          {r:6.4f} +-{sd:.4f}  ({20 * math.log10(r):+6.2f} dB)")

    print()
    print("SD -- the real noise's spectrum (the residual), % of energy per band")
    edges = [700, 1500, 3000, 5000, 8000, 12000, 16000]
    print("   " + "  ".join(f"{x / 1000:g}-{y / 1000:g}k" for x, y in zip(edges, edges[1:])))
    for stem in ("SD5010", "SD2510", "SD7510"):
        sr, x = read_wav(os.path.join(a.refs, "sd8", stem + ".WAV"))
        r = noise_share(x, sr, VOICE_MODES["SD"])
        print(f"   {stem}: " + "  ".join(f"{v:6.2f}" for v in band_shares(r["residual"], sr, edges)))

    print()
    print("BD -- the DECAY law: tau of the body mode, mean over the five TUNING positions")
    taus, hz = {}, {}
    for p in sorted(glob.glob(os.path.join(a.refs, "bd8", "BD????.WAV"))):
        stem = os.path.basename(p)[2:6]
        sr, x = read_wav(p)
        seg = trim_onset(x, sr)[int(0.020 * sr):]
        r = noise_share(seg, sr, VOICE_MODES["BD"], win_s=0.30)
        hz.setdefault(KNOB[stem[2:]], []).append(r["modes"][0]["hz"])
        taus.setdefault(KNOB[stem[2:]], []).append(r["modes"][0]["tau_ms"])
    print("  DECAY    f0 (Hz)            mode tau (ms)")
    for k in sorted(hz):
        print(f"  {k:5.1f}   {np.mean(hz[k]):7.2f} +-{np.std(hz[k]):5.2f}   {np.mean(taus[k]):8.1f} +-{np.std(taus[k]):6.1f}")

    print()
    print("CB -- the difference tone against the recording's own floor")
    sr, x = read_wav(os.path.join(a.refs, "cb8", "CB.WAV"))
    fl = spectral_floor(x, sr, 230, 290)
    for h in (265.0, 558.0, 824.0, 1117.0, 1382.0):
        print(f"  {h:8.1f} Hz: {line_db(x, sr, h):7.2f} dB")
    print(f"  floor {fl['lo']:.0f}-{fl['hi']:.0f} Hz: median {fl['median_db']:.2f} dB, "
          f"90th pct {fl['p90_db']:.2f} dB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
