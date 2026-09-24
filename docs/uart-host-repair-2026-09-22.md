# UART host command repair + CI evidence coverage -- 2026-09-22

Branch `feature/uart-control-bridge` (PR #202). Everything here was
demonstrated red on the unrepaired code before it was fixed, through a
controlled serial endpoint (`fpga/uart_device_sim.py`, a pty loopback that
implements rtl-sketch/uart_bridge.v's side of the contract) driving the REAL
`fpga/uart_host.py` `main()`/`Bridge` path. The RTL bench had never once
driven the CLI; that was the testing gap.

Transcripts (committed):

- `docs/uart-bridge-red-transcript.txt` -- all ten findings reproduced, 0/10
- `docs/uart-bridge-gold-transcript.txt` -- same probes after the repair, 10/10

## Per-finding before/after

| # | finding | before (recorded) | after |
|---|---|---|---|
| 1 | `note_writes(45, False)` released nothing | `[]` -- a fresh KeyHost has no held key | `[(0, 0, 0x21, 0)]` -- the gate-off write |
| 2 | `note-off` subcommand never opened a port | exit 2, usage printed, 0 gate-off writes on the wire | exit 0, exactly one gate-off write executed by the device |
| 3 | `Bridge.run()` planned before the device origin | `ValueError: event 1: due 0 is at or before its acceptance frame 75` | plans from the origin; verify+re-anchor loop; impossible schedules are `Refused`, never raw ValueErrors |
| 4 | `--hold-frames` parsed but unused | dry-run output identical for hold 1920 and 4800 | the note-off is a DEVICE event at gate-apply + hold; dry-run shows the gate-off row moving by exactly the delta |
| 5 | origin applied twice | first send planned at device frame 2044 with the device at ~1022 (~2x origin) | `first send f1046 = origin 1029 + lead 17` (exact equality asserted) |
| 6 | `send()` coalesced a waited schedule | second write applied 61 frames after the first (plan said 4800) | 5948 frames apart; contiguous packets burst, plan waits are STATUS-paced |
| 7 | `status()` read 64 bytes at 0.5 s | `REFUSED (exit 2) after 2.31s` vs a device that answers after 0.6 s; otherwise one-round-trip-STALE snapshots (28 878 frames old recorded) | fresh snapshot (156 frames old); framed 8-byte reads, consumed buffer, round trip measured into the send lead |
| 8 | `wait_until` integer compare | `REFUSED (exit 2)` for a target past the 65536 wrap | wrap-safe (`_reached`), reached in 0.03 s |
| 9 | default `bar808` had no preflight | raw ValueError before any check; the shifted schedule it would have sent peaks at 178 queued events (first excess index 64) vs a 64-deep queue | `REFUSED (exit 2)` BEFORE sending: "event packet 51 cannot be delivered in time: earliest acceptance frame 8334 is at or after its due 8309. The wire carries one 10-byte event packet per 41.8 frames at 115200 baud but this fixture schedules one every 26.1 frames; the queue's peak demand is 189 against a depth of 64 (first excess at packet index 64)" |
| 10 | `or True` masked a wrong golden | payload bytes `014000000004` vs "expected" `004000000004` | golden fixed (SEC is bit 40, the LSB of the first register byte); `or True` deleted |

Feasibility verdicts (deterministic, from the planner's ideal acceptance
times; recompute with the units in `test_uart_host.py`):

- `bar808` (default): **REFUSED at 115200 baud** -- peak demand 189 vs 64,
  first excess at packet index 64, wire delivers one event packet per 41.8
  frames but the bar schedules one per 26.1. It cannot preload and flow
  control cannot keep up; musical dues are not silently stretched.
- `m5a` (the short smoke phrase): **FEASIBLE by preload** -- 67 events, peak
  16 in flight; dues spread to the link's own event pacing (one event packet
  per 42 frames, the same spreading rule `feasible()` applies on the SPI
  side); works end to end through the pty device with zero drops/errs.
- Flow-control contract: documented in the `uart_host` module docstring
  (preload if peak fits; watermark batching via STATUS/ACK/ERR otherwise;
  REFUSED with the packet index if the wire cannot meet the dues).

## CI evidence coverage

`fpga/build_arty.py --prepare-only` refused with `verification source
differs: rtl-sketch/uart_bridge.v`: the build requires the file,
`verify_synth_top.simulate()` compiled it by appending it OUTSIDE
`resolve_sources()`, so `verify_arty`'s verification record never hashed it.
`resolve_sources()` is now the single authoritative set (compile, provenance
hash, build gate all read it); the refusal on an omitted or changed UART
source is kept (`test_verification_cannot_be_missing_stale_or_mutated`).

Local spi-i2s job reproduction, all green:

- `pytest fpga/test_build_arty.py fpga/test_publish_arty.py` -- 16 passed
- `fpga/verify_arty_controls.py` -- clean PASS, both pin mutations caught
- `fpga/build_arty.py --prepare-only` -- PREPARED, exit 0

## CLI bytes through the RTL bench

`fpga/verify_uart_bridge.py --replay` feeds a CLI capture
(`uart_host --capture`: bench S-lines + plan.json) through the UART RX of the
real Arty wrapper; I2S is decoded at the pins and compared against the
integer model. Records and captures: `fpga/reports/arty/uart-replay/`.

| replay | verdict | numbers |
|---|---|---|
| held note (`run --fixture none --note 45 --hold-frames 1920`) | PASS | 26/26 writes, 0 timing errors, 0 corrupt, 0 I2S mismatches over 3412 periods |
| held note + m5a phrase (`run --fixture m5a ...`) | PASS | 93/93 writes, 0 timing errors, 0 corrupt, 0 I2S mismatches over 7701 periods |
| negative control: one flipped data bit in the stimulus | **FAIL, exit 1** | writes_bad 25, timing bad 25, 25/26 seen, 1 checksum ERR |

The capture separates stimulus (emitted bytes) from expectation (decoded
intent): a stimulus-only mutation is otherwise invisible by construction --
the first mutated replay PASSED, which is why the separation exists.

## Mutation controls on the pty harness

```
UART_HOST_INJECT=drop-gate-off pytest fpga/test_uart_host_pty.py -k control
  -> CAUGHT: gate-off events fired = 0 (want 1)
UART_HOST_INJECT=wrong-due  pytest fpga/test_uart_host_pty.py -k control
  -> CAUGHT: gate interval 2062 frames (want 1920+-8)
```

## pulse2x-rtl

Run 35715612528's pulse2x-rtl was not a defect: the push and pull_request
events of this branch shared the `pulse2x-short-${{ github.head_ref || ...
}}` concurrency group, and the PR run cancelled the push run ("Canceling
since a higher priority waiting request exists"). The PR run (35715716183)
is green including pulse2x-rtl. The group is now keyed by PR number / full
ref so the two event kinds queue separately.

## Wrong-then-right accounting

Measurements in this repair that were wrong before they were right, all
caught by controls or plausibility checks rather than inspection:

1. The device sim treated its own BOOT byte as input (spurious ERR 6) --
   seen because the first STATUS probe returned nothing.
2. The sim's resync-gap check fired on idle-between-packets (spurious
   ERR 4 flood) -- gap belongs to incomplete packets only.
3. The sim's reg-frame unpacking assumed flag/sec in separate bytes; bit 40
   is the LSB of byte 0 (struct.error, then a wrong golden in my own probe
   -- finding 10's trap reproduced by the repairer).
4. The golden payload: my first "want" (000140000004) was itself wrong;
   bit-40 arithmetic gives 014000000004 (the old test's bytes were wrong
   too, masked by `or True`).
5. `preflight`'s first watermark simulation ignored wire pacing between held
   packets and reported bar808 FEASIBLE; with pacing chained through holds
   it refuses at packet 51 (first excess 64).
6. The re-anchor deficit went negative on wrap and shifted dues BACKWARD
   into the wrap zone (dues of 0 on the wire).
7. Per-packet `ser.flush()` (~25 ms each on this driver) alone pushed the
   gate-off due into the past; then per-packet `ser.write` syscalls still
   did -- contiguous bursts are the contract-true fix.
8. The pty sim's 1 ms fire polling logged fires up to 48 frames late, and
   post-stall wall-clock stamps corrupted acceptance frames; the sim now
   processes its timeline chronologically with a cursor and logs scheduled
   frames.
9. `MUTANT_EXIT=0` after a failing mutant: `$?` after a pipe captured
   `tail`'s status -- the exact bash trap this repository documents.
10. The first replay mutant PASSED: the mutation had removed the event from
    both stimulus and expectation (self-consistent, therefore meaningless).
    Stimulus and expectation are now separate in the capture format.

That is ten wrong-then-rights in one session; the controls, plausibility
guards and the harness caught every one.
