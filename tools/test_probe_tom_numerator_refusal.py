#!/usr/bin/env python3
"""Known-answer exit-status controls: a cross-module refusal from
`run_case.load_reference` must reach the tom-numerator probe's exit
contract as REFUSED, never as an uncaught exit-1 traceback (#1183, the
`run_case.*` raiser family).

THE DEFECT, AS MEASURED ON main @ f912072f (2026-11-24, red-first run of
every control in this file): `tools/probe_tom_numerator.py` guards only
`render()` with `except rc.Refused` -- the register-overflow refusal of the
compensated amp -- but `score()` calls `rc.load_reference(voice, refdir)`
OUTSIDE that guard, and nothing at `main()` interprets a refusal at all. A
digitally silent `lt8/LT50.WAV` therefore escaped as a
`preparation_contract.Refused` traceback and the process exited 1: a
refusal about the apparatus rendered as FAIL, the exact inversion of the
exit contract (tools/exit_contract.py, #804). The repair widens the
boundary to `refusal.Refused` around the measurement loop, catching the
sibling `preparation_contract.Refused` and the exact-match render guard in
one place -- never `except Exception`.

THE KNOWN ANSWERS, independent of anything this repository models: a
digitally silent mono PCM16 44.1 kHz WAV (4,410 frames, the #1162 fixture
spec) must be refused "reference recording lt8/LT50.WAV is silent" by
`run_case.load_reference` before any report is written; an absent corpus
root must refuse naming the root; a corpus file scipy cannot read must
STILL exit 1 as an execution error. All three were verified by hand against
the pre-fix tree before the fix landed (each exited 1 with the traceback --
the score path had no boundary at all).

WHAT THE PROBE OWES, and what these controls assert:

  silent LT      exit `exit_contract.REFUSED` (2), "REFUSED" and the file
                 name on stdout, no traceback, and NO --out report written
                 (a refused sweep must not look like a completed one)
  absent corpus  exit 2 with the corpus-naming reason
  unreadable     a corpus file scipy cannot read raises ValueError at the
                 same boundary and must STILL exit 1

The fixture's file placement is derived from the probe's own VOICES order
and `run_case.REF_MAIN`, so this file never spells a corpus path the tool
could drift away from.

#1909, THE OTHER CLASS THE CENSUS NAMED (2026-11-28, RUN against origin/main
@ 460284d6): `tools/estimator_refusal_census.py` lists this module because
`run_case.load_reference` (:101, :210) and `run_case.prepare` (:135) reach
`audio_measure.require_finite`, whose `NonFiniteAudio` is an
`InsufficientEvidence` -- an `AssertionError`, so neither handler here
(:223 `rc.Refused`, :232 `refusal.Refused`) stops it. The census follows the
call one level and stops; what the callee does with the exception it does
not read. Both callees wrap it (`run_case.py:1625-1628` and `:1478-1481`:
`except am.NonFiniteAudio as why: raise Refused(str(why)) from why`), so a
NaN leaves every site as `rc.Refused`.

    python tools/test_probe_tom_numerator_refusal.py      # the per-site table

  :210  NaN float32 WAV, the scoring CLI   site raised rc.Refused; HANDLED, exit
                                             2, `REFUSED: <reason>` on stdout
  :101  same corpus, a bare score() call   site raised rc.Refused; nothing
                                             handles a bare library call
  :135  all-NaN render, --explain LT       site raised rc.Refused; --explain
                                             has no handler, and no real render
                                             can reach it: output_fx is int16
  control, the boundary defeated           all three sites raise
                                             NonFiniteAudio; exit 1 + traceback

The control is the census's premise made true by injection: with
`run_case`'s `except am.NonFiniteAudio` matched against a class nothing
raises, the identical inputs escape exactly as the row predicts, and the
three controls above are green because of the boundary, not because the
input was too weak. No handler was added.

The controls read the record's facts -- the class the site raised, the class
that left the entry point, the exit status, the traceback -- and never the
tracer's per-site verdict: for a control run `run_child` says UNRESOLVED
(raised, not followed: the tracer follows refusals and `NonFiniteAudio` is
not one to it) and `view()` then re-labels the same record RETURNED, "the
call ran and came back", for a process that exited 1 (#1952). Nor do they
read `follows[0]["handlers"]`: the :210 call sits in a comprehension, which
the tracer reports as a clause-less handler on :210 and never as the
`except refusal.Refused` on :232 that ran (#1958).

WRONG THEN RIGHT (2, both caught by running rather than reading):
1. The tracer's verdict for a control run was first written here as
   UNRESOLVED, read off `classify`'s docstring; the table printed RETURNED.
2. The :210 control first asserted the handler's clause from the record;
   the run showed `clause: None` on :210, so it pins the handler by its
   effect instead -- the `REFUSED:` line and the 2.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile

import numpy as np
import pytest
from scipy.io import wavfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "model"))

import audio_measure as am                                # noqa: E402
import drums_fx as dx                                     # noqa: E402
import exit_contract                                      # noqa: E402
import refusal_candidate_trace as rct                     # noqa: E402
import run_case as _rc_env                                # noqa: E402 -- REFS_ENV names only
import run_case as rc                                     # noqa: E402
import probe_tom_numerator as ptn                         # noqa: E402

SR = 44100
FRAMES = 4410               # 100 ms of mono PCM16: the #1162 fixture spec
FIRST_VOICE = next(iter(ptn.VOICES))
FIRST_REL = rc.REF_MAIN[FIRST_VOICE][0]


def _corpus_with(tmp_path: pathlib.Path, content) -> pathlib.Path:
    d = tmp_path / "refs"
    (d / pathlib.Path(FIRST_REL).parent).mkdir(parents=True)
    p = d / FIRST_REL
    if isinstance(content, np.ndarray):
        wavfile.write(str(p), SR, content)
    else:
        p.write_bytes(content)
    return d


def _silent_corpus(tmp_path):
    return _corpus_with(tmp_path, np.zeros(FRAMES, dtype="<i2"))


def _unreadable_corpus(tmp_path):
    return _corpus_with(tmp_path, b"this is not a WAV file at all, just text")


def _probe(refs: pathlib.Path, tmp_path: pathlib.Path) -> subprocess.CompletedProcess:
    """The real interpreter and the real environment a batch runner sees
    (GF180_TR808_REFS / TR808_REFS agree, per the test plan's 'unset or
    equal'), with --refs naming the fixture corpus and --out under tmp."""
    env = _child_env(refs)
    out = tmp_path / "report.json"
    return subprocess.run(
        [sys.executable, str(ROOT / "tools" / "probe_tom_numerator.py"),
         "--refs", str(refs), "--out", str(out)],
        capture_output=True, text=True, timeout=300, env=env, cwd=str(ROOT))


# ---------------------------------------------------------------------------
# the class relations the whole fix rests on
# ---------------------------------------------------------------------------
def test_raiser_is_a_refusal_sibling_of_the_render_guard():
    import refusal

    assert issubclass(rc.Refused, refusal.Refused), (
        "the widened boundary relies on run_case.Refused subclassing refusal.Refused")


# ---------------------------------------------------------------------------
# the silent first reference must REFUSE by name, not traceback
# ---------------------------------------------------------------------------
def test_silent_reference_exits_refused_2_no_report(tmp_path):
    p = _probe(_silent_corpus(tmp_path), tmp_path)
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stderr[-400:])
    assert "REFUSED" in p.stdout, p.stdout[-400:]
    assert FIRST_REL in p.stdout and "silent" in p.stdout, p.stdout[-400:]
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout, p.stderr[-400:]
    assert not (tmp_path / "report.json").exists(), "a refused run must not write a report"


# ---------------------------------------------------------------------------
# the absent corpus keeps the raiser's own refusal
# ---------------------------------------------------------------------------
def test_absent_corpus_exits_refused_2(tmp_path):
    p = _probe(tmp_path / "no-such-refs", tmp_path)
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stderr[-400:])
    assert "REFUSED" in p.stdout and "reference corpus not at" in p.stdout, p.stdout[-400:]
    assert "Traceback" not in p.stderr, p.stderr[-400:]


# ---------------------------------------------------------------------------
# unrelated errors at the same boundary stay execution errors (exit 1)
# ---------------------------------------------------------------------------
def test_unreadable_reference_is_an_execution_error_not_a_refusal(tmp_path):
    p = _probe(_unreadable_corpus(tmp_path), tmp_path)
    assert p.returncode == 1, (p.returncode, p.stdout[-200:], p.stderr[-400:])
    assert "Traceback" in p.stderr, p.stderr[-400:]
    assert "REFUSED" not in p.stdout, "a scipy ValueError must not be relabelled a refusal"


# ---------------------------------------------------------------------------
# #1909: `NonFiniteAudio` at the three census sites, each one RUN under
# tools/refusal_candidate_trace.py with the input the census posits
# ---------------------------------------------------------------------------
PROBE = "tools/probe_tom_numerator.py"
#: census site line -> the callee it reaches
SITES = {101: "run_case.load_reference", 135: "run_case.prepare", 210: "run_case.load_reference"}
WRAP = sorted(set(SITES.values()))
RC_REFUSED = "preparation_contract.Refused"        # what `rc.Refused` resolves to
NON_FINITE = "audio_measure.NonFiniteAudio"

#: The boundary's conversion, defeated. `run_case` reads `am.NonFiniteAudio`
#: at `except` time through its module global, so a proxy whose attribute is a
#: class nothing raises lets the real `NonFiniteAudio` out of BOTH
#: `load_reference` (:1627) and `prepare` (:1480). Nothing else in `run_case`
#: changes: `require_finite` keeps its own module's globals and raises the
#: real class.
_BOUNDARY_OPEN = """\
import types
import audio_measure as am
import run_case as rc
_open = types.SimpleNamespace(**vars(am))
_open.NonFiniteAudio = type("NeverRaised", (Exception,), {})
rc.am = _open
"""

#: A NaN-bearing render: the output stage returns all-NaN float where it
#: returns its int16 rail, so `render()`'s `/ 32768.0` hands `explain` a
#: non-finite x. The entry point is exec'd as `__main__`, so the patch has to
#: be on a module the probe shares (`drums_fx`), not on the probe itself.
_NAN_RENDER = """\
import numpy as np
import drums_fx as dx
dx.output_fx = lambda v, vol, dmix, dvol, body, bvol: np.full(len(v), np.nan)
"""

#: `score()` with no preloaded reference, the only way :101 calls the loader
_SCORE_DRIVER = f"""\
import pathlib
import sys
import numpy as np
import probe_tom_numerator as ptn

ptn.score({FIRST_VOICE!r}, np.zeros(4800), 48000, pathlib.Path(sys.argv[1]))
"""


def _nan_corpus(tmp_path):
    """IEEE float is the one WAV encoding that holds a NaN; `load_reference`
    asserts finiteness BEFORE its int16 width guard, so this reaches
    `require_finite` rather than the format refusal."""
    a = np.zeros(FRAMES, dtype=np.float32)
    a[5] = np.nan
    return _corpus_with(tmp_path, a)


def _child_env(refs: pathlib.Path) -> dict:
    drop = (_rc_env.REFS_ENV, _rc_env.REFS_ALIAS_ENV)
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env[_rc_env.REFS_ENV] = str(refs)
    env[_rc_env.REFS_ALIAS_ENV] = str(refs)
    return env


def _child_dirs() -> list:
    return [str(ROOT / "tools"), str(ROOT / "model")]


def _site_run(line: int, work: pathlib.Path, refs: pathlib.Path,
              prelude: str | None = None) -> dict:
    """The entry that reaches one site with `refs` as its corpus: the scoring
    CLI (:210), a bare `score()` with no preloaded reference (:101), or
    `--explain` (:135, its render made non-finite by `_NAN_RENDER`)."""
    out = work / f"report-{line}.json"
    common = {"root": str(ROOT), "cwd": str(ROOT), "timeout": 300, "path": _child_dirs()}
    if line == 210:
        return {**common, "entry": str(ROOT / PROBE),
                "argv": ["--refs", str(refs), "--out", str(out)],
                "env": _child_env(refs), "prelude": prelude}
    if line == 101:
        driver = work / "score_driver.py"
        driver.write_text(_SCORE_DRIVER)
        return {**common, "entry": str(driver), "argv": [str(refs)],
                "env": _child_env(refs), "prelude": prelude}
    return {**common, "entry": str(ROOT / PROBE),
            "argv": ["--explain", FIRST_VOICE, "--out", str(out)],
            "env": _child_env(refs), "prelude": (prelude or "") + _NAN_RENDER}


def _site(run: dict, work: pathlib.Path, line: int, tag: str = "") -> dict:
    """The tracer's view of one site: what the callee raised there, every
    handler the refusal then reached, and what left the entry point."""
    site = f"{PROBE}:{line}"
    rec = rct.run_child(run, work / f"trace{tag}-{line}", WRAP, [site])
    return rct.view(rec, site, SITES[line])


def _site_raised(rec: dict) -> dict:
    [call] = rec["calls"]
    assert call["outcome"] == "raised", call
    return call["raised"]


# ---------------------------------------------------------------------------
# the census row's true half: neither handler can take this class
# ---------------------------------------------------------------------------
def test_census_premise_the_handlers_do_not_stop_non_finite():
    import refusal

    assert issubclass(am.NonFiniteAudio, am.InsufficientEvidence)
    assert issubclass(am.InsufficientEvidence, AssertionError)
    assert not issubclass(am.NonFiniteAudio, refusal.Refused)


# ---------------------------------------------------------------------------
# :210 -- the scoring CLI; the preload refuses before any render
# ---------------------------------------------------------------------------
def test_210_nan_reference_leaves_the_loader_as_refused_exit_2(tmp_path):
    rec = _site(_site_run(210, tmp_path, _nan_corpus(tmp_path)), tmp_path, 210)
    raised = _site_raised(rec)
    # require_finite's own words in the message: NonFiniteAudio WAS raised
    # inside the loader, and the loader is what converted it
    assert raised["class"] == RC_REFUSED and raised["in_family"], raised
    assert "1 non-finite samples" in raised["message"] and "1 NaN" in raised["message"], raised
    assert rec["verdict"] == rct.HANDLED and rec["exit_status"] == exit_contract.REFUSED
    assert rec["uncaught"] is None and not rec["traceback"], rec["stderr_tail"]
    [follow] = rec["follows"]
    assert follow["raised"]["class"] == RC_REFUSED and not follow["left_entry"]
    # The handler is pinned by its EFFECT -- the `REFUSED: <reason>` line and
    # the 2 are `except refusal.Refused` at :232-234 and nothing else prints
    # them -- not by `follow["handlers"]`: the call sits in a comprehension,
    # which the tracer misattributes (#1958), and reading it here would go red
    # when that is repaired.
    stdout = "\n".join(rec["stdout_tail"])
    assert f"REFUSED: reference recording {FIRST_REL} holds 1 non-finite samples" in stdout, stdout
    assert not (tmp_path / "report-210.json").exists(), "a refused run must not write a report"


# ---------------------------------------------------------------------------
# :101 -- a bare score() with no preloaded reference (main passes ref=)
# ---------------------------------------------------------------------------
def test_101_bare_score_with_unloaded_reference_raises_refused(tmp_path):
    rec = _site(_site_run(101, tmp_path, _nan_corpus(tmp_path)), tmp_path, 101)
    raised = _site_raised(rec)
    assert raised["class"] == RC_REFUSED and "1 non-finite samples" in raised["message"], raised
    # a bare library call has no handler, so the refusal leaves the driver --
    # as rc.Refused, never as the class the census named
    assert rec["uncaught"]["class"] == RC_REFUSED, rec["uncaught"]
    assert NON_FINITE not in rec["uncaught"]["mro"]


# ---------------------------------------------------------------------------
# :135 -- explain() on a NaN-bearing render
# ---------------------------------------------------------------------------
def test_135_nan_render_leaves_prepare_as_refused_not_non_finite(tmp_path):
    rec = _site(_site_run(135, tmp_path, tmp_path), tmp_path, 135)
    raised = _site_raised(rec)
    assert raised["class"] == RC_REFUSED and "non-finite samples" in raised["message"], raised
    assert rec["uncaught"]["class"] == RC_REFUSED, rec["uncaught"]
    assert NON_FINITE not in rec["uncaught"]["mro"]


def test_135_a_real_render_is_finite_and_prepare_takes_it():
    """The first stop at :135: the output stage returns int16, which cannot
    hold NaN or Inf, so `render()`'s `/ 32768.0` is finite by construction.
    `--explain` has no refusal handler, and this is why it never needs one."""
    z = np.zeros(8, dtype=np.int64)
    assert dx.output_fx(z, 0, z, 0, z, 0).dtype == np.int16
    x, sr = ptn.render(FIRST_VOICE, None)
    assert x.dtype == np.float64 and bool(np.isfinite(x).all())
    y = rc.prepare(x, sr, side=FIRST_VOICE)
    assert isinstance(y, np.ndarray) and bool(np.isfinite(y).all())


# ---------------------------------------------------------------------------
# the injected bug: run_case's conversion defeated, the same inputs
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("line", sorted(SITES))
def test_control_boundary_defeated_each_site_escapes_as_non_finite_exit_1(tmp_path, line):
    run = _site_run(line, tmp_path, _nan_corpus(tmp_path), _BOUNDARY_OPEN)
    rec = _site(run, tmp_path, line, "open")
    raised = _site_raised(rec)
    assert raised["class"] == NON_FINITE and not raised["in_family"], raised
    assert rec["uncaught"]["class"] == NON_FINITE, rec["uncaught"]
    assert rec["exit_status"] == exit_contract.FAIL and rec["traceback"], (
        rec["exit_status"], rec["stderr_tail"])
    assert "NonFiniteAudio" in "\n".join(rec["stderr_tail"]), rec["stderr_tail"]
    assert "REFUSED" not in "\n".join(rec["stdout_tail"]), rec["stdout_tail"]


def _table(work: pathlib.Path) -> None:
    """The per-site record: what left each site with the boundary as shipped
    and with it defeated."""
    refs = _nan_corpus(work)
    for line in sorted(SITES):
        for state, prelude in (("shipped", None), ("open", _BOUNDARY_OPEN)):
            rec = _site(_site_run(line, work, refs, prelude), work, line, state)
            raised = _site_raised(rec)["class"] if rec["calls"] else "nothing"
            left = rec["uncaught"]["class"] if rec["uncaught"] else "nothing"
            print(f":{line:<4d} boundary {state:8s} site raised {raised:32s} "
                  f"left the entry: {left:32s} "
                  f"exit {rec['exit_status']}  traceback={rec['traceback']}  {rec['verdict']}")


if __name__ == "__main__":
    work = pathlib.Path(tempfile.mkdtemp(prefix="ptn-refusal-"))
    _table(work)
    print(f"(scratch kept in {work})")
