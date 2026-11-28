#!/usr/bin/env python3
"""Known-answer exit-status controls for the RTL filter-chain harness's CLI: a
cross-module refusal raised while the frozen probe grid is read must reach
`main`'s exit contract as REFUSED (2), never as an uncaught exit-1 traceback
(#1612).

THE DEFECT, AS MEASURED RED-FIRST ON main @ 0a6b0861 (2026-11-28): `main` in
tools/f1_rtl_filter_path.py wraps `RtlFilterChainPath(...)`, `_profile_freqs()`
and `path.curve(...)` in `except Refused`, the module's LOCAL class.
`_profile_freqs` calls `run_case.load_filter_reference`, which reaches
`refprofile.load_clip`, and `refprofile.Refused` is a SIBLING of the local
class, not the local class. With the frozen-audio cache absent, the refusal
therefore escaped `main` as a traceback and the process exited 1: a refusal
rendered as FAIL, the inversion of the exit contract (tools/exit_contract.py,
#804). `run_case.Refused` (the same callee's own "could not be projected"
refusal, run_case.py `load_filter_reference`) is a second sibling on the same
path and escaped the same way.

THE RED RUN, this file against the unfixed tree (2 failed, 2 passed in 1.82 s):

  absent cache    FAILED  `refprofile.Refused: the frozen reference audio for
                          'surge-type2/lp-cut250-res0.00' is not in the cache
                          (...)` escaped `main`
  run_case        FAILED  `preparation_contract.Refused: the frozen clip ...
                          could not be projected: <control>` escaped `main`
  ValueError      PASSED  an inverse control is green on either side of the
                          RIGHT repair; it exists to defeat the wrong one
  class relation  PASSED  the premise the repair rests on, already true

The shipped CLI on the same host (no `refprofile/cache/`), before the fix:
`python tools/f1_rtl_filter_path.py --json r.json` exited 1 with that
traceback on stderr and nothing on stdout. After it: exit 2, the REFUSED line
on stdout, stderr empty, no report written.

THE WRONG REPAIR, MEASURED: with `except Exception` swapped in, this file gives
1 failed, 3 passed, and the ValueError control is the one that fails.

THE KNOWN ANSWERS, independent of anything this repository models: a frozen
clip whose audio is not in the cache is refused "the frozen reference audio for
<clip> is not in the cache" by `refprofile.load_clip` -- its own stated
contract ("if the cache is not there, the answer is 'no evidence, here is
why'") -- and "no evidence" is REFUSED (2) by the exit contract, never FAIL
(1) and never a traceback. An exception that is not a refusal stays an
EXECUTION ERROR (exit 1), whatever handler is written.

WHAT THE CONTROLS ASSERT. All three behavioural controls drive the real `main`
through the real `RtlFilterChainPath` and the real `_profile_freqs`, and differ
ONLY in what is raised at `run_case.load_filter_reference`:

  absent cache      the real raise site: REFUSED (2), the clip and "is not in
                    the cache" named on stdout, no `--json` report written
  run_case.Refused  the second sibling on the same callee: REFUSED (2)
  ValueError        an execution error: propagates, nothing relabelled REFUSED

The third is the input that defeats the WRONG repair (verification-rules rule
8): `except Exception` turns every traceback into a refusal, and that control
is the one that goes red when it is swapped in.

APPARATUS PRECONDITIONS, asserted rather than assumed. (1) `RtlFilterChainPath`
refuses -- by its own local class, correctly -- when iverilog/vvp are off PATH,
BEFORE the frozen cache is read, so on such a host the known answer under test
is unreachable and the behavioural controls REFUSE (skip) rather than report.
(2) The cache location is `refprofile.PROFILE_DIR` (`clip_path` reads it at
call time). It is not named by `REFS_ENV`: that variable names the TR-808
corpus, which this path never reads. The controls point `PROFILE_DIR` at an
empty directory; the committed `profile.json` still names the clip, so only
the audio is gone, which is the state a host is in before
`tools/refprofile_restore.py`.

COMPARATOR, NOT A REPAIR: tools/test_rs_guard_band_refusal.py is the pattern
this file copies (#1162).
"""
from __future__ import annotations

import pathlib
import shutil
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "model"))

import exit_contract                                     # noqa: E402
import refusal                                           # noqa: E402
import refprofile as rp                                  # noqa: E402
import run_case as rc                                    # noqa: E402
import f1_rtl_filter_path as rtlpath                     # noqa: E402

CLIP = "surge-type2/lp-cut250-res0.00"
REFUSED_PREFIX = "f1_rtl_filter_path: REFUSED -- "

needs_iv = pytest.mark.skipif(not (shutil.which("iverilog") and shutil.which("vvp")),
                              reason="REFUSED: iverilog/vvp off PATH, so the harness "
                                     "refuses before it reads the frozen cache")


def test_the_classes_the_handler_must_catch_share_only_the_base():
    """The relation the widened handler rests on: neither callee's refusal is
    the local class, and all three are `refusal.Refused`."""
    assert rtlpath.Refused is not rp.Refused
    assert not issubclass(rp.Refused, rtlpath.Refused)
    assert not issubclass(rc.Refused, rtlpath.Refused)
    for klass in (rtlpath.Refused, rp.Refused, rc.Refused):
        assert issubclass(klass, refusal.Refused), klass


@needs_iv
def test_absent_frozen_cache_exits_refused_2_and_names_the_clip(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rp, "PROFILE_DIR", tmp_path)
    report = tmp_path / "report.json"
    code = rtlpath.main(["--json", str(report)])
    out = capsys.readouterr()
    assert code == exit_contract.REFUSED, (code, out.out[-400:], out.err[-400:])
    assert REFUSED_PREFIX in out.out, out.out[-400:]
    assert CLIP in out.out and "is not in the cache" in out.out, out.out[-400:]
    assert not report.exists(), "a refused run must not write a report"


@needs_iv
def test_a_run_case_refusal_at_the_same_boundary_exits_refused_2(tmp_path, monkeypatch, capsys):
    def not_projectable(clip_id, inject=""):
        raise rc.Refused(f"the frozen clip {clip_id} could not be projected: <control>")
    monkeypatch.setattr(rc, "load_filter_reference", not_projectable)
    code = rtlpath.main(["--json", str(tmp_path / "report.json")])
    out = capsys.readouterr()
    assert code == exit_contract.REFUSED, (code, out.out[-400:], out.err[-400:])
    assert REFUSED_PREFIX in out.out and "could not be projected" in out.out, out.out[-400:]


@needs_iv
def test_an_unrelated_error_at_the_same_boundary_is_an_execution_error(tmp_path, monkeypatch,
                                                                      capsys):
    def unreadable(clip_id, inject=""):
        raise ValueError("<control> the probe grid is not a number")
    monkeypatch.setattr(rc, "load_filter_reference", unreadable)
    with pytest.raises(ValueError, match="the probe grid is not a number"):
        rtlpath.main(["--json", str(tmp_path / "report.json")])
    out = capsys.readouterr()
    assert "REFUSED" not in out.out, "an execution error must not be relabelled a refusal"
    assert not (tmp_path / "report.json").exists()
