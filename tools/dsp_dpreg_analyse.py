#!/usr/bin/env python3
"""Parse dsp_cells_dump.txt into per-cell DSP48E1 dispositions.

For each DPREG-4 cell:
  * mapped parameters (PREG/MREG/OPMODEREG/AREG/BREG/CREG/DREG/USE_DPORT)
  * per-OPMODE-bit driving net and driver cell
  * the reachable OPMODE value set: every distinct net on OPMODE pins takes
    values {0,1} (constants are fixed), so the reachable set is the product
    over distinct nets -- a worst-case superset of what the RTL can drive;
  * DPREG-4 is DISMISSED iff no reachable value selects P as an operand:
      X mux (OPMODE[1:0]) = 10, or Y mux (OPMODE[3:2]) = 10,
      or Z mux (OPMODE[6:4]) = 010   (UG479 tables 2-7..2-9);
  * P/PCOUT fanout (whether the unregistered P bus leaves the DSP at all).

The structural facts come from the routed checkpoint, not from RTL: if two
OPMODE pins are driven by the SAME net, their joint values are 00 or 11 --
10 is structurally impossible whatever drives the net.

Writes dsp-opmode-analysis.json and prints a per-cell verdict table.
"""

import json
import pathlib
import re
import sys

EVIDENCE = pathlib.Path(__file__).resolve().parents[1] / (
    "fpga/reports/arty/vivado-2025.1/dsp-dpreg-evidence")
DUMP = EVIDENCE / "dsp_cells_dump.txt"

# OPMODE subfield semantics per the vendor unisim model (Xilinx
# XilinxUnisimLibrary DSP48E1.v, commit-pinned in dsp-dpreg-evidence/unisim):
#   X (OPMODE[1:0]): 00=0 01=M 10=P 11=A:B
#   Y (OPMODE[3:2]): 00=0 01=0 10=ones(mask, not P) 11=C
#   Z (OPMODE[6:4]): 000=0 001=PCIN 010=P 011=C 100=P 101/11x=P-derived
# P is an operand only for X=10 or Z in {010,100,110,111}. UG479 tables
# 2-7..2-9 agree that those are the P-feedback selections.
X_MUX = {0: "0", 1: "M", 2: "P", 3: "A:B"}
Y_MUX = {0: "0", 1: "0", 2: "ones", 3: "C"}
Z_MUX = {0: "0", 1: "PCIN", 2: "P", 3: "C",
         4: "P", 5: "PCIN>>17", 6: "P>>17", 7: "P>>17"}


def parse_cells(text):
    cells = {}
    cur = None
    section = None
    for line in text.splitlines():
        m = re.match(r"^==== CELL (\S+) ====$", line)
        if m:
            cur = m.group(1)
            cells[cur] = {"name": cur, "props": {}, "pins": [],
                          "fanout": [], "cone": []}
            section = "props"
            continue
        if cur is None:
            continue
        if line.startswith("---- pins ----"):
            section = "pins"
            continue
        if line.startswith("---- P/PCOUT fanout ----"):
            section = "fanout"
            continue
        if line.startswith("---- control cones"):
            section = "cone"
            continue
        if line.startswith("==== END CELL"):
            cur = None
            continue
        if section == "props" and " = " in line:
            k, v = line.split(" = ", 1)
            cells[cur]["props"][k] = v
        elif section == "pins" and line.startswith("PIN "):
            m = re.match(
                r"^PIN (\S+) dir=(\S+) net=(\S*) driver=(.*)$", line)
            if m:
                cells[cur]["pins"].append(
                    {"pin": m.group(1), "dir": m.group(2),
                     "net": m.group(3), "driver": m.group(4)})
        elif section == "fanout" and line.startswith("FANOUT "):
            cells[cur]["fanout"].append(line)
        elif section == "cone":
            cells[cur]["cone"].append(line)
    return cells


def pin_map(cell):
    """pin base name -> list of (index, net, driver)."""
    out = {}
    for p in cell["pins"]:
        m = re.match(r"^([A-Za-z][A-Za-z0-9]*)(?:\[(\d+)\])?$", p["pin"])
        base, idx = m.group(1), m.group(2)
        out.setdefault(base, []).append(
            (int(idx) if idx is not None else None, p["net"], p["driver"]))
    return out


def bit_value(entry):
    """Constant net value, or None if driven by logic/register."""
    net, driver = entry[1], entry[2]
    if "GND" in driver:
        return 0
    if "VCC" in driver:
        return 1
    return None


def analyse(name, cell):
    pm = pin_map(cell)
    props = cell["props"]
    rec = {
        "name": name,
        "site": props.get("SITE"),
        "AREG": props.get("AREG"), "BREG": props.get("BREG"),
        "CREG": props.get("CREG"), "DREG": props.get("DREG"),
        "MREG": props.get("MREG"), "PREG": props.get("PREG"),
        "OPMODEREG": props.get("OPMODEREG"),
        "USE_DPORT": props.get("USE_DPORT"),
        "opmode_pins": [],
        "p_fanout_lines": cell["fanout"],
        "p_fanout_count": 0,
    }
    for f in cell["fanout"]:
        m = re.search(r"loads=(\d+)", f)
        if m:
            rec["p_fanout_count"] += int(m.group(1))

    op = pm.get("OPMODE", [])
    # distinct nets across the dynamic bits
    nets = {}
    for idx, net, driver in op:
        rec["opmode_pins"].append(
            {"bit": idx, "net": net, "driver": driver,
             "const": bit_value((idx, net, driver))})
        if bit_value((idx, net, driver)) is None:
            nets.setdefault(net, []).append(idx)

    # reachable OPMODE values: every distinct dynamic net ranges over {0,1}
    # (it is a register Q or a function of registers); constants are fixed.
    # This is a superset of what the RTL can reach, so a verdict over it is
    # conservative.
    netlist = sorted(nets)
    values = []
    for assign in range(1 << len(netlist)):
        val = {}
        for idx, net, driver in op:
            c = bit_value((idx, net, driver))
            if c is None:
                c = (assign >> netlist.index(net)) & 1
            val[idx] = c
        x = val.get(1, 0) * 2 + val.get(0, 0)
        y = val.get(3, 0) * 2 + val.get(2, 0)
        z = val.get(6, 0) * 4 + val.get(5, 0) * 2 + val.get(4, 0)
        values.append({"opmode": ''.join(str(val.get(i, 0))
                                         for i in range(6, -1, -1)),
                       "x": X_MUX[x], "y": Y_MUX[y], "z": Z_MUX[z]})
    rec["dynamic_nets"] = {n: nets[n] for n in netlist}
    rec["reachable_opmode_values"] = values
    rec["p_feedback_reachable"] = any(
        v["x"] == "P" or v["z"].startswith("P") for v in values)

    alumode = pm.get("ALUMODE", [])
    rec["alumode_consts"] = [
        {"bit": e[0], "net": e[1], "const": bit_value(e)} for e in alumode]
    rec["alumode_dynamic"] = any(bit_value(e) is None for e in alumode)
    rec["carryinsel_dynamic"] = any(
        bit_value(e) is None for e in pm.get("CARRYINSEL", []))

    # data/control port summaries for the record and the directed sim
    for port in ("A", "B", "C", "D"):
        entries = pm.get(port, [])
        consts = [bit_value(e) for e in entries]
        if entries and all(c is not None for c in consts):
            rec[f"{port}_source"] = "CONST " + ''.join(
                str(c) for c in reversed(consts))
        else:
            drivers = sorted({e[2].split("/")[-1] for e in entries})
            rec[f"{port}_source"] = "logic " + ",".join(drivers[:4])
    for pin in ("RSTA", "RSTB", "RSTC", "RSTD", "RSTM", "RSTP", "RSTCTRL",
                "CEA1", "CEA2", "CEB1", "CEB2", "CEC", "CEM", "CEP"):
        entries = pm.get(pin, [])
        if not entries:
            continue
        e = entries[0]
        rec[f"{pin}_net"] = e[1].split("/")[-1]
        rec[f"{pin}_driver"] = e[2].split("/")[-1]
    return rec


def main():
    cells = parse_cells(DUMP.read_text())
    target = [n for n in cells if n and "prod0__0" in n.split("/")[-1]
              or n and "p_1_out" in n.split("/")[-1]]
    target = [n for n in cells if re.search(r"(prod0__0|p_1_out(__\d+)?)$", n)]
    recs = [analyse(n, cells[n]) for n in target]
    out = EVIDENCE / "dsp-opmode-analysis.json"
    out.write_text(json.dumps(
        {"cells": recs, "source_dump": DUMP.name,
         "mux_tables": {"X": X_MUX, "Y": Y_MUX, "Z": Z_MUX}}, indent=2))

    print(f"{'cell':<58} {'PREG':>4} {'dyn nets on OPMODE':<24} "
          f"{'reachable Z mux':<22} verdict")
    all_clear = True
    for r in recs:
        zs = sorted({v["z"] for v in r["reachable_opmode_values"]})
        xs = sorted({v["x"] for v in r["reachable_opmode_values"]})
        ys = sorted({v["y"] for v in r["reachable_opmode_values"]})
        verdict = "P-FEEDBACK REACHABLE" if r["p_feedback_reachable"] \
            else "dismissed (P unreachable)"
        if r["p_feedback_reachable"]:
            all_clear = False
        short = r["name"].replace("u_synth/u_voice/", "")
        print(f"{short:<58} {r['PREG']:>4} "
              f"{str(r['dynamic_nets']):<24.24} "
              f"Z={','.join(zs)} X={','.join(xs)} Y={','.join(ys):<10} "
              f"{verdict}")
    print()
    for r in recs:
        short = r["name"].replace("u_synth/u_voice/", "")
        print(f"{short}: P_fanout_loads={r['p_fanout_count']} "
              f"ALUMODE_dynamic={r['alumode_dynamic']} "
              f"CARRYINSEL_dynamic={r['carryinsel_dynamic']}")
    print()
    print("VERDICT:", "UNSUPPORTED P-FEEDBACK REACHABLE -- STOP" if not all_clear
          else "all 13 cells: P-feedback unreachable on every reachable OPMODE")
    return 0 if all_clear else 2


if __name__ == "__main__":
    sys.exit(main())
