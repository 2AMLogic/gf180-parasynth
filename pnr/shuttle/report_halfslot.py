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
import check_route as cr      # noqa: E402
import finish_halfslot as fh  # noqa: E402

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


def worst_setup(m: dict, post: set) -> tuple[float | None, str]:
    """(worst POST-ROUTE setup slack over the per-corner keys, corner name).

    ``(None, reason)`` when the run has no post-route per-corner slack. It is not
    acceptable to fall back to `timing__setup__ws` there: that key is the *nominal*
    corner, and falling back silently turns "we did not measure the slow corner" into
    "the slow corner is fine". On the run this was written against the difference is
    +34 ns against -178 ns.
    """
    per = {k.split("corner:")[1]: v for k, v in m.items()
           if k.startswith("timing__setup__ws__corner:") and k in post}
    if not per:
        return None, "no per-corner setup slack was recorded after detailed routing"
    c = min(per, key=per.get)
    return per[c], c


def timing_section(m: dict, post: set, src: dict) -> str:
    if "timing__setup__ws" not in m:
        raise cr.Refusal("the run has no timing__setup__ws")
    lines = [
        f"Clock period {CLOCK_PERIOD_NS} ns (12.288 MHz). Slack in ns; "
        "positive closes.",
        "",
        "**The `written by` column is the point of this table.** LibreLane's metrics are "
        "cumulative, so a per-corner key that no step has updated since synthesis is still "
        "present in the final payload and reads exactly like a fresh measurement. A row "
        "whose step is not a post-route one is **not a post-route number** and is marked "
        "so; see §5.",
        "",
        "| corner | setup WNS | hold WNS | implied min period | written by | post-route? |",
        "|---|---:|---:|---:|---|---|",
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
        fresh = "yes" if sk in post else "**NO — written before the router ran**"
        lines.append(f"| {bold}`{c}`{bold} | {bold}{s:+.3f}{bold} | "
                     f"{('%+.3f' % h) if h is not None else '—'} | {imp:.2f} ns | "
                     f"`{src.get(sk, '?')}` | {fresh} |")
    if not any_corner:
        raise cr.Refusal("the run recorded no per-corner setup slack")
    worst_s, worst_c = worst_setup(m, post)
    if worst_s is None:
        lines += [
            "",
            f"**This run has no post-route timing at any corner** — {worst_c}. The "
            "un-suffixed `timing__setup__ws` reads "
            f"**{m['timing__setup__ws']:+.3f} ns** and was written by "
            f"`{src.get('timing__setup__ws', '?')}` — it is the **nominal corner only**, so "
            "it is not the design's slack and must not be quoted as one. The per-corner "
            "rows above are `OpenROAD.STAPrePNR`'s: an unplaced, unrouted netlist with an "
            "ideal clock. The step that would produce real per-corner numbers is "
            "`OpenROAD.STAPostPNR`, which runs after detailed routing with extracted "
            "parasitics. This run has not reached it.",
            "",
            "So **the 12.288 MHz question is open**, in both directions: this page neither "
            "shows the chip closing timing nor shows it failing to.",
        ]
        for label, key in [("setup TNS", "timing__setup__tns"),
                           ("max-slew violations", "design__max_slew_violation__count"),
                           ("max-cap violations", "design__max_cap_violation__count")]:
            if key in m:
                lines.append(f"\n- {label}: **{num(m[key], 3 if isinstance(m[key], float) else 0)}**"
                             f" (`{key}`, written by `{src.get(key, '?')}`)")
        return "\n".join(lines)
    worst_h = m.get("timing__hold__ws")
    lines += [
        f"| **worst post-route corner** | **{worst_s:+.3f}** | "
        f"{('**%+.3f**' % worst_h) if worst_h is not None else '—'} | "
        f"{CLOCK_PERIOD_NS - worst_s:.2f} ns | `{src.get(f'timing__setup__ws__corner:{worst_c}', '?')}` "
        "| yes |",
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


def iteration_section(run_dir: str) -> str:
    """What the router's iterations after convergence cost — the §6.5 evidence.

    Generated rather than typed because the claim is quantitative: the violation
    count stops moving many iterations before the router stops, and the iterations
    after that point are the expensive ones.  A prose summary of this would be an
    argument; the table is a measurement.
    """
    step = fh.find_step(run_dir, fh.DETAILED_ROUTING)
    if not step:
        raise cr.Refusal(f"{run_dir} has no {fh.DETAILED_ROUTING} step")
    log = os.path.join(run_dir, step, "openroad-detailedrouting.log")
    if not os.path.exists(log):
        raise cr.Refusal(f"{step} has no openroad-detailedrouting.log")
    its = fh.parse_drt_iterations(open(log, encoding="utf-8", errors="replace").read())
    done = [r for r in its if r["violations"] is not None]
    if not done:
        raise cr.Refusal("the router log has no completed iteration")
    final = done[-1]["violations"]
    # The first iteration that reached the count the router ended on.
    first_at_final = next(r for r in done if r["violations"] == final)
    after = [r for r in done if r["iteration"] > first_at_final["iteration"]]
    cap = None
    cfg = os.path.join(run_dir, step, "config.json")
    if os.path.exists(cfg):
        import json as _json
        cap = _json.load(open(cfg, encoding="utf-8")).get("DRT_OPT_ITERS")

    lines = [
        "| | |",
        "|---|---:|",
        f"| iterations logged | {len(done)} |",
        f"| `DRT_OPT_ITERS` — **INPUT** | {num(cap)} |",
        f"| final violation count | {num(final)} |",
        f"| first iteration to reach it | **{first_at_final['iteration']}** |",
        f"| iterations after that | {len(after)} |",
    ]
    cpu_after = sum(r["cpu_s"] or 0 for r in after)
    wall_after = sum(r["elapsed_s"] or 0 for r in after)
    lines += [
        f"| CPU time in those iterations | {cpu_after / 3600:.2f} h |",
        f"| wall time in those iterations | {wall_after / 3600:.2f} h |",
        "",
        "| iteration | kind | violations | elapsed | CPU |",
        "|---:|---|---:|---:|---:|",
    ]
    for r in done[-12:]:
        lines.append(
            f"| {r['iteration']} | {r['kind']} | {num(r['violations'])} | "
            f"{(r['elapsed_s'] or 0) / 60:.1f} min | {(r['cpu_s'] or 0) / 60:.1f} min |")
    lines += ["", f"The count has not moved since iteration "
                  f"**{first_at_final['iteration']}**. `stubborn` iterations cost minutes "
                  f"to tens of minutes each and `guides` iterations seconds, so the cost is "
                  f"concentrated in exactly the iterations that are not improving anything."]
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
    dregs_nets = cr.dregs_output_nets(os.path.join(REPO, "rtl-sketch", "synth_top.v"))
    seq = cr.flops_by_block(def_path, dregs_nets)
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
        "| block | flops placed | flops declared (RTL) | |",
        "|---|---:|---:|---|",
    ]
    pairs = [
        (cr.DREGS_BUCKET, "drum_regs", "**the discriminator**"),
        ("u_drums.src      (drum_dp)", "drum_dp", ""),
        ("u_drums.bank     (modal_dp)", "modal_dp", ""),
        ("u_voice          (voice_dp + submodules)", "voice_dp", ""),
        ("u_voice.u_ladder (ladder_dp_n, NCH=2)", "ladder_dp_n", ""),
        ("u_voice.u_div    (recip_div)", "recip_div", ""),
        ("u_spi            (spi_ctl)", "spi_ctl", ""),
        ("u_i2s            (i2s_tx)", "i2s_tx", ""),
        (cr.SYNTH_OWN, "synth_top", ""),
    ]
    for label, mod, note in pairs:
        if label not in seq:
            continue
        want = declared.get(mod) if mod else None
        lines.append(f"| `{label.split()[0]}` | {num(seq[label])} | "
                     f"{num(want) if want is not None else '—'} | {note} |")
    total = sum(seq.values())
    lines.append(f"| **total placed** | **{num(total)}** | | |")
    lines.append("")
    lines.append(
        "**Only the `u_dregs` row is expected to match exactly, and only that row is "
        "asserted on.** Synthesis flattens `synth_top` and optimises across module "
        "boundaries, so a per-module census taken *before* flattening does not have to "
        "agree block by block with what survives after it — every other row here is "
        "lower than its census figure and that is the normal amount. `drum_regs` is "
        "different because it is a register file: every bit is architecturally visible "
        "at a port, so nothing can be merged away without changing the chip.")
    lines.append("")
    got = seq.get(cr.DREGS_BUCKET, 0)
    want = declared.get("drum_regs")
    verdict = "**equal**" if got == want else f"**{got - want:+,} against the census**"
    lines.append(f"`drum_regs` places **{num(got)}** flops; the RTL declares "
                 f"**{num(want)}** — {verdict}. `drum_regs` is the best single probe in this "
                 "design: it is the largest register file and every bit of it is a declared "
                 "`reg`, so a collapsed netlist cannot produce the number by accident.")
    lines.append("")
    lines.append(
        "The flop is attributed to a block by **the name of the net its `Q` drives**, not "
        "by its instance name: the netlist is flattened before placement, so every "
        "instance in the DEF is an auto-name (`_141690_`) and no hierarchy survives "
        "there. An earlier version of this check keyed on instance names, bucketed all "
        f"{num(total)} placed flops as 'outside `synth_top`', and reported the collapse "
        "signature for a layout that is fine. `drum_regs`'s own internal names do not "
        "survive either (`memory` rewrites its `reg [25:0] a1 [0:MODES-1]` arrays), so "
        "its bucket is the set of nets `u_dregs` drives — read out of "
        "`rtl-sketch/synth_top.v` on every run rather than listed here, so a port rename "
        "cannot quietly shrink it.")
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


def conditions(m: dict, post: set, src: dict):
    """(fit_checks, clean_checks) as (label, verdict-or-None, evidence) triples.

    Extracted so the markdown verdict and the machine-readable one in
    ``pnr/shuttle/evidence/halfslot-verdict.json`` -- which is what
    ``docs/dag.json`` node ``S2`` reads -- cannot drift apart.  A DAG node whose
    colour is computed from a different predicate than the document a reader sees
    is the exact failure ``tools/compile_dag.py`` was written to stop.
    """
    util = m.get("design__instance__utilization")
    drc = m.get("route__drc_errors")
    pads = m.get("design__instance__count__padcells")
    worst_s, worst_c = worst_setup(m, post)

    # Does one half slot hold this design?  Geometry, a populated padframe, a route
    # that finished, and timing that closes.
    fit_checks = [
        ("placed inside the template's own die and core",
         (util is not None and util < 1.0),
         f"`design__instance__utilization` = {util * 100:.2f} %" if util is not None
         else "no utilisation metric"),
        ("the detailed route ran to completion",
         True if drc is not None else None,
         f"`route__drc_errors` present, written by `{src.get('route__drc_errors', '?')}`"
         if drc is not None
         else "the run has not completed a detailed route — no `route__drc_errors`"),
        ("a populated padframe (S2's own condition)",
         (pads is not None and pads > 0),
         f"`design__instance__count__padcells` = {num(pads)}" if pads is not None
         else "no padcell metric"),
        (f"setup closes post-route at every corner against {CLOCK_PERIOD_NS} ns",
         None if worst_s is None else worst_s >= 0,
         f"worst `{worst_c}` = {worst_s:+.3f} ns" if worst_s is not None else worst_c),
    ]
    # Is the layout clean?  This run can only speak to the router's own check; every
    # other row of that list is in §5 and was skipped, so it is NOT aggregated here.
    clean_checks = [
        ("the detailed router reports no violations of its own",
         (drc == 0) if drc is not None else None,
         f"`route__drc_errors` = {num(drc)} (`{src.get('route__drc_errors', '?')}`)"
         if drc is not None
         else "the run has not completed a detailed route — no `route__drc_errors`"),
    ]

    return fit_checks, clean_checks


def verdict_section(m: dict, post: set, src: dict) -> str:
    """#33's question, answered from the metrics rather than from the narrative.

    Independent conditions, each printed with the key it came from and each allowed
    to fail on its own.  A single "it fits"/"it does not fit" line would let a design
    that places cleanly but misses timing by two periods be reported as a pass, which
    is what an earlier state of this run would have done.

    AND THE TWO QUESTIONS ARE KEPT APART, which is the change that matters here.
    #33 asks an AREA question: does the joined chip need two quarter slots or a
    structural cut, or does one half slot hold it?  "Is this layout ready for
    tapeout?" is a different question with a much longer condition list (§5), and
    the run's router-DRC count belongs to the second.  Folding them together makes
    a route that completes with a handful of shorts report as *"does not fit"* --
    which would send this issue to a business decision about dies per wafer on the
    strength of three Metal2 shorts.  It is equally wrong to let the area answer
    launder the DRC count into a clean bill; both verdicts are printed, separately,
    with their own condition lists.
    """
    fit_checks, clean_checks = conditions(m, post, src)
    drc = m.get("route__drc_errors")

    def table(checks):
        out = ["| condition | verdict | measured |", "|---|---|---|"]
        for label, ok, ev in checks:
            mark = {True: "**yes**", False: "**NO**", None: "*not measured*"}[ok]
            out.append(f"| {label} | {mark} | {ev} |")
        return out

    lines = ["#### Does the joined chip fit one half slot? — #33's question", ""]
    lines += table(fit_checks)
    lines.append("")
    fit_failed = [c[0] for c in fit_checks if c[1] is False]
    fit_unknown = [c[0] for c in fit_checks if c[1] is None]
    if not fit_failed and not fit_unknown:
        lines.append(
            "**Yes — measured, on one half slot.** Every condition above is measured "
            "from this run. The original issue's choice between two quarter slots and a "
            "structural cut (dropping the modal bank, the voice, or the writable "
            "configuration) **does not arise**: no capability has to be given up and no "
            "second die has to be paid for.")
    else:
        lines.append("**This run does not establish that the joined chip fits one half slot.**")
        for f in fit_failed:
            lines.append(f"\n- FAILED: {f}")
        for u in fit_unknown:
            lines.append(f"\n- NOT MEASURED: {u}")
        lines.append(
            "\n**NOT MEASURED is not FAILED**, and neither is evidence that the design is too "
            "big: §4.1 shows the cells placed inside the core with room left. Every condition "
            "above is a claim about **this run of this flow**. Read which condition is which "
            "before re-opening the area question — and in particular do not read an unfinished "
            "run as a negative result.")

    lines += ["", "#### Is the layout clean? — a different question, and not #33's", ""]
    lines += table(clean_checks)
    lines.append("")
    clean_failed = [c[0] for c in clean_checks if c[1] is False]
    clean_unknown = [c[0] for c in clean_checks if c[1] is None]
    if clean_unknown:
        lines.append("**Not measured.** The router has not reported a violation count.")
    elif clean_failed:
        lines.append(
            f"**No — the route is not clean.** The router leaves **{num(drc)}** violation"
            f"{'' if drc == 1 else 's'} of its own (§4.5 breaks them down by layer from a "
            "second, independent source). That is a sign-off question, not an area "
            "question: it does not become a reason to spend a second die or cut a "
            "capability, and it is **not** cleared by the area answer above. Everything in "
            "§5 — sign-off DRC, LVS, XOR, antenna and density decks, IR drop, gate-level "
            "simulation — is additionally unrun, so this row is the *weakest* of the "
            "cleanliness claims and not a summary of them.")
    else:
        lines.append(
            "**The router reports no violations of its own.** That is the router checking "
            "its own work; §5 lists the sign-off decks this run did not run, and this row "
            "does not stand in for them.")
    return "\n".join(lines)


def router_section(run_dir: str, m: dict, src: dict) -> str:
    """The router's violation count from BOTH places that hold it.

    The metric and the router's log have different producers, and §6.1 is a finding
    about a metric that was byte-identical for thirty steps after the run that wrote
    it.  A count that agrees across two producers is a count; one source is a claim.
    """
    try:
        d = fh.drc_verdict(run_dir)
    except fh.Refusal as e:
        # finish_halfslot has its own Refusal; main() catches check_route's.  Left
        # unconverted this is a traceback in the place a REFUSED belongs.
        raise cr.Refusal(str(e)) from e
    metric = d["route__drc_errors"]
    log_n = d["log_violations"]
    lines = [
        "| | violations | source |",
        "|---|---:|---|",
        f"| flow metric | {num(metric)} | `route__drc_errors`, written by "
        f"`{src.get('route__drc_errors', '?')}` |",
        f"| the router's own log | {num(log_n)} | `{d['step']}/"
        "openroad-detailedrouting.log`, last `DRT-0199` |",
    ]
    lines.append("")
    if metric is not None and log_n is not None and int(metric) == int(log_n):
        lines.append("The two agree. `finish_halfslot.py drc` REFUSES if they do not — "
                     "one source cannot tell a fresh count from a carried-forward one.")
    else:
        lines.append("**The two do not agree**, and `finish_halfslot.py drc` refuses on "
                     "that; treat neither as the run's count.")
    if d["by_layer"]:
        lines += ["", "| layer | violations |", "|---|---:|"]
        for layer, n in sorted(d["by_layer"].items()):
            lines.append(f"| `{layer}` | {num(n)} |")
    if d["log_iteration"] is not None:
        lines += ["", f"Reported at the router's **{d['log_iteration']}th** iteration of "
                      f"{d['log_iterations_seen']} logged. §6.5 is about what those "
                      "iterations cost."]
    if log_n:
        lines += ["", "These are **shorts the router could not resolve**, not rule "
                      "violations it never checked — for the rules it never checked, see "
                      "§6.4 and §5."]
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


def verdict_json(run_dir: str, step: str, m: dict, post: set, src: dict,
                 census: dict) -> dict:
    """The verdict table as data, for docs/dag.json node S2.

    ``passed`` is the FIT question and only the FIT question -- S2 is named "Fits a
    real shuttle padframe".  The router's own DRC count is carried in the payload as
    ``router_clean`` and is deliberately NOT part of ``passed``: a node that goes red
    on three Metal2 shorts would be reporting a sign-off problem in the place a
    reader looks for an area answer, and a node that goes green while hiding the
    count would be worse.  ``tools/compile_dag.py`` honours ``passed``; everything
    else here is for the human who follows the link.

    A condition that is NOT MEASURED makes ``passed`` false.  That is deliberate and
    it is not the same as FAILED: the ``conditions`` list distinguishes them, and
    ``blocked_on`` names the unmeasured ones so the node's note can say which.
    """
    fit, clean = conditions(m, post, src)
    unmeasured = [c[0] for c in fit if c[1] is None]
    failed = [c[0] for c in fit if c[1] is False]
    drc = m.get("route__drc_errors")
    declared = census.get("flops_declared_per_module", {}).get("drum_regs")
    seq = None
    try:
        def_path = cr.find_final_def(run_dir)
        dregs = cr.dregs_output_nets(os.path.join(REPO, "rtl-sketch", "synth_top.v"))
        seq = cr.flops_by_block(def_path, dregs).get(cr.DREGS_BUCKET)
    except cr.Refusal:
        pass
    worst_s, worst_c = worst_setup(m, post)
    return {
        "passed": not failed and not unmeasured,
        "question": "Does the joined chip fit one wafer.space gf180mcu half slot "
                    "(slot_1x0p5) -- issue #33",
        "run": os.path.basename(run_dir),
        "last_step_with_metrics": step,
        "conditions": [{"condition": c[0],
                        "verdict": {True: "yes", False: "no", None: "not measured"}[c[1]],
                        "measured": c[2]} for c in fit],
        "failed": failed,
        "not_measured": unmeasured,
        "router_clean": None if drc is None else (drc == 0),
        "route__drc_errors": drc,
        "utilisation": m.get("design__instance__utilization"),
        "padcells": m.get("design__instance__count__padcells"),
        "ws_ip_macros": m.get("design__instance__count__macros"),
        "worst_post_route_setup_ns": worst_s,
        "worst_post_route_setup_corner": worst_c if worst_s is not None else None,
        "clock_period_ns": CLOCK_PERIOD_NS,
        "drum_regs_flops_placed": seq,
        "drum_regs_flops_declared": declared,
        "signoff_checks_run": [],
        "note": "passed is the AREA question only. Sign-off DRC, LVS, XOR, antenna, "
                "density, IR drop and gate-level simulation were NOT run -- see "
                "docs/pnr-shuttle-halfslot.md section 5.",
        "doc": "docs/pnr-shuttle-halfslot.md",
    }


# name -> (fn, what it is called with).  "prov" means (metrics, post_route_keys,
# source_step_by_key); the provenance arguments exist so no section can print a
# carried-forward pre-route metric as a post-route one.
SECTIONS = {
    "measured:verdict": (verdict_section, "prov"),
    "measured:area": (area_section, "metrics"),
    "measured:timing": (timing_section, "prov"),
    "measured:padframe": (padframe_section, "metrics"),
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
    ap.add_argument("--verdict-json",
                    default=os.path.join(HERE, "evidence", "halfslot-verdict.json"),
                    help="the verdict as data; docs/dag.json node S2 reads this")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if the document on disk differs from the generated one")
    a = ap.parse_args(argv)
    try:
        import json
        step, metrics = cr.read_metrics(a.run_dir)
        post = cr.post_route_keys(a.run_dir)
        src = cr.metric_source_step(a.run_dir)
        with open(a.census, encoding="utf-8") as f:
            census = json.load(f)
        doc = open(a.doc, encoding="utf-8").read()
        for name, (fn, kind) in SECTIONS.items():
            doc = splice(doc, name,
                         fn(metrics, post, src) if kind == "prov" else fn(metrics))
        doc = splice(doc, "measured:growth", growth_section(metrics, census))
        doc = splice(doc, "measured:router", router_section(a.run_dir, metrics, src))
        doc = splice(doc, "measured:iterations", iteration_section(a.run_dir))
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
    v = verdict_json(a.run_dir, step, metrics, post, src, census)
    with open(a.verdict_json, "w", encoding="utf-8") as f:
        import json as _json
        _json.dump(v, f, indent=2, sort_keys=True)
        f.write("\n")
    print(f"wrote {a.verdict_json}: passed={v['passed']} "
          f"router_clean={v['router_clean']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
