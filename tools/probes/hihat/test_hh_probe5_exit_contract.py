"""Known-answer exit-status controls for `hh_probe5.py`'s CLI boundary (#1611).

THE DEFECT, AS REPRODUCED RED-FIRST: this file, run with the unfixed
`hh_probe5.py` of `origin/main` @ `0a6b0861` (a detached checkout, cwd
asserted), gave 5 failed, 6 passed in 68.77 s. The probe's `main()` had no
boundary handler, and the one `except Refused` it has (the surrogate sweep's
`cy_kit` row) catches only the module's OWN class. `scorecard_record` hands the
development reference to `run_case.prepare`, whose refusal is
`preparation_contract.Refused` -- a SIBLING of the local class. A
`cy8/CY5025.WAV` cut into the strike therefore ran the whole sweep and then
escaped as a traceback, exit 1: a refusal rendered as FAIL, the inversion
`tools/exit_contract.py` exists to prevent.

WRONG IN THE ISSUE, AND WORSE (#1611's side note, and the comment in
test_hh_probe5_refused.py, both say an uncaught LOCAL refusal "exits 1, with
its message on stderr and no traceback"). Measured on the unfixed tree: the
absent-corpus control exited **0** and printed its reason NOWHERE. The local
class is `class Refused(refusal.Refused, SystemExit)` and its `__init__` calls
`super().__init__(msg)`, which is `Exception.__init__` -- it comes first in the
MRO -- so `SystemExit.__init__` never runs and the instance's `code` stays
None. CPython's top-level SystemExit handling reads `code`, finds None, and
exits 0 silently. A refusal rendered as PASS. Neither statement was ever
checked by an uncaught run; the #815 pins catch the class in-process.

WRONG, THEN RIGHT (caught by running the repository's own census, not by
reading): the first fix put the handler in a `_cli()` wrapper. Every control
below went green and `tools/refusal_sibling_scan.py` -- the census the #1576
tracer reads its rows from -- still classed both `rc.prepare` sites
`candidate-escape`, because it credits a guard only when its try sits in
`main()` or in the `__main__` dispatch. The handler now sits in that block, and
the last control pins the census to the behaviour.

THE SIX THAT PASS ON THE UNFIXED TREE do so by design; they are what the fix
must not move: the unreadable-WAV exit-1 control, the class-relation pin, the
two fixture self-checks, and the two `__main__`-block controls for behaviour
the fix leaves alone (a returning `main` exits with its own status; a
`ValueError` propagates).

THE KNOWN ANSWERS, independent of anything this repository models:

  cut into the strike   a PCM16 record whose FIRST sample is its own peak
                        begins at 100 % of its peak, at or above the 2 % onset
                        threshold `run_case.prepare` refuses on. The arithmetic
                        is asserted on the fixture before the probe sees it.
  missing corpus        a `--refs` directory that does not exist is the probe's
                        own local refusal, raised before any render.
  unreadable WAV        a file scipy cannot read raises ValueError at the same
                        call boundary: an EXECUTION ERROR, which must stay exit
                        1 -- widening the handler to `refusal.Refused` must not
                        relabel it (tools/refusal.py's "inverse defect").

WHAT THE PROBE OWES, and what these controls assert: every refusal, local or
foreign, exits `exit_contract.REFUSED` (2) and names its reason on stdout
exactly once, with no traceback, no verdict and no result file; an execution
error still exits 1 with its traceback; a `main()` that returns gets its status
through unchanged.

HOW THE CONTROLS STAY SMALL. The refusal site is AFTER the surrogate sweep (77 s
on the full grid). The subprocess controls run the real CLI with
`--quick --seconds 0.05` -- one shipped-setting render per arm, 50 ms each --
so nothing between `if __name__` and `run_case.prepare` is stubbed; the four
2.0-3.6 s renders the preconditions and the control arm pin are what remain
(27-41 s per control on a host at load 13). The absent-corpus control refuses
before any render. The in-process controls execute the shipped `__main__` block
with `main` replaced, which is the only way to reach its success path without
the corpus-wide knob-law fit the real `main()` ends with.
"""
from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys

import numpy as np
import pytest
from scipy.io import wavfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "model"))

import exit_contract                                     # noqa: E402
import hh_probe5                                         # noqa: E402
import preparation_contract as pc                        # noqa: E402
import refusal                                           # noqa: E402
import run_case as _rc_env                               # noqa: E402 -- REFS_ENV names only

SR_REF = 44100
FRAMES = 22050              # 500 ms of mono PCM16: the whole fixture is 86 kB


# ---------------------------------------------------------------------------
# the fixtures. Each is checked against its own definition below, before the
# probe ever sees it (a control built on the wrong input is a green lie).
# ---------------------------------------------------------------------------
def _cut_strike_pcm() -> np.ndarray:
    """A recording cut INTO the strike: sample 0 IS the peak (a cosine starts
    there), so it begins at 100 % of its own peak."""
    t = np.arange(FRAMES) / SR_REF
    x = np.cos(2.0 * np.pi * 5200.0 * t) * np.exp(-t / 0.05)
    return np.round(x * 32000.0).astype("<i2")


def _lead_in_pcm() -> np.ndarray:
    """A well-formed recording: 20 ms of digital silence, then the strike."""
    t = np.arange(FRAMES) / SR_REF
    x = np.cos(2.0 * np.pi * 4100.0 * t) * np.exp(-t / 0.08)
    return np.concatenate([np.zeros(int(20e-3 * SR_REF), dtype="<i2"),
                           np.round(x * 32000.0).astype("<i2")])


def _corpus(tmp_path: pathlib.Path, dev: np.ndarray | bytes) -> pathlib.Path:
    """`<refs>/cy8/` holding the two recordings `main()` requires to exist:
    the development reference (`dev`, whatever the test is injecting) and the
    held-out one (always well-formed; the refusal fires before it is read)."""
    d = tmp_path / "refs"
    (d / "cy8").mkdir(parents=True)
    p = d / hh_probe5.DEV["rel"]
    if isinstance(dev, bytes):
        p.write_bytes(dev)
    else:
        wavfile.write(str(p), SR_REF, dev)
    wavfile.write(str(d / hh_probe5.HOLDOUT["rel"]), SR_REF, _lead_in_pcm())
    return d


def _probe(refs: pathlib.Path, outdir: pathlib.Path) -> subprocess.CompletedProcess:
    """The real interpreter, the real CLI, the real `run_case.prepare`. The
    refusal site sits AFTER the surrogate sweep (77 s on the full grid), so
    the sweep is cut to `--quick --seconds 0.05`: one shipped-setting render
    per arm, 50 ms each. Nothing is stubbed on the path to the refusal.

    The corpus variables are dropped from the child's environment and set
    equal to the fixture by run_case's own constant NAMES (test_corpus_path_
    single_source): `--refs` decides the location, but argparse evaluates
    `configured_refs()` for the default, which refuses a disagreeing pair."""
    drop = (_rc_env.REFS_ENV, _rc_env.REFS_ALIAS_ENV)
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env[_rc_env.REFS_ENV] = str(refs)
    env[_rc_env.REFS_ALIAS_ENV] = str(refs)
    return subprocess.run(
        [sys.executable, str(HERE / "hh_probe5.py"), "--refs", str(refs),
         "--out", str(outdir), "--quick", "--seconds", "0.05"],
        capture_output=True, text=True, timeout=600, env=env, cwd=str(ROOT))


def _run_main_block(monkeypatch, main_stub) -> None:
    """Execute the shipped `if __name__ == "__main__":` block with `main`
    replaced, in the imported module's own namespace. The block is the
    boundary; a returning stub reaches its success path with no render."""
    src = (HERE / "hh_probe5.py").read_text()
    block = src[src.index('if __name__ == "__main__":'):]
    monkeypatch.setattr(hh_probe5, "main", main_stub)
    monkeypatch.setitem(hh_probe5.__dict__, "__name__", "__main__")
    exec(compile(block, str(HERE / "hh_probe5.py"), "exec"), hh_probe5.__dict__)


# ---------------------------------------------------------------------------
# the class relations the fix rests on, and the fixtures' own known answers
# ---------------------------------------------------------------------------
def test_the_refusing_classes_are_siblings_sharing_only_the_base():
    assert hh_probe5.Refused is not pc.Refused
    assert not issubclass(pc.Refused, hh_probe5.Refused)
    for klass in (hh_probe5.Refused, pc.Refused):
        assert issubclass(klass, refusal.Refused), klass


def test_the_cut_strike_fixture_is_what_the_issue_names():
    pcm = _cut_strike_pcm()
    pk = int(np.abs(pcm.astype(np.int32)).max())
    assert abs(int(pcm[0])) == pk, "sample 0 must BE the peak"
    assert abs(int(pcm[0])) >= 0.02 * pk, "the issue's definition: begins >= 2 % of its peak"
    assert pcm.dtype == np.dtype("<i2") and pcm.ndim == 1
    # and `run_case.prepare`, called directly on the same samples, refuses it
    # in the words the subprocess controls look for
    with pytest.raises(pc.Refused, match="cut into the strike"):
        _rc_env.prepare(pcm.astype(np.float64) / 32768.0, SR_REF, side="the fixture")


def test_the_lead_in_fixture_is_prepared_not_refused():
    y = _rc_env.prepare(_lead_in_pcm().astype(np.float64) / 32768.0, SR_REF, side="the fixture")
    assert y.ndim == 1 and np.isfinite(y).all() and float(np.abs(y).max()) == 1.0


# ---------------------------------------------------------------------------
# through the real CLI: a reference cut into the strike must REFUSE by name
# ---------------------------------------------------------------------------
def test_cut_strike_reference_exits_refused_2_naming_the_reason(tmp_path):
    outdir = tmp_path / "out"
    p = _probe(_corpus(tmp_path, _cut_strike_pcm()), outdir)
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stderr[-800:])
    assert "REFUSED  " in p.stdout, p.stdout[-800:]
    assert "cy8/CY5025.WAV" in p.stdout, p.stdout[-800:]
    assert "cut into the strike" in p.stdout, p.stdout[-800:]
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout, p.stderr[-800:]
    assert "THE DECISION" not in p.stdout, "a refused run must not reach a verdict"
    assert not (outdir / "hh5.json").exists(), "a refused run must not write a result"


# ---------------------------------------------------------------------------
# the probe's OWN refusal: exit 2 now, its reason printed once
# ---------------------------------------------------------------------------
def test_absent_corpus_exits_refused_2_with_the_local_reason(tmp_path):
    p = _probe(tmp_path / "no-such-refs", tmp_path / "out")
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stderr[-800:])
    # one REFUSED, two spaces, then the reason: the local class's own
    # "REFUSED: " prefix must not be printed a second time
    assert re.search(r"^REFUSED {2}reference corpus not at ", p.stdout, re.M), p.stdout[-800:]
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout, p.stderr[-800:]


# ---------------------------------------------------------------------------
# an unrelated error at the same boundary stays an execution error (exit 1)
# ---------------------------------------------------------------------------
def test_unreadable_reference_is_an_execution_error_not_a_refusal(tmp_path):
    p = _probe(_corpus(tmp_path, b"this is not a WAV file at all, just text"), tmp_path / "out")
    assert p.returncode == exit_contract.FAIL, (p.returncode, p.stdout[-200:], p.stderr[-800:])
    assert "Traceback" in p.stderr, p.stderr[-800:]
    assert "REFUSED" not in p.stdout, "a scipy ValueError must not be relabelled a refusal"


# ---------------------------------------------------------------------------
# the boundary in-process, no render: the format it prints, the status it
# passes through, and the one thing it must never catch
# ---------------------------------------------------------------------------
def test_a_foreign_refusal_is_printed_once_and_is_refused_2(capsys):
    e = pc.Refused("the reference recording begins at or above 2 % of its own peak")
    assert hh_probe5._refused_exit(e) == exit_contract.REFUSED
    out = capsys.readouterr()
    assert out.out == ("REFUSED  the reference recording begins at or above "
                       "2 % of its own peak\n"), out.out
    assert out.err == ""


def test_the_local_refusal_is_printed_once_and_is_refused_2(capsys):
    e = hh_probe5.Refused("the apparatus precondition failed")
    assert str(e) == "REFUSED: the apparatus precondition failed"     # what #815 pins
    assert hh_probe5._refused_exit(e) == exit_contract.REFUSED
    out = capsys.readouterr()
    assert out.out == "REFUSED  the apparatus precondition failed\n", out.out
    assert out.err == ""


def test_main_block_passes_a_returning_main_status_through(monkeypatch, capsys):
    with pytest.raises(SystemExit) as caught:
        _run_main_block(monkeypatch, lambda: exit_contract.PASS)
    assert caught.value.code == exit_contract.PASS
    assert capsys.readouterr().out == ""


def test_main_block_does_not_relabel_an_execution_error(monkeypatch, capsys):
    def boom():
        raise ValueError("File format not supported.")

    with pytest.raises(ValueError, match="File format not supported"):
        _run_main_block(monkeypatch, boom)
    assert capsys.readouterr().out == ""


# ---------------------------------------------------------------------------
# the repository's own census must agree that the site is covered
# ---------------------------------------------------------------------------
def test_the_sibling_census_classes_the_prepare_sites_as_covered():
    import refusal_sibling_scan as rss

    files = rss.tracked_files()
    idx = rss.build_index(files)
    rows = [s for s in rss.scan(files, idx)
            if s["site"].startswith("tools/probes/hihat/hh_probe5.py:")]
    prepare = [s for s in rows if s["callee"] == "run_case.prepare"]
    assert len(prepare) == 2, rows
    assert {s["classification"] for s in prepare} == {"covered(cli-base)"}, prepare
    assert not [s for s in rows if s["classification"] == "candidate-escape"], rows
