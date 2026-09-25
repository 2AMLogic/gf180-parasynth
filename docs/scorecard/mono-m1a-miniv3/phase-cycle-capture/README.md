# M1A phase-cycle capture: does the Mini V3 darken near ψ ≈ 0?

**Diagnostic only.** This folder does not modify the frozen M1A reference
(`../manifest.json`, `../m1a-repeat-*.wav`, `../control-*.wav`), the scorer, the
patch, the engine or any tolerance. It answers the question PR #218 left open
(`../harmonic-diagnosis/README.md` §6).

## Pre-registration (committed before any capture was taken)

This section was committed on its own, before the capture or the analysis
tool had produced a single number. Its commit is the evidence of ordering.

### The question

The reference's oscillator 2 free-runs about 3.49 cents flat of the octave, so
the relative phase ψ (oscillator 2 minus twice oscillator 1, degrees of
oscillator 2) cycles every ~3.80 s. The reference was only ever observed at
ψ = 156°, −53° and 43°. In the model, odd partials h5/h7/h9/h11 swing 4.5–12.6 dB
with ψ and are darkest within about ±20° of 0°. Does the Mini V3 darken the same
way near 0°?

### Capture (Mini V3 only; the instrument of the frozen reference)

One held MIDI 36 note, 12 s gate, velocity and patch as the frozen reference,
through the existing renderer `measure_mono_m1a_reference.render(overrides, events, seconds)`.
Three conditions, two takes each:

| condition | overrides (same as the frozen controls) |
|---|---|
| `full` | none -- the complete M1A patch |
| `osc1_open` | `osc2_level=0, filter_cutoff=1, filter_contour=0` |
| `osc2_open` | `osc1_level=0, filter_cutoff=1, filter_contour=0` |

Take A starts the note at 0.1 s after instantiation. Take B starts it at 2.0 s,
roughly half a ψ cycle later, so the two takes see ψ at different times since
note-on. If ψ explains the level changes, the level-versus-ψ curves agree; if
something time-dependent does (envelope settling, a slow LFO), they do not.

### Preconditions -- each asserted at the point of use; any failure REFUSES

1. Plugin bundle version and binary SHA-256 equal the frozen manifest's identity.
2. The frozen reference phrase re-renders **bit-exactly** (all three repeats)
   in this host before any new take is trusted.
3. The existing reference-integrity qualification passes: 40 s held note,
   zero unprompted transients, and the injected-click control is detected.
4. Every take: finite, not clipped, non-silent; the MAD transient detector finds
   zero events in the held sustain, and finds the injected clicks in the same
   take (control); no run of ≥ 1 ms of exact digital zeros inside the gate; the
   isolated takes' 40 ms RMS varies < 0.5 dB across the sustain (no dropouts).
5. Parameter names and readbacks, before and after every render, as the
   renderer already enforces; the full settings dict of each condition equals
   the frozen manifest's recorded settings for that condition.
6. Octave: oscillator 1 within 5 c of 65.406 Hz; oscillator 2 within 10 c of
   twice that.
7. Sample rate 48 kHz and host block 16 samples, as the frozen manifest.
8. The isolated captures describe the full capture's oscillators: a take of
   both oscillators with the filter open equals the sum of the two isolated
   takes (residual ≤ −40 dB relative to the sum). Otherwise ψ from the isolated
   takes cannot be attributed to the full-patch take.

### Analysis (identical on both sides)

- Window: 250 ms (12 000 samples), the scorer's length and its 4-term
  Blackman-Harris coherent projection (`windowed_tone_amplitude`). Hop 50 ms.
  Windows lie wholly inside note-on + 1.0 s .. gate-off − 0.05 s.
- Level: `20·log10` of one partial's amplitude, dBFS (a full-scale sine reads
  0 dBFS). f0 per window is `refine_f0` on the full-patch window. Bin and zone
  averages are power means (mean of amplitude²) reported in dB.
- ψ per window from the isolated open-filter takes, with the #218 estimator
  (`relative_phase` logic: θ_j of oscillator 2 partial j against oscillator 1
  partial 2j, fitted to j·ψ; a window whose fit residual exceeds 2° is refused;
  more than 10 % refused windows REFUSES the take).
- Bins: 18 bins of 20° centred on 0°, ±20°, … 180°. Each needs ≥ 3 windows
  or the take is REFUSED for coverage.
- range(h) = max − min of the binned levels. dark(h) = power mean over
  |ψ| ≤ 20° minus power mean over |ψ| ≥ 60°; negative means darker near 0°.
- **Model, moving phase.** The model is rendered with the selected M1A patch
  and oscillator 2 detuned by the reference's **measured** octave offset from
  this capture (diagnostic only), held the same 12 s, with its own isolated
  open-filter renders for ψ, and analysed by the same code with the same
  windows. The model's measured drift must equal the reference's within 5 %,
  or it REFUSES. The static-phase curves of #218 are not used for the decision.

### Decision rule (h ∈ {h5, h7, h9, h11})

Evaluated in this order:

1. **Take agreement** (reference): for every h, |dark_A − dark_B| ≤ 1.0 dB and
   the largest per-bin |A − B| ≤ 1.5 dB. Otherwise **INCONCLUSIVE**.
2. **Model premise**: the moving-phase model has dark(h) ≤ −3 dB for at least
   3 of the 4. Otherwise **INCONCLUSIVE** -- the model's static darkening does
   not survive equivalent windowing, and neither branch applies.
3. **SUPPORTS-DRIVE**: for every h, the reference (takes pooled) has
   range(h) < 1.0 dB and |dark(h)| ≤ 1.0 dB. This supports investigating the
   model's filter-input level and drive; it does not uniquely prove drive.
4. **REDIRECT-TO-PHASE**: for at least 3 of the 4, the reference has
   dark_ref(h) ≤ 0.5 · dark_model(h) (at least half the model's darkening,
   same sign) and its darkest bin lies within |ψ| ≤ 40°. The model's odd
   partials are then phase-appropriate. This does **not** establish that
   permanently locked oscillators are appropriate; the next task goes to
   oscillator phase behaviour (free-running, detune).
5. Anything else is **INCONCLUSIVE**, with what would resolve it.

Mini V4 and Model D are not substitutes for the V3 reference and play no part
in this rule.
