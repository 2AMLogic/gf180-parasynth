#!/usr/bin/env python3
"""ONE compensated parameter on the F1 selected path: the ladder's input scaling,
with the inverse output compensation, against matched-level Surge references.

    tools/probes/f1_level_scaling.py --json docs/scorecard/f1-level/results.json
    tools/probes/f1_level_scaling.py --inject CONSTRUCTOR_ONLY   # must exit 2
    tools/probes/f1_level_scaling.py --inject WORDS_NOT_ENTERING # must exit 2

The rule it applies is `docs/scorecard/f1-level/selection-rule.md`, committed
before this was run. In brief:

  candidate s in {1, 1/2, 1/4}: gain/ogain from `LadderFx.regs` with
  volts_per_unit = 0.13*s, i.e. gain x s and ogain / s. The small-signal level
  is unchanged by construction, so level alone cannot win.

REGISTER PRECONDITION (the known trap). A voice built with a different
`ladder_cfg["volts_per_unit"]` still gets the GLOBAL configuration's gain/ogain
from `VoiceFx.patch_regs`, so a constructor-only sweep can measure identical
audio three times. Every render here records the gain/ogain words at the
ladder's own `process` call and asserts (a) they are the candidate's predicted
words and (b) a non-baseline candidate's words differ from the baseline's.
Either failing is REFUSED (exit 2), not a result.

  --inject CONSTRUCTOR_ONLY    build the candidates the trap way: a voice whose
                               ladder_cfg carries the scaled vpu, registers from
                               the host conversion. Must refuse: words equal.
  --inject WORDS_NOT_ENTERING  predict the candidate's words but hand the ladder
                               the baseline's. Must refuse: the recorded words
                               are not the predicted ones.

Exit: 0 measured, 1 a check failed, 2 refused.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import contextlib
import hashlib
import json
import math
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "probes"))

import audio_measure as am           # noqa: E402
import fixed                         # noqa: E402
import run_case as rc                # noqa: E402
import reference_rigs as rr          # noqa: E402
import voice_fx as vf                # noqa: E402
import f1_selected_path as f1        # noqa: E402
import f1_level_capture as cap       # noqa: E402

CANDIDATES = {"baseline": 1.0, "half": 0.5, "quarter": 0.25}
AMPS = cap.AMPS
PRIMARY_AMP = 0.25
CASES = {"F1A": "cut250", "F1B": "cut1000", "F1C": "cut4000"}
OPEN = "open20k"
RES, DRIVE = 0.0, f1.DRIVE
INJECTS = ("", "CONSTRUCTOR_ONLY", "WORDS_NOT_ENTERING")
NOISE_FMAX = 4000.0
N_HARM = 9

#: The volts_per_unit the global host conversion uses today. LADDER_CFG does
#: not set it, so it is LadderFx's default; asserted, not assumed.
BASE_VPU = 0.13


class Refused(RuntimeError):
    pass


def base_vpu_asserted() -> float:
    if "volts_per_unit" in vf.LADDER_CFG:
        raise Refused(f"LADDER_CFG now sets volts_per_unit={vf.LADDER_CFG['volts_per_unit']}; "
                      f"the baseline assumption no longer holds")
    got = fixed.LadderFx(**vf.LADDER_CFG).vpu
    if got != BASE_VPU:
        raise Refused(f"LadderFx default vpu is {got}, not {BASE_VPU}")
    return got


# ---------------------------------------------------------------------------
# candidate registers, and the trap
# ---------------------------------------------------------------------------
def candidate_regs(s: float, cut_hz: float) -> dict:
    """The host registers for cutoff `cut_hz` with the gain/ogain conversion
    run at volts_per_unit = BASE_VPU * s. Everything else is `host_regs`."""
    r = dict(f1.host_regs(cut_hz, RES, DRIVE))
    _k, gain, ogain = fixed.LadderFx(**{**vf.LADDER_CFG, "volts_per_unit": BASE_VPU * s}).regs(
        RES, DRIVE)
    r["gain"], r["ogain"] = gain, ogain
    return r


def constructor_only_regs(s: float, cut_hz: float) -> tuple:
    """THE TRAP, reproduced: a voice whose ladder_cfg carries the scaled vpu,
    with its registers from the host conversion a note would use. Returns
    (voice, regs). The regs are the global configuration's."""
    prof = f1.m5.engine_configuration("selected")
    prof_voice = vf.VoiceFx(
        oversample_2x=prof["oscillator_oversample_2x"],
        ladder_cfg={**vf.LADDER_CFG, "oversample": prof["ladder_coefficient_oversample"],
                    "volts_per_unit": BASE_VPU * s},
        rate_converted_ladder=prof["filter_rate_converted"],
        preserve_filter_headroom=prof["filter_preserve_headroom"],
        causal_filter=prof["filter_causal"],
        pulse479_filter_candidate=prof["pulse479_filter_candidate"])
    regs = dict(f1.host_regs(cut_hz, RES, DRIVE))
    return prof_voice, regs


def assert_registers(name: str, s: float, entering: set, predicted: tuple,
                     baseline: tuple) -> None:
    """REFUSE unless exactly the predicted words entered the ladder and, for a
    non-baseline candidate, they differ from the baseline's."""
    if entering != {predicted}:
        raise Refused(f"{name}: gain/ogain entering the ladder {sorted(entering)} are not the "
                      f"candidate's predicted words {predicted}")
    if s != 1.0 and predicted == baseline:
        raise Refused(f"{name}: gain/ogain {predicted} equal the baseline's -- this candidate "
                      f"would measure the baseline's audio under another name")


# ---------------------------------------------------------------------------
# instrumentation: counts only; verified not to change a word
# ---------------------------------------------------------------------------
@contextlib.contextmanager
def instrument(voice):
    """Record the gain/ogain words at the inner ladder's `process` call, count
    `fixed.sat` events by width and tanh-domain clamps. Restores everything."""
    inner = voice.ladder._ladder if hasattr(voice.ladder, "_ladder") else voice.ladder
    rec = {"words": set(), "sat_state": 0, "sat_out": 0, "tanh_clamp": 0, "tanh_calls": 0}
    orig_proc, orig_tanh, orig_sat = inner.process, inner.tanh_fx, fixed.sat
    SB, OB, dom = inner.SB, inner.OB, inner.dom_fx

    def proc(*a, **kw):
        rec["words"].add((int(kw["gain"]), int(kw["ogain"])))
        return orig_proc(*a, **kw)

    def tanh(y):
        rec["tanh_calls"] += 1
        if (y if y >= 0 else -y) >= dom:
            rec["tanh_clamp"] += 1
        return orig_tanh(y)

    def sat(v, bits):
        r = orig_sat(v, bits)
        if r != v:
            if bits == OB:
                rec["sat_out"] += 1
            else:
                rec["sat_state"] += 1
        return r

    inner.process, inner.tanh_fx, fixed.sat = proc, tanh, sat
    try:
        yield rec
    finally:
        del inner.process, inner.tanh_fx
        fixed.sat = orig_sat


# ---------------------------------------------------------------------------
# measurement
# ---------------------------------------------------------------------------
def stimulus(freqs, amp):
    x, parts = rr.tone_train(list(freqs), amp * rr.FS_Q15, 0.06, 0.20)
    return np.clip(np.round(x), -32768, 32767).astype(np.int16), parts


def project(y, parts, amp) -> np.ndarray:
    out = []
    for i0, nw, f in parts:
        a = am.tone_amplitude(y[i0:i0 + nw].astype(np.float64), f)
        if not a.ok:
            raise Refused(f"projection refused at {f:.0f} Hz: {a.reason}")
        out.append(20 * math.log10(max(a.value, 1e-12) / amp))
    return np.asarray(out)


def noise_thd(y, parts, amp, sr=rr.SR, fmax=NOISE_FMAX, n_harm=N_HARM) -> dict:
    """Per tone <= fmax: least-squares fit of DC + harmonics 1..n_harm (below
    0.45 sr). Noise = residual RMS in dB re the stimulus RMS (amp/sqrt 2);
    THD = harmonics 2..n RMS re the fundamental. Median over tones."""
    nz, th = [], []
    for i0, nw, f in parts:
        if f > fmax:
            continue
        seg = np.asarray(y[i0:i0 + nw], dtype=np.float64)
        t = np.arange(nw) / sr
        ks = [k for k in range(1, n_harm + 1) if k * f < 0.45 * sr]
        cols = [np.ones(nw)]
        for k in ks:
            cols += [np.cos(2 * np.pi * k * f * t), np.sin(2 * np.pi * k * f * t)]
        A = np.stack(cols, axis=1)
        coef, *_ = np.linalg.lstsq(A, seg, rcond=None)
        resid = seg - A @ coef
        amps_k = np.hypot(coef[1::2], coef[2::2])
        nz.append(20 * math.log10(max(np.sqrt(np.mean(resid ** 2)), 1e-12) / (amp / math.sqrt(2))))
        h = np.sqrt(np.sum(amps_k[1:] ** 2)) if len(amps_k) > 1 else 0.0
        th.append(20 * math.log10(max(h, 1e-12) / max(amps_k[0], 1e-12)))
    return {"noise_db_re_stimulus_median": round(float(np.median(nz)), 2),
            "noise_db_re_stimulus_worst": round(float(np.max(nz)), 2),
            "thd_db_median": round(float(np.median(th)), 2),
            "thd_db_worst": round(float(np.max(th)), 2),
            "n_tones": len(nz)}


def reads(freqs, g, cut, open_plateau) -> dict:
    return {"corner_hz": f1._v(rc.filt_corner(cut)(freqs, g)),
            "rolloff_db_oct": f1._v(rc.filt_rolloff(cut)(freqs, g)),
            "lowband_db": f1._v(rc.filt_lowband_gain(cut, open_plateau)(freqs, g))}


def surge_side(manifest) -> dict:
    """Surge's reads at each level from the captures. Takes must be
    bit-identical (asserted); take 1 is read."""
    out = {}
    for amp in AMPS:
        for cond in list(CASES.values()) + [OPEN]:
            k1, k2 = cap.clip_key(cond, amp, 1), cap.clip_key(cond, amp, 2)
            if manifest["clips"][k1]["sha256"] != manifest["clips"][k2]["sha256"]:
                raise Refused(f"Surge takes differ for {cond} at {amp}; take-to-take spread "
                              f"must be measured before a single take is used")
        fo, go = cap.capture_curve(OPEN, amp, 1, manifest)
        yo, mo = cap.load_capture(OPEN, amp, 1, manifest)
        po = [(int(a), int(b), float(f)) for a, b, f in mo["parts"]]
        lvl = {"open_noise": noise_thd(yo, po, amp), "cases": {}}
        for cid, cond in CASES.items():
            cut = rc.FILTER_CASES[cid]["cut_hz"]
            f, g = cap.capture_curve(cond, amp, 1, manifest)
            pl = am.plateau_db(fo, go, rc._ref_band(fo, cut))
            lvl["cases"][cid] = {**reads(f, g, cut, pl), "open_plateau_db": round(pl, 4),
                                 "curve_db": [round(float(v), 4) for v in g]}
        out[cap.amp_tag(amp)] = lvl
    return out


def surge_vs_frozen(manifest) -> dict:
    """The nominal-level captures against the frozen clips, as curves."""
    out = {}
    for cid, cond in list(CASES.items()) + [("open", OPEN)]:
        fz = cap.CONDITIONS[cond][1]
        _ff, gf, _ = rc.load_filter_reference(fz)
        _f, g = cap.capture_curve(cond, 0.25, 1, manifest)
        out[cond] = {"frozen_clip": fz,
                     "max_abs_curve_diff_db": float(np.max(np.abs(g - gf)))}
    return out


def run_job(args) -> dict:
    """One candidate at one level: the three cases plus wide-open."""
    name, s, amp, inject = args
    freqs = np.asarray(cap.load_manifest()["clips"][
        cap.clip_key(OPEN, amp, 1)]["freqs_hz"], dtype=np.float64)
    out = {"candidate": name, "s": s, "amp": amp, "conditions": {}}
    for cond, cut in [(OPEN, 20000.0)] + [(c, rc.FILTER_CASES[cid]["cut_hz"])
                                          for cid, c in CASES.items()]:
        base_words = tuple(candidate_regs(1.0, cut)[k] for k in ("gain", "ogain"))
        if inject == "CONSTRUCTOR_ONLY":
            voice, regs = constructor_only_regs(s, cut)
            predicted = (regs["gain"], regs["ogain"])
        else:
            _p, voice = f1.build_voice("selected")
            regs = candidate_regs(s, cut)
            predicted = (regs["gain"], regs["ogain"])
            if inject == "WORDS_NOT_ENTERING" and s != 1.0:
                regs = candidate_regs(1.0, cut)          # the words that actually go in
        ident = f1.check_identity(voice)
        if ident["problems"]:
            raise Refused(f"{name}: wrong ladder path: {ident['problems']}")
        xq, parts = stimulus(freqs, amp)
        cutv = np.full(len(xq), regs["cut_lo"], dtype=np.int64)
        with instrument(voice) as rec:
            y, g, k_eff = f1.render_path(voice, xq, cutv, regs)
        assert_registers(name, s, rec["words"], predicted, base_words)
        recon = dict(voice.ladder.last_reconstruction or {})
        gain_db = project(y, parts, amp * rr.FS_Q15)
        c = {"regs": {"gain": predicted[0], "ogain": predicted[1],
                      "entering": sorted(rec["words"]),
                      "baseline": list(base_words), "g_q16": int(g[0]),
                      "k_eff_q14": int(k_eff[0]), "cut_lo": regs["cut_lo"]},
             "curve_db": [round(float(v), 4) for v in gain_db],
             "clipping": {"reconstruction_would_clip": recon.get("would_clip_count"),
                          "reconstruction_max_abs_q15": recon.get("max_abs_q15"),
                          "output_rail_words": int(np.count_nonzero(
                              np.abs(y) >= (1 << (voice.ladder.out_bits - 1)) - 1)),
                          "sat_out_events": rec["sat_out"],
                          "sat_state_events": rec["sat_state"],
                          "tanh_domain_clamps": rec["tanh_clamp"],
                          "tanh_calls": rec["tanh_calls"]},
             "output_max_abs": int(np.max(np.abs(y)))}
        if cond == OPEN:
            c["noise"] = noise_thd(y, parts, amp * rr.FS_Q15)
        out["conditions"][cond] = c
    return out


def assemble(jobs: list, surge: dict) -> dict:
    """Per candidate, per level, per case: reads and errors vs matched Surge."""
    table = {}
    for j in jobs:
        tag = cap.amp_tag(j["amp"])
        freqs = None
        opn = j["conditions"][OPEN]
        lvl = {"open": {"noise": opn["noise"], "clipping": opn["clipping"],
                        "regs": opn["regs"]}, "cases": {}}
        for cid, cond in CASES.items():
            cut = rc.FILTER_CASES[cid]["cut_hz"]
            freqs = np.asarray(cap.load_manifest()["clips"][cap.clip_key(cond, j["amp"], 1)]
                               ["freqs_hz"], dtype=np.float64)
            g = np.asarray(j["conditions"][cond]["curve_db"])
            go = np.asarray(opn["curve_db"])
            pl = am.plateau_db(freqs, go, rc._ref_band(freqs, cut))
            ours = reads(freqs, g, cut, pl)
            ref = surge[tag]["cases"][cid]
            sc = f1.score(ours, ref)
            lvl["cases"][cid] = {"ours": ours, "surge": {k: ref[k] for k in ours},
                                 "score": sc, "regs": j["conditions"][cond]["regs"],
                                 "clipping": j["conditions"][cond]["clipping"]}
        table.setdefault(j["candidate"], {"s": j["s"], "levels": {}})["levels"][tag] = lvl
    return table


# ---------------------------------------------------------------------------
# the pre-declared rule (docs/scorecard/f1-level/selection-rule.md)
# ---------------------------------------------------------------------------
def _err(lv, cid, key):
    s = lv["cases"][cid]["score"][key]
    return (abs(s["error_pct"]) if key == "corner_hz" else abs(s["error"])) if s["valid"] else None


def _clip_sum(lv, key):
    tot = lv["open"]["clipping"][key]
    for cid in CASES:
        tot += lv["cases"][cid]["clipping"][key]
    return tot


def evaluate(table: dict) -> dict:
    P = cap.amp_tag(PRIMARY_AMP)
    base = table["baseline"]["levels"]
    verdicts = {}
    for name, cand in table.items():
        if name == "baseline":
            continue
        L = cand["levels"]
        why = {}
        why["1_registers"] = all(
            tuple(L[t]["cases"][c]["regs"]["entering"][0]) != tuple(L[t]["cases"][c]["regs"]["baseline"])
            for t in L for c in CASES)
        why["2_validity"] = all(L[t]["cases"][c]["score"][k]["valid"]
                                for t in L for c in CASES
                                for k in ("corner_hz", "rolloff_db_oct", "lowband_db"))
        if not why["2_validity"]:
            verdicts[name] = {"eligible": False, "checks": why}
            continue
        ce = {c: _err(L[P], c, "corner_hz") for c in CASES}
        cb = {c: _err(base[P], c, "corner_hz") for c in CASES}
        why["3_corner"] = (np.mean(list(cb.values())) - np.mean(list(ce.values())) >= 3.0
                           and all(ce[c] <= cb[c] for c in CASES))
        r_ok = True
        for c in CASES:
            re_, rb_ = _err(L[P], c, "rolloff_db_oct"), _err(base[P], c, "rolloff_db_oct")
            if re_ > rb_ + 0.30 or (rb_ <= 1.5 and re_ > 1.5):
                r_ok = False
        why["4_rolloff"] = r_ok
        why["5_lowband"] = all(_err(L[P], c, "lowband_db") <= 3.0 and
                               _err(L[P], c, "lowband_db") <= _err(base[P], c, "lowband_db") + 0.5
                               for c in CASES)
        why["6_noise"] = L[cap.amp_tag(0.0625)]["open"]["noise"]["noise_db_re_stimulus_median"] <= -60.0
        why["7_clipping"] = (all(_clip_sum(L[t], "reconstruction_would_clip") == 0 and
                                 _clip_sum(L[t], "output_rail_words") == 0 for t in L)
                             and _clip_sum(L[P], "tanh_domain_clamps") <= _clip_sum(base[P], "tanh_domain_clamps")
                             and _clip_sum(L[P], "sat_state_events") <= _clip_sum(base[P], "sat_state_events"))

        def spread(levels, c):
            v = [levels[t]["cases"][c]["score"]["corner_hz"]["error_pct"] for t in levels]
            return max(v) - min(v)
        why["8_level_dependence"] = all(spread(L, c) <= spread(base, c) for c in CASES)
        verdicts[name] = {"eligible": all(why.values()), "checks": why,
                          "mean_abs_corner_err_pct": round(float(np.mean(list(ce.values()))), 3),
                          "baseline_mean_abs_corner_err_pct": round(float(np.mean(list(cb.values()))), 3)}
    elig = [(n, v["mean_abs_corner_err_pct"], table[n]["s"]) for n, v in verdicts.items()
            if v["eligible"]]
    sel = None
    if elig:
        best = min(e[1] for e in elig)
        near = [e for e in elig if e[1] - best <= 0.5]
        sel = max(near, key=lambda e: e[2])[0]
    return {"verdicts": verdicts, "selection": sel}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", default=None)
    ap.add_argument("--inject", default="", choices=INJECTS)
    ap.add_argument("--workers", type=int, default=9)
    a = ap.parse_args(argv)
    try:
        base_vpu_asserted()
        manifest = cap.load_manifest()
        _, selected = f1.build_voice("selected")
        _, probe = f1.build_voice("selected")
        ident = f1.check_identity(probe)
        match = f1.exact_match(selected, probe)
        print(f"identity {ident['ladder_class']} factor={ident['factor']} causal={ident['causal']}"
              f"  exact match: ladder {match['ladder_mismatches']} / g {match['g_mismatches']} / "
              f"k {match['k_mismatches']} mismatches in {match['frames']} frames")
        if ident["problems"]:
            raise Refused("wrong ladder path: " + "; ".join(ident["problems"]))
        if not match["ok"]:
            print("FAIL     probe does not reproduce the selected component bit for bit")
            return 1
        # registers before any render: cheap, and the trap is visible here
        words = {}
        for name, s in CANDIDATES.items():
            for cid in CASES:
                cut = rc.FILTER_CASES[cid]["cut_hz"]
                r = (constructor_only_regs(s, cut)[1] if a.inject == "CONSTRUCTOR_ONLY"
                     else candidate_regs(s, cut))
                words.setdefault(name, set()).add((r["gain"], r["ogain"]))
        for name in CANDIDATES:
            print(f"registers {name:9s} s={CANDIDATES[name]:<6} gain/ogain {sorted(words[name])}")
        surge = surge_side(manifest)
        frozen_cmp = surge_vs_frozen(manifest)
        jobs_in = [(n, s, amp, a.inject) for n, s in CANDIDATES.items() for amp in AMPS]
        with cf.ProcessPoolExecutor(max_workers=a.workers) as ex:
            jobs = list(ex.map(run_job, jobs_in))
    except (Refused, cap.Refused, f1.Refused, rc.Refused) as e:
        print(f"REFUSED  {e}")
        return 2

    table = assemble(jobs, surge)
    ev = evaluate(table)

    print("\nSurge Type 2, matched levels (takes bit-identical; take 1 read)")
    print(f"{'amp':>7s} {'case':4s} {'corner':>9s} {'rolloff':>8s} {'lowband':>8s}")
    for tag, lv in surge.items():
        for cid, r in lv["cases"].items():
            print(f"{tag:>7s} {cid:4s} {r['corner_hz']:9.2f} {r['rolloff_db_oct']:8.2f} "
                  f"{r['lowband_db']:8.3f}")
    print("\ncandidates vs matched Surge")
    print(f"{'cand':8s} {'amp':>7s} {'case':4s} {'gain':>7s} {'ogain':>7s} | {'corner':>8s} "
          f"{'err%':>7s} | {'roll':>7s} {'err':>6s} | {'lowb':>6s} {'err':>6s} | "
          f"{'noise':>7s} {'thd':>7s} | clamps sat rail")
    for name, cand in table.items():
        for tag, lv in cand["levels"].items():
            for cid, c in lv["cases"].items():
                sc = c["score"]
                print(f"{name:8s} {tag:>7s} {cid:4s} {c['regs']['gain']:7d} {c['regs']['ogain']:7d} | "
                      f"{sc['corner_hz']['value']:8.2f} {sc['corner_hz']['error_pct']:+7.2f} | "
                      f"{sc['rolloff_db_oct']['value']:7.2f} {sc['rolloff_db_oct']['error']:+6.2f} | "
                      f"{sc['lowband_db']['value']:6.2f} {sc['lowband_db']['error']:+6.2f} | "
                      f"{lv['open']['noise']['noise_db_re_stimulus_median']:7.1f} "
                      f"{lv['open']['noise']['thd_db_median']:7.1f} | "
                      f"{c['clipping']['tanh_domain_clamps']} {c['clipping']['sat_state_events']} "
                      f"{c['clipping']['output_rail_words']}")
    for n, v in ev["verdicts"].items():
        print(f"verdict {n}: eligible={v['eligible']} {v['checks']}")
    print(f"SELECTION (within the rig's scope only): {ev['selection']}")

    if a.json:
        doc = {"what": "F1 selected path: compensated input-scaling candidates vs matched-level "
                       "Surge Type 2 captures; rule in selection-rule.md",
               "source_commit": rc.source_commit(),
               "inputs": {p: "sha256:" + f1._sha16(ROOT / p) for p in (
                   "tools/probes/f1_level_scaling.py", "tools/probes/f1_selected_path.py",
                   "tools/f1_level_capture.py", "tools/run_case.py",
                   "model/audio_measure.py", "model/voice_fx.py", "model/fixed.py",
                   "model/filter_rate_chain.py", "model/reference_rigs.py",
                   "docs/scorecard/f1-level/selection-rule.md",
                   "docs/scorecard/f1-level/captures/manifest.json",
                   "docs/scorecard/f1-level/captures/audio.zip")},
               "identity": ident, "exact_match": match,
               "base_vpu": BASE_VPU, "candidates": CANDIDATES,
               "fixed": {"res": RES, "drive": DRIVE, "LADDER_CFG": vf.LADDER_CFG,
                         "CUT_TRIM": vf.CUT_TRIM, "GROM_BITS": vf.GROM_BITS,
                         "KROM_BITS": vf.KROM_BITS, "rate": "RateConvertedLadder 2x causal"},
               "surge": surge, "surge_nominal_vs_frozen": frozen_cmp,
               "table": table, "evaluation": ev}
        p = pathlib.Path(a.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(doc, indent=1, default=list) + "\n")
        print(f"wrote {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
