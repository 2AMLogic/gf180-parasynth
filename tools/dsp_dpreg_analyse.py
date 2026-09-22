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

The REQUIRED target set is derived at analysis time from the bound DRC
report (drc.rpt), never from the dump's contents and never hardcoded: a
dump is only ever asked about the instances this drc.rpt flags.

REFUSAL (docs/failure-modes.md: REFUSED is a first-class outcome). The
analyser answers only when its apparatus is in a state to answer; any of
these prints "NO VERDICT: <reason>" and exits 3, distinct from a verdict:

  * missing input (dump, manifest, drc.rpt, names file, hash record)
  * hash of any manifest-listed input mismatched (incl. the dump itself)
  * drc.rpt identity drift: grep -n DPREG of drc.rpt != drc_dpreg_names.txt
    (the bytes recorded in drc_rpt.sha256 are NOT byte-reproducible --
    report_drc embeds a run timestamp -- so the DPREG-4 body of the local
    report is bound through the names file instead, which the box manifest
    hash-pins)
  * DRC summary-table count != parsed DPREG-4 body count
  * required (flagged) instance missing from the dump
  * duplicate cell header in the dump
  * extra dump cell with a dynamic (non-constant) OPMODE -- a cell the DRC
    would have flagged; constant-OPMODE siblings are dumped deliberately
    as context and stay allowed
  * a required cell missing a mandatory property, or missing any of its
    seven OPMODE pins -- a cell with no OPMODE pins is a DEFECT, not
    OPMODE 0000000, and is refused rather than dismissed

The simulator's clean-pass predicate carries the matching refusal: a trace
shorter than the stimulus is refused (see dsp_dpreg_sim.py).

Writes dsp-opmode-analysis.json and prints a per-cell verdict table.
Exit codes: 0 all required cells dismissed; 2 P-feedback reachable on at
least one required cell; 3 NO VERDICT (refused).
"""

import hashlib
import json
import pathlib
import re
import sys

EVIDENCE = pathlib.Path(__file__).resolve().parents[1] / (
    "fpga/reports/arty/vivado-2025.1/dsp-dpreg-evidence")
DUMP = EVIDENCE / "dsp_cells_dump.txt"
DRC_RPT = EVIDENCE.parent / "drc.rpt"

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

MANDATORY_PROPS = ("SITE", "AREG", "BREG", "CREG", "DREG",
                   "MREG", "PREG", "OPMODEREG", "USE_DPORT")
OPMODE_BITS = frozenset(range(7))


class Refused(Exception):
    """The apparatus is not in a state to answer. NO VERDICT, not a pass."""


def sha256(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def verify_manifest(evidence):
    """Every file the evidence manifest lists must exist and hash-match."""
    man = evidence / "MANIFEST.sha256"
    if not man.exists():
        raise Refused(f"missing evidence manifest: {man.name}")
    checked = 0
    for line in man.read_text().splitlines():
        if not line.strip():
            continue
        h, name = line.split(None, 1)
        name = name.strip().lstrip("*")
        p = evidence / name
        if not p.exists():
            raise Refused(f"manifest lists missing input: {name}")
        if sha256(p) != h:
            raise Refused(f"hash mismatch for {name}: recorded {h}")
        checked += 1
    if checked == 0:
        raise Refused("evidence manifest lists no files")


def verify_drc_identity(evidence, drc):
    """The local drc.rpt must carry the DPREG-4 findings the box recorded.

    report_drc embeds a run timestamp, so the box-side bytes (recorded in
    drc_rpt.sha256) are not reproducible; the DPREG-4 *body* is bound via
    the grep-identity file the box manifest hash-pins.
    """
    names = evidence / "drc_dpreg_names.txt"
    if not names.exists():
        raise Refused("missing drc_dpreg_names.txt")
    if not drc.exists():
        raise Refused(f"missing bound DRC report: {drc}")
    grep = "".join(f"{i}:{l}\n"
                   for i, l in enumerate(drc.read_text().splitlines(), 1)
                   if "DPREG" in l)
    if grep != names.read_text():
        raise Refused(
            "drc.rpt identity drift: grep -n DPREG of the local report "
            "does not match drc_dpreg_names.txt (the report the extraction "
            "was run against)")
    return drc


def parse_drc_targets(drc_text):
    """DPREG-4 instance names, in report order, from the DRC body."""
    table = re.search(
        r"^\|\s*DPREG-4\s*\|\s*Warning\s*\|.*?\|\s*(\d+)\s*\|",
        drc_text, re.M)
    if not table:
        raise Refused("drc.rpt has no DPREG-4 summary-table row")
    blocks = re.findall(
        r"^DPREG-4#(\d+) Warning\n.*?^The DSP48E1 cell (\S+) "
        r"with the given dynamic OPMODE",
        drc_text, re.M | re.S)
    if not blocks:
        raise Refused("no DPREG-4 violations parsed from drc.rpt")
    ids = [int(i) for i, _ in blocks]
    if ids != list(range(1, len(ids) + 1)):
        raise Refused(f"DPREG-4 violation ids not contiguous: {ids}")
    names = [n for _, n in blocks]
    if len(set(names)) != len(names):
        raise Refused("drc.rpt flags the same instance twice")
    if int(table.group(1)) != len(names):
        raise Refused(
            f"drc.rpt summary says {table.group(1)} DPREG-4 violations "
            f"but the body lists {len(names)}")
    return names


def parse_cells(text):
    cells = {}
    cur = None
    section = None
    for line in text.splitlines():
        m = re.match(r"^==== CELL (\S+) ====$", line)
        if m:
            cur = m.group(1)
            if cur in cells:
                raise Refused(f"duplicate cell header in dump: {cur}")
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


def validate_required_cell(name, cell):
    """A required (DRC-flagged) cell must be complete enough to analyse."""
    pm = pin_map(cell)
    op = pm.get("OPMODE", [])
    if not op:
        raise Refused(
            f"required cell {name}: no OPMODE pins in the extraction -- "
            f"a defect of the apparatus, not OPMODE 0000000")
    idxs = sorted(i for i, _, _ in op)
    if len(idxs) != len(set(idxs)):
        raise Refused(f"required cell {name}: duplicate OPMODE pin index")
    if set(idxs) != OPMODE_BITS:
        raise Refused(
            f"required cell {name}: OPMODE pins {idxs} != all seven bits "
            f"{sorted(OPMODE_BITS)}")
    missing = [k for k in MANDATORY_PROPS
               if not (cell["props"].get(k) or "").strip()]
    if missing:
        raise Refused(
            f"required cell {name}: missing mandatory properties "
            f"{missing}")


def analyse(name, cell):
    pm = pin_map(cell)
    props = cell["props"]
    op = pm.get("OPMODE", [])
    if not op:
        raise Refused(
            f"{name}: no OPMODE pins -- refusing rather than treating the "
            f"cell as OPMODE 0000000")
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


def refuse_extra_dynamic_cells(cells, required):
    """A non-required dump cell with a dynamic OPMODE is DRC-flag-shaped
    evidence from some other build; constant-OPMODE siblings are dumped
    deliberately as context and stay allowed."""
    for name, cell in cells.items():
        if name in required:
            continue
        pm = pin_map(cell)
        if any(bit_value(e) is None for e in pm.get("OPMODE", [])):
            raise Refused(
                f"extra dump cell with dynamic OPMODE, flagged by no "
                f"DPREG-4 in drc.rpt: {name}")


def run():
    evidence = EVIDENCE
    dump = DUMP
    drc = DRC_RPT
    verify_manifest(evidence)
    verify_drc_identity(evidence, drc)
    if not dump.exists():
        raise Refused(f"missing extraction dump: {dump}")
    required = parse_drc_targets(drc.read_text())

    cells = parse_cells(dump.read_text())
    if not cells:
        raise Refused("dump parses to zero cells")
    missing = [n for n in required if n not in cells]
    if missing:
        raise Refused(
            f"dump is missing {len(missing)} DPREG-4-flagged instance(s) "
            f"required by drc.rpt, e.g. {missing[:3]}")

    # a non-required dump cell with a dynamic OPMODE is DRC-flag-shaped
    # evidence from some other build; see refuse_extra_dynamic_cells
    refuse_extra_dynamic_cells(cells, required)

    for n in required:
        validate_required_cell(n, cells[n])

    recs = [analyse(n, cells[n]) for n in required]
    out = evidence / "dsp-opmode-analysis.json"
    out.write_text(json.dumps(
        {"cells": recs, "source_dump": dump.name,
         "required_source": "drc.rpt DPREG-4 violations (parsed at "
                            "analysis time)",
         "required_instances": required,
         "dump_sha256": sha256(dump),
         "drc_identity": {
             "grep_identity": "drc_dpreg_names.txt == grep -n DPREG "
                              "drc.rpt",
             "box_rpt_sha256_recorded": (evidence / "drc_rpt.sha256")
             .read_text().split()[0],
             "note": "report_drc embeds a run timestamp, so box bytes are "
                     "not reproducible; the DPREG-4 body is bound via the "
                     "manifest-pinned names file"},
         "mux_tables": {"X": X_MUX, "Y": Y_MUX, "Z": Z_MUX}}, indent=2))

    print(f"required set: {len(required)} DPREG-4 instance(s) from "
          f"{drc.name} (identity: drc_dpreg_names.txt)")
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
          else f"all {len(required)} cells: P-feedback unreachable on "
               f"every reachable OPMODE")
    return 0 if all_clear else 2


def main():
    try:
        return run()
    except Refused as e:
        print(f"NO VERDICT: {e}")
        return 3


if __name__ == "__main__":
    sys.exit(main())
