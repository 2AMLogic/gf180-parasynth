# Arty wrapper UART-bridge clean run, contract-revision-14 tree

The wrapper's digital proof for the tree that carries **both** the shark-tooth's
polyBLAMP (contract revision 13, DR 0017, #244: `voice_dp.v`) and the clap's
final strike (revision 14, #273: `ENV_FRATE` in `drum_regs.v`/`drum_dp.v`/
`drum_kit.v`, wired through `synth_top.v`). This is what
`fpga/publish_arty.py`'s `VERIFICATION_BY_WRAPPER["arty_a7_top"]` binds.

It exists because merging main into #273 left both parents' records stale:
`shark-blamp-clean` predates the L2 drum RTL and `l2-clean` predates the
polyBLAMP `voice_dp.v`. Neither was a correct textual resolution of the
conflict, so the merged tree was benched. Run on the build box at `36e1f83`
(clean tree) through `tools/run_all.py` (`run_all.json`: 2 jobs, exit 0):

    python3 fpga/verify_uart_bridge.py --scenario phrase --outdir build/uart-rev14
    python3 fpga/verify_uart_bridge.py --start-red --outdir build/uart-startred

| file | what |
|---|---|
| `verification.json` / `.txt` | clean `phrase`: 87/87 writes, 6,734 I2S periods bit-exact, `worst_strobe_cycle` 176 of 256 |
| `start-red.json` / `.txt` | the same bench against `fpga/stubs/arty_a7_uart_stub.v`: `FAIL`, 0 of 26 writes |

Against both parents the comparison block and transcript hash are identical
(`4fbbbb87…`). Only source hashes move: against `shark-blamp-clean` the four
L2 files, and against `l2-clean` `voice_dp.v` (polyBLAMP) plus `drum_dp.v`
and `drum_regs.v` (comments renumbered 13 to 14). The phrase is a sawtooth
voice phrase, so it exercises neither the shark-tooth corner nor the clap.
It proves that the wrapper and the unchanged paths work on this tree, and
nothing about those two features at the pins. Their pin-level evidence is
their own benches.

No Vivado, no bitstream, no board. The published R0/R1 image is revision-11
RTL and cites `uart-clean` (see
`tools/test_check_arty_evidence_binding.py::test_r0_published_image_is_bound_to_its_historical_source_set`).
