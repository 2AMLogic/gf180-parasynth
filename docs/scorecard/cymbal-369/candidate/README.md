# #369 candidate: the §10 three-band cymbal, evaluated at the shipped setting. It overcorrects

The candidate is `model/cymbal_candidate.py` (model only; the shared `modal_fixed` decode is unchanged, and
`verify_modal` passes on 57,600 samples). Its structural choices were fixed in the module docstring before any
render:

- **Low band:** Hh1 restored, a 2.5 kHz Q 0.97 2-pole high-pass.
- **High DECAY band:** Hh2 as a 2-pole high-pass. The reference does not resolve its corner, so the kit's 10.5 kHz Q 2.5 is carried over, and this is stated as a carried choice.
- **High short band:** its own third-order Hh3, a 2-pole plus a 1-pole at 10.5 kHz, instead of borrowing the closed hat's 11.7 kHz high-pass.
- **Level stage:** a +6 dB/oct differentiator on all three bands at their last stage, as one more (1 − z⁻¹).
- **Bank:** 19 modes and 24 paths. `N_NUMS` stays 11, because BD, SDLO and SDHI move to modes 16–18. The operator accepted the 17th-mode cliff.
- **Levels:** each band's output matches the shipped kit's same band in the 1/3 octave at its centre (a rule fixed in advance that references the kit, never a recording).
  - The low band needs its envelope peak ×1.60 and the short band ×2.79, both inside the register.
  - The short band's cascade taps a mode, which costs 18 dB (DR 0022).

**Preservation.** All 15 other sounds render **bit-identically** to the shipped kit on the new layout: BD, SD, LT, LC,
MT, MC, HT, HC, RS, CL, CP, MA, CB, OH and CH. That includes the hats, D15A and D16A.

## Result at CY5025, the anchor and D14A (`candidate.json`)

1/3-octave energy against the 808 (dB):

| window | 1.0 | 1.26 | 1.59 | 2.0 | 2.5 | 5.0 | 6.3 | 12.7 | 16 | 20 kHz |
|---|---|---|---|---|---|---|---|---|---|---|
| shipped, 0–50 ms | +15.4 | +11.2 | +10.4 | +9.1 | +9.4 | −2.9 | −2.0 | −2.2 | +0.2 | −7.0 |
| **candidate, 0–50 ms** | **−14.4** | **−12.2** | −6.8 | −3.2 | +4.4 | −7.9 | −8.8 | +3.3 | **+8.1** | **+6.1** |
| candidate, 50–300 ms | −19.0 | −18.6 | −16.0 | −12.3 | −5.4 | −6.9 | −9.3 | +5.5 | +8.1 | +7.7 |
| candidate, 300–1000 ms | −20.7 | −19.1 | −15.6 | −13.5 | −6.7 | +1.0 | −3.5 | +6.5 | +9.4 | +10.5 |

| | H−L | H EDT10 | Ln EDT10 |
|---|---:|---:|---:|
| 808 CY5025 | 8.16 dB | 148 ms | 591 ms |
| shipped | 10.82 dB | 151 ms | 598 ms |
| candidate | **8.64 dB** | 162 ms | 542 ms |

**Verdict: the candidate as specified does not close the defect. It inverts it.**
- The 1–2.5 kHz strike excess of +9 to +15 dB becomes a deficit of −3 to −14 dB, and it grows to −13 to −21 dB later in the strike.
- The 16–20 kHz deficit of −7 dB becomes an excess of +6 to +10 dB.
- The coarse balance improves (H−L 8.64 against the 808's 8.16).
- It is **not taken forward**. Nothing reaches RTL.

## A diagnostic ablation, not a selection (`candidate-notilt.json`)

This is the same structure with the level-stage differentiator removed (Hh3 is its 2-pole alone here). The bank has
no (1 − z⁻¹) numerator, and the short band falls 3.1 dB short of its level rule.

| window | 1.0 | 1.26 | 2.0 | 2.5 | 16 | 20 kHz |
|---|---|---|---|---|---|---|
| no-tilt, 0–50 ms | −1.7 | −2.8 | +2.7 | +7.6 | +5.8 | +3.1 |
| no-tilt, 300–1000 ms | −8.9 | −9.6 | −7.5 | −2.8 | +6.9 | +4.2 |

Without the tilt, the strike's low end is within ±3 dB below 2 kHz, so **restoring Hh1 is the right direction**.
The **pure digital differentiator is what overshoots both ends**. The top octave is still +3 to +7 dB too bright even
without the tilt, which points at **Hh2's corner**: it is carried at 10.5 kHz and now a high-pass, and the reference
does not resolve it.

## Next decisive step

The two remaining unknowns are circuit values, not tuning knobs:
1. **Hh2's corner and Q**, from SN p.13's component values for the Sallen-Key on the DECAY band.
2. **The LEVEL buffer's differentiator corner.** A real RC differentiator is a first-order high-pass with a corner, not a pure (1 − z⁻¹) up to Nyquist.

Both come from the schematic (SN p.13 and W14b §9–11). They are to be read and derived exactly as Hh1 was, then the
candidate rebuilt with those values fixed before rendering. If a value cannot be read, it is stated as unresolved,
not fitted.

The knob laws (`test_discrimination.kit_at`) still need their own labelled repair before any selection across the 9
development settings.

**Listening pack:** `/tmp/gf180-listen/cymbal-candidate/` on the laptop: 808, shipped, 808, candidate at CY5025,
loudness-matched. Built with `tools/make_cymbal_pack.py`.
