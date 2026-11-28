#!/usr/bin/env python3
"""The capability-gated comparison pack (#136), with no plugin in the path.

    python -m pytest tools/test_capability_pack.py -q

TWO JOBS, KEPT APART -- and the split matters here more than usual.

**The gate.** `tools/capability_pack.py` exists to demonstrate that a pack
needing pitch, waveform and a sustained filter can run against Mini V3, whose
RIG-LEVEL verdict is False because of envelope timing alone. That is a claim
about the verdict table and it is tested as one: the pack must be cleared, the
same pack with `envelope_timing` added must be REFUSED, and the refusal must
name the capability.

**The measurements.** The gate passing says nothing about whether the pack
measures anything real, and a demonstration whose measuring half is a stub is a
demonstration of nothing. So `measure_oscillator` and `measure_filter_pair` are
exercised against CLOSED-FORM signals: a mathematical ramp and a 50 % rectangle
at a commanded note, and a one-pole at two corners an octave apart. Their answers
are known from arithmetic, not from this repository -- the distinction
`CLAUDE.md` draws when it says an estimator calibrated on our own model is not
validated.

The two halves also fail differently, which is the useful part. A broken gate
parks work that was never blocked (#122's failure). A broken measurement
publishes a number about a knob position. Only the first is visible by reading
the verdict table.
"""
from __future__ import annotations

import math
import pathlib
import subprocess
import sys
import textwrap

import numpy as np
import pytest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "audition"))

import audio_measure as am                                           # noqa: E402
import capability_pack as cp                                        # noqa: E402
import exit_contract                                                # noqa: E402
import refprofile as rp                                             # noqa: E402
from dsp import SR, note_hz                                         # noqa: E402


# ===========================================================================
# closed-form signals. The answers come from arithmetic, not from this repo.
# ===========================================================================
#
# WRONG BEFORE IT WAS RIGHT, and the record is worth more than the fix.
#
# The first version of these generators used `note_hz(note)` directly. `saw` then
# identified at MIDI 36 and REFUSED at 48 and 60 -- "the record does not repeat at
# 130.8 Hz (per-period residual 5.7 % of the cycle)". That read exactly like a
# defect in `waveform_id` at higher pitches, and it is a defect in the STIMULUS:
# 48000 / 130.813 is 367.02 samples, so a naive ramp's discontinuity lands at a
# different sub-sample position every cycle and the record genuinely does not
# repeat. A real oscillator is bandlimited and has no such jitter.
#
# So the test signals are built at the nearest frequency with an INTEGER number
# of samples per period -- 0.3 cents off the note, which `refine_f0` accepts
# (its window is 50 cents) and which `measure_oscillator` reports as
# `cents_from_commanded`. Had the threshold been loosened instead, the suite
# would have "passed" while hiding that the estimator is sensitive to period
# jitter, which is the one thing Mini V3's +0.14 cents makes load-bearing here.
def integer_period_hz(note: int, sr: int = SR) -> float:
    """The frequency nearest `note` whose period is a whole number of samples."""
    return sr / round(sr / float(note_hz(int(note))))


def _t(seconds=0.5, sr=SR):
    return np.arange(int(seconds * sr)) / sr


def saw(f0, seconds=0.5, amp=0.5, sr=SR):
    """A ramp: one jump per cycle, which is what `waveform_id` counts."""
    return amp * (2.0 * ((f0 * _t(seconds, sr)) % 1.0) - 1.0)


def rect(f0, duty=0.5, seconds=0.5, amp=0.5, sr=SR):
    """A rectangle at a stated duty: two jumps per cycle."""
    ph = (f0 * _t(seconds, sr)) % 1.0
    return amp * np.where(ph < duty, 1.0, -1.0)


def saw_at(note, **kw):
    return saw(integer_period_hz(note), **kw)


def rect_at(note, duty=0.5, **kw):
    return rect(integer_period_hz(note), duty=duty, **kw)


def one_pole(x, fc, sr=SR):
    """A one-pole low-pass with a KNOWN corner, so the centroid pair has a
    known direction. Not our ladder: the point is a filter nothing in this
    repository tuned."""
    a = math.exp(-2.0 * math.pi * fc / sr)
    y = np.zeros(len(x))
    s = 0.0
    for i, v in enumerate(x):
        s = a * s + (1.0 - a) * v
        y[i] = s
    return y


# ===========================================================================
# the gate
# ===========================================================================
def test_the_pack_consumes_exactly_the_three_capabilities_it_claims():
    assert cp.MINIV3_PACK.requires == ("pitch", "waveform", "sustained_filter")
    for cap in cp.MINIV3_PACK.requires:
        assert cap in rp.CAPABILITIES
    assert "envelope_timing" not in cp.MINIV3_PACK.requires, \
        "the whole point of this pack is that it does not need envelope timing"


def test_the_pack_is_cleared_against_a_rig_whose_overall_verdict_is_NO():
    """**#136's payoff as an executable fact.** Mini V3's rig-level verdict is
    False, it is not in `qualified_rigs()`, and this pack may still run against
    it -- which was unexpressible before the capability map and is why two
    waveforms at two pitches sat behind an envelope calibration."""
    pack = cp.MINIV3_PACK
    assert rp.RIG_VERDICTS[pack.rig]["qualified"] is False
    assert pack.rig not in rp.qualified_rigs()
    rows = cp.gate(pack)                                 # must not raise
    assert [r["capability"] for r in rows] == list(pack.requires)
    assert all(r["qualified"] is True for r in rows)
    assert all(r["why"] for r in rows), "a cleared capability still says why"


def test_the_same_pack_with_envelope_timing_added_is_REFUSED_by_capability():
    """The control that ships with the gate. Same rig, same host, one capability
    added: a gate nobody has watched reject anything is a gate nobody should
    trust."""
    with pytest.raises(rp.Refused) as e:
        cp.gate(cp.MINIV3_ENVELOPE_PACK)
    why = str(e.value)
    assert "envelope_timing" in why, why
    assert "NOT QUALIFIED" in why, why
    assert "pitch, waveform, sustained_filter" in why, \
        f"the refusal must say what the rig IS good for: {why}"


def test_the_gate_reports_the_capabilities_the_pack_does_not_use():
    """The finding, not a footnote: the pack runs while the rig's envelope timing
    reads NO, and the two causality axes read "no verdict" rather than NO."""
    rows = {r["capability"]: r["verdict"] for r in cp.not_required(cp.MINIV3_PACK)}
    assert rows["envelope_timing"] == "NO"
    assert rows["pitch_causality"] == "no verdict"
    assert rows["filter_causality"] == "no verdict"


def test_the_cli_separates_FAIL_from_REFUSED():
    """Three outcomes, three exit codes, and the two that are easy to conflate
    are the ones that matter: a gate rejection is FAIL (somebody has to change
    the pack or measure the capability) and a missing plugin host is REFUSED
    (nothing was attempted and the pack is fine)."""
    assert cp.main(["--list"]) == cp.OK
    assert cp.main(["--gate"]) == cp.OK
    assert cp.main(["--gate", "--pack", cp.MINIV3_ENVELOPE_PACK.name]) == cp.FAIL


def test_a_run_without_the_plugin_host_REFUSES_and_does_not_fail():
    """The answer on almost every host in this fleet, and it must not look like a
    broken pack. Skipped where dawdreamer IS installed, because there the
    precondition holds and this test has nothing to assert."""
    try:
        import dawdreamer                                       # noqa: F401
    except ImportError:
        pass
    else:
        pytest.skip("dawdreamer is installed here, so the refusal cannot fire")
    with pytest.raises(rp.Refused) as e:
        cp.build_rig(cp.MINIV3_PACK)
    why = str(e.value)
    assert "no plugin host" in why, why
    assert "REFUSED and not FAIL" in why, why
    assert cp.main(["--run"]) == cp.REFUSED


# ===========================================================================
# the measurements, on signals whose answers are arithmetic
# ===========================================================================
@pytest.mark.parametrize("note", [36, 48])
def test_a_mathematical_ramp_reads_back_as_a_saw_at_the_commanded_note(note):
    got = cp.measure_oscillator(saw_at(note), note)
    assert got["waveform"] == "saw", got
    assert got["f0_hz"] == pytest.approx(integer_period_hz(note), rel=1e-3), got
    assert abs(got["cents_from_commanded"]) < 1.0, got


@pytest.mark.parametrize("note", [36, 48])
def test_a_mathematical_rectangle_reads_back_with_its_duty_MEASURED(note):
    """"pulse:50.0%" and not "square": the duty is reported, never assumed. A
    rig at 47.9 % duty answering "square" is the shape of a published result that
    is wrong by a measurable amount nobody looked at."""
    got = cp.measure_oscillator(rect_at(note, duty=0.5), note)
    assert got["waveform"].startswith("pulse:"), got
    assert float(got["waveform"].split(":")[1].rstrip("%")) == pytest.approx(50.0, abs=1.5)


def test_an_off_duty_rectangle_is_not_rounded_to_fifty_percent():
    """The control for the test above: if the duty were assumed rather than
    measured, both of these would answer 50 %."""
    got = cp.measure_oscillator(rect_at(36, duty=0.35), 36)
    assert got["waveform"].startswith("pulse:"), got
    assert float(got["waveform"].split(":")[1].rstrip("%")) == pytest.approx(35.0, abs=2.0)


def test_the_two_waveforms_are_told_apart_at_both_pitches():
    """The pack's own discrimination matrix. Two waveforms at two pitches buys
    nothing if the pair is not separable at either."""
    for note in (36, 48):
        assert cp.measure_oscillator(saw_at(note), note)["waveform"] == "saw"
        assert cp.measure_oscillator(rect_at(note), note)["waveform"].startswith("pulse:")


def test_an_octave_down_record_is_refused_rather_than_mislabelled():
    """Mini V3's Range default answered MIDI 48 with 65.42 Hz, exactly half of
    130.81. The measurement must REFUSE a record that is not at the commanded
    note, because a waveform label attached to the wrong octave is a result."""
    got = cp.measure_oscillator(saw(integer_period_hz(48) / 2.0), 48)
    assert "refused" in got, got
    assert "waveform" not in got
    assert "fundamental" in got["refused"], got


def test_silence_is_refused_and_not_reported_as_a_waveform():
    got = cp.measure_oscillator(np.zeros(int(0.5 * SR)), 36)
    assert "refused" in got and "waveform" not in got


def test_the_closed_filter_position_measures_darker_than_the_open_one():
    """A one-pole at 3000 Hz and at 300 Hz on the same ramp. The direction is
    arithmetic; the only thing being checked is that the pair measurement reads
    it the right way round -- the failure that would make an open/closed pack
    report its own sign error as a finding about the rig.

    **The assertion is the DIRECTION and the ORDERING, not a magnitude.** The
    first version asserted `ratio > 2.0` and measured 1.74, and the honest reading
    of that is not "loosen it to 1.7": a power-weighted centroid over 20..20 kHz on
    a ramp sits near the fundamental whatever the corner is, so the absolute ratio
    is a property of the stimulus's spectrum as much as of the filter. A threshold
    picked after seeing 1.74 would be a number chosen to pass. What IS a property
    of the measurement is that a WIDER corner gap must read as a larger ratio, and
    that is asserted instead."""
    x = saw_at(36)
    wide = cp.measure_filter_pair(one_pole(x, 3000.0), one_pole(x, 300.0))
    narrow = cp.measure_filter_pair(one_pole(x, 3000.0), one_pole(x, 1500.0))
    assert wide["darker_when_closed"] is True, wide
    assert wide["centroid_open_hz"] > wide["centroid_closed_hz"], wide
    assert wide["centroid_ratio_open_over_closed"] > 1.0, wide
    assert (wide["centroid_ratio_open_over_closed"]
            > narrow["centroid_ratio_open_over_closed"] > 1.0), (wide, narrow)


def test_an_open_closed_pair_that_did_not_move_reports_a_ratio_of_one():
    """The control: a cutoff knob connected to nothing gives the same centroid
    twice. The pack must report a ratio of 1 rather than a number that looks like
    a measurement of a filter."""
    y = one_pole(saw_at(36), 1000.0)
    got = cp.measure_filter_pair(y, y)
    assert got["centroid_ratio_open_over_closed"] == pytest.approx(1.0, abs=1e-6)
    assert got["darker_when_closed"] is False


def test_measure_assembles_a_report_for_the_whole_pack_over_a_stub_rig():
    """The shipping `render`/`measure` path with a stub in place of the plugin, so
    the step wiring is exercised on every host. A pack whose step ids and
    measurement keys only line up on a machine with dawdreamer is a pack nobody
    can review."""
    class Stub:
        """Mini V3's two methods the pack uses, over closed-form audio."""
        def __init__(self):
            self.knob = 1.0

        def set_point(self, cut, res):
            self.knob = cut
            return cut

        def osc_tone(self, wave, note, seconds=0.5):
            x = saw_at(note) if wave == "saw" else rect_at(note)
            # the knob as a corner only for the stub's own sake: 1.0 open,
            # 0.30 nearly closed. Nothing is claimed about the real taper.
            return one_pole(x, 200.0 + 6000.0 * self.knob ** 2)

    res = cp.measure(cp.MINIV3_PACK, cp.render(cp.MINIV3_PACK, Stub()))
    assert res["rig"] == "miniv3" and res["host"] == "dawdreamer"
    assert res["requires"] == ["pitch", "waveform", "sustained_filter"]
    assert set(res["steps"]) == {"saw-36", "saw-48", "square-36", "square-48"}
    assert res["steps"]["saw-36"]["waveform"] == "saw"
    assert res["steps"]["square-48"]["waveform"].startswith("pulse:")
    assert res["filter_pair"]["darker_when_closed"] is True


def test_every_step_in_the_pack_says_what_it_is_for():
    """A step with no reason is a render nobody can interpret afterwards -- the
    same rule `refprofile`'s clips and `NOT_RUN`'s entries carry."""
    assert len(cp.MINIV3_PACK.steps) == 6
    for s in cp.MINIV3_PACK.steps:
        assert len(s.why) > 40, s
        assert s.kind in ("oscillator", "filter")
        if s.kind == "oscillator":
            assert s.wave and s.note is not None
        else:
            assert s.cutoff_knob is not None


def test_the_pack_crosses_its_two_axes_rather_than_sampling_a_diagonal():
    """Two waveforms at two pitches is four renders, not two. A diagonal (saw
    low, square high) cannot separate a pitch defect from a waveform defect."""
    osc = [(s.wave, s.note) for s in cp.MINIV3_PACK.steps if s.kind == "oscillator"]
    waves = {w for w, _ in osc}
    notes = {n for _, n in osc}
    assert len(waves) == 2 and len(notes) == 2
    assert set(osc) == {(w, n) for w in waves for n in notes}


# ===========================================================================
# an evidence refusal RAISED by the measurement: REFUSED, never a traceback
# ===========================================================================
#
# #1884, this file's row of the #1782 census. `measure` reaches
# `audio_measure`, which refuses two ways: by RETURNING a `not ok` estimate,
# which `measure_oscillator` turns into its `"refused"` key, and by RAISING
# `InsufficientEvidence`, which `cmd_run` did not catch -- its one handler
# named `rp.Refused`, a class `InsufficientEvidence` is not. A raised refusal
# therefore left as a traceback and exit 1: a refusal rendered as FAIL, the
# inversion `tools/exit_contract.py` (#804) exists to prevent.
#
# TWO SITES RAISE, and both are driven through the real `main` -> `cmd_run` ->
# `measure` path with only the apparatus replaced (`_patches`):
#
#   `am.peak`         a record holding NaN or Inf: `require_finite` raises
#                     `NonFiniteAudio`, an `InsufficientEvidence`
#   `am.waveform_id`  a record of TEN or ELEVEN whole periods: `cycle_average`
#                     needs ten and passes, then `harmonic_signature` calls
#                     `.require()` on a projection that needs twelve, which
#                     raises a PLAIN `InsufficientEvidence`
#
# WRONG BEFORE RIGHT (1). #1884's curation traced the second site as
# UNREACHABLE: its probes covered silence, a 1e-12 sine, DC, and 50- and
# 200-sample records, every one of which stops at the guarded `cycle_average`
# or at `refine_f0`. None sat in the window between `cycle_average`'s ten
# periods and `windowed_tone_amplitude`'s twelve. A 10- or 11-period ramp at
# the pack's own notes raises, measured on `origin/main` 560a1580. It is the
# census's NAMED site, and it is the control that tells `except
# am.InsufficientEvidence` apart from a handler written against the NaN
# subclass alone.
#
# The control in the other direction: `InsufficientEvidence` IS an
# `AssertionError`, so `except AssertionError` would catch it too -- and would
# relabel a bug in the pack as a refusal. A plain `AssertionError` raised inside
# `measure` must still leave as a traceback and exit 1.
_AT = 100                     # one sample, well inside every record the rig renders

#: mode -> (the step whose record is damaged, the damage)
_NON_FINITE = {
    "nan":        ("saw-48",       lambda x: _with(x, _AT, np.nan)),
    "inf":        ("square-36",    lambda x: _with(x, _AT, np.inf)),
    "all-nan":    ("saw-36",       lambda x: np.full(len(x), np.nan)),
    "nan-open":   ("filter-open",  lambda x: _with(x, _AT, np.nan)),
    "nan-closed": ("filter-closed", lambda x: _with(x, _AT, np.nan)),
}


def _with(x, i, value):
    y = np.array(x, dtype=np.float64)
    y[i] = value
    return y


def _short(note, periods):
    """The first `periods` whole periods of a record and nothing after them: a
    rig that returned early. The ramp at `note` has a whole number of samples
    per period, so the cut is exact."""
    return lambda x: x[: int(periods * round(SR / integer_period_hz(note)))]


def _plain_assertion(y, note, sr=SR):
    raise AssertionError("a bug in the pack, not a refusal")


class _Rig:
    """Mini V3's two methods over closed-form audio, with ONE record damaged.

    `poison` names the step whose record `how` damages. Every other step still
    measures, so the refusal has to come from the damaged record and not from a
    rig that cannot render at all."""

    def __init__(self, poison=None, how=None):
        self.poison, self.how = poison, how
        self.knob, self.filter_step = 1.0, False

    def set_point(self, cut, res):
        self.knob, self.filter_step = cut, True
        return cut

    def osc_tone(self, wave, note, seconds=0.5):
        x = saw_at(note) if wave == "saw" else rect_at(note)
        if self.filter_step:
            self.filter_step = False
            step = "filter-open" if self.knob > 0.5 else "filter-closed"
            x = one_pole(x, 200.0 + 6000.0 * self.knob ** 2)
        else:
            step = f"{wave}-{note}"
        return self.how(x) if step == self.poison else x


def _patches(mode):
    """The apparatus substitutions for `mode`, as attributes of `capability_pack`
    to replace: the rig `build_rig` returns, or -- for the control -- one
    measurement. Nothing else is touched; the dispatch, `cmd_run`, `measure`
    and `audio_measure` are the shipped ones."""
    if mode == "plain-assertion":
        return {"build_rig": lambda pack: _Rig(),
                "measure_oscillator": _plain_assertion}
    if mode.startswith("short-"):
        _, note, periods = mode.split("-")
        return {"build_rig": lambda pack: _Rig(f"saw-{note}",
                                               _short(int(note), int(periods)))}
    return {"build_rig": lambda pack: _Rig(*_NON_FINITE[mode])}


def _arm(monkeypatch, mode):
    for name, value in _patches(mode).items():
        monkeypatch.setattr(cp, name, value)


def _rig_for(mode):
    """The rig a fake `reference_rigs.MiniV3Rig()` hands the SHIPPED script: the
    same damage as `_patches`, but supplied at the plugin boundary so nothing
    in `capability_pack` itself is replaced."""
    if mode == "plain-assertion":
        class _Buggy(_Rig):
            def osc_tone(self, wave, note, seconds=0.5):
                raise AssertionError("a bug in the pack, not a refusal")
        return _Buggy()
    if mode.startswith("short-"):
        _, note, periods = mode.split("-")
        return _Rig(f"saw-{note}", _short(int(note), int(periods)))
    return _Rig(*_NON_FINITE[mode])


def _process(mode: str, tmp_path) -> subprocess.CompletedProcess:
    """Execute `tools/capability_pack.py --run` itself, as `__main__`, in a real
    interpreter, so the number a batch runner sees is the exit status of the
    shipped entry point (`sys.exit(main())`) and not a return value. Only the
    plugin host (`dawdreamer`, `reference_rigs`) is faked; the script's own
    functions are the shipped ones."""
    driver = tmp_path / "drive_pack.py"
    driver.write_text(textwrap.dedent(f"""
        import runpy, sys, types
        sys.path.insert(0, {str(HERE)!r})
        import test_capability_pack as t
        sys.modules["dawdreamer"] = types.ModuleType("dawdreamer")
        rr = types.ModuleType("reference_rigs")
        rr.MiniV3Rig = lambda: t._rig_for({mode!r})
        sys.modules["reference_rigs"] = rr
        sys.argv = [{str(HERE / "capability_pack.py")!r}, "--run"]
        runpy.run_path(sys.argv[0], run_name="__main__")
    """), encoding="utf-8")
    return subprocess.run([sys.executable, str(driver)], capture_output=True,
                          text=True, timeout=300, cwd=str(ROOT))


def test_the_classes_the_handler_rests_on():
    """Pinned because the whole fix is a class boundary: `rp.Refused` does not
    cover an evidence refusal, and `AssertionError` covers too much."""
    assert issubclass(am.NonFiniteAudio, am.InsufficientEvidence)
    assert not issubclass(am.InsufficientEvidence, rp.Refused)
    assert issubclass(am.InsufficientEvidence, AssertionError)


@pytest.mark.parametrize("mode", sorted(_NON_FINITE))
def test_a_non_finite_record_from_the_rig_is_REFUSED_not_a_traceback(
        mode, monkeypatch, capsys, tmp_path):
    """A refusal that reaches the CLI as an exception must come out as the
    pack's REFUSED with its reason, and must write no report: measurements on
    disk beside a refusal are a result nobody can trust."""
    _arm(monkeypatch, mode)
    out = tmp_path / "report.json"
    assert cp.main(["--run", "--out", str(out)]) == cp.REFUSED
    stdout = capsys.readouterr().out
    assert stdout.startswith("REFUSED"), stdout
    assert "non-finite" in stdout and "this is not audio" in stdout, stdout
    assert not out.exists(), "a refused run must not write a report"


@pytest.mark.parametrize("note", [36, 48])
@pytest.mark.parametrize("periods", [10, 11])
def test_a_record_of_ten_or_eleven_periods_is_REFUSED_at_the_census_site(
        note, periods, monkeypatch, capsys):
    """The census's named site. Twelve periods of the same ramp read back as a
    saw, so what refuses below is the LENGTH of the record, not a stub that
    cannot render one."""
    assert cp.measure_oscillator(_short(note, 12)(saw_at(note)), note)["waveform"] == "saw"
    with pytest.raises(am.InsufficientEvidence) as e:
        cp.measure_oscillator(_short(note, periods)(saw_at(note)), note)
    assert type(e.value) is am.InsufficientEvidence, type(e.value)
    assert "too few periods" in str(e.value)
    _arm(monkeypatch, f"short-{note}-{periods}")
    assert cp.main(["--run"]) == cp.REFUSED
    stdout = capsys.readouterr().out
    assert stdout.startswith("REFUSED") and "too few periods" in stdout, stdout


def test_a_plain_AssertionError_inside_measure_is_still_an_execution_error(
        monkeypatch, capsys):
    """The control for the handler, and the reason it is not `except
    AssertionError`: a bug in the pack is an execution error, never a refusal
    (`tools/refusal.py`'s "inverse defect")."""
    _arm(monkeypatch, "plain-assertion")
    with pytest.raises(AssertionError) as e:
        cp.main(["--run"])
    assert type(e.value) is AssertionError, type(e.value)
    assert "REFUSED" not in capsys.readouterr().out


def test_the_process_exits_REFUSED_with_no_traceback_on_a_nan_record(tmp_path):
    """The defect as a batch runner saw it: exit 1 with a traceback. Now exit 2,
    the status `tools/exit_contract.py` declares as no-evidence."""
    assert cp.REFUSED == exit_contract.REFUSED
    p = _process("nan", tmp_path)
    assert p.returncode == cp.REFUSED, (p.returncode, p.stderr[-400:])
    assert p.stdout.startswith("REFUSED") and "non-finite" in p.stdout, p.stdout[-400:]
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout, p.stderr[-400:]


def test_the_process_still_exits_1_with_a_traceback_on_a_plain_AssertionError(tmp_path):
    p = _process("plain-assertion", tmp_path)
    assert p.returncode == 1, (p.returncode, p.stdout[-200:], p.stderr[-400:])
    assert "Traceback" in p.stderr and "AssertionError: a bug in the pack" in p.stderr, \
        p.stderr[-400:]
    assert "REFUSED" not in p.stdout, "a bug must not be relabelled a refusal"
