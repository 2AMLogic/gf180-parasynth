# Arty wrapper UART-bridge clean run, polyBLAMP-on-shark-tooth tree

The wrapper's digital proof for the tree that carries the shark-tooth's
polyBLAMP correction (contract revision 12, DR 0017 — `voice_dp.v` appends
states `S_SKM`/`S_W3`/`S_W4` after `S_DR1`, so this PR's states start at 80
rather than colliding with the drift work's `S_DA0`..`S_DR1` at 75..79). This
is what `fpga/publish_arty.py`'s `VERIFICATION_BY_WRAPPER["arty_a7_top"]`
binds and what `fpga/build_arty.py --prepare-only` validates against the live
source set.

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

## Why this directory exists rather than an edit to `drift-clean`

`fpga/reports/arty/drift-clean` was the proof bound to `arty_a7_top` before
this PR. Evidence directories here are added, never rewritten — the same
reason `clean`, `uart-clean`, `uart-bridge-2025.1` and `drift-clean` all
coexist (see `drift-clean/README.md`, and `tools/check_arty_evidence_binding.py`'s
docstring: "Evidence directories here are added, never rewritten"). The UART
bridge's own logic (parser, event queue, reset) does not touch the voice
datapath, so this record only needed refreshing because it hashes the full
compiled source set, including `rtl-sketch/voice_dp.v`, and this PR's shark
FSM states change that file's bytes.

## What this PR changed, measured rather than argued

The same bench was run on this tree and against the pre-PR `drift-clean`
record. Exactly one source hash moves (`rtl-sketch/voice_dp.v`) and every
measured field is identical:

| | `drift-clean` (pre-PR) | this tree |
|---|---|---|
| `worst_strobe_cycle` | 176 of 256 | 176 of 256 — unchanged |
| `transcript_sha256` | `4fbbbb8744d064143b366a86664bfcb9ece9b94d2598df026fadeadb0c96734b` | `4fbbbb8744d064143b366a86664bfcb9ece9b94d2598df026fadeadb0c96734b` — identical |
| writes / periods | 87/87, 6,734 | 87/87, 6,734 — unchanged |

The UART-bridge scenario never exercises the shark-tooth oscillator, so the
polyBLAMP correction and the new FSM states are invisible to this particular
bench — as expected, since this record's job is only to prove the bridge
still binds to the current compiled source set, not to re-verify the voice
datapath (that is `rtl-sketch/verify_voice.py`'s job; see the shark/waves3
scenarios and the `SHARK_BLAMP_SIGN` injected control in `Makefile`'s
`controls` target).

## What this record does NOT cover

No Vivado, no bitstream, no board. It is an Icarus Verilog run of the wrapper
at its pins with the MMCM bypassed, and it carries no physical timing claim.
The published Arty bitstream predates both drift and polyBLAMP — see
[`../../../ARTY.md`](../../../ARTY.md), "the published baseline predates
per-oscillator drift".
