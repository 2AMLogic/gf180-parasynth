#!/usr/bin/env python3
"""Does lengthening the `noise` fixture widen the ORIGINAL slope control's
margin? The bounded 2 s vs 4 s experiment #528 asks for, as committed code.

    python3 tools/probes/noise_fixture_duration.py
    python3 tools/probes/noise_fixture_duration.py --json docs/noise-fixture-duration-results.json

#528: `SINGLE_WINDOW_SLOPE` -- `psd_slope_db_oct(x[:4096], nfft=1024)` -- clears
`noise/psd_slope`'s 0.60 dB/oct threshold by 1.05x. The issue's first option is
a longer fixture, so the calibrated threshold tightens while the mutant stays
the mutant. The Curator's acceptance criteria (binding): measure baseline and
candidate; ship only a candidate that improves the ORIGINAL control's margin
beyond baseline with clean-suite passage intact; otherwise report the negative
result. Calibration and confirmation populations are disjoint, and the
selected configuration is confirmed on seeds nothing here looked at while
choosing.

WHAT IS STATED BEFORE THE RUN, in this file and therefore in its commit
----------------------------------------------------------------------
  * The grid: `GRID` = 2.0 s (shipped) and 4.0 s (#528 option 1). Bounded on
    purpose -- the issue asks for this comparison, and 8 s is 4x the noise rows'
    runtime for a dial the prediction below says moves as a square root.
  * The prediction, from Welch theory and NOT from any measurement here:
    `psd_slope_db_oct` averages Hann segments of nfft = 8192 at 50 % overlap,
    22 of them at 2 s and 45 at 4 s. A slope is a linear fit to band-averaged
    dB, so its realisation spread scales as 1/sqrt(segments): 4 s should read
    sqrt(22/45) = 0.70x the 2 s spread. NOT the halving #528 hoped for -- that
    is the 1/N of a variance read as the 1/sqrt(N) of a spread. `PREDICTED_RATIO`.
  * A DERIVED INVARIANCE that is asserted as a precondition: the mutant reads
    only `x[:4096]`, and `_noise(seed, n)` is a prefix-stable draw, so the
    mutant's residuals must be IDENTICAL at every duration (up to the record's
    scale, which a dB/oct slope does not see). If they are not, the experiment
    is not measuring what it says and REFUSES. So the only thing the dial can
    move is the threshold.
  * The decision rule, `decide()`, reading the SELECTION data only:
      1. the candidate's original-control margin on VALIDATE_BASE beats the
         baseline's, and the control is CAUGHT there;
      2. the candidate's clean noise rows PASS on VALIDATE_BASE;
      3. on the SELECT_BASE groups the candidate has zero false alarms, zero
         refusals, and no control REFUSED / NO-VERDICT;
      4. the improvement is MATERIAL -- resolvable by the measurement rather
         than a nominal ratio: either the original control's detection rate
         over the select groups improves with non-overlapping 95 %
         Clopper-Pearson intervals, or the candidate survives EVERY
         independent recalibration (`RECAL_POPS` fresh 96-draw populations,
         each re-deriving the threshold) where the baseline does not.
    Rule 4 is the one a reader may disagree with, so the literal-criterion
    comparison (margin_cand > margin_base) is printed beside it. Its reason:
    #528's hazard is "a recalibration on a different machine's draws pushed
    the threshold past 0.6288", and a ratio of two worst-of-N statistics on one
    population cannot say whether that hazard has moved.

Exit 0: the experiment ran and the configuration it leaves shipped (selected or
baseline) held on the confirmation population. 1: the confirmation population
contradicts it -- a clean false alarm, a refusal, or a detection pair not
CAUGHT. 2: REFUSED -- a precondition failed and nothing here is a result.
"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import json
import pathlib
import platform
import subprocess
import sys
import time

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import permitted_differences as pd                                    # noqa: E402

GRID = (2.0, 4.0)
BASELINE = 2.0
PREDICTED_RATIO = float(np.sqrt(22 / 45))    # 4 s spread / 2 s spread, Welch theory

NOISE_ROWS = ("noise/psd_slope", "noise/centroid", "noise/waveform_max_db")
CONTROL = ("SINGLE_WINDOW_SLOPE", "noise/psd_slope")      # the original control
NEIGHBOUR = ("SHORT_WINDOW_SPECTRUM", "noise/centroid")   # same fixture, reported

SELECT_GROUPS = 40      # 12-trial groups at SELECT_BASE + g * 1000
RECAL_OFFSET = 50_000   # recalibration populations at SELECT_BASE + 50_000 + k * 1000
RECAL_POPS = 8
CONFIRM_GROUPS = 10     # 12-trial groups at CONFIRM_BASE + g * 1000
GROUP_STRIDE = 1000
assert SELECT_GROUPS * GROUP_STRIDE <= RECAL_OFFSET
assert RECAL_OFFSET + RECAL_POPS * GROUP_STRIDE <= pd.SEED_SPAN
assert CONFIRM_GROUPS * GROUP_STRIDE <= pd.SEED_SPAN
INVARIANCE_TOL = 1e-9   # dB/oct; the mutant's residual must not see the dial


class Refused(Exception):
    """A precondition failed. Never reported as a result."""


# ---------------------------------------------------------------------------
# the dial
# ---------------------------------------------------------------------------
@contextlib.contextmanager
def noise_seconds(seconds: float):
    """Set `pd.NOISE_SECONDS` and PROVE it took: a hook that silently left the
    fixture at 2.0 s would report two identical candidates as a flat dial."""
    old = pd.NOISE_SECONDS
    pd.NOISE_SECONDS = seconds
    try:
        p = pd.draw_noise(np.random.default_rng(0))
        n = len(pd.build(p).x)
        if n != int(round(seconds * pd.SR)):
            raise Refused(f"asked for a {seconds:g} s noise fixture, built "
                          f"{n / pd.SR:g} s -- the dial did not take")
        yield
    finally:
        pd.NOISE_SECONDS = old


def cp_interval(k: int, n: int, alpha: float = 0.05) -> tuple[float, float]:
    """Two-sided Clopper-Pearson interval on a binomial rate."""
    from scipy.stats import beta
    lo = 0.0 if k == 0 else float(beta.ppf(alpha / 2, k, n - k + 1))
    hi = 1.0 if k == n else float(beta.ppf(1 - alpha / 2, k + 1, n - k))
    return lo, hi


# ---------------------------------------------------------------------------
# one candidate
# ---------------------------------------------------------------------------
def _with_threshold(c: pd.Case, thr: float, cal: float) -> pd.Case:
    return dataclasses.replace(c, policy=dataclasses.replace(c.policy, threshold=thr,
                                                             calibrated=cal))


def measure(seconds: float, trials: int = pd.TRIALS_DEFAULT,
            cal_trials: int = pd.CAL_TRIALS, select_groups: int = SELECT_GROUPS,
            recal_pops: int = RECAL_POPS, log=print) -> dict:
    """Everything the decision reads, for one duration. Reads CALIBRATE_BASE,
    VALIDATE_BASE and SELECT_BASE only -- never CONFIRM_BASE."""
    out: dict = dict(seconds=seconds, rows={})
    with noise_seconds(seconds):
        cases = {}
        t0 = time.time()
        for cid in NOISE_ROWS:
            c = pd.CASE_BY_ID[cid]
            ts = time.time()
            r = pd.run_case(c, cal_trials, pd.CALIBRATE_BASE, None)
            if r.refusals:
                raise Refused(f"{seconds:g} s: {cid} refused {r.refusals}/{cal_trials} "
                              f"calibration draws: {sorted(set(r.reasons))[:2]}")
            row = dict(cal_worst=r.worst, cal_p95=r.stat(95.0),
                       cal_std=float(np.std(r.residuals)),
                       ms_per_trial=(time.time() - ts) / cal_trials * 1e3)
            if c.policy.tightness == "calibrated":
                thr = pd._signif(r.worst * pd.SAFETY)
                cases[cid] = _with_threshold(c, thr, r.worst)
            else:
                thr = c.policy.threshold          # a floor; reported, not moved
                cases[cid] = c
                row["floor_room"] = (r.worst - thr if c.policy.kind == "moves_above"
                                     else thr - r.worst)
            row["threshold"] = thr
            out["rows"][cid] = row
        out["calibrate_s"] = time.time() - t0

        # clean passage and the two controls on the established population
        for cid in NOISE_ROWS:
            r = pd.run_case(cases[cid], trials, pd.VALIDATE_BASE, None)
            out["rows"][cid].update(validate_verdict=r.verdict,
                                    validate_refusals=r.refusals)
        for name, cid in (CONTROL, NEIGHBOUR):
            r = pd.run_case(cases[cid], trials, pd.VALIDATE_BASE, name)
            pm = pd.pair_margin(name, r)
            out[name] = dict(case=cid, state=pm.state, why=pm.why,
                             residual=pm.residual, margin=pm.margin,
                             threshold=pm.threshold,
                             residuals=[float(v) for v in r.residuals])

        # recalibration: does the control survive the threshold being
        # re-derived on an independent 96-draw population?
        recal = []
        for k in range(recal_pops):
            base = pd.SELECT_BASE + RECAL_OFFSET + k * GROUP_STRIDE
            rr = pd.run_case(pd.CASE_BY_ID[CONTROL[1]], cal_trials, base, None)
            if rr.refusals:
                raise Refused(f"{seconds:g} s: recalibration population {k} refused "
                              f"{rr.refusals} draws")
            thr_k = pd._signif(rr.worst * pd.SAFETY)
            recal.append(dict(pop=k, worst=rr.worst, threshold=thr_k,
                              survives=out[CONTROL[0]]["residual"] is not None
                              and out[CONTROL[0]]["residual"] > thr_k))
        out["recal"] = recal

        # selection groups: per-group verdicts at the candidate's thresholds
        sel = dict(control={}, neighbour={}, clean={cid: {} for cid in NOISE_ROWS})
        for g in range(select_groups):
            base = pd.SELECT_BASE + g * GROUP_STRIDE
            for key, (name, cid) in (("control", CONTROL), ("neighbour", NEIGHBOUR)):
                st = pd.pair_margin(name, pd.run_case(cases[cid], trials, base, name)).state
                sel[key][st] = sel[key].get(st, 0) + 1
            for cid in NOISE_ROWS:
                v = pd.run_case(cases[cid], trials, base, None).verdict
                sel["clean"][cid][v] = sel["clean"][cid].get(v, 0) + 1
        out["select"] = sel
        out["select_groups"] = select_groups
    log(f"   measured {seconds:g} s")
    return out


# ---------------------------------------------------------------------------
# the decision, stated before the run
# ---------------------------------------------------------------------------
def decide(base: dict, cand: dict) -> tuple[bool, list[str]]:
    """Ship `cand` over `base`? Reads selection data only. Every failed
    condition is listed, so a negative result says which one was binding."""
    why, ok = [], True
    bc, cc = base[CONTROL[0]], cand[CONTROL[0]]
    bm, cm = bc.get("margin"), cc.get("margin")
    if cc.get("state") != pd.CAUGHT or cm is None or not np.isfinite(cm):
        ok = False
        why.append(f"1. candidate control is {cc.get('state')} on VALIDATE_BASE, not CAUGHT")
    elif bm is None or not np.isfinite(bm) or not cm > bm:
        ok = False
        why.append(f"1. candidate margin {cm:.3g}x does not beat baseline {bm}")
    bad = [cid for cid, r in cand["rows"].items()
           if r.get("validate_verdict") != pd.PASS]
    if bad:
        ok = False
        why.append(f"2. clean rows not PASS on VALIDATE_BASE: {bad}")
    sel = cand["select"]
    fa = {cid: v.get(pd.FAIL, 0) for cid, v in sel["clean"].items() if v.get(pd.FAIL)}
    rf = {cid: v.get(pd.REFUSED, 0) for cid, v in sel["clean"].items() if v.get(pd.REFUSED)}
    ctl_bad = {s: n for s, n in sel["control"].items() if s in (pd.REFUSED, pd.NO_VERDICT)}
    if fa or rf or ctl_bad:
        ok = False
        why.append(f"3. selection groups: false alarms {fa}, refusals {rf}, "
                   f"control REFUSED/NO-VERDICT {ctl_bad}")
    n = cand["select_groups"]
    if n != base["select_groups"]:
        raise Refused("baseline and candidate were measured on different group counts")
    kb = base["select"]["control"].get(pd.CAUGHT, 0)
    kc = sel["control"].get(pd.CAUGHT, 0)
    det = cp_interval(kc, n)[0] > cp_interval(kb, n)[1]
    rb = sum(r["survives"] for r in base["recal"])
    rc = sum(r["survives"] for r in cand["recal"])
    rec = rc == len(cand["recal"]) and rb < len(base["recal"])
    if not (det or rec):
        ok = False
        why.append(f"4. not material: control caught in {kc}/{n} selection groups "
                   f"vs {kb}/{n} (95 % CP intervals overlap), survives {rc}/"
                   f"{len(cand['recal'])} recalibrations vs {rb}/{len(base['recal'])}")
    if ok:
        why.append("all four conditions hold")
    return ok, why


# ---------------------------------------------------------------------------
# confirmation, on seeds the decision never read
# ---------------------------------------------------------------------------
def confirm(seconds: float, measured: dict, trials: int = pd.TRIALS_DEFAULT,
            groups: int = CONFIRM_GROUPS, full_suite: bool = True) -> dict:
    """Per-row PASS / FAIL / REFUSED counts and per-pair CAUGHT / MISSED /
    REFUSED / NO-VERDICT counts over `groups` 12-trial groups on CONFIRM_BASE,
    at the thresholds `measured` derived. `full_suite` runs all eighteen rows;
    otherwise the three noise rows only."""
    with noise_seconds(seconds):
        cases = []
        for c in pd.CASES:
            if c.cid in measured["rows"] and c.policy.tightness == "calibrated":
                r = measured["rows"][c.cid]
                c = _with_threshold(c, r["threshold"], r["cal_worst"])
            cases.append(c)
        if not full_suite:
            cases = [c for c in cases if c.cid in NOISE_ROWS]
        by_id = {c.cid: c for c in cases}
        rows = {c.cid: {} for c in cases}
        pairs = {f"{n}|{cid}": {} for n, cid in pd.DETECTION_PAIRS if cid in by_id}
        t0 = time.time()
        for g in range(groups):
            base = pd.CONFIRM_BASE + g * GROUP_STRIDE
            for c in cases:
                v = pd.run_case(c, trials, base, None).verdict
                rows[c.cid][v] = rows[c.cid].get(v, 0) + 1
            for n, cid in pd.DETECTION_PAIRS:
                if cid not in by_id:
                    continue
                st = pd.pair_margin(n, pd.run_case(by_id[cid], trials, base, n)).state
                pairs[f"{n}|{cid}"][st] = pairs[f"{n}|{cid}"].get(st, 0) + 1
    return dict(seconds=seconds, groups=groups, rows=rows, pairs=pairs,
                wall_s=time.time() - t0)


def confirmation_holds(conf: dict) -> list[str]:
    bad = []
    for cid, v in conf["rows"].items():
        for s in (pd.FAIL, pd.REFUSED):
            if v.get(s):
                bad.append(f"{cid}: {s} in {v[s]}/{conf['groups']} groups")
    for key, v in conf["pairs"].items():
        for s, n in v.items():
            if s != pd.CAUGHT:
                bad.append(f"{key}: {s} in {n}/{conf['groups']} groups")
    return bad


# ---------------------------------------------------------------------------
# provenance and report
# ---------------------------------------------------------------------------
def _git(*a: str) -> str:
    try:
        return subprocess.run(["git", *a], cwd=str(pd.ROOT), capture_output=True,
                              text=True, timeout=60).stdout.strip()
    except Exception as e:                                          # pragma: no cover
        return f"<unavailable: {e}>"


def provenance(argv) -> dict:
    import scipy
    return dict(commit=_git("rev-parse", "HEAD"),
                dirty=bool(_git("status", "--porcelain", "--", "tools/probes", "model")),
                behind_origin_main=_git("rev-list", "--count", "HEAD..origin/main"),
                command=" ".join(["tools/probes/noise_fixture_duration.py", *argv]),
                utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                python=sys.version.split()[0], numpy=np.__version__,
                scipy=scipy.__version__, machine=platform.machine(),
                system=platform.system(), cpus=__import__("os").cpu_count(),
                load=list(__import__("os").getloadavg()))


def check_invariance(results: list[dict]) -> float:
    """The derived precondition: the mutant's residuals must not see the dial."""
    ref = np.array(results[0][CONTROL[0]]["residuals"])
    worst = 0.0
    for r in results[1:]:
        x = np.array(r[CONTROL[0]]["residuals"])
        if x.shape != ref.shape:
            raise Refused("the control produced different trial counts across durations")
        worst = max(worst, float(np.max(np.abs(x - ref))))
    if worst > INVARIANCE_TOL:
        raise Refused(f"the original mutant's residuals moved by {worst:g} dB/oct "
                      f"with the fixture length; it reads x[:4096] and must not -- "
                      f"this experiment is not measuring what it says")
    return worst


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--select-groups", type=int, default=SELECT_GROUPS)
    ap.add_argument("--recal-pops", type=int, default=RECAL_POPS)
    ap.add_argument("--confirm-groups", type=int, default=CONFIRM_GROUPS)
    ap.add_argument("--json", type=pathlib.Path)
    a = ap.parse_args(argv)
    argv = list(argv if argv is not None else sys.argv[1:])
    prov = provenance(argv)
    t_all = time.time()
    print("=" * 100)
    print(f"NOISE FIXTURE DURATION -- grid {GRID}, baseline {BASELINE:g} s; "
          f"commit {prov['commit'][:10]}{' DIRTY' if prov['dirty'] else ''}, "
          f"{prov['behind_origin_main']} behind origin/main")
    print("=" * 100)
    try:
        res = [measure(s, select_groups=a.select_groups, recal_pops=a.recal_pops)
               for s in GRID]
        inv = check_invariance(res)
    except Refused as e:
        print(f"\nREFUSED: {e}")
        return 2
    base = next(r for r in res if r["seconds"] == BASELINE)

    print(f"\n{'seconds':>8}{'row':>22}{'cal worst':>11}{'cal std':>10}{'thresh':>9}"
          f"{'validate':>10}{'ms/trial':>10}")
    for r in res:
        for cid, v in r["rows"].items():
            print(f"{r['seconds']:>8g}{cid:>22}{v['cal_worst']:>11.4g}{v['cal_std']:>10.4g}"
                  f"{v['threshold']:>9.4g}{v['validate_verdict']:>10}{v['ms_per_trial']:>10.1f}")
    print(f"\n   spread ratio {GRID[1]:g} s / {GRID[0]:g} s on noise/psd_slope: "
          f"worst {res[1]['rows']['noise/psd_slope']['cal_worst'] / res[0]['rows']['noise/psd_slope']['cal_worst']:.3f}, "
          f"std {res[1]['rows']['noise/psd_slope']['cal_std'] / res[0]['rows']['noise/psd_slope']['cal_std']:.3f}"
          f"  (predicted {PREDICTED_RATIO:.3f} from Welch theory)")
    print(f"   mutant invariance across durations: max |delta residual| {inv:.2g} dB/oct "
          f"(tolerance {INVARIANCE_TOL:g})")

    print(f"\n{'seconds':>8}  {'control':<44}{'neighbour':<40}")
    for r in res:
        cells = []
        for name in (CONTROL[0], NEIGHBOUR[0]):
            d = r[name]
            m = f"{d['margin']:.3g}x" if d["margin"] is not None else "--"
            cells.append(f"{name} {d['residual'] if d['residual'] is None else round(d['residual'], 4)}"
                         f"/{d['threshold']:g} = {m} {d['state']}")
        print(f"{r['seconds']:>8g}  {cells[0]:<44}{cells[1]:<40}")
    print(f"\n{'seconds':>8}{'recal thresholds (min..max)':>30}{'control survives':>18}"
          f"{'select: control':>28}{'select clean FA/REF':>22}")
    for r in res:
        th = [x["threshold"] for x in r["recal"]]
        surv = sum(x["survives"] for x in r["recal"])
        ctl = r["select"]["control"]
        fa = sum(v.get(pd.FAIL, 0) for v in r["select"]["clean"].values())
        rf = sum(v.get(pd.REFUSED, 0) for v in r["select"]["clean"].values())
        print(f"{r['seconds']:>8g}{f'{min(th):g}..{max(th):g}':>30}"
              f"{f'{surv}/{len(th)}':>18}{json.dumps(ctl):>28}{f'{fa}/{rf}':>22}")

    cand = next(r for r in res if r["seconds"] != BASELINE)
    ship, why = decide(base, cand)
    print(f"\nDECISION ({cand['seconds']:g} s vs {BASELINE:g} s, selection data only): "
          f"{'SHIP ' + format(cand['seconds'], 'g') + ' s' if ship else 'KEEP ' + format(BASELINE, 'g') + ' s'}")
    for w in why:
        print(f"   {w}")
    lit = cand[CONTROL[0]]["margin"] > base[CONTROL[0]]["margin"]
    print(f"   literal criterion only (margin_cand > margin_base): {lit} "
          f"({cand[CONTROL[0]]['margin']:.3g}x vs {base[CONTROL[0]]['margin']:.3g}x)")
    chosen = cand if ship else base

    print(f"\nCONFIRMATION on CONFIRM_BASE ({a.confirm_groups} groups x "
          f"{pd.TRIALS_DEFAULT} trials), never read by the decision")
    conf = confirm(chosen["seconds"], chosen, groups=a.confirm_groups, full_suite=True)
    other = confirm(cand["seconds"] if not ship else base["seconds"],
                    cand if not ship else base, groups=a.confirm_groups, full_suite=False)
    for label, cf in ((f"{conf['seconds']:g} s (shipped), all rows", conf),
                      (f"{other['seconds']:g} s (not shipped), noise rows", other)):
        print(f"   -- {label}, {cf['wall_s']:.0f} s")
        for cid, v in cf["rows"].items():
            print(f"      {cid:<26}{json.dumps(v)}")
        for key, v in cf["pairs"].items():
            print(f"      {key:<50}{json.dumps(v)}")
    bad = confirmation_holds(conf)
    print("\n   " + ("confirmation HOLDS: no clean false alarm, no refusal, every "
                     "detection pair CAUGHT in every group" if not bad
                     else "confirmation CONTRADICTS the shipped configuration:"))
    for b in bad:
        print(f"      {b}")
    total = time.time() - t_all
    print(f"\n   total {total:.0f} s")
    if a.json:
        a.json.parent.mkdir(parents=True, exist_ok=True)
        a.json.write_text(json.dumps(dict(
            schema="noise-fixture-duration-v1", provenance=prov, grid=list(GRID),
            baseline=BASELINE, predicted_ratio=PREDICTED_RATIO,
            invariance_max_delta=inv, candidates=res,
            decision=dict(ship=ship, seconds=chosen["seconds"], why=why,
                          literal_margin_criterion=bool(lit)),
            confirmation=dict(shipped=conf, other=other, contradictions=bad),
            runtime_s=total), indent=2, default=float))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
