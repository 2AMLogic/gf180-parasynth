#!/usr/bin/env python3
"""Known-answer exit-status controls: a cross-module refusal from
`run_case.load_reference` must reach the estimator-defects probe's exit
contract as REFUSED, never as an uncaught exit-1 traceback (#1183, the
`run_case.*` raiser family).

THE DEFECT, AS MEASURED ON main @ f912072f (2026-11-24, red-first run of
every control in this file): `tools/probes/estimator_defects.py` checks
`REFS.exists()` at `main()` and then calls `defect_1()` .. `defect_6()`
bare. `defect_1`'s lead table and `defect_4` both call
`rc.load_reference(v, REFS)`, which raises `run_case.Refused`, i.e.
`preparation_contract.Refused` -- a class this probe never names (it has
no local `Refused` at all). A digitally silent `lc8/LC50.WAV` therefore
escaped as a traceback and the process exited 1, BELOW the probe's own
verdict contract (`verdict_exit_code`: 2 = refused precondition, 1 = a
failed invariant, 0 = all held): a refusal about the apparatus rendered
exactly like a failed invariant. The repair adds the missing boundary --
`except refusal.Refused` around the defect sections, mapping to the
probe's own refused exit (2) -- never `except Exception`.

THE KNOWN ANSWERS, independent of anything this repository models: a
digitally silent mono PCM16 44.1 kHz WAV (4,410 frames, the #1162 fixture
spec) must be refused "reference recording lc8/LC50.WAV is silent" by
`run_case.load_reference` before any defect section reports; an absent
corpus root must keep the probe's own missing-corpus refusal (its
stderr print, exit 2); a corpus file scipy cannot read at all must STILL
exit 1 as an execution error. All three were verified by hand against the
pre-fix tree before the fix landed (exit 1 + traceback, exit 2 + stderr
line, exit 1 + traceback, respectively).

WHAT THE PROBE OWES, and what these controls assert:

  silent LC      exit `exit_contract.REFUSED` (2), "REFUSED" and the file
                 name on stdout, no traceback, and no end-of-run summary
                 (a refused run has made no comparison whose FAIL could be
                 trusted -- the probe's own #805 rule)
  absent corpus  the pre-existing local refusal is preserved: exit 2 and
                 the stderr corpus line
  unreadable     a corpus file scipy cannot read raises ValueError at the
                 same boundary and must STILL exit 1

The fixture's file placement is derived from the probe's default voice
list and `run_case.REF_MAIN`, so this file never spells a corpus path the
tool could drift away from.
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
import run_case as _rc_env                               # noqa: E402 -- REFS_ENV names only
import run_case as rc                                    # noqa: E402

SR = 44100
FRAMES = 4410               # 100 ms of mono PCM16: the #1162 fixture spec
FIRST_VOICE = "LC"          # estimator_defects' default voices[0]
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


def _probe(refs: pathlib.Path) -> subprocess.CompletedProcess:
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
        [sys.executable, str(ROOT / "tools" / "probes" / "estimator_defects.py")],
        capture_output=True, text=True, timeout=300, env=env, cwd=str(ROOT))


# ---------------------------------------------------------------------------
# the silent first reference must REFUSE by name, not traceback
# ---------------------------------------------------------------------------
def test_silent_reference_exits_refused_2_no_summary(tmp_path):
    p = _probe(_silent_corpus(tmp_path))
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stderr[-400:])
    assert "REFUSED" in p.stdout, p.stdout[-400:]
    assert FIRST_REL in p.stdout and "silent" in p.stdout, p.stdout[-400:]
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout, p.stderr[-400:]
    assert "check(s) FAILED" not in p.stdout, "a refused run must not print a fail summary"


# ---------------------------------------------------------------------------
# the pre-existing local refusal is preserved
# ---------------------------------------------------------------------------
def test_absent_corpus_still_refuses_with_local_reason(tmp_path):
    p = _probe(tmp_path / "no-such-refs")
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stderr[-400:])
    assert "the Fischer corpus is not at" in p.stderr, p.stderr[-400:]
    assert "Traceback" not in p.stderr, p.stderr[-400:]


# ---------------------------------------------------------------------------
# an undecodable reference is a missing precondition: REFUSED (exit 2), #1860
# (retires the #1183 / b7c88251 position that a scipy ValueError at this
# boundary stays an execution error. That position predates the readers
# refusing; unrelated errors still exit 1 -- pinned at the reader in
# test_run_case.py / test_perceptual_gate.py)
# ---------------------------------------------------------------------------
def test_unreadable_reference_is_a_refusal_naming_the_file(tmp_path):
    p = _probe(_unreadable_corpus(tmp_path))
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stdout[-200:], p.stderr[-400:])
    assert "REFUSED" in p.stdout and "unreadable" in p.stdout, p.stdout[-400:]
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout, p.stderr[-400:]
