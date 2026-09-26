#!/usr/bin/env python3
"""D12A (clap burst/tail balance): reproduce, check the segmentation, split the
ratio into its parts, and demonstrate parameter sensitivity -- DIAGNOSTIC ONLY.

This file changes nothing that ships. It does not edit `model/drums_fx.py`, the
scorer (`tools/run_case.py`) or `docs/scorecard/results/D12A.json`; every
"candidate" render below is a register image built HERE and thrown away. The
record it writes, `docs/scorecard/clap-d12a/probe.json`, is evidence for a
proposed change, not a promotion.

    tools/clap_d12a_probe.py                       everything, writes probe.json
    tools/clap_d12a_probe.py --known-answer-only   the synthetic control alone

WHAT EACH SECTION ANSWERS
-------------------------
basis        Which tree, which scorer, which reference bytes. The corpus is
             checked against its pinned manifest file by file; a mismatch
             REFUSES (exit 2) rather than measuring a different corpus.
reproduce    The committed D12A numbers, re-derived through run_case's own
             `run_drum_case` code path, compared field by field. A mismatch
             REFUSES: a diagnosis of a result we cannot reproduce is a diagnosis
             of something else. Our render is hashed and rendered twice.
segmentation Onset, burst positions and where the 30 ms split falls on each
             side, and the ratio error as a function of the split time.
energies     Energy in each window at ORIGINAL gain (dB re FS^2 s -- the
             reference's gain is arbitrary, Fischer pinned LEVEL at maximum, so
             these are recorded, not compared) and in the metric's own frame
             (peak-normalised). Plus the tail's own level and time constant,
             fitted on 80-200 ms where neither side has a burst left.
components   Our render split into burst-only and tail-only renders. The path
             is `tanh(noise) * (env_burst + env_tail)`, linear in the envelopes,
             so x = x_burst + x_tail to within integer rounding; that identity
             is ASSERTED, and is what makes the sensitivity predictions below
             predictions rather than fits.
sensitivity  Controlled perturbations of the clap's register fields, each with
             its predicted ratio (from the component renders, before the render
             is made) and the measured one, with all three D12A metrics.

The known-answer control (`known_answer()`) is a synthetic clap-like signal
whose window energies are closed-form, run through the scorer's own
`prepare()` and `_early_late_db`, at both sample rates, together with four
estimator mutants that must each be caught.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import pathlib
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "model"))

import numpy as np  # noqa: E402

import run_case as rc  # noqa: E402

OUT_DIR = ROOT / "docs" / "scorecard" / "clap-d12a"
RECORD = ROOT / "docs" / "scorecard" / "results" / "D12A.json"
MANIFEST_DEFAULT = pathlib.Path.home() / "dev" / "refs" / "tr808-fischer-85fbecf.sha256"
PINNED_COMMIT = "85fbecf"

SPLIT_S, END_S = 0.030, 0.200          # the metric's own windows (run_case DRUM_PLAN["CP"])
TAIL_FIT = (0.080, 0.200)              # after every burst on BOTH sides (see segmentation)
KNOWN_ANSWER_TOL_DB = 0.01

#: The proposed next change's frozen candidate set: ONE mechanism (the burst
#: VCA's final strike decays slowly -- a host write of the burst envelope's
#: RATE at the fourth strike), with the tail's time constant set to its
#: MEASURED 80 ms rather than the 47 ms component estimate, which is not a
#: candidate dimension but a precondition: without it the final burst alone
#: drops the decay T20 out of tolerance (sensitivity rows). 38.5 ms is RC of
#: C144 x R365, the discharge the final ramp completes (tr808-reference 7).
CANDIDATES = [
    ("C1 final tau 30 ms", dict(bursts=3, period=511, final_tau=30e-3, t_tau=80e-3)),
    ("C2 final tau 38.5 ms (C144 x R365)", dict(bursts=3, period=511, final_tau=38.5e-3, t_tau=80e-3)),
    ("C3 final tau 45 ms", dict(bursts=3, period=511, final_tau=45e-3, t_tau=80e-3)),
]


class Refused(Exception):
    """A precondition failed: the probe says so instead of producing numbers."""


# ---------------------------------------------------------------------------
# basis
# ---------------------------------------------------------------------------
def _sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_manifest(refdir: pathlib.Path, manifest: pathlib.Path) -> dict:
    if not manifest.exists():
        raise Refused(f"reference manifest missing: {manifest}")
    rows = [ln.split(None, 1) for ln in manifest.read_text().splitlines() if ln.strip()]
    bad = []
    for digest, rel in rows:
        p = refdir / rel.strip()
        if not p.exists() or _sha256(p) != digest:
            bad.append(rel.strip())
    if bad:
        raise Refused(f"{len(bad)} of {len(rows)} reference files differ from {manifest.name}: "
                      f"{bad[:5]}")
    head = subprocess.run(["git", "-C", str(refdir), "rev-parse", "HEAD"],
                          capture_output=True, text=True)
    return {"refdir": str(refdir), "manifest": str(manifest), "files_checked": len(rows),
            "files_mismatched": 0, "corpus_git_head": head.stdout.strip() or "unknown",
            "cp_sha256": _sha256(refdir / "cp8" / "CP.WAV")}


def basis(refdir, manifest) -> dict:
    git = lambda *a: subprocess.run(["git", "-C", str(ROOT), *a], capture_output=True,
                                    text=True).stdout.strip()
    b = {"head": git("rev-parse", "HEAD"), "origin_main": git("rev-parse", "origin/main"),
         "dirty_paths": [ln for ln in git("status", "--porcelain").splitlines() if ln],
         "scorer_inputs": rc.model_input_hashes(),
         "reference": verify_manifest(refdir, manifest)}
    if not b["reference"]["corpus_git_head"].startswith(PINNED_COMMIT):
        raise Refused(f"corpus at {b['reference']['corpus_git_head']}, pinned {PINNED_COMMIT}")
    return b


# ---------------------------------------------------------------------------
# rendering with a register override (diagnostic only)
# ---------------------------------------------------------------------------
DEFAULT = dict(b_tau=4e-3, b_peak=0.69, bursts=2, period=480, t_tau=47e-3, t_peak=0.22, final_tau=None)


def final_rate_writes(p: dict, hit_frame: int) -> list:
    """The host-sequenced part of the FINAL-BURST mechanism (diagnostic): at the
    frame of the last re-strike, rewrite the burst envelope's RATE register so
    the last strike decays with `final_tau` instead of the burst tau. Same kind
    of host write as `drums_fx.bd_attack_writes` / `tom_pitch_drop_writes`
    (contract 15.6: registers may change on any frame), no block change."""
    import drums_fx as dx
    if not p.get("final_tau"):
        return []
    f = hit_frame + p["bursts"] * p["period"]
    return [(f, dx.A_ENV + dx.E_CPBURST * dx.ENV_STRIDE + 2, dx.rate_reg(p["final_tau"]))]


def render_cp(p: dict | None = None, accent: float = 1.0, offset: int = 0) -> tuple:
    """`run_case.render_drum_solo("CP")` with the two clap envelopes rewritten.
    With `p` = DEFAULT it must be bit-identical to render_drum_solo; `main`
    asserts that before any other render is believed."""
    import drums_fx as dx
    p = dict(DEFAULT, **(p or {}))
    img = dict(dx.kit_with_sounds("CP"))
    for a, v in (dx.env_writes(dx.E_CPBURST, dx.CP, p["b_tau"], p["b_peak"],
                               bursts=p["bursts"], period=p["period"])
                 + dx.env_writes(dx.E_CPTAIL, dx.CP, p["t_tau"], p["t_peak"])):
        img[a] = v
    n = int(rc.SOLO_SECONDS.get("CP", 2.2) * dx.SR)
    d = dx.DrumsFx()
    # `offset` moves the strike later by whole frames. The LFSR free-runs from
    # frame 0, so this changes ONLY the noise realisation under the envelope --
    # the nuisance variation a real machine's free-running noise has on every hit.
    # The render is then shifted back so the record still starts 10 ms before it.
    hit = int(0.01 * dx.SR) + offset
    n += offset
    w = dx.hit_writes([(hit, dx.CP, accent)], sorted(img.items())) + final_rate_writes(p, hit)
    dm, bd = d.play(sorted(w, key=lambda t: t[0]), n)
    g = dx.accent_reg(0.45)
    out = dx.output_fx(np.zeros(n), 0, dm, g, bd, g)[offset:]
    return np.asarray(out, dtype=np.float64) / 32768.0, dx.SR


def _render_job(args):
    return render_cp(*args)


def audio_sha(x) -> str:
    return hashlib.sha256(np.ascontiguousarray(np.asarray(x, dtype=np.float64)).tobytes()).hexdigest()


# ---------------------------------------------------------------------------
# measurement helpers (all on run_case's own prepare/window)
# ---------------------------------------------------------------------------
def metrics(x, sr, ref) -> dict:
    ours = (rc.prepare(x, sr), sr)
    out = {}
    for name, units, est, tol_rule in rc.DRUM_PLAN["CP"]:
        m = rc.measure_pair(name, units, est, ours, ref, tol_rule, {})
        out[name] = {k: m.get(k) for k in ("value", "reference", "error", "tolerance", "valid")}
        if m.get("valid"):
            out[name]["pass"] = abs(m["error"]) <= m["tolerance"]
    return out


def win_db(y, sr, t0, t1) -> float:
    e = float(np.sum(rc.window(y, sr, t0, t1) ** 2)) / sr
    return 10 * math.log10(e) if e > 0 else float("-inf")


def ratio_db(y, sr, split=SPLIT_S, end=END_S) -> float:
    return rc._early_late_db(split, end)(y, sr).value


def bursts(y, sr) -> list:
    env = rc.am.rms_envelope(rc.window(y, sr, 0.0, 0.120), rc.ENV_WIN_MS["CP"], sr)
    b = rc.am.envelope_bursts(env, sr, min_sep_s=0.006, min_dip_db=2.0)
    return [{"t_ms": round(float(t) * 1e3, 2), "rel_level": round(float(v), 3)} for t, v, *_ in b]


def tail_fit(y, sr, t0=TAIL_FIT[0], t1=TAIL_FIT[1], bin_s=0.010) -> dict:
    """Straight line through 10 ms energy bins (dB) over [t0, t1): the tail's
    amplitude time constant and its level extrapolated back to t = 0, in dB re
    the side's own peak. Validated on a synthetic exponential in known_answer()."""
    ts, es = [], []
    t = t0
    while t + bin_s <= t1 + 1e-12:
        seg = rc.window(y, sr, t, t + bin_s)
        ts.append(t + bin_s / 2)
        es.append(10 * math.log10(float(np.mean(seg ** 2))))
        t += bin_s
    slope, icpt = np.polyfit(ts, es, 1)
    resid = float(np.max(np.abs(np.polyval([slope, icpt], ts) - es)))
    tau = -20 / (slope * math.log(10))   # amplitude tau: mean-square slope is -20/(tau ln10) dB/s
    return {"amp_tau_ms": round(float(tau) * 1e3, 2), "ms_level_at_t0_db": round(float(icpt), 2),
            "max_residual_db": round(resid, 2), "window_s": [t0, t1]}


# ---------------------------------------------------------------------------
# the known-answer control
# ---------------------------------------------------------------------------
def synthetic_clap(sr: int, *, a=1.0, b=0.2, b_tau=0.004, t_tau=0.047, lead_s=0.050,
                   n_bursts=3, period_s=0.010, tail_start_s=0.031):
    """A clap-shaped signal whose window energies are CLOSED FORM.

    Sign-alternating (x^2 is the envelope squared exactly), bursts are
    exponentials re-struck every `period_s`, the tail an exponential that
    starts at `tail_start_s` after the onset -- deliberately just past the
    30 ms split, so a split or rate error moves tail energy across it. Time
    is measured from the ONSET, i.e. the first nonzero sample; the scorer's
    t = 0 is `TRIM_MS` before it."""
    n = int(round(0.6 * sr))
    lead = int(round(lead_s * sr))
    env = np.zeros(n)
    rb, rt = math.exp(-1 / (b_tau * sr)), math.exp(-1 / (t_tau * sr))
    per, ts = int(round(period_s * sr)), int(round(tail_start_s * sr))
    for k in range(n_bursts):
        s = lead + k * per
        e = s + per
        env[s:e] = a * rb ** np.arange(e - s)
    if ts < n_bursts * per:
        # The closed form sums burst and tail energies separately, which is only
        # true when they do not overlap. The first version overlapped them in
        # the 4-burst case and was 4.7 dB "wrong" -- the generator's fault.
        raise ValueError("synthetic tail overlaps the burst train; the closed form would not hold")
    env[lead + ts:] = b * rt ** np.arange(n - lead - ts)
    sign = np.where(np.arange(n) % 2 == 0, 1.0, -1.0)
    return sign * env, dict(sr=sr, a=a, b=b, rb=rb, rt=rt, per=per, ts=ts, lead=lead,
                            n_bursts=n_bursts, n=n)


def _geom(r2: float, i0: int, i1: int) -> float:
    """sum_{i=i0}^{i1-1} r2^i, closed form."""
    if i1 <= i0:
        return 0.0
    return (r2 ** i0 - r2 ** i1) / (1 - r2)


def synthetic_expected_db(q: dict, split_s=SPLIT_S, end_s=END_S) -> float:
    """The ratio the scorer SHOULD report, from the construction alone: window
    [0, split) and [split, end) in samples relative to (onset - TRIM_MS), per
    `run_case.window`'s documented convention, energies as geometric sums."""
    sr = q["sr"]
    trim = int(round(rc.TRIM_MS * 1e-3 * sr))
    w = lambda t: int(t * sr) - trim            # window edge as a sample offset from the onset

    def energy(lo, hi):
        e = 0.0
        for k in range(q["n_bursts"]):
            s = k * q["per"]
            a0, a1 = max(lo, s), min(hi, s + q["per"])
            e += q["a"] ** 2 * _geom(q["rb"] ** 2, a0 - s, a1 - s)
        a0, a1 = max(lo, q["ts"]), hi
        e += q["b"] ** 2 * _geom(q["rt"] ** 2, a0 - q["ts"], a1 - q["ts"])
        return e
    return 10 * math.log10(energy(w(0.0), w(split_s)) / energy(w(split_s), w(end_s)))


def _mutant_amplitude(y, sr):          # sums |x|, not x^2
    return 20 * math.log10(np.sum(np.abs(rc.window(y, sr, 0, SPLIT_S))) /
                           np.sum(np.abs(rc.window(y, sr, SPLIT_S, END_S))))


def _mutant_split40(y, sr):            # boundary at 40 ms instead of 30
    return ratio_db(y, sr, 0.040)


def _mutant_wrong_rate(y, sr):         # 48 kHz window arithmetic on every side
    return ratio_db(y, 48000)


def _mutant_onset_at_peak(y, sr):      # t = 0 at the envelope PEAK rather than the 2 % onset
    o = rc.required_lead_samples(sr)
    pk = int(np.argmax(np.abs(y)))
    yy = np.concatenate([np.zeros(o), y[pk:]])
    return ratio_db(yy, sr)


MUTANTS = {"energy->amplitude": _mutant_amplitude, "split 30->40 ms": _mutant_split40,
           "assumes 48 kHz": _mutant_wrong_rate, "onset at peak": _mutant_onset_at_peak}


def known_answer() -> dict:
    rows = []
    for sr in (44100, 48000):
        for kw in (dict(), dict(b=0.05), dict(b=0.5, t_tau=0.090), dict(n_bursts=4, period_s=0.012, tail_start_s=0.050)):
            x, q = synthetic_clap(sr, **kw)
            y = rc.prepare(x, sr, side="synthetic")
            want = synthetic_expected_db(q)
            got = ratio_db(y, sr)
            row = {"sr": sr, "params": kw, "expected_db": round(want, 5), "got_db": round(got, 5),
                   "abs_err_db": round(abs(got - want), 6),
                   "ok": bool(abs(got - want) <= KNOWN_ANSWER_TOL_DB), "mutants": {}}
            for name, f in MUTANTS.items():
                v = f(y, sr)
                row["mutants"][name] = {"got_db": round(v, 4), "abs_err_db": round(abs(v - want), 4),
                                        "caught": bool(abs(v - want) > KNOWN_ANSWER_TOL_DB)}
            rows.append(row)
    # injected defect in the SIGNAL: tail x2 must move the ratio by -6.02 dB (burst-only early window
    # because the synthetic tail starts after the split)
    x, q = synthetic_clap(48000)
    x2, q2 = synthetic_clap(48000, b=0.4)
    d = ratio_db(rc.prepare(x2, 48000), 48000) - ratio_db(rc.prepare(x, 48000), 48000)
    # Not -6.02 dB: the last burst's final millisecond lies past the split, so
    # the late window is tail + a sliver of burst. The first version asserted
    # -6.02 and failed by 0.03 dB; the closed form is the right expectation.
    want = synthetic_expected_db(q2) - synthetic_expected_db(q)
    inj = {"defect": "tail amplitude x2", "expected_shift_db": round(want, 4),
           "measured_shift_db": round(d, 4), "ok": abs(d - want) <= KNOWN_ANSWER_TOL_DB,
           "caught_by_baseline_expectation": abs(ratio_db(rc.prepare(x2, 48000), 48000)
                                                 - synthetic_expected_db(q)) > KNOWN_ANSWER_TOL_DB}
    # tail-fit known answer: exponential of known tau
    fits = []
    for sr in (44100, 48000):
        for tau in (0.047, 0.090):
            x, _ = synthetic_clap(sr, t_tau=tau, b=0.3)
            f = tail_fit(rc.prepare(x, sr), sr)
            fits.append({"sr": sr, "true_tau_ms": tau * 1e3, "fit_tau_ms": f["amp_tau_ms"],
                         "ok": bool(abs(f["amp_tau_ms"] - tau * 1e3) <= 0.01 * tau * 1e3)})
    # Mutation-testing semantics: a mutant is KILLED when at least one known-answer
    # signal catches it. ("assumes 48 kHz" is the identity on a 48 kHz signal, and
    # "onset at peak" moves t = 0 by the 1 ms trim only, so neither is caught by
    # every signal -- both must be caught by some.)
    killed = {name: any(r["mutants"][name]["caught"] for r in rows) for name in MUTANTS}
    ok = (all(r["ok"] for r in rows) and all(killed.values())
          and inj["ok"] and inj["caught_by_baseline_expectation"] and all(f["ok"] for f in fits))
    return {"tolerance_db": KNOWN_ANSWER_TOL_DB, "cases": rows, "mutants_killed": killed,
            "injected_signal_defect": inj, "tail_fit": fits, "ok": bool(ok)}


# ---------------------------------------------------------------------------
# the diagnosis
# ---------------------------------------------------------------------------
def reproduce(refdir) -> tuple:
    committed = json.loads(RECORD.read_text())
    res = rc.run_drum_case(dict(case_id="D12A", subject="Clap / anchor",
                                required_measurements="Burst timing; burst/tail ratio; decay"),
                           refdir, "", keep_audio=False)
    diffs = []
    for name, m in committed["metrics"].items():
        for k in ("value", "reference", "error", "tolerance", "valid"):
            if res["metrics"][name].get(k) != m.get(k):
                diffs.append(f"{name}.{k}: committed {m.get(k)!r} now {res['metrics'][name].get(k)!r}")
    for side in ("ours", "reference"):
        if res["windowing"][side] != committed["windowing"][side]:
            diffs.append(f"windowing.{side} differs")
    if res["diagnostics"] != committed["diagnostics"]:
        diffs.append("diagnostics differ")
    if diffs:
        raise Refused("the committed D12A record does not reproduce: " + "; ".join(diffs))
    x1, sr = rc.render_drum_solo("CP")
    x2, _ = rc.render_drum_solo("CP")
    x3, _ = render_cp(DEFAULT)
    if audio_sha(x1) != audio_sha(x2):
        raise Refused("render_drum_solo('CP') is not deterministic")
    if audio_sha(x1) != audio_sha(x3):
        raise Refused("the probe's override renderer does not reproduce render_drum_solo "
                      "at the default registers; no override render can be trusted")
    y16 = np.clip(x1 * 32768.0, -32768, 32767).astype("<i2")
    return {"fields_compared": "value/reference/error/tolerance/valid x 3 metrics, windowing, diagnostics",
            "mismatches": 0, "metrics": {k: committed["metrics"][k] for k in committed["metrics"]},
            "committed_source_commit": committed["source_commit"],
            "committed_analysis_run": committed["analysis_run"],
            "committed_audio": committed["audio"],
            "ours_float64_sha256": audio_sha(x1),
            "ours_wav16_pcm_sha256": hashlib.sha256(y16.tobytes()).hexdigest(),
            "render_repeat_identical": True, "override_renderer_bit_identical": True}, x1, sr


def diagnose(refdir, jobs: int) -> dict:
    rx, rsr, rel, _ = rc.load_reference("CP", refdir)
    ref = (rc.prepare(rx, rsr), rsr)
    ox, osr = render_cp(DEFAULT)
    ours = (rc.prepare(ox, osr), osr)
    sides = {"ours": (ox, osr, ours[0]), "reference": (rx, rsr, ref[0])}

    seg = {}
    for k, (x, sr, y) in sides.items():
        rep = rc.lead_report(x, sr)
        seg[k] = {"onset_index": rep["onset_index"], "sr": sr, "bursts": bursts(y, sr),
                  "onset_frac_sensitivity_db": {}}
    # onset threshold sensitivity of the ratio (does the boundary move with the detector?)
    for frac in (0.01, 0.02, 0.05):
        old = rc.ONSET_FRAC
        rc.ONSET_FRAC = frac
        try:
            for k, (x, sr, _) in sides.items():
                seg[k]["onset_frac_sensitivity_db"][str(frac)] = round(ratio_db(rc.prepare(x, sr), sr), 3)
        finally:
            rc.ONSET_FRAC = old
    seg["split_sweep"] = [{"split_ms": s, "ours_db": round(ratio_db(*ours, s / 1e3), 3),
                           "ref_db": round(ratio_db(*ref, s / 1e3), 3),
                           "error_db": round(ratio_db(*ours, s / 1e3) - ratio_db(*ref, s / 1e3), 3)}
                          for s in (20, 25, 30, 35, 40, 45, 50, 60, 80)]

    wins = [(0, 30), (30, 200), (0, 50), (50, 200), (80, 200), (30, 50)]
    en = {}
    for k, (x, sr, y) in sides.items():
        # Original gain = the metric's peak-normalised window scaled back by the
        # side's own peak. Indexing the raw record directly was the first
        # version, and it read BEFORE sample 0 on the reference (onset at sample
        # 8, trim 44): prepare() manufactures that lead, the raw file has none.
        g = 20 * math.log10(float(np.max(np.abs(np.asarray(x, dtype=np.float64)))))
        raw = lambda a, b: win_db(y, sr, a / 1e3, b / 1e3) + g
        en[k] = {"original_gain_db_fs2s": {f"{a}-{b}ms": round(raw(a, b), 2) for a, b in wins},
                 "peak_normalised_db": {f"{a}-{b}ms": round(win_db(y, sr, a / 1e3, b / 1e3), 2)
                                        for a, b in wins},
                 "tail_fit": tail_fit(y, sr)}
    en["difference_ours_minus_ref_peak_normalised_db"] = {
        w: round(en["ours"]["peak_normalised_db"][w] - en["reference"]["peak_normalised_db"][w], 2)
        for w in en["ours"]["peak_normalised_db"]}

    # components: burst-only and tail-only, same noise
    xb, _ = render_cp(dict(t_peak=0.0))
    xt, _ = render_cp(dict(b_peak=0.0))
    resid = float(np.max(np.abs(ox - (xb + xt))))
    if resid > 4 / 32768:
        raise Refused(f"x != x_burst + x_tail (max residual {resid*32768:.1f} LSB): the path is "
                      f"not linear in the envelopes, so the component predictions do not hold")
    pk = float(np.max(np.abs(ox)))
    o = rc._onset_index(ox)

    def comp_db(sig, a, b):
        lead = rc.required_lead_samples(osr)
        trim = int(round(rc.TRIM_MS * 1e-3 * osr))
        s = sig[o - trim + int(a * osr):o - trim + int(b * osr)] / pk
        return 10 * math.log10(float(np.sum(s ** 2)) / osr + 1e-30)
    comps = {"linearity_max_residual_lsb": round(resid * 32768, 2),
             "burst_only_db": {"0-30ms": round(comp_db(xb, 0, .03), 2), "30-200ms": round(comp_db(xb, .03, .2), 2)},
             "tail_only_db": {"0-30ms": round(comp_db(xt, 0, .03), 2), "30-200ms": round(comp_db(xt, .03, .2), 2)}}

    def predict(k_tail):
        """Ratio at tail gain k, from the component renders alone."""
        trim = int(round(rc.TRIM_MS * 1e-3 * osr))
        a, s, e = o - trim, o - trim + int(SPLIT_S * osr), o - trim + int(END_S * osr)
        z = xb + k_tail * xt
        return 10 * math.log10(np.sum(z[a:s] ** 2) / np.sum(z[s:e] ** 2))

    # sensitivity: single-field perturbations, each with all three metrics
    sweep = [("baseline", {}),
             ("tail peak x1.41 (+3 dB)", dict(t_peak=0.22 * 2 ** 0.5)),
             ("tail peak x2 (+6 dB)", dict(t_peak=0.44)),
             ("tail peak x0.5 (-6 dB)", dict(t_peak=0.11)),
             ("tail tau 47->70 ms", dict(t_tau=70e-3)),
             ("tail tau 47->90 ms", dict(t_tau=90e-3)),
             ("bursts 3 strikes->4, period 480", dict(bursts=3)),
             ("bursts 4 strikes, period 511", dict(bursts=3, period=511)),
             ("burst tau 4->8 ms", dict(b_tau=8e-3)),
             ("tail peak 0.44 + tau 90 ms", dict(t_peak=0.44, t_tau=90e-3)),
             ("4 strikes p511 + tail tau 90 ms, peak 0.33", dict(bursts=3, period=511, t_tau=90e-3, t_peak=0.33)),
             ("final burst tau 20 ms alone", dict(bursts=3, period=511, final_tau=20e-3)),
             ("final burst tau 38.5 ms alone", dict(bursts=3, period=511, final_tau=38.5e-3)),
             ]
    with ProcessPoolExecutor(max_workers=jobs) as ex:
        renders = list(ex.map(_render_job, [(p, 1.0, 0) for _, p in sweep]
                              + [(p, 2.0, 0) for _, p in sweep]))
    n = len(sweep)
    sens = []
    for i, (label, p) in enumerate(sweep):
        x, sr = renders[i]
        x2, _ = renders[n + i]
        y = rc.prepare(x, sr)
        row = {"label": label, "override": p, "metrics": metrics(x, sr, ref),
               "burst_list": bursts(y, sr),
               "peak_fs_accent1": round(float(np.max(np.abs(x))), 4),
               "peak_fs_accent2": round(float(np.max(np.abs(x2))), 4),
               "rail_samples_accent2": int(np.sum(np.abs(x2) >= 32767 / 32768)),
               "ratio_accent2_db": round(ratio_db(rc.prepare(x2, sr), sr), 3)}
        if set(p) <= {"t_peak"}:
            row["predicted_ratio_db"] = round(predict(p.get("t_peak", 0.22) / 0.22), 3)
        sens.append(row)
    # --- which part of the late window: counterfactual window swaps -------------
    # The ratio is scale-free, so "burst too loud" versus "tail too weak" needs an
    # ANCHOR; the Fischer set has no absolute level (LEVEL pinned at maximum). Two
    # anchors are reported and the conclusion is only drawn where they agree.
    pn = {k: en[k]["peak_normalised_db"] for k in ("ours", "reference")}
    lin = lambda d: 10 ** (d / 10)
    db = lambda v: 10 * math.log10(v)
    def ratio_with(late_parts):
        return pn["ours"]["0-30ms"] - db(sum(lin(v) for v in late_parts))
    base_err = ratio_db(*ours) - ratio_db(*ref)
    decomp = {
        "error_db": round(base_err, 3),
        "late_deficit_db_anchor_peak": round(pn["reference"]["30-200ms"] - pn["ours"]["30-200ms"], 2),
        "early_excess_db_anchor_peak": round(pn["ours"]["0-30ms"] - pn["reference"]["0-30ms"], 2),
        "late_deficit_db_anchor_first_30ms": round(base_err, 2),
        "early_excess_db_anchor_first_30ms": 0.0,
        "error_if_ours_had_ref_30_50ms": round(ratio_with([pn["reference"]["30-50ms"], pn["ours"]["50-200ms"]])
                                               - ratio_db(*ref), 2),
        "error_if_ours_had_ref_50_200ms": round(ratio_with([pn["ours"]["30-50ms"], pn["reference"]["50-200ms"]])
                                                - ratio_db(*ref), 2),
        "error_if_ours_had_ref_30_200ms": round(ratio_with([pn["reference"]["30-50ms"],
                                                            pn["reference"]["50-200ms"]]) - ratio_db(*ref), 2),
    }

    # --- the reference's burst train at 1 ms resolution --------------------------
    yr = ref[0]
    ms1 = lambda y, sr, t: 10 * math.log10(float(np.mean(rc.window(y, sr, t, t + 1e-3) ** 2)) + 1e-30)
    fine = {k: [round(ms1(y, sr, t / 1e3), 1) for t in range(0, 80)]
            for k, (y, sr) in (("ours", ours), ("reference", ref))}
    def slope_tau(y, sr, a, b):
        ts = np.arange(a, b) / 1e3
        sl = np.polyfit(ts, [ms1(y, sr, t) for t in ts], 1)[0]
        return round(float(-20 / (sl * math.log(10)) * 1e3), 1)
    ref_fine = {"one_ms_mean_square_db_0_80ms": fine,
                "final_burst_amp_tau_ms": {"40-55": slope_tau(yr, rsr, 40, 55),
                                           "42-60": slope_tau(yr, rsr, 42, 60),
                                           "45-65": slope_tau(yr, rsr, 45, 65)},
                "first_burst_amp_tau_ms_4_12": slope_tau(yr, rsr, 4, 12),
                "second_burst_amp_tau_ms_15_24": slope_tau(yr, rsr, 15, 24)}

    # --- the frozen candidates and the nuisance variation ------------------------
    # FROZEN before this block was run on 2026-09-25 (see
    # docs/scorecard/clap-d12a/README.md for how they were chosen, from renders
    # of development data -- this is not independent confirmation).
    offsets = [0, 7, 131, 977, 2203, 4099, 7919, 12007]
    configs = [("baseline", {})] + CANDIDATES
    with ProcessPoolExecutor(max_workers=jobs) as ex:
        rr = list(ex.map(_render_job, [(p, 1.0, o) for _, p in configs for o in offsets]
                         + [(p, 2.0, 0) for _, p in configs]
                         + [(p, 0.5, 0) for _, p in configs]))
    nuis = []
    k = 0
    for label, p in configs:
        runs = []
        for o in offsets:
            x, sr = rr[k]; k += 1
            m = metrics(x, sr, ref)
            runs.append({"offset": o, **{n: (m[n]["value"], m[n].get("pass")) for n in m}})
        nuis.append({"label": label, "override": p, "runs": runs})
    acc = []
    for label, p in configs:
        x2, sr = rr[k]; k += 1
        acc.append({"label": label, "accent2_peak_fs": round(float(np.max(np.abs(x2))), 4),
                    "accent2_rail_samples": int(np.sum(np.abs(x2) >= 32767 / 32768)),
                    "accent2_ratio_db": round(ratio_db(rc.prepare(x2, sr), sr), 3)})
    for i, (label, p) in enumerate(configs):
        x5, sr = rr[k]; k += 1
        acc[i]["accent0.5_peak_fs"] = round(float(np.max(np.abs(x5))), 4)
        acc[i]["accent0.5_ratio_db"] = round(ratio_db(rc.prepare(x5, sr), sr), 3)
    return {"reference_file": rel, "segmentation": seg, "energies": en, "components": comps,
            "decomposition": decomp, "reference_fine_structure": ref_fine,
            "sensitivity": sens, "candidates": [dict(label=l, override=p) for l, p in CANDIDATES],
            "nuisance_offsets_frames": offsets, "nuisance": nuis, "accent_headroom": acc}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refs", default=str(rc.configured_refs()))
    ap.add_argument("--manifest", default=str(MANIFEST_DEFAULT))
    ap.add_argument("--out", default=str(OUT_DIR / "probe.json"))
    ap.add_argument("--jobs", type=int, default=min(8, os.cpu_count() or 1))
    ap.add_argument("--known-answer-only", action="store_true")
    a = ap.parse_args(argv)
    ka = known_answer()
    print(f"known answer: {'PASS' if ka['ok'] else 'FAIL'} "
          f"({len(ka['cases'])} signals, {sum(len(r['mutants']) for r in ka['cases'])} mutant runs)")
    if a.known_answer_only:
        return 0 if ka["ok"] else 1
    if not ka["ok"]:
        print("REFUSED: the burst/tail estimator failed its known-answer control")
        return 2
    try:
        refdir = pathlib.Path(a.refs)
        out = {"probe": "tools/clap_d12a_probe.py", "basis": basis(refdir, pathlib.Path(a.manifest)),
               "known_answer": ka}
        out["reproduction"], _, _ = reproduce(refdir)
        out.update(diagnose(refdir, a.jobs))
    except (Refused, rc.Refused) as e:
        print(f"REFUSED: {e}")
        return 2
    p = pathlib.Path(a.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=1, sort_keys=False) + "\n")
    print(f"reproduced D12A: {out['reproduction']['metrics']['burst/tail ratio']['error']} dB error; "
          f"ours sha256 {out['reproduction']['ours_float64_sha256'][:16]}")
    for r in out["sensitivity"]:
        m = r["metrics"]
        print(f"{r['label']:38s} ratio {m['burst/tail ratio']['value']:7.2f} "
              f"(err {m['burst/tail ratio']['error']:+6.2f}) pred {r.get('predicted_ratio_db', '')!s:8s} "
              f"span {m['Burst timing']['value']:6.2f} ({'ok' if m['Burst timing'].get('pass') else 'FAIL'}) "
              f"T20 {m['decay']['value']} ({'ok' if m['decay'].get('pass') else 'FAIL'}) "
              f"pk2 {r['peak_fs_accent2']} rail {r['rail_samples_accent2']}")
    print("decomposition:", out["decomposition"])
    for c in out["nuisance"]:
        v = {n: [r[n] for r in c["runs"]] for n in ("Burst timing", "burst/tail ratio", "decay")}
        print(f"{c['label']:38s} " + "  ".join(
            f"{n[:10]} {min(x for x, _ in v[n]):.2f}..{max(x for x, _ in v[n]):.2f} "
            f"pass {sum(bool(ok) for _, ok in v[n])}/{len(v[n])}" for n in v))
    for a_ in out["accent_headroom"]:
        print(a_)
    print(f"wrote {p.relative_to(ROOT) if p.is_relative_to(ROOT) else p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
