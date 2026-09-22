# DSP DPREG-4 disposition — the 13 feedback warnings in the published Arty build

Disposition: **all 13 DPREG-4 warnings are dismissed.** No reachable OPMODE
value on any flagged DSP48E1 selects P as an operand, so the unsupported
unregistered feedback path the warning guards against cannot occur in this
netlist. No RTL, parameter or latency change is made; the published baseline
is untouched. **No latency is added for DPIP-1/DPOP-1/DPOP-2 either** —
rationale in [§6](#6-why-no-latency-is-added-for-dpip-1dpop-1dpop-2).

Per the deliverable's stop rule: zero instances can reach unsupported
P-feedback, so there is nothing to escalate; this document is the full
account.

Evidence set: [dsp-dpreg-evidence/](dsp-dpreg-evidence/) — routed-checkpoint
extraction (`dsp_cells_dump.txt`, `vivado_extract.log`, manifest-verified),
parsed analysis (`dsp-opmode-analysis.json`), and the directed mapped-DSP
comparison (`dsim/`, `unisim/`).

---

## 1. What was examined, and preconditions asserted

The object under review is the **published routed checkpoint** — the exact
checkpoint the published bitstream was written from — not a re-synthesis.

| precondition | assertion | result |
|---|---|---|
| routed.dcp integrity | SHA-256 asserted **on the box, before `open_checkpoint`** | `fdb3b45d7d7b108bf3246a5013f7ac6171f126956a4d0fcc2af1a20e499945fe` ✓ (box copy and `/private/tmp/wt-arty-bringup` copy) |
| tool version | `vivado -version` and in-Tcl `[version -short]` guard | 2025.1 (SW Build 6140274), matches `report.json` |
| instance names | all 13 names from the box's own `drc.rpt` matched `get_cells` exactly once | 19/19 cells found (13 flagged + 6 unflagged siblings), `MISSING_COUNT 0` |
| checkpoint read-only | extraction only queried properties/pins/nets; no `write_checkpoint`; nothing written outside `/home/ubuntu/dsp-review` | ✓ |
| RTL provenance | SHA-256 of reviewed sources vs `report.json` `source_sha256` | `rate_conv_2x.v` `0e284688…`, `decimate_2x_tm_sym.v` `0575e80e…`, `osc_substep_pair.v` `6dc5cfb9…`, `voice_dp.v` `a1575257…` — all match the published build |
| extraction integrity | artifacts hashed on the box, re-hashed locally against the box manifest | ✓ `MANIFEST.sha256` |

Extraction run under the shared `flock /home/ubuntu/vivado.lock`; transcript
`dsp_cells_dump.txt` SHA-256
`3a8f8b83d124f1be09c229134e6cdfd8e6eaceeb05e0d1b9f39579597832208d`
(148,858 bytes, 19 cells, pins, drivers, and backward control cones).

**Environment incident, disclosed:** part-way through this session the remote
box's root volume was replaced (cloud-init rebuild; uptime reset; `/tools`
with Vivado 2025.1 and the box's `routed.dcp` copy vanished; root df changed
from 194G/143G-used to 97G/5.0G-used; `/home/ubuntu` survived). All
checkpoint evidence above was extracted **and hash-verified before the
loss**. The xsim leg of the directed comparison therefore could not run on
the box and was **refused**, and re-run under the brief's authorized
alternative (iverilog + Xilinx unisim) — see §5.4. The box needs Vivado
reinstalled before any future build work; flagging that to the coordinator is
part of this deliverable.

## 2. What DPREG-4 claims, and what would satisfy it

The warning (`DSP48E1_PregDynOpmodeZmuxP`, one per cell):

> The DSP48E1 cell … with the given dynamic OPMODE[6:0] connections may lead
> to an unregistered asynchronous feedback path without the PREG attribute
> enabled. … if one of the internal P feedback opmodes is possible for this
> design the PREG attribute must be set to 1, currently set to 0

The feared failure mode: with `PREG=0`, the P output is combinational; if the
(dynamic) OPMODE can select **P as a post-adder operand**, the DSP closes a
P→adder→P combinational loop — an unanalyzable asynchronous feedback path
(AMD UG479, DSP48E1, OPMODE tables 2-7 through 2-9).

The warning is satisfied by proving **P-as-operand is unreachable**: for
every OPMODE value the design can present, the X mux never selects 10 (P),
the Y mux never selects 10, and the Z mux field never selects 010/100/11x
(the P and P-derived selections). PREG=0 is then legal — there is no
feedback, asynchronous or otherwise, because nothing reads P back.

Mux semantics cited here are the vendor's own simulation model
(`Xilinx/XilinxUnisimLibrary` `DSP48E1.v`, commit
`1c8e05fd1e9a79ceb8b996a0996674122eed086f`, hash-pinned in
[unisim/](dsp-dpreg-evidence/unisim/)):

| field | 00/000 | 01/001 | 10/010 | 11/011 | 100 | 101 | 11x |
|---|---|---|---|---|---|---|---|
| X = OPMODE[1:0] | 0 | M | **P** | A:B | | | |
| Y = OPMODE[3:2] | 0 | 0 | ones-mask (not P) | C | | | |
| Z = OPMODE[6:4] | 0 | PCIN | **P** | C | **P** | PCIN>>17 | **P>>17** |

P is an operand only for X=10 or Z ∈ {010, 100, 110, 111}.

## 3. The 13 cells, and their mapped structure

All 13 are two structural groups; within each group every instance is
identically mapped except for which net Vivado routed onto the two dynamic
OPMODE pins.

### Group A — `u_synth/u_voice/osc2_path/p{0,1,2}/pair/dec/prod0__0` (DPREG-4 #1–#3)

The 31-tap symmetric decimator's product stage. The RTL
(`decimate_2x_tm_sym.v`) computes `acc += h[tap]·(x[tap]+x[30−tap])` per tap,
plus the center tap. Vivado split that across three DSP48E1s per decimator:

| cell (p0; p1/p2 identical) | mapping | flagged |
|---|---|---|
| `dec/prod0` | pre-adder computes the pair sum (`USE_DPORT=1`), OPMODE **constant** `0000101` (P=M), PREG=0 | no |
| `dec/prod0__0` | center/odd-tap product + summing stage, **dynamic OPMODE**, PREG=0 | **YES** |
| `dec/acc0` | accumulator: OPMODE **constant** `0010011` (P = PCIN + A:B), PREG=0, **AREG=1/BREG=1** | no |

`prod0__0`'s extracted wiring (full pin dump in the evidence set):

| OPMODE pin | driver | |
|---|---|---|
| [6] | GND | const 0 |
| [5], [4] | **one net**: `prod1` = LUT5 `prod0__0_i_1`, INIT `32'h00007FFF` = NAND(`tap_reg[4:0]`) = (tap ≠ 31) | dynamic, paired |
| [3] | GND | const 0 |
| [2], [0] | **one net**: `prod0__0_i_2_n_0` = LUT5 `prod0__0_i_2`, INIT `32'hEAAAAAAA` = `tap_reg[0]` | dynamic, paired |
| [1] | GND | const 0 |

**Reachable OPMODE set** (worst case over the dynamic nets' values; the RTL
FSM only ever reaches tap ∈ 0..15, so `prod1`=1 and the LUTs' tap=31 branch
is dead, but the verdict below does not need that):

```
{ 0000000, 0000101, 0110000, 0110101 }
```

Decode against the mux tables: X ∈ {00→0, 01→M}; Y ∈ {00→0, 01→0} (always
zero); **Z ∈ {000→0, 011→C}**. Bits [5] and [4] are the *same net*, so their
joint value is 00 or 11 — the P-selecting Z encodings (010/100/11x) are
**structurally impossible**, whatever drives the net. Bit [1] is a constant
0, so X never reaches 10. Bit [3] is a constant 0, so Y never reaches 10.

The C operand when Z=011 is `prod0.P` — the paired-tap product of a
feed-forward DSP whose own OPMODE is constant `0000101` (P=M, no feedback).
This is Vivado time-multiplexing two product streams into one cascade.

**The registered feedback that does exist** (and why the design is sound):
`acc0` holds the accumulator in its **A:B input registers** (AREG=1, BREG=1,
enabled by the accumulate strobe, cleared by the reset LUT), fed from its own
P output through fabric routing — an identity 48-bit map
(`A[i]←P[i+18]`, `B[j]←P[j]`, asserted on all 48 bits during extraction).
The loop acc0.P → A:B → post-adder → P passes through a register inside the
DSP; nothing is asynchronous. `acc0`'s OPMODE is constant and it is not
flagged.

**Disposition #1–#3: dismissed.** P unreachable; the only feedback path is
registered inside `acc0`.

### Group B — `u_synth/u_voice/voice_rate_converter/p_1_out{,__0..__8}` (DPREG-4 #4–#13)

The `VOICE_FILTER_2X` decimation filter (`rate_conv_2x.v`): symmetric
polyphase products with Q2.30 taps; half the tap table is identically zero.
Vivado mapped ten leaf product DSPs whose sums feed the fabric
round-and-clamp; their C ports carry routed **constants** (Vivado folded the
rounding offset algebraically into the DSP C input), their A ports carry the
tap constants, and their B ports carry the `y0hist`/`y1hist` history
registers (B reset = `rst_n` LUT6; B clock-enable = `decim_valid`).

| OPMODE pin | driver | |
|---|---|---|
| [6] | GND | const 0 |
| [5], [4] | **one net** — a single routed bit, different per cell | dynamic, paired |
| [3] | GND | const 0 |
| [2] | VCC | const 1 |
| [1] | GND | const 0 |
| [0] | VCC | const 1 |

The scavenged bit per instance (extraction, pins section):

| instance | net on OPMODE[5]=[4] |
|---|---|
| `p_1_out` | `y1hist_reg[1][0]/Q` |
| `p_1_out__0` | `y1hist_reg[2][0]/Q` |
| `p_1_out__1` | `y_out23[0]` |
| `p_1_out__2` | `y_out22[0]` |
| `p_1_out__3` | `y_out21[0]` |
| `p_1_out__4` | `y_out11[0]` |
| `p_1_out__5` | `y_out10[0]` |
| `p_1_out__6` | `y_out9[0]` |
| `p_1_out__7` | `y1hist_reg[17][0]/Q` |
| `p_1_out__8` | `y1hist_reg[18][0]/Q` |

**Reachable OPMODE set** (per instance):

```
{ 0000101, 0110101 }
```

Decode: X = 01 → M (constant); Y ∈ {00, 01} → 0 (constant zero, both
encodings); **Z ∈ {000→0, 011→C}** — the dynamic bit only selects between
two routed constants: zero, or the cell's C constant. The P-selecting
encodings 010/100/11x require OPMODE[5]≠OPMODE[4], which the single-net
routing makes structurally impossible; X=10 and Y=10 are excluded by
constant bits [1]=0 and [3]=0.

This is also the clearest view of *why* Vivado warns: a data-dependent net
sits on pins where a hypothetical 010 would mean P, and no DRC checker
symbolically proves a routed net's value range. The netlist itself is safe
by construction.

**Disposition #4–#13: dismissed.** P unreachable; PREG=0 is correct as
mapped.

## 4. Reachability argument, in one place

1. **Structural (strongest, netlist-level):** in all 13 cells the two
   dynamic OPMODE pins that would form the Z-mux field's low bits are driven
   by the **same net**. Joint values {00, 11}; encoding 10 never occurs.
   The remaining Z bit [6] is GND in every cell, and X's bit [1] and Y's
   bit [3] are GND in every cell, excluding X=10 and Y=10 outright.
2. **Value-range (conservative):** every distinct dynamic net ranges over
   {0,1} (each is a register Q or a LUT of register outputs), so the
   reachable sets above are a **superset** of what the RTL can drive. The
   RTL-level argument narrows them further — e.g. `prod1` = (tap ≠ 31) is
   constantly 1 because the decimator's tap counter is bounded at 15 — but
   the verdict does not depend on that.
3. **RTL state machines:** the drivers' reachability was checked against the
   FSMs (`decimate_2x_tm_sym.v` busy/tap; `osc_substep_pair.v`; `voice_dp.v`
   reset and `decim_valid` gating), including reset assertion/release and
   mode transitions. No state sequence produces an OPMODE outside the sets
   above. (Tool: `tools/dsp_dpreg_analyse.py`, output
   `dsp-opmode-analysis.json`; verdict line: *all 13 cells: P-feedback
   unreachable on every reachable OPMODE*.)

## 5. Directed mapped-DSP comparison (red-first)

### 5.1 Apparatus

Two DSP48E1 primitives parameterized **exactly as extracted** from the routed
checkpoint (D2 = the `prod0__0` archetype: AREG=2/ACASCREG=2, BREG=0/
BCASCREG=0, MREG=0, PREG=0, OPMODEREG=0, USE_DPORT=FALSE, B = 18'd14712 =
h[15]; D1 = the `acc0` archetype: AREG=1/BREG=1, OPMODE `0010011`, PREG=0),
wired as the netlist is wired (D2's PCOUT → D1's PCIN cascade; D1's A:B fed
from its own P through the identity map; D2's C carrying `prod0.P` as routed
stimulus), driven by the control sequences the RTL can issue: tap 0..15 with
idle holds at 15 (31 is unreachable), ±full-scale rails (32767/−32768),
small/large/random signed data, wide C-port rails, the reset window, reset
asserted **mid-stream** on D1 (accumulate reset) and on D2 (the RTL's
`rst_n` falls on a RESET register write, any cycle), and every OPMODE/CE/RST
transition those sequences produce. Golden: an independently written Python
model of the DSP48E1 arithmetic (25×18 signed multiply, pipeline registers
with the same enables/resets, post-adder mux semantics per the vendor
tables).

Instruments: `tools/dsp_dpreg_sim.py` (generator + runner + classifier),
`tools/dsp_dpreg_analyse.py` (netlist parser). Evidence: `dsim/`.

### 5.2 Red runs BEFORE the clean run (verification-rules rule 1)

The controls ran first, in a single recorded run order ("controls (1, 2)
before clean (0)" in `dsim/verdict.json`), and each turned the harness red
with a recorded mismatch count:

| control | injected defect | class | mismatch count |
|---|---|---|---|
| `pregflip` (mode 1) | D2 PREG flipped 0→1 (the warning's own remedy, applied naively — note the extracted CEP=0 means the P register then never loads) | wrong value | **142 of 148 cycles**, first at cycle 6 (first accumulate) |
| `opmodep` (mode 2) | OPMODE[6:4] forced to 010 (Z=P) on every tap==7 cycle — exactly the unsupported feedback DPREG-4 guards against | wrong value | **130 of 148 cycles**, first at cycle 12 |

Class separation (rule: X is a distinct outcome from wrong value): the
classifier counts X-cycles and wrong-value-cycles separately; the clean run
must show 0 of both. On the controls: `pregflip` is pure wrong-value; the
would-be combinational P→Z→P loop of `opmodep` does **not** oscillate to X in
the vendor's behavioral model — the model resolves it to wrong values (in
silicon with PREG=0 it would be a combinational loop, i.e. an unanalyzable
path, which is precisely what the DRC exists to prevent). The control's
evidence stands on its mismatch count either way, and the class actually
observed is recorded rather than asserted.

### 5.3 Clean sequence (mode 0 — the extracted, routed configuration)

**PASS: 148/148 cycles bit-exact against the independent Python golden —
0 X, 0 wrong-value, on both the product stage (D2.P) and the accumulator
(D1.P).** The mapped DSPs, under the reachable control sequences, compute
exactly the multiply-accumulate arithmetic a UG479 reading of their
parameterization predicts — in particular, **no P-feedback term appears**,
which is the behavioral counterpart of the structural argument in §4.

### 5.4 Simulator provenance and limitations (labelled, per failure-modes §6)

The brief's primary simulator (xsim on the box) was lost to the box rebuild
(§1). The authorized alternative was used: **Icarus Verilog 13.0** with the
vendor's own public unisim Verilog model, hash-pinned:
`DSP48E1.v` SHA-256 `a98661443ef3a3b41a64d1d26d20326ebc4f229a84e96192c02759e6b9f8b7b5`
(from `Xilinx/XilinxUnisimLibrary` at commit
`1c8e05fd1e9a79ceb8b996a0996674122eed086f`, "Version 2016.1" of the model),
plus a one-pulse GSR stub (`dsim/glbl_stub.v`, hashed in the run manifest)
so pipeline registers start at 0 as the golden assumes.

Limitation, stated plainly: this is the 2016.1 unisim model, not the copy
Vivado 2025.1 ships internally. It is still an **external** referent with
respect to the golden — the Python model was written independently from the
architectural specification, and the two agree bit-exactly across 148 cycles
of stimulus including rails, resets and every reachable OPMODE. A vendor
model subtly wrong in exactly the P-feedback semantics under test, agreeing
with an independently derived golden everywhere else, is not a plausible
failure mode of this comparison; and the primary disposition argument (§3/§4)
is structural, from the routed netlist, and does not depend on the simulator
at all.

### 5.5 Wrong-then-right accounting

Published where the numbers are read, per the house rule. Ten instrument
errors were made and caught this session; none reached a conclusion:

| # | error | caught by |
|---|---|---|
| 1 | extractor ran Vivado with `-nolog`, suppressing the log artifact | manifest step refused on the missing file |
| 2 | `REF_PIN_NAME == P` never matches indexed bus pins — empty P fanout | clean-room check of the dump against expectation (P *must* be loaded) |
| 3 | same filter bug left the OPMODE cones unextracted | noticed while reading the cone sections |
| 4 | pin-name regex rejected `CEA1`/`CEB2` (digits) | parser crash |
| 5 | raw-string `\\` gave bash a literal backslash, not a continuation | runner rc≠0 |
| 6 | tb used a nonexistent `RSTCARRYIN` port (it is `RSTALLCARRYIN`) | iverilog elaboration error |
| 7 | `BCASCREG` left at default vs extracted `BREG=0` | vendor model's own attribute check |
| 8 | `USE_DPORT(0)` — model wants the string `"FALSE"` | vendor model's own attribute check |
| 9 | `-g2012` fails generate-case with string parameters (see §7) | minimal repro (`dsim/micro.v`); `-g2005` correct |
| 10 | D1 OPMODE typed `0001011` instead of the extracted `0010011` (Y-mux=ones instead of zero) | **the clean-run comparison itself** — 147 mismatches against golden, root-caused, fixed, re-run |
| 11 | my closed-form hand-checks of the golden were wrong three times (pipeline index slips) while the golden was right | the unisim clean run replaced them as the external anchor |

Items 7, 8 and 10 are the important ones: the vendor model refused to lie
about its configuration, and the comparison caught a one-bit spec error in
my own apparatus. That is the harness working.

## 6. Why no latency is added for DPIP-1/DPOP-1/DPOP-2

The build also reports 64 DPIP-1 (input pipelining), 97 DPOP-1 (PREG output
pipelining) and 94 DPOP-2 (MREG output pipelining) warnings. None of them
indicates incorrect behavior, and **no pipeline stage is added**:

1. **There is no timing need.** Published `timing.rpt`: setup worst slack
   **+46.498 ns**, TNS 0.000, **0 failing endpoints** of 38,304; hold
   +0.050 ns, 0 failing; clock `hardware_clock.clock_raw` period
   **81.380 ns** (12.288 MHz). The setup margin is 57 % of the period. The
   warnings are performance/power advisories ("will improve performance",
   "is suggested"), not correctness findings, and their premise — timing
   pressure — does not hold at 12.288 MHz on this device.
2. **Latency is not a free optimization here; it is a contract change.** The
   voice datapath's cycle schedule is part of the verified behavior: the
   published digital verification (SPI-to-I2S wrapper vs the Python model,
   4,821 I2S periods, bit-exact) and the block benches are tied to this
   schedule. Adding A/B/M/P pipeline stages to the mapped DSPs inserts
   cycles into the oscillator decimators and the filter rate converter,
   changing when samples appear on the I2S wire — a different
   implementation, requiring re-verification end to end.
3. **It would invalidate the published baseline.** `publication.json` binds
   `arty.bit` to the source SHA-256 set, the verification record and the
   reports. A rebuild with different DSP parameters produces a different
   bitstream whose warnings, timing and verification must all be re-taken;
   the published artifact this review protects would be retired without any
   correctness gain.
4. **Decision ownership.** Power savings from MREG (DPOP-2) may be worth
   taking in a future build — at 12.288 MHz static-ish DSP power is not the
   binding constraint — but that is a rebuild policy decision for the
   coordinator, informed by this document, not a change to smuggle into a
   warning-cleanup.

The same reasoning bounds this disposition itself: DPREG-4 was the one
warning class that could have implied a **correctness** defect in the
shipped image. It is dismissed on netlist evidence, so the published
baseline stands as-is.

## 7. Upstream-filing candidates

Per the house rule — a workaround or tool defect is a deliverable:

1. **Icarus Verilog 13.0 (v13_0): `generate case` over a string parameter
   elaborates no items under `-g2012`.** Minimal repro committed at
   `dsp-dpreg-evidence/dsim/micro.v`: a `case (B_INPUT)` with string
   literals selects no branch, the mux output stays X ("No generate items
   found" debug note); the identical source elaborates correctly under
   `-g2005`. Workaround here: compile with `-g2005`. Candidate upstream:
   steveicarus/iverilog.
2. **Vivado 2025.1 DPREG-4 message could state the checker's bounds.** All
   13 warnings quote the same paragraph for two structurally different
   situations (a genuinely unconstrained dynamic OPMODE vs a same-net-paired
   2-bit field that cannot encode P). If the checker emitted the reachable
   OPMODE encodings it assumed possible (or flagged *which* mux field
   worried it per cell), triage would be mechanical. Not a defect — a
   sharpening suggestion for the DRC message.
3. **Remote box root-volume replacement mid-session** (Vivado 2025.1
   install lost; `/home` preserved). Coordinator/loom matter: the box needs
   `/tools/Xilinx/2025.1` reinstalled before any Arty build work, and any
   long-running evidence run should pull + hash artifacts before ending a
   turn (which is what made this session survivable).

## 8. Artifact index and hashes

| artifact | SHA-256 |
|---|---|
| routed.dcp (asserted on box before open) | `fdb3b45d7d7b108bf3246a5013f7ac6171f126956a4d0fcc2af1a20e499945fe` |
| box `drc.rpt` | `1c06b86f770da9e1fea2b70de8a04a77193dfc78def994a10eb9de77dc9c981f` |
| `dsp-dpreg-evidence/dsp_cells_dump.txt` | `3a8f8b83d124f1be09c229134e6cdfd8e6eaceeb05e0d1b9f39579597832208d` |
| `dsp-dpreg-evidence/vivado_extract.log` | `8232de8e7bded6db1edcbfcb3534aa14c298c2f39e23bd58873162c36ed328e4` |
| `dsp-dpreg-evidence/drc_dpreg_names.txt` | `862a1a8c8af883db7bbe3d8ffdd2fcbd022210a4d4282e4f440f39a566ebaf57` |
| `dsp-dpreg-evidence/unisim/DSP48E1.v` | `a98661443ef3a3b41a64d1d26d20326ebc4f229a84e96192c02759e6b9f8b7b5` |
| `dsp-dpreg-evidence/dsim/verdict.json` | (committed; counts in §5) |
| RTL sources | identical to `report.json` `source_sha256` (§1) |

Instruments (this branch): `tools/dsp_dpreg_extract.py`,
`tools/dsp_dpreg_analyse.py`, `tools/dsp_dpreg_sim.py`.
Vivado: `vivado v2025.1 (64-bit)` SW Build 6140274. Simulator: Icarus
Verilog 13.0 (v13_0), `-g2005`.
