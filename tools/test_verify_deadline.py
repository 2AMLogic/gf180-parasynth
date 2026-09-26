"""The production-deadline driver's arithmetic and verdicts, on schedule rows
whose answer is known by construction (plan075 T1, docs/deadline/README.md).

The simulation itself is exercised by rtl-sketch/verify_deadline.py and its
VOICE_LATE_DONE control; these tests pin the part that turns monitor rows into
a verdict, because a deadline checker's only failure that matters is a false
green."""
import os
import re
import sys

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
                win=0, w1=0, im1=0, sk=0, ywait=0)


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
