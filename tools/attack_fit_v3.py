"""Waveform-fit attack estimator, measurement version m1a-envelope-score-v3.

The v2 estimator (`measure_mono_m1a_reference.attack_fit`) fits

    x(t) = A * s(t) * r(t),   r = 0 before t0, ((t-t0)/R)^p inside, 1 after

over a 150 ms window. Both M1A patches DECAY after the attack (sustain 0.75),
which that model cannot represent: the known-answer suite
(`attack_known_answer.py`) shows the plateau absorbing the decay and the
fitted 10-90 % time coming out short, by up to 4.8 ms on a 15 ms attack with a
steady spectrum. v3 changes exactly two things:

1. the level after (and during) the ramp is a quadratic in window time,
       x(t) = s(t) * r(t) * (A + B*tau + C*tau^2),  tau in [0, 1) over the window,
   solved by linear least squares for every (t0, R, p) -- the same FFT
   sliding-dot machinery, eight correlations instead of two;
2. the fit window is 80 ms (was 150 ms), so the quadratic has less curvature
   to represent.

It still reports ONE quantity, the 10-90 % time of the ramp r in ms
(kfrac(p) * R), and says whether the fitted R lies on a search boundary (the
32-sample minimum or the maximum), in which case the value is a search limit,
not a measurement. Template construction (fold of the steady gate) is
unchanged and reused from v2.
"""
from __future__ import annotations

import math

import numpy as np

import measure_mono_m1a_reference as v2

Refused = v2.ref.Refused
MIN_RAMP_SAMPLES = 32


def _suffix(v):
    return np.concatenate(([0.], np.cumsum(v[::-1])))[::-1]


def attack_fit_v3(audio, sr, f0, on_s, off_s, *, fold_ms=280.0, guard_ms=20.0,
                  fit_ms=80.0, pre_ms=2.0, t_max_ms=25.0, step_ms=0.25,
                  shapes=(0.5, 1.0, 2.0, 3.0, 4.0), degree=2):
    audio = np.asarray(audio, dtype=np.float64)
    if not np.isfinite(audio).all() or v2.ref.am.is_silent(audio):
        raise Refused("attack fit input is silent or non-finite")
    on, off = round(on_s * sr), round(off_s * sr)
    start = on - round(pre_ms * 1e-3 * sr)
    nfit = round(fit_ms * 1e-3 * sr)
    b_fold = off - round(guard_ms * 1e-3 * sr)
    if start < 0 or start + nfit > len(audio) or b_fold > len(audio):
        raise Refused("attack fit window truncates the audio")
    a_fold = off - round((fold_ms + guard_ms) * 1e-3 * sr)
    s_up, n0_up, upsample = v2.fold_waveform(audio, sr, f0, a_fold, b_fold)
    s = v2.aligned_template(s_up, n0_up, upsample, a_fold, start, nfit)
    x = audio[start:start + nfit]
    x2 = float(np.sum(x * x))
    if x2 <= 0:
        raise Refused("attack fit window is silent")
    tau = np.arange(nfit) / nfit
    m = degree + 1
    ys = [x * s * tau ** i for i in range(m)]                  # sum y s r tau^i
    ss = [s * s * tau ** i for i in range(2 * m - 1)]           # sum s^2 r^2 tau^i
    suf_y = [_suffix(v) for v in ys]
    suf_s = [_suffix(v) for v in ss]

    def best_over(p, n_spans):
        rows = []
        for n_span in n_spans:
            ramp = (np.arange(n_span) / n_span) ** p
            L = nfit - n_span + 1
            idx = np.arange(L) + n_span
            g = np.stack([v2._sliding_dot(v, ramp) + sv[idx] for v, sv in zip(ys, suf_y)], -1)
            h = [v2._sliding_dot(v, ramp * ramp) + sv[idx] for v, sv in zip(ss, suf_s)]
            M = np.empty((L, m, m))
            for i in range(m):
                for j in range(m):
                    M[:, i, j] = h[i + j]
            M += np.eye(m) * 1e-12 * np.maximum(M[:, :1, :1], 1e-30)
            coef = np.linalg.solve(M, g[..., None])[..., 0]
            resid = x2 - np.sum(coef * g, axis=-1)
            i = int(np.argmin(resid))
            rows.append((float(resid[i]), n_span, i, coef[i]))
        return min(rows, key=lambda r: r[0])

    n_max = round(t_max_ms * 1e-3 * sr)
    coarse = np.unique(np.maximum(MIN_RAMP_SAMPLES, np.round(
        np.arange(1.0, t_max_ms + step_ms, step_ms) * 1e-3 * sr))).astype(int)
    best = (math.inf,)
    for p in shapes:
        _, n_coarse, _, _ = best_over(p, coarse)
        fine = np.arange(max(MIN_RAMP_SAMPLES, n_coarse - 16),
                         min(n_max, n_coarse + 16) + 1).astype(int)
        row = best_over(p, fine)
        if row[0] < best[0]:
            best = (*row, p)
    resid, n_span, i, coef, p = best
    explained = 1.0 - resid / x2
    if not math.isfinite(explained) or explained < v2.MIN_EXPLAINED_RATIO:
        raise Refused(f"waveform-fit attack model explains only {explained:.2f} of the window")
    kfrac = 0.9 ** (1 / p) - 0.1 ** (1 / p)
    boundary = ("minimum" if n_span <= MIN_RAMP_SAMPLES else
                "maximum" if n_span >= n_max else None)
    return {"t0_ms": i * 1000 / sr - pre_ms, "ramp_ms": n_span * 1000 / sr,
            "shape_p": p, "attack_10_90_ms": n_span * kfrac * 1000 / sr,
            "search_boundary": boundary,
            "level_poly": [float(c) for c in coef],
            "explained_ratio": round(explained, 4), "valid": True}
