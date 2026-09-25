#!/usr/bin/env python3
"""Rescore the EXISTING M1A attack-sweep renders (10, 4, 5 ms) under
m1a-envelope-score-v3. No render: each WAV must hash to the SHA-256 its sweep
point recorded, or this REFUSES. Writes attack-qualification/rescore.json.
Exit 0 done, 2 refused."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from scipy.io import wavfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import mono_m1a_score as bass                                       # noqa: E402
import sweep_m1a_attack as sweep                                    # noqa: E402

SETTINGS_MS = (10.0, 4.0, 5.0)


def main():
    try:
        manifest, reference = bass.load_reference()
        out = {"schema": "m1a-attack-rescore-v1", "analysis_version": bass.ANALYSIS_VERSION,
               "domain": bass.qualified_attack_domain(), "settings": []}
        for ms in SETTINGS_MS:
            point = json.loads((sweep.OUT / f"{sweep.point_name(ms)}.json").read_text())
            wav = ROOT / point["audio"]
            if not wav.exists() or bass.sha(wav) != point["sha256"]:
                raise bass.Refused(f"{wav.name} missing or not bit-identical to its recorded hash")
            sr, pcm = wavfile.read(wav)
            m = bass.compare_audio(pcm.astype(np.float64) / 32768, reference)
            events = [{"note": e["note"], "on_s": e["on_s"],
                       **{side: {"attack_10_90_ms": f["attack_10_90_ms"], "shape_p": f["shape_p"],
                                 "explained_ratio": f["explained_ratio"],
                                 "search_boundary": f["search_boundary"],
                                 "qualified": e["attack_out_of_domain"][side] is None,
                                 "why": e["attack_out_of_domain"][side]}
                          for side, f in e["attack_fit"].items()},
                       "grade": e["attack_state"]} for e in m["events"]]
            out["settings"].append({"amp_attack_ms": ms, "sha256": point["sha256"],
                                    "events": events,
                                    "properties": {k: {kk: v.get(kk) for kk in ("valid", "error", "unqualified_error")}
                                                   for k, v in m["properties"].items()}})
            for e in events:
                print(f"{ms:>4g} ms MIDI {e['note']} @{e['on_s']:g}s  model {e['model']['attack_10_90_ms']:.3f} "
                      f"({'Q' if e['model']['qualified'] else 'U'})  ref {e['reference']['attack_10_90_ms']:.3f} "
                      f"({'Q' if e['reference']['qualified'] else 'U'})  {e['grade']}")
        (bass.QUALIFICATION / "rescore.json").write_text(json.dumps(out, indent=2, default=float) + "\n")
        return 0
    except bass.Refused as exc:
        print(f"REFUSED: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
