# 0005: Gain structure — where saturation is designed, where headroom lives, and the VCA after the filter

- **Status**: proposed
- **Date**: 2026-09-17
- **Decided by**: block agent, from the instrument evidence below and measurements in `model/` (`test_voice_fx.py`, the DR 0005 tests; `voice_fx_render.py`)

## Context

Four of the eight audition patches exceed full scale; in revision 1 they
clip at the ladder output's 16-bit clamp (contract 12, open item 17.6), and
that clip is the whole 13 dB float-versus-fixed gap on `growl-bass`. The
float renderer hid it by normalising afterwards. The question is where
headroom should live — mixer, filter input, output stage or host — and
whether the clipping is part of the sound.

A second thing surfaced while making the voice continuous (DR 0003): the
audition chain applies the amplitude envelope **before** the filter
(contract 1, 9). With one continuous filter, a self-oscillating patch then
never ends — the envelope closes the filter's input and the filter keeps
singing at full level. Measured: the whistle riff's ladder output is at
0.41 × full scale at the end of the render, after every gate is off. The
float harness never showed this because it truncated every note at its
duration.

### What the instruments do

- **The Minimoog's overdrive is the mixer into the filter, and it is the
  sound.** Moog: "By increasing the External Input volume far enough, the
  Mixer can overload, introducing varying levels of overdrive or distortion"
  and, at the extreme, "it is possible to overload the mixer to the point
  that only one sound is heard and different pitches do not sound. This will
  not damage the instrument." — 2016/2022 Model D manual,
  <https://www.moogmusic.com/sites/default/files/Minimoog_Model_D_Users_Manual_Web.pdf>.
  The service manual's factory modification "to reduce intermodulation
  distortion which occurs when mixing two or more signals" is on the filter
  board, where the mixer summing stage and the ladder live —
  <https://www.synfo.nl/servicemanuals/Moog/MINIMOOG-D_SERVICE_MANUAL.pdf>.
  Whether the clipping element is the summing transistors or the ladder's
  input pair is **not stated in any source found**. Moog's modern manuals
  describe the same control as the filter input: "Mixer settings higher
  than 5 will overdrive the input of the filter" (Sub 37,
  <https://www.moogmusic.com/sites/default/files/Sub_37_Web_Manual_8_13.pdf>);
  "The VCOs begin to clip the filter at about 2 o'clock" (Sirin,
  <https://api.moogmusic.com/sites/default/files/2019-02/Sirin_Manual_2_1.pdf>).
- **The feedback trick** — output into the external input — is the same
  overdrive with more gain: "many players connected the Minimoog's unused
  audio output … to its external signal input, thus creating a feedback
  loop" (<https://www.soundonsound.com/reviews/moog-minimoog-model-d>); the
  reissue builds it in ("the Main Output signal is sent back to the input of
  the mixer"), Arturia copies it as Brute Factor. This survey is context
  only here; the tap point, gain/delay/saturation parameters and the
  digital-return-vs-physical-input distinction for this chip are decided in
  DR 0016.
- **The VCA is after the filter.** Mixer → filter → loudness contour → volume
  is the Minimoog's chain and every subtractive synth's; the "Loudness
  Contour" is the VCA's envelope (owner's manual, above).
- **Output headroom is the player's, not a limiter's.** The one manufacturer
  statement found: "Rather than limit the Prophet-6's outputs to keep the
  instrument from clipping, we allow you to adjust levels at various points
  in its signal path. This gives you the option to 'overload' things in
  interesting ways" and "There is enough gain in the Prophet-6 that if you
  set some programs to a high program volume, clipping distortion may
  occur." — <https://sequential.com/wp-content/uploads/2021/02/Prophet-6-Operation-Manual-2.1.pdf>
  (same text in the Pro 3 guide). **No manual in the survey documents a
  soft clipper on a master output; an industry convention could not be
  established**, and the choice below is made on the stated grounds.

### Measured, revision 3 model (`test_reference_volume_clips_no_audition_patch`, and the script in this record's history)

| patch | ladder peak (× full scale) | output clip at `vol` 0.45 | output clip at rev 1's 0.9 | output peak at 0.45 |
|---|---:|---:|---:|---:|
| bass-classic | 1.22 | 0 % | 0.04 % | 0.53 |
| bass-octave | 1.22 | 0 % | 0.04 % | 0.53 |
| lead-line | 0.87 | 0 % | 0 % | 0.38 |
| lead-glide | 0.93 | 0 % | 0 % | 0.40 |
| filter-sweep | 0.97 | 0 % | 0 % | 0.44 |
| pluck-seq | 0.92 | 0 % | 0 % | 0.39 |
| growl-bass | **1.94** | 0 % | **4.83 %** | 0.86 |
| self-osc-whistle | 0.41 | 0 % | 0 % | — |

The RTL bench's stimulus (a full-scale square at 15 kHz, drive 3, among
others) peaks at 1.96 × full scale through an unclamped output. With the VCA
after the filter, the whistle's output is 2 LSB 0.5 s after its last
gate-off while the ladder inside is still at 13 316 LSB
(`test_note_ends_after_gate_off_even_when_the_filter_sings`).

## Decision

The signal chain and its clamps, in order (contract 1, 4.2, 12):

```
osc_k ─► × w_k ─► Σ ─► sat16 ─► ladder(gain, g, k_eff, ogain) ─► sat19 ─► × ae >> 15 ─► × vol >> 15 ─► sat16 ─► s16
                     mixer        tanh in every stage            Q4.15       VCA              volume     the rail
```

1. **Designed saturation is the ladder's `tanh`, in every stage, driven by
   `gain`.** That is the Minimoog's overdrive as Huovilainen models it (DR
   0001) and the transfer function is `TANH16` interpolated (contract 11.3);
   `gain` (2.6 × drive) is the panel's drive. The mixer's `sat16` is the hard
   rail of the Q1.15 word: weights the host normalises never reach it, and a
   host that pushes them past 1.0 gets a hard mixer overload, which the
   contract states rather than softens. There is no other nonlinearity in
   the path.
2. **The ladder's output word gets headroom: Q4.15, 19 bits, ±8.0,
   saturated (`sat19`).** It is a width, not a clip — it never fires on the
   eight patches (peak 1.94) or on the bench (1.96), and it covers the
   `(1 + 2 res)` compensation to `res = 1.5` (× 4) at a state peak of 2.
3. **The amplitude envelope is applied after the filter** — the VCA of the
   instrument: `v = (y19 · ae) >> 15`, 20 bits signed. The ladder's input is
   the mixer's output, so the drive does not follow the envelope; the note
   ends when the VCA closes, whatever the filter is doing.
4. **The output stage is a host volume and a hard rail:** `sample =
   sat16((v · vol) >> 15)`, `vol` a 16-bit Q0.15 register replacing rev 1's
   constant 0.9. Headroom is the host's, as Sequential states it. The
   reference host writes `vol = 14746` (0.45), which puts the loudest
   audition patch at −1.3 dBFS and clips none of them; a host that wants the
   rail can have it.
5. `ogain` and its `(1 + 2 res)` term are unchanged: that partial
   passband-loss compensation is what the audition heard.

## Alternatives considered

- **Keep the VCA before the filter** (the auditioned order) — a
  self-oscillating or strongly resonant patch never ends, and the audition
  never heard a note's tail (it truncated each note), so the order was never
  actually auditioned with what it affects. "The filter is driven harder on
  loud notes" is lost; the drive control is where that belongs.
- **A soft clipper at the output** (tanh through the existing table) —
  no instrument in the survey documents one; it is a second, non-Moog
  nonlinearity on every loud sample, and Sequential's explicit policy is the
  opposite. A hard rail the host stays under is what every digital
  instrument's DAC is.
- **Halve `ogain` instead of a `vol` register** — the same numbers on the
  loud patches, but `ogain` is the filter's physical constant (2Vt/vpu) and
  a `vol` is the control every instrument has; the host should not have to
  encode headroom into a filter coefficient.
- **Normalise per patch** (the float harness's habit) — hides the decision;
  a chip cannot look ahead.
- **Manage headroom through `w[k]` and `gain` alone** — possible, but the
  resonant peak is up to 1.94 × the input at these settings and reducing
  the input changes the drive, i.e. the sound; the headroom belongs after
  the filter.

## Consequences

- At the reference `vol` the voice is 6 dB quieter than rev 1 and nothing
  clips; the float-versus-fixed gap on the bass and growl patches is now the
  ladder's own quantisation (`voice_fx_render.py`), not a clamp.
- Contract 1, 2, 4.2, 9, 11.2, 11.4, 11.6, 12 and 14 change; revision 3.
  The clamp list has six entries: oscillator `sat16`, mixer `sat16`, ladder
  `u` `sat24`, ladder `y[s]` `sat24`, ladder output `sat19`, output `sat16`.
- `ladder_dp.v` gets a 19-bit `y_out` (parameter `OW`, 16 still available);
  re-verified bit-exact against the model, 28 800 samples, 0 mismatches; the
  bench's vector word grows to 128 bits.
- One more multiply per frame (the VCA), on the shared multiplier.
- Every reference sequence's sample values change (the chain order and the
  volume); rev 1's are not comparable.
