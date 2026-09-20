# 2x oscillator RTL integration plan

The Python reference and the standalone decimator are now checked in. The
remaining integration must preserve the existing voice contract:

1. Keep the phase increment in base-rate units and derive two internal phase
   points per output frame (`phase` and `phase + inc/2`).
2. Run the existing PolyBLEP window for each internal point. The reciprocal is
   shared because both points have the same increment.
3. Present both Q1.15 oscillator words to `decimate_2x` in consecutive cycles.
4. Wait for `out_valid`, then use the decimated word in `S_MIX` and advance the
   base phase exactly once.

The decimator costs 31 taps in the reference implementation. Its symmetric
coefficients reduce this to 16 products; the current generic RTL block keeps
all taps explicit so its impulse response is easy to audit. Before connecting
it to the voice, synthesize both forms and measure the 256-cycle frame budget.

The integration is not accepted until a clean RTL run is bit-exact against
`model.oversampled_osc.render_saw`, and the negative control bypasses the
decimator with a clearly worse independent inharmonic measurement.
