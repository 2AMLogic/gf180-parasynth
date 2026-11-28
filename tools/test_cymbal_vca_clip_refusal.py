#!/usr/bin/env python3
"""Known-answer exit-status controls for tools/cymbal_vca_clip.py (#1991).

`--asym nan` is accepted by argparse, which types it `float` and checks no
range. `clip()` then divides by `1 - tanh(nan)^2` and every sample comes out
NaN. Two guards on the way compare against that NaN and are DEFEATED rather
than tripped, because NaN compares False (#134's shape). `run_case.prepare` is
the first check that stops it, as a `preparation_contract.Refused` -- and
`main()` had no handler, so the process died with a traceback and exit 1: a
refusal rendered as FAIL, the inversion `tools/exit_contract.py` exists to
prevent.

THE DEFECT, AS MEASURED ON main @ dd0449e0 (2026-11-28). This file's final
47 controls, run against that tool unchanged: 37 red, 10 green. The ten green
are the controls for behaviour the fix must not touch -- the clean run,
`--asym 0.8`, `--asym-sweep`, the usage error, the declared-status check, the
two implementation-error controls, the child-interpreter AssertionError and
the two decimal controls -- and the 37 red are the defect: six inputs dying
with exit 1 and the traceback below, `-inf` with a usage line, the guards, the
census, `--asym 19`'s crash, and the controls for API that did not exist yet.

    .venv/bin/python tools/cymbal_vca_clip.py --asym nan
    (the control report, the corrections, then exit 1)
      File "tools/cymbal_vca_clip.py", line 1161, in main
        rows = sweep(asym=asym, kind=a.source)
      File "tools/run_case.py", line 1481, in prepare
        raise Refused(str(why)) from why
    preparation_contract.Refused: cy-vca pre_vca -40 dB staircase holds 172800
    non-finite samples of 175200 (172800 NaN, 0 Inf; first at index 2400) ...

Same for `--asym inf`, and for the FINITE `--asym 20`: `tanh(20)` is exactly
1.0 in float64, so the same normalisation is 0 and the clipper returns 0/0.

WRONG IN THE ISSUE, as run: `--asym -inf` was not that traceback. argparse
reads a token that starts with `-` and is not a negative number as an option
flag, so it never reached the tool: exit 2, a usage error on stderr, stdout
EMPTY. Exit 2 is also this tool's REFUSED, so a caller that saw only the
status read a misparse as a refusal that names no reason. Every exit
assertion here pins the `REFUSED:` line beside the status for that reason.

THE KNOWN ANSWERS, independent of anything this repository models:

  * tanh is bounded by 1, and float64 has no number between 1 - 1.1e-16 and 1,
    so `tanh(b)` IS 1.0 for every large enough finite b (from |b| = 19.07 with
    glibc's): `1 - tanh(b)^2` is exactly 0 there and a clipper normalised by
    it returns 0/0.
  * `nan`, `inf` and `-inf` are not biases, and arithmetic on them is not audio.
  * a tool that measured nothing owes `exit_contract.REFUSED` (2) with its
    reason; an implementation error is not a precondition and owes exit 1.

WHAT THE TOOL OWES, and what the controls below assert:

  --asym outside   exit 2, and the `REFUSED:` reason is the ONLY line on
  the domain       stdout: refused up front, before the control report and
                   before any render.
  --asym inside    exit 0 and the sweep at that asymmetry: 19, the documented
                   0.5, and every `ASYM_SWEEP` value under `--asym-sweep`
  the verdict      when the LINEAR row is the closest one, names "the linear
                   chain" instead of formatting its missing drive (`--asym 19`)
  its own refusal  `targets()` refusing (a quote that disagrees with the
                   committed artifact) exits 2 through `main([])`
  run_case's       a `run_case.Refused` reaching `main()` exits 2
  other errors     a plain AssertionError at the same place still exits 1
  pre-fix replay   the source at `PRE_FIX`, read out of this repository's own
                   history and run under the SAME seam, still exits 1 with the
                   traceback: the injected-bug control
  the two guards   a NaN THD is a refused drive and a NaN peak is a refused
                   render, each beside the input that defeats `x > 0`
  the census       `tools/refusal_sibling_scan.py` reads both `rc.prepare`
                   sites as covered

WHAT THE DOMAIN GUARD IS BLIND TO, measured rather than argued: it is about
FINITENESS, not accuracy. Near the edge the clipper subtracts two numbers
that are both within 1e-16 of 1, so it is finite and wrong. Against its own
formula in 60-digit decimal arithmetic it is exact to 7e-16 at every
documented asymmetry, wrong by 1e-5 of full scale at 14, by 2e-4 at 15 and
by 43 % at 19 -- which this issue's edge case requires to keep rendering.
The last control pins those numbers so the blind spot is a stated one
(`docs/verification-rules.md` rule 8), not a discovered one.

WRONG IN THE ISSUE A SECOND TIME, as run, and repaired here because the edge
case cannot pass without it: `--asym 19` did NOT "return normally". It printed
its whole report and then died at `verdict()`, exit 1: at that bias the
clipper adds so little that the LINEAR row is the one closest to the 808's
box, and the "closest is ..." clause formatted that row's missing drive as a
float (`TypeError`). `verdict()` now names it.

WRONG-THEN-RIGHT OF THIS FILE (1, in the apparatus): the `--asym=-inf` control
was first written `("--asym=-inf")` -- a bracketed STRING, not a tuple -- so
the control passed its value to the tool one character at a time and was red
on the fixed tree for a usage error of its own making. Caught by reading the
failing assertion rather than the count.
"""
from __future__ import annotations

import json
import math
import pathlib
import re
import subprocess
import sys
from decimal import Decimal, getcontext

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "model"))

import cymbal_vca_clip as v      # noqa: E402
import run_case as rc            # noqa: E402
import exit_contract             # noqa: E402
import refusal                   # noqa: E402

TOOL = ROOT / "tools" / "cymbal_vca_clip.py"
TOOL_REL = "tools/cymbal_vca_clip.py"
SCAN = ROOT / "tools" / "refusal_sibling_scan.py"
PRE_FIX = "dd0449e0"

REFUSED, FAIL, PASS = exit_contract.REFUSED, exit_contract.FAIL, exit_contract.PASS
NAN = float("nan")
TIMEOUT = 900

OUTSIDE = [pytest.param(argv, id=" ".join(argv)) for argv in
           [("--asym", "nan"), ("--asym", "inf"), ("--asym", "-inf"), ("--asym=-inf",),
            ("--asym", "20"), ("--asym", "-20"), ("--asym", "1e300"),
            ("--asym-sweep", "--asym", "nan")]]


def _cli(*argv, timeout=TIMEOUT) -> subprocess.CompletedProcess:
    """The tool as a batch runner launches it: the working tree's file, a
    child interpreter."""
    return subprocess.run([sys.executable, str(TOOL), *argv], capture_output=True,
                          text=True, timeout=timeout, check=False, cwd=str(ROOT))


_HEAD = (f"import sys\n"
         f"sys.path.insert(0, {str(ROOT / 'model')!r})\n"
         f"sys.path.insert(0, {str(ROOT / 'tools')!r})\n")
_AS_MODULE = (f"import types\n"
              f"v = types.ModuleType('cymbal_vca_clip')\n"
              f"v.__file__ = {str(TOOL)!r}\n"
              f"exec(compile(sys.stdin.read(), {str(TOOL)!r}, 'exec'), v.__dict__)\n")


def _main(argv, *, patch="", source=None, timeout=TIMEOUT) -> subprocess.CompletedProcess:
    """`main(argv)` in a child interpreter: the working tree's module, or
    `source` loaded as that module under the tool's own path so its ROOT
    resolves. `patch` runs in the child between the load and the call."""
    load = _AS_MODULE if source is not None else "import cymbal_vca_clip as v\n"
    code = _HEAD + load + patch + f"raise SystemExit(v.main({list(argv)!r}))\n"
    return subprocess.run([sys.executable, "-c", code], input=source or "",
                          capture_output=True, text=True, timeout=timeout, check=False,
                          cwd=str(ROOT))


def _pre_fix_source() -> str:
    """The tool before the fix, out of this repository's own history. A
    `git show` that cannot run is NO VERDICT, which is red -- never a skip
    that reads like a control that ran."""
    r = subprocess.run(["git", "-C", str(ROOT), "show", f"{PRE_FIX}:{TOOL_REL}"],
                       capture_output=True, text=True, timeout=60, check=False)
    assert r.returncode == 0, (
        f"NO VERDICT: git show {PRE_FIX}:{TOOL_REL} failed in this clone "
        f"({r.stderr.strip()[:160]}); the pre-fix tool cannot be read back")
    assert "refusal.Refused" not in r.stdout and "check_asym" not in r.stdout, (
        f"{PRE_FIX}:{TOOL_REL} carries a base handler or the domain check: it is not "
        "the pre-fix tool this control exists to replay")
    return r.stdout


def _refusal(p: subprocess.CompletedProcess) -> str:
    """The tool's own refusal: its last line, `REFUSED: <reason>`, at column 0
    -- where the sweep's own INDENTED `REFUSED: no single (position, drive)`
    verdict never appears."""
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout, p.stderr[-400:]
    lines = p.stdout.splitlines()
    assert lines and lines[-1].startswith("REFUSED: "), (p.returncode, p.stdout[-400:])
    return lines[-1]


def _own_refusals(p: subprocess.CompletedProcess) -> list[str]:
    """Every line of stdout that starts at column 0 with `REFUSED`: where the
    tool's own refusal is printed, and where the sweep's indented verdict is
    not."""
    return [l for l in p.stdout.splitlines() if l.startswith("REFUSED")]


def _sweep_at(tmp_path, *argv) -> tuple[subprocess.CompletedProcess, dict | None]:
    """The process and the result file of one full run of the tool. The file
    is None when the run never got as far as writing it, so the exit status is
    the first thing a caller reads, not a FileNotFoundError."""
    out = tmp_path / "o.json"
    p = _cli(*argv, "--out", str(out))
    return p, json.loads(out.read_text()) if out.is_file() else None


@pytest.mark.parametrize("argv", OUTSIDE)
def test_an_asym_outside_the_clippers_domain_exits_refused_2_before_anything_is_measured(argv):
    """Wrong-then-right, as a control: on main `nan`, `inf` and `20` printed a
    report and died with exit 1, and `-inf` exited 2 with nothing on stdout.
    Refused up front means the reason is the whole of stdout."""
    p = _cli(*argv)
    assert p.returncode == REFUSED, (p.returncode, p.stderr[-400:])
    assert p.stderr == "", p.stderr[-400:]
    assert p.stdout.splitlines() == [_refusal(p)], p.stdout[-400:]
    value = repr(float(argv[-1].split("=")[-1]))
    assert _refusal(p).startswith(f"REFUSED: --asym {value} is outside"), _refusal(p)
    assert "1 - tanh(b)^2" in _refusal(p), _refusal(p)


def test_a_usage_error_keeps_its_own_shape_and_is_not_read_as_this_refusal():
    """argparse's usage error also exits 2. It is told apart by what it
    prints: a usage line on stderr, no `REFUSED:` on stdout."""
    p = _cli("--asym", "abc")
    assert p.returncode == REFUSED and "usage:" in p.stderr, (p.returncode, p.stderr[-400:])
    assert p.stdout == "" and "Traceback" not in p.stderr, p.stderr[-400:]


def test_the_clean_run_still_exits_0_and_writes_its_result(tmp_path):
    p, res = _sweep_at(tmp_path)
    assert p.returncode == PASS, (p.returncode, p.stderr[-400:])
    assert res is not None, p.stdout[-400:]
    assert "Traceback" not in p.stderr
    assert _own_refusals(p) == [], p.stdout[-400:]
    verdict = [l for l in p.stdout.splitlines() if l.startswith("  REFUSED: no single (position")]
    assert verdict and verdict[0].startswith("  REFUSED: no single (position, drive)"), verdict
    assert res["asym"] == v.ASYM == 0.5 and list(res["sweeps"]) == ["0.5"]
    assert res["commit"] and res["controls_pass"] is True


@pytest.mark.parametrize("token, key", [("19", "19"), ("0.8", "0.8")])
def test_an_asym_inside_the_domain_still_renders(tmp_path, token, key):
    """The issue's edge case: 19 is the last integer inside. Its clipper is
    finite and no longer accurate -- the blind spot measured by
    `test_the_domain_guard_is_about_finiteness_and_its_blind_spot_is_measured`
    -- which is stated, and is not this guard's question. At 19 the verdict's
    closest row is the linear chain; at 0.8 it is a clipped one."""
    p, res = _sweep_at(tmp_path, "--asym", token)
    assert p.returncode == PASS, (p.returncode, p.stderr[-400:])
    assert res is not None, p.stdout[-400:]
    assert _own_refusals(p) == [] and "Traceback" not in p.stderr
    assert res["asym"] == float(token) and list(res["sweeps"]) == [key]
    closest = [l for l in p.stdout.splitlines() if "the closest is" in l]
    assert len(closest) == 1, closest
    assert ("the closest is the linear chain" in closest[0]) == (token == "19"), closest


def test_the_verdict_names_the_linear_row_as_closest_instead_of_crashing():
    """Wrong-then-right, as a control, found by the edge case above: on main
    `--asym 19` did NOT return normally. At that bias the clipper adds so
    little that the LINEAR row is the one closest to the 808's box, and
    `verdict()` formatted that row's missing drive as a float: a TypeError,
    exit 1, after the whole report had printed."""
    linear = {"position": "none", "drive_db": None, "asym": 19.0, "source": "staircase",
              "thd_db": None, "refused": {}, f"M_re_{v.REF}": -5.07, f"Mn_re_{v.REF}": -12.0,
              "rho_M_-10": 0.9917, "rho_Mn_-10": 1.0966}
    far = dict(linear, position="post_vca", drive_db=0.0, thd_db=-20.0,
               **{f"M_re_{v.REF}": 0.5, "rho_M_-10": 4.0})
    out = v.verdict([linear, far])
    assert out["positions_matching"] == [] and out["closest_row"] is linear
    assert "the closest is the linear chain, rho_M 0.9917, M re Ln -5.07 dB" in out["refused"]
    assert "the closest is post_vca at +0 dB" in v.verdict([far])["refused"]


def test_the_asym_sweep_is_untouched(tmp_path):
    p, res = _sweep_at(tmp_path, "--asym-sweep")
    assert p.returncode == PASS, (p.returncode, p.stderr[-400:])
    assert res is not None, p.stdout[-400:]
    assert list(res["sweeps"]) == [f"{a:g}" for a in v.ASYM_SWEEP]
    assert _own_refusals(p) == [] and "Traceback" not in p.stderr


@pytest.mark.parametrize("argv", [pytest.param(a, id=a[-1].split("=")[-1]) for a in
                                  [("--asym", "nan"), ("--asym", "20"), ("--asym=-inf",)]])
def test_the_pre_fix_tool_still_escapes_under_the_same_seam_that_now_refuses(argv):
    """With the fix reverted, the seam the refusal controls run must still
    produce the defect, so they cannot be green without the fix."""
    fixed = _main(["--skip-controls", *argv])
    assert fixed.returncode == REFUSED and len(fixed.stdout.splitlines()) == 1, fixed.stdout[-400:]
    pre = _main(["--skip-controls", *argv], source=_pre_fix_source())
    assert pre.returncode == FAIL, (pre.returncode, pre.stderr[-400:])
    assert "Traceback" in pre.stderr, pre.stderr[-400:]
    assert "preparation_contract.Refused: cy-vca pre_vca" in pre.stderr
    assert _own_refusals(pre) == [] and "the ANALYSIS filter" in pre.stdout


def test_the_status_is_a_declared_no_evidence_one():
    assert REFUSED in exit_contract.NO_EVIDENCE and REFUSED != FAIL
    for klass in (v.Refused, rc.Refused):
        assert issubclass(klass, refusal.Refused), klass
    assert v.Refused is not rc.Refused and not issubclass(v.Refused, rc.Refused)


_DRIFTED_QUOTE = "v.T_RHO_808_MIN = 1.9999\n"
DRIFTED = ("REFUSED: low-tail.json's weakest 808 rho_M(-10) in the 2.0s window is 1.0476, "
           "this module quotes 1.9999")


def test_the_modules_own_refusal_exits_refused_2_through_main(monkeypatch, capsys):
    """`targets()` raises the module's OWN class, which the old `main()` did
    not catch either. `low-tail.json` is committed, so this runs rather than
    skips."""
    assert (ROOT / "docs/scorecard/cymbal-369/low-tail/low-tail.json").is_file()
    monkeypatch.setattr(v, "T_RHO_808_MIN", 1.9999)
    assert v.main(["--skip-controls"]) == REFUSED
    assert capsys.readouterr().out.splitlines()[-1] == DRIFTED


def test_the_modules_own_refusal_exits_2_in_a_child_and_did_not_before():
    """The issue's second reproduction, as stated: `main([])` in a child
    interpreter, controls and all; then the pre-fix tool under the same seam."""
    fixed = _main([], patch=_DRIFTED_QUOTE)
    assert fixed.returncode == REFUSED, (fixed.returncode, fixed.stderr[-400:])
    assert _own_refusals(fixed) == [DRIFTED] and "clean measurement" in fixed.stdout
    pre = _main([], patch=_DRIFTED_QUOTE, source=_pre_fix_source())
    assert pre.returncode == FAIL, (pre.returncode, pre.stderr[-400:])
    assert "Traceback" in pre.stderr and "cymbal_m_origin.Refused:" in pre.stderr
    assert "this module quotes 1.9999" in pre.stderr and _own_refusals(pre) == []


def test_a_run_case_refusal_reaching_main_exits_refused_2(monkeypatch, capsys):
    """The class that escaped in the issue. No CLI input reaches it once the
    domain is checked up front, so the refusal is planted where the issue put
    it: at `run_case.prepare`, the boundary `render` and `_combine` call."""
    def refuses(*a, **k):
        raise rc.Refused("forced at the boundary: the record is not audio")
    monkeypatch.setattr(rc, "prepare", refuses)
    assert v.main(["--skip-controls"]) == REFUSED
    assert capsys.readouterr().out.splitlines()[-1] == (
        "REFUSED: forced at the boundary: the record is not audio")


_NOT_A_REFUSAL = (
    "def _boom(*a, **k):\n"
    "    raise AssertionError('forced: an implementation error, not a precondition')\n"
    "v.targets = _boom\n")


@pytest.mark.parametrize("error", [AssertionError, RuntimeError])
def test_an_implementation_error_is_not_relabelled_a_refusal(monkeypatch, capsys, error):
    """The inverse defect (`tools/refusal.py`): the handler catches
    `refusal.Refused` and nothing wider."""
    def boom(*a, **k):
        raise error("forced: an implementation error, not a precondition")
    monkeypatch.setattr(v, "targets", boom)
    with pytest.raises(error, match="implementation error"):
        v.main(["--skip-controls"])
    assert "REFUSED" not in capsys.readouterr().out


def test_a_plain_assertion_error_still_exits_1_in_a_child():
    p = _main(["--skip-controls"], patch=_NOT_A_REFUSAL)
    assert p.returncode == FAIL, (p.returncode, p.stderr[-400:])
    assert "Traceback" in p.stderr and "AssertionError: forced: an implementation error" in p.stderr
    assert _own_refusals(p) == []


def test_the_silence_guards_refuse_a_peak_that_is_not_a_positive_finite_number():
    """`peak <= 0` and `rms <= 0` are False for NaN, so on main a NaN render
    reached `run_case.prepare` and left as ITS refusal; here it is the
    module's own, raised at the guard, and the reason says which of the two
    it was."""
    assert not (NAN <= 0.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        with pytest.raises(v.Refused, match=r"the render is silent or not finite \(peak nan\)"):
            v.render(position="post_vca", drive=0.0, asym=NAN, dur=0.1)
        with pytest.raises(v.Refused, match=r"\(peak nan\)"):
            v.render(position="post_vca", drive=0.0, asym=20.0, dur=0.1)
        with pytest.raises(v.Refused, match=r"the mix is silent or not finite \(peak nan\)"):
            v._combine({"low": np.full(8, NAN)}, {"low": 0.0})
        with pytest.raises(v.Refused, match=r"the clipper is silent or not finite \(rms nan\)"):
            v.clip(np.full(8, NAN), 0.0, v.ASYM)
    silent = {band: -math.inf for band in ("low", "decay", "short")}
    with pytest.raises(v.Refused, match=r"\(peak 0\.0\)"):
        v.render(position="none", levels=silent, dur=0.1)
    with pytest.raises(v.Refused, match=r"\(rms 0\.0\)"):
        v.clip(np.zeros(128), 0.0, v.ASYM)


def test_the_thd_guard_refuses_a_thd_that_is_not_a_finite_number(monkeypatch):
    """`thd_db > MAX_THD_DB` is False for NaN, so on main a NaN THD was a
    drive in good standing."""
    assert not (NAN > v.MAX_THD_DB)
    measured = v.thd_db(12.0)
    assert math.isfinite(measured) and measured <= v.MAX_THD_DB
    row = {"refused": {}, f"M_re_{v.REF}": -5.0, f"Mn_re_{v.REF}": -12.0,
           "rho_M_-10": 1.1, "rho_Mn_-10": 1.0}
    monkeypatch.setattr(v, "render", lambda **k: (None, v.SR))
    monkeypatch.setattr(v, "read", lambda *a, **k: dict(row))

    def sweep_at(thd):
        monkeypatch.setattr(v, "thd_db", lambda d, asym: thd)
        return v.sweep(positions=("post_vca",), drives=(0.0,))[0]

    assert "refused_drive" not in sweep_at(measured)
    assert "exceeds" in sweep_at(measured + 20.0)["refused_drive"]
    for thd in (NAN, float("inf")):
        got = sweep_at(thd)["refused_drive"]
        assert "exceeds" not in got and "not a finite number" in got, got


INSIDE = (0.0, 0.5, -0.5, 0.15, 0.3, 0.8, -0.8, 15.0, 19.0)
OUTSIDE_VALUES = (NAN, float("inf"), float("-inf"), 20.0, -20.0, 1e300)


@pytest.mark.parametrize("asym", INSIDE)
def test_a_bias_the_clipper_can_realise_is_returned_unchanged(asym):
    assert 1 - math.tanh(asym) ** 2 > 0
    assert v.check_asym(asym) == asym


@pytest.mark.parametrize("asym", OUTSIDE_VALUES,
                         ids=[repr(a) for a in OUTSIDE_VALUES])
def test_a_bias_the_clipper_cannot_realise_is_refused_naming_it(asym):
    """The known answer is typed here from `math.tanh` alone, so the control
    does not read the bound back off the module. `nan`, `-inf` and `-20` are
    what defeat a one-sided `asym >= 19.07`: every comparison with them is
    False, and the bound had the wrong sign for a negative bias."""
    assert not (1 - math.tanh(asym) ** 2 > 0)
    with pytest.raises(v.Refused, match="--asym " + re.escape(repr(asym)) + " is outside"):
        v.check_asym(asym)


def test_where_the_domain_ends_is_float64_s_tanh_and_not_a_number_quoted_here():
    assert v.bias_norm(0.0) == 1.0
    assert math.isnan(v.bias_norm(NAN))
    assert v.bias_norm(float("inf")) == 0.0 and v.bias_norm(20.0) == 0.0
    assert v.bias_norm(-20.0) == 0.0
    assert 0.0 < v.bias_norm(19.0) < 1e-15, \
        "19 is inside on glibc; the edge-case controls need that"


def test_the_check_and_the_clipper_read_one_normalisation(monkeypatch):
    """The guard and the guarded expression are one definition: a second copy
    of `1 - tanh(b)^2` is a second thing to go stale. With the sign of the
    normalisation flipped, the clipper's output flips with it and the check
    refuses the documented bias."""
    x = np.random.default_rng(0).standard_normal(64)
    whole = v.clip(x, 0.0, v.ASYM)
    real = v.bias_norm
    monkeypatch.setattr(v, "bias_norm", lambda b: -real(b))
    assert np.allclose(v.clip(x, 0.0, v.ASYM), -whole, rtol=1e-12, atol=0.0)
    with pytest.raises(v.Refused):
        v.check_asym(v.ASYM)


def _decimal_error(x, asym, drive=0.0) -> float:
    """`max |clip() - its formula in decimal| / max |the formula|`. The gain
    is built from the RMS the float clipper itself computes, in float64, so
    only the tanh subtraction differs between the two."""
    getcontext().prec = 60

    def dec(t):
        return Decimal(repr(float(t)))

    def tanh(z):
        e = (2 * z).exp()
        return (e - 1) / (e + 1)

    gain = dec(10.0 ** (drive / 20.0)) / dec(np.sqrt(np.mean(x ** 2)))
    bias, tb = dec(asym), tanh(dec(asym))

    def reference(xi):
        return (tanh(gain * dec(xi) + bias) - tb) / (gain * (1 - tb * tb))

    want = np.array([float(reference(xi)) for xi in x])
    got = v.clip(x, drive, asym)
    return float(np.max(np.abs(got - want)) / np.max(np.abs(want)))


def test_the_clipper_agrees_with_its_formula_in_decimal_arithmetic_at_every_documented_asymmetry():
    """An answer that does not come from float64: `clip()`'s own formula in
    60-digit decimal arithmetic. At the documented asymmetries the two agree
    to float64's last bits, which is what makes the sweeps' figures the
    block's and not the arithmetic's."""
    x = np.random.default_rng(0).standard_normal(96)
    for asym in (v.ASYM, *v.ASYM_SWEEP):
        assert _decimal_error(x, asym) < 1e-12, asym


def test_the_domain_guard_is_about_finiteness_and_its_blind_spot_is_measured():
    """Rule 8's second form: the adversary this guard does not stop, with the
    measurement. 15 is inside the domain and the clipper is already wrong by
    more than 1e-5 of full scale; 19 is inside and it is wrong by about 40 %.
    Stated here so nobody reads "inside the domain" as "accurate"."""
    x = np.random.default_rng(0).standard_normal(96)
    assert _decimal_error(x, 15.0) > 1e-5
    assert _decimal_error(x, 19.0) > 0.1


def test_a_float_that_starts_with_a_dash_is_joined_to_its_flag(monkeypatch):
    """argparse gives `--asym -inf` and `--asym -1e-9` to its option parser,
    which takes them for flags: a usage error, exit 2, nothing measured. A
    token that parses as a float is the flag's value whatever it starts
    with; anything else is left for argparse to reject."""
    explicit = v._asym_explicit
    assert explicit(["--asym", "-inf"]) == ["--asym=-inf"]
    assert explicit(["--asym", "-1e-9", "--source", "white"]) == \
        ["--asym=-1e-9", "--source", "white"]
    assert explicit(["--asym-sweep", "--asym", "nan"]) == ["--asym-sweep", "--asym=nan"]
    assert explicit(["--asym", "--out", "x"]) == ["--asym", "--out", "x"]
    assert explicit(["--asym", "abc"]) == ["--asym", "abc"]
    assert explicit(["--asym"]) == ["--asym"] and explicit([]) == []
    monkeypatch.setattr(sys, "argv", [str(TOOL), "--asym", "-inf"])
    assert explicit(None) == ["--asym=-inf"]


def test_the_census_reads_both_prepare_sites_as_covered():
    """`tools/refusal_sibling_scan.py` listed both `rc.prepare` sites as
    `candidate-escape`, guard `none`, on main. The census says covered only
    when a BASE handler sits at the CLI boundary: an exact one would read
    `covered(cli-exact)` and cover one family alone."""
    p = subprocess.run([sys.executable, str(SCAN), "--json"], capture_output=True, text=True,
                       timeout=300, check=False, cwd=str(ROOT))
    assert p.returncode == 0, p.stderr[-400:]
    sites = {s["site"]: s for s in json.loads(p.stdout)["sites"]
             if s["site"].startswith(TOOL_REL + ":")}
    assert len(sites) == 2, sites
    for site in sites.values():
        assert site["callee"] == "run_case.prepare", site
        assert site["origins"] == ["preparation_contract"], site
        assert site["classification"] == "covered(cli-base)" and site["uncovered"] == [], site
