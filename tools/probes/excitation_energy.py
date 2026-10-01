#!/usr/bin/env python3
"""Where, in time and in band, our excitation differs from the machine -- and
what the instrument that said so was actually measuring.

    .venv/bin/python tools/probes/excitation_energy.py --floors
    .venv/bin/python tools/probes/excitation_energy.py --map
    .venv/bin/python tools/probes/excitation_energy.py --attribute
    .venv/bin/python -m pytest tools/probes/excitation_energy.py -q

#152 reads `docs/discrimination-trajectory.txt` as "all sixteen voices carry
broadband energy in the first 30 ms that the machine does not have". This file
re-derives that map with the floor of every row stated, because two of the
three things it rests on turn out to be the instrument rather than the design.

THE THREE FLOORS, each measured here and not assumed
----------------------------------------------------

**F1 -- the conditioning creates the thing it is used to measure.**
`test_discrimination.condition()` high-passes at 20 Hz with `sosfiltfilt`.
A zero-phase filter has no causal excuse for an edge, and scipy pads it with
`padlen = 3*(2*len(sos)+1-1) = 6` samples for a filter whose pole is 0.99739
-- a settling time of about 1900 samples. Fed a unit impulse at index 0 it
answers with a near-full-scale NEGATIVE PEDESTAL: the first output samples are
-0.997, -0.995, -0.992 ..., and the first 30 ms integrate to -373 instead of 0.
That pedestal lands exactly on window 0 of every clip, and window 0 is where
#152's entire finding lives.  `condition()`'s own docstring warns about this
("a 20 Hz filter settles over ~50 ms ... right on top of the attack") and then
reaches for the acausal filter, which has the same transient at index 0.

**F2 -- the two sides do not enter the conditioning the same way.**
`_render_raw` returns `out[onset(out):]`: OUR clip is pre-trimmed to its onset
before `condition()` ever sees it. `read_wav` does not trim the machine's. The
pedestal of F1 is therefore placed differently on the two sides, and applying
the same pre-trim to the machine moves its own window-0 sub-120 Hz reading by
up to +32 dB.  "Applied identically to both sides" is true of the code and
false of the measurement.

**F3 -- ten of the fifty-two bands cannot hold a measurement at all.**
The map's windows are 30 ms, so the analysis bin is 33.3 Hz wide (at BOTH
44.1 and 48 kHz -- 0.24 s / 8 is a whole number of samples at each, which is
why the rate-independence test passes). Bands 0-15 are all NARROWER than one
bin, and bands 0,1,2,3,5,6,8,9,11,14 contain NO bin centre at all: they are
pinned to the -75 dB clamp on both sides by construction. The bands that do
appear in the report -- 67, 95, 135, 170, 190, 240 Hz -- are each ONE FFT bin
carrying up to +6.3 dB of band-width attribution bias, and "67 Hz" means bin 2
of a 30 ms Hann window, whose main lobe reaches DC.

WHAT SURVIVES
-------------
Run with `--map`. The excess is real on a named subset and is NOT broadband:
it is a near-DC onset pedestal, and `--attribute` names the path that emits it
by muting one register path at a time.

Provenance is printed by every mode: commit, dirty flag, corpus hash.
"""
from __future__ import annotations

import argparse
import hashlib
import pathlib
import subprocess
import sys

import numpy as np
from scipy.signal import butter, sosfilt, sosfilt_zi, sosfiltfilt

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "model"))

import discrimination_features as dfx            # noqa: E402
import discrimination_trajectory as dtj          # noqa: E402
import drums_fx as dx                            # noqa: E402
import test_discrimination as td                 # noqa: E402

N_WIN = dtj.N_WIN
HPF_HZ = td.HPF_HZ
FLOOR_DB = dtj.FLOOR_DB
REFS_DEFAULT = "/tmp/tr808-ref"

# A cell is only allowed to carry a claim when the disagreement clears every
# applicable floor by this much. Chosen before the map was read.
MARGIN_DB = 6.0


# ===========================================================================
# Provenance
# ===========================================================================
def provenance(refdir: str) -> str:
    def git(*a):
        try:
            return subprocess.run(["git", "-C", str(ROOT), *a], capture_output=True,
                                  text=True, timeout=20).stdout.strip()
        except Exception:                                   # pragma: no cover
            return "?"
    commit = git("rev-parse", "HEAD")
    dirty = bool(git("status", "--porcelain"))
    h = hashlib.sha256()
    n = 0
    for p in sorted(pathlib.Path(refdir).rglob("*.WAV")):
        h.update(p.name.encode())
        h.update(p.read_bytes())
        n += 1
    return (f"commit {commit[:12]}{' DIRTY' if dirty else ''}  "
            f"corpus {refdir} {n} files sha256 {h.hexdigest()[:16]}  "
            f"argv {' '.join(sys.argv[1:]) or '(none)'}")


# ===========================================================================
# F3: what a band can hold at this resolution. Structural, no data needed.
# ===========================================================================
def bin_support(sr: int, n_total: int, n_win: int = N_WIN, edges=None):
    """(bins per band, band width Hz, bin width Hz) for ONE window of the map.

    A band with zero bins is not a low reading, it is NO reading: `trajectory`
    sums an empty mask to 0.0 and clamps to -75 dB on both sides. A band with
    one bin reports that bin's whole 33 Hz of power as if it were the band's,
    which is the +10log10(bin_bw/band_bw) attribution bias."""
    e = dfx.cqt_edges() if edges is None else np.asarray(edges, float)
    b = np.linspace(0, n_total, n_win + 1).astype(int)
    n_seg = b[1] - b[0]
    f = np.fft.rfftfreq(n_seg, 1.0 / sr)
    counts = np.array([int(((f >= e[k]) & (f < e[k + 1])).sum()) for k in range(len(e) - 1)])
    return counts, np.diff(e), float(sr) / n_seg


def attribution_bias_db(counts, band_bw, bin_bw):
    """+dB a band over-reports because its one bin is wider than it is."""
    with np.errstate(divide="ignore"):
        return np.where(counts > 0, 10.0 * np.log10(np.maximum(counts, 1) * bin_bw / band_bw),
                        np.nan)


# ===========================================================================
# F1/F2: conditioning, the study's and a causal one
# ===========================================================================
def _hpf_sos(sr):
    return butter(1, HPF_HZ / (sr / 2.0), btype="highpass", output="sos")


LEADIN_S = 0.010        # the declared lead-in the initial condition is read from


def condition_meansub(x, sr, level_match: bool = True, pretrim: bool = True):
    """**THE DEFECT, KEPT AS A CONTROL. Do not measure with this.**

    This is `condition_causal` as it was shipped up to #165: it subtracts the
    WHOLE CLIP's mean before filtering, so the conditioned prefix depends on
    samples that arrive after it. Appending 700 ms of silence to a 400 ms clip
    moves the first 240 ms by 0.27 % of peak on a DC-free decaying sine, 9.58 %
    on a rectified one and 6.42 % on a swing-VCA one -- i.e. the error scales
    with the clip's DC and is therefore LARGEST on exactly the signals this
    probe exists to measure. A probe investigating unmodelled DC blocking that
    applies its own clip-length-dependent DC removal is circular.

    Kept, not deleted: `test_the_old_conditioning_let_the_future_move_the_past`
    is the injected-bug control for the fix, and a record of a number that
    looked fine and was wrong is worth more than one that was right first
    time (CLAUDE.md)."""
    x = np.asarray(x, dtype=float)
    x = x - x.mean()
    if pretrim:
        x = x[td.onset(x):]
    x = sosfilt(_hpf_sos(sr), x)
    return _cut(x, sr, level_match)


def condition_causal(x, sr, level_match: bool = True, pretrim: bool = True,
                     leadin_s: float = LEADIN_S):
    """`td.condition` with the acausal filter replaced by a causal one, the
    pre-trim made explicit instead of inherited from whoever produced the
    array, and **the initial condition DECLARED rather than computed from the
    future.**

    A causal 20 Hz high-pass IS what an AC-coupled output does to a signal
    that begins at t = 0, so its step response is physics and not an artefact.
    `sosfiltfilt`'s pedestal is neither -- and neither is a whole-clip mean
    subtraction, which is what this function used to do (`condition_meansub`).

    THE INITIAL CONDITION. The coupling is declared to be in steady state for
    the DC level of a lead-in of `leadin_s` that ENDS where the analysis
    begins: `zi = sosfilt_zi(sos) * dc0`. For a constant input equal to `dc0`
    a settled high-pass emits exactly zero, so a converter's standing DC
    offset (the refs carry about 1 LSB) is removed as completely as the mean
    subtraction removed it -- without any sample after the window entering the
    answer. Where there is no lead-in to read -- the Fischer files begin AT
    their onset -- the coupling is declared AT REST (`dc0 = 0`), and the step
    it then answers with is the physics of a signal that begins at t = 0. The
    filter state runs CONTINUOUSLY from there; nothing is refiltered.

    The property this buys, and it is tested:
    **no sample after the window can change the window.**"""
    x = np.asarray(x, dtype=float)
    sos = _hpf_sos(sr)
    i0 = td.onset(x) if pretrim else 0
    n_lead = int(round(leadin_s * sr))
    lead = x[max(0, i0 - n_lead):i0]
    dc0 = float(lead.mean()) if lead.size else 0.0
    y, _ = sosfilt(sos, x[i0:], zi=sosfilt_zi(sos) * dc0)
    return _cut(y, sr, level_match)


def _cut(x, sr, level_match: bool):
    """Onset-align, cut to WINDOW_S, pad rather than lie about length, peak
    normalise. `td.onset` reads the clip's own maximum, which appended silence
    cannot raise, so this stage is prefix-determined too."""
    i = td.onset(x)
    n = int(round(td.WINDOW_S * sr))
    seg = x[i:i + n]
    if len(seg) < n:
        seg = np.concatenate([seg, np.zeros(n - len(seg))])
    if level_match:
        pk = float(np.abs(seg).max())
        if pk > 0:
            seg = seg / pk
    return seg, len(seg) - min(n, max(0, len(x) - i))       # (segment, samples padded)


def filtfilt_pedestal(sr: int = 48000, ms: float = 30.0):
    """The F1 defect as one number: the first `ms` of `sosfiltfilt`'s answer to
    a unit impulse at index 0, summed. Zero would be a high-pass behaving."""
    n = max(4096, int(sr * 0.5))
    x = np.zeros(n)
    x[0] = 1.0
    a = sosfiltfilt(_hpf_sos(sr), x)
    b = sosfilt(_hpf_sos(sr), x)
    k = int(sr * ms / 1e3)
    return float(a[:k].sum()), float(b[:k].sum()), float(a[1])


# ===========================================================================
# F4: the 16-bit reference's own quantisation floor, per cell
# ===========================================================================
def quantisation_floor_db(x_file, sr, n_win: int = N_WIN, lsb: float = 1.0 / 32768.0):
    """Per (band, window) dB that uniform 16-bit quantisation noise alone would
    put in the map, in the map's own units (band share of the CONDITIONED
    clip's total energy).

    Uniform quantisation error has variance lsb^2/12, white over 0..sr/2. A
    band of width W therefore holds lsb^2/12 * (2W/sr) of it. The conditioning
    peak-normalises, so the LSB grows by 1/peak; that is the whole calculation
    and it is checked against a measured requantisation in the self-tests."""
    seg, _ = condition_causal(x_file, sr)
    scale = 1.0 / (float(np.abs(x_file).max()) + 1e-20)     # peak-normalisation gain
    q_var = (lsb * scale) ** 2 / 12.0
    e = dfx.cqt_edges()
    n_seg = len(seg) // n_win
    tot = float((seg ** 2).sum()) + 1e-20
    # `trajectory` scales P by len(seg)/sum(win^2) and sums bins; white noise of
    # variance v over a band of B bins contributes v * n_seg * B under that
    # scaling (the window normalisation is exactly what makes it so).
    return np.repeat(noise_cell_db(q_var, sr, len(seg), tot, n_win)[:, :1], n_win, axis=1)


def noise_cell_db(var: float, sr: int, n_total: int, clip_energy: float, n_win: int = N_WIN):
    """dB the map reports for WHITE noise of variance `var`, per band.

    `trajectory` scales |rfft(seg*win)|^2 by len(seg)/sum(win^2); white noise of
    variance v has E|X_k|^2 = v*sum(win^2) in every bin, so a band holding B
    bins reads v * n_seg * B before the clip-total division. That is the whole
    derivation and the self-tests check it against a measured requantisation."""
    counts, _, _ = bin_support(sr, n_total, n_win)
    n_seg = n_total // n_win
    with np.errstate(divide="ignore"):
        row = 10.0 * np.log10(var * n_seg * np.maximum(counts, 0) / (clip_energy + 1e-20) + 1e-20)
    return np.repeat(row[:, None], n_win, axis=1)


# ===========================================================================
# F5: the analysis's own leakage floor, measured by substitution
# ===========================================================================
def leakage_floor_db(seg, sr, n_win: int = N_WIN):
    """What the map reports in band k when band k has been REMOVED from the
    signal. Everything left in that cell is leakage from elsewhere -- the
    30 ms Hann skirt and the brick-wall's own time smear, which is why this is
    a floor and not a correction.

    52 notched re-renders per clip; this is the expensive part of the probe."""
    e = dfx.cqt_edges()
    X = np.fft.rfft(seg)
    f = np.fft.rfftfreq(len(seg), 1.0 / sr)
    out = np.full((len(e) - 1, n_win), FLOOR_DB)
    for k in range(len(e) - 1):
        Y = X.copy()
        Y[(f >= e[k]) & (f < e[k + 1])] = 0.0
        y = np.fft.irfft(Y, n=len(seg))
        M, _, _ = dtj.trajectory(y, sr, n_win)
        out[k] = M[k]
    return out


# ===========================================================================
# The map
# ===========================================================================
class Cell:
    __slots__ = ("k", "w", "cent", "ms", "a", "b", "diff", "floor", "why")

    def __init__(self, k, w, cent, ms, a, b, floor, why):
        self.k, self.w, self.cent, self.ms = k, w, float(cent), float(ms)
        self.a, self.b, self.diff, self.floor, self.why = float(a), float(b), float(b - a), float(floor), why


def voice_map(voice, refs, laws, refdir, arm="ours", n_win=N_WIN,
              study_conditioning=False):
    """The 52 x 8 map for one voice, ours minus machine, with a floor per cell.

    Uses the HELD-OUT settings when the voice has a knob, exactly as
    `discrimination_trajectory.compare` does, so the two are comparable.

    THE FLOOR IS THREE THINGS AND LEAKAGE IS NOT ONE OF THEM. A cell is
    refused when (F3) its band holds no analysis bin, when (F4) the machine's
    reading is at its 16-bit quantisation floor -- then the difference is a
    lower BOUND, which is still a claim -- or when (F6) the cell rests on a
    single FFT bin and the disagreement is inside that bin's own chi-square
    scatter. Leakage is measured too, but it says a difference cannot be
    ATTRIBUTED to that band, not that there is no difference: both sides read
    the same 33 Hz bin, so the difference of the readings is real whatever
    else is in it. It is reported per row, never folded into the floor."""
    use = [c for c in refs if c.voice == voice and c.is_test]
    held_out = bool(use)
    if not use:
        use = [c for c in refs if c.voice == voice]
    if not use:
        return None
    cond = (lambda x, sr: (td.condition(x, sr, True), 0)) if study_conditioning else condition_causal
    A, B, Q, pads, segs = [], [], [], 0, []
    for c in use:
        xr, sr = td.read_wav(c.path)
        sa, pa = cond(xr, sr)
        xo, so = td.render(voice, c.knobs, laws, arm)
        sb, pb = cond(xo, so)
        pads += int(pa > 0) + int(pb > 0)
        A.append(dtj.trajectory(sa, sr, n_win)[0])
        M, cent, ms = dtj.trajectory(sb, so, n_win)
        B.append(M)
        Q.append(quantisation_floor_db(xr, sr, n_win))
        segs.append((sa, sr, sb, so))
    n = len(use)
    A, B, Q = np.mean(A, axis=0), np.mean(B, axis=0), np.mean(Q, axis=0)
    counts, bw, binbw = bin_support(sr, int(round(td.WINDOW_S * sr)), n_win)
    # F6: one FFT bin is chi-square with 2 dof -- 5.57 dB standard deviation --
    # and averaging n settings divides the variance by n.
    scatter = np.where(counts <= 1, ONE_BIN_SCATTER_DB / np.sqrt(n), 0.0)
    return dict(voice=voice, n=n, held_out=held_out, real=A, ours=B, diff=B - A,
                centres=cent, ms=ms, quant=Q, counts=counts, scatter=scatter,
                bias=attribution_bias_db(counts, bw, binbw), pads=pads, segs=segs)


ONE_BIN_SCATTER_DB = 5.57          # sqrt(var of 10*log10(chi2_2/2)) in dB


def _leak(r, k, w):
    """Leakage floor for ONE cell, measured on both sides and taken as the
    larger. Lazy: 52 notched re-renders per clip is the expensive part of the
    probe and only reported rows need it."""
    e = dfx.cqt_edges()
    per_side = [[], []]
    for sa, sra, sb, srb in r["segs"]:
        for i, (seg, sr) in enumerate(((sa, sra), (sb, srb))):
            X = np.fft.rfft(seg)
            f = np.fft.rfftfreq(len(seg), 1.0 / sr)
            X[(f >= e[k]) & (f < e[k + 1])] = 0.0
            per_side[i].append(dtj.trajectory(np.fft.irfft(X, n=len(seg)), sr, N_WIN)[0][k, w])
    # mean per side, to match how `real` and `ours` are averaged, then the
    # larger of the two: the floor has to cover whichever side is dirtier.
    return float(max(np.mean(per_side[0]), np.mean(per_side[1])))


def rows(r, top=8, margin=MARGIN_DB):
    """Reportable cells, largest |difference| first, each with its verdict."""
    out = []
    D, A, B, Q, S = r["diff"], r["real"], r["ours"], r["quant"], r["scatter"]
    for k in range(D.shape[0]):
        if r["counts"][k] == 0:
            continue                                     # F3
        need = max(margin, 2.0 * S[k])                   # F6
        for w in range(D.shape[1]):
            if abs(D[k, w]) < need:
                continue
            if max(A[k, w], B[k, w]) <= FLOOR_DB + 3.0:
                continue
            mach_bound = A[k, w] <= Q[k, w] + margin     # F4
            ours_bound = B[k, w] <= Q[k, w] + margin
            if mach_bound and ours_bound:
                continue
            kind = "EXCESS" if D[k, w] > 0 else "DEFICIT"
            out.append((abs(D[k, w]), kind, k, w, mach_bound, ours_bound, need))
    out.sort(reverse=True, key=lambda t: t[0])
    seen, keep = set(), []
    for _, kind, k, w, mb, ob, need in out:
        if (k // 2, w // 2) in seen:
            continue
        seen.add((k // 2, w // 2))
        leak = _leak(r, k, w)
        attributable = max(A[k, w], B[k, w]) > leak + margin
        keep.append(dict(kind=kind, k=k, w=w, mach_bound=mb, ours_bound=ob,
                         need=need, leak=leak, attributable=attributable))
        if len(keep) >= top:
            break
    return keep


# ===========================================================================
# Attribution: mute one register path at a time
# ===========================================================================
def render_kit(voice, kit, seconds=None):
    n = int((seconds or td.RENDER_S) * dx.SR)
    d = dx.DrumsFx()
    dmix, body = d.play(dx.hit_writes([(10, dx.SOUND_STOP[voice], 1.0)], kit), n)
    g = dx.accent_reg(td.RENDER_GAIN)
    out = dx.output_fx(np.zeros(n), 0, dmix, g, body, g).astype(np.float64) / 32768.0
    return out[td.onset(out):], dx.SR


def _mutate_paths(kit, fn):
    """kit with every PATH word passed through fn(p, word) -> word or None."""
    out = []
    for a, v in kit:
        if dx.A_PATH <= a < dx.A_PATH + dx.N_PATH:
            nv = fn(a - dx.A_PATH, v)
            out.append((a, dx.path_word(dx.SRC_OFF, dx.ENV_NONE) if nv is None else nv))
        else:
            out.append((a, v))
    return out


def onset_lf_db(x, sr, hz=120.0, ms=30.0):
    """Sub-`hz` energy of the first `ms`, in dB relative to the conditioned
    clip's total. The scalar the map's low bands are actually reporting."""
    seg, _ = condition_causal(x, sr)
    n = int(sr * ms / 1e3)
    w = seg[:n]
    win = np.hanning(len(w))
    P = np.abs(np.fft.rfft(w * win)) ** 2
    P = P / (float((win ** 2).sum()) + 1e-20) * len(w)
    f = np.fft.rfftfreq(len(w), 1.0 / sr)
    return 10.0 * np.log10(P[f < hz].sum() / (float((seg ** 2).sum()) + 1e-20) + 1e-20)


SRC_NAMES = {dx.SRC_OFF: "OFF", dx.SRC_NOISE: "NOISE", dx.SRC_SQSUM: "SQSUM",
             dx.SRC_PULSE: "PULSE", dx.SRC_SQPAIR: "SQPAIR"}
NL_NAMES = ("LIN", "SWING", "TANH", "?")


def describe_path(word):
    src, e1, e2 = word & 31, (word >> 5) & 31, (word >> 10) & 31
    nl, att, dest = (word >> 15) & 3, (word >> 17) & 7, (word >> 20) & 31
    s = SRC_NAMES.get(src, f"SQ{src - dx.SRC_SQ}" if dx.SRC_SQ <= src < dx.SRC_SQ + dx.N_OSC
                      else f"TAP{src - dx.SRC_TAP}")
    return f"{s:7s} e{e1:<2d} {NL_NAMES[nl]:5s} -> {'MIX' if dest == 31 else f'm{dest}'}"


def attribute(voice, laws):
    """Every candidate, each rendered with the others held.

    The candidates #152 names are (1) the excitation pulse shape, (2) the
    envelope attack, (3) the noise source's onset and (4) a coefficient-write
    transient. (1)-(3) are all PATH words, so a per-path mute sweep tests all
    three exhaustively rather than one guess at a time; (4) is `coef_seq`."""
    base_kit = dx.kit_with_sounds(voice)
    x, sr = render_kit(voice, base_kit)
    base = onset_lf_db(x, sr)
    res = [("baseline", base, 0.0, "")]

    # (4) the coefficient sequences (BD attack window, tom pitch drop)
    n = int(td.RENDER_S * dx.SR)
    d = dx.DrumsFx()
    dmix, body = d.play(dx.hit_writes([(10, dx.SOUND_STOP[voice], 1.0)], base_kit,
                                      coef_seq=False), n)
    g = dx.accent_reg(td.RENDER_GAIN)
    out = dx.output_fx(np.zeros(n), 0, dmix, g, body, g).astype(np.float64) / 32768.0
    out = out[td.onset(out):]
    v = onset_lf_db(out, dx.SR)
    res.append(("coef_seq=False", v, v - base, "the BD attack window / tom pitch drop"))

    # (1)(2)(3) one path at a time
    kitd = dict(base_kit)
    for p in range(dx.N_PATH):
        word = kitd.get(dx.A_PATH + p, 0)
        if word == 0:
            continue
        muted = _mutate_paths(base_kit, lambda i, w, p=p: None if i == p else w)
        y, sy = render_kit(voice, muted)
        if float(np.abs(y).max()) <= 0:
            res.append((f"mute p{p}", float("nan"), float("nan"), describe_path(word)))
            continue
        v = onset_lf_db(y, sy)
        res.append((f"mute p{p}", v, v - base, describe_path(word)))

    # the nonlinearity, held: SWING -> LIN everywhere
    lin = _mutate_paths(base_kit, lambda i, w: (w & ~(3 << 15)) | (dx.NL_LIN << 15))
    y, sy = render_kit(voice, lin)
    if float(np.abs(y).max()) > 0:
        v = onset_lf_db(y, sy)
        res.append(("all nl -> LIN", v, v - base, "the swing/tanh VCAs"))
    return base, res


# ===========================================================================
# Reports
# ===========================================================================
def report_floors(refdir):
    print(provenance(refdir))
    print("\n=== F1  the conditioning's own edge transient ===")
    for sr in (44100, 48000):
        ff, ca, first = filtfilt_pedestal(sr)
        print(f"  {sr} Hz: sosfiltfilt(impulse at 0) sums to {ff:+9.2f} over the first 30 ms "
              f"(causal sosfilt: {ca:+.4f}); its second sample is {first:+.5f}")
    print("  A high-pass that answers a unit impulse with a full-scale negative pedestal is\n"
          "  not measuring the signal's low frequencies in window 0. REFUSE window 0 of any\n"
          "  row produced with `td.condition`.")

    print("\n=== F3  bands that cannot hold a reading at 30 ms ===")
    for sr in (44100, 48000):
        counts, bw, binbw = bin_support(sr, int(round(td.WINDOW_S * sr)))
        e = dfx.cqt_edges()
        cent = np.sqrt(e[:-1] * e[1:])
        dead = [k for k in range(len(counts)) if counts[k] == 0]
        print(f"  {sr} Hz: analysis bin {binbw:.2f} Hz; {len(dead)} of {len(counts)} bands hold "
              f"NO bin -> pinned to {FLOOR_DB:.0f} dB on both sides:")
        print("    " + ", ".join(f"{cent[k]:.0f}" for k in dead) + " Hz")
        one = [k for k in range(len(counts)) if counts[k] == 1]
        b = attribution_bias_db(counts, bw, binbw)
        print(f"    {len(one)} bands hold exactly ONE bin; their band-width attribution bias is "
              f"{np.nanmin(b[one]):+.1f} to {np.nanmax(b[one]):+.1f} dB "
              f"({', '.join(f'{cent[k]:.0f}' for k in one)} Hz)")

    print("\n=== F2  the two sides do not enter the conditioning the same way ===")
    print("  `_render_raw` returns out[onset(out):]; `read_wav` does not trim. Sub-120 Hz of")
    print("  window 0 under the STUDY's conditioning, machine side, with and without the")
    print("  pre-trim our side always gets:")
    refs = td.ref_clips(refdir, include_unmodelled=True)
    print(f"    {'voice':6s} {'as read':>9s} {'pre-trimmed':>12s} {'move':>7s}")
    worst = 0.0
    for v in sorted({c.voice for c in refs}):
        c = [c for c in refs if c.voice == v][0]
        x, sr = td.read_wav(c.path)

        def lf(sig):
            s = td.condition(sig, sr, True)
            n = len(s) // N_WIN
            w = s[:n]
            win = np.hanning(len(w))
            P = np.abs(np.fft.rfft(w * win)) ** 2 / (float((np.hanning(len(w)) ** 2).sum())) * len(w)
            f = np.fft.rfftfreq(len(w), 1.0 / sr)
            return 10 * np.log10(P[f < 120].sum() / float((s ** 2).sum()) + 1e-20)
        a, b = lf(x), lf(x[td.onset(x):])
        worst = max(worst, abs(b - a))
        print(f"    {v:6s} {a:9.1f} {b:12.1f} {b - a:+7.1f}")
    print(f"  worst move {worst:+.1f} dB, from a choice of array bounds. REFUSE any window-0 row")
    print("  whose value moves by more than the difference it is being used to claim.")

    print("\n=== F4  the reference's 16-bit quantisation floor ===")
    for v in sorted({c.voice for c in refs})[:4]:
        c = [c for c in refs if c.voice == v][0]
        x, sr = td.read_wav(c.path)
        Q = quantisation_floor_db(x, sr)
        live = np.isfinite(Q[:, 0]) & (Q[:, 0] > -200)
        print(f"    {v:4s} peak {20 * np.log10(np.abs(x).max()):6.2f} dBFS -> quantisation floor "
              f"{np.nanmin(Q[live, 0]):.1f} to {np.nanmax(Q[live, 0]):.1f} dB across the live bands")
    return 0


def report_map(refdir, sounds="16", top=6, study=False, voices=None):
    print(provenance(refdir))
    all16 = sounds == "16"
    refs = td.ref_clips(refdir, include_unmodelled=all16)
    laws = td.fit_laws(refdir, all_sounds=all16)
    which = "the STUDY's acausal conditioning (F1 ACTIVE -- for comparison only)" if study else \
        "a causal 20 Hz high-pass, both sides pre-trimmed (F1 and F2 removed)"
    print(f"\nband x time map, ours minus machine, {N_WIN} x 30 ms, {which}")
    print("refused: F3 (band holds no analysis bin), F4 (both sides under the 16-bit floor),")
    print(f"F6 (inside a one-bin cell's own {ONE_BIN_SCATTER_DB:.1f} dB/sqrt(n) scatter). "
          f"margin {MARGIN_DB:.0f} dB.")
    print("`bound` = the other side is at its noise floor, so the difference is a LOWER bound.")
    print("`BIN-WIDE` = removing the named band from the signal barely moves the cell, so the")
    print("cell is reading its whole 33 Hz analysis bin. The difference is real AT THAT BIN;")
    print("it is not attributable to the band the row is named after.\n")
    summary = {}
    for v in (voices or sorted({c.voice for c in refs})):
        r = voice_map(v, refs, laws, refdir, study_conditioning=study)
        if r is None:
            continue
        tag = f"{r['n']} held-out" if r["held_out"] else f"{r['n']} setting(s), NOT held out"
        dead = int((r["counts"] == 0).sum())
        print(f"{v}  ({tag}; {dead}/{len(r['counts'])} bands refused F3"
              f"{'; ZERO-PADDED' if r['pads'] else ''})")
        got = rows(r, top)
        if not got:
            print("    no cell clears its floor by the margin")
        step = r["ms"][1] - r["ms"][0]
        for g in got:
            k, w = g["k"], g["w"]
            tags = []
            if g["mach_bound"]:
                tags.append("bound: machine at floor")
            if g["ours_bound"]:
                tags.append("bound: ours at floor")
            if not g["attributable"]:
                tags.append("BIN-WIDE")
            print(f"    {g['kind']:7s} {r['centres'][k]:6.0f} Hz  {r['ms'][w] - step / 2:3.0f}-"
                  f"{r['ms'][w] + step / 2:3.0f} ms  {r['diff'][k, w]:+6.1f} dB "
                  f"(machine {r['real'][k, w]:+6.1f}, ours {r['ours'][k, w]:+6.1f}; "
                  f"16-bit {r['quant'][k, w]:+6.1f}, leak {g['leak']:+6.1f}, "
                  f"bias {r['bias'][k]:+.1f}, need {g['need']:.1f})"
                  + ("  [" + "; ".join(tags) + "]" if tags else ""))
        summary[v] = (r, got)
        print()
    _verdict(summary)
    return 0


LOW_HZ, HIGH_LO, HIGH_HI = 200.0, 700.0, 5000.0


def _verdict(summary):
    """The number the one-mechanism-or-two question turns on, per voice."""
    print("=== one mechanism or two ===")
    print("  low  = largest EXCESS under 200 Hz in window 0 (the onset pedestal)")
    print("  high = mean signed difference over live cells in 0.7-5 kHz, all windows\n")
    print(f"    {'voice':6s} {'low w0':>8s} {'high 0.7-5k':>12s} {'excess':>7s} {'deficit':>8s}")
    for v, (r, _) in sorted(summary.items()):
        cent, D, A, B, Q = r["centres"], r["diff"], r["real"], r["ours"], r["quant"]
        ok = (r["counts"] > 0)[:, None] & (np.maximum(A, B) > Q + MARGIN_DB) & \
             (np.maximum(A, B) > FLOOR_DB + 3.0)
        lowm = (cent < LOW_HZ)[:, None] & ok
        lowm[:, 1:] = False
        lo = float(D[lowm].max()) if lowm.any() else float("nan")
        hm = ((cent >= HIGH_LO) & (cent <= HIGH_HI))[:, None] & ok
        net = float(D[hm].mean()) if hm.any() else float("nan")
        print(f"    {v:6s} {lo:8.1f} {net:12.1f} {int(((D > MARGIN_DB) & ok).sum()):7d} "
              f"{int(((D < -MARGIN_DB) & ok).sum()):8d}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refs", default=REFS_DEFAULT)
    ap.add_argument("--sounds", choices=("8", "16"), default="16")
    ap.add_argument("--floors", action="store_true", help="the instrument's limits, measured")
    ap.add_argument("--map", action="store_true", help="the band x time map with floors")
    ap.add_argument("--attribute", action="store_true", help="per-path mute sweep")
    ap.add_argument("--reconcile", action="store_true",
                    help="re-measure every row #152 rests on, in its own cell")
    ap.add_argument("--traj", default=TRAJ_DEFAULT)
    ap.add_argument("--shared", action="store_true",
                    help="the tom/conga 0.7-5 kHz constraint, at HEAD and before #154")
    ap.add_argument("--dc", action="store_true",
                    help="the excitation's mean and the DC gain it is multiplied by")
    ap.add_argument("--bands", action="store_true",
                    help="the 0.7-5 kHz split the shared-circuit constraint is stated in")
    ap.add_argument("--study-conditioning", action="store_true",
                    help="re-run the map with the acausal filter, to show what it added")
    ap.add_argument("--voices", default="")
    ap.add_argument("--top", type=int, default=6)
    a = ap.parse_args(argv)
    if not (a.floors or a.map or a.attribute or a.bands or a.dc or a.shared
            or a.reconcile):
        a.floors = a.map = True
    rc = 0
    if a.floors:
        rc |= report_floors(a.refs)
    if a.map:
        rc |= report_map(a.refs, a.sounds, a.top, a.study_conditioning,
                         a.voices.split(",") if a.voices else None)
    if a.attribute:
        print(provenance(a.refs))
        laws = td.fit_laws(a.refs, all_sounds=True)
        vs = a.voices.split(",") if a.voices else list(dx.SOUND_NAMES)
        print("\nper-path mute sweep: sub-120 Hz energy of the first 30 ms, dB re clip total\n"
              "a path whose removal DROPS the number is the one emitting the onset excess\n")
        for v in vs:
            base, res = attribute(v, laws)
            print(f"{v}  baseline {base:+.1f} dB")
            for name, val, d, what in res[1:]:
                mark = "  <== " if (np.isfinite(d) and d <= -6.0) else "      "
                print(f"    {name:16s} {val:+7.1f}  {d:+7.1f}{mark}{what}")
            print()
    if a.bands:
        rc |= report_bands(a.refs, a.voices.split(",") if a.voices else None)
    if a.dc:
        rc |= report_dc(a.refs, a.voices.split(",") if a.voices else None)
    if a.shared:
        rc |= report_shared(a.refs, a.voices.split(",") if a.voices else None)
    if a.reconcile:
        rc |= report_reconcile(a.refs, a.traj, a.sounds)
    return rc


# ===========================================================================
# Self-tests. Every number this file prints has one.
# ===========================================================================
def test_the_filtfilt_pedestal_is_the_size_claimed():
    """F1, pinned. If scipy ever fixes its padding this test goes green-to-red
    and the docstring above becomes wrong, which is the point of pinning it."""
    ff, ca, second = filtfilt_pedestal(48000)
    assert ff < -300.0, ff                       # ~-373 in the version measured
    assert abs(ca) < 1.0, ca                     # a causal high-pass integrates to ~0
    assert second < -0.99, second                # a full-scale negative second sample


def test_the_pedestal_creates_low_frequency_where_there_is_none():
    """The injected-bug control for F1, run the other way round: a signal built
    with NO energy under 200 Hz must still come out of the study's conditioning
    with a large window-0 low-frequency reading, and must not out of the
    causal one."""
    sr = 48000
    n = int(sr * 0.4)
    t = np.arange(n) / sr
    rng = np.random.default_rng(0)
    x = rng.standard_normal(n) * np.exp(-t / 0.02)
    X = np.fft.rfft(x)
    X[np.fft.rfftfreq(n, 1.0 / sr) < 400.0] = 0.0            # nothing at all below 400 Hz
    x = np.fft.irfft(X, n=n)

    def w0_lf(seg):
        w = seg[:len(seg) // N_WIN]
        win = np.hanning(len(w))
        P = np.abs(np.fft.rfft(w * win)) ** 2 / float((win ** 2).sum()) * len(w)
        f = np.fft.rfftfreq(len(w), 1.0 / sr)
        return 10 * np.log10(P[f < 120].sum() / float((seg ** 2).sum()) + 1e-20)
    study = w0_lf(td.condition(x, sr, True))
    causal = w0_lf(condition_causal(x, sr)[0])
    assert study > causal + 15.0, (study, causal)
    assert causal < -20.0, causal


# --- F0: the conditioning must not let the future move the past -------------
def _prefix_signals(sr=48000, secs=0.40):
    """Three 400 ms clips with the same envelope and increasing DC content, so
    the error a whole-clip mean subtraction makes is seen to SCALE with DC.

      * `sine`      a decaying 200 Hz sine -- no DC at all
      * `rectified` the same, half-wave rectified -- like SRC_PULSE, which
                    never changes sign (mean/|mean| = 1.000, #152)
      * `swing`     the same through NL_SWING's law (x4 above zero, /8 below)
    """
    n = int(sr * secs)
    t = np.arange(n) / sr
    x = np.sin(2 * np.pi * 200.0 * t) * np.exp(-t / 0.08)
    return {"sine": x, "rectified": np.maximum(x, 0.0),
            "swing": np.where(x > 0, 4.0 * x, x / 8.0)}


def prefix_drift_pct(cond, sr=48000, pad_s=0.70):
    """Largest change in the CONDITIONED first WINDOW_S, as a percent of its
    own peak, when `pad_s` of silence is appended to the clip. Zero is the
    only defensible answer: silence in the future is not information about the
    past. Returns {name: percent}."""
    out = {}
    for name, x in _prefix_signals(sr).items():
        a, _ = cond(x, sr)
        b, _ = cond(np.concatenate([x, np.zeros(int(sr * pad_s))]), sr)
        m = min(len(a), len(b))
        pk = float(np.abs(a[:m]).max()) + 1e-20
        out[name] = 100.0 * float(np.abs(a[:m] - b[:m]).max()) / pk
    return out


def test_future_silence_cannot_change_an_already_rendered_prefix():
    """THE property the repair buys. `condition_causal` is prefix-determined:
    every stage reads only samples at or before the one it emits (the declared
    initial condition reads a lead-in that PRECEDES the window; `td.onset`
    reads a maximum that appended silence cannot raise). So appending silence
    must move the conditioned window by zero, to floating point."""
    for name, pct in prefix_drift_pct(condition_causal).items():
        assert pct < 1e-9, (name, pct)


def test_the_old_conditioning_let_the_future_move_the_past():
    """The injected-bug control for the test above: the SAME assertion run
    against the implementation that shipped must fail, and must fail WORST on
    the rectified signal. If a future scipy or numpy made `condition_meansub`
    pass this, the repair would no longer be pinned to anything.

    #165 quotes 0.27 % / 9.58 % / 6.42 % on ITS test signals; these are not
    those signals, so the thresholds below are what THESE measure (0.18 /
    4.97 / 4.80) and the shape -- error proportional to DC -- is the claim."""
    d = prefix_drift_pct(condition_meansub)
    assert d["sine"] > 0.1, d                      # even a DC-free clip moves
    assert d["rectified"] > 3.0, d                 # ~5 %: it scales with DC
    assert d["swing"] > 3.0, d
    assert d["rectified"] > 20.0 * d["sine"], d    # the error IS the DC


def test_a_standing_dc_offset_is_still_removed():
    """The repair must not cost what the mean subtraction was there for. The
    refs carry about 1 LSB of converter DC; with a lead-in to read, the
    declared initial condition removes a constant offset as completely as
    subtracting the mean did -- and does it causally."""
    sr = 48000
    n = int(sr * 0.40)
    t = np.arange(n) / sr
    x = np.zeros(n)
    lead = int(sr * 0.05)
    x[lead:] = np.sin(2 * np.pi * 200.0 * t[:n - lead]) * np.exp(-t[:n - lead] / 0.08)
    off = 1.0 / 32768.0
    a, _ = condition_causal(x, sr, level_match=False)
    b, _ = condition_causal(x + off, sr, level_match=False)
    assert float(np.abs(a - b).max()) < 0.02 * off, float(np.abs(a - b).max())


def test_bands_with_no_bin_are_refused_not_reported():
    """F3. The named bands must hold no bin at either rate, and `trajectory`
    must pin them to the clamp on a broadband signal that genuinely has energy
    everywhere -- so a -75 there is the instrument, not the sound."""
    for sr in (44100, 48000):
        n = int(round(td.WINDOW_S * sr))
        counts, _, binbw = bin_support(sr, n)
        assert abs(binbw - 33.333) < 0.01, binbw
        dead = set(np.where(counts == 0)[0])
        assert {0, 1, 2, 3, 5, 6, 8, 9, 11, 14} <= dead, sorted(dead)
        rng = np.random.default_rng(1)
        M, _, _ = dtj.trajectory(rng.standard_normal(n), sr)
        for k in sorted(dead):
            assert M[k].max() <= FLOOR_DB + 1e-9, (sr, k, M[k].max())


def test_a_bands_reading_is_its_BIN_count_not_its_width():
    """F3's consequence, on ground truth: white noise, whose true spectral
    density is flat, so a band holding B bins must read 10*log10(B) + const --
    NOT 10*log10(its width). `attribution_bias_db` reports the gap.

    Averaged over 200 realisations because ONE realisation cannot test this: a
    one-bin cell is chi-square with 2 dof and scatters by about 5.6 dB, which
    is larger than the effect. That scatter is itself a floor on any single
    reading of bands 4, 7, 10, 12, 13, 15 and 16 in the shipped map."""
    sr = 48000
    n = int(round(td.WINDOW_S * sr))
    counts, bw, binbw = bin_support(sr, n)
    bias = attribution_bias_db(counts, bw, binbw)
    rng = np.random.default_rng(2)
    acc = []
    for _ in range(200):
        M, cent, _ = dtj.trajectory(rng.standard_normal(n), sr)
        acc.append(10.0 ** (M[:, 0] / 10.0))
    m = 10.0 * np.log10(np.mean(acc, axis=0))
    live = counts > 0
    off = m[live] - 10.0 * np.log10(counts[live].astype(float))
    assert float(off.std()) < 0.5, (float(off.std()), off.min(), off.max())
    width_pred = 10.0 * np.log10(bw[live] / binbw) + off.mean()
    assert np.allclose(m[live] - width_pred, bias[live], atol=1.0), \
        float(np.abs(m[live] - width_pred - bias[live]).max())
    # the three bands the shipped report quotes most -- 67, 95 and 135 Hz --
    # are each one bin, each over-reporting by more than 3 dB
    for k, hz in ((4, 67), (7, 95), (10, 135)):
        assert counts[k] == 1, (k, hz, counts[k])
        assert bias[k] > 3.0, (hz, float(bias[k]))
    assert abs(bias[4] - 6.3) < 0.2, float(bias[4])


def test_a_single_one_bin_cell_scatters_by_more_than_the_effect():
    """The floor a ONE-BIN band carries even when everything else is perfect.
    Reported so no row of the map resting on bands 4/7/10/12/13/15/16 is read
    as if it had the precision of a wide band."""
    sr = 48000
    n = int(round(td.WINDOW_S * sr))
    counts, _, _ = bin_support(sr, n)
    rng = np.random.default_rng(7)
    k = int(np.where(counts == 1)[0][0])
    vals = [dtj.trajectory(rng.standard_normal(n), sr)[0][k, 0] for _ in range(200)]
    assert 4.0 < float(np.std(vals)) < 8.0, float(np.std(vals))


def test_the_noise_floor_closed_form_is_what_the_map_reports():
    """The formula F4 rests on, against a MEASURED requantisation and nothing
    else. Quantise a known signal to 16 bits, isolate the error it made, and
    check that `noise_cell_db` predicts the map the error alone produces."""
    sr = 44100
    n = int(round(td.WINDOW_S * sr))
    t = np.arange(n) / sr
    x = 0.5 * np.sin(2 * np.pi * 220.0 * t)
    err = np.round(x * 32768.0) / 32768.0 - x               # the real quantisation error
    var = float((err ** 2).mean())
    assert abs(10 * np.log10(var * 12 * 32768.0 ** 2)) < 1.0, var   # it IS lsb^2/12
    M, _, _ = dtj.trajectory(err, sr)
    counts, _, _ = bin_support(sr, n)
    pred = noise_cell_db(var, sr, n, float((err ** 2).sum()))
    live = counts > 5
    d = M[live, 0] - pred[live, 0]
    assert abs(float(np.median(d))) < 1.5, (float(np.median(d)), float(d.std()))


def test_the_quantisation_floor_tracks_the_clips_own_headroom():
    """A quiet 16-bit clip has a HIGHER floor once the conditioning normalises
    it, and the floor must move by exactly the headroom."""
    sr = 44100
    n = int(round(td.WINDOW_S * sr)) * 2
    t = np.arange(n) / sr
    loud = np.round(0.9 * np.sin(2 * np.pi * 220.0 * t) * np.exp(-t / 0.08) * 32768) / 32768
    quiet = np.round(0.09 * np.sin(2 * np.pi * 220.0 * t) * np.exp(-t / 0.08) * 32768) / 32768
    a, b = quantisation_floor_db(loud, sr), quantisation_floor_db(quiet, sr)
    counts, _, _ = bin_support(sr, int(round(td.WINDOW_S * sr)))
    live = counts > 5
    gap = float(np.median(b[live, 0] - a[live, 0]))
    assert abs(gap - 20.0) < 1.5, gap


def test_the_leakage_floor_sees_a_tone_that_is_not_there():
    """F5, constructed: one loud tone and nothing else. Every OTHER band's
    reading must be at or under the leakage floor this function measures --
    if it were not, the floor would not be a floor."""
    sr = 48000
    n = int(round(td.WINDOW_S * sr))
    t = np.arange(n) / sr
    x = np.sin(2 * np.pi * 1000.0 * t) * np.exp(-t / 0.05)
    M, cent, _ = dtj.trajectory(x, sr)
    L = leakage_floor_db(x, sr)
    k0 = int(np.argmin(np.abs(cent - 1000.0)))
    bad = [(int(k), float(M[k, 0]), float(L[k, 0])) for k in range(len(cent))
           if abs(k - k0) > 2 and M[k, 0] > L[k, 0] + 6.0 and M[k, 0] > FLOOR_DB + 3]
    assert not bad, bad[:5]


def test_a_known_dc_pedestal_is_reported_as_low_band_excess():
    """The injected-bug control the map itself must catch: add a decaying
    UNIPOLAR pedestal -- the shape `SRC_PULSE * env` puts on the mix bus -- to
    a clean high-band sound, and the map must find it in the low bands of
    window 0 and nowhere else."""
    sr = 48000
    n = int(sr * 0.4)
    t = np.arange(n) / sr
    rng = np.random.default_rng(3)
    clean = rng.standard_normal(n) * np.exp(-t / 0.03)
    X = np.fft.rfft(clean)
    X[np.fft.rfftfreq(n, 1.0 / sr) < 2000.0] = 0.0
    clean = np.fft.irfft(X, n=n)
    dirty = clean + 0.5 * np.abs(clean).max() * np.exp(-t / 0.01)      # the pedestal
    A, cent, ms = dtj.trajectory(condition_causal(clean, sr)[0], sr)
    B, _, _ = dtj.trajectory(condition_causal(dirty, sr)[0], sr)
    counts, _, _ = bin_support(sr, int(round(td.WINDOW_S * sr)))
    D = B - A
    low = (cent < 200.0) & (counts > 0)
    assert D[low, 0].max() > 20.0, D[low, 0].max()
    hi = (cent > 3000.0) & (counts > 0)
    assert abs(D[hi, 0]).max() < 6.0, abs(D[hi, 0]).max()
    assert D[low, N_WIN - 1].max() < D[low, 0].max() - 15.0


def test_muting_a_path_removes_exactly_that_path():
    """The attribution sweep's own control: muting the BD's click path must
    silence the mix bus and leave the body bus bit-exact."""
    kit = dx.kit_with_sounds("BD")
    n = int(0.2 * dx.SR)

    def play(k):
        d = dx.DrumsFx()
        return d.play(dx.hit_writes([(10, dx.SOUND_STOP["BD"], 1.0)], k), n)
    dm0, bd0 = play(kit)
    dm1, bd1 = play(_mutate_paths(kit, lambda i, w: None if i == 1 else w))
    assert np.abs(dm0).max() > 0 and np.abs(dm1).max() == 0, (np.abs(dm0).max(), np.abs(dm1).max())
    assert np.array_equal(bd0, bd1)


def test_a_gain_does_not_move_the_map():
    sr = 48000
    n = int(sr * 0.4)
    t = np.arange(n) / sr
    x = np.sin(2 * np.pi * 400.0 * t) * np.exp(-t / 0.05)
    A, _, _ = dtj.trajectory(condition_causal(x, sr)[0], sr)
    B, _, _ = dtj.trajectory(condition_causal(0.017 * x, sr)[0], sr)
    assert np.allclose(A, B, atol=1e-9)




# ===========================================================================
# The 0.7-5 kHz split the shared-circuit constraint is stated in
# ===========================================================================
# `docs/discrimination.md` 5a states the tom/conga puzzle in three bands, and
# `model/audio_measure.band_energy` -- the filter bank that produced it -- says
# in its own docstring that `sosfiltfilt` "manufactures an edge worth up to
# 10 dB in a sparsely-occupied band" unless the segment arrives with a TRUE
# pre-onset lead. The Fischer files begin AT their onset: there is no lead to
# give it, on the machine's side, for any of the sixteen. So this uses the
# other instrument that docstring names -- a RECTANGULAR-window FFT, where
# Parseval is exact and prepended silence is worth 0.44 dB -- which needs no
# lead and cannot have that artefact.
SPLIT_EDGES = ((20.0, 700.0), (700.0, 5000.0), (5000.0, 20000.0))


def parseval_shares(x, sr, edges=SPLIT_EDGES):
    """Fraction of total energy per band, rectangular window, exact."""
    x = np.asarray(x, float)
    X = np.fft.rfft(x)
    p = (np.abs(X) ** 2)
    p[1:-1] *= 2.0 if len(x) % 2 == 0 else 2.0
    f = np.fft.rfftfreq(len(x), 1.0 / sr)
    tot = float(p.sum())
    return np.array([float(p[(f >= lo) & (f < hi)].sum()) / (tot + 1e-30) for lo, hi in edges])


def parseval_leakage_floor(x, sr, edges=SPLIT_EDGES):
    """The share each band still reports when that band has been removed from
    the signal. A share at or under this is the instrument's skirt."""
    x = np.asarray(x, float)
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1.0 / sr)
    out = []
    for i, (lo, hi) in enumerate(edges):
        Y = X.copy()
        Y[(f >= lo) & (f < hi)] = 0.0
        out.append(parseval_shares(np.fft.irfft(Y, n=len(x)), sr, edges)[i])
    return np.array(out)


def report_bands(refdir, voices=None):
    """Ours against the machine in the three bands of `docs/discrimination.md`
    5a, at HEAD -- which is AFTER #154 removed the x1.7 tom sweep that the
    table was measured with."""
    print(provenance(refdir))
    refs = td.ref_clips(refdir, include_unmodelled=True)
    laws = td.fit_laws(refdir, all_sounds=True)
    vs = voices or sorted({c.voice for c in refs})
    print("\nenergy share of the 240 ms conditioned window, rectangular-FFT Parseval\n"
          "(the filter bank of `discrimination.md` 5a cannot be used: it needs a true\n"
          " pre-onset lead and the Fischer files begin at their onset)\n")
    hdr = ("voice", "<0.7k m", "<0.7k o", "0.7-5k m", "0.7-5k o", "dB(o/m)",
           ">5k m", ">5k o", "dB(o/m)", "no-coef 0.7-5k", "dB")
    print("    " + " ".join(f"{h:>10s}" for h in hdr))
    for v in vs:
        use = [c for c in refs if c.voice == v and c.is_test] or [c for c in refs if c.voice == v]
        A, B, C, floors = [], [], [], []
        for c in use:
            xr, sr = td.read_wav(c.path)
            A.append(parseval_shares(condition_causal(xr, sr)[0], sr))
            xo, so = td.render(v, c.knobs, laws, "ours")
            B.append(parseval_shares(condition_causal(xo, so)[0], so))
            floors.append(parseval_leakage_floor(condition_causal(xo, so)[0], so))
            kit = td.kit_at(v, c.knobs, laws, "ours")
            n = int(td.RENDER_S * dx.SR)
            d = dx.DrumsFx()
            dm, bd = d.play(dx.hit_writes([(10, dx.SOUND_STOP[v], 1.0)], kit, coef_seq=False), n)
            g = dx.accent_reg(td.RENDER_GAIN)
            o = dx.output_fx(np.zeros(n), 0, dm, g, bd, g).astype(np.float64) / 32768.0
            C.append(parseval_shares(condition_causal(o[td.onset(o):], dx.SR)[0], dx.SR))
        A, B, C, F = (np.mean(x, axis=0) for x in (A, B, C, floors))
        d = lambda o, m: 10 * np.log10((o + 1e-30) / (m + 1e-30))       # noqa: E731
        flag = "  FLOOR" if B[1] < 3 * F[1] else ""
        print(f"    {v:>10s} " + " ".join(
            f"{x:10.5f}" if isinstance(x, float) else f"{x:>10s}" for x in
            (float(A[0]), float(B[0]), float(A[1]), float(B[1]), float(d(B[1], A[1])),
             float(A[2]), float(B[2]), float(d(B[2], A[2])), float(C[1]),
             float(d(C[1], A[1])))) + flag)
    print("\n  FLOOR = our own 0.7-5 kHz share is within 5 dB of this estimator's leakage floor,")
    print("  so the row is a bound and not a reading.")
    return 0


def test_parseval_shares_split_a_two_tone_signal():
    """Ground truth for the band split: two tones of known power, one each
    side of the 700 Hz edge. This is `band_energy`'s own ground-truth test,
    run against the rectangular instrument."""
    sr = 48000
    n = sr
    t = np.arange(n) / sr
    x = 1.0 * np.sin(2 * np.pi * 200.0 * t) + 0.5 * np.sin(2 * np.pi * 2000.0 * t)
    s = parseval_shares(x, sr)
    want = np.array([1.0, 0.25, 0.0]) / 1.25
    assert np.allclose(s, want, atol=1e-4), (s, want)


def test_parseval_shares_are_invariant_to_prepended_silence():
    """The property the filter bank does not have, and the reason this is the
    instrument used where no pre-onset lead exists."""
    sr = 48000
    n = int(sr * 0.24)
    t = np.arange(n) / sr
    x = np.sin(2 * np.pi * 180.0 * t) * np.exp(-t / 0.05)
    a = parseval_shares(x, sr)
    b = parseval_shares(np.concatenate([np.zeros(1000), x]), sr)
    assert np.abs(10 * np.log10((b + 1e-30) / (a + 1e-30)))[:2].max() < 0.5, (a, b)


def test_a_decaying_resonators_own_skirt_is_above_the_leakage_floor():
    """The floor that decides whether a tom's 0.7-5 kHz reading is a reading.
    A 90 Hz two-pole ring at Q 25 has a real Lorentzian skirt up there; the
    estimator's own leakage must be well under it, or the row is a bound."""
    sr = 48000
    n = int(sr * 0.24)
    t = np.arange(n) / sr
    x = np.sin(2 * np.pi * 90.0 * t) * np.exp(-t * np.pi * 90.0 / 25.0)
    s = parseval_shares(x, sr)
    f = parseval_leakage_floor(x, sr)
    assert s[1] > 10 * f[1], (s[1], f[1])


# ===========================================================================
# The other half of the attribution: DC in, DC gain, DC out
# ===========================================================================
def mode_dc_gain(kit: dict, m: int) -> float:
    """Gain from a mode's excitation to its output at DC.

    A two-pole resonator with the RAW numerator is ALL-POLE: it has no zero at
    z = 1, so its DC gain is 1/(1 - a1 - a2) -- 18 to 23,899 in the shipped
    kit. The BP numerator (1 - z^-2) and the HP numerator (1 - z^-1)^2 both
    have a zero at z = 1 and gain exactly 0 there. Modes at or above N_NUMS
    cannot carry a numerator at all, so they are always all-pole."""
    b = dx.A_MODE + m * dx.MODE_STRIDE
    a1 = dx.s26(kit.get(b, 0)) / (1 << 24)
    a2 = dx.s26(kit.get(b + 1, 0)) / (1 << 24)
    num = kit.get(b + 3, 0) & 3
    if m < dx.N_NUMS and num in (dx.BP, dx.HP):
        return 0.0
    d = 1.0 - a1 - a2
    return float("inf") if abs(d) < 1e-12 else 1.0 / d


def report_dc(refdir, voices=None, ms: float = 30.0):
    """Why a strike emits a low-frequency step at all: the excitation's MEAN
    is not zero and the resonator it drives has no zero at DC.

    `SRC_PULSE` is the constant +32767 gated by an envelope -- a positive-only
    pulse -- and `NL_SWING` (x<<2 above zero, x>>3 below it) rectifies a
    bipolar source into one. Neither is followed by anything that removes a
    mean: the block has no output coupling anywhere."""
    print(provenance(refdir))
    print(f"\nexcitation mean over the first {ms:.0f} ms and the DC gain it is multiplied by\n")
    print(f"    {'voice':6s} {'mode':6s} {'num':>4s} {'DC gain':>10s} {'mean exc':>10s} "
          f"{'mean|exc|':>10s} {'mean/|mean|':>11s}")
    for v in (voices or list(dx.SOUND_NAMES)):
        kit = dx.kit_with_sounds(v)
        n = int(td.RENDER_S * dx.SR)
        d = dx.DrumsFx()
        dmix, body = d.play(dx.hit_writes([(10, dx.SOUND_STOP[v], 1.0)], kit), n)
        tot = np.asarray(dmix, float) + np.asarray(body, float)
        i = td.onset(tot)
        w = slice(i, i + int(dx.SR * ms / 1e3))
        kd = dict(kit)
        exc = d.trace["exc"]
        for m in range(exc.shape[1]):
            if np.abs(exc[:, m]).max() == 0:
                continue
            e = exc[w, m].astype(float)
            g = mode_dc_gain(kd, m)
            num = {0: "RAW", 1: "BP", 2: "HP", 3: "RAW"}[kd.get(
                dx.A_MODE + m * dx.MODE_STRIDE + 3, 0) & 3]
            frac = e.mean() / (np.abs(e).mean() + 1e-20)
            print(f"    {v:6s} m{m:<5d} {num:>4s} {g:10.1f} {e.mean():10.1f} "
                  f"{np.abs(e).mean():10.1f} {frac:11.3f}")
    print("\n  mean/|mean| = 1.000 means the excitation NEVER changes sign: it is a pulse with")
    print("  a full DC component, and an all-pole mode multiplies that by its DC gain.")
    return 0


def test_the_pulse_source_has_a_full_dc_component():
    """The excitation shape, as a number. `SRC_PULSE` is +32767 gated by an
    envelope, so its mean equals its mean magnitude exactly -- it never goes
    negative -- and every mode it drives in the shipped kit is all-pole."""
    kit = dx.kit_with_sounds("RS")
    n = int(0.1 * dx.SR)
    d = dx.DrumsFx()
    d.play(dx.hit_writes([(10, dx.SOUND_STOP["RS"], 1.0)], kit), n)
    exc = d.trace["exc"]
    kd = dict(kit)
    for m in (dx.M_RS1, dx.M_RS2):
        e = exc[:, m].astype(float)
        assert e.min() >= 0.0 and e.max() > 0.0, (m, e.min(), e.max())
        assert abs(e.mean() / np.abs(e).mean() - 1.0) < 1e-12, m
        assert mode_dc_gain(kd, m) > 10.0, (m, mode_dc_gain(kd, m))


def test_a_numerator_removes_the_dc_gain_and_raw_does_not():
    """The control on `mode_dc_gain`, measured on the bank itself.

    A constant excitation into the same pole pair: RAW settles at a large
    positive DC, BP and HP settle at a small NEGATIVE residue -- their zero at
    z = 1 is exact in the transfer function but the datapath's arithmetic
    shifts FLOOR, so a steady drive leaves about -1 % of itself behind. That
    residue is real and is recorded here; it is 30 dB under what RAW does and
    is not the mechanism this probe is about."""
    from modal_fixed import ModalFx
    got = {}
    for num in (dx.RAW, dx.BP, dx.HP):
        kit = dict(dx.mode_writes(0, 1100.0, 2.8, 0.25, num))
        bank = ModalFx(modes=1, nums=1, headroom=dx.BODY_HR, out_bits=dx.BODY_BITS)
        a1, a2 = dx.s26(kit[dx.A_MODE]), dx.s26(kit[dx.A_MODE + 1])
        amp = kit[dx.A_MODE + 2]
        y = [bank.step([1000], [(a1, a2, amp)], [num]) for _ in range(20000)]
        got[num] = (mode_dc_gain(kit, 0), float(np.mean(y[-2000:])))
    assert got[dx.RAW][0] > 1.0 and got[dx.RAW][1] > 100.0, got
    for num in (dx.BP, dx.HP):
        assert got[num][0] == 0.0, got
        assert abs(got[num][1]) < 0.05 * abs(got[dx.RAW][1]), got
        assert abs(got[num][1]) < 0.02 * 1000, got        # the truncation residue


def test_the_swing_nonlinearity_rectifies(  ):
    """`NL_SWING` is x<<2 above zero and x>>3 below it, so a zero-mean square
    comes out with a large positive mean -- the second source of the onset
    step. Measured on the block's own `_nonlinear`, not reimplemented."""
    d = dx.DrumsFx()
    sq = np.array([1000, -1000] * 64)
    out = np.array([d._nonlinear(int(v), dx.NL_SWING) for v in sq], float)
    assert abs(sq.mean()) < 1e-9
    assert out.mean() / np.abs(out).mean() > 0.7, out.mean() / np.abs(out).mean()


# ===========================================================================
# The shared-circuit constraint of #152, measured at HEAD and before #154
# ===========================================================================
# "LT/MT/HT have nothing in 0.7-5 kHz while their conga twins on the same three
# circuits have up to +28 dB too much" (#152, from `docs/discrimination.md` 5a,
# which is #148's table). #148 predates #154, and #154 removed the x1.7 pitch
# sweep those six voices were rendered with. This measures both states.
def tom_pitch_drop_writes_before_154(frame, mode, f0_hz, q, amp, accent=1.0):
    """`drums_fx.tom_pitch_drop_writes` EXACTLY as it stood at 24cff92, so the
    red number stays reproducible after the red is gone -- the standard this
    repository already holds `tools/probes/estimator_defects.py` to.

    One law for tom and conga, the TUNING pot ignored, and `min(max(accent,
    0), 1)` -- a CLAMP, so an unaccented hit got the full sweep."""
    import math
    out = []
    excess = (1.7 - 1.0) * min(max(accent, 0.0), 1.0)
    for i in range(dx.TOM_DROP_STEPS + 1):
        t = i / dx.TOM_DROP_STEPS
        hz = f0_hz * (1.0 + excess * math.exp(-3.0 * t))
        f = frame + int(round(t * dx.TOM_DROP_MS * 1e-3 * dx.SR))
        out += [(f, a, v) for a, v in dx.mode_writes(mode, hz, q, amp)[:2]]
    return out


SHARED = ("LT", "LC", "MT", "MC", "HT", "HC")


def report_shared(refdir, voices=None):
    print(provenance(refdir))
    refs = td.ref_clips(refdir, include_unmodelled=True)
    laws = td.fit_laws(refdir, all_sounds=True)
    print("\n0.7-5 kHz share of the 240 ms conditioned window, rectangular-FFT Parseval.")
    print("`before #154` re-renders with the x1.7 sweep and its accent CLAMP reinstated --")
    print("the state `docs/discrimination.md` 5a and `docs/discrimination-trajectory.txt`")
    print("were both measured in.\n")
    print(f"    {'voice':6s} {'machine':>9s} {'HEAD':>9s} {'dB(o/m)':>8s} "
          f"{'before #154':>12s} {'dB(o/m)':>8s} {'sweep made':>11s}")
    orig = dx.tom_pitch_drop_writes
    for v in (voices or SHARED):
        use = [c for c in refs if c.voice == v and c.is_test] or \
              [c for c in refs if c.voice == v]
        M, H, B = [], [], []
        for c in use:
            xr, sr = td.read_wav(c.path)
            M.append(parseval_shares(condition_causal(xr, sr)[0], sr)[1])
            xo, so = td.render(v, c.knobs, laws, "ours")
            H.append(parseval_shares(condition_causal(xo, so)[0], so)[1])
            dx.tom_pitch_drop_writes = tom_pitch_drop_writes_before_154
            try:
                xb, sb = td.render(v, c.knobs, laws, "ours")
            finally:
                dx.tom_pitch_drop_writes = orig
            B.append(parseval_shares(condition_causal(xb, sb)[0], sb)[1])
        m, h, b = float(np.mean(M)), float(np.mean(H)), float(np.mean(B))
        db = lambda a, c: 10 * np.log10((a + 1e-30) / (c + 1e-30))      # noqa: E731
        print(f"    {v:6s} {m:9.5f} {h:9.5f} {db(h, m):8.2f} {b:12.5f} {db(b, m):8.2f} "
              f"{db(b, h):11.2f}")
    print("\n  `sweep made` = what the x1.7 sweep put into 0.7-5 kHz that HEAD does not.")
    return 0


def test_the_pre_154_sweep_is_the_one_that_was_shipped():
    """The reimplementation, checked against what #154 documents about it.

    The old law CLAMPED the accent, so a plain hit (accent 1.0, the model's
    'x' and the 808's step with the accent bit off) got the FULL x1.7 and an
    accented one got no more. The new law has a threshold: a plain tom gets
    x1.06 and a plain conga gets nothing at all."""
    def onset_ratio(fn, mode, f0, accent):
        w = fn(0, mode, f0, 25.0, 0.3, accent)
        return dx.poles_from_regs(w[0][2], w[1][2])[0] / f0
    for accent in (1.0, 1.4, 2.0):
        r = onset_ratio(tom_pitch_drop_writes_before_154, dx.M_LT, 90.0, accent)
        assert abs(r - 1.7) < 0.02, (accent, r)          # clamped: all the same
    assert abs(onset_ratio(dx.tom_pitch_drop_writes, dx.M_LT, 90.0, 1.0)
               - dx.TOM_DROP_RATIO) < 0.02
    # the conga position at a plain hit: full sweep before, none after
    assert abs(onset_ratio(tom_pitch_drop_writes_before_154, dx.M_HT, 400.0, 1.0) - 1.7) < 0.02
    assert dx.tom_drop_excess(dx.M_HT, 400.0, 1.0) == 0.0
    assert abs(onset_ratio(dx.tom_pitch_drop_writes, dx.M_HT, 400.0, 1.0) - 1.0) < 1e-4


# ===========================================================================
# Reconciling #152's own rows, one at a time
# ===========================================================================
TRAJ_DEFAULT = str(ROOT / "docs" / "discrimination-trajectory.txt")
_EXCESS_RE = __import__("re").compile(
    r"^\s*(\d+) Hz carries\s*([+-][\d.]+) dB between\s*(\d+) and\s*(\d+) ms")
_VOICE_RE = __import__("re").compile(r"^([A-Z]{2})\s+\(")


def parse_trajectory_excess(path=TRAJ_DEFAULT):
    """The '(b)' rows of `docs/discrimination-trajectory.txt` -- the ones #152
    is made of -- as (voice, centre Hz, window index, dB). Parsed, never
    transcribed: a number retyped into prose is a claim, not evidence."""
    out, voice = [], None
    for line in pathlib.Path(path).read_text().splitlines():
        m = _VOICE_RE.match(line)
        if m:
            voice = m.group(1)
            continue
        m = _EXCESS_RE.match(line)
        if m and voice:
            hz, db, t0, t1 = float(m.group(1)), float(m.group(2)), float(m.group(3)), float(m.group(4))
            out.append((voice, hz, int(round((t0 + t1) / 2 / 30.0 - 0.5)), db))
    return out


def report_reconcile(refdir, traj=TRAJ_DEFAULT, sounds="16"):
    """Every row #152 rests on, re-measured in its OWN cell at HEAD with a
    causal conditioning. The band and window are the trajectory file's, read
    out of the file; only the instrument and the commit have changed."""
    print(provenance(refdir))
    rowsrc = parse_trajectory_excess(traj)
    print(f"\n{len(rowsrc)} 'carries N dB where the machine has none' rows in {traj}")
    print("re-measured in the SAME band and window, at HEAD, causal conditioning.\n")
    all16 = sounds == "16"
    refs = td.ref_clips(refdir, include_unmodelled=all16)
    laws = td.fit_laws(refdir, all_sounds=all16)
    print(f"    {'voice':6s} {'band':>7s} {'window':>9s} {'#152 said':>10s} {'HEAD+causal':>12s} "
          f"{'machine':>9s} {'ours':>8s} {'16-bit':>8s} {'need':>6s}  verdict")
    survive = []
    for v in sorted({r[0] for r in rowsrc}):
        r = voice_map(v, refs, laws, refdir)
        if r is None:
            continue
        for voice, hz, w, db in [x for x in rowsrc if x[0] == v]:
            k = int(np.argmin(np.abs(r["centres"] - hz)))
            w = min(max(w, 0), r["diff"].shape[1] - 1)
            need = max(MARGIN_DB, 2.0 * r["scatter"][k])
            d = r["diff"][k, w]
            if r["counts"][k] == 0:
                verdict = "REFUSED (F3: no bin)"
            elif abs(d) < need:
                verdict = "GONE (inside the floor)"
            elif d <= 0:
                verdict = "SIGN FLIPPED"
            elif d < db - 10.0:
                verdict = f"SHRUNK by {db - d:.0f} dB"
            else:
                verdict = "STANDS"
            survive.append((voice, verdict))
            print(f"    {voice:6s} {r['centres'][k]:6.0f}  {30 * w:4.0f}-{30 * w + 30:4.0f} ms "
                  f"{db:10.1f} {d:12.1f} {r['real'][k, w]:9.1f} {r['ours'][k, w]:8.1f} "
                  f"{r['quant'][k, w]:8.1f} {need:6.1f}  {verdict}")
    n_stand = sum(1 for _, x in survive if x == "STANDS")
    print(f"\n  {n_stand} of {len(survive)} stand.")
    return 0


def test_the_trajectory_file_has_fourteen_excess_rows_not_sixteen():
    """#152 says 'all sixteen voices carry broadband energy in the first 30 ms
    that the real machine does not have'. Its own source file carries such a
    row for FOURTEEN voices: BD and LT have none. Parsed, so this cannot drift
    away from the file."""
    rows = parse_trajectory_excess()
    voices = sorted({v for v, _, _, _ in rows})
    assert len(rows) == 14, (len(rows), rows)
    assert set(voices) == set(dx.SOUND_NAMES) - {"BD", "LT"}, voices
    # and they are not all in the first 30 ms either: CY, MA and SD are in 30-60
    late = sorted(v for v, _, w, _ in rows if w != 0)
    assert late == ["CY", "MA", "SD"], late


if __name__ == "__main__":
    sys.exit(main())
