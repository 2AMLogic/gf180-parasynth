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

## Result (instrument: `tools/envelope_history_337.py`, tests: `tools/test_envelope_history_337.py`)

**REFUTED. Residual envelope level does not explain the shortening, and no
restart-from-level candidate is built. The unmet requirement stays open.**

Effect (measured, 36 renders, medians of three; attack in ms):

| wave | context | 5 ms scorer | 1 ms | pre-onset residual |
| --- | --- | ---: | ---: | --- |
| saw | isolated84 / delayed84_at4p1 / at5p7 | 7.33 / 8.29 / 8.54 | 7.40 / 7.90 / 7.81 | exact digital zero |
| saw | repeat84_gap3p4 / from72_gap3p4 / repeat84_gap5 | 3.10 / 3.21 / 3.56 | 1.96 / 1.92 / 1.94 | exact digital zero |
| pulse | isolated84 / delayed84_at4p1 / at5p7 | 7.46 / 7.96 / 8.31 | 7.40 / 7.60 / 7.44 | exact digital zero |
| pulse | repeat84_gap3p4 / from72_gap3p4 / repeat84_gap5 | 2.88 / 2.85 / 3.38 | 1.69 / 1.67 / 1.67 | exact digital zero |

All 36 residuals are floor-limited: the audio is exactly 0.0 for at least the
whole 40 ms window, in the history contexts as in the others (exactly zero for at least 1.79 s before the target in every history render; the earlier note's
release ends at about 2.31 s and the plugin is exactly silent from there to the
4.10 s or 5.70 s note-on). The gate needed history residual >= -30 dB and a 20 dB
contrast; it got -120 against -120.

Second effect, from the resolution check: the shortening is not an artefact of the
5 ms window. At 1 ms the warm attack is about 1.7-2.0 ms against 7.4-7.9 ms cold,
a larger contrast than the scorer reports. The warm onset reaches 0.9 of held level
by about 2 ms; the cold onset is still at 0.8 at 7 ms. Both begin at the same sample
offset (0.44 ms) after the note-on.

Limits, stated as a separate sentence from the effect (rule 7). The refutation is
of an audible residual in the output. It does not exclude internal plugin state that
is not audible while the voice is silent. The mechanism for the difference is
still `unverified`: the data distinguish "a note has already sounded in this
session" from "none has", and cannot say whether 4 s or 20 s of idle changes it
(the longest idle tested is 5.6 s before). The model's attack is 8.5-8.8 ms in every
context, so it matches the cold condition (error +0.2 to +1.4 ms) and misses the
warm one by 5.0-5.9 ms, unchanged from `../model/README.md`.

Guard adversaries (rule 8): an all-zero file is REFUSED rather than read as
"below -30 dB" (`test_all_silent_file_is_refused_not_refuted`); a window that reaches
the onset, a NaN, too little lead-in and a hash mismatch each REFUSE; a late onset
index is not silent (`test_wrong_onset_moves_the_number_start_red`); a known -20 dB
residual reads -20.0 +-0.1; and the instrument sees a real residual in the frozen
earlier-note release (`test_positive_control_on_real_audio_sees_the_previous_release`).
The adversary that remains is the one the refutation depends on: REFUTED is also the
verdict for any file whose pre-onset audio is exactly zero. That is stated in
the result above and is why the zero-run length is reported, not only the verdict.

What this does not change: no sound, engine, RTL or image changed. Nothing here
reaches the hardware. Wrong-then-right: 0 corrected measurements; 1 test
(the real-audio positive control) was specified against the wrong held-level
window and corrected before the result was recorded.

## What a next step would need

A mechanism that is not envelope level. Candidates are mechanisms in the Mini V3
that depend on a previous note having sounded (voice or oscillator start-up,
VCA/filter smoothing). Choosing between them needs a new reference capture with
idle times well beyond 5.6 s and a second note shortly after the first, which needs
the macOS plugin rig; neither this host nor the build box has it. The shorter route
is to ask whether an attack that depends on note history is a Mini V3 property the
product needs at all (an operator ruling).
