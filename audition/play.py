#!/usr/bin/env python3
"""A playable Minimoog-shaped monosynth, with the filter on the front panel.

    .venv/bin/python play.py

Plays from the computer keyboard, and from a MIDI device if one is connected
(auto-detected at start). The point of the thing is the filter: hold a note
with SPACE and sweep the cutoff, which is the setting where the Huovilainen
model and a naive one-tanh ladder actually diverge.

Keyboard, laid out like a piano octave:

      w e     t y u          sharps
     a s d f g h j k         C D E F G A B C

    z / x     octave down / up
    [ / ]     cutoff  down / up        (hold shift-ish: use { } for big steps)
    - / =     resonance down / up
    ; / '     drive down / up
    < / >     filter envelope amount down / up (shift + , / .)
    SPACE     hold the last note on (sustain) -- the filter-exploring switch
    1..5      presets: bass, lead, sweep, growl, whistle
    n m , .   drums: kick, snare, hat, open hat
    o p       drums: fm tom, fm bell
    G         glide on/off (capital G; lowercase g is a note)
    ?         print this again
    q         quit
"""
from __future__ import annotations
import math, os, sys, termios, threading, time, tty
from pathlib import Path
import numpy as np
import sounddevice as sd

sys.path.insert(0, str(Path(__file__).parent))
import rt
from rt import BLOCK, Mono, Drums, apply_preset
from dsp import SR

KEYS = {"a": 0, "w": 1, "s": 2, "e": 3, "d": 4, "f": 5, "t": 6,
        "g": 7, "y": 8, "h": 9, "u": 10, "j": 11, "k": 12}
DRUM = {"n": "k", "m": "s", ",": "h", ".": "H", "o": "t", "p": "b"}


class Player:
    def __init__(self):
        self.voice = Mono()
        self.drums = Drums()
        self.octave = 3
        self.hold = False
        self.preset = apply_preset(self.voice, "1")
        self.note_off_at = 0.0
        self.key_hold_s = 0.40
        self.running = True
        self.peak = 0.0
        self.glide_on = False
        self.lock = threading.Lock()

    # ---- audio ------------------------------------------------------------
    def callback(self, outdata, frames, time_info, status):
        with self.lock:
            if (not self.hold and self.voice.note is not None
                    and time.monotonic() > self.note_off_at):
                n = self.voice.note
                self.voice.note = None
                self.voice.held.clear()
                self.voice.aenv.gate_off(); self.voice.fenv.gate_off()
            y = self.voice.block(frames) * 0.55 + self.drums.block(frames) * 0.8
        y = np.tanh(y)                      # soft limit; never clips the DAC
        self.peak = max(self.peak * 0.9, float(np.abs(y).max()))
        outdata[:, 0] = y.astype(np.float32)

    # ---- input ------------------------------------------------------------
    def note_on(self, semis, vel=1.0):
        note = 12 * self.octave + semis
        with self.lock:
            self.voice.glide = 0.06 if self.glide_on else 0.0
            self.voice.note_on(note, vel)
            self.note_off_at = time.monotonic() + self.key_hold_s

    def handle(self, ch: str) -> bool:
        v = self.voice
        if ch == "q": return False
        if ch in KEYS:
            self.note_on(KEYS[ch]); return True
        if ch in DRUM:
            with self.lock: self.drums.trigger(DRUM[ch]); return True
        if ch in rt.PRESETS:
            with self.lock: self.preset = apply_preset(v, ch); return True
        if ch == "z": self.octave = max(0, self.octave - 1)
        elif ch == "x": self.octave = min(8, self.octave + 1)
        elif ch == "[": v.cutoff = max(30.0, v.cutoff / 1.15)
        elif ch == "]": v.cutoff = min(12000.0, v.cutoff * 1.15)
        elif ch == "{": v.cutoff = max(30.0, v.cutoff / 1.6)
        elif ch == "}": v.cutoff = min(12000.0, v.cutoff * 1.6)
        elif ch == "-": v.res = max(0.0, v.res - 0.04)
        elif ch == "=": v.res = min(1.12, v.res + 0.04)
        elif ch == ";": v.drive = max(0.2, v.drive / 1.15)
        elif ch == "'": v.drive = min(12.0, v.drive * 1.15)
        elif ch == "<": v.env_amt = max(0.0, v.env_amt - 400)
        elif ch == ">": v.env_amt = min(9000.0, v.env_amt + 400)
        elif ch == " ":
            self.hold = not self.hold
            if not self.hold:
                with self.lock:
                    v.note = None; v.held.clear()
                    v.aenv.gate_off(); v.fenv.gate_off()
        elif ch == "G" or ch == "\x07": self.glide_on = not self.glide_on
        elif ch == "?": print("\n" + __doc__)
        return True

    def status(self) -> str:
        v = self.voice
        bar = "#" * int(min(1.0, self.peak) * 18)
        return (f"\r  [{self.preset:7}] oct {self.octave}  "
                f"cut {v.cutoff:7.0f}Hz  res {v.res:.2f}  drive {v.drive:4.1f}  "
                f"env {v.env_amt:5.0f}  {'HOLD' if self.hold else '    '}  "
                f"|{bar:<18}|   ")

    # ---- MIDI -------------------------------------------------------------
    def start_midi(self):
        try:
            import mido
        except ImportError:
            return None
        names = mido.get_input_names()
        if not names:
            return None

        def on_msg(msg):
            v = self.voice
            if msg.type == "note_on" and msg.velocity > 0:
                with self.lock:
                    self.voice.glide = 0.06 if self.glide_on else 0.0
                    v.note_on(msg.note, msg.velocity / 127.0)
                    self.note_off_at = float("inf")     # MIDI gives a real note-off
            elif msg.type in ("note_off",) or (msg.type == "note_on" and msg.velocity == 0):
                with self.lock: v.note_off(msg.note)
            elif msg.type == "control_change":
                if msg.control in (74, 1):              # filter cutoff / mod wheel
                    v.cutoff = 40.0 * (10000.0 / 40.0) ** (msg.value / 127.0)
                elif msg.control == 71:                 # resonance
                    v.res = 1.10 * msg.value / 127.0
                elif msg.control == 73:
                    v.drive = 0.3 + 6.0 * msg.value / 127.0
        port = mido.open_input(names[0], callback=on_msg)
        return names[0], port


def main() -> int:
    p = Player()
    midi = p.start_midi()
    print(__doc__)
    print(f"  audio out : {sd.query_devices(sd.default.device[1])['name']} @ {SR} Hz, "
          f"{BLOCK} frames ({1000*BLOCK/SR:.1f} ms)")
    print(f"  midi in   : {midi[0] if midi else 'none -- computer keyboard only'}")
    print()
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        with sd.OutputStream(samplerate=SR, channels=1, dtype="float32",
                             blocksize=BLOCK, callback=p.callback, latency="low"):
            last = 0.0
            while True:
                r, _, _ = __import__("select").select([sys.stdin], [], [], 0.05)
                if r:
                    ch = sys.stdin.read(1)
                    if not p.handle(ch):
                        break
                if time.monotonic() - last > 0.06:
                    sys.stdout.write(p.status()); sys.stdout.flush(); last = time.monotonic()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        if midi: midi[1].close()
        print("\n  bye")
    return 0


if __name__ == "__main__":
    sys.exit(main())
