#!/usr/bin/env python3
"""#1466 tier-1 empty-record verdict controls for rtl-sketch verifiers.

    .venv/bin/python -m pytest rtl-sketch/test_rtl_empty_record_verdicts.py -q

Covers the three gated verifiers under `rtl-sketch/`:

  verify_ctl.py        FIXED: `compare_writes([], empty_out)` and a match
                       whose rows carry no cycle column (or all `x`) entered
                       the PASS branch and crashed on `min(cycs)` instead of
                       returning 2 (NO VERDICT) with `LAST` cleared; `main()`
                       with an empty `stimulus()` proceeded to simulate.
  verify_synth_top.py  FIXED: non-empty `i2s` whose first row has period
                       `p >= n` broke out of the comparison loop after 0
                       periods, saw `mism == swap == width == core_bad == 0`,
                       printed `PASS -- 1 I2S periods ...` and exited 0;
                       `--frames 0` was silently ignored and `--frames -5`
                       crashed after simulation; empty `cmds` was not refused
                       before simulation.
  verify_deadline.py   UNREACHABLE: every empty input (`wrs`, `i2s`, `sched`,
                       `SCHEDULE_RTL`, `provenance.files`, 0-segment Arty) is
                       already refused with exit 2 / non-empty reasons; pinned
                       here so the disposition stays verified.

Rule 8 controls (inputs that must keep their real PASS/FAIL verdict and must
not be swallowed by the empty-record guard) sit alongside each fix.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
for _p in (str(HERE), str(ROOT / "model"), str(ROOT / "fpga")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import verify_ctl as vc          # noqa: E402
import verify_deadline as vd     # noqa: E402
import verify_synth_top as vst   # noqa: E402

PASS, FAIL, NO_VERDICT = 0, 1, 2


# ---------------------------------------------------------------------------
# rtl-sketch/verify_ctl.py -- FIXED
# ---------------------------------------------------------------------------
def _write_ctl_log(path: pathlib.Path, rows: list[str]) -> str:
    path.write_text("".join(r + "\n" for r in rows))
    return str(path)


def test_verify_ctl_empty_writes_and_empty_log_is_no_verdict(tmp_path, capsys):
    """Pre-fix: `bad == 0`, `count_bad == False`, `late == []` entered PASS
    and raised `ValueError: min() iterable argument is empty`."""
    # Seed LAST first to confirm exit 2 leaves LAST empty (compare_writes contract).
    vc.LAST["stale"] = 1
    log = _write_ctl_log(tmp_path / "empty.txt", [])
    code = vc.compare_writes([], log)
    out = capsys.readouterr().out
    assert code == NO_VERDICT, out
    assert "NO VERDICT" in out, out
    assert vc.LAST == {}, "return 2 must leave LAST empty"


@pytest.mark.parametrize("row_text", ["0 0 63 0", "0 0 63 0 x"])
def test_verify_ctl_matching_writes_without_cycle_column_is_no_verdict(
        tmp_path, capsys, row_text):
    """Pre-fix: all fields matched, `cycs == []`, entered PASS and raised
    `ValueError: min() iterable argument is empty`."""
    vc.LAST["stale"] = 1
    log = _write_ctl_log(tmp_path / "nocyc.txt", [row_text])
    code = vc.compare_writes([(0, 0, 0x3F, 0)], log)
    out = capsys.readouterr().out
    assert code == NO_VERDICT, out
    assert "NO VERDICT" in out, out
    assert vc.LAST == {}, "return 2 must leave LAST empty"


def test_verify_ctl_main_empty_stimulus_refuses_before_simulating(
        tmp_path, monkeypatch, capsys):
    """Empty `stimulus()` must refuse with exit 2 before calling `simulate`."""
    monkeypatch.setattr(vc, "stimulus", lambda: [])
    monkeypatch.setattr(vc, "simulate", lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError("simulate() must not be called for an empty stimulus")))
    code = vc.main(["--outdir", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == NO_VERDICT, out
    assert "NO VERDICT" in out, out


def test_verify_ctl_main_empty_stimulus_with_expect_fail_is_no_verdict(
        tmp_path, monkeypatch, capsys):
    """Under `--expect-fail`, an empty stimulus is still exit 2 (not caught)."""
    monkeypatch.setattr(vc, "stimulus", lambda: [])
    monkeypatch.setattr(vc, "simulate", lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError("simulate() must not be called for an empty stimulus")))
    code = vc.main(["--expect-fail", "--outdir", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == NO_VERDICT, out


def test_verify_ctl_rule8_valid_run_still_passes_and_bad_nocyc_still_fails(
        tmp_path, capsys):
    """Rule 8: a real match with cycles stays exit 0; a measured field error
    with no cycle column stays exit 1 and keeps LAST populated for blindness."""
    good_log = _write_ctl_log(tmp_path / "good.txt", ["0 0 63 0 2"])
    assert vc.compare_writes([(0, 0, 0x3F, 0)], good_log) == PASS
    assert vc.LAST["compared"] == 1 and vc.LAST["cyc_seen"] == 1
    capsys.readouterr()

    bad_log = _write_ctl_log(tmp_path / "bad.txt", ["0 0 63 1"])
    assert vc.compare_writes([(0, 0, 0x3F, 0)], bad_log) == FAIL
    assert vc.LAST["bad_data"] == 1 and vc.LAST["cyc_seen"] == 0


# ---------------------------------------------------------------------------
# rtl-sketch/verify_synth_top.py -- FIXED
# ---------------------------------------------------------------------------
def _fake_synth_top_out(tmp_path: pathlib.Path, cmds: list, i2s_lines: list[str],
                        samp_lines: list[str] | None = None) -> dict:
    f = 10
    wrs_lines = []
    for wait, fl, sec, addr, data in cmds:
        f += wait
        wrs_lines.append(f"{f} {fl} {sec} {addr} {data} {f}\n")
    wrs_f = tmp_path / "top_wrs.txt"
    wrs_f.write_text("".join(wrs_lines))
    i2s_f = tmp_path / "top_i2s.txt"
    i2s_f.write_text("".join(l + "\n" for l in i2s_lines))
    samp_f = tmp_path / "top_samp.txt"
    samp_f.write_text("".join(l + "\n" for l in (samp_lines or [])))
    return dict(
        sources=vst.resolve_sources(None),
        report=[
            "datapath busy at a tick: 0; overrun 0; overflow 0",
            "sample strobed in 100 frames, MISSING in 0, worst strobe cycle 120",
        ],
        wrs=str(wrs_f), i2s=str(i2s_f), samp=str(samp_f),
    )


@pytest.mark.parametrize("period", [999999, -352])
def test_verify_synth_top_zero_compared_i2s_periods_is_no_verdict(
        tmp_path, monkeypatch, capsys, period):
    """Pre-fix: `i2s` with one row whose period index `p >= n` broke out of
    the loop on row 0 (0 periods compared), printed `PASS -- 1 I2S periods
    decoded from the wire, every one identical to the model` and exited 0."""
    cmds, _, _ = vst.script(short=True)
    fake = _fake_synth_top_out(tmp_path, cmds, [f"{period} 0 0 32 32"])
    monkeypatch.setattr(vst, "simulate", lambda *a, **kw: fake)
    code = vst.main(["--short", "--outdir", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == NO_VERDICT, out
    assert "NO VERDICT" in out, out
    assert "verify_synth_top: PASS" not in out, out


@pytest.mark.parametrize("frames", ["0", "-5"])
def test_verify_synth_top_non_positive_frames_refuses_before_simulating(
        tmp_path, monkeypatch, capsys, frames):
    """`--frames 0` was silently ignored; `--frames -5` crashed after running
    the simulation. Both must refuse with exit 2 before `simulate` runs."""
    monkeypatch.setattr(vst, "simulate", lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError("simulate() must not run for non-positive --frames")))
    code = vst.main(["--short", "--frames", frames, "--outdir", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == NO_VERDICT, out
    assert "NO VERDICT" in out, out


def test_verify_synth_top_empty_cmds_refuses_before_simulating(
        tmp_path, monkeypatch, capsys):
    """A focused stimulus returning no commands must refuse before simulation."""
    monkeypatch.setattr(vst, "f1cal_script",
                        lambda cal, fault: ([], [], 10, {"calibration": cal, "image": {}, "points": []}))
    monkeypatch.setattr(vst, "simulate", lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError("simulate() must not run for an empty command list")))
    code = vst.main(["--f1cal-smoke", "surge-type2-clean-v1", "--outdir", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == NO_VERDICT, out
    assert "NO VERDICT" in out, out


def test_verify_synth_top_rule8_real_match_passes_and_core_mismatch_fails(
        tmp_path, monkeypatch, capsys):
    """Rule 8: matching periods inside `0..n-1` still pass (exit 0); a core
    mismatch with 0 compared I2S periods still fails (exit 1, failure outranks
    no verdict)."""
    import synth_top_model as stm

    cmds, tail, _ = vst.script(short=True)
    f = 10
    model_writes = []
    for wait, fl, sec, addr, data in cmds:
        f += wait
        model_writes.append((f, fl, sec, addr, data))
    n = f + tail + 1
    m = stm.SynthTopModel().run(model_writes, n)
    exp_i2s, exp_s = m["i2s"], m["sample"]

    # Real match over 5 periods inside 0..n-1: PASS (exit 0)
    i2s_ok = [f"{p} {int(exp_i2s[p])} {int(exp_i2s[p])} 32 32" for p in range(5)]
    samp_ok = [f"{p} {int(exp_s[p])}" for p in range(5)]
    fake_ok = _fake_synth_top_out(tmp_path, cmds, i2s_ok, samp_ok + [f"{-n} 123"])
    monkeypatch.setattr(vst, "simulate", lambda *a, **kw: fake_ok)
    code = vst.main(["--short", "--outdir", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == PASS, out
    assert "PASS -- 5 I2S periods" in out, out

    # Core sample differs while I2S has only p >= n: FAIL (exit 1)
    bad_s = int(exp_s[0]) ^ 1
    fake_bad = _fake_synth_top_out(tmp_path, cmds, ["999999 0 0 32 32"], [f"0 {bad_s}"])
    monkeypatch.setattr(vst, "simulate", lambda *a, **kw: fake_bad)
    code_bad = vst.main(["--short", "--outdir", str(tmp_path)])
    capsys.readouterr()
    assert code_bad == FAIL


# ---------------------------------------------------------------------------
# rtl-sketch/verify_deadline.py -- UNREACHABLE, refusals pinned
# ---------------------------------------------------------------------------
def test_verify_deadline_empty_inputs_are_refused(tmp_path, monkeypatch):
    """Every empty-record path in `verify_deadline.py` is already refused:
    empty wrs, empty i2s, empty sched, empty SCHEDULE_RTL / provenance.files,
    and 0-segment Arty evaluation."""
    wrs_e = tmp_path / "wrs_e.txt"; wrs_e.write_text("")
    wrs_1 = tmp_path / "wrs_1.txt"; wrs_1.write_text("0 0 0 0 0 0\n")
    i2s_e = tmp_path / "i2s_e.txt"; i2s_e.write_text("")
    sched_e = tmp_path / "sched_e.txt"; sched_e.write_text("")
    report = [
        "datapath busy at a tick: 0; overrun 0; overflow 0",
        "sample strobed in 10 frames, MISSING in 0, worst strobe cycle 120",
    ]
    meta = dict(regs={}, claim="three_2x_saws", drums=False)

    # Empty wrs -> NO VERDICT (exit 2)
    res_w = vd.evaluate_spi(dict(wrs=str(wrs_e), i2s=str(i2s_e), sched=str(sched_e)),
                            [], 300, meta, osc2x=True, filter2x=True, pulse2x=False,
                            bench_report=report)
    st_w, _, _ = vd.verdict(res_w, vd.analyse_sched(res_w["sched_rows"], 8), 8)
    assert st_w == NO_VERDICT

    # Non-empty wrs, empty i2s & empty sched -> NO VERDICT (exit 2)
    res_i = vd.evaluate_spi(dict(wrs=str(wrs_1), i2s=str(i2s_e), sched=str(sched_e)),
                            [(0, 0, 0, 0, 0)], 300, meta, osc2x=True, filter2x=True,
                            pulse2x=False, bench_report=report)
    st_i, _, _ = vd.verdict(res_i, vd.analyse_sched(res_i["sched_rows"], 8), 8)
    assert st_i == NO_VERDICT

    # 0-segment Arty run -> NO VERDICT (exit 2)
    e_f = tmp_path / "arty_e.txt"; e_f.write_text("")
    arty_run = dict(
        outdir=str(tmp_path), items=[], bodies=[], planned=[], reset_frames=[],
        report=[
            "writes drained 0, spi+uart slot collisions 0",
            "sample strobed in 10 frames, MISSING in 0, worst strobe cycle 100 of 256",
            "busy at a tick: 0; core overrun 0; link overflow 0",
            "TX bytes captured 0",
        ],
        files=dict(wrs=str(e_f), txd=str(e_f), i2s=str(e_f), samp=str(e_f)),
        scenario="deadline-arty", inject=None, defines=[],
    )
    res_a = vd.evaluate_arty(arty_run, {})
    st_a, _, _ = vd.verdict(res_a, vd.analyse_sched(res_a["sched_rows"], 8), 8)
    assert st_a == NO_VERDICT

    # Empty SCHEDULE_RTL / empty provenance.files -> non-empty refusal
    with monkeypatch.context() as m:
        m.setattr(vd, "SCHEDULE_RTL", {})
        assert vd.rtl_binding({"provenance": {"files": {"a.v": "0"}}})
    assert vd.rtl_binding({"provenance": {"files": {}}})
