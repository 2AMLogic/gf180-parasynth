"""fpga/ext_io_extract.py's verdict logic, on the committed R1 extraction and
on mutations of it (no Vivado): a clean record PASSes, a failing port FAILs,
a control that is not caught or a missing port REFUSES."""
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ext_io_extract as ex  # noqa: E402

PATHS = HERE / "reports/arty/r1-player-preview-2025.1/ext-io/ext_io_paths.txt"


def _run(tmp_path, text):
    p = tmp_path / "paths.txt"
    p.write_text(text)
    out = tmp_path / "out"
    rc = ex.main(["--dcp", "x", "--dcp-sha256", "x", "--out", str(out), "--parse-only", str(p)])
    return rc, json.loads((out / "ext-io-extract.json").read_text())


def _edit(text, variant, port, kind, slack):
    lines = []
    for ln in text.splitlines():
        f = ln.split("\t")
        if f[:4] == ["PATH", variant, port, kind]:
            f[4] = slack
            ln = "\t".join(f)
        lines.append(ln)
    return "\n".join(lines) + "\n"


def test_the_committed_r1_extraction_passes(tmp_path):
    rc, rec = _run(tmp_path, PATHS.read_text())
    assert rc == 0 and rec["state"] == "PASS"
    assert rec["control"]["caught"] and rec["control"]["spi_miso_setup_slack_ns"] < 0
    assert rec["spi_miso"]["readback_qualified"]
    # the dead UART-RX XDC constraints are recorded, not hidden
    pats = rec["synchronisers"]["patterns"]
    assert pats["xdc:uart_async_reg"]["matched"] == 0
    assert pats["xdc:uart_false_path_d"]["matched"] == 0
    assert pats["netlist:uart_rx_q"]["matched"] == 2
    assert pats["xdc:spi_async_reg"]["matched"] == 6


def test_a_failing_port_fails(tmp_path):
    text = _edit(PATHS.read_text(), "clean", "uart_txd", "min", "-0.100")
    rc, rec = _run(tmp_path, text)
    assert rc == 1 and rec["state"] == "FAIL" and rec["failing_ports"] == ["uart_txd"]


def test_an_uncaught_control_refuses(tmp_path):
    text = _edit(PATHS.read_text(), "IMPOSSIBLE_MISO", "spi_miso", "max", "15.445")
    rc, rec = _run(tmp_path, text)
    assert rc == 2 and "not caught" in rec["reason"]


def test_a_control_that_moves_another_port_refuses(tmp_path):
    text = _edit(PATHS.read_text(), "IMPOSSIBLE_MISO", "led[2]", "max", "-1.000")
    rc, rec = _run(tmp_path, text)
    assert rc == 2 and "not caught" in rec["reason"]


@pytest.mark.parametrize("port", ["spi_miso", "uart_txd"])
def test_an_unconstrained_port_refuses(tmp_path, port):
    text = _edit(PATHS.read_text(), "clean", port, "max", "NONE")
    rc, rec = _run(tmp_path, text)
    assert rc == 2 and port in rec["reason"]
