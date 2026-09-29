#!/usr/bin/env python3
"""Where does aliasing enter the oscillator/ladder path? (issue #80)

**Two different structures are measured here and they must not be confused.**

    CHAIN A -- the #80 experiment, and the ONLY thing the 9.6 dB regression is
    about. The OSCILLATOR is run at 2x (phase increment halved, PolyBLEP
    evaluated at the high rate) and its output is reduced to the base rate by
    keeping the last sub-step. No ladder is involved at any point.

        2x-rate oscillator -> keep last sub-step

    CHAIN B -- what the voice actually ships (`VoiceFx._render` +
    `LadderFx.process`, both read, both confirmed). The oscillators run at the
    OUTPUT rate; `LadderFx.process` reuses the same input sample for all
    `self.os` internal updates; `g` is computed for the internal rate; the
    output is the last sub-step; there is NO decimation filter.

        base-rate oscillator -> sample-and-hold across sub-steps
                             -> oversampled nonlinear ladder -> keep last sub-step

Neither is "a properly reconstructed, oversampled, filtered path". Chain A is
not the shipped path. Chain B's oversampling is of the NONLINEARITY only, and
changing `LadderFx.os` does not touch `OscFx`'s increment or reciprocal at all
-- `VoiceFx._render` calls `o.render(n, inc)` with the base-rate increment
whatever the ladder is set to -- so a PolyBLEP rate mismatch cannot be a chain
B mechanism. In chain A the increment DOES change, and
`report_blep_normalisation` checks what that did to the correction.

**References, per stage.** An analytic band-limited waveform is the right
reference for an OSCILLATOR and is used only there (chain A). It is not the
expected output of a driven nonlinear ladder, so chain B is driven with a
CLEAN SINUSOID -- whose legitimate output is harmonics of f0 and nothing else,
which makes "everything not at a harmonic" an aliasing measure that is blind to
the filter's gain and response -- and cross-checked against a CONVERGED
higher-rate render for the harmonic amplitudes, so a response difference cannot
masquerade as an aliasing difference.

**One metric, one setting, everywhere.** `MEASURE` fixes record length, window,
zero-padding and guard width; every reading in every table comes through
`frac()` with those settings, at the rate the signal is actually at, and every
reading is printed beside the RMS of the signal it came from and beside the
estimator's own floor for that f0/rate/length. A reading that is not clear of
its floor is not a measurement.

    .venv/bin/python model/alias_probe.py --out build/alias/<tag>

Everything here is measurement. It changes no shipped behaviour.
"""
import hashlib
import json
import math
import os
import subprocess
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "audition"))
import dsp                                                          # noqa: E402
import fixed                                                        # noqa: E402
import voice_fx as vf                                               # noqa: E402
import audio_measure as am                                          # noqa: E402

SR = dsp.SR                       # 48 000, the OUTPUT rate
PHASE_BITS = 24
CYCLE = 1 << PHASE_BITS
FS = 32768.0                      # Q1.15 full scale

# ---- the metric, fixed once (point 5: hold it still) -----------------------
MEASURE = dict(seconds=0.5, guard=5, window="hann", pad=1)
GUARD = MEASURE["guard"]

NOTES = (40, 52, 64, 76, 88, 100)


# ---------------------------------------------------------------------------
# provenance (point 7: pin the run)
# ---------------------------------------------------------------------------
def _git(*a) -> str:
    try:
        return subprocess.run(["git", *a], cwd=os.path.dirname(os.path.abspath(__file__)),
                              capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception as e:                                          # pragma: no cover
        return f"<unavailable: {e}>"


def provenance(argv=None) -> dict:
    """Commit, uncommitted diff hash, the exact command, and a hash of every
    source file this run's numbers depend on.

    Other agents are editing this tree. An earlier green run does not cover a
    later dependency change, so the dependency set is hashed INTO the artefact
    rather than assumed stable."""
    deps = {}
    here = os.path.dirname(os.path.abspath(__file__))
    for rel in ("voice_fx.py", "fixed.py", "audio_measure.py", "alias_probe.py",
                "../audition/dsp.py"):
        p = os.path.normpath(os.path.join(here, rel))
        try:
            deps[os.path.relpath(p, os.path.dirname(here))] = \
                hashlib.sha256(open(p, "rb").read()).hexdigest()[:16]
        except OSError:
            deps[rel] = "<missing>"
    diff = _git("diff", "HEAD")
    return dict(commit=_git("rev-parse", "HEAD"),
                branch=_git("rev-parse", "--abbrev-ref", "HEAD"),
                dirty=bool(diff),
                diff_sha256=hashlib.sha256(diff.encode()).hexdigest()[:16],
                command=" ".join(argv or sys.argv),
                python=sys.version.split()[0], numpy=np.__version__,
                measure=dict(MEASURE), deps=deps,
                utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))


# ---------------------------------------------------------------------------
# the one metric
# ---------------------------------------------------------------------------
def frac(x: np.ndarray, f0: float, sr: int, what: str = "") -> float:
    """`inharmonic_fraction_db` at MEASURE's guard, at the rate `x` is at.

    `.require()` so an estimator refusal is a REFUSED outcome and not a
    plausible number.

    `min_headroom_db=None` (#115): this probe's whole discipline is that it
    measures the estimator's own floor per f0/rate/length and prints every
    reading beside it -- "a reading that is not clear of its floor is not a
    measurement" (module docstring), and `validate_estimator` goes further and
    adds the floor back in closed form (`want = 10log10(s + floor*(1-s))`),
    which is the same additive bias `INHARMONIC_MIN_HEADROOM_DB` was derived
    from. So the floor-clearance check lives HERE, in the probe, not in the
    estimator's default gate.

    It has to be applied at this one chokepoint rather than per-row, for a
    reason: the floor rows (`floor_lo`, `floor_hi`, `floor_fir`, `floor`) are
    alias-free by construction, so their reading IS the floor at ~0 dB
    headroom, and under the default gate they refuse outright. A floor row and
    the reading it bounds must come from the IDENTICAL instrument or the
    comparison means nothing -- gating one and not the other would silently
    change what the tables compare. The estimator's refusal machinery is still
    live for every other axis; only the SNR-vs-own-floor gate is deferred to
    the probe that already measures it."""
    return am.inharmonic_fraction_db(x, f0, sr, guard=GUARD,
                                     min_headroom_db=None).require(what)


def rms_db(x: np.ndarray) -> float:
    return 20.0 * math.log10(max(float(np.sqrt(np.mean(np.asarray(x, dtype=np.float64) ** 2))), 1e-30))


# ---------------------------------------------------------------------------
# the analytic reference -- OSCILLATORS ONLY (chain A)
# ---------------------------------------------------------------------------
def saw_harmonics(f0: float, sr: int) -> np.ndarray:
    """k for every harmonic of `f0` strictly below `sr/2`."""
    return np.arange(1, int(math.floor((sr / 2.0 - 1e-9) / f0)) + 1)


def bl_saw(f0: float, n: int, sr: int) -> np.ndarray:
    """A band-limited sawtooth summed additively: 2t-1 = -(2/pi) sum sin(2 pi k t)/k.

    Amplitude and phase match `voice_fx.naive_fx("saw", .)` exactly -- our
    oscillator emits phase BEFORE incrementing, so sample i is at t = i*f0/sr,
    and the naive saw is 2t-1 over [0,1). Peak-to-peak 2, as Q1.15/32768 is.

    By construction this has NO inharmonic content. What the estimator reads on
    it is the estimator's floor at this f0, rate and record length and nothing
    else -- a floor that is NOT a constant, because it depends on how many
    harmonics are present and where they fall relative to the bins. That is
    why it is measured per row. (`audio_measure.inharmonic_fraction_db` used
    to quote -54 dB in its docstring; since #119 it windows with `_bh4` and
    measures its own floor per call, and this probe and that estimator now
    agree on the principle as well as the number.)"""
    t = np.arange(n, dtype=np.float64) * (f0 / sr)
    out = np.zeros(n, dtype=np.float64)
    for k in saw_harmonics(f0, sr):
        out -= np.sin(2.0 * math.pi * k * t) / k
    return out * (2.0 / math.pi)


def plant_inharmonics(x: np.ndarray, f0: float, sr: int, share: float,
                      count: int = 24, seed: int = 7) -> np.ndarray:
    """`x` plus a comb of `count` tones that are not harmonics of `f0` and not
    within a guard of one, carrying exactly `share` of the total energy of the
    result. The estimator must read back 10*log10(share).

    A comb rather than one tone, because real aliasing is dense and a one-tone
    control cannot expose a mask that is subtly the wrong width."""
    rng = np.random.default_rng(seed)
    n = len(x)
    bin_hz = sr / n
    freqs = []
    guard_hz = (2 * GUARD + 2) * bin_hz
    while len(freqs) < count:
        f = float(rng.uniform(0.08, 0.45) * sr)
        if abs(f / f0 - round(f / f0)) * f0 < guard_hz:
            continue
        if any(abs(f - g) < guard_hz for g in freqs):
            continue
        freqs.append(f)
    t = np.arange(n, dtype=np.float64) / sr
    noise = np.zeros(n, dtype=np.float64)
    for f in freqs:
        noise += np.sin(2.0 * math.pi * f * t + rng.uniform(0, 2 * math.pi))
    ph, pn = float((x ** 2).mean()), float((noise ** 2).mean())
    return x + math.sqrt(share / (1.0 - share) * ph / pn) * noise


def alias_fraction_closed_form(f0: float, sr_hi: int, m: int, n: int,
                               guard: int = GUARD) -> dict:
    """Drop-decimating a band-limited saw by `m`: the exact aliased fraction,
    computed from the Fourier coefficients WITHOUT taking an FFT.

    Harmonic k has power (1/k)^2 and lands at `fold_frequency(k*f0, sr_hi/m)`.
    Those below the new Nyquist stay harmonic; the rest are aliases unless the
    fold puts them inside the estimator's +-guard window of a real harmonic, in
    which case the estimator counts them as harmonic and so does this.

    Calls nothing in `audio_measure` except `fold_frequency` (four lines of
    arithmetic), so agreeing with `inharmonic_fraction_db` on the same signal
    is an independent check of the estimator rather than a restatement of it."""
    sr_lo = sr_hi // m
    bin_hz = sr_lo / n
    k_hi = int(math.floor((sr_hi / 2.0 - 1e-9) / f0))
    k_lo = int(math.floor((sr_lo / 2.0 - 1e-9) / f0))
    harm = np.arange(1, k_lo + 1) * f0
    p_keep = sum(1.0 / k ** 2 for k in range(1, k_lo + 1))
    p_alias = p_masked = 0.0
    for k in range(k_lo + 1, k_hi + 1):
        fa = am.fold_frequency(k * f0, sr_lo)
        p = 1.0 / k ** 2
        if (len(harm) and np.abs(harm - fa).min() <= (guard + 0.5) * bin_hz) \
                or fa <= (guard + 0.5) * bin_hz:
            p_masked += p
        else:
            p_alias += p
    total = p_keep + p_alias + p_masked
    return dict(db=10.0 * math.log10(max(p_alias / total, 1e-300)),
                above_nyquist_db=10.0 * math.log10(max((p_alias + p_masked) / total, 1e-300)),
                k_lo=k_lo, k_hi=k_hi, folded=k_hi - k_lo,
                masked_share=p_masked / max(p_alias + p_masked, 1e-300))


# ---------------------------------------------------------------------------
# decimators
# ---------------------------------------------------------------------------
def decimate_drop(x: np.ndarray, m: int) -> np.ndarray:
    """The decimator under test: keep the LAST sub-step, which is what
    `LadderFx.process` does and what Surge's Huovilainen does
    (`return outputOS[1]`)."""
    return x[m - 1::m]


def lowpass_taps(m: int, taps: int = 127, stop_ratio: float = 0.95) -> np.ndarray:
    """Windowed-sinc anti-imaging filter for decimate-by-`m`, cutoff at
    `stop_ratio` of the LOW rate's Nyquist. A float64 DIAGNOSTIC -- it is here
    to identify the cause, not to propose an implementation."""
    fc = stop_ratio / (2.0 * m)
    i = np.arange(taps) - (taps - 1) / 2.0
    h = 2.0 * fc * np.sinc(2.0 * fc * i) * np.blackman(taps)
    return h / h.sum()


def halfband_taps(order: int) -> np.ndarray:
    """A half-band FIR of `order` taps (order % 4 == 3): symmetric, and every
    even tap but the centre is zero, so a 2x decimator costs (order+1)/4
    multiplies per OUTPUT sample. Used only to put a MEASURED bound on "how
    long a decimator has to be"; it is not a proposal and carries no area
    number -- that belongs to the fix task, not this one."""
    assert order % 4 == 3, "a half-band filter has order % 4 == 3"
    i = np.arange(order) - (order - 1) // 2
    h = 0.5 * np.sinc(i / 2.0) * np.blackman(order)
    h[i % 2 == 0] = 0.0
    h[(order - 1) // 2] = 0.5
    return h / h.sum()


def decimate_halfband(x: np.ndarray, order: int) -> np.ndarray:
    h = halfband_taps(order)
    return np.convolve(x, h)[order - 1:len(x)][1::2]


def decimate_fir(x: np.ndarray, m: int, taps: int = 127) -> np.ndarray:
    """Low-pass then drop, with the group delay compensated and the filter's
    transient trimmed, so what comes back is the same steady-state stretch
    `decimate_drop` returns -- otherwise the comparison would be partly a
    measurement of edge effects."""
    h = lowpass_taps(m, taps)
    y = np.convolve(x, h)[taps - 1:len(x)]
    return y[m - 1::m]


# ---------------------------------------------------------------------------
# band bookkeeping at the oversampled rate
# ---------------------------------------------------------------------------
def band_split(x: np.ndarray, f0: float, sr: int, edge: float, guard: int = GUARD) -> dict:
    """Power of `x` split three ways: harmonics below `edge`, harmonics above
    `edge`, everything outside a harmonic guard.

    `above` is the fold-down budget: energy that exists ONLY because the
    oscillator is running at the higher rate -- at the base rate those
    harmonics are not in the signal -- and which a drop-decimator moves into
    the baseband wholesale."""
    n = len(x)
    _, X = am.spectrum(x, sr)
    p = X ** 2
    mask = np.zeros_like(p, dtype=bool)
    lo = np.zeros_like(p, dtype=bool)
    k = 1
    while k * f0 < sr / 2.0:
        c = int(round(k * f0 * n / sr))
        sl = slice(max(0, c - guard), c + guard + 1)
        mask[sl] = True
        if k * f0 < edge:
            lo[sl] = True
        k += 1
    mask[:guard + 1] = True
    lo[:guard + 1] = True
    tot = p.sum()
    above, other = p[mask & ~lo].sum(), p[~mask].sum()
    return dict(below=float(p[lo].sum() / tot), above=float(above / tot),
                inharmonic=float(other / tot),
                above_db=10 * math.log10(max(above / tot, 1e-300)),
                inharmonic_db=10 * math.log10(max(other / tot, 1e-300)))


# ---------------------------------------------------------------------------
# CHAIN A: the #80 experiment -- oscillator at 2x, last sub-step kept
# ---------------------------------------------------------------------------
def base_inc(note: int, os_max: int = 4) -> int:
    """The base-rate phase increment for `note`, forced to a multiple of
    `os_max` so inc/2 and inc/4 are EXACT.

    Without this the oversampled runs sit at a slightly different f0 from the
    base run (`int(round(inc/os))` moves it by up to half a phase LSB) and the
    comparison carries a confound it does not need. The cost is at most 2 LSB
    = 0.0057 Hz, and the base-rate readings are unchanged to 0.1 dB against the
    locked ALIAS_CURVE."""
    return int(round(dsp.phase_inc(dsp.note_hz(note)) / os_max)) * os_max


def render_osc(shape: str, inc: int, n: int, blep: bool = True) -> np.ndarray:
    return vf.OscFx(shape, blep=blep).render(n, inc).astype(np.float64) / FS


def float_blep(shape: str, inc: int, n: int) -> np.ndarray:
    """`dsp.osc_bl` -- the SAME PolyBLEP in float64: no Q1.15, no reciprocal
    approximation, no saturation. The fixed-point control."""
    ph = (inc * np.arange(n, dtype=np.int64)) & (CYCLE - 1)
    return dsp.osc_bl(shape, ph, inc)


def chain_a(note: int, shape: str = "saw", os_: int = 2) -> dict:
    """The four points issue #80 names, for one note, plus the controls that
    separate its four hypotheses.

        P1 naive oscillator, base rate        (before the correction)
        P2 PolyBLEP oscillator, base rate     (after the correction)
        P3 PolyBLEP oscillator at os_ x rate  (after upsampling, before decimation)
        P4 P3 with the last sub-step kept     (after decimation)
    """
    n = int(MEASURE["seconds"] * SR)
    inc = base_inc(note)
    f0 = inc * SR / CYCLE
    sr_hi = SR * os_
    inc_hi = inc // os_                                 # exact: base_inc is a multiple of 4

    p1 = render_osc(shape, inc, n, blep=False)
    p2 = render_osc(shape, inc, n, blep=True)
    p3 = render_osc(shape, inc_hi, n * os_, blep=True)
    p3n = render_osc(shape, inc_hi, n * os_, blep=False)
    p4 = decimate_drop(p3, os_)
    ref_lo, ref_hi = bl_saw(f0, n, SR), bl_saw(f0, n * os_, sr_hi)

    return dict(
        note=note, f0=f0, inc=inc, inc_hi=inc_hi, os=os_, shape=shape, n=n,
        floor_lo=frac(ref_lo, f0, SR, "analytic saw, base rate"),
        floor_hi=frac(ref_hi, f0, sr_hi, "analytic saw, high rate"),
        p1=frac(p1, f0, SR, "P1"), p2=frac(p2, f0, SR, "P2"),
        p3=frac(p3, f0, sr_hi, "P3"), p4=frac(p4, f0, SR, "P4"),
        p3_naive=frac(p3n, f0, sr_hi, "P3 naive"),
        rms=dict(p1=rms_db(p1), p2=rms_db(p2), p3=rms_db(p3), p4=rms_db(p4)),
        split=band_split(p3, f0, sr_hi, SR / 2.0),
        ctl_analytic=frac(decimate_drop(ref_hi, os_), f0, SR, "analytic, dropped"),
        ctl_closed_form=alias_fraction_closed_form(f0, sr_hi, os_, n),
        ctl_naive_dec=frac(decimate_drop(p3n, os_), f0, SR, "naive, dropped"),
        ctl_float=frac(decimate_drop(float_blep(shape, inc_hi, n * os_), os_), f0, SR,
                       "float PolyBLEP, dropped"),
        ctl_fir=frac(decimate_fir(p3, os_), f0, SR, "PolyBLEP, FIR-decimated"),
        # the floor for the FIR column specifically: the same decimator applied
        # to the analytic saw, which has no aliasing to remove. A `ctl_fir`
        # reading at this value is a BOUND ("nothing resolvable"), not a level.
        floor_fir=frac(decimate_fir(ref_hi, os_), f0, SR, "analytic, FIR-decimated"),
        raw=dict(p1=p1, p2=p2, p3=p3, p4=p4),
    )


def blep_normalisation(note: int, os_: int = 2) -> dict:
    """Point 2: chain A DOES change the increment, so check what that did to
    the correction rather than assuming either way.

    PolyBLEP's window is defined in units of the increment (`blep_fx` compares
    `ph < inc` and divides by `inc` through the reciprocal), so halving the
    increment should halve the window IN PHASE and leave it at exactly one
    sample on each side IN TIME, with an unchanged peak. If that holds, the
    correction was not computed for one rate and applied at another."""
    out = {}
    n = int(MEASURE["seconds"] * SR)
    for rate, inc, nn in (("base", base_inc(note), n), (f"{os_}x", base_inc(note) // os_, n * os_)):
        e, r = vf.recip_of(inc)
        # the window shape, sampled finely in phase. It is in TWO pieces -- just
        # after the wrap (ph < inc) and just before it (CYCLE - ph < inc) -- so
        # each is measured separately, as a fraction of the cycle.
        ph = np.arange(0, CYCLE, max(1, inc // 256), dtype=np.int64)
        c = vf.blep_fx(ph, inc, e, r).astype(np.float64) / FS
        lead = ph[np.nonzero(c[ph < CYCLE // 2])[0]]                 # the piece after the wrap
        trail = ph[(ph >= CYCLE // 2)][np.nonzero(c[ph >= CYCLE // 2])[0]]
        # how many RENDERED samples actually land in the window per wrap
        rph = (inc * np.arange(nn, dtype=np.int64)) & (CYCLE - 1)
        hits = int(np.count_nonzero(vf.blep_fx(rph, inc, e, r)))
        wraps = max(1.0, nn * inc / CYCLE)
        out[rate] = dict(
            inc=inc, peak=float(np.abs(c).max()),
            # the window in units of ONE SAMPLE at this rate: 1.0 on each side
            # is what PolyBLEP is defined to be, at whatever rate it runs.
            lead_samples=float((lead.max() + inc / 256) / inc) if len(lead) else 0.0,
            trail_samples=float((CYCLE - trail.min()) / inc) if len(trail) else 0.0,
            hits_per_wrap=hits / wraps)
    a, b = out["base"], out[f"{os_}x"]
    out["peak_ratio"] = b["peak"] / max(a["peak"], 1e-30)
    out["inc_ratio"] = b["inc"] / a["inc"]
    return out


# ---------------------------------------------------------------------------
# CHAIN B: the shipped path -- base-rate source, S&H, oversampled ladder,
#          last sub-step kept
# ---------------------------------------------------------------------------
# Operating point. res = 0 is deliberate for the REFERENCE comparison: the
# ladder's feedback path is a HALF-SAMPLE delay at the internal rate
# ((d1+d2)/2), so its phase at a given frequency moves with the rate and a
# higher-rate render at the same registers is a DIFFERENT FILTER. Measured
# with `voice_fx.k_onset`: the onset k at cutoff 8 kHz goes 4.769 (2x) ->
# 4.119 (16x), 13.6 %, and the self-oscillation frequency moves 8010 -> 7474 Hz
# (-120 cents). With res = 0 that path is out of the loop entirely and the only
# rate-dependent coefficient is g, which is recomputed exactly -- so the
# converged render is the same filter and can be used as a reference without
# retuning. The resonant case is reported separately, with k retuned through
# k_onset and the residual tuning difference stated.
# `drive` is bounded by an ASSERTED precondition, not by taste: at drive 4.0
# the ladder's 24-bit input word `u` overflows its rail by 0.34 dB, so the probe
# would be measuring the hard clamp rather than the nonlinearity. That was
# measured here the wrong way round first -- see `assert_drive_headroom`, which
# now REFUSES rather than reporting. 2.0 leaves 5.68 dB of headroom and still
# drives the tanh past its domain edge (u/dom = 1.04), which is the point.
LAD_CUT, LAD_RES, LAD_DRIVE = 8000, 0.0, 2.0


def assert_drive_headroom(drive: float, res: float, peak_q15: int = 26214) -> float:
    """dB of headroom the ladder's 24-bit input word has at this operating
    point. REFUSES at or below 0, because past the rail the probe measures a
    hard clamp instead of the nonlinearity it is aimed at."""
    lad = fixed.LadderFx(**vf.LADDER_CFG)
    _, gain, _ = lad.regs(res, drive)
    u = abs(fixed.shl(peak_q15 * gain, lad.SQ - fixed.SIG_Q - fixed.COEF_Q))
    rail = (1 << (lad.SB - 1)) - 1
    hr = 20.0 * math.log10(rail / max(u, 1))
    if hr <= 0.0:
        raise am.InsufficientEvidence(
            f"REFUSED: drive {drive} overflows the ladder's {lad.SB}-bit input by "
            f"{-hr:.2f} dB; this probe would measure the clamp, not the nonlinearity")
    return hr


def _ladder_regs(res=LAD_RES, drive=LAD_DRIVE, cut=LAD_CUT, os_=2):
    """The register image and the g coefficient for one operating point, with g
    computed for the INTERNAL rate `SR*os_` exactly as `LadderFx.coefficients`
    does. Passing g explicitly is what lets the same filter be run at several
    internal rates without the coefficient silently following the rate."""
    lad = fixed.LadderFx(**vf.LADDER_CFG)
    k, gain, ogain = lad.regs(res, drive)
    fs = SR * os_
    g = int(np.clip(round((1.0 - math.exp(-2.0 * math.pi * fixed.tuned_cutoff(float(cut)) / fs))
                          * (1 << fixed.COEF_Q)), 1, (1 << fixed.COEF_Q) - 1))
    return dict(k=k, gain=gain, ogain=ogain), g


def ladder_substeps(x_q15: np.ndarray, os_: int, cut=LAD_CUT, res=LAD_RES, drive=LAD_DRIVE):
    """Every internal sub-step of the shipped ladder, at rate SR*os_.

    Built by running `LadderFx` with oversample=1 over the sample-and-held
    input, with g computed for the internal rate -- which is EXACTLY the
    arithmetic `process` does inside its `for _ in range(os_)` loop, one
    sub-step per call instead of os_ per call.

    **Asserted, not assumed** (`docs/failure-modes.md`: preconditions at the
    point of use): the last sub-step of this must equal, bit for bit, what the
    real `LadderFx(oversample=os_)` returns. If it does not, this is not the
    thing that ships and the function REFUSES."""
    regs, g = _ladder_regs(res, drive, cut, os_)
    held = np.repeat(np.asarray(x_q15, dtype=np.int16), os_)
    cfg = dict(vf.LADDER_CFG); cfg["oversample"] = 1
    sub = fixed.LadderFx(**cfg).process(held, None, res, drive,
                                        g_q16=np.full(len(held), g, dtype=np.int64), **regs)
    cfg2 = dict(vf.LADDER_CFG); cfg2["oversample"] = os_
    ref = fixed.LadderFx(**cfg2).process(np.asarray(x_q15, dtype=np.int16), None, res, drive,
                                         g_q16=np.full(len(x_q15), g, dtype=np.int64), **regs)
    bad = int(np.count_nonzero(np.asarray(sub[os_ - 1::os_], dtype=np.int64)
                               - np.asarray(ref, dtype=np.int64)))
    if bad:
        raise am.InsufficientEvidence(
            f"REFUSED: the sub-step tap is not the shipping ladder -- {bad} samples differ")
    return np.asarray(sub, dtype=np.float64), np.asarray(ref, dtype=np.float64), g


def retuned_k(cut: int, res: float, os_from: int, os_to: int) -> float:
    """`res` at `os_to` that sits the same distance from self-oscillation as
    `res` does at `os_from`. Only needed when res > 0; the onset ratio comes
    from `voice_fx.k_onset`, which is the linearised loop the ROM is built
    from."""
    if res <= 0:
        return res
    return res * vf.k_onset(cut, oversample=os_to)[0] / vf.k_onset(cut, oversample=os_from)[0]


def converged_ladder(x_q15: np.ndarray, os_ref: int = 16, cut=LAD_CUT, res=LAD_RES,
                     drive=LAD_DRIVE, os_from: int = 2) -> np.ndarray:
    """The ladder's own reference: the same filter at a much higher internal
    rate, brought back with a long decimation filter.

    An analytic waveform is NOT the expected output of a driven nonlinear
    ladder, so chain B's reference is a converged higher-rate render. `g` is
    recomputed for `SR*os_ref` and `res` is retuned through `k_onset`, so the
    two runs are the same filter to the extent the linearised loop says they
    can be. `chain_b` reports the residual harmonic-amplitude difference, and
    no aliasing claim here is allowed to be smaller than it."""
    sub, _, _ = ladder_substeps(x_q15, os_ref, cut, retuned_k(cut, res, os_from, os_ref), drive)
    return decimate_fir(sub / FS, os_ref, taps=511)


def chain_b(note: int, source: str = "sine", os_: int = 2, cut=LAD_CUT, res=LAD_RES,
            drive=LAD_DRIVE) -> dict:
    """The four taps, on the SHIPPED structure.

        B1 oscillator output, base rate         -- aliasing before any rate conversion
        B2 high-rate ladder input (S&H)         -- what the sample-and-hold introduces
        B3 every ladder sub-step, internal rate -- what the nonlinearity generates
        B4 final output (last sub-step kept)    -- what reducing to output rate changes

    `source="sine"` is the controlled substitution: a clean sinusoid has no
    aliasing of its own, so everything inharmonic downstream is the ladder's.
    `source="saw"` is the real excitation, where B1 is already dirty.

    **What may and may not be compared here.** B1 -> B4 crosses a filter, so it
    mixes a spectral-SHAPE change (a lowpass removes harmonic energy, which
    raises an inharmonic FRACTION with no new aliasing) with any real aliasing.
    It is printed for completeness and is NOT quoted as a ladder aliasing
    figure. The load-bearing comparisons are the ones that hold the signal
    fixed and change only the rate reduction: B3 vs B4, B4 vs FIR(B3), and both
    against the converged reference."""
    n = int(MEASURE["seconds"] * SR)
    inc = base_inc(note)
    f0 = inc * SR / CYCLE
    sr_hi = SR * os_

    if source == "sine":
        t = np.arange(n, dtype=np.float64) * (f0 / SR)
        b1 = np.round(0.8 * np.sin(2.0 * math.pi * t) * 32767).astype(np.int16)
    else:
        b1 = (vf.OscFx(source, blep=True).render(n, inc)).astype(np.int16)
    b1f = b1.astype(np.float64) / FS

    sub, out, g = ladder_substeps(b1, os_, cut, res, drive)
    b2 = np.repeat(b1f, os_)                                # the S&H input at the internal rate
    b3, b4 = sub / FS, out / FS

    # Control: in a LINEAR path the S&H images cost exactly nothing, because
    # repeat-then-keep-the-last-sub-step is the identity. This is why the same
    # naive decimation is harmless here and ruinous in chain A.
    sh_roundtrip_exact = bool(np.array_equal(decimate_drop(b2, os_), b1f))

    ref = converged_ladder(b1, 16, cut, res, drive, os_from=os_)
    m = min(len(ref), len(b4))
    ks = [k for k in range(1, 16) if k * f0 < SR / 2 - 200]
    hp_ref = am.harmonic_powers(ref[:m], f0, ks, SR)
    hp_out = am.harmonic_powers(b4[:m], f0, ks, SR)
    # Only harmonics the reference actually HAS. A symmetric nonlinearity puts
    # nothing at the even harmonics, and a ratio of two numbers at the window
    # floor is a 20 dB swing that means nothing -- reading it as a response
    # difference is the "25 dB of separation that was window leakage" mistake.
    live = hp_ref > hp_ref[0] * 1e-6
    resp = 10.0 * np.log10(np.maximum(hp_out[live], 1e-300) / np.maximum(hp_ref[live], 1e-300))

    return dict(
        note=note, f0=f0, os=os_, source=source, g=g, n=n, cut=cut, res=res, drive=drive,
        b1=frac(b1f, f0, SR, "B1 source"),
        b2=frac(b2, f0, sr_hi, "B2 S&H input"),
        b3=frac(b3, f0, sr_hi, "B3 sub-steps"),
        b4=frac(b4, f0, SR, "B4 output"),
        ctl_bypass=frac(b1f, f0, SR, "ladder bypassed"),
        ctl_fir=frac(decimate_fir(b3, os_), f0, SR, "sub-steps, FIR-decimated"),
        ctl_converged=frac(ref, f0, SR, "converged 16x reference"),
        sh_roundtrip_exact=sh_roundtrip_exact,
        split=band_split(b3, f0, sr_hi, SR / 2.0),
        rms=dict(b1=rms_db(b1f), b3=rms_db(b3), b4=rms_db(b4), ref=rms_db(ref)),
        resp_max_db=float(np.abs(resp).max()) if len(resp) else float("nan"),
        resp=resp.tolist(), resp_harmonics=int(live.sum()), ks=ks,
        headroom_db=assert_drive_headroom(drive, res),
        raw=dict(b1=b1f, b3=b3, b4=b4, ref=ref),
    )


# ---------------------------------------------------------------------------
# estimator validation (before any number above is quoted)
# ---------------------------------------------------------------------------
def validate_estimator(notes=(40, 64, 100), shares=(-20.0, -30.0, -40.0)) -> list:
    """Three independent checks on `inharmonic_fraction_db`, in the regime the
    acceptance suite actually uses it (hundreds of harmonics, not eleven):

      1. a dense analytic band-limited saw, which has no inharmonic content --
         the reading is the floor and must be well below any quoted number;
      2. that saw plus a planted comb carrying a KNOWN share -- read back
         within 0.5 dB once the floor is added in;
      3. a drop-decimated analytic saw, whose alias content is known in closed
         form from the Fourier coefficients with no FFT involved.

    Six estimator bugs were found the day this module's ground truth was
    written and eight-plus measurements have been withdrawn here. No number
    from this file is quoted before these pass."""
    n = int(MEASURE["seconds"] * SR)
    rows = []
    for note in notes:
        f0 = base_inc(note) * SR / CYCLE
        clean = bl_saw(f0, n, SR)
        floor = frac(clean, f0, SR, "clean")
        floor_lin = 10 ** (floor / 10.0)
        for s_db in shares:
            s = 10 ** (s_db / 10.0)
            got = frac(plant_inharmonics(clean, f0, SR, s), f0, SR, "planted")
            want = 10.0 * math.log10(s + floor_lin * (1 - s))
            rows.append(dict(kind="planted", note=note, f0=f0, floor=floor,
                             want=want, got=got, err=got - want))
        hi = bl_saw(f0, 2 * n, 2 * SR)
        cf = alias_fraction_closed_form(f0, 2 * SR, 2, n)
        got = frac(decimate_drop(hi, 2), f0, SR, "dropped analytic")
        rows.append(dict(kind="closed-form", note=note, f0=f0, floor=floor,
                         want=cf["db"], got=got, err=got - cf["db"]))
    return rows


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------
def report_validation(rows) -> bool:
    print("## 0. estimator validation (no number below is quoted until this passes)")
    print(f"{'kind':>12} {'note':>5} {'floor':>7} {'want':>8} {'got':>8} {'err':>7}")
    ok = True
    for r in rows:
        tol = 0.5 if r["kind"] == "planted" else 1.0
        bad = abs(r["err"]) > tol
        ok &= not bad
        print(f"{r['kind']:>12} {r['note']:>5} {r['floor']:>7.1f} {r['want']:>8.1f} "
              f"{r['got']:>8.1f} {r['err']:>7.2f}{'   <-- OUT OF TOLERANCE' if bad else ''}")
    print(f"   -> {'PASS' if ok else 'FAIL'}\n")
    return ok


def report_chain_a(rows, os_):
    print(f"## 1. CHAIN A -- oscillator at {os_}x, last sub-step kept (the #80 experiment)")
    print("   P1 naive@48k  P2 blep@48k  P3 blep@{0}k  P4 = P3 with every {1}th sample kept"
          .format(48 * os_, os_))
    print("   above = share of P3's power in HARMONICS above 24 kHz (the fold-down budget)")
    print("   cf    = closed form for a drop-decimated analytic band-limited saw")
    print("   fir   = P3 through a 127-tap float decimator (diagnostic, not a proposal)")
    print(f"{'note':>4} {'f0':>9} {'floor':>7} {'P1':>7} {'P2':>7} {'P3':>7} {'P4':>7} "
          f"{'P2-P4':>7} {'above':>8} {'cf':>8} {'anal':>8} {'fir':>7} {'firflr':>7} {'rmsP2':>7} {'rmsP4':>7}")
    for d in rows:
        print(f"{d['note']:>4} {d['f0']:>9.2f} {d['floor_lo']:>7.1f} {d['p1']:>7.1f} "
              f"{d['p2']:>7.1f} {d['p3']:>7.1f} {d['p4']:>7.1f} {d['p2'] - d['p4']:>7.1f} "
              f"{d['split']['above_db']:>8.1f} {d['ctl_closed_form']['db']:>8.1f} "
              f"{d['ctl_analytic']:>8.1f} {d['ctl_fir']:>7.1f} {d['floor_fir']:>7.1f} "
              f"{d['rms']['p2']:>7.2f} {d['rms']['p4']:>7.2f}")
    print("\n   controls")
    for d in rows:
        print(f"   note {d['note']:>4}: PolyBLEP gain {d['p1'] - d['p2']:>5.1f} dB at 48k, "
              f"{d['p3_naive'] - d['p3']:>5.1f} dB at {48*os_}k | dropped: "
              f"blep {d['p4']:>6.1f}  naive {d['ctl_naive_dec']:>6.1f}  "
              f"float-blep {d['ctl_float']:>6.1f}  analytic {d['ctl_analytic']:>6.1f}")
    print()


def report_blep_normalisation(notes, os_):
    print("## 2. CHAIN A control -- was the PolyBLEP correction rate-mismatched?")
    print("   Hypothesis 1 of #80. The window is defined in units of inc (`blep_fx`")
    print("   compares ph < inc and divides by inc), so at ANY rate it should be one")
    print("   sample on each side with an unchanged peak. lead/trail are the window's")
    print("   two pieces in units of one sample AT THAT RATE; hits is how many rendered")
    print("   samples land in it per wrap. Anything but 1.00 / 1.00 / ~2.0 / peak 1.0")
    print("   at both rates would be a rate mismatch.")
    print(f"{'note':>4} {'inc_base':>10} {'inc_hi':>8} {'pk_base':>8} {'pk_hi':>7} {'pk_rat':>7} "
          f"{'lead_b':>7} {'trail_b':>8} {'lead_h':>7} {'trail_h':>8} {'hits_b':>7} {'hits_h':>7}")
    for note in notes:
        d = blep_normalisation(note, os_)
        a, b = d["base"], d[f"{os_}x"]
        print(f"{note:>4} {a['inc']:>10} {b['inc']:>8} {a['peak']:>8.5f} {b['peak']:>7.5f} "
              f"{d['peak_ratio']:>7.4f} {a['lead_samples']:>7.3f} {a['trail_samples']:>8.3f} "
              f"{b['lead_samples']:>7.3f} {b['trail_samples']:>8.3f} "
              f"{a['hits_per_wrap']:>7.3f} {b['hits_per_wrap']:>7.3f}")
    print()


def report_chain_b(rows, os_, title=""):
    d0 = rows[0]
    print(f"## 3. CHAIN B{title} -- the SHIPPED path: base-rate source -> S&H "
          f"-> {os_}x ladder -> last sub-step")
    print(f"   operating point: cutoff {d0['cut']} Hz, res {d0['res']}, drive {d0['drive']}")
    print("   B1 source@48k  B2 S&H input@{0}k  B3 sub-steps@{0}k  B4 output@48k"
          .format(48 * os_))
    print("   above = share of B3's power in harmonics above 24 kHz (the fold-down budget)")
    print("   fir   = B3 through a 127-tap decimator; conv = the same filter at 16x, decimated")
    print("   resp  = worst harmonic-amplitude difference B4 vs conv over the nh harmonics")
    print("           the reference actually has. NO aliasing claim here may be smaller.")
    print("   hdrm  = dB of headroom on the ladder's 24-bit input word; <= 0 is REFUSED")
    print("   NOTE  B1 -> B4 crosses a filter and so mixes a spectral-SHAPE change with")
    print("         aliasing; it is NOT quoted. B3 vs B4 and B4 vs fir hold the signal")
    print("         fixed and are the load-bearing comparisons.")
    print(f"{'note':>4} {'f0':>9} {'src':>6} {'B1':>7} {'B2':>7} {'B3':>7} {'B4':>7} "
          f"{'B3-B4':>7} {'fir':>7} {'conv':>7} {'above':>8} {'resp':>6} {'nh':>3} "
          f"{'hdrm':>6} {'S&H=id':>7}")
    for d in rows:
        print(f"{d['note']:>4} {d['f0']:>9.2f} {d['source']:>6} {d['b1']:>7.1f} {d['b2']:>7.1f} "
              f"{d['b3']:>7.1f} {d['b4']:>7.1f} {d['b3'] - d['b4']:>7.2f} {d['ctl_fir']:>7.1f} "
              f"{d['ctl_converged']:>7.1f} {d['split']['above_db']:>8.1f} "
              f"{d['resp_max_db']:>6.2f} {d['resp_harmonics']:>3} "
              f"{d['headroom_db']:>6.2f} {str(d['sh_roundtrip_exact']):>7}")
    print()


def decimator_order_sweep(notes=(40, 64, 100), orders=(3, 7, 11, 15, 23, 31, 47, 63)) -> list:
    """How much of chain A's regression a decimator of each length recovers.

    A BOUND, in float64, on a question the fix task has to answer properly. The
    monotonicity check is not decoration: a sizing study was withdrawn from this
    repository for reporting worse suppression from a longer kernel, which is
    impossible, and the only reason it was caught is that somebody looked."""
    n = int(MEASURE["seconds"] * SR)
    rows = []
    for note in notes:
        inc = base_inc(note)
        f0 = inc * SR / CYCLE
        p2 = render_osc("saw", inc, n, blep=True)
        p3 = render_osc("saw", inc // 2, n * 2, blep=True)
        row = dict(note=note, f0=f0, p2=frac(p2, f0, SR), drop=frac(decimate_drop(p3, 2), f0, SR),
                   floor=frac(bl_saw(f0, n, SR), f0, SR), by_order={})
        for o in orders:
            row["by_order"][o] = frac(decimate_halfband(p3, o), f0, SR, f"halfband {o}")
        vals = [row["by_order"][o] for o in orders]
        # monotone to within the estimator's own resolution; a longer kernel
        # cannot suppress less.
        row["monotone"] = all(b <= a + 0.5 for a, b in zip(vals, vals[1:]))
        rows.append(row)
    return rows


def report_decimator_orders(rows, orders=(3, 7, 11, 15, 23, 31, 47, 63)):
    print("## 4. A BOUND on the decimator the explanation implies (float64, not a proposal)")
    print("   chain A's P3 brought to 48 kHz by a half-band FIR of each order, against")
    print("   P2 (the base-rate PolyBLEP oscillator we ship) and against drop-decimation.")
    print("   floor = the estimator's floor; a reading there is 'nothing resolvable'.")
    print(f"{'note':>4} {'P2':>7} {'drop':>7} " + " ".join(f"{('hb%d' % o):>7}" for o in orders)
          + f" {'floor':>7} {'mono':>5}")
    ok = True
    for r in rows:
        ok &= r["monotone"]
        print(f"{r['note']:>4} {r['p2']:>7.1f} {r['drop']:>7.1f} "
              + " ".join(f"{r['by_order'][o]:>7.1f}" for o in orders)
              + f" {r['floor']:>7.1f} {str(r['monotone']):>5}")
    if not ok:
        print("   !! NON-MONOTONE: a longer kernel suppressed less. This table is WITHDRAWN.")
    print()
    return ok


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--shape", default="saw")
    ap.add_argument("--os", type=int, default=2, dest="os_")
    ap.add_argument("--out", default=None, help="artefact directory (unique per run)")
    a = ap.parse_args(argv)

    prov = provenance()
    out = a.out or os.path.join("build", "alias",
                                f"{prov['commit'][:8]}{'-dirty-' + prov['diff_sha256'] if prov['dirty'] else ''}"
                                f"-os{a.os_}-{a.shape}")
    os.makedirs(out, exist_ok=True)
    print(f"# alias_probe: commit {prov['commit'][:12]} "
          f"{'DIRTY(' + prov['diff_sha256'] + ')' if prov['dirty'] else 'clean'} "
          f"| {prov['utc']} | artefacts -> {out}")
    print(f"# metric held fixed: {MEASURE}\n")

    vrows = validate_estimator()
    if not report_validation(vrows):
        print("REFUSED: the estimator did not reproduce a known alias content. "
              "No measurement below is quoted.")
        return 1

    arows = [chain_a(n, a.shape, a.os_) for n in NOTES]
    report_chain_a(arows, a.os_)
    report_blep_normalisation(NOTES, a.os_)
    brows = [chain_b(n, "sine", a.os_) for n in NOTES]
    report_chain_b(brows, a.os_, " (sine drive, res 0)")
    brows_saw = [chain_b(n, "saw", a.os_) for n in (40, 64, 100)]
    report_chain_b(brows_saw, a.os_, " (saw drive, res 0)")
    brows_res = [chain_b(n, "sine", a.os_, res=0.85) for n in (40, 64, 100)]
    report_chain_b(brows_res, a.os_, " (sine drive, res 0.85 -- k retuned for the reference)")
    drows = decimator_order_sweep()
    if not report_decimator_orders(drows):
        drows = [dict(withdrawn="non-monotone")]

    def strip(d):
        return {k: v for k, v in d.items() if k != "raw"}
    for d in arows:
        np.savez_compressed(os.path.join(out, f"chainA-note{d['note']}.npz"), **d["raw"])
    for tag, rs in (("sine", brows), ("saw", brows_saw), ("res", brows_res)):
        for d in rs:
            np.savez_compressed(os.path.join(out, f"chainB-{tag}-note{d['note']}.npz"),
                                **d["raw"])
    json.dump(dict(provenance=prov, validation=vrows,
                   chain_a=[strip(d) for d in arows],
                   chain_b_sine=[strip(d) for d in brows],
                   chain_b_saw=[strip(d) for d in brows_saw],
                   chain_b_resonant=[strip(d) for d in brows_res],
                   decimator_orders=drows),
              open(os.path.join(out, "alias_probe.json"), "w"), indent=1)
    print(f"# wrote {out}/alias_probe.json and the raw signals beside it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
