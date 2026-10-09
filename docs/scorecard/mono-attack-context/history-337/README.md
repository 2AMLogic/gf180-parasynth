# Envelope response (#337): can residual envelope level explain the prior-note attack shortening?

**Pre-declaration, committed before any number from the instrument existed.**
Nothing here changes the engine, a patch, a scorer or a tolerance.

## Which open #337 area, and why

#337 leaves drift, resonance and envelope response open after #583. Envelope
response has the best frozen data: 36 hash-bound Mini V3 renders
(`../report.json`) and 12 matched selected-engine renders (`../model/`). Drift
has 0 usable windows on the short frozen notes
(`../../mono-osc-drift/reference-drift-v1.json`, `TOO_FEW_WINDOWS` and
`TOO_FEW_PERIODS`). Resonance has no frozen resonant mono reference at all.

## What is already known (not re-derived)

The Mini V3 attack at MIDI 84 is 7.3-8.5 ms with no preceding note and 2.9-3.6 ms
after a preceding note, 3.4-5 s earlier. The selected model is 8.5-8.8 ms in
every context (`../model/README.md`). That unmet requirement stays open
whatever this diagnostic finds.

## The question (one)

The earlier note ended its release 3 s or more before the target note starts.
Is the Mini V3's shorter attack after a preceding note explained by **residual
envelope level at the target onset** (an envelope that restarts from where it
is rather than from zero)? Mechanism status going in: `unverified`
(verification-rules rule 7); this diagnostic is the test of that status.

## Baseline

Selected-engine model, unchanged: attack 8.5-8.8 ms in all six contexts for
both waveforms. Reference: as above. Zero candidates are built before the gate
below.

## Target property and the gate (reference-only; no model render)

For each of 36 reference renders, `r` = RMS (dB re the target note's held RMS)
of the 40 ms ending 5 ms before the target note-on. The window ends before the
onset so the attack cannot leak in.

- **VIABLE** iff, for both waveforms, the median `r` over the three
  history contexts (repeat84_gap3p4, from72_gap3p4, repeat84_gap5) is
  at least **-30 dB** AND the median `r` over the no-history contexts
  (isolated84, delayed84_at4p1, delayed84_at5p7) is at least 20 dB lower.
  Reason for -30: a restart from a level below 3 % of held cannot move a
  10 %-of-peak crossing by 5 ms of a ~8 ms rise.
- **REFUTED** otherwise. Then no restart-from-level candidate is built: it
  would have nothing to be fitted to.
- **REFUSED** (not refuted) if any render lacks a note-on preceded by 45 ms of
  samples, has a non-finite sample, has a silent held level, or fails its
  hash. A digital-zero residual is reported as floor-limited, never as -inf.

## Resolution check (declared because the scorer's window is 5 ms)

The scorer reads attack from a 5 ms centred RMS envelope. A 3 ms attack is
shorter than that window. A second instrument reading, `attack_10_90` at 1 ms
RMS (about 1 pitch period at MIDI 84), is reported beside it for every
render. It is supplementary: it does not enter the gate, and no rubric is
corrected here (plan098 §7).

## If VIABLE (not expected to be reached without a build-box run)

One mechanism, at most 3 candidates. Selection contexts: repeat84_gap3p4 only.
Confirmation, untouched by selection: from72_gap3p4 and repeat84_gap5. Preservation set
with limits: isolated84 and delayed84 attack unchanged to 0.5 ms; held RMS within
0.5 dB; f0 within 1 cent; H2-H12 power mean within 0.5 dB. A candidate may not win by
being quieter or darker. RTL and production-path equivalence are then separate
build-box work.

## Controls the instrument must carry (rule 2, rule 8)

1. Start red: the gate is run on a stub whose onset index is wrong by +40 ms;
   it must report a different `r` and not VIABLE/REFUTED silently.
2. Synthetic ground truth: a tone with a known pre-onset residual of -20 dB
   must read -20.0 dB +-0.1.
3. Defeating inputs: NaN sample, all-zero pre-onset, window that would include
   the onset (injected `end_offset=0`), hash mismatch. Each must REFUSE or move
   the number as stated, in tests.
