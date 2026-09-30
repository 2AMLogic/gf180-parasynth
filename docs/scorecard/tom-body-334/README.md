# Tom/conga body spectrum: one shared cause, and what blocks its fix (#334)

This is model evidence on the selected engine. The kit digest `321a93546cfa` equals R1's pinned revision-14 kit,
so the numbers are R1's drum model. Nothing here changes the kit, RTL or scorer.

## Reproduction

`run_case.py D03A–D08A` reproduces the published board exactly (`results/`). For example, D03A's body spectrum
reads −40.6387 dB against a reference of −22.5321 dB, distance 6.04.

| case | body spectrum, ours / reference (dB) | distance | pitch (or drop) distance | decay distance |
|---|---|---:|---:|---:|
| D03A LT | −40.64 / −22.53 | 6.04 | 0.06 | 0.04 |
| D05A MT | −38.23 / −21.84 | 5.46 | 0.14 | 0.04 |
| D07A HT | −36.78 / −23.23 | 4.51 | 0.14 | 0.04 |
| D04A LC | −42.18 / −29.76 | 4.14 | 0.77 | invalid |
| D06A MC | −39.39 / −27.42 | 3.99 | 0.04 | 0.00 |
| D08A HC | −42.27 / −31.36 | 3.64 | 0.30 | 0.01 |

## The common cause (`tools/diagnose_tom_body.py`, `decompose.json`)

`body spectrum` is E[split..2 kHz] / E[20..split] over the first 150 ms. The tool splits the above-split energy of
each side three ways:

- the strike transient (first 10 ms after the scorer's t = 0);
- harmonic lines after that;
- everything else.

It also tracks that energy in 2 ms blocks. The known-answer suite (`tools/test_diagnose_tom_body.py`) plants each
component at a known level.

| position | reference: transient share | ours: transient share | transient deficit, ours − reference | body-spectrum deficit |
|---|---:|---:|---:|---:|
| LT | 0.978 | 1.0 | −18.2 dB | −18.1 dB |
| MT | 0.985 | 1.0 | −16.4 dB | −16.4 dB |
| HT | 0.989 | 1.0 | −13.6 dB | −13.5 dB |
| LC | 1.0 | 1.0 | −12.6 dB | −12.4 dB |
| MC | 1.0 | 1.0 | −12.0 dB | −12.0 dB |
| HC | 1.0 | 1.0 | −11.0 dB | −10.9 dB |

**In all six, the metric is the strike transient.**

- **Where the energy is.** 98–100 % of the reference's above-split energy arrives in the first 10 ms. Ours has the same time shape (it falls about 10 dB per 2 ms on both sides) but starts 11–18 dB lower. The transient deficit equals the body-spectrum deficit to within 0.2 dB, voice by voice.
- **Ruled out: recording floor.** The reference's file tail sits at −89 to −99 dB re peak.
- **Ruled out: the toms' missing pink-noise path.** Late above-split content is −41 to −49 dB and is absent in the congas. It is under 2 % of the metric.
- **Ruled out: sustained ring harmonics.** They are also under 2 % of the metric.
- **Consistent with: an all-pole resonator.** Ours is a 2-pole, all-pole mode pinged by a 0.1 ms pulse, so above f0 it falls 12 dB/octave. A resonator with a numerator zero falls 6 dB/octave less, which is the size of the gap.

## The mechanism tested (`tools/probe_tom_numerator.py`, `numerator.json`)

The bank implements numerators on modes 0–10, and the tom circuits sit on 11–13. BD/SDLO/SDHI on 8–10 are RAW, so
a kit remap gives the toms numerators with no RTL change. That remap is bit-identical to running the bank with
`nums = 16`, which is what the probe does. A control shows `nums = 16` with every numerator RAW reproduces the
shipped render exactly, for LT, BD and SD.

**Candidate budget and rule, fixed first.**
- Candidates: BP, HP, and a third held back until the first two were measured.
- The mode's amp is compensated at f0, so a candidate cannot win on level.
- Selection is on the toms, admissible only if pitch and decay distances stay ≤ 1 and grow by ≤ 0.10, and the bus peak stays within 1 dB.
- Confirmation is on the congas.

| candidate | LT / MT / HT body distance | LC / MC / HC body distance | verdict |
|---|---|---|---|
| shipped (RAW) | 6.04 / 5.46 / 4.51 | 4.14 / 3.99 / 3.64 | — |
| **BP** (1 − z⁻²) | **0.75 / 0.76 / 0.26** | **0.63 / 0.16 / 0.26** | rejected: LT decay 0.04 → 0.53, MT 0.04 → 0.68; MC/HC decay estimator refuses |
| HP ((1 − z⁻¹)²) | — | — | REFUSED: compensated amp 34.6 exceeds its Q0.16 register |
| BP+X4 (exciter to its register ceiling, amp compensated) | 0.68 / 0.73 / 0.25 | 0.63 / 0.16 / 0.26 | rejected: LT/MT decay estimator refuses (HT 0.05, MC 0.19, HC 0.04 recover) |

**Negative result by the frozen rule. Nothing is promoted.** It is informative, though:

- A numerator closes the body-spectrum gap on **all six** positions at once: every distance goes to ≤ 0.76, and pitch is preserved.
- Selection used the toms, and the congas confirm the spectral effect.

## Why the decay breaks: a deadband in the modal bank

Decay is not a property of the numerator. An LTI resonator's decay does not depend on its zeros. The explanation
(`numerator-explain.json`) shows the **ring itself** decays faster with BP: LT's f0-band T20 goes from 202 to
131 ms, and MC's from 88 to 29 ms.

The known-answer check (`deadband.json`) pings one bank mode (the LT pole pair) at four levels and compares it with
a float recursion that uses the same integer coefficients:

| ping (LSB) | fixed departs from float by > 3 dB at | float level there | tail |
|---:|---:|---:|---|
| 100 | 10 ms | 7,258 LSB | 2 LSB limit cycle |
| 1,000 | 230 ms | 6,261 LSB | 3 LSB limit cycle |
| 10,000 | 410 ms | 7,874 LSB | stuck DC at 7,201 LSB |
| 100,000 | 640 ms | 5,990 LSB | stuck DC at 7,201 LSB |

The departure level is the same whatever the ping, at about 6,000–7,900 LSB of state. That is a **floor-rounding
deadband** in the recursion, and pure LTI behaviour cannot produce it.

- **How it hits the candidate.** A numerator lowers the state by |N(ω₀)| (−32 dB at LT). Output amp restores the level but not the state, so the ring reaches the deadband 32 dB sooner. The Schroeder decay then reads the collapse. The exciter is already near its register ceiling, which buys back only 12 dB.
- **What it already does to the shipped kit.** Tails end in a limit cycle or a **stuck DC state** rather than decaying. Whether that is audible at the bus depends on each mode's amp. It is filed as #350.

## Next decisive step

**Remove the modal bank's deadband** (more fractional state bits, or a rounding change), with a known-answer decay
test and injected controls. This is an RTL change to the modal datapath, so it goes to the next image. The
numerator candidate for #334 should then be re-run unchanged. It is the measured mechanism for all six body-spectrum
failures, and the budget is spent until the blocker is gone.

**Wrong-then-right this task: 5**, all caught by known-answer tests or controls before any number was reported:

1. Rectangular-window leakage of the fundamental read as "other" energy.
2. Uncapped harmonic masks overlapped from k = 8 and called noise harmonic.
3. The first windows ignored `prepare()`'s guaranteed lead, so "early" was silence.
4. Two known-answer expectations mis-stated the physics.
5. The BP+X4 exciter was set one LSB past its register ceiling.
