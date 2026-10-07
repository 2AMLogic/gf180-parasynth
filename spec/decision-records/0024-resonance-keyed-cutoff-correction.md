# 0024: A resonance-keyed cutoff correction, with the two coefficient-ROM refits, in one revision

- **Status**: proposed. Validated in the fixed-point MODEL only; no RTL, no contract revision, no regenerated expectation.
- **Date**: 2026-10-07
- **Decided by**: Builder, issue #257. Awaiting review; the RTL half is unbuilt.

## Context

Issue #237 measured that the ladder's self-oscillation sings progressively flat
as resonance rises: about **105 cents of travel** across the resonance knob
(`model/ladder_headroom.py`). DR 0011's amendment showed that no single
constant can close it and deferred the fix, because a correction keyed on a
per-frame host value is a datapath change. Two build-time refits to the same
coefficient ROM (`docs/ladder-rung1-audit.md` sections 3 and 4) were deferred
with it, so that one contract revision pays for all three.

## Decision (proposed)

Three changes to the cutoff -> coefficient path, specified exactly by
`model/res_tuning.py` (the code wins where this prose is ambiguous):

1. **Tuning law.** `CUT_TRIM * fcr(f)` is replaced by a cubic in `fc = f / SR`
   refitted to our linearised loop (`ladder_headroom.refit_tuning(REFIT_CUTS, 3)`):
   `ratio = 0.99544796 fc^3 + 1.08341535 fc^2 - 0.70269763 fc + 1.02031117`.
   Selected by the pre-registered sweep `docs/sensitivity/res-cut-law-degree.json`
   (worst offset 18.8 c with the shipped law, 12.3 c with a cubic or a quartic).
2. **Coefficient entries.** The 129 Q0.16 words are refitted to minimise the
   interpolated read error against that law (`ladder_headroom.refit_rom_entries(law=...)`),
   worst read error 7.7 c. Same read, same 2064 bits. SHA-256 prefix of the
   candidate table `6438f4b3903db5e4` (shipped `48c6c974faf01776`).
3. **Correction stage (the datapath change).** A 33-entry, 16-bit unsigned
   Q1.15 table `CORR_ROM33`, entry 0 = 32768 (unity, not fitted), entries 1..32
   fitted per knot on the fit grid, values in `docs/res-tuning/validation.json`
   (maximum 34841, i.e. x1.063 at res 2). Per frame, voice context only:

   ```
   d    = clamp(k - 65536, 0, 65535)          k: the HOST k register, 17 b, Q3.14 (4*res)
   i    = d >> 11 ;  f = d & 0x7FF            5-bit index, 11-bit fraction
   c    = rom[i] + (((rom[i+1] - rom[i]) * f) >>> 11)     signed difference, floor shift
   cut' = clamp((cut * c + 16384) >> 15, 30, 21600)       15 b x 16 b = 31 b product, ROUNDED
   g    = g_from_cut(cut') ;  kc = kc_from_cut(cut') ;  k_eff = (k * kc) >> 15   (unchanged)
   ```

   **Sequencing and why it is not circular.** The table is keyed on the host
   `k` register, which nothing downstream modifies. Both ROM reads then see the
   same `cut'`, so DR 0006's compensation is re-derived unchanged in form,
   `k_eff = res * k_onset(cut')` evaluated with `g(cut')`, and `res = 1` stays
   the onset at every cutoff (asserted at 10 untouched cutoffs). Keying it on
   `k_eff` instead is a committed control (`keyed-on-k-eff`), and it is worse:
   23.0 c of travel against 5.5 c. For `k <= 65536` the stage is the identity.

   **Proposed RTL shape** (`rtl-sketch/voice_dp.v`), five new states between
   `S_CUTM` and `S_ROM0` using the existing shared multiplier (`ma`/`mb` ->
   `mr`, one-cycle latency as in `S_ROM2`/`S_ROM3`): `S_CR0` latch `rom[i]`;
   `S_CR1` latch `rom[i+1]`; `S_CR2` multiply difference by `f`; `S_CR3`
   multiply `cut` by `c`; `S_CR4` round, clamp, write `cut`. 528 ROM bits. The
   frame's cycle budget, area and timing are **not measured** -- no synthesis
   was run.

4. **Drum context.** The drum ladder (`S_DC0`..`S_DC5`, `k_eff2`) shares the g
   and k ROMs, so changes 1 and 2 reach it (the g table moves by up to 438 LSB,
   0.81 %); change 3 does not (the drum path is not given the stage). The drum
   filter's tuning change is unmeasured and its expectations must be
   regenerated.
5. **Contract.** The next free revision at implementation time (16 against
   `origin/main` `0d4f437`; open PR #377 also proposes a revision). `G_ROM128`
   and `K_ROM32` change and `CORR_ROM33` is new. Revision-3 untuned hashes are
   untouched: `make_g_rom(tune=False)` is not modified.

## Evidence (model, fixed-point free ring)

Grids, targets and margins were committed in `docs/res-tuning/plan.json` before
any candidate was fitted. Fit, selection and validation grids are disjoint.
Validation on the untouched grid (10 cutoffs 30 Hz..16 kHz x 6 resonances
1.03..1.99, `docs/res-tuning/validation.txt`, every point published):

| | baseline | candidate | gate |
|---|---:|---:|---|
| mean-offset travel | 106.1 c | **5.5 c** | T1 <= 25 c: PASS |
| worst per-cutoff travel | 119.3 c | 41.7 c (30 Hz) | T2 <= 0.5 x baseline: PASS |
| worst \|offset\| | 126.4 c | 40.9 c | N1 <= baseline + 5: PASS |
| mean \|offset\| | 66.1 c | 4.1 c | N2 <= baseline: PASS |
| onset (0.97 decays, 1.03 sustains) | 10/10 | 10/10 | N3: PASS |

**Limits, measured.** At 30 Hz the corrected cutoff is integer Hz, so the
correction is quantised to about 57 c per Hz (30 Hz, res 1.15: +40.9 c). A
fractional `cut'` carried into the interpolation, or applying the factor to `g`
rather than to the cutoff, would remove that. Both are unmeasured and would need
a fresh untouched grid. 12 kHz and 16 kHz turned out to be SR/4 and SR/3, and
the candidate's ring locks to exactly those frequencies (verified by period),
which flatters it there. Excluding both cutoffs, travel is still 105.4 -> 5.7 c.

**Controls** (`docs/res-tuning/controls.json`, properties x controls). The
acceptance targets catch 2 of 6: `disabled` and `reversed`. `keyed-on-k-eff`,
`index-off-by-one`, `floor-not-round` and `k-rom-not-rederived` are BLIND to
all five targets, because those targets are set relative to a 105-cent
baseline. The candidate lock in `model/test_res_tuning.py` catches all six.

**Wrong-then-right: 3.** One estimator fixture was wrong (a two-tone signal
that is not ambiguous). One pre-registered sweep prediction failed and is
recorded as a limit in its sensitivity record. One confound (the SR/n lock) was
found after the validation run.

## Alternatives considered

- **Widened g/k ROM address including a k index.** 129 x 33 words is about 68 kbit against 528 bit. Rejected.
- **A 9-entry table.** 3.9 c of travel on the select grid against 1.3 c for 33 entries, at 144 bits. The pre-registered rule picked 33. The difference is near the estimator granularity the rule was derived from. Choosing 9 is a reasonable RTL-review call, provided the reason is stated.
- **Correction applied to `g`.** Avoids the integer-Hz limit. Not measured.

## Consequences (owed, none done here)

RTL in `voice_dp.v` with start-red and the six controls as `INJECT_BUG_*`
defines. The contract revision and `spec/reference/tables/*.hex` regenerated
via `gen_tables.py`. Every bit-exact expectation regenerated (`verify_voice.py`,
`verify_synth_top.py`, `verify_ladder.py`, the drum benches) and the affected
`.wav`s regenerated with provenance, keeping historical ones. Then the same
improvement measured through the SPI link and decoded I2S. This record makes
no claim about any of those. It also does not establish Moog fidelity, because
no external reference was used for the target.
