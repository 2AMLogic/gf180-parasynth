#!/usr/bin/env python3
"""The TR-808 rimshot's and cowbell's partial balance, measured from hardware,
against what model/drums_fx.py ships.

WHAT THE SCORECARD MEANS BY "Partial balance" -- two different things under one
name, both from tools/run_case.py on origin/tools/case-runner, the branch that
produced the stored D10A/D13A results:

    D10A  RS  band_pair_db((1500,2100), (380,560)) over 0-60 ms
              = 10*log10( energy 1500-2100 Hz / energy 380-560 Hz ), where each
                energy is a zero-phase 4th-order Butterworth band divided by the
                slice's total energy.
    D13A  CB  tone_ratio_db(800, 540) over 0-100 ms
              = 20*log10( |projection at 800 Hz| / |projection at 540 Hz| ), a
                rectangular-window coherent projection at each NOMINAL frequency.

Both are scored against a 3.0 dB tolerance ("energy ratio, the half-power
convention"), and `worst` on the board is |error| / tolerance.

    python tools/measure_partial_balance.py validate    # start red, then the known cases
    python tools/measure_partial_balance.py measure     # the recordings, floor-gated
    python tools/measure_partial_balance.py apparatus   # is the failure the window, not the voice?
    python tools/measure_partial_balance.py decay       # CB: each partial's own tau (issue #107)

Needs the Fischer corpus (tidalcycles/sounds-tr808-fischer) at $TR808_REFS or
/tmp/tr808-ref. REFUSES rather than reports when it is absent.
"""
import math, os, sys, pathlib, hashlib, subprocess, datetime
import numpy as np
from scipy.io import wavfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "tools"))
import audio_measure as am                      # noqa: E402
import partial_trajectory as PT                 # noqa: E402

REFDIR = pathlib.Path(os.environ.get("TR808_REFS", "/tmp/tr808-ref"))
REF_MAIN = {"RS": "rs8/RS.WAV", "CB": "cb8/CB.WAV"}


class Refused(Exception):
    """A precondition of the apparatus failed. REFUSED is a first-class outcome
    here, distinct from pass and from fail."""


# --- the scorecard's two estimators, verbatim, so this compares like with like
def tone_ratio_db(x, sr, hz_num, hz_den):
    a, b = am.tone_amplitude(x, hz_num, sr), am.tone_amplitude(x, hz_den, sr)
    if not (a.ok and b.ok) or a.value <= 0 or b.value <= 0:
        return None
    return 20.0 * math.log10(a.value / b.value)


def band_pair_db(x, sr, band_a, band_b):
    e_a, e_b = am.band_energy(x, (tuple(band_a), tuple(band_b)), sr)
    if e_a <= 0.0 or e_b <= 0.0:
        return None
    return 10.0 * math.log10(e_a / e_b)


def window(y, sr, t0, t1):
    a = int(t0 * sr)
    return y[a: len(y) if t1 is None else min(len(y), int(t1 * sr))]


# --- both sides, prepared exactly as run_case.py prepares them
def prepare(x, sr):
    x = np.asarray(x, dtype=np.float64)
    if am.is_silent(x):
        return x
    pk = float(np.abs(x).max())
    i = int(np.argmax(np.abs(x) > 0.02 * pk))
    lead = max(0, i - int(1e-3 * sr))
    if lead >= int(5e-3 * sr):
        x = x - float(x[:lead].mean())
    y = x[lead:]
    p = float(np.abs(y).max())
    return y / p if p > 0 else y


def sha(p):
    return "sha256:" + hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()[:16]


def load_reference(voice):
    if not REFDIR.exists():
        raise Refused(f"reference corpus not at {REFDIR} -- clone "
                      f"tidalcycles/sounds-tr808-fischer or set TR808_REFS")
    path = REFDIR / REF_MAIN[voice]
    if not path.exists():
        raise Refused(f"reference recording missing: {REF_MAIN[voice]} under {REFDIR}")
    sr, x = wavfile.read(str(path))
    x = np.asarray(x, dtype=np.float64)
    if x.ndim > 1:
        x = x.mean(axis=1)
    if am.is_silent(x / 32768.0):
        raise Refused(f"reference recording {REF_MAIN[voice]} is silent")
    return prepare(x / 32768.0, sr), sr, REF_MAIN[voice], sha(path)


def render_ours(voice, seconds=2.2, accent=1.0, kit=None):
    """One hit of `voice` through the reference host (`hit_writes`) and the
    output stage -- the path a player's image takes. `kit` replaces the
    shipped image (for controls); None is `kit_with_sounds(voice)`."""
    import drums_fx as dx
    n = int(seconds * dx.SR)
    d = dx.DrumsFx()
    dm, bd = d.play(dx.hit_writes([(int(0.01 * dx.SR), dx.SOUND_STOP[voice], accent)],
                                  kit if kit is not None else dx.kit_with_sounds(voice)), n)
    g = dx.accent_reg(0.45)
    out = dx.output_fx(np.zeros(n), 0, dm, g, bd, g)
    return prepare(np.asarray(out, dtype=np.float64) / 32768.0, dx.SR), dx.SR


# Operating points, each with the error VALIDATE establishes for it.
OP = {
    "RS": dict(win_ms=6.0, hop_ms=0.25, lo=(380, 620), hi=(1450, 2150), t_end=0.060,
               at=(0.004, 0.006, 0.008, 0.010, 0.012, 0.016), err_db=2.4),
    "CB": dict(win_ms=20.0, hop_ms=2.0, lo=(460, 700), hi=(700, 1000), t_end=0.600,
               at=(0.030, 0.060, 0.100, 0.200, 0.300, 0.400), err_db=1.1),
}
FLOOR_MARGIN_DB = 6.0          # issue #92: refuse rather than report inside the floor


def damped(f, tau, amp, n, sr, phase=0.0):
    t = np.arange(n) / sr
    return amp * np.exp(-t / tau) * np.sin(2 * math.pi * f * t + phase)


def two_partial(f1, tau1, a1, f2, tau2, a2, seconds, sr):
    n = int(seconds * sr)
    return damped(f1, tau1, a1, n, sr, 0.3) + damped(f2, tau2, a2, n, sr, 1.9)


def balance_traj(x, sr, f_lo, f_hi, op):
    tl, al = PT.trajectory(x, sr, f_lo, win_ms=op["win_ms"], hop_ms=op["hop_ms"], t_end=op["t_end"])
    th, ah = PT.trajectory(x, sr, f_hi, win_ms=op["win_ms"], hop_ms=op["hop_ms"], t_end=op["t_end"])
    return tl, al, th, ah


def cmd_validate():
    SR = 44100
    print("RED FIRST -- the estimator against inputs that carry no decaying partial.")
    for name, x in (("digital silence", np.zeros(int(0.3 * SR))),
                    ("white noise only", 0.01 * np.random.default_rng(0).standard_normal(int(0.3 * SR))),
                    ("a partial that never decays", np.sin(2 * math.pi * 540 * np.arange(int(0.3 * SR)) / SR))):
        ts, a = PT.trajectory(x, SR, 540.0, win_ms=20.0, hop_ms=2.0)
        fl = PT.floor_at(x, SR, 540.0, [412.0, 468.0, 631.0, 702.0], win_ms=20.0, hop_ms=2.0)
        fit = PT.fit_decay(ts, a, floor=fl, sr=SR, win_ms=20.0)
        assert not fit["ok"], f"{name} was not refused -- the bench cannot fail"
        print(f"    {name:30s} REFUSED: {fit['reason']}")

    print("\nKNOWN CASES -- two damped partials, every parameter chosen. "
          "Error in the balance, dB.")
    for voice, cases in (
        ("RS", [(455, 0.0047, 1.0, 1786, 0.0024, 1.0), (455, 0.0047, 1.0, 1786, 0.0024, 0.25),
                (455, 0.0047, 1.0, 1786, 0.0024, 4.0), (455, 0.0032, 1.0, 1786, 0.0024, 1.0),
                (455, 0.0047, 1.0, 1786, 0.0100, 1.0), (455, 0.0032, 1.0, 1786, 0.0014, 2.0)]),
        ("CB", [(540, 0.060, 1.0, 800, 0.060, 1.0), (540, 0.180, 1.0, 800, 0.060, 1.0),
                (540, 0.060, 1.0, 800, 0.180, 1.0), (540, 0.120, 1.0, 800, 0.100, 0.15),
                (540, 0.120, 1.0, 800, 0.100, 6.6), (540, 0.250, 1.0, 800, 0.080, 1.0)])):
        op = OP[voice]
        worst = 0.0
        for f_lo, tl_, al_, f_hi, th_, ah_ in cases:
            x = two_partial(f_lo, tl_, al_, f_hi, th_, ah_, op["t_end"], SR)
            tl, a_l, th, a_h = balance_traj(x, SR, float(f_lo), float(f_hi), op)
            for t in op["at"]:
                i = int(np.argmin(np.abs(tl - t))); j = int(np.argmin(np.abs(th - t)))
                got = 20 * math.log10(a_h[j] / a_l[i])
                truth = 20 * math.log10(ah_ * math.exp(-t / th_) / (al_ * math.exp(-t / tl_)))
                worst = max(worst, abs(got - truth))
        ok = "OK" if worst <= op["err_db"] else "EXCEEDS the declared error"
        print(f"    {voice}: worst balance error over {len(cases)} known cases x "
              f"{len(op['at'])} time points = {worst:.2f} dB "
              f"(declared {op['err_db']:.1f} dB) -- {ok}")
        if worst > op["err_db"]:
            return 1

    print("\nWHAT THE SCORECARD'S OWN ESTIMATORS DO ON THE SAME KNOWN SIGNALS.")
    x = two_partial(540, 0.200, 1.0, 800, 0.200, 1.0, 0.100, SR)     # truth 0.000 dB
    print(f"    tone_ratio_db(800/540), lines exactly on nominal : "
          f"{tone_ratio_db(x, SR, 800.0, 540.0):+7.2f} dB  (truth 0.00)")
    for pct in (1.0, 5.0, 8.7):
        x = two_partial(540 * (1 + pct / 100), 0.200, 1.0, 800 * (1 + pct / 100), 0.200, 1.0, 0.100, SR)
        print(f"    tone_ratio_db(800/540), lines +{pct:.1f} % off nominal : "
              f"{tone_ratio_db(x, SR, 800.0, 540.0):+7.2f} dB  (truth 0.00)")
    x = two_partial(540, 0.200, 1.0, 800, 0.030, 1.0, 0.100, SR)
    print(f"    tone_ratio_db(800/540), equal A0 but tau 200/30 ms: "
          f"{tone_ratio_db(x, SR, 800.0, 540.0):+7.2f} dB  (truth 0.00 at the strike)")
    print("\n    WITHDRAWN, by its own control: band_pair_db applied to a 6 ms SLICE is")
    print("    biased up to 22.6 dB on signals of known balance -- a 380-560 Hz band-pass")
    print("    rings for about as long as the slice, so it cannot settle inside it. Slice")
    print("    the trajectory, never the filter's input.")
    return 0


def cmd_measure():
    def git(*a):
        try:
            return subprocess.check_output(["git", "-C", str(ROOT), *a], text=True).strip()
        except Exception:
            return "?"
    print("PROVENANCE")
    print(f"    commit {git('rev-parse','HEAD')} ({git('rev-parse','--abbrev-ref','HEAD')})  "
          f"dirty={bool(git('status','--porcelain'))}")
    for rel in ("model/drums_fx.py", "model/audio_measure.py", "docs/scorecard/cases.csv"):
        print(f"    {rel:28s} {sha(ROOT/rel)}")
    print(f"    reference corpus            {REFDIR}  (Fischer/Technopolis 1994, CC0-1.0, "
          f"real TR-808 s/n 103852)")
    print(f"    run_at                      {datetime.datetime.now(datetime.timezone.utc):%Y-%m-%dT%H:%M:%SZ}")

    for voice in ("RS", "CB"):
        op = OP[voice]
        print(f"\n{'='*94}\n{voice}   (estimator error +-{op['err_db']:.1f} dB, "
              f"floor margin {FLOOR_MARGIN_DB:.0f} dB)\n{'='*94}")
        sides = {}
        for side in ("reference", "ours"):
            if side == "reference":
                x, sr, rel, h = load_reference(voice); tag = f"{rel} {h}"
            else:
                x, sr = render_ours(voice); tag = "drums_fx render, accent 1.0, bus 0.45"
            nf = float(np.sqrt(np.mean(x[int(0.90 * len(x)):] ** 2)))
            f_lo = PT.find_partial(x, sr, *op["lo"], seconds=op["t_end"])
            f_hi = PT.find_partial(x, sr, *op["hi"], seconds=op["t_end"])
            tl, a_l, th, a_h = balance_traj(x, sr, f_lo, f_hi, op)
            sides[side] = dict(x=x, sr=sr, nf=nf, f_lo=f_lo, f_hi=f_hi, tl=tl, al=a_l, th=th, ah=a_h)
            print(f"  {side:10s} {tag}")
            print(f"             lines actually present: low {f_lo:8.2f} Hz   high {f_hi:8.2f} Hz"
                  f"   record floor RMS {nf:.2e}")
        print(f"\n  {'t (ms)':>8s} {'reference':>11s} {'ours':>11s} {'ERROR':>9s}   headroom over each record's floor")
        for t in op["at"]:
            cell = {}
            for side in ("reference", "ours"):
                d = sides[side]
                i = int(np.argmin(np.abs(d["tl"] - t))); j = int(np.argmin(np.abs(d["th"] - t)))
                hl = 20 * math.log10(d["al"][i] / d["nf"]) if d["nf"] > 0 else float("inf")
                hh = 20 * math.log10(d["ah"][j] / d["nf"]) if d["nf"] > 0 else float("inf")
                ok = hl > FLOOR_MARGIN_DB and hh > FLOOR_MARGIN_DB
                cell[side] = (20 * math.log10(d["ah"][j] / d["al"][i]) if ok else None, hh, hl)
            vr, rh, rl = cell["reference"]; vo, oh, ol = cell["ours"]
            f = lambda v: f"{v:11.2f}" if v is not None else f"{'REFUSED':>11s}"
            e = f"{vo-vr:9.2f}" if (vr is not None and vo is not None) else f"{'-':>9s}"
            print(f"  {t*1e3:8.1f} {f(vr)} {f(vo)} {e}   ref {rh:5.1f}/{rl:5.1f}  ours {oh:5.1f}/{ol:5.1f}")

        print(f"\n  the scorecard's own number, reproduced:")
        for side in ("ours", "reference"):
            d = sides[side]
            v = (band_pair_db(window(d["x"], d["sr"], 0.0, 0.060), d["sr"], (1500, 2100), (380, 560))
                 if voice == "RS" else
                 tone_ratio_db(window(d["x"], d["sr"], 0.0, 0.100), d["sr"], 800.0, 540.0))
            print(f"    {side:10s} {v:+8.4f} dB")
    return 0


def balance_at(x, sr, voice, times):
    """The partial balance (dB, high over low) at each of `times`, by the
    trajectory estimator at `voice`'s operating point -- the numbers `measure`
    prints, as a function, so a test reads them through the same code."""
    op = OP[voice]
    f_lo = PT.find_partial(x, sr, *op["lo"], seconds=op["t_end"])
    f_hi = PT.find_partial(x, sr, *op["hi"], seconds=op["t_end"])
    tl, a_l, th, a_h = balance_traj(x, sr, f_lo, f_hi, op)
    out = {}
    for t in times:
        i = int(np.argmin(np.abs(tl - t))); j = int(np.argmin(np.abs(th - t)))
        out[t] = 20 * math.log10(a_h[j] / a_l[i])
    return out


# Guard frequencies for the per-partial floor (no line of either record there;
# the Fischer unit's lines are 558/824 Hz, ours 540/800).
CB_GUARDS = {"lo": [412.0, 468.0, 631.0, 702.0], "hi": [702.0, 740.0, 900.0, 960.0]}
CB_DECAY_SPAN = (0.030, 0.400)     # the balance table's own span: past the 5 ms slope


def partial_decay(x, sr, voice="CB", span=CB_DECAY_SPAN):
    """Each partial's own tau, fitted over `span` of its trajectory with the
    record's floor measured at guard lines. Returns {"lo": fit, "hi": fit}
    (partial_trajectory.fit_decay dicts, plus the line frequency)."""
    op = OP[voice]
    out = {}
    for tag in ("lo", "hi"):
        f = PT.find_partial(x, sr, *op[tag], seconds=op["t_end"])
        ts, a = PT.trajectory(x, sr, f, win_ms=op["win_ms"], hop_ms=op["hop_ms"], t_end=op["t_end"])
        fl = PT.floor_at(x, sr, f, CB_GUARDS[tag], win_ms=op["win_ms"], hop_ms=op["hop_ms"],
                         t_end=op["t_end"])
        keep = (ts >= span[0]) & (ts <= span[1])
        fit = PT.fit_decay(ts[keep], a[keep], floor=fl[keep], floor_margin_db=FLOOR_MARGIN_DB,
                           sr=sr, win_ms=op["win_ms"])
        fit["f"] = f
        out[tag] = fit
    return out


def cmd_decay():
    """Issue #107: does each cowbell partial decay at its own rate? tau per
    partial, both sides, over the balance table's 30-400 ms. REFUSES a side
    whose fit refuses rather than printing a number for it."""
    print("KNOWN CASES FIRST -- two damped partials of chosen tau.")
    SR = 44100
    worst = 0.0
    for tl_, th_ in ((0.120, 0.100), (0.100, 0.100), (0.180, 0.060)):
        x = two_partial(540, tl_, 1.0, 800, th_, 3.0, OP["CB"]["t_end"], SR)
        got = partial_decay(x, SR)
        for tag, truth in (("lo", tl_), ("hi", th_)):
            if not got[tag]["ok"]:
                raise Refused(f"known case {tag} tau {truth*1e3:.0f} ms refused: {got[tag]['reason']}")
            worst = max(worst, abs(got[tag]["tau_ms"] / (truth * 1e3) - 1))
    print(f"    worst tau error over 3 cases x 2 partials: {100*worst:.2f} %")
    if worst > 0.03:
        raise Refused(f"the tau fit is off by {100*worst:.1f} % on known signals")
    rows = {}
    for side in ("reference", "ours"):
        x, sr = (load_reference("CB")[:2] if side == "reference" else render_ours("CB", seconds=0.8))
        rows[side] = partial_decay(x, sr)
    print(f"\n  {'':10s} {'low line':>10s} {'tau lo':>9s} {'high line':>10s} {'tau hi':>9s} "
          f"{'lo / hi':>8s}")
    for side, r in rows.items():
        if not (r["lo"]["ok"] and r["hi"]["ok"]):
            print(f"  {side:10s} REFUSED  lo: {r['lo'].get('reason')}  hi: {r['hi'].get('reason')}")
            continue
        print(f"  {side:10s} {r['lo']['f']:8.2f}Hz {r['lo']['tau_ms']:7.1f}ms {r['hi']['f']:8.2f}Hz "
              f"{r['hi']['tau_ms']:7.1f}ms {r['lo']['tau_ms']/r['hi']['tau_ms']:8.3f}")
    return 0


def cmd_apparatus():
    """Two unrelated circuits failing one check is what a shared apparatus term
    looks like, so it is tested rather than assumed.

    `prepare()` trims to 1 ms before the onset only when the lead-in is at least
    5 ms. The Fischer WAVs begin AT the strike (onset sample 6 and 10), so they
    get lead = 0 and no trim; our renders lead with 10 ms of digital silence, so
    they get trimmed to exactly 1 ms of lead-in. The two sides' windows therefore
    start at different places relative to their own onsets, and `sosfiltfilt`'s
    odd extension of a segment that starts at full amplitude manufactures an
    edge. Where a band is nearly empty that edge can more than double it.

    The test: prepend silence to each side and watch the number. An edge term
    moves; the voice does not."""
    def pad(x, sr, ms):
        return np.concatenate([np.zeros(int(ms * 1e-3 * sr)), x]) if ms > 0 else x

    for voice, est, lbl in (
            ("RS", lambda x, sr: band_pair_db(window(x, sr, 0.0, 0.060), sr, (1500, 2100), (380, 560)),
             "band_pair_db((1500,2100),(380,560)) 0-60 ms  [filtered -- the edge term lives here]"),
            ("CB", lambda x, sr: tone_ratio_db(window(x, sr, 0.0, 0.100), sr, 800.0, 540.0),
             "tone_ratio_db(800/540) 0-100 ms  [unfiltered -- no filtfilt edge, but a fixed window]")):
        xr, srr, rel, h = load_reference(voice)
        xo, sro = render_ours(voice)
        print(f"\n{voice}  {lbl}")
        print(f"  {'pad ms':>9s} {'reference':>11s} {'ours':>10s} {'error':>9s} {'worst':>7s}   note")
        base = None
        for ms in (0.0, 0.5, 1.0, 2.0, 5.0, 10.0):
            a, b = est(pad(xr, srr, ms), srr), est(pad(xo, sro, ms), sro)
            if a is None or b is None:
                print(f"  {ms:9.2f}   REFUSED"); continue
            if base is None:
                base = a
            note = "both sides as the scorecard has them" if ms == 0.0 else "both sides padded equally"
            print(f"  {ms:9.2f} {a:11.3f} {b:10.3f} {b-a:9.3f} {abs(b-a)/3.0:7.2f}   {note}")
        print(f"  reference padded alone (ours already carries prepare()'s 1 ms lead-in):")
        for ms in (0.5, 1.0, 2.0, 5.0):
            a, b = est(pad(xr, srr, ms), srr), est(xo, sro)
            if a is None or b is None:
                continue
            print(f"  ref+{ms:5.1f} {a:11.3f} {b:10.3f} {b-a:9.3f} {abs(b-a)/3.0:7.2f}   "
                  f"reference moved {a-base:+.3f} dB")
    print("\n  Read it as: how far the number moves is how much of it is the window.")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "validate"
    try:
        sys.exit({"validate": cmd_validate, "measure": cmd_measure,
                  "apparatus": cmd_apparatus, "decay": cmd_decay}[cmd]())
    except Refused as e:
        print(f"REFUSED  {e}")
        sys.exit(2)
