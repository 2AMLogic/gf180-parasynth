#!/usr/bin/env python3
"""Are the references trustworthy? Demo artefacts, and run-to-run variance.

    .venv/bin/python model/reference_integrity.py --stage demo
    .venv/bin/python model/reference_integrity.py --stage variance

Two questions that have to be settled before any number measured against a
commercial plugin means anything, and neither had been asked.

**1. Is this a demo?** u-he's Diva demo inserts intermittent crackling;
Arturia's demos insert noise bursts. Both are SPARSE IMPULSIVE EVENTS against
an otherwise stationary spectrum, so a short render or any averaged measure
misses them entirely -- every measurement in `docs/discrimination.md` section
8 is short and averaged. The test is a long held note and a search for
isolated broadband transients.

Surge XT is the control: it is free and open source, has no demo mode, and
whatever the detector reports on Surge is its floor.

**2. How much do repeated renders differ?** There were no repeated renders
anywhere in this harness, so "7.92 percentage points against Surge Type 2's
0.62" was quoted without knowing the run-to-run spread of either number. If
Mini V3's vintage variation or Diva's analogue drift is on, repeated renders
differ BY DESIGN and a difference smaller than that spread is not a
difference. This measures the spread and reports it beside every quantity.

The detector is checked against a known answer first: a clean render with a
synthetic click train added must be found, and the same render without it must
come back clean.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "audition"))

import audio_measure as am                                          # noqa: E402
import reference_rigs as rr                                         # noqa: E402

SR = rr.SR


# ===========================================================================
# the detector
# ===========================================================================
TRANSIENT_BLOCK_MS = 5.0     # the un-pitched default block
TRANSIENT_PERIODS = 2        # blocks per note period when f0 is known
TRANSIENT_FLOOR_DB = 1.0     # minimum threshold above the median, pitched path only


def transient_report(x, sr: int = SR, *, hp_hz: float = 6000.0,
                     block_ms: float | None = None, k: float = 12.0, skip_s: float = 0.5,
                     f0_hz: float | None = None,
                     periods: int = TRANSIENT_PERIODS,
                     floor_db: float = TRANSIENT_FLOOR_DB) -> dict:
    """Isolated broadband transients against a stationary background.

    A held note through a synthesiser is stationary: its short-time energy
    above `hp_hz` sits at a steady floor. A click is broadband and brief, so
    it appears as a small number of blocks whose high-band energy is far above
    that floor. The threshold is `k` times the MEDIAN ABSOLUTE DEVIATION of
    the block levels, not their standard deviation -- one loud click inflates
    a standard deviation enough to hide itself.

    Reported against a Gaussian expectation: for a stationary Gaussian process
    the block levels are tightly clustered and the count above 12 MADs is
    essentially zero, so any nonzero count is an event and not a tail.

    **A pitched source needs `f0_hz`.** A saw (or any source with an edge per
    cycle) puts one broadband burst in the high band every period. With a
    fixed block that is not a whole number of periods, blocks hold a varying
    count of edges, and the blocks that happen to hold one more are outliers
    against the median: a clean MIDI 36 saw at 5 ms blocks reported 359
    "events" in 6 s -- one per period -- and MIDI 60 (3.8 ms period, 1.3
    periods per block) failed the same way while MIDI 45 and 72 did not
    (issue #225). With `f0_hz` the block is `periods` whole periods,
    truncated, so every block holds the same number of edges bar a sliver of
    grid drift that can only LOWER a block. This is the block
    `tools/capture_m1a_phase_cycle.click_report` already uses.

    An explicit `block_ms` shorter than `periods` periods of `f0_hz` is
    REFUSED rather than measured: it is the configuration that produced the
    false events above.

    The pitched path also floors the threshold at `floor_db` above the median.
    Whole-period blocks of a clean saw are so alike that the MAD collapses,
    and the sub-sample grid drift (a block of int(2P) samples cuts a
    fraction of an edge) then crossed `k*MAD`: 1 to 34 false events per 8 s
    from MIDI 45 to 84 with the period block alone (second wrong-then-right
    of #225). Measured over MIDI 33-84 on a full-band synthetic saw: clean
    blocks peak <= 0.6 dB over the median, injected clicks (0.25 x peak,
    12 samples) >= 3.7 dB. The floor sits between; it is the same 1 dB as
    `capture_m1a_phase_cycle.CLICK_FLOOR_DB`. A click under 1 dB of the
    block's own high-band energy is below this detector's resolution: on a
    full-band saw (every harmonic to Nyquist) about 85 % of 0.25 x peak
    12-sample clicks are caught -- a click split across a block boundary, or
    a small random draw, sits under the floor -- and all of 0.5 x peak; on
    the 24-partial saw of `tools/test_capture_m1a_phase_cycle` every 0.25 x
    peak click is caught. (A one-period block halves the dilution but its
    clean jitter reached 1.04 dB at MIDI 60, over the floor.)

    Without `f0_hz` the behaviour is unchanged (5 ms blocks, no floor)."""
    if f0_hz is not None:
        if not (f0_hz > 0 and math.isfinite(f0_hz)):
            raise ValueError(f"transient_report: f0_hz must be positive, got {f0_hz!r}")
        if int(periods) != periods or periods < 1:
            raise ValueError(f"transient_report: periods must be a positive integer, got {periods!r}")
        period_samples = sr / f0_hz
        if block_ms is None:
            nb = max(8, int(periods * period_samples))
        else:
            nb = max(8, int(block_ms * sr / 1000.0))
            if nb < int(periods * period_samples):
                raise am.InsufficientEvidence(
                    f"transient_report: block_ms={block_ms} is {nb / period_samples:.2f} "
                    f"periods of f0={f0_hz:.2f} Hz; need >= {periods} or every cycle "
                    f"edge reads as a click (issue #225)")
    else:
        nb = max(8, int((TRANSIENT_BLOCK_MS if block_ms is None else block_ms) * sr / 1000.0))
    block_ms = nb * 1000.0 / sr if f0_hz is not None else (
        TRANSIENT_BLOCK_MS if block_ms is None else block_ms)
    x = _mono(x)[int(skip_s * sr):]
    n = len(x)
    if n < sr:
        raise am.InsufficientEvidence("transient_report: need at least one second")
    # a difference high-pass: no FFT wrap artefact, no filter design, and its
    # response above hp_hz is flat enough for a detector
    d = np.diff(x, n=2)
    nblocks = len(d) // nb
    lev = np.sqrt((d[:nblocks * nb].reshape(nblocks, nb) ** 2).mean(axis=1))
    med = float(np.median(lev))
    mad = float(np.median(np.abs(lev - med))) or 1e-30
    thr = med + k * mad
    if f0_hz is not None:
        thr = max(thr, med * 10 ** (floor_db / 20.0))
    hot = np.nonzero(lev > thr)[0]
    # group adjacent blocks into events
    events, run = [], []
    for i in hot:
        if run and i - run[-1] > 2:
            events.append(run)
            run = []
        run.append(int(i))
    if run:
        events.append(run)
    peak_ratio = float(lev.max() / med) if med > 0 else float("inf")
    return dict(seconds=n / sr, n_blocks=nblocks, block_ms=block_ms, block_samples=nb,
                f0_hz=f0_hz,
                median_level=med, mad=mad, threshold=thr,
                n_hot_blocks=int(len(hot)), n_events=len(events),
                events_per_minute=len(events) / (n / sr) * 60.0,
                per_minute=[int(sum(1 for e in events
                                    if m <= e[0] * nb / sr < m + 60.0))
                            for m in np.arange(0, n / sr, 60.0)],
                peak_over_median=peak_ratio,
                peak_over_median_db=20 * math.log10(max(peak_ratio, 1e-12)),
                event_times_s=[round(e[0] * nb / sr, 3) for e in events[:40]],
                max_sample_step=float(np.abs(np.diff(x)).max()),
                rms=float(am.rms(x)))


def _mono(x):
    x = np.asarray(x, dtype=np.float64)
    return x.mean(axis=0) if x.ndim > 1 else x


def inject_clicks(x, sr: int = SR, every_s: float = 7.0, amp_rel: float = 0.25,
                  n_samples: int = 12, seed: int = 4) -> np.ndarray:
    """A click train of a stated size, for checking the detector finds one."""
    rng = np.random.default_rng(seed)
    y = np.array(_mono(x), dtype=np.float64)
    a = amp_rel * float(np.abs(y).max() or 1.0)
    for t in np.arange(every_s, len(y) / sr, every_s):
        i = int(t * sr)
        y[i:i + n_samples] += a * rng.standard_normal(min(n_samples, len(y) - i))
    return y


# ===========================================================================
# a long steady note from each reference
# ===========================================================================
def steady(name, seconds=40.0, note=45, source="saw", block=rr.BLOCK):
    """A long held note. `source`:

      'saw'      each synth's own sawtooth. USELESS as a crackle background:
                 a band-limited saw still has a broadband event once per
                 cycle, and the first run of this detector duly reported
                 3300 "events" per minute on Mini V3 and Diva and none on
                 Surge -- which is a difference in oscillator anti-aliasing,
                 not a demo artefact. Kept because it is what a real patch
                 sounds like and the numbers are worth having.
      'smooth'   a sine or triangle: no per-cycle discontinuity, so the
                 high band is flat and anything in it is an event.
      'silence'  every oscillator and the noise at zero, the note still held
                 so the VCA is open. The decisive test: a demo artefact
                 inserted at the output is present even when the instrument
                 is producing nothing, and here there is nothing else at all.
    """
    if name == "surge":
        d = rr.SurgeRig("Type 2")
        d.set(d.I['osc1_type'], {"saw": 0.0238, "smooth": 0.0938,
                                 "silence": 0.0238}[source])   # Classic / Sine
        d.set(d.I['osc1_level'], 0.0 if source == "silence" else 1.0)
        d.set(d.I['osc1_mute'], 1.0 if source == "silence" else 0.0)
        d.set(d.I['f1_cut'], d.cut_value(2000.0))
        d.set(d.I['f1_res'], 0.2)
        d.note = note
        y = d.render(np.zeros(1), seconds)
    elif name == "miniv3":
        d = rr.MiniV3Rig(block=block)
        d.set(d.I['lvl_ext'], 0.0)
        d.set(d.I['ext_sw'], 0.0)
        d.set(48, {"saw": 0.4083, "smooth": 0.075, "silence": 0.075}[source])  # Wave Osc1
        d.set(d.I['lvl_o1'], 0.0 if source == "silence" else 0.9)
        d.set(d.I['o1'], 0.0 if source == "silence" else 1.0)
        d.set(d.I['cutoff'], 0.55)
        d.set(d.I['emphasis'], 0.2)
        d.note = note
        y = d.render(np.zeros(1), seconds)
    elif name == "diva":
        d = rr.DivaRig("rough")
        if source == "smooth":
            d.set(d.I['osc_model'], 0.9)                # Digital
            d.set(144, 0.9333)                          # DigitalType1 = Triangle
        else:
            d.set(d.I['osc_model'], 0.0917)             # Triple VCO
        d.set(d.I['vol1'], 0.0 if source == "silence" else 1.0)
        d.set(d.I['noisevol'], 0.0)
        d.p.set_automation(d.I['noisevol'], np.zeros(int(seconds * SR) + SR, dtype=np.float32))
        d.set(d.I['vcf_freq'], d.freq_value(2000.0))
        d.set(d.I['vcf_res'], 0.2)
        d.note = note
        y = d.render(np.zeros(1), seconds)
    else:
        raise KeyError(name)
    del d
    return np.asarray(y, dtype=np.float64)


# ===========================================================================
def stage_demo(devices, seconds, out, source="smooth", block=rr.BLOCK):
    rows = {}
    for name in devices:
        print(f"\n-- {name}: {seconds:.0f} s, one held note, source={source}", flush=True)
        y = steady(name, seconds, source=source, block=block)
        r = transient_report(y)
        rows[f"{name}-{source}"] = r
        print(f"   rms {r['rms']:.4f}   peak {np.abs(y).max():.5f}   "
              f"peak/median high-band {r['peak_over_median_db']:+6.1f} dB   "
              f"events {r['n_events']} ({r['events_per_minute']:.2f}/min)   "
              f"per minute {r['per_minute']}", flush=True)
        if r["n_events"]:
            print(f"   at {r['event_times_s'][:12]} s", flush=True)
        # the detector's own control, on this very render
        c = transient_report(inject_clicks(y, amp_rel=0.25))
        rows[f"{name}-{source}+injected"] = c
        print(f"   CONTROL, same render with a click every 7 s at 0.25 x peak: "
              f"{c['n_events']} events ({c['peak_over_median_db']:+.1f} dB) "
              f"-- detector {'WORKS' if c['n_events'] >= int(seconds // 7) else 'FAILED'}",
              flush=True)
        np.save(os.path.join(out, f"steady-{name}-{source}.npy"), y.astype(np.float32))
    return rows


# ===========================================================================
# run-to-run variance
# ===========================================================================
VAR_CUTS = [100.0, 400.0, 1600.0, 6400.0]


def _tracking_once(name, cache):
    """f_osc / commanded cutoff at each cutoff, at maximum resonance -- the
    quantity behind "7.92 percentage points against Surge Type 2's 0.62"."""
    import reference_compare as rc
    dev = None if name == "ours" else rc.build(name)
    try:
        errs = {}
        for hz in VAR_CUTS:
            if name == "ours":
                y = rr.OurLadder().ring(hz, 2.0, seconds=0.8)
                cmd = hz
            else:
                cs, chz = rc.cut_setting_for(dev, name, hz, cache)
                y = dev.ring(cs, rc.res_grid(name)[-1], seconds=0.8)
                cmd = chz if chz is not None else hz
            e = am.dominant_frequency(y, hz * 0.3, hz * 2.5, SR)
            z = am.zero_crossing_frequency(y, SR)
            f = (z.value if (z.ok and e.ok and abs(z.value - e.value) / e.value < 0.02)
                 else (e.value if e.ok else None))
            errs[hz] = None if not f else (f / cmd - 1) * 100
        return errs
    finally:
        del dev


def stage_variance(devices, repeats):
    """Repeat the same configuration from a FRESH plugin instance and report
    the spread. Until this exists no difference between two references is
    interpretable, because nobody knows what a difference of zero looks like.

    `ours` is the control: it is deterministic, so its spread must be exactly
    zero. A nonzero spread there would mean the harness, not the instrument."""
    import reference_compare as rc
    cache = {}
    out = {}
    for name in devices:
        runs = [_tracking_once(name, cache) for _ in range(repeats)]
        out[name] = runs
        print(f"\n-- {name}: {repeats} repeats, fresh instance each", flush=True)
        print(f"   {'cutoff':>8} {'mean err %':>11} {'spread pp':>10} {'sd pp':>8}", flush=True)
        for hz in VAR_CUTS:
            v = [r[hz] for r in runs if r.get(hz) is not None]
            if not v:
                print(f"   {hz:8.0f}  no self-oscillation"); continue
            print(f"   {hz:8.0f} {np.mean(v):+11.3f} {max(v) - min(v):10.4f} "
                  f"{np.std(v):8.4f}", flush=True)
        drifts = [max(x for x in r.values() if x is not None)
                  - min(x for x in r.values() if x is not None)
                  for r in runs if any(x is not None for x in r.values())]
        if drifts:
            print(f"   DRIFT ACROSS THE RANGE (the quoted statistic): "
                  f"{np.mean(drifts):.2f} pp, run-to-run spread {max(drifts) - min(drifts):.3f} pp",
                  flush=True)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stage", default="demo", choices=["demo", "variance"])
    ap.add_argument("--devices", default="surge,miniv3,diva")
    ap.add_argument("--seconds", type=float, default=40.0)
    ap.add_argument("--source", default="smooth", choices=["saw", "smooth", "silence"])
    ap.add_argument("--block", type=int, default=rr.BLOCK,
                    help="plugin host block size (Mini V3 apparatus)")
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--out", default="/tmp/refint")
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    devs = [d for d in a.devices.split(",") if d]
    if a.stage == "demo":
        if a.block <= 0:
            ap.error("--block must be positive")
        r = stage_demo(devs, a.seconds, a.out, a.source, block=a.block)
    else:
        r = stage_variance(devs, a.repeats)
    json.dump(r, open(os.path.join(a.out, f"{a.stage}-{a.source}.json"), "w"),
              indent=1, default=str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
