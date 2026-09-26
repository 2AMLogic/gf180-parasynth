#!/usr/bin/env python3
"""plan081 C: ONE bounded final-strike experiment for D12A -- model only,
diagnostic renders only. Nothing here changes `model/drums_fx.py`, the scorer,
the kit or `docs/scorecard/results/D12A.json`.

QUESTION. Does a STRONGER final strike with an appropriate decay repair the
late envelope without smearing the clap or damaging its other properties?

THE MECHANISM, PROTOTYPED IN THIS FILE ONLY. The shipped envelope re-strikes
at 13/16 of the previous strike, so the 4th strike is (13/16)^3 ~ 54 % of the
first, and PEAK is read only on fire -- no register sequence can raise it.
`FinalStrikeEnv` subclasses `drums_fx.EnvFx` for the clap's burst envelope
alone: identical integer behaviour, except that the LAST re-strike (k ==
bursts) sets `strike <- (fire_level * FINAL_LEVEL_Q16) >> 16` and switches to
`final_rate`. That is the "explicit implementation" #253's review asked for,
as a model experiment; it is NOT an RTL design.

FROZEN BEFORE RENDERING (2026-09-26, commit that adds this file):
  conditions   B0  baseline (kit as shipped)
               T   tail-only ablation: tail tau 47 -> 80 ms (one-record fit, #253)
               L1  T + 4 strikes (period 511) + final strike 0.75 x first, tau 20 ms
               L2  T + 4 strikes (period 511) + final strike 1.00 x first, tau 20 ms
               L3  T + 4 strikes (period 511) + final strike 1.25 x first, tau 20 ms
               C2  archived #253 development experiment: 4 strikes, final strike
                   left at 54 %, host rate rewrite to 38.5 ms, tail 80 ms
  final decay  20 ms -- one value inside the reference's ~18-24 ms observation,
               a development guide, not a constant
  offsets      development DEV (the #253 set) for every condition; FRESH, never
               used before, only for B0 and the ONE selected level, after selection
  anchor       ENERGY WINDOWS are at FIXED GAIN (bus 0.45, accent 1), never
               peak-normalised per candidate. Comparison with the reference uses
               ONE declared anchor: each side's own 0-30 ms energy (the first three
               strikes, which the mechanism does not touch). Labelled as such.
  selection    a level is ELIGIBLE on DEV when
                 (a) burst/tail ratio valid and within tolerance on >= 7/8,
                 (b) decay valid and within tolerance on >= B0's DEV count
                     (non-regression only -- not robust decay qualification),
                 (c) 0 rail samples at accent 0.5 / 1 / 2,
                 (d) NOT a long-tail shift: median anchored 30-50 ms energy within
                     3 dB of the reference's, AND median anchored 80-200 ms energy
                     no more than 3 dB above the reference's.
               Among eligible levels choose the smallest median |ratio error|;
               a tie (< 0.5 dB) goes to the LOWER level. Then FRESH must satisfy
               (a), (b) against B0-on-FRESH, and (d), with no retuning.
  timing       "Burst timing" is reported but is NOT a gate. Its qualification
               is plan081 B's verdict, read from
               docs/scorecard/clap-d12a/burst-timing-qual.json; if the official
               detector is unqualified there, a selected level is at most
               "model improvement; promotion incomplete". Programmed strike
               times are reported from envelope state as the separate quantity.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import sys
from concurrent.futures import ProcessPoolExecutor

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "model"))

import numpy as np  # noqa: E402

import clap_d12a_probe as probe  # noqa: E402
import run_case as rc  # noqa: E402

DEV = [0, 7, 131, 977, 2203, 4099, 7919, 12007]
FRESH = [3301, 5557, 8803, 10501, 14009, 16411, 18503, 20011]
FINAL_TAU = 20e-3
TAIL_TAU = 80e-3
CONDITIONS = {
    "B0 baseline": {},
    "T tail-only (tau 80 ms)": dict(t_tau=TAIL_TAU),
    "L1 final 0.75": dict(t_tau=TAIL_TAU, bursts=3, period=511, final_level=0.75, final_tau=FINAL_TAU),
    "L2 final 1.00": dict(t_tau=TAIL_TAU, bursts=3, period=511, final_level=1.00, final_tau=FINAL_TAU),
    "L3 final 1.25": dict(t_tau=TAIL_TAU, bursts=3, period=511, final_level=1.25, final_tau=FINAL_TAU),
    "C2 archived (54 %, rate 38.5 ms)": dict(t_tau=TAIL_TAU, bursts=3, period=511, final_tau_rewrite=38.5e-3),
}
LEVELS = ["L1 final 0.75", "L2 final 1.00", "L3 final 1.25"]
WINDOWS = [(0, 30), (30, 50), (50, 80), (80, 200)]
ACCENTS = (0.5, 1.0, 2.0)


def _final_strike_env_class():
    import drums_fx as dx

    class FinalStrikeEnv(dx.EnvFx):
        """EnvFx with an explicit final strike. Bit-identical to EnvFx when
        `final_q16` is None (asserted in `main`)."""
        __slots__ = ("final_q16", "final_rate", "fire_level")

        def __init__(self, floor=True, final_q16=None, final_rate=0):
            super().__init__(floor)
            self.final_q16, self.final_rate, self.fire_level = final_q16, final_rate, 0

        def frame(self, fire, accents):
            if self.final_q16 is None:
                return super().frame(fire, accents)
            if self.stop < dx.N_STOPS and (fire >> self.stop) & 1:
                self.level = dx.usat((self.peak * accents[self.stop]) >> 15, dx.ENV_BITS)
                self.strike = self.fire_level = self.level
                self.t = 0
                self.n_fire += 1
            else:
                self.t = min(self.t + 1, dx.T_MAX)
                t = self.t
                last = self.bursts * self.period
                if t < self.hold:
                    pass
                elif self.period and t == last and self.bursts:
                    self.strike = dx.usat((self.fire_level * self.final_q16) >> 16, dx.ENV_BITS)
                    self.level = self.strike
                    self.n_restrike += 1
                elif self.period and any(t == k * self.period for k in (1, 2, 3) if k < self.bursts):
                    self.strike = (self.strike * dx.BURST_C) >> 16
                    self.level = self.strike
                    self.n_restrike += 1
                else:
                    rate = self.final_rate if (self.bursts and t > last) else self.rate
                    dec = (self.level * rate) >> dx.RATE_Q
                    if dec == 0:
                        self.n_floor += self.level > 0
                        if self.floor:
                            dec = 1
                    self.level = max(0, self.level - dec)
            if self.choke < dx.N_STOPS and (fire >> self.choke) & 1:
                self.level = 0
                self.n_choke += 1
    return FinalStrikeEnv


def render(cond: dict, accent: float = 1.0, offset: int = 0, force_subclass: bool = False):
    """One CP hit. Returns (x, sr, strike_times_ms, env_burst_trace)."""
    import drums_fx as dx
    p = dict(probe.DEFAULT, **{k: v for k, v in cond.items() if k in probe.DEFAULT})
    img = dict(dx.kit_with_sounds("CP"))
    for a, v in (dx.env_writes(dx.E_CPBURST, dx.CP, p["b_tau"], p["b_peak"],
                               bursts=p["bursts"], period=p["period"])
                 + dx.env_writes(dx.E_CPTAIL, dx.CP, p["t_tau"], p["t_peak"])):
        img[a] = v
    n = int(rc.SOLO_SECONDS.get("CP", 2.2) * dx.SR) + offset
    hit = int(0.01 * dx.SR) + offset
    d = dx.DrumsFx()
    if "final_level" in cond or force_subclass:
        cls = _final_strike_env_class()
        q16 = int(round(cond["final_level"] * 65536)) if "final_level" in cond else None
        d.envs[dx.E_CPBURST] = cls(d.floor, q16, dx.rate_reg(cond.get("final_tau", 0.0)))
    w = dx.hit_writes([(hit, dx.CP, accent)], sorted(img.items()))
    if "final_tau_rewrite" in cond:
        w += probe.final_rate_writes(dict(p, final_tau=cond["final_tau_rewrite"]), hit)
    dm, bd = d.play(sorted(w, key=lambda t: t[0]), n)
    g = dx.accent_reg(0.45)
    out = dx.output_fx(np.zeros(n), 0, dm, g, bd, g)[offset:]
    eb = d.trace["env"][dx.E_CPBURST][offset:]
    rise = np.where(np.diff(eb) > 0)[0] + 1
    strikes = [round((f - (hit - offset)) / dx.SR * 1e3, 3) for f in rise if f < (hit - offset) + dx.SR // 5]
    # The fire frame is itself a rise from 0, so it is already in `rise`. The
    # first run ALSO prepended 0.0 and reported every schedule with a doubled
    # first strike ([0.0, 0.0, 10.0, 20.0]) -- a reporting bug, not a render one.
    return np.asarray(out, dtype=np.float64) / 32768.0, dx.SR, strikes, eb


def _job(args):
    name, off, acc = args
    x, sr, strikes, eb = render(CONDITIONS[name], acc, off)
    return name, off, acc, x, sr, strikes


def fixed_gain_windows(x, sr) -> dict:
    """dB re FS^2*s at FIXED gain, t = 0 at TRIM_MS before the 2 % onset."""
    xr = np.asarray(x, dtype=np.float64)
    i = rc._onset_index(xr)
    o = i - int(round(rc.TRIM_MS * 1e-3 * sr))
    return {f"{a}-{b}": 10 * math.log10(float(np.sum(xr[max(0, o + int(a * sr / 1e3)):
                                                        o + int(b * sr / 1e3)] ** 2)) / sr + 1e-30)
            for a, b in WINDOWS}


def late_event(x, sr) -> dict:
    """Late event under the early anchor: peak of the 4 ms RMS envelope in
    28-60 ms, in dB re the envelope peak in 0-28 ms; duration = time from that
    late peak until the envelope first falls 20 dB below it."""
    y = rc.prepare(x, sr)
    env = rc.am.rms_envelope(rc.window(y, sr, 0.0, 0.300), 4.0, sr)
    a, b = int(0.028 * sr), int(0.060 * sr)
    ep = float(np.max(env[:a]))
    j = a + int(np.argmax(env[a:b]))
    lp = float(env[j])
    below = np.where(env[j:] < lp * 0.1)[0]
    dur = (below[0] / sr * 1e3) if len(below) else float("nan")
    return {"late_peak_ms": round(j / sr * 1e3, 2), "late_peak_db_re_early_peak": round(20 * math.log10(lp / ep), 2),
            "late_minus20db_duration_ms": round(dur, 2)}


def summarise(rows, ref_anchor):
    """Per-condition: per-offset rows plus counts and medians."""
    out = {}
    for name, rs in rows.items():
        cnt = lambda m: sum(1 for r in rs if r["metrics"][m]["valid"] and r["metrics"][m].get("pass"))
        valid = lambda m: sum(1 for r in rs if r["metrics"][m]["valid"])
        anch = [{w: r["fixed_gain_db"][w] - r["fixed_gain_db"]["0-30"] for w in r["fixed_gain_db"]} for r in rs]
        med = lambda key: round(float(np.median([a[key] for a in anch])), 2)
        out[name] = {
            "n": len(rs),
            "ratio_pass": cnt("burst/tail ratio"), "ratio_valid": valid("burst/tail ratio"),
            "decay_pass": cnt("decay"), "decay_valid": valid("decay"),
            "timing_pass_UNQUALIFIED": cnt("Burst timing"), "timing_valid": valid("Burst timing"),
            "median_abs_ratio_error_db": round(float(np.median([abs(r["metrics"]["burst/tail ratio"]["error"])
                                                                for r in rs if r["metrics"]["burst/tail ratio"]["valid"]])), 3),
            "median_anchored_db": {w: med(w) for w in anch[0]},
            "median_anchored_minus_ref_db": {w: round(med(w) - ref_anchor[w], 2) for w in anch[0]},
        }
    return out


def eligible(s, base_decay):
    why = []
    if s["ratio_pass"] < 7:
        why.append(f"(a) ratio {s['ratio_pass']}/8 < 7")
    if s["decay_pass"] < base_decay:
        why.append(f"(b) decay {s['decay_pass']} < baseline {base_decay}")
    d = s["median_anchored_minus_ref_db"]
    if abs(d["30-50"]) > 3:
        why.append(f"(d) anchored 30-50 ms {d['30-50']:+.2f} dB vs ref")
    if d["80-200"] > 3:
        why.append(f"(d) anchored 80-200 ms {d['80-200']:+.2f} dB above ref")
    return why


def run_set(names, offsets, ref, jobs):
    with ProcessPoolExecutor(max_workers=jobs) as ex:
        res = list(ex.map(_job, [(n, o, 1.0) for n in names for o in offsets]))
    rows = {n: [] for n in names}
    for name, off, acc, x, sr, strikes in res:
        rows[name].append({"offset": off, "metrics": probe.metrics(x, sr, ref),
                           "fixed_gain_db": {k: round(v, 2) for k, v in fixed_gain_windows(x, sr).items()},
                           "late_event": late_event(x, sr), "programmed_strikes_ms": strikes})
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refs", default=str(rc.configured_refs()))
    ap.add_argument("--out", default=str(ROOT / "docs/scorecard/clap-d12a/final-strike.json"))
    ap.add_argument("--jobs", type=int, default=min(6, os.cpu_count() or 1))
    a = ap.parse_args(argv)
    refdir = pathlib.Path(a.refs)
    try:
        basis = probe.basis(refdir, probe.MANIFEST_DEFAULT if probe.MANIFEST_DEFAULT.exists()
                            else refdir.parent / "tr808-fischer-85fbecf.sha256")
    except probe.Refused as e:
        print(f"REFUSED: {e}")
        return 2
    import hashlib
    # precondition: the subclass with no final strike is bit-identical to the shipped envelope
    x0, _, s0, _ = render({}, 1.0, 0)
    x1, _, s1, _ = render({}, 1.0, 0, force_subclass=True)
    xs, _ = rc.render_drum_solo("CP")
    if probe.audio_sha(x0) != probe.audio_sha(xs) or probe.audio_sha(x1) != probe.audio_sha(xs):
        print("REFUSED: the experiment renderer does not reproduce render_drum_solo('CP') bit for bit")
        return 2
    rx, rsr, rel, _ = rc.load_reference("CP", refdir)
    ref = (rc.prepare(rx, rsr), rsr)
    rw = fixed_gain_windows(rx, rsr)
    ref_anchor = {w: rw[w] - rw["0-30"] for w in rw}

    dev = run_set(list(CONDITIONS), DEV, ref, a.jobs)
    sdev = summarise(dev, ref_anchor)
    base_decay = sdev["B0 baseline"]["decay_pass"]
    elig = {n: eligible(sdev[n], base_decay) for n in LEVELS}
    ok = [n for n in LEVELS if not elig[n]]
    selected = None
    if ok:
        best = min(sdev[n]["median_abs_ratio_error_db"] for n in ok)
        selected = next(n for n in ok if sdev[n]["median_abs_ratio_error_db"] <= best + 0.5)

    with ProcessPoolExecutor(max_workers=a.jobs) as ex:
        acc = list(ex.map(_job, [(n, 0, g) for n in CONDITIONS for g in ACCENTS]))
    head = {}
    for name, off, g, x, sr, strikes in acc:
        head.setdefault(name, {})[str(g)] = {
            "peak_fs": round(float(np.max(np.abs(x))), 4),
            "rail_samples": int(np.sum(np.abs(x) >= 32767 / 32768)),
            "rms_fs_0_200ms": round(float(np.sqrt(np.mean(x[:int(0.21 * sr)] ** 2))), 5),
            "ratio_db_level_matched": round(probe.ratio_db(rc.prepare(x, sr), sr), 3)}
    rail = {n: sum(v["rail_samples"] for v in head[n].values()) for n in CONDITIONS}
    for n in LEVELS:
        if rail[n]:
            elig[n].append(f"(c) {rail[n]} rail samples")
    if selected and rail[selected]:
        ok = [n for n in ok if not rail[n]]
        selected = None
        if ok:
            best = min(sdev[n]["median_abs_ratio_error_db"] for n in ok)
            selected = next(n for n in ok if sdev[n]["median_abs_ratio_error_db"] <= best + 0.5)

    qf = ROOT / "docs/scorecard/clap-d12a/burst-timing-qual.json"
    timing_qualified = None
    if qf.exists():
        timing_qualified = json.loads(qf.read_text())["detectors"]["v0 official (min_dip 2 dB)"]["qualified_everywhere"]

    fresh, sfresh, confirm = None, None, None
    if selected:
        fresh = run_set(["B0 baseline", selected], FRESH, ref, a.jobs)
        sfresh = summarise(fresh, ref_anchor)
        why = eligible(sfresh[selected], sfresh["B0 baseline"]["decay_pass"])
        confirm = {"confirmed": not why, "why_not": why}

    out = {"tool": "tools/clap_final_strike_experiment.py", "basis": basis,
           "frozen": {"conditions": CONDITIONS, "dev_offsets": DEV, "fresh_offsets": FRESH,
                      "final_tau_s": FINAL_TAU, "tail_tau_s": TAIL_TAU,
                      "anchor": "each side's own 0-30 ms energy at fixed gain"},
           "renderer_bit_identical_to_shipped": True,
           "burst_timing_qualified_by_B": timing_qualified,
           "reference": {"file": rel, "fixed_gain_db": {k: round(v, 2) for k, v in rw.items()},
                         "anchored_db": {k: round(v, 2) for k, v in ref_anchor.items()},
                         "late_event": late_event(rx, rsr)},
           "dev": {"summary": sdev, "rows": dev}, "eligibility": elig, "selected": selected,
           "fresh": {"summary": sfresh, "rows": fresh, "confirmation": confirm},
           "headroom_offset0": head,
           "label": (None if not selected else
                     ("selected on DEV but NOT confirmed on FRESH" if not (confirm and confirm["confirmed"])
                      else "model improvement; promotion incomplete (burst timing "
                      + ("unqualified)" if not timing_qualified else "qualified by B)")))}
    p = pathlib.Path(a.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=1, default=float) + "\n")

    print(f"{'condition':34s} ratio  decay  timing* |ratio err| anch-ref 30-50 50-80 80-200  pk@2  rail")
    for n, s in sdev.items():
        d = s["median_anchored_minus_ref_db"]
        print(f"{n:34s} {s['ratio_pass']}/8   {s['decay_pass']}/8   {s['timing_pass_UNQUALIFIED']}/8   "
              f"{s['median_abs_ratio_error_db']:7.2f}   {d['30-50']:+6.2f} {d['50-80']:+6.2f} {d['80-200']:+6.2f} "
              f"{head[n]['2.0']['peak_fs']:.3f} {rail[n]}")
    print("eligibility:", elig)
    print("selected:", selected, "| fresh:", confirm)
    if sfresh:
        for n, s in sfresh.items():
            print(f"FRESH {n:28s} ratio {s['ratio_pass']}/8 decay {s['decay_pass']}/8 |err| "
                  f"{s['median_abs_ratio_error_db']} anch-ref {s['median_anchored_minus_ref_db']}")
    print("label:", out["label"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
