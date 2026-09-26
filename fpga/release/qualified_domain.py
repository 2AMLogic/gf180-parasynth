#!/usr/bin/env python3
"""fpga/release/qualified_domain.py -- the player-facing qualified domain of
the baseline release, ENFORCED on the register writes that actually ship.

WHY THE NOTE NUMBER IS NOT ENOUGH (plan080, #247). The host turns a note into
an increment AFTER each oscillator's transposition/detune: `VoiceFx.note_incs`
clamps only to the 24-bit register (~48 kHz), and `KeyHost.writes` does not
clamp at all (the register then keeps the low 24 bits -- a different pitch).
MIDI 127 transposed up an octave is 25.1 kHz, above the 24 kHz Nyquist
increment 2^23, which is where #247's glide mismatch lives. So the check here
is on the FINAL programmed increment of every oscillator, and on every glide
transition between two of them -- never on the note number.

THE DOMAIN (derivation in fpga/release/RELEASE.md, evidence in
fpga/release/glide_boundary.py):

  * every programmed increment lies in [INC_LO, INC_HI] = [phase_inc(MIDI 0),
    phase_inc(MIDI 127)] = [2858, 4384395]: no oscillator is programmed below
    8.18 Hz or above 12.54 kHz, whatever the note and transposition;
    INC_HI is 0.91 octave below 2^23;
  * a glide (a non-jump INC write while the glide register may be nonzero)
    is admitted only from a KNOWN source inside that range. The slew is
    monotone between its source and target, so every increment it passes
    through is inside the range too. A glide from reset (inc 0) or from an
    unknown device state is refused;
  * #247 as FILED -- a glide with either endpoint at or above 2^23 -- is
    named separately (`in_247_domain`) and refused with its own reason even
    though the range check already excludes it. Measured on #247's own bench
    (fpga/release/probe_247.py) the mismatch needs the voice routed through
    the drum filter (ROUTE = 1) with the kit struck, and needs neither a
    glide nor a high increment: ROUTE = 1 is refused (ROUTE_DRUMFILTER). No
    player-facing path writes ROUTE; it resets to 0;
  * MODULATION is a separate question. Oscillator pitch modulation (MROUTE
    bit 0 with nonzero wheel and depth) moves the effective increment every
    frame WITHOUT the glide slew; #247 reproduced identically with it on and
    off. It is refused only if its worst-case excursion leaves the same
    range (MOD_EXCURSION), and is never reported as a glide defect;
  * the waveform triple must be one of the release's presets' sets.

NEVER CLAMPS. A write outside the domain raises `Rejected` naming the write,
the pitch and the rule. Nothing is re-pitched to fit.

The raw engineering register interface (`uart_host.py --engineering`, the SPI
host, the benches) stays available and is OUTSIDE this domain by declaration.
"""
from __future__ import annotations

import math
import os
import sys
from dataclasses import dataclass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for _p in (os.path.join(ROOT, "model"), os.path.join(ROOT, "audition")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import voice_fx as vf                          # noqa: E402
from dsp import SR, note_hz, phase_inc         # noqa: E402

# ---- the bounds -------------------------------------------------------------
MIDI_LO, MIDI_HI = 0, 127
INC_LO = phase_inc(note_hz(MIDI_LO))           # 2858     (8.18 Hz)
INC_HI = phase_inc(note_hz(MIDI_HI))           # 4384395  (12543.85 Hz)
NYQUIST_INC = 1 << (vf.INC_BITS - 1)           # 2^23: SR / 2
INC_REG_MAX = vf.INC_MAX                       # 2^24 - 1
assert INC_LO > 0 and INC_HI < NYQUIST_INC, "the admitted range must sit below #247's domain"

SEC_VOICE = 0
A_INC0, A_INC2 = 0x00, 0x02
A_WAVE = 0x04
A_ROUTE = 0x0F
A_DRIFT = 0x2D                 # per-oscillator drift (#252): NOT in the released image
A_W0 = 0x08
A_GLIDE = 0x0C
A_MROUTE = 0x1F
A_MWHEEL, A_MPD = 0x25, 0x26
A_RESET = 0x23
W24 = (1 << 24) - 1

WAVE_NAME = {v: k for k, v in vf.WAVE_CODE.items()}
# the release's AUDIBLE waveform sets (an oscillator whose mixer weight is 0
# is None): the default patch, which bar808/demo also use, all three audible;
# m5a-saw / m5a-pulse and the m5a phrase, oscillator 0 alone. After reset
# every WAVE register is 0 (saw) and every weight 0.
SUPPORTED_WAVES = frozenset({("saw", "saw", "square"), ("saw", None, None),
                             ("pulse29", None, None)})
_WAVES_TXT = ", ".join(sorted(str(w) for w in SUPPORTED_WAVES))
EXCLUDED_CALIBRATION_WITH_RESONANCE = "surge-type2-clean-v1"

RULES = ("INC_WIDTH", "INC_RANGE", "GLIDE_SOURCE", "GLIDE_247", "MOD_EXCURSION",
         "WAVES", "CALIBRATION_RESONANCE", "PULSE2X", "ROUTE_DRUMFILTER", "NOT_IN_IMAGE")


class Rejected(ValueError):
    """A write, patch or note outside the qualified domain. `rule` is one of
    RULES; `index` the offending write's position (or None)."""

    def __init__(self, rule: str, message: str, index: int | None = None):
        assert rule in RULES, rule
        self.rule, self.index = rule, index
        super().__init__(f"[{rule}] " + (f"write {index}: " if index is not None else "") + message)


def audible(waves, weights) -> tuple:
    """The waveform set as heard: None for an oscillator the mixer silences."""
    return tuple((w if int(g) else None) for w, g in zip(tuple(waves)[:3], tuple(weights)[:3]))


def inc_hz(inc: int) -> float:
    return inc * SR / (1 << vf.INC_BITS)


def in_247_domain(src: int, dst: int) -> bool:
    """#247: a glide between increments where either endpoint is >= 2^23."""
    return src >= NYQUIST_INC or dst >= NYQUIST_INC


def check_inc(value: int, *, index: int | None = None, osc: int | None = None) -> int:
    """One programmed increment, BEFORE the register masks it. Returns it."""
    where = f"osc {osc} " if osc is not None else ""
    if not 0 <= value <= W24:
        raise Rejected("INC_WIDTH", f"{where}increment {value} does not fit the 24-bit register; "
                       f"the register would keep {value & W24} ({inc_hz(value & W24):.1f} Hz), "
                       f"a different pitch", index)
    if not INC_LO <= value <= INC_HI:
        raise Rejected("INC_RANGE", f"{where}increment {value} ({inc_hz(value):.2f} Hz) is outside "
                       f"the qualified [{INC_LO}, {INC_HI}] ({inc_hz(INC_LO):.2f}..{inc_hz(INC_HI):.2f} Hz, "
                       f"MIDI {MIDI_LO}..{MIDI_HI} after transposition)", index)
    return value


# ---- the patch --------------------------------------------------------------
def mod_octaves(mroute: int, mwheel: int, mpd: int) -> float:
    """Worst-case oscillator pitch-modulation excursion, in octaves (either
    direction), from the registers alone. amt = clamp16((mod * wheel) >> 15)
    with |mod| <= 32768, so |amt| <= min(1, wheel / 2^15) * 2^15; the octave
    word is (amt * mpd) >> 15 in Q3.12, saturated to +-4 octaves (OCT_SAT)."""
    if not (mroute & vf.MR_OSC) or mwheel == 0 or mpd == 0:
        return 0.0
    amt = min(1.0, mwheel / 32768.0)
    return min(vf.OCT_SAT / (1 << vf.OCT_Q), amt * mpd / (1 << vf.OCT_Q))


def check_patch(regs: dict, *, name: str = "patch", pulse2x: bool = False) -> dict:
    """A control image (VoiceFx.patch_regs form). Returns a summary including
    the playable MIDI range; raises Rejected."""
    if pulse2x:
        raise Rejected("PULSE2X", f"{name}: PULSE2X=1 is excluded from this release")
    waves = audible(regs["waves"], regs["weights"])
    if waves not in SUPPORTED_WAVES:
        raise Rejected("WAVES", f"{name}: waveform set {waves} is not one of the release's "
                       f"{_WAVES_TXT}")
    cal = regs.get("filter_calibration")
    if cal == EXCLUDED_CALIBRATION_WITH_RESONANCE and float(regs.get("res", 0.0)) != 0.0:
        raise Rejected("CALIBRATION_RESONANCE",
                       f"{name}: calibration {cal} is qualified at resonance 0 only; "
                       f"res = {regs['res']}")
    if cal is not None and cal != EXCLUDED_CALIBRATION_WITH_RESONANCE:
        raise Rejected("CALIBRATION_RESONANCE", f"{name}: calibration {cal!r} is not in this release")
    octs = mod_octaves(int(regs.get("mroute", 0)), int(regs.get("mwheel", 0)), int(regs.get("mpd", 0)))
    lo, hi = playable_notes(regs["detune"], mod_oct=octs)
    return dict(name=name, waves=list(waves), detune=list(regs["detune"]),
                glide=int(regs["glide"]), mod_octaves=octs, calibration=cal,
                playable_midi=[lo, hi] if lo is not None else None)


def _note_ok(note: int, detune, mod_oct: float = 0.0) -> bool:
    for dt in detune:
        v = phase_inc(note_hz(note) * 2.0 ** (dt / 12.0))
        if not INC_LO <= v <= INC_HI:
            return False
        if mod_oct and not (INC_LO <= v * 2.0 ** -mod_oct and v * 2.0 ** mod_oct <= INC_HI):
            return False
    return True


def playable_notes(detune, mod_oct: float = 0.0):
    """(lowest, highest) MIDI note 0..127 whose every oscillator increment is
    admitted, or (None, None). The admitted set is contiguous (monotone)."""
    ok = [n for n in range(128) if _note_ok(n, detune, mod_oct)]
    if not ok:
        return None, None
    assert ok == list(range(ok[0], ok[-1] + 1))
    return ok[0], ok[-1]


def check_note(note: int, regs: dict) -> None:
    """Refuse a note whose FINAL increments leave the domain. Never re-pitch."""
    if not 0 <= note <= 127:
        raise Rejected("INC_RANGE", f"MIDI note {note} is not 0..127")
    for k, dt in enumerate(regs["detune"]):
        v = phase_inc(note_hz(note) * 2.0 ** (dt / 12.0))
        try:
            check_inc(v, osc=k)
        except Rejected as exc:
            lo, hi = playable_notes(regs["detune"])
            raise Rejected(exc.rule, f"MIDI note {note} at osc {k} transposition {dt:+g} semitones: "
                           f"{exc.args[0].split('] ', 1)[1]}; this patch plays MIDI {lo}..{hi}") from None


# ---- the stream -------------------------------------------------------------
@dataclass
class _Osc:
    state: str = "reset"        # reset (inc 0) | unknown | in (a known in-range interval)
    lo: int = 0
    hi: int = 0


def check_stream(writes, *, initial: str = "reset", mod_initial: str | None = None) -> dict:
    """Validate register writes in the order they APPLY: (flag, sec, addr,
    data). `initial` is "reset" (the device's reset image: incs 0, glide 0,
    waves saw, weights 0, mroute/mwheel/mpd 0) or "unknown" (the device was
    left in some earlier state). `mod_initial` (default: `initial`) states the
    modulation registers separately: the player-facing fixtures never write
    them, so a fixture run ASSUMES their reset value -- a precondition the
    host cannot read back, declared in RELEASE.md, not verified here. With
    "unknown" modulation state an INC is refused. Returns counts; raises
    Rejected at the first violation."""
    assert initial in ("reset", "unknown")
    mod_initial = mod_initial or initial
    assert mod_initial in ("reset", "unknown")
    known = initial == "reset"
    osc = [_Osc("reset" if known else "unknown") for _ in range(3)]
    glide = 0 if known else None
    waves = ["saw"] * 3 if known else ["?"] * 3
    weights = [0] * 3 if known else [None] * 3
    mroute, mwheel, mpd = (0, 0, 0) if mod_initial == "reset" else (None, None, None)
    n_inc = n_glide = 0

    def mod_check(i, programming=False):
        if None in (mroute, mwheel, mpd):
            if programming:
                raise Rejected("MOD_EXCURSION", "an oscillator is programmed while the pitch-"
                               "modulation registers (MROUTE/MWHEEL/MPD) are in an unknown state", i)
            return
        octs = mod_octaves(mroute, mwheel, mpd)
        if not octs:
            return
        dests = (0, 1, 2) if (mroute & vf.MR_OSC3) else (0, 1)
        for k in dests:
            o = osc[k]
            if o.state != "in":
                continue
            if o.lo * 2.0 ** -octs < INC_LO or o.hi * 2.0 ** octs > INC_HI:
                raise Rejected("MOD_EXCURSION",
                               f"oscillator pitch modulation of +-{octs:.3f} octaves takes osc {k}'s "
                               f"effective increment to {o.lo * 2 ** -octs:.0f}..{o.hi * 2 ** octs:.0f}, "
                               f"outside [{INC_LO}, {INC_HI}]. This is the modulation path, not the "
                               f"#247 glide slew", i)

    for i, (flag, sec, addr, data) in enumerate(writes):
        if sec != SEC_VOICE:
            continue
        addr &= 0xFF
        if addr == A_RESET:
            osc = [_Osc("reset") for _ in range(3)]
            glide, waves, weights, mroute, mwheel, mpd = 0, ["saw"] * 3, [0] * 3, 0, 0, 0
            continue
        if addr == A_DRIFT:
            if data & 0xFFFF:
                raise Rejected("NOT_IN_IMAGE", "DRIFT (0x2D) was added to voice_dp.v by #252 after "
                               "the released image was built; this image ignores it, so a "
                               "nonzero drift would not sound as the model says", i)
            continue
        if addr == A_ROUTE:
            if data & 1:
                raise Rejected("ROUTE_DRUMFILTER",
                               "ROUTE = 1 sends the voice through the drum filter; with the kit "
                               "struck this is where #247's mismatch lives (fpga/release/"
                               "probe_247.py: 1809 differing periods with route 1, 0 with route 0, "
                               "unchanged by glide vs jump or by in-range increments)", i)
            continue
        if A_W0 <= addr <= A_W0 + 2:
            weights[addr - A_W0] = data & 0xFFFF
            continue
        if A_WAVE <= addr <= A_WAVE + 2:
            waves[addr - A_WAVE] = WAVE_NAME.get(data & 0xF, f"code{data & 0xF}")
            continue
        if addr == A_GLIDE:
            glide = data & W24
            continue
        if addr == A_MROUTE:
            mroute = data & 7; mod_check(i); continue
        if addr == A_MWHEEL:
            mwheel = data & 0xFFFF; mod_check(i); continue
        if addr == A_MPD:
            mpd = data & 0xFFFF; mod_check(i); continue
        if A_INC0 <= addr <= A_INC2:
            k = addr
            v = check_inc(int(data), index=i, osc=k)
            heard = None if None in weights else audible(waves, weights)
            if heard not in SUPPORTED_WAVES:
                raise Rejected("WAVES", f"osc {k} programmed while the audible waveform set is "
                               f"{heard if heard else 'unknown (this stream does not write the image; use `run`, which loads it)'}; "
                               f"release sets are {_WAVES_TXT}", i)
            o = osc[k]
            jump = bool(flag) or glide == 0
            if not jump:
                if o.state != "in":
                    raise Rejected("GLIDE_SOURCE", f"osc {k} glides to {v} from "
                                   f"{'reset (increment 0)' if o.state == 'reset' else 'an unknown increment'}"
                                   f"; a glide is qualified only from a known in-range increment", i)
                if in_247_domain(o.lo, v) or in_247_domain(o.hi, v):
                    raise Rejected("GLIDE_247", f"osc {k} glide {o.lo}..{o.hi} -> {v} touches >= 2^23 "
                                   f"(#247, excluded)", i)
                o.lo, o.hi = min(o.lo, v), max(o.hi, v)
                n_glide += 1
            else:
                o.state, o.lo, o.hi = "in", v, v
            n_inc += 1
            mod_check(i, programming=True)
    return dict(writes=len(writes), inc_writes=n_inc, glide_transitions=n_glide)
