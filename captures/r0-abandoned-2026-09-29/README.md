# Abandoned R0 capture attempt, 2026-09-29

**This is NOT a T-PHYSICAL capture bundle and must not be analysed as one.**
`tools/r0_capture.py` will refuse it, correctly: there is no `session.json`, no
`takes/` and no `analysis.json`. It is deliberately not at `captures/r0`, the
path `docs/capture-r0.md` reserves for a real bundle, so that it cannot be
mistaken for one.

## What it is

The programming transcript and host logs from the point in the 2026-09-29
first-audio session where **R0 was the wrong image for the bench hardware**, and
we did not yet know it. Kept because the claims made elsewhere rest on it.

- `detect.txt` -- `openFPGALoader --detect`, scoped to the Digilent FT2232H
  serial. Names `xc7a100t`, exit 0. Two FT2232H devices were on the bus (the
  Arty and a CJMCU-2232HL), which is why the scoping was needed.
- `program.txt` -- `release_manifest` BOUND, the bitstream's SHA-256
  (`a66c9349…4cb95`, R0 as `fpga/release/baseline-2025.1.json` binds it), the
  programmer version, and a successful load (`done 1`, exit 0).
- `host/first-note.*` -- `run --note 45 --fixture none`, the documented first
  playback. Delivered clean: `drops 0, errs 0`.
- `host/demo-listen.*` -- `run --fixture demo`, 4 s of voice and drums, also
  delivered clean across 86 rolling windows.

## Why it was abandoned

**Every command above succeeded and produced no sound at all.** R0 drives
`JA1=BCK, JA2=WSEL, JA3=DIN` and nothing on JA4, while the bench's PCM5102
breakout plugs straight into JA's top row and expects `SCK/BCK/DIN/LCK`. No word
clock ever reached the DAC. The session moved to the #408 sd-demo image, which
carries the direct-plug layout, and later found a second and independent cause:
the breakout's **XSMT floated**, hard-muting the part (#460).

## Why it is worth keeping

These logs are the record of a correct instrument in a wrong state. While they
were being produced, `fpga/release/held_note_audible.py` reported a decoded I2S
peak of **12,760 LSB** (floor 1,024) for the very bytes in `host/first-note.*`,
with its `--legacy-image` control correctly returning 0. So the host was right,
the device was right, the bytes were right, the RTL was right -- and the room was
silent. That is the whole failure mode `docs/failure-modes.md` describes, caught
here by a control rather than by inspection.

No `.wav` files exist: nothing was ever recorded, because there was nothing to
record.
