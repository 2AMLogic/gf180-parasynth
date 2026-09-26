# 0011: Huovilainen's `fcr` tuning polynomial in the cutoff ROM, and one constant scale

- **Status**: proposed
- **Date**: 2026-09-18
- **Decided by**: voice agent, from the reference comparison in PR #42 (`docs/discrimination.md` §8.4) and the free-ring measurements re-run here (`model/test_moog_acceptance.py`, the DR 0011 tests)

## Context

Contract 11.5 and open item 17.12 recorded a known defect: the frequency the
filter self-oscillates at is **not** the cutoff the host commanded, and the
error is not a constant — it ran from −3.0 % at 100 Hz to **+6.85 % at
10 kHz**. A constant offset is one scale factor and a player never hears it;
a *drift* means the cutoff knob does not mean the same thing across its range,
which is exactly what makes a filter feel wrong under a hand.

DR 0001 implements Huovilainen, DAFx-04. The paper's §5 also publishes a
**tuning polynomial** that corrects the one-pole cascade's corner for the four
poles and the half-sample feedback delay. **We implemented the structure and
left the tuning out.** Surge XT's `LP Vintage Ladder` Type 2 implements the
same paper and applies it, and its measured drift over six octaves is 0.62
percentage points against our 7.92 (`docs/discrimination.md` §8.4).

## The polynomial, and a constant that was wrong on the way here

With `fc` the cutoff normalised to the **base** rate (not the oversampled one
— this is how both Surge and Csound's original evaluate it):

```
fcr = 1.8730·fc³ + 0.4955·fc² − 0.6490·fc + 0.9988
g   = 1 − exp(−2π · f · CUT_TRIM · fcr(f) / f_os)
```

**The quadratic term is 0.4955.** `docs/discrimination.md` §8.3 and
`model/reference_rigs.py` both print **0.4995**. The source they cite —
`sst-filters/include/sst/filters/VintageLadders.h`, namespace `Huov`, which
attributes the implementation to Victor Lazzarini for Csound 5 — spells the
constant `m04955`. The two differ by **0.03 percentage points** on the
measurement below, so no measurement in this repository would ever have caught
it; it is caught by reading the source that was cited. Both numbers are
recorded here so that the discrepancy is a fact and not a silent correction.

## The constant scale

The polynomial removes the *drift* and leaves a roughly frequency-independent
residual of −2.5 to −3.9 %. `CUT_TRIM = 1.030` removes that. It is a fitted
constant, not a published one, and **its value depends on the operating point**:
chosen here at `res = 1.05` on the free-ring probe, because that is the
operating point at which "the cutoff" has an unambiguous physical meaning (the
frequency the loop actually sings at). At maximum resonance the residual is
different, so this number is a decision and not a measurement.

## Measured, `res = 1.05`, free ring after a 5 ms kick, dominant frequency

| commanded cutoff | rev 8 | rev 9 |
|---|---|---|
| 30 Hz | −2.08 % | **+0.22 %** |
| 100 Hz | −2.99 % | **−0.72 %** |
| 400 Hz | −2.10 % | **+0.15 %** |
| 1.6 kHz | −0.28 % | **+0.43 %** |
| 3 kHz | +1.55 % | **+0.43 %** |
| 6.4 kHz | +5.05 % | **−0.08 %** |
| 10 kHz | **+6.85 %** | **−0.90 %** |
| 16 kHz | +4.80 % | −1.33 % |
| 21.6 kHz (the clamp) | −1.73 % | −1.23 % |

**Worst error 30 Hz .. 10 kHz: 6.85 % → 0.90 % (115 cents → 15 cents). Spread
over that range: 9.84 → 1.33 percentage points.**

## The decision

Bake `CUT_TRIM · fcr(f)` into `make_g_rom` at build time. **One multiply per
ROM entry, at ROM-build time, and no datapath change at all**: the ROM keeps
its 129 × Q0.16 shape, `g_from_cut` keeps its read, `voice_dp.v` is untouched.

The resonance-compensation ROM of DR 0006 is *derived* from the cutoff ROM
(`make_k_rom` → `k_onset` → `g_from_cut`), so it moves with it and DR 0006's
property — `res = 1` is the onset at every cutoff — is preserved by
construction rather than refitted.

This is the first time a pinned table in this contract has moved for a
structural reason rather than a fit to a recording, so `G_ROM128` and
`K_ROM32` both change hash and the contract goes to **revision 9**.
`spec/reference/test_tables.py` keeps the revision-3 hashes and asserts that
`make_g_rom(tune=False)` still reproduces them exactly — so the whole
difference between the two pins is this polynomial and this constant, and
nothing else crept in with them.

## What was NOT taken

Huovilainen also publishes `acr = −3.9364·fc² + 1.8409·fc + 0.9968` for the
resonance, and Surge applies it as `4·res·acr`. **We do not**, because DR 0006
already compensates resonance with a ROM derived from *this* filter's own
linearised loop — measured, not published — and it holds the onset to 0.39 %
where `acr` is a curve fit to a different implementation. Applying both would
compensate twice.

## Consequences

- Every bit-exact expectation for the voice moves: `verify_voice.py`,
  `verify_synth_top.py` and every rendered `.wav` are regenerated.
- The drum filter (`voice_dp.v`'s second ladder context) reads the same two
  ROMs, so it is retuned by the same amount. That is a consistent improvement
  and not a separate decision.
- Huovilainen's own caveat stands and is why 17.12 does not close entirely: a
  *tuning* table is two-dimensional (the zero-resonance corner and the
  self-oscillation frequency cannot both be exact with one table). This record
  chooses the self-oscillation frequency at `res = 1.05` as the thing that is
  right. The measured −3 dB corner moves with it.

## Two metrics, because one of them is blind

The figure that motivated this change — "7.92 percentage points of drift over
six octaves against Surge Type 2's 0.62" — measures **non-uniformity**: how
much the corners differ *from each other*. That is the right measure for
contract 17.12, and it is **blind to absolute error by construction**. An
injected uniform 30 % skew of the coefficient lookup moves it by a quarter of a
percentage point, because a skew that displaces every corner equally cannot
change how much they differ.

So this record reports both, and the acceptance suite locks both:

| | rev 8 | rev 9 | rev 9 + injected uniform 30 % skew |
|---|---|---|---|
| **absolute**, self-oscillation f/cutoff, worst over 200 Hz .. 10 kHz | 7.2 % | **0.94 %** | — |
| **absolute**, −3 dB corner / commanded at 800 Hz (res 0.1, drive 0.3) | 0.7024 | **0.7145** | 0.9285 |
| **drift**, spread of that ratio over 200 Hz .. 10 kHz | 4.03 pp | **3.16 pp** | 4.28 pp |

Both absolute metrics improve and the drift improves; nothing here is a case of
quoting whichever moved. The last column is
`test_control_a_uniform_cutoff_skew_is_absolute_error_a_drift_metric_cannot_see`,
which injects the defect the estimators exist to catch and requires the
absolute property to go red while the drift property stays green — the
estimator was tested by breaking it, not by trusting it.

**And the same blindness, at a second operating point, found later.** The
injected skew above tests the drift metric at `res = 1.05` — the resonance
`CUT_TRIM` was fitted at. Swept across the resonance knob
(`model/ladder_headroom.py`, issue #46's rung-1 audit), the mean offset walks
from **+0.07 % at res 1.05 to −5.32 % at res 2.00** while the drift stays at
1.0–1.3 pp throughout. So the residual this record removes with one constant is
resonance-dependent, which the "What was NOT taken" section above anticipates in
principle ("its value depends on the operating point") without measuring: it is
**105 cents of travel**, and it is the largest remaining rung-1 error by an order
of magnitude. Not a defect in this record — it is the consequence this record
says it is accepting — but it is now a number rather than a caveat.
`docs/ladder-rung1-audit.md` §1 has the table.

The −3 dB corner is not 1.0 in either revision and was never going to be: a
four-pole cascade's corner is structurally below its per-pole corner. What that
row locks is that the corner is a *fixed fraction* of the commanded cutoff, and
which fraction. `docs/discrimination.md` §8 locks its own corner-ratio number
(0.7860) under different probe conditions — a different resonance, drive and
level — so the two are not comparable and neither is a restatement of the other.
