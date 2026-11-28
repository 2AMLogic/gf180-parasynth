#!/usr/bin/env python3
"""Known-answer exit-status controls: a cross-module refusal from
`run_case.load_reference` / `run_case.prepare` must reach the balance-line
probe's exit contract as REFUSED, never as an uncaught exit-1 traceback
(#1183, the `run_case.*` raiser family).

THE DEFECT, AS MEASURED ON main @ f912072f (2026-11-24, red-first run of
every control in this file): `tools/probes/balance_line_shape.py` catches
only its own local `Refused` at the CLI dispatch, but `records` reaches
`RC.load_reference` / `RC.prepare` through `_real_records()`, and
`run_case.Refused` is `preparation_contract.Refused` -- a SIBLING class,
not the local one. A digitally silent `rs8/RS.WAV` therefore escaped as a
`preparation_contract.Refused` traceback and the process exited 1. This is
the same shape #1162 repaired in `tools/probes/rs_guard_band.py`, in the
same directory, one sweep later; the fix copies it: widen the boundary
handler to `refusal.Refused`, never `except Exception`.

THE KNOWN ANSWERS, independent of anything this repository models: a
digitally silent mono PCM16 44.1 kHz WAV (4,410 frames, the #1162 fixture
spec) must be refused "reference recording rs8/RS.WAV is silent" by
`run_case.load_reference` before any verdict is printed; an absent corpus
root must keep the probe's own missing-corpus refusal (the local
pre-check in `_real_records`); a corpus file scipy cannot read at all must
STILL exit 1 as an execution error. All three were verified by hand
against the pre-fix tree before the fix landed (exit 1 + traceback, exit 2
+ reason, exit 1 + traceback, respectively).

WHAT THE PROBE OWES, and what these controls assert:

  records         the silent reference is reported by name, exit
                  `exit_contract.REFUSED` (2), no traceback and no verdict
                  table (the run must not have started reporting)
  missing corpus  the pre-existing local refusal is preserved: exit 2 and
                  the corpus-naming reason
  unreadable      a corpus file scipy cannot read raises ValueError at the
                  same boundary and must STILL exit 1 -- widening the
                  handler to the base class must not relabel an
                  implementation/input error as a refusal

WRONG-THEN-RIGHT (1, caught by running rather than reading): the first
draft asserted the section banner ("THE FOUR REAL RECORDS") never prints,
but `cmd_records` prints its banner before `_real_records()` yields its
first record, so the refusal lands one line later -- with no reading
reported. The assertion now pins what carries evidence ("REPORTED" never
appears), not the banner that carries none.

#1910 -- THE NON-FINITE CENSUS ROW, TRACED AND FOUND UNREACHABLE. No handler
change; these controls exist so that removing either conversion turns red.

The #1782 estimator-refusal census read this module off the AST and listed
it among 21 whose every handler a `NonFiniteAudio` would pass: `records`
reaches `audio_measure.require_finite` through `RC.load_reference`
(`_real_records`, first call) and `RC.prepare` (both yields), and
`NonFiniteAudio` is an `AssertionError` subclass -- NOT a `refusal.Refused`
-- so the dispatch handler above cannot catch it. The census does not see
the enclosing `try`: both `run_case` boundaries CONVERT the raise
(`raise Refused(str(why)) from why` at `run_case.load_reference` and at
`run_case.prepare`), and `run_case.Refused` is
`preparation_contract.Refused`, a `refusal.Refused`.

RUN, NOT READ, on main @ 8f3d8ed2 (2026-11-28, red-first, before any test
here was written):

  float32 rs8/RS.WAV, one NaN / one +Inf / one -Inf   exit 2, the
      `require_finite` message naming the file and the count, no traceback
  a NaN / Inf render handed to `RC.prepare` by `render_drum_solo`
      exit 2, the same message naming the "ours" side
  a plain `AssertionError` raised by `require_finite` at either boundary
      NOT converted: escapes the dispatch as an execution error (exit 1)
  a FINITE float32 rs8/RS.WAV
      exit 2 by the WIDTH guard (`run_case.require_int16_pcm`), a different
      message -- so "non-finite" in the output distinguishes the two

The candidate is therefore unreachable AS A TRACEBACK and the handler is
unchanged. What each control pins, and the mutation it must catch (every
row is run as a mutant by tools/balance_line_shape_refusal_mutants.py,
which must exit 0: all eight red at the recorded assertion):

  load_reference site   removing that conversion: exit 1 + traceback.
                        Removing the finiteness assert as well: the width
                        guard refuses the same float32 file, so the
                        `require_finite` message pin is what goes red
  prepare site          removing that conversion: `NonFiniteAudio` escapes
  AssertionError        widening either conversion (or the handler) to
                        `AssertionError` / `Exception` relabels it
  finite float32        the "non-finite" pin is not satisfied by any
                        float32 refusal, only by the finiteness assert

WRONG-THEN-RIGHT (2, both caught by running the controls, not by reading):

  1. The first AssertionError control replaced `load_reference` wholesale,
     so its plain AssertionError was raised OUTSIDE the converted region and
     exercised neither `except`. A mutation harness widening either
     conversion to `except AssertionError` left all twelve tests green. The
     raise now comes from `require_finite` itself, at each site in turn, and
     both widenings turn it red.
  2. That control's last pin was `"REFUSED" not in out`, and it was red on
     the UNMODIFIED tree at the prepare site: the reference record is
     yielded before the ours-side raise, and `cmd_records` prints the
     ESTIMATOR's own per-record "REFUSED: <reason>" verdict for a 440 Hz
     tone -- the same word from a different speaker. The pin is now the
     handler's rendering of the control's own message. (The same lesson as
     #1183's entry above, learned twice in one file.)
"""
from __future__ import annotations

import os
import pathlib
import runpy
import subprocess
import sys

import numpy as np
import pytest
from scipy.io import wavfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "probes"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "model"))

import audio_measure as am                                # noqa: E402
import exit_contract                                     # noqa: E402
import refusal                                           # noqa: E402
import run_case as _rc_env                               # noqa: E402 -- REFS_ENV names only
import run_case as rc                                    # noqa: E402
import balance_line_shape as bls                         # noqa: E402

SR = 44100
FRAMES = 4410               # 100 ms of mono PCM16: the #1162 fixture spec
FIRST_OP = next(iter(bls.OPS))                 # "RS (D10A)"
FIRST_VOICE = FIRST_OP.split()[0]              # "RS"
FIRST_REL = rc.REF_MAIN[FIRST_VOICE][0]        # rs8/RS.WAV
SCRIPT = ROOT / "tools" / "probes" / "balance_line_shape.py"
#: (value written at `index`, the `require_finite` count it must print)
NONFINITE = [(np.nan, 1000, "1 NaN, 0 Inf"),
             (np.inf, 2000, "0 NaN, 1 Inf"),
             (-np.inf, 7, "0 NaN, 1 Inf")]


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


def _tone(dtype):
    t = np.arange(FRAMES) / SR
    return (8000 * np.sin(2 * np.pi * 440 * t)).astype(dtype)


def _finite_corpus(tmp_path):
    """A readable, non-silent int16 corpus: passes every `load_reference` guard."""
    return _corpus_with(tmp_path, _tone("<i2"))


def _nonfinite_corpus(tmp_path, value, index):
    x = _tone(np.float32)
    x[index] = value
    return _corpus_with(tmp_path, x)


def _probe(cmd: str, refs: pathlib.Path) -> subprocess.CompletedProcess:
    """The real interpreter and the real environment a batch runner sees:
    GF180_TR808_REFS names the corpus root, TR808_REFS agrees with it (the
    test plan's 'unset or equal'), and nothing else about the ambient corpus
    configuration can leak in. The variable NAMES come from run_case so this
    file never spells them (test_corpus_path_single_source)."""
    drop = (_rc_env.REFS_ENV, _rc_env.REFS_ALIAS_ENV)
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env[_rc_env.REFS_ENV] = str(refs)
    env[_rc_env.REFS_ALIAS_ENV] = str(refs)
    return subprocess.run(
        [sys.executable, str(ROOT / "tools" / "probes" / "balance_line_shape.py"), cmd],
        capture_output=True, text=True, timeout=300, env=env, cwd=str(ROOT))


def _dispatch(monkeypatch, argv, refs):
    """The probe's REAL `__main__` dispatch, in this process, so a boundary
    can be monkeypatched across it. `runpy` re-executes the script, whose
    `import run_case` resolves to the already-imported (patched) module.
    The corpus is injected at `run_case.configured_refs()` -- the probe's one
    resolution point -- so this helper reads no corpus variable itself
    (test_corpus_path_single_source); the env-to-path step is the subprocess
    tests' job. Returns (exit code or None, the exception that escaped or None)."""
    monkeypatch.setattr(rc, "configured_refs", lambda: refs)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT)] + argv)
    monkeypatch.setattr(sys, "path", list(sys.path))
    try:
        runpy.run_path(str(SCRIPT), run_name="__main__")
    except SystemExit as e:
        return e.code, None
    except BaseException as e:                              # noqa: BLE001
        return None, e
    return None, None


# ---------------------------------------------------------------------------
# the class relations the whole fix rests on
# ---------------------------------------------------------------------------
def test_sibling_refused_classes_share_only_the_base():
    assert bls.Refused is not rc.Refused
    assert not issubclass(rc.Refused, bls.Refused)
    # the contract the post-fix handler relies on:
    for klass in (bls.Refused, rc.Refused):
        assert issubclass(klass, refusal.Refused), klass


# ---------------------------------------------------------------------------
# records: the silent reference must REFUSE by name, not traceback
# ---------------------------------------------------------------------------
def test_records_silent_reference_exits_refused_2_no_verdict(tmp_path):
    p = _probe("records", _silent_corpus(tmp_path))
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stderr[-400:])
    assert "REFUSED" in p.stdout, p.stdout[-400:]
    assert FIRST_REL in p.stdout and "silent" in p.stdout, p.stdout[-400:]
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout, p.stderr[-400:]
    assert "REPORTED" not in p.stdout, "a refused run must not report any record's verdict"


# ---------------------------------------------------------------------------
# the pre-existing local refusal is preserved
# ---------------------------------------------------------------------------
def test_records_absent_corpus_still_refuses_with_local_reason(tmp_path):
    p = _probe("records", tmp_path / "no-such-refs")
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stderr[-400:])
    assert "REFUSED" in p.stdout, p.stdout[-400:]
    assert "reference corpus not at" in p.stdout, p.stdout[-400:]
    assert "Traceback" not in p.stderr, p.stderr[-400:]


# ---------------------------------------------------------------------------
# unrelated errors at the same boundary stay execution errors (exit 1)
# ---------------------------------------------------------------------------
def test_unreadable_reference_is_an_execution_error_not_a_refusal(tmp_path):
    p = _probe("records", _unreadable_corpus(tmp_path))
    assert p.returncode == 1, (p.returncode, p.stdout[-200:], p.stderr[-400:])
    assert "Traceback" in p.stderr, p.stderr[-400:]
    assert "REFUSED" not in p.stdout, "a scipy ValueError must not be relabelled a refusal"


# ---------------------------------------------------------------------------
# #1910: the census premise, pinned. The dispatch handler catches
# `refusal.Refused`; `NonFiniteAudio` is outside it. Only the conversion at
# the `run_case` boundaries makes a non-finite input a refusal here.
# ---------------------------------------------------------------------------
def test_nonfinite_audio_is_not_a_refused_the_run_case_boundaries_make_it_one():
    assert issubclass(am.NonFiniteAudio, AssertionError)
    assert not issubclass(am.NonFiniteAudio, refusal.Refused)
    assert not issubclass(am.NonFiniteAudio, bls.Refused)
    assert issubclass(rc.Refused, refusal.Refused)


# ---------------------------------------------------------------------------
# #1910, the `load_reference` site: a NaN or Inf reference WAV exits 2 with
# the require_finite message -- the fingerprint of the conversion, which the
# width guard's own refusal of the same float32 file does not carry
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("value, index, counts", NONFINITE,
                         ids=["nan", "pos-inf", "neg-inf"])
def test_records_nonfinite_reference_exits_refused_2_no_verdict(tmp_path, value, index,
                                                                counts):
    p = _probe("records", _nonfinite_corpus(tmp_path, value, index))
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stderr[-400:])
    assert "REFUSED" in p.stdout, p.stdout[-400:]
    assert (f"{FIRST_REL} holds 1 non-finite samples of {FRAMES} "
            f"({counts}; first at index {index})") in p.stdout, p.stdout[-400:]
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout, p.stderr[-400:]
    assert "REPORTED" not in p.stdout, "a refused run must not report any record's verdict"


def test_records_finite_float32_reference_is_refused_by_the_width_guard(tmp_path):
    p = _probe("records", _corpus_with(tmp_path, _tone(np.float32)))
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stderr[-400:])
    assert "expected 16-bit PCM" in p.stdout, p.stdout[-400:]
    assert "non-finite" not in p.stdout, \
        "a finite file must not read as a finiteness refusal"
    assert "Traceback" not in p.stderr, p.stderr[-400:]


# ---------------------------------------------------------------------------
# #1910, the `prepare` site: a non-finite render reaches `RC.prepare` from
# `render_drum_solo` and must leave the real dispatch as REFUSED, exit 2
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("value", [np.nan, np.inf], ids=["nan", "inf"])
def test_prepare_site_nonfinite_render_exits_refused_2_through_dispatch(
        monkeypatch, capsys, tmp_path, value):
    refs = _finite_corpus(tmp_path)
    monkeypatch.setattr(rc, "render_drum_solo", lambda voice: (np.full(FRAMES, value), SR))
    code, escaped = _dispatch(monkeypatch, ["records"], refs)
    out = capsys.readouterr().out
    assert escaped is None, repr(escaped)
    assert code == exit_contract.REFUSED, (code, out[-400:])
    assert "REFUSED  ours holds" in out and "non-finite" in out, out[-400:]
    assert "REPORTED" not in out, "a refused run must not report any record's verdict"


# ---------------------------------------------------------------------------
# #1910, the control: a plain AssertionError raised INSIDE each converted
# region must still escape the dispatch as an execution error. The first
# draft of this control replaced `load_reference` wholesale, so its
# AssertionError was raised OUTSIDE the `try` -- and widening either
# conversion to `except AssertionError` left every test here green. The
# raise now comes from `require_finite` itself, at each site in turn.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("site, what_starts", [("load_reference", "reference recording"),
                                               ("prepare", "ours")],
                         ids=["load_reference", "prepare"])
def test_plain_assertion_error_inside_the_converted_region_still_escapes(
        monkeypatch, capsys, tmp_path, site, what_starts):
    refs = _finite_corpus(tmp_path)
    real = am.require_finite

    def raise_plain(x, what="the signal"):
        if what.startswith(what_starts):
            raise AssertionError("control: a plain AssertionError, not a NonFiniteAudio")
        return real(x, what)

    monkeypatch.setattr(am, "require_finite", raise_plain)
    if site == "prepare":
        monkeypatch.setattr(rc, "render_drum_solo",
                            lambda voice: (_tone(np.float64) / 32768.0, SR))
    code, escaped = _dispatch(monkeypatch, ["records"], refs)
    out = capsys.readouterr().out
    assert isinstance(escaped, AssertionError), (repr(escaped), code, out[-400:])
    assert not isinstance(escaped, refusal.Refused)
    assert not isinstance(escaped, am.NonFiniteAudio)
    assert code is None
    # The estimator's own per-record "REFUSED: <reason>" verdict for the
    # reference tone precedes the raise on the prepare site; pin only the
    # handler's rendering of THIS control, never the bare word.
    assert "REFUSED  control:" not in out


# --- the mutant runner's aggregate exit status (#2013 review) ---------------

def _mutant_report(outcomes, capsys):
    """`report()`'s exit status for a stubbed outcome map. The unit under
    test is the precedence, so the results are synthetic -- no suite run."""
    import balance_line_shape_refusal_mutants as blm
    import holdout_injected_controls as hic
    results = {f"m{i}": hic.Result(o, [], "stub") for i, o in enumerate(outcomes)}
    code = blm.report(results)
    capsys.readouterr()
    return code


@pytest.mark.parametrize("outcomes, expected", [
    (("FIRED", "FIRED"), 0),
    (("FIRED", "REFUSED"), 2),
    (("FIRED", "NO_FIRE"), 1),
    # The inversion the review measured: a survivor is a hole in the
    # controls and must outrank a refusal, which only says nothing was
    # measured. `max()` over a rank table returned 2 here.
    (("NO_FIRE", "REFUSED"), 1),
    (("REFUSED", "NO_FIRE"), 1),
], ids=["all-fired", "refused-only", "survivor-only", "survivor-then-refused",
        "refused-then-survivor"])
def test_mutant_report_exit_status_survivor_outranks_refusal(outcomes, expected, capsys):
    import holdout_injected_controls as hic
    names = {"FIRED": hic.FIRED, "NO_FIRE": hic.NO_FIRE, "REFUSED": hic.REFUSED}
    assert _mutant_report(tuple(names[o] for o in outcomes), capsys) == expected
