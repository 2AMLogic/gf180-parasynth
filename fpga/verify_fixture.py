#!/usr/bin/env python3
"""fpga/verify_fixture.py -- A MUSICAL FIXTURE THROUGH THE PRODUCTION CONTROL
PATH, BIT-COMPARED AGAINST model/synth_top_model.py.

    .venv/bin/python fpga/verify_fixture.py
    .venv/bin/python fpga/verify_fixture.py --wrong no-coef-seq --expect-fail

This is issue #81's acceptance test. Everything the instrument has demonstrated
so far was made by poking internal model state, or by `verify_synth_top.py`'s
coverage script, which bursts writes at the pins and then asks the model where
they happened to land. Neither is a musician.

WHAT RUNS HERE:

  fpga/fixtures.py  a bass line and a drum part -- key events, hits with
                    accents, three knobs
        |
  fpga/spi_host.py  the host: the register map, the reference conversions, the
                    TIMED COEFFICIENT SEQUENCES of contract 15.7.1, and a
                    schedule that says which frame each transaction lands in
        |
  48-bit frames     {F, 6'b0, SEC, A[7:0], D[31:0]}, one per write, over CS_N,
                    SCK and MOSI -- rtl-sketch/tb_top_bx.v's master
        |
  synth_top.v       the chip, unmodified
        |
  I2S wire          decoded from BCLK, LRCLK and SDATA the way a DAC does
        |
  == compared, with no tolerance, against ==
  model/synth_top_model.py driven by the HOST'S OWN PREDICTED FRAMES

THE ANTI-CIRCULARITY, which is the whole design. The model is driven by frames
the host computed BEFORE the simulation ran, from `spi_host.LinkTiming`. The
chip is separately required to agree: for every write, the host's prediction,
the frame the CS_N PIN predicts, and the frame the write actually drained in
must all be the same number. So a scheduler that is wrong about the link fails
here even when the audio would have matched, and a link that delays a write
cannot drag the model along with it.

  --wrong NAME   send a DELIBERATELY WRONG transaction stream while the model
                 keeps the correct schedule. This is docs/verification-rules.md
                 rule 1 for a host rather than for RTL: a bench that has never
                 been watched fail is not a bench.

                 no-coef-seq    the host loads the kit and forwards triggers
                                and does NOTHING ELSE -- the bridge #81 warns
                                about. Both sequences of 15.7.1 vanish.
                 drop-restore   the BD attack window opens and never closes
                 late-window    the window closes at 16 ms instead of 4 ms
                 no-tom-bend    the toms lose their diode pitch drop
                 drop-tom-step  one step of one bend goes missing
                 burst          the host sends every transaction back to back
                                with no schedule at all -- the naive bridge

  --fixture N    bar808 (default), bar808-full, demo
  --link NAME    bench (default, what tb_top_bx's master actually does) or max
  --expect-fail  exit 0 only if the comparison gave 1
  --frames N     override the run length

Exit status, as verify_ladder.py: 0 identical, 1 differed, 2 did not run --
which includes REFUSED.

THE HOST DECIDES NO VALUE. It sends whatever the current model specifies, and
what is verified here is the PATH, not the value -- a host that "fixed" a
coefficient would verify neither. The toms' pitch drop was the standing example:
it shipped at an inferred x1.7 while 99 hardware files measured x1.06 / x1.14 /
x1.24 by accent (#110), and this fixture sent x1.7. The correction has landed in
`drums_fx` and the fixture now sends the measured law, unchanged in either
case.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import spi_host as sh                                        # noqa: E402  (sets up sys.path)
import numpy as np                                           # noqa: E402
import drums_fx as dx                                        # noqa: E402
import synth_top_model as stm                                # noqa: E402
import fixtures                                              # noqa: E402
import verify_synth_top as vst                               # noqa: E402  (rtl-sketch/)
from spi_host import FRAME_PS, TX_BYTES, LinkTiming, Placed  # noqa: E402

LINKS = {"bench": sh.BENCH, "max": sh.CONTRACT}
LAST: dict = {}                        # what the last run actually found


# ---- the deliberately wrong transaction streams -----------------------------
def corrupt(laid: list, how: str) -> tuple:
    """Return (writes to SEND, a sentence saying what was done). The model keeps
    the uncorrupted schedule, so whatever this removes or moves is exactly what
    the comparison has to notice."""
    if not how:
        return list(laid), ""
    keep, note = [], ""
    if how == "no-coef-seq":
        keep = [w for w in laid if not w.tag.startswith(("bd-attack", "tom-bend"))]
        note = (f"dropped all {len(laid) - len(keep)} timed coefficient writes "
                f"(15.7.1): the kit is loaded and triggers are forwarded, nothing else")
    elif how == "drop-restore":
        keep = [w for w in laid if w.tag != "bd-attack-restore"]
        note = (f"dropped {len(laid) - len(keep)} BD attack-window RESTORE writes: "
                f"the window opens at 130 Hz / Q 6 and never closes")
    elif how == "late-window":
        late = 768                                       # 16 ms instead of 4 ms
        keep = [replace(w, frame=w.frame + late) if w.tag == "bd-attack-restore" else w
                for w in laid]
        n = sum(1 for w in laid if w.tag == "bd-attack-restore")
        note = (f"delivered {n} BD attack-window RESTORE writes {late} frames "
                f"({late * FRAME_PS / 1e9:.1f} ms) late: a 20 ms window, not 4 ms")
    elif how == "no-tom-bend":
        keep = [w for w in laid if w.tag != "tom-bend"]
        note = f"dropped all {len(laid) - len(keep)} tom pitch-drop writes (15.7.1)"
    elif how == "drop-tom-step":
        idx = [i for i, w in enumerate(laid) if w.tag == "tom-bend"]
        drop = set(idx[6:8]) if len(idx) >= 8 else set(idx[:2])
        keep = [w for i, w in enumerate(laid) if i not in drop]
        note = (f"dropped ONE step ({len(drop)} writes) out of the middle of a "
                f"60 ms tom pitch drop")
    elif how == "burst":
        keep = list(laid)
        note = ("sent every transaction back to back with no schedule: the naive "
                "bridge, which is what the writes land like when nobody decides")
    else:
        raise SystemExit(f"verify_fixture: unknown --wrong {how!r}")
    return keep, note


def place_burst(writes: list, link: LinkTiming) -> list:
    out, t_free = [], link.t_rst_ps
    for w in writes:
        out.append(Placed(w, 0, t_free, link.land_frame(t_free)))
        t_free += link.tx_period_ps
    return out


# ---- the run ----------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fixture", default="bar808", choices=sorted(fixtures.FIXTURES))
    ap.add_argument("--link", default="bench", choices=sorted(LINKS))
    ap.add_argument("--wrong", default=None)
    ap.add_argument("--expect-fail", action="store_true")
    ap.add_argument("--frames", type=int, default=None)
    ap.add_argument("--outdir", default=os.path.join(ROOT, "build", "fixture"))
    a = ap.parse_args(argv)
    a.outdir = os.path.abspath(a.outdir)
    os.makedirs(a.outdir, exist_ok=True)
    link = LINKS[a.link]

    # ---- the music, and what it claims to contain ---------------------------
    host, n_fix, cover = fixtures.FIXTURES[a.fixture]()
    print(f"verify_fixture: fixture {a.fixture!r} -- {cover['writes']} register writes, "
          f"{cover['strikes']} drum strikes, {cover['gates']} gate/trigger events, "
          f"{cover['knobs']} knob turns")
    # Preconditions of the CLAIM, checked before anything is simulated. A fixture
    # that stopped containing the sequences it exists to exercise must refuse,
    # not pass: that is the only difference between this and a recording.
    want = dict(bd_hot=2, bd_restore=2, tom_bend=2 * (dx.TOM_DROP_STEPS + 1), knobs=3,
                strikes=3, gates=2)
    missing = {k: (cover.get(k, 0), v) for k, v in want.items() if cover.get(k, 0) < v}
    if missing:
        print("verify_fixture: REFUSED -- the fixture does not contain what this bench "
              "verifies: " + ", ".join(f"{k} {g} < {v}" for k, (g, v) in missing.items()))
        return 2
    print(f"verify_fixture: contains {cover['bd_hot']//2} BD attack window(s) "
          f"({cover['bd_hot']} hot + {cover['bd_restore']} restore writes, "
          f"{int(round(dx.BD_ATTACK_MS))} ms each) and "
          f"{cover['tom_bend'] // (2 * (dx.TOM_DROP_STEPS + 1))} tom pitch drop(s) "
          f"({cover['tom_bend']} writes, {int(round(dx.TOM_DROP_MS))} ms each) -- "
          f"contract 15.7.1, the part a trigger bridge does not reproduce")

    # ---- the schedule the MODEL is told about, computed before any simulation --
    laid = sh.lay_out(host.w, link)
    truth = sh.place(laid, link)
    st = sh.check(truth)
    if st["conflicts"]:
        p = st["conflicts"][0]
        print(f"verify_fixture: REFUSED -- the host cannot deliver its own schedule: "
              f"{len(st['conflicts'])} anchor(s) slipped, first {p.w.tag} frame "
              f"{p.w.frame} -> {p.land}")
        return 2
    print(f"verify_fixture: link {link.name} -- {len(truth)} transactions, "
          f"{len(truth) * TX_BYTES} bytes, one every {link.tx_period_ps / FRAME_PS:.3f} frames "
          f"({link.tx_period_ps / 1e6:.3f} us); musical events quantised by at most "
          f"{st['anchor_jitter_us']:.1f} us ({st['anchor_jitter']} of them); "
          f"BD window width change worst {st['window_stretch_frames']:+d} frames")

    # ---- what is actually SENT ---------------------------------------------
    sent_w, note = corrupt(laid, a.wrong)
    sent = place_burst(sent_w, link) if a.wrong == "burst" else sh.place(sent_w, link)
    if a.wrong:
        print(f"verify_fixture: WRONG STREAM {a.wrong!r} -- {note}")
        moved = sum(1 for p, q in zip(truth, sent)
                    if p.w is q.w and p.land != q.land)
        print(f"verify_fixture: the transaction stream now has {len(sent)} writes against "
              f"the model's {len(truth)}; {moved} surviving write(s) also changed frame")

    with open(os.path.join(a.outdir, "top_bx_cmds.txt"), "w") as fh:
        fh.write("\n".join(sh.cmd_lines(sent)) + "\n")

    last = max(p.land for p in truth + sent)
    n = a.frames or max(n_fix, last + 600)
    tail = max(1, n - last)
    print(f"verify_fixture: writes land in frames {min(p.land for p in sent)}..{last}; "
          f"modelling {n} frames ({n / sh.SR * 1000:.0f} ms)")

    # ---- the chip, at its pins ---------------------------------------------
    out = vst.simulate([], a.outdir, tail)
    if out is None:
        return 2
    rep = "\n".join(out["report"])
    mb, ms_ = vst.RE_BUSY.search(rep), vst.RE_STRB.search(rep)
    if not mb or not ms_:
        print("verify_fixture: REFUSED -- the bench did not report the frame budget")
        return 2
    busy, overrun, overflow = (int(x) for x in mb.groups())
    strobed, missing_s, worst = (int(x) for x in ms_.groups())
    LAST.update(busy_at_tick=busy, overrun=overrun, overflow=overflow,
                frames_no_sample=missing_s, worst_strobe_cycle=worst)
    if busy or overrun or overflow or missing_s:
        print(f"verify_fixture: FAIL -- frame budget not met: busy at {busy} tick(s), "
              f"overrun {overrun}, link overflow {overflow}, {missing_s} frame(s) with no sample")
        return 1

    # ---- did every transaction arrive, intact, in the frame the HOST predicted? --
    wr = vst.rows(out["wrs"])
    if len(wr) != len(sent):
        print(f"verify_fixture: FAIL -- {len(wr)} writes reached the register port, "
              f"{len(sent)} were sent")
        if not wr:
            return 2
    bad = sum(1 for p, g in zip(sent, wr)
              if (int(g[1]), int(g[2]), int(g[3]), int(g[4])) != p.w.key())
    pin_bad = [(i, p, int(g[5])) for i, (p, g) in enumerate(zip(sent, wr))
               if int(g[5]) != p.land]
    drain_bad = sum(1 for p, g in zip(sent, wr) if int(g[0]) != p.land)
    LAST.update(writes_sent=len(sent), writes_seen=len(wr), writes_bad=bad,
                pin_bad=len(pin_bad), drain_bad=drain_bad)
    if bad:
        print(f"verify_fixture: FAIL -- {bad} of {len(sent)} transactions arrived corrupted "
              f"(that is the link: run rtl-sketch/verify_ctl.py)")
        return 1
    # THE SCHEDULER'S OWN VALIDATION. spi_host.LinkTiming predicted every landing
    # frame before iverilog ran; the CS_N pin says where they actually landed.
    if pin_bad or drain_bad:
        i, p, got = pin_bad[0] if pin_bad else (0, sent[0], -1)
        print(f"verify_fixture: FAIL -- the host's link timing model is wrong: "
              f"{len(pin_bad)} of {len(sent)} transactions landed in a frame it did not "
              f"predict ({drain_bad} also disagree with the drain). First: write {i} "
              f"({p.w.tag}) predicted {p.land}, the pin says {got}")
        return 1
    print(f"verify_fixture: every one of {len(sent)} transactions arrived intact and in the "
          f"frame spi_host predicted, confirmed at the CS_N pin and at the drain")

    # ---- the model, on the frames the host predicted ------------------------
    m = stm.SynthTopModel().run(sh.model_writes(truth), n)
    exp_i2s, exp_s = m["i2s"], m["sample"]
    peak = int(np.abs(exp_s).max())
    rms = float(np.sqrt(np.mean(exp_s.astype(np.float64) ** 2)))
    LAST.update(model_peak=peak, model_rms=rms)
    print(f"verify_fixture: model peak |sample| {peak} of 32768, RMS {rms:.0f} "
          f"({20 * np.log10(max(rms, 1e-9) / 32768):.1f} dBFS); drums |dmix| max "
          f"{int(np.abs(m['dmix']).max())}, |body| max {int(np.abs(m['body']).max())}")
    # A fixture whose model output is silence would compare two silences and pass.
    if peak < 1000:
        print(f"verify_fixture: REFUSED -- the model's output peaks at {peak} of 32768: "
              f"this fixture is not music and a comparison against it proves nothing")
        return 2

    # ---- the comparison: the WIRE against the MODEL, no tolerance ------------
    i2s = vst.rows(out["i2s"])
    if not i2s:
        print("verify_fixture: FAIL -- no I2S periods decoded from the wire")
        return 2
    nper = min(len(i2s), n)
    mism = swap = width = 0
    first = None
    for r in i2s[:nper]:
        p_, left, right, nbl, nbr = (int(r[0]), int(r[1]), int(r[2]), int(r[3]), int(r[4]))
        if p_ >= n:
            break
        if nbl != 32 or nbr != 32:
            width += 1
        e = int(exp_i2s[p_])
        if left != e:
            mism += 1
            if first is None:
                first = (p_, e, left)
        if right != left:
            swap += 1
    sm = vst.rows(out["samp"])
    core_bad = sum(1 for r in sm if int(r[0]) < n and int(r[1]) != int(exp_s[int(r[0])]))
    LAST.update(periods=nper, wire_mismatch=mism, swap=swap, width=width, core_bad=core_bad)

    if mism == 0 and swap == 0 and width == 0 and core_bad == 0:
        print(f"verify_fixture: PASS -- {nper} I2S periods "
              f"({nper / sh.SR * 1000:.0f} ms of audio) decoded from the wire, every one "
              f"identical to the model. A musical fixture entered through the production "
              f"control path -- {len(sent)} 48-bit transactions, "
              f"{cover['bd_hot'] // 2} attack window(s) and "
              f"{cover['tom_bend'] // (2 * (dx.TOM_DROP_STEPS + 1))} pitch drop(s) among "
              f"them -- and came out bit-exact.")
        return 0
    print(f"verify_fixture: FAIL -- of {nper} decoded I2S periods: {mism} differ from the "
          f"model, {swap} have L != R, {width} have a slot that is not 32 BCLK")
    if first:
        p_, e, g = first
        print(f"  first wire mismatch at period {p_} ({p_ / sh.SR * 1000:.1f} ms): "
              f"model {e}, wire {g}, error {g - e:+d} LSB")
    d = exp_i2s[:nper].astype(np.float64)
    w_ = np.array([int(r[1]) for r in i2s[:nper]], dtype=np.float64)
    err = w_ - d
    print(f"  worst |error| {int(np.abs(err).max())} LSB; error RMS {np.sqrt(np.mean(err**2)):.1f} "
          f"LSB vs signal RMS {np.sqrt(np.mean(d**2)):.1f} LSB "
          f"({20 * np.log10(max(np.sqrt(np.mean(err**2)), 1e-9) / max(np.sqrt(np.mean(d**2)), 1e-9)):+.1f} dB)")
    print(f"  the core's own sample stream: {core_bad} of {len(sm)} frames differ")
    return 1


if __name__ == "__main__":
    st_ = main()
    if "--expect-fail" in sys.argv:
        w = sys.argv[sys.argv.index("--wrong") + 1] if "--wrong" in sys.argv else "?"
        if st_ == 1:
            print(f"verify_fixture: wrong stream {w!r} CAUGHT (comparison failed as required)")
            sys.exit(0)
        print(f"verify_fixture: WRONG STREAM NOT CAUGHT (status {st_})")
        sys.exit(1 if st_ == 0 else 2)
    sys.exit(st_)
