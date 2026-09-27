# #369 cymbal: a qualified band measurement, and where the shipped cymbal differs from the 808

Everything here is model evidence against the Fischer TR-808 recordings (`cy8/CY{TONE}{DECAY}.WAV`, decoded as in
DR 0022). No kit, RTL or scorer changes. R1 is unchanged.

## 1. The measurement (`tools/cymbal_bands.py`, tests in `tools/test_cymbal_bands.py`)

**Bands.** The bands follow §10 of the reference:

| band | range | what it holds |
|---|---|---|
| L | 2–5 kHz | the low band |
| Ln | 2.9–4.1 kHz | the low band's 3.45 kHz peak, where the shared 7.1 kHz band-pass is about 17 dB down |
| H | 6–14 kHz | both high bands |

**Quantities, per band.**
- Energy share of the first 1 s after the strike, in dB relative to 200 Hz–20 kHz.
- EDT10 and a late T20 (−10 to −30 dB), taken off the floor-subtracted Schroeder curve.
- Every filter is zero-phase and run from `prepare()`'s guaranteed lead (#101).
- `thirds()` adds 1/3-octave energy, 1–20 kHz, in three time windows.

**Refusals.** The tool refuses a quantity rather than answering when:
- the curve does not reach −30 dB;
- the record ends less than 15 dB (of envelope) after the −30 dB point;
- the late residual exceeds 1.5 dB.

**Known answers (6 tests, passing).** These are synthetic three-band strikes with planted time constants and levels:
- single-exponential T20 to within 5 %;
- a two-slope high band, where EDT reads the short component and the late T20 reads the DECAY component to within 8 %;
- a planted 6 dB level change reads as 6 dB;
- invariance to scale and to prepended silence;
- swapped band decays move both bands;
- a truncated record refuses.

**Wrong-then-right 1.** The first version fitted the raw 5 ms envelope and refused **every** 808 file, because six
beating squares make the envelope wander 2–4 dB. It now uses the Schroeder curve.

**Checked against the recordings themselves** (`fischer.json`, all 25 settings). These are consistency checks against
the knobs' known physics, not a fit:
- **DECAY.** H's late T20 rises monotonically with DECAY in every TONE column, from 250–295 ms at DECAY 0 to about 1,090 ms at DECAY 10. At fixed DECAY it is nearly independent of TONE. 23 of 25 are measured; 2 refuse as non-exponential.
- **TONE.** H−L rises monotonically with TONE at every DECAY, for example 7.2 → 8.1 → 9.5 → 11.1 → 14.6 dB at DECAY 0.
- **The low band's own decay tracks DECAY.** Ln EDT rises from about 400 ms to about 1,280 ms in every TONE column. This is measured where the high bands' skirt is about 17 dB down, so **the recordings contradict §10's "DECAY changes only the middle band's RC"**. The kit's knob law already scales the low band's envelope with DECAY, which agrees with the machine.
- **The low band's late decay is mostly two-slope.** Its late T20 refuses at most settings, so the low band's qualified decay measure is EDT10.

## 2. The frozen development/confirmation split

This was fixed in `cymbal_bands.py` before any candidate existed.
- **Development (9 settings).** The knob laws' fit points (the TONE 5.0 column and the DECAY 5.0 row) plus the D14A anchor: CY0050, CY1050, CY2550, CY5000, CY5010, CY5025, CY5050, CY5075, CY7550.
- **Confirmation (16 settings).** All the others, including **CY2500**, which is D14B's holdout.

## 3. What the shipped cymbal gets wrong (`shipped-vs-CY5025.json`)

The shipped kit, which R1 plays with no TONE or DECAY control, compared with its anchor CY5025:

| quantity | 808 CY5025 | shipped |
|---|---:|---:|
| H − L | 8.16 dB | 10.82 dB |
| H EDT10 | 148 ms | 151 ms |
| Ln EDT10 | 591 ms | 598 ms |

The coarse band balance and decays are close, so they do not explain what the operator heard. The 1/3 octaves do
(shipped − 808, dB):

| window | 1.0 | 1.26 | 1.59 | 2.0 | 2.5 | 3.2 | 4.0 | 5.0 | 6.3 | 8.0 | 10 | 12.7 | 16 | 20 kHz |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0–50 ms | **+15.4** | **+11.2** | **+10.4** | **+9.1** | **+9.4** | +0.8 | −2.3 | −2.9 | −2.0 | +2.9 | −2.3 | −2.2 | +0.2 | **−7.0** |
| 50–300 ms | **+9.4** | +3.7 | +0.7 | −1.8 | −1.4 | −5.1 | −1.3 | −5.8 | −3.5 | +3.2 | +1.1 | +0.4 | −0.5 | **−7.9** |
| 300–1000 ms | **+8.8** | +3.9 | +1.7 | −2.4 | −1.7 | −3.4 | +4.1 | −2.1 | −3.2 | +3.4 | +2.2 | +1.4 | +1.5 | −1.1 |

- **Below 2.5 kHz the strike has 9–15 dB too much energy.** That is the shape of the **omitted Hh1**, the 2.5 kHz Q 0.97 high-pass on the low band that the kit dropped. The operator decision now allows restoring it.
- **The top octave is 7–8 dB short (16–20 kHz)**, and 8 kHz is about 3 dB too much. This is consistent with the short high band being routed through the closed hat's **11.7 kHz** 2-pole high-pass instead of its own **Hh3 (≈10.5 kHz, third-order)**, and with the missing level-stage +6 dB/oct tilt (§10, "LEVEL's buffer… differentiator"). This is inferred, not yet isolated.
- **3–6 kHz is 2–6 dB short** after the attack.

## 4. A second finding: the knob-law render is not the shipped cymbal

`test_discrimination.kit_at("CY", knobs, laws, "ours")`, which the knob experiments and DR 0022's probes render
through, gives H − L of **−2.4 dB** at CY5025 against the shipped kit's +10.8. Its DECAY also saturates: 7.5 and 10.0
render identically (`kit_at-map.json`, which is the file formerly written as `ours.json`).

That map is **not** a measurement of the instrument. Its knob laws need their own repair before any knob-tracking
claim can be made. Wrong-then-right 2: I rendered through `kit_at` first and nearly reported a 10 dB band error that
the shipped instrument does not have.

## 5. Next step: one structural candidate, fixed before it is rendered

Restore §10's structure, as a set of discrete choices checked against the circuit, not Q hacks:
1. Low band: 3.45 kHz Q 6 band-pass → its own VCA/envelope → **Hh1, 2.5 kHz Q 0.97 high-pass**.
2. High, DECAY band: 7.1 kHz band-pass → VCA → **Hh2**, a resonant 2nd-order high-pass. Its corner is still unresolved in the reference, and that will be stated.
3. High, short band: 7.1 kHz band-pass → VCA → **Hh3, a 2-pole plus a 1-pole at the same ≈10.5 kHz corner**, instead of borrowing the closed hat's 11.7 kHz high-pass.
4. The level stage's +6 dB/oct.

**Budget.** 16 → 17+ modes. The operator has accepted padding the bank to 32, about +31 % of drum-section area. Paths and envelopes must be counted exactly.

**Selection and confirmation.**
- Select on the 9 development settings, once the knob law renders the shipped structure faithfully.
- Confirm on the 16 untouched settings, including CY2500.
- Preserve the hats: D15A, D16A and OH00–OH75.
- Then carry it through RTL, I²S and the deadlines, and produce the loudness-matched A/B pack (`tools/ab_808.py`, `tools/ab_808_loud.py`, committed here).
