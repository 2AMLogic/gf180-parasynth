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
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import numpy as np
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
# an undecodable reference is a missing precondition: REFUSED (exit 2), #1860
# (retires the #1183 / b7c88251 position that a scipy ValueError at this
# boundary stays an execution error. That position predates the readers
# refusing; unrelated errors still exit 1 -- pinned at the reader in
# test_run_case.py / test_perceptual_gate.py)
# ---------------------------------------------------------------------------
def test_unreadable_reference_is_a_refusal_naming_the_file(tmp_path):
    p = _probe("compare", _unreadable_corpus(tmp_path))
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stdout[-200:], p.stderr[-400:])
    assert "REFUSED" in p.stdout and "unreadable" in p.stdout, p.stdout[-400:]
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
