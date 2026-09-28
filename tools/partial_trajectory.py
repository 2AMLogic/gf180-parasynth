"""Per-partial amplitude TRAJECTORY estimator for a decaying percussive hit.

The quantity a 'partial balance' should name is the ratio of the partials'
amplitudes AT THE STRIKE (A0), together with how that ratio evolves, because
partials with different decay constants make a single-window ratio a function
of the window rather than of the voice.

Method, per partial:
  1. frequency by peak of the coherent projection magnitude on a fine grid
     (a damped sinusoid's DTFT magnitude is a Lorentzian centred on f0);
  2. amplitude trajectory A(t) by a sliding Hann-windowed coherent projection
     at that frequency, normalised by the window's coherent gain;
  3. A0 and tau by a weighted log-linear fit of A(t), with the Hann window's
     own exponential-averaging bias divided out (it is a constant factor for an
     exponential, so it biases A0 and not tau; solved by one fixed-point pass);
  4. a FLOOR measured on the same record and the same window, by projecting at
     guard frequencies that hold no partial. Any point within `floor_margin_db`
     of it is dropped, and a fit with too few surviving points REFUSES.

Step 1 is a MAXIMUM over a grid, so on a record that holds no partial the value
at the frequency it returns is biased upward by the pick itself, and any
comparison of it against a floor read at FIXED frequencies measures that bias
rather than a partial. `line_is_resolved` is the precondition that catches it
(#389): it tests the SHAPE of the line against a damped sinusoid's closed form,
where the height -- and with it the selection bias -- divides out.
"""
import math
import numpy as np


def _hann(n):
    return np.hanning(n + 2)[1:-1] if n > 2 else np.ones(n)


def project(x, f, sr, w=None):
    """Amplitude of a sinusoid at f over the whole of x, window-corrected.
    For a stationary tone of amplitude A this returns exactly A."""
    n = len(x)
    if w is None:
        w = np.ones(n)
    k = np.exp(-2j * math.pi * f * np.arange(n) / sr)
    return 2.0 * abs(np.sum(w * x * k)) / np.sum(w)


def find_partial(x, sr, f_lo, f_hi, *, seconds=None, df=0.05):
    """Frequency of the strongest line in [f_lo, f_hi], by a fine grid search of
    the coherent projection over the first `seconds` of the record."""
    y = x if seconds is None else x[: int(seconds * sr)]
    n = len(y)
    grid = np.arange(f_lo, f_hi + df, df)
    t = np.arange(n) / sr
    # chunked so the grid x n outer product stays small
    best_f, best_v = None, -1.0
    for i in range(0, len(grid), 256):
        g = grid[i:i + 256]
        k = np.exp(-2j * math.pi * np.outer(g, t))
        v = np.abs(k @ y)
        j = int(np.argmax(v))
        if v[j] > best_v:
            best_v, best_f = float(v[j]), float(g[j])
    return best_f


# --------------------------------------------------------------------------
# Is the line `find_partial` returned a PARTIAL, or the maximum of a
# fluctuation? (#389)
#
# A damped sinusoid of decay tau, projected coherently over a span T about its
# own line, has a Lorentzian magnitude:
#
#     P(f0 + d) = P(f0) / sqrt(1 + (2*pi*tau*d)**2)
#
# so the DROP in dB at an offset d is 10*log10(1 + (2*pi*tau*d)**2) -- a
# one-parameter curve in d whose only free parameter is the mode's own tau, and
# which does not contain P(f0) at all. That is the whole point: the pick's
# upward bias lives entirely in P(f0), so a test on the SHAPE is a test the bias
# cannot pass on noise's behalf.
#
# The gate is therefore a one-parameter least-squares fit of that closed form to
# the drops measured at +-`n_bins` search bins, scored by its RMS residual in dB.
# Nothing in it is calibrated against our own model: the curve comes from the
# DTFT, and both the width and the tolerance were set from the gap between real
# records and white noise (tools/probes/balance_line_shape.py `sweep` chose
# n_bins, `margin` chose max_resid_db).
# --------------------------------------------------------------------------
_TAU_GRID_S = np.logspace(-5.0, 0.0, 501)          # 10 us .. 1 s


def line_offsets(x, sr, f, *, seconds, n_bins=6):
    """The measured line shape about `f`: (P(f), offsets in Hz, drops in dB).

    Offsets are whole multiples of `1/seconds` -- the resolution of the very
    span the line was FOUND on, so they are the estimator's own bins and not a
    chosen span. Both sides are returned, interleaved (-1, +1, -2, +2, ...),
    because a line sitting on the shoulder of another partial is asymmetric and
    the fit should see that as residual."""
    d = 1.0 / seconds
    y = x[: int(seconds * sr)]
    p0 = project(y, f, sr)
    ds, drops = [], []
    for k in range(1, n_bins + 1):
        for side in (-1.0, +1.0):
            p = project(y, f + side * k * d, sr)
            ds.append(k * d)
            drops.append(20.0 * math.log10(p0 / p) if p > 0 and p0 > 0 else math.inf)
    return p0, np.asarray(ds, float), np.asarray(drops, float)


_PEDESTAL_GRID_DB = np.concatenate(([-300.0], np.arange(-60.0, -2.9, 0.5)))


def _lorentzian_fit(ds, drops_db, *, min_points=9, pedestal=False):
    """Best-fit tau for the drops at offsets `ds`, and the fit's RMS dB residual.

        (P(f0+d)/P(f0))**2 = 1/(1 + (2*pi*tau*d)**2) + g**2

    with `g` -- a pedestal standing for whatever else the record holds at these
    offsets -- FORCED TO ZERO by default, which is the surprise in this function
    and the reason it is a keyword rather than a constant.

    The pedestal is the physically honest model: a real neighbourhood is never
    empty, and a mode whose skirt has fallen into the record's own floor by the
    sixth bin reads a drop that stops growing. It was added for that reason. Then
    `balance_line_shape.py margin` measured it, over 8 real partials and 96
    white-noise partials: giving the fit a floor term to absorb makes it fit
    NOISE better too, and it closes the residual margin from 1.71x to 1.17x.
    An extra parameter helps the null more than the signal here, so it is off --
    a decision that can only be made by measuring both, which is why both are
    still reachable and why the probe prints both columns.

    Grids rather than a solver (501 taus over five decades, ~2.3 % apart; `g`
    from -60 dB in 0.5 dB steps plus exactly zero) because the residual surface
    is not convex in tau, the grid is finer than the residual's own noise, and a
    grid cannot fail to converge -- which a precondition must never do. Returns
    dict(ok, tau_ms, pedestal_db, resid_db, n_points, reason)."""
    m = np.isfinite(drops_db)
    if int(m.sum()) < min_points:
        return dict(ok=False, tau_ms=math.inf, pedestal_db=math.nan,
                    resid_db=math.inf, n_points=int(m.sum()),
                    reason=f"only {int(m.sum())} of {len(drops_db)} offsets hold any "
                           f"energy at all (need {min_points})")
    d, y = ds[m], drops_db[m]
    gs = _PEDESTAL_GRID_DB if pedestal else _PEDESTAL_GRID_DB[:1]
    lor = 1.0 / (1.0 + (2.0 * math.pi * np.outer(_TAU_GRID_S, d)) ** 2)   # (T, D)
    g2 = 10.0 ** (gs / 10.0)                                             # (G,)
    pred = -10.0 * np.log10(lor[None, :, :] + g2[:, None, None])         # (G, T, D)
    resid = np.sqrt(np.mean((pred - y[None, None, :]) ** 2, axis=2))
    gi, ti = np.unravel_index(int(np.argmin(resid)), resid.shape)
    return dict(ok=True, tau_ms=float(_TAU_GRID_S[ti] * 1e3),
                pedestal_db=float(gs[gi]), resid_db=float(resid[gi, ti]),
                n_points=int(m.sum()), reason="")


def line_is_resolved(x, sr, f, *, seconds, n_bins=6, max_resid_db=2.2):
    """Is the line `find_partial` returned at `f` a resolved DECAYING MODE of
    this record, or the maximum of a fluctuation? (#389)

    `find_partial` returns the strongest line in its search range -- a maximum
    over thousands of grid points -- so on a record that holds no partial the
    value at the frequency it picks is upward-biased by the pick itself. Every
    downstream comparison against a floor read at FIXED frequencies then
    measures that selection artifact rather than a partial, which is how
    `run_case.balance_trajectory_db` came to REPORT a balance on white noise.

    TWO CONDITIONS, BOTH ON THE SHAPE AND NEITHER ON THE HEIGHT. They catch
    different records and only one of them catches noise -- said plainly here
    because a gate whose conditions are described as interchangeable is a gate
    nobody can reason about:

      1. THE SHAPE, which is what refuses NOISE. The drops at +-`n_bins` search
         bins must fit a damped sinusoid's Lorentzian to within `max_resid_db`
         RMS. Noise cannot: at a spacing of a whole multiple of 1/seconds a
         rectangular window's correlation with its own line is zero (sinc(k)=0),
         so noise's off-centre projections are independent of the peak and of
         each other and scatter by several dB about no curve at all.
      2. THE DECAY, which is what refuses a STEADY TONE. The tau the fit
         recovers must decay inside the window it was measured over
         (`tau < seconds`). This condition does NOT discriminate noise -- 96 of
         96 white-noise partials measured fit a tau between 0.04 and 0.47 of
         their window, comfortably inside the bound -- and a reading of this
         function that assumed it did would be wrong. It is here for the
         undamped-tone record, which is a clean line and still not a struck mode.

    MEASURED (tools/probes/balance_line_shape.py margin 24, 2026-09-27, 48 kHz):
    over 8 real partials -- the Fischer TR-808 rimshot and cowbell and our own
    renders of both, low and high, at both shipping operating points -- the
    residual runs 0.22 to 1.70 dB, while over 96 white-noise partials it runs
    2.91 to 12.60 dB. `max_resid_db=2.2` is the geometric mean of those two
    edges: 1.29x of margin above the worst real record, 1.32x below the best
    noise record. Four other statistics were measured on the same signals and
    every one of them overlaps; the probe carries all five columns and says which.

    THE MARGIN IS THE WEAK POINT, and quoting it is the point of quoting it: 1.3x
    is thin, it rests on 8 real partials, and `margin` is the command to re-run
    before trusting this gate on a fifth record or a third voice.

    Returns dict(ok, tau_ms, resid_db, drop1_db, n_points, max_resid_db, reason).
    This is a PRECONDITION, so the reason names what failed and `ok=False` is
    REFUSED rather than a verdict. `max_resid_db` is echoed back because the
    residual is only readable against the tolerance that scored it, and callers
    that record the one (`run_case.balance_trajectory_db`'s `detail`) should not
    have to re-derive the other from this signature.

    Ground truth: test_line_is_resolved_passes_a_damped_mode_at_every_tau,
    test_line_is_resolved_refuses_white_noise,
    test_line_is_resolved_refuses_a_line_that_never_decays,
    test_line_is_resolved_is_blind_to_the_lines_height."""
    p0, ds, drops = line_offsets(x, sr, f, seconds=seconds, n_bins=n_bins)
    drop1 = tuple(float(v) for v in drops[:2])
    if not (p0 > 0):
        return dict(ok=False, tau_ms=math.inf, resid_db=math.inf, drop1_db=drop1,
                    n_points=0, max_resid_db=float(max_resid_db),
                    reason=f"no energy at all at {f:.1f} Hz")
    fit = _lorentzian_fit(ds, drops)
    out = dict(tau_ms=fit["tau_ms"], resid_db=fit["resid_db"], drop1_db=drop1,
               n_points=fit["n_points"], max_resid_db=float(max_resid_db))
    if not fit["ok"]:
        return dict(ok=False, reason=fit["reason"], **out)
    if not (fit["tau_ms"] * 1e-3 < seconds):
        return dict(ok=False, reason=(
            f"the line at {f:.1f} Hz fits a mode of tau {fit['tau_ms']:.1f} ms, which "
            f"does not decay inside the {seconds*1e3:.0f} ms window it was measured "
            f"over: this is the shape of a steady tone, not of a struck mode"), **out)
    if fit["resid_db"] > max_resid_db:
        return dict(ok=False, reason=(
            f"the line at {f:.1f} Hz does not have the shape of one: its drops over "
            f"+-{n_bins} search bins ({ds[-1]:.0f} Hz) fit a damped sinusoid's "
            f"Lorentzian no better than {fit['resid_db']:.2f} dB RMS (accepted "
            f"{max_resid_db:.1f}), at a best-fit tau of {fit['tau_ms']:.1f} ms. This "
            f"is the shape of a maximum picked out of a fluctuation, not of a "
            f"partial"), **out)
    return dict(ok=True, reason="", **out)


def trajectory(x, sr, f, *, win_ms=20.0, hop_ms=2.0, t_end=None):
    """Sliding Hann-windowed projection at f. Returns (t_centres, amplitude)."""
    W = int(win_ms * 1e-3 * sr)
    H = max(1, int(hop_ms * 1e-3 * sr))
    w = _hann(W)
    n = len(x) if t_end is None else min(len(x), int(t_end * sr))
    ts, amps = [], []
    for a in range(0, n - W + 1, H):
        seg = x[a:a + W]
        amps.append(project(seg, f, sr, w))
        ts.append((a + W / 2.0) / sr)
    return np.asarray(ts), np.asarray(amps)


def floor_at(x, sr, f, guards, *, win_ms=20.0, hop_ms=2.0, t_end=None):
    """The estimator's own floor for this record, this window and this f: the
    LARGEST trajectory found at frequencies that hold no partial. Leakage from
    the real partials is inside this number by construction, which is the point
    -- issue #92 asks for a floor measured from the same record, not a quoted
    constant."""
    worst = None
    for g in guards:
        _, a = trajectory(x, sr, g, win_ms=win_ms, hop_ms=hop_ms, t_end=t_end)
        worst = a if worst is None else np.maximum(worst, a)
    return worst


def _hann_exp_gain(W, tau, sr):
    """Hann-weighted mean of exp(-t/tau) about the window centre. A constant
    factor for an exponential, so it shifts A0 and not tau."""
    w = _hann(W)
    d = (np.arange(W) - W / 2.0) / sr
    return float(np.sum(w * np.exp(-d / tau)) / np.sum(w))


def fit_decay(ts, amps, *, floor=None, floor_margin_db=6.0, sr=None, win_ms=20.0,
              min_points=5, min_span_ms=4.0):
    """A0 and tau from a log-linear fit, dropping every point inside the floor.

    Returns dict(ok, A0, tau_ms, n_points, span_ms, resid_db, reason)."""
    keep = np.ones(len(ts), dtype=bool)
    if floor is not None:
        keep &= amps > floor * (10.0 ** (floor_margin_db / 20.0))
    keep &= amps > 0
    if keep.sum() < min_points:
        return dict(ok=False, reason=f"only {int(keep.sum())} points clear the floor "
                                     f"by {floor_margin_db:.0f} dB (need {min_points})")
    t, a = ts[keep], amps[keep]
    # contiguous run from the peak onward -- the decay, not the attack
    pk = int(np.argmax(a))
    t, a = t[pk:], a[pk:]
    if len(t) < min_points:
        return dict(ok=False, reason=f"only {len(t)} points after the peak (need {min_points})")
    span_ms = (t[-1] - t[0]) * 1e3
    if span_ms < min_span_ms:
        return dict(ok=False, reason=f"decay spans {span_ms:.1f} ms (need {min_span_ms})")
    A0, tau = None, None
    for _ in range(12):                       # fixed point on the window bias
        c = np.polyfit(t, np.log(a), 1)
        tau_new = -1.0 / c[0] if c[0] < 0 else None
        if tau_new is None or not np.isfinite(tau_new) or tau_new <= 0:
            return dict(ok=False, reason="fitted envelope does not decay")
        g = _hann_exp_gain(int(win_ms * 1e-3 * sr), tau_new, sr) if sr else 1.0
        A0_new = math.exp(c[1]) / g
        if tau is not None and abs(tau_new - tau) < 1e-9 and abs(A0_new - A0) < 1e-12:
            A0, tau = A0_new, tau_new
            break
        A0, tau = A0_new, tau_new
    pred = np.log(A0 * _hann_exp_gain(int(win_ms*1e-3*sr), tau, sr) * np.exp(-t / tau)) if sr \
        else np.log(A0 * np.exp(-t / tau))
    resid_db = float(np.sqrt(np.mean((20.0 / math.log(10)) ** 2 * (np.log(a) - pred) ** 2)))
    return dict(ok=True, A0=float(A0), tau_ms=float(tau * 1e3), n_points=int(len(t)),
                span_ms=float(span_ms), resid_db=resid_db, t0=float(t[0]), reason="")


def measure(x, sr, f_lo_range, f_hi_range, *, win_ms=20.0, hop_ms=2.0,
            search_s=None, t_end=None, guards=None, floor_margin_db=6.0):
    """Both partials of a two-mode voice: frequency, A0, tau, and the balance."""
    f_lo = find_partial(x, sr, *f_lo_range, seconds=search_s)
    f_hi = find_partial(x, sr, *f_hi_range, seconds=search_s)
    out = {"f_lo": f_lo, "f_hi": f_hi}
    for tag, f in (("lo", f_lo), ("hi", f_hi)):
        ts, a = trajectory(x, sr, f, win_ms=win_ms, hop_ms=hop_ms, t_end=t_end)
        g = guards.get(tag) if guards else None
        fl = floor_at(x, sr, f, g, win_ms=win_ms, hop_ms=hop_ms, t_end=t_end) if g else None
        fit = fit_decay(ts, a, floor=fl, floor_margin_db=floor_margin_db, sr=sr, win_ms=win_ms)
        out[tag] = dict(f=f, ts=ts, amp=a, floor=fl, fit=fit)
    return out
