#!/usr/bin/env python3
"""Where, in band and in time, our render differs from the machine -- as
sentences a circuit designer can act on.

A knob-equivalent says how far apart we are. It does not say what to change.
This says "the 806 Hz band is 14 dB hot between 30 and 60 ms" and "our 322 Hz
partial decays at 6 dB per 100 ms where the machine's decays at 21", which is
the shape of statement that names a fix.

It is deliberately NOT a discriminator. There is no classifier, no split and
no verdict here: it runs on exactly the same conditioned window the study's
features run on (`condition`, WINDOW_S, level-matched), over exactly the same
held-out settings, and reports signed differences. Read it next to the
knob-equivalent, not instead of it.

    .venv/bin/python model/discrimination_trajectory.py --refs /tmp/tr808-ref
    .venv/bin/python -m pytest model/discrimination_trajectory.py -q

TWO PROPERTIES THAT MAKE THE NUMBERS COMPARABLE

Every cell is a band's share of the WHOLE CLIP's energy, in dB, so a pure gain
cancels -- the corpus is peak-limited to -2.4 dBFS and a level difference here
would carry no information about the machine anyway.

Bands are constant-Q and stated in Hz, and windows are stated in ms, so the
reference's 44.1 kHz and our 48 kHz land in the same cells with nothing
resampled. That is the same rule the feature set follows and for the same
reason.
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import discrimination_features as dfx        # noqa: E402
import test_discrimination as td             # noqa: E402

N_WIN = 8                       # 8 x 30 ms over the 240 ms window
FLOOR_DB = -75.0
# A band is only reported while it is within this much of its own peak; below
# it the slope is the noise floor's, not the partial's.
LIVE_DB = 30.0
MIN_LIVE_WINDOWS = 3            # below this a decay slope is REFUSED


def trajectory(x: np.ndarray, sr: int, n_win: int = N_WIN, edges=None):
    """(n_bands, n_win) of band energy in dB relative to the clip's total.

    One power spectrum per window, integrated over constant-Q edges. Returns
    (matrix, centre frequencies, window centres in ms)."""
    x = np.asarray(x, float)
    e = dfx.cqt_edges() if edges is None else np.asarray(edges, float)
    b = np.linspace(0, len(x), n_win + 1).astype(int)
    tot = float((x ** 2).sum()) + 1e-20
    M = np.zeros((len(e) - 1, n_win))
    for w in range(n_win):
        seg = x[b[w]:max(b[w + 1], b[w] + 1)]
        win = np.hanning(len(seg)) if len(seg) > 1 else np.ones(len(seg))
        P = np.abs(np.fft.rfft(seg * win)) ** 2
        f = np.fft.rfftfreq(len(seg), 1.0 / sr)
        # the window's own normalisation, so a Hann's 0.375 power factor and
        # the window length both cancel between the two sides
        P = P / (float((win ** 2).sum()) + 1e-20) * len(seg)
        for k in range(len(e) - 1):
            m = (f >= e[k]) & (f < e[k + 1])
            v = float(P[m].sum()) / tot if m.any() else 0.0
            M[k, w] = max(FLOOR_DB, 10.0 * np.log10(v + 1e-20))
    centres = np.sqrt(e[:-1] * e[1:])
    ms = 1e3 * (b[:-1] + np.diff(b) / 2.0) / sr
    return M, centres, ms


def decay_rate_db_per_100ms(row: np.ndarray, ms: np.ndarray):
    """Slope of one band's dB trajectory over the windows where it is alive.

    REFUSES -- returns None -- when fewer than MIN_LIVE_WINDOWS windows sit
    within LIVE_DB of the band's own peak. A slope fitted through the floor
    is the floor's slope; `docs/verification-rules.md` exists about exactly
    this, and PR #132's bass drum was passing on one."""
    pk = float(row.max())
    if pk <= FLOOR_DB + 3.0:
        return None                 # the band is pinned to the floor: no evidence
    live = row >= pk - LIVE_DB
    if int(live.sum()) < MIN_LIVE_WINDOWS:
        return None
    i = np.where(live)[0]
    i = i[i >= int(np.argmax(row))]
    if len(i) < MIN_LIVE_WINDOWS:
        return None
    return float(np.polyfit(ms[i] / 100.0, row[i], 1)[0])


def compare(voice: str, refs, laws, arm: str = "ours", n_win: int = N_WIN):
    """Mean signed difference, ours minus the machine, over the HELD-OUT
    settings of one voice. Falls back to every setting when the voice has no
    knob -- and says so, because that is sound-matching territory, not
    emulation, and must never be read as a generalisation result."""
    use = [c for c in refs if c.voice == voice and c.is_test]
    held_out = bool(use)
    if not use:
        use = [c for c in refs if c.voice == voice]
    if not use:
        return None
    A, B = [], []
    for c in use:
        xr, sr = td.read_wav(c.path)
        A.append(trajectory(td.condition(xr, sr, True), sr, n_win)[0])
        xo, so = td.render(voice, c.knobs, laws, arm)
        M, cent, ms = trajectory(td.condition(xo, so, True), so, n_win)
        B.append(M)
    A, B = np.mean(A, axis=0), np.mean(B, axis=0)
    rates = []
    for k in range(A.shape[0]):
        ra, rb = decay_rate_db_per_100ms(A[k], ms), decay_rate_db_per_100ms(B[k], ms)
        rates.append((ra, rb))
    return dict(voice=voice, n=len(use), held_out=held_out, real=A, ours=B,
                diff=B - A, centres=cent, ms=ms, rates=rates)


# A band is "significant" for a side when it is within this much of that
# side's own loudest cell. Ranking raw dB differences without it puts the
# largest numbers where one side is on the -75 dB clamp, which is a
# difference between a signal and a floor and not a difference of instrument.
SIGNIFICANT_DB = 40.0


def sentences(r: dict, top: int = 4) -> list:
    """The report, in three kinds of statement, because they ask for three
    different fixes:

      (a) the machine has energy here and we get its level wrong
      (b) we put energy where the machine has essentially none
      (c) the band is in both but decays at the wrong rate
    """
    out = []
    D, A, B, cent, ms = r["diff"], r["real"], r["ours"], r["centres"], r["ms"]
    step = ms[1] - ms[0] if len(ms) > 1 else 30.0
    a_sig, b_sig = A > A.max() - SIGNIFICANT_DB, B > B.max() - SIGNIFICANT_DB

    def span(w):
        return f"between {ms[w] - step / 2:3.0f} and {ms[w] + step / 2:3.0f} ms"

    # (a) where the MACHINE has energy: a level error on something real
    flat = sorted(((abs(D[k, w]), k, w) for k in range(D.shape[0]) for w in range(D.shape[1])
                   if a_sig[k, w]), reverse=True)
    seen = set()
    for _, k, w in flat:
        if len(out) >= top:
            break
        if (k // 2, w // 2) in seen:
            continue
        seen.add((k // 2, w // 2))
        out.append(f"{cent[k]:6.0f} Hz is {D[k, w]:+5.1f} dB {span(w)} "
                   f"(machine {A[k, w]:+5.1f} dB, ours {B[k, w]:+5.1f})")
    # (b) where only WE have energy: something the circuit should not emit
    only = sorted(((D[k, w], k, w) for k in range(D.shape[0]) for w in range(D.shape[1])
                   if b_sig[k, w] and not a_sig[k, w]), reverse=True)
    seen = set()
    for d, k, w in only[:top * 4]:
        if len([x for x in out if "the machine has none" in x]) >= max(1, top // 2):
            break
        if (k // 2, w // 2) in seen or d < 10.0:
            continue
        seen.add((k // 2, w // 2))
        out.append(f"{cent[k]:6.0f} Hz carries {d:+5.1f} dB {span(w)} where the machine has "
                   f"none ({A[k, w]:+5.1f} dB, {A.max() - A[k, w]:.0f} dB under its own peak)")
    # 2. decay-rate disagreements, which are what "too slowly" means
    rr = []
    for k, (ra, rb) in enumerate(r["rates"]):
        if ra is None or rb is None or not a_sig[k].any():
            continue
        rr.append((abs(rb - ra), k, ra, rb))
    for _, k, ra, rb in sorted(rr, reverse=True)[:top]:
        word = "too slowly" if rb > ra else "too fast"
        out.append(f"{cent[k]:6.0f} Hz decays {word}: ours {rb:+5.1f} dB/100 ms against the "
                   f"machine's {ra:+5.1f}")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sys.path.insert(0, os.path.join(HERE, "..", "tools"))
    import run_case
    ap.add_argument("--refs", default=str(run_case.configured_refs()))
    ap.add_argument("--sounds", choices=("8", "16"), default="16")
    ap.add_argument("--arm", default="ours")
    ap.add_argument("--top", type=int, default=3)
    a = ap.parse_args(argv)
    all16 = a.sounds == "16"
    refs = td.ref_clips(a.refs, include_unmodelled=all16)
    laws = td.fit_laws(a.refs, all_sounds=all16)
    print(f"band x time trajectory, arm '{a.arm}', {N_WIN} windows of "
          f"{1e3 * td.WINDOW_S / N_WIN:.0f} ms over the study's own conditioned window\n")
    for v in sorted({c.voice for c in refs}):
        r = compare(v, refs, laws, a.arm)
        if r is None:
            continue
        tag = f"{r['n']} held-out settings" if r["held_out"] else \
            f"{r['n']} setting(s), NOT held out -- descriptive only, no knob to hold out"
        print(f"{v}  ({tag})")
        for line in sentences(r, a.top):
            print(f"    {line}")
        print()
    return 0


# ===========================================================================
# Self-tests
# ===========================================================================
def _tone_burst(f, sr, dur, tau, amp=1.0):
    t = np.arange(int(sr * dur)) / sr
    return amp * np.sin(2 * np.pi * f * t) * np.exp(-t / tau)


def test_a_gain_does_not_move_a_single_cell():
    sr = 44100
    x = _tone_burst(500.0, sr, td.WINDOW_S, 0.05)
    A = trajectory(x, sr)[0]
    B = trajectory(x * 0.013, sr)[0]
    assert np.allclose(A, B, atol=1e-9)


def test_the_trajectory_is_rate_independent():
    """Same rule as the feature set: the reference is 44.1 kHz and we render
    at 48 kHz, so a cell that moves with the rate would be a rate detector.

    THE FIRST VERSION OF THIS TEST COMPARED A RESAMPLER AGAINST A FLOOR. Its
    signal was a pure 500 Hz tone, which puts nothing at all in the 3.4 kHz
    band, so one side sat at the -75 dB clamp and the other at the resampler's
    own -45 dB ringing: a 30 dB "rate dependence" that was entirely the test.
    The signal is broadband now, and only cells live on BOTH sides are
    compared -- a cell where one side is at the floor has no ratio to check."""
    sr = 44100
    n = int(sr * td.WINDOW_S)
    t = np.arange(n) / sr
    rng = np.random.default_rng(0)
    x = (np.sin(2 * np.pi * 500.0 * t) + 0.5 * rng.standard_normal(n)) * np.exp(-t / 0.08)
    y = dfx._resample(x, 44100, 48000)
    A = trajectory(x, 44100)[0]
    B = trajectory(y, 48000)[0]
    live = (A > FLOOR_DB + 10) & (B > FLOOR_DB + 10)
    assert live.sum() > 100, live.sum()
    assert np.abs(A - B)[live].max() < 1.5, np.abs(A - B)[live].max()


def test_a_known_decay_rate_is_recovered():
    """Ground truth before any number is quoted, AND THE FIRST VERSION OF
    THIS TEST HAD THE CONSTANT WRONG -- it asserted -8.686 dB/100 ms for a
    50 ms tau, which is -8.686/tau in SECONDS misread as per 100 ms. The
    estimator was right and the expectation was wrong, which is the cheaper
    of the two ways round.

    An amplitude e^(-t/tau) is 20*log10(a) = -8.686*t/tau dB, so tau = 50 ms
    decays at -8.686*0.1/0.05 = -17.37 dB per 100 ms. Checked at two taus so
    that a constant offset could not satisfy it."""
    sr = 44100
    for tau, want in ((0.05, -17.37), (0.10, -8.686)):
        x = _tone_burst(500.0, sr, td.WINDOW_S, tau)
        M, cent, ms = trajectory(x, sr)
        k = int(np.argmin(np.abs(cent - 500.0)))
        r = decay_rate_db_per_100ms(M[k], ms)
        assert r is not None and abs(r - want) < 1.0, (tau, r, want)


def test_a_slower_decay_is_reported_as_slower():
    """The direction of the sentence, constructed. Two identical tones, one
    decaying at half the rate, must come out as 'decays too slowly'."""
    sr = 44100
    fast = _tone_burst(500.0, sr, td.WINDOW_S, 0.03)
    slow = _tone_burst(500.0, sr, td.WINDOW_S, 0.09)
    Mf, cent, ms = trajectory(fast, sr)
    Ms, _, _ = trajectory(slow, sr)
    k = int(np.argmin(np.abs(cent - 500.0)))
    rf = decay_rate_db_per_100ms(Mf[k], ms)
    rs = decay_rate_db_per_100ms(Ms[k], ms)
    assert rs > rf + 5.0, (rf, rs)


def test_a_decay_rate_is_refused_when_the_band_is_not_alive():
    """REFUSED is a first-class outcome. A band with nothing in it must
    return None, not a slope through the floor -- the defect PR #132 fixed
    on the bass drum was a decay fitted past the end of the evidence."""
    sr = 44100
    x = _tone_burst(500.0, sr, td.WINDOW_S, 0.05)
    M, cent, ms = trajectory(x, sr)
    k = int(np.argmin(np.abs(cent - 9000.0)))
    assert M[k].max() <= FLOOR_DB + 1e-6
    assert decay_rate_db_per_100ms(M[k], ms) is None


def test_the_named_band_and_window_are_where_the_difference_is():
    """The sentence must point at the right cell. Put an extra tone into one
    band for one 30 ms window only and check the report names both."""
    sr = 44100
    n = int(sr * td.WINDOW_S)
    t = np.arange(n) / sr
    base = np.sin(2 * np.pi * 300.0 * t) * np.exp(-t / 0.10)
    w0, w1 = int(n * 3 / N_WIN), int(n * 4 / N_WIN)
    extra = base.copy()
    extra[w0:w1] += 0.9 * np.sin(2 * np.pi * 2000.0 * t[w0:w1])
    A, cent, ms = trajectory(base, sr)
    B, _, _ = trajectory(extra, sr)
    D = B - A
    k, w = np.unravel_index(int(np.argmax(np.abs(D))), D.shape)
    assert abs(cent[k] - 2000.0) / 2000.0 < 0.15, cent[k]
    assert w == 3, (w, ms[w])


if __name__ == "__main__":
    sys.exit(main())
