# #369 step 11 (#411): Hh1's own gain, restored to its circuit value — REFUSED

**The question** (issue #411): *does restoring Hh1 at the level the circuit gives — and nothing
else — bring the mid band's independent content to the 808's +4.4 dB, and does it move the
50–300 ms residual step 5 could not explain?*

**The answer: no, on the metric the issue names, and it costs the strike-window guard.** Revision
4 changes exactly one register — Hh1's own amp, from revision 3's derived 0.693828 to `AMP_MAX`
(0.99998, the circuit's stated unity gain, `HH1_PASS_DB = 0.0`) — and the render moves
`tools/cymbal_mid.py`'s qualified over-skirt residual the **wrong direction** (+0.04 dB → **−0.55
dB**, away from the 808's +4.41 dB), while the frozen `thirds()` strike-window guard, which
candidate 3 uniquely held at zero violations, now has one 1/3 octave outside ±6 dB. Both are
measured with the unmodified instruments the issue requires (`tools/cymbal_mid.py`,
`tools/cymbal_bands.thirds`); neither instrument was changed. Preservation holds: all 15 non-CY
sounds stay bit-identical (`preservation` below, all `True`).

Instrumented at `2dd8f8d` (clean tree; every number below is reproducible from that commit —
`docs/scorecard/cymbal-369/candidate4/candidate4.json` records `"sources_dirty": false`).

## 1. The prediction and the change

`model/cymbal_candidate.py`'s REVISION 5 docstring (committed before this render) states the
change and its predicted consequence: Hh1's amp rises by `AMP_MAX / 0.693828 = 1.4413` = **+3.17
dB**, a flat linear multiplier on Hh1's entire post-filter output, so (a) the mid band's
over-skirt residual "should move toward the 808's +4.41 dB by close to +3.17 dB, and possibly by
more", and (b) revision 3's strike-window thirds, already within 0.5 dB of the 6 dB bound, are
"very likely" to trade back some of that margin. The docstring states explicitly that both
consequences "have to be confirmed by the render, not assumed" — this is that confirmation, and
(a) does not hold.

## 2. What the render measured

**Mid band, `tools/cymbal_mid.py --renders`, unchanged instrument** (`M`, 891–1782 Hz):

| | over the band-pass-only skirt prediction (EDT window) | M energy share (dB re total, 1 s) | M EDT10 (qualified) |
|---|---:|---:|---:|
| 808 CY5025 | +4.41 dB | −26.77 | 632 ms |
| shipped kit (Hh1 omitted) | +10.52 dB | −22.83 | 528 ms |
| candidate 3 (Hh1 restored, shipped-kit-matched amp) | +0.04 dB | −35.26 | 451 ms |
| **candidate 4 (Hh1 restored, circuit unity amp)** | **−0.55 dB** | −32.84 | 477 ms |

Candidate 4's absolute mid-band content did rise, as the flat-gain arithmetic predicts (energy
share −35.26 → −32.84 dB, **+2.42 dB**, a bit short of the predicted +3.17 dB) and its EDT10
moved a little further toward the 808's (451 → 477 ms, against the 808's 632 ms — still well
short, same conclusion as step 6). But the metric acceptance item 1 actually names — the
over-skirt *residual*, M measured against a leak prediction built from **this render's own L
band** — did not follow: it went from +0.04 to **−0.55 dB**, 0.59 dB further from the 808, not
3+ dB closer.

**Why a flat amp on Hh1 cannot move that specific ratio much.** `over_leak_edt_bp_only_db`
compares M's measured energy in a render against a leak prediction proportional to **that same
render's L-band energy** (`tools/cymbal_mid.leak_dominance_db`). Hh1's `amp` register is a scalar
applied to Hh1's *entire* filtered output, uniformly at every frequency, *after* its IIR
filtering (`ModalFxHP3.step`: `mix += (y * amp) >> 16`) — so if M and L both derive (almost)
entirely from that same post-Hh1 signal, scaling it by a constant cannot change the M/L ratio:
both scale together. The metric is close to insensitive to Hh1's own amp alone, by its own
construction, independent of whether the amp goes up or down. `tools/probes/
cymbal_candidate4_gain_invariance.py` rules out the other candidate explanation — clipping — by
direct measurement (§4): the small movement that *is* observed (−0.59 dB, not exactly 0) is
consistent with L's own energy having a small, roughly constant contribution from the (unchanged)
decay/short bands' skirts, not with any nonlinearity.

**Strike window, `tools/cymbal_bands.thirds()`, unchanged instrument, 0–50 ms:**

| | worst 1/3-octave vs 808 | count outside ±6 dB |
|---|---:|---:|
| candidate 3 | 5.70 dB (16 kHz) | **0** — the property no earlier revision has had |
| candidate 4 | **8.32 dB (2.5 kHz)** | **1** |

The 2.5 kHz third — directly at Hh1's own corner, where the raised amp has the most leverage —
crosses the bound. This is the regression the docstring's own second prediction anticipated, and
it is why candidate 4 is not shippable even though the metric below improves.

**Tail windows, same instrument, 50–300 ms and 300–1000 ms** (worst deviation and count of thirds
outside ±6 dB, both vs. 808):

| window | candidate 3 worst | candidate 3 count >6 dB | candidate 4 worst | candidate 4 count >6 dB |
|---|---:|---:|---:|---:|
| 50–300 ms | −11.59 dB (1.26 kHz) | 7 of 14 | −8.97 dB (1.26 kHz) | 4 of 14 |
| 300–1000 ms | −10.48 dB (1.26 kHz) | 6 of 14 | −9.37 dB (1.26 kHz) | 5 of 14 |

On this fixed-time-window instrument, the tail genuinely improves: fewer thirds exceed the bound
and the worst case shrinks by 2–3 dB in both windows, roughly tracking the measured +2.42 dB
absolute gain on the mid band. **This is the part of the render that behaves as revision 5's
docstring predicted**; the over-skirt metric above is the part that does not, and that
distinction — one instrument improving while the metric the issue is framed around does not — is
the reportable finding, not a contradiction to paper over.

**Preservation** (`preservation`, `candidate4.json`): all 15 non-CY sounds (BD, SD, LT, LC, MT,
MC, HT, HC, RS, CL, CP, MA, CB, OH, CH) render bit-identically to the shipped kit — unaffected, as
expected, since the change touches only Hh1's own amp register.

## 3. Verdict against #411's acceptance criteria

1. **One circuit-derived change, stated before the render.** Done: Hh1's amp → `AMP_MAX`, the
   table's own `HH1_PASS_DB = 0.0`. **Does not achieve the intended effect** — see §2's
   explanation of why a flat gain on Hh1 alone is close to powerless against this specific ratio
   metric.
2. **Measured with the unchanged instruments.** Done — `tools/cymbal_mid.py` and
   `tools/cymbal_bands.thirds()` are untouched (`tools/test_cymbal_mid.py`,
   `tools/test_cymbal_bands.py`: 45/45 pass, unmodified, §5).
3. **Both directions reported.** Done, and unfavorable: the strike-window guard regresses (0 → 1
   thirds outside ±6 dB) even though the tail improves. Per the issue's own text, "losing that
   guard is a regression even if the tail improves."
4. **Preservation.** Holds — 15/15 non-CY sounds bit-identical.
5. **No promotion without the tail.** Moot for shipping purposes: candidate 4 is not proposed for
   promotion. It stays a diagnostic ablation (`--variant candidate4`), exactly like `notilt` and
   `balance` before it; the shipped default (`--variant full`) is unchanged and still candidate 3.

**Net: REFUSED.** Restoring Hh1's own gain to its stated circuit value, alone, does not satisfy
acceptance item 1 (the qualified over-skirt metric moves 0.59 dB further from the 808, not
3+ dB closer) and trades away acceptance item 3's strike-window guard. It is not promoted. The
mid-band level defect this chain is chasing needs either a *frequency-shaped* correction (Hh1's
own gain cannot move an M/L ratio built from Hh1's own output) or a change to the level rule that
sets the OTHER bands too — #414 independently measured the same shipped-kit anchoring rule
against the circuit for all three bands and found comparable residuals there (+9.9 dB decay-band,
+38.2 dB short-band, at each band's own level-setting 1/3 octave), which is outside this issue's
scope (the inter-band balance is explicitly excluded, both by #411 and by #396's REFUSED
finding).

## 4. Ruling out clipping (`tools/probes/cymbal_candidate4_gain_invariance.py`)

Before accepting the gain-invariance explanation in §2, the shared-accumulator saturation
`model/modal_fixed.sat` performs once per sample across every active mode
(`model/cymbal_candidate.py`'s `ModalFxHP3.step`) was checked directly, at both revision 3's and
revision 5's calibrated amp, for the same full CY render:

| variant | Hh1 amp | `sat()` calls | clipped | peak output (full scale) | headroom |
|---|---:|---:|---:|---:|---:|
| full (candidate 3) | 0.693828 | 25,152,000 | **0** | 0.2863 | 10.86 dB |
| candidate4 | 0.999985 | 25,152,000 | **0** | 0.2858 | 10.88 dB |

Zero saturation events in either render, and the peak output sample is within noise of the same
value in both (≈11 dB of headroom under the 16-bit rail in both cases) — clipping is not the
cause of the counter-intuitive over-skirt movement. The standing explanation is §2's: the metric
is close to gain-invariant with respect to a flat change to Hh1's own amp, by construction.

## 5. Instrument integrity

`tools/test_cymbal_mid.py` (36 tests) and `tools/test_cymbal_bands.py` (8 tests) — the two
instruments this step is required to use unchanged — pass at 45/45, and neither file is touched
by this branch's diff (`git diff --stat` against `main` touches only `model/cymbal_candidate.py`,
`tools/cymbal_candidate_eval.py`, `tools/probes/cymbal_candidate4_gain_invariance.py`, and this
`docs/scorecard/` directory).

## Files

- `candidate4.json` — the full `cymbal_candidate_eval.py --variant candidate4` record (levels,
  preservation, bands, thirds, mid), commit `2dd8f8d`, clean tree.
- `gain-invariance-probe.json` — the clipping-elimination probe's record, same commit.
