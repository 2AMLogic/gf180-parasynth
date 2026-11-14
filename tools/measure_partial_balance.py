#!/usr/bin/env python3
"""The TR-808 rimshot's and cowbell's partial balance, measured from hardware,
against what model/drums_fx.py ships.

WHAT IS MEASURED -- the quantity the scorecard scores, not a second one beside
it. Since #109 both D10A (RS) and D13A (CB) "Partial balance" are scored by
`run_case.balance_trajectory_db`: the two REAL lines' amplitude ratio,
20*log10( A_high(t) / A_low(t) ), read from each line's own decay trajectory,
with the lines located by search rather than probed at a nominal frequency
(#108). It is NOT a fixed window's filtered energy ratio -- that quantity is
A^2*tau, so it double-charges the decay already scored by "tail decay".

This tool reports that ratio as a trajectory, at every time point in the
operating point's `at` list, so a balance that falls through the note is
visible rather than sampled once at whatever instant a single window lands on.
Both rows are scored against a 3.0 dB tolerance ("energy ratio, the half-power
convention"), and `worst` on the board is |error| / tolerance.

The operating points are TAKEN FROM `run_case.RS_BALANCE_OP` / `CB_BALANCE_OP`
(see OP below), never copied, so the window, hop, search ranges and `t_end`
here cannot drift from the ones D10A/D13A are scored at.

    python tools/measure_partial_balance.py validate    # start red, then the known cases
    python tools/measure_partial_balance.py measure     # the recordings, floor-gated

Needs the Fischer corpus (tidalcycles/sounds-tr808-fischer) at $GF180_TR808_REFS (alias
$TR808_REFS), else /tmp/tr808-ref. REFUSES rather than reports when it is absent.
"""
import math, os, sys, pathlib, hashlib, subprocess, datetime
import numpy as np
from scipy.io import wavfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "tools"))
import audio_measure as am                      # noqa: E402
import partial_trajectory as PT                 # noqa: E402
import preparation_contract as pc               # noqa: E402
import run_case as rc                           # noqa: E402

REFDIR = rc.configured_refs()
REF_MAIN = {"RS": "rs8/RS.WAV", "CB": "cb8/CB.WAV"}


# A precondition of the apparatus failed. REFUSED is a first-class outcome here,
# distinct from pass and from fail. The same class `run_case.prepare` and the
# preparation contract raise, so the CLI's handler below catches all three.
Refused = pc.Refused


# --- both sides, prepared by run_case.prepare (#132: the lead is guaranteed,
# not clamped). A private copy is how this file came to carry the #101 clamp
# after run_case had moved on (#636), and how it went on carrying pre-#108
# `tone_ratio_db`/`band_pair_db` after #109 retired both from these two rows
# (#772). Nothing here is reimplemented; `tools/test_measure_partial_balance.py`
# pins that.
def prepared(x, sr, side):
    return rc.prepare(x, sr, side=side)


def origin(y, sr):
    """`y` from the window origin (TRIM_MS before the onset), dropping the
    guaranteed silent lead -- for the trajectory code, which times its windows
    from sample 0 and does no zero-phase filtering across the edge."""
    return y[rc.required_lead_samples(sr):]


def prepare_pair(ref, ours):
    """Prepare both sides, then REFUSE -- naming every violating field -- unless
    they are in the same apparatus state. `ref` and `ours` are (x, sr, side
    name); returns the two prepared arrays. Both sides are prepared by the same
    function, which is what #101 looked like too: the check is on the result."""
    ry, oy = prepared(*ref), prepared(*ours)
    pc.check_prepared_pair(pc.Side(ref[2], ry, ref[1]), pc.Side(ours[2], oy, ours[1]))
    return ry, oy


def sha(p):
    return "sha256:" + hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()[:16]


def load_reference(voice):
    """The recording, UNPREPARED: the caller prepares it, so the pair can be checked."""
    if not REFDIR.exists():
        raise Refused(f"reference corpus not at {REFDIR} -- clone "
                      f"tidalcycles/sounds-tr808-fischer or point GF180_TR808_REFS at it")
    path = REFDIR / REF_MAIN[voice]
    if not path.exists():
        raise Refused(f"reference recording missing: {REF_MAIN[voice]} under {REFDIR}")
    sr, x = wavfile.read(str(path))
    x = np.asarray(x, dtype=np.float64)
    if x.ndim > 1:
        x = x.mean(axis=1)
    if am.is_silent(x / 32768.0):
        raise Refused(f"reference recording {REF_MAIN[voice]} is silent")
    return x / 32768.0, sr, REF_MAIN[voice], sha(path)


def render_ours(voice, seconds=2.2, accent=1.0):
    """Our render, UNPREPARED (see load_reference)."""
    import drums_fx as dx
    n = int(seconds * dx.SR)
    d = dx.DrumsFx()
    dm, bd = d.play(dx.hit_writes([(int(0.01 * dx.SR), dx.SOUND_STOP[voice], accent)],
                                  dx.kit_with_sounds(voice)), n)
    g = dx.accent_reg(0.45)
    out = dx.output_fx(np.zeros(n), 0, dm, g, bd, g)
    return np.asarray(out, dtype=np.float64) / 32768.0, dx.SR


# Operating points. The window, hop, search ranges and `t_end` are READ FROM the
# scorecard's own rows, so this tool cannot measure at a different operating
# point than the one it is a diagnostic for. `guards`/`min_gap_ms` are
# deliberately not taken: this tool reads the trajectory at chosen instants
# (`at`) rather than letting the estimator pick the earliest floor-clearing one,
# so it has no in-gap candidate to guard. `at` and `err_db` -- the error VALIDATE
# establishes for this tool at that operating point -- are its own.
def _op(scored, *, at, err_db):
    return dict(win_ms=scored["win_ms"], hop_ms=scored["hop_ms"], t_end=scored["t_end"],
                lo=scored["f_lo_range"], hi=scored["f_hi_range"], at=at, err_db=err_db)


OP = {
    "RS": _op(rc.RS_BALANCE_OP, at=(0.004, 0.006, 0.008, 0.010, 0.012, 0.016), err_db=2.4),
    "CB": _op(rc.CB_BALANCE_OP, at=(0.030, 0.060, 0.100, 0.200, 0.300, 0.400), err_db=1.1),
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

    print("\nWhy a fixed window was retired from these two rows, and where the\n"
          "evidence now lives (#772 -- it was reproduced here from pre-#108 copies\n"
          "that no longer matched the shipped estimators of the same name):\n"
          "    a nominal-frequency probe's detuning loss -- tools/test_run_case.py\n"
          "      ::test_tone_ratio_db_finds_a_detuned_line_the_nominal_probe_misses\n"
          "      and ::test_tone_ratio_db_refuses_rather_than_falling_back_to_nominal\n"
          "    a filtered band's A^2*tau and band-edge terms -- tools/run_case.py\n"
          "      `band_pair_db`'s declared domain, and tools/test_run_case.py\n"
          "      ::test_band_pair_db_refuses_partials_that_decay_at_different_rates\n"
          "    both measured against the CURRENT estimators -- tools/probes/\n"
          "      estimator_domains.py and tools/probes/verify_109_claims.py")
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
        raw = {}
        x, sr, rel, h = load_reference(voice)
        raw["reference"] = (x, sr, f"the reference recording {rel}", f"{rel} {h}")
        x, sr = render_ours(voice)
        raw["ours"] = (x, sr, f"our {voice} render", "drums_fx render, accent 1.0, bus 0.45")
        prep = dict(zip(("reference", "ours"),
                        prepare_pair(raw["reference"][:3], raw["ours"][:3])))
        for side in ("reference", "ours"):
            sr, tag = raw[side][1], raw[side][3]
            y = prep[side]
            x = origin(y, sr)
            nf = float(np.sqrt(np.mean(x[int(0.90 * len(x)):] ** 2)))
            f_lo = PT.find_partial(x, sr, *op["lo"], seconds=op["t_end"])
            f_hi = PT.find_partial(x, sr, *op["hi"], seconds=op["t_end"])
            tl, a_l, th, a_h = balance_traj(x, sr, f_lo, f_hi, op)
            sides[side] = dict(x=y, sr=sr, nf=nf, f_lo=f_lo, f_hi=f_hi, tl=tl, al=a_l, th=th, ah=a_h)
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
    return 0


# The CLI, as data: `tools/test_measure_partial_balance.py` checks the usage
# block in the module docstring against this table, because `apparatus` stayed
# advertised there for months after nothing ran it (#772).
COMMANDS = {"validate": cmd_validate, "measure": cmd_measure}


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "validate"
    if cmd not in COMMANDS:
        print(f"unknown command {cmd!r}; expected one of {', '.join(COMMANDS)}")
        sys.exit(64)
    try:
        sys.exit(COMMANDS[cmd]())
    except Refused as e:
        print(f"REFUSED  {e}")
        sys.exit(2)
