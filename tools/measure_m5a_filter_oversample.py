#!/usr/bin/env python3
"""Compare production 2x, reconstructed 2x, and reconstructed 4x M5A renders.

All configurations use the same frozen reference, oscillator mode, patch,
controls, and seven-property judge. The two reconstructed paths use rate-matched
g/k ROMs, Kaiser input reconstruction, and Kaiser output decimation. `pulse479`
is a model-only challenger with no RTL waveform encoding.
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

import numpy as np
import scipy

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "tools"))
import mono_m5a_score as m5a
import voice_fx as vf
import fixed
import filter_rate_chain as frc


# These are non-regression deadbands, not M5A pass limits. They suppress
# quantization-scale churn while preserving the strict tolerances below.
METRIC_NONREGRESSION_DEADBANDS = {
    "Pitch": 0.01,                 # cents
    "Harmonic shape": 0.01,       # dB
    "Foldback energy": 0.01,      # dB
    "Envelope attack": 5.0,       # ms; RMS envelope analysis window
    "Envelope release": 5.0,      # ms; RMS envelope analysis window
    "Gain": 0.01,                 # dB
    "Clipping": 0.01,             # percent of samples
}
PARTIAL_NONREGRESSION_DEADBAND_DB = 0.01


def _voice_factory(mode: str):
    if mode == "production_2x_sample_hold":
        return lambda: vf.VoiceFx(oversample_2x=True)
    factor = {"reconstructed_2x": 2, "reconstructed_4x": 4}.get(mode)
    if factor is None:
        raise ValueError(f"unknown ladder mode: {mode}")
    cfg = {**vf.LADDER_CFG, "oversample": factor}
    return lambda: vf.VoiceFx(oversample_2x=True, ladder_cfg=cfg,
                              rate_converted_ladder=True)


def _error_magnitude(metric: dict) -> float:
    if metric["units"] == "cents":
        return abs(float(metric["error"]))
    return abs(float(metric["error"]))


def _case_passes(row: dict) -> bool:
    """Strict case gate: every measured M5A property meets its fixed limit."""
    if set(row.get("metrics", {})) != set(m5a.TOLERANCES):
        raise m5a.Refused("case gate requires a complete seven-property score")
    return all(_error_magnitude(row["metrics"][name])
               <= float(m5a.TOLERANCES[name][0]) for name in m5a.TOLERANCES)


def _compare_incremental(baseline: dict, candidate: dict) -> dict:
    """Accept a genuine vector improvement without requiring a passing case.

    Every scalar property and every measured partial must be present and must
    not regress. At least one component must improve beyond report precision.
    The independent strict case gate remains available as ``case_passes``.
    """
    expected = set(m5a.TOLERANCES)
    for label, row in (("baseline", baseline), ("candidate", candidate)):
        if set(row.get("metrics", {})) != expected:
            raise m5a.Refused(f"{label} has an incomplete seven-property score")
        events = row.get("event_diagnostics")
        if not events:
            raise m5a.Refused(f"{label} has no executed event evidence")

    components = {}
    for name in m5a.TOLERANCES:
        b = _error_magnitude(baseline["metrics"][name])
        c = _error_magnitude(candidate["metrics"][name])
        components[f"metric:{name}"] = {"baseline_error": b, "candidate_error": c}

    before_events, after_events = baseline["event_diagnostics"], candidate["event_diagnostics"]
    if len(before_events) != len(after_events):
        raise m5a.Refused("baseline and candidate event evidence differs")
    for index, (before, after) in enumerate(zip(before_events, after_events)):
        identity = (before.get("wave"), before.get("midi"))
        if identity != (after.get("wave"), after.get("midi")):
            raise m5a.Refused("baseline and candidate event order differs")
        bpart = before.get("harmonic_error_db_model_minus_reference")
        cpart = after.get("harmonic_error_db_model_minus_reference")
        if not isinstance(bpart, dict) or not bpart or set(bpart) != set(cpart or {}):
            raise m5a.Refused("baseline/candidate partial evidence is incomplete or differs")
        for partial in sorted(bpart):
            components[f"partial:{index}:{identity[0]}:{identity[1]}:{partial}"] = {
                "baseline_error": abs(float(bpart[partial])),
                "candidate_error": abs(float(cpart[partial])),
            }

    regressions, improvements = [], []
    for name, pair in components.items():
        if name.startswith("partial:"):
            deadband = PARTIAL_NONREGRESSION_DEADBAND_DB
        else:
            metric_name = name.removeprefix("metric:")
            deadband = METRIC_NONREGRESSION_DEADBANDS[metric_name]
        delta = pair["candidate_error"] - pair["baseline_error"]
        pair["delta_error"] = round(delta, 6)
        pair["non_regression_deadband"] = deadband
        if delta > deadband:
            regressions.append(name)
        elif delta < -deadband:
            improvements.append(name)
    return {
        "accepts_incremental_improvement": bool(improvements) and not regressions,
        "case_passes": _case_passes(candidate),
        "improved_components": improvements,
        "regressed_components": regressions,
        "non_regression_deadbands": {
            "metrics": METRIC_NONREGRESSION_DEADBANDS,
            "per_partial_db": PARTIAL_NONREGRESSION_DEADBAND_DB,
        },
        "components": components,
        "meaning": "incremental acceptance permits unchanged failing properties; case_passes requires every fixed limit",
    }


def _model_screen(configurations: dict) -> dict:
    checks = {}
    for pulse in ("pulse29", "pulse479"):
        rows = {mode: configurations[mode][pulse] for mode in
                ("production_2x_sample_hold", "reconstructed_2x", "reconstructed_4x")}
        expected = set(m5a.TOLERANCES)
        for mode, row in rows.items():
            if set(row.get("metrics", {})) != expected:
                raise m5a.Refused(f"{mode}/{pulse} has an incomplete seven-property score")
            if not row.get("event_diagnostics") or any(
                    "stages" not in event for event in row["event_diagnostics"]):
                raise m5a.Refused(f"{mode}/{pulse} has no executed stage/event evidence")
        prod = rows["production_2x_sample_hold"]["metrics"]
        matched = rows["reconstructed_2x"]["metrics"]
        candidate = rows["reconstructed_4x"]["metrics"]
        target = {}
        for name in ("Harmonic shape", "Foldback energy"):
            c = _error_magnitude(candidate[name])
            target[name] = {
                "4x_better_than_production_2x": c < _error_magnitude(prod[name]),
                "4x_better_than_reconstructed_2x": c < _error_magnitude(matched[name]),
            }
        preservation = {}
        for name, metric in candidate.items():
            if name in ("Harmonic shape", "Foldback energy"):
                continue
            preservation[name] = {
                "within_existing_limit": _error_magnitude(metric) <= float(metric["tolerance"]),
                "error_magnitude": round(_error_magnitude(metric), 6),
                "limit": metric["tolerance"],
            }
        checks[pulse] = {"target_improvements": target,
                         "other_properties": preservation,
                         "passes_model_screen": (
                             all(all(v.values()) for v in target.values())
                             and all(v["within_existing_limit"] for v in preservation.values()))}
    return {"per_pulse": checks,
            "passes_model_screen_for_both_pulse_widths": all(
                item["passes_model_screen"] for item in checks.values()),
            "meaning": "screen only; passing justifies a separately verified RTL prototype, not release"}


def _linear_response(mode: str, cutoff: int = 8_000) -> dict:
    """Measure the fixed ladder's low-level sine response and its analytic poles."""
    factor = 2 if mode == "production_2x_sample_hold" else (2 if mode == "reconstructed_2x" else 4)
    cfg = {**vf.LADDER_CFG, "oversample": factor}
    grom = vf.make_g_rom(vf.GROM_BITS, factor)
    krom = vf.make_k_rom(vf.KROM_BITS, vf.GROM_BITS, factor)
    n = 48_000
    start = 24_000
    drive, res, amplitude = 0.5, 0.0, 0.02
    reg_ladder = fixed.LadderFx(**cfg)
    k_reg, gain_reg, ogain_reg = reg_ladder.regs(res, drive)
    cut = np.full(n, cutoff, dtype=np.int64)
    g = vf.g_from_cut(cut, grom, vf.GROM_BITS)
    kc = vf.kc_from_cut(cut, krom, vf.KROM_BITS)
    k_eff = vf.k_effective(k_reg, kc)
    frequencies = (150, 1_000, 2_000, 4_000, 8_000, 12_000, 16_000)
    gains = {}
    for frequency in frequencies:
        t = np.arange(n, dtype=np.float64) / vf.SR
        x = np.clip(np.rint(amplitude * 32768.0 * np.sin(2.0 * np.pi * frequency * t)),
                    -32768, 32767).astype(np.int16)
        if mode == "production_2x_sample_hold":
            lad = fixed.LadderFx(**cfg)
        else:
            lad = frc.RateConvertedLadder(factor, cfg)
        y = lad.process(x, None, res, drive, g_q16=g, k=k_reg, gain=gain_reg,
                        ogain=ogain_reg, k_q14=k_eff)
        # All requested frequencies have an integer number of cycles in this
        # 0.5 s analysis window. Direct projection therefore has a known answer
        # and does not depend on an FFT peak-picking convention.
        indices = np.arange(start, n, dtype=np.float64)
        phase = np.exp(-2j * np.pi * frequency * indices / vf.SR)
        out_amp = 2.0 * abs(np.dot(y[start:].astype(np.float64) / 32768.0, phase)) / (n - start)
        in_amp = 2.0 * abs(np.dot(x[start:].astype(np.float64) / 32768.0, phase)) / (n - start)
        gains[str(frequency)] = round(20.0 * math.log10(max(out_amp / in_amp, 1e-15)), 5)
    passband = gains["150"]
    observed = {key: round(value - passband, 5) for key, value in gains.items()}
    g0 = int(vf.g_from_cut(np.array([cutoff]), grom, vf.GROM_BITS)[0]) / 65536.0
    analytic = {}
    for frequency in frequencies:
        w = 2.0 * math.pi * frequency / (vf.SR * factor)
        h1 = g0 / (1.0 - (1.0 - g0) * complex(math.cos(w), -math.sin(w)))
        analytic[str(frequency)] = 20.0 * math.log10(abs(h1 ** 4))
    return {"factor": factor, "commanded_cutoff_hz": cutoff,
            "drive": drive, "input_peak_fs": amplitude,
            "measured_absolute_gain_db": gains,
            "measured_response_relative_to_150_hz_db": observed,
            "analytic_four_pole_response_relative_to_dc_db": {
                key: round(value - analytic["150"], 5) for key, value in analytic.items()},
            "measured_at_cutoff_db": observed[str(cutoff)] if str(cutoff) in observed else None,
            "scope": "small-signal filter response, separate from the Mini V3 sound-fidelity score"}


def run(out_dir: pathlib.Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    configurations = {}
    for mode in ("production_2x_sample_hold", "reconstructed_2x", "reconstructed_4x"):
        configurations[mode] = {}
        for pulse in ("pulse29", "pulse479"):
            result = m5a.measure(
                pulse_shape=pulse,
                voice_factory=_voice_factory(mode),
                model_label=mode,
                output_path=out_dir / f"M5A-{mode}-{pulse}.wav",
            )
            configurations[mode][pulse] = result

    comparisons = {}
    for pulse in ("pulse29", "pulse479"):
        base = configurations["production_2x_sample_hold"][pulse]["metrics"]
        matched_2x = configurations["reconstructed_2x"][pulse]["metrics"]
        candidate = configurations["reconstructed_4x"][pulse]["metrics"]
        comparisons[pulse] = {}
        for metric in base:
            comparisons[pulse][metric] = {
                "production_2x_error_magnitude": round(_error_magnitude(base[metric]), 6),
                "reconstructed_2x_error_magnitude": round(_error_magnitude(matched_2x[metric]), 6),
                "reconstructed_4x_error_magnitude": round(_error_magnitude(candidate[metric]), 6),
                "reconstructed_4x_minus_reconstructed_2x": round(
                    _error_magnitude(candidate[metric]) - _error_magnitude(matched_2x[metric]), 6),
                "units": candidate[metric]["units"],
                "limit": candidate[metric]["tolerance"],
                "4x_within_existing_limit": _error_magnitude(candidate[metric]) <= candidate[metric]["tolerance"],
            }

    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
                                capture_output=True, text=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                                    check=True, capture_output=True, text=True).stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        commit, dirty = "unknown", True
    ref = json.loads(m5a.MANIFEST.read_text())
    report = {
        "experiment": "M5A ladder rate conversion 2x versus 4x",
        "schema": "m5a-filter-rate-v1",
        "status": "model_measurement_only",
        "source_commit": commit,
        "source_dirty": dirty,
        "reference_sha256": ref["audio"]["sha256"],
        "manifest_sha256": hashlib.sha256(m5a.MANIFEST.read_bytes()).hexdigest(),
        "reference_identity": "Mini V3 3.12.0.3422 software-synth recording; not physical hardware",
        "controls_held_fixed": ["M5A timeline", "cutoff and envelopes", "drive", "oscillator 2x mode", "voice gains"],
        "rate_conversion": {
            "scipy_version": scipy.__version__,
            "input": "scipy.signal.resample_poly, Kaiser beta 8.6, integer Q1.15 reconstruction; default taps: 41 (2x), 81 (4x)",
            "output": "scipy.signal.resample_poly, Kaiser beta 8.6, anti-alias decimation to 48 kHz; default taps: 41 (2x), 81 (4x)",
            "coefficients": "g/k ROMs generated at the actual ladder factor; per-frame ROM values held across reconstructed subframes",
            "ladder_arithmetic": "fixed-point LadderFx, one update per internal-rate sample",
        },
        "filter_response": {
            mode: _linear_response(mode) for mode in
            ("production_2x_sample_hold", "reconstructed_2x", "reconstructed_4x")
        },
        "pulse479_scope": "model-only challenger; deliberately absent from WAVE_CODE and unsupported by RTL",
        "wrong_then_right": [
            "Initial red harness run refused during collection because model/filter_rate_chain.py did not exist; after implementation all five ground-truth tests passed."
        ],
        "comparisons": comparisons,
        "model_screen": _model_screen(configurations),
        "source_sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in ("model/filter_rate_chain.py", "model/voice_fx.py",
                         "tools/mono_m5a_score.py", "tools/measure_m5a_filter_oversample.py")
        },
        "configurations": configurations,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "decision": {
            "rtl_implementation_authorized_by_this_report": False,
            "reason": "model measurements are not RTL evidence; any 4x candidate must improve harmonic shape and foldback for both pulse widths while remaining within gain, envelope, pitch, and clipping limits before RTL work",
        },
    }
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="build/scorecard/m5a-filter-rate-v1.json")
    args = parser.parse_args(argv)
    report = run(ROOT / "build/scorecard/m5a-filter-rate-audio")
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"report": str(out.relative_to(ROOT)),
                      "source_commit": report["source_commit"],
                      "source_dirty": report["source_dirty"],
                      "comparisons": report["comparisons"],
                      "decision": report["decision"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
