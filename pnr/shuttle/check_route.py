#!/usr/bin/env python3
"""check_route.py -- is the routed layout the design, or is it a plausible-looking
collapse?

    ./check_route.py census  [--out census.json]        # yosys, from the RTL
    ./check_route.py verify  <run-dir> --census census.json

WHY THIS EXISTS.  ``docs/pnr-synth-top.md`` records that this project quoted a
"1,917-cell" result three times before noticing every output was X: the
``ladder_dp_t16`` netlist had collapsed, yosys ``check`` reported 0 problems, and
the flow produced a clean-looking layout of almost nothing.  A DRC count and a
utilisation figure cannot distinguish that from a real result -- **both are
smaller and cleaner when the design has collapsed.**  The discriminator is
whether the flops the RTL declares are the flops the layout places, per block,
from two tools that share no code path.

  census  elaborates the RTL with yosys (``proc``, no flattening, no mapping) and
          counts the register bits each MODULE declares.  This number comes from
          the RTL, not from the P&R flow.
  verify  reads the run's own final DEF, buckets the placed sequential cells by
          the RTL hierarchy surviving in their instance names, and compares.

REFUSED (exit 3) is a first-class outcome and is NOT the same as a mismatch
(exit 1): it means this script could not obtain the evidence it needs, so its
silence must not read as a pass.

The padframe assertions are the other half, and they are the ones ``docs/dag.json``
node S2 turns on: a run that places **zero pad cells** is not a chip on a shuttle
padframe however clean it routes -- that is the exact text S2 was blocked with.
"""

import argparse
import collections
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))

REFUSED = 3

# RTL modules whose flop count is worth asserting.  Order is the report order.
CENSUS_MODULES = [
    "synth_top", "spi_ctl", "drum_regs", "drum_kit", "drum_dp", "modal_dp",
    "voice_dp", "recip_div", "ladder_dp_n", "i2s_tx",
]

# (regex on the DEF instance name, bucket label).  First match wins.  The paths
# are chip_top -> core (chip_core) -> u_synth (synth_top) -> ...  See
# pnr/orfs/blockarea.py for the same buckets one level shallower (ORFS ran
# synth_top as the top, this flow wraps it in the padframe).
BUCKETS = [
    (r"u_synth\.u_voice\.u_ladder\b", "u_voice.u_ladder (ladder_dp_n, NCH=2)"),
    (r"u_synth\.u_voice\.u_div\b",    "u_voice.u_div    (recip_div)"),
    (r"u_synth\.u_voice\b",           "u_voice          (own)"),
    (r"u_synth\.u_drums\.src\b",      "u_drums.src      (drum_dp)"),
    (r"u_synth\.u_drums\.bank\b",     "u_drums.bank     (modal_dp)"),
    (r"u_synth\.u_dregs\b",           "u_dregs          (drum_regs)"),
    (r"u_synth\.u_spi\b",             "u_spi            (spi_ctl)"),
    (r"u_synth\.u_i2s\b",             "u_i2s            (i2s_tx)"),
    (r"u_synth\b",                    "synth_top own"),
]

# gf180mcu_fd_sc_mcu7t5v0 sequential masters: dff*/sdff*/latch*.  Matching on the
# master name and not on the instance name matters -- an instance name proves
# nothing about what was placed.
SEQ_MASTER = re.compile(r"__(s?dff|dlh|dlm|latch)", re.I)
PAD_MASTER = re.compile(r"gf180mcu_(fd|ef)_io__", re.I)
WS_IP_MASTER = re.compile(r"gf180mcu_ws_ip__", re.I)


class Refusal(Exception):
    pass


# --------------------------------------------------------------------------- #
# census: from the RTL, with yosys
# --------------------------------------------------------------------------- #

# `-top synth_top` matters twice over: it drops the module variants nothing
# instantiates, and it elaborates each module at the PARAMETERS synth_top actually
# passes.  Without it yosys also keeps the default-parameter copy of every module,
# and drum_regs at default MODES is a different size from drum_regs as
# instantiated -- precisely the stale-number failure this check exists to catch
# (#33's acceptance criterion quoted 2,276 flops, the figure for the MODES=12
# revision that is no longer in the RTL).
# `memory` is not optional here and getting it wrong cost a wrong number once
# already: drum_regs declares its register map as `reg [25:0] a1 [0:MODES-1]`
# arrays, which `proc` leaves as `$mem`.  Without the `memory` pass the census
# reports drum_regs at ELEVEN flops -- the `stops` register and nothing else --
# and a layout placing the real three and a half thousand would read as a wild
# mismatch.  Wrong-then-right, caught by the count being absurd rather than by
# inspection.
CENSUS_SCRIPT = """
read_verilog -I {rtl} {files}
hierarchy -top synth_top -check
proc
memory
opt_clean
tee -o {out} stat -width
"""


def run_census(rtl_dir: str, out_json: str) -> dict:
    files = " ".join(os.path.join(rtl_dir, f"{m}.v") for m in CENSUS_MODULES)
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        stat_txt = os.path.join(td, "stat.txt")
        script = os.path.join(td, "census.ys")
        with open(script, "w", encoding="utf-8") as f:
            f.write(CENSUS_SCRIPT.format(rtl=rtl_dir, files=files, out=stat_txt))
        proc = subprocess.run(["yosys", "-q", "-s", script],
                              capture_output=True, text=True)
        if proc.returncode != 0:
            raise Refusal(f"yosys exited {proc.returncode}:\n{proc.stderr[-2000:]}")
        with open(stat_txt, encoding="utf-8") as f:
            stat = f.read()
    census = parse_yosys_stat(stat)
    missing = [m for m in CENSUS_MODULES if m not in census]
    if missing:
        raise Refusal("yosys reported no statistics for: " + ", ".join(missing))
    payload = {"source": "yosys stat -width on rtl-sketch; hierarchy -top synth_top, proc, memory, opt_clean; NOT flattened, NOT technology-mapped",
               "flops_declared_per_module": census}
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
        f.write("\n")
    return payload


SEQ_PRIM = ("dff", "adff", "sdff", "dffe", "adffe", "sdffe", "dffsr", "dffsre",
            "dlatch", "adlatch", "dlatchsr")

# `stat -width` prints `<count> $dff_<width>` -- so the BIT count is count x width,
# not count.  Reading the count alone under-reports every multi-bit register, which
# would make a real design look collapsed and a collapsed one look fine.
STAT_CELL = re.compile(r"^\s+(\d+)\s+\$(\w+?)(?:_(\d+))?\s*$")
# Parameterised modules appear as `$paramod$<hash>\<name>` (or `$paramod\<name>\...`).
# `stat` also emits a trailing `=== design hierarchy ===` section whose cell counts
# are the FLATTENED totals for the whole design.  It has to end the current module,
# not extend it: attributing it to the last module seen reported synth_top's own
# 29 flops as 12,572 -- the whole chip's total -- which is wrong in the direction
# that makes a collapsed netlist look fine.  A section name containing a space is
# never a module name, so that is the discriminator.
STAT_MODULE = re.compile(r"^=== (.+) ===$")


def module_base_name(raw: str) -> str:
    """`$paramod$c0ffee\\drum_regs` -> `drum_regs`; `\\i2s_tx` -> `i2s_tx`."""
    return raw.split("\\")[-1].split("$")[-1]


def parse_yosys_stat(text: str) -> dict:
    """Register BITS each module declares, from `stat -width`'s per-module sections.

    yosys emits generic `$dff`/`$sdff`/`$dffe`... cells before technology mapping,
    one cell per register of a given width, so bits = count x width.  Only the
    sequential primitives are counted; `$mem` is not (the ROM tables are read-only
    and map to logic, not to flops).
    """
    out: dict[str, int] = {}
    module = None
    for line in text.splitlines():
        m = STAT_MODULE.match(line.strip())
        if m:
            name = m.group(1)
            if " " in name:          # "design hierarchy" -- flattened totals, not a module
                module = None
                continue
            module = module_base_name(name)
            # Seed at zero: a module with no flops of its own (drum_kit is a pure
            # structural wrapper) must report 0, not be absent.  An absent entry
            # would make the caller REFUSE on a correct design.
            out.setdefault(module, 0)
            continue
        if module is None:
            continue
        m = STAT_CELL.match(line)
        if not m:
            continue
        count, prim, width = int(m.group(1)), m.group(2), m.group(3)
        if prim not in SEQ_PRIM:
            continue
        out[module] = out.get(module, 0) + count * (int(width) if width else 1)
    return {k: v for k, v in out.items() if k in CENSUS_MODULES}


# --------------------------------------------------------------------------- #
# verify: from the run's own DEF
# --------------------------------------------------------------------------- #

def find_final_def(run_dir: str) -> str:
    """The DEF of the last step that produced one.

    Deliberately NOT a fixed filename: LibreLane's step numbering shifts with
    --skip, and hard-coding a step number is how a checker ends up silently
    reading a placement DEF and calling it a route.
    """
    best = None
    for root, _dirs, files in os.walk(run_dir):
        for fn in files:
            if fn.endswith(".def"):
                p = os.path.join(root, fn)
                key = os.path.relpath(p, run_dir)
                if best is None or key > best[0]:
                    best = (key, p)
    if best is None:
        raise Refusal(f"no .def anywhere under {run_dir} -- there is no layout to check")
    return best[1]


def parse_def_components(path: str) -> list[tuple[str, str]]:
    """(instance, master) for every COMPONENT in the DEF."""
    comps: list[tuple[str, str]] = []
    inside = False
    with open(path, errors="ignore") as f:
        for line in f:
            s = line.strip()
            if s.startswith("COMPONENTS "):
                inside = True
                continue
            if s.startswith("END COMPONENTS"):
                break
            if inside and s.startswith("- "):
                parts = s.split()
                if len(parts) >= 3:
                    comps.append((parts[1], parts[2]))
    return comps


def bucket_of(inst: str) -> str:
    for rx, label in BUCKETS:
        if re.search(rx, inst):
            return label
    return "outside synth_top (padframe, wrapper, fill, tap)"


# LibreLane 3 carries the cumulative metrics inside each step's `state_out.json`
# under a "metrics" key.  There is no top-level metrics.json -- looking for one
# (as collect-evidence.py's HEADLINE table does) finds nothing and reports nothing,
# which is a silence that reads like "no violations".
def read_metrics(run_dir: str) -> tuple[str, dict]:
    """(step name, cumulative metrics) from the last step that recorded any."""
    best = None
    for name in sorted(os.listdir(run_dir)):
        if not name[:2].isdigit():
            continue
        p = os.path.join(run_dir, name, "state_out.json")
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8") as f:
            payload = json.load(f)
        if payload.get("metrics"):
            best = (name, payload["metrics"])
    if best is None:
        raise Refusal(f"no step under {run_dir} recorded metrics in state_out.json")
    return best


def verify(run_dir: str, census: dict, min_pads: int, expect_ws_ip: int) -> int:
    def_path = find_final_def(run_dir)
    comps = parse_def_components(def_path)
    if not comps:
        raise Refusal(f"{def_path} has no COMPONENTS section")

    seq = collections.Counter()
    pads = 0
    ws_ip = 0
    for inst, master in comps:
        if PAD_MASTER.search(master):
            pads += 1
        if WS_IP_MASTER.search(master):
            ws_ip += 1
        if SEQ_MASTER.search(master):
            seq[bucket_of(inst)] += 1

    declared = census["flops_declared_per_module"]
    placed_total = sum(seq.values())

    print(f"DEF:        {os.path.relpath(def_path, run_dir)}")
    print(f"components: {len(comps)}")
    print()
    print("placed sequential cells by block (measured, from the DEF)")
    print(f"  {'block':<40} {'flops':>8}")
    for label, n in sorted(seq.items(), key=lambda kv: -kv[1]):
        print(f"  {label:<40} {n:>8}")
    print(f"  {'total':<40} {placed_total:>8}")
    print()

    problems: list[str] = []

    # --- the discriminator ------------------------------------------------ #
    # drum_regs is the single best probe: it is the largest register file in the
    # design and every one of its bits is a declared reg, so a collapsed netlist
    # cannot produce the number by accident.
    want = declared.get("drum_regs")
    got = seq.get("u_dregs          (drum_regs)", 0)
    print(f"drum_regs flops: RTL declares {want}, layout places {got}")
    if want is None:
        problems.append("census has no drum_regs entry")
    elif got != want:
        problems.append(
            f"drum_regs places {got} flops, RTL declares {want}. "
            "A mismatch here is the collapse signature docs/pnr-synth-top.md records; "
            "it is not a rounding difference.")

    # --- S2's own assertion ----------------------------------------------- #
    print(f"pad cells:       {pads} (>= {min_pads} required)")
    if pads < min_pads:
        problems.append(
            f"{pads} pad cells placed. docs/dag.json node S2 was blocked with exactly "
            "'routed die has padcells: 0' -- a die with no padframe is not a chip on "
            "a shuttle slot however clean it routes.")
    print(f"wafer.space IP:  {ws_ip} (expected {expect_ws_ip})")
    if ws_ip != expect_ws_ip:
        problems.append(f"{ws_ip} gf180mcu_ws_ip__* macros placed, expected {expect_ws_ip} "
                        "(qrcode_id, shuttle_id, project_id, marker, logo are mandatory)")

    # --- the run's own metrics, reported not asserted ---------------------- #
    # Reported and not asserted on purpose: utilisation is the MEASURED quantity
    # here (the die and core are fixed inputs from the template's slot file), so
    # there is no threshold for this script to enforce.  A DRC or timing gate
    # belongs in the flow's own Checker steps, which `full` skips and names.
    try:
        step, metrics = read_metrics(run_dir)
    except Refusal as e:
        print(f"(no metrics: {e})")
    else:
        print(f"metrics as of step {step} (cumulative):")
        wanted = ["design__die__area", "design__core__area",
                  "design__instance__area__stdcell", "design__instance__count__stdcell",
                  "design__instance__count__class:sequential_cell",
                  "design__instance__count__padcells", "design__instance__count__macros",
                  "design__instance__utilization", "design__io",
                  "route__wirelength", "route__drc_errors", "route__antenna_violations",
                  "timing__setup__ws", "timing__hold__ws",
                  "timing__setup__tns", "power__total"]
        wanted += sorted(k for k in metrics
                         if k.startswith(("timing__setup__ws__corner", "timing__hold__ws__corner")))
        for k in wanted:
            if k in metrics:
                print(f"  {k:<58} {metrics[k]}")

    print()
    if problems:
        print("VERDICT: MISMATCH")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("VERDICT: the layout places the flops the RTL declares, on a populated padframe.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("census", help="count declared flops per module with yosys")
    c.add_argument("--rtl", default=os.path.join(REPO, "rtl-sketch"))
    c.add_argument("--out", default=os.path.join(HERE, "evidence", "flop-census.json"))

    v = sub.add_parser("verify", help="compare a run's DEF against the census")
    v.add_argument("run_dir")
    v.add_argument("--census", default=os.path.join(HERE, "evidence", "flop-census.json"))
    v.add_argument("--min-pads", type=int, default=1,
                   help="minimum placed pad cells; 0 disables the S2 assertion")
    v.add_argument("--expect-ws-ip", type=int, default=5)

    a = ap.parse_args(argv)
    try:
        if a.cmd == "census":
            os.makedirs(os.path.dirname(a.out), exist_ok=True)
            payload = run_census(a.rtl, a.out)
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 0
        if not os.path.exists(a.census):
            raise Refusal(f"{a.census} does not exist; run `check_route.py census` first")
        with open(a.census, encoding="utf-8") as f:
            census = json.load(f)
        return verify(a.run_dir, census, a.min_pads, a.expect_ws_ip)
    except Refusal as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return REFUSED


if __name__ == "__main__":
    sys.exit(main())
