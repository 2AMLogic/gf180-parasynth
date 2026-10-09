#!/usr/bin/env python3
"""Measure the two #138 metrics on the Fischer corpus and on our own render.

    python tools/measure_promoted_bands.py validate   # start red, then the controls
    python tools/measure_promoted_bands.py measure    # ceilings + ours-vs-real

`validate` answers "does the estimator read a signal whose answer is known, and
does it go red when it is broken". It runs first and `measure` REFUSES (exit 2)
if it does not pass: a number from an unvalidated estimator is not data.

`measure` reports, per voice, (a) the knob TRAVEL of each metric across every
recorded real setting -- the CEILING `promoted_bands` needs -- and (b) ours
minus the machine at each HELD-OUT setting. Ruler: raw dB / ms of the named
estimator, NOT the knob-equivalent rulers of docs/discrimination.md 3.1.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "tools"))
import audio_measure as am      # noqa: E402
import promoted_measures as pm  # noqa: E402
import promoted_bands as pb     # noqa: E402

import run_case as rc       # noqa: E402

REFS = rc.configured_refs()  # the Fischer repo root, <REFS>/bd8/... (#522)
OUT = ROOT / "docs" / "promoted-bands-results.json"
#: per-voice search band for the dominant partial: 40 Hz to the corpus module's
#: own one-octave-above-range fmax (test_discrimination.TUNING_FMAX).
PERIOD_VOICES = ("LT", "MT", "HT", "LC", "MC", "HC")
SR = 44100
#: Acceptance tolerance of the closed-form low-band cases (see known_cases).
LOWBAND_TOL_DB = 0.01
#: Acceptance tolerance of the closed-form period cases, relative.
PERIOD_TOL = 0.005
#: How far `dominant_period_ms` may sit from a GLIDE's SETTLED frequency, in %.
#: Measured worst over 0-20 % glide depth is 1.12 % (`glide_cases`), so this is
#: 1.34x the estimator's own error there -- tight enough that an uninterpolated
#: argmax (4.2 Hz bins, 4.6 % at 90 Hz) fails it, which is the control.
GLIDE_SETTLED_TOL_PCT = 1.5
#: How far the same reading must sit from the glide's ENERGY-WEIGHTED MEAN
#: instantaneous frequency, so the characterisation "it reads the settled
#: frequency, not the note's mean" cannot go quietly false. Measured 3.12 % at
#: 10 % depth.
GLIDE_NOT_THE_MEAN_PCT = 2.0
#: Glide depths measured. 0-20 % brackets an 808 tom's sweep; 40 % is past it
#: and is reported, not gated.
GLIDE_DEPTHS = (0.02, 0.10, 0.20)
GLIDE_TAU_S = 0.03


def _tone(f, dur=0.24, tau=0.1, sr=SR):
    t = np.arange(int(dur * sr)) / sr
    return np.sin(2 * np.pi * f * t) * np.exp(-t / tau)


def _glide(f_end: float, depth: float, *, glide_tau=GLIDE_TAU_S, dur=0.24,
           tau=0.1, sr=SR):
    """A downward glide with a KNOWN instantaneous-frequency trajectory.

    f(t) = f_end + (f0 - f_end) * exp(-t / glide_tau) with f0 = f_end*(1+depth);
    the phase is that function's exact integral, so the signal's instantaneous
    frequency is `inst` by construction rather than by approximation.

    Returns (x, f_bar), where `f_bar` is the ENERGY-WEIGHTED MEAN instantaneous
    frequency -- the thing a single spectral line of a glide would report if it
    reported an average. It does not; see `glide_cases`."""
    t = np.arange(int(dur * sr)) / sr
    f0 = f_end * (1.0 + depth)
    inst = f_end + (f0 - f_end) * np.exp(-t / glide_tau)
    phase = 2 * np.pi * (f_end * t - (f0 - f_end) * glide_tau
                         * (np.exp(-t / glide_tau) - 1.0))
    env = np.exp(-t / tau)
    w = env ** 2
    return np.sin(phase) * env, float((inst * w).sum() / w.sum())


def _early_low_band_clip(a_hi=0.3, split_s=0.080, dur=0.24, sr=SR):
    """A clip whose LOW band exists only before `split_s` and whose HIGH band
    exists only after it -- the time structure `docs/discrimination-trajectory.
    txt` reports on all sixteen sounds (energy early, where the machine has
    none).

    Inside the first window the answer is closed-form: two sines of amplitude 1
    at 90 Hz and `a_hi` at 1500 Hz, so the band share is the amplitude-squared
    ratio. The edges are raised-cosine over 5 ms because a hard split is a
    click, and a click is broadband for a reason that is not the signal."""
    n, k = int(dur * sr), int(split_s * sr)
    t = np.arange(n) / sr
    ramp = int(0.005 * sr)
    g = np.ones(k)
    g[-ramp:] = np.cos(np.linspace(0, np.pi / 2, ramp)) ** 2
    h = np.ones(n - k)
    h[:ramp] = np.sin(np.linspace(0, np.pi / 2, ramp)) ** 2
    x = np.zeros(n)
    x[:k] = (np.sin(2 * np.pi * 90 * t[:k])
             + a_hi * np.sin(2 * np.pi * 1500 * t[:k])) * g
    x[k:] += np.sin(2 * np.pi * 1500 * t[k:]) * h
    return x


def known_cases() -> list:
    """(label, passed, detail). Every answer is closed-form."""
    out = []
    t = np.arange(int(0.24 * SR)) / SR
    # Two partials, amplitudes 1 at 90 Hz and a_hi at 1500 Hz. The band-power
    # ratio of two sines under a Hann window is the amplitude-squared ratio
    # (leakage is far below 1e-3 at this separation), so the answer is exact.
    #
    # WRONG-THEN-RIGHT, twice, both found by controls (rules 1 and 5):
    # * this list held a_hi=0.1, whose answer (-0.043 dB) sat INSIDE the then
    #   0.05 dB tolerance of 0.0 -- a stub answering 0.0, and an estimator
    #   whose band swallowed the 1.5 kHz partial, both passed it. Found by the
    #   start-red stub run (`start_red`). Replaced by 0.2 (-0.170 dB).
    # * the 0.05 dB tolerance let a RECTANGULAR window pass three of these four
    #   cases (errors 0.043-0.045 dB) and fail the fourth by 0.002 dB. Hann's
    #   own error here is < 0.0004 dB, so the tolerance is now 0.01 dB: 25x
    #   Hann's error, and every rectangular-window case fails.
    # a_hi=0.0 still passes a 0.0 stub by construction (its answer IS 0 dB); it
    # is the no-leakage case, listed in STUB_MAY_PASS.
    for a_hi in (0.0, 0.2, 0.3, 1.0):
        x = np.sin(2 * np.pi * 90 * t) + a_hi * np.sin(2 * np.pi * 1500 * t)
        want = 10 * np.log10(1.0 / (1.0 + a_hi ** 2))
        e = pm.lowband_level_db(x, SR)
        out.append((f"lowband a_hi={a_hi}", e.ok and abs(e.value - want) < LOWBAND_TOL_DB,
                    f"want {want:.3f} got {e.value if e.ok else e.reason}"))
    for f0 in (60.0, 90.0, 190.0, 410.0):
        for detune in (0.0, 0.02, 0.07):
            f = f0 * (1 + detune)
            e = pm.dominant_period_ms(_tone(f), SR, (40.0, f0 * 2.0))
            want = 1000.0 / f
            out.append((f"period {f:.1f} Hz", e.ok and abs(e.value / want - 1) < PERIOD_TOL,
                        f"want {want:.4f} got {e.value if e.ok else e.reason}"))
    # Gain cancels exactly.
    x = np.sin(2 * np.pi * 90 * t) + 0.3 * np.sin(2 * np.pi * 1500 * t)
    a, b = pm.lowband_level_db(x, SR), pm.lowband_level_db(0.01 * x, SR)
    out.append(("gain cancels", a.ok and b.ok and abs(a.value - b.value) < 1e-9, ""))
    # Refusals.
    out.append(("silence refuses", not pm.lowband_level_db(np.zeros(len(t)), SR).ok, ""))
    out.append(("short clip refuses", not pm.lowband_level_db(x[:500], SR).ok, ""))
    out.append(("band below floor refuses",
                not pm.lowband_level_db(np.sin(2 * np.pi * 1000 * t), SR).ok, ""))
    noise = np.random.default_rng(0).standard_normal(len(t))
    out.append(("noise has no period", not pm.dominant_period_ms(noise, SR, (40.0, 400.0)).ok, ""))
    out.extend(onset_cases())
    out.extend(glide_cases())
    out.extend(floor_cases())
    return out


def onset_cases() -> list:
    """`lowband_onset_db`: the TIME-RESOLVED low-band reading (#138, third
    increment), with closed-form answers.

    Two things are being checked, and they are different. That the ratio is
    still the ratio when it is read over a window -- so a windowed number and a
    whole-clip number are the same quantity measured over different spans. And
    that the WINDOW IS WHERE IT SAYS IT IS, which is what the whole-clip
    estimator cannot do and is the only reason this one exists."""
    out = []
    t = np.arange(int(0.24 * SR)) / SR
    #: the closed-form share of two sines of amplitude 1 and 0.3, used by
    #: several cases below: 10*log10(1 / (1 + 0.3**2)).
    want_lo = 10 * np.log10(1.0 / (1.0 + 0.3 ** 2))
    # (1) the ratio, unchanged by windowing. Same closed form as the whole-clip
    # cases: the band share of two sines is the amplitude-squared ratio.
    for a_hi in (0.2, 0.3, 1.0):
        x = np.sin(2 * np.pi * 90 * t) + a_hi * np.sin(2 * np.pi * 1500 * t)
        want = 10 * np.log10(1.0 / (1.0 + a_hi ** 2))
        e = pm.lowband_onset_db(x, SR)
        out.append((f"onset a_hi={a_hi} stationary",
                    e.ok and abs(e.value - want) < LOWBAND_TOL_DB,
                    f"want {want:.3f} got {e.value if e.ok else e.reason}"))
    # (2) the window. On a clip whose low band is early-only, the 0-80 ms
    # reading is the closed-form in-window ratio AND stands clear of the
    # whole-clip reading by more than the 3 dB half-power convention. Both
    # halves are required: the first alone passes a stub answering 0.0 dB on a
    # case whose answer is near 0, and the second alone passes it too (0.0 is
    # "more than 3 dB above" nothing). Measured: +9.17 dB apart.
    x = _early_low_band_clip()
    want = want_lo
    on, whole = pm.lowband_onset_db(x, SR), pm.lowband_level_db(x, SR)
    out.append(("onset 0-80 ms reads an early low band the whole clip misses",
                on.ok and whole.ok and abs(on.value - want) < LOWBAND_TOL_DB
                and on.value - whole.value > 3.0,
                f"want {want:.3f} got {on.value if on.ok else on.reason}; "
                f"whole clip {whole.value if whole.ok else whole.reason}"))
    # (3) the preconditions, each a refusal. The first is the finding: a 40 Hz
    # lower edge is not resolvable in the 30 ms window the trajectory report
    # uses, so that reading does not exist and is not approximated.
    e = pm.lowband_onset_db(x, SR, window_ms=30.0)
    out.append(("onset 30 ms on a 40 Hz edge refuses",
                not e.ok and "cannot resolve" in e.reason, e.reason))
    e = pm.lowband_onset_db(x, SR, start_ms=200.0)
    out.append(("onset window past the clip end refuses",
                not e.ok and "fit inside" in e.reason, e.reason))
    e = pm.lowband_onset_db(x, SR, start_ms=160.0)
    out.append(("onset window after the low band refuses", not e.ok, e.reason))
    # (3b) the precondition that was NOT foreseen, found by a registration test
    # rather than by design: a line within one Hann main lobe of a band edge
    # straddles it, and the share becomes a reading of the straddle. A 50 Hz
    # line 10 Hz above a 40 Hz edge in an 80 ms window (25 Hz half-width) is the
    # case, and it is the #111 bass drum fixture, not a contrived signal.
    bd = _tone(50.0, tau=0.12)
    e = pm.lowband_onset_db(bd, SR)
    out.append(("onset refuses a 50 Hz line against a 40 Hz edge",
                not e.ok and "main lobe" in e.reason, e.reason))
    # ...and the SAME line against the SAME edge is readable in a LONGER
    # window, so the refusal is about window-versus-edge resolution rather than
    # about 50 Hz, about this band, or about bass drums -- and it names its own
    # remedy. 150 ms halves the main lobe to 13.3 Hz, which clears 50 Hz of a
    # 40 Hz edge (measured edge/in-band 0.0005).
    #
    # WRONG-THEN-RIGHT, 2: this case first moved the BAND EDGE down to 20 Hz
    # instead of lengthening the window. That is impossible for a different
    # reason -- 80 ms holds 1.6 of the 3 cycles a 20 Hz edge needs -- so the
    # case failed on the OTHER precondition and demonstrated nothing. Found by
    # running validate, not by reading it. The two preconditions pull opposite
    # ways on the lower edge, which is worth knowing and is why this is here.
    # The high partial carries THE SAME ENVELOPE as the 50 Hz one. WRONG-THEN-
    # RIGHT, 3: it was first an undamped sine beside a tau=0.12 s damped one, so
    # over a 150 ms window their energy ratio was not the amplitude ratio and
    # the closed form was wrong by 0.75 dB. The case failed; the estimator was
    # right. An envelope shared by both components cancels out of a ratio
    # exactly, which is what makes the closed form a closed form.
    bd_hi = bd + 0.3 * np.sin(2 * np.pi * 1500 * t) * np.exp(-t / 0.12)
    e = pm.lowband_onset_db(bd_hi, SR, window_ms=150.0)
    out.append(("onset reads the same 50 Hz line in a 150 ms window",
                e.ok and abs(e.value - want_lo) < LOWBAND_TOL_DB,
                f"want {want_lo:.3f} got {e.value if e.ok else e.reason}"))
    # The whole-clip estimator does NOT refuse it (a 240 ms window's half-width
    # is 8.3 Hz), and the known hole is pinned here rather than only in prose:
    # it reports the straddle in `detail` and answers anyway.
    e = pm.lowband_level_db(bd, SR)
    out.append(("the whole clip reports its edge leak without refusing",
                e.ok and "edge_leak" in e.detail and e.detail["edge_leak"] < 0.05,
                f"edge_leak {e.detail.get('edge_leak') if e.ok else e.reason}"))
    # (4) ...and the refusal is a property of the band/window PAIR, not a ban
    # on short windows. Raise the lower edge and 30 ms becomes legal, with the
    # same closed-form answer. Without this case the gate above would read as
    # "30 ms is impossible", which is false and would be the wrong lesson.
    x2 = np.sin(2 * np.pi * 300 * t) + 0.3 * np.sin(2 * np.pi * 1500 * t)
    e = pm.lowband_onset_db(x2, SR, (150.0, 600.0), 30.0)
    out.append(("onset 30 ms on a 150 Hz edge answers",
                e.ok and abs(e.value - want) < LOWBAND_TOL_DB,
                f"want {want:.3f} got {e.value if e.ok else e.reason}"))
    return out


def glide_cases() -> list:
    """`dominant_period_ms` on a DOWNWARD GLIDE -- the case
    `promoted_measures`' header said had never been measured, and which every
    808 tom does.

    The answer is not a tolerance on a guess: the glide's instantaneous
    frequency is known in closed form (`_glide`), and the two candidate
    "right answers" are its endpoint and its energy-weighted mean. The
    estimator reads the ENDPOINT. Both facts are gated, because the second one
    is what stops the reading being quoted as the pitch of the note."""
    out = []
    for depth in GLIDE_DEPTHS:
        x, f_bar = _glide(90.0, depth)
        e = pm.dominant_period_ms(x, SR, (40.0, 200.0))
        # `e.ok and e.value` and not `e.ok`: a stub answering 0.0 ms is ok=True,
        # and 1000/0 would raise out of the harness rather than fail a case.
        got = (1000.0 / e.value) if (e.ok and e.value) else float("nan")
        err = 100.0 * (got / 90.0 - 1.0)
        out.append((f"glide {depth*100:.0f} % reads the settled frequency",
                    got == got and abs(err) < GLIDE_SETTLED_TOL_PCT,
                    f"read {got:.3f} Hz, f(end) 90.000, err {err:+.2f} %"
                    if e.ok else e.reason))
    x, f_bar = _glide(90.0, 0.10)
    e = pm.dominant_period_ms(x, SR, (40.0, 200.0))
    got = (1000.0 / e.value) if (e.ok and e.value) else float("nan")
    out.append(("glide 10 % is not the note's mean frequency",
                got == got and abs(100.0 * (got / f_bar - 1.0)) > GLIDE_NOT_THE_MEAN_PCT,
                f"read {got:.3f} Hz against an energy-weighted mean of "
                f"{f_bar:.3f} Hz ({100.0*(got/f_bar-1.0):+.2f} %)"
                if e.ok else e.reason))
    return out


def _repeat_doc(metric="lowband_level_db", floor=0.04, span=0.01) -> dict:
    """A #111 harness record, in the shape `promoted_bands.harness_floor`
    reads, holding one metric with a stated session spread and editing noise."""
    return {"session_to_session": {"metrics": {metric: {"abs_diff_median": floor}}},
            "self_test": {"editing_noise": {metric: {"span": span}}}}


def floor_cases() -> list:
    """The FLOOR half of the tolerance, with closed-form answers.

    `promoted_bands` stopped carrying a hand-entered floor table under #138's
    second increment and now reads `tools/measure_repeatability.py`'s own
    output. A reader is a place a number can be invented silently -- a missing
    key read as zero, an apparatus-dominated spread used anyway, the arithmetic
    mean in place of the geometric one -- so each of those is a case here with
    an answer that does not come from this repository's model."""
    out = []
    # sqrt(0.04 * 0.25) = 0.1 and sqrt(0.25 * 1.0) = 0.5, exactly.
    for floor, ceiling, want in ((0.04, 0.25, 0.1), (0.25, 1.0, 0.5)):
        tol, basis = pb.band_tolerance("lowband_level_db", "BD", ceiling,
                                       doc=_repeat_doc(floor=floor))
        out.append((f"floor {floor} x ceiling {ceiling} -> sqrt",
                    abs(tol - want) < 1e-12 if tol == tol else False,
                    f"want {want} got {tol} ({basis})"))

    def refuses(label, metric, voice, ceiling, doc):
        tol, basis = pb.band_tolerance(metric, voice, ceiling, doc=doc)
        out.append((label, tol != tol and "REFUSED" in basis, basis))

    refuses("floor absent from the record refuses", "lowband_level_db", "BD", 0.25,
            _repeat_doc(metric="band energy 20-200"))
    # verdicts()' own machine_over_estimator < 1 rule: a session spread the
    # estimator's editing noise could have produced is not the machine.
    refuses("floor below the apparatus refuses", "lowband_level_db", "BD", 0.25,
            _repeat_doc(floor=0.01, span=0.04))
    refuses("floor with no editing-noise span refuses", "lowband_level_db", "BD", 0.25,
            {"session_to_session": {"metrics": {"lowband_level_db":
                                                {"abs_diff_median": 0.04}}}})
    refuses("a voice with no second session refuses", "lowband_level_db", "SD", 0.25,
            _repeat_doc(floor=0.04))
    refuses("a floor at or above the knob travel refuses", "lowband_level_db", "BD", 0.04,
            _repeat_doc(floor=0.04))
    # THE LIVE STATE, pinned where the controls are read rather than only in a
    # test: the committed #111 record predates the registration and holds no
    # entry for either metric, so nothing is on the board today.
    for metric in pb.METRICS:
        refuses(f"the shipped #111 record has no floor for {metric}", metric, "BD", 0.25, None)
    return out


class _NumpyWithRectangularWindow:
    """`numpy`, except `hanning` is a rectangular window. Installed as
    `promoted_measures.np` so the SHIPPED `lowband_level_db` body executes with
    the wrong window -- the mutant is in the estimator, not beside it."""

    def __getattr__(self, name):
        return getattr(np, name)

    @staticmethod
    def hanning(n):
        return np.ones(n)


def _wrap(name, **override):
    """The shipped estimator `pm.<name>`, called with one argument forced to a
    wrong value (sample rate, band, a disabled check). The real body runs."""
    real = getattr(pm, name)

    def mutant(x, sr, *a, **k):
        if "sr" in override:
            sr = override["sr"]
        k.update({kk: v for kk, v in override.items() if kk != "sr"})
        if "band" in override and a:
            a = a[1:]
        return real(x, sr, *a, **k)
    return mutant


def _onset_truncating_the_window():
    """`lowband_onset_db` that CLIPS the window to the end of the clip instead
    of refusing. The most plausible way this estimator would be written by
    someone not thinking about it -- `x[a:a+n]` already truncates silently in
    Python, so the shipped version's `a + n > len(x)` check is the only thing
    standing between a caller and a number for a window that was not there."""
    real = pm.lowband_onset_db

    def mutant(x, sr, band=pm.DEFAULT_LOWBAND_HZ, window_ms=pm.ONSET_WINDOW_MS,
               *, start_ms=0.0, **k):
        a = int(round(start_ms * sr / 1000.0))
        n = int(round(window_ms * sr / 1000.0))
        if a + n > len(np.asarray(x)):
            avail = 1000.0 * (len(np.asarray(x)) - a) / sr
            return real(x, sr, band, avail, start_ms=start_ms, **k)
        return real(x, sr, band, window_ms, start_ms=start_ms, **k)
    return mutant


def _frequency_without_parabolic_interpolation():
    """`audio_measure.dominant_frequency` reporting the raw argmax bin.

    Mutating the SHARED estimator rather than the promoted wrapper on purpose:
    the glide cases' whole claim is about sub-bin accuracy, and a 240 ms clip
    at 44.1 kHz has 4.2 Hz bins -- 4.6 % at 90 Hz, three times the tolerance.
    If the interpolation ever went away, the glide characterisation in
    `promoted_measures`' header would be false and nothing else would notice."""
    real = am.dominant_frequency

    def mutant(x, lo, hi, sr=am.SR_DEFAULT, **k):
        e = real(x, lo, hi, sr, **k)
        if not e.ok:
            return e
        step = float(sr) / len(np.asarray(x, dtype=float))
        return am.Estimate(round(e.value / step) * step, True, "", e.detail)
    return mutant


def _floor_ignoring_missing_entry():
    """`floor_for` that answers a number when the record has no entry -- the
    way a reader with a `.get(metric, {}).get(k, DEFAULT)` in it would.

    The real `floor_for` is captured HERE, when the factory runs, because by
    the time the mutant is called the attribute it would otherwise look up is
    the mutant itself."""
    real = pb.floor_for

    def mutant(metric, voice, doc=None):
        entry, _why = real(metric, voice, doc)
        if entry is None:
            return dict(value=0.04, source="mutant default"), None
        return entry, None
    return mutant


def _floor_without_the_apparatus_guard(metric, voice, doc=None):
    """`harness_floor` with `verdicts`' machine_over_estimator rule removed:
    the session spread is used even when the estimator's own editing noise
    could have produced all of it."""
    if voice != pb.FLOOR_VOICE:
        return None, "not the floor voice"
    d = pb._load(doc)
    m = ((d or {}).get("session_to_session") or {}).get("metrics") or {}
    if metric not in m:
        return None, "no entry"
    v = m[metric].get("abs_diff_median")
    if v is None or float(v) <= 0.0:
        return None, "not positive"
    return dict(value=float(v), source="mutant: no apparatus guard"), None


def _arithmetic_mean_tolerance():
    """`band_tolerance` with (floor+ceiling)/2 in place of sqrt(floor*ceiling).
    Both are 'between the two bounds'; only one is equidistant from them."""
    real = pb.floor_for

    def mutant(metric, voice, ceiling, doc=None):
        fl, why = real(metric, voice, doc)
        if fl is None:
            return float("nan"), f"{pb.BASIS} (REFUSED: {why})"
        floor = float(fl["value"])
        if ceiling is None or not (0.0 < floor < ceiling):
            return float("nan"), f"{pb.BASIS} (REFUSED: mutant)"
        return 0.5 * (floor + ceiling), pb.BASIS
    return mutant


#: (control, module holding the thing replaced, its attribute, factory for the
#: replacement, the NAMED known case that must go red). Each satisfies rule
#: 5's three conditions, which `injected_bugs` checks rather than assumes: the
#: clean run passes that case, the mutant executes (the replacement is what
#: `known_cases` calls), and that case is the one that fails.
#:
#: Not here, on purpose: patching `pm.DEFAULT_LOWBAND_HZ` would be a control
#: that cannot activate -- the default is bound when the function is defined,
#: so the shipped code never reads the patched constant.
MUTANTS = (
    ("rectangular window in place of Hann", pm, "np",
     lambda: _NumpyWithRectangularWindow(), "lowband a_hi=1.0"),
    ("band upper edge a decade off (2000 Hz for 200 Hz)", pm, "lowband_level_db",
     lambda: _wrap("lowband_level_db", band=(40.0, 2000.0)), "lowband a_hi=0.2"),
    ("leakage floor disabled (answers below its own floor)", pm, "lowband_level_db",
     lambda: _wrap("lowband_level_db", floor_db=-np.inf), "band below floor refuses"),
    ("sample rate taken as 48000 for a 44100 clip", pm, "dominant_period_ms",
     lambda: _wrap("dominant_period_ms", sr=48000), "period 90.0 Hz"),
    ("prominence check disabled (any argmax is a period)", pm, "dominant_period_ms",
     lambda: _wrap("dominant_period_ms", min_prominence_db=-np.inf), "noise has no period"),
    # The time-resolved reading. Each of these is a way a windowed number could
    # be a number for a window other than the one named.
    ("onset window taken from 160 ms instead of the onset", pm, "lowband_onset_db",
     lambda: _wrap("lowband_onset_db", start_ms=160.0),
     "onset 0-80 ms reads an early low band the whole clip misses"),
    # Unlike `DEFAULT_LOWBAND_HZ`, this constant IS read at call time rather
    # than bound as a default, so patching it reaches the shipped body.
    ("the window's resolvability precondition disabled", pm, "MIN_CYCLES_OF_LOWER_EDGE",
     lambda: 0.0, "onset 30 ms on a 40 Hz edge refuses"),
    ("window truncated to the clip instead of refusing", pm, "lowband_onset_db",
     _onset_truncating_the_window, "onset window past the clip end refuses"),
    ("edge-straddle guard disabled (answers over a band edge)", pm, "EDGE_LEAK_MAX",
     lambda: np.inf, "onset refuses a 50 Hz line against a 40 Hz edge"),
    ("dominant line not interpolated (raw argmax bin)", am, "dominant_frequency",
     _frequency_without_parabolic_interpolation,
     "glide 20 % reads the settled frequency"),
    # The floor reader. Each of these is a way a tolerance could appear from a
    # record that does not contain one.
    ("floor reader defaults a missing entry to a number", pb, "floor_for",
     _floor_ignoring_missing_entry, "floor absent from the record refuses"),
    ("floor reader drops the apparatus guard", pb, "floor_for",
     lambda: _floor_without_the_apparatus_guard, "floor below the apparatus refuses"),
    ("tolerance takes the arithmetic mean of floor and ceiling", pb, "band_tolerance",
     _arithmetic_mean_tolerance, "floor 0.04 x ceiling 0.25 -> sqrt"),
)


def _run_with(module, attr, replacement) -> dict:
    """known_cases() with `<module>.<attr>` replaced; restored afterwards."""
    saved = getattr(module, attr)
    setattr(module, attr, replacement)
    try:
        return {label: ok for label, ok, _d in known_cases()}
    finally:
        setattr(module, attr, saved)


def injected_bugs() -> list:
    """(control, expected red case, caught, detail). `caught` is True only if
    the named case passes clean AND fails under the mutant. Reporting which
    OTHER cases went red is detail, not the verdict: `any(...)` would let a
    mutant that broke something unrelated stand in for the intended assertion."""
    clean = {label: ok for label, ok, _d in known_cases()}
    out = []
    for label, module, attr, make, case in MUTANTS:
        if case not in clean:
            out.append((label, case, False, "named case does not exist"))
            continue
        broken = _run_with(module, attr, make())
        red = sorted(k for k, ok in broken.items() if not ok)
        caught = clean[case] and not broken[case]
        out.append((label, case, caught, f"{len(red)} case(s) red: {', '.join(red)}"))
    return out


def start_red() -> list:
    """Rule 1: the harness run against stubs with the right signature and no
    behaviour. (stub, red labels, green labels). Two stubs, because a harness
    can pass a stub two different ways:

    * `answers 0.0` -- answers ok=True with a constant, like an RTL output
      stuck at a value. Every value case and every refusal case must be red.
      'gain cancels' stays GREEN against it, because a constant does cancel a
      gain: that case has no power on its own and is not counted as a control.
    * `always refuses` -- the analogue of X. Every value case must be red; the
      refusal cases are green, correctly, and are covered by the mutants above.

    The FLOOR READER is stubbed in the same two flavours and in the same run,
    because the floor cases are part of `known_cases` and a stub that left
    them untouched would make the harness look like it had more power against
    a stub than it has."""
    stubs = (
        ("answers 0.0", lambda *a, **k: pm.Estimate(0.0, True, "", {}),
         lambda *a, **k: (dict(value=0.0, source="stub"), None)),
        ("always refuses", lambda *a, **k: pm.Estimate(float("nan"), False, "stub", {}),
         lambda *a, **k: (None, "stub")),
    )
    out = []
    for name, stub, floor_stub in stubs:
        saved = (pm.lowband_level_db, pm.lowband_onset_db, pm.dominant_period_ms,
                 pb.floor_for)
        pm.lowband_level_db = pm.lowband_onset_db = pm.dominant_period_ms = stub
        pb.floor_for = floor_stub
        try:
            res = known_cases()
        finally:
            (pm.lowband_level_db, pm.lowband_onset_db, pm.dominant_period_ms,
             pb.floor_for) = saved
        out.append((name, [l for l, ok, _ in res if not ok], [l for l, ok, _ in res if ok]))
    return out


#: Every FLOOR case whose correct answer is a refusal. Both stubs refuse (the
#: `answers 0.0` one by handing back a floor of 0.0, which fails the
#: `0 < floor` guard), so a refusal case cannot distinguish a working reader
#: from a dead one and has no power here. It is the three floor MUTANTS above
#: that carry these, and all three are CAUGHT -- listing the cases rather than
#: pretending to a start-red result they do not have is the point of this set.
_FLOOR_REFUSALS = {
    "floor absent from the record refuses",
    "floor below the apparatus refuses",
    "floor with no editing-noise span refuses",
    "a voice with no second session refuses",
    "a floor at or above the knob travel refuses",
    # DERIVED from `promoted_bands.METRICS` rather than listed, because
    # `floor_cases` generates one of these per metric: a hand-written list goes
    # stale the moment a metric is promoted, and a stale entry here reads as a
    # case the stub is ALLOWED to pass.
} | {f"the shipped #111 record has no floor for {m}" for m in pb.METRICS}

#: What each stub is REQUIRED to leave green; anything else green is a case
#: the stub passes, i.e. a hole in the harness.
STUB_MAY_PASS = {
    # a_hi=0.0's exact answer IS 0.0 dB (all power in band): it checks for
    # leakage, not for an answer, and has no power against this stub.
    "answers 0.0": {"gain cancels", "lowband a_hi=0.0"} | _FLOOR_REFUSALS,
    "always refuses": {"silence refuses", "short clip refuses", "band below floor refuses",
                       "noise has no period",
                       # The one onset precondition case that asks only "did it
                       # refuse". The other two name the REASON they must
                       # refuse for, so a stub that refuses everything for its
                       # own reason fails them -- which is why they are worded
                       # that way and are not listed here.
                       "onset window after the low band refuses",
                       } | _FLOOR_REFUSALS,
}


def period_error_on_known_cases() -> float:
    """Worst |relative error| of `dominant_period_ms` over the known period
    cases, in %. These are STEADY exponentially decaying sines; the estimator's
    error on a GLIDE is a different and larger number, and is
    `glide_error_vs_settled_pct` below."""
    worst = 0.0
    for f0 in (60.0, 90.0, 190.0, 410.0):
        for detune in (0.0, 0.02, 0.07):
            f = f0 * (1 + detune)
            e = pm.dominant_period_ms(_tone(f), SR, (40.0, f0 * 2.0))
            worst = max(worst, abs(e.value / (1000.0 / f) - 1.0) * 100.0)
    return worst


def glide_error_vs_settled_pct() -> float:
    """Worst |error| of `dominant_period_ms` against a GLIDE's SETTLED
    frequency, in %, over `GLIDE_DEPTHS`.

    The companion to `period_error_on_known_cases`, and the reason the two are
    separate functions rather than one number: an 808 tom glides, so the steady
    figure is not its error on a tom. Measured 1.12 % at 20 % depth against
    0.068 % on a steady tone -- sixteen times larger, and in a known
    direction (upward, toward the start of the glide)."""
    worst = 0.0
    for depth in GLIDE_DEPTHS:
        x, _f_bar = _glide(90.0, depth)
        e = pm.dominant_period_ms(x, SR, (40.0, 200.0))
        if not (e.ok and e.value):
            return float("nan")
        worst = max(worst, abs(100.0 * ((1000.0 / e.value) / 90.0 - 1.0)))
    return worst


def glide_report() -> list:
    """(depth, f(end), weighted mean, read, err vs settled, err vs mean) --
    the characterisation, printed by `validate` so the number a reader quotes
    comes out of a run rather than out of a docstring. 40 % is included here
    and NOT gated: it is past an 808 tom's sweep, so it is evidence about the
    estimator's shape rather than a requirement."""
    rows = []
    for depth in tuple(GLIDE_DEPTHS) + (0.40,):
        x, f_bar = _glide(90.0, depth)
        e = pm.dominant_period_ms(x, SR, (40.0, 200.0))
        got = (1000.0 / e.value) if (e.ok and e.value) else float("nan")
        rows.append((depth, 90.0, f_bar, got, 100.0 * (got / 90.0 - 1.0),
                     100.0 * (got / f_bar - 1.0)))
    return rows


def cmd_validate() -> int:
    bad = 0
    for name, red, green in start_red():
        holes = sorted(set(green) - STUB_MAY_PASS[name])
        print(f"{'RED' if not holes else 'HOLE'}  start-red stub '{name}': "
              f"{len(red)} red, {len(green)} green{'; passes stub: ' + ', '.join(holes) if holes else ''}")
        bad += bool(holes)
    for label, ok, detail in known_cases():
        print(f"{'PASS' if ok else 'FAIL'}  {label}  {detail}")
        bad += not ok
    for label, case, caught, detail in injected_bugs():
        print(f"{'CAUGHT' if caught else 'NOT-CAUGHT'}  injected: {label} -> "
              f"'{case}' {'red' if caught else 'NOT red'} ({detail})")
        bad += not caught
    print(f"period estimator worst error on steady decaying tones: "
          f"{period_error_on_known_cases():.3f} %")
    print(f"period estimator worst error against a GLIDE's settled frequency "
          f"({', '.join(f'{d*100:.0f}' for d in GLIDE_DEPTHS)} % depth): "
          f"{glide_error_vs_settled_pct():.3f} %")
    for depth, f_end, f_bar, got, e_set, e_mean in glide_report():
        print(f"  glide depth {depth*100:5.1f} %: f(end) {f_end:6.2f}, "
              f"energy-weighted mean {f_bar:6.2f}, read {got:6.2f} Hz -> "
              f"{e_set:+6.2f} % vs settled, {e_mean:+6.2f} % vs the mean"
              f"{'   [reported, not gated]' if depth not in GLIDE_DEPTHS else ''}")
    print("validate:", "PASS" if not bad else f"FAIL ({bad})")
    return 1 if bad else 0


def cmd_measure() -> int:
    if cmd_validate() != 0:
        print("REFUSED: estimators did not validate; no number is reported", file=sys.stderr)
        return 2
    if not REFS.is_dir():
        print(f"REFUSED: reference corpus not found at {REFS}", file=sys.stderr)
        return 2
    import test_discrimination as td
    laws = td.fit_laws(str(REFS), True)
    rows, ceilings = [], {}
    for voice in td.ALL_REF:
        clips = [c for c in td.ref_clips(str(REFS), [voice], True)]
        if not clips:
            continue
        per = {}
        for c in clips:
            x, sr = td.read_wav(c.path)
            y = td.condition(x, sr, level_match=False)
            lb = pm.lowband_level_db(y, sr)
            # The time-resolved reading, at the default 40-200 Hz / 80 ms pair.
            # Reported ALONGSIDE the whole-clip one, never instead of it: the
            # pair is the measurement, because their difference is where in the
            # clip the low band sits, and that is what 5c asked for.
            lo_on = pm.lowband_onset_db(y, sr)
            pr = (pm.dominant_period_ms(y, sr, (40.0, td.TUNING_FMAX[voice]))
                  if voice in PERIOD_VOICES else None)
            per[c.knobs] = dict(
                lowband=lb.value if lb.ok else None, lowband_why=None if lb.ok else lb.reason,
                onset=lo_on.value if lo_on.ok else None,
                onset_why=None if lo_on.ok else lo_on.reason,
                period=pr.value if pr is not None and pr.ok else None,
                period_why=None if pr is None or pr.ok else pr.reason, test=c.is_test,
                # the study's own f0 reader (0.3 s window, argmax bin), as the
                # independent cross-check for the period estimator
                f0_td=(td.measure_f0(y, sr, td.TUNING_FMAX[voice])
                       if voice in PERIOD_VOICES else None))
        for metric, key in (("lowband_level_db", "lowband"),
                            ("lowband_onset_db", "onset"),
                            ("dominant_period_ms", "period")):
            v = [d[key] for d in per.values() if d[key] is not None]
            ceilings[f"{metric}/{voice}"] = dict(
                ceiling=(max(v) - min(v)) if len(v) > 1 else None, n=len(v), of=len(per),
                refused=len(per) - len(v))
        # ours at the held-out settings, against the machine at the same setting
        for c in clips:
            if not c.is_test or not td.KNOB_NAMES.get(voice):
                continue
            xo, so = td.render(voice, c.knobs, laws, "ours")
            yo = td.condition(xo, so, level_match=False)
            lo_ = pm.lowband_level_db(yo, so)
            on_ = pm.lowband_onset_db(yo, so)
            po = (pm.dominant_period_ms(yo, so, (40.0, td.TUNING_FMAX[voice]))
                  if voice in PERIOD_VOICES else None)
            r = per[c.knobs]
            row = dict(voice=voice, knobs=list(c.knobs))
            if lo_.ok and r["lowband"] is not None:
                row["lowband_ours_minus_real_db"] = lo_.value - r["lowband"]
            if on_.ok and r["onset"] is not None:
                row["onset_ours_minus_real_db"] = on_.value - r["onset"]
                # The whole point of carrying both: a difference that is larger
                # in the 80 ms window than over the whole clip is energy placed
                # early, which is finding 1 of docs/discrimination.md 6.
                if r["lowband"] is not None and lo_.ok:
                    row["onset_minus_wholeclip_delta_db"] = (
                        (on_.value - r["onset"]) - (lo_.value - r["lowband"]))
            if po is not None and po.ok and r["period"] is not None:
                row["period_ours_over_real_pct"] = 100.0 * (po.value / r["period"] - 1.0)
                row["freq_pct_sharp"] = 100.0 * (r["period"] / po.value - 1.0)
                row["freq_pct_sharp_study_f0"] = 100.0 * (
                    td.measure_f0(yo, so, td.TUNING_FMAX[voice]) / r["f0_td"] - 1.0)
            rows.append(row)
    out = dict(
        ruler="raw dB / ms of the named estimator on conditioned clips; NOT voice_scale or ours_distance",
        commit=subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                              cwd=ROOT).stdout.strip(),
        dirty=bool(subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                                  capture_output=True, text=True, cwd=ROOT).stdout.strip()),
        machine_floor=pb.MACHINE_FLOOR, ceilings=ceilings, held_out=rows,
        tolerances={k: pb.band_tolerance(k.split("/")[0], k.split("/")[1], v["ceiling"])[1]
                    for k, v in ceilings.items()})
    OUT.write_text(json.dumps(out, indent=1, default=float) + "\n")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("validate", "measure"))
    sys.exit({"validate": cmd_validate, "measure": cmd_measure}[ap.parse_args().cmd]())
