#!/usr/bin/env python3
"""Apparatus controls for tools/probes/mono_attack_context_refusal_trace.py (#1905).

    .venv/bin/python -m pytest tools/probes/test_mono_attack_context_refusal_trace.py -q

THE CLAIM PINNED: no audio `tools/measure_mono_attack_context.py` accepts makes
`audio_measure` raise `InsufficientEvidence` past its Refused-only handler, so
the handler at `measure_mono_attack_context.py:111` was left alone (#1905: "a
candidate found unreachable gets no code change"). Each test says what turns
it red and what that red would mean.

  injected control   an `InsufficientEvidence` raised inside `rv.measure`
                     leaves `main` uncaught: exit 1, no `refused.json`. This is
                     the census row's failure mode made real, and it is the
                     probe's own evidence that it can see what it traces. If it
                     ever exits 2, someone widened the handler and this file
                     should become that fix's test (#1751 shape). A plain
                     `AssertionError` that is not an estimator refusal must
                     stay exit 1 whatever happens to the handler.
  non-finite audio   the one route to an `InsufficientEvidence` family
                     exception. `_render_segment` must refuse it by name before
                     any estimator sees it. Red means the render gate moved or
                     the envelope no longer checks finiteness: the handler fix
                     this issue withheld becomes due.
  everything else    every other fixture must leave the real `main` at exit 0
                     or 2 with a named `Refused`. Red means an input the module
                     accepts now reaches an estimator refusal no handler names.
  full trace         the verdict over every fixture, and both of the probe's own
                     preconditions: REFUSED without the frozen capture,
                     NO-VERDICT when the injected control is not seen.
  real CLI           the two rows above on the process itself: `python
                     tools/measure_mono_attack_context.py` in a child
                     interpreter, exit status and stderr, not a return value.
  rule 8             with one fixture made to exit 1 through the shipped gates,
                     the verdict must say REACHABLE and name it.

Nothing here needs Mini V3: the probe's playback rig reports what it is asked
and plays a fixture, and every estimator and gate from `_render_segment` inward
is the shipped code.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "probes"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "model"))

import exit_contract                               # noqa: E402
import mono_attack_context_refusal_trace as T      # noqa: E402

STOPPED_IN_EVENT_ANALYSIS = ("sine_at_pitch", "saw_minus_h3", "saw_100c_flat", "saw_octave_down",
                             "saw_octave_up", "white_noise", "dc_only", "ideal_saw_0p5",
                             "tremolo_5hz", "detuned_pair_2c", "naive_saw", "subharmonic_m30")
STOPPED_IN_RENDER_SEGMENT = {"silence": "plugin output is silent",
                             "tone_peak_1e-10": "plugin output is silent",
                             "tone_peak_1e-6": "plugin output is silent",
                             "saw_unit_peak": "plugin output clips",
                             "short_1s": "host returned only 48000 samples",
                             "empty": "host returned only 0 samples"}


def test_handler_names_nothing_an_insufficient_evidence_is_under():
    """The premise the whole issue rests on, read from the class relations."""
    assert issubclass(T.am.InsufficientEvidence, AssertionError)
    assert not issubclass(T.am.InsufficientEvidence, T.ref.Refused)
    assert not issubclass(T.am.InsufficientEvidence, (OSError, ValueError))


def test_injected_estimator_refusal_escapes_main_uncaught():
    cell = T.drive_module(T.frozen, inject=T._injected())
    assert cell["exit"] == exit_contract.FAIL, cell
    assert cell["is_insufficient_evidence"], cell
    assert cell["refused_at"] is None and cell["reason"] is None, cell
    assert "InsufficientEvidence" in cell["raised"], cell
    assert "injected #1905 control" in cell["raised"], cell


def test_plain_assertion_error_is_an_execution_error_not_a_refusal():
    """The issue's edge case, on the unchanged handler: an `AssertionError`
    that is not an estimator refusal is nobody's refusal -- exit 1, and no
    `refused.json`."""
    def plain(*args, **kwargs):
        raise AssertionError("plain assertion, not an estimator refusal")
    cell = T.drive_module(T.frozen, inject=plain)
    assert cell["exit"] == exit_contract.FAIL and cell["reason"] is None, cell
    assert not cell["is_insufficient_evidence"] and "AssertionError" in cell["raised"], cell


@pytest.mark.parametrize("name", ["nan_one_sample", "inf_one_sample"])
def test_non_finite_plugin_output_is_refused_by_the_render_gate(name):
    make = T.FIXTURES[name]["make"]
    cell = T.drive_module(make)
    assert cell["exit"] == exit_contract.REFUSED and cell["raised"] is None, cell
    assert "_render_segment" in cell["refused_at"] and "NaN or infinity" in cell["refused_at"], cell
    leaked = T.drive_module(make, bypass_render_gate=True)
    assert leaked["exit"] == exit_contract.FAIL and leaked["is_insufficient_evidence"], leaked
    assert "NonFiniteAudio" in leaked["raised"] and "rms_envelope" in leaked["raised"], leaked


@pytest.mark.parametrize("name", sorted(STOPPED_IN_RENDER_SEGMENT))
def test_render_gate_output_is_refused_by_name(name):
    cell = T.drive_module(T.FIXTURES[name]["make"])
    assert cell["exit"] == exit_contract.REFUSED and cell["raised"] is None, cell
    assert "_render_segment" in cell["refused_at"], cell
    assert STOPPED_IN_RENDER_SEGMENT[name] in cell["refused_at"], cell


@pytest.mark.parametrize("name", STOPPED_IN_EVENT_ANALYSIS)
@pytest.mark.parametrize("bypass", [False, True], ids=["real gates", "render gate bypassed"])
def test_estimator_refusal_reaches_main_as_refused_not_traceback(name, bypass):
    """Every fixture that gets past the render gates is stopped by a named
    `Refused` in `_event_analysis`, with or without those gates."""
    cell = T.drive_module(T.FIXTURES[name]["make"], bypass_render_gate=bypass)
    assert cell["exit"] == exit_contract.REFUSED and cell["raised"] is None, cell
    assert "_event_analysis" in cell["refused_at"], cell


def test_frozen_capture_still_passes_every_gate():
    """The known-good control: 36 real renders, exit 0 through the shipped gates."""
    assert T.drive_module(T.frozen)["exit"] == exit_contract.PASS
    assert T.drive_module(T.frozen, bypass_render_gate=True)["exit"] == exit_contract.PASS


def test_harmonic_signature_cannot_refuse_the_fundamental_after_refine_f0():
    """The structural reason, on numbers rather than on "no fixture raised":
    `windowed_tone_amplitude(x, f0).require()` refuses on silence, a frequency
    outside (0, Nyquist) or fewer than 12 periods, and `refine_f0` returning ok
    on the same window excludes all three."""
    for name in ("frozen_capture", "ideal_saw_0p5", "subharmonic_m30", "sine_at_pitch"):
        for wave in T.ref.WAVE_SETTINGS:
            for condition, index in T.WINDOWS:
                cell = T.drive_estimator(T.FIXTURES[name]["make"], wave, condition, index)
                assert cell["rv_measure"].startswith("refine_f0 ok"), (name, wave, condition, cell)
                assert cell["fundamental_ok"] and cell["fundamental_periods"] >= 100, cell
                assert 0 < cell["f0"] < T.SR / 2, cell
                assert "RAISE" not in cell["rv_measure"], cell
                assert "RAISE" not in cell.get("inharmonic_fraction_db", ""), cell


def _real_cli(seam: str, out: pathlib.Path) -> subprocess.CompletedProcess:
    """`python tools/measure_mono_attack_context.py --out <out>` in a child
    interpreter, with `seam` installed first: it names the playback source and
    whatever else the test faults. `run_path` under `__main__` is what a direct
    invocation runs, so the exit status is the process's own."""
    src = ("import sys, runpy\n"
           f"sys.path[:0] = {[str(ROOT / p) for p in ('model', 'tools', 'tools/probes')]!r}\n"
           "import mono_attack_context_refusal_trace as T\n"
           "import measure_mono_m5a_reference as ref, audio_measure as am, subprocess\n"
           f"{seam}\n"
           "ref.rr.MiniV3Rig = lambda block=ref.BLOCK: T._PlaybackRig(SOURCE)\n"
           "ref._plugin_metadata = lambda: {'fixture': 'child interpreter'}\n"
           "ref._qualify_reference_integrity = lambda: {'fixture': 'child interpreter'}\n"
           "ref._source_provenance = lambda: {'commit': 'fixture', 'files_sha256': {}}\n"
           "subprocess.check_output = lambda *a, **k: ''\n"
           "runpy.run_path(%r, run_name='__main__')\n"
           % str(ROOT / "tools" / "measure_mono_attack_context.py"))
    return subprocess.run([sys.executable, "-c", src, "--out", str(out)],
                          capture_output=True, text=True, timeout=300, cwd=str(ROOT))


def test_real_cli_exits_1_with_a_traceback_when_an_estimator_refuses(tmp_path):
    """The census row, on the process: an `InsufficientEvidence` out of
    `rv.measure` is not caught by `:111`, and the interpreter exits 1."""
    seam = ("SOURCE = T.frozen\n"
            "def _refuse(*a, **k):\n"
            "    raise am.InsufficientEvidence('harmonic_signature: injected #1905 control')\n"
            "am.harmonic_signature = _refuse\n")
    p = _real_cli(seam, tmp_path)
    assert p.returncode == exit_contract.FAIL, (p.returncode, p.stdout[-300:], p.stderr[-600:])
    assert "Traceback" in p.stderr, p.stderr[-600:]
    assert "audio_measure.InsufficientEvidence: harmonic_signature: injected" in p.stderr
    assert "REFUSED" not in p.stdout
    assert not (tmp_path / "refused.json").exists() and not (tmp_path / "report.json").exists()


def test_real_cli_refuses_non_finite_plugin_output_by_name(tmp_path):
    """The same process on the one input that reaches an `InsufficientEvidence`
    family: `_render_segment` names it, exit 2, `refused.json` written."""
    p = _real_cli("SOURCE = T.FIXTURES['nan_one_sample']['make']\n", tmp_path)
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stdout[-300:], p.stderr[-600:])
    assert "REFUSED: plugin output contains NaN or infinity" in p.stdout, p.stdout[-300:]
    assert "Traceback" not in p.stderr
    assert json.loads((tmp_path / "refused.json").read_text()) == {
        "state": "REFUSED", "reason": "plugin output contains NaN or infinity"}
    assert not (tmp_path / "report.json").exists()


def test_full_trace_is_unreachable_behind_the_render_gate():
    result = T.trace()
    assert result["state"] == "UNREACHABLE_BEHIND_RENDER_GATE", result["state"]
    assert result["escapes_through_shipped_gates"] == [], result["escapes_through_shipped_gates"]
    assert result["escapes_only_when_render_gate_bypassed"] == ["nan_one_sample", "inf_one_sample"]
    assert result["control"]["exit"] == exit_contract.FAIL, result["control"]
    assert result["control"]["is_insufficient_evidence"], result["control"]
    assert {row["fixture"] for row in result["rows"]} == set(T.FIXTURES)
    assert all(row["module"]["exit"] in (exit_contract.PASS, exit_contract.REFUSED)
               for row in result["rows"]), [r["module"] for r in result["rows"]]


def test_probe_refuses_without_the_frozen_capture(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(T, "CAPTURE", tmp_path)
    assert T.main(["--only", "silence"]) == exit_contract.REFUSED
    out = capsys.readouterr().out
    assert "VERDICT: REFUSED" in out and "incomplete" in out, out


def test_probe_is_no_verdict_when_the_injected_control_is_not_seen(monkeypatch):
    def not_an_estimator_refusal(*args, **kwargs):
        raise T.ref.Refused("stub: the injected control did not raise InsufficientEvidence")
    monkeypatch.setattr(T, "_injected", lambda: not_an_estimator_refusal)
    result = T.trace({"silence": T.FIXTURES["silence"]})
    assert result["state"] == "NO-VERDICT", result
    assert result["control"]["exit"] == exit_contract.REFUSED, result["control"]
    assert not result["control"]["is_insufficient_evidence"], result["control"]
    assert T.main(["--only", "silence", "--json"]) == exit_contract.FAIL


def test_trace_names_a_reachable_route_rather_than_explaining_it_away(monkeypatch):
    """The committed input that defeats the guard (verification-rules rule 8):
    with one real fixture made to exit 1 through the shipped gates, the verdict
    must say REACHABLE and name it -- not fold it into 'unreachable'."""
    real = T.drive_module
    escaped = dict(exit=1, refused_at=None, reason=None, raised="RAISE InsufficientEvidence",
                   is_insufficient_evidence=True)

    def drive(make, **kw):
        return real(make, **kw) if kw.get("bypass_render_gate") else escaped
    monkeypatch.setattr(T, "drive_module", drive)
    result = T.trace({"silence": T.FIXTURES["silence"]})
    assert result["state"] == "REACHABLE", result
    assert result["escapes_through_shipped_gates"] == ["silence"], result
    assert T.main(["--only", "silence"]) == exit_contract.FAIL
