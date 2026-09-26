#!/usr/bin/env python3
"""Sweep the live session's lookahead and admission reserve on the declared load.

    .venv/bin/python fpga/sweep_live_midi.py

Issue #281 says: if the target fails, investigate lookahead, redundant writes
and batching FIRST, and never relax the target. The first declared-load run
did not miss the latency target -- it REFUSED five events (a crash on a
downbeat with a note, a kick and a hat) because the lookahead could not carry
the cluster plus the admission reserve. This measures the three levers
instead of arguing about them:

  lookahead   LOOKAHEAD_FRAMES over 12..18 ms (the latency floor moves with it)
  reserve     ADMISSION_RESERVE_PACKETS over 2..4
  redundant   how many executed writes re-write the value the register already
              holds (the upper bound on what eliding redundant writes can save)
  batching    how many drum STOPS writes share a frame window with another
              strike (the upper bound on what striking a cluster in one STOPS
              write can save)

Every row runs the SAME session and the SAME oracle (fpga/verify_live_midi.py)
with the contract's two numbers overridden, and reports the verdict of every
property -- a setting that admits the load by breaking something else shows it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "fpga"))

import live_midi_contract as C                      # noqa: E402
import verify_live_midi as vlm                      # noqa: E402
import drums_fx as dx                               # noqa: E402

OUT = ROOT / "fpga/reports/live-midi/sweep.json"


def redundancy(run: dict) -> dict:
    """Executed writes (static image first) that re-write the register's value."""
    image, redundant, by_class = {}, 0, {}
    for w in run["sim"].writes:
        key = (w[2], w[3])
        if w[5] == "event" and image.get(key) == w[4] and not (
                w[2] == 0 and w[3] in (0x20, 0x21)) and not (w[2] == 1 and w[3] == dx.A_STOPS):
            redundant += 1
            cls = "drum" if w[2] else "voice"
            by_class[cls] = by_class.get(cls, 0) + 1
        image[key] = w[4]
    strikes = [w for w in run["sim"].writes if w[5] == "event" and w[2] == 1
               and w[3] == dx.A_STOPS and w[4]]
    return {"redundant_writes": redundant, "by_class": by_class,
            "event_writes": sum(1 for w in run["sim"].writes if w[5] == "event"),
            "stops_on_writes": len(strikes)}


def one(lookahead_ms: float, reserve: int) -> dict:
    saved = (C.LOOKAHEAD_FRAMES, C.ADMISSION_RESERVE_PACKETS)
    C.LOOKAHEAD_FRAMES = int(round(lookahead_ms * 1e-3 * C.SR))
    C.ADMISSION_RESERVE_PACKETS = reserve
    try:
        run = vlm.run_session("sustained")
        r = vlm.check(run, target=True)
        p = vlm.check(vlm.run_session("pressure"))
        lat = r["latency"]
        return {"lookahead_ms": lookahead_ms, "reserve": reserve, "verdict": r["verdict"],
                "moved": [k for k, v in r["props"].items() if v["moved"]],
                "refused_declared": sum(1 for _, c in r["_exp"]["refusals"]
                                        if c == "queue-pressure"),
                "p95_ms": lat["p95_ms"], "p99_ms": lat["p99_ms"], "max_ms": lat["max_ms"],
                "pressure_verdict": p["verdict"],
                "pressure_refused": sum(1 for _, c in p["_exp"]["refusals"]
                                        if c == "queue-pressure"),
                "pressure_device_queue_peak": p["_run"]["sim"].evq_peak,
                **redundancy(run)}
    finally:
        C.LOOKAHEAD_FRAMES, C.ADMISSION_RESERVE_PACKETS = saved


def main() -> int:
    rows = [one(ms, res) for ms in (12, 13, 14, 15, 16, 17, 18) for res in (2, 3, 4)]
    print(f"{'L ms':>5} {'res':>3} {'verdict':>8} {'refused':>7} {'p95':>6} {'p99':>6} "
          f"{'max':>6} {'press.ref':>9} {'redund':>6} moved")
    for r in rows:
        print(f"{r['lookahead_ms']:>5} {r['reserve']:>3} {r['verdict']:>8} "
              f"{r['refused_declared']:>7} {r['p95_ms']:>6.2f} {r['p99_ms']:>6.2f} "
              f"{r['max_ms']:>6.2f} {r['pressure_refused']:>9} {r['redundant_writes']:>6} "
              f"{','.join(r['moved']) or '-'}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"tool": "fpga/sweep_live_midi.py", "workload": "sustained",
                               "seed": C.SUSTAINED_SEED, "seconds": C.SUSTAINED_S,
                               "rows": rows}, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
