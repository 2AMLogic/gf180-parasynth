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
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "tools"))
import run_case as rc
import scorecard
from score_ensemble_i2s import Refused
import delta_report  # noqa: E402

#: The three properties `docs/scorecard/cases.csv` requires of every Ensemble
#: case, with the units and tolerance basis the frozen policy gives them. A
#: record that renames one, or re-tunes one, is INCOMPARABLE rather than better.
CONTRACT = {
    "Event timing": ("ms", 10.0, "event timing"),
    "bus balance": ("dB", 0.5, "bus sum"),
    "output artifacts": ("% of samples", 0.01, "rail"),
}
ENSEMBLE_ENGINES = ("fixed-model", "integrated-rtl")

#: The sources a delta record's `provenance.source_sha256` identifies the
#: comparison by (kept at module scope, as `compare_drum_i2s_candidate` does).
SOURCES = ("tools/compare_ensemble_candidate.py", "tools/score_ensemble_i2s.py",
           "tools/run_case.py", "tools/scorecard.py", "tools/delta_report.py")


def _row(record: dict, label: str, *, engines: tuple) -> dict:
    """One side of the comparison: the board's verdict on it, which
    `delta_report.evaluate_record` recomputes, and then this comparison's
    own measurement contract."""
    row = delta_report.evaluate_record(record, label, engines=engines, family="Ensemble",
                                       refused=Refused)
    engine, case_id, metrics = row["engine"], row["case_id"], row["metrics"]
    if set(metrics) != set(CONTRACT):
        raise Refused(f"{label} does not contain exactly the three Ensemble properties")
    policy = record.get("tolerance_policy")
    if not isinstance(policy, dict):
        raise Refused(f"{label} omits its tolerance policy")
    for name, (units, limit, basis) in CONTRACT.items():
        metric = metrics[name]
        tolerance = delta_report.require_json_number(
            metric["tolerance"], f"{label} property {name} tolerance", refused=Refused)
        if metric.get("units") != units or tolerance != limit \
                or metric.get("tolerance_basis") != basis:
            raise Refused(f"{label} property {name} uses a different measurement contract")
        if policy.get(basis) != rc.TOLERANCE_POLICY[basis]:
            raise Refused(f"{label} tolerance policy for {basis} is not the frozen contract")
    if engine == "integrated-rtl":
        diagnostics = record.get("diagnostics") or {}
        decoded = diagnostics.get("decoded_i2s_sha256")
        if not isinstance(decoded, dict) or "mix" not in decoded:
            raise Refused(f"{label} is integrated-rtl but binds no decoded I2S evidence")
        per_stop = diagnostics.get("per_stop_event_timing")
        if not isinstance(per_stop, dict) or not per_stop:
            raise Refused(f"{label} is integrated-rtl but names no per-stop event timing")
    verdict = row["verdict"]
    return {"engine": engine, "case_id": case_id, "metrics": metrics,
            "state": verdict["state"], "worst": verdict["worst"],
            "source_commit": record.get("source_commit"),
            "decoded_i2s_sha256": (record.get("diagnostics") or {}).get("decoded_i2s_sha256"),
            "negative_control": (record.get("diagnostics") or {}).get("negative_control")}


def compare_records(baseline: dict, candidate: dict) -> dict:
    """The candidate must be integrated-rtl; the baseline may be either engine."""
    candidate_row = _row(candidate, "candidate", engines=("integrated-rtl",))
    baseline_row = _row(baseline, "baseline", engines=ENSEMBLE_ENGINES)
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
    report["provenance"] = delta_report.comparison_provenance(
        baseline_path, candidate_path, SOURCES, root=ROOT)
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("baseline", help="the record being compared against (either engine)")
    ap.add_argument("candidate", help="the integrated-rtl record under consideration")
    ap.add_argument("--out", default="build/scorecard/ensemble-integrated-delta.json")
    a = ap.parse_args(argv)
    baseline = delta_report.repo_path(a.baseline, root=ROOT)
    candidate = delta_report.repo_path(a.candidate, root=ROOT)
    try:
        report = compare_files(baseline, candidate)
    except (OSError, ValueError, Refused, subprocess.CalledProcessError) as exc:
        print(f"compare_ensemble_candidate: REFUSED -- {exc}")
        return 2
    out = delta_report.write_report(report, a.out, "compare_ensemble_candidate", root=ROOT)
    if out is None:
        return 2
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
