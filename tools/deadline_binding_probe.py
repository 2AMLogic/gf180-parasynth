#!/usr/bin/env python3
"""Ground `verify_deadline.stimulus_binding` in the simulator (#443).

    .venv/bin/python tools/deadline_binding_probe.py            # every probe, ~80 s each, --jobs 4
    .venv/bin/python tools/deadline_binding_probe.py --only voice-inc
    .venv/bin/python tools/deadline_binding_probe.py --history 8c3de22 a224050   # no simulator

`--history A B` is the natural experiment, read from git rather than re-run:
the committed `-l2` capture at two revisions, file by file, with what the
binding says about the two stimuli. For #426 (8c3de22 -> a224050) it reports
2 of 461 command lines differing (drum ENV_PEAK[14], PATH[15]), 0 of 2159
schedule lines, 997 of 2158 I2S periods -- the sound moved, the schedule did
not.

The binding admits a committed capture when the tree's stimulus differs from
the capture's only in drum parameter values, on an ARGUMENT (drum RTL control
flow reads no register value). This probe is the argument's external check:
for each perturbation of the stress-saw stimulus it asks the binding what it
predicts, then runs the perturbed stimulus through the real SPI bench and
compares the per-frame schedule trace with the committed `-l2` capture's.

  ADMITTED + schedule identical     the relaxation held on this condition
  ADMITTED + schedule moved         FALSE ADMISSION -- the only failure that
                                    matters; exit 1
  REFUSED  + schedule moved         the refusal was needed
  REFUSED  + schedule identical     conservative, not a failure -- EXCEPT for a
                                    probe marked as the control (expect_moves):
                                    a control that does not move the schedule
                                    demonstrates nothing, NO VERDICT, exit 2

Preconditions, REFUSED (exit 2) rather than reported: the committed capture's
stimulus must equal this tree's byte for byte (otherwise "identical to the
capture" is not a statement about the perturbation), and the tree's drum RTL
must be the RTL the argument was made on.

Heavy: one iverilog compile + run per probe. Run it on the build box
(CLAUDE.md), not a laptop.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import shutil
import sys
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "rtl-sketch"))

import verify_deadline as vd  # noqa: E402

RECORD = "prod-stress-saw-l2"
TRACE = os.path.join(ROOT, "docs", "deadline", "traces", RECORD)
SCHED = "top_wrs_VOICE_OSC_2X_VOICE_FILTER_2X.txt.sched.gz"
#: the values #426's re-capture replaced (lines 150 and 176 of top_bx_cmds.txt):
#: ENV_PEAK[14] (0x79) and PATH[15] (0x9F). The natural experiment: the stale
#: and the fresh capture's schedule traces were byte-identical.
PRE_426 = {(1, 0x79): 5754585, (1, 0x9F): 14711203}


def _drum_param(c):
    return c[2] == vd.SEC_D and c[3] != vd.dx.A_STOPS


def perturb(cmds, name):
    """The perturbed stimulus for one named probe. Returns (cmds, expect_moves)."""
    out = [list(c) for c in cmds]
    if name == "drum-426":                       # #426 in reverse: the two values it moved
        hit = 0
        for c in out:
            if (c[2], c[3]) in PRE_426:
                c[4] = PRE_426[(c[2], c[3])]; hit += 1
        assert hit == len(PRE_426), f"drum-426: {hit} of {len(PRE_426)} target writes found"
        return [tuple(c) for c in out], False
    if name == "drum-all-params":                # every drum parameter value, low bits flipped
        for c in out:
            if _drum_param(c):
                c[4] ^= 0x5
        return [tuple(c) for c in out], False
    if name == "drum-env-ctl":                   # every ENV_CTL word (hold/choke/final fields)
        for c in out:
            if c[2] == vd.SEC_D and vd.dx.A_ENV <= c[3] < vd.dx.A_PATH and (c[3] - vd.dx.A_ENV) % 4 == 0:
                c[4] ^= 0x7FFFFFF
        return [tuple(c) for c in out], False
    if name == "voice-inc":                      # THE CONTROL: first note two octaves up
        k = next(i for i, c in enumerate(out) if c[2] == vd.SEC_V and c[3] == vd.stm.A_INC)
        for c in out[k:k + 3]:
            assert c[2] == vd.SEC_V and vd.stm.A_INC <= c[3] < vd.stm.A_INC + 3
            c[4] = min(c[4] * 4, 0xFFFFFF)
        return [tuple(c) for c in out], True
    raise SystemExit(f"deadline_binding_probe: REFUSED -- unknown probe {name!r}")


PROBES = ("drum-426", "drum-all-params", "drum-env-ctl", "voice-inc")


def classify(kind, base_rows, rows, expect_moves):
    """(status, word) for one probe: 0 held / needed / conservative, 1 false
    admission, 2 no verdict."""
    if not rows:
        return 2, "NO VERDICT (no schedule trace)"
    moved = rows != base_rows
    if kind != "refused":
        return (1, "FALSE ADMISSION: admitted, schedule moved") if moved else (0, "admitted, schedule identical")
    if moved:
        return 0, "refused, schedule moved (the refusal was needed)"
    if expect_moves:
        return 2, "NO VERDICT: the control did not move the schedule, so it demonstrates nothing"
    return 0, "refused, schedule identical (conservative)"


def base_rows():
    """The committed capture's schedule rows (FIELDS only), strictly parsed."""
    import tempfile
    with gzip.open(os.path.join(TRACE, SCHED), "rt") as fh, \
            tempfile.NamedTemporaryFile("w", suffix=".sched", delete=False) as tmp:
        tmp.write(fh.read())
    try:
        rows, problems = vd.read_sched(tmp.name)
    finally:
        os.remove(tmp.name)
    if problems:
        raise SystemExit(f"deadline_binding_probe: REFUSED -- committed schedule: {problems[0]}")
    return rows


def run_one(name):
    cmds, tail, meta = vd.SPI_SCENARIOS["stress-saw"](False)
    pert, expect = perturb(cmds, name)
    base = base_rows()
    rec = json.load(open(os.path.join(ROOT, "docs", "deadline", "runs", RECORD + ".json")))
    # the committed capture (whose stimulus main() asserted is `cmds`) against
    # a tree whose scenario would build `pert`
    kind, why = vd.stimulus_binding(cmds, pert, sched_rows=base, record=rec)
    vd.SPI_SCENARIOS["probe"] = lambda short: (pert, tail, meta)
    out = os.path.join(ROOT, "build", "dl-probe", name)
    shutil.rmtree(out, ignore_errors=True)
    res = vd.run_spi("probe", osc2x=True, filter2x=True, pulse2x=False, inject=None, outdir=out, short=False)
    rows = [{k: r[k] for k in vd.FIELDS} for r in ((res or {}).get("sched_rows") or [])]
    st, word = classify(kind, base, rows, expect)
    s = vd.analyse_sched([dict(r) for r in rows], vd.chip_go_cycle()) if rows else {}
    return dict(probe=name, binding=kind, binding_reasons=why, expect_moves=expect, status=st, verdict=word,
                frames=len(rows), frames_differing=sum(1 for x, y in zip(base, rows) if x != y),
                worst_sample_slack=s.get("worst_sample_slack"), missed=s.get("missed"),
                strobe_range=s.get("strobe_range"))


def history(rev_a, rev_b):
    """Per archived file of the committed capture, lines differing between two
    revisions; plus the binding's kind for the two recorded stimuli."""
    import subprocess

    def get(rev, f):
        blob = subprocess.run(["git", "show", f"{rev}:docs/deadline/traces/{RECORD}/{f}"], cwd=ROOT,
                              capture_output=True, check=True).stdout
        return gzip.decompress(blob).decode().splitlines()
    out = {}
    for f in sorted(os.listdir(TRACE)):
        a, b = get(rev_a, f), get(rev_b, f)
        out[f[:-3]] = dict(lines=[len(a), len(b)], differing=sum(1 for x, y in zip(a, b) if x != y)
                           + abs(len(a) - len(b)))
    ca = [tuple(int(x) for x in ln.split()) for ln in get(rev_a, "top_bx_cmds.txt.gz")]
    cb = [tuple(int(x) for x in ln.split()) for ln in get(rev_b, "top_bx_cmds.txt.gz")]
    out["projection_equal"] = vd.schedule_projection(ca) == vd.schedule_projection(cb)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", action="append", choices=PROBES)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--history", nargs=2, metavar=("REV_A", "REV_B"))
    ap.add_argument("--json", default=os.path.join(ROOT, "build", "dl-probe", "probe.json"))
    a = ap.parse_args(argv)
    if a.history:
        print(json.dumps(history(*a.history), indent=1))
        return 0
    cmds, _, _ = vd.SPI_SCENARIOS["stress-saw"](False)
    with gzip.open(os.path.join(TRACE, "top_bx_cmds.txt.gz"), "rt") as fh:
        captured = [tuple(int(x) for x in ln.split()) for ln in fh if ln.strip()]
    if captured != list(cmds):
        print(f"deadline_binding_probe: REFUSED -- {RECORD} was not driven by this tree's stimulus; "
              "re-capture it first (tools/deadline_recapture_l2.py)")
        return 2
    if vd.drum_rtl_hashes() != vd.DRUM_LATENCY_ARGUED_AT:
        print("deadline_binding_probe: REFUSED -- the drum RTL is not the RTL the argument was made on")
        return 2
    names = a.only or list(PROBES)
    with ProcessPoolExecutor(max_workers=a.jobs) as ex:
        results = list(ex.map(run_one, names))
    for r in results:
        print(f"{r['probe']:16s} binding={r['binding']:11s} frames={r['frames']} "
              f"differing={r['frames_differing']} worst_slack={r['worst_sample_slack']} "
              f"missed={r['missed']}  {r['verdict']}")
    os.makedirs(os.path.dirname(a.json), exist_ok=True)
    json.dump(results, open(a.json, "w"), indent=1)
    worst = max(r["status"] for r in results)
    return 1 if any(r["status"] == 1 for r in results) else worst


if __name__ == "__main__":
    sys.exit(main())
