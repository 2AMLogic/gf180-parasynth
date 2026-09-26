"""The production-deadline driver's arithmetic and verdicts, on schedule rows
whose answer is known by construction (plan075 T1, docs/deadline/README.md).

The simulation itself is exercised by rtl-sketch/verify_deadline.py and its
VOICE_LATE_DONE control; these tests pin the part that turns monitor rows into
a verdict, because a deadline checker's only failure that matters is a false
green."""
import os
import re
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "rtl-sketch"))

import verify_deadline as vd  # noqa: E402

GO = 8


def row(frame, *, go=GO, strobe=150, v_last=150, d_last=100, wr_n=0, wr_first=-1, wr_last=-1,
        wr_in_compute=0, busy_at_tick=0):
    return dict(frame=frame, go=go, v_start=go + 1, v_last=v_last, strobe=strobe, d_start=go + 1,
                d_last=d_last, wr_n=wr_n, wr_first=wr_first, wr_last=wr_last,
                wr_in_compute=wr_in_compute, busy_at_tick=busy_at_tick, rwait=0, oscwait=0, dwait=0,
                win=0, w1=0, im1=0, sk=0, ywait=5)


def clean_res(claim_frames=500):
    return dict(meta=dict(claim="three_2x_audible", drums=True),
                coverage=dict(three_2x_audible_gate_on=claim_frames, three_2x_audible_with_strike=10),
                busy_at_tick=0, overrun=0, overflow=0, frames_no_sample=0, wire_mismatch=0, swap=0,
                width=0, writes_bad=0, frame_pred_bad=0, writes_sent=5, writes_seen=5)


def test_slack_is_measured_to_cycle_254_for_the_strobe_and_255_for_busy():
    s = vd.analyse_sched([row(f, strobe=200, v_last=201) for f in range(20)], GO)
    assert s["worst_sample_slack"] == 54 and s["worst_busy_slack"] == 54
    s = vd.analyse_sched([row(f, strobe=254, v_last=255) for f in range(20)], GO)
    assert s["worst_sample_slack"] == 0 and s["worst_busy_slack"] == 0 and s["missed"] == 0


def test_a_strobe_in_cycle_255_is_a_missed_frame():
    rows = [row(f) for f in range(20)]
    rows[7] = row(7, strobe=255, v_last=255)
    s = vd.analyse_sched(rows, GO)
    assert s["missed"] == 1 and s["missed_frames"] == [7] and s["worst_sample_slack"] == -1
    st, reasons, deadline = vd.verdict(clean_res(), s, GO)
    assert st == 1 and deadline and "missed" in reasons[0]


def test_busy_at_the_tick_or_no_strobe_is_a_missed_frame():
    rows = [row(f) for f in range(20)]
    rows[5] = row(5, busy_at_tick=1)
    rows[9] = row(9, strobe=-1)
    s = vd.analyse_sched(rows, GO)
    assert s["missed_frames"] == [5, 9]


def test_the_reset_frames_are_not_scored():
    rows = [row(0, strobe=-1), row(1, strobe=-1)] + [row(f) for f in range(2, 20)]
    assert vd.analyse_sched(rows, GO)["missed"] == 0


def test_clean_rows_pass_and_a_sticky_overrun_alone_fails_as_a_deadline():
    s = vd.analyse_sched([row(f) for f in range(20)], GO)
    assert vd.verdict(clean_res(), s, GO)[0] == 0
    res = clean_res(); res["overrun"] = 1
    st, _, deadline = vd.verdict(res, s, GO)
    assert st == 1 and deadline


def test_an_i2s_mismatch_fails_but_is_not_a_deadline_failure():
    s = vd.analyse_sched([row(f) for f in range(20)], GO)
    res = clean_res(); res["wire_mismatch"] = 3
    st, _, deadline = vd.verdict(res, s, GO)
    assert st == 1 and not deadline          # --expect-fail must not accept this as the control


def test_a_write_at_or_after_go_fails():
    rows = [row(f) for f in range(20)]
    rows[4] = row(4, wr_n=1, wr_first=GO, wr_last=GO, wr_in_compute=1)
    s = vd.analyse_sched(rows, GO)
    assert s["writes_in_compute"] == 1 and s["frames_with_write_after_go"] == 1
    assert vd.verdict(clean_res(), s, GO)[0] == 1


def test_go_elsewhere_than_the_chips_cycle_refuses():
    s = vd.analyse_sched([row(f, go=48) for f in range(20)], GO)
    assert vd.verdict(clean_res(), s, GO)[0] == 2


def test_a_stimulus_that_never_reaches_its_claim_refuses():
    s = vd.analyse_sched([row(f) for f in range(20)], GO)
    assert vd.verdict(clean_res(claim_frames=3), s, GO)[0] == 2


def test_coverage_counts_three_2x_shapes_only_with_the_gate_on():
    saw, p29 = vd.vf.WAVE_CODE["saw"], vd.vf.WAVE_CODE["pulse29"]
    mw = [(0, 0, 0, vd.stm.A_WAVE + k, saw) for k in range(3)] \
        + [(0, 0, 0, vd.stm.A_W + k, 1000) for k in range(3)] \
        + [(5, 0, 0, vd.stm.A_GATE_ON, 0), (10, 0, 0, vd.stm.A_WAVE + 1, p29),
           (15, 0, 0, vd.stm.A_GATE_OFF, 0)]
    tl = vd.image_timeline(mw, 20)
    assert vd.coverage(tl, pulse2x=False)["three_2x_audible_gate_on"] == 5     # frames 5..9
    assert vd.coverage(tl, pulse2x=True)["three_2x_audible_gate_on"] == 10     # frames 5..14


def test_the_component_bench_launches_where_the_chip_does():
    """tb_voice.v's GO is synth_top.v's GO_CYCLE (verify_voice.py refuses
    otherwise); pinned here so the drift is caught without a simulator."""
    def param(path, name):
        return int(re.search(rf"parameter\s+{name}\s*=\s*(\d+)",
                             open(os.path.join(ROOT, "rtl-sketch", path)).read()).group(1))
    assert param("tb_voice.v", "GO") == param("synth_top.v", "GO_CYCLE") == vd.chip_go_cycle()


def test_the_cost_model_is_explained_only_when_one_constant_remains():
    rows = [row(f, strobe=100 + 2 * (f % 3)) for f in range(20)]
    for r in rows:
        r["w1"] = r["frame"] % 3                       # each active window costs two cycles
    assert vd.cost_model(rows[2:])["explained"]
    rows[10]["strobe"] += 1                             # a cycle the counts do not explain
    assert not vd.cost_model(rows[2:])["explained"]


def test_every_mutant_anchor_occurs_exactly_once_in_the_current_voice(tmp_path):
    """The late-completion control and the candidate correction are generated
    from voice_dp.v at run time; an anchor that moved would REFUSE the control
    at run time -- pinned here so the drift is seen without a simulator."""
    src = open(os.path.join(ROOT, "rtl-sketch", "voice_dp.v")).read()
    for kind, edits in vd.MUTANTS.items():
        for anchor, _ in edits:
            assert src.count(anchor) == 1, (kind, anchor)
    d = vd.make_mutant("late:37", str(tmp_path))
    mutated = open(os.path.join(d, "voice_dp.v")).read()
    assert mutated.count("MUTANT late") == 3 and "9'd37" in mutated
    # the stall is in S_OUT2, after every wait, so it cannot overlap one and be absorbed
    assert "S_OUT2: if (late_cnt != 9'd37)" in mutated


# ---- analysis version 2: the reconciled cost model (plan080 Repair 1) --------------
def _committed_rows(record, frames):
    import json
    d = json.load(open(os.path.join(ROOT, "docs", "deadline", "runs", record)))
    by = {r["frame"]: r for r in d["schedule"]["worst_frames"]}
    return [dict(by[f]) for f in frames]


def test_paired_frames_with_different_ywait_and_equal_completion_are_one_constant():
    """stress-saw frames 538 and 840: every serial term equal, ywait 5 vs 11,
    strobe 240 in both. ywait trades with shadow work; it is not a serial term.
    Version 1 subtracted it and reported 82 / 76 -- `explained` False."""
    rows = _committed_rows("prod-stress-saw.json", (538, 840))
    assert rows[0]["ywait"] != rows[1]["ywait"] and rows[0]["strobe"] == rows[1]["strobe"]
    cm = vd.cost_model(rows)
    assert cm["explained"] and cm["constant"] == 87 and cm["residuals"] == {87: 2}
    v1 = {r["strobe"] - (r["rwait"] + r["oscwait"] + r["dwait"] + r["ywait"] + 2 * r["w1"]
                         + r["win"] + r["im1"] + 3 * r["sk"]) for r in rows}
    assert v1 == {82, 76}                      # what the superseded formula said


def test_a_frame_where_the_shadow_work_ends_the_wait_is_an_exception_not_explained():
    rows = _committed_rows("prod-stress-saw.json", (538, 840))
    rows[1]["ywait"] = 1                       # no wait: the overlap condition is not met
    cm = vd.cost_model(rows)
    assert not cm["explained"] and cm["exceptions"] == 1


# ---- evidence completeness (plan080 Repair 2): red first on a real capture ------------
import gzip, shutil  # noqa: E402

TRACE = os.path.join(ROOT, "docs", "deadline", "traces")
# The judge compares a capture against the stimulus the scenario builds NOW.
# A capture is only a clean capture of THIS tree if it was driven by that
# stimulus, so _capture asserts it -- the same precondition
# verify_deadline.analyse_capture enforces with REFUSED. The retained
# `prod-stress-saw` / `ctl-prod-stress-late15` captures (2221d2c) predate
# contract revision 13, whose kit has one more write (461 vs 460), and stay in
# the tree as history; these tests use the `-l2` re-captures
# (docs/deadline/runs/*-l2.json, build box, tools/run_all.py 2/2).
CLEAN, LATE15 = "prod-stress-saw-l2", "ctl-prod-stress-late15-l2"


def _stimulus_matches(d, scenario="stress-saw"):
    import tempfile
    import verify_synth_top as vst
    cmds, _, _ = vd.SPI_SCENARIOS[scenario](False)
    with tempfile.TemporaryDirectory() as t:
        probe = os.path.join(t, "cmds.txt")
        vst.write_cmds(probe, cmds)
        return open(probe, "rb").read() == open(os.path.join(d, "top_bx_cmds.txt"), "rb").read()


def _capture(tmp_path, record, scenario="stress-saw"):
    d = tmp_path / record
    d.mkdir()
    for gz in os.listdir(os.path.join(TRACE, record)):
        with gzip.open(os.path.join(TRACE, record, gz), "rb") as src, open(d / gz[:-3], "wb") as dst:
            shutil.copyfileobj(src, dst)
    assert _stimulus_matches(d, scenario), (
        f"{record} was not driven by this tree's {scenario} stimulus: re-capture it, "
        "do not judge historical evidence against current source")
    wrs = [f for f in os.listdir(d) if f.startswith("top_wrs_") and f.endswith(".txt")][0]
    i2s = [f for f in os.listdir(d) if f.startswith("top_i2s_")][0]
    return dict(i2s=str(d / i2s), wrs=str(d / wrs), sched=str(d / (wrs + ".sched")))


def test_the_pre_revision_13_captures_are_history_not_this_trees_stimulus(tmp_path):
    """The control for _capture's precondition: the retained pre-L2 capture is
    refused as this tree's evidence (it stays in the tree, unmodified)."""
    with pytest.raises(AssertionError, match="not driven by this tree"):
        _capture(tmp_path, "prod-stress-saw")


def _judge(files, scenario="stress-saw", overflow=0):
    cmds, tail, meta = vd.SPI_SCENARIOS[scenario](False)
    res = vd.evaluate_spi(files, cmds, tail, meta, osc2x=True, filter2x=True, pulse2x=False,
                          bench_report=None, recorded_overflow=overflow)
    rows = res.get("sched_rows", [])
    return (res,) + vd.verdict(res, vd.analyse_sched(rows, GO), GO)


def _edit(path, fn):
    lines = open(path).read().splitlines(keepends=True)
    open(path, "w").write("".join(fn(lines)))


def test_verdict_refuses_zero_compared_periods_even_with_clean_facts():
    """The reviewer's probe: clean facts with periods 0 or 1 used to PASS."""
    s = vd.analyse_sched([row(f) for f in range(20)], GO)
    for n in (0, 1):
        res = clean_res(); res["evidence"] = vd.i2s_completeness(list(range(n)), 2156)
        st, reasons, deadline = vd.verdict(res, s, GO)
        assert st == 2 and not deadline and "incomplete" in reasons[0]


def test_i2s_completeness_names_every_defect():
    good = list(range(10))
    assert vd.i2s_completeness(good, 10) == []
    assert vd.i2s_completeness([], 10)
    assert vd.i2s_completeness(good[:7], 10)                       # truncated
    assert vd.i2s_completeness(good[:4] + good[5:], 9)             # interior gap
    assert vd.i2s_completeness(good[:5] + [4] + good[5:], 10)      # duplicate
    assert vd.i2s_completeness([1, 0] + good[2:], 10)              # out of order


def test_the_retained_clean_capture_passes_and_each_corruption_refuses(tmp_path):
    files = _capture(tmp_path, CLEAN)
    res, st, reasons, dl = _judge(files)
    # 2158 on the revision-13 re-capture; the pre-L2 capture required 2156
    assert st == 0 and res["periods"] == res["periods_required"] == 2158
    corruptions = {
        "i2s-empty": ("i2s", lambda L: []),
        "i2s-truncated": ("i2s", lambda L: L[:1000]),
        "i2s-interior-period-dropped": ("i2s", lambda L: L[:700] + L[701:]),
        "i2s-duplicated": ("i2s", lambda L: L[:700] + [L[700]] + L[700:]),
        "sched-empty": ("sched", lambda L: []),
        "sched-interior-frame-dropped": ("sched", lambda L: L[:538] + L[539:]),
        "sched-malformed-line": ("sched", lambda L: L[:300] + ["F 300 8 9\n"] + L[301:]),
    }
    for name, (key, fn) in corruptions.items():
        (tmp_path / name).mkdir()
        bad = _capture(tmp_path / name, CLEAN)
        _edit(bad[key], fn)
        r, st, reasons, dl = _judge(bad)
        assert st == 2 and not dl and "incomplete" in reasons[0], (name, st, reasons)


def test_a_late_but_complete_capture_is_still_a_deadline_fail(tmp_path):
    """late:15 on stress-saw: complete evidence, frames missed (32 on the
    revision-13 re-capture, the first at 539)."""
    files = _capture(tmp_path, LATE15)
    res, st, reasons, dl = _judge(files)
    assert res["evidence"] == [] and st == 1 and dl and "missed" in reasons[0]
