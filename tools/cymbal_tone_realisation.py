#!/usr/bin/env python3
"""How the measured TR-808 tone stage is realised on the drum bank, and the error of doing so (#369/#396).

Step 4 (`docs/scorecard/cymbal-369/tone-stage/`) measured the tone stage off
W14b Figure 9 and deliberately did NOT apply it: three of the six numbers it
needs are 9-18 dB uncertain. What IS resolved is each band's SHAPE -- one RC
high-pass cascaded with one RC low-pass -- and this tool decides how that shape
reaches the bank, and states the error of every simplification, BEFORE a
candidate is rendered.

It answers one question: for each band, what does the bank actually implement
for (tone stage x LEVEL stage), and how far is that from the measured analog
cascade over the part of the spectrum where the band has energy?

Everything is read from the two committed artifacts -- `werner-fig9.json` (the
tone stage) and `werner-fig4.json` (the LEVEL corner, and the band filters'
values) -- and from `model/cymbal_candidate.py`, so the tool and the model
cannot drift apart. No recording is read: this is transfer-function arithmetic
against a digitised figure, and it is why the render still has to be run.

  --report     the per-band tables and the realisation
  --check      the gate: five named properties, and a properties x defects
               matrix over six injected controls (verification rule 4)
  --json PATH  write the evidence record

REFUSES rather than answering when a coefficient will not fit its register,
when an artifact is absent, or when a band's realisation exceeds SHAPE_BOUND_DB.
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "model"), str(ROOT / "tools")]
import drums_fx as dx                      # noqa: E402
import modal_fixed as mf                   # noqa: E402
import cymbal_candidate as cc              # noqa: E402
import werner_fig4 as wf                   # noqa: E402
import werner_fig9 as w9                   # noqa: E402

SR = dx.SR
BAND_OF = {"low": "Ht1", "decay": "Ht2", "short": "Ht3"}
BANDS = ("low", "decay", "short")

# `tools/cymbal_bands.py`'s 1/3-octave centres -- where the CY5025 residual is
# reported -- imported rather than restated.
THIRDS = np.array(w9.THIRD_OCTAVE_HZ, dtype=float)

# The level rule's calibration centres (`tools/cymbal_candidate_eval.CENTRE`):
# each band's absolute gain is matched to the shipped kit in ONE 1/3 octave, so
# that is the frequency a shape error has to be measured relative to.
CENTRE_HZ = {"low": 3175.0, "decay": 10079.0, "short": 10079.0}

# A band's ACTIVE RANGE: the 1/3 octaves where its own filter chain is within
# this much of its peak. Outside it a deviation cannot reach the residual --
# the decay band is 65 dB down at 1 kHz.
ACTIVE_DB = 20.0

# The bound a realisation must stay inside, stated before the numbers were
# computed: 3 dB, "the board's tolerance" for band tilt in
# docs/audio-distance-metrics.md (which also measures the machine's own
# unit-to-unit tilt spread at 0.159 dB). It is 9x the tone stage's own
# measurement uncertainty -- step 4's extrapolation control reads 0.34 dB at
# 7.1 kHz -- and an eighth of the +24.0 dB residual climb being closed. The
# bound is discriminating rather than decorative: revision 2's realisation of
# the same two stages FAILS it in all three bands (control CAND2_LEVEL_ONLY).
SHAPE_BOUND_DB = 3.0

# The integer bank must agree with the analytic transfer function of the
# registers it was handed. Quantisation and the bank's floor-shifts set the
# floor here, not the filter.
BANK_TOL_DB = 0.5
# The analog tone model, rebuilt from the artifact's own 2-pole fit, must
# reproduce werner_fig9.tilt_table exactly -- same fit, same evaluation.
TILT_TOL_DB = 0.02
# Step 4 recorded the net of (Ht3 x LEVEL) over 2-20 kHz as -1.1 dB, from
# +16.6 and -17.7 measured separately. This tool recomputes it from the
# artifacts and must land on the same number.
NET_TOL_DB = 0.2
NET_RECORDED_DB = {"low": -2.82, "decay": -0.80, "short": -1.09}


class Refused(Exception):
    """The tool cannot stand behind an answer. Distinct from a failed check."""


# ---------------------------------------------------------------------------
# What the figures say
# ---------------------------------------------------------------------------


def tone_poles(fig9=None) -> dict:
    """{band: (high-pass pole Hz, low-pass pole Hz)} at TONE k = 1.0."""
    data = fig9 if fig9 is not None else w9.from_artifact()[0]
    out = {}
    for band, name in BAND_OF.items():
        v = data[name]
        hz, db = v["curves"][v["k1_index"]]
        fit = w9.fit_bp2(hz, db)
        lo, hi = w9.bp2_poles(fit["f0"], fit["q"])
        out[band] = (float(lo), float(hi))
    return out


def level_corner_hz(path: pathlib.Path | None = None) -> float:
    """The LEVEL buffer's differentiator corner, off W14b Figure 10."""
    p = path or wf.ARTIFACT
    if not p.exists():
        raise Refused(f"{p} is absent; run tools/werner_fig4.py --from-pdf --json")
    blob = json.loads(p.read_text())
    try:
        return float(blob["level_stage"]["one_pole_corner_hz"])
    except (KeyError, TypeError) as exc:
        raise Refused(f"{p} carries no level_stage.one_pole_corner_hz") from exc


def analog_target_db(band, hz, poles, corner_hz, *, level=True):
    """(tone stage x LEVEL stage) for one band, as the circuit builds it.

    One RC high-pass and one RC low-pass (Figure 9), times a single-pole
    differentiator (Figure 10). Absolute gain is irrelevant -- the candidate's
    level rule renormalises each band -- so only the shape is used.
    """
    s = 2j * np.pi * np.asarray(hz, dtype=float)
    w1, w2 = (2 * math.pi * f for f in poles[band])
    h = s / ((s + w1) * (s + w2))
    if level:
        h = h * (s / (s + 2 * math.pi * corner_hz))
    return _db(h)


# ---------------------------------------------------------------------------
# What the bank implements
# ---------------------------------------------------------------------------


def _db(x):
    return 20.0 * np.log10(np.maximum(np.abs(x), 1e-30))


def _z(hz):
    return np.exp(-2j * np.pi * np.asarray(hz, dtype=float) / SR)


def numerator(code, hz):
    z = _z(hz)
    if code == mf.RAW:
        return np.ones_like(z)
    if code == mf.BP:
        return 1 - z * z
    if code == mf.HP:
        return (1 - z) ** 2
    if code == cc.HP3:
        return (1 - z) ** 3
    raise Refused(f"unknown numerator code {code}")


def section_db(a1, a2, code, hz):
    """One bank mode's transfer function, from its registers as written."""
    z = _z(hz)
    return _db(numerator(code, hz) / (1 - a1 * z - a2 * z * z))


def realisation(band, poles, *, defect=None) -> dict:
    """What revision 3 hands the bank for (tone x LEVEL), band by band.

    The low and decay bands get the EMPTY cascade: the tone low-pass pole and
    the LEVEL differentiator are both in their asymptotic regions over the
    band, so they cancel, and dropping both is cheaper and closer than
    realising either. The short band gets the tone low-pass pole exactly, in
    Hh3's 1-pole stage's unused second pole slot, with the LEVEL zero kept.
    """
    spec = dict(cc.TONE_REALISATION[band])
    extra = spec["extra_pole_hz"]
    zeros = 0 if extra is None else 1          # DC zeros this sub-chain keeps
    if defect == "CAND2_LEVEL_ONLY":           # the realisation that shipped as revision 2
        extra, zeros = None, 1
    elif defect == "HP_POLE_NOT_LP" and extra is not None:
        extra = poles[band][0]
    elif defect == "SWAP_BANDS":
        other = {"low": "decay", "decay": "low", "short": "short"}[band]
        extra = None if extra is None else poles[other][1]
    if extra is None:
        a1, a2 = 0, 0
    else:
        a1, a2 = cc.real_pole_regs([extra])
    if defect == "SIGN_A1":
        a1 = -a1
    return {"extra_pole_hz": extra, "dc_zeros": zeros, "a1": a1, "a2": a2,
            "a1_f": a1 / (1 << 24), "a2_f": a2 / (1 << 24)}


def realised_db(band, hz, poles, *, defect=None, real=None):
    r = real if real is not None else realisation(band, poles, defect=defect)
    z = _z(hz)
    h = (1 - z) ** r["dc_zeros"] / (1 - r["a1_f"] * z - r["a2_f"] * z * z)
    out = _db(h)
    if defect == "UNIFORM_6DB":
        out = out + 6.0
    return out


def section_regs(band, poles, *, defect=None):
    """The (a1, a2, numerator, pole frequencies) of the one bank section that
    carries a tone pole, or None for a band whose realisation has no section.

    For the short band that section is Hh3's 1-pole stage, which ends up
    holding TWO real poles: its own third pole and the tone low-pass.
    """
    r = realisation(band, poles, defect=defect)
    if r["extra_pole_hz"] is None:
        return None
    hz = [cc.HH3_P1_HZ, r["extra_pole_hz"]]
    a1, a2 = cc.real_pole_regs(hz)
    if defect == "SIGN_A1":
        a1 = -a1
    return a1, a2, cc.TONE_REALISATION[band]["num"], hz


def band_chain_db(band, hz):
    """The band's OWN filters (band-pass x high-pass), as the candidate writes
    them -- used only to find where the band has energy."""
    z = _z(hz)
    f0 = 3450.0 if band == "low" else 7100.0
    sections = [(f0, 6.0, mf.BP)]
    if band == "low":
        sections.append((cc.HH1_HZ, cc.HH1_Q, mf.HP))
    elif band == "decay":
        sections.append((cc.HH2_HZ, cc.HH2_Q, mf.HP))
    else:
        sections.append((cc.HH3_HZ, cc.HH3_Q, mf.HP))
    out = np.zeros(len(np.atleast_1d(hz)))
    for f, q, code in sections:
        a1, a2 = mf.pole_regs(f, q)
        out = out + section_db(a1 / (1 << 24), a2 / (1 << 24), code, hz)
    if band == "short":
        # Hh3's THIRD pole and its own single zero -- not the register's second
        # zero, which belongs to the LEVEL stage and so to the sub-chain under
        # test. Counting it here would tilt the active range by +6 dB/octave.
        a1, _ = cc.real_pole_regs([cc.HH3_P1_HZ])
        out = out + _db((1 - _z(hz)) / (1 - (a1 / (1 << 24)) * _z(hz)))
    return out


# ---------------------------------------------------------------------------
# The error of the realisation
# ---------------------------------------------------------------------------


def shape_error(band, poles, corner_hz, *, defect=None):
    """The realisation's deviation from the measured cascade, per 1/3 octave.

    Normalised at the band's own calibration centre, because the level rule
    matches absolute energy there: a uniform offset is absorbed by the render
    and a shape error is not. `shape_max_db` is taken over the ACTIVE range
    only, and is the number the bound applies to.
    """
    target = analog_target_db(band, THIRDS, poles, corner_hz,
                              level=(defect != "NO_LEVEL_STAGE"))
    got = realised_db(band, THIRDS, poles, defect=defect)
    chain = band_chain_db(band, THIRDS)
    active = chain >= chain.max() - ACTIVE_DB
    ref = float(np.interp(CENTRE_HZ[band], THIRDS, target))
    dev = (got - float(np.interp(CENTRE_HZ[band], THIRDS, got))) - (target - ref)
    w = (10.0 ** (chain / 10.0)) * active
    offset = float(np.sum(w * dev) / np.sum(w))
    shape = dev - offset
    return {
        "band": band, "centre_hz": CENTRE_HZ[band],
        "active_hz": [float(THIRDS[active][0]), float(THIRDS[active][-1])],
        "n_active": int(active.sum()),
        "target_db": [round(float(v), 2) for v in target - ref],
        "realised_db": [round(float(v), 2) for v in
                        got - float(np.interp(CENTRE_HZ[band], THIRDS, got))],
        "deviation_db": [round(float(v), 2) for v in dev],
        "level_offset_db": round(offset, 2),
        "shape_max_db": round(float(np.max(np.abs(shape[active]))), 2),
        "shape_max_hz": float(THIRDS[active][int(np.argmax(np.abs(shape[active])))]),
    }


def net_tilt_db(band, poles, corner_hz, lo=2000.0, hi=20000.0, *, defect=None):
    """The net tilt of (tone x LEVEL) across lo..hi -- step 4's headline."""
    f = np.array([lo, hi])
    t = analog_target_db(band, f, poles, corner_hz, level=(defect != "NO_LEVEL_STAGE"))
    return float(t[1] - t[0])


def bank_impulse_db(a1, a2, code, hz, n=1 << 14, drive=1 << 14):
    """The INTEGER bank's own magnitude response, measured not derived.

    One mode of the real `ModalFxHP3` is excited with a single sample and the
    state's spectrum is read. This is the check that the registers as packed
    are the filter the arithmetic above claims -- a sign error, a swapped
    a1/a2, a masked-off bit all show here and nowhere else.
    """
    bank = cc.ModalFxHP3(modes=1, nums=1)
    bank.reset()
    coefs = [(a1, a2, 0)]
    y = np.empty(n)
    for i in range(n):
        bank.step([drive if i == 0 else 0], coefs, [code])
        y[i] = bank.y1[0]
    spec = np.fft.rfft(y)
    f = np.fft.rfftfreq(n, 1.0 / SR)
    mag = _db(np.interp(np.asarray(hz, dtype=float), f, np.abs(spec)))
    return mag - _db(np.array([drive], dtype=float))[0]


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


PROPERTIES = ("tone-tilt", "net-cancel", "shape-bound", "bank-exact", "image-bind")
DEFECTS = ("SIGN_A1", "SWAP_BANDS", "NO_LEVEL_STAGE", "CAND2_LEVEL_ONLY",
           "HP_POLE_NOT_LP")
# Asserted the other way round: this one MUST leave every property BLIND. A
# uniform 6 dB gain change is exactly what the candidate's level rule
# renormalises away, so a suite that reported it would be reading a level where
# it claims to read a shape (verification rule 4's BLIND column, stated as a
# requirement rather than as an excuse).
BLIND_BY_CONSTRUCTION = ("UNIFORM_6DB",)


def properties(poles, corner_hz, fig9, *, defect=None) -> dict:
    """Each named property's verdict, as (ok, detail). All five run always."""
    out = {}

    tilt = w9.tilt_table(fig9)
    worst, at = 0.0, ""
    for band, name in BAND_OF.items():
        ours = analog_target_db(band, THIRDS, poles, corner_hz, level=False)
        ours = ours - float(np.interp(1000.0, THIRDS, ours))
        theirs = np.array([v for _, v, _ in tilt[name]])
        if defect == "SWAP_BANDS":
            other = {"low": "decay", "decay": "low", "short": "short"}[band]
            ours = analog_target_db(other, THIRDS, poles, corner_hz, level=False)
            ours = ours - float(np.interp(1000.0, THIRDS, ours))
        d = float(np.max(np.abs(ours - theirs)))
        if d > worst:
            worst, at = d, name
    out["tone-tilt"] = (worst <= TILT_TOL_DB,
                        f"max {worst:.3f} dB vs werner_fig9.tilt_table ({at}), bound {TILT_TOL_DB}")

    worst, at = 0.0, ""
    for band in BANDS:
        d = abs(net_tilt_db(band, poles, corner_hz, defect=defect) - NET_RECORDED_DB[band])
        if d > worst:
            worst, at = d, band
    out["net-cancel"] = (worst <= NET_TOL_DB,
                         f"max {worst:.2f} dB from the recorded net tilt ({at}), bound {NET_TOL_DB}")

    rows = {b: shape_error(b, poles, corner_hz, defect=defect) for b in BANDS}
    bad = {b: r["shape_max_db"] for b, r in rows.items() if r["shape_max_db"] > SHAPE_BOUND_DB}
    out["shape-bound"] = (not bad,
                          "  ".join(f"{b} {r['shape_max_db']:.2f}" for b, r in rows.items())
                          + f"  bound {SHAPE_BOUND_DB}")

    worst, at = 0.0, ""
    probe = THIRDS[(THIRDS >= 1000.0) & (THIRDS <= 16000.0)]
    for band in BANDS:
        regs = section_regs(band, poles, defect=defect)
        if regs is None:
            continue
        a1, a2, code, pole_hz = regs
        # The registers as packed, measured through the INTEGER bank...
        got = bank_impulse_db(a1, a2, code, probe)
        # ...against the cascade of real poles the realisation NAMES, derived
        # from the frequencies rather than from the same registers. A sign
        # error, a swapped pair or a masked bit shows here; a self-comparison
        # against the registers' own transfer function could not see any of them.
        den = np.ones_like(_z(probe))
        for f in pole_hz:
            den = den * (1 - math.exp(-2 * math.pi * f / SR) * _z(probe))
        want = _db(numerator(code, probe) / den)
        d = float(np.max(np.abs((got - got[0]) - (want - want[0]))))
        if d > worst:
            worst, at = d, band
    out["bank-exact"] = (worst <= BANK_TOL_DB,
                         f"max {worst:.3f} dB, integer bank vs the named poles' transfer ({at or 'n/a'}), "
                         f"bound {BANK_TOL_DB}")

    out["image-bind"] = _image_bind(poles, defect=defect)
    return out


def _sign26(v):
    return v - (1 << 26) if v & (1 << 25) else v


def _image_bind(poles, *, defect=None):
    """The register image `candidate_kit` writes IS what this tool describes.

    The verified object has to be the shipped object: without this, every
    number above could be arithmetic about a filter the kit does not contain.
    Two assertions -- no mode carries numerator code 3 (so the shared
    `modal_fixed`/`modal_dp` decode needs no HP3), and Hh3's 1-pole stage's
    coefficient pair is exactly the two real poles the realisation names.
    """
    img = dict(cc.candidate_kit())
    nums = {m: img[dx.A_MODE + m * dx.MODE_STRIDE + 3]
            for m in (cc.M_CYH1, dx.M_CYHI, cc.M_CYH3, cc.M_CYH3B)}
    base = dx.A_MODE + cc.M_CYH3B * dx.MODE_STRIDE
    got = (_sign26(img[base]), _sign26(img[base + 1]))
    regs = section_regs("short", poles, defect=defect)
    if regs is None:                      # a defect that removes the section
        want, hz = cc.real_pole_regs([cc.HH3_P1_HZ]), [cc.HH3_P1_HZ]
    else:
        want, hz = (regs[0], regs[1]), regs[3]
    bad = []
    if cc.HP3 in nums.values():
        bad.append("HP3 is written")
    if got != want:
        bad.append(f"M_CYH3B coefficients {got} != {want}")
    if defect == "CAND2_LEVEL_ONLY" and nums[cc.M_CYH1] != cc.HP3:
        bad.append("Hh1 does not carry the LEVEL zero revision 2 gave it")
    return (not bad, "; ".join(bad) if bad
            else f"no HP3 (codes {sorted(set(nums.values()))}), "
                 f"Hh3 1-pole pair {got} = poles {[round(f, 1) for f in hz]} Hz")


def check(poles, corner_hz, fig9) -> tuple[bool, list[str]]:
    lines = []
    clean = properties(poles, corner_hz, fig9)
    ok = all(v[0] for v in clean.values())
    for name in PROPERTIES:
        good, detail = clean[name]
        lines.append(f"  {'PASS' if good else 'FAIL'}  {name:12s} {detail}")
    if not ok:
        lines.append("the clean run does not pass; the controls below mean nothing (rule 5, condition 1)")
        return False, lines
    lines.append("")
    lines.append(f"  {'defect':20s} " + " ".join(f"{p:>11s}" for p in PROPERTIES))
    caught, blind_ok = [], []
    for d in DEFECTS + BLIND_BY_CONSTRUCTION:
        try:
            got = properties(poles, corner_hz, fig9, defect=d)
            cells = ["MOVED" if got[p][0] is False else "BLIND" for p in PROPERTIES]
        except (Refused, ValueError) as exc:               # a control may be unrealisable
            lines.append(f"  {d:20s} REFUSED: {exc}")
            caught.append(False)
            continue
        if d in BLIND_BY_CONSTRUCTION:
            blind_ok.append("MOVED" not in cells)
            lines.append(f"  {d:20s} " + " ".join(f"{c:>11s}" for c in cells)
                         + "   (must be BLIND everywhere)")
        else:
            caught.append("MOVED" in cells)
            lines.append(f"  {d:20s} " + " ".join(f"{c:>11s}" for c in cells))
    lines.append("")
    lines.append(f"  {sum(caught)}/{len(DEFECTS)} controls turned at least one property red; "
                 f"{sum(blind_ok)}/{len(BLIND_BY_CONSTRUCTION)} blind-by-construction controls stayed blind")
    return all(caught) and all(blind_ok), lines


# ---------------------------------------------------------------------------


def report(poles, corner_hz) -> list[str]:
    head = "        " + " ".join(f"{f / 1000:>6.2f}k" for f in THIRDS)
    lines = [f"LEVEL differentiator corner {corner_hz:.1f} Hz (W14b Fig. 10)", ""]
    for band in BANDS:
        r = realisation(band, poles)
        e = shape_error(band, poles, corner_hz)
        lo, hi = poles[band]
        lines.append(f"{band}  tone poles {lo:7.1f} / {hi:7.1f} Hz   net (tone x LEVEL) "
                     f"2-20 kHz {net_tilt_db(band, poles, corner_hz):+.2f} dB")
        lines.append(f"       realisation: "
                     + ("no section -- the two stages cancel over the band"
                        if r["extra_pole_hz"] is None else
                        f"one real pole at {r['extra_pole_hz']:.1f} Hz in Hh3's 1-pole stage, "
                        f"a1 {r['a1_f']:.6f} a2 {r['a2_f']:.6f}"))
        lines.append(f"       active {e['active_hz'][0]:.0f}-{e['active_hz'][1]:.0f} Hz "
                     f"({e['n_active']} thirds)   shape error {e['shape_max_db']:.2f} dB "
                     f"at {e['shape_max_hz'] / 1000:.1f} kHz   bound {SHAPE_BOUND_DB}")
        lines.append(head)
        for label, key in (("target", "target_db"), ("ours", "realised_db"), ("dev", "deviation_db")):
            lines.append(f"  {label:5s} " + " ".join(f"{v:+7.2f}" for v in e[key]))
        lines.append("")
    return lines


def record(poles, corner_hz, fig9) -> dict:
    ok, lines = check(poles, corner_hz, fig9)
    return {
        "tool": "tools/cymbal_tone_realisation.py",
        "sources": {"tone": str(w9.ARTIFACT.relative_to(ROOT)),
                    "level": str(wf.ARTIFACT.relative_to(ROOT))},
        "level_corner_hz": corner_hz,
        "tone_poles_hz": {b: list(v) for b, v in poles.items()},
        "thirds_hz": [float(f) for f in THIRDS],
        "active_db": ACTIVE_DB, "shape_bound_db": SHAPE_BOUND_DB,
        "bands": {b: {**shape_error(b, poles, corner_hz),
                      "realisation": realisation(b, poles),
                      "net_tilt_2k_20k_db": round(net_tilt_db(b, poles, corner_hz), 2),
                      "revision2_shape_max_db":
                          shape_error(b, poles, corner_hz, defect="CAND2_LEVEL_ONLY")["shape_max_db"]}
              for b in BANDS},
        "gate": {"pass": bool(ok), "lines": lines},
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--json", type=pathlib.Path)
    a = ap.parse_args(argv)
    if not (a.report or a.check or a.json):
        a.report = True
    try:
        fig9 = w9.from_artifact()[0]
        poles, corner = tone_poles(fig9), level_corner_hz()
    except (w9.Refused, Refused) as exc:
        print(f"REFUSED: {exc}")
        return 2
    rc = 0
    if a.report:
        print("\n".join(report(poles, corner)))
    if a.check:
        ok, lines = check(poles, corner, fig9)
        print("\n".join(lines))
        print("gate:", "PASS" if ok else "FAIL")
        rc = 0 if ok else 1
    if a.json:
        a.json.parent.mkdir(parents=True, exist_ok=True)
        rec = record(poles, corner, fig9)
        a.json.write_text(json.dumps(rec, indent=1, default=float) + "\n")
        print(f"wrote {a.json}")
        rc = rc or (0 if rec["gate"]["pass"] else 1)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
