#!/usr/bin/env python3
"""summarize.py -- turn an ORFS run's per-stage metric JSONs into the tables in docs/pnr-first-run.md.

Usage: summarize.py <design> [flow_variant] [--work DIR]
Reads work/logs/gf180/<design>/<variant>/{1_synth,2_1_floorplan,3_5_place_dp,4_1_cts,5_2_route,6_report}.json,
reports/.../synth_stat.txt and (if present) logs/.../sta_corners.log. Prints markdown.

EXIT 0 report / 1 nothing to report / 2 REFUSED: the die area is not quotable,
or a required metric is duplicated with differing values in a stage metrics
file. Exit 2 is not a failure of the run; it means this tool will not present
a die/cell-area ratio -- or a number it can only attribute to emission order --
whose value it cannot attribute. `area_provenance.py`
decides that from the run's own config.mk or par_request.json and explains
itself in the output -- a `CORE_UTILIZATION` target makes the die a restatement
of the cell area, so the ratio only recovers 1/target (issue #245,
docs/pnr-synth-top.md section 2). Everything else in the report still prints:
routing, timing and the cell census are unaffected by where the die came from.

The duplicate half is issue #835, pinned as OpenROAD#12303: OpenROAD's metrics
writer serialises the entry list verbatim and dedups nothing, and two of its
commands emit the bare name `design__instance__area` with two definitions
(`report_cell_usage`'s fill-inclusive census total, then `odb::blockMetrics`'s
filler-excluded active area), so every standard ORFS finish stage writes the
key twice with different values and `json.load` silently keeps the last. The
same writer condition carries `detailedroute__route__drc_errors` twice (the
#2089 family). Reading a stage file asserts emission order rather than
assuming it: duplicates are recorded in the report, and a metric this report
presents, duplicated with differing values, takes the exit 2 REFUSED path --
the last-wins value still prints, marked unattributed.
"""
import json, re, sys, os, glob

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from area_provenance import die_area_provenance  # noqa: E402

#: stage -> metrics file, in the order ORFS writes them.
STAGE_FILES = [("synth", "1_synth"), ("floorplan", "2_1_floorplan"),
               ("place", "3_5_place_dp"), ("cts", "4_1_cts"),
               ("route", "5_2_route"), ("finish", "6_report")]

#: The metrics this report presents as numbers, as the suffixes pick()
#: resolves them. A duplicated key resolving to one of these is refused when
#: its values differ; anything else is recorded, not refused.
REQUIRED_METRIC_SUFFIXES = (
    "design__die__area", "design__core__area",
    "design__instance__count", "design__instance__area",
    "design__instance__utilization",
    "timing__setup__ws", "timing__hold__ws",
    "timing__setup__tns", "timing__hold__tns",
    "timing__drv__setup_violation_count", "timing__drv__hold_violation_count",
    "timing__drv__max_slew", "timing__drv__max_cap", "clock__skew__setup",
    "timing__fmax", "power__total",
    "route__wirelength", "route__drc_errors",
    "antenna__violating__nets", "antenna_diodes_count",
)


def load_metrics(p):
    """json.load's last-wins view plus every duplicated key's values in
    emission order -- emission order is the thing last-wins silently assumes,
    so a strict read records it instead. ({}, {}) for a missing file.

    The duplicate map is the document ROOT's. `object_pairs_hook` fires
    innermost-first, so the root is the hook's LAST call (`pairs[-1]`);
    `pairs[0]` is the root only while the document is flat, and on a nested
    document it reads some inner object -- reporting zero duplicates for the
    root while last-wins publishes its ambiguous number (test_summarize.py
    commits that defeating input as a control, docs/verification-rules.md
    rule 8)."""
    pairs = []

    def hook(pairs_list):
        pairs.append(pairs_list)
        return dict(pairs_list)

    try:
        with open(p) as f:
            data = json.load(f, object_pairs_hook=hook)
    except FileNotFoundError:
        return {}, {}
    seen = {}
    for k, v in pairs[-1]:
        seen.setdefault(k, []).append(v)
    return data, {k: vs for k, vs in seen.items() if len(vs) > 1}


def _differing(values):
    try:
        return len(set(values)) > 1
    except TypeError:
        return len({repr(v) for v in values}) > 1


def _presented(key):
    """pick()'s rule: suffix match, excluding the per-class/per-iter
    `...:name` keys pick can never return."""
    return (":" not in key.split("__")[-1]
            and any(key.endswith(s) for s in REQUIRED_METRIC_SUFFIXES))


def duplicate_refusals(dups):
    """[(key, values)] -- duplicated keys this report presents, with differing
    values. An identical duplicate is redundant, not ambiguous; a duplicated
    key the report never presents is not this report's number to refuse."""
    return [(k, vs) for k, vs in sorted(dups.items())
            if _presented(k) and _differing(vs)]

def pick(d, suffix):
    for k, v in d.items():
        if k.endswith(suffix) and ":" not in k.split("__")[-1]: return v
    return None

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    design = args[0]; variant = args[1] if len(args) > 1 else "base"
    work = sys.argv[sys.argv.index("--work") + 1] if "--work" in sys.argv else os.path.join(os.path.dirname(os.path.abspath(__file__)), "work")
    L = f"{work}/logs/gf180/{design}/{variant}"; R = f"{work}/reports/gf180/{design}/{variant}"
    st = {}; dups = {}
    for s, f in STAGE_FILES:
        st[s], dups[s] = load_metrics(f"{L}/{f}.json")
    fin = st["finish"]
    if not fin: print(f"no 6_report.json for {design}/{variant}"); return 1
    # The strict read: record every duplicate, refuse the presented-and-
    # differing ones, note the rest. Nothing below is inferred from the
    # duplicate's order -- the refusal is the order-dependence itself.
    dup_refusals, same_notes = [], []
    diff_note_rows = {}
    for s, f in STAGE_FILES:
        for k, vs in duplicate_refusals(dups.get(s, {})):
            dup_refusals.append((s, f, k, vs))
        for k, vs in sorted(dups.get(s, {}).items()):
            if _presented(k) and _differing(vs):
                continue
            if not _differing(vs):
                same_notes.append(f"`{k}` ({s}, {len(vs)}x identical)")
            else:
                n, ex = diff_note_rows.get((s, f), (0, k))
                diff_note_rows[(s, f)] = (n + 1, ex)
    die = pick(fin, "design__die__area"); core = pick(fin, "design__core__area")
    # die is square (aspect 1): side from area
    print(f"### {design} ({variant})\n")
    if dup_refusals or same_notes or diff_note_rows:
        print("- duplicate metric keys in the ORFS metrics JSON (the writer emits the entry list verbatim and dedups nothing; The-OpenROAD-Project/OpenROAD#12303):")
        for s, f, k, vs in dup_refusals:
            print(f"  - **REFUSED** (duplicate-metric): `{k}` is emitted {len(vs)}x in `{f}.json` ({s} stage) with differing values {vs}; a last-wins reader sees {vs[-1]}. The report below prints that last-wins value unchanged -- it is not attributed.")
        if same_notes:
            print(f"  - note: duplicate keys with identical values, benign under last-wins: " + ", ".join(same_notes))
        for (s, f), (n, ex) in sorted(diff_note_rows.items()):
            print(f"  - note: {n} further duplicate keys with differing values in `{f}.json` ({s} stage) are not presented by this report -- recorded, not refused -- e.g. `{ex}`")
    print("| Stage | instances | std-cell area (um2) | utilisation | setup WNS @tt (ns) |")
    print("|---|---|---|---|---|")
    for s in ["synth", "floorplan", "place", "cts", "finish"]:
        d = st[s]
        if not d: continue
        n = pick(d, "design__instance__count"); a = pick(d, "design__instance__area"); u = pick(d, "design__instance__utilization"); ws = pick(d, "timing__setup__ws")
        note = {"synth": "yosys+ABC, DONT_USE *_1", "floorplan": "+ 0 (taps/endcaps not counted as std cells)", "place": "+ input/output buffers, resizing", "cts": "+ clock tree", "finish": "incl. fillers/taps/endcaps in count; area = std cells only"}[s]
        print(f"| {s} ({note}) | {n} | {a:,.0f} | {'' if u is None else f'{u*100:.1f} %'} | {'' if ws is None else f'{ws:+.2f}'} |")
    print()
    cls = {k.split("class:")[1]: v for k, v in fin.items() if "instance__count__class:" in k}
    cla = {k.split("class:")[1]: v for k, v in fin.items() if "instance__area__class:" in k}
    print("| Cell class (final) | count | area (um2) |"); print("|---|---|---|")
    for c in sorted(cls, key=lambda c: -cla.get(c, 0)): print(f"| {c} | {cls[c]} | {cla.get(c,0):,.1f} |")
    print()
    rt = st["route"]
    # THE DIE AREA IS NOT PRINTED UNTIL ITS PROVENANCE IS KNOWN. A die sized from
    # a CORE_UTILIZATION target is the cell area divided by that target, so
    # `die / synth cell area` recovers the input and nothing else (issue #245).
    prov = die_area_provenance(work, design, variant,
                               synth_cell_area=pick(st["synth"], "design__instance__area"),
                               core_area=core, die_area=die)
    if prov.quotable:
        print(f"- die area **{die:,.0f} um2** ({die**0.5:.1f} x {die**0.5:.1f} um, aspect 1), core area **{core:,.0f} um2** -- an INPUT, per `{prov.source}`")
        print(f"- final std-cell area {pick(fin,'design__instance__area'):,.0f} um2 = {pick(fin,'design__instance__utilization')*100:.1f} % of core; die / synth cell area = {die/pick(st['synth'],'design__instance__area'):.2f}, core / synth cell area = {core/pick(st['synth'],'design__instance__area'):.2f}")
    else:
        print(f"- die area: **REFUSED** ({prov.reason}) -- not reported as a measurement, and no die/cell ratio is given.")
        print(f"  - provenance read from `{prov.source}`: {prov.detail}")
        if prov.corroboration:
            print(f"  - arithmetic: {prov.corroboration}")
        print(f"  - the numbers themselves, as INPUTS rather than results: die {die:,.0f} um2, core {core:,.0f} um2, final std-cell area {pick(fin,'design__instance__area'):,.0f} um2")
        if prov.utilisation_target is not None:
            print(f"  - the utilisation column above is therefore the TARGET at floorplan ({prov.utilisation_target*100:.0f} %), not a measurement; only the drift the resizer and CTS add to it is measured")
    print(f"- routed wirelength {pick(rt,'route__wirelength'):,} um, detailed-route DRC errors {pick(rt,'route__drc_errors')}, antenna violating nets {pick(rt,'antenna__violating__nets')}, antenna diodes {pick(rt,'antenna_diodes_count')}")
    print(f"- finish (tt_025C_5v00, RCX typ): setup WNS {pick(fin,'timing__setup__ws'):+.3f} ns, hold WNS {pick(fin,'timing__hold__ws'):+.3f} ns, TNS {pick(fin,'timing__setup__tns')}/{pick(fin,'timing__hold__tns')}, setup/hold violations {pick(fin,'timing__drv__setup_violation_count')}/{pick(fin,'timing__drv__hold_violation_count')}, max slew/cap violations {pick(fin,'timing__drv__max_slew')}/{pick(fin,'timing__drv__max_cap')}, clock skew {pick(fin,'clock__skew__setup'):.3f} ns, ORFS fmax {pick(fin,'timing__fmax')/1e6:.1f} MHz, power {pick(fin,'power__total')*1e3:.2f} mW")
    sta = f"{L}/sta_corners.log"
    if os.path.exists(sta):
        txt = open(sta).read()
        print("- multi-corner STA on the routed design (`sta-corners.tcl`, per-corner OpenRCX):")
        for m in re.finditer(r"=== CORNER (\w+) setup_wns_ns=(\S+) setup_tns_ns=(\S+) hold_wns_ns=(\S+) hold_tns_ns=(\S+) implied_min_period_ns=(\S+) implied_fmax_mhz=(\S+)", txt):
            c, sw, stn, hw, htn, mp, fm = m.groups()
            name = {"tt": "tt_025C_5v00", "ss": "ss_125C_4v50", "ff": "ff_n40C_5v50"}[c]
            print(f"  - **{name}**: setup WNS {sw} ns (TNS {stn}), hold WNS {hw} ns (TNS {htn}); implied min period {mp} ns ({fm} MHz)")
    ss = f"{R}/synth_stat.txt"
    if os.path.exists(ss):
        rows = [(int(m.group(1)), m.group(2)) for m in re.finditer(r"^\s+(\d+)\s+\S+\s+\d+\s+\S+\s+gf180mcu_fd_sc_mcu7t5v0__(\S+)", open(ss).read(), re.M)]
        rows.sort(reverse=True)
        print(f"- synth cell mix (top 8 of {len(rows)} types): " + ", ".join(f"{c}x {n}" for c, n in rows[:8]))
    return 2 if (dup_refusals or not prov.quotable) else 0

if __name__ == "__main__": sys.exit(main())
