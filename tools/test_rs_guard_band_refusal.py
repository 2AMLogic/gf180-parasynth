#!/usr/bin/env python3
"""Known-answer exit-status controls: a cross-module refusal must reach the
rimshot probe's exit contract as REFUSED, never as an uncaught exit-1
traceback (#1162).

THE DEFECT, AS MEASURED ON main @ 334e0856 (2026-11-24, red-first run of
every control in this file): `tools/probes/rs_guard_band.py` catches only its
own local `Refused` at the CLI dispatch, but `compare` and `guards` reach
`run_case.load_reference` / `run_case.prepare` through `both_records()`, and
`run_case.Refused` is `preparation_contract.Refused` -- a SIBLING class, not
the local one. A digitally silent `rs8/RS.WAV` therefore escaped as a
`preparation_contract.Refused` traceback and the process exited 1: a refusal
rendered as FAIL, the exact inversion of the exit contract
(tools/exit_contract.py, #804). The D10A partial-balance investigation needs
an unusable reference reported as a refusal, because "the corpus is silent"
is evidence about the apparatus, not about the instrument.

THE KNOWN ANSWERS, independent of anything this repository models: a
digitally silent mono PCM16 44.1 kHz WAV (4,410 frames, per the issue's
fixture spec) must be refused "reference recording rs8/RS.WAV is silent" by
`run_case.load_reference` before any render or verdict; an absent corpus root
must keep the probe's own missing-corpus refusal. Both answers were verified
by hand against the pre-fix tree before the fix landed (exit 1 + traceback,
and exit 2 + reason, respectively).

WHAT THE PROBE OWES, and what these controls assert:

  compare / guards  the silent reference is reported by name, exit
                    `exit_contract.REFUSED` (2), no traceback and no sound
                    verdict (the report header must not have started)
  missing corpus    the pre-existing local refusal is preserved: exit 2 and
                    the corpus-naming reason
  unrelated errors  a corpus file scipy cannot read at all raises ValueError
                    at the same call boundary and must STILL exit 1 as an
                    execution error -- widening the handler to the base class
                    must not relabel an implementation/input error as a
                    refusal (tools/refusal.py's "inverse defect")

COMPARATOR, NOT A REPAIR: `tools/measure_partial_balance.py measure` on the
same silent fixture already exits 2 and names the file (its `Refused =
pc.Refused` resolves to the raising class); #1162 keeps it out of the repair
set for this path. `tools/probes/cymbal_400_production_tail.py` and the
cymbal callers fixed by #1160 are the pattern this fix copies.

WRONG-THEN-RIGHT (1, caught by running rather than reading): the first draft
asserted the REFUSED line on stderr, because the cymbal fix (#1160) prints
`REFUSED:` there; this probe's established convention (its missing-corpus
control, unchanged by the fix) prints `REFUSED  <reason>` on stdout with two
spaces. Every assertion here pins the message, not just the code, because an
exit code alone is satisfied by a crashed-and-caught-anything handler.

THE #1918 ROW, MEASURED (main @ 616b8d63, 2026-11-28): the refusal census filed
this module as one whose every handler a `NonFiniteAudio` would pass. Run, not
read: a float32 `rs8/RS.WAV` with one NaN, then one +Inf, at index 100 exits 2
with stderr empty and

    REFUSED  reference recording rs8/RS.WAV holds 1 non-finite samples of 4410
    (1 NaN, 0 Inf; first at index 100): this is not audio. ...

because `run_case.load_reference` converts `NonFiniteAudio` to its own `Refused`
before it can leave (run_case.py:1625-1628) and the dispatch handler catches
the base class. Unreachable as an exit-1 traceback: no handler change, and the
tests below pin that. Coverage is the `load_reference` path only
(`rs_guard_band.py:167`); a non-finite WAV never reaches either `prepare` call
(`:169-170`), whose conversion `model/test_nan_invariance.py` covers directly
(`test_boundary_run_case_prepare_refuses_it_and_says_why`).

WHY THE FINITE FLOAT32 CONTROL IS HERE. Any float32 WAV, NaN or not, exits 2
with `REFUSED` and a path ending in `rs8/RS.WAV`: the width guard
(`require_int16_pcm`) refuses float32 one line later and names the file by
absolute path. An exit-2-plus-`REFUSED` assertion on the NaN file is therefore
satisfied by the wrong guard, and would stay green if the width check were
moved ahead of the finiteness check -- the order run_case.py:1629-1632 forbids.
So the NaN/Inf tests require the non-finite diagnosis itself, and the
all-finite float32 file is the committed adversary (verification-rules rule 8):
it must exit 2 through the WIDTH guard, with no non-finite diagnosis. With the
two guards swapped the NaN/Inf tests go red and that control stays green (run
by hand before committing, then reverted).
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import numpy as np
import pytest
from scipy.io import wavfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "probes"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "model"))

import exit_contract                                     # noqa: E402
import refusal                                           # noqa: E402
import run_case as _rc_env                               # noqa: E402 -- REFS_ENV names only

SR = 44100
FRAMES = 4410               # 100 ms of mono PCM16: the issue's fixture spec
BAD_INDEX = 100             # the one non-finite sample: the issue's fixture spec

#: what tells `am.NonFiniteAudio`'s text (model/audio_measure.py `require_finite`)
#: apart from the width refusal one line below it, which says REFUSED too
DIAGNOSIS = "non-finite samples"
DIAGNOSIS_INDEX = "first at index"


def _silent_corpus(tmp_path: pathlib.Path) -> pathlib.Path:
    d = tmp_path / "refs"
    (d / "rs8").mkdir(parents=True)
    wavfile.write(str(d / "rs8" / "RS.WAV"), SR, np.zeros(FRAMES, dtype="<i2"))
    return d


def _unreadable_corpus(tmp_path: pathlib.Path) -> pathlib.Path:
    d = tmp_path / "refs"
    (d / "rs8").mkdir(parents=True)
    (d / "rs8" / "RS.WAV").write_bytes(b"this is not a WAV file at all, just text")
    return d


def _float32_corpus(tmp_path: pathlib.Path, bad=None) -> pathlib.Path:
    """`rs8/RS.WAV` as mono float32, 0.25 throughout, sample `BAD_INDEX` set to
    `bad` when one is given. Float32 is the only WAV width that can carry a
    NaN, and it is also what the width guard refuses, so one file shape serves
    the non-finite cases and their wrong-reason control alike."""
    d = tmp_path / "refs"
    (d / "rs8").mkdir(parents=True)
    x = np.full(FRAMES, 0.25, dtype=np.float32)
    if bad is not None:
        x[BAD_INDEX] = bad
    wavfile.write(str(d / "rs8" / "RS.WAV"), SR, x)
    return d


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
        [sys.executable, str(ROOT / "tools" / "probes" / "rs_guard_band.py"), cmd],
        capture_output=True, text=True, timeout=300, env=env, cwd=str(ROOT))


# ---------------------------------------------------------------------------
# the class relations the whole fix rests on (the issue's own probe, committed
# so the boundary cannot silently regress)
# ---------------------------------------------------------------------------
def test_sibling_refused_classes_share_only_the_base():
    import run_case as rc
    import preparation_contract as pc
    import rs_guard_band as rsb

    assert rsb.Refused is not rc.Refused
    assert not issubclass(rc.Refused, rsb.Refused)
    # the contract the post-fix handler relies on:
    for klass in (rsb.Refused, rc.Refused, pc.Refused):
        assert issubclass(klass, refusal.Refused), klass


# ---------------------------------------------------------------------------
# compare / guards: the silent reference must REFUSE by name, not traceback
# ---------------------------------------------------------------------------
def test_compare_silent_reference_exits_refused_2_no_verdict(tmp_path):
    p = _probe("compare", _silent_corpus(tmp_path))
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stderr[-400:])
    assert "REFUSED" in p.stdout, p.stdout[-400:]
    assert "rs8/RS.WAV" in p.stdout and "silent" in p.stdout, p.stdout[-400:]
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout, p.stderr[-400:]
    assert "PROVENANCE" not in p.stdout, "a refused run must not start a sound report"


def test_guards_silent_reference_exits_refused_2_no_verdict(tmp_path):
    p = _probe("guards", _silent_corpus(tmp_path))
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stderr[-400:])
    assert "REFUSED" in p.stdout, p.stdout[-400:]
    assert "rs8/RS.WAV" in p.stdout and "silent" in p.stdout, p.stdout[-400:]
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout, p.stderr[-400:]
    assert "SHIPPING ESTIMATOR" not in p.stdout, "a refused run must not start a sound report"


# ---------------------------------------------------------------------------
# the pre-existing local refusal is preserved
# ---------------------------------------------------------------------------
def test_compare_absent_corpus_still_refuses_with_local_reason(tmp_path):
    p = _probe("compare", tmp_path / "no-such-refs")
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stderr[-400:])
    assert "REFUSED" in p.stdout, p.stdout[-400:]
    assert "reference corpus not at" in p.stdout, p.stdout[-400:]
    assert "Traceback" not in p.stderr, p.stderr[-400:]


# ---------------------------------------------------------------------------
# unrelated errors at the same boundary stay execution errors (exit 1)
# ---------------------------------------------------------------------------
def test_unreadable_reference_is_an_execution_error_not_a_refusal(tmp_path):
    p = _probe("compare", _unreadable_corpus(tmp_path))
    assert p.returncode == 1, (p.returncode, p.stdout[-200:], p.stderr[-400:])
    assert "Traceback" in p.stderr, p.stderr[-400:]
    assert "REFUSED" not in p.stdout, "a scipy ValueError must not be relabelled a refusal"


# ---------------------------------------------------------------------------
# #1918: a non-finite float32 reference must REFUSE with the finiteness
# diagnosis -- not traceback, and not through the width guard, which says
# REFUSED too (so the exit code and the file name alone cannot tell them apart)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("cmd", ["compare", "guards"])
@pytest.mark.parametrize("bad, kind", [(np.nan, "1 NaN, 0 Inf"), (np.inf, "0 NaN, 1 Inf")],
                         ids=["NaN", "Inf"])
def test_nonfinite_float32_reference_refuses_with_finite_diagnosis(tmp_path, cmd, bad, kind):
    """Covers the `load_reference` path only (rs_guard_band.py:167). The
    non-finite WAV stops at the loader and never reaches `prepare` (:169-170),
    whose conversion model/test_nan_invariance.py covers directly."""
    p = _probe(cmd, _float32_corpus(tmp_path, bad))
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stderr[-400:])
    assert "REFUSED" in p.stdout, p.stdout[-400:]
    assert "rs8/RS.WAV" in p.stdout, p.stdout[-400:]
    # the diagnosis am.NonFiniteAudio carries: count, kind and sample index
    assert f"1 {DIAGNOSIS} of {FRAMES} ({kind}; {DIAGNOSIS_INDEX} {BAD_INDEX})" in p.stdout, \
        p.stdout[-400:]
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout, p.stderr[-400:]
    assert "PROVENANCE" not in p.stdout and "SHIPPING ESTIMATOR" not in p.stdout, \
        "a refused run must not start a sound report"


@pytest.mark.parametrize("cmd", ["compare", "guards"])
def test_finite_float32_reference_refuses_on_width_not_finiteness(tmp_path, cmd):
    """The wrong-reason control. The same file shape with every sample finite
    must still exit 2, but through the WIDTH guard and without the non-finite
    diagnosis -- which is what proves the test above cannot be satisfied by it."""
    p = _probe(cmd, _float32_corpus(tmp_path))
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stderr[-400:])
    assert "REFUSED" in p.stdout and "rs8/RS.WAV" in p.stdout, p.stdout[-400:]
    assert "expected 16-bit PCM" in p.stdout, p.stdout[-400:]
    assert DIAGNOSIS not in p.stdout and DIAGNOSIS_INDEX not in p.stdout, \
        f"require_finite fired on a finite file: {p.stdout[-400:]}"
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout, p.stderr[-400:]


# ---------------------------------------------------------------------------
# rs_mode_drive: the SAME shape in the rimshot family's sibling probe, found
# by the #1162 census and reproduced red-first by hand before the fix
# (confirm and distort exited 1 with the preparation_contract.Refused
# traceback on this exact fixture; derive and sweep, which never read the
# corpus, exited 0 then and must still)
# ---------------------------------------------------------------------------
def _mode_drive(cmd: str, refs: pathlib.Path) -> subprocess.CompletedProcess:
    drop = (_rc_env.REFS_ENV, _rc_env.REFS_ALIAS_ENV)
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env[_rc_env.REFS_ENV] = str(refs)
    env[_rc_env.REFS_ALIAS_ENV] = str(refs)
    return subprocess.run(
        [sys.executable, str(ROOT / "tools" / "probes" / "rs_mode_drive.py"), cmd],
        capture_output=True, text=True, timeout=300, env=env, cwd=str(ROOT))


def test_mode_drive_confirm_silent_reference_exits_refused_2(tmp_path):
    p = _mode_drive("confirm", _silent_corpus(tmp_path))
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stderr[-400:])
    assert "REFUSED" in p.stdout, p.stdout[-400:]
    assert "rs8/RS.WAV" in p.stdout and "silent" in p.stdout, p.stdout[-400:]
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout, p.stderr[-400:]
    assert "PROVENANCE" not in p.stdout, "a refused run must not start a report"


def test_mode_drive_distort_silent_reference_exits_refused_2(tmp_path):
    p = _mode_drive("distort", _silent_corpus(tmp_path))
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stderr[-400:])
    assert "REFUSED" in p.stdout and "rs8/RS.WAV" in p.stdout, p.stdout[-400:]
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout, p.stderr[-400:]


def test_mode_drive_corpus_free_command_still_runs_on_silent_corpus(tmp_path):
    """derive reads no reference, so the widened handler must not have made
    the corpus-free commands refuse (the successful-path check)."""
    p = _mode_drive("derive", _silent_corpus(tmp_path))
    assert p.returncode == 0, (p.returncode, p.stdout[-300:], p.stderr[-300:])


def test_mode_drive_absent_corpus_still_refuses_with_local_reason(tmp_path):
    p = _mode_drive("confirm", tmp_path / "no-such-refs")
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stderr[-400:])
    assert "reference corpus not at" in p.stdout, p.stdout[-400:]
