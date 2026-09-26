# Arty wrapper UART-bridge clean run, per-oscillator-drift tree

The wrapper's digital proof for the tree that carries per-oscillator drift
(contract 6.11, DR 0019 — `voice_dp.v` states `S_DA0`/`S_DV0..1`/`S_DR0..1`
and register `0x2D`). This is what `fpga/publish_arty.py`'s
`VERIFICATION_BY_WRAPPER["arty_a7_top"]` binds and what
`fpga/build_arty.py --prepare-only` validates against the live source set.

    python3 fpga/verify_uart_bridge.py --scenario phrase --outdir build/uart-controls
    cp build/uart-controls/phrase/verification.json  verification.json
    cp build/uart-controls/phrase/transcript.txt     verification.txt

    python3 fpga/verify_uart_bridge.py --start-red --outdir build/uart-startred
    cp build/uart-startred/start-red/verification.json  start-red.json
    cp build/uart-startred/start-red/transcript.txt      start-red.txt

| file | what |
|---|---|
| `verification.json` | the clean `phrase` run: 87/87 writes delivered, 6,734 I2S periods bit-exact against the integer model, `worst_strobe_cycle` 176 of 256 |
| `verification.txt` | that run's transcript, hashed into the record as `transcript_sha256` |
| `start-red.json` | the same bench against `fpga/stubs/arty_a7_uart_stub.v`: `FAIL`, 0 of 26 writes delivered. The clean PASS above means nothing without it |
| `start-red.txt` | that run's transcript |

## Why this directory exists rather than an edit to `uart-clean`

`fpga/reports/arty/uart-clean` is the proof the **published integrated
baseline bitstream** cites by hash (`integrated-baseline-2025.1/report.json`
and `publication.json`, `verification.record_sha256`
`ae9cedc8a2045cdaf9d83fce81ab356f04ca5f535845dc713b36ba0d129c3fcb`).
Overwriting it would leave a published hardware artifact naming a digital
proof no longer in the tree. Evidence directories here are added, never
rewritten — the same reason `clean`, `uart-clean`, `uart-bridge-2025.1` and
`vivado-2025.1` all coexist.

## What drift changed, measured rather than argued

The same bench was run on a pristine `main` tree (`git archive main`) and on
this one. `main` reproduces `uart-clean/verification.json` exactly — same
comparison, same `transcript_sha256`
(`9a96ebb387e26e61de9cc09a5d68c044c4d6f743f88bac4db004c206f493977e`); only
the key *order* of `source_sha256` differs, because `resolve_sources` has
since moved `uart_bridge.v` ahead of `tb_uart_bx.v`.

Between the two trees exactly one source hash moves (`rtl-sketch/voice_dp.v`)
and exactly one measured field moves:

| | main | this tree |
|---|---|---|
| `worst_strobe_cycle` | 175 of 256 | **176 of 256** |
| `uart_i2s.txt` (the decoded I2S wire) | `3c9d32e671a5…` | `3c9d32e671a5…` — identical |
| `uart_samp.txt`, `uart_txd.txt`, `uart_wrs.txt`, `uart_cmds.txt` | | all identical |

The audio is byte-identical. The one cycle is `S_DA0`, which the frame enters
unconditionally so that switching drift on mid-note does not depend on when it
was switched on; at `DRIFT = 0` it computes three zero deviations and leaves
the increments alone. The cost is one cycle of the 256-cycle frame, and the
sample still strobes 80 cycles inside the deadline.

## What this record does NOT cover

No Vivado, no bitstream, no board. It is an Icarus Verilog run of the wrapper
at its pins with the MMCM bypassed, and it carries no physical timing claim.
The published Arty bitstream predates drift — see
[`../../../ARTY.md`](../../../ARTY.md), "the published baseline predates
per-oscillator drift".
