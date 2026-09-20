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


def run(out_dir: pathlib.Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = {}
    for pulse in ("pulse29", "pulse479"):
        rows[pulse] = {}
        for label, preserve, causal in (("clamped", False, False),
                                        ("headroom", True, False),
                                        ("causal_headroom", True, True)):
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
        }

    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
                                capture_output=True, text=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                                    check=True, capture_output=True, text=True).stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        commit, dirty = "unknown", True
    ref = json.loads(m5a.MANIFEST.read_text())
    return {
        "experiment": "M5A reconstructed 2x input interpolation headroom",
        "schema": "m5a-filter-headroom-v1",
        "status": "model_measurement_only",
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
    args = parser.parse_args(argv)
    report = run(ROOT / "build/scorecard/m5a-filter-headroom-audio")
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"report": str(out.relative_to(ROOT)),
                      "source_commit": report["source_commit"],
                      "source_dirty": report["source_dirty"],
                      "comparisons": report["comparisons"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
