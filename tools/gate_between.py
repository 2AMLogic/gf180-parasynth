#!/usr/bin/env python3
"""The between-recording bar for the perceptual gate, rule v2 (#379).

WHY A SECOND RULE. `gate_calibrate.py` (rule v1) froze a rule before any
distance was read, ran it on the MARS 808 library, and REFUSED: no k in
(1, 3, 5) let a bar built from the development keys cover the calibration
keys. That refusal is kept and published. Rule v1 had two defects, found by
sweeping rather than arguing, and v2 repairs exactly those:

  1. THE VALIDATION STATISTIC WAS A SINGLE HASH SPLIT. With 5-30 keys per
     group, "the calibration group's nearest take passes a bar built from the
     development group" is a coin flip on which keys landed where. v2 repeats
     it over SPLITS fixed splits (a seeded shuffle of the pool's keys in
     halves, both directions) and reports a rate, not one outcome.
  2. k WAS AN ABSOLUTE COUNT. "The 5 nearest keys" means 5 of 7 for a conga and
     5 of 72 for the bass drum: two different statements under one name. v2's
     dial is a FRACTION p of the pool's keys: "as close as the nearest p of the
     real recordings of this voice".

LINEAGE (unchanged, and still binding). The pack is ONE machine, recorded once,
through an API 1608 / Apogee chain, group-normalised per voice
(`docs/corpus-lineage.md`). The Fischer target is a different recording.
Neither documents a serial number. So the claim this module can make is
`cross-recording`: "no farther from the Fischer target than the nearest p of
the MARS recordings of the same voice". "As close as another real UNIT" is not
claimed; the chain, the mastering, the knob setting and any unit spread are
inseparable in the number. The operator's ruling (2026-10-02) made this
distance the bar; it is a decision, not something the data proves is unit
variation.

WHAT WAS FROZEN WHEN. Honest ordering, because it is the thing a reader cannot
check from the code: rule v1 and its REFUSED result existed first
(docs/scorecard/gate-379/README.md section 8). Rule v2 was designed AFTER an
exploration that ran half-splits over ALL keys, VAL included, and saw that
coverage rises with the matched fraction. So the VAL group is untouched by
v2's selection computation but NOT by v2's design; its pass rate is a check on
the procedure, not an independent test. The independent test is a second real
808 (the Boutique cross-check), which is not on the host that ran this.

RULE v2.
  pool       Clean/Digital and Clean/Tape takes, "Combo" excluded; keys as v1
             (a take and its Tape twin share a key). A key's representative is
             its take nearest the target by `spec`.
  groups     v1's hash: 0 development, 1 calibration, 2 VAL (never touched by
             the selection).  POOL = groups 0 + 1.
  matched    the nearest ceil(p * n_keys) keys by `spec`.
  bar        per feature, the max over the matched keys' representatives, plus
             the apparatus floor, with the gate's perceptual floors. No fit.
  selection  p is the smallest of P_GRID for which, over the sounds with at
             least MIN_KEYS pool keys, the mean rate at which the best take of
             one random half of the POOL passes the bar built from the other
             half (SPLITS seeded splits, both directions) is >= COVERAGE. If
             none reaches it: REFUSED, no looser p.
  final      the bar is rebuilt from the whole POOL at the chosen p (twice the
             keys the selection saw, so the selection was conservative).
  VAL        the best VAL take passes the final bar: recorded per sound,
             counted in Q4 as before.
  WEAK       a sound with fewer than MIN_KEYS pool keys (the single-take
             voices: two keys, A and B) cannot hold anything out. Its bar is
             the max over its MARS_WEAK_KEYS nearest keys (all of them for the
             single-take voices), from ALL keys, and it is labelled
             WEAK-UNVALIDATED. It is a weaker bar, said so wherever it is used.
  QUALIFIED  gate_calibrate's Q1-Q5, unchanged, plus Q6: a bar that did not
             come from the selection (WEAK) is never counted as validated.
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import gate_calibrate as gc  # noqa: E402
import perceptual_gate as pg  # noqa: E402

P_GRID = (0.05, 0.10, 0.15, 0.20, 0.30, 0.50)
#: Reported, never selected from: p values beyond the selectable grid, so the
#: refusal to widen after seeing data stays visible.
P_SWEEP = P_GRID + (0.75, 1.0)
SPLITS = 200
SPLIT_SEED = 379
COVERAGE = 0.80
MIN_KEYS = 8
MARS_WEAK_KEYS = 2


def reps(rows: list) -> list:
    """One representative take per key: the one nearest the target by `spec`."""
    best = {}
    for r in rows:
        if r["key"] not in best or r["d"]["spec"] < best[r["key"]]["d"]["spec"]:
            best[r["key"]] = r
    return sorted(best.values(), key=lambda r: (r["d"]["spec"], r["key"]))


def n_matched(n_keys: int, p: float) -> int:
    return max(1, math.ceil(p * n_keys - 1e-9))


def bar_p(rows: list, floor: dict, p: float) -> dict | None:
    rp = reps(rows)
    return gc.make_bar(rp[:n_matched(len(rp), p)], floor) if rp else None


def split_coverage(rows: list, floor: dict, p: float, splits: int = SPLITS, seed: int = SPLIT_SEED) -> dict:
    """Rate at which the best take of one random half of the keys passes the
    bar built from the other half. Seeded: the same table gives the same rate."""
    keys = sorted({r["key"] for r in rows})
    rng = random.Random(f"{seed}:{len(keys)}")
    ok = n = 0
    fails: dict = {}
    for _ in range(splits):
        ks = keys[:]
        rng.shuffle(ks)
        half = set(ks[:len(ks) // 2])
        for a, b in ((half, set(keys) - half), (set(keys) - half, half)):
            A = [r for r in rows if r["key"] in a]
            B = [r for r in rows if r["key"] in b]
            bar = bar_p(A, floor, p)
            tgt = reps(B)[0]
            v = pg.verdict(tgt["d"], bar)
            n += 1
            ok += v["verdict"] == "PASS"
            for f in v["failing"]:
                fails[f] = fails.get(f, 0) + 1
    return {"rate": ok / n, "n": n, "top_failing": sorted(fails.items(), key=lambda x: -x[1])[:3]}


def pool_rows(t: dict) -> list:
    return [r for r in t["rows"] if r["group"] in (0, 1)]


def val_rows(t: dict) -> list:
    return [r for r in t["rows"] if r["group"] == 2]


def choose_p(tabs: dict) -> dict:
    """Selection over the sounds that can hold something out. Sweeps P_SWEEP
    for the record; only P_GRID is selectable."""
    elig = {s: t for s, t in tabs.items() if len({r["key"] for r in pool_rows(t)}) >= MIN_KEYS}
    by_p = {}
    for p in P_SWEEP:
        per = {s: split_coverage(pool_rows(t), t["floor"], p) for s, t in elig.items()}
        mean = sum(v["rate"] for v in per.values()) / len(per) if per else None
        by_p[p] = {"mean_rate": mean, "per_sound": {s: v["rate"] for s, v in per.items()},
                   "top_failing": {s: v["top_failing"] for s, v in per.items()}}
    chosen = next((p for p in P_GRID if by_p[p]["mean_rate"] is not None and by_p[p]["mean_rate"] >= COVERAGE), None)
    return {"eligible": sorted(elig), "by_p": by_p, "chosen": chosen, "coverage_required": COVERAGE,
            "min_keys": MIN_KEYS, "splits": SPLITS}


def calibrate(tabs: dict, force_p: float | None = None) -> dict:
    """Pure function of the per-take tables. Same output shape as
    gate_calibrate.calibrate (`sounds[s]["bar"]`) so `qualify`/`decide` run
    unchanged."""
    tabs = gc.regroup(tabs)
    sel = choose_p(tabs)
    p = sel["chosen"] if force_p is None else force_p
    out = {"rule": "v2", "selection": sel, "p": p, "sounds": {}, "twins": gc.twin_report(tabs),
           "diagnostic": ("DIAGNOSTIC: p overridden by hand; NOT acceptance authority"
                          if force_p is not None else None)}
    if p is None:
        out["status"] = "REFUSED: no p in P_GRID reaches the split-coverage requirement"
        return out
    for s, t in tabs.items():
        pool, val = pool_rows(t), val_rows(t)
        nkeys = len({r["key"] for r in pool})
        row = {"keys_total": len(t["keys"]), "pool_keys": nkeys, "n_takes": len(t["rows"]),
               "no_verdict": t["missing"]}
        if nkeys >= MIN_KEYS:
            bar = bar_p(pool, t["floor"], p)
            row.update(bar=bar, kind=f"MARS-nearest-{p:.0%}-of-pool", n_matched=n_matched(nkeys, p),
                       bar_from=[r["rel"] for r in reps(pool)[:n_matched(nkeys, p)]])
            vr = reps(val)
            if not vr:
                row["status"] = "UNVALIDATED: no untouched key"
            else:
                v = pg.verdict(vr[0]["d"], bar)
                row["validation"] = {"matched": vr[0]["rel"], "best_passes": v["verdict"] == "PASS",
                                     "worst_ratio_best": v["worst_ratio"], "worst_feature": v["worst_feature"]}
                row["status"] = ("VALIDATED" if row["validation"]["best_passes"]
                                 else "VALIDATION-FAILED: best untouched take fails the bar")
        else:
            rp = reps(t["rows"])[:MARS_WEAK_KEYS]
            row.update(bar=gc.make_bar(rp, t["floor"]), kind=f"WEAK-MARS-nearest-{len(rp)}-keys",
                       n_matched=len(rp), bar_from=[r["rel"] for r in rp],
                       status="WEAK-UNVALIDATED: too few keys to hold any out")
        out["sounds"][s] = row
    out["status"] = "DIAGNOSTIC-CALIBRATION" if force_p is not None else "CALIBRATED"
    return out


def rank_rows(old_bar_context: dict) -> list:
    """The shipped kit ranked by how much farther from the Fischer target it is
    than the nearest real MARS recording of the same voice (`ours / MARS best`,
    both as worst-feature ratios to the same-unit / WEAK bar). DESCRIPTIVE: the
    MARS side is the most favourable take over all groups, an optimistic bound,
    and no bar was calibrated, so nothing here is a verdict."""
    rows = []
    for s, r in old_bar_context.items():
        o = r.get("ours")
        if not o:
            rows.append({"sound": s, "ours": None})
            continue
        m = r["mars_best"]["worst_ratio"]
        # a NaN would pass `if m` below and make the sort order undefined (#134)
        if not (math.isfinite(m) and math.isfinite(o["worst_ratio"])):
            raise ValueError(f"REFUSED: {s}: non-finite worst ratio (ours {o['worst_ratio']}, MARS best {m})")
        rows.append({"sound": s, "bar": r["old_bar"], "ours": o["worst_ratio"], "ours_feature": o["worst_feature"],
                     "mars_best": m, "mars_feature": r["mars_best"]["worst_feature"],
                     "n_takes": r["n_takes"], "mars_pass_old_bar": r["mars_takes_passing_old_bar"],
                     "ours_over_mars": (o["worst_ratio"] / m if m else math.inf)})
    return sorted(rows, key=lambda x: -(x.get("ours_over_mars") or -1))


def render_table(rows: list) -> str:
    out = ["| rank | sound | ours / MARS best | ours (worst, vs old bar) | MARS best (worst) | MARS takes | bar |",
           "|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        if not r["ours"]:
            out.append(f"| {i} | {r['sound']} | no render | | | | |")
            continue
        out.append(f"| {i} | {r['sound']} | **{r['ours_over_mars']:.1f}x** | {r['ours']:.1f} ({r['ours_feature']}) "
                   f"| {r['mars_best']:.1f} ({r['mars_feature']}) | {r['n_takes']} | {r['bar']} |")
    return "\n".join(out)


def main(argv=None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "table":
        res = json.loads(pathlib.Path(argv[1]).read_text())
        print(render_table(rank_rows(res["calibration"]["old_bar_context"])))
        return 0
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--refs", type=pathlib.Path, required=True)
    ap.add_argument("--tables", type=pathlib.Path, required=True)
    ap.add_argument("--corrupt-tables", type=pathlib.Path, default=None)
    ap.add_argument("--ours", type=pathlib.Path, default=None, help="dir of our rendered <SOUND>.wav")
    ap.add_argument("--fixtures", type=pathlib.Path, default=None,
                    help="dir with CY5025-candidate.wav (the rejected #374 candidate)")
    ap.add_argument("--force-p", type=float, default=None)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    a = ap.parse_args(argv)
    tabs = json.loads(a.tables.read_text())["tables"]
    cal = calibrate(tabs, a.force_p)
    ours = {}
    if a.ours:
        for s in pg.SOUNDS16:
            f = a.ours / f"{s}.wav"
            if f.exists():
                ours[s] = pg.load_wav(f)
    inj = None
    if a.corrupt_tables:
        ci = calibrate(json.loads(a.corrupt_tables.read_text())["tables"], a.force_p)
        qi = gc.qualify(ci, a.refs, ours) if ci.get("p") else None
        inj = (gc.decide(qi, False)["status"] == "QUALIFIED") if qi else None
        cal["injected_control"] = {"corrupt": json.loads(a.corrupt_tables.read_text())["corrupt"],
                                   "calibration_status": ci["status"], "qualified": inj}
    q = gc.qualify(cal, a.refs, ours) if cal.get("p") else None
    if q and a.fixtures and (a.fixtures / "CY5025-candidate.wav").exists() and "CY" in cal["sounds"]:
        T = pg.Target(*pg.load_wav(a.refs / pg.target_rel("CY")), "CY", pg.target_rel("CY"))
        q["known_bad"]["rejected-374-candidate"] = pg.verdict(
            T.distance(*pg.load_wav(a.fixtures / "CY5025-candidate.wav"), "374"), cal["sounds"]["CY"]["bar"])
    dec = gc.decide(q, inj) if q else {"status": "REFUSED", "why": cal["status"]}
    if q and cal.get("diagnostic"):
        dec["status"] = f"DIAGNOSTIC (not authority): {dec['status']}"
    cal["old_bar_context"] = gc.old_bar_context(gc.regroup(tabs), a.refs, ours)
    res = {"calibration": cal, "qualification": q, "decision": dec, "provenance": pg.provenance(a)}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(pg._r(res), indent=1) + "\n")
    print(json.dumps(dec, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
