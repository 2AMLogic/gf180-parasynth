#!/usr/bin/env python3
"""fpga/release/held_note_audible.py -- the release's one-command held note,
as the CLI emits it, through the Arty wrapper RTL, and AUDIBLE.

WHY. `uart_host.py run --note 45 --fixture none` was the documented first
playback. Its image (`voice_image_writes`) carried no mixer weights, every
weight resets to 0, so the note was exact SILENCE -- and the UART bench's
`held` scenario was bit-exact against a model that was silent too, with a
release-tail check (peak <= 1024 LSB) that silence passes. Bit-exactness
cannot see a sound that is not there; a level check on the wire can.

WHAT RUNS. For each release preset: `uart_host.main([--dry-run, run,
--note N, --fixture none, --preset P, --capture PREFIX])` -- the shipped CLI,
validator included -- then `fpga/verify_uart_bridge.py --replay PREFIX`
(the real wrapper at its UART pins, I2S decoded from the wire, compared with
the integer model), then the peak |left| of the DECODED I2S. PASS needs the
replay PASS and a peak of at least MIN_PEAK.

CONTROL (--legacy-image): the same capture with the mixer writes the fix
added removed -- the pre-fix bytes. The replay still PASSES (silence is
bit-exact) and the level check must FAIL. With --expect-fail, exit 0 only
then.

    .venv/bin/python fpga/release/held_note_audible.py --preset default
    .venv/bin/python fpga/release/held_note_audible.py --preset default --legacy-image --expect-fail

Exit: 0 pass, 1 ran and failed, 2 did not run (REFUSED/no simulator).
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [os.path.join(ROOT, "fpga"), os.path.join(ROOT, "model"),
                os.path.join(ROOT, "audition"), os.path.join(ROOT, "tools"),
                os.path.join(ROOT, "rtl-sketch")]

MIN_PEAK = 1024        # LSB of 16-bit; the old release-tail bound, now as a FLOOR
NOTES = {"default": 45, "m5a-saw": 72, "m5a-pulse": 72}


def capture(preset, note, prefix, legacy=False):
    """The CLI's own capture. `legacy`: the same CLI with `voice_mixer_writes`
    emptied -- exactly the pre-fix bytes -- under --engineering, because the
    release validator refuses that stream (its waveform set is unknown)."""
    import uart_host as uh
    argv = ["--dry-run", "run", "--note", str(note), "--fixture", "none", "--capture", prefix]
    if preset != "default":
        argv += ["--preset", preset]
    if legacy:
        argv.insert(0, "--engineering")
    saved = uh.voice_mixer_writes
    if legacy:
        uh.voice_mixer_writes = lambda preset=None: []
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            return uh.main(argv)
    finally:
        uh.voice_mixer_writes = saved


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--preset", choices=sorted(NOTES), required=True)
    ap.add_argument("--legacy-image", action="store_true")
    ap.add_argument("--expect-fail", action="store_true")
    ap.add_argument("--outdir", default=None)
    a = ap.parse_args(argv)
    name = a.preset + ("-legacy" if a.legacy_image else "")
    outdir = os.path.abspath(a.outdir or os.path.join(ROOT, "build", "release-held", name))
    os.makedirs(outdir, exist_ok=True)
    prefix = os.path.join(outdir, "capture")
    rc = capture(a.preset, NOTES[a.preset], prefix, legacy=a.legacy_image)
    if rc != 0:
        print(f"held_note_audible: REFUSED -- the CLI refused the capture (exit {rc})")
        return 2
    import verify_uart_bridge as vub
    status = vub.main(["--replay", prefix, "--replay-name", name, "--outdir", outdir])
    i2s = os.path.join(outdir, name, "uart_i2s.txt")
    if status == 2 or not os.path.exists(i2s):
        print("held_note_audible: NO VERDICT -- the replay did not run")
        return 2
    peak = 0
    for line in open(i2s):
        parts = line.split()
        try:
            peak = max(peak, abs(int(parts[1])))
        except (ValueError, IndexError):
            continue
    audible = peak >= MIN_PEAK
    verdict = status == 0 and audible
    rec = {"preset": a.preset, "note": NOTES[a.preset], "legacy_image": a.legacy_image,
           "replay_status": status, "i2s_peak_lsb": peak, "min_peak_lsb": MIN_PEAK,
           "verdict": "PASS" if verdict else "FAIL"}
    with open(os.path.join(outdir, "held_note_audible.json"), "w") as fh:
        json.dump(rec, fh, indent=2)
        fh.write("\n")
    print(f"held_note_audible[{name}]: replay {'PASS' if status == 0 else 'FAIL'}, "
          f"decoded I2S peak {peak} LSB (floor {MIN_PEAK}) -> {rec['verdict']}")
    if a.expect_fail:
        if not verdict:
            print("held_note_audible: control CAUGHT (the check failed as required)")
            return 0
        print("held_note_audible: CONTROL NOT CAUGHT")
        return 1
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())
