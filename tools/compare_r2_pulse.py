#!/usr/bin/env python3
"""R1 versus the R2 pulse candidate on the 110-point probe set: one comparison, one verdict (#333, plan101 §2).

Input: a `mono_artifact_probe.py sweep` record whose `pulse2x` rows were run
at a stated rectangle gain (and optional mixer compensation); its `r1` rows
are R1's engine. The rows pair by (waveform, note, cutoff, q, drive).

What differs between the two sides, and nothing else: the pulse2x engine's
rectangles are rendered through the 2x chain at the record's rectangle gain
(`pulse2x_rect_gain_q15`) and, when set, mixed at `pulse2x_rect_mix_comp`.
Stimulus, reference-free analysis, filter/preset settings and every other
engine option are identical (the saw is bit-identical between engines).

THE DECLARED RULE is the one `mono_artifact_probe.summarize` applied when it
was committed at d1fe013, before any 0.74 result existed: a change counts as
a regression when it is WORSE by more than MOVE_DB = 1.00 dB (exactly: 1.02
fails) on
  * absolute unwanted energy (dBFS),
  * unwanted energy relative to the intended signal,
  * upper wanted power relative to intended (darker is worse),
or when the candidate adds output rail samples or a dropout. Internal rails
(oscillator, mixer, reconstruction, decimation) are reported per point and
counted; they are not part of the declared rule and are not made so here.
A point whose output is inactive or whose stage refused is NO VERDICT for the
properties it cannot support, and is counted as such, never dropped.

Mandatory passes: the M5A/M5B case properties (the acceptance tests the
candidate is scored on) are reported separately by the caller; this tool
counts probe conditions only.
"""
from __future__ import annotations

import argparse
import gzip
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import mono_artifact_probe as mp  # noqa: E402

BAND = mp.MOVE_DB
PROPS = (("unwanted_dbfs", +1), ("unwanted_rel_db", +1), ("upper_wanted_rel_db", -1))


def _load(paths):
    rows, meta = [], {}
    for p in paths:
        op = gzip.open if str(p).endswith(".gz") else open
        with op(p, "rt") as fh:
            d = json.load(fh)
        rows += d["rows"]
        for k in ("pulse2x_rect_gain_q15", "pulse2x_rect_mix_comp", "pulse2x_rect_drive_comp", "provenance"):
            meta.setdefault(k, d.get(k))
    return rows, meta


def _key(r):
    """Pair by the SCHEDULE's point. A repair that changes the rendered patch
    (e.g. drive compensation) must still pair with R1's row for the same
    condition; the first version keyed on the rendered patch, paired nothing,
    and reported acceptance over zero measured points."""
    p = r.get("nominal") or r["patch"]
    return (p["waves"][0], r["note"], p["cutoff"][0], p["q"], p["drive"])


def compare(rows, dev=mp.DEV_CONDITIONS):
    by = {(r["engine"],) + _key(r): r for r in rows}
    out, counts = [], {"measured": 0, "refused": 0, "not_run": 0}
    for k, cand in sorted(by.items()):
        if k[0] != "pulse2x":
            continue
        base = by.get(("r1",) + k[1:])
        wave, note, cut, q, drv = k[1:]
        rec = {"wave": wave, "note": note, "cutoff": cut, "q": q, "drive": drv,
               "development": (note, cut, q, drv) in dev and wave == "pulse29"}
        if base is None:
            rec["verdict"] = "NOT RUN (no R1 row)"
            counts["not_run"] += 1
            out.append(rec)
            continue
        if cand["verdict"] != "MEASURED" or base["verdict"] != "MEASURED":
            rec["verdict"] = f"NO VERDICT (r1 {base['verdict']}, candidate {cand['verdict']})"
            counts["refused"] += 1
            out.append(rec)
            continue
        counts["measured"] += 1
        ob, oc = base["stages"]["output"], cand["stages"]["output"]
        fails = []
        for prop, worse in PROPS:
            b, c = ob.get(prop), oc.get(prop)
            if b is None or c is None or "refused" in ob or "refused" in oc:
                rec[prop] = {"r1": b, "candidate": c, "verdict": "NO VERDICT"}
                continue
            d = round(c - b, 3)
            adverse = d * worse
            state = ("regressed beyond band" if adverse > BAND else
                     "improved beyond band" if -adverse > BAND else "within band")
            rec[prop] = {"r1": b, "candidate": c, "delta": d, "adverse": adverse > 0, "state": state}
            if state == "regressed beyond band":
                fails.append(prop)
        rec["intended_dbfs"] = {"r1": ob["intended_dbfs"], "candidate": oc["intended_dbfs"],
                                "delta": round(oc["intended_dbfs"] - ob["intended_dbfs"], 3)}
        rec["rails"] = {"r1": base["clip"], "candidate": cand["clip"]}
        if cand["clip"]["output_rail_samples"] > base["clip"]["output_rail_samples"]:
            fails.append("new output rail samples")
        db, dc = base.get("dropout_depth_db"), cand.get("dropout_depth_db")
        rec["dropout_depth_db"] = {"r1": db, "candidate": dc}
        if dc is not None and db is not None and dc < mp.DROPOUT_DB <= db:
            fails.append("new dropout")
        rec["fails_declared_rule"] = fails
        rec["verdict"] = "FAIL" if fails else "PASS"
        out.append(rec)
    return out, counts


def tally(recs):
    t = {}
    meas = [r for r in recs if r.get("verdict") in ("PASS", "FAIL")]
    for prop, _ in PROPS:
        v = [r[prop] for r in meas if "state" in r.get(prop, {})]
        t[prop] = {"any_adverse_change": sum(1 for x in v if x["adverse"]),
                   "improved_beyond_band": sum(1 for x in v if x["state"] == "improved beyond band"),
                   "within_band": sum(1 for x in v if x["state"] == "within band"),
                   "regressed_beyond_band": sum(1 for x in v if x["state"] == "regressed beyond band"),
                   "worst_adverse": max((x["delta"] for x in v), key=lambda d: d * dict(PROPS)[prop], default=None)}
    t["new_output_rail"] = sum(1 for r in meas if "new output rail samples" in r["fails_declared_rule"])
    t["new_dropout"] = sum(1 for r in meas if "new dropout" in r["fails_declared_rule"])
    for rail in ("oscillator_rail_samples", "mixer_rail_samples", "reconstruction_would_clip",
                 "decimation_would_clip"):
        t[f"points_with_more_{rail}"] = sum(
            1 for r in meas if (r["rails"]["candidate"].get(rail) or 0) > (r["rails"]["r1"].get(rail) or 0))
    t["points_failing_declared_rule"] = sum(1 for r in meas if r["verdict"] == "FAIL")
    return t


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("records", nargs="+")
    ap.add_argument("--out", type=pathlib.Path, required=True)
    a = ap.parse_args(argv)
    rows, meta = _load(a.records)
    recs, counts = compare(rows)
    dev = [r for r in recs if r.get("development")]
    unt = [r for r in recs if not r.get("development")]
    res = {"configuration": {"candidate": "pulse2x", "rect_gain_q15": meta["pulse2x_rect_gain_q15"],
                             "rect_mix_comp": meta["pulse2x_rect_mix_comp"],
                             "rect_drive_comp": meta.get("pulse2x_rect_drive_comp"), "baseline": "r1",
                             "differences": "rectangles through the 2x chain at rect_gain_q15 (and ladder drive "
                                            "x rect_drive_comp when set); nothing else"},
           "declared_rule": f"worse by > {BAND:.2f} dB on {[p for p, _ in PROPS]}, new output rail, new dropout",
           "counts": counts, "development": tally(dev), "untouched": tally(unt),
           "failing_points": [r for r in recs if r.get("verdict") == "FAIL"],
           "accepted_under_declared_rule": (
               "NO VERDICT" if counts["measured"] == 0 or counts["not_run"] or counts["refused"]
               else all(r.get("verdict") == "PASS" for r in recs if r.get("verdict") in ("PASS", "FAIL"))),
           "points": recs, "source_records": [str(p) for p in a.records], "probe_provenance": meta["provenance"]}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps({k: res[k] for k in ("configuration", "counts", "development", "untouched",
                                          "accepted_under_declared_rule")}, indent=1))
    for r in res["failing_points"]:
        print("FAIL", r["wave"], r["note"], r["cutoff"], r["q"], r["drive"], r["fails_declared_rule"],
              {p: r[p].get("delta") for p, _ in PROPS})
    v = res["accepted_under_declared_rule"]
    return 0 if v is True else (2 if v == "NO VERDICT" else 1)


if __name__ == "__main__":
    raise SystemExit(main())
