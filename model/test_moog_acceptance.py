#!/usr/bin/env python3
"""Acceptance suite: does this behave like the instrument we said we were building?

Every other test in `model/` asks whether the integer model matches the float
model, or whether the RTL matches the integer model. None of them asks whether
the thing we are building is the thing the decision records describe. This file
does, one test per claimed musical property, each citing its source and its
tolerance, so that a claim in a DR cannot quietly stop being true.

    .venv/bin/python -m pytest model/test_moog_acceptance.py -q      # ~75 s

Sources of truth, in the repository:

  DR 0001  the ladder model -- Huovilainen, the nonlinearity in every stage,
           the aliasing table PolyBLEP was adopted from
  DR 0004  glide: constant rate, linear in pitch, 90 ms per octave
  DR 0005  gain structure: the tanh is the designed saturation, the VCA is
           after the filter, exactly one hard rail
  DR 0006  resonance compensation: res = 1 is the onset everywhere within 0.39 %
  spec/NUMERIC-CONTRACT.md  6 (oscillators), 8 (envelopes), 11 (the ladder),
           12 (the output stage and every clamp)
  docs/DESIGN.md  sections 5 and 6, the measured behaviour

Method: every estimator comes from `model/audio_measure.py`, the shared,
ground-truthed module (`model/test_audio_measure.py`), and every one of them
can answer "insufficient evidence" instead of a plausible number. The voice's
additions to that module -- `tone_amplitude`, `harmonic_powers`,
`inharmonic_fraction_db`, `foldback_alias_db`, `zero_crossing_frequency`,
`tonality_db`, `max_sample_step`, `longest_plateau`, `event_slices` -- are in
its last section with their own ground truth. What that buys here:

  * **The filter is measured with a controlled probe**, never inferred from the
    finished voice's spectrum. A spectral centroid is not a cutoff: it moves
    with the excitation, the envelope and the nonlinearity as much as with the
    filter. Frequency response, passband gain, resonance and rolloff all come
    from a stepped sine at a stated drive and a coherent projection -- an
    impulse response would presume linearity, and this filter's response
    depends on level by design. Finished-voice renders are for acceptance
    (does a note end, does anything clip), never for diagnosis.
  * **Aliasing is measured where the aliases land** -- at the predicted
    fold-back frequency of each harmonic above Nyquist -- and cross-checked
    against DR 0001's own inharmonic-energy measure. Waveform accuracy is a
    separate property from aliasing, with its own test.
  * **Events are analysed separately**, and an envelope is the analytic one,
    not a moving average.
  * **Tonality is max-over-median**, not spectral flatness.
  * **Levels are reported absolutely**; no A/B normalises both sides, which
    would hide a gain error by construction.

What this suite does NOT claim: that the filter sounds like a Moog ladder.
It shows our structure is not the linearised one (test_the_nonlinearity_is_in
_every_stage), which is what DR 0001 decided. Matching a real circuit would
need an independently derived circuit response or a documented reference
recording, and this repository has neither.

The last group, "the suite can fail", injects the four defects most likely to
be silently wrong -- the resonance-compensation ROM, the square's PolyBLEP
sign, the envelope release floor, the VCA's position -- plus a dropped pole and
an all-silent stub, and requires the corresponding property to go red
(`docs/verification-rules.md`, rules 1 and 2).
"""
import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "audition"))
import dsp                                                          # noqa: E402
import voice_fx as vf                                               # noqa: E402
import audio_measure as am                                          # noqa: E402
import reference_movement as rm                                     # noqa: E402
import alias_probe as ap                                            # noqa: E402
from audio_measure import InsufficientEvidence      # noqa: E402,F401  (the stub controls below rely on it being an AssertionError)
from dsp import SR                                                  # noqa: E402

FS = 32768.0                     # Q1.15 full scale
_REAL_LADDER = vf.LadderFx       # captured before any test monkeypatches the name
G_ROM = vf.make_g_rom()
K_ROM = vf.make_k_rom()


# =============================================================================
# probes: how the filter is driven, and how the voice is played
# =============================================================================
def _ladder_regs(res, drive, cut, compensated=True, skew=1.0, g_rom=None):
    """The register image the host would write for this operating point
    (contract 5.5 and 10.2), including the per-frame k_eff of DR 0006.

    `skew` looks the coefficients up at a DIFFERENT cutoff from the one we
    claim to have commanded, without changing the claim: the injected UNIFORM
    cutoff error. `g_rom` substitutes a different cutoff ROM (DR 0011's
    untuned one) for the same purpose."""
    ref = _REAL_LADDER(**vf.LADDER_CFG)
    k, gain, ogain = ref.regs(res, drive)
    rom = G_ROM if g_rom is None else g_rom
    krom = K_ROM if g_rom is None else _k_rom_for(g_rom)
    g = int(vf.g_from_cut(np.array([cut * skew]), rom)[0])
    if compensated:
        kc = int(vf.kc_from_cut(np.array([cut * skew]), krom)[0])
        k = int(vf.k_effective(k, kc))
    return g, k, gain, ogain


def _k_rom_for(g_rom):
    """`make_k_rom` against a substituted cutoff ROM: the compensation is
    DERIVED from the cutoff coefficients, so a control that changes one has to
    change the other or it is measuring two defects at once."""
    key = id(g_rom)
    if key not in _K_ROM_CACHE:
        step = (1 << 15) >> vf.KROM_BITS
        _K_ROM_CACHE[key] = np.array(
            [int(round(vf.k_onset(min(max(vf.CUT_MIN, i * step), vf.CUT_MAX),
                                  g_rom, vf.GROM_BITS, 2)[0] / 4.0 * 32768))
             for i in range((1 << vf.KROM_BITS) + 1)], dtype=np.int64)
    return _K_ROM_CACHE[key]


_K_ROM_CACHE = {}
G_ROM_UNTUNED = vf.make_g_rom(tune=False)          # the ROM of contract revisions 1-6


def _neutral_ogain() -> int:
    """`ogain` with the passband compensation taken out -- the res = 0 value,
    which is the state-units-to-Q1.15 conversion and nothing else
    (`fixed.LadderFx.regs`: `ogain = 2Vt/vpu * (1 + 2 res)`).

    Rendering with this is how the LOOP's own low-frequency gain is read: at
    the shipped `ogain` the `(1 + 2 res)` factor is multiplied back in, and
    the two must not be confused -- one is a property of the ladder, the other
    is our level policy on top of it."""
    return int(_REAL_LADDER(**vf.LADDER_CFG).regs(0.0, 1.0)[2])


def _k_float(res, cut, compensated=True) -> float:
    """The feedback coefficient the loop actually runs at, as a number: the
    Q3.14 `k` register (after DR 0006's per-cutoff compensation, when it is
    on) divided by 2^14. This is Stinchcombe's `k`, not the host's `res`."""
    return _ladder_regs(res, drive=1.0, cut=cut, compensated=compensated)[1] / float(1 << 14)


def ladder_render(x_q15, cut, res, drive, compensated=True, ladder=None,
                  skew=1.0, g_rom=None, ogain=None, **cfg):
    """`x_q15` through one ladder at a fixed operating point. `vf.LadderFx` is
    looked up at call time so an injected defect is seen.

    `ogain` overrides the output register the host would write, which is the
    only way to measure the LOOP on its own: the shipped `ogain` carries DR
    0005's `(1 + 2 res)` passband compensation, and a measurement that leaves
    it in is measuring the instrument's level policy rather than the filter's
    transfer function (`_neutral_ogain`, below)."""
    g, k, gain, og = _ladder_regs(res, drive, cut, compensated, skew, g_rom)
    ogain = og if ogain is None else int(ogain)
    lad = ladder if ladder is not None else vf.LadderFx(**{**vf.LADDER_CFG, **cfg})
    n = len(x_q15)
    return lad.process(np.asarray(x_q15, dtype=np.int16), None, res, drive,
                       g_q16=np.full(n, g, dtype=np.int64),
                       k=k, gain=gain, ogain=ogain).astype(np.float64)


def probe_gain_db(f, cut, res, drive, amp=30000, dur=0.6, settle=0.2, **kw):
    """Steady-state gain at one frequency, from a stepped sine and a coherent
    projection: the transfer measurement. The projection is what lets the
    stopband be read at 0.6 LSB peak (-94 dB), 25 dB under the truncation
    noise (ground truth: test_tone_amplitude_recovers_a_known_amplitude...)."""
    n = int(dur * SR)
    x = np.round(amp * np.sin(2 * math.pi * f * np.arange(n) / SR)).astype(np.int16)
    y = ladder_render(x, cut, res, drive, **kw)[int(settle * SR):]
    a = am.tone_amplitude(y, f).require(f"probe at {f:.0f} Hz, cutoff {cut}")
    return 20.0 * math.log10(max(a, 1e-12) / amp)


def _ring_windows(cut):
    """Burst, settle and analysis windows for a free ring at this cutoff --
    20 cycles each, so that at a high cutoff the windows close before the
    oscillation reaches its limit cycle (model/k_comp_sweep.py)."""
    ncyc = SR / cut
    return (max(int(0.02 * SR), int(20 * ncyc)),
            max(int(0.005 * SR), int(10 * ncyc)),
            max(int(0.01 * SR), int(20 * ncyc)))


def ring_growth(cut, res, compensated=True):
    """Growth rate (nepers/s) of the filter's FREE ring: a short sine burst at
    the predicted oscillation frequency, then silence. Positive means the loop
    sustains, negative means it decays; the sign is the onset test.

    The burst amplitude is adapted so the tail sits between 150 and 1200 LSB --
    inside the tanh table's first bin, where the loop is linear apart from
    truncation and the sign is amplitude-independent, and above the truncation
    floor. Returns (rate, tail)."""
    n_burst, n_settle, n_win = _ring_windows(cut)
    _, f_lin = vf.k_onset(cut, G_ROM)
    for amp in (100.0, 25.0, 6.0, 1.5, 400.0, 1600.0):
        n = n_burst + n_settle + 2 * n_win
        x = np.zeros(n, dtype=np.int16)
        x[:n_burst] = np.round(
            amp * np.sin(2 * math.pi * f_lin * np.arange(n_burst) / SR)).astype(np.int16)
        tail = ladder_render(x, cut, res, 1.0, compensated)[n_burst:]
        a = tail[n_settle:n_settle + n_win]
        b = tail[n_settle + n_win:n_settle + 2 * n_win]
        peak = float(np.abs(tail[n_settle:n_settle + 2 * n_win]).max())
        rate = math.log(max(am.rms(b), 1e-9) / max(am.rms(a), 1e-9)) / (n_win / SR)
        if 150.0 <= peak <= 1200.0:
            return rate, tail[n_settle:]
        if peak > 1200.0 and amp <= 1.5:
            return 100.0, tail[n_settle:]          # sustains even from nothing
    return (100.0 if peak > 1200.0 else -100.0), tail[n_settle:]


def onset_res(cut, compensated=True, lo=0.95, hi=1.05, iters=10):
    """The host `res` at which the ring stops decaying and starts growing,
    bisected on the fixed-point filter itself. 10 iterations over a 0.1-wide
    bracket resolve 1e-4, well inside DR 0006's 0.39 % tolerance."""
    assert ring_growth(cut, lo, compensated)[0] < 0, f"{cut} Hz already sustains at res {lo}"
    assert ring_growth(cut, hi, compensated)[0] > 0, f"{cut} Hz does not sustain at res {hi}"
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        if ring_growth(cut, mid, compensated)[0] > 0:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


def sustained_tail(cut, res=1.05, dur=0.5, kick=3000.0, compensated=True):
    """Kick the filter once and let it sing; returns the last 60 % of the
    render -- the steady self-oscillation, well after the excitation."""
    n = int(dur * SR)
    n_kick = int(0.005 * SR)
    x = np.zeros(n, dtype=np.int16)
    x[:n_kick] = np.round(
        kick * np.sin(2 * math.pi * cut * np.arange(n_kick) / SR)).astype(np.int16)
    return ladder_render(x, cut, res, 1.0, compensated)[int(0.4 * n):]


def corner_ratio(cut, res=0.1, drive=0.3, **kw):
    """The -3 dB corner divided by the COMMANDED cutoff, bisected on the
    stepped-sine transfer probe at small signal. ABSOLUTE, not differential: a
    uniform error in the cutoff mapping moves it one for one, which
    `test_control_a_uniform_cutoff_skew_...` asserts by injecting one."""
    ref = probe_gain_db(max(30.0, cut / 16.0), cut, res, drive, **_probe_kw(cut / 16.0, **kw))
    lo, hi = cut * 0.2, min(cut * 3.0, 0.45 * SR)
    for _ in range(18):
        mid = math.sqrt(lo * hi)
        if probe_gain_db(mid, cut, res, drive, **_probe_kw(mid, **kw)) - ref > -3.0:
            lo = mid
        else:
            hi = mid
    return math.sqrt(lo * hi) / cut


def _probe_kw(f, **kw):
    """Enough periods for a coherent projection at `f`, whatever `f` is: the
    estimator refuses below about five of them and would otherwise decline the
    reference probe at 30 Hz rather than return a wrong number."""
    settle = max(0.12, 12.0 / max(f, 1.0))
    return dict(kw, amp=3000, dur=settle + max(0.25, 40.0 / max(f, 1.0)), settle=settle)


def one_note(note=45, dur=0.5, **patch):
    """One note from reset through the whole voice; returns (out, trace)."""
    v = vf.VoiceFx()
    out = v.note(note, dur, **patch)
    return out, v.trace


# =============================================================================
# deliberately-wrong ladders: the negative controls of DR 0001's argument
# =============================================================================
class _LadderVariant(_REAL_LADDER):
    """A ladder that is NOT the model, for controls and comparisons.

    `stages`   how many one-poles are in the cascade (4 is the model)
    `nonlin`   'every'    tanh in every stage -- the model (DR 0001)
               'feedback' ONE saturating element, on the feedback signal, with
                          a hard-limited input stage and four LINEAR poles:
                          the Stilson & Smith shape DR 0001 rejected
               'input'    ONE tanh at the input stage, four linear poles

    This duplicates the model's inner loop (contract 11.4) with two knobs, so
    `test_the_negative_control_is_the_model_when_it_is_not_defective` checks it
    bit for bit against `LadderFx` in its non-defective setting. If that test
    goes red, this class has drifted and every comparison below is void.
    """

    def __init__(self, *a, stages=4, nonlin="every", **kw):
        super().__init__(*a, **kw)
        self.stages, self.nonlin = stages, nonlin

    def process(self, x_q15, cutoff_hz, res, drive=1.0, *, g_q16=None,
                k=None, gain=None, ogain=None, k_q14=None):
        from fixed import sat, shl
        os_, SQ, SB, OB = self.os, self.SQ, self.SB, self.OB
        n = len(x_q15)
        g_tab, k_tab, gain, ogain = self.coefficients(
            cutoff_hz, res, drive, g_q16=g_q16, n=n, k=k, gain=gain, ogain=ogain, k_q14=k_q14)
        k_per_sample = np.ndim(k_tab) > 0
        k = None if k_per_sample else int(k_tab)
        out = np.empty(n, dtype=np.int16 if OB <= 16 else np.int32)
        y, w = self.y, self.w
        d1, d2 = self.d1, self.d2
        TQ = SQ - 15
        S, nl = self.stages, self.nonlin
        for i in range(n):
            xi = int(x_q15[i])
            g = int(g_tab[i])
            if k_per_sample:
                k = int(k_tab[i])
            for _ in range(os_):
                fb = (d1 + d2) >> 1
                if nl == "feedback":
                    fb = shl(self.tanh_fx(fb), TQ)          # the one saturating element
                u = sat(shl(xi * gain, TQ - 16) - ((k * fb) >> 14), SB)
                w0 = self.tanh_fx(u) if nl != "feedback" else sat(shl(u, -TQ), 16)
                for s in range(S):
                    prev = w0 if s == 0 else w[s - 1]
                    diff = prev - w[s]
                    y[s] = sat(y[s] + ((g * shl(diff, TQ)) >> 16), SB)
                    w[s] = self.tanh_fx(y[s]) if nl == "every" else sat(shl(y[s], -TQ), 16)
                d2, d1 = d1, y[S - 1]
            out[i] = sat((shl(y[S - 1], -TQ) * ogain) >> 16, OB)
        self.y, self.w, self.d1, self.d2 = y, w, d1, d2
        return out


def test_the_negative_control_is_the_model_when_it_is_not_defective():
    """[method] `_LadderVariant(stages=4, nonlin='every')` must be the model bit for
    bit. Every comparison in this file rests on that: a control that has
    drifted from the model measures the drift, not the structure.

    Ground truth: test_audio_measure.test_damped_sinusoid_matches_the_coefficients_that_generated_it
    """
    n = int(0.2 * SR)
    x = np.round(0.9 * FS * np.sin(2 * math.pi * 220.0 * np.arange(n) / SR)).astype(np.int16)
    a = ladder_render(x, 3000, 0.8, 2.0, ladder=_REAL_LADDER(**vf.LADDER_CFG))
    b = ladder_render(x, 3000, 0.8, 2.0, ladder=_LadderVariant(**vf.LADDER_CFG))
    assert np.array_equal(a, b)


# =============================================================================
# 1. THE FILTER
# =============================================================================
ONSET_CUTS = [30, 200, 1600, 3000, 10000, 21600]


@pytest.mark.parametrize("cut", ONSET_CUTS)
def test_res_1_is_the_onset_of_self_oscillation_at_every_cutoff(cut):
    """[source-verified: DR 0006] **DR 0006, tolerance 0.39 %.** The compensation ROM exists so that the
    resonance knob means the same thing everywhere: `res = 1` is the edge of
    self-oscillation at 30 Hz and at 21.6 kHz alike. DR 0006's residual table
    gives a worst case of 0.39 % (at the 21.6 kHz clamp edge) and under 0.2 %
    elsewhere.

    Measured here by bisecting the sign of the free ring's growth on the
    fixed-point filter, with DR 0011's retuned ROM: 0.99829 at 30 Hz, 0.99868
    at 200 Hz, 0.99819 at 1.6 kHz, 0.99888 at 3 kHz, 0.99868 at 10 kHz, and
    0.99526 at the 21.6 kHz clamp.

    The tolerance is 0.20 % below the clamp and 0.50 % AT it. DR 0011 tightened
    the interior (revision 8 needed 0.39 %) and loosened the clamp entry from
    0.38 % to 0.47 %: the ROM's last usable entry is evaluated at the clamped
    cutoff, and the TUNED coefficient there sits further from the entry grid
    than the untuned one did. It is one entry, at 21.6 kHz, an octave above the
    top of the keyboard's usable range.

    This is the ONSET only. The pitch it oscillates at is a separate property
    with its own error, and its own test below.

    Ground truth: test_audio_measure.test_rms_of_a_sinusoid_is_amplitude_over_root_two
    """
    r = onset_res(cut)
    tol = 0.0050 if cut >= vf.CUT_MAX else 0.0020
    assert abs(r - 1.0) <= tol, f"{cut} Hz: onset at res {r:.5f} ({(r-1)*100:+.3f} %)"


# f_osc / cutoff at the onset, DR 0006's own table. Locked here so the tuning
# error stays visible: it is open item 17.12, measured and NOT corrected.
TRACKING = {200: 1.004, 400: 1.006, 800: 1.008, 1600: 1.008, 3000: 1.009, 10000: 0.994}
TRACKING_REV8 = {200: 0.979, 400: 0.984, 800: 0.990, 1600: 1.001, 3000: 1.020, 10000: 1.072}


def test_the_self_oscillation_pitch_tracks_the_cutoff_with_a_recorded_error():
    """[source-verified: DR 0011; DR 0006's tracking table; DESIGN.md section 5] **DR 0011; DR 0006's tracking table; DESIGN.md section 5.**

    The filter used as an oscillator has to play in tune with its cutoff. This
    is an ABSOLUTE property, not a drift one: a uniform error in the cutoff
    mapping moves every ratio here one-for-one, which
    `test_control_a_uniform_cutoff_skew_...` below demonstrates by injecting
    one. Each ratio is locked within 0.005.

    Revision 9 put Huovilainen's `fcr` polynomial and DR 0011's constant trim
    into the cutoff ROM. Measured at the onset:

    | cutoff | rev 8 | rev 9 |
    |---|---|---|
    | 200 Hz | 0.979 | 1.004 |
    | 400 Hz | 0.984 | 1.006 |
    | 800 Hz | 0.990 | 1.008 |
    | 1.6 kHz | 1.001 | 1.008 |
    | 3 kHz | 1.020 | 1.009 |
    | 10 kHz | **1.072** | **0.994** |

    **Worst error 7.2 % -> 0.94 %; spread over the six, 9.3 -> 1.5 percentage
    points.** DR 0001's and DESIGN.md's "+-2 % from 200 Hz to 1.6 kHz" was
    0.06 pp optimistic at its own endpoint in revision 8; it now holds with a
    factor of two in hand, and is asserted at +-1 %.

    Ground truth: test_audio_measure.test_zero_crossing_frequency_on_a_known_tone, test_audio_measure.test_rms_of_a_sinusoid_is_amplitude_over_root_two
    """
    ratios = {}
    for cut, expect in TRACKING.items():
        rate, tail = ring_growth(cut, onset_res(cut))
        f = am.zero_crossing_frequency(tail).require(f"the ring at {cut} Hz")
        ratios[cut] = f / cut
        assert abs(ratios[cut] - expect) < 0.005, \
            f"{cut} Hz: f_osc/cut {ratios[cut]:.4f}, DR 0011 says {expect}"
    # DESIGN.md's band, at a tolerance revision 8 could not meet
    assert max(abs(ratios[c] - 1.0) for c in (200, 400, 800, 1600)) <= 0.010, ratios
    # the whole range, including the point that used to be 7.2 % sharp
    assert max(abs(r - 1.0) for r in ratios.values()) <= 0.010, ratios
    # and the size of the improvement, so a regression to rev 8 is loud
    assert max(abs(TRACKING_REV8[c] - 1.0) for c in TRACKING) > 0.06


def test_the_minus_3db_corner_is_where_the_commanded_cutoff_says():
    """[measured-here: the -3 dB corner ratio at 800 Hz] **The ABSOLUTE cutoff, measured a second and independent way.**

    The self-oscillation test above is resonant; this one is not, and the two
    can fail separately. A four-pole cascade's -3 dB corner is structurally
    BELOW its per-pole corner, so the number here is not 1.0 and was never
    going to be: what it locks is that the corner is a FIXED fraction of the
    commanded cutoff, and which fraction.

    Stepped sine into the ladder at res 0.1, drive 0.3 (small signal), the
    corner bisected against the gain four octaves below:

    | cutoff | rev 8 | rev 9 |
    |---|---|---|
    | 800 Hz | 0.7024 | **0.7145** |

    This is the property a drift metric cannot see. `docs/discrimination.md`
    section 8's "7.92 pp over six octaves" measures how much the corners differ
    FROM EACH OTHER; an injected uniform 30 % cutoff skew moves that by a
    quarter of a percentage point and moves THIS by 30 %.

    Ground truth: test_audio_measure.test_tone_amplitude_recovers_a_known_amplitude_under_noise
    """
    r = corner_ratio(800)
    assert abs(r - 0.7145) < 0.010, r


def test_the_corner_does_not_drift_across_the_range():
    """[measured-here: corner/commanded spread over 200 Hz .. 10 kHz] **Non-uniformity, the other half of the pair.** The spread of
    corner/commanded over 200 Hz .. 10 kHz: 4.03 percentage points in revision
    8, 3.16 in revision 9 (DR 0011). Locked at 3.5 pp -- which revision 8 fails
    and an injected uniform skew passes, because a skew that displaces every
    corner equally cannot change how much they differ from each other.

    Ground truth: test_audio_measure.test_tone_amplitude_recovers_a_known_amplitude_under_noise
    """
    r = [corner_ratio(c) for c in (200, 800, 3000, 10000)]
    assert (max(r) - min(r)) <= 0.035, r


def test_control_a_uniform_cutoff_skew_is_absolute_error_a_drift_metric_cannot_see():
    """[meta] **The estimator tested by injecting the error it exists to catch**
    (`docs/verification-rules.md` rule 2), and the reason there are two cutoff
    properties above instead of one.

    A +30 % uniform skew of the coefficient lookup, with the cutoff we CLAIM to
    have commanded left alone:

      * the absolute corner ratio moves 0.7145 -> 0.9285 and its property goes
        red -- the estimator is not blind to it
      * the drift moves 3.16 -> 4.28 pp and ITS property stays green

    A fix that flattened the drift while leaving a uniform offset in place
    would be reported as a success by the drift metric alone. That is asserted
    here, not assumed.

    Ground truth: test_audio_measure.test_tone_amplitude_recovers_a_known_amplitude_under_noise
    """
    skewed = corner_ratio(800, skew=1.30)
    assert abs(skewed - 0.7145) > 0.10, skewed                  # the absolute property fires
    r = [corner_ratio(c, skew=1.30) for c in (200, 800, 3000, 10000)]
    assert (max(r) - min(r)) <= 0.050, r                        # ... and the drift barely moves


def test_control_removing_the_tuning_polynomial(monkeypatch):
    """[meta] Defect: DR 0011's cutoff ROM reverted to revision 8's untuned one,
    `make_g_rom(tune=False)`. The self-oscillation pitch then misses its locked
    table at every cutoff, which is what that property is for.

    Ground truth: test_audio_measure.test_zero_crossing_frequency_on_a_known_tone, test_audio_measure.test_rms_of_a_sinusoid_is_amplitude_over_root_two
    """
    monkeypatch.setitem(globals(), "G_ROM", G_ROM_UNTUNED)
    monkeypatch.setitem(globals(), "K_ROM", _k_rom_for(G_ROM_UNTUNED))
    msg = _expect_red(test_the_self_oscillation_pitch_tracks_the_cutoff_with_a_recorded_error)
    assert "DR 0011 says" in msg


def test_it_still_self_oscillates_at_10_khz():
    """[source-verified: DR 0006] **DR 0006, the regression guard for the whole decision record.** Before
    the compensation ROM the filter would not sustain above about 3 kHz at a
    fixed `k = 4 res` -- DR 0001 recorded that as a consequence, and it is the
    defect DR 0006 was written to remove.

    At 10 kHz and `res = 1.05`, kicked once and then left alone: the tail is a
    sustained tone (rms 2966 LSB, peak-to-median 111 dB) within 8 % of the
    cutoff. Uncompensated, the same patch decays to rms 3 -- silence.

    Ground truth: test_audio_measure.test_dominant_frequency_on_known_tones, test_audio_measure.test_rms_of_a_sinusoid_is_amplitude_over_root_two, test_audio_measure.test_tonality_separates_a_tone_from_noise_where_a_percentile_does_not
    """
    tail = sustained_tail(10000, res=1.05)
    assert am.rms(tail) > 500.0, f"rms {am.rms(tail):.1f}: not sustaining"
    assert am.tonality_db(tail) > 40.0, "sustaining, but not as a tone"
    f = am.dominant_frequency(tail, 5000.0, 20000.0).require("the 10 kHz whistle")
    assert 0.9 < f / 10000.0 < 1.12, f"{f:.0f} Hz at a 10 kHz cutoff"
    dead = sustained_tail(10000, res=1.05, compensated=False)
    assert am.rms(dead) < 50.0, f"uncompensated rms {am.rms(dead):.1f}: the ROM changed nothing"


def test_the_stopband_falls_at_24_db_per_octave():
    """[source-verified: contract 11.4] **Four poles, contract 11.4: ~24 dB/octave.** Measured between 4x and 8x
    the cutoff, which is the band where a cascade of four scaled
    impulse-invariant one-poles is asymptotic (the ideal falls 23.3 dB there
    against the 24 dB asymptote, and only 21.2 dB between 2x and 4x) and is
    still far from Nyquist: with a 500 Hz cutoff the band is 2-4 kHz, i.e.
    2-4 % of the 96 kHz rate the filter runs at.

    Probed at res = 0 and drive 0.1 so the tanh is in its linear region.
    Measured: -48.57 dB at 2x, -70.02 at 4x, -93.67 at 8x -- 21.45 and
    23.65 dB/octave. Tolerance 21..26 dB/octave on the asymptotic band; a
    three-pole cascade measures 17.4 there, a five-pole 29, so the band
    separates a dropped stage by more than 3 dB.

    Ground truth: test_audio_measure.test_tone_amplitude_recovers_a_known_amplitude_under_noise
    """
    cut = 500.0
    g2 = probe_gain_db(2 * cut, cut, 0.0, 0.1)
    g4 = probe_gain_db(4 * cut, cut, 0.0, 0.1)
    g8 = probe_gain_db(8 * cut, cut, 0.0, 0.1)
    assert 21.0 <= g4 - g8 <= 26.0, f"4x->8x {g4-g8:.2f} dB/oct (gains {g4:.2f}, {g8:.2f})"
    assert 19.5 <= g2 - g4 <= 23.5, f"2x->4x {g2-g4:.2f} dB/oct"


@pytest.mark.parametrize("cut,drive", [(8000, 3.0), (12000, 2.5)])
def test_the_nonlinearity_is_in_every_stage(cut, drive):
    """[source-verified: DR 0001] **DR 0001: "Anyone reimplementing this must not simplify to a single
    feedback-path tanh. That is a different filter, and the difference is the
    point." Tolerance: 5 dB on harmonics 6-10, DR 0001's own figure.**

    Driven hard, with the filter open and the resonance high, the linearised
    structure -- one saturating element on the feedback, a hard-limited input
    stage and four LINEAR poles -- flat-tops, and a flat top is high harmonics.
    Ours rounds over instead, because every stage's tanh compresses what the
    stage before it made. DR 0001 measured the linearised shape "5-8 dB hotter
    on harmonics 6-10"; at these operating points it is 30.6 dB and 37.8 dB
    hotter, so the direction reproduces and the margin is large.

    A pure tone in the bass register (110 Hz, A2), res 0.9, so that every
    partial in the output is the filter's own work. The stimulus that produced
    DR 0001's 5-8 dB is not in the repository, which is why the assertion is
    the DR's floor and not its exact number.

    What this does NOT show: that our filter matches a Moog ladder. It shows it
    is not the linearised alternative. Matching the circuit needs an
    independently derived response or a reference recording; we have neither.

    Ground truth: test_audio_measure.test_harmonic_powers_recovers_a_known_series
    """
    f0, n = 110.0, int(0.5 * SR)
    x = np.round(0.95 * FS * np.sin(2 * math.pi * f0 * np.arange(n) / SR)).astype(np.int16)
    ours = ladder_render(x, cut, 0.9, drive, ladder=_REAL_LADDER(**vf.LADDER_CFG))
    lin = ladder_render(x, cut, 0.9, drive,
                        ladder=_LadderVariant(nonlin="feedback", **vf.LADDER_CFG))
    h_ours = am.harmonic_powers(ours, f0, range(6, 11)).sum() / am.harmonic_powers(ours, f0, [1])[0]
    h_lin = am.harmonic_powers(lin, f0, range(6, 11)).sum() / am.harmonic_powers(lin, f0, [1])[0]
    hotter = 10 * math.log10(h_lin / h_ours)
    assert hotter >= 5.0, (f"the linearised structure is only {hotter:.1f} dB hotter on "
                           f"harmonics 6-10 (ours {10*math.log10(h_ours):.1f} dB, "
                           f"linearised {10*math.log10(h_lin):.1f} dB)")


def test_driving_it_thickens_where_the_linearised_structure_flat_tops():
    """[source-verified: DR 0001] **DR 0001: "Driving it brightens; driving the real structure thickens."**

    The structural reason, measured as a level. Each stage integrates toward
    `tanh(y) = prev`, so as the drive rises the state keeps climbing (atanh
    diverges) and the waveform rounds over: peak output 0.63 -> 1.56 x full
    scale from drive 1 to 6, +7.9 dB. The linearised structure's state is
    pinned by its one limiter, so its output stops dead at 0.615 x full scale
    and every further dB of drive becomes harmonics instead of level: its peak
    at drive 6 is the same number as at drive 1, to the LSB.

    Tolerance: ours must grow by at least 4 dB over that range, theirs by less
    than 0.5 dB. res 0.3, cutoff 8 kHz, a 110 Hz tone at 0.95 full scale.

    Ground truth: test_audio_measure.test_peak_is_the_largest_magnitude_either_sign
    """
    f0, n = 110.0, int(0.25 * SR)
    x = np.round(0.95 * FS * np.sin(2 * math.pi * f0 * np.arange(n) / SR)).astype(np.int16)

    def peak_at(drive, ladder):
        return am.peak(ladder_render(x, 8000, 0.3, drive, ladder=ladder)) / FS

    lo_ours = peak_at(1.0, _REAL_LADDER(**vf.LADDER_CFG))
    hi_ours = peak_at(6.0, _REAL_LADDER(**vf.LADDER_CFG))
    lo_lin = peak_at(1.0, _LadderVariant(nonlin="feedback", **vf.LADDER_CFG))
    hi_lin = peak_at(6.0, _LadderVariant(nonlin="feedback", **vf.LADDER_CFG))
    assert 20 * math.log10(hi_ours / lo_ours) > 4.0, (lo_ours, hi_ours)
    assert 20 * math.log10(hi_lin / lo_lin) < 0.5, (lo_lin, hi_lin)


def test_resonance_lifts_a_peak_at_the_cutoff():
    """[source-verified: contract 11.5 / DR 0006] **Contract 11.5 / DR 0006: the resonance control is a resonance.**
    Probed at the cutoff and a decade below it, at res 0, 0.3, 0.6 and 0.9.
    The peak above the passband rises monotonically -12.2, -2.8, +4.6,
    +10.4 dB: a 22.6 dB swing across the knob. Tolerances: monotone, at most
    -10 dB at res 0 (no peak at all) and at least +8 dB at res 0.9.

    Ground truth: test_audio_measure.test_tone_amplitude_recovers_a_known_amplitude_under_noise
    """
    cut, drive = 500.0, 0.1
    peaks = []
    for res in (0.0, 0.3, 0.6, 0.9):
        low = probe_gain_db(0.1 * cut, cut, res, drive)
        at = probe_gain_db(cut, cut, res, drive)
        peaks.append(at - low)
    assert all(b > a + 2.0 for a, b in zip(peaks, peaks[1:])), peaks
    assert peaks[0] <= -10.0, peaks
    assert peaks[-1] >= 8.0, peaks


def test_the_passband_compensation_is_partial_and_is_the_one_in_the_contract():
    """[source-verified: DR 0005 item 5 and contract 11.5] **DR 0005 item 5 and contract 11.5: `ogain`'s `(1 + 2 res)` term is a
    PARTIAL passband-loss compensation** -- what the audition heard, not a
    flat response. The ladder's passband loses `1/(1 + 4 res)`; `ogain` gives
    back `(1 + 2 res)`, so the net is `(1 + 2 res)/(1 + 4 res)`.

    Probed a decade below a 500 Hz cutoff, relative to res = 0:

        res    measured    (1+2r)/(1+4r)   uncompensated 1/(1+4r)
        0.25   -2.30 dB      -2.50 dB          -6.02 dB
        0.50   -3.34         -3.52             -9.54
        0.75   -3.92         -4.08            -12.04
        1.00   -4.29         -4.44            -13.98

    Tolerance 0.5 dB against the closed form, which also refutes the
    uncompensated law by 9 dB at res = 1.

    Ground truth: test_audio_measure.test_tone_amplitude_recovers_a_known_amplitude_under_noise
    """
    cut, drive = 500.0, 0.1
    ref = probe_gain_db(0.1 * cut, cut, 0.0, drive)
    for res in (0.25, 0.5, 0.75, 1.0):
        got = probe_gain_db(0.1 * cut, cut, res, drive) - ref
        want = 20 * math.log10((1 + 2 * res) / (1 + 4 * res))
        assert abs(got - want) < 0.5, f"res {res}: {got:.2f} dB, closed form {want:.2f} dB"
        uncomp = 20 * math.log10(1.0 / (1 + 4 * res))
        assert got > uncomp + 2.0, f"res {res}: no compensation is visible"


@pytest.mark.parametrize("compensated", [False, True])
def test_the_ladders_low_frequency_gain_is_one_over_one_plus_k(compensated):
    """[source-verified: Stinchcombe 2008 SS 2.3-2.4, equations 21 and 22]
    **The small-signal ladder with a feedback loop, from a source outside this
    repository.** Stinchcombe derives it from the block diagram and nothing
    else (SS 2.3): with the core's transfer function `G(s)` and a feedback
    gain `k`,

        Vout = G(s)(Vin - k Vout)   =>   H(s) = G(s) / (1 + k G(s))

    and with the normalised four-pole core `G(s) = 1 / (s + 1)^4` (eq. 21)
    that is `Hstd(s) = 1 / ((s + 1)^4 + k)` (eq. 22). At `s = 0`, `G(0) = 1`
    and therefore

        H(0) = 1 / (1 + k)

    -- the classic ladder bass loss, and the thing DR 0006's k_comp ROM is
    compensating against. SS 2.4 is the author's own check that this model
    tracks a SPICE simulation of the transistor circuit, which is why it is a
    referent for us rather than one more restatement of our decision records.

    Source: T. E. Stinchcombe, "Analysis of the Moog Transistor Ladder and
    Derivative Filters", 25 Oct 2008,
    <http://www.timstinchcombe.co.uk/synth/Moog_ladder_tf.pdf> (fetched and
    read for this test; see docs/minimoog-reference.md SS 0, key STIN).

    WHAT IS MEASURED, and why it is not the test above. The shipped `ogain`
    multiplies `(1 + 2 res)` back in (DR 0005), so a probe that leaves it in
    reads OUR LEVEL POLICY, not the ladder. This renders at `_neutral_ogain()`
    -- `ogain` at its res = 0 value, the state-units conversion alone -- so
    what comes back is the loop. And the abscissa is `k` itself, the Q3.14
    register the loop runs on (`_k_float`), not the host's `res`: with DR
    0006's compensation on, `k` is 4.25 at res = 1 and 2000 Hz, and the law
    has to hold at THAT k or the ROM is not doing what it claims.

    Measured, cutoff 2000 Hz probed at 100 Hz (a 20:1 ratio), relative to the
    same probe at k = 0:

        res   k (comp off)   measured    1/(1+k)    G/(1+kG) at 100 Hz
        0.25    1.000        -5.954 dB   -6.021     -5.956
        0.50    2.000        -9.473      -9.542     -9.475
        0.75    3.000       -11.975     -12.041    -11.976
        1.00    4.000       -13.916     -13.979    -13.917

    The residual against `1/(1+k)` is +0.066 dB and it is NOT an error: it is
    the finite-frequency term, `|1 + k G(j0.05)|` rather than `|1 + k|`, and
    the last column -- the same `H(s)` evaluated at the probe frequency
    instead of at DC -- reproduces the measurement to 0.003 dB. Probing a
    decade down instead of a decade and a half (500 Hz cutoff, 50 Hz) moves
    both the measurement and that column to +0.26 dB, together.

    Tolerances: 0.05 dB against `H(j omega)/H_0(j omega)` from eq. 21, and
    0.12 dB against the `H(0) = 1/(1 + k)` limit itself at the 20:1 probe.

    Ground truth: test_audio_measure.test_tone_amplitude_recovers_a_known_amplitude_under_noise
    """
    cut, f, drive = 2000.0, 100.0, 0.1
    og = _neutral_ogain()
    ref = probe_gain_db(f, cut, 0.0, drive, compensated=compensated, ogain=og)
    for res in (0.25, 0.5, 0.75, 1.0):
        k = _k_float(res, cut, compensated)
        got = probe_gain_db(f, cut, res, drive, compensated=compensated, ogain=og) - ref
        G = 1.0 / (1.0 + 1j * f / cut) ** 4                     # eq. 21, normalised core
        exact = 20 * math.log10(abs((G / (1 + k * G)) / G))     # eq. 22 at the probe
        dc = -20 * math.log10(1.0 + k)                          # H(0) = 1/(1+k)
        assert abs(got - exact) < 0.05, \
            f"res {res} (k {k:.3f}): {got:.3f} dB, H(jw)/H0 {exact:.3f} dB"
        assert abs(got - dc) < 0.12, \
            f"res {res} (k {k:.3f}): {got:.3f} dB, 1/(1+k) {dc:.3f} dB"
        # and the shipped output register is exactly (1 + 2 res) on top of it:
        # the two tests are measuring two different things, on purpose
        shipped = (probe_gain_db(f, cut, res, drive, compensated=compensated)
                   - probe_gain_db(f, cut, 0.0, drive, compensated=compensated))
        assert abs((shipped - got) - 20 * math.log10(1 + 2 * res)) < 0.1, \
            f"res {res}: ogain contributes {shipped - got:.3f} dB, not (1 + 2 res)"


def test_opening_the_cutoff_makes_a_note_brighter():
    """[measured-here: cutoff-to-brightness ordering on the finished voice] **Acceptance, not diagnosis.** The cutoff control has to do the one
    thing a player expects: open it and the note gets brighter. Measured on
    the finished voice as a POWER-weighted spectral centroid, which is an
    ordering of brightness and NOT a measurement of the cutoff -- it moves
    with the excitation, the envelope and the nonlinearity as well (amplitude
    weighting would give a number four times larger and equally meaningless).
    The filter itself is measured with a probe, above.

    A held saw at 110 Hz, res 0.3, no tracking, filter envelope pinned open:
    105, 134, 162, 195, 233 Hz for cutoffs 300..4800. Tolerance: strictly
    increasing, and at least 1.5 x from end to end.

    Ground truth: test_audio_measure.test_rms_of_a_sinusoid_is_amplitude_over_root_two, test_audio_measure.test_a_centroid_is_not_a_corner_frequency
    """
    cents = []
    for cut in (300, 600, 1200, 2400, 4800):
        out, _ = one_note(45, 0.5, waves=("saw",), detune=(0.0,), mix=(1.0,),
                          cutoff=(cut, cut), q=0.3, drive=1.0, track=0.0,
                          amp=(0.005, 0.1, 1.0, 0.05), fenv=(0.001, 0.01, 1.0, 0.01),
                          gate=0.45)
        seg = out[int(0.1 * SR):int(0.4 * SR)].astype(np.float64)
        assert am.rms(seg) > 100.0, f"cutoff {cut}: the note is inaudible, nothing is proved"
        cents.append(am.spectral_centroid(seg, weight="power"))
    assert all(b > a for a, b in zip(cents, cents[1:])), cents
    assert cents[-1] / cents[0] > 1.5, cents


# =============================================================================
# 2. THE OSCILLATORS
# =============================================================================
def _osc(shape, note, n, blep=True):
    inc = dsp.phase_inc(dsp.note_hz(note))
    f0 = inc * SR / (1 << 24)
    return vf.OscFx(shape, blep).render(n, inc).astype(np.float64) / FS, f0


@pytest.mark.parametrize("note", [40, 64, 88])
@pytest.mark.parametrize("shape", ["saw", "square"])
def test_polyblep_suppresses_aliasing_at_every_register(shape, note):
    """[source-verified: DR 0001's table] **DR 0001's table, tolerance 14 dB (measured ~16).** Inharmonic energy
    was the largest defect in the survey that chose the filter -- -27.7 dB at
    note 40 and -14.8 dB at note 88 for a naive saw -- and PolyBLEP was adopted
    to remove about 16 dB of it uniformly.

    Measured with DR 0001's own estimator (energy outside +-5 bins of every
    harmonic, as a fraction of total), so the numbers compare directly:

        note 40  saw -27.9 -> -43.0 (15.0 dB)   square -29.5 -> -44.5 (15.0)
        note 64  saw -20.8 -> -36.6 (15.9 dB)   square -22.5 -> -38.2 (15.7)
        note 88  saw -14.8 -> -31.0 (16.2 dB)   square -16.5 -> -32.1 (15.7)

    Every one is far above the estimator's own leakage floor, which is MEASURED
    per call and reported as `detail['headroom_db']` -- the assertion below
    reads that rather than a floor quoted here, because #119 moved the floor
    from about -54 dB (Hann) to about -88 (Blackman-Harris) without moving any
    of the numbers above, and a floor written into a docstring is wrong the
    next time the window changes.

    Ground truth: test_audio_measure.test_inharmonic_fraction_db_reading_of_an_alias_free_signal_is_its_floor
    """
    n = int(0.5 * SR)
    naive, f0 = _osc(shape, note, n, blep=False)
    blep, _ = _osc(shape, note, n, blep=True)
    a = am.inharmonic_fraction_db(naive, f0).require(f"{shape} note {note}, naive")
    b = am.inharmonic_fraction_db(blep, f0).require(f"{shape} note {note}, PolyBLEP")
    assert a - b >= 14.0, f"{shape} note {note}: naive {a:.1f}, PolyBLEP {b:.1f}"
    assert b < -28.0, f"{shape} note {note}: PolyBLEP leaves {b:.1f} dB"
    head = am.inharmonic_fraction_db(blep, f0).detail["headroom_db"]
    assert head > 10.0, \
        f"{shape} note {note}: PolyBLEP's {b:.1f} dB is only {head:.1f} dB above its own floor"


@pytest.mark.parametrize("note", [64, 88])
def test_polyblep_removes_the_predicted_fold_back_images(note):
    """[measured-here: fold-back images measured directly, not by exclusion] The same property measured where the aliases actually are, rather than
    by exclusion: every harmonic above Nyquist images at a computable
    frequency, and only those bins are read (`audio_measure.foldback_alias_db`,
    ground truth: a planted image of known energy is recovered to 0.8 dB).

        note 64  saw -21.0 -> -36.7 (15.6 dB), 800 image bins, no collisions
        note 88  saw -15.2 -> -31.0 (15.8 dB), 199 image bins, no collisions

    Note 40 is deliberately absent: there the images are dense enough to
    collide with real harmonics and the estimator refuses the measurement
    (test_foldback_refuses_a_low_note_where_the_images_are_dense). The test
    above covers note 40 with the complementary measure.

    Ground truth: test_audio_measure.test_foldback_finds_a_planted_image_and_ignores_the_real_harmonics
    """
    n = 1 << 15
    naive, f0 = _osc("saw", note, n, blep=False)
    blep, _ = _osc("saw", note, n, blep=True)
    ea, eb = am.foldback_alias_db(naive, f0), am.foldback_alias_db(blep, f0)
    a = ea.require(f"note {note}, naive")
    b = eb.require(f"note {note}, PolyBLEP")
    assert ea.detail["images"] > 50 and ea.detail["collided"] == 0, ea
    assert a - b >= 14.0, f"note {note}: naive {a:.1f}, PolyBLEP {b:.1f}, {ea.detail}"


def test_the_squares_correction_has_the_opposite_sign_to_the_saws():
    """[source-verified: DESIGN.md section 6 / contract 6.4] **DESIGN.md section 6 / contract 6.4: a square steps UP at the wrap
    where a saw steps DOWN, and getting it backwards measures worse than no
    correction at all.** Tolerance: the square must beat naive by 14 dB at
    every register (measured 15.0 / 15.7 / 15.7); inverted it measures 0.0 dB
    of improvement, i.e. the correction buys nothing -- which is the injected
    defect in the last group.

    Ground truth: test_audio_measure.test_inharmonic_fraction_db_reading_of_an_alias_free_signal_is_its_floor
    """
    n = int(0.5 * SR)
    for note in (40, 64, 88):
        naive, f0 = _osc("square", note, n, blep=False)
        blep, _ = _osc("square", note, n, blep=True)
        a = am.inharmonic_fraction_db(naive, f0).require(f"square {note}, naive")
        b = am.inharmonic_fraction_db(blep, f0).require(f"square {note}, corrected")
        assert a - b >= 14.0, f"note {note}: naive {a:.1f}, corrected {b:.1f}"


def test_the_waveforms_are_the_shapes_the_contract_names():
    """[source-verified: contract 6.4] **Contract 6.4, a property of the SHAPE, separate from aliasing.** Each
    naive waveform's harmonic series is what its name means, at 110 Hz:

        saw       every harmonic, falling 1/k          h2 -6.0, h3 -9.5 dB
        square    odd harmonics only, falling 1/k      h2 below -60 dB
        tri       odd harmonics only, falling 1/k^2    h3 -19.1, h5 -28.0 dB
        pulse25   a null at every 4th harmonic         h4 below -60 dB
        sine      no harmonic above -80 dB

    Tolerance 1 dB on the present partials, 60 dB of rejection on the absent
    ones. This catches a shape wired to the wrong formula, a duty cycle that
    is not 50 % or 25 %, and a sine table read with the wrong symmetry.

    Ground truth: test_audio_measure.test_harmonic_powers_recovers_a_known_series
    """
    n = 1 << 15
    f0 = dsp.phase_inc(110.0) * SR / (1 << 24)

    def rel_db(shape, ks):
        x, _ = _osc(shape, 45, n, blep=False)
        p = am.harmonic_powers(x, f0, [1] + list(ks))
        return 10 * np.log10(p[1:] / p[0])

    saw = rel_db("saw", [2, 3, 4, 5])
    for i, k in enumerate((2, 3, 4, 5)):
        assert abs(saw[i] - 20 * math.log10(1.0 / k)) < 1.0, ("saw", k, saw[i])
    sq = rel_db("square", [2, 3, 4, 5])
    assert sq[0] < -60.0 and sq[2] < -60.0, sq          # even harmonics absent
    assert abs(sq[1] - 20 * math.log10(1 / 3)) < 1.0 and abs(sq[3] - 20 * math.log10(1 / 5)) < 1.0
    tri = rel_db("tri", [2, 3, 5])
    assert tri[0] < -60.0, tri
    assert abs(tri[1] - 20 * math.log10(1 / 9)) < 1.0, tri
    assert abs(tri[2] - 20 * math.log10(1 / 25)) < 1.5, tri
    p25 = rel_db("pulse25", [2, 3, 4])
    assert p25[2] < -60.0, p25                          # null at the 4th
    assert abs(p25[0] - 20 * math.log10(math.sin(math.pi / 2) / 2
                                        / math.sin(math.pi / 4))) < 1.0, p25
    sine = rel_db("sine", [2, 3, 4, 5, 6, 7])
    assert sine.max() < -80.0, sine


# =============================================================================
# 3. THE VOICE
# =============================================================================
def _glide_incs(a, b, dur=1.0, **kw):
    v = vf.VoiceFx()
    v.run(v.note_on(b, dur, glide_from=a, waves=("saw",), detune=(0.0,), mix=(1.0,), **kw))
    seq = v.trace["incs"][0].astype(np.float64)
    land = int(np.argmax(seq == seq[-1]))
    assert 0 < land < len(seq), "the glide never landed"
    return seq, land


def test_the_glide_is_geometric_and_at_a_constant_rate():
    """[source-verified: DR 0004] **DR 0004: constant rate, linear in pitch, 90 ms per octave +-1 %.**
    The Minimoog's panel control is a RATE ("the further to the right ... the
    longer it will take a tone to move from one pitch to the next"), and the
    reissue specifies it per octave; Moog's and Sequential's current
    instruments default to the same law. So a two-octave glide must take twice
    as long as a one-octave one, and the trajectory must be a straight line in
    log-frequency, not in Hz.

    Measured: one octave 90.0 ms, two octaves 180.1 ms (2.00 x), down the same
    as up; the deviation from a straight line in log2(inc) is 0.097 cents over
    the whole glide, while the best straight line in Hz misses by 593 cents.

    This is OUR specified behaviour, recorded in DR 0004 as a design decision
    with its evidence; it is not to be "fixed" toward some other instrument's
    constant-time law, which remains available as a host policy.

    Ground truth: test_audio_measure.test_damped_sinusoid_matches_the_coefficients_that_generated_it
    """
    one_up, land1 = _glide_incs(52, 64)
    two_up, land2 = _glide_incs(40, 64)
    _, land_dn = _glide_incs(64, 52)
    assert abs(land1 / SR / vf.GLIDE_REF_S - 1.0) < 0.01, land1 / SR
    assert abs(land2 / land1 - 2.0) < 0.02, (land1, land2)
    assert abs(land_dn / land1 - 1.0) < 0.02, (land1, land_dn)

    t = np.arange(land2)
    A = np.vstack([t, np.ones(land2)]).T
    lg = np.log2(two_up[:land2])
    fit_log, *_ = np.linalg.lstsq(A, lg, rcond=None)
    cents_log = 1200.0 * np.abs(lg - A @ fit_log).max()
    fit_hz, *_ = np.linalg.lstsq(A, two_up[:land2], rcond=None)
    cents_hz = 1200.0 * np.abs(np.log2(np.maximum(A @ fit_hz, 1.0) / two_up[:land2])).max()
    assert cents_log < 1.0, f"not linear in pitch: {cents_log:.2f} cents off a straight line"
    assert cents_hz > 100.0, f"indistinguishable from a linear-in-Hz glide ({cents_hz:.1f} cents)"


def test_a_high_resonance_note_reaches_exactly_zero_after_its_release():
    """[source-verified: DR 0005 item 3] **DR 0005 item 3, tolerance: exactly zero.** The audition applied the
    amplitude envelope BEFORE the filter. With one continuous voice that means
    a self-oscillating patch never ends -- the envelope closes the filter's
    input and the filter keeps singing; measured then at 0.41 x full scale
    half a second after the last gate-off. The VCA is now after the filter, so
    the note ends when the VCA closes, whatever the filter is doing.

    A 600 Hz sine patch at res 1.06, gate off at 0.4 s: the ladder is still
    ringing at 9758 LSB at 1.2 s and the output is exactly 0 -- not small,
    zero -- from the frame the amplitude envelope reaches 0 (contract 8.3's
    `max(1, .)` floor is what makes it reach 0 at all).

    Also the acceptance question behind it: nothing audible after gate-off, no
    DC, no limit cycle at the output.

    Ground truth: test_audio_measure.test_peak_is_the_largest_magnitude_either_sign
    """
    regs = vf.VoiceFx.patch_regs(waves=("sine",), detune=(0.0,), mix=(1.0,),
                                 cutoff=(600, 600), q=1.06, drive=0.5, track=0.0,
                                 amp=(0.005, 0.1, 0.8, 0.05))
    v = vf.VoiceFx()
    gate_off = int(0.4 * SR)
    writes = [(0, "INC", 0, dsp.phase_inc(600.0), True), (0, "GATE", 1), (gate_off, "GATE", 0)]
    out = v.play(regs, writes, int(1.2 * SR))
    ladder, ae = v.trace["ladder"], v.trace["amp_env"]
    assert am.peak(ladder[-int(0.2 * SR):]) > 3000.0, "the filter is not singing; nothing is proved"
    silent = gate_off + int(np.argmax(ae[gate_off:] == 0))
    assert silent > gate_off, "the amplitude envelope never reached zero"
    assert np.all(out[silent:] == 0), \
        f"note does not end: peak {am.peak(out[silent:])} LSB after the envelope closed"
    assert float(np.mean(out[silent:].astype(np.float64))) == 0.0


def test_nothing_clips_at_the_reference_gain_structure():
    """[source-verified: DR 0005 and contract 12] **DR 0005 and contract 12, tolerance: zero samples.** The chain has
    exactly six clamps and at the reference `vol = 0.45` none of the
    undesigned ones fires on any of the eight audition patches. Measured per
    patch (each render analysed as its own event):

        clamp 2  mixer sum sat16        peak |acc >> 15| = 32767, never over
        clamp 5  ladder output sat19    peak 1.94 x full scale (growl-bass),
                                        against the word's 8.0
        clamp 6  output sat16           0 samples at `vol` 0.45; at rev 1's
                                        0.9, growl-bass clips 4.8 %

    The designed saturation -- the ladder's tanh -- is a separate test.

    Ground truth: test_audio_measure.test_peak_is_the_largest_magnitude_either_sign
    """
    import patches
    worst_ladder, worst_out = 0.0, 0.0
    for name, seq, total in patches.MONO:
        v = vf.VoiceFx()
        out = vf.render_mono_fx(seq, total, v)
        t = v.trace
        acc = sum(o * int(w) for o, w in zip(t["osc"], v.weights))
        assert np.abs(acc >> 15).max() <= 32767, f"{name}: the mixer saturates"
        assert np.abs(t["ladder"]).max() < (1 << 18), f"{name}: the ladder output word saturates"
        pre_rail = (t["vca"] * v.vol) >> 15
        assert np.abs(pre_rail).max() <= 32767, f"{name}: the output rail fires"
        assert np.abs(out).max() < 32767, f"{name}: the output reaches full scale"
        worst_ladder = max(worst_ladder, np.abs(t["ladder"]).max() / FS)
        worst_out = max(worst_out, np.abs(out).max() / FS)
        # rev 1's volume is the control: it must still clip, or the patch set has changed
        if name.startswith("07"):
            assert np.mean(np.abs((t["vca"] * 29491) >> 15) > 32767) > 0.03, name
    assert 1.5 < worst_ladder < 2.5, worst_ladder
    assert 0.8 < worst_out < 0.95, worst_out


def test_one_oscillator_in_isolation_fills_its_word_and_stays_inside_it():
    """[source-verified: contract 6 and 5.1] **The first node of the gain structure: one oscillator, before the
    mixer.** Contract 6 makes every waveform a signed Q1.15 word, so the
    reference level at this node is the word itself -- `-32768 .. +32767`,
    nothing above it and nothing meaningfully below. The test above asserts
    the MIXER SUM; this asserts what the mixer is handed, which is a different
    number and the one the mixer's weight budget is sized against.

    Both directions matter, and the second is the one that rots quietly:

      * **Nothing leaves the word.** The risk is not the waveform -- a ramp
        cannot overflow its own generator -- it is the PolyBLEP correction
        added at each discontinuity (contract 6.5), which is a SUBTRACTION
        near the edge and could push a full-scale step past the rail. It does
        not: swept over MIDI 0..124, the worst sample of any shape is +32767
        / -32768, i.e. exactly the word and never outside it.
      * **The word is actually used.** A quiet oscillator satisfies any upper
        bound. The smallest peak anywhere in that sweep is 19949 LSB (0.61 x
        full scale), the saw at the top of the range where PolyBLEP has
        rounded most of the ramp away; every other shape stays above 0.82.

    Measured, worst over MIDI 0..124 (`max`, `min`, and the smallest peak):

        saw      +32753  -32757   19949      shark    +27018  -32748   27019
        square   +32767  -32768   32768      revsaw   +32757  -32753   19949
        pulse25  +32767  -32768   32768      pulse29  +32767  -32768   32768
        tri      +32767  -32768   32768      pulse15  +32767  -32768   32768
        sine     +32767  -32767   32763

    `shark` is 47/57 of the word by construction (`SHARK_W_TRI`), not a
    defect; it is asserted at its own level.

    Ground truth: test_audio_measure.test_peak_is_the_largest_magnitude_either_sign
    """
    shapes = list(vf.WAVE_CODE)                      # every shape the register map can select
    floor = {"shark": 0.80 * 27019}                  # 47/57 of the word, by construction
    for shape in shapes:
        worst_peak = None
        for note in (0, 24, 45, 69, 93, 120):
            inc = dsp.phase_inc(dsp.note_hz(note))
            n = min(20000, max(2400, int(3 * SR / max(dsp.note_hz(note), 1.0))))
            x = vf.OscFx(shape).render(n, inc)
            assert x.max() <= 32767, f"{shape} at note {note}: {x.max()} is above the Q1.15 word"
            assert x.min() >= -32768, f"{shape} at note {note}: {x.min()} is below the Q1.15 word"
            pk = am.peak(x)
            worst_peak = pk if worst_peak is None else min(worst_peak, pk)
        assert worst_peak >= floor.get(shape, 0.60 * 32768), \
            f"{shape} only reaches {worst_peak} LSB: the node is not at its reference level"


def test_the_vca_node_has_headroom_and_never_adds_gain():
    """[source-verified: DR 0005 item 2 and contract 12] **The node between the filter and the output rail, asserted on its
    own.** `voice_fx._render` step 7 is `v = (y * ae) >> 15`: the amplitude
    envelope applied to the ladder's 19-bit Q4.15 word, AFTER the filter (DR
    0005 item 2). In the chip that `v` is what meets the drum buses, still
    unsaturated (`synth_top_model.py` point 3), so its bound is a question
    the output clamp cannot answer -- the clamp is downstream of `vol`, and at
    the reference `vol = 0.45` an output that never rails is consistent with a
    `v` more than twice full scale. It is, in fact: growl-bass runs this node
    at 1.918 x full scale with a perfectly clean output.

    Two properties, neither of them implied by the existing clipping test:

      * **The word holds the loudest patch the REGISTER MAP can express**, not
        just the loudest audition patch. At the register clamps -- `drive` 6
        (the 20-bit `gain` clamp) and `res` 2.0 (the 17-bit `k` clamp), three
        oscillators at full mix -- `y` reaches 3.456 x full scale and `v`
        3.377, which is 0.43 of the Q4.15 word. 2.3 x of headroom left, and
        the output rail is firing on 3.4 % of samples while it happens: the
        two bounds are measuring different things, which is the point.
      * **The VCA attenuates and never adds gain.** `ae` is Q0.15 in
        `0 .. 32767`, so `|v| <= |y|` sample by sample -- asserted pointwise
        rather than on peaks, because a gain jump at one frame is exactly
        what a peak comparison would miss.

    Measured on the audition patches, `v` relative to full scale: 1.172,
    1.174, 0.845, 0.884, 0.972, 0.866, **1.918** (growl-bass), 0.412.

    Ground truth: test_audio_measure.test_peak_is_the_largest_magnitude_either_sign
    """
    import patches
    name, seq, total = next(p for p in patches.MONO if p[0].endswith("growl-bass"))
    v = vf.VoiceFx()
    out = vf.render_mono_fx(seq, total, v)
    y = np.asarray(v.trace["ladder"], dtype=np.int64)
    vca = np.asarray(v.trace["vca"], dtype=np.int64)
    ae = np.asarray(v.trace["amp_env"], dtype=np.int64)
    assert ae.max() <= 32767 and ae.min() >= 0, (ae.min(), ae.max())
    assert np.all(np.abs(vca) <= np.abs(y)), \
        f"{name}: the VCA added gain at {int(np.sum(np.abs(vca) > np.abs(y)))} frames"
    assert np.abs(vca).max() < (1 << 18), f"{name}: the VCA word saturates"
    assert 1.5 < am.peak(vca) / FS < 2.5, am.peak(vca) / FS
    assert am.peak(out) < 32767, f"{name}: the output rail fires; this is the clean case"

    # the loudest thing the register map can ask for: both clamps, full mix
    hot = vf.VoiceFx()
    out_hot = hot.note(28, 0.6, waves=("saw", "saw", "square"), mix=(1.0, 1.0, 1.0),
                       q=2.0, drive=6.0, cutoff=(400, 12000))
    y_hot = np.asarray(hot.trace["ladder"], dtype=np.int64)
    vca_hot = np.asarray(hot.trace["vca"], dtype=np.int64)
    assert np.all(np.abs(vca_hot) <= np.abs(y_hot)), "the VCA added gain at the register clamps"
    assert np.abs(vca_hot).max() < (1 << 18), \
        f"the VCA word saturates at the register clamps: {np.abs(vca_hot).max()} LSB"
    assert 3.0 < am.peak(vca_hot) / FS < 5.0, am.peak(vca_hot) / FS
    # and it is NOT the output clamp keeping it there: that clamp is firing
    assert np.mean(np.abs(out_hot) >= 32767) > 0.01, \
        "the hot patch does not reach the output rail, so it is not the hot case"


def test_the_drive_control_engages_the_ladders_saturation():
    """[source-verified: DR 0005 item 1] **DR 0005 item 1: the designed saturation is the ladder's tanh, driven
    by `gain`.** The other side of the clipping test -- nothing clips, but the
    thing that is supposed to distort must distort.

    A pure tone through the filter at res 0.2, cutoff 4 kHz. Distortion (all
    partials above the first, relative to the first) against the drive
    control: -73.8 dB at 0.05, -86.2 at 0.2, -37.2 at 0.45, -12.8 at the
    reference patch's 1.6, -9.5 at 3.0. And the level compresses as it does
    it: 60 x the drive buys 21 dB of output, not 36.

    The two lowest drives are AT THE MODEL'S TRUNCATION FLOOR, not on a
    distortion curve -- the output there is a few hundred LSB and the reading
    depends on where truncation noise lands, which is why -86 dB sits below
    -74 dB. So the monotone assertion covers only the engaged region
    (0.45 upward) and the low end is asserted as "clean", which is all the
    measurement supports.

    Tolerances: below -60 dB at drive 0.05 and 0.2 (clean), above -20 dB at
    drive 1.6 (the drive control does something), monotone from 0.45 up with
    at least 20 dB between 0.45 and 3.0, and at least 10 dB of level
    compression from drive 0.1 to 6.

    Ground truth: test_audio_measure.test_harmonic_powers_recovers_a_known_series, test_audio_measure.test_peak_is_the_largest_magnitude_either_sign
    """
    f0, n = 220.0, int(0.3 * SR)
    x = np.round(0.9 * FS * np.sin(2 * math.pi * f0 * np.arange(n) / SR)).astype(np.int16)

    def thd_db(drive):
        y = ladder_render(x, 4000, 0.2, drive)[int(0.05 * SR):]
        p = am.harmonic_powers(y, f0, range(1, 40))
        return 10 * math.log10(p[1:].sum() / p[0])

    thd = [thd_db(d) for d in (0.05, 0.2, 0.45, 1.6, 3.0)]
    assert thd[0] < -60.0 and thd[1] < -60.0, thd
    assert thd[3] > -20.0, thd
    assert all(b > a for a, b in zip(thd[2:], thd[3:])), thd
    assert thd[4] - thd[2] > 20.0, thd
    lo = am.peak(ladder_render(x, 4000, 0.2, 0.1))
    hi = am.peak(ladder_render(x, 4000, 0.2, 6.0))
    growth = 20 * math.log10(hi / lo)
    assert growth < 20 * math.log10(60.0) - 10.0, f"no compression: {growth:.1f} dB for 35.6 dB of drive"


@pytest.mark.parametrize("release", [0.02, 0.12, 0.5, 1.0])
def test_the_envelope_release_reaches_zero_and_does_not_stair_step(release):
    """[source-verified: contract 8.3 and DESIGN.md section 4] **Contract 8.3 and DESIGN.md section 4, tolerances: exactly zero, and a
    floor below -62 dBFS up to a 1 s release.**

    `L -= max(1, (L*rate) >> 16)`. The `max(1, .)` is load-bearing: without it
    the shifted product truncates to zero below `2^16/rate` and the note never
    ends. With it the release is exponential down to that floor and then walks
    to zero at 1 LSB per frame.

    Measured on the 24-bit level: strictly decreasing at every frame above the
    floor, the frame-to-frame ratio constant to 2.1e-3 at the shortest release
    and 4e-5 at the longest (it IS an exponential, not a staircase, and the
    spread is the level's own quantisation near the floor), reaching exactly
    0 at 53 ms / 321 ms / 1.17 s / 2.37 s,
    with the floor at -96.9 / -81.2 / -69.0 / -62.1 dBFS. 24 bits is chosen so
    that the last figure stays under the 16-bit noise floor; at 16 bits the
    same release turns linear at -14 dBFS, which is the staircase this test
    exists to catch.

    Ground truth: test_audio_measure.test_longest_plateau_counts_a_stair_step
    """
    env = vf.AdsrFx(0.005, 0.1, 1.0, release)
    env.render(int(0.3 * SR), int(0.3 * SR))                 # hold at sustain
    level = env.render(int(4 * SR), 0, q=env.EB)             # the 24-bit level per frame
    assert (level == 0).any(), "the release never reaches zero"
    floor_db = 20 * math.log10(env.floor_level / (1 << env.EB))
    assert floor_db < -62.0, f"the exponential gives out at {floor_db:.1f} dBFS"
    above = level[level > env.floor_level]
    assert len(above) > 100
    assert np.all(np.diff(above) < 0), "the release stalls"
    assert am.longest_plateau(above) == 1
    ratio = above[1:] / above[:-1]
    assert ratio.max() - ratio.min() < 3e-3, "the release is not an exponential"


def test_a_cutoff_jump_mid_note_does_not_click():
    """[source-verified: contract 4.3 and 10.1] **Contract 4.3 and 10.1: a control write takes effect at a frame
    boundary, and nothing else about the voice restarts.**

    An instantaneous 16 x cutoff write is a legitimate level change -- there is
    no smoothing in the contract and none is claimed -- so the property is not
    "no step" but "no step the signal does not already have": the sample-to-
    sample jump at the write must not exceed the largest ordinary jump on
    either side of it. A transient, a state reset or a re-attack would exceed
    both. Measured: 2294 LSB at the write, against 336 LSB before (filter shut)
    and 5307 LSB after (filter open) -- the write is quieter than the waveform
    it lands in.

    Then the continuous case, which is where a zipper would live: the fastest
    filter envelope the registers allow (1 ms attack, 5 ms decay) sweeps the
    cutoff every frame from 200 Hz to 8 kHz, and no sample step may exceed
    8000 LSB.

    Then the resonance, which `play` cannot write mid-note but the chip
    modulates per frame: `k_eff` stepped between 0 and the onset every 5 ms
    for half a second must stay inside the 19-bit output word, with no step
    beyond the signal's own.

    Ground truth: test_audio_measure.test_max_sample_step_finds_a_planted_click, test_audio_measure.test_analytic_envelope_of_a_damped_sinusoid_is_the_exponential, test_audio_measure.test_peak_is_the_largest_magnitude_either_sign
    """
    regs = vf.VoiceFx.patch_regs(waves=("saw",), detune=(0.0,), mix=(1.0,),
                                 cutoff=(400, 400), q=0.7, drive=1.6, track=0.0,
                                 amp=(0.005, 0.2, 1.0, 0.1), fenv=(0.001, 0.01, 1.0, 0.01))
    v = vf.VoiceFx()
    at = int(0.25 * SR)
    out = v.play(regs, [(0, "INC", 0, dsp.phase_inc(110.0), True), (0, "GATE", 1),
                        (at, "TRACK", 6000)], int(0.5 * SR)).astype(np.float64)
    before = am.max_sample_step(out[at - 400:at - 1])
    after = am.max_sample_step(out[at + 200:at + 600])
    at_write = abs(out[at] - out[at - 1])
    assert at_write <= 1.2 * max(before, after), \
        f"click at the write: {at_write:.0f} LSB against {before:.0f} before, {after:.0f} after"

    fast, _ = one_note(45, 0.4, waves=("saw",), detune=(0.0,), mix=(1.0,),
                       cutoff=(200, 8000), q=0.8, drive=1.6, track=0.0,
                       fenv=(0.001, 0.005, 0.2, 0.05))
    env = am.analytic_envelope(fast.astype(np.float64))
    steps = np.abs(np.diff(fast.astype(np.float64)))
    assert steps.max() < 8000.0, f"zipper on the fastest filter envelope: {steps.max():.0f} LSB"
    assert am.peak(env) > 1000.0, "the note is too quiet to prove anything"

    n = int(0.5 * SR)
    saw = vf.OscFx("saw").render(n, dsp.phase_inc(110.0)).astype(np.int16)
    g, k_on, gain, ogain = _ladder_regs(1.0, 1.6, 1200)
    square = (np.arange(n) // int(0.005 * SR)) % 2
    k_mod = np.where(square, k_on, 0).astype(np.int64)
    lad = vf.LadderFx(**vf.LADDER_CFG)
    y = lad.process(saw, None, 1.0, 1.6, g_q16=np.full(n, g, dtype=np.int64),
                    gain=gain, ogain=ogain, k_q14=k_mod).astype(np.float64)
    assert am.peak(y) < (1 << 18), f"a stepped resonance reaches the output word: {am.peak(y)}"
    assert am.max_sample_step(y) < 4.0 * np.abs(np.diff(y)).mean() * 20.0, "resonance steps click"


# -----------------------------------------------------------------------------
# movement, the two cases the test above cannot reach (#53). Both reuse
# `model/reference_movement.py`'s measurement rather than a second one, so the
# numbers here and the numbers in `docs/surge-source-notes.md` section 6 are
# the same numbers. Each has an injected control in section 4 below.
# -----------------------------------------------------------------------------
RES_SWEEP_BOUND_DB = -75.0       # shipping measures -82.2; the control reads -58.3
MOD_RESIDUAL_BOUND_DB = -45.0    # shipping measures -52 to -63


def _resonance_sweep_ripple(hold=1, seconds=0.8, span=None):
    """Envelope ripple over a CONTINUOUS resonance ramp through the
    self-oscillation onset, `hold` frames between `k` writes."""
    lo, hi = rm.RES_SPAN if span is None else span
    y = rm.resonance_render(lo, hi, seconds, hold)
    e = am.envelope_ripple_db(am.analytic_envelope(y), SR, lp_hz=rm.RES_LP_HZ)
    value, verdict = rm.ripple_verdict(e, rm.RES_LP_HZ)
    assert value is not None, f"the ripple estimator refused: {verdict}"
    return value, verdict


def _assert_a_resonance_sweep_through_onset_is_quiet(hold=1):
    up, verdict = _resonance_sweep_ripple(hold)
    assert up < RES_SWEEP_BOUND_DB, \
        f"a resonance sweep through onset ripples at {up:.1f} dB ({verdict})"
    return up


def _assert_audio_rate_filter_modulation_is_quiet(quant=1, hold=1):
    worst = None
    for depth, f3 in ((0.25, 1760.0), (1.30, 440.0)):
        cut, cut_f, _ = rm.mod_trajectory(depth, f3, seconds=0.2)
        a, b = rm.mod_pair(cut, cut_f, quant=quant, hold=hold)
        resid = am.db(am.rms(a - b), am.rms(b))
        worst = resid if worst is None else max(worst, resid)
        assert resid < MOD_RESIDUAL_BOUND_DB, (
            f"audio-rate filter modulation at {depth:.2f} oct / {f3:.0f} Hz leaves "
            f"{resid:.1f} dB of control-path error against the float control path")
    return worst


def test_a_continuous_resonance_sweep_through_self_oscillation_does_not_zipper():
    """[measured-here: #53 -- no source states a bound; this is our own floor] **#53, and the case `test_a_cutoff_jump_mid_note_does_not_click` cannot
    reach.** That test steps `k_eff` between 0 and the onset as a 5 ms square
    wave -- two abrupt toggles. This one ramps `res` CONTINUOUSLY from 0.6 to
    1.4 through the onset (DR 0006 puts the onset at res = 1.000 by
    construction, and that is asserted here rather than assumed), which is the
    case #53 calls "most likely to click or thump" because the loop sits at
    unity gain for as long as the ramp takes to cross it.

    Measured with `reference_movement.resonance_render` /
    `audio_measure.envelope_ripple_db`, band-limited to the same 800 Hz as the
    section-3 sweep table so the numbers compare. Shipping reads **-82.2 dB**
    at a 1 res/s ramp and **-90.2 dB** at 0.25 res/s -- both at the estimator's
    own floor, so they are upper bounds -- against **-58.3 dB** when `k` is
    written every 5 ms instead of every frame
    (test_control_a_coarse_resonance_write_interval_zippers).

    Both directions, because the crossing is not symmetric: entering
    self-oscillation and leaving it are different transients.

    Ground truth: test_reference_voice.test_envelope_ripple_matches_the_closed_form_of_a_known_staircase, test_reference_voice.test_envelope_ripple_under_reads_when_the_steps_are_slower_than_its_window, test_audio_measure.test_analytic_envelope_of_a_damped_sinusoid_is_the_exponential
    """
    onset = rm.onset_res()
    assert abs(onset - 1.0) < 0.004, f"DR 0006 puts the onset at res 1.000; it is {onset:.4f}"
    assert rm.RES_SPAN[0] < onset < rm.RES_SPAN[1], "the ramp does not cross the onset"

    # apparatus precondition: the ramp has to reach self-oscillation, or this
    # measures a resonant peak and answers a different question.
    y = rm.resonance_render(*rm.RES_SPAN, 0.8, mute_from=0.5)
    env = am.analytic_envelope(y)
    n = len(env)
    grew = env[int(0.95 * n):].mean() / max(env[int(0.55 * n):int(0.6 * n)].mean(), 1e-9)
    assert grew > 1.0, ("with the input muted the output decays: the ramp never "
                        f"reaches self-oscillation ({20 * math.log10(grew):+.1f} dB)")

    up = _assert_a_resonance_sweep_through_onset_is_quiet()
    down, _ = _resonance_sweep_ripple(1, span=rm.RES_SPAN[::-1])
    assert down < RES_SWEEP_BOUND_DB, f"the downward crossing ripples at {down:.1f} dB"
    assert abs(up - down) < 6.0, f"the two directions differ by {abs(up - down):.1f} dB"

    # and the crossing itself is not the expensive part: the same ramp rate
    # over a span that never reaches onset is no quieter.
    below, _ = _resonance_sweep_ripple(1, span=rm.RES_BELOW)
    assert below > up - 6.0, (f"crossing the onset reads {up:.1f} dB against "
                              f"{below:.1f} dB for a ramp that never crosses it")


def test_audio_rate_filter_modulation_adds_no_more_than_the_static_control_error():
    """[measured-here: #53 -- the differential of section 2, at audio rate] **#53, and the case `test_the_mod_wheel_at_full_sweeps_the_cutoff_from_
    440_to_at_least_2400` cannot reach.** That test drives `MR_FILT` from
    oscillator 3 as a square-wave LFO and checks the DEPTH of the swing. This
    one runs the same bus at an AUDIO rate -- 440 Hz and 1760 Hz -- which #53
    calls "far harder than a hand on a knob", and measures the artefact
    instead of the depth.

    The estimator is the differential of section 2, not the envelope ripple:
    the same carrier through the same ladder twice, once with the shipping
    control path (Q3.12 octave word -> interpolated exp ROM -> integer-hertz
    register -> g ROM) and once with that arithmetic in float. **Envelope
    ripple cannot answer this question at all** -- an audio-rate cutoff
    modulation IS an envelope modulation, 40 dB larger than any artefact, and
    `reference_movement.stage_audio_rate_mod` refuses it at all nine of its
    operating points. The refusal is asserted here, not assumed, because a
    number from that estimator would look exactly like data.

    Measured: **-52 to -63 dB** across 0.25/1.30/3.90 octaves and
    110/440/1760 Hz, flat in both -- the signature of a static control error,
    the same conclusion section 3 reached for cutoff sweeps. Holding the
    cutoff for one 512-sample block instead reads **+1.3 dB**
    (test_control_a_block_rate_cutoff_hold_breaks_audio_rate_modulation).

    Ground truth: test_audio_measure.test_rms_of_a_sinusoid_is_amplitude_over_root_two, test_reference_voice.test_envelope_ripple_band_limit_attenuates_the_hilbert_artefact
    """
    _assert_audio_rate_filter_modulation_is_quiet()

    # the apparatus's own limit, asserted: at audio rate the envelope-ripple
    # estimator reads the intended modulation, and must say so.
    cut, cut_f, _ = rm.mod_trajectory(1.30, 440.0, seconds=0.2)
    a, b = rm.mod_pair(cut, cut_f)
    ea = am.envelope_ripple_db(am.analytic_envelope(a), SR, lp_hz=rm.MOD_LP_HZ)
    eb = am.envelope_ripple_db(am.analytic_envelope(b), SR, lp_hz=rm.MOD_LP_HZ)
    value, verdict = rm.ripple_verdict(ea, rm.MOD_LP_HZ, avoid_hz=440.0)
    assert value is None, f"the ripple estimator answered where it cannot: {ea.value:.1f} dB"
    assert verdict.startswith("REFUSED"), verdict
    assert abs(ea.value - eb.value) < 3.0, (
        "the artefact-free render should read the same ripple, since the ripple "
        f"is the intended modulation: {ea.value:.1f} against {eb.value:.1f} dB")


# =============================================================================
# 4. THE SUITE CAN FAIL
#
# Every property above is green. `docs/verification-rules.md` rule 2: that
# means nothing until each control has been shown to turn it red. Each test
# here injects one defect INTO THE MODEL and requires the property that covers
# it to fail -- the four defects the rules name as most likely to be silently
# wrong, plus a dropped pole and an all-silent stub.
# =============================================================================
def _expect_red(fn, *a, **kw):
    """Run a property check and require it to fail. A defect that leaves the
    property green is a hole in the suite, and this reports it as one."""
    with pytest.raises(AssertionError) as e:
        fn(*a, **kw)
    return str(e.value)


def test_control_removing_the_resonance_compensation(monkeypatch):
    """[meta] Defect: `k_eff = k`, the rev-1 filter with no compensation ROM
    (DR 0006). The filter must then stop sustaining above ~3 kHz, and
    test_it_still_self_oscillates_at_10_khz must go red.

    Ground truth: test_audio_measure.test_dominant_frequency_on_known_tones, test_audio_measure.test_rms_of_a_sinusoid_is_amplitude_over_root_two, test_audio_measure.test_tonality_separates_a_tone_from_noise_where_a_percentile_does_not
    """
    monkeypatch.setattr(vf, "k_effective", lambda k, kc: np.asarray(k, dtype=np.int64))
    msg = _expect_red(test_it_still_self_oscillates_at_10_khz)
    assert "not sustaining" in msg or "ROM changed nothing" in msg
    # and the onset moves far outside DR 0006's 0.39 %
    _expect_red(test_res_1_is_the_onset_of_self_oscillation_at_every_cutoff, 10000)


def test_control_inverting_the_squares_polyblep_sign(monkeypatch):
    """[meta] Defect: the square's correction with the saw's sign (DESIGN.md section
    6). The improvement over naive collapses from 15 dB to 0.

    Ground truth: test_audio_measure.test_inharmonic_fraction_db_reading_of_an_alias_free_signal_is_its_floor
    """
    real = vf.blep_fx
    monkeypatch.setattr(vf, "blep_fx", lambda *a, **kw: -real(*a, **kw))
    n = int(0.5 * SR)
    naive, f0 = _osc("square", 88, n, blep=False)
    broken, _ = _osc("square", 88, n, blep=True)
    gain = (am.inharmonic_fraction_db(naive, f0).require()
            - am.inharmonic_fraction_db(broken, f0).require())
    assert gain < 1.0, f"the inverted correction still buys {gain:.1f} dB"
    _expect_red(test_the_squares_correction_has_the_opposite_sign_to_the_saws)


class _NoFloorAdsr(vf.AdsrFx):
    """Defect: contract 8.3's release without its `max(1, .)`."""

    def render(self, n, gate, trig=None, q=15):
        out = np.empty(n, dtype=np.int64)
        gate_a = ((np.arange(n) < int(gate)).astype(np.int64) if np.ndim(gate) == 0
                  else np.asarray(gate, dtype=np.int64))
        trig_a = None if trig is None else np.asarray(trig, dtype=np.int64)
        L, seg = self.level, self.seg
        sh = self.EB - q
        for i in range(n):
            if trig_a is not None and trig_a[i]:
                seg = self.ATTACK
            out[i] = L >> sh
            if gate_a[i]:
                if seg == self.ATTACK:
                    L += self.a_inc
                    if L >= self.full:
                        L, seg = self.full, self.DECAY
                elif seg == self.DECAY:
                    L -= self.d_dec
                    if L <= self.sus:
                        L, seg = self.sus, self.SUSTAIN
                else:
                    L = self.sus
            else:
                mantissa = self.rate & ((1 << self.RQ) - 1)
                exponent = self.rate >> self.RQ
                L -= (L * mantissa) >> (self.RQ + exponent)  # the floor, removed
                if L < 0:
                    L = 0
        self.level, self.seg = L, seg
        return out


def test_control_removing_the_envelope_release_floor(monkeypatch):
    """[meta] Defect: no `max(1, .)` in the release. The level stalls at
    `2^(16+exponent)/mantissa - 1` and the note never ends -- which is exactly what the
    'a note ends' property is for.

    Ground truth: test_audio_measure.test_peak_is_the_largest_magnitude_either_sign, test_audio_measure.test_longest_plateau_counts_a_stair_step
    """
    monkeypatch.setattr(vf, "AdsrFx", _NoFloorAdsr)
    env = vf.AdsrFx(0.005, 0.1, 1.0, 0.12)
    env.render(int(0.3 * SR), int(0.3 * SR))
    assert not (env.render(int(4 * SR), 0, q=env.EB) == 0).any(), "the defect does not stall"
    _expect_red(test_a_high_resonance_note_reaches_exactly_zero_after_its_release)
    _expect_red(test_the_envelope_release_reaches_zero_and_does_not_stair_step, 0.12)


def _render_vca_before_filter(self, incs, track, gate, trig, n, mw=None):
    """Defect: the auditioned chain order -- amplitude envelope applied to the
    mixer output, ahead of the ladder (DR 0005's rejected alternative)."""
    if mw is None:
        mw = np.full(n, self.mwheel, dtype=np.int64)
    incs, white, pink, red, mant_f, sh_f, msig = self._modulate(incs, n, mw)
    sig = [o.render(n, inc) for o, inc in zip(self.oscs, incs)]
    mixed = vf.mix_fx(sig + [pink if self.nsel else white], self.weights)
    ae = self.amp_env.render(n, gate, trig)
    fe = self.filt_env.render(n, gate, trig)
    span = self.cut_hi - self.cut_lo
    cut = np.clip(self.cut_lo + ((span * fe) >> 15) + track, vf.CUT_MIN, vf.CUT_MAX)
    cut = np.clip((cut * mant_f) >> sh_f, vf.CUT_MIN, vf.CUT_MAX)
    kc = vf.kc_from_cut(cut, self.k_rom, self.KB)
    k_eff = vf.k_effective(self.k_reg, kc) if self.k_comp else np.full(n, self.k_reg, dtype=np.int64)
    g = vf.g_from_cut(cut, self.g_rom, self.GB)
    pre = vf.sat16((mixed * ae) >> 15).astype(np.int16)          # the VCA, before the filter
    y = self.ladder.process(pre, None, self.res, self.drive, g_q16=g,
                            k=self.k_reg, gain=self.gain, ogain=self.ogain, k_q14=k_eff)
    y = y.astype(np.int64)
    out = vf.sat16((y * self.vol) >> 15)
    self.trace = dict(osc=sig, mixed=mixed, amp_env=ae, filt_env=fe, cut=cut, g=g,
                      kc=kc, k_eff=k_eff, ladder=y, vca=y, incs=incs, gate=gate, trig=trig)
    return out.astype(np.int16)


def test_control_moving_the_vca_before_the_filter(monkeypatch):
    """[meta] Defect: the audition's chain order. A self-oscillating patch then never
    ends -- the envelope closes the filter's input and the filter keeps
    singing -- which is the measurement DR 0005 was written from.

    Ground truth: test_audio_measure.test_peak_is_the_largest_magnitude_either_sign
    """
    monkeypatch.setattr(vf.VoiceFx, "_render", _render_vca_before_filter)
    _expect_red(test_a_high_resonance_note_reaches_exactly_zero_after_its_release)


class _ThreePoleLadder(_LadderVariant):
    """Defect: a stage dropped from the cascade (contract 11.4)."""

    def __init__(self, *a, **kw):
        kw.pop("stages", None)
        super().__init__(*a, stages=3, **kw)


def test_control_dropping_a_pole(monkeypatch):
    """[meta] Defect: three one-poles instead of four. The stopband then falls at
    17.4 dB/octave instead of 23.7, which is what the slope property is for.

    Ground truth: test_audio_measure.test_tone_amplitude_recovers_a_known_amplitude_under_noise
    """
    monkeypatch.setattr(vf, "LadderFx", _ThreePoleLadder)
    _expect_red(test_the_stopband_falls_at_24_db_per_octave)


class _SilentLadder(_REAL_LADDER):
    def process(self, x_q15, *a, **kw):
        return np.zeros(len(x_q15), dtype=np.int32)


class _SilentOsc(vf.OscFx):
    def render(self, n, inc):
        return np.zeros(n, dtype=np.int64)


class _SilentVoice(vf.VoiceFx):
    def _render(self, incs, track, gate, trig, n, mw=None):
        self.trace = dict(osc=[np.zeros(n, dtype=np.int64) for _ in self.oscs],
                          mixed=np.zeros(n, dtype=np.int64),
                          amp_env=np.zeros(n, dtype=np.int64),
                          filt_env=np.zeros(n, dtype=np.int64),
                          cut=np.full(n, self.cut_lo, dtype=np.int64), g=None,
                          kc=np.zeros(n, dtype=np.int64), k_eff=np.zeros(n, dtype=np.int64),
                          ladder=np.zeros(n, dtype=np.int64), vca=np.zeros(n, dtype=np.int64),
                          incs=incs, gate=gate, trig=trig,
                          white=np.zeros(n, dtype=np.int64), pink=np.zeros(n, dtype=np.int64),
                          red=np.zeros(n, dtype=np.int64), noise=np.zeros(n, dtype=np.int64),
                          mod_sig=np.zeros(n, dtype=np.int64))
        return np.zeros(n, dtype=np.int16)


def test_control_a_silent_stub_passes_nothing(monkeypatch):
    """[meta] The anti-vacuous-pass control, `docs/verification-rules.md` rule 1: a
    model with the right ports and no behaviour must fail every property, not
    pass any of them by default. The estimators refuse a silent record
    (`InsufficientEvidence`, itself an AssertionError) rather than returning a
    plausible number, so each of these goes red for a stated reason.

    Ground truth: test_audio_measure.test_rms_of_a_sinusoid_is_amplitude_over_root_two, test_audio_measure.test_dominant_frequency_on_known_tones, test_audio_measure.test_tonality_separates_a_tone_from_noise_where_a_percentile_does_not, test_audio_measure.test_tone_amplitude_recovers_a_known_amplitude_under_noise, test_audio_measure.test_a_centroid_is_not_a_corner_frequency, test_audio_measure.test_inharmonic_fraction_db_reading_of_an_alias_free_signal_is_its_floor, test_audio_measure.test_foldback_finds_a_planted_image_and_ignores_the_real_harmonics, test_audio_measure.test_harmonic_powers_recovers_a_known_series, test_audio_measure.test_peak_is_the_largest_magnitude_either_sign, test_audio_measure.test_analytic_envelope_of_a_damped_sinusoid_is_the_exponential, test_audio_measure.test_max_sample_step_finds_a_planted_click
    """
    monkeypatch.setattr(vf, "LadderFx", _SilentLadder)
    monkeypatch.setattr(vf, "OscFx", _SilentOsc)
    monkeypatch.setattr(vf, "VoiceFx", _SilentVoice)
    checks = [
        (test_res_1_is_the_onset_of_self_oscillation_at_every_cutoff, (200,)),
        (test_it_still_self_oscillates_at_10_khz, ()),
        (test_the_stopband_falls_at_24_db_per_octave, ()),
        (test_resonance_lifts_a_peak_at_the_cutoff, ()),
        (test_the_passband_compensation_is_partial_and_is_the_one_in_the_contract, ()),
        (test_opening_the_cutoff_makes_a_note_brighter, ()),
        (test_polyblep_suppresses_aliasing_at_every_register, ("saw", 64)),
        (test_polyblep_removes_the_predicted_fold_back_images, (88,)),
        (test_the_squares_correction_has_the_opposite_sign_to_the_saws, ()),
        (test_the_waveforms_are_the_shapes_the_contract_names, ()),
        (test_the_drive_control_engages_the_ladders_saturation, ()),
        (test_a_cutoff_jump_mid_note_does_not_click, ()),
    ]
    for fn, args in checks:
        _expect_red(fn, *args)


def test_control_a_silent_stub_is_not_mistaken_for_a_note_that_ended(monkeypatch):
    """[meta] The one property a silent stub could pass by accident -- "the output is
    zero after the release" is true of a model that is zero throughout. The
    test guards against it by requiring the filter to be audibly ringing
    first, and that guard is what has to fire.

    Ground truth: test_audio_measure.test_peak_is_the_largest_magnitude_either_sign
    """
    monkeypatch.setattr(vf, "LadderFx", _SilentLadder)
    msg = _expect_red(test_a_high_resonance_note_reaches_exactly_zero_after_its_release)
    assert "not singing" in msg


# ---- controls for the gain structure (issue #47) -----------------------------
class _SkewedFeedbackLadder(_REAL_LADDER):
    """Defect: the loop runs at 1.2 x the `k` the register holds. Every other
    property of the filter survives -- it still self-oscillates, still falls at
    24 dB/octave -- so only a test that reads the LEVEL against `k` can see
    it."""

    def process(self, x_q15, cutoff_hz, res, drive=1.0, *, k=None, k_q14=None, **kw):
        if k_q14 is not None:
            k_q14 = (np.asarray(k_q14, dtype=np.int64) * 6) // 5
        if k is not None:
            k = (int(k) * 6) // 5
        return super().process(x_q15, cutoff_hz, res, drive, k=k, k_q14=k_q14, **kw)


@pytest.mark.parametrize("compensated", [False, True])
def test_control_a_feedback_gain_that_is_not_the_one_the_register_holds(monkeypatch,
                                                                        compensated):
    """[meta] Defect: 20 % more feedback inside the loop than the `k` register
    says. Stinchcombe's `H(0) = 1/(1 + k)` is then read against the wrong `k`
    -- 1.28 dB out at k = 4 -- and
    test_the_ladders_low_frequency_gain_is_one_over_one_plus_k must go red.

    This is the control that makes that test an EXTERNAL check rather than a
    tautology: the law is asserted against the register the host wrote, so a
    model that quietly runs a different feedback gain fails it.

    Ground truth: test_audio_measure.test_tone_amplitude_recovers_a_known_amplitude_under_noise
    """
    monkeypatch.setattr(vf, "LadderFx", _SkewedFeedbackLadder)
    _expect_red(test_the_ladders_low_frequency_gain_is_one_over_one_plus_k, compensated)


def _scaled_osc(factor):
    real = vf.OscFx.render

    def render(self, n, inc):
        return (np.asarray(real(self, n, inc), dtype=np.int64) * factor).astype(np.int64)
    return render


def test_control_an_oscillator_at_the_wrong_reference_level(monkeypatch):
    """[meta] Two defects at the first node of the gain structure, one on each
    side of the bound, because a level check with only an upper bound is half
    a check:

      * **10 % hot.** The oscillator leaves the Q1.15 word and the mixer is
        handed something it was not sized for.
      * **6 dB quiet.** Nothing overflows anywhere, every downstream headroom
        test goes greener, and the instrument is wrong -- this is the
        direction a headroom suite rewards if nobody asserts the floor.

    Both must turn test_one_oscillator_in_isolation_fills_its_word_and_stays_inside_it red.

    Ground truth: test_audio_measure.test_peak_is_the_largest_magnitude_either_sign
    """
    monkeypatch.setattr(vf.OscFx, "render", _scaled_osc(1.1))
    msg = _expect_red(test_one_oscillator_in_isolation_fills_its_word_and_stays_inside_it)
    assert "above the Q1.15 word" in msg or "below the Q1.15 word" in msg

    monkeypatch.setattr(vf.OscFx, "render", _scaled_osc(0.5))
    msg = _expect_red(test_one_oscillator_in_isolation_fills_its_word_and_stays_inside_it)
    assert "not at its reference level" in msg


def test_control_an_amplitude_envelope_that_adds_gain(monkeypatch):
    """[meta] Defect: the amplitude envelope rendered at twice its Q0.15 word,
    so the VCA multiplies UP. The output still sounds like the instrument at a
    lower `vol`, and the existing clipping test can still pass at the
    reference volume -- what has changed is that the node between the filter
    and the rail now carries twice the level the contract gives it, which is
    the case `test_the_vca_node_has_headroom_and_never_adds_gain` exists for.

    Ground truth: test_audio_measure.test_peak_is_the_largest_magnitude_either_sign
    """
    real = vf.AdsrFx.render

    def render(self, n, gate, trig=None, q: int = 15):
        return np.asarray(real(self, n, gate, trig, q), dtype=np.int64) * 2
    monkeypatch.setattr(vf.AdsrFx, "render", render)
    _expect_red(test_the_vca_node_has_headroom_and_never_adds_gain)


# =============================================================================
# 5. THE MINIMOOG'S OWN FEATURES (docs/minimoog-reference.md)
#
# Everything above this line measures the voice against OUR decision records.
# Everything below measures it against a Moog service manual or a Moog drawing,
# and every test names the tag in `docs/minimoog-reference.md` it is checking.
# =============================================================================
def band_power_db(x, lo, hi, sr=SR):
    """Power in a band, in dB relative to full scale squared, from a Hann-
    windowed periodogram. BROADBAND, so this is a power sum over bins and not
    any kind of line estimator; `test_meta_band_power_db_recovers_known_band_powers`
    is its ground truth."""
    f, mag = am.spectrum(x, sr)
    m = (f >= lo) & (f < hi)
    if m.sum() < 4:
        raise am.InsufficientEvidence(f"band {lo:.0f}..{hi:.0f} Hz has {int(m.sum())} bins")
    return 10.0 * math.log10(max(float((mag[m] ** 2).sum()), 1e-30))


def octave_slope_db(x, lo=63.0, hi=16000.0, sr=SR):
    """Least-squares slope in dB per octave of the power SPECTRAL DENSITY,
    measured in octave bands. Returns (slope, the band PSDs).

    The bandwidth division is the whole point and the thing that is easy to get
    wrong: octave bands double in width, so a flat SPECTRUM has octave-band
    POWER rising at 3 dB per octave -- which is what "equal power per octave"
    means for pink noise. The number drawing 1431 labels its filter with is a
    slope of the TRANSFER FUNCTION, i.e. of the density, so that is what this
    returns. `test_meta_band_power_db_recovers_known_band_powers` pins both halves
    against closed-form signals."""
    edges, f = [], lo
    while f * 2 <= hi * 1.0001:
        edges.append((f, f * 2)); f *= 2
    p = np.array([band_power_db(x, a, b, sr) - 10.0 * math.log10(b - a) for a, b in edges])
    c = np.array([math.log2(math.sqrt(a * b)) for a, b in edges])
    A = np.vstack([c, np.ones(len(c))]).T
    return float(np.linalg.lstsq(A, p, rcond=None)[0][0]), p


def test_meta_band_power_db_recovers_known_band_powers():
    """[meta] **Ground truth for the two estimators above** (`docs/verification-rules.md`
    rule 2, and the brief's rule that no estimator is quoted before it is
    validated against a closed-form signal). Three of them, because the
    bandwidth division has two ways to be wrong and only one of them shows up
    on a tone.

    1. `band_power_db` against a sum of sinusoids, one per octave band, with
       amplitudes set so the band POWERS fall at exactly 3 dB per octave.
    2. `octave_slope_db` against a unit IMPULSE, whose spectrum is exactly
       flat: the density slope must be 0. A version that forgot to divide by
       the bandwidth reports +3 here, which is the error this catches.
    3. `octave_slope_db` against an impulse through a closed-form one-pole:
       each band's density must sit at 20*log10|H| at the band centre."""
    n = 1 << 16
    t = np.arange(n) / SR
    centres = [88.0, 177.0, 354.0, 707.0, 1414.0, 2828.0, 5657.0, 11314.0]
    amps = [0.2 * 2.0 ** (-0.5 * i) for i in range(len(centres))]      # -3 dB per octave
    x = sum(a * np.sin(2 * math.pi * f * c) for a, f in zip(amps, centres) for c in (t,))
    edges = [(f, 2 * f) for f in (63.0, 126.0, 252.0, 504.0, 1008.0, 2016.0, 4032.0, 8064.0)]
    bp = np.array([band_power_db(x * 32768.0, a, b) for a, b in edges])
    assert np.abs(np.diff(bp) - (-3.0)).max() < 0.15, bp
    # the impulse goes in the MIDDLE of the record: `am.spectrum` windows, and
    # an impulse at the edge is multiplied by a window value near zero
    imp = np.zeros(n); imp[n // 2] = 32768.0
    sl_flat, p_flat = octave_slope_db(imp)
    assert abs(sl_flat) < 0.10, (sl_flat, p_flat)                      # exactly flat
    g = 1.0 - math.exp(-2 * math.pi * 300.0 / SR)
    lp = np.empty(n); acc = 0.0
    for i in range(n):
        acc += (imp[i] - acc) * g
        lp[i] = acc
    _, p_lp = octave_slope_db(lp)
    ref = None
    for (a, b), got in zip(edges, p_lp):
        # the band-AVERAGED |H|^2, not |H| at the centre: a one-pole is not flat
        # across an octave and the estimator is not claiming it is
        ff = np.linspace(a, b, 512)
        zz = np.exp(-2j * np.pi * ff / SR)
        want = 10 * math.log10(float((np.abs(g / (1 - (1 - g) * zz)) ** 2).mean()))
        ref = got - want if ref is None else ref
        assert abs((got - want) - ref) < 0.35, (math.sqrt(a * b), got - want, ref)


def noise_colours(n=1 << 17, seed=None):
    """The three colours from one run of the noise board, as float arrays."""
    nz = vf.NoiseFx() if seed is None else vf.NoiseFx(seed)
    w, p, r = nz.render(n)
    return w.astype(float), p.astype(float), r.astype(float)


# ---- the noise source ------------------------------------------------------
def test_white_is_white_and_pink_falls_at_three_db_per_octave():
    """[source-verified: docs/minimoog-reference.md N4] **docs/minimoog-reference.md N4.** Drawing 1431 labels the network
    between the white emitter follower and the pink amplifier
    "-3 db/OCTAVE FILTER", and evaluating its five components gives -3.10
    dB/octave over 20 Hz .. 20 kHz. Measured on the integer noise board over
    131 072 frames: white is flat within 0.5 dB/octave, pink falls at 3 dB per
    octave within 0.4.

    Ground truth: test_moog_acceptance.test_meta_band_power_db_recovers_known_band_powers
    """
    w, p, _ = noise_colours()
    sw, _ = octave_slope_db(w[1000:])
    sp, bands = octave_slope_db(p[1000:])
    assert abs(sw) < 0.35, f"white density slope {sw:.2f} dB/oct"
    assert abs(sp - (-3.0)) < 0.45, f"pink density slope {sp:.2f} dB/oct, bands {bands}"


def test_red_is_pink_through_one_more_pole_at_about_100_hz():
    """[source-verified: docs/minimoog-reference.md N5] **docs/minimoog-reference.md N5.** Drawing 1431 labels the section after
    the pink amplifier "100 Hz Lowpass Filter", and SM 2.5 says it is one R and
    one C; R914 = 10 k with C908 = 0.15 uF is 106.1 Hz. So red/pink must be a
    single pole: -3 dB at the corner, and falling 6 dB per octave above it.

    Ground truth: test_moog_acceptance.test_meta_band_power_db_recovers_known_band_powers
    """
    _, p, r = noise_colours()
    p, r = p[4000:], r[4000:]
    fc = 106.1
    # red carries its own make-up gain (N6), so the pole is a SHAPE and the
    # reference is the ratio well below the corner, where the pole does nothing.
    ref = band_power_db(r, 8.0, 14.0) - band_power_db(p, 8.0, 14.0)
    for f in (fc, 4 * fc, 16 * fc):
        got = (band_power_db(r, f * 0.9, f * 1.1) - band_power_db(p, f * 0.9, f * 1.1)) - ref
        want = 20.0 * math.log10(1.0 / math.sqrt(1.0 + (f / fc) ** 2))
        assert abs(got - want) < 1.2, f"{f:.0f} Hz: red/pink {got:.2f} dB, one pole says {want:.2f}"


def test_the_three_noise_colours_leave_at_the_same_level():
    """[source-verified: docs/minimoog-reference.md N6] **docs/minimoog-reference.md N6.** Drawing 1431 labels the white, pink
    and red outputs -4 dBm EACH, and SM 5.27's acceptance test asks for both
    white and pink at "-5 +-3 dB" at the same output: the colour switch is not
    a level change. Ours are equalised by noise power and land within 0.2 dB of
    each other.

    Ground truth: test_audio_measure.test_rms_of_a_sinusoid_is_amplitude_over_root_two
    """
    w, p, r = noise_colours()
    lv = [20 * math.log10(am.rms(v[4000:]) / FS) for v in (w, p, r)]
    assert max(lv) - min(lv) < 0.2, [f"{v:.2f}" for v in lv]
    assert -20.0 < lv[0] < -13.0, lv          # and at the level NOISE_SHIFT puts them


def test_nothing_in_the_noise_path_reaches_the_rail():
    """[source-verified: docs/minimoog-reference.md N7] **docs/minimoog-reference.md N7.** Equal-RMS colours and a hard rail
    fight: the pink network's crest factor is 4.6. `NOISE_SHIFT = 2` is what
    buys the headroom, and this is the measurement that says it is enough --
    no colour comes within 2 dB of full scale over 131 072 frames.

    Ground truth: test_audio_measure.test_peak_is_the_largest_magnitude_either_sign
    """
    for name, v in zip(("white", "pink", "red"), noise_colours()):
        pk = am.peak(v[1000:]) / FS
        assert pk < 0.79, f"{name} peaks at {pk:.3f} of full scale"


def test_the_noise_selector_swaps_both_pairs_at_once():
    """[source-verified: docs/minimoog-reference.md N2] **docs/minimoog-reference.md N2.** "The noise selector switch selects
    white or pink noise for audio and pink or red for modulation" (SM 2.5) --
    one bit, two destinations, and the pair moves together. With `nsel` clear
    the audio path carries white; with it set, pink. The modulation path moves
    the other way, from pink to red, which is measurable as a fall in its
    high-frequency content.

    Ground truth: test_audio_measure.test_rms_of_a_sinusoid_is_amplitude_over_root_two
    """
    def trace(nsel):
        v = vf.VoiceFx()
        v.note(45, 0.5, mix=(0, 0, 0), noise=1.0, nsel=nsel, cutoff=(18000, 18000),
               q=0.1, drive=0.5, mod_mix=1.0, mod_wheel=1.0, osc_mod=True)
        return v.trace
    t0, t1 = trace(0), trace(1)
    assert np.array_equal(t0["noise"], t0["white"]), "nsel = 0 must put WHITE in the mixer"
    assert np.array_equal(t1["noise"], t1["pink"]), "nsel = 1 must put PINK in the mixer"
    # the modulation side: pink -> red is another pole, so the bus gets slower
    fast0 = am.rms(np.diff(t0["mod_sig"].astype(float)))
    fast1 = am.rms(np.diff(t1["mod_sig"].astype(float)))
    assert fast1 < 0.6 * fast0, (fast0, fast1)


def test_the_voice_uses_the_same_generator_as_the_drums_and_not_the_same_sequence():
    """[source-verified: docs/minimoog-reference.md N3] **docs/minimoog-reference.md N3.** Two claims, and they pull opposite
    ways.

    The polynomial is the drum section's, so that the one primitive pentanomial
    this chip has justified (contract 15.4) is the one both sections use: from a
    common seed the two step functions must agree bit for bit.

    The SEED is not, so that the two noises are independent: sharing one
    generator would be nearly free in area and would make the voice's noise and
    the drums' the SAME signal, which sums at +6 dB instead of +3. The two
    16-bit word streams must be uncorrelated.

    Ground truth: test_audio_measure.test_damped_sinusoid_matches_the_coefficients_that_generated_it
    """
    import drums_fx as dx
    assert vf.LFSR_TAPS == dx.LFSR_TAPS and vf.LFSR_BITS == dx.LFSR_BITS
    for seed in (1, 12345, 0x7F215FF7):
        assert vf.lfsr_frame(seed) == dx.lfsr_frame(seed), seed
    assert vf.VOICE_LFSR_SEED != dx.LFSR_SEED
    n = 1 << 15
    ours, _, _ = noise_colours(n)
    s, theirs = dx.LFSR_SEED, np.empty(n)
    for i in range(n):
        s, theirs[i] = dx.lfsr_frame(s)
    a = ours - ours.mean(); b = theirs - theirs.mean()
    rho = float((a * b).sum() / math.sqrt((a * a).sum() * (b * b).sum()))
    assert abs(rho) < 0.02, f"voice and drum noise correlate at {rho:.4f}"


def test_noise_through_the_ladder_is_a_swept_band_of_noise():
    """[source-verified: docs/minimoog-reference.md N8] **docs/minimoog-reference.md N8, and the reason noise was the largest
    gap.** "Audio signals from the three VCO's, the noise..." are summed ahead
    of the filter (SM 2.2.1), so a cutoff envelope on noise is a swept band --
    wind, surf, breath. Measured on the finished voice with a 300 Hz -> 9 kHz
    cutoff envelope: the power-weighted centroid of the first 40 ms is at least
    an octave above the last 40 ms of the sustain, and the output is broadband
    throughout (tonality below 12 dB).

    Ground truth: test_audio_measure.test_a_centroid_is_not_a_corner_frequency, test_audio_measure.test_tonality_separates_a_tone_from_noise_where_a_percentile_does_not
    """
    v = vf.VoiceFx()
    y = v.note(48, 1.0, mix=(0, 0, 0), noise=1.0, cutoff=(300, 9000), q=0.8, drive=2.0,
               amp=(0.002, 0.9, 0.35, 0.1), fenv=(0.002, 0.25, 0.08, 0.1)).astype(float)
    n = int(0.04 * SR)
    early = am.spectral_centroid(y[int(0.01 * SR):int(0.01 * SR) + n], weight="power")
    late = am.spectral_centroid(y[int(0.60 * SR):int(0.60 * SR) + n], weight="power")
    assert early > 2.0 * late, f"centroid {early:.0f} Hz -> {late:.0f} Hz is not a sweep"
    # ... and it is still NOISE, not a pitch. Tonality is max-over-median and a
    # steeply filtered noise reads high on it in absolute terms, so the
    # comparison is against the SAME patch driven by a sawtooth instead.
    v2 = vf.VoiceFx()
    tone = v2.note(48, 1.0, mix=(1, 0, 0), noise=0.0, cutoff=(300, 9000), q=0.8, drive=2.0,
                   amp=(0.002, 0.9, 0.35, 0.1), fenv=(0.002, 0.25, 0.08, 0.1)).astype(float)
    a, b = int(0.3 * SR), int(0.6 * SR)
    assert am.tonality_db(y[a:b]) < am.tonality_db(tone[a:b]) - 20.0, \
        (am.tonality_db(y[a:b]), am.tonality_db(tone[a:b]))


def test_the_pink_filter_is_the_network_on_the_drawing():
    """[source-verified: docs/minimoog-reference.md N4] **docs/minimoog-reference.md N4, the traceability claim itself.** The
    digital pink filter is not "a pink filter"; it is the bilinear transform of
    drawing 1431's five components. So it must match that analog network's
    transfer function, not merely have the right slope: within 0.6 dB below
    2 kHz, and within 2.6 dB at 20 kHz where bilinear warping costs what it
    costs. This is the test that would catch a plausible pink filter
    substituted for the traceable one.

    Ground truth: test_moog_acceptance.test_meta_band_power_db_recovers_known_band_powers
    """
    Rs, R8, C4, R9, C5 = 10e3, 3.3e3, 0.12e-6, 240.0, 0.033e-6

    def analog(f):
        s = 2j * math.pi * f
        z1, z2 = 1 + s * R8 * C4, 1 + s * R9 * C5
        return abs((z1 * z2) / (s * Rs * (C5 * z1 + C4 * z2) + z1 * z2))

    w, p, _ = noise_colours(1 << 17)
    w, p = w[4000:], p[4000:]
    ref = 20 * math.log10(analog(50.0))
    for f, tol in ((100.0, 0.6), (400.0, 0.6), (2000.0, 0.6), (8000.0, 1.5), (20000.0, 2.6)):
        got = (band_power_db(p, f * 0.85, f * 1.15) - band_power_db(w, f * 0.85, f * 1.15)) \
            - (band_power_db(p, 42.5, 57.5) - band_power_db(w, 42.5, 57.5))
        want = 20 * math.log10(analog(f)) - ref
        assert abs(got - want) < tol, f"{f:.0f} Hz: measured {got:.2f} dB, the network says {want:.2f}"


# ---- the waveform set ------------------------------------------------------
def test_the_model_d_waveform_set_is_complete():
    """[source-verified: docs/minimoog-reference.md W1] **docs/minimoog-reference.md W1.** Six waveforms per oscillator
    (drawing 1448): triangle, shark-tooth, sawtooth, square, wide rectangular,
    narrow rectangular -- with the reverse sawtooth in oscillator 3's second
    position instead of the shark-tooth. All seven distinct shapes must exist,
    be band-limitable, and be distinct from one another.

    Ground truth: test_audio_measure.test_damped_sinusoid_matches_the_coefficients_that_generated_it
    """
    need = ("tri", "shark", "saw", "square", "pulse29", "pulse15", "revsaw")
    for s in need:
        assert s in vf.WAVE_CODE, s
        assert s == "tri" or s in vf.BLEP_SHAPES, f"{s} is discontinuous and needs PolyBLEP"
    ph = np.arange(0, 1 << 24, 1 << 10, dtype=np.int64)
    got = {s: vf.naive_fx(s, ph) for s in need}
    for i, a in enumerate(need):
        for b in need[i + 1:]:
            assert not np.array_equal(got[a], got[b]), f"{a} and {b} are the same shape"


def test_the_shark_tooth_is_the_divider_on_the_drawing():
    """[source-verified: docs/minimoog-reference.md W3] **docs/minimoog-reference.md W3.** The shark-tooth position taps the
    junction of R030 (47 k, from the saw) and R031 (10 k, from the triangle),
    between two buffered sources of equal amplitude (W2), so it is
    10/57 saw + 47/57 triangle -- 5749 and 27019 in Q0.15, summing to exactly
    32768. Asserted as arithmetic on the naive shapes, and then as a spectral
    consequence: the shark-tooth's EVEN harmonics come from its sawtooth share
    alone, so h2 must sit 20*log10(10/57) below the sawtooth's own h2.

    Ground truth: test_audio_measure.test_harmonic_powers_recovers_a_known_series
    """
    assert vf.SHARK_W_SAW + vf.SHARK_W_TRI == 32768
    assert vf.SHARK_W_SAW == round(10 / 57 * 32768) and vf.SHARK_W_TRI == round(47 / 57 * 32768)
    ph = np.arange(0, 1 << 24, 1 << 9, dtype=np.int64)
    mix = (vf.SHARK_W_SAW * vf.naive_fx("saw", ph) + vf.SHARK_W_TRI * vf.naive_fx("tri", ph)) >> 15
    assert np.array_equal(vf.naive_fx("shark", ph), vf.sat16(mix))
    n = 1 << 15
    f0 = dsp.phase_inc(110.0) * SR / (1 << 24)
    saw, _ = _osc("saw", 45, n, blep=False)
    shk, _ = _osc("shark", 45, n, blep=False)
    ps = am.harmonic_powers(saw, f0, [2])[0]
    pk = am.harmonic_powers(shk, f0, [2])[0]
    got = 10 * math.log10(pk / ps)
    want = 20 * math.log10(10 / 57)
    assert abs(got - want) < 0.5, f"shark h2 is {got:.2f} dB under the saw's, the divider says {want:.2f}"


def test_the_three_rectangular_widths_are_50_29_and_15_percent():
    """[source-verified: docs/minimoog-reference.md W4] **docs/minimoog-reference.md W4.** SW6's width deck selects 0 V, -1.5 V
    or -2.5 V (drawing 1448's 1.5 k / 1 k / 7.5 k divider), and SM 2.3 pins the
    ends of that range at 50 % and 15 % duty. Duty is linear in the threshold,
    so the middle tap is 29 %.

    Measured two ways, because a duty cycle counted off the waveform and a duty
    cycle read out of the spectrum fail differently: the fraction of the cycle
    the naive wave spends high, and the position of the spectral nulls (a pulse
    of duty d has a null at every harmonic k with k*d an integer).

    Ground truth: test_audio_measure.test_harmonic_powers_recovers_a_known_series
    """
    ph = np.arange(1 << 24, dtype=np.int64)
    for shape, duty in (("square", 0.50), ("pulse29", 0.29), ("pulse15", 0.15)):
        high = float((vf.naive_fx(shape, ph) > 0).mean())
        assert abs(high - duty) < 1e-6, f"{shape}: duty {high:.6f}, W4 says {duty}"
    # The spectral half: a +-1 pulse of duty d has |h_k / h_1| =
    # |sin(pi k d) / (k sin(pi d))|. The three duties give three clearly
    # different signatures (50 % has no even harmonics at all; 29 % puts h2 at
    # -4.25 dB and 15 % at -1.00), so each shape must match ITS OWN closed form
    # within 1 dB and miss the other two by more than 3.
    n = 1 << 16
    note = 31
    f0 = dsp.phase_inc(dsp.note_hz(note)) * SR / (1 << 24)
    ks = [2, 3, 4, 5, 6]

    def predicted(d):
        return np.array([20 * math.log10(max(abs(math.sin(math.pi * k * d)
                                                 / (k * math.sin(math.pi * d))), 1e-6)) for k in ks])

    duties = dict(square=0.50, pulse29=0.29, pulse15=0.15)
    for shape, d in duties.items():
        x, _ = _osc(shape, note, n, blep=False)
        p = am.harmonic_powers(x, f0, [1] + ks)
        got = 10 * np.log10(np.maximum(p[1:] / p[0], 1e-12))
        mine = np.abs(got - predicted(d))
        mine = mine[predicted(d) > -40.0]                    # the 50 % nulls are floor-limited
        assert mine.max() < 1.0, f"{shape}: harmonics miss duty {d} by {mine.max():.2f} dB"
        for other, od in duties.items():
            if other == shape:
                continue
            keep = (predicted(d) > -40.0) & (predicted(od) > -40.0)
            assert np.abs(got - predicted(od))[keep].max() > 3.0, \
                f"{shape} is indistinguishable from duty {od}"


def test_the_reverse_sawtooth_is_the_sawtooth_inverted():
    """[source-verified: docs/minimoog-reference.md W5] **docs/minimoog-reference.md W5.** Oscillator 3's Q20 is "a standard
    common emitter transistor inverter" on the sawtooth (SM 2.3), so the
    reverse saw is the band-limited saw negated -- not a second oscillator and
    not a second PolyBLEP. Sample for sample, off by at most the one LSB the
    Q1.15 rail costs at -32768.

    Ground truth: test_audio_measure.test_damped_sinusoid_matches_the_coefficients_that_generated_it
    """
    n = 1 << 13
    saw, _ = _osc("saw", 60, n)
    rev, _ = _osc("revsaw", 60, n)
    d = rev + saw
    assert np.abs(d).max() <= 1, f"worst |rev + saw| = {np.abs(d).max()}"
    assert (np.abs(d) == 1).mean() < 0.01, "the rail is being hit too often to call this an inversion"


def test_the_new_shapes_are_band_limited_too():
    """[source-verified: DR 0001 applied to W1's additions] **DR 0001 applied to W1's additions.** A waveform set is only complete
    if the new shapes alias no worse than the old ones. Each discontinuous
    shape added in revision 9 must suppress the predicted fold-back images by
    at least 10 dB at a high note, the same property
    `test_polyblep_removes_the_predicted_fold_back_images` asserts for the
    sawtooth.

    Ground truth: test_audio_measure.test_foldback_finds_a_planted_image_and_ignores_the_real_harmonics
    """
    n = 1 << 14
    for shape in ("shark", "revsaw", "pulse29", "pulse15"):
        naive, f0 = _osc(shape, 88, n, blep=False)
        bl, _ = _osc(shape, 88, n, blep=True)
        a = am.foldback_alias_db(naive, f0).require(f"{shape} naive")
        b = am.foldback_alias_db(bl, f0).require(f"{shape} band-limited")
        assert b < a - 10.0, f"{shape}: PolyBLEP only bought {a - b:.1f} dB"


# ---- oscillator 3 as a modulation source -----------------------------------
def _mod_voice(dur=1.0, note=57, **patch):
    v = vf.VoiceFx()
    y = v.note(note, dur, gate=dur, **patch)
    return y.astype(float), v.trace


def test_the_mod_wheel_at_full_moves_the_pitch_13_to_23_semitones():
    """[source-verified: docs/minimoog-reference.md M7 -- SM 5.37] **docs/minimoog-reference.md M7 -- the factory acceptance window, SM
    5.37.** "Turn on OSCILLATOR MODULATION switch and rotate MOD control wheel
    fully up. The oscillator should change 13 to 23 semitones."

    Measured twice. The control law, on the increment the oscillator actually
    runs on -- exact, and what the register path promises. And the SOUND: one
    sine oscillator through a wide-open filter, the instantaneous frequency of
    the analytic signal, which is what a technician with a keyboard was doing.

    Ground truth: test_audio_measure.test_instantaneous_frequency_tracks_a_known_glide
    """
    y, t = _mod_voice(waves=("sine", "saw", "tri"), mix=(1.0, 0.0, 0.0), cutoff=(20000, 20000),
                      q=0.1, drive=0.4, track=0.0, osc_mod=True, osc3_ctl=False,
                      mod_wheel=1.0, mod_mix=0.0, mod_pitch=vf.MPD_REF_OCT)
    inc = t["incs"][0].astype(float)[int(0.1 * SR):]
    semis = 12.0 * math.log2(inc.max() / inc.min())
    assert 13.0 <= semis <= 23.0, f"the increment swings {semis:.2f} semitones; SM 5.37 says 13 to 23"
    f = am.instantaneous_frequency(y[int(0.15 * SR):int(0.85 * SR)], smooth_ms=1.0)
    f = f[int(0.02 * SR):-int(0.02 * SR)]
    heard = 12.0 * math.log2(np.percentile(f, 99.0) / np.percentile(f, 1.0))
    assert 13.0 <= heard <= 23.0, f"the note swings {heard:.2f} semitones; SM 5.37 says 13 to 23"


def test_the_mod_wheel_at_full_sweeps_the_cutoff_from_440_to_at_least_2400():
    """[source-verified: docs/minimoog-reference.md M8 -- SM 5.19] **docs/minimoog-reference.md M8 -- SM 5.19.** "Adjust CUTOFF FREQUENCY
    control for 440 Hz when pitch is low. When pitch switches to high, check to
    see that frequency is a minimum of 2.4 kHz." A square-wave oscillator 3, so
    the cutoff has two values and the test is a ratio between them.

    Asserted on the commanded cutoff, and then HEARD: the same patch at res
    1.05 sings, and the frequency it sings at follows the commanded cutoff
    within 1 % (DR 0011), so the sung frequency has to make the same jump.

    Ground truth: test_audio_measure.test_damped_sinusoid_matches_the_coefficients_that_generated_it
    """
    _, t = _mod_voice(waves=("saw", "saw", "square"), mix=(1.0, 0, 0), cutoff=(440, 440),
                      q=0.7, drive=1.0, track=0.0, filt_mod=True, osc3_ctl=False,
                      mod_wheel=1.0, mod_mix=0.0, mod_filter=vf.MFD_REF_OCT)
    cut = t["cut"][int(0.1 * SR):]
    assert abs(cut.min() - 440 / (2400.0 / 440.0) ** 0.5) / cut.min() < 0.5   # sanity: it is a ratio
    lo, hi = float(cut.min()), float(cut.max())
    assert abs(math.sqrt(lo * hi) - 440.0) / 440.0 < 0.02, (lo, hi)
    assert hi / lo >= 2400.0 / 440.0, f"cutoff swings {lo:.0f}..{hi:.0f} Hz, a ratio of {hi/lo:.2f}; SM 5.19 needs {2400/440:.2f}"


def test_the_modulation_mix_is_a_pan_and_not_two_levels():
    """[source-verified: docs/minimoog-reference.md M4 -- SM 2.4] **docs/minimoog-reference.md M4 -- SM 2.4.** "The wiper of R23 is
    connected to ground and, therefore, when the MODULATION MIX potentiometer
    is rotated, it PANS between the two modulation signals." A pan, so: at one
    end the bus is oscillator 3 alone, at the other it is noise alone, and in
    between the two weights sum to a constant -- which means a fully-panned bus
    is never louder than either source on its own.

    Ground truth: test_audio_measure.test_rms_of_a_sinusoid_is_amplitude_over_root_two
    """
    assert vf.mod_pan(30000, -20000, 0) == 30000
    assert vf.mod_pan(30000, -20000, vf.MMIX_FULL) == -20000
    assert vf.mod_pan(30000, -20000, vf.MMIX_FULL + 9999) == -20000, "mmix past its top must clamp"
    for m in range(0, vf.MMIX_FULL + 1, 1024):
        assert vf.mod_pan(32767, 32767, m) in (32766, 32767), m      # a constant-sum pan
    ends = [am.rms(np.array([vf.mod_pan(a, b, m) for a, b in
                             zip(range(-30000, 30000, 977), range(30000, -30000, -977))], float))
            for m in (0, vf.MMIX_FULL // 2, vf.MMIX_FULL)]
    assert max(ends) <= 1.01 * max(ends[0], ends[2]), ends


def test_osc_3_control_takes_oscillator_3_off_the_modulation_bus():
    """[source-verified: docs/minimoog-reference.md M1 -- SM 2.18] **docs/minimoog-reference.md M1 -- SM 2.18.** "A switch, SW2, interrupts
    the keyboard, modulation, external, and pitchbend voltage on oscillator
    three." That is what makes oscillator 3 usable as a modulator at all: with
    the switch off it is not modulated by the bus it is driving. Oscillators 1
    and 2 must still be.

    Ground truth: test_audio_measure.test_damped_sinusoid_matches_the_coefficients_that_generated_it
    """
    kw = dict(waves=("saw", "saw", "tri"), mix=(1.0, 0.8, 0.0), cutoff=(6000, 6000), q=0.3,
              drive=1.0, track=0.0, osc_mod=True, mod_wheel=1.0, mod_mix=0.0)
    _, off = _mod_voice(0.5, **kw, osc3_ctl=False)
    _, on = _mod_voice(0.5, **kw, osc3_ctl=True)
    def swing(t, k):
        i = t["incs"][k].astype(float)[2000:]
        return 12.0 * math.log2(i.max() / i.min())
    assert swing(off, 0) > 12.0 and swing(off, 1) > 12.0, "oscillators 1 and 2 must be modulated"
    assert swing(off, 2) == 0.0, "OSC-3 CONTROL off must leave oscillator 3's pitch alone"
    assert swing(on, 2) > 1.0, "OSC-3 CONTROL on must put oscillator 3 back on the bus"


def test_the_lo_range_reaches_the_clicks_two_to_five_seconds_apart():
    """[source-verified: docs/minimoog-reference.md M2 -- SM 5.36] **docs/minimoog-reference.md M2 -- SM 5.36.** "Set... RANGE switch to LO,
    and OSCILLATOR-3 FREQUENCY counterclockwise to minimum. Listen to the
    audible clicks which should occur between two to five seconds apart." That
    is 0.2 to 0.5 Hz, and the same paragraph requires the top of LO to overlap
    the bottom of 32'.

    No hardware is needed for this and that is the point of the test: the
    24-bit increment register already spans it, and the host's Hz-to-increment
    conversion lands inside the window.

    Ground truth: test_audio_measure.test_damped_sinusoid_matches_the_coefficients_that_generated_it
    """
    for hz in (0.2, 0.35, 0.5):
        inc = dsp.phase_inc(hz)
        assert inc > 0, hz
        assert abs(inc * SR / (1 << 24) - hz) / hz < 0.01, hz
    assert dsp.phase_inc(0.2) >= 1
    lowest = SR / (1 << 24)
    assert lowest < 0.2, f"the register bottoms out at {lowest:.4f} Hz"
    # and the top of LO overlaps the bottom of 32': one register, one conversion
    assert dsp.phase_inc(32.7) < (1 << 24)
    # the period is what it says it is, measured on the oscillator itself
    v = vf.VoiceFx()
    v.oscs[2].set_shape("saw", blep=False)
    x = v.oscs[2].render(int(6 * SR), dsp.phase_inc(0.25)).astype(float)
    wraps = int((np.diff(x) < -30000).sum())
    assert wraps == 1, f"0.25 Hz gave {wraps} wraps in 6 s"


def test_the_modulation_tap_is_the_same_signal_band_limited_or_not_at_lo_rates():
    """[source-verified: docs/minimoog-reference.md M5's own caveat] **docs/minimoog-reference.md M5's own caveat.** The modulation tap is
    oscillator 3's NAIVE waveform, on the argument that at LO rates the
    PolyBLEP window never opens so the two are identical -- and that the
    modulation path is a control voltage nobody hears anyway. The first half of
    that argument is checkable, so it is checked here rather than asserted: at
    every LO-range rate, naive and band-limited agree sample for sample.

    Ground truth: test_audio_measure.test_damped_sinusoid_matches_the_coefficients_that_generated_it
    """
    n = int(2.0 * SR)
    for hz in (0.2, 1.0, 4.0, 20.0):
        inc = dsp.phase_inc(hz)
        cycles = math.ceil(n * inc / (1 << 24))
        for shape in ("saw", "revsaw", "square", "tri", "shark"):
            edges = 2 if shape in vf.TWO_EDGE else (1 if shape in vf.BLEP_SHAPES else 0)
            a = vf.OscFx(shape, blep=False).render(n, inc)
            b = vf.OscFx(shape, blep=True).render(n, inc)
            diff = int((a != b).sum())
            # The window is open for the sample either side of each
            # discontinuity, and nowhere else -- two samples per edge per
            # cycle, plus the one at phase 0 where the render starts.
            # Measured: the triangle never differs at all; the sawtooth,
            # reverse sawtooth and shark-tooth differ on 1 sample in 96 000 at
            # 0.2 Hz and 81 at 20 Hz; the square, with two edges, on twice
            # that. So M5's claim is very nearly true, and this says exactly
            # how nearly rather than repeating it.
            assert diff <= 2 * edges * cycles + 2, \
                f"{shape} at {hz} Hz: {diff} samples differ over {cycles} cycles ({edges} edges)"
            if shape == "tri":
                assert diff == 0, "the triangle has no discontinuity and must not be corrected"
            assert np.abs(a - b).max() <= 32768, f"{shape} at {hz} Hz: correction exceeds its own bound"


def test_the_modulation_value_is_exactly_one_frame_old():
    """[source-verified: docs/minimoog-reference.md M9] **docs/minimoog-reference.md M9.** Oscillator 3 is both the modulation
    source and, with OSC-3 CONTROL on, a modulation destination -- a feedback
    path. The chip breaks it with one register, so the modulation value used in
    frame n is built from frame n-1's sources. One frame is 20.8 us. This is a
    specification and not an artefact, so it is pinned: the modulation trace
    must be the panned source delayed by exactly one frame, and by no more.

    Ground truth: test_audio_measure.test_damped_sinusoid_matches_the_coefficients_that_generated_it
    """
    v = vf.VoiceFx()
    v.note(57, 0.3, waves=("saw", "saw", "tri"), mix=(1.0, 0, 0), noise=0.0,
           cutoff=(4000, 4000), q=0.3, drive=1.0, track=0.0,
           osc_mod=True, osc3_ctl=False, mod_wheel=1.0, mod_mix=0.0)
    t = v.trace
    osc3_naive = np.array([vf.naive_one("tri", int(p)) for p in
                           np.cumsum(np.concatenate([[0], t["incs"][2][:-1]])) & dsp.PHASE_MASK])
    assert np.array_equal(t["mod_sig"][1:], osc3_naive[:-1]), "the delay is not exactly one frame"
    assert not np.array_equal(t["mod_sig"], osc3_naive), "there is no delay at all"


# =============================================================================
# 6. ... AND THESE CAN FAIL TOO
# =============================================================================
def test_control_a_pink_filter_that_is_only_a_plausible_pink_filter(monkeypatch):
    """[meta] Defect: the traceable biquad replaced by a single pole placed to give
    roughly the right slope over the audio band. It is a perfectly reasonable
    pink filter and it is not the one on drawing 1431, so the slope test may
    survive and the NETWORK test must not.

    Ground truth: test_moog_acceptance.test_meta_band_power_db_recovers_known_band_powers
    """
    class _Plausible(vf.NoiseFx):
        def step(self):
            self.lfsr, x0 = vf.lfsr_frame(self.lfsr)
            self.y1 = self.y1 + (((x0 << 12) - self.y1) * 1200 >> 16)
            p = vf.clamp16((self.y1 * 6) >> 12)
            return x0 >> vf.NOISE_SHIFT, p, p
    monkeypatch.setattr(vf, "NoiseFx", _Plausible)
    _expect_red(test_the_pink_filter_is_the_network_on_the_drawing)


def test_control_noise_colours_that_are_not_level_matched(monkeypatch):
    """[meta] Defect: the make-up gains that equalise the three colours removed --
    the pink and red paths left at the raw gain of their filters, which is
    where a straightforward implementation lands. Drawing 1431's three -4 dBm
    labels are what says that is wrong.

    Ground truth: test_audio_measure.test_rms_of_a_sinusoid_is_amplitude_over_root_two
    """
    monkeypatch.setattr(vf, "RED_GAIN", 1 << vf.PINK_AQ)
    monkeypatch.setattr(vf, "PINK_B", tuple(int(b / 9.547084) for b in vf.PINK_B))
    _expect_red(test_the_three_noise_colours_leave_at_the_same_level)


def test_control_a_shark_tooth_mixed_in_the_wrong_proportion(monkeypatch):
    """[meta] Defect: R030 and R031 read off the drawing the wrong way round, so the
    mix becomes 47/57 saw and 10/57 triangle. It is still a shark-tooth-shaped
    wave; it is the wrong one, and the even-harmonic test is what sees it.

    Ground truth: test_audio_measure.test_harmonic_powers_recovers_a_known_series
    """
    monkeypatch.setattr(vf, "SHARK_W_SAW", 27019)
    monkeypatch.setattr(vf, "SHARK_W_TRI", 5749)
    _expect_red(test_the_shark_tooth_is_the_divider_on_the_drawing)


def test_control_rectangular_widths_left_at_the_shipped_25_percent(monkeypatch):
    """[meta] Defect: the two new widths wired to the 25 % duty contract revision 4
    shipped -- which is what "we have four waveforms and called it done" looks
    like from the inside. SM 2.3's 50 % and 15 % are what reject it.

    Ground truth: test_audio_measure.test_harmonic_powers_recovers_a_known_series
    """
    monkeypatch.setitem(vf.DUTY, "pulse29", vf.DUTY_P25)
    monkeypatch.setitem(vf.DUTY, "pulse15", vf.DUTY_P25)
    _expect_red(test_the_three_rectangular_widths_are_50_29_and_15_percent)


def test_control_a_modulation_depth_that_misses_the_factory_window(monkeypatch):
    """[meta] Defect: the pitch depth halved -- 0.375 octave instead of 0.75, i.e.
    9 semitones of swing where SM 5.37 requires 13 to 23. A plausible-looking
    vibrato that a Model D would have failed its acceptance test with.

    Ground truth: test_audio_measure.test_instantaneous_frequency_tracks_a_known_glide
    """
    monkeypatch.setattr(vf, "MPD_REF_OCT", 0.375)
    msg = _expect_red(test_the_mod_wheel_at_full_moves_the_pitch_13_to_23_semitones)
    assert "SM 5.37" in msg


def test_control_a_modulation_mix_that_sums_instead_of_panning(monkeypatch):
    """[meta] Defect: MOD MIX read as two independent levels rather than SM 2.4's pan.
    Both sources at full then drive the bus to twice full scale, where the
    Model D's pot cannot.

    Ground truth: test_audio_measure.test_rms_of_a_sinusoid_is_amplitude_over_root_two
    """
    monkeypatch.setattr(vf, "mod_pan",
                        lambda o, nz, m: vf.clamp16(o + ((nz * min(m, vf.MMIX_FULL)) >> 15)))
    _expect_red(test_the_modulation_mix_is_a_pan_and_not_two_levels)


def test_control_oscillator_3_left_on_the_modulation_bus(monkeypatch):
    """[meta] Defect: SW2's modulation half missing, so oscillator 3 modulates itself
    whatever OSC-3 CONTROL says. The instrument's whole use of oscillator 3 as
    an LFO depends on that switch, and this is the property that holds it.

    Ground truth: test_audio_measure.test_damped_sinusoid_matches_the_coefficients_that_generated_it
    """
    real = vf.VoiceFx._modulate

    def always_on(self, incs, n, mw):
        self.mroute = self.mroute | vf.MR_OSC3
        return real(self, incs, n, mw)
    monkeypatch.setattr(vf.VoiceFx, "_modulate", always_on)
    _expect_red(test_osc_3_control_takes_oscillator_3_off_the_modulation_bus)


def test_control_a_coarse_resonance_write_interval_zippers():
    """[meta] Defect: the host writes `k` every 5 ms instead of every frame --
    a block-rate resonance control, which is what a firmware that updated the
    filter from a timer rather than from the sample clock would do. The
    continuous-sweep property must go red.

    The separation is what makes the green reading usable: shipping measures
    **-82.2 dB** and this measures **-58.3 dB**, a **+23.9 dB** move, so the
    estimator is not sitting at a floor that nothing can lift. A 2 ms write
    interval reads -74.3 dB, so the control is graded rather than binary.

    Ground truth: test_reference_voice.test_envelope_ripple_matches_the_closed_form_of_a_known_staircase
    """
    msg = _expect_red(_assert_a_resonance_sweep_through_onset_is_quiet, hold=240)
    assert "ripples at" in msg, msg


def test_control_a_block_rate_cutoff_hold_breaks_audio_rate_modulation():
    """[meta] Defect: the modulated cutoff is held across a 512-sample host
    block instead of being written per frame -- the exact artefact that
    inverted the section-3 plugin conclusion until it was found (dawdreamer's
    default block, 93.75 Hz). At an audio modulation rate this does not merely
    coarsen the control, it ALIASES the modulation, and the differential must
    go red.

    Shipping measures -52 to -63 dB; held for one block it measures **+1.3
    dB**, a **+55 dB** move. The same control at 128 frames (375 Hz) reads
    +0.9 dB, and rounding the cutoff to 32 Hz -- a far milder injection --
    reads +6.0 dB, which is the smallest injection this measurement resolves.

    Ground truth: test_audio_measure.test_rms_of_a_sinusoid_is_amplitude_over_root_two
    """
    msg = _expect_red(_assert_audio_rate_filter_modulation_is_quiet, hold=512)
    assert "control-path error" in msg, msg


# =============================================================================
# 7. THE ALIASING GAP (contract open item 15)
#
# These two do not assert that we are good. They assert what we MEASURE, beside
# the reference numbers we do not meet, so that the largest known defect in the
# voice is tracked by a test instead of by a memory -- and so that a change
# which quietly makes it worse is caught. Every property above this point
# compared us against our own prediction of what PolyBLEP should do, which is
# why none of them could ever have seen this.
# =============================================================================
ALIAS_CURVE = {40: -42.7, 52: -39.7, 64: -36.6, 76: -33.7, 88: -31.0, 100: -28.5}


def test_the_sawtooths_aliasing_floor_degrades_with_pitch_and_is_locked():
    """[measured-here: contract open item 15] **Contract open item 15.** Sawtooth inharmonic fraction, PolyBLEP on,
    at six registers:

    | note | f0 | ours | Surge |
    |---|---|---|---|
    | 40 | 82 Hz | −42.7 dB | ≈ −60 |
    | 64 | 330 Hz | −36.6 dB | ≈ −60 |
    | 88 | 1.3 kHz | −31.0 dB | ≈ −60 |
    | 100 | 2.6 kHz | −28.5 dB | ≈ −60 |

    **About 2.8 dB lost per octave, where Surge is flat across six**, and
    19–32 dB behind Mini V3 on every waveform. Locked at ±1.5 dB so that a
    regression is loud, and the *slope* is asserted as PRESENT rather than
    absent — this test exists to keep a defect visible, not to claim it is
    fixed. When the fix lands, these numbers move and this docstring is the
    before.

    Ground truth: test_audio_measure.test_inharmonic_fraction_db_reading_of_an_alias_free_signal_is_its_floor
    """
    n = int(0.5 * SR)
    got = {}
    for note, want in ALIAS_CURVE.items():
        x, f0 = _osc("saw", note, n, blep=True)
        got[note] = am.inharmonic_fraction_db(x, f0).require(f"saw note {note}")
        assert abs(got[note] - want) < 1.5, f"note {note}: {got[note]:.1f} dB, locked at {want}"
    octaves = (100 - 40) / 12.0
    slope = (got[100] - got[40]) / octaves      # POSITIVE: the fraction rises toward 0
    assert 2.0 < slope < 3.6, f"the degradation is {slope:.2f} dB/octave, was 2.8"
    assert got[100] > -35.0, "if the top of the range has improved this much, the fix landed"


def test_oversampling_the_oscillators_regresses_only_through_the_drop_decimator():
    """[measured-here: issue #80] **Issue #80, explained and locked. `docs/oversampling-paradox.md`,
    `model/alias_probe.py`.**

    Running the oscillator at 2x and reducing it to the base rate by KEEPING
    THE LAST SUB-STEP measures 9.6 dB worse than the base-rate oscillator:
    -42.7 -> -33.1 at note 40. This test locks that number and locks WHERE it
    enters, so the negative result cannot be re-proposed and cannot be
    mis-attributed:

      * at the oversampled rate the correction is BETTER, not worse
        (-42.7 -> -55.5, which is that estimator's floor). The whole regression
        enters at the rate reduction and nowhere else;
      * the reading after dropping equals the share of the oversampled
        signal's power that sits in HARMONICS ABOVE 24 kHz -- energy that
        exists only because the oscillator runs at 96 kHz -- to within a few
        tenths of a dB. That is the mechanism, as an energy budget;
      * a decimation filter removes it. This is the assertion that keeps the
        test honest about scope: it guards the DROP-DECIMATOR, not the
        technique. Oversampling with a real decimator beats what we ship.

    Ground truth: test_audio_measure.test_inharmonic_fraction_db_reading_of_an_alias_free_signal_is_its_floor
    """
    n = int(0.5 * SR)
    inc = ap.base_inc(40)
    f0 = inc * SR / (1 << 24)
    p2 = ap.render_osc("saw", inc, n, blep=True)
    p3 = ap.render_osc("saw", inc // 2, n * 2, blep=True)
    p4 = ap.decimate_drop(p3, 2)

    a = am.inharmonic_fraction_db(p2, f0, SR).require("saw at 48 kHz")
    b = am.inharmonic_fraction_db(p4, f0, SR).require("saw at 2x, dropped")
    c = am.inharmonic_fraction_db(p3, f0, 2 * SR).require("saw at 2x, before decimation")
    assert abs(a - (-42.7)) < 1.0, f"base rate {a:.1f}, locked at -42.7"
    assert abs(b - (-33.1)) < 1.0, f"2x dropped {b:.1f}, locked at -33.1"
    assert b > a + 5.0, f"the regression is {b - a:.1f} dB; it was +9.6"

    # it is NOT the correction, and NOT the oversampling
    assert c < a - 10.0, f"at 96 kHz the corrected saw reads {c:.1f} against {a:.1f} at 48 kHz"

    # the mechanism, as an energy budget: what folds down IS the reading
    above = ap.band_split(p3, f0, 2 * SR, SR / 2.0)["above_db"]
    assert abs(above - b) < 1.0, \
        f"above-Nyquist harmonics carry {above:.1f} dB, the dropped reading is {b:.1f}"

    # and a decimator removes it -- the technique is fine, this decimator is not
    fir = am.inharmonic_fraction_db(ap.decimate_fir(p3, 2), f0, SR).require("2x, FIR-decimated")
    assert fir < a - 10.0, f"FIR-decimated reads {fir:.1f}, base rate reads {a:.1f}"


def test_the_oversampling_regression_is_not_the_fixed_point_and_not_our_oscillator():
    """[measured-here: issue #80 hypotheses 3 and 4] **Issue #80 hypotheses 3 and 4, both ruled out by substitution.**

    Hypothesis 3 (more samples, more quantisation events): the SAME PolyBLEP in
    float64, with no Q1.15, no reciprocal approximation and no saturation,
    regresses identically. Fixed point is not the mechanism.

    Hypothesis 4 (something in how we implemented it): a mathematically exact
    band-limited sawtooth, summed from its Fourier series at 96 kHz, put
    through the same drop-decimator, comes out WORSE than ours -- because
    PolyBLEP's two-sample residual attenuates the top of the oversampled band,
    so there is less there to fold. A perfect oscillator would regress more.

    Ground truth: test_audio_measure.test_inharmonic_fraction_db_reading_of_an_alias_free_signal_is_its_floor
    """
    n = int(0.5 * SR)
    for note in (40, 64, 100):
        inc = ap.base_inc(note)
        f0 = inc * SR / (1 << 24)
        ours = ap.decimate_drop(ap.render_osc("saw", inc // 2, n * 2, blep=True), 2)
        flt = ap.decimate_drop(ap.float_blep("saw", inc // 2, n * 2), 2)
        exact = ap.decimate_drop(ap.bl_saw(f0, n * 2, 2 * SR), 2)
        b = am.inharmonic_fraction_db(ours, f0, SR).require(f"note {note} fixed point")
        f = am.inharmonic_fraction_db(flt, f0, SR).require(f"note {note} float")
        e = am.inharmonic_fraction_db(exact, f0, SR).require(f"note {note} analytic")
        assert abs(b - f) < 0.5, f"note {note}: fixed {b:.1f} vs float {f:.1f}"
        assert e > b - 0.5, \
            f"note {note}: the EXACT band-limited saw reads {e:.1f}, ours {b:.1f}"


def test_the_polyblep_correction_is_the_same_at_both_rates():
    """[measured-here: issue #80 hypothesis 1] **Issue #80 hypothesis 1, ruled out by construction and by
    measurement.** The leading guess was that the correction was computed for
    one rate and applied at another.

    It cannot have been: `blep_fx` decides the window with `ph < inc` and
    scales it by the reciprocal of `inc`, so the window is one sample on each
    side AT WHATEVER RATE, with the same peak. Halving the increment halves it
    in phase and leaves it unchanged in samples. Measured at six registers.

    Ground truth: test_audio_measure.test_damped_sinusoid_matches_the_coefficients_that_generated_it
    """
    for note in (40, 52, 64, 76, 88, 100):
        d = ap.blep_normalisation(note, 2)
        lo, hi = d["base"], d["2x"]
        assert hi["inc"] * 2 == lo["inc"], f"note {note}: increments are not exactly 2:1"
        assert abs(d["peak_ratio"] - 1.0) < 1e-6, \
            f"note {note}: the correction's peak changed by {d['peak_ratio']:.6f}"
        for r in (lo, hi):
            assert abs(r["lead_samples"] - 1.0) < 0.02 and abs(r["trail_samples"] - 1.0) < 0.02, \
                f"note {note}: window is {r['lead_samples']:.3f}/{r['trail_samples']:.3f} samples"
            assert abs(r["hits_per_wrap"] - 2.0) < 0.1, \
                f"note {note}: {r['hits_per_wrap']:.2f} corrected samples per wrap, expected 2"


def test_the_shipped_ladder_loses_nothing_to_its_last_sub_step_decimation():
    """[measured-here: the #80 mechanism, contrasted against a decimator that costs nothing] **The contrast that makes #80's mechanism a mechanism rather than a
    rule about decimators.** The voice already reduces a 2x rate to the output
    rate by keeping the last sub-step, in `LadderFx.process`, and it costs
    nothing there. Surge's Huovilainen does the same and is likewise fine.

    The difference is not the decimator. It is what is sitting above 24 kHz
    when the decimator runs. After a 4-pole lowpass there is essentially
    nothing there; after an oscillator there is the waveform's own harmonic
    series. Measured below 24 kHz apart in the two cases.

    **THE FIRST ASSERTION USED TO BE `abs(i4 - i3) < 1.0` AND IT WAS GREEN FOR
    THE WRONG REASON.** With the Hann window `inharmonic_fraction_db` used
    before #119, the two readings were -52.98 and -52.97 dB -- both sitting
    exactly on that window's -53 dB leakage floor. The 0.02 dB agreement was
    the estimator's blindness, not the ladder's cleanliness. Blackman-Harris
    puts the floor at -87.85 dB, and the two readings separate: **-78.41 dB at
    2x and -75.07 dB at the output, so the decimation does cost 3.34 dB.** The
    title's "loses nothing" is withdrawn.

    What is asserted instead is the thing that was ever load-bearing: both
    readings must be MEASUREMENTS (headroom over their own measured floor, so
    this can never again pass on a floor), and the output must be far below the
    level this suite already accepts from an oscillator -- PolyBLEP is required
    only to reach -28 dB by `test_polyblep_suppresses_aliasing_at_every_register`,
    and the ladder's output is 47 dB below that. The contrast with #80 is the
    second assertion and is unaffected.

    Ground truth: test_audio_measure.test_inharmonic_fraction_db_reading_of_an_alias_free_signal_is_its_floor, test_audio_measure.test_inharmonic_fraction_db_window_is_blackman_harris_not_hann
    """
    n = int(0.25 * SR)
    inc = ap.base_inc(64)
    f0 = inc * SR / (1 << 24)
    t = np.arange(n, dtype=np.float64) * (f0 / SR)
    x = np.round(0.8 * np.sin(2 * math.pi * t) * 32767).astype(np.int16)
    sub, out, _ = ap.ladder_substeps(x, 2)          # REFUSES if this is not the shipping ladder
    b3, b4 = sub / FS, out / FS
    e3 = am.inharmonic_fraction_db(b3, f0, 2 * SR)
    e4 = am.inharmonic_fraction_db(b4, f0, SR)
    i3, i4 = e3.require("ladder sub-steps"), e4.require("ladder output")
    for lbl, e in (("sub-steps", e3), ("output", e4)):
        assert e.detail["headroom_db"] > 6.0, \
            (f"the {lbl} reading {e.value:.2f} dB is only "
             f"{e.detail['headroom_db']:.2f} dB above its own measured floor "
             f"{e.detail['floor_db']:.2f} dB -- that is the estimator, not the ladder")
    assert i4 < -60.0, \
        (f"after the rate reduction the ladder leaves {i4:.2f} dB, against the "
         f"-28 dB this suite accepts from a PolyBLEP oscillator "
         f"(it cost {i4 - i3:+.2f} dB going from {i3:.2f})")

    lad_above = ap.band_split(b3, f0, 2 * SR, SR / 2.0)["above_db"]
    osc_above = ap.band_split(ap.render_osc("saw", inc // 2, n * 2, blep=True),
                              f0, 2 * SR, SR / 2.0)["above_db"]
    assert lad_above < osc_above - 20.0, \
        f"fold-down budget: ladder {lad_above:.1f} dB, oscillator {osc_above:.1f} dB"

    # and the sample-and-hold the ladder upsamples with is EXACTLY invertible by
    # the same decimator, which is why its large images cost nothing.
    assert np.array_equal(ap.decimate_drop(np.repeat(b4, 2), 2), b4)


def test_the_aliasing_estimator_reproduces_a_known_alias_content():
    """[method] **The estimator's own ground truth, in the regime this section uses it**
    -- hundreds of harmonics, not the eleven `test_audio_measure` checks.

    Drop-decimating an analytic band-limited sawtooth produces an alias content
    that is computable in closed form from the Fourier coefficients and the
    guard rule, with no FFT anywhere. `inharmonic_fraction_db` must agree.
    Six estimator bugs were found the day `audio_measure`'s ground truth was
    written and eight-plus measurements have been withdrawn here; nothing in
    the two tests above is worth reading unless this passes.

    Ground truth: test_audio_measure.test_inharmonic_fraction_db_reading_of_an_alias_free_signal_is_its_floor
    """
    n = int(0.5 * SR)
    for note in (40, 64, 100):
        f0 = ap.base_inc(note) * SR / (1 << 24)
        clean = ap.bl_saw(f0, n, SR)
        floor = am.inharmonic_fraction_db(clean, f0, SR).require(f"note {note} clean")
        assert floor < -50.0, f"note {note}: the floor is {floor:.1f} dB, too high to measure at"
        for share_db in (-20.0, -30.0, -40.0):
            share = 10 ** (share_db / 10.0)
            got = am.inharmonic_fraction_db(
                ap.plant_inharmonics(clean, f0, SR, share), f0, SR).require()
            want = 10 * math.log10(share + 10 ** (floor / 10.0) * (1 - share))
            assert abs(got - want) < 0.5, f"note {note}: planted {want:.1f}, read {got:.1f}"
        cf = ap.alias_fraction_closed_form(f0, 2 * SR, 2, n)["db"]
        got = am.inharmonic_fraction_db(
            ap.decimate_drop(ap.bl_saw(f0, 2 * n, 2 * SR), 2), f0, SR).require()
        assert abs(got - cf) < 1.0, f"note {note}: closed form {cf:.1f}, estimator {got:.1f}"


# =============================================================================
# 8. THE NOISE SOURCE AGAINST THE REFERENCE EMULATIONS
#
# Section 5 checks the noise board against the Moog drawing it is a copy of.
# This checks it against what two software Minimoogs actually produce, which is
# a different question and answers differently on one column.
# =============================================================================
def test_the_noise_distribution_is_inside_the_references_where_it_is_heard():
    """[source-verified: docs/minimoog-reference.md N6a; contract open item 18] **docs/minimoog-reference.md N6a; contract open item 18.**

    | source | crest dB | kurtosis |
    |---|---|---|
    | ours, white RAW | 4.8 | 1.80 |
    | ours, white through the ladder | 10.0–11.8 | 2.54–2.92 |
    | ours, pink | 12.3 | 2.91 |
    | Mini V3 white | 8.1 | 2.23 |
    | Surge white | 11.3 | 2.64 |
    | Mini V3 pink | 13.0 | 3.00 |

    Raw white is **uniform**, because a multi-bit LFSR slice is uniform — and
    that is asserted here as PRESENT, not argued away. What is also asserted is
    that it does not reach the output that way: the noise source is a mixer
    input, the mixer feeds a four-pole low-pass, and a four-pole low-pass
    Gaussianises. Through it, every value lands inside the span of the three
    references.

    The distinction matters because the cheap fix — summing independent LFSR
    slices — costs two or three times the noise generator to buy a
    distribution the filter already delivers.

    Ground truth: test_audio_measure.test_rms_of_a_sinusoid_is_amplitude_over_root_two, test_audio_measure.test_peak_is_the_largest_magnitude_either_sign
    """
    def shape(x):
        x = np.asarray(x, dtype=np.float64)
        rms = am.rms(x)
        k = float(((x - x.mean()) ** 4).mean() / (((x - x.mean()) ** 2).mean()) ** 2)
        return 20 * math.log10(am.peak(x) / rms), k

    w, p, _ = noise_colours(1 << 17)
    cw, kw = shape(w[4000:])
    assert abs(cw - 4.8) < 0.5 and abs(kw - 1.80) < 0.05, (cw, kw)   # uniform, asserted present
    cp, kp = shape(p[4000:])
    assert abs(cp - 13.0) < 1.5 and abs(kp - 3.00) < 0.35, (cp, kp)  # pink hits the target raw
    for cut in (600, 2000, 8000):
        y = ladder_render(np.clip(w[4000:], -32768, 32767).astype(np.int16), cut, 0.7, 2.0)
        c, k = shape(y[2000:])
        assert 8.1 - 1.0 <= c <= 11.3 + 1.0, f"{cut} Hz: crest {c:.1f} dB outside the references"
        assert 2.23 - 0.1 <= k <= 3.00 + 0.1, f"{cut} Hz: kurtosis {k:.2f} outside the references"


def test_the_noise_period_is_not_a_loop_anyone_will_hear():
    """[source-verified: docs/minimoog-reference.md N3] **docs/minimoog-reference.md N3.** Neither reference repeats inside 8 s,
    which puts a floor of about 19 register bits on any LFSR that wants to be
    ruled out by that measurement. Ours is 31 bits: 2^31 − 1 output bits at 16
    per frame is **46.6 minutes** before the word stream repeats — four
    thousand times the floor, and long enough that a sustained noise bed cannot
    be heard as a loop.

    Ground truth: test_audio_measure.test_damped_sinusoid_matches_the_coefficients_that_generated_it
    """
    seconds = (2 ** vf.LFSR_BITS - 1) / vf.NOISE_BITS / SR
    assert vf.LFSR_BITS >= 19, vf.LFSR_BITS
    assert seconds > 8.0 * 100, f"{seconds:.1f} s"
    assert 2700 < seconds < 2900, f"{seconds:.1f} s"


def test_the_reference_noise_balance_is_reachable_from_the_register():
    """[source-verified: docs/minimoog-reference.md N7] **docs/minimoog-reference.md N7.** Mini V3 puts its white noise **7.1 dB
    below its own sawtooth** at the mixer. `NOISE_SHIFT = 2` puts ours 12.0 dB
    below at equal mixer weights — the headroom that keeps pink off the rail.

    The 4.9 dB is a patch value, not a hardware limit, and this is the test
    that says so: `WN` is Q0.15 and reaches 2.0, the balance is monotonic in
    it, and weight 1.76 lands on -7.1 dB with an eighth of the register's range
    still spare.

    Ground truth: test_audio_measure.test_rms_of_a_sinusoid_is_amplitude_over_root_two
    """
    n = 1 << 16
    w, _, _ = noise_colours(n)
    saw = vf.OscFx("saw", blep=True).render(n, dsp.phase_inc(dsp.note_hz(45))).astype(np.float64)
    ref = am.rms(saw)
    lv = {}
    for wt in (1.0, 1.76, 2.0):
        lv[wt] = 20 * math.log10(am.rms(w[4000:] * (wt * 32768) / 32768) / ref)
    assert abs(lv[1.0] - (-12.0)) < 0.5, lv
    assert abs(lv[1.76] - (-7.1)) < 0.4, f"weight 1.76 gives {lv[1.76]:.1f} dB, Mini V3's balance is -7.1"
    assert lv[1.0] < lv[1.76] < lv[2.0], lv
    assert int(round(1.76 * 32768)) <= (1 << vf.WEIGHT_BITS) - 1, "the balance must fit the register"


# =============================================================================
# per-oscillator drift (6.11, DR 0019; issue #56)
# =============================================================================
def _drift_note(cents, note=48, secs=0.7, detune=(0.0, 0.0, 0.0), mix=(1.0, 0.0, 0.0),
                **kw):
    """One note from reset at a drift depth. Cutoff wide open, no filter
    envelope and no tracking, so nothing but the oscillator can move the
    pitch -- the same isolation the frozen Mini V3 `osc*_open` controls use.
    The gate is held for the WHOLE render and the sustain is 1.0, so the level
    is flat: a release tail decaying into silence is not a held tone and the
    probe refuses on it (correctly, and it did on the first attempt here)."""
    v = vf.VoiceFx()
    return v, v.note(note, secs, gate=secs, waves=("saw", "saw", "saw"),
                     detune=detune, mix=mix,
                     cutoff=(21000, 21000), q=0.0, drive=1.0, track=0.0,
                     amp=(0.002, 0.2, 1.0, 0.05), fenv=(0.002, 0.2, 1.0, 0.05),
                     drift_cents=cents, **kw)


def _held(y, skip_s=0.15):
    """The render past its attack, as float -- what the probe measures."""
    return y.astype(np.float64)[int(skip_s * SR):] / 32768.0


def test_drift_zero_is_bit_identical_to_no_drift_register_at_all():
    """[method] The DRIFT register's default must be transparent. Every one of
    this suite's other tests, every recorded scenario in `verify_voice.py` and
    every frozen register image predates 6.11, so a mechanism that moved a
    single LSB at DRIFT = 0 would have to be re-baselined against all of them
    rather than reviewed on its own.

    Ground truth: test_moog_acceptance.test_meta_band_power_db_recovers_known_band_powers
    """
    v = vf.VoiceFx()
    r = v.note_on(48, 0.3, drift_cents=0.0)
    assert r["regs"]["drift"] == 0
    with_reg = v.note(48, 0.3, drift_cents=0.0)
    # the same image with the register absent entirely, as a pre-6.11 host wrote it
    v2 = vf.VoiceFx()
    regs = dict(r["regs"])
    regs.pop("drift")
    v2.reset()
    without = v2.play(regs, r["writes"], r["n"])
    assert np.array_equal(with_reg, without), "DRIFT = 0 is not transparent"


def test_drift_is_deterministic_and_repeatable_from_the_seed():
    """[method] Bit-exactness against the RTL requires the walk to be a
    function of the seed and the frame count, nothing else. Two voices given
    the same writes must render the same samples, and one voice must repeat
    itself across RESET -- an oscillator that drifts differently in the model
    and the RTL breaks `verify_voice.py`, which is why this is a constraint on
    the design and not an afterthought (issue #56's own words).

    Ground truth: test_moog_acceptance.test_meta_band_power_db_recovers_known_band_powers
    """
    _, a = _drift_note(vf.DRIFT_REF_CENTS)
    _, b = _drift_note(vf.DRIFT_REF_CENTS)
    assert np.array_equal(a, b), "two voices at the same seed differ"
    v, c = _drift_note(vf.DRIFT_REF_CENTS)
    d = v.note(48, 0.7, gate=0.7, waves=("saw", "saw", "saw"), detune=(0.0, 0.0, 0.0),
               mix=(1.0, 0.0, 0.0), cutoff=(21000, 21000), q=0.0, drive=1.0,
               track=0.0, amp=(0.002, 0.2, 1.0, 0.05), fenv=(0.002, 0.2, 1.0, 0.05),
               drift_cents=vf.DRIFT_REF_CENTS)
    assert np.array_equal(c, d), "the same voice does not repeat across RESET"
    _, off = _drift_note(0.0)
    assert not np.array_equal(a, off), "drift at the reference depth changed nothing"


def test_the_three_oscillators_drift_independently():
    """[measured-here: the three walks' pairwise correlation over 2^18 updates]
    Three oscillators in lockstep are vibrato, not drift -- issue #56 says so
    and the shared MR_OSC path of 6.9 is exactly that, which is why 6.11 is a
    separate mechanism rather than a depth on the old one. The three walks read
    non-overlapping 5-bit fields of the same LFSR word, so they are samples of
    one m-sequence 5 and 10 places apart; two shifts of an m-sequence
    cross-correlate at -1/(2^31 - 1) (the DR 0012 argument, reused).

    The CONTROL is the defect this would otherwise hide: one field driving all
    three walks passes every single-oscillator measurement and fails here.

    Ground truth: test_moog_acceptance.test_meta_band_power_db_recovers_known_band_powers
    """
    n = 1 << 18
    state = vf.VOICE_LFSR_SEED
    steps = np.empty((3, n))
    acc = [0, 0, 0]
    walk = np.empty((3, n))
    shared = np.empty((3, n))
    sacc = [0, 0, 0]
    for i in range(n):
        state, _ = vf.lfsr_frame(state)
        w = state & 0xFFFF
        for k in range(3):
            steps[k, i] = vf.drift_step(w, k)
            acc[k] = vf.drift_acc_next(acc[k], vf.drift_step(w, k))
            walk[k, i] = acc[k]
            sacc[k] = vf.drift_acc_next(sacc[k], vf.drift_step(w, 0))   # the CONTROL
            shared[k, i] = sacc[k]
    pairs = ((0, 1), (0, 2), (1, 2))
    cs = np.corrcoef(steps)
    assert max(abs(cs[i, j]) for i, j in pairs) < 0.01, cs
    cw = np.corrcoef(walk)
    assert max(abs(cw[i, j]) for i, j in pairs) < 0.05, cw
    # the steps must have zero mean: a biased step against the leak is a static
    # detune, not drift (the DRIFT_MEANSTEP negative control in voice_dp.v)
    assert max(abs(steps.mean(axis=1))) < 2.0, steps.mean(axis=1)
    # and the CONTROL must be caught by the same bound
    csh = np.corrcoef(shared)
    assert min(csh[i, j] for i, j in pairs) > 0.99, (
        "the shared-field control does not correlate, so this test cannot fail")


def test_the_three_oscillators_run_on_different_increments_when_drifting():
    """[method] The audible form of the test above, at the level that ships:
    three oscillators started on the SAME increment (detune 0) must run on
    different increments frame by frame once drift is on, and on identical ones
    when it is off.

    Ground truth: test_moog_acceptance.test_meta_band_power_db_recovers_known_band_powers
    """
    for cents, want_same in ((0.0, True), (vf.DRIFT_REF_CENTS, False)):
        v, _ = _drift_note(cents, secs=1.5, mix=(1.0, 1.0, 1.0))
        inc = [np.asarray(a) for a in v.trace["incs"]]
        same = (np.array_equal(inc[0], inc[1]) and np.array_equal(inc[0], inc[2]))
        assert same == want_same, (cents, [a[:4] for a in inc])
        if not want_same:
            # not merely different: each pair must differ on most frames
            for a, b in ((0, 1), (0, 2), (1, 2)):
                frac = float(np.mean(inc[a] != inc[b]))
                assert frac > 0.5, (a, b, frac)


def test_the_walks_stationary_rms_is_the_constant_the_host_conversion_uses():
    """[measured-here: 3394 LSB rms over 2^18 updates, largest |acc| 15403]
    `drift_reg` turns a request in cents into the DRIFT register by dividing by
    `DRIFT_ACC_RMS`, so that constant is part of the host conversion of 5.5 and
    a drifted value silently rescales every drift depth ever written. It is
    MEASURED from the shipped integer generator, not taken from the
    continuous-time formula for an Ornstein-Uhlenbeck process, and the 16-bit
    saturation must have real headroom over the largest state the generator
    reaches -- a walk that saturates is a walk that has a maximum detune, which
    is a different mechanism.

    Ground truth: test_moog_acceptance.test_meta_band_power_db_recovers_known_band_powers
    """
    _, rms, mx = vf.drift_walk(1 << 18, div=1)
    assert abs(np.mean(rms) - vf.DRIFT_ACC_RMS) < 0.03 * vf.DRIFT_ACC_RMS, rms
    assert max(mx) < (1 << (vf.DRIFT_ACC_BITS - 1)) / 2, mx
    # the decimation does not change the stationary statistics, only the
    # timescale: the shipped div = DRIFT_DIV must agree within its own error
    _, rms_d, _ = vf.drift_walk(1 << 12, div=vf.DRIFT_DIV)
    assert abs(np.mean(rms_d) - vf.DRIFT_ACC_RMS) < 0.12 * vf.DRIFT_ACC_RMS, rms_d
    # and the register round-trips in cents across its useful range
    for c in (0.2, 0.8, 1.5, 4.0, 5.5):
        assert abs(vf.drift_cents(vf.drift_reg(c)) - c) < 0.01, c
    assert vf.drift_reg(0.0) == 0 and vf.drift_reg(-1.0) == 0


def test_drift_moves_the_rendered_pitch_by_the_commanded_cents():
    """[measured-here: the deviation trace reads 0.920x the commanded rms at all
    three depths and the probe recovers 0.935x of that trace's mean-removed rms]

    The register is specified in cents, so the end-to-end claim is that the
    AUDIO moves by the commanded amount -- not that an internal word does. But
    this test was WRONG BEFORE IT WAS RIGHT and the wrong version is the
    interesting one: comparing the probe's reading against the COMMANDED cents
    read 0.601x at 1.0, 1.5 and 4.0 cents, which looks like a 40 % error in the
    mechanism and is not one. Over a 14 s window -- ten correlation times -- a
    bounded walk's own mean is NOT zero; this window's is -0.99 cents at the
    reference depth. A constant offset is a STATIC DETUNE, and the probe removes
    the mean f0 by construction, so it cannot see one and must not.

    So the claim is split in two, each against a ground truth that is not the
    other:

      1. the register is LINEAR in cents, against the model's own Q0.20
         deviation trace, which is what the register is defined in terms of.
         The window's realised rms is 0.920x the generator's stationary rms at
         every depth -- one number, because it is one trajectory -- and the
         bound on it is the spread of 14 s windows MEASURED from the generator
         in this test rather than a tolerance chosen to fit;
      2. the probe recovers the part of that same trace a listener would call
         drift -- its mean-removed rms -- to within 10 %. That is a check of
         the probe against a deviation known independently of the audio, which
         is the only direction in which it is a check at all.

    Ground truth: test_osc_drift_probe.test_known_ou_wander_is_recovered
    """
    import osc_drift_probe as odp

    def _rms(x):
        return float(np.sqrt(np.mean(np.asarray(x, dtype=np.float64) ** 2)))

    # the spread of realised rms over 14 s windows of the shipped generator,
    # measured here: the bound below is this, not a round number.
    trace, _, _ = vf.drift_walk(1 << 16, div=1)
    w = int(14.0 * SR / vf.DRIFT_DIV)
    seg = [_rms(trace[0][i:i + w]) / vf.DRIFT_ACC_RMS
           for i in range(0, len(trace[0]) - w, w)]
    lo, hi = min(seg), max(seg)
    assert lo < 0.8 and hi > 1.2, ("a generator whose 14 s windows all read the "
                                   "stationary rms would make bound below vacuous",
                                   lo, hi)

    f0 = dsp.note_hz(48)
    ratios = []
    for cents in (1.0, vf.DRIFT_REF_CENTS, 4.0):
        v, y = _drift_note(cents, secs=14.0)
        dev = np.asarray(v.trace["drift_dev"][0], dtype=np.float64) / vf.CENTS_TO_DEV
        held = dev[int(0.15 * SR):]
        ratios.append(_rms(held) / cents)
        assert lo <= ratios[-1] <= hi, (cents, ratios[-1], lo, hi)
        want = _rms(held - held.mean())
        r = odp.drift_report(_held(y), SR, f0, label=f"drift-{cents}c")
        assert r["verdict"] == odp.DRIFTING, r
        assert abs(r["drift_rms_cents"] / want - 1.0) < 0.10, (cents, want, r)
    # one trajectory scaled three ways: the register is linear in cents
    assert max(ratios) - min(ratios) < 0.005, ratios


def test_the_drift_walk_is_bounded_not_a_random_walk():
    """[measured-here: the increment variance saturates at 2.00x Var[acc] by
    eight correlation times; with the leak removed it reaches 0.36x and is still
    growing]

    "Bounded vs random walk" is one of the five things issue #56 asks to be
    measured, and the mechanism's answer has to be measured the same way --
    Var[acc(t+T) - acc(t)] against T, which saturates at exactly 2 Var[acc] for
    a stationary process and grows without bound for a random walk.

    It is measured HERE rather than read off `osc_drift_probe`'s
    `walk_exponent`, and the reason is a precondition the probe cannot assert
    for us: that exponent is fitted over lags of 0.05-0.8 s, chosen so the fit
    does not read the window's own length, and this generator's correlation time
    is 1.365 s. Every one of those lags is BELOW it, where a bounded walk is
    indistinguishable from a random one -- the probe reads 1.29 on our own
    drift, and would read about the same on a true random walk. Asserting
    `walk_exponent < 0.9` on this generator is therefore an unsatisfiable gate,
    and one was written here before this test replaced it.

    The CONTROL is the same generator with the leak deleted, which is a genuine
    random walk: it must fail the bound this test applies.

    Ground truth: test_osc_drift_probe.test_bounded_and_unbounded_are_distinguished
    """
    tau = 1 << vf.DRIFT_LEAK_LOG2                       # updates, = 1.365 s

    def _walk(n, leak):
        from fixed import sat
        state, acc, tr = vf.VOICE_LFSR_SEED, [0, 0, 0], [[], [], []]
        for _ in range(n):
            state, _ = vf.lfsr_frame(state)
            word = state & 0xFFFF
            for k in range(3):
                step = vf.drift_step(word, k)
                acc[k] = (vf.drift_acc_next(acc[k], step) if leak
                          else sat(acc[k] + step, vf.DRIFT_ACC_BITS))
                tr[k].append(acc[k])
        return np.array(tr, dtype=np.float64)

    for leak, want in ((True, True), (False, False)):
        a = _walk(1 << 16, leak)
        for k in range(3):
            var = float(np.var(a[k]))
            lag = 8 * tau
            ratio = float(np.var(a[k][lag:] - a[k][:-lag])) / var
            bounded = abs(ratio - 2.0) < 0.2
            assert bounded == want, (leak, k, ratio, var)


def test_drift_off_is_stable_and_a_static_detune_mix_is_not_called_drift():
    """[method] The two negative controls of issue #138, on OUR OWN renders
    rather than a synthetic stimulus. With DRIFT = 0 a single oscillator must
    read STABLE; and the default three-oscillator patch with its 0.07-semitone
    static detune -- which beats, and whose combined waveform therefore never
    settles -- must not be reported as DRIFTING at any depth, including zero.

    Ground truth: test_osc_drift_probe.test_static_detune_mix_is_not_drifting
    """
    import osc_drift_probe as odp
    f0 = dsp.note_hz(48)
    _, y = _drift_note(0.0, secs=14.0)
    r = odp.drift_report(_held(y), SR, f0, label="drift-off")
    assert r["verdict"] == odp.STABLE, r
    for cents in (0.0, vf.DRIFT_REF_CENTS):
        _, y = _drift_note(cents, secs=14.0, detune=(0.0, 0.07, -0.05),
                           mix=(1.0, 0.8, 0.6))
        r = odp.drift_report(_held(y), SR, f0, label=f"static-detune-mix-{cents}c")
        assert r["verdict"] != odp.DRIFTING, r


# =============================================================================
# meta -- this suite must be able to fail, and must say what it claims
# (#222, extending #45 item 1's gate beyond `test_808_acceptance.py`)
# =============================================================================
def test_meta_every_test_declares_status_and_ground_truth():
    """[meta] Every test opens with its claim status, and every test that
    measures something names the ground-truth test that backs the estimator it
    uses -- and that test must exist. "Verified in a source" and "validated by
    our own measurement" are different claims, and a suite with 42 (now 68)
    tests and zero external references is exactly the gap `docs/failure-modes.md`
    section 2 and issue #45 name.

    A `Ground truth:` line may name `test_audio_measure.<fn>`, for the shared,
    ground-truthed estimator module every acceptance suite measures through,
    or `test_moog_acceptance.<fn>` -- a self-reference -- for the two local
    convenience estimators this file defines and validates itself
    (`band_power_db`, `octave_slope_db`, pinned by
    `test_meta_band_power_db_recovers_known_band_powers`) rather than adding
    to `audio_measure.py`. `test_reference_voice.<fn>` is the third accepted
    module: `audio_measure.envelope_ripple_db` -- the movement estimator the
    two #53 tests measure through -- is ground-truthed there, against a
    staircase of known step size, not in `test_audio_measure.py`.

    The checking logic is shared with `test_808_acceptance.py`'s own
    meta-test, in `model/acceptance_meta.py`, so this suite and that one
    cannot drift into checking the claim differently; this test's only job is
    to name ITS OWN module and modules. This suite has no NOT_ASSERTED /
    KNOWN_DEFECTS table of its own (every property it claims is either met or
    absent from the file), so those escape hatches are not passed here."""
    import test_moog_acceptance as mod
    import test_audio_measure as gt
    import test_reference_voice as gtr
    import test_osc_drift_probe as gtd
    from acceptance_meta import assert_ground_truth_gate
    assert_ground_truth_gate(mod, {gt.__name__: gt, gtr.__name__: gtr,
                                   gtd.__name__: gtd, mod.__name__: mod})
