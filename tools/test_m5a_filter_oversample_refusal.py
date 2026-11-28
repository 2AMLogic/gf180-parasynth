#!/usr/bin/env python3
"""#1902: where an estimator's own refusal goes in the M5A ladder rate
comparison (`tools/measure_m5a_filter_oversample.py`), and which inputs can
send it there.

`audio_measure.InsufficientEvidence` subclasses AssertionError, not
`refusal.Refused`, so the dispatch handler `except refusal.Refused` cannot see
an estimator refusing. The #1782 census listed this CLI as a candidate: the
call at `run` -> `mono_m5a_score.measure` -> `audio_measure.harmonic_signature`
under a `Refused`-only handler. A census row is a candidate, not a defect; this
file is the row RUN, red-first on main @ 8f3d8ed2 (2026-11-28), through the
real `__main__` (runpy).

WHAT THE RUNS FOUND, all on the real estimators:

  the handler lets the class through
      `InsufficientEvidence` raised at the `measure` seam left `main` as exit
      1 with a traceback at the census site, `run` (:255). Recorded here as
      the red of the fix; the seam row below now pins exit 2.

  the accepted input never reaches it
      The CLI's only argument is `--out`; everything else it scores is the
      frozen M5A record and the model's own render through three fixed voice
      factories. The whole tool was run once, unmodified (146 s, exit 0), with
      every `refine_f0` and `harmonic_signature` call recorded at the point of
      use: 48 and 144 calls, all answered. Each event window is 19,200 samples
      (0.4 s) at MIDI 84 or 96, so the fundamental spans 418.6 or 837.3
      periods against `windowed_tone_amplitude`'s minimum of 12, and the
      quietest window handed to `harmonic_signature` -- output, reference,
      oscillator, mixer or ladder -- peaked 164 dB above the 1e-9 silence
      floor. Nothing an operator can pass changes those margins.

  a dead stage is already a refusal
      What a regression looks like at the estimator is a silent render. With
      the voice silenced at `VoiceFx.play` the output window is silent too,
      and `measure` refuses it at `refine_f0` (`mono_m5a_score.py:457-460`)
      before any harmonic estimate: REFUSED, exit 2, named. The same was run
      once through this CLI with the oscillator seam (`_render_2x` and
      `OscFx.render`) and both ladders (`fixed.LadderFx.process`,
      `filter_rate_chain.RateConvertedLadder.process`) returning zeros: both
      exit 2, `REFUSED: saw MIDI 84 pitch refused: silent`. Pinned here on
      the voice seam, with `harmonic_signature` counted at zero calls.

  the one un-gated call: a stage window
      `_stage_diagnostics` (`mono_m5a_score.py:219`) hands `harmonic_signature`
      the oscillator, mixer and ladder slices with no `refine_f0` of their
      own, so a silent STAGE window under a sounding output raises
      `InsufficientEvidence` there, and it left this CLI as exit 1 with a
      traceback at `:255` -> `:491` -> `:219` -> `audio_measure.py:2379`. Run
      two ways, both exit 1: with the reference phrase as the output and a
      zeroed oscillator trace, and with the tool's own production render as
      the output and one stage's recorded window overwritten with zeros
      (oscillator, mixer, ladder; each run separately).

      The production renderer does not produce that condition: the traced
      arrays ARE the signal path (`voice_fx.py:1543-1574`), so a stage silent
      over the window means the output is silent over it, and the pitch gate
      refuses first -- which is what the dead-stage runs show. It takes a
      trace that disagrees with its own output. The handler now names the
      class so that state is REFUSED (2) rather than FAIL (1), which is what
      an evidence refusal is wherever it arises (#1751, #1763).

WHAT THE TOOL OWES, and what these rows assert:

  Refused from `measure`          exit 2, the reason by name, no report
  InsufficientEvidence, any path  exit 2, `REFUSED: harmonic_signature:
                                  silent`, no traceback, no report
  plain AssertionError            exit 1 with its message in the traceback:
                                  a programming error is not a refusal
  a silent voice                 exit 2 from the pitch gate, zero harmonic
                                  estimates taken
  the production voices           every stage window the un-gated call
                                  reads holds evidence, for all three
                                  factories the tool renders with; the same
                                  call on a zeroed stage slice raises

WRONG-THEN-RIGHT (2, both in the apparatus):

  1. Caught by running. The first full run passed `--out /tmp/...`. The tool
     measured for two and a half minutes, wrote the report, then raised
     ValueError from `out.relative_to(ROOT)` because the path is outside the
     checkout. The trace's own record was lost with the process; the harness
     now writes under `build/` and dumps its record in `finally`. The defect
     is this tool's instance of the shape #1584 tracks, and is left to it.
  2. Caught by reading, then confirmed by a probe run. The #1763 oscillator
     seam (`OscFx.render` alone) does not take on this tool's voices: the 2x
     saw path computes the waveform in `_render_2x` and calls `render` only
     without BLEP. With only that seam, a saw note through the tool's own
     factory still peaked at 10200 while a pulse note fell to 0 -- on this
     CLI the first segment is the saw, so the tool would have measured on and
     the run would have read as a verdict. Every seam here renders a probe
     through the tool's own factory and asserts the fault is present before
     the tool runs.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "model"))

import audio_measure as am                              # noqa: E402
import exit_contract                                   # noqa: E402
import mono_m5a_score as m5a                           # noqa: E402
import refusal                                         # noqa: E402
import voice_fx as vf                                  # noqa: E402
import measure_m5a_filter_oversample as tool           # noqa: E402

#: what a seam says when it has not taken: reported as that, never as a
#: verdict about the tool
SEAM_FAILED = "SEAM FAILED"
FAULT = "plain assertion control"
STAGES = ("oscillator", "mixer", "ladder")
MODES = ("production_2x_sample_hold", "reconstructed_2x", "reconstructed_4x")
CASES = (("saw", "pulse29"), ("pulse", "pulse29"), ("pulse", "pulse479"))
PITCH_GATE = "REFUSED: saw MIDI 84 pitch refused: silent"
ESTIMATOR = "REFUSED: harmonic_signature: silent"

TOOL = ROOT / "tools" / "measure_m5a_filter_oversample.py"
_CHILD_PATHS = "sys.path[:0] = %r\n" % [str(ROOT / p) for p in ("model", "tools")]

VOICE_SILENT = (
    "import numpy as np, voice_fx as vf\n"
    "def _play(self, regs, writes, n):\n"
    "    self.trace = dict(osc=[np.zeros(n, dtype=np.int64)],\n"
    "                     mixed=np.zeros(n, dtype=np.int64),\n"
    "                     ladder=np.zeros(n, dtype=np.int64))\n"
    "    return np.zeros(n, dtype=np.int16)\n"
    "vf.VoiceFx.play = _play\n"
    "import measure_m5a_filter_oversample as tool\n"
    "for _mode in %r:\n"
    "    _pcm = vf.render_mono_fx([(0.0, 84, 0.3, {})], 0.3, tool._voice_factory(_mode)())\n"
    f"    assert not np.any(_pcm), {SEAM_FAILED + ': the voice still renders'!r}\n"
    "del _pcm\n") % (MODES,)


def _measure_raises(exception: str) -> str:
    return ("import mono_m5a_score as m5a\n"
            "def _measure(*args, **kwargs):\n"
            f"    raise {exception}\n"
            "m5a.measure = _measure\n")


def _silent_stage(stage: str) -> str:
    """Child-interpreter source: the first render the tool scores returns the
    frozen phrase itself as its output -- a valid output by construction, it
    is the reference -- with `stage` silent over the first event's window and
    the other stages carrying that output. Later renders are the real ones."""
    return (
        "import numpy as np, voice_fx as vf, mono_m5a_score as m5a\n"
        "ref = m5a.load_reference('M5A')\n"
        "seg = ref.manifest['timeline']['segments'][0]\n"
        "ev = seg['midi_events'][0]\n"
        "pcm = np.rint(ref.pcm[:seg['samples']] * 32767).astype(np.int16)\n"
        "a = int((float(ev['on_s']) + 0.12) * m5a.SR)\n"
        "b = int((float(ev['on_s']) + float(ev['gate_s']) - 0.08) * m5a.SR)\n"
        "stages = {'oscillator': pcm.copy(), 'mixer': pcm.copy(), 'ladder': pcm.copy()}\n"
        f"stages[{stage!r}][a:b] = 0\n"
        f"assert not np.any(stages[{stage!r}][a:b]) and np.any(pcm[a:b]), "
        f"{SEAM_FAILED + ': the stage window is not silent under a sounding output'!r}\n"
        "armed = [True]\n"
        "_real = vf.render_mono_fx\n"
        "def _render(seq, duration, voice):\n"
        "    if not armed[0]:\n"
        "        return _real(seq, duration, voice)\n"
        "    armed[0] = False\n"
        "    voice.trace = {'osc': [stages['oscillator']], 'mixed': stages['mixer'],\n"
        "                   'ladder': stages['ladder']}\n"
        f"    print('SEAM took: silent {stage} window, output peak', int(np.max(np.abs(pcm[a:b].astype(np.int64)))))\n"
        "    return pcm\n"
        "vf.render_mono_fx = _render\n")


def _handoff(seam: str, report: str) -> list[str]:
    """`python -c` with `seam` installed first, then the tool's real
    `__main__` with a direct invocation's argv. `report` is relative to the
    checkout, because the tool resolves `--out` against its own root."""
    script = (f"import sys, runpy\n{_CHILD_PATHS}\n{seam}"
              f"sys.argv = [{str(TOOL)!r}, '--out', {report!r}]\n"
              f"runpy.run_path({str(TOOL)!r}, run_name='__main__')\n")
    return [sys.executable, "-c", script]


def _run(argv: list[str], report: pathlib.Path) -> subprocess.CompletedProcess:
    if report.exists():
        report.unlink()
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=300, cwd=str(ROOT))
    assert SEAM_FAILED not in proc.stdout + proc.stderr, (proc.stdout + proc.stderr)[-800:]
    return proc


def _assert_refused(proc: subprocess.CompletedProcess, reason: str, report: pathlib.Path) -> None:
    assert proc.returncode == exit_contract.REFUSED, (proc.stdout, proc.stderr)
    assert f"REFUSED: {reason}" in proc.stdout.splitlines(), proc.stdout
    assert "Traceback" not in proc.stdout + proc.stderr
    assert not report.exists()


def _voice_silent_in_process(monkeypatch) -> None:
    def _play(self, regs, writes, n):
        self.trace = dict(osc=[np.zeros(n, dtype=np.int64)],
                          mixed=np.zeros(n, dtype=np.int64),
                          ladder=np.zeros(n, dtype=np.int64))
        return np.zeros(n, dtype=np.int16)

    monkeypatch.setattr(vf.VoiceFx, "play", _play)
    for mode in MODES:
        pcm = vf.render_mono_fx([(0.0, 84, 0.3, {})], 0.3, tool._voice_factory(mode)())
        assert not np.any(pcm), f"{SEAM_FAILED}: {mode} still renders"


def _manifest_patch() -> dict:
    return m5a._voice_patch(json.loads(m5a.MANIFEST.read_text()))


def _saw_cutoff() -> int:
    return int(round(json.loads(m5a.MANIFEST.read_text())
                     ["patch"]["cutoff_measurement"]["f0_hz"]))


def _render(mode: str, wave: str, pulse_shape: str):
    """One 0.6 s note at MIDI 84 through the tool's own factory and the
    scorer's own patch; returns (voice, pcm, start, stop) with the window
    taken by the formula `measure` applies to an event at 0.0 s."""
    segment = m5a._patch_for_wave(_manifest_patch(), wave, pulse_shape)
    if wave == "saw":
        segment = {**segment, "cutoff": (_saw_cutoff(), _saw_cutoff())}
    voice = tool._voice_factory(mode)()
    pcm = vf.render_mono_fx([(0.0, 84, 0.6, {**segment, "gate": 0.6})], 0.62, voice)
    start, stop = int((0.0 + 0.12) * m5a.SR), int((0.0 + 0.6 - 0.08) * m5a.SR)
    return voice, pcm, start, stop


def _refined(pcm: np.ndarray, start: int, stop: int):
    gate = am.refine_f0(pcm[start:stop].astype(np.float64) / 32768.0, vf.note_hz(84), m5a.SR)
    assert gate.ok, f"{SEAM_FAILED}: the output window is not a note ({gate.reason})"
    return gate.value


def test_refused_from_the_measure_seam_is_refused_2_and_writes_no_report(
        monkeypatch, tmp_path, capsys):
    def _measure(*args, **kwargs):
        raise refusal.Refused("control refusal")

    monkeypatch.setattr(m5a, "measure", _measure)
    monkeypatch.setattr(tool, "ROOT", tmp_path)
    code = tool.main(["--out", "result.json"])
    assert code == exit_contract.REFUSED
    assert capsys.readouterr().out.splitlines() == ["REFUSED: control refusal"]
    assert not (tmp_path / "result.json").exists()


def test_insufficient_evidence_from_the_measure_seam_is_refused_2(
        monkeypatch, tmp_path, capsys):
    def _measure(*args, **kwargs):
        raise am.InsufficientEvidence("harmonic_signature: silent")

    monkeypatch.setattr(m5a, "measure", _measure)
    monkeypatch.setattr(tool, "ROOT", tmp_path)
    code = tool.main(["--out", "result.json"])
    assert code == exit_contract.REFUSED
    assert capsys.readouterr().out.splitlines() == [ESTIMATOR]
    assert not (tmp_path / "result.json").exists()


def test_a_silent_voice_is_stopped_by_the_pitch_gate_before_any_harmonic_estimate(
        monkeypatch, tmp_path, capsys):
    _voice_silent_in_process(monkeypatch)
    monkeypatch.setattr(tool, "ROOT", tmp_path)
    harmonic_calls = []
    real = am.harmonic_signature

    def _counting(*args, **kwargs):
        harmonic_calls.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(am, "harmonic_signature", _counting)
    code = tool.main(["--out", "result.json"])
    assert code == exit_contract.REFUSED
    assert capsys.readouterr().out.splitlines() == [PITCH_GATE]
    assert harmonic_calls == []
    assert not (tmp_path / "result.json").exists()


@pytest.mark.parametrize("stage", STAGES)
def test_a_valid_output_with_a_silent_stage_window_exits_refused(stage):
    report = ROOT / "build" / f"i1902-silent-{stage}.json"
    proc = _run(_handoff(_silent_stage(stage), str(report.relative_to(ROOT))), report)
    assert f"SEAM took: silent {stage} window" in proc.stdout, proc.stdout
    _assert_refused(proc, "harmonic_signature: silent", report)


def test_a_silent_voice_through_the_real_main_exits_refused_from_the_pitch_gate():
    report = ROOT / "build" / "i1902-silent-voice.json"
    proc = _run(_handoff(VOICE_SILENT, str(report.relative_to(ROOT))), report)
    _assert_refused(proc, "saw MIDI 84 pitch refused: silent", report)


def test_a_plain_assertion_error_from_the_measure_seam_is_still_exit_1():
    report = ROOT / "build" / "i1902-assertion.json"
    proc = _run(_handoff(_measure_raises(f"AssertionError({FAULT!r})"),
                         str(report.relative_to(ROOT))), report)
    assert proc.returncode == exit_contract.FAIL, (proc.stdout, proc.stderr)
    assert "Traceback" in proc.stderr, proc.stderr
    assert FAULT in proc.stderr, proc.stderr
    assert "REFUSED" not in proc.stdout, proc.stdout
    assert not report.exists()


@pytest.mark.parametrize(("wave", "pulse_shape"), CASES)
@pytest.mark.parametrize("mode", MODES)
def test_every_stage_window_holds_evidence_for_the_voices_the_tool_renders(mode, wave, pulse_shape):
    voice, pcm, start, stop = _render(mode, wave, pulse_shape)
    f0 = _refined(pcm, start, stop)
    stages = m5a._stage_diagnostics(voice.trace, pcm, start, stop, f0)
    assert set(stages) == {"oscillator", "mixer", "ladder", "output"}
    for name, row in stages.items():
        assert np.isfinite(row["rms_dbfs"]), (name, row)


@pytest.mark.parametrize("stage", STAGES)
def test_the_same_call_on_a_silent_stage_slice_raises(stage):
    voice, pcm, start, stop = _render("production_2x_sample_hold", "saw", "pulse29")
    f0 = _refined(pcm, start, stop)
    trace = voice.trace
    silent = {"oscillator": trace["osc"][0], "mixer": trace["mixed"], "ladder": trace["ladder"]}[stage]
    silent[start:stop] = 0
    assert not np.any(silent[start:stop]), f"{SEAM_FAILED}: {stage} window still holds samples"
    with pytest.raises(am.InsufficientEvidence, match="harmonic_signature: silent"):
        m5a._stage_diagnostics(trace, pcm, start, stop, f0)
