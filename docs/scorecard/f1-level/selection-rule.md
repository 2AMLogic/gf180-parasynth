# F1 input-level scaling: pre-declared selection rule

Committed **before any candidate was rendered or measured**. The Surge
matched-level captures (`captures/`) exist when this is written, but no
candidate curve has been computed. Candidate results must not amend this file.
A change to it after the candidates are evaluated is a new experiment and has to
say so.

## The one parameter

`s`, the ladder's input scaling. It is applied as the physical constant it
scales, `volts_per_unit`, through the host conversion `LadderFx.regs`. That
conversion sets `gain = drive·vpu·s/2Vt` and `ogain = 2Vt/(vpu·s)·(1+2·res)`.
The output compensation is therefore the exact inverse of the input scaling. The
small-signal passband level is unchanged by construction, so **level alone
cannot win**.

| candidate | s | meaning |
|---|---|---|
| baseline | 1 | today's `LADDER_CFG` (vpu 0.13) |
| half | 1/2 | vpu 0.065 in the gain/ogain conversion only |
| quarter | 1/4 | vpu 0.0325 in the gain/ogain conversion only |

The following are held fixed: source level (the stimulus amplitudes below), g/k
ROMs and `CUT_TRIM`, resonance 0, drive 1.0 (the F1 setting), state
bits/Q, tanh table, output word, the 2x causal rate converter, and `k_comp`. The
path is the #228 selected-path probe (`tools/probes/f1_selected_path.py`
`render_path`). Its identity and exact-match checks must pass first.

## Register precondition (REFUSED, not a result)

For every candidate, the `gain`/`ogain` words that **actually enter the ladder**
are recorded at the call and asserted to be:

1. the words the candidate's conversion predicts, and
2. different from the baseline's (for the non-baseline candidates).

A candidate whose words equal the baseline's is **REFUSED**, because it would be
measuring the baseline's audio under another name. A constructor-only change is
the known trap: setting a voice's `ladder_cfg["volts_per_unit"]` leaves
`VoiceFx.patch_regs`, which uses the global `LADDER_CFG`, unchanged. The
control demonstrates this trap and its refusal.

## Measurements (per candidate, per level, per case)

Levels: stimulus amp 0.25 (−12.04 dBFS, the F1 case level), 0.125 and 0.0625.
Each level is compared against the Surge capture **at the same amplitude**. The
two takes are bit-identical, so take 1 is used and the identity is asserted.

- **Corner** (Hz, and % error vs matched reference): `run_case.filt_corner`
- **Rolloff** (dB/oct, error): `run_case.filt_rolloff`
- **Low-band gain** (dB, error): `run_case.filt_lowband_gain`, each device
  against its own wide-open curve at the same level
- **Noise**: in the wide-open condition, for each tone ≤ 4 kHz, the RMS
  residual after a least-squares fit of DC plus harmonics 1–9. It is reported
  in dB re the stimulus RMS, median over those tones. **THD** is reported
  beside it from the same fit, harmonics 2–9 re the fundamental.
- **Clipping**: reconstruction would-clip count, output words at the 19-bit
  rails, internal `sat()` events in the ladder (state or input word
  saturated), and tanh-domain clamps (|u| ≥ 4.0). All come from counters on the
  real run. The counters are verified not to change a single output word.

## Selection rule

The candidate is scored at amp 0.25, the F1 level. The other two levels are
development checks and are not refit.

A non-baseline candidate is **eligible** only if every item below holds:

1. **Registers:** it passed the register precondition, with words different
   from the baseline's.
2. **Validity:** every estimator reads a value (no refusal) for all three cases
   at all three levels.
3. **Corner (the targeted property):** at amp 0.25, the mean |corner error|
   over F1A–F1C falls by **≥ 3.0 percentage points** relative to the baseline,
   and no single case's |corner error| is worse than the baseline's.
4. **Rolloff preserved:** at amp 0.25, no case's |rolloff error| exceeds the
   baseline's by more than 0.30 dB/oct. No case that is within the 1.5 dB/oct
   tolerance for the baseline may fall outside it.
5. **Low-band gain preserved:** at amp 0.25, every case is within the 3 dB
   tolerance and none is worse than the baseline by more than 0.50 dB.
6. **Noise:** the wide-open median noise at amp 0.0625, the worst level for
   quantisation, is at least **60 dB below the stimulus**.
7. **Clipping:** zero reconstruction clips and zero output-rail words at every
   level. At amp 0.25 there must be no more tanh-domain clamps and no more
   internal `sat()` events than the baseline has.
8. **Level dependence (the hypothesis):** across the three levels, the
   spread (max − min) of corner error % per case must not exceed the
   baseline's in any case.

**Selection:** among the eligible candidates, pick the one with the smallest
mean |corner error| at amp 0.25. If two are within 0.5 points of each other,
pick the one with the larger `s`, which is the smaller departure and the one
with less noise. If none is eligible, **the selection is none**, recorded as
such, and no wider sweep is opened.

## Scope of any selection

Any selection is a **calibrated operating point for this rig**: Surge Type 2
comparator, F1 stepped tone, res 0, drive 1.0, the selected path. It does
**not** license changing `LADDER_CFG`, `volts_per_unit`, the drive→gain
conversion or any musical patch. `s` enters the gain register exactly as drive
does, so `s = 1/2` has the same gain word as drive 0.5. The two differ only in
the ogain compensation. #226 showed lowering drive loses harmonic passes. The
README says what a global change would additionally require.
