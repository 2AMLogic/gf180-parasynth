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

# A FLOP IS BUCKETED BY THE NET ITS Q DRIVES, NOT BY ITS INSTANCE NAME.
#
# WRONG-THEN-RIGHT, and it was a false RED.  The first version of this matched the
# RTL hierarchy in DEF *instance* names, the way pnr/orfs/blockarea.py does.  Against
# this flow's DEF it bucketed all 12,275 placed sequential cells into "outside
# synth_top", reported drum_regs as placing ZERO flops, and printed the collapse
# signature -- for a layout that is completely fine.  The netlist is flattened before
# placement, so every instance is an auto-name (`_141690_`); no hierarchy survives
# there at all.  It survives in NET names (`i_chip_core.u_synth.u_drums....`), which
# is why the bucketing keys on them.
#
# A checker that cries collapse at a healthy design is not "safely conservative": it
# is the thing that trains a reader to skip the check.
DEF_TOP = r"i_chip_core\.u_synth\."
BUCKETS = [
    (DEF_TOP + r"u_voice\.u_ladder\b", "u_voice.u_ladder (ladder_dp_n, NCH=2)"),
    (DEF_TOP + r"u_voice\.u_div\b",    "u_voice.u_div    (recip_div)"),
    (DEF_TOP + r"u_voice\b",           "u_voice          (voice_dp + submodules)"),
    (DEF_TOP + r"u_drums\.src\b",      "u_drums.src      (drum_dp)"),
    (DEF_TOP + r"u_drums\.bank\b",     "u_drums.bank     (modal_dp)"),
    (DEF_TOP + r"u_drums\b",           "u_drums          (drum_kit)"),
    (DEF_TOP + r"u_spi\b",             "u_spi            (spi_ctl)"),
    (DEF_TOP + r"u_i2s\b",             "u_i2s            (i2s_tx)"),
]
DREGS_BUCKET = "u_dregs          (drum_regs)"
SYNTH_OWN = "synth_top own"
OUTSIDE = "outside synth_top (padframe, wrapper, fill, tap)"

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


DEF_NET_PIN = re.compile(r"\(\s*([^\s()]+)\s+([^\s()]+)\s*\)")


def parse_def_nets(path: str):
    """Yield (net name, [(instance, pin), ...]) for every net in the DEF.

    DEF wraps a long net over many lines and ends it with ';', so the statement is
    accumulated rather than read line by line -- a per-line parse silently drops the
    connections of exactly the high-fanout nets that matter most.
    """
    inside = False
    buf = ""
    with open(path, errors="ignore") as f:
        for line in f:
            s = line.strip()
            if s.startswith("NETS "):
                inside = True
                continue
            if s.startswith("END NETS"):
                break
            if not inside:
                continue
            buf = s if s.startswith("- ") else (buf + " " + s)
            if not s.endswith(";") or not buf.startswith("- "):
                continue
            name = buf[2:].split()[0]
            yield name, DEF_NET_PIN.findall(buf)
            buf = ""


# drum_regs' own internal net names do NOT survive synthesis: its register map is
# declared as `reg [25:0] a1 [0:MODES-1]` arrays, which yosys' `memory` pass rewrites
# into flops with generated names.  What does survive is the net each of those flops
# DRIVES -- synth_top's `d_a1`, `d_path`, ... wires, which are exactly u_dregs' output
# ports (rtl-sketch/synth_top.v, the `u_dregs (` instantiation).
#
# So the drum_regs bucket is an OUTPUT-NAME attribution, not a hierarchy path, and
# that distinction is worth stating: it counts the flops whose output is a bit of the
# register file, which for a register file is the same set.  The port list is read
# back out of the RTL rather than typed here, so renaming a port cannot silently
# shrink the bucket into a false pass.
DREGS_INST = re.compile(r"\bdrum_regs\b[^;]*?\bu_dregs\s*\((.*?)\)\s*;", re.S)
PORT_CONN = re.compile(r"\.\s*\w+\s*\(\s*([A-Za-z_]\w*)\s*\)")


def dregs_output_nets(synth_top_v: str) -> set:
    """The synth_top-scope net names u_dregs drives, read from the RTL."""
    try:
        with open(synth_top_v, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        raise Refusal(f"cannot read {synth_top_v}: {e}")
    m = DREGS_INST.search(text)
    if not m:
        raise Refusal(f"no `drum_regs ... u_dregs (...)` instantiation in {synth_top_v}; "
                      "the bucketing cannot be derived from the RTL")
    # clk/rst_n/wr_* are u_dregs INPUTS and are driven from elsewhere; a flop on them
    # is not a drum_regs flop.  Everything else on this instance is an output.
    inputs = {"clk", "rst_n", "rst_n_drum", "wr_valid", "wr_drum", "wr_addr", "wr_data"}
    nets = {n for n in PORT_CONN.findall(m.group(1)) if n not in inputs}
    if not nets:
        raise Refusal(f"u_dregs in {synth_top_v} has no output connections")
    return nets


def net_stem(net: str) -> str:
    r"""`i_chip_core.u_synth.d_a1\[0\]` -> `d_a1`.

    DEF escapes the bracket of a bit-select inside a hierarchical name, so the name is
    literally `d_a1\[0\]`.  Splitting on '[' without removing the escapes leaves a
    trailing backslash on every stem -- which silently sent all 3,520 drum_regs flops
    into the "synth_top own" bucket and kept the drum_regs row at zero, i.e. it
    preserved the false collapse report through the FIRST attempt at this fix.
    """
    s = net.replace("\\", "").split("[")[0]
    prefix = "i_chip_core.u_synth."
    return s[len(prefix):] if s.startswith(prefix) else s


def bucket_of(net: str, dregs_nets: set) -> str:
    """Which block a flop belongs to, from the name of the net its Q drives."""
    clean = net.replace("\\", "")
    for rx, label in BUCKETS:
        if re.search(rx, clean):
            return label
    if net_stem(net) in dregs_nets:
        return DREGS_BUCKET
    if clean.startswith("i_chip_core.u_synth."):
        return SYNTH_OWN
    return OUTSIDE


def flops_by_block(def_path: str, dregs_nets: set) -> collections.Counter:
    """{bucket: placed sequential cells}, each flop counted once, by its Q net."""
    masters = dict(parse_def_components(def_path))
    seq = {i for i, mstr in masters.items() if SEQ_MASTER.search(mstr)}
    counted: set = set()
    out: collections.Counter = collections.Counter()
    for name, conns in parse_def_nets(def_path):
        for inst, pin in conns:
            if pin in ("Q", "QN") and inst in seq and inst not in counted:
                counted.add(inst)
                out[bucket_of(name, dregs_nets)] += 1
    # A sequential cell whose Q drives nothing is still placed; not counting it would
    # make the totals disagree with design__instance__count__class:sequential_cell for
    # a reason no reader could find.
    for inst in seq - counted:
        out["placed but Q drives no net"] += 1
    return out


# LibreLane 3 carries the cumulative metrics inside each step's `state_out.json`
# under a "metrics" key.  There is no top-level metrics.json -- looking for one
# (as collect-evidence.py's HEADLINE table does) finds nothing and reports nothing,
# which is a silence that reads like "no violations".
def metrics_by_step(run_dir: str) -> list[tuple[str, dict]]:
    """[(step name, cumulative metrics)] in step order, for steps that recorded any."""
    out = []
    for name in sorted(os.listdir(run_dir)):
        if not name[:2].isdigit():
            continue
        p = os.path.join(run_dir, name, "state_out.json")
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8") as f:
            payload = json.load(f)
        if payload.get("metrics"):
            out.append((name, payload["metrics"]))
    return out


def read_metrics(run_dir: str) -> tuple[str, dict]:
    """(step name, cumulative metrics) from the last step that recorded any."""
    steps = metrics_by_step(run_dir)
    if not steps:
        raise Refusal(f"no step under {run_dir} recorded metrics in state_out.json")
    return steps[-1]


# LibreLane's metrics are CUMULATIVE: every step's state_out.json carries forward every
# key any earlier step set.  So a key the flow stopped updating stays in the final
# payload, looking exactly like a fresh measurement.
#
# THIS IS NOT HYPOTHETICAL AND IT NEARLY SHIPPED FROM THIS DIRECTORY.  In the run this
# checker was written against, `timing__setup__ws__corner:nom_ss_125C_4v50` reads
# -178.5 ns in the LAST step's metrics.  It was written ONCE, by step 12,
# `OpenROAD.STAPrePNR` -- an unplaced, unrouted netlist with an ideal clock -- and is
# byte-identical in every step after it, because the mid-PnR STA steps update only the
# un-suffixed `timing__setup__ws`.  Read naively, that run reports a post-route slow
# corner missing by more than two clock periods.  It has no post-route slow corner at
# all.  Reporting one would be a fabricated measurement with a real key next to it.
def metric_source_step(run_dir: str) -> dict:
    """{metric key: name of the last step whose metrics CHANGED that key}."""
    src: dict[str, str] = {}
    prev: dict = {}
    for name, m in metrics_by_step(run_dir):
        for k, v in m.items():
            if k not in prev or prev[k] != v:
                src[k] = name
        prev = m
    return src


ROUTE_STEP = re.compile(r"detailedrouting", re.I)


def post_route_keys(run_dir: str) -> set:
    """Keys last written by a step that ran AFTER detailed routing began.

    Empty if the run has not reached detailed routing -- in which case nothing in it
    is a post-route measurement and a caller must not present anything as one.
    """
    steps = [n for n, _ in metrics_by_step(run_dir)]
    route_idx = max((i for i, n in enumerate(steps) if ROUTE_STEP.search(n)), default=None)
    if route_idx is None:
        return set()
    after = set(steps[route_idx:])
    return {k for k, s in metric_source_step(run_dir).items() if s in after}


def verify(run_dir: str, census: dict, min_pads: int, expect_ws_ip: int) -> int:
    def_path = find_final_def(run_dir)
    comps = parse_def_components(def_path)
    if not comps:
        raise Refusal(f"{def_path} has no COMPONENTS section")

    pads = 0
    ws_ip = 0
    for inst, master in comps:
        if PAD_MASTER.search(master):
            pads += 1
        if WS_IP_MASTER.search(master):
            ws_ip += 1

    dregs_nets = dregs_output_nets(os.path.join(REPO, "rtl-sketch", "synth_top.v"))
    seq = flops_by_block(def_path, dregs_nets)

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
    got = seq.get(DREGS_BUCKET, 0)
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

    mx = sub.add_parser(
        "metrics", help="final metrics + the step that wrote each, as committable evidence")
    mx.add_argument("run_dir")
    mx.add_argument("--out", default=os.path.join(HERE, "evidence", "halfslot-metrics.json"))

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
        if a.cmd == "metrics":
            # The whole run directory is gigabytes and is gitignored, so the numbers in
            # docs/pnr-shuttle-halfslot.md would otherwise have no committed source.
            # This is the small file that backs them -- and it carries the WRITING STEP
            # per key, because a value without that cannot be told from one the flow
            # stopped updating thirty steps ago.
            step, metrics = read_metrics(a.run_dir)
            src = metric_source_step(a.run_dir)
            post = post_route_keys(a.run_dir)
            payload = {
                "run": os.path.basename(os.path.abspath(a.run_dir)),
                "last_step_with_metrics": step,
                "steps": [n for n, _ in metrics_by_step(a.run_dir)],
                "note": ("`written_by` is the last step whose metrics CHANGED the key; "
                         "LibreLane's metrics are cumulative, so a key not in "
                         "`post_route_keys` is not a post-route measurement however "
                         "current it looks."),
                "post_route_keys": sorted(post),
                "metrics": {k: {"value": v, "written_by": src.get(k)}
                            for k, v in sorted(metrics.items())},
            }
            os.makedirs(os.path.dirname(a.out), exist_ok=True)
            with open(a.out, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=1, sort_keys=True)
                f.write("\n")
            print(f"wrote {a.out}: {len(metrics)} metrics from {step}, "
                  f"{len(post)} of them post-route")
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
