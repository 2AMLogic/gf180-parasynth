#!/usr/bin/env python3
"""Both host boundaries in `reference_rigs` vouch for what they hand back:
finite, and non-empty once the plugin's reported latency is removed (#2106).

    python3 -m pytest model/test_reference_rigs_latency.py -q

**No plugin and no host anywhere in this file.** The SHIPPING boundary methods
run against stubs this file controls -- the pattern `test_nan_invariance.py`
uses for `_Plugin.render` and `test_modeld_pedalboard_rig.py` uses for the
whole pedalboard rig -- because the rigs cannot be constructed on a machine
with no plugins, and because the one thing a real plugin cannot be made to do
on demand is report a latency at or above the render length.

THE DEFECT, on record before the fix (origin/main @ 8d4adf6e)
--------------------------------------------------------------
A plugin reporting `lat >= len(y)` left an EMPTY slice after the trim. The
dawdreamer path had finite-checked the FULL buffer and then handed on the
empty remainder; the pedalboard path asserted nothing, before or after. An
empty buffer reads as silence downstream: `calibrate_range` recorded
`peak: 0.0` and the row said "the render is silent", and the sweep's verdict
was FAIL -- "NOT correctable through this plugin's own parameters" -- for what
is a host anomaly. Confident, specific, and wrong about the plugin.

WHAT EACH CASE ESTABLISHES
--------------------------
    dawdreamer, lat >= len       refused, an `InsufficientEvidence` (the family
                                 `require_finite` raises) in the boundary's own
                                 words with both numbers; never "0 frames"
    dawdreamer, 0 <= lat < len   the control: trimmed by exactly `lat`, and
                                 latency 0 returns the buffer untouched
    pedalboard, the same two     the other host, over a 2-D host buffer whose
                                 two channels differ, so channel 0 is proven
    non-finite AND lat >= len    finiteness is reported FIRST, on the full
                                 buffer; an empty trim never masks a NaN
    0 frames / negative latency  refused as what each is: the `lat >= len`
                                 guard alone would blame latency for an empty
                                 host buffer, and `y[-k:]` silently returns the
                                 END of the render
    the Range sweep              the boundary refuses before a row can collapse;
                                 and a trim that leaves too little for the
                                 steady window is recorded `peak: None` and
                                 `empty: True`, never `0.0`
    the battery                  a boundary refusal is a `Check(REFUSED)`
                                 carrying the boundary's words, not an
                                 exception out of `qualify_voice`
    the shipping rig, end to end `Rig()` raises `RigRefusal` with that record
                                 attached -- the outcome
                                 `qualify_modeld_pedalboard` already handles --
                                 and a NORMAL latency still qualifies
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

SR = rr.SR
SECONDS = 0.70
N = int(SECONDS * SR)
NOTE = 60
LATENCY_WORDS = "reported latency >= render length"


def _saw(f0: float, n: int, amp: float = 0.4) -> np.ndarray:
    """A band-limited saw at `f0`, peak-normalised -- band-limited because an
    aliased one does not repeat at its own f0 and `refine_f0` would refuse it,
    which would be the fake's defect being measured."""
    t = np.arange(n) / SR
    y = np.zeros(n)
    for k in range(1, 400):
        if k * f0 >= SR / 2:
            break
        y += np.sin(2 * math.pi * k * f0 * t) / k
    return (amp * y / float(np.abs(y).max())).astype(np.float32)


SAW = _saw(vf.note_hz(NOTE), N)


class _DawHost:
    """A `_Plugin`-shaped stub: the SHIPPING `_Plugin.render` runs against it.
    Channel 1 is the negation of channel 0, so a render that read the wrong
    one would show."""

    name, note, have_input, pb = "stub", NOTE, False, None

    def __init__(self, buf, latency):
        self._buf = np.asarray(buf, dtype=np.float32)
        self.p = types.SimpleNamespace(
            clear_midi=lambda: None, add_midi_note=lambda *a, **k: None,
            get_latency_samples=lambda: latency)
        self.eng = types.SimpleNamespace(
            render=lambda seconds: None,
            get_audio=lambda: np.vstack([self._buf, -self._buf]))


class _PbParams:
    """The reads `_PedalboardPlugin.render` and `ModelDPedalboardRig.
    calibrate_range` make of a parameter table, over one parameter, plus the
    latency the fake plugin reports."""

    def __init__(self, latency):
        self.latency, self.held, self.writes = int(latency), 0.0, []

    def set_parameter(self, i, v):
        self.writes.append(float(v))
        self.held = float(v)

    def get_parameter(self, i):
        return self.held

    def get_parameter_text(self, i):
        return f"{self.held:.2f}"

    def get_parameter_name(self, i):
        return "Osc 1 Range"

    def raw_tolerance(self, i):
        return 1e-6

    def get_latency_samples(self):
        return self.latency


class _PbRig(rr.ModelDPedalboardRig):
    """The SHIPPING pedalboard rig with its constructor replaced: every method
    that runs in these tests -- `render`, `render_note`, `steady`,
    `render_cutoff`, `calibrate_range` -- is the one that would drive a real
    Model D. What is faked is the HOST, which returns `buf` on both channels
    (channel 1 negated) and reports `latency`."""

    def __init__(self, buf, latency):
        self._buf = np.asarray(buf, dtype=np.float32)
        self.plugin = self._plugin
        self.p = _PbParams(latency)
        self.sr, self.block = SR, rr.BLOCK

    def _plugin(self, midi_messages, duration, sample_rate, num_channels=2,
                buffer_size=rr.BLOCK, reset=True):
        n = int(float(duration) * sample_rate)
        assert n == self._buf.size, "the fake renders exactly what it was given"
        return np.repeat(self._buf[None, :], 2, axis=0)


# ---------------------------------------------------------------------------
# 1. the dawdreamer boundary: `render_midi_note` through `_Plugin.render`
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("latency", [N, N + 1, 2 * N], ids=["equal", "one-more", "double"])
def test_dawdreamer_refuses_a_latency_at_or_above_the_render_length(latency):
    """The refusal exists, is the `require_finite` family so existing callers
    handle it uniformly, and names the anomaly rather than an empty buffer."""
    stub = _DawHost(SAW, latency)
    with pytest.raises(am.InsufficientEvidence, match=LATENCY_WORDS) as e:
        rr._Plugin.render(stub, None, SECONDS)
    why = str(e.value)
    assert f"{latency} samples" in why and f"{N} samples" in why
    assert "returned 0 frames" not in why and "empty buffer" not in why


@pytest.mark.parametrize("latency", [0, 1, 512, N - 1],
                         ids=["zero", "one", "one-block", "all-but-one"])
def test_dawdreamer_trims_exactly_the_reported_latency_below_the_render_length(latency):
    """The control: every latency the guard lets through is removed sample for
    sample, and a latency of 0 returns the host's buffer untouched."""
    out = rr._Plugin.render(_DawHost(SAW, latency), None, SECONDS)
    assert out.dtype == np.float64
    assert np.array_equal(out, SAW[latency:].astype(np.float64))


def test_dawdreamer_reports_a_non_finite_buffer_before_the_latency_anomaly():
    """A NaN and a fatal latency together: finiteness is what gets reported. The
    full buffer is checked before anything is trimmed, so the trim's emptiness
    cannot mask it."""
    buf = SAW.copy()
    buf[7] = np.nan
    with pytest.raises(am.NonFiniteAudio, match=r"\(1 NaN, 0 Inf; first at index 7\)"):
        rr._Plugin.render(_DawHost(buf, N), None, SECONDS)


def test_dawdreamer_refuses_a_host_that_returned_no_frames_as_exactly_that():
    """Zero frames with zero latency is not a latency anomaly, and must not be
    reported as one: the two have different causes."""
    with pytest.raises(am.InsufficientEvidence, match="returned 0 frames") as e:
        rr._Plugin.render(_DawHost(np.zeros(0, dtype=np.float32), 0), None, 0.0)
    assert LATENCY_WORDS not in str(e.value)


def test_dawdreamer_refuses_a_negative_latency_rather_than_reading_the_end():
    """`y[-k:]` is the LAST k samples: a buffer that passes `lat < len(y)`,
    is finite, and is wrong. The guard would be defeated by its own input."""
    with pytest.raises(am.InsufficientEvidence, match="NEGATIVE"):
        rr._Plugin.render(_DawHost(SAW, -512), None, SECONDS)


# ---------------------------------------------------------------------------
# 2. the pedalboard boundary: `_PedalboardPlugin.render`
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("latency", [N, N + 1, 2 * N], ids=["equal", "one-more", "double"])
def test_pedalboard_refuses_a_latency_at_or_above_the_render_length(latency):
    rig = _PbRig(SAW, latency)
    with pytest.raises(am.InsufficientEvidence, match=LATENCY_WORDS) as e:
        rig.render(np.zeros(1), SECONDS)
    why = str(e.value)
    assert f"{latency} samples" in why and f"{N} samples" in why
    assert "returned 0 frames" not in why and "empty buffer" not in why


@pytest.mark.parametrize("latency", [0, 1, 300, N - 1],
                         ids=["zero", "one", "partial-block", "all-but-one"])
def test_pedalboard_trims_exactly_the_reported_latency_below_the_render_length(latency):
    """The control, and the 2-D host buffer: channel 0, mono, trimmed."""
    out = _PbRig(SAW, latency).render(np.zeros(1), SECONDS)
    assert out.ndim == 1 and out.dtype == np.float64
    assert np.array_equal(out, SAW[latency:].astype(np.float64))


@pytest.mark.parametrize("fill", [np.nan, np.inf])
def test_pedalboard_refuses_a_non_finite_host_buffer(fill):
    """The pedalboard path asserted nothing. It now asserts what the
    dawdreamer path has asserted since #134."""
    buf = SAW.copy()
    buf[11] = fill
    with pytest.raises(am.NonFiniteAudio, match="non-finite"):
        _PbRig(buf, 0).render(np.zeros(1), SECONDS)


def test_pedalboard_reports_a_non_finite_buffer_before_the_latency_anomaly():
    buf = SAW.copy()
    buf[7] = np.nan
    with pytest.raises(am.NonFiniteAudio, match=r"\(1 NaN, 0 Inf; first at index 7\)"):
        _PbRig(buf, N).render(np.zeros(1), SECONDS)


def test_pedalboard_refuses_a_host_that_returned_no_frames_as_exactly_that():
    with pytest.raises(am.InsufficientEvidence, match="returned 0 frames") as e:
        _PbRig(np.zeros(0, dtype=np.float32), 0).render(np.zeros(1), 0.0)
    assert LATENCY_WORDS not in str(e.value)


def test_pedalboard_refuses_a_negative_latency_rather_than_reading_the_end():
    with pytest.raises(am.InsufficientEvidence, match="NEGATIVE"):
        _PbRig(SAW, -300).render(np.zeros(1), SECONDS)


# ---------------------------------------------------------------------------
# 3. the Range sweep's row: `calibrate_range`
# ---------------------------------------------------------------------------
def test_the_range_sweep_never_sees_a_render_the_boundary_refused():
    """On origin/main the sweep recorded `peak: 0.0` and "silent" for every
    position. The boundary refuses first now, and the sweep's own `finally`
    still puts the plugin back where it found it."""
    rig = _PbRig(SAW, N)
    rig.p.held = 0.5
    with pytest.raises(am.InsufficientEvidence, match=LATENCY_WORDS):
        rr.ModelDPedalboardRig.calibrate_range(rig)
    assert rig.p.writes[0] == 0.0, "the sweep began at the first grid position"
    assert rig.p.writes[-1] == 0.5 and rig.p.held == 0.5, "and put the plugin back"


def test_the_range_sweep_records_a_render_too_short_for_its_window_as_empty():
    """Defence in depth, for the one empty buffer the boundary cannot refuse:
    a latency just UNDER the render length leaves a non-empty trim that the
    steady cut then empties. The row says so. It does not say `0.0`."""
    c = rr.ModelDPedalboardRig.calibrate_range(_PbRig(SAW, N - 100))
    assert c.outcome == rq.FAIL
    assert c.detail["sweep"], "the sweep must still record every position"
    for row in c.detail["sweep"]:
        assert row["peak"] is None
        assert row["empty"] is True
        assert row["ok"] is False and row["f0_hz"] is None
        assert "EMPTY" in row["why"] and "silent" not in row["why"]


@pytest.mark.parametrize("buf, sounding", [(SAW, True), (np.zeros(N, dtype=np.float32), False)],
                         ids=["sounding", "silent"])
def test_the_range_sweep_rows_are_unchanged_for_a_non_empty_render(buf, sounding):
    """The control for the `:2048` branch: a non-empty render is measured
    exactly as before -- `peak` is a number, silence is still called silence --
    and the new flag is present and False."""
    c = rr.ModelDPedalboardRig.calibrate_range(_PbRig(buf, 0))
    assert c.detail["sweep"]
    for row in c.detail["sweep"]:
        assert row["empty"] is False
        assert isinstance(row["peak"], float)
        assert row["ok"] is sounding
        if sounding:
            assert row["peak"] > rq.SILENCE_FLOOR
            assert row["f0_hz"] == pytest.approx(vf.note_hz(NOTE), rel=2e-3)
        else:
            assert row["peak"] == 0.0
            assert row["why"] == "the render is silent at this Range position"
    assert c.outcome == (rq.PASS if sounding else rq.FAIL)


# ---------------------------------------------------------------------------
# 4. the battery: a refusal is a record, not an exception
# ---------------------------------------------------------------------------
BATTERY_CHECKS = ("sounding", "level", "pitch", "waveform", "pitch causality",
                  "filter causality")


def _battery(rig):
    return rq.qualify_voice(rig=rig.name, host="pedalboard", render_note=rig.render_note,
                            render_cutoff=rig.render_cutoff, note_hz=vf.note_hz,
                            note=NOTE, sr=SR, steady=rig.steady,
                            cutoff_knobs=rig.CUTOFF_KNOBS)


def test_the_battery_returns_a_refused_record_when_the_boundary_refuses():
    """`qualify_voice` does not raise. On origin/main it did not raise either,
    but every check said "the host returned an empty buffer" -- the wrong
    cause. Now each says what the boundary said."""
    q = _battery(_PbRig(SAW, N))
    assert q.verdict == rq.REFUSED
    got = {c.name: c for c in q.checks}
    assert tuple(got) == BATTERY_CHECKS
    for name, check in got.items():
        assert check.outcome == rq.REFUSED, name
        assert LATENCY_WORDS in check.why, name
        assert "empty buffer" not in check.why, name


def test_the_battery_returns_a_refused_record_when_the_buffer_is_non_finite():
    """The control for the finiteness assert below: a REFUSED record naming the
    NaN is what the battery gave before (through `check_sounding`) and must
    keep giving -- `require_finite` at the boundary now raises first, and the
    battery must turn that into the same record, not a traceback."""
    buf = SAW.copy()
    buf[7] = np.nan
    q = _battery(_PbRig(buf, 0))
    assert q.verdict == rq.REFUSED
    assert "finite" in {c.name: c for c in q.checks}["sounding"].why


def test_pitch_causality_refuses_rather_than_raises_when_the_render_refuses():
    c = rq.check_pitch_causality(_PbRig(SAW, N).render_note, NOTE, vf.note_hz)
    assert c.outcome == rq.REFUSED
    assert LATENCY_WORDS in c.why and c.detail["base"]["why"]


def test_filter_causality_refuses_rather_than_raises_when_the_render_refuses():
    c = rq.check_filter_causality(_PbRig(SAW, N).render_cutoff, (0.3, 0.5))
    assert c.outcome == rq.REFUSED
    assert LATENCY_WORDS in c.why and c.detail["sweep"][0]["why"]


def test_a_render_that_raises_something_else_is_not_swallowed_as_a_refusal():
    """The injected-bug control on the catch itself: a harness defect is a
    traceback, not a `Check(REFUSED)` that looks like evidence."""
    def broken(note):
        raise RuntimeError("host exploded")
    with pytest.raises(RuntimeError, match="host exploded"):
        rq.qualify_voice(rig="stub", host="pedalboard", render_note=broken,
                         render_cutoff=broken, note_hz=vf.note_hz, note=NOTE)


# ---------------------------------------------------------------------------
# 5. the shipping rig, end to end, over the fake host that file already has
# ---------------------------------------------------------------------------
def test_the_shipping_rig_refuses_to_be_built_with_a_record_not_a_traceback(
        monkeypatch, tmp_path):
    """The whole construction path, including the Range sweep and the level
    trim that run BEFORE the battery: `RigRefusal`, with the boundary's words
    on the record `qualify_modeld_pedalboard` prints."""
    import test_modeld_pedalboard_rig as t
    Rig = t.install(monkeypatch, tmp_path)
    Rig.fake.reported_latency_samples = 10 ** 6
    with pytest.raises(rq.RigRefusal) as e:
        Rig()
    q = e.value.qualification
    assert q is not None, "a refusal with no record is a refusal nobody can act on"
    assert q.verdict == rq.REFUSED
    got = t.by_name(q)
    assert got["render"].outcome == rq.REFUSED
    assert LATENCY_WORDS in got["render"].why
    assert got["pins"].outcome == rq.PASS


@pytest.mark.parametrize("latency", [0, 64], ids=["zero", "normal"])
def test_the_shipping_rig_still_qualifies_at_a_normal_latency(monkeypatch, tmp_path, latency):
    """The end-to-end control: nothing above changed for a plugin whose
    reported latency is shorter than its render."""
    import test_modeld_pedalboard_rig as t
    Rig = t.install(monkeypatch, tmp_path)
    Rig.fake.reported_latency_samples = latency
    dev = Rig()
    assert dev.qualification.qualified, dev.qualification.table()
    assert len(t.by_name(dev.qualification)) == 10
    assert len(dev.render_note(dev.note)) == N - latency
