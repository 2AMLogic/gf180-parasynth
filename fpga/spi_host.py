#!/usr/bin/env python3
"""fpga/spi_host.py -- THE PRODUCTION CONTROL PATH, HOST SIDE.

Issue #81: everything this instrument has demonstrated so far was produced by
poking internal model state, or by a bench that bursts register writes at the
pins with no regard for when they land. A musician does not get to do either.
This module is what stands between a key press and the chip: musical events in,
**48-bit DR 0007 revision 2 transactions out**, `{F, 6'b0, SEC, A[7:0], D[31:0]}`
MSB first, with a schedule that says which FRAME each one lands in.

THREE THINGS IT HAS TO GET RIGHT, and only the first is obvious.

1.  THE FRAME FORMAT. 48 bits, contract 5.4. `rtl-sketch/verify_ctl.py` already
    proves the receiver; this is the transmitter.

2.  THE RATE. One transaction is 48 SCK periods plus a CS_N gap, and the frame
    is 20.833 us. At the contract's 2.0 MHz ceiling a transaction is 24.325 us
    -- LONGER THAN A FRAME. So the link delivers at most ONE register write per
    frame and there is no such thing as two writes landing together. Anything
    the reference host emits "at frame f" that is more than one write has to be
    spread, and something has to decide which way it spreads. `LinkTiming` is
    the measurement and `spread()` is the decision.

3.  THE TIMED COEFFICIENT SEQUENCES (contract 15.7.1). The bass drum's 4 ms
    attack window and the toms' diode pitch drop are NOT RTL behaviour. They are
    writes the host makes at particular frames after the hit. A bridge that
    loads the kit and forwards triggers does not reproduce those voices at all
    -- it reproduces a different instrument that happens to use the same
    register map. `MusicHost` emits them; `fpga/verify_fixture.py --wrong
    no-coef-seq` is what it sounds like when a host does not.

WHAT IT DOES NOT DO. It does not decide any value. This host sends faithfully
whatever the current model specifies, because what is being verified here is the
PATH, not the values. (The tom pitch drop was the standing example of that: it
shipped at an inferred x1.7 while 99 hardware files measured x1.06 / x1.14 /
x1.24 by accent, and the host sent x1.7. The correction has since landed -- it
set a constant to a measured quantity rather than fitting one to a score, which
is why #99 never covered it -- and the host now sends x1.06 for the same reason
it sent x1.7.)

TIMING MODEL. `LinkTiming` is exact to the picosecond and its landing-frame
prediction is CHECKED against the RTL, write for write, by
`fpga/verify_fixture.py` -- a scheduler whose predictions have never been
compared against a simulator is an estimator calibrated on itself.
"""
from __future__ import annotations

import math
import os
import sys
from dataclasses import dataclass, field, replace

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in ("model", "audition", "rtl-sketch"):
    _q = os.path.join(ROOT, _p)
    if _q not in sys.path:
        sys.path.insert(0, _q)

import drums_fx as dx                       # noqa: E402
import voice_fx as vf                       # noqa: E402
import synth_top_model as stm               # noqa: E402

SEC_VOICE, SEC_DRUM = 0, 1

# ---- the physical layer, contract 5.4 / DR 0007 -----------------------------
F_CORE_HZ       = 12_288_000       # synth_top.v: one clock
CYC_PER_FRAME   = 256              # ... 256 cycles per 48 kHz frame
SR              = 48_000
TX_BITS         = 48               # {F, 6'b0, SEC, A[7:0], D[31:0]}
TX_BYTES        = TX_BITS // 8     # 6
SCK_MAX_HZ      = 2_000_000        # "SCK MUST be <= 2.0 MHz"
CS_GAP_CYCLES   = 4                # "CS_N MUST be high for >= 4 core cycles"
PIN_TO_ACCEPT   = 3                # informative, DR 0007 section 5
FRAME_PS        = round(1e12 * CYC_PER_FRAME / F_CORE_HZ)      # 20_833_333 ps
WAVE_CODE       = dict(vf.WAVE_CODE)


def encode_tx(flag: int, sec: int, addr: int, data: int) -> int:
    """One transaction as a 48-bit integer, MSB first on the wire."""
    return (((flag & 1) << 47) | ((sec & 1) << 40)
            | ((addr & 0xFF) << 32) | (data & 0xFFFFFFFF))


def decode_tx(word: int) -> tuple:
    """(flag, sec, addr, data). The six reserved bits MUST be zero (5.4)."""
    if (word >> 41) & 0x3F:
        raise ValueError(f"reserved bits set in {word:012x}")
    return ((word >> 47) & 1, (word >> 40) & 1, (word >> 32) & 0xFF, word & 0xFFFFFFFF)


def tx_bits(flag: int, sec: int, addr: int, data: int) -> list:
    w = encode_tx(flag, sec, addr, data)
    return [(w >> b) & 1 for b in range(TX_BITS - 1, -1, -1)]


# ---- the link's timing, to the picosecond -----------------------------------
@dataclass(frozen=True)
class LinkTiming:
    """Every duration in picoseconds. `bench()` is the master inside
    rtl-sketch/tb_top_bx.v, which is the one the bit-comparison actually runs
    through; `contract_max()` is the fastest link DR 0007 permits. The two
    differ by 25 % and quoting either as "the" rate without saying which is how
    a budget becomes a number nobody can check."""
    name:        str
    clk_half_ps: int = 40_690      # tb_top_bx: `always #40.69 clk = ~clk`
    sck_half_ps: int = 325_500     # ... SCK = clk/8 = 1.536 MHz
    cs_lead_ps:  int = 200_000     # CS_N low -> first SCK edge
    cs_trail_ps: int = 200_000     # last SCK edge -> CS_N high
    cs_gap_ps:   int = 700_000     # CS_N high between transactions
    t_rst_ps:    int = 651_040     # when the script may first drive CS_N (rst_n_pad high)
    k_rst:       int = 10          # posedge index at which `cyc` first increments

    # k_rst IS 10, NOT 8, AND THE PIN IS WHAT SAID SO. `rst_n_pad` goes high at
    # posedge 8, but synth_top.v synchronises it through TWO flops, so the core's
    # own `rst_n` is only true from posedge 10 and `cyc` counts from there. The
    # first version of this model used 8, predicted the landing frame correctly
    # for 232 of 234 transactions, and was WRONG BY ONE FRAME on the two that
    # straddled a tick -- caught by fpga/verify_fixture.py's check of the host's
    # prediction against the CS_N pin, which is the only reason it is not still
    # wrong. A scheduler calibrated on itself would have passed.

    @classmethod
    def bench(cls) -> "LinkTiming":
        return cls(name="tb_top_bx 1.536 MHz")

    @classmethod
    def contract_max(cls) -> "LinkTiming":
        """SCK at the 2.0 MHz ceiling with the minimum 4-cycle CS_N gap and no
        host overhead -- the fastest DR 0007 allows, which is the number the
        link budget has to be quoted against."""
        return cls(name="DR 0007 maximum 2.0 MHz",
                   sck_half_ps=round(1e12 / SCK_MAX_HZ / 2),
                   cs_lead_ps=0, cs_trail_ps=0,
                   cs_gap_ps=round(1e12 * CS_GAP_CYCLES / F_CORE_HZ))

    # -- derived
    @property
    def sck_hz(self) -> float:
        return 1e12 / (2 * self.sck_half_ps)

    @property
    def tx_span_ps(self) -> int:
        """CS_N falling to CS_N rising: the transaction itself."""
        return self.cs_lead_ps + 2 * self.sck_half_ps * TX_BITS + self.cs_trail_ps

    @property
    def tx_period_ps(self) -> int:
        """The soonest the next transaction may start: the transaction plus the
        mandatory CS_N gap. This, divided by the frame, IS the link budget."""
        return self.tx_span_ps + self.cs_gap_ps

    @property
    def clk_ps(self) -> int:
        return 2 * self.clk_half_ps

    @property
    def tx_per_s(self) -> float:
        return 1e12 / self.tx_period_ps

    @property
    def writes_per_frame(self) -> float:
        return FRAME_PS / self.tx_period_ps

    def posedge_ps(self, k: int) -> int:
        return self.clk_half_ps + self.clk_ps * k

    def first_posedge_at_or_after(self, t_ps: int) -> int:
        return max(0, -(-(t_ps - self.clk_half_ps) // self.clk_ps))

    def frames_done_before(self, k: int) -> int:
        """`fr` as tb_top_bx counts it, read before posedge k's update."""
        return max(0, (k - self.k_rst) // CYC_PER_FRAME)

    def tick_posedge(self, m: int) -> int:
        """The posedge at which the frame counter becomes m (m >= 1)."""
        return self.k_rst + CYC_PER_FRAME * m - 1

    def tick_ps(self, m: int) -> int:
        return self.t_rst_ps if m <= 0 else self.posedge_ps(self.tick_posedge(m))

    def ticks_at(self, t_ps: int) -> int:
        """How many frame ticks have fired by time t (tb_top_bx's `ticks`)."""
        k = (t_ps - self.clk_half_ps) // self.clk_ps          # last posedge at or before t
        if k < self.k_rst - 1:
            return 0
        return max(0, (k - self.k_rst + 1) // CYC_PER_FRAME)

    def land_frame(self, t_start_ps: int) -> int:
        """The frame a transaction STARTED at t_start lands in -- the same
        derivation tb_top_bx makes from the CS_N pin: the acceptance cycle is
        the pin's rising edge plus three core cycles, and a write accepted
        during frame f applies at the start of f+1."""
        k0 = self.first_posedge_at_or_after(t_start_ps + self.tx_span_ps)
        return self.frames_done_before(k0 + PIN_TO_ACCEPT - 1) + 1

    def start_for_land(self, frame: int) -> int:
        """The LATEST start time whose transaction still lands in `frame`."""
        # frames_done_before(k0 + 2) must be frame - 1, so k0 + 2 < k_rst + 256*frame
        k0 = self.k_rst + CYC_PER_FRAME * frame - 1 - PIN_TO_ACCEPT
        return self.posedge_ps(k0) - self.tx_span_ps

    def min_land_gap(self) -> int:
        """Worst-case frames between two back-to-back landings."""
        return math.ceil(self.tx_period_ps / FRAME_PS)


BENCH = LinkTiming.bench()
CONTRACT = LinkTiming.contract_max()


# ---- one register write, on its way to the pins -----------------------------
@dataclass
class Write:
    """`frame` is where the host WANTS it to land. `anchor` means the frame is
    musical and may not move -- a stop bit's rising edge, a gate, a trigger.
    Everything else is an image write that may be sent early."""
    frame: int
    flag: int
    sec: int
    addr: int
    data: int
    tag: str = ""
    anchor: bool = False
    nominal: int = -1          # the reference host's frame, before spreading

    def key(self) -> tuple:
        return (self.flag, self.sec, self.addr, self.data & 0xFFFFFFFF)


@dataclass
class Placed:
    w: Write
    wait: int                  # `wait_frames` for tb_top_bx's command file
    start_ps: int
    land: int

    @property
    def slip(self) -> int:
        return self.land - self.w.frame

    @property
    def moved(self) -> int:
        return self.land - self.w.nominal if self.w.nominal >= 0 else 0


def feasible(writes: list, link: LinkTiming) -> list:
    """The forward pass: no write may be asked for a frame the one before it has
    not left yet.

    Two musical instants in the same frame -- a key down on the beat a drum hit
    lands on -- are not both deliverable, because a transaction is longer than a
    frame. A player does that constantly, so the host has to have an answer, and
    the answer is the only one that keeps the order: the later one moves later,
    by the minimum the link needs. That is a QUANTISATION of musical time to one
    transaction, and `check()` reports it in microseconds as `anchor_jitter`
    rather than letting it hide."""
    step, prev = link.min_land_gap(), None
    out = [replace(w, nominal=w.frame if w.nominal < 0 else w.nominal) for w in writes]
    for w in out:
        if prev is not None and w.frame < prev + step:
            w.frame = prev + step
        prev = w.frame
    return out


def spread(writes: list, link: LinkTiming) -> list:
    """Move image writes EARLIER until every write has a frame of its own.

    The link delivers one write per `min_land_gap()` frames. The reference
    hosts (`drums_fx.hit_writes`, `voice_fx.KeyHost.writes`) emit whole bursts
    "at frame f" because the MODEL applies a burst atomically; the LINK cannot.
    Something has to choose, and the choice here is:

        anchors keep their frame; everything else backs up in front of them,
        latest first, in the order the reference host emitted it.

    So a bass drum's `a1`, `a2` and accent land in the frames BEFORE the stop
    bit rises, and the strike itself is never late. The alternative -- letting
    the strike slip while the coefficients hold their frame -- moves the music
    instead of the setup, which is the wrong thing to move.

    Anchors closer together than the link's gap cannot both be honoured; those
    are returned as they are and `check()` reports them as conflicts rather
    than silently absorbing them."""
    step = link.min_land_gap()
    out = [replace(w, nominal=w.frame if w.nominal < 0 else w.nominal) for w in writes]
    latest = None
    for w in reversed(out):
        if w.anchor:
            latest = w.frame - step
            continue
        if latest is not None and w.frame > latest:
            w.frame = latest
        latest = w.frame - step
    return out


def lay_out(writes: list, link: LinkTiming) -> list:
    """The whole scheduling decision, in the order it has to happen.

    The sort is load-bearing: within one frame the SETUP goes before the
    INSTANTS. Two hosts writing into the same frame -- a drum hit from the
    sequencer and a key down from the keyboard -- otherwise interleave by
    whichever was appended first, and the backward pass then tries to put the
    keyboard's pitch writes in front of a strike that is already behind them in
    the queue. Measured, not argued: without `w.anchor` in the sort key, a key
    down landing on a bass-drum beat moved 10 frames (208 us); with it, 2
    frames (42 us), which is one transaction, which is the floor."""
    ws = sorted(writes, key=_write_order_key)
    return feasible(spread(ws, link), link)


def _write_order_key(w: Write) -> tuple:
    """Stable musical ordering for writes with the same event time.

    ``list.sort`` used to preserve API call order here.  That made a BD and a
    tom at the same timestamp produce different wire schedules depending on
    which ``hits`` call happened first.  The tag order keeps voice operations
    (pitch, track, then gate/trigger) meaningful while the remaining fields
    make drum and image writes independent of arrival order.
    """
    tag_order = {
        "inc": 10, "track": 20, "glide": 30, "mwheel": 40,
        "accent": 50, "kit": 55, "knob-cutoff": 60, "knob-res": 61,
        "knob-decay": 62, "knob-volume": 63,
        "bd-attack-hot": 70, "tom-bend": 71,
        "stops-on": 80, "gate": 81, "trig": 82, "stops-off": 90,
    }
    return (w.frame, bool(w.anchor), tag_order.get(w.tag, 100),
            w.flag, w.sec, w.addr, w.data & 0xFFFFFFFF)


def causal_lay_out(writes: list, link: LinkTiming) -> list:
    """Lay out a live stream without sending any write before its event.

    Offline ``lay_out`` deliberately backs setup writes into the future
    musical instant.  A live caller cannot know that future, so it submits
    writes at the event frame and accepts serial-link latency.  ``feasible``
    then serialises collisions while preserving the causal lower bound.
    """
    ws = sorted(writes, key=_write_order_key)
    return feasible(ws, link)


def place(writes: list, link: LinkTiming) -> list:
    """Turn wanted frames into tb_top_bx `wait_frames` commands.

    The bench's script can start a transaction at exactly two kinds of moment:
    the instant the previous one finished (`wait_frames` 0) or a frame tick
    (`wait_frames` n). `land_frame` is monotonic in the start time, so for each
    write this takes the EARLIEST allowed start that still lands at or after
    the wanted frame. Landing early is not an option -- an image write that
    arrives before the host meant it to is a different sound -- and landing
    late is recorded as slip rather than absorbed.

    The landing frame that comes back is the HOST's prediction. It is checked
    against the CS_N pin, write for write, by fpga/verify_fixture.py."""
    out, t_free, gap = [], link.t_rst_ps, link.min_land_gap()
    for w in writes:
        start, wait = t_free, 0
        if link.land_frame(start) < w.frame:
            t0 = link.ticks_at(t_free)
            m = max(t0 + 1, w.frame - gap - 1)
            while link.land_frame(link.tick_ps(m)) < w.frame:
                m += 1
            while m > t0 + 1 and link.land_frame(link.tick_ps(m - 1)) >= w.frame:
                m -= 1
            wait, start = m - t0, link.tick_ps(m)
        out.append(Placed(w, wait, start, link.land_frame(start)))
        t_free = start + link.tx_period_ps
    return out


def check(placed: list) -> dict:
    """What the schedule actually achieved, in absolute units. `conflicts` is
    the only fatal one: an anchor that did not land in the frame the scheduler
    asked for means the scheduler and the link disagree. `anchor_jitter` is not
    fatal but IS the musical cost -- how far a note or a hit moved from where
    the player put it, because two of them wanted the same frame."""
    conflicts = [p for p in placed if p.w.anchor and p.slip != 0]
    slipped = [p for p in placed if p.slip != 0]
    moved = [p for p in placed if p.moved]
    jitter = [p for p in placed if p.w.anchor and p.moved]
    return dict(n=len(placed),
                conflicts=conflicts,
                slipped=len(slipped),
                moved=len(moved),
                anchor_jitter=len(jitter),
                anchor_jitter_frames=max((abs(p.moved) for p in jitter), default=0),
                anchor_jitter_us=max((abs(p.moved) for p in jitter), default=0)
                * FRAME_PS / 1e6,
                worst_move_frames=max((abs(p.moved) for p in moved), default=0),
                worst_slip_frames=max((abs(p.slip) for p in slipped), default=0))


def live_latency(placed: list, first_event_frame: int) -> dict:
    """Latency for writes belonging to a live event, excluding boot image.

    ``check`` intentionally reports movement for every transaction.  A live
    acceptance result also needs the musical queue bound without counting the
    one-time patch load, which may legitimately occupy the link before play.
    """
    event = [p for p in placed if p.w.nominal >= int(first_event_frame)]
    frames = max((p.land - p.w.nominal for p in event), default=0)
    return {"writes": len(event), "max_frames": frames,
            "max_us": frames * FRAME_PS / 1e6}


def cmd_lines(placed: list) -> list:
    return [f"{p.wait} {p.w.flag} {p.w.sec} {p.w.addr} {p.w.data & 0xFFFFFFFF}" for p in placed]


def model_writes(placed: list) -> list:
    """(frame, flag, sec, addr, data) at the frames the host PREDICTS, for
    model/synth_top_model.py."""
    return [(p.land, p.w.flag, p.w.sec, p.w.addr, p.w.data & 0xFFFFFFFF) for p in placed]


# ---- the host ---------------------------------------------------------------
class MusicHost:
    """Key events, drum hits and knobs into register writes.

    It keeps the register IMAGE, which is not bookkeeping: `bd_attack_writes`
    needs the pair to write BACK, and recomputing it from the kit instead would
    silently undo a DECAY knob (the fault that record already had to be fixed
    for). A host that does not track its image cannot do the attack window."""

    def __init__(self, patch: dict = None, kit: list = None, *, stop_hold: int = 2,
                 coef_seq: bool = True, keyhost: vf.KeyHost = None):
        self.regs = dict(patch or vf.VoiceFx.patch_regs())
        self.kit = list(kit if kit is not None else dx.kit_808())
        self.image = dict(self.kit)                # SEC = 1 register image
        self.stop_hold = int(stop_hold)            # frames a stop bit is held high
        self.coef_seq = bool(coef_seq)
        self.host = keyhost or vf.KeyHost()
        self.w: list = []
        self.events: list = []                     # (frame, what) for the report
        self._bd_decay = 5.0
        self.load_span = None                      # (first, end, frame) of load()

    # -- primitives
    def _put(self, frame, flag, sec, addr, data, tag="", anchor=False):
        self.w.append(Write(int(frame), int(flag) & 1, int(sec), int(addr) & 0xFF,
                            int(data) & 0xFFFFFFFF, tag, anchor))
        if sec == SEC_DRUM:
            self.image[int(addr) & 0xFF] = int(data) & 0xFFFFFFFF

    def voice(self, frame, addr, data, *, flag=0, tag="", anchor=False):
        self._put(frame, flag, SEC_VOICE, addr, data, tag, anchor)

    def drum(self, frame, addr, data, *, tag="", anchor=False):
        self._put(frame, 0, SEC_DRUM, addr, data, tag, anchor)

    # -- boot: the patch image and the kit
    def load(self, frame: int = 0, *, dvol: float = 0.45, bvol: float = 0.45,
             accents=None) -> "MusicHost":
        first = len(self.w)
        r = self.regs
        for k, s in enumerate(r["waves"]):
            self.voice(frame, stm.A_WAVE + k, WAVE_CODE[s], tag="wave")
        for k, g in enumerate(r["weights"]):
            self.voice(frame, stm.A_W + k, g, tag="weight")
        for base, key in ((stm.A_AMP, "amp"), (stm.A_FILT, "fenv")):
            for j, v in enumerate(r[key]):
                self.voice(frame, base + j, v, tag=key)
        for addr, key in ((stm.A_CUT_LO, "cut_lo"), (stm.A_CUT_HI, "cut_hi"),
                          (stm.A_K, "k"), (stm.A_GAIN, "gain"), (stm.A_OGAIN, "ogain"),
                          (stm.A_GLIDE, "glide"), (stm.A_VOL, "vol")):
            self.voice(frame, addr, r[key], tag=key)
        self.voice(frame, stm.A_DVOL, dx.accent_reg(dvol), tag="dvol")
        self.voice(frame, stm.A_BVOL, dx.accent_reg(bvol), tag="bvol")
        for a, v in self.kit:
            self.drum(frame, a, v, tag="kit")
        for st, lvl in enumerate(accents or [1.0] * dx.N_STOPS):
            self.drum(frame, dx.A_ACCENT + st, dx.accent_reg(lvl), tag="accent")
        self.events.append((frame, f"load: patch + kit ({len(self.w)} writes)"))
        # which writes ARE the configuration image, by position: the tags
        # cannot say (a hit writes "accent" and a key writes "glide" too),
        # and a link that delivers setup separately from the performance
        # needs to know exactly what setup was
        self.load_span = (first, len(self.w), int(frame))
        return self

    # -- the keyboard
    def keys(self, events: list, *, first_from_reset: bool = True) -> "MusicHost":
        """events: (frame, 'on'|'off', note). Through the reference host of
        contract 5.6, so the note logic is the model's and only the DELIVERY is
        this module's."""
        for f, op, *args in self.host.writes(events, self.regs, first_from_reset):
            if op == "INC":
                k, v, jump = args
                self.voice(f, stm.A_INC + k, v, flag=1 if jump else 0, tag="inc")
            elif op == "TRACK":
                self.voice(f, stm.A_TRACK, args[0], tag="track")
            elif op == "GATE":
                self.voice(f, stm.A_GATE_ON if args[0] else stm.A_GATE_OFF, 0,
                           tag="gate", anchor=True)
            elif op == "TRIG":
                self.voice(f, stm.A_TRIG, 0, tag="trig", anchor=True)
            elif op == "GLIDE":
                self.voice(f, stm.A_GLIDE, args[0], tag="glide")
            elif op == "MWHEEL":
                self.voice(f, stm.A_MWHEEL, args[0], tag="mwheel")
        for f, kind, note in events:
            self.events.append((f, f"key {kind} {note}"))
        return self

    # -- the drums, WITH the timed coefficient sequences of 15.7.1
    def hits(self, hits: list) -> "MusicHost":
        """hits: (frame, stop, accent). Emits, for each hit:
             - the accent (an image write, may be sent early)
             - the stop bit's RISING EDGE, an anchor: this is the note's time
             - the BD's 4 ms attack window, or the tom's 60 ms pitch drop,
               both as timed writes (15.7.1) and both anchored, because a
               coefficient sequence whose steps move is a different sequence.
        The stop bit is held `stop_hold` frames and then dropped; the block
        fires on the 0 -> 1 edge (15.2) so a longer hold does not re-strike,
        and one frame is not deliverable on this link (`min_land_gap`)."""
        tom_mode = {dx.LT: dx.M_LT, dx.MT: dx.M_MT, dx.HT: dx.M_HT}
        by_frame: dict = {}
        for f, s, a in hits:
            by_frame.setdefault(int(f), []).append((int(s), float(a)))
        for f in sorted(by_frame):
            bits = 0
            for s, a in by_frame[f]:
                bits |= 1 << s
                if self.coef_seq and s == dx.BD:
                    self._bd_window(f)
                elif self.coef_seq and s in tom_mode:
                    self._tom_bend(f, tom_mode[s], a)
                self.drum(f, dx.A_ACCENT + s, dx.accent_reg(a), tag="accent")
                self.events.append((f, f"hit {dx.STOP_NAMES[s]} accent {a:.2f}"))
            self.drum(f, dx.A_STOPS, bits, tag="stops-on", anchor=True)
            self.drum(f + self.stop_hold, dx.A_STOPS, 0, tag="stops-off", anchor=True)
        return self

    def _mode_pair(self, mode: int) -> list:
        base = dx.A_MODE + mode * dx.MODE_STRIDE
        return [self.image.get(base, 0), self.image.get(base + 1, 0)]

    def _bd_window(self, frame: int):
        """Contract 15.7.1, first bullet. FOUR writes: the resonator up to
        130 Hz / Q 6 at the hit and back 192 frames (4 ms) later, to whatever
        the image holds -- NOT to a recomputed preset."""
        seq = dx.bd_attack_writes(frame, self._mode_pair(dx.M_BD))
        for i, (f, a, v) in enumerate(seq):
            self.drum(f, a, v, tag="bd-attack-hot" if f == frame else "bd-attack-restore")
        self.events.append((frame, f"BD attack window: 4 writes, "
                                   f"{int(round(dx.BD_ATTACK_MS * 1e-3 * SR))} frames wide"))

    def _tom_bend(self, frame: int, mode: int, accent: float):
        """Contract 15.7.1, second bullet. (TOM_DROP_STEPS + 1) x 2 writes over
        60 ms, read out of the IMAGE so a retuned tom or a conga sweeps from
        where it actually sits -- which now matters twice over, because the
        measured law depends on the TUNING pot as well as the accent. Sent as
        the model specifies it: this verifies the path, not the value."""
        a1, a2 = self._mode_pair(mode)
        f0, q = dx.poles_from_regs(a1, a2)
        amp = self.image.get(dx.A_MODE + mode * dx.MODE_STRIDE + 2, 0) / float(1 << 15)
        seq = dx.tom_pitch_drop_writes(frame, mode, f0, q, amp, accent)
        for f, a, v in seq:
            self.drum(f, a, v, tag="tom-bend")
        self.events.append((frame, f"tom pitch drop: {len(seq)} writes over "
                                   f"{dx.TOM_DROP_MS:.0f} ms, "
                                   f"x{1.0 + dx.tom_drop_excess(mode, f0, accent):.4f} at accent "
                                   f"{accent:.2f}, f0 {f0:.1f} Hz (#110's measured law)"))

    # -- the knobs
    def knob(self, frame: int, name: str, value: float) -> "MusicHost":
        """cutoff (Hz), resonance (0..1 q), decay (BD DECAY 0..10). Three knobs
        because that is what #81 asks for at minimum, and each one is a
        different NUMBER OF WRITES, which is the point: resonance is three and
        decay is two, so a knob sweep is a bandwidth question, not a free one."""
        n = name.lower()
        if n == "cutoff":
            lo = vf.usat(int(round(value)), vf.CUT_BITS)
            self.regs["cut_lo"] = lo
            self.voice(frame, stm.A_CUT_LO, lo, tag="knob-cutoff")
        elif n == "cutoff_hi":
            hi = vf.usat(int(round(value)), vf.CUT_BITS)
            self.regs["cut_hi"] = hi
            self.voice(frame, stm.A_CUT_HI, hi, tag="knob-cutoff")
        elif n == "resonance":
            # the COMMON conversion, under the image's own calibration: a
            # knob turn must not silently revert a calibrated patch to the
            # legacy gain/ogain words (plan074 B)
            k, g, og = vf.ladder_regs(float(value), self.regs["drive"],
                                      self.regs.get("filter_calibration"))
            self.regs.update(res=float(value), k=k, gain=g, ogain=og)
            self.voice(frame, stm.A_K, k, tag="knob-res")
            self.voice(frame, stm.A_GAIN, g, tag="knob-res")
            self.voice(frame, stm.A_OGAIN, og, tag="knob-res")
        elif n == "decay":
            self._bd_decay = float(value)
            q = dx.bd_decay_q(float(value))
            for a, v in dx.mode_writes(dx.M_BD, dx.BD_HZ, q, 0.0)[:2]:
                self.drum(frame, a, v, tag="knob-decay")
        elif n == "volume":
            v = vf.usat(int(round(float(value) * 32768)), vf.VOL_BITS)
            self.regs["vol"] = v
            self.voice(frame, stm.A_VOL, v, tag="knob-volume")
        else:
            raise ValueError(f"unknown knob {name!r}")
        self.events.append((frame, f"knob {n} = {value}"))
        return self

    # -- the schedule
    def schedule(self, link: LinkTiming = BENCH) -> list:
        """The whole decision, in order: push colliding musical instants apart,
        back the image writes up in front of them, then lay the transactions on
        the wire. Returns `Placed` -- what to send, and the frame the host
        PREDICTS each one lands in."""
        return place(lay_out(self.w, link), link)


class LiveMusicHost(MusicHost):
    """Causal event adapter for a playable host.

    ``submit`` is the boundary used by a MIDI/UI bridge.  The first argument
    is the frame at which the bridge receives the event; writes are never
    scheduled before it.  Events sharing a frame are canonicalised by type
    and payload, so API call order cannot change the coefficients.  The wire
    still has one transaction per roughly 1.55 frames on the bench link;
    queueing therefore adds latency, reported by :func:`check`, rather than
    moving a setup write into the past.

    Policy for a busy queue: preserve every event, serialize writes in the
    link's order, and let the musical instant move later by the measured link
    service time.  ``MAX_LIVE_LATENCY_FRAMES`` is the bounded workload used by
    the acceptance tests (boot plus three simultaneous events), not a claim
    that an unbounded input stream has finite latency.
    """
    MAX_LIVE_LATENCY_FRAMES = 512

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._live_base_w: list[Write] = []
        self._live_events: list[tuple[int, str, tuple]] = []
        self._live_materialized = False

    def load(self, *args, **kwargs) -> "LiveMusicHost":
        super().load(*args, **kwargs)
        self._live_base_w = list(self.w)
        return self

    def submit(self, frame: int, kind: str, payload: tuple) -> "LiveMusicHost":
        frame = int(frame)
        if frame < 0:
            raise ValueError("live event frame must be non-negative")
        if kind not in {"key", "hit", "knob"}:
            raise ValueError(f"unknown live event kind {kind!r}")
        if not isinstance(payload, tuple):
            payload = tuple(payload)
        self._live_events.append((frame, kind, payload))
        self._live_materialized = False
        return self

    @staticmethod
    def _event_key(event: tuple) -> tuple:
        frame, kind, payload = event
        rank = {"knob": 0, "key": 1, "hit": 2}[kind]
        return frame, rank, tuple(str(x) for x in payload)

    def _materialize(self):
        if self._live_materialized:
            return
        self.w = list(self._live_base_w)
        # KeyHost is stateful across the phrase, so hand it one canonical
        # stream.  Drum image changes are applied bucket by bucket below.
        keys = sorted((e[0], e[1], *e[2]) for e in self._live_events
                      if e[1] == "key")
        if keys:
            self.keys([(f, op, note) for f, _, op, note in keys])
        for frame in sorted({e[0] for e in self._live_events}):
            bucket = [e for e in self._live_events if e[0] == frame]
            for _, _, payload in sorted((e for e in bucket if e[1] == "knob"),
                                        key=self._event_key):
                self.knob(frame, payload[0], payload[1])
            hits = [(frame, payload[0], payload[1])
                    for _, _, payload in sorted((e for e in bucket if e[1] == "hit"),
                                                key=self._event_key)]
            if hits:
                self.hits(hits)
        self._live_materialized = True

    def schedule(self, link: LinkTiming = BENCH) -> list:
        self._materialize()
        return place(causal_lay_out(self.w, link), link)

    def latency(self, placed: list) -> dict:
        """Return measured queue latency after the one-time boot image."""
        if not self._live_events:
            return {"writes": 0, "max_frames": 0, "max_us": 0.0}
        return live_latency(placed, min(e[0] for e in self._live_events))


def knob_cost() -> dict:
    """Writes per knob turn, which is the only unit that matters on this link."""
    return {"cutoff": 1, "resonance": 3, "decay": 2, "volume": 1}
