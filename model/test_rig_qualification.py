#!/usr/bin/env python3
"""Ground truth for `model/rig_qualification.py`, and the injected-defect
discrimination matrix that says the battery is not a pile of overlapping
checks.

    python3 -m pytest model/test_rig_qualification.py -q

**No plugin and no host anywhere in this file.** Every signal is synthesised
from a closed form, so each check is validated against an answer known
independently of anything in this repository -- a band-limited saw's
fundamental is the number it was built at, a one-pole's centroid rises with its
corner, a hard-clipped signal's fraction at the rail is countable by hand.

TWO KINDS OF TEST, AND THE SECOND IS THE ONE THAT MATTERS
---------------------------------------------------------
1. **each check on a signal whose answer is known.** Necessary, and cheap to
   pass.
2. **the discrimination matrix.** One deliberately-wrong rig per defect (the
   `DEFECTS` table is the list; no count is written here, so it cannot go
   stale), and for each one the test asserts BOTH which checks fire AND which
   do not. A check count is not evidence of coverage; a check that fires on
   everything is worse than absent, because it stops distinguishing.

The matrix carries two pairs that look redundant and are not, and it is
asserted that they are not:

  * a rig an octave down doubles CORRECTLY on +12, so
    `check_pitch_causality` PASSES on it and only `check_pitch` refuses it
  * a rig whose pitch command does nothing plays the right note at the base,
    so `check_pitch` PASSES on it and only `check_pitch_causality` refuses it

Started red: on an earlier draft the waveform check was handed the COMMANDED
fundamental instead of the measured one, and the octave-down row then reported
a period-residual complaint from `waveform_id` instead of the octave -- the
right verdict for the wrong reason, which is the failure mode this file exists
to catch.
"""
from __future__ import annotations

import math
import pathlib
import sys

import numpy as np
import pytest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "audition"))

import audio_measure as am                                          # noqa: E402
import rig_qualification as rq                                      # noqa: E402

SR = 48000
NOTE = 60
NOTE_HZ = 261.6255653005986          # MIDI 60, equal temperament, A4 = 440


def note_hz(note: int) -> float:
    return 440.0 * 2.0 ** ((int(note) - 69) / 12.0)


def ideal(shape: str, f0: float, n: int, sr: int = SR, duty: float = 0.25,
          kmax: int = 200) -> np.ndarray:
    """A band-limited ideal waveform from its Fourier series: no aliasing by
    construction. The same generator `model/test_reference_voice.py` uses, so
    the two files agree about what a saw is."""
    t = np.arange(n) / sr
    y = np.zeros(n)
    for k in range(1, kmax + 1):
        if k * f0 >= sr / 2:
            break
        if shape == "saw":
            y += np.sin(2 * math.pi * k * f0 * t) / k
        elif shape == "square":
            if k % 2:
                y += np.sin(2 * math.pi * k * f0 * t) / k
        elif shape == "pulse":
            y += math.sin(math.pi * k * duty) * np.cos(2 * math.pi * k * f0 * t) / k
        else:
            raise ValueError(shape)
    return y


def one_pole(x, fc: float, sr: int = SR) -> np.ndarray:
    """A one-pole low-pass at a known corner, in closed form. Its -3 dB point
    is `fc` by construction, which is what makes it usable as the independent
    answer for a centroid measurement."""
    a = math.exp(-2 * math.pi * fc / sr)
    y = np.zeros(len(x))
    s = 0.0
    for i, v in enumerate(x):
        s = a * s + (1 - a) * v
        y[i] = s
    return y


def unit(shape="saw", f0=NOTE_HZ, seconds=0.5, amp=0.4) -> np.ndarray:
    y = ideal(shape, f0, int(seconds * SR))
    return amp * y / max(float(np.abs(y).max()), 1e-30)


# ===========================================================================
# 1. each check against an independently known answer
# ===========================================================================
def test_sounding_refuses_exact_silence_and_does_not_call_it_a_failure():
    """Model D under dawdreamer renders a buffer whose peak is exactly 0.0.
    That is an absence of evidence about the plugin, not evidence that the
    plugin is wrong, and the two are different outcomes here."""
    c = rq.check_sounding(np.zeros(SR))
    assert c.outcome == rq.REFUSED
    assert c.detail["peak"] == 0.0


def test_sounding_refuses_non_finite_audio_before_any_threshold_sees_it():
    """NaN and Inf compare False against every threshold, so a silence test
    alone passes them -- `refprofile.load_clip` had all-NaN audio pass every
    content check it owned for exactly this reason."""
    for fill in (np.nan, np.inf, -np.inf):
        c = rq.check_sounding(np.full(1000, fill))
        assert c.outcome == rq.REFUSED, fill
        assert c.detail["non_finite"] == 1000


def test_sounding_passes_a_signal_at_the_known_amplitude():
    c = rq.check_sounding(unit(amp=0.4))
    assert c.outcome == rq.PASS
    assert c.detail["peak"] == pytest.approx(0.4, abs=1e-9)


def test_the_silence_floor_is_the_one_this_module_documents():
    """The prior-art corpus generator gates on 1e-6 and this gates on 1e-9.
    The divergence is deliberate (see the module docstring) and a signal
    between the two must be SOUNDING here, or the two floors have been
    conflated."""
    assert rq.SILENCE_FLOOR == 1e-9
    tiny = 1e-7 * np.sin(2 * math.pi * 1000 * np.arange(SR) / SR)
    assert rq.check_sounding(tiny).outcome == rq.PASS
    # ...and NOT usable, which is the other check's job and not this one's.
    assert rq.check_level(tiny).outcome == rq.FAIL


def test_level_counts_the_fraction_at_the_rail_against_a_counted_case():
    """Ground truth by counting, not by an integral: 300 samples at the rail
    and 700 below it is 0.300 and nothing about the estimator can round it.

    A sine "clipped for the fraction of its period where |sin| >= 0.5" is NOT
    that ground truth and was the first version of this test -- the continuous
    answer is 2/3, the SAMPLED answer at 96 samples per period is 66/96 =
    0.6875 because both boundary samples land at the rail, and the test failed
    against its own arithmetic. The sampled case is kept below, with the
    sampled number, because the discrepancy is the estimator being right."""
    x = np.concatenate([np.full(300, 1.0), np.full(700, 0.5)])
    c = rq.check_level(x)
    assert c.outcome == rq.FAIL
    assert c.detail["clipped_fraction"] == pytest.approx(0.300, abs=1e-12)
    assert "not a reference" in c.why


def test_level_counts_the_rail_on_a_sampled_sine_at_its_sampled_answer():
    """A 500 Hz sine at amplitude 2.0, clipped at 1.0, at 48 kHz: 96 samples
    per period, |2 sin(2*pi*k/96)| >= 1 for k = 8..40 and its mirror, so 66 of
    every 96 samples are at the rail. 0.6875, not the continuous 2/3."""
    x = np.clip(2.0 * np.sin(2 * math.pi * 500 * np.arange(SR) / SR), -1.0, 1.0)
    assert rq.check_level(x).detail["clipped_fraction"] == pytest.approx(0.6875, abs=1e-3)


#: The pre-clip gain on a band-limited saw that puts EXACTLY the Model D figure
#: -- 8.57 % of samples at the rail (`refprofile/README.md`) -- at full scale.
#: Solved by bisection against `clipped_fraction`, not chosen: the number that
#: matters is the 8.57 %, and the gain is whatever produces it.
CLIP_GAIN_8_57 = 1.298251


def test_level_fails_a_clipped_signal_and_passes_the_same_signal_trimmed():
    """The Model D case in one test: the clipped patch is refused as a
    reference and the trimmed one is accepted, with nothing else changed. This
    is `ModelDPedalboardRig.trim_level`'s whole job, on a signal whose clipped
    fraction is the one on record."""
    loud = np.clip(CLIP_GAIN_8_57 * unit(amp=1.0), -1.0, 1.0)
    bad = rq.check_level(loud)
    assert bad.outcome == rq.FAIL
    assert bad.detail["clipped_fraction"] == pytest.approx(0.0857, abs=5e-4)
    assert rq.check_level(0.5 * loud).outcome == rq.PASS


def test_level_fails_a_signal_below_the_stated_peak_window():
    c = rq.check_level(unit(amp=0.001))
    assert c.outcome == rq.FAIL and c.detail["peak"] == pytest.approx(0.001)
    assert c.detail["peak_window"] == [rq.PEAK_MIN, rq.PEAK_MAX]


def test_pitch_passes_the_note_it_was_built_at():
    c = rq.check_pitch(unit(), NOTE_HZ)
    assert c.outcome == rq.PASS
    assert c.detail["f0_hz"] == pytest.approx(NOTE_HZ, rel=1e-4)


def test_pitch_fails_an_octave_down_and_is_not_fooled_by_its_second_harmonic():
    """Model D's own defect: a saw an octave below the commanded note puts its
    SECOND harmonic exactly on that note, so a search around the command locks
    onto it and reports no error. The check must answer FAIL -- it measured a
    fundamental, and the fundamental is the wrong one -- and not REFUSED."""
    c = rq.check_pitch(unit(f0=NOTE_HZ / 2), NOTE_HZ)
    assert c.outcome == rq.FAIL
    assert "below the commanded" in c.why


def test_pitch_fails_a_note_a_semitone_out_and_passes_a_five_cent_mistuning():
    """The window is 50 cents, so the check must be tight enough to refuse a
    wrong note and loose enough not to refuse an analogue-modelled one: Mini V3
    plays +0.14 cents sharp."""
    assert rq.check_pitch(unit(f0=NOTE_HZ * 2 ** (1 / 12)), NOTE_HZ).outcome == rq.FAIL
    assert rq.check_pitch(unit(f0=NOTE_HZ * 2 ** (5 / 1200)), NOTE_HZ).outcome == rq.PASS


def test_pitch_refuses_rather_than_fails_when_there_is_nothing_to_measure():
    c = rq.check_pitch(np.zeros(SR), NOTE_HZ)
    assert c.outcome == rq.REFUSED


def test_waveform_names_a_saw_a_saw_and_a_pulse_a_pulse_with_its_duty():
    assert rq.check_waveform(unit("saw"), NOTE_HZ).detail["label"] == "saw"
    c = rq.check_waveform(unit("pulse", amp=0.4), NOTE_HZ)
    assert c.outcome == rq.PASS and c.detail["label"].startswith("pulse:")
    assert c.detail["duty"] == pytest.approx(0.25, abs=0.02)


def test_waveform_fails_when_a_different_family_was_requested():
    """The `SurgeRig` defect, as a test: asked for a saw, handed a pulse."""
    c = rq.check_waveform(unit("pulse"), NOTE_HZ, expect="saw")
    assert c.outcome == rq.FAIL and "pulse" in c.why


def test_waveform_with_no_expectation_still_requires_identifiability():
    """`expect=None` is "whatever it is, name it" -- not "anything passes".
    Noise has no period and must REFUSE."""
    rng = np.random.default_rng(7)
    c = rq.check_waveform(0.4 * rng.standard_normal(SR // 2), NOTE_HZ)
    assert c.outcome == rq.REFUSED


def test_pitch_causality_reads_exactly_two_on_a_rig_that_transposes():
    calls = []

    def render(note):
        calls.append(note)
        return unit(f0=note_hz(note))
    c = rq.check_pitch_causality(render, NOTE, note_hz)
    assert c.outcome == rq.PASS
    assert calls == [NOTE, NOTE + 12]
    assert c.detail["ratio"] == pytest.approx(2.0, rel=2e-3)


def test_pitch_causality_fails_a_rig_whose_pitch_command_does_nothing():
    """Issue #137's case. The rig sounds, at the right note, with the right
    waveform, at a sane level -- and its transpose command is not connected to
    anything. Only this check can say so."""
    c = rq.check_pitch_causality(lambda note: unit(f0=NOTE_HZ), NOTE, note_hz)
    assert c.outcome == rq.FAIL
    assert c.detail["ratio"] == pytest.approx(1.0, rel=2e-3)
    assert "did not cause the effect" in c.why


def test_pitch_causality_fails_a_rig_transposing_by_the_wrong_interval():
    """11 semitones is 100 cents out -- three times the tolerance, so a rig
    that is nearly right is still refused."""
    c = rq.check_pitch_causality(
        lambda note: unit(f0=NOTE_HZ * 2 ** ((note - NOTE) * 11 / 144)), NOTE, note_hz)
    assert c.outcome == rq.FAIL


def test_pitch_causality_still_passes_a_rig_an_octave_down_at_every_note():
    """**The pair that looks redundant and is not.** An octave-down rig
    doubles correctly, so this check PASSES it. `check_pitch` is the only one
    that refuses it, and dropping either check loses a real defect."""
    c = rq.check_pitch_causality(lambda note: unit(f0=note_hz(note) / 2), NOTE, note_hz)
    assert c.outcome == rq.PASS
    assert c.detail["ratio"] == pytest.approx(2.0, rel=2e-3)
    assert rq.check_pitch(unit(f0=NOTE_HZ / 2), NOTE_HZ).outcome == rq.FAIL


def test_pitch_causality_refuses_a_silent_transposed_render():
    def render(note):
        return unit() if note == NOTE else np.zeros(SR // 2)
    c = rq.check_pitch_causality(render, NOTE, note_hz)
    assert c.outcome == rq.REFUSED


# --- the filter check, against a corner that is known independently -------
def _fc(knob: float) -> float:
    """A knob mapped onto a one-pole corner, exponentially: 0.0 -> 100 Hz,
    1.0 -> 10 kHz. The mapping is stated so the expected centroid movement is
    a property of the test and not of the estimator."""
    return 100.0 * 100.0 ** float(knob)


@pytest.mark.parametrize("shape,want_ratio", [("saw", 1.913), ("pulse", 1.767)])
def test_filter_causality_sees_a_one_poles_known_corners_move_monotonically(
        shape, want_ratio):
    """Ground truth independent of this repository: a one-pole at a higher
    corner passes more of the same source, so the spectral centroid of the
    output must rise. The source is fixed, so nothing but the corner moves.

    **These two numbers are where `CENTROID_MIN_RATIO` comes from, and they are
    pinned so it cannot drift back to a value nobody measured.** Over a 15.9x
    sweep of the corner the power-weighted centroid moves by 1.913 (saw) and
    1.767 (25 % pulse) -- far less than the corner does, because a saw's power
    falls as 1/k^2 and the centroid sits near the fundamental. The first
    version of the gate asked for 2.0 and was unsatisfiable on its own
    validation case."""
    src = unit(shape)
    c = rq.check_filter_causality(lambda k: one_pole(src, _fc(k)), (0.3, 0.5, 0.7, 0.9))
    assert c.outcome == rq.PASS, c.why
    cents = c.detail["centroid_hz"]
    assert cents == sorted(cents)
    assert c.detail["centroid_ratio"] == pytest.approx(want_ratio, abs=0.01)
    assert c.detail["centroid_ratio"] >= rq.CENTROID_MIN_RATIO


def test_the_centroid_gate_sits_between_the_two_states_it_separates():
    """The gate is satisfiable by the case it was derived from and is not
    satisfied by a disconnected knob. Both halves asserted, because a gate
    checked against only one of them is the unsatisfiable-gate failure."""
    assert 1.0 < rq.CENTROID_MIN_RATIO < 1.767


def test_filter_causality_fails_a_cutoff_knob_wired_to_nothing():
    """Issue #137 again, on the other control: the filter sounds, the knob
    moves, the spectrum does not."""
    src = unit("saw")
    c = rq.check_filter_causality(lambda k: one_pole(src, 2000.0), (0.3, 0.5, 0.7, 0.9))
    assert c.outcome == rq.FAIL
    assert c.detail["centroid_ratio"] == pytest.approx(1.0, abs=1e-6)
    assert "did not cause the effect" in c.why


def test_filter_causality_fails_a_knob_wired_backwards():
    """A large total movement in the WRONG direction. A check that only
    measured "how much did it move" would pass this."""
    src = unit("saw")
    c = rq.check_filter_causality(lambda k: one_pole(src, _fc(1.0 - k)),
                                 (0.3, 0.5, 0.7, 0.9))
    assert c.outcome == rq.FAIL
    assert "BACKWARDS" in c.why
    assert c.detail["backward_steps"]


def test_filter_causality_fails_a_knob_that_moves_too_little():
    """A knob connected to a range so narrow it cannot be the filter's: stated
    as a required factor, not as "it moved". The movement is real and
    monotonic, so the monotonic half of the check passes it and only the stated
    minimum refuses it."""
    src = unit("saw")
    c = rq.check_filter_causality(lambda k: one_pole(src, 2000.0 * (1.0 + 0.05 * k)),
                                 (0.3, 0.5, 0.7, 0.9))
    assert c.outcome == rq.FAIL
    assert 1.0 < c.detail["centroid_ratio"] < rq.CENTROID_MIN_RATIO
    assert not c.detail["backward_steps"]


def test_filter_causality_refuses_one_knob_position_that_renders_silence():
    src = unit("saw")
    c = rq.check_filter_causality(
        lambda k: (np.zeros(len(src)) if k > 0.6 else one_pole(src, _fc(k))),
        (0.3, 0.5, 0.7, 0.9))
    assert c.outcome == rq.REFUSED


def test_a_sweep_of_one_position_is_refused_rather_than_passed():
    assert rq.check_filter_causality(lambda k: unit(), (0.5,)).outcome == rq.REFUSED


# --- the pin check, lifted rather than reimplemented ----------------------
def test_pins_pass_fail_and_refuse_on_an_empty_table():
    assert rq.check_pins(lambda: [], n_pins=4).outcome == rq.PASS
    bad = rq.check_pins(lambda: [(265, "NAME", "Unison Voices", "High Cut")], n_pins=4)
    assert bad.outcome == rq.FAIL and "265" in bad.why
    # A rig that pins nothing has not been written down, and an empty check
    # reporting success is the exact shape of a false green.
    assert rq.check_pins(lambda: [], n_pins=0).outcome == rq.REFUSED


def test_a_pin_check_that_itself_raises_is_a_refusal_not_a_pass():
    def boom():
        raise RuntimeError("the host lost the parameter list")
    assert rq.check_pins(boom, n_pins=3).outcome == rq.REFUSED


# ===========================================================================
# 2. the verdict, and that an empty battery is not a pass
# ===========================================================================
def test_an_empty_battery_is_refused_and_never_qualified():
    q = rq.Qualification("nothing", "nowhere", ())
    assert q.verdict == rq.REFUSED and not q.qualified
    with pytest.raises(rq.RigRefusal):
        q.require()


def test_a_refusal_outranks_a_failure_in_the_verdict():
    """"we measured this and it is wrong" and "we could not measure it" are
    different claims, and the second one is not evidence about the rig."""
    q = rq.Qualification("r", "h", (rq.Check("a", rq.PASS, ""),
                                    rq.Check("b", rq.FAIL, "wrong"),
                                    rq.Check("c", rq.REFUSED, "no answer")))
    assert q.verdict == rq.REFUSED
    q2 = rq.Qualification("r", "h", (rq.Check("a", rq.PASS, ""),
                                     rq.Check("b", rq.FAIL, "wrong")))
    assert q2.verdict == rq.FAIL


def test_a_refusal_carries_the_record_it_came_from():
    """A refusal whose evidence is only in its message is a refusal nobody can
    act on -- and issue #124's whole failure branch is a refusal that has to be
    recorded as a finding."""
    q = rq.Qualification("r", "h", (rq.Check("level", rq.FAIL, "at the rail",
                                             {"clipped_fraction": 0.0857}),))
    with pytest.raises(rq.RigRefusal) as e:
        q.require()
    assert e.value.qualification is q
    assert e.value.qualification.as_dict()["checks"][0]["detail"]["clipped_fraction"] \
        == 0.0857


def test_the_record_serialises_to_json():
    import json
    q = rq.Qualification("r", "h", (rq.Check("a", rq.PASS, "", {"x": np.float64(1.5),
                                                               "y": np.arange(3)}),))
    d = json.loads(json.dumps(q.as_dict()))
    assert d["verdict"] == "qualified" and d["checks"][0]["detail"]["y"] == [0, 1, 2]


# ===========================================================================
# 3. THE DISCRIMINATION MATRIX
#
# One synthetic rig, one state per defect, and for each state the checks that
# must fire AND the checks that must not. `qualify_voice` -- the shipping battery -- is
# what runs; nothing here reimplements a check.
# ===========================================================================
class _SynthRig:
    """A rig with every defect this battery exists to catch, switchable one at
    a time. It is a closed-form signal generator, not a plugin stand-in: the
    point is that each defect's answer is known before the battery runs."""

    def __init__(self, *, octave=1.0, amp=0.4, clip=False, transposes=True,
                 cutoff="normal", shape="saw", silent=False, pins=()):
        self.octave, self.amp, self.clip = octave, amp, clip
        self.transposes, self.cutoff, self.shape = transposes, cutoff, shape
        self.silent, self.pins = silent, list(pins)
        self.n_pins = 6

    def _tone(self, note, fc=None):
        if self.silent:
            return np.zeros(int(0.5 * SR))
        f0 = note_hz(note if self.transposes else NOTE) * self.octave
        y = unit(self.shape, f0=f0, amp=1.0)
        if fc is not None:
            y = one_pole(y, fc)
            y = y / max(float(np.abs(y).max()), 1e-30)
        y = self.amp * y
        return np.clip(y, -1.0, 1.0) if self.clip else y

    def render_note(self, note):
        return self._tone(note)

    def render_cutoff(self, knob):
        fc = {"normal": _fc(knob), "dead": 2000.0,
              "inverted": _fc(1.0 - knob)}[self.cutoff]
        return self._tone(NOTE, fc)

    def check_pins(self):
        return self.pins

    def qualify(self):
        return rq.qualify_voice(
            rig="synth", host="none", render_note=self.render_note,
            render_cutoff=self.render_cutoff, note_hz=note_hz, note=NOTE,
            check_pins_fn=self.check_pins, n_pins=self.n_pins,
            expect_wave="saw", cutoff_knobs=(0.3, 0.5, 0.7, 0.9))


#: (label, rig kwargs, the checks that must NOT pass). Every other check in
#: the battery must PASS -- that half is the discrimination and it is asserted.
DEFECTS = [
    ("good", {}, set()),
    ("silent", dict(silent=True),
     {"sounding", "level", "pitch", "waveform", "pitch causality",
      "filter causality"}),
    # At the Model D figure -- 8.57 % of samples at the rail -- the record is
    # still named a saw and still plays the commanded note, so the LEVEL check
    # is the only thing between a clipped reference and the profile. Measured,
    # not assumed: see test_the_clipped_row_... below.
    ("clipped (8.57 % at the rail, the Model D figure)",
     dict(amp=CLIP_GAIN_8_57, clip=True), {"level"}),
    # Gross clipping is a second, independent refusal: the waveform check stops
    # being able to name it. Both rows are kept because only the first one is
    # the defect this repository actually has.
    ("clipped hard (41 % at the rail)", dict(amp=2.0, clip=True),
     {"level", "waveform"}),
    ("too quiet", dict(amp=0.001), {"level"}),
    ("octave down", dict(octave=0.5), {"pitch"}),
    ("wrong waveform", dict(shape="pulse"), {"waveform"}),
    ("pitch command dead", dict(transposes=False), {"pitch causality"}),
    ("cutoff knob dead", dict(cutoff="dead"), {"filter causality"}),
    ("cutoff knob backwards", dict(cutoff="inverted"), {"filter causality"}),
    ("a pin did not hold",
     dict(pins=[(265, "NAME", "A Osc 1 Unison Voices", "A Osc 1 High Cut")]),
     {"pins"}),
]


@pytest.mark.parametrize("label,kw,expect_bad", DEFECTS, ids=[d[0] for d in DEFECTS])
def test_the_discrimination_matrix(label, kw, expect_bad):
    """For each injected defect: exactly the named checks are non-PASS, and
    every other check in the battery PASSES.

    The second half is what makes this a matrix rather than a list. A battery
    where everything fires on everything has no diagnostic value, and a check
    that never fires has none either -- the union of these rows covers every
    check `qualify_voice` runs, which is asserted separately below."""
    q = _SynthRig(**kw).qualify()
    bad = {c.name for c in q.failures()}
    assert bad == expect_bad, q.table()
    assert q.qualified is (not expect_bad), q.table()


def test_the_octave_down_row_does_not_also_fail_the_causality_check():
    """Spelled out because it is the one result that looks like a bug. An
    octave-down rig transposes CORRECTLY, so the causality check passes and the
    matrix row names `pitch` alone. Both checks are needed and neither is
    redundant."""
    q = _SynthRig(octave=0.5).qualify()
    by = {c.name: c for c in q.checks}
    assert by["pitch"].outcome == rq.FAIL
    assert by["pitch causality"].outcome == rq.PASS
    assert by["pitch causality"].detail["ratio"] == pytest.approx(2.0, rel=2e-3)


def test_the_dead_pitch_command_row_does_not_also_fail_the_pitch_check():
    """The mirror image: at the base note the rig plays exactly what was asked
    for, so `check_pitch` passes and only the causality check sees the fault."""
    by = {c.name: c for c in _SynthRig(transposes=False).qualify().checks}
    assert by["pitch"].outcome == rq.PASS
    assert by["pitch causality"].outcome == rq.FAIL


def test_the_clipped_row_is_caught_by_the_level_check_and_by_nothing_else():
    """**The measurement that makes the level check load-bearing.** At the
    8.57 % the Model D default patch actually produces, the record is still
    named a saw, still plays the commanded note, still transposes and still
    sweeps. If the level check were dropped, that clip would pass every
    remaining check in the battery and be frozen as a reference.

    The threshold where the waveform check starts to catch it independently is
    measured in the row below: between 21.5 % and 40.8 % at the rail. Well
    above the defect we have."""
    by = {c.name: c for c in _SynthRig(amp=CLIP_GAIN_8_57, clip=True).qualify().checks}
    assert by["level"].outcome == rq.FAIL
    assert by["level"].detail["clipped_fraction"] == pytest.approx(0.0857, abs=5e-4)
    assert by["pitch"].outcome == rq.PASS
    assert by["waveform"].outcome == rq.PASS
    assert by["pitch causality"].outcome == rq.PASS
    assert by["filter causality"].outcome == rq.PASS


@pytest.mark.parametrize("gain,frac,wave", [
    (1.2000, 0.0264, rq.PASS),
    (1.5000, 0.2150, rq.PASS),
    (2.0000, 0.4080, rq.REFUSED),
])
def test_where_the_waveform_check_starts_to_see_clipping_on_its_own(gain, frac, wave):
    """Swept rather than argued about. The waveform check refuses somewhere
    between 21.5 % and 40.8 % of samples at the rail; the clipping this
    repository has is 8.57 %, which is why it needs the level check and not a
    tighter waveform tolerance."""
    y = np.clip(gain * unit(amp=1.0), -1.0, 1.0)
    assert am.clipped_fraction(y, 1.0) == pytest.approx(frac, abs=1e-3)
    assert rq.check_waveform(y, NOTE_HZ, expect="saw").outcome == wave


def test_every_check_the_battery_runs_is_exercised_by_some_row():
    """A defect matrix that leaves a check untested is a check nobody has ever
    seen fire. The union of the rows must cover the whole battery."""
    ran = {c.name for c in _SynthRig().qualify().checks}
    covered = set().union(*(bad for _l, _k, bad in DEFECTS))
    assert ran - covered == set(), f"never exercised: {sorted(ran - covered)}"


def test_the_battery_measures_causality_even_when_the_level_is_wrong():
    """Order is documented and the later checks are NOT conditional on the
    earlier ones: "the level is wrong AND the knob is disconnected" and "the
    level is wrong" send the next person to different places."""
    q = _SynthRig(amp=CLIP_GAIN_8_57, clip=True, cutoff="dead").qualify()
    assert {c.name for c in q.failures()} == {"level", "filter causality"}


def test_the_waveform_check_is_handed_the_measured_fundamental():
    """The bug this file was started red on. On an octave-down rig the battery
    must identify the waveform at the fundamental it MEASURED (130.81 Hz), not
    at the one that was commanded -- so the row's only complaint is the pitch,
    and the waveform is still named correctly."""
    by = {c.name: c for c in _SynthRig(octave=0.5).qualify().checks}
    assert by["waveform"].outcome == rq.PASS
    assert by["waveform"].detail["label"] == "saw"
    assert by["waveform"].detail["f0_measured_hz"] == pytest.approx(NOTE_HZ / 2, rel=1e-3)


# ===========================================================================
# the wiring (#137): `_Plugin.qualify()` itself runs the battery
#
# The tests above prove the checks. These prove the shipping base class CALLS
# them: a dawdreamer-hosted rig whose cutoff or pitch command is disconnected
# must REFUSE construction, with the record attached. No plugin or host is
# needed -- the fake supplies only the calls `_Plugin.qualify` makes on the
# engine, and its signals are the closed-form ones used above.
# ===========================================================================
import reference_rigs as rr                                         # noqa: E402


class _Stub:
    def __getattr__(self, _name):
        return lambda *a, **k: None


def _fake_rig(*, cutoff="normal", transposes=True, adapters=True):
    synth = _SynthRig(cutoff=cutoff, transposes=transposes)

    class Fake(rr._Plugin):
        name = "fake"
        have_input = False
        setups = 0

        def __init__(self):                 # no dawdreamer: skip the host
            self.p, self.eng, self.qualification = _Stub(), _Stub(), None
            self.setup()
            self.qualify()

        def setup(self):
            type(self).setups += 1

        if adapters:
            def render_note(self, note, seconds=None):
                return synth.render_note(note)

            def render_cutoff(self, knob, seconds=None):
                return synth.render_cutoff(knob)

    return Fake


def test_wiring_a_connected_rig_qualifies_and_carries_the_battery():
    rig = _fake_rig()()
    names = {c.name for c in rig.qualification.checks}
    assert rig.qualification.qualified
    assert {"pitch causality", "filter causality"} <= names


def test_wiring_a_disconnected_cutoff_refuses_construction_with_the_record():
    with pytest.raises(rq.RigRefusal, match="filter causality") as e:
        _fake_rig(cutoff="dead")()
    assert "pitch causality: " not in str(e.value)      # only the broken one


def test_wiring_a_dead_pitch_command_refuses_construction():
    with pytest.raises(rq.RigRefusal, match="pitch causality"):
        _fake_rig(transposes=False)()


def test_wiring_restores_the_measurement_patch_after_the_battery():
    cls = _fake_rig()
    cls()
    assert cls.setups == 2          # once to build, once after the battery


def test_wiring_a_rig_without_adapters_is_not_measured_rather_than_qualified():
    rig = _fake_rig(adapters=False)()
    assert rig.qualification is None
