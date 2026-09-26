#!/usr/bin/env python3
"""report_halfslot.py -- fill docs/pnr-shuttle-halfslot.md from a LibreLane run.

    ./report_halfslot.py <run-dir> [--doc docs/pnr-shuttle-halfslot.md] [--check]

The tables in that document are GENERATED, not typed.  Transcribing a metric by
hand is how ``docs/capability-dag.md`` came to carry 3,232 where the RTL says
3,520, and how #33's acceptance criterion came to carry 2,276; this repository has
enough of those.  Every cell below traces to a key in the run's own
``state_out.json['metrics']`` or to ``check_route.py``'s census, and the key is
printed in the table so a reader can go and look.

``--check`` regenerates into memory and exits non-zero if the document on disk
disagrees, so a stale document is a failure and not a surprise.

REFUSED (exit 3) if the run has not produced the metric a section needs.  A
section that silently renders empty reads as "no violations".
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_route as cr   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
REFUSED = 3

CORNERS = ["nom_tt_025C_5v00", "nom_ss_125C_4v50", "nom_ff_n40C_5v50"]
CLOCK_PERIOD_NS = 81.38
# docs/pnr-synth-top.md, the two-quarter-slot ORFS route (#36 / PR #40).  Quoted for
# comparison only; nothing on this page is derived from it.
ORFS_UTILISATION_PCT = 60.1


def num(v, nd=0):
    if v is None:
        return "—"
    if isinstance(v, float) and nd == 0 and abs(v - round(v)) < 1e-9:
        v = int(round(v))
    if isinstance(v, int):
        return f"{v:,}"
    return f"{v:,.{nd}f}"


def row(label, key, metrics, nd=0, unit="", note=""):
    if key not in metrics:
        return None
    return f"| {label} | {num(metrics[key], nd)}{unit} | `{key}` |{note}"


def area_section(m: dict) -> str:
    need = ["design__die__area", "design__core__area",
            "design__instance__area__stdcell", "design__instance__utilization"]
    missing = [k for k in need if k not in m]
    if missing:
        raise cr.Refusal("the run has no " + ", ".join(missing) +
                         " -- it has not reached a floorplan")
    util = m["design__instance__utilization"]
    lines = [
        "| quantity | value | metric key |",
        "|---|---:|---|",
        f"| die area — **INPUT** | {num(m['design__die__area'])} µm² | `design__die__area` |",
        f"| core area — **INPUT** | {num(m['design__core__area'])} µm² | `design__core__area` |",
        f"| standard-cell area | {num(m['design__instance__area__stdcell'])} µm² "
        "| `design__instance__area__stdcell` |",
        f"| **utilisation of the core — MEASURED** | **{util * 100:.2f} %** "
        "| `design__instance__utilization` |",
    ]
    for label, key in [
        ("standard-cell instances", "design__instance__count__stdcell"),
        ("of which sequential", "design__instance__count__class:sequential_cell"),
        ("tap cells", "design__instance__count__class:tap_cell"),
        ("standard-cell rows", "design__rows"),
        ("routed wirelength", "route__wirelength"),
        ("power (flow estimate)", "power__total"),
    ]:
        if key in m:
            v = m[key]
            lines.append(f"| {label} | {num(v, 4 if isinstance(v, float) else 0)} | `{key}` |")
    lines.append("")
    # Do NOT assert the direction of this comparison in prose.  An earlier draft of
    # this generator said "the lower figure is margin, not a smaller chip" -- written
    # before the run landed, against a hypothesis of ~40 %.  The measurement came back
    # HIGHER than the ORFS route, and the sentence would have shipped as a confident
    # inversion of its own table.  The comparison is computed from the number.
    delta = util * 100 - ORFS_UTILISATION_PCT
    direction = ("**above**" if delta > 0 else "**below**")
    lines.append(
        f"**{util * 100:.2f} % of a {m['design__core__area'] / 1e6:.2f} mm² core**, against "
        f"**{ORFS_UTILISATION_PCT:.1f} %** for the two-quarter-slot ORFS route on a 3.38 mm² "
        f"core (`docs/pnr-synth-top.md`) — {abs(delta):.1f} points {direction} it. The two are "
        "not the same measurement: this one is a padframe flow and its "
        "`design__instance__area__stdcell` includes the tap cells, end caps, clock tree and "
        "timing-repair buffers the flow inserted, which the earlier figure's core logic does "
        "not. Read §3 before treating the gap as a change in the design.")
    for label, key in [
        ("timing-repair buffers", "design__instance__area__class:timing_repair_buffer"),
        ("tap cells", "design__instance__area__class:tap_cell"),
        ("end caps", "design__instance__area__class:endcap_cell"),
        ("clock buffers", "design__instance__area__class:clock_buffer"),
    ]:
        if key in m and m.get("design__instance__area__stdcell"):
            share = 100.0 * m[key] / m["design__instance__area__stdcell"]
            lines.append(f"\n- {label}: **{num(m[key])} µm²**, {share:.1f} % of the "
                         f"standard-cell area (`{key}`)")
    return "\n".join(lines)


def worst_setup(m: dict) -> tuple[float, str]:
    """(worst setup slack over every per-corner key, corner name).

    NOT `timing__setup__ws`: LibreLane records that for the run's nominal corner, and
    on this design it is +34 ns while the slow corner is -178 ns.
    """
    per = {k.split("corner:")[1]: v for k, v in m.items()
           if k.startswith("timing__setup__ws__corner:")}
    if not per:
        return m["timing__setup__ws"], "(nominal corner only)"
    c = min(per, key=per.get)
    return per[c], c


def timing_section(m: dict) -> str:
    if "timing__setup__ws" not in m:
        raise cr.Refusal("the run has no timing__setup__ws")
    lines = [
        f"Clock period {CLOCK_PERIOD_NS} ns (12.288 MHz). Slack in ns; "
        "positive closes. `WNS` is the worst negative slack over the whole design.",
        "",
        "| corner | setup WNS | hold WNS | implied min period |",
        "|---|---:|---:|---:|",
    ]
    any_corner = False
    for c in CORNERS:
        sk, hk = f"timing__setup__ws__corner:{c}", f"timing__hold__ws__corner:{c}"
        if sk not in m:
            continue
        any_corner = True
        s = m[sk]
        h = m.get(hk)
        imp = CLOCK_PERIOD_NS - s
        bold = "**" if "ss_" in c else ""
        lines.append(f"| {bold}`{c}`{bold} | {bold}{s:+.3f}{bold} | "
                     f"{('%+.3f' % h) if h is not None else '—'} | {imp:.2f} ns |")
    if not any_corner:
        raise cr.Refusal("the run recorded no per-corner setup slack")
    worst_s, worst_c = worst_setup(m)
    worst_h = m.get("timing__hold__ws")
    lines += [
        f"| **worst over all corners** | **{worst_s:+.3f}** | "
        f"{('**%+.3f**' % worst_h) if worst_h is not None else '—'} | "
        f"{CLOCK_PERIOD_NS - worst_s:.2f} ns |",
        "",
        '"Implied min period" is `period − WNS`, an estimate from one run and not a '
        "closure sweep — the same caveat `docs/pnr-synth-top.md` §3.1 attaches to it.",
        "",
    ]
    # The verdict is computed from the per-corner numbers, never typed.  `timing__setup__ws`
    # alone is NOT the answer: LibreLane reports it for the run's *nominal* corner, so a
    # design that misses by two clock periods at ss can show a large positive there.  That
    # is exactly what this run does, and a reader who quotes the headline figure gets the
    # opposite of the truth.
    if worst_s >= 0:
        lines.append(f"**Setup closes at every corner reported**; the worst is `{worst_c}` at "
                     f"{worst_s:+.3f} ns, {100 * worst_s / CLOCK_PERIOD_NS:.1f} % of the period.")
    else:
        lines.append(
            f"**Setup does NOT close.** The worst corner is `{worst_c}` at **{worst_s:+.3f} ns** "
            f"against an {CLOCK_PERIOD_NS} ns period — {abs(worst_s) / CLOCK_PERIOD_NS:.2f} "
            f"clock periods short. `timing__setup__ws` reads **{m['timing__setup__ws']:+.3f}** "
            "because LibreLane reports that key for the run's nominal corner only; quoting it "
            "as the design's slack inverts this result.")
    for label, key in [("setup TNS", "timing__setup__tns"), ("hold TNS", "timing__hold__tns"),
                       ("max-slew violations", "design__max_slew_violation__count"),
                       ("max-cap violations", "design__max_cap_violation__count")]:
        if key in m:
            lines.append(f"\n- {label}: **{num(m[key], 3 if isinstance(m[key], float) else 0)}** (`{key}`)")
    return "\n".join(lines)


def padframe_section(m: dict) -> str:
    if "design__instance__count__padcells" not in m:
        raise cr.Refusal("the run has no design__instance__count__padcells")
    pads = m["design__instance__count__padcells"]
    lines = [
        "`docs/dag.json` node `S2` was blocked with *\"routed die has padcells: 0\"*. "
        "This is that number.",
        "",
        "| quantity | value | metric key |",
        "|---|---:|---|",
        f"| **pad cells placed** | **{num(pads)}** | `design__instance__count__padcells` |",
    ]
    for label, key in [
        ("signal / bidirectional pads", "design__instance__count__class:input_output_pad"),
        ("input-only pads", "design__instance__count__class:input_pad"),
        ("power pads", "design__instance__count__class:power_pad"),
        ("pad spacers / fill", "design__instance__count__class:pad_spacer"),
        ("hard macros (the wafer.space IP cells)", "design__instance__count__macros"),
        ("top-level IO ports", "design__io"),
    ]:
        if key in m:
            lines.append(f"| {label} | {num(m[key])} | `{key}` |")
    lines += [
        "",
        "The five mandatory wafer.space IP cells (`qrcode_id`, `shuttle_id`, `project_id`, "
        "`marker`, `logo`) sit in the die margin **outside** `CORE_AREA`, so they consume no "
        "core area and do not appear in the utilisation figure above. Their contents are "
        "placeholders — see §5.",
    ]
    return "\n".join(lines)


def crosscheck_section(run_dir: str, m: dict, census: dict) -> str:
    def_path = cr.find_final_def(run_dir)
    comps = cr.parse_def_components(def_path)
    if not comps:
        raise cr.Refusal(f"{def_path} has no COMPONENTS section")
    import collections
    seq = collections.Counter()
    for inst, master in comps:
        if cr.SEQ_MASTER.search(master):
            seq[cr.bucket_of(inst)] += 1
    declared = census["flops_declared_per_module"]
    lines = [
        "A DRC count cannot tell a real result from a collapsed netlist: **both are "
        "smaller and cleaner when the design has collapsed.** This repository quoted a "
        '"1,917-cell" `ladder_dp` three times before noticing every output was X. So the '
        "check is whether the flops the RTL *declares* are the flops the layout *places* — "
        "yosys on `rtl-sketch/` against the run's own DEF, two tools sharing no code path.",
        "",
        f"From `{os.path.relpath(def_path, run_dir)}` ({len(comps):,} components), by "
        "`pnr/shuttle/check_route.py verify`:",
        "",
        "| block | flops placed | flops declared (RTL) |",
        "|---|---:|---:|",
    ]
    pairs = [
        ("u_dregs          (drum_regs)", "drum_regs"),
        ("u_drums.bank     (modal_dp)", "modal_dp"),
        ("u_drums.src      (drum_dp)", "drum_dp"),
        ("u_voice          (own)", None),
        ("u_voice.u_ladder (ladder_dp_n, NCH=2)", "ladder_dp_n"),
        ("u_voice.u_div    (recip_div)", "recip_div"),
        ("u_spi            (spi_ctl)", "spi_ctl"),
        ("u_i2s            (i2s_tx)", "i2s_tx"),
        ("synth_top own", "synth_top"),
    ]
    for label, mod in pairs:
        if label not in seq:
            continue
        want = declared.get(mod) if mod else None
        # voice_dp's census covers the whole voice including its submodules, so the
        # own-logic row has no single declared counterpart; say so rather than
        # inventing a comparison.
        lines.append(f"| `{label.split()[0]}` | {num(seq[label])} | "
                     f"{num(want) if want is not None else 'see below'} |")
    total = sum(seq.values())
    lines.append(f"| **total under `synth_top`** | **{num(total - seq.get('outside synth_top (padframe, wrapper, fill, tap)', 0))}** | |")
    lines.append("")
    got = seq.get("u_dregs          (drum_regs)", 0)
    want = declared.get("drum_regs")
    verdict = "**equal**" if got == want else f"**{got - want:+,} against the census**"
    lines.append(f"`drum_regs` places **{num(got)}** flops; the RTL declares "
                 f"**{num(want)}** — {verdict}. `drum_regs` is the best single probe in this "
                 "design: it is the largest register file and every bit of it is a declared "
                 "`reg`, so a collapsed netlist cannot produce the number by accident.")
    lines.append("")
    lines.append("`voice_dp`'s census figure (**"
                 f"{num(declared.get('voice_dp'))}**) covers the whole voice including "
                 "`ladder_dp_n` and `recip_div`, so it has no single row here; the three voice "
                 "rows sum against it.")
    if "design__instance__count__class:sequential_cell" in m:
        lines.append("")
        lines.append("The flow's own count of sequential cells anywhere on the die is "
                     f"**{num(m['design__instance__count__class:sequential_cell'])}** "
                     "(`design__instance__count__class:sequential_cell`), which includes the "
                     "clock tree's own registers and anything outside `synth_top`.")
    for label, key in [("detailed-router DRC violations", "route__drc_errors"),
                       ("antenna-violating nets", "route__antenna_violations")]:
        if key in m:
            lines.append("")
            lines.append(f"- **{label}: {num(m[key])}** (`{key}`) — "
                         "the *router checking its own work*, not a sign-off deck. See §5.")
    return "\n".join(lines)


def verdict_section(m: dict) -> str:
    """#33's question, answered from the metrics rather than from the narrative.

    Three independent conditions, each printed with the key it came from and each
    allowed to fail on its own.  A single "it fits"/"it does not fit" line would let
    a design that places cleanly but misses timing by two periods be reported as a
    pass, which is what this run would have done.
    """
    util = m.get("design__instance__utilization")
    drc = m.get("route__drc_errors")
    pads = m.get("design__instance__count__padcells")
    worst_s, worst_c = worst_setup(m)

    checks = [
        ("placed and routed inside the template's own die and core",
         (util is not None and util < 1.0),
         f"`design__instance__utilization` = {util * 100:.2f} %" if util is not None
         else "no utilisation metric"),
        ("detailed router reports no violations",
         (drc == 0) if drc is not None else None,
         f"`route__drc_errors` = {num(drc)}" if drc is not None
         else "the run did not reach a completed detailed route"),
        ("a populated padframe (S2's own condition)",
         (pads is not None and pads > 0),
         f"`design__instance__count__padcells` = {num(pads)}" if pads is not None
         else "no padcell metric"),
        (f"setup closes at every corner against {CLOCK_PERIOD_NS} ns",
         worst_s >= 0,
         f"worst `{worst_c}` = {worst_s:+.3f} ns"),
    ]
    lines = ["| condition | verdict | measured |", "|---|---|---|"]
    for label, ok, ev in checks:
        mark = {True: "**yes**", False: "**NO**", None: "*not measured*"}[ok]
        lines.append(f"| {label} | {mark} | {ev} |")
    failed = [c[0] for c in checks if c[1] is False]
    unknown = [c[0] for c in checks if c[1] is None]
    lines.append("")
    if not failed and not unknown:
        lines.append("**The joined chip fits one half slot.** Every condition above is "
                     "measured from this run. The original issue's choice between two "
                     "quarter slots and a structural cut does not arise.")
    else:
        lines.append("**This run does not establish that the joined chip fits one half slot.**")
        for f in failed:
            lines.append(f"\n- FAILED: {f}")
        for u in unknown:
            lines.append(f"\n- NOT MEASURED: {u}")
        lines.append(
            "\nWhat this does *not* say is that the design is too big: see §4.1 — the cells "
            "are inside the core with room left. A failure here is a claim about **this "
            "configuration of this flow**, and the next step is to read which condition "
            "failed, not to re-open the area question.")
    return "\n".join(lines)


# docs/pnr-synth-top.md, the two-quarter-slot ORFS route on d1e5068 at MODES = 12.
ORFS_DRUM_REGS_FLOPS = 2276
ORFS_SEQ_CELL_AREA_UM2 = 564689


def growth_section(m: dict, census: dict) -> str:
    """How much of the gap against the ~40 % hypothesis is the design having grown.

    Both columns carry their source. The right-hand column is re-derived on every
    run; typing it is what put 3,232 into docs/capability-dag.md and 2,276 into
    #33's acceptance criterion, both of which are now wrong.
    """
    declared = census["flops_declared_per_module"].get("drum_regs")
    if declared is None:
        raise cr.Refusal("the census has no drum_regs entry")
    seq_area = m.get("design__instance__area__class:sequential_cell")
    lines = [
        "| | `d1e5068` (ORFS, `docs/pnr-synth-top.md`) | this run, `main` | source of the "
        "right-hand column |",
        "|---|---:|---:|---|",
        f"| `drum_regs` flops declared | {num(ORFS_DRUM_REGS_FLOPS)} | **{num(declared)}** "
        "| `check_route.py census` (yosys on `rtl-sketch/`) |",
    ]
    if seq_area is not None:
        lines.append(f"| sequential-cell area | {num(ORFS_SEQ_CELL_AREA_UM2)} µm² | "
                     f"**{num(seq_area)} µm²** | `design__instance__area__class:"
                     "sequential_cell` |")
    if seq_area:
        lines += ["", f"Sequential cells alone grew by "
                      f"{100 * (seq_area - ORFS_SEQ_CELL_AREA_UM2) / ORFS_SEQ_CELL_AREA_UM2:.1f} %"
                      " between the two runs. That is the design, not the flow."]
    return "\n".join(lines)


SECTIONS = {
    "measured:verdict": verdict_section,
    "measured:area": area_section,
    "measured:timing": timing_section,
    "measured:padframe": padframe_section,
}


def splice(doc: str, name: str, body: str) -> str:
    begin, end = f"<!-- BEGIN {name} -->", f"<!-- END {name} -->"
    if begin not in doc or end not in doc:
        raise cr.Refusal(f"{name} markers are not in the document")
    pre = doc[:doc.index(begin) + len(begin)]
    post = doc[doc.index(end):]
    return pre + "\n\n" + body.rstrip() + "\n\n" + post


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir")
    ap.add_argument("--doc", default=os.path.join(REPO, "docs", "pnr-shuttle-halfslot.md"))
    ap.add_argument("--census", default=os.path.join(HERE, "evidence", "flop-census.json"))
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if the document on disk differs from the generated one")
    a = ap.parse_args(argv)
    try:
        import json
        step, metrics = cr.read_metrics(a.run_dir)
        with open(a.census, encoding="utf-8") as f:
            census = json.load(f)
        doc = open(a.doc, encoding="utf-8").read()
        for name, fn in SECTIONS.items():
            doc = splice(doc, name, fn(metrics))
        doc = splice(doc, "measured:growth", growth_section(metrics, census))
        doc = splice(doc, "measured:crosscheck",
                     crosscheck_section(a.run_dir, metrics, census))
        doc = re.sub(r"(?m)^<!-- generated-from:.*\n", "", doc)
    except cr.Refusal as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return REFUSED
    doc = doc.rstrip("\n") + (
        f"\n\n<!-- generated-from: {os.path.basename(a.run_dir)} step {step} "
        f"by pnr/shuttle/report_halfslot.py -->\n")
    if a.check:
        if doc != open(a.doc, encoding="utf-8").read():
            print(f"{a.doc} is stale: regenerate with "
                  f"./pnr/shuttle/report_halfslot.py {a.run_dir}", file=sys.stderr)
            return 1
        print(f"{a.doc} is up to date with {a.run_dir} (step {step})")
        return 0
    with open(a.doc, "w", encoding="utf-8") as f:
        f.write(doc)
    print(f"wrote {a.doc} from {a.run_dir} step {step}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
