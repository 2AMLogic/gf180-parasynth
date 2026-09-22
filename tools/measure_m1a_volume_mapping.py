#!/usr/bin/env python3
"""Fixed -4 dB M1A challenger and a separately labelled octave-detune probe."""
from __future__ import annotations

import json
import math
from pathlib import Path
import subprocess

import numpy as np
from scipy.io import wavfile
import mono_m1a_score as bass

ROOT, SR = bass.ROOT, bass.SR
OUT = ROOT / "docs/scorecard/mono-m1a-miniv3/volume-mapping"


def counts(measured):
    props = list(measured["properties"].values())
    return {"passing": sum(p.get("valid", False) and abs(p["error"]) <= p["tolerance"] for p in props),
            "failing": sum(p.get("valid", False) and abs(p["error"]) > p["tolerance"] for p in props),
            "unqualified": sum(not p.get("valid", False) for p in props)}


def passed_partials(measured):
    return {(i, h) for i, e in enumerate(measured["events"])
            for h, error in e["harmonic_error_db_model_minus_reference"].items()
            if error is not None and abs(error) <= 1.}


def oscillator_mapping(manifest):
    """Re-measure both isolated controls for all three matching event windows."""
    controls = {}
    for name, octave in (("osc1_open", 0), ("osc2_open", 12)):
        sr, pcm = wavfile.read(bass.MANIFEST.parent / manifest["controls"][name]["file"])
        assert sr == SR
        rows = []
        for event in bass.reference.EVENTS:
            clip = pcm[round((event["on_s"] + .3) * SR):round((event["on_s"] + .55) * SR)]
            hz = bass.lead.vf.note_hz(event["note"] + octave)
            estimate = bass.lead.am.refine_f0(clip, hz, SR)
            if not estimate.ok:
                raise bass.Refused("isolated oscillator pitch refused")
            rows.append({**event, "f0_hz": estimate.value,
                         "cents_from_command": 1200 * math.log2(estimate.value / hz)})
        controls[name] = rows
    detunes = [1200 * math.log2(b["f0_hz"] / (2 * a["f0_hz"]))
               for a, b in zip(controls["osc1_open"], controls["osc2_open"])]
    return {"isolated_controls": controls, "octave_offset_cents": detunes,
            "probe_offset_cents": float(np.median(detunes)),
            "scope": "isolated open-filter audio; offset applied only in diagnostic challenger"}


def main():
    manifest, reference = bass.load_reference()
    patch = bass.patch_for_reference(manifest)
    engine = bass.lead.engine_configuration("selected")
    mapping = oscillator_mapping(manifest)
    OUT.mkdir(parents=True, exist_ok=True)
    source = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    files = ["tools/measure_m1a_volume_mapping.py", "tools/mono_m1a_score.py",
             "tools/mono_m5a_score.py", "tools/measure_mono_m1a_reference.py",
             "tools/measure_mono_m5a_reference.py", "model/voice_fx.py", "model/fixed.py",
             "model/filter_rate_chain.py", "model/audio_measure.py", "audition/dsp.py"]
    baseline_record = json.loads((ROOT / "docs/scorecard/results/M1A.json").read_text())
    rows = {}
    for name, volume_db, detune_cents in (("baseline", 0., 0.), ("volume-minus4db", -4., 0.),
                                         ("detune-diagnostic", -4., mapping["probe_offset_cents"])):
        selected = {**patch, "vol": patch["vol"] * 10 ** (volume_db / 20),
                    "detune": (0., 12. + detune_cents / 100, 0.)}
        sequence = [(e["on_s"], e["note"], e["gate_s"], {**selected, "gate": e["gate_s"]})
                    for e in bass.reference.EVENTS]
        pcm = bass.lead.vf.render_mono_fx(sequence, bass.reference.SECONDS,
                                         bass.lead._voice_for_engine(engine))[:len(reference)]
        audio = OUT / f"{name}.wav"
        wavfile.write(audio, SR, pcm.astype('<i2'))
        measured = bass.compare_audio(pcm.astype(np.float64) / 32768, reference)
        if name == "baseline":
            if bass.sha(audio) != baseline_record["diagnostics"]["model_audio_sha256"]:
                raise bass.Refused("current baseline does not reproduce the recorded WAV")
        rows[name] = {"patch": selected, "volume_change_db": volume_db,
                      "octave_offset_cents": detune_cents, "measurements": measured,
                      "components": counts(measured), "audio": str(audio.relative_to(ROOT)),
                      "sha256": bass.sha(audio), "metrics": bass.required_metrics(measured)}
        (OUT / f"{name}.json").write_text(json.dumps(rows[name], indent=2) + "\n")
        print(name, rows[name]["components"], flush=True)
    baseline, candidate = (rows[n]["measurements"] for n in ("baseline", "volume-minus4db"))
    lost = sorted(passed_partials(baseline) - passed_partials(candidate))
    gains = [e["rms_dbfs"]["model"] - e["rms_dbfs"]["reference"] for e in candidate["events"]]
    if lost or not all(abs(g) <= candidate["properties"]["Gain"]["tolerance"] for g in gains):
        raise bass.Refused("volume challenger loses a harmonic pass or fails per-note gain")
    for name, metric in baseline["properties"].items():
        if metric.get("valid") and abs(metric["error"]) <= metric["tolerance"]:
            other = candidate["properties"][name]
            if not other.get("valid") or abs(other["error"]) > other["tolerance"]:
                raise bass.Refused(f"volume challenger loses {name} pass")
    report = {"analysis_version": "m1a-volume-mapping-v1", "source_commit": source,
              "source_sha256": {name: bass.sha(ROOT / name) for name in files},
              "reference_sha256": bass.sha(bass.MANIFEST),
              "engine_configuration": {**engine, "oscillator_pulse_oversample_2x": False},
              "evidence_level": "model only", "case_state": "NO VERDICT",
              "oscillator_mapping": mapping, "runs": rows,
              "volume_acceptance": {"lost_harmonic_passes": lost, "gain_errors_db": gains,
                                    "existing_property_passes_preserved": True},
              "envelope_qualification": bass.reference.qualify_envelope_basis(),
              "corroboration": {"Model D cross-check": {"state": "not measured", "required_for_M1A": False}},
              "scope": "volume challenger accepted as component improvement; detune run diagnostic only; no filter redesign or threshold changes"}
    (OUT / "report.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
