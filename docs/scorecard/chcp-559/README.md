# #559 CH and CP: DEV/CONFIRM record (Part of #282)

Ratios are to the #379 WEAK bar (1.0 = at the bar; lower is better). No calibrated
between-recording bar exists (gate-379 README section 9), so nothing here is a gate pass.

## CH (flatness)
- Shipped: flatness 8.87 (DEV median), 8.07 (CONFIRM conditions).
- Selected on DEV (`ch-sweep-dev.*`): CH high-pass Q 2.5 -> 0.5 (DEV 8.87 -> 5.21).
- CONFIRM on conditions not used to select it (`ch-confirm-hpq05.*`): 8.07 -> 4.75, median over
  8 conditions, better on 6/6 accent-1 offsets, no regressions. PROVISIONAL until the provenance
  below is repaired. One per-condition disclosure: attack 1.01 -> 1.44 at [13651, 1.0].
- Stage 2 (adding hat band-pass Q 6 -> 3, `ch-confirm-hpq05-bpq3.*`): flatness 2.43 but
  attack regresses 0.92 -> 1.72 (and CY worsens in the sweep): NOT CONFIRMED, not in candidate.
- Still failing: flatness 4.75 is far above the bar. The Qs are [inferred] reference readings.
- Holdout width: the CONFIRM conditions are 6 strikes of ONE recording (ch8/CH.WAV). Two of the
  eight are degenerate: `[8356, 2.0]` equals `[8356, 1.0]` to 4 decimals and `[4355, 0.5]` differs
  from `[4355, 1.0]` only in the 3rd-4th decimal (the gate features are level-invariant), so the
  8-condition median double-counts two strikes and accent is effectively untested.
- PROVENANCE NOT CLEAN, clean rerun required. All four committed sweep/confirm JSONs have
  `sources_dirty: true`. `ch-confirm-hpq05` changed model hash during its run (`.log` at start
  `50d295f6859e9c13`, `.json` at end `136fbadcc3a8a3ff`; the JSON alone does not say so) and the
  tree that rendered it was never committed. These figures are not reproducible from a named
  source. `chcp_559.py confirm` now REFUSES such records (dirty, moved during run, absent, or
  different model/engine/probe fingerprint). Rerun on the build box from a clean commit:
  `ch-sweep`, then `confirm --sound CH --sweep ...ch-sweep-dev.json`, then `tables`, plus the bpq3
  confirm (`--cand '{"hpq": 0.5, "bpq": 3.0}'`); commit the regenerated records. If the numbers
  move, record it here as a wrong-then-right entry.
- Status: `CANDIDATE_559 = {"CH_HP_Q_X10": 5}` in model/drums_fx.py, DISABLED (kit_808 unchanged).
  Reachable via `kit_808_candidate_559()` / `verify_drums.py --kit candidate-559`.

## CP (decay 15.9, attack 6.2-7.1)
- Tail tau / level sweep (`cp-sweep-dev.*`, `cpt-sweep-dev.*`, frozen amendment CP-T):
  no programming-only change selects. tau 120 ms improves decay (9.1) but breaks the
  eligibility rules (spec/centroid regress); every CP-T candidate violates at least one guard.
  `SELECTED (DEV): None`. The CP defect is not repairable by the registered tail tunables;
  it needs a structural change (coordinate with #556 shared MA resources).
- `cp-tail.*`: measured reference tail (amp tau ~83 ms 0.08-0.2 s, ~315 ms 0.3-1.2 s: two slopes,
  which a single-tau tail cannot match).

## Not done
- No RTL / I2S equivalence or deadline run for the candidate (needs the build box):
  `python rtl-sketch/verify_drums.py --kit candidate-559` then `make verify`.
- No image delivery claimed.
