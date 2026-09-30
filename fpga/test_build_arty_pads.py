"""fpga/build_arty_pads.py's gates, without Vivado (#449).

What these can and cannot show: that the build REFUSES on a tampered kit ROM, a
missing/stale/injected bench record, a non-routed or failing timing report and
an undispositioned input -- and that the Tcl it emits is well-formed. They
cannot show that Vivado accepts the Tcl or that the image closes timing; that
is the build box's run."""
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _p in ("fpga", "model", "rtl-sketch"):
    sys.path.insert(0, str(ROOT / _p))

import build_arty_pads as bp    # noqa: E402
import pads_rom as pr           # noqa: E402
import xdc_bindings as xb       # noqa: E402


def test_sources_are_the_pads_wrapper_not_the_release_one():
    names = [p.name for p in bp.sources()]
    assert names[:3] == ["arty_a7_pads_top.v", "pads_seq.v", "pads_rom.v"]
    assert "arty_a7_top.v" not in names
    assert {"synth_top.v", "drum_kit.v", "drum_dp.v", "uart_bridge.v"} <= set(names)
    text = bp.tcl_script(Path("/tmp/pads test"), bp.sources())
    assert "-top arty_a7_pads_top " in text and "-top arty_a7_top " not in text
    assert "SIM_NO_MMCM=0" in text and "SIM_NO_MMCM=1" not in text
    assert "-verilog_define VOICE_OSC_2X -verilog_define VOICE_FILTER_2X" in text
    assert "arty-a7-100-pads.xdc" in text
    for step in ("route_design", "write_bitstream", "CONSTRAINT_MATCH_REFUSED",
                 "CONSTRAINT_EFFECT_REFUSED", "PADS_INPUT_REFUSED"):
        assert step in text


def test_tcl_is_well_formed():
    if not shutil.which("tclsh"):
        pytest.skip("Tcl interpreter unavailable")
    text = bp.tcl_script(Path("/tmp/pads test"), bp.sources())
    r = subprocess.run(["tclsh"], input="puts [info complete " + "{" + text + "}]",
                       capture_output=True, text=True)
    assert r.stdout.strip() == "1", r.stderr


def test_xdc_pins_and_queries():
    xdc = bp.XDC.read_text()
    # every object query has a stated match count (#315), or this raises; and
    # they are the release XDC's queries, no more and no fewer
    release = (ROOT / "fpga/boards/arty-a7-100.xdc").read_text()
    assert [q[1:] for q in xb.object_queries(xdc)] == [q[1:] for q in xb.object_queries(release)]
    want = {"{btn[0]}": "D9", "{btn[1]}": "C9", "{btn[2]}": "B9", "{btn[3]}": "B8",
            "sw_pads": "A8", "sw_reset": "A10"}
    for port, pin in want.items():
        assert f"set_property PACKAGE_PIN {pin} [get_ports {port}]" in xdc
        assert f"set_false_path -from [get_ports {port}]" in xdc
    assert "get_ports btn_reset" not in xdc
    # the release XDC is not this file and still has its reset on D9
    assert "set_property PACKAGE_PIN D9 [get_ports btn_reset]" in \
        (ROOT / "fpga/boards/arty-a7-100.xdc").read_text()


def _sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _records(tmp_path, paths):
    for sc in bp.SCENARIOS:
        d = tmp_path / sc
        d.mkdir()
        (d / "transcript.txt").write_text(f"{sc} pass")
        comp = {k: 0 for k in ("writes_bad", "extra_writes", "missing_writes",
                               "hit_frame_bad", "boot_frame_bad", "wire_mismatch", "swap",
                               "width", "errs", "collisions", "frames_no_sample",
                               "busy_at_tick", "overrun", "x_bad")}
        comp.update(periods=100, periods_required=100, writes_seen=10, presses=2)
        rec = {"state": "PASS", "exit_code": 0, "inject": None, "scenario": sc,
               "configuration": bp.CONFIG, "comparison": comp,
               "source_sha256": {str(p): _sha(p) for p in paths},
               "transcript_sha256": _sha(d / "transcript.txt")}
        (d / "verification.json").write_text(json.dumps(rec))


def test_verification_gate(tmp_path):
    src = tmp_path / "core.v"
    src.write_text("module core; endmodule")
    with pytest.raises(ValueError, match="missing"):
        bp.validate_verification(tmp_path, [src])
    _records(tmp_path, [src])
    assert set(bp.validate_verification(tmp_path, [src])) == set(bp.SCENARIOS)
    src.write_text("module other; endmodule")
    with pytest.raises(ValueError, match="source differs"):
        bp.validate_verification(tmp_path, [src])
    src.write_text("module core; endmodule")
    path = tmp_path / "reset/verification.json"
    good = json.loads(path.read_text())
    for mutate, match in [({"inject": "PADS_TRIG_STUCK"}, "clean PASS"),
                          ({"state": "REFUSED"}, "clean PASS"),
                          ({"scenario": "phases"}, "clean PASS")]:
        path.write_text(json.dumps({**good, **mutate}))
        with pytest.raises(ValueError, match=match):
            bp.validate_verification(tmp_path, [src])
    for field, value in [("frames_no_sample", 1), ("hit_frame_bad", 1), ("periods", 99),
                         ("presses", 0)]:
        path.write_text(json.dumps({**good, "comparison": {**good["comparison"], field: value}}))
        with pytest.raises(ValueError, match="passing comparison"):
            bp.validate_verification(tmp_path, [src])
    path.write_text(json.dumps(good))
    (tmp_path / "reset/transcript.txt").write_text("another run")
    with pytest.raises(ValueError, match="transcript"):
        bp.validate_verification(tmp_path, [src])


def test_timing_summary_reads_a_real_routed_report():
    """The release image's committed UART-bridge build: the parser must read
    its numbers and refuse it as the pads design."""
    text = (ROOT / "fpga/reports/arty/uart-bridge-2025.1/timing.rpt").read_text()
    with pytest.raises(ValueError, match="not for arty_a7_pads_top"):
        bp.timing_summary(text)
    m = bp.timing_summary(text, design="arty_a7_top")
    assert m["wns_ns"] == 46.102 and m["setup_failing"] == 0 and m["setup_endpoints"] == 40510
    assert bp.timing_verdict(m) == (True, [])
    bad = {**m, "wns_ns": -0.01, "hold_failing": 2.0}
    ok, why = bp.timing_verdict(bad)
    assert not ok and any("WNS" in w for w in why) and any("hold" in w for w in why)
    assert not bp.timing_verdict({**m, "setup_endpoints": 0.0})[0]
    with pytest.raises(ValueError, match="routed"):
        bp.timing_summary(text.replace("Routed", "Placed"), design="arty_a7_top")


def test_pads_inputs_report():
    good = "".join(f"INPUT\t{p}\t1\tstage1 a; stage2 b\n" for p in bp.INPUTS) + "END\t0\n"
    assert bp.pads_inputs_report(good) == []
    assert bp.pads_inputs_report(good.replace("INPUT\tsw_reset\t1", "INPUT\tsw_reset\t0"))
    assert bp.pads_inputs_report(good.replace("END\t0\n", ""))
    assert bp.pads_inputs_report("\n".join(good.splitlines()[1:]))      # btn[0] absent


def test_tampered_kit_rom_refuses_the_build_before_vivado(tmp_path, monkeypatch):
    text = pr.ROM_V.read_text()
    bad = tmp_path / "pads_rom.v"
    bad.write_text(text.replace("8'd2: word = {1'b0, 1'b1, 8'h20, 32'h0001184E};",
                                "8'd2: word = {1'b0, 1'b1, 8'h20, 32'h0001184F};"))
    assert bad.read_text() != text
    monkeypatch.setattr(pr, "ROM_V", bad)
    out = tmp_path / "out"
    rc = bp.main(["--out", str(out), "--verification", str(tmp_path / "none"),
                  "--prepare-only", "--vivado", "/nonexistent/vivado"])
    rep = json.loads((out / "report.json").read_text())
    assert rc == 2 and rep["state"] == "REFUSED" and "kit-hash" in rep["reason"]
    assert not (out / "build.tcl").exists()
