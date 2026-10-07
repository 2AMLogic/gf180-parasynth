# 0025: The shared-bus DC coupling: register, fixed K = 10, widths, lifecycle

- **Status**: proposed. Model and contract only; no RTL (#552), no sound claim (#553).
- **Date**: 2026-10-07
- **Decided by**: Builder, issue #551 (part of #510, which rests on the diagnosis of #152 and the prototype of #165).

## Context

The drum block has no DC blocking, and the machine does (`docs/dcblock/README.md`).
`model/drums_fx.py` carried an EXPERIMENTAL constructor-selected blocker (`DrumsFx(couple=...)`),
default off, measured by `tools/probes/dc_blocker.py`. That is an instrument, not a product:
it has no register, no declared widths, no stated enable or reset behaviour, and a
Python integer accumulator that cannot overflow, so no number measured on it says what a
finite RTL word must be. #551 turns it into a specified, register-driven block so #552
(RTL) and #553 (sound preservation, decoded I2S) have one contract to meet.

## Decision

Specified in `spec/NUMERIC-CONTRACT.md` 15.10 (revision 16); the model is `drums_fx.DrumsFx`
and `drums_fx.DcBlockFx`, the tests `model/test_drums_fx.py::test_coupling_*`.

1. **Address `0x30`, one enable bit.** Audited against the model's map (`0x00`, `0x10..0x1A`,
   `0x20..0x25`, `0x40..0x87`, `0x90..0xA6`, `0xB0..0xEF`, `0xFF`) and `rtl-sketch/drum_regs.v`
   (no `8'h30` literal; decoder ranges are the same set). Candidates `0x88` and `0xA7` were
   rejected: both sit directly after a block that has already grown (ENV 8 -> 18 entries moved
   PATH and MODE once; `0x88` would be the first address ENV's next entry takes). `0x30` is
   16 addresses from ENV and 10 from OSC_INC. Bit 0 enables; bits 31..1 reserved and ignored;
   no read-back; reset 0.
2. **K = 10, fixed, not a register.** A tunable corner is a register and a verification
   surface nobody has asked for; the sweep (below) is the evidence for the value.
3. **Placement**: one filter on `dmix` (22 bits signed) and one on `body` (19 bits signed), after
   the bank and before the output stage's products and its single clamp. Not on excitations
   (`COUPLE_EXC`: the excitation is asymmetric by design) and not after the clamp
   (`COUPLE_POST`: DC removed after clipping cannot recover what the clip destroyed;
   `dc_blocker.py --placement`).
4. **Widths**: charge `n + K` bits (32 and 29), output `n + 1` bits (23 and 20). The bound
   is a proof, not an observed counter (`DcBlockFx` docstring): the update is nondecreasing in
   the charge, so the interval is closed; the near-limit tests drive both buses to the
   reachable extremes and assert the counters equal the declared widths (a narrower word
   fails). The output stage's exact sum grows to 40 bits signed.
5. **Shifts and truncation**: one arithmetic right shift (floor). `-1` leaves one nonzero
   sample, `+1` leaves 1024; this asymmetry is part of the contract, as is the dead zone
   (an impulse below 2^10 stays in the charge, with `y = 0`).
6. **Enable semantics: the filter always runs, the enable selects its output.** The charge
   tracks the bus while bypassed. Re-enable reads the true charge; the output steps once
   from `x` to `x - floor(acc / 2^10)` and follows the recurrence. Frozen-while-bypassed
   was rejected: re-enable would depend on how long the bypass lasted. Costs no extra
   hardware (two adders, one mux per bus).
7. **Lifecycle**: charge survives hits, chokes, accent writes and retunes; the drum `RESET`
   (`0xFF`) and the hardware reset clear charge and enable; the voice-page `RESET` does not.
8. **Constructor modes are not the product.** `DrumsFx(couple=...)` stays for the probes;
   writing `COUPLE` to such a block raises, and the register path is K = 10 regardless of
   `couple_k`.

## What this record does not establish

- **K = 10 is the BD network's corner, not the CY's.** C49 0.47 uF into 45.05 kOhm gives
  7.52 Hz (reference 2). The CY goes through different circuitry, so that rationale does not
  set the CY's corner. `docs/sensitivity/coupling-k.json` is a K = 8..13 sweep of the CY's
  residual sub-20 Hz energy: it shows K = 10 is a point on a measured, monotone curve. It does
  not say the CY wants 10, and the curve says larger K removes less sub-20 Hz energy and
  settles more slowly. `dc_blocker.py` carries the preservation side.
- That the sweep's prediction is independent of the measurement is a claim about its derivation
  (the closed-form one-pole law, see the record), not that the model is acoustically right.
- Nothing here says it sounds better. #553 owns that.

## Alternatives considered

- **A register for K.** Rejected above.
- **Enable bit in the voice page.** Rejected: the voice page's RESET would then have to
  decide whether it reaches the drum coupling; keeping it in the drum page keeps the two
  pages independent, which is already a tested property (15.8).
- **Charge frozen or cleared on disable.** See 6; cleared makes the disable a hidden reset.
- **Per-voice or per-excitation filters.** A linear filter commutes with a sum; two registers
  for sixteen voices is the same superposition (`dc_blocker.py` placement record).

## Consequences

- #552 implements exactly the table in 15.10, including the 40-bit exact sum, and verifies
  bit-exactness against `DrumsFx` with the enable on, off and toggled mid-ring.
- Reset-off is pinned by SHA-256 of the previous design's streams, taken on the bypass model at
  `7dd33fb` before the change, so an RTL image that never writes `0x30` is byte-identical.
- `tools/probes/coupling_controls.py` is the injected-defect inventory for this contract
  (wired into `make controls`).
