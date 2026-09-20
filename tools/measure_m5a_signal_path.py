#!/usr/bin/env python3
"""Locate M5A saw-spectrum error at oscillator, mixer, ladder, and output.

Uses the committed Mini V3 recording and the same steady-state event windows
as the M5A scorer. The only intervention swept here is the model cutoff.
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


def measure(cutoffs: list[int], drives: list[float]) -> dict:
    manifest = json.loads(m5a.MANIFEST.read_text())
    audio_meta = manifest["audio"]
    ref_path = m5a.MANIFEST.parent / audio_meta["file"]
    raw = ref_path.read_bytes()
    reference_sha = hashlib.sha256(raw).hexdigest()
    if reference_sha != audio_meta["sha256"]:
        raise RuntimeError("frozen M5A WAV hash does not match its manifest")
    sr, ref = wavfile.read(ref_path)
    if sr != m5a.SR or ref.dtype != np.float32 or ref.ndim != 1:
        raise RuntimeError("reference must be mono float32 at 48 kHz")
    ref = ref.astype(np.float64)
    if len(ref) != audio_meta["samples"] or not np.isfinite(ref).all():
        raise RuntimeError("reference length or finite-sample check failed")

    segment = next(s for s in manifest["timeline"]["segments"] if s["wave"] == "saw")
    base_patch = m5a._patch_for_wave(m5a._voice_patch(manifest), "saw")
    outputs = []
    for cutoff in cutoffs:
        if not vf.CUT_MIN <= cutoff <= vf.CUT_MAX:
            raise ValueError(f"cutoff {cutoff} outside [{vf.CUT_MIN}, {vf.CUT_MAX}]")
        for drive in drives:
            if not np.isfinite(drive) or not 0.0 <= drive <= 6.15:
                raise ValueError(f"drive {drive} outside [0, 6.15]")
            patch = {**base_patch, "cutoff": (cutoff, cutoff), "drive": drive}
            seq = [(float(ev["on_s"]), int(ev["note"]), float(ev["gate_s"]),
                    {**patch, "gate": float(ev["gate_s"])})
                   for ev in segment["midi_events"]]
            voice = vf.VoiceFx(oversample_2x=True)
            pcm = vf.render_mono_fx(seq, float(segment["duration_s"]), voice)
            trace = voice.trace
            arrays = {
                "oscillator": trace["osc"][0],
                "mixer": trace["mixed"],
                "ladder": trace["ladder"],
                "output": pcm.astype(np.float64) / 32768.0,
            }
            offset = int(segment["offset_samples"])
            ref_segment = ref[offset:offset + int(segment["samples"])]
            common = min(len(ref_segment), len(pcm))
            if abs(len(ref_segment) - len(pcm)) > 128:
                raise RuntimeError("model and reference saw segment lengths differ by >128 samples")
            ref_segment = ref_segment[:common]
            events = []
            for ev in segment["midi_events"]:
                note = int(ev["note"])
                on = float(ev["on_s"])
                off = on + float(ev["gate_s"])
                a, b = int((on + 0.12) * sr), int((off - 0.08) * sr)
                xref = ref_segment[a:b]
                fref = am.refine_f0(xref, vf.note_hz(note), sr)
                if not fref.ok:
                    raise RuntimeError(f"reference MIDI {note} f0 refused: {fref.reason}")
                ref_sig = am.harmonic_signature(xref, sr, f0=fref.value, kmax=12)
                ref_alias = am.foldback_alias_db(xref, fref.value, sr)
                if not ref_alias.ok:
                    raise RuntimeError(f"reference MIDI {note} alias estimate refused")
                stages = {}
                for name, values in arrays.items():
                    x = np.asarray(values[a:b], dtype=np.float64)
                    f0 = am.refine_f0(x, vf.note_hz(note), sr)
                    if not f0.ok:
                        raise RuntimeError(f"{name} MIDI {note} f0 refused: {f0.reason}")
                    sig = am.harmonic_signature(x, sr, f0=f0.value, kmax=12)
                    comparisons = {f"h{k}": m5a._harmonic_error(sig, ref_sig, k)
                                   for k in range(2, 13)
                                   if k * max(f0.value, fref.value) < sr / 2}
                    diffs = {key: (round(float(error), 4) if error is not None else None)
                             for key, (error, _status) in comparisons.items()}
                    statuses = {key: status for key, (_error, status) in comparisons.items()}
                    measured = [abs(v) for v in diffs.values() if v is not None]
                    alias = am.foldback_alias_db(x, f0.value, sr)
                    if not alias.ok:
                        raise RuntimeError(f"{name} MIDI {note} alias estimate refused")
                    stages[name] = {
                        "harmonic_error_db_model_minus_reference": diffs,
                        "harmonic_comparison": statuses,
                        "max_common_partial_error_db": round(max(measured), 4),
                        "alias_db": round(float(alias.value), 4),
                        "excess_alias_db": round(m5a._excess_alias_db(alias.value, ref_alias.value), 4),
                    }
                out_level = 20.0 * np.log10(max(float(am.rms(arrays["output"][a:b])), 1e-15))
                ref_level = 20.0 * np.log10(max(float(am.rms(xref)), 1e-15))
                events.append({"midi": note, "reference_f0_hz": round(fref.value, 4),
                               "stages": stages,
                               "output_gain_error_db": round(out_level - ref_level, 4),
                               "reference": {"alias_db": round(float(ref_alias.value), 4),
                                             "harmonics_db": {f"h{k}": ref_sig.get(f"h{k}")
                                                              for k in range(2, 13)}}})
            outputs.append({"cutoff_hz": cutoff, "filter_drive": drive, "events": events})

    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                check=True, capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        commit = "unknown"
    source_files = ["tools/measure_m5a_signal_path.py", "tools/mono_m5a_score.py",
                    "model/audio_measure.py", "model/voice_fx.py"]
    source_hashes = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                     for name in source_files}
    return {
        "probe": "M5A oscillator -> mixer -> ladder -> final output",
        "reference_identity": "Mini V3 3.12.0.3422 software synth; not physical hardware",
        "analysis_version": "m5a-signal-path-v2",
        "source_commit": commit,
        "source_sha256": source_hashes["tools/measure_m5a_signal_path.py"],
        "source_hashes": source_hashes,
        "reference_sha256": reference_sha,
        "manifest_sha256": hashlib.sha256(m5a.MANIFEST.read_bytes()).hexdigest(),
        "window": "on + 120 ms through note-off - 80 ms (same as M5A scorer)",
        "oscillator_config": "2x candidate saw; remaining Mini V3 oscillators disabled",
        "intervention": "change only filter drive; cutoff and all other patch fields fixed",
        "runs": outputs,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cutoff", type=int, nargs="+", default=[14073, 21600],
                    help="one or more cutoff values in Hz (default: baseline and maximum)")
    ap.add_argument("--drive", type=float, nargs="+", default=[1.0, 0.75, 0.5],
                    help="one or more ladder drive values (default: baseline, 0.75, 0.5)")
    ap.add_argument("--out", default="build/scorecard/m5a-signal-path.json")
    args = ap.parse_args(argv)
    report = measure(args.cutoff, args.drive)
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"report": str(out.relative_to(ROOT)),
                      "source_commit": report["source_commit"],
                      "cutoffs_hz": args.cutoff,
                      "filter_drives": args.drive,
                      "results": [{"cutoff_hz": run["cutoff_hz"],
                                   "filter_drive": run["filter_drive"],
                                   "events": [{"midi": ev["midi"],
                                               "stage_max_error_db": {
                                                   stage: ev["stages"][stage]["max_common_partial_error_db"]
                                                   for stage in ev["stages"]},
                                               "final_excess_alias_db": ev["stages"]["output"]["excess_alias_db"],
                                               "output_gain_error_db": ev["output_gain_error_db"]}
                                              for ev in run["events"]]}
                                  for run in report["runs"]]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
