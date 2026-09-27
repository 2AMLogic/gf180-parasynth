#!/usr/bin/env python3
"""The 808 kit's perceptual gate (#379): is our sound as close to the target
Fischer take as the real 808's own nearest neighbour setting?

WHY THIS EXISTS. The per-case scorecard's coarse band balance and decay called
the shipped cymbal "close" while the operator clearly heard it as wrong (#369,
#374: H-L 8.64 dB against the 808's 8.16 for a candidate that sounds bad).
This gate compares the WHOLE hit, time-resolved, on perceptual features, and
takes its pass bar from the 808 itself, not from a number we chose.

FROZEN CONVENTIONS -- declared once, identical for every signal, never refitted
per candidate:

  rate        everything is analysed at 48 kHz; the 44.1 kHz Fischer takes are
              resampled up with resample_poly(160, 147).
  AC coupling a causal 2nd-order 20 Hz Butterworth high-pass on every signal
              (the audio path's coupling; the drum block has no DC blocking,
              #152, and DC is not audible).
  alignment   t = 0 is 1 ms before the first sample above 2 % of the signal's
              own peak (run_case's ONSET_FRAC / TRIM_MS). No cross-correlation
              search: a search is a fit.
  level       BS.1770 K-weighted loudness over the 50 ms blocks within 20 dB
              of the loudest one, the rule tools/ab_808_loud.py uses for the
              listening packs, set to the same value for every signal. So the
              gate compares at the level the operator listens at.
  span        from t = 0 to TWICE the last 10 ms frame where the TARGET is
              within 60 dB of its loudest frame (+50 ms), capped at 3.0 s, so a
              candidate that rings on is seen. Both sides are read over it.
  floor       a fixed power floor, 60 dB under the target's loudest
              critical-band cell, is added to both sides before any log or
              loudness, so recording hiss and digital silence compare equal.

FEATURES (each a distance; 0 = identical):

  spec       critical-band (25 Zwicker bands) spectrogram, 10 ms hop, 21 ms
             Hann frames, compared as specific loudness N = P**0.23:
             sum|N_o - N_t| / sum N_t. Loudness-weighted by construction.
  centroid   the Bark centroid of specific loudness (Zwicker's sharpness
             weighting) as a trajectory, RMS difference in Bark, frames
             weighted by max(loudness_t, loudness_o).
  flatness   the spectral-flatness trajectory (100 Hz - 16 kHz), RMS
             difference in dB, weighted the same way.
  spec_peak  the same on 5.3 ms frames every 2.7 ms: the worst frame's
             loudness difference over the loudest target frame's loudness. A summed
             distance averages a click away; this does not.
  impulse    the worst sample's crest over its 5 ms RMS, above 2 kHz, for
             the strike (first 10 ms) and the body; the larger dB difference.
             A click is an outlier sample, which frame spectra average away.
  attack     per band group (6 groups), the times the band's forward-
             cumulative energy over the first 100 ms reaches 5/20/50 %;
             RMS difference in ms; the WORST band group holding >= 10 % of
             either side's loudness.
  decay      per band group, the Schroeder energy-decay curve over the span
             while the target is above -40 dB; RMS dB difference; worst group.
  modulation per band group, the fluctuation spectrum of the log envelope
             (detrended by a 100 ms mean) in six octave bands 10-640 Hz, from
             20 ms after the strike while the target group is within 30 dB
             of its peak (>= 150 ms needed, else not applicable); RMS dB
             difference; worst group. The six squares' metallic beating and
             roughness live here.
  pitch      pitched sounds only (BD, toms, congas, CB, CL): each side's own
             strongest line within x1.5 of the target's (first 150 ms),
             tracked by amplitude-weighted instantaneous frequency every 5 ms
             in a +-25 % band while both sides are within 30 dB of their
             peaks. `pitch` is the median offset in cents (tuning);
             `pitch_shape` the worst 20 ms after removing it (a slide).

THE BAR comes from the 808 (see `bar_for`), and the verdict is per feature:
a sound PASSES only if every applicable feature is within its bar. There is
no combined or averaged score; the rank is by the worst feature's ratio.
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys

import numpy as np
from scipy.io import wavfile
from scipy.signal import butter, hilbert, lfilter, resample_poly, sosfilt, sosfiltfilt, welch

ROOT = pathlib.Path(__file__).resolve().parents[1]

SR = 48000
HP_HZ = 20.0
ONSET_FRAC = 0.02
TRIM_S = 0.001
LEAD_S = 0.020              # silence kept before t = 0 so zero-phase filters meet it in silence
SPAN_DB = 60.0
SPAN_MAX_S = 3.0
FLOOR_DB = 60.0
NFFT = 1024
HOP = 480                   # 10 ms
LOUD_EXP = 0.23             # Zwicker's specific-loudness exponent
BARK_EDGES = (0, 100, 200, 300, 400, 510, 630, 770, 920, 1080, 1270, 1480, 1720, 2000, 2320,
              2700, 3150, 3700, 4400, 5300, 6400, 7700, 9500, 12000, 15500, 22050)
GROUPS = ((20, 150), (150, 500), (500, 1500), (1500, 4000), (4000, 9000), (9000, 20000))
GROUP_MIN_SHARE = 0.10
ATTACK_S = 0.100
ATTACK_FRACS = (0.05, 0.20, 0.50)
ENV_RMS_S = 0.001
#: Hearing's temporal resolution floor on the attack bar: half the ~2 ms
#: broadband gap-detection threshold. Rise times that differ by less are not
#: resolved by the ear, and sub-ms sample jitter failed a half-size resample
#: of RS and CH (#379).
ATTACK_JND_MS = 1.0
DECAY_DB = 40.0
DECAY_CLIP_DB = 50.0
MOD_RATE = 2000
MOD_START_S = 0.020
MOD_DB = 30.0
MOD_MIN_S = 0.150
MOD_TREND_S = 0.100
MOD_EDGES = (10, 20, 40, 80, 160, 320, 640)
PITCH_SOUNDS = ("BD", "LT", "MT", "HT", "LC", "MC", "HC", "CB", "CL")
PITCH_SEARCH_S = 0.150
PITCH_HOP_S = 0.005
IMPULSE_HP = 2000.0
IMPULSE_STRIKE_S = 0.010
PITCH_DB = 30.0
PITCH_WORST_FRAMES = 4      # 20 ms

FEATURES = ("spec", "spec_peak", "centroid", "flatness", "impulse", "attack", "decay", "modulation",
            "pitch", "pitch_shape")
UNITS = {"spec": "loudness L1 fraction", "spec_peak": "worst frame, fraction of loudest frame",
         "centroid": "Bark RMS", "flatness": "dB RMS",
         "attack": "ms RMS of 5/20/50 % cumulative-energy times, first 100 ms (worst band)", "decay": "dB RMS (worst band)",
         "modulation": "dB RMS (worst band)", "impulse": "dB, worst-sample crest >2 kHz (strike, body)",
         "pitch": "cents, median offset", "pitch_shape": "cents, worst 20 ms after the offset"}


#: Rule 5 (docs/verification-rules.md): the apparatus bugs found while this
#: gate was built, reinstated on demand. Each must turn a known answer red
#: (tools/test_perceptual_gate.py::test_injection_*).
INJECTIONS = ("pitch-rms", "centroid-max-weights", "span-target-only")
INJECT: set = set()


class Refused(RuntimeError):
    """A precondition of the apparatus failed; the gate withholds a verdict."""


# ---------------------------------------------------------------------------
# conditioning: rate, AC coupling, alignment, level (frozen, see module doc)
# ---------------------------------------------------------------------------
def load_wav(path) -> tuple:
    sr, raw = wavfile.read(str(path))
    x = np.asarray(raw, dtype=np.float64)
    if x.ndim > 1:
        x = x.mean(axis=1)
    if np.issubdtype(raw.dtype, np.integer):
        x = x / float(np.iinfo(raw.dtype).max + 1)
    return x, int(sr)


def to_rate(x, sr: int) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    if sr == SR:
        return x
    g = math.gcd(SR, sr)
    return resample_poly(x, SR // g, sr // g)


def kweight(x):
    b1, a1 = [1.53512485958697, -2.69169618940638, 1.19839281085285], [1.0, -1.69065929318241, 0.73248077421585]
    b2, a2 = [1.0, -2.0, 1.0], [1.0, -1.99004745483398, 0.99007225036621]
    return lfilter(b2, a2, lfilter(b1, a1, x))


def loudness_db(x) -> float:
    """tools/ab_808_loud.py's rule: K-weighted, 50 ms blocks at 50 % overlap,
    gated to blocks within 20 dB of the loudest."""
    y = kweight(x)
    blk = int(0.05 * SR)
    if len(y) < blk + 1:
        y = np.concatenate([y, np.zeros(blk + 1 - len(y))])
    e = np.array([np.mean(y[i:i + blk] ** 2) for i in range(0, len(y) - blk, blk // 2)])
    g = e >= e.max() * 10 ** (-20 / 10)
    return 10 * math.log10(float(np.mean(e[g])) + 1e-30)


def condition(x, sr: int, *, side: str = "signal") -> np.ndarray:
    """Rate -> AC coupling -> alignment -> level. Returns a 48 kHz signal whose
    sample LEAD_S*SR is t = 0."""
    y = to_rate(x, sr)
    if not np.any(np.abs(y) > 1e-9):
        raise Refused(f"{side} is silent")
    y = sosfilt(butter(2, HP_HZ / (SR / 2), btype="highpass", output="sos"), y)
    pk = float(np.abs(y).max())
    i = int(np.argmax(np.abs(y) > ONSET_FRAC * pk))
    if i == 0:
        raise Refused(f"{side} begins above {ONSET_FRAC:.0%} of its peak: cut into the strike")
    t0 = i - int(round(TRIM_S * SR))
    lead = int(round(LEAD_S * SR))
    y = y[max(t0, 0):]
    y = np.concatenate([np.zeros(lead + max(0, -t0)), y])
    y = y * 10 ** ((-23.0 - loudness_db(y)) / 20)
    return y


# ---------------------------------------------------------------------------
# analysis
# ---------------------------------------------------------------------------
_LEAD = int(round(LEAD_S * SR))


def _hz_to_bark_index(f):
    """Fractional band index: band b spans [b, b+1) on this axis, linear in Hz
    inside each Zwicker band."""
    e = np.asarray(BARK_EDGES, dtype=float)
    return np.interp(f, e, np.arange(len(e)))


def _tri_bank(f):
    """Overlapping triangular critical-band filters (unit sum per bin), centred
    on each band's middle. RECTANGULAR bands were used first: a line crossing a
    band edge jumped a whole band, and a 3 % resample of CB moved the centroid
    further than a 6 % one (wrong-then-right, #379)."""
    nb = len(BARK_EDGES) - 1
    x = _hz_to_bark_index(f) - 0.5
    m = np.zeros((nb, len(f)))
    for b in range(nb):
        m[b] = np.maximum(0.0, 1.0 - np.abs(x - b))
    m[0, x < 0] = 1.0
    m[-1, x > nb - 1] = 1.0
    return m


def _bark_matrix():
    f = np.fft.rfftfreq(NFFT, 1 / SR)
    m = _tri_bank(f)
    centres = np.array([0.5 * (BARK_EDGES[b] + BARK_EDGES[b + 1]) for b in range(len(BARK_EDGES) - 1)])
    return m, f, centres


_BARKM, _FREQS, _BCENT = _bark_matrix()
NFFT_FINE, HOP_FINE = 256, 128           # 5.3 ms frames every 2.7 ms, for spec_peak


def _bark_matrix_fine():
    return _tri_bank(np.fft.rfftfreq(NFFT_FINE, 1 / SR))


_BARKM_FINE = _bark_matrix_fine()
_BARK_OF_BAND = np.arange(len(BARK_EDGES) - 1) + 0.5


def _frames(y, n_frames: int, nfft: int = NFFT, hop: int = HOP) -> np.ndarray:
    """|STFT|^2, frame k centred at t = k * hop."""
    need = _LEAD + n_frames * hop + nfft
    if len(y) < need:
        y = np.concatenate([y, np.zeros(need - len(y))])
    w = np.hanning(nfft)
    starts = _LEAD + np.arange(n_frames) * hop - nfft // 2
    idx = starts[:, None] + np.arange(nfft)[None, :]
    seg = y[np.clip(idx, 0, len(y) - 1)] * (idx >= 0)
    return np.abs(np.fft.rfft(seg * w, axis=1)) ** 2 / np.sum(w ** 2)


def _pad(y, n):
    return y if len(y) >= n else np.concatenate([y, np.zeros(n - len(y))])


def plan_from_target(yt, sound: str) -> dict:
    """Everything the TARGET fixes for the comparison: span, floor, pitch line,
    modulation regions. Computed once per target, never from the candidate."""
    n_max = int(SPAN_MAX_S * SR / HOP)
    n_avail = max(1, (len(yt) - _LEAD) // HOP)
    P = _frames(yt, min(n_max, n_avail + 1))
    fp = P.sum(axis=1)
    live = np.nonzero(fp > fp.max() * 10 ** (-SPAN_DB / 10))[0]
    # twice the target's own -60 dB span (+50 ms): a candidate that rings on
    # after the target has gone must be SEEN (a doubled MA decay was invisible
    # inside the target's span -- wrong-then-right, #379).
    n = int(min(2 * (live[-1] + 1) + 5, n_max))
    if "span-target-only" in INJECT:
        n = int(min(live[-1] + 1, n_max))
    B = (P[:n] @ _BARKM.T)
    plan = {"sound": sound, "n_frames": n, "span_s": n * HOP / SR,
            "floor_cell": float(B.max()) * 10 ** (-FLOOR_DB / 10),
            "floor_bin": float(P[:n].max()) * 10 ** (-FLOOR_DB / 10)}
    nf = n * HOP // HOP_FINE
    plan["n_fine"] = nf
    plan["floor_fine"] = float((_frames(yt, nf, NFFT_FINE, HOP_FINE) @ _BARKM_FINE.T).max()) \
        * 10 ** (-FLOOR_DB / 10)
    # per band group: a WHITE energy floor 60 dB under the target's loudest
    # broadband 10 ms, spread over the group's share of the spectrum -- not
    # relative to the group's own peak, which put an empty group's floor under
    # 16-bit dither and failed the target against itself (MT, HT --
    # wrong-then-right, #379). And the modulation region.
    regions, gfloor = [], []
    end = _LEAD + n * HOP
    bb = float(_moving(yt[_LEAD:end] ** 2, int(0.01 * SR)).max()) * 10 ** (-FLOOR_DB / 10)
    for lo, hi in GROUPS:
        gfloor.append(bb * (min(hi, SR / 2) - lo) / (SR / 2))
        env = _group_env(yt, lo, hi, n)
        sm = _moving(env ** 2, int(0.02 * MOD_RATE)) ** 0.5
        a = int(MOD_START_S * MOD_RATE)
        above = np.nonzero(sm > sm.max() * 10 ** (-MOD_DB / 20))[0]
        b = int(above[-1]) if len(above) else 0
        regions.append((a, b) if (b - a) >= MOD_MIN_S * MOD_RATE else None)
    plan["mod_regions"] = regions
    plan["group_floor"] = gfloor
    if sound in PITCH_SOUNDS:
        plan["f_ref"] = _strongest_line(yt, 30.0, 5000.0)
    return plan


def _moving(x, n):
    n = max(1, int(n))
    c = np.cumsum(np.concatenate([[0.0], x]))
    out = (c[n:] - c[:-n]) / n
    return np.concatenate([np.full(n // 2, out[0]), out, np.full(len(x) - len(out) - n // 2, out[-1])])


def _bandpass(y, lo, hi, order=4):
    hi = min(hi, 0.45 * SR)
    return sosfiltfilt(butter(order, [lo / (SR / 2), hi / (SR / 2)], btype="band", output="sos"), y)


def _group_env(y, lo, hi, n_frames):
    """Hilbert envelope of one band group at MOD_RATE, from t = 0 over the span."""
    end = _LEAD + n_frames * HOP
    z = _bandpass(_pad(y, end + SR // 10), lo, hi)[:end]
    env = np.abs(hilbert(z))[_LEAD:]
    k = SR // MOD_RATE
    m = len(env) // k
    return np.sqrt(np.mean(env[:m * k].reshape(m, k) ** 2, axis=1))


def _strongest_line(y, lo, hi) -> float:
    seg = y[_LEAD:_LEAD + int(PITCH_SEARCH_S * SR)]
    seg = _pad(seg, int(PITCH_SEARCH_S * SR))
    nf = 1 << 18
    S = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), nf))
    f = np.fft.rfftfreq(nf, 1 / SR)
    sel = (f >= lo) & (f <= hi)
    k = int(np.argmax(np.where(sel, S, 0)))
    return float(f[k])


def analyse(y, plan: dict) -> dict:
    n = plan["n_frames"]
    P = _frames(y, n)
    Bp = P @ _BARKM.T
    B = Bp + plan["floor_cell"]
    N = B ** LOUD_EXP
    # loudness ABOVE the floor, exactly zero under it. (P+floor)^0.23 -
    # floor^0.23 was used first: it is linear, not zero, below the floor, so
    # 16-bit requantisation steered the centroid of quiet frames by 13 %
    # (wrong-then-right, #379).
    Nx = np.maximum(Bp ** LOUD_EXP - plan["floor_cell"] ** LOUD_EXP, 0.0)
    loud = Nx.sum(axis=1)
    # the Bark centroid of SPECIFIC LOUDNESS (Zwicker's sharpness weighting),
    # not of power: a power centroid on a low tom is its fundamental and was
    # blind to a 6 dB/oct tilt (wrong-then-right, #379)
    cent = (Nx * _BARK_OF_BAND).sum(axis=1) / (Nx.sum(axis=1) + 1e-30)
    Bf = _frames(y, plan["n_fine"], NFFT_FINE, HOP_FINE) @ _BARKM_FINE.T + plan["floor_fine"]
    sel = (_FREQS >= 100) & (_FREQS <= 16000)
    Pf = P[:, sel] + plan["floor_bin"]
    flat = 10 * np.log10(np.exp(np.mean(np.log(Pf), axis=1)) / np.mean(Pf, axis=1))
    out = {"N": N, "Nx": Nx, "loud": loud, "centroid": cent, "flatness": flat,
           "Nfine": Bf ** LOUD_EXP}
    # per band group
    end = _LEAD + n * HOP
    yp = _pad(y, end + SR // 10)
    shares, att, edc, mods = [], [], [], []
    for gi, (lo, hi) in enumerate(GROUPS):
        z = _bandpass(yp, lo, hi)[:end]
        e = z[_LEAD:] ** 2
        shares.append(float(np.sum(e)))
        # attack: when the band's FORWARD-cumulative energy over the first
        # 100 ms reaches 5, 20 and 50 %, in ms. Integrated, like the decay's
        # Schroeder curve, so envelope ripple cannot move it. Three earlier
        # forms failed the apparatus checks (wrong-then-right x3, #379): a
        # 2 ms RMS envelope in dB rippled with a 50 Hz wave; dB-vs-time on a
        # steep edge swung tens of dB on a 0.1 ms shift; first-crossing times
        # jumped between noise peaks (RS, CP, MA under a 3 % resample).
        head = e[:int(ATTACK_S * SR)]
        cum = np.cumsum(head)
        cum = cum / (cum[-1] + 1e-30)
        att.append(np.array([float(np.searchsorted(cum, q)) / SR * 1e3 for q in ATTACK_FRACS]))
        # decay: Schroeder EDC, floored by the plan's floor spread over the band
        fl = plan["group_floor"][gi]
        ef = e + fl
        c = np.cumsum(ef[::-1])[::-1]
        edc_db = 10 * np.log10(c / c[0])
        edc.append(edc_db[::int(0.002 * SR)])
        # modulation
        reg = plan["mod_regions"][gi]
        if reg is None:
            mods.append(None)
        else:
            genv = _group_env(y, lo, hi, n)
            s = genv[reg[0]:reg[1]]
            ldb = 20 * np.log10(s + math.sqrt(fl) + 1e-30)
            fluct = ldb - _moving(ldb, int(MOD_TREND_S * MOD_RATE))
            fr, pxx = welch(fluct, fs=MOD_RATE, nperseg=min(256, len(fluct)))
            mp = []
            for m in range(len(MOD_EDGES) - 1):
                sel_m = (fr >= MOD_EDGES[m]) & (fr < MOD_EDGES[m + 1])
                mp.append(10 * math.log10(float(np.sum(pxx[sel_m])) + 1e-12))
            mods.append(np.array(mp))
    tot = sum(shares) + 1e-30
    out.update({"share": np.array(shares) / tot, "attack": att, "edc": edc, "mod": mods})
    # band-group loudness share (for choosing which groups count)
    out["gshare"] = _group_loudness_share(Nx)
    out["impulse"] = _impulse(y, plan)
    if "f_ref" in plan:
        out["pitch"] = _pitch_track(y, plan)
    return out


def _impulse(y, plan):
    """Crest of the >2 kHz waveform over its own 5 ms RMS, the worst sample,
    separately for the strike (first 10 ms) and the body (after it), in dB.
    A click is an outlier SAMPLE; 10 ms spectra average it into the noise
    around it (a snare click was missed at 10 ms and at 2.7 ms frames -- #379).
    The floor keeps silence from reading as infinitely impulsive."""
    end = _LEAD + plan["n_frames"] * HOP
    z = sosfiltfilt(butter(4, IMPULSE_HP / (SR / 2), btype="highpass", output="sos"),
                    _pad(y, end + SR // 10))[_LEAD:end]
    fl = plan["group_floor"][-1] + plan["group_floor"][-2]
    rms = np.sqrt(_moving(z * z, int(0.005 * SR)) + fl)
    c = 20 * np.log10(np.abs(z) / rms + 1e-9)
    k = int(IMPULSE_STRIKE_S * SR)
    return np.array([float(c[:k].max()), float(c[k:].max()) if len(c) > k else 0.0])


def _group_loudness_share(N):
    tot = N.sum()
    res = []
    for lo, hi in GROUPS:
        sel = (_BCENT >= lo) & (_BCENT < hi)
        res.append(float(N[:, sel].sum() / tot))
    return np.array(res)


def _pitch_track(y, plan):
    f_ref = plan["f_ref"]
    f0 = _strongest_line(y, f_ref / 1.5, f_ref * 1.5)
    end = _LEAD + plan["n_frames"] * HOP
    z = hilbert(_bandpass(_pad(y, end + SR // 5), f0 / 1.25, f0 * 1.25, order=2))[:end]
    dphi = np.angle(z[1:] * np.conj(z[:-1]))
    w = np.abs(z[1:]) ** 2
    win = int(max(2.0 / f0, 0.004) * SR)
    num = _moving(dphi * w, win)
    den = _moving(w, win) + 1e-30
    f_inst = num / den * SR / (2 * np.pi)
    amp = np.sqrt(_moving(w, win))
    hop = int(PITCH_HOP_S * SR)
    idx = np.arange(_LEAD, end - 1, hop)
    return {"f0": f0, "f": f_inst[idx], "amp": amp[idx]}


# ---------------------------------------------------------------------------
# distances
# ---------------------------------------------------------------------------
def distances(at: dict, ao: dict, plan: dict) -> dict:
    d = {}
    d["spec"] = float(np.abs(ao["N"] - at["N"]).sum() / at["N"].sum())
    # frames where BOTH sides sound (geometric-mean weights): a silent frame
    # has no centroid, and max() weights read 0 Bark there and turned the
    # centroid into a second decay detector (OH, wrong-then-right, #379).
    # Whether energy is present at all is `spec` and `decay`'s job.
    w = (np.maximum(at["loud"], ao["loud"]) if "centroid-max-weights" in INJECT
         else np.sqrt(at["loud"] * ao["loud"]))
    w = w / (w.sum() + 1e-30)
    d["centroid"] = float(math.sqrt(np.sum(w * (ao["centroid"] - at["centroid"]) ** 2)))
    d["flatness"] = float(math.sqrt(np.sum(w * (ao["flatness"] - at["flatness"]) ** 2)))
    use = [g for g in range(len(GROUPS))
           if max(at["gshare"][g], ao["gshare"][g]) >= GROUP_MIN_SHARE]
    d["impulse"] = float(np.max(np.abs(ao["impulse"] - at["impulse"])))
    d["attack"] = max(float(np.sqrt(np.mean((ao["attack"][g] - at["attack"][g]) ** 2))) for g in use)
    fd = np.abs(ao["Nfine"] - at["Nfine"]).sum(axis=1)
    d["spec_peak"] = float(fd.max() / at["Nfine"].sum(axis=1).max())
    dec = []
    for g in use:
        et, eo = at["edc"][g], ao["edc"][g]
        live = et > -DECAY_DB
        dec.append(float(np.sqrt(np.mean((np.maximum(eo[live], -DECAY_CLIP_DB)
                                          - np.maximum(et[live], -DECAY_CLIP_DB)) ** 2))))
    d["decay"] = max(dec)
    mods = [float(np.sqrt(np.mean((ao["mod"][g] - at["mod"][g]) ** 2)))
            for g in use if at["mod"][g] is not None]
    d["modulation"] = max(mods) if mods else None
    if "pitch" in at:
        pt, po = at["pitch"], ao["pitch"]
        # frames where BOTH sides' line is within 30 dB of its own peak: a side
        # that has died has no pitch to read (BD5025 read 1,827 cents from the
        # IF of silence -- wrong-then-right, #379). Energy the candidate lacks
        # is the decay feature's job, not this one's.
        live = ((pt["amp"] > pt["amp"].max() * 10 ** (-PITCH_DB / 20))
                & (po["amp"] > po["amp"].max() * 10 ** (-PITCH_DB / 20)))
        if live.sum() < 3:
            raise Refused("fewer than 3 frames where both sides carry the pitched line")
        # Two numbers, because a TUNING step and a spurious slide are
        # different defects: `pitch` is the median offset (the tuning), and
        # `pitch_shape` the WORST 20 ms of the trajectory after that offset is
        # removed. One RMS over the hit averaged a 30 ms slide away, and a
        # worst-20 ms of the raw difference could not tell a slide from a
        # tuning step one knob away (wrong-then-right twice, #379).
        c = 1200 * np.log2(np.maximum(po["f"], 1.0) / np.maximum(pt["f"], 1.0))
        off = float(np.median(c[live]))
        k = PITCH_WORST_FRAMES
        best = [abs(float(np.mean(c[i:i + k])) - off) for i in range(0, len(c) - k + 1)
                if live[i:i + k].all()]
        if not best:
            best = [abs(float(v) - off) for v in c[live]]
        d["pitch"] = abs(off)
        d["pitch_shape"] = max(best)
        if "pitch-rms" in INJECT:
            wa = pt["amp"][live]
            d["pitch"] = d["pitch_shape"] = float(math.sqrt(np.sum(wa * c[live] ** 2) / np.sum(wa)))
    else:
        d["pitch"] = None
        d["pitch_shape"] = None
    return d


class Target:
    """A conditioned target take with its plan and analysis, for repeated use."""

    def __init__(self, x, sr, sound, label=""):
        self.label = label
        self.sound = sound
        self.y = condition(x, sr, side=label or "target")
        self.plan = plan_from_target(self.y, sound)
        self.a = analyse(self.y, self.plan)

    def distance(self, x, sr, label="candidate") -> dict:
        y = condition(x, sr, side=label)
        return distances(self.a, analyse(y, self.plan), self.plan)


# ---------------------------------------------------------------------------
# the corpus: targets and their real-808 neighbours
# ---------------------------------------------------------------------------
CODES = ("00", "25", "50", "75", "10")         # 0.0, 2.5, 5.0, 7.5, 10.0 in that order
TWO_KNOB = {"BD": "bd8/BD", "SD": "sd8/SD", "CY": "cy8/CY"}
ONE_KNOB = {"LT": "lt8/LT", "MT": "mt8/MT", "HT": "ht8/HT", "LC": "lc8/LC",
            "MC": "mc8/MC", "HC": "hc8/HC", "OH": "oh8/OH"}
SINGLE = {"CB": "cb8/CB.WAV", "CH": "ch8/CH.WAV", "CL": "cl8/CL.WAV",
          "CP": "cp8/CP.WAV", "MA": "ma8/MA.WAV", "RS": "rs8/RS.WAV"}


def target_rel(sound: str) -> str:
    sys.path.insert(0, str(ROOT / "model"))
    import drum_verify as dv
    return dv.REF_MAIN[sound][0]


def neighbours(sound: str, rel: str | None = None) -> list:
    """The adjacent knob settings of a take (one step on one knob)."""
    rel = rel or target_rel(sound)
    if sound in SINGLE:
        return []
    stem = pathlib.Path(rel).stem
    pre = TWO_KNOB.get(sound) or ONE_KNOB[sound]
    codes = [stem[2:4], stem[4:6]] if sound in TWO_KNOB else [stem[2:4]]
    out = []
    for k in range(len(codes)):
        i = CODES.index(codes[k])
        for j in (i - 1, i + 1):
            if 0 <= j < len(CODES):
                c = list(codes)
                c[k] = CODES[j]
                out.append(f"{pre}{''.join(c)}.WAV")
    return out


def resampled(x, sr, r: float) -> tuple:
    """The same take played r times faster: every frequency x r, every time / r.
    Done by relabelling the rate, so to_rate() does the only resampling."""
    return x, int(round(sr * r))


def survey(refs: pathlib.Path, sounds, rs=(1.02, 1.05, 1.10)) -> dict:
    res = {}
    for s in sounds:
        rel = target_rel(s)
        T = Target(*load_wav(refs / rel), s, rel)
        row = {"target": rel, "span_s": round(T.plan["span_s"], 3),
               "f_ref": T.plan.get("f_ref"), "neighbours": {}, "resampled": {}}
        for nb in neighbours(s, rel):
            if (refs / nb).exists():
                row["neighbours"][nb] = T.distance(*load_wav(refs / nb), nb)
        x, sr = load_wav(refs / rel)
        for r in rs:
            row["resampled"][str(r)] = T.distance(*resampled(x, sr, r), f"x{r}")
        res[s] = row
        print(s, json.dumps(row, default=lambda v: round(v, 4) if isinstance(v, float) else v), flush=True)
    return res


# ---------------------------------------------------------------------------
# THE BAR. Frozen before our sounds were measured (the survey is 808-only).
# ---------------------------------------------------------------------------
#: Where a sound has only one Fischer take (CB, CH, CL, CP, MA, RS) there is
#: no neighbour, and the bar is the WEAKER calibration: the same take played
#: WEAK_R faster. WEAK_R is the 808's own median TUNING step, measured on the
#: six tom/conga sweeps by this tool's pitch feature (`survey`: 12 adjacent
#: pairs, 95.8-133.2 cents, median 105.4 cents), and it sits inside the
#: +-10 % f0 component tolerance of docs/tr808-reference.md 1.7. It is a
#: resampling of one take, not a second machine: it moves every frequency and
#: every time together, so it is labelled WEAK wherever it is used.
WEAK_CENTS = 105.4
WEAK_R = 2 ** (WEAK_CENTS / 1200)


def bar_for(sound: str, refs: pathlib.Path, T: "Target" = None) -> dict:
    """The per-feature pass bar for one sound.

    The attack bar is floored at ATTACK_JND_MS. Every bar is that distance PLUS the apparatus's own floor: the target
    against itself through `candidate_path` (48 kHz, another lead and gain,
    16-bit). Without it the nearest neighbour sits exactly on its own bar and
    a resample's rounding fails it (wrong-then-right, #379).

    Every 808 take that forms a bar is measured THROUGH `candidate_path`,
    exactly as a candidate is, so the bar and the candidate carry the same
    apparatus.

    Multi-take sounds: the NEAREST real-808 neighbour -- the adjacent knob
    setting (one step on one knob) with the smallest `spec` distance to the
    target -- and that neighbour's distance on every feature. So "as close as
    the nearest other real 808 setting", feature by feature, and the nearest
    is chosen once, by the whole-hit spectrogram, not per feature.

    Single-take sounds: the WEAK calibration (WEAK_R, above)."""
    rel = target_rel(sound)
    T = T or Target(*load_wav(refs / rel), sound, rel)
    nbs = {}
    for nb in neighbours(sound, rel):
        if (refs / nb).exists():
            nbs[nb] = T.distance(*candidate_path(*load_wav(refs / nb)), nb)
    x, sr = load_wav(refs / rel)
    floor = T.distance(*candidate_path(x, sr), "apparatus floor")
    if nbs:
        best = min(nbs, key=lambda k: nbs[k]["spec"])
        base, kind, frm = nbs[best], "808-neighbour", best
    else:
        base = T.distance(*candidate_path(*resampled(x, sr, WEAK_R)), "weak")
        kind = "WEAK-resampled-take"
        frm = f"{rel} played x{WEAK_R:.4f} ({WEAK_CENTS} cents, the 808's median TUNING step)"
    bar = {f: (None if base[f] is None else base[f] + (floor[f] or 0.0)) for f in base}
    if bar.get("attack") is not None:
        bar["attack"] = max(bar["attack"], ATTACK_JND_MS)
    return {"sound": sound, "target": rel, "kind": kind, "from": frm, "bar": bar,
            "base": base, "apparatus_floor": floor, "neighbours": nbs}


def verdict(d: dict, bar: dict) -> dict:
    """PASS only if every applicable feature is within its bar. No combined
    score: `worst` is the largest ratio, reported, never summed."""
    per, worst, wf = {}, 0.0, None
    for f in FEATURES:
        b, v = bar.get(f), d.get(f)
        if b is None:
            per[f] = {"status": "n/a"}
            continue
        if v is None:
            per[f] = {"status": "REFUSED", "bar": b}
            continue
        r = v / b if b > 0 else (math.inf if v > 0 else 0.0)
        per[f] = {"status": "pass" if v <= b else "FAIL", "d": v, "bar": b, "ratio": r}
        if r > worst:
            worst, wf = r, f
    refused = [f for f in per if per[f]["status"] == "REFUSED"]
    failing = [f for f in per if per[f]["status"] == "FAIL"]
    overall = "REFUSED" if refused else ("FAIL" if failing else "PASS")
    return {"verdict": overall, "failing": failing, "worst_ratio": worst, "worst_feature": wf,
            "features": per}


# ---------------------------------------------------------------------------
# seeded defects, applied to the 808 target itself. Magnitudes frozen here,
# before any was run, each an audibly wrong sound:
# ---------------------------------------------------------------------------
TILT_DB_OCT = 6.0           # the LEVEL stage's own slope, the thing #374 got wrong
DECAY_K = 2.0               # decay time halved / doubled
CLICK_FRAC = 0.5            # one sample at half the peak, where the hit is 20 dB down
PITCH_SEMIS = 2.0           # two semitones, about two TUNING steps
SLIDE_SEMIS = 2.0           # a spurious +2 semitone pitch envelope ...
SLIDE_TAU_S = 0.030         # ... relaxing with a 30 ms time constant
DEFECTS = ("darker", "brighter", "decay_short", "decay_long", "missing_band", "click",
           "wrong_pitch", "slide")
INTENDED = {"darker": ("centroid",), "brighter": ("centroid",), "decay_short": ("decay",),
            "decay_long": ("decay",), "missing_band": ("spec",), "click": ("impulse",),
            "wrong_pitch": ("pitch",), "slide": ("pitch_shape",)}
INTENDED_UNPITCHED = {"wrong_pitch": ("spec", "centroid")}


def _onset(x):
    pk = float(np.abs(x).max())
    return int(np.argmax(np.abs(x) > ONSET_FRAC * pk))


def _broadband_tau(x, sr):
    i = _onset(x)
    e = x[i:] ** 2
    c = np.cumsum(e[::-1])[::-1]
    edc = 10 * np.log10(c / c[0] + 1e-30)
    t5 = np.argmax(edc <= -5.0) / sr
    t25 = np.argmax(edc <= -25.0) / sr
    return 8.686 * (t25 - t5) / 20.0


def seed(x, sr, defect: str, sound: str, T: "Target"):
    x = np.asarray(x, dtype=np.float64).copy()
    if defect in ("darker", "brighter"):
        s = -TILT_DB_OCT if defect == "darker" else TILT_DB_OCT
        X = np.fft.rfft(x)
        f = np.maximum(np.fft.rfftfreq(len(x), 1 / sr), 20.0)
        return np.fft.irfft(X * 10 ** (s * np.log2(f / 1000.0) / 20), len(x)), sr
    if defect in ("decay_short", "decay_long"):
        tau = _broadband_tau(x, sr)
        k = 1 / DECAY_K if defect == "decay_short" else DECAY_K
        i = _onset(x)
        t = np.maximum(np.arange(len(x)) - i, 0) / sr
        g = np.exp(-t * (1 / (k * tau) - 1 / tau))
        return x * np.minimum(g, 100.0), sr
    if defect == "missing_band":
        # the octave around the target's long-term spectral peak (the Bark
        # band's CENTRE was used first, which for LT is 50 Hz and missed its
        # 89 Hz line -- wrong-then-right, #379)
        X = np.fft.rfft(x)
        f = np.fft.rfftfreq(len(x), 1 / sr)
        fc = float(f[1 + int(np.argmax(np.abs(X[1:])))])
        X[(f >= fc / math.sqrt(2)) & (f <= fc * math.sqrt(2))] = 0
        return np.fft.irfft(X, len(x)), sr
    if defect == "click":
        pk = float(np.abs(x).max())
        env = np.sqrt(_moving_n(x ** 2, int(0.01 * sr)))
        ip = int(np.argmax(env))
        k = ip + int(np.argmax(env[ip:] <= env[ip] * 0.1))
        x[k] += CLICK_FRAC * pk
        return x, sr
    if defect == "wrong_pitch":
        return resampled(x, sr, 2 ** (PITCH_SEMIS / 12))
    if defect == "slide":
        up = 4
        xu = resample_poly(x, up, 1)
        i = _onset(x)
        t = np.maximum(np.arange(len(x)) - i, 0) / sr
        rate = 2 ** ((SLIDE_SEMIS / 12) * np.exp(-t / SLIDE_TAU_S))
        rate[:i] = 1.0
        src = np.concatenate([[0.0], np.cumsum(rate[:-1])])
        src = np.minimum(src, len(x) - 1)
        return np.interp(src * up, np.arange(len(xu)), xu), sr
    raise ValueError(defect)


def _moving_n(x, n):
    return _moving(x, n)


def candidate_path(x, sr):
    """The 808 take made to look like one of OUR renders: 48 kHz, a different
    lead, a different gain, 16-bit. Nothing a listener could hear changes, so a
    take must read ~0 against itself through this path."""
    y = to_rate(x, sr)
    y = np.concatenate([np.zeros(int(0.0073 * SR)), y]) * 0.25
    return np.round(y * 32767) / 32767, SR


# ---------------------------------------------------------------------------
# our side
# ---------------------------------------------------------------------------
SOUNDS16 = ("BD", "SD", "LT", "LC", "MT", "MC", "HT", "HC", "RS", "CL", "CP", "MA", "CB", "CY", "OH", "CH")


def render_ours(sound: str) -> tuple:
    sys.path[:0] = [str(ROOT / "model"), str(ROOT / "tools")]
    import run_case as rc
    return rc.render_drum_solo(sound)


def stub_candidate(sound: str) -> tuple:
    """START RED: a stub with the right shape and no 808 in it -- a 1 kHz sine
    struck and decaying with a 100 ms time constant, for every sound."""
    t = np.arange(int(1.5 * SR)) / SR
    y = np.concatenate([np.zeros(480), np.sin(2 * np.pi * 1000 * t) * np.exp(-t / 0.1)])
    return y, SR


def _r(v):
    if isinstance(v, float):
        return round(v, 4) if math.isfinite(v) else str(v)
    if isinstance(v, dict):
        return {k: _r(w) for k, w in v.items()}
    if isinstance(v, (list, tuple)):
        return [_r(w) for w in v]
    return v


def prove(refs: pathlib.Path, fixtures: pathlib.Path | None) -> dict:
    """Every check the gate must pass before it is used. Order: start red,
    stays green, must go red (operator-heard bad cymbals), seeded defects."""
    out = {"bars": {}, "start_red": {}, "stays_green": {}, "known_bad": {}, "seeded": {}}
    for s in SOUNDS16:
        rel = target_rel(s)
        x, sr = load_wav(refs / rel)
        T = Target(x, sr, s, rel)
        b = bar_for(s, refs, T)
        out["bars"][s] = {k: b[k] for k in ("target", "kind", "from", "bar", "base", "apparatus_floor")}
        # start red
        out["start_red"][s] = verdict(T.distance(*stub_candidate(s), "stub"), b["bar"])["verdict"]
        try:
            T.distance(np.zeros(SR), SR, "silence")
            out["start_red"][s + "-silence"] = "NOT REFUSED"
        except Refused:
            out["start_red"][s + "-silence"] = "REFUSED"
        # stays green
        g = {"self_through_candidate_path": verdict(T.distance(*candidate_path(x, sr), "self"), b["bar"])}
        for nb, d in b["neighbours"].items():
            nx, nsr = load_wav(refs / nb)
            g[nb] = verdict(T.distance(*candidate_path(nx, nsr), nb), b["bar"])
        if not b["neighbours"]:
            g["half-WEAK_R resample"] = verdict(T.distance(*candidate_path(*resampled(x, sr, WEAK_R ** 0.5)),
                                                           "half"), b["bar"])
        out["stays_green"][s] = g
        # seeded defects
        row = {}
        for dname in DEFECTS:
            if dname == "slide" and s not in PITCH_SOUNDS:
                continue
            dx_, dsr = seed(x, sr, dname, s, T)
            v = verdict(T.distance(*candidate_path(dx_, dsr), dname), b["bar"])
            want = INTENDED_UNPITCHED.get(dname, INTENDED[dname]) if s not in PITCH_SOUNDS else INTENDED[dname]
            hit = [f for f in want if f in v["failing"]]
            v["intended"] = list(want)
            v["outcome"] = ("CAUGHT" if hit else ("CAUGHT-OTHER" if v["verdict"] == "FAIL" else
                                                  ("NO-VERDICT" if v["verdict"] == "REFUSED" else "MISSED")))
            row[dname] = v
        out["seeded"][s] = row
        print(s, "bar", b["kind"], b["from"], "| red:", out["start_red"][s],
              "| green:", {k: v["verdict"] for k, v in g.items()},
              "| seeds:", {k: v["outcome"] for k, v in row.items()}, flush=True)
    if fixtures:
        rel = target_rel("CY")
        T = Target(*load_wav(refs / rel), "CY", rel)
        b = out["bars"]["CY"]["bar"]
        for name in ("CY5025-shipped.wav", "CY5025-candidate.wav"):
            p = fixtures / name
            if p.exists():
                out["known_bad"][name] = verdict(T.distance(*load_wav(p), name), b)
                print("known-bad", name, out["known_bad"][name]["verdict"],
                      out["known_bad"][name]["failing"], flush=True)
            else:
                out["known_bad"][name] = {"verdict": "REFUSED", "why": f"fixture missing: {p}"}
    return out


def rank(refs: pathlib.Path, wavdir: pathlib.Path | None) -> dict:
    rows = {}
    for s in SOUNDS16:
        rel = target_rel(s)
        T = Target(*load_wav(refs / rel), s, rel)
        b = bar_for(s, refs, T)
        y, sr = render_ours(s)
        if wavdir:
            wavdir.mkdir(parents=True, exist_ok=True)
            wavfile.write(wavdir / f"{s}.wav", sr, np.asarray(y, dtype=np.float32))
        try:
            v = verdict(T.distance(y, sr, f"ours {s}"), b["bar"])
        except Refused as e:
            v = {"verdict": "REFUSED", "why": str(e), "worst_ratio": math.inf, "failing": []}
        v.update({"target": rel, "bar_kind": b["kind"], "bar_from": b["from"]})
        rows[s] = v
        print(s, v["verdict"], round(v["worst_ratio"], 2), v.get("worst_feature"), v["failing"], flush=True)
    order = sorted(rows, key=lambda k: -rows[k]["worst_ratio"])
    return {"order": order, "rows": rows}


def provenance(a) -> dict:
    import hashlib
    import subprocess
    run = lambda *c: subprocess.run(c, cwd=ROOT, capture_output=True, text=True).stdout.strip()
    fx = {}
    if getattr(a, "fixtures", None) and a.fixtures.exists():
        fx = {q.name: hashlib.sha256(q.read_bytes()).hexdigest()[:16] for q in sorted(a.fixtures.glob("*.wav"))}
    return {"commit": run("git", "rev-parse", "HEAD"),
            "sources_dirty": bool(run("git", "status", "--porcelain", "--", "tools", "model")),
            "refs": str(a.refs), "fixtures_sha256_16": fx,
            "features": list(FEATURES), "units": UNITS, "weak_r": WEAK_R,
            "conventions": {"rate": SR, "hp_hz": HP_HZ, "onset_frac": ONSET_FRAC, "trim_s": TRIM_S,
                            "loudness": "BS.1770 K, 50 ms blocks, 20 dB relative gate, -23",
                            "span": f"2 x target -{SPAN_DB:.0f} dB + 50 ms, cap {SPAN_MAX_S} s",
                            "floor_db": FLOOR_DB}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sv = sub.add_parser("survey", help="808-vs-808 distances: neighbours and resampled takes")
    sv.add_argument("--refs", type=pathlib.Path, required=True)
    sv.add_argument("--sounds", nargs="*", default=None)
    sv.add_argument("--out", type=pathlib.Path, required=True)
    pv = sub.add_parser("prove", help="start red, stays green, known-bad, seeded defects")
    pv.add_argument("--refs", type=pathlib.Path, required=True)
    pv.add_argument("--fixtures", type=pathlib.Path, default=None,
                    help="dir with CY5025-shipped.wav and CY5025-candidate.wav (#374)")
    pv.add_argument("--out", type=pathlib.Path, required=True)
    rk = sub.add_parser("rank", help="the 16 shipped sounds against their 808 bars")
    rk.add_argument("--refs", type=pathlib.Path, required=True)
    rk.add_argument("--wavs", type=pathlib.Path, default=None)
    rk.add_argument("--out", type=pathlib.Path, required=True)
    pr = sub.add_parser("pair", help="distances of files from one target")
    pr.add_argument("sound")
    pr.add_argument("target")
    pr.add_argument("others", nargs="+")
    a = ap.parse_args(argv)
    if a.cmd == "survey":
        sounds = a.sounds or list(TWO_KNOB) + list(ONE_KNOB) + list(SINGLE)
        res = survey(a.refs, sounds)
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(res, indent=1, default=float) + "\n")
        return 0
    if a.cmd in ("prove", "rank"):
        res = prove(a.refs, a.fixtures) if a.cmd == "prove" else rank(a.refs, a.wavs)
        res["provenance"] = provenance(a)
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(_r(res), indent=1) + "\n")
        return 0
    T = Target(*load_wav(a.target), a.sound, a.target)
    for o in a.others:
        print(o, json.dumps({k: (None if v is None else round(v, 4))
                             for k, v in T.distance(*load_wav(o), o).items()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
