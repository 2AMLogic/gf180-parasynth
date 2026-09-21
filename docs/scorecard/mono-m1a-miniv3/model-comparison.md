# M1A first model comparison

This is a fixed-point **model** observation against frozen Mini V3 audio.
It has no SPI → I²S or physical-board evidence. The complete 7.5 s phrase
uses MIDI 36, 43, 36, the shared selected engine, saw plus quieter octave saw,
and a declared provisional patch. No sound code was changed to fit this result.

| Component | Error | Screening limit | Outcome |
|---|---:|---:|---|
| Pitch | −0.05026 cents | 1 cent | pass |
| Harmonic shape | 19.82259 dB | 1 dB | fail |
| Release | +8.47917 ms | 20 ms | pass |
| Bass level | +4.97474 dB | 3 dB | fail |
| Output clipping | 0% | 0.01% | pass |
| Attack | unqualified | — | no verdict |
| Filter envelope | unqualified mapping | — | no verdict |

The component view is **three passes, two failures, two no-verdict properties**.
The case's required fundamental/harmonics and bass-level measurements fail;
its required envelope remains NO VERDICT. Its named Model D cross-check is
also missing. The official board remains **20 valid / six passing**: this
result does not increase valid coverage.

The mapping uses the measured open-filter oscillator level ratio, measured
release T20, and reference ring frequency as the resting cutoff command.
Low-resonance cutoff equivalence, resonance, attack/decay/sustain and filter
peak/time remain explicitly provisional. Those settings are recorded in the
result; they are not inferred from equal normalized plugin knob values.
Raw gain is reported without post-render normalization.

The bass RMS window remains qualified only for release. A known 8 ms linear
10–90% attack produces 19.04–20.79 ms observations across MIDI 36/43 and four
carrier phases, an error as large as 12.79 ms. Attack has no numeric distance
and cannot become a false pass. The source instrument retains all eight
observations and the original failed 5 ms release-window control.

Reproduce with `python3 tools/run_case.py M1A`. Expected exit is **2**, meaning
NO VERDICT; a bad sound measurement and missing evidence are distinct outcomes.
The WAV is `m1a-model.wav`; per-note signed partials, all settings and hashes
are in `../results/M1A.json`. Fifteen focused tests validate known bass partial
levels, known release, a 25-cent pitch mutation, corrupt/missing references,
silence/truncation refusal, and preservation of missing required evidence.

Wrong-then-right: the fourth apparatus correction prevents release qualification
from being treated as attack qualification. The first synthetic scorer fixture
had only one non-fundamental partial and correctly refused; adding its declared
third harmonic made the known-signal qualification executable.
