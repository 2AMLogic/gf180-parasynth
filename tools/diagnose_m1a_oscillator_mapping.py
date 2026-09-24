#!/usr/bin/env python3
"""Measure repeated-note partial changes in frozen M1A isolation controls."""
from __future__ import annotations
import json
import math
from pathlib import Path
import subprocess
import numpy as np
from scipy.io import wavfile
import measure_m1a_volume_mapping as experiment

bass, SR = experiment.bass, experiment.SR


def interference(first, second, hz):
    """Power decomposition at one shared projection frequency, not two bins."""
    if len(first) != len(second) or len(first) * hz / SR < 12:
        raise bass.Refused("interference projection requires matching windows and 12 periods")
    window = bass.lead.am._bh4(len(first))
    kernel = window * np.exp(-2j * np.pi * hz * np.arange(len(first)) / SR)
    project = lambda x: 2 * np.sum(kernel * x) / np.sum(window)
    a, b, combined = project(first), project(second), project(first + second)
    incoherent = abs(a) ** 2 + abs(b) ** 2
    if incoherent <= 1e-20 or abs(combined) <= 1e-15:
        raise bass.Refused("interference projection is silent or cancels below numeric floor")
    return {"projection_hz": hz, "first_power": abs(a) ** 2, "second_power": abs(b) ** 2,
            "cross_power": float(2 * (a * b.conjugate()).real),
            "sum_power": abs(combined) ** 2,
            "relative_phase_degrees": float(np.angle(b * a.conjugate(), deg=True)),
            "coherent_vs_incoherent_db": 10 * math.log10(abs(combined) ** 2 / incoherent),
            "linearity_residual": float(abs(combined - a - b))}


def partials(audio, octave=0):
    rows = []
    for event in bass.reference.EVENTS:
        clip = audio[round((event["on_s"] + .3) * SR):round((event["on_s"] + .55) * SR)]
        hz = bass.lead.vf.note_hz(event["note"] + octave)
        pitch = bass.lead.am.refine_f0(clip, hz, SR)
        if not pitch.ok:
            raise bass.Refused("isolated/summed diagnostic pitch refused")
        signature = bass.lead.am.harmonic_signature(clip, SR, f0=pitch.value, kmax=12)
        rows.append({**event, "f0_hz": pitch.value, "harmonics_db": signature})
    return {"events": rows, "repeat36_change_db": {
        f"h{k}": rows[2]["harmonics_db"][f"h{k}"] - rows[0]["harmonics_db"][f"h{k}"]
        for k in range(2, 13) if rows[2]["harmonics_db"].get(f"h{k}") is not None
        and rows[0]["harmonics_db"].get(f"h{k}") is not None}}


def main():
    manifest, reference = bass.load_reference()
    inputs = {"manifest": bass.sha(bass.MANIFEST)}
    controls = {}
    for name in ("osc1_open", "osc2_open", "filter_envelope_disabled"):
        path = bass.MANIFEST.parent / manifest["controls"][name]["file"]
        rate, audio = wavfile.read(path)
        assert rate == SR
        controls[name] = audio.astype(np.float64)
        inputs[name] = bass.sha(path)
    measured = {"reference_patch": partials(reference),
                "osc1_open": partials(controls["osc1_open"]),
                "osc2_open": partials(controls["osc2_open"], 12),
                "sum_of_isolated_outputs": partials(controls["osc1_open"] + controls["osc2_open"]),
                "filter_envelope_disabled": partials(controls["filter_envelope_disabled"])}
    cross_terms = []
    for i in (0, 2):
        event = bass.reference.EVENTS[i]
        start, stop = [round((event["on_s"] + offset) * SR) for offset in (.3, .55)]
        hz = measured["osc1_open"]["events"][i]["f0_hz"] * 8
        cross_terms.append({**event, **interference(controls["osc1_open"][start:stop],
                                                   controls["osc2_open"][start:stop], hz)})
    for name in ("baseline", "volume-minus4db", "detune-diagnostic"):
        path = experiment.OUT / (name + ".wav")
        rate, audio = wavfile.read(path)
        assert rate == SR and audio.dtype == np.int16
        measured[name] = partials(audio.astype(np.float64) / 32768)
        inputs[name] = bass.sha(path)
    report = {"source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=bass.ROOT, text=True).strip(),
              "source_sha256": {f: bass.sha(bass.ROOT / f) for f in
                  ("tools/diagnose_m1a_oscillator_mapping.py", "tools/measure_m1a_volume_mapping.py", "model/audio_measure.py")},
              "inputs_sha256": inputs, "measurements": measured,
              "eighth_partial_interference": cross_terms,
              "scope": "same late held windows; isolated outputs summed as a diagnostic, not an internal filter-input reconstruction",
              "envelope_scope": "40 ms window remains release-only; fast attack and internal cutoff trajectory remain unqualified"}
    (experiment.OUT / "oscillator-diagnosis.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v["repeat36_change_db"].get("h8") for k, v in measured.items()}, indent=2))


if __name__ == "__main__":
    main()
