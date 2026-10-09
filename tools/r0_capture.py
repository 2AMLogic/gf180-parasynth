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
import re
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
# an unexpected exception: neither a refusal (a stated missing precondition)
# nor a FAIL (a completed, qualified measurement). Exit 3, outside the 0/1/2
# checker convention, so tools/trial.py reads it as NO VERDICT (crashed).
ERROR = "ERROR"

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
DROPOUT_RUN = 96        # 2 ms of raw near-silence where the prediction sounds
HOLD_UNCERTAINTY_FRAMES = 96   # host-planned vs device-applied hold (34 measured, scripted device)
MIN_SCORED_S = 0.020    # a waveform comparison needs at least this much signal
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


_EXIT = re.compile(r"exit (-?\d+)")
_SHA_LINE = re.compile(r"^([0-9a-f]{64})\s+\S*arty\.bit$")


def command_blocks(text: str) -> tuple:
    """A transcript in the procedure's `cmd >> file 2>&1; echo "exit $?" >>
    file` shape, split into command blocks: (lines, exit_code) for every block
    that `exit N` closes, and the lines left over after the last exit."""
    blocks, cur = [], []
    for ln in (x.strip() for x in text.splitlines()):
        if not ln:
            continue
        m = _EXIT.fullmatch(ln)
        if m:
            blocks.append((cur, int(m.group(1))))
            cur = []
        else:
            cur.append(ln)
    return blocks, cur


def programming_attempts(text: str) -> list:
    """Every programmer block of step 5, in order, with its OWN result: the
    image digest it named, the programmer version line, its exit status. A
    failed attempt stays in this list -- a later success never erases it."""
    blocks, _ = command_blocks(text)
    out = []
    for lines, code in blocks[1:]:
        shas = [_SHA_LINE.match(ln).group(1) for ln in lines if _SHA_LINE.match(ln)]
        ver = [ln for ln in lines if ln.startswith("openFPGALoader v")]
        out.append({"image_sha256": shas[0] if shas else None,
                    "programmer": ver[0] if ver else None, "exit": code,
                    "result": "success" if code == 0 else "FAILED"})
    return out


def transcript_problems(text: str, bitstream_sha256: str, *, synthetic_ok=False) -> list:
    """docs/capture-r0.md step 5, read as COMMAND BLOCKS -- each result bound
    to its own command, never a tail or a count of `exit 0` lines (#299 B1,
    then C1: a failed programmer followed by a stray `exit 0` was accepted).
    Step 5 writes, in order:

      block 1  release_manifest.py           -> `release_manifest: BOUND`, exit 0
      block 2  shasum -a 256 arty.bit          (no exit line of its own)
               openFPGALoader --Version        (no exit line of its own)
               openFPGALoader -b ... arty.bit  -> its own exit status

    Accepted: exactly one manifest block, first, BOUND, exit 0; after it ONLY
    complete programming attempts -- each block carries R0's `arty.bit`
    digest line, THEN the `openFPGALoader v` line, then the programmer's
    output and exit; the LAST attempt exits 0. An earlier failed attempt is
    allowed only because a later COMPLETE attempt for the same image
    succeeded, and it is kept in the record (`programming_attempts`).
    Refused: no attempt; a block that is not a complete attempt (a bare
    `exit 0`, a manifest re-run, an attempt without the image digest or the
    programmer line); lines after the last exit (truncated); SYNTHETIC."""
    probs = []
    if not synthetic_ok and "SYNTHETIC" in text:
        probs.append("program transcript is SYNTHETIC: no board was programmed")
    blocks, tail = command_blocks(text)
    if tail:
        probs.append(f"program transcript: {len(tail)} line(s) after the last exit status "
                     f"(truncated or unfinished command: {tail[-1]!r})")
    if not blocks:
        return probs + ["program transcript: no command block (no `exit N` line)"]
    m_lines, m_exit = blocks[0]
    if not any(ln.startswith("release_manifest: BOUND") for ln in m_lines):
        probs.append("program transcript: the first block is not `release_manifest: BOUND`")
    if m_exit != 0:
        probs.append(f"program transcript: release_manifest exited {m_exit}, not 0")
    if len(blocks) < 2:
        return probs + ["program transcript: no openFPGALoader programming attempt"]
    for i, (lines, code) in enumerate(blocks[1:]):
        shas = [j for j, ln in enumerate(lines) if _SHA_LINE.match(ln)]
        ver = [j for j, ln in enumerate(lines) if ln.startswith("openFPGALoader v")]
        what = f"block {i + 2} (exit {code})"
        if any(ln.startswith("release_manifest:") for ln in lines):
            probs.append(f"program transcript: {what} re-runs the manifest check")
        if not ver:
            probs.append(f"program transcript: {what} is not a programming attempt (no "
                         "`openFPGALoader v` line) -- no other command may follow the "
                         "manifest check")
            continue
        if not shas:
            probs.append(f"program transcript: {what}: the attempt names no image (no "
                         "`shasum -a 256 ... arty.bit` line before the programmer)")
            continue
        dig = _SHA_LINE.match(lines[shas[0]]).group(1)
        if dig != bitstream_sha256:
            probs.append(f"program transcript: {what}: arty.bit hashes {dig[:12]}, not R0's "
                         f"{bitstream_sha256[:12]}")
        if shas[0] > ver[0]:
            probs.append(f"program transcript: {what}: the shasum line follows the programmer")
    if blocks[-1][1] != 0:
        probs.append(f"program transcript: the last programming attempt exited "
                     f"{blocks[-1][1]}, not 0")
    return probs


def detect_problems(text: str, *, synthetic_ok=False) -> list:
    """Step 2.2's detect transcript, as one command block: it names an
    xc7a100t and exits 0, with nothing after its exit line."""
    probs = []
    if not synthetic_ok and "SYNTHETIC" in text:
        probs.append("detect transcript is SYNTHETIC: no board was detected")
    blocks, tail = command_blocks(text)
    if tail or not blocks:
        probs.append("detect transcript: openFPGALoader --detect has no exit status line")
        return probs
    lines, code = blocks[-1]
    if code != 0:
        probs.append(f"detect transcript: openFPGALoader --detect exited {code}, not 0")
    if not lines:
        probs.append("detect transcript: its exit line closes no command output")
    if not any("xc7a100t" in ln.lower() for ln in lines):
        probs.append("detect transcript: no xc7a100t device named")
    return probs


def host_log_problems(plan) -> list:
    """The host log's schema, asserted BEFORE any comparison (#299 C2: a log
    without `apply_frame` was silently skipped by one check and crashed the
    next, which exited 1 -- this tool's FAIL code). Every write needs its
    `expect` register fields and an integer `apply_frame`; every event its
    `expect` and an integer `due`."""
    if not isinstance(plan, dict) or not isinstance(plan.get("rows"), list):
        return ["host log has no `rows` list"]
    probs = []
    for i, r in enumerate(plan["rows"]):
        if not isinstance(r, dict):
            probs.append(f"row {i} is not an object")
            continue
        kind = r.get("kind")
        if kind not in ("write", "event", "status", "abort"):
            probs.append(f"row {i}: kind {kind!r}")
            continue
        if kind in ("write", "event"):
            e = r.get("expect")
            if not isinstance(e, dict) or not all(isinstance(e.get(k), int)
                                                  for k in ("flag", "sec", "addr", "data")):
                probs.append(f"row {i} ({kind}): `expect` missing or malformed")
            need = "apply_frame" if kind == "write" else "due"
            if not isinstance(r.get(need), int):
                probs.append(f"row {i} ({kind}): `{need}` missing or not an integer")
    if not probs:
        probs += event_packet_problems(plan) + hold_timing_problems(plan)
    return probs[:5] + ([f"... {len(probs) - 5} more"] if len(probs) > 5 else [])


_SHA256_HEX = re.compile(r"[0-9a-f]{64}")


def _is_int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def live_identity_problems(rec: dict, *, allow_synthetic: bool = False) -> list:
    """Why a reference record's `schedule.kind == "live"` label is not backed
    by its own replay identity (PR #563). A live render (r0_reference.py
    render --schedule live) hashes the bytes it REPLAYED through the RTL into
    BOTH `replayed_stimulus_sha256` and `schedule.live_cmds_sha256`, and
    those bytes carry the gate bracket's STATUS queries, so they are never
    the pinned dry-run command's (`cmds_sha256`). A label is cheap; these
    three hashes are what a relabelled dry-run render cannot all satisfy.

    Defeating input (rule 8), shipped as a test: a record whose hashes are
    copied from a GENUINE live run onto a dry-run render's audio satisfies
    every check here -- the wav is not re-derived from the bytes. Only a
    re-render closes that; such records are marked `synthetic` (the unit
    fixture is one) and refused unless `allow_synthetic`."""
    sched = rec.get("schedule") or {}
    live = sched.get("live_cmds_sha256")
    replayed = rec.get("replayed_stimulus_sha256")
    probs = []
    if not allow_synthetic and ("synthetic" in sched or "synthetic" in rec):
        probs.append("the live reference is declared synthetic (a test fixture, not a "
                     "rendered live schedule)")
    if not (isinstance(live, str) and _SHA256_HEX.fullmatch(live)) or live == "0" * 64:
        probs.append(f"schedule.live_cmds_sha256 {live!r} is not a sha256 digest")
    elif replayed != live:
        probs.append(f"schedule.live_cmds_sha256 {live[:12]} is not the record's replayed "
                     f"stimulus {str(replayed)[:12]}: the label is not the replay")
    elif live == rec.get("cmds_sha256"):
        probs.append("the replayed stimulus is the pinned dry-run command's bytes, not "
                     "a live schedule")
    return probs


def release_qualification(ref: dict, host_plan: dict | None, *,
                          allow_synthetic: bool = False) -> tuple:
    """(qualified, why) -- may this held take's RELEASE be compared?

    Only against a reference rendered from a LIVE schedule that binds the
    host's own hold record: its identity must carry `schedule.kind == "live"`,
    a live-bytes hash that IS the record's replayed stimulus and is not the
    dry-run's (`live_identity_problems`), and a `hold_timing` naming the same
    requested hold and bound as the host log. Two planned holds that merely
    match are NOT enough (#306): a dry-run reference's hold is the planner's,
    and a matching number in the host log says nothing about what the board
    applied. Nor is the label alone (PR #563): a dry-run reference relabelled
    `live` with an all-zero hash qualified until the hash was bound. No such
    reference exists yet (rendering one is the build box's job), so today
    every held take's release is NOT EVALUATED, and says so."""
    rec = (ref or {}).get("record") or {}
    sched = rec.get("schedule")
    ht = (host_plan or {}).get("hold_timing")
    if not isinstance(sched, dict) or sched.get("kind") != "live":
        return False, ("release NOT EVALUATED: the reference is the dry-run schedule's, "
                       "not a live schedule bound to this take's hold record")
    if not isinstance(ht, dict):
        return False, ("release NOT EVALUATED: the host log carries no hold record "
                       "(a legacy log: its hold is the planner's)")
    ident = live_identity_problems(rec, allow_synthetic=allow_synthetic)
    if ident:
        return False, "release NOT EVALUATED: " + "; ".join(ident)
    bind = sched.get("hold_timing") or {}
    tol = sched.get("release_tolerance_frames")
    if not (_is_int(tol) and _is_int(bind.get("requested_hold_frames"))):
        return False, ("release NOT EVALUATED: the live reference does not bind its "
                       "hold and release tolerance")
    if bind["requested_hold_frames"] != ht.get("requested_hold_frames"):
        return False, (f"release NOT EVALUATED: the live reference holds "
                       f"{bind['requested_hold_frames']} frames, the host log requested "
                       f"{ht.get('requested_hold_frames')}")
    if not _is_int(ht.get("hold_bound_frames")) or ht["hold_bound_frames"] > tol:
        return False, (f"release NOT EVALUATED: the host's hold bound "
                       f"{ht.get('hold_bound_frames')} exceeds the reference's release "
                       f"tolerance {tol}")
    return True, "the live-schedule reference binds this take's hold record"


def check_session(bundle: pathlib.Path, s: dict, manifest: dict, *,
                  synthetic_ok: bool = False) -> list:
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
    elif t:
        probs += transcript_problems((bundle / t).read_text(errors="replace"), want,
                                     synthetic_ok=synthetic_ok)
    if img.get("readback") is True:
        probs.append("session.image.readback is true, but the procedure has no readback step: "
                     "a programming transcript is not a readback")
    dt = s.get("detect_transcript", "detect.txt")
    dp = bundle / dt
    if not dp.is_file():
        probs.append(f"detect transcript {dt} missing (procedure step 2.2)")
    else:
        probs += [p.replace("detect transcript", f"detect transcript {dt}", 1)
                  for p in detect_problems(dp.read_text(errors="replace"),
                                           synthetic_ok=synthetic_ok)]
    if "synthetic" not in s:
        # a declaration copied from the synthetic generator is not an acquisition
        for sec in ("image", "board", "dac", "interface"):
            for k, v in (s.get(sec) or {}).items():
                if isinstance(v, str) and "synthetic" in v.lower():
                    probs.append(f"session.{sec}.{k} is declared synthetic")
        for tk in s.get("takes") or []:
            if not str(tk.get("started") or "").strip() or \
                    "synthetic" in str(tk.get("started")).lower():
                probs.append(f"take {tk.get('id')}: started time missing")
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
    planned it. None for commands that are not live-writes-then-one-event.

    A log with a `hold_timing` record (#306) carries the host's bracketed
    hold: the gate-off due minus the gate's ESTIMATED apply frame, with a
    bound. It is used only when it agrees with the log's own event row
    (`hold_timing_problems` refuses a log where it does not). A legacy log
    has only the planner's prediction: the gate-off's due minus the last
    live write's PLANNED apply frame -- a plan, never an observation (on the
    scripted device the live path's plan was 34 frames off the device)."""
    if not plan:
        return None
    ev = [r for r in plan.get("rows", []) if r.get("kind") == "event"]
    live = [r for r in plan.get("rows", []) if r.get("kind") == "write"]
    if len(ev) != 1 or not live:
        return None
    ht = plan.get("hold_timing")
    if isinstance(ht, dict) and isinstance(ht.get("gate_apply_estimate"), int):
        return int(ev[0]["due"]) - int(ht["gate_apply_estimate"])
    return int(ev[0]["due"]) - int(live[-1]["apply_frame"])


def hold_timing_problems(plan: dict | None) -> list:
    """The held note's timing record, checked against the log it sits in.
    A record whose due is not the event row's due, whose bound exceeds the
    declared maximum, or whose verdict is not WITHIN_BOUND cannot vouch for
    a hold. Absent record: nothing to check (a legacy log)."""
    ht = (plan or {}).get("hold_timing")
    if ht is None:
        return []
    if not isinstance(ht, dict):
        return ["`hold_timing` is not an object"]
    probs = []
    ev = [r for r in plan.get("rows", []) if r.get("kind") == "event"]
    for k in ("requested_hold_frames", "gate_apply_estimate", "gate_off_due",
              "hold_bound_frames", "declared_max_bound_frames"):
        if not isinstance(ht.get(k), int):
            probs.append(f"hold_timing.{k} missing or not an integer")
    b = ht.get("gate_apply_bounds")
    if not (isinstance(b, list) and len(b) == 2 and all(isinstance(x, int) for x in b)):
        probs.append("hold_timing.gate_apply_bounds missing or malformed")
    if probs:
        return probs
    if ht.get("verdict") != "WITHIN_BOUND":
        probs.append(f"hold_timing verdict {ht.get('verdict')!r}: the host did not "
                     f"establish the hold ({ht.get('reason')})")
    if not ev or ht["gate_off_due"] != ev[0].get("due"):
        probs.append(f"hold_timing names gate-off due {ht['gate_off_due']}, the log's "
                     f"event row {ev[0].get('due') if ev else None}")
    if not b[0] <= ht["gate_apply_estimate"] <= b[1]:
        probs.append("hold_timing's estimate lies outside its own bracket")
    if ht["hold_bound_frames"] > ht["declared_max_bound_frames"]:
        probs.append(f"hold_timing bound {ht['hold_bound_frames']} exceeds the declared "
                     f"{ht['declared_max_bound_frames']}")
    if ht["gate_off_due"] - ht["gate_apply_estimate"] != ht["requested_hold_frames"]:
        probs.append("hold_timing's due is not estimate + requested hold")
    return probs


def event_packet_problems(plan: dict | None) -> list:
    """A scheduled event's `due` is a claim; its packet is what went down the
    wire. The packet carries the low 16 bits of the absolute due, the row
    the due rebased by `base_send_frame`. A row whose claim is not its own
    bytes is a deceptive log (#306 HOLD_FORGED_LOG) and is refused."""
    probs = []
    base = int((plan or {}).get("base_send_frame", 0) or 0)
    for i, r in enumerate((plan or {}).get("rows", [])):
        if r.get("kind") != "event" or not isinstance(r.get("due"), int):
            continue
        try:
            pkt = bytes.fromhex(r.get("packet", ""))
        except ValueError:
            probs.append(f"row {i}: packet is not hex")
            continue
        if len(pkt) != 10:
            probs.append(f"row {i}: event packet is {len(pkt)} bytes, not 10")
            continue
        wire = pkt[1] | (pkt[2] << 8)
        if wire != (r["due"] + base) & 0xFFFF:
            probs.append(f"row {i}: the packet carries due {wire}, the row claims "
                         f"{(r['due'] + base) & 0xFFFF}")
    return probs


def command_identity(plan_ref: dict | None, plan_cap: dict) -> list:
    """The host's own log of what it sent vs the reference's pinned command.

    Compared: the LIVE WRITES as one sequence, in order (they apply in arrival
    order, so their order is part of the command), and the SCHEDULED EVENTS
    as a second sequence, in order, with each event's due frame relative to
    the first event's. NOT compared: where the events sit among the writes in
    the byte stream. An event fires at its due frame whatever byte position
    carried it, and the live CLI deliberately sends a held note's gate-off
    AFTER the writes (anchored to the observed gate), where the dry-run plan
    lists it first -- the Judge measured every held-note take refused by the
    row-by-row comparison this replaces (#299 B2). The hold itself, which
    that anchoring changes, is carried separately by `planned_hold`."""
    if plan_ref is None:
        return ["the reference carries no plan to compare the host log against"]

    def split(p):
        writes, events, first_due = [], [], None
        for r in p.get("rows", []):
            e = r.get("expect") or {}
            key = (e.get("flag"), e.get("sec"), e.get("addr"), e.get("data"))
            if r.get("kind") == "write":
                writes.append(key)
            elif r.get("kind") == "event":
                first_due = r.get("due", -1) if first_due is None else first_due
                events.append(key + (r.get("due", -1) - first_due,))
        return writes, events
    (wa, ea), (wb, eb) = split(plan_ref), split(plan_cap)
    out = []
    # the device timeline: every scheduled event must fall AFTER the live
    # setup it depends on has applied (a gate-off before its gate-on is a
    # different command, wherever the rows sit in the log)
    live_apply = [r.get("apply_frame") for r in plan_cap.get("rows", [])
                  if r.get("kind") == "write" and r.get("apply_frame") is not None]
    ev_due = [r.get("due") for r in plan_cap.get("rows", [])
              if r.get("kind") == "event" and r.get("due") is not None]
    if live_apply and ev_due and min(ev_due) <= max(live_apply):
        out.append(f"host log schedules an event at frame {min(ev_due)}, at or before the "
                   f"last live write's frame {max(live_apply)}: the setup/event order differs")
    for what, a, b in (("live write", wa, wb), ("scheduled event", ea, eb)):
        if a == b:
            continue
        for i, (x, y) in enumerate(zip(a, b)):
            if x != y:
                out.append(f"{what} {i} differs from the released command: {y} vs {x}")
                break
        else:
            out.append(f"host log has {len(b)} {what}s, the released command {len(a)}")
    return out


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
                 hold_offset=None, capb_all=None, release=(False, None)) -> dict:
    """Every property of one take. `frozen` = {rho, gain}; d_t is estimated
    here from the declared window only.

    `hold_offset`: frames by which this take's host log planned a different
    hold from the reference's (`planned_hold`). Before #306 the live CLI
    dated the gate from an ACK drain and a STATUS minus its round trip; on
    the scripted device it planned 3121 frames against the dry-run's 1920 --
    1201 frames, 25 ms -- and the device fired 34 frames later still. Since
    #306 a live log carries a bracketed `hold_timing` record and plans the
    requested hold (offset 0). The offset is read from the host log, never
    fitted from audio and never absorbed by a tolerance. When it is not zero the waveform comparisons
    end before the earlier release (less HOLD_UNCERTAINTY_FRAMES), the take
    records `release_compared: false` and the excluded interval, and an
    interval shorter than MIN_SCORED_S is NOT EVALUATED rather than passed.
    The stuck-output check covers the whole take: it shows the note ended,
    not that the release matches.

    `release` = `release_qualification(...)` (#306): a held take (one with a
    `hold_offset`, zero included) has its release compared ONLY when that
    says so. Equal planned holds alone no longer turn `release_compared`
    true."""
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
    e1_full = e1
    held = hold_offset is not None
    release_ok, release_why = release if release else (False, None)
    if held and release_ok:
        M["release_compared"] = True
        M["release_qualified"] = release_why
    if held and not release_ok:
        # the host's own timing record says this take's release is planned
        # `hold_offset` frames away from the reference's; the waveform
        # comparisons end before the EARLIER of the two releases, less the
        # planned-vs-device uncertainty. Nothing is fitted or widened.
        nz = np.flatnonzero(x_raw)
        rel = (int(nz[0]) + int(ref["hold"]) + min(0, int(hold_offset))
               - HOLD_UNCERTAINTY_FRAMES - BLOCK) if nz.size and ref.get("hold") else None
        M["hold_offset_frames"] = int(hold_offset)
        M["hold_offset_ms"] = round(1e3 * hold_offset / sr, 2)
        M["release_compared"] = False
        if rel is not None:
            e1 = min(e1, int(d + rel / rho))
    why = None
    if e1 < e1_full:
        why = ("release not compared: the host planned a different hold from the "
               "reference's" if hold_offset else
               (release_why or "release not compared"))
    M["coverage"] = {"scored_s": [round(e0 / sr, 4), round(e1 / sr, 4)],
                     "excluded_s": ([round(e1 / sr, 4), round(e1_full / sr, 4)]
                                    if e1 < e1_full else None),
                     "excluded_why": why}
    scored = (e1 - e0) >= MIN_SCORED_S * sr
    M["waveform_scored"] = bool(scored)
    if not scored:
        # trimming away the region under test cannot create a PASS: these
        # properties are NOT EVALUATED on this take, and the record says so
        out["not_evaluated"] = {k: f"scored interval {(e1 - e0) / sr * 1e3:.1f} ms < "
                                   f"{MIN_SCORED_S * 1e3:.0f} ms" for k in
                                ("residual", "timing", "gain", "clock", "dropout")}
        e1 = e0
    ev = slice(e0, e1)
    if scored:
        er = float(np.sum((A[ev] - p[ev]) ** 2) / max(np.sum(p[ev] ** 2), 1e-30))
        M["residual_db"] = round(10 * math.log10(max(er, 1e-30)), 2)
        if M["residual_db"] > LIMITS["residual_db_max"]:
            F["residual"] = f"{M['residual_db']} dB > {LIMITS['residual_db_max']} dB"
    # local lags and gains, measured AGAINST the frozen alignment
    pts = (_local_lags(x, A, d, rho, int(cal[1]), int(min(len(x), (e1 - d) * rho)))
           if scored else [])
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
    pb, cb_ = block_rms(p[ev]), block_rms(A[ev])       # (empty when not scored)
    n = min(pb.size, cb_.size)
    pb, cb_ = pb[:n], cb_[:n]
    lim = 10 ** (LIMITS["dropout_ref_dbfs_min"] / 20)
    drop = np.flatnonzero((pb >= lim) & (cb_ < pb * 10 ** (-LIMITS["dropout_drop_db"] / 20)))
    M["dropout_blocks"] = int(drop.size)
    # and on the RAW capture: a run of near-silence where the prediction
    # sounds. The banded check alone missed a 20 ms gap placed on the loudest
    # block of bar808-full: the band filter rings a loud onset into the gap.
    raw_p = block_rms(g * warp_reference(x_raw, A_raw.size, d, rho)[ev])
    if raw_p.size == 0:
        raw_p = np.zeros(1)
    quiet_lin = 10 ** ((max(noise_floor_dbfs, -110.0) + 6.0) / 20)
    qr = np.abs(A_raw[ev]) <= quiet_lin
    dq = np.diff(np.concatenate([[0], qr.astype(np.int8), [0]]))
    runs = [(a0, b0) for a0, b0 in zip(np.flatnonzero(dq == 1), np.flatnonzero(dq == -1))
            if b0 - a0 >= DROPOUT_RUN and raw_p[min(a0 // BLOCK, raw_p.size - 1)] >= lim]
    M["dropout_raw_runs"] = len(runs)
    if runs and not drop.size:
        drop = np.array([runs[0][0] // BLOCK])
    if drop.size:
        F["dropout"] = (f"{drop.size} x 5 ms blocks {LIMITS['dropout_drop_db']:.0f} dB below "
                        f"prediction, first at {(e0 + drop[0] * BLOCK) / sr:.3f} s")
    # stuck output over the WHOLE evaluation span, release included. With a
    # planned hold offset the prediction is the running maximum over that
    # offset (plus its uncertainty), i.e. "silent in the reference at every
    # release time the host log allows" -- it checks the note ENDS; it does
    # not compare release timing or shape.
    evf = slice(e0, e1_full)
    pb, cb_ = block_rms(p[evf]), block_rms(A[evf])
    n = min(pb.size, cb_.size)
    pb, cb_ = pb[:n], cb_[:n]
    if held and not release_ok and n:
        from scipy.ndimage import maximum_filter1d
        w = 2 * int(math.ceil((abs(hold_offset) + HOLD_UNCERTAINTY_FRAMES) / BLOCK)) + 1
        pb = maximum_filter1d(pb, size=w, mode="nearest")
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
            manifest_path=rr.MANIFEST, *, allow_synthetic: bool = False) -> dict:
    """The whole session -> a record with verdict PASS / FAIL / REFUSED.

    `allow_synthetic` is for the synthetic-defect controls and the tests
    only; the CLI and therefore the trial never set it, so a synthetic
    session is REFUSED as a physical capture (#299 B1: `synth` output placed
    where the operator's bundle goes had made T-PHYSICAL PASS, exit 0)."""
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
        if "synthetic" in s and not allow_synthetic:
            raise Refused("the session is synthetic (r0_capture.py synth): it is not a physical "
                          "capture of the board")
        rec["synthetic"] = "synthetic" in s
        probs = check_session(bundle, s, manifest, synthetic_ok=rec["synthetic"])
        if probs:
            raise Refused("capture metadata incomplete: " + "; ".join(probs))
        rec["identity"] = {"programming_attempts": programming_attempts(
                               (bundle / s["image"]["program_transcript"]).read_text(
                                   errors="replace")),
                           "bitstream_sha256": s["image"]["bitstream_sha256"],
                           "readback": bool(s["image"]["readback"]),
                           "programming": ("readback-verified" if s["image"]["readback"] else
                                           "programming transcript only -- NOT a readback")}
        rec["inputs_sha256"][s["image"]["program_transcript"]] = sha256_file(
            bundle / s["image"]["program_transcript"])
        dac = s["interface"]["dac_channels"]
        takes, refs, caps, capb, hold_offsets = s["takes"], {}, {}, {}, {}
        release_q = {}
        seen_logs = {}
        dt = s.get("detect_transcript", "detect.txt")
        rec["inputs_sha256"][dt] = sha256_file(bundle / dt)
        for cmd in sorted({tk["command_id"] for tk in takes} - {"silence"}):
            bad = rr.reference_problems(refdir, cmd, manifest)
            if bad:
                raise Refused("the reference is not the release's: " + "; ".join(bad))
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
                try:
                    host_plan = json.loads(hp.read_text())
                except ValueError as exc:
                    raise Refused(f"take {tk['id']}: host log {hp.name} unreadable ({exc})")
                bad = host_log_problems(host_plan)
                if bad:
                    raise Refused(f"take {tk['id']}: host log {hp.name} is incomplete: "
                                  + "; ".join(bad))
                hh = rec["inputs_sha256"][str(hp.relative_to(bundle))]
                if not rec["synthetic"] and hh in seen_logs:
                    raise Refused(f"take {tk['id']}: host log is byte-identical to take "
                                  f"{seen_logs[hh]}'s -- one run cannot be two takes")
                seen_logs[hh] = tk["id"]
                diff = command_identity(refs[tk["command_id"]].get("plan"), host_plan)
                h_cap, h_ref = planned_hold(host_plan), refs[tk["command_id"]].get("hold")
                if h_cap is not None and h_ref is not None:
                    hold_offsets[tk["id"]] = h_cap - h_ref
                    release_q[tk["id"]] = release_qualification(
                        refs[tk["command_id"]], host_plan, allow_synthetic=allow_synthetic)
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
                                        capb_all=capb[tk["id"]],
                                        release=release_q.get(tk["id"], (False, None))))
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
            if (any(k["id"] in hold_offsets and not release_q[k["id"]][0] for _, k in lst)
                    and refs[cmd].get("hold")):
                # the holds were planned differently: compare up to the release
                b = min(b, int(np.flatnonzero(refs[cmd]["x"])[0]) + int(refs[cmd]["hold"]) - BLOCK)
            (t0, k0) = lst[0]
            u0 = unwarp_capture(capb[k0["id"]][:, dac[0] - 1], len(x), *t0["_aligned"])[a:b]
            for t1, k1 in lst[1:]:
                u1 = unwarp_capture(capb[k1["id"]][:, dac[0] - 1], len(x), *t1["_aligned"])[a:b]
                r = 10 * math.log10(max(float(np.sum((u1 - u0) ** 2)) /
                                        max(float(np.sum(u0 ** 2)), 1e-30), 1e-30))
                t1["metrics"]["repeat_db_vs_" + k0["id"]] = round(r, 2)
                t0["metrics"]["repeat_db_vs_" + k1["id"]] = round(r, 2)
                if r > LIMITS["repeat_db_max"]:
                    t1["fails"]["repeat"] = (f"differs from take {k0['id']} by {r:.1f} dB "
                                             f"(limit {LIMITS['repeat_db_max']} dB)")
        rec["takes"] = [{k: v for k, v in t.items() if not k.startswith("_")} for t in results]
        props = {p: "PASS" for p in PROPERTIES}
        props["channel-identity"] = "UNOBSERVABLE"
        reasons = []
        if clock_fail:
            props["clock"] = "FAIL"
            reasons.append(f"clock: session offset {cal['ppm']:.1f} ppm > "
                           f"{LIMITS['clock_ppm_max']} ppm")
        for t in results:
            for p, why in t["fails"].items():
                props[p] = "FAIL"
                reasons.append(f"{t['id']}: {p}: {why}")
        # each property carries its SCOPE: the takes it was evaluated on and
        # the ones it was not (#299 re-Judge N1, plan090: no unqualified PASS
        # implying every take; no new verdict value either)
        sounding = [t for t in results if "_aligned" in t]
        silent = [t for t in results if t["command_id"] == "silence"]
        structured = {}
        for prop in PROPERTIES:
            if prop == "noise":
                on, off = [t["id"] for t in silent], []
            elif prop == "clipping":
                on, off = [t["id"] for t in results], []
            elif prop == "channel-identity":
                on, off = [], [t["id"] for t in results]
            elif prop in EVALUATED_BY:
                on = [t["id"] for t in sounding if EVALUATED_BY[prop](t["metrics"])
                      and prop not in (t.get("not_evaluated") or {})]
                off = [t["id"] for t in sounding if t["id"] not in on]
            else:
                on, off = [t["id"] for t in sounding], []
            structured[prop] = {"verdict": props[prop], "evaluated_on": on,
                                "not_evaluated_on": off}
        structured["channel-identity"]["why"] = (
            "R0 is dual-mono (rtl-sketch/i2s_tx.v sends one sample on both channels); a "
            "left/right swap cannot be seen in the audio")
        props = structured
        rec["properties"] = props
        rec["coverage"] = {
            "release_compared": {t["id"]: t["metrics"].get("release_compared", True)
                                 for t in results if "_aligned" in t},
            "excluded_intervals_s": {t["id"]: t["metrics"]["coverage"]["excluded_s"]
                                     for t in results
                                     if (t["metrics"].get("coverage") or {}).get("excluded_s")},
            "not_evaluated": {t["id"]: t["not_evaluated"] for t in results
                              if t.get("not_evaluated")},
            "note": ("a take with release_compared false had its waveform compared only "
                     "before the release; its release timing and shape were NOT compared, "
                     "and the stuck-output check shows only that the note ended")}
        rec["reasons"] = reasons
        rec["verdict"] = FAIL if reasons else PASS
        return rec
    except Refused as exc:
        rec["verdict"] = REFUSED
        rec["reasons"] = [str(exc)]
        return rec
    except Exception as exc:                        # noqa: BLE001 -- deliberately total
        # An unexpected exception is an EXECUTION ERROR (plan090): not a
        # measured FAIL (exit 1) and not relabelled as a refusal. Diagnostics
        # are kept; the CLI exits 3, which the trial reads as NO VERDICT.
        import traceback
        rec["verdict"] = ERROR
        rec["reasons"] = [f"execution error (no verdict): {type(exc).__name__}: {exc}"]
        rec["traceback"] = traceback.format_exc().splitlines()[-20:]
        return rec


# the metric each scope-qualified property leaves when a take WAS evaluated for it
EVALUATED_BY = {
    "residual": lambda m: "residual_db" in m,
    "timing": lambda m: "timing_scatter" in m,
    "gain": lambda m: "gain_change_db" in m,
    "clock": lambda m: "clock_drift_ppm" in m,
    "dropout": lambda m: m.get("waveform_scored", False),
    "pitch": lambda m: "pitch" in m,
    "repeat": lambda m: any(k.startswith("repeat_db_vs_") for k in m),
}


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
    (out / "detect.txt").write_text("SYNTHETIC: no board was detected\n"
                                    "index 0: idcode 0x13631093 xc7a100t\nexit 0\n")
    # step 5's format, so the controls exercise the transcript checks too --
    # and marked SYNTHETIC, which the real path refuses
    (out / "program.txt").write_text(
        "SYNTHETIC: no board was programmed\n"
        "release_manifest: BOUND -- synthetic session\nexit 0\n"
        f"{manifest['image']['bitstream_sha256']}  {manifest['image']['bitstream']}\n"
        "openFPGALoader v0.0.0-synthetic\nSYNTHETIC: nothing was loaded\nexit 0\n")
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


def _pv(v):
    """A property's verdict, from the structured form or the stub's string."""
    return v.get("verdict") if isinstance(v, dict) else v


def _run(analyser, bundle, refdir):
    if analyser is analyse:
        return analyse(bundle, refdir, allow_synthetic=True)
    return analyser(bundle, refdir)


def run_controls(out: pathlib.Path, *, refdir=REFERENCES, analyser=analyse,
                 defects=None) -> dict:
    """The clean synthetic session must PASS with its known answers recovered;
    each defect must FAIL with its own property among the failures; the swap
    must be reported, honestly, as unobservable (BLIND)."""
    res = {"clean": None, "defects": {}, "matrix": {}}
    clean = _run(analyser, synth_session(out / "clean", refdir=refdir), refdir)
    # the known answers are REQUIRED: a clean run with no calibration record
    # (the stub's) recovered nothing and is not fine (#299 N1)
    ok_clean = clean["verdict"] == PASS and bool(clean.get("calibration"))
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
        r = _run(analyser, synth_session(out / name, refdir=refdir, defect=name), refdir)
        failed = {p for p, v in (r.get("properties") or {}).items() if _pv(v) == "FAIL"}
        res["matrix"][name] = {p: ("MOVED" if p in failed else "BLIND") for p in PROPERTIES}
        if prop is None:
            caught = r["verdict"] == PASS and _pv(
                (r.get("properties") or {}).get("channel-identity")) == "UNOBSERVABLE"
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
    out = a.out or a.bundle
    dest = out / "analysis.json" if (out.exists() or a.out) else None
    if dest is not None:
        # the current run's record exists from the start: a crash leaves
        # THIS run's in-progress ERROR, never an older run's PASS or FAIL
        try:
            out.mkdir(parents=True, exist_ok=True)
            _write_atomic(dest, {"schema": RECORD_SCHEMA, "verdict": ERROR,
                                 "reasons": ["in progress: the analysis did not finish"],
                                 "started_at": datetime.datetime.now(
                                     datetime.timezone.utc).isoformat()})
        except OSError as exc:
            # nowhere to record a verdict: do not measure one (#340)
            print(f"r0_capture: ERROR (execution error, NO VERDICT) -- cannot write "
                  f"{dest}: {type(exc).__name__}: {exc}")
            return 3
    try:
        rec = analyse(a.bundle, a.references)
    except BaseException as exc:                     # noqa: BLE001 -- see ERROR
        import traceback
        rec = {"schema": RECORD_SCHEMA, "verdict": ERROR,
               "reasons": [f"execution error (no verdict): {type(exc).__name__}: {exc}"],
               "traceback": traceback.format_exc().splitlines()[-20:]}
    rec["analysed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if dest is not None:
        try:
            _write_atomic(dest, rec)
        except OSError as exc:
            # #340: the record on disk is still this run's in-progress ERROR;
            # the exit status must agree with it, not escape as 1 (FAIL)
            print(f"r0_capture: ERROR (execution error, NO VERDICT) -- the "
                  f"{rec['verdict']} record was not written to {dest}: "
                  f"{type(exc).__name__}: {exc}")
            return 3
    for r in rec["reasons"][:12]:
        print(f"  {r}")
    for prop, v in (rec.get("properties") or {}).items():
        if isinstance(v, dict) and v.get("verdict") == PASS and v.get("not_evaluated_on"):
            print(f"  {prop}: PASS on {len(v['evaluated_on'])} take(s); NOT evaluated on "
                  f"{', '.join(v['not_evaluated_on'])}")
    for tid, ok in ((rec.get("coverage") or {}).get("release_compared") or {}).items():
        if ok is False:
            print(f"  {tid}: release NOT compared (host-planned hold differs)")
    print(f"r0_capture: {rec['verdict']}"
          + {REFUSED: " (NO VERDICT)", ERROR: " (execution error, NO VERDICT)"}.get(
              rec["verdict"], ""))
    return {PASS: 0, FAIL: 1, REFUSED: 2}.get(rec["verdict"], 3)


def _write_atomic(path: pathlib.Path, rec: dict) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(rec, indent=1, default=str) + "\n")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)                  # never leave the tmp behind (#340)
        raise


if __name__ == "__main__":
    sys.exit(main())
