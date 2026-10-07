#!/usr/bin/env python3
"""Calibrate the perceptual gate's comparison bar from the licensed MARS 808
library (#379, docs/scorecard/gate-379/README.md section 6 option b).

WHAT THIS CAN AND CANNOT CLAIM (lineage first, numbers second).
`refaudio/catalog.json` says what the pack is: "the 808 recorded through the
API 1608 ... Apogee Symphony", Clean/Digital and Clean/Tape chains, accent
levels and knob grids, group-normalised per voice. That is ONE machine
(singular) recorded in one session. The Fischer take (`sounds-tr808-fischer`,
`docs/drum-verification.md` section 1) is another recording. NEITHER source
documents a serial number or the other's knob positions, and "multiple
libraries" is not "multiple units" (the legacy MARS edition is the same
vendor and machine). So:

  * "as close as another real UNIT" is REFUSED as a claim. This module never
    emits it.
  * What it measures is typed: `cross-recording` -- the distance from the
    Fischer target to MARS Clean takes of the same voice, with the knob
    setting UNMATCHED (the MARS settings are matched to nothing; the nearest
    by whole-hit `spec` stands in, as the existing bar does for neighbours).
    That distance contains unit-to-unit spread, the recording chain, the
    mastering and the setting residual, inseparably. Its use as a bar is a
    decision (the operator's ruling, 2026-10-02), not something this data
    proves is "unit variation".
  * The one separable permitted difference is the CHAIN: a Digital take and
    its Tape twin (same name, same hit by the vendor's naming; whether it is
    literally one strike is NOT documented). `chain_floor` reports it.

FROZEN BEFORE ANY DISTANCE WAS COMPUTED:

  corpus   Clean/Digital and Clean/Tape only (Color is processed; "Combo"
           files are CH+OH together and are not a CH or an OH).
  groups   key = path with the chain removed (a Digital take and its Tape
           twin share a key, so the twin cannot leak across groups); group =
           sha256(key) mod 3: 0 development, 1 calibration, 2 untouched
           validation. Adjacent knob settings are NOT independent of each
           other and can still straddle groups; that leak is stated, not
           removed. A sound with fewer than 3 keys cannot hold anything out
           and is UNVALIDATED.
  matched  a group's matched set = its k nearest samples to the target by
           `spec`, k chosen on DEVELOPMENT only (below).
  bar      per feature, the max over the CALIBRATION matched set of the
           distance, plus the apparatus floor, with the gate's own perceptual
           floors (attack, pitch, impulse). Never below the floors, never fit.
  k        the smallest k in (1, 3, 5) for which a bar built from the
           development group is passed by the calibration group's nearest
           sample on at least COVERAGE of the sounds that have both. If none
           reaches it, there is NO calibrated bar (REFUSED), not a looser k.
  QUALIFIED only if all of: Q1 the stub is red on 16/16 and silence is
           REFUSED; Q2 >= SEED_CATCH of the seeded defects FAIL; Q3 the
           shipped cymbal FAILs; Q4 the Fischer target through the candidate
           path PASSes 16/16 and the best untouched validation sample passes
           for >= COVERAGE of the sounds that have validation; Q5 calibrating
           from a deliberately corrupted corpus does NOT qualify. A bar that
           fails any of these is not acceptance authority.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import perceptual_gate as pg  # noqa: E402

ROOT = pg.ROOT
CACHE = ROOT / "refaudio" / "cache" / "808-from-mars" / "808 From Mars" / "WAV" / "01. Individual Hits"
ARCHIVE = "808-from-mars.zip"
FOLDER = {"BD": "01. Bass Drum", "SD": "02. Snare Drum", "LT": "03. Low Tom", "MT": "04. Mid Tom",
          "HT": "05. Hi Tom", "LC": "06. Low Conga", "MC": "07. Mid Conga", "HC": "08. Hi Conga",
          "RS": "09. Rim Shot", "CP": "10. Hand Clap", "CL": "11. Claves", "MA": "12. Maracas",
          "CB": "13. Cowbell", "CY": "14. Cymbal", "OH": "15. Open HH", "CH": "16. Closed HH"}
CHAINS = ("Digital", "Tape")
KS = (1, 3, 5)
#: Reported, never selected from: the coverage a larger k WOULD have bought,
#: so the refusal to widen the rule after seeing the data is visible and the
#: dial is swept rather than argued (CLAUDE.md, sweep before arguing).
KS_SWEEP = (1, 3, 5, 8, 12)
COVERAGE = 0.80
SEED_CATCH = 0.90
GROUP_NAMES = ("development", "calibration", "validation")


def group_of(key: str) -> int:
    return int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) % 3


def key_of(rel: str) -> str:
    """The path with the chain removed, so a take and its twin share a key."""
    k = rel.replace("/Digital/", "/*/").replace("/Tape/", "/*/")
    return re.sub(r" Tape\b", "", k)


def corpus(sound: str, root: pathlib.Path = CACHE) -> list:
    """(rel path, chain, key) of the Clean takes of one voice, sorted. 'Combo'
    files are excluded: they are two voices struck together."""
    out = []
    base = root / FOLDER[sound] / "Clean"
    for chain in CHAINS:
        for p in sorted((base / chain).glob("**/*.wav")):
            if "Combo" in p.name:
                continue
            rel = p.relative_to(root).as_posix()
            out.append((rel, chain, key_of(rel)))
    return out


def measure(sound: str, refs: pathlib.Path, root: pathlib.Path = CACHE, corrupt: str | None = None) -> dict:
    """The per-take distance table for one sound. Every take goes through
    `candidate_path`, as a candidate does. A take the gate cannot read is
    recorded with its reason (no-verdict), never dropped silently."""
    rel_t = pg.target_rel(sound)
    T = pg.Target(*pg.load_wav(refs / rel_t), sound, rel_t)
    x, sr = pg.load_wav(refs / rel_t)
    floor = T.distance(*pg.candidate_path(x, sr), "apparatus floor")
    rows, missing = [], []
    for rel, chain, key in corpus(sound, root):
        try:
            mx, msr = pg.load_wav(root / rel)
            if corrupt:
                mx, msr = pg.seed(mx, msr, corrupt, sound, T)
            d = T.distance(*pg.candidate_path(mx, msr), rel)
        except pg.Refused as e:
            missing.append({"rel": rel, "why": str(e)})
            continue
        rows.append({"rel": rel, "chain": chain, "key": key, "group": group_of(key), "d": d})
    return {"sound": sound, "target": rel_t, "floor": floor, "rows": rows, "missing": missing,
            "keys": sorted({r[2] for r in corpus(sound, root)})}


def chain_floor(tab: dict, root: pathlib.Path = CACHE, refs: pathlib.Path | None = None) -> dict:
    """Digital take vs its Tape twin, per feature, over the pairs. The part of
    the cross-recording distance the vendor's own chain choice accounts for."""
    by = {}
    for r in tab["rows"]:
        by.setdefault(r["key"], {})[r["chain"]] = r["rel"]
    pairs = [v for v in by.values() if len(v) == 2]
    if not pairs or refs is None:
        return {"pairs": len(pairs), "why": "no twin pairs" if not pairs else "refs not given"}
    out = {f: [] for f in pg.FEATURES}
    for v in pairs:
        dx, dsr = pg.load_wav(root / v["Digital"])
        T = pg.Target(dx, dsr, tab["sound"], v["Digital"])
        try:
            d = T.distance(*pg.candidate_path(*pg.load_wav(root / v["Tape"])), v["Tape"])
        except pg.Refused:
            continue
        for f in pg.FEATURES:
            if d.get(f) is not None:
                out[f].append(d[f])
    return {"pairs": len(pairs), "median": {f: (float(sorted(v)[len(v) // 2]) if v else None) for f, v in out.items()},
            "max": {f: (max(v) if v else None) for f, v in out.items()}}


def matched(rows: list, group: int, k: int) -> list:
    g = sorted((r for r in rows if r["group"] == group), key=lambda r: r["d"]["spec"])
    return g[:k]


def make_bar(mset: list, floor: dict) -> dict | None:
    """max over the matched set per feature + the apparatus floor, then the
    gate's perceptual floors. None for a feature no matched take can read."""
    if not mset:
        return None
    bar = {}
    for f in pg.FEATURES:
        vs = [r["d"][f] for r in mset if r["d"].get(f) is not None]
        bar[f] = (max(vs) + (floor.get(f) or 0.0)) if vs else None
    if bar.get("attack") is not None:
        bar["attack"] = max(bar["attack"], pg.ATTACK_JND_MS)
    if bar.get("pitch") is not None:
        bar["pitch"] = max(bar["pitch"], pg.PITCH_JND_CENTS)
    if bar.get("impulse") is not None:
        bar["impulse"] = max(bar["impulse"], pg.IMPULSE_FLOOR_DB)
    return bar


def passes(d: dict, bar: dict) -> bool:
    return pg.verdict(d, bar)["verdict"] == "PASS"


def choose_k(tabs: dict) -> dict:
    """Development only: the bar from the development group, tried on the
    calibration group's nearest sample. Returns per-k coverage and the choice.
    `by_k` covers KS_SWEEP; only KS can be chosen."""
    cov = {}
    for k in KS_SWEEP:
        n = ok = 0
        for t in tabs.values():
            dev = make_bar(matched(t["rows"], 0, k), t["floor"])
            cal = matched(t["rows"], 1, 1)
            if dev is None or not cal:
                continue
            n += 1
            ok += passes(cal[0]["d"], dev)
        cov[k] = {"sounds": n, "covered": ok, "coverage": (ok / n if n else None)}
    chosen = next((k for k in KS if cov[k]["coverage"] is not None and cov[k]["coverage"] >= COVERAGE), None)
    return {"by_k": cov, "chosen": chosen, "coverage_required": COVERAGE}


def regroup(tabs: dict) -> dict:
    """Re-derive key and group from each take's path, so the frozen grouping
    rule is applied by one function and a table measured under an earlier key
    rule cannot carry its stale groups (wrong-then-right 1: the first key rule
    paired no BD, SD, tom, conga, CY or OH twin, because the Tape take's name
    carries ' Tape' mid-name)."""
    out = {}
    for s, t in tabs.items():
        rows = [dict(r, key=key_of(r["rel"]), group=group_of(key_of(r["rel"]))) for r in t["rows"]]
        out[s] = dict(t, rows=rows, keys=sorted({r["key"] for r in rows}))
    return out


def twin_report(tabs: dict) -> dict:
    """Digital takes with no Tape twin (and vice versa), per sound. A pairing
    rule that pairs nothing is a leak the grouping was written to prevent."""
    rep = {}
    for s, t in tabs.items():
        ch = {}
        for r in t["rows"]:
            ch.setdefault(r["key"], set()).add(r["chain"])
        rep[s] = {"keys": len(ch), "unpaired": sorted(k for k, c in ch.items() if len(c) < 2)}
    return rep


def calibrate(tabs: dict, force_k: int | None = None) -> dict:
    """Pure function of the per-take tables: the between-recording bars, the
    validity of each, and the reason for every missing one."""
    tabs = regroup(tabs)
    sel = choose_k(tabs)
    k = sel["chosen"]
    diagnostic = force_k is not None
    if diagnostic:
        k = force_k
    out = {"selection": sel, "k": k, "sounds": {}, "twins": twin_report(tabs),
           "diagnostic": ("DIAGNOSTIC: k overridden by hand after the pre-registered rule refused; "
                          "NOT acceptance authority" if diagnostic else None)}
    if k is None:
        out["status"] = "REFUSED: no k reaches the development->calibration coverage requirement"
        return out
    for s, t in tabs.items():
        row = {"keys": len(t["keys"]), "n_takes": len(t["rows"]), "no_verdict": t["missing"],
               "groups": {GROUP_NAMES[g]: len({r["key"] for r in t["rows"] if r["group"] == g}) for g in range(3)}}
        cal = matched(t["rows"], 1, k)
        bar = make_bar(cal, t["floor"])
        if bar is None:
            row["status"] = "NO-BAR: calibration group has no readable take"
            out["sounds"][s] = row
            continue
        row["bar"] = bar
        row["bar_from"] = [r["rel"] for r in cal]
        val = matched(t["rows"], 2, k)
        if len(t["keys"]) < 3 or not val:
            row["status"] = "UNVALIDATED: no untouched take to test the bar on"
        else:
            res = [passes(r["d"], bar) for r in val]
            row["validation"] = {"matched": [r["rel"] for r in val], "pass": res,
                                 "best_passes": passes(val[0]["d"], bar),
                                 "worst_ratio_best": pg.verdict(val[0]["d"], bar)["worst_ratio"]}
            row["status"] = "VALIDATED" if row["validation"]["best_passes"] else "VALIDATION-FAILED: best untouched take fails the bar"
        out["sounds"][s] = row
    out["status"] = "DIAGNOSTIC-CALIBRATION" if diagnostic else "CALIBRATED"
    return out


def old_bar_context(tabs: dict, refs: pathlib.Path, ours: dict | None) -> dict:
    """DESCRIPTIVE, nothing selected from it: where the MARS takes and OUR sound
    sit against the EXISTING (same-unit / WEAK) bar. The MARS side reports its
    most favourable take (all groups, so it is an optimistic bound), as
    crosscheck does for the Boutique 808."""
    out = {}
    for s, t in tabs.items():
        T = pg.Target(*pg.load_wav(refs / t["target"]), s, t["target"])
        b = pg.bar_for(s, refs, T)
        vs = [(pg.verdict(r["d"], b["bar"]), r["rel"]) for r in t["rows"]]
        best = min(vs, key=lambda v: v[0]["worst_ratio"])
        row = {"old_bar": b["kind"], "n_takes": len(vs), "mars_takes_passing_old_bar": sum(v[0]["verdict"] == "PASS" for v in vs),
               "mars_best": {"rel": best[1], "worst_ratio": best[0]["worst_ratio"],
                             "worst_feature": best[0]["worst_feature"], "failing": best[0]["failing"]}}
        if ours and s in ours:
            v = pg.verdict(T.distance(*ours[s], f"ours {s}"), b["bar"])
            row["ours"] = {"verdict": v["verdict"], "worst_ratio": v["worst_ratio"], "worst_feature": v["worst_feature"]}
        out[s] = row
    return out


def load_bars(cal: dict) -> dict:
    """sound -> bar, only where a bar exists AND was not refused."""
    return {s: r["bar"] for s, r in cal["sounds"].items() if "bar" in r}


# ---------------------------------------------------------------------------
# qualification: does the calibrated bar still separate the controls?
# ---------------------------------------------------------------------------
def qualify(cal: dict, refs: pathlib.Path, ours: dict | None = None) -> dict:
    """Run the named controls through the ACTUAL gate with the calibrated bars.
    `ours` maps sound -> (signal, sr), used for the shipped-cymbal control."""
    bars = load_bars(cal)
    q = {"sounds_with_bar": sorted(bars), "sounds_without_bar": sorted(set(pg.SOUNDS16) - set(bars)),
         "start_red": {}, "clean": {}, "seeded": {}, "known_bad": {}}
    n_seed = n_caught = 0
    missed = []
    for s in pg.SOUNDS16:
        if s not in bars:
            continue
        rel = pg.target_rel(s)
        x, sr = pg.load_wav(refs / rel)
        T = pg.Target(x, sr, s, rel)
        b = bars[s]
        q["start_red"][s] = pg.verdict(T.distance(*pg.stub_candidate(s), "stub"), b)["verdict"]
        try:
            T.distance(pg.np.zeros(pg.SR), pg.SR, "silence")
            q["start_red"][s + "-silence"] = "NOT REFUSED"
        except pg.Refused:
            q["start_red"][s + "-silence"] = "REFUSED"
        q["clean"][s] = pg.verdict(T.distance(*pg.candidate_path(x, sr), "self"), b)["verdict"]
        for dname in pg.DEFECTS:
            if dname == "slide" and s not in pg.PITCH_SOUNDS:
                continue
            dx_, dsr = pg.seed(x, sr, dname, s, T)
            v = pg.verdict(T.distance(*pg.candidate_path(dx_, dsr), dname), b)
            n_seed += 1
            if v["verdict"] == "FAIL":
                n_caught += 1
            else:
                missed.append(f"{s}:{dname}:{v['verdict']}")
        q["seeded"][s] = None
    q["seeded_summary"] = {"n": n_seed, "caught": n_caught, "missed": missed,
                           "rate": (n_caught / n_seed if n_seed else None)}
    if ours and "CY" in bars and "CY" in ours:
        T = pg.Target(*pg.load_wav(refs / pg.target_rel("CY")), "CY", pg.target_rel("CY"))
        q["known_bad"]["shipped-cymbal"] = pg.verdict(T.distance(*ours["CY"], "ours CY"), bars["CY"])
    else:
        q["known_bad"]["shipped-cymbal"] = {"verdict": "REFUSED", "why": "no CY bar or no render supplied"}
    val = [r for r in cal["sounds"].values() if "validation" in r]
    if ours:
        rk = {}
        for s, y in ours.items():
            if s not in bars:
                rk[s] = {"verdict": "NO-BAR"}
                continue
            T = pg.Target(*pg.load_wav(refs / pg.target_rel(s)), s, pg.target_rel(s))
            try:
                v = pg.verdict(T.distance(*y, f"ours {s}"), bars[s])
                rk[s] = {"verdict": v["verdict"], "worst_ratio": v["worst_ratio"],
                         "worst_feature": v["worst_feature"], "failing": v["failing"],
                         "ratios": {f: x.get("ratio") for f, x in v["features"].items()}}
            except pg.Refused as e:
                rk[s] = {"verdict": "REFUSED", "why": str(e)}
        q["ours"] = rk
    q["validation"] = {"sounds": len(val), "best_passes": sum(r["validation"]["best_passes"] for r in val)}
    return q


def decide(q: dict, injected_qualified: bool | None) -> dict:
    """The five named gates, each with its observed value. REFUSED if a control
    could not be run: an unrun control is not a pass."""
    have = len(q["sounds_with_bar"])
    sr = q["start_red"]
    sil = [v for k, v in sr.items() if k.endswith("-silence")]
    red = [v for k, v in sr.items() if not k.endswith("-silence")]
    g = {}
    g["Q1 start red"] = (have > 0 and all(v == "FAIL" for v in red) and all(v == "REFUSED" for v in sil),
                         f"stub FAIL {sum(v == 'FAIL' for v in red)}/{len(red)}, silence REFUSED {sum(v == 'REFUSED' for v in sil)}/{len(sil)}")
    rate = q["seeded_summary"]["rate"]
    g["Q2 seeded defects"] = (rate is not None and rate >= SEED_CATCH,
                              f"{q['seeded_summary']['caught']}/{q['seeded_summary']['n']} FAIL (need {SEED_CATCH:.0%})")
    kb = q["known_bad"].get("shipped-cymbal", {}).get("verdict")
    g["Q3 shipped cymbal"] = (kb == "FAIL" if kb != "REFUSED" else None, f"shipped cymbal {kb}")
    clean_ok = [v for v in q["clean"].values()]
    v = q["validation"]
    vcov = (v["best_passes"] / v["sounds"]) if v["sounds"] else None
    g["Q4 stays green"] = (bool(clean_ok) and all(c == "PASS" for c in clean_ok) and vcov is not None and vcov >= COVERAGE,
                           f"self PASS {sum(c == 'PASS' for c in clean_ok)}/{len(clean_ok)}; untouched best take passes "
                           f"{v['best_passes']}/{v['sounds']}")
    g["Q5 corrupted corpus"] = ((injected_qualified is False) if injected_qualified is not None else None,
                                f"corrupted-corpus calibration qualified={injected_qualified}")
    unrun = [k for k, (ok, _) in g.items() if ok is None]
    allok = all(ok for ok, _ in g.values() if ok is not None) and not unrun
    status = "REFUSED" if unrun else ("QUALIFIED" if allok else "NOT QUALIFIED")
    return {"status": status, "gates": {k: {"ok": ok, "observed": o} for k, (ok, o) in g.items()},
            "unrun": unrun}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("measure", help="per-take distance tables (heavy: ~1,000 takes)")
    m.add_argument("--refs", type=pathlib.Path, required=True)
    m.add_argument("--sounds", nargs="*", default=list(pg.SOUNDS16))
    m.add_argument("--corrupt", default=None, choices=[None, *pg.DEFECTS],
                   help="INJECTED DEFECT control: seed every MARS take before measuring")
    m.add_argument("--out", type=pathlib.Path, required=True)
    c = sub.add_parser("calibrate", help="bars + validity from the tables, then qualify through the gate")
    c.add_argument("--refs", type=pathlib.Path, required=True)
    c.add_argument("--tables", type=pathlib.Path, required=True)
    c.add_argument("--corrupt-tables", type=pathlib.Path, default=None)
    c.add_argument("--ours", type=pathlib.Path, default=None, help="dir of our rendered <SOUND>.wav (rank --wavs)")
    c.add_argument("--force-k", type=int, default=None,
                   help="DIAGNOSTIC: override the pre-registered k; the result is labelled not-authority")
    c.add_argument("--out", type=pathlib.Path, required=True)
    a = ap.parse_args(argv)
    if a.cmd == "measure":
        res = {}
        for s in a.sounds:
            res[s] = measure(s, a.refs, corrupt=a.corrupt)
            print(s, len(res[s]["rows"]), "takes,", len(res[s]["missing"]), "no-verdict", flush=True)
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(pg._r({"corrupt": a.corrupt, "archive": ARCHIVE, "tables": res}), indent=1) + "\n")
        return 0
    tabs = json.loads(a.tables.read_text())["tables"]
    cal = calibrate(tabs, a.force_k)
    ours = {}
    if a.ours:
        for s in pg.SOUNDS16:
            p = a.ours / f"{s}.wav"
            if p.exists():
                ours[s] = pg.load_wav(p)
    inj = None
    if a.corrupt_tables:
        ci = calibrate(json.loads(a.corrupt_tables.read_text())["tables"], a.force_k)
        qi = qualify(ci, a.refs, ours) if ci.get("k") else None
        # A refusal is not a pass: if the corrupted corpus is refused by the same
        # rule that refused the clean one, the control saw nothing (None, REFUSED).
        inj = (decide(qi, False)["status"] == "QUALIFIED") if qi else None
        cal["injected_control"] = {"corrupt": json.loads(a.corrupt_tables.read_text())["corrupt"],
                                   "calibration_status": ci["status"], "qualified": inj}
    q = qualify(cal, a.refs, ours) if cal.get("k") else None
    dec = decide(q, inj) if q else {"status": "REFUSED", "why": cal["status"]}
    if q and cal.get("diagnostic"):
        dec["status"] = f"DIAGNOSTIC (not authority): {dec['status']}"
    cal["old_bar_context"] = old_bar_context(regroup(tabs), a.refs, ours)
    res = {"calibration": cal, "qualification": q, "decision": dec, "provenance": pg.provenance(a)}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(pg._r(res), indent=1) + "\n")
    print(json.dumps(dec, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
