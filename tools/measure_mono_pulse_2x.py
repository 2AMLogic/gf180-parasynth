#!/usr/bin/env python3
"""Fixed-duty pulse 2x experiment against complete frozen M5A/M5B phrases.

The selected saw/filter/patch/envelope settings remain fixed. The 2x pulse
uses the saw decimator's 0.85 headroom gain; a separate gain-only control
distinguishes reduced drive from the rate change. Nothing here selects RTL.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "model"), str(ROOT / "tools")]
import voice_fx as vf
import mono_m5a_score as score
from measure_m5a_filter_oversample import _compare_incremental
from compare_m5a_i2s_candidate import _event_errors

SOURCES = ("tools/measure_mono_pulse_2x.py", "model/voice_fx.py",
           "tools/mono_m5a_score.py", "model/audio_measure.py",
           "model/filter_rate_chain.py", "model/fixed.py",
           "tools/measure_m5a_filter_oversample.py",
           "tools/compare_m5a_i2s_candidate.py")


class GainOnlyVoice(vf.VoiceFx):
    def reset(self):
        super().reset()
        for osc in self.oscs:
            original = osc.render

            def render(n, inc, *, _osc=osc, _original=original):
                pcm = _original(n, inc)
                if _osc.shape in vf.TWO_EDGE:
                    pcm = (np.asarray(pcm, dtype=np.int64) * vf._OS2_SUBSTEP_GAIN_Q15) >> 15
                return pcm

            osc.render = render


def factory(mode):
    if mode not in ("baseline", "pulse2x", "gain_only", "disabled_control"):
        raise ValueError(f"unknown pulse experiment mode {mode!r}")
    cls = GainOnlyVoice if mode == "gain_only" else vf.VoiceFx
    return cls(oversample_2x=True, rate_converted_ladder=True,
               preserve_filter_headroom=True, causal_filter=True,
               pulse479_filter_candidate=True,
               oversample_pulse_2x=mode == "pulse2x")


def compare(baseline, candidate):
    for key in ("analysis_version", "reference_sha256", "manifest_sha256"):
        if not baseline.get(key) or baseline[key] != candidate.get(key):
            raise score.Refused(f"pulse comparison differs in {key}")
    for row in (baseline, candidate):
        if any(m.get("valid") is not True for m in row["metrics"].values()):
            raise score.Refused("pulse comparison has an invalid metric")
    result = _compare_incremental(baseline, candidate)
    lost = []
    for before, after in zip(baseline["event_diagnostics"], candidate["event_diagnostics"]):
        bp, cp = _event_errors(before, "baseline"), _event_errors(after, "candidate")
        for name, value in bp.items():
            if value["error"] <= value["limit"] < cp[name]["error"]:
                lost.append({"wave": before["wave"], "midi": before["midi"],
                             "property": name, "before": value["error"],
                             "after": cp[name]["error"], "limit": value["limit"]})
    result["lost_per_note_passes"] = lost
    result["accepts_incremental_improvement"] &= not lost
    return result


def measure(case_id, directory):
    directory.mkdir(parents=True, exist_ok=True)
    rows = {}
    for mode in ("baseline", "pulse2x", "gain_only", "disabled_control"):
        audio = directory / f"{case_id}-{mode}.wav"
        rows[mode] = score.measure(
            case_id=case_id, voice_factory=lambda: factory(mode),
            model_label=f"pulse-rate-experiment/{mode}", output_path=audio)
        rows[mode]["audio_sha256"] = hashlib.sha256(audio.read_bytes()).hexdigest()
        rows[mode]["pulse_oscillator_gain_q15"] = (
            vf._OS2_SUBSTEP_GAIN_Q15 if mode in ("pulse2x", "gain_only") else 32768)
        print(case_id, mode, {k: v["error"] for k, v in rows[mode]["metrics"].items()}, flush=True)

    baseline, candidate, disabled = rows["baseline"], rows["pulse2x"], rows["disabled_control"]
    if baseline["audio_sha256"] != disabled["audio_sha256"] or baseline["metrics"] != disabled["metrics"]:
        raise score.Refused("disabled pulse2x control does not reproduce baseline")
    if baseline["audio_sha256"] == candidate["audio_sha256"]:
        raise score.Refused("pulse2x switch produced no audio change")
    comparisons = {mode: compare(baseline, rows[mode])
                   for mode in ("pulse2x", "gain_only", "disabled_control")}
    if comparisons["disabled_control"]["accepts_incremental_improvement"]:
        raise score.Refused("disabled pulse2x control falsely promoted")
    comparisons["pulse2x_vs_gain_only"] = compare(rows["gain_only"], candidate)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", *SOURCES], cwd=ROOT).returncode
    if dirty not in (0, 1):
        raise score.Refused("cannot determine experiment-source state")
    report = {
        "schema": "mono-pulse-2x-experiment-v1", "case_id": case_id,
        "evidence": "model-only; rectangular 2x rendering is not implemented in RTL",
        "source_commit": commit, "source_dirty": bool(dirty),
        "source_sha256": {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in SOURCES},
        "fixed_duty_percent": 100 * vf.DUTY["pulse479"] / vf.CYCLE,
        "headroom_control": "baseline pulse multiplied by the candidate's exact 27853/32768 gain before mixing",
        "wrong_then_right": {"reported_sound_measurements_corrected": 0,
                             "complete_phrase_measurements": len(rows),
                             "start_red": "Fourier/DC test rejected saw-only renderer; optional-flag test rejected absent implementation"},
        "controls": {"disabled_bit_identical_to_baseline": True,
                     "disabled_not_promoted": True, "candidate_changes_audio": True},
        "rows": rows, "comparisons": comparisons,
    }
    destination = directory / f"{case_id}.json"
    destination.write_text(json.dumps(report, indent=2) + "\n")
    print(f"{case_id}: incremental acceptance={comparisons['pulse2x']['accepts_incremental_improvement']}; report={destination}")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=("M5A", "M5B"), required=True)
    parser.add_argument("--out", type=Path, default=ROOT / "build/mono-pulse-2x")
    args = parser.parse_args()
    measure(args.case, args.out)
