#!/usr/bin/env python3
"""Does an output coupling correct the drum block, and what does it cost?

    .venv/bin/python tools/probes/dc_blocker.py --limits
    .venv/bin/python tools/probes/dc_blocker.py --placement
    .venv/bin/python tools/probes/dc_blocker.py --measure
    .venv/bin/python tools/probes/dc_blocker.py --cutoff
    .venv/bin/python tools/probes/dc_blocker.py --continuous
    .venv/bin/python -m pytest tools/probes/dc_blocker.py -q

#152 established a DIAGNOSIS: the drum block has no DC blocking anywhere,
`SRC_PULSE` never changes sign (mean/|mean| = 1.000 exactly), the modes it
drives are all-pole with DC gains of 18 to 23,899, and the AC coupling the
circuit puts after every voice is unmodelled. #165 is the bounded prototype,
and "one DC block fixes five voices" is what it has to TEST. A positive
excitation and a large DC gain do not establish a cutoff, a placement or a
transient behaviour, and a blocker trades settling speed against bass.

WHAT THIS FILE MEASURES, AND HOW IT AVOIDS MEASURING ITSELF
-----------------------------------------------------------
Everything here is read off the block's own int16 output -- `output_fx`, the
one hard rail of contract 12 -- with NO conditioning, NO high-pass and NO
normalisation before the measurement. `tools/probes/excitation_energy.py`
conditions because it compares against a machine recording; this file compares
the block against itself, so conditioning would only add an instrument whose
own DC handling is the thing under test.

Every band figure is ABSOLUTE: dB relative to digital full scale, on a
rectangular-window FFT where Parseval is exact. Normalised band SHARES are
printed too, in their own columns, marked, and never used for a verdict --

    **removing low-frequency energy raises a normalised high-band percentage
    without adding one sample of high-frequency content.**

That is the trap #165 names, and `share_rise_is_lf_removal` is the rule that
catches it: a share that rises while the band's ABSOLUTE energy is flat is
LF removal, not synthesis.

UNCERTAINTY. The model is integer and deterministic: two renders of the same
configuration are bit-identical, so the model-side measurement uncertainty is
exactly zero and every difference below is a real difference, not noise.
`test_the_measurement_has_no_uncertainty_to_hide_behind` pins that.
"""
from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "model"))

import drums_fx as dx                            # noqa: E402

SR = dx.SR
FS = 32768.0                                     # int16 full scale

# ===========================================================================
# THE LIMITS. Declared here, in the commit that adds this file, BEFORE the
# first measurement was taken -- git history is the evidence of the ordering.
# ===========================================================================
#
# A real high-pass changes low-frequency amplitude AND phase. "Unchanged" is
# not achievable and is not the criterion; "within a declared allowance" is,
# and the allowance is part of the deliverable (#165 s3).
#
# Where the numbers come from. The candidate corner is 7.457 Hz (`dx.COUPLE_K`
# = 10), read off the BD's own coupling network C49 / R176 || R177 (7.52 Hz,
# reference 2). A one-pole high-pass at fc attenuates a tone at f by
# f / sqrt(f^2 + fc^2):
#
#     BD   f0 ~ 50 Hz  -> -0.096 dB, +8.5 deg
#     HT   f0 ~ 190 Hz -> -0.007 dB, +2.2 deg
#     CH   band-pass at 11.7 kHz -> below any number this file can print
#
# so the allowances below are set at roughly 5x the predicted change for the
# control voices, which is tight enough that a placement or cutoff error
# cannot hide inside them, and loose enough that the honest phase change does
# not read as a regression.
IMPROVE_SUB20_DB = 6.0        # REQUIRED of CY and RS: sub-20 Hz absolute energy
                              # must fall by at least this much. 6 dB = a factor
                              # of 4 in energy; below that the correction is not
                              # worth a register, a state word and an adder.

LIMITS = {
    # property           allowance            what it protects
    "peak_dbfs":        {"BD": 0.5, "*": 0.3},   # headroom
    "body_20_700_db":   {"BD": 0.5, "*": 0.3},   # body, absolute
    "mid_700_5k_db":    {"*": 0.2},              # the middle, absolute
    "hf_5k_20k_db":     {"*": 0.2},              # HF, absolute: a DC blocker must
                                                 # not touch it AT ALL, and this is
                                                 # the anti-trap anchor
    "t20_ms_pct":       {"*": 3.0},              # decay, relative percent
    "attack_samp":      {"*": 2.0},              # onset -> peak, samples
    "centroid_pct":     {"*": 1.0},              # spectral centroid, relative percent
}
CONTROLS = ("BD", "HT", "CH")     # the diagnosis says these need nothing
SUBJECTS = ("CY", "RS")           # sustained rectified energy, and a short onset

# The bands. 20 Hz is the edge of the audible band and of the discrimination
# conditioning (`test_discrimination.HPF_HZ`); 700 Hz and 5 kHz are the splits
# of docs/discrimination.md 5a, so the shares below are comparable with the
# ones the tom question is argued in.
SUB20 = (0.0, 20.0)
BANDS = {"body_20_700_db": (20.0, 700.0), "mid_700_5k_db": (700.0, 5000.0),
         "hf_5k_20k_db": (5000.0, 20000.0)}

RENDER_S = 0.60                   # long enough for the CY tail; a 0.24 s window
                                  # would cut the decay this file has to report
RENDER_GAIN = 0.45                # the reference drum-bus gain (DR 0005), as
                                  # `test_discrimination.RENDER_GAIN`
LEAD_FRAMES = 10                  # the hit lands at frame 10, as every other probe


def provenance() -> str:
    def git(*a):
        try:
            return subprocess.run(["git", *a], cwd=ROOT, capture_output=True,
                                  text=True, check=True).stdout.strip()
        except Exception:
            return "?"
    dirty = "-dirty" if git("status", "--porcelain") else ""
    return (f"commit {git('rev-parse', '--short=12', 'HEAD')}{dirty}  "
            f"SR {SR}  render {RENDER_S:.2f}s  gain {RENDER_GAIN}  "
            f"K {dx.COUPLE_K} ({SR / (2 * np.pi * (1 << dx.COUPLE_K)):.3f} Hz)")


# ===========================================================================
# Rendering: one sound, one placement
# ===========================================================================
def render(sound, couple=dx.COUPLE_OFF, k=dx.COUPLE_K, seconds=RENDER_S,
           hits=None, kit=None, extra=None, gain=RENDER_GAIN, acc_out=False):
    """The block's int16 output for one sound, at one coupling placement.

    Returns (out_int16, n_clip). `n_clip` is how many samples the output
    stage's single clamp (contract 12) actually railed -- headroom, as a
    count rather than an adjective.

    COUPLE_POST is applied HERE, after the clamp, precisely because that is
    where it cannot be applied inside the block: it is the control that shows
    whether removing DC after clipping recovers the waveform the clipping
    destroyed. Same filter, same arithmetic (`dx.dc_block`), different side of
    the rail."""
    n = int(seconds * SR)
    kit = kit if kit is not None else dx.kit_with_sounds(sound)
    hits = hits if hits is not None else [(LEAD_FRAMES, dx.SOUND_STOP[sound], 1.0)]
    inner = couple if couple in (dx.COUPLE_EXC, dx.COUPLE_BUS) else dx.COUPLE_OFF
    d = dx.DrumsFx(couple=inner, couple_k=k)
    w = dx.hit_writes(hits, kit)
    if extra:
        w = sorted(list(w) + list(extra), key=lambda t: t[0])
    dmix, body = d.play(w, n)
    g = dx.accent_reg(gain)
    acc = (np.asarray(dmix, np.int64) * g + np.asarray(body, np.int64) * g) >> 15
    n_clip = int(((acc > 32767) | (acc < -32768)).sum())
    out = dx.output_fx(np.zeros(n), 0, dmix, g, body, g)
    if couple == dx.COUPLE_POST:
        y = dx.dc_block(out, k)
        n_clip += int(((y > 32767) | (y < -32768)).sum())
        acc = y
        out = np.clip(y, -32768, 32767).astype(np.int16)
    return (out, n_clip, acc) if acc_out else (out, n_clip)


# ===========================================================================
# The estimators. Each is validated against synthetic ground truth below;
# none is calibrated on the model it measures (docs/failure-modes.md).
# ===========================================================================
def band_energy_dbfs(x, lo, hi):
    """Absolute energy in [lo, hi) Hz, dB relative to digital full scale.

    Rectangular window, so Parseval is exact and the bands sum to the total
    with no window correction to get wrong. NOTHING is normalised: this number
    falls when energy is removed and does not rise when other energy is."""
    x = np.asarray(x, float) / FS
    X = np.fft.rfft(x)
    p = np.abs(X) ** 2
    p[1:(-1 if len(x) % 2 == 0 else None)] *= 2.0
    p /= len(x) ** 2
    f = np.fft.rfftfreq(len(x), 1.0 / SR)
    return 10.0 * np.log10(float(p[(f >= lo) & (f < hi)].sum()) + 1e-30)


def band_share_pct(x, lo, hi):
    """**NORMALISED. Never a verdict.** The same band as a percentage of the
    clip's total energy -- the quantity the tom question is argued in, and the
    quantity that rises when low frequency is REMOVED."""
    x = np.asarray(x, float)
    X = np.fft.rfft(x)
    p = np.abs(X) ** 2
    p[1:(-1 if len(x) % 2 == 0 else None)] *= 2.0
    f = np.fft.rfftfreq(len(x), 1.0 / SR)
    return 100.0 * float(p[(f >= lo) & (f < hi)].sum()) / (float(p.sum()) + 1e-30)


def peak_dbfs(x):
    return 20.0 * np.log10(float(np.abs(np.asarray(x, float)).max()) / FS + 1e-30)


def onset_index(x, frac=0.02):
    x = np.abs(np.asarray(x, float))
    pk = float(x.max())
    return int(np.argmax(x > frac * pk)) if pk > 0 else 0


def envelope(x, hp=20.0):
    """|analytic signal| of the clip with everything below `hp` Hz removed by
    exact FFT zeroing, applied identically to both sides of every comparison.

    WHY THIS EXISTS, AND IT IS A REPAIR. `attack_samples` first shipped as
    `argmax(|x|) - onset(|x|)`, which does not measure the attack: it
    identifies which HALF-CYCLE is largest, and a standing DC offset is
    exactly what decides that. On the BD it reported a **460-sample attack
    change** -- 9.6 ms, half a 50 Hz period -- from a 7.5 Hz high-pass that
    attenuates 50 Hz by 0.096 dB. The estimator moved the peak to the other
    polarity and called it an attack. The repair is independent of the
    outcome: the old definition was wrong about the BD's baseline too, and
    would have been wrong with no blocker in the file at all.

    The sub-20 Hz removal is not smuggling the correction into the ruler: it
    is applied to the UNCOUPLED and the COUPLED clip alike, so the attack of a
    clip that carries a DC pedestal and the attack of the same clip with the
    pedestal removed are read on the same basis. It is whole-clip and exact,
    so there is no prefix to contaminate (cf. `excitation_energy.condition_causal`)."""
    x = np.asarray(x, float)
    X = np.fft.rfft(x)
    X[np.fft.rfftfreq(len(x), 1.0 / SR) < hp] = 0.0
    y = np.fft.irfft(X, n=len(x))
    Z = np.fft.rfft(y)
    Z[1:(-1 if len(x) % 2 == 0 else None)] *= 2.0      # analytic signal
    return np.abs(np.fft.irfft(Z, n=len(x)).astype(complex)
                  + 1j * 0) if False else np.abs(_analytic(y))


def _analytic(y):
    n = len(y)
    Y = np.fft.fft(y)
    h = np.zeros(n)
    h[0] = 1.0
    if n % 2 == 0:
        h[n // 2] = 1.0
        h[1:n // 2] = 2.0
    else:
        h[1:(n + 1) // 2] = 2.0
    return np.fft.ifft(Y * h)


def attack_samples(x):
    """Onset to the peak of the ENVELOPE, in samples."""
    e = envelope(x)
    return int(np.argmax(e)) - onset_index(e)


def t20_ms(x, frame_ms=2.0):
    """Time from the loudest frame to 20 dB below it, in ms, on a backward
    energy integral (Schroeder) so a decay that is not a clean exponential --
    the cymbal's is not -- still gets a defined number.

    Returns nan when the clip never falls 20 dB, which is a REFUSAL and not a
    zero: a number that cannot be measured must not be reported as one."""
    x = np.asarray(x, float)
    h = max(1, int(SR * frame_ms / 1e3))
    e = np.add.reduceat(x ** 2, np.arange(0, len(x) - len(x) % h, h))
    edc = np.cumsum(e[::-1])[::-1]
    if edc[0] <= 0:
        return float("nan")
    db = 10.0 * np.log10(edc / edc[0] + 1e-30)
    i0 = int(np.argmax(db <= -0.0))
    below = np.where(db <= -20.0)[0]
    return float("nan") if not len(below) else (below[0] - i0) * frame_ms


def centroid_hz(x):
    x = np.asarray(x, float)
    p = np.abs(np.fft.rfft(x)) ** 2
    f = np.fft.rfftfreq(len(x), 1.0 / SR)
    return float((p * f).sum() / (p.sum() + 1e-30))


def measure(x, n_clip=0):
    m = {"peak_dbfs": peak_dbfs(x), "n_clip": float(n_clip),
         "sub20_dbfs": band_energy_dbfs(x, *SUB20),
         "t20_ms": t20_ms(x), "attack_samp": float(attack_samples(x)),
         "centroid_hz": centroid_hz(x)}
    for name, (lo, hi) in BANDS.items():
        m[name] = band_energy_dbfs(x, lo, hi)
        m[name.replace("_db", "_pct")] = band_share_pct(x, lo, hi)
    return m


# ===========================================================================
# The acceptance rule (DR 0015: the property vector judges, not an aggregate)
# ===========================================================================
def allowance(prop, voice):
    d = LIMITS[prop]
    return d.get(voice, d.get("*"))


def deltas(base, cand):
    """Property-by-property change, in the units each limit is declared in."""
    d = {}
    for p in ("peak_dbfs", "body_20_700_db", "mid_700_5k_db", "hf_5k_20k_db"):
        d[p] = cand[p] - base[p]
    d["t20_ms_pct"] = 100.0 * (cand["t20_ms"] - base["t20_ms"]) / (base["t20_ms"] + 1e-30)
    d["attack_samp"] = cand["attack_samp"] - base["attack_samp"]
    d["centroid_pct"] = 100.0 * (cand["centroid_hz"] - base["centroid_hz"]) / (base["centroid_hz"] + 1e-30)
    d["sub20_dbfs"] = cand["sub20_dbfs"] - base["sub20_dbfs"]
    d["n_clip"] = cand["n_clip"] - base["n_clip"]
    return d


def preserved(voice, d):
    """Every declared limit, checked one at a time. Returns the list of
    property names that BROKE their allowance -- empty is preservation.

    `worst` is not computed anywhere in this file. DR 0015: the property
    vector is authoritative and an aggregate hides the thing you need to
    see."""
    bad = []
    for p in ("peak_dbfs", "body_20_700_db", "mid_700_5k_db", "hf_5k_20k_db",
              "t20_ms_pct", "attack_samp", "centroid_pct"):
        v = d[p]
        if np.isnan(v) or abs(v) > allowance(p, voice) + 1e-9:
            bad.append(p)
    if d["n_clip"] > 0:
        bad.append("n_clip")
    return bad


def share_rise_is_lf_removal(d, tol_db=0.2):
    """**THE TRAP, as a decision rule.** A high-band SHARE that rises while
    the same band's ABSOLUTE energy is flat to within `tol_db` did not gain a
    single harmonic: the denominator shrank. Returns True when that is what
    happened, and the caller must then refuse to read the share as an
    improvement.

    This is why every share column in this file has an absolute column
    beside it, and why a tom improvement from a DC blocker is to be treated
    as suspect until this rule has been applied to it (#165 s4)."""
    return abs(d["hf_5k_20k_db"]) <= tol_db and abs(d["mid_700_5k_db"]) <= tol_db


# ===========================================================================
# Reports
# ===========================================================================
def report_limits():
    print(provenance())
    print("\nDECLARED BEFORE THE FIRST MEASUREMENT (git: the commit that adds this file)\n")
    print(f"  REQUIRED improvement, {' and '.join(SUBJECTS)}:")
    print(f"    sub-20 Hz ABSOLUTE energy falls by >= {IMPROVE_SUB20_DB:.1f} dB\n")
    print("  PRESERVATION allowance, every voice (controls: " + ", ".join(CONTROLS) + ")\n")
    print(f"    {'property':18s} {'unit':10s} {'BD':>7s} {'others':>7s}")
    units = {"peak_dbfs": "dB", "body_20_700_db": "dB(abs)", "mid_700_5k_db": "dB(abs)",
             "hf_5k_20k_db": "dB(abs)", "t20_ms_pct": "%", "attack_samp": "samples",
             "centroid_pct": "%"}
    for p, u in units.items():
        print(f"    {p:18s} {u:10s} {allowance(p, 'BD'):7.2f} {allowance(p, 'CY'):7.2f}")
    print("    n_clip             count       must not increase")
    print("\n  A normalised band SHARE is never a verdict; `share_rise_is_lf_removal`")
    print("  refuses one whose absolute band energy did not move.")
    return 0


def _row(name, base, cand, voice):
    d = deltas(base, cand)
    bad = preserved(voice, d)
    print(f"  {name:22s} {d['sub20_dbfs']:+8.2f} {d['body_20_700_db']:+8.2f} "
          f"{d['mid_700_5k_db']:+8.2f} {d['hf_5k_20k_db']:+8.2f} "
          f"{d['peak_dbfs']:+7.2f} {d['t20_ms_pct']:+7.2f} {d['attack_samp']:+6.0f} "
          f"{d['centroid_pct']:+7.2f}  {'ok' if not bad else ','.join(bad)}")
    return d, bad


def _head():
    print(f"  {'':22s} {'sub20':>8s} {'body':>8s} {'mid':>8s} {'HF':>8s} "
          f"{'peak':>7s} {'T20':>7s} {'atk':>6s} {'cent':>7s}  limits")
    print(f"  {'':22s} {'dB abs':>8s} {'dB abs':>8s} {'dB abs':>8s} {'dB abs':>8s} "
          f"{'dB':>7s} {'%':>7s} {'samp':>6s} {'%':>7s}")


def report_placement(k=dx.COUPLE_K):
    """Placement is a MEASUREMENT here, not an argument. Four of them, on the
    two subjects, all against the same baseline on the same instrument."""
    print(provenance())
    print("\nPLACEMENT. Every number is a change from the uncoupled baseline of the")
    print("SAME voice on the SAME instrument (DR 0015: same qualified basis).\n")
    out = {}
    for v in SUBJECTS + CONTROLS:
        b, nb = render(v, dx.COUPLE_OFF)
        base = measure(b, nb)
        print(f"{v}   baseline: peak {base['peak_dbfs']:+.2f} dBFS  sub20 "
              f"{base['sub20_dbfs']:+.2f} dB  body {base['body_20_700_db']:+.2f} dB  "
              f"HF {base['hf_5k_20k_db']:+.2f} dB  T20 {base['t20_ms']:.1f} ms  "
              f"clip {int(base['n_clip'])}")
        _head()
        for pl in (dx.COUPLE_EXC, dx.COUPLE_BUS, dx.COUPLE_POST):
            c, nc = render(v, pl, k)
            d, bad = _row(f"{pl}", base, measure(c, nc), v)
            out[(v, pl)] = (d, bad)
        print()
    return out


def report_measure(k=dx.COUPLE_K, placement=dx.COUPLE_BUS):
    """The deliverable: improvement and preservation, together, in one table."""
    print(provenance())
    print(f"\nIMPROVEMENT AND PRESERVATION, placement '{placement}', K = {k} "
          f"({SR / (2 * np.pi * (1 << k)):.3f} Hz)\n")
    print("  Absolute dB throughout. Shares are printed after, separately, and")
    print("  are not part of any verdict.\n")
    _head()
    rows, verdict = {}, {}
    for v in SUBJECTS + CONTROLS:
        b, nb = render(v, dx.COUPLE_OFF)
        c, nc = render(v, placement, k)
        base, cand = measure(b, nb), measure(c, nc)
        d, bad = _row(f"{v}  ({'subject' if v in SUBJECTS else 'control'})", base, cand, v)
        rows[v] = (base, cand, d, bad)
        if v in SUBJECTS:
            verdict[v] = (d["sub20_dbfs"] <= -IMPROVE_SUB20_DB, bad)
        else:
            verdict[v] = (True, bad)
    print("\n  NORMALISED SHARES (%% of clip energy) -- diagnostic only, never a verdict\n")
    print(f"  {'':10s} {'body %':>16s} {'mid %':>16s} {'HF %':>16s}   HF share")
    print(f"  {'':10s} {'before':>7s} {'after':>8s} {'before':>7s} {'after':>8s} "
          f"{'before':>7s} {'after':>8s}")
    for v in SUBJECTS + CONTROLS:
        base, cand, d, _ = rows[v]
        why = "LF removal, NOT new HF" if share_rise_is_lf_removal(d) else "absolute HF moved"
        print(f"  {v:10s} {base['body_20_700_pct']:7.2f} {cand['body_20_700_pct']:8.2f} "
              f"{base['mid_700_5k_pct']:7.2f} {cand['mid_700_5k_pct']:8.2f} "
              f"{base['hf_5k_20k_pct']:7.2f} {cand['hf_5k_20k_pct']:8.2f}   {why}")
    print("\nVERDICT (DR 0015: property vector, no aggregate)\n")
    ok = True
    for v in SUBJECTS:
        imp, bad = verdict[v]
        ok &= imp and not bad
        print(f"  {v:4s} improvement >= {IMPROVE_SUB20_DB:.1f} dB sub-20: "
              f"{'MET' if imp else 'NOT MET'} ({rows[v][2]['sub20_dbfs']:+.2f} dB)   "
              f"preservation: {'ok' if not bad else 'BROKE ' + ','.join(bad)}")
    for v in CONTROLS:
        _, bad = verdict[v]
        ok &= not bad
        print(f"  {v:4s} control, must be preserved: "
              f"{'ok' if not bad else 'BROKE ' + ','.join(bad)}")
    print(f"\n  {'ACCEPTED' if ok else 'NOT ACCEPTED'} against the limits declared in this file.")
    return rows


def report_cutoff(ks=(8, 9, 10, 11, 12, 13), placement=dx.COUPLE_BUS):
    """The trade-off the brief says a blocker makes: settling speed against
    bass preservation. One column each, swept, so it is a curve and not an
    opinion."""
    print(provenance())
    print(f"\nCUTOFF SWEEP, placement '{placement}'. K is the pole 1 - 2^-K.\n")
    for v in SUBJECTS + CONTROLS:
        b, nb = render(v, dx.COUPLE_OFF)
        base = measure(b, nb)
        print(f"{v}")
        print(f"  {'K':>3s} {'fc Hz':>8s} {'sub20':>8s} {'body':>8s} {'HF':>8s} "
              f"{'peak':>7s} {'T20 %':>7s} {'cent %':>7s}  limits")
        for k in ks:
            c, nc = render(v, placement, k)
            d = deltas(base, measure(c, nc))
            bad = preserved(v, d)
            print(f"  {k:3d} {SR / (2 * np.pi * (1 << k)):8.3f} {d['sub20_dbfs']:+8.2f} "
                  f"{d['body_20_700_db']:+8.2f} {d['hf_5k_20k_db']:+8.2f} "
                  f"{d['peak_dbfs']:+7.2f} {d['t20_ms_pct']:+7.2f} {d['centroid_pct']:+7.2f}"
                  f"  {'ok' if not bad else ','.join(bad)}")
        print()
    return 0


# ===========================================================================
# Continuous playing: the state a blocker carries
# ===========================================================================
def _passage(sound, second=None, n_hits=8, spacing_ms=120.0, couple=dx.COUPLE_BUS,
             k=dx.COUPLE_K, switch_at=None):
    """A passage of repeated hits on one circuit, optionally retuned to the
    circuit's OTHER sound part-way through -- mid-ring, which is where state
    handling breaks (#165 s5)."""
    step = int(SR * spacing_ms / 1e3)
    n = LEAD_FRAMES + step * (n_hits + 1)
    kit = dx.kit_with_sounds(sound)
    hits = [(LEAD_FRAMES + i * step, dx.SOUND_STOP[sound], 1.0) for i in range(n_hits)]
    extra = []
    if second is not None and switch_at is not None:
        # The retune lands BETWEEN hits, while the previous hit is still
        # ringing: a panel switch is a register write, not a silence.
        f = LEAD_FRAMES + int(switch_at * step) + step // 2
        extra = [(f, a, v) for a, v in dx.preset_writes(second)]
        hits = [h for h in hits if h[0] < f] + \
               [(t, dx.SOUND_STOP[second], 1.0) for _, t, _ in [] ] + \
               [(t, dx.SOUND_STOP[second], 1.0) for t in
                [LEAD_FRAMES + i * step for i in range(n_hits)] if t > f]
    out, nclip = render(sound, couple, k, seconds=n / SR, hits=sorted(hits),
                        kit=kit, extra=extra)
    return out, nclip, step


def report_continuous(k=dx.COUPLE_K, placement=dx.COUPLE_BUS):
    """Four things a one-shot cannot show: repeated hits, an overlap, a choke,
    and a retune mid-ring on a shared circuit."""
    print(provenance())
    print(f"\nCONTINUOUS PLAYING, placement '{placement}', K = {k}\n")

    print("  repeated hits: peak of each hit's window, dBFS, uncoupled -> coupled")
    print(f"  {'sound':6s} {'hit':>4s} {'off':>9s} {'on':>9s} {'delta':>7s}   drift")
    for v in SUBJECTS:
        a, _, step = _passage(v, couple=dx.COUPLE_OFF, k=k)
        b, _, _ = _passage(v, couple=placement, k=k)
        pa, pb = [], []
        for i in range(8):
            s = slice(LEAD_FRAMES + i * step, LEAD_FRAMES + (i + 1) * step)
            pa.append(peak_dbfs(a[s])); pb.append(peak_dbfs(b[s]))
        for i in (0, 7):
            print(f"  {v:6s} {i:4d} {pa[i]:9.3f} {pb[i]:9.3f} {pb[i] - pa[i]:+7.3f}")
        drift = (pb[7] - pa[7]) - (pb[0] - pa[0])
        print(f"  {v:6s} last-minus-first of the coupled/uncoupled delta: "
              f"{drift:+.4f} dB  -- state that wandered would show here\n")

    print("  retune mid-ring on a shared circuit (the five exclusive pairs)")
    print(f"  {'pair':10s} {'largest 1-sample step at the switch frame':>44s}")
    for a_, b_ in dx.PAIRS:
        for couple, label in ((dx.COUPLE_OFF, "off"), (placement, placement)):
            out, _, step = _passage(a_, b_, couple=couple, k=k, switch_at=3)
            f = LEAD_FRAMES + 3 * step + step // 2
            w = np.asarray(out[f - 4:f + 5], float)
            print(f"  {a_}->{b_:6s} {label:>6s} {float(np.abs(np.diff(w)).max()) / FS:44.6f}")
    print("\n  A blocker holds charge across a retune, so the switch must not add a")
    print("  step of its own: the coupled column must not exceed the uncoupled one.\n")

    print("  choke (CH chokes OH) and overlap (a hit into a ring)")
    for name, hits, sound in (
            ("choke  OH then CH", [(10, dx.SOUND_STOP["OH"], 1.0),
                                   (10 + int(0.05 * SR), dx.SOUND_STOP["CH"], 1.0)], "OH"),
            ("overlap CY + CY", [(10, dx.SOUND_STOP["CY"], 1.0),
                                 (10 + int(0.08 * SR), dx.SOUND_STOP["CY"], 1.0)], "CY")):
        a, na = render(sound, dx.COUPLE_OFF, k, seconds=0.6, hits=hits)
        b, nb = render(sound, placement, k, seconds=0.6, hits=hits)
        d = deltas(measure(a, na), measure(b, nb))
        print(f"  {name:20s} sub20 {d['sub20_dbfs']:+7.2f} dB   HF {d['hf_5k_20k_db']:+6.2f} dB"
              f"   peak {d['peak_dbfs']:+6.2f} dB   clip {int(d['n_clip']):+d}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--limits", action="store_true")
    ap.add_argument("--placement", action="store_true")
    ap.add_argument("--measure", action="store_true")
    ap.add_argument("--cutoff", action="store_true")
    ap.add_argument("--continuous", action="store_true")
    ap.add_argument("-k", type=int, default=dx.COUPLE_K)
    ap.add_argument("--at", default=dx.COUPLE_BUS, choices=list(dx.COUPLE_PLACEMENTS))
    a = ap.parse_args(argv)
    if not any((a.limits, a.placement, a.measure, a.cutoff, a.continuous)):
        ap.print_help()
        return 2
    if a.limits:
        report_limits()
    if a.placement:
        report_placement(a.k)
    if a.measure:
        report_measure(a.k, a.at)
    if a.cutoff:
        report_cutoff(placement=a.at)
    if a.continuous:
        report_continuous(a.k, a.at)
    return 0


# ===========================================================================
# Self-tests: every estimator against ground truth it cannot have been fitted
# to, and every claim this file's reports make.
# ===========================================================================
def test_the_blocker_is_the_transfer_function_claimed():
    """H(z) = (1 - z^-1)/(1 - (1 - 2^-K) z^-1), measured, not asserted:
    an exact zero at DC and the declared corner, on the integer filter."""
    k = 10
    n = 1 << 16
    # DC in, nothing out -- and EXACTLY nothing, not "small": the fixed point
    # of the integer accumulator is reached and the output is identically 0.
    f = dx.DcBlockFx(k)
    y = np.array([f.step(10_000) for _ in range(n)])
    assert abs(y[-1]) == 0, y[-1]
    assert np.abs(y[n // 2:]).max() == 0, np.abs(y[n // 2:]).max()
    # The corner: the amplitude at fc must be 1/sqrt(2) of the passband.
    fc = SR / (2 * np.pi * (1 << k))
    t = np.arange(n) / SR
    def gain(hz):
        x = np.round(20000 * np.sin(2 * np.pi * hz * t)).astype(np.int64)
        o = dx.dc_block(x, k)[n // 2:]
        return float(np.abs(o).max()) / 20000.0
    assert abs(gain(fc) - 2 ** -0.5) < 0.03, (fc, gain(fc))
    assert abs(gain(1000.0) - 1.0) < 0.01, gain(1000.0)


def test_the_corner_is_the_machines_coupling_network():
    """`COUPLE_K` is read off C49 0.47 uF into R176 100k || R177 82k
    (reference 2's BD output buffer), not chosen. If either the constant or
    the reference value moves, this goes red."""
    r = 1.0 / (1.0 / 100e3 + 1.0 / 82e3)
    f_circuit = 1.0 / (2 * np.pi * 0.47e-6 * r)
    f_model = SR / (2 * np.pi * (1 << dx.COUPLE_K))
    assert abs(f_circuit - 7.52) < 0.05, f_circuit
    assert abs(f_model / f_circuit - 1.0) < 0.02, (f_model, f_circuit)


def test_coupling_off_is_the_block_bit_for_bit():
    """The prototype must not be able to change the shipped block by accident.
    Default construction and `couple='none'` are the same samples, exactly."""
    for v in ("CY", "RS", "BD"):
        a, _ = render(v, dx.COUPLE_OFF)
        n = int(RENDER_S * SR)
        d = dx.DrumsFx()
        dmix, body = d.play(dx.hit_writes([(LEAD_FRAMES, dx.SOUND_STOP[v], 1.0)],
                                          dx.kit_with_sounds(v)), n)
        g = dx.accent_reg(RENDER_GAIN)
        b = dx.output_fx(np.zeros(n), 0, dmix, g, body, g)
        assert np.array_equal(np.asarray(a), np.asarray(b)), v


def test_the_measurement_has_no_uncertainty_to_hide_behind():
    """Two renders of one configuration are bit-identical, so every delta this
    file prints is a real difference and not measurement scatter. DR 0015 asks
    for the measurement's own uncertainty; here it is exactly zero."""
    for couple in (dx.COUPLE_OFF, dx.COUPLE_BUS, dx.COUPLE_POST):
        a, na = render("CY", couple)
        b, nb = render("CY", couple)
        assert np.array_equal(a, b) and na == nb, couple


def test_band_energy_is_absolute_and_a_share_is_not():
    """The distinction the whole report rests on, on ground truth: remove the
    low tone from a two-tone signal and the HIGH tone's ABSOLUTE energy must
    not move, while its SHARE must rise. An instrument that cannot tell those
    apart would score a DC blocker as having synthesised harmonics."""
    n = int(SR * 0.5)
    t = np.arange(n) / SR
    lo = 8000 * np.sin(2 * np.pi * 100.0 * t)
    hi = 8000 * np.sin(2 * np.pi * 9000.0 * t)
    both = np.round(lo + hi).astype(np.int64)
    only = np.round(hi).astype(np.int64)
    a_both, a_only = band_energy_dbfs(both, 5000, 20000), band_energy_dbfs(only, 5000, 20000)
    s_both, s_only = band_share_pct(both, 5000, 20000), band_share_pct(only, 5000, 20000)
    assert abs(a_both - a_only) < 0.01, (a_both, a_only)      # absolute: unmoved
    assert s_only > s_both + 40.0, (s_both, s_only)           # share: doubled
    d = {"hf_5k_20k_db": a_only - a_both, "mid_700_5k_db": 0.0}
    assert share_rise_is_lf_removal(d)                        # and the rule says so


def test_the_trap_rule_does_not_fire_on_real_new_harmonics():
    """The other half of the control: when HF energy is genuinely ADDED, the
    rule must NOT call the share rise 'LF removal'. A rule that always fires
    would refuse every real improvement."""
    n = int(SR * 0.5)
    t = np.arange(n) / SR
    base = np.round(8000 * np.sin(2 * np.pi * 100.0 * t)).astype(np.int64)
    added = np.round(8000 * np.sin(2 * np.pi * 100.0 * t)
                     + 4000 * np.sin(2 * np.pi * 9000.0 * t)).astype(np.int64)
    d = {"hf_5k_20k_db": band_energy_dbfs(added, 5000, 20000) - band_energy_dbfs(base, 5000, 20000),
         "mid_700_5k_db": 0.0}
    assert d["hf_5k_20k_db"] > 10.0, d
    assert not share_rise_is_lf_removal(d)


def test_t20_recovers_a_known_decay_and_refuses_one_it_cannot_see():
    """The decay estimator against ground truth, and a number that was WRONG
    BEFORE IT WAS RIGHT: this assertion first shipped with `want = tau *
    ln(10)/2`, which is the -10 dB point, and the estimator was blamed for the
    27 ms gap. The backward energy integral of an amplitude decay exp(-t/tau)
    is exp(-2t/tau), so -20 dB of ENERGY is at

        t = tau * ln(100) / 2 = 2.3026 * tau,

    twice what was asserted. The estimator was right and the ground truth was
    not -- CLAUDE.md's "verify the method before the number", from the inside."""
    n = int(SR * 2.0)
    t = np.arange(n) / SR
    for tau in (0.02, 0.05, 0.2):
        x = np.round(20000 * np.sin(2 * np.pi * 300 * t) * np.exp(-t / tau)).astype(np.int64)
        want = 1e3 * tau * np.log(100.0) / 2.0
        got = t20_ms(x)
        assert abs(got - want) < 0.06 * want + 2.5, (tau, want, got)
    flat = np.full(int(SR * 0.1), 10000, dtype=np.int64)
    assert np.isnan(t20_ms(flat))            # REFUSED, not reported as 0


def test_attack_and_centroid_recover_ground_truth():
    n = int(SR * 0.3)
    t = np.arange(n) / SR
    env = np.minimum(t / 0.005, 1.0) * np.exp(-t / 0.05)
    x = np.round(20000 * np.sin(2 * np.pi * 400 * t) * env).astype(np.int64)
    assert abs(attack_samples(x) - 0.005 * SR) < 0.002 * SR, attack_samples(x)
    tone = np.round(20000 * np.sin(2 * np.pi * 1234.0 * t)).astype(np.int64)
    assert abs(centroid_hz(tone) - 1234.0) < 12.0, centroid_hz(tone)


def test_a_bus_blocker_is_the_superposition_of_per_path_blockers():
    """The hardware claim: a linear filter commutes with a sum, so ONE blocker
    on a bus is what a blocker on every path summing into it would be. Two
    registers, not twenty-three. Checked on the float filter to isolate the
    claim from integer truncation, and the truncation cost is measured beside
    it."""
    rng = np.random.default_rng(0)
    k = 10
    a = rng.integers(-5000, 5000, 4096)
    b = rng.integers(-5000, 5000, 4096)
    one = dx.dc_block(a + b, k)
    two = dx.dc_block(a, k) + dx.dc_block(b, k)
    err = float(np.abs(one - two).max())
    assert err <= 2.0, err                    # <= 1 LSB per blocker, from the shift
    assert float(np.abs(one).max()) > 1000.0


def test_the_coupling_state_survives_a_hit_and_a_retune_but_not_a_reset():
    """#165 s5: a capacitor does not know a stop fired. The accumulator must
    carry across hits and across a panel switch, and only A_RESET may clear
    it -- a blocker that reset on every hit would re-emit the step it exists
    to remove."""
    d = dx.DrumsFx(couple=dx.COUPLE_BUS)
    d.play(dx.hit_writes([(10, dx.SOUND_STOP["RS"], 1.0)], dx.kit_with_sounds("RS")), 4000)
    assert d.dc_dmix.acc != 0
    held = d.dc_dmix.acc
    for a, v in dx.preset_writes("CL"):        # a retune: registers, not silence
        d.write(a, v)
    assert d.dc_dmix.acc == held
    d.write(dx.A_RESET, 0)
    assert d.dc_dmix.acc == 0


def test_the_accumulator_width_is_declared_and_not_exceeded():
    """What the RTL has to build. acc ~ x * 2^K, so a 22-bit mix bus needs
    22 + K + 1 bits; the model counts the widest it actually saw so the RTL
    word is measured rather than guessed."""
    d = dx.DrumsFx(couple=dx.COUPLE_BUS)
    for v in ("BD", "SD", "CY", "RS", "OH"):
        d.write(dx.A_RESET, 0)
        d.play(dx.hit_writes([(10, dx.SOUND_STOP[v], 2.0)], dx.kit_with_sounds(v)),
               int(0.5 * SR))
    assert d.dc_dmix.acc_bits <= dx.MIX_BITS + dx.COUPLE_K + 1, d.dc_dmix.acc_bits
    assert d.dc_body.acc_bits <= dx.BODY_BITS + dx.COUPLE_K + 1, d.dc_body.acc_bits
    assert d.dc_dmix.acc_bits > dx.MIX_BITS, d.dc_dmix.acc_bits   # it is really used


if __name__ == "__main__":
    raise SystemExit(main())
