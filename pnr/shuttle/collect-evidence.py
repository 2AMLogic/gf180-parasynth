#!/usr/bin/env python3
"""Collect one LibreLane run into pnr/shuttle/evidence/<name>/.

Copies the config actually used, the per-step metrics, the reports worth opening and
the logs, then writes SUMMARY.md. Numbers come from the run's own metrics.json --
nothing here is computed from a rule of thumb, and nothing is extrapolated.

  ./collect-evidence.py <run-dir> <evidence-name> [--rtl-sha SHA]
"""
import json, os, shutil, subprocess, sys, argparse, glob

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))

# metrics we always want on the front page, in order
HEADLINE = [
    ("design__die__area",                 "die area (um2)              INPUT"),
    ("design__core__area",                "core area (um2)             INPUT"),
    ("design__instance__area__stdcell",   "std-cell area (um2)         MEASURED"),
    ("design__instance__count__stdcell",  "std-cell instances          MEASURED"),
    ("design__instance__count__macros",   "macros                      MEASURED"),
    ("design__instance__count__padcells", "PAD CELLS                   MEASURED"),
    ("design__instance__utilization",     "utilisation                 MEASURED"),
    ("design__io",                        "top-level IO ports"),
    ("design__rows",                      "std-cell rows"),
    ("route__wirelength",                 "routed wirelength (um)"),
    ("route__drc_errors",                 "detailed-router DRC errors"),
    ("route__antenna_violations",         "antenna-violating nets"),
    ("timing__setup__ws",                 "setup WNS (ns)"),
    ("timing__hold__ws",                  "hold WNS (ns)"),
    ("timing__setup__tns",                "setup TNS (ns)"),
    ("power__total",                      "power (W)"),
]

def load(p):
    with open(p) as f:
        return json.load(f)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("name")
    ap.add_argument("--rtl-sha", default=None)
    a = ap.parse_args()

    run = os.path.abspath(a.run_dir)
    out = os.path.join(HERE, "evidence", a.name)
    os.makedirs(out, exist_ok=True)

    steps = sorted(d for d in os.listdir(run) if os.path.isdir(os.path.join(run, d)) and d[:2].isdigit())
    if not steps:
        sys.exit(f"no step directories under {run}")

    # per-step metrics, and the final resolved config
    per_step = {}
    for s in steps:
        mp = os.path.join(run, s, "metrics.json")
        if os.path.exists(mp):
            per_step[s] = load(mp)
    for f in ("resolved.json", "config.json", "warnings.log", "error.log"):
        p = os.path.join(run, f)
        if os.path.exists(p):
            shutil.copy2(p, out)
    with open(os.path.join(out, "metrics_by_step.json"), "w") as f:
        json.dump(per_step, f, indent=1, sort_keys=True)

    # the reports and logs worth keeping
    keep = os.path.join(out, "reports")
    os.makedirs(keep, exist_ok=True)
    pats = ("*.rpt", "*drc*", "*.log", "*antenna*", "*.min.rpt", "*.max.rpt", "summary.rpt", "*.csv")
    kept = 0
    for s in steps:
        for pat in pats:
            for p in glob.glob(os.path.join(run, s, "**", pat), recursive=True):
                if os.path.getsize(p) > 12_000_000:
                    continue
                rel = os.path.relpath(p, run).replace(os.sep, "__")
                shutil.copy2(p, os.path.join(keep, rel)); kept += 1

    # the config this repo supplied (as opposed to the resolved one)
    src = os.path.join(out, "config-in")
    os.makedirs(src, exist_ok=True)
    for rel in ("librelane/config.yaml", "librelane/density.yaml", "librelane/chip_top.sdc",
                "librelane/slots/slot_1x0p5.yaml", "librelane/macros/macros_5v.yaml",
                "librelane/pdn/pdn_cfg.tcl", "src/chip_core.sv", "src/generated_defines.svh",
                "TEMPLATE_PROVENANCE.txt", "run-librelane.sh"):
        p = os.path.join(HERE, rel)
        if os.path.exists(p):
            shutil.copy2(p, os.path.join(src, os.path.basename(rel)))

    last = per_step[max(per_step)] if per_step else {}
    rtl_sha = a.rtl_sha or subprocess.run(
        ["git", "-C", REPO, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    md5s = subprocess.run(
        ["bash", "-c", "cd %s && md5 -r rtl-sketch/{synth_top,spi_ctl,drum_regs,drum_kit,drum_dp,modal_dp,voice_dp,recip_div,ladder_dp_n,i2s_tx}.v "
                       "spec/reference/tables/*.hex rtl-sketch/tanh16.hex 2>/dev/null" % REPO],
        capture_output=True, text=True).stdout

    def fmt(v):
        if isinstance(v, float):
            return f"{v:,.4f}" if abs(v) < 1000 else f"{v:,.1f}"
        if isinstance(v, int):
            return f"{v:,}"
        return str(v)

    with open(os.path.join(out, "SUMMARY.md"), "w") as f:
        f.write(f"# `{a.name}` -- LibreLane run `{os.path.basename(run)}`\n\n")
        f.write(f"- RTL commit `{rtl_sha}`\n- run dir `{run}`\n\n")
        f.write("## Headline (final step)\n\n| metric | value |\n|---|---:|\n")
        for k, label in HEADLINE:
            for cand in (k, k.replace("design__", "")):
                if cand in last:
                    f.write(f"| {label} | {fmt(last[cand])} |\n"); break
        f.write("\n## Every step\n\n")
        cols = ["design__instance__count__stdcell", "design__instance__area__stdcell",
                "design__instance__utilization", "design__instance__count__padcells",
                "timing__setup__ws", "timing__hold__ws"]
        f.write("| step | " + " | ".join(c.split("__")[-1] if "timing" not in c else c for c in cols) + " |\n")
        f.write("|---" * (len(cols) + 1) + "|\n")
        for s in steps:
            m = per_step.get(s)
            if not m: continue
            f.write(f"| {s} | " + " | ".join(fmt(m[c]) if c in m else "" for c in cols) + " |\n")
        f.write("\n## RTL fingerprint\n\n```\n" + md5s + "```\n")
        f.write("\n## What this is not\n\n"
                "Area, timing and DRC only. Nothing here shows the chip computes anything\n"
                "(`docs/verification-rules.md` rule 3). Say which DRC: a router DRC count is\n"
                "the detailed router checking its own work, not a sign-off deck.\n")
    print(f"evidence -> {out}  ({kept} reports, {len(per_step)} steps)")
    print(open(os.path.join(out, "SUMMARY.md")).read())

if __name__ == "__main__":
    main()
