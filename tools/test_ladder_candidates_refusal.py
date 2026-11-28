#!/usr/bin/env python3
"""#1881: where an estimator's own refusal goes, in the two entry points that
reach `model/ladder_candidates.py`'s four `.require()` sites (red-first on
origin/main @ 560a1580, 2026-11-28).

`audio_measure.InsufficientEvidence` subclasses AssertionError, not
`refusal.Refused`, so a dispatch handler written `except Refused` cannot see an
estimator refusing. The #1782 census listed this module as a candidate -- no
handler it defines stops that class -- and a census row is a candidate, not a
defect. This file is the candidate RUN, by the #1791 method: the fault put
below the tool and asserted at the seam, the tool then run through its real
`__main__` (`runpy`, so its argv is a direct invocation's).

THE SITES, AND WHAT THE RUNS FOUND: one path escaped, and it is repaired.

    :540  stepped_tone_db         am.tone_amplitude(...).require(...)
    :553  reference_response_db   -> :540
    :663  peak_probe              -> :540
    :954  Candidate.tone_gain_db  am.tone_amplitude(...).require(...)

  ESCAPED  assert_apparatus (4) -> reference_floor_db:726 ->
           reference_response_db:553 -> stepped_tone_db:540, under
           compare_ladder_candidates.main(), whose dispatch caught only
           `Refused`. A rung-4 reference rendering silence left as exit 1 with
           `audio_measure.InsufficientEvidence: reference 1000 Hz 249.0 Hz:
           insufficient evidence -- silent`; one rendering NaN, as exit 1 with
           `audio_measure.NonFiniteAudio`. Nothing stops either on the way:
           `assert_apparatus` checks (1), (2), (3) and (5) never call the
           reference model, so a dead reference reaches the estimator every
           time. `assert_apparatus`'s own docstring promises "raising Refused
           rather than returning a score", and step (4) was the one
           precondition whose failure could not keep that promise. `main` now
           names the class beside `Refused`, as its four stage handlers
           (:154, :204, :239, :465) already did.
  stopped  a REAL input refuses at :540 through :663 on the DEFAULT grid, and
           is recorded today: at cut = 100 Hz the passband probe is
           max(0.1 cut, 2 SR/N) = 10 Hz, which snaps to k = 3 -- three periods
           of the 16384-sample window against tone_amplitude's min_periods of
           8. The committed report carries both rows (`100/0.5`, `100/0.9`).
           stage_linear's handler at :154 records them.
  stopped  the null core, the start-red control of every run, refuses at :540
           "silent" through :663 and through stage_bass:237; :154 and :239
           record it.
  stopped  a dead CANDIDATE does not reach the stages from compare's main():
           assert_apparatus (2) refuses its bit-exactness first, exit 2. With
           every ladder dead -- both sides of that check -- the stages run and
           the handlers above record every point: exit 0, no traceback.
  stopped  model/probe_rungs_2_4_gaps.py reaches the sites only through
           cc.stage_linear; its own `except lc.Refused` (:73) wraps peak_of,
           which raises only Refused.
  absent   :954 has no caller: 16 `.tone_gain_db(` call sites in the tree,
           none in a file that imports ladder_candidates (reference_compare,
           sound_report and run_case drive reference_rigs.OurLadder, another
           class). Nothing to run.

On the entry point's own inputs the estimator never refuses: 24 calls across
the quick and full `assert_apparatus` grids, all ok -- at least 17 periods
against 8, at least 10,303x over the 1e-9 silence floor, every sample finite.
The escape is latent until the reference model breaks, which is #1751's status
and the reason it is repaired rather than dismissed.

NOT CHANGED, AND WHY. stage_movement's `except Refused` (:327) wraps
render_modulated, which makes no estimator call. Under pytest an
InsufficientEvidence fails the test, the conservative outcome, so
model/test_ladder_candidates.py's direct calls to :553 need no handler.

WHAT THE HANDLER MUST NOT DO. InsufficientEvidence IS an AssertionError, so
`except AssertionError` would pass the two refusal rows and relabel an
ordinary assertion as a refusal. The control below puts a plain
AssertionError at the same seam: exit 1, with its traceback.

WRONG-THEN-RIGHT (6, every one in the apparatus, each caught because the run
disagreed with what reading predicted):

  1. The healthy row was first invoked `--only null` and died in _verdict
     with `KeyError: 'shipped'`: `--only` without `shipped` is not a valid
     run (filed separately). The row is `--only shipped`.
  2. The first candidate seam replaced `Candidate.render`, which also owns
     the cost bookkeeping: probe_rungs_2_4_gaps probe 2 died on
     `last_cost = None`.
  3. That partial seam silenced one side of assert_apparatus (3), so compare
     refused "does not reproduce reference_rigs.OurLadder" -- a real gate,
     reached only because the seam was half a fault.
  4. The second seam replaced the cores' `process`, tripping cost()'s own
     precondition ("cost() before anything was rendered"). The seam here
     WRAPS `process` and zeroes its output: the model runs, the audio is dead.
  5. A `fixed.LadderFx.process(x, None, ...)` probe without coefficients is
     not a render; it is a TypeError in `coefficients`.
  6. The null-core row first read `.reason` off the caught exception. That
     attribute is the Estimate's; `require()` raises a bare message. It was
     red on the pre-fix tree for that wrong reason.
"""
from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "model"))
import exit_contract                                     # noqa: E402

#: reported as that when a seam has not taken: never a verdict about the tool
SEAM_FAILED = "SEAM FAILED"
COMPARE = "tools/compare_ladder_candidates.py"
GAPS = "model/probe_rungs_2_4_gaps.py"
CHILD_PATHS = "sys.path[:0] = %r\n" % [str(ROOT / p) for p in ("model", "tools")]

#: the rung-4 reference renders digital silence. Asserted audible before the
#: fault and silent after it.
REF_SILENT = (
    "import numpy as np, ladder_candidates as lc\n"
    "_p = 1e-4 * np.sin(2 * np.pi * 250.0 * np.arange(4800) / 48000.0)\n"
    "assert np.any(lc.reference_ode(_p, 1000.0, 0.9)), "
    f"{SEAM_FAILED + ' the reference was silent before the fault'!r}\n"
    "lc.reference_ode = lambda x, *a, **kw: np.zeros(len(x))\n"
    "assert not np.any(lc.reference_ode(_p, 1000.0, 0.9)), "
    f"{SEAM_FAILED + ' the reference still renders'!r}\n")

#: the reference renders NaN: the other route to the same class (#134)
REF_NAN = (
    "import numpy as np, ladder_candidates as lc\n"
    "_p = 1e-4 * np.sin(2 * np.pi * 250.0 * np.arange(4800) / 48000.0)\n"
    "assert np.all(np.isfinite(lc.reference_ode(_p, 1000.0, 0.9))), "
    f"{SEAM_FAILED + ' the reference was not finite before the fault'!r}\n"
    "lc.reference_ode = lambda x, *a, **kw: np.full(len(x), np.nan)\n"
    "assert np.all(np.isnan(lc.reference_ode(_p, 1000.0, 0.9))), "
    f"{SEAM_FAILED + ' the reference still renders numbers'!r}\n")

#: a plain AssertionError -- not an InsufficientEvidence -- at the same seam
REF_FAULT = "unrelated reference fault"
ASSERTION_FAULT = (
    "import numpy as np, ladder_candidates as lc\n"
    f"FAULT = {REF_FAULT!r}\n"
    "def _broken(x, *a, **kw):\n"
    "    raise AssertionError(FAULT)\n"
    "lc.reference_ode = _broken\n"
    "try:\n"
    "    lc.reference_ode(np.ones(4800), 1000.0, 0.9)\n"
    "except AssertionError as e:\n"
    "    assert type(e) is AssertionError and str(e) == FAULT, "
    f"{SEAM_FAILED + ' the wrong error came out of the seam'!r}\n"
    "else:\n"
    f"    raise AssertionError({SEAM_FAILED + ' the reference still renders'!r})\n")

#: the cores run but their output is digital silence: `process` is WRAPPED,
#: never replaced, so the cost counters `Candidate.render` reads have run
CORES = "lc.ShippedCore, lc.NewtonCore, lc.ExplicitDFCore"
WRAP = (
    "def _silence(cls):\n"
    "    _real = cls.process\n"
    "    def _process(self, x, *a, **kw):\n"
    "        _real(self, x, *a, **kw)\n"
    "        return np.zeros(len(x), dtype=np.int64)\n"
    "    cls.process = _process\n")
CAND_SILENT = (
    "import numpy as np, ladder_candidates as lc\n"
    "_c = lc.Candidate('shipped')\n"
    "_p = np.ones(4800)\n"
    "assert np.any(_c.render(_p, 1000.0, 0.9)), "
    f"{SEAM_FAILED + ' the candidate was silent before the fault'!r}\n"
    + WRAP +
    f"for _k in ({CORES}):\n    _silence(_k)\n"
    "assert not np.any(_c.render(_p, 1000.0, 0.9)), "
    f"{SEAM_FAILED + ' the candidate still renders'!r}\n")

#: the same plus the shipped fixed-point ladder on BOTH sides of the
#: bit-exactness check, so assert_apparatus (2) and (3) pass on silence
TOTAL_SILENT = (
    "import numpy as np, fixed, voice_fx as vf, reference_rigs as rr, ladder_candidates as lc\n"
    "_c = lc.Candidate('shipped')\n"
    "_p = np.ones(4800)\n"
    "_ours = lambda: rr.OurLadder('ours')._render(_p, 1000.0, 0.9)\n"
    "assert np.any(_ours()) and np.any(_c.render(_p, 1000.0, 0.9)), "
    f"{SEAM_FAILED + ' a ladder was silent before the fault'!r}\n"
    + WRAP +
    f"for _k in (fixed.LadderFx, rr._Variant, {CORES}):\n    _silence(_k)\n"
    "assert not np.any(_ours()) and not np.any(_c.render(_p, 1000.0, 0.9)), "
    f"{SEAM_FAILED + ' a ladder still renders'!r}\n")


def _run(script: str, seam: str, *argv: str) -> subprocess.CompletedProcess:
    """`python -c` with `seam` installed first, then the tool through its real
    `__main__`. `run_path` puts the script's path at argv[0], which is what a
    direct invocation leaves there."""
    src = (f"import sys, runpy\n{CHILD_PATHS}\n{seam}"
           f"runpy.run_path({str(ROOT / script)!r}, run_name='__main__')\n")
    p = subprocess.run([sys.executable, "-c", src, *argv], capture_output=True,
                       text=True, timeout=600, cwd=str(ROOT))
    assert SEAM_FAILED not in p.stderr, p.stderr[-600:]
    return p


def _start_red(stdout: str) -> str:
    assert "START RED" in stdout, stdout[-600:]
    return stdout.split("START RED", 1)[1].split("INJECTED DEFECTS", 1)[0]


# ---------------------------------------------------------------------------
# the class relations the fix rests on
# ---------------------------------------------------------------------------
def test_the_refusal_class_is_an_assertion_error_and_not_a_refused():
    import audio_measure as am
    import compare_ladder_candidates as cc
    import refusal
    assert issubclass(am.InsufficientEvidence, AssertionError)
    assert issubclass(am.NonFiniteAudio, am.InsufficientEvidence)
    assert not issubclass(am.InsufficientEvidence, cc.Refused)
    assert not issubclass(am.InsufficientEvidence, refusal.Refused)


# ---------------------------------------------------------------------------
# ESCAPED before the fix: a dead rung-4 reference must REFUSE, not traceback
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("seam, words", [
    (REF_SILENT, "REFUSED: reference 1000 Hz 249.0 Hz: insufficient evidence -- silent"),
    (REF_NAN, "REFUSED: the signal holds 16384 non-finite samples"),
], ids=["silent", "nan"])
def test_a_dead_rung4_reference_refuses_through_compares_main(seam, words):
    p = _run(COMPARE, seam, "--quick", "--only", "shipped")
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stderr[-600:])
    assert words in p.stderr, p.stderr[-600:]
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout, p.stderr[-600:]
    assert "Rungs 2-4" not in p.stdout, "a refused run must not start the report"


def test_a_plain_assertion_error_at_the_same_seam_is_still_an_execution_error():
    p = _run(COMPARE, ASSERTION_FAULT, "--quick", "--only", "shipped")
    assert p.returncode == exit_contract.FAIL, (p.returncode, p.stderr[-600:])
    assert "Traceback" in p.stderr and REF_FAULT in p.stderr, p.stderr[-600:]
    assert "REFUSED" not in p.stderr and "REFUSED" not in p.stdout, p.stderr[-600:]


# ---------------------------------------------------------------------------
# stopped today by the handlers that already name the class: :154 and :239
# ---------------------------------------------------------------------------
def test_a_real_input_refuses_in_the_estimator_and_stage_linear_records_it():
    """cut = 100 Hz is on the default grid: the passband probe snaps to 3
    periods and tone_amplitude refuses. What :154 catches is the estimator's
    class, so narrowing it to Refused turns this red."""
    import audio_measure as am
    import ladder_candidates as lc
    import compare_ladder_candidates as cc
    cand = lc.Candidate("shipped")
    with pytest.raises(am.InsufficientEvidence) as info:
        lc.peak_probe(lambda x: cand.render(x * lc.FS_Q15, 100.0, 0.5), 100.0, 0.5)
    assert "too few periods" in str(info.value)
    assert not isinstance(info.value, cc.Refused)
    row = cc.stage_linear(["shipped"], (100.0,), (0.5, 0.9))["shipped"]
    assert row["refused"] == len(row["points"]) == 2, row
    for point in row["points"].values():
        assert "insufficient evidence -- too few periods" in point["refused"], point


def test_the_null_core_refuses_in_the_estimator_and_stage_bass_records_it():
    import audio_measure as am
    import ladder_candidates as lc
    import compare_ladder_candidates as cc
    freqs = lc.snap_freqs(cc.BASS_FREQS)
    with pytest.raises(am.InsufficientEvidence) as info:
        lc.stepped_tone_db(lambda x: np.zeros_like(x), freqs, lc.PROBE_AMP)
    assert "insufficient evidence -- silent" in str(info.value), str(info.value)
    row = cc.stage_bass(["null"])["null"]
    assert set(row) == {"refused"} and "insufficient evidence -- silent" in row["refused"], row


def test_a_dead_candidate_is_refused_by_the_bit_exactness_gate_before_any_stage():
    p = _run(COMPARE, CAND_SILENT, "--quick", "--only", "shipped")
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stderr[-600:])
    assert "REFUSED: ShippedCore is not bit-exact against fixed.LadderFx" in p.stderr, p.stderr[-600:]
    assert "Traceback" not in p.stderr and "Rungs 2-4" not in p.stdout, p.stderr[-600:]


def test_with_every_ladder_dead_the_stages_record_every_refusal_and_complete():
    p = _run(COMPARE, TOTAL_SILENT, "--quick", "--only", "shipped")
    assert p.returncode == exit_contract.PASS, (p.returncode, p.stderr[-600:])
    assert "Traceback" not in p.stderr, p.stderr[-600:]
    start_red = _start_red(p.stdout)
    assert re.search(r"linear\s+REFUSED", start_red) and re.search(r"bass\s+REFUSED", start_red), start_red


def test_probe_rungs_reaches_the_sites_only_through_stage_linear():
    p = _run(GAPS, TOTAL_SILENT, "--only", "4")
    assert p.returncode == exit_contract.PASS, (p.returncode, p.stderr[-600:])
    assert "Traceback" not in p.stderr, p.stderr[-600:]
    report = json.loads(p.stdout[p.stdout.index("{"):])
    assert report["refused_points"] == report["total_points"] == 6, report
    for point in report["four_poles"]["points"].values():
        assert "insufficient evidence -- silent" in point["refused"], point


# ---------------------------------------------------------------------------
# the successful path: the widened handler changed none of it
# ---------------------------------------------------------------------------
def test_a_healthy_run_still_completes_with_the_null_core_refused():
    p = _run(COMPARE, "", "--quick", "--only", "shipped")
    assert p.returncode == exit_contract.PASS, (p.returncode, p.stderr[-600:])
    assert "Traceback" not in p.stderr, p.stderr[-600:]
    assert "Rungs 2-4: candidate ladders" in p.stdout, p.stdout[:300]
    start_red = _start_red(p.stdout)
    assert re.search(r"linear\s+REFUSED", start_red) and re.search(r"bass\s+REFUSED", start_red), start_red
