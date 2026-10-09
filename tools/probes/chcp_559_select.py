#!/usr/bin/env python3
"""#559 candidate selection (DEV) and confirmation (CONFIRM) for CH and CP.
Driven by `tools/probes/chcp_559.py cp-sweep | ch-sweep | confirm`.

FROZEN IN THIS FILE BEFORE THE DEV SWEEP RAN (the commit that adds it). What
had been looked at before freezing, stated so a reader can discount it: the
shipped CH and CP at the default strike (a DEV condition) through the gate; a
CH ablation and a CH high-pass (f0, Q) and band-pass-Q exploration at the
default strike only; the CP reference's own band/tail shape. No CONFIRM
condition had been rendered for anything.

THE MEASURED EFFECTS (diagnosis; mechanisms are separate and unverified):
  CH  the skirt below ~6 kHz is 5-10 dB short and >12 kHz 2-6 dB hot against
      the take (1/3-octave, 0-60 ms); smoothing to 1/3 octave leaves 2.5 of the
      3.4 dB flatness gap, so the gap is mostly SPECTRAL BALANCE, not line
      structure. Removing the distortion makes flatness WORSE (13.3x), removing
      the high-pass far worse (centroid 33.6x).
  CP  the take's tail has TWO slopes: amplitude tau 83 ms on 0.08-0.2 s and
      315 ms on 0.3-1.2 s (fit validated on a known 270 ms answer: 262 ms), and
      is 8.5 dB above the recording floor at 1.2 s; SD5050 from the same session
      reaches its floor by 0.4 s, so the chain adds no slow tail. Ours is one
      80 ms slope. The take's gaps between the first bursts are -30 dB, ours
      -10 to -16: our tail sits too HIGH under the bursts and dies too FAST.

CANDIDATE FAMILIES (each a register value the shipped block already has,
unless marked EXPERIMENT):
  CH-1  M_CHHP Q (the CH high-pass), f0 held at 11.7 kHz. Why Q and not f0:
        under any equal-C Sallen-Key reading the components fix
        f0 = 1/(2 pi C sqrt(R153 R155)) = 11.75 kHz, while Q depends on the
        topology/gain reading, which reference 11 marks [inferred].
        Grid: CH_HPQ. M_CHHP is shared with CY's short high band (E_CYS).
  CH-2  M_HATBP Q (the hats' 7.1 kHz band-pass), at CH-1's selected Q. SHARED
        with OH and CY on the machine (one IC3 stage) and here; reference 10
        derives Q 6 from R58/R59 but omits the input C11/R55 network.
        Grid: CH_BPQ. OH and CY are NOT used to select; they are read after.
  CP-P  E_CPTAIL tau x peak (two registers), every other register shipped.
        Grid: CP_TAU_MS x CP_PEAK_DB (dB re the shipped 0.22). Programming only.
  CP-X  EXPERIMENT (needs a 19th envelope: N_ENV = 18 is full, so a block
        change): the shipped tail plus a slow component, tau CPX_TAU_MS at
        CPX_DB re the shipped tail peak. Prototyped in the exact fast path only.

SELECTION RULE (DEV only; 5 conditions: the shipped strike + 4 offsets):
  objective   CH: median over DEV of the CH flatness ratio.
              CP: median over DEV of max(decay ratio, attack ratio).
  eligible    (g1) no gate feature's median DEV ratio rises by more than
                   REGRESS_BAR (a quarter of the bar, i.e. of the 808's own one
                   WEAK step), and no feature with median baseline ratio <= 1
                   rises above 1;
              (CP d12a) D12A burst/tail-ratio and decay (T20) pass counts over
                   DEV >= the baseline's (tools/clap_d12a_probe.metrics, the
                   official CP plan);
              (CP rail) 0 rail samples at accent 2.0 at the shipped strike.
              (CH-2 is additionally read on OH and CY at their gate targets,
                   REPORTED, never selected on.)
  choose      the lowest objective; within TIE_REL of it, prefer programming
              only (P over X), then the smallest change from shipped.
CONFIRMATION (CONFIRM only, after selection, no retuning): the selected
  candidate and the shipped kit on CONFIRM_OFFSETS at accent 1 and the two
  CONFIRM_ACCENTS. CONFIRMED iff the median objective falls by >= CONFIRM_REL
  relative to the shipped kit on the same conditions, the objective improves on
  >= 5 of the 6 accent-1 offsets, and (g1) holds on CONFIRM.

AMENDMENT 1 -- CP-T, ADDED AFTER the CP-P/CP-X DEV results (which selected
NOTHING: every decay gain failed (g1) on CENTROID, +0.4 to +2.6 bar) and
BEFORE any CP-T point was rendered. Disclosed as post-hoc: its existence was
prompted by DEV results, so CONFIRM is the only evidence for it that was not
looked at. Measured before writing it: tanh does NOT darken our tail (the
band-pass tap's centroid is 1878 Hz linear, 1889 Hz after tanh; the take's
tail reads ~1220 Hz) -- the "distortion makes the tail bright" reading is
REFUTED, and a RAW (all-pole) mode on noise at 1.0-1.3 kHz, Q 0.7, has the
take's tail centroid (1085-1300 Hz).
  CP-T  EXPERIMENT (needs one more path AND one more mode: a block change).
        The bursts keep P_CPOUT (tanh, E_CPBURST alone); the TAIL gets its own
        path: noise -> RAW mode CPT_F_HZ, Q CPT_Q -> LIN x E_CPTAIL -> mix.
        Prototyped EXACTLY in the real block for a CP-solo render by borrowing
        the RS circuit's idle slots (P_RS1X, P_RS1OUT, M_RS1); `check-cpt`
        asserts bit-identity of the fast path against that real-block render.
        Grid: CPT_F_HZ x CPT_TAU_MS x CPT_DB, where CPT_DB is the tail's RMS at
        its fire re the SHIPPED tail's (0.22 x rms(tanh tap)).
        Same selection rule, objective and confirmation as the others.
  Prediction: decay improves AND centroid no longer regresses (the take's
        tail is dark); attack is not repaired by the tail (it lives in
        1.5-4 kHz in the first 30 ms).

PREDICTIONS (stated before the sweep, from the diagnosis above, not from DEV
grid numbers):
  CH-1  flatness falls monotonically from Q 2.5 to about Q 0.7 and flattens
        below (the ~12 dB/oct skirt below f0 does not depend on Q; only the
        resonant bump does).
  CH-2  flatness falls as Q falls from 6 (a broader band is flatter); OH and
        CY move in the SAME direction if Q is the machine's (shared stage).
  CP-P  decay improves with tau and worsens with peak at fixed tau: the best
        P point is long and LOW (tau >= 250 ms, peak <= -8 dB), i.e. H2 (one
        slow quiet tail) rather than "the same tail, longer". D12A's ratio is
        the constraint that bites at high peak x long tau.
  CP-X  at least as good as the best P point on decay (it has one more degree
        of freedom), and not better on attack.
"""
from __future__ import annotations

import math

import numpy as np

import chcp_559 as c

CH_HPQ = (0.5, 0.7, 1.0, 1.5, 2.5)
CH_BPQ = (2.0, 3.0, 4.0, 6.0)
CP_TAU_MS = (80, 120, 160, 200, 250, 315, 400)
CP_PEAK_DB = (0.0, -4.0, -8.0, -12.0, -16.0)
CPX_TAU_MS = (250, 315, 400)
CPX_DB = (-12.0, -17.0, -22.0)
CPT_F_HZ = (1000.0, 1300.0)
CPT_Q = 0.7
CPT_EXC_ATT = 2
CPT_TAU_MS = (80, 120, 160, 250, 315)
CPT_DB = (0.0, -6.0, -12.0)
REGRESS_BAR = 0.25
TIE_REL = 0.05
CONFIRM_REL = 0.25
SHIPPED = {"CH_HPQ": 2.5, "CH_BPQ": 6.0, "CP_TAU_MS": 80, "CP_PEAK_DB": 0.0}
CP_TAIL_PEAK = 0.22
FEATURES = ("spec", "spec_peak", "centroid", "flatness", "impulse", "attack", "decay", "modulation")


# ---------------------------------------------------------------------------
# kits
# ---------------------------------------------------------------------------
def ch_kit(hpq: float = 2.5, bpq: float = 6.0, sound: str = "CH") -> list:
    dx = c._dx()
    img = dict(dx.kit_with_sounds(sound))
    for a, v in dx.mode_writes(dx.M_CHHP, 11700.0, hpq, 0.69, dx.HP):
        img[a] = v
    for a, v in dx.mode_writes(dx.M_HATBP, 7117.0, bpq, 0.0, dx.BP):
        img[a] = v
    return sorted(img.items())


def cp_p_kit(tau_ms: float, peak_db: float, sound: str = "CP") -> list:
    dx = c._dx()
    img = dict(dx.kit_with_sounds(sound))
    if sound == "CP":
        for a, v in dx.env_writes(dx.E_CPTAIL, dx.CP, tau_ms * 1e-3, CP_TAIL_PEAK * 10 ** (peak_db / 20)):
            img[a] = v
    return sorted(img.items())


def cpx_extra(tau_ms: float, db: float, hit: int, accent: float, n: int) -> np.ndarray:
    """EXPERIMENT: a slow envelope on the clap's stop, as a 19th EnvFx would be."""
    dx = c._dx()
    kit = dict(dx.kit_with_sounds("CP"))
    e = dx.E_CPTAIL                     # borrow the slot's address layout only
    for a, v in dx.env_writes(e, dx.CP, tau_ms * 1e-3, CP_TAIL_PEAK * 10 ** (db / 20)):
        kit[a] = v
    return c.env_trace(sorted(kit.items()), e, dx.CP, hit, accent, n)


# ---------------------------------------------------------------------------
# evaluation
# ---------------------------------------------------------------------------
def _d12a_ref(refs):
    import run_case as rc
    rx, rsr, _, _ = rc.load_reference("CP", refs)
    return (rc.prepare(rx, rsr), rsr)


def eval_cp(y, refs, ref12) -> dict:
    import clap_d12a_probe as probe
    g = c.gate("CP", y, 48000, refs)
    m = probe.metrics(y, 48000, ref12)
    g["d12a"] = {k: {"value": v.get("value"), "error": v.get("error"), "tolerance": v.get("tolerance"),
                     "valid": v.get("valid"), "pass": v.get("pass")} for k, v in m.items()}
    g["peak"] = float(np.abs(y).max())
    return g


def cp_render(cand: dict, hit: int, accent: float) -> np.ndarray:
    dx = c._dx()
    if cand["family"] == "T":
        peak = c.cpt_peak_for_db(cand["f_hz"], CPT_Q, CPT_EXC_ATT, cand["db"])
        return c.cpt_fast(cand["f_hz"], CPT_Q, CPT_EXC_ATT, peak, cand["tau_ms"] * 1e-3, hit, accent)
    if cand["family"] == "X":
        n = hit - c.BASE_HIT + int(2.2 * dx.SR)
        ex = cpx_extra(cand["tau_ms"], cand["db"], hit, accent, n)
        return c.cp_fast(dx.kit_with_sounds("CP"), hit, accent, extra=ex)
    return c.cp_fast(cp_p_kit(cand["tau_ms"], cand["peak_db"]), hit, accent)


def ch_render(cand: dict, hit: int, accent: float, sound: str = "CH") -> np.ndarray:
    secs = 0.8 if sound == "CH" else 3.6
    return c.render_block(sound, ch_kit(cand["hpq"], cand["bpq"], sound), hit, accent, seconds=secs)


def _median_ratios(rows: list) -> dict:
    return {f: float(np.median([r["ratios"][f] for r in rows])) for f in FEATURES if f in rows[0]["ratios"]}


def objective(sound: str, rows: list) -> float:
    if sound == "CH":
        return float(np.median([r["ratios"]["flatness"] for r in rows]))
    return float(np.median([max(r["ratios"]["decay"], r["ratios"]["attack"]) for r in rows]))


def regressions(base_rows: list, rows: list) -> list:
    mb, mc = _median_ratios(base_rows), _median_ratios(rows)
    out = []
    for f in mb:
        if mc[f] - mb[f] > REGRESS_BAR:
            out.append(f"{f} {mb[f]:.2f}->{mc[f]:.2f}")
        elif mb[f] <= 1.0 < mc[f]:
            out.append(f"{f} crossed 1.0 ({mb[f]:.2f}->{mc[f]:.2f})")
    return out


def d12a_counts(rows: list) -> dict:
    cnt = lambda k: sum(1 for r in rows if r["d12a"][k]["valid"] and r["d12a"][k]["pass"])
    return {"ratio_pass": cnt("burst/tail ratio"), "decay_pass": cnt("decay"), "n": len(rows)}


def _label(cand: dict) -> str:
    if cand.get("family") == "X":
        return f"X slow{cand['tau_ms']}ms@{cand['db']:+.0f}dB"
    if cand.get("family") == "T":
        return f"T dark{cand['f_hz']:.0f}Hz tail{cand['tau_ms']}ms@{cand['db']:+.0f}dB"
    if "tau_ms" in cand:
        return f"P tail{cand['tau_ms']}ms@{cand['peak_db']:+.0f}dB"
    return f"CH hpQ{cand['hpq']} bpQ{cand['bpq']}"


def _summ(sound, base_rows, rows, cand) -> dict:
    s = {"label": _label(cand), "cand": cand, "objective": objective(sound, rows),
         "median_ratios": _median_ratios(rows), "regressions": regressions(base_rows, rows),
         "per_condition_worst": [round(r["worst"], 3) for r in rows]}
    if sound == "CP":
        s["d12a"] = d12a_counts(rows)
    return s


def cp_sweep(refs, families: str = "PX") -> dict:
    dx = c._dx()
    ref12 = _d12a_ref(refs)
    conds = c.conditions("dev")
    cands = []
    if "P" in families:
        cands += [{"family": "P", "tau_ms": t, "peak_db": p} for t in CP_TAU_MS for p in CP_PEAK_DB]
    if "X" in families:
        cands += [{"family": "X", "tau_ms": t, "db": d} for t in CPX_TAU_MS for d in CPX_DB]
    if "T" in families:
        cands += [{"family": "T", "f_hz": f, "tau_ms": t, "db": d}
                  for f in CPT_F_HZ for t in CPT_TAU_MS for d in CPT_DB]
    base = {"family": "P", "tau_ms": SHIPPED["CP_TAU_MS"], "peak_db": SHIPPED["CP_PEAK_DB"]}
    base_rows = [eval_cp(cp_render(base, h, a), refs, ref12) for h, a in conds]
    b = _summ("CP", base_rows, base_rows, base)
    b["d12a"] = d12a_counts(base_rows)
    print(f"CP shipped  O={b['objective']:.2f} d12a {b['d12a']} med {_fmtm(b['median_ratios'])}", flush=True)
    out = {"baseline": b, "candidates": []}
    for cand in cands:
        rows = [eval_cp(cp_render(cand, h, a), refs, ref12) for h, a in conds]
        s = _summ("CP", base_rows, rows, cand)
        y2 = cp_render(cand, c.BASE_HIT, 2.0)
        s["rail_acc2"] = int(np.sum(np.abs(y2) >= 32767 / 32768))
        s["peak_acc2"] = float(np.abs(y2).max())
        why = list(s["regressions"])
        if s["d12a"]["ratio_pass"] < b["d12a"]["ratio_pass"]:
            why.append(f"d12a ratio {s['d12a']['ratio_pass']} < {b['d12a']['ratio_pass']}")
        if s["d12a"]["decay_pass"] < b["d12a"]["decay_pass"]:
            why.append(f"d12a decay {s['d12a']['decay_pass']} < {b['d12a']['decay_pass']}")
        if s["rail_acc2"]:
            why.append(f"{s['rail_acc2']} rail samples at accent 2")
        s["ineligible"] = why
        out["candidates"].append(s)
        print(f"{s['label']:24s} O={s['objective']:.2f} d12a {s['d12a']['ratio_pass']}/{s['d12a']['decay_pass']} "
              f"pk2 {s['peak_acc2']:.3f} {'ELIGIBLE' if not why else 'x ' + '; '.join(why)} | {_fmtm(s['median_ratios'])}",
              flush=True)
    out["selected"] = choose(out["candidates"], b)
    print("SELECTED (DEV):", out["selected"], flush=True)
    return out


def _change_size(cand: dict) -> float:
    if "tau_ms" in cand and cand.get("family") == "P":
        return abs(math.log(cand["tau_ms"] / SHIPPED["CP_TAU_MS"])) + abs(cand["peak_db"]) / 20
    if cand.get("family") in ("X", "T"):
        return 99.0 + abs(math.log(cand["tau_ms"] / SHIPPED["CP_TAU_MS"])) + abs(cand["db"]) / 20
    return abs(math.log(cand["hpq"] / SHIPPED["CH_HPQ"])) + abs(math.log(cand["bpq"] / SHIPPED["CH_BPQ"]))


def choose(cands: list, base: dict):
    ok = [s for s in cands if not s["ineligible"] and s["objective"] < base["objective"]]
    if not ok:
        return None
    best = min(s["objective"] for s in ok)
    tied = [s for s in ok if s["objective"] <= best * (1 + TIE_REL)]
    tied.sort(key=lambda s: (s["cand"].get("family") in ("X", "T"), _change_size(s["cand"])))
    return {"label": tied[0]["label"], "cand": tied[0]["cand"], "objective": tied[0]["objective"],
            "best_objective_any": best, "tied": [s["label"] for s in tied]}


def _fmtm(m: dict) -> str:
    return " ".join(f"{k}={v:.2f}" for k, v in m.items())


def ch_sweep(refs) -> dict:
    conds = c.conditions("dev")
    base = {"hpq": SHIPPED["CH_HPQ"], "bpq": SHIPPED["CH_BPQ"]}
    base_rows = [c.gate("CH", ch_render(base, h, a), 48000, refs) for h, a in conds]
    b = _summ("CH", base_rows, base_rows, base)
    print(f"CH shipped  O={b['objective']:.2f} med {_fmtm(b['median_ratios'])}", flush=True)
    out = {"baseline": b, "stage1": [], "stage2": []}

    def run(cand):
        rows = [c.gate("CH", ch_render(cand, h, a), 48000, refs) for h, a in conds]
        s = _summ("CH", base_rows, rows, cand)
        s["ineligible"] = list(s["regressions"])
        print(f"{s['label']:24s} O={s['objective']:.2f} {'ELIGIBLE' if not s['ineligible'] else 'x ' + '; '.join(s['ineligible'])}"
              f" | {_fmtm(s['median_ratios'])}", flush=True)
        return s

    for q in CH_HPQ:
        out["stage1"].append(run({"hpq": q, "bpq": SHIPPED["CH_BPQ"]}))
    sel1 = choose(out["stage1"], b)
    out["selected_stage1"] = sel1
    print("SELECTED stage 1 (DEV):", sel1, flush=True)
    hpq = sel1["cand"]["hpq"] if sel1 else SHIPPED["CH_HPQ"]
    for q in CH_BPQ:
        out["stage2"].append(run({"hpq": hpq, "bpq": q}))
    sel2 = choose(out["stage2"] + ([s for s in out["stage1"] if s["label"] == sel1["label"]] if sel1 else []), b)
    out["selected"] = sel2
    print("SELECTED stage 2 (DEV):", sel2, flush=True)
    # OH and CY read AFTER selection, at their gate targets' default strike; never selected on
    out["shared_voices_after_selection"] = {}
    for cand in ([sel1["cand"]] if sel1 else []) + ([sel2["cand"]] if sel2 else []):
        row = {}
        for snd in ("OH", "CY"):
            gs = c.gate(snd, ch_render(base, c.BASE_HIT, 1.0, snd), 48000, refs)
            gc = c.gate(snd, ch_render(cand, c.BASE_HIT, 1.0, snd), 48000, refs)
            row[snd] = {"shipped": gs["ratios"], "candidate": gc["ratios"],
                        "worst": [gs["worst"], gc["worst"]]}
            print(f"  {_label(cand)} {snd}: worst {gs['worst']:.2f} -> {gc['worst']:.2f} | "
                  + " ".join(f"{f}={gs['ratios'][f]:.2f}->{gc['ratios'][f]:.2f}" for f in gs["ratios"]), flush=True)
        out["shared_voices_after_selection"][_label(cand)] = row
    return out


def confirm(sound: str, cand: dict, refs) -> dict:
    conds = c.conditions("confirm")
    if sound == "CP":
        ref12 = _d12a_ref(refs)
        base = {"family": "P", "tau_ms": SHIPPED["CP_TAU_MS"], "peak_db": SHIPPED["CP_PEAK_DB"]}
        ev = lambda k, h, a: eval_cp(cp_render(k, h, a), refs, ref12)
    else:
        base = {"hpq": SHIPPED["CH_HPQ"], "bpq": SHIPPED["CH_BPQ"]}
        ev = lambda k, h, a: c.gate("CH", ch_render(k, h, a), 48000, refs)
    br = [ev(base, h, a) for h, a in conds]
    cr = [ev(cand, h, a) for h, a in conds]
    one = lambda r: (r["ratios"]["flatness"] if sound == "CH" else max(r["ratios"]["decay"], r["ratios"]["attack"]))
    n1 = len(c.CONFIRM_OFFSETS)
    better = sum(1 for i in range(n1) if one(cr[i]) < one(br[i]))
    ob, oc = objective(sound, br), objective(sound, cr)
    reg = regressions(br, cr)
    # ADDED AFTER THE FREEZE, before any CONFIRM render, as disclosure only
    # (rule 8: a median over conditions is the aggregate that would hide one
    # condition's regression). Reported per condition; it can only expose.
    per_cond_reg = [{"cond": list(k), "regressions": regressions([br[i]], [cr[i]])}
                    for i, k in enumerate(conds) if regressions([br[i]], [cr[i]])]
    ok = (oc <= ob * (1 - CONFIRM_REL)) and better >= n1 - 1 and not reg
    res = {"candidate": _label(cand), "objective_shipped": ob, "objective_candidate": oc,
           "relative_change": oc / ob - 1, "better_on": f"{better}/{n1}", "regressions": reg,
           "per_condition_regressions_DISCLOSURE": per_cond_reg,
           "median_ratios_shipped": _median_ratios(br), "median_ratios_candidate": _median_ratios(cr),
           "per_condition": [{"cond": list(k), "shipped": br[i]["ratios"], "candidate": cr[i]["ratios"]}
                             for i, k in enumerate(conds)],
           "confirmed": bool(ok)}
    if sound == "CP":
        res["d12a_shipped"] = d12a_counts(br)
        res["d12a_candidate"] = d12a_counts(cr)
        res["d12a_per_condition"] = [{"cond": list(k), "shipped": br[i]["d12a"], "candidate": cr[i]["d12a"]}
                                     for i, k in enumerate(conds)]
    print(f"CONFIRM {sound} {_label(cand)}: objective {ob:.2f} -> {oc:.2f} ({oc / ob - 1:+.0%}), better on "
          f"{better}/{n1}, regressions {reg or 'none'} -> {'CONFIRMED' if ok else 'NOT CONFIRMED'}", flush=True)
    for pc in per_cond_reg:
        print(f"   per-condition regression (disclosure) at {pc['cond']}: {pc['regressions']}", flush=True)
    print(f"   shipped   {_fmtm(res['median_ratios_shipped'])}", flush=True)
    print(f"   candidate {_fmtm(res['median_ratios_candidate'])}", flush=True)
    return res


def sensitivity_tables(ch: dict, cp: dict) -> str:
    """The DEV sweeps as `tools/sensitivity.py` fixed-width tables: one dial per
    table, the others held at the values the header names. Objectives are the
    selection objectives (median over DEV of the ratio to the WEAK bar)."""
    chr_, cpr = ch["result"], cp["result"]
    sel_hpq = chr_["selected_stage1"]["cand"]["hpq"] if chr_.get("selected_stage1") else SHIPPED["CH_HPQ"]
    L = ["Issue #559 DEV sweeps -- written by `python3 tools/probes/chcp_559.py tables`.",
         f"provenance: CH sweep commit {ch.get('commit', '?')[:12]} model {ch.get('model_sha16')}; "
         f"CP sweep commit {cp.get('commit', '?')[:12]} model {cp.get('model_sha16')}",
         "Grids, rules and predictions: tools/probes/chcp_559_select.py docstring (committed before the sweep).",
         "Objective: median over the 5 DEV conditions of the ratio to the #379 WEAK bar (lower is better).", ""]
    L += ["ch_hp_q -- CH flatness ratio against the CH high-pass Q x10 (f0 11.7 kHz, hat band-pass Q 6)",
          "CH_HP_Q_X10 flatness_ratio worst_median"]
    for s in sorted(chr_["stage1"], key=lambda s: s["cand"]["hpq"]):
        L.append(f"{round(s['cand']['hpq'] * 10)} {s['objective']:.3f} {max(s['median_ratios'].values()):.3f}")
    L += ["", f"ch_bp_q -- CH flatness ratio against the hats' 7.1 kHz band-pass Q x10 (CH high-pass Q {sel_hpq})",
          "HAT_BP_Q_X10 flatness_ratio worst_median"]
    for s in sorted(chr_["stage2"], key=lambda s: s["cand"]["bpq"]):
        L.append(f"{round(s['cand']['bpq'] * 10)} {s['objective']:.3f} {max(s['median_ratios'].values()):.3f}")
    sel = cpr.get("selected") or {"cand": {"family": "P", "tau_ms": SHIPPED["CP_TAU_MS"],
                                            "peak_db": SHIPPED["CP_PEAK_DB"]}}
    pk = sel["cand"].get("peak_db", SHIPPED["CP_PEAK_DB"])
    tau = sel["cand"].get("tau_ms", SHIPPED["CP_TAU_MS"])
    P = [s for s in cpr["candidates"] if s["cand"]["family"] == "P"]
    L += ["", f"cp_tail_tau -- CP max(decay, attack) ratio against the tail tau in ms (tail peak {pk:+.0f} dB re 0.22)",
          "CP_TAIL_TAU_MS objective_ratio decay_ratio attack_ratio"]
    for s in sorted((s for s in P if s["cand"]["peak_db"] == pk), key=lambda s: s["cand"]["tau_ms"]):
        m = s["median_ratios"]
        L.append(f"{s['cand']['tau_ms']} {s['objective']:.3f} {m['decay']:.3f} {m['attack']:.3f}")
    L += ["", f"cp_tail_peak -- CP max(decay, attack) ratio against the tail peak in dB re 0.22 (tail tau {tau} ms)",
          "CP_TAIL_PEAK_DB objective_ratio decay_ratio attack_ratio"]
    for s in sorted((s for s in P if s["cand"]["tau_ms"] == tau), key=lambda s: s["cand"]["peak_db"]):
        m = s["median_ratios"]
        L.append(f"{round(s['cand']['peak_db'])} {s['objective']:.3f} {m['decay']:.3f} {m['attack']:.3f}")
    return "\n".join(L) + "\n"


def run(cmd: str, refs, cand: dict | None = None) -> dict:
    if cmd == "cp-sweep":
        return cp_sweep(refs)
    if cmd == "cpt-sweep":
        return cp_sweep(refs, "T")
    if cmd == "ch-sweep":
        return ch_sweep(refs)
    raise ValueError(cmd)
