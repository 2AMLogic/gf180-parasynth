# Clap L2 in production: model, RTL, I²S (plan084)

This folder is the evidence for the one production-clap change: the confirmed
L2 final strike (`docs/scorecard/clap-d12a/README.md` §10), implemented in
`model/drums_fx.py` and the drum RTL as **contract revision 13**. Every heavy
run was on the build box, and every verdict below comes from a checked exit
status recorded by `tools/run_all.py`.

## Three statements, kept separate (plan084 §5)

| # | statement | status |
|---|---|---|
| 1 | L2 improves the qualified clap energy/decay measurements | yes, from #261: DEV 8/8, fresh 8/8. Official D12A rescored below |
| 2 | the production model and RTL reproduce that behaviour | **yes**, bit for bit, model → RTL → I²S (below) |
| 3 | an FPGA image plays it | **no claim**. The RTL changed, so a new image must be built and qualified. Vivado was not started. The published baseline (#255) is untouched and stays independent |

## The contract (spec/NUMERIC-CONTRACT.md 15.1, 15.3, 15.8, Appendix G)

**New register.** `ENV_FRATE[e]` at `0x43 + 4e` (16 bits) takes the stride's
spare slot, so no address moved. **FRATE = 0 is revision 10 exactly**, and 0 is
the reset value.

With `FRATE ≠ 0` and `last = bursts·period`:

- the fire captures `fcap` (the accent-scaled fire level);
- the re-strike at `t = last` restores `strike = level = fcap`;
- decay after `last` uses FRATE;
- a choke clears `fcap`, so there is no ghost final strike.

**When writes take effect.**

- PEAK and ACCENT are read only when the envelope fires.
- RATE and FRATE apply from the frame they are written.
- A retrigger recaptures `fcap`: the new hit owns its state.
- MA's preset writes `FRATE = 0`.

**RTL cost.** No new multiply. The final strike is a register copy and the final
decay only swaps `mul_b`. The drum section's slack is unchanged (below).

**Kit CP.** 4 strikes at period 511, early strikes τ 4 ms at 13/16, the final
strike at 1.00× the fire level with τ 20 ms, and a tail of τ 80 ms. These are
the frozen L2 settings.

## Evidence

| check | where | result |
|---|---|---|
| production CP == frozen L2 experiment, 18 renders (accents 0.5/1/2, all DEV and FRESH offsets): float64, PCM and burst-envelope trace | `goldens-pre.json` vs `goldens-post.json`, `tools/test_clap_l2_identity.py` | identical |
| every other sound, **including MA**, bit-identical: output, both buses, every envelope trace, at accents 1 and 2 | same | identical (30 renders) |
| CP's other 16 envelopes untouched; only envelopes 8 (burst) and 9 (tail) change | same | yes |
| control: the pre-change CP ≠ L2 | same | caught |
| event-level model tests: schedule and levels, final decay at FRATE, retrigger at final −1 / 0 / +1, choke (no ghost), mid-note PEAK/ACCENT (no effect), mid-note FRATE (next frame), FRATE = 0 is revision 10; CP→MA and MA→CP while sounding; RESET mid-note | `model/test_clap_final_strike.py` | 21 pass |
| model mutants caught for their own reason: weak final strike, short final decay, stale capture on retrigger, shifted/omitted final strike, **MA preset that does not clear FRATE** | same | all caught |
| drum RTL bit-exact vs model, full stimulus incl. new §4b (retrigger around the boundary at 3 accents, mid-note PEAK/ACCENT/RATE/FRATE, choke before the final strike, CP→MA→CP while sounding) and FRATE extremes | `verify_drums.py` (run 2) | PASS |
| RTL controls `DRUM_FINAL_WEAK`, `_FINAL_SHORT`, `_FINAL_SHIFT`, `_FCAP_STALE`, each exit 1 | `rtl-sketch/test_rtl.py -k drum` (run 1, 17 pass) | caught |
| **production path**: the clap phrase through the SPI pins with the voice held, every decoded I²S period vs `synth_top_model`. The phrase: 9 CP fires with re-strikes at final −2 / +2 / +12 frames, 6 completed final strikes, CP→MA→CP while sounding, drum RESET mid-train, every stop at accent 2 | `clap-phrase.json`, run 4 | **30 201 / 30 201 periods identical** (wire sha = model sha `a1400eb2…`); frame budget met |
| the same phrase with `DRUM_FINAL_WEAK`, `_FCAP_STALE`, `_FINAL_SHORT`, `_FINAL_SHIFT` | runs 2 and 4 | all caught at the pins |
| full-chip regression (`verify_synth_top`, the standing stimulus) | run 1 | PASS |
| `verify_ctl` (register-map corners, now including FRATE at `0x43` and `0x87`) | run 1 | PASS |
| frame deadlines, #248's verifier with evidence-completeness checks (analysis v2), `stress-saw` and `arty-uart` | `deadline-*.json` vs `deadline-*-pre.json` (pre = `a438881`, same verifier) | PASS; worst sample slack 13, busy slack 15, **drum slack 130, all identical before and after** |
| acceptance suite, drums model, synth_top model, identity, unqualified-metric tests | run 3 | 180 pass |
| `make reference-integration` | run 3 | 147 pass |
| `tools/scorecard.py --check` | `scorecard-check.log` | exit 0 |

## Headroom: output rail vs internal envelope saturation (reported separately)

The combined fixture is the clap phrase at the intended gains (DVOL = BVOL =
0.45), with the mono voice held, ending in every stop at accent 2.0 in one frame.

- **Output rail.** L2 produces 327 samples at the rail. The same fixture with
  the revision-10 clap produces 324.
  - All of them in both cases fall within 50 ms of the all-stops-at-accent-2
    hit.
  - Nowhere else in the phrase does either version reach the rail. That covers
    CP alone at accents 0.5, 1 and 2, repeated CP, CP/MA switching, and the
    RESET.
  - So **L2 adds 3 output-rail samples**, only in the everything-at-accent-2
    case, which already clipped.
- **Internal envelope saturation.** Five strikes reached the 24-bit envelope
  rail:
  - CP's burst envelope on its own accent-2.0 hit (frame 3415);
  - envelopes 6, 8, 10 and 11 in the all-stops accent-2 frame.
  - This is `usat24` at fire. It is not an output clip, and it is why L2 and L3
    coincided at accent 2 in #261.
- **Correction to #253/#261's wording.** "Zero rail samples" there meant solo
  output only. The accent-compression attribution to "a small tanh effect" was
  not shown causally. The envelope saturation above is what was measured.

### Disposition (plan087 B1): the nominal demo mix is qualified, no gain change

The stress result above stays as it is: **327 output-rail samples with L2
against 324 pre-L2**, all within 50 ms of the all-stops-at-accent-2 hit. It is
an extreme case and is not claimed to be clean.

The mix a player hears was measured separately with
`tools/headroom_demo_mix.py`, which reads the master sum **before** the final
clamp (`synth_top_model` step 6) rather than counting samples that equal the
rail. Integer model, Arty configuration (OSC2X=1, FILTER2X=1, PULSE2X=0), the
`fpga/fixtures.py` fixtures at their own gains (DVOL = BVOL = 0.45, the
reference kit, the fixture's accents; each fires CP twice). Build box, commit
`3886191`, `tools/run_all.py` 4/4 exit 0; records in `headroom/`.

| fixture | clap | clamp events | rail samples | peak before clamp | headroom | envelope-rail fires (internal) |
|---|---|---|---|---|---|---|
| `demo` | L2 | **0** | 0 | 26,072 | **+1.99 dB** | 16 |
| `demo` | pre-L2 | 0 | 0 | 26,159 | +1.96 dB | 16 |
| `bar808-full` | L2 | **0** | 0 | 19,971 | **+4.30 dB** | 16 |
| `bar808-full` | pre-L2 | 0 | 0 | 19,971 | +4.30 dB | 16 |
| control: `demo`, every accent 2.0, DVOL/BVOL full | L2 | 30,595 | 30,601 | 147,419 | −13.06 dB | 22 |
| control: `bar808-full`, same | L2 | 27,132 | 27,135 | 143,326 | −12.82 dB | 22 |

- **No gain change.** Neither nominal mix reaches the final clamp with either
  clap. L2 does not reduce the demo's headroom (+1.99 dB vs +1.96 dB), so the
  gains stay where they are and there is nothing to bind.
- **The controls make the zero meaningful.** On the same material, the
  hot variant clips before the clamp on about 30,000 frames. The tool refuses
  (exit 2) if the control does not clip, if the fixture never fires the clap,
  if the output is not music, or if the L2 and pre-L2 renders are identical.
- **Internal saturation is separate, and it happens in nominal play too.**
  Sixteen strikes in each nominal fixture reach the 24-bit envelope rail, the
  same number with either clap. That is `usat24` at fire on accented hits. It
  is not an output clip, and L2 does not change it.
- **Scope.** Model only. No RTL run of these fixtures and no image. The
  digital-playback pilot's UART evidence for `demo` belongs to R1 (B2), not to
  this PR.

## Official D12A, rescored through the normal runner

`tools/run_case.py D12A` at `d42ac14`: **no verdict**.

| property | ours | reference | error | tolerance | status |
|---|---|---|---|---|---|
| burst/tail ratio | −2.14 dB | −2.97 dB | +0.84 dB | 3 dB | valid, **within tolerance** (was +14.26) |
| decay T20 | 111.8 ms | 199.0 ms | −87.2 ms | 99.5 ms | valid, **within tolerance** (was −97.9) |
| Burst timing | 47.0 ms (unqualified reading) | 35.8 ms | — | — | **UNQUALIFIED**: refused, reading kept as `unqualified_*` |

- **No whole-case pass is claimed.** It is not a pass because the timing
  estimator is unqualified for this domain (`clap-d12a/burst-timing-qual.json`).
  The property was **not dropped**. Plan084 §5 allows a measured, better clap to
  ship with that limitation documented, and does not allow claiming complete
  acoustic equivalence.
- **History is preserved.** The previous "fail, worst 4.75" record is carried in
  `rubric_history` as a measurement-version change, and noted as also predating
  the sound change.
- **Board effect.** D12A moves from a valid fail to a no-verdict, so the board
  now shows 19 valid cases instead of 20. That is the honest consequence of
  refusing an unqualified property.
- **Ensembles.** E1A, E1B and E2A were rescored because they render the kit.
  Every value is unchanged; only their provenance is refreshed.

## Not done here, deliberately

- The FPGA image rebuild and its timing/publication checks, and a versioned
  release. The RTL changed, so a clap-L2 image needs its own build. The #255
  baseline release manifest was not touched.
- Any acoustic-envelope replacement for Burst timing (plan084: a later, bounded
  rubric change).
- A physical recording.

## Integration for review (plan087 B1)

CI failed on four jobs. All of them were stale inputs, and each guard was kept:

1. **Contract revision (python, acceptance).** L2 was written as "contract
   revision 11", but revision 11 (the tom rebalance) and revision 12 (drift)
   already existed. The kit changed without the pins changing, so
   `spec/reference/test_tables.py` failed 5 tests. The change is renumbered as
   **revision 13**, and KIT808 is re-pinned `a43fe2a7…` → `321a9354…` (147 →
   148 writes). A new test rebuilds revision 11's image from the live one by
   undoing exactly the three clap writes, and requires revision 11's hash.
2. **Arty binding (spi-i2s, m5a-fast).** The live-tree UART proof still named
   `drift-clean`. A new `fpga/reports/arty/l2-clean` run is bound instead. R0
   stays pinned to `uart-clean`, now by an explicit test.

## Wrong-then-right, this rung (5)

1. The first box batch launched on the **previous** commit: the bundle fetch
   refused to update the checked-out branch, and the job ran anyway. It was
   killed before any result was used. Every later batch refuses unless HEAD
   equals the commit I pushed and the tree is clean.
2. `--clap-phrase` crashed on a coverage print written for the standing
   stimulus, which surfaced as a FAIL rather than a verdict. The print is fixed.
3. `verify_drums.py` returned NO-VERDICT in batch 1 because two iverilog
   compiles collided in the shared `build/` directory. It passed in batch 2 with
   its own output directory.
4. Two acceptance tests encoded SN-inferred claims that the recording
   contradicts: "no burst after 30 ms" and "tail τ 47 ms". They were revised to
   the hardware-measured basis, with the source history kept in the docstrings.
   The maracas test's burst window was then first set without its 10 ms
   pre-roll, and failed.
5. The first rescore dropped D12A's history. A newly-UNQUALIFIED property makes
   the record a no-verdict, which has no measurement policy, so
   `carry_rubric_history` could not see the change. That case is now handled,
   and the rescore was repeated from the committed record.
