#!/usr/bin/env python3
"""Derive the toms' pitch-drop law from the measured corpus, with held-out splits.

    python model/tom_drop_fit.py                 # the constants and every hold-out
    python model/tom_drop_fit.py --json out.json

INPUT is `docs/tom-pitch-drop-results.json` -- the per-file table PR #110
measured from 99 clean-digital tom files and 66 conga files of a real TR-808,
with `model/tom_pitch_probe.py` behind its own gate. Nothing here re-measures
audio and nothing here is fitted to a score. This module does one thing: turn
that table into the three constants `model/drums_fx.py` needs, and then try to
break them on settings it did not use.

THE LAW

    excess(accent, f0) = max(0, K * (accent - A0[position])) * exp(G * u)
    u = f0 / f0_nominal(position) - 1          (where the TUNING pot sits)
    f0 = f0_settled * (1 + excess)             at the onset, relaxing over
                                               TOM_DROP_MS as shipped

Three terms because the measurement found three separate things, not one:

  * K, A0  -- the drop is amplitude-dependent, and it is NOT proportional to
    accent: it has a THRESHOLD. The germanium diodes do not conduct at all
    below a drive, which is why the shipped `min(max(accent,0),1)` clamp is
    backwards -- it applies the full drop to the unaccented hit, where the
    machine does x1.06.
  * G -- the drop depends on the TUNING pot and the shipped sequence ignores
    it. LT at More Accent runs x1.169 at 82 Hz to x1.325 at 101 Hz. G is fitted
    per POSITION, not pooled: the toms give 3.58 +- 0.14 and the congas 7.46
    +- 0.09, which is not a disagreement to average away.
  * A0 differs between the TOM and the CONGA position of the same circuit.
    That is not an f0 effect: HT and LC are BOTH nominally 185 Hz, on the same
    bridged-T with a capacitor switched, and unaccented their drops differ by
    11x (0.061 against 0.0055). The switch, not the frequency.

WHY u IS NORMALISED TO EACH POSITION'S OWN CENTRE

The predictor is where the pot sits, not the frequency. Across LT / MT / HT the
excess at the pot centre is the same to +-10 % while f0 spans 2x, and the
HT-against-LC pair above rules f0 out directly. So u is measured from each
position's centre: the machine's own (the median settled f0 of that voice's
files) on the measuring side, and the model's own (TOM_PRESET's chart f0) on
the shipping side. Centre to centre. Using the chart f0 on BOTH sides would
import one machine's unit variation (its MT centre sits 4.5 % above the chart)
into a constant meant to describe the circuit.

THE ACCENT MAP, WHICH IS AN ASSUMPTION AND IS LABELLED ONE

The pack ships A = No Accent, B = Accent, C = More Accent -- ordered, and
not calibrated to any scalar (`docs/tom-pitch-drop-measurement.md`, "What this
does not settle"). The model's accent is continuous. The map used here comes
from the two ends both sides already define:

    A -> 1.0   the model's plain hit ('x' in `drums_fx.pattern`), the 808's
               step with the accent bit off
    C -> 2.0   the model's legal maximum accent, the 808's ACCENT pot at
               maximum (the common trigger's 14 V end, reference 1.1)
    B -> 1.4   the model's accented hit ('X' in `drums_fx.pattern`)

`--accent-map` re-runs the whole fit under a different one, and the sensitivity
is reported, because a reader who disagrees with this map should be able to see
what it costs rather than be told it does not matter.
"""
from __future__ import annotations
import argparse, json, math, pathlib, re, sys
import numpy as np
_REFUSAL_TOOLS = str(pathlib.Path(__file__).resolve().parents[1] / "tools")
if _REFUSAL_TOOLS not in sys.path:
    sys.path.append(_REFUSAL_TOOLS)
import refusal  # noqa: E402
import manifest as mf  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
RESULTS = REPO / "docs" / "tom-pitch-drop-results.json"

TOMS = ("LT", "MT", "HT")
CONGAS = ("LC", "MC", "HC")
ACCENT_MAP = {"A": 1.0, "B": 1.4, "C": 2.0}
USABLE = ("OK",)          # NO-DROP-ABOVE-FLOOR claims nothing in either direction
MIN_CELL = 4              # rows needed before a (voice, accent) cell gets an intercept


class Refused(refusal.Refused, Exception):
    """A precondition is not met; nothing was attempted."""


def load_rows(path: pathlib.Path | None = None) -> tuple[list[dict], dict]:
    """Every usable per-file row, with the tuning index parsed out of the name.
    `path` defaults to the committed results, read at call time."""
    path = RESULTS if path is None else path
    if not path.exists():
        raise Refused(f"{path} is missing: this fit has no measurement to fit to")
    doc = json.loads(path.read_text(encoding="utf-8"))
    mf.require_keys(doc, "voices", f"the pitch-drop measurement at {path}", refused=Refused)
    rows = []
    for v in TOMS + CONGAS:
        for acc in "ABC":
            cell = doc["voices"].get(v, {}).get(acc)
            if not cell:
                continue
            for r in cell["rows"]:
                if r.get("verdict") not in USABLE or r.get("ratio_at_onset") is None:
                    continue
                m = re.search(r"(\d\d)\.wav$", r["label"])
                if not m:
                    raise Refused(f"cannot read a tuning index out of {r['label']!r}")
                rows.append({"voice": v, "accent": acc, "tuning": int(m.group(1)),
                             "f": float(r["f_settled_hz"]),
                             "excess": float(r["ratio_at_onset"]) - 1.0,
                             "tau_ms": r.get("fit_tau_ms"),
                             "label": r["label"]})
    if not rows:
        raise Refused("no usable rows in the measurement")
    return rows, doc


def centres(rows: list[dict]) -> dict:
    """Each voice's own pot centre: the median settled f0 over its files.

    Over the measured files only -- a REFUSED row still tells us where the pot
    was, but this is read off the same population the excesses come from so the
    two cannot disagree about which files they describe."""
    out = {}
    for v in TOMS + CONGAS:
        f = [r["f"] for r in rows if r["voice"] == v]
        if f:
            out[v] = float(np.median(f))
    return out


def with_u(rows: list[dict]) -> list[dict]:
    c = centres(rows)
    return [dict(r, u=r["f"] / c[r["voice"]] - 1.0) for r in rows if r["voice"] in c]


def fit_g(rows: list[dict]) -> dict:
    """One tuning slope G shared by every cell, each cell keeping its own level.

    log(excess_i) = c[voice_i, accent_i] + G * u_i, least squares. One slope
    across nine cells rather than nine slopes, because the claim being tested is
    that the pot moves the drop the same way everywhere; cells with fewer than
    MIN_CELL rows are dropped rather than given an intercept they cannot
    support (LT at No Accent survives in only 4 files, all at the top of the
    pot, so its own slope is not a measurement of anything)."""
    cells = sorted({(r["voice"], r["accent"]) for r in rows})
    keep = [k for k in cells if sum(1 for r in rows if (r["voice"], r["accent"]) == k) >= MIN_CELL]
    use = [r for r in rows if (r["voice"], r["accent"]) in keep]
    if len(keep) < 2 or len(use) < 3 * len(keep):
        raise Refused(f"only {len(keep)} cells with >= {MIN_CELL} rows; G is not identifiable")
    idx = {k: i for i, k in enumerate(keep)}
    A = np.zeros((len(use), len(keep) + 1))
    y = np.zeros(len(use))
    for i, r in enumerate(use):
        A[i, idx[(r["voice"], r["accent"])]] = 1.0
        A[i, -1] = r["u"]
        y[i] = math.log(max(r["excess"], 1e-6))
    beta, *_ = np.linalg.lstsq(A, y, rcond=None)
    resid = y - A @ beta
    dof = max(1, len(use) - len(beta))
    s2 = float(resid @ resid) / dof
    cov = s2 * np.linalg.pinv(A.T @ A)
    return {"G": float(beta[-1]), "G_se": float(math.sqrt(max(cov[-1, -1], 0.0))),
            "cells": {f"{v} {a}": float(math.exp(beta[idx[(v, a)]])) for v, a in keep},
            "n": len(use), "rms_log": float(math.sqrt(s2))}


def centre_excess(g: dict, voices: tuple) -> dict:
    """Pot-centre excess per accent level, pooled across the voices given."""
    out = {}
    for acc in "ABC":
        vals = [v for k, v in g["cells"].items() if k.endswith(" " + acc) and k[:2] in voices]
        if vals:
            out[acc] = {"mean": float(np.mean(vals)), "n_voices": len(vals),
                        "per_voice": vals}
    return out


def fit_accent(centre: dict, amap: dict) -> dict:
    """K and A0 from excess = K * (accent - A0), least squares over the levels."""
    xs = np.array([amap[a] for a in sorted(centre)])
    ys = np.array([centre[a]["mean"] for a in sorted(centre)])
    if len(xs) < 2:
        raise Refused("fewer than two accent levels; K and A0 are not identifiable")
    K, c = np.polyfit(xs, ys, 1)
    if K <= 0:
        raise Refused(f"the fitted accent slope is {K:.4g}: the drop would shrink with accent")
    A0 = -c / K
    pred = K * (xs - A0)
    return {"K": float(K), "A0": float(A0), "levels": sorted(centre),
            "fitted": {a: float(p) for a, p in zip(sorted(centre), pred)},
            "measured": {a: float(v) for a, v in zip(sorted(centre), ys)},
            "max_abs_residual": float(np.max(np.abs(ys - pred)))}


def fit_conga_threshold(centre_c: dict, K: float, amap: dict, check: dict = None) -> dict:
    """The conga position's threshold, with K held at the toms'.

    The pack records congas at A and B only -- two levels. Fitting both K and A0
    to two points leaves nothing to check them against, so K is taken from the
    toms (three levels, with a held-out level) and only the threshold is fitted,
    to the ACCENT level. The NO-ACCENT level is then a prediction, not an input,
    and it is reported as one."""
    if "B" not in centre_c:
        raise Refused("no conga Accent level; the threshold is not identifiable")
    A0 = amap["B"] - centre_c["B"]["mean"] / K
    meas = {a: float(v["mean"]) for a, v in centre_c.items()}
    for a, v in (check or {}).items():
        meas.setdefault(a, float(v["mean"]))
    out = {"A0": float(A0), "fitted_from": "B", "predicted": {}, "measured": meas}
    for a in meas:
        out["predicted"][a] = float(max(0.0, K * (amap[a] - A0)))
    return out


def predict(accent: float, u: float, K: float, A0: float, G: float) -> float:
    return max(0.0, K * (accent - A0)) * math.exp(G * u)


# ------------------------------------------------------------- hold-outs ----

def _fit_all(rows: list[dict], amap: dict) -> dict:
    """The tom law from tom rows only. The conga position has its own G and its
    own threshold; pooling the two would average a real difference away."""
    g = fit_g([r for r in rows if r["voice"] in TOMS])
    tom = fit_accent(centre_excess(g, TOMS), amap)
    return {"G": g["G"], "K": tom["K"], "A0": tom["A0"], "_g": g, "_tom": tom}


def _score(rows: list[dict], p: dict, amap: dict) -> dict:
    """Error of the law against rows it was not fitted to, in excess units."""
    err, rel = [], []
    for r in rows:
        e = predict(amap[r["accent"]], r["u"], p["K"], p["A0"], p["G"])
        err.append(e - r["excess"])
        if r["excess"] > 1e-3:
            rel.append((e - r["excess"]) / r["excess"])
    return {"n": len(err), "bias": float(np.mean(err)), "rms": float(np.sqrt(np.mean(np.square(err)))),
            "max_abs": float(np.max(np.abs(err))),
            "median_rel": float(np.median(rel)) if rel else None}


def holdouts(rows: list[dict], amap: dict) -> dict:
    out = {}
    # 1. an entire ACCENT LEVEL. Fit on No Accent and More Accent; Accent is unseen.
    tr = [r for r in rows if r["accent"] in ("A", "C")]
    te = [r for r in rows if r["accent"] == "B" and r["voice"] in TOMS]
    p = _fit_all(tr, amap)
    out["accent_B_unseen"] = {"train": "A + C", "test": "B, toms", "law": {k: p[k] for k in "GKA0" if k in p},
                              **_score(te, p, amap)}
    out["accent_B_unseen"]["law"] = {"K": p["K"], "A0": p["A0"], "G": p["G"]}
    # 2. HALF THE TUNING POSITIONS. Fit on odd, predict even.
    tr = [r for r in rows if r["tuning"] % 2 == 1]
    te = [r for r in rows if r["tuning"] % 2 == 0 and r["voice"] in TOMS]
    p = _fit_all(tr, amap)
    out["tuning_even_unseen"] = {"train": "tuning 01,03,05,07,09,11", "test": "tuning 02,04,06,08,10, toms",
                                 "law": {"K": p["K"], "A0": p["A0"], "G": p["G"]}, **_score(te, p, amap)}
    # 3. AN ENTIRE VOICE. Fit on LT and HT, predict MT.
    tr = [r for r in rows if r["voice"] != "MT"]
    te = [r for r in rows if r["voice"] == "MT"]
    p = _fit_all(tr, amap)
    out["voice_MT_unseen"] = {"train": "LT + HT (+ congas for G)", "test": "MT",
                              "law": {"K": p["K"], "A0": p["A0"], "G": p["G"]}, **_score(te, p, amap)}
    # 4. THE TOP AND BOTTOM OF THE POT. Fit on the middle seven, predict the ends.
    tr = [r for r in rows if 3 <= r["tuning"] <= 9]
    te = [r for r in rows if r["tuning"] in (1, 2, 10, 11) and r["voice"] in TOMS]
    p = _fit_all(tr, amap)
    out["pot_ends_unseen"] = {"train": "tuning 03..09", "test": "tuning 01,02,10,11, toms",
                              "law": {"K": p["K"], "A0": p["A0"], "G": p["G"]}, **_score(te, p, amap)}
    return out


def run(amap: dict = None) -> dict:
    amap = amap or ACCENT_MAP
    raw, doc = load_rows()
    rows = with_u(raw)
    toms = [r for r in rows if r["voice"] in TOMS]
    congas = [r for r in rows if r["voice"] in CONGAS]
    g_toms = fit_g(toms)
    g_congas = fit_g(congas)
    g_congas_b = fit_g([r for r in congas if r["accent"] == "B"])
    tom = fit_accent(centre_excess(g_toms, TOMS), amap)
    conga = fit_conga_threshold(centre_excess(g_congas_b, CONGAS), tom["K"], amap,
                                check=centre_excess(g_congas, CONGAS))
    return {
        "source": str(RESULTS.relative_to(REPO)),
        "source_provenance": doc["provenance"],
        "n_rows_used": len(rows), "n_toms": len(toms), "n_congas": len(congas),
        "accent_map": amap,
        "pot_centres_hz": centres(raw),
        "G": {"tom": g_toms["G"], "tom_se": g_toms["G_se"], "tom_rms_log": g_toms["rms_log"],
              "conga": g_congas_b["G"], "conga_se": g_congas_b["G_se"],
              "conga_rms_log": g_congas_b["rms_log"],
              "conga_all_levels": g_congas["G"],
              "note": "fitted per position: the conga's pot has about twice the "
                      "leverage on the drop that the tom's has, and the No Accent "
                      "conga rows sit at the measurement floor so the conga slope "
                      "is taken from the Accent level"},
        "tom": tom, "conga": conga,
        "cells_at_pot_centre": dict(g_toms["cells"], **g_congas["cells"]),
        "holdouts": holdouts(rows, amap),
        "shipped_for_comparison": {"TOM_DROP_RATIO": 1.7, "excess": 0.7,
                                   "clamp": "min(max(accent,0),1)"},
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", default=None)
    ap.add_argument("--accent-map", default=None,
                    help="A,B,C accent scalars, e.g. 1.0,1.5,2.0")
    a = ap.parse_args(argv)
    amap = dict(ACCENT_MAP)
    if a.accent_map:
        v = [float(x) for x in a.accent_map.split(",")]
        if len(v) != 3:
            print("--accent-map needs three numbers", file=sys.stderr)
            return 2
        amap = dict(zip("ABC", v))
    try:
        r = run(amap)
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    print(f"corpus: {r['n_rows_used']} usable rows ({r['n_toms']} tom, {r['n_congas']} conga)")
    print(f"accent map: {amap}")
    print(f"pot centres (Hz): " + ", ".join(f"{k} {v:.2f}" for k, v in r["pot_centres_hz"].items()))
    print(f"\nG (tuning slope, per unit f0/f0_centre - 1), fitted per position:")
    print(f"   tom   {r['G']['tom']:6.3f} +- {r['G']['tom_se']:.3f}  "
          f"(rms of log residual {r['G']['tom_rms_log']:.4f})")
    print(f"   conga {r['G']['conga']:6.3f} +- {r['G']['conga_se']:.3f}  "
          f"(rms {r['G']['conga_rms_log']:.4f}; all levels pooled would say "
          f"{r['G']['conga_all_levels']:.3f})")
    print("\nexcess at the pot centre, per cell:")
    for k in sorted(r["cells_at_pot_centre"]):
        print(f"   {k}: {r['cells_at_pot_centre'][k]:.4f}")
    t = r["tom"]
    print(f"\nTOM position: K = {t['K']:.4f} per unit accent, threshold A0 = {t['A0']:.4f}")
    for a_ in t["levels"]:
        print(f"   {a_}: measured {t['measured'][a_]:.4f}  fitted {t['fitted'][a_]:.4f}")
    c = r["conga"]
    print(f"\nCONGA position: A0 = {c['A0']:.4f} (K held at the toms', fitted to {c['fitted_from']})")
    for a_ in sorted(c["measured"]):
        print(f"   {a_}: measured {c['measured'][a_]:.4f}  law {c['predicted'][a_]:.4f}"
              f"{'   <- PREDICTION, not an input' if a_ != c['fitted_from'] else ''}")
    print("\nHELD OUT (the law never saw these rows):")
    for k, h in r["holdouts"].items():
        print(f"   {k}: train={h['train']}  test={h['test']}  n={h['n']}")
        print(f"      K={h['law']['K']:.4f} A0={h['law']['A0']:.4f} G={h['law']['G']:.3f}"
              f"   bias {h['bias']:+.4f}  rms {h['rms']:.4f}  worst {h['max_abs']:.4f}"
              + (f"  median rel {100*h['median_rel']:+.1f} %" if h["median_rel"] is not None else ""))
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(r, indent=1), encoding="utf-8")
        print(f"\nwrote {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
