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
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys

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
