#!/usr/bin/env python3
r"""report_synth_area.py -- report a synthesised cell count, or REFUSE if the
design's outputs are X.

This exists because of one shipped bug, reinstated below as a permanent
injection (`docs/verification-rules.md` rule 5, issue #245):

    `ladder_dp_t16.v`'s tanh index was out of range, yosys marked the datapath
    don't-care, EVERY OUTPUT WAS X -- and it was quoted at 1,917 cells for
    three rounds before anyone simulated it.

Nothing refused. A cell count is not evidence of correctness (rule 3), but
until now no tool in this repository enforced that: every area-reporting path
would answer "1,917 cells" for a design that computes nothing, and the answer
looks exactly like data.

    THREE OUTCOMES, AND THE THIRD IS THE POINT

    exit 0  PASS     -- an area is reported (or `--expect` was met)
    exit 1  the expectation was NOT met: a clean run refused, or an injected
                        run reported an area. A control that does not fire.
    exit 2  REFUSED  -- no area, and none will be given. Either the design's
                        outputs are X (`reason: outputs-are-x`) or a
                        precondition of the apparatus failed (`reason:
                        apparatus`, `no-clock`, `no-reset`, `port-too-wide`,
                        `injection-not-active`, `no-receipt`). REFUSED is not a
                        pass and not a fail: nothing was measured.

`--expect refused-x` requires state REFUSED **with reason `outputs-are-x`**, so
a missing yosys cannot make the control look like it fired -- condition 3 of the
three-condition rule ("the intended assertion is the one that fails").

    HOW THE X IS FOUND, AND WHAT IS BLIND TO IT

Four properties are measured and each is reported MOVED/BLIND per rule 4,
because they emphatically do not all see the same thing. Measured on this tree,
2026-09-26, yosys 0.67+post / Icarus 13.0, fixture `tanh_dp`, injection
`TANH_INDEX_OOR`:

    property              clean            injected TANH_INDEX_OOR
    rtl-simulation        0 x cycles       MOVED: y is x on 506 of 512 cycles,
                                           first at cycle 6
    netlist-simulation    0 x cycles       BLIND: yosys resolved the don't-care
                                           to concrete values; the gate-level
                                           netlist simulates x-free and WRONG
    netlist-constant-x    none             BLIND: no output bit is a constant x
    cell-count            1,679 cells      BLIND: 1,677 cells, -0.1 %

**That is the whole finding of this file.** The two checks a layout engineer
would reach for first -- does the netlist contain x, does the netlist simulate
to x -- are both blind, because yosys is entitled to resolve a don't-care to
anything it likes and does. The area moves by 0.1 %: it is not a weak detector,
it is not a detector at all. Only a behavioural simulation of the sources
distinguishes "computes something" from "is don't-care", which is exactly rule
1's hardware-specific part: in hardware an unimplemented function outputs X, and
an X comparison can silently pass depending on how the bench is written.

    APPARATUS PRECONDITIONS, ASSERTED AT THE POINT OF USE

* yosys, `yosys-config`, iverilog and vvp must be on PATH. Absent => REFUSED.
* the top module must have a 1-bit clock port and a reset port, because an x at
  an output of a netlist with an uninitialised flop says nothing. Absent =>
  REFUSED, rather than a number nobody can interpret.
* parameter overrides reach BOTH tools through one generated wrapper file that
  both read, and the run is REFUSED unless two independent receipts agree that
  the override is live: the `$paramod\<mod>\<P>=...` module yosys derived, and
  the parameter value the running simulation prints from inside the elaborated
  hierarchy. `iverilog -Ptb.dut.IDX_BITS=5` SILENTLY DOES NOTHING (-P takes
  root-module parameters only), which is exactly how an injection comes to not
  be injected while the log looks clean; see `docs/tool-findings.md`.
* the simulation must print its end-of-run markers. A missing marker is
  REFUSED, never a zero x-count: `docs/verification-rules.md` rule 5 condition
  2 -- a tool that could not run must not be indistinguishable from a control
  that worked.

    USE

    pnr/report_synth_area.py --fixture tanh_dp                  # report an area
    pnr/report_synth_area.py --fixture tanh_dp --inject TANH_INDEX_OOR
    pnr/report_synth_area.py --top ladder_dp --src rtl-sketch/ladder_dp.v \\
        --data rtl-sketch/tanh16.hex --reset rst_n:low
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

PASS, EXPECTATION_UNMET, REFUSED = 0, 1, 2

#: 64-bit maximal-length LFSR taps, so the stimulus is deterministic and the
#: same on every machine. A control whose stimulus is random is not a control.
LFSR_SEED = "64'h0123456789abcdef"
LFSR_STEP = "lfsr = {lfsr[62:0], lfsr[63]^lfsr[60]^lfsr[58]^lfsr[55]};"


class Refused(Exception):
    """Nothing was measured. Distinct from both a pass and a failure."""

    def __init__(self, reason: str, detail: str):
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


@dataclasses.dataclass(frozen=True)
class Fixture:
    """A design this tool can be pointed at, with its apparatus preconditions."""

    top: str
    sources: tuple[str, ...]
    data: tuple[str, ...]          # staged beside the sources: $readmemh reads cwd
    params: dict[str, int]         # integer parameter overrides, always explicit
    clock: str
    reset: str                     # "<port>:low" or "<port>:high"
    cycles: int = 512


@dataclasses.dataclass(frozen=True)
class Injection:
    """A defect this project SHIPPED, reinstated as a permanent control."""

    shipped_as: str
    params: dict[str, int]


FIXTURES = {
    "tanh_dp": Fixture(
        top="tanh_dp",
        sources=("pnr/fixtures/tanh_dp.v",),
        data=("rtl-sketch/tanh16.hex",),
        params={"IDX_BITS": 4},
        clock="clk",
        reset="rst_n:low",
    ),
}

INJECTIONS = {
    "TANH_INDEX_OOR": Injection(
        shipped_as=(
            "ladder_dp_t16.v -- the tanh index field one bit wider than the "
            "table it indexes, so the read is out of range, which is x in "
            "Verilog and don't-care to yosys. Quoted at 1,917 cells for three "
            "rounds (docs/verification-rules.md rules 1 and 3)."
        ),
        params={"IDX_BITS": 5},
    ),
}


# --------------------------------------------------------------- pure helpers

def decode_yosys_const(text: str) -> int | str:
    """Decode a yosys constant as it appears in JSON/module names.

    `"s32'00000000000000000000000000000101"` -> 5, `"00000000...0100"` -> 4,
    anything with x/z or non-binary content is returned verbatim (a string
    parameter, or a constant this tool must not pretend to understand).
    """
    m = re.fullmatch(r"(s?)(\d+)'([01]+)", text)
    if m:
        signed, width, bits = m.group(1), int(m.group(2)), m.group(3)
        value = int(bits, 2)
        if signed and len(bits) == width and bits[0] == "1":
            value -= 1 << width
        return value
    if re.fullmatch(r"[01]+", text):
        value = int(text, 2)
        # yosys writes plain 32-bit two's complement for integer defaults.
        if len(text) == 32 and text[0] == "1":
            value -= 1 << 32
        return value
    return text


def paramod_params(module_name: str) -> tuple[str, dict[str, int | str]] | None:
    r"""Decode `$paramod\tanh_dp\IDX_BITS=s32'...0101` into ('tanh_dp', {...}).

    Returns None for a module name that is not a yosys paramod.
    """
    if not module_name.startswith("$paramod"):
        return None
    fields = module_name.split("\\")
    if len(fields) < 3:
        return None
    base, params = fields[1], {}
    for field in fields[2:]:
        if "=" not in field:
            return None
        key, _, value = field.partition("=")
        params[key] = decode_yosys_const(value)
    return base, params


def yosys_parameter_receipt(module_names: list[str], base: str,
                            wanted: dict[str, int]) -> dict[str, int | str]:
    """What yosys actually elaborated `base` with, from the module names.

    Raises `Refused` when no module in the design corresponds to `base` with
    the requested parameters -- i.e. the override did not reach yosys, so
    whatever the run goes on to measure is not the mutant.
    """
    seen = []
    for name in module_names:
        decoded = paramod_params(name)
        if decoded and decoded[0] == base:
            seen.append(decoded[1])
            if all(decoded[1].get(k) == v for k, v in wanted.items()):
                return decoded[1]
    if not wanted and base in module_names:
        return {}
    raise Refused(
        "injection-not-active",
        f"yosys elaborated no {base} with {wanted} -- the parameter override "
        f"did not reach synthesis. Derived modules seen: {seen or module_names}. "
        f"Nothing measured after this point would be the mutant.")


def module_ports(design: dict, top: str) -> dict[str, dict]:
    """`{name: {"direction":…, "width":…, "signed":…, "bits":[…]}}` for `top`."""
    modules = design.get("modules", {})
    if top not in modules:
        raise Refused("apparatus",
                      f"yosys wrote no module {top!r} (saw {list(modules)})")
    out = {}
    for name, port in modules[top].get("ports", {}).items():
        out[name] = {"direction": port["direction"], "bits": port["bits"],
                     "width": len(port["bits"]), "signed": bool(port.get("signed"))}
    return out


def constant_x_output_bits(design: dict, top: str) -> dict[str, list[int]]:
    """Output port bit positions that are a constant x/z, or driven by nothing.

    The static half of the X check: what a reader of the netlist could see
    without simulating it. Measured to be BLIND for `TANH_INDEX_OOR` -- see the
    module docstring; it is kept because it is the only property that can see
    an output yosys left with no driver at all, and reporting a property that
    did NOT see the defect is rule 4's whole point.
    """
    module = design["modules"][top]
    driven: set[int] = set()
    for name, port in module.get("ports", {}).items():
        if port["direction"] in ("input", "inout"):
            driven.update(b for b in port["bits"] if isinstance(b, int))
    for cell in module.get("cells", {}).values():
        directions = cell.get("port_directions", {})
        for pin, bits in cell.get("connections", {}).items():
            if directions.get(pin) in ("output", "inout"):
                driven.update(b for b in bits if isinstance(b, int))
    bad: dict[str, list[int]] = {}
    for name, port in module.get("ports", {}).items():
        if port["direction"] != "output":
            continue
        positions = [i for i, bit in enumerate(port["bits"])
                     if (isinstance(bit, str) and bit in ("x", "z"))
                     or (isinstance(bit, int) and bit not in driven)]
        if positions:
            bad[name] = positions
    return bad


def wrapper_source(top: str, ports: dict[str, dict], params: dict[str, int]) -> str:
    """A wrapper that fixes the parameters, read by BOTH yosys and iverilog.

    One text for both tools is the point: `chparam` on one side and `-P` on the
    other are two mechanisms that can disagree, and one of them (iverilog's)
    fails silently.
    """
    def decl(name: str, port: dict) -> str:
        kind = "input wire" if port["direction"] == "input" else "output wire"
        sign = " signed" if port["signed"] else ""
        width = "" if port["width"] == 1 else f" [{port['width'] - 1}:0]"
        return f"{kind}{sign}{width} {name}"

    overrides = ", ".join(f".{k}({v})" for k, v in sorted(params.items()))
    hookup = ", ".join(f".{n}({n})" for n in ports)
    return (
        "// generated by pnr/report_synth_area.py -- do not edit.\n"
        "// The parameter overrides live HERE, in one file both yosys and\n"
        "// iverilog read, so the two tools cannot disagree about the mutant.\n"
        "`default_nettype none\n"
        f"module {top}_cfg (\n    "
        + ",\n    ".join(decl(n, p) for n, p in ports.items())
        + f"\n);\n    {top}"
        + (f" #({overrides})" if overrides else "")
        + f" u (\n        {hookup}\n    );\nendmodule\n"
        "`default_nettype wire\n")


def tb_source(top: str, ports: dict[str, dict], clock: str, reset: str,
              reset_active_low: bool, params: dict[str, int], cycles: int) -> str:
    """A generated bench that drives every input and counts x at every output.

    `^vec === 1'bx` is true when ANY bit of `vec` is x or z, which is the check
    rule 1 asks for: X is a third outcome, not a mismatch.
    """
    inputs = [n for n, p in ports.items()
              if p["direction"] == "input" and n not in (clock, reset)]
    outputs = [n for n, p in ports.items() if p["direction"] == "output"]
    if not outputs:
        raise Refused("apparatus", f"{top} has no output ports to check for x")
    for name in inputs:
        if ports[name]["width"] > 64:
            raise Refused(
                "port-too-wide",
                f"input {name} is {ports[name]['width']} bits and this driver's "
                f"LFSR is 64 -- refusing rather than silently driving part of it")

    lines = [
        "// generated by pnr/report_synth_area.py -- do not edit.",
        "`timescale 1ns/1ps",
        "module tb;",
        "  reg clk = 1'b0;",
        f"  reg [63:0] lfsr = {LFSR_SEED};",
        "  integer cyc, samples, first_x;",
    ]
    for name in inputs + [reset]:
        width = ports[name]["width"]
        lines.append(f"  reg{'' if width == 1 else f' [{width - 1}:0]'} {name};")
    for name in outputs:
        width = ports[name]["width"]
        lines.append(f"  wire{'' if width == 1 else f' [{width - 1}:0]'} {name};")
        lines.append(f"  integer x_{name};")
    hookup = ", ".join(f".{n}(clk)" if n == clock else f".{n}({n})" for n in ports)
    lines += [
        f"  {top}_cfg dut ({hookup});",
        "  always #5 clk = ~clk;",
        "  initial begin",
    ]
    lines += [f"    x_{n} = 0;" for n in outputs]
    lines += [
        "    samples = 0; first_x = -1;",
        f"    {reset} = 1'b{0 if reset_active_low else 1};",
    ]
    lines += [f"    {n} = 0;" for n in inputs]
    lines += [
        "    repeat (4) @(posedge clk);",
        f"    {reset} = 1'b{1 if reset_active_low else 0};",
        f"    for (cyc = 0; cyc < {cycles}; cyc = cyc + 1) begin",
    ]
    for name in inputs:
        width = ports[name]["width"]
        lines.append(f"      {LFSR_STEP}")
        lines.append(f"      {name} = lfsr[{width - 1}:0];" if width > 1
                     else f"      {name} = lfsr[0];")
    lines += [
        "      @(posedge clk);",
        "      #1;",
        "      samples = samples + 1;",
    ]
    for name in outputs:
        lines += [
            f"      if (^{name} === 1'bx) begin",
            f"        x_{name} = x_{name} + 1;",
            "        if (first_x < 0) first_x = cyc;",
            "      end",
        ]
    lines.append("    end")
    # The parameter receipts read the ELABORATED hierarchy, so they prove the
    # override is live in the design the simulation just ran. The synthesised
    # netlist has no parameters, hence the guard.
    lines.append("`ifndef NO_PARAM_RECEIPT")
    for name in sorted(params):
        lines.append(f'    $display("PARAM {name} %0d", dut.u.{name});')
    lines.append("`endif")
    lines += [
        '    $display("SAMPLES %0d", samples);',
        '    $display("FIRSTX %0d", first_x);',
    ]
    lines += [f'    $display("PORTX {n} %0d", x_{n});' for n in outputs]
    lines += ['    $display("DONE");', "    $finish;", "  end", "endmodule", ""]
    return "\n".join(lines)


def parse_sim(stdout: str, outputs: list[str], want_params: dict[str, int] | None
              ) -> dict:
    """Read the bench's markers, or REFUSE. A missing marker is not a zero."""
    if "DONE" not in stdout:
        raise Refused("no-receipt",
                      "the simulation printed no DONE marker, so it did not run "
                      f"to the end -- nothing was measured. Output: {stdout[-600:]!r}")
    def one(pattern: str, what: str) -> str:
        found = re.findall(pattern, stdout, re.M)
        if len(found) != 1:
            raise Refused("no-receipt",
                          f"expected exactly one {what} marker, found {len(found)}")
        return found[0]

    samples = int(one(r"^SAMPLES (\d+)$", "SAMPLES"))
    first_x = int(one(r"^FIRSTX (-?\d+)$", "FIRSTX"))
    if samples <= 0:
        raise Refused("no-receipt", "the bench sampled 0 cycles")
    per_port = dict(re.findall(r"^PORTX (\S+) (\d+)$", stdout, re.M))
    missing = [n for n in outputs if n not in per_port]
    if missing:
        raise Refused("no-receipt",
                      f"the bench reported no x count for output(s) {missing}")
    if want_params is not None:
        seen = {k: int(v) for k, v in re.findall(r"^PARAM (\S+) (-?\d+)$", stdout, re.M)}
        if seen != want_params:
            raise Refused(
                "injection-not-active",
                f"the ELABORATED design carries parameters {seen}, not "
                f"{want_params} -- the override never reached the simulator "
                f"(iverilog's -P is root-module-only and silently ignores a "
                f"hierarchical path). The run measured the wrong design.")
    return {"samples": samples, "first_x": first_x,
            "x_cycles": {k: int(v) for k, v in per_port.items()}}


def verdict(properties: dict[str, dict]) -> tuple[str, str]:
    """PASS unless a property saw x. The tool never returns both."""
    moved = [name for name, p in properties.items() if p["moved"]]
    if moved:
        return "REFUSED", "outputs-are-x"
    return "PASS", "outputs-are-defined"


def exit_code(state: str, reason: str, expect: str | None) -> int:
    """0 met / 1 not met / 2 nothing measured -- see the module docstring."""
    if expect is None:
        return PASS if state == "PASS" else REFUSED
    if expect == "area":
        if state == "PASS":
            return PASS
        return EXPECTATION_UNMET if reason == "outputs-are-x" else REFUSED
    if expect == "refused-x":
        if state == "REFUSED" and reason == "outputs-are-x":
            return PASS
        return EXPECTATION_UNMET if state == "PASS" else REFUSED
    raise Refused("apparatus", f"unknown --expect {expect!r}")


# ------------------------------------------------------------------ apparatus

def require_tools(*names: str) -> dict[str, str]:
    found = {}
    for name in names:
        path = shutil.which(name)
        if not path:
            raise Refused("apparatus", f"{name} is not on PATH -- nothing was measured")
        found[name] = path
    return found


def yosys_datdir() -> pathlib.Path:
    out = subprocess.run(["yosys-config", "--datdir"], capture_output=True, text=True)
    if out.returncode != 0:
        raise Refused("apparatus", f"yosys-config --datdir failed: {out.stderr.strip()}")
    datdir = pathlib.Path(out.stdout.strip())
    for name in ("simcells.v", "simlib.v"):
        if not (datdir / name).is_file():
            raise Refused("apparatus", f"{datdir / name} is absent; a gate-level "
                                       "simulation of a yosys netlist needs it")
    return datdir


def run(cmd: list[str], cwd: pathlib.Path, log: pathlib.Path) -> str:
    done = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    log.write_text(f"$ {' '.join(cmd)}\n{done.stdout}\n{done.stderr}\n")
    if done.returncode != 0:
        raise Refused("apparatus",
                      f"{cmd[0]} exited {done.returncode} (see {log}): "
                      f"{(done.stderr or done.stdout)[-600:]}")
    return done.stdout


def synthesise(work: pathlib.Path, fixture: Fixture, params: dict[str, int],
               liberty: str | None) -> dict:
    """Stage sources, elaborate, synthesise, and report cells/area + netlist."""
    work.mkdir(parents=True, exist_ok=True)
    staged = []
    for rel in fixture.sources:
        dest = work / pathlib.Path(rel).name
        shutil.copyfile(ROOT / rel, dest)
        staged.append(dest.name)
    for rel in fixture.data:
        shutil.copyfile(ROOT / rel, work / pathlib.Path(rel).name)

    # Pass A: the ports, before anything is generated from them.
    run(["yosys", "-q", "-l", "yosys-ports.log", "-p",
         f"read_verilog {' '.join(staged)}; hierarchy -check -top {fixture.top}; "
         f"proc; write_json ports.json"], work, work / "yosys-ports.cmd.log")
    ports = module_ports(json.loads((work / "ports.json").read_text()), fixture.top)

    (work / "cfg.v").write_text(wrapper_source(fixture.top, ports, params))

    mapping = (f"dfflibmap -liberty {liberty}; abc -liberty {liberty}; " if liberty else "")
    stat = f"tee -q -o stat.json stat -json{f' -liberty {liberty}' if liberty else ''}"
    run(["yosys", "-q", "-l", "yosys-synth.log", "-p",
         f"read_verilog cfg.v {' '.join(staged)}; "
         f"hierarchy -check -top {fixture.top}_cfg; proc; write_json elab.json; "
         f"synth -top {fixture.top}_cfg; {mapping}opt_clean -purge; {stat}; "
         f"write_verilog -noattr netlist.v; write_json netlist.json"],
        work, work / "yosys-synth.cmd.log")

    elaborated = json.loads((work / "elab.json").read_text())
    receipt = yosys_parameter_receipt(list(elaborated.get("modules", {})),
                                     fixture.top, params)
    stats = json.loads((work / "stat.json").read_text())
    design = stats.get("design", {})
    return {"ports": ports, "cells": design.get("num_cells"),
            "area": design.get("area"), "yosys_parameters": receipt,
            "netlist": json.loads((work / "netlist.json").read_text())}


def simulate(work: pathlib.Path, tb: str, tag: str,
             sources: list[str], outputs: list[str], extra: list[str],
             want_params: dict[str, int] | None) -> dict:
    (work / f"tb_{tag}.v").write_text(tb)
    binary = f"sim_{tag}.vvp"
    cmd = ["iverilog", "-g2012", "-o", binary]
    if want_params is None:
        cmd.append("-DNO_PARAM_RECEIPT")
    cmd += [f"tb_{tag}.v", *sources, *extra]
    run(cmd, work, work / f"iverilog-{tag}.log")
    stdout = run(["vvp", binary], work, work / f"vvp-{tag}.log")
    return parse_sim(stdout, outputs, want_params)


# ----------------------------------------------------------------------- main

def measure(fixture: Fixture, injection: Injection | None, outdir: pathlib.Path,
            liberty: str | None, baseline: bool) -> dict:
    require_tools("yosys", "yosys-config", "iverilog", "vvp")
    datdir = yosys_datdir()
    params = dict(fixture.params)
    if injection:
        params.update(injection.params)

    reset_port, _, polarity = fixture.reset.partition(":")
    reset_polarity = polarity or "low"
    clock = fixture.clock

    built = synthesise(outdir / "synth", fixture, params, liberty)
    ports = built["ports"]
    if ports.get(clock, {}).get("direction") != "input" or ports.get(clock, {}).get("width") != 1:
        raise Refused("no-clock", f"{fixture.top} has no 1-bit input port {clock!r}; "
                                  "this bench cannot exercise it")
    if ports.get(reset_port, {}).get("direction") != "input":
        raise Refused("no-reset",
                      f"{fixture.top} has no input port {reset_port!r}. An x at an "
                      "output of a design with no reset cannot be attributed to the "
                      "logic, so no area is quotable and none is given.")

    tb = tb_source(fixture.top, ports, clock, reset_port,
                   reset_polarity == "low", params, fixture.cycles)
    outputs = [n for n, p in ports.items() if p["direction"] == "output"]
    staged = [pathlib.Path(s).name for s in fixture.sources]
    work = outdir / "synth"
    rtl = simulate(work, tb, "rtl", ["cfg.v", *staged], outputs, [], params)
    gates = simulate(work, tb, "netlist", ["netlist.v"], outputs,
                     [str(datdir / "simcells.v"), str(datdir / "simlib.v")], None)
    static = constant_x_output_bits(built["netlist"], f"{fixture.top}_cfg")

    clean_cells = None
    if baseline and injection:
        clean_cells = synthesise(outdir / "baseline", fixture,
                                 dict(fixture.params), liberty)["cells"]

    def x_note(sim: dict) -> str:
        worst = max(sim["x_cycles"].items(), key=lambda kv: kv[1])
        total = sum(sim["x_cycles"].values())
        if not total:
            return f"no x at any output over {sim['samples']} sampled cycles"
        return (f"{worst[0]} is x on {worst[1]} of {sim['samples']} cycles, "
                f"first at cycle {sim['first_x']}")

    properties = {
        "rtl-simulation": {
            "moved": any(sim > 0 for sim in rtl["x_cycles"].values()),
            "note": x_note(rtl), "x_cycles": rtl["x_cycles"]},
        "netlist-simulation": {
            "moved": any(sim > 0 for sim in gates["x_cycles"].values()),
            "note": x_note(gates), "x_cycles": gates["x_cycles"]},
        "netlist-constant-x": {
            "moved": bool(static),
            "note": (f"constant x/z or undriven output bits: {static}" if static
                     else "every output bit has a driver and none is a constant x")},
    }
    state, reason = verdict(properties)
    # Not a detector, and reported as one so that it cannot be mistaken for one:
    # this is the quantity the whole tool exists to withhold.
    properties["cell-count"] = {
        "moved": False,
        "note": (f"{built['cells']:,} cells"
                 + (f" against {clean_cells:,} clean, "
                    f"{100 * (built['cells'] - clean_cells) / clean_cells:+.1f} %"
                    if clean_cells else "")
                 + " -- BLIND BY CONSTRUCTION: an area is not evidence of "
                   "correctness (docs/verification-rules.md rule 3)")}

    return {
        "state": state, "reason": reason,
        "fixture": fixture.top, "injection": None if not injection else {
            "name": next(k for k, v in INJECTIONS.items() if v is injection),
            "shipped_as": injection.shipped_as, "parameters": injection.params},
        "parameters": params,
        "yosys_parameters": built["yosys_parameters"],
        "cells": built["cells"] if state == "PASS" else None,
        "area_um2": built["area"] if state == "PASS" else None,
        "withheld": None if state == "PASS" else {
            "cells": built["cells"], "area_um2": built["area"],
            "why": "the design's outputs are X; this number is not an area of "
                   "anything that computes"},
        "baseline_cells": clean_cells,
        "properties": properties,
        "cycles": fixture.cycles,
        "work": str(outdir),
    }


def report(result: dict) -> None:
    print(f"report_synth_area: {result['state']} ({result['reason']}) "
          f"-- {result['fixture']}"
          + (f" +{result['injection']['name']}" if result["injection"] else " clean"))
    if result["state"] == "PASS":
        area = result["area_um2"]
        print(f"  area: {result['cells']:,} cells"
              + (f", {area:,.1f} um2" if area else " (no liberty: cell count only)"))
    else:
        print(f"  area: WITHHELD -- {result['withheld']['why']}")
        print(f"        (yosys reported {result['withheld']['cells']:,} cells for it)")
    width = max(len(n) for n in result["properties"])
    for name, prop in result["properties"].items():
        print(f"  {'MOVED' if prop['moved'] else 'BLIND':<5} {name:<{width}}  {prop['note']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fixture", choices=sorted(FIXTURES), default="tanh_dp")
    ap.add_argument("--top", help="report on this module instead of a fixture")
    ap.add_argument("--src", action="append", default=[], help="with --top")
    ap.add_argument("--data", action="append", default=[],
                    help="$readmemh data staged beside the sources")
    ap.add_argument("--param", action="append", default=[], metavar="P=N")
    ap.add_argument("--clock", default="clk")
    ap.add_argument("--reset", default="rst_n:low", metavar="PORT:low|high")
    ap.add_argument("--cycles", type=int, default=512)
    ap.add_argument("--inject", choices=sorted(INJECTIONS))
    ap.add_argument("--expect", choices=["area", "refused-x"])
    ap.add_argument("--liberty", help="report um2 as well as a cell count")
    ap.add_argument("--outdir", type=pathlib.Path)
    ap.add_argument("--no-baseline", action="store_true",
                    help="skip the clean synthesis an injected run compares against")
    args = ap.parse_args(argv)

    if args.top:
        fixture = Fixture(top=args.top, sources=tuple(args.src), data=tuple(args.data),
                          params={k: int(v) for k, v in
                                  (p.split("=", 1) for p in args.param)},
                          clock=args.clock, reset=args.reset, cycles=args.cycles)
    else:
        fixture = FIXTURES[args.fixture]
    injection = INJECTIONS[args.inject] if args.inject else None
    outdir = args.outdir or (ROOT / "build/pnr-area"
                             / f"{fixture.top}-{args.inject.lower() if args.inject else 'clean'}")
    outdir = pathlib.Path(outdir)
    if outdir.exists():
        shutil.rmtree(outdir)
    outdir.mkdir(parents=True)

    try:
        result = measure(fixture, injection, outdir, args.liberty, not args.no_baseline)
    except Refused as exc:
        result = {"state": "REFUSED", "reason": exc.reason, "detail": exc.detail,
                  "fixture": fixture.top, "injection": args.inject,
                  "properties": {}, "work": str(outdir)}
        (outdir / "report.json").write_text(json.dumps(result, indent=2) + "\n")
        print(f"report_synth_area: REFUSED ({exc.reason}) -- {exc.detail}")
        print("  nothing was measured: this is not a pass and not a failure")
        return REFUSED if args.expect != "area" or exc.reason != "outputs-are-x" \
            else EXPECTATION_UNMET

    (outdir / "report.json").write_text(json.dumps(result, indent=2) + "\n")
    report(result)
    code = exit_code(result["state"], result["reason"], args.expect)
    if args.expect:
        print(f"  --expect {args.expect}: "
              + {PASS: "met", EXPECTATION_UNMET: "NOT MET",
                 REFUSED: "NO VERDICT (nothing measured)"}[code])
    return code


if __name__ == "__main__":
    sys.exit(main())
