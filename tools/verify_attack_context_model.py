#!/usr/bin/env python3
"""Reproduce the frozen model-context measurements without rendering a voice."""
from __future__ import annotations

import argparse
import json
import math
import hashlib
import subprocess
from pathlib import Path

import numpy as np
from scipy.io import wavfile

import compare_mono_attack_context as producer


def verify(report_path: Path) -> dict:
    root = producer.ROOT
    ref = producer.context.ref
    lead = producer.lead
    report = json.loads(report_path.read_text())
    reference_path = root / "docs/scorecard/mono-attack-context/report.json"
    assert ref.sha256(reference_path) == report["reference_report_sha256"], "reference report changed"
    assert ref.sha256(lead.MANIFEST) == report["envelope_calibration_manifest_sha256"], "calibration changed"
    changed_sources = []
    # The stored audio remains evidence for its original engine commit.
    # Verify that historical identity without relabelling it as today's engine.
    analysis_sources = {"model/audio_measure.py", "tools/measure_mono_m5a_reference.py",
                        "tools/measure_mono_attack_context.py"}
    assert analysis_sources <= report["source_sha256"].keys(), "analysis source binding absent"
    for name, digest in report["source_sha256"].items():
        historical = subprocess.check_output(["git", "show", f"{report['source_commit']}:{name}"], cwd=root)
        assert hashlib.sha256(historical).hexdigest() == digest, f"historical source hash differs: {name}"
        if ref.sha256(root / name) != digest:
            changed_sources.append(name)
            assert name not in analysis_sources, f"analysis basis changed: {name}; use the recorded checkout"
    reference = json.loads(reference_path.read_text())
    for row in reference["renders"]:
        assert ref.sha256(reference_path.parent / row["wav"]) == row["sha256"], "reference audio changed"
        assert row["events"] == list(producer.context.CONDITIONS[row["condition"]]), "reference events changed"
    assert producer.context.summarize(reference["renders"]) == reference["summary"]
    expected = {(wave, condition) for wave in producer.context.ref.WAVE_SETTINGS
                for condition in producer.context.CONDITIONS}
    assert len(report["rows"]) == len(expected)
    assert {(r["wave"], r["condition"]) for r in report["rows"]} == expected
    assert report["engine_configuration"] == lead.engine_configuration("selected")
    assert report["pulse_oscillator_2x"] is False
    rms_max_delta = 0.0
    for row in report["rows"]:
        audio = report_path.parent / Path(row["audio"]).name
        assert ref.sha256(audio) == row["sha256"], f"audio changed: {audio.name}"
        sr, pcm = wavfile.read(audio)
        assert sr == lead.SR and pcm.dtype == np.int16 and pcm.shape == (648000,), "audio format changed"
        events = list(producer.context.CONDITIONS[row["condition"]])
        assert row["events"] == events, "model events changed"
        rms = lead.am.rms_envelope(pcm.astype(np.float64) / 32768, ms=5., sr=sr)
        event = events[-1]
        timing = ref.envelope_timing(rms, sr, event["on_s"], event["on_s"] + event["gate_s"])
        assert timing.keys() == row["model_timing"].keys()
        assert timing["valid"] and timing["release_complete_40db"]
        for key, value in timing.items():
            recorded = row["model_timing"][key]
            # ARM/x86 summation differs by one ulp in one held RMS value.
            # Time crossings and all other diagnostics still reproduce exactly.
            if key == "held_rms":
                assert math.isclose(value, recorded, rel_tol=1e-14, abs_tol=0), "held RMS differs"
                rms_max_delta = max(rms_max_delta, abs(value - recorded))
            else:
                assert value == recorded, f"measurement differs: {row['wave']}/{row['condition']}/{key}"
        reference_ms = reference["summary"][row["wave"]][row["condition"]]["median_ms"]
        assert row["reference_attack_ms"] == reference_ms
        assert row["model_attack_ms"] == timing["attack_10_90_ms"]
        assert row["error_ms"] == row["model_attack_ms"] - reference_ms
    contrasts = []
    for wave in producer.context.ref.WAVE_SETTINGS:
        values = {r["condition"]: r for r in report["rows"] if r["wave"] == wave}
        for repeated, delayed in (("repeat84_gap3p4", "delayed84_at4p1"),
                                  ("from72_gap3p4", "delayed84_at4p1"),
                                  ("repeat84_gap5", "delayed84_at5p7")):
            contrasts.append({"wave": wave, "with_history": repeated, "without_history": delayed,
                              **{key: values[repeated][key] - values[delayed][key]
                                 for key in ("model_attack_ms", "reference_attack_ms")}})
    assert contrasts == report["history_effect_ms"], "history contrasts differ"
    return {"state": "reproduced", "model_wavs": len(expected), "reference_wavs": len(reference["renders"]),
            "evidence_basis": "historical model audio; unchanged analysis basis",
            "render_source_commit": report["source_commit"], "current_source_differences": changed_sources,
            "exact_timing_rows": len(expected), "held_rms_max_absolute_delta": rms_max_delta,
            "history_contrasts": len(contrasts)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, nargs="?", default=producer.ROOT /
                        "docs/scorecard/mono-attack-context/model/report.json")
    print(json.dumps(verify(parser.parse_args().report), indent=2))
