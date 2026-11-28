#!/usr/bin/env python3
"""Known-answer exit-status controls for the #1782 census row on
`tools/cymbal_tone_render.py`: a non-finite render reaching one of its three
`run_case.prepare` sites must leave as REFUSED (exit 3), never as an uncaught
`NonFiniteAudio` traceback (#1894).

THE CANDIDATE. The census read three sites that hand a render to
`run_case.prepare` (`measure_render`, `band_shares`, `sweep_row`) and two
handlers that name only `refusal.Refused`, and asked whether an estimator's
`NonFiniteAudio` could pass both. It cannot: `prepare` converts the raise
itself (`except am.NonFiniteAudio` -> `raise Refused(str(why))`) into
`run_case.Refused`, which IS `preparation_contract.Refused`, a subclass of the
base both handlers catch. So this is the issue's Outcome A -- no handler
changes -- and this file is the evidence, not a repair. `load_reference`
converts the same way (the same `except am.NonFiniteAudio` clause in
`tools/run_case.py`), which the sibling census rows that reach
`NonFiniteAudio` only through `prepare` / `load_reference` can cite.

RED FIRST, on the unmodified module at `ff1532c5` (2026-11-28), nothing
stubbed: `render_r4` made to return NaN when called from `measure_render`,
then `main(["--out", ..., "--skip-preservation"])` in a subprocess --

    exit 3, no result file written, 153 s
    REFUSED: candidate TONE 00 holds 192000 non-finite samples of 192000
    (192000 NaN, 0 Inf; first at index 0): this is not audio. ...

That is the census's "exit-1 traceback" branch, refuted by running it.

WHAT THESE CONTROLS ASSERT, each through a real interpreter and the real
`main`, so the exit status is the one a batch runner sees:

  NaN, Inf        at each of the three sites: exit 3 (`REFUSED_ALTERNATE`,
                  in NO_EVIDENCE); `REFUSED: <that site's own side label>
                  holds` on stderr; a NaN count for a NaN injection and an
                  Inf count for an Inf one; no traceback, no verdict on
                  stdout and no result file
  AssertionError  raised at the same call: still exit 1 with a traceback.
                  The handler must not have been widened to `Exception`.
  noconvert       `prepare` with its conversion removed: the SAME NaN is the
                  exit-1 `NonFiniteAudio` traceback. This is the NaN guards'
                  own start-red, kept committed.

THE SIDE LABEL attributes a refusal to a site. An exit code alone is
satisfied by a refusal raised anywhere upstream (docs/verification-rules.md
rule 8), so every NaN assertion pins the label its call site passes.

WHAT THE DRIVER STUBS. The claim is the route FROM a site to the exit status,
and `calibrate` (~91 s: three shipped-band renders and three `render_r4`
calls of its own) merely stands before every site. So do the precondition
(~5 s), `anchor_equivalence` and -- for `band_shares` only -- `measure_render`.
The driver replaces them. The poisoned call is the real renderer at 0.05 s
(a full-length `render_r4` is 18-23 s) with every sample replaced, so the
array's dtype, rate and shape are still the renderer's. A case costs 1.4-2.6 s
where the unstubbed run cost 153 s on this shared host, and `make verify`
collects all of `tools/`.

NOT EXERCISED: whether the renderer can produce a non-finite sample at all.
Every claim here is conditional on one arriving at a site.

WRONG-THEN-RIGHT (2, both caught by running rather than reading):
  1. The first driver unpacked every renderer's return as `(y, sr)`.
     `render_r4` returns that; `render_shipped_knob` returns the bare array,
     and the `sweep_row` driver died in a `ValueError` before reaching its
     site.
  2. This file first credited the precondition with ~61 s and `calibrate`
     with ~130 s, off a harness log whose timestamps print at the END of each
     render and so straddle two stages. Timed alone: 5 s and 91 s.
"""
from __future__ import annotations

import ast
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import exit_contract                                     # noqa: E402

MODULE = ROOT / "tools" / "cymbal_tone_render.py"

#: each census site -> the `side` its `rc.prepare` call passes, as written there
SITES = {
    "measure_render": "candidate TONE 00",
    "band_shares": "low band alone",
    "sweep_row": "knob CY0000",
}

DRIVER = '''\
import pathlib
import sys

root, site, kind, out = sys.argv[1:5]
sys.path.insert(0, str(pathlib.Path(root) / "tools"))
import numpy as np
import cymbal_tone_render as ctr

# not under test, and minutes each: everything between main() and the sites
ctr.assert_realisation_is_the_validated_one = lambda: {}
ctr.calibrate = lambda: {"anchor_level": {b: 0.25 for b in ("low", "decay", "short")},
                         "per_band": {}}
ctr.anchor_equivalence = lambda: {}
if site == "band_shares":
    ctr.measure_render = lambda anchor_level: {}

if kind == "noconvert":
    import audio_measure as am
    real_prepare = ctr.rc.prepare

    def unconverted(x, sr, *, side="the recording"):
        am.require_finite(np.asarray(x, dtype=np.float64), side)
        return real_prepare(x, sr, side=side)

    ctr.rc.prepare = unconverted

name = "render_shipped_knob" if site == "sweep_row" else "render_r4"
real = getattr(ctr, name)
value = {"nan": np.nan, "inf": np.inf}.get(kind)


def poisoned(*a, **k):
    y = real(*a, **{**k, "seconds": 0.05})
    if kind == "assertion":
        raise AssertionError(f"injected at the {site} site")
    arr = y[0] if isinstance(y, tuple) else y
    bad = np.full(arr.shape, value, dtype=np.float64)
    return (bad, y[1]) if isinstance(y, tuple) else bad


setattr(ctr, name, poisoned)
sys.exit(ctr.main(["--out", out, "--skip-preservation"]
                  + (["--knob-render"] if site == "sweep_row" else [])))
'''


def _drive(tmp_path: pathlib.Path, site: str, kind: str) -> subprocess.CompletedProcess:
    driver = tmp_path / f"drive-{site}-{kind}.py"
    driver.write_text(DRIVER)
    return subprocess.run(
        [sys.executable, str(driver), str(ROOT), site, kind, str(tmp_path / "o.json")],
        capture_output=True, text=True, timeout=300, cwd=str(ROOT))


def _why(p: subprocess.CompletedProcess) -> str:
    return f"exit {p.returncode}\n--- stdout\n{p.stdout[-400:]}\n--- stderr\n{p.stderr[-800:]}"


# ---------------------------------------------------------------------------
# the driver calls `main(argv)` under `sys.exit`. That is the shipped entry
# only while the entry block is exactly `raise SystemExit(main())`
# ---------------------------------------------------------------------------
def test_the_driver_stands_in_for_the_shipped_entry_block():
    guard = ast.parse(MODULE.read_text()).body[-1]
    assert isinstance(guard, ast.If) and not guard.orelse
    assert ast.unparse(guard.test) == "__name__ == '__main__'"
    assert [ast.unparse(s) for s in guard.body] == ["raise SystemExit(main())"]


# ---------------------------------------------------------------------------
# NaN and Inf at each of the three sites: REFUSED, naming the site
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("kind, tally", [("nan", " NaN, 0 Inf"), ("inf", "0 NaN, ")])
@pytest.mark.parametrize("site", sorted(SITES))
def test_a_non_finite_render_leaves_as_refused_naming_its_site(tmp_path, site, kind, tally):
    p = _drive(tmp_path, site, kind)
    assert p.returncode == exit_contract.REFUSED_ALTERNATE, _why(p)
    assert p.returncode in exit_contract.NO_EVIDENCE and p.returncode != exit_contract.FAIL
    assert f"REFUSED: {SITES[site]} holds" in p.stderr, _why(p)
    assert tally in p.stderr, _why(p)
    assert "Traceback" not in p.stderr and "NonFiniteAudio" not in p.stderr, _why(p)
    for token in ("REFUSED", "PASS", "FAIL"):
        assert token not in p.stdout, (token, _why(p))
    assert not (tmp_path / "o.json").exists(), "a refused run must not write a result"


# ---------------------------------------------------------------------------
# rule 8: the input that would defeat the NaN guards -- a handler widened to
# `Exception` relabels an execution error as a refusal
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("site", sorted(SITES))
def test_a_plain_assertionerror_at_the_same_call_is_still_exit_1(tmp_path, site):
    p = _drive(tmp_path, site, "assertion")
    assert p.returncode == exit_contract.FAIL, _why(p)
    assert "Traceback" in p.stderr, _why(p)
    assert f"AssertionError: injected at the {site} site" in p.stderr, _why(p)
    assert "REFUSED" not in p.stderr and "REFUSED" not in p.stdout, _why(p)
    assert not (tmp_path / "o.json").exists()


# ---------------------------------------------------------------------------
# the NaN guards' start-red: with `prepare`'s conversion gone, the identical
# injection is the exit-1 NonFiniteAudio traceback. Must stay exit 1
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("site", sorted(SITES))
def test_without_prepares_conversion_the_same_nan_is_the_exit_1_traceback(tmp_path, site):
    p = _drive(tmp_path, site, "noconvert")
    assert p.returncode == exit_contract.FAIL, _why(p)
    assert "Traceback" in p.stderr and "NonFiniteAudio" in p.stderr, _why(p)
    assert SITES[site] in p.stderr, _why(p)
    assert "REFUSED" not in p.stderr and "REFUSED" not in p.stdout, _why(p)
    assert not (tmp_path / "o.json").exists()
