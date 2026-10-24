#!/usr/bin/env python3
"""How much of #161's pedestal reached #148's published numbers, and what the
repair moves.

`test_discrimination.condition()` high-passed with `sosfiltfilt`, which pads
6 samples against a 20 Hz pole needing ~2,400. On a unit impulse at index 0 it
answers with a full-scale negative pedestal (second sample -0.994; the first
30 ms integrating to -342 at 44.1 k, -373 at 48 k, against a causal filter's
+0.02). `condition()` is inside the classifier's own feature pipeline, so that
pedestal was in every 320-column vector, every interpretable feature and
therefore every knob-equivalent in #148.

IT APPLIES TO BOTH SIDES, SO MUCH OF IT MAY CANCEL -- AND CANCELLATION CANNOT
BE ASSUMED. The two sides are not shaped alike at the onset: `_render_raw`
pre-trims our clip and `read_wav` does not trim the machine's (#160's F2), and
a pedestal whose size depends on the first sample lands differently on a
trimmed and an untrimmed record. This module measures how much cancels instead
of arguing about it.

It renders each clip ONCE and conditions it BOTH ways, so the only thing that
differs between the two columns is the boundary condition:

    legacy   condition(..., legacy=True)   the shipped acausal path, exactly
    fixed    condition(...)                onset-cut, then causal from rest

Two sections.

  1. THE ARTEFACT, BOTH SIDES. For every voice and setting, the shift the
     boundary puts on the machine's vector (d_real) and on ours (d_ours), in
     the features' own units, and the part that does NOT cancel in the paired
     difference -- ||d_real - d_ours|| -- read against the size of the
     difference actually being reported, ||real - ours||.

  2. #148's KNOB-EQUIVALENTS, RE-DERIVED. `distance_curve` and
     `ours_distance` exactly as `discrimination_run.py` calls them, run twice
     off the same renders, with the delta per voice.

The baseline column is legacy AT THIS COMMIT, not #148's printed figure: HEAD
carries #154's tom pitch-drop correction, which moves the renders themselves.
Comparing fixed-at-HEAD against legacy-at-HEAD is the only comparison that
isolates the conditioning change; #148's printed figures are shown alongside
for reference and the gap between them and legacy-at-HEAD is #154's, not this
module's.

    .venv/bin/python model/condition_boundary.py --refs /tmp/tr808-ref \\
        --json docs/condition-boundary-results.json

Six of the sixteen sounds (CH CP CB RS CL MA) have no knob, so the corpus
holds one recording of each, there is no held-out setting and no yardstick:
they REFUSE here exactly as they do in the study.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "audition"))

import test_discrimination as td                                    # noqa: E402
from test_discrimination import ALL_REF, Clip                       # noqa: E402

MODES = ("legacy", "fixed")

# #148's printed knob-equivalents, for the reference column only. nan is the
# report's ">=10": further than a 0..10 dial can travel.
PUBLISHED_148 = {"SD": 3.4, "BD": 2.5, "OH": 6.9, "HT": 7.3, "LT": float("nan"),
                 "CY": 8.1, "MT": 5.2, "LC": 3.5, "MC": 5.4, "HC": float("nan")}


# ---------------------------------------------------------------------------
def provenance(refdir: str) -> dict:
    def git(*a):
        try:
            return subprocess.check_output(["git", "-C", os.path.dirname(HERE), *a],
                                           text=True).strip()
        except Exception:
            return "?"
    # deterministic: sorted relative paths, each name then its bytes, so the
    # digest does not depend on the filesystem's walk order
    h = hashlib.sha256()
    wavs = []
    for root, _, fs in os.walk(refdir):
        for f in fs:
            if f.lower().endswith(".wav"):
                wavs.append(os.path.relpath(os.path.join(root, f), refdir))
    for rel in sorted(wavs):
        h.update(rel.encode())
        h.update(open(os.path.join(refdir, rel), "rb").read())
    m = hashlib.sha256()
    for f in ("drums_fx.py", "modal_fixed.py", "test_discrimination.py"):
        p = os.path.join(HERE, f)
        if os.path.exists(p):
            m.update(open(p, "rb").read())
    def rgit(*a):
        try:
            return subprocess.check_output(["git", "-C", refdir, *a], text=True).strip()
        except Exception:
            return "?"
    return dict(commit=git("rev-parse", "HEAD"),
                described=git("describe", "--always", "--dirty"),
                dirty=bool(git("status", "--porcelain")),
                model_sha256=m.hexdigest()[:16],
                corpus_commit=rgit("rev-parse", "--short", "HEAD"),
                corpus_n_wav=len(wavs),
                corpus_sha256=h.hexdigest()[:16])


# ---------------------------------------------------------------------------
def _both_ways(x, sr) -> dict:
    """One signal, conditioned both ways. Rendering is the expensive half and
    it happens once, so the two columns cannot drift apart for any reason but
    the boundary condition."""
    out = {}
    for m in MODES:
        seg = td.condition(x, sr, True, legacy=(m == "legacy"))
        out[m] = td.features(seg, sr, True, False)[0]
    out["_prep"] = td.preparation_state(x, sr)
    return out


def _real_one(job):
    voice, knobs, path = job
    x, sr = td.read_wav(path)
    return (voice, knobs, "real"), _both_ways(x, sr)


def _ours_one(job):
    voice, knobs, laws = job
    x, sr = td.render(voice, knobs, laws, "ours")
    return (voice, knobs, "ours"), _both_ways(x, sr)


def build(refs, laws, jobs):
    work_r = [(c.voice, c.knobs, c.path) for c in refs]
    work_o = [(c.voice, c.knobs, laws) for c in refs]
    cache = {}
    with ProcessPoolExecutor(max_workers=jobs) as ex:
        for k, v in ex.map(_real_one, work_r, chunksize=4):
            cache[k] = v
        done = 0
        for k, v in ex.map(_ours_one, work_o, chunksize=4):
            cache[k] = v
            done += 1
            if done % 25 == 0:
                print(f"    rendered {done}/{len(work_o)}", flush=True)
    return cache


def _ckey(k):
    return f"{k[0]}|{'/'.join(str(x) for x in k[1])}|{k[2]}"


def save_cache(cache, path):
    flat = {}
    for k, v in cache.items():
        for m in MODES:
            flat[f"{_ckey(k)}|{m}"] = v[m]
        flat[f"{_ckey(k)}|_lead"] = np.asarray([v["_prep"]["lead_ms"]])
    np.savez_compressed(path, **flat)


def load_cache(path):
    z = np.load(path)
    out = {}
    for name in z.files:
        voice, knobs, side, field = name.split("|")
        k = (voice, tuple(float(x) for x in knobs.split("/") if x), side)
        d = out.setdefault(k, {})
        if field == "_lead":
            d["_prep"] = dict(lead_ms=float(z[name][0]))
        else:
            d[field] = z[name]
    return out


# ---------------------------------------------------------------------------
def artefact_table(refs, cache) -> dict:
    """Section 1. Per voice, in the features' own units (log10 power, so 1.0
    is 10 dB), medians over that voice's settings:

        real   ||legacy - fixed|| on the machine's vector
        ours   ||legacy - fixed|| on ours
        resid  ||d_real - d_ours||, the part that does NOT cancel
        paired ||real - fixed - ours - fixed||, the difference being reported
        frac   resid / paired

    `frac` is the answer to "does it cancel". A small `frac` means the
    published comparison was reading a difference the artefact barely touched;
    a large one means it was reading the artefact."""
    out = {}
    for v in sorted({c.voice for c in refs}):
        rows = []
        for c in [c for c in refs if c.voice == v]:
            r, o = cache[(v, c.knobs, "real")], cache[(v, c.knobs, "ours")]
            d_real = r["legacy"] - r["fixed"]
            d_ours = o["legacy"] - o["fixed"]
            resid = d_real - d_ours
            paired = r["fixed"] - o["fixed"]
            paired_l = r["legacy"] - o["legacy"]
            rows.append((np.linalg.norm(d_real), np.linalg.norm(d_ours),
                         np.linalg.norm(resid), np.linalg.norm(paired),
                         np.linalg.norm(paired_l),
                         np.abs(d_real).max(), np.abs(d_ours).max()))
        a = np.asarray(rows)
        med = np.median(a, axis=0)
        out[v] = dict(n=len(rows), real=med[0], ours=med[1], resid=med[2],
                      paired_fixed=med[3], paired_legacy=med[4],
                      frac=float(med[2] / med[3]) if med[3] > 0 else float("nan"),
                      real_max_col_dB=10.0 * med[5], ours_max_col_dB=10.0 * med[6],
                      lead_ms_real=float(np.median([cache[(v, c.knobs, "real")]["_prep"]["lead_ms"]
                                                    for c in refs if c.voice == v])),
                      lead_ms_ours=float(np.median([cache[(v, c.knobs, "ours")]["_prep"]["lead_ms"]
                                                    for c in refs if c.voice == v])))
    return out


def knob_equivalents(refs, cache, mode) -> dict:
    """Section 2. `distance_curve` / `ours_distance` / `knob_equivalent_distance`
    exactly as discrimination_run.py calls them, on one mode's vectors."""
    flat = {k: v[mode] for k, v in cache.items()}
    mfn = lambda cl: np.asarray([flat[(c.voice, c.knobs, c.side)] for c in cl])
    out = {}
    for v in sorted({c.voice for c in refs}):
        if ALL_REF[v][2] == 0:
            out[v] = dict(refused="no knob -> no held-out setting and no yardstick")
            continue
        dc = td.distance_curve(None, flat, v, mfn)
        od = td.ours_distance(refs, flat, v, "ours", mfn)
        ke = td.knob_equivalent_distance(od, dc)
        sc = td.voice_scale(refs, v, mfn)
        dcf = td.distance_curve(None, flat, v, mfn, scale=sc)
        odf = td.ours_distance(refs, flat, v, "ours", mfn, scale=sc)
        # How steep is the yardstick where we land? A knob-equivalent is read
        # off an interpolation over four points, so a small distance change
        # can move it a long way -- and if it can, the published one-decimal
        # figure is carrying more uncertainty than it shows. This reports the
        # knob-equivalent at +/-1 % of the measured distance.
        lo = td.knob_equivalent_distance(od * 0.99, dc)
        hi = td.knob_equivalent_distance(od * 1.01, dc)
        out[v] = dict(knob_equivalent=ke, ours_distance=od,
                      knob_equivalent_frozen=td.knob_equivalent_distance(odf, dcf),
                      ours_distance_frozen=odf,
                      ke_at_dist_minus_1pct=lo, ke_at_dist_plus_1pct=hi,
                      curve={f"{k[0]}|{k[1]}": val for k, val in dc.items()},
                      knob10=max(dc.values()) if dc else float("nan"))
    return out


# ---------------------------------------------------------------------------
def _ke(x):
    if x is None:
        return "  refused"
    return ">=10" if (isinstance(x, float) and np.isnan(x)) else f"{x:.1f}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sys.path.insert(0, os.path.join(HERE, "..", "tools"))
    import run_case
    ap.add_argument("--refs", default=str(run_case.configured_refs()))
    ap.add_argument("--json", default="docs/condition-boundary-results.json")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 4) - 1))
    ap.add_argument("--cache", default="",
                    help="npz of the conditioned feature vectors. Written if absent, "
                         "read if present -- so re-reading the same renders costs "
                         "nothing and the two columns cannot drift between runs.")
    a = ap.parse_args(argv)

    prov = provenance(a.refs)
    print(f"commit {prov['described']} ({prov['commit'][:12]}) "
          f"dirty={prov['dirty']}  model sha {prov['model_sha256']}  "
          f"corpus {prov['corpus_commit']} {prov['corpus_n_wav']} WAVs "
          f"sha {prov['corpus_sha256']}")

    refs = td.ref_clips(a.refs, include_unmodelled=True)
    import discrimination_run as dr
    sh = dr.split_hash(refs)
    print(f"{len(refs)} reference clips over {len({c.voice for c in refs})} sounds; "
          f"split hash {sh}")
    if a.cache and os.path.exists(a.cache):
        cache = load_cache(a.cache)
        print(f"read {len(cache)} cached feature vectors from {a.cache} (nothing re-rendered)")
    else:
        laws = td.fit_laws(a.refs, all_sounds=True)
        print(f"rendering {len(refs)} clips on {a.jobs} workers, "
              f"conditioning each both ways...")
        cache = build(refs, laws, a.jobs)
        if a.cache:
            save_cache(cache, a.cache)
            print(f"wrote {a.cache}")

    print("\n== 1. the artefact, both sides ==")
    print("   norms over the 320 columns in log10 power (1.0 = 10 dB), median over")
    print("   each sound's settings. resid = the part that does NOT cancel.")
    print(f"  {'':3s} {'n':>3s} {'lead ms':>16s} {'|d|real':>8s} {'|d|ours':>8s} "
          f"{'resid':>8s} {'paired':>8s} {'resid/paired':>13s}")
    art = artefact_table(refs, cache)
    for v, d in sorted(art.items()):
        print(f"  {v:3s} {d['n']:3d} {d['lead_ms_real']:7.2f}/{d['lead_ms_ours']:<8.2f} "
              f"{d['real']:8.2f} {d['ours']:8.2f} {d['resid']:8.2f} "
              f"{d['paired_fixed']:8.2f} {d['frac']:12.1%}")

    print("\n== 2. #148's knob-equivalents, re-derived ==")
    ke = {m: knob_equivalents(refs, cache, m) for m in MODES}
    print(f"  {'':3s} {'#148':>7s} {'legacy@HEAD':>12s} {'fixed@HEAD':>11s} {'delta':>7s}"
          f"   {'dist legacy':>11s} {'dist fixed':>10s} {'knob-10':>8s}"
          f"   {'ke at dist -1%/+1%':>20s}")
    for v in sorted(ke["fixed"]):
        L, F = ke["legacy"][v], ke["fixed"][v]
        if "refused" in F:
            print(f"  {v:3s} {'REFUSED':>7s}  {F['refused']}")
            continue
        p = PUBLISHED_148.get(v)
        dl = ""
        if not (np.isnan(L["knob_equivalent"]) or np.isnan(F["knob_equivalent"])):
            dl = f"{F['knob_equivalent'] - L['knob_equivalent']:+.1f}"
        elif np.isnan(L["knob_equivalent"]) != np.isnan(F["knob_equivalent"]):
            dl = "crosses"
        print(f"  {v:3s} {(_ke(p) if p is not None else '-'):>7s} "
              f"{_ke(L['knob_equivalent']):>12s} {_ke(F['knob_equivalent']):>11s} "
              f"{dl:>7s}   {L['ours_distance']:11.2f} {F['ours_distance']:10.2f} "
              f"{F['knob10']:8.2f}   "
              f"{_ke(F['ke_at_dist_minus_1pct']):>9s}/{_ke(F['ke_at_dist_plus_1pct']):<10s}")
    print("\n  #148 vs legacy@HEAD is #154's render change, not this one.")
    print("  legacy@HEAD vs fixed@HEAD is the conditioning repair, alone.")

    res = dict(provenance=prov, refs=a.refs, split_hash=sh, n_clips=len(refs),
               published_148=PUBLISHED_148, artefact=art, knob_equivalents=ke,
               command=" ".join(["model/condition_boundary.py", "--refs", a.refs,
                                 "--json", a.json]))
    os.makedirs(os.path.dirname(os.path.abspath(a.json)), exist_ok=True)
    with open(a.json, "w") as f:
        json.dump(res, f, indent=1,
                  default=lambda o: (list(o) if isinstance(o, tuple)
                                     else float(o) if isinstance(o, np.floating)
                                     else int(o) if isinstance(o, np.integer) else str(o)))
    print(f"\nwrote {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
