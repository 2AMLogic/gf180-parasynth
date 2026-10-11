"""#593: do the 808 toms carry H2..H4 that ours lack, and what makes them?

This is the QUALIFICATION INSTRUMENT, not a repair. It changes no model file.
Pieces, in the order they must be trusted:

  1. synth_mixture / harness: analytic decaying-sinusoid mixtures whose H2..H4
     are specified INDEPENDENTLY of the estimator (and of our model).
  2. the harmonic estimator, run through that same harness.
  3. the frozen judge, driven by a NEW preregistration (docs/scorecard/
     tom-distortion-593/prereg.json), not by the #558 one.
  4. start-red and injected-defect controls through the verdict path.

START RED (docs/verification-rules.md rule 1): this module first existed with
only the stub estimator below. `harness_verdict(stub)` is the recorded red run.

CLI exit codes: 0 = the asked-for verdict is good (harness PASS, controls OK,
judge CONFIRMED or NO CANDIDATE); 1 = completed and negative (harness FAIL,
controls FAIL, judge NOT CONFIRMED); 2 = REFUSED -- the apparatus cannot
answer (a Refused exception, a controls baseline that is itself red, or a
judge REFUSED verdict). Refusal is never reported as success.
"""
from __future__ import annotations

import math
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
PREREG = ROOT / "docs" / "scorecard" / "tom-distortion-593" / "prereg.json"


class Refused(RuntimeError):
    """The apparatus cannot answer. Distinct from pass and from fail."""


#: Injected defects (rule 5). Each must turn a named property red.
DEFECTS = ("stub-estimator", "missing-distortion", "leakage-as-h2",
           "preservation-bypass", "contaminated-confirmation", "target-ignored",
           "mechanism-bypass", "population-bypass", "nonfinite-bypass")
INJECT: set = set()


# ---------------------------------------------------------------------------
# 1. closed-form ground truth
# ---------------------------------------------------------------------------
def synth_mixture(f0: float, tau: float, h_db=(-35.0, -38.0, -41.0), *, sr: int = 48000,
                  seconds: float = 0.5, glide_cents: float = 0.0, glide_tau: float = 0.03,
                  noise_db: float | None = None, h_tau_ratio=1.0, seed: int = 0) -> np.ndarray:
    """sum_k a_k exp(-t/(tau*r_k)) sin(k phi(t) + p_k), a_1 = 1, a_k = 10^(h_db[k-2]/20).
    phi' = 2 pi f(t), f(t) = f0 * 2^(glide_cents/1200 * exp(-t/glide_tau)) (a fall to f0).
    `h_tau_ratio` is a scalar or a per-harmonic tuple (2..4): 1.0 keeps H_k/H_1
    constant in time (an intrinsic waveform), 1/(k) models a polynomial of a
    decaying carrier. `noise_db` is white-noise rms in dB re the H1 amplitude."""
    t = np.arange(int(round(seconds * sr))) / sr
    f = f0 * 2.0 ** (glide_cents / 1200.0 * np.exp(-t / glide_tau))
    phi = 2 * np.pi * np.cumsum(f) / sr
    phi = phi - phi[0]
    rng = np.random.default_rng(seed)
    ph = rng.uniform(0, 2 * np.pi, 4)
    r = (list(h_tau_ratio) if hasattr(h_tau_ratio, "__len__") else [h_tau_ratio] * len(h_db))
    y = np.exp(-t / tau) * np.sin(phi + ph[0])
    for k, (db, rk) in enumerate(zip(h_db, r), start=2):
        y += 10 ** (db / 20) * np.exp(-t / (tau * rk)) * np.sin(k * phi + ph[k - 1])
    if noise_db is not None:
        y = y + rng.standard_normal(len(t)) * 10 ** (noise_db / 20)
    return y


def apply_quadratic(y: np.ndarray, h2_db: float) -> np.ndarray:
    """The candidate distortion under test: y + c*y^2, with c chosen so H2 sits
    at `h2_db` re H1 at the strike (peak amplitude A): H2/H1 = c*A/2."""
    if "missing-distortion" in INJECT:
        return np.array(y, copy=True)
    A = float(np.max(np.abs(y)))
    c = 2 * 10 ** (h2_db / 20) / A
    return y + c * y * y


# ---------------------------------------------------------------------------
# 2. estimator: START RED -- the stub has the right ports and no behaviour
# ---------------------------------------------------------------------------
def stub_levels(y, sr, onset=0, **kw) -> dict:
    """Right ports, no behaviour: reports a perfectly clean signal."""
    return {"f0": 0.0, "db": [-120.0, -120.0, -120.0], "resolved": [False] * 3,
            "floor_db": [-120.0] * 3}


SUPPORTED_RATES = (44100, 48000)
SILENT = 1e-6                 # absolute peak below which the input is silent
F_RANGE = (40.0, 600.0)       # fundamental search range, Hz
BAND = 0.05                   # +-5 % about k*f0
LOC_TOL = 0.03                # a harmonic's peak must sit within 3 % of k*f0
NFFT = 1 << 17


def _bh(n):
    from scipy.signal.windows import blackmanharris
    return blackmanharris(n, sym=False)


def _band_peak(S, f, lo, hi):
    m = (f >= lo) & (f <= hi)
    if not m.any():
        return 0.0, 0.0
    i = int(np.argmax(np.where(m, S, 0.0)))
    return float(S[i]), float(f[i])


def _window_spectrum(y, sr, a, b, rect=False):
    seg = y[a:b]
    w = np.ones(len(seg)) if rect else _bh(len(seg))
    return np.abs(np.fft.rfft(seg * w, NFFT)), np.fft.rfftfreq(NFFT, 1 / sr)


def _check_input(y, sr):
    if sr not in SUPPORTED_RATES:
        raise Refused(f"unsupported sample rate {sr}")
    y = np.asarray(y, dtype=np.float64)
    if y.ndim != 1 or y.size == 0:
        raise Refused("empty or non-1-D input")
    if not np.all(np.isfinite(y)):
        raise Refused("non-finite samples")
    if float(np.max(np.abs(y))) < SILENT:
        raise Refused("silent input")
    return y


def fit_tau(y, sr, f0, a, b) -> float:
    """Envelope time constant of the fundamental over [a, b): log-linear fit of
    the analytic envelope of a band-pass about f0."""
    from scipy.signal import butter, hilbert, sosfiltfilt
    sos = butter(4, [0.8 * f0 / (sr / 2), 1.2 * f0 / (sr / 2)], btype="bandpass", output="sos")
    env = np.abs(hilbert(sosfiltfilt(sos, y)))[a:b]
    t = np.arange(len(env)) / sr
    m = env > env.max() * 1e-4
    if m.sum() < 16:
        raise Refused("fundamental envelope not resolvable for a decay fit")
    slope = np.polyfit(t[m], np.log(env[m]), 1)[0]
    if slope >= -0.5:
        raise Refused(f"fundamental does not decay over the window (slope {slope:.2f}/s)")
    return float(-1.0 / slope)


GLIDE_MAX_CENTS = 60.0        # validated domain: fundamental excursion inside the window


def glide_span_cents(y, sr, f0, a, b) -> float:
    """5-95 percentile spread of the fundamental's smoothed instantaneous
    frequency over [a, b), in cents (analytic phase of a band-pass about f0)."""
    from scipy.signal import butter, hilbert, sosfiltfilt
    sos = butter(4, [0.8 * f0 / (sr / 2), 1.2 * f0 / (sr / 2)], btype="bandpass", output="sos")
    z = hilbert(sosfiltfilt(sos, y))
    inst = np.diff(np.unwrap(np.angle(z))) * sr / (2 * np.pi)
    m = max(int(0.02 * sr), 1)
    sm = np.convolve(inst, np.ones(m) / m, mode="valid")
    seg = sm[max(a - m, 0):max(b - m, 1)]
    seg = seg[len(seg) // 20: len(seg) - len(seg) // 20]
    if len(seg) < 8 or np.any(seg <= 0):
        raise Refused("fundamental frequency track not resolvable")
    lo, hi = np.percentile(seg, [5, 95])
    return float(1200 * math.log2(hi / lo))


def harmonic_levels(y, sr, onset: int = 0, win=(0.02, 0.30), _rect=False) -> dict:
    """H2..H4 in dB re H1 over [onset+win0, onset+win1), with, per harmonic, its
    own FLOOR (the larger of the apparent level the same window reads on a pure
    sinusoid with this f0 and decay, and the larger neighbouring off-harmonic
    band) and `resolved` = clears the floor by RESOLVE_MARGIN_DB and sits at k*f0.
    REFUSES on non-finite, silent, short, unsupported-rate or unresolved-
    fundamental input. Validated domain: docs/scorecard/tom-distortion-593/."""
    y = _check_input(y, sr)
    a, b = onset + int(round(win[0] * sr)), onset + int(round(win[1] * sr))
    if b > len(y):
        raise Refused(f"window ends at {b} but the signal has {len(y)} samples")
    if b - a < int(0.1 * sr):
        raise Refused("measurement window shorter than 100 ms")
    S, f = _window_spectrum(y, sr, a, b, _rect)
    lo, hi = F_RANGE
    i0 = int(np.argmax(np.where((f > lo) & (f < hi), S, 0.0)))
    f0 = float(f[i0])
    if f0 < lo * 1.1 or f0 > hi * 0.9:
        raise Refused(f"fundamental {f0:.1f} Hz at the edge of the search range")
    if 4 * f0 * (1 + BAND) > 0.45 * sr:
        raise Refused(f"H4 of {f0:.0f} Hz is too close to Nyquist")
    inband = S[(f > lo) & (f < hi)]
    if S[i0] < 30.0 * float(np.median(inband)):
        raise Refused("fundamental not resolved above the spectral median")
    tau = fit_tau(y, sr, f0, a, b)
    gl = glide_span_cents(y, sr, f0, a, b)
    if gl > GLIDE_MAX_CENTS:
        raise Refused(f"fundamental moves {gl:.0f} cents inside the window "
                      f"(validated domain <= {GLIDE_MAX_CENTS:.0f}): H2..H4 would be smeared")
    n = np.arange(len(y)) / sr
    null = np.exp(-n / tau) * np.sin(2 * np.pi * f0 * n)
    Sn, _ = _window_spectrum(null, sr, a, b, _rect)
    h1, _ = _band_peak(S, f, f0 * (1 - BAND), f0 * (1 + BAND))
    h1n, _ = _band_peak(Sn, f, f0 * (1 - BAND), f0 * (1 + BAND))
    db, floor, ok = [], [], []
    for k in (2, 3, 4):
        pk, fk = _band_peak(S, f, k * f0 * (1 - BAND), k * f0 * (1 + BAND))
        nk, _ = _band_peak(Sn, f, k * f0 * (1 - BAND), k * f0 * (1 + BAND))
        off = max(_band_peak(S, f, (k + d) * f0 * (1 - BAND), (k + d) * f0 * (1 + BAND))[0]
                  for d in (-0.5, 0.5))
        lvl = 20 * math.log10(max(pk, 1e-300) / h1)
        fl = 20 * math.log10(max(nk / h1n, off / h1, 1e-300))
        db.append(lvl)
        floor.append(fl)
        ok.append(bool(pk > 0 and abs(fk / (k * f0) - 1) <= LOC_TOL and lvl >= fl + RESOLVE_MARGIN_DB))
    if "leakage-as-h2" in INJECT:                  # the floor guard removed
        ok = [True] * 3
    return {"f0": f0, "tau": tau, "glide_cents": gl, "db": db, "resolved": ok, "floor_db": floor}


def estimate(y, sr, onset: int = 0, **kw) -> dict:
    """The acceptance estimator (what the harness, the properties and the takes use)."""
    if "stub-estimator" in INJECT:
        return stub_levels(y, sr, onset)
    return harmonic_levels(y, sr, onset, **kw)


ESTIMATOR = estimate


# ---------------------------------------------------------------------------
# the known-answer harness (the same estimator that is later used on takes)
# ---------------------------------------------------------------------------
#: Tolerances are FROZEN from the grid below (see docs/scorecard/tom-distortion-593/
#: knownanswer.json) and may not be edited after a candidate has been measured.
TOL_DB = 2.5                  # observed max 2.09 dB on the grid (glide 60 cents x H4 at ~20 dB SNR) + margin
RESOLVE_MARGIN_DB = 6.0       # a harmonic must clear its own floor by this much
SPEC_FLOOR_DB = -60.0         # harnessed range: harmonics specified at or above this


def ka_grid() -> list:
    """Independent specifications across the tom range (f0 70..330 Hz, tau
    60..300 ms, both sample rates), with and without glide and noise."""
    cases = []
    for sr in (44100, 48000):
        for f0 in (72.0, 110.0, 165.0, 247.0, 330.0):
            for tau in (0.06, 0.15, 0.30):
                for h_db in ((-33.0, -37.0, -41.0), (-45.0, -50.0, -55.0), (-58.0, -60.0, -60.0)):
                    for glide, gtau in ((0.0, 0.03), (60.0, 0.03), (60.0, 0.1)):
                        for noise in (None, -60.0, -50.0):
                            cases.append(dict(f0=f0, tau=tau, h_db=h_db, sr=sr, glide_cents=glide,
                                              glide_tau=gtau, noise_db=noise))
    return cases


SNR_REQ_DB = 20.0             # harnessed domain: harmonic's analytic SNR in the window


def analytic_snr_db(c: dict, k: int) -> float:
    """Closed-form SNR of the specified harmonic k (index into h_db) against the
    case's white noise in the measurement window: tone peak |sum w e^{-t/tau}|/2
    over noise sigma * sqrt(sum w^2). No estimator is involved."""
    if c["noise_db"] is None:
        return math.inf
    sr = c["sr"]
    a, b = int(round(0.02 * sr)), int(round(0.30 * sr))
    n = np.arange(a, b)
    w = _bh(b - a)
    tone = 10 ** (c["h_db"][k] / 20) * abs(np.sum(w * np.exp(-n / sr / c["tau"]))) / 2
    return 20 * math.log10(tone / (10 ** (c["noise_db"] / 20) * math.sqrt(np.sum(w * w))))


def _run(estimator, y, sr, onset=0):
    return estimator(y, sr, onset=onset)


def harness_verdict(estimator, grid=None) -> dict:
    """Run the estimator over the analytic grid. PASS needs: every specified
    harmonic >= SPEC_FLOOR_DB is read as RESOLVED within TOL_DB; every absent
    harmonic (pure decaying sinusoids) is NOT resolved; every refusal case
    raises Refused. Anything else is FAIL, and a Refused where a number was
    owed is counted as REFUSED-WRONGLY, not skipped."""
    grid = ka_grid() if grid is None else grid
    errs, missed, wrong_refusal = [], 0, 0
    for c in grid:
        y = synth_mixture(c["f0"], c["tau"], c["h_db"], sr=c["sr"], glide_cents=c["glide_cents"],
                          glide_tau=c["glide_tau"], noise_db=c["noise_db"])
        try:
            r = _run(estimator, y, c["sr"])
        except Refused:
            wrong_refusal += 1
            continue
        for k, want in enumerate(c["h_db"]):
            if want < SPEC_FLOOR_DB or analytic_snr_db(c, k) < SNR_REQ_DB:
                continue
            if not r["resolved"][k]:
                missed += 1
            else:
                errs.append(r["db"][k] - want)
    false_resolved = 0
    for sr in (44100, 48000):
        for f0 in (72.0, 165.0, 330.0):
            for tau in (0.06, 0.15, 0.30):
                for glide in (0.0, 60.0):
                    for noise in (None, -50.0):
                        y = synth_mixture(f0, tau, (), sr=sr, glide_cents=glide, noise_db=noise)
                        try:
                            r = _run(estimator, y, sr)
                        except Refused:
                            continue
                        false_resolved += sum(bool(x) for x in r["resolved"])
    refusal_ok, refusal_bad = refusal_cases(estimator)
    ea = np.abs(np.array(errs)) if errs else np.array([math.inf])
    out = {"n_cases": len(grid), "n_read": len(errs), "missed_resolved": missed,
           "refused_wrongly": wrong_refusal, "false_resolved_on_absent": false_resolved,
           "max_abs_err_db": float(ea.max()), "p95_abs_err_db": float(np.percentile(ea, 95)) if errs else math.inf,
           "refusal_cases_ok": refusal_ok, "refusal_cases_bad": refusal_bad}
    out["pass"] = bool(errs and missed == 0 and wrong_refusal == 0 and false_resolved == 0
                       and ea.max() <= TOL_DB and not refusal_bad)
    return out


def refusal_cases(estimator):
    """Inputs the estimator must REFUSE (the dangerous answer is a number)."""
    sr = 48000
    good = synth_mixture(110.0, 0.15, (-35.0, -38.0, -41.0), sr=sr)
    bad = {
        "nan": np.where(np.arange(len(good)) == 5000, np.nan, good),
        "inf": np.where(np.arange(len(good)) == 5000, np.inf, good),
        "silent": np.zeros_like(good),
        "below_silence_floor": good * 1e-9,
        "short_window": good[: int(0.12 * sr)],
        "f0_too_high_for_h4": synth_mixture(11000.0, 0.15, (-35.0, -38.0, -41.0), sr=sr),
        "glide_outside_domain": synth_mixture(110.0, 0.15, (-35.0, -38.0, -41.0), sr=sr,
                                              glide_cents=480.0, glide_tau=0.1),
        "unsupported_rate": good,
        "empty": np.zeros(0),
    }
    ok, badl = [], []
    for name, y in bad.items():
        try:
            _run(estimator, y, 8000 if name == "unsupported_rate" else sr)
            badl.append(name)
        except Refused:
            ok.append(name)
        except Exception:                        # an execution error is NO VERDICT, not a refusal
            badl.append(name + " (raised a non-Refused error)")
    return ok, badl


# ---------------------------------------------------------------------------
# mechanism: does H_k re H1 fall with time like a POLYNOMIAL OF THE DECAYING
# CARRIER (a nonlinearity after the resonator), or stay constant (harmonics
# inside the waveform)?  y + c*y^2 puts H2 on exp(-2t/tau): H2/H1 falls
# 8.686*(t/tau) dB per second of window separation; a constant-ratio mixture
# does not move.  Known answers for both are synth_mixture(h_tau_ratio=...).
# ---------------------------------------------------------------------------
TRAJ_EARLY = (0.02, 0.14)
TRAJ_LATE = (0.18, 0.30)
TRAJ_DT = ((TRAJ_LATE[0] + TRAJ_LATE[1]) - (TRAJ_EARLY[0] + TRAJ_EARLY[1])) / 2
MIN_PRED_DB = 4.0           # below this predicted fall the two classes cannot be told apart
POLY_BAND = (0.6, 1.4)      # observed/predicted fall ratio for "polynomial of the carrier"
CONST_BAND = 0.25           # |observed/predicted| below this is "constant ratio"


def harmonic_trajectory(y, sr, onset: int = 0) -> dict:
    e = harmonic_levels(y, sr, onset, TRAJ_EARLY)
    l = harmonic_levels(y, sr, onset, TRAJ_LATE)
    pred = -8.686 * TRAJ_DT / e["tau"]          # dB for k=2 (order k-1 = 1)
    out = {"tau": e["tau"], "predicted_fall_db_k2": pred, "early": e, "late": l}
    if not (e["resolved"][0] and l["resolved"][0]):
        out["class"] = "insufficient"
        out["why"] = "H2 not resolved in both windows"
    elif abs(pred) < MIN_PRED_DB:
        out["class"] = "insufficient"
        out["why"] = f"predicted fall {pred:.1f} dB < {MIN_PRED_DB} dB: classes indistinguishable"
    else:
        s = (l["db"][0] - e["db"][0]) / pred
        out["observed_over_predicted"] = s
        out["class"] = ("polynomial-of-carrier" if POLY_BAND[0] <= s <= POLY_BAND[1] else
                        "constant-ratio" if abs(s) <= CONST_BAND else "indeterminate")
    if "mechanism-bypass" in INJECT:               # a classifier that always blames the output term
        out["class"] = "polynomial-of-carrier"
    return out


def mechanism_verdict(trajs: list, effect_real: bool | None, min_takes: int = 4,
                      frac: float = 0.75) -> dict:
    """Support / refutation / insufficient evidence for 'a polynomial after the
    resonator' as the source of the 808's H2..H4. Reported SEPARATELY from any
    feature improvement. `effect_real` = the recordings' harmonics clear their
    own null floor (leakage refuted); None = not measured -> INSUFFICIENT."""
    cls = [t["class"] for t in trajs]
    n_dec = sum(c != "insufficient" for c in cls)
    out = {"n_takes": len(cls), "n_decidable": n_dec, "effect_real": effect_real,
           "counts": {c: cls.count(c) for c in sorted(set(cls))}}
    if effect_real is None:
        out["verdict"], out["why"] = "INSUFFICIENT", "effect (H2 above leakage floor) not measured"
    elif effect_real is False:
        out["verdict"], out["why"] = "REFUTED", ("the recordings' H2..H4 do not clear the leakage floor: "
                                                 "there is no distortion to explain")
    elif n_dec < min_takes:
        out["verdict"], out["why"] = "INSUFFICIENT", f"only {n_dec} decidable takes (< {min_takes})"
    elif cls.count("polynomial-of-carrier") >= frac * n_dec:
        out["verdict"], out["why"] = "SUPPORTED", "H2 falls with the carrier's squared envelope"
    elif cls.count("constant-ratio") >= frac * n_dec:
        out["verdict"], out["why"] = "REFUTED", ("H2/H1 is constant in time: harmonics are inside the "
                                                  "waveform, not made by a post-resonator polynomial")
    else:
        out["verdict"], out["why"] = "INSUFFICIENT", "mixed or indeterminate trajectories"
    return out


# ---------------------------------------------------------------------------
# 3. preregistration reader and the frozen judge
# ---------------------------------------------------------------------------
def load_prereg(path: pathlib.Path = PREREG) -> dict:
    import json
    if not path.exists():
        raise Refused(f"preregistration missing: {path}")
    p = json.loads(path.read_text())
    for k in ("issue", "frozen", "corpus", "prior_seen", "development", "preservation_only",
              "confirmation", "features", "targets", "candidates", "rules"):
        if k not in p:
            raise Refused(f"preregistration lacks '{k}'")
    if p["issue"] != 593:
        raise Refused("preregistration is not #593's (the #558 split may not be reused)")
    return p


def _harm_pair(base: dict, cand: dict, target: dict | None):
    """(shipped error, candidate error) in dB, mean over harmonics where the 808
    target is RESOLVED, else (None, None). Absence is never zero error.
    A shipped harmonic that is unresolved is bounded by its floor (an upper bound
    on its level), usable only if that floor is below the target; a candidate
    harmonic that is unresolved gets NO credit (it is charged the shipped error).
    Both bounds understate the improvement, never overstate it."""
    if not target or not base.get("harm") or not cand.get("harm"):
        return None, None
    eb, ec = [], []
    for i in range(3):
        if not target["resolved"][i]:
            continue
        t = target["db"][i]
        hb, hc = base["harm"], cand["harm"]
        lb = hb["db"][i] if hb["resolved"][i] else hb.get("floor_db", [math.inf] * 3)[i]
        if not hb["resolved"][i] and not lb < t:
            continue                                   # cannot tell shipped from the target
        e0 = abs(lb - t)
        e1 = abs(hc["db"][i] - t) if hc["resolved"][i] else e0
        eb.append(e0)
        ec.append(e1)
    if not eb:
        return None, None
    return sum(eb) / len(eb), sum(ec) / len(ec)


def _violations(base: dict, cand: dict, rule: dict, features, targets) -> list:
    out = []
    if "preservation-bypass" in INJECT:
        return out
    for f in features:
        if f in targets:
            continue
        rb, rc_ = base["ratio"].get(f), cand["ratio"].get(f)
        if rb is None and rc_ is None:
            # an explicit null on both sides is not a pass: preservation of this
            # feature cannot be verified on this row, so the row cannot confirm.
            out.append(f"{f} null-on-both-sides: preservation unverifiable")
            continue
        if rb is None or rc_ is None:
            out.append(f"{f} null-mismatch {rb}->{rc_}")
        elif rc_ > rb * rule["preserve_rel"] + rule["preserve_abs"]:
            out.append(f"{f} {rb:.2f}->{rc_:.2f}")
    dp = 20 * math.log10(cand["peak_fs"] / base["peak_fs"])
    if abs(dp) > rule["peak_db"]:
        out.append(f"peak {dp:+.2f} dB")
    if abs(cand["dc_fs"]) > abs(base["dc_fs"]) + rule["dc_abs_fs"]:
        out.append(f"dc {base['dc_fs']:+.4f}->{cand['dc_fs']:+.4f} FS")
    return out


def _finite(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _validate_variant(k: str, name: str, v: dict, feats) -> None:
    """Schema and finiteness, BEFORE any scoring. A missing field is not a
    measured null, and a NaN would silently defeat every ordered comparison
    below (NaN > x is false): refuse what cannot be scored."""
    for key in ("ratio", "peak_fs", "dc_fs", "harm"):
        if key not in v:
            raise Refused(f"row {k} variant '{name}' lacks '{key}'")
    ratio = v["ratio"]
    if not isinstance(ratio, dict):
        raise Refused(f"row {k} variant '{name}': ratio is not a mapping")
    for f in feats:
        if f not in ratio:
            raise Refused(f"row {k} variant '{name}': ratio lacks registered feature '{f}' "
                          "(a missing field is not a measured null)")
        if ratio[f] is not None and not _finite(ratio[f]):
            raise Refused(f"row {k} variant '{name}': ratio['{f}'] = {ratio[f]!r} is not finite")
    pk, dc = v["peak_fs"], v["dc_fs"]
    if not _finite(pk) or pk <= 0:
        raise Refused(f"row {k} variant '{name}': peak_fs = {pk!r} is not a positive finite peak")
    if not _finite(dc):
        raise Refused(f"row {k} variant '{name}': dc_fs = {dc!r} is not finite")
    h = v["harm"]
    for key in ("db", "resolved"):
        if not isinstance(h.get(key), list) or len(h[key]) != 3:
            raise Refused(f"row {k} variant '{name}': harm['{key}'] is not a 3-vector")
    for i in range(3):
        if h["resolved"][i] and not _finite(h["db"][i]):
            raise Refused(f"row {k} variant '{name}': resolved harm[{i}] = {h['db'][i]!r} is not finite")
    for i, x in enumerate(h.get("floor_db") or ()):
        if not _finite(x):
            raise Refused(f"row {k} variant '{name}': floor_db[{i}] = {x!r} is not finite")


def _validate_target(k: str, t) -> None:
    if t is None:
        return
    if not isinstance(t, dict):
        raise Refused(f"row {k}: target_harm is not a mapping")
    for key in ("db", "resolved"):
        if not isinstance(t.get(key), list) or len(t[key]) != 3:
            raise Refused(f"row {k}: target_harm['{key}'] is not a 3-vector")
    for i in range(3):
        if t["resolved"][i] and not _finite(t["db"][i]):
            raise Refused(f"row {k}: resolved target harmonic {i} is not finite")


def _stage(rows: dict, keys, name: str, prereg: dict) -> dict:
    import statistics
    rule, feats, targets = prereg["rules"], prereg["features"], prereg["targets"]
    hred, mimp, viol, nulls = [], [], {}, {"harm": 0, "modulation": 0}
    for k in keys:
        r = rows[k]
        if "shipped" not in r["variants"] or name not in r["variants"]:
            raise Refused(f"row {k} lacks variant '{name}' or 'shipped'")
        b, c = r["variants"]["shipped"], r["variants"][name]
        if "nonfinite-bypass" not in INJECT:
            _validate_target(k, r.get("target_harm"))
            _validate_variant(k, "shipped", b, feats)
            _validate_variant(k, name, c, feats)
        v = _violations(b, c, rule, feats, targets)
        if v:
            viol[k] = v
        if r["split"] == "preservation-only":
            continue
        eb, ec = _harm_pair(b, c, r.get("target_harm"))
        if "target-ignored" in INJECT:
            hred.append(1.0)
        elif eb is None or ec is None or eb <= 0:
            nulls["harm"] += 1
        else:
            hred.append(1 - ec / eb)
        mb, mc = b["ratio"].get("modulation"), c["ratio"].get("modulation")
        if mb is None or mc is None:
            nulls["modulation"] += 1
        else:
            mimp.append(mc < mb)
    return {"n_rows": len(keys), "violations": viol, "null_excluded": nulls,
            "n_harm": len(hred), "harm_error_reduction_median": statistics.median(hred) if hred else None,
            "harm_improved_frac": sum(x > 0 for x in hred) / len(hred) if hred else None,
            "n_modulation": len(mimp), "modulation_improved_frac": sum(mimp) / len(mimp) if mimp else None}


def _meets(s: dict, rule: dict, med: float, frac: float) -> bool:
    return (not s["violations"] and s["n_harm"] >= rule["min_rows"]
            and s["harm_error_reduction_median"] >= med and s["harm_improved_frac"] >= frac
            and s["n_modulation"] >= rule["min_rows"] and s["modulation_improved_frac"] >= frac)


def judge(record: dict, prereg: dict) -> dict:
    """The frozen verdict path. Verdicts: CONFIRMED, NOT CONFIRMED, NO CANDIDATE,
    REFUSED (the apparatus cannot answer). REFUSED is never softened to NOT
    CONFIRMED and never to CONFIRMED."""
    rule = prereg["rules"]
    rows = record.get("rows") or {}
    if not rows:
        raise Refused("record has no rows")
    dev_keys = list(prereg["development"]["keys"])
    pres_keys = list(prereg["preservation_only"]["keys"])
    conf_keys = list(prereg["confirmation"]["keys"])
    if "population-bypass" in INJECT:
        # the defeated guard: populations taken from the submitter's own labels
        dev = [k for k, r in rows.items() if r.get("split") in ("development", "preservation-only")]
        conf = [k for k, r in rows.items() if r.get("split") == "confirmation"]
    else:
        # frozen population: which row is which split is the PREREGISTRATION's
        # decision, not the submitter's. Every registered row must be present
        # with exactly its frozen split, and nothing else may be scored.
        frozen = {k: "development" for k in dev_keys}
        frozen.update({k: "preservation-only" for k in pres_keys})
        frozen.update({k: "confirmation" for k in conf_keys})
        for k, r in rows.items():
            want = frozen.get(k)
            if want is None:
                res = {"verdict": "REFUSED",
                       "why": (f"row {k} is not in the frozen population: "
                               "which rows exist, and their splits, are not the submitter's to choose")}
                return res
            if r.get("split") != want:
                res = {"verdict": "REFUSED",
                       "why": f"row {k}: split {r.get('split')!r} contradicts the frozen assignment {want!r}"}
                return res
        missing = [k for k in frozen if k not in rows]
        if missing:
            res = {"verdict": "REFUSED",
                   "why": (f"frozen population incomplete: {len(missing)} registered row(s) absent "
                           f"(e.g. {sorted(missing)[0]}): a shrunken population cannot be audited")}
            return res
        reg = prereg["confirmation"].get("sha256_16") or {}
        if conf_keys:
            unreg = [k for k in conf_keys if not reg.get(k)]
            if unreg:
                res = {"verdict": "REFUSED",
                       "why": (f"confirmation registration unusable: {unreg[0]} has no registered hash; "
                               "confirmation cannot run")}
                return res
            for k in conf_keys:
                if rows[k].get("sha16") != reg[k]:
                    res = {"verdict": "REFUSED",
                           "why": (f"confirmation row {k}: hash {rows[k].get('sha16')!r} is not the "
                                   f"registered {reg[k]!r}: an unregistered file cannot confirm")}
                    return res
            shas = [reg[k] for k in conf_keys]
            if len(set(shas)) != len(shas):
                res = {"verdict": "REFUSED",
                       "why": ("one recording registered under several confirmation keys: "
                               "not independent confirmation")}
                return res
        dev = dev_keys + pres_keys
        conf = conf_keys
    res = {"mechanism": record.get("mechanism", {"verdict": "INSUFFICIENT", "why": "not supplied"}),
           "selection": {}, "confirmation": None, "chosen": None}
    # contamination guard: it ships with the input that defeats it (a row relabelled
    # 'confirmation' that is a development key, a prior-seen key or a prior-seen file)
    seen_keys = set(prereg["prior_seen"]["keys"]) | set(prereg["development"]["keys"])
    seen_sha = set(prereg["prior_seen"]["sha256_16"].values())
    seen_sha |= {r["sha16"] for r in rows.values() if r["split"] != "confirmation" and r.get("sha16")}
    if "contaminated-confirmation" not in INJECT:
        for k in conf:
            if k in seen_keys or rows[k].get("sha16") in seen_sha or rows[k].get("sha16") is None:
                res["verdict"] = "REFUSED"
                res["why"] = f"confirmation row {k} is prior-seen or lacks a corpus hash: not independent"
                return res
    cands = list(prereg["candidates"])
    if not cands:
        res["verdict"], res["why"] = "NO CANDIDATE", "no candidate registered: nothing to select or confirm"
        return res
    for name in cands:
        s = _stage(rows, dev, name, prereg)
        s["target_measurable"] = s["n_harm"] >= rule["min_rows"]
        s["admissible"] = _meets(s, rule, rule["select_median"], rule["select_frac"])
        res["selection"][name] = s
    if not any(res["selection"][n]["target_measurable"] for n in cands):
        res["verdict"] = "REFUSED"
        res["why"] = "the harmonic target is unresolved/null on too many rows to measure any candidate"
        return res
    adm = [n for n in cands if res["selection"][n]["admissible"]]
    if not adm:
        res["verdict"], res["why"] = "NOT CONFIRMED", "no candidate meets the frozen targets and preservation on development"
        return res
    chosen = max(adm, key=lambda n: res["selection"][n]["harm_error_reduction_median"])
    res["chosen"] = chosen
    if len(conf) < rule["min_rows"]:
        res["verdict"] = "REFUSED"
        res["why"] = (f"{len(conf)} independent confirmation rows (< {rule['min_rows']}): "
                      "independent confirmation REFUSED; the candidate stays unqualified")
        return res
    c = _stage(rows, conf, chosen, prereg)
    if c["n_harm"] < rule["min_rows"]:
        res["verdict"], res["why"], res["confirmation"] = "REFUSED", "confirmation target unmeasurable", c
        return res
    c["confirmed"] = _meets(c, rule, rule["confirm_median"], rule["confirm_frac"])
    res["confirmation"] = c
    res["verdict"] = "CONFIRMED" if c["confirmed"] else "NOT CONFIRMED"
    res["why"] = "frozen confirmation rule " + ("met" if c["confirmed"] else "not met")
    return res


# ---------------------------------------------------------------------------
# 4. controls: synthetic fixtures for the verdict path, properties, defects
# ---------------------------------------------------------------------------
GATE_FEATURES = ("spec", "spec_peak", "centroid", "flatness", "impulse", "attack", "decay",
                 "modulation", "pitch", "pitch_shape")


def synthetic_prereg(n_conf: int = 6) -> dict:
    dev = ["LT25", "LT75", "MT25", "MT75", "HT25", "HT75"]
    conf = [f"EXT{i:02d}" for i in range(n_conf)]
    return {
        "issue": 593, "frozen": "synthetic", "corpus": {},
        "prior_seen": {"keys": [f"{s}{c}" for s in ("LT", "MT", "HT", "LC", "MC", "HC")
                                for c in ("00", "25", "50", "75", "10")],
                       "sha256_16": {f"x{i}": f"seen{i:012d}" for i in range(30)}},
        "development": {"keys": dev}, "preservation_only": {"keys": ["LC25", "MC25", "HC25"]},
        "confirmation": {"status": "synthetic", "keys": conf,
                         "sha256_16": {k: f"new{k}" for k in conf}},
        "features": list(GATE_FEATURES), "targets": ["modulation"],
        "candidates": {"Q": "quadratic output term (synthetic control)"},
        "rules": {"preserve_rel": 1.10, "preserve_abs": 0.10, "peak_db": 1.0, "dc_abs_fs": 0.002,
                  "min_rows": 4, "select_median": 0.5, "select_frac": 0.75,
                  "confirm_median": 0.5, "confirm_frac": 0.75}}


def _variant(mod=14.0, h=-85.0, dec=2.0, peak=0.5, dc=0.0):
    ratio = {f: 2.0 for f in GATE_FEATURES}
    ratio["modulation"], ratio["decay"] = mod, dec
    return {"ratio": ratio, "peak_fs": peak, "dc_fs": dc,
            "harm": {"db": [h, h, h], "resolved": [True] * 3}}


def synthetic_record(*, prereg=None, decay_bad=False, peak_bad=False, no_distortion=False,
                     contaminated=False, null_target=False, n_conf=6, ht_null_mod=True) -> dict:
    """A record the clean judge must CONFIRM, with one attractive-but-wrong
    variation on request. `decay_bad`/`peak_bad` keep the target gain and break
    a preserved quantity; `contaminated` relabels development/prior-seen rows
    as confirmation; `no_distortion` improves `modulation` with no harmonic change.
    The population comes from `prereg` (default: the matching synthetic one), so
    a record can never carry a population its judge has not frozen."""
    if prereg is None:
        prereg = synthetic_prereg(n_conf=n_conf)
    dev = list(prereg["development"]["keys"])
    pres = list(prereg["preservation_only"]["keys"])
    conf = list(prereg["confirmation"]["keys"])
    reg = prereg["confirmation"].get("sha256_16") or {}
    tgt = {"db": [-35.0, -38.0, -41.0], "resolved": [True] * 3}
    rows = {}
    for split, ks in (("development", dev), ("preservation-only", pres), ("confirmation", conf)):
        for k in ks:
            cand = _variant(mod=7.0, h=-37.0 if not no_distortion else -85.0)
            if split == "preservation-only":
                cand = _variant(mod=14.0, h=-85.0)
            if decay_bad and split != "confirmation":
                cand["ratio"]["decay"] = 9.0
            if peak_bad and split != "confirmation":
                cand["peak_fs"] = 0.5 * 10 ** (3.0 / 20)
            ship = _variant()
            if ht_null_mod and k.startswith("HT"):
                ship["ratio"]["modulation"] = cand["ratio"]["modulation"] = None
            rows[k] = {"split": split, "sha16": reg.get(k) if split == "confirmation" else None,
                       "target_harm": None if null_target else tgt,
                       "variants": {"shipped": ship, "Q": cand}}
    if contaminated:
        for i, k in enumerate(conf):
            rows[k]["sha16"] = f"seen{i:012d}"            # a prior-seen file under a new label
        if dev:
            src = dev[0]
            rows[src + "c"] = dict(rows[src], split="confirmation", sha16="seen000000000001")
    return {"rows": rows, "mechanism": {"verdict": "INSUFFICIENT"}}


def prop_K1_known_answers() -> bool:
    return harness_verdict(estimate, ka_grid()[::7])["pass"]


def prop_K2_pure_sinusoid_reads_unresolved() -> bool:
    for tau, g in ((0.06, 0.0), (0.06, 60.0), (0.15, 0.0)):
        y = synth_mixture(110.0, tau, (), glide_cents=g, glide_tau=0.1, noise_db=-50.0)
        if any(estimate(y, 48000)["resolved"]):
            return False
    return True


def prop_K3_applied_quadratic_reads_at_spec() -> bool:
    """The candidate path (apply_quadratic) read by the acceptance estimator
    equals the independently synthesised polynomial-of-carrier mixture."""
    base = synth_mixture(110.0, 0.15, ())
    got = estimate(apply_quadratic(base, -37.0), 48000)
    want = estimate(synth_mixture(110.0, 0.15, (-37.0,), h_tau_ratio=(0.5,)), 48000)
    return bool(got["resolved"][0] and want["resolved"][0]
                and abs(got["db"][0] - want["db"][0]) <= TOL_DB)


def _verdict(**kw):
    return judge(synthetic_record(**kw), synthetic_prereg())


def prop_J0_good_candidate_confirms() -> bool:
    return _verdict()["verdict"] == "CONFIRMED"


def prop_J1_decay_violation_rejected() -> bool:
    v = _verdict(decay_bad=True)
    return v["verdict"] != "CONFIRMED" and not v["selection"]["Q"]["admissible"]


def prop_J2_peak_violation_rejected() -> bool:
    v = _verdict(peak_bad=True)
    return v["verdict"] != "CONFIRMED" and not v["selection"]["Q"]["admissible"]


def prop_J3_contaminated_confirmation_refused() -> bool:
    """The contamination guard's own defeating input: a confirmation
    registration that names a prior-seen file (a cheating amendment), with the
    record matching its registration exactly. (A confirmation row merely
    LABELLED with prior-seen content is already caught by the frozen
    population and hash-exactness checks -- see prop_J6.)"""
    p = synthetic_prereg()
    k0 = p["confirmation"]["keys"][0]
    p["confirmation"]["sha256_16"][k0] = sorted(p["prior_seen"]["sha256_16"].values())[0]
    try:
        return judge(synthetic_record(prereg=p), p)["verdict"] == "REFUSED"
    except Refused:
        return True


def prop_J4_no_distortion_candidate_not_confirmed() -> bool:
    return _verdict(no_distortion=True)["verdict"] != "CONFIRMED"


def prop_J5_null_target_refused_not_zero_error() -> bool:
    return _verdict(null_target=True)["verdict"] == "REFUSED"


def prop_J6_frozen_population_enforced() -> bool:
    """The defeating inputs for the frozen population: an omitted preservation
    row, an omitted development row, a row the registration never named, an
    unregistered file on a registered key, a split the submitter may not
    choose, and one recording registered under two keys."""
    def refused(rec, p) -> bool:
        try:
            return judge(rec, p)["verdict"] == "REFUSED"
        except Refused:
            return True                                     # a refusal is a rejection too
    p = synthetic_prereg()
    rec = synthetic_record(prereg=p)
    del rec["rows"]["LC25"]                                 # preservation-only row omitted
    if not refused(rec, p):
        return False
    rec = synthetic_record(prereg=p)
    del rec["rows"]["MT75"]                                 # development row omitted
    if not refused(rec, p):
        return False
    rec = synthetic_record(prereg=p)
    rec["rows"]["EXT99"] = dict(rec["rows"]["EXT00"])       # a row the registration never named
    if not refused(rec, p):
        return False
    rec = synthetic_record(prereg=p)
    rec["rows"]["EXT00"]["sha16"] = "unregistered00"        # registered key, unregistered file
    if not refused(rec, p):
        return False
    rec = synthetic_record(prereg=p)
    rec["rows"]["EXT00"]["split"] = "development"           # a split the submitter may not choose
    if not refused(rec, p):
        return False
    p2 = synthetic_prereg()
    k0, k1 = p2["confirmation"]["keys"][:2]
    p2["confirmation"]["sha256_16"][k1] = p2["confirmation"]["sha256_16"][k0]
    rec = synthetic_record(prereg=p2)                       # matches the (bad) registration
    return refused(rec, p2)                                 # one recording, two keys


def prop_J7_nonfinite_or_missing_measurement_refused() -> bool:
    """NaN/Inf would defeat every ordered comparison (NaN > x is false) and a
    deleted field is not a measured null: each must be refused. An explicit
    null on both sides of a preserved feature is a violation, not a skip."""
    nan, inf = float("nan"), float("inf")

    def mangled(mut):
        rec = synthetic_record()
        row = rec["rows"]["MT25"]
        mut(row["variants"]["shipped"], row["variants"]["Q"], row)
        return rec

    def must_refuse(rec) -> bool:
        try:
            return judge(rec, synthetic_prereg())["verdict"] == "REFUSED"
        except Refused:
            return True

    for mut in (
        lambda s, c, r: c["ratio"].__setitem__("attack", nan),   # candidate side
        lambda s, c, r: s["ratio"].__setitem__("decay", nan),    # shipped side
        lambda s, c, r: s.__setitem__("peak_fs", inf),
        lambda s, c, r: c.__setitem__("peak_fs", nan),
        lambda s, c, r: c.__setitem__("peak_fs", 0.0),           # not a positive peak
        lambda s, c, r: s.__setitem__("dc_fs", nan),
        lambda s, c, r: c.__setitem__("dc_fs", -inf),
        lambda s, c, r: s["harm"]["db"].__setitem__(0, nan),
        lambda s, c, r: (s["ratio"].pop("pitch"), c["ratio"].pop("pitch")),  # deleted, not null
        lambda s, c, r: r["target_harm"]["db"].__setitem__(1, nan),
    ):
        if not must_refuse(mangled(mut)):
            return False
    rec = mangled(lambda s, c, r: (s["ratio"].__setitem__("attack", None),
                                   c["ratio"].__setitem__("attack", None)))
    v = judge(rec, synthetic_prereg())
    return v["verdict"] == "NOT CONFIRMED" and v["chosen"] is None


def prop_M1_mechanism_classes() -> bool:
    sr = 48000
    poly = synth_mixture(110.0, 0.1, (-35.0, -40.0, -45.0), h_tau_ratio=(0.5, 1 / 3, 0.25))
    const = synth_mixture(110.0, 0.1, (-35.0, -40.0, -45.0))
    return (harmonic_trajectory(poly, sr)["class"] == "polynomial-of-carrier"
            and harmonic_trajectory(const, sr)["class"] == "constant-ratio")


PROPERTIES = {f.__name__[5:]: f for f in (
    prop_K1_known_answers, prop_K2_pure_sinusoid_reads_unresolved,
    prop_K3_applied_quadratic_reads_at_spec, prop_J0_good_candidate_confirms,
    prop_J1_decay_violation_rejected, prop_J2_peak_violation_rejected,
    prop_J3_contaminated_confirmation_refused, prop_J4_no_distortion_candidate_not_confirmed,
    prop_J5_null_target_refused_not_zero_error, prop_J6_frozen_population_enforced,
    prop_J7_nonfinite_or_missing_measurement_refused, prop_M1_mechanism_classes)}

#: defect -> the property whose assertion it is meant to defeat
INTENDED = {"stub-estimator": ("K1_known_answers",),
            "missing-distortion": ("K3_applied_quadratic_reads_at_spec",),
            "leakage-as-h2": ("K2_pure_sinusoid_reads_unresolved",),
            "preservation-bypass": ("J1_decay_violation_rejected", "J2_peak_violation_rejected"),
            "contaminated-confirmation": ("J3_contaminated_confirmation_refused",),
            "target-ignored": ("J4_no_distortion_candidate_not_confirmed",),
            "mechanism-bypass": ("M1_mechanism_classes",),
            "population-bypass": ("J6_frozen_population_enforced",),
            "nonfinite-bypass": ("J7_nonfinite_or_missing_measurement_refused",)}


def _outcome(fn) -> str:
    try:
        return "PASS" if fn() else "FAIL"
    except AssertionError:
        return "FAIL"
    except Exception as e:                                   # missing apparatus / execution error
        return f"NO VERDICT ({type(e).__name__}: {e})"


def controls_matrix() -> dict:
    """properties x defects. MOVED = clean PASS -> defect FAIL (the property
    notices); BLIND = still PASS; NO VERDICT = the control itself errored (never
    counted as a caught mutant). If any clean property is not PASS the matrix is
    REFUSED: a control run against a red baseline proves nothing."""
    INJECT.clear()
    clean = {n: _outcome(f) for n, f in PROPERTIES.items()}
    out = {"clean": clean, "cells": {}, "intended": {k: list(v) for k, v in INTENDED.items()}}
    if any(v != "PASS" for v in clean.values()):
        out["status"] = "REFUSED"
        return out
    for d in DEFECTS:
        INJECT.clear()
        INJECT.add(d)
        try:
            row = {}
            for n, f in PROPERTIES.items():
                o = _outcome(f)
                row[n] = "MOVED" if o == "FAIL" else "BLIND" if o == "PASS" else "NO VERDICT"
            out["cells"][d] = row
        finally:
            INJECT.clear()
    bad = [(d, n) for d, ns in INTENDED.items() for n in ns if out["cells"][d][n] != "MOVED"]
    out["intended_not_moved"] = bad
    out["status"] = "OK" if not bad else "FAIL"
    return out


# ---------------------------------------------------------------------------
# 5. measuring commands (need the Fischer corpus and the model: build box)
# ---------------------------------------------------------------------------
TOMS = ("LT", "MT", "HT")
CODES = ("00", "25", "50", "75", "10")
DIRTY_SCOPE = ("model", "tools", "docs/scorecard/tom-distortion-593/prereg.json")


def _git(*a) -> str:
    import subprocess
    p = subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True)
    if p.returncode != 0:
        raise Refused(f"git {' '.join(a)} failed ({p.returncode}): {p.stderr.strip()}")
    return p.stdout.strip()


def provenance(refs) -> dict:
    """Read at the START of a measuring command; REFUSES on a dirty model/, tools/
    or prereg so the record names a commit that reproduces it."""
    dirty = _git("status", "--porcelain", "--", *DIRTY_SCOPE)
    if dirty:
        raise Refused("uncommitted changes in " + ", ".join(DIRTY_SCOPE) + ":\n" + dirty)
    return {"commit": _git("rev-parse", "HEAD"), "origin_main": _git("rev-parse", "origin/main"),
            "dirty": False, "refs": None if refs is None else str(refs)}


def _gate():
    sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "model"), str(ROOT / "tools" / "probes")]
    import perceptual_gate as pg
    import tom_conga_gate as tc
    return pg, tc


def _levels_json(r: dict) -> dict:
    return {k: r[k] for k in ("f0", "tau", "glide_cents", "db", "resolved", "floor_db")}


def _try(fn, *a):
    try:
        return fn(*a)
    except Refused as e:
        return {"REFUSED": str(e)}


def baseline(refs: pathlib.Path, prereg: dict) -> dict:
    """The CURRENT production kit at TUNING 5.0 (host writes + output path =
    tom_conga_gate.render at ratio 1.0, which must equal run_case.render_drum_solo
    bit-exactly or this REFUSES), the 808 target, every gate feature, the raw
    peak, corpus hashes. Effect (the harmonic levels) and mechanism are separate
    records (`mechanism`)."""
    pg, tc = _gate()
    import run_case as rc
    hashes = prereg["corpus"]["sha256_16"]
    out = {}
    for s in tc.FAMILY:
        ours, sr = tc.render(s)
        if not np.array_equal(ours, rc.render_drum_solo(s)[0]):
            raise Refused(f"{s}: render at ratio 1.0 is not render_drum_solo: not the shipped engine")
        rel = pg.target_rel(s)
        x, xsr = tc.load_checked(refs, rel, hashes)
        T = pg.Target(x, xsr, s, rel)
        bar = pg.bar_for(s, refs, T)["bar"]
        sc = tc.score(T, bar, ours, sr, f"baseline {s}")
        yo, yt = pg.condition(ours, sr), T.y
        out[s] = {"take": rel, "sha16": tc.sha16(refs / rel), "render_equals_render_drum_solo": True,
                  "raw_peak_fs": float(np.max(np.abs(ours))), "dc_fs": float(np.mean(ours)),
                  "gate_verdict": sc["verdict"], "worst": [sc["worst"], sc["worst_feature"]],
                  "ratio": sc["ratio"],
                  "harm_ours": _try(lambda: _levels_json(harmonic_levels(yo, pg.SR, pg._LEAD))),
                  "harm_808": _try(lambda: _levels_json(harmonic_levels(yt, pg.SR, pg._LEAD)))}
    return out


def mechanism(refs: pathlib.Path, prereg: dict) -> dict:
    """Effect and mechanism on the 808 toms (all 15 takes; prior-seen, so this
    is characterisation, not confirmation). Refuses takes outside the validated
    domain individually and counts them."""
    pg, tc = _gate()
    hashes = prereg["corpus"]["sha256_16"]
    takes, trajs, real = {}, [], []
    for s in TOMS:
        for c in CODES:
            rel = tc.take_rel(s, c)
            x, sr = tc.load_checked(refs, rel, hashes)
            y = pg.condition(x, sr)
            h = _try(lambda: harmonic_levels(y, pg.SR, pg._LEAD))
            t = _try(lambda: harmonic_trajectory(y, pg.SR, pg._LEAD))
            takes[f"{s}{c}"] = {"sha16": tc.sha16(refs / rel),
                                "levels": h if "REFUSED" in h else _levels_json(h),
                                "trajectory_class": t.get("class", "REFUSED"),
                                "trajectory": t if "REFUSED" in t else
                                {k: v for k, v in t.items() if k not in ("early", "late")}}
            if "REFUSED" not in h:
                real.append(bool(h["resolved"][0]))
            if "REFUSED" not in t:
                trajs.append(t)
    eff = None if not real else (sum(real) >= 0.75 * len(real))
    return {"takes": takes, "n_refused": sum("REFUSED" in v["levels"] for v in takes.values()),
            "mechanism": mechanism_verdict(trajs, eff)}


def main(argv=None) -> int:
    import argparse
    import json
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for n in ("knownanswer", "controls"):
        sub.add_parser(n).add_argument("--out", type=pathlib.Path, required=True)
    for n in ("baseline", "mechanism"):
        p = sub.add_parser(n)
        p.add_argument("--refs", type=pathlib.Path, required=True)
        p.add_argument("--out", type=pathlib.Path, required=True)
    j = sub.add_parser("judge")
    j.add_argument("record", type=pathlib.Path)
    j.add_argument("--out", type=pathlib.Path, required=True)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "knownanswer":
            res = harness_verdict(estimate)
            res["frozen"] = {"TOL_DB": TOL_DB, "RESOLVE_MARGIN_DB": RESOLVE_MARGIN_DB,
                             "SPEC_FLOOR_DB": SPEC_FLOOR_DB, "SNR_REQ_DB": SNR_REQ_DB,
                             "GLIDE_MAX_CENTS": GLIDE_MAX_CENTS}
        elif a.cmd == "controls":
            res = controls_matrix()
        elif a.cmd == "judge":
            res = judge(json.loads(a.record.read_text()), load_prereg())
        else:
            pr = load_prereg()
            if not a.refs.is_dir():
                raise Refused(f"corpus directory missing: {a.refs}")
            prov = provenance(a.refs)
            res = baseline(a.refs, pr) if a.cmd == "baseline" else mechanism(a.refs, pr)
            res = {"provenance": prov, "result": res}
    except Refused as e:
        print("REFUSED:", e)
        return 2
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, indent=1, default=str) + "\n")
    print(json.dumps({k: v for k, v in res.items() if k in ("pass", "status", "verdict")}))
    # exit codes (module docstring): 0 good verdict, 1 completed and negative,
    # 2 REFUSED. A completed result is never reported as success.
    if a.cmd == "knownanswer":
        return 0 if res.get("pass") is True else 1
    if a.cmd == "controls":
        st = res.get("status")
        return 0 if st == "OK" else 2 if st == "REFUSED" else 1
    if a.cmd == "judge":
        v = res.get("verdict")
        if v == "REFUSED":
            return 2
        return 0 if v in ("CONFIRMED", "NO CANDIDATE") else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
