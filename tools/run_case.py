#!/usr/bin/env python3
"""Fill the scorecard: render our side, load the reference side, measure, write
the result JSON that `tools/scorecard.py` renders.

    tools/run_case.py D01A                     one case
    tools/run_case.py --batch "First 32"       a batch from docs/scorecard/cases.csv
    tools/run_case.py --list                   what is covered, what is not, and why
    tools/run_case.py --inject REF_F0_20PCT D01A --results /tmp/x   an injected control

`docs/scorecard/cases.csv` names 100 cases and `tools/scorecard.py` renders the
board from `docs/scorecard/results/<case_id>.json`. Until this file existed,
nothing wrote those. This does.

WHAT IT REFUSES TO DO, because each is a way a scorecard starts lying
---------------------------------------------------------------------

**An invalid measurement gets no distance, not zero.** Every estimator here
returns `audio_measure.Estimate`, which may be a refusal. A refusal is written
as `"valid": false` with the reason and **no `error` key at all**. Zero would
read on the board as a perfect match.

**A case we cannot run is not quietly dropped.** It is either written as a
no-verdict with the reason (we tried, the apparatus said no) or listed by
`--list` as deliberately not run with the reason (we did not try, and why).
Both are in `COVERAGE` below; neither is silent.

**Every result names its engine.** `fixed-model` for everything here: the
integer models `model/drums_fx.py` and `model/voice_fx.py`, in this process,
never a committed WAV. No case here has been measured on the integrated RTL,
and `tools/scorecard.py` says so out loud on the board.

**Tolerances are frozen before the measurement, from the reference's own
documentation, and are never per case.** Three classes, chosen before any
number was computed (see `TOLERANCE_POLICY`). Retuning a tolerance after
seeing an error is fitting around a deficiency, and the board would still
look green.

**The apparatus asserts its preconditions at the point of use and REFUSES
rather than reports.** The reference corpus must be present, the named file
must exist, the sound must be in the kit, and the reference clip must not be
silent. Each failure produces a stated no-verdict, never a number.

**And it asserts the premise of the BATCH before any of it runs.** An earlier
run of this file returned eight honest per-case refusals -- "the eight-stop kit
does not implement LC / MT / MC / HC / CL / RS / MA / CY" -- from a worktree
that was two commits behind `origin/main`, where the complete sixteen-sound kit
had already landed. Every one of those records was true of the tree it had and
false about the project, and eight of them together read as a permanent hole in
the instrument. `base_check` now refuses the whole batch when the tree is
behind `origin/main` or its drum-circuit count differs from it, because a stale
premise is a property of the checkout and must never come out looking like a
property of the instrument.

THE CONTROLS (`--inject`)
-------------------------

A runner's only failure mode that matters is a false green, so the two states
that are easy to get wrong are injectable and are checked in
`tools/test_run_case.py` and by `make controls`:

    REF_F0_20PCT   shift the reference pitch by 20 %, which is twice the
                   frequency tolerance: the case MUST come back `fail`
    REF_MISSING    point the reference at a file that is not there: the case
                   MUST come back `no verdict`, with a stated reason

`--inject` refuses to write into `docs/scorecard/results`. A control's output
is not evidence about the instrument and must never be mistaken for it.

PROVENANCE, AND THE EXIT CODE
-----------------------------

Nothing else in this repository records what it ran against -- no verifier here
calls `rev-parse` -- so a result cannot be told apart from a stale one, and with
many worktrees live at once that is not hypothetical. Every result carries a
`provenance` block: the commit AND a hash of the uncommitted tree (a clean SHA
that silently means "plus whatever was in the working tree" is worse than no
SHA), the exact command and configuration, content hashes of every generated
input including the reference recording, the path to the raw artefact so a
number can be re-derived rather than re-trusted, and the engine.
`tools/scorecard.py` gives **no verdict** to a result without one.

The exit status follows the repository's verifier convention, and the same code
is written onto each record as `provenance.outcome_code`:

    0  match         every requested case passed
    1  mismatch      at least one case was measured and scored badly -- a RESULT
    2  did not run   at least one case produced no evidence: no verdict, not run,
                     or an internal error

A case with no evidence and a case that was measured and failed need opposite
responses, so they are never the same code and never the same cell on the board.
`--expect` switches to control semantics: exit 0 exactly when every case landed
in the stated state.
"""
from __future__ import annotations

import argparse
import csv
import datetime
import hashlib
import json
import math
import os
import pathlib
import subprocess
import sys
import traceback
import wave

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "audition"))
sys.path.insert(0, str(ROOT / "tools"))

import numpy as np                                                   # noqa: E402
from scipy.io import wavfile                                         # noqa: E402
from scipy.signal import butter, sosfiltfilt                         # noqa: E402

import audio_measure as am                                           # noqa: E402
import drum_verify as dv                                             # noqa: E402
import refprofile as rp                                              # noqa: E402
import mono_m5a_score as mono_m5a                                    # noqa: E402

CASES_CSV = ROOT / "docs" / "scorecard" / "cases.csv"
RESULTS = ROOT / "docs" / "scorecard" / "results"
AUDIO_OUT = ROOT / "build" / "scorecard"

ENGINE = "fixed-model"          # the integer model, rendered in this process
SR_OURS = 48000

# The reference corpus. Not committed -- fetch it yourself:
#   git clone --depth 1 https://github.com/tidalcycles/sounds-tr808-fischer /tmp/tr808-ref
REFS_ENV = "GF180_TR808_REFS"
REFS_DEFAULT = "/tmp/tr808-ref"
REF_ID = ("Fischer/Technopolis 1994, CC0-1.0 via TidalCycles, real TR-808 "
          "s/n 103852, individual voice outputs, 16-bit/44.1 kHz")


# ===========================================================================
# 1. The tolerance policy. Frozen here, before any measurement, and sourced.
# ===========================================================================
TOLERANCE_POLICY = {
    "frequency": "10 % of the reference value -- the TR-808's own component "
                 "tolerance on an oscillator's f0 (docs/tr808-reference.md 1.7)",
    "time": "50 % of the reference value -- 1.7 states +-50 % on Q, and for "
            "these bridged-T resonators tau is proportional to Q",
    "energy ratio": "3 dB, the half-power convention. A stated convention, not "
                    "a number derived from any error of ours",
    "event timing": "10 ms -- the positional accuracy audio_measure.onsets "
                    "states for itself; a failure is larger than the estimator's "
                    "own resolution",
    "bus sum": "0.5 dB -- the final output must be the sum of the per-bus stems",
    "rail": "0.01 % of samples at the rail, a stated budget for a render with "
            "no limiter in the path",
    "rolloff": "1.5 dB per octave -- audio_measure.slope_db_oct's own "
               "max_residual_db. That estimator REFUSES a straight-line fit "
               "whose RMS residual exceeds it, so a difference smaller than it "
               "is inside the fit's own scatter and is not a difference. A "
               "number the estimator states about itself, not one derived from "
               "any error of ours",
}


def tol_frequency(ref: float, ctx: dict) -> tuple:
    return abs(ref) * 0.10, "frequency"


def tol_frequency_of_f0(ref: float, ctx: dict) -> tuple:
    """For a metric that is a DIFFERENCE of two frequencies, 10 % of a
    difference is not the machine's tolerance -- 10 % of the voice's own
    steady f0 is."""
    f0 = ctx.get("ref_f0")
    if f0 is None:
        return abs(ref) * 0.10, "frequency"
    return abs(f0) * 0.10, "frequency (10 % of the reference steady f0)"


def tol_time(ref: float, ctx: dict) -> tuple:
    return abs(ref) * 0.50, "time"


def tol_db(ref: float, ctx: dict) -> tuple:
    return 3.0, "energy ratio"


def tol_fixed(value: float, basis: str):
    def f(ref: float, ctx: dict) -> tuple:
        return value, basis
    return f


# ===========================================================================
# 2. Estimators
#
# Everything that can be is `model/audio_measure.py`, which is ground-truthed
# against closed-form signals in `model/test_audio_measure.py`. The four added
# here are ground-truthed the same way in `tools/test_run_case.py`, named on
# each function -- six estimator bugs were found the day that suite was
# written, and an estimator that has never met a signal with a known answer is
# not a measurement.
# ===========================================================================
def band_ratio_db(x, sr: int, split_hz: float, lo: float = 0.0,
                  hi: float | None = None, *, floor_db: float = -80.0) -> am.Estimate:
    """10*log10(energy above `split_hz` / energy below it), both inside
    [lo, hi]. The dimensionless balance the drum work reports as a body/air or
    body/noise split, in a unit that combines with a dB tolerance.

    The energy comes from `audio_measure.band_energy`, which filters rather
    than summing FFT bins. That is not a detail: `spectrum` applies a Hann
    window, so on a decaying voice it weights the middle of the file and
    reports the TAIL's spectrum instead of the event's energy -- on a real
    TR-808 cymbal the two disagree by a factor of four in the 5-9 kHz band,
    and the windowed answer is the misleading one. This function summed FFT
    bins until that was found.

    Refuses when either side is empty, or when the ratio is past `floor_db`:
    a band with nothing in it has no balance, it has an absence.

    Ground truth: test_band_ratio_db_of_two_sines_is_their_amplitude_ratio."""
    hi = min(sr / 2.0 - 1.0, 20000.0 if hi is None else hi)
    lo = max(lo, 1.0)
    if hi <= split_hz or split_hz <= lo:
        return am.Estimate(None, False, "the split is outside the band",
                           dict(lo=lo, split=split_hz, hi=hi))
    e_lo, e_hi = am.band_energy(x, ((lo, split_hz), (split_hz, hi)), sr)
    if e_lo <= 0.0 or e_hi <= 0.0:
        return am.Estimate(None, False, "one side of the split holds no energy",
                           dict(e_lo=e_lo, e_hi=e_hi))
    r = 10.0 * math.log10(e_hi / e_lo)
    if r < floor_db or r > -floor_db:
        return am.Estimate(None, False, "band ratio past the stated floor",
                           dict(ratio_db=r, floor_db=floor_db))
    return am.Estimate(r, True, "", dict(e_lo=e_lo, e_hi=e_hi))


def band_pair_db(x, sr: int, band_a, band_b, *, floor_db: float = -80.0) -> am.Estimate:
    """10*log10(energy in `band_a` / energy in `band_b`), for a voice whose
    balance is between two named partials rather than either side of one
    split -- the rimshot's two bridged-T modes, the cymbal's bands.

    Ground truth: test_band_pair_db_of_two_sines_is_their_amplitude_ratio."""
    e_a, e_b = am.band_energy(x, (tuple(band_a), tuple(band_b)), sr)
    if e_a <= 0.0 or e_b <= 0.0:
        return am.Estimate(None, False, "one of the two bands holds no energy",
                           dict(e_a=e_a, e_b=e_b))
    r = 10.0 * math.log10(e_a / e_b)
    if r < floor_db or r > -floor_db:
        return am.Estimate(None, False, "band ratio past the stated floor",
                           dict(ratio_db=r, floor_db=floor_db))
    return am.Estimate(r, True, "", dict(e_a=e_a, e_b=e_b))


def pitch_drop_hz(x, sr: int, band, *, early=(0.004, 0.018), late=(0.060, 0.150),
                  smooth_ms: float = 3.0, origin: int = 0) -> am.Estimate:
    """How far the voice's pitch falls between an early and a late window, in
    Hz, from the analytic phase derivative (`audio_measure.instantaneous_
    frequency`, ground-truthed against a known glide) of the band-limited body.

    NOT two windowed FFTs. A 30 ms window holds under three periods of a 90 Hz
    tom, so a spectrum of it cannot resolve the pitch at all, and the first
    version of this measurement was that -- it read the reference's drop as
    1.2 Hz where a phase derivative reads 7.

    Refuses when either window falls below `-30 dB` of the peak envelope,
    where the phase derivative is noise.

    **Its floor is about 2 Hz** -- the band-pass transient biases the early
    window by that much on a tone that does not move at all
    (`test_pitch_drop_hz_is_zero_for_a_steady_tone`). A reading inside +-2 Hz
    is "no measurable sweep", not "a small sweep".

    Ground truth: test_pitch_drop_hz_on_a_known_exponential_glide."""
    lo, hi = band
    x = np.asarray(x, dtype=np.float64)
    if am.is_silent(x):
        return am.Estimate(None, False, "silent", {})
    from scipy.signal import butter as _butter, sosfiltfilt as _sos
    sos = _butter(4, [max(lo, 5.0) / (sr / 2.0), min(hi, sr / 2.0 - 1.0) / (sr / 2.0)],
                  btype="band", output="sos")
    # The whole record, lead included: this is the one estimator here that
    # filters everything it is given, so the segment it hands `sosfiltfilt`
    # already begins in `prepare()`'s guaranteed silence. `origin` is where
    # t = 0 sits in it, so `early` and `late` mean what they meant before.
    y = _sos(sos, x)
    env = am.analytic_envelope(y)
    fi = am.instantaneous_frequency(y, sr, smooth_ms=smooth_ms)
    pk = float(env.max())
    out = []
    for name, (t0, t1) in (("early", early), ("late", late)):
        a, b = origin + int(t0 * sr), min(len(fi), origin + int(t1 * sr))
        if b - a < 16:
            return am.Estimate(None, False, f"{name} window too short", dict(n=b - a))
        if float(env[a:b].max()) < pk * 10 ** (-30.0 / 20.0):
            return am.Estimate(None, False,
                               f"{name} window is below -30 dB, where the phase "
                               f"derivative is noise", dict(window=name))
        out.append(float(np.median(fi[a:b])))
    return am.Estimate(out[0] - out[1], True, "", dict(early_hz=out[0], late_hz=out[1]))


def attack_ms(x, sr: int, *, window_ms: float = 4.0,
              onset_frac: float = 0.02) -> am.Estimate:
    """Onset to peak of the short-time RMS envelope, in ms.

    The RMS envelope, never the analytic one: a hi-hat's instantaneous
    amplitude beats by tens of dB and its analytic maximum lands on a beat.
    The window smears the answer by up to about one window, which is why the
    ground-truth test states a window-sized bound rather than an exact value,
    and why `docs/drum-verification.md` compares attack ratios rather than
    absolute attack times across different windows.

    Refuses silence and a record whose envelope peaks on its first sample.
    It does NOT refuse an attack faster than its own window -- it cannot see
    one, and reports about a window instead. That floor is stated rather than
    hidden, and it is why both sides of every comparison below are taken with
    the same window.

    Ground truth: test_attack_ms_finds_a_known_linear_rise,
    test_attack_ms_cannot_resolve_an_attack_shorter_than_its_window."""
    x = np.asarray(x, dtype=np.float64)
    if am.is_silent(x):
        return am.Estimate(None, False, "silent", {})
    env = am.rms_envelope(x, window_ms, sr)
    pk = int(np.argmax(env))
    if pk == 0:
        return am.Estimate(None, False, "envelope peaks on the first sample", dict(pk=pk))
    above = np.nonzero(env[: pk + 1] >= onset_frac * env[pk])[0]
    if not len(above):
        return am.Estimate(None, False, "no onset below the peak", dict(pk=pk))
    ms = (pk - int(above[0])) / sr * 1e3
    if ms <= 0.0:
        return am.Estimate(None, False, "no measurable rise", dict(ms=ms))
    return am.Estimate(ms, True, "", dict(peak_index=pk, window_ms=window_ms))


#: How far either side of a NOMINAL partial frequency a real one is looked for.
#: 10 % is the TR-808's own component tolerance on an oscillator's f0
#: (docs/tr808-reference.md 1.7) and the same figure `tol_frequency` uses, so
#: the search covers exactly the range a unit is allowed to sit in.
LINE_SEARCH_FRAC = 0.10

#: How far above its own measured floor a reading has to sit before it is a
#: measurement rather than the estimator. 6 dB, which is the margin
#: `audio_measure.harmonic_signature` already uses for the same decision.
FLOOR_MARGIN_DB = 6.0


def find_line(x, sr: int, hz_nominal: float, *,
              search: float = LINE_SEARCH_FRAC) -> am.Estimate:
    """The frequency of the real partial nearest `hz_nominal`, not
    `hz_nominal`.

    #108: `tone_ratio_db` probed the cowbell at 800 and 540 Hz. **The
    machine's lines are 558.35 and 823.70 Hz.** Probing the same recording at
    trims inside Roland's own +-10 % swings the reference value from 8.79 to
    15.91 dB -- 7.1 dB against a 3.0 dB tolerance, and non-monotonically. The
    unit we have happens to sit where nominal probing reads 15.15 against
    15.21 on its true lines, which is luck and not method: a different 808, or
    this one after a trim adjustment, moves the reference by twice the
    tolerance with the instrument unchanged.

    This is the same failure as `refine_f0` before #87 -- a rig ASSUMING a
    frequency instead of measuring one. There, assuming pitch took a
    per-period residual from 0.6 % to 25 % because Mini V3 played 0.14 cents
    sharp.

    Refuses when the band holds no line, rather than returning the nominal:
    "there is no partial here" and "the partial is exactly where the chart
    says" are opposite findings and must not share a return value."""
    e = am.dominant_frequency(x, hz_nominal * (1 - search), hz_nominal * (1 + search), sr)
    if not e.ok:
        return am.Estimate(None, False,
                           f"no line within +-{search*100:.0f} % of {hz_nominal:.1f} Hz: "
                           f"{e.reason}", dict(nominal_hz=hz_nominal, **(e.detail or {})))
    return am.Estimate(e.value, True, "",
                       dict(nominal_hz=hz_nominal, found_hz=e.value,
                            offset_pct=100.0 * (e.value / hz_nominal - 1.0),
                            **(e.detail or {})))


def _amplitude_at(x, sr: int, hz: float, label: str) -> am.Estimate:
    """A WINDOWED coherent projection, which is the right primitive for a free
    ring: `tone_amplitude`'s rectangular projection is exact only over a whole
    number of periods, and a partial found by measurement never lands on one.
    The window's coherent gain is divided out, so a ratio of two of these is
    exact for partials further apart than its 8-bin main lobe."""
    e = am.windowed_tone_amplitude(x, hz, sr)
    if not e.ok:
        return am.Estimate(None, False, f"{label} {hz:.2f} Hz: {e.reason}", e.detail)
    return e


def tone_ratio_db(x, sr: int, hz_num: float, hz_den: float, *,
                  search: float = LINE_SEARCH_FRAC) -> am.Estimate:
    """Level of the partial NEAR `hz_num` over the partial NEAR `hz_den`, in
    dB: both lines are found in the record and then measured between (#108).

    The nominal frequencies are search centres, not probe points. On this unit
    it changes the answer by 0.06 dB, which is exactly why it is worth doing
    now, while it is a no-op, rather than after a reference swap makes it a
    mystery.

    Ground truth: test_tone_ratio_db_of_two_known_sines,
    test_tone_ratio_db_finds_a_detuned_line_the_nominal_probe_misses."""
    fn, fd = find_line(x, sr, hz_num, search=search), find_line(x, sr, hz_den, search=search)
    if not fn.ok:
        return am.Estimate(None, False, f"numerator: {fn.reason}", fn.detail)
    if not fd.ok:
        return am.Estimate(None, False, f"denominator: {fd.reason}", fd.detail)
    a = _amplitude_at(x, sr, fn.value, "numerator")
    b = _amplitude_at(x, sr, fd.value, "denominator")
    if not a.ok:
        return a
    if not b.ok:
        return b
    if a.value <= 0 or b.value <= 0:
        return am.Estimate(None, False, "a line measured at zero amplitude",
                           dict(num=a.value, den=b.value))
    return am.Estimate(20.0 * math.log10(a.value / b.value), True, "",
                       dict(num=a.value, den=b.value,
                            num_hz=fn.value, den_hz=fd.value,
                            num_nominal_hz=hz_num, den_nominal_hz=hz_den,
                            num_offset_pct=fn.detail["offset_pct"],
                            den_offset_pct=fd.detail["offset_pct"]))


def difference_tone_db(x, sr: int, hz_hi: float, hz_lo: float, *,
                       search: float = LINE_SEARCH_FRAC) -> am.Estimate:
    """Level at the DIFFERENCE of the two real partials, over the upper one,
    in dB.

    The difference tone is not a line to be searched for -- the whole point of
    the metric is that it should not be there -- so its frequency is DERIVED
    from the two lines that were found rather than looked for as a peak. That
    is still #108's fix: the frequency probed comes from measurement and not
    from a chart. Searching for a peak here would be the opposite error,
    because "no peak at the difference frequency" is the PASSING case and must
    not come back as a refusal.

    Ground truth: test_difference_tone_db_probes_the_measured_difference."""
    fh, fl = find_line(x, sr, hz_hi, search=search), find_line(x, sr, hz_lo, search=search)
    if not fh.ok:
        return am.Estimate(None, False, f"upper partial: {fh.reason}", fh.detail)
    if not fl.ok:
        return am.Estimate(None, False, f"lower partial: {fl.reason}", fl.detail)
    f_diff = fh.value - fl.value
    if f_diff <= 0 or f_diff >= sr / 2:
        return am.Estimate(None, False, "the difference frequency is outside (0, Nyquist)",
                           dict(hi_hz=fh.value, lo_hz=fl.value, diff_hz=f_diff))
    a = _amplitude_at(x, sr, f_diff, "difference tone")
    b = _amplitude_at(x, sr, fh.value, "upper partial")
    lo_a = _amplitude_at(x, sr, fl.value, "lower partial")
    if not a.ok:
        return a
    if not b.ok:
        return b
    if not lo_a.ok:
        return lo_a
    if a.value <= 0 or b.value <= 0:
        return am.Estimate(None, False, "a line measured at zero amplitude",
                           dict(num=a.value, den=b.value))

    # THE FLOOR, MEASURED (#92). Two partials 60 dB above the thing being
    # looked for leak into its bin, and a projection that reports that leakage
    # as a difference tone is the "25 dB of separation" failure again -- the
    # rectangular projection this replaced read our render's difference tone at
    # -41 dB where the windowed one reads -102. So the same two partials are
    # resynthesised alone, at the amplitudes measured here, and projected at
    # the same difference frequency: whatever that reads is leakage, because
    # the synthetic signal has no difference tone in it at all.
    n = len(x)
    t = np.arange(n) / sr
    leak = (b.value * np.sin(2 * math.pi * fh.value * t)
            + lo_a.value * np.sin(2 * math.pi * fl.value * t))
    fl_e = am.windowed_tone_amplitude(leak, f_diff, sr)
    floor = float(fl_e.value) if fl_e.ok else 0.0
    floor_db = 20.0 * math.log10(floor / b.value) if floor > 0 else float("-inf")
    value = 20.0 * math.log10(a.value / b.value)
    detail = dict(num=a.value, den=b.value, diff_hz=f_diff,
                  hi_hz=fh.value, lo_hz=fl.value,
                  hi_nominal_hz=hz_hi, lo_nominal_hz=hz_lo,
                  floor_db=floor_db, headroom_db=value - floor_db,
                  floor_margin_db=FLOOR_MARGIN_DB)
    if value < floor_db + FLOOR_MARGIN_DB:
        # `harmonic_signature` already does exactly this -- it returns None for
        # any harmonic within 6 dB of its measured floor -- and that pattern is
        # right. A row at the floor reports the estimator, not the signal.
        return am.Estimate(None, False,
                           f"the difference tone at {f_diff:.1f} Hz reads {value:.1f} dB, "
                           f"within {FLOOR_MARGIN_DB:.0f} dB of the {floor_db:.1f} dB the "
                           f"two partials leak into that bin by themselves: there is no "
                           f"difference tone here to measure, only the window", detail)
    return am.Estimate(value, True, "", detail)


def worst_event_offset_ms(x, sr: int, scheduled_s, *, group_s: float = 0.020) -> am.Estimate:
    """Worst |detected onset - scheduled time| over a render, in ms.

    Scheduled hits closer together than `group_s` are one event: two stops
    struck in the same frame produce one onset and counting them as two would
    make a correct render look like a miss. Refuses -- rather than matching
    greedily and reporting a plausible number -- when the detected count does
    not equal the scheduled group count, because then the pairing is a guess.

    The detector's minimum gap is taken from the SCHEDULE, at half the smallest
    interval in it. That is a property of the stimulus we wrote, not of the
    result: without it, a bass drum whose T20 (348 ms) outlasts the gap between
    its own hits (363 ms) beats against its own ring and reads 12 onsets where
    10 were written. It is never allowed below `audio_measure.onsets`'s own
    20 ms default, so it can only ever reject rises closer together than the
    stimulus can contain.

    Ground truth: test_worst_event_offset_ms_on_bursts_at_known_times,
    test_worst_event_offset_ms_survives_hits_that_ring_into_each_other."""
    groups: list[float] = []
    for t in sorted(scheduled_s):
        if not groups or t - groups[-1] > group_s:
            groups.append(float(t))
    gaps = [b - a for a, b in zip(groups, groups[1:])]
    min_gap = max(0.020, 0.5 * min(gaps)) if gaps else 0.020
    all_found = [i / sr for i in am.onsets(x, sr, min_gap_s=min_gap)]
    lo, hi = groups[0] - 0.020, groups[-1] + min_gap
    found = [t for t in all_found if lo <= t <= hi]
    outside = len(all_found) - len(found)
    detail = dict(scheduled=len(groups), detected_in_window=len(found),
                  min_gap_s=round(min_gap, 4), onsets_outside_the_schedule=outside)
    if len(found) != len(groups):
        return am.Estimate(None, False,
                           "detected onsets do not match the scheduled events", detail)
    worst = max(abs(f - g) for f, g in zip(found, groups)) * 1e3
    return am.Estimate(worst, True, "", detail)


# ===========================================================================
# 3. Signal preparation, identical on both sides. Nothing is resampled: the
#    references are 44.1 kHz and our renders 48 kHz, and every metric here is
#    rate-independent.
#
#    THE PRE-ONSET LEAD (#101, #103, docs/analysis-conventions.md sections 0-2)
#    --------------------------------------------------------------------------
#    `scipy.signal.sosfiltfilt` defaults to `padtype='odd'` and pads by
#    `3*(2*len(sos)+1 - ...)` samples, extended ODDLY through the first sample.
#    Hand it a segment that begins at full amplitude and it manufactures an
#    edge; hand it one that begins in silence and the extension is exactly
#    zero. So the lead a segment carries before the strike is not cosmetic --
#    it decides whether a band split is the sound or the filter.
#
#    `prepare()` used to trim to `max(0, onset - 1 ms)`, and **the `max(0, ...)`
#    was the whole bug**: every one of the sixteen Fischer references crosses
#    2 % of peak within 5-52 samples, so the clamp fired on all sixteen and the
#    reference side got 0.11-1.18 ms of lead while our renders -- which begin
#    with 10 ms of digital silence -- got exactly 1.00 ms. Two sides of every
#    comparison, filtered under different boundary conditions, and nothing in
#    the record said so. #101 measured 6.07 dB of it against a 3.0 dB
#    tolerance on the congas.
#
#    Both numbers are inside or barely outside the pad, which is why neither is
#    enough. The pad is COMPUTED here rather than quoted, because both issues
#    quoted 12-15 samples: that is the figure for a 4th-order LOW-pass. The
#    band-pass this code builds is 8th order overall -- 4 sections, 27 samples,
#    0.562 ms at 48 kHz and 0.612 ms at 44.1 kHz.
# ===========================================================================
def _sosfiltfilt_padlen(sos) -> int:
    """`scipy.signal.sosfiltfilt`'s own default pad length, from its own
    formula. Computed and not quoted: see above."""
    sos = np.asarray(sos)
    return int(3 * (2 * len(sos) + 1
                    - min((sos[:, 2] == 0).sum(), (sos[:, 5] == 0).sum())))


def _bandpass_sos(sr: int, lo: float, hi: float, order: int = 4):
    return butter(order, [max(lo, 5.0) / (sr / 2.0), min(hi, sr / 2.0 - 1.0) / (sr / 2.0)],
                  btype="band", output="sos")


#: 27 samples, for every 4th-order band-pass in this file and in
#: `audio_measure.band_energy`. Independent of the rate and of the band edges:
#: it is a function of the section count alone.
BANDPASS_PADLEN = _sosfiltfilt_padlen(_bandpass_sos(48000, 20.0, 400.0))

#: The lead every analysed segment gets before the strike, on BOTH sides.
#: `docs/analysis-conventions.md` section 1: at least 10 ms, or 20x the
#: filter's `padlen`, whichever is larger. 20 x 27 = 540 samples wins at both
#: our rates -- 11.25 ms at 48 kHz, 12.24 ms at 44.1 kHz. Measured there: the
#: band split is settled to 0.12 dB by 2 ms of lead and to 0.001 dB by 10 ms.
LEAD_MS = 10.0
LEAD_PADLENS = 20

#: Where t = 0 sits for every window in section 6: 1 ms before the onset, which
#: is where it has always sat. The lead is added BEFORE it rather than shifting
#: it, so that this change moves numbers for one reason and not two.
TRIM_MS = 1.0

#: The crossing that marks the onset, and the level below which a record counts
#: as not yet sounding.
ONSET_FRAC = 0.02


def required_lead_samples(sr: int) -> int:
    """True silence before t = 0, in samples. Stated in SAMPLES because
    `padlen` is a sample count: the same millisecond figure is a different
    number of pads at 44.1 and 48 kHz."""
    return max(int(round(LEAD_MS * 1e-3 * sr)), LEAD_PADLENS * BANDPASS_PADLEN)


def _onset_index(x, pk: float | None = None) -> int:
    x = np.asarray(x)
    pk = float(np.abs(x).max()) if pk is None else pk
    return int(np.argmax(np.abs(x) > ONSET_FRAC * pk))


def prepare(x, sr: int, *, side: str = "the recording") -> np.ndarray:
    """DC out from the PRE-ONSET region, trimmed so that exactly
    `required_lead_samples(sr) + 1 ms` of TRUE silence precedes the strike,
    peak-normalised.

    **The lead is guaranteed, not attempted.** When the record cannot supply
    it, the missing part is made of digital silence -- an operation that cannot
    change what the machine did, and the one `test_prepare_is_unchanged_by_
    prepended_silence` asserts is free. When the record cannot support even
    that, because it begins at or above the onset threshold and so was cut
    INTO the strike, this REFUSES. A clamp was what produced #101: it turned a
    missing precondition into a number.

    **Not by subtracting the mean of the whole buffer, and that is not a style
    choice.** These are single strikes in a buffer seconds long, so the mean of
    the whole thing is a constant offset left across every silent sample after
    the voice has gone. A constant has constant energy density and never
    decays, so it dominates a backward-integrated energy curve: it put 0.19 %
    of the rimshot's energy in a floor that never ended and `schroeder_t20`
    duly reported a **4.5-second** T20 for a 15 ms sound. A 20 Hz zero-phase
    high-pass removes the references' converter DC without adding anything.

    The DC estimate needs a pre-onset region to estimate FROM, so it is only
    taken when the record itself supplies 5 ms of one. **None of the sixteen
    Fischer references does** -- they carry 5 to 52 pre-onset samples and a
    converter offset of 0.1-0.4 % of peak, which is therefore left in place,
    exactly as it was before this change. `lead_report()` records that per
    side so it is visible rather than assumed. It is not corrected here
    because it is a separate defect from the one this function is fixing, and
    fixing two things at once makes neither attributable.

    The level-matched copy is what every metric below is taken on, and that is
    not a convenience: the Fischer set states that LEVEL was pinned at maximum
    for every voice, so its levels between voices are not the machine's. Every
    metric here is a frequency, a time or a ratio, all invariant under the
    normalisation; the original-gain peak and RMS are recorded in the result's
    diagnostics so the discarded information is still on record."""
    x = np.asarray(x, dtype=np.float64)
    if am.is_silent(x):
        return x
    pk = float(np.abs(x).max())
    i = _onset_index(x, pk)
    need = required_lead_samples(sr) + int(round(TRIM_MS * 1e-3 * sr))
    # DC from the PRE-ONSET region, where there is no voice to bias it, and
    # only when the record supplies enough of one to estimate from. A
    # zero-phase high-pass would do the job too and was tried, but filtfilt is
    # not causal: a 20 Hz first-order high-pass puts a precursor tens of ms
    # AHEAD of a sharp strike, which moved the trim point back and read the
    # rimshot's 2 ms attack as 11 ms.
    dc = float(x[:i].mean()) if i >= int(5e-3 * sr) else 0.0
    if i >= need:
        y = x[i - need:] - dc
    else:
        if i == 0:
            raise Refused(
                f"{side} begins at or above {ONSET_FRAC*100:.0f} % of its own peak: it was "
                f"cut into the strike, so there is no pre-onset region and prepending "
                f"silence would manufacture the very edge the lead exists to avoid. "
                f"{required_lead_samples(sr)} samples of true lead are required and 0 "
                f"are available")
        y = np.concatenate([np.zeros(need - i, dtype=np.float64), x - dc])
    p = float(np.abs(y).max())
    return y / p if p > 0 else y


def lead_report(x, sr: int) -> dict:
    """What `prepare()` did to one side, for the result record.

    Section 8 row 12 and #103: nothing in a result used to state the windowing
    convention, and a number that cannot be re-derived can only be re-trusted.
    `dc_removed` is here because the answer for every Fischer reference is
    `false` and that should be readable rather than inferred from the code."""
    x = np.asarray(x, dtype=np.float64)
    if am.is_silent(x):
        return {"silent": True}
    pk = float(np.abs(x).max())
    i = _onset_index(x, pk)
    lead = required_lead_samples(sr)
    need = lead + int(round(TRIM_MS * 1e-3 * sr))
    from_record = min(i, need)
    return {
        "onset_index": i,
        "onset_ms_into_the_record": round(i / sr * 1e3, 4),
        "lead_samples": lead,
        "lead_ms": round(lead / sr * 1e3, 4),
        "trim_ms": TRIM_MS,
        "lead_from_the_record_samples": int(from_record),
        "lead_manufactured_samples": int(max(0, need - i)),
        "junction_level_frac_of_peak": round(float(abs(x[0])) / pk, 6),
        "dc_removed": bool(i >= int(5e-3 * sr)),
        "dc_frac_of_peak": round(float(x[:max(i, 1)].mean()) / pk, 8),
        "padtype": "odd (scipy default)",
        "bandpass_padlen_samples": BANDPASS_PADLEN,
        "lead_in_padlens": round(lead / BANDPASS_PADLEN, 2),
        "convention": ("both sides are trimmed so that exactly lead_samples + trim_ms "
                       "of TRUE silence precede the strike; t = 0 for every window is "
                       "trim_ms before the onset, and the lead sits before it"),
    }


def window(y, sr: int, t0: float, t1: float | None) -> np.ndarray:
    """The analysis window [t0, t1), measured from `TRIM_MS` before the onset.

    `prepare()` puts `required_lead_samples(sr)` samples of silence in front of
    that origin, so the indices here are offset by it. The times in section 6's
    plans therefore mean exactly what they meant before this change."""
    o = required_lead_samples(sr)
    a = o + int(t0 * sr)
    b = len(y) if t1 is None else min(len(y), o + int(t1 * sr))
    return y[a:b]


def window_with_lead(y, sr: int, t0: float, t1: float | None) -> tuple:
    """The same window, but starting at the front of `prepare()`'s guaranteed
    lead instead of at `t0`, and how many samples of it precede `t0`.

    **Everything that zero-phase filters goes through this**, so the filter
    always meets the segment's edge in silence rather than mid-strike. The
    caller either drops the returned prefix afterwards (`_bandpass`) or is
    summing energy, which leading silence cannot change (`band_energy`)."""
    o = required_lead_samples(sr)
    a = o + int(t0 * sr)
    b = len(y) if t1 is None else min(len(y), o + int(t1 * sr))
    return y[:b], min(a, b)


def highpass(y, sr: int, hz: float, order: int = 4) -> np.ndarray:
    sos = butter(order, hz / (sr / 2.0), btype="highpass", output="sos")
    return sosfiltfilt(sos, y)


# ===========================================================================
# 4. The reference side
# ===========================================================================
# The Fischer file each of the sixteen sounds is compared against, at Roland's
# own June-1981 tuning chart -- every knob at 12 o'clock, the setting the case
# file calls "the documented reference setting". Taken from `model/drum_verify.py`
# rather than copied: a second copy of a reference mapping is a second thing to
# go stale, and this one gained eight entries while this runner was being written.
REF_MAIN = dv.REF_MAIN

class Refused(Exception):
    """A precondition of the apparatus failed. REFUSED is a first-class
    outcome here, distinct from pass and from fail: the case is written as a
    no-verdict carrying this reason, and never as a number."""


def load_reference(voice: str, refdir: pathlib.Path, inject: str = "") -> tuple:
    rel, setting = REF_MAIN[voice]
    if inject == "REF_MISSING":
        rel = "bd8/NO-SUCH-FILE.WAV"
    path = refdir / rel
    if not refdir.exists():
        raise Refused(f"reference corpus not at {refdir} -- clone "
                      f"tidalcycles/sounds-tr808-fischer or set {REFS_ENV}")
    if not path.exists():
        raise Refused(f"reference recording missing: {rel} under {refdir}")
    sr, x = wavfile.read(str(path))
    x = np.asarray(x, dtype=np.float64)
    if x.ndim > 1:
        x = x.mean(axis=1)
    x = x / 32768.0
    if am.is_silent(x):
        raise Refused(f"reference recording {rel} is silent")
    if inject == "REF_F0_20PCT":
        # Resample by 1.2 in time, which moves every frequency in it by -20 %:
        # twice the frequency tolerance, so a correct runner must report fail.
        n = int(len(x) * 1.2)
        x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x)
    return x, int(sr), rel, setting


# ===========================================================================
# 5. Our side
# ===========================================================================
# The cymbal and the open hat ring for over a second; a render that ends before
# the decay does cannot have its own decay read off it, and `schroeder_t20`
# refuses exactly that. These are `drums_fx_render.solo_renders`'s own spans.
SOLO_SECONDS = {"CY": 3.6, "OH": 3.6}


def render_drum_solo(sound: str, accent: float = 1.0) -> tuple:
    """One hit of one SOUND from the kit that ships, rendered here and now
    through the register interface -- never a committed WAV, so what is
    measured is the design as it stands.

    Sixteen sounds sit on eleven circuits and five of them are pairs sharing
    one, so the circuit is switched to the named sound with `kit_with_sounds`
    before the hit: rendering LC by striking the LT stop would measure the
    low tom and call it a conga."""
    import drums_fx as dx
    if sound not in dx.SOUND_NAMES:
        raise Refused(f"{sound} is not one of the sixteen sounds the kit implements "
                      f"({', '.join(dx.SOUND_NAMES)})")
    stop = dx.SOUND_STOP[sound]
    seconds = SOLO_SECONDS.get(sound, 2.2)
    n = int(seconds * dx.SR)
    d = dx.DrumsFx()
    dm, bd = d.play(dx.hit_writes([(int(0.01 * dx.SR), stop, accent)],
                                  dx.kit_with_sounds(sound)), n)
    g = dx.accent_reg(0.45)
    out = dx.output_fx(np.zeros(n), 0, dm, g, bd, g)
    return np.asarray(out, dtype=np.float64) / 32768.0, dx.SR


def _bass_line(notes, bpm: float, kw: dict, start_s: float, gate_frac: float = 0.9):
    """The patch's own note list placed on the 16th grid of the case's tempo:
    one note every other 16th, so two bars hold eight notes."""
    step = 60.0 / bpm / 4.0
    return [(start_s + i * 2 * step, n, 2 * step, dict(kw, gate=2 * step * gate_frac))
            for i, n in enumerate(notes)]


# Two bars at 124 BPM, the tempo the ensemble cases state. Sparse is the
# reference 808 groove; dense adds the tom and clap rows of
# model/drums_fx_render.py, which puts several stops in the same frame.
PATTERN_DENSE_EXTRA = {"LT": "....x.x.....xx..", "MT": "..x.......x...x.",
                       "HT": "..x.......x...x.", "CP": "....X.......X...",
                       "CB": "x..x..x...x..x.."}


def render_ensemble(patch_name: str, dense: bool, seconds: float = 6.0,
                    bpm: float = 124.0) -> dict:
    """The final output and the per-bus stems, from one render.

    Stems are the SAME buses through the SAME output stage with the other two
    sources zeroed, so "does the output equal the sum of the stems" is a
    question about `drums_fx.output_fx`'s one hard rail and nothing else."""
    import drums_fx as dx
    import voice_fx as vf
    import patches

    name, seq, _ = next(p for p in patches.MONO if p[0].endswith(patch_name))
    kw = dict(seq[0][3])
    notes = [ev[1] for ev in seq]
    n = int(seconds * dx.SR)

    line = _bass_line(notes, bpm, kw, start_s=0.05)
    v = vf.VoiceFx()
    vf.render_mono_fx(line, seconds, v)
    vca = v.trace["vca"]

    pattern = dict(dx.PATTERN_808)
    if dense:
        for k, row in PATTERN_DENSE_EXTRA.items():
            pattern[k] = row
    hits = [h for h in dx.pattern_hits(pattern, bpm=bpm, bars=2, start_s=0.05) if h[0] < n - 2]
    d = dx.DrumsFx()
    dm, bd = d.play(dx.hit_writes(hits, dx.kit_808()), n)

    g = dx.accent_reg(0.45)
    zero = np.zeros(n, dtype=np.int64)
    mix = dx.output_fx(vca, vf.VOL_REF, dm, g, bd, g)
    stems = {
        "voice": dx.output_fx(vca, vf.VOL_REF, zero, 0, zero, 0),
        "drum-mix": dx.output_fx(zero, 0, dm, g, zero, 0),
        "body": dx.output_fx(zero, 0, zero, 0, bd, g),
    }

    # Event timing is asked PER STOP, each row rendered alone through the same
    # path. On the mixed drum bus it cannot be asked at all: a sum of eight
    # voices beats by several dB, so `audio_measure.onsets` finds rises in the
    # tail that are no strike and misses a hat under a ringing kick -- measured,
    # 19 detections against 18 scheduled events, five of them after the last
    # hit. That is a limit of onset detection on a mixture, not of the render,
    # and the well-posed version of the same question is one stop at a time.
    per_stop = {}
    for stop_name in sorted(pattern):
        s_i = dx.SOUND_STOP[stop_name]
        rows = [h for h in hits if h[1] == s_i]
        if not rows:
            continue
        d1 = dx.DrumsFx()
        dm1, bd1 = d1.play(dx.hit_writes(rows, dx.kit_808()), n)
        one = dx.output_fx(zero, 0, dm1, g, bd1, g)
        per_stop[stop_name] = (np.asarray(one, dtype=np.float64),
                               sorted(h[0] / dx.SR for h in rows))

    return dict(mix=np.asarray(mix, dtype=np.float64),
                stems={k: np.asarray(s, dtype=np.float64) for k, s in stems.items()},
                per_stop=per_stop,
                hits_s=sorted(h[0] / dx.SR for h in hits),
                patch=name, bpm=bpm, sr=dx.SR, n=n,
                render="voice_fx %s + drums_fx %s groove, %.0f BPM, 2 bars, "
                       "buses 0.45/0.45, no limiter"
                       % (name, "dense" if dense else "sparse", bpm))


# ===========================================================================
# 6. The measurement plans, one per case.
#
# A plan is a list of (metric name, units, estimator, tolerance rule). The
# metric NAMES are exactly the case's `required_measurements`, because
# tools/scorecard.py invalidates a case whose required component is missing --
# and it should: the cheapest route to a better score is to stop measuring the
# inconvenient thing.
# ===========================================================================
# Per-sound analysis settings come from `model/drum_verify.py`'s own tables --
# BAND (where the voice lives), SPLIT_HZ (what separates its body from its air
# or noise) and ENV_WIN_MS (a window spanning several periods of its own
# fundamental). Imported, not copied, for the same reason as REF_MAIN.
BAND, SPLIT_HZ, ENV_WIN_MS = dv.BAND, dv.SPLIT_HZ, dv.ENV_WIN_MS

#: How far the backward-integrated energy curve may depart from a straight line
#: before its T20 is refused: 6 dB over the 20 dB range it is fitted across.
MAX_T20_RESIDUAL_DB = 6.0


def _t20_ms(t0: float = 0.0, t1: float | None = None, band=None):
    """Decay as T20 off the backward-integrated energy curve
    (`audio_measure.schroeder_t20`), for EVERY voice, and never a single
    exponential's tau.

    This was `decay_tau` and it refused five of the eight references outright:
    "not a single exponential", residuals of 4 to 23 dB. It was right to. The
    hats, the cowbell and the cymbal are sums of incommensurate squares whose
    envelope beats by 6-10 dB, and the clap is three bursts over a tail -- two
    exponentials, and a single tau fitted across them is the error that had the
    snare's TONE law withdrawn (`docs/discrimination.md` section 2).

    The right response to a refused precondition is an estimator whose
    precondition holds, not a looser threshold on the first one. T20 off the
    Schroeder curve assumes no model at all: the curve is monotone by
    construction, so beating cannot make it read a trough, and on a signal that
    IS a single exponential it equals ln(10)*tau exactly
    (`test_schroeder_t20_equals_ln10_tau_on_a_damped_sinusoid`). It is also the
    convention Roland's own chart column is comparable to."""
    def f(y, sr):
        seg = window(y, sr, t0, t1) if band is None else _bandpass(y, sr, band, t0, t1)
        e = am.schroeder_t20(seg, sr)
        if not e.ok:
            return e
        # A T20 fitted across a KNEE is not a decay time. The Schroeder curve
        # is monotone but it need not be straight -- a voice that decays and
        # then sits on a floor gives a line fit that is neither. The estimator
        # reports the worst residual; this refuses past MAX_T20_RESIDUAL_DB of
        # it, which is 30 % non-linearity over the 20 dB range and is the same
        # bound on both sides of every comparison.
        r = float(e.detail.get("residual_db", 0.0))
        if r > MAX_T20_RESIDUAL_DB:
            return am.Estimate(None, False,
                               "the energy decay curve is not straight -- a T20 "
                               "fitted across a knee is not a decay time", e.detail)
        return am.Estimate(e.value * 1e3, True, "", e.detail)
    return f


def _bandpass(y, sr, band, t0, t1):
    """The band-limited analysis window -- FILTERED from the front of the
    guaranteed lead and sliced afterwards, never filtered from `t0`.

    The difference is the whole of #101. `sosfiltfilt` extends oddly through
    the first sample of whatever it is given: from `t0` that first sample is
    mid-strike and the extension manufactures an edge; from the lead it is
    silence and the extension is exactly zero. The samples between the two
    points are dropped after filtering, so the window analysed is the same one
    as before -- the filter simply saw how the sound started."""
    lo, hi = band
    seg, drop = window_with_lead(y, sr, t0, t1)
    return sosfiltfilt(_bandpass_sos(sr, lo, hi), seg)[drop:]


def _energy_window(y, sr, t0, t1):
    """The window a BAND-ENERGY metric is taken over, extended back through the
    guaranteed lead so that `band_energy`'s own `sosfiltfilt` meets silence at
    the edge. Leading silence adds no energy, so a fraction-of-total cannot
    move -- `test_split_db_is_unchanged_by_prepended_silence` asserts exactly
    that -- which is why this one does not have to be sliced off again.

    Only legal for a window that opens at or before the onset. Extending one
    that opens mid-strike would sum sound from outside the window, so that
    REFUSES rather than quietly reporting a different quantity."""
    seg, drop = window_with_lead(y, sr, t0, t1)
    if drop != required_lead_samples(sr):
        raise Refused(
            f"a band-energy window that opens {t0*1e3:.1f} ms after t = 0 cannot be "
            f"extended back through the lead without summing sound from outside it")
    return seg


def _f0(sound: str, t0: float, t1: float):
    lo, hi = BAND[sound]

    def f(y, sr):
        return am.dominant_frequency(window(y, sr, t0, t1), lo, hi, sr)
    return f


def _pitch_drop(sound: str):
    band = (BAND[sound][0], SPLIT_HZ[sound])

    def f(y, sr):
        return pitch_drop_hz(y, sr, band, origin=required_lead_samples(sr))
    return f


def _split_db(sound: str, t0: float, t1: float | None, lo: float | None = None,
              hi: float | None = None):
    b_lo, b_hi = BAND[sound]
    split = SPLIT_HZ[sound]

    def f(y, sr):
        return band_ratio_db(_energy_window(y, sr, t0, t1), sr, split,
                             b_lo if lo is None else lo, b_hi if hi is None else hi)
    return f


def _attack(sound: str, t1: float = 0.250):
    ms = ENV_WIN_MS[sound]

    def f(y, sr):
        return attack_ms(window(y, sr, 0.0, t1), sr, window_ms=ms)
    return f


def _noise_t20_ms(sound: str, t0: float):
    """The T20 of the voice's ABOVE-split content alone -- the snare's noise,
    separated from its two body modes by a band-pass before the curve is
    integrated, so the number is the noise's decay and not a blend."""
    return _t20_ms(t0, None, band=(SPLIT_HZ[sound], min(BAND[sound][1], 20000.0)))


def _burst_span_ms(sound: str):
    ms = ENV_WIN_MS[sound]

    def f(y, sr):
        env = am.rms_envelope(window(y, sr, 0.0, 0.120), ms, sr)
        bursts = am.envelope_bursts(env, sr, min_sep_s=0.006, min_dip_db=2.0)
        if len(bursts) < 2:
            return am.Estimate(None, False, "fewer than two bursts in the flam",
                               dict(bursts=len(bursts)))
        return am.Estimate((bursts[-1][0] - bursts[0][0]) * 1e3, True, "",
                           dict(bursts=len(bursts)))
    return f


def _early_late_db(t_split: float, t_end: float):
    def f(y, sr):
        e_early = float(np.sum(window(y, sr, 0.0, t_split) ** 2))
        e_late = float(np.sum(window(y, sr, t_split, t_end) ** 2))
        if e_early <= 0 or e_late <= 0:
            return am.Estimate(None, False, "one half of the window holds no energy",
                               dict(early=e_early, late=e_late))
        return am.Estimate(10.0 * math.log10(e_early / e_late), True, "",
                           dict(early=e_early, late=e_late))
    return f


def _line_ratio(hz_num: float, hz_den: float, t1: float = 0.100):
    def f(y, sr):
        return tone_ratio_db(window(y, sr, 0.0, t1), sr, hz_num, hz_den)
    return f


def _difference_tone(hz_hi: float, hz_lo: float, t1: float = 0.100):
    def f(y, sr):
        return difference_tone_db(window(y, sr, 0.0, t1), sr, hz_hi, hz_lo)
    return f


def _band_pair(band_a, band_b, t1: float | None = None):
    def f(y, sr):
        return band_pair_db(_energy_window(y, sr, 0.0, t1), sr, band_a, band_b)
    return f


# The sixteen sounds, and the drum case that anchors each.
DRUM_CASE_VOICE = {
    "D01A": "BD", "D02A": "SD", "D03A": "LT", "D04A": "LC", "D05A": "MT",
    "D06A": "MC", "D07A": "HT", "D08A": "HC", "D09A": "CL", "D10A": "RS",
    "D11A": "MA", "D12A": "CP", "D13A": "CB", "D14A": "CY", "D15A": "OH",
    "D16A": "CH",
}


def _tom_plan(sound: str, drop: bool):
    """The six tom/conga sounds differ only in whether the case asks for the
    pitch DROP or the pitch. Three tunings of one circuit twice over."""
    first = (("Pitch drop", "Hz", _pitch_drop(sound), tol_frequency_of_f0) if drop
             else ("Pitch", "Hz", _f0(sound, 0.010, 0.200), tol_frequency))
    return [first,
            ("body spectrum", "dB", _split_db(sound, 0.0, 0.150), tol_db),
            ("decay", "ms", _t20_ms(0.005), tol_time)]


# name -> (units, estimator, tolerance rule). Names match cases.csv exactly,
# because scorecard.py invalidates a case whose required component is missing --
# and it should: the cheapest route to a better score is to stop measuring the
# inconvenient thing.
DRUM_PLAN = {
    "BD": [
        ("Pitch trajectory", "Hz", _f0("BD", 0.010, 0.500), tol_frequency),
        # A TIME split, not a frequency one. The first version asked for the
        # energy above 300 Hz inside a 10 ms window, and 10 ms is three periods
        # of a 49 Hz kick: too short for a spectrum to resolve the split and
        # too short for a 4th-order filter to settle. Early-against-body is
        # what "no attack, no harmonics" (docs/drum-verification.md 4.1) is a
        # statement about anyway, and it needs no filter at all.
        ("early/body energy", "dB", _early_late_db(0.010, 0.200), tol_db),
        ("decay", "ms", _t20_ms(0.005), tol_time),
    ],
    "SD": [
        ("Body/noise balance", "dB", _split_db("SD", 0.0, 0.100), tol_db),
        ("attack", "ms", _attack("SD"), tol_time),
        ("noise decay", "ms", _noise_t20_ms("SD", 0.002), tol_time),
    ],
    "LT": _tom_plan("LT", drop=True),
    "MT": _tom_plan("MT", drop=True),
    "HT": _tom_plan("HT", drop=True),
    "LC": _tom_plan("LC", drop=False),
    "MC": _tom_plan("MC", drop=False),
    "HC": _tom_plan("HC", drop=False),
    "CL": [
        ("Pitch", "Hz", _f0("CL", 0.002, 0.060), tol_frequency),
        ("attack duration", "ms", _attack("CL", 0.060), tol_time),
        ("tail decay", "ms", _t20_ms(0.002), tol_time),
    ],
    "RS": [
        # Two bridged-T networks on one circuit: reference section 5 gives the
        # low mode at 455 Hz Q 6.7 and the high at 1786 Hz Q 13.5, so the
        # balance is asked as the energy in a band around each.
        ("Partial balance", "dB", _band_pair((1500, 2100), (380, 560), 0.060), tol_db),
        ("attack", "ms", _attack("RS", 0.060), tol_time),
        ("tail decay", "ms", _t20_ms(0.002), tol_time),
    ],
    "MA": [
        ("Rise time", "ms", _attack("MA", 0.060), tol_time),
        ("band energy", "dB", _split_db("MA", 0.0, 0.150), tol_db),
        ("decay", "ms", _t20_ms(0.001), tol_time),
    ],
    "CP": [
        ("Burst timing", "ms", _burst_span_ms("CP"), tol_time),
        ("burst/tail ratio", "dB", _early_late_db(0.030, 0.200), tol_db),
        ("decay", "ms", _t20_ms(0.002), tol_time),
    ],
    "CB": [
        # Nominal frequencies, used as SEARCH CENTRES and not as probe points
        # (#108). This machine's lines are 558.35 and 823.70 Hz; the difference
        # tone is at their measured difference, not at the 260 Hz the chart
        # implies.
        ("Partial balance", "dB", _line_ratio(800.0, 540.0), tol_db),
        ("unwanted difference tone", "dB", _difference_tone(800.0, 540.0), tol_db),
        ("decay", "ms", _t20_ms(0.005), tol_time),
    ],
    "CY": [
        ("Band energy", "dB", _split_db("CY", 0.0, 0.400), tol_db),
        ("band decay", "ms", _t20_ms(0.002, None, band=(SPLIT_HZ["CY"], 20000.0)), tol_time),
        ("total decay", "ms", _t20_ms(0.002), tol_time),
    ],
    "OH": [
        ("Band energy", "dB", _split_db("OH", 0.0, 0.200), tol_db),
        ("attack", "ms", _attack("OH"), tol_time),
        ("decay", "ms", _t20_ms(0.002), tol_time),
    ],
    "CH": [
        ("Band energy", "dB", _split_db("CH", 0.0, 0.100), tol_db),
        ("attack", "ms", _attack("CH"), tol_time),
        ("decay", "ms", _t20_ms(0.001), tol_time),
    ],
}

ENSEMBLE_CASES = {
    "E1A": ("01-bass-classic", False),
    "E1B": ("01-bass-classic", True),
    "E2A": ("03-lead-line", False),
}

# ===========================================================================
# 6b. The filter side, from the FROZEN reference profile
#
# Nothing here renders a plugin. `refprofile.load_clip` reads audio that was
# rendered once, cached and hashed, and REFUSES when the cache is absent or has
# drifted from the hash in `refprofile/profile.json`. That is the difference
# between a reference and a render: a re-rendered reference moves when the
# plugin updates and moves silently, because the number coming out looks the
# same.
#
# Our side is `reference_rigs.OurLadder`, the integer ladder at the host's own
# operating point -- the same device `model/reference_compare.py` measures, at
# the same drive, through the same stimulus. No plugin is involved on either
# side of this comparison at run time.
# ===========================================================================
#: Below this, relative to the same curve's passband plateau, the stepped-tone
#: projection is reading the path's own truncation noise: a slope fitted
#: through it reads first far too steep and then far too shallow.
#: `reference_compare.response_row` caps its fit band there and
#: `refprofile.ESTIMATOR_FLOORS` publishes the number; this reads it from the
#: profile's table rather than keeping a second copy.
STOPBAND_FLOOR_DB = rp.ESTIMATOR_FLOORS["stepped-tone stopband"]["value_db"]


def _ref_band(freqs, cut_hz):
    """The passband a corner, a peak and a plateau are measured against --
    `reference_compare.response_row`'s own band, so the two agree by
    construction."""
    return (freqs[0], max(freqs[0] * 2.5, (cut_hz or 400.0) * 0.25))


def filt_corner(cut_hz: float):
    """The -3 dB corner READ OFF the measured response, never the commanded
    cutoff. The cases say "verified by measurement" and mean it: Surge is
    commanded at 250 Hz and its four cascaded poles put the -3 dB point well
    below it, which is a property of a 4-pole low-pass and not a tuning error.

    THE BIAS THAT WAS NOT COMMON MODE (#150)
    ----------------------------------------
    This measured -3 dB relative to the MEDIAN of `_ref_band`, a band that
    moves with the commanded cutoff. The docstring said the resulting bias was
    "COMMON MODE" and that only the absolute value was affected. **It is not
    common mode across cutoffs**, which is the axis every Filters conclusion is
    read along. On an ideal 4-pole, whose corner/cutoff ratio is 0.4342 at
    every cutoff by construction, this read

        commanded    250 Hz    1000 Hz    4000 Hz
        ratio        0.4998     0.4446     0.4342      (grid 40 Hz-12 kHz, 32 pts)
        bias        +15.1 %     +2.4 %     +0.0 %

    -- 15 points of droop manufactured by the instrument, concentrated exactly
    where #146 read a trend and attributed it to our cutoff mapping. A
    CONSTANT 16 % tuning error read back as -11.9 / -15.3 / -15.7 %, a
    3.8-point trend out of no trend at all.

    The reference is now `audio_measure.dc_plateau_db`: the same band, fitted
    against `(f/cut)^2` and extrapolated to DC, which is shape-agnostic for any
    real filter and does not depend on where the band sits relative to the
    cutoff. Measured on ideal 2-, 4- and 6-pole responses over 250 Hz-4 kHz the
    ratio's spread falls from 8.92 / 15.10 / 20.93 % to 0.43 / 0.73 / 0.80 %,
    and a constant 16 % error reads back as -15.59 / -15.91 / -15.98 %, a
    0.39-point trend where there was a 3.84-point one.

    WHAT IS LEFT, AND IT IS NOT ZERO. Up to 0.80 % of residual spread, from the
    log grid's own 20.2 % spacing and the linear-in-dB interpolation across it
    -- the same systematic `filt_rolloff` documents, and it is now the whole of
    this estimator's frequency dependence. **A trend smaller than about 1 %
    across the range is this instrument and not a filter's**, which is the
    number a reader of F1A/F1B/F1C needs and did not have.

    THE GRID IS PART OF THE INSTRUMENT. #150 records two different readings of
    this control, 0.500/0.445/0.434 and 0.460/0.439/0.432. Both are right: the
    first is `reference_compare.FREQS`, `geomspace(40, 12000, 32)`, which is
    the grid the board is actually measured on; the second is
    `geomspace(20, 18000, 32)`, which reproduces to 0.4597/0.4390/0.4317 and
    -14.66/-15.97/-15.96. A 20 Hz floor puts the plateau band lower relative to
    the cutoff, so it catches less droop and the bias is smaller. The number to
    correct against is the FIRST, because `probe_freqs()` is the profile's own
    grid. Pinned in test_run_case.py::test_filt_corner_grid_dependence_is_the
    _reconciliation_of_150s_two_readings.

    Ground truth: test_filt_corner_ratio_is_constant_across_the_range,
    test_filt_corner_reads_a_constant_tuning_error_as_constant,
    test_filt_corner_recovers_a_known_ratio_between_two_corners,
    audio_measure.dc_plateau_db's own tests."""
    def f(freqs, g):
        band = _ref_band(freqs, cut_hz)
        ref = am.dc_plateau_db(freqs, g, band, scale_hz=(cut_hz or 400.0))
        if not ref.ok:
            return am.Estimate(None, False,
                               "no passband reference to measure a corner against: "
                               + ref.reason, ref.detail)
        e = am.corner_from_curve(freqs, g, ref_band=band, ref_db=ref.value)
        if e.ok:
            e.detail.update({k: ref.detail[k] for k in
                             ("fit_residual_db", "extrapolation_db", "n")})
            e.detail["plateau_basis"] = "dc-extrapolated (#150)"
        return e
    return f


def filt_lowband_gain(cut_hz: float, open_plateau_db: float):
    """Passband level with the filter AT the cutoff, relative to the SAME
    instrument with its filter effectively out of the way. A ratio inside one
    instrument, so a fixed gain difference between two synthesisers cancels
    out of it by construction -- which is the rule every number in
    `model/reference_compare.py` is held to and the reason a raw plateau in
    dBFS is not quoted here.

    Ground truth: test_run_case.py::test_filt_lowband_gain_reads_a_known_offset."""
    def f(freqs, g):
        rb = _ref_band(freqs, cut_hz)
        try:
            pl = am.plateau_db(freqs, g, rb)
        except am.InsufficientEvidence as e:
            return am.Estimate(None, False, str(e), dict(band=rb))
        return am.Estimate(pl - open_plateau_db, True, "",
                           dict(plateau_db=round(pl, 4),
                                wide_open_plateau_db=round(open_plateau_db, 4),
                                band_hz=[round(b, 2) for b in rb]))
    return f


def filt_rolloff(cut_hz: float):
    """Stopband slope in dB per octave, fitted between 2.2 and 7 times the
    device's OWN measured corner -- `reference_compare.response_row`'s band,
    so the two agree by construction.

    **Each device is measured over its own band, not over a shared one, and
    the raw slope is reported with no correction.** An earlier version of this
    subtracted the slope an ideal analogue 4-pole gives over the same band
    (`reference_compare.ideal_4pole_slope`), on the argument that two filters
    with different corners are fitted over different bands. That correction was
    measured before it was used and it was WORSE than the thing it corrected:
    `ideal_4pole_slope`'s `fp` is a POLE frequency and what this has is a -3 dB
    CORNER, which for a 4-pole are a factor of 2.3 apart, so the "ideal" it
    subtracted was the wrong curve. On closed-form ideal 4-poles it read +3.77,
    +4.09 and +4.99 dB/oct at pole frequencies of 250, 312 and 500 Hz -- three
    different answers for three filters of identical shape.

    THE SYSTEMATIC THAT REPLACES IT, MEASURED. The band is scale-invariant in
    principle, so two filters of the same shape should read the same slope
    wherever their corners are. They do not quite, because the measured corner
    is interpolated on a log grid whose points are 20.2 % apart and that
    interpolation's error depends on where the corner falls between two of
    them. Over a 2:1 range of corners on closed-form ideal 4-poles the raw
    slope moves from -18.68 to -17.46 dB/oct: **0.32 dB/oct per 25 % of corner
    difference.** At F1A's 8 % corner difference that is about 0.1 dB/oct,
    a fifteenth of the 1.5 dB/oct tolerance. It is pinned in
    test_run_case.py::test_filt_rolloff_is_nearly_scale_invariant so it cannot
    grow unnoticed.

    Refuses whenever `slope_db_oct` refuses -- a curve that is not a straight
    line over the band has no slope, and that refusal is what found the
    quantisation floor this profile's probe level is chosen inside.

    Ground truth: test_run_case.py::test_filt_rolloff_of_an_ideal_4pole,
    test_filt_rolloff_sees_a_pole_that_is_not_there."""
    def f(freqs, g):
        rb = _ref_band(freqs, cut_hz)
        # Keep the corner definition identical to filt_corner.  Using the
        # old moving-median reference here makes the fit band inherit the
        # frequency-dependent bias that #150/#164 removed from corner.
        ref = am.dc_plateau_db(freqs, g, rb, scale_hz=(cut_hz or 400.0))
        if not ref.ok:
            return am.Estimate(None, False,
                               "no passband reference to measure a corner against: "
                               + ref.reason, ref.detail)
        c = am.corner_from_curve(freqs, g, ref_band=rb, ref_db=ref.value)
        if not c.ok:
            return am.Estimate(None, False,
                               "no measured corner, so no band to fit a slope over: "
                               + c.reason, c.detail)
        try:
            pl = am.plateau_db(freqs, g, rb)
        except am.InsufficientEvidence as e:
            return am.Estimate(None, False, str(e), dict(band=rb))
        live = np.asarray(freqs)[np.asarray(g) > pl + STOPBAND_FLOOR_DB]
        top = float(live.max()) if len(live) else 9000.0
        band = (2.2 * c.value, min(7.0 * c.value, 9000.0, top))
        sl = am.slope_db_oct(freqs, g, band)
        if not sl.ok:
            return sl
        return am.Estimate(sl.value, True, "",
                           dict(band_hz=[round(b, 2) for b in band],
                                corner_hz=round(c.value, 3),
                                n_points=int(sl.detail.get("n", 0)),
                                fit_residual_db=round(float(sl.detail.get("residual_db", 0.0)), 4),
                                stopband_floor_db=STOPBAND_FLOOR_DB,
                                band_systematic_db_oct_per_25pct_corner=0.32))
    return f


#: The First-32 filter cases this runner can measure, and the frozen clips each
#: one is measured against. `res_ours` is our own control's value, not a
#: translation of Surge's: `k = 4*res` (model/fixed.py regs), so res 0.0 is our
#: zero and the two zeros are the same physical setting. Resonance grids above
#: zero are NOT commensurable between devices and nothing here maps one onto
#: the other -- which is why F2A is not in this table.
FILTER_CASES = {
    "F1A": dict(ref_clip="surge-type2/lp-cut250-res0.00",
                ref_open_clip="surge-type2/lp-open20k-res0.00",
                cut_hz=250.0, open_hz=20000.0, res_ref=0.0, res_ours=0.0),
    # The other two cutoff REGIONS `cases.csv` states for this family. Same
    # stimulus, same probe level, same estimators, same wide-open clip on the
    # far side of the ratio: the ONLY thing that moves is the commanded cutoff,
    # which is what makes F1A/F1B/F1C a sweep of one variable rather than three
    # separate measurements. `refprofile.CUT_REGIONS_HZ` renders them.
    "F1B": dict(ref_clip="surge-type2/lp-cut1000-res0.00",
                ref_open_clip="surge-type2/lp-open20k-res0.00",
                cut_hz=1000.0, open_hz=20000.0, res_ref=0.0, res_ours=0.0),
    "F1C": dict(ref_clip="surge-type2/lp-cut4000-res0.00",
                ref_open_clip="surge-type2/lp-open20k-res0.00",
                cut_hz=4000.0, open_hz=20000.0, res_ref=0.0, res_ours=0.0),
}

#: All three cutoff-response cases state the same three measurements, so they
#: share one plan rather than three copies of it that could drift apart.
_CUTOFF_RESPONSE_PLAN = [
    ("Corner frequency", "Hz", "corner", tol_frequency),
    ("low-band gain", "dB", "lowband", tol_db),
    ("rolloff", "dB/oct", "rolloff", tol_fixed(1.5, "rolloff")),
]

FILTER_PLAN = {cid: _CUTOFF_RESPONSE_PLAN for cid in FILTER_CASES}


def load_filter_reference(clip_id: str, inject: str = "") -> tuple:
    """The frozen reference response curve, derived from cached audio whose
    content hash is checked on every read.

    `refprofile.load_clip` raises `refprofile.Refused` for a cache that is
    absent, short, at the wrong rate, silent, or whose bytes do not hash to
    what the committed profile says. Every one of those is a stated
    no-verdict here and never a number."""
    profile = rp.load_profile()
    if inject == "REF_PROFILE_MISSING":
        clip_id = "surge-type2/NO-SUCH-CLIP"
    if inject == "REF_PROFILE_TAMPERED":
        # Exercise the real refusal: the audio on disk no longer matches the
        # hash in the committed profile. Done by moving the profile's expected
        # hash rather than by damaging the cache, because a control must not be
        # able to leave the operator's reference corpus broken behind it.
        profile = json.loads(json.dumps(profile))
        m = profile["clips"][clip_id]
        h = m["sha256"]
        m["sha256"] = ("0" if h[0] != "0" else "1") + h[1:]
    y, sr, meta = rp.load_clip(clip_id, profile)
    freqs = list(meta["freqs_hz"])
    parts = [(int(a), int(b), float(f)) for a, b, f in meta["parts"]]
    amp = float(meta["amp"])
    try:
        import reference_rigs as rr
        g = rr.SurgeRig.tone_project(y, parts, amp, clip_id)
    except am.InsufficientEvidence as e:
        raise Refused(f"the frozen clip {clip_id} could not be projected: {e}")
    freqs = np.asarray(freqs, dtype=np.float64)
    g = np.asarray(g, dtype=np.float64)
    if inject == "REF_CORNER_2X":
        # The measured curve for a reference whose filter corner is an octave
        # lower. This is equivalent to time-stretching the original audio by
        # 2 before projection, but keeping the already-projected response makes
        # the mutation independently testable without a plugin/cache.
        freqs = freqs / 2.0
    return freqs, g, meta


def our_filter_curve(freqs, cut_hz: float, res: float, amp: float):
    """Our ladder through the SAME stepped tone at the SAME level. Rendered
    here and now from the integer model -- never a committed WAV -- so what is
    measured is the design as it stands."""
    import reference_rigs as rr
    dev = rr.OurLadder("ours")
    try:
        g = dev.tone_gain_db(list(freqs), cut_hz, res, amp)
    except am.InsufficientEvidence as e:
        raise Refused(f"our ladder's stepped-tone probe refused at cutoff {cut_hz:.0f} Hz, "
                      f"resonance {res}: {e}")
    return np.asarray(g, dtype=np.float64)


def run_filter_case(case: dict, inject: str, keep_audio: bool) -> dict:
    cid = case["case_id"]
    spec = FILTER_CASES[cid]
    required = [m.strip() for m in case["required_measurements"].split(";") if m.strip()]
    cut, amp = spec["cut_hz"], rp.PROBE_AMP

    ref_f, ref_g, meta = load_filter_reference(spec["ref_clip"], inject)
    open_f, open_g, open_meta = load_filter_reference(spec["ref_open_clip"])
    ours_g = our_filter_curve(ref_f, cut, spec["res_ours"], amp)
    ours_open_g = our_filter_curve(open_f, spec["open_hz"], spec["res_ours"], amp)

    ref_open_plateau = am.plateau_db(open_f, open_g, _ref_band(open_f, cut))
    ours_open_plateau = am.plateau_db(open_f, ours_open_g, _ref_band(open_f, cut))

    ests = {
        "corner": (filt_corner(cut), filt_corner(cut)),
        "lowband": (filt_lowband_gain(cut, ours_open_plateau),
                    filt_lowband_gain(cut, ref_open_plateau)),
        "rolloff": (filt_rolloff(cut), filt_rolloff(cut)),
    }
    metrics = {}
    for name, units, key, tol_rule in FILTER_PLAN[cid]:
        e_ours, e_ref = ests[key]
        metrics[name] = measure_pair(name, units, e_ours, (ref_f, ours_g),
                                     (ref_f, ref_g), tol_rule, {}, est_ref=e_ref)
    for m in required:
        metrics.setdefault(m, invalid_metric("", "this runner has no estimator for it"))

    audio = f"reference {spec['ref_clip']} (frozen, sha256 {meta['sha256'][:12]})"
    audio_path = "not written (--no-audio)"
    if keep_audio:
        pth = AUDIO_OUT / f"{cid}-ours-response.json"
        pth.parent.mkdir(parents=True, exist_ok=True)
        pth.write_text(json.dumps({"freqs_hz": [float(f) for f in ref_f],
                                   "ours_gain_db": [float(v) for v in ours_g],
                                   "reference_gain_db": [float(v) for v in ref_g],
                                   "cut_hz": cut, "res_ours": spec["res_ours"],
                                   "res_reference": spec["res_ref"], "amp": amp},
                                  indent=1) + "\n")
        audio_path = str(pth.relative_to(ROOT))
        audio = f"{audio}; ours {audio_path}"

    import reference_rigs as rr
    prof = rp.load_profile()
    rig = prof["rigs"][meta["rig"]]
    base = {
        "engine": ENGINE, "case_id": cid, "subject": case["subject"],
        "source_commit": source_commit(), "analysis_run": analysis_run(),
        "provenance": provenance(
            model_input_hashes({
                f"frozen:{spec['ref_clip']}": "sha256:" + meta["sha256"][:16],
                f"frozen:{spec['ref_open_clip']}": "sha256:" + open_meta["sha256"][:16],
            }),
            {"ours": audio_path, "reference": meta["file"]},
            dict(cut_hz=cut, res_ours=spec["res_ours"], res_reference=spec["res_ref"],
                 probe_amp=amp, probe_level_dbfs=rp.PROBE_LEVEL_DBFS,
                 n_probe_tones=len(ref_f), inject=inject or None)),
        "reference_profile": (f"{meta['rig']}:{spec['ref_clip']} "
                              f"(commanded {cut:.0f} Hz, readback "
                              f"{meta['cutoff_readback_hz']} Hz, resonance "
                              f"{spec['res_ref']}), frozen at "
                              f"{prof['built']['worktree']['commit']}"),
        "reference_identity": (
            f"{rig['plugin'].get('bundle_version', '?')} "
            f"{rig['plugin'].get('bundle_id', '?')}, binary sha256 "
            f"{str(rig['plugin'].get('binary_sha256'))[:16]}; LP Vintage Ladder "
            f"subtype Type 2 = sst-filters VintageLadder::Huov, Huovilainen DAFx-04, "
            f"the same paper DR 0001 implements. Rendered once through the qualified "
            f"rig of #87 and frozen: this result did not run a plugin."),
        "render_run": (f"reference_rigs.OurLadder@{_sha(ROOT / 'model' / 'reference_rigs.py')} "
                       f"stepped tone, {len(ref_f)} frequencies {ref_f[0]:.0f}-{ref_f[-1]:.0f} Hz, "
                       f"amp {amp} ({rp.PROBE_LEVEL_DBFS:+.2f} dBFS), cutoff {cut:.0f} Hz, "
                       f"resonance {spec['res_ours']} (k = 4*res, so this is our zero), "
                       f"{rp.SR} Hz, no resampling anywhere"),
        "audio": audio,
        "tolerance_policy": TOLERANCE_POLICY,
        "estimator_floors": rp.ESTIMATOR_FLOORS,
        "metrics": metrics,
        "diagnostics": {
            "ours_corner_hz": _est_value(filt_corner(cut)(ref_f, ours_g)),
            "reference_corner_hz": _est_value(filt_corner(cut)(ref_f, ref_g)),
            "ours_wide_open_plateau_db": round(float(ours_open_plateau), 4),
            "reference_wide_open_plateau_db": round(float(ref_open_plateau), 4),
            "reference_commanded_cutoff_hz": meta["commanded"]["cut_hz"],
            "reference_cutoff_readback_hz": meta["cutoff_readback_hz"],
            "probe_level_dbfs": rp.PROBE_LEVEL_DBFS,
            "note": ("levels are never compared across the two instruments: every "
                     "number here is a ratio inside one of them, a frequency, or a "
                     "slope. The wide-open plateaus are recorded so the discarded "
                     "absolute levels are still on the record."),
        },
    }
    if inject:
        base["INJECTED_CONTROL"] = inject
    return base


def _est_value(e):
    return round(float(e.value), 4) if e.ok else None


# Deliberately not run, with the reason. `--list` prints this table and the PR
# carries it: a case nobody attempted has to say so, or "not run" and "we
# forgot" become the same entry.
NOT_RUN = {}

# These twenty entries used to read "out of scope for this reference profile,
# which freezes the 250 Hz cutoff region". That was true of the profile and it
# was the WRONG REASON for most of them, which is the failure mode a not-run
# table exists to prevent: one plausible sentence covering twenty cases stops
# anybody asking what each is actually blocked on. The profile now freezes
# 1 kHz and 4 kHz as well (`refprofile.CUT_REGIONS_HZ`) and exactly two of the
# twenty ran as a result -- F1B and F1C, above. The other eighteen were never
# blocked on the cutoff, and each now says what it is blocked on.
#
# It was most visibly wrong for F4A, whose cutoff region is 250 Hz: the one the
# profile froze from the start. Nothing about that case was ever out of scope
# for the cutoff reason it was given.

#: The holdout trajectories. Not a tooling gap -- a sequencing rule.
for _c in ("F1D", "F2D", "F3D", "F5D"):
    NOT_RUN[_c] = (
        "a Holdout-20 case whose settings are not sealed yet. Its stimulus is "
        "'seal an unseen cutoff/resonance/drive trajectory', and the sealing is "
        "the measurement's whole value: docs/scorecard/README.md, 'once a holdout "
        "case's detailed errors have guided a change, it has become development "
        "data'. An agent that picks the setting, freezes the clip and reads the "
        "error in one pass has produced a development case wearing a holdout's "
        "label, and there is no way to tell afterwards which it was. The rig, the "
        "profile and the estimators are all ready -- what is missing is somebody "
        "OTHER than the party tuning the model choosing the trajectory and "
        "committing it before it is rendered.")

#: The resonance family. Unblocked on audio since this commit; still blocked on
#: the same definition F2A is.
for _c in ("F2B", "F2C"):
    NOT_RUN[_c] = (
        "blocked on exactly what F2A is blocked on, and no longer on the cutoff: "
        "a peak gain quoted at a fixed input level is a statement about that "
        "level, because Surge Type 2 is level-independent over the whole probed "
        "range and our fixed-point ladder is not (+32.1 dB at -60 dBFS against "
        "+10.0 at -12, at res 1.20). See NOT_RUN['F2A'] for the measurement and "
        "for why reference_compare.stage_peakdrive's matched-drive answer is not "
        "available to us. The resonance LADDER at 1 kHz and 4 kHz was "
        "deliberately not frozen: whoever writes the matched-drive definition "
        "decides which rungs it needs, and freezing ten guesses per region now "
        "would move every consumer's profile hash to cache audio no case can "
        "read. The 250 Hz ladder is already frozen and is enough to write the "
        "definition against.")

#: Mixer drive. A rig change, not a runner change.
for _c in ("F3B", "F3C"):
    NOT_RUN[_c] = (
        "blocked on exactly what F3A is blocked on, and no longer on the cutoff: "
        "the case requires separating MIXER DRIVE from output gain, Surge's "
        "mixer drive is Pre-Filter Gain (parameter 316), and the qualified rig "
        "PINS it at '0.00 dB' as a setting that is not the thing under test. "
        "#87's rig refuses to build when a pin does not hold, which is the "
        "behaviour to keep. Making 316 the thing under test is a change to "
        "reference_rigs.SurgeRig and to what 'the qualified rig' means, and it "
        "has to be re-qualified after, at every cutoff region -- not a clip this "
        "profile can render.")

#: Self oscillation. Renderable on BOTH sides today; no estimator, and no
#: matched-resonance definition.
for _c in ("F4A", "F4B", "F4C", "F4D"):
    NOT_RUN[_c] = (
        "no self-oscillation clip in the profile, and -- separately -- no "
        "estimator in this runner for what the case asks: frequency tracking, "
        "spectrum, stability. The cutoff was never the blocker; F4A's region is "
        "250 Hz, which this profile froze from the beginning, and it still "
        "carried the cutoff excuse. What is actually missing, measured here so "
        "the next agent does not have to: BOTH sides ring. "
        "reference_rigs.OurLadder.ring at resonance 2.00 peaks at 0.55 and "
        "sounds at 236.8 / 949.4 / 1425.2 / 3803.3 Hz for commanded cutoffs of "
        "250 / 1000 / 1500 / 4000 Hz, and SurgeRig.ring exists and is the same "
        "excite-then-remove stimulus. So the audio is a render away. The two "
        "things that are not: (1) an estimator, because 'spectrum' and "
        "'stability' have no definition in this repository yet and a frequency "
        "read off a decaying ring is not the same measurement as one off a "
        "sustained oscillation; (2) WHICH resonance each device is set to, "
        "which is F2A's incommensurability again -- 'each device at its own "
        "maximum' is a defensible definition and it is a definition somebody "
        "has to write down and defend before a number is published against it.")

#: Cutoff motion. Our side cannot produce the stimulus at all.
for _c in ("F5B", "F5C"):
    NOT_RUN[_c] = (
        "blocked on exactly what F5A is blocked on, and no longer on the cutoff: "
        "no clip in this profile automates a parameter, deliberately, and "
        "'stepping' cannot be attributed between the plugin and the host without "
        "the host's automation block size pinned -- docs/failure-modes.md records "
        "all three plugins appearing to step at 94 Hz because that was the "
        "host's block rate. Our side additionally has no swept-cutoff render: "
        "reference_rigs.OurLadder answers three questions (tone_gain_db, ring, "
        "drive_tone) and a sweep is not one of them, so there would be nothing "
        "to compare a frozen sweep against.")

#: Audio-rate modulation. The same missing stimulus as F5, one decade faster.
for _c in ("F6A", "F6B", "F6C", "F6D"):
    NOT_RUN[_c] = (
        "the same missing stimulus as F5, and not the cutoff: the case wants an "
        "identical MODULATED control trace through both devices with the "
        "modulation rate and depth pinned, and reference_rigs.OurLadder has no "
        "modulation input -- its cutoff is an argument to _render, fixed for the "
        "whole buffer. SurgeRig.swept_cutoff can drive the reference side and is "
        "a slow sweep through host automation, which is a different thing from "
        "audio-rate modulation and would be capped by the host block rate "
        "anyway. Sidebands and foldback measured against a stimulus only one "
        "side can produce would be a property of the stimulus. Our ladder "
        "needs a per-sample cutoff input before this case means anything.")

NOT_RUN["F2A"] = (
    "the reference is frozen and the resonance ladder is in the profile; the "
    "COMPARISON is not well posed at a fixed input level. Measured: Surge Type 2's "
    "response at 250 Hz is identical to two decimal places at -60, -36 and -12 dBFS "
    "(its thermal = 1/70 input scaling keeps it small-signal throughout), while our "
    "measured peak at res 1.20 is +32.1 dB at -60 dBFS, +28.3 at -36 and +10.0 at "
    "-12. The two are therefore never at the same drive into their own "
    "nonlinearity, and a peak gain quoted at a fixed input level is a statement "
    "about that level. reference_compare.stage_peakdrive's answer -- match a "
    "fraction of each device's own self-oscillation onset and read the "
    "small-signal end -- is not available to us: our small-signal end is inside "
    "our own quantisation floor, where slope_db_oct refuses every resonant row. "
    "This needs a matched-drive definition written down before it is measured. "
    "The ten-rung ladder is frozen in refprofile/profile.json so whoever writes "
    "it does not have to re-render the reference.")

NOT_RUN["F3A"] = (
    "the case requires separating MIXER DRIVE from output gain. Surge's mixer "
    "drive is Pre-Filter Gain, parameter 316, which the qualified rig PINS at "
    "'0.00 dB' as a setting that is not the thing under test -- and #87's rig "
    "refuses to build when a pin does not hold, which is the behaviour to keep, "
    "not to work around. Making 316 the thing under test is a change to the rig, "
    "not to this runner. The three drive clips at stated input levels are frozen "
    "in the profile and already show the shape of the answer: Surge's h3 at 250 Hz "
    "/ res 0.5 is -113.0 dB at -6 dBFS and -100.6 dB at 0 dBFS against a MEASURED "
    "floor of -122.6 dB, where ours is -22.7 dB at 0 dBFS. That ~78 dB gap is "
    "Surge's documented thermal = 1/70 input scaling, not a finding about either "
    "filter's harmonic ratios, and publishing it as one would be the scorecard "
    "lying in our favour's direction.")

NOT_RUN["F5A"] = (
    "no clip in this profile automates a parameter, deliberately. The case asks "
    "for cutoff MOTION and for the host automation block size to be pinned, and "
    "'stepping' cannot be attributed between the plugin and the host without that "
    "control: docs/failure-modes.md records all three plugins appearing to step at "
    "94 Hz because that was the host's block rate. Our side additionally has no "
    "swept-cutoff render -- reference_rigs.OurLadder answers three questions and a "
    "sweep is not one of them -- so there is nothing to compare a frozen sweep "
    "against yet.")

for _c in ("M2A", "M3A", "M4A", "M6A", "M7A", "M8A"):
    NOT_RUN[_c] = (
        "no qualified Mono reference, and the two candidates failed for different "
        "reasons that are MEASURED and recorded in refprofile/profile.json rather "
        "than inherited. Model D -- the cross-check these cases name -- renders "
        "EXACT silence UNDER DAWDREAMER: peak 0.0 with oscillator 1 on at full "
        "level and the filter wide open, and peak 0.0 with the filter "
        "self-oscillating. That is a property of (plugin, HOST), not of the "
        "plugin: under pedalboard the same Model D renders at full level -- peak "
        "1.000, 8.57 % of samples at the rail, strongest partial 131.00 Hz for "
        "MIDI 60, an octave down and the same default trap as Mini V3 -- and Mini "
        "V3 is the exact reverse, sounding under dawdreamer and silent under "
        "pedalboard. See #123; naming the tool and not the pair is the fifth "
        "instance of it here and the first already written into a file that "
        "presents itself as a reference. So the rig builds and its pins hold, and "
        "under dawdreamer it makes no sound, so nothing downstream of THAT "
        "combination can be a reference until the pair is qualified. Mini V3 does "
        "make sound under dawdreamer, and every "
        "one of its parameters is a bare 0..1 with no units and no readback: its "
        "cutoff can be calibrated against its own self-oscillation "
        "(reference_compare.calibrate_knob) and its ENVELOPE knobs cannot, because "
        "nothing in this repository maps a Mini V3 envelope knob to a time. Every "
        "Mono case requires envelope timing, so a Mini V3 patch frozen today would "
        "compare our envelope against an arbitrary knob position and publish the "
        "difference as a result. The envelope-knob calibration is the missing "
        "piece and it is a deliverable of its own.")
NOT_RUN["E3A"] = ("no shipped patch uses the noise source or oscillator-3 "
                  "modulation, so the stimulus cannot be built from frozen "
                  "material without inventing a patch.")


def plan_for(case_id: str) -> str:
    """What this runner will do with a case: 'drum', 'ensemble', 'not-run' or
    'unplanned'."""
    if case_id == "M1A":
        return "mono-bass"
    if case_id in ("M5A", "M5B"):
        return "mono"
    if case_id in NOT_RUN:
        return "not-run"
    if case_id in DRUM_CASE_VOICE:
        return "drum"
    if case_id in ENSEMBLE_CASES:
        return "ensemble"
    if case_id in FILTER_CASES:
        return "filter"
    return "unplanned"


# ===========================================================================
# 7. Running one case
# ===========================================================================
def _sha(*paths) -> str:
    h = hashlib.sha256()
    for p in paths:
        h.update(pathlib.Path(p).read_bytes())
    return h.hexdigest()[:12]


def _git(*args) -> str:
    try:
        return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True,
                                       stderr=subprocess.DEVNULL)
    except Exception:
        return ""


def mono_reference_pulse_mapping(manifest: dict) -> str:
    """Summarize only waveform labels the reference actually classified."""
    values = {str(measurement["waveform"])
              for segment in manifest["timeline"]["segments"]
              if segment["wave"] == "pulse"
              for measurement in segment["measurements"]
              if measurement.get("waveform") is not None}
    return ", ".join(sorted(values)) or "waveform not classified in frozen reference"


def source_commit() -> str:
    return (_git("rev-parse", "--short", "HEAD").strip() or "?")


def worktree_state() -> dict:
    """The commit is not enough. Fourteen worktrees are live on this repository
    at once and a clean SHA that silently means "plus whatever was in the tree"
    is worse than no SHA: a stale result is indistinguishable from a current
    one. So the uncommitted diff is hashed too -- tracked modifications from
    `git diff HEAD`, and every untracked file git would not ignore, by content.
    `dirty` says which of the two kinds of record this is."""
    h = hashlib.sha256()
    diff = _git("diff", "HEAD")
    h.update(diff.encode())
    untracked = [f for f in _git("ls-files", "--others", "--exclude-standard").split("\n") if f]
    for rel in sorted(untracked):
        f = ROOT / rel
        try:
            h.update(rel.encode())
            h.update(hashlib.sha256(f.read_bytes()).digest())
        except OSError:
            h.update(b"?")
    return {"commit": source_commit(),
            "described": _git("describe", "--always", "--dirty").strip() or "?",
            "branch": _git("rev-parse", "--abbrev-ref", "HEAD").strip() or "?",
            "dirty": bool(diff.strip() or untracked),
            "uncommitted_sha256": h.hexdigest()[:16],
            "untracked_files": len(untracked)}


def _file_sha(path) -> str:
    try:
        return "sha256:" + hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()[:16]
    except OSError:
        return "missing"


# 0 match, 1 mismatch (a result), 2 did not run (no evidence). The repository's
# verifier convention, kept on the record as well as in the exit status: a case
# that produced no evidence and a case that was measured and scored badly need
# opposite responses, and a scorecard that renders them alike gets the wrong one.
OUTCOME_CODE = {"pass": 0, "fail": 1, "no verdict": 2, "not run": 2}
OUTCOME_MEANING = "0 match, 1 mismatch (a result), 2 did not run (no evidence)"


class StaleBase(Exception):
    """The premise of the whole batch is false. Not a per-case refusal."""


# The inputs a result DEPENDS ON and does not own. If one of these differs from
# `origin/main`, an earlier green run does not cover the tree -- which is
# exactly what happened: eight drum cases refused because this worktree's
# `drums_fx.py` had eight circuits while origin/main's had eleven.
# `refprofile/profile.json` is here because a result measured against a
# different frozen reference is not comparable with one measured against this
# one -- the same argument that put `drums_fx.py` here.
#
# `model/reference_rigs.py` is deliberately NOT here, although the filter
# cases render our side from it. It is the file a reference-profile branch
# changes, so gating on it would make the gate unsatisfiable on exactly the
# branch that has to run the board -- and CLAUDE.md is explicit that an
# unsatisfiable gate is worse than no gate. It is hashed onto every record
# through MODEL_INPUTS instead, so a stale result is still detectable.
DEPENDENCIES = ("model/drums_fx.py", "model/voice_fx.py", "model/audio_measure.py",
                "model/drum_verify.py", "refprofile/profile.json",
                "docs/scorecard/cases.csv")


def base_check(allow_stale: bool = False) -> dict:
    """Assert the premise of the whole batch BEFORE any of it runs.

    This exists because of a specific, expensive shape of wrong answer. Eight
    drum cases came back

        REFUSED: the eight-stop kit does not implement LC / MT / MC / HC /
        CL / RS / MA / CY

    which is exactly what the runner should say about the tree it had -- and
    was a false statement about the project, because the complete sixteen-sound
    kit had landed on `origin/main` two commits earlier. Eight honest per-case
    refusals read as a permanent hole in the instrument, and the next person
    would have re-implemented voices that already existed.

    A stale premise is a property of the BATCH, not of a case, so it refuses
    the batch. Per-case refusals describe the instrument; this describes our
    checkout, and the two must never come out looking alike.

    **What it refuses on is the DEPENDENCIES, not the commit count.** `main`
    moves several times an hour here and most of those commits cannot change a
    measurement; refusing on all of them would train everyone to pass
    `--allow-stale`, and an ignored gate is worse than no gate. So: any of
    `DEPENDENCIES` differing from `origin/main`, or a drum circuit count that
    differs, refuses. Being behind on anything else is recorded and warned
    about, because it is still worth knowing when reading the record.

    **AHEAD IS NOT BEHIND.** A content difference against `origin/main` was
    enough to refuse, and that made the gate unsatisfiable on exactly the
    branches that have most reason to run it: a branch whose whole subject is
    repairing `model/audio_measure.py` differs from `origin/main` BECAUSE of
    the repair, and the only way to measure the repair was `--allow-stale`,
    which then stamped every record with a staleness warning that was false.
    An ignored gate and a lying record are both worse than no gate.

    So the refusal now needs BOTH a differing dependency and `HEAD` actually
    being behind `origin/main`. When `behind_commits` is 0, `origin/main` is an
    ancestor of `HEAD`: every difference is this branch's own work, the tree
    cannot be missing anything `origin/main` has, and the failure this guard
    was written for -- a worktree two commits behind, reporting a landed kit as
    a capability gap -- cannot occur. A branch that is ahead AND behind is
    still refused, because then it IS missing something. The differing files
    are recorded either way, under `stale_dependencies` when behind and
    `ahead_dependencies` when not, so the record never loses the fact that
    these inputs are not `origin/main`'s."""
    ref = "origin/main"
    have = _git("rev-parse", "--verify", f"{ref}^{{commit}}").strip()
    if not have:
        return {"checked": False,
                "why": f"no {ref} in this clone -- the base could not be checked"}
    behind = int(_git("rev-list", "--count", f"HEAD..{ref}").strip() or 0)
    import drums_fx as dx

    stale_deps = {}
    for rel in DEPENDENCIES:
        theirs = _git("show", f"{ref}:{rel}")
        ours = ""
        try:
            ours = (ROOT / rel).read_text()
        except OSError:
            pass
        if theirs and ours and theirs != ours:
            stale_deps[rel] = {"origin_main": hashlib.sha256(theirs.encode()).hexdigest()[:12],
                               "here": hashlib.sha256(ours.encode()).hexdigest()[:12]}

    theirs_stops = None
    for line in _git("show", f"{ref}:model/drums_fx.py").splitlines():
        if line.startswith("N_STOPS, N_ENV"):
            try:
                theirs_stops = int(line.split("=")[1].split(",")[0])
            except (IndexError, ValueError):
                theirs_stops = None
            break

    ahead = int(_git("rev-list", "--count", f"{ref}..HEAD").strip() or 0)
    is_ahead_only = behind == 0
    state = {"checked": True, "origin_main": have[:12], "behind_commits": behind,
             "ahead_commits": ahead,
             "n_stops_here": dx.N_STOPS, "n_stops_origin_main": theirs_stops,
             "stale_dependencies": {} if is_ahead_only else stale_deps,
             "ahead_dependencies": stale_deps if is_ahead_only else {},
             "allow_stale": allow_stale}
    problems = []
    if stale_deps and not is_ahead_only:
        problems.append("these inputs differ from origin/main: " + ", ".join(sorted(stale_deps)))
    if theirs_stops is not None and theirs_stops != dx.N_STOPS and not is_ahead_only:
        problems.append(f"the kit here has {dx.N_STOPS} drum circuits, {ref} has {theirs_stops}")
    state["problems"] = problems
    if is_ahead_only and stale_deps:
        state["ahead_note"] = (
            f"{ahead} commit(s) ahead of {ref} and 0 behind, so these inputs differ "
            f"because this branch changed them: {', '.join(sorted(stale_deps))}. That is "
            f"not a stale premise -- the tree contains everything {ref} has.")
    if problems and not allow_stale:
        raise StaleBase("; ".join(problems) +
                        f" -- rebase onto {ref} and re-run. Refusing the whole batch: "
                        f"per-case refusals from a stale checkout describe our tooling, "
                        f"not the instrument, and read like capability gaps. "
                        f"--allow-stale overrides and records that it did.")
    if behind:
        state["note"] = (f"{behind} commit(s) behind {ref}, none of them touching an "
                         f"input this measurement depends on")
    return state


BASE_STATE: dict = {}


def provenance(inputs: dict, artefacts: dict, config: dict) -> dict:
    """What this result was produced by, in enough detail to re-derive it
    rather than re-trust it."""
    return {
        "engine": ENGINE,
        "worktree": worktree_state(),
        "base_check": BASE_STATE,
        "command": " ".join([os.path.relpath(sys.argv[0], ROOT)] + sys.argv[1:])
                   if sys.argv and sys.argv[0] else "(imported)",
        "config": config,
        "python": sys.version.split()[0],
        "run_at": _now(),
        "inputs": inputs,
        "artefacts": artefacts,
        "outcome_code": None,            # filled in once the board has judged it
        "outcome_code_meaning": OUTCOME_MEANING,
    }


# The generated inputs every result here depends on: change one and an earlier
# green result no longer covers the tree.
MODEL_INPUTS = ("model/drums_fx.py", "model/voice_fx.py", "model/audio_measure.py",
                "model/reference_rigs.py", "tools/run_case.py", "tools/refprofile.py",
                "tools/mono_m5a_score.py", "tools/measure_mono_m5a_reference.py",
                "tools/scorecard.py", "refprofile/profile.json", "docs/scorecard/cases.csv")


def model_input_hashes(extra: dict | None = None) -> dict:
    d = {rel: _file_sha(ROOT / rel) for rel in MODEL_INPUTS}
    d.update(extra or {})
    return d


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def analysis_run() -> str:
    return (f"run_case@{_sha(__file__)} + audio_measure@{_sha(ROOT / 'model' / 'audio_measure.py')}"
            f" + mono_m5a_score@{_sha(ROOT / 'tools' / 'mono_m5a_score.py')}"
            f" + scorecard@{_sha(ROOT / 'tools' / 'scorecard.py')}"
            f" at {_now()}")


def invalid_metric(units: str, why: str, tolerance: float | None = None) -> dict:
    """No distance, not zero distance. There is deliberately no `error` key."""
    m = {"units": units, "valid": False, "why": why}
    if tolerance:
        m["tolerance"] = tolerance
    return m


def measure_pair(name, units, est, ours, ref, tol_rule, ctx, est_ref=None) -> dict:
    """`est_ref`, when given, is the estimator for the REFERENCE side.

    It exists for one metric and is not a loophole: "low-band gain" is a gain
    against the SAME instrument with its filter out of the way, so each side's
    estimator carries its own wide-open plateau. A plateau in dBFS compared
    across two instruments is a level mismatch wearing a measurement's
    clothes; a ratio inside one instrument is not. Everything else here passes
    one estimator and it is used on both sides, which is the rule."""
    a = est(*ours)
    b = (est if est_ref is None else est_ref)(*ref)
    if not b.ok:
        return invalid_metric(units, f"reference: {b.reason} {b.detail}")
    tol, basis = tol_rule(b.value, ctx)
    if not a.ok:
        m = invalid_metric(units, f"ours: {a.reason} {a.detail}", tol)
        m["reference"] = round(float(b.value), 4)
        return m
    if not tol or not math.isfinite(tol):
        return invalid_metric(units, f"no usable tolerance for reference {b.value!r}")
    return {"value": round(float(a.value), 4), "units": units,
            "reference": round(float(b.value), 4),
            "error": round(float(a.value) - float(b.value), 4),
            "tolerance": round(float(tol), 4), "valid": True,
            "tolerance_basis": basis}


def write_wav16(path: pathlib.Path, x, sr: int):
    path.parent.mkdir(parents=True, exist_ok=True)
    y = np.clip(np.asarray(x) * 32768.0, -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(y.tobytes())


def run_drum_case(case: dict, refdir: pathlib.Path, inject: str, keep_audio: bool) -> dict:
    voice = DRUM_CASE_VOICE[case["case_id"]]
    required = [m.strip() for m in case["required_measurements"].split(";") if m.strip()]
    base = {"engine": ENGINE, "case_id": case["case_id"], "subject": case["subject"],
            "source_commit": source_commit(), "analysis_run": analysis_run()}

    why = ""
    if voice not in REF_MAIN:
        why = f"the Fischer corpus has no reference recording mapped for {voice}"
    elif voice not in DRUM_PLAN:
        why = f"this runner has no measurement plan for {voice}"
    if why:
        base.update({
            "reference_profile": "none",
            "render_run": "not rendered", "audio": "none",
            "note": (f"REFUSED: {why}. There is nothing to compare, so this case "
                     f"has no distance -- not a zero one."),
            "provenance": provenance(model_input_hashes(), {},
                                     dict(voice=voice, refs=str(refdir), inject=inject or None)),
            "metrics": {m: invalid_metric("", why) for m in required}})
        return base

    ref_x, ref_sr, rel, setting = load_reference(voice, refdir, inject)
    ours_x, ours_sr = render_drum_solo(voice)

    # Each side names itself, so a refused lead says WHICH recording could not
    # supply one. A reference that was cut into the strike and a render that
    # was need opposite responses.
    ref_y = prepare(ref_x, ref_sr, side=f"the reference recording {rel}")
    ours_y = prepare(ours_x, ours_sr, side=f"our {voice} render")
    ref, ours = (ref_y, ref_sr), (ours_y, ours_sr)
    windowing = {"ours": lead_report(ours_x, ours_sr),
                 "reference": lead_report(ref_x, ref_sr)}

    ctx = {}
    f0 = _f0(voice, 0.010, 0.200)(*ref)
    if f0.ok:
        ctx["ref_f0"] = f0.value

    metrics = {}
    for name, units, est, tol_rule in DRUM_PLAN[voice]:
        metrics[name] = measure_pair(name, units, est, ours, ref, tol_rule, ctx)
    missing = [m for m in required if m not in metrics]
    for m in missing:
        metrics[m] = invalid_metric("", "this runner has no estimator for it")

    import drums_fx as dx
    audio_path, audio = "not written (--no-audio)", f"reference {rel}; ours not written"
    if keep_audio:
        p = AUDIO_OUT / f"{case['case_id']}-ours.wav"
        write_wav16(p, ours_x, ours_sr)
        audio_path = str(p.relative_to(ROOT))
        audio = f"reference {rel}; ours {audio_path}"

    base.update({
        "provenance": provenance(
            model_input_hashes({f"reference:{rel}": _file_sha(refdir / rel)}),
            {"ours": audio_path, "reference": str(refdir / rel)},
            dict(voice=voice, refs=str(refdir), inject=inject or None,
                 render_seconds=SOLO_SECONDS.get(voice, 2.2), accent=1.0,
                 bus_gain=0.45, level_matched=True)),
        "reference_profile": f"fischer-tr808-103852:{rel} ({setting})",
        "reference_identity": REF_ID,
        "render_run": (f"drums_fx@{_sha(ROOT / 'model' / 'drums_fx.py')} "
                       f"kit_with_sounds({voice!r}) solo, one hit at accent 1.0, "
                       f"{SOLO_SECONDS.get(voice, 2.2):.2f} s, both drum buses 0.45, "
                       f"{dx.SR} Hz, circuit {dx.SOUND_STOP[voice]} of {dx.N_STOPS}"),
        "audio": audio,
        "tolerance_policy": TOLERANCE_POLICY,
        # #103 and section 8 row 12: a number that cannot be re-derived can
        # only be re-trusted. This is the convention both sides were windowed
        # under, per side, so a reader can check the thing #101 turned out to
        # be rather than assume it.
        "windowing": windowing,
        "metrics": metrics,
        "diagnostics": {
            "ours_peak_fs": round(float(np.abs(ours_x).max()), 6),
            "ours_rms_fs": round(float(am.rms(ours_x)), 6),
            "reference_peak_fs": round(float(np.abs(ref_x).max()), 6),
            "reference_rate_hz": ref_sr, "ours_rate_hz": ours_sr,
            "level_matched": True,
            "note": ("levels are not compared: the Fischer set pinned LEVEL at "
                     "maximum for every voice, so its inter-voice levels are not "
                     "the machine's. Original-gain peak and RMS are recorded above."),
        },
    })
    if inject:
        base["INJECTED_CONTROL"] = inject
    return base


def run_ensemble_case(case: dict, keep_audio: bool) -> dict:
    patch, dense = ENSEMBLE_CASES[case["case_id"]]
    required = [m.strip() for m in case["required_measurements"].split(";") if m.strip()]
    r = render_ensemble(patch, dense)
    sr, mix = r["sr"], r["mix"]
    stem_sum = sum(r["stems"].values())

    metrics = {}

    # Event timing: each stop's row alone, against the frames its writes were
    # made at. The reference is the schedule itself, which is why this case
    # needs no external reference at all -- and the worst stop is what is
    # reported, never an average over the stops that were on time.
    tol, basis = tol_fixed(10.0, "event timing")(0.0, {})
    worst, worst_stop, refusal, outside_total = None, "", "", 0
    for stop_name, (sig, sched) in r["per_stop"].items():
        e = worst_event_offset_ms(sig / 32768.0, sr, sched)
        outside_total += int((e.detail or {}).get("onsets_outside_the_schedule", 0))
        if not e.ok:
            refusal = refusal or f"{stop_name}: {e.reason} {e.detail}"
            continue
        if worst is None or e.value > worst:
            worst, worst_stop = e.value, stop_name
    if refusal or worst is None:
        metrics["Event timing"] = invalid_metric("ms", refusal or "no stop to measure", tol)
    else:
        metrics["Event timing"] = {"value": round(worst, 4), "units": "ms", "reference": 0.0,
                                   "error": round(worst, 4), "tolerance": tol, "valid": True,
                                   "tolerance_basis": basis, "worst_stop": worst_stop}

    # Bus balance: does the one hard rail in output_fx change the sum?
    if am.rms(stem_sum) <= 0 or am.rms(mix) <= 0:
        metrics["bus balance"] = invalid_metric("dB", "a silent render")
    else:
        v = 20.0 * math.log10(am.rms(mix) / am.rms(stem_sum))
        metrics["bus balance"] = {"value": round(v, 4), "units": "dB", "reference": 0.0,
                                  "error": round(v, 4), "tolerance": 0.5, "valid": True,
                                  "tolerance_basis": "bus sum"}

    # Output artifacts: samples on the rail of the unnormalised final output.
    rail = 100.0 * am.clipped_fraction(mix, 32767.0)
    metrics["output artifacts"] = {"value": round(rail, 6), "units": "% of samples",
                                   "reference": 0.0, "error": round(rail, 6),
                                   "tolerance": 0.01, "valid": True,
                                   "tolerance_basis": "rail"}

    for m in required:
        metrics.setdefault(m, invalid_metric("", "this runner has no estimator for it"))

    audio = "not written (--no-audio)"
    if keep_audio:
        p = AUDIO_OUT / f"{case['case_id']}-mix.wav"
        write_wav16(p, mix / 32768.0, sr)
        audio = str(p.relative_to(ROOT))

    return {
        "engine": ENGINE, "case_id": case["case_id"], "subject": case["subject"],
        "source_commit": source_commit(), "analysis_run": analysis_run(),
        "provenance": provenance(model_input_hashes(), {"mix": audio},
                                 dict(patch=patch, dense=dense, bpm=r["bpm"],
                                      seconds=r["n"] / sr, bus_gain=0.45, limiter=False)),
        "reference_profile": ("our own per-bus stems and the written hit schedule; "
                              "no external reference is involved in this case"),
        "render_run": r["render"], "audio": audio,
        "tolerance_policy": TOLERANCE_POLICY,
        "metrics": metrics,
        "diagnostics": {
            "peak_fs": round(float(np.abs(mix).max()) / 32768.0, 6),
            "stem_peaks_fs": {k: round(float(np.abs(s).max()) / 32768.0, 6)
                              for k, s in r["stems"].items()},
            "scheduled_events": len(r["hits_s"]),
            "stops_timed": sorted(r["per_stop"]),
            "onsets_outside_the_schedule": outside_total,
            "dc_offset_fs": round(float(mix.mean()) / 32768.0, 8),
            "patch": r["patch"], "bpm": r["bpm"],
        },
    }


def run_case(case: dict, refdir: pathlib.Path, inject: str = "",
             keep_audio: bool = True) -> dict | None:
    """One case's result dict, or None when the case is deliberately not run."""
    cid = case["case_id"]
    kind = plan_for(cid)
    if kind == "not-run":
        return None
    base = {"engine": ENGINE, "case_id": cid, "subject": case.get("subject", ""),
            "source_commit": source_commit(), "analysis_run": analysis_run(),
            "reference_profile": "", "render_run": "", "audio": "",
            "provenance": provenance(model_input_hashes(), {},
                                     dict(refs=str(refdir), inject=inject or None))}
    required = [m.strip() for m in (case.get("required_measurements") or "").split(";")
                if m.strip()]
    try:
        if kind == "mono-bass":
            import mono_m1a_score
            return mono_m1a_score.run(case, inject, keep_audio)
        if kind == "drum":
            return run_drum_case(case, refdir, inject, keep_audio)
        if kind == "ensemble":
            return run_ensemble_case(case, keep_audio)
        if kind == "filter":
            return run_filter_case(case, inject, keep_audio)
        if kind == "mono":
            control_audio = (ROOT / f"build/scorecard/{cid}-model-control-{inject}.wav"
                             if inject else None)
            measured = mono_m5a.measure(case_id=cid, engine="selected", inject=inject,
                                         output_path=control_audio)
            report_path = None
            smoke_sha = None
            if cid == "M5A":
                smoke_dir = ROOT / "build/scorecard/M5A-spi-i2s"
                report_path = smoke_dir / "verification.txt"
                smoke = subprocess.run(
                    [sys.executable, str(ROOT / "rtl-sketch/verify_synth_top.py"),
                     "--m5a-smoke", "--filter2x", "--m5a-pulse-shape", mono_m5a.M5A_PULSE_WAVE,
                     "--m5a-saw-cutoff-hz", "20000",
                     "--m5a-saw-volume-correction-db", "-0.45428",
                     "--outdir", str(smoke_dir),
                     "--wav-out", str(smoke_dir / "m5a-i2s.wav")],
                    cwd=ROOT, capture_output=True, text=True, timeout=3600)
                report_path.parent.mkdir(parents=True, exist_ok=True)
                report_path.write_text(smoke.stdout + smoke.stderr)
                if (smoke.returncode != 0 or "PASS --" not in report_path.read_text()
                        or "M5A path verified" not in report_path.read_text()):
                    raise mono_m5a.Refused(f"SPI-to-I2S M5A smoke did not pass; see {report_path.relative_to(ROOT)}")
                smoke_sha = hashlib.sha256(report_path.read_bytes()).hexdigest()
            profile = mono_m5a.MANIFESTS[cid]
            manifest = json.loads(profile.read_text())
            notes = sorted({ev["note"] for seg in manifest["timeline"]["segments"]
                            for ev in seg["midi_events"]})
            duration = manifest["timeline"]["audio_duration_s"]
            pulse_mapping = mono_reference_pulse_mapping(manifest)
            outputs = {"ours": measured["audio"]}
            if report_path is not None:
                outputs["spi_i2s"] = str(report_path.relative_to(ROOT))
            inputs = model_input_hashes({f"frozen:{cid}:audio": "sha256:" + measured["reference_sha256"],
                                         f"frozen:{cid}:manifest": "sha256:" + measured["manifest_sha256"]})
            config = {**measured["model_configuration"],
                      "case_id": cid,
                      "reference": "frozen Mini V3 WAV",
                      "reference_pulse_classification": pulse_mapping,
                      "inject": inject or None}
            if smoke_sha:
                config["spi_i2s_smoke"] = "selected filter2x, saw 20000 Hz, gain correction -0.45428 dB"
            base.update({
                "reference_profile": (f"Mini V3 {manifest['identity']['version']} via "
                                      f"dawdreamer {manifest['identity']['host_version']}; frozen raw audio"),
                "reference_identity": manifest["reference_kind"],
                "analysis_version": measured["analysis_version"],
                "render_run": (
                    f"fixed integer model; engine={config['name']}; "
                    f"osc2x={config['oscillator_oversample_2x']}; "
                    f"filter_rate_converted={config['filter_rate_converted']}; "
                    f"filter_preserve_headroom={config['filter_preserve_headroom']}; "
                    f"filter_causal={config['filter_causal']}; "
                    f"pulse479_filter_candidate={config['pulse479_filter_candidate']}; "
                    f"pulse control={config['pulse_control_label']}; "
                    f"effective pulse={config['pulse_effective_waveform']} "
                    f"({config['pulse_effective_duty_percent']:.2f}% duty); "
                    f"saw cutoff={config['saw_cutoff_override_hz']} Hz; "
                    f"saw gain correction={config['saw_volume_correction_db']:+.5f} dB; "
                    f"envelope={config['envelope_calibration_source']}; MIDI {notes}, "
                    f"complete {duration:.2f} s phrase"),
                "audio": measured["audio"], "note": measured["note"],
                "tolerance_policy": mono_m5a.TOLERANCES,
                "metrics": measured["metrics"],
                "diagnostics": {"reference_sha256": measured["reference_sha256"],
                                "reference_manifest_sha256": measured["manifest_sha256"],
                                "cutoff_calibration": measured["cutoff_calibration"],
                                "model_segments": measured["model_segments"],
                                "events": measured["event_diagnostics"],
                                "wrong_then_right": measured["wrong_then_right"],
                                "duration_s": measured["duration_s"],
                                **({"spi_i2s_report_sha256": smoke_sha} if smoke_sha else {})},
                "provenance": provenance(inputs, outputs, config)})
            if inject:
                base["INJECTED_CONTROL"] = inject
            return base
        base["note"] = "REFUSED: this runner has no plan for this case."
        base["metrics"] = {m: invalid_metric("", "no measurement plan") for m in required}
        return base
    except (Refused, rp.Refused, mono_m5a.Refused) as e:
        base["note"] = f"REFUSED: {e}"
        base["metrics"] = {m: invalid_metric("", str(e)) for m in required}
        if inject:
            base["INJECTED_CONTROL"] = inject
        return base
    except Exception as e:                       # pragma: no cover - guard
        base["note"] = f"REFUSED: {type(e).__name__}: {e}"
        base["traceback"] = traceback.format_exc(limit=6)
        base["metrics"] = {m: invalid_metric("", f"{type(e).__name__}: {e}")
                           for m in required}
        return base


# ===========================================================================
# 8. CLI
# ===========================================================================
def load_cases() -> list[dict]:
    with open(CASES_CSV) as fh:
        return list(csv.DictReader(fh))


def verdict_of(case: dict, res: dict | None) -> tuple:
    """The board's own verdict for one result, so the runner reports what the
    board will say rather than its own opinion of it."""
    sys.path.insert(0, str(ROOT / "tools"))
    import scorecard
    r = scorecard.evaluate(case, res)
    return r["state"], r["worst"], r["why"]


def control_changed(clean: tuple, injected: tuple) -> bool:
    """Return whether an injection changed an observable board outcome.

    A state-only control is false green when the clean case already fails.
    Preserve the useful distance in that situation: a valid injected result
    must either change state or move the board's worst error.  Missing worst
    values cannot establish a change, so two refusals never count as a fired
    control.
    """
    clean_state, clean_worst = clean[:2]
    injected_state, injected_worst = injected[:2]
    if clean_state != injected_state:
        return True
    if clean_worst is None or injected_worst is None:
        return False
    return not math.isclose(float(clean_worst), float(injected_worst),
                            rel_tol=1e-9, abs_tol=1e-6)


CONTROL_CAUSE = {
    "REF_MISSING": "no-such-file.wav",
    "REF_PROFILE_MISSING": "no-such-clip",
    "REF_PROFILE_TAMPERED": "hashes",
}


def control_outcome(expect: str, inject: str, case_ids: list[str],
                    plans: dict[str, str], clean: dict[str, tuple],
                    injected: dict[str, tuple]) -> tuple[str, str]:
    """Judge an injected control only when its clean baseline is usable.

    `REFUSED` is distinct from a caught defect: missing references, unplanned
    cases, and incomplete comparisons cannot make an injection look effective.
    Verdict tuples contain (state, worst, explanation, injection note).
    """
    if not case_ids:
        return "refused", "no cases were selected"
    unplanned = [cid for cid in case_ids if plans.get(cid) in (None, "not-run", "unplanned")]
    if unplanned:
        return "refused", f"cases are not implemented: {unplanned}"
    if set(clean) != set(case_ids) or set(injected) != set(case_ids):
        return "refused", "clean and injected runs did not execute the same nonempty case set"

    def valid_measurement(v: tuple) -> bool:
        state, worst = v[:2]
        return (state in ("pass", "fail") and worst is not None
                and math.isfinite(float(worst)))

    for cid in case_ids:
        baseline, result = clean[cid], injected[cid]
        if not valid_measurement(baseline):
            return "refused", f"{cid} clean baseline has no valid measured verdict"
        if expect == "changed":
            if not valid_measurement(result):
                return "refused", f"{cid} injected run has no valid measured verdict"
            if not control_changed(baseline, result):
                return "fail", f"{cid} is indistinguishable from the clean baseline"
        elif expect == "no verdict":
            if result[0] != "no verdict":
                if result[0] == "not run":
                    return "refused", f"{cid} injected run did not execute"
                return "fail", f"{cid} injection did not produce no verdict"
            cause = CONTROL_CAUSE.get(inject)
            note = " ".join(str(part) for part in result[2:]).lower()
            if not cause or cause not in note:
                return "fail", f"{cid} refusal does not identify the {inject} mutation"
        elif expect == "fail":
            if not valid_measurement(result):
                return "refused", f"{cid} injected run has no valid measured verdict"
            if baseline[0] != "pass":
                return "refused", f"{cid} clean baseline must pass to qualify a fail control"
            if result[0] != "fail":
                return "fail", f"{cid} injection did not produce fail"
        elif expect == "pass":
            if not valid_measurement(result):
                return "refused", f"{cid} injected run has no valid measured verdict"
            if result[0] != "pass":
                return "fail", f"{cid} injection did not produce pass"
    return "pass", f"{inject} caused the expected result on {len(case_ids)} measured case(s)"


def cmd_list(cases: list[dict]) -> int:
    print(f"{'case':<7}{'family':<10}{'batch':<14}{'plan':<11}reason / reference")
    print("-" * 100)
    counts = {}
    for c in cases:
        kind = plan_for(c["case_id"])
        counts[kind] = counts.get(kind, 0) + 1
        why = ""
        if kind == "not-run":
            why = NOT_RUN[c["case_id"]]
        elif kind == "drum":
            v = DRUM_CASE_VOICE[c["case_id"]]
            why = (f"{v} vs {REF_MAIN[v][0]} ({REF_MAIN[v][1]})" if v in REF_MAIN
                   else f"{v} is not implemented by the kit -- no verdict, with the reason")
        elif kind == "ensemble":
            p, d = ENSEMBLE_CASES[c["case_id"]]
            why = f"{p}, {'dense' if d else 'sparse'} 808 groove, stems vs final output"
        elif kind == "filter":
            f = FILTER_CASES[c["case_id"]]
            why = (f"ours vs frozen {f['ref_clip']} "
                   f"(cut {f['cut_hz']:.0f} Hz, res {f['res_ref']})")
        elif kind == "mono":
            why = ("fixed integer-model phrase vs frozen Mini V3; " +
                   ("includes SPI-to-I2S smoke" if c["case_id"] == "M5A"
                    else "model-only; no integrated RTL claim"))
        else:
            why = "no plan in this runner"
        print(f"{c['case_id']:<7}{c['family']:<10}{c['batch']:<14}{kind:<11}{why[:70]}")
    print("-" * 100)
    print("  ".join(f"{k}: {v}" for k, v in sorted(counts.items())))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cases", nargs="*", help="case ids, e.g. D01A")
    ap.add_argument("--batch", help='a batch from cases.csv, e.g. "First 32"')
    ap.add_argument("--family", help="Drums | Mono | Filters | Ensemble")
    ap.add_argument("--all", action="store_true", help="every case in cases.csv")
    ap.add_argument("--list", action="store_true", help="print the plan and stop")
    ap.add_argument("--refs", default=os.environ.get(REFS_ENV, REFS_DEFAULT),
                    help=f"the Fischer TR-808 corpus (default {REFS_DEFAULT}, ${REFS_ENV})")
    ap.add_argument("--results", default=None, help="where result JSON goes")
    ap.add_argument("--inject", default="",
                    choices=["", "REF_F0_20PCT", "REF_MISSING", "REF_CORNER_2X",
                             "MONO_PITCH_UP_25_CENTS",
                             "REF_PROFILE_MISSING", "REF_PROFILE_TAMPERED"],
                    help="an injected control; requires --results outside the board")
    ap.add_argument("--expect", default="",
                    choices=["", "pass", "fail", "no verdict", "changed"],
                    help="exit 1 unless every case lands in this state (for controls); "
                         "'changed' compares injected results with a clean run")
    ap.add_argument("--no-audio", action="store_true", help="do not write the rendered WAVs")
    ap.add_argument("--allow-stale", action="store_true",
                    help="run even though this tree is behind origin/main, and say so "
                         "on every record it writes")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    all_cases = load_cases()
    if a.list:
        sel = all_cases
        if a.batch:
            sel = [c for c in sel if c["batch"] == a.batch]
        if a.family:
            sel = [c for c in sel if c["family"] == a.family]
        return cmd_list(sel)

    by_id = {c["case_id"]: c for c in all_cases}
    chosen: list[dict] = []
    for cid in a.cases:
        if cid not in by_id:
            print(f"no such case: {cid}", file=sys.stderr)
            return 2
        chosen.append(by_id[cid])
    if a.batch:
        chosen += [c for c in all_cases if c["batch"] == a.batch]
    if a.family:
        chosen += [c for c in all_cases if c["family"] == a.family]
    if a.all:
        chosen += all_cases
    if not chosen:
        ap.error("name at least one case, or --batch / --family / --all / --list")
    if a.expect and not a.inject:
        ap.error("--expect is only meaningful together with --inject")
    seen, uniq = set(), []
    for c in chosen:
        if c["case_id"] not in seen:
            seen.add(c["case_id"])
            uniq.append(c)
    chosen = uniq

    outdir = pathlib.Path(a.results) if a.results else RESULTS
    # An injected control's output is not evidence. It must not be able to
    # land on the board, whatever else goes wrong in this invocation.
    if a.inject and outdir.resolve() == RESULTS.resolve():
        print("--inject refuses to write into docs/scorecard/results; pass --results",
              file=sys.stderr)
        return 2

    # The premise of the batch, asserted before any of it runs. A stale
    # checkout produces per-case refusals that read like capability gaps, and
    # that has already happened once here.
    global BASE_STATE
    try:
        BASE_STATE = base_check(a.allow_stale)
    except StaleBase as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    if not BASE_STATE.get("checked"):
        print(f"NOTE: {BASE_STATE.get('why')}")
    elif BASE_STATE.get("problems"):
        print(f"WARNING (--allow-stale): {'; '.join(BASE_STATE['problems'])}")
    elif BASE_STATE.get("note"):
        print(f"base: {BASE_STATE['note']}")

    refdir = pathlib.Path(a.refs)
    outdir.mkdir(parents=True, exist_ok=True)
    print(f"reference corpus: {refdir}{'' if refdir.exists() else '   (ABSENT)'}")
    print(f"results into:     {outdir}")
    if a.inject:
        print(f"INJECTED CONTROL: {a.inject} -- this output is a control, not evidence")
    print()
    print(f"{'case':<7}{'state':<12}{'worst':>7}  note")
    print("-" * 96)

    states, errors, code = {}, 0, 0
    clean_verdicts = {}
    plans = {c["case_id"]: plan_for(c["case_id"]) for c in chosen}
    if a.expect:
        unplanned = [cid for cid, kind in plans.items() if kind in ("not-run", "unplanned")]
        if unplanned:
            print(f"CONTROL REFUSED: cases are not implemented: {unplanned}",
                  file=sys.stderr)
            return 2
        print("CONTROL BASELINE: measuring the same cases without the injection")
        for c in chosen:
            clean = run_case(c, refdir, "", keep_audio=False)
            clean_verdicts[c["case_id"]] = (*verdict_of(c, clean), clean.get("note", ""))
        print("CONTROL BASELINE: complete\n")
    injected_verdicts = {}
    for c in chosen:
        cid = c["case_id"]
        if plan_for(cid) == "not-run":
            states["not run"] = states.get("not run", 0) + 1
            code = max(code, OUTCOME_CODE["not run"])
            print(f"{cid:<7}{'not run':<12}{'--':>7}  {NOT_RUN[cid][:60]}")
            continue
        res = run_case(c, refdir, a.inject, keep_audio=not a.no_audio)
        # Judge BEFORE writing, so the outcome code on the record is the board's
        # verdict and not this runner's opinion of it.
        state, worst, why = verdict_of(c, res)
        injected_verdicts[cid] = (state, worst, why, res.get("note", ""))
        res.setdefault("provenance", {})["outcome_code"] = OUTCOME_CODE[state]
        if not a.dry_run:
            (outdir / f"{cid}.json").write_text(json.dumps(res, indent=2, sort_keys=False) + "\n")
        states[state] = states.get(state, 0) + 1
        code = max(code, OUTCOME_CODE[state])
        if "traceback" in res:
            errors += 1
        w = f"{worst:.2f}" if worst is not None else "--"
        note = why or res.get("note", "")
        print(f"{cid:<7}{state:<12}{w:>7}  {note[:60]}")

    print("-" * 96)
    print("  ".join(f"{k}: {v}" for k, v in sorted(states.items())))
    if a.expect:
        outcome, reason = control_outcome(
            a.expect, a.inject, [c["case_id"] for c in chosen], plans,
            clean_verdicts, injected_verdicts)
        if outcome == "refused":
            print(f"CONTROL REFUSED: {reason}", file=sys.stderr)
            return 2
        if outcome == "fail":
            print(f"CONTROL DID NOT FIRE: {reason}", file=sys.stderr)
            return 1
        print(f"control fired: {reason}")
        return 0
    if errors:
        print(f"{errors} case(s) hit an unexpected internal error; see the result JSON",
              file=sys.stderr)
    print(f"exit {code}: {OUTCOME_MEANING}")
    return code


if __name__ == "__main__":
    sys.exit(main())
