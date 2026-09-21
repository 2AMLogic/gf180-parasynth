#!/usr/bin/env python3
"""Score a saw-only cutoff challenger over the complete frozen M5A phrase.

Both renders use the selected causal, headroom-preserving 2x filter path and
pulse479. Only the saw-segment cutoff differs. The seven-property vector and
every common partial are compared with the repository's incremental gate.
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
import measure_m5a_filter_oversample as filter_gate
import mono_m5a_score as m5a
import score_m5a_i2s
import voice_fx as vf


def measure(cutoff_hz: int, saw_gain_correction_db: float = 0.0) -> dict:
    if isinstance(cutoff_hz, bool) or not isinstance(cutoff_hz, int):
        raise m5a.Refused("saw cutoff must be an integer Hz value")
    if not vf.CUT_MIN <= cutoff_hz <= vf.CUT_MAX:
        raise m5a.Refused(f"saw cutoff {cutoff_hz} outside [{vf.CUT_MIN}, {vf.CUT_MAX}]")
    if (isinstance(saw_gain_correction_db, bool)
            or not isinstance(saw_gain_correction_db, (int, float))
            or not math.isfinite(saw_gain_correction_db)
            or not -12.0 <= saw_gain_correction_db <= 12.0):
        raise m5a.Refused("saw gain correction must be finite and within [-12, 12] dB")

    voice_factory = score_m5a_i2s._candidate_factory
    baseline = m5a.measure(
        pulse_shape="pulse479", engine="selected", voice_factory=voice_factory,
        saw_cutoff_override=int(round(json.loads(m5a.MANIFEST.read_text())
                                      ["patch"]["cutoff_measurement"]["f0_hz"])),
        saw_volume_correction_db=0.0,
        model_label="selected-filter-2x-saw-cutoff-baseline",
        output_path=ROOT / "build/scorecard/M5A-saw-cutoff-baseline.wav")
    candidate = m5a.measure(
        pulse_shape="pulse479", engine="selected", voice_factory=voice_factory,
        model_label=f"selected-filter-2x-saw-cutoff-{cutoff_hz}",
        output_path=ROOT / "build/scorecard/M5A-saw-cutoff-candidate.wav",
        saw_cutoff_override=cutoff_hz,
        saw_volume_correction_db=saw_gain_correction_db)
    comparison = filter_gate._compare_incremental(baseline, candidate)

    source_files = (
        "tools/measure_m5a_saw_cutoff.py", "tools/measure_m5a_filter_oversample.py",
        "tools/mono_m5a_score.py", "tools/score_m5a_i2s.py",
        "model/filter_rate_chain.py", "model/voice_fx.py")
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                            check=True, capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                                check=True, capture_output=True, text=True).stdout.strip())
    return {
        "schema": "m5a-saw-cutoff-candidate-v1", "valid": True,
        "reference_sha256": baseline["reference_sha256"],
        "manifest_sha256": baseline["manifest_sha256"],
        "filter_config": "selected production candidate: causal reconstructed 2x ladder with headroom preserved",
        "pulse_shape": "pulse479 (held fixed)",
        "cutoff_hz": {"baseline": baseline["cutoff_calibration"]["f0_hz"],
                      "saw_candidate": cutoff_hz,
                      "pulse_candidate": "unchanged from frozen manifest"},
        "saw_gain_correction_db": {"baseline": 0.0,
                                   "candidate": float(saw_gain_correction_db)},
        "comparison": comparison,
        "baseline": {"model_configuration": baseline["model_configuration"],
                     "metrics": baseline["metrics"],
                     "event_diagnostics": baseline["event_diagnostics"],
                     "audio": baseline["audio"]},
        "candidate": {"model_configuration": candidate["model_configuration"],
                      "metrics": candidate["metrics"],
                      "event_diagnostics": candidate["event_diagnostics"],
                      "audio": candidate["audio"]},
        "provenance": {
            "source_commit": commit, "source_dirty": dirty,
            "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                              for name in source_files},
            "run_at_utc": datetime.now(timezone.utc).isoformat(),
        },
        "decision": "measured model challenger only; no RTL promotion",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cutoff", type=int, default=20_000,
                        help="saw-only cutoff challenger in Hz (default: 20000)")
    parser.add_argument("--gain-correction-db", type=float, default=0.0,
                        help="fixed saw-only final-volume correction in dB (default: 0)")
    parser.add_argument("--out", default="build/scorecard/m5a-saw-cutoff-candidate.json")
    args = parser.parse_args(argv)
    try:
        report = measure(args.cutoff, args.gain_correction_db)
    except (OSError, ValueError, m5a.Refused) as exc:
        print(f"measure_m5a_saw_cutoff: REFUSED -- {exc}")
        return 2
    out = pathlib.Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    if not out.resolve().is_relative_to(ROOT):
        print("measure_m5a_saw_cutoff: REFUSED -- output must stay inside the repository")
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"report": str(out.relative_to(ROOT)),
                      "source_commit": report["provenance"]["source_commit"],
                      "case_passes": report["comparison"]["case_passes"],
                      "accepts_incremental_improvement": report["comparison"]["accepts_incremental_improvement"],
                      "improved_components": len(report["comparison"]["improved_components"]),
                      "regressed_components": len(report["comparison"]["regressed_components"])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
