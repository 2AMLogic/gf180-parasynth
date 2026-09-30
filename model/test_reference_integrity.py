"""Controls for the reference-integrity apparatus configuration."""
from __future__ import annotations

import numpy as np

from model import reference_integrity as ri


def test_miniv3_integrity_render_uses_the_requested_host_block(monkeypatch):
    observed = {}

    class MiniV3Stub:
        I = {"lvl_ext": 1, "ext_sw": 2, "lvl_o1": 3, "o1": 4,
             "cutoff": 5, "emphasis": 6}

        def __init__(self, *, block):
            observed["block"] = block

        def set(self, _index, _value):
            pass

        def render(self, _audio, seconds):
            return np.ones(int(seconds * ri.SR), dtype=np.float32) * 0.1

    monkeypatch.setattr(ri.rr, "MiniV3Rig", MiniV3Stub)
    audio = ri.steady("miniv3", seconds=1.0, source="smooth", block=16)
    assert observed["block"] == 16
    assert len(audio) == ri.SR



# ---------------------------------------------------------------------------
# transient_report on pitched sources (issue #225)
# ---------------------------------------------------------------------------
import pytest                                                        # noqa: E402

SWEEP = (33, 36, 45, 52, 60, 64, 69, 72, 76, 84)


def _hz(midi):
    return 440.0 * 2 ** ((midi - 69) / 12)


def _saw(hz, seconds=6.0, amp=0.1, partials=None):
    """An additive saw. `partials=None` is full band -- every harmonic below
    Nyquist, the sharpest edge a band-limited oscillator can have; 24 is the
    fixture `tools/test_capture_m1a_phase_cycle.saw` uses."""
    t = np.arange(int(seconds * ri.SR)) / ri.SR
    x = np.zeros_like(t)
    top = int((ri.SR / 2) // hz) if partials is None else partials
    for k in range(1, top + 1):
        if k * hz < ri.SR / 2:
            x += np.sin(2 * np.pi * k * hz * t) / k
    return amp * x


def _clicks(x, every_s, amp_rel):
    y = ri.inject_clicks(x, every_s=every_s, amp_rel=amp_rel)
    n = len([t for t in np.arange(every_s, len(x) / ri.SR, every_s) if t >= 0.5 + 0.1])
    return y, n


@pytest.mark.parametrize("midi,partials", ((36, None), (60, None), (60, 24), (69, 24)))
def test_unpitched_default_reproduces_the_issue_225_defect(midi, partials):
    # the control that the fixtures show the bug: 5 ms blocks on a clean held
    # saw flag (about) one event per period. Not monotonic in pitch: the
    # full-band saw fails at 36 and 60 but not 45; the 24-partial saw fails at
    # 60 and 69 but not 36. If this goes red, the fixtures no longer exercise
    # #225 and the pitched tests below prove nothing.
    r = ri.transient_report(_saw(_hz(midi), partials=partials))
    assert r["n_events"] > 100
    assert r["block_samples"] == 240 and r["block_ms"] == 5.0


@pytest.mark.parametrize("partials", (None, 24))
@pytest.mark.parametrize("midi", SWEEP)
def test_clean_saw_reports_no_transients_with_f0(midi, partials):
    r = ri.transient_report(_saw(_hz(midi), partials=partials), f0_hz=_hz(midi))
    assert r["n_events"] == 0, r["event_times_s"]
    assert r["block_samples"] == int(2 * ri.SR / _hz(midi))
    # margin, not just a pass: the cleanest-looking block stays well under the floor
    assert r["peak_over_median_db"] < 0.75


@pytest.mark.parametrize("midi", SWEEP)
def test_injected_clicks_on_a_full_band_saw_are_all_caught_with_f0(midi):
    # 0.5 x peak: on a full-band saw a 0.25 x peak, 12-sample click is at this
    # detector's resolution (about 85 % caught; see transient_report)
    x = _saw(_hz(midi), seconds=10.0)
    y, n = _clicks(x, every_s=1.0, amp_rel=0.5)
    r = ri.transient_report(y, f0_hz=_hz(midi))
    assert r["n_events"] == n, r["event_times_s"]


@pytest.mark.parametrize("midi", (36, 45, 60, 69, 72))
def test_default_click_train_is_caught_with_f0(midi):
    # the issue's own case at inject_clicks' defaults (0.25 x peak every 7 s),
    # on the repository's 24-partial saw fixture
    x = _saw(_hz(midi), seconds=30.0, partials=24)
    y, n = _clicks(x, every_s=7.0, amp_rel=0.25)
    r = ri.transient_report(y, f0_hz=_hz(midi))
    assert n == 4 and r["n_events"] == n, r["event_times_s"]


def test_floor_is_load_bearing():
    # injected-bug control: the period block alone (no dB floor) still flags
    # sub-dB grid-drift jitter at higher notes, because a clean saw's
    # whole-period blocks are so alike that the MAD collapses
    bad = [m for m in SWEEP
           if ri.transient_report(_saw(_hz(m)), f0_hz=_hz(m), floor_db=0.0)["n_events"]]
    assert bad, "the floor is no longer needed; revisit TRANSIENT_FLOOR_DB"


def test_explicit_block_shorter_than_two_periods_is_refused():
    with pytest.raises(ri.am.InsufficientEvidence, match="periods"):
        ri.transient_report(_saw(_hz(36)), f0_hz=_hz(36), block_ms=5.0)
    # a long enough explicit block is measured, not refused
    r = ri.transient_report(_saw(_hz(36)), f0_hz=_hz(36), block_ms=40.0)
    assert r["block_samples"] == 1920


def test_bad_f0_is_rejected():
    for f0 in (0.0, -65.4, float("nan")):
        with pytest.raises(ValueError):
            ri.transient_report(_saw(_hz(36), seconds=2.0), f0_hz=f0)


# ---------------------------------------------------------------------------
# the weighting is a tilt, and no parameter claims to change it (issue #276)
# ---------------------------------------------------------------------------
import ast                                                            # noqa: E402
import inspect                                                        # noqa: E402
import textwrap                                                       # noqa: E402


def test_hp_hz_is_refused_not_silently_ignored():
    # `hp_hz=6000` was accepted and never read: the report came back identical
    # whatever the caller asked for. Removing it turns that into a TypeError.
    with pytest.raises(TypeError, match="hp_hz"):
        ri.transient_report(_saw(_hz(45), seconds=2.0), hp_hz=200.0)
    with pytest.raises(TypeError, match="hp_hz"):
        ri.transient_report(_saw(_hz(45), seconds=2.0), hp_hz=6000.0)


def _parameters_read_in_body(fn):
    """Parameter names of `fn` that its body actually READS.

    From the AST, so a name that appears only in the docstring or a comment
    does not count -- which is exactly how `hp_hz` looked used for as long as
    it was dead."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn))).body[0]
    body = tree.body
    if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]                      # drop the docstring
    read = set()
    for stmt in body:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Name):
                read.add(node.id)
            elif isinstance(node, ast.arg):  # a nested def/lambda
                read.add(node.arg)
    a = tree.args
    declared = [p.arg for p in a.posonlyargs + a.args + a.kwonlyargs]
    if a.vararg:
        declared.append(a.vararg.arg)
    if a.kwarg:
        declared.append(a.kwarg.arg)
    return declared, read


@pytest.mark.parametrize("name,fn", [(n, f) for n, f in vars(ri).items()
                                     if inspect.isfunction(f)
                                     and f.__module__ == ri.__name__])
def test_no_parameter_of_a_reference_integrity_function_is_dead(name, fn):
    # the general form of #276: a measurement tool that accepts a setting and
    # ignores it reports the same number whatever it was asked, and nothing
    # anywhere says so. Green against the whole module as written.
    declared, read = _parameters_read_in_body(fn)
    dead = [p for p in declared if p not in read]
    assert not dead, f"{name} accepts {dead} and never reads them"


def test_the_dead_parameter_guard_is_not_vacuous():
    # the guard's own control: it must flag a parameter that appears ONLY in a
    # docstring and a comment, which is the exact shape of the #276 defect. A
    # green guard over a module with no dead parameters proves nothing on its
    # own.
    def decoy(x, *, hp_hz: float = 6000.0):
        """Looks at the energy above `hp_hz`."""
        return x + 1                                   # flat above hp_hz

    declared, read = _parameters_read_in_body(decoy)
    assert declared == ["x", "hp_hz"]
    assert "hp_hz" not in read and "x" in read


@pytest.mark.parametrize("hz,want_db", ((250.0, -59.4), (500.0, -47.4), (1000.0, -35.3),
                                        (2000.0, -23.3), (4000.0, -11.4), (6000.0, -4.7),
                                        (8000.0, 0.0), (20000.0, +11.4)))
def test_second_difference_weighting_is_a_tilt(hz, want_db):
    # the docstring's table, measured through the shipping function rather than
    # a reimplementation of it: a pure tone in, the block level out, referred
    # to the tone's own RMS. Unity at sr/6 = 8 kHz and +12 dB/octave below it,
    # so 6 kHz -- the cutoff the dead `hp_hz` claimed to set -- is neither a
    # corner nor otherwise distinguished.
    amp = 0.1
    t = np.arange(int(2.0 * ri.SR)) / ri.SR
    r = ri.transient_report(amp * np.sin(2 * np.pi * hz * t))
    gain_db = 20 * np.log10(r["median_level"] / (amp / np.sqrt(2)))
    assert gain_db == pytest.approx(want_db, abs=0.1)
    assert gain_db == pytest.approx(
        20 * np.log10(4 * np.sin(np.pi * hz / ri.SR) ** 2), rel=0.01)


def test_the_tilt_has_no_stopband():
    # "+12 dB/octave everywhere" is the honest description, and it has a
    # consequence worth stating: low frequencies are attenuated, not removed,
    # and the slope does not flatten out anywhere below sr/6.
    amp, out = 0.1, {}
    for hz in (125.0, 250.0, 500.0, 1000.0, 2000.0, 4000.0):
        t = np.arange(int(2.0 * ri.SR)) / ri.SR
        out[hz] = ri.transient_report(amp * np.sin(2 * np.pi * hz * t))["median_level"]
        assert out[hz] > 0.0, f"{hz} Hz reads as exactly zero"
    # 125 Hz is 71 dB down and still measurable, not gone
    assert 20 * np.log10(out[125.0] / (amp / np.sqrt(2))) == pytest.approx(-70.9, abs=0.2)
    # the slope, on octaves where the 5 ms block holds enough cycles for the
    # block median to be the tone's RMS -- 125 Hz is 0.6 cycles per block and
    # its median reads 6 % high, which is the estimator, not the weighting
    for lo, hi in ((250.0, 500.0), (500.0, 1000.0), (1000.0, 2000.0)):
        assert 20 * np.log10(out[hi] / out[lo]) == pytest.approx(12.04, abs=0.1)
    # and it is an asymptote, not a law: the octave under sr/6 already shallows
    assert 20 * np.log10(out[4000.0] / out[2000.0]) == pytest.approx(11.88, abs=0.1)


def test_unpitched_path_is_unchanged():
    # no f0: 5 ms blocks and the plain median + k*MAD threshold, no floor
    x = ri.inject_clicks(_saw(_hz(45), seconds=4.0), every_s=1.0)
    r = ri.transient_report(x)
    assert r["f0_hz"] is None and r["block_samples"] == 240
    assert r["threshold"] == pytest.approx(r["median_level"] + 12.0 * r["mad"])
    assert ri.transient_report(x, block_ms=5.0) == r
