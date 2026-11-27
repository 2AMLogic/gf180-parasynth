#!/usr/bin/env python3
"""#1466 tier-1 empty-record verdict controls for fpga verifiers.

    .venv/bin/python -m pytest fpga/test_fpga_empty_record_verdicts.py -q

Covers the two gated verifiers under `fpga/`:

  verify_fixture.py      FIXED: non-empty `i2s` whose first row has period
                         `p_ >= n` broke out of the comparison loop after 0
                         periods, saw `mism == swap == width == core_bad == 0`,
                         printed `PASS -- 1 I2S periods (0 ms of audio) ...`
                         and exited 0; `--frames 0` was silently ignored and
                         `--frames -5` crashed after simulation.
  verify_uart_bridge.py  FIXED: (1) empty `SCENARIOS = {}` under `--scenario
                         all` left `work = []`, `all([])` evaluated True, and
                         `_main` wrote `{"state": "PASS", "runs": {}}` and
                         exited 0 without simulating anything; (2) `analyze()`
                         on a run with 0 expected writes (`planned = []` or
                         `planned = [[]]`) left `required_periods == 0`,
                         `i2s_incomplete == False`, and returned `(True, ...)`.

Rule 8 controls (real passing and failing runs keep exit 0 / exit 1) sit
alongside each fix.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
for _p in (str(HERE), str(ROOT / "rtl-sketch"), str(ROOT / "model")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import fixtures                 # noqa: E402
import spi_host as sh           # noqa: E402
import synth_top_model as stm   # noqa: E402
import verify_fixture as vf     # noqa: E402
import verify_synth_top as vst  # noqa: E402
import verify_uart_bridge as vub  # noqa: E402

PASS, FAIL, NO_VERDICT = 0, 1, 2


# ---------------------------------------------------------------------------
# fpga/verify_fixture.py -- FIXED
# ---------------------------------------------------------------------------
def _fake_fixture_out(tmp_path: pathlib.Path, i2s_lines: list[str],
                      samp_lines: list[str] | None = None):
    host, n_fix, _ = fixtures.FIXTURES["bar808"]()
    laid = sh.lay_out(host.w, sh.BENCH)
    truth = sh.place(laid, sh.BENCH)
    wrs_f = tmp_path / "top_wrs.txt"
    wrs_f.write_text("".join(
        f"{p.land} {p.w.key()[0]} {p.w.key()[1]} {p.w.key()[2]} {p.w.key()[3]} {p.land}\n"
        for p in truth))
    i2s_f = tmp_path / "top_i2s.txt"
    i2s_f.write_text("".join(l + "\n" for l in i2s_lines))
    samp_f = tmp_path / "top_samp.txt"
    samp_f.write_text("".join(l + "\n" for l in (samp_lines or [])))
    out = dict(
        report=[
            "datapath busy at a tick: 0; overrun 0; overflow 0",
            "sample strobed in 100 frames, MISSING in 0, worst strobe cycle 120",
        ],
        wrs=str(wrs_f), i2s=str(i2s_f), samp=str(samp_f),
    )
    return out, truth, n_fix


def test_verify_fixture_zero_compared_i2s_periods_is_no_verdict(
        tmp_path, monkeypatch, capsys):
    """Pre-fix: `i2s` with one row whose period `p_ >= n` broke out of the
    loop on row 0 (0 periods compared), printed `PASS -- 1 I2S periods (0 ms
    of audio) ...` and exited 0."""
    fake, _, _ = _fake_fixture_out(tmp_path, ["999999 0 0 32 32"])
    monkeypatch.setattr(vst, "simulate", lambda *a, **kw: fake)
    code = vf.main(["--outdir", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == NO_VERDICT, out
    assert "NO VERDICT" in out, out
    assert "verify_fixture: PASS" not in out, out


@pytest.mark.parametrize("frames", ["0", "-5"])
def test_verify_fixture_non_positive_frames_refuses_before_simulating(
        tmp_path, monkeypatch, capsys, frames):
    """`--frames 0` was silently ignored; `--frames -5` crashed after running
    the simulation. Both must refuse with exit 2 before `simulate` runs."""
    monkeypatch.setattr(vst, "simulate", lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError("simulate() must not run for non-positive --frames")))
    code = vf.main(["--frames", frames, "--outdir", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == NO_VERDICT, out
    assert "NO VERDICT" in out, out


def test_verify_fixture_rule8_real_match_passes_and_core_mismatch_fails(
        tmp_path, monkeypatch, capsys):
    """Rule 8: matching periods inside `0..n-1` stay exit 0; a core mismatch
    even with 0 compared I2S periods stays exit 1."""
    _, truth, n_fix = _fake_fixture_out(tmp_path, [])
    last = max(p.land for p in truth)
    n = max(n_fix, last + 600)
    m = stm.SynthTopModel().run(sh.model_writes(truth), n)
    exp_i2s, exp_s = m["i2s"], m["sample"]

    i2s_ok = [f"{p} {int(exp_i2s[p])} {int(exp_i2s[p])} 32 32" for p in range(5)]
    samp_ok = [f"{p} {int(exp_s[p])}" for p in range(5)]
    fake_ok, _, _ = _fake_fixture_out(tmp_path, i2s_ok, samp_ok)
    monkeypatch.setattr(vst, "simulate", lambda *a, **kw: fake_ok)
    code = vf.main(["--outdir", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == PASS, out
    assert "PASS -- 5 I2S periods" in out, out

    bad_s = int(exp_s[0]) ^ 1
    fake_bad, _, _ = _fake_fixture_out(tmp_path, ["999999 0 0 32 32"], [f"0 {bad_s}"])
    monkeypatch.setattr(vst, "simulate", lambda *a, **kw: fake_bad)
    code_bad = vf.main(["--outdir", str(tmp_path)])
    capsys.readouterr()
    assert code_bad == FAIL


# ---------------------------------------------------------------------------
# fpga/verify_uart_bridge.py -- FIXED
# ---------------------------------------------------------------------------
def _empty_uart_run(tmp_path: pathlib.Path, *, planned: list,
                    extra_report: list[str] | None = None,
                    scenario: str = "phrase") -> dict:
    e_f = tmp_path / "empty.txt"
    e_f.write_text("")
    report = list(extra_report or []) + [
        "writes drained 0, spi+uart slot collisions 0",
        "sample strobed in 10 frames, MISSING in 0, worst strobe cycle 100 of 256",
        "busy at a tick: 0; core overrun 0; link overflow 0",
        "TX bytes captured 0",
    ]
    return dict(
        outdir=tmp_path, items=[], bodies=[], planned=planned, reset_frames=[],
        report=report,
        files=dict(wrs=str(e_f), txd=str(e_f), i2s=str(e_f), samp=str(e_f)),
        scenario=scenario, inject=None, defines=[],
    )


def test_verify_uart_bridge_empty_scenarios_is_no_verdict(
        tmp_path, monkeypatch, capsys):
    """Pre-fix: `SCENARIOS = {}` left `work = []`, `all([])` was True,
    wrote `{"state": "PASS", "runs": {}}` and exited 0."""
    monkeypatch.setattr(vub, "SCENARIOS", {})
    monkeypatch.setattr(vub, "simulate", lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError("simulate() must not run when there are no scenarios")))
    code = vub.main(["--outdir", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == NO_VERDICT, out
    assert "NO VERDICT" in out, out
    assert not (tmp_path / "controls.json").exists(), "controls.json must not record PASS"


def test_verify_uart_bridge_analyze_empty_planned_is_not_clean(tmp_path):
    """Pre-fix: `analyze()` on 0 segments / 0 planned rows left
    `periods_required == 0`, `i2s_incomplete == False`, and returned `ok=True`."""
    run = _empty_uart_run(tmp_path, planned=[])
    ok, comp, _detail = vub.analyze(run)
    assert not ok, f"empty planned run returned ok=True: {comp}"
    assert comp["i2s_incomplete"] is True
    assert comp["periods_required"] == 0


def test_verify_uart_bridge_analyze_one_segment_zero_writes_is_not_clean(tmp_path):
    """Pre-fix: 1 segment with 0 writes skipped the model comparison loop
    (`if not exp: continue`), leaving `i2s_incomplete == False` and `ok=True`."""
    run = _empty_uart_run(tmp_path, planned=[[]],
                          extra_report=["SEG 0 origin_tcc 0 periods 0 strobes 0"])
    ok, comp, _detail = vub.analyze(run)
    assert not ok, f"0-write segment returned ok=True: {comp}"
    assert comp["i2s_incomplete"] is True
    assert comp["periods_required"] == 0


def test_verify_uart_bridge_main_zero_write_scenario_exits_no_verdict(
        tmp_path, monkeypatch, capsys):
    """A scenario whose simulation produces 0 required periods must exit 2
    (NO VERDICT), not 0 (PASS) or 1 (FAIL)."""
    sub = tmp_path / "phrase"
    sub.mkdir()
    run = _empty_uart_run(sub, planned=[[]],
                          extra_report=["SEG 0 origin_tcc 0 periods 0 strobes 0"])
    (sub / "transcript.txt").write_text("\n".join(run["report"]) + "\n")
    monkeypatch.setattr(vub, "simulate", lambda sc, inj, outdir, **kw: run)
    code = vub.main(["--scenario", "phrase", "--outdir", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == NO_VERDICT, out
    rec = json.loads((tmp_path / "controls.json").read_text())
    assert rec == {"state": "REFUSED", "runs": {"phrase": "NO VERDICT"}}
    child = json.loads((sub / "verification.json").read_text())
    assert (child["state"], child["exit_code"]) == ("NO VERDICT", 2)


def test_verify_uart_bridge_rule8_pass_and_fail_runs_keep_their_exits(
        tmp_path, monkeypatch, capsys):
    """Rule 8: a real PASS run stays exit 0, and a real FAIL run (even with 0
    required periods, e.g. `busy_at_tick == 1`) stays exit 1."""
    sub = tmp_path / "phrase"
    sub.mkdir()
    run = _empty_uart_run(sub, planned=[[]],
                          extra_report=["SEG 0 origin_tcc 0 periods 0 strobes 0"])
    (sub / "transcript.txt").write_text("\n".join(run["report"]) + "\n")
    monkeypatch.setattr(vub, "simulate", lambda sc, inj, outdir, **kw: run)

    # Mock analyze returning a genuine PASS
    monkeypatch.setattr(vub, "analyze", lambda r: (True, {"periods": 10, "periods_required": 10,
                                                          "i2s_incomplete": False}, []))
    code_ok = vub.main(["--scenario", "phrase", "--outdir", str(tmp_path)])
    capsys.readouterr()
    assert code_ok == PASS
    assert json.loads((tmp_path / "controls.json").read_text())["state"] == "PASS"

    # Real FAIL: busy_at_tick == 1 with 0 required periods -> failure outranks no verdict
    run_fail = dict(run, report=[
        "SEG 0 origin_tcc 0 periods 0 strobes 0",
        "writes drained 0, spi+uart slot collisions 0",
        "sample strobed in 10 frames, MISSING in 0, worst strobe cycle 100 of 256",
        "busy at a tick: 1; core overrun 0; link overflow 0",
        "TX bytes captured 0",
    ])
    monkeypatch.setattr(vub, "simulate", lambda sc, inj, outdir, **kw: run_fail)
    monkeypatch.undo()  # restore real analyze
    monkeypatch.setattr(vub, "simulate", lambda sc, inj, outdir, **kw: run_fail)
    code_fail = vub.main(["--scenario", "phrase", "--outdir", str(tmp_path)])
    capsys.readouterr()
    assert code_fail == FAIL
