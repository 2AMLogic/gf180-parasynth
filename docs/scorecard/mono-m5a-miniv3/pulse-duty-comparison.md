# M5A pulse duty sweep

The frozen Mini V3 M5A pulse segment was compared with each rectangular duty
already supported by the voice model. The probe holds the selected 2× path,
filter cutoff, filter drive 0.75, envelope, and note windows fixed. The WAV and
manifest hashes, code hashes, and per-note harmonic diagnostics are in
[`pulse-duty-v1.json`](pulse-duty-v1.json).

| Model shape | Duty | MIDI 84 max harmonic error | MIDI 84 excess alias | MIDI 96 max harmonic error | MIDI 96 excess alias |
| --- | ---: | ---: | ---: | ---: | ---: |
| square | 50% | 93.1901 dB | 19.7014 dB | 89.5744 dB | 21.5061 dB |
| pulse15 | 15% | 21.0979 dB | **16.2950 dB** | 19.7263 dB | 18.7390 dB |
| pulse25 | 25% | 19.7916 dB | 18.1947 dB | 18.9990 dB | 19.4168 dB |
| pulse29 | 29% | **19.3092 dB** | 18.6204 dB | **17.9919 dB** | 20.0613 dB |

The model's 50% square is a poor match to this recorded pulse even though the
reference estimator measured a 47.9% duty: even harmonics that are nearly
absent in an ideal square are present in the Mini V3 recording. Among existing
model duties, 29% gives the best worst-note harmonic-shape error; 15% gives the
lowest alias excess. Neither passes the 1 dB shape or 3 dB alias limits. This
is evidence to test the supported 29% setting as a bounded pulse intervention,
not evidence that the 29% waveform reproduces the reference pulse.

The report is a fixed-model comparison. The probe does not change the default
waveform or claim RTL/I2S phrase verification.
