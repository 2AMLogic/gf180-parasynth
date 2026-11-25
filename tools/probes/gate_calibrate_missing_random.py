"""#1317: can take loss independent of the measured distances reach CALIBRATED?

Table-only experiment on the committed 772-take record. Controls first (the
complete record must be REFUSED at 3/9; the issue's deliberately selected 226
development takes must be CALIBRATED k=3 at 9/9), then N seeded trials, each
moving exactly 226 of the 772 distinct paths, drawn uniformly without
replacement, from `rows` to `missing` (path preserved) regardless of sound,
group or distance. The unmodified default `calibrate()` classifies every trial.

Run:  python -I tools/probes/gate_calibrate_missing_random.py [--trials N] [--out FILE.json]
Trial i uses random.Random(i) over the sorted path list; trials and seeds are
fixed here (TRIALS), before any outcome was inspected. Anything other than
CALIBRATED / evidence refusal / coverage refusal invalidates the run (exit 2).
Replay one trial: --replay SEED.
"""
import argparse
import hashlib
import json
import pathlib
import random
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import gate_calibrate as gc  # noqa: E402

TABLES = ROOT / "docs/scorecard/gate-379/mars-calibration/tables.json"
TRIALS = 5000
MOVE = 226
KEEP = dict(BD=1, SD=1, LT=6, LC=1, MT=11, MC=1, HT=3, HC=3, CY=3)


def load():
    return gc.regroup(json.loads(TABLES.read_text())["tables"])


def move(tabs, removed):
    """Return a copy of tabs with the `removed` paths moved rows -> missing."""
    out = {}
    for s, t in tabs.items():
        gone = [r["rel"] for r in t["rows"] if r["rel"] in removed]
        out[s] = dict(t, rows=[r for r in t["rows"] if r["rel"] not in removed],
                      missing=list(t["missing"]) + [{"rel": p, "why": "injected unreadable"} for p in sorted(gone)])
    return out


def classify(res):
    st = res["status"]
    if st == "CALIBRATED":
        return "CALIBRATED"
    if "incomplete evidence" in st:
        return "EVIDENCE_REFUSED"
    if st.startswith("REFUSED: no k reaches"):
        return "COVERAGE_REFUSED"
    return "UNEXPECTED"


def cov(res, k=3):
    b = (res["selection"] or {}).get("by_k", {}).get(k)
    return None if b is None else (b["covered"], b["sounds"])


def draw(paths, seed):
    return set(random.Random(seed).sample(paths, MOVE))


def controls(tabs):
    clean = gc.calibrate(tabs)
    assert len(tabs) == 16 and sum(len(t["rows"]) for t in tabs.values()) == 772
    assert all(not t["missing"] for t in tabs.values())
    assert classify(clean) == "COVERAGE_REFUSED" and clean["k"] is None and cov(clean) == (3, 9), clean["status"]
    removed = set()
    for s, n in KEEP.items():
        dev = sorted((r for r in tabs[s]["rows"] if r["group"] == 0), key=lambda r: r["d"]["spec"])
        removed |= {r["rel"] for r in dev[:-n]}
    assert len(removed) == MOVE
    sel = move(tabs, removed)
    assert gc.evidence_refusal(sel) is None
    res = gc.calibrate(sel)
    assert (res["status"], res["k"], cov(res)) == ("CALIBRATED", 3, (9, 9)), res["status"]
    return "complete record REFUSED k=None 3/9; selected 226 -> CALIBRATED k=3 9/9"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=TRIALS)
    ap.add_argument("--out")
    ap.add_argument("--replay", type=int)
    a = ap.parse_args()
    tabs = load()
    paths = sorted(r["rel"] for t in tabs.values() for r in t["rows"])
    assert len(paths) == len(set(paths)) == 772
    print(controls(tabs))
    if a.replay is not None:
        rem = draw(paths, a.replay)
        res = gc.calibrate(move(tabs, rem))
        print(json.dumps({"seed": a.replay, "verdict": classify(res), "k": res["k"], "cov_k3": cov(res),
                          "removed_sha256": hashlib.sha256("\n".join(sorted(rem)).encode()).hexdigest()}))
        return 0
    counts, hits, bad = {}, [], 0
    for seed in range(a.trials):
        rem = draw(paths, seed)
        try:
            res = gc.calibrate(move(tabs, rem))
            v = classify(res)
        except Exception as e:  # an execution error invalidates the run
            print(f"seed {seed}: ERROR {type(e).__name__}: {e}")
            bad += 1
            continue
        counts[v] = counts.get(v, 0) + 1
        if v == "CALIBRATED":
            h = {"seed": seed, "k": res["k"], "cov_k3": cov(res), "cov_chosen_k": cov(res, res["k"]),
                 "removed_sha256": hashlib.sha256("\n".join(sorted(rem)).encode()).hexdigest()}
            if not hits:  # one exact, replayable path set is published in full
                h["removed"] = sorted(rem)
            hits.append(h)
        elif v == "UNEXPECTED":
            bad += 1
    n = a.trials
    hit = counts.get("CALIBRATED", 0)
    summary = {"trials": n, "move": MOVE, "counts": counts, "errors_or_unexpected": bad,
               "tables_sha256": hashlib.sha256(TABLES.read_bytes()).hexdigest(),
               "calibrated_hits": hits,
               "upper95_one_sided": (1 - 0.05 ** (1 / n)) if hit == 0 else None}
    print(json.dumps({k: v for k, v in summary.items() if k != "calibrated_hits"}, indent=1))
    print(f"hits: {[h['seed'] for h in hits]}")
    if a.out:
        pathlib.Path(a.out).write_text(json.dumps(summary, indent=1) + "\n")
    return 2 if bad or sum(counts.values()) != n else 0


if __name__ == "__main__":
    sys.exit(main())
