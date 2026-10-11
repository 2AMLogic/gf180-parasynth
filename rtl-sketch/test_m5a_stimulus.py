from __future__ import annotations

import pathlib

import pytest

import verify_synth_top as top

MANIFEST = pathlib.Path(__file__).resolve().parents[1] / "docs/scorecard/mono-m5a-miniv3/manifest.json"


def test_m5a_smoke_writes_selected_pulse29_through_the_register_image():
    writes, _tail, report = top.m5a_script(str(MANIFEST), smoke=True,
                                           pulse_shape="pulse29")
    pulse_writes = [w for w in writes if w[3] == top.A.A_WAVE]
    assert report["pulse_shape"] == "pulse29"
    assert any(w[4] == top.WAVE_CODE["pulse29"] for w in pulse_writes)
    assert report["events"] and {event["wave"] for event in report["events"]} == {"saw", "pulse"}


def test_m5a_stimulus_refuses_waveforms_without_a_supported_pulse_duty():
    with pytest.raises(ValueError, match="supported rectangular shape"):
        top.m5a_script(str(MANIFEST), smoke=True, pulse_shape="tri")


def test_m5a_saw_candidate_settings_are_written_over_spi_and_pulse_is_restored():
    writes, _tail, report = top.m5a_script(
        str(MANIFEST), smoke=True, pulse_shape="pulse29",
        saw_cutoff_hz=20_000, saw_volume_correction_db=-0.45428)
    controls = [w[3:] for w in writes
                if w[3] in (top.A.A_CUT_LO, top.A.A_CUT_HI, top.A.A_VOL)]
    base = top.vf.VoiceFx.patch_regs(cutoff=(14_073, 14_073), vol=0.45)
    scale = 10 ** (-0.45428 / 20.0)
    saw = top.vf.VoiceFx.patch_regs(cutoff=(20_000, 20_000), vol=0.45 * scale)
    assert controls[-6:] == [
        (top.A.A_CUT_LO, saw["cut_lo"]), (top.A.A_CUT_HI, saw["cut_hi"]),
        (top.A.A_VOL, saw["vol"]),
        (top.A.A_CUT_LO, base["cut_lo"]), (top.A.A_CUT_HI, base["cut_hi"]),
        (top.A.A_VOL, base["vol"]),
    ]
    assert report["saw_cutoff_hz"] == 20_000
    assert report["saw_volume_correction_db"] == pytest.approx(-0.45428)


@pytest.mark.parametrize("kwargs", [
    {"saw_cutoff_hz": 30_000},
    {"saw_volume_correction_db": 13.0},
    {"saw_volume_correction_db": float("nan")},
])
def test_m5a_stimulus_refuses_unsupported_saw_overrides(kwargs):
    with pytest.raises(ValueError, match="saw (cutoff|volume correction)"):
        top.m5a_script(str(MANIFEST), smoke=True, **kwargs)


# ---- #372: the schedule lands on the case's timing map ---------------------
#
# The link timing this scheduler relies on (tb_top_bx_timing) is checked
# against the bench's OWN write logs stored under docs/deadline/traces, and the
# schedule is checked by tools/check_phrase_schedule.py, which computes its
# targets from the manifest without consulting the scheduler.

import gzip
import hashlib
import json
import shutil
import sys

import tb_top_bx_timing as link

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import check_phrase_schedule as cps                                   # noqa: E402

M5B = ROOT / "docs/scorecard/mono-m5b-miniv3/manifest.json"


def _bench_traces():
    out = []
    for d in sorted((ROOT / "docs/deadline/traces").iterdir()):
        cmds, wrs = d / "top_bx_cmds.txt.gz", sorted(d.glob("top_wrs_*.txt.gz"))
        if cmds.exists() and len(wrs) == 1:
            waits = [int(l.split()[0]) for l in gzip.open(cmds, "rt") if l.strip()]
            pred = [int(l.split()[5]) for l in gzip.open(wrs[0], "rt") if l.strip()]
            out.append((d.name, waits, pred))
    return out


def test_link_timing_reproduces_every_stored_bench_trace():
    traces = _bench_traces()
    assert len(traces) >= 18 and sum(len(p) for _, _, p in traces) >= 6000
    for name, waits, pred in traces:
        assert link.landing_frames(waits) == pred, name


@pytest.mark.parametrize("name,value", [
    ("FIRST_TICK_EDGE", link.FIRST_TICK_EDGE + 1), ("FIRST_TICK_EDGE", link.FIRST_TICK_EDGE - 1),
    ("WRITE_PS", link.WRITE_PS + 1_000_000), ("PIN_TO_PUSH_EDGES", 4),
    ("CSN_RISE_PS", link.CSN_RISE_PS + 600_000),
])
def test_a_wrong_link_timing_misses_the_stored_traces(monkeypatch, name, value):
    """Start red for the timing model: each constant, off by one edge or a
    fraction of a write, contradicts the bench somewhere."""
    monkeypatch.setattr(link, name, value)
    assert any(link.landing_frames(w) != p for _, w, p in _bench_traces())


# The two simulator races the model refuses to resolve (Judge note on #647: the
# `Race` case was never constructed). Each is built through `landing_frames`
# by moving the start so the event falls EXACTLY on the edge, with a 1 ps
# control that must NOT raise -- otherwise "raises" could mean "always raises".
def test_a_csn_rise_exactly_on_a_posedge_is_a_race(monkeypatch):
    k = 1000
    monkeypatch.setattr(link, "START_PS", link.edge_time(k) - link.CSN_RISE_PS)
    with pytest.raises(link.Race, match="CS_N rises exactly on posedge"):
        link.landing_frames([0])
    monkeypatch.setattr(link, "START_PS", link.edge_time(k) - link.CSN_RISE_PS + 1)
    link.landing_frames([0])


def test_a_wait_called_exactly_on_a_tick_edge_is_a_race(monkeypatch):
    monkeypatch.setattr(link, "START_PS", link.tick_time(0))
    with pytest.raises(link.Race, match="exactly on a tick edge"):
        link.landing_frames([1])
    monkeypatch.setattr(link, "START_PS", link.tick_time(0) + 1)
    link.landing_frames([1])


def test_the_published_bench_start_is_not_a_race():
    """The real START_PS with a zero wait and a one-frame wait resolves."""
    assert link.landing_frames([0, 1, 0, 2])


@pytest.mark.parametrize("manifest,periods", [(MANIFEST, 1_305_543), (M5B, 729_543)])
def test_legacy_schedule_reproduces_the_stored_confirm_run_length(manifest, periods):
    """docs/scorecard/m5-artifacts-333/saw-drive (confirm/M5A.txt): the
    published scheduler at saw drive 0.35 ran M5A for 1,305,543 I2S periods
    and M5B for 729,543 -- the bench's numbers, which the link timing must
    reproduce for the old waits before its numbers for the new ones mean
    anything."""
    w, tail, _ = top.m5a_script(str(manifest), saw_drive=0.35, schedule="legacy-relative")
    assert max(link.landing_frames([c[0] for c in w])) + tail + 1 == periods


ORDER_SHA = {   # (flag, sec, addr, data) of every write, recorded before #372
    (MANIFEST, ()): "5dc521e06dac84aa",
    (MANIFEST, (("saw_drive", 0.35),)): "7c81fc8c080c4e18",
    (MANIFEST, (("saw_cutoff_hz", 20_000), ("saw_volume_correction_db", 5.22576))): "f1d88c21c460f92b",
    (M5B, ()): "0ea2d89f13aa0b01",
    (M5B, (("saw_drive", 0.35),)): "fd9f5673c51a44ac",
    (M5B, (("saw_cutoff_hz", 20_000), ("saw_volume_correction_db", 5.22576))): "b649d13e9b862db0",
}


def _log(writes):
    frames = link.landing_frames([c[0] for c in writes])
    return [[f, *c[1:], f] for f, c in zip(frames, writes)]


@pytest.mark.parametrize("key", list(ORDER_SHA))
@pytest.mark.parametrize("schedule", ["absolute", "legacy-relative"])
def test_schedule_changes_only_waits_and_lands_on_the_map(key, schedule):
    manifest, kw = key
    w, tail, rep = top.m5a_script(str(manifest), schedule=schedule, **dict(kw))
    seq = json.dumps([list(c[1:]) for c in w])
    assert hashlib.sha256(seq.encode()).hexdigest()[:16] == ORDER_SHA[key]
    res = cps.judge(cps.targets(json.loads(pathlib.Path(manifest).read_text())), _log(w))
    if schedule == "absolute":
        assert res["verdict"] == "PASS", res["violations"]
        audio = round(rep["audio_duration_s"] * 48_000)
        assert max(f for f, *_ in _log(w)) + tail + 1 == audio + 1
    else:
        assert res["verdict"] == "FAIL"


def _held_out(tmp_path, segments):
    """A copy of M5A's manifest with a different event pattern, next to the
    same reference audio (m5a_script checks its hash)."""
    m = json.loads(MANIFEST.read_text())
    for seg, events in zip(m["timeline"]["segments"], segments):
        seg["midi_events"] = [dict(seg["midi_events"][0], note=n, on_s=on, gate_s=g)
                              for n, on, g in events]
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(m))
    ref = MANIFEST.parent / m["audio"]["file"]
    (tmp_path / m["audio"]["file"]).symlink_to(ref)
    return path, m


def test_held_out_tight_spacing_and_a_waveform_boundary_land_exactly(tmp_path):
    """Not the pattern the scheduler was repaired on: three notes 12 frames
    apart (release to next gate), a release 6 frames before the saw segment
    ends, and the pulse segment's first gate 2 ms after its boundary."""
    f = 1 / 48_000
    seg0 = [(60, 0.1, 0.5), (72, 0.6 + 12 * f, 0.25), (84, 0.85 + 24 * f, 0.3),
            (96, 13.4, (647_950 - 643_200) * f)]
    seg1 = [(84, 0.002, 0.01), (96, 0.012 + 12 * f, 0.6)]
    path, m = _held_out(tmp_path, [seg0, seg1])
    for kw in ({}, {"saw_drive": 0.35}):
        w, _tail, _ = top.m5a_script(str(path), **kw)
        res = cps.judge(cps.targets(m), _log(w))
        assert res["verdict"] == "PASS", res["violations"]
        assert res["events"] == 6


def test_an_infeasible_spacing_is_refused_not_moved(tmp_path):
    f = 1 / 48_000
    path, _ = _held_out(tmp_path, [[(60, 0.1, 0.5), (72, 0.6 + 4 * f, 0.25)],
                                   [(84, 0.1, 0.6)]])
    with pytest.raises(ValueError, match="infeasible"):
        top.m5a_script(str(path))


def test_smoke_schedule_lands_on_its_own_map():
    w, tail, rep = top.m5a_script(str(MANIFEST), smoke=True)
    frames = link.landing_frames([c[0] for c in w])
    on = [f for f, c in zip(frames, w) if c[3] == top.A.A_GATE_ON]
    off = [f for f, c in zip(frames, w) if c[3] == top.A.A_GATE_OFF]
    # hand-derived: segments at 0 and round(0.050 * 48000) = 2400, events at
    # 5 and 20 ms for 10 ms, less the one period of I2S delay
    assert on == [239, 959, 2639, 3359]
    assert off == [719, 1439, 3119, 3839]
    assert max(frames) + tail + 1 == 4801


def test_unknown_schedule_and_stray_margin_are_refused():
    with pytest.raises(ValueError, match="unknown M5 schedule"):
        top.m5a_script(str(MANIFEST), smoke=True, schedule="relative")
    with pytest.raises(ValueError, match="legacy_margin"):
        top.m5a_script(str(MANIFEST), smoke=True, legacy_margin=22)
