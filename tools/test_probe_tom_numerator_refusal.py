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
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import numpy as np
from scipy.io import wavfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "model"))

import exit_contract                                     # noqa: E402
import run_case as _rc_env                               # noqa: E402 -- REFS_ENV names only
import run_case as rc                                    # noqa: E402
import probe_tom_numerator as ptn                        # noqa: E402

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
    drop = (_rc_env.REFS_ENV, _rc_env.REFS_ALIAS_ENV)
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env[_rc_env.REFS_ENV] = str(refs)
    env[_rc_env.REFS_ALIAS_ENV] = str(refs)
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
# an undecodable reference is a missing precondition: REFUSED (exit 2), #1860
# (retires the #1183 / b7c88251 position that a scipy ValueError at this
# boundary stays an execution error. That position predates the readers
# refusing; unrelated errors still exit 1 -- pinned at the reader in
# test_run_case.py / test_perceptual_gate.py)
# ---------------------------------------------------------------------------
def test_unreadable_reference_is_a_refusal_naming_the_file(tmp_path):
    p = _probe(_unreadable_corpus(tmp_path), tmp_path)
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stdout[-200:], p.stderr[-400:])
    assert "REFUSED" in p.stdout and "unreadable" in p.stdout, p.stdout[-400:]
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout, p.stderr[-400:]
