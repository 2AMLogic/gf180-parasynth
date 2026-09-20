#!/usr/bin/env python3
"""Measure reconstructed 2x interpolation headroom on both M5A pulse widths."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import pathlib
import subprocess
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "tools"))
import mono_m5a_score as m5a
import voice_fx as vf
import measure_m5a_filter_oversample as experiment
import filter_rate_chain as frc


def _factory(preserve_headroom: bool, causal: bool = False):
    cfg = {**vf.LADDER_CFG, "oversample": 2}
    return lambda: vf.VoiceFx(
        oversample_2x=True, ladder_cfg=cfg, rate_converted_ladder=True,
        preserve_filter_headroom=preserve_headroom, causal_filter=causal)


def _measure_control_response() -> dict:
    """Measure output response to a known cutoff-coefficient step."""
    n, step = 512, 100
    x = np.full(n, 12000, dtype=np.int16)
    k = np.zeros(n, dtype=np.int64)
    g_before = np.full(n, 2000, dtype=np.int64)
    g_after = g_before.copy()
    g_after[step:] = 12000

    def render(g):
        chain = frc.RateConvertedLadder(2, {"oversample": 2},
                                        preserve_headroom=True, causal=True)
        return chain.process(x, None, 0.0, 0.5, g_q16=g, k_q14=k,
                             k=0, gain=1 << 16, ogain=1 << 16)

    difference = render(g_after).astype(np.float64) - render(g_before).astype(np.float64)
    changed = np.flatnonzero(difference)
    energy = difference * difference
    cumulative = np.cumsum(energy)
    if not len(changed) or cumulative[-1] <= 0:
        raise m5a.Refused("known cutoff step produced no measurable control response")
    quantile_frames = {
        str(q): int(np.searchsorted(cumulative, q * cumulative[-1]))
        for q in (0.1, 0.5, 0.9)
    }
    return {
        "control": "g_q16 cutoff coefficient step 2000->12000",
        "step_frame": step,
        "first_changed_output_frame": int(changed[0]),
        "first_changed_latency_frames": int(changed[0] - step),
        "squared_response_energy_quantiles_frames_from_start": quantile_frames,
        "sample_rate_hz": vf.SR,
        "method": "paired fixed-point ladder renders with identical constant input; cumulative output-difference squared energy",
    }


def _recompare_report(path: pathlib.Path) -> dict:
    """Re-run only the declared property-vector gate on saved measurements."""
    report = json.loads(path.read_text())
    if report.get("schema") != "m5a-filter-headroom-v1":
        raise m5a.Refused("recomparison requires an m5a-filter-headroom-v1 report")
    expected_pulses = {"pulse29", "pulse479"}
    if set(report.get("configurations", {})) != expected_pulses:
        raise m5a.Refused("recomparison requires both pulse-width rows")
    comparisons = {}
    for pulse, rows in report["configurations"].items():
        if set(rows) != {"clamped", "headroom", "causal_headroom"}:
            raise m5a.Refused(f"recomparison requires all three {pulse} configurations")
        comparisons[pulse] = {
            "headroom_vs_clamped_offline": experiment._compare_incremental(
                rows["clamped"], rows["headroom"]),
            "causal_vs_headroom_offline": experiment._compare_incremental(
                rows["headroom"], rows["causal_headroom"]),
            "causal_vs_clamped_offline": experiment._compare_incremental(
                rows["clamped"], rows["causal_headroom"]),
        }
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
                                capture_output=True, text=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT,
                                    check=True, capture_output=True, text=True).stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        commit, dirty = "unknown", True
    report["comparisons"] = comparisons
    report["comparison_reanalysis"] = {
        "source_commit": commit,
        "source_dirty": dirty,
        "source_sha256": hashlib.sha256(
            (ROOT / "tools/measure_m5a_filter_oversample.py").read_bytes()).hexdigest(),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "meaning": "recomputed decision only; raw M5A renders and event measurements remain from the per-configuration provenance in the report",
    }
    return report


def run(out_dir: pathlib.Path, reuse_offline_report: pathlib.Path | None = None,
        reuse_causal_report: pathlib.Path | None = None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = {"pulse29": {}, "pulse479": {}}
    reused_sources = {}
    reference_sha256 = json.loads(m5a.MANIFEST.read_text())["audio"]["sha256"]

    def load_reuse(path, labels):
        previous = json.loads(path.read_text())
        if (previous.get("schema") != "m5a-filter-headroom-v1"
                or previous.get("reference_sha256") != reference_sha256
                or set(previous.get("configurations", {})) != set(rows)):
            raise m5a.Refused("reuse report schema, reference, or pulse set does not match")
        for pulse in rows:
            for mode in labels:
                row = previous["configurations"][pulse].get(mode)
                if row is None or set(row.get("metrics", {})) != set(m5a.TOLERANCES):
                    raise m5a.Refused(f"reuse report lacks complete {pulse}/{mode} evidence")
                rows[pulse][mode] = row
        reused_sources["+".join(labels)] = {
            "report": str(path),
            "source_commit": previous.get("source_commit"),
            "source_dirty": previous.get("source_dirty"),
            "source_sha256": previous.get("source_sha256"),
            "note": "saved complete-phrase measurements reused without rerendering",
        }
    if reuse_offline_report is not None:
        load_reuse(reuse_offline_report, ("clamped", "headroom"))
    if reuse_causal_report is not None:
        load_reuse(reuse_causal_report, ("causal_headroom",))
    for pulse in ("pulse29", "pulse479"):
        for label, preserve, causal in (("clamped", False, False),
                                        ("headroom", True, False),
                                        ("causal_headroom", True, True)):
            if label in rows[pulse]:
                continue
            rows[pulse][label] = m5a.measure(
                pulse_shape=pulse, voice_factory=_factory(preserve, causal),
                model_label=f"reconstructed_2x_{label}",
                output_path=out_dir / f"M5A-reconstructed-2x-{label}-{pulse}.wav")

    comparisons = {}
    for pulse, pair in rows.items():
        comparisons[pulse] = {
            "headroom_vs_clamped_offline": experiment._compare_incremental(
                pair["clamped"], pair["headroom"]),
            "causal_vs_headroom_offline": experiment._compare_incremental(
                pair["headroom"], pair["causal_headroom"]),
            "causal_vs_clamped_offline": experiment._compare_incremental(
                pair["clamped"], pair["causal_headroom"]),
        }

    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
                                capture_output=True, text=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT,
                                    check=True, capture_output=True, text=True).stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        commit, dirty = "unknown", True
    ref = json.loads(m5a.MANIFEST.read_text())
    return {
        "experiment": "M5A reconstructed 2x input interpolation headroom",
        "schema": "m5a-filter-headroom-v1",
        "status": "model_measurement_only",
        "wrong_then_right": [
            {
                "attempt": "initial six-row report aggregation",
                "wrong": "all renders completed, but the report exited before write because the control-latency instrument lacked its NumPy import",
                "right": "added NumPy import and an injected cutoff-step test; reused the valid causal renders with explicit provenance, then rerendered offline rows from a clean commit",
            }
        ],
        "source_commit": commit,
        "source_dirty": dirty,
        "reference_sha256": ref["audio"]["sha256"],
        "manifest_sha256": hashlib.sha256(m5a.MANIFEST.read_bytes()).hexdigest(),
        "controls_held_fixed": ["M5A timeline", "2x oscillator path", "2x ladder path",
                                 "rate-matched ROMs", "input/output Kaiser filters",
                                 "pulse width other than the explicit model challenger"],
        "headroom": {
            "fixed_point_units": "Q1.15-scaled signed int32 samples into LadderFx",
            "legacy": "round then saturate reconstructed samples to int16 before ladder",
            "candidate": "retain reconstruction overshoot before nonlinear ladder; output decimator still saturates to ladder output width",
        },
        "causal_converter": {
            "coefficient_format": "Q2.30 Kaiser FIR; int64 multiply-accumulate",
            "chunk_state": "interpolator and decimator histories plus decimation phase persist across process calls",
            "declared_latency_frames": 20,
            "latency_seconds_at_48khz": 20 / vf.SR,
            "latency_samples": 20,
            "verified_in_model": True,
            "rtl_verified": False,
            "control_response": _measure_control_response(),
        },
        "comparisons": comparisons,
        "configurations": rows,
        "reused_measurements": reused_sources,
        "source_sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in ("model/filter_rate_chain.py", "model/voice_fx.py",
                         "tools/mono_m5a_score.py",
                         "tools/measure_m5a_filter_headroom.py")
        },
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "decision": {
            "causal_or_stateful": True,
            "rtl_verified": False,
            "meaning": "candidate is accepted only as an incremental model improvement if both pulse widths have a complete non-regressing property/partial vector and at least one measured improvement; strict case_passes remains separate",
        },
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="build/scorecard/m5a-filter-headroom-v1.json")
    parser.add_argument("--reuse-offline-report", type=pathlib.Path)
    parser.add_argument("--reuse-causal-report", type=pathlib.Path)
    parser.add_argument("--recompare-report", type=pathlib.Path,
                        help="recompute acceptance decisions from a complete saved measurement without rerendering")
    args = parser.parse_args(argv)
    if args.recompare_report is not None:
        out = args.recompare_report
        report = _recompare_report(out)
    else:
        report = run(ROOT / "build/scorecard/m5a-filter-headroom-audio",
                     args.reuse_offline_report, args.reuse_causal_report)
        out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    comparison_summary = {
        pulse: {
            comparison: {
                "accepts_incremental_improvement": row["accepts_incremental_improvement"],
                "case_passes": row["case_passes"],
                "improved_components": len(row["improved_components"]),
                "regressed_components": len(row["regressed_components"]),
            }
            for comparison, row in comparisons.items()
        }
        for pulse, comparisons in report["comparisons"].items()
    }
    print(json.dumps({"report": str(out.resolve().relative_to(ROOT.resolve())),
                      "source_commit": report["source_commit"],
                      "source_dirty": report["source_dirty"],
                      "comparisons": comparison_summary,
                      "causal_converter": report["causal_converter"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
