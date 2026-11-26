"""Exercise the Mini V4 renderer's MIDI call with a real DawDreamer VST3 processor.

Supply an instrument that is silent at rest and sounds on MIDI 60 at its default
preset. The fixture used for the proposal was DISTRHO Nekobi from Ubuntu's
dpf-plugins-vst3 1.7+ds-2build3. This checks host dispatch only.
"""
import argparse
import hashlib
from importlib.metadata import version
from pathlib import Path
import platform
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import render_miniv4_suite as suite


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    args = parser.parse_args()
    bundle = args.bundle.resolve(strict=True)
    print(f"{platform.system()} {platform.machine()}, DawDreamer {version('dawdreamer')}, "
          f"plugin {bundle.name}, default preset, {suite.SR} Hz, block {suite.BLOCK}")
    for binary in sorted(bundle.rglob("*.so")):
        print(f"{binary.name} sha256 {hashlib.sha256(binary.read_bytes()).hexdigest()}")

    control = suite._Rig(str(bundle))
    control.p.clear_midi()
    control.eng.render(suite.CAL_RENDER_S)
    rest = control.eng.get_audio()
    if not rest.size or not np.isfinite(rest).all() or np.any(rest):
        print("REFUSED: the no-MIDI control requires finite digital silence", file=sys.stderr)
        return 2
    control.p.add_midi_note(suite.CAL_NOTE, suite.VELOCITY, 0.02, suite.CAL_RENDER_S)
    control.eng.render(suite.CAL_RENDER_S)
    sound = control.eng.get_audio()
    if not sound.size or not np.isfinite(sound).all() or not np.any(sound):
        print("REFUSED: the direct MIDI control produced no usable sound", file=sys.stderr)
        return 2
    print("Controls: silent without MIDI, sounding after add_midi_note")

    rig = suite._Rig(str(bundle))
    audio = rig.render_note(suite.CAL_NOTE, suite.CAL_RENDER_S)
    if not audio.size or not np.isfinite(audio).all() or not np.any(audio):
        raise SystemExit("FAIL: the suite adapter returned no usable sound")
    print("PASS: the suite adapter delivered the calibration note")
    return 0


if __name__ == "__main__":
    sys.exit(main())
