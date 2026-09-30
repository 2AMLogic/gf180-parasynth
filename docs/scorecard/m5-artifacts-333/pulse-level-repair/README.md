# R2 pulse candidate: the one bounded repair (rectangle level into the ladder). Also NOT ACCEPTED

**Why this repair.** R2's pulse candidate (pulse2x, rectangles at 0.74) fails the declared rule against R1 at 6 of
107 conditions (#343 §D). Five are brightness losses at drive 1.6. Rectangles at 0.74 drive the tanh ladder about
2.6 dB less than R1's full-scale base-rate rectangle, so the ladder generates fewer upper harmonics.

**The repair.** For rectangle-only patches, the ladder drive is raised by 1/0.74 = 1.3514. Through the host's
`ladder_regs` this restores R1's level into the ladder. It is preset data, with no RTL change.

A mixer-weight version was tried first. The host's `mix_weights` normalises a single oscillator back to 1.0, so it
was a no-op: the records came out identical to the unrepaired candidate. It is kept only so that record stays
reproducible. Wrong-then-right 1.

The comparison tool first paired rows by the rendered patch, so it paired none of the drive-compensated rows. It then
reported acceptance over 0 measured points. That was caught before any report. Pairs now use the scheduled
condition, and an unpaired or refused pair yields NO VERDICT. Wrong-then-right 2.

The rule is unchanged (the 1.00 dB band declared at `d1fe013`). The tool is `tools/compare_r2_pulse.py`, the probe
records are at `1af59e1`, and sources are clean.

| set | conditions | fail the rule | failing conditions |
|---|---:|---:|---|
| the 110 standard (tuned on, so not a holdout) | 110 | **3** | two new **output rail** conditions (pulse29 and square, MIDI 36, cutoff 21.6k, q 0.5, drive 4.0 × 1.35); one brightness (pulse29, MIDI 84, cutoff 400, −1.21 dB) |
| **fresh** (MIDI 42/54/66/78/90/102/114 × pulse29/square × cutoff 20k/8k × q 0/0.5 × drive 0.75/1.6) | 112 | **11** | relative and absolute unwanted +1.14 to +1.98 dB at MIDI 42 (8 conditions) and MIDI 114 (3), all at drive 1.6 |

**M5 phrases with the repair** (`phrases-074-drivecomp.json`):

| | foldback | Gain |
|---|---|---|
| M5A | 2.21 dB, unchanged | −1.54 → **+1.42 dB**: the worst event changes from the too-quiet pulse at MIDI 96 to the too-loud saw at MIDI 84 (the magnitude falls by 0.12 dB) |
| M5B | unchanged | 2.05 → **2.71 dB** |

M5B's gain change is **0.66 dB**, which exceeds the 0.5 dB preservation allowance. It stays within the 3 dB
absolute limit.

**Verdict: NOT ACCEPTED.** Restoring the level fixes most of the brightness losses. It also puts R1's aliasing
behaviour back where the harder drive saturates (MIDI 42 and 114), and it rails the output at extreme drive. The one
bounded repair is spent.

R2's pulse candidate stays **not accepted**. The pulse2x alias benefit at the M5 notes is real. The unresolved
tradeoff is brightness at drive 1.6 and resonance.

The next step is not another level knob. It is a question about the product: is R1's drive-1.6 brightness on
rectangles wanted character, or saturation of an aliasing input? Answering it needs a reference-relative measurement
of brightness, not an R1-relative one.
