"""#1899: can `audio_measure.InsufficientEvidence` reach
`tools/measure_m5_pulse_cutoff.py`'s `Refused`-only dispatch? Known answers,
on the real estimators. Fast: the 255k-frame voice is never rendered.

THE CENSUS ROW (#1782, read off the AST): `measure_m5_pulse_cutoff.py:73`
(`phrase`) calls `mono_m5a_score.measure` -> `audio_measure.harmonic_signature`,
which raises `InsufficientEvidence` -- an `AssertionError`, not a
`refusal.Refused` -- and `main()` catches only `refusal.Refused`. The census
cannot say whether any input the tool accepts makes the estimator refuse.

RED, AS RUN ON `ff1532c5` (= `origin/main`, 2026-11-28), before anything was
changed -- `InsufficientEvidence` injected at the `score.measure` seam and
driven through `main(["--out", <tmp>])`:

    File ".../tools/measure_m5_pulse_cutoff.py", line 73, in phrase
        m = score.measure(case_id=case, voice_factory=lambda: ol.voice("pulse2x"),
    File "/tmp/m5pc/red_inject.py", line 9, in boom
        raise AM.InsufficientEvidence("harmonic_signature: silent")
    audio_measure.InsufficientEvidence: harmonic_signature: silent
    EXIT=1

So the handler does let it through. Whether anything REACHES it is a separate
question, and the answer is no -- every refusal-shaped input is stopped one
line earlier, by a gate that renders the same verdict as a `Refused`:

THE GATE (`mono_m5a_score.py:457-464`). `measure()` calls `refine_f0(xo)` and
raises `Refused("... pitch refused: ...")` unless it is ok, and only THEN calls
`harmonic_signature(xo, f0=eo.value)`. With `f0` given, `harmonic_signature`
skips its own fundamental search and has exactly two ways to refuse --
`is_silent(x)` and `windowed_tone_amplitude(x, f0).require()` -- and
`refine_f0` runs the SAME `is_silent(x)` (same 1e-9 floor) on the SAME slice
first, then refuses short, off-pitch, octave-down, DC and fundamental-free
records on its own account. Thirteen inputs were run through the pair below:
eleven are refused by `refine_f0`; the two that pass it (a tone at the note; a
600-sample tone, 13 periods) pass `harmonic_signature` too. `harmonic_signature`
refuses only the two silent ones -- the other nine it would have ACCEPTED with
`f0` given -- so the gate refuses a strict superset, and nothing is refused by
the guarded estimator alone.

THE VERDICT: unreachable, so `main()`'s handler is deliberately left narrow
(issue AC#4: "If no input can reach the refusal (an earlier gate stops it),
no code change"). `tools/measure_m5_pulse_cutoff.py` is not in this change.

WHAT THESE ROWS PIN:
  the gate      `refine_f0` refuses every refusal-shaped input, on the real
                estimators, so the census site cannot raise on its own
  the dispatch  a `refusal.Refused` from `measure` is REFUSED (2) through this
                tool's `main`, by name, with nothing written and `probe` unreached
  control       a plain `AssertionError` from the same seam is still exit 1 at
                `__main__` (AC#3 -- holds with or without a wider handler)
  the row       `InsufficientEvidence` from the same seam is exit 1 today. That
                is the recorded verdict, not an oversight: it flips to REFUSED
                only together with an input the gate lets through, and the gate
                row above is what fails first if one appears.

ONE THING THE GATE DOES NOT COVER, recorded so it is not re-traced:
`_stage_diagnostics` (`mono_m5a_score.py:219`) hands the four STAGE slices
(osc, mixed, ladder, output) to `harmonic_signature` with no `refine_f0` of
their own, and a silent stage raises `InsufficientEvidence` there -- pinned
below on the real function. It sits INSIDE `event_diagnostics.append(...)`,
downstream of every gate above on the OUTPUT, so it is reached only when the
output is already a healthy in-tune note; and the shipping chain
(`voice_fx.py:1571-1573`: `v = (y*ae)>>15; out = sat16((v*vol)>>15)`, ladder
`y`) cannot sound with a silent ladder. A silent osc/mixed under a sounding
output would be the ladder's free ring at a 14-21.6 kHz cutoff, which
`refine_f0` rejects as off-pitch. That last step is READ, not run.

GUARD-DEFEAT STATEMENT (verification-rules rule 8): the gate row pins the
census site only. A NEW estimator call added to this tool, outside `measure`,
needs its own row -- none of these tests can see it.

WRONG-THEN-RIGHT (5, all caught by running, none by reading):
  1. The first red run passed a `pathlib.Path` to `main()`, and the traceback
     was argparse's (`'PosixPath' object is not subscriptable`) -- a harness
     fault rendered where the tool's result belonged. `main` takes strings.
  2. The first gate row asserted `harmonic_signature` would ALSO refuse the
     nine off-pitch / DC / noise / short inputs. With `f0` given it refuses
     none of them (5 rows red): `refine_f0` is the only estimator on this
     site that looks for a fundamental. The row below now pins the weaker,
     true implication -- `harmonic_signature` raises only where `refine_f0`
     already refused -- and says the superset out loud.
  3. `capsys.readouterr()` is `.out` / `.err`, not `.stdout` / `.stderr`.
  4. The `_stage_diagnostics` control at a 50 ms slice was refused by that
     function's OWN foldback gate (`predicted images collide with real
     harmonics`) -- a `Refused` from the second estimator in its loop, not the
     un-gated first one. 0.3 s passes; the 255k-frame voice is still not run.
  5. The same row's silent `osc` was a bare array where the trace keeps a
     1-tuple (`trace["osc"][0]`), so it raised IndexError at :218 -- a harness
     shape fault, not the refusal being pinned.
"""
from __future__ import annotations

import pathlib
import sys
import textwrap

import numpy as np
import pytest

import exit_contract
import measure_m5_pulse_cutoff as tool
import refusal

score = tool.score
am = score.am
SR = score.SR
NOTE = 84                      # M5A's lower pulse event
F0 = score.vf.note_hz(NOTE)


# ---------------------------------------------------------------------------
# inputs for the gate: everything an estimator could refuse a segment for
# ---------------------------------------------------------------------------
def _cases():
    rng = np.random.default_rng(0)
    n = int(0.3 * SR)
    t = np.arange(n) / SR
    tone = lambda m: 0.5 * np.sin(2 * np.pi * F0 / m * t)  # noqa: E731
    return {
        "digital silence": np.zeros(n),
        "under the 1e-9 floor": np.full(n, 5e-10),
        "just over the floor, DC": np.full(n, 2e-9),
        "just over the floor, noise": rng.uniform(-2e-9, 2e-9, n),
        "quiet noise, no fundamental": rng.uniform(-1e-6, 1e-6, n),
        "full-scale noise": rng.uniform(-1, 1, n),
        "tone an octave down": tone(2),
        "DC at full scale": np.ones(n),
        "single impulse": np.eye(1, n, 0)[0],
        "1-sample record": np.array([0.5]),
        "100-sample record": 0.5 * np.sin(2 * np.pi * F0 * np.arange(100) / SR),
        "healthy tone at the note": tone(1),
        "600-sample tone (13 periods)": 0.5 * np.sin(2 * np.pi * F0 * np.arange(600) / SR),
    }


REFUSED_BY_GATE = [k for k in _cases() if not k.startswith("healthy")
                   and not k.startswith("600")]
PASSES_BOTH = ["healthy tone at the note", "600-sample tone (13 periods)"]


def test_class_relations_the_trace_rests_on():
    assert issubclass(am.InsufficientEvidence, AssertionError)
    assert not issubclass(am.InsufficientEvidence, refusal.Refused)
    assert issubclass(score.Refused, refusal.Refused), "the gate's refusal is one main() catches"


def _guarded_estimator_refuses(x) -> bool:
    """Would `harmonic_signature(x, f0=<given>)` raise? The census site always
    passes `f0`, so this is its refusal set: silence, nothing else."""
    try:
        am.harmonic_signature(x, SR, f0=F0, kmax=12)
    except am.InsufficientEvidence:
        return True
    return False


@pytest.mark.parametrize("name", REFUSED_BY_GATE)
def test_gate_refuses_every_refusal_shaped_input(name):
    """The gate: `refine_f0` -- which `measure()` runs first, on the same
    slice -- refuses each of these, and `measure()` turns that into a
    `Refused` before the census site is reached."""
    eo = am.refine_f0(_cases()[name], F0, SR)
    assert not eo.ok, name
    assert eo.reason, "a refusal carries its reason"


@pytest.mark.parametrize("name", list(_cases()))
def test_nothing_is_refused_by_the_guarded_estimator_alone(name):
    """The implication the verdict rests on, over the whole list: wherever
    `harmonic_signature(f0=given)` would raise, `refine_f0` already refused.
    The two inputs it does refuse are named here so the row cannot pass
    vacuously on a list the guarded estimator accepts outright."""
    x = _cases()[name]
    guarded = _guarded_estimator_refuses(x)
    gate_refused = not am.refine_f0(x, F0, SR).ok
    assert not guarded or gate_refused, (name, "UNGATED: harmonic_signature raises past refine_f0")
    if name in ("digital silence", "under the 1e-9 floor"):
        assert guarded, "the guarded estimator's own refusal must be exercised on the list"


@pytest.mark.parametrize("name", PASSES_BOTH)
def test_inputs_the_gate_admits_pass_harmonic_signature_too(name):
    """The successful path, so the gate is not simply refusing everything."""
    x = _cases()[name]
    eo = am.refine_f0(x, F0, SR)
    assert eo.ok, (name, eo.reason)
    am.harmonic_signature(x, SR, f0=eo.value, kmax=12)


# ---------------------------------------------------------------------------
# this tool's dispatch, at the measure seam, with the render stubbed out
# ---------------------------------------------------------------------------
def _stub(monkeypatch, exc):
    """`score.measure` raises `exc`; `probe` must never be reached (it is
    called after both phrases in the same loop iteration)."""
    def measure(**_kw):
        raise exc

    def probe(*_a, **_k):
        pytest.fail("probe() reached after measure() refused")

    monkeypatch.setattr(score, "measure", measure)
    monkeypatch.setattr(tool.mp, "measure_point", probe)


def test_gated_refusal_from_measure_is_refused_2_through_main(monkeypatch, tmp_path, capsys):
    """What a silent render actually produces: `measure()`'s own `Refused`
    from the `refine_f0` gate, which `main()` renders REFUSED by name."""
    out = tmp_path / "nested" / "r.json"
    _stub(monkeypatch, score.Refused(f"pulse MIDI {NOTE} pitch refused: silent"))
    rc = tool.main(["--out", str(out)])
    assert rc == exit_contract.REFUSED
    captured = capsys.readouterr()
    assert f"REFUSED: pulse MIDI {NOTE} pitch refused: silent" in captured.out
    assert "Traceback" not in captured.err
    assert not out.exists(), "a refused run must not write a result"


_DRIVER = textwrap.dedent("""
    import pathlib, sys
    sys.path[:0] = [str(pathlib.Path.cwd() / "model"), str(pathlib.Path.cwd() / "tools")]
    import measure_m5_pulse_cutoff as tool
    tool.score.measure = lambda **kw: (_ for _ in ()).throw({exc})
    raise SystemExit(tool.main(["--out", {out!r}]))
""")


def _through_main(exc_src: str, tmp_path):
    """The real `main` return code and an uncaught exception's exit status, as
    a process: `SystemExit(main())` for a handled outcome, Python's own exit 1
    for one that escapes."""
    import subprocess
    d = tmp_path / "drv"
    d.mkdir()
    script = d / "drive.py"
    script.write_text(_DRIVER.format(exc=exc_src, out=str(d / "r.json")))
    return subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                          timeout=300, cwd=str(pathlib.Path(__file__).resolve().parents[1]))


def test_plain_assertion_error_from_the_seam_is_still_exit_1(tmp_path):
    """AC#3 control: an `AssertionError` that is not an estimator refusal is an
    execution error -- exit 1 with its traceback, never REFUSED."""
    p = _through_main('AssertionError("injected")', tmp_path)
    assert p.returncode == exit_contract.FAIL, (p.returncode, p.stderr[-400:])
    assert "Traceback" in p.stderr and "AssertionError: injected" in p.stderr, p.stderr[-400:]
    assert "REFUSED" not in p.stdout + p.stderr


def test_census_row_insufficient_evidence_is_exit_1_today_and_that_is_the_verdict(tmp_path):
    """The row, confirmed end to end, and pinned AS the no-change verdict:
    `main()` names only `refusal.Refused`, so the estimator's own refusal
    escapes as exit 1. No input reaches it (the gate rows above); the day one
    does, this row flips to REFUSED together with the handler."""
    p = _through_main('tool.score.am.InsufficientEvidence("harmonic_signature: silent")', tmp_path)
    assert p.returncode == exit_contract.FAIL, (p.returncode, p.stderr[-400:])
    assert "audio_measure.InsufficientEvidence: harmonic_signature: silent" in p.stderr, p.stderr[-400:]
    assert "line 73, in phrase" in p.stderr, "it escapes at the census site"
    assert "REFUSED" not in p.stdout + p.stderr


# ---------------------------------------------------------------------------
# the one un-gated call, recorded so the next reader does not re-trace it
# ---------------------------------------------------------------------------
def _trace(osc, mixed, ladder):
    return {"osc": (osc,), "mixed": mixed, "ladder": ladder}


def test_stage_diagnostics_has_no_gate_of_its_own():
    """`_stage_diagnostics` (mono_m5a_score.py:219) refuses a silent STAGE with
    `InsufficientEvidence`, not `Refused`, and nothing upstream of it inside
    `measure()` checks the stage slices. Reachable only downstream of the
    output's own gates -- see the module docstring for why the shipping chain
    does not produce the trace."""
    n = int(0.3 * SR)
    t = np.arange(n) / SR
    tone = 0.4 * np.sin(2 * np.pi * F0 * t) * 32768
    good = _trace(tone, tone, tone.astype(np.int64))
    score._stage_diagnostics(good, tone.astype(np.int16), 0, n, F0)   # control: passes
    for stage in ("osc", "mixed", "ladder"):
        z = np.zeros(n, dtype=np.int64 if stage == "ladder" else float)
        silent = {**good, stage: (z,) if stage == "osc" else z}    # trace["osc"] is a 1-tuple
        with pytest.raises(am.InsufficientEvidence, match="silent"):
            score._stage_diagnostics(silent, tone.astype(np.int16), 0, n, F0)
