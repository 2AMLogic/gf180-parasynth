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
    # #333: the candidate skip and its negative control differ only in the predicate
    cand = open(os.path.join(vd.make_mutant("skip2xwin", str(tmp_path)), "voice_dp.v")).read()
    ctl = open(os.path.join(vd.make_mutant("skipallwin", str(tmp_path)), "voice_dp.v")).read()
    assert "if (!blep || (use_osc2x && shape_osc2x)) state <= S_MIX;" in cand
    assert "if (1'b1) state <= S_MIX;" in ctl and "if (!blep) state <= S_MIX;" not in ctl


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
RUNS = os.path.join(ROOT, "docs", "deadline", "runs")
# A capture is judged against the stimulus that DROVE it (its own
# top_bx_cmds.txt), and _capture admits it as THIS tree's evidence only if
# verify_deadline.stimulus_binding says that stimulus and the one the scenario
# builds now agree on everything that can move the schedule (#443: every byte
# except drum parameter values, under asserted preconditions -- the argument is
# at stimulus_binding). The retained `prod-stress-saw` /
# `ctl-prod-stress-late15` captures (2221d2c) predate contract revision 14,
# whose kit has one more write (461 vs 460), and stay in the tree as history;
# these tests use the `-l2` re-captures (docs/deadline/runs/*-l2.json).
CLEAN, LATE15 = "prod-stress-saw-l2", "ctl-prod-stress-late15-l2"


def _capture(tmp_path, record, scenario="stress-saw"):
    import json
    d = tmp_path / record
    d.mkdir()
    for gz in os.listdir(os.path.join(TRACE, record)):
        with gzip.open(os.path.join(TRACE, record, gz), "rb") as src, open(d / gz[:-3], "wb") as dst:
            shutil.copyfileobj(src, dst)
    wrs = [f for f in os.listdir(d) if f.startswith("top_wrs_") and f.endswith(".txt")][0]
    i2s = [f for f in os.listdir(d) if f.startswith("top_i2s_")][0]
    files = dict(i2s=str(d / i2s), wrs=str(d / wrs), sched=str(d / (wrs + ".sched")))
    captured = vd.read_cmds(str(d / "top_bx_cmds.txt"))
    current, _, _ = vd.SPI_SCENARIOS[scenario](False)
    kind, why = vd.stimulus_binding(captured, current, sched_rows=vd.read_sched(files["sched"])[0],
                                    record=json.load(open(os.path.join(RUNS, record + ".json"))))
    assert kind != "refused", (
        f"{record} was not driven by this tree's {scenario} stimulus ({'; '.join(why)}): "
        "re-capture it (tools/deadline_recapture_l2.py), do not judge historical evidence "
        "against current source")
    files.update(cmds=captured, binding=kind)
    return files


def test_the_pre_revision_14_captures_are_history_not_this_trees_stimulus(tmp_path):
    """The control for _capture's precondition: the retained pre-L2 capture is
    refused as this tree's evidence (it stays in the tree, unmodified)."""
    with pytest.raises(AssertionError, match="not driven by this tree"):
        _capture(tmp_path, "prod-stress-saw")


def _judge(files, scenario="stress-saw", overflow=0):
    """Judged against the capture's OWN stimulus: a drum value the binding
    admitted must not then count as a corrupted write."""
    _, tail, meta = vd.SPI_SCENARIOS[scenario](False)
    res = vd.evaluate_spi(files, files["cmds"], tail, meta, osc2x=True, filter2x=True, pulse2x=False,
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
    # 2158 on the revision-14 re-capture; the pre-L2 capture required 2156
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
    revision-14 re-capture, the first at 539)."""
    files = _capture(tmp_path, LATE15)
    res, st, reasons, dl = _judge(files)
    assert res["evidence"] == [] and st == 1 and dl and "missed" in reasons[0]


# ---- #443: the binding is the schedule-relevant projection, with controls ------------
PRE_426 = {(1, 0x79): 5754585, (1, 0x9F): 14711203}     # the two values #426 moved


def _tree_stimulus(monkeypatch, edit, scenario="stress-saw"):
    """Make the tree's scenario build an edited stimulus; the committed capture
    stays as it is."""
    real = vd.SPI_SCENARIOS[scenario]

    def patched(short):
        cmds, tail, meta = real(short)
        return edit([list(c) for c in cmds]), tail, meta
    monkeypatch.setitem(vd.SPI_SCENARIOS, scenario, patched)


def _set(pred, fn):
    def edit(L):
        k = next(i for i, c in enumerate(L) if pred(c))
        L[k] = fn(list(L[k]))
        return [tuple(c) for c in L]
    return edit


def _revert_426(L):
    for c in L:
        if (c[2], c[3]) in PRE_426:
            c[4] = PRE_426[(c[2], c[3])]
    return [tuple(c) for c in L]


def _with(i, v):
    def fn(c):
        c[i] = v(c[i]); return c
    return fn


def _drum_param(c):
    return c[2] == vd.SEC_D and c[3] == 0x79


def test_the_426_value_only_change_is_admitted_and_the_capture_still_passes(tmp_path, monkeypatch):
    """#426 in reverse: the tree builds the two pre-#426 drum values, the
    capture holds the new ones. The re-capture's schedule trace was
    byte-identical to the stale one's (`tools/deadline_binding_probe.py
    --history 8c3de22 a224050`: 2 command lines, 0 schedule lines, 997 I2S
    periods differ), so this capture is admitted -- and judged against its OWN
    stimulus, so the moved values are not counted as corrupted writes."""
    _tree_stimulus(monkeypatch, _revert_426)
    files = _capture(tmp_path, CLEAN)
    assert files["binding"] == "drum-values"
    res, st, reasons, dl = _judge(files)
    assert st == 0 and res["writes_bad"] == 0 and res["periods"] == res["periods_required"] == 2158
    (tmp_path / "late").mkdir()
    late = _capture(tmp_path / "late", LATE15)
    assert late["binding"] == "drum-values"
    res, st, reasons, dl = _judge(late)
    assert st == 1 and dl and "missed" in reasons[0]          # the negative control still bites


# The CONTROLS: each edit below can move the schedule or a verdict fact, and the
# binding must refuse it on the real capture. A binding that admitted any of
# these would be worse than the byte-exact one it replaced.
REFUSED_EDITS = {
    # THE control the issue asks for: a register VALUE that moves the schedule.
    # Two octaves on the first note's increments: PolyBLEP windows open four
    # times as often (tools/deadline_binding_probe.py `voice-inc` runs it
    # through the SPI bench and must see the schedule move, or it is NO VERDICT)
    "voice-inc-value": _set(lambda c: c[2] == vd.SEC_V and c[3] == vd.stm.A_INC,
                            _with(4, lambda d: min(d * 4, 0xFFFFFF))),
    # a voice value the schedule may or may not see -- bound anyway: the voice
    # page is not classified register by register
    "voice-vol-value": _set(lambda c: c[2] == vd.SEC_V and c[3] == vd.stm.A_VOL, _with(4, lambda d: d ^ 1)),
    # a drum-page value the verdict's coverage reads
    "drum-stops-value": _set(lambda c: c[2] == vd.SEC_D and c[3] == vd.dx.A_STOPS and c[4],
                             _with(4, lambda d: d ^ 1)),
    "drum-param-landing-frame": _set(_drum_param, _with(0, lambda w: w + 1)),
    "drum-param-address": _set(_drum_param, _with(3, lambda a: a + 4)),
    "drum-param-flag": _set(_drum_param, _with(1, lambda f: f ^ 1)),
    "drum-param-section": _set(_drum_param, _with(2, lambda s: vd.SEC_V)),
    "write-dropped": lambda L: [tuple(c) for c in L[:149] + L[150:]],
    "write-added": lambda L: [tuple(c) for c in L[:150] + [[0, 0, vd.SEC_D, 0x79, 1]] + L[150:]],
    "writes-swapped": lambda L: [tuple(c) for c in L[:149] + [L[150], L[149]] + L[151:]],
}


@pytest.mark.parametrize("name", sorted(REFUSED_EDITS))
def test_a_schedule_relevant_stimulus_change_is_still_refused(tmp_path, monkeypatch, name):
    _tree_stimulus(monkeypatch, REFUSED_EDITS[name])
    cmds = vd.SPI_SCENARIOS["stress-saw"](False)[0]
    with gzip.open(os.path.join(TRACE, CLEAN, "top_bx_cmds.txt.gz"), "rt") as fh:
        assert cmds != [tuple(int(x) for x in ln.split()) for ln in fh if ln.strip()]   # the edit landed
    with pytest.raises(AssertionError, match="not driven by this tree"):
        _capture(tmp_path, CLEAN)


def test_the_relaxation_is_refused_when_its_premise_is_not_asserted(tmp_path, monkeypatch):
    """The drum-value relaxation stands on an argument made on specific drum
    RTL: a different drum section (in the tree or in the capture's record), or
    a capture whose drum busy window varies, falls back to byte-exact."""
    _tree_stimulus(monkeypatch, _revert_426)
    monkeypatch.setitem(vd.DRUM_LATENCY_ARGUED_AT, "drum_dp.v", "000000000000")
    with pytest.raises(AssertionError, match="latency argument"):
        _capture(tmp_path, CLEAN)
    monkeypatch.undo()

    import json
    cur, _, _ = vd.SPI_SCENARIOS["stress-saw"](False)
    new = _revert_426([list(c) for c in cur])
    rec = json.load(open(os.path.join(RUNS, CLEAN + ".json")))
    rows = [dict(r) for r in _committed_sched(CLEAN)]
    assert vd.stimulus_binding(cur, new, sched_rows=rows, record=rec)[0] == "drum-values"
    bad_rec = json.loads(json.dumps(rec)); bad_rec["provenance"]["files"]["modal_dp.v"] = "0" * 12
    kind, why = vd.stimulus_binding(cur, new, sched_rows=rows, record=bad_rec)
    assert kind == "refused" and "run record" in why[0]
    rows[700]["d_last"] += 1
    kind, why = vd.stimulus_binding(cur, new, sched_rows=rows, record=rec)
    assert kind == "refused" and "drum busy windows" in why[0]
    assert vd.stimulus_binding(cur, new, sched_rows=[], record=rec)[0] == "refused"


def test_the_drum_rtl_is_the_rtl_the_argument_was_made_on():
    """Pinned so a drum-section change is SEEN (the relaxation silently turning
    off would make every drum value change red again, with no reason given)."""
    assert vd.drum_rtl_hashes() == vd.DRUM_LATENCY_ARGUED_AT


def test_the_projection_masks_exactly_drum_parameter_data():
    cmds = [(3, 1, vd.SEC_V, 0x20, 7), (0, 0, vd.SEC_D, vd.dx.A_STOPS, 5), (0, 0, vd.SEC_D, 0x79, 9),
            (2, 0, vd.SEC_D, 0xFF, 0)]
    assert vd.schedule_projection(cmds) == [(3, 1, vd.SEC_V, 0x20, 7), (0, 0, vd.SEC_D, 0, 5),
                                            (0, 0, vd.SEC_D, 0x79, None), (2, 0, vd.SEC_D, 0xFF, None)]


def _committed_sched(record):
    import tempfile
    with gzip.open(os.path.join(TRACE, record, "top_wrs_VOICE_OSC_2X_VOICE_FILTER_2X.txt.sched.gz"),
                   "rt") as fh, tempfile.NamedTemporaryFile("w", suffix=".sched", delete=False) as t:
        t.write(fh.read())
    try:
        rows, problems = vd.read_sched(t.name)
    finally:
        os.remove(t.name)
    assert not problems
    return rows


def test_the_controls_can_fail(tmp_path, monkeypatch):
    """Injected defects in the binding itself, each of which must let a control
    edit through (so the refusals above are the binding's, not an accident of
    the edit). Measured out of process too: masking every value, masking STOPS,
    dropping the waits, and skipping the preconditions each turned 1-4 of the
    tests above red."""
    cur, _, _ = vd.SPI_SCENARIOS["stress-saw"](False)
    inc = REFUSED_EDITS["voice-inc-value"]([list(c) for c in cur])
    rec = __import__("json").load(open(os.path.join(RUNS, CLEAN + ".json")))
    rows = _committed_sched(CLEAN)
    assert vd.stimulus_binding(cur, inc, sched_rows=rows, record=rec)[0] == "refused"
    monkeypatch.setattr(vd, "schedule_projection", lambda cmds: [c[:4] for c in cmds])   # values ignored
    assert vd.stimulus_binding(cur, inc, sched_rows=rows, record=rec)[0] == "drum-values"
