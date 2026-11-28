#!/usr/bin/env python3
"""fpga/test_measure_mac_midi_latency.py -- the latency apparatus's known answer (#322).

fpga/measure_mac_midi_latency.py computes receipt -> applied itself, because
T-LIVE-MIDI's check() cannot follow a real clock's re-anchoring. So its
pairing and endpoint arithmetic must first reproduce check()'s -- which pairs
through the independently built oracle -- on SIMULATED time, where both see
the same run. And its one control (a host stall) must move `on_time`.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import measure_mac_midi_latency as mml                     # noqa: E402


def test_the_probe_reproduces_check_latency_on_simulated_time():
    mine, ref = mml.sim_reference(5.0)
    assert ref["verdict"] == "PASS", ref["reasons"]
    theirs = ref["latency"]
    assert mine["endpoint_ms"]["n"] == theirs["n"] > 200
    for k in ("min", "p50", "p95", "p99", "max"):
        assert abs(mine["endpoint_ms"][k] - theirs[k + "_ms"]) < 1e-9, k
    assert mine["props"] == {"target": True, "all_delivered": True, "on_time": True,
                             "load_admitted": True}
    assert mine["accounting_complete"] and mine["lost"] == 0
    assert mine["anchors_unpaired"] == 0
    assert max(map(abs, mine["map_error_frames"].values())) <= 1   # check()'s own tolerance


def test_one_host_stall_moves_on_time_on_simulated_time():
    """The control, deterministic: the same stall the Mac run injects."""
    mine, _ref = mml.sim_reference(5.0, stall_ms=25.0)
    assert not mine["props"]["on_time"]
    assert mine["anchors_off_due"] or mine["device_errors"] or mine["deadline_misses"]


# ---- host overload (timing contract 3): reported, rejected where defined, recovered --
import pytest                                               # noqa: E402

import live_midi_contract as C                              # noqa: E402


@pytest.mark.parametrize("stall_ms", [25.0, 250.0, 1000.0])
def test_a_host_stall_loses_nothing_and_recovers_at_once(stall_ms):
    """Every offered message is accounted for and none is lost. Packets that
    left late were re-dated and counted, so the device reports no error. Notes
    reaching the host more than STALE_MS late are refused as `stale`; nothing
    else is. No message received after the stall ends misses the target."""
    r, _ = mml.sim_reference(8.0, stall_ms=stall_ms)
    assert r["accounting_complete"] and r["lost"] == 0, r["accounting"]
    assert r["deadline_misses"] >= 1 and r["device_errors"] == []
    stale = r["accounting"].get("refused: stale", 0)
    assert (stale > 0) == (stall_ms > C.STALE_MS)
    assert r["stall"]["over_target_after_end"] == 0
    # no note or drum hit sounds more than STALE_MS (plus the lookahead) late
    worst = max(r["endpoint_by_kind_max_ms"].get(k, 0.0) for k in ("note-on", "hit"))
    assert worst <= C.STALE_MS + C.LOOKAHEAD_MS + 1.0, r["endpoint_by_kind_max_ms"]


def test_control_no_redate_a_long_stall_loses_events():
    """Re-dating removed (a late packet sent with its stale due): the same 1 s
    stall LOSES events -- the defect measured before the repair."""
    r, _ = mml.sim_reference(8.0, stall_ms=1000.0, inject={"NO_REDATE"})
    assert r["accounting_complete"] and r["lost"] > 0, r["accounting"]


def test_control_no_stale_notes_sound_long_after_the_key():
    """Stale refusal removed: notes and hits received during a 1 s stall are
    played up to a second late instead of refused."""
    r, _ = mml.sim_reference(8.0, stall_ms=1000.0, inject={"NO_STALE"})
    worst = max(r["endpoint_by_kind_max_ms"].get(k, 0.0) for k in ("note-on", "hit"))
    assert worst > C.STALE_MS + C.LOOKAHEAD_MS + 1.0, r["endpoint_by_kind_max_ms"]


def test_a_stale_note_is_refused_but_its_note_off_and_a_knob_are_delivered():
    import midi_session as ms
    import uart_device_sim as dev
    clock = dev.SimClock()
    sim = dev.UartDeviceSim(clock=clock)
    s = ms.MidiSession(dev.SimSerial(sim), clock=clock, image="tree")
    s.start()
    t_old = clock.t
    clock.sleep(0.2)                                         # the loop stalled 200 ms
    s.feed(t_old, bytes([0x90, 60, 100, 0xB0, 74, 64]))
    assert [r.category for r in s.refusals] == ["stale"]
    assert s.stats["groups"].get("knob") == 1 and "note-on" not in s.stats["groups"]
    s.feed(clock.t, bytes([0x90, 62, 100]))                  # fresh: played
    assert s.stats["groups"].get("note-on") == 1


def test_a_note_handed_over_late_after_a_kick_is_not_dropped():
    """#339: the device DROPS an event whose due is before the last one it
    queued. A kick's timed tail (the BD attack window) is sent with a due
    ~190 frames after its anchor. A note received 1 ms after the kick but
    handed over 11 ms later (host hold; not late, not stale) is scheduled
    from its receipt -- BEFORE that already-sent tail. The host holds it at
    the last sent due and counts it; the device drops nothing. Control
    NO_ORDER_GUARD: the device drops every packet of the note (ERR 3)."""
    import midi_session as ms
    import synth_top_model as stm
    import uart_device_sim as dev

    def run(inject):
        clock = dev.SimClock()
        sim = dev.UartDeviceSim(clock=clock)
        s = ms.MidiSession(dev.SimSerial(sim), clock=clock, image="tree", inject=inject)
        s.start()
        t = clock.t + 0.01
        s.service(t)
        s.feed(t, bytes([0x99, 36, 100]))               # the kick, tail included
        s.service(t + 0.012)
        s.feed(t + 0.001, bytes([0x90, 60, 100]))       # received t+1 ms, handed over t+12 ms
        s.service(clock.t + 0.1)
        s.close()
        on = [w for w in sim.writes if w[5] == "event" and w[3] == stm.A_GATE_ON]
        return s, sim, on
    s, sim, on = run(set())
    assert s.stats["order_guarded"] >= 1 and s.stats["deadline_misses"] == 0
    assert not sim.errors and sim.drops == 0 and len(on) == 1
    s, sim, on = run({"NO_ORDER_GUARD"})
    assert s.stats["order_guarded"] == 0 and not on
    assert [e for e in sim.errors if e[0] == 3]


# ---- CLI help and host preconditions must work without the voice model --
_BLOCK_NUMPY = ("import sys; sys.modules['numpy'] = None; import runpy; "
                "sys.argv = sys.argv[1:]; runpy.run_path(sys.argv[0], run_name='__main__')")


def _cli_without_numpy(*args, setup=""):
    return subprocess.run([sys.executable, "-ISB", "-c", setup + "\n" + _BLOCK_NUMPY,
                           os.path.join(HERE, "measure_mac_midi_latency.py"), *args],
                          check=False, capture_output=True, text=True, timeout=60)


def test_help_works_with_numpy_blocked():
    """The script's public path in a fresh interpreter, NumPy blocked in the
    child so an installed package or pytest's import cache cannot hide it."""
    r = _cli_without_numpy("--help")
    assert r.returncode == 0, r.stderr[-800:]
    assert "--source" in r.stdout and "--control-stall-ms" in r.stdout


@pytest.mark.parametrize("source,reason", [
    pytest.param("coremidi", f"--source coremidi needs macOS (this is {sys.platform})",
                 marks=pytest.mark.skipif(sys.platform == "darwin", reason="CoreMIDI is supported")),
    ("direct", "load average 9.0 on 8 CPUs"),
])
def test_preflight_refuses_without_numpy(source, reason, tmp_path):
    report = tmp_path / "latency.json"
    busy_host = """\
import os
os.getloadavg = lambda: (9.0, 0.0, 0.0)
os.cpu_count = lambda: 8
"""
    r = _cli_without_numpy("--source", source, "--json", str(report), setup=busy_host)
    assert r.returncode == 2, r.stderr
    assert r.stdout.startswith(f"mac-latency: REFUSED -- {reason}")
    assert r.stderr == ""
    assert not report.exists()


# ---- a drifted frozen kit is a precondition: REFUSED (exit 2), never FAIL (#1645) --
import json                                                  # noqa: E402

import uart_host as uh                                       # noqa: E402

_RUN_SCRIPT = ("import runpy, sys; sys.argv = sys.argv[1:]; "
               "runpy.run_path(sys.argv[0], run_name='__main__')")
_DRIFT = {"kit": f"not R1's frozen kit {uh.R1_KIT_SHA256[:12]}",
          "presets": f"not R1's frozen sound presets {uh.R1_PRESETS_SHA256[:12]}"}


def _cli(*args, setup: str = ""):
    """The script's public path in a fresh interpreter with NumPy available
    (the measurement needs it); `setup` runs first in that interpreter."""
    return subprocess.run([sys.executable, "-B", "-c", setup + "\n" + _RUN_SCRIPT,
                           os.path.join(HERE, "measure_mac_midi_latency.py"), *args],
                          check=False, capture_output=True, text=True, timeout=120)


def _drifted_kit(tmp_path, drift: str):
    """A copy of fpga/release/r1-kit.json with only `drift` moved: `kit`
    flips the first kit write, `presets` one value of the LT position."""
    rec = json.loads(uh.R1_KIT.read_text())
    if drift == "kit":
        rec["writes"][0][1] ^= 1
    else:
        rec["presets"]["LT"][0][1] ^= 1
    p = tmp_path / "r1-kit.json"
    p.write_text(json.dumps(rec))
    return p


@pytest.mark.parametrize("drift,reason", sorted(_DRIFT.items()))
def test_a_drifted_frozen_kit_is_refused_not_failed(drift, reason, tmp_path):
    """#1645, through the real CLI (__main__ -> main): the session and its
    reference both read R1's frozen kit, so a drifted kit or sound table is
    a precondition of the measurement. Exit 2 with the KitRefused reason in
    each run's REFUSED line and no traceback. Before the repair: exit 1 by
    traceback, before anything was printed."""
    kit = _drifted_kit(tmp_path, drift)
    report = tmp_path / "latency.json"
    setup = (f"import pathlib, sys; sys.path.insert(0, {HERE!r}); import uart_host as uh; "
             f"uh.R1_KIT = pathlib.Path({str(kit)!r})")
    r = _cli("--source", "direct", "--seconds", "1", "--control-seconds", "1",
             "--allow-loaded", "--json", str(report), setup=setup)
    assert r.returncode == 2, r.stderr[-800:]
    assert "Traceback" not in r.stderr, r.stderr[-800:]
    refused = [line for line in r.stdout.splitlines() if ": REFUSED -- " in line]
    assert [line.split(":")[0] for line in refused] == [
        "mac-latency[clean]", "mac-latency[control stall 25 ms]"], r.stdout
    for line in refused:
        assert f"KitRefused: {kit.name}" in line and reason in line, line
    rec = json.loads(report.read_text())
    assert rec["verdict"] == "REFUSED"
    assert rec["clean"]["preconditions"] and rec["control"]["preconditions"]


@pytest.mark.parametrize("drift,reason", sorted(_DRIFT.items()))
def test_sim_reference_reports_a_drifted_kit_as_no_verdict(drift, reason, tmp_path, monkeypatch):
    """The other construction site (#1645): the known-answer reference cannot
    be built from a drifted kit and says so the way check() does -- NO
    VERDICT with the precondition named -- instead of raising KitRefused into
    the caller. The probe record carries the same precondition and no
    property a test could mistake for a result."""
    monkeypatch.setattr(uh, "R1_KIT", _drifted_kit(tmp_path, drift))
    mine, ref = mml.sim_reference(1.0)
    assert ref["verdict"] == "NO VERDICT"
    why = ref["preconditions"][0]
    assert len(ref["preconditions"]) == 1 and ref["reasons"] == ref["preconditions"]
    assert "KitRefused: r1-kit.json" in why and reason in why
    assert mine == {"preconditions": ref["preconditions"], "props": {}}


def test_control_an_unrelated_error_at_the_kit_read_is_not_a_refusal(tmp_path, monkeypatch):
    """The catch is narrow -- `except (uh.Refused, dx.KitRefused)`, never
    `except Exception`. A RuntimeError that is not a KitRefused, raised at
    the same read (uart_host.image_kit, where the refusal comes from), still
    propagates from both construction sites; through the CLI that is exit 1
    with a traceback, as before the repair."""
    def boom(image=uh.DEFAULT_IMAGE):
        raise RuntimeError("boom")
    monkeypatch.setattr(uh, "image_kit", boom)
    calls = [lambda: mml.measure("direct", 0.5),
             lambda: mml.main(["--source", "direct", "--seconds", "0.5",
                               "--control-seconds", "0.5",
                               "--json", str(tmp_path / "j.json")]),
             lambda: mml.sim_reference(0.5)]
    for call in calls:
        with pytest.raises(RuntimeError, match="boom"):
            call()
    setup = (f"import sys; sys.path.insert(0, {HERE!r}); import uart_host as uh\n"
             "def boom(image=uh.DEFAULT_IMAGE): raise RuntimeError('boom')\n"
             "uh.image_kit = boom")
    r = _cli("--source", "direct", "--seconds", "1", "--control-seconds", "1",
             "--allow-loaded", "--json", str(tmp_path / "latency.json"), setup=setup)
    assert r.returncode == 1, (r.returncode, r.stdout[-400:], r.stderr[-400:])
    assert "Traceback" in r.stderr and "RuntimeError: boom" in r.stderr


def test_the_undrifted_kit_still_measures():
    """Control for the guard: with R1's frozen kit as shipped, measure()
    reaches its analysis on the real clock -- no precondition, the four
    properties present, anchors paired."""
    rec = mml.measure("direct", 0.5)
    assert rec["preconditions"] == []
    assert set(rec["props"]) == {"target", "all_delivered", "on_time", "load_admitted"}
    assert rec["endpoint_ms"]["n"] > 0
