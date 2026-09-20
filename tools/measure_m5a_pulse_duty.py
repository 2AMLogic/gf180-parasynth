#!/usr/bin/env python3
"""Measure supported rectangular-wave duties against the frozen M5A pulse.

This is a model experiment, not an RTL or physical Minimoog measurement. It
keeps the selected 2x oscillator, filter drive, cutoff, and all patch settings
fixed while selecting one of the existing chip waveform shapes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import subprocess
import sys

import numpy as np
from scipy.io import wavfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "tools"))
import audio_measure as am
import mono_m5a_score as m5a
import voice_fx as vf

SHAPES = ("square", "pulse15", "pulse25", "pulse29")


def _shape_summary(model, reference, f0_model, f0_reference, sr):
    comparisons = {f"h{k}": m5a._harmonic_error(model, reference, k)
                   for k in range(2, 13)
                   if k * max(f0_model, f0_reference) < sr / 2}
    errors = {key: (round(float(value), 4) if value is not None else None)
              for key, (value, _status) in comparisons.items()}
    statuses = {key: status for key, (_value, status) in comparisons.items()}
    measured = [abs(value) for value in errors.values() if value is not None]
    if len(measured) < 2:
        raise RuntimeError("fewer than two pulse harmonics are comparable")
    return {"harmonic_error_db_model_minus_reference": errors,
            "harmonic_comparison": statuses,
            "max_common_partial_error_db": round(max(measured), 4)}


def measure(shapes=SHAPES):
    unknown = sorted(set(shapes) - set(SHAPES))
    if unknown or not shapes or len(set(shapes)) != len(shapes):
        raise ValueError(f"shapes must be unique members of {SHAPES}; invalid={unknown}")
    manifest_bytes = m5a.MANIFEST.read_bytes()
    manifest = json.loads(manifest_bytes)
    audio_meta = manifest["audio"]
    reference_path = m5a.MANIFEST.parent / audio_meta["file"]
    reference_bytes = reference_path.read_bytes()
    reference_sha = hashlib.sha256(reference_bytes).hexdigest()
    if reference_sha != audio_meta["sha256"]:
        raise RuntimeError("frozen M5A WAV hash does not match its manifest")
    sr, reference = wavfile.read(reference_path)
    if sr != m5a.SR or reference.dtype != np.float32 or reference.ndim != 1:
        raise RuntimeError("reference must be mono float32 at 48 kHz")
    reference = reference.astype(np.float64)
    if len(reference) != audio_meta["samples"] or not np.isfinite(reference).all():
        raise RuntimeError("reference length or finite-sample check failed")

    segment = next(item for item in manifest["timeline"]["segments"]
                   if item["wave"] == "pulse")
    base_patch = m5a._voice_patch(manifest)
    offset = int(segment["offset_samples"])
    ref_segment = reference[offset:offset + int(segment["samples"])]
    runs = []
    for shape in shapes:
        patch = {**base_patch, "waves": (shape, shape, shape)}
        sequence = [(float(event["on_s"]), int(event["note"]), float(event["gate_s"]),
                     {**patch, "gate": float(event["gate_s"])})
                    for event in segment["midi_events"]]
        pcm = vf.render_mono_fx(sequence, float(segment["duration_s"]),
                                vf.VoiceFx(oversample_2x=True))
        common = min(len(ref_segment), len(pcm))
        if abs(len(ref_segment) - len(pcm)) > 128:
            raise RuntimeError("model and reference pulse segment lengths differ by >128 samples")
        model_segment = pcm[:common].astype(np.float64) / 32768.0
        events = []
        for event in segment["midi_events"]:
            note = int(event["note"])
            start = float(event["on_s"])
            end = start + float(event["gate_s"])
            a, b = int((start + 0.12) * sr), int((end - 0.08) * sr)
            x_model, x_reference = model_segment[a:b], ref_segment[a:b]
            f0_expected = vf.note_hz(note)
            fm = am.refine_f0(x_model, f0_expected, sr)
            fr = am.refine_f0(x_reference, f0_expected, sr)
            if not fm.ok or not fr.ok:
                raise RuntimeError(f"pulse MIDI {note} pitch refused: "
                                   f"{fm.reason if not fm.ok else fr.reason}")
            sm = am.harmonic_signature(x_model, sr, f0=fm.value, kmax=12)
            sf = am.harmonic_signature(x_reference, sr, f0=fr.value, kmax=12)
            amodel = am.foldback_alias_db(x_model, fm.value, sr)
            aref = am.foldback_alias_db(x_reference, fr.value, sr)
            if not amodel.ok or not aref.ok:
                raise RuntimeError(f"pulse MIDI {note} alias estimator refused")
            summary = _shape_summary(sm, sf, fm.value, fr.value, sr)
            summary.update({
                "wave": shape,
                "midi": note,
                "pitch_error_cents": round(m5a._cents_error(fm.value, fr.value), 5),
                "excess_alias_db": round(m5a._excess_alias_db(amodel.value, aref.value), 4),
                "harmonic_error_db_model_minus_reference": summary.pop(
                    "harmonic_error_db_model_minus_reference"),
                "reference_even_harmonics_db": {
                    f"h{k}": sf.get(f"h{k}") for k in (2, 4, 6, 8, 10, 12)
                    if k * fr.value < sr / 2},
            })
            events.append(summary)
        runs.append({"shape": shape,
                     "duty_fraction": vf.DUTY[shape] / vf.CYCLE,
                     "events": events})

    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                check=True, capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        commit = "unknown"
    sources = ["tools/measure_m5a_pulse_duty.py", "tools/mono_m5a_score.py",
               "model/audio_measure.py", "model/voice_fx.py"]
    return {
        "probe": "M5A pulse duty sweep",
        "reference_identity": "Mini V3 3.12.0.3422 software synth; not physical hardware",
        "analysis_version": "m5a-pulse-duty-v1",
        "source_commit": commit,
        "source_hashes": {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
                          for path in sources},
        "reference_sha256": reference_sha,
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "oscillator_config": "selected 2x voice model",
        "fixed_filter_drive": base_patch["drive"],
        "fixed_cutoff_hz": base_patch["cutoff"][0],
        "window": "on + 120 ms through note-off - 80 ms (same as M5A scorer)",
        "intervention": "existing rectangular waveform duty only",
        "runs": runs,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shapes", nargs="+", choices=SHAPES, default=list(SHAPES))
    parser.add_argument("--out", default="docs/scorecard/mono-m5a-miniv3/pulse-duty-v1.json")
    args = parser.parse_args(argv)
    report = measure(args.shapes)
    path = ROOT / args.out
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"report": str(path.relative_to(ROOT)),
                      "source_commit": report["source_commit"],
                      "results": [{"shape": run["shape"], "duty_fraction": run["duty_fraction"],
                                   "events": [{key: event[key] for key in (
                                       "midi", "max_common_partial_error_db",
                                       "excess_alias_db", "pitch_error_cents")}
                                              for event in run["events"]]}
                                  for run in report["runs"]]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
