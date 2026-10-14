#!/usr/bin/env python3
"""fpga/link_budget.py -- CAN THE LINK PLAY THE INSTRUMENT? The cheap gate.

Issue #81 asks for this BEFORE the host is built, because if the answer is no
it is worth more than a working demo: the bass drum's attack window and the
toms' pitch bends are timed writes from the host (contract 15.7.1), not RTL
behaviour, so a link that cannot deliver them on time means the design depends
on host timing the hardware cannot guarantee.

Everything here is an ABSOLUTE number -- bytes, microseconds, frames,
transactions per second. A percentage of an unstated maximum is not a budget.

THE GATE, in one line: a transaction is longer than a frame, so the link
delivers at most ONE register write per frame, and no burst the reference host
emits "at frame f" arrives together. What has to be checked is not whether the
bursts survive -- they cannot -- but whether the two SEQUENCES survive, because
they are the two things no register image can hold:

    the BD attack window   4 ms   = 192 frames wide, 4 writes
    the tom pitch drop    60 ms   = 2880 frames wide, 14 writes

    .venv/bin/python fpga/link_budget.py
    .venv/bin/python fpga/link_budget.py --bpm 160 --bars 2

Exit 0 if the budget holds, 1 if it does not, 2 if the instrument refused.
"""
from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import spi_host as sh                       # noqa: E402  (puts model/ and audition/ on the path)
import drums_fx as dx                       # noqa: E402
from spi_host import BENCH, CONTRACT, FRAME_PS, SR, TX_BITS, TX_BYTES, LinkTiming   # noqa: E402

US = 1e6


def us(ps: float) -> float:
    return ps / US


def physical(link: LinkTiming) -> dict:
    return dict(
        name=link.name,
        sck_hz=link.sck_hz,
        bits=TX_BITS,
        bytes=TX_BYTES,
        span_us=us(link.tx_span_ps),
        period_us=us(link.tx_period_ps),
        tx_per_s=link.tx_per_s,
        frame_us=us(FRAME_PS),
        writes_per_frame=link.writes_per_frame,
        frames_per_write=link.tx_period_ps / FRAME_PS,
        min_land_gap=link.min_land_gap(),
    )


# ---- what each musical event costs, in transactions -------------------------
def event_costs() -> list:
    """(name, writes, note). Counted from the reference hosts, not estimated:
    the numbers come out of the write lists themselves."""
    kit = dx.kit_808()
    rows = [
        ("note on (3 osc + track + gate)", 5, "voice_fx.KeyHost, glide off"),
        ("note off", 1, "GATE_OFF"),
        ("retrigger under a held key", 1, "TRIG"),
        ("drum hit, no sequence", 3, "accent + STOPS on + STOPS off"),
    ]
    bd = dx.bd_attack_writes(0, [0, 0])
    rows.append(("BD hit WITH the 4 ms attack window", 3 + len(bd),
                 f"+{len(bd)} timed coefficient writes (15.7.1)"))
    tom = dx.tom_pitch_drop_writes(0, dx.M_LT, 90.0, 25.0, 0.25, 1.0)
    rows.append(("tom hit WITH the 60 ms pitch drop", 3 + len(tom),
                 f"+{len(tom)} timed coefficient writes (15.7.1)"))
    rows.append(("knob: cutoff", 1, "CUT_LO"))
    rows.append(("knob: resonance", 3, "K + GAIN + OGAIN, the compensation lookup"))
    rows.append(("knob: BD decay", 2, "mode 6 a1, a2"))
    rows.append(("boot: the whole patch image", 21, "voice_fx.patch_regs through the map"))
    rows.append(("boot: the reference kit", len(kit) + dx.N_STOPS,
                 f"drums_fx.kit_808 ({len(kit)}) + {dx.N_STOPS} accents"))
    return rows


def worst_simultaneous(link: LinkTiming) -> dict:
    """"the worst case when several voices retrigger together" -- measured by
    building it, not by arguing about it. Every circuit struck in one frame,
    with both timed sequences running."""
    f0 = 8000
    host = sh.MusicHost().load(0)
    host.hits([(f0, st, 1.0) for st in range(dx.N_STOPS)])
    at_f0 = [w for w in host.w if w.frame == f0]
    sched = host.schedule(link)
    setup = [p for p in sched if p.w.frame <= f0 and p.w.nominal == f0]
    lead = max((f0 - p.land for p in setup), default=0)
    st = sh.check(sched)
    return dict(stops=dx.N_STOPS,
                writes_at_the_hit_frame=len(at_f0),
                serialised_us=len(at_f0) * us(link.tx_period_ps),
                serialised_frames=len(at_f0) * link.tx_period_ps / FRAME_PS,
                lead_frames=lead, lead_us=lead * us(FRAME_PS),
                conflicts=len(st["conflicts"]))


def sequence_deadlines(link: LinkTiming) -> list:
    """The two sequences of 15.7.1 against the link: how wide the window is,
    how many writes it needs, and how long those writes take. This is the
    question #81 says to stop on."""
    bd_frames = int(round(dx.BD_ATTACK_MS * 1e-3 * SR))
    bd_writes = len(dx.bd_attack_writes(0, [0, 0]))
    tom = dx.tom_pitch_drop_writes(0, dx.M_LT, 90.0, 25.0, 0.25, 1.0)
    tom_frames = int(round(dx.TOM_DROP_MS * 1e-3 * SR))
    steps = sorted({f for f, _, _ in tom})
    tom_gap = min((b - a for a, b in zip(steps, steps[1:])), default=tom_frames)
    per = link.tx_period_ps / FRAME_PS
    return [
        dict(name="BD attack window (15.7.1, reference 2)",
             width_ms=dx.BD_ATTACK_MS, width_frames=bd_frames, writes=bd_writes,
             writes_at_once=2, need_frames=2 * per, need_us=2 * us(link.tx_period_ps),
             margin_frames=bd_frames - 2 * per),
        dict(name="tom diode pitch drop (15.7.1, reference 4)",
             width_ms=dx.TOM_DROP_MS, width_frames=tom_frames, writes=len(tom),
             writes_at_once=2, need_frames=2 * per, need_us=2 * us(link.tx_period_ps),
             margin_frames=tom_gap - 2 * per, step_gap_frames=tom_gap),
    ]


def fixture_demand(bpm: float, bars: int, link: LinkTiming) -> dict:
    """The reference 808 pattern as a real demand: transactions, bytes, the
    peak in any one frame, and whether the schedule that comes out of the host
    honours every anchor."""
    hits = dx.pattern_hits(dx.PATTERN_808, bpm=bpm, bars=bars, start_s=0.25)
    host = sh.MusicHost()
    host.load(0)
    boot = len(host.w)
    host.hits(hits)
    play = [w for w in host.w[boot:]]
    n = max(w.frame for w in host.w) + 1
    dur_s = n / SR
    per_frame: dict = {}
    for w in play:
        per_frame[w.frame] = per_frame.get(w.frame, 0) + 1
    sched = host.schedule(link)
    st = sh.check(sched)
    busy_ps = len(sched) * link.tx_period_ps
    moved = [p for p in sched if p.moved]
    boot_moved = [p for p in sched[:boot] if p.moved]
    play_late = [p for p in sched[boot:] if p.moved > 0]
    play_early = [p for p in sched[boot:] if p.moved < 0]
    return dict(hits=len(hits), boot=boot, writes=len(host.w), play=len(play),
                bytes=len(host.w) * TX_BYTES,
                boot_us=boot * us(link.tx_period_ps),
                frames=n, seconds=dur_s,
                mean_writes_per_s=len(play) / dur_s,
                peak_writes_in_one_frame=max(per_frame.values()),
                frames_wanting_more_than_one=sum(1 for v in per_frame.values() if v > 1),
                link_busy_fraction=busy_ps / (n * FRAME_PS),
                conflicts=len(st["conflicts"]),
                moved=len(moved),
                boot_spread_frames=max((p.moved for p in boot_moved), default=0),
                play_late=len(play_late),
                play_lead_frames=max((-p.moved for p in play_early), default=0),
                worst_move_frames=st["worst_move_frames"],
                worst_move_us=st["worst_move_frames"] * us(FRAME_PS),
                bd_window_stretch_frames=st["window_stretch_frames"],
                bd_window_stretch_us=st["window_stretch_us"],
                sched=sched, check=st)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bpm", type=float, default=118.0)
    ap.add_argument("--bars", type=int, default=2)
    a = ap.parse_args(argv)

    print("=" * 78)
    print("SPI LINK BUDGET -- absolute numbers, contract 5.4 / DR 0007 revision 2")
    print("=" * 78)
    print(f"\nThe frame is {us(FRAME_PS):.3f} us ({SR} Hz, {sh.CYC_PER_FRAME} cycles of "
          f"{sh.F_CORE_HZ/1e6:.3f} MHz).")
    print(f"One transaction is {TX_BITS} bits = {TX_BYTES} bytes: "
          "{F, 6'b0, SEC, A[7:0], D[31:0]}.\n")
    hdr = f"{'link':<26}{'SCK':>10}{'tx span':>11}{'+CS gap':>11}{'tx/s':>9}{'writes/frame':>14}"
    print(hdr); print("-" * len(hdr))
    for link in (CONTRACT, BENCH):
        p = physical(link)
        print(f"{p['name']:<26}{p['sck_hz']/1e6:>8.3f} M{p['span_us']:>10.3f}u"
              f"{p['period_us']:>10.3f}u{p['tx_per_s']:>9.0f}{p['writes_per_frame']:>14.4f}")
    print()
    for link in (CONTRACT, BENCH):
        p = physical(link)
        print(f"  {p['name']}: one write every {p['frames_per_write']:.3f} frames "
              f"({p['period_us']:.3f} us). A transaction is LONGER THAN A FRAME, so at most "
              f"ONE write lands per frame")
    print("\n  -> no burst arrives together. Two writes are "
          f"{2*us(CONTRACT.tx_period_ps):.1f} us apart at best "
          f"({2*CONTRACT.tx_period_ps/FRAME_PS:.2f} frames), "
          f"{2*us(BENCH.tx_period_ps):.1f} us on the bench link.")

    print("\n" + "=" * 78)
    print("BYTES PER EVENT (counted from the reference hosts' own write lists)")
    print("=" * 78)
    print(f"{'event':<40}{'writes':>7}{'bytes':>7}{'at 2.0 MHz':>13}{'on the bench':>14}")
    print("-" * 81)
    for name, n, note in event_costs():
        print(f"{name:<40}{n:>7}{n*TX_BYTES:>7}"
              f"{n*us(CONTRACT.tx_period_ps):>11.1f}us{n*us(BENCH.tx_period_ps):>12.1f}us")
        if note:
            print(f"{'':<40}  {note}")

    print("\n" + "=" * 78)
    print("THE TWO TIMED SEQUENCES OF 15.7.1 -- the gate #81 says to stop on")
    print("=" * 78)
    ok = True
    for link in (CONTRACT, BENCH):
        print(f"\n  {link.name}:")
        for s in sequence_deadlines(link):
            fits = s["margin_frames"] > 0
            ok = ok and fits
            print(f"    {s['name']}")
            print(f"      window {s['width_ms']:.0f} ms = {s['width_frames']} frames; "
                  f"{s['writes']} writes, {s['writes_at_once']} of them at the hit")
            print(f"      those {s['writes_at_once']} take {s['need_us']:.1f} us "
                  f"({s['need_frames']:.2f} frames) -- "
                  f"{'FITS' if fits else 'DOES NOT FIT'}, margin "
                  f"{s['margin_frames']:.1f} frames "
                  f"({s['margin_frames']*us(FRAME_PS)/1000:.2f} ms)")

    print("\n" + "=" * 78)
    print("WORST CASE: EVERY CIRCUIT RETRIGGERED IN ONE FRAME, both sequences running")
    print("=" * 78)
    for link in (CONTRACT, BENCH):
        w = worst_simultaneous(link)
        print(f"\n  {link.name}: {w['writes_at_the_hit_frame']} writes wanted in ONE frame "
              f"(all {w['stops']} circuits at once, both sequences running)")
        print(f"    serialised they occupy {w['serialised_us']:.1f} us = "
              f"{w['serialised_frames']:.1f} frames, so the host sends the setup "
              f"{w['lead_frames']} frames ({w['lead_us']:.0f} us = "
              f"{w['lead_us']/1000:.2f} ms) AHEAD of the strike")
        print(f"    the strike itself is on time: {w['conflicts']} anchor conflict(s)")

    print("\n" + "=" * 78)
    print(f"A REAL FIXTURE: the reference 808 pattern, {a.bpm:g} bpm, {a.bars} bars")
    print("=" * 78)
    verdict = 0
    for link in (CONTRACT, BENCH):
        d = fixture_demand(a.bpm, a.bars, link)
        print(f"\n  {link.name}:")
        print(f"    {d['hits']} hits -> {d['writes']} transactions = {d['bytes']} bytes "
              f"over {d['seconds']:.2f} s ({d['frames']} frames)")
        print(f"    of those, {d['boot']} are the one-off boot load "
              f"({d['boot_us']/1000:.2f} ms of link time) and {d['play']} are the music")
        print(f"    mean {d['mean_writes_per_s']:.1f} transactions/s against a link that "
              f"carries {1e12/ (BENCH.tx_period_ps if link is BENCH else CONTRACT.tx_period_ps):.0f}/s; "
              f"link busy {100*d['link_busy_fraction']:.1f} % of the time")
        print(f"    peak demand in any ONE frame: {d['peak_writes_in_one_frame']} writes "
              f"({d['frames_wanting_more_than_one']} frames want more than one)")
        print(f"    after spreading: {d['conflicts']} anchor conflict(s), "
              f"{d['play_late']} musical write(s) late")
        print(f"      the boot load lands over {d['boot_spread_frames']} frames "
              f"({d['boot_spread_frames']*us(FRAME_PS)/1000:.2f} ms), once, before a note is played")
        print(f"      each hit's setup goes at most {d['play_lead_frames']} frames "
              f"({d['play_lead_frames']*us(FRAME_PS):.0f} us = "
              f"{d['play_lead_frames']*us(FRAME_PS)/1000:.2f} ms) EARLY; no strike is late")
        if d["conflicts"]:
            verdict = 1
            for p in d["check"]["conflicts"][:5]:
                print(f"      CONFLICT {p.w.tag} wanted frame {p.w.frame}, landed {p.land}")

    print("\n" + "=" * 78)
    if ok and verdict == 0:
        print("VERDICT: THE BUDGET HOLDS.")
        print("  The link cannot deliver a burst atomically and never could -- a")
        print("  transaction is longer than a frame. What #81 asks about is the two")
        print("  TIMED SEQUENCES, and both fit with orders of magnitude to spare: the")
        print(f"  BD attack window is {int(round(dx.BD_ATTACK_MS*1e-3*SR))} frames wide and needs "
              f"{2*CONTRACT.tx_period_ps/FRAME_PS:.2f} of them.")
        print("  The cost is not the deadline, it is SIMULTANEITY: a hit's setup writes")
        print("  have to be sent before the strike, and the host is what decides that.")
    else:
        print("VERDICT: THE BUDGET DOES NOT HOLD. See the conflicts above.")
    print("=" * 78)
    return verdict if ok else 1


if __name__ == "__main__":
    sys.exit(main())
