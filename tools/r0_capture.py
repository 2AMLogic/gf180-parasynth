#!/usr/bin/env python3
"""Analyse a physical R0 capture session against the simulated R0 reference.

    python tools/r0_capture.py analyse  --bundle captures/r0 [--out DIR]
    python tools/r0_capture.py synth    --out DIR [--defect NAME]      # a synthetic session
    python tools/r0_capture.py controls --out DIR [--analyser stub]    # every defect, one matrix

The operator procedure is docs/capture-r0.md. The reference is
fpga/release/evidence/r0-reference/ (tools/r0_reference.py: the published
image's RTL, driven by the release CLI's pinned bytes, decoded at the I2S pins).

THE ALIGNMENT IS FROZEN AND DECLARED, never fitted per candidate:

    capture[n] = g * ref((n - d_t) * rho) + everything the analog path adds

  rho  one sample-clock ratio (DAC clock / interface clock) for the SESSION,
       estimated once from the declared calibration take: local lags of 2048-
       sample windows across that take, regressed against reference position.
  g    one gain for the SESSION, least squares over the same take once rho and
       its delay are known.
  d_t  one fixed delay PER TAKE (the recording starts by hand, so there is no
       shared time origin), estimated only inside the reference's declared
       calibration window (r0_reference.CAL_* -- the first 30 ms after the
       first onset). Nothing after that window moves it.

Every other quantity -- local lags, local gains, residual, pitch -- is MEASURED
after the alignment is frozen and reported against it. Local lags and gains are
never used to re-align; they are how a timing slip, a clock mismatch or a gain
change is SEEN. Re-normalising each take would erase exactly those errors.

VERDICTS (docs/trials.md rule 4): PASS / FAIL / REFUSED. REFUSED is NO VERDICT:
missing inputs, a sample rate other than 48000, incomplete capture metadata, a
take shorter than its reference, a host log that is not the released command,
or a calibration take too short to estimate the clock. A refusal is never a
pass and never a fail.

WHAT IT CANNOT SEE, stated rather than hidden (docs/verification-rules.md
rule 4): R0 is dual-mono -- rtl-sketch/i2s_tx.v sends the same sample on both
I2S channels -- so a left/right swap is UNOBSERVABLE from the audio. The
`channel-identity` property is reported UNOBSERVABLE on every run; the swap
control is expected BLIND. A missing, foreign or inverted channel IS seen.

Exit: 0 PASS, 1 FAIL, 2 REFUSED (NO VERDICT).
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import os
import pathlib
import shutil
import sys

import numpy as np
from scipy.io import wavfile
from scipy.signal import correlate

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import r0_reference as rr                                     # noqa: E402

REFERENCES = ROOT / "fpga" / "release" / "evidence" / "r0-reference"
DEFAULT_BUNDLE = ROOT / "captures" / "r0"
SESSION_SCHEMA = "r0-capture-session/1"
RECORD_SCHEMA = "r0-capture-analysis/1"
SR = 48000
PASS, FAIL, REFUSED = "PASS", "FAIL", "REFUSED"

# ---- the criterion (versioned; a change here is a criterion change) ---------
CRITERION = "r0-capture/1 (provisional until the first real capture; see docs/capture-r0.md)"
LIMITS = {
    "coarse_ncc_min": 0.5,          # the reference must be findable at all
    "clock_ppm_max": 200.0,         # |rho - 1|: two crystal oscillators, generous
    "clock_ppm_drift_max": 20.0,    # a take's own lag slope vs the frozen rho
    "clock_min_span_s": 1.0,        # shorter takes cannot resolve 20 ppm
    "timing_slip_max_samples": 0.5, # local lag scatter about the frozen line
    "gain_change_db_max": 0.5,      # median local gain vs the frozen g
    "residual_db_max": -20.0,       # sum (c - p)^2 / sum p^2 over the evaluation region
    "noise_dbfs_max": -70.0,        # silence take, DAC channels, DC removed
    "silent_margin_db": 10.0,       # a sounding region must sit this far above the floor
    "dropout_drop_db": 20.0,        # a 5 ms block this far below prediction
    "dropout_ref_dbfs_min": -50.0,  # ... where the prediction is at least this loud
    "stuck_ref_dbfs_max": -70.0,    # the prediction is silent here ...
    "stuck_rise_db": 20.0,          # ... and the capture this far above the floor
    "stuck_min_s": 0.1,
    "clip_level": 0.999,            # of interface full scale
    "clip_run": 3,                  # consecutive samples
    "pitch_cents_max": 3.0,         # f_cap / f_ref against the frozen rho
    "channels_corr_min": 0.99,      # the two DAC channels, dual-mono
    "channels_level_db_max": 1.0,
    "foreign_ncc_max": 0.3,         # the reference on a channel not declared
    "repeat_db_max": -30.0,         # take-to-take residual, same command
}
WIN = 2048            # local lag / gain window, samples
BLOCK = 240           # 5 ms, dropout / stuck blocks
COARSE_S = 0.5        # the coarse-search template, from the calibration window start
CLUSTER_S = 0.25      # coarse candidates closer than this are one placement
# THE DECLARED ANALYSIS BAND. Capture and prediction pass through the SAME
# zero-phase filter before every waveform comparison (delay, lags, gains,
# residual, repeat, dropout, stuck). It is declared, identical for every take
# and never fitted: it removes what the analog path is allowed to do outside
# the audio band (AC coupling below ~20 Hz, the DAC/ADC anti-image filters
# near Nyquist) and nothing inside it. The first clean synthetic run, with a
# 5 Hz AC-coupling high-pass, FAILED timing by 1.0 sample and residual by
# 0.5 dB without it: a first-order high-pass moves a 50 Hz kick's local lag.
BAND_HZ = (100.0, 16000.0)
TIMING_MIN_WINDOWS = 3  # a slip must show in at least this many local-lag windows
END_GUARD_S = 0.1     # not scored: the last 0.1 s of each reference (see analyse_take)
PEAK_TIE = 0.97       # correlation peaks this close to the best are ties
ENV_LP_HZ = 100.0     # the alignment envelope: x^2, zero-phase low-pass, sqrt
TONE_COMMANDS = {"held-m5a-saw", "held-m5a-pulse"}     # single-oscillator presets
REQUIRED_COMMANDS = ("silence", "held-m5a-saw", "held-default", "bar808-full", "demo")
PROPERTIES = ("routing", "silence", "clipping", "pitch", "clock", "timing", "gain",
              "noise", "dropout", "stuck", "residual", "repeat", "channel-identity")


class Refused(Exception):
    pass


# =============================================================================
# estimators (each has a closed-form known-answer test in test_r0_capture.py)
# =============================================================================
def dbfs(rms: float) -> float:
    return 20.0 * math.log10(max(float(rms), 1e-12))


def rms(x) -> float:
    x = np.asarray(x, dtype=np.float64)
    return float(np.sqrt(np.mean(x * x))) if x.size else 0.0


def _kaiser(t, half, beta=9.0):
    u = np.clip(1.0 - (t / half) ** 2, 0.0, None)
    from scipy.special import i0            # numpy's i0 is 10x slower (profiled)
    return i0(beta * np.sqrt(u)) / i0(beta)


def interp(x: np.ndarray, pos: np.ndarray, half: int = 32) -> np.ndarray:
    """Band-limited (Kaiser-windowed sinc, 2*half taps) value of x at
    fractional positions `pos`; outside x is zero. Used both to warp the
    reference onto a capture's timeline and a capture onto the reference's."""
    x = np.asarray(x, dtype=np.float64)
    pos = np.asarray(pos, dtype=np.float64)
    out = np.zeros(pos.shape)
    pad = np.concatenate([np.zeros(half + 1), x, np.zeros(half + 1)])
    k = np.arange(-half + 1, half + 1)
    for s in range(0, pos.size, 8192):
        p = pos[s:s + 8192]
        i0 = np.floor(p).astype(np.int64)
        frac = p - i0
        idx = i0[:, None] + k[None, :] + half + 1
        valid = (idx >= 0) & (idx < pad.size)
        t = frac[:, None] - k[None, :]
        w = np.sinc(t) * _kaiser(t, half)
        vals = np.where(valid, pad[np.clip(idx, 0, pad.size - 1)], 0.0)
        out[s:s + 8192] = np.sum(vals * w, axis=1)
    return out


def warp_reference(ref: np.ndarray, n_out: int, d: float, rho: float) -> np.ndarray:
    """ref on the capture's timeline: p[n] = ref((n - d) * rho)."""
    return interp(ref, (np.arange(n_out) - d) * rho)


def unwarp_capture(cap: np.ndarray, n_ref: int, d: float, rho: float) -> np.ndarray:
    """capture on the reference's timeline: x[m] = cap(d + m / rho)."""
    return interp(cap, d + np.arange(n_ref) / rho)


def ncc_lag(template: np.ndarray, signal: np.ndarray, lo: int, hi: int,
            center: float | None = None):
    """Best normalised cross-correlation of `template` placed at integer lags
    lo..hi of `signal`, refined to a fraction of a sample. Returns (lag, ncc,
    all_ncc), or (None, 0.0, None) when the range holds no complete placement.

    `center`: a periodic tone matches itself one period off almost exactly as
    well as in place (0.9986 against 0.9974 on the dev reference: sub-sample
    sampling of a sharp peak decides it), so with a center the answer is the
    local maximum within PEAK_TIE of the best that lies NEAREST the center."""
    template = np.asarray(template, dtype=np.float64)
    signal = np.asarray(signal, dtype=np.float64)
    n = template.size
    lo, hi = max(0, int(lo)), min(int(hi), signal.size - n)
    if hi < lo or n == 0:
        return None, 0.0, None
    seg = signal[lo:hi + n]
    num = correlate(seg, template, mode="valid", method="fft")
    e = np.concatenate([[0.0], np.cumsum(seg * seg)])
    win_e = e[n:] - e[:-n]
    te = float(np.dot(template, template))
    den = np.sqrt(np.maximum(win_e, 1e-30) * max(te, 1e-30))
    c = num / den
    # a (near-)silent placement has no correlation: FFT round-off over a
    # denominator of ~0 is not a match (the first run found "matches" there)
    c[win_e < 1e-9 * max(te, 1e-30)] = 0.0
    i = int(np.argmax(c))
    if center is not None and c.size >= 3:
        pk = np.flatnonzero((c[1:-1] >= c[:-2]) & (c[1:-1] >= c[2:])) + 1
        pk = pk[c[pk] >= PEAK_TIE * c[i]]
        if pk.size:
            i = int(pk[np.argmin(np.abs(lo + pk - center))])
    t = _refine_peak(c, i)
    peak = float(interp(c, np.array([t]), half=16)[0]) if t != i else float(c[i])
    return lo + t, min(1.0, max(peak, float(c[i]))), c


def _refine_peak(c: np.ndarray, i: int) -> float:
    """Sub-sample peak of a correlation sequence: golden-section on its
    band-limited interpolant. A parabola through three points is biased by
    up to ~0.07 sample on band-limited noise (measured by the known-answer
    test); the interpolant is not."""
    if not 0 < i < c.size - 1:
        return float(i)
    a, b = i - 1.0, i + 1.0
    gr = (math.sqrt(5) - 1) / 2

    def f(t):
        return -float(interp(c, np.array([t]), half=16)[0])
    x1, x2 = b - gr * (b - a), a + gr * (b - a)
    f1, f2 = f(x1), f(x2)
    for _ in range(40):
        if f1 < f2:
            b, x2, f2 = x2, x1, f1
            x1 = b - gr * (b - a)
            f1 = f(x1)
        else:
            a, x1, f1 = x1, x2, f2
            x2 = a + gr * (b - a)
            f2 = f(x2)
    return (a + b) / 2


def first_strong_lag(template, signal, lo, hi, frac=0.9):
    """The EARLIEST placement within `frac` of the best: a repeated drum
    pattern matches several times, and the take starts before the command."""
    lag, best, c = ncc_lag(template, signal, lo, hi)
    if lag is None:
        return None, 0.0
    cand = np.flatnonzero(c >= frac * best)
    # candidates within CLUSTER_S of each other are one placement seen at
    # neighbouring periods of a tone (a held note matches itself one and two
    # periods off at 0.9 of the peak -- the first dev run locked there); take
    # the best of the FIRST cluster
    gaps = np.flatnonzero(np.diff(cand) > int(CLUSTER_S * SR))
    first = cand[: gaps[0] + 1] if gaps.size else cand
    i = int(first[np.argmax(c[first])])
    lo_c = max(lo, 0)
    # refine around that earliest candidate
    l2, n2, _ = ncc_lag(template, signal, lo_c + i - 40, lo_c + i + 40)
    return l2, n2


def tone_f0(x: np.ndarray, sr: float, lo_hz: float, hi_hz: float) -> float | None:
    """Fundamental of a periodic tone: the strongest Hann-windowed spectral
    line in [lo, hi], refined by golden-section on the continuous DTFT
    magnitude. None when the signal is too short or silent."""
    x = np.asarray(x, dtype=np.float64)
    x = x - x.mean()
    if x.size < 64 or rms(x) == 0.0:
        return None
    w = np.hanning(x.size)
    xw = x * w
    nfft = 1 << int(math.ceil(math.log2(x.size * 16)))
    spec = np.abs(np.fft.rfft(xw, nfft))
    f = np.fft.rfftfreq(nfft, 1.0 / sr)
    band = (f >= lo_hz) & (f <= hi_hz)
    if not band.any():
        return None
    k = int(np.flatnonzero(band)[np.argmax(spec[band])])
    n = np.arange(x.size)

    def mag(fr):
        return -abs(np.dot(xw, np.exp(-2j * np.pi * fr * n / sr)))
    a, b = f[max(k - 1, 0)], f[min(k + 1, f.size - 1)]
    gr = (math.sqrt(5) - 1) / 2
    c1, c2 = b - gr * (b - a), a + gr * (b - a)
    m1, m2 = mag(c1), mag(c2)
    for _ in range(60):
        if m1 < m2:
            b, c2, m2 = c2, c1, m1
            c1 = b - gr * (b - a)
            m1 = mag(c1)
        else:
            a, c1, m1 = c1, c2, m2
            c2 = a + gr * (b - a)
            m2 = mag(c2)
    return float((a + b) / 2)


def band(x: np.ndarray, sr: int = SR) -> np.ndarray:
    """The declared analysis band (BAND_HZ): 4th-order Butterworth band-pass,
    zero-phase, no edge padding -- every comparison region sits at least
    0.5 s from a take's edges, so the edge transient is never scored."""
    from scipy.signal import butter, sosfiltfilt
    sos = butter(4, BAND_HZ, btype="bandpass", fs=sr, output="sos")
    return sosfiltfilt(sos, np.asarray(x, dtype=np.float64), padtype=None)


def clip_runs(x: np.ndarray, level: float, run: int) -> int:
    """Number of runs of >= `run` consecutive samples at |x| >= level."""
    m = np.abs(np.asarray(x)) >= level
    if not m.any():
        return 0
    d = np.diff(np.concatenate([[0], m.astype(np.int8), [0]]))
    starts, stops = np.flatnonzero(d == 1), np.flatnonzero(d == -1)
    return int(np.sum((stops - starts) >= run))


def block_rms(x: np.ndarray, block: int = BLOCK) -> np.ndarray:
    n = (len(x) // block) * block
    if n == 0:
        return np.zeros(0)
    return np.sqrt(np.mean(np.asarray(x[:n], dtype=np.float64).reshape(-1, block) ** 2, axis=1))


def sounding_extent(ref: np.ndarray, floor_dbfs: float = -60.0) -> tuple:
    """First and last sample of the reference above the floor (5 ms blocks)."""
    b = block_rms(ref)
    on = np.flatnonzero(b > 10 ** (floor_dbfs / 20))
    if on.size == 0:
        return None, None
    return int(on[0] * BLOCK), int(min(len(ref), (on[-1] + 1) * BLOCK))


# =============================================================================
# inputs
# =============================================================================
def sha256_file(path) -> str:
    return rr.sha256_file(path)


def read_capture(path) -> tuple:
    """(rate, float64 array (n, channels) in interface full scale)."""
    try:
        sr, x = wavfile.read(str(path))
    except (OSError, ValueError) as exc:
        raise Refused(f"{path}: unreadable WAV ({exc})")
    if x.ndim == 1:
        x = x[:, None]
    if x.dtype == np.int16:
        y = x / 32768.0
    elif x.dtype == np.int32:
        y = x / 2147483648.0
    elif x.dtype in (np.float32, np.float64):
        y = x.astype(np.float64)
    else:
        raise Refused(f"{path}: sample format {x.dtype} is not 16/24/32-bit PCM or float")
    return int(sr), y.astype(np.float64)


def load_reference(refdir: pathlib.Path, command_id: str) -> dict:
    if command_id == "silence":
        p = refdir / "silence.json"
        if not p.is_file():
            raise Refused(f"no silence reference at {p}")
        return {"record": json.loads(p.read_text()), "x": None}
    p = refdir / f"{command_id}.json"
    if not p.is_file():
        raise Refused(f"no reference for {command_id!r} at {p}")
    rec = json.loads(p.read_text())
    wav = refdir / str(rec.get("wav"))
    if not wav.is_file() or sha256_file(wav) != rec.get("wav_sha256"):
        raise Refused(f"reference {wav} missing or altered (sha256 differs from {p.name})")
    sr, x = rr.read_wav_int16(wav)
    if sr != SR:
        raise Refused(f"reference {wav} is {sr} Hz")
    cal = rec.get("calibration") or {}
    if cal.get("start") is None:
        raise Refused(f"reference {command_id} declares no calibration window")
    plan = refdir / f"{command_id}.plan.json"
    pl = json.loads(plan.read_text()) if plan.is_file() else None
    xf = x[:, 0] / 32768.0
    pad = np.zeros(SR // 2)       # the reference is silence outside itself: say so to the filter
    xb = band(np.concatenate([pad, xf, pad]))[pad.size:pad.size + xf.size]
    return {"record": rec, "x": xf, "xb": xb, "env": envelope(xb),
            "cal": (int(cal["start"]), int(cal["stop"])), "plan": pl, "hold": planned_hold(pl)}


SESSION_REQUIRED = {
    "image": ("bitstream_sha256", "programmer", "program_transcript", "readback"),
    "board": ("model", "revision", "power"),
    "dac": ("model", "wiring"),
    "interface": ("model", "sample_rate", "dac_channels", "gain", "processing", "recorder"),
    "calibration": ("take",),
}
TAKE_REQUIRED = ("id", "command_id", "wav")


def check_session(bundle: pathlib.Path, s: dict, manifest: dict) -> list:
    probs = []
    if s.get("schema") != SESSION_SCHEMA:
        probs.append(f"session schema {s.get('schema')!r} is not {SESSION_SCHEMA}")
    for sec, keys in SESSION_REQUIRED.items():
        d = s.get(sec)
        if not isinstance(d, dict):
            probs.append(f"session.{sec} missing")
            continue
        for k in keys:
            if d.get(k) in (None, ""):
                probs.append(f"session.{sec}.{k} missing")
    img = s.get("image") or {}
    want = manifest["image"]["bitstream_sha256"]
    if img.get("bitstream_sha256") and img["bitstream_sha256"] != want:
        probs.append(f"session.image.bitstream_sha256 {img['bitstream_sha256'][:12]} is not "
                     f"R0's {want[:12]}")
    t = img.get("program_transcript")
    if t and not ((bundle / t).is_file() and (bundle / t).stat().st_size > 0):
        probs.append(f"programming transcript {t} missing or empty")
    itf = s.get("interface") or {}
    if itf.get("sample_rate") not in (None, SR):
        probs.append(f"session.interface.sample_rate {itf.get('sample_rate')} is not {SR}")
    ch = itf.get("dac_channels")
    if ch is not None and not (isinstance(ch, list) and len(ch) == 2
                               and all(isinstance(c, int) and c >= 1 for c in ch)
                               and ch[0] != ch[1]):
        probs.append(f"session.interface.dac_channels {ch!r} is not two distinct 1-based inputs")
    if str(itf.get("processing", "none")).strip().lower() != "none":
        probs.append(f"session.interface.processing {itf.get('processing')!r}: must be 'none'")
    takes = s.get("takes")
    if not isinstance(takes, list) or not takes:
        probs.append("session.takes missing or empty")
        return probs
    ids = set()
    known = set(manifest["commands"]) | {"silence"}
    for i, tk in enumerate(takes):
        for k in TAKE_REQUIRED:
            if tk.get(k) in (None, ""):
                probs.append(f"take {i}: {k} missing")
        if tk.get("id") in ids:
            probs.append(f"take id {tk.get('id')!r} repeated")
        ids.add(tk.get("id"))
        if tk.get("command_id") not in known:
            probs.append(f"take {tk.get('id')}: command {tk.get('command_id')!r} is not an R0 "
                         f"release command (known: {sorted(known)})")
        if tk.get("command_id") not in (None, "silence") and not tk.get("host_capture"):
            probs.append(f"take {tk.get('id')}: host_capture missing (uart_host --capture)")
        if tk.get("wav") and not (bundle / tk["wav"]).is_file():
            probs.append(f"take {tk.get('id')}: {tk['wav']} not found")
    cal = (s.get("calibration") or {}).get("take")
    if cal and cal not in ids:
        probs.append(f"calibration take {cal!r} is not a take")
    return probs


def planned_hold(plan: dict | None) -> int | None:
    """A held-note command's hold, in device frames, as its own host log
    planned it: the gate-off event's due minus the last live write's apply
    frame. None for commands that are not live-writes-then-one-event."""
    if not plan:
        return None
    ev = [r for r in plan.get("rows", []) if r.get("kind") == "event"]
    live = [r for r in plan.get("rows", []) if r.get("kind") == "write"]
    if len(ev) != 1 or not live:
        return None
    return int(ev[0]["due"]) - int(live[-1]["apply_frame"])


def command_identity(plan_ref: dict | None, plan_cap: dict) -> list:
    """The host's own log of what it sent vs the reference's pinned command:
    the same register writes in the same order, and the same event spacing."""
    if plan_ref is None:
        return ["the reference carries no plan to compare the host log against"]

    def rows(p):
        out, first_due = [], None
        for r in p.get("rows", []):
            if r.get("kind") not in ("write", "event"):
                continue
            e = r.get("expect") or {}
            due = r.get("due", -1)
            if r["kind"] == "event":
                first_due = due if first_due is None else first_due
                due = due - first_due
            else:
                due = None
            out.append((r["kind"], e.get("flag"), e.get("sec"), e.get("addr"), e.get("data"), due))
        return out
    a, b = rows(plan_ref), rows(plan_cap)
    if a == b:
        return []
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return [f"host log differs from the released command at write {i}: {y} vs {x}"]
    return [f"host log has {len(b)} writes, the released command {len(a)}"]


# =============================================================================
# the analysis
# =============================================================================
def envelope(x: np.ndarray, sr: int = SR) -> np.ndarray:
    """Alignment envelope: sqrt of x^2 low-passed at ENV_LP_HZ (4th-order
    Butterworth, zero-phase). Used ONLY to choose which period of a tone the
    waveform correlation locks to -- never as a measured quantity."""
    from scipy.signal import butter, sosfiltfilt
    sos = butter(4, ENV_LP_HZ, fs=sr, output="sos")
    return np.sqrt(np.maximum(sosfiltfilt(sos, np.asarray(x, np.float64) ** 2, padtype=None),
                              0.0))


def _coarse(ref, cal, cap, sr=SR, *, ref_env=None, polarity=False):
    """Where the reference's calibration window starts in `cap`: the envelope
    finds the placement (a tone's periods are indistinguishable to the
    waveform), the waveform then refines it to the nearest correlation peak.
    Returns (lag, ncc, envelope_lag, envelope_ncc); with `polarity`, ncc is
    signed: the better of cap and -cap, negative when inverted."""
    t = ref[cal[0]:cal[0] + int(COARSE_S * sr)]
    renv = envelope(ref) if ref_env is None else ref_env
    te = renv[cal[0]:cal[0] + int(COARSE_S * sr)]
    elag, encc = first_strong_lag(te, envelope(cap), 0, cap.size - t.size)
    if elag is None:
        return None, 0.0, None, 0.0
    lag, ncc, _ = ncc_lag(t, cap, int(elag) - 128, int(elag) + 128, center=elag)
    if polarity:
        nlag, nncc, _ = ncc_lag(t, -cap, int(elag) - 128, int(elag) + 128, center=elag)
        if nncc > ncc:
            return nlag, -nncc, elag, encc
    return lag, ncc, elag, encc


def _local_lags(ref, cap, d, rho, lo, hi, *, search=128, min_dbfs=-40.0, min_ncc=0.9):
    """(m, lag - predicted, ncc) for WIN-sample reference windows in [lo, hi)."""
    out = []
    for m in range(lo, hi - WIN, WIN):
        t = ref[m:m + WIN]
        if dbfs(rms(t)) < min_dbfs:
            continue
        pred = d + m / rho
        lag, ncc, _ = ncc_lag(t, cap, int(round(pred)) - search, int(round(pred)) + search,
                              center=pred)
        if lag is None or ncc < min_ncc:
            continue
        out.append((m, lag - pred, ncc))
    return out


def calibrate(ref, cal, cap, sr=SR) -> dict:
    """Session constants from the declared calibration take (whole take)."""
    lag, ncc, _, _ = _coarse(ref, cal, cap, sr)
    if lag is None or ncc < LIMITS["coarse_ncc_min"]:
        return {"ok": False, "reason": f"the reference is not in the calibration take "
                                        f"(coarse ncc {ncc:.2f})"}
    d0 = lag - cal[0]
    a, b = sounding_extent(ref)
    pts = _local_lags(ref, cap, d0, 1.0, a, b)
    if len(pts) < 5 or (pts[-1][0] - pts[0][0]) < LIMITS["clock_min_span_s"] * sr:
        raise Refused(f"calibration take spans {len(pts)} usable windows "
                      f"({(pts[-1][0] - pts[0][0]) / sr if pts else 0:.2f} s): too short to "
                      f"estimate the sample clock (needs >= 5 over "
                      f"{LIMITS['clock_min_span_s']} s)")
    m = np.array([p[0] for p in pts], dtype=np.float64)
    n = np.array([d0 + p[0] + p[1] for p in pts])
    keep = np.ones(m.size, bool)
    for _ in range(3):
        B, A = np.polyfit(m[keep], n[keep], 1)
        res = n - (A + B * m)
        keep = np.abs(res) <= max(0.5, 3 * np.std(res[keep]))
    rho, d = 1.0 / B, float(A)
    p = warp_reference(ref, cap.size, d, rho)
    lo, hi = int(d + a / rho), int(min(cap.size, d + b / rho))
    g = float(np.dot(cap[lo:hi], p[lo:hi]) / max(np.dot(p[lo:hi], p[lo:hi]), 1e-30))
    return {"ok": True, "rho": rho, "ppm": (rho - 1.0) * 1e6, "gain": g,
            "gain_db": dbfs(abs(g)), "delay": d, "windows": int(keep.sum()),
            "span_s": float((m[keep][-1] - m[keep][0]) / sr),
            "line_residual_max": float(np.max(np.abs(res[keep])))}


def analyse_take(take, ref, cap_all, dac, frozen, noise_floor_dbfs, sr=SR,
                 hold_offset=None, capb_all=None) -> dict:
    """Every property of one take. `frozen` = {rho, gain}; d_t is estimated
    here from the declared window only.

    `hold_offset`: frames by which this take's host log planned a different
    hold from the reference's. uart_host anchors a held note's gate-off to an
    OBSERVED gate frame (a STATUS minus its round trip), so on hardware the
    hold can differ from the dry-run's by a few frames, and the release then
    lands that much later or earlier than the reference's. The offset is
    read from the host log, never fitted from audio; when it is not zero the
    release cannot be compared sample-for-sample with this reference, so
    the waveform comparisons stop 5 ms before the reference's release and
    the record says so (`release_compared: false`). The stuck-note check
    still covers the whole take."""
    out = {"id": take["id"], "command_id": take["command_id"], "fails": {}, "metrics": {}}
    F = out["fails"]
    M = out["metrics"]
    chans = [cap_all[:, c - 1] for c in dac]
    A, Bc = chans
    # clipping: anywhere in the take, on either DAC channel
    clips = [clip_runs(c, LIMITS["clip_level"], LIMITS["clip_run"]) for c in chans]
    M["clip_runs"] = clips
    if any(clips):
        F["clipping"] = f"{clips} runs of >= {LIMITS['clip_run']} samples at full scale"
    if ref["x"] is None:                              # silence take
        lv = [dbfs(rms(c - c.mean())) for c in chans]
        M["noise_dbfs"] = lv
        if max(lv) > LIMITS["noise_dbfs_max"]:
            F["noise"] = f"floor {max(lv):.1f} dBFS > {LIMITS['noise_dbfs_max']} dBFS"
        return out
    # every waveform comparison below is in the declared analysis band
    x_raw, A_raw = ref["x"], A
    if capb_all is None:
        capb_all = np.column_stack([band(cap_all[:, k]) for k in range(cap_all.shape[1])])
    x, cal = ref["xb"], ref["cal"]
    A, Bc = capb_all[:, dac[0] - 1], capb_all[:, dac[1] - 1]
    rho, g = frozen["rho"], frozen["gain"]
    # routing: where is the reference?
    nccs, found = {}, {}
    for k in range(cap_all.shape[1]):
        found[k + 1] = _coarse(x, cal, capb_all[:, k], sr, ref_env=ref["env"], polarity=True)
        nccs[k + 1] = round(found[k + 1][1], 3)
    M["routing_ncc"] = nccs
    route = []
    for c in dac:
        if nccs[c] < LIMITS["coarse_ncc_min"]:
            route.append(f"declared input {c} does not carry the reference (ncc {nccs[c]})")
    for k, v in nccs.items():
        if k not in dac and abs(v) > LIMITS["foreign_ncc_max"]:
            route.append(f"the reference is on undeclared input {k} (ncc {v})")
    # the fixed per-take delay, from the declared window only
    lag, ncc, elag, encc = found[dac[0]]
    if lag is None or ncc < LIMITS["coarse_ncc_min"]:
        F["routing"] = "; ".join(route) or f"reference not found (ncc {ncc:.2f})"
        # a tone at the wrong pitch does not correlate as a waveform, but its
        # envelope still places it: measure the pitch there, so the failure
        # carries its cause (the first dev run reported only routing/silence)
        if elag is not None and encc >= LIMITS["coarse_ncc_min"]:
            _pitch(take, x_raw, A_raw, elag - cal[0] / rho, rho, M, F, sr)
        if "pitch" not in F:
            lvA = dbfs(rms(A))
            F["silence"] = f"declared DAC input carries {lvA:.1f} dBFS and no reference"
        return out
    wl, wn, _ = ncc_lag(x[cal[0]:cal[1]], A, int(round(lag)) - 40, int(round(lag)) + 40,
                        center=lag)
    d = wl - cal[0] / rho
    M["delay_samples"] = round(d, 3)
    M["cal_window_ncc"] = round(wn, 4)
    a_ref, b_ref = sounding_extent(x)
    end_cap = d + len(x) / rho
    if end_cap > A.size:
        raise Refused(f"take {take['id']} ends {(end_cap - A.size) / sr:.2f} s before its "
                      "reference does: record the whole tail")
    p = g * warp_reference(x, A.size, d, rho)
    # the evaluation region ends END_GUARD_S before the reference does: a
    # reference can end while still sounding (the m5a presets' release is at
    # -27 dBFS when the 1 s render tail stops) and the capture goes on, so
    # the analysis band's filter differs between the two near that edge
    e0 = int(math.ceil(d + cal[1] / rho))
    e1 = int(d + (len(x) - END_GUARD_S * sr) / rho)
    # the two DAC channels: dual-mono
    seg = slice(int(d + a_ref / rho), int(d + b_ref / rho))
    ca, cb = A[seg], Bc[seg]
    if rms(ca) > 0 and rms(cb) > 0:
        corr = float(np.dot(ca, cb) / math.sqrt(np.dot(ca, ca) * np.dot(cb, cb)))
        lvd = dbfs(rms(cb)) - dbfs(rms(ca))
    else:
        corr, lvd = 0.0, float("inf")
    M["dac_channel_corr"], M["dac_channel_level_db"] = round(corr, 5), round(lvd, 3)
    if corr < LIMITS["channels_corr_min"]:
        route.append(f"DAC inputs {dac} disagree (correlation {corr:.3f}"
                     + (": one is inverted" if corr < -0.9 else "") + ")")
    elif abs(lvd) > LIMITS["channels_level_db_max"]:
        route.append(f"DAC inputs {dac} differ in level by {lvd:.2f} dB")
    if route:
        F["routing"] = "; ".join(route)
    # unexpected silence, per channel, where the prediction sounds
    for c, ch in zip(dac, (A, Bc)):
        lv = dbfs(rms(ch[seg]))
        want = dbfs(rms(p[seg]))
        if lv < max(noise_floor_dbfs + LIMITS["silent_margin_db"], want - 20.0):
            F["silence"] = (F.get("silence", "") + f"input {c}: {lv:.1f} dBFS where "
                            f"{want:.1f} dBFS is predicted; ").strip()
    # residual over the evaluation region (after the calibration window)
    if hold_offset:
        nz = np.flatnonzero(x_raw)
        rel = int(nz[0]) + int(ref["hold"]) - BLOCK if nz.size and ref.get("hold") else None
        M["hold_offset_frames"] = int(hold_offset)
        M["release_compared"] = False
        if rel is not None:
            e1 = min(e1, int(d + rel / rho))
    ev = slice(e0, e1)
    er = float(np.sum((A[ev] - p[ev]) ** 2) / max(np.sum(p[ev] ** 2), 1e-30))
    M["residual_db"] = round(10 * math.log10(max(er, 1e-30)), 2)
    if M["residual_db"] > LIMITS["residual_db_max"]:
        F["residual"] = f"{M['residual_db']} dB > {LIMITS['residual_db_max']} dB"
    # local lags and gains, measured AGAINST the frozen alignment
    pts = _local_lags(x, A, d, rho, int(cal[1]), int(min(len(x), (e1 - d) * rho)))
    if pts:
        dev = np.array([q[1] for q in pts])
        ms = np.array([q[0] for q in pts], dtype=np.float64)
        M["local_lag_dev"] = [round(float(dev.min()), 3), round(float(dev.max()), 3)]
        span = (ms[-1] - ms[0]) / sr
        if span >= LIMITS["clock_min_span_s"] and len(pts) >= 5:
            # robust line: a lone narrowband window can sit samples off (below)
            fit = np.polyfit(ms, dev, 1)
            keep = np.abs(dev - np.polyval(fit, ms)) <= max(1.0, 3 * float(np.median(
                np.abs(dev - np.polyval(fit, ms)))))
            if keep.sum() >= 5:
                fit = np.polyfit(ms[keep], dev[keep], 1)
            slope = fit[0]                                  # samples per reference sample
            M["clock_drift_ppm"] = round(float(slope * 1e6), 2)
            if abs(M["clock_drift_ppm"]) > LIMITS["clock_ppm_drift_max"]:
                F["clock"] = (f"this take's clock differs from the frozen calibration by "
                              f"{M['clock_drift_ppm']} ppm")
            lin = dev - np.polyval(fit, ms)
        else:
            lin = dev - np.median(dev)
        # A slip is SUSTAINED: at least TIMING_MIN_WINDOWS windows (or 10 %)
        # beyond the limit. One narrowband window is not a slip: its lag is a
        # phase delay, and AC coupling at fc moves it by fc*SR/(2 pi f^2)
        # samples -- 3.8 samples for a 5 Hz high-pass on a ~110 Hz bass window,
        # which failed the clean synthetic session on the full bar808 reference.
        off = np.abs(lin) > LIMITS["timing_slip_max_samples"]
        scatter = float(np.max(np.abs(lin)))
        M["timing_scatter"] = round(scatter, 3)
        M["timing_windows_off"] = [int(off.sum()), int(off.size)]
        if off.sum() >= max(TIMING_MIN_WINDOWS, int(math.ceil(0.1 * off.size))):
            F["timing"] = (f"local lag moves up to {scatter:.2f} samples against the frozen "
                           f"delay in {int(off.sum())} of {off.size} windows "
                           f"(limit {LIMITS['timing_slip_max_samples']})")
        elif abs(float(np.median(dev))) > LIMITS["timing_slip_max_samples"]:
            F["timing"] = f"median local lag {float(np.median(dev)):.2f} samples off the frozen delay"
        gains = []
        for m, _, _ in pts:
            lo = int(d + m / rho)
            pp, cc = p[lo:lo + WIN], A[lo:lo + WIN]
            den = float(np.dot(pp, pp))
            if den > 0:
                gains.append(float(np.dot(cc, pp) / den))
        if gains:
            gm = float(np.median(gains))
            M["gain_change_db"] = round(dbfs(abs(gm)) if gm else -240.0, 3)
            if abs(M["gain_change_db"]) > LIMITS["gain_change_db_max"]:
                F["gain"] = f"median local gain {M['gain_change_db']:+.2f} dB against the frozen gain"
    # dropouts and stuck output, 5 ms blocks over the evaluation region
    pb, cb_ = block_rms(p[ev]), block_rms(A[ev])
    n = min(pb.size, cb_.size)
    pb, cb_ = pb[:n], cb_[:n]
    lim = 10 ** (LIMITS["dropout_ref_dbfs_min"] / 20)
    drop = np.flatnonzero((pb >= lim) & (cb_ < pb * 10 ** (-LIMITS["dropout_drop_db"] / 20)))
    M["dropout_blocks"] = int(drop.size)
    if drop.size:
        F["dropout"] = (f"{drop.size} x 5 ms blocks {LIMITS['dropout_drop_db']:.0f} dB below "
                        f"prediction, first at {(e0 + drop[0] * BLOCK) / sr:.3f} s")
    quiet = pb < 10 ** (LIMITS["stuck_ref_dbfs_max"] / 20)
    base = np.maximum(pb, 10 ** (max(noise_floor_dbfs, -100.0) / 20))
    loud = cb_ > base * 10 ** (LIMITS["stuck_rise_db"] / 20)
    stuck = int(np.sum(quiet & loud))
    M["stuck_s"] = round(stuck * BLOCK / sr, 3)
    if stuck * BLOCK / sr >= LIMITS["stuck_min_s"]:
        F["stuck"] = f"{M['stuck_s']} s of output where the reference is silent"
    _pitch(take, x_raw, A_raw, d, rho, M, F, sr)
    out["_aligned"] = (d, rho)
    return out


def _pitch(take, x_raw, A_raw, d, rho, M, F, sr=SR):
    """f0 of a single-oscillator tone take, capture against reference, over
    the reference's loud span mapped through the frozen alignment."""
    if take["command_id"] in TONE_COMMANDS:
        env = block_rms(x_raw)
        loud_b = np.flatnonzero(env >= env.max() * 0.1)
        s0, s1 = int(loud_b[0] * BLOCK), int((loud_b[-1] + 1) * BLOCK)
        f_ref = tone_f0(x_raw[s0:s1], sr, 30.0, 5000.0)
        c0, c1 = int(d + s0 / rho), int(d + s1 / rho)
        f_cap = tone_f0(A_raw[c0:c1], sr, 30.0, 5000.0)
        if f_ref and f_cap:
            cents = 1200 * math.log2(f_cap / f_ref)
            M["pitch"] = {"f_ref_hz": round(f_ref, 4), "f_cap_hz": round(f_cap, 4),
                          "cents": round(cents, 3),
                          "cents_vs_clock": round(cents - 1200 * math.log2(rho), 3)}
            if abs(M["pitch"]["cents_vs_clock"]) > LIMITS["pitch_cents_max"]:
                F["pitch"] = (f"{M['pitch']['cents_vs_clock']:+.2f} cents against the frozen "
                              "clock ratio")


def analyse(bundle: pathlib.Path, refdir: pathlib.Path = REFERENCES,
            manifest_path=rr.MANIFEST) -> dict:
    """The whole session -> a record with verdict PASS / FAIL / REFUSED."""
    rec = {"schema": RECORD_SCHEMA, "criterion": CRITERION, "limits": LIMITS,
           "bundle": str(bundle), "references": str(refdir), "inputs_sha256": {},
           "verdict": REFUSED, "reasons": [], "takes": [], "properties": {}}
    try:
        manifest = rr.load_manifest(manifest_path)
        sp = bundle / "session.json"
        if not sp.is_file():
            rec["operator_blocked"] = True
            raise Refused(f"operator-blocked: no capture session at {sp} (the rig has not "
                          "been recorded; docs/capture-r0.md)")
        try:
            s = json.loads(sp.read_text())
        except ValueError as exc:
            raise Refused(f"{sp}: unreadable ({exc})")
        rec["inputs_sha256"]["session.json"] = sha256_file(sp)
        probs = check_session(bundle, s, manifest)
        if probs:
            raise Refused("capture metadata incomplete: " + "; ".join(probs))
        rec["identity"] = {"bitstream_sha256": s["image"]["bitstream_sha256"],
                           "readback": bool(s["image"]["readback"]),
                           "programming": ("readback-verified" if s["image"]["readback"] else
                                           "programming transcript only -- NOT a readback")}
        rec["inputs_sha256"][s["image"]["program_transcript"]] = sha256_file(
            bundle / s["image"]["program_transcript"])
        dac = s["interface"]["dac_channels"]
        takes, refs, caps, capb, hold_offsets = s["takes"], {}, {}, {}, {}
        for tk in takes:
            refs[tk["command_id"]] = refs.get(tk["command_id"]) or load_reference(
                refdir, tk["command_id"])
            sr, y = read_capture(bundle / tk["wav"])
            rec["inputs_sha256"][tk["wav"]] = sha256_file(bundle / tk["wav"])
            if sr != SR:
                raise Refused(f"take {tk['id']}: {sr} Hz, not {SR} (no resampling is allowed)")
            if y.shape[1] < max(dac):
                raise Refused(f"take {tk['id']}: {y.shape[1]} channels; the DAC is declared on "
                              f"input {max(dac)}")
            caps[tk["id"]] = y
            capb[tk["id"]] = np.column_stack([band(y[:, k]) for k in range(y.shape[1])])
            if tk["command_id"] != "silence":
                hp = bundle / f"{tk['host_capture']}.plan.json"
                if not hp.is_file():
                    raise Refused(f"take {tk['id']}: host log {hp} missing")
                rec["inputs_sha256"][str(hp.relative_to(bundle))] = sha256_file(hp)
                host_plan = json.loads(hp.read_text())
                diff = command_identity(refs[tk["command_id"]].get("plan"), host_plan)
                h_cap, h_ref = planned_hold(host_plan), refs[tk["command_id"]].get("hold")
                if h_cap is not None and h_ref is not None:
                    hold_offsets[tk["id"]] = h_cap - h_ref
                if diff:
                    raise Refused(f"take {tk['id']} is not the released command "
                                  f"{tk['command_id']}: " + "; ".join(diff))
        have = {tk["command_id"] for tk in takes}
        missing = [c for c in REQUIRED_COMMANDS if c not in have]
        counts = {}
        for tk in takes:
            counts[tk["command_id"]] = counts.get(tk["command_id"], 0) + 1
        if missing:
            raise Refused(f"diagnostic set incomplete: no take of {missing}")
        if not any(v >= 2 for c, v in counts.items() if c != "silence"):
            raise Refused("no repeat take: repeat-take stability cannot be measured")
        # the noise floor, from the silence take(s)
        sil = [analyse_take(tk, refs["silence"], caps[tk["id"]], dac, None, -200.0)
               for tk in takes if tk["command_id"] == "silence"]
        floor = max(max(t["metrics"]["noise_dbfs"]) for t in sil)
        rec["noise_floor_dbfs"] = round(floor, 2)
        # the frozen session constants
        ctk = next(tk for tk in takes if tk["id"] == s["calibration"]["take"])
        if ctk["command_id"] == "silence":
            raise Refused("the calibration take is the silence take")
        cref = refs[ctk["command_id"]]
        cal = calibrate(cref["xb"], cref["cal"], capb[ctk["id"]][:, dac[0] - 1])
        rec["calibration"] = {"take": ctk["id"], **{k: (round(v, 9) if isinstance(v, float)
                                                        else v) for k, v in cal.items()}}
        results = list(sil)
        if not cal["ok"]:
            rec["verdict"] = FAIL
            rec["reasons"] = [f"calibration: {cal['reason']}"]
            rec["takes"] = [{k: v for k, v in t.items() if not k.startswith("_")} for t in results]
            return rec
        frozen = {"rho": cal["rho"], "gain": cal["gain"]}
        clock_fail = abs(cal["ppm"]) > LIMITS["clock_ppm_max"]
        for tk in takes:
            if tk["command_id"] == "silence":
                continue
            results.append(analyse_take(tk, refs[tk["command_id"]], caps[tk["id"]], dac,
                                        frozen, floor, hold_offset=hold_offsets.get(tk["id"]),
                                        capb_all=capb[tk["id"]]))
        # repeat-take stability, on the reference timeline
        by_cmd = {}
        for t, tk in zip(results, [*[x for x in takes if x["command_id"] == "silence"],
                                   *[x for x in takes if x["command_id"] != "silence"]]):
            if "_aligned" in t:
                by_cmd.setdefault(tk["command_id"], []).append((t, tk))
        for cmd, lst in by_cmd.items():
            if len(lst) < 2:
                continue
            x = refs[cmd]["xb"]
            a, b = sounding_extent(x)
            b = min(b, len(x) - int(END_GUARD_S * SR))
            if any(hold_offsets.get(k["id"]) for _, k in lst) and refs[cmd].get("hold"):
                # the holds were planned differently: compare up to the release
                b = min(b, int(np.flatnonzero(refs[cmd]["x"])[0]) + int(refs[cmd]["hold"]) - BLOCK)
            (t0, k0) = lst[0]
            u0 = unwarp_capture(capb[k0["id"]][:, dac[0] - 1], len(x), *t0["_aligned"])[a:b]
            for t1, k1 in lst[1:]:
                u1 = unwarp_capture(capb[k1["id"]][:, dac[0] - 1], len(x), *t1["_aligned"])[a:b]
                r = 10 * math.log10(max(float(np.sum((u1 - u0) ** 2)) /
                                        max(float(np.sum(u0 ** 2)), 1e-30), 1e-30))
                t1["metrics"]["repeat_db_vs_" + k0["id"]] = round(r, 2)
                if r > LIMITS["repeat_db_max"]:
                    t1["fails"]["repeat"] = (f"differs from take {k0['id']} by {r:.1f} dB "
                                             f"(limit {LIMITS['repeat_db_max']} dB)")
        rec["takes"] = [{k: v for k, v in t.items() if not k.startswith("_")} for t in results]
        props = {p: "PASS" for p in PROPERTIES}
        props["channel-identity"] = ("UNOBSERVABLE: R0 is dual-mono (rtl-sketch/i2s_tx.v sends "
                                     "one sample on both channels); a left/right swap cannot be "
                                     "seen in the audio")
        reasons = []
        if clock_fail:
            props["clock"] = "FAIL"
            reasons.append(f"clock: session offset {cal['ppm']:.1f} ppm > "
                           f"{LIMITS['clock_ppm_max']} ppm")
        for t in results:
            for p, why in t["fails"].items():
                props[p] = "FAIL"
                reasons.append(f"{t['id']}: {p}: {why}")
        rec["properties"] = props
        rec["reasons"] = reasons
        rec["verdict"] = FAIL if reasons else PASS
        return rec
    except Refused as exc:
        rec["verdict"] = REFUSED
        rec["reasons"] = [str(exc)]
        return rec


# =============================================================================
# synthetic sessions: the reference, through a declared, known analog path
# =============================================================================
CLEAN = {"gain": 0.30, "ppm": 35.0, "noise_dbfs": -100.0, "hpf_hz": 5.0,
         "delays": (4321.37, 9876.5, 1500.25, 22222.75, 777.6, 3333.3, 12000.1, 2500.9, 6000.4)}
SYNTH_TAKES = (("silence-1", "silence"), ("demo-1", "demo"), ("tone-1", "held-m5a-saw"),
               ("pulse-1", "held-m5a-pulse"), ("held-1", "held-default"),
               ("phrase-1", "phrase-m5a"), ("drums-1", "bar808-full"), ("demo-2", "demo"),
               ("held-2", "held-default"))
# defect -> (the property that must FAIL, the take it is applied to)
DEFECTS = {
    "delay-slip": ("timing", "demo-2"),
    "gain": ("gain", "demo-2"),
    "clock-drift": ("clock", "demo-2"),
    "noise": ("noise", "*"),
    "dropout": ("dropout", "drums-1"),
    "clipping": ("clipping", "drums-1"),
    "stuck": ("stuck", "held-2"),
    "pitch": ("pitch", "tone-1"),
    "one-channel": ("routing", "drums-1"),
    "inverted": ("routing", "drums-1"),
    "wrong-input": ("routing", "drums-1"),
    "swap": (None, "*"),            # expected BLIND: R0 is dual-mono
}


def _hpf(x, hz, sr=SR):
    if not hz:
        return x
    a = math.exp(-2 * math.pi * hz / sr)
    from scipy.signal import lfilter
    return lfilter([(1 + a) / 2, -(1 + a) / 2], [1, -a], x)


def synth_take(ref_x, *, delay, gain, ppm, noise_dbfs, hpf_hz, rng, defect=None,
               tail_s=1.0, sr=SR) -> np.ndarray:
    """Four interface channels; the DAC on inputs 3 and 4."""
    rho = 1.0 + ppm * 1e-6
    n = int(math.ceil(delay + (len(ref_x) if ref_x is not None else sr) / rho + tail_s * sr))
    if ref_x is None:
        dacsig = np.zeros(n)
    elif defect == "delay-slip":
        half = n // 2
        dacsig = gain * warp_reference(ref_x, n, delay, rho)
        dacsig[half:] = gain * warp_reference(ref_x, n, delay + 24.0, rho)[half:]
    elif defect == "clock-drift":
        dacsig = gain * warp_reference(ref_x, n, delay, rho * (1 + 300e-6))
    elif defect == "pitch":
        dacsig = gain * warp_reference(ref_x, n, delay, rho * 2 ** (20 / 1200))
    elif defect == "gain":
        dacsig = gain * 10 ** (3 / 20) * warp_reference(ref_x, n, delay, rho)
    elif defect == "clipping":
        w = warp_reference(ref_x, n, delay, rho)
        # peaks at twice full scale; the interface's converter clips it (the
        # final clip below), AFTER the analog path -- clipping before the
        # AC-coupling high-pass would tilt the flat tops off full scale
        dacsig = 2.0 * w / np.max(np.abs(w))
    else:
        dacsig = gain * warp_reference(ref_x, n, delay, rho)
    if defect == "stuck" and ref_x is not None:
        env = block_rms(dacsig)
        loud = np.flatnonzero(env >= env.max() * 0.3)
        end = int((loud[-1] + 1) * BLOCK)
        loop = dacsig[end - 960:end]
        reps = int(math.ceil((n - end) / loop.size))
        dacsig[end:] = np.tile(loop, reps)[:n - end]
    dacsig = _hpf(dacsig, hpf_hz, sr)
    nl = 10 ** (noise_dbfs / 20)
    y = rng.normal(0.0, nl, (n, 4))
    y[:, 2] += dacsig
    y[:, 3] += dacsig
    if defect == "dropout" and ref_x is not None:
        s0 = int(delay + 0.6 * len(ref_x) / rho)
        env = block_rms(dacsig)
        # move to the loudest block at or after 60 %, so the dropout is audible
        k = s0 // BLOCK + int(np.argmax(env[s0 // BLOCK:]))
        y[k * BLOCK:k * BLOCK + int(0.02 * sr), 2:4] = 0.0
    if defect == "noise":
        y += rng.normal(0.0, 10 ** (-45 / 20), y.shape)
    if defect == "one-channel":
        y[:, 3] = rng.normal(0.0, nl, n)
    if defect == "inverted":
        y[:, 3] = -y[:, 3]
    if defect == "wrong-input":
        y[:, [0, 1, 2, 3]] = y[:, [2, 3, 0, 1]]
    if defect == "swap":
        y[:, [2, 3]] = y[:, [3, 2]]
    return np.clip(y, -1.0, 1.0 - 2 ** -23)


def write_capture_wav(path, y: np.ndarray, sr=SR) -> None:
    """24-bit PCM, as the procedure's recorder writes."""
    import wave
    q = np.round(np.clip(y, -1.0, 1.0 - 2 ** -23) * 2 ** 23).astype(np.int32)
    b = q.astype("<i4").view(np.uint8).reshape(-1, 4)[:, :3].tobytes()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(y.shape[1])
        w.setsampwidth(3)
        w.setframerate(sr)
        w.writeframes(b)


def synth_session(out: pathlib.Path, *, refdir=REFERENCES, defect=None, seed=1,
                  takes=SYNTH_TAKES, manifest_path=rr.MANIFEST) -> pathlib.Path:
    manifest = rr.load_manifest(manifest_path)
    out.mkdir(parents=True, exist_ok=True)
    (out / "takes").mkdir(exist_ok=True)
    (out / "host").mkdir(exist_ok=True)
    rng = np.random.default_rng(seed)
    target = DEFECTS.get(defect, (None, None))[1] if defect else None
    tlist = []
    for i, (tid, cmd) in enumerate(takes):
        ref = load_reference(refdir, cmd)
        this = defect if target in ("*", tid) else None
        y = synth_take(ref["x"], delay=CLEAN["delays"][i % len(CLEAN["delays"])],
                       gain=CLEAN["gain"], ppm=CLEAN["ppm"], noise_dbfs=CLEAN["noise_dbfs"],
                       hpf_hz=CLEAN["hpf_hz"], rng=rng, defect=this)
        write_capture_wav(out / "takes" / f"{tid}.wav", y)
        t = {"id": tid, "command_id": cmd, "wav": f"takes/{tid}.wav",
             "started": "synthetic", "host_capture": None}
        if cmd != "silence":
            shutil.copyfile(refdir / f"{cmd}.plan.json", out / "host" / f"{tid}.plan.json")
            t["host_capture"] = f"host/{tid}"
        tlist.append(t)
    (out / "program.txt").write_text("SYNTHETIC: no board was programmed\n")
    s = {"schema": SESSION_SCHEMA, "synthetic": {"defect": defect, "seed": seed, **CLEAN},
         "image": {"bitstream_sha256": manifest["image"]["bitstream_sha256"],
                   "programmer": "synthetic", "program_transcript": "program.txt",
                   "readback": False},
         "board": {"model": "synthetic", "revision": "synthetic", "power": "synthetic"},
         "dac": {"model": "synthetic", "wiring": "synthetic"},
         "interface": {"model": "synthetic", "sample_rate": SR, "dac_channels": [3, 4],
                       "gain": "fixed", "processing": "none", "recorder": "synthetic"},
         "calibration": {"take": "demo-1"}, "takes": tlist}
    (out / "session.json").write_text(json.dumps(s, indent=1) + "\n")
    return out


# The procedure's takes (docs/capture-r0.md section 7), in recording order.
PROCEDURE_TAKES = SYNTH_TAKES
RECORDER = "sox -D -t coreaudio M4 -b 24 takes/<id>.wav trim 0 <seconds>"


def new_session(bundle: pathlib.Path, manifest_path=rr.MANIFEST) -> pathlib.Path:
    """The session.json skeleton the operator completes. Everything the
    procedure fixes is filled in; everything only the operator can know is
    left empty, and `analyse` REFUSES until it is filled."""
    manifest = rr.load_manifest(manifest_path)
    p = bundle / "session.json"
    if p.exists():
        raise Refused(f"{p} exists; a session is never overwritten")
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "takes").mkdir(exist_ok=True)
    (bundle / "host").mkdir(exist_ok=True)
    s = {"schema": SESSION_SCHEMA,
         "operator": "", "date": datetime.date.today().isoformat(),
         "image": {"bitstream_sha256": manifest["image"]["bitstream_sha256"],
                   "bitstream": manifest["image"]["bitstream"],
                   "programmer": "", "program_transcript": "program.txt",
                   "readback": False,
                   "_readback": "false unless an actual configuration readback was compared; "
                                "a programming transcript is not a readback"},
         "board": {"model": "Arty A7-100T", "revision": "", "power": "",
                   "_power": "USB (J10) or external 7-15 V on J13; the Arty A7 selects "
                             "automatically and has no power-select jumper"},
         "dac": {"model": "Adafruit PCM5102 I2S DAC breakout #6250",
                 "wiring": "fpga/ARTY.md Wiring: JA1 BCK, JA2 WSEL, JA3 DIN, JA5 GND, "
                           "JA6 VIN (3.3 V); MCK, DE, FIL, MU, FM unconnected"},
         "interface": {"model": "MOTU M4", "sample_rate": SR, "dac_channels": [3, 4],
                       "gain": "fixed: M4 line inputs 3/4 have no gain control",
                       "processing": "none", "monitor": "", "recorder": RECORDER,
                       "cable": "3.5 mm TRS to two 1/4-inch TS, tip -> input 3, ring -> input 4"},
         "calibration": {"take": "demo-1"},
         "takes": [{"id": tid, "command_id": cmd, "wav": f"takes/{tid}.wav",
                    "host_capture": None if cmd == "silence" else f"host/{tid}",
                    "command": (None if cmd == "silence" else
                                manifest["commands"][cmd]["command"]),
                    "started": ""} for tid, cmd in PROCEDURE_TAKES]}
    p.write_text(json.dumps(s, indent=1) + "\n")
    return p


def stub_analyse(bundle, refdir=REFERENCES, manifest_path=rr.MANIFEST) -> dict:
    """The starting stub: right interface, no behaviour. Every control must be
    NOT caught against it (docs/verification-rules.md rule 1)."""
    return {"schema": RECORD_SCHEMA, "verdict": PASS, "reasons": [], "takes": [],
            "properties": {p: "PASS" for p in PROPERTIES}}


def run_controls(out: pathlib.Path, *, refdir=REFERENCES, analyser=analyse,
                 defects=None) -> dict:
    """The clean synthetic session must PASS with its known answers recovered;
    each defect must FAIL with its own property among the failures; the swap
    must be reported, honestly, as unobservable (BLIND)."""
    res = {"clean": None, "defects": {}, "matrix": {}}
    clean = analyser(synth_session(out / "clean", refdir=refdir), refdir)
    ok_clean = clean["verdict"] == PASS
    known = {}
    if clean.get("calibration"):
        c = clean["calibration"]
        known = {"ppm": [CLEAN["ppm"], c.get("ppm")],
                 "gain_db": [dbfs(CLEAN["gain"]), c.get("gain_db")]}
        ok_clean &= abs(c["ppm"] - CLEAN["ppm"]) < 2.0 and abs(c["gain_db"] - dbfs(CLEAN["gain"])) < 0.2
    res["clean"] = {"verdict": clean["verdict"], "reasons": clean["reasons"][:6],
                    "known_answers": known, "ok": bool(ok_clean)}
    all_ok = ok_clean
    for name in (defects or DEFECTS):
        prop, _ = DEFECTS[name]
        r = analyser(synth_session(out / name, refdir=refdir, defect=name), refdir)
        failed = {p for p, v in (r.get("properties") or {}).items() if v == "FAIL"}
        res["matrix"][name] = {p: ("MOVED" if p in failed else "BLIND") for p in PROPERTIES}
        if prop is None:
            caught = r["verdict"] == PASS and "UNOBSERVABLE" in str(
                (r.get("properties") or {}).get("channel-identity", ""))
            outcome = "BLIND as declared" if caught else "NOT as declared"
        else:
            caught = r["verdict"] == FAIL and prop in failed
            outcome = "caught" if caught else "NOT caught"
        res["defects"][name] = {"intended": prop, "verdict": r["verdict"], "caught": caught,
                                "outcome": outcome, "failed": sorted(failed),
                                "reasons": r["reasons"][:4]}
        all_ok &= caught
    res["all_caught"] = bool(all_ok)
    return res


def print_matrix(res: dict) -> None:
    print("r0_capture controls: properties x defects (MOVED = the property failed)")
    cols = PROPERTIES
    print(f"  {'defect':<13}" + "".join(f"{c[:8]:>9}" for c in cols) + "   outcome")
    for name, row in res["matrix"].items():
        d = res["defects"][name]
        print(f"  {name:<13}" + "".join(f"{('MOVED' if row[c] == 'MOVED' else '.'):>9}"
                                         for c in cols)
              + f"   {d['outcome']} (intended {d['intended']})")
    print(f"  clean: {res['clean']['verdict']} known answers {res['clean']['known_answers']}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a1 = sub.add_parser("analyse")
    a1.add_argument("--bundle", type=pathlib.Path,
                    default=pathlib.Path(os.environ.get("R0_CAPTURE_BUNDLE", DEFAULT_BUNDLE)))
    a1.add_argument("--references", type=pathlib.Path, default=REFERENCES)
    a1.add_argument("--out", type=pathlib.Path, default=None)
    a2 = sub.add_parser("synth")
    a2.add_argument("--out", type=pathlib.Path, required=True)
    a2.add_argument("--defect", choices=sorted(DEFECTS), default=None)
    a2.add_argument("--references", type=pathlib.Path, default=REFERENCES)
    a4 = sub.add_parser("new-session")
    a4.add_argument("--bundle", type=pathlib.Path,
                    default=pathlib.Path(os.environ.get("R0_CAPTURE_BUNDLE", DEFAULT_BUNDLE)))
    a3 = sub.add_parser("controls")
    a3.add_argument("--out", type=pathlib.Path, required=True)
    a3.add_argument("--references", type=pathlib.Path, default=REFERENCES)
    a3.add_argument("--analyser", choices=("real", "stub"), default="real")
    a = ap.parse_args(argv)
    if a.cmd == "new-session":
        try:
            p = new_session(a.bundle)
        except Refused as exc:
            print(f"r0_capture: REFUSED -- {exc}")
            return 2
        print(f"r0_capture: session skeleton at {p}; fill image.programmer, board.revision "
              "and board.power (analyse refuses until they are filled)")
        return 0
    if a.cmd == "synth":
        p = synth_session(a.out, refdir=a.references, defect=a.defect)
        print(f"r0_capture: synthetic session ({a.defect or 'clean'}) at {p}")
        return 0
    if a.cmd == "controls":
        a.out.mkdir(parents=True, exist_ok=True)
        try:
            res = run_controls(a.out / "sessions", refdir=a.references,
                               analyser=stub_analyse if a.analyser == "stub" else analyse)
        except Refused as exc:
            res = {"all_caught": False, "refused": str(exc)}
            print(f"r0_capture controls: REFUSED -- {exc}")
        else:
            print_matrix(res)
        res["analyser"] = a.analyser
        (a.out / "controls.json").write_text(json.dumps(res, indent=1, default=str) + "\n")
        shutil.rmtree(a.out / "sessions", ignore_errors=True)
        verdict = "ALL CAUGHT" if res.get("all_caught") else "NOT ALL CAUGHT"
        print(f"r0_capture controls: {verdict}")
        return 0 if res.get("all_caught") else (2 if "refused" in res else 1)
    rec = analyse(a.bundle, a.references)
    rec["analysed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    out = a.out or a.bundle
    if out.exists() or a.out:
        out.mkdir(parents=True, exist_ok=True)
        (out / "analysis.json").write_text(json.dumps(rec, indent=1, default=str) + "\n")
    for r in rec["reasons"][:12]:
        print(f"  {r}")
    print(f"r0_capture: {rec['verdict']}"
          + (" (NO VERDICT)" if rec["verdict"] == REFUSED else ""))
    return {PASS: 0, FAIL: 1}.get(rec["verdict"], 2)


if __name__ == "__main__":
    sys.exit(main())
