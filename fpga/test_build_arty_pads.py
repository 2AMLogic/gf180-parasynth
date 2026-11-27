"""fpga/build_arty_pads.py's gates, without Vivado (#449).

What these can and cannot show: that the build REFUSES on a tampered kit ROM, a
missing/stale/injected bench record, a non-routed or failing timing report, a
DRC error or critical warning, a wrong core clock, a constraint that did not
bind or take effect, an unintended timing exception and an undispositioned
input -- and that the Tcl it emits is well-formed. The post-Vivado cases replay
the committed pads-demo-2025.1 reports with one defect injected each. They
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
               "source_sha256": {bp._key(p): _sha(p) for p in paths},
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


PADS_REPORTS = ROOT / "fpga/reports/arty/pads-demo-2025.1"
PADS_TIMING = {"wns_ns": 15.82, "tns_ns": 0.0, "setup_failing": 0.0, "setup_endpoints": 44078.0,
               "whs_ns": 0.03, "ths_ns": 0.0, "hold_failing": 0.0, "hold_endpoints": 44078.0,
               "wpws_ns": 3.0, "tpws_ns": 0.0, "pulse_failing": 0.0, "pulse_endpoints": 14098.0}


def _rewrite(name, old, new):
    """A stand-in Vivado's last step: the build wrote `new` where the committed
    report holds `old`."""
    return (f"p = out / {name!r}; t = p.read_text()\n"
            f"assert {old!r} in t, 'mutation target absent'\n"
            f"p.write_text(t.replace({old!r}, {new!r}))\n")


_EXTRA_EXCEPTION = (
    "p = out / 'exceptions.rpt'\n"
    "ls = p.read_text().splitlines(keepends=True)\n"
    "last = max(i for i, ln in enumerate(ls) if ln[:1].isdigit())\n"
    "ls.insert(last + 1, '12        [get_ports spi_miso]  *  *  false  false\\n')\n"
    "p.write_text(''.join(ls))\n")


def _replay(tmp_path, after=""):
    """main() over the committed pads-demo-2025.1 reports: a stand-in Vivado
    copies them into the build directory, runs the `after` snippet on them (a
    defect a real build could produce) and writes the two binary outputs.
    Returns (build directory, exit status, report.json)."""
    verification = tmp_path / "verification"
    verification.mkdir()
    _records(verification, bp.sources() + bp.roms())
    hook = tmp_path / "after_reports.py"
    hook.write_text(after)
    vivado = tmp_path / "vivado"
    vivado.write_text(f"""#!{sys.executable}
import shutil, sys
from pathlib import Path
if '-version' in sys.argv:
    print('report replay')
    sys.exit(0)
out = Path(sys.argv[sys.argv.index('-log') + 1]).parent
for p in Path({str(PADS_REPORTS)!r}).glob('*.rpt'):
    shutil.copyfile(p, out / p.name)
exec(compile(Path({str(hook)!r}).read_text(), 'after_reports', 'exec'))
for name in ('arty_pads.bit', 'routed.dcp'):
    (out / name).write_bytes(b'fixture')
""")
    vivado.chmod(0o755)
    build = tmp_path / "build"
    rc = bp.main(["--vivado", str(vivado), "--out", str(build), "--verification", str(verification)])
    return build, rc, json.loads((build / "report.json").read_text())


@pytest.mark.parametrize("after,changed,problem", [
    ("", (), None),
    (_rewrite("timing.rpt", "        3.000        0.000", "       -0.500        0.000"),
     ("timing.rpt",), "internal timing does not pass"),
    (_rewrite("timing.rpt", "     15.820        0.000", "          NaN        0.000"),
     ("timing.rpt",), "unexpected timing summary shape"),
    (_rewrite("drc.rpt", "| Design State : Fully Routed", "| Design State : Placed"),
     ("drc.rpt",), "report is not the routed Arty design"),
    (_rewrite("timing.rpt", "checking unconstrained_internal_endpoints (0)",
              "checking unconstrained_internal_endpoints (5)"),
     ("timing.rpt",), "internal timing is incomplete: unconstrained_internal_endpoints"),
    (_rewrite("drc.rpt", "| DPREG-4 | Warning  |", "| DPREG-4 | Critical Warning  |"),
     ("drc.rpt",), "DRC error or critical warning"),
    (_rewrite("clocks.rpt", "hardware_clock.clock_raw     81.380",
              "hardware_clock.clock_raw     50.000"), ("clocks.rpt",), "wrong core clock period"),
    (_rewrite(xb.REPORT, "CHECK\tuart_s1s2\t1\t", "CHECK\tuart_s1s2\t0\t"),
     (xb.REPORT,), "after-route check uart_s1s2 failed"),
    (_EXTRA_EXCEPTION, (xb.EXCEPTIONS,), "not exactly the XDC's set_false_path lines"),
], ids=["clean", "wpws", "nan-slack", "drc-not-routed", "check_timing", "drc", "clock", "route-check",
        "exception"])
def test_post_vivado_verdict_reads_every_report(tmp_path, after, changed, problem):
    build, rc, report = _replay(tmp_path, after)
    mutated = {p.name for p in build.glob("*.rpt")
               if p.read_bytes() != (PADS_REPORTS / p.name).read_bytes()}
    assert mutated == set(changed), mutated
    if problem is None:
        assert rc == 0 and report["state"] == "PASS" and report["problems"] == []
        assert report["timing"] == PADS_TIMING
    else:
        assert rc == 1 and report["state"] == "FAIL", report
        assert any(problem in p for p in report["problems"]), report["problems"]


def test_the_verdict_reads_the_compiled_xdc_snapshot_not_the_live_tree(tmp_path):
    """A line pushed into the live XDC while Vivado runs shifts every query's
    line number: the verdict must still be about the XDC the build compiled."""
    original = bp.XDC.read_text()
    try:
        after = (f"p = Path({str(bp.XDC)!r})\n"
                 "p.write_text('# edited in the live tree while Vivado ran\\n' + p.read_text())\n")
        build, rc, report = _replay(tmp_path, after)
        assert bp.XDC.read_text() != original
    finally:
        bp.XDC.write_text(original)
    assert rc == 0 and report["state"] == "PASS" and report["problems"] == [], report
    assert (build / "inputs" / bp._key(bp.XDC)).read_text() == original


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
