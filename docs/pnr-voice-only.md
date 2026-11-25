# Voice-only quarter-slot placement

**Verdict for #1049 — REFUSED pending the source and stage evidence.**
A previous Builder reported a legal placement and a pin-level run for
`209e661e`. The issue comment has the summary. At `e27e9798` the repository
has neither the measured selection nor its ORFS stage files. #1051 still
needs a verifiable fit decision.
[Previous Builder's report](https://github.com/2AMLogic/gf180-parasynth/issues/1048#issuecomment-7006289246)

Recover the `209e661e` git bundle, `pnr/orfs/evidence/voice_only/` and the
pin-bench transcripts from the previous Builder's worktree or the pinned build
box first. A recovered measurement belongs to the source that produced it.
Check the command, source revision, top parameters, defines, tool versions
and image digest before using those files. Start a fresh run on the pinned
box if the original inputs or evidence cannot be recovered.

## Replay the historical synthesis control

The historical joined RTL is `d1e5068`. The quarter-slot configuration and
its recorded synthesis metrics landed together in `2c7e3b53`. The Git tree
IDs for both `rtl-sketch/` and `spec/reference/tables/` match across the
two commits, so the latter supplies the recorded configuration **and** the
original source bytes. The comparison target is 59,289 standard cells and
1.97938 mm² at synthesis.
[Archived stage metrics](../pnr/orfs/evidence/synth_top/quarterslot-does-not-fit/1_synth.json)

On the pinned box, run these commands from the repository root. Use the
launcher from this revision so that Make receives a whole rectangle as one
argument. Keep the archived configuration, tech maps, SDC, RTL and ROMs
together. Stop on a failed command.

```sh
git diff --exit-code d1e5068 2c7e3b53 -- rtl-sketch spec/reference/tables
run=$(mktemp -d)
git archive -o "$run/control.tar" 2c7e3b53 -- rtl-sketch spec/reference/tables \
    pnr/orfs/synth_top pnr/orfs/gf180_7t
tar -xf "$run/control.tar" -C "$run"
cp pnr/orfs/run-orfs.sh "$run/pnr/orfs/"
export ORFS_IMAGE='openroad/orfs:26Q3-296-gda37dce1c@sha256:ebc8142da6d65d1a1e9a528aa2cedcde356243465dd859af8d3ade51075f8cb2'
die='0 0 1314.88 1317.12'
core='10.64 11.76 1304.24 1305.36'

ORFS_WORK="$run/control-work" "$run/pnr/orfs/run-orfs.sh" synth_top \
    FLOW_VARIANT=archived "DIE_AREA=$die" "CORE_AREA=$core" synth
control="$run/control-work/logs/gf180/synth_top/archived/1_synth.json"
jq -e '[.synth__design__instance__count__stdcell, .synth__design__instance__area__stdcell] == [59289, 1979380]' "$control"
```

## Compare and place the measured selection

These commands require a checkout containing the measured `WITH_DRUMS`
parameter. First exercise its default joined selection and its voice-only
selection through SPI and I²S. Preserve the non-silent model comparison,
frame cadence and stalled-drum-ready negative control. From `rtl-sketch/`,
check the elaborated cell types using the ORFS config's default voice
defines and the same assertions for both selections.

```sh
read_top='read_verilog synth_top.v'
hierarchy='hierarchy -check -libdir . -top synth_top'
kept='select -assert-count 1 t:voice_dp; select -assert-count 1 t:*ladder_dp_n; select -assert-count 1 t:spi_ctl; select -assert-count 1 t:i2s_tx'
absent='select -assert-none t:*drum_regs t:*drum_kit'
yosys -p "$read_top; $hierarchy; $kept; $absent"
yosys -p "$read_top; $hierarchy -chparam WITH_DRUMS 0; $kept; $absent"
```

The first command is the intentional red control. At `e27e9798` Yosys
0.67+post passed all four presence assertions, then rejected exactly
`synth_top/u_dregs` and `synth_top/u_drums`. The second command must exit
successfully on the measured source.

Back at the repository root, after the historical control passes, use the
same current source, image and voice defines for both commands. The config
selects GF180 7-track, TC, 81.38 ns and the stock cell exclusions. Supply the rectangles explicitly
and leave `CORE_UTILIZATION` unset. The 0.78 value below sets global
placement density.

```sh
ORFS_WORK="$run/current-work" pnr/orfs/run-orfs.sh synth_top \
    FLOW_VARIANT=joined "DIE_AREA=$die" "CORE_AREA=$core" PLACE_DENSITY=0.78 synth
ORFS_WORK="$run/current-work" pnr/orfs/run-orfs.sh synth_top \
    FLOW_VARIANT=voice_only "DIE_AREA=$die" "CORE_AREA=$core" PLACE_DENSITY=0.78 \
    'VERILOG_TOP_PARAMS=WITH_DRUMS 0' place
```

Check the hierarchy in the voice-only synthesis input saved by ORFS as well.
This binds the structure to the run that produced the area. Using the Yosys
version from that run, apply the same selectors to its checkpoint.

```sh
netlist="$run/current-work/results/gf180/synth_top/voice_only/1_1_yosys_canonicalize.rtlil"
yosys -p "read_rtlil \"$netlist\"; $kept; $absent"
```

Keep both `1_synth.json` files. For the voice-only run, retain the floorplan
log with the die and core coordinates, `2_1_floorplan.json`,
`3_5_place_dp.json` and the detailed-placement log with the
`check_placement` result. A fit requires successful legal placement in the
fixed rectangle. Report its standard-cell count and area from that stage.
Divide the placed area by the fixed core area for achieved utilisation and
subtract the placed area from the core area for remaining space.
