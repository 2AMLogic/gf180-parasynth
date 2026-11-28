#!/usr/bin/env python3
"""Known-answer exit-status controls for one #1782 census row: a non-finite
sample reaching `run_case.prepare` from inside `tools/cymbal_mid.py` must
leave as REFUSED (2), never as an uncaught exit-1 `NonFiniteAudio` (#1892).

THE ROW, AS THE CENSUS READ IT. `tools/estimator_refusal_census.py` (filed by
#1782; not on main at the sha below) walks the AST and reported that a
`NonFiniteAudio` raised by `run_case.prepare -> audio_measure.require_finite`
"would pass every handler this module defines". Read that way it does:
`NonFiniteAudio` is an `InsufficientEvidence`, that is an `AssertionError`, it
is NOT a `refusal.Refused`, and `cymbal_mid.main` catches only
`refusal.Refused`. A NaN at any of the four sites -- `fischer` (:471) and
`renders` (:484, :486, :493) -- would exit 1 with a traceback, a refusal
rendered as FAIL.

WHAT WAS MEASURED on origin/main @ 8f3d8ed2 (2026-11-28; first run at
ff1532c5, two merges earlier, where every file on this path is byte-identical):
UNREACHABLE, and no handler changed. The census follows the raise and not the
`except` that converts it. `prepare` wraps its one `require_finite` call in
`except am.NonFiniteAudio as why: raise Refused(str(why)) from why`
(tools/run_case.py:1478-1481); `rc.Refused is preparation_contract.Refused`,
which is a `refusal.Refused`; so the handler at :527 already sees it. Every
injected NaN and Inf below exited 2 with one line on stderr, no traceback,
no report row and no output file:

    REFUSED: CY5025 holds 1 non-finite samples of 48000 (1 NaN, 0 Inf; first
    at index 24000): this is not audio. ...

A NaN cannot even arrive from a FILE at the two `cb._load` sites:
`cymbal_bands._load` refuses a non-int16 WAV BEFORE the cast to float, and
int16 cannot hold one. The real command on a float32 WAV holding a NaN exits 2
with `<path>: float32 samples, expected 16-bit PCM (int16)`. What is left is a
RENDER that is non-finite (:486, :493), and that is what the injections stand
in for.

WHAT THESE CONTROLS ASSERT, each through the real interpreter and the real
`tools/cymbal_mid.py` run as `__main__`, with the site's audio boundary
replaced (the real-WAV case replaces nothing):

  nan / inf at each of the four sites   exit `exit_contract.REFUSED` (2); the
                                        one stderr line is `REFUSED: <side>
                                        holds ... non-finite samples`, naming
                                        the site and the count; no traceback,
                                        no `NonFiniteAudio`, no report row, no
                                        output file
  a plain AssertionError at each site   STILL exit 1 with a traceback. The
                                        handler must not be widened to
                                        `AssertionError` or `Exception`, which
                                        relabels an execution error as a
                                        refusal (tools/refusal.py's "inverse
                                        defect")
  the same harness, ordinary audio      exit 0 and the report is written: the
                                        refusals above are the injected
                                        sample's, not the harness's
  a NaN in a real float32 WAV           the unpatched command exits 2 and
                                        names the file

EVERY INJECTION WAS WATCHED TURN SOMETHING RED (docs/verification-rules.md
rule 2): each source mutant below was run against this file's own tests on
the tree at that sha, then reverted. MOVED is the number of cases that went
red; BLIND is the number that stayed green:

                                     nan/inf  AssertionError  real WAV  ordinary
                                     (8)      control (4)     (2)       audio (1)
  M1  conversion deleted, `prepare`  MOVED 8  BLIND 4         BLIND 2   BLIND 1
  M2  handler + `AssertionError`     BLIND 8  MOVED 4         BLIND 2   BLIND 1
  M3  handler = `Exception`          BLIND 8  MOVED 4         BLIND 2   BLIND 1
  M4  `_load`'s format refusal       BLIND 8  BLIND 4         MOVED 2   BLIND 1
      raised as a ValueError

  M1 is the escape the census predicted: exit 1 with
  `audio_measure.NonFiniteAudio: ...` on stderr. M2 and M3 are the widening
  the issue forbids ("not `AssertionError`, not `Exception`"): every nan/inf
  case STILL exits 2, so those tests cannot see it. The control does, as exit
  2 with `REFUSED: control: a plain AssertionError ...`. M4 is #1160's shape
  at the loader: the unpatched command exits 1 with a ValueError traceback.
  The class-relation test is the premise rather than a detector: BLIND to all
  four.

WRONG-THEN-RIGHT (2, both caught by running rather than reading):
  1. The first real-file run passed a RELATIVE script path under
     `cwd=tmp_path`: `python: can't open file` exits 2 with no traceback,
     which an exit-code-only check reads as REFUSED. The same trap
     test_cymbal_refusal_exit.py records for argparse; every assertion here
     pins the `REFUSED:` line.
  2. The scratch runner kept only the last 900 characters of stderr, so under
     M1 "Traceback" looked absent -- truncated, not missing. These tests read
     whole stderr.

WHAT THIS FILE DOES NOT EXERCISE, so it is not read as more than it is:
  * the replaced boundaries themselves (`cb._load`, `rc.render_drum_solo`, and
    for candidate 3 `calibrate`, `kit_with_levels` and `cc.render`, because
    the calibration takes ~4 minutes and is not what is under test). Whether a
    render can BE non-finite is a question about the model, not asked here.
  * the AssertionError control is BLIND to a handler widened to
    `AM.InsufficientEvidence` alone (a plain AssertionError is its parent).
    The issue asks for that widening only if the conversion fails, and every
    site it lists reaches the family through `prepare`.
  * the other `NonFiniteAudio via run_case` rows of the same census. The
    conversion lives in `prepare` and `load_reference`, not in any caller, so
    the class that leaves either is `rc.Refused` for all of them; what each
    module's handler does with that class is that row's own question.
"""
from __future__ import annotations

import json
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
import refusal                                           # noqa: E402
import run_case as _rc_env                               # noqa: E402 -- REFS_ENV names only

SR = 44100

#: The census row's four sites at 8f3d8ed2: the cymbal_mid arguments that reach
#: one, and the `side` its `rc.prepare` call names, which the refusal must carry.
SITES = {
    "corpus_renders": (["--renders", "--no-candidate"], "CY5025"),    # renders :484
    "fischer": (["--fischer"], "CY0000.WAV"),                         # fischer :471
    "shipped": (["--renders", "--no-candidate"], "shipped CY"),       # renders :486
    "candidate3": (["--renders"], "candidate3 CY"),                   # renders :493
}
#: every boundary, nothing injected: the paired opposite of every refusal here
CLEAN_ARGS = ["--renders", "--fischer"]

#: Materialised into tmp_path and run with the real interpreter.
DRIVER = '''\
"""The REAL tools/cymbal_mid.py as __main__, its audio boundaries replaced (#1892).

argv: ROOT SITE FILL -- <cymbal_mid's own arguments>
  SITE  the boundary that hands back FILL: corpus_renders | fischer | shipped | candidate3 | clean
  FILL  nan | inf | assertion | clean
Every other boundary hands back ordinary audio.
"""
import pathlib
import runpy
import sys

root, site, fill = sys.argv[1:4]
script = pathlib.Path(root) / "tools" / "cymbal_mid.py"
sys.argv = [str(script)] + sys.argv[sys.argv.index("--") + 1:]
sys.path[:0] = [str(pathlib.Path(root) / "model"), str(pathlib.Path(root) / "tools")]

import numpy as np

import cymbal_bands as cb
import run_case as rc

SR = 48_000


def strike():
    """An ordinary finite one-second decaying tone."""
    t = np.arange(int(1.0 * SR)) / SR
    return 0.5 * np.exp(-t / 0.2) * np.sin(2 * np.pi * 1300.0 * t)


def boundary(*_args, **_kw):
    """What a replaced boundary hands back under FILL."""
    if fill == "assertion":
        raise AssertionError(f"control: a plain AssertionError from the {site} boundary")
    y = strike()
    if fill != "clean":
        y[len(y) // 2] = {"nan": np.nan, "inf": np.inf}[fill]
    return y, SR


def ordinary(*_args, **_kw):
    return strike(), SR


def replace_candidate(replace_render):
    """candidate 3 (cymbal_mid.renders :493) sits behind a ~4 minute calibration
    that is not what is under test."""
    import cymbal_candidate as cc
    import cymbal_candidate_eval as ce
    ce.calibrate = lambda: {"amps": {}}
    ce.kit_with_levels = lambda amps, sound="CY": None
    cc.render = boundary if replace_render else ordinary


cb._load = boundary if site in ("corpus_renders", "fischer") else ordinary
rc.render_drum_solo = boundary if site == "shipped" else ordinary
if site in ("candidate3", "clean"):
    replace_candidate(site == "candidate3")

runpy.run_path(str(script), run_name="__main__")
'''


def _env(refs: pathlib.Path) -> dict:
    """The corpus variables name this run's `refs` and nothing ambient leaks in.
    The variable NAMES come from run_case so this file never spells them."""
    drop = (_rc_env.REFS_ENV, _rc_env.REFS_ALIAS_ENV)
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env[_rc_env.REFS_ENV] = str(refs)
    env[_rc_env.REFS_ALIAS_ENV] = str(refs)
    return env


def _drive(tmp_path: pathlib.Path, site: str, fill: str, args: list[str]) -> tuple:
    """The real `tools/cymbal_mid.py` as `__main__`, with `site`'s audio boundary
    handing back `fill`. Returns `(process, the --out path)`."""
    driver = tmp_path / "drive.py"
    driver.write_text(DRIVER)
    refs, out = tmp_path / "no-refs", tmp_path / "o.json"
    assert not refs.exists(), "the replaced boundary must be the only source of audio"
    p = subprocess.run(
        [sys.executable, str(driver), str(ROOT), site, fill, "--",
         "--refs", str(refs), "--out", str(out)] + args,
        capture_output=True, text=True, timeout=300, env=_env(refs), cwd=str(tmp_path))
    return p, out


def _refused_line(p: subprocess.CompletedProcess) -> str:
    """The one `REFUSED` line, or the whole stderr tail in the failure message."""
    lines = [ln for ln in p.stderr.splitlines() if ln.startswith("REFUSED")]
    assert len(lines) == 1, p.stderr[-600:]
    return lines[0]


# ---------------------------------------------------------------------------
# the class relations the row rests on: nothing but the conversion in `prepare`
# stands between the two families
# ---------------------------------------------------------------------------
def test_the_handler_cannot_see_a_non_finite_audio_by_class():
    import audio_measure as am
    import preparation_contract as pc
    import run_case as rc

    assert issubclass(am.NonFiniteAudio, am.InsufficientEvidence)
    assert issubclass(am.InsufficientEvidence, AssertionError)
    assert not issubclass(am.NonFiniteAudio, refusal.Refused)
    assert rc.Refused is pc.Refused and issubclass(rc.Refused, refusal.Refused)


# ---------------------------------------------------------------------------
# nan / inf at each of the four sites: REFUSED, by name, never a traceback
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("fill", ["nan", "inf"])
@pytest.mark.parametrize("site", sorted(SITES))
def test_a_non_finite_sample_at_each_site_is_refused_never_a_traceback(tmp_path, site, fill):
    p, out = _drive(tmp_path, site, fill, SITES[site][0])
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stderr[-600:])
    line = _refused_line(p)
    assert line.startswith(f"REFUSED: {SITES[site][1]} holds 1 non-finite samples of "), line
    nan, inf = "(1 NaN, 0 Inf; first at index ", "(0 NaN, 1 Inf; first at index "
    assert (nan if fill == "nan" else inf) in line, line
    assert "Traceback" not in p.stderr and "NonFiniteAudio" not in p.stderr, p.stderr[-600:]
    assert p.stdout == "", ("a refused run must not print a report row", p.stdout[-300:])
    assert not out.exists(), "a refused run must not write a report"


# ---------------------------------------------------------------------------
# the control: a plain AssertionError at the same boundary is an execution
# error. Widening the handler to AssertionError / Exception relabels it.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("site", sorted(SITES))
def test_a_plain_assertion_error_at_each_site_is_still_an_execution_error(tmp_path, site):
    p, out = _drive(tmp_path, site, "assertion", SITES[site][0])
    assert p.returncode == exit_contract.FAIL, (p.returncode, p.stderr[-600:])
    assert "Traceback (most recent call last)" in p.stderr, p.stderr[-600:]
    raised = f"AssertionError: control: a plain AssertionError from the {site} boundary"
    assert raised in p.stderr, p.stderr[-600:]
    # raised INSIDE main's try block, so it is the handler that lets it through
    assert "cymbal_mid.py" in p.stderr and "in _main_body" in p.stderr, p.stderr[-600:]
    assert "REFUSED" not in p.stderr and "REFUSED" not in p.stdout, p.stderr[-600:]
    assert p.stdout == "" and not out.exists()


# ---------------------------------------------------------------------------
# the paired opposite: with nothing injected, every boundary answers and the
# run completes -- so each refusal above is the injected sample's
# ---------------------------------------------------------------------------
def test_the_same_harness_on_ordinary_audio_exits_0_and_writes_the_report(tmp_path):
    p, out = _drive(tmp_path, "clean", "clean", CLEAN_ARGS)
    assert p.returncode == 0, (p.returncode, p.stdout[-400:], p.stderr[-600:])
    assert "REFUSED" not in p.stderr and "Traceback" not in p.stderr, p.stderr[-600:]
    res = json.loads(out.read_text())
    assert sorted(res["renders"]) == ["candidate3", "fischer_CY5025", "shipped"]
    assert len(res["fischer"]) == 25 and "CY0000" in res["fischer"]


# ---------------------------------------------------------------------------
# no replacement at all: a NaN in a real WAV, through the shipped loader
# ---------------------------------------------------------------------------
def _write_float32_wav(path: pathlib.Path, fill: float) -> None:
    t = np.arange(int(0.5 * SR)) / SR
    y = (0.5 * np.exp(-t / 0.1) * np.sin(2 * np.pi * 1300.0 * t)).astype(np.float32)
    y[len(y) // 2] = fill
    wavfile.write(str(path), SR, y)


@pytest.mark.parametrize("fill", [np.nan, np.inf], ids=["nan", "inf"])
def test_a_non_finite_sample_in_a_real_wav_is_refused_by_the_unpatched_command(tmp_path, fill):
    refs = tmp_path / "refs"
    (refs / "cy8").mkdir(parents=True)
    wav = refs / "cy8" / "CY5025.WAV"
    _write_float32_wav(wav, fill)
    _, back = wavfile.read(str(wav))
    assert back.dtype == np.float32 and bool((~np.isfinite(back)).any()), "the file must hold it"
    out = tmp_path / "o.json"
    p = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "cymbal_mid.py"),
         "--refs", str(refs), "--out", str(out), "--renders", "--no-candidate"],
        capture_output=True, text=True, timeout=300, env=_env(refs), cwd=str(tmp_path))
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stderr[-600:])
    line = _refused_line(p)
    assert "CY5025.WAV" in line, line
    assert "Traceback" not in p.stderr, p.stderr[-600:]
    assert p.stdout == "" and not out.exists()
