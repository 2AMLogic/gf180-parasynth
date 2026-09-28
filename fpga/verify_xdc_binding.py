#!/usr/bin/env python3
"""Is this XDC the constraint file the CURRENT wrapper is built against? (#436)

    python3 fpga/verify_xdc_binding.py                       # 0 pass, 1 fail, 2 refused
    python3 fpga/verify_xdc_binding.py --outdir fpga/reports/arty/xdc-binding
    python3 fpga/verify_xdc_binding.py --matrix               # properties x defects
    python3 fpga/verify_xdc_binding.py --inject UART_SLASH_JOIN --expect-fail

WHY THIS EXISTS. `tools/check_arty_evidence_binding.py --scope publication`
asks whether the evidence bound to the Arty wrapper covers the live bytes of
everything a BITSTREAM depends on -- which includes its pin constraints. Until
this bench existed it could only REFUSE, because no committed record in this
repository had ever hashed fpga/boards/arty-a7-100.xdc: the UART digital bench
(fpga/verify_uart_bridge.py) drives no physical pin, so it correctly hashes
sources()+roms() and nothing else, and the only records carrying the XDC are
publication records, which are history by construction (#421, #436).

The answer is NOT to widen the digital bench's record. A record that claims
coverage of bytes its bench never read is the failure `docs/failure-modes.md`
calls "the verified artifact was not the shipped artifact", with the sign
flipped. The constraint file needs its OWN evidence, from a check that
actually reads it -- and that is what this is.

WHAT IT CHECKS, AND WHY EACH IS FALSIFIABLE RATHER THAN BOOKKEEPING. Nine
named properties, every one a cross-check between the XDC and something
maintained independently of it -- the wrapper's RTL, the PCM5102 datasheet
budget derived in fpga/ext_io_timing.py, or the required match counts declared
in fpga/xdc_bindings.EXPECT. None of them can be satisfied by editing a hash:

  ports_declared     every get_ports name is a port of arty_a7_top, index in
                     range. A get_ports that matches nothing is DROPPED by
                     Vivado with a CRITICAL WARNING and the build succeeds.
  ports_constrained  every bit of every wrapper port carries PACKAGE_PIN and
                     IOSTANDARD. An unpinned port is placed wherever Vivado
                     likes.
  hier_names         every hierarchical name the XDC references -- generate
                     blocks, instances, inferred flops, nets, pins -- exists
                     in the compiled RTL.
  hier_separators    a generate block is joined to what is inside it with a
                     DOT and an instance with a SLASH. THIS IS #315, THE
                     DEFECT THAT SHIPPED: `.*g_uart/u_uart/rx_q_reg...` never
                     matched `u_synth/g_uart.u_uart`, so both UART-RX
                     synchroniser constraints were silently dropped from the
                     R0 and R1 bitstreams. Until now only Vivado could see it
                     (fpga/xdc_bindings.tcl_assertions, at build time); this
                     sees it in 0.2 s, from the sources.
  clock_names        every `-clock <name>` is a clock this XDC creates, or a
                     hierarchical net that exists in the RTL.
  query_counts       every `-hier -regexp` object query has a REQUIRED match
                     count declared in fpga/xdc_bindings.EXPECT (#315).
  exception_model    fpga/xdc_bindings.expected_exceptions() models every
                     exception kind in the file, so the publisher's
                     reconciliation cannot silently ignore one.
  output_delay_budget  every set_output_delay equals the budget derived in
                     fpga/ext_io_timing.py from the PCM5102 datasheet and the
                     core clock -- not "a constraint exists", the VALUE.
  uart_gate          the UART ports carry their pins, output delay,
                     synchroniser evidence and recorded disposition.

WHAT IT IS NOT. It is not the build's constraint check and does not replace
it. Only Vivado can say how many objects a query MATCHED on the elaborated
netlist, which is what fpga/xdc_bindings.tcl_assertions asserts at build time
and fpga/xdc_bindings.check_report re-checks at publish time. This bench
answers the question those two cannot answer without a 40-minute Vivado run:
does this constraint file still refer to THIS wrapper? A record from it is
therefore constraint-BINDING evidence, not constraint-EFFECT evidence, and
the record says so in its own `scope` field.

START RED, ON A DEFECT THAT ACTUALLY SHIPPED. The pre-#315 constraint file is
in this repository's history, and it is the bytes R0 and R1 were routed
against:

    git show 383f10b^:fpga/boards/arty-a7-100.xdc > /tmp/pre315.xdc
    python3 fpga/verify_xdc_binding.py --xdc /tmp/pre315.xdc
    #   FAIL hier_separators: XDC line 42: `g_uart` is a generate block ...
    #   exit 1

That is an external counterexample -- a real defect, found by someone else,
not one this bench's author imagined -- and `--inject UART_SLASH_JOIN`
reinstates it permanently as a control (docs/verification-rules.md rule 5).

THE RECORD, AND WHY IT IS REGENERATED IN PLACE. `--outdir` writes binding.json
(the verdict, the nine properties, and the sha256 of every byte the check
read) plus binding.txt (its transcript, hashed into the record). It is not a
superseded-run directory like reports/arty/rev14-clean: no published image
cites it, so there is nothing to preserve and it is refreshed in place
whenever the XDC or a compiled source moves. Refreshing it is one command and
0.2 s -- and it CANNOT be refreshed by editing the file, because a record is
only written when all nine properties pass on the bytes named in it.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_arty as build          # noqa: E402
import ext_io_timing as iotime      # noqa: E402
import xdc_bindings as xb           # noqa: E402

ROOT = build.ROOT
WRAPPER = "arty_a7_top"

# wrapper top -> the constraint-binding evidence bound to it, exactly as
# fpga/publish_arty.VERIFICATION_BY_WRAPPER binds the digital proof. It lives
# HERE, beside the bench that produces it, so a consumer reads the binding off
# the producer rather than restating it (tools/check_arty_evidence_binding.py
# does the same for both maps).
CONSTRAINT_BY_WRAPPER = {
    WRAPPER: ROOT / "fpga/reports/arty/xdc-binding/binding.json",
}

RECORD = "binding.json"
TRANSCRIPT = "binding.txt"

SCOPE = ("Static constraint-BINDING check of the Arty XDC against the wrapper "
         "RTL it constrains, the datasheet-derived output-delay budget and the "
         "declared object-query counts. No Vivado, no netlist: this says the "
         "constraints still refer to this tree's design, NOT how many objects "
         "they matched -- that is the build's constraint_matches.rpt "
         "(fpga/xdc_bindings.py) and is not claimed here.")

PROPERTIES = ("ports_declared", "ports_constrained", "hier_names",
              "hier_separators", "clock_names", "query_counts",
              "exception_model", "output_delay_budget", "uart_gate")

# Vivado's pin names on an inferred flop. A hierarchical reference ending in
# one of these names a PIN of the cell before it, not a cell.
FLOP_PINS = {"D", "Q", "C", "CE", "R", "S", "CLR", "PRE"}


class Refused(ValueError):
    """The check cannot be made -- distinct from the check failing."""


# ---------------------------------------------------------------- RTL model
_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
_GENERATE = re.compile(r"\bbegin\s*:\s*([A-Za-z_]\w*)")
_INSTANCE = re.compile(r"([A-Za-z_]\w*)\s*(\()\s*\.")
_DECL = re.compile(r"\b(reg|wire)\b\s*(?:\[[^\]]*\]\s*)?([A-Za-z_]\w*"
                   r"(?:\s*,\s*[A-Za-z_]\w*)*)\s*[;=]")


def strip_comments(text: str) -> str:
    return _COMMENT.sub(" ", text)


def rtl_model(sources) -> dict:
    """{generates, instances, regs, wires, pins_of} over the compiled RTL.

    Names only: this is a name resolver for constraint paths, not an
    elaborator, and it says so rather than implying it knows the hierarchy."""
    generates, instances, regs, wires = set(), set(), set(), set()
    pins_of: dict[str, set] = {}
    for path in sources:
        text = strip_comments(path.read_text())
        generates |= set(_GENERATE.findall(text))
        for match in _INSTANCE.finditer(text):
            name = match.group(1)
            instances.add(name)
            # the instance's own connection list: `.PIN(` up to the matching
            # close paren, used to check a pin reference names a real pin
            depth, start = 0, match.start(2)
            i = start
            while i < len(text):
                if text[i] == "(":
                    depth += 1
                elif text[i] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            pins_of.setdefault(name, set()).update(
                re.findall(r"\.([A-Za-z_]\w*)\s*\(", text[start:i]))
        for kind, names in _DECL.findall(text):
            target = regs if kind == "reg" else wires
            target.update(n.strip() for n in names.split(","))
    # a keyword can never be an instance name; `if (` and `always @(` do not
    # match _INSTANCE (no leading `.`), but a macro or task might
    instances -= {"if", "else", "case", "for", "while", "always", "initial",
                  "generate", "assign", "module", "function", "task"}
    return {"generates": generates, "instances": instances, "regs": regs,
            "wires": wires, "pins_of": pins_of}


def module_ports(text: str, top: str) -> dict:
    """{port name: width} for `top`'s header. Raises Refused if absent."""
    text = strip_comments(text)
    match = re.search(r"\bmodule\s+" + re.escape(top) + r"\b", text)
    if not match:
        raise Refused(f"module {top} is not in the wrapper source")
    i = match.end()
    # skip an optional parameter list
    while i < len(text) and text[i] not in "(;":
        i += 1
    if i < len(text) and text[i] == "(" and re.match(r"\s*#", text[match.end():i + 1]):
        depth = 0
        while i < len(text):
            depth += text[i] == "("
            depth -= text[i] == ")"
            i += 1
            if depth == 0:
                break
        while i < len(text) and text[i] != "(":
            i += 1
    if i >= len(text) or text[i] != "(":
        raise Refused(f"module {top} has no port list")
    depth, start = 0, i
    while i < len(text):
        depth += text[i] == "("
        depth -= text[i] == ")"
        i += 1
        if depth == 0:
            break
    header = text[start + 1:i - 1]
    ports, direction, width = {}, None, 1
    item_re = re.compile(
        r"^\s*(?:(input|output|inout)\s+)?(?:(?:wire|reg|logic)\s+)?"
        r"(?:\[\s*(\d+)\s*:\s*(\d+)\s*\]\s*)?([A-Za-z_]\w*)\s*$")
    for item in header.split(","):
        match = item_re.match(item.replace("\n", " "))
        if not match:
            raise Refused(f"module {top}'s port list has an item this parser "
                          f"does not model: {item.strip()!r}")
        this_dir, high, low, name = match.groups()
        if this_dir:
            direction = this_dir
            width = abs(int(high) - int(low)) + 1 if high is not None else 1
        elif direction is None:
            raise Refused(f"module {top}'s port list starts without a direction")
        ports[name] = width
    return ports


# ------------------------------------------------------- XDC object queries
_PORT_TOKEN = r"[A-Za-z_]\w*(?:\[(?:\d+|\*)\])?"
_GET_PORTS = re.compile(r"get_ports\s+(\{[^}]*\}|" + _PORT_TOKEN + r")")
_PACKAGE_PIN = re.compile(r"set_property\s+PACKAGE_PIN\s+\S+\s+"
                          r"\[get_ports\s+(\{[^}]*\}|\S+?)\s*\]")
_IOSTANDARD = re.compile(r"set_property\s+IOSTANDARD\s+\S+\s+"
                         r"\[get_ports\s+(\{[^}]*\}|\S+?)\s*\]")
_CREATED_CLOCK = re.compile(r"create_(?:generated_)?clock\s+-name\s+(\S+)")
_USED_CLOCK = re.compile(r"-clock\s+(\S+)")
_LITERAL_PATH = re.compile(r"get_pins\s+(?!-)([A-Za-z_][\w./]*(?:\[\d+\])?)")


def port_spec_bits(spec: str, ports: dict) -> tuple[set, list]:
    """(the port bits a get_ports spec names, the names that are not ports)."""
    bits, unknown = set(), []
    for token in spec.strip("{} ").split():
        match = re.fullmatch(r"([A-Za-z_]\w*)(?:\[(\d+|\*)\])?", token)
        if not match or match.group(1) not in ports:
            unknown.append(token)
            continue
        name, index = match.group(1), match.group(2)
        width = ports[name]
        if index is None or index == "*":
            bits |= {f"{name}[{i}]" for i in range(width)} if width > 1 else {name}
        elif int(index) >= width:
            unknown.append(token)
        else:
            bits.add(f"{name}[{index}]" if width > 1 else name)
    return bits, unknown


def all_bits(ports: dict) -> set:
    out = set()
    for name, width in ports.items():
        out |= {f"{name}[{i}]" for i in range(width)} if width > 1 else {name}
    return out


def _expand_alternation(name: str) -> list:
    """`(sck_q|mosi_q|csn_q)_reg` -> the three concrete names."""
    match = re.search(r"\(([^)]*)\)", name)
    if not match:
        return [name]
    return [name[:match.start()] + choice + name[match.end():]
            for choice in match.group(1).split("|")]


def path_atoms(pattern: str, regexp: bool = True) -> list:
    """[(separator, name)] for a hierarchical constraint path.

    `separator` is "" for the first atom, "/" for an instance boundary and
    "." for a generate-block boundary -- the distinction #315 turned on. A
    leading `.*` (the -hier wildcard) is dropped; index and character classes
    are stripped from each name.

    `regexp` is not cosmetic: in a -regexp query a generate-block dot is
    WRITTEN `\\.` and a bare `.` is the any-character metacharacter, while in
    a literal path (`get_pins hardware_clock.mmcm/CLKOUT0`, a `-clock` name)
    the dot is itself. Parsing one as the other is how a checker would report
    the pre-#315 file as fine."""
    pattern = pattern.strip()
    if not regexp:
        atoms, sep = [], ""
        for token in re.split(r"([./])", re.sub(r"\[\d+\]$", "", pattern)):
            if token in "./":
                sep = token
            elif token:
                atoms.append((sep, token))
        return atoms
    if pattern.startswith(".*"):
        pattern = pattern[2:]
    atoms, name, sep = [], "", ""
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if char == "\\" and i + 1 < len(pattern):
            if pattern[i + 1] == ".":
                atoms.append((sep, name))
                name, sep = "", "."
                i += 2
                continue
            if pattern[i + 1] == "[":          # an escaped index: stop the name
                depth = 0
                while i < len(pattern):
                    if pattern[i:i + 2] == "\\[":
                        depth += 1
                        i += 2
                        continue
                    if pattern[i:i + 2] == "\\]":
                        depth -= 1
                        i += 2
                        if depth == 0:
                            break
                        continue
                    i += 1
                continue
            i += 2
            continue
        if char == "/":
            atoms.append((sep, name))
            name, sep = "", "/"
            i += 1
            continue
        if char == "[":                         # an unescaped index
            depth = 0
            while i < len(pattern):
                depth += pattern[i] == "["
                depth -= pattern[i] == "]"
                i += 1
                if depth == 0:
                    break
            continue
        name += char
        i += 1
    atoms.append((sep, name))
    return [(s, n) for s, n in atoms if n]


def xdc_references(xdc_text: str) -> list:
    """[(line, kind, path, regexp)] for every hierarchical object the XDC
    names -- the -hier -regexp queries AND the literal get_pins paths and the
    hierarchical clock names, which are the same class of reference and were
    not covered by #315's build-time assertion."""
    out = []
    created = set(_CREATED_CLOCK.findall(xdc_text))
    for n, line in enumerate(xdc_text.splitlines(), 1):
        code = line.split("#", 1)[0]
        for kind, rx in xb._QUERY.findall(code):
            out.append((n, kind, rx, True))
        for literal in _LITERAL_PATH.findall(code):
            out.append((n, "pins", literal, False))
        for clock in _USED_CLOCK.findall(code):
            if clock not in created and ("." in clock or "/" in clock):
                out.append((n, "net", clock, False))
    return out


# ------------------------------------------------------------- the properties
def check(xdc_text: str, wrapper_text: str, model: dict) -> dict:
    """{property: [problems]} -- empty lists everywhere is a pass."""
    found = {name: [] for name in PROPERTIES}
    ports = module_ports(wrapper_text, WRAPPER)

    # 1/2: the ports
    named, constrained = set(), {"PACKAGE_PIN": set(), "IOSTANDARD": set()}
    for n, line in enumerate(xdc_text.splitlines(), 1):
        code = line.split("#", 1)[0]
        for spec in _GET_PORTS.findall(code):
            bits, unknown = port_spec_bits(spec, ports)
            named |= bits
            for token in unknown:
                found["ports_declared"].append(
                    f"XDC line {n}: get_ports {{{token}}} is not a port of "
                    f"{WRAPPER} (its ports are {sorted(ports)})")
        for key, regex in (("PACKAGE_PIN", _PACKAGE_PIN),
                           ("IOSTANDARD", _IOSTANDARD)):
            for spec in regex.findall(code):
                constrained[key] |= port_spec_bits(spec, ports)[0]
    for key, bits in constrained.items():
        for missing in sorted(all_bits(ports) - bits):
            found["ports_constrained"].append(
                f"{WRAPPER} port {missing} has no {key} in the XDC")

    # 3/4: the hierarchical references
    for n, kind, path, regexp in xdc_references(xdc_text):
        atoms = path_atoms(path, regexp)
        if not atoms:
            found["hier_names"].append(f"XDC line {n}: {kind} reference "
                                       f"{{{path}}} names nothing")
            continue
        for index, (sep, raw) in enumerate(atoms):
            last = index == len(atoms) - 1
            for name in _expand_alternation(raw):
                _check_atom(found, n, kind, path, name, sep, index, last, model,
                            atoms)

    # 5: clock names that are hierarchical nets are covered above; the rest
    # must be created in this file
    created = set(_CREATED_CLOCK.findall(xdc_text))
    for n, line in enumerate(xdc_text.splitlines(), 1):
        for clock in _USED_CLOCK.findall(line.split("#", 1)[0]):
            if clock in created or "." in clock or "/" in clock:
                continue
            found["clock_names"].append(
                f"XDC line {n}: -clock {clock} is neither created in this file "
                f"{sorted(created)} nor a hierarchical net reference")

    # 6/7: the declared match counts and the modelled exception kinds
    try:
        xb.object_queries(xdc_text)
    except xb.Refused as exc:
        found["query_counts"].append(str(exc))
    try:
        xb.expected_exceptions(xdc_text)
    except xb.Refused as exc:
        found["exception_model"].append(str(exc))

    # 8/9: the datasheet-derived budget and the UART gate
    found["output_delay_budget"] += iotime.xdc_contract_drift(xdc_text)
    found["uart_gate"] += iotime.uart_gate_drift(xdc_text)
    return found


def _check_atom(found, n, kind, path, name, sep, index, last, model, atoms):
    """One segment of one hierarchical reference: does the name exist, and is
    it joined to the one before it the way the RTL joins it?"""
    is_generate = name in model["generates"]
    is_instance = name in model["instances"]
    pin_position = last and kind == "pins"
    cell_position = (last and kind == "cells") or (
        kind == "pins" and index == len(atoms) - 2)

    # #315: the separator BEFORE this atom says what the one before it was.
    # Checked for every atom, including the pin, so no position is exempt.
    if index:
        previous = atoms[index - 1][1]
        was_generate = any(c in model["generates"]
                           for c in _expand_alternation(previous))
        was_instance = any(c in model["instances"]
                           for c in _expand_alternation(previous))
        if sep == "/" and was_generate and not was_instance:
            found["hier_separators"].append(
                f"XDC line {n}: `{previous}` is a generate block, which Vivado "
                f"joins to `{name}` with a DOT, not a slash -- {{{path}}} "
                f"matches nothing and Vivado DROPS the constraint (this is "
                f"#315, which shipped dead in R0 and R1)")
        if sep == "." and was_instance and not was_generate:
            found["hier_separators"].append(
                f"XDC line {n}: `{previous}` is an instance, which Vivado joins "
                f"to `{name}` with a SLASH, not a dot -- {{{path}}} matches "
                f"nothing")

    if pin_position:
        previous = atoms[index - 1][1] if index else ""
        owners = set()
        for candidate in _expand_alternation(previous):
            owners |= model["pins_of"].get(candidate, set())
        if name not in FLOP_PINS and name not in owners:
            found["hier_names"].append(
                f"XDC line {n}: {{{path}}} names pin {name} of {previous}, "
                f"which is neither an inferred-flop pin {sorted(FLOP_PINS)} "
                f"nor a pin of that instance")
        return

    if cell_position:
        base = re.sub(r"_reg$", "", name)
        if not (is_instance or base in model["regs"] or name in model["wires"]
                or base in model["wires"]):
            found["hier_names"].append(
                f"XDC line {n}: {{{path}}} names cell {name}, which is neither "
                f"an instance nor an inferred flop of a declared reg "
                f"(looked for `{base}`)")
    elif not (is_generate or is_instance):
        if kind == "net" and last:
            if name not in model["wires"] and name not in model["regs"]:
                found["hier_names"].append(
                    f"XDC line {n}: clock {path} ends in {name}, which is not a "
                    f"net declared in the compiled RTL")
            return
        found["hier_names"].append(
            f"XDC line {n}: {{{path}}} names {name}, which is neither a "
            f"generate block nor an instance in the compiled RTL")


# -------------------------------------------------------------- injections
def _sub_once(text, old, new, label):
    if old not in text:
        raise Refused(f"injection {label} cannot be applied: {old!r} is not in "
                      "the constraint file -- the control would prove nothing")
    return text.replace(old, new)


INJECTIONS = {
    "UART_SLASH_JOIN": (
        "#315 exactly as it shipped: the UART-RX synchroniser constraints "
        "join the generate block with a slash, match nothing, and Vivado "
        "drops them (dead in R0 and R1)",
        lambda t: _sub_once(t, r"g_uart\.u_uart", "g_uart/u_uart",
                            "UART_SLASH_JOIN")),
    "CLOCK_SOURCE_SLASH": (
        "the generated clock's source pin joins the MMCM's generate block "
        "with a slash, so i2s_bclk_ext has no source",
        lambda t: _sub_once(t, "hardware_clock.mmcm/CLKOUT0",
                            "hardware_clock/mmcm/CLKOUT0", "CLOCK_SOURCE_SLASH")),
    "PORT_TYPO": (
        "the UART RX pin constraint names a port the wrapper does not have",
        lambda t: _sub_once(t, "PACKAGE_PIN A9 [get_ports uart_rxd]",
                            "PACKAGE_PIN A9 [get_ports uart_rx]", "PORT_TYPO")),
    "LEAF_TYPO": (
        "the synchroniser flop is misspelt, so the ASYNC_REG property and the "
        "false path are applied to nothing",
        lambda t: _sub_once(t, "rx_q_reg", "rx_r_reg", "LEAF_TYPO")),
    "OUTPUT_DELAY_DRIFT": (
        "an I2S setup budget is loosened by 1 ns against the PCM5102 "
        "datasheet figure ext_io_timing derives",
        lambda t: _sub_once(t, "-max 8.200 [get_ports i2s_sdata]",
                            "-max 9.200 [get_ports i2s_sdata]",
                            "OUTPUT_DELAY_DRIFT")),
    "CLOCK_NAME_TYPO": (
        "an output delay is referred to a clock name this file never creates, "
        "so the constraint is applied against nothing",
        lambda t: _sub_once(t, "-clock i2s_bclk_ext -max 8.200",
                            "-clock i2s_bclk_extra -max 8.200",
                            "CLOCK_NAME_TYPO")),
    "UNMODELLED_EXCEPTION": (
        "an exception kind fpga/xdc_bindings.expected_exceptions() does not "
        "model is added, so the publisher's reconciliation of the routed "
        "design's exceptions would silently ignore it",
        lambda t: t + "set_multicycle_path -setup 2 -from [get_ports spi_sck]\n"),
    "UNDECLARED_QUERY": (
        "a new object query is added without declaring its required match "
        "count in fpga/xdc_bindings.EXPECT",
        lambda t: t + "set_property ASYNC_REG TRUE [get_cells -hier -regexp "
                      r"{.*u_mystery/guess_q_reg\[0\]}]" + "\n"),
}


# ------------------------------------------------------------------ reporting
def render(found: dict, header: str) -> str:
    lines = [header]
    for name in PROPERTIES:
        problems = found[name]
        lines.append(f"  {name:<20s} {'ok' if not problems else 'FAIL'}")
        lines += [f"      {p}" for p in problems]
    total = sum(len(v) for v in found.values())
    lines.append(f"  {'PASS' if not total else 'FAIL'}: {total} problem(s) "
                 f"across {len(PROPERTIES)} properties")
    return "\n".join(lines)


def matrix(xdc_text, wrapper_text, model) -> tuple[str, bool]:
    """docs/verification-rules.md rule 4: a multi-property suite reports what
    it is BLIND to, not only what failed. One row per injected defect, MOVED
    for the properties that saw it and BLIND for the ones that did not."""
    clean = check(xdc_text, wrapper_text, model)
    lines = ["properties x defects (MOVED = this property saw it, BLIND = it "
             "could not)", ""]
    width = max(len(n) for n in INJECTIONS)
    lines.append(" " * (width + 2) + "  ".join(p[:9].ljust(9) for p in PROPERTIES))
    ok = not any(clean.values())
    if not ok:
        lines.append("REFUSED: the clean run does not pass, so no row below "
                     "means anything")
    for name, (_why, mutate) in sorted(INJECTIONS.items()):
        try:
            found = check(mutate(xdc_text), wrapper_text, model)
        except Refused as exc:
            lines.append(f"{name.ljust(width)}  REFUSED: {exc}")
            ok = False
            continue
        cells = []
        moved = 0
        for prop in PROPERTIES:
            saw = len(found[prop]) > len(clean[prop])
            moved += saw
            cells.append(("MOVED" if saw else "BLIND").ljust(9))
        lines.append(f"{name.ljust(width)}  " + "  ".join(cells))
        if not moved:
            lines.append(f"  DEFECT: {name} moved NO property -- it is not a "
                         "control, it is a no-op")
            ok = False
    lines.append("")
    lines.append("every injected defect moved at least one property"
                 if ok else "AN INJECTION DID NOT BEHAVE AS NAMED")
    return "\n".join(lines), ok


def row(found, clean) -> str:
    cells = [(("MOVED" if len(found[p]) > len(clean[p]) else "BLIND") + f" {p}")
             for p in PROPERTIES]
    return "  " + "\n  ".join(cells)


# --------------------------------------------------------------- the record
def validate_record(path: Path) -> dict:
    """The committed constraint record, or Refused. A consumer that reads a
    record without this cannot tell a clean run from an injected one."""
    try:
        record = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise Refused(f"missing or unreadable constraint record {path}") from exc
    if not isinstance(record, dict):
        raise Refused(f"constraint record {path} is not an object")
    if record.get("state") != "PASS" or record.get("exit_code") != 0:
        raise Refused(f"constraint record {path} is not a clean pass "
                      f"({record.get('state')})")
    if record.get("inject") is not None:
        raise Refused(f"constraint record {path} is an INJECTED run "
                      f"({record['inject']}), not evidence")
    if record.get("xdc_override") is not None:
        raise Refused(f"constraint record {path} was made against "
                      f"{record['xdc_override']}, not the tree's constraint file")
    if record.get("wrapper") != WRAPPER:
        raise Refused(f"constraint record {path} is for wrapper "
                      f"{record.get('wrapper')!r}, not {WRAPPER!r}")
    properties = record.get("properties")
    if not isinstance(properties, dict) or set(properties) != set(PROPERTIES):
        raise Refused(f"constraint record {path} does not carry this bench's "
                      f"nine properties (it has "
                      f"{sorted(properties) if isinstance(properties, dict) else properties})")
    if any(properties[name] for name in PROPERTIES):
        raise Refused(f"constraint record {path} records a property with "
                      "problems but claims PASS")
    transcript = Path(path).with_name(TRANSCRIPT)
    if not transcript.is_file() or build.sha(transcript) != record.get("transcript_sha256"):
        raise Refused(f"constraint record {path}'s transcript is absent or changed")
    if not isinstance(record.get("source_sha256"), dict):
        raise Refused(f"constraint record {path} hashes nothing it read")
    return record


def read_files(xdc: Path) -> list:
    """Every file the check reads, in the order it reads them. The record
    hashes exactly this set -- nothing it did not read, and nothing it did."""
    return [xdc] + list(build.sources())


def answered_files() -> list:
    """The files a constraint record is entitled to answer for: the
    constraint file, and the compiled sources the resolver resolved its names
    against. It is NOT entitled to answer for the ROMs, which it never opens."""
    return [build.XDC] + list(build.sources())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--xdc", type=Path, default=None,
                        help="check other constraint bytes (a start-red or a "
                             "historical file); a record made this way is "
                             "marked and consumers refuse it")
    parser.add_argument("--outdir", type=Path, default=None,
                        help="write binding.json + binding.txt here")
    parser.add_argument("--inject", choices=sorted(INJECTIONS), default=None)
    parser.add_argument("--expect-fail", action="store_true",
                        help="0 when the injected defect is caught")
    parser.add_argument("--list-injections", action="store_true")
    parser.add_argument("--matrix", action="store_true",
                        help="run every injection and print the "
                             "properties x defects matrix")
    args = parser.parse_args(argv)

    if args.list_injections:
        for name, (why, _) in sorted(INJECTIONS.items()):
            print(f"{name}\n    {why}")
        return 0

    xdc_path = (args.xdc or build.XDC).resolve()
    wrapper = ROOT / "fpga/rtl/arty_a7_top.v"
    try:
        if not xdc_path.is_file():
            raise Refused(f"{xdc_path} is not a file")
        xdc_text = xdc_path.read_text()
        wrapper_text = wrapper.read_text()
        model = rtl_model(build.sources())
        if args.matrix:
            text, ok = matrix(xdc_text, wrapper_text, model)
            print(text)
            return 0 if ok else 1
        clean = check(xdc_text, wrapper_text, model)
        found = clean
        if args.inject:
            found = check(INJECTIONS[args.inject][1](xdc_text), wrapper_text,
                          model)
    except Refused as exc:
        print(f"REFUSED: {exc}")
        return 2

    header = (f"constraint binding: {xdc_path.relative_to(ROOT) if xdc_path.is_relative_to(ROOT) else xdc_path}"
              f" vs {WRAPPER}" + (f"  [inject {args.inject}]" if args.inject else ""))
    text = render(found, header)
    print(text)
    if args.inject:
        print("properties x this defect (rule 4):")
        print(row(found, clean))
    failed = any(found.values())

    if args.outdir:
        if args.inject or args.expect_fail:
            print("REFUSED: an injected run is not evidence and is never "
                  "written as a record")
            return 2
        args.outdir.mkdir(parents=True, exist_ok=True)
        (args.outdir / TRANSCRIPT).write_text(text + "\n")
        record = {
            "state": "FAIL" if failed else "PASS",
            "exit_code": 1 if failed else 0,
            "inject": None,
            "wrapper": WRAPPER,
            "scope": SCOPE,
            "xdc_override": (None if args.xdc is None
                             else str(xdc_path)),
            "properties": found,
            "source_sha256": {
                (str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)):
                build.sha(p) for p in read_files(xdc_path)},
            "transcript_sha256": build.sha(args.outdir / TRANSCRIPT),
        }
        (args.outdir / RECORD).write_text(json.dumps(record, indent=2) + "\n")
        print(f"wrote {args.outdir / RECORD}")

    if args.expect_fail:
        if failed:
            print("EXPECTED FAIL: the control was caught")
            return 0
        print("NOT CAUGHT: the injected defect moved no property")
        return 1
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
