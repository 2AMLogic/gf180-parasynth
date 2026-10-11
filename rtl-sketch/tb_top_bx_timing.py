"""The pin-side landing frame of every scripted write on `tb_top_bx.v`, in Python.

Why this exists (#372). `m5a_script` used to express its schedule as RELATIVE
waits with a fixed reserve subtracted for the writes in flight ("ten frames for
the three increment writes and the gate write"). A reserve is a guess at the
link's cost; any error in it accumulates event by event, and the published M5
phrases landed later notes up to 27 frames early (0.56 ms) because of it. To
schedule against ABSOLUTE event targets instead, the scheduler has to know the
frame a write will land in before the bench runs, and that is a property of the
bench's own timing, which is fully deterministic:

* `clk` toggles every 40.69 ns (`always #40.69 clk = ~clk`), so posedge k is at
  40.69 * (2k + 1) ns;
* the pad reset rises at the negedge after eight posedges; `synth_top` takes it
  through a two-stage synchroniser (`rst_q`), so `cyc` is 0 after posedge 9 and
  counts from there; the bench's tick (`dut.cyc == 255` sampled at a posedge)
  is therefore posedge 265 + 256 m, and after tick m the frame index `fr` is
  m + 1;
* `spi_write` holds CS_N low for 200 + 48 * 651 + 200 ns, then idles 700 ns:
  CS_N rises 31 648 ns after the call and the task returns 32 348 ns after it;
* `wait_ticks(n)` resumes AT the posedge of the n-th tick after the call;
* the pin prediction (`pred`) is `fr + 1`, with `fr` read at the third posedge
  at or after the CS_N rise (edge 1 samples the rise, edge 3 registers the
  push -- the bench's own comment on `csn_cd == 2`).

This module mirrors exactly that, in integer picoseconds. It is NOT the oracle
of any acceptance check: the bench's own write log (applied frame == pin
prediction, which `main()` and `tools/check_phrase_schedule.py` both require)
is. It is a scheduling aid, and it is validated against the stored bench traces
under `docs/deadline/traces/` (`test_tb_top_bx_timing.py`), which were produced
by the real bench and not by this file. If the bench's timing changes and this
file does not, the scheduler will miss its targets and the independent
schedule check goes red -- which is the right failure.

A CS_N rise that falls EXACTLY on a clock edge, or a wait that is called
exactly on a tick edge, is a simulator race this model cannot resolve;
`landing_frames` raises rather than guess.
"""
from __future__ import annotations

HALF_PS = 40_690                 # always #40.69 clk = ~clk, timescale 1ns/1ps
PERIOD_PS = 2 * HALF_PS
FIRST_TICK_EDGE = 265            # posedge index of tick 0 (see the module docstring)
CYCLES_PER_FRAME = 256
START_PS = 8 * PERIOD_PS         # rst_n = 1 at the negedge after eight posedges
CSN_RISE_PS = 200_000 + 48 * 651_000 + 200_000    # from the call to CS_N high
WRITE_PS = CSN_RISE_PS + 700_000                  # from the call to the task's return
PIN_TO_PUSH_EDGES = 3            # edge 1 samples the rise, edge 3 pushes `pred`


class Race(ValueError):
    """An event coincides with a clock edge; the simulator's order decides."""


def edge_time(k: int) -> int:
    return HALF_PS * (2 * k + 1)


def tick_time(m: int) -> int:
    return edge_time(FIRST_TICK_EDGE + CYCLES_PER_FRAME * m)


def ticks_before(t: int, *, inclusive: bool = False) -> int:
    """How many ticks have happened strictly before t (or at or before t)."""
    first = tick_time(0)
    span = PERIOD_PS * CYCLES_PER_FRAME
    if t < first or (t == first and not inclusive):
        return 0
    n = (t - first) // span + 1
    if not inclusive and tick_time(n - 1) == t:
        n -= 1
    return n


def first_edge_at_or_after(t: int) -> int:
    k = max(0, -(-(t - HALF_PS) // PERIOD_PS))
    if edge_time(k) == t:
        raise Race(f"CS_N rises exactly on posedge {k} ({t} ps)")
    return k


def landing_frames(waits) -> list[int]:
    """The pin-predicted applied frame of each write, given its `wait_frames`."""
    t = START_PS
    out = []
    for w in waits:
        w = int(w)
        if w < 0:
            raise ValueError("a wait cannot be negative")
        if w > 0:
            t0 = ticks_before(t, inclusive=True)
            if ticks_before(t, inclusive=True) != ticks_before(t):
                raise Race(f"wait_ticks called exactly on a tick edge ({t} ps)")
            t = tick_time(t0 + w - 1)
        rise = t + CSN_RISE_PS
        k3 = first_edge_at_or_after(rise) + PIN_TO_PUSH_EDGES - 1
        fr = ticks_before(edge_time(k3))         # read before that edge's tick update
        out.append(fr + 1)
        t += WRITE_PS
    return out


def end_frame(waits) -> int:
    """The bench's tick count when the last write's task returns."""
    t = START_PS
    for w in waits:
        if int(w) > 0:
            t = tick_time(ticks_before(t, inclusive=True) + int(w) - 1)
        t += WRITE_PS
    return ticks_before(t, inclusive=True)
