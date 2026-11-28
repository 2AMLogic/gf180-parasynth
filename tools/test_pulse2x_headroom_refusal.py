#!/usr/bin/env python3
"""Known-answer exit-status controls: a refusal raised inside
`mono_m5a_score.measure` must reach the pulse2x headroom tool's exit contract
as REFUSED, never as an uncaught exit-1 traceback (#1613).

THE DEFECT, AS MEASURED ON main @ 0a6b0861 (2026-11-28, red-first run of
every control in this file): `tools/measure_pulse2x_headroom.py` carried no
`refusal` import and no `except` at all. `phrases()` calls
`mono_m5a_score.measure`, which raises its module's `Refused` from 23 sites
(16 in `measure`, 7 in `load_reference`; counted on that tree), and a refusal
left the process as exit 1 with a traceback: a missing precondition rendered
as FAIL, the inversion the exit contract exists to prevent
(tools/exit_contract.py). The repair widens the CLI boundary in `main()` to
`refusal.Refused`, the handler shape of #1183
(tools/measure_repeatability.py) and #1162 (tools/test_rs_guard_band_refusal.py).
Red-first on that tree: 7 of the 9 controls failed, each with the process at
exit 1 and the `mono_m5a_score.Refused` traceback in stderr; the class
relation and the ValueError control were green, as they must be on any tree.

THE KNOWN ANSWERS, independent of anything this repository models:

  silenced pulse   a candidate substep gain of exactly 0 leaves every pulse
                   segment digitally silent, and a silent segment has no pitch
                   to estimate: the scorer must refuse "pulse MIDI 84 pitch
                   refused: silent". Run through the real file against the real
                   frozen M5A record, which scores and prints the baseline
                   phrase first -- the partial-output case, where a
                   half-finished --out report is the hazard. 23 s on an
                   8-vCPU AWS worker (2026-11-28).
  altered record   a frozen M5A reference WAV that no longer hashes to its
                   manifest must be refused "frozen M5A Mini V3 audio hash
                   mismatch" by `load_reference`, before any render.

WHAT THE TOOL OWES, and what these controls assert:

  phrases          both known answers exit `exit_contract.REFUSED` (2) with
                   exactly one line, `REFUSED  <reason>`, no traceback and no
                   --out file
  every mode       the handler sits at the `main()` dispatch, not inside
                   `phrases()`: a refusal raised by the decimator / fine / mix
                   / probe entry function reaches the same exit 2
  the --out write  a refusal raised while the report is written is exit 2 as
                   well (structural: nothing on the write path refuses today)
  unrelated errors a ValueError from `score.measure` at the same call is
                   still exit 1 with its traceback -- widening the handler to
                   the base class must not relabel an execution error as a
                   refusal (tools/refusal.py's "inverse defect"). This control
                   is green on the pre-fix tree and must stay green.

HOW THE INJECTIONS ARE MADE. The silenced pulse is a real command line. The
altered record is real bytes read by the real `load_reference`; only its
LOCATION is redirected (`mono_m5a_score.MANIFEST` and `MANIFESTS["M5A"]`, the
module's own documented override). The other controls have no command-line
input that reaches them, so each runs the real `main()` in a real interpreter
after replacing one attribute (`score.measure`, the mode's entry function,
`pathlib.Path.write_text`); the process exit status and the traceback
rendering are still the real ones.

EVERY ASSERTION PINS THE MESSAGE, not just the code: an exit code alone is
satisfied by a crashed-and-caught-anything handler, and the exact reason is
each control's own precondition check. Were the frozen record absent or
altered in the tree, the silenced-pulse run would refuse EARLIER, with the
hash-mismatch reason, and this file would turn red instead of passing for the
wrong reason.

THE SECOND DEFECT, #1628: `probe` on a row that did not measure. `probe_points`
reads five numbers out of each `mono_artifact_probe.measure_point` row, and
`measure_point` ANSWERS a render it cannot analyse with a verdict row instead
of an exception -- other callers consume that row -- so the `main()` boundary
above never saw it. On origin/main @ a3186508 (2026-11-28, after #1630 landed
the boundary) `probe --gains 0.0 --out F` (q15(0) = 0: the candidate silences
every rectangle) exited 1 with `KeyError: 'output'`, and the same reader,
asked through the Python API for MIDI 12, raised `KeyError: 'unwanted_dbfs'`
-- though that row's verdict is MEASURED, which is why a verdict check alone
is not the guard (docs/verification-rules.md rule 8: the input that defeats it
is committed below, and it is a real row from the real engine). `probe_points`
now reads only through `_measured_numbers`, which returns the five numbers or
raises the tool's own `Refused`.

WHAT THESE CONTROLS ASSERT, each pinning the message:

  silenced rectangles  the real CLI: exit 2, exactly
               `REFUSED  square MIDI 36: candidate gain 0.000 silences the
               output (-300.0 dBFS)`, no traceback, no --out file
  first point   the refusal names the first point in iteration order that did
               not measure (read off the loop: MIDI 60 when asked for 60, 84)
  refused stage  MIDI 12 (f0 16.35 Hz = 12.3 bins of the 36000-sample
               window; the floor is 16): MEASURED, every stage refused; the
               probe refuses with the output stage's own text. Its
               precondition -- that the real row HAS that shape -- is asserted
               first, so the control cannot pass for another reason
  normal gain   `probe --gains 0.80 --out F` still measures all 32 points and
               writes F (exit 0, no REFUSED line, every value numeric, the
               candidate applied at least once): the guard must stay quiet on
               a run that measured. The real CLI, unpatched, because the
               shipped default grid is what it must let through; 73 s
               single-threaded on an 8-vCPU AWS worker (2026-11-28)
  other branches, on rows written key by key to the contract `measure_point`
               returns rather than to the guard's own constant (no render): a
               non-MEASURED verdict that is not silence, a verdict missing, an
               output stage short of a field, a row short of the rail count --
               each refused with the missing thing named; and a measured row
               passes through unchanged, including one whose `ladder_out`
               stage refused (the guard requires what it reads, no more).
               The three rendered controls above anchor that contract to the
               real producer; these do not stand in for them.

RED FIRST, on a3186508 with the tool unmodified: 10 of the 11 #1628 controls
failed. The three defect controls failed with the defect -- the silenced run
at exit 1 with `KeyError: 'output'` (`measure_pulse2x_headroom.py:194`), the
first-point control with the same `KeyError`, the refused-stage control with
`KeyError: 'unwanted_dbfs'` (`:196`) after its precondition on the real MIDI
12 row passed. The class relation and the six guard controls failed with
`AttributeError` (no `Refused`, no `_measured_numbers` yet). The normal-gain
control passed on that tree, as it must on any tree.

NOT CLAIMED: that the refused-stage path is reachable from the CLI. `probe`
takes no `--notes` and its default grid (36..127) sits far above the floor;
that path belongs to a Python caller.

#1693, THE THIRD DEFECT, AS MEASURED ON main @ 3e2534c7 (2026-11-28): the
boundary above was right, but three of the tool's own entry functions did not
raise a refusal at a stated precondition. `decimator --gains 0.0` and `fine
--gains 0.0` raised `RuntimeError("... silent decimator output")` from
`decimator_run`: exit 1 with a traceback, a precondition rendered as FAIL. `mix
--gains 0.0` completed with exit 0 and a report whose ten `0.000/three-rect/*`
rows read `output_rms_dbfs: -240.0` -- a 1e-12 floor printing digital silence
as a measurement; under `--scope rect` the default preset's two saws are
untouched, so nothing stopped the run.

THE KNOWN ANSWERS for those, independent of anything this repository models:

  zero gain       a substep gain of exactly 0 is digital silence at the
                  decimator and at the mixer output. `decimator` refuses at its
                  first shape and note, `fine` at its first reachable shape and
                  grid point, `mix` at its first rectangle row -- after the
                  baseline rows and the candidate's saw rows, which measure:
                  the partial-output hazard again, so no report may remain.
  a square wave   +/-1 LSB has an RMS of exactly 1 LSB, -90.31 dBFS, 0.3 dB
                  below mono_artifact_probe's -90.0 activity floor; +/-1.1 LSB
                  is -89.48 dBFS, 0.5 dB above it. The floor is read from the
                  probe's module at the call, so moving that attribute moves
                  the tool's.
  NaN             every IEEE comparison with NaN is False (#134), so a guard
                  written `level < floor` lets a NaN level through: the rule-8
                  adversary for the new floor, committed as a control.
  quiet, not      a Q15 gain of 28 puts the three-rectangle mix at MIDI 60 at
  silent          -74.7 dBFS on that tree, 58 dB under its baseline and 15 dB
                  above the floor: it must still measure.
  --scope all     applies the gain to the saws too, so the default preset is
                  silent as well: the floor applies to every preset's rows and
                  the refusal names that preset.

The pre-saturation `RuntimeError` in `decimator_run` ("recorded pre-saturation
samples do not produce the output") is an execution error, not a precondition,
and has a stays-green control here like the ValueError one: exit 1 with its
traceback.

#1908, A CANDIDATE TRACED AND REFUTED (2026-11-28, origin/main @ e2d1bae7): the
#1782 census listed this tool because `phrases()` calls `mono_m5a_score.measure`
under `refusal.Refused` alone while `measure` reaches
`audio_measure.harmonic_signature` unguarded -- per event, and inside
`_stage_diagnostics` on the oscillator / mixer / ladder / output stage taps --
and `InsufficientEvidence` is an `AssertionError`, not a `Refused`. Injected,
the escape is real and is at exactly that site: an `InsufficientEvidence`
raised at `score.measure`, and a silent oscillator tap beside a live output,
both leave as exit 1 with the `audio_measure.InsufficientEvidence` traceback
(`_stage_diagnostics` <- `harmonic_signature`, "harmonic_signature: silent").
Through the real CLI it is UNREACHABLE, so the tool is unchanged -- the outcome
the issue names. 21 command lines were run against the unmodified file
(tools/probes/pulse2x_headroom_insufficient_evidence_trace.py): zero, the
smallest non-zero Q15 gain, negative, large, int64-overflowing and non-finite
`--gains`; both scopes; zero, tiny, negative, large and non-finite
`--pulse-drive-comp`. None raised either class. What stopped each, read off
the runs:

  anything that     the chain is serial with ONE mixed source and no noise
  silences the      (`_voice_patch`: mix (1, 0, 0) -> mixer weights [32768, 0,
  output            0, 0]; noise 0.0), and `candidate()` applies one gain to
                    every oscillator of a segment, so a zero gain silences the
                    oscillator, the mixer, the ladder and the output together
                    (--gains 0.0 and 1e-9, both Q15 0). A zero, tiny or
                    negative --pulse-drive-comp silences only the ladder, whose
                    gain register is 0 (measured: `patch_regs(...)["gain"]` is 0
                    at each; a negative drive clamps to 0), and the output with
                    it, while the oscillator and mixer stay live. Either way
                    `refine_f0` on the OUTPUT slice refuses first
                    (`mono_m5a_score.py`, `pitch refused:`), before
                    `_stage_diagnostics` reads a tap, and `Refused` is what the
                    handler catches: exit 2, "pulse MIDI 84 pitch refused:
                    silent". Under --scope all the saw segment comes first and
                    the same gate refuses "saw MIDI 84 pitch refused: silent".
  a tiny signal     --gains 2e-5 (Q15 1) is NOT silence -- every stage tap is
  with no pitch     live -- and `refine_f0` -> `dominant_frequency` refuses
                    "no prominent line in the band": exit 2. The gate is a
                    pitch estimator, not only a silence check.
  an inverted or    --gains -0.85 / -1.0 / 2.0 / 100.0 and --pulse-drive-comp
  overdriven one    1e6: every stage tap measures, exit 0.
  a non-finite or   --gains nan / inf / 1e15 and --pulse-drive-comp nan / inf
  overflowing       exit 1 with ValueError / OverflowError out of
  float             `int(round(...))` in `q15()` and `LadderFx.regs_unclamped`:
                    an execution error of another class. Recorded; not a
                    refusal, and not this candidate. `-inf` is not an accepted
                    input at all: argparse reads it as a flag and exits 2 with
                    a usage error -- the same status as REFUSED, with no
                    REFUSED line.

Two routes are closed by construction rather than by a gate. `NonFiniteAudio`
needs a non-finite sample, and every array `measure` hands an estimator is an
integer render divided by 32768 (`render_mono_fx` returns int16; the stage
taps are int64). `harmonic_signature`'s `.require` on the fundamental needs
fewer than 12 periods in the window, and the frozen M5A / M5B grids hold at
least 209 (M5B saw MIDI 72, gate 0.6 s) -- a property of the manifest that no
CLI input moves. `harmonic_signature: no fundamental` needs `f0=None`, and
both call sites pass `f0=`.

WRONG, THEN RIGHT, in the instruments that produced the above (kept where
the numbers are read). The trace probe's first outcome parser matched
exception names ending in `Error` / `Exception` and so could not read
`audio_measure.InsufficientEvidence` -- the one class it exists to find --
and reported the injected control as "no traceback parsed"; that control is
what showed the probe blind, which is what a control is for. Its second draft
counted `-inf` among the exit-2 rows. And the one-note control below first
asserted that the four taps are silent TOGETHER or live together: its drive
rows refuted that -- oscillator and mixer live, ladder and output silent --
so the gate it pins is the output's silence beside any silent tap, not the
taps' agreement. Three wrong instruments, each caught by a control or an
explicit expectation rather than by reading.

WHAT THE #1908 CONTROLS PIN, so the refutation does not outlive its evidence:

  the escape,     an `InsufficientEvidence` at `score.measure`, and the silent
  injected        oscillator tap beside a live output, still exit 1 with the
                  traceback and no REFUSED line: the candidate's start-red,
                  kept committed. Both are TRUE on this tree and are the reason
                  the gates below carry the weight. Should the handler ever be
                  widened to `audio_measure.InsufficientEvidence` (as #1976
                  does for the sibling `measure_mono_pulse_2x`), these two flip
                  to exit 2 deliberately, in that diff.
  the gate, on    inside the candidate context `phrases()` itself builds -- its
  a one-note      own `voice_factory`, `candidate()` and `pulse_drive()` -- no
  render          accepted (gain, scope, drive) input leaves a stage tap silent
                  beside a LIVE output; which taps each input silences is
                  pinned with it. A short render, because the question is
                  about the stage arrays, not the phrase.
  the gate, on    the real CLI: --gains 2e-5 is REFUSED by name, exit 2, no
  the real CLI    report, after the baseline scored. The silenced-gain control
                  above (#1613) already pins the silent route. About 45 s.

NOT CLAIMED: that those 21 command lines are the whole input space. The
structural controls carry the claim -- the serial chain, the one mixed source,
the integer render -- and they assert on the model as it is; a noise source or
a per-oscillator gain in the pulse patch would leave a silent tap beside a
live output and redden them before any CLI run.

#2042, THE FOURTH DEFECT, AS MEASURED ON main @ 616b8d63 (2026-11-28): the
boundary caught refusals, but a number argparse accepts and the engine cannot
represent was never refused, because `q15` and `LadderFx.regs_unclamped` are
conversion helpers, not validators. `--gains nan|inf` exited 1 with
`ValueError` / `OverflowError` from `q15`; `--gains 1e15` exited 1 with numpy's
`OverflowError` inside `_render_2x`, in `mix` and `phrases` after the baseline
had scored; `--gains 1e14` was worse -- its Q15 word fits int64, `_render_2x`
wrapped silently, and every mode exited 0 with a report; `probe --gains 0.80
nan` measured all 32 points (it reads `gains[0]` alone); and `phrases --gains
0.85 --pulse-drive-comp <bad>` scored both baselines and exited 0 with the
multiplier never applied (0.85 is BASE_Q15, so there is no candidate). `main()`
now calls `_refuse_unrepresentable` before the dispatch.

THE KNOWN ANSWERS, from IEEE arithmetic and the model's own taps:

  not a float64   `g * 32768` is inf for the finite 1e308, before `round`:
                  a `math.isfinite(g)` guard alone is defeated by it.
  the int64 bound after `hi * gain >> 15` (|hi| <= 2**15) a sample is at most
                  |gain|, and the 31-tap convolve sums to `sum(|taps|)` =
                  53980 times that: |gain| <= (2**63 - 1) // 53980 =
                  170,866,469,745,364. Tight: 5.214e9 passes, 5.3e9 wraps.
                  1e14's word, 3.2768e18, is under 2**63 and over the bound:
                  an int64-fit guard alone is defeated by it, silently.
  the drive bound `int(round(drive * 0.13 / 0.05 * 65536))` overflows float64
                  above drive 1.055e303; the preset's 0.75 scales the flag.

WHAT THESE CONTROLS ASSERT, each pinning the message and `BASELINE_LINE not in
stdout`: 30 non-finite-gain rows and 20 int64 rows across all five modes; 5
`--pulse-drive-comp` rows at `--gains 0.85`; two rule-8 controls proving, in
process, that each obvious guard is defeated (the real `_render_2x` returns a
rail-pinned render for 1e14; the real `regs_unclamped` raises on the preset's
drive x 1e308); the premise the drive guard rests on (the preset's drive is
within its reference of 1.0, read from the real preset); and the stays-green
side -- the gains and multipliers the trace measured pass the guard, and
through the real `main()` they reach every mode's entry function as parsed.

RED FIRST, on 616b8d63 with the tool unmodified: 56 of the 64 controls fail.
30 non-finite rows: 29 at exit 1 with the `q15` traceback, `probe --gains 0.80
nan` at exit 0 (which `main()` reaches only after writing the report). 10
`1e15` rows: exit 1, in `mix` / `phrases` after "mix gain None:" / the M5A
baseline line had printed. 10 `1e14` rows: exit 0. 5 drive rows: exit 0.
`documented_accepted_numbers`: AttributeError, no guard yet. The other 8 pass,
as they must on any tree (the two rule-8 controls, the premise, and the five
valid-numbers rows). The ten `1e15` rows were re-pinned after the red run (the
value's repr); pre-fix they fail at the exit-code assertion, before the
message is read.

WRONG THEN RIGHT, three times, all in this file's own constants and all caught
by its controls before the fix was relied on: `q15(1e14)` was written
3276800000000000 for 3276800000000000000 and the rail-pinned render bounded
`<= 32767` where `sat16` reaches -32768 (both by the rule-8 control, run green
on the pre-fix tree), and `repr(1e15)` was written `1e+15` for
`1000000000000000.0` (by the message pin).
"""
from __future__ import annotations

import json
import math
import pathlib
import shutil
import subprocess
import sys

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import exit_contract                                     # noqa: E402
import refusal                                           # noqa: E402

TOOL = ROOT / "tools" / "measure_pulse2x_headroom.py"
FROZEN_M5A = ROOT / "docs/scorecard" / "mono-m5a-miniv3"
SILENT_GAIN = "0.0"          # q15(0.0) = 0, so `phrases` keeps it as a candidate
SILENT_REASON = "pulse MIDI 84 pitch refused: silent"
HASH_REASON = "frozen M5A Mini V3 audio hash mismatch"
BASELINE_LINE = "M5A/0.850 (pulse2x as built)"
#: every mode other than `phrases`, and the function `main()` dispatches it to
MODE_ENTRY = {"decimator": "decimator_sweep", "fine": "fine_sweep",
              "mix": "mix_check", "probe": "probe_points"}


def _cli(*argv: str) -> subprocess.CompletedProcess:
    """The real file in a real interpreter: the process a batch runner sees."""
    return subprocess.run([sys.executable, str(TOOL), *argv],
                          capture_output=True, text=True, timeout=600, cwd=str(ROOT))


def _run_patched(argv: list[str], patch: str) -> subprocess.CompletedProcess:
    """The real `main()` in a real interpreter, with `patch` executed after the
    tool is imported and before the dispatch."""
    code = ("import pathlib, sys\n"
            f"sys.path[:0] = [{str(ROOT / 'model')!r}, {str(ROOT / 'tools')!r}]\n"
            f"sys.argv = [{str(TOOL)!r}] + {argv!r}\n"
            "import measure_pulse2x_headroom as mph\n"
            f"{patch}\n"
            "raise SystemExit(mph.main())\n")
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          timeout=600, cwd=str(ROOT))


def _refused_lines(stdout: str) -> list[str]:
    return [line for line in stdout.splitlines() if line.startswith("REFUSED")]


def _assert_refused(p: subprocess.CompletedProcess, reason: str, out: pathlib.Path) -> None:
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stdout[-300:], p.stderr[-400:])
    assert _refused_lines(p.stdout) == [f"REFUSED  {reason}"], p.stdout[-400:]
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout, p.stderr[-400:]
    assert not out.exists(), "a refused run must leave no --out report behind"


# ---------------------------------------------------------------------------
# the class relation the whole fix rests on
# ---------------------------------------------------------------------------
def test_raiser_is_a_subclass_of_the_base_the_boundary_catches():
    import mono_m5a_score as score

    assert score.Refused is not refusal.Refused
    assert issubclass(score.Refused, refusal.Refused)


# ---------------------------------------------------------------------------
# phrases: the two known answers must REFUSE by name, not traceback
# ---------------------------------------------------------------------------
def test_silenced_pulse_gain_exits_refused_2_without_partial_report(tmp_path):
    out = tmp_path / "phrases.json"
    p = _cli("phrases", "--gains", SILENT_GAIN, "--out", str(out))
    _assert_refused(p, SILENT_REASON, out)
    assert BASELINE_LINE in p.stdout, (
        "the baseline phrase is scored before the candidate; a run that refused "
        "earlier did not reach the raising site")
    assert "wrote" not in p.stdout, p.stdout[-200:]


def test_altered_frozen_reference_exits_refused_2_before_any_render(tmp_path):
    manifest = json.loads((FROZEN_M5A / "manifest.json").read_text())
    audio = bytearray((FROZEN_M5A / manifest["audio"]["file"]).read_bytes())
    audio[-1] ^= 0xFF
    box = tmp_path / "mono-m5a-miniv3"
    box.mkdir()
    shutil.copy(FROZEN_M5A / "manifest.json", box / "manifest.json")
    (box / manifest["audio"]["file"]).write_bytes(audio)
    out = tmp_path / "phrases.json"
    p = _run_patched(["phrases", "--gains", "0.84", "--out", str(out)],
                     "import mono_m5a_score as score\n"
                     f"score.MANIFEST = pathlib.Path({str(box / 'manifest.json')!r})\n"
                     'score.MANIFESTS["M5A"] = score.MANIFEST\n')
    _assert_refused(p, HASH_REASON, out)
    assert BASELINE_LINE not in p.stdout, "nothing may be scored before the record is bound"


# ---------------------------------------------------------------------------
# the handler is at the `main()` dispatch: the other four modes reach it too
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("mode", sorted(MODE_ENTRY))
def test_refusal_from_any_mode_entry_exits_refused_2(tmp_path, mode):
    entry = MODE_ENTRY[mode]
    reason = f"injected refusal: {mode}"
    out = tmp_path / "out.json"
    p = _run_patched([mode, "--gains", "0.84", "--out", str(out)],
                     "import mono_m5a_score as score\n"
                     f"def _{entry}(*a, **kw):\n"
                     f"    raise score.Refused({reason!r})\n"
                     f"mph.{entry} = _{entry}\n")
    _assert_refused(p, reason, out)


def test_refusal_during_the_out_write_exits_refused_2(tmp_path):
    out = tmp_path / "out.json"
    p = _run_patched(["decimator", "--gains", "0.84", "--out", str(out)],
                     "import mono_m5a_score as score\n"
                     "mph.decimator_sweep = lambda *a, **kw: {}\n"
                     "def _write_text(self, *a, **kw):\n"
                     "    raise score.Refused('injected refusal: --out write')\n"
                     "pathlib.Path.write_text = _write_text\n")
    _assert_refused(p, "injected refusal: --out write", out)


# ---------------------------------------------------------------------------
# an unrelated error at the same boundary stays an execution error (exit 1)
# ---------------------------------------------------------------------------
def test_execution_error_at_the_same_boundary_still_exits_1(tmp_path):
    out = tmp_path / "phrases.json"
    p = _run_patched(["phrases", "--gains", SILENT_GAIN, "--out", str(out)],
                     "import mono_m5a_score as score\n"
                     "def _measure(**kw):\n"
                     "    raise ValueError('injected execution error')\n"
                     "score.measure = _measure\n")
    assert p.returncode == exit_contract.FAIL, (p.returncode, p.stdout[-200:], p.stderr[-400:])
    assert "Traceback" in p.stderr and "ValueError: injected execution error" in p.stderr, p.stderr[-400:]
    assert "REFUSED" not in p.stdout, "a ValueError must not be relabelled a refusal"
    assert not out.exists()


# ---------------------------------------------------------------------------
# probe: a row that did not measure is REFUSED, never KeyError (#1628)
# ---------------------------------------------------------------------------
SILENCED_PROBE_REASON = "square MIDI 36: candidate gain 0.000 silences the output (-300.0 dBFS)"
FIRST_POINT_REASON = "square MIDI 60: candidate gain 0.000 silences the output (-300.0 dBFS)"
NORMAL_GAIN = "0.80"
#: `split_spectrum` needs f0 >= 16 bins of the 36000-sample window (21.33 Hz).
#: MIDI 12 is 16.35 Hz, so `measure_point` returns a MEASURED row whose every
#: stage is refused: the row a verdict-only guard lets through.
LOW_NOTE = 12
LOW_NOTE_REFUSAL = "f0 16.35 Hz is 12.3 bins; need >= 16"
STAGE_REFUSED_REASON = f"square MIDI {LOW_NOTE}: gain 0.850 output stage not measured: {LOW_NOTE_REFUSAL}"


def _measured_row(**over) -> dict:
    """A `measure_point` row that measured, written out key by key to the
    contract the function returns (not built from the guard's own constant)."""
    row = {"verdict": "MEASURED", "activity": {"output_level_dbfs": -12.07, "ok": True},
           "clip": {"oscillator_rail_samples": 7, "output_rail_samples": 0},
           "stages": {"ladder_out": {"refused": "a stage the probe does not read"},
                      "output": {"unwanted_dbfs": -61.2, "unwanted_rel_db": -40.1,
                                 "intended_dbfs": -21.1, "upper_wanted_rel_db": -0.02}}}
    row.update(over)
    return row


def test_probe_raiser_is_the_tool_s_own_subclass_of_the_base():
    import measure_pulse2x_headroom as mph

    assert mph.Refused is not refusal.Refused
    assert issubclass(mph.Refused, refusal.Refused)


def test_silenced_rectangles_in_probe_exit_refused_2_without_report(tmp_path):
    out = tmp_path / "probe.json"
    p = _cli("probe", "--gains", SILENT_GAIN, "--out", str(out))
    _assert_refused(p, SILENCED_PROBE_REASON, out)
    assert "wrote" not in p.stdout, p.stdout[-200:]


def test_probe_refusal_names_the_first_point_that_did_not_measure():
    import measure_pulse2x_headroom as mph

    with pytest.raises(refusal.Refused) as caught:
        mph.probe_points(float(SILENT_GAIN), notes=(60, 84))
    assert str(caught.value) == FIRST_POINT_REASON


def test_refused_output_stage_is_refused_with_the_stage_s_own_text():
    import measure_pulse2x_headroom as mph
    import mono_artifact_probe as mp

    row = mp.measure_point("pulse2x", LOW_NOTE, mp.held_patch(waves=("square",) * 3))
    assert row["verdict"] == "MEASURED" and row["activity"]["ok"], (
        "this control IS the row a verdict-only guard lets through; if MIDI 12 "
        "stopped measuring, it would refuse for another reason")
    assert row["stages"]["output"] == {"refused": LOW_NOTE_REFUSAL}
    with pytest.raises(refusal.Refused) as caught:
        mph.probe_points(float(NORMAL_GAIN), notes=(LOW_NOTE,))
    assert str(caught.value) == STAGE_REFUSED_REASON


def test_normal_gain_in_probe_still_measures_and_writes_the_report(tmp_path):
    out = tmp_path / "probe.json"
    p = _cli("probe", "--gains", NORMAL_GAIN, "--out", str(out))
    assert p.returncode == exit_contract.PASS, (p.returncode, p.stdout[-300:], p.stderr[-400:])
    assert not _refused_lines(p.stdout), p.stdout[-300:]
    assert f"wrote {out}" in p.stdout, p.stdout[-200:]
    res = json.loads(out.read_text())["results"]
    assert len(res) == 32 and {tuple(sorted(v)) for v in res.values()} == {("0.800", "0.850")}, sorted(res)
    assert all(isinstance(x, (int, float)) for v in res.values() for w in v.values() for x in w.values())
    assert any(v["0.850"] != v["0.800"] for v in res.values()), "the candidate gain was never applied"


def test_guard_passes_a_measured_row_through_unchanged():
    import measure_pulse2x_headroom as mph

    assert mph._measured_numbers(_measured_row(), "where") == {
        "osc_rail": 7, "unwanted_dbfs": -61.2, "unwanted_rel_db": -40.1,
        "intended_dbfs": -21.1, "upper_wanted_rel_db": -0.02}


@pytest.mark.parametrize("over, expected", [
    ({"verdict": "REFUSED", "reason": "resonance at or above the self-oscillation boundary is out of domain"},
     "where did not measure (verdict REFUSED: resonance at or above the self-oscillation boundary is out of domain)"),
    ({"verdict": None}, "where did not measure (verdict None)"),
    ({"stages": {"output": {"refused": LOW_NOTE_REFUSAL}}},
     "where output stage not measured: " + LOW_NOTE_REFUSAL),
    ({"stages": {}},
     "where output stage not measured: no unwanted_dbfs, unwanted_rel_db, intended_dbfs, upper_wanted_rel_db"),
    ({"clip": {"output_rail_samples": 0}},
     "where row not measured: no clip.oscillator_rail_samples"),
])
def test_guard_refuses_every_row_that_is_not_a_measurement(over, expected):
    import measure_pulse2x_headroom as mph

    with pytest.raises(mph.Refused) as caught:
        mph._measured_numbers(_measured_row(**over), "where")
    assert str(caught.value) == expected

# #1693: the tool's own preconditions must REFUSE, not traceback or floor
# ---------------------------------------------------------------------------
DECIMATOR_REASON = "pulse479 MIDI 0 gain Q15 0: silent decimator output"    # first shape, first note
FINE_REASON = "square MIDI 90.0 gain Q15 0: silent decimator output"        # first reachable shape, first grid point
MIX_REASON = ("mix preset three-rect MIDI 24 gain 0.000: silent: -inf dBFS "
              "< activity floor -90.0 dBFS")
DEFAULT_PRESET_REASON = ("mix preset default MIDI 60 gain 0.000: silent: -inf dBFS "
                         "< activity floor -90.0 dBFS")
QUIET_Q15 = 28         # three-rect MIDI 60 at -74.7 dBFS on main @ 3e2534c7: 15 dB above the floor


def _square(amp, n: int = 24_000) -> np.ndarray:
    """A +/- amp LSB square: RMS exactly amp LSB by definition, so its level
    is known from arithmetic alone. An integer amplitude gives an integer
    array, like VoiceFx.note's render; a fractional one stays float64."""
    return np.where(np.arange(n) % 2, -amp, amp)


@pytest.mark.parametrize("mode, reason", [("decimator", DECIMATOR_REASON), ("fine", FINE_REASON)])
def test_zero_gain_decimator_or_fine_sweep_exits_refused_2(tmp_path, mode, reason):
    out = tmp_path / f"{mode}.json"
    p = _cli(mode, "--gains", SILENT_GAIN, "--out", str(out))
    _assert_refused(p, reason, out)


def test_zero_gain_mix_exits_refused_2_without_partial_report(tmp_path):
    out = tmp_path / "mix.json"
    p = _cli("mix", "--gains", SILENT_GAIN, "--out", str(out))
    _assert_refused(p, MIX_REASON, out)
    assert "mix gain None:" in p.stdout, (
        "the baseline is rendered and summarised before the candidate; a run "
        "that refused earlier did not reach the rectangle rows")
    assert "mix gain 0.0:" not in p.stdout, "the candidate's summary prints only after its last row"


def test_mix_floor_is_the_probe_activity_floor_on_a_known_level(monkeypatch):
    import measure_pulse2x_headroom as mph
    import mono_artifact_probe as mp

    below = 20 * math.log10(1 / 32768)          # -90.31 dBFS: 0.3 dB under the floor
    above = 20 * math.log10(1.1 / 32768)        # -89.48 dBFS: 0.5 dB above it
    assert below < mp.SILENT_DBFS < above, "the two known levels must straddle the probe's floor"
    assert mph.output_dbfs(_square(1.1), "known level") == pytest.approx(above, rel=1e-12)
    floor = f"{mp.SILENT_DBFS:.1f}"
    for what, out, level in (("silence", np.zeros(24_000, dtype=np.int64), "-inf"),
                             ("one LSB", _square(1), f"{below:.1f}")):
        with pytest.raises(refusal.Refused) as e:
            mph.output_dbfs(out, f"mix preset {what} MIDI 0 gain x")
        assert str(e.value) == (f"mix preset {what} MIDI 0 gain x: silent: {level} dBFS "
                                f"< activity floor {floor} dBFS")
    with pytest.raises(refusal.Refused):      # NaN: `level < floor` is False for it (#134)
        mph.output_dbfs(np.full(24_000, np.nan), "known level")
    monkeypatch.setattr(mp, "SILENT_DBFS", below - 1.0)
    assert mph.output_dbfs(_square(1), "known level") == pytest.approx(below, rel=1e-12), (
        "the floor is the probe's attribute, read at the call: a constant retyped "
        "in the tool would not move with it")


def test_mix_output_above_the_floor_still_measures():
    import measure_pulse2x_headroom as mph
    import mono_artifact_probe as mp

    gain = QUIET_Q15 / 32768
    assert mph.q15(gain) == QUIET_Q15
    res = mph.mix_check([gain], notes=(60,))
    level = res[f"{gain:.3f}/three-rect/60"]["output_rms_dbfs"]
    assert mp.SILENT_DBFS <= level < mp.SILENT_DBFS + 20, (level, "quiet, and above the floor")
    assert level < res["base/three-rect/60"]["output_rms_dbfs"] - 30, "far under the baseline"


def test_mix_scope_all_zero_gain_refuses_on_the_default_preset():
    import measure_pulse2x_headroom as mph

    with pytest.raises(refusal.Refused) as e:
        mph.mix_check([0.0], scope="all", notes=(60,))
    assert str(e.value) == DEFAULT_PRESET_REASON


def test_presaturation_mismatch_stays_an_execution_error_exit_1(tmp_path):
    out = tmp_path / "decimator.json"
    p = _run_patched(["decimator", "--gains", "0.84", "--out", str(out)],
                     "import voice_fx as vf\n"
                     "_orig = vf.sat16\n"
                     "vf.sat16 = lambda v: _orig(v) + 1\n")
    assert p.returncode == exit_contract.FAIL, (p.returncode, p.stdout[-200:], p.stderr[-400:])
    assert "RuntimeError: recorded pre-saturation samples do not produce the output" in p.stderr
    assert "Traceback" in p.stderr and not _refused_lines(p.stdout), (p.stdout[-200:], p.stderr[-400:])
    assert not out.exists()


def test_unfloored_level_equals_the_retired_floored_expression_on_real_renders(monkeypatch):
    """The committed form of #1693's before/after control. On real `mix_check`
    renders at the built gain and a default candidate gain, `output_dbfs`
    returns what the retired `20 * log10(max(rms, 1e-12))` returned, so
    removing the floor moved no measured level, and every level sits far
    above the activity floor (the lowest default-gain row was -23.4 dBFS on
    main @ 3e2534c7)."""
    import measure_pulse2x_headroom as mph
    import mono_artifact_probe as mp

    real = mph.output_dbfs
    rows = []

    def spy(out, what):
        retired = 20 * math.log10(max(float(np.sqrt(np.mean(
            (out[12000:].astype(float) / 32768) ** 2))), 1e-12))
        level = real(out, what)
        rows.append((what, round(level, 3), round(retired, 3)))
        return level
    monkeypatch.setattr(mph, "output_dbfs", spy)
    mph.mix_check([0.84], notes=(24, 84, 127))
    assert len(rows) == 12, "2 gains x 2 presets x 3 notes must each reach the level check"
    assert all(new == old for _, new, old in rows), rows
    assert min(old for _, _, old in rows) > mp.SILENT_DBFS + 30, rows


# ---------------------------------------------------------------------------
# #1908: an estimator InsufficientEvidence beside the Refused-only handler.
# UNREACHABLE through the CLI (module docstring); these pin the injected escape
# and the gates that make it so. `measure_pulse2x_headroom.py` is unchanged.
# ---------------------------------------------------------------------------
IE_CLASS = "audio_measure.InsufficientEvidence"
#: Q15 1: the smallest candidate gain that is not digital silence
SMALLEST_NONZERO_GAIN = "2e-5"
NO_FUNDAMENTAL_REASON = ("pulse MIDI 84 pitch refused: no fundamental near 1046.5 Hz "
                         "(no prominent line in the band)")
#: Q15 of 0.84, a candidate gain that measures on its own
NEUTRAL_GAIN_Q15 = 27525
#: the four stage arrays `_stage_diagnostics` reads
ALL_TAPS = {"oscillator", "mixer", "ladder", "output"}
LADDER_AND_OUTPUT = {"ladder", "output"}
#: (Q15 gain, scope, drive multiplier, the stage taps that are silent) -- the
#: boundary values of every accepted input class the trace ran. A gain silences
#: the oscillators and so everything after them; a zero/tiny/negative drive
#: leaves the oscillator and mixer LIVE and silences the ladder (gain register
#: 0) and with it the output.
STAGE_TAP_CASES = [
    pytest.param(0, "rect", None, ALL_TAPS, id="gain 0 silences the rectangles"),
    pytest.param(0, "all", None, ALL_TAPS, id="gain 0 under scope all"),
    pytest.param(1, "rect", None, set(), id="the smallest non-zero gain is not silence"),
    pytest.param(-27853, "rect", None, set(), id="inverted"),
    pytest.param(65536, "rect", None, set(), id="2x, at the rail"),
    pytest.param(NEUTRAL_GAIN_Q15, "rect", 0.0, LADDER_AND_OUTPUT, id="drive 0"),
    pytest.param(NEUTRAL_GAIN_Q15, "rect", 1e-9, LADDER_AND_OUTPUT, id="drive 1e-9"),
    pytest.param(NEUTRAL_GAIN_Q15, "rect", -1.0, LADDER_AND_OUTPUT, id="negative drive"),
    pytest.param(NEUTRAL_GAIN_Q15, "rect", 1e6, set(), id="drive 1e6"),
]


def _stage_taps_silent(gain_q15: int, scope: str, drive_comp) -> tuple[dict, dict]:
    """Per stage tap (`oscillator`, `mixer`, `ladder`, `output`): digitally
    silent over the window `measure` analyses? -- for one held M5A pulse note
    (MIDI 84, gate 0.6 s) rendered inside the candidate context `phrases()`
    itself builds.

    `score.measure` is replaced by a recorder and the real `phrases()` loop is
    run, so its own `voice_factory`, `candidate()` and `pulse_drive()` are the
    active contexts and nothing about the voice is restated here. The baseline
    call answers an empty score, which is all `phrases()` reads of it; the
    first candidate's taps are kept and the loop is stopped there. Returns
    (baseline taps, candidate taps)."""
    import measure_pulse2x_headroom as mph
    import mono_m5a_score as score

    # the base patch only: `_patch_for_wave` is itself what `pulse_drive()`
    # replaces, so it has to be called from inside the context (first draft
    # called it here, rendered every drive case at the as-built 0.75 and the
    # three drive rows came out live against the real CLI's "silent")
    base = score._voice_patch(score.load_reference("M5A").manifest)

    class _Stop(Exception):
        pass

    calls = []

    def _record(**kw):
        voice = kw["voice_factory"]()
        patch = score._patch_for_wave(base, "pulse")
        pcm = score.vf.render_mono_fx([(0.0, 84, 0.6, {**patch, "gate": 0.6})], 0.6, voice)
        calls.append((voice.trace, pcm))
        if len(calls) == 2:                      # the first candidate: M5A at the gain under test
            raise _Stop
        return {"metrics": {}, "event_diagnostics": []}

    real, score.measure = score.measure, _record
    try:
        with pytest.raises(_Stop):
            mph.phrases([gain_q15 / 32768], scope, drive_comp)
    finally:
        score.measure = real
    assert len(calls) == 2, "the recorder saw the baseline and one candidate"
    a, b = int(0.12 * score.SR), int((0.6 - 0.08) * score.SR)       # measure's own window
    return [{name: not np.count_nonzero(np.asarray(x)[a:b])
             for name, x in zip(("oscillator", "mixer", "ladder", "output"),
                                (tr["osc"][0], tr["mixed"], tr["ladder"], pcm))}
            for tr, pcm in calls]


@pytest.mark.parametrize("gain_q15, scope, drive_comp, silent_taps", STAGE_TAP_CASES)
def test_1908_no_accepted_input_leaves_a_stage_tap_silent_beside_a_live_output(gain_q15, scope, drive_comp, silent_taps):
    baseline, candidate = _stage_taps_silent(gain_q15, scope, drive_comp)
    assert not any(baseline.values()), ("the as-built voice is live on every tap; "
                                        "a silent baseline did not reach the candidate", baseline)
    silent = {name for name, is_silent in candidate.items() if is_silent}
    assert silent == set(silent_taps), candidate
    # THE GATE. `_stage_diagnostics` is reached only after `refine_f0` accepted
    # the OUTPUT, so a silent ladder or oscillator tap is harmless while the
    # output is silent with it; a silent tap beside a LIVE output is the one
    # input that reaches `harmonic_signature: silent` past `refine_f0`.
    assert not silent or "output" in silent, candidate


@pytest.mark.parametrize("drive_comp", [0.0, 1e-9, -1.0])
def test_1908_a_silencing_drive_is_a_zero_ladder_gain_register(drive_comp):
    import measure_pulse2x_headroom as mph
    import mono_m5a_score as score

    base = score._voice_patch(score.load_reference("M5A").manifest)
    with mph.pulse_drive(drive_comp):
        regs = score.vf.VoiceFx.patch_regs(**score._patch_for_wave(base, "pulse"))
    assert regs["gain"] == 0, regs


def test_1908_injected_insufficient_evidence_at_score_measure_still_exits_1(tmp_path):
    out = tmp_path / "phrases.json"
    p = _run_patched(["phrases", "--gains", "0.84", "--out", str(out)],
                     "import audio_measure as am\n"
                     "import mono_m5a_score as score\n"
                     "def _measure(**kw):\n"
                     "    raise am.InsufficientEvidence('injected: insufficient evidence at score.measure')\n"
                     "score.measure = _measure\n")
    assert p.returncode == exit_contract.FAIL, (p.returncode, p.stdout[-200:], p.stderr[-400:])
    assert "Traceback" in p.stderr, p.stderr[-400:]
    assert f"{IE_CLASS}: injected: insufficient evidence at score.measure" in p.stderr, p.stderr[-400:]
    assert "REFUSED" not in p.stdout, "this handler catches refusal.Refused alone; that is the #1908 pin"
    assert not out.exists()


def test_1908_injected_silent_oscillator_tap_beside_a_live_output_exits_1_at_the_census_site(tmp_path):
    """The one route to the census site: `trace["osc"][0]` is zeroed after every
    render while the output stays live, which no command line can do."""
    out = tmp_path / "phrases.json"
    p = _run_patched(["phrases", "--gains", "0.84", "--out", str(out)],
                     "import numpy as np\n"
                     "import mono_m5a_score as score\n"
                     "_render = score.vf.render_mono_fx\n"
                     "def _silent_tap(seq, dur, voice=None, host=None):\n"
                     "    pcm = _render(seq, dur, voice, host)\n"
                     "    voice.trace['osc'][0] = np.zeros_like(voice.trace['osc'][0])\n"
                     "    return pcm\n"
                     "score.vf.render_mono_fx = _silent_tap\n")
    assert p.returncode == exit_contract.FAIL, (p.returncode, p.stdout[-200:], p.stderr[-400:])
    assert "Traceback" in p.stderr, p.stderr[-400:]
    assert f"{IE_CLASS}: harmonic_signature: silent" in p.stderr, p.stderr[-400:]
    assert "in _stage_diagnostics" in p.stderr and "in harmonic_signature" in p.stderr, p.stderr[-600:]
    assert "REFUSED" not in p.stdout and not out.exists()


def test_1908_smallest_non_zero_gain_is_refused_by_the_pitch_estimator(tmp_path):
    """The real CLI, on the input nearest the census's suspected cause (a small
    gain): not silent, so not the silence gate -- `refine_f0` finds no
    fundamental, and that refusal is a `Refused`."""
    out = tmp_path / "phrases.json"
    p = _cli("phrases", "--gains", SMALLEST_NONZERO_GAIN, "--out", str(out))
    _assert_refused(p, NO_FUNDAMENTAL_REASON, out)
    assert BASELINE_LINE in p.stdout and "wrote" not in p.stdout, p.stdout[-200:]


# ---------------------------------------------------------------------------
# #2042: a number argparse accepts but the engine cannot represent is REFUSED
# at the CLI boundary, before any mode renders
# ---------------------------------------------------------------------------
ALL_MODES = ("decimator", "fine", "mix", "phrases", "probe")
NON_FINITE_GAIN = "not a finite Q1.15 substep gain"
#: `--gains` values whose `g * 32768` is not a float64: the IEEE specials
#: argparse's `type=float` reads, and the finite 1e308 whose product overflows
#: BEFORE `round` -- the rule-8 input for a `math.isfinite(g)` guard. `-inf`
#: reaches `main()` only in `=` syntax (two tokens: argparse reads a flag).
NON_FINITE_GAINS = [
    (["--gains", "nan"], "--gains[0] nan"),
    (["--gains", "inf"], "--gains[0] inf"),
    (["--gains=-inf"], "--gains[0] -inf"),
    (["--gains", "1e308"], "--gains[0] 1e+308"),
    (["--gains=-1e308"], "--gains[0] -1e+308"),
    (["--gains", "0.80", "nan"], "--gains[1] nan"),     # trailing: `probe` reads gains[0] alone
]
#: `--gains` values with a finite Q15 word beyond the decimator's int64 range.
#: 1e15's word leaves int64 outright (numpy raises); 1e14's word FITS int64 and
#: wraps silently inside `_render_2x` -- the rule-8 input for an int64-fit guard.
WRAPPING_GAINS = [
    (["--gains", "1e15"], "--gains[0] 1000000000000000.0", 32768000000000000000),
    (["--gains=-1e15"], "--gains[0] -1000000000000000.0", -32768000000000000000),
    (["--gains", "1e14"], "--gains[0] 100000000000000.0", 3276800000000000000),
    (["--gains=-1e14"], "--gains[0] -100000000000000.0", -3276800000000000000),
]
NOT_FINITE_DRIVE = "not a finite ladder-drive multiplier"
OVERFLOWING_DRIVE = "the ladder's drive-to-gain conversion overflows float64"
#: `--pulse-drive-comp` values the ladder cannot convert: the IEEE specials,
#: and the finite 1e308 whose `drive * vpu / VT2 * 65536` overflows float64
#: before `int(round(.))` -- the rule-8 input for a `math.isfinite(d)` guard.
BAD_DRIVE_COMP = [
    (["--pulse-drive-comp", "nan"], f"--pulse-drive-comp nan: {NOT_FINITE_DRIVE}"),
    (["--pulse-drive-comp", "inf"], f"--pulse-drive-comp inf: {NOT_FINITE_DRIVE}"),
    (["--pulse-drive-comp=-inf"], f"--pulse-drive-comp -inf: {NOT_FINITE_DRIVE}"),
    (["--pulse-drive-comp", "1e308"], f"--pulse-drive-comp 1e+308: {OVERFLOWING_DRIVE}"),
    (["--pulse-drive-comp=-1e308"], f"--pulse-drive-comp -1e+308: {OVERFLOWING_DRIVE}"),
]
ACCEPTED_GAINS = [-1.0, -0.85, 0.0, 2e-5, 0.80, 0.84, 0.85, 2.0, 100.0]
ACCEPTED_DRIVE_COMP = [-1.0, 0.0, 1e-9, 1e6]


def _token(argv: list[str]) -> str:
    """The number a one-flag argv tail carries, whichever syntax it used."""
    return argv[-1].split("=", 1)[-1]


def _decimator_limit() -> int:
    """The largest |Q15 substep gain| `voice_fx._render_2x` applies without
    wrapping signed int64, from the model's taps and none of the guard's text:
    after `hi * gain >> 15` (|hi| <= 2**15) a sample is at most |gain|, and the
    31-tap `np.convolve` sums to `sum(|taps|)` times that."""
    import measure_pulse2x_headroom                     # noqa: F401  puts model/ on sys.path
    import voice_fx as vf

    return ((1 << 63) - 1) // int(np.abs(vf._DECIM2_TAPS).sum())


@pytest.mark.parametrize("mode", ALL_MODES)
@pytest.mark.parametrize("argv, named", NON_FINITE_GAINS, ids=[named for _, named in NON_FINITE_GAINS])
def test_non_finite_gain_exits_refused_2_before_any_mode_renders(tmp_path, mode, argv, named):
    out = tmp_path / "out.json"
    p = _cli(mode, *argv, "--out", str(out))
    _assert_refused(p, f"{named}: {NON_FINITE_GAIN}", out)
    assert BASELINE_LINE not in p.stdout, "the guard sits before the dispatch, not inside a mode"


@pytest.mark.parametrize("mode", ALL_MODES)
@pytest.mark.parametrize("argv, shown, word", WRAPPING_GAINS, ids=[_token(a) for a, _, _ in WRAPPING_GAINS])
def test_int64_wrapping_gain_exits_refused_2_before_any_mode_renders(tmp_path, mode, argv, shown, word):
    limit = _decimator_limit()
    assert abs(word) > limit, "not an adversary: this word is inside the no-wrap range"
    out = tmp_path / "out.json"
    p = _cli(mode, *argv, "--out", str(out))
    _assert_refused(p, f"{shown}: Q15 {word} is beyond the decimator's int64 range (+/-{limit})", out)
    assert BASELINE_LINE not in p.stdout, "the guard sits before the dispatch, not inside a mode"


def test_each_obvious_gain_guard_is_defeated_by_a_committed_adversary():
    """Rule 8, in-process. `math.isfinite(g)` alone lets 1e308 through: it is
    finite, and `g * 32768` is inf before `round` sees it. An int64-fit check
    alone lets 1e14 through: its word is below 2**63, and the real `_render_2x`
    then returns a Q1.15 render from WRAPPED arithmetic -- no exception, a
    number where nothing was measured. Both words are inside the committed
    bound; 1e15's is the one numpy itself refuses."""
    import measure_pulse2x_headroom as mph
    import voice_fx as vf

    assert math.isfinite(1e308) and not math.isfinite(1e308 * 32768)
    assert mph.q15(1e15) == 32768000000000000000 >= (1 << 63)
    limit = _decimator_limit()
    assert mph.q15(1e14) == 3276800000000000000 < (1 << 63)
    assert mph.q15(1e14) > limit > mph.q15(5.214e9), (limit, "tight: the next gain wraps")
    o = vf.OscFx("square")
    with mph._gain("square", mph.q15(1e14)):
        y, _, _ = vf._render_2x(o, 64, vf.phase_inc(vf.note_hz(84.0)), np.zeros(30, dtype=np.int64), 0)
    assert len(y) == 64 and np.max(np.abs(y)) == 32768, "it rendered: the wrap is silent"


@pytest.mark.parametrize("argv, reason", BAD_DRIVE_COMP, ids=[_token(a) for a, _ in BAD_DRIVE_COMP])
def test_unconvertible_pulse_drive_comp_exits_refused_2_before_any_render(tmp_path, argv, reason):
    """`--gains 0.85` is BASE_Q15, so `phrases` keeps no candidate and never
    enters `pulse_drive`: on the pre-fix tree these rows scored both baseline
    phrases and exited 0, the multiplier never applied."""
    out = tmp_path / "phrases.json"
    p = _cli("phrases", "--gains", "0.85", *argv, "--out", str(out))
    _assert_refused(p, reason, out)
    assert BASELINE_LINE not in p.stdout, "the guard sits before the dispatch, not inside a mode"


def test_the_finite_drive_adversary_overflows_inside_the_real_ladder_conversion():
    """Rule 8 for `--pulse-drive-comp`: `math.isfinite(1e308)` is True, yet the
    real `LadderFx.regs_unclamped` overflows float64 on it -- along the path
    the phrases run takes, the preset's own drive scaled by the multiplier --
    while the overdrive the trace measured (1e6) converts."""
    import measure_pulse2x_headroom as mph
    import mono_m5a_score as score
    import voice_fx as vf

    assert math.isfinite(1e308)
    preset = score._voice_patch(score.load_reference("M5A").manifest)["drive"]
    lad = vf.LadderFx(**vf.LADDER_CFG)
    for drive in (1e308, preset * 1e308):
        with pytest.raises(OverflowError):
            lad.regs_unclamped(0.0, drive)
    with pytest.raises(ValueError):
        lad.regs_unclamped(0.0, math.nan)
    assert mph.q15(0.85) == mph.BASE_Q15, "`phrases --gains 0.85` keeps no candidate"


def test_phrases_preset_drive_is_within_the_guard_s_reference():
    """The guard converts `--pulse-drive-comp` as if it were the drive itself.
    `phrases` scales the preset's drive by the multiplier, so a preset drive
    above the reference (1.0) would open a gap above ~1e303 -- narrow, and no
    physical multiplier, but a premise the guard rests on: read from the real
    preset, not assumed."""
    import mono_m5a_score as score

    drive = score._voice_patch(score.load_reference("M5A").manifest)["drive"]
    assert 0.0 <= drive <= 1.0, (drive, "raise the guard's reference drive with the preset")


def test_documented_accepted_numbers_pass_the_cli_guard():
    """The gains the trace measured on main (ordinary, overdriven, inverted)
    and every `--pulse-drive-comp` it measured pass the guard, together and
    each alone -- including the two that must still REACH a downstream
    refusal (`0.0` silences, `2e-5` is one Q15 step) rather than stop here."""
    import measure_pulse2x_headroom as mph

    for d in (None, *ACCEPTED_DRIVE_COMP):
        mph._refuse_unrepresentable(ACCEPTED_GAINS, d)
        for g in ACCEPTED_GAINS:
            mph._refuse_unrepresentable([g], d)


@pytest.mark.parametrize("mode", ALL_MODES)
def test_valid_numbers_reach_every_mode_unchanged(tmp_path, mode):
    """Through the real `main()`, with only the mode's entry function replaced
    by a recorder: the accepted gains pass the guard and arrive as parsed --
    not clamped, dropped or reordered -- and `phrases` receives its drive
    multiplier as parsed. (0.85 is BASE_Q15; `mix` and `phrases` filter it on
    their own, as before the guard.)"""
    out = tmp_path / "out.json"
    parsed = [-1.0, -0.85, 0.80, 0.85, 0.84, 2.0, 100.0]
    entry = MODE_ENTRY.get(mode, "phrases")
    p = _run_patched([mode, "--gains", *map(str, parsed), "--pulse-drive-comp", "1e6", "--out", str(out)],
                     f"mph.{entry} = lambda *a, **kw: {{'args': [list(x) if isinstance(x, list) else x for x in a]}}")
    assert p.returncode == exit_contract.PASS, (p.returncode, p.stdout[-300:], p.stderr[-400:])
    assert not _refused_lines(p.stdout) and "Traceback" not in p.stderr, (p.stdout[-300:], p.stderr[-400:])
    kept = [-1.0, -0.85, 0.80, 0.84, 2.0, 100.0]
    assert json.loads(out.read_text())["results"]["args"] == {
        "decimator": [parsed], "fine": [parsed], "probe": [-1.0],
        "mix": [kept, "rect"], "phrases": [kept, "rect", 1e6]}[mode]
