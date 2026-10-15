#!/usr/bin/env python3
"""Run nextpnr and turn its log into a fit report with a TRUTHFUL verdict.

Replaces the logic of report.sh, which decided "RESULT: routed." from the
ABSENCE of the text ERROR/Error: in the log, while fpga/Makefile discarded
nextpnr's exit status (`-cd ... | tail -40`). An empty log, an OOM-killed run
or a missing tool therefore printed "routed." (issue #565; docs/failure-modes.md:
a correct instrument in a wrong state that still answers).

Verdicts (a first-class outcome each, never inferred from silence):

  ROUTED        exit status 0 AND a populated utilisation block AND
                "Routing complete." AND a "Max frequency" line AFTER that
                marker AND every pack/timing stage exited 0 AND (when the
                target packs) the bitstream exists and is non-empty.
  DOES-NOT-FIT  nextpnr exited non-zero (not by signal) AND its log carries a
                diagnosed capacity/fit error (no BELs remaining, ...).
  REFUSED       everything else, with the reason named: missing tool or log,
                empty log, no recorded exit status, killed by signal,
                non-zero exit without a diagnosed fit error, a missing or
                mis-ordered marker, a failed pack stage.

Exit codes: 0 ROUTED (and DOES-NOT-FIT when --allow-no-fit, the headroom
probe); 1 DOES-NOT-FIT; 2 REFUSED.

Subcommands:
  run     delete this invocation's stale artefacts, run nextpnr (+ --after
          stages) with a fresh log, record every exit status, print the report.
          The report file --out is written only for ROUTED (or a permitted
          no-fit): a refused or failed run never replaces a committed report.
  report  re-derive the report from the files `run` left in the build dir
          (what report.sh used to do). No recorded status means REFUSED.
          It TRUSTS the status.json/log of the last `run`, which need not be
          the current build: use `run` for any verdict you intend to quote.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROUTED, NOFIT, REFUSED = "ROUTED", "DOES-NOT-FIT", "REFUSED"

TARGETS = {
    "ice40": dict(
        dev="iCE40 UP5K (SG48), iCEBreaker",
        pnr="ice40_pnr.log", stat="ice40_stat.txt", bit="ice40.bin",
        stale=("ice40.asc", "ice40.bin", "ice40_report.json", "ice40_icetime.txt")),
    "ecp5": dict(
        dev="ECP5 LFE5U-25F (CABGA381), ULX3S",
        pnr="ecp5_pnr.log", stat="ecp5_stat.txt", bit="ecp5.bit",
        stale=("ecp5.config", "ecp5.bit", "ecp5_report.json")),
    "ecp5hr": dict(
        dev="ECP5 LFE5U-25F (CABGA381) -- HEADROOM PROBE, MODES=16 NUMS=11",
        pnr="ecp5hr_pnr.log", stat="ecp5hr_stat.txt", bit=None,
        stale=("ecp5hr.config", "ecp5hr_report.json")),
}

# nextpnr diagnostics that mean "this design does not fit this device" as
# opposed to "the tool broke". Deliberately narrow: anything else is REFUSED.
FIT_ERRORS = re.compile(
    r"no BELs remaining|Unable to place cell|Failed to find a place|"
    r"Unable to find legal placement|Failed to route|"
    r"Placement failed|too many .* for device|exceeds? .*capacity", re.I)
ERROR_LINE = re.compile(r"ERROR|Error:")
UTIL_ROW = re.compile(r"^Info:\s+[A-Z][A-Za-z0-9_]*:\s+\d+/\s*\d+\s+\d+%")
MAXF = re.compile(r"Max frequency for clock")


@dataclass
class Verdict:
    state: str
    reasons: list = field(default_factory=list)
    errors: list = field(default_factory=list)


def classify(log, status):
    """log: text or None (missing). status: dict from `run`, or None.

    Pure function of its two inputs so the controls can drive it directly.
    """
    why = []
    if log is None:
        return Verdict(REFUSED, ["nextpnr log is missing (tool missing or did not run)"])
    if not log.strip():
        why.append("nextpnr log is empty")
    if status is None:
        return Verdict(REFUSED, why + ["no recorded nextpnr exit status "
                                       "(a log alone cannot prove the run succeeded)"])
    lines = log.splitlines()
    errors = [l for l in lines if ERROR_LINE.search(l)]
    nx = status.get("nextpnr", {})
    if nx.get("error"):
        return Verdict(REFUSED, why + [f"nextpnr could not be run: {nx['error']}"])
    rc, sig = nx.get("returncode"), nx.get("signal")
    if sig:
        return Verdict(REFUSED, why + [f"nextpnr was killed by signal {sig}"], errors[:5])
    if rc is None:
        return Verdict(REFUSED, why + ["nextpnr exit status not recorded"])
    if rc != 0:
        fit = [l for l in errors if FIT_ERRORS.search(l)]
        if fit:
            return Verdict(NOFIT, [f"nextpnr exited {rc} with a diagnosed fit error"], errors[:5])
        return Verdict(REFUSED, why + [f"nextpnr exited {rc} without a diagnosed fit error "
                                       "(unclassified tool failure)"], errors[:5])
    # rc == 0 from here.
    if errors:
        return Verdict(REFUSED, why + ["nextpnr exited 0 but its log contains error lines "
                                       "(inconsistent run)"], errors[:5])
    for st in status.get("after", []):
        if st.get("error") or st.get("signal") or st.get("returncode") != 0:
            what = st.get("error") or (f"killed by signal {st['signal']}" if st.get("signal")
                                       else f"exited {st.get('returncode')}")
            return Verdict(REFUSED, why + [f"{st.get('name', 'post stage')} {what}"])
    util = [i for i, l in enumerate(lines) if "Device utilisation" in l]
    rows = []
    if util:
        for l in lines[util[0]:]:
            if UTIL_ROW.match(l):
                rows.append(l)
            if l.startswith("Info: Placed"):
                break
    route = [i for i, l in enumerate(lines) if "Routing complete" in l]
    missing = []
    if not util:
        missing.append("'Device utilisation' block")
    elif not rows:
        missing.append("populated utilisation rows (block is empty)")
    if not route:
        missing.append("'Routing complete.'")
    else:
        r = route[-1]
        if util and util[0] > r:
            missing.append("utilisation block BEFORE 'Routing complete.' (order)")
        if not any(MAXF.search(l) for l in lines[r + 1:]):
            missing.append("'Max frequency' line AFTER 'Routing complete.'")
    if missing:
        return Verdict(REFUSED, why + ["incomplete nextpnr log, missing: " + "; ".join(missing)])
    if why:
        return Verdict(REFUSED, why)
    return Verdict(ROUTED, ["exit 0; utilisation, routing-complete and post-route timing present"])


# ----------------------------------------------------------------- the report
def _sed_range_util(lines):
    """sed -n '/Device utilisation/,/^Info: Placed/p', ranges may reopen."""
    out, on = [], False
    for l in lines:
        if not on and "Device utilisation" in l:
            on = True
        if on:
            out.append(l)
            if re.match(r"Info: Placed", l):
                on = False
    return out


def _stat_part(stat_text, top):
    out = []
    if stat_text is None:
        return ["   (no yosys statistics: stat file missing)"]
    hdr = f"=== {top} ==="
    seg, on = [], False
    for l in stat_text.splitlines():
        if l == hdr:
            on = True
        if on:
            seg.append(l)
    pat = re.compile(r"^\s+[0-9]+\s+(SB_|TRELLIS|DP16KD|MULT|CCU2|L6MUX|PFUMX)")
    out += ["   " + l.lstrip(" ") for l in seg if pat.match(l)]
    for l in seg:
        if l.endswith("cells"):
            out.append("   total mapped cells: " + l.split()[0])
            break
    return out


def _info(l):
    return "   " + l[len("Info:"):] if l.startswith("Info:") else l


def render(target, top, log, stat_text, bit_path, verdict):
    t = TARGETS[target]
    o = [f"=== {t['dev']} : synth_top with the REAL drum section, {top} wrapper ===", "",
         "   contents: synth_top + spi_ctl + voice_dp + recip_div + ladder_dp_n + i2s_tx",
         "             + drum_regs + drum_kit (drum_dp + modal_dp).  No placeholder."]
    if target == "ecp5hr":
        o += ["",
              "   THIS IS NOT THE BUILD. It is fpga/rtl/headroom_top.v: the same wrapper",
              "   and the same PLL with synth_top's drum parameters raised from the",
              "   shipping MODES=12 NUMS=6 to MODES=16 NUMS=11, which is what completing",
              "   the 808 (8 of 11 circuits today) costs. Nothing else differs, so the",
              "   difference against ecp5_25f.txt is the drum growth and only that.",
              "   The extra modes are unpopulated and the register map does not yet have",
              "   room for 16 of them -- see docs/fpga-build.md section 6."]
    o += ["", "-- FPGA synthesised (yosys technology mapping; NOT place-and-routed) --"]
    o += _stat_part(stat_text, top)
    o += ["", "-- FPGA place-and-route --"]
    lines = (log or "").splitlines()
    if log and "Device utilisation" in log:
        o.append("  device utilisation:")
        o += [_info(l) for l in _sed_range_util(lines) if re.match(r"Info:\s+[A-Z]", l)]
    o.append("")
    if verdict.state == ROUTED:
        o.append("  RESULT: routed.")
    elif verdict.state == NOFIT:
        o.append("  RESULT: DOES NOT FIT / did not complete.")
        o += ["    " + l for l in verdict.errors[:5]]
    else:
        o.append("  RESULT: REFUSED -- no verdict on fit or timing can be given.")
        o += ["    reason: " + r for r in verdict.reasons]
        o += ["    " + l for l in verdict.errors[:5]]
    o += ["", "  clock constraints nextpnr DERIVED (ecp5: from the PLL instance, not from the LPF):"]
    o += [_info(l) for l in lines if re.search(
        r"Input frequency of PLL|Derived frequency constraint|promoting clock net", l)]
    if "Derived frequency constraint" not in (log or ""):
        o += ["   (none derived; the constraint is whatever --freq / the LPF asserted --",
              "    an asserted constraint is a promise the checker verifies, NOT a frequency",
              "    the board produces. See docs/fpga-clock.md.)"]
    o += ["", "  achieved Fmax. nextpnr prints this twice: once after placement (an",
          "  ESTIMATE) and once after \"Routing complete.\" (the RESULT). Both are shown",
          "  so nobody quotes the estimate by accident."]
    mf = [l for l in lines if MAXF.search(l)]
    if mf and verdict.state != ROUTED:
        # Without a routed verdict the last Max frequency line may be the
        # placement estimate; labelling it "post-route RESULT" invites quoting it.
        o.append("   (suppressed: no routed verdict, so no Fmax figure is a RESULT)")
    elif mf:
        o.append(_info(mf[0]).replace("   ", "   post-placement ESTIMATE: ", 1))
        o.append(_info(mf[-1]).replace("   ", "   post-route RESULT      : ", 1))
    o += ["", "  worst cross-domain delays (unconstrained I/O paths):"]
    o += [_info(l) for l in [l for l in lines if re.search(r"Max delay .*posedge", l)][-3:]]
    o.append("")
    if bit_path is not None and bit_path.is_file() and verdict.state == ROUTED:
        o.append(f"  bitstream: {bit_path.name} {bit_path.stat().st_size} bytes")
    elif t["bit"] is None and verdict.state == ROUTED:
        o.append("  bitstream: none (headroom probe is routed only, never packed)")
    else:
        o.append("  bitstream: none (no routed design)")
    o += ["", "  NOTE: this is a physical result only. It says nothing about whether the",
          "  design computes the right samples; see docs/verification-rules.md rule 3."]
    return "\n".join(o) + "\n"


# ------------------------------------------------------------------ plumbing
def _read(p):
    try:
        return Path(p).read_text(errors="replace")
    except OSError:
        return None


def _paths(target, build):
    t, b = TARGETS[target], Path(build)
    return (b / t["pnr"], b / t["stat"], (b / t["bit"]) if t["bit"] else None,
            b / f"{target}_pnr.status.json")


def evaluate(target, top, build):
    log_p, stat_p, bit_p, st_p = _paths(target, build)
    log = _read(log_p)
    try:
        status = json.loads(st_p.read_text())
    except (OSError, ValueError):
        status = None
    v = classify(log, status)
    if v.state == ROUTED and bit_p is not None:
        if not (bit_p.is_file() and bit_p.stat().st_size > 0):
            v = Verdict(REFUSED, [f"{bit_p.name} missing or empty after a 'routed' log"])
    return v, render(target, top, log, _read(stat_p), bit_p, v)


def _exec(cmd, cwd, out_path):
    """Run cmd (no shell); return {returncode|signal|error}. Output to out_path."""
    try:
        with open(out_path, "w") as fh:
            p = subprocess.run(cmd, cwd=cwd, stdout=fh, stderr=subprocess.STDOUT)
    except OSError as exc:
        return {"error": f"{cmd[0]}: {exc}"}
    if p.returncode < 0:
        return {"signal": -p.returncode, "returncode": p.returncode}
    return {"returncode": p.returncode}


def _exit_code(v, allow_no_fit):
    if v.state == ROUTED or (v.state == NOFIT and allow_no_fit):
        return 0
    return 1 if v.state == NOFIT else 2


def cmd_run(a):
    build = Path(a.build)
    build.mkdir(parents=True, exist_ok=True)
    log_p, _, bit_p, st_p = _paths(a.target, build)
    console = Path(str(log_p) + ".console")
    # Per-invocation state: nothing from an earlier run may vouch for this one.
    for name in (*TARGETS[a.target]["stale"], log_p.name, st_p.name, console.name,
                 f"{a.target}_refused.txt", f"{a.target}_nofit.txt"):
        (build / name).unlink(missing_ok=True)
    log_p.write_text("")
    status = {"nextpnr": _exec(a.cmd, build, console), "after": []}
    if "returncode" in status["nextpnr"] and status["nextpnr"]["returncode"] == 0:
        for s in a.after:
            r = _exec(shlex.split(s), build, build / f"{a.target}_after.log")
            r["name"] = shlex.split(s)[0]
            status["after"].append(r)
            if r.get("error") or r["returncode"] != 0:
                break
    st_p.write_text(json.dumps(status, indent=1))
    v, text = evaluate(a.target, a.top, build)
    sys.stdout.write(text)
    if v.state == ROUTED or (v.state == NOFIT and a.allow_no_fit):
        Path(a.out).write_text(text)
    else:
        # The committed report stays. The attempt is kept beside the build.
        name = f"{a.target}_{'nofit' if v.state == NOFIT else 'refused'}.txt"
        (build / name).write_text(text)
    if v.state != ROUTED:
        tail = (_read(console) or "").splitlines()[-40:]
        print(f"pnr_report: {v.state}: " + "; ".join(v.reasons), file=sys.stderr)
        if tail:
            print("pnr_report: last console lines:\n  " + "\n  ".join(tail), file=sys.stderr)
        print(f"pnr_report: {a.out} NOT updated (previous report, if any, preserved)",
              file=sys.stderr)
    return _exit_code(v, a.allow_no_fit)


def cmd_report(a):
    v, text = evaluate(a.target, a.top, a.build)
    sys.stdout.write(text)
    if v.state != ROUTED:
        print(f"pnr_report: {v.state}: " + "; ".join(v.reasons), file=sys.stderr)
    return _exit_code(v, a.allow_no_fit)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = []
    if "--" in argv:
        i = argv.index("--")
        argv, cmd = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="sub", required=True)
    here = Path(__file__).resolve().parent.parent / "build"
    for name in ("run", "report"):
        p = sub.add_parser(name)
        p.add_argument("target", choices=sorted(TARGETS))
        p.add_argument("top", nargs="?", default="fpga_top")
        p.add_argument("--build", default=str(here))
        p.add_argument("--allow-no-fit", action="store_true",
                       help="a diagnosed no-fit is a legitimate answer (headroom probe)")
        if name == "run":
            p.add_argument("--out", required=True)
            p.add_argument("--after", action="append", default=[],
                           help="post stage (packer/timer), shlex-split, run in --build")
    a = ap.parse_args(argv)
    if a.target == "ecp5hr":
        a.allow_no_fit = True
    if a.sub == "run":
        if not cmd:
            ap.error("run needs the nextpnr command after --")
        a.cmd = cmd
        return cmd_run(a)
    return cmd_report(a)


if __name__ == "__main__":
    sys.exit(main())
