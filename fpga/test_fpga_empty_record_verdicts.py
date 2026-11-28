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


@pytest.mark.parametrize("period", [999999, -8380])
def test_verify_fixture_zero_compared_i2s_periods_is_no_verdict(
        tmp_path, monkeypatch, capsys, period):
    """Pre-fix: `i2s` with one row whose period `p_ >= n` broke out of the
    loop on row 0 (0 periods compared), printed `PASS -- 1 I2S periods (0 ms
    of audio) ...` and exited 0."""
    fake, _, _ = _fake_fixture_out(tmp_path, [f"{period} 0 0 32 32"])
    monkeypatch.setattr(vst, "simulate", lambda *a, **kw: fake)
    code = vf.main(["--outdir", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == NO_VERDICT, out
    assert f"first index {period}" in out, out
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
    """Rule 8: a complete matching capture passes; an in-window core mismatch fails."""
    _, truth, n_fix = _fake_fixture_out(tmp_path, [])
    last = max(p.land for p in truth)
    n = max(n_fix, last + 600)
    m = stm.SynthTopModel().run(sh.model_writes(truth), n)
    exp_i2s, exp_s = m["i2s"], m["sample"]

    i2s_ok = [f"{p} {int(exp_i2s[p])} {int(exp_i2s[p])} 32 32" for p in range(n)]
    samp_ok = [f"{p} {int(exp_s[p])}" for p in range(5)]
    fake_ok, _, _ = _fake_fixture_out(tmp_path, i2s_ok, samp_ok + [f"{-n} 123"])
    monkeypatch.setattr(vst, "simulate", lambda *a, **kw: fake_ok)
    code = vf.main(["--outdir", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == PASS, out
    assert f"PASS -- {n} I2S periods" in out, out

    bad_s = int(exp_s[0]) ^ 1
    fake_bad, _, _ = _fake_fixture_out(tmp_path, i2s_ok, [f"0 {bad_s}"])
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
    summary = tmp_path / "controls.json"
    summary.write_text(json.dumps({"state": "PASS", "runs": {"phrase": "PASS"}}))
    monkeypatch.setattr(vub, "SCENARIOS", {})
    monkeypatch.setattr(vub, "simulate", lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError("simulate() must not run when there are no scenarios")))
    code = vub.main(["--outdir", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == NO_VERDICT, out
    assert "NO VERDICT" in out, out
    assert json.loads(summary.read_text()) == {"state": "REFUSED", "runs": {}}


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


@pytest.mark.parametrize("scenario, defect, state, code, diagnostic", [
    ("phrase", "missing-audio", "NO VERDICT", 2, "0 of 634 required I2S periods decoded"),
    ("phrase", "complete", "PASS", 0, "634 periods"),
    ("phrase", "busy", "FAIL", 1, "busy_at_tick"),
    ("phrase", "strobe", "FAIL", 1, "worst strobe cycle 256 outside 1..255"),
    ("noff-full", None, "NO VERDICT", 2, "gate-off"),
    ("overflow", None, "FAIL", 1, "expected 6 ERR(event-queue overflow), got 0"),
    ("held", None, "NO VERDICT", 2, "release"),
    ("reset-mid", None, "NO VERDICT", 2, "reset"),
])
def test_uart_capture_verdict(tmp_path, monkeypatch, capsys, scenario, defect,
                              state, code, diagnostic):
    def simulate(sc, inject, path):
        path.mkdir()
        run = _empty_uart_run(path, planned=[[]], scenario=sc,
                              extra_report=["SEG 0 origin_tcc 0 periods 0 strobes 0"])
        if defect in ("missing-audio", "complete"):
            body = [("write", 0, 0, 0, 0)]
            rows = vub.uh.plan(body)
            frame = rows[0].apply_frame
            run.update(bodies=[body], planned=[rows])
            (path / "wrs.txt").write_text(f"{frame} 0 0 0 0 {frame}\n")
            (path / "txd.txt").write_text(f"{frame} {vub.uh.RSP_ACK}\n{frame} 0\n")
            run["files"].update(wrs=path / "wrs.txt", txd=path / "txd.txt")
            run["report"][1] = "writes drained 1, spi+uart slot collisions 0"
            run["report"][-1] = "TX bytes captured 2"
            if defect == "complete":
                wire = path / "i2s.txt"
                wire.write_text("".join(f"{p} 0 0 32 32\n" for p in range(634)))
                run["files"]["i2s"] = wire
        elif defect == "busy":
            run["report"][3] = "busy at a tick: 1; core overrun 0; link overflow 0"
        elif defect == "strobe":
            run["report"][2] = run["report"][2].replace("cycle 100", "cycle 256")
        (path / "transcript.txt").write_text("\n".join(run["report"]))
        return run

    monkeypatch.setattr(vub, "simulate", simulate)
    rc = vub.main(["--scenario", scenario, "--outdir", str(tmp_path)])
    output = capsys.readouterr().out
    child = json.loads((tmp_path / scenario / "verification.json").read_text())
    assert rc == code, output
    assert (child["state"], child["exit_code"]) == (state, code)
    if defect == "busy":
        assert child["comparison"][diagnostic] == 1
    else:
        assert diagnostic in output
    assert json.loads((tmp_path / "controls.json").read_text()) == {
        "state": "REFUSED" if code == 2 else state, "runs": {scenario: state}}


def test_uart_injection_without_comparison_propagates_refusal(tmp_path, monkeypatch):
    def simulate(sc, inject, path):
        path.mkdir()
        run = _empty_uart_run(path, planned=[[]], scenario=sc,
                              extra_report=["SEG 0 origin_tcc 0 periods 0 strobes 0"])
        run["inject"] = inject
        # The expected checksum ERR is present. No comparison demonstrated a defect.
        tx = path / "txd.txt"
        tx.write_text("".join(f"0 {b}\n" for b in (vub.uh.RSP_ERR, vub.uh.ERR_CHECKSUM, 0, 0)))
        run["files"]["txd"] = tx
        run["report"][-1] = "TX bytes captured 4"
        (path / "transcript.txt").write_text("\n".join(run["report"]))
        return run

    monkeypatch.setattr(vub, "simulate", simulate)
    rc = vub.main(["--inject", "UART_NO_CHECKSUM", "--outdir", str(tmp_path)])
    child = json.loads((tmp_path / "drop-byte-inject-UART_NO_CHECKSUM/verification.json").read_text())
    assert (rc, child["state"], child["exit_code"]) == (2, "NO VERDICT", 2)


@pytest.mark.parametrize("mode, subdir", [("--start-red", "start-red"), ("--replay", "replay")])
def test_uart_empty_capture_cannot_confirm_a_control(tmp_path, monkeypatch, mode, subdir):
    def simulate(sc, inject, path, **kwargs):
        path.mkdir()
        run = _empty_uart_run(path, planned=[[]], scenario=sc,
                              extra_report=["SEG 0 origin_tcc 0 periods 0 strobes 0"])
        (path / "transcript.txt").write_text("\n".join(run["report"]))
        return run

    if mode == "--replay":
        monkeypatch.setattr(vub, "simulate_replay",
                            lambda prefix, path: simulate("replay", None, path))
        args = [mode, "capture"]
    else:
        monkeypatch.setattr(vub, "simulate", simulate)
        args = [mode]
    code = vub.main([*args, "--outdir", str(tmp_path)])
    rec = json.loads((tmp_path / subdir / "verification.json").read_text())
    assert (code, rec["state"], rec["exit_code"]) == (2, "NO VERDICT", 2)
    if mode == "--replay":
        assert rec["truncation_control"] == {"caught": False, "reason": "baseline NO VERDICT"}


@pytest.mark.parametrize("rows", ["", "0 0 0 32 32\n"])
def test_unchanged_uart_capture_cannot_count_as_a_truncation(tmp_path, rows):
    run = _empty_uart_run(tmp_path, planned=[])
    path = tmp_path / "i2s.txt"
    path.write_text(rows)
    run["files"]["i2s"] = path
    control = vub.truncation_control(run)
    assert control["caught"] is False
    assert control["kept_rows"] == control["of_rows"] == len(rows.splitlines())
