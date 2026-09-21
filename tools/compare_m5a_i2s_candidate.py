#!/usr/bin/env python3
"""Compare two complete, independently scored M5A SPI-to-I2S records."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "tools"))
import measure_m5a_filter_oversample as filter_gate
import mono_m5a_score as m5a


def _row(record: dict, label: str) -> dict:
    if record.get("engine") != "integrated-rtl" or record.get("case_id") != "M5A":
        raise m5a.Refused(f"{label} must be an integrated-rtl M5A record")
    if record.get("scorecard_state") not in ("pass", "fail"):
        raise m5a.Refused(f"{label} has no valid scorecard verdict")
    metrics = record.get("metrics")
    if not isinstance(metrics, dict) or set(metrics) != set(m5a.TOLERANCES):
        raise m5a.Refused(f"{label} does not contain all seven M5A properties")
    diagnostics = record.get("diagnostics", {})
    events = diagnostics.get("events")
    if not events or not diagnostics.get("decoded_i2s_sha256"):
        raise m5a.Refused(f"{label} lacks decoded-I2S event evidence")
    if not diagnostics.get("reference_sha256") or not diagnostics.get("reference_manifest_sha256"):
        raise m5a.Refused(f"{label} lacks frozen reference identity")
    return {"metrics": metrics, "event_diagnostics": events}


def compare_records(baseline: dict, candidate: dict) -> dict:
    base_row, candidate_row = _row(baseline, "baseline"), _row(candidate, "candidate")
    base_diag, candidate_diag = baseline["diagnostics"], candidate["diagnostics"]
    if (base_diag["reference_sha256"] != candidate_diag["reference_sha256"]
            or base_diag["reference_manifest_sha256"] != candidate_diag["reference_manifest_sha256"]):
        raise m5a.Refused("baseline and candidate use different frozen reference audio or manifest")
    base_filter = baseline.get("provenance", {}).get("config", {}).get("filter_config")
    candidate_filter = candidate.get("provenance", {}).get("config", {}).get("filter_config")
    if not base_filter or base_filter != candidate_filter:
        raise m5a.Refused("baseline and candidate filter configurations differ or are missing")
    comparison = filter_gate._compare_incremental(base_row, candidate_row)
    return {
        "schema": "m5a-integrated-rtl-delta-v1", "valid": True,
        "reference_sha256": base_diag["reference_sha256"],
        "manifest_sha256": base_diag["reference_manifest_sha256"],
        "filter_config": base_filter,
        "baseline": {"source_commit": baseline.get("source_commit"),
                     "scorecard_state": baseline["scorecard_state"],
                     "decoded_i2s_sha256": base_diag["decoded_i2s_sha256"],
                     "metrics": baseline["metrics"]},
        "candidate": {"source_commit": candidate.get("source_commit"),
                      "scorecard_state": candidate["scorecard_state"],
                      "decoded_i2s_sha256": candidate_diag["decoded_i2s_sha256"],
                      "controls": candidate.get("provenance", {}).get("config", {}),
                      "metrics": candidate["metrics"]},
        "comparison": comparison,
    }


def compare_files(baseline_path: pathlib.Path, candidate_path: pathlib.Path) -> dict:
    try:
        baseline = json.loads(baseline_path.read_text())
        candidate = json.loads(candidate_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise m5a.Refused(f"cannot load M5A score record: {exc}") from exc
    report = compare_records(baseline, candidate)
    sources = ("tools/compare_m5a_i2s_candidate.py",
               "tools/measure_m5a_filter_oversample.py", "tools/mono_m5a_score.py")
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", help="valid prior integrated M5A score JSON")
    parser.add_argument("candidate", help="valid candidate integrated M5A score JSON")
    parser.add_argument("--out", default="build/scorecard/m5a-integrated-delta.json")
    args = parser.parse_args(argv)
    baseline = pathlib.Path(args.baseline)
    candidate = pathlib.Path(args.candidate)
    if not baseline.is_absolute(): baseline = ROOT / baseline
    if not candidate.is_absolute(): candidate = ROOT / candidate
    try:
        report = compare_files(baseline, candidate)
    except (OSError, ValueError, m5a.Refused, subprocess.CalledProcessError) as exc:
        print(f"compare_m5a_i2s_candidate: REFUSED -- {exc}")
        return 2
    out = pathlib.Path(args.out)
    if not out.is_absolute(): out = ROOT / out
    if not out.resolve().is_relative_to(ROOT):
        print("compare_m5a_i2s_candidate: REFUSED -- output must remain inside the repository")
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    comparison = report["comparison"]
    print(json.dumps({"report": str(out.relative_to(ROOT)),
                      "accepts_incremental_improvement": comparison["accepts_incremental_improvement"],
                      "case_passes": comparison["case_passes"],
                      "improved_components": len(comparison["improved_components"]),
                      "regressed_components": comparison["regressed_components"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
