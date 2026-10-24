#!/usr/bin/env python3
"""Can our digital 808 be told apart from a real one?

This module is the machinery; `model/discrimination_run.py` is the script that
drives it end to end and `docs/discrimination.md` is the written result. The
pytest functions at the bottom check the machinery itself, not the synth.

A discrimination test is easy to run in a way that proves nothing. Almost all
of the code here is about not doing that. The five things it is guarding
against, in the order they would have bitten us:

1. LEAKING THE KNOB SETTING. Fischer trimmed every file to its own decay, so
   `bd8/` runs from 0.25 s at DECAY 0.0 to 3.00 s at DECAY 10.0. File length
   alone identifies both the knob and the provenance. Every clip on both sides
   is therefore onset-aligned and cut to exactly WINDOW_S; nothing longer is
   ever looked at, and nothing shorter exists.

2. LEAKING THE RECORDING CHAIN. The real files carry a 1994 Macintosh
   Quadra 660AV's converter: about 1 LSB of DC and a floor near -76 dBFS.
   Our renders are digitally silent between hits. A classifier will happily
   score 100 % on that and learn nothing about synthesis. Both sides get a
   20 Hz high-pass and a floor clamp FLOOR_DB below the clip's own peak.
   `--no-floor-clamp` runs the ablation, and it is reported.

3. TWO REPRESENTATIONS, NOT ONE. The drum model was fitted against centroid
   and decay (docs/drum-verification.md). Measuring those on the settings the
   fit never saw is a legitimate generalisation test, and they are the
   quantities a circuit designer can act on, so they are kept:
   `interpretable_features()` reports f0, tau, band shares and centroid at the
   held-out settings. But they can only find what they measure, so the
   classifier runs on a general representation as well -- log-mel band
   energies and MFCCs over four time segments -- and the report says which
   groups carry the discrimination, per voice. Both, not one instead of the
   other.

4. FITTING AND TESTING ON THE SAME KNOB POSITIONS. The control law that turns
   a knob position into register values is fitted on FIT_KNOBS = {0.0, 5.0,
   10.0} and then frozen. Everything reported as a result is measured at
   TEST_KNOBS = {2.5, 7.5}, which the law has never seen. A setting with two
   knobs is a test setting if EITHER knob is held out, so the 16 held-out
   bass-drum settings also test the law's separability assumption.

5. CLAIMING MORE THAN THE POWER SUPPORTS. Every accuracy is reported with a
   Clopper-Pearson interval and with the one-sided upper bound that actually
   decides an equivalence claim. 10 of 20 correct is not "indistinguishable":
   its one-sided 95 % upper bound is 0.68.

TWO DIFFERENT EXPERIMENTS, WHICH MUST NOT BE CONFLATED (see EXP_* below):

    EXP_EMULATION      the model is given knob positions and note events only,
                       renders blind, and is compared. A pass here is evidence
                       about instrument emulation.
    EXP_SOUND_MATCHING the model was fitted to reproduce these very
                       recordings. A pass here is evidence about sound
                       matching and nothing more.

The knob law is fitted at FIT_KNOBS, so results at FIT_KNOBS are
EXP_SOUND_MATCHING and results at TEST_KNOBS are EXP_EMULATION. Every table
in the report is tagged. A sound-matching number is never presented as an
emulation number.

THE FLOOR, AND WHY THIS CORPUS CANNOT GIVE IT

The number that makes an "ours vs real" accuracy interpretable is the
"real vs real" accuracy: two genuine takes of the same voice at the same
knobs are not identical, because the 808's six hat oscillators free-run, its
noise generator is an avalanche diode and its components drift. If a
discriminator separates real from real at 80 %, then separating ours from
real at 85 % means very little.

That number cannot be computed from any freely licensed 808 material we could
obtain. Fischer states he recorded many hits of each sound and kept the one
he judged most representative, so the set has exactly ONE take per knob
setting; the 808 directories redistributed in tidalcycles/Dirt-Samples are
the same files. What this module computes instead is `separation_curve()`:
the discriminator's accuracy on real-versus-real pairs whose knob differs by
Δ = 2.5, 5.0, 7.5 and 10.0 of 10. That curve is an UPPER bound on the floor
(Δ = 0 is off its left edge, and every pair on it differs by a real knob
move), and it converts each voice's raw accuracy into the statistic that is
actually interpretable: the KNOB-EQUIVALENT SEPARATION -- how far apart the
real machine's own knob would have to be to look as different from itself as
our render looks from it. That is the finding. The raw accuracy is not.

SPLITTING IS BY RECORDING. The group key is the source recording, so a clip
and every processed version of it stay on the same side of every split.
`assert_split_disjoint()` enforces it rather than trusting it.

REFERENCE SET, CC0-1.0, not committed (12 MB of audio, and *.wav is ignored):

    git clone --depth 1 https://github.com/tidalcycles/sounds-tr808-fischer

Michael Fischer / Technopolis 1994, relicensed CC0-1.0 by TidalCycles. A real
TR-808, SERIAL NO. 103852, individual voice outputs, 16-bit/44.1 kHz, every
knob position in the filename on an 0..10 scale sampled at 00/25/50/75/10,
TONE or TUNING before DECAY or SNAPPY.
"""
from __future__ import annotations

import hashlib
import hashlib
import math
import os
import sys
import warnings
from dataclasses import dataclass, field

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "audition"))

from scipy.io import wavfile
from scipy.signal import butter, sosfilt, sosfiltfilt
from scipy.stats import beta as beta_dist

warnings.filterwarnings("ignore", message=".*EOF.*")

# ---------------------------------------------------------------------------
# The split, pre-registered. Nothing below chooses these; they are stated once
# here and every experiment refers to them.
# ---------------------------------------------------------------------------
KNOB_CODES = {"00": 0.0, "25": 2.5, "50": 5.0, "75": 7.5, "10": 10.0}
FIT_KNOBS = (0.0, 5.0, 10.0)        # the control law sees only these
TEST_KNOBS = (2.5, 7.5)             # every reported emulation result is here

EXP_EMULATION = "emulation"          # knobs in, render blind, compare
EXP_SOUND_MATCHING = "sound-matching"  # fitted to reproduce these recordings

# The practical-equivalence margin, chosen BEFORE any data was collected. A
# discriminator held below this is called practically indistinguishable; the
# claim is made only when the one-sided 95 % upper bound is below it, never
# from a point estimate.
EQUIV_MARGIN = 0.60

# Fallback regularisation for tasks too small to cross-validate C on. Reported
# as `C_searched=False` wherever it is used, so a result can never quietly
# rest on an unsearched hyper-parameter.
DEFAULT_C = 0.1

# Conditioning. WINDOW_S is under the shortest file in the corpus (0.25 s) and
# is the same for every voice, so no length or decay cue survives.
WINDOW_S = 0.24
HPF_HZ = 20.0                       # kills the refs' ~1 LSB of DC
FLOOR_DB = -60.0                    # log-mel clamp, relative to the clip peak
N_MELS, FMIN, FMAX = 40, 30.0, 18000.0   # in Hz: rate-independent, nothing resampled
N_MFCC = 20
WIN_S, HOP_S = 0.0464, 0.0058       # ~46 ms window, ~5.8 ms hop -> 41 frames
N_SEG = 4                           # attack / early / mid / tail

# Our eight stops and the reference directory each is compared against. The
# reference set also holds mt, lc, mc, hc, cy, rs, cl and ma, which we do not
# implement; those are used only as controls.
VOICE_REF = {
    "BD": ("bd8", "BD", 2),         # (directory, prefix, number of knobs)
    "SD": ("sd8", "SD", 2),
    "LT": ("lt8", "LT", 1),
    "HT": ("ht8", "HT", 1),
    "CH": ("ch8", "CH", 0),
    "OH": ("oh8", "OH", 1),
    "CP": ("cp8", "CP", 0),
    "CB": ("cb8", "CB", 0),
}
KNOB_NAMES = {"BD": ("TONE", "DECAY"), "SD": ("TONE", "SNAPPY"), "LT": ("TUNING",),
              "HT": ("TUNING",), "OH": ("DECAY",), "CH": (), "CP": (), "CB": (),
              "MT": ("TUNING",), "LC": ("TUNING",), "MC": ("TUNING",), "HC": ("TUNING",),
              "CY": ("TONE", "DECAY"), "RS": (), "CL": (), "MA": ()}

# Revision 8 shipped eight stops. The kit is now ELEVEN circuits carrying
# SIXTEEN sounds (drums_fx.SOUND_NAMES), and the Fischer set has a directory
# for every one of them -- so the eight that used to be usable only as
# cross-voice controls are now subjects. The table below is the same shape as
# VOICE_REF and the two are merged into ALL_REF, which every measurement that
# needs a knob count reads.
#
# KNOB COUNTS ARE MEASURED, NOT ASSUMED. `tools/probe_new_voice_knobs.py`
# reads them off the corpus: TUNING moves f0 monotonically on all six
# tom/conga sounds and leaves tau flat (LT 80->100, MT 123->153, HT 170->213,
# LC 183->223, MC 260->320, HC 377->467 Hz); the cymbal's SECOND filename code
# is DECAY (tau 158 -> 510 ms, and the recordist's own file lengths grow
# 1.5 -> 4.0 s with it) and its FIRST is TONE, which moves the 5-13 kHz /
# 2-5 kHz balance and NOT a decay.
EXTRA_REF = {
    "MT": ("mt8", "MT", 1), "LC": ("lc8", "LC", 1), "MC": ("mc8", "MC", 1),
    "HC": ("hc8", "HC", 1), "CY": ("cy8", "CY", 2), "RS": ("rs8", "RS", 0),
    "CL": ("cl8", "CL", 0), "MA": ("ma8", "MA", 0),
}
ALL_REF = {**VOICE_REF, **EXTRA_REF}
# Six of the sixteen have no knob at all, so the corpus holds one recording of
# each and there is nothing to hold out: CH, CP, CB (the old study's three)
# plus RS, CL and MA. A sound with no knob cannot produce a knob-equivalent --
# the unit is defined as a distance ON its own knob's yardstick -- and the
# only honest output for those six is a refusal.
NO_KNOB = tuple(v for v, (_, _, nk) in ALL_REF.items() if nk == 0)
# The fmax each TUNING law's f0 search is allowed, one clear octave above the
# top of that sound's measured range.
TUNING_FMAX = {"LT": 200.0, "HT": 400.0, "MT": 400.0,
               "LC": 500.0, "MC": 700.0, "HC": 900.0}

RENDER_S = 0.40                     # enough for onset search + WINDOW_S
RENDER_GAIN = 0.45                  # the reference drum-bus gain (DR 0005)


# ---------------------------------------------------------------------------
# Clips
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Clip:
    """One recording. `rec` is the group key: a clip and every processed
    version of it share it, so they cannot land on opposite sides of a split."""
    voice: str
    knobs: tuple           # () | (k,) | (k1, k2), on the 0..10 dial
    side: str              # "real" | an arm name
    path: str = ""
    rec: str = ""

    def __post_init__(self):
        if not self.rec:
            object.__setattr__(self, "rec", f"{self.side}:{self.voice}:{self.knobs}")

    @property
    def is_test(self) -> bool:
        """A setting is held out if ANY of its knobs is. With no knob at all
        there is nothing to hold out, so the clip can only ever be a fit clip
        -- which is why CH, CP and CB get no emulation verdict."""
        return any(k in TEST_KNOBS for k in self.knobs)

    @property
    def experiment(self) -> str:
        return EXP_EMULATION if self.is_test else EXP_SOUND_MATCHING


def knob_str(knobs) -> str:
    return "/".join(f"{k:.1f}" for k in knobs) if knobs else "-"


def ref_clips(refdir: str, voices=None, include_unmodelled: bool = False) -> list:
    """Index the Fischer set. Filenames carry the knobs; nothing is guessed.

    `include_unmodelled` is the historical name of the flag that adds the
    other eight directories. They are no longer unmodelled -- the kit plays
    all sixteen -- so the flag now means "all sixteen sounds", and the name is
    kept so that the documented eight-voice invocation still reproduces."""
    out = []
    table = dict(ALL_REF) if include_unmodelled else dict(VOICE_REF)
    for voice, (d, prefix, nk) in table.items():
        if voices and voice not in voices:
            continue
        if nk == 0:
            settings = [()]
        elif nk == 1:
            settings = [(v,) for v in sorted(KNOB_CODES.values())]
        else:
            settings = [(a, b) for a in sorted(KNOB_CODES.values())
                        for b in sorted(KNOB_CODES.values())]
        for knobs in settings:
            code = "".join(c for k in knobs for c, v in KNOB_CODES.items() if v == k)
            p = os.path.join(refdir, d, f"{prefix}{code}.WAV")
            if os.path.exists(p):
                out.append(Clip(voice, knobs, "real", p))
    return out


def read_wav(path: str) -> tuple:
    sr, x = wavfile.read(path)
    x = x.astype(np.float64)
    if x.ndim > 1:
        x = x.mean(axis=1)
    return x / 32768.0, int(sr)


# ---------------------------------------------------------------------------
# Conditioning: identical on both sides, and the only place a cue can hide
# ---------------------------------------------------------------------------
def onset(x: np.ndarray, frac: float = 0.02) -> int:
    pk = float(np.abs(x).max())
    if pk <= 0:
        return 0
    return int(np.argmax(np.abs(x) > frac * pk))


def preparation_state(x: np.ndarray, sr: int) -> dict:
    """What two sides of a paired comparison have to agree about before a
    shared function can be called fair (#163).

    A comparison is fair when both sides are in the same STATE when the shared
    function runs, not when they call the same function. The state is
    established upstream -- `_render_raw` for ours, `read_wav` for the
    machine's -- so the call site, which reads `f(ours)` and `f(theirs)`,
    cannot see it. This makes it visible and cheap to assert."""
    x = np.asarray(x, dtype=float)
    if len(x) == 0:
        return dict(n=0, sr=int(sr), lead_ms=0.0, first=0.0, peak=0.0, dur_ms=0.0)
    return dict(n=int(len(x)), sr=int(sr), lead_ms=1000.0 * onset(x) / sr,
                first=float(x[0]), peak=float(np.abs(x).max()),
                dur_ms=1000.0 * len(x) / sr)


def assert_same_preparation(a: dict, b: dict, lead_tol_ms: float = 0.1) -> bool:
    """Raise, naming every field that differs. `lead_ms` is the one #101,
    #132 and #160's F2 all turned on."""
    bad = []
    if abs(a["lead_ms"] - b["lead_ms"]) > lead_tol_ms:
        bad.append(f"lead_ms {a['lead_ms']:.3f} vs {b['lead_ms']:.3f}")
    if a["sr"] != b["sr"]:
        bad.append(f"sr {a['sr']} vs {b['sr']}")
    if abs(a["dur_ms"] - b["dur_ms"]) > 1.0:
        bad.append(f"dur_ms {a['dur_ms']:.1f} vs {b['dur_ms']:.1f}")
    if bad:
        raise AssertionError("inputs are not in the same preparation state: "
                             + "; ".join(bad))
    return True


def _highpass_sos(sr: int):
    return butter(1, HPF_HZ / (sr / 2.0), btype="highpass", output="sos")


def condition(x: np.ndarray, sr: int, level_match: bool = True,
              legacy: bool = False) -> np.ndarray:
    """Onset-align, cut to WINDOW_S, remove DC, high-pass, optionally peak
    normalise. Applied identically to a real clip and to one of ours; nothing
    is resampled (the features are rate-independent).

    ORDER, AND WHY IT IS NOT THE ORDER THIS FUNCTION USED TO USE (#161).

    It used to high-pass the WHOLE signal first, with `sosfiltfilt`, and cut
    afterwards. The docstring's reason was sound -- a filter transient must
    not land on the attack, which is where the discrimination lives -- but the
    filter it reached for is the one that cannot honour it. `sosfiltfilt` pads
    6 samples; the 20 Hz pole is 0.99715 at 44.1 k and 0.99739 at 48 k, about
    2,400 samples to settle. Six samples against that is not a boundary
    condition, it is an initial condition chosen at random, and on a unit
    impulse at index 0 it answers with a full-scale NEGATIVE pedestal: second
    sample -0.994, first 30 ms integrating to -342 (-373 at 48 k) against a
    causal filter's +0.02. That pedestal sat on window 0 of every one of the
    320 feature columns.

    Filtering the whole signal first had a second cost, which is the one #163
    is about: it let everything AHEAD of the onset into the answer. Our render
    reaches here pre-trimmed by `_render_raw` and the machine's does not
    (#160's F2), so the two sides were filtered from different initial
    conditions while the call site read `condition(ours)` and
    `condition(theirs)` and looked obviously fair.

    Cutting at the onset FIRST and filtering CAUSALLY from rest fixes both.
    The window depends on nothing outside itself, so the answer is invariant
    to the lead -- `test_a_measurement_does_not_depend_on_where_the_record_
    begins` asserts it to 1e-9 for 1, 10 and 50 ms of silence and for a
    converter's DC-plus-hiss lead -- and the causal filter's initial condition
    is rest, which is a stated one rather than a reflected guess. Subtracting
    the window's own mean first (the refs carry about 1 LSB of converter DC,
    our renders carry none) makes the DC removal exact instead of exponential,
    so the first-order high-pass is again belt and braces below FMIN.

    `legacy=True` restores the shipped acausal path EXACTLY, so #148's
    published numbers stay re-derivable and the delta can be measured rather
    than asserted (the same reason `_zspace` keeps its unfrozen ruler). It is
    not for use in a result; `model/condition_boundary.py` is its only caller
    outside the tests."""
    x = np.asarray(x, dtype=float)
    n = int(round(WINDOW_S * sr))
    if legacy:
        y = x - x.mean()
        y = sosfiltfilt(_highpass_sos(sr), y)
        i = onset(y)
        seg = y[i:i + n]
    else:
        i = onset(x)
        seg = x[i:i + n]
    if len(seg) < n:                      # never expected; pad rather than lie about length
        seg = np.concatenate([seg, np.zeros(n - len(seg))])
    if not legacy:
        seg = seg - seg.mean()
        seg = sosfilt(_highpass_sos(sr), seg)
    if level_match:
        pk = float(np.abs(seg).max())
        if pk > 0:
            seg = seg / pk
    return seg


# ---------------------------------------------------------------------------
# Features: log-mel and MFCC over four time segments. Deliberately NOT
# centroid and NOT decay -- those are what the model was fitted against.
# The mel bank is defined in Hz, so a 44.1 kHz reference and a 48 kHz render
# land in the same bands with no resampling and no resampler fingerprint.
# ---------------------------------------------------------------------------
_MELBANK: dict = {}


def _hz_to_mel(f):
    return 2595.0 * np.log10(1.0 + np.asarray(f, dtype=float) / 700.0)


def _mel_to_hz(m):
    return 700.0 * (10.0 ** (np.asarray(m, dtype=float) / 2595.0) - 1.0)


def mel_bank(sr: int, n_fft: int) -> np.ndarray:
    key = (sr, n_fft)
    if key in _MELBANK:
        return _MELBANK[key]
    edges = _mel_to_hz(np.linspace(_hz_to_mel(FMIN), _hz_to_mel(FMAX), N_MELS + 2))
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    bank = np.zeros((N_MELS, len(freqs)))
    for m in range(N_MELS):
        lo, mid, hi = edges[m], edges[m + 1], edges[m + 2]
        left = (freqs - lo) / max(mid - lo, 1e-9)
        right = (hi - freqs) / max(hi - mid, 1e-9)
        bank[m] = np.maximum(0.0, np.minimum(left, right))
        w = bank[m].sum()
        if w > 0:
            bank[m] /= w                  # area-normalised: rate-independent
    _MELBANK[key] = bank
    return bank


def logmel(x: np.ndarray, sr: int, floor_clamp: bool = True) -> np.ndarray:
    """(N_MELS, frames) of log10 power. n_fft and hop are chosen in SECONDS so
    a 44.1 k and a 48 k clip give the same number of frames covering the same
    time, and the bank is in Hz, so the rows mean the same thing."""
    n_fft = int(round(WIN_S * sr)) // 2 * 2
    hop = max(1, int(round(HOP_S * sr)))
    win = np.hanning(n_fft)
    n = len(x)
    starts = np.arange(0, max(1, n - n_fft + 1), hop)
    frames = np.stack([x[s:s + n_fft] * win for s in starts])
    spec = np.abs(np.fft.rfft(frames, axis=1)) ** 2
    mel = (mel_bank(sr, n_fft) @ spec.T)
    if floor_clamp:
        # One absolute floor for the whole clip, FLOOR_DB below its own peak
        # band energy. Buries the refs' -76 dBFS converter hiss and our exact
        # digital zero at the same value, so neither is a cue. The lower guard
        # matters: `deg_nonoise` renders CP as pure silence, and a silent clip
        # must come out as a finite floor-valued vector, not a nan.
        floor = max(mel.max() * (10.0 ** (FLOOR_DB / 10.0)), 1e-20)
    else:
        floor = 1e-20
    return np.log10(np.maximum(mel, floor))


def dct_ii(x: np.ndarray, n_out: int) -> np.ndarray:
    n = x.shape[0]
    k = np.arange(n_out)[:, None]
    m = np.arange(n)[None, :]
    return (np.cos(np.pi * k * (2 * m + 1) / (2 * n)) @ x) * np.sqrt(2.0 / n)


def features(x: np.ndarray, sr: int, floor_clamp: bool = True, extra: bool = False) -> tuple:
    """Returns (vector, names). 40 log-mel bands x 4 segments (mean) plus
    20 MFCCs x 4 segments (mean and std) = 320 numbers.

    With `extra`, 190 further columns from `model/discrimination_features.py`
    are appended: the multi-period fold, the dominant-period probe, the
    constant-Q sub-bands and the multi-scale windows. They are deterministic
    transforms handed to the SAME classifier and read on the SAME
    knob-equivalent yardstick -- not a learned judge, and the arm is reported
    both ways so the cost of the addition is visible."""
    lm = logmel(x, sr, floor_clamp)
    mf = dct_ii(lm, N_MFCC)
    bounds = np.linspace(0, lm.shape[1], N_SEG + 1).astype(int)
    vals, names = [], []
    edges = _mel_to_hz(np.linspace(_hz_to_mel(FMIN), _hz_to_mel(FMAX), N_MELS + 2))
    for s in range(N_SEG):
        a, b = bounds[s], max(bounds[s + 1], bounds[s] + 1)
        for m in range(N_MELS):
            vals.append(lm[m, a:b].mean())
            names.append(f"mel{m:02d}@{edges[m + 1]:.0f}Hz.seg{s}")
        for c in range(N_MFCC):
            vals.append(mf[c, a:b].mean())
            names.append(f"mfcc{c:02d}.seg{s}.mean")
            vals.append(mf[c, a:b].std())
            names.append(f"mfcc{c:02d}.seg{s}.std")
    if extra:
        import discrimination_features as dfx
        ev, en = dfx.extra_features(x, sr)
        vals = list(vals) + list(ev)
        names = names + en
    return np.asarray(vals, dtype=float), names


# --- the interpretable diagnostics, kept alongside the general representation ---
# These ARE the quantities the model was fitted against. Measuring them at the
# held-out settings is a generalisation test, not circular, and they are the
# only features here a circuit designer can act on directly.
INTERP_NAMES = ("f0_Hz", "tau_ms", "centroid_Hz", "share_lo", "share_mid",
                "share_hi", "attack_ms", "flatness_dB")


def interpretable_features(x: np.ndarray, sr: int) -> dict:
    """f0, tau, magnitude centroid, three band shares, attack time and
    spectral flatness -- computed on the same conditioned WINDOW_S clip as the
    classifier features, so the two representations see exactly one signal."""
    X = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    f = np.fft.rfftfreq(len(x), 1.0 / sr)
    P = X ** 2
    tot = max(P.sum(), 1e-20)
    w = max(1, int(0.005 * sr))
    e = np.sqrt(np.convolve(x ** 2, np.ones(w) / w, "same"))
    band = (f >= 5000) & (f < 15000)
    g = np.exp(np.log(np.maximum(P[band], 1e-20)).mean())
    return dict(
        f0_Hz=measure_f0(x, sr, 900.0),
        tau_ms=1000.0 * measure_tau(x, sr),
        centroid_Hz=float((f * X).sum() / max(X.sum(), 1e-20)),
        share_lo=float(P[f < 700].sum() / tot),
        share_mid=float(P[(f >= 700) & (f < 5000)].sum() / tot),
        share_hi=float(P[f >= 5000].sum() / tot),
        attack_ms=1000.0 * float(np.argmax(e)) / sr,
        flatness_dB=float(10 * np.log10(g / max(P[band].mean(), 1e-20))),
    )


def interpretable_table(clips, laws, refdir, cache=None) -> list:
    """One row per clip: the diagnostics, tagged with the experiment it
    belongs to so a sound-matching row can never be read as an emulation row."""
    rows = []
    for c in clips:
        key = ("interp", c.rec)
        if cache is not None and key in cache:
            d = cache[key]
        else:
            if c.path:
                x, sr = read_wav(c.path)
            else:
                x, sr = render(c.voice, c.knobs, laws, c.side)
            d = interpretable_features(condition(x, sr, level_match=True), sr)
            if cache is not None:
                cache[key] = d
        rows.append(dict(voice=c.voice, knobs=c.knobs, side=c.side,
                         experiment=c.experiment, **d))
    return rows


def interpretable_gap(rows, voice: str, side: str, test_only: bool = True) -> dict:
    """Relative error of each diagnostic, ours against the real machine, at
    matched settings. This is the generalisation test on the fitted
    quantities: `test_only` restricts it to knob positions the law never saw."""
    real = {r["knobs"]: r for r in rows if r["voice"] == voice and r["side"] == "real"
            and (r["experiment"] == EXP_EMULATION or not test_only)}
    ours = {r["knobs"]: r for r in rows if r["voice"] == voice and r["side"] == side
            and (r["experiment"] == EXP_EMULATION or not test_only)}
    out = {}
    for nm in INTERP_NAMES:
        errs = []
        for k in sorted(set(real) & set(ours)):
            a, b = real[k][nm], ours[k][nm]
            if not (np.isfinite(a) and np.isfinite(b)):
                continue
            errs.append((b - a) / abs(a) if abs(a) > 1e-9 else b - a)
        if errs:
            out[nm] = dict(n=len(errs), mean_rel_err=float(np.mean(errs)),
                           abs_rel_err=float(np.mean(np.abs(errs))))
    return out


FEATURE_BANDS = ((30, 200, "30-200Hz"), (200, 700, "200-700Hz"), (700, 2000, "0.7-2kHz"),
                 (2000, 5000, "2-5kHz"), (5000, 18000, "5-18kHz"))


def feature_groups(names) -> dict:
    """Group the 320 features into (frequency band x time segment) buckets so
    attribution comes out as something a circuit designer can act on, plus one
    bucket per segment for the MFCCs (which are not band-localised)."""
    import discrimination_features as dfx
    groups: dict = {}
    for i, nm in enumerate(names):
        seg = nm.split(".seg")[1][0]
        g = dfx.extra_group(nm)
        if g is not None:
            groups.setdefault(g, []).append(i)
            continue
        if nm.startswith("mel"):
            hz = float(nm.split("@")[1].split("Hz")[0])
            band = next((lab for lo, hi, lab in FEATURE_BANDS if lo <= hz < hi), "5-18kHz")
        else:
            band = "mfcc"
        groups.setdefault(f"{band}.seg{seg}", []).append(i)
    return groups


# ---------------------------------------------------------------------------
# Control laws: knob position -> register values, fitted ONLY at FIT_KNOBS
# ---------------------------------------------------------------------------
def fit_quad(ks, ys, link: str = "log"):
    """Fit y(k) through exactly the three FIT_KNOBS points. `log` for
    quantities that are positive and multiplicative (tau, f0); `logit` for
    quantities that are a fraction of total energy. Three points, three
    parameters -- an interpolating law, not a regression, and its only test is
    whether it lands on the two knob positions it has never seen."""
    ks = np.asarray(ks, float)
    if link == "log":
        z = np.log(np.maximum(ys, 1e-12))
    elif link == "logit":
        p = np.clip(np.asarray(ys, float), 1e-3, 1 - 1e-3)
        z = np.log(p / (1 - p))
    else:
        raise ValueError(link)
    return QuadLaw(np.polyfit(ks, z, min(2, len(ks) - 1)), link)


class QuadLaw:
    """A fitted law, as an object rather than a closure so the whole law set
    can be pickled out to the render workers unchanged. Being able to ship the
    exact fitted law to every worker is what keeps the parallel renders
    identical to the serial ones."""

    def __init__(self, coef, link):
        self.coef, self.link = np.asarray(coef, float), link

    def __call__(self, k):
        z = float(np.polyval(self.coef, k))
        return math.exp(z) if self.link == "log" else 1.0 / (1.0 + math.exp(-z))

    def __repr__(self):
        return f"QuadLaw({self.link}, {np.array2string(self.coef, precision=4)})"


def measure_tau(x: np.ndarray, sr: int, lo_db: float = -3.0, hi_db: float = -27.0) -> float:
    """Amplitude time constant as a REGRESSION on the log envelope over a
    stated dB range, never a first 1/e crossing -- the hats and the cowbell
    are sums of squares whose envelopes beat by several dB and a first
    crossing reads the first trough (docs/drum-verification.md)."""
    w = max(1, int(0.005 * sr))
    e = np.sqrt(np.convolve(x ** 2, np.ones(w) / w, "same"))
    if e.max() <= 0:
        return float("nan")
    d = 20 * np.log10(np.maximum(e / e.max(), 1e-9))
    i0 = int(np.argmax(d <= lo_db))
    idx = np.arange(i0, len(d))
    m = (d[i0:] <= lo_db) & (d[i0:] >= hi_db)
    if m.sum() < 20:
        return float("nan")
    j = idx[m]
    j = j[j < j[0] + int(0.9 * sr)]
    a = np.polyfit(j / sr, d[j], 1)[0]
    return -20.0 / math.log(10) / a if a < 0 else float("nan")


def measure_f0(x: np.ndarray, sr: int, fmax: float) -> float:
    n = min(len(x), int(0.3 * sr))
    X = np.abs(np.fft.rfft(x[:n] * np.hanning(n)))
    f = np.fft.rfftfreq(n, 1.0 / sr)
    m = (f > 40) & (f < fmax)
    return float(f[m][np.argmax(X[m])])


def band_share(x: np.ndarray, sr: int, f1: float, f2: float) -> float:
    X = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    f = np.fft.rfftfreq(len(x), 1.0 / sr)
    tot = X.sum()
    return float(X[(f >= f1) & (f < f2)].sum() / tot) if tot > 0 else 0.0


def partial_ratio(x: np.ndarray, sr: int, t1: float = 0.060) -> float:
    """Energy of the snare's upper bridged-T partial over its lower one, over
    the first `t1` from onset where both are alive.

    This is what VR8 TONE moves. It is a RATIO of two narrow bands of one
    clip, so the window it is measured through cancels; what does not cancel
    is measuring it over a span long enough for the fast partial to be gone,
    which is why t1 is 60 ms and stated.
    """
    seg = np.asarray(x, float)[:int(t1 * sr)]
    X = np.abs(np.fft.rfft(seg * np.hanning(len(seg)))) ** 2
    f = np.fft.rfftfreq(len(seg), 1.0 / sr)
    return float(X[(f >= 260) & (f < 430)].sum() / max(X[(f >= 130) & (f < 230)].sum(), 1e-20))


def fit_laws(refdir: str, all_sounds: bool = False) -> dict:
    """Measure the real machine at FIT_KNOBS only and return the laws.

    Which physical quantity each knob moves was read off the machine, not
    assumed: BD TONE leaves f0 at 50.0 Hz on all 25 files and moves only the
    click; SD TONE shortens the body ring (28.5 -> 13.6 ms) and does not move
    the 168/172 Hz pair; SNAPPY sets the noise share; TUNING is the tom f0;
    OH DECAY saturates above 7.5, which is exactly the held-out position.

    Laws are separable by construction: the DECAY law is measured down the
    TONE = 5.0 column and the TONE law across the DECAY = 5.0 row. The 16
    held-out bass-drum settings therefore also test separability, which is a
    claim about the circuit and not only about interpolation.
    """
    def ref(voice, knobs):
        d, prefix, _ = ALL_REF[voice]
        code = "".join(c for k in knobs for c, v in KNOB_CODES.items() if v == k)
        x, sr = read_wav(os.path.join(refdir, d, f"{prefix}{code}.WAV"))
        return x[onset(x):], sr

    laws: dict = {}
    # --- BD: DECAY -> body tau; TONE -> click energy above 300 Hz in 10 ms ---
    taus, clicks = [], []
    for k in FIT_KNOBS:
        x, sr = ref("BD", (5.0, k))
        taus.append(measure_tau(x, sr))
        x, sr = ref("BD", (k, 5.0))
        clicks.append(band_share(x[:int(0.010 * sr)], sr, 300.0, 20000.0))
    laws["BD.decay_tau"] = fit_quad(FIT_KNOBS, taus, "log")
    laws["BD.tone_click"] = fit_quad(FIT_KNOBS, clicks, "logit")
    laws["_meas.BD.decay_tau"] = taus
    laws["_meas.BD.tone_click"] = clicks

    # --- SD: TONE -> the two partials' RATIO; SNAPPY -> noise share ---
    #
    # TONE WAS READ WRONG UNTIL 2026-09-18, and the error is the same family as
    # every other one in this voice's history: a single tau fitted to a sum of
    # two modes that decay at different rates. `measure_tau` returns 28.5 /
    # 27.4 / 13.6 ms across TONE, and the earlier law wrote that tau into BOTH
    # body modes -- which made our upper partial ring three times too long and
    # left the balance, the thing the knob actually moves, fixed.
    #
    # Fitted separately, the machine's two modes are 29-39 ms and 5-11 ms at
    # every TONE position and their RATIO moves 0.0015 -> 2.205, a span of
    # 31.7 dB. It is the same ratio with the snappy path up and down, which is
    # what says it belongs to the resonators. Roland says so too: "The output
    # ratio of the two can be changed by VR8 (TONE)" (SN p.6).
    #
    # `SD.tone_tau` is still measured, and still reported, as the record of
    # the withdrawn law; nothing writes it into a mode any more.
    taus, shares, ratios = [], [], []
    for k in FIT_KNOBS:
        x, sr = ref("SD", (k, 0.0))          # SNAPPY 0: the body alone
        taus.append(measure_tau(x, sr))
        ratios.append(partial_ratio(x, sr))
        x, sr = ref("SD", (5.0, k))
        shares.append(band_share(x, sr, 700.0, 20000.0))
    laws["SD.tone_ratio"] = fit_quad(FIT_KNOBS, ratios, "log")
    laws["SD.tone_tau"] = fit_quad(FIT_KNOBS, taus, "log")       # withdrawn; reported only
    laws["SD.snappy_share"] = fit_quad(FIT_KNOBS, shares, "logit")
    laws["_meas.SD.tone_ratio"] = ratios
    laws["_meas.SD.tone_tau"] = taus
    laws["_meas.SD.snappy_share"] = shares

    # --- toms: TUNING -> f0 ---
    for voice, fmax in (("LT", 200.0), ("HT", 400.0)):
        f0s = []
        for k in FIT_KNOBS:
            x, sr = ref(voice, (k,))
            f0s.append(measure_f0(x, sr, fmax))
        laws[f"{voice}.tuning_f0"] = fit_quad(FIT_KNOBS, f0s, "log")
        laws[f"_meas.{voice}.tuning_f0"] = f0s

    # --- OH: DECAY -> envelope tau ---
    taus = []
    for k in FIT_KNOBS:
        x, sr = ref("OH", (k,))
        taus.append(measure_tau(x, sr))
    laws["OH.decay_tau"] = fit_quad(FIT_KNOBS, taus, "log")
    laws["_meas.OH.decay_tau"] = taus

    if not all_sounds:
        return laws

    # --- the other four tuned circuits: TUNING -> f0, exactly as LT and HT --
    for voice in ("MT", "LC", "MC", "HC"):
        f0s = []
        for k in FIT_KNOBS:
            x, sr = ref(voice, (k,))
            f0s.append(measure_f0(x, sr, TUNING_FMAX[voice]))
        laws[f"{voice}.tuning_f0"] = fit_quad(FIT_KNOBS, f0s, "log")
        laws[f"_meas.{voice}.tuning_f0"] = f0s

    # --- CY: DECAY -> envelope tau; TONE -> the two bands' RATIO ------------
    #
    # TONE IS A BALANCE, NOT A DECAY, and this is the second voice in this
    # study to make that distinction matter. Measured down the TONE column the
    # cymbal's single fitted tau runs 464 -> 196 ms, which reads exactly like a
    # decay knob; what is actually moving is the share of the energy sitting on
    # the long 3.45 kHz band-pass versus the short 10.5 kHz one (2-5 kHz share
    # 0.762 -> 0.686, 5-13 kHz 0.180 -> 0.261). Fitting a tau to it would write
    # a decay into a circuit whose decay the knob does not touch -- which is
    # precisely the error withdrawn from the snare's TONE law on 2026-09-18.
    # So TONE is fitted as the band ratio and DECAY as the tau, and the tau is
    # measured down the TONE = 5.0 column where the balance is fixed.
    taus, ratios = [], []
    for k in FIT_KNOBS:
        x, sr = ref("CY", (5.0, k))
        taus.append(measure_tau(x, sr))
        x, sr = ref("CY", (k, 5.0))
        ratios.append(band_share(x, sr, 5000.0, 13000.0)
                      / max(band_share(x, sr, 2000.0, 5000.0), 1e-9))
    laws["CY.decay_tau"] = fit_quad(FIT_KNOBS, taus, "log")
    laws["CY.tone_ratio"] = fit_quad(FIT_KNOBS, ratios, "log")
    laws["_meas.CY.decay_tau"] = taus
    laws["_meas.CY.tone_ratio"] = ratios
    return laws


# ---------------------------------------------------------------------------
# Arms: our renders. `ours` is the kit as it stands; the rest are controls.
# ---------------------------------------------------------------------------
ARMS = ("ours", "docfix",
        "deg_decay", "deg_nonoise", "deg_qcoarse",              # large
        "deg_tail75", "deg_tail50", "deg_snappy6", "deg_snappy12",
        "deg_q6bit", "deg_q5bit")                                # graded
POSITIVE_CONTROL_ARMS = ("deg_decay", "deg_nonoise", "deg_qcoarse")
GRADED_CONTROL_ARMS = ("deg_tail75", "deg_tail50", "deg_snappy6", "deg_snappy12",
                       "deg_q6bit", "deg_q5bit")
# Which voices each degradation can possibly touch: rendering an arm for a
# voice it cannot change would just duplicate `ours` and waste an hour.
ARM_VOICES = {
    "deg_nonoise": ("SD", "CP", "MA"),
    "deg_snappy6": ("SD",), "deg_snappy12": ("SD",),
    "deg_tail75": ("BD", "SD", "LT", "HT", "CH", "OH",
                   "MT", "LC", "MC", "HC", "CY", "RS", "CL"),
    "deg_tail50": ("BD", "SD", "LT", "HT", "CH", "OH",
                   "MT", "LC", "MC", "HC", "CY", "RS", "CL"),
    "deg_qcoarse": ("SD", "CH", "OH", "CP", "CB", "CY", "RS", "CL", "MA"),
    "deg_q6bit": ("SD", "CH", "OH", "CP", "CB", "CY", "RS", "CL", "MA"),
    "deg_q5bit": ("SD", "CH", "OH", "CP", "CB", "CY", "RS", "CL", "MA"),
}

ARM_DOC = {
    "ours": "kit_808() as it stands on the rendered commit, with the fitted knob law: "
            "SD TONE as the two partials' amplitude ratio (contract rev 7), SD SNAPPY as "
            "the noise share, BD DECAY as the body tau, BD TONE as the click, tom TUNING "
            "as f0 and OH DECAY as the envelope tau.",
    "docfix": "the same, plus the fixes docs/drum-verification.md prescribes: BD f0 50.0 Hz "
              "and the 130 Hz/4 ms attack window, SD noise raised to the machine's share, "
              "CB band-pass 1100 Hz Q 2.8 with a 98 ms tail and each oscillator gated "
              "separately. Reported as a second subject, never as the subject.",
    "deg_decay": "POSITIVE CONTROL: every body mode's tau multiplied by 4. Wrong decay.",
    "deg_nonoise": "POSITIVE CONTROL: the SD snappy path and the CP noise path removed. "
                   "The missing-noise-path failure, taken to its limit.",
    "deg_qcoarse": "POSITIVE CONTROL: every mode's f0 quantised to a 4-bit grid across its "
                   "own decade. A coarsely quantised cutoff.",
    "deg_tail75": "GRADED CONTROL: body tau x 0.75. A moderately shortened tail.",
    "deg_tail50": "GRADED CONTROL: body tau x 0.50.",
    "deg_snappy6": "GRADED CONTROL: SD noise power 6 dB under the law's target -- a "
                   "partially restored snare, not an absent one.",
    "deg_snappy12": "GRADED CONTROL: SD noise power 12 dB under the law's target.",
    "deg_q6bit": "GRADED CONTROL: mode f0 on a 6-bit grid. A slightly coarse cutoff step.",
    "deg_q5bit": "GRADED CONTROL: mode f0 on a 5-bit grid.",
}


def _quantise_f0(hz: float, bits: int = 4) -> float:
    lo, hi = hz / 3.0, hz * 3.0
    steps = (1 << bits) - 1
    g = np.log(lo) + np.round((np.log(hz) - np.log(lo)) / (np.log(hi) - np.log(lo)) * steps) \
        / steps * (np.log(hi) - np.log(lo))
    return float(np.exp(g))


def _TOM_MODE(voice: str) -> int:
    import drums_fx as dx
    return {"LT": dx.M_LT, "LC": dx.M_LT, "MT": dx.M_MT,
            "MC": dx.M_MT, "HT": dx.M_HT, "HC": dx.M_HT}[voice]


def _degrade_modes(voice: str, bd_f0: float):
    """Which body modes a decay degradation must move for THIS voice.

    The original five are listed unconditionally, exactly as they were before
    the kit grew, because a mode only reaches the output when its own stop is
    struck -- so listing another voice's modes is inert and every degraded arm
    rendered before this change renders identically. The tom and conga sounds
    are the exception and must be looked up: LC, MC and HC share their
    circuit's mode with LT, MT and HT but sit at a different f0 and Q, and
    re-typing the tom's 90 Hz into a conga would retune it rather than
    lengthen it."""
    import drums_fx as dx
    if voice in ("MT", "LC", "MC", "HC"):
        f0, q, _ = dx.TOM_PRESET[voice]
        return [(_TOM_MODE(voice), f0, q)]
    return [(dx.M_BD, bd_f0, None), (dx.M_SDLO, 173.0, None), (dx.M_SDHI, 336.0, None),
            (dx.M_LT, 90.0, 25.0), (dx.M_HT, 185.0, 25.0)]


def _degrade_envs(voice: str):
    """Which envelopes a decay degradation must move. Same rule: the hats are
    listed unconditionally and are inert elsewhere; the sounds whose whole
    decay lives in an envelope rather than a resonator are added."""
    import drums_fx as dx
    base = [(dx.E_CH, dx.CH), (dx.E_OH, dx.OH)]
    if voice == "CY":
        return base + [(dx.E_CYS, dx.CY), (dx.E_CYD, dx.CY), (dx.E_CYL, dx.CY)]
    if voice in ("RS", "CL"):
        return base + [(dx.E_RSG, dx.CL)]
    if voice == "MA":
        return base + [(dx.E_CPBURST, dx.CP)]
    return base


def kit_at(voice: str, knobs: tuple, laws: dict, arm: str = "ours"):
    """The reference kit with this voice's knobs applied through the fitted
    law, then the arm's modification. Returns a write list."""
    import drums_fx as dx

    # `kit_with_sounds` puts this sound's circuit into this sound's position.
    # For the eight sounds the kit already loads it rewrites the same values,
    # so the register image is identical to `kit_808()` and every arm rendered
    # before the kit grew to sixteen still renders bit for bit the same --
    # asserted in `test_the_eight_default_sounds_are_untouched_by_the_preset`.
    base = dict(dx.kit_with_sounds(voice))

    def set_mode(m, f0, q, amp=None, num=dx.RAW):
        if amp is None:                        # keep the kit's own level
            amp = base[dx.A_MODE + m * dx.MODE_STRIDE + 2] / 65536.0
        for a, v in dx.mode_writes(m, f0, q, amp, num):
            base[a] = v

    def get_mode_amp(m):
        return base[dx.A_MODE + m * dx.MODE_STRIDE + 2] / 65536.0

    def set_env(e, stop, tau, peak, **kw):
        for a, v in dx.env_writes(e, stop, tau, peak, **kw):
            base[a] = v

    # --- the knob law -------------------------------------------------------
    bd_f0 = 50.0 if arm == "docfix" else 56.0
    if voice == "BD":
        tone, decay = knobs
        tau = laws["BD.decay_tau"](decay)
        set_mode(dx.M_BD, bd_f0, math.pi * bd_f0 * tau)
        # TONE moves the click. Its level is solved in closed form from one
        # calibration render (click power is quadratic in the envelope peak).
        set_env(dx.E_BDCLICK, dx.BD, 0.0, _bd_click_peak(laws["BD.tone_click"](tone)), hold=48)
    elif voice == "SD":
        tone, snappy = knobs
        # TONE moves the upper partial's LEVEL, not either mode's decay: the
        # two Q registers stay exactly as the kit writes them (the circuit's),
        # and one amp register carries the knob.
        q_hi = _mode_q_from_regs(base[dx.A_MODE + dx.M_SDHI * dx.MODE_STRIDE],
                                 base[dx.A_MODE + dx.M_SDHI * dx.MODE_STRIDE + 1], 336.0)
        set_mode(dx.M_SDHI, 336.0, q_hi, _sd_partial_amp(laws["SD.tone_ratio"](tone)))
        share = laws["SD.snappy_share"](snappy)
        trim = {"deg_snappy6": 10 ** (-6 / 20.0), "deg_snappy12": 10 ** (-12 / 20.0)}.get(arm, 1.0)
        # the kit's own snappy rate, not a literal: revision 7 measured it
        set_env(dx.E_SDN, dx.SD,
                _env_tau_from_reg(base[dx.A_ENV + dx.E_SDN * dx.ENV_STRIDE + 2]),
                trim * _sd_noise_peak(share, arm))
    elif voice in ("LT", "HT"):
        f0 = laws[f"{voice}.tuning_f0"](knobs[0])
        m = dx.M_LT if voice == "LT" else dx.M_HT
        q = base[dx.A_MODE + m * dx.MODE_STRIDE]      # keep Q: tau is flat across TUNING
        kit_f0, kit_q = (90.0, 25.0) if voice == "LT" else (185.0, 25.0)
        set_mode(m, f0, kit_q * f0 / kit_f0)          # tau constant => Q scales with f0
    elif voice == "OH":
        set_env(dx.E_OH, dx.OH, laws["OH.decay_tau"](knobs[0]), 1.0, choke=dx.CH)
    elif voice in ("MT", "LC", "MC", "HC"):
        # the same TUNING law as LT and HT: f0 from the law, Q scaled with f0
        # so that the circuit's tau stays where the machine keeps it (measured
        # flat across the knob on all six -- LT 91->86 ms, HC 36->33 ms).
        f0 = laws[f"{voice}.tuning_f0"](knobs[0])
        m = _TOM_MODE(voice)
        kit_f0, kit_q, _ = dx.TOM_PRESET[voice]
        set_mode(m, f0, kit_q * f0 / kit_f0)
    elif voice == "CY":
        # #413: delegated. The old branch solved TONE for the REFERENCE's own
        # band ratio (`CY.tone_ratio`, so it reproduced the recording it was
        # judged against by construction) and DECAY through a clamped two-point
        # power law whose E_CYL tau ran off the end of the Q0.16 rate register
        # (7.5 and 10.0 rendered bit-identical). Both are gone: the image comes
        # from the one shared constructor, `cymbal_candidate.shipped_knob_kit`,
        # which at the anchor writes nothing, so it IS the shipped kit. `laws`
        # is accepted for compatibility and deliberately unread for CY.
        import cymbal_candidate as cc
        base.clear()
        base.update(dict(cc.shipped_knob_kit(knobs[0], knobs[1], sound=voice)))

    # --- the arm ------------------------------------------------------------
    if arm == "docfix":
        if voice == "BD":
            # the 130 Hz / 4 ms attack window of the reference, as a host would
            # write it; applied as a raised, pitched click rather than a retune
            set_env(dx.E_BDCLICK, dx.BD, 1.5e-3,
                    min(0.5, 4.0 * _bd_click_peak(laws["BD.tone_click"](knobs[0]))), hold=8)
        if voice == "CB":
            set_mode(dx.M_CBBP, 1100.0, 2.8, num=dx.BP)
            set_env(dx.E_CBA, dx.CB, 12e-3, 0.5)
            set_env(dx.E_CBB, dx.CB, 98e-3, 0.5)
    elif arm == "deg_decay":
        for m, f0, q in _degrade_modes(voice, bd_f0):
            a1 = base[dx.A_MODE + m * dx.MODE_STRIDE]
            cur = _mode_q_from_regs(a1, base[dx.A_MODE + m * dx.MODE_STRIDE + 1], f0)
            set_mode(m, f0, cur * 4.0)
        for e, stop in _degrade_envs(voice):
            set_env(e, stop, 4.0 * _env_tau_from_reg(base[dx.A_ENV + e * dx.ENV_STRIDE + 2]),
                    base[dx.A_ENV + e * dx.ENV_STRIDE + 1] / dx.FULL24,
                    choke=dx.CH if e == dx.E_OH else 15)
    elif arm == "deg_nonoise":
        set_env(dx.E_SDN, dx.SD, 15e-3, 0.0)
        set_env(dx.E_CPBURST, dx.CP, 4e-3, 0.0, bursts=2, period=480)
        set_env(dx.E_CPTAIL, dx.CP, 47e-3, 0.0)
    elif arm in ("deg_tail75", "deg_tail50"):
        f = 0.75 if arm == "deg_tail75" else 0.50
        for m, f0, kq in _degrade_modes(voice, bd_f0):
            a1 = base[dx.A_MODE + m * dx.MODE_STRIDE]
            cur = _mode_q_from_regs(a1, base[dx.A_MODE + m * dx.MODE_STRIDE + 1], f0)
            set_mode(m, f0, cur * f)
        for e, stop in _degrade_envs(voice):
            set_env(e, stop, f * _env_tau_from_reg(base[dx.A_ENV + e * dx.ENV_STRIDE + 2]),
                    base[dx.A_ENV + e * dx.ENV_STRIDE + 1] / dx.FULL24,
                    choke=dx.CH if e == dx.E_OH else 15)
    elif arm in ("deg_qcoarse", "deg_q6bit", "deg_q5bit"):
        bits = {"deg_qcoarse": 4, "deg_q6bit": 6, "deg_q5bit": 5}[arm]
        # f0 and Q are read back from the KIT, never re-typed here: `M_SDHP`
        # and the cowbell's 900 Hz / Q 4 were both re-typed, and both went
        # stale at contract revision 6 -- the first into an AttributeError
        # that would have crashed this positive control the moment it ran.
        for m, f0, q, num in ((dx.M_HATBP, 7117.0, 6.0, dx.BP), (dx.M_OHHP, 7800.0, 2.5, dx.HP),
                              (dx.M_CHHP, 11700.0, 2.5, dx.HP), (dx.M_SDN, 2750.0, 0.7, dx.BP),
                              (dx.M_CPBP, 1071.0, 1.6, dx.BP), (dx.M_CBBP, 1100.0, 2.8, dx.BP),
                              # the five circuits added with the sixteen-sound
                              # kit. Inert for the original eight -- a mode only
                              # sounds when its own stop is struck -- so every
                              # number this arm produced before still stands.
                              (dx.M_CYBP, dx.CY_LO_HZ, dx.CY_Q, dx.BP),
                              (dx.M_CYHI, dx.CY_HI_HZ, dx.CY_HI_Q, dx.BP),
                              (dx.M_RS1, dx.RS_LO_HZ, dx.RS_LO_Q, dx.RAW),
                              (dx.M_RS2, dx.RS_HI_HZ, dx.RS_HI_Q, dx.RAW)):
            num = base[dx.A_MODE + m * dx.MODE_STRIDE + 3]
            set_mode(m, _quantise_f0(f0, bits), q, get_mode_amp(m), num)

    return sorted(base.items())


def _mode_q_from_regs(a1: int, a2: int, f0: float | None) -> float:
    """Recover Q from a pole pair: r = sqrt(a2/2^24-ish) is fiddly, so go
    through the documented identity tau = Q / (pi f0) using the kit's own f0
    where we know it, and fall back to the kit constant where we do not."""
    import drums_fx as dx
    r = math.sqrt(max(1e-9, -dx.s26(a2) / float(1 << 24)))
    if f0 is None:
        return 22.3
    tau = -1.0 / (dx.SR * math.log(max(r, 1e-9)))
    return math.pi * f0 * tau


def _env_tau_from_reg(rate: int) -> float:
    import drums_fx as dx
    rate = max(1, int(rate))
    return -1.0 / (dx.SR * math.log(max(1e-12, 1.0 - rate / float(1 << dx.RATE_Q))))


# Closed-form level solves, calibrated once per process from a single render.
# Both paths are linear in the envelope peak, so their power is quadratic in
# it and the peak that hits a target energy share follows directly.
_CAL: dict = {}


def _calibrate():
    if _CAL:
        return _CAL
    import drums_fx as dx
    for name, stop, env, meas in (("sd", dx.SD, dx.E_SDN, None), ("bd", dx.BD, dx.E_BDCLICK, None)):
        pass
    # SD: body power with the noise off, noise power at a reference peak.
    # BOTH renders use the KIT's own snappy rate. Calibrating at a literal
    # 15 ms while the kit runs at the measured 30 ms would leave every solved
    # peak sqrt(2) high -- the noise power a peak buys is proportional to the
    # envelope's time constant, so the calibration and the render have to
    # agree about it.
    sd_tau = _env_tau_from_reg({a: v for a, v in dx.kit_808()}
                               [dx.A_ENV + dx.E_SDN * dx.ENV_STRIDE + 2])
    x0, sr = _render_raw(dx.SD, _override(dx.env_writes(dx.E_SDN, dx.SD, sd_tau, 0.0)))
    x1, _ = _render_raw(dx.SD, _override(dx.env_writes(dx.E_SDN, dx.SD, sd_tau, 0.5)))
    b = band_share(x0, sr, 0.0, 700.0) * float((x0 ** 2).sum())
    n = max(1e-12, band_share(x1, sr, 700.0, 20000.0) * float((x1 ** 2).sum()))
    _CAL["sd"] = (b, n, 0.5)
    # SD TONE: the rendered partial ratio at the kit's own upper-mode amp.
    # The ratio is quadratic in that amp, so one render fixes the whole law.
    _CAL["sd_tone"] = (partial_ratio(x0, sr),
                       {a: v for a, v in dx.kit_808()}[dx.A_MODE + dx.M_SDHI * dx.MODE_STRIDE + 2]
                       / 65536.0)
    # BD: same, for the click above 300 Hz in the first 10 ms.
    y0, sr = _render_raw(dx.BD, _override(dx.env_writes(dx.E_BDCLICK, dx.BD, 0.0, 0.0, hold=48)))
    y1, _ = _render_raw(dx.BD, _override(dx.env_writes(dx.E_BDCLICK, dx.BD, 0.0, 0.06, hold=48)))
    w = int(0.010 * sr)
    lo = float((y0[:w] ** 2).sum()) * band_share(y0[:w], sr, 0.0, 300.0)
    hi = max(1e-12, float((y1[:w] ** 2).sum()) * band_share(y1[:w], sr, 300.0, 20000.0))
    _CAL["bd"] = (lo, hi, 0.06)
    return _CAL


def _sd_noise_peak(target_share: float, arm: str = "ours") -> float:
    b, n, p0 = _calibrate()["sd"]
    s = min(max(target_share, 0.0), 0.995)
    if s <= 0:
        return 0.0
    return float(min(1.0, p0 * math.sqrt(s / (1 - s) * b / n)))


def _sd_partial_amp(target_ratio: float) -> float:
    """M_SDHI's amp that puts the two partials at `target_ratio`. Power is
    quadratic in the amp, so this is exact in level given one calibration
    render, the same closed form the two envelope peaks use."""
    ratio0, amp0 = _calibrate()["sd_tone"]
    return float(min(1.0, amp0 * math.sqrt(max(target_ratio, 1e-12) / max(ratio0, 1e-12))))


def _bd_click_peak(target_share: float) -> float:
    lo, hi, p0 = _calibrate()["bd"]
    s = min(max(target_share, 0.0), 0.9)
    if s <= 0:
        return 0.0
    return float(min(1.0, p0 * math.sqrt(s / (1 - s) * lo / hi)))


def _override(writes):
    import drums_fx as dx
    base = {a: v for a, v in dx.kit_808()}
    base.update(dict(writes))
    return sorted(base.items())


def _render_raw(stop: int, kit) -> tuple:
    import drums_fx as dx
    n = int(RENDER_S * dx.SR)
    d = dx.DrumsFx()
    dm, bd = d.play(dx.hit_writes([(10, stop, 1.0)], kit), n)
    g = dx.accent_reg(RENDER_GAIN)
    out = dx.output_fx(np.zeros(n), 0, dm, g, bd, g).astype(np.float64) / 32768.0
    return out[onset(out):], dx.SR


def render(voice: str, knobs: tuple, laws: dict, arm: str = "ours") -> tuple:
    """A STOP is a circuit and a SOUND is a position of it, so the stop struck
    comes from SOUND_STOP and not from the sound's index. Striking
    STOP_NAMES.index("CL") for the claves would fire the rimshot's circuit --
    the same circuit, but that is luck, and LC would fire stop 3 (HT)."""
    import drums_fx as dx
    return _render_raw(dx.SOUND_STOP[voice], kit_at(voice, knobs, laws, arm))


def ours_clips(voices, arm: str, refs) -> list:
    """One of our clips for every real clip, at the same knobs. Matching the
    settings one for one is what makes the paired test possible."""
    return [Clip(c.voice, c.knobs, arm) for c in refs if c.voice in voices]


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------
def clopper_pearson(k: int, n: int, alpha: float = 0.05) -> tuple:
    if n == 0:
        return (0.0, 1.0)
    lo = 0.0 if k == 0 else beta_dist.ppf(alpha / 2, k, n - k + 1)
    hi = 1.0 if k == n else beta_dist.ppf(1 - alpha / 2, k + 1, n - k)
    return (float(lo), float(hi))


def upper_bound_one_sided(k: int, n: int, alpha: float = 0.05) -> float:
    """The number an equivalence claim actually rests on. 10 of 20 correct has
    a one-sided 95 % upper bound of 0.68, which is not 'indistinguishable'."""
    if n == 0:
        return 1.0
    return 1.0 if k == n else float(beta_dist.ppf(1 - alpha, k + 1, n - k))


def n_for_margin(margin: float = EQUIV_MARGIN, alpha: float = 0.05) -> int:
    """Trials needed for a discriminator performing at exactly chance to be
    bounded below `margin` one-sided. Reported so 'not distinguishable' is
    always accompanied by what it would take to say it more tightly."""
    n = 4
    while n < 20000:
        if upper_bound_one_sided(n // 2, n, alpha) < margin:
            return n
        n += 2
    return -1


def assert_split_disjoint(train, test):
    """Split by recording, not by hit: a clip and every processed version of
    it share `rec`, and no `rec` may appear on both sides."""
    a = {c.rec for c in train}
    b = {c.rec for c in test}
    both = a & b
    if both:
        raise AssertionError(f"recording on both sides of the split: {sorted(both)[:5]}")
    return True


# ---------------------------------------------------------------------------
# The discriminator
# ---------------------------------------------------------------------------
def build_matrix(clips, laws, refdir, level_match=True, floor_clamp=True, cache=None,
                 extra=False):
    X, names = [], None
    for c in clips:
        key = (c.rec, level_match, floor_clamp, extra)
        if cache is not None and key in cache:
            v, names = cache[key]
        else:
            # a clip with a path is a recording (either side of a real-vs-real
            # control); only a clip without one is ours to render
            if c.path:
                x, sr = read_wav(c.path)
            else:
                x, sr = render(c.voice, c.knobs, laws, c.side)
            v, names = features(condition(x, sr, level_match), sr, floor_clamp, extra)
            if cache is not None:
                cache[key] = (v, names)
        X.append(v)
    return np.asarray(X), names


def zscore_per_voice(X, clips, fit_mask):
    """Standardise each feature within each voice, using that voice's FIT
    clips from both classes. Removes voice identity, so the classifier cannot
    score by recognising a kick, and keeps the scaling symmetric between the
    two classes."""
    Z = np.array(X, dtype=float)
    for v in sorted({c.voice for c in clips}):
        idx = np.array([i for i, c in enumerate(clips) if c.voice == v])
        f = idx[fit_mask[idx]]
        if len(f) < 2:
            f = idx
        mu, sd = X[f].mean(0), X[f].std(0)
        sd = np.where(sd < 1e-9, 1.0, sd)
        Z[idx] = (X[idx] - mu) / sd
    return Z


def stable_group_id(rec: str) -> int:
    """A group id that does not move between processes.

    THIS WAS `hash(rec) % (1 << 31)` AND THAT IS NOT REPRODUCIBLE. Python
    salts `hash()` on str per process (PYTHONHASHSEED), so the group ids
    handed to StratifiedGroupKFold were different on every run, the folds
    were different, the C chosen by the inner CV was different, and the
    published balanced accuracies moved between two runs of the identical
    command on the identical cache. Caught 2026-09-18 by re-running the study
    and getting pooled 0.868 once and 0.816 the next time with a byte-equal
    split hash and byte-equal feature vectors.

    Nothing about the distances moved, which is why it went unseen: a
    knob-equivalent has no classifier in it. It is the accuracies, the CIs and
    every ABX count that were unreproducible."""
    return int(hashlib.sha256(rec.encode()).hexdigest()[:8], 16)


def discriminate(clips, X, train_mask, test_mask, seed=0):
    """L2 logistic regression, C chosen by grouped inner CV on the training
    clips only (groups = the recording). Returns the held-out predictions and
    the fitted model."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import GridSearchCV, StratifiedGroupKFold

    y = np.array([0 if c.side == "real" else 1 for c in clips])
    assert set(np.unique(y)) <= {0, 1}, "the task must be binary"
    tr, te = np.where(train_mask)[0], np.where(test_mask)[0]
    assert_split_disjoint([clips[i] for i in tr], [clips[i] for i in te])
    groups = np.array([stable_group_id(c.rec) for c in clips])
    # STRATIFIED and grouped. Grouping alone lets a fold hold only one class
    # -- sklearn then fails that fit, scores it nan, and C is chosen by
    # accident rather than by cross-validation. Stratifying keeps both classes
    # in every fold while the grouping still keeps a recording whole.
    # A fold that holds only one class makes sklearn score that fit nan, and C
    # then gets chosen by accident instead of by cross-validation. Stratify as
    # well as group; and where the task is too small to stratify at all -- the
    # closed-hat control has exactly one recording on its side -- skip the
    # search and say so, rather than letting a silent nan pick C.
    counts_ = np.bincount(y[tr], minlength=2)
    mc = int(counts_.min())
    n_splits = int(min(4, mc, len(set(groups[tr])) // 2))
    best_C, searched = DEFAULT_C, False
    clf = LogisticRegression(max_iter=5000, solver="lbfgs", C=DEFAULT_C)
    if n_splits >= 2 and len(np.unique(y[tr])) == 2 and mc >= 2 * n_splits:
        try:
            grid = GridSearchCV(
                LogisticRegression(max_iter=5000, solver="lbfgs"),
                {"C": [0.003, 0.01, 0.03, 0.1, 0.3, 1.0, 3.0]},
                cv=StratifiedGroupKFold(n_splits=n_splits, shuffle=True, random_state=seed),
                scoring="accuracy", error_score="raise")
            grid.fit(X[tr], y[tr], groups=groups[tr])
            clf, best_C, searched = grid.best_estimator_, grid.best_params_["C"], True
        except ValueError:
            searched = False
    if not searched:
        clf.fit(X[tr], y[tr])
    pred = clf.predict(X[te])
    score = clf.decision_function(X[te])
    return dict(idx=te, y=y[te], pred=pred, score=score, clf=clf, C=best_C,
                C_searched=searched, n_train=len(tr))


def summarise(y, pred, label=""):
    k = int((y == pred).sum())
    n = len(y)
    lo, hi = clopper_pearson(k, n)
    return dict(label=label, n=n, correct=k, acc=k / n if n else float("nan"),
                ci=(lo, hi), upper=upper_bound_one_sided(k, n))


def paired_abx(clips, idx, score):
    """The intuitive framing: for each held-out knob setting, shown the real
    clip and ours, pick the real one. One trial per setting, chance 0.5."""
    by = {}
    for j, i in enumerate(idx):
        c = clips[i]
        by.setdefault((c.voice, c.knobs), {})[c.side == "real"] = score[j]
    k = n = 0
    for key, d in by.items():
        if True in d and False in d:
            n += 1
            k += int(d[True] < d[False])       # lower score = classified "real"
    return summarise(np.ones(n), np.ones(n) * (1 if n == 0 else 1), "") | \
        dict(n=n, correct=k, acc=k / n if n else float("nan"),
             ci=clopper_pearson(k, n), upper=upper_bound_one_sided(k, n), label="paired ABX")


def group_importance(clf, X, y, idx, names, seed=0, repeats=20):
    """Permutation importance by feature GROUP on the held-out clips: how far
    accuracy falls when a whole (band x segment) bucket is shuffled. Grouped,
    because single log-mel bands are strongly correlated and a per-feature
    importance would spread the credit into noise."""
    rng = np.random.default_rng(seed)
    base = float((clf.predict(X[idx]) == y).mean())
    out = {}
    for g, cols in feature_groups(names).items():
        drops = []
        for _ in range(repeats):
            Xp = X[idx].copy()
            Xp[:, cols] = Xp[rng.permutation(len(idx))][:, cols]
            drops.append(base - float((clf.predict(Xp) == y).mean()))
        out[g] = float(np.mean(drops))
    return base, dict(sorted(out.items(), key=lambda kv: -kv[1]))


def effect_sizes(X, clips, names, voice, mask):
    """Per-feature standardised difference in units of the REAL machine's own
    spread across its knob range: 'our 2-5 kHz tail sits N sigma outside
    anything the machine does to itself'. Interpretable; not used to classify."""
    r = np.array([i for i, c in enumerate(clips)
                  if c.voice == voice and c.side == "real" and mask[i]])
    o = np.array([i for i, c in enumerate(clips)
                  if c.voice == voice and c.side != "real" and mask[i]])
    if len(r) < 2 or len(o) < 1:
        return {}
    # THE DENOMINATOR IS THE MACHINE'S SPREAD OVER len(r) RECORDINGS, and for
    # the single-knob voices len(r) is 2. A column that happens to be nearly
    # constant across two recordings then has a near-zero sd and produces an
    # effect size in the thousands -- the sixteen-sound run reported 4747 for
    # the high conga's jitter bucket on exactly this. Refuse the column rather
    # than report it: the spread must be resolvable, not merely non-zero.
    sd = X[r].std(0)
    floor = 0.05 if len(r) >= 4 else 0.20
    sd = np.where(sd < floor, np.nan, sd)
    d = (X[o].mean(0) - X[r].mean(0)) / sd
    out = {}
    for g, cols in feature_groups(names).items():
        vals = d[cols]
        vals = vals[np.isfinite(vals)]
        if len(vals):
            out[g] = float(np.mean(np.abs(vals)))
    out["_n_real"] = float(len(r))
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


# ---------------------------------------------------------------------------
# Controls
# ---------------------------------------------------------------------------
def permutation_null(clips, X, train_mask, test_mask, n_iter=200, seed=0):
    """Shuffle the real/ours labels and re-run the whole fit. Calibrates what
    'chance' is for this pipeline at this sample size, rather than assuming
    0.5 -- if the pipeline had any structural bias, this is where it shows."""
    from sklearn.linear_model import LogisticRegression
    rng = np.random.default_rng(seed)
    y = np.array([0 if c.side == "real" else 1 for c in clips])
    tr, te = np.where(train_mask)[0], np.where(test_mask)[0]
    accs = []
    for _ in range(n_iter):
        p = rng.permutation(len(y))
        yp = y[p]
        clf = LogisticRegression(max_iter=2000, solver="lbfgs", C=0.1)
        clf.fit(X[tr], yp[tr])
        accs.append(float((clf.predict(X[te]) == yp[te]).mean()))
    return dict(mean=float(np.mean(accs)), p05=float(np.percentile(accs, 5)),
                p95=float(np.percentile(accs, 95)), n_iter=n_iter)


def cross_voice_control(refdir, cache, level_match=True, floor_clamp=True, seed=0,
                        extra=False):
    """POSITIVE CONTROL with no provenance cue at all: real against real,
    different voices, same machine, same converter, same afternoon. If the
    pipeline cannot separate a real low tom from a real low conga it has no
    timbral resolution and no result below means anything."""
    # Every pair needs at least 5 recordings a side. CH vs OH looks tempting
    # and is useless: the set holds exactly one closed hat, so the task has one
    # clip on one side and cannot be stratified, split or believed.
    pairs = (("LT", "LC"), ("HT", "HC"), ("BD", "MT"), ("LT", "MT"), ("LC", "MC"))
    out = {}
    for a, b in pairs:
        ca = ref_clips(refdir, voices=[a], include_unmodelled=True)
        cb = ref_clips(refdir, voices=[b], include_unmodelled=True)
        if not ca or not cb:
            continue
        clips = [Clip(c.voice, c.knobs, "real", c.path, rec=f"A:{c.rec}") for c in ca] + \
                [Clip(c.voice, c.knobs, "realB", c.path, rec=f"B:{c.rec}") for c in cb]
        X, names = build_matrix(clips, None, refdir, level_match, floor_clamp, cache, extra)
        fit = np.array([not c.is_test for c in clips])
        X = zscore_per_voice(X, [Clip("X", c.knobs, c.side) for c in clips], fit)
        tm, sm = fit, ~fit
        if sm.sum() < 2 or tm.sum() < 4:
            tm = np.array([i % 2 == 0 for i in range(len(clips))])
            sm = ~tm
        r = discriminate(clips, X, tm, sm, seed)
        out[f"{a} vs {b}"] = summarise(r["y"], r["pred"], f"real {a} vs real {b}")
    return out


def separation_curve(refdir, cache, voices=("BD", "SD"), level_match=True,
                     floor_clamp=True, seed=0, extra=False):
    """Real against real at a known knob separation. This is what converts an
    accuracy into something interpretable.

    ONE AXIS AT A TIME. An earlier version pooled both knobs into one task and
    topped out at 0.75 everywhere, because "low TONE" and "low DECAY" are
    different directions in feature space and a single classifier was being
    asked to call both of them the same class. Each (voice, knob, delta) is
    its own task; the split is on the OTHER knob, so a held-out item is a
    genuinely unseen position of the knob that is not being varied and no
    recording is used twice within a delta.

    The curve is an UPPER bound on the take-to-take floor. The Fischer set has
    one take per setting, so delta = 0 -- the number that would actually be
    the floor -- cannot be measured from this corpus at all.
    """
    disjoint = {2.5: [(0.0, 2.5), (5.0, 7.5)], 5.0: [(0.0, 5.0), (2.5, 7.5)],
                7.5: [(0.0, 7.5), (2.5, 10.0)], 10.0: [(0.0, 10.0)]}
    out = {}
    for voice in voices:
        d, prefix, nk = ALL_REF[voice]
        if nk != 2:
            continue
        for axis in (0, 1):
            kname = KNOB_NAMES[voice][axis]
            for delta, prs in disjoint.items():
                clips = []
                for lo, hi in prs:
                    for other in sorted(KNOB_CODES.values()):
                        for val, side in ((lo, "real"), (hi, "realB")):
                            kn = (val, other) if axis == 0 else (other, val)
                            code = "".join(c for k in kn for c, v in KNOB_CODES.items()
                                           if v == k)
                            p = os.path.join(refdir, d, f"{prefix}{code}.WAV")
                            if os.path.exists(p):
                                clips.append(Clip(voice, kn, side, p,
                                                  rec=f"{axis}:{lo}-{hi}:{side}:{kn}"))
                if len(clips) < 8:
                    continue
                split_val = [c.knobs[1 - axis] for c in clips]
                fit = np.array([v not in TEST_KNOBS for v in split_val])
                if fit.sum() < 4 or (~fit).sum() < 4:
                    continue
                X, names = build_matrix(clips, None, refdir, level_match, floor_clamp, cache, extra)
                X = zscore_per_voice(X, [Clip("X", c.knobs, c.side) for c in clips],
                                     np.ones(len(clips), bool))
                r = discriminate(clips, X, fit, ~fit, seed)
                s = summarise(r["y"], r["pred"], f"{voice} {kname} d={delta}")
                s["bal_acc"] = balanced_accuracy(r["y"], r["pred"])
                out[(voice, kname, delta)] = s
    return out


def _zspace(clips, cache_matrix, scale=None):
    """Z-score a feature matrix. With `scale` = (mu, sd) the scaling is FROZEN
    and supplied by the caller; without it, it is computed from the matrix
    itself -- which is what the published study did and is the behaviour kept
    so that published numbers reproduce.

    THE PUBLISHED KNOB-EQUIVALENT MEASURES TWO DISTANCES IN DIFFERENT UNITS.
    `distance_curve` scales on the pair of real populations it is comparing;
    `ours_distance` scales on the real-plus-ours population. Two different
    mu/sd, and then one is read off the other as though they shared a ruler.
    `voice_scale` gives both the same one."""
    if scale is not None:
        mu, sd = scale
    else:
        mu, sd = cache_matrix.mean(0), cache_matrix.std(0)
    sd = np.where(sd < 1e-9, 1.0, sd)
    return (cache_matrix - mu) / sd


def voice_scale(refs, voice, matrix_fn):
    """The frozen ruler for one voice: mean and sd over every REAL recording
    of that voice, and nothing else.

    It is independent of which arm is being measured and identical for the
    yardstick and for the thing read off it, which is the whole point. It is
    still a PER-VOICE ruler -- a knob-equivalent is by construction a distance
    on that voice's own knob -- so SD 3.4 and LT 6.7 are two readings on two
    dials and are NOT interchangeable numbers. That was true before this
    function and stays true after it."""
    use = [c for c in refs if c.voice == voice]
    if not use:
        return None
    M = matrix_fn(use)
    return M.mean(0), M.std(0)


def distance_curve(refdir, cache, voice, matrix_fn, level_match=True, floor_clamp=True,
                   scale=None):
    """The non-saturating version of the separation curve, and the one the
    knob-equivalent is read off.

    Classifier accuracy saturates: once every held-out clip is called
    correctly, 1.00 cannot say whether we are a little outside the machine's
    own spread or far outside it. A distance does not saturate. For each knob
    and each separation delta, take the median feature-space distance between
    real clips that far apart; that is the yardstick. Then measure the median
    distance between a real clip and ours AT THE SAME SETTING, and read it off
    the yardstick.
    """
    d, prefix, nk = ALL_REF[voice]
    steps = sorted(KNOB_CODES.values())
    out = {}
    if nk == 0:
        return out
    axes = range(nk)
    for axis in axes:
        kname = KNOB_NAMES[voice][axis]
        for delta in (2.5, 5.0, 7.5, 10.0):
            pairs = []
            for i, lo in enumerate(steps):
                hi = lo + delta
                if hi not in steps:
                    continue
                others = [(o,) for o in steps] if nk == 2 else [()]
                for o in others:
                    ka = (lo,) + o if axis == 0 else o + (lo,)
                    kb = (hi,) + o if axis == 0 else o + (hi,)
                    ka, kb = ka[:nk], kb[:nk]
                    if (voice, ka, "real") in cache and (voice, kb, "real") in cache:
                        pairs.append((ka, kb))
            if len(pairs) < 2:
                continue
            A = matrix_fn([Clip(voice, a, "real") for a, _ in pairs])
            B = matrix_fn([Clip(voice, b, "real") for _, b in pairs])
            Z = _zspace(None, np.vstack([A, B]), scale)
            n = len(A)
            dist = np.linalg.norm(Z[:n] - Z[n:], axis=1)
            out[(kname, delta)] = float(np.median(dist))
    return out


def ours_distance(refs, cache, voice, arm, matrix_fn, test_only=True, scale=None) -> float:
    """Median feature-space distance between the real machine and our render
    at the SAME knob setting, over the held-out settings."""
    use = [c for c in refs if c.voice == voice and (c.is_test or not test_only)]
    if not use:
        use = [c for c in refs if c.voice == voice]
    if not use:
        return float("nan")
    A = matrix_fn(use)
    B = matrix_fn([Clip(c.voice, c.knobs, arm) for c in use])
    Z = _zspace(None, np.vstack([A, B]), scale)
    n = len(A)
    return float(np.median(np.linalg.norm(Z[:n] - Z[n:], axis=1)))


def knob_equivalent_distance(dist: float, curve: dict, knob: str | None = None) -> float:
    """Place a real-versus-ours distance on the machine's own knob yardstick:
    how far its knob would have to move for it to look this different from
    itself. nan means further than the knob can travel -- which for a 0..10
    dial is the strongest statement this corpus can make."""
    pts = sorted((d, v) for (k, d), v in curve.items() if knob is None or k == knob)
    if len(pts) < 2:
        return float("nan")
    ds = [p[0] for p in pts]
    vs = [p[1] for p in pts]
    if dist >= max(vs):
        return float("nan")
    if dist <= min(vs):
        return float(ds[int(np.argmin(vs))])
    order = np.argsort(vs)
    return float(np.interp(dist, np.array(vs)[order], np.array(ds)[order]))


def real_vs_real_random(refdir, cache, voice="BD", n_iter=40, level_match=True,
                        floor_clamp=True, seed=0, extra=False):
    """NEGATIVE CONTROL on real structure: split one voice's real clips into
    two arbitrary pseudo-classes and classify. Must sit at chance. If it does
    not, the pipeline is manufacturing separation out of nothing."""
    rng = np.random.default_rng(seed)
    base = ref_clips(refdir, voices=[voice])
    accs = []
    for _ in range(n_iter):
        p = rng.permutation(len(base))
        clips = [Clip(c.voice, c.knobs, "real" if p[i] < len(base) // 2 else "realB",
                      c.path, rec=f"r{i}") for i, c in enumerate(base)]
        X, _ = build_matrix(clips, None, refdir, level_match, floor_clamp, cache, extra)
        fit = np.array([not c.is_test for c in clips])
        if fit.sum() < 4 or (~fit).sum() < 2:
            continue
        X = zscore_per_voice(X, [Clip("X", c.knobs, c.side) for c in clips],
                             np.ones(len(clips), bool))
        r = discriminate(clips, X, fit, ~fit, seed)
        accs.append(summarise(r["y"], r["pred"])["acc"])
    return dict(mean=float(np.mean(accs)), p05=float(np.percentile(accs, 5)),
                p95=float(np.percentile(accs, 95)), n_iter=len(accs))


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------
MIN_N_FOR_VERDICT = 8       # below this, no number of correct calls can exclude 0.5

# The only four things this test is allowed to say. Near-chance classifier
# accuracy is an automated SCREENING result about these evaluators on this
# corpus. It is not a claim about human indistinguishability: that is a
# different experiment, with listeners, trials and controls we have not run.
V_DEFECT = "known defect remains"
V_IMPROVED = "improved on the tested comparisons"
V_NOSEP = "no separation detected by the tested evaluators"
V_NONE = "no verdict"


def verdict(s: dict, controls_ok: bool, floor_acc: float | None = None,
            improved_over: float | None = None) -> str:
    """`floor_acc` is the real-vs-real separation at the smallest knob step
    the corpus contains -- an upper bound on the take-to-take floor, which
    this corpus cannot give. `improved_over` is the same voice's accuracy for
    the arm this one is meant to improve on."""
    if not controls_ok:
        return f"{V_NONE} (discriminator failed its positive controls)"
    if s["n"] < MIN_N_FOR_VERDICT:
        return f"{V_NONE} (underpowered: {s['n']} held-out recordings; " \
               f"{MIN_N_FOR_VERDICT} is the minimum that can exclude chance)"
    if s["ci"][0] > 0.5:
        bits = [V_DEFECT]
        if floor_acc is not None and s["acc"] <= floor_acc + 1e-9:
            bits.append(f"but no more separable than the machine from itself "
                        f"at the smallest knob step ({floor_acc:.2f})")
        if improved_over is not None and s["acc"] < improved_over - 1e-9:
            bits.append(f"{V_IMPROVED} ({improved_over:.2f} -> {s['acc']:.2f})")
        return "; ".join(bits)
    if s["upper"] < EQUIV_MARGIN:
        return f"{V_NOSEP} (one-sided upper bound {s['upper']:.2f} < {EQUIV_MARGIN:.2f})"
    return f"{V_NONE} (screening inconclusive: one-sided upper bound {s['upper']:.2f}; " \
           f"n={n_for_margin()} recordings needed to bound at {EQUIV_MARGIN:.2f})"


def balanced_accuracy(y, pred) -> float:
    accs = [float((pred[y == c] == c).mean()) for c in np.unique(y) if (y == c).sum()]
    return float(np.mean(accs)) if accs else float("nan")


def group_bootstrap_ci(y, pred, groups, n_iter: int = 2000, seed: int = 0) -> tuple:
    """Uncertainty over SETTINGS, not over generated comparisons: resample
    whole groups. A corpus of 116 files does not become 500 independent
    observations by pairing it with itself."""
    rng = np.random.default_rng(seed)
    g = np.asarray(groups)
    uniq = np.unique(g)
    if len(uniq) < 3:
        return clopper_pearson(int((y == pred).sum()), len(y))
    accs = []
    for _ in range(n_iter):
        pick = rng.choice(uniq, len(uniq), replace=True)
        idx = np.concatenate([np.where(g == u)[0] for u in pick])
        accs.append(balanced_accuracy(y[idx], pred[idx]))
    return (float(np.percentile(accs, 2.5)), float(np.percentile(accs, 97.5)))


def frechet(A: np.ndarray, B: np.ndarray, n_pc: int = 8, basis=None) -> float:
    """Frechet distance between Gaussians fitted to two feature sets -- the
    construct behind FAD, computed here on this module's own log-mel/MFCC
    representation rather than a VGGish/CLAP embedding. It is reported ONLY
    comparatively, with encoder, sample counts, voice balance and
    preprocessing identical across every row; there is no absolute scale and
    none is claimed. Labelled FD-mel, never FAD, because it is not FAD."""
    from scipy.linalg import sqrtm
    if len(A) < 2 or len(B) < 2:
        return float("nan")
    # 320 features against a few dozen clips is rank-deficient, and an
    # unregularised Frechet distance on a singular covariance is noise: the
    # first version of this reported the reference set as further from itself
    # than from our synth. Project onto a fixed low-dimensional basis, shared
    # by every row, before estimating any covariance.
    if n_pc:
        M = np.vstack([A, B]) if basis is None else basis
        mu = M.mean(0)
        _, _, Vt = np.linalg.svd(M - mu, full_matrices=False)
        P = Vt[:min(n_pc, len(M) - 1, M.shape[1])].T
        A, B = (A - mu) @ P, (B - mu) @ P
    mu1, mu2 = A.mean(0), B.mean(0)
    s1 = np.cov(A, rowvar=False) + 1e-6 * np.eye(A.shape[1])
    s2 = np.cov(B, rowvar=False) + 1e-6 * np.eye(B.shape[1])
    covmean = sqrtm(s1 @ s2)
    if np.iscomplexobj(covmean):
        covmean = covmean.real
    return float(((mu1 - mu2) ** 2).sum() + np.trace(s1 + s2 - 2 * covmean))


def corpus_is_level_normalised(refdir: str) -> dict:
    """Point 5 of the review, answered from the files rather than assumed.
    If the pack was peak-limited, level differences carry nothing about the
    machine's accent behaviour and the unmatched-level column is void."""
    import glob
    pks = []
    for f in sorted(glob.glob(os.path.join(refdir, "*8", "*.WAV"))):
        x, _ = read_wav(f)
        pks.append(float(np.abs(x).max()))
    pks = np.asarray(pks)
    at_ceiling = int((pks > 0.99 * pks.max()).sum())
    return dict(n=len(pks), max_dbfs=20 * math.log10(pks.max()),
                min_dbfs=20 * math.log10(pks.min()),
                range_db=20 * math.log10(pks.max() / pks.min()),
                at_ceiling=at_ceiling,
                normalised=at_ceiling >= 0.2 * len(pks))


def counts(clips) -> dict:
    """Unique source recordings and settings, reported separately from the
    number of generated comparisons."""
    return dict(recordings=len({c.rec for c in clips}),
                settings=len({(c.voice, c.knobs) for c in clips}),
                voices=len({c.voice for c in clips}))


# ===========================================================================
# pytest: these check the machinery, not the synth. They must not need the
# reference set, so they run on signals built here.
# ===========================================================================
def _tone(f, sr=44100, dur=WINDOW_S + 0.05, tau=0.05, seed=0):
    t = np.arange(int(dur * sr)) / sr
    rng = np.random.default_rng(seed)
    return np.sin(2 * np.pi * f * t) * np.exp(-t / tau) + 1e-4 * rng.standard_normal(len(t))


def test_window_is_shorter_than_every_reference_file():
    """The decay knob sets the file length in the Fischer set; the window must
    fit inside the shortest file that exists, or length leaks the knob."""
    assert WINDOW_S < 0.25


def test_features_are_rate_independent():
    """A 44.1 k reference and a 48 k render must land in the same mel bands,
    or the sample rate itself is the discriminator."""
    a = features(condition(_tone(440, 44100), 44100), 44100)[0]
    b = features(condition(_tone(440, 48000), 48000), 48000)[0]
    mel = np.array([n.startswith("mel") for n in features(condition(_tone(440), 44100), 44100)[1]])
    assert np.abs(a[mel] - b[mel]).max() < 0.35, np.abs(a[mel] - b[mel]).max()


def test_conditioning_removes_length_and_dc():
    sr = 44100
    short, long = _tone(200, sr, 0.30, 0.01), _tone(200, sr, 1.50, 0.40)
    assert len(condition(short, sr)) == len(condition(long, sr))
    # the refs carry about 1 LSB of converter DC; our renders carry exactly
    # none, so it must not survive conditioning as a cue
    clean, dc = _tone(200, sr), _tone(200, sr) + 3e-5
    a = condition(clean, sr, level_match=False)
    b = condition(dc, sr, level_match=False)
    assert np.abs(a - b).max() < 3e-6, np.abs(a - b).max()
    # and the high-pass must not ring inside the window: a flat-DC-free tone
    # must come through with its attack intact
    t = condition(_tone(200, sr, tau=10.0), sr, level_match=False)
    assert abs(np.abs(t[:200]).max() / np.abs(t[-200:]).max() - 1.0) < 0.25


def test_conditioning_does_not_answer_an_impulse_with_a_pedestal():
    """CONTROL for #161, and the signal that found it.

    `sosfiltfilt` pads 6 samples. The 20 Hz high-pass has a pole at 0.99715
    (44.1 k) / 0.99739 (48 k) -- about 2,400 samples to settle to 1e-3. Six
    samples against that is not a boundary condition, it is an initial
    condition chosen at random, and on a unit impulse at index 0 the acausal
    filter answers with a FULL-SCALE NEGATIVE PEDESTAL: y[1] = -0.997 and the
    first 30 ms integrating to -342 (44.1 k) / -373 (48 k), against a causal
    filter's +0.02.

    The pedestal lands on the attack, which is where the discrimination lives,
    and `condition()` feeds every one of the 320 feature columns."""
    for sr in (44100, 48000):
        x = np.zeros(int(0.30 * sr))
        x[0] = 1.0
        y = condition(x, sr, level_match=False)
        n30 = int(0.030 * sr)
        assert abs(y[1]) < 0.05, f"sr={sr}: second sample {y[1]:.3f} is a pedestal"
        assert abs(float(y[:n30].sum())) < 1.0, \
            f"sr={sr}: first 30 ms integrates to {float(y[:n30].sum()):.1f}, not ~0"
        # and the impulse itself must survive: a high-pass keeps an impulse
        assert y[0] > 0.9, f"sr={sr}: the impulse itself was eaten ({y[0]:.3f})"


def test_the_published_boundary_is_still_reachable_and_still_wrong():
    """`legacy=True` must keep reproducing the defect exactly, or #148's
    numbers stop being re-derivable and the delta becomes an assertion instead
    of a measurement. This is the control that keeps its own teeth."""
    sr = 44100
    x = np.zeros(int(0.30 * sr))
    x[0] = 1.0
    y = condition(x, sr, level_match=False, legacy=True)
    assert y[1] < -0.9, y[1]
    assert float(y[:int(0.030 * sr)].sum()) < -300.0, float(y[:int(0.030 * sr)].sum())
    # and it must still be lead-dependent, which is the other half of #161
    hit = _tone(200, sr, 0.40, 0.05)
    lead = np.concatenate([np.zeros(int(0.010 * sr)), hit])
    d = np.abs(features(condition(hit, sr, legacy=True), sr)[0]
               - features(condition(lead, sr, legacy=True), sr)[0]).max()
    assert d > 1e-3, d


def test_a_measurement_does_not_depend_on_where_the_record_begins():
    """#103's invariance, the one that catches this class without knowing the
    mechanism: prepend silence and the answer must not move.

    This is the asymmetry #160's F2 named -- `_render_raw` pre-trims OUR clip
    at its onset and `read_wav` does not trim the machine's -- expressed as a
    property of the measurement instead of a rule about the callers. Once
    `condition()` cuts at the onset BEFORE it filters, nothing ahead of the
    onset can reach the answer, so the two sides may arrive differently
    prepared and still be compared fairly."""
    sr = 44100
    hit = _tone(200, sr, 0.40, 0.05)
    for lead_ms in (1.0, 10.0, 50.0):
        lead = np.concatenate([np.zeros(int(lead_ms * 1e-3 * sr)), hit])
        a = features(condition(hit, sr), sr)[0]
        b = features(condition(lead, sr), sr)[0]
        assert np.abs(a - b).max() < 1e-9, \
            f"{lead_ms} ms of silence moved the features by {np.abs(a - b).max():.3g}"
    # and the machine's lead is not silence: it carries about 1 LSB of
    # converter DC and a -76 dBFS floor, which must not reach the answer either
    rng = np.random.default_rng(11)
    n = int(0.010 * sr)
    dirty = np.concatenate([3e-5 + 1.6e-4 * rng.standard_normal(n), hit])
    c = features(condition(dirty, sr), sr)[0]
    a = features(condition(hit, sr), sr)[0]
    assert np.abs(a - c).max() < 1e-9, \
        f"a converter's lead moved the features by {np.abs(a - c).max():.3g}"


def test_the_two_sides_arrive_differently_prepared_and_it_is_recorded():
    """#163 rule 1: a paired comparison asserts its inputs are in the same
    preparation state, or records that they are not.

    Ours and the machine's genuinely are NOT in the same state -- our render
    has no lead to give, so the asymmetry cannot be removed by equalising it.
    What can be done is to record it and to prove it no longer reaches the
    measurement, which is what the two assertions below are."""
    sr = 44100
    hit = _tone(200, sr, 0.40, 0.05)
    trimmed = hit[onset(hit):]
    untrimmed = np.concatenate([np.zeros(int(0.010 * sr)), hit])
    a, b = preparation_state(trimmed, sr), preparation_state(untrimmed, sr)
    assert a["lead_ms"] != b["lead_ms"], "the fixture no longer poses the problem"
    try:
        assert_same_preparation(a, b)
    except AssertionError as e:
        assert "lead_ms" in str(e), str(e)
    else:
        raise AssertionError("differing lead was not reported")
    assert np.abs(condition(trimmed, sr) - condition(untrimmed, sr)).max() < 1e-12


def test_floor_clamp_hides_a_noise_floor_difference():
    """The refs carry a 1994 converter's hiss and our renders are digitally
    silent. With the clamp on, a -76 dBFS floor must not change the features."""
    sr, rng = 44100, np.random.default_rng(3)
    clean = _tone(300, sr, tau=0.01)
    hissy = clean + 10 ** (-76 / 20.0) * rng.standard_normal(len(clean))
    on = np.abs(features(condition(clean, sr), sr, True)[0]
                - features(condition(hissy, sr), sr, True)[0]).max()
    off = np.abs(features(condition(clean, sr), sr, False)[0]
                 - features(condition(hissy, sr), sr, False)[0]).max()
    assert on < off / 3, (on, off)


def test_level_matching_removes_a_pure_gain():
    sr = 44100
    a = features(condition(_tone(300, sr), sr, True), sr)[0]
    b = features(condition(_tone(300, sr) * 0.1, sr, True), sr)[0]
    assert np.abs(a - b).max() < 1e-6


def test_split_disjointness_is_enforced():
    a = Clip("BD", (0.0, 0.0), "real")
    b = Clip("BD", (2.5, 0.0), "real")
    assert assert_split_disjoint([a], [b])
    try:
        assert_split_disjoint([a], [a])
    except AssertionError:
        return
    raise AssertionError("a recording on both sides of the split was not caught")


def test_test_knobs_are_never_fit_knobs():
    assert not set(FIT_KNOBS) & set(TEST_KNOBS)
    assert Clip("BD", (2.5, 0.0), "real").is_test
    assert Clip("BD", (0.0, 5.0), "real").experiment == EXP_SOUND_MATCHING
    assert Clip("BD", (7.5, 5.0), "real").experiment == EXP_EMULATION
    assert not Clip("CB", (), "real").is_test     # no knob -> no emulation verdict


def test_a_single_tau_on_two_modes_reads_a_balance_change_as_a_decay_change():
    """GROUND TRUTH for the SD TONE law, and the control for the one it
    replaced. Two damped sinusoids at 173 and 336 Hz whose decays are FIXED at
    30 and 9.7 ms -- the machine's, fitted separately -- and whose amplitude
    ratio is set to the machine's own 0.48 / 1.43 / 10.1 at TONE 0 / 5 / 10.

    Nothing in this construction decays differently at the three settings. Fit
    ONE exponential to the sum and it reports 30.1 / 28.8 / 11.4 ms, which is
    the 28.5 / 27.4 / 13.6 ms the withdrawn law read off the machine and wrote
    into BOTH of our body modes. So that law's whole content is reproduced by
    a balance change with fixed decays, and `partial_ratio` recovers the
    balance monotonically over the same sweep.

    Constructed, so both answers are known in advance. If `measure_tau` ever
    stops collapsing here, the diagnosis in contract 17.24 is wrong."""
    sr = 44100
    t = np.arange(int(0.30 * sr)) / sr
    taus, ratios = [], []
    for a_hi in (0.48, 1.43, 10.1):                  # the machine at TONE 0 / 5 / 10
        x = (np.exp(-t / 0.030) * np.cos(2 * np.pi * 173.0 * t)
             + a_hi * np.exp(-t / 0.0097) * np.cos(2 * np.pi * 336.0 * t))
        taus.append(measure_tau(x, sr))
        ratios.append(partial_ratio(x, sr))
    assert taus[0] > taus[-1] * 2.0, (
        f"the single-tau fit did not collapse: {[round(v * 1e3, 1) for v in taus]} ms -- "
        f"then it is not the artefact the SD TONE law was withdrawn for")
    assert abs(taus[0] / 0.030 - 1) < 0.10, (
        f"with the upper partial low the fit should read the lower mode's own 30 ms, "
        f"not {taus[0] * 1e3:.1f} ms")
    # the machine measures 28.5 / 27.4 / 13.6; the construction must land near it
    for got, machine in zip(taus, (0.0285, 0.0274, 0.0136)):
        assert abs(got - machine) < 0.005, \
            f"construction {got*1e3:.1f} ms against the machine's {machine*1e3:.1f} ms"
    assert ratios == sorted(ratios) and ratios[-1] / ratios[0] > 100, \
        f"partial_ratio did not track the balance: {[round(v, 4) for v in ratios]}"


def test_law_interpolates_its_three_fit_points():
    law = fit_quad(FIT_KNOBS, [17.5e-3, 241.2e-3, 540.8e-3], "log")
    for k, y in zip(FIT_KNOBS, [17.5e-3, 241.2e-3, 540.8e-3]):
        assert abs(law(k) - y) / y < 1e-6
    assert 17.5e-3 < law(2.5) < 541e-3


def test_upper_bound_calls_a_coin_flip_what_it_is():
    """10 of 20 is not 'indistinguishable'; its one-sided bound is near 0.7."""
    assert 0.65 < upper_bound_one_sided(10, 20) < 0.72
    assert clopper_pearson(20, 20)[0] > 0.8
    assert upper_bound_one_sided(n_for_margin() // 2, n_for_margin()) < EQUIV_MARGIN


def test_verdict_refuses_when_underpowered_or_controls_fail():
    perfect = summarise(np.zeros(4), np.zeros(4))
    assert "underpowered" in verdict(perfect, True)
    big = summarise(np.zeros(40), np.zeros(40))
    assert verdict(big, True).startswith(V_DEFECT)
    assert verdict(big, True, floor_acc=1.0).endswith("(1.00)")
    assert V_IMPROVED in verdict(summarise(np.r_[np.zeros(30), np.ones(10)],
                                           np.zeros(40)), True, improved_over=1.0)
    assert V_NONE in verdict(big, False)
    coin = summarise(np.r_[np.zeros(10), np.ones(10)], np.zeros(20))
    assert verdict(coin, True).startswith(V_NONE)          # screening inconclusive
    # a real "no separation" needs the one-sided bound under the margin
    n = n_for_margin()
    tight = summarise(np.r_[np.zeros(n // 2), np.ones(n // 2)],
                      np.r_[np.zeros(n // 2), np.ones(n // 2)] * 0 + np.r_[np.zeros(n // 2), np.ones(n // 2)])
    assert V_DEFECT in verdict(tight, True)


def test_group_importance_names_are_actionable():
    names = features(condition(_tone(300), 44100), 44100)[1]
    g = feature_groups(names)
    assert len(g) == (len(FEATURE_BANDS) + 1) * N_SEG
    assert sum(len(v) for v in g.values()) == len(names)


# ---------------------------------------------------------------------------
# The sixteen-sound extension. Added 2026-09-18 with the re-run the scorecard
# asks for; these are the checks that stop the extension from quietly
# changing what the eight-voice study measured.
# ---------------------------------------------------------------------------
def test_the_eight_default_sounds_are_untouched_by_the_preset():
    """`kit_at` now builds from `kit_with_sounds(voice)` instead of
    `kit_808()`. For the eight sounds the kit already loads that must be the
    SAME register image, or every arm this study has ever published would
    have silently moved under the extension."""
    import drums_fx as dx
    base = {a: v for a, v in dx.kit_808()}
    for v in ("BD", "SD", "LT", "HT", "CH", "OH", "CP", "CB"):
        img = dict(dx.kit_with_sounds(v))
        diff = {a: (base.get(a), img[a]) for a in img if base.get(a) != img[a]}
        assert not diff, f"{v}: preset changed {len(diff)} registers: {list(diff)[:4]}"
        assert set(img) == set(base), v


def test_the_stop_struck_is_the_sounds_own_circuit():
    """A STOP is a circuit and a SOUND is a position of it. The old `render`
    did `STOP_NAMES.index(voice)`, which for the eight it knew about happened
    to be right and for the eight it did not know about is wrong in two
    different ways: "CL" would index stop 10 (the CL circuit, right by luck)
    but "LC" would index nothing and "MA" would raise. Each of the five
    shared circuits must give two AUDIBLY different renders."""
    import drums_fx as dx
    laws = {}
    for a, b in dx.PAIRS:
        xa, _ = render(a, (), laws) if not KNOB_NAMES[a] else render(a, (5.0,) * len(KNOB_NAMES[a]), _tom_laws(a))
        xb, _ = render(b, (), laws) if not KNOB_NAMES[b] else render(b, (5.0,) * len(KNOB_NAMES[b]), _tom_laws(b))
        n = min(len(xa), len(xb))
        assert n > 100 and float(np.abs(xa[:n]).max()) > 1e-4, a
        d = float(np.sqrt(((xa[:n] - xb[:n]) ** 2).mean()))
        r = float(np.sqrt((xa[:n] ** 2).mean()))
        assert d > 0.1 * r, f"{a} and {b} render the same signal (rms diff {d:.2e} vs {r:.2e})"


def _tom_laws(voice):
    """A stand-in law set for the render tests: the identity at knob 5.0, so
    these tests need no corpus."""
    import drums_fx as dx
    if voice == "CY":
        return {"CY.tone_ratio": fit_quad(FIT_KNOBS, [0.2, 0.3, 0.45], "log"),
                "CY.decay_tau": fit_quad(FIT_KNOBS, [0.16, 0.39, 0.51], "log")}
    if voice in TUNING_FMAX:
        f0 = dx.TOM_PRESET[voice][0]
        return {f"{voice}.tuning_f0": fit_quad(FIT_KNOBS, [f0 * 0.9, f0, f0 * 1.1], "log")}
    if voice == "BD":
        return {"BD.decay_tau": fit_quad(FIT_KNOBS, [0.018, 0.24, 0.54], "log"),
                "BD.tone_click": fit_quad(FIT_KNOBS, [0.013, 0.017, 0.019], "logit")}
    if voice == "SD":
        return {"SD.tone_ratio": fit_quad(FIT_KNOBS, [0.0015, 0.084, 2.2], "log"),
                "SD.snappy_share": fit_quad(FIT_KNOBS, [1e-4, 0.52, 0.93], "logit")}
    if voice == "OH":
        return {"OH.decay_tau": fit_quad(FIT_KNOBS, [0.023, 0.19, 0.22], "log")}
    return {}


def test_every_one_of_the_sixteen_sounds_renders_and_is_not_silent():
    """A silent render is the failure this repository has shipped most often
    (`verify_voice` on a stub, the all-X ladder). A voice that renders
    silence would be reported as maximally separable from the machine, which
    is a defect wearing a result's clothes."""
    import drums_fx as dx
    for v in dx.SOUND_NAMES:
        knobs = (5.0,) * len(KNOB_NAMES[v])
        x, sr = render(v, knobs, _tom_laws(v))
        pk = float(np.abs(x).max())
        assert np.all(np.isfinite(x)), v
        assert pk > 1e-4, f"{v} rendered silence (peak {pk:.2e})"


def test_a_sound_with_no_knob_can_produce_no_knob_equivalent():
    """REFUSE rather than report. The knob-equivalent is a distance read off
    the machine's OWN knob; six of the sixteen have no knob, so the corpus
    holds one recording of each, there is no held-out setting, there is no
    yardstick, and the only honest output is a refusal -- never an
    interpolation onto a yardstick that does not exist."""
    assert set(NO_KNOB) == {"CH", "CP", "CB", "RS", "CL", "MA"}, NO_KNOB
    for v in NO_KNOB:
        assert KNOB_NAMES[v] == (), v
        assert not Clip(v, (), "real").is_test
        assert math.isnan(knob_equivalent_distance(1.0, {}))


def test_the_sixteen_sound_split_holds_out_the_same_settings_as_the_eight():
    """The eight-voice study's split must survive inside the sixteen-voice
    one: same voices, same held-out settings, or the before/after table
    compares two different experiments."""
    import drums_fx as dx
    assert set(ALL_REF) == set(dx.SOUND_NAMES)
    for v, (d, pre, nk) in VOICE_REF.items():
        assert ALL_REF[v] == (d, pre, nk), v
    n_knobbed = sum(1 for v in ALL_REF if ALL_REF[v][2] > 0)
    assert n_knobbed == 10, n_knobbed


def test_the_extra_columns_append_and_never_reorder_the_original_320():
    """The extra feature set is an APPENDIX. If it ever inserted a column the
    published importance attributions would silently point at the wrong
    feature."""
    x = np.zeros(int(WINDOW_S * 44100))
    v0, n0 = features(x, 44100)
    v1, n1 = features(x, 44100, extra=True)
    assert n1[:len(n0)] == n0 and len(n1) > len(n0)
    assert np.allclose(v1[:len(v0)], v0)
    g = feature_groups(n1)
    assert sum(len(ix) for ix in g.values()) == len(n1)
    assert any(k.startswith("mpd.") for k in g) and any(k.startswith("cqt.") for k in g)
    assert "jit.stability" in g and "jit.period" in g and "ms.scale-difference" in g


def test_the_cross_validation_grouping_does_not_move_between_processes():
    """The defect that made this study's accuracies unreproducible: `hash()`
    on a str is salted per process, so the grouped inner CV saw different
    folds every run and chose a different C. The group id must be a function
    of the recording name and nothing else.

    The red form of this test is to put `hash(rec) % (1 << 31)` back and run
    pytest twice with different PYTHONHASHSEED; the two ids differ (961059919
    / 524828002 / 1741712856 for one recording at seeds 1/2/3)."""
    known = {"real:BD:(0.0, 0.0)": stable_group_id("real:BD:(0.0, 0.0)"),
             "ours:SD:(2.5, 7.5)": stable_group_id("ours:SD:(2.5, 7.5)")}
    assert known["real:BD:(0.0, 0.0)"] == 0x01ffdc7c, hex(known["real:BD:(0.0, 0.0)"])
    assert known["ours:SD:(2.5, 7.5)"] == stable_group_id("ours:SD:(2.5, 7.5)")
    assert len({stable_group_id(f"r{i}") for i in range(500)}) == 500


def test_the_frozen_ruler_makes_the_two_distances_commensurate():
    """The knob-equivalent reads one distance off another. Unfrozen, the two
    are z-scored on different populations, so the reading is a ratio of two
    different units. This checks that a frozen scale is applied to both and
    that a pure rescaling of the feature space then leaves the
    knob-equivalent unchanged -- which is what "a ruler" means."""
    rng = np.random.default_rng(0)
    base = rng.standard_normal((12, 6))
    mu, sd = base.mean(0), base.std(0)
    a, b = base[:6], base[6:]
    z1 = _zspace(None, np.vstack([a, b]), (mu, sd))
    z2 = _zspace(None, np.vstack([a * 7.0, b * 7.0]), (mu * 7.0, sd * 7.0))
    assert np.allclose(z1, z2)
    # and without a frozen scale the same rescaling is invisible, which is the
    # property that hides the mismatch rather than the property we want
    assert np.allclose(_zspace(None, np.vstack([a, b])),
                       _zspace(None, np.vstack([a * 7.0, b * 7.0])))


# ---- #413: the CY knob branch is the shipped constructor, not a fitted law ----
def test_cy_knob_render_is_delegated_and_reads_no_fitted_law():
    """`kit_at("CY", ...)` used to solve TONE for the reference's own band ratio
    and DECAY through a clamped power law (7.5 and 10.0 rendered bit-identical).
    Pass NO laws: if the branch still read `CY.tone_ratio` / `CY.decay_tau` this
    would KeyError, and the anchor must be the shipped image exactly."""
    import cymbal_candidate as cc
    import drums_fx as dx
    assert not hasattr(sys.modules[__name__], "_cy_hi_amp")
    assert not hasattr(sys.modules[__name__], "_cy_decay_scale")
    assert dict(kit_at("CY", (5.0, 2.5), {})) == dict(dx.kit_with_sounds("CY"))
    img75, img10 = dict(kit_at("CY", (5.0, 7.5), {})), dict(kit_at("CY", (5.0, 10.0), {}))
    assert img75 != img10                                  # the historical saturation
    assert img10 == dict(cc.shipped_knob_kit(5.0, 10.0))   # one constructor, not two
