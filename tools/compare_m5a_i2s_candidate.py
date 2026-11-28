#!/usr/bin/env python3
"""Compare two complete, independently scored M5A SPI-to-I2S records."""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "tools"))
import measure_m5a_filter_oversample as filter_gate
import mono_m5a_score as m5a
import delta_report  # noqa: E402

#: The sources a delta record's `provenance.source_sha256` identifies the
#: comparison by (kept at module scope, as `compare_drum_i2s_candidate` does).
SOURCES = ("tools/compare_m5a_i2s_candidate.py",
           "tools/measure_m5a_filter_oversample.py", "tools/mono_m5a_score.py",
           "tools/delta_report.py")


class _NegativeUnderflow(float):
    """A strictly negative JSON number that rounded to -0.0."""


def _parse_score_float(token: str) -> float:
    value = float(token)
    if value == 0.0 and token.startswith("-"):
        # Exponent digits do not distinguish an exact zero from underflow.
        significand = token.lower().partition("e")[0]
        if any(digit in "123456789" for digit in significand):
            return _NegativeUnderflow(value)
    return value


def _row(record: dict, label: str) -> dict:
    if record.get("engine") != "integrated-rtl" or record.get("case_id") != "M5A":
        raise m5a.Refused(f"{label} must be an integrated-rtl M5A record")
    if record.get("scorecard_state") not in ("pass", "fail"):
        raise m5a.Refused(f"{label} has no valid scorecard verdict")
    metrics = record.get("metrics")
    if not isinstance(metrics, dict) or set(metrics) != set(m5a.TOLERANCES):
        raise m5a.Refused(f"{label} does not contain all seven M5A properties")
    policy = record.get("tolerance_policy")
    if not isinstance(policy, dict) or set(policy) != set(m5a.TOLERANCES):
        raise m5a.Refused(f"{label} omits the complete fixed tolerance policy")
    for name, (limit, basis) in m5a.TOLERANCES.items():
        metric = metrics[name]
        if not isinstance(metric, dict) or metric.get("valid") is not True:
            raise m5a.Refused(f"{label} property {name} is invalid")
        try:
            error = float(metric["error"])
            tolerance = float(metric["tolerance"])
        except (KeyError, TypeError, ValueError) as exc:
            raise m5a.Refused(f"{label} property {name} lacks a numeric error or tolerance") from exc
        if not math.isfinite(error) or not math.isfinite(tolerance):
            raise m5a.Refused(f"{label} property {name} has a non-finite measurement")
        if (metric.get("units") != _expected_units(name)
                or tolerance != float(limit)
                or metric.get("tolerance_basis") != basis):
            raise m5a.Refused(f"{label} property {name} uses a different measurement contract")
        try:
            policy_limit, policy_basis = policy[name]
            policy_limit = float(policy_limit)
        except (TypeError, ValueError) as exc:
            raise m5a.Refused(f"{label} tolerance policy for {name} is malformed") from exc
        if policy_limit != float(limit) or policy_basis != basis:
            raise m5a.Refused(f"{label} tolerance policy for {name} is not the frozen contract")
    diagnostics = record.get("diagnostics", {})
    if not isinstance(diagnostics, dict):
        raise m5a.Refused(f"{label} diagnostics are malformed")
    events = diagnostics.get("events")
    if (not isinstance(events, list) or not events
            or any(not isinstance(event, dict) for event in events)
            or not diagnostics.get("decoded_i2s_sha256")):
        raise m5a.Refused(f"{label} lacks decoded-I2S event evidence")
    identities = [(event.get("wave"), event.get("midi")) for event in events]
    if any(not wave or midi is None for wave, midi in identities) or len(set(identities)) != len(identities):
        raise m5a.Refused(f"{label} event identities are missing or duplicated")
    if not diagnostics.get("reference_sha256") or not diagnostics.get("reference_manifest_sha256"):
        raise m5a.Refused(f"{label} lacks frozen reference identity")
    analysis_version = record.get("analysis_version")
    if not isinstance(analysis_version, str) or not analysis_version:
        raise m5a.Refused(f"{label} omits its analysis-version identifier")
    if not isinstance(record.get("reference_profile"), str) or not record["reference_profile"]:
        raise m5a.Refused(f"{label} omits its reference profile")
    return {"metrics": metrics, "event_diagnostics": events,
            "analysis_version": analysis_version}


def _expected_units(name: str) -> str:
    return {"Pitch": "cents", "Harmonic shape": "dB", "Foldback energy": "dB",
            "Envelope attack": "ms", "Envelope release": "ms",
            "Gain": "dB", "Clipping": "%"}[name]


def _event_errors(event: dict, label: str) -> dict[str, dict[str, float]]:
    """Extract independently thresholdable values for one note/wave event."""
    identity = f"{label}:{event.get('wave')}/{event.get('midi')}"
    try:
        recorded_excess = event["foldback_db"]["excess_over_reference_db"]
        if isinstance(recorded_excess, bool) or not isinstance(recorded_excess, (int, float)):
            raise m5a.Refused(f"{identity} foldback excess must be a JSON number")
        excess = float(recorded_excess)
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise m5a.Refused(f"{identity} lacks a numeric foldback excess") from exc
    if isinstance(recorded_excess, _NegativeUnderflow) or not 0 <= excess < math.inf:
        raise m5a.Refused(f"{identity} foldback excess must be finite and nonnegative")

    try:
        errors = {
            "Pitch": abs(float(event["pitch_cents_from_midi"]["model_minus_reference"])),
            "Foldback energy": excess,
            "Envelope attack": abs(float(event["envelope_ms"]["attack_model"]
                                          - event["envelope_ms"]["attack_reference"])),
            "Envelope release": abs(float(event["envelope_ms"]["release_model"]
                                           - event["envelope_ms"]["release_reference"])),
            "Gain": abs(float(event["gain_dbfs"]["model"] - event["gain_dbfs"]["reference"])),
        }
        partials = event["harmonic_error_db_model_minus_reference"]
        if not isinstance(partials, dict) or not partials:
            raise ValueError("no measured harmonics")
        errors.update({f"Harmonic shape/{partial}": abs(float(value))
                       for partial, value in partials.items()})
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        # OverflowError: a JSON integer beyond float range, which `float()`
        # refuses and which left here as a traceback -- exit 1, FAIL -- where
        # the foldback excess above REFUSES the same input (#1669).
        raise m5a.Refused(f"{identity} lacks complete per-note property evidence") from exc
    if any(not math.isfinite(value) for value in errors.values()):
        raise m5a.Refused(f"{identity} has a non-finite per-note measurement")
    return {name: {"error": value,
                   "limit": (1.0 if name.startswith("Harmonic shape/")
                             else float(m5a.TOLERANCES[name][0]))}
            for name, value in errors.items()}


def _provenance_config(record: dict, identity: str) -> dict:
    provenance = record.get("provenance")
    if not isinstance(provenance, dict):
        raise m5a.Refused(f"{identity} provenance is absent, null or not an object")
    config = provenance.get("config")
    if not isinstance(config, dict):
        raise m5a.Refused(f"{identity} provenance config is absent, null or not an object")
    return config


def compare_records(baseline: dict, candidate: dict) -> dict:
    base_row, candidate_row = _row(baseline, "baseline"), _row(candidate, "candidate")
    if base_row["analysis_version"] != candidate_row["analysis_version"]:
        raise m5a.Refused("baseline and candidate analysis-version identifiers differ")
    if baseline.get("reference_profile") != candidate.get("reference_profile"):
        raise m5a.Refused("baseline and candidate reference profiles differ")
    if baseline.get("tolerance_policy") != candidate.get("tolerance_policy"):
        raise m5a.Refused("baseline and candidate tolerance policies differ")
    base_diag, candidate_diag = baseline["diagnostics"], candidate["diagnostics"]
    if (base_diag["reference_sha256"] != candidate_diag["reference_sha256"]
            or base_diag["reference_manifest_sha256"] != candidate_diag["reference_manifest_sha256"]):
        raise m5a.Refused("baseline and candidate use different frozen reference audio or manifest")
    base_config = _provenance_config(baseline, "baseline")
    candidate_config = _provenance_config(candidate, "candidate")
    base_filter = base_config.get("filter_config")
    candidate_filter = candidate_config.get("filter_config")
    if not base_filter or base_filter != candidate_filter:
        raise m5a.Refused("baseline and candidate filter configurations differ or are missing")
    comparison = filter_gate._compare_incremental(base_row, candidate_row)
    base_events, candidate_events = base_row["event_diagnostics"], candidate_row["event_diagnostics"]
    if len(base_events) != len(candidate_events):
        raise m5a.Refused("baseline and candidate event evidence differs")
    preserved_passes, lost_passes, degraded_within_limit = [], [], []
    for before, after in zip(base_events, candidate_events):
        identity = (before.get("wave"), before.get("midi"))
        if identity != (after.get("wave"), after.get("midi")):
            raise m5a.Refused("baseline and candidate event order differs")
        before_errors = _event_errors(before, "baseline")
        after_errors = _event_errors(after, "candidate")
        if set(before_errors) != set(after_errors):
            raise m5a.Refused("baseline and candidate per-note properties differ")
        for prop, base_value in before_errors.items():
            cand_value = after_errors[prop]
            if base_value["limit"] != cand_value["limit"]:
                raise m5a.Refused(f"per-note limit differs for {identity}/{prop}")
            base_pass = base_value["error"] <= base_value["limit"]
            candidate_pass = cand_value["error"] <= cand_value["limit"]
            item = {"wave": identity[0], "midi": identity[1], "property": prop,
                    "baseline_error": round(base_value["error"], 6),
                    "candidate_error": round(cand_value["error"], 6),
                    "limit": base_value["limit"]}
            if base_pass and not candidate_pass:
                lost_passes.append(item)
            elif base_pass:
                preserved_passes.append(item)
                if cand_value["error"] > base_value["error"] + 0.01:
                    degraded_within_limit.append(item)
    comparison["preserved_per_note_passes"] = preserved_passes
    comparison["lost_per_note_passes"] = lost_passes
    comparison["degraded_but_within_per_note_limit"] = degraded_within_limit
    comparison["accepts_incremental_improvement"] = (
        comparison["accepts_incremental_improvement"] and not lost_passes)
    return {
        "schema": "m5a-integrated-rtl-delta-v1", "valid": True,
        "reference_sha256": base_diag["reference_sha256"],
        "manifest_sha256": base_diag["reference_manifest_sha256"],
        "filter_config": base_filter,
        "analysis_version": base_row["analysis_version"],
        "baseline": {"source_commit": baseline.get("source_commit"),
                     "scorecard_state": baseline["scorecard_state"],
                     "decoded_i2s_sha256": base_diag["decoded_i2s_sha256"],
                     "metrics": baseline["metrics"]},
        "candidate": {"source_commit": candidate.get("source_commit"),
                      "scorecard_state": candidate["scorecard_state"],
                      "decoded_i2s_sha256": candidate_diag["decoded_i2s_sha256"],
                      "controls": candidate_config,
                      "metrics": candidate["metrics"]},
        "comparison": comparison,
    }


def compare_files(baseline_path: pathlib.Path, candidate_path: pathlib.Path) -> dict:
    try:
        baseline = json.loads(baseline_path.read_text(), parse_float=_parse_score_float)
        candidate = json.loads(candidate_path.read_text(), parse_float=_parse_score_float)
    except (OSError, json.JSONDecodeError) as exc:
        raise m5a.Refused(f"cannot load M5A score record: {exc}") from exc
    report = compare_records(baseline, candidate)
    report["provenance"] = delta_report.comparison_provenance(
        baseline_path, candidate_path, SOURCES, root=ROOT)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", help="valid prior integrated M5A score JSON")
    parser.add_argument("candidate", help="valid candidate integrated M5A score JSON")
    parser.add_argument("--out", default="build/scorecard/m5a-integrated-delta.json")
    args = parser.parse_args(argv)
    baseline = delta_report.repo_path(args.baseline, root=ROOT)
    candidate = delta_report.repo_path(args.candidate, root=ROOT)
    try:
        report = compare_files(baseline, candidate)
        out = delta_report.write_report(report, args.out, "compare_m5a_i2s_candidate", root=ROOT)
    except (OSError, ValueError, m5a.Refused, subprocess.CalledProcessError) as exc:
        print(f"compare_m5a_i2s_candidate: REFUSED -- {exc}")
        return 2
    if out is None:
        return 2
    comparison = report["comparison"]
    print(json.dumps({"report": str(out.relative_to(ROOT)),
                      "accepts_incremental_improvement": comparison["accepts_incremental_improvement"],
                      "case_passes": comparison["case_passes"],
                      "improved_components": len(comparison["improved_components"]),
                      "regressed_components": comparison["regressed_components"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
