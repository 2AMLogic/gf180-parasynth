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
"""
from __future__ import annotations

import math
import pathlib

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
PREREG = ROOT / "docs" / "scorecard" / "tom-distortion-593" / "prereg.json"


class Refused(RuntimeError):
    """The apparatus cannot answer. Distinct from pass and from fail."""


#: Injected defects (rule 5). Each must turn a named property red.
DEFECTS = ("stub-estimator", "missing-distortion", "leakage-as-h2",
           "preservation-bypass", "contaminated-confirmation", "target-ignored",
           "mechanism-bypass")
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


ESTIMATOR = stub_levels


# ---------------------------------------------------------------------------
# the known-answer harness (the same estimator that is later used on takes)
# ---------------------------------------------------------------------------
#: Tolerances are FROZEN from the grid below (see docs/scorecard/tom-distortion-593/
#: knownanswer.json) and may not be edited after a candidate has been measured.
TOL_DB = 2.0                  # |read - specified| for a resolved harmonic
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
                    for glide in (0.0, 60.0):
                        for noise in (None, -60.0, -50.0):
                            cases.append(dict(f0=f0, tau=tau, h_db=h_db, sr=sr,
                                              glide_cents=glide, noise_db=noise))
    return cases


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
                          noise_db=c["noise_db"])
        try:
            r = _run(estimator, y, c["sr"])
        except Refused:
            wrong_refusal += 1
            continue
        for k, want in enumerate(c["h_db"]):
            if want < SPEC_FLOOR_DB:
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
