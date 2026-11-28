#!/usr/bin/env python3
"""Where the ladder starts to self-oscillate, and at what k. Measured on the
fixed-point filter, then compared with the linearised-loop prediction the
resonance-compensation ROM is built from (voice_fx.k_onset, DR 0006).

For each cutoff the onset is bisected: the filter is rung with a short sine
burst, then left alone, and the tail's envelope either grows (k above onset)
or decays (below). Inside the tanh table's first bin the fixed-point loop is
exactly linear apart from truncation, so the sign of the growth rate is
amplitude-independent and the onset is a sharp number. The input stage sees
k * fb, four times the output, so "inside the first bin" means an output
under about 1200 LSB (state 0.25 / 4 / 1.15); the burst amplitude is adapted
to keep the tail between 150 and 1200 LSB, and the windows are 20 cycles so
that at a high cutoff they end before the oscillation reaches its limit cycle.

    .venv/bin/python model/k_comp_sweep.py            # ~3 min; the table DR 0006 quotes
    .venv/bin/python model/k_comp_sweep.py --quick    # fewer cutoffs, coarser bisection
"""
import argparse, json, math, os, pathlib, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "audition"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_REFUSAL_TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools")
if _REFUSAL_TOOLS not in sys.path:
    sys.path.append(_REFUSAL_TOOLS)
import numpy as np
import dsp, fixed
import voice_fx as vf
from dsp import SR
import refusal


class Refused(refusal.Refused, RuntimeError):
    """A precondition is not met; nothing was attempted."""


def check_json_destination(name: str) -> None:
    """Make `--json`'s parent directory, or REFUSE the path.

    Called before the first cutoff is bisected, so a path that names a
    directory, or whose parent cannot be made, is refused in seconds. It used
    to raise at the write instead, after the whole sweep (minutes): exit 1 and
    a traceback, with every row lost (#2097). A parent that is there but not
    writable passes here -- `mkdir(exist_ok=True)` cannot see permission -- and
    is REFUSED at the write."""
    p = pathlib.Path(name)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except OSError as why:
        raise Refused(f"--json {name}: its parent cannot be made ({why})") from why
    if p.is_dir():
        raise Refused(f"--json {name} is a directory, not a file to write")


CUTS = [30, 50, 100, 200, 300, 400, 600, 800, 1000, 1200, 1600, 2000, 2500, 3000,
        4000, 5000, 6000, 8000, 10000, 12000, 15000, 18000, 21600]
QUICK = [30, 200, 800, 2000, 3000, 6000, 10000, 15000, 21600]


def ring(k_q14: int, g: int, f_exc: float, amp: float, n_burst: int, n_tail: int) -> np.ndarray:
    """amp * sin(f_exc) for n_burst frames, then silence; the int16 tail as float."""
    n = n_burst + n_tail
    x = np.zeros(n, dtype=np.int16)
    x[:n_burst] = np.round(amp * np.sin(2 * np.pi * f_exc * np.arange(n_burst) / SR)).astype(np.int16)
    lad = fixed.LadderFx(**vf.LADDER_CFG)
    y = lad.process(x, None, k_q14 / 65536.0, 1.0, g_q16=np.full(n, g, dtype=np.int64))
    return y[n_burst:].astype(np.float64)


def growth(tail: np.ndarray, n_settle: int, n_win: int):
    """ln(rms(window 2) / rms(window 1)) per second, and the peak seen."""
    a = tail[n_settle:n_settle + n_win]
    b = tail[n_settle + n_win:n_settle + 2 * n_win]
    ra, rb = math.sqrt((a ** 2).mean()), math.sqrt((b ** 2).mean())
    return math.log(max(rb, 1e-9) / max(ra, 1e-9)) / (n_win / SR), float(np.abs(tail[n_settle:n_settle + 2 * n_win]).max())


def period_hz(tail: np.ndarray) -> float:
    """Frequency from interpolated upward zero crossings, first to last."""
    s = np.signbit(tail)
    up = np.nonzero(s[:-1] & ~s[1:])[0]
    if len(up) < 3:
        return float("nan")
    t = up + (-tail[up]) / (tail[up + 1] - tail[up])
    return (len(t) - 1) / ((t[-1] - t[0]) / SR)


def measure(cut: int, iters: int = 14, verbose=False):
    rom = vf.make_g_rom()
    g = int(vf.g_from_cut(np.array([cut]), rom)[0])
    k_lin, f_lin = vf.k_onset(cut, rom)
    n_cyc = SR / cut
    n_burst = max(int(0.02 * SR), int(20 * n_cyc))
    n_settle = max(int(0.005 * SR), int(10 * n_cyc))
    n_win = max(int(0.01 * SR), int(20 * n_cyc))
    amp = 300.0
    lo, hi = int(3.6 * 16384), int(5.6 * 16384)

    AMPS = (100.0 / 256, 100.0 / 64, 100.0 / 16, 100.0 / 4, 100.0, 400.0, 1600.0, 6400.0)

    def rate_at(k):
        """Growth rate of the tail at this k, from the burst amplitude at which
        the tail sits inside the loop's linear range (peak < 1200 LSB) and
        above the truncation floor (peak > 150). If no burst in AMPS gives
        such a tail: a tail that is large even for the smallest burst is an
        oscillation the loop sustains on its own (above onset, +100); one
        that is small even for the largest has decayed to nothing (below,
        -100)."""
        tails = {}
        i = AMPS.index(100.0)
        while 0 <= i < len(AMPS):
            tail = ring(k, g, f_lin, AMPS[i], n_burst, n_settle + 2 * n_win)
            r, peak = growth(tail, n_settle, n_win)
            tails[i] = (r, peak, tail)
            if 150 <= peak <= 1200:
                return r, tail
            if peak > 1200:
                if i - 1 in tails:                 # bounced: 4x less burst gives < 300
                    return 100.0, tail             # -> the response is not linear in the burst
                i -= 1
            else:
                if i + 1 in tails:
                    return 100.0, tail
                i += 1
        if i < 0:
            return 100.0, tails[0][2]
        return -100.0, tails[len(AMPS) - 1][2]

    r_lo, _ = rate_at(lo); r_hi, _ = rate_at(hi)
    assert r_lo < 0 < r_hi, (cut, r_lo, r_hi)
    for _ in range(iters):
        mid = (lo + hi) // 2
        r, tail = rate_at(mid)
        if r > 0:
            hi = mid
        else:
            lo = mid
        if verbose:
            print(f"    k={mid} ({mid/16384:.5f}) rate {r:+.3f}/s", file=sys.stderr)
    # the oscillation frequency, measured just above onset where it sustains
    _, tail = rate_at(hi)
    f_meas = period_hz(tail[n_settle:])
    return dict(cut=cut, g=g, k_lin=k_lin, f_lin=f_lin, k_lo=lo, k_hi=hi,
                k_meas=(lo + hi) / 2 / 16384, f_meas=f_meas)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--json", type=str, default=None, help="write the rows here")
    ap.add_argument("-v", action="store_true")
    a = ap.parse_args(argv)
    if a.json:
        try:
            check_json_destination(a.json)
        except Refused as why:
            print(f"REFUSED: {why}", file=sys.stderr)
            return 2
    cuts = QUICK if a.quick else CUTS
    iters = 10 if a.quick else 14
    rows = []
    print("ONSET OF SELF-OSCILLATION  (k = 4*res in the loop; 'lin' is voice_fx.k_onset, 'meas' is bisected on the fixed-point filter)")
    print(f"| {'cut Hz':>6} | {'g':>5} | {'k lin':>7} | {'k meas':>7} | {'meas/lin':>8} | {'k meas Q3.14':>12} | {'f_osc lin':>9} | {'f_osc meas':>10} | {'f_osc/cut':>9} |")
    print("|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    t0 = time.time()
    for cut in cuts:
        r = measure(cut, iters, a.v)
        rows.append(r)
        print(f"| {cut:6} | {r['g']:5} | {r['k_lin']:7.4f} | {r['k_meas']:7.4f} | {r['k_meas']/r['k_lin']:8.4f} | "
              f"{r['k_lo']:6}..{r['k_hi']:<5} | {r['f_lin']:9.1f} | {r['f_meas']:10.1f} | {r['f_meas']/cut:9.4f} |", flush=True)
    print(f"  ({time.time()-t0:.0f} s)")

    print("\nRESIDUAL WITH THE COMPENSATION ROM APPLIED  (res at the measured onset, i.e. k_meas / (4 * kc(cut)); 1.0000 is perfect)")
    sizes = (3, 4, 5, 6, 7)
    print(f"| {'cut Hz':>6} | " + " | ".join(f"{1<<b:>4} entries" for b in sizes) + " |")
    print("|---:|" + "---:|" * len(sizes))
    worst = {b: 0.0 for b in sizes}
    worst_lin = {b: 0.0 for b in sizes}
    for r in rows:
        cells = []
        for b in sizes:
            kc = int(vf.kc_from_cut(np.array([r["cut"]]), vf.make_k_rom(b), b)[0])
            res = r["k_meas"] * 32768 / (4 * kc)
            res_lin = r["k_lin"] * 32768 / (4 * kc)
            worst[b] = max(worst[b], abs(res - 1)); worst_lin[b] = max(worst_lin[b], abs(res_lin - 1))
            cells.append(f"{res:11.4f}")
        print(f"| {r['cut']:6} | " + " | ".join(cells) + " |")
    print("| worst | " + " | ".join(f"{100*worst[b]:9.2f} %" for b in sizes) + " |")
    print("| interp only | " + " | ".join(f"{100*worst_lin[b]:9.3f} %" for b in sizes) + " |")
    print("  'interp only' is the ROM against its own generator (linear interpolation error alone);")
    print("  'worst' adds the measured-vs-linearised gap, which is the truncation in the loop.")
    if a.json:
        try:
            with open(a.json, "w") as fh:
                json.dump(rows, fh, indent=1)
        except OSError as why:
            print(f"REFUSED: the table above was swept, but --json {a.json} could not be "
                  f"written ({why})", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
