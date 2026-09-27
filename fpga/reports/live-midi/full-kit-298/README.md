# T-LIVE-MIDI on the 16-sound live player (#298)

Run on the build box at `7c298ee` (this branch rebased on `main` 492a554), through
`tools/run_all.py` (`run_all.json`, exit 0):

    python -m pytest fpga/test_midi_session.py fpga/test_uart_host.py fpga/test_uart_host_rolling.py fpga/release -q   # 231 passed
    python tools/trial.py run T-LIVE-MIDI --mode sim     # PASS, 5/5 controls caught
    python tools/trial.py run T-LIVE-MIDI --mode rtl     # PASS, 5/5 controls caught (9,560 s)

RTL (the session's own UART bytes through `arty_a7_top`, decoded I2S against the model on
the independently built schedule; `trial-rtl.log`):

| scenario | writes | I2S periods | mismatch |
|---|---|---|---|
| coverage | 404/404 | 109,053 | 0 |
| alternates (all five exclusive pairs, both directions, switches mid-tail) | 590/590 | 212,733 | 0 |
| pressure | 588/588 | 46,653 | 0 |
| sustained (3 s of the declared load) | 502/502 | 166,513 | 0 |
| control WRONG_DRUM_MAP | | 109,053 | 47,837 (caught) |
| control DELAYED_EVENT | | 109,053 | 84,474 (caught) |
| control WRONG_ALT | | 212,733 | 202,649 (caught) |

Device-contract controls, both modes: DROP_NOTE_OFF, WRONG_DRUM_MAP, DELAYED_EVENT,
WRONG_ALT and NO_TAIL_CUT caught. Latency (sustained, sim): p95 16.73 ms, p99 16.98 ms.

An earlier rtl run of this trial (at `a784cff`, before the rebase) was cancelled at
9,000 s when its owner was stopped (receipt status `cancelled`, NO VERDICT); this run
replaced it. The `.plan.json` replay plans (~150 KB each) are not copied; their digests
are in `trial-rtl.receipt.json`.

R1's released record still says the live player maps 11 of 16 sounds: it describes R1
as released, and is not changed here.
