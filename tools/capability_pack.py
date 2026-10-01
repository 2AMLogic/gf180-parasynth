#!/usr/bin/env python3
"""A small comparison pack, gated on the CAPABILITIES it consumes (#136).

    python tools/capability_pack.py --list                 what the pack is, and
                                                           whether its rig is
                                                           qualified for it
    python tools/capability_pack.py --gate                 the capability gate alone
    python tools/capability_pack.py --run --out report.json  render and measure

    exit 0  OK       the pack ran
    exit 1  FAIL     the gate says this rig may not be used for this pack
    exit 2  REFUSED  a precondition is unmet -- no plugin host on this box

WHY THIS FILE EXISTS
--------------------
#136's "immediate payoff", made concrete. `RIG_VERDICTS` used to hold one
boolean per reference, so Mini V3's uncalibrated ENVELOPE knobs read as a
blocker on every Mono case -- and the review that caught it pointed out that
**two waveforms at two pitches, plus an open/closed filter pair, needs no
envelope timing at all.** That pack was parked behind a calibration it does not
depend on.

So this is that pack, and the point of it being a file rather than a sentence is
that the gate is EXECUTABLE. `gate()` asks
`refprofile.require_capability(rig, host, cap)` for each capability the pack
consumes, which means:

  * the pack declares `pitch`, `waveform` and `sustained_filter`, and Mini V3 is
    recorded qualified for all three, so the gate PASSES -- on a table where the
    rig's overall verdict is False. Before #136 there was no way to express that
    and no way to run this pack;
  * a pack that declared `envelope_timing` would be refused, with a refusal that
    names envelope timing rather than the rig. `--gate --pack
    miniv3-envelope-timing-NOT-QUALIFIED` is that control, and it is in the file
    on purpose: a gate nobody has seen reject anything is a gate nobody should
    trust.

WHAT THIS FILE CANNOT DO, STATED RATHER THAN IMPLIED
----------------------------------------------------
**It cannot produce reference numbers on this fleet.** `--run` needs
`dawdreamer` and the Mini V3 bundle, and almost no host here has either; on
those hosts it REFUSES (exit 2) and names the missing precondition. REFUSED is
not FAIL and must not be read as one: the pack is unblocked, the apparatus is
absent, and those are different facts.

What IS validated everywhere, including here, is the half that does the
measuring. `measure_oscillator` and `measure_filter_pair` are exercised in
`tools/test_capability_pack.py` against closed-form signals whose answers are
known independently of this repository -- a mathematical saw and rectangle at a
commanded f0, and a one-pole at two corners an octave apart. An instrument
calibrated on our own model is not validated; these two are calibrated on
arithmetic.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import math
import pathlib
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "audition"))

import audio_measure as am                                          # noqa: E402
import refprofile as rp                                             # noqa: E402
from dsp import SR, note_hz                                         # noqa: E402

OK, FAIL, REFUSED = 0, 1, 2


@dataclasses.dataclass(frozen=True)
class Step:
    """One render in the pack. `kind` is what it is FOR, which is what decides
    which capability it consumes -- not what it looks like."""
    id: str
    kind: str                   # "oscillator" | "filter"
    why: str
    note: int | None = None
    wave: str | None = None
    cutoff_knob: float | None = None
    res: float = 0.0


@dataclasses.dataclass(frozen=True)
class Pack:
    name: str
    rig: str
    host: str
    requires: tuple
    steps: tuple
    why: str


#: Two waveforms at two pitches, plus an open/closed filter pair.
#:
#: **The notes are an octave apart and are not either endpoint of the rig's
#: range.** MIDI 36 (65.41 Hz) and 48 (130.81 Hz) are the two the Range defect
#: was measured at -- Mini V3 answered 48 with 65.42 Hz until parameter 45 was
#: written -- so a regression in the one capability this pack most depends on
#: lands on the exact note that caught it the first time.
#:
#: **The two waveforms are `saw` and `square`** because `waveform_id` decides the
#: family by COUNTING THE JUMPS in the averaged cycle: a ramp has one and a
#: rectangle has two. A saw/triangle pair would differ by a smoothness
#: descriptor rather than by a countable feature, so a confusion between them
#: would be a quieter failure.
#:
#: **The filter pair is one knob apart on one patch**, open and nearly closed at
#: zero resonance, and the measurement is the centroid RATIO rather than either
#: centroid. Mini V3's cutoff knob is a bare 0..1 whose taper is unknown, so a
#: ratio between two positions is a statement the rig can support and an
#: absolute corner frequency at a knob position is not (that needs
#: `reference_compare.calibrate_knob`, which is a separate and heavier run).
MINIV3_PACK = Pack(
    name="miniv3-waveform-filter",
    rig="miniv3", host="dawdreamer",
    requires=("pitch", "waveform", "sustained_filter"),
    why="two waveforms at two pitches and an open/closed filter pair -- the pack "
        "#136's review named, which needs no envelope timing and was parked "
        "behind the envelope-knob calibration anyway because one boolean per rig "
        "could not say so",
    steps=(
        Step("saw-36", "oscillator", wave="saw", note=36,
             why="a ramp at 65.41 Hz: one jump per cycle, low enough that nine "
                 "harmonics are well inside the band waveform_id reads"),
        Step("saw-48", "oscillator", wave="saw", note=48,
             why="the same ramp an octave up, at the note Mini V3's Range "
                 "default got wrong (65.42 Hz for a commanded 130.81)"),
        Step("square-36", "oscillator", wave="square", note=36,
             why="a rectangle at the same pitch as saw-36: two jumps per cycle, "
                 "and the duty is MEASURED rather than assumed to be 50 %"),
        Step("square-48", "oscillator", wave="square", note=48,
             why="the rectangle an octave up, so the two-waveform and "
                 "two-pitch axes are crossed rather than sampled on a diagonal"),
        Step("filter-open", "filter", note=36, cutoff_knob=1.0, res=0.0,
             why="the cutoff knob at its maximum, zero resonance, on the saw at "
                 "65.41 Hz -- the brightest the patch gets"),
        Step("filter-closed", "filter", note=36, cutoff_knob=0.30, res=0.0,
             why="the same patch with the knob nearly closed. The pair's "
                 "centroid RATIO is the measurement; neither absolute centroid "
                 "is, because the knob has no units"),
    ))

#: The control, and it ships. A pack that asks for a capability its rig does not
#: have must be refused, and the refusal must name the capability -- a gate
#: nobody has watched reject anything is a gate nobody should trust. Same rig,
#: same host, one capability added.
MINIV3_ENVELOPE_PACK = dataclasses.replace(
    MINIV3_PACK,
    name="miniv3-envelope-timing-NOT-QUALIFIED",
    requires=("pitch", "waveform", "sustained_filter", "envelope_timing"),
    why="NOT RUNNABLE, and in this file deliberately: the same pack with a "
        "commanded envelope time added. It must be refused, by capability, and "
        "it is the control for the gate above")

PACKS = {p.name: p for p in (MINIV3_PACK, MINIV3_ENVELOPE_PACK)}


# ===========================================================================
# the gate
# ===========================================================================
def gate(pack: Pack) -> list:
    """Every capability this pack consumes, with its verdict -- or a REFUSAL
    naming the first capability the rig is not qualified for.

    This is the whole #136 change seen from a caller: the question asked is "is
    this rig qualified for what this pack needs", never "is this rig qualified"."""
    out = []
    for cap in pack.requires:
        v = rp.require_capability(pack.rig, pack.host, cap)     # raises, by name
        out.append({"capability": cap, "qualified": v["qualified"],
                    "why": v["why"]})
    return out


def not_required(pack: Pack) -> list:
    """The capabilities this pack does NOT consume, with their verdicts. Printed
    beside the gate because that list is the finding: Mini V3's envelope timing
    is `NO` and the pack runs anyway, which is what a per-capability verdict buys
    and what one boolean per rig could not express."""
    out = []
    for cap in rp.CAPABILITIES:
        if cap in pack.requires:
            continue
        out.append({"capability": cap,
                    "verdict": rp.verdict_word(
                        rp.capability_verdict(pack.rig, pack.host, cap))})
    return out


# ===========================================================================
# the measurements -- validated on closed-form signals, no plugin in the path
# ===========================================================================
def measure_oscillator(y, note: int, sr: int = SR) -> dict:
    """What waveform is this, at what fundamental.

    The fundamental is MEASURED and the waveform is identified at the measured
    value, never at the commanded one. That order is not a nicety: Mini V3 plays
    +0.14 cents sharp, which is inaudible and takes the per-period residual from
    0.6 % to 25 % over a half-second record, so every Mini V3 row refuses if the
    commanded f0 is used (`audio_measure.refine_f0`'s docstring). And on an
    octave-down record `waveform_id` at the commanded f0 reports a period
    residual instead of the octave, which is a wrong reason rather than no
    answer."""
    y = np.asarray(y, dtype=np.float64).ravel()
    want = float(note_hz(int(note)))
    out = {"note": int(note), "commanded_hz": round(want, 3),
           "peak": round(float(am.peak(y)), 6)}
    f0 = am.refine_f0(y, want, sr=sr)
    if not f0.ok:
        out["refused"] = f"no usable fundamental near {want:.2f} Hz: {f0.reason}"
        return out
    out["f0_hz"] = round(float(f0.value), 3)
    out["cents_from_commanded"] = round(
        1200.0 * math.log2(float(f0.value) / want), 2)
    wid = am.waveform_id(y, float(f0.value), sr=sr)
    if not wid.ok:
        out["refused"] = f"no waveform identified: {wid.reason}"
        return out
    out["waveform"] = wid.label
    return out


def measure_filter_pair(y_open, y_closed, sr: int = SR) -> dict:
    """How much darker the closed position is than the open one, as a centroid
    RATIO.

    A ratio and not two corner frequencies, because Mini V3's cutoff knob is a
    bare 0..1 with an unknown taper: a corner in Hz at a knob position needs
    `reference_compare.calibrate_knob`, and quoting one without it would publish
    a frequency for a knob. The ratio is a statement about the two positions
    only, which is what the pack actually renders."""
    co = am.spectral_centroid(np.asarray(y_open, dtype=np.float64).ravel(), sr=sr)
    cc = am.spectral_centroid(np.asarray(y_closed, dtype=np.float64).ravel(), sr=sr)
    out = {"centroid_open_hz": round(float(co), 2),
           "centroid_closed_hz": round(float(cc), 2)}
    if not (cc > 0):
        out["refused"] = "the closed position has no measurable centroid"
        return out
    out["centroid_ratio_open_over_closed"] = round(float(co) / float(cc), 4)
    out["darker_when_closed"] = bool(cc < co)
    return out


# ===========================================================================
# rendering -- the half that needs the apparatus
# ===========================================================================
def build_rig(pack: Pack):
    """The rig, or a REFUSAL that names the missing precondition. Never a skip:
    a pack that quietly reports nothing on a host with no plugin is
    indistinguishable from one that measured nothing interesting."""
    if pack.host != "dawdreamer":
        raise rp.Refused(f"{pack.name} is scoped to host {pack.host!r} and this "
                         f"runner only builds dawdreamer rigs")
    try:
        import dawdreamer                                        # noqa: F401
    except ImportError as e:
        raise rp.Refused(
            f"no plugin host on this box ({e}), so the pack cannot be rendered "
            f"here. This is REFUSED and not FAIL: the capability gate passes "
            f"(run --gate to see it), the apparatus is absent. Most hosts in this "
            f"fleet have neither dawdreamer nor the plugins") from e
    import reference_rigs as rr
    builders = {"miniv3": rr.MiniV3Rig}
    if pack.rig not in builders:
        raise rp.Refused(f"no builder here for rig {pack.rig!r}")
    return builders[pack.rig]()


def render(pack: Pack, dev) -> dict:
    """Render every step on an already-built rig. Split from `build_rig` so the
    apparatus refusal and the measurement are separable, and so a test can drive
    this with a stub."""
    audio = {}
    for s in pack.steps:
        if s.kind == "oscillator":
            audio[s.id] = dev.osc_tone(s.wave, s.note)
        elif s.kind == "filter":
            dev.set_point(s.cutoff_knob, s.res)
            audio[s.id] = dev.osc_tone("saw", s.note)
        else:                                                # pragma: no cover
            raise rp.Refused(f"unknown step kind {s.kind!r}")
    return audio


def measure(pack: Pack, audio: dict) -> dict:
    """Every step's measurement, plus the filter pair's ratio."""
    res: dict = {"pack": pack.name, "rig": pack.rig, "host": pack.host,
                 "requires": list(pack.requires), "steps": {}}
    for s in pack.steps:
        if s.kind != "oscillator":
            continue
        res["steps"][s.id] = measure_oscillator(audio[s.id], s.note)
    if "filter-open" in audio and "filter-closed" in audio:
        res["filter_pair"] = measure_filter_pair(audio["filter-open"],
                                                 audio["filter-closed"])
    return res


# ===========================================================================
# CLI
# ===========================================================================
def cmd_list(pack: Pack) -> int:
    print(f"pack      {pack.name}")
    print(f"rig       {pack.rig} under {pack.host}")
    print(f"why       {pack.why}")
    print()
    print(f"rig-level verdict for {pack.rig}: "
          f"{rp.verdict_word(rp.RIG_VERDICTS[pack.rig]['qualified'])}  "
          f"-- and it is NOT what gates this pack")
    print()
    print(f"{'consumes':<20}{'verdict':<12}why")
    print("-" * 100)
    for cap in pack.requires:
        q = rp.capability_verdict(pack.rig, pack.host, cap)
        why = (rp.RIG_VERDICTS[pack.rig]["capability_why"] or {}).get(cap, "")
        print(f"{cap:<20}{rp.verdict_word(q):<12}{why[:60]}")
    print()
    print(f"{'does not consume':<20}{'verdict':<12}")
    print("-" * 100)
    for row in not_required(pack):
        print(f"{row['capability']:<20}{row['verdict']:<12}")
    print()
    print(f"{'step':<16}{'kind':<13}what")
    print("-" * 100)
    for s in pack.steps:
        print(f"{s.id:<16}{s.kind:<13}{s.why[:66]}")
    print("-" * 100)
    print(f"{len(pack.steps)} renders")
    return OK


def cmd_gate(pack: Pack) -> int:
    try:
        rows = gate(pack)
    except rp.Refused as why:
        print(f"GATE REFUSES  {why}")
        return FAIL
    for r in rows:
        print(f"OK  {pack.rig}/{pack.host} is qualified for {r['capability']}")
    print(f"OK -- {pack.name} may run against {pack.rig} under {pack.host}. "
          f"Its rig-level verdict is "
          f"{rp.verdict_word(rp.RIG_VERDICTS[pack.rig]['qualified'])}, which is "
          f"not the question this pack asked (#136)")
    return OK


def cmd_run(pack: Pack, out: pathlib.Path | None) -> int:
    try:
        gate(pack)
    except rp.Refused as why:
        print(f"GATE REFUSES  {why}")
        return FAIL
    try:
        dev = build_rig(pack)
        audio = render(pack, dev)
    except rp.Refused as why:
        print(f"REFUSED  {why}")
        return REFUSED
    res = measure(pack, audio)
    text = json.dumps(res, indent=1, sort_keys=False)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
        print(f"wrote {out}")
    print(text)
    return OK


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pack", default=MINIV3_PACK.name, choices=sorted(PACKS))
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--list", action="store_true", help="what the pack is")
    g.add_argument("--gate", action="store_true", help="the capability gate alone")
    g.add_argument("--run", action="store_true",
                   help="gate, render and measure. Needs the plugin host")
    ap.add_argument("--out", default=None, help="write the report here")
    a = ap.parse_args(argv)
    pack = PACKS[a.pack]
    if a.gate:
        return cmd_gate(pack)
    if a.run:
        return cmd_run(pack, pathlib.Path(a.out) if a.out else None)
    return cmd_list(pack)


if __name__ == "__main__":
    sys.exit(main())
