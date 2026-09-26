#!/usr/bin/env python3
"""tools/r1_harvest.py -- turn the R1 qualification run's trial receipts into
the committed evidence (#279). Build box, after the batch:

    python tools/r1_harvest.py --runs ~/work/r1q-out --to fpga/reports/r1-candidate

For every receipt under <runs>/trials: re-check it with THIS tree's
`tools/trial.py check-receipt` (a receipt that does not check is carried as
receipt_valid false, never dropped), then write one summary row per run:
identity (commit, sender/target image, kit revision and digest), workload,
expected vs observed coverage, verdict, evidence level and controls. The
receipts and their artifacts go into receipts.tgz; every RTL run identity is
copied out beside it so fpga/release/r1_candidate.py can bind it to the
candidate's sources. The domain tests are run here too and recorded by exit
status. Nothing is typed by hand: a missing receipt is a missing row, which
the scorecard reports as NO VERDICT.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "fpga" / "release"), str(ROOT / "fpga"),
                str(ROOT / "model")]
import trial                                              # noqa: E402
import r1_candidate as r1c                                # noqa: E402

RTL_LEVEL = "RTL simulation, Arty wrapper at its UART pins (digital production path; no image)"
SPI_LEVEL = "RTL simulation, synth_top at its SPI pins"
SIM_LEVEL = "host against the device contract on simulated time (no RTL)"
KIT = f"kit rev {r1c.CONTRACT_REVISION} {r1c.KIT_R14_SHA256[:12]}"

# (trial, mode) -> (gate, workload, evidence level, identity)
ROWS = {
    ("T-PLAY-DIGITAL", "r1"): ("implementation",
        "held notes (default, m5a-saw, m5a-pulse), `run --note 45 --fixture m5a`, `demo` and "
        "`bar808-full` as the CLI emits them (known-state preamble, full kit, three oscillators "
        "on the default patch, knob moves, glides on legato)", RTL_LEVEL,
        f"tree / tree (frozen), {KIT}"),
    ("T-DEADLINE", "sim"): ("implementation",
        "arty-uart: three audible 2x saws, glide, wheel, waveform and control writes over the UART "
        "pins; drum page at reset (strikes silent)", RTL_LEVEL + " + deadline monitor",
        "engineering stream (no kit)"),
    ("T-DEADLINE", "stress"): ("implementation",
        "stress-saw: three audible 2x oscillators, glide + oscillator/filter modulation, noise, "
        "full revision-14 kit loaded and all eleven stops struck, cutoff/resonance/volume/wheel/"
        "waveform writes, drum filter toggled (a superset of the player domain)",
        SPI_LEVEL + " + deadline monitor", f"engineering stream, {KIT}"),
    ("T-LIVE-MIDI", "sim"): ("playability",
        "coverage (incl. counter wrap), pressure, sustained 20 s declared load", SIM_LEVEL,
        f"session --image tree / frozen oracle, {KIT}"),
    ("T-LIVE-MIDI", "rtl"): ("playability",
        "the session's own bytes for coverage, pressure and 3 s of the declared load, replayed "
        "through the wrapper; two I2S controls", RTL_LEVEL, f"session --image tree / frozen oracle, {KIT}"),
    ("T-RELEASE-BOUND", "check"): ("trust",
        "R0's manifest and evidence binding, preserved on this branch", "binding check",
        "R0 (release, rev 11) -- not R1 evidence"),
}


def _git(*a) -> str:
    return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def coverage_text(child: dict) -> str:
    cov = child.get("coverage") or {}
    m = child.get("metrics") or {}
    exp, obs = cov.get("expected") or {}, cov.get("observed") or {}
    bits = []
    if "periods" in m and "periods_required" in m:
        bits.append(f"I2S {m.get('periods')}/{m.get('periods_required')} periods")
    if "worst_sample_slack" in m:
        bits.append(f"frames {m.get('frames')}, missed {m.get('missed')}, worst sample slack "
                    f"{m.get('worst_sample_slack')}")
    if "i2s_peak_lsb" in m:
        bits.append(f"I2S {m.get('periods')}/{m.get('periods_required')} periods, peak "
                    f"{m.get('i2s_peak_lsb')} LSB (floor {m.get('min_peak_lsb')})")
    for fx, v in (m.items() if isinstance(m, dict) else []):
        if isinstance(v, dict) and "writes_sent" in v:
            bits.append(f"{fx}: writes {v.get('writes_seen')}/{v.get('writes_sent')}, I2S "
                        f"{v.get('periods')}/{v.get('periods_required')} periods, mismatch "
                        f"{v.get('wire_mismatch')}, "
                        f"off-frame {v.get('frame_pred_bad')}, worst strobe "
                        f"{v.get('worst_strobe_cycle')}")
    if "latency_sustained" in m:
        lat = m["latency_sustained"]
        r2 = lambda v: round(v, 2) if isinstance(v, (int, float)) else v  # noqa: E731
        bits.append(f"latency n {lat.get('n')} p95 {r2(lat.get('p95_ms'))} p99 "
                    f"{r2(lat.get('p99_ms'))} ms")
    per = obs.get("i2s_periods")
    if isinstance(per, dict) and per and not any("writes_sent" in str(v) for v in m.values()):
        req = exp.get("i2s_periods") if isinstance(exp.get("i2s_periods"), dict) else {}
        bits.append("RTL I2S periods compared/required " + ", ".join(
            f"{k} {v}/{req.get(k, '?')}" for k, v in per.items()))
    if not bits:
        bits.append(f"expected {exp}, observed {obs}")
    return "; ".join(bits)


def harvest(runs: Path, to: Path) -> dict:
    receipts = sorted((runs / "trials").rglob("receipt.json"))
    rows = []
    heads = set()
    for rp in receipts:
        rec = json.loads(rp.read_text())
        chk = subprocess.run([sys.executable, str(ROOT / "tools/trial.py"), "check-receipt",
                              str(rp)], cwd=ROOT, capture_output=True, text=True)
        t, mode = rec["trial"]["id"], rec["trial"]["mode"]
        if rec["trial"]["candidate"] != "baseline":
            continue
        gate, workload, level, ident = ROWS.get((t, mode), ("trust", "?", "?", "?"))
        heads.add(rec["identities"]["source"]["head"])
        rows.append({
            "name": f"{t} {mode}", "gate": gate, "workload": workload, "level": level,
            "identity": f"`{rec['identities']['source']['head'][:8]}`; {ident}",
            "verdict": rec["verdict"], "verdict_reasons": rec["verdict_reasons"][:4],
            "receipt": str(rp.relative_to(runs)), "receipt_valid": chk.returncode == 0,
            "execution": rec["execution"]["status"],
            "coverage": " / ".join(f"{c['id']}: {coverage_text(c)}" for c in rec["children"]),
            "children": {c["id"]: {"verdict": c["verdict"], "metrics": c.get("metrics"),
                                   "coverage": c.get("coverage")} for c in rec["children"]},
            "controls": {c["id"]: bool(c.get("caught")) for c in rec["controls"]},
            "control_reasons": {c["id"]: c.get("reasons", [])[:2] for c in rec["controls"]},
        })
    dom = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                          "fpga/release/test_release_domain.py", "fpga/release/test_r1_candidate.py"],
                         cwd=ROOT, capture_output=True, text=True)
    tail = (dom.stdout.strip().splitlines() or ["?"])[-1]
    rows.append({"name": "supported domain + session start (unit)", "gate": "implementation",
                 "workload": "refusals with a reason (INC_RANGE, GLIDE_247, ROUTE_DRUMFILTER, "
                             "MOD_EXCURSION, WAVES, PULSE2X, NOT_IN_IMAGE R1 wording, preset with "
                             "fixture); known-state preamble; queued-event refusal; wrong-kit "
                             "controls", "level": "host code (no RTL)",
                 "identity": f"`{_git('rev-parse', 'HEAD')[:8]}`; R1 host (--image tree)",
                 "verdict": "PASS" if dom.returncode == 0 else "FAIL",
                 "coverage": f"pytest exit {dom.returncode}: {tail}", "receipt_valid": None,
                 "controls": {}})
    to.mkdir(parents=True, exist_ok=True)
    ids = to / "rtl-run-identities"
    if ids.exists():
        shutil.rmtree(ids)
    ids.mkdir()
    for p in sorted((runs / "trials").rglob("*run_identity.json")):
        rel = p.relative_to(runs / "trials")
        name = "__".join(rel.parts[:1] + rel.parts[2:])
        shutil.copyfile(p, ids / name)
    with tarfile.open(to / "receipts.tgz", "w:gz") as tf:
        tf.add(runs / "trials", arcname="trials")
    summary = {"schema": "r1-qualification-summary/1", "head": _git("rev-parse", "HEAD"),
               "origin_main": _git("rev-parse", "origin/main"), "evidence_heads": sorted(heads),
               "runs": rows}
    (to / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", type=Path, required=True)
    ap.add_argument("--to", type=Path, default=ROOT / "fpga/reports/r1-candidate")
    a = ap.parse_args(argv)
    s = harvest(a.runs.expanduser(), a.to)
    for r in s["runs"]:
        print(f"{r['name']:<42} {r['verdict']:<11} receipt_valid={r['receipt_valid']} "
              f"controls={r['controls']}")
    bad = [r for r in s["runs"] if r["receipt_valid"] is False]
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
