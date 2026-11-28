#!/usr/bin/env python3
"""Measure reconstructed 2x interpolation headroom on both M5A pulse widths."""
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

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "tools"))
import mono_m5a_score as m5a
import refusal
from exit_contract import REFUSED
import voice_fx as vf
import measure_m5a_filter_oversample as experiment
import filter_rate_chain as frc

_SOURCE_FILES = ("model/filter_rate_chain.py", "model/voice_fx.py",
                 "tools/mono_m5a_score.py", "tools/measure_m5a_filter_headroom.py",
                 "tools/measure_m5a_filter_oversample.py")


def _source_dirty() -> bool:
    """Report only edits to code that produces or judges the measurements."""
    result = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", *_SOURCE_FILES],
                            cwd=ROOT, check=False)
    if result.returncode not in (0, 1):
        raise m5a.Refused("could not establish measurement-source cleanliness")
    return result.returncode == 1


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


def require_json_number(value, label: str, *, refused: type[Exception]) -> float:
    """`value` as a float when it is a finite JSON number; otherwise raise.

    A JSON number is an int or a float. `bool` is an `int` subclass in Python
    and `float()` accepts strings, so a raw coercion reads `false` as a
    perfect 0.0 and `"0.5"` as half; it raises on `null` and on an integer
    beyond float range, and accepts a NaN or infinity without a word.
    `refused` is the caller's own `Refused` class, so the name at its catch
    sites does not change (#163). The same function is
    `delta_report.require_json_number` in #1638; import that once it lands.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise refused(f"{label} must be a JSON number, not {value!r}")
    try:
        number = float(value)
    except OverflowError:
        number = math.inf if value > 0 else -math.inf
    if not math.isfinite(number):
        raise refused(f"{label} must be finite, not {number!r}")
    return number


def _gate_row(row: dict, label: str) -> None:
    """REFUSE a saved measurement row unless every number `_compare_incremental`
    reads from it is a finite JSON number (#1687).

    The comparator coerces on faith: `abs(float(...))` on each
    `metrics.*.error` and on every harmonic partial of every event. From JSON
    that coercion raises for `null` and for an integer beyond float range --
    a traceback, exit 1, which tools/exit_contract.py defines as FAIL -- and
    accepts everything else: `true` reads as 1.0, `"1.5"` as 1.5, and a NaN
    partial compares unequal to itself, so it lands in neither
    `regressed_components` nor `improved_components` and is reported as no
    change (#134's class). Rows measured in-process by `m5a.measure` never
    pass through JSON, which is why the gate sits at the load boundary and
    not in the comparator they share."""
    if not isinstance(row, dict):
        raise m5a.Refused(f"{label} is not an object")
    metrics = row.get("metrics")
    if not isinstance(metrics, dict):
        raise m5a.Refused(f"{label} metrics is missing or not an object")
    for name, metric in metrics.items():
        if not isinstance(metric, dict):
            raise m5a.Refused(f"{label} metrics.{name} is not an object")
        require_json_number(metric.get("error"), f"{label} metrics.{name}.error",
                            refused=m5a.Refused)
    events = row.get("event_diagnostics")
    if not isinstance(events, list):
        raise m5a.Refused(f"{label} event_diagnostics is missing or not a list")
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            raise m5a.Refused(f"{label} event {index} is not an object")
        partials = event.get("harmonic_error_db_model_minus_reference")
        if not isinstance(partials, dict):
            raise m5a.Refused(f"{label} event {index} harmonic partial evidence is missing "
                              "or not an object")
        where = f"{label} event {index} {event.get('wave')}/{event.get('midi')} harmonic partial"
        for partial, value in partials.items():
            require_json_number(value, f"{where} {partial}", refused=m5a.Refused)


def _recompare_report(path: pathlib.Path) -> dict:
    """Re-run only the declared property-vector gate on saved measurements."""
    report = json.loads(path.read_text())
    if report.get("schema") != "m5a-filter-headroom-v1":
        raise m5a.Refused("recomparison requires an m5a-filter-headroom-v1 report")
    expected_pulses = {"pulse29", "pulse479"}
    if set(report.get("configurations", {})) != expected_pulses:
        raise m5a.Refused("recomparison requires both pulse-width rows")
    for pulse, rows in report["configurations"].items():
        if set(rows) != {"clamped", "headroom", "causal_headroom"}:
            raise m5a.Refused(f"recomparison requires all three {pulse} configurations")
        for mode, row in rows.items():
            _gate_row(row, f"{pulse}/{mode}")
    comparisons = {}
    for pulse, rows in report["configurations"].items():
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
        dirty = _source_dirty()
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
                _gate_row(row, f"reuse {pulse}/{mode}")
                rows[pulse][mode] = row
        reused_sources["+".join(labels)] = {
            "reused_rows": list(labels),
            "source_commit": previous.get("source_commit"),
            "source_dirty": previous.get("source_dirty"),
            "source_sha256": previous.get("source_sha256"),
            "note": "saved complete-phrase measurement rows reused without rerendering; row data is embedded in this report",
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
                pulse_shape=pulse, engine="legacy",
                saw_cutoff_override=int(round(m5a.json.loads(m5a.MANIFEST.read_text())
                                               ["patch"]["cutoff_measurement"]["f0_hz"])),
                saw_volume_correction_db=0.0,
                voice_factory=_factory(preserve, causal),
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
        dirty = _source_dirty()
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
            for name in _SOURCE_FILES
        },
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "decision": {
            "causal_or_stateful": True,
            "rtl_verified": False,
            "meaning": "candidate is accepted only as an incremental model improvement if both pulse widths have a complete non-regressing property/partial vector and at least one measured improvement; strict case_passes remains separate",
        },
    }


def _summary(report: dict, out: pathlib.Path) -> dict:
    """What `main` prints about a report, built before the report is written.

    A report that cannot be summarised is then REFUSED with its file as it
    was. Built after the write, as it was until #1687, it rewrote the file
    and exited 1. `out` has already been confined to the repository."""
    for key in ("source_commit", "source_dirty", "comparisons", "causal_converter"):
        if key not in report:
            raise m5a.Refused(f"report lacks {key}, so there is nothing to summarise")
    return {
        "report": str(out.resolve().relative_to(ROOT.resolve())),
        "source_commit": report["source_commit"],
        "source_dirty": report["source_dirty"],
        "comparisons": {
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
        },
        "causal_converter": report["causal_converter"],
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
        out, option = args.recompare_report, "--recompare-report"
    else:
        out, option = ROOT / args.out, "--out"
    try:
        if not out.resolve().is_relative_to(ROOT.resolve()):
            raise m5a.Refused(f"{option} output must remain inside the repository, not {out}")
        if args.recompare_report is not None:
            report = _recompare_report(out)
        else:
            report = run(ROOT / "build/scorecard/m5a-filter-headroom-audio",
                         args.reuse_offline_report, args.reuse_causal_report)
        summary = _summary(report, out)
    except refusal.Refused as exc:
        print(f"REFUSED: {exc}")
        return REFUSED
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
