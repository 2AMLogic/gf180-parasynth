# The pedalboard-hosted reference rig

Issue #124. A second plugin host beside `dawdreamer`, because **which host works
differs per plugin and that was measured, not assumed.** Same machine, same
binaries, same note:

| | `pedalboard` | `dawdreamer` 0.9.0 |
|---|---|---|
| **Moog Model D** | peak 1.000000 — **sounds** | peak 0.0 — **silent** |
| **Arturia Mini V3** | peak 0.000000 — **silent** | sounds |

The rig layer therefore supports both and records which one produced a clip
(#123). What this document holds: the dependency, what the pedalboard rig
qualifies on, the environment tuple every clip carries, and **the current
verdict, which is a stated no-verdict.**

## Status: `qualified: None`, and that is not `false`

`tools/refprofile.py`'s `RIG_VERDICTS["modeld-pedalboard"]` carries
`qualified: None`. No operator has run the rig on a machine with the Model D
bundle yet, so there is no verdict either way.

**`None` is not `False`.** "Nobody has run it" and "it cannot be used" are
different facts, and writing either one as the other is what #123 was about.
`refprofile.qualified_rigs()` will not pick up a `None`, and nothing may read
one as a rejection — including `--list`, whose renderer printed one as **NO**
until review caught it.
<!-- claim: test=tools/test_qualify_modeld_pedalboard.py::test_an_unrun_rig_is_None_and_not_False -->
<!-- claim: test=tools/test_qualify_modeld_pedalboard.py::test_the_list_renderer_does_not_print_a_None_verdict_as_a_rejection -->

The command that turns it into a verdict is:

```sh
python tools/qualify_modeld_pedalboard.py --json docs/pedalboard-rig-run.json
```

```
exit 0  OK       the rig qualified; the clip may be frozen and #124's 8 Mono
                 anchor cases are unblocked
exit 1  FAIL     the rig was measured and is not usable
exit 2  REFUSED  a precondition is unmet, so nothing was attempted: no
                 pedalboard, no Model D bundle, or a check that could not answer
```

On every Linux host in this fleet it exits **2** with
`no pedalboard on this machine` — a stated no-verdict, and the correct answer.
That refusal deliberately does **not** print the #122 note below: a host that
never loaded the plugin has concluded nothing about it.
<!-- claim: test=tools/test_qualify_modeld_pedalboard.py::test_no_pedalboard_is_REFUSED_and_says_nothing_about_issue_122 -->

## The dependency

`pedalboard` is declared where `dawdreamer` is — the reference-rig install line
in [`discrimination.md` §8](discrimination.md):

```sh
.venv/bin/pip install dawdreamer pedalboard
```

It was **not** installed into any shared venv by the work that added this rig.
Neither host is in any CI workflow and neither needs to be: everything that
depends on them refuses with a stated reason without them, and every test in
this rig's suites runs with neither (see "How this is tested without a plugin"
below).

| host | what needs it | what it cannot do |
|---|---|---|
| `dawdreamer` | Surge, Diva, Mini V3; every clip in `refprofile/`; the swept-cutoff movement study — it is the only host here with parameter automation | Model D (silent) |
| `pedalboard` | Model D | Mini V3 (silent); any swept-cutoff measurement (no automation) |

`ModelDPedalboardRig.swept_cutoff` raises `NotImplementedError` rather than
stitching a ramp out of per-block renders, which would measure the stitching.

## A sibling, not a fork

`reference_rigs._PedalboardPlugin` **subclasses** `_Plugin` and overrides
exactly the three methods that touch the host — `__init__`, `render`,
`silence_state` — plus `qualify`, which is a superset. Everything else is
inherited as the same function object, asserted in
`model/test_modeld_pedalboard_rig.py`:

| inherited verbatim | why it matters |
|---|---|
| `set`, `text` | one way to address a parameter |
| `apply_pins`, `pinned_report`, `check_pins` | **one** implementation of the name-and-readback rule — the rule that caught Surge renaming index 265 from "Unison Voices" to "High Cut" by oscillator type |
| `check_names` | one implementation of the index-map assertion |

`pedalboard` addresses parameters by sanitised Python name and this repository's
pin discipline is keyed by index, so `_PedalboardParams` adapts one to the
other. It asserts two preconditions before any pin is read: indices must be
**unique** (two parameters at index 32 makes every pin there a coin toss) and
must cover **0..n-1 with no gaps** (a host that hides parameters renumbers the
rest, and a pin table measured under the other host would then be pinning
different controls with no error anywhere).

`ModelDPedalboardRig` takes `ModelDRig`'s index map, parameter names and
`setup()` **by reference**, not by copy.

Two genuine host differences, both properties of the reference and not of the code:

- **State does not carry between renders.** `pedalboard`'s `process` takes
  `reset=True`, so each render starts clean. Under `dawdreamer` the engine
  carries voice and filter state forward, which is why `SurgeRig.select_osc` has
  to settle with no note — calling `render` there stacks note-ons.
- **There is no parameter automation.** So the 94 Hz artefact cannot be
  manufactured under this host, and neither can the movement study.

## What the rig qualifies on

`model/rig_qualification.py` is the **host-agnostic** half of qualification: it
asserts the rig's *output*, where `_Plugin.qualify()` asserts its *controls*.
Three outcomes, kept apart on purpose — `PASS`, `FAIL`, and `REFUSED` for a
check that could not answer at all. `Qualification.verdict` is `qualified` only
when every check passes, and a refusal outranks a failure because "we measured
this and it is wrong" and "we could not measure this" are different claims.

| check | what it asserts | the number |
|---|---|---|
| `pins` | every pinned setting holds by NAME and by readback | `_Plugin.check_pins`, lifted not reimplemented |
| `pins written (raw readback)` | every write took effect, and the plugin's two readback routes agree | raw within half a step for a discrete control; `string_value` == `get_text_for_raw_value(held)` |
| `sounding` | there is a signal, and it is finite | peak > 1e-9 |
| `level` | usable, and not at the rail | peak in [0.05, 0.99], **zero** samples at full scale |
| `pitch` | the note **commanded**, measured | `refine_f0`, within 50 cents, with its own sub-harmonic look-below |
| `waveform` | identified from the record at the **measured** f0 | `waveform_id`; must be nameable, need not be a particular name |
| `pitch causality` (#137) | +12 semitones **causes** a doubling | measured ratio within 2 % of 2.000 |
| `filter causality` (#137) | the cutoff knob moves the spectrum, monotonically | power-weighted centroid ratio ≥ **1.25**, no step back by > 2 % |

### Why the checks are not redundant

Each pair below looks like it covers the other and does not. The
`DEFECTS` matrix in `model/test_rig_qualification.py` asserts, for every
injected defect, **both which checks fire and which do not**.

- **pitch vs. pitch causality.** A rig an octave down at every note doubles
  *correctly* on +12, so the causality check PASSES it (measured ratio 2.000);
  only `check_pitch` refuses it. A rig whose pitch command does nothing plays
  the right note at the base, so `check_pitch` PASSES it; only the causality
  check refuses it. Dropping either loses a real defect.
  <!-- claim: test=model/test_rig_qualification.py::test_the_octave_down_row_does_not_also_fail_the_causality_check -->
  <!-- claim: test=model/test_rig_qualification.py::test_the_dead_pitch_command_row_does_not_also_fail_the_pitch_check -->
- **level vs. waveform.** At **8.57 %** of samples at the rail — the figure on
  record for this plugin — the clip is still named a saw, still plays the
  commanded note, still transposes and still sweeps. The level check is the only
  thing between it and the profile. The waveform check starts to see clipping on
  its own only between **21.5 %** and **40.8 %** at the rail, swept rather than
  argued about.
  <!-- claim: test=model/test_rig_qualification.py::test_the_clipped_row_is_caught_by_the_level_check_and_by_nothing_else -->
- **any signal check vs. the pins.** Surge's Phaser is a chain of allpasses: at
  its default mix it moves phase and leaves every harmonic amplitude where it
  was. No signal check can refuse it; the pin readback can.

### Two gates that were wrong before they were right

Published here because a reader calibrates every other number in this file
against the rate at which they were wrong.

1. **`CENTROID_MIN_RATIO` started at 2.0 and was unsatisfiable by its own
   validation case.** A one-pole swept over a **15.9×** range of corners, driven
   by a fixed band-limited source, moves its power-weighted centroid by only
   **1.913** (saw) / **1.767** (25 % pulse) — a saw's power falls as 1/k² so the
   centroid sits near the fundamental. An unsatisfiable gate is worse than no
   gate. It is now **1.25**, between the measured 1.767 and the **exactly
   1.000** a disconnected knob gives, and both bounds are asserted.
   *Power*-weighted, not amplitude: the amplitude centroid is 2.846 on the same
   sweep — more sensitive, and the estimator `sound_report --inject
   sd-centroid-amp-weighted` reinstates as a defect.
   <!-- claim: test=model/test_rig_qualification.py::test_the_centroid_gate_sits_between_the_two_states_it_separates -->
2. **The waveform check was handed the wrong fundamental.** `refine_f0` reports
   `f0_measured` as the strongest component *near* the command, which on an
   octave-down record is that record's **second harmonic** sitting exactly on
   the commanded note. The octave-down matrix row was green for the wrong
   reason — a 173.6 % period residual instead of the octave. It reads
   `subharmonic` first now. Caught by the matrix, not by inspection.

## The silence gate, against the prior art (#125)

Four different numbers answering four different questions. They do **not**
agree, and the divergence is deliberate:

| floor | where | the question it answers |
|---|---|---|
| `1e-6` | `~/dev/generate-random-dexed-sounds/`, `max_val_05 < 1e-6` — **quoted from #125, not read** (see below) | *corpus filter*: is this draw worth keeping? A cheap reject-and-redraw, for which a floor three orders above true silence is exactly right |
| `1e-9` | `rig_qualification.SILENCE_FLOOR` (`audio_measure.is_silent`'s default) | *is there anything at all?* The only question a floor can answer without knowing the level the rig was set to. At 1e-9 a refusal means the host returned zeros — which is precisely the Model-D-under-dawdreamer finding, peak exactly 0.0 |
| `1e-6` | `refprofile.LEVEL_FLOOR`, at **both** ends of the freeze: the render loop that writes a clip and `load_clip` that reads it back (#481) | *is this frozen clip worth keeping at all?* The prior art's question, asked of a clip instead of a draw — so it takes the prior art's number. See below |
| `−12.04 dBFS` | `refprofile.ESTIMATOR_FLOORS["probe level"]` | *at what input level is OUR fixed-point ladder inside its measured stability window?* A property of the thing being compared **against** the reference. Not a silence gate at all |

For **`rig_qualification`**, the prior art's floor is the right answer to its
question and the wrong answer to this one: `1e-7` is not silent and is not
usable, and collapsing those into one threshold is how a level defect gets
reported as an absence of signal.
`test_the_silence_floor_is_the_one_this_module_documents` asserts exactly that —
a signal between the two floors is SOUNDING and FAILS the level check.

For **`refprofile`**, it is the other way round, and #481 is where that was
found. `load_clip` carried the `1e-9` floor and so refused only a buffer of
zeros: a clip peaking at `1e-7` hashed correctly, was not silent, was 140 dB
below the probe level every consumer reads it at, and was unusable — every
number measured off it would have been the path's own truncation noise wearing
the profile's provenance block. That is the corpus filter's question, not the
apparatus's, so it now takes the corpus filter's number. This is the one place
in the comparison where the prior art's floor is the better of the two.

Three things about that change worth having in one place:

- **Both floors moved, to the same constant.** A freeze-time floor *below* the
  read-time one would let `--render` write, hash and commit a clip at, say,
  `5e-7` — a render that reports success and is refused by every consumer
  forever afterwards. `test_the_freeze_and_read_floors_are_the_same_number`
  holds them together.
- **It is a flat floor and not `check_level`'s window**, although a level
  window is the richer check. `PEAK_MIN` is `0.05` and the committed profile's
  quietest clip, `surge-type2/drive-100hz-cut250-res0.50-in-12dbfs`, peaks at
  `0.0357` **by design** — it is the clip driven at −12 dBFS. Gating the
  profile on that window would refuse a clip the profile exists to hold: an
  unsatisfiable gate, which is worse than no gate.
- **The refusal still distinguishes the two causes even though the gate does
  not.** Under `1e-6` it reports a level defect; under `1e-9` as well it adds
  that the clip is silent and not merely quiet. One threshold, two sentences.

All sixteen committed clips clear `1e-6` by four orders of magnitude or more
(`0.0357` is the minimum); `tools/refprofile.py` verifies 16/16 against the
restored cache under the new floor, and
`test_every_committed_clip_clears_the_level_floor` keeps that true for the next
clip frozen here.

### The four prior-art scripts were NOT read, and that is an open item

#124's acceptance criteria ask that
`~/dev/generate-random-dexed-sounds/dawdreamer_synths.py`,
`playdawdreamer-synth.py`, `dawdreamer-find-all-synth-parameters.py` and
`~/dev/vst3synthpresets/modeld.py` be read before the rig is written.
**They could not be: `~/dev` does not exist on the Linux dispatch worker this
rig was built on** (`ls ~/dev` → `No such file or directory`), and neither does
any plugin bundle. Saying otherwise would be the failure mode this whole
document is written against, so:

| claim | status |
|---|---|
| the `1e-6` corpus gate above | **quoted from issue #125**, which states it as `max_val_05 < 1e-6`. The two numbers it is compared against are read out of this repository, so only the prior-art column is second-hand |
| `load_plugin` → `_parameters` → `.index` as the route to index-addressable parameters, and the `(midi_bytes, timestamp)` message form (so no `mido` dependency) | **from `pedalboard`'s public API**, not from the prior art. Neither is verified against an installed `pedalboard` either — see the caveat below |
| anything else the four scripts may settle | **unknown.** Not read |

**And the host API itself is unverified on this machine.** `pedalboard` is not
installed here, so `_PedalboardParams` and `_PedalboardPlugin.render` are
written against `pedalboard`'s documented interface and exercised against a
*fake* of it (below). The first run of `tools/qualify_modeld_pedalboard.py` on
the operator's machine is what tests that shape, and an `AttributeError` there
is a finding about this adapter, not about the plugin. Reading the four scripts
on that machine is the cheapest way to shorten that step, and it is still
worth doing: they are a *working* pedalboard corpus generator, which is
evidence about the API that a docstring is not.

## The two corrections, each measured

The default patch fails two checks, and both are on record. The rig tries to
correct each through Model D's own parameters and **measures whether it worked**:

| | what it does | why not a lookup |
|---|---|---|
| `calibrate_range` | sweeps Osc 1 Range across a 25-point grid, measures the fundamental at each position with `refine_f0`, selects the position closest to the commanded note | Mini V3's 8' is 0.575; a value that works on a different vendor's emulation of the same panel is a guess. The octave-down position puts its second harmonic exactly on the commanded note, so a sweep that accepted "there is energy at 261.63 Hz" would select it |
| `trim_level` | steps master volume down a 13-point grid, loudest first, and takes the **loudest** setting with zero samples at the rail | loudest-that-is-clean, not quietest-that-is-safe: a reference frozen 20 dB down carries 20 dB less of what it is a reference for, and the quantisation floor of the fixed-point side it is compared against does not move |

Both record `raw_held` — what the plugin **holds** after the write, not what was
written to it. Osc 1 Range is discrete and snaps, so a 25-point grid visits six
positions and a table of commanded values would name nineteen the plugin was
never in. Both also **refuse** if re-applying the selection lands anywhere other
than where the measurement was taken: qualifying one state and shipping another
is the failure the whole battery exists to prevent.

The selection rule is stated before the sweep runs — among the positions inside
50 cents, the one closest to the commanded note — because a rule chosen after
seeing the table is not a rule.

### If either correction fails, that is the complete answer

Issue #124 gates its own scope on one clip. **If the octave default or the
clipping cannot be corrected through the plugin's own parameters, the rig
REFUSES**, the sweep tables are the evidence, and
`tools/qualify_modeld_pedalboard.py` prints:

> Model D under pedalboard cannot be the Mono cases' reference, and the
> re-specification question in issue #122 (Route 1 / Route 2 / Route 3)
> REOPENS.

It does not keep an octave-down clipped Model D as the Mono reference in order
to report more scope completed, and it will not write a `--wav` from a rig that
did not qualify: a clip from an unqualified rig is not evidence, and putting one
on disk is how it later gets used as though it were.

## The environment tuple every clip carries (#123)

One recorder, `refprofile.environment_tuple`, used by **both** hosts — the
dawdreamer render path goes through it too. A second recorder would have been a
second thing to keep in step, which is how "the reference" comes to mean two
things in one file.

| field | source | refuses when |
|---|---|---|
| `host` | `"dawdreamer"` / `"pedalboard"` | absent |
| `host_version` | the module's `__version__` | absent |
| `plugin` | `plugin_identity()` — path, `CFBundleShortVersionString`, **binary sha256** | the bundle is absent, or has no binary hash. The version is what the bundle *claims*; the hash is what it *is*, and a vendor who ships a fix without bumping the version moves only the second |
| `block` + `block_rate_hz` | the rig's pinned block, and `sr/block` computed | either is non-numeric or ≤ 0 |
| `sr` | the rig's pinned rate | as above |
| `licence` | the rig's own `licence` dict: `state` **and how it was established** | absent |
| `preset` | `preset_identity()` — sha256 of `preset_data` where the host exposes it | absent |
| `automates_a_parameter` | whether any clip in the run automates | — |

**`block` and `sr` may not be `"unstated"`, and every other field may.** The
caller handed those two to the host, so not knowing them is not a gap in the
evidence — it is a caller that did not pin them. That is the 94 Hz artefact
waiting to happen: three unrelated plugins once appeared to step identically at
94 Hz because that was `dawdreamer`'s automation rate at its 512-sample default,
and a 16-sample block **reversed the conclusion**. `pedalboard`'s own default
buffer is **8192** — a 5.86 Hz chunk rate, straight through the middle of an
envelope measurement — so the rig pins 512 and the record spells the rate out.

**Licence state is `unverified`, and that is measured rather than lazy.** There
is no licence probe for any plugin in this repository. The one licence finding we
have — an unlicensed Diva inserting clicks,
[`reference-integrity.md`](reference-integrity.md) §1 — was found by *hearing*
the clicks. So the rig states `unverified` together with how that position was
reached, and `environment_tuple` refuses a tuple that leaves the field out.

### The pin readback column is deliberately empty

`ModelDPedalboardRig.PINS` carries `ModelDRig.PINS`' indices and expected names
with every expected readback **string** dropped to `None`. A readback string is
the host's rendering of the plugin's answer; `ModelDRig.PINS` pins `'0.00'`
because that is what `dawdreamer` returned when somebody measured it. Copying
those strings across hosts would assert a measurement nobody took here, and a
formatting difference would be refused as a parameter drift.

What replaces it is not weaker. `written_pin_problems` asserts, for every pin,
that the raw value came back where it was written (within half a step for a
discrete control — the largest error a correct snap can produce) **and** that
the plugin's two readback routes agree with each other: `string_value` is what
it says it holds now, `get_text_for_raw_value(held)` is what it says that value
means. A plugin whose text lags its processor disagrees between them — precisely
the Diva cutoff that read 90 for a whole session — and that is a refusal here,
not a footnote.

The observed strings go into the record's `pinned_readback`, so the first
qualifying run on an operator's machine is what fills the column in — by
measurement.

## How this is tested without a plugin

| file | what runs | what is faked |
|---|---|---|
| `model/test_rig_qualification.py` | every check, and the whole `DEFECTS` discrimination matrix through the shipping `qualify_voice` | nothing — closed-form signals only: band-limited waveforms from their Fourier series, a one-pole at known corners, counted clipping |
| `model/test_modeld_pedalboard_rig.py` | the shipping rig: `setup`, `voice_patch`, `calibrate_range`, `trim_level`, `written_pin_problems`, `qualify`, `render` | the **host** — a `pedalboard` module whose `load_plugin` returns a parameter table and a synthesiser the test can put into any state |
| `tools/test_qualify_modeld_pedalboard.py` | the shipping tool: exit codes, the record, the environment tuple, the verdict table | the same host |

The fake's **default** state is the defect on record — peak 1.000, 8.57 % at the
rail, 130.81 Hz for a commanded MIDI 60 — and it is asserted to be, before
anything is claimed about the rig. A test whose apparatus does not reproduce the
defect proves nothing about the fix. **This is the apparatus being *set* to the
condition on record, not a second measurement of the plugin:** the fake's output
gain is solved so its default patch lands on 8.57 %. What it establishes is that
the rig corrects *that* condition, which is the only thing a test without the
plugin can establish. The fake asserts the sample
rate and the block size on **every** call, so a rig that left either to the host
would fail every case in those files.

Both gate branches are covered: the rig correcting both defects and qualifying,
and the rig refusing with its sweep table as evidence when no Range position
sounds the note or no master position clears the rail.
<!-- claim: test=model/test_modeld_pedalboard_rig.py::test_the_default_patch_really_is_the_defect_on_record -->
<!-- claim: test=model/test_modeld_pedalboard_rig.py::test_the_rig_corrects_both_defects_and_qualifies -->
<!-- claim: test=model/test_modeld_pedalboard_rig.py::test_an_uncorrectable_octave_refuses_and_the_sweep_is_the_evidence -->
<!-- claim: test=model/test_modeld_pedalboard_rig.py::test_uncorrectable_clipping_refuses_and_the_sweep_is_the_evidence -->

## What is still not answered

- **No verdict for the real plugin.** Everything above is the instrument and its
  controls. `qualified: None` stands until somebody runs
  `tools/qualify_modeld_pedalboard.py` on a machine with the bundle.
- **The `pedalboard` API shape is unverified against an installed
  `pedalboard`**, and neither were the four prior-art scripts read — `~/dev` and
  the plugin bundles are both on the operator's machine, and this was built on a
  Linux dispatch worker. See "The four prior-art scripts were NOT read" above for
  exactly which claims are second-hand and which are not.
- **The 8 Mono family anchor cases are not built.** #124 gates them on the one
  clip qualifying, and it has not been run. `docs/scorecard/` is untouched.
- **The readback column.** Empty by design until one qualifying run measures it.
- **The wave-selector mapping.** `EXPECT_WAVE` is `None`: the battery requires
  the record to be *identifiable* and does not require a particular label,
  because no Model D wave mapping has been measured on any host. Asserting one
  would be the `SurgeRig` defect again — it asked for a saw, received a 50 %
  pulse, and published it for a whole study.
- **Per-capability verdicts (#136).** `RIG_VERDICTS` is still one verdict per
  (rig, host). "Mini V3's cutoff is answerable, its envelope timing is not" is
  really two verdicts about one rig. Nothing here forecloses that change: the
  entries are keyed by a string and carry a `host` field, so a `capability`
  field is additive.
- **`profile.json` and `tools/run_case.py` still carry the unscoped wording**
  ("renders exact silence headlessly", no host named). Both are hashed inputs,
  so correcting them there is #129 and #101's re-run and not this change's.
