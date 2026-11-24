#!/usr/bin/env python3
"""Known-answer exit-status controls: a cross-module refusal from
`run_case.prepare` must reach the repeatability harness's exit contract as
REFUSED, never as an uncaught exit-1 traceback (#1183, the `run_case.*`
raiser family).

THE DEFECT, AS MEASURED ON main @ f912072f (2026-11-24, red-first run of
every control in this file): `tools/measure_repeatability.py` guards its
whole `main()` and `loops_main()` with `except Refused` -- its OWN local
class -- but every corpus record passes through `rc.prepare()`, which
raises `run_case.Refused`, i.e. `preparation_contract.Refused`: a SIBLING
class the local handler cannot catch. A bass-drum WAV that begins at or
above 2 % of its own peak (cut into the strike by the vendor's editor)
therefore escaped as a traceback and the process exited 1 -- below the
harness's own contract, where `REFUSED = 2` is a declared outcome ("a
refusal is the outcome", its own audit path). The repair widens the two
CLI boundary handlers to `refusal.Refused`; the per-record `except
rc.Refused` inside `take_to_take` was already exact and stays; the
double-time semantic check (`except Refused:` around `slice_strikes`)
stays LOCAL on purpose -- it asserts a refusal this module itself raises.

THE KNOWN ANSWERS, independent of anything this repository models: a
44.1 kHz mono recording whose first sample is already above 2 % of its
peak must be refused "begins at or above 2 % of its own peak ... cut into
the strike" by `run_case.prepare` before any spread is reported. The
corpus root is fixed repo state (`refaudio/cache`, not an env var), so the
control runs the tool against a SANDBOX COPY of tools/ + model/ with a
synthetic 36-file grid under the copy's refaudio/cache -- the real tree is
never mutated, and parallel tests cannot collide on it. Verified by hand
against the pre-fix tree before the fix landed: exit 1 with the
`preparation_contract.Refused` traceback.

The tool's own corpus reader needs `soundfile`, which the CI image does
not install; the control skips where the tool itself could not run (same
precondition, refused rather than reported -- docs/failure-modes.md).

RULE-8 DEFEATER, STATED RATHER THAN CARRIED (docs/verification-rules.md): the
four sibling controls for this PR each carry an unreadable-corpus case pinning
that a scipy/soundfile ValueError still exits 1. This file has none: the
handlers repaired here widen to `refusal.Refused` only, so a ValueError cannot
be caught by them by construction (no `except Exception` exists in the diff),
and the module-level `importorskip("soundfile")` means a new control would not
run in the CI image either. The cut-into-strike control below is the only
known-answer case this file owns; an execution-error control is not shipped
because it could not be run (or red-checked) on the hosts that build this PR.
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys

import numpy as np
import pytest

sf = pytest.importorskip("soundfile")

ROOT = pathlib.Path(__file__).resolve().parents[1]
SR = 44100
LEAD_S = 0.05          # pre-onset silence the well-formed grid files carry
BODY_S = 0.50
#: (decay letter, tone index) of the file self_test reads FIRST (the
#: editing-noise section's `g[("C", 3)]`), derived from the tool, not retyped.
CUT_CELL = ("C", 3)


def _record(cut_into_strike: bool) -> np.ndarray:
    """A synthetic bass drum: 50 ms of true digital silence, then a decaying
    50 Hz sine. `cut_into_strike` removes the lead, so the record begins at
    ~83 % of its own peak -- `run_case.prepare`'s exact refusal, with the
    file still non-silent and correctly scaled for the tool's own `load()`."""
    n = int((LEAD_S + BODY_S) * SR) if not cut_into_strike else int(BODY_S * SR)
    t = np.arange(n) / SR
    body_t = t - (0.0 if cut_into_strike else LEAD_S)
    x = 0.5 * np.sin(2 * np.pi * 50.0 * body_t) * np.exp(-body_t / 0.12)
    return x


def _sandbox(tmp_path: pathlib.Path) -> pathlib.Path:
    """A private copy of the module tree, so the fixture corpus the tool
    finds is under the copy's own ROOT (its REPO is derived from __file__,
    never from an env var)."""
    box = tmp_path / "sandbox"
    shutil.copytree(ROOT / "tools", box / "tools",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(ROOT / "model", box / "model",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    # run_case puts ROOT/audition on sys.path for `import dsp`
    # (model/voice_fx.py:85); without the copy the sandbox dies on an import
    # error before any fixture is reached.
    shutil.copytree(ROOT / "audition", box / "audition",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return box


def _grid(box: pathlib.Path) -> None:
    """The 36-file accent-A digital grid self_test's editing-noise section
    reads, with `CUT_CELL` cut into the strike and every other file
    well-formed (the happy-path grid the tool's own `load()` accepts)."""
    folder = (box / "refaudio" / "cache" / "808-from-mars" / "808 From Mars" / "WAV" /
              "01. Individual Hits" / "01. Bass Drum" / "Clean" / "Digital" / "A")
    folder.mkdir(parents=True)
    for decay in "ABCDEF":
        for tone in range(1, 7):
            name = f"BD A 808 Decay {decay} {tone:02d}.wav"
            sf.write(str(folder / name), _record((decay, tone) == CUT_CELL), SR,
                     subtype="FLOAT")


def _self_test(box: pathlib.Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(box / "tools" / "measure_repeatability.py"), "--self-test"],
        capture_output=True, text=True, timeout=600, cwd=str(box))


# ---------------------------------------------------------------------------
# the class relations the whole fix rests on
# ---------------------------------------------------------------------------
def test_raiser_is_a_refusal_sibling_of_the_boundary_guard():
    import refusal
    import measure_repeatability as mr
    import run_case as rc

    assert mr.Refused is not rc.Refused
    assert not issubclass(rc.Refused, mr.Refused)
    for klass in (mr.Refused, rc.Refused):
        assert issubclass(klass, refusal.Refused), klass


# ---------------------------------------------------------------------------
# the cut-into-strike record must REFUSE, not traceback
# ---------------------------------------------------------------------------
def test_cut_into_strike_reference_exits_refused_2(tmp_path):
    box = _sandbox(tmp_path)
    _grid(box)
    p = _self_test(box)
    assert p.returncode == 2, (p.returncode, p.stdout[-300:], p.stderr[-400:])
    assert "REFUSED" in p.stdout, p.stdout[-400:]
    assert "cut into the strike" in p.stdout, p.stdout[-400:]
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout, p.stderr[-400:]
    assert "ESTIMATOR, before the machine" in p.stdout, (
        "the synthetic estimator sections run before the corpus is touched; "
        "a control that refused earlier would not be reaching this raising site")
