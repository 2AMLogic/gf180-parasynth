#!/usr/bin/env python3
"""Known-answer exit-status control: a selected filter path that renders
nothing must leave `holdout_f1d_preconditions.py` as REFUSED (exit 2), never
as an uncaught `f1_filter_path.Refused` traceback and exit 1 (#1780).

THE DEFECT, AS MEASURED ON main @ 1c56aaf6 (2026-11-28, red-first run of
this file's controls before the fix): the probe has no `except` anywhere.
`fp.SelectedFilterPath()` and `path.curve(...)` both raise
`f1_filter_path.Refused` for a stated missing precondition -- a constructor
and an instance method, which is why `tools/refusal_sibling_scan.py` has no
row for this module (it resolves callees through import bindings). With the
ladder returning zeros the probe died at :83 and exited 1:

    File "tools/probes/holdout_f1d_preconditions.py", line 83, in main
    File "tools/f1_filter_path.py", line 161, in curve
f1_filter_path.Refused: selected-path probe refused at 40 Hz, cutoff 500 Hz: silent

Exit 1 is what this tool returns when an ESTIMATOR refused -- its answer
about the setting (`return 0 if ok else 1`) -- so a path that could not
render the probe at all was indistinguishable from a finding about the
filter: a refusal rendered as FAIL, the inversion `tools/exit_contract.py`
exists to prevent.

THE KNOWN ANSWERS, independent of anything this repository models: a render
that is exactly zero carries no tone, so the amplitude estimate at the first
grid point (40 Hz) must be refused "silent", and a tool that therefore
measured nothing owes `exit_contract.REFUSED` (2). A live path owes exit 0
and its three `OK` rows, as before the fix.

WHAT THE TOOL OWES, and what the controls below assert:

  dead path       exit `exit_contract.REFUSED` (2); the refusal's reason as
                  the tool's own last line on stdout, `REFUSED  <reason>`
                  (the F1 family's convention, `f1_selected_path.py`); no
                  traceback; no estimator row, because none was measured
  live path       exit 0, three OK rows, no REFUSED -- the successful path
  pre-fix probe   the handler-free source at `PRE_FIX` (main when the defect
                  was measured, the last revision with no handler), read out
                  of this repository's own history and run under the SAME
                  seam, still escapes with
                  that traceback and exit 1: the injected-bug control. Both
                  it and the dead-path control red together mean the seam
                  stopped reaching the defect; the dead-path control red on
                  its own means the handler regressed
  which calls     a refusal from the constructor or from either `curve`
                  call (the commanded cutoff, then the wide-open reference)
                  is rendered the same way
  estimator row   unchanged: an estimator that refuses is still the tool's
                  answer, still exits 1, and never prints the tool's own
                  `REFUSED` line
  other errors    a plain RuntimeError or AssertionError at the same calls
                  still propagates: the handler catches `refusal.Refused`
                  and nothing wider (tools/refusal.py's "inverse defect")

WRONG-THEN-RIGHT (2, caught by running rather than reading):

  1. The seam the issue names is "`fixed.LadderFx.process` replaced by
     `np.zeros`". The literal does not reach the defect. The inner ladder is
     called as `process(x_hi, None, res, drive, g_q16=..., k=..., gain=...,
     ogain=..., k_q14=...)` (`model/filter_rate_chain.py`) and `np.zeros`
     takes a shape and none of that: `TypeError: zeros() takes from 1 to 3
     positional arguments but 4 were given`, raised inside
     `SelectedFilterPath.__init__`. That is an execution error, exit 1 before
     the fix AND after it -- a control that could never go green, red for the
     wrong reason. The seam here keeps the real signature -- `LadderFx.process`
     returns exactly zero for whatever it is given, which is what a dead voice
     or a muted output path looks like at the estimator -- and reproduces the
     issue's traceback line for line.

  2. The first draft counted estimator rows with
     `line.startswith(("corner", "lowband", "rolloff"))`. A fitted rolloff
     also prints a `rolloff band    476.2 .. 1515.1 Hz` line, so the LIVE
     control read four rows and went red on the untouched tool (`assert 4 ==
     3`) in the same run that was red for the right reason elsewhere. A row
     is now a name alone in the probe's 15-wide first field (`_rows`).

WHY THE REFUSAL SURFACES IN `curve`, NOT THE CONSTRUCTOR: the seam patches
the class, so the constructor's exact-match check compares a dead probe
voice against an equally dead reference voice -- zeros equal zeros -- and
passes. `curve`'s own amplitude check is the first guard to see the dead
path, which is why the controls pin :83's message.
"""
from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "model"))

import exit_contract                                     # noqa: E402
import refusal                                           # noqa: E402

PROBE = ROOT / "tools" / "probes" / "holdout_f1d_preconditions.py"
PROBE_REL = "tools/probes/holdout_f1d_preconditions.py"
PRE_FIX = "1c56aaf6"
ROWS = ("corner", "lowband", "rolloff")
DEAD_REASON = "selected-path probe refused at 40 Hz, cutoff 500 Hz: silent"
REFUSED_LINE = f"REFUSED  {DEAD_REASON}"
ESCAPED = f"f1_filter_path.Refused: {DEAD_REASON}"

_HEAD = f"import sys\nsys.path.insert(0, {str(ROOT / 'model')!r})\n"
_DEAD = ("import numpy as np\n"
         "import fixed\n"
         "fixed.LadderFx.process = "
         "lambda self, x, *a, **k: np.zeros(len(x), dtype=np.int32)\n")
_RUN = f"import runpy\nrunpy.run_path({str(PROBE)!r}, run_name='__main__')\n"
_EXEC = (f"sys.argv[0] = {str(PROBE)!r}\n"
         f"exec(compile(sys.stdin.read(), {str(PROBE)!r}, 'exec'),\n"
         f"     {{'__name__': '__main__', '__file__': {str(PROBE)!r}}})\n")


def _probe(argv=("--cut", "500"), *, dead=False, source=None) -> subprocess.CompletedProcess:
    """The probe in a child interpreter, as a batch runner sees it: the
    working tree's file, or `source` executed under the probe's own path so
    its ROOT resolves. `-c` leaves argv as `['-c', *argv]`; `run_path` puts
    the probe's path at argv[0] in place, which is what a direct invocation
    leaves there, so `source` gets the same."""
    code = _HEAD + (_DEAD if dead else "") + (_RUN if source is None else _EXEC)
    return subprocess.run([sys.executable, "-c", code, *argv], input=source or "",
                          capture_output=True, text=True, timeout=300, check=False,
                          cwd=str(ROOT))


def _pre_fix_source() -> str:
    """The handler-free probe, out of this repository's own history. A
    `git show` that cannot run is NO VERDICT, which is red -- never a skip
    that reads like a control that ran."""
    r = subprocess.run(["git", "-C", str(ROOT), "show", f"{PRE_FIX}:{PROBE_REL}"],
                       capture_output=True, text=True, timeout=60, check=False)
    assert r.returncode == 0, (
        f"NO VERDICT: git show {PRE_FIX}:{PROBE_REL} failed in this clone "
        f"({r.stderr.strip()[:160]}); the handler-free probe cannot be read back")
    assert "except" not in r.stdout, (
        f"{PRE_FIX}:{PROBE_REL} carries a handler: it is not the pre-fix probe "
        "this control exists to replay")
    return r.stdout


@pytest.fixture
def probe():
    spec = importlib.util.spec_from_file_location("holdout_f1d_preconditions_under_test",
                                                  PROBE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Refusing:
    """A selected path that raises `exc` at call `at` -- 0 the constructor,
    1 the first `curve` call, 2 the second -- and is otherwise flat at 0 dB."""

    def __init__(self, at: int, exc: BaseException):
        if at == 0:
            raise exc
        self.at, self.exc, self.calls = at, exc, 0

    def curve(self, freqs, cut_hz, res, amp):
        self.calls += 1
        if self.calls == self.at:
            raise self.exc
        return np.zeros(len(freqs)), {}


class _Flat:
    """A live selected path whose curve is flat at 0 dB: no corner to find."""

    def curve(self, freqs, cut_hz, res, amp):
        return np.zeros(len(freqs)), {}


def _rows(lines) -> list[str]:
    """The estimator rows: a name alone in the probe's 15-wide first field,
    which the `rolloff band` line a fitted rolloff prints does not have."""
    return [l for l in lines if l[:15].strip() in ROWS]


def test_the_path_refusal_is_a_refusal_and_the_status_is_a_declared_no_evidence_one():
    import f1_filter_path as fp

    assert issubclass(fp.Refused, refusal.Refused)
    assert exit_contract.REFUSED in exit_contract.NO_EVIDENCE
    assert exit_contract.REFUSED != exit_contract.FAIL


def test_dead_selected_path_exits_refused_2_naming_the_reason():
    p = _probe(dead=True)
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stderr[-400:])
    lines = p.stdout.splitlines()
    assert any(l.startswith("plateau band") for l in lines), (
        "the tool never got as far as the selected path: " + p.stdout[-400:])
    assert lines[-1] == REFUSED_LINE, p.stdout[-400:]
    assert not _rows(lines), "a refused run printed an estimator row"
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout, p.stderr[-400:]


def test_live_selected_path_still_measures_and_exits_0():
    p = _probe()
    assert p.returncode == exit_contract.PASS, (p.returncode, p.stderr[-400:])
    lines = p.stdout.splitlines()
    rows = _rows(lines)
    assert len(rows) == 3 and all(" OK " in r for r in rows), p.stdout[-400:]
    assert not any(l.startswith("REFUSED") for l in lines), p.stdout[-400:]
    assert "Traceback" not in p.stderr, p.stderr[-400:]


def test_pre_fix_probe_under_the_same_seam_escapes_exit_1():
    """The injected-bug control: with the handler reverted (the pre-fix
    source), the seam that the dead-path control runs must still produce the
    defect -- so that control cannot be green without the handler."""
    p = _probe(dead=True, source=_pre_fix_source())
    assert p.returncode == exit_contract.FAIL, (p.returncode, p.stderr[-400:])
    assert "Traceback" in p.stderr and ESCAPED in p.stderr, p.stderr[-400:]
    assert not any(l.startswith("REFUSED") for l in p.stdout.splitlines()), p.stdout[-400:]


@pytest.mark.parametrize("at", [0, 1, 2], ids=["constructor", "curve-commanded", "curve-open"])
def test_a_path_refusal_is_the_tool_refusing_at_every_guarded_call(probe, monkeypatch, capsys,
                                                                    at):
    import f1_filter_path as fp

    reason = f"selected path unavailable (raised at call {at})"
    monkeypatch.setattr(fp, "SelectedFilterPath", lambda: _Refusing(at, fp.Refused(reason)))
    assert probe.main(["--cut", "500"]) == exit_contract.REFUSED
    lines = capsys.readouterr().out.splitlines()
    assert lines[-1] == f"REFUSED  {reason}"
    assert not _rows(lines)


@pytest.mark.parametrize("exc_type", [RuntimeError, AssertionError])
def test_other_errors_at_the_guarded_calls_are_not_refusals(probe, monkeypatch, capsys,
                                                            exc_type):
    import f1_filter_path as fp

    fault = exc_type("unrelated selected-path fault")
    monkeypatch.setattr(fp, "SelectedFilterPath", lambda: _Refusing(0, fault))
    with pytest.raises(exc_type) as caught:
        probe.main(["--cut", "500"])
    assert caught.value is fault
    assert "REFUSED" not in capsys.readouterr().out


def test_an_estimator_row_refusal_is_still_the_tools_answer_exit_1(probe, monkeypatch, capsys):
    import f1_filter_path as fp

    monkeypatch.setattr(fp, "SelectedFilterPath", _Flat)
    assert probe.main(["--cut", "500"]) == exit_contract.FAIL
    lines = capsys.readouterr().out.splitlines()
    rows = _rows(lines)
    assert len(rows) == 3 and any("REFUSED" in r for r in rows), lines
    assert not any(l.startswith("REFUSED") for l in lines), lines
    assert lines[-1].startswith("NO REFERENCE WAS READ"), lines
