#!/usr/bin/env python3
"""The voice past the filter: oscillators, and the waveforms we do not have.

    .venv/bin/python model/reference_voice.py --stage osc --out /tmp/refvoice

`docs/discrimination.md` section 8 measured the FILTER against three
references and found two real defects. The oscillators, envelopes, glide and
noise had never been compared to anything. This is the oscillator half.

Two different jobs, kept apart:

  COMPARE   where we have the thing. Harmonic amplitudes against the
            closed-form ideal waveform, and aliasing at high notes, ours
            against Surge XT and Arturia Mini V3 at matched pitch.
  TARGET    where we do not. The Model D has six waveforms per oscillator and
            we have four of them; the SHARK-TOOTH (saw/triangle hybrid) has no
            counterpart on our side, so the job there is not comparison but
            producing a declared target someone can build against.

Diva is not in this study: it is a general analogue-modelling synth, not a
Minimoog emulation, and it was additionally found to be running unlicensed
(`docs/reference-integrity.md` section 1).

**What is and is not comparable.** Ours is measured with `voice_fx.OscFx`
alone -- no filter, no envelope, nothing else in the path. Surge is measured
with its filter set to Off, which is a true bypass. **Mini V3's filter cannot
be bypassed**, so it is measured with the cutoff knob at maximum and its
residual response is a systematic that is stated rather than corrected. That
asymmetry is CONSERVATIVE for us on aliasing: a filter in the path can only
remove aliases, so if ours aliases less than Mini V3's it does so against a
handicap, and if it aliases more the margin is a lower bound.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "audition"))

import audio_measure as am                                          # noqa: E402
import reference_rigs as rr                                         # noqa: E402
import voice_fx as vf                                               # noqa: E402

SR = rr.SR
KMAX = 12
NOTES = [33, 45, 57, 69, 81, 93]          # A1 .. A6


def ideal_db(shape: str, k: int, duty: float = 0.5) -> float | None:
    """Closed-form harmonic amplitude relative to the fundamental, in dB."""
    def a(n):
        if shape == "saw":
            return 1.0 / n
        if shape == "tri":
            return 1.0 / n ** 2 if n % 2 else 0.0
        return abs(math.sin(math.pi * n * duty)) / n      # pulse, duty 0.5 = square
    return None if abs(a(k)) < 1e-12 else 20 * math.log10(abs(a(k)) / a(1))


IDEAL_WAVES = {"saw": ("saw", 0.5), "square": ("pulse", 0.5),
               "pulse25": ("pulse", 0.25), "tri": ("tri", 0.5), "sine": ("sine", 0.5)}


def ideal_tone(wave, note, seconds=0.5, sr=SR):
    """A band-limited ideal waveform from its Fourier series -- no aliasing by
    construction, and no instrument in the path.

    It is in the comparison as a DEVICE, not as a curve drawn beside one,
    because the aliasing estimator has a floor and that floor is the only
    thing an instrument reading -88 dB can be compared against. Quoting a
    reference's number without it is how "Surge is flat at -60 dB over six
    octaves" got published: -60 dB was the harness's own leftover note tails,
    identical at every pitch because it was not a property of Surge at all."""
    shape, duty = IDEAL_WAVES[wave]
    f0 = vf.note_hz(note)
    n = int(seconds * sr)
    t = np.arange(n) / sr
    y = np.zeros(n)
    for k in range(1, int(0.5 * sr / f0) + 1):
        if k * f0 >= sr / 2:
            break
        if shape == "saw":
            y += np.sin(2 * math.pi * k * f0 * t) / k
        elif shape == "tri":
            if k % 2:
                y += (-1) ** ((k - 1) // 2) * np.sin(2 * math.pi * k * f0 * t) / k ** 2
        elif shape == "sine":
            if k == 1:
                y += np.sin(2 * math.pi * f0 * t)
        else:
            y += math.sin(math.pi * k * duty) * np.cos(2 * math.pi * k * f0 * t) / k
    return y / max(np.abs(y).max(), 1e-12) * 0.5


def ours_tone(wave, note, seconds=0.5, blep=True):
    o = vf.OscFx(wave, blep=blep)
    inc = vf.phase_inc(vf.note_hz(note))
    return np.asarray(o.render(int(seconds * SR), inc), dtype=np.float64) / 32768.0


def duty_from_harmonics(sig, kmax=KMAX, dip_db=12.0):
    """A pulse's duty cycle from where its harmonic nulls fall: a duty of 1/m
    has a null at every m-th harmonic. Measured, not taken from a knob with no
    units.

    A real instrument's null is a DIP, not an absence -- an analogue-modelled
    pulse is never exactly 1/m -- so a harmonic counts as a null if it is
    `dip_db` below both of its neighbours, as well as if the estimator refused
    it outright. **A spectrum cannot tell duty d from 1 - d**: they are
    identical, so the answer is always reported as a pair."""
    def lv(k):
        v = sig.get(f"h{k}")
        return -200.0 if v is None else v
    nulls = [k for k in range(2, kmax) if lv(k) < lv(k - 1) - dip_db and lv(k) < lv(k + 1) - dip_db]
    present = [k for k in range(2, kmax + 1) if sig.get(f"h{k}") is not None]
    if not nulls:
        return None, present, nulls
    return 1.0 / min(nulls), present, nulls


def measure(y, f0_cmd, tag, wave):
    """Every number a row carries, plus the qualification that decides whether
    the row is allowed into a table at all.

    TWO different claims, kept apart because they fail for different reasons:

      steady    the APPARATUS produced one steady cycle of one waveform at the
                commanded pitch -- it repeats, it crosses its own midpoint
                twice, nothing is beating and nothing is in the path
      verified  and that waveform IS the one the rig's label claims

    A row can be steady and unverified: Mini V3's shark-tooth is a perfectly
    good record of a shape with no closed form. It cannot be the other way
    round."""
    # The fundamental is MEASURED, not assumed. Mini V3 plays +0.14 cents
    # sharp -- inaudible, and enough to take a perfectly steady oscillator's
    # per-period residual from 0.6 % to 25 % at 1760 Hz. Every Mini V3 row
    # failed to repeat until this was measured. A rig more than 50 cents out,
    # or one whose fundamental is an octave below the note it was given, is
    # REFUSED here rather than measured at the wrong frequency.
    fe = am.refine_f0(y, f0_cmd, SR)
    if not fe.ok:
        return dict(tag=tag, f0=None, f0_cmd=f0_cmd, f0_cents=None, valid=False,
                    invalid_harmonics=[], wave_label=None, wave_reason=fe.reason,
                    verified=False, verify_why=fe.reason, steady=False,
                    period_residual=None, half_period_corr=None, crossings=None,
                    rectangularity=None, step_ratio=None, jumps=None,
                    duty_measured=None, inharmonic_db=None, n_valid=0)
    f0 = fe.value
    s = am.harmonic_signature(y, SR, f0=f0, kmax=KMAX)
    # A saw, pulse or triangle cannot have a harmonic ABOVE its fundamental:
    # every one of them has |a_n| <= |a_1|. A row that does is not a
    # measurement of that waveform -- it means the commanded f0 is not the
    # signal's f0, i.e. the rig is not making the waveform it was asked for.
    # Refuse the row rather than print it; the first run published a Surge
    # "square" with h2 at +79.6 dB.
    bad = [k for k in range(2, KMAX + 1)
           if s.get(f"h{k}") is not None and s[f"h{k}"] > 0.0]
    s["valid"] = not bad
    s["invalid_harmonics"] = bad
    w = am.waveform_id(y, f0, SR)
    ok, why = am.waveform_matches(w, wave)
    d = w.detail
    s.update(wave_label=w.label, wave_reason=w.reason, verified=ok, verify_why=why,
             steady=bool(d.get("steady")), period_residual=d.get("period_residual"),
             half_period_corr=d.get("half_period_corr"), crossings=d.get("midpoint_crossings"),
             rectangularity=d.get("rectangularity"), step_ratio=d.get("step_ratio"),
             jumps=d.get("jumps_per_period"), duty_measured=d.get("duty"))
    al = am.inharmonic_fraction_db(y, f0, SR)
    s["inharmonic_db"] = al.value if al.ok else None
    s.update(tag=tag, f0_cmd=f0_cmd, f0_cents=fe.detail.get("cents"))
    return s


def stage_osc(out):
    devices = {}
    rows = []
    surge = rr.SurgeRig("Type 2")
    mini = rr.MiniV3Rig()
    try:
        plan = [("ideal", "ideal", ["saw", "square", "pulse25", "tri", "sine"]),
                ("ours", None, ["saw", "square", "pulse25", "tri", "sine"]),
                ("surge", surge, list(rr.SurgeRig.WAVES)),
                ("miniv3", mini, list(rr.MiniV3Rig.WAVES))]
        for name, dev, waves in plan:
            for w in waves:
                for note in NOTES:
                    f0 = vf.note_hz(note)
                    if f0 * 2 >= SR / 2:
                        continue
                    y = (ideal_tone(w, note) if dev == "ideal" else
                         ours_tone(w, note) if dev is None else dev.osc_tone(w, note))
                    r = measure(y, f0, f"{name}/{w}/{note}", w)
                    r.update(device=name, wave=w, note=note, f0_cmd=f0)
                    rows.append(r)
            print(f"  {name}: {len(waves)} waveforms x {len(NOTES)} pitches", flush=True)
    finally:
        del surge, mini
    json.dump(rows, open(os.path.join(out, "osc.json"), "w"), indent=1, default=str)
    report_osc(rows)
    return rows


def _fmt(v, w=7):
    return (" " * (w - 3) + "---") if v is None else f"{v:{w}.1f}"


def _pct(v, w=7):
    """A per-period residual, in percent of the cycle, to two decimals -- an
    ideal waveform reads 0.02 and unison two cents wide reads 28, so one
    decimal place throws away the whole margin."""
    return (" " * (w - 3) + "---") if v is None else f"{v * 100:{w}.2f}"


def report_osc(rows):
    at45 = [r for r in rows if r["note"] == 45]
    good = [r for r in rows if r.get("verified")]

    print("\n" + "=" * 100)
    print("0. WAVEFORM QUALIFICATION -- what each rig ACTUALLY made, at A2 (110 Hz)")
    print("=" * 100)
    print("  Identified from the signal, not from the request: the period is averaged and")
    print("  its duty measured in the TIME domain, then the harmonic nulls are checked")
    print("  against THAT measured duty. 'odd harmonics only' is a test for 50 % duty, not")
    print("  for 'square', and two saws do not always make a comb -- at half a period apart")
    print("  they are simply a saw at twice the frequency.")
    print(f"  {'asked for':22s} {'resid':>7} {'x/per':>6} {'jumps':>6} {'step':>7} "
          f"{'duty':>7}  measured")
    print(f"  {'':22s} {'% cyc':>7} {'':>6} {'/per':>6} {'ratio':>7} {'%':>7}")
    for r in at45:
        got = r["wave_label"] if r.get("verified") else (
            f"NOT VERIFIED -- {r['verify_why']}")
        d = r.get("duty_measured")
        print(f"  {r['device'] + '/' + r['wave']:22s} "
              f"{_pct(r.get('period_residual'), 7)} "
              f"{'---' if r.get('crossings') is None else r['crossings']:>6} "
              f"{'---' if r.get('jumps') is None else r['jumps']:>6} "
              f"{_fmt(r.get('step_ratio'), 7)} "
              f"{'    ---' if d is None else f'{d * 100:5.1f} %'}  {got}")
    ex = sorted((r["device"], r["wave"], vf.note_hz(r["note"]), r["verify_why"])
                for r in rows if not r.get("verified"))
    if ex:
        print(f"\n  EXCLUDED, {len(ex)} of {len(rows)} rows, each named with its pitch:")
        for dv, w, hz, why in ex:
            print(f"    {dv + '/' + w:20s} {hz:7.0f} Hz  {why}")
        print("  Excluded, not caveated. A row whose waveform is not the one it is labelled")
        print("  with is a measurement of a different instrument setting.")

    print("\n" + "=" * 100)
    print("1. HARMONIC SERIES at A2 (110 Hz), dB relative to the fundamental")
    print("=" * 100)
    print(f"{'device/wave':22s} " + " ".join(f"{'h'+str(k):>7s}" for k in range(2, 10)))
    for shape, duty, lbl in (("saw", 0.5, "IDEAL saw"), ("pulse", 0.5, "IDEAL square"),
                             ("pulse", 0.25, "IDEAL 25% pulse"), ("tri", 0.5, "IDEAL triangle")):
        print(f"{lbl:22s} " + " ".join(_fmt(ideal_db(shape, k, duty)) for k in range(2, 10)))
    print("-" * 100)
    for r in [r for r in at45 if r.get("verified")]:
        if not r.get("valid", True):
            print(f"{r['device'] + '/' + r['wave']:22s} "
                  f"REFUSED: harmonics above the fundamental at {r['invalid_harmonics']}"
                  f" -- this rig is not making the waveform it was asked for")
            continue
        print(f"{r['device'] + '/' + r['wave']:22s} "
              + " ".join(_fmt(r.get(f"h{k}")) for k in range(2, 10)))

    print("\n" + "=" * 100)
    print("2. WHICH RECTANGULAR IS OURS?  duty from the period, cross-checked by the nulls")
    print("=" * 100)
    disagree = []
    for r in [r for r in at45 if r.get("verified") and r.get("duty_measured") is not None]:
        duty, present, absent = duty_from_harmonics(r)
        d = r["duty_measured"] * 100
        off = (None if duty is None else
               min(abs(duty * 100 - d), abs(duty * 100 - (100 - d))))
        print(f"  {r['device'] + '/' + r['wave']:22s} period says {d:5.1f} % "
              f"(or {100 - d:5.1f} %);  nulls at harmonics {absent[:6]}"
              f" -> {'no null found' if duty is None else f'{duty * 100:.1f} %'}"
              f"{'   <-- DISAGREE' if off is not None and off > 3.0 else ''}")
        if off is not None and off > 3.0:
            disagree.append((r, d, duty * 100))
    print("  Two independent measurements of the same quantity. The period can tell d from")
    print("  1 - d and the spectrum cannot, so the period is the answer and the nulls are")
    print("  the check.")
    for r, d, nd in disagree:
        print(f"\n  WHERE THEY DISAGREE -- {r['device']}/{r['wave']}: the nulls read"
              f" {nd:.1f} %, the period {d:.1f} %.")
        print(f"  Reading a duty from the nulls assumes the DIP AT HARMONIC m MEANS d = 1/m,")
        print(f"  and that is only true when the null is exact. A {d:.1f} % rectangle has no")
        print(f"  exact null at all; it has a deep dip where m*d is nearest an integer, and")
        print(f"  the measured level there is what the {d:.1f} % duty predicts. The period is")
        print(f"  the measurement; the null estimator is reporting 1/m and should be read as")
        print(f"  'the deepest dip is at harmonic m', which is all it can see.")
    print("\n  NOTE ON THE ~52 % SQUARE (miniv3/square): the Model D service manual records")
    print("  that R137 was HAND-SELECTED PER UNIT to trim the square to 50 %. A reference")
    print("  showing 52 % is modelling A UNIT OUT OF TRIM, not the design intent, and our")
    print("  exact 50 % is arguably the more faithful value. Carried as an open")
    print("  disagreement, not as our defect. GENERALLY: where a reference emulation models")
    print("  UNIT VARIATION, a difference from it is not automatically a defect in ours.")

    print("\n" + "=" * 100)
    print("3. ALIASING: inharmonic energy, dB of total, at rising pitch")
    print("=" * 100)
    notes = sorted({r["note"] for r in rows})
    floors = alias_floor(good, notes)
    print(f"{'device/wave':22s} " + " ".join(f"{vf.note_hz(n):>7.0f}" for n in notes))
    keys = sorted({(r["device"], r["wave"]) for r in good})
    for dv, w in keys:
        cells = []
        for n in notes:
            m = [r for r in good if r["device"] == dv and r["wave"] == w and r["note"] == n]
            v = m[0].get("inharmonic_db") if m else None
            cells.append(_floor_fmt(v, floors.get((w, n)), dv == "ideal"))
        print(f"{dv + '/' + w:22s} " + " ".join(cells))
    print("  Only VERIFIED rows appear. A cell is blank where that pitch did not qualify.")
    print("  less negative = more inharmonic energy.")
    print("  '<' marks a cell within 3 dB of the IDEAL row at the same waveform and pitch --")
    print("  the estimator's own floor, measured here and not assumed. The floor is about")
    print("  -113 dB from 110 Hz up and only -52 dB at 55 Hz, where +-5-bin guards around")
    print("  harmonics 55 Hz apart cover 40 % of the spectrum. A '<' cell is a limit of the")
    print("  instrument, not a measurement of the instrument under test, and the published")
    print("  55 Hz column was entirely of that kind.")
    print("  Mini V3 carries its own filter at maximum cutoff (it cannot be bypassed), which")
    print("  can only REMOVE aliases, so its row is a best case for it and the margin against")
    print("  ours is a lower bound.")
    report_margins(good, notes, floors)


def alias_floor(good, notes):
    """The aliasing estimator's floor, per waveform and pitch, measured from
    the band-limited ideal of that same waveform."""
    out = {}
    for r in good:
        if r["device"] == "ideal" and r.get("inharmonic_db") is not None:
            out[(r["wave"], r["note"])] = r["inharmonic_db"]
    for n in notes:                     # a waveform with no ideal falls back to the saw
        if ("saw", n) in out:
            for w in {r["wave"] for r in good}:
                out.setdefault((w, n), out[("saw", n)])
    return out


def _floor_fmt(v, floor, is_ideal=False):
    if v is None:
        return "    ---"
    if floor is not None and not is_ideal and v <= floor + 3.0:
        return f"<{v:6.1f}"
    return f"{v:7.1f}"

    print("\n" + "=" * 100)
    print("4. TARGET: the shark-tooth we do not have (Mini V3's 'saw-triangular')")
    print("=" * 100)
    sh = [r for r in rows if r["device"] == "miniv3" and r["wave"] == "shark" and r["steady"]]
    for r in sorted(sh, key=lambda r: r["note"]):
        print(f"  {vf.note_hz(r['note']):7.1f} Hz  "
              + " ".join(f"h{k} {_fmt(r.get(f'h{k}'), 6)}" for k in range(2, 8))
              + f"   inharmonic {_fmt(r.get('inharmonic_db'), 6)}")
    print("  These rows are STEADY but not VERIFIED, and the two are different claims: the")
    print("  apparatus made one clean cycle per period at the commanded pitch, and the shape")
    print("  it made has no closed form to be checked against. That is why the shark-tooth")
    print("  appears here, as a target description, and not in the aliasing comparison.")
    print("  A saw has h_n = 1/n (-6.0, -9.5, -12.0 dB) and a triangle 1/n^2 on odd harmonics")
    print("  only (-19.1 at h3). A hybrid sits between them and has BOTH an amplitude and a")
    print("  slope discontinuity, so a generator needs BLEP and BLAMP. WE NOW HAVE BOTH:")
    print("  PolyBLEP on the saw share's step and polyBLAMP on the triangle share's two")
    print("  corners (DR 0017, issue #48), worth up to 6.3 dB of inharmonic energy at the")
    print("  top of the register -- measured by tools/measure_shark_blamp.py, not here.")


def report_margins(good, notes, floors):
    """Ours against each reference, on the waveforms BOTH of them verified at
    the same pitch. Any other pairing is two different waveforms subtracted."""
    print("\n  MARGIN, ours minus the reference, dB, on matched waveform AND pitch:")
    print(f"  {'':22s} " + " ".join(f"{vf.note_hz(n):>7.0f}" for n in notes))
    for ref in ("ideal", "miniv3", "surge"):
        for w in sorted({r["wave"] for r in good if r["device"] == "ours"}):
            row, any_pair = [], False
            for n in notes:
                a = [r for r in good if r["device"] == "ours" and r["wave"] == w
                     and r["note"] == n]
                b = [r for r in good if r["device"] == ref and r["wave"] == w
                     and r["note"] == n]
                if a and b and a[0].get("inharmonic_db") is not None \
                        and b[0].get("inharmonic_db") is not None:
                    d = a[0]["inharmonic_db"] - b[0]["inharmonic_db"]
                    fl = floors.get((w, n))
                    bound = (fl is not None and ref != "ideal"
                             and b[0]["inharmonic_db"] <= fl + 3.0)
                    row.append(f">{d:6.1f}" if bound else f"{d:7.1f}")
                    any_pair = True
                else:
                    row.append("    ---")
            if any_pair:
                print(f"  ours-{ref}/{w:15s} " + " ".join(row))
    print("  positive = we carry MORE inharmonic energy than the reference.")
    print("  '>' marks a margin whose REFERENCE cell sat at the estimator's floor, so the")
    print("  true margin is at least that and the number is a lower bound, not a value.")
    print("  ours-ideal is the margin against a waveform with no aliasing at all, and it is")
    print("  the one that does not depend on any plugin's settings being right.")


# ===========================================================================
# SHAPE SWEEP: where the Surge mapping comes from
# ===========================================================================
SHAPES = [0.0, 0.125, 0.25, 0.375, 0.5, 0.625, 0.75, 0.875, 1.0]


def stage_shape(out, note=45, widths=(0.5, 0.25)):
    """Sweep Surge's Classic oscillator Shape across its range at two Widths
    and IDENTIFY what comes out of each, so the mapping in
    `reference_rigs.SurgeRig.WAVES` is derived from measurement and the table
    records why each value was chosen.

    Surge's Shape is bipolar and dawdreamer presents it as a bare 0..1, so
    whether 0.0 is -100 % (one end) or 0 % (the centre) decides the entire
    mapping -- and no manual says which normalisation the host applies. This
    does."""
    rows = []
    surge = rr.SurgeRig("Type 2")
    try:
        f0 = vf.note_hz(note)
        for width in widths:
            for sh in SHAPES:
                y, reads = surge.osc_raw("Classic", sh, width, note)
                r = measure(y, f0, f"shape{sh}/w{width}", "saw")   # label is irrelevant here
                r.update(shape=sh, width=width, shape_text=reads[0], width_text=reads[1])
                rows.append(r)
                print(f"  shape {sh:.3f} ({reads[0]}) width {reads[1]}: "
                      f"{r['wave_label'] or 'UNQUALIFIED: ' + r['wave_reason']}", flush=True)
        kind, sh, wd, exp = rr.SurgeRig.WAVES["sine"]
        y, reads = surge.osc_raw(kind, sh, wd, note, expect=exp)
        r = measure(y, f0, "sine-osc", "sine")
        r.update(shape=None, width=None, shape_text=reads[0], width_text=reads[1])
        rows.append(r)
        print(f"  Sine oscillator: {r['wave_label']}", flush=True)
    finally:
        del surge
    json.dump(rows, open(os.path.join(out, "shape.json"), "w"), indent=1, default=str)
    report_shape(rows)
    return rows


def report_shape(rows):
    print("\n" + "=" * 100)
    print("SURGE CLASSIC OSCILLATOR: what each Shape value ACTUALLY produces")
    print("=" * 100)
    print("  A2 = 110 Hz, filter OFF, unison asserted at 1 voice, every FX slot off.")
    print(f"  {'Shape':>6} {'reads':>11} {'Width':>8} {'resid':>7} {'x/per':>6} "
          f"{'jumps':>6} {'rect':>6} {'duty':>7}  identified as")
    for r in rows:
        if r.get("shape") is None:
            continue
        lbl = r["wave_label"] or f"-- {r['wave_reason']}"
        d = r.get("duty_measured")
        duty = "    ---" if d is None else f"{d * 100:5.1f} %"
        cross = "---" if r.get("crossings") is None else str(r["crossings"])
        jmp = "---" if r.get("jumps") is None else str(r["jumps"])
        print(f"  {r['shape']:6.3f} {r['shape_text']:>11} {r['width_text']:>8} "
              f"{_pct(r.get('period_residual'), 7)} {cross:>6} {jmp:>6} "
              f"{_fmt(r.get('rectangularity'), 6)} {duty:>7}  {lbl}")
    sine = [r for r in rows if r.get("shape") is None]
    if sine:
        print(f"  {'(Sine oscillator type)':>34} "
              f"{_pct(sine[0].get('period_residual'), 7)} "
              f"{sine[0].get('crossings'):>6} {sine[0].get('jumps'):>6} "
              f"{_fmt(sine[0].get('rectangularity'), 6)} "
              f"{'    ---':>7}  {sine[0]['wave_label']}")
    print("\n  WHAT THE MAPPING TAKES FROM THIS:")
    print("    saw      Shape 0.50 (0.00 %)    -- the CENTRE of a bipolar control, and Width")
    print("                                       has no effect there at all")
    print("    square   Shape 0.00 (-100.00 %) with Width 50 %")
    print("    pulse25  Shape 0.00 (-100.00 %) with Width 25 %")
    print("    sine     the Sine oscillator type; the Classic one has no sine and no triangle")
    print("  The mapping that shipped had saw at 0.00 and square at 1.00: it asked for a")
    print("  50 % PULSE and labelled it a saw, and for the DUAL SAW and labelled it a square.")


# ===========================================================================
# QUALIFICATION CONTROLS: setups that are deliberately wrong
# ===========================================================================
def stage_controls(out, note=45):
    """A qualification that has only ever seen correct configurations has not
    been shown to reject anything. Each of these is a real render of a real
    plugin in a state this repository has actually been bitten by, and each
    must be REFUSED."""
    f0 = vf.note_hz(note)
    rows = []
    surge = rr.SurgeRig("Type 2")
    try:
        def add(name, y, wave, expect, pin=False):
            """A control is REJECTED if EITHER the signal fails to qualify or
            a pinned setting fails to hold. Both are refusals; they are not
            the same refusal, and an apparatus needs both -- an allpass effect
            changes no harmonic amplitude and only the pin can see it."""
            r = measure(y, f0, name, wave)
            r.update(control=name, expect=expect, pin_fired=bool(pin),
                     rejected=(not r["verified"]) or bool(pin),
                     how=("--" if r["verified"] and not pin else
                          "pin readback" if pin and r["verified"] else
                          "the pitch or the period" if not r["steady"] else
                          "the waveform"))
            rows.append(r)
            print(f"  {name}: {'REJECTED' if r['rejected'] else 'ACCEPTED'} "
                  f"({r['how']}) -- {r['verify_why'] or r['wave_label']}", flush=True)

        # the control that is a control: the corrected setting must PASS
        add("surge/saw as saw (the corrected mapping)",
            surge.osc_tone("saw", note), "saw", "accept")
        # 1. the bug itself: a 50 % pulse labelled a saw
        y, _ = surge.osc_raw("Classic", 0.0, 0.5, note)
        add("a 50 % pulse labelled 'saw'", y, "saw", "reject")
        # 2. the dual saw labelled a square
        y, _ = surge.osc_raw("Classic", 1.0, 0.5, note)
        add("the dual saw labelled 'square'", y, "square", "reject")
        # 3. unison left on -- three detuned voices behind one label
        surge.select_osc("Classic")
        surge.set(259, 0.5); surge.set(260, 0.5)
        surge.set(265, 0.15)                              # A Osc 1 Unison Voices
        surge.render(np.zeros(1), 0.05)
        print(f"    (unison voices now read {surge.text(265)!r})", flush=True)
        surge.note = int(note)
        y = surge.render(np.zeros(1), 0.75)[int(0.2 * SR):int(0.2 * SR) + int(0.5 * SR)]
        add("unison left on behind a 'saw' label", y, "saw", "reject")
        pins = surge.check_pins()
        print(f"    and the rig's own pin check says: {pins or 'nothing'}", flush=True)
        # 4. an effect left in the path. TWO of them, because they fail
        #    differently and only one of them is visible in the signal.
        for fxv, tag in ((0.2, "spectral"), (0.1, "phase only")):
            surge.set(265, 0.0)
            surge.select_osc("Classic")
            surge.set(259, 0.5); surge.set(260, 0.5)
            surge.set(19, fxv)                            # FX slot A1 type
            surge.silence_state(0.10)
            fx = surge.text(19)
            surge.note = int(note)
            y = surge.render(np.zeros(1), 0.75)[int(0.2 * SR):int(0.2 * SR) + int(0.5 * SR)]
            pins = [b for b in surge.check_pins() if b[0] == 19]
            add(f"an effect ({fx}) left in the path", y, "saw", "reject",
                pin=bool(pins))
            print(f"    the FX slot pin says: {pins or 'nothing'}", flush=True)
            surge.set(19, 0.0)
        # 5. the type-change hazard: Surge's 259-267 under a Classic oscillator
        #    while the rig is pinned for an Audio In one
        surge.silence_state(0.05)
        bad = [b for b in surge.check_pins() if b[0] in (259, 260, 264, 265)]
        print(f"\n  the construction-time pin check, with oscillator 1 left on Classic:")
        print(f"    {bad or 'nothing -- THE CHECK IS NOT FIRING'}")
        rows.append(dict(control="pins under the wrong oscillator type", expect="reject",
                         rejected=bool(bad), how="pin readback", detail=str(bad)))
    finally:
        del surge
    json.dump(rows, open(os.path.join(out, "controls.json"), "w"), indent=1, default=str)
    print("\n" + "=" * 100)
    print("QUALIFICATION CONTROLS")
    print("=" * 100)
    fails = [r for r in rows if (r["expect"] == "reject") != bool(r["rejected"])]
    for r in rows:
        want = "reject" if r["expect"] == "reject" else "accept"
        got = "rejected" if r["rejected"] else "accepted"
        print(f"  [{'ok ' if (r['expect'] == 'reject') == bool(r['rejected']) else 'BAD'}] "
              f"{r['control']:48s} want {want:6s} got {got:9s} by {r.get('how', '')}")
    print("  'by' is WHICH refusal fired. They are different instruments and an apparatus")
    print("  needs both: an allpass effect changes no harmonic amplitude, so only the pin")
    print("  readback can see a Phaser left switched on.")
    print(f"  {len(rows) - len(fails)}/{len(rows)} controls behaved as required.")
    if fails:
        print("  A control that does not fire is worse than no control.")
    return rows


# ===========================================================================
# NOISE: target-setting, because we have no noise source at all
# ===========================================================================
def noise_report(y, ref_rms, tag):
    """Everything the mono-synth agent needs to build a noise source to, from
    a reference that has one. No comparison side exists on our part."""
    y = np.asarray(y, dtype=np.float64)
    out = dict(tag=tag, rms=float(am.rms(y)), peak=float(am.peak(y)),
               crest_db=float(am.db(am.peak(y), am.rms(y))),
               vs_osc_db=float(am.db(am.rms(y), ref_rms)) if ref_rms else None,
               seconds=len(y) / SR)
    sl = am.psd_slope_db_oct(y, (100.0, 15000.0), SR)
    out["slope_db_oct"] = sl.value if sl.ok else None
    out["slope_why"] = None if sl.ok else sl.reason
    out["slope_resid_db"] = sl.detail.get("residual_db") if sl.ok else None
    rp = am.repeat_period(y, SR, max_lag_s=min(5.0, len(y) / SR / 2 - 0.1))
    out["repeat_s"] = rp.value if rp.ok else None
    out["repeat_corr"] = rp.detail.get("corr") if rp.ok else rp.detail.get("best_corr")
    out["repeat_why"] = None if rp.ok else rp.reason
    # Gaussian or not: a 1-bit LFSR is +-1 and has a crest factor of 0 dB,
    # true Gaussian noise about 12 dB over a long record.
    n = y / (am.rms(y) or 1.0)
    out["kurtosis"] = float(np.mean(n ** 4))
    out["frac_within_1sd"] = float(np.mean(np.abs(n) < 1.0))
    return out


def stage_noise(out, seconds=8.0):
    rows = []
    surge, mini = rr.SurgeRig("Type 2"), rr.MiniV3Rig()
    try:
        for name, dev, colours in (("surge", surge, {"white(0%)": 0.5, "dark(-100%)": 0.0,
                                                     "bright(+100%)": 1.0}),
                                   ("miniv3", mini, {"white": 0.0, "pink": 1.0})):
            ref = float(am.rms(dev.osc_level_ref()))
            for cname, cv in colours.items():
                y = dev.noise_tone(seconds, cv)
                r = noise_report(y, ref, f"{name}/{cname}")
                r.update(device=name, colour=cname)
                rows.append(r)
                print(f"  {name}/{cname}: rms {r['rms']:.4f} slope "
                      f"{r['slope_db_oct'] if r['slope_db_oct'] is None else round(r['slope_db_oct'],2)}"
                      f" dB/oct", flush=True)
    finally:
        del surge, mini
    json.dump(rows, open(os.path.join(out, "noise.json"), "w"), indent=1, default=str)
    report_noise(rows)
    return rows


def report_noise(rows):
    print("\n" + "=" * 100)
    print("NOISE TARGETS -- we have no noise source, so none of this is a comparison")
    print("=" * 100)
    print(f"{'source':22s} {'slope':>10} {'resid':>7} {'vs saw':>8} {'crest':>7} "
          f"{'kurtosis':>9} {'repeats at':>12}")
    print(f"{'':22s} {'dB/oct':>10} {'dB':>7} {'dB':>8} {'dB':>7} {'':>9} {'':>12}")
    for r in rows:
        rep = ("no repeat" if r["repeat_s"] is None
               else f"{r['repeat_s']:.4f} s")
        print(f"{r['tag']:22s} {_fmt(r.get('slope_db_oct'), 10)} "
              f"{_fmt(r.get('slope_resid_db'), 7)} {_fmt(r.get('vs_osc_db'), 8)} "
              f"{_fmt(r.get('crest_db'), 7)} {r['kurtosis']:9.2f} {rep:>12}")
    print("  slope: white is 0.00, pink is -3.01 dB/oct, both exactly.")
    print("  vs saw: the noise's RMS relative to that instrument's own sawtooth at the SAME")
    print("     mixer setting -- the number a mix balance is built from.")
    print("  crest: peak over RMS. Gaussian noise is about 12 dB over a long record; a 1-bit")
    print("     LFSR output is exactly 0 dB.  kurtosis: Gaussian is 3.0, a +-1 square is 1.0.")
    print("  repeats at: the lag where the record repeats itself, or a refusal. A maximal")
    print("     16-BIT LFSR AT 48 kHz REPEATS AT 1.365313 s, which is audibly a loop on a held")
    print("     note; the estimator recovers that to 2 samples (test_reference_voice.py).")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stage", default="osc",
                    choices=["osc", "noise", "shape", "controls"])
    ap.add_argument("--out", default="/tmp/refvoice")
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    {"osc": stage_osc, "noise": stage_noise,
     "shape": stage_shape, "controls": stage_controls}[a.stage](a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
