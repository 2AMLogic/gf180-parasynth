"""Pitch-trajectory estimator for a decaying low tone (the 808 BD), #557.

WHY A SEPARATE ESTIMATOR.  `tools/perceptual_gate.py::_pitch_track` smooths
over `max(2/f0, 4 ms)` = 40 ms at 50 Hz, which is the same length as the 58->50
Hz glide this issue is about; and its only proof is "a 100-cent offset reads
~100 cents".  Nothing showed it can SEE a 50 ms glide, or that it does not
invent one.  This module is the instrument for that question, qualified by
`tools/test_pitch_trajectory.py` against signals whose frequency law is fixed
by a closed-form phase (not by this code).

WHAT IT IS.  Band-pass (zero phase) around the strongest line near a reference
frequency, analytic signal, instantaneous frequency from the phase increment,
averaged with amplitude-squared weights over WIN_CYCLES periods, sampled every
HOP_S.  A frame is LIVE while the band's amplitude is within LIVE_DB of its
peak; only live frames carry a frequency.

WHAT IT REFUSES (Refused, never a number):
  - non-finite samples, silence, or fewer than MIN_LIVE_S of live signal;
  - no onset (signal already loud at sample 0 -- cut into the strike);
  - a metric whose window holds fewer than MIN_FRAMES live frames.  A hit that
    has died before the late window has NO late pitch; reporting 0 cents there
    would be a short-decay refusal rendered as a perfect score;
  - a glide where the closed-form known answer is wrong by more than
    GLIDE_FLOOR_CENTS (`_qualify_glide`, #602): the late window must be full and
    no weaker than GLIDE_LATE_MIN_DB below the peak, and the strongest line must
    lie within GLIDE_F0_CENTS of f_ref.  MIN_FRAMES alone answered tens of cents
    wrong just above its threshold.

THE THREE QUANTITIES, kept apart (issue acceptance bullet 2):
  offset_cents(a, b)   median tuning difference over co-live frames
  shape_cents(a, b)    worst 20 ms of the difference AFTER the offset is removed
  glide_cents(t)       sustained glide: early window vs late window of ONE
                       trajectory, both starting after ATTACK_SKIP_S so the
                       4 ms attack retune (model/drums_fx.py:bd_attack_writes,
                       130 Hz / Q 6) is not read as a glide.

KNOWN LIMITS, measured by the qualification (wrong-then-right, see the test
file): the estimator is edge-limited for the first ~20 ms (a 50 Hz tone reads
+40 cents at t = 0, +14 at 20 ms: step-onset ringing of the band-pass), and it
cannot resolve a 4 ms attack retune at all (window ~15 ms).  An attack artefact
is therefore NOT separable here -- `model/bd_excitation_probe.py` owns it; this
module only guarantees it does not leak into the sustained glide.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.signal import butter, hilbert, sosfiltfilt

ESTIMATOR_ID = "pitch_trajectory/v1 bandpass-zero-phase+hilbert-IF, amp^2 weighted"

HOP_S = 0.005
WIN_CYCLES = 0.75           # IF averaging window in periods of the reference
MIN_WIN_S = 0.004
BAND = 1.35                 # pass band: f_ref/BAND .. f_ref*BAND
SEARCH = 1.5                # strongest line within f_ref/SEARCH .. f_ref*SEARCH
LIVE_DB = 30.0
MIN_LIVE_S = 0.060
ONSET_FRAC = 0.02
ATTACK_SKIP_S = 0.010
EARLY_WINDOW_S = (0.020, 0.050)   # estimator is edge-limited before ~20 ms (qualified)
LATE_WINDOW_S = (0.080, 0.130)
MIN_FRAMES = 3
# glide_cents qualification (#602).  A constant-pitch decaying sinusoid has a
# TRUE glide of 0, so the reading of one is its own error; the sweep is
# tools/bd_glide_phase_sweep.py and tools/test_pitch_trajectory.py.  The floor is
# the documented steady-tone worst error (docs/bd-pitch-baseline-request.md).
# The two thresholds were selected on the 48 kHz sweep only (knobs 0..10 step
# 0.1, f0 40..65 step 0.5).  WRONG-THEN-RIGHT, twice: the late threshold was
# first -16.5 dB; the held-out 44.1 kHz grid read -13.94 c (knob 2.5, 50 Hz) for
# a true 0, so it became -16.0; the full `range` sweep then found knob 2.6 /
# 50.5 Hz / 44.1 kHz at -13.96 c (its window bottoms at -15.88 dB), so it became
# -15.7.  The declared knob 2.5 / 50 Hz / 48 kHz bottoms at -15.56 dB, 0.14 dB
# inside it: that condition is AT the edge, not comfortably within.  After each
# move the grid that triggered it stopped being untouched; HELDOUT3 in the
# test file was generated after the last move.
GLIDE_FLOOR_CENTS = 13.9
GLIDE_LATE_MIN_DB = -15.7   # weakest late-window frame vs the peak: a decay this short reads biased by the filter edge
GLIDE_F0_CENTS = (-140.0, 40.0)   # strongest line vs f_ref: the fixed pass band skews the IF off-centre
WORST_FRAMES = 4            # 20 ms at HOP_S
ANALYSIS_S = 0.300
TAIL_S = 0.100              # signal filtered beyond the analysed span


def TAIL_S_N(sr):
    return int(TAIL_S * sr)


class Refused(RuntimeError):
    """A precondition failed.  This is a first-class outcome, not an error code
    to be mapped to zero."""


@dataclass
class Trajectory:
    t: np.ndarray           # s after onset, frame centres
    f: np.ndarray           # Hz (meaningful where live)
    amp: np.ndarray
    live: np.ndarray        # bool
    f0: float               # strongest line found near f_ref
    sr: int
    onset_index: int
    f_ref: float = 0.0      # the reference the band was centred on (0 = unknown)


def _check(x, sr):
    x = np.asarray(x, dtype=np.float64)
    if x.ndim != 1:
        raise Refused(f"expected a mono signal, got shape {x.shape}")
    if not np.all(np.isfinite(x)):
        raise Refused(f"{int((~np.isfinite(x)).sum())} non-finite sample(s)")
    if sr < 4000:
        raise Refused(f"sample rate {sr} too low")
    if not np.any(np.abs(x) > 1e-9):
        raise Refused("signal is silent")
    return x


def find_onset(x) -> int:
    pk = float(np.abs(x).max())
    i = int(np.argmax(np.abs(x) > ONSET_FRAC * pk))
    if i == 0:
        raise Refused(f"signal begins above {ONSET_FRAC:.0%} of its peak: cut into the strike")
    return i


def _strongest_line(seg, sr, lo, hi):
    n = 1 << int(math.ceil(math.log2(max(len(seg), 1) * 8)))
    sp = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), n))
    fr = np.fft.rfftfreq(n, 1 / sr)
    m = (fr >= lo) & (fr <= hi)
    if not m.any() or sp[m].max() <= 0:
        raise Refused(f"no spectral line in {lo:.1f}..{hi:.1f} Hz")
    return float(fr[m][np.argmax(sp[m])])


def _moving(x, n):
    n = max(int(n), 1)
    c = np.cumsum(np.concatenate([[0.0], x]))
    h = n // 2
    i = np.arange(len(x))
    lo, hi = np.clip(i - h, 0, len(x)), np.clip(i - h + n, 0, len(x))
    return (c[hi] - c[lo]) / np.maximum(hi - lo, 1)


def trajectory(x, sr: int, f_ref: float, *, onset_index: int | None = None) -> Trajectory:
    """Pitch trajectory of the line near `f_ref` Hz, in time from the onset."""
    x = _check(x, sr)
    if not (math.isfinite(f_ref) and 10.0 < f_ref < sr / 8):
        raise Refused(f"reference frequency {f_ref} outside 10 Hz .. sr/8")
    i0 = find_onset(x) if onset_index is None else int(onset_index)
    n = int(ANALYSIS_S * sr)
    pad = int(0.1 * sr)
    seg = x[i0: i0 + n]
    if len(seg) < int(0.05 * sr):
        raise Refused("fewer than 50 ms of signal after the onset")
    f0 = _strongest_line(seg[: int(0.15 * sr)], sr, f_ref / SEARCH, f_ref * SEARCH)
    # filter a TAIL beyond the analysed span too: zero-phase filtering of a
    # truncated record rings at the cut and read +80 cents on the last frame.
    ext = np.concatenate([np.zeros(pad), x[i0: i0 + n + TAIL_S_N(sr)], np.zeros(pad)])
    sos = butter(2, [f_ref / BAND / (sr / 2), min(f_ref * BAND / (sr / 2), 0.99)],
                 btype="bandpass", output="sos")
    z = hilbert(sosfiltfilt(sos, ext))
    dphi = np.angle(z[1:] * np.conj(z[:-1]))
    w = np.abs(z[1:]) ** 2
    win = int(max(WIN_CYCLES / f_ref, MIN_WIN_S) * sr)
    fi = _moving(dphi * w, win) / (_moving(w, win) + 1e-300) * sr / (2 * math.pi)
    amp = np.sqrt(_moving(w, win))
    hop = max(int(round(HOP_S * sr)), 1)
    idx = np.arange(pad, pad + len(seg) - 1, hop)
    a = amp[idx]
    if not np.all(np.isfinite(a)) or a.max() <= 0:
        raise Refused("band holds no signal")
    live = a > a.max() * 10 ** (-LIVE_DB / 20)
    if live.sum() * HOP_S < MIN_LIVE_S:
        raise Refused(f"only {live.sum() * HOP_S * 1e3:.0f} ms live (< {MIN_LIVE_S * 1e3:.0f} ms)")
    return Trajectory((idx - pad) / sr, fi[idx], a, live, f0, sr, i0, float(f_ref))


def _window_mean_cents(tr: Trajectory, w, ref_hz: float, what: str) -> float:
    m = tr.live & (tr.t >= w[0]) & (tr.t < w[1])
    if m.sum() < MIN_FRAMES:
        raise Refused(f"{what}: {int(m.sum())} live frame(s) in {w[0]*1e3:.0f}-{w[1]*1e3:.0f} ms "
                      f"(< {MIN_FRAMES}); the hit has no pitch there")
    return 1200 * math.log2(float(np.mean(tr.f[m])) / ref_hz)


def _qualify_glide(tr: Trajectory) -> None:
    """Refuse a glide whose closed-form known answer is off by more than
    GLIDE_FLOOR_CENTS (#602).  Two causes, one check each; both are written so
    that a NaN fails them."""
    if not (math.isfinite(tr.f_ref) and tr.f_ref > 0 and math.isfinite(tr.f0) and tr.f0 > 0):
        raise Refused("glide: trajectory carries no usable f_ref / line frequency")
    c = 1200 * math.log2(tr.f0 / tr.f_ref)
    if not (GLIDE_F0_CENTS[0] <= c <= GLIDE_F0_CENTS[1]):
        raise Refused(f"glide: strongest line {tr.f0:.2f} Hz is {c:+.0f} cents from f_ref {tr.f_ref:.1f} Hz "
                      f"(outside {GLIDE_F0_CENTS[0]:+.0f}..{GLIDE_F0_CENTS[1]:+.0f}); "
                      f"the fixed band reads a zero glide more than {GLIDE_FLOOR_CENTS} cents off there")
    m = (tr.t >= LATE_WINDOW_S[0]) & (tr.t < LATE_WINDOW_S[1])
    need = int(round((LATE_WINDOW_S[1] - LATE_WINDOW_S[0]) / HOP_S))
    if int(m.sum()) < need:
        raise Refused(f"glide: late window holds {int(m.sum())} of {need} frames")
    db = 20 * math.log10(max(float(np.min(tr.amp[m])), 1e-300) / float(np.max(tr.amp)))
    if not (db >= GLIDE_LATE_MIN_DB):
        raise Refused(f"glide: late window falls to {db:.1f} dB of the peak (< {GLIDE_LATE_MIN_DB} dB); "
                      f"the ring is too short for a zero glide to read within {GLIDE_FLOOR_CENTS} cents")


def glide_cents(tr: Trajectory) -> float:
    """Early-window pitch minus late-window pitch of ONE trajectory, in cents
    (positive = starts high, glides down).  Both windows begin after
    ATTACK_SKIP_S, so a sub-10 ms attack retune cannot be read as a glide."""
    assert EARLY_WINDOW_S[0] >= ATTACK_SKIP_S
    late = np.mean(tr.f[tr.live & (tr.t >= LATE_WINDOW_S[0]) & (tr.t < LATE_WINDOW_S[1])]) \
        if (tr.live & (tr.t >= LATE_WINDOW_S[0]) & (tr.t < LATE_WINDOW_S[1])).sum() >= MIN_FRAMES else None
    if late is None:
        raise Refused("late window has too few live frames; the hit has no pitch there")
    _qualify_glide(tr)
    return _window_mean_cents(tr, EARLY_WINDOW_S, float(late), "early window")


def _paired(a: Trajectory, b: Trajectory):
    n = min(len(a.t), len(b.t))
    live = a.live[:n] & b.live[:n]
    if live.sum() < MIN_FRAMES:
        raise Refused(f"{int(live.sum())} co-live frame(s) (< {MIN_FRAMES}): nothing to compare")
    c = 1200 * np.log2(np.maximum(b.f[:n], 1.0) / np.maximum(a.f[:n], 1.0))
    return c, live


def offset_cents(a: Trajectory, b: Trajectory) -> float:
    """Median tuning offset b - a over co-live frames (signed cents)."""
    c, live = _paired(a, b)
    return float(np.median(c[live]))


def shape_cents(a: Trajectory, b: Trajectory) -> float:
    """Worst 20 ms of (b - a) after the median offset is removed (cents)."""
    c, live = _paired(a, b)
    off = float(np.median(c[live]))
    k = WORST_FRAMES
    best = [abs(float(np.mean(c[i:i + k])) - off) for i in range(len(c) - k + 1) if live[i:i + k].all()]
    if not best:
        raise Refused(f"no run of {k} consecutive co-live frames for the worst-20 ms statistic")
    return max(best)
