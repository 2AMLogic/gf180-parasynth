"""Ground-truth tests for the M5A waveform-level envelope measurement.

THE `_render_segment` HANDLER (#1777), at the end of this file -- RECORDED,
NOT REPAIRED. It raises whatever is not its own `Refused` as
`Refused('Mini V3 render failed: ...')`, so a bug anywhere from the rig's
construction to the readbacks after the render is reported as a missing
precondition, exit 2: an execution error filed as a refusal. The repair is one
line -- catch `refusal.Refused`, `RuntimeError` and `AssertionError`, which is
the vocabulary `reference_rigs` refuses in, as `measure_mono_case._apparatus`
now does -- and it was written and measured against this file (9 passed; by
mutation, the base class alone turns the two `RuntimeError` controls and the
`RigRefusal` control red, and dropping `AssertionError` the `RigRefusal` one).
IT IS NOT IN THE TREE, because this reader is FROZEN: #1048 bound the Mini V3
records to these exact bytes, `model/test_ring_estimator_readers.py` pins the
file's digest, `tools/verify_attack_context_model.py` refuses any edit with
`analysis basis changed: tools/measure_mono_m5a_reference.py`, and
docs/scorecard/mono-attack-context/model/README.md says the pin does not move.
Whether that pin gives way is a maintainer's decision, not a Builder's.
So the negative control below is `xfail(strict=True)`: it is the defect,
stated executably, and it starts FAILING (as an unexpected pass) the moment
someone repairs the handler, which is when its marker must go. The positive
controls pass on today's tree and stay: they pin that the rig's own refusals
are this module's `Refused`, and they are half of the control pair the repair
will need.
No plugin anywhere: a stand-in rig answers the patch from the module's own
tables, and its reaching the body's LAST check is itself asserted.
WHAT THE CONTROLS DO NOT CLAIM: no plugin ran. The stand-in raises the
injected exception where the real rig would raise its own, so this exercises
the handler's class boundary and not a production path.
"""
from __future__ import annotations

import functools

import numpy as np
import pytest

from tools import measure_mono_m5a_reference as m
from tools.measure_mono_m5a_reference import CASES, envelope_timing
import rig_qualification as rq  # noqa: E402  (model/ is on sys.path once m is imported)


SR = 48_000


def test_m5a_reference_case_keeps_its_frozen_note_map_and_timing():
    case = CASES["M5A"]
    assert [event["note"] for event in case["events"]["saw"]] == [84, 96]
    assert case["segment_seconds"] == 13.5
    assert case["requires_waveform_at_every_note"] is False


def test_m5b_reference_is_a_lower_lead_with_a_complete_phrase_and_release():
    case = CASES["M5B"]
    assert [event["note"] for event in case["events"]["saw"]] == [72, 84]
    assert case["events"]["pulse"] == case["events"]["saw"]
    phrase_end = max(e["on_s"] + e["gate_s"] for e in case["events"]["saw"])
    assert 4.0 <= phrase_end <= 8.0
    assert case["segment_seconds"] > phrase_end
    assert case["segment_seconds"] - phrase_end >= 2.0
    assert abs(case["segment_seconds"] - phrase_end - 2.8) < 1e-9
    assert case["requires_waveform_at_every_note"] is True


def test_envelope_timing_matches_a_piecewise_linear_attack_and_exponential_release():
    on_s, off_s, attack_s, tau_s = 0.1, 0.7, 0.05, 0.1
    t = np.arange(int(2.0 * SR)) / SR
    env = np.zeros_like(t)
    attack = (t >= on_s) & (t < on_s + attack_s)
    env[attack] = (t[attack] - on_s) / attack_s
    sustain = (t >= on_s + attack_s) & (t < off_s)
    env[sustain] = 1.0
    release = t >= off_s
    env[release] = np.exp(-(t[release] - off_s) / tau_s)

    got = envelope_timing(env, SR, on_s, off_s)
    assert got["valid"]
    assert abs(got["attack_10_90_ms"] - 40.0) <= 1.0
    assert abs(got["release_t20_ms"] - tau_s * np.log(10) * 1000) <= 1.0
    assert got["release_complete_40db"]


def test_envelope_timing_refuses_silence_and_incomplete_release():
    silence = envelope_timing(np.zeros(1000), SR, 0.002, 0.010)
    assert silence == {"valid": False, "why": "silent during gate"}

    # A held signal with no post-gate decay cannot be reported as a release.
    held = np.ones(SR)
    got = envelope_timing(held, SR, 0.1, 0.5)
    assert not got["valid"]
    assert got["why"] == "20 dB release threshold not reached"


# ---------------------------------------------------------------------------
# #1777: `_render_segment`'s handler -- recorded, not repaired; the reader is
# frozen (#1048). See the module docstring. No plugin anywhere: a stand-in rig
# answers the patch from the module's own tables, and each control makes ONE
# statement raise.
# ---------------------------------------------------------------------------
class _Engine:
    """A render that returns silence, and calls `on_segment` on the second
    render -- the segment itself, after `_apply_patch`'s 0.05 s warm-up."""

    def __init__(self, on_segment=None):
        self.samples = 0
        self.renders = 0
        self.on_segment = on_segment

    def render(self, seconds):
        self.samples = int(seconds * m.SR)
        self.renders += 1
        if self.renders == 2 and self.on_segment is not None:
            self.on_segment()
        return True

    def get_audio(self):
        return [np.zeros(self.samples), np.zeros(self.samples)]


class _Processor:
    """Every patched index answers the name and the text the patch asks for,
    until `fail` is set; `rename` makes one index answer the wrong name."""

    def __init__(self, rename=None):
        self.fail = None
        self.names = {i: n for i, n, _v in m.PATCH.values()}
        self.names[m.WAVE_PARAM[0]] = m.WAVE_PARAM[1]
        if rename:
            self.names.update(rename)
        self.text = {i: (m.RANGE_READBACK if k == "osc1_range" else str(v))
                     for k, (i, _n, v) in m.PATCH.items()}
        self.text[m.WAVE_PARAM[0]] = m.WAVE_SETTINGS["saw"][1]

    def get_parameter_name(self, index):
        if self.fail is not None:
            raise self.fail
        return self.names[index]

    def get_parameter_text(self, index):
        if self.fail is not None:
            raise self.fail
        return self.text[index]

    def clear_midi(self):
        pass

    def add_midi_note(self, *_args):
        pass

    def get_latency_samples(self):
        return 0


class _Port:
    @staticmethod
    def set_data(_buffer):
        pass


class _Rig:
    """Enough of a `MiniV3Rig` for `_render_segment` to run to its last check:
    `fail_in_pins` raises from `check_pins`, `fail_after_render` from every
    parameter query after the segment has rendered, `rename` from `_readback`."""

    I = {k: i for i, k in enumerate(("lvl_o2", "lvl_o3", "lvl_noise", "o2", "o3",
                                     "noise_sw", "ext_sw", "lvl_ext"))}

    def __init__(self, block=None, fail_in_pins=None, fail_after_render=None, rename=None):
        self.p = _Processor(rename)
        self.eng = _Engine(lambda: setattr(self.p, "fail", fail_after_render))
        self.pb = _Port()
        self.fail_in_pins = fail_in_pins

    def set(self, _idx, _v):
        pass

    def check_pins(self):
        if self.fail_in_pins is not None:
            raise self.fail_in_pins
        return []


def _segment(**rig):
    return m._render_segment("saw", m.PATCH["amp_decay"][2], m.M5A_EVENTS, segment_seconds=0.2)


def test_the_stand_in_rig_reaches_the_end_of_the_render(monkeypatch):
    """The apparatus of the controls below, asserted: with nothing injected the
    real `_render_segment` runs the stand-in through the patch, the readbacks
    and the segment render, and stops at its own LAST check -- the in-band
    `Refused` for silent output, not at an earlier `TypeError` on a missing
    attribute."""
    monkeypatch.setattr(m.rr, "MiniV3Rig", _Rig)
    with pytest.raises(m.Refused, match="^plugin output is silent$"):
        _segment()


@pytest.mark.parametrize("where, exc", [
    pytest.param("construction", RuntimeError("mini-v3: pinned settings did not hold: [(265, 'NAME', ...)]"),
                 id="construction-RuntimeError"),
    pytest.param("construction", rq.RigRefusal("mini-v3 under dawdreamer: REFUSED -- pins: ..."),
                 id="construction-RigRefusal"),
    pytest.param("construction", m.refusal.Refused("injected"), id="construction-another-modules-Refused"),
    pytest.param("final readback", RuntimeError("the host lost the parameter list"),
                 id="final-readback-RuntimeError"),
])
def test_render_segment_still_refuses_on_the_rigs_own_refusals(monkeypatch, where, exc):
    """The positive control. `reference_rigs` refuses in its own vocabulary --
    `RuntimeError` for a pin that does not hold, `RigRefusal` from the battery,
    and whatever the host raises when it will not answer a parameter query --
    and each is still this module's `Refused`, with the cause chained."""
    if where == "construction":
        def ctor(**_kw):
            raise exc
        monkeypatch.setattr(m.rr, "MiniV3Rig", ctor)
    else:
        monkeypatch.setattr(m.rr, "MiniV3Rig", functools.partial(_Rig, fail_after_render=exc))
    with pytest.raises(m.Refused, match=r"^Mini V3 render failed: ") as e:
        _segment()
    assert e.value.__cause__ is exc


@pytest.mark.xfail(strict=True, raises=m.Refused, reason=(
    "#1777 site `measure_mono_m5a_reference._render_segment`: the reader is frozen "
    "(#1048), so its handler still files a bug as Refused. See the module docstring."))
@pytest.mark.parametrize("where, bug", [
    pytest.param("check_pins", KeyError("injected bug"), id="check_pins-KeyError"),
    pytest.param("check_pins", TypeError("injected bug"), id="check_pins-TypeError"),
    pytest.param("construction", AttributeError("injected bug"), id="construction-AttributeError"),
])
def test_render_segment_lets_a_bug_escape_as_an_execution_error(monkeypatch, where, bug):
    """#1777's injected-bug control for the FROZEN reader -- the defect as it
    stands on main, kept as a strict xfail. The handler is `except Exception`,
    so a bug anywhere from the rig's construction to the readbacks after the
    render is raised as `Refused('Mini V3 render failed: ...')`: an execution
    error filed as a missing precondition, exit 2. It must propagate unchanged.
    `RuntimeError` and `AssertionError` are not injected: they are how this
    callee refuses. `raises=m.Refused` pins WHY it fails today -- any other
    outcome (the repair landing, or the stand-in rig breaking) is reported."""
    if where == "construction":
        def ctor(**_kw):
            raise bug
        monkeypatch.setattr(m.rr, "MiniV3Rig", ctor)
    else:
        monkeypatch.setattr(m.rr, "MiniV3Rig", functools.partial(_Rig, fail_in_pins=bug))
    with pytest.raises(type(bug), match="injected bug") as e:
        _segment()
    assert not isinstance(e.value, m.Refused)


def test_render_segment_passes_its_own_refusal_through_unwrapped(monkeypatch):
    """The module's own refusals were never relabelled and still are not: a
    readback that does not hold is raised by `_readback` as this module's
    `Refused` and reaches the caller with that message, not "render failed"."""
    monkeypatch.setattr(m.rr, "MiniV3Rig", functools.partial(_Rig, rename={15: "Level Osc2"}))
    with pytest.raises(m.Refused, match=r"^parameter 15 is 'Level Osc2', expected 'Level Osc1'$") as e:
        _segment()
    assert e.value.__cause__ is None
