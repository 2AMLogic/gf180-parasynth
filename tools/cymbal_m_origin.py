#!/usr/bin/env python3
"""Where does the 808's 1-2.5 kHz content come from? (#369 step 7, #400)

`docs/scorecard/cymbal-369/low-tail/` measures, with a qualified instrument,
that the 808's 1-2.5 kHz band OUTLASTS its own 3.45 kHz low band at all 25
settings (rho_M(-10) 1.021-1.139) while ours DIES FIRST (0.866-0.894), and that
neither the recordings' noise floor nor the strike onset can account for it. This
module asks the next question of the CIRCUIT rather than of a knob: can the
three-band chain §10 of the reference documents produce that region at all?

The answer is no, and it is a bound rather than a fit. Everything here is
arithmetic on the two digitised W14b artifacts plus the committed measurement;
no candidate is rendered and no kit is changed. It is a probe.

THE ANSWER, and it does not depend on the unresolved inter-band balance
----------------------------------------------------------------------
For each of the three bands, the documented cascade -- its own band-pass at
Q 6, its Sallen-Key high-pass, its tone-stage path and the LEVEL stage -- puts
its energy in M (891-2828 Hz) this far below its energy in Ln (2900-4100 Hz):

    band     M re Ln     Mn re Ln
    low      -13.10 dB   -31.92 dB
    decay    -13.54 dB   -30.30 dB
    short    -16.23 dB   -36.33 dB

A mix of the three cannot beat the best of them. For positive weights a_b,

    (Sum a_b X_b) / (Sum a_b R_b)  <=  max_b (X_b / R_b)

-- a weighted mediant never exceeds the largest of the ratios it mixes -- so NO
inter-band balance, including the one §10 leaves unresolved (TONE_K1's `peak_db`,
9-18 dB uncertain, #396), can put the documented chain's M above **-13.10 dB re
Ln**, or its Mn above **-30.30 dB**. `bound()` states it and `bound_grid()`
checks the algebra against a brute-force sweep of the balance.

The recordings, measured over the first second by the same instrument:

    808, 22 settings   M re Ln  -3.45 .. -5.00 dB (median -3.73)
                       Mn re Ln -13.05 .. -15.20 dB (median -13.75)

**So the real machine has about 9.4 dB more 891-2828 Hz energy, and about
16.6 dB more 891-1782 Hz energy, than the documented three-band chain can
produce at any balance.** That is not a discrepancy inside anybody's tolerance;
it is a missing mechanism. And it is the same region whose DECAY we cannot
reproduce either, which makes one missing mechanism the parsimonious explanation
for both halves of the finding.

§10 names exactly one element it describes qualitatively and does not quantify:
"The VCAs' asymmetric clipping is what makes the sum sizzle; a linear VCA gives
a flat, chorus-like tone." Six square waves beating through an asymmetric
nonlinearity put intermodulation products at DIFFERENCE frequencies, which is
where this energy would have to come from. This module does not test that -- one
question at a time -- it establishes that the linear chain is not the answer, so
that the next candidate is not another filter.

WHAT THIS SAYS ABOUT OUR OWN RENDERS, and it is not what we expected
--------------------------------------------------------------------
    shipped     M re Ln  -3.28 dB   Mn re Ln  -8.91 dB
    candidate 3 M re Ln  -7.34 dB   Mn re Ln -19.96 dB
    808 median  M re Ln  -3.73 dB   Mn re Ln -13.75 dB

The SHIPPED kit's 1-2.5 kHz ENERGY is already close to the machine's (0.45 dB in
M), and candidate 3 moved it 3.6 dB AWAY in M and 6.2 dB away in Mn. What is
wrong in both is the DECAY, not the level -- which is exactly why this needed a
decay instrument, and why the band-balance measurements that came before it
(H-L, the 1/3 octaves) could not see it.

THE HYPOTHESIS THAT WAS WRONG, recorded because it was the obvious one
---------------------------------------------------------------------
§10 records, off Figure 9, that the DECAY band's tone path Ht2 is a 2-pole
band-pass PEAKING AT 972 Hz, with real poles at 610 and 1549 Hz -- inside
Figure 9's plotted range for Ht2 (562-1640 Hz), so measured rather than
extrapolated. The DECAY band also carries the longest of the three envelopes. So
the 808 appears to route its longest envelope straight into the middle of the M
band, while candidate 3 deliberately does not realise that pole (it argues,
correctly over each band's OWN active range, that the tone low-pass and the
LEVEL differentiator cancel, and drops both). That would have been a clean
structural explanation with the right sign.

It is wrong. The table above is computed WITH the tone stage in both cases, and
the tone stage's shape moves the M band by at most **0.80 dB** relative to Ln
(the DECAY band; 0.05 and 0.03 dB for the other two), because each band's own
filters roll off so steeply below 3 kHz that the tone stage barely reaches M at
all. 0.80 dB against a rho gap of 0.25 and an energy gap of 9.4 dB. A candidate
built on this hypothesis would have cost a render cycle to find out.

WRONG-THEN-RIGHT RATE OF THIS MODULE: 2, both caught by a control rather than
by inspection.
  1. The first mixture built each band from one noise generator per ANALYSIS
     band, weighted by that band's energy there relative to its OWN energy in
     Ln. Two errors in one line: it asserted that all three bands contribute
     EQUALLY to Ln (they do not -- only the low band's peak is inside it), and M
     and Mn OVERLAP, so 891-1782 Hz was generated twice. The sweep came out
     BACKWARDS, rho falling from 1.22 to 0.98 as the DECAY band got louder. The
     numbers looked entirely plausible; only the sign was wrong. Now the noise is
     shaped by the band's full documented response over 200 Hz-20 kHz, by FFT.
  2. The rho sweep reported rho_Mn between 0.006 and 0.049 without complaint.
     Those are not measurements: the documented cascade puts Mn 30 dB below Ln,
     far below anything the instrument was qualified on, and a Schroeder curve on
     a band that holds almost nothing crosses every depth at once. The sweep now
     REFUSES a band whose energy is below `MIN_BAND_RE_REF_DB` and says so, which
     turns the nonsense into the finding.
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import subprocess
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "model"), str(ROOT / "tools")]
import run_case as rc  # noqa: E402
import cymbal_low_tail as lt  # noqa: E402
import cymbal_tone_realisation as tr  # noqa: E402

BANDS = dict(lt.BANDS)
REF = lt.REF_BAND
SR = lt.SR
SPAN_HZ = (200.0, 20000.0)

# §10's envelope time constants for the three paths: the low band is "fixed,
# medium", the short one "fixed, short", and the DECAY band's RC runs "up to
# ~0.38 s". The reference's numbers, not fitted ones; `--taus` overrides them so
# the conclusion's dependence on them is shown rather than asserted.
TAU_S = {"low": 0.100, "decay": 0.380, "short": 0.020}

# §10's own first stage: "Trigger -> attack smoother (Q19, tau ~ 0.1 ms) -> three
# envelope generators". It is here because leaving it out is a measurement error,
# not a simplification: an envelope that STEPS from 0 to 1 at t = 0 is a
# broadband click, and in a band that holds almost no steady content the click is
# all the band holds -- which is how the first version of this probe came to
# report rho_Mn of 0.006 (a Schroeder curve falling 10 dB in under 3 ms). A
# documented value from the reference, not a fitted one.
ATTACK_S = 1e-4

# The filter chain's own peak gains put the DECAY band +7.2 dB and the short band
# +10.0 dB above the low band (§10, "What to implement"). The tone stage then
# imposes an inter-band balance Figure 9 does NOT resolve, so the sweep spans far
# past its stated 9-18 dB uncertainty in both directions: a bound that does not
# bracket the answer is not a bound.
CHAIN_DB = {"low": 0.0, "decay": 7.2, "short": 10.0}
SWEEP_DB = (-12.0, -6.0, 0.0, 6.0, 9.0, 12.0, 15.0, 18.0, 24.0, 30.0)

# The instrument's validated range. Across the Fischer settings it answers and
# both of our renders, the analysed band's first-second energy is never more than
# 20 dB below the reference band's. rho on a band far below that is division by
# almost nothing, which is wrong-then-right 2 of this module.
MIN_BAND_RE_REF_DB = -22.0


class Refused(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# what the documented chain can do, as a bound rather than a fit
# ---------------------------------------------------------------------------
def band_energies(bands=None, *, span=SPAN_HZ, n=4000) -> dict:
    """Per band, its energy in each analysis band on ONE COMMON scale (dB,
    arbitrary offset but shared across all three bands), for the 808's documented
    cascade and for candidate 3's realisation of it.

    A common scale is the whole point: the first version normalised each band to
    its own energy in Ln, which silently asserted all three contribute equally
    there (wrong-then-right 1). Both cascades come from
    `cymbal_tone_realisation`, which computed them off the two digitised W14b
    artifacts and REFUSES outside its bound.
    """
    bands = bands or BANDS
    poles = tr.tone_poles()
    corner = tr.level_corner_hz()
    hz = np.geomspace(span[0], span[1], n)
    out = {}
    for band in ("low", "decay", "short"):
        chain = tr.band_chain_db(band, hz)
        got = {}
        for tag, tone in (("ref", tr.analog_target_db(band, hz, poles, corner)),
                          ("cand3", tr.realised_db(band, hz, poles))):
            e = {}
            tot = chain + tone
            for name, (a, b) in bands.items():
                m = (hz >= a) & (hz <= b)
                e[name] = round(10 * math.log10(float(np.trapezoid(10 ** (tot[m] / 10.0), hz[m]))), 3)
            got[tag] = e
        for tag in ("ref", "cand3"):
            for name in bands:
                if name != REF:
                    got[f"{tag}_{name}_re_{REF}"] = round(got[tag][name] - got[tag][REF], 3)
        got["tone_shape_moves_M_by"] = round(got[f"cand3_M_re_{REF}"] - got[f"ref_M_re_{REF}"], 3)
        out[band] = got
    return out


def bound(energies=None, which="ref", bands=("M", "Mn")) -> dict:
    """The best ratio ANY positive mix of the three documented bands can reach.

    For positive weights, (Sum a_b X_b) / (Sum a_b R_b) <= max_b (X_b / R_b): a
    weighted mediant never exceeds the largest ratio it mixes. So the bound is the
    per-band maximum, and it holds for every inter-band balance -- including the
    one the reference leaves unresolved, which is what makes this a bound and not
    a fit."""
    e = energies or band_energies()
    out = {}
    for name in bands:
        per = {b: e[b][f"{which}_{name}_re_{REF}"] for b in e}
        arg = max(per, key=per.get)
        out[name] = {"bound_db": per[arg], "argmax_band": arg, "per_band": per}
    return out


def bound_grid(energies=None, which="ref", lo=-30.0, hi=40.0, step=1.0) -> dict:
    """Brute force over the balance, as a check on the algebra above rather than
    as the result. If the grid ever beats the bound, the lemma is being applied
    wrongly -- and a test asserts it does not, which is the only way to notice."""
    e = energies or band_energies()
    grid = np.arange(lo, hi + 1e-9, step)
    best = {"M": (-1e9, None), "Mn": (-1e9, None)}
    for dx in grid:
        for sy in grid:
            lv = {"low": 0.0, "decay": float(dx), "short": float(sy)}
            tot = {k: sum(10 ** ((e[b][which][k] + lv[b]) / 10.0) for b in e)
                   for k in ("M", "Mn", REF)}
            for name in ("M", "Mn"):
                r = 10 * math.log10(tot[name] / tot[REF])
                if r > best[name][0]:
                    best[name] = (r, lv)
    return {k: {"best_db": round(v[0], 3), "at_levels": v[1]} for k, v in best.items()}


# ---------------------------------------------------------------------------
# what the machine and our renders actually do
# ---------------------------------------------------------------------------
def measured_energies(result: dict) -> dict:
    """The first-second band energies the committed low-tail measurement already
    recorded, as ratios to the reference band. REFUSES a result file whose own
    controls did not pass, for the reason `cymbal_low_tail.summarise` does."""
    if not result.get("controls_pass"):
        raise Refused("this result file's own controls did not pass")
    out = {}
    for wname, w in result["windows"].items():
        rows = {}
        pairs = list(w["fischer"].items()) + [(k, w[k]) for k in ("shipped", "candidate") if k in w]
        for label, m in pairs:
            if "bands" not in m:
                continue
            e = {b: m["bands"][b]["energy_j"] for b in BANDS}
            if min(e.values()) <= 0:
                continue
            rows[label] = {f"{n}_re_{REF}": round(10 * math.log10(e[n] / e[REF]), 3)
                           for n in BANDS if n != REF}
        out[wname] = rows
    return out


def gap(result: dict, *, window="2.0s") -> dict:
    """The headline: how much 1-2.5 kHz energy the documented chain cannot account
    for, as a LOWER bound -- it compares the machine against the best the chain
    can do at ANY balance."""
    b = bound()
    rows = measured_energies(result)[window]
    ours = {k: rows[k] for k in ("shipped", "candidate") if k in rows}
    f = {k: v for k, v in rows.items() if k.startswith("CY")}
    out = {"window": window, "n_808": len(f), "bands": {}}
    for name in ("M", "Mn"):
        key = f"{name}_re_{REF}"
        v = sorted(r[key] for r in f.values())
        med = float(np.median(v))
        out["bands"][name] = {
            "chain_bound_db": b[name]["bound_db"], "bound_from_band": b[name]["argmax_band"],
            "measured_min": v[0], "measured_max": v[-1], "measured_median": round(med, 3),
            "gap_median_db": round(med - b[name]["bound_db"], 3),
            "gap_min_db": round(v[0] - b[name]["bound_db"], 3),
            "n_808_above_bound": sum(1 for x in v if x > b[name]["bound_db"]),
            **{k: r[key] for k, r in ours.items()},
        }
    return out


# ---------------------------------------------------------------------------
# and the decay: no linear balance reaches the 808's rho either
# ---------------------------------------------------------------------------
def _shaped_noise(band, n, seed, sr=SR, which="ref", span=SPAN_HZ):
    """White noise shaped by the band's full documented magnitude response, by
    FFT -- exact over the whole span, rather than a sum of analysis-band
    generators (which double-counts the overlap of M and Mn and leaves the
    response undefined at the band's own 3.45 or 7.1 kHz peak, where nearly all
    of its energy actually is)."""
    poles = tr.tone_poles()
    corner = tr.level_corner_hz()
    f = np.fft.rfftfreq(n, 1.0 / sr)
    fc = np.clip(f, span[0], span[1])
    tone = (tr.analog_target_db(band, fc, poles, corner) if which == "ref"
            else tr.realised_db(band, fc, poles))
    mag = 10 ** ((tr.band_chain_db(band, fc) + tone) / 20.0)
    mag[(f < span[0]) | (f > span[1])] = 0.0
    x = np.fft.rfft(np.random.default_rng(seed).standard_normal(n))
    return np.fft.irfft(x * mag, n)


def envelope(tau, n, sr=SR, attack_s=ATTACK_S):
    """§10's attack smoother followed by the band's exponential decay:
    (1 - exp(-t/attack)) * exp(-t/tau). The attack is what stops the envelope's
    own step at t = 0 from being a broadband click -- see ATTACK_S."""
    t = np.arange(n) / sr
    a = 1.0 - np.exp(-t / attack_s) if attack_s > 0 else np.ones(n)
    return a * np.exp(-t / tau)


def mixture(decay_rel_db, *, taus=None, dur=3.6, seed=3, sr=SR, which="ref", short_rel_db=None,
            attack_s=ATTACK_S):
    """A LINEAR three-band mix from the documented shapes and envelopes, with the
    DECAY band's level relative to the low band set on `band_energies`' common
    scale. Deliberately has no oscillator staircase and no VCA clipping, so that
    a negative answer implicates them rather than hiding them."""
    taus = taus or TAU_S
    n = int(dur * sr)
    rel = {"low": 0.0, "decay": decay_rel_db,
           "short": CHAIN_DB["short"] if short_rel_db is None else short_rel_db}
    y = np.zeros(n)
    for i, band in enumerate(("low", "decay", "short")):
        y = y + 10 ** (rel[band] / 20.0) * _shaped_noise(band, n, seed + 17 * i, sr, which=which) \
            * envelope(taus[band], n, sr, attack_s)
    peak = float(np.max(np.abs(y)))
    if peak <= 0:
        raise Refused("the mixture is silent")
    # The strike is full amplitude at t=0, so a true pre-onset lead has to be
    # prepended or `run_case.prepare` REFUSES -- correctly: without one the
    # analysis filters ring on the record's own first edge, which is #101.
    return rc.prepare(np.concatenate([np.zeros(sr // 20), y / peak]), sr,
                      side=f"mix {which} {decay_rel_db:+.1f} dB"), sr


def sweep(levels=SWEEP_DB, *, which="ref", taus=None, trim_s=lt.TRIM_S) -> list[dict]:
    """rho against the DECAY band's relative level, measured with the SAME
    instrument as the 25 settings, and REFUSING any band whose own energy is more
    than `MIN_BAND_RE_REF_DB` below the reference band's -- outside the range the
    instrument was qualified on (wrong-then-right 2)."""
    rows = []
    for d in levels:
        y, sr = mixture(d, which=which, taus=taus)
        m = lt.measure(y, sr, trim_s=trim_s)
        row = {"decay_rel_db": d, "refused": {}}
        for band in ("M", "Mn"):
            re_ref = 10 * math.log10(m["bands"][band]["energy_j"] / m["bands"][REF]["energy_j"])
            row[f"{band}_re_{REF}"] = round(re_ref, 2)
            if re_ref < MIN_BAND_RE_REF_DB:
                row[f"rho_{band}_-10"] = None
                row["refused"][band] = (f"{band} is {re_ref:.1f} dB below {REF}, under the "
                                        f"{MIN_BAND_RE_REF_DB:.0f} dB the instrument is qualified to")
            else:
                row[f"rho_{band}_-10"] = m["rho"][band]["-10"]
        rows.append(row)
    return rows


def verdict(rows, target=1.048, baseline_max=1.017) -> dict:
    """The lowest swept DECAY level at which a linear mix reaches the 808's
    WEAKEST measured rho_M(-10) (1.048, CY2550 in the 2.0 s window). REFUSES to
    name one when none does -- "no linear balance does this" is the answer the
    probe exists to be able to give."""
    got = [r for r in rows if r.get("rho_M_-10") is not None]
    out = {"target": target, "baseline_max": baseline_max,
           "n_rows": len(rows), "n_answered": len(got),
           "n_refused_M": sum(1 for r in rows if "M" in r["refused"]),
           "n_refused_Mn": sum(1 for r in rows if "Mn" in r["refused"]),
           "max_rho_M_-10": max((r["rho_M_-10"] for r in got), default=None),
           "n_above_baseline": sum(1 for r in got if r["rho_M_-10"] > baseline_max)}
    ok = [r for r in got if r["rho_M_-10"] >= target]
    out["reaches_target_at_db"] = min((r["decay_rel_db"] for r in ok), default=None)
    if out["reaches_target_at_db"] is None:
        out["refused"] = (f"no swept DECAY level in {min(r['decay_rel_db'] for r in rows):+.0f}"
                          f"..{max(r['decay_rel_db'] for r in rows):+.0f} dB reaches rho_M(-10) "
                          f"{target}; the highest reached is {out['max_rho_M_-10']}")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--result", type=pathlib.Path,
                    default=ROOT / "docs/scorecard/cymbal-369/low-tail/low-tail.json")
    ap.add_argument("--out", type=pathlib.Path, default=None)
    ap.add_argument("--taus", default=None, help="low,decay,short seconds; overrides §10's")
    ap.add_argument("--no-grid", action="store_true", help="skip the brute-force check of the bound")
    a = ap.parse_args(argv)

    taus = (dict(zip(("low", "decay", "short"), (float(x) for x in a.taus.split(","))))
            if a.taus else None)

    ok, lines = lt.check()
    if not ok:
        print("\n".join(lines))
        print("REFUSED: the shared instrument's own controls do not pass")
        return 1

    e = band_energies()
    res = {"taus": taus or TAU_S, "energies": e, "bound": bound(e), "controls_pass": ok}
    print("each band's documented cascade, dB relative to its OWN energy in Ln:")
    print(f"  {'band':7s} {'M (808)':>9s} {'M (cand3)':>10s} {'tone moves M':>13s}   {'Mn (808)':>9s}")
    for band, w in e.items():
        print(f"  {band:7s} {w[f'ref_M_re_{REF}']:9.2f} {w[f'cand3_M_re_{REF}']:10.2f} "
              f"{w['tone_shape_moves_M_by']:+13.2f}   {w[f'ref_Mn_re_{REF}']:9.2f}")
    print(f"\nthe tone stage's SHAPE moves M by at most "
          f"{max(abs(w['tone_shape_moves_M_by']) for w in e.values()):.2f} dB relative to Ln. "
          f"It is not the difference.")
    for name, b in res["bound"].items():
        print(f"  no mix of the three can beat {name} re {REF} = {b['bound_db']:+.2f} dB "
              f"(the {b['argmax_band']} band's own ratio)")
    if not a.no_grid:
        res["bound_grid"] = bound_grid(e)
        for name, g in res["bound_grid"].items():
            print(f"    brute force over the balance agrees for {name}: {g['best_db']:+.2f} dB")

    if a.result.is_file():
        res["gap"] = gap(json.loads(a.result.read_text()))
        print(f"\nagainst the machine ({res['gap']['n_808']} Fischer settings, "
              f"{res['gap']['window']} window):")
        for name, g in res["gap"]["bands"].items():
            print(f"  {name} re {REF}: chain bound {g['chain_bound_db']:+.2f} dB, 808 measured "
                  f"{g['measured_min']:+.2f}..{g['measured_max']:+.2f} "
                  f"(median {g['measured_median']:+.2f}) -> the chain is SHORT by "
                  f"{g['gap_median_db']:.2f} dB at the median and {g['gap_min_db']:.2f} dB at the "
                  f"closest setting; {g['n_808_above_bound']}/{res['gap']['n_808']} settings "
                  f"exceed the bound")
            print(f"     ours: shipped {g.get('shipped')} dB, candidate {g.get('candidate')} dB")
    else:
        print(f"\n(no measurement at {a.result}; the energy gap is not reported)")

    res["sweep"] = sweep(which="ref", taus=taus)
    res["verdict"] = verdict(res["sweep"])
    print("\nand the decay: rho of a LINEAR mix vs the DECAY band's level re the low band")
    for r in res["sweep"]:
        s = "; ".join(f"{k}: {v}" for k, v in r["refused"].items())
        print(f"  {r['decay_rel_db']:+6.1f} dB   rho_M(-10) {r['rho_M_-10']}"
              + (f"   REFUSED {s}" if s else f"   rho_Mn(-10) {r['rho_Mn_-10']}"))
    v = res["verdict"]
    print(f"\nREFUSED to name a level: {v['refused']}" if v["reaches_target_at_db"] is None else
          f"\nreaches the 808's weakest rho_M(-10) at {v['reaches_target_at_db']:+.1f} dB")

    res["commit"] = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                   capture_output=True, text=True).stdout.strip()
    res["sources_dirty"] = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", "model", "tools"],
                                          cwd=ROOT).returncode != 0
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(res, indent=1, default=float) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
