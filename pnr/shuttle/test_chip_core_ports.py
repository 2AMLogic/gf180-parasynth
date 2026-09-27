#!/usr/bin/env python3
"""The routed chip must be the design: every port of `synth_top` is connected.

WHY THIS IS A TEST AND NOT A CODE READING.  #33's first acceptance criterion is that
`pnr/shuttle`'s RTL "match the current `synth_top`", and the way this repository has
got that wrong before is not by connecting a port to the wrong thing -- it is by not
connecting it at all.  `CLAUDE.md`: *"Check that the thing you are testing is the
thing that ships. Every bench drove the register write port rather than the link, so
the control path delivered 37 of 155 writes with every block still bit-exact."*

A `synth_top` port omitted from `chip_core.sv`'s instantiation is exactly that shape,
and it is quiet in every direction that matters:

  * Verilog named-port connection does not require completeness, so an omitted input
    elaborates as unconnected and synthesises to a constant;
  * a constant input makes the design SMALLER and the route CLEANER, so utilisation,
    DRC and timing all improve;
  * the flop cross-check in `check_route.py` would still pass, because `drum_regs`'s
    bits are all still there.

`chip_core.sv`'s own comment says "Every port of synth_top is listed". This asserts it
against the file, on every run, so the comment cannot outlive the fact -- the drift
that PR #176 shipped (written against a `synth_top` predating `uart_rxd`/`uart_txd`)
is the case this would have caught.

Run: pytest pnr/shuttle/test_chip_core_ports.py
"""

import os
import re

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))

SYNTH_TOP = os.path.join(REPO, "rtl-sketch", "synth_top.v")
CHIP_CORE = os.path.join(HERE, "src", "chip_core.sv")

PORT = re.compile(r"^\s*(input|output|inout)\b[^;]*?(\w+)\s*(?:,|\)\s*;|//|$)", re.M)


def synth_top_ports(path: str) -> list[str]:
    """The port names in `module synth_top ... );`, in declaration order.

    Parsed from the module header only: a `reg`/`wire` of the same name declared in
    the body is not a port, and an `input` inside a *different* module in the same
    file is not this module's.
    """
    text = open(path, encoding="utf-8").read()
    m = re.search(r"^module\s+synth_top\b", text, re.M)
    if not m:
        raise AssertionError(f"{path} has no `module synth_top`")
    # The header ends at the first `);` that closes the port list.
    rest = text[m.end():]
    end = rest.index(");")
    header = rest[:end]
    ports = [mm.group(2) for mm in PORT.finditer(header)]
    if not ports:
        raise AssertionError("parsed no ports out of synth_top's header")
    return ports


def chip_core_connections(path: str) -> dict:
    """`.port (expr)` pairs of the `synth_top u_synth (...)` instantiation."""
    text = open(path, encoding="utf-8").read()
    m = re.search(r"synth_top\s+(?:#\s*\([^)]*\)\s*)?u_synth\s*\(", text)
    if not m:
        raise AssertionError(f"{path} does not instantiate `synth_top u_synth`")
    # Walk to the matching close paren so a nested `(...)` in a connection does not
    # end the instantiation early.
    depth, i = 1, m.end()
    while depth and i < len(text):
        depth += {"(": 1, ")": -1}.get(text[i], 0)
        i += 1
    body = text[m.end():i - 1]
    return {mm.group(1): mm.group(2).strip()
            for mm in re.finditer(r"\.(\w+)\s*\(([^()]*(?:\([^()]*\)[^()]*)*)\)", body)}


def test_every_synth_top_port_is_connected_in_chip_core():
    ports = synth_top_ports(SYNTH_TOP)
    conn = chip_core_connections(CHIP_CORE)
    missing = [p for p in ports if p not in conn]
    assert not missing, (
        f"chip_core.sv does not connect {missing}. An omitted named port elaborates "
        f"as unconnected: it synthesises to a constant, which makes the design "
        f"SMALLER and the route CLEANER, and no area, DRC, timing or flop-census "
        f"check would go red. Connect it, or tie it off explicitly.")


def test_chip_core_connects_nothing_synth_top_does_not_have():
    """The other direction: a stale connection is a typo that elaborates silently."""
    ports = set(synth_top_ports(SYNTH_TOP))
    extra = [p for p in chip_core_connections(CHIP_CORE) if p not in ports]
    assert not extra, f"chip_core.sv connects {extra}, which synth_top does not declare"


def test_no_connection_is_left_empty():
    """`.port ()` is syntactically fine and means 'unconnected'."""
    conn = chip_core_connections(CHIP_CORE)
    empty = [p for p, e in conn.items() if not e.strip()]
    assert not empty, f"chip_core.sv leaves {empty} explicitly unconnected"


def test_the_parser_finds_the_uart_ports_reconciliation_added():
    """A control on the parser itself, not on the design.

    If `synth_top_ports` silently returned a short list, the assertions above would
    pass for a file that connects nothing. `uart_rxd`/`uart_txd` are the two ports
    PR #176's wrapper predated, so their presence is the thing the reconciliation
    criterion is about.
    """
    ports = synth_top_ports(SYNTH_TOP)
    assert {"clk", "rst_n_pad", "sck", "mosi", "cs_n", "miso",
            "uart_rxd", "uart_txd", "bclk", "lrclk", "sdata"} <= set(ports)


@pytest.mark.parametrize("header,want", [
    ("#(parameter A=1) (\n input wire clk,\n output wire q\n", ["clk", "q"]),
    ("(\n input  wire [3:0] d,   // a comment\n inout  wire pad\n", ["d", "pad"]),
])
def test_port_parser_on_shapes_it_has_to_handle(tmp_path, header, want):
    f = tmp_path / "synth_top.v"
    f.write_text("module other (input wire x);\nendmodule\n"
                 f"module synth_top {header});\nreg input_like;\nendmodule\n")
    assert synth_top_ports(str(f)) == want


# --------------------------------------------------------------------------- #
# controls: the guard going red.  A gate that has only ever been seen green is
# not known to be a gate (CLAUDE.md: "Run a gate against the current state
# before committing it. An unsatisfiable gate is worse than no gate").
# --------------------------------------------------------------------------- #

def _pair(tmp_path, ports, connections):
    st = tmp_path / "synth_top.v"
    st.write_text("module synth_top (\n"
                  + "".join(f"  input wire {p},\n" for p in ports[:-1])
                  + f"  input wire {ports[-1]}\n);\nendmodule\n")
    cc = tmp_path / "chip_core.sv"
    cc.write_text("module chip_core;\n  synth_top u_synth (\n"
                  + ",\n".join(f"    .{p} ({e})" for p, e in connections.items())
                  + "\n  );\nendmodule\n")
    return str(st), str(cc)


def test_control_an_omitted_port_is_detected(tmp_path):
    st, cc = _pair(tmp_path, ["clk", "sck", "miso"],
                   {"clk": "clk", "sck": "s"})          # miso omitted
    assert [p for p in synth_top_ports(st) if p not in chip_core_connections(cc)] \
        == ["miso"]


def test_control_a_stale_extra_connection_is_detected(tmp_path):
    st, cc = _pair(tmp_path, ["clk"], {"clk": "clk", "spi_clk": "x"})
    ports = set(synth_top_ports(st))
    assert [p for p in chip_core_connections(cc) if p not in ports] == ["spi_clk"]


def test_control_an_explicitly_empty_connection_is_detected(tmp_path):
    st, cc = _pair(tmp_path, ["clk", "miso"], {"clk": "clk", "miso": ""})
    conn = chip_core_connections(cc)
    assert [p for p, e in conn.items() if not e.strip()] == ["miso"]
