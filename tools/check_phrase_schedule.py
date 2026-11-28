#!/usr/bin/env python3
"""Applied versus intended note times for an M5A/M5B SPI-to-I2S phrase (plan101 §4).

The phrase bench serialises each event's writes ahead of the note and reserves
a margin for them. When extra writes are added (the saw-drive override adds
K, GAIN, OGAIN), the GATE_ON may land late -- or, with a larger reserve,
early. A transport offset must not be read as an envelope change, so this
compares the frame each GATE_ON / GATE_OFF was APPLIED at the pins (the
bench's own write log, `top_wrs_*.txt`: applied frame, flag, section,
address, data, predicted frame) with the musical schedule the frozen manifest
states, both relative to the phrase's first applied write.

Exit status: 0 with the offsets printed and, when `--out` is given, the record
written. 2 (REFUSED, `tools/exit_contract.py`) when the `--out` destination
cannot hold the record -- refused before a run is read where the path says so
-- or when the write fails after the offsets (#2098). The input refusals in
this file are `SystemExit("REFUSED: ...")`, which exits 1; that is the older
convention and it is not changed here.
"""
from __future__ import annotations

import argparse
import glob
import json
import pathlib
import sys

A_GATE_ON, A_GATE_OFF = 0x20, 0x21
SR = 48_000


def applied(outdir: pathlib.Path):
    f = sorted(glob.glob(str(outdir / "top_wrs_*.txt")))
    if len(f) != 1:
        raise SystemExit(f"REFUSED: expected one write log in {outdir}, found {len(f)}")
    rows = [list(map(int, l.split())) for l in open(f[0]) if l.strip()]
    on = [r[0] for r in rows if r[3] == A_GATE_ON]
    off = [r[0] for r in rows if r[3] == A_GATE_OFF]
    if any(r[0] != r[5] for r in rows):
        raise SystemExit("REFUSED: an applied frame differs from its pin-side prediction")
    return rows[0][0], on, off


def intended(manifest: pathlib.Path):
    m = json.loads(manifest.read_text())
    cursor, on, off = 0.0, [], []
    for seg in m["timeline"]["segments"]:
        for ev in seg["midi_events"]:
            t = cursor + float(ev["on_s"])
            on.append(t)
            off.append(t + float(ev["gate_s"]))
        cursor += float(seg["duration_s"]) + float(m["timeline"]["segment_silence_s"])
    return on, off


def _unwritable_out(name) -> str | None:
    """Why the `--out` destination cannot hold the record, or None.

    A missing parent directory is created here, as `fpga/selected_preset.py`
    creates `--out`'s. Called before a run is read (#2098): a destination
    that is a directory, or whose parent cannot be made, is refused at once
    rather than by `OSError` from the write after every offset was computed
    and printed -- which exited 1 with a traceback.

    What this cannot see -- a parent that exists but is not writable, a disk
    that fills -- is left to the guard around the write.
    """
    out = pathlib.Path(name)
    try:
        is_dir = out.is_dir()
        if not is_dir:
            out.parent.mkdir(parents=True, exist_ok=True)
    except (OSError, ValueError) as exc:
        return f"--out {str(name)!r}: no record can be written there: {exc}"
    if is_dir:
        return f"--out {str(name)!r} names an existing directory; name the record file"
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--manifest", type=pathlib.Path, required=True)
    ap.add_argument("runs", nargs="+", help="label=outdir")
    ap.add_argument("--out", type=pathlib.Path, default=None,
                    help="write the offsets here (a missing parent directory is created)")
    a = ap.parse_args(argv)
    if a.out is not None:
        unwritable = _unwritable_out(a.out)
        if unwritable:
            print(f"REFUSED: {unwritable}", file=sys.stderr)
            return 2
    ion, ioff = intended(a.manifest)
    res = {}
    for spec in a.runs:
        label, d = spec.split("=", 1)
        first, on, off = applied(pathlib.Path(d))
        if len(on) != len(ion) or len(off) != len(ioff):
            raise SystemExit(f"REFUSED: {label} has {len(on)}/{len(off)} gates, schedule {len(ion)}/{len(ioff)}")
        # offsets relative to the FIRST event, so a constant lead-in is not counted
        base_on = on[0] - round(ion[0] * SR)
        don = [on[i] - round(ion[i] * SR) - base_on for i in range(len(on))]
        doff = [off[i] - round(ioff[i] * SR) - base_on for i in range(len(off))]
        res[label] = {"gate_on_offset_frames": don, "gate_off_offset_frames": doff,
                      "worst_abs_frames": max(abs(x) for x in don + doff)}
        print(f"{label:24s} GATE_ON offsets (frames, vs the schedule, first event aligned): {don}; "
              f"GATE_OFF: {doff}")
    if a.out:
        try:
            a.out.write_text(json.dumps(res, indent=1) + "\n")
        except OSError as exc:
            # The offsets are on stdout above; the record is their evidence
            # (#2098).
            print(f"REFUSED: offsets printed above; --out record not written: {exc}", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
