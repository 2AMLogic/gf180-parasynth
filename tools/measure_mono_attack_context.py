#!/usr/bin/env python3
"""Measure attack context using the frozen Mini V3 patch, without retuning it."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess
import sys
import numpy as np
from scipy.io import wavfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import measure_mono_m5a_reference as ref

CONDITIONS = {
    "isolated84": ({"note": 84, "on_s": .1, "gate_s": .6},),
    "repeat84_gap3p4": ({"note": 84, "on_s": .1, "gate_s": .6},
                          {"note": 84, "on_s": 4.1, "gate_s": .6}),
    "from72_gap3p4": ({"note": 72, "on_s": .1, "gate_s": .6},
                        {"note": 84, "on_s": 4.1, "gate_s": .6}),
    "repeat84_gap5": ({"note": 84, "on_s": .1, "gate_s": .6},
                       {"note": 84, "on_s": 5.7, "gate_s": .6}),
}


def summarize(rows):
    """Require executed, valid, complete-release repeats for every condition."""
    result = {}
    for wave in ref.WAVE_SETTINGS:
        result[wave] = {}
        for condition in CONDITIONS:
            selected = [r for r in rows if r["wave"] == wave and r["condition"] == condition]
            if len(selected) < 3:
                raise ref.Refused(f"missing three independent renders: {wave}/{condition}")
            attacks = []
            for row in selected:
                events = row["measurements"]
                if len(events) != len(CONDITIONS[condition]) or events[-1]["note"] != 84:
                    raise ref.Refused("unexpected note context")
                timing = events[-1]["envelope"]
                if not timing.get("valid") or not timing.get("release_complete_40db"):
                    raise ref.Refused("invalid envelope or incomplete release")
                attacks.append(float(timing["attack_10_90_ms"]))
            if not np.isfinite(attacks).all():
                raise ref.Refused("non-finite attack")
            result[wave][condition] = {"attack_ms": attacks, "median_ms": float(np.median(attacks)),
                                       "range_ms": float(np.ptp(attacks))}
        anchor = result[wave]["isolated84"]["median_ms"]
        for item in result[wave].values():
            item["delta_from_isolated_ms"] = item["median_ms"] - anchor
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=ROOT / "docs/scorecard/mono-attack-context")
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    try:
        if subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip():
            raise ref.Refused("commit the measurement instrument before capture")
        code = ref._source_provenance()
        code["files_sha256"]["tools/measure_mono_attack_context.py"] = ref.sha256(Path(__file__))
        identity = ref._plugin_metadata()
        integrity = ref._qualify_reference_integrity()
        rows = []
        for wave in ref.WAVE_SETTINGS:
            for condition, events in CONDITIONS.items():
                for repeat in range(3):
                    audio, apparatus = ref._render_segment(wave, ref.PATCH["amp_decay"][2],
                                                           {wave: events}, 13.5)
                    measurements = ref._event_analysis(audio, wave, {wave: events},
                                                       classify_every_note=True)
                    path = a.out / f"{wave}-{condition}-{repeat}.wav"
                    wavfile.write(path, ref.SR, np.asarray(audio, dtype=np.float32))
                    rows.append({"wave": wave, "condition": condition, "repeat": repeat,
                                 "events": events, "measurements": measurements,
                                 "apparatus": apparatus, "wav": path.name,
                                 "sha256": ref.sha256(path)})
                print(f"measured {wave}/{condition}: three renders", flush=True)
        report = {"schema": "mono-attack-context-v1", "valid": True,
                  "scope": "audio-envelope measurement; does not reveal the plugin's internal VCA state",
                  "identity": identity, "integrity": integrity, "provenance": code,
                  "summary": summarize(rows), "renders": rows,
                  "wrong_then_right": {"corrected_measurements": 0, "executed_renders": len(rows)}}
        (a.out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report["summary"], indent=2))
        return 0
    except (ref.Refused, OSError, ValueError) as exc:
        (a.out / "refused.json").write_text(json.dumps({"state": "REFUSED", "reason": str(exc)}, indent=2))
        print(f"REFUSED: {exc}"); return 2


if __name__ == "__main__":
    raise SystemExit(main())
