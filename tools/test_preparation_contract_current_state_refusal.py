#!/usr/bin/env python3
"""#1917: where a non-finite input goes in the #163 preparation-contract probe,
at each of the three sites the #1782 census listed. Run on origin/main @
8f3d8ed2 (2026-11-28) BEFORE anything was changed.

THE CANDIDATE. `tools/probes/preparation_contract_current_state.py` `run()`
catches `rc.Refused` (:47) and `pc.Refused` (:54) and nothing wider. Its three
listed sites reach `audio_measure.require_finite` through `run_case`:

    :43  rc.load_reference(voice, refdir)   run_case.load_reference:1626
    :45  rc.prepare(ref_x, ...)             run_case.prepare:1479
    :46  rc.prepare(ours_x, ...)            run_case.prepare:1479

and `require_finite` raises `NonFiniteAudio`, an `InsufficientEvidence`, i.e.
an `AssertionError` -- not a `refusal.Refused`. Were it to leave `run_case`,
the probe's handler could not see it and a refusal would leave as an exit-1
traceback.

THE VERDICT: UNREACHABLE at all three sites. The probe is unchanged. The census
row's premise -- that `run_case.prepare` and `run_case.load_reference` "call
`audio_measure.require_finite` with no guard" -- is not what the tree says:
each wraps the call in `except am.NonFiniteAudio: raise Refused(str(why))`
(run_case.py:1627-1628 and :1480-1481, both from a34ca2d88, 2026-10-01, eight
weeks before the census). The probe's existing `except rc.Refused` at :47 then
reports the pair UNAVAILABLE and `main` exits 2. An AST walk sees the call and
the raise; it does not see the `except` one line below.

WHAT WAS RUN (the real interpreter, the probe's real `__main__`, the real
`run_case` boundaries; only the input is synthetic, because this host has no
Fischer corpus):

  site  input                                   observed
  :43   rs8/RS.WAV as float32 with 1 NaN,       exit 2; "RS  UNAVAILABLE (never
        the other fifteen absent                 reached the contract): reference
                                                 recording rs8/RS.WAV holds 1
                                                 non-finite samples of 4410 (1
                                                 NaN, 0 Inf; ...)"; stderr empty
  :46   sixteen sound int16 references, a NaN   exit 2; sixteen "UNAVAILABLE ...
        in what render_drum_solo returns        our <voice> render holds 1
                                                 non-finite samples"; stderr empty
  :45   an Inf planted in what load_reference   exit 2; sixteen "UNAVAILABLE ...
        hands back (its own :1626 gate bypassed  the reference recording <rel>
        by the seam, so prepare's gate is the   holds 1 non-finite samples ...
        one under test)                          (0 NaN, 1 Inf; ...)"; stderr empty

The words are the gate's own, so each row pins WHICH gate stopped it and not
only the exit status. For :43 that matters twice: the diagnosis is the
finiteness one and not the width guard's ("float32 samples, expected 16-bit
PCM"), which is the order run_case.py:1629-1632 states on purpose.

THE CONTROL THAT MAKES THE PIN MEAN SOMETHING. A green row over a gate that
exists proves nothing unless it goes red when the gate leaves. `UNGUARDED`
below removes the translation without editing the file -- run_case's two
`except am.NonFiniteAudio` clauses stop naming the class `require_finite`
raises -- and with it installed every site does exactly what the census
predicted: exit 1, `audio_measure.NonFiniteAudio` traceback, no tally. That is
the defect, demonstrated, and the probe needs no repair only because the gate
is there.

The translation itself is pinned at the `run_case` boundary, in-process and
over NaN and Inf, by `model/test_nan_invariance.py` (#134):
`test_boundary_run_case_load_reference_refuses_it` and
`test_boundary_run_case_prepare_refuses_it_and_says_why`. Those two are the
shared-mechanism evidence for the census's other `NonFiniteAudio via run_case`
rows; this file is the one entry point the issue asked to have verified on
its own.

WRONG-THEN-RIGHT (3, all in this apparatus, all caught because the run
disagreed with what reading predicted):

  1. The first :45 seam assigned `rc.load_reference = _loader` and then had
     `_loader` call `_loader`: it recursed 990 deep and the probe died with
     `RecursionError`, exit 1 -- which reads EXACTLY like the escape being
     looked for. Every seam now keeps the original under its own name and
     asserts it took before the probe runs (`SEAM FAILED` otherwise).
  2. The first scratch harness was run as `python /tmp/x.py` with another
     agent's stray `/tmp/struct.py` beside it, which shadowed the stdlib and
     made numpy fail to import. Loud, so harmless here; a quieter shadow would
     not have been.
  3. The :45 seam copied #1763's `@TMP@` placeholder without the substitution
     that goes with it, so its self-check read a corpus at a literal
     `@TMP@/int16` and both :45 rows went red on "reference corpus not at" --
     an exit-1 traceback, again the shape of the escape. The self-check is
     what put the failure in the seam rather than in a verdict.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
from typing import NamedTuple

import numpy as np
import pytest
from scipy.io import wavfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "model"))

import exit_contract                                     # noqa: E402
import refusal                                           # noqa: E402
import run_case as _rc_env                               # noqa: E402 -- env NAMES, REF_MAIN paths

PROBE = ROOT / "tools" / "probes" / "preparation_contract_current_state.py"
SR = 44100
FRAMES = 4410               # 100 ms: 441 frames of lead, then a decaying tone
LEAD = 441
NAN_INDEX, INF_INDEX = 1000, 10
RENDER_FRAMES = 1000
RENDER_NAN_INDEX = 500

#: what a seam raises when it has not taken: reported as that, never as a
#: verdict about the probe
SEAM_FAILED = "SEAM FAILED"
RENDER_FAULT = "genuine unrelated fault"

GOOD = "int16"             # tmp_path/<GOOD>: sixteen sound int16 references
NANF32 = "nan-f32"         # tmp_path/<NANF32>: rs8/RS.WAV as float32, one NaN

_CHILD_PATHS = "sys.path[:0] = %r\n" % [str(ROOT / p) for p in ("model", "tools")]


# ---------------------------------------------------------------------------
# corpora: synthetic, because the Fischer corpus is not on every host that runs
# `pytest tools/`. Only :43's file is ever refused for being non-finite.
# ---------------------------------------------------------------------------
def _burst() -> np.ndarray:
    t = np.arange(FRAMES - LEAD) / SR
    return 8000.0 * np.sin(2 * np.pi * 180.0 * t) * np.exp(-t / 0.05)


def _int16_corpus(tmp_path: pathlib.Path) -> pathlib.Path:
    d = tmp_path / GOOD
    for voice in _rc_env.REF_MAIN:
        rel = _rc_env.REF_MAIN[voice][0]
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        x = np.zeros(FRAMES, dtype="<i2")
        x[LEAD:] = _burst().astype("<i2")
        wavfile.write(str(d / rel), SR, x)
    return d


def _nan_corpus(tmp_path: pathlib.Path, voice: str = "RS") -> pathlib.Path:
    d = tmp_path / NANF32
    rel = _rc_env.REF_MAIN[voice][0]
    (d / rel).parent.mkdir(parents=True, exist_ok=True)
    x = np.zeros(FRAMES, dtype=np.float32)
    x[LEAD:] = _burst()
    x[NAN_INDEX] = np.nan
    wavfile.write(str(d / rel), SR, x)
    return d


def _probe(refs: pathlib.Path, seam: str = "") -> subprocess.CompletedProcess:
    """The real interpreter and the real environment a batch runner sees:
    GF180_TR808_REFS names the corpus root, the alias variable is unset (the
    "unset or equal" rule, test_corpus_path_single_source), and nothing else
    about the ambient corpus configuration can leak in. The variable NAMES
    come from run_case so this file never spells them. `seam` is installed
    before the probe's `__main__` runs; `@TMP@` is the test's temp directory.
    """
    env = {k: v for k, v in os.environ.items()
           if k not in (_rc_env.REFS_ENV, _rc_env.REFS_ALIAS_ENV)}
    env[_rc_env.REFS_ENV] = str(refs)
    src = (f"import sys, runpy\n{_CHILD_PATHS}\n{seam}"
           f"runpy.run_path({str(PROBE)!r}, run_name='__main__')\n")
    return subprocess.run([sys.executable, "-c", src.replace("@TMP@", str(refs.parent))],
                          capture_output=True, text=True, timeout=300, env=env, cwd=str(ROOT))


# ---------------------------------------------------------------------------
# seams: the fault put below the probe, and asserted there before it runs
# ---------------------------------------------------------------------------
RENDER_NAN = (
    "import numpy as np, run_case as rc\n"
    "def _render(sound, *a, **k):\n"
    f"    x = np.zeros({RENDER_FRAMES}); x[{RENDER_NAN_INDEX}] = np.nan; return x, 48000\n"
    "rc.render_drum_solo = _render\n"
    "_y = rc.render_drum_solo('BD')[0]\n"
    f"assert not np.isfinite(_y).all(), {SEAM_FAILED + ': the render is finite'!r}\n")

#: a sound render, by seam, so the :45 row refuses at :45 and not after
#: sixteen real model renders (about 8 s each) that the site does not read
RENDER_OK = (
    "import numpy as np, run_case as rc\n"
    f"rc.render_drum_solo = lambda sound, *a, **k: (np.zeros({FRAMES}), 48000)\n"
    "_y = rc.render_drum_solo('BD')[0]\n"
    f"assert np.isfinite(_y).all() and len(_y) == {FRAMES}, {SEAM_FAILED + ': the render seam did not take'!r}\n")

LOADER_INF = (
    "import pathlib, numpy as np, run_case as rc\n"
    "_orig = rc.load_reference\n"
    "def _loader(voice, refdir, inject=''):\n"
    "    x, sr, rel, setting = _orig(voice, refdir, inject)\n"
    "    x = np.array(x, dtype=np.float64)\n"
    f"    x[{INF_INDEX}] = np.inf\n"
    "    return x, sr, rel, setting\n"
    "rc.load_reference = _loader\n"
    "_y = rc.load_reference('BD', pathlib.Path('@TMP@') / %r)[0]\n" % GOOD
    + f"assert not np.isfinite(_y).all(), {SEAM_FAILED + ': the reference is finite'!r}\n")

RENDER_FAULT_SEAM = (
    "import run_case as rc\n"
    "def _render(sound, *a, **k):\n"
    f"    raise AssertionError({RENDER_FAULT!r})\n"
    "rc.render_drum_solo = _render\n"
    "try:\n"
    "    rc.render_drum_solo('BD')\n"
    "except AssertionError:\n"
    "    pass\n"
    "else:\n"
    f"    raise AssertionError({SEAM_FAILED + ': the render still returns'!r})\n")

#: the injected bug: run_case's `except am.NonFiniteAudio` clauses no longer
#: name the class `require_finite` raises, so the translation to `Refused` is
#: gone from BOTH boundaries and the file is untouched
UNGUARDED = (
    "import numpy as np, run_case as rc, audio_measure as am\n"
    "class _Decoy:\n"
    "    NonFiniteAudio = type('NotTheRaisedClass', (Exception,), {})\n"
    "    def __getattr__(self, name):\n"
    "        return getattr(am, name)\n"
    "rc.am = _Decoy()\n"
    "try:\n"
    "    rc.prepare(np.array([np.nan]), 48000)\n"
    "except am.NonFiniteAudio:\n"
    "    pass\n"
    "else:\n"
    f"    raise SystemExit({SEAM_FAILED + ': prepare still translates NonFiniteAudio'!r})\n")


class Row(NamedTuple):
    """One site. `corpus` names the refs to build; `seam` is installed first.
    `stopped_by` are `require_finite`'s words for the side that refused and
    must all appear on an UNAVAILABLE line; `lines` is (a substring, how many
    UNAVAILABLE lines carry it); `absent` must appear nowhere."""
    id: str
    site: str
    corpus: str
    seam: str
    stopped_by: tuple
    lines: tuple
    absent: tuple = ()


UNAVAILABLE = "UNAVAILABLE (never reached the contract): "
_THIS_IS_NOT_AUDIO = "this is not audio"
_NAN_REF = _rc_env.REF_MAIN["RS"][0]
_BD_REF = _rc_env.REF_MAIN["BD"][0]

SITES = (
    Row("site43-load_reference", "run_case.load_reference:1627", NANF32, "",
        (f"reference recording {_NAN_REF} holds 1 non-finite samples of {FRAMES}",
         f"(1 NaN, 0 Inf; first at index {NAN_INDEX})", _THIS_IS_NOT_AUDIO),
        (("reference recording missing", 15),),
        ("float32 samples, expected 16-bit",)),
    Row("site46-prepare-ours", "run_case.prepare:1480", GOOD, RENDER_NAN,
        (f"our BD render holds 1 non-finite samples of {RENDER_FRAMES}",
         f"(1 NaN, 0 Inf; first at index {RENDER_NAN_INDEX})", _THIS_IS_NOT_AUDIO),
        ((UNAVAILABLE + "our ", 16),)),
    Row("site45-prepare-reference", "run_case.prepare:1480", GOOD, RENDER_OK + LOADER_INF,
        (f"the reference recording {_BD_REF} holds 1 non-finite samples of {FRAMES}",
         f"(0 NaN, 1 Inf; first at index {INF_INDEX})", _THIS_IS_NOT_AUDIO),
        ((UNAVAILABLE + "the reference recording ", 16),)),
)


# ---------------------------------------------------------------------------
# the class relations the whole candidate rests on
# ---------------------------------------------------------------------------
def test_non_finite_audio_is_an_assertion_error_not_a_refusal():
    """Why the census listed this module: by CLASS, the probe's handler cannot
    see the estimator refusing. That stays true whether or not any input
    reaches it, and it is the reason a repair (if one were needed) would name
    `InsufficientEvidence` rather than `AssertionError` or `Exception`."""
    import audio_measure as am
    import preparation_contract as pc

    assert issubclass(am.NonFiniteAudio, am.InsufficientEvidence)
    assert issubclass(am.InsufficientEvidence, AssertionError)
    assert not issubclass(am.NonFiniteAudio, refusal.Refused)
    assert _rc_env.Refused is pc.Refused
    assert issubclass(_rc_env.Refused, refusal.Refused)


def _run(row: Row, tmp_path: pathlib.Path, extra: str = "") -> subprocess.CompletedProcess:
    refs = (_int16_corpus if row.corpus == GOOD else _nan_corpus)(tmp_path)
    p = _probe(refs, extra + row.seam)
    assert SEAM_FAILED not in p.stderr, p.stderr[-600:]
    return p


# ---------------------------------------------------------------------------
# each site: stopped by run_case's own translation, reported UNAVAILABLE
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("row", SITES, ids=[r.id for r in SITES])
def test_a_non_finite_input_is_stopped_by_run_case_and_reported_unavailable(row, tmp_path):
    """Exit 2 with the pair UNAVAILABLE and `require_finite`'s own words on the
    line; no traceback; the `NonFiniteAudio` class name nowhere; nothing after
    the NO-VERDICT line. The words also pin WHICH site refused: :46's line
    names "our <voice> render", which `prepare` is only asked for after :45
    passed, and :45's names the recording."""
    p = _run(row, tmp_path)
    out = p.stdout + p.stderr
    assert p.returncode == exit_contract.REFUSED, (row.id, p.returncode, out[-600:])
    unavailable = [l for l in p.stdout.splitlines() if l.startswith(tuple(_rc_env.REF_MAIN))
                   and UNAVAILABLE in l]
    assert len(unavailable) == 16, (row.id, len(unavailable), p.stdout[-600:])
    carrying = [l for l in unavailable if all(t in l for t in row.stopped_by)]
    for text, n in row.lines:
        assert sum(text in l for l in unavailable) == n, (row.id, text, p.stdout[-600:])
    for text in row.absent + ("Traceback", "NonFiniteAudio"):
        assert text not in out, f"{row.id}: {text!r} in: " + out[-600:]
    assert "accepted 0 of 16; contract-refused 0; unavailable 16" in p.stdout, p.stdout[-300:]
    assert p.stdout.splitlines()[-1].startswith("REFUSED (NO-VERDICT)"), p.stdout[-300:]
    assert carrying, (row.id, "no UNAVAILABLE line carries every stopped_by word", p.stdout[-600:])


# ---------------------------------------------------------------------------
# the injected bug: with run_case's translation gone, every site escapes
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("row", SITES, ids=[r.id for r in SITES])
def test_each_site_escapes_as_a_traceback_once_the_run_case_translation_is_gone(row, tmp_path):
    """`UNGUARDED` removes the gate the rows above are stopped by. This is the
    census's prediction, reproduced: exit 1, `audio_measure.NonFiniteAudio` in
    the traceback, and no tally -- the run died inside `run()`. The pin above
    is worth what this row is worth: if the gate is ever removed, that row
    goes red on the words and this one goes red on the exit status."""
    p = _run(row, tmp_path, UNGUARDED)
    out = p.stdout + p.stderr
    assert p.returncode == exit_contract.FAIL, (row.id, p.returncode, out[-600:])
    assert "Traceback" in p.stderr and "audio_measure.NonFiniteAudio" in p.stderr, (
        row.id, p.stderr[-600:])
    assert "non-finite samples" in p.stderr, (row.id, p.stderr[-600:])
    assert "non-finite samples" not in p.stdout, (row.id, "a refusal was rendered", p.stdout[-600:])
    assert "accepted " not in p.stdout and "REFUSED" not in p.stdout, (row.id, p.stdout[-600:])


# ---------------------------------------------------------------------------
# the control against catching too broadly
# ---------------------------------------------------------------------------
def test_a_plain_assertion_error_from_the_render_seam_is_still_an_execution_error(tmp_path):
    """An ordinary AssertionError at the :46 seam is not a refusal and must
    stay an execution error: exit 1, the fault named in the traceback, no
    REFUSED line. `NonFiniteAudio` IS an AssertionError, so a handler widened
    to `except AssertionError` would silence the escape above and this control."""
    p = _probe(_int16_corpus(tmp_path), RENDER_FAULT_SEAM)
    assert SEAM_FAILED not in p.stderr, p.stderr[-600:]
    assert p.returncode == exit_contract.FAIL, (p.returncode, p.stdout[-200:], p.stderr[-600:])
    assert "Traceback" in p.stderr and f"AssertionError: {RENDER_FAULT}" in p.stderr, p.stderr[-600:]
    assert "REFUSED" not in p.stdout and UNAVAILABLE not in p.stdout, p.stdout[-600:]
