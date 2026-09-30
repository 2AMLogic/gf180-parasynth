#!/usr/bin/env python3
"""`reference_rigs.ModelDPedalboardRig` end to end, over a `pedalboard` host
this test controls -- including both branches of issue #124's sequencing gate.

    python3 -m pytest model/test_modeld_pedalboard_rig.py -q

**The shipping rig is what runs.** Nothing here reimplements a rig method:
`setup`, `voice_patch`, `calibrate_range`, `trim_level`, `written_pin_problems`,
`qualify` and `render` are the ones that would drive a real Model D. What is
faked is the HOST -- a `pedalboard` module whose `load_plugin` returns a
parameter table and a synthesiser this test can put into any state, which is
the one thing a real Model D cannot be made to do on demand. Same pattern as
`tools/test_refprofile_post_render.py`.

The fake's DEFAULT state is the Model D defect as `refprofile/README.md`
records it, and that is the point of the file:

    MIDI 60 commanded (261.63 Hz), sounding 130.81 Hz   -- an octave down
    8.57 % of samples at the rail                       -- clipped
    peak 1.000

So the default case is not a happy path. It is the rig being handed the
documented defect and asked to correct it through the plugin's own parameters,
which is exactly what issue #124 gates the rest of its scope on.

WHAT EACH CASE ESTABLISHES
--------------------------
    the gate PASSES            a Range position sounds the commanded note and
                               a master-volume position clears the rail, so the
                               rig qualifies and both sweeps are on the record
    the gate REFUSES (octave)  no Range position sounds the note: the rig
                               refuses and the sweep table is the evidence
    the gate REFUSES (level)   no master position clears the rail: same
    #137, both halves          a host whose note number does nothing, and one
                               whose cutoff does nothing, are each refused
                               while every static check still passes
    the pin readback           a parameter whose write does not take, and one
                               whose text readback lags its raw value (the Diva
                               cutoff that read 90 for a session), are refused
    the host preconditions     an effect rather than an instrument, a missing
                               bundle, duplicate parameter indices and a gapped
                               index range are each refused before any audio
"""
from __future__ import annotations

import math
import pathlib
import sys
import types

import numpy as np
import pytest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "audition"))

import audio_measure as am                                          # noqa: E402
import reference_rigs as rr                                         # noqa: E402
import rig_qualification as rq                                      # noqa: E402
import voice_fx as vf                                               # noqa: E402

SR = 48000
N_PARAMS = 54                     # Model D's highest index in `ModelDRig.I` is 53

#: Osc 1 Range, as the Model D panel has it, and the octave each position
#: sounds relative to the commanded note. The DEFAULT is position 2, which is
#: the octave-down defect on record.
RANGE_LABELS = ("LO", "32'", "16'", "8'", "4'", "2'")
RANGE_MULTIPLIER = (0.125, 0.25, 0.5, 1.0, 2.0, 4.0)
RANGE_DEFAULT_POS = 2

#: The gain the fake applies, solved so the DEFAULT patch -- master 0.80,
#: Osc 1 Volume 0.90, and the octave-DOWN Range position -- puts 8.57 % of
#: samples at the rail with a peak of 1.000: the two figures
#: `refprofile/README.md` records for this plugin under pedalboard. Solved
#: against `clipped_fraction` through the fake's own signal chain (filter
#: included), not picked. The octave matters: the same gain at the CORRECTED
#: 261.63 Hz clips less, because a saw an octave lower carries twice as many
#: harmonics and a sharper peak. So 8.57 % is a property of the UNCORRECTED
#: patch, which is exactly where the measurement on record was taken.
OUTPUT_GAIN = 1.798391


# ===========================================================================
# the fake host
# ===========================================================================
class _FakeParam:
    """One parameter, with the four things `_PedalboardParams` reads: `index`,
    `name`, `raw_value` (settable) and `string_value`, plus the
    `get_text_for_raw_value` the rig's own readback check uses.

    `stale_text` reproduces the defect that check exists for: the text readback
    lags the raw value, which is how a Diva cutoff appeared stuck at 90 for a
    whole session."""

    def __init__(self, index, name, *, steps=0, sticky=False, stale_text=False):
        self.index, self.name = int(index), str(name)
        self.num_steps, self.is_discrete = int(steps), bool(steps)
        self.sticky, self.stale_text = bool(sticky), bool(stale_text)
        self._raw = 0.0
        self._shown = 0.0

    @property
    def raw_value(self):
        return self._raw

    @raw_value.setter
    def raw_value(self, v):
        if self.sticky:                       # the write does not take
            return
        v = min(1.0, max(0.0, float(v)))
        if self.is_discrete and self.num_steps > 1:
            n = self.num_steps - 1
            v = round(v * n) / n              # a real discrete control snaps
        self._raw = v
        if not self.stale_text:
            self._shown = v

    @property
    def string_value(self):
        return f"{self._shown:.2f}"

    def get_text_for_raw_value(self, raw_value, maximum_string_length=512):
        return f"{float(raw_value):.2f}"


class _FakeModelD:
    """A Model D-shaped instrument: one oscillator, a one-pole low-pass on the
    cutoff knob, a master gain, and a hard rail at +-1.0.

    It is NOT a Minimoog model and makes no claim to be. It reproduces the four
    behaviours the rig's qualification asks about -- pitch from the note number
    times the Range multiplier, level from the master gain, brightness from the
    cutoff knob, and clipping at the rail -- so that each of them can be broken
    one at a time."""

    is_instrument = True
    reported_latency_samples = 0

    def __init__(self, *, ranges=RANGE_MULTIPLIER, pitch_dead=False,
                 cutoff_dead=False, output_gain=OUTPUT_GAIN, always_silent=False,
                 sticky=(), stale_text=(), duplicate_index=False, gap_index=False,
                 is_instrument=True):
        self.ranges = tuple(ranges)
        self.pitch_dead, self.cutoff_dead = pitch_dead, cutoff_dead
        self.output_gain, self.always_silent = float(output_gain), bool(always_silent)
        self.is_instrument = bool(is_instrument)
        # Both tables, because they do not overlap: `ModelDRig.NAMES` names the
        # controls the rig drives and `ModelDRig.PINS` names eight more (5-8,
        # 29-31, 53) that it only holds. A fake built from NAMES alone makes the
        # shipping rig refuse on those eight, which is the rig being right.
        names = dict(rr.ModelDRig.NAMES)
        names.update({i: n for i, _v, n, _w in rr.ModelDRig.PINS if n})
        ps = []
        for i in range(N_PARAMS):
            ps.append(_FakeParam(
                i, names.get(i, f"param {i}"),
                steps=len(self.ranges) if i == rr.ModelDRig.I['o1_range'] else 0,
                sticky=i in sticky, stale_text=i in stale_text))
        if duplicate_index:
            ps[7].index = ps[6].index
        if gap_index:
            ps[7].index = N_PARAMS + 5
        self._parameters = ps
        # The plugin's own default patch: the octave-down Range position.
        ps[rr.ModelDRig.I['o1_range']].raw_value = RANGE_DEFAULT_POS / (len(self.ranges) - 1)
        self._cache: dict = {}

    # -- parameter reads the synth itself needs ----------------------------
    def _raw(self, key):
        return self._parameters[rr.ModelDRig.I[key]].raw_value

    def _saw(self, f0: float, n: int) -> np.ndarray:
        """A band-limited saw, peak-normalised, cached. Band-limited because an
        aliased one does not repeat at its own f0 and every periodic estimator
        in the battery would refuse it -- which would be the fake's defect
        being measured, not the rig's."""
        key = (round(f0, 6), n)
        if key not in self._cache:
            t = np.arange(n) / SR
            y = np.zeros(n)
            for k in range(1, 400):
                if k * f0 >= SR / 2:
                    break
                y += np.sin(2 * math.pi * k * f0 * t) / k
            self._cache[key] = y / max(float(np.abs(y).max()), 1e-30)
        return self._cache[key]

    def __call__(self, midi_messages, duration, sample_rate, num_channels=2,
                 buffer_size=8192, reset=True):
        assert int(sample_rate) == SR, "the rig must pin the sample rate"
        assert int(buffer_size) == rr.BLOCK, (
            f"the rig must pin the host block size; got {buffer_size}. "
            f"pedalboard's own default is 8192, a 5.86 Hz chunk rate")
        n = int(float(duration) * SR)
        notes = [m[0][1] for m in midi_messages if m[0][0] & 0xF0 == 0x90]
        if self.always_silent or not notes:
            return np.zeros((2, n), dtype=np.float32)
        if not (self._raw('o1_on') > 0.5 and self._raw('o1_vol') > 0.0):
            return np.zeros((2, n), dtype=np.float32)
        pos = int(round(self._raw('o1_range') * (len(self.ranges) - 1)))
        f0 = vf.note_hz(60 if self.pitch_dead else int(notes[0])) * self.ranges[pos]
        y = self._saw(f0, n)
        knob = 1.0 if self.cutoff_dead else self._raw('cutoff')
        fc = 100.0 * 100.0 ** float(knob)
        if fc < 0.45 * SR:
            # NOT renormalised afterwards. A real low-pass loses level as it
            # closes, and renormalising would hand the level check a signal the
            # filter setting could not produce -- the trim's whole job is to
            # find the master volume that suits the patch as it actually is.
            a = math.exp(-2 * math.pi * fc / SR)
            from scipy.signal import lfilter
            y = lfilter([1 - a], [1.0, -a], y)
        y = self.output_gain * self._raw('master') * self._raw('o1_vol') * y
        y = np.clip(y, -1.0, 1.0)
        return np.repeat(y[None, :].astype(np.float32), 2, axis=0)


def install(monkeypatch, tmp_path, **kw) -> type:
    """Install a fake `pedalboard` module and return a `ModelDPedalboardRig`
    subclass whose bundle path exists. Everything else about the rig -- its
    pins, its patch, its calibrations, its battery -- is the shipping class."""
    fake = _FakeModelD(**kw)
    mod = types.ModuleType("pedalboard")
    mod.load_plugin = lambda path, **_kw: fake
    monkeypatch.setitem(sys.modules, "pedalboard", mod)
    bundle = tmp_path / "Model D.vst3"
    bundle.mkdir(parents=True, exist_ok=True)

    class Rig(rr.ModelDPedalboardRig):
        path = str(bundle)
    Rig.fake = fake
    return Rig


def by_name(q) -> dict:
    return {c.name: c for c in q.checks}


# ===========================================================================
# 1. the sequencing gate: it PASSES, and what it had to correct to do so
# ===========================================================================
def test_the_default_patch_really_is_the_defect_on_record(monkeypatch, tmp_path):
    """Before anything is claimed about the rig: the fake, in its default
    state, reproduces both numbers `refprofile/README.md` records for this
    plugin under pedalboard. A test whose apparatus does not reproduce the
    defect proves nothing about the fix."""
    fake = _FakeModelD()
    for k, v in (('o1_on', 1.0), ('o1_vol', 0.9), ('cutoff', 1.0), ('master', 0.8)):
        fake._parameters[rr.ModelDRig.I[k]].raw_value = v
    y = np.asarray(fake([([0x90, 60, 100], 0.02)], 0.7, SR, buffer_size=rr.BLOCK),
                   dtype=np.float64)[0]
    assert float(np.abs(y).max()) == pytest.approx(1.0, abs=1e-6)
    assert am.clipped_fraction(y, 1.0) == pytest.approx(0.0857, abs=5e-4)
    f = am.dominant_frequency(y, 50.0, 4000.0)
    assert f.ok and f.value == pytest.approx(vf.note_hz(60) / 2, rel=2e-3)


def test_the_rig_corrects_both_defects_and_qualifies(monkeypatch, tmp_path):
    """Issue #124's gate, pass branch. The rig is handed the octave-down
    clipped default and has to find its way out through Model D's own
    parameters; if it does, every check in the battery must then pass."""
    dev = install(monkeypatch, tmp_path)()
    q = dev.qualification
    assert q.qualified, q.table()
    assert q.verdict == "qualified"
    got = by_name(q)
    assert set(got) == {"pins", "pins written (raw readback)", "osc range calibration",
                        "level trim", "sounding", "level", "pitch", "waveform",
                        "pitch causality", "filter causality"}
    assert all(c.ok for c in q.checks), q.table()


def test_the_range_sweep_measures_the_octave_rather_than_looking_it_up(
        monkeypatch, tmp_path):
    """The correction has to be a MEASUREMENT. The record must show the whole
    sweep, the position the default was at, and the fundamental measured at the
    position chosen -- not a constant borrowed from Mini V3."""
    dev = install(monkeypatch, tmp_path)()
    cal = by_name(dev.qualification)["osc range calibration"].detail
    assert len(cal["sweep"]) == len(rr.ModelDPedalboardRig.RANGE_GRID)
    assert cal["commanded_note"] == 60
    assert cal["commanded_hz"] == pytest.approx(vf.note_hz(60))
    # The default was the octave-down position and the rig did not keep it.
    assert cal["default_was_correct"] is False
    assert cal["default_raw"] == pytest.approx(RANGE_DEFAULT_POS / 5)
    # The table records what the plugin HELD at each step, not what was written
    # to it: the Range control is discrete and snaps, so a 25-point grid visits
    # only the six positions that exist.
    assert len({round(r["raw_held"], 6) for r in cal["sweep"]}) == len(RANGE_MULTIPLIER)
    assert cal["selected_raw_held"] == pytest.approx(cal["selected"]["raw_held"])
    sel = cal["selected"]
    assert sel["f0_hz"] == pytest.approx(vf.note_hz(60), rel=2e-3)
    assert abs(sel["cents"]) < rr.ModelDPedalboardRig.RANGE_MAX_CENTS
    # ...and the rejected positions are on the record too, which is what makes
    # a refusal in the next test a finding rather than an assertion.
    assert sum(1 for r in cal["sweep"] if not r["ok"]) > 0
    assert all(("f0_hz" in r and "why" in r) for r in cal["sweep"])


def test_the_range_sweep_is_not_fooled_by_the_octave_down_second_harmonic(
        monkeypatch, tmp_path):
    """The octave-down position puts its SECOND harmonic exactly on the
    commanded note. A sweep that accepted "there is energy at 261.63 Hz" would
    select it and report success. Every rejected row's reason is on the record,
    and the octave-down row's reason must be the subharmonic."""
    dev = install(monkeypatch, tmp_path)()
    sweep = by_name(dev.qualification)["osc range calibration"].detail["sweep"]
    down = [r for r in sweep
            if abs(r["raw"] - RANGE_DEFAULT_POS / 5) < 0.02 and not r["ok"]]
    assert down, "the octave-down position was not rejected"
    assert any("fundamental is below the commanded" in (r["why"] or "") for r in down)


def test_the_level_trim_takes_the_loudest_clean_setting(monkeypatch, tmp_path):
    """Loudest-that-is-clean, not quietest-that-is-safe: a reference frozen 20
    dB down carries 20 dB less of what it is a reference for. So the chosen
    position must be the FIRST on the grid (which is ordered loudest first)
    that has zero samples at the rail, and every louder one must have been
    tried and rejected."""
    dev = install(monkeypatch, tmp_path)()
    trim = by_name(dev.qualification)["level trim"].detail
    grid, rows = trim["grid"], trim["sweep"]
    assert [r["raw"] for r in rows] == grid[:len(rows)]
    assert rows[-1] is not None and rows[-1]["outcome"] == rq.PASS
    assert all(r["outcome"] == rq.FAIL for r in rows[:-1])
    assert all((r["clipped_fraction"] or 0.0) > 0.0 for r in rows[:-1])
    sel = trim["selected"]
    assert sel["clipped_fraction"] == 0.0
    assert rq.PEAK_MIN <= sel["peak"] <= rq.PEAK_MAX
    assert trim["clipped_max"] == 0.0
    # The trim starts from the patch's own master volume, not from somewhere
    # convenient, and that first row is at the rail.
    assert rows[0]["raw"] == rr.ModelDPedalboardRig.MASTER_START
    assert rows[0]["clipped_fraction"] > 0.0
    assert rows[0]["peak"] == pytest.approx(1.0, abs=1e-6)


def test_the_qualified_rig_is_left_at_the_settings_it_was_qualified_at(
        monkeypatch, tmp_path):
    """A calibration that is measured and then not applied is worse than none.
    After construction the plugin must be holding the selected Range and the
    selected master volume, and a fresh render must still qualify."""
    dev = install(monkeypatch, tmp_path)()
    got = by_name(dev.qualification)
    assert dev.p.get_parameter(dev.I['o1_range']) == pytest.approx(
        got["osc range calibration"].detail["selected"]["raw_held"], abs=1e-9)
    assert dev.p.get_parameter(dev.I['master']) == pytest.approx(
        got["level trim"].detail["selected"]["raw_held"], abs=1e-9)
    y = dev.render_note(dev.note)
    assert rq.check_level(y).outcome == rq.PASS
    assert rq.check_pitch(dev.steady(y), vf.note_hz(dev.note)).outcome == rq.PASS


# ===========================================================================
# 2. the sequencing gate: it REFUSES, which is a complete outcome
# ===========================================================================
def test_an_uncorrectable_octave_refuses_and_the_sweep_is_the_evidence(
        monkeypatch, tmp_path):
    """Issue #124's gate, failure branch, and the whole reason the gate is
    there: if no Range position sounds the commanded note, the rig REFUSES and
    the sweep table says which positions were tried. It does not keep the
    octave-down default and call it the Mono reference."""
    Rig = install(monkeypatch, tmp_path, ranges=(0.125, 0.25, 0.5, 0.5, 2.0, 4.0))
    with pytest.raises(rq.RigRefusal) as e:
        Rig()
    q = e.value.qualification
    assert q is not None, "a refusal with no record is a refusal nobody can act on"
    cal = by_name(q)["osc range calibration"]
    assert cal.outcome == rq.FAIL
    assert "NOT correctable through this plugin's own parameters" in cal.why
    assert len(cal.detail["sweep"]) == len(Rig.RANGE_GRID)
    assert all(not r["ok"] for r in cal.detail["sweep"])
    assert "selected" not in cal.detail


def test_uncorrectable_clipping_refuses_and_the_sweep_is_the_evidence(
        monkeypatch, tmp_path):
    """The other half of the same gate: a plugin whose quietest master-volume
    setting still puts samples at the rail cannot produce a reference, and the
    grid that was tried is on the record."""
    Rig = install(monkeypatch, tmp_path, output_gain=400.0)
    with pytest.raises(rq.RigRefusal) as e:
        Rig()
    trim = by_name(e.value.qualification)["level trim"]
    assert trim.outcome == rq.FAIL
    assert "NOT correctable through this plugin's own parameters" in trim.why
    assert len(trim.detail["sweep"]) == len(Rig.MASTER_GRID)
    assert all(r["outcome"] == rq.FAIL for r in trim.detail["sweep"])


def test_a_refusal_still_measures_everything_it_can(monkeypatch, tmp_path):
    """A refusal is a record, not an early exit. Even with the octave
    uncorrectable, the battery must have run and the checks that CAN answer
    must have answered -- otherwise the finding is "it refused" and nobody
    knows whether the filter knob works either."""
    Rig = install(monkeypatch, tmp_path, ranges=(0.125, 0.25, 0.5, 0.5, 2.0, 4.0))
    with pytest.raises(rq.RigRefusal) as e:
        Rig()
    got = by_name(e.value.qualification)
    assert len(got) == 10
    assert got["pins"].outcome == rq.PASS
    assert got["pitch"].outcome == rq.FAIL
    assert got["filter causality"].outcome == rq.PASS
    assert got["pitch causality"].outcome == rq.PASS


def test_a_host_that_renders_only_silence_refuses(monkeypatch, tmp_path):
    """The dawdreamer Model D case, moved to this host: exact silence is a
    REFUSAL and never a pass, and it is not reported as a failure of the
    plugin."""
    Rig = install(monkeypatch, tmp_path, always_silent=True)
    with pytest.raises(rq.RigRefusal) as e:
        Rig()
    got = by_name(e.value.qualification)
    assert e.value.qualification.verdict == rq.REFUSED
    assert got["sounding"].outcome == rq.REFUSED
    assert got["sounding"].detail["peak"] == 0.0


# ===========================================================================
# 3. issue #137, both halves, on the shipping rig
# ===========================================================================
def test_a_host_whose_note_number_does_nothing_is_refused(monkeypatch, tmp_path):
    """The command is issued, the plugin sounds, and the pitch does not follow.
    Every static check passes. Only the causality check sees it -- which is the
    whole of #137."""
    Rig = install(monkeypatch, tmp_path, pitch_dead=True)
    with pytest.raises(rq.RigRefusal) as e:
        Rig()
    got = by_name(e.value.qualification)
    assert got["pitch causality"].outcome == rq.FAIL
    assert got["pitch causality"].detail["ratio"] == pytest.approx(1.0, rel=2e-3)
    for other in ("pins", "sounding", "level", "pitch", "waveform"):
        assert got[other].outcome == rq.PASS, other


def test_a_host_whose_cutoff_does_nothing_is_refused(monkeypatch, tmp_path):
    """The same for the filter. The knob is written, the plugin reads it back,
    and the spectrum does not move."""
    Rig = install(monkeypatch, tmp_path, cutoff_dead=True)
    with pytest.raises(rq.RigRefusal) as e:
        Rig()
    got = by_name(e.value.qualification)
    assert got["filter causality"].outcome == rq.FAIL
    assert got["filter causality"].detail["centroid_ratio"] == pytest.approx(1.0, abs=1e-6)
    assert got["pitch causality"].outcome == rq.PASS


# ===========================================================================
# 4. the pin readback this host can assert
# ===========================================================================
def test_a_pin_whose_write_does_not_take_is_refused(monkeypatch, tmp_path):
    """The written-pin check is not a duplicate of the name check: index 52
    ('Key Hold') keeps its NAME and keeps its default, and only a raw readback
    can see that the rig's write went nowhere."""
    Rig = install(monkeypatch, tmp_path, sticky=(3,))    # 3 is 'Tune', pinned to 0.5
    with pytest.raises(rq.RigRefusal) as e:
        Rig()
    got = by_name(e.value.qualification)
    assert got["pins"].outcome == rq.PASS, "the NAME check cannot see this"
    bad = got["pins written (raw readback)"]
    assert bad.outcome == rq.FAIL
    assert [b for b in bad.detail["bad"] if b[0] == 3 and b[1] == "RAW"]


def test_a_text_readback_that_lags_the_raw_value_is_refused(monkeypatch, tmp_path):
    """The Diva cutoff that read 90 for a whole session. The raw value is
    right, the name is right, and the plugin's two readback routes disagree
    with each other -- which is a refusal here and not a footnote."""
    Rig = install(monkeypatch, tmp_path, stale_text=(3,))
    with pytest.raises(rq.RigRefusal) as e:
        Rig()
    bad = by_name(e.value.qualification)["pins written (raw readback)"]
    assert bad.outcome == rq.FAIL
    assert [b for b in bad.detail["bad"] if b[0] == 3 and b[1] == "TEXT"]


def test_the_pins_are_the_dawdreamer_rigs_indices_and_names_with_no_readback(
        monkeypatch, tmp_path):
    """The pin table is derived from `ModelDRig.PINS`, not retyped, and its
    readback column is deliberately empty -- a readback STRING is the host's
    rendering and copying one across hosts would assert a measurement nobody
    took here. The gap is asserted so it cannot be quietly filled with a guess:
    what fills it is a measurement, and the observed strings are recorded for
    that purpose."""
    assert [(i, v, n) for i, v, n, _w in rr.ModelDPedalboardRig.PINS] == \
           [(i, v, n) for i, v, n, _w in rr.ModelDRig.PINS]
    assert all(w is None for *_x, w in rr.ModelDPedalboardRig.PINS)
    dev = install(monkeypatch, tmp_path)()
    report = dev.pinned_report()
    assert len(report) == len(rr.ModelDPedalboardRig.PINS)
    assert all(isinstance(v, str) for v in report.values())


# ===========================================================================
# 5. host preconditions, asserted before any audio
# ===========================================================================
def test_an_effect_plugin_is_refused_rather_than_handed_midi(monkeypatch, tmp_path):
    Rig = install(monkeypatch, tmp_path, is_instrument=False)
    with pytest.raises(rq.RigRefusal, match="not an instrument"):
        Rig()


def test_a_missing_bundle_is_refused_before_the_host_is_asked(monkeypatch, tmp_path):
    Rig = install(monkeypatch, tmp_path)
    Rig.path = str(tmp_path / "not here.vst3")
    with pytest.raises(rq.RigRefusal, match="no plugin bundle"):
        Rig()


def test_duplicate_parameter_indices_are_refused(monkeypatch, tmp_path):
    """An index-keyed pin table is meaningless if two parameters claim the same
    index: every pin at that index becomes a coin toss."""
    Rig = install(monkeypatch, tmp_path, duplicate_index=True)
    with pytest.raises(RuntimeError, match="report index"):
        Rig()


def test_a_gapped_parameter_index_range_is_refused(monkeypatch, tmp_path):
    """A host that hides some parameters renumbers the rest, and a pin table
    measured under the other host would then be pinning different controls with
    no error anywhere."""
    Rig = install(monkeypatch, tmp_path, gap_index=True)
    with pytest.raises(RuntimeError, match="indices are not"):
        Rig()


def test_the_rig_pins_the_block_size_and_the_sample_rate(monkeypatch, tmp_path):
    """The fake asserts both on every call (pedalboard's own default buffer is
    8192, a 5.86 Hz chunk rate), so a rig that left either to the host would
    fail every case in this file. Stated as its own test because the 94 Hz
    artefact came from exactly this."""
    dev = install(monkeypatch, tmp_path)()
    assert dev.block == rr.BLOCK == 512
    assert dev.sr == SR == 48000
    assert dev.render_note(60).size > 0


def test_a_known_stimulus_handed_to_this_host_is_refused_not_discarded(
        monkeypatch, tmp_path):
    """A pedalboard instrument has no audio input. A measurement that thinks it
    put a known signal through the plugin's filter and did not is the bench
    that drove the wrong port, so a non-zero stimulus refuses."""
    dev = install(monkeypatch, tmp_path)()
    with pytest.raises(rq.RigRefusal, match="no audio input"):
        dev.render(np.ones(100), 0.1)
    assert dev.render(np.zeros(100), 0.1).size > 0


def test_the_swept_cutoff_is_not_answerable_under_this_host(monkeypatch, tmp_path):
    """pedalboard has no parameter automation. Stitching a sweep out of
    per-block renders would measure the stitching, so the rig says the
    measurement is not answerable instead of producing one."""
    dev = install(monkeypatch, tmp_path)()
    with pytest.raises(NotImplementedError, match="no parameter automation"):
        dev.swept_cutoff(100.0, 200.0, 2000.0, 1.0)


def test_the_rig_states_its_licence_position_rather_than_implying_one(
        monkeypatch, tmp_path):
    """There is no licence probe for any plugin here; the one licence finding
    in this repository was an unlicensed Diva found by HEARING its clicks. So
    'unverified' is the measured position and the rig has to say so, in a field
    the environment tuple can record (#123)."""
    dev = install(monkeypatch, tmp_path)()
    assert dev.licence["state"] == "unverified"
    assert "docs/reference-integrity.md" in dev.licence["how"]


def test_the_two_hosts_are_separate_rigs_with_separate_names(monkeypatch, tmp_path):
    """#123's host-scoping finding, as a property of the code: the pedalboard
    Model D is not the dawdreamer Model D under a flag. It has its own rig
    name, its own host and its own note -- MIDI 60, the note the pedalboard
    measurement was taken at, not the 48 the dawdreamer rig uses."""
    assert rr.ModelDPedalboardRig.name == "modeld-pedalboard"
    assert rr.ModelDRig.name == "modeld"
    assert rr.ModelDPedalboardRig.kind == rr.ModelDRig.kind == "modeld"
    assert rr.ModelDPedalboardRig.host == "pedalboard"
    assert rr.ModelDPedalboardRig.note == 60 and rr.ModelDRig.note == 48


def test_the_pedalboard_base_inherits_the_pin_discipline_rather_than_copying_it():
    """"A sibling, not a fork" as an assertion. The pin methods must be the
    SAME function objects as `_Plugin`'s, so there is one implementation of the
    name-and-readback rule and not two that can drift apart."""
    for meth in ("set", "text", "apply_pins", "pinned_report", "check_pins",
                 "check_names"):
        assert getattr(rr._PedalboardPlugin, meth) is getattr(rr._Plugin, meth), meth
    # ...and exactly the host-shaped methods are overridden.
    for meth in ("__init__", "render", "silence_state", "qualify"):
        assert getattr(rr._PedalboardPlugin, meth) is not getattr(rr._Plugin, meth), meth
    # The Model D patch itself is the dawdreamer rig's, taken by reference.
    assert rr.ModelDPedalboardRig._base_setup is rr.ModelDRig.setup
