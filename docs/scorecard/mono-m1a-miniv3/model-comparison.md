# M1A model comparison

> **Selected patch: `m1a-gain-minus4db-v2`** (`tools/mono_m1a_score.py:
> PATCH_VERSIONS`) — the provisional mapping with the fixed −4 dB output
> volume of [the volume candidate](volume-mapping/README.md), applied in the
> patch before rendering. Promoted through `tools/run_case.py M1A` at
> `ecdc50e`; the record reproduces the candidate's audio
> (`628a6a31…a2ba61c`) and its property vector exactly, checked by
> `tools/check_m1a_selection.py`. Current vector: Pitch −0.05059 cents
> **pass** · Harmonic shape 19.82331 dB **fail** · Envelope attack
> +8.10889 ms **fail** · Envelope release +8.47917 ms **pass** · Gain
> −1.04361 dB **pass** · Clipping 0 % **pass** · Filter envelope
> **unqualified** — 4 passing / 2 failing / 1 unqualified. Still a valid
> fail. The table below is the v1 (pre-selection) record and is kept as
> history.

This is a fixed-point **model** observation against frozen Mini V3 audio.
It has no SPI → I²S or physical-board evidence. The complete 7.5 s phrase
uses MIDI 36, 43, 36, the shared selected engine, saw plus quieter octave saw,
and a declared provisional patch. No sound code was changed to fit this result.

Latest record: `m1a-envelope-score-v1` — **a valid FAIL, the case's first
verdict.** The model render is byte-identical to the earlier no-verdict run
(same sha256), so every number below describes the same audio the earlier
record described; only the measurement apparatus changed.

| Component | Error | Screening limit | Outcome |
|---|---:|---:|---|
| Pitch | −0.05026 cents | 1 cent | pass |
| Harmonic shape | 19.82259 dB | 1 dB | fail |
| Envelope attack | +8.11 ms (10–90%) | 5 ms | fail |
| Envelope release | +8.48 ms (T20) | 20 ms | pass |
| Bass level | +4.97 dB | 3 dB | fail |
| Output clipping | 0% | 0.01% | pass |
| Filter envelope | unqualified mapping | — | unqualified property, not a metric |

The required envelope metric is the worst-normalized of the qualified attack
(1.62×) and release (0.42×): the model's provisional 10 ms amplitude attack
measures 7.4–9.0 ms 10–90% against the reference's 0.27–1.64 ms. That is a
real mapping finding, not an apparatus gap, and the case fails on it — a
valid failure is coverage.

## What qualified the attack

The 40 ms RMS window — qualified for release only — cannot measure an 8 ms
attack: an 8 ms rise needs ~125 Hz of envelope bandwidth, which overlaps the
65 Hz harmonic spacing of a 56 Hz carrier, so envelope and carrier ripple are
entangled in *any* pointwise envelope, analytic included. The qualified
estimator is a **waveform-domain fit**: fold the steady gate into one
carrier-period template (8× upsampled, fractionally aligned to absolute
time), then least-squares a piecewise power-ramp envelope against the whole
note window. A short-ramp-in-sustain solution must leave the true transition
unexplained, so the full-window residual cannot prefer it.

Ground truth (docs/failure-modes.md rule 1): 56 known signals — spans
0.5–20 ms, shapes p = 0.5/1/2, MIDI 36/43, four carrier phases, 400 Hz and
3 kHz carriers, bright-attack transients standing in for the filter envelope.
Demonstrated worst error **0.50 ms**; the qualification gate refuses at
1.0 ms. The fit must also explain ≥ 60% of the window's energy or it REFUSES
(known signals fit at ≥ 0.98; the real reference audio at 0.74–0.83). The
rejected RMS windows are kept as controls that must stay red on the same
signals — the 40 ms window errs ≥ 10.6 ms and the 5 ms lead window up to
11.1 ms depending on carrier phase, which is why phase-dependent luck made
the 5 ms window unqualifiable.

Wrong-then-right while building the estimator — six defects, all caught by
the ground-truth suite rather than inspection: a correlation-only objective
that preferred sustain matching (T = 2 ms in sustain); the template phase
sign, wrong twice; an FFT correlation offset; integer-period fold smear
(−0.55 ms at MIDI 43); coarse-only shape refinement (−0.58 ms); and a silent
input that "explained" NaN until a refusal was added. Historical: the fourth
apparatus correction had stopped release qualification being treated as
attack qualification; this estimator is the fifth and the first that passes.

## Model D cross-check

Removed from the metrics dict, kept as recorded missing evidence. It is not
in the case's `required_measurements`; Model D renders exact digital silence
under the qualified host (dawdreamer, #123); and an always-invalid
non-required metric made the case permanently no-verdict — an unsatisfiable
gate (docs/failure-modes.md). The screening policy
(`../mono-m5a-policy.md`) accepts the frozen Mini V3 software reference
alone; `reference_profile` and `diagnostics.missing_evidence` carry the
limitation on every record.

Reproduce with `python3 tools/run_case.py M1A`. Expected exit is **1** (a
measured mismatch); a bad sound measurement and missing evidence remain
distinct outcomes. `--inject MONO_PITCH_UP_25_CENTS --expect changed`
demonstrates the verdict moves on a real defect (worst 19.82 → 25.07).
Per-note signed partials, attack-fit diagnostics, all settings and hashes
are in `../results/M1A.json`.

The next measured component improvement is the fixed −4 dB output-volume
candidate: it moves the Gain property inside tolerance (worst signed gain
error +4.97 → −1.04 dB) while preserving every other verdict, including the
envelope attack failure unchanged by construction. The case remains a valid
fail. See [the fixed volume challenger and oscillator
diagnosis](volume-mapping/README.md); the attack sweep that follows it holds
the reference, estimator and every other patch setting fixed.
