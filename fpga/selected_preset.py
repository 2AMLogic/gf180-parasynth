#!/usr/bin/env python3
"""Named playable settings for the measured OSC2X=1 FILTER2X=1 candidate.

Exit 0: the preset was written to --out. Exit 2: REFUSED -- the reference
audio is not the one the frozen manifest names, the reason is on stderr and
nothing was written, not even --out's directory. An execution error is a
traceback at exit 1, never a verdict."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "model"), str(ROOT / "tools")]
import mono_m5a_score as score
import voice_fx as vf
import exit_contract
import refusal


class Refused(refusal.Refused, ValueError):
    """A precondition of the preset is not met (#1674): the reference audio
    differs from the frozen manifest, so no preset was written and no verdict
    exists, which a traceback at exit 1 misreports as a FAIL."""


NAMES = ("m5a-saw", "m5a-pulse")


def definition(name):
    if name not in NAMES:
        raise ValueError(f"unknown selected preset: {name}")
    manifest = json.loads(score.MANIFEST.read_text())
    audio = score.MANIFEST.parent / manifest["audio"]["file"]
    if hashlib.sha256(audio.read_bytes()).hexdigest() != manifest["audio"]["sha256"]:
        raise Refused("selected preset reference audio differs from frozen manifest")
    profile = score.engine_configuration("selected")
    wave = "pulse" if name == "m5a-pulse" else "saw"
    patch = score._patch_for_wave(score._voice_patch(manifest), wave)
    if wave == "saw":
        cutoff = profile["saw_cutoff_hz"]
        patch["cutoff"] = (cutoff, cutoff)
        patch["vol"] *= 10 ** (profile["saw_volume_correction_db"] / 20)
    return {"name": name, "engine_profile": profile,
            "required_build": {"OSC2X": 1, "FILTER2X": 1},
            "oscillator_2x_waveforms": ["saw"],
            "reference_manifest_sha256": hashlib.sha256(score.MANIFEST.read_bytes()).hexdigest(),
            "patch": patch, "registers": vf.VoiceFx.patch_regs(**patch),
            "sound_status": "M5A has four passing properties and three failures"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", choices=NAMES)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        selected = definition(args.name)
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        raise SystemExit(exit_contract.REFUSED)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(selected, indent=2) + "\n")
    print(args.out)
