#!/usr/bin/env python3
"""Compare unchanged selected-model attack in the frozen note-history contexts."""
from __future__ import annotations
import json
from pathlib import Path
import subprocess
import numpy as np
from scipy.io import wavfile
import measure_mono_attack_context as context
import mono_m5a_score as lead

ROOT = lead.ROOT


def main():
    source = ROOT / "docs/scorecard/mono-attack-context/report.json"
    frozen = json.loads(source.read_text())
    for row in frozen["renders"]:
        if context.ref.sha256(source.parent / row["wav"]) != row["sha256"]:
            raise lead.Refused("attack-context reference audio hash mismatch")
        if row["events"] != list(context.CONDITIONS[row["condition"]]):
            raise lead.Refused("attack-context reference MIDI timeline differs")
    if context.summarize(frozen["renders"]) != frozen["summary"]:
        raise lead.Refused("attack-context reference summary is inconsistent")
    manifest = json.loads(lead.MANIFEST.read_text())
    patch = lead._voice_patch(manifest)
    engine = lead.engine_configuration("selected")
    out = ROOT / "docs/scorecard/mono-attack-context/model"
    out.mkdir(exist_ok=True)
    rows = []
    for wave in context.ref.WAVE_SETTINGS:
        wave_patch = lead._patch_for_wave(patch, wave)
        if wave == "saw":
            wave_patch.update(cutoff=(engine["saw_cutoff_hz"],) * 2,
                              vol=patch["vol"] * 10 ** (engine["saw_volume_correction_db"] / 20))
        for condition, events in context.CONDITIONS.items():
            voice = lead._voice_for_engine(engine)
            sequence = [(e["on_s"], e["note"], e["gate_s"], {**wave_patch, "gate": e["gate_s"]}) for e in events]
            pcm = lead.vf.render_mono_fx(sequence, 13.5, voice)
            rms = lead.am.rms_envelope(pcm.astype(np.float64) / 32768, ms=5., sr=lead.SR)
            target = events[-1]
            timing = context.ref.envelope_timing(rms, lead.SR, target["on_s"], target["on_s"] + target["gate_s"])
            if not timing.get("valid") or not timing.get("release_complete_40db"):
                raise lead.Refused(f"model envelope incomplete: {wave}/{condition}")
            audio = out / f"{wave}-{condition}.wav"
            wavfile.write(audio, lead.SR, pcm.astype('<i2'))
            reference_ms = frozen["summary"][wave][condition]["median_ms"]
            rows.append({"wave": wave, "condition": condition, "events": events,
                         "reference_attack_ms": reference_ms,
                         "model_attack_ms": timing["attack_10_90_ms"],
                         "error_ms": timing["attack_10_90_ms"] - reference_ms,
                         "model_timing": timing, "patch": wave_patch,
                         "audio": str(audio.relative_to(ROOT)), "sha256": context.ref.sha256(audio)})
            print(wave, condition, rows[-1]["model_attack_ms"], "reference", reference_ms, flush=True)
    contrasts = []
    for wave in context.ref.WAVE_SETTINGS:
        values = {r["condition"]: r for r in rows if r["wave"] == wave}
        for repeated, delayed in (("repeat84_gap3p4", "delayed84_at4p1"),
                                  ("from72_gap3p4", "delayed84_at4p1"),
                                  ("repeat84_gap5", "delayed84_at5p7")):
            contrasts.append({"wave": wave, "with_history": repeated, "without_history": delayed,
                              **{key: values[repeated][key] - values[delayed][key]
                                 for key in ("model_attack_ms", "reference_attack_ms")}})
    files = ("tools/compare_mono_attack_context.py", "tools/measure_mono_attack_context.py",
             "tools/measure_mono_m5a_reference.py", "tools/mono_m5a_score.py",
             "model/voice_fx.py", "model/audio_measure.py", "model/filter_rate_chain.py")
    report = {"engine": "fixed-model", "analysis_version": "attack-context-model-v1",
              "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
              "source_sha256": {f: context.ref.sha256(ROOT / f) for f in files},
              "reference_report_sha256": context.ref.sha256(source),
              "envelope_calibration_manifest_sha256": context.ref.sha256(lead.MANIFEST),
              "engine_configuration": engine, "pulse_oscillator_2x": False,
              "scope": "unchanged baseline model; matched note-history contexts; no global attack adjustment",
              "rows": rows, "history_effect_ms": contrasts}
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
