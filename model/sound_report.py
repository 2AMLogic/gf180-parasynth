#!/usr/bin/env python3
"""Per-voice, per-property measurement report for any commit that changes the sound.

    .venv/bin/python model/sound_report.py                    # measure and report
    .venv/bin/python model/sound_report.py --inject sd-noise-6db
    .venv/bin/python model/sound_report.py --list-injections
    .venv/bin/python model/sound_report.py --changed origin/main   # which voices moved
    .venv/bin/python model/sound_report.py --relock            # print a fresh lock table

The point: an agent working on a voice should receive a **concrete, measured
statement** -- "the snare's noise share is 12 pp low", "the open hat's decay is
94 ms short" -- with the numbers behind it, rather than a red dot. Every line
below says what was measured, what it was measured against, where that came
from, and by how much it is out in the property's own unit.

Three rules this file enforces on itself:

  * **Per voice and per behaviour, never one number.** There is no overall
    score and `--score` is not a flag. An aggregate lets an improvement to the
    kick hide a worse snare, which is the specific thing this report exists to
    prevent.
  * **Every target says where it comes from.** A `target` is a figure some
    document or decision record states, and being outside it means the model
    is WRONG. A `lock` is the value this model measured at a named commit, and
    being outside it means the model CHANGED -- which may be the point of the
    commit. The two are never mixed in one verdict.
  * **A report that cannot go red is decoration.** `--inject` applies a
    known-broken variant and asserts the relevant properties turn red. It also
    prints the properties that did NOT move, because a defect nothing measures
    is a hole in the coverage and is more useful printed than hidden. (This
    repository has shipped a "negative control" that mutated a function
    signature into invalid Python and passed, proving only that Python rejects
    syntax errors.)

Estimators come from `model/audio_measure.py` and `model/drum_verify.py`, both
ground-truthed against closed-form signals; nothing here invents one.

**Why there is no FAD score here.** Frechet Audio Distance is a reasonable
supplement to a table like this one and a bad replacement for it. Its value
depends on the sample count, the embedding, and which reference set it is
computed against -- Gui et al., "Adapting Frechet Audio Distance for
Generative Music Evaluation" (arXiv:2311.01616) -- so a single FAD number
cannot say *which voice* moved or *which property* of it, which is the whole
job of this file. If FAD is added later it goes in a column BESIDE these rows,
never instead of them, and it states its embedding and its sample count on the
same line as its value.
"""
from __future__ import annotations

import argparse
import math
import os
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "audition"))

import audio_measure as am                                          # noqa: E402
import drum_verify as dv                                            # noqa: E402
import drums_fx as dx                                               # noqa: E402
import drums_fx_render as dr                                        # noqa: E402
import reference_rigs as rr                                         # noqa: E402

SR = rr.SR


# ===========================================================================
# the property table
# ===========================================================================
@dataclass
class Prop:
    voice: str
    name: str
    unit: str
    kind: str                 # "target" (a document says so) or "lock" (this model, pinned)
    value: float              # the target or the locked value
    tol: float                # +- this, in `unit`
    source: str
    fn: Callable              # ctx -> float or None
    note: str = ""


@dataclass
class Result:
    prop: Prop
    measured: float | None
    ok: bool
    why: str = ""
    delta: float = 0.0

    def sentence(self) -> str:
        """The measured statement an agent can act on."""
        if self.measured is None:
            return f"{self.prop.voice} {self.prop.name}: NOT MEASURABLE -- {self.why}"
        d = self.measured - self.prop.value
        direction = "high" if d > 0 else "low"
        word = "target" if self.prop.kind == "target" else "the locked value"
        if self.ok:
            return (f"{self.prop.voice} {self.prop.name} is {self.measured:.4g} "
                    f"{self.prop.unit}, within {self.prop.tol:g} of {word} "
                    f"{self.prop.value:.4g}")
        return (f"{self.prop.voice} {self.prop.name} is {abs(d):.4g} {self.prop.unit} "
                f"{direction}: measured {self.measured:.4g}, {word} "
                f"{self.prop.value:.4g} +- {self.prop.tol:g} "
                f"({self.prop.source})")


# ---------------------------------------------------------------- ladder ------
CUTS = [100.0, 200.0, 400.0, 800.0, 1600.0, 3200.0, 6400.0]
FREQS = np.geomspace(40.0, 12000.0, 28)


def _ladder(ctx):
    return ctx.get("ladder_factory", rr.OurLadder)()


def _ladder_curves(ctx):
    if "curves" not in ctx:
        lad = _ladder(ctx)
        ctx["curves"] = {c: np.asarray(lad.tone_gain_db(FREQS, c, 0.2, 0.25)) for c in CUTS}
    return ctx["curves"]


def _corner(ctx, cut):
    g = _ladder_curves(ctx)[cut]
    e = am.corner_from_curve(FREQS, g, ref_band=(FREQS[0], max(100.0, cut * 0.25)))
    return e.value if e.ok else None


def m_corner_spread(ctx):
    """How much the measured corner / commanded cutoff ratio DRIFTS across the
    range. A constant ratio is a definition; a drifting one is a cutoff
    control that means different things at its two ends."""
    r = [(_corner(ctx, c) or float("nan")) / c for c in CUTS]
    r = [v for v in r if np.isfinite(v)]
    return 100.0 * (max(r) - min(r)) / np.mean(r) if len(r) > 3 else None


def m_corner_800(ctx):
    """Measured -3 dB corner at a commanded 800 Hz, as a ratio. Not expected
    to be 1 -- a 4-pole cascade is -3 dB at 0.435 of its pole frequency and
    resonance lifts that back up -- so it is a lock, not a target. It exists
    because `--inject ladder-cut30` showed the drift metric BLIND to a uniform
    30 % cutoff error: a skew that moves every corner equally does not change
    how much they differ from each other, by construction."""
    k = _corner(ctx, 800.0)
    return None if not k else k / 800.0


def m_slope(ctx):
    g = _ladder_curves(ctx)[400.0]
    k = _corner(ctx, 400.0)
    if not k:
        return None
    e = am.slope_db_oct(FREQS, g, (2.2 * k, min(7.0 * k, 9000.0)))
    return e.value if e.ok else None


def m_peak_small_signal(ctx):
    lad = _ladder(ctx)
    g = np.asarray(lad.tone_gain_db(FREQS, 800.0, 0.9, 10 ** (-54 / 20.0)))
    e = am.peak_from_curve(FREQS, g, ref_band=(FREQS[0], 200.0))
    return e.value if e.ok else None


def _ring_sig(ctx, cut=261.6, res=1.3):
    key = ("sig", cut, res)
    if key not in ctx:
        try:
            ctx[key] = am.harmonic_signature(_ladder(ctx).ring(cut, res, seconds=1.0), SR)
        except am.InsufficientEvidence:
            ctx[key] = None
    return ctx[key]


def m_selfosc_h3(ctx):
    s = _ring_sig(ctx)
    return None if not s else s.get("h3")


def m_selfosc_spread(ctx):
    s = _ring_sig(ctx)
    if not s or s.get("h3") is None or s.get("h5") is None:
        return None
    return s["h5"] - s["h3"]


def m_track_spread(ctx):
    """Spread of f_osc / commanded cutoff across six octaves, in percentage
    points. docs/discrimination.md section 8.4: ours 7.92 pp against Surge's
    0.62 and 1.28, and Huovilainen's fcr polynomial is the identified fix."""
    lad = _ladder(ctx)
    errs = []
    for c in (200.0, 800.0, 3200.0, 6400.0):
        y = lad.ring(c, 1.05, seconds=0.6)
        z = am.zero_crossing_frequency(y, SR)
        d = am.dominant_frequency(y, c * 0.4, c * 2.2, SR)
        f = z.value if (z.ok and d.ok and abs(z.value - d.value) / d.value < 0.03) else \
            (d.value if d.ok else None)
        if f:
            errs.append((f / c - 1) * 100)
    return (max(errs) - min(errs)) if len(errs) >= 3 else None


# ---------------------------------------------------------------- drums -------
def _solo(ctx, voice):
    """One hit of one voice at accent 1.0, through the whole drum path."""
    key = ("solo", voice)
    if key not in ctx:
        s = dx.STOP_NAMES.index(voice)
        kit = ctx.get("kit_factory", dx.kit_808)()
        out, _ = dr.drums_only([(int(0.02 * SR), s, 1.0)], 1.2, kit=kit)
        ctx[key] = np.asarray(out, dtype=np.float64) / 32768.0
    return ctx[key]


def _meas(ctx, voice):
    key = ("meas", voice)
    if key not in ctx:
        ctx[key] = dv.measure(_solo(ctx, voice), SR, voice)
    return ctx[key]


def m_drum_f0(voice):
    def f(ctx):
        m = _meas(ctx, voice)
        return None if m.get("silent") else m.get("peak_hz")
    return f


def m_drum_tau(voice):
    def f(ctx):
        m = _meas(ctx, voice)
        v = None if m.get("silent") else m.get("tau_ms")
        return None if (v is None or not np.isfinite(v)) else v
    return f


def m_drum_t20(voice):
    def f(ctx):
        m = _meas(ctx, voice)
        v = None if m.get("silent") else m.get("t20_ms")
        return None if (v is None or not np.isfinite(v)) else v
    return f


def m_drum_attack(voice):
    def f(ctx):
        m = _meas(ctx, voice)
        v = None if m.get("silent") else m.get("attack_ms")
        return None if (v is None or not np.isfinite(v)) else v
    return f


def m_noise_share(voice, bands):
    """Share of the hit that is NOT the fitted damped modes, as a percentage.
    `drum_fit.noise_share` -- validated against known truth, and the
    replacement for the withdrawn windowed energy split (drum-verification 8.0,
    which read 1.2 % where the truth was 18.6 %)."""
    def f(ctx):
        import drum_fit
        try:
            return 100.0 * drum_fit.noise_share(_solo(ctx, voice), SR, bands)["share"]
        except Exception:
            return None
    return f


def m_sd_brightness(ctx):
    """The snare's brightness, power-weighted by default -- `ctx["centroid_weight"]`
    lets `--inject sd-centroid-amp-weighted` read the other field instead
    (`drum_verify.measure` already computes both). docs/drum-verification.md 3:
    the amplitude/magnitude weighting 'weights a wide, quiet noise floor
    heavily,' which is exactly the snare's shape -- a ~173 Hz body under a
    16 kHz-wide noise band."""
    m = _meas(ctx, "SD")
    if m.get("silent"):
        return None
    field = "centroid_hz" if ctx.get("centroid_weight") == "amplitude" else "power_centroid_hz"
    return m.get(field)


# ===========================================================================
# injections: the known-broken variants the report must be able to see
# ===========================================================================
def _ladder_injection(**kw):
    def make(ctx):
        ctx["ladder_factory"] = lambda: rr.OurLadder("injected", **kw)
    return make


def _mode_injection(mode_attr, **scale):
    """Scale one body mode's f0, Q or amplitude as `kit_808()` writes it.
    Patches `drums_fx.mode_writes` rather than editing the kit's numbers, so
    the injection keeps working when the kit's numbers change."""
    def make(ctx):
        mid = getattr(dx, mode_attr)
        orig = dx.mode_writes

        def patched(m, f0_hz, q, amp, num=dx.RAW):
            if m == mid:
                f0_hz *= scale.get("f0", 1.0)
                q *= scale.get("q", 1.0)
                amp *= scale.get("amp", 1.0)
            return orig(m, f0_hz, q, amp, num)
        ctx["_patches"].append((dx, "mode_writes", orig))
        dx.mode_writes = patched
    return make


def _env_injection(env_attr, **scale):
    """Scale one envelope's tau or peak as `kit_808()` writes it."""
    def make(ctx):
        eid = getattr(dx, env_attr)
        orig = dx.env_writes

        def patched(e, stop, tau_s, peak, **kw):
            if e == eid:
                tau_s *= scale.get("tau", 1.0)
                peak *= scale.get("peak", 1.0)
            return orig(e, stop, tau_s, peak, **kw)
        ctx["_patches"].append((dx, "env_writes", orig))
        dx.env_writes = patched
    return make


def _envelope_injection(kind):
    """Replace `drum_verify`'s tuned per-voice envelope (moving RMS, window
    matched to each voice's own fundamental) with ONE method for every voice --
    which is what this repository shipped before ENV_WIN_MS existed. `kind`
    selects the historical replacement:

      "moving_average"  the deprecated `audio_measure.moving_average_envelope`
                         at its original 5 ms window -- audio_measure.py's own
                         rule 1: 'a 5 ms moving average spans 0.28 of a cycle
                         at 56 Hz.' Kept alive ONLY as
                         `test_moving_average_envelope_ripples_where_the_analytic_one_does_not`
                         until this injection, which reinstates it on the
                         acceptance path rather than a helper-function test.

    Patches the module attribute, so every voice's `decay_fit`-derived
    property is exposed to it -- not only the one this injection's declared
    `voices` list names, which is why the un-declared voices' movement (or
    lack of it) is worth reading in the printed report too."""
    def make(ctx):
        orig = dv.envelope

        def patched(x, sr, win_ms=4.0):
            if kind == "moving_average":
                return np.abs(am.moving_average_envelope(x, 5.0, sr))
            return orig(x, sr, win_ms)
        ctx["_patches"].append((dv, "envelope", orig))
        dv.envelope = patched
    return make


def _centroid_weight_injection(weight):
    """Read `m_sd_brightness` (and any other centroid-based property added
    later) through the given weighting instead of the correct default."""
    def make(ctx):
        ctx["centroid_weight"] = weight
    return make


INJECTIONS = {
    "ladder-2pole": ("a pole dropped from the ladder (2 stages, not 4)",
                     ["LADDER"], _ladder_injection(stages=2)),
    "ladder-1tanh": ("the linearised structure DR 0001 rejected: four linear poles, "
                     "one saturating element in the feedback",
                     ["LADDER"], _ladder_injection(nonlin="feedback")),
    "ladder-cut30": ("the cutoff ROM read 30 % high",
                     ["LADDER"], _ladder_injection(cut_skew=1.3)),
    "ladder-tanh4": ("the tanh table cut from 16 entries to 4",
                     ["LADDER"], _ladder_injection(cfg=dict(tanh_entries=4))),
    "sd-noise-6db": ("the snare's noise (snappy) envelope peak 6 dB low",
                     ["SD"], _env_injection("E_SDN", peak=0.5)),
    "sd-detune": ("the snare's low body mode 15 % sharp",
                  ["SD"], _mode_injection("M_SDLO", f0=1.15)),
    "bd-decay-short": ("the kick's body Q 40 % low, so it decays 40 % faster",
                       ["BD"], _mode_injection("M_BD", q=0.6)),
    "lt-tune": ("the low tom 15 % sharp",
                ["LT"], _mode_injection("M_LT", f0=1.15)),
    "oh-decay-half": ("the open hat's envelope tau halved",
                      ["OH"], _env_injection("E_OH", tau=0.5)),
    # -- permanent injections of historical bugs (issue #52): these two
    # reinstate defects that actually shipped and were fixed at the estimator
    # layer, rather than inventing a new defect, per docs/verification-rules.md
    # ("SPI_ADDR7"/"SPI_DATA24" are the RTL-side precedent for this pattern).
    "bd-ma-envelope": ("the decay estimator reads every voice through the "
                       "deprecated 5 ms moving-average envelope instead of "
                       "the per-voice-tuned RMS window -- the historical "
                       "defect audio_measure.py rule 1 and "
                       "test_moving_average_envelope_ripples_where_the_analytic_one_does_not "
                       "describe, reinstated here on the acceptance path",
                       ["BD"], _envelope_injection("moving_average")),
    "sd-centroid-amp-weighted": ("the snare's brightness is read from the "
                                 "amplitude/magnitude-weighted centroid "
                                 "instead of the power-weighted one -- "
                                 "docs/drum-verification.md 3's withdrawn "
                                 "measurement method, reinstated here",
                                 ["SD"], _centroid_weight_injection("amplitude")),
}


# ===========================================================================
# the declared properties
#
# kind="target": a figure a document states. Outside it means the model is
#                WRONG, and the source column says who to argue with.
# kind="lock":   what this model measured at the commit named in `source`.
#                Outside it means the model CHANGED, which may be intended --
#                the commit that changes it re-locks it and says why.
# ===========================================================================
LOCK = "ce400a6"          # the commit the locks below were measured at


def build_properties():
    P = []
    # -- the ladder ---------------------------------------------------------
    P += [
        Prop("LADDER", "stopband slope", "dB/oct", "target", -21.6, 2.0,
             "DR 0001 / DESIGN.md 5: 24 dB/oct. An ideal 4-pole gives -17.6 dB/oct "
             "over 2.2-7x its corner (discrimination.md 8.6), so the target is the "
             "band-corrected figure, not the asymptote.", m_slope),
        Prop("LADDER", "small-signal resonant peak", "dB", "target", 22.0, 4.0,
             "discrimination.md 8.6: Surge 22.0/21.9, Diva 17.7, Mini V3 14.1 at "
             "0.9 of their own thresholds; ours must stay inside that spread.",
             m_peak_small_signal),
        Prop("LADDER", "self-oscillation tuning spread", "pp", "target", 0.0, 9.0,
             "contract 17.12, open: measured 7.92 pp over six octaves against "
             "Surge's 0.62 and 1.28 (discrimination.md 8.4). The tolerance is set "
             "ABOVE today's value on purpose -- it is a regression guard on a known "
             "defect, not a claim that 9 pp is acceptable.", m_track_spread),
        Prop("LADDER", "corner at 800 Hz / commanded", "ratio", "lock", None, 0.05,
             f"locked at {LOCK}; absolute cutoff accuracy, which the drift metric "
             "below is blind to for a uniform error", m_corner_800),
        Prop("LADDER", "corner ratio drift", "%", "lock", None, 3.0,
             f"locked at {LOCK}; NON-uniformity of the cutoff scaling -- this is the "
             "one contract 17.12 is about (discrimination.md 8.4)", m_corner_spread),
        Prop("LADDER", "h3 at self-oscillation (res 1.3)", "dB", "lock", None, 3.0,
             f"locked at {LOCK}; inside the references' -36..-50 dB "
             "(discrimination.md 8.5)", m_selfosc_h3),
        Prop("LADDER", "h5-h3 at self-oscillation (res 1.3)", "dB", "lock", None, 6.0,
             f"locked at {LOCK}; dominated by the 16-entry tanh table, not by the "
             "structure (discrimination.md 8.5)", m_selfosc_spread),
    ]
    # -- the drum voices ----------------------------------------------------
    for v in dv.STOPS:
        spec = dv.SPEC[v]
        if spec.get("f0"):
            P.append(Prop(v, "fundamental", "Hz", "target", spec["f0"],
                          max(2.0, 0.04 * spec["f0"]),
                          "docs/tr808-reference.md 12/14 via drum_verify.SPEC",
                          m_drum_f0(v)))
        P.append(Prop(v, "decay tau", "ms", "target", spec["tau_ms"],
                      max(4.0, 0.25 * spec["tau_ms"]),
                      "docs/tr808-reference.md 12/14 via drum_verify.SPEC; tau, "
                      "not T20 -- the chart's column is chart_ms and is different",
                      m_drum_tau(v)))
        P.append(Prop(v, "T20", "ms", "lock", None, max(8.0, 0.25 * spec["tau_ms"]),
                      f"locked at {LOCK}; comparable with Roland's chart column "
                      f"({spec['chart_ms']:.0f} ms)", m_drum_t20(v)))
        P.append(Prop(v, "attack", "ms", "lock", None, 3.0, f"locked at {LOCK}",
                      m_drum_attack(v)))
    P.append(Prop("SD", "noise share", "%", "lock", None, 8.0,
                  f"locked at {LOCK}; drum_fit.noise_share, the validated measure "
                  "(drum-verification.md 8.0 withdrew the windowed split)",
                  m_noise_share("SD", [(150.0, 200.0), (300.0, 380.0)])))
    P.append(Prop("SD", "brightness (power centroid)", "Hz", "lock", None, 300.0,
                  f"locked at {LOCK}; drum_verify.power_centroid_hz, over the "
                  "amplitude/magnitude-weighted centroid_hz drum-verification.md "
                  "3 withdrew ('a voice with 98 % of its energy below 700 Hz can "
                  "still show a 6.5 kHz magnitude centroid') -- ground truth in "
                  "test_audio_measure.test_amplitude_weighted_centroid_reads_a_quiet_wideband_floor_as_bright",
                  m_sd_brightness))
    return P


# Locked values, measured on LOCK. `--relock` prints a fresh block to paste
# here. A commit that intends to move one of these re-locks it and says why in
# its message; a commit that moves one without meaning to is what this table
# is for.
LOCKS = {
    ("LADDER", "corner at 800 Hz / commanded"): 0.786039,
    ("LADDER", "corner ratio drift"): 9.40462,
    ("LADDER", "h3 at self-oscillation (res 1.3)"): -45.3762,
    ("LADDER", "h5-h3 at self-oscillation (res 1.3)"): -13.7836,
    ("BD", "T20"): 307.979,
    ("BD", "attack"): 14.5625,
    ("SD", "T20"): 56.3958,
    ("SD", "attack"): 4.27083,
    ("LT", "T20"): 198.229,
    ("LT", "attack"): 16.625,
    ("HT", "T20"): 89.3125,
    ("HT", "attack"): 4.20833,
    ("CH", "T20"): 42.5417,
    ("CH", "attack"): 2.54167,
    ("OH", "T20"): 312.083,
    ("OH", "attack"): 4.39583,
    ("CP", "T20"): 40.6042,
    ("CP", "attack"): 3.0,
    ("CB", "T20"): 176.125,
    ("CB", "attack"): 4.39583,
    ("SD", "noise share"): 27.5488,
    ("SD", "brightness (power centroid)"): 1918.08,
}


# ===========================================================================
# running
# ===========================================================================
def run(inject=None):
    ctx = {"_patches": []}
    touched = []
    if inject:
        _, touched, make = INJECTIONS[inject]
        if make.__code__.co_argcount:
            make(ctx)
        ctx["kit_factory"] = dx.kit_808
    props = build_properties()
    for p in props:
        if p.kind == "lock":
            p.value = LOCKS.get((p.voice, p.name))
    out = []
    try:
        for p in props:
            try:
                v = p.fn(ctx)
                why = "" if v is not None else "the estimator refused"
            except Exception as e:                                  # noqa: BLE001
                v, why = None, f"{type(e).__name__}: {e}"
            if v is None:
                out.append(Result(p, None, False, why))
            elif p.value is None:
                out.append(Result(p, v, True, "no lock recorded yet"))
            else:
                d = v - p.value
                out.append(Result(p, v, abs(d) <= p.tol, "", d))
    finally:
        for mod, attr, orig in ctx["_patches"]:
            setattr(mod, attr, orig)
    return out, touched


def print_report(results, title):
    print("=" * 94)
    print(title)
    print("=" * 94)
    voices = []
    for r in results:
        if r.prop.voice not in voices:
            voices.append(r.prop.voice)
    nfail = 0
    for v in voices:
        rows = [r for r in results if r.prop.voice == v]
        bad = [r for r in rows if not r.ok]
        nfail += len(bad)
        print(f"\n-- {v}  ({len(rows) - len(bad)}/{len(rows)} within tolerance)")
        for r in rows:
            p = r.prop
            mark = "ok  " if r.ok else "OUT "
            m = "        --" if r.measured is None else f"{r.measured:10.4g}"
            t = "     (none)" if p.value is None else f"{p.value:10.4g}"
            print(f"   {mark} {p.name:34s} {m} {p.unit:6s} vs {t} +-{p.tol:<7.3g}"
                  f" [{p.kind}]")
            if not r.ok:
                print(f"        -> {r.sentence()}")
    print(f"\n{nfail} properties outside tolerance, across {len(voices)} voices.")
    print("There is deliberately no overall score: an aggregate lets a better kick hide")
    print("a worse snare, which is the thing this report exists to prevent.")
    return nfail


def cmd_inject(name):
    base, _ = run()
    inj, touched = run(name)
    desc, voices, _ = INJECTIONS[name]
    print_report(inj, f"INJECTED DEFECT '{name}': {desc}")
    moved, still = [], []
    for b, i in zip(base, inj):
        if b.measured is None or i.measured is None:
            continue
        d = abs(i.measured - b.measured)
        (moved if d > b.prop.tol else still).append((b.prop, b.measured, i.measured, d))
    print("\n" + "=" * 94)
    print(f"DID THE REPORT SEE IT?   defect touches: {', '.join(voices)}")
    print("=" * 94)
    hit = [m for m in moved if m[0].voice in voices]
    for p, a, b, d in moved:
        print(f"  MOVED   {p.voice:7s} {p.name:34s} {a:10.4g} -> {b:10.4g}  "
              f"(by {d:.4g} {p.unit}, tolerance {p.tol:g})")
    print()
    for p, a, b, d in still:
        if p.voice in voices:
            print(f"  BLIND   {p.voice:7s} {p.name:34s} {a:10.4g} -> {b:10.4g}  "
                  f"(by {d:.4g} {p.unit}) -- this property cannot see this defect")
    if not hit:
        print(f"\n  NO PROPERTY OF {voices} MOVED. This injection is invisible to the")
        print("  report: either the defect does not change the sound, or the coverage")
        print("  has a hole. Either way the report cannot currently go red for it.")
        return 1
    print(f"\n  {len(hit)} of {len([p for p in base if p.prop.voice in voices])} properties "
          f"on {voices} moved by more than their tolerance. The report can go red here.")
    return 0


def cmd_changed(ref):
    files = subprocess.run(["git", "diff", "--name-only", ref, "--"],
                           capture_output=True, text=True).stdout.split()
    sound = [f for f in files if f.startswith(("model/", "audition/"))
             and f.endswith(".py") and not os.path.basename(f).startswith("test_")]
    print("sound-model files changed against", ref)
    for f in sound:
        print("   ", f)
    if not sound:
        print("    (none -- this commit does not change the sound; no report needed)")
        return 0
    lad = any(os.path.basename(f) in ("fixed.py", "voice_fx.py", "dsp.py") for f in sound)
    drum = any(os.path.basename(f) in ("drums_fx.py", "modal_fixed.py") for f in sound)
    print("\nvoices whose measurements this could move:")
    print("   ", "LADDER (the whole Moog voice)" if lad else "", "the drum kit" if drum else "")
    print("\nrun:  .venv/bin/python model/sound_report.py")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--inject", choices=sorted(INJECTIONS))
    ap.add_argument("--list-injections", action="store_true")
    ap.add_argument("--changed", metavar="REF")
    ap.add_argument("--relock", action="store_true")
    a = ap.parse_args(argv)
    if a.list_injections:
        for k, (d, v, _) in sorted(INJECTIONS.items()):
            print(f"  {k:18s} {','.join(v):8s} {d}")
        return 0
    if a.changed:
        return cmd_changed(a.changed)
    if a.inject:
        return cmd_inject(a.inject)
    res, _ = run()
    if a.relock:
        print("LOCKS = {")
        for r in res:
            if r.prop.kind == "lock" and r.measured is not None:
                print(f'    ("{r.prop.voice}", "{r.prop.name}"): {r.measured:.6g},')
        print("}")
        return 0
    return 1 if print_report(res, "SOUND REPORT -- per voice, per property") else 0


if __name__ == "__main__":
    sys.exit(main())
