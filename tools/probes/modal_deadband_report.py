#!/usr/bin/env python3
"""Summaries and the disposition for #350, computed from the committed tables.

    python3 tools/probes/modal_deadband_report.py DIR      DIR holds dev.json confirm.json population.json ground.json

Writes DIR/summary.json and prints the markdown tables that DIR/README.md quotes,
so no number in the README is typed by hand.

DISPOSITION RULE (frozen with the thresholds, before dev was run):
  * ARITHMETIC FLOOR: one int16 LSB at the output. A mode whose persistent
    int16 residual is below it cannot be larger than the output stage's own
    granularity (#618's `>> 15`). This is NOT a sound-facing limit.
  * "defect above the floor" needs a measured row above it in BOTH tables.
  * A sound-facing limit (persistent DC/tail floor; T20 error) is externally
    grounded only if the capture gate permits reading the recording's DC (for
    the DC/tail limit) and a same-machine repeatability exists at the shipped
    setting (for T20). Otherwise that limit is REFUSED and named.
  * exactly one of: "repair target established" | "no production-path defect
    above the qualified floor" | "REFUSED".
"""
from __future__ import annotations

import json
import pathlib
import sys

ARITH_FLOOR_INT16_LSB = 1.0
GAIN_REG = 14746            # dx.accent_reg(0.45), the scorer's bus gain (asserted in modal_deadband.py)


def load(d, n):
    return json.loads((pathlib.Path(d) / n).read_text())


def summarise(rec: dict) -> dict:
    rows = [r for t in rec["tables"] for r in t["rows"]]
    meas = [r for r in rows if r["status"] == "MEASURED" and r.get("int16_residual_peak_lsb") is not None]
    over = [r for r in meas if r["int16_residual_peak_lsb"] > ARITH_FLOOR_INT16_LSB]
    kinds = {}
    for r in meas:
        kinds[r["tail"]["kind"]] = kinds.get(r["tail"]["kind"], 0) + 1
    t20 = [t["voice"]["t20"] for t in rec["tables"]]
    t20m = [x for x in t20 if x["status"] == "MEASURED"]
    return dict(renders=len(rec["tables"]), cells=len(rows), by_status=rec["reconcile"]["by_status"],
                measured_with_bus=len(meas), over_arith_floor=len(over),
                max_int16_residual_lsb=max((r["int16_residual_peak_lsb"] for r in meas), default=None),
                max_state_residual_lsb=max((r["state_residual_peak_lsb"] for r in meas), default=None),
                tail_kinds=kinds, voices_tail_int16_peak_max=max(t["voice"]["tail_fixed_int16_peak"] for t in rec["tables"]),
                voices_over_floor=sum(1 for t in rec["tables"] if t["voice"]["tail_fixed_int16_peak"] > ARITH_FLOOR_INT16_LSB),
                t20_measured=len(t20m), t20_refused=len(t20) - len(t20m),
                t20_max_abs_pct=max((abs(x["delta_pct"]) for x in t20m), default=None),
                t20_refusal_reasons=sorted({x["reason"][:60] for x in t20 if x["status"] != "MEASURED"}))


def state_budget(amp: int, gain: int, lsb: float = ARITH_FLOOR_INT16_LSB) -> float:
    """State LSB whose bus term equals `lsb` int16 LSB: state*amp/65536*gain/32768."""
    return lsb * 65536.0 * 32768.0 / (amp * gain)


def disposition(dev: dict, conf: dict, ground: dict) -> dict:
    sd, sc = summarise(dev), summarise(conf)
    defect = sd["over_arith_floor"] > 0 and sc["over_arith_floor"] > 0
    dc_ok = ground["gate"].get("dc") == "PERMITTED"
    reasons = []
    if not dc_ok:
        reasons.append("persistent DC / tail-floor limit: capture gate refuses DC (" + "; ".join(ground["gate"]["reasons"]) + ")")
    reasons.append("T20 limit: no same-machine repeatability at the shipped settings of the voices that move "
                   "(only BD, at DECAY position A, 1.30 %, docs/bd-repeatability-measurement.md)")
    if not defect:
        verdict = "no production-path defect above the qualified floor" if dc_ok else "REFUSED"
    else:
        verdict = "repair target established" if (dc_ok and not reasons[1:]) else "REFUSED"
    return dict(verdict=verdict, defect_above_arithmetic_floor_confirmed=defect, refusals=reasons)


def by_sound(rec: dict) -> str:
    out = ["| sound | renders | measured rows | rows > 1 int16 LSB | max int16 resid (LSB) | voices with tail > 1 LSB | T20 measured / refused | max abs T20 delta |",
           "|---|---:|---:|---:|---:|---:|---|---:|"]
    sounds = sorted({t["voice"]["sound"] for t in rec["tables"]})
    for s in sounds:
        ts = [t for t in rec["tables"] if t["voice"]["sound"] == s]
        rows = [r for t in ts for r in t["rows"] if r["status"] == "MEASURED" and r.get("int16_residual_peak_lsb") is not None]
        tm = [t["voice"]["t20"] for t in ts if t["voice"]["t20"]["status"] == "MEASURED"]
        out.append(f"| {s} | {len(ts)} | {len(rows)} | {sum(r['int16_residual_peak_lsb'] > ARITH_FLOOR_INT16_LSB for r in rows)} | "
                   f"{max((r['int16_residual_peak_lsb'] for r in rows), default='-')} | "
                   f"{sum(t['voice']['tail_fixed_int16_peak'] > ARITH_FLOOR_INT16_LSB for t in ts)} | {len(tm)} / {len(ts) - len(tm)} | "
                   f"{max((abs(x['delta_pct']) for x in tm), default='-')} |")
    return "\n".join(out)


def table_md(rec: dict) -> str:
    out = ["| sound | accent | mode | amp | tail | departs (ms) | float level there | state resid (LSB) | bus resid (LSB) | int16 resid (LSB / dBFS) | voice T20 delta |",
           "|---|---:|---:|---:|---|---:|---:|---:|---:|---|---|"]
    for t in rec["tables"]:
        t20 = t["voice"]["t20"]
        t20s = f"{t20['delta_pct']:+.2f} %" if t20["status"] == "MEASURED" else "REFUSED"
        for r in t["rows"]:
            if r["status"] != "MEASURED":
                continue
            tail = r["tail"]
            ts = f"stuck {tail['value']}" if tail["kind"] == "stuck" else (
                f"cycle p={tail['period']}" if tail["kind"] == "limit-cycle" else tail["kind"])
            if r.get("int16_residual_peak_lsb") is None:
                bus, i16 = "REFUSED tap-only", "REFUSED tap-only"
            else:
                bus = f"{r['bus_residual_peak_lsb']}"
                i16 = (f"{r['int16_residual_peak_lsb']} / {r['int16_residual_dbfs']}"
                       if r["int16_residual_peak_lsb"] >= 1e-3 else "~0 (below float resolution)")
            out.append(f"| {r['sound']} | {r['accent']} | {r['mode']} | {r['amp']} | {ts} | {r['departure_ms']} | "
                       f"{r['float_level_at_departure_lsb']} | {r['state_residual_peak_lsb']} | {bus} | {i16} | {t20s} |")
    return "\n".join(out)


def main(argv=None) -> int:
    d = (argv or sys.argv[1:])[0]
    dev, conf, pop, g = (load(d, n) for n in ("dev.json", "confirm.json", "population.json", "ground.json"))
    res = dict(dev=summarise(dev), confirm=summarise(conf), population=summarise(pop),
               disposition=disposition(dev, conf, g),
               state_budget_for_1_int16_lsb={f"{r['sound']}:mode{r['mode']}": round(state_budget(r["amp"], GAIN_REG), 1)
                                              for t in (dev["tables"] + conf["tables"]) for r in t["rows"]
                                              if r["status"] == "MEASURED" and r.get("amp")})
    pathlib.Path(d, "summary.json").write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps(res, indent=1))
    for name, rec in (("DEV", dev), ("CONFIRM", conf), ("POPULATION", pop)):
        print(f"\n### {name}\n" + by_sound(rec) + "\n\n" + table_md(rec))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
