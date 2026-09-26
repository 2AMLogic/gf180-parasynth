# Release: Arty A7-100T baseline 2025.1, r1

**Status: CANDIDATE — digital evidence only. No physical programming, control
or audio capture has been performed on this image.**

The machine-readable manifest is [`baseline-2025.1.json`](baseline-2025.1.json).
Every identity in it is derived from the tree by
[`release_manifest.py`](release_manifest.py). Check it before using it:

```sh
.venv/bin/python fpga/release/release_manifest.py      # BOUND (0) / STALE (1) / REFUSED (2)
```

`BOUND` means the committed manifest equals a fresh derivation and every
selected artifact agrees with every other. That covers the bitstream, the
publication, `report.json`, the DSP evidence's `routed.dcp` digest, the tree's
RTL and ROM bytes, the evidence runs' source identities, and the CLI's current
bytes. `STALE` means something that ships has changed; bind it again
deliberately (`--write`) or cut a new release. `REFUSED` means the artifacts
disagree, so no release can be bound.

## What is released

| | |
|---|---|
| Configuration | `OSC2X=1 FILTER2X=1 PULSE2X=0` |
| Image | [`integrated-baseline-2025.1/arty.bit`](../reports/arty/integrated-baseline-2025.1/arty.bit), 3,825,912 bytes, SHA-256 `a66c9349…4cb95` |
| Routed checkpoint | `routed.dcp` SHA-256 `6c3c22c5…1fbf8`: the same digest in publication.json, report.json and the DSP DPREG-4 disposition |
| Sources and ROMs | the 25 `source_sha256` entries of the publication. The manifest's check re-hashes each one in the tree, and they are equal at binding |
| Timing and fit | internal timing pass (WNS +16.19 ns, WHS +0.024 ns, 0 failing); external I/O qualified with the one `i2s_bclk` exception |
| DSP review | complete: all 13 DPREG-4 cells, P-feedback unreachable on every reachable OPMODE |
| Host | [`fpga/uart_host.py`](../uart_host.py): USB-UART at 115200 8N1; packets W/E/Q/X; DR 0007 rev 2 register frames. No numeric protocol version exists, so the manifest pins the exact bytes of every supported command instead |
| Rollback | [`vivado-2025.1/arty.bit`](../reports/arty/vivado-2025.1/arty.bit) `1a562b42…`. This is a known-built fallback, not a qualified release: its control path is SPI only (`uart_host.py` cannot drive it) and its DPREG-4 review is incomplete |

## Playback commands (the supported set)

```sh
.venv/bin/python fpga/uart_host.py --port /dev/cu.usbserial-XXXX run --note 45 --fixture none
.venv/bin/python fpga/uart_host.py --port /dev/cu.usbserial-XXXX run --preset m5a-saw --note 72 --fixture none
.venv/bin/python fpga/uart_host.py --port /dev/cu.usbserial-XXXX run --preset m5a-pulse --note 72 --fixture none
.venv/bin/python fpga/uart_host.py --port /dev/cu.usbserial-XXXX play --fixture m5a
.venv/bin/python fpga/uart_host.py --port /dev/cu.usbserial-XXXX run --fixture demo
.venv/bin/python fpga/uart_host.py --port /dev/cu.usbserial-XXXX run --fixture bar808-full
```

| Preset | Audible waveforms | Transposition | Playable MIDI (enforced) |
|---|---|---|---|
| default (also bar808, demo) | saw, saw, square | 0, +0.07, −12 | **12..126** |
| m5a-saw | saw on osc 0 only | 0, 0, 0 | 0..127 |
| m5a-pulse | pulse29 (47.9 %) on osc 0 only | 0, 0, 0 | 0..127 |

The only routing is oscillator 3 control (`mroute` 4). The mod wheel is 0, so
modulation does not move pitch. No live controller path exists in this
release. Knob movement comes only from the fixed fixtures: cutoff 340–1500 Hz,
resonance 0.5–0.95 and drum decay 3–8.5, with the default patch and no
calibration.

## The supported domain, and how it is enforced

**The note number is not the bound.** `VoiceFx.note_incs` applies each
oscillator's transposition and then clamps only to the 24-bit register
(~48 kHz). `KeyHost.writes` does not clamp at all, so the register keeps the
low 24 bits, which is a *different* pitch. MIDI 127 an octave up is 25.1 kHz,
above the 24 kHz Nyquist increment 2^23. That is where #247's glide mismatch
lives.

So [`qualified_domain.py`](qualified_domain.py) checks the **final
programmed register writes** in the order the device applies them.
`uart_host.py main()` calls it on every player-facing command before a byte
is sent:

- **Every programmed increment must lie in [2858, 4384395]**:
  `phase_inc(note_hz(0))` to `phase_inc(note_hz(127))`, or 8.18 Hz to
  12,543.85 Hz. No oscillator is programmed outside MIDI 0–127's pitch span
  after its transposition. `INC_HI` is 0.94 octave below 2^23.
- **A glide is admitted only from a known in-range increment to an in-range
  increment.** The slew is monotone between its endpoints (`OscFx.slew`,
  `voice_dp.v` `S_SL2`), so every increment it passes through is in range. A
  glide from reset (increment 0) or from an unknown device state is refused.
- **#247 as filed is refused under its own rule** (`GLIDE_247`, any
  endpoint ≥ 2^23), even though the range rule already excludes it.
- **#247 as measured is refused too** (`ROUTE_DRUMFILTER`: ROUTE = 1 sends
  the voice through the drum filter). No player-facing path writes ROUTE, so
  it stays at its reset value 0. See below.
- **Modulation is checked separately** (`MOD_EXCURSION`). Oscillator pitch
  modulation moves the effective increment every frame without the glide
  slew. Its worst-case excursion must keep the effective increment in range.
  This check does not claim that #247 is a modulation defect; #247 reproduced
  with the same mismatch count whether oscillator modulation was on or off.
- The audible waveform set must be one of the three above. The clean
  calibration `surge-type2-clean-v1` is refused at nonzero resonance. PULSE2X
  is refused. `--preset` combined with a fixture is refused, because the
  fixture loads its own patch.
- **Nothing is clamped.** A write outside the domain exits 2, naming the
  write, the pitch, the rule and the patch's playable range.
- **Engineering interface.** `uart_host.py --engineering`, `spi_host.py` and
  the benches remain available, and they are **outside** the qualified
  player-facing domain. The CLI says so on stderr.

**Precondition that is declared but not verified:** fixture runs never write
the modulation or ROUTE registers, and the host cannot read them back. The
validator therefore assumes their reset value (0) on fixture runs. Reset the board with
BTN0 after any engineering-interface session.

### RTL evidence for the boundary

[`glide_boundary.py`](glide_boundary.py) runs the admitted boundary
transitions and the excluded ones against the RTL.
[`evidence/glide-boundary/summary.json`](evidence/glide-boundary/summary.json)
records every run's exit status and whether it met its expectation. The cases
are:

- LO → HI in one glide at 0.02 s/oct;
- HI → LO at the maximum rate;
- a semitone into HI at the preset rate;
- the glide = 1 floor at HI and at LO;
- a retarget mid-glide.

GLIDE_BOUNDARY_RESULTS

## Runtime qualification

| Evidence | State | What it establishes |
|---|---|---|
| Three-saw production deadline, **#248** (merged `d089c67`; [docs/deadline/README.md](../../docs/deadline/README.md), bound by digest) | **landed** | In the tested runs the baseline met every production frame deadline: 0 missed, 0 overrun. The worst observed slack was 14 cycles under musical stress and 6 cycles in extreme-register runs. Every musical-range I2S comparison was exact and complete. The cost model is reconciled: C = 87 across 30,333 frames, 0 overlap exceptions. Incomplete evidence now returns NO VERDICT. **Not established:** a formal whole-register-space bound (the per-term bound is "not a formal proof"), and model agreement in the extreme runs (#247) |
| Rolling playback RTL, #210 (merged) | bound | The manifest's check regenerates the CLI's `demo` and `bar808-full` transmit logs and confirms they are byte-identical to the replayed captures, whose RTL sources equal the image's |
| Held note, [`evidence/held-note/`](evidence/held-note) | bound | Each release preset's held-note command, as the CLI emits it now, replayed through the Arty wrapper: bit-exact I2S, and **audible** (decoded I2S peak ≥ 1024 LSB) |
| Wrapper digital proof ([uart-clean](../reports/arty/uart-clean)) | bound by digest in the publication | 7 scenarios and 5 controls at the pins |
| Build / timing / publication / DSP | bound | see "What is released" |

## #247 is not a glide defect, as far as its own stimulus shows

#247 was filed as a mismatch in "glides between increments ≥ 2^23". Its
stimulus (`verify_deadline.py` `_extreme`) also routes the voice through the
drum filter (`route = 1`, `dcut = 600`) and strikes the whole kit under it.
[`probe_247.py`](probe_247.py) imports that bench unchanged and varies one
ingredient at a time
([`evidence/probe-247/run_all.json`](evidence/probe-247/run_all.json), exit
status per run):

| Variant | I2S periods differing |
|---|---:|
| #247 verbatim | 1809 |
| ROUTE = 0 (voice through its own ladder), strikes kept | **0** |
| No strikes, ROUTE = 1 kept | **0** |
| Every glide write replaced by a jump | 1809 |
| Increments scaled into the release range (top = INC_HI), ROUTE = 1 | 1809 |
| The same, ROUTE = 0 | **0** |

In that stimulus the mismatch needs the drum-filter route and the strikes. It
does not need a glide or an increment above Nyquist. The release therefore
refuses ROUTE = 1 as well as the filed domain.

The trigger inside the drum-filter path is **not isolated**. The deadline
README's `stress-saw` also toggles the drum filter with strikes, and it
matched exactly, so "route 1 plus strikes" is not sufficient by itself.

## A defect this manifest found: the documented first playback was silent

`run --note 45 --fixture none` was the first playback command in
`fpga/ARTY.md`. `voice_image_writes` never wrote the mixer weights, and every
weight resets to 0. The note was therefore **exact silence**. The UART bench's
`held` scenario was bit-exact against a model that was equally silent, and its
release-tail check (peak ≤ 1024 LSB) passes on silence.

The validator refused this stream because the audible waveform set was empty.
The replay of the pre-fix bytes (`evidence/held-note/default-legacy`) is RTL
bit-exact with a **decoded I2S peak of 0**. After the fix the peak is 12,760
LSB (default), 7,345 LSB (m5a-saw) and 10,376 LSB (m5a-pulse).

The fix is `uart_host.voice_mixer_writes`. It writes the weights and the
noise/modulation registers, and only when no fixture image follows. The
demo, bar808 and bar808-full byte streams are unchanged, which the manifest
verifies. `note_writes` now uses the preset's own transposition; before the
fix, `--preset m5a-saw --note N` detuned oscillators 2 and 3 like the default
patch.

## Exclusions

| Excluded | Why | Enforced by |
|---|---|---|
| `PULSE2X=1` | Not in this image. #248 records missed deadlines at extreme increments; it needs its own correction and a rebuilt image (#205) | the image; `check_patch(pulse2x=True)` |
| #247 as filed (a glide with an endpoint ≥ 2^23) | Exact model/RTL mismatch; open defect | `INC_RANGE`, `GLIDE_247` |
| #247 as measured (voice through the drum filter, ROUTE = 1) | The mismatch needs this route in #247's stimulus (below) | `ROUTE_DRUMFILTER` |
| Resonance with `surge-type2-clean-v1` | Qualified at resonance 0 only | `CALIBRATION_RESONANCE` |
| `--preset` with a fixture | The fixture's patch plays under the preset's name | CLI refusal |
| Standalone `note-on` | The device image is unknown to the command | `WAVES`; use `run` |

## What could not be bound

- **The m5a phrase.** Its RTL evidence (the uart-clean `phrase` scenario) is
  built by the bench from the same `phrase_events('m5a')` function. It is not a
  replay of the CLI's captured bytes.
- **The voice-level glide runs.** `verify_voice` records no run identity. They
  ran on the tree whose sources the manifest verifies equal to the image's;
  the wrapper-level runs carry digests.
- **Modulation and ROUTE registers on fixture runs.** They are assumed at
  reset.
- **A numeric protocol version.** None exists.
- **Physical audio.** No capture exists. The next step is plan076 §6: silence,
  held note and release, drum hits, the fixture and a repeat capture, taken
  on a MOTU M4 at fixed gain with the raw captures kept.

## Wrong-then-right, this session

| First reading | Caught by |
|---|---|
| The accept bench looked adequate. Its GLIDE_FLOOR injection was **not caught**, because at HI the floor never engages. A glide = 1 case at LO was added. | the injected-bug control |
| The voice-level probe and control runs gave **no verdict**: the component bench launches at cycle 48 and was busy at the end of frame 0. They moved to the shipped wrapper path. | exit status 2 |
| The first waveform rule refused the m5a phrase: it counted silent oscillators. The rule now uses audible sets. | the fixture streams |
| The #247 control at the wrapper (increments, glide shape and patch as #247, but **no drum route or strikes**) was **not caught**, because it matched the model. Varying #247's own stimulus then showed that the route, not the glide, carries the mismatch. The run is now recorded as a probe, not used as a control. | the control's exit status |
| The first wrapper-level captures wrote the default image, not the case's (its glide rate, noise and modulation). The captures were fixed and every wrapper case re-run. | reading the capture code after the control failed |
