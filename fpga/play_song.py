#!/usr/bin/env python3
"""Play a song on the Arty over the USB-UART link.

    python3 fpga/play_song.py --port /dev/cu.usbserial-XXXX1            # Ode to Joy
    python3 fpga/play_song.py --port ... --song twinkle --bpm 100
    python3 fpga/play_song.py --port ... --notes "C3:1 E3:1 G3:2 R:1 C4:4"
    python3 fpga/play_song.py --port ... --song-file tune.txt
    python3 fpga/play_song.py --dry-run                  # the byte schedule, no board
    python3 fpga/play_song.py --render song.wav          # the model, no board

A SONG is a line of `NOTE:BEATS` tokens: NOTE is a name with octave (`C3`,
`F#3`, `Bb2`) or `R` for a rest, BEATS a positive number (`0.5`, `1`, `1.5`).
A song file holds the same tokens across lines; `#` at the start of a line or
after a space starts a comment (so `F#3` stays a note), and an
optional first line `bpm: 120` sets the tempo.

HOW IT PLAYS. Nothing here is new delivery code; it is uart_host's, used as is:

  * the patch goes first as LIVE writes (known-state preamble for the
    revision-14 images, the voice image, the mixer weights), exactly what
    `uart_host.py run --image r1` sends;
  * every note-on and note-off becomes the model's own KeyHost writes
    (pitch increments, note track, gate), stamped with the audio FRAME it
    belongs to and sent as a SCHEDULED EVENT. The FPGA fires each one in
    exactly its frame; the Mac never sleeps to keep time. A song longer than
    one planning window rolls in batches the 64-deep queue can hold
    (`Bridge.run` -> `run_rolling`).
  * before the first byte leaves, every write is checked against the release
    domain (fpga/release/qualified_domain.py): a note the patch cannot play in
    range is REFUSED, never re-pitched. `--transpose` moves the whole song.

`--image` is the board's image. The default here is `r1`, which also matches
the no-DAC demo bitstream (both are contract revision 14); uart_host's own
default, `release`, is the older revision-11 image. `--render` plays the same
writes through the integer model (model/synth_top_model.py) into a WAV -- what
the chip computes, before any DAC or filter.

Exit 0 played (every packet ACKed, no device drops or errors), 1 the device
reported a problem, 2 refused.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in ("fpga", "model", "rtl-sketch"):
    _d = os.path.join(ROOT, _p)
    if _d not in sys.path:
        sys.path.insert(0, _d)

import uart_host as uh                                      # noqa: E402

SONGS = {
    # Beethoven, Symphony No. 9, "Ode to Joy" (public domain)
    "ode": (120, """
        E3:1 E3:1 F3:1 G3:1  G3:1 F3:1 E3:1 D3:1  C3:1 C3:1 D3:1 E3:1  E3:1.5 D3:0.5 D3:2
        E3:1 E3:1 F3:1 G3:1  G3:1 F3:1 E3:1 D3:1  C3:1 C3:1 D3:1 E3:1  D3:1.5 C3:0.5 C3:2
    """),
    # "Twinkle, Twinkle, Little Star" (traditional)
    "twinkle": (110, """
        C3:1 C3:1 G3:1 G3:1  A3:1 A3:1 G3:2  F3:1 F3:1 E3:1 E3:1  D3:1 D3:1 C3:2
        G3:1 G3:1 F3:1 F3:1  E3:1 E3:1 D3:2  G3:1 G3:1 F3:1 F3:1  E3:1 E3:1 D3:2
        C3:1 C3:1 G3:1 G3:1  A3:1 A3:1 G3:2  F3:1 F3:1 E3:1 E3:1  D3:1 D3:1 C3:2
    """),
}

_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_TOKEN = re.compile(r"^(R|[A-Ga-g][#b]?-?\d+):(\d+(?:\.\d+)?)$")


def note_number(name: str) -> int:
    """MIDI note for `C3`-style names; C4 = 60 (middle C)."""
    m = re.match(r"^([A-Ga-g])([#b]?)(-?\d+)$", name)
    if not m:
        raise ValueError(f"not a note name: {name!r}")
    pc = _PC[m[1].upper()] + {"#": 1, "b": -1, "": 0}[m[2]]
    return 12 * (int(m[3]) + 1) + pc


def parse_song(text: str) -> tuple:
    """(bpm or None, [(midi or None, beats)]) from the NOTE:BEATS notation."""
    bpm, notes = None, []
    for line in text.splitlines():
        # a comment is `#` at the start or after whitespace; `F#3` is a sharp
        line = re.sub(r"(^|\s)#.*$", "", line).strip()
        if not line:
            continue
        m = re.match(r"^bpm\s*:\s*(\d+(?:\.\d+)?)$", line, re.I)
        if m:
            bpm = float(m[1])
            continue
        for tok in line.split():
            t = _TOKEN.match(tok)
            if not t:
                raise ValueError(f"bad token {tok!r}: expected NOTE:BEATS or R:BEATS")
            beats = float(t[2])
            if beats <= 0:
                raise ValueError(f"bad token {tok!r}: beats must be positive")
            notes.append((None if t[1] == "R" else note_number(t[1]), beats))
    if not any(n is not None for n, _ in notes):
        raise ValueError("the song has no notes")
    return bpm, notes


def key_events(notes: list, bpm: float, *, gate: float, transpose: int = 0) -> tuple:
    """[(frame, 'on'|'off', midi)] and the song's length in frames. Each note
    sounds for `gate` of its duration, so a repeated note re-strikes."""
    frames_per_beat = uh.SR * 60.0 / bpm
    t, out = 0.0, []
    for midi, beats in notes:
        dur = beats * frames_per_beat
        if midi is not None:
            on, off = round(t), round(t + dur * gate)
            if off <= on:
                raise ValueError("a note is shorter than one frame at this tempo")
            out += [(on, "on", midi + transpose), (off, "off", midi + transpose)]
        t += dur
    return out, round(t)


def song_writes(events: list, preset: str | None) -> list:
    """(frame, flag, sec, addr, data) through the model's own KeyHost. Writes
    that share a frame are spread one frame apart, in order, as uart_host's
    phrase_events does: the device's two write slots then deliver them exactly."""
    import spi_host as sh
    import synth_top_model as stm
    import voice_fx as vf
    out, prev = [], None
    for f, op, *args in vf.KeyHost().writes(events, uh.preset_regs(preset)):
        if op == "INC":
            k, v, jump = args
            w = (1 if jump else 0, sh.SEC_VOICE, stm.A_INC + k, v)
        elif op == "TRACK":
            w = (0, sh.SEC_VOICE, stm.A_TRACK, args[0])
        elif op == "GATE":
            w = (0, sh.SEC_VOICE, stm.A_GATE_ON if args[0] else stm.A_GATE_OFF, 0)
        else:
            raise ValueError(f"unexpected KeyHost op {op!r}")
        due = f if prev is None else max(f, prev + 1)
        out.append((due, *w))
        prev = due
    return out


def build_commands(timed: list, *, preset: str | None, image: str) -> list:
    """uart_host's command list: the patch as live writes, the song as events."""
    cmds = []
    if image in uh.KNOWN_STATE_IMAGES:
        cmds += [("write", *w) for w in uh.known_state_preamble()]
    cmds += [("write", *w) for w in uh.voice_image_writes(preset)]
    cmds += [("write", *w) for w in uh.voice_mixer_writes(preset)]
    cmds += [("event", *w) for w in timed]
    return cmds


def qualify(cmds: list, notes: list, *, preset: str | None, image: str) -> None:
    """Refuse anything outside the release domain; raises qualified_domain.Rejected."""
    sys.path.insert(0, os.path.join(ROOT, "fpga", "release"))
    import qualified_domain as qd
    regs = uh.preset_regs(preset)
    for midi in sorted({n for n in notes if n is not None}):
        qd.check_note(midi, regs)
    uh.qualify(cmds, preset=preset, note=None, image_sent=True, image=image)


def render(cmds: list, length: int, path: str, *, image: str) -> dict:
    """The integer model plays the same writes: live ones at frame 0 in order,
    events at START + due. Writes a 48 kHz mono 16-bit WAV."""
    import numpy as np
    from scipy.io import wavfile
    import synth_top_model as stm
    start = 64                                     # after the image, like the device
    writes = [(0, *c[1:]) for c in cmds if c[0] == "write"]
    writes += [(start + c[1], *c[2:]) for c in cmds if c[0] == "event"]
    n = start + length + uh.SR                     # one second of release tail
    m = stm.SynthTopModel(oversample_2x=True, filter_2x=True).run(writes, n)
    s = np.asarray(m["sample"], dtype=np.int16)
    wavfile.write(path, uh.SR, s)
    return {"frames": n, "peak": int(np.abs(s.astype(np.int32)).max())}


def main(argv=None, *, bridge_factory=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__.split("\n", 2)[2])
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--song", choices=sorted(SONGS), default="ode")
    src.add_argument("--notes", help="the song inline, e.g. \"C3:1 E3:1 G3:2\"")
    src.add_argument("--song-file", help="a file of NOTE:BEATS tokens")
    ap.add_argument("--bpm", type=float, default=None, help="tempo (default: the song's, else 120)")
    ap.add_argument("--gate", type=float, default=0.85,
                    help="fraction of each note that sounds (default 0.85; 1.0 = legato)")
    ap.add_argument("--transpose", type=int, default=0, help="semitones")
    ap.add_argument("--preset", default=None, help="named preset (uart_host --preset)")
    ap.add_argument("--image", default="r1", choices=sorted(uh.IMAGE_REVISION),
                    help="the board's image (default r1: the R1 image and the demo bitstream)")
    ap.add_argument("--port", default=os.environ.get("UART_BRIDGE_PORT", ""))
    ap.add_argument("--baud", type=int, default=uh.DEFAULT_BAUD)
    ap.add_argument("--dry-run", action="store_true", help="print the schedule; no board")
    ap.add_argument("--render", metavar="WAV", help="render through the model; no board")
    a = ap.parse_args(argv)

    try:
        if a.notes:
            bpm, notes = parse_song(a.notes)
        elif a.song_file:
            with open(a.song_file) as fh:
                bpm, notes = parse_song(fh.read())
        else:
            song_bpm, text = SONGS[a.song]
            bpm, notes = parse_song(text)
            bpm = bpm or song_bpm
        bpm = a.bpm or bpm or 120.0
        if not 0 < a.gate <= 1:
            raise ValueError("--gate must be in (0, 1]")
        events, length = key_events(notes, bpm, gate=a.gate, transpose=a.transpose)
        timed = song_writes(events, a.preset)
        cmds = build_commands(timed, preset=a.preset, image=a.image)
        qualify(cmds, [n + a.transpose if n is not None else None for n, _ in notes],
                preset=a.preset, image=a.image)
    except (ValueError, OSError) as exc:
        print(f"play_song: REFUSED -- {exc}", file=sys.stderr)
        return 2
    except Exception as exc:                      # qualified_domain.Rejected
        if type(exc).__name__ != "Rejected":
            raise
        print(f"play_song: REFUSED -- outside the qualified release domain: {exc}",
              file=sys.stderr)
        return 2
    n_notes = sum(1 for n, _ in notes if n is not None)
    print(f"play_song: {n_notes} notes, {length / uh.SR:.1f} s at {bpm:g} bpm, "
          f"{len(timed)} scheduled events after {len(cmds) - len(timed)} patch writes")

    if a.render:
        r = render(cmds, length, a.render, image=a.image)
        print(f"play_song: rendered {r['frames'] / uh.SR:.1f} s to {a.render}, "
              f"peak {r['peak']} ({r['peak'] / 32768:.0%} of full scale)")
        if r["peak"] == 0:
            print("play_song: FAIL -- the model rendered silence", file=sys.stderr)
            return 1
        return 0

    if a.dry_run:
        try:
            if uh.rolling_needed(cmds):
                rows, windows = uh.plan_rolling_virtual(cmds, baud=a.baud)
                verdict = f"FEASIBLE: {windows} rolling windows, each preflighted"
            else:
                rows = uh.plan_show(cmds, baud=a.baud)
                v = uh.preflight(rows, baud=a.baud)
                if v["verdict"] != "FEASIBLE":
                    raise uh.Refused(v["reason"])
                verdict = f"FEASIBLE: {v['reason']}"
        except (uh.Refused, ValueError) as exc:
            print(f"play_song: REFUSED -- {exc}", file=sys.stderr)
            return 2
        print(uh.render_plan(rows))
        print(f"play_song: {len(rows)} packets; preflight {verdict}")
        return 0

    if not a.port:
        print("play_song: REFUSED -- no --port (or UART_BRIDGE_PORT); "
              "--dry-run or --render work without a board", file=sys.stderr)
        return 2
    open_bridge = bridge_factory or uh.Bridge
    try:
        bridge = open_bridge(a.port, a.baud)
        # a known-state image refuses to start over an earlier session's queue
        bridge.require_idle = a.image in uh.KNOWN_STATE_IMAGES
        rows = bridge.run(cmds, baud=a.baud, quiet=True)
    except uh.Refused as exc:
        print(f"play_song: REFUSED -- {exc}", file=sys.stderr)
        return 2
    print("play_song: all events queued on the device; playing")
    end = max(r.apply_frame for r in rows)
    bridge.wait_until(end + 64, timeout_s=length / uh.SR + 30)
    acked = bridge.acks_seen - bridge._run_ack_base
    if acked < bridge._run_acks_expected:
        print(f"play_song: FAIL -- {bridge._run_acks_expected - acked} of "
              f"{bridge._run_acks_expected} packets were never ACKed", file=sys.stderr)
        return 1
    st = bridge.status()
    print(f"play_song: done; device frame {st.frame}, drops {st.drops}, errs {st.errs}")
    if st.drops or st.errs or (st.flags & 0x0F):
        print("play_song: FAIL -- the device reported dropped or corrupted traffic",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
