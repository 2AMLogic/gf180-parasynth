#!/usr/bin/env python3
"""Known-answer exit-status controls: a cross-module refusal from
`run_case.load_reference` must reach the listening aid's exit contract as
REFUSED, never as an uncaught exit-1 traceback (#1183, the `run_case.*`
raiser family).

THE DEFECT, AS MEASURED ON main @ f912072f (2026-11-24, red-first run of
every control in this file): `tools/ab_808.py` executed its whole per-sound
loop at MODULE level with no handler anywhere, and its first act per sound
was `rc.load_reference(snd, REFS)` -- which raises `run_case.Refused`, i.e.
`preparation_contract.Refused`, a class ab_808 never names. A digitally
silent `bd8/BD5050.WAV` therefore escaped as a traceback and the process
exited 1: a refusal about the apparatus rendered as FAIL, the exact
inversion of the exit contract (tools/exit_contract.py, #804). The repair
gives the script a `main()` so a boundary exists to live at, catching only
`refusal.Refused` -- never `except Exception` (tools/refusal.py's "inverse
defect": a scipy ValueError stays an execution error).

THE KNOWN ANSWERS, independent of anything this repository models: a
digitally silent mono PCM16 44.1 kHz WAV (4,410 frames, the #1162 fixture
spec) must be refused "reference recording bd8/BD5050.WAV is silent" by
`run_case.load_reference` before any render or write; an absent corpus root
must refuse naming the root; a corpus file scipy cannot read at all must
STILL exit 1 as an execution error. All three were verified by hand against
the pre-fix tree before the fix landed (exit 1 + traceback, exit 1 +
traceback, exit 1 + traceback -- the tool had no refusal boundary at all).

WHAT THE TOOL OWES, and what these controls assert:

  silent BD      exit `exit_contract.REFUSED` (2), "REFUSED" and the file
                 name on stdout, no traceback, and NO per-sound artefact
                 written (the A/B listening files must not start appearing
                 for a run that never measured anything)
  absent corpus  exit 2 with the corpus-naming reason (the raiser's own
                 message, now actually delivered)
  unreadable     a corpus file scipy cannot read raises ValueError at the
                 same boundary and must STILL exit 1 -- widening to the
                 base refusal class must not relabel an input error

The fixture's file placement is derived from `drums_fx.SOUND_NAMES[0]` and
`run_case.REF_MAIN`, so this file never spells a corpus path the tool could
drift away from.
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
import drums_fx as dx                                    # noqa: E402

SR = 44100
FRAMES = 4410               # 100 ms of mono PCM16: the #1162 fixture spec
FIRST_SOUND = dx.SOUND_NAMES[0]
FIRST_REL = rc.REF_MAIN[FIRST_SOUND][0]


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


def _ab(refs: pathlib.Path, tmp_path: pathlib.Path) -> subprocess.CompletedProcess:
    """The real interpreter and the real environment a batch runner sees:
    GF180_TR808_REFS names the corpus root, TR808_REFS agrees with it (the
    test plan's 'unset or equal'), and the tool's argv is ROOT OUT REFS."""
    drop = (_rc_env.REFS_ENV, _rc_env.REFS_ALIAS_ENV)
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env[_rc_env.REFS_ENV] = str(refs)
    env[_rc_env.REFS_ALIAS_ENV] = str(refs)
    out = tmp_path / "ab-out"
    p = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "ab_808.py"), str(ROOT), str(out), str(refs)],
        capture_output=True, text=True, timeout=300, env=env, cwd=str(ROOT))
    return p


# ---------------------------------------------------------------------------
# the silent first reference must REFUSE by name, not traceback
# ---------------------------------------------------------------------------
def test_silent_reference_exits_refused_2_no_artefacts(tmp_path):
    p = _ab(_silent_corpus(tmp_path), tmp_path)
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stderr[-400:])
    assert "REFUSED" in p.stdout, p.stdout[-400:]
    assert FIRST_REL in p.stdout and "silent" in p.stdout, p.stdout[-400:]
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout, p.stderr[-400:]
    out = tmp_path / "ab-out"
    written = list(out.glob("*.wav")) if out.exists() else []
    assert not written, f"a refused run must not leave listening artefacts: {written}"
    assert "wrote" not in p.stdout, "a refused run must not report completion"


# ---------------------------------------------------------------------------
# the absent corpus keeps the raiser's own refusal
# ---------------------------------------------------------------------------
def test_absent_corpus_exits_refused_2(tmp_path):
    p = _ab(tmp_path / "no-such-refs", tmp_path)
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
    p = _ab(_unreadable_corpus(tmp_path), tmp_path)
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stdout[-200:], p.stderr[-400:])
    assert "REFUSED" in p.stdout and "unreadable" in p.stdout, p.stdout[-400:]
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout, p.stderr[-400:]
