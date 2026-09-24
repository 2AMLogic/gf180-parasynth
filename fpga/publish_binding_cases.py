#!/usr/bin/env python3
"""Binding cases for fpga/publish_arty.py: artifacts in a plausible but
wrong state, each of which publication must REFUSE.

Every case here PASSED the pre-fix publisher (publication-binding
.pre-fix.json records the run); the post-fix publisher refuses all of
them (.post-fix.json). The pair is the wrong-then-right accounting for
the publication gate.

Cases build on a copy of the published fixture, repaired into an
internally consistent artifact first (dummy routed.dcp whose hash is
written into report.json) so that the ONLY defect is the one under test:

  extra_compiled_source   build.tcl reads a Verilog file the digital
                          proof never covered (hash set unchanged).
  uart_top_with_clean_proof  -top renamed to the UART wrapper; the clean
                          wrapper's verification record still "covers" it.
  foreign_exception       the routed report's forwarded-clock exception
                          list doctored to a port that is not i2s_bclk.
  xdc_value_drift         the shipped XDC snapshot's output-delay value
                          edited away from the recorded budget.
  uart_ports_without_disposition  XDC gains uart_tx/uart_rx pins with no
                          constraints and no recorded disposition.

Usage:
    publish_binding_cases.py --record PATH   run against the CURRENT
                             publisher, record one JSON line per case
"""

import hashlib
import json
import pathlib
import re
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
FIXTURE = ROOT / "fpga/reports/arty/vivado-2025.1"


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def make_artifact(tmp):
    """Internally consistent copy of the published fixture, re-bound to the
    CURRENT build script.

    The published *.rpt are the sanitized copies (Host line omitted), so
    the build record's artifact hashes cannot match them; the record is
    re-bound to the files actually present. The publisher demands an
    artifact of the CURRENT build_arty.sources(): since the UART bridge
    merged, that set includes rtl-sketch/uart_bridge.v and the bound proof
    is the UART-wrapper clean run -- the fixture (an earlier, pre-uart
    publication) is brought to that state, so the ONLY defect is the one
    the case adds.
    """
    import build_arty as build
    import publish_arty
    art = tmp / "artifact"
    shutil.copytree(FIXTURE, art)
    (art / "routed.dcp").write_bytes(b"publication-binding-case\n")
    # an honest build snapshots its inputs; the fixture predates that copy
    for rel in [str(p.relative_to(ROOT)) for p in build.sources()] \
            + [str(build.XDC.relative_to(ROOT))]:
        dest = art / "inputs" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / rel, dest)
    record = json.loads((art / "report.json").read_text())
    record["artifact_sha256"] = {name: sha(art / name) for name in
                                 record["artifact_sha256"]}
    # re-bind sources to the CURRENT tree: the committed fixture predates
    # later source-set changes (e.g. uart_bridge.v joining the compiled
    # set), and the publisher must refuse artifacts that are not
    # tree-current
    record["source_sha256"] = {str(p.relative_to(ROOT)): build.sha(p)
                               for p in build.sources() + build.roms()
                               + [build.XDC]}
    # re-bind the wrapper proof to the CURRENT wrapper's evidence
    top = "arty_a7_top"
    record["verification"] = build.validate_verification(
        publish_arty.VERIFICATION_BY_WRAPPER[top],
        build.sources() + build.roms())
    # the compiled set must show the CURRENT script's read_verilog list:
    # add the bridge to the snapshot list the same way build.tcl carries it
    tcl = (art / "build.tcl").read_text()
    bridge = str(art / "inputs/rtl-sketch/uart_bridge.v")
    # append INSIDE the list: keep the last path's closing brace, then the
    # new word, then close the list (replacing "}" ate the brace on an
    # earlier revision and the appended path merged into the previous entry)
    tcl, n = re.subn(r"\}\]$", "} {" + bridge + "}]", tcl, count=1,
                     flags=re.M)
    assert n == 1, "build.tcl read_verilog list not found"
    (art / "build.tcl").write_text(tcl)
    (art / "report.json").write_text(json.dumps(record, indent=2) + "\n")
    rehash_script(art)
    return art


def rehash_script(art):
    """build.tcl was edited: keep the recorded script hash consistent so
    the ONLY defect under test is the binding, not a hash mismatch."""
    record = json.loads((art / "report.json").read_text())
    record["script_sha256"] = sha(art / "build.tcl")
    (art / "report.json").write_text(json.dumps(record, indent=2) + "\n")


def case_extra_compiled_source(tmp):
    art = make_artifact(tmp)
    stub = art / "inputs/rtl-sketch/uart_tx_stub.v"
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.write_text("module uart_tx_stub(input clk);\nendmodule\n")
    tcl = (art / "build.tcl").read_text()
    # an extra source, snapshotted under the build's inputs/ tree, that no
    # verification run ever saw (append inside the read_verilog list,
    # keeping the previous word's closing brace)
    tcl, n = re.subn(r"\}\]$", "} {" + str(stub) + "}]", tcl, count=1,
                     flags=re.M)
    assert n == 1, "build.tcl read_verilog list not found"
    (art / "build.tcl").write_text(tcl)
    rehash_script(art)
    return art


def case_uart_top_with_clean_proof(tmp):
    art = make_artifact(tmp)
    tcl = (art / "build.tcl").read_text()
    assert "synth_design -top arty_a7_top" in tcl
    tcl = tcl.replace("synth_design -top arty_a7_top",
                      "synth_design -top arty_a7_uart_top", 1)
    (art / "build.tcl").write_text(tcl)
    rehash_script(art)
    return art


def case_foreign_exception(tmp):
    art = make_artifact(tmp)
    rpt = art / "timing.rpt"
    text = rpt.read_text()
    # the routed report lists exactly one timing-clock exception by name;
    # keep counts consistent, change the NAME
    text, n = re.subn(r"(timing clock defined on it.*?\n\n)i2s_bclk\n",
                      r"\1some_other_port\n", text, count=1, flags=re.DOTALL)
    if n != 1:
        raise SystemExit("fixture no longer lists the exception port")
    rpt.write_text(text)
    # keep the artifact internally consistent (the doctored report is
    # hashed into the build record) so the ONLY defect is that the
    # permitted-exception binding does not exist at all
    record = json.loads((art / "report.json").read_text())
    record["artifact_sha256"]["timing.rpt"] = sha(rpt)
    (art / "report.json").write_text(json.dumps(record, indent=2) + "\n")
    return art


def case_xdc_value_drift(tmp):
    art = make_artifact(tmp)
    xdc = art / "inputs/fpga/boards/arty-a7-100.xdc"
    xdc.parent.mkdir(parents=True, exist_ok=True)
    text = (ROOT / "fpga/boards/arty-a7-100.xdc").read_text()
    # the approved I2S setup budget is 8.200 ns (SLAS764B Table 7 tDS +
    # flight); ship 12.000 instead and see whether publication notices
    assert "-max 8.200 [get_ports i2s_sdata]" in text
    text = text.replace("-max 8.200 [get_ports i2s_sdata]",
                        "-max 12.000 [get_ports i2s_sdata]", 1)
    xdc.write_text(text)
    return art


def case_uart_ports_without_disposition(tmp):
    art = make_artifact(tmp)
    xdc = art / "inputs/fpga/boards/arty-a7-100.xdc"
    text = xdc.read_text()
    # the integrated XDC constrains the UART ports; strip that evidence and
    # publication must notice the ports it now demands evidence for
    for line in text.splitlines():
        if "uart_rxd" in line and "false_path" in line:
            text = text.replace(line + "\n", "")
        elif "ASYNC_REG TRUE" in line and "uart" in line:
            text = text.replace(line + "\n", "")
        elif "set_output_delay" in line and "uart_txd" in line:
            text = text.replace(line + "\n", "")
    xdc.write_text(text)
    return art


def case_baseline(tmp):
    """The unmutated internally-consistent artifact: MUST still publish.

    Without this case the binding gates above could be unsatisfiable --
    the failure mode that trains everyone to ignore gates."""
    return make_artifact(tmp)


CASES = [
    ("baseline", case_baseline),
    ("extra_compiled_source", case_extra_compiled_source),
    ("uart_top_with_clean_proof", case_uart_top_with_clean_proof),
    ("foreign_exception", case_foreign_exception),
    ("xdc_value_drift", case_xdc_value_drift),
    ("uart_ports_without_disposition", case_uart_ports_without_disposition),
]


def run_case(name, builder, tmp, publisher=None):
    """Returns {"case", "published", "error"} for one case."""
    import publish_arty
    publish = publisher or publish_arty.publish
    art = builder(tmp)
    out = tmp / "publication"
    try:
        summary = publish(art, out)
        return {"case": name, "published": True,
                "exceptions": summary.get("output_delay_exceptions"),
                "wrapper_verification": summary.get("verification")}
    except ValueError as exc:
        return {"case": name, "published": False, "error": str(exc)}


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    record_path = None
    only = None
    it = iter(argv)
    for a in it:
        if a == "--record":
            record_path = pathlib.Path(next(it))
        elif a == "--case":
            only = next(it)
    import tempfile
    results = []
    for name, builder in CASES:
        if only and name != only:
            continue
        with tempfile.TemporaryDirectory(prefix="pub-binding-") as td:
            res = run_case(name, builder, pathlib.Path(td))
        results.append(res)
        outcome = "PUBLISHED" if res["published"] else f"refused: {res.get('error')}"
        print(f"{name:<32} {outcome}")
    if record_path:
        record_path.write_text(json.dumps(results, indent=2) + "\n")
        print(f"recorded -> {record_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
