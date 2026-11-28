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


def _probe(refs: pathlib.Path, rig: str | None = None) -> subprocess.CompletedProcess:
    """The real interpreter and the real environment a batch runner sees:
    GF180_TR808_REFS names the corpus root, TR808_REFS agrees with it (the
    test plan's 'unset or equal'), and nothing else about the ambient corpus
    configuration can leak in. The variable NAMES come from run_case so this
    file never spells them (test_corpus_path_single_source).

    With `rig`, the child runs that source instead of the script file: the
    probe's own imports, then the rig, then the entry point's own last line.
    It is the only way to put a stand-in, or an injected raise, INSIDE a
    process whose exit status is the thing asserted."""
    drop = (_rc_env.REFS_ENV, _rc_env.REFS_ALIAS_ENV)
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env[_rc_env.REFS_ENV] = str(refs)
    env[_rc_env.REFS_ALIAS_ENV] = str(refs)
    argv = ([sys.executable, "-c", rig] if rig is not None else
            [sys.executable, str(ROOT / "tools" / "probes" / "estimator_defects.py")])
    return subprocess.run(
        argv, capture_output=True, text=True, timeout=300, env=env, cwd=str(ROOT))


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
# unrelated errors at the same boundary stay execution errors (exit 1)
# ---------------------------------------------------------------------------
def test_unreadable_reference_is_an_execution_error_not_a_refusal(tmp_path):
    p = _probe(_unreadable_corpus(tmp_path))
    assert p.returncode == 1, (p.returncode, p.stdout[-200:], p.stderr[-400:])
    assert "Traceback" in p.stderr, p.stderr[-400:]
    assert "REFUSED" not in p.stdout, "a scipy ValueError must not be relabelled a refusal"


# ---------------------------------------------------------------------------
# the estimator's OWN refusal (#1912): `audio_measure.InsufficientEvidence`,
# a class no handler between a defect section and `sys.exit(main())` named
# ---------------------------------------------------------------------------
LOADED_VOICES = ("LC", "HC", "MC", "CB")          # what the default run loads
NAMED_IN_DEFECT_5 = ("BD", "CL")                  # what defect 5 reads by name
#: a voice no section loads through `run_case.load_reference` and defect 5
#: does not read by name, so its file reaches defect 5's corpus SWEEP
UNLOADED_REL = next(rel for v, (rel, _setting) in rc.REF_MAIN.items()
                    if v not in LOADED_VOICES + NAMED_IN_DEFECT_5)
_PATHS = ", ".join(repr(str(p)) for p in
                   (ROOT / "tools" / "probes", ROOT / "tools", ROOT / "model"))

#: Speed only: the real render costs about 10 s a voice and no line of the
#: refusal path under test reads it.
_FINITE_RENDER_RIG = (
    "import numpy as np\n"
    "import run_case as rc\n"
    "def _finite_render(sound, *a, **k):\n"
    "    t = np.arange(14400) / 48000.0\n"
    "    return np.exp(-t / 0.05) * np.sin(2 * np.pi * 220 * t), 48000\n"
    "rc.render_drum_solo = _finite_render\n"
)
_PLAIN_ASSERTION_RIG = (
    "def _section(voices):\n"
    "    raise AssertionError('injected: a plain AssertionError, not an evidence refusal')\n"
    "ed.defect_1 = _section\n"
)


def _child_main(*setup: str) -> str:
    """Source for the `-c` child `_probe(rig=...)` runs: the probe's own
    imports, then `setup`, then the entry point's own last line."""
    return (f"import sys\nsys.path[:0] = [{_PATHS}]\n"
            "import estimator_defects as ed\n" + "".join(setup)
            + "sys.argv = ['estimator_defects.py']\nsys.exit(ed.main())\n")


def _strike(frames=FRAMES):
    """int16 PCM: a decaying strike with a pre-onset lead, so the reference
    gate accepts it (not silent, 16-bit, an onset after index 0)."""
    rng = np.random.default_rng(0)
    t = np.arange(frames) / SR
    x = np.exp(-t / 0.05) * np.sin(2 * np.pi * 220 * t) + 0.01 * rng.normal(size=frames)
    x[:100] = 0.0
    return np.round(x / np.abs(x).max() * 20000).astype("<i2")


def _nan_float32(frames=FRAMES):
    return np.full(frames, np.nan, dtype=np.float32)


def _write(path: pathlib.Path, content) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(str(path), SR, content)


def _loaded_corpus(tmp_path, extra=()) -> pathlib.Path:
    """A valid file for each voice the default run loads, plus `extra`
    (relative path, content) pairs."""
    d = tmp_path / "refs"
    for v in LOADED_VOICES:
        _write(d / rc.REF_MAIN[v][0], _strike())
    for rel, content in extra:
        _write(d / rel, content)
    return d


def test_nan_float32_reference_exits_refused_2_no_traceback(tmp_path):
    """#1912's census sites :176 and :417 through the shipped entry point,
    with no rig. A float32 all-NaN WAV is the one corpus file that reaches
    `audio_measure.require_finite` (an int16 file cannot hold a NaN), and
    `run_case.load_reference` turns that raise into a `Refused` two lines
    below it. Exited 2 before #1912's change as well: the record that the
    earlier gate holds, not a red run."""
    p = _probe(_corpus_with(tmp_path, _nan_float32()))
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stderr[-400:])
    assert "REFUSED" in p.stdout, p.stdout[-400:]
    assert FIRST_REL in p.stdout and "non-finite samples" in p.stdout, p.stdout[-400:]
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout, p.stderr[-400:]
    assert "check(s) FAILED" not in p.stdout


def test_nan_wav_no_section_loads_exits_refused_2_no_traceback(tmp_path):
    """#1912's red run at process level. RED FIRST on main @ ff1532c5: exit 1,
    an `audio_measure.NonFiniteAudio` traceback out of defect 5. A float32
    all-NaN WAV for a voice no section loads: defect 5 reads the corpus
    itself, and its `if pk <= 0: continue` is DEFEATED rather than tripped
    (`nan <= 0` is False, #134), so the file reaches `am.sounding_extent`,
    which raises `InsufficientEvidence`. No census row names this site; it is
    the same shape as :715 and the widened boundary exists for both."""
    refs = _loaded_corpus(tmp_path, extra=[(UNLOADED_REL, _nan_float32())])
    p = _probe(refs, rig=_child_main(_FINITE_RENDER_RIG))
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stderr[-600:])
    assert "REFUSED" in p.stdout, p.stdout[-400:]
    assert "non-finite samples" in p.stdout, p.stdout[-400:]
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout, p.stderr[-600:]
    assert "check(s) FAILED" not in p.stdout


def test_plain_assertion_error_through_the_entry_point_exits_1(tmp_path):
    """RULE 8's control at process level. `InsufficientEvidence` IS an
    `AssertionError`, so a handler written against the base class -- or
    `except Exception` -- would relabel a real bug as a refused precondition.
    A plain `AssertionError` from a section must still exit 1 with its
    traceback and print no REFUSED line, before and after #1912 alike. The
    corpus is only a directory here: the injected section reads nothing."""
    p = _probe(tmp_path, rig=_child_main(_PLAIN_ASSERTION_RIG))
    assert p.returncode == exit_contract.FAIL, (p.returncode, p.stderr[-400:])
    assert "Traceback" in p.stderr, p.stderr[-400:]
    assert "AssertionError: injected: a plain AssertionError" in p.stderr, p.stderr[-400:]
    assert "REFUSED" not in p.stdout, "a real bug must not be relabelled a refusal"
