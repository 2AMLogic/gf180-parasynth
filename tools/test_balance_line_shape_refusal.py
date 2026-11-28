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
import run_case as rc                                    # noqa: E402
import balance_line_shape as bls                         # noqa: E402

SR = 44100
FRAMES = 4410               # 100 ms of mono PCM16: the #1162 fixture spec
FIRST_OP = next(iter(bls.OPS))                 # "RS (D10A)"
FIRST_VOICE = FIRST_OP.split()[0]              # "RS"
FIRST_REL = rc.REF_MAIN[FIRST_VOICE][0]        # rs8/RS.WAV


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
# an undecodable reference is a missing precondition: REFUSED (exit 2), #1860
# (retires the #1183 / b7c88251 position that a scipy ValueError at this
# boundary stays an execution error. That position predates the readers
# refusing; unrelated errors still exit 1 -- pinned at the reader in
# test_run_case.py / test_perceptual_gate.py)
# ---------------------------------------------------------------------------
def test_unreadable_reference_is_a_refusal_naming_the_file(tmp_path):
    p = _probe("records", _unreadable_corpus(tmp_path))
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stdout[-200:], p.stderr[-400:])
    assert "REFUSED" in p.stdout and "unreadable" in p.stdout, p.stdout[-400:]
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout, p.stderr[-400:]
