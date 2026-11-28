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
  NaN / Inf      a float32 reference holding NaN or Inf (all of it, or one
                 sample mid-file) is refused by the FINITENESS assert and
                 exits 2 -- never as the bare `audio_measure.NonFiniteAudio`
                 of an exit-1 traceback (#1883)

THE NaN CASE IS A REGRESSION GUARD ON A CONVERSION, NOT ON A REPAIR (#1883;
red-first on main @ 8ebfe4cd, 2026-11-28, before this file changed): the
`estimator_refusal_census` row filed for this module saw `main` call
`rc.load_reference` -> `audio_measure.require_finite`, which raises
`NonFiniteAudio` -- a class this handler never names, and not a `Refused`
subclass (InsufficientEvidence -> AssertionError). Driven for real, a float32
`bd8/BD5050.WAV` of NaN exited 2 with "REFUSED: reference recording
bd8/BD5050.WAV holds 4410 non-finite samples of 4410 (4410 NaN, 0 Inf; first
at index 0)", stderr empty, no artefact: `load_reference` converts the class
at its own boundary (`run_case.py:1626`, `except am.NonFiniteAudio: raise
Refused(...)`) before it can escape. The row is therefore UNREACHABLE and the
handler is deliberately unchanged. `rc.render_drum_solo`, the loop's other
call, was traced too: `model/drums_fx.py` never imports `audio_measure`, and
no `audio_measure` function executes in any of the sixteen renders.

THE ADVERSARY FOR THAT CASE (verification-rules rule 8): a float32 WAV with
NO NaN in it ALSO exits 2 with REFUSED here, because the width guard
(`require_int16_pcm`) refuses every float32 file -- it runs after the
finiteness assert precisely so a NaN gets the more specific diagnosis. So
"exit 2 + REFUSED" on its own is satisfied by a record the finiteness assert
passed. The NaN cases therefore assert the non-finite diagnosis is on stdout
and the width one is not;
`test_finite_float32_reference_is_refused_by_the_width_guard_not_as_non_finite`
commits that adversary, and `test_clean_reference_reaches_the_render` is the
control showing the boundary admits what it should.

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
import pytest
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
FIRST_SETTING = rc.REF_MAIN[FIRST_SOUND][1]
SECOND_REL = rc.REF_MAIN[dx.SOUND_NAMES[1]][0]
_T = np.arange(FRAMES) / SR
_TONE = np.sin(2 * np.pi * 50 * _T) * 0.5    # finite, audible: passes every assert but the width one


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
# unrelated errors at the same boundary stay execution errors (exit 1)
# ---------------------------------------------------------------------------
def test_unreadable_reference_is_an_execution_error_not_a_refusal(tmp_path):
    p = _ab(_unreadable_corpus(tmp_path), tmp_path)
    assert p.returncode == 1, (p.returncode, p.stdout[-200:], p.stderr[-400:])
    assert "Traceback" in p.stderr, p.stderr[-400:]
    assert "REFUSED" not in p.stdout, "a scipy ValueError must not be relabelled a refusal"


# ---------------------------------------------------------------------------
# a reference holding NaN or Inf is refused by the FINITENESS assert, never
# handed to the handler as the bare NonFiniteAudio (#1883)
# ---------------------------------------------------------------------------
def _one_nan_mid_file():
    a = _TONE.astype(np.float32)
    a[FRAMES // 2] = np.nan
    return a


# id -> (content, NaN count, Inf count, first bad index); the counts and the
# index are `require_finite`'s own diagnosis, so the assertion names WHICH
# assert refused (THE ADVERSARY, module docstring)
NON_FINITE_REFERENCES = {
    "all-NaN": (np.full(FRAMES, np.nan, dtype=np.float32), FRAMES, 0, 0),
    "all-Inf": (np.full(FRAMES, np.inf, dtype=np.float32), 0, FRAMES, 0),
    "one-NaN-mid-file": (_one_nan_mid_file(), 1, 0, FRAMES // 2),
}


@pytest.mark.parametrize("content, nan, inf, first", list(NON_FINITE_REFERENCES.values()),
                         ids=list(NON_FINITE_REFERENCES))
def test_non_finite_reference_is_refused_by_the_finiteness_assert(content, nan, inf, first, tmp_path):
    p = _ab(_corpus_with(tmp_path, content), tmp_path)
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stderr[-400:])
    assert "REFUSED" in p.stdout and FIRST_REL in p.stdout, p.stdout[-400:]
    assert "non-finite samples" in p.stdout, p.stdout[-400:]
    assert f"{nan} NaN, {inf} Inf; first at index {first}" in p.stdout, p.stdout[-400:]
    assert "float32 samples" not in p.stdout, "the width guard refused it, not the finiteness assert"
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout, p.stderr[-400:]
    out = tmp_path / "ab-out"
    written = list(out.glob("*.wav")) if out.exists() else []
    assert not written, f"a refused run must not leave listening artefacts: {written}"
    assert "wrote" not in p.stdout, "a refused run must not report completion"


# ---------------------------------------------------------------------------
# the adversary: a FINITE float32 record exits 2 as well -- refused by the
# width guard. Same status, different diagnosis; the two must not collide.
# ---------------------------------------------------------------------------
def test_finite_float32_reference_is_refused_by_the_width_guard_not_as_non_finite(tmp_path):
    p = _ab(_corpus_with(tmp_path, _TONE.astype(np.float32)), tmp_path)
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stderr[-400:])
    assert "float32 samples" in p.stdout and "int16" in p.stdout, p.stdout[-400:]
    assert "non-finite samples" not in p.stdout, "a finite record must not be diagnosed non-finite"
    assert "Traceback" not in p.stderr, p.stderr[-400:]


# ---------------------------------------------------------------------------
# the control: the one shape load_reference admits gets PAST the boundary
# ---------------------------------------------------------------------------
def test_clean_reference_reaches_the_render(tmp_path):
    p = _ab(_corpus_with(tmp_path, (_TONE * 20000).astype("<i2")), tmp_path)
    tag = f"00-{FIRST_SOUND}"
    assert p.stdout.splitlines()[0] == f"{tag} {FIRST_REL} {FIRST_SETTING}", p.stdout[:400:]
    ours = tmp_path / "ab-out" / f"{tag}-b-ours.wav"
    assert ours.exists(), "our render for the first sound was never written: the boundary was not crossed"
    sr, y = wavfile.read(str(ours))
    assert sr == dx.SR and int(np.abs(y.astype(np.int32)).max()) > 0, (sr, int(np.abs(y).max()))
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stderr[-400:])
    refused = [ln for ln in p.stdout.splitlines() if ln.startswith("REFUSED")]
    assert len(refused) == 1 and SECOND_REL in refused[0] and FIRST_REL not in refused[0], p.stdout[-400:]
