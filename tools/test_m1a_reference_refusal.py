#!/usr/bin/env python3
"""Known-answer exit-status controls: an evidence refusal raised at the M1A
reference capture's bass-ring step must reach the tool's exit contract as
REFUSED, never as an uncaught exit-1 traceback (#1906).

THE DEFECT, AS MEASURED ON main @ dd0449e0 (2026-11-28, red-first run of every
control in this file): `main()` in `tools/measure_mono_m1a_reference.py`
caught `(ref.Refused, OSError, ValueError, RuntimeError)`. The last
measurement it takes is the filter's rest ring, `ref.am.dominant_frequency(y,
25, 15000, ref.SR).require("bass cutoff ring")` (`:482`), and `.require`
raises `audio_measure.InsufficientEvidence`: an `AssertionError` subclass and
a SIBLING of `ref.Refused`, not a subclass of it or of the three stdlib
errors. A ring that rendered silent therefore left the process at exit 1 with
the `InsufficientEvidence` traceback and no `refused.json` -- an unusable
apparatus rendered as FAIL, the inversion the exit contract exists to prevent
(tools/exit_contract.py). The repair adds `ref.am.InsufficientEvidence` to
that one `except` tuple, the handler shape of #1162
(tools/test_rs_guard_band_refusal.py) and #1613
(tools/test_pulse2x_headroom_refusal.py).

RED FIRST, on dd0449e0 with the tool unmodified: 1 of the 7 controls failed.
`test_silent_bass_ring_exits_refused_2_with_refused_json` failed with the
defect -- exit 1; stderr a traceback through `measure_mono_m1a_reference.py:482`
and `audio_measure.py:378` ending `audio_measure.InsufficientEvidence: bass
cutoff ring: insufficient evidence -- silent {}`; no `refused.json`. It failed
on the exit-status assertion, after `_assert_reached_ring_step` had passed:
the refusal came from the ring step. The estimator precondition, the class
relation, the repeatability precondition, the plain-`AssertionError` control,
the local-`Refused` control and the passing run were green, as they must be
on any tree. The whole file runs in 2-3 s (23 s once, under a host load
average of 12-17).

THE CONTROLS HAVE TEETH, checked on the fixed tree by mutating that one
line: widened to `AssertionError`, or to `Exception`, the plain-`AssertionError`
control went red (1 failed, 6 passed, each time); with the addition removed,
the silent-ring control went red (1 failed, 6 passed).

THE KNOWN ANSWERS, independent of anything this repository models:

  silent ring     a buffer of exact zeros has no dominant frequency, so the
                  estimator must refuse it. The capture owes exit
                  `exit_contract.REFUSED` (2), exactly one `REFUSED: <reason>`
                  line naming the ring and silence, a `refused.json` holding
                  `state: REFUSED` and that reason, no traceback and no
                  `manifest.json`.
  plain assertion an `AssertionError` that is not an evidence refusal, raised
                  at the same `.require` call site by a stub, is an execution
                  error: exit 1 with its traceback and no `refused.json`. This
                  control is what turns red if the handler is widened to
                  `AssertionError` or `Exception` (tools/refusal.py's
                  "inverse defect").
  drifting ring   three rings at 440/540/640 Hz trip the tool's own
                  repeatability gate, `ref.Refused("bass cutoff ring is not
                  repeatable")`: the pre-existing refusal path, unchanged.
  steady ring     three rings at 440 Hz measure and the capture completes:
                  exit 0, a `manifest.json`, no `refused.json`. The handler
                  addition must not touch a run that measured.

HOW THE INJECTIONS ARE MADE. No command-line input reaches the ring step
without the Mini V3 plugin, so each control runs the real `main()` in a real
interpreter after `install_stubs` replaces what needs the plugin, a committed
tree or 24 s of known-signal qualification: `render`, `event_measurements`,
`qualify_envelope_basis`, the `git status` call, `ref.rv.measure`,
`ref._plugin_metadata`, `ref._qualify_reference_integrity`,
`ref._source_provenance` and `ref.rr.MiniV3Rig`; the plain-assertion control
also replaces `ref.am.dominant_frequency`, its whole subject being what the
handler does with an error that is not the estimator's. The ring step itself
(`ref.am.dominant_frequency(...).require(...)` and the repeatability gate),
the handler, the process exit status and the traceback rendering are the real
ones. `_assert_reached_ring_step` pins that each outcome came from the ring
step and not from a stub that fell short: all three repeat lines printed and
all six WAVs written first.

EVERY ASSERTION PINS THE MESSAGE, not just the code: an exit code alone is
satisfied by a crashed-and-caught-anything handler.

NOT CLAIMED: that the real Mini V3 ring ever decays to silence. That needs
the plugin and is untraced; `:482` is estimator-reachable, which is what
these controls exercise. It does not change the handler either way -- a run
that measures is untouched (the steady-ring control).

SITES TRACED UNREACHABLE, recorded from issue #1906's curator trace
(synthetic-input injection, 2026-11-28; not re-measured here) so nobody
re-traces them. `:98`, `:102`, `:419` and `:441` reach `harmonic_signature`
through `event_measurements`: a steady tone on all three events returns,
silence stops at the pitch gate (`:88`), and the only onset delay that
silences the `:102` clip (0.2-0.29 s) stops at the attack-fit gate (`:92`)
first. `:431` goes through `reference_voice.measure`, which returns
`verified=False` instead of raising. `:503` is the `raise SystemExit(main())`
that `_run_main` performs itself.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import types

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import exit_contract                                     # noqa: E402
import measure_mono_m1a_reference as m1a                 # noqa: E402

SR = m1a.ref.SR
RING_SECONDS = 1.2            # `main()` asks the rig for this long
SILENT_RING_REASON = "bass cutoff ring: insufficient evidence -- silent {}"
UNREPEATABLE_REASON = "bass cutoff ring is not repeatable"
INJECTED = "injected plain AssertionError: not an evidence refusal"
CAPTURED = "M1A reference captured; scorecard coverage unchanged"
REPEAT_LINES = [f"M1A repeat {i}: measured three bass notes" for i in range(3)]
#: everything `main()` writes before the ring step, and nothing after it
WAVS_BEFORE_RING = ["control-filter-envelope-disabled.wav", "control-osc1_open.wav",
                    "control-osc2_open.wav", "m1a-repeat-0.wav", "m1a-repeat-1.wav",
                    "m1a-repeat-2.wav"]
DRIFTING_HZ = (440., 540., 640.)


def install_stubs(m1a, ring: str) -> None:
    """Replace what `main()` needs the plugin, a committed tree or the
    known-signal qualification for; leave the ring step and the handler real.

    `ring` selects what the stubbed rig answers: "silent", "steady",
    "drifting", or "assertion" (a silent ring AND an estimator whose
    `.require` raises a plain `AssertionError`)."""
    ref = m1a.ref
    sr = ref.SR
    t = np.arange(round(m1a.SECONDS * sr)) / sr
    baseline = .25 * np.sin(2 * np.pi * 65.4 * t)
    disabled = baseline.copy()
    for event in m1a.EVENTS:
        on = round(event["on_s"] * sr)
        disabled[on:on + round(.04 * sr)] += .1

    def render(overrides=None, events=m1a.EVENTS, seconds=m1a.SECONDS):
        if overrides is None:
            audio, label = baseline, "three-repeat render"
        elif "osc1_level" in overrides:
            audio, label = .05 * baseline, "osc2_open"
        elif "osc2_level" in overrides:
            audio, label = baseline, "osc1_open"
        else:
            audio, label = disabled, "filter-envelope disabled"
        return audio, {"stub": label}

    def event_measurements(audio):
        return [{"h4_trajectory": [{"offset_s": .02, "h4_db": -12.0}]} for _ in m1a.EVENTS]

    asked = []

    def rig_ring(cut, res, seconds=RING_SECONDS, amp=.25):
        n = round(.6 * seconds * sr)
        hz = DRIFTING_HZ[0] if ring == "steady" else DRIFTING_HZ[min(len(asked), 2)]
        asked.append(hz)
        if ring in ("silent", "assertion"):
            return np.zeros(n)
        return .25 * np.sin(2 * np.pi * hz * np.arange(n) / sr)

    class Rig:
        def __init__(self, **kwargs):
            self.ring = rig_ring

    class Estimate:
        def require(self, what=""):
            raise AssertionError(INJECTED)

    m1a.subprocess = types.SimpleNamespace(check_output=lambda *a, **kw: "")
    m1a.qualify_envelope_basis = lambda: {"stub": "known-signal qualification not run"}
    m1a.render = render
    m1a.event_measurements = event_measurements
    ref._source_provenance = lambda: {"files_sha256": {}, "stub": "provenance not read"}
    ref._plugin_metadata = lambda: {"stub": "plugin not loaded"}
    ref._qualify_reference_integrity = lambda: {"stub": "integrity qualification not run"}
    ref.rv.measure = lambda *a, **kw: {"verified": True, "steady": True,
                                       "stub": "reference_voice not run"}
    ref.rr.MiniV3Rig = Rig
    if ring == "assertion":
        ref.am.dominant_frequency = lambda *a, **kw: Estimate()


def _run_main(tmp_path: pathlib.Path, ring: str) -> tuple[subprocess.CompletedProcess, pathlib.Path]:
    """The real `main()` in a real interpreter: the process a batch runner sees."""
    out = tmp_path / "mono-m1a-miniv3"
    code = ("import sys\n"
            f"sys.path[:0] = [{str(ROOT / 'tools')!r}]\n"
            "import measure_mono_m1a_reference as m1a\n"
            "import test_m1a_reference_refusal as controls\n"
            f"controls.install_stubs(m1a, {ring!r})\n"
            f"raise SystemExit(m1a.main(['--out', {str(out)!r}]))\n")
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       timeout=300, cwd=str(ROOT))
    return p, out


def _lines_starting(text: str, prefix: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith(prefix)]


def _last_line(text: str) -> str:
    return text.rstrip().splitlines()[-1] if text.strip() else ""


def _assert_reached_ring_step(p: subprocess.CompletedProcess, out: pathlib.Path) -> None:
    """Every `main()` step before the ring step printed or wrote its mark, so
    the outcome under test came from the ring step and nowhere earlier."""
    assert _lines_starting(p.stdout, "M1A repeat") == REPEAT_LINES, p.stdout[-300:]
    assert sorted(f.name for f in out.glob("*.wav")) == WAVS_BEFORE_RING, sorted(
        f.name for f in out.iterdir())


# ---------------------------------------------------------------------------
# the estimator precondition and the class relation the whole fix rests on
# ---------------------------------------------------------------------------
def test_silent_ring_is_refused_by_the_real_estimator():
    am = m1a.ref.am
    y = np.zeros(round(.6 * RING_SECONDS * SR))
    with pytest.raises(am.InsufficientEvidence) as raised:
        am.dominant_frequency(y, 25, 15000, SR).require("bass cutoff ring")
    assert type(raised.value) is am.InsufficientEvidence
    assert str(raised.value) == SILENT_RING_REASON


def test_evidence_refusal_passes_every_class_the_handler_names():
    ref = m1a.ref
    assert issubclass(ref.am.InsufficientEvidence, AssertionError)
    assert not issubclass(ref.am.InsufficientEvidence,
                          (ref.Refused, OSError, ValueError, RuntimeError))


def test_drifting_rings_each_measure_and_fail_the_repeatability_gate():
    am = m1a.ref.am
    n = round(.6 * RING_SECONDS * SR)
    rings = [am.dominant_frequency(.25 * np.sin(2 * np.pi * hz * np.arange(n) / SR),
                                   25, 15000, SR).require("bass cutoff ring")
             for hz in DRIFTING_HZ]
    assert all(abs(got - hz) < 1. for got, hz in zip(rings, DRIFTING_HZ)), rings
    assert np.ptp(rings) > 10, "the tool's repeatability gate would not trip"


# ---------------------------------------------------------------------------
# the ring step: a silent ring must REFUSE by name, not traceback
# ---------------------------------------------------------------------------
def test_silent_bass_ring_exits_refused_2_with_refused_json(tmp_path):
    p, out = _run_main(tmp_path, "silent")
    _assert_reached_ring_step(p, out)
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stdout[-300:], p.stderr[-400:])
    assert _lines_starting(p.stdout, "REFUSED") == [f"REFUSED: {SILENT_RING_REASON}"], p.stdout[-400:]
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout, p.stderr[-400:]
    assert "InsufficientEvidence" not in p.stderr, p.stderr[-400:]
    assert json.loads((out / "refused.json").read_text()) == {"state": "REFUSED",
                                                              "reason": SILENT_RING_REASON}
    assert not (out / "manifest.json").exists(), "a refused run must not leave a manifest"


# ---------------------------------------------------------------------------
# an unrelated error at the same call stays an execution error (exit 1)
# ---------------------------------------------------------------------------
def test_plain_assertion_error_at_the_ring_step_still_exits_1(tmp_path):
    p, out = _run_main(tmp_path, "assertion")
    _assert_reached_ring_step(p, out)
    assert p.returncode == exit_contract.FAIL, (p.returncode, p.stdout[-200:], p.stderr[-400:])
    assert "Traceback" in p.stderr, p.stderr[-400:]
    assert _last_line(p.stderr) == f"AssertionError: {INJECTED}", p.stderr[-400:]
    assert "REFUSED" not in p.stdout, "a plain AssertionError must not be relabelled a refusal"
    assert not (out / "refused.json").exists() and not (out / "manifest.json").exists()


# ---------------------------------------------------------------------------
# the pre-existing local refusal is preserved
# ---------------------------------------------------------------------------
def test_unrepeatable_bass_ring_still_refuses_through_the_local_class(tmp_path):
    p, out = _run_main(tmp_path, "drifting")
    _assert_reached_ring_step(p, out)
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stdout[-300:], p.stderr[-400:])
    assert _lines_starting(p.stdout, "REFUSED") == [f"REFUSED: {UNREPEATABLE_REASON}"], p.stdout[-400:]
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout, p.stderr[-400:]
    assert json.loads((out / "refused.json").read_text()) == {"state": "REFUSED",
                                                              "reason": UNREPEATABLE_REASON}
    assert not (out / "manifest.json").exists()


# ---------------------------------------------------------------------------
# a run that measured is untouched (the successful-path check)
# ---------------------------------------------------------------------------
def test_repeatable_bass_ring_still_captures_and_exits_0(tmp_path):
    p, out = _run_main(tmp_path, "steady")
    assert p.returncode == exit_contract.PASS, (p.returncode, p.stdout[-300:], p.stderr[-400:])
    assert "REFUSED" not in p.stdout and "Traceback" not in p.stderr, p.stderr[-400:]
    assert _last_line(p.stdout) == CAPTURED, p.stdout[-300:]
    assert not (out / "refused.json").exists()
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["valid"] is True and manifest["case_id"] == "M1A"
    assert len(manifest["filter_rest_ring_hz"]) == 3
    assert np.ptp(manifest["filter_rest_ring_hz"]) == 0
