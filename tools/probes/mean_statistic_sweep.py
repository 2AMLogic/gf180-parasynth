#!/usr/bin/env python3
"""How reliably does a DISTRIBUTION statistic catch the two noise controls?
#539, as committed code.

    python3 tools/probes/mean_statistic_sweep.py
    python3 tools/probes/mean_statistic_sweep.py --json docs/mean-statistic-sweep-results.json \
        --table docs/sensitivity/mean-trials-sweep.txt

#539: `SINGLE_WINDOW_SLOPE` on `noise/psd_slope` and `SHORT_WINDOW_SPECTRUM` on
`noise/centroid` were detected on the WORST of 12 trials against 4 x the worst
of 96. #528 measured the slope control CAUGHT in only 23 of 40 / 7 of 10
untouched 12-trial groups. The fixture length cannot fix it (the mutant reads a
prefix), so this asks whether the MEAN residual over N trials, against
`MEAN_FACTOR` x the clean row's calibrated mean, separates the defect from the
clean row's own spread.

WHAT IS STATED BEFORE THE RUN, in this file and therefore in its commit
----------------------------------------------------------------------
  * The grid: `TRIALS_GRID` = 12, 24, 48, 96 trials per statistic. All four are
    prefixes of one 96-trial run (trial i is seeded `[base, i, case]`, so a
    longer run extends a shorter one), asserted below as a precondition.
  * The factor is held at `pd.MEAN_FACTOR` while trials are swept, and swept
    over `FACTOR_GRID` at the chosen trial count.
  * The prediction, from the central limit theorem and NOT from this sweep: a
    mean of N independent residuals has spread sigma/sqrt(N), so the separation
    between the clean mean (mu_c) and the defect mean (mu_d), in clean-mean
    standard errors, grows as sqrt(N). `predict_separation()` computes it from
    the single-trial moments on CALIBRATE_BASE -- a stream no group below reads
    -- and `predicted_state()` says which N should already show zero misses.
  * The selection rule, `select()`, reading SELECT_BASE groups only: the
    SMALLEST trial count such that, for BOTH pairs at `pd.MEAN_FACTOR`, there
    are zero clean false-reds and the one-sided 95 % Clopper-Pearson LOWER bound
    on the detection rate is at least `DETECT_FLOOR` (0.95, the issue's bar).
    If none qualifies the result is "cannot be detected reliably" and the
    controls must say so (the issue's option 3).
  * The shipped configuration is then CONFIRMED on CONFIRM_BASE groups the
    selection never read, and again on one DIFFERENT seed base (`ALT_BASE`,
    FPR_BASE: a stream no injection has ever read). Both must hold.

Exit 0: a configuration was selected and both confirmations held. 1: none
qualified, or a confirmation contradicts the selection. 2: REFUSED -- a
precondition failed and nothing here is a result.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import pathlib
import subprocess
import sys
import time

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import permitted_differences as pd                                    # noqa: E402

PAIRS = pd.MEAN_PAIRS
TRIALS_GRID = (12, 24, 48, 96)
FACTOR_GRID = (1.5, 2, 3, 4, 5, 6)
MAX_TRIALS = max(TRIALS_GRID)
SELECT_GROUPS = 80
CONFIRM_GROUPS = 80
ALT_GROUPS = 80
GROUP_STRIDE = 1000
ALT_BASE = pd.FPR_BASE
DETECT_FLOOR = 0.95
assert MAX_TRIALS <= GROUP_STRIDE
assert max(SELECT_GROUPS, CONFIRM_GROUPS, ALT_GROUPS) * GROUP_STRIDE <= pd.SEED_SPAN


class Refused(Exception):
    """A precondition failed. Never reported as a result."""


def cp_lower_one_sided(k: int, n: int, alpha: float = 0.05) -> float:
    """One-sided Clopper-Pearson LOWER bound on a binomial rate."""
    from scipy.stats import beta
    return 0.0 if k == 0 else float(beta.ppf(alpha, k, n - k + 1))


# ---------------------------------------------------------------------------
# one group's residuals -- the unit of work
# ---------------------------------------------------------------------------
def group_residuals(task: tuple[str, str | None, int]) -> tuple[tuple, list[float]]:
    cid, injection, base = task
    r = pd.run_case(pd.CASE_BY_ID[cid], MAX_TRIALS, base, injection)
    if r.verdict == pd.REFUSED or r.refusals:
        raise Refused(f"{cid} {injection} base {base}: {r.refusals} refusals")
    return task, [float(v) for v in r.residuals]


def collect(bases: list[int], workers: int) -> dict:
    tasks = [(cid, i, b) for b in bases for inj, cid in PAIRS for i in (inj, None)]
    out = {}
    with cf.ProcessPoolExecutor(max_workers=workers) as ex:
        for task, res in ex.map(group_residuals, tasks, chunksize=4):
            out[task] = res
    return out


def stat(res: list[float], trials: int) -> float:
    return float(np.mean(res[:trials]))


# ---------------------------------------------------------------------------
# preconditions
# ---------------------------------------------------------------------------
def check_prefix_and_shipped(cal: dict) -> None:
    """The statistic this file tabulates must BE the one that ships. Prefix
    means of the 96-trial run must equal `pd.mean_margin`'s residual at each
    trial count (it re-runs the row from scratch), or the sweep is measuring
    something `make controls` does not run."""
    for inj, cid in PAIRS:
        base = pd.SELECT_BASE
        _, res = group_residuals((cid, inj, base))
        for t in TRIALS_GRID:
            pm = pd.mean_margin(inj, cid, base, trials=t, cal_mean=cal[cid])
            if pm.residual is None or abs(pm.residual - stat(res, t)) > 1e-12:
                raise Refused(f"{inj}: prefix mean of {t} trials {stat(res, t)!r} != "
                              f"shipped statistic {pm.residual!r}")


def predict_separation(cid: str, injection: str) -> dict:
    """CLT prediction from single-trial moments on CALIBRATE_BASE only."""
    c = pd.run_case(pd.CASE_BY_ID[cid], pd.CAL_TRIALS, pd.CALIBRATE_BASE, None).residuals
    d = pd.run_case(pd.CASE_BY_ID[cid], pd.CAL_TRIALS, pd.CALIBRATE_BASE, injection).residuals
    mc, sc, md, sd = np.mean(c), np.std(c, ddof=1), np.mean(d), np.std(d, ddof=1)
    out = {}
    for t in TRIALS_GRID:
        thr = pd.MEAN_FACTOR * mc
        out[t] = dict(clean_z=float((thr - mc) / (sc / np.sqrt(t))),
                      defect_z=float((md - thr) / (sd / np.sqrt(t))))
    return dict(clean_mean=float(mc), defect_mean=float(md), by_trials=out)


def predicted_state(pred: dict, groups: int) -> dict:
    """Which trial counts the CLT says should show ZERO misses over `groups`
    groups (expected misses < 0.5) for every pair. A prediction, stated before
    the SELECT_BASE groups are read; `main` prints it beside the measurement."""
    from scipy.stats import norm
    out = {}
    for t in TRIALS_GRID:
        exp = {inj: float(groups * norm.cdf(-p["by_trials"][t]["defect_z"]))
               for inj, p in pred.items()}
        out[t] = dict(expected_misses=exp, zero_misses=all(v < 0.5 for v in exp.values()))
    return out


# ---------------------------------------------------------------------------
# tabulation and decision
# ---------------------------------------------------------------------------
def tabulate(data: dict, bases: list[int], cal: dict, trials: int, factor: float) -> dict:
    out = {}
    for inj, cid in PAIRS:
        thr = factor * cal[cid]
        miss = sum(1 for b in bases if not stat(data[(cid, inj, b)], trials) > thr)
        fred = sum(1 for b in bases if stat(data[(cid, None, b)], trials) > thr)
        out[inj] = dict(case=cid, groups=len(bases), missed=miss, false_red=fred,
                        threshold=thr)
    return out


def select(rows: dict) -> tuple[int | None, list[str]]:
    why = []
    for t in TRIALS_GRID:
        ok = True
        for inj, r in rows[t].items():
            n, caught = r["groups"], r["groups"] - r["missed"]
            lo = cp_lower_one_sided(caught, n)
            if r["false_red"] or lo < DETECT_FLOOR:
                ok = False
                why.append(f"{t} trials: {inj} missed {r['missed']}/{n} "
                           f"(lower95 {lo:.3f}), false-red {r['false_red']}/{n}")
        if ok:
            return t, why + [f"{t} trials qualifies"]
    return None, why + ["no trial count in the grid qualifies"]


def _git(*a: str) -> str:
    try:
        return subprocess.run(["git", *a], cwd=str(pd.ROOT), capture_output=True,
                              text=True, timeout=60).stdout.strip()
    except Exception as e:                                          # pragma: no cover
        return f"<unavailable: {e}>"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", type=pathlib.Path)
    ap.add_argument("--table", type=pathlib.Path,
                    help="write the fixed-width artefact docs/sensitivity reads")
    ap.add_argument("--workers", type=int, default=2, help="at most 2 on a shared host")
    a = ap.parse_args(argv)
    if a.workers > 2:
        print("REFUSED: --workers above 2 on a shared host", file=sys.stderr)
        return 2
    t0 = time.time()
    try:
        cal = {cid: pd.calibration_mean(cid) for _, cid in PAIRS}
        check_prefix_and_shipped(cal)
        sel_bases = [pd.SELECT_BASE + g * GROUP_STRIDE for g in range(SELECT_GROUPS)]
        conf_bases = [pd.CONFIRM_BASE + g * GROUP_STRIDE for g in range(CONFIRM_GROUPS)]
        alt_bases = [ALT_BASE + g * GROUP_STRIDE for g in range(ALT_GROUPS)]
        pred = {inj: predict_separation(cid, inj) for inj, cid in PAIRS}
        sel_data = collect(sel_bases, a.workers)
        rows = {t: tabulate(sel_data, sel_bases, cal, t, pd.MEAN_FACTOR) for t in TRIALS_GRID}
        chosen, why = select(rows)
        report = dict(
            commit=_git("rev-parse", "HEAD"), utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            behind_origin_main=_git("rev-list", "--count", "HEAD..origin/main"),
            shipped=dict(MEAN_TRIALS=pd.MEAN_TRIALS, MEAN_FACTOR=pd.MEAN_FACTOR),
            calibration_mean=cal, prediction=pred, select_groups=SELECT_GROUPS,
            predicted=predicted_state(pred, SELECT_GROUPS),
            by_trials={str(t): rows[t] for t in TRIALS_GRID}, selected=chosen, why=why)
        if chosen is not None:
            report["by_factor"] = {str(f): tabulate(sel_data, sel_bases, cal, chosen, f)
                                   for f in FACTOR_GRID}
            for label, bases in (("confirm", conf_bases), ("alt_base", alt_bases)):
                d = collect(bases, a.workers)
                report[label] = tabulate(d, bases, cal, chosen, pd.MEAN_FACTOR)
    except Refused as e:
        print(f"REFUSED: {e}")
        return 2
    report["wall_s"] = time.time() - t0

    print(f"SELECT_BASE x {SELECT_GROUPS} groups, MEAN_FACTOR {pd.MEAN_FACTOR}")
    print(f"{'trials':>7} {'pair':<24} {'missed':>7} {'false_red':>10}")
    for t in TRIALS_GRID:
        for inj, r in rows[t].items():
            print(f"{t:>7} {inj:<24} {r['missed']:>4}/{r['groups']:<3} {r['false_red']:>7}/{r['groups']}")
    print("\n".join(why))
    for t, pr in report["predicted"].items():
        print(f"CLT predicts {t} trials: expected misses "
              + ", ".join(f"{k} {v:.2f}" for k, v in pr["expected_misses"].items())
              + f" -> zero misses {pr['zero_misses']}")
    bad = chosen is None
    if chosen is not None:
        print(f"\nfactor sweep at {chosen} trials (select groups)")
        for f, rr in report["by_factor"].items():
            print(f"  x{f}: " + "  ".join(f"{i}: miss {r['missed']} fred {r['false_red']}"
                                          for i, r in rr.items()))
        for label in ("confirm", "alt_base"):
            for inj, r in report[label].items():
                print(f"{label}: {inj} missed {r['missed']}/{r['groups']} "
                      f"false-red {r['false_red']}/{r['groups']}")
                bad |= bool(r["missed"] or r["false_red"])
        if chosen != pd.MEAN_TRIALS:
            print(f"NOTE: selected {chosen} trials but MEAN_TRIALS ships {pd.MEAN_TRIALS}")
            bad = True
    if a.json:
        a.json.parent.mkdir(parents=True, exist_ok=True)
        a.json.write_text(json.dumps(report, indent=2))
    if a.table:
        a.table.parent.mkdir(parents=True, exist_ok=True)
        a.table.write_text(render_table(report, rows))
    return 1 if bad else 0


def render_table(report: dict, rows: dict) -> str:
    """The fixed-width artefact `tools/sensitivity.py` re-extracts its points
    from. The objective is the WORST pair's one-sided 95 % upper bound on the
    miss rate, in percent: never zero, so a relative-tolerance rule can read it."""
    from scipy.stats import beta
    lines = [f"Issue #539 MEAN_TRIALS sweep -- written by `python3 tools/probes/"
             f"mean_statistic_sweep.py --table`.",
             f"provenance: commit {report['commit'][:12]}  {report['utc']}  "
             f"MEAN_FACTOR {pd.MEAN_FACTOR}  {report['select_groups']} SELECT_BASE groups",
             "", "mean_trials -- worst-pair miss upper bound against trials per mean statistic",
             "MEAN_TRIALS miss_slope miss_centroid false_red miss_upper95_pct"]
    for t in TRIALS_GRID:
        (_, a), (_, b) = rows[t].items()
        n = a["groups"]
        ks = max(a["missed"], b["missed"])
        up = 100.0 * (1.0 if ks == n else float(beta.ppf(0.95, ks + 1, n - ks)))
        lines.append(f"{t} {a['missed']} {b['missed']} {a['false_red'] + b['false_red']} {up:.2f}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
