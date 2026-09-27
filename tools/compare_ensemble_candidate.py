#!/usr/bin/env python3
"""Compare two scored Ensemble records, one of which MUST be integrated-rtl.

    tools/compare_ensemble_candidate.py <baseline> <candidate>

WHY THIS EXISTS AND WHAT IT REFUSES. An ensemble case has exactly one result
path on the board, `docs/scorecard/results/<case>.json`, so an integrated-rtl
anchor lands where the `fixed-model` twin used to be. Without a tool that
compares them the twin is simply gone and any disagreement between the model
and the chip leaves no trace -- which is the failure mode this repository keeps
producing: the cheap internal check (the RTL agrees with the RTL) passing for
the expensive external one (the chip agrees with the model of it).

So the CANDIDATE is required to be `integrated-rtl`. That is the gate, not a
formality: a comparison that accepts two `fixed-model` records would certify
the model against itself and report it as instrument evidence. The BASELINE may
be either engine, and the report says which comparison it made:

  cross-engine  fixed-model versus integrated-rtl. "Do the model and the chip
                agree about this case?" A disagreement here is a finding about
                the integration, not about the case.
  rtl-to-rtl    a prior anchor versus a new one. "Did this change move the
                chip?" Requires the same measurement contract on both sides.

Both sides must carry the case's complete required measurement set under the
frozen tolerance policy, and both are scored by `tools/scorecard.py` here
rather than trusted to report their own verdict -- a record that names its own
state is a claim, and the board's `evaluate` is the only thing entitled to make
it.

Exit status: 0 the comparison was made, 2 REFUSED (no comparison).
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "tools"))
import run_case as rc
import scorecard
from score_ensemble_i2s import Refused

#: The three properties `docs/scorecard/cases.csv` requires of every Ensemble
#: case, with the units and tolerance basis the frozen policy gives them. A
#: record that renames one, or re-tunes one, is INCOMPARABLE rather than better.
CONTRACT = {
    "Event timing": ("ms", 10.0, "event timing"),
    "bus balance": ("dB", 0.5, "bus sum"),
    "output artifacts": ("% of samples", 0.01, "rail"),
}
ENSEMBLE_ENGINES = ("fixed-model", "integrated-rtl")


def _case(case_id: str) -> dict:
    case = next((c for c in rc.load_cases() if c["case_id"] == case_id), None)
    if case is None:
        raise Refused(f"{case_id} is not a case in docs/scorecard/cases.csv")
    if case["family"] != "Ensemble":
        raise Refused(f"{case_id} is a {case['family']} case, not an Ensemble case")
    return case


def _row(record: dict, label: str, *, require_rtl: bool) -> dict:
    """One side of the comparison, with its preconditions asserted here."""
    engine = record.get("engine")
    if require_rtl and engine != "integrated-rtl":
        raise Refused(f"{label} must be an integrated-rtl record, not {engine!r}")
    if engine not in ENSEMBLE_ENGINES:
        raise Refused(f"{label} names engine {engine!r}, which is not one this "
                      f"comparison knows ({', '.join(ENSEMBLE_ENGINES)})")
    case_id = record.get("case_id")
    if not case_id:
        raise Refused(f"{label} does not name its case")
    metrics = record.get("metrics")
    if not isinstance(metrics, dict) or set(metrics) != set(CONTRACT):
        raise Refused(f"{label} does not contain exactly the three Ensemble properties")
    policy = record.get("tolerance_policy")
    if not isinstance(policy, dict):
        raise Refused(f"{label} omits its tolerance policy")
    for name, (units, limit, basis) in CONTRACT.items():
        metric = metrics[name]
        if not isinstance(metric, dict) or metric.get("valid") is not True:
            raise Refused(f"{label} property {name} is invalid: no distance to compare")
        try:
            error = float(metric["error"])
            tolerance = float(metric["tolerance"])
        except (KeyError, TypeError, ValueError) as exc:
            raise Refused(f"{label} property {name} lacks a numeric error or tolerance") from exc
        if not math.isfinite(error) or not math.isfinite(tolerance):
            raise Refused(f"{label} property {name} has a non-finite measurement")
        if metric.get("units") != units or tolerance != limit \
                or metric.get("tolerance_basis") != basis:
            raise Refused(f"{label} property {name} uses a different measurement contract")
        if policy.get(basis) != rc.TOLERANCE_POLICY[basis]:
            raise Refused(f"{label} tolerance policy for {basis} is not the frozen contract")
    if not isinstance(record.get("provenance"), dict) or not record["provenance"].get("inputs"):
        raise Refused(f"{label} carries no provenance inputs: it cannot be re-derived")
    if engine == "integrated-rtl":
        diagnostics = record.get("diagnostics") or {}
        decoded = diagnostics.get("decoded_i2s_sha256")
        if not isinstance(decoded, dict) or "mix" not in decoded:
            raise Refused(f"{label} is integrated-rtl but binds no decoded I2S evidence")
        per_stop = diagnostics.get("per_stop_event_timing")
        if not isinstance(per_stop, dict) or not per_stop:
            raise Refused(f"{label} is integrated-rtl but names no per-stop event timing")
    verdict = scorecard.evaluate(_case(case_id), record)
    if verdict["state"] not in (scorecard.PASS, scorecard.FAIL):
        raise Refused(f"{label} has no valid board verdict: {verdict['why']}")
    return {"engine": engine, "case_id": case_id, "metrics": metrics,
            "state": verdict["state"], "worst": verdict["worst"],
            "source_commit": record.get("source_commit"),
            "decoded_i2s_sha256": (record.get("diagnostics") or {}).get("decoded_i2s_sha256"),
            "negative_control": (record.get("diagnostics") or {}).get("negative_control")}


def compare_records(baseline: dict, candidate: dict) -> dict:
    """The candidate must be integrated-rtl; the baseline may be either engine."""
    candidate_row = _row(candidate, "candidate", require_rtl=True)
    baseline_row = _row(baseline, "baseline", require_rtl=False)
    if baseline_row["case_id"] != candidate_row["case_id"]:
        raise Refused("baseline and candidate are different cases")
    mode = ("cross-engine" if baseline_row["engine"] != candidate_row["engine"]
            else "rtl-to-rtl")
    if mode == "rtl-to-rtl" and baseline_row["engine"] != "integrated-rtl":
        raise Refused("two fixed-model records cannot certify the instrument")
    control = candidate_row["negative_control"]
    if not isinstance(control, dict) or control.get("caught") is not True:
        raise Refused("the candidate carries no CAUGHT negative control: the comparison it "
                      "rests on has not been shown to be discriminating")

    per_metric, improved, regressed, lost_passes = {}, [], [], []
    for name, (units, limit, _basis) in sorted(CONTRACT.items()):
        before = float(baseline_row["metrics"][name]["error"])
        after = float(candidate_row["metrics"][name]["error"])
        before_pass, after_pass = abs(before) <= limit, abs(after) <= limit
        item = {"units": units, "limit": limit,
                "baseline_error": round(before, 6), "candidate_error": round(after, 6),
                "baseline_inside_tolerance": before_pass,
                "candidate_inside_tolerance": after_pass,
                "delta": round(abs(after) - abs(before), 6),
                "delta_fraction_of_tolerance": round((abs(after) - abs(before)) / limit, 6)}
        per_metric[name] = item
        if before_pass and not after_pass:
            lost_passes.append(name)
        elif abs(after) < abs(before) - 1e-9:
            improved.append(name)
        elif abs(after) > abs(before) + 1e-9:
            regressed.append(name)
    return {
        "schema": "ensemble-integrated-rtl-delta-v1", "valid": True,
        "comparison_mode": mode,
        "case_id": candidate_row["case_id"],
        "baseline": {"engine": baseline_row["engine"], "state": baseline_row["state"],
                     "worst": (None if baseline_row["worst"] is None
                               else round(baseline_row["worst"], 4)),
                     "source_commit": baseline_row["source_commit"]},
        "candidate": {"engine": candidate_row["engine"], "state": candidate_row["state"],
                      "worst": (None if candidate_row["worst"] is None
                                else round(candidate_row["worst"], 4)),
                      "source_commit": candidate_row["source_commit"],
                      "decoded_i2s_sha256": candidate_row["decoded_i2s_sha256"],
                      "negative_control": {"inject": control.get("inject"),
                                           "caught": control.get("caught")}},
        "comparison": {
            "states_agree": baseline_row["state"] == candidate_row["state"],
            "case_passes": candidate_row["state"] == scorecard.PASS,
            "metrics": per_metric,
            "improved_components": improved,
            "regressed_components": regressed,
            "lost_passes": lost_passes,
            # The one summary judgement, and it is deliberately conservative:
            # a candidate that lost a pass on any property is not accepted
            # however much the others improved.
            "accepts_candidate": (candidate_row["state"] == scorecard.PASS and not lost_passes),
        },
    }


def compare_files(baseline_path: pathlib.Path, candidate_path: pathlib.Path) -> dict:
    try:
        baseline = json.loads(baseline_path.read_text())
        candidate = json.loads(candidate_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise Refused(f"cannot load an Ensemble score record: {exc}") from exc
    report = compare_records(baseline, candidate)
    sources = ("tools/compare_ensemble_candidate.py", "tools/score_ensemble_i2s.py",
               "tools/run_case.py", "tools/scorecard.py")
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                            check=True, capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                                check=True, capture_output=True, text=True).stdout.strip())
    report["provenance"] = {
        "source_commit": commit, "source_dirty": dirty,
        "input_sha256": {
            "baseline_record": hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
            "candidate_record": hashlib.sha256(candidate_path.read_bytes()).hexdigest()},
        "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                          for name in sources},
        "run_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("baseline", help="the record being compared against (either engine)")
    ap.add_argument("candidate", help="the integrated-rtl record under consideration")
    ap.add_argument("--out", default="build/scorecard/ensemble-integrated-delta.json")
    a = ap.parse_args(argv)
    baseline = pathlib.Path(a.baseline)
    candidate = pathlib.Path(a.candidate)
    if not baseline.is_absolute():
        baseline = ROOT / baseline
    if not candidate.is_absolute():
        candidate = ROOT / candidate
    try:
        report = compare_files(baseline, candidate)
    except (OSError, ValueError, Refused, subprocess.CalledProcessError) as exc:
        print(f"compare_ensemble_candidate: REFUSED -- {exc}")
        return 2
    out = pathlib.Path(a.out)
    if not out.is_absolute():
        out = ROOT / out
    if not out.resolve().is_relative_to(ROOT):
        print("compare_ensemble_candidate: REFUSED -- output must remain inside the repository")
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    comparison = report["comparison"]
    print(json.dumps({"report": str(out.relative_to(ROOT)),
                      "comparison_mode": report["comparison_mode"],
                      "states_agree": comparison["states_agree"],
                      "case_passes": comparison["case_passes"],
                      "improved": comparison["improved_components"],
                      "regressed": comparison["regressed_components"],
                      "lost_passes": comparison["lost_passes"],
                      "accepts_candidate": comparison["accepts_candidate"]}, indent=2))
    if not comparison["states_agree"]:
        print(f"compare_ensemble_candidate: THE TWO RECORDS DISAGREE -- "
              f"{report['baseline']['engine']} {report['baseline']['state']} vs "
              f"{report['candidate']['engine']} {report['candidate']['state']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
