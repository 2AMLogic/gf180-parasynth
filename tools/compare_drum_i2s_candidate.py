#!/usr/bin/env python3
"""Compare an integrated-RTL drum record against its fixed-model twin.

    tools/compare_drum_i2s_candidate.py <fixed-model twin> <integrated-rtl record>

`tools/score_drum_i2s.py` replaces a Drums row on the board with a measurement
taken off the chip's I2S pins. The row it replaces was measured on
`model/drums_fx.py`. Issue #94's instruction about that swap is explicit --
"every model row on the board needs re-reading" -- so the difference between
the two has to be PUBLISHED, per property, rather than absorbed by the
overwrite.

What it refuses:

  * a candidate whose `engine` is not `integrated-rtl`. A fixed-model record --
    or a mutation run's -- must not be filed as chip evidence, which is the one
    thing this gate exists for;
  * a baseline whose `engine` is not `fixed-model`. Comparing a chip record
    with another chip record answers a different question;
  * two records of different cases, different references, different tolerance
    policies or different properties. A delta between incomparable measurements
    is a number with no meaning.

What it reports: per property, both values, the signed difference, that
difference as a fraction of the property's own tolerance, and whether the two
engines reach the SAME verdict on it; then the case verdicts side by side.

ATTRIBUTION, which is the part that matters. A drum record carries the
controlled comparison `score_drum_i2s` checked: was the decoded window bit-exact
against the fixed model struck in the frame the LINK put the strike in? If it
was, the engines are the same engine and every difference below is the
STIMULUS -- the drum noise LFSR free-runs, so a strike in a different frame is
a different waveform. If it was not, the difference is the chip and is reported
as such. The two are never merged into one "delta".
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
import run_case  # noqa: E402
import scorecard  # noqa: E402

CANDIDATE_ENGINE = "integrated-rtl"
BASELINE_ENGINE = "fixed-model"
SOURCES = ("tools/compare_drum_i2s_candidate.py", "tools/score_drum_i2s.py",
           "tools/run_case.py", "tools/scorecard.py")


class Refused(Exception):
    """The two records cannot be compared, so there is no comparison."""


def _metrics(record: dict, label: str) -> dict:
    metrics = record.get("metrics")
    if not isinstance(metrics, dict) or not metrics:
        raise Refused(f"{label} carries no metrics")
    for name, metric in metrics.items():
        if not isinstance(metric, dict):
            raise Refused(f"{label} property {name} is malformed")
        if metric.get("valid") is not True:
            raise Refused(f"{label} property {name} is invalid, so it has no distance")
        try:
            error = float(metric["error"])
            tolerance = float(metric["tolerance"])
            value = float(metric["value"])
        except (KeyError, TypeError, ValueError) as exc:
            raise Refused(f"{label} property {name} lacks a numeric value, error or "
                          f"tolerance") from exc
        if not all(math.isfinite(v) for v in (error, tolerance, value)) or tolerance <= 0.0:
            raise Refused(f"{label} property {name} has a non-finite or zero-tolerance "
                          f"measurement")
    return metrics


def _row(record: dict, label: str, engine: str, case: dict) -> dict:
    """One record's metrics and the verdict THE BOARD would give it.

    The verdict is recomputed with `scorecard.evaluate` rather than read out of
    the record: `run_case` does not store one, and a stored one that disagrees
    with the board is the bug this would otherwise hide."""
    if not isinstance(record, dict):
        raise Refused(f"{label} is not a record")
    if record.get("engine") != engine:
        raise Refused(f"{label} must name engine {engine!r} (saw {record.get('engine')!r})")
    provenance_engine = (record.get("provenance") or {}).get("engine")
    if provenance_engine is not None and provenance_engine != engine:
        raise Refused(f"{label} record and provenance disagree about the engine "
                      f"({engine!r} vs {provenance_engine!r})")
    if not record.get("case_id"):
        raise Refused(f"{label} does not name its case")
    if not record.get("reference_profile"):
        raise Refused(f"{label} does not name its reference profile")
    verdict = scorecard.evaluate(case, record)
    if verdict["state"] not in (scorecard.PASS, scorecard.FAIL):
        raise Refused(f"{label} has no pass/fail verdict on the board: {verdict['why']}")
    stored = record.get("scorecard_state")
    if stored is not None and stored != verdict["state"]:
        raise Refused(f"{label} stores state {stored!r} but the board evaluates it as "
                      f"{verdict['state']!r}")
    return {"metrics": _metrics(record, label), "verdict": verdict}


def _integration(candidate: dict) -> dict:
    integration = (candidate.get("diagnostics") or {}).get("integration")
    if not isinstance(integration, dict):
        raise Refused("the candidate carries no integration evidence, so nothing about it "
                      "can be attributed to the chip rather than the stimulus")
    controlled = integration.get("fixed_model_at_realised_strike")
    phase = integration.get("fixed_model_scorecard_render")
    if not isinstance(controlled, dict) or not isinstance(phase, dict):
        raise Refused("the candidate omits one of the two fixed-model comparisons")
    for key in ("differing_samples", "max_abs_difference", "identical"):
        if key not in controlled or key not in phase:
            raise Refused(f"the candidate's fixed-model comparisons omit {key}")
    if not integration.get("simulator"):
        raise Refused("the candidate does not name the simulator its samples came from")
    return integration


def _attribution(integration: dict, differing: list) -> dict:
    controlled = integration["fixed_model_at_realised_strike"]
    phase = integration["fixed_model_scorecard_render"]
    same_engine = bool(controlled["identical"])
    same_stimulus = bool(phase["identical"])
    if not same_engine:
        why = ("THE CHIP. The decoded window is not bit-exact against the fixed model struck "
               f"in the same frame ({controlled['differing_samples']} samples differ, max "
               f"{controlled['max_abs_difference']} LSB), so the two engines are not the same "
               "engine and the property differences below are a defect report.")
    elif not same_stimulus:
        why = ("THE STIMULUS, not the engine. The decoded window is bit-exact against the "
               "fixed model struck in the frame the SPI link put the strike in, and differs "
               f"from the board's own render ({phase['differing_samples']} samples, max "
               f"{phase['max_abs_difference']} LSB) only because that render strikes in frame "
               f"{phase.get('strike_frame')} and the drum noise LFSR free-runs. The chip and "
               "the model are the same engine on the same input.")
    elif differing:
        why = ("NEITHER, which cannot be right. The decoded window is bit-identical to both "
               "fixed-model renders, so identical samples produced different numbers: the "
               "difference is in the ESTIMATOR, and this comparison is evidence of that "
               "rather than of either engine.")
    else:
        why = ("nothing to attribute: the decoded window is bit-identical to the board's own "
               "render and every property agrees.")
    return {"engines_bit_identical_on_the_same_stimulus": same_engine,
            "stimulus_identical": same_stimulus,
            "attributable_to": ("the chip" if not same_engine else
                                "the stimulus" if not same_stimulus else
                                "the estimator" if differing else "nothing"),
            "why": why}


def compare_records(baseline: dict, candidate: dict, case: dict | None = None) -> dict:
    if not isinstance(candidate, dict) or candidate.get("engine") != CANDIDATE_ENGINE:
        raise Refused(f"candidate must name engine {CANDIDATE_ENGINE!r} "
                      f"(saw {(candidate or {}).get('engine')!r})")
    if baseline.get("case_id") != candidate.get("case_id"):
        raise Refused(f"baseline is {baseline.get('case_id')} and candidate is "
                      f"{candidate.get('case_id')}")
    if case is None:
        case = next((c for c in run_case.load_cases()
                     if c["case_id"] == candidate.get("case_id")), None)
        if case is None:
            raise Refused(f"{candidate.get('case_id')} is not a case in "
                          f"docs/scorecard/cases.csv")
    if case.get("family") != "Drums":
        raise Refused(f"{case['case_id']} is a {case.get('family')} case, not a Drums case")
    base_row = _row(baseline, "baseline", BASELINE_ENGINE, case)
    cand_row = _row(candidate, "candidate", CANDIDATE_ENGINE, case)
    if baseline["reference_profile"] != candidate["reference_profile"]:
        raise Refused("baseline and candidate were measured against different references")
    if baseline.get("tolerance_policy") != candidate.get("tolerance_policy"):
        raise Refused("baseline and candidate tolerance policies differ")
    base_metrics, cand_metrics = base_row["metrics"], cand_row["metrics"]
    if set(base_metrics) != set(cand_metrics):
        raise Refused("baseline and candidate measure different properties")
    integration = _integration(candidate)

    properties, differing, verdict_flips = [], [], []
    for name in sorted(base_metrics):
        b, c = base_metrics[name], cand_metrics[name]
        if b.get("units") != c.get("units") or b.get("tolerance_basis") != c.get("tolerance_basis"):
            raise Refused(f"property {name} uses a different measurement contract")
        if float(b["tolerance"]) != float(c["tolerance"]):
            raise Refused(f"property {name} is scored against a different tolerance")
        tolerance = float(b["tolerance"])
        # the distance the BOARD gives each property, not a second definition of it
        base_worst = base_row["verdict"]["properties"][name]
        cand_worst = cand_row["verdict"]["properties"][name]
        base_pass, cand_pass = base_worst <= 1.0, cand_worst <= 1.0
        row = {"property": name, "units": b.get("units"), "tolerance": tolerance,
               "baseline_value": float(b["value"]), "candidate_value": float(c["value"]),
               "difference": float(c["value"]) - float(b["value"]),
               "difference_in_tolerances": (float(c["value"]) - float(b["value"])) / tolerance,
               "baseline_worst": base_worst, "candidate_worst": cand_worst,
               "baseline_within_tolerance": base_pass,
               "candidate_within_tolerance": cand_pass,
               "verdict_agrees": base_pass == cand_pass}
        properties.append(row)
        if row["difference"] != 0.0:
            differing.append(name)
        if not row["verdict_agrees"]:
            verdict_flips.append(name)

    base_state, cand_state = base_row["verdict"]["state"], cand_row["verdict"]["state"]
    return {
        "schema": "drum-integrated-rtl-vs-fixed-model-v1",
        "case_id": candidate["case_id"],
        "subject": candidate.get("subject"),
        "reference_profile": candidate["reference_profile"],
        "baseline": {"engine": BASELINE_ENGINE,
                     "source_commit": baseline.get("source_commit"),
                     "scorecard_state": base_state,
                     "render_run": baseline.get("render_run")},
        "candidate": {"engine": CANDIDATE_ENGINE,
                      "source_commit": candidate.get("source_commit"),
                      "scorecard_state": cand_state,
                      "render_run": candidate.get("render_run"),
                      "simulator": integration.get("simulator"),
                      "rtl_build": integration.get("rtl_build"),
                      "decoded_i2s_sha256": (candidate.get("diagnostics") or {})
                                            .get("decoded_i2s_sha256")},
        "properties": properties,
        "case_verdicts_agree": base_state == cand_state,
        "properties_that_differ": sorted(differing),
        "properties_whose_verdict_flips": sorted(verdict_flips),
        "worst": {"baseline": max(p["baseline_worst"] for p in properties),
                  "candidate": max(p["candidate_worst"] for p in properties)},
        "attribution": _attribution(integration, differing),
    }


def compare_files(baseline_path: pathlib.Path, candidate_path: pathlib.Path) -> dict:
    try:
        baseline = json.loads(baseline_path.read_text())
        candidate = json.loads(candidate_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise Refused(f"cannot load a drum score record: {exc}") from exc
    report = compare_records(baseline, candidate)
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
                          for name in SOURCES},
        "run_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("baseline", help="the fixed-model twin this record replaces")
    ap.add_argument("candidate", help="the integrated-rtl record")
    ap.add_argument("--out", default="build/scorecard/drum-integrated-delta.json")
    a = ap.parse_args(argv)
    baseline, candidate = pathlib.Path(a.baseline), pathlib.Path(a.candidate)
    if not baseline.is_absolute(): baseline = ROOT / baseline
    if not candidate.is_absolute(): candidate = ROOT / candidate
    try:
        report = compare_files(baseline, candidate)
    except (OSError, ValueError, Refused, subprocess.CalledProcessError) as exc:
        print(f"compare_drum_i2s_candidate: REFUSED -- {exc}")
        return 2
    out = pathlib.Path(a.out)
    if not out.is_absolute(): out = ROOT / out
    if not out.resolve().is_relative_to(ROOT):
        print("compare_drum_i2s_candidate: REFUSED -- output must remain inside the repository")
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"report": str(out.relative_to(ROOT)),
                      "case_id": report["case_id"],
                      "case_verdicts_agree": report["case_verdicts_agree"],
                      "baseline_state": report["baseline"]["scorecard_state"],
                      "candidate_state": report["candidate"]["scorecard_state"],
                      "properties_that_differ": report["properties_that_differ"],
                      "properties_whose_verdict_flips": report["properties_whose_verdict_flips"],
                      "attributable_to": report["attribution"]["attributable_to"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
