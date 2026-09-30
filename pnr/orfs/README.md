# pnr/orfs -- placed-and-routed area on gf180mcu with OpenROAD-flow-scripts

Reproducible ORFS (OpenROAD-flow-scripts) configs for the monosynth datapaths and the polysynth
`synth_core`, run inside the pinned `openroad/orfs:26Q3-296-gda37dce1c` Docker image (the one
`prep/asic` and klayout-tools already use; it ships the complete gf180 platform: 7t/9t liberty,
LEF, GDS, PDN strategy, tapcell script, metal-fill config, OpenRCX rules). Results and the
write-up are in `docs/pnr-first-run.md`.

```
./run-orfs.sh ladder_dp                      # full flow: synth -> floorplan -> place -> cts -> route -> finish
./run-orfs.sh synth_core                     # NV=4, RTL from the sibling gf180-polysynth checkout
./run-orfs.sh ladder_dp run RUN_SCRIPT=$PWD/sta-corners.tcl RUN_LOG_NAME_STEM=sta_corners   # tt/ss/ff STA
python3 check-pdn.py work/results/gf180/ladder_dp/base/6_final.def                          # PDN/tap/fill proof
```

Results, logs and reports land under `work/` (git-ignored):
`work/{results,logs,reports}/gf180/<design>/base/`. `6_final.{def,gds,v,odb,spef}`, `6_report.log`
and `synth_stat.txt` are the files worth opening.

| File | Purpose |
|---|---|
| `<design>/config.mk` | ORFS design config: `TRACK_OPTION=7t`, `CORNER=TC` (tt_025C_5v00; ORFS's default `BC` is ff_n40C_5v50), utilisation, density |
| `<design>/constraint.sdc` | 81.38 ns clock (12.288 MHz), 20 % I/O delays |
| `gf180_7t/cells_adders.v`, `cells_latch.v` | 7-track copies of the platform techmaps: the stock ORFS gf180 platform hard-codes `mcu9t5v0` cell names, which breaks `TRACK_OPTION=7t` |
| `gf180_7t/synth_unsigned.tcl` | `SYNTH_SCRIPT` wrapper that strips `signed` from netlist declarations (OpenSTA's Verilog reader rejects them; `[ERROR STA-0171]`) |
| `sta-corners.tcl` | post-route STA at tt_025C_5v00 / ss_125C_4v50 / ff_n40C_5v50 with per-corner OpenRCX extraction |
| `check-pdn.py` | proves the routed DEF has Metal1 follow-pin rails, Metal4/Metal5 straps, PDN vias, tap/endcap and filler cells |
| `summarize.py` | the per-stage tables in `docs/pnr-first-run.md`. **Exit 2 = REFUSED**: it will not print a `die / synth cell area` ratio for a run whose die came from a utilisation target |
| `area_provenance.py` | the guard behind that refusal, and its two permanent injections (issue #245). `area_provenance.py --design <d>` classifies a design's config on its own: `synth_top` OK (fixed die), `ladder_dp` and `synth_core` REFUSED (`CORE_UTILIZATION = 50`, so their die area is the cell area / 0.50 and a ratio only recovers the input) |

Gotchas met on the way (each cost a run): GNU make keeps trailing spaces before an inline `#`
comment in a value (`CORNER = TC   # ...` makes `$(TC   _LIB_FILES)` empty); the design config is
included *before* the platform config, so platform variables set with plain `=` (e.g.
`DONT_USE_CELLS = *_1`) can only be overridden on the make command line.
