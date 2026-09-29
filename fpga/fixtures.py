#!/usr/bin/env python3
"""fpga/fixtures.py -- MUSIC, as a host actually sends it.

Each fixture is a `MusicHost` with its events already loaded: a patch, the
reference kit, notes from a keyboard, drum hits with the timed coefficient
sequences of contract 15.7.1, and knob turns. Nothing here pokes model state;
everything becomes a 48-bit transaction.

`short` exists because these are SIMULATED. `fpga/verify_fixture.py` runs the
whole thing through iverilog at the pins, and a bar at 118 bpm is 198 000
frames -- longer than the drum section's own hour-long bench. The short fixture
is the same music compressed to a few thousand frames, and it still contains
BOTH sequences at full length (192 and 2880 frames), because a fixture that
outran the sequence it exists to exercise would be the third unsatisfiable gate
this project has written.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import spi_host as sh                                   # noqa: E402
import drums_fx as dx                                   # noqa: E402
import voice_fx as vf                                   # noqa: E402
from spi_host import MusicHost, SR                      # noqa: E402

BD_WINDOW_FRAMES = int(round(dx.BD_ATTACK_MS * 1e-3 * SR))     # 192
TOM_DROP_FRAMES = int(round(dx.TOM_DROP_MS * 1e-3 * SR))       # 2880


def _coverage(host: MusicHost) -> dict:
    """What this fixture actually contains, counted from its write list. The
    bench checks this against its own claim instead of trusting it."""
    tags = [w.tag for w in host.w]
    return dict(writes=len(host.w),
                bd_attack=tags.count("bd-attack-hot") + tags.count("bd-attack-restore"),
                bd_hot=tags.count("bd-attack-hot"),
                bd_restore=tags.count("bd-attack-restore"),
                tom_bend=tags.count("tom-bend"),
                strikes=tags.count("stops-on"),
                gates=tags.count("gate") + tags.count("trig"),
                knobs=sum(1 for t in tags if t.startswith("knob-")))


def bar_808(*, short: bool = True, bpm: float = 118.0, kit: list = None,
            rest_s: float = 0.0) -> tuple:
    """THE MUSICAL FIXTURE: a bass line under a drum part, both through the
    link, with every host behaviour #81 names.

    Long form is the reference pattern at `bpm`. Short form is the same
    material with the rests taken out -- the hits keep their order and their
    accents, and both timed sequences keep their full real duration, so the
    tom's 60 ms bend and the BD's 4 ms window are exercised exactly as they
    would be on a board. `kit` is the kit image the target plays (None: the
    tree's `kit_808()`). Returns (host, frames, coverage).

    `rest_s` (long form only) puts a REST of that length between the two bars.
    The material does not change -- same hits, keys, knobs and accents -- only
    the silence in the middle, so a harness that cannot measure the result
    cannot measure this music. At 1 s that rest is 48000 frames, past the
    0x8000 a 16-bit frame log can step unambiguously, which is what
    `bar808-rest` exists to prove (fpga/verify_rolling_playback.py, #474)."""
    patch = vf.VoiceFx.patch_regs(cutoff=(320, 4200), q=0.62)
    host = MusicHost(patch=patch, kit=kit)
    host.load(0, dvol=0.45, bvol=0.45)

    if not short:
        hits = dx.pattern_hits(dx.PATTERN_808, bpm=bpm, bars=2, start_s=0.35)
        if rest_s:
            # the second bar, `rest_s` later. Only drums play there (the keys
            # and knobs below are all inside the first bar), so the rest is a
            # gap in the write stream and nothing else
            bar_s = 16 * 60.0 / bpm / 4.0
            hits = (dx.pattern_hits(dx.PATTERN_808, bpm=bpm, bars=1, start_s=0.35)
                    + dx.pattern_hits(dx.PATTERN_808, bpm=bpm, bars=1,
                                      start_s=0.35 + bar_s + rest_s))
        step = int(round(60.0 / bpm / 4.0 * SR))
        host.keys([(400, "on", 33), (400 + 6 * step, "off", 33),
                   (400 + 8 * step, "on", 40), (400 + 14 * step, "off", 40)])
        host.hits(hits)
        host.knob(400 + 4 * step, "cutoff", 900)
        host.knob(400 + 10 * step, "resonance", 0.92)
        host.knob(400 + 12 * step, "decay", 8.5)
        n = max(w.frame for w in host.w) + TOM_DROP_FRAMES + 2000
        return host, n, _coverage(host)

    # ---- the short fixture: the same events, 600 frames apart -----------------
    # 600 frames = 12.5 ms, room for a hit's setup (up to 20 writes, 0.8 ms) and
    # well inside the BD window's 192 frames so a second BD arrives while the
    # first is still ringing -- which is the case where the window's RESTORE has
    # to write back the IMAGE and not a recomputed preset.
    host.keys([(400, "on", 33), (2800, "off", 33), (3400, "on", 40), (5200, "off", 40)])
    host.hits([
        (600,  dx.BD, 1.4),          # + the 4 ms attack window
        (1000, dx.SD, 1.0),
        (1200, dx.CH, 0.6),
        (1400, dx.LT, 1.0),          # + the 60 ms pitch drop, 1400..4280
        (2000, dx.HT, 0.6),          # + a second pitch drop, accent-scaled, 2000..4880
        (2600, dx.BD, 1.0),          # a second window while the first still rings
        (3000, dx.CH, 1.0),
        (3800, dx.BD, 1.4),          # ... after the DECAY knob moved: the restore
        (4200, dx.CP, 1.0),          #     must write back the KNOB, not the kit
        (4600, dx.MT, 1.0),          # the middle tom: a third bend
        (5000, dx.CB, 1.0),
    ])
    host.knob(1800, "cutoff", 900)
    host.knob(2400, "resonance", 0.92)
    host.knob(3400, "decay", 8.5)            # the BD DECAY knob, before the 3800 hit
    host.knob(4800, "cutoff", 380)
    n = max(w.frame for w in host.w) + 900
    return host, n, _coverage(host)


def demo(*, bars: int = 2, bpm: float = 118.0, kit: list = None) -> tuple:
    """DEMO MODE: no keyboard attached. The pattern plays, the bass line walks
    and the three knobs sweep on their own, so a board with nothing plugged in
    still makes the instrument's own noise. `kit` as for `bar_808`."""
    patch = vf.VoiceFx.patch_regs(cutoff=(300, 4600), q=0.7)
    host = MusicHost(patch=patch, kit=kit)
    host.load(0)
    step = int(round(60.0 / bpm / 4.0 * SR))
    start = 400
    host.hits(dx.pattern_hits(dx.PATTERN_808, bpm=bpm, bars=bars, start_s=start / SR))
    walk = [33, 33, 40, 36, 33, 45, 40, 36]
    ev = []
    for b in range(bars):
        for i, nt in enumerate(walk):
            f = start + (b * 16 + i * 2) * step
            ev += [(f, "on", nt), (f + int(step * 1.6), "off", nt)]
    host.keys(ev)
    for b in range(bars):
        for i in range(4):
            f = start + (b * 16 + i * 4) * step
            host.knob(f, "cutoff", [340, 700, 1500, 620][i])
            host.knob(f + step, "resonance", [0.5, 0.75, 0.95, 0.7][i])
        host.knob(start + b * 16 * step + 8 * step, "decay", 3.0 + 5.0 * b)
    n = max(w.frame for w in host.w) + TOM_DROP_FRAMES + 2000
    return host, n, _coverage(host)


# Each takes an optional `kit=`: the image a host drives decides the kit
# (drums_fx.KITS_BY_REVISION); the default is the tree's.
FIXTURES = {"bar808": lambda kit=None: bar_808(short=True, kit=kit),
            "bar808-full": lambda kit=None: bar_808(short=False, kit=kit),
            # bar808-full with a 1 s rest between its bars: the CONTROL
            # fixture for a 16-bit frame log's unwrap (#474)
            "bar808-rest": lambda kit=None: bar_808(short=False, rest_s=1.0, kit=kit),
            "demo": demo}
