#!/usr/bin/env python3
"""Measure the TR-808 toms' pitch drop across the 808 From Mars clean set.

    python model/tom_drop_measure.py                      # the table
    python model/tom_drop_measure.py --json out.json      # + machine-readable

Reads `refaudio/cache/808-from-mars/.../Clean/Digital/{A,B,C}/*.wav`, fetched by
`tools/refaudio_local.py` (or `tools/refaudio_fetch.py`) and size-checked against
`refaudio/index/808-from-mars.tsv`.

The pack's own notes define the folders: **A = No Accent, B = Accent, C = More
Accent**, and 01..11 are eleven TUNING positions. Both halves of that are
checked here rather than trusted -- the settled f0 must move across 01..11 and
must not move across A/B/C.

THE CONTROL THAT CARRIES THIS RESULT. Accent is, to first order, a gain on the
trigger pulse. A linear system's frequency trajectory does not change when the
input is scaled, and neither does any artefact of a linear estimator on it. So
if the normalised trajectory is the SAME at A, B and C, whatever the probe
reports is artefact; if it grows with accent, the mechanism is a real
amplitude-dependent nonlinearity. The accent-A rows are therefore this
measurement's empirical artefact floor -- not a synthetic one.
"""
from __future__ import annotations
import argparse, hashlib, json, pathlib, subprocess, sys
import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import tom_pitch_probe as P

REPO = HERE.parent
CACHE = REPO / "refaudio" / "cache" / "808-from-mars"
STEM = "808 From Mars/WAV/01. Individual Hits"
VOICES = [("LT", "03. Low Tom", "Tom Low"), ("MT", "04. Mid Tom", "Tom Mid"),
          ("HT", "05. Hi Tom", "Tom Hi")]
CONGAS = [("LC", "06. Low Conga", "Conga Low"), ("MC", "07. Mid Conga", "Conga Mid"),
          ("HC", "08. Hi Conga", "Conga Hi")]
ACCENTS = ["A", "B", "C"]

# What the contract shipped WHEN THIS MEASUREMENT WAS TAKEN
# (spec/NUMERIC-CONTRACT.md 15.7.1, drums_fx TOM_DROP_RATIO/MS/STEPS at the
# commit recorded in the provenance block). Frozen on purpose: this module's
# output is the record of the disagreement it found, so the baseline it was
# compared against must not move underneath it. 15.7.1 now ships 1.060 (#154);
# `model/tom_drop_docs.py` is what checks the CURRENT constant against prose.
SHIPPED_RATIO, SHIPPED_MS, SHIPPED_K = 1.7, 60.0, 3.0


def files_for(folder: str, stem: str, accent: str) -> list[pathlib.Path]:
    d = CACHE / STEM / folder / "Clean" / "Digital" / accent
    return sorted(d.glob("*.wav"))


def run_voice(folder: str, stem: str, accent: str) -> list[dict]:
    rows = []
    for p in files_for(folder, stem, accent):
        sr, x = P.read_wav(str(p))
        r = P.measure(x, sr, label=p.name)
        r["path"] = str(p.relative_to(REPO))
        r["member"] = f"{STEM}/{folder}/Clean/Digital/{accent}/{p.name}"
        rows.append(r)
    return rows


def agg(rows: list[dict], key: str) -> dict:
    v = [r[key] for r in rows if r.get("verdict") in ("OK", "NO-DROP-ABOVE-FLOOR")
         and r.get(key) is not None and r[key] == r[key]]
    if not v:
        return {"n": 0}
    a = np.array(v, dtype=float)
    return {"n": len(a), "median": float(np.median(a)), "mean": float(a.mean()),
            "sd": float(a.std(ddof=1)) if len(a) > 1 else 0.0,
            "min": float(a.min()), "max": float(a.max()),
            "p16": float(np.percentile(a, 16)), "p84": float(np.percentile(a, 84))}


def shipped_reading(f0: float, q: float) -> dict:
    """What the SAME estimator reads on a synthetic carrying the contract's own
    drop at this voice's measured f0. This is the like-for-like comparison: no
    inversion, no model, the same instrument on both sides."""
    y = P.synth_tom(f0, q, P.tau_from_q(f0, q), ratio=SHIPPED_RATIO,
                    drop_ms=SHIPPED_MS, shape="exp", k_exp=SHIPPED_K, seed=17, trim=False)
    i0 = P.onset_index(y, 0.01)
    return P.measure(y[max(0, i0 - 4):], P.SR_EXPECTED, label="shipped")


def control_leading_silence(n_files: int = 6) -> list[dict]:
    """Prepending digital silence cannot change what the machine did.

    A sibling agent found a filter-edge artefact worth up to 6 dB in the drum
    path: `sosfiltfilt`'s odd extension on a segment that starts at full
    amplitude manufactures an edge, and the Fischer WAVs start at the strike
    while our renders lead with 10 ms of silence. A pitch tracker over the first
    60-100 ms sits exactly in that region.

    This probe's default path does NOT filter: the trajectory is interpolated
    zero crossings of the raw signal, and band-limiting is off precisely because
    the gate showed filtfilt pre-ringing costs 40 % of the excess. The two FFTs
    it does run (the settled cross-check and the neighbour diagnostic) both sit
    in the SETTLED window, not at the onset. So the artefact should not reach
    this measurement -- which is a prediction, and this control tests it.
    """
    out = []
    for vid, folder, stem in VOICES:
        for acc in ("A", "C"):
            fs = files_for(folder, stem, acc)
            for p in fs[:max(1, n_files // (2 * len(VOICES)))]:
                sr, x = P.read_wav(str(p))
                row = {"file": p.name, "voice": vid, "accent": acc}
                for lead_ms in (0.0, 0.5, 1.0, 10.0):
                    y = np.concatenate([np.zeros(int(lead_ms * 1e-3 * sr)), x])
                    r = P.measure(y, sr, label=p.name)
                    ok = r["verdict"] == "OK"
                    row[f"lead_{lead_ms}"] = (r.get("ratio_at_onset") if ok else None,
                                              r.get("f_settled_hz"), r["verdict"])
                out.append(row)
    return out


def control_distinct_events(folder: str, stem: str) -> dict:
    """These are eleven TUNINGS at three accents, not re-pressings of one event.

    The coordinator's warning is that "808" sets on some hosts cross-correlate
    at 1.000 against each other -- the same recorded events re-issued -- so a
    spread computed across them is not a spread. This measures the largest
    normalised cross-correlation between any two files in the set at the best
    lag. Anything near 1.000 would mean the spread here is fictitious.
    """
    sigs = []
    for acc in ACCENTS:
        for p in files_for(folder, stem, acc):
            sr, x = P.read_wav(str(p))
            n = int(0.20 * sr)
            v = x[:n]
            if len(v) < n:
                v = np.concatenate([v, np.zeros(n - len(v))])
            v = v - v.mean()
            sigs.append((f"{acc}/{p.name[-6:-4]}", v / (np.linalg.norm(v) + 1e-30)))
    worst = (0.0, "", "")
    for i in range(len(sigs)):
        for j in range(i + 1, len(sigs)):
            c = float(np.max(np.abs(np.correlate(sigs[i][1], sigs[j][1], mode="same"))))
            if c > worst[0]:
                worst = (c, sigs[i][0], sigs[j][0])
    return {"n_files": len(sigs), "max_xcorr": worst[0], "pair": [worst[1], worst[2]]}


def provenance() -> dict:
    def git(*a):
        try:
            return subprocess.run(["git", "-C", str(REPO), *a], capture_output=True,
                                  text=True, timeout=30).stdout.strip()
        except Exception:
            return "?"
    cat = json.loads((REPO / "refaudio" / "catalog.json").read_text())
    pack = [p for p in cat["packs"] if p["archive"] == "808-from-mars.zip"][0]
    return {"commit": git("rev-parse", "HEAD"),
            "dirty": bool(git("status", "--porcelain")),
            "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
            "archive": pack["archive"], "archive_sha256": pack["archive_sha256"],
            "archive_bytes": pack["archive_bytes"],
            "index": "refaudio/index/808-from-mars.tsv",
            "index_sha256": hashlib.sha256(
                (REPO / "refaudio" / "index" / "808-from-mars.tsv").read_bytes()).hexdigest()}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    ap.add_argument("--congas", action="store_true", help="also measure the congas (same circuit)")
    ap.add_argument("--controls", action="store_true",
                    help="leading-silence and distinct-event controls on the RECORDINGS")
    a = ap.parse_args(argv)

    prov = provenance()
    print(f"commit {prov['commit'][:12]}{'  DIRTY' if prov['dirty'] else '  clean'}"
          f"   archive {prov['archive']} sha256 {prov['archive_sha256'][:16]}...")
    print()

    out = {"provenance": prov, "shipped": {"ratio": SHIPPED_RATIO, "drop_ms": SHIPPED_MS,
                                           "k_exp": SHIPPED_K}, "voices": {}}
    groups = VOICES + (CONGAS if a.congas else [])
    for vid, folder, stem in groups:
        out["voices"][vid] = {}
        accents = ACCENTS if vid in ("LT", "MT", "HT") else ["A", "B"]
        print(f"=== {vid}  ({folder})")
        print("  acc  n  ref   settled Hz (11 tunings)   floor Hz   x1st period        "
              "x at onset (fit)        tau ms        shape   neigh dB")
        for acc in accents:
            rows = run_voice(folder, stem, acc)
            if not rows:
                print(f"   {acc}   -- no files in the cache")
                continue
            ref = [r for r in rows if r["verdict"] == "REFUSED"]
            good = [r for r in rows if r["verdict"] in ("OK", "NO-DROP-ABOVE-FLOOR")]
            fs, r1 = agg(good, "f_settled_hz"), agg(good, "ratio_first_period")
            ro = agg([r for r in good if r["verdict"] == "OK"], "ratio_at_onset")
            r2 = agg(good, "ratio_second_period")
            rn = agg([r for r in good if r["verdict"] == "OK"], "ratio_at_onset_nofirst")
            tau = agg([r for r in good if r["verdict"] == "OK"], "fit_tau_ms")
            fl, nb = agg(good, "floor_hz"), agg(good, "neighbour_db")
            shapes = [r.get("fit_shape") for r in good if r["verdict"] == "OK"]
            sh = f"{shapes.count('exponential')}e/{shapes.count('linear')}l"
            nodrop = sum(1 for r in good if r["verdict"] == "NO-DROP-ABOVE-FLOOR")
            print(f"   {acc} {len(good):3d} {len(ref):3d}  "
                  f"{fs.get('median',float('nan')):7.2f} [{fs.get('min',float('nan')):6.2f},"
                  f"{fs.get('max',float('nan')):7.2f}]  {fl.get('median',float('nan')):7.3f}  "
                  f"{r1.get('median',float('nan')):.4f} +-{r1.get('sd',float('nan')):.4f}  "
                  + (f"{ro['median']:.4f} +-{ro['sd']:.4f} [{ro['min']:.4f},{ro['max']:.4f}]  "
                     if ro['n'] else f"{'-- no drop above floor --':<34}")
                  + (f"{tau['median']:6.1f} +-{tau['sd']:5.1f}  " if tau['n'] else f"{'--':>14}  ")
                  + f"{sh:>7}  {nb.get('median',float('nan')):6.1f}"
                  + (f"   nofirst x{rn['median']:.4f}+-{rn['sd']:.4f} (n={rn['n']})" if rn['n'] else "")
                  + (f"   ({nodrop} of {len(good)} below floor)" if nodrop else ""))
            out["voices"][vid][acc] = {
                "n_ok": len(good), "n_refused": len(ref), "n_no_drop": nodrop,
                "f_settled": fs, "floor_hz": fl, "ratio_first_period": r1,
                "ratio_second_period": r2, "ratio_at_onset_nofirst": rn,
                "ratio_at_onset": ro, "tau_ms": tau, "neighbour_db": nb,
                "shapes": {"exponential": shapes.count("exponential"),
                           "linear": shapes.count("linear")},
                "refusals": [{"file": r["label"], "why": r["why"]} for r in ref],
                "rows": [{k: r.get(k) for k in
                          ("label", "member", "verdict", "f_settled_hz", "floor_hz",
                           "ratio_first_period", "ratio_second_period",
                           "ratio_at_onset", "ratio_at_onset_nofirst", "fit2_tau_ms",
                           "fit_tau_ms",
                           "fit_shape", "excess_sigma", "neighbour_db", "peak_dbfs",
                           "floor_dbfs", "why")} for r in rows]}
        # structural controls
        st = out["voices"][vid]
        if all(acc in st and st[acc]["f_settled"]["n"] for acc in accents):
            spread_tuning = max(st[acc]["f_settled"]["max"] - st[acc]["f_settled"]["min"]
                                for acc in accents)
            med = [st[acc]["f_settled"]["median"] for acc in accents]
            spread_accent = max(med) - min(med)
            print(f"   control: settled f0 spans {spread_tuning:.2f} Hz across the 11 TUNINGS "
                  f"and {spread_accent:.2f} Hz across the ACCENTS "
                  f"-> 01..11 is tuning, A/B/C is not")
            st["control_tuning_span_hz"] = spread_tuning
            st["control_accent_span_hz"] = spread_accent
        print()

    print("=== the same estimator on the drop the contract ships "
          f"(x{SHIPPED_RATIO}, {SHIPPED_MS:.0f} ms, exp(-{SHIPPED_K:.0f}t/T))")
    out["shipped_reading"] = {}
    for vid, q in (("LT", 25.0), ("MT", 24.0), ("HT", 25.0)):
        st = out["voices"].get(vid, {})
        f0 = st.get("A", {}).get("f_settled", {}).get("median")
        if not f0:
            continue
        r = shipped_reading(f0, q)
        out["shipped_reading"][vid] = {k: r.get(k) for k in
                                       ("f_settled_hz", "ratio_first_period",
                                        "ratio_at_onset", "fit_tau_ms", "fit_shape")}
        print(f"   {vid} at {f0:.2f} Hz: first period x{r['ratio_first_period']:.4f}, "
              f"onset x{r['ratio_at_onset']:.4f}, tau {r['fit_tau_ms']:.1f} ms")

    print()
    print("=== THE ANSWER: onset f0 / settled f0, pooled over the three toms and 11 tunings")
    print("    accent   n   median      mean +- sd        range          tau ms (median, range)"
          "    contract x1.7 excess / measured")
    out["pooled"] = {}
    for acc in ACCENTS:
        vals, taus = [], []
        for vid in ("LT", "MT", "HT"):
            for r in out["voices"].get(vid, {}).get(acc, {}).get("rows", []):
                if r["verdict"] == "OK" and r.get("ratio_at_onset"):
                    vals.append(r["ratio_at_onset"]); taus.append(r["fit_tau_ms"])
        if not vals:
            print(f"      {acc}     -- every row refused or below floor --")
            out["pooled"][acc] = {"n": 0}
            continue
        v, t = np.array(vals), np.array(taus)
        gap = (SHIPPED_RATIO - 1.0) / (np.median(v) - 1.0)
        out["pooled"][acc] = {"n": len(v), "median": float(np.median(v)), "mean": float(v.mean()),
                              "sd": float(v.std(ddof=1)), "min": float(v.min()),
                              "max": float(v.max()), "tau_median_ms": float(np.median(t)),
                              "tau_min_ms": float(t.min()), "tau_max_ms": float(t.max()),
                              "contract_excess_over_measured": float(gap)}
        print(f"      {acc}   {len(v):3d}   x{np.median(v):.4f}   x{v.mean():.4f} +-{v.std(ddof=1):.4f}"
              f"   [{v.min():.4f}, {v.max():.4f}]   {np.median(t):5.1f}  [{t.min():4.1f}, {t.max():5.1f}]"
              f"        {gap:6.2f} x too large")
    print(f"    The contract clamps the accent scale at 1.0, so it applies the FULL x{SHIPPED_RATIO} "
          f"at every accent >= 1.0,")
    print("    including the unaccented hit that is most of what an 808 plays.")

    if a.controls:
        print()
        print("=== CONTROL: prepending digital silence must not change the answer")
        cs = control_leading_silence()
        out["control_leading_silence"] = cs
        worst = 0.0
        for row in cs:
            base = row["lead_0.0"][0]
            line = f"   {row['voice']}/{row['accent']} {row['file'][-10:]:>10}  "
            for lead in (0.0, 0.5, 1.0, 10.0):
                v, fset, verd = row[f"lead_{lead}"]
                line += f"{lead:4.1f} ms: " + (f"x{v:.4f}  " if v else f"{verd[:8]}  ")
                if v and base:
                    worst = max(worst, abs(v - base))
            print(line)
        print(f"   worst change in the measured onset ratio from 10 ms of leading silence: "
              f"{worst:.6f}")
        out["control_leading_silence_worst_delta"] = worst
        print()
        print("=== CONTROL: the 33 files per voice are distinct events, not re-pressings")
        for vid, folder, stem in VOICES:
            d = control_distinct_events(folder, stem)
            out.setdefault("control_distinct", {})[vid] = d
            print(f"   {vid}: {d['n_files']} files, largest |xcorr| between any pair "
                  f"{d['max_xcorr']:.4f}  ({d['pair'][0]} vs {d['pair'][1]})")

    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(out, indent=1))
        print(f"\nwrote {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
