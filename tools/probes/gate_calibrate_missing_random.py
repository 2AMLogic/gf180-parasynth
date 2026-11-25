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
COVERAGE_REFUSAL = "REFUSED: no k reaches the development->calibration coverage requirement"
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


def check_partition(tabs, expected, removed):
    """Check the exact requested move against the index, including multiplicity."""
    if tabs.keys() != expected.keys() or not removed <= set().union(*expected.values()):
        raise ValueError("partition contains an unindexed path or the wrong sounds")
    for sound, indexed in expected.items():
        for field, wanted in (("rows", indexed - removed), ("missing", indexed & removed)):
            actual = [r["rel"] for r in tabs[sound][field]]
            if len(actual) != len(wanted) or set(actual) != wanted:
                raise ValueError(f"{sound}: {field} partition differs from the requested move")


def evaluate(tabs, expected, removed):
    if len(removed) != MOVE:
        raise ValueError(f"expected {MOVE} distinct removals, got {len(removed)}")
    altered = move(tabs, removed)
    check_partition(altered, expected, removed)
    res = gc.calibrate(altered)  # default path: the gate must still read its own index
    status = res["status"]
    if status == "CALIBRATED":
        return res, "CALIBRATED"
    if status == COVERAGE_REFUSAL:
        return res, "COVERAGE_REFUSED"
    if status.startswith("REFUSED: incomplete evidence for "):
        # Only an indexed, formerly eligible sound losing a whole group counts.
        # Confirm the actual refusal against the known-good index as well.
        for sound, table in tabs.items():
            before = {r["group"] for r in table["rows"]}
            after = {r["group"] for r in altered[sound]["rows"]}
            if {0, 1} <= before and not {0, 1} <= after:
                if gc.evidence_refusal(altered, expected) == status:
                    return res, "EVIDENCE_REFUSED"
                break
    raise ValueError(f"unexpected calibration result: {status}")


def cov(res, k=3):
    b = (res["selection"] or {}).get("by_k", {}).get(k)
    return None if b is None else (b["covered"], b["sounds"])


def draw(paths, seed):
    return set(random.Random(seed).sample(paths, MOVE))


def controls(tabs, expected=None):
    if expected is None:
        expected = gc.expected_takes()
    check_partition(tabs, expected, set())
    if (len(tabs) != 16 or sum(map(len, expected.values())) != 772
            or len(set().union(*expected.values())) != 772):
        raise ValueError("complete control requires 16 sounds and 772 distinct readable paths")
    clean = gc.calibrate(tabs)
    if (clean["status"], clean["k"], cov(clean)) != (COVERAGE_REFUSAL, None, (3, 9)):
        raise ValueError(f"complete control expected a coverage refusal at 3/9, got {clean['status']}")
    removed = set()
    for s, n in KEEP.items():
        dev = sorted((r for r in tabs[s]["rows"] if r["group"] == 0), key=lambda r: r["d"]["spec"])
        removed |= {r["rel"] for r in dev[:-n]}
    res, verdict = evaluate(tabs, expected, removed)
    if (verdict, res["k"], cov(res)) != ("CALIBRATED", 3, (9, 9)):
        raise ValueError(f"selected control expected CALIBRATED k=3 at 9/9, got {res['status']}")
    return "complete record REFUSED k=None 3/9; selected 226 -> CALIBRATED k=3 9/9"


def experiment(args):
    tabs = load()
    expected = gc.expected_takes()
    print(controls(tabs, expected))
    paths = sorted(set().union(*expected.values()))
    counts, hits = {}, []
    seeds = [args.replay] if args.replay is not None else range(args.trials)
    for seed in seeds:
        try:
            removed = draw(paths, seed)
            res, verdict = evaluate(tabs, expected, removed)
        except Exception as exc:
            raise ValueError(f"seed {seed}: {exc}") from exc
        counts[verdict] = counts.get(verdict, 0) + 1
        if args.replay is not None or verdict == "CALIBRATED":
            ordered = sorted(removed)
            entry = {"seed": seed, "k": res["k"], "cov_k3": cov(res), "cov_chosen_k": cov(res, res["k"]),
                     "removed_sha256": hashlib.sha256("\n".join(ordered).encode()).hexdigest()}
            if args.replay is not None:
                print(json.dumps(dict(entry, verdict=verdict)))
                return
            if not hits:
                entry["removed"] = ordered
            hits.append(entry)

    # Every attempted trial returned a recognised verdict before we get here.
    summary = {"trials": args.trials, "move": MOVE, "counts": counts, "errors_or_unexpected": 0,
               "tables_sha256": hashlib.sha256(TABLES.read_bytes()).hexdigest(),
               "calibrated_hits": hits,
               "upper95_one_sided": (1 - 0.05 ** (1 / args.trials)) if not hits else None}
    print(json.dumps({k: v for k, v in summary.items() if k != "calibrated_hits"}, indent=1))
    print(f"hits: {[h['seed'] for h in hits]}")
    if args.out:
        pathlib.Path(args.out).write_text(json.dumps(summary, indent=1) + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=TRIALS)
    ap.add_argument("--out")
    ap.add_argument("--replay", type=int)
    args = ap.parse_args(argv)
    if args.trials < 1:
        ap.error("--trials must be positive")
    try:
        experiment(args)
    except Exception as exc:
        print(f"Invalid experiment: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
