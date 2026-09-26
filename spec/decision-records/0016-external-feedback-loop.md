# 0016: The external feedback loop — a digital return, not an input pin

- **Status**: proposed
- **Date**: 2026-09-26
- **Decided by**: block agent, from the instrument survey below and the signal
  chain fixed in DR 0005 and DR 0001/0006

## Context

Issue #49 (original text): "the current Model D normals its output back
through the external-input path. For our engine, specify the feedback tap,
gain, delay and saturation independently of ladder resonance — they interact
but they are not the same control." DR 0005 already surveys the vintage
"feedback trick" as historical context (`0005-gain-structure-headroom-and-
the-vca.md:45-50`) but never turns it into a decision for this chip: no tap
point, no gain, no delay, no saturation register exists anywhere in
`spec/NUMERIC-CONTRACT.md`, `model/voice_fx.py`, or `rtl-sketch/voice_dp.v`.
This record makes that decision. It is documentation only — see
"Consequences" for what is explicitly deferred.

## What comparable instruments do

- **Minimoog (original, hand-patched).** "Many players connected the
  Minimoog's unused audio output (either High or Low, as appropriate) to its
  external signal input, thus creating a feedback loop whose gain was
  affected by both the output volume and the external signal input volume.
  The results could range from nothing (the latter set to zero) through mild
  overdrive, to complete screaming, uncontrollable mayhem." — verified live
  this session, <https://www.soundonsound.com/reviews/moog-minimoog-model-d>.
  The tap is the **finished audio output** (post-VCA, post-volume — whatever
  leaves the output jack); the return point is the **external-input mixer
  channel**, i.e. pre-filter (the Minimoog's mixer feeds the ladder; docs
  `docs/minimoog-reference.md:572-574`, "the fifth mixer source"). There is
  no separate delay or saturation control — the cable itself and the
  external-input preamp are the whole "circuit."
- **Minimoog reissue (2016/2022).** Same review, same page, verified live
  this session: "the new Minimoog contains an internal signal path that
  replicates this when nothing is inserted into the external signal input."
  One control (the external-input level knob) substitutes for the patch
  cable; the tap/return points are unchanged. (DR 0005's paraphrase of this
  same fact, "the Main Output signal is sent back to the input of the
  mixer," was not re-locatable verbatim on this page this session — it may
  be from the reissue's own manual, which returned 404 when re-fetched here.
  Treated as DR 0005's finding, not independently re-verified in this
  record.)
- **Arturia MiniBrute 2, "Brute Factor."** Verified live this session by
  fetching and extracting text from
  <https://dl.arturia.net/products/minibrute-2/manual/minibrute-2_Manual_1_0_EN.pdf>:
  the front-matter feature list describes it as "overdrive the filter input
  with the audio output"; §5.5.1 states it in full —
  "a special MiniBrute 2 feature inspired by a common patch used on a famous
  vintage mono-synthesizer that connected the headphone output to the
  external audio input. The result is a kind of feedback loop that's ideal
  for raspy and grungy sounds. This patch has been implemented internally
  … and is controlled by the Brute Factor knob," off at the fully
  counter-clockwise default, "low Brute Factor settings the distortion is
  smooth and gentle, but it becomes more harsh as you turn up the knob …
  higher than about 75% … barely controllable, crazy feedback sounds," with
  the caveat "drastically alters the filter characteristics." This is the
  same tap/return topology as the Minimoog (output → filter input) collapsed
  into **one knob** that sweeps gain and, implicitly, drive together — no
  separate delay or saturation control is documented.
- **Not surveyed further.** No claim is made here about the Matriarch,
  Grandmother, Sub 37 or any other instrument having (or lacking) this
  specific output-into-input trick; DR 0003/0005 already cite them for
  unrelated policies and none of those citations mention it. Extending the
  survey was not needed to answer this issue's question, which is about tap
  point and parameterisation, not about which synths do it.

**Every source found describes the same tap and return point** — the
finished audio output, fed back before the filter — and **no source found
gives a delay figure**, because in every case above the "delay" is a patch
cable or analog trace: not zero, but not a designed, specified parameter
either. **Not stated in any source found**: a numeric gain range, a delay
time, or a distinct saturation curve for the feedback path as opposed to
whatever nonlinearity the mixer/filter already have. That absence is itself
informative for the decision below: the vintage trick supplies overdrive by
re-using the mixer's own overload, not a bespoke soft clipper.

## Decision

### Tap point (normative)

The feedback tap is the voice's **VCA output, `v`** (contract §9), taken
**before** the output-stage volume multiply and the output rail (contract
§12: `vol`/`dvol`/`bvol`, `sat16`). This is the "post-VCA output, matching
the vintage patch point" option, not "pre-VCA/post-filter" (the ladder's
`y_out`, contract §11.4). Two reasons, both grounded in what already exists
in this contract:

1. **Every source surveyed patches the *finished* output**, not an internal
   filter node — the Minimoog's patch cable runs from the output jack, the
   reissue and the MiniBrute 2 replicate exactly that path internally. No
   source describes tapping the filter's output directly ahead of the
   amplitude envelope.
2. **Tapping post-VCA ties the feedback to the note's own gate.** `v` goes
   to zero when the envelope releases (DR 0003's mechanism: the release
   branch runs the level to zero from wherever it is). Tapping pre-VCA
   (`y_out`) instead would inject signal into the mixer for as long as the
   filter is ringing, independent of any envelope — compounding, rather than
   using, the already-accepted self-oscillating-filter-tail behaviour of DR
   0005 ("a self-oscillating patch now sings across a rest … inaudible
   because the VCA is closed"). A second, ungated energy source at that same
   point removes the one thing (DR 0005's VCA-after-filter decision) that
   currently guarantees a note ends.

`v` is scoped **before** `vol`/`dvol`/`bvol` specifically so the feedback
gain register is independent of the host's chosen output level — a host
that turns down `vol` for the room should not also silently starve the
feedback loop, and a host that raises `vol` for a loud room should not also
silently intensify it.

### Return point (normative)

The feedback signal re-enters as an additional term in the mixer's
accumulator (contract §7, `acc = osc_0·w_0 + osc_1·w_1 + osc_2·w_2 +
noise·WN`), summed **before** the mixer's existing `sat16` clamp — i.e.
exactly where a fifth physical source would land if this chip had one. It is
named and registered as a **feedback tap**, not as "the external input":
`docs/minimoog-reference.md:59` and `spec/NUMERIC-CONTRACT.md` §7 both
already record that this chip's mixer has four sources, "the external input
being the one it does not have" — that omission is a physical-input
statement (see below) and stays true. The new term is a digital loop from
this chip's own output stage back to its own mixer input; it does not
create, and must not be described as creating, a fifth *external* source.

### Parameters (normative, distinct from ladder resonance)

| parameter | role | distinct from resonance because |
|---|---|---|
| **feedback gain** (`fb_gain`, a new Q0.15-style unsigned register, mirroring `w_k`'s shape in §7) | scales the delayed `v` sample before it is summed into the mixer accumulator | `res`/`k` (DR 0006's `K_ROM32`) scale the ladder's own internal state once per filter stage, every frame, always active in the sense that the loop exists even at `res = 0`; `fb_gain` scales a *whole-voice* signal (all three oscillators plus noise, post-filter, post-VCA) and is zero at reset (§14 pattern: every optional gain register is silent until the host writes it), so the two controls move different, independently-observable things — resonance sharpens a peak in the filter's own response; feedback overloads the mixer with the note's own sound |
| **feedback delay** (`fb_delay`, a register selecting how many frames the tapped `v` is held before it re-enters the mixer; **minimum one frame is mandatory**, not a musical choice) | breaks the combinational loop `v(f)` depends on `mixed(f)` depends on `v(f)` that a same-frame return would create | the ladder's own feedback (DR 0001, DR 0006) is already a *sub-frame* delay handled inside the sequenced pipeline (Huovilainen's explicit, non-iterative solver, chosen in DR 0001 exactly so the per-frame cycle budget is fixed); this is a *frame-granular* delay register sitting entirely outside the filter, and is why a chip must specify a delay where a patch cable did not need one — an analog path has no register to reset and no fixed-latency budget to protect |
| **feedback saturation**: **no new nonlinearity register.** The existing mixer `sat16` (contract §7, already the site DR 0005 names as "the hard rail of the Q1.15 word … a host that pushes them past 1.0 gets a hard mixer overload, which the contract states rather than softens") is the feedback path's saturation, reached the same way any other over-hot mixer input reaches it. | — | the ladder's `tanh` (DR 0001 §11.3, `TANH16`) is a *soft*, per-stage nonlinearity that shapes the resonant peak and self-oscillation onset (DR 0006); the mixer's `sat16` is a *hard* rail with no shaping. Routing feedback into the mixer, not into the ladder directly, means "overloading the feedback loop" and "raising resonance" produce audibly different characters by construction — it is the same *mechanism* DR 0005 already names for the Minimoog's stock mixer overdrive ("the Mixer can overload, introducing varying levels of overdrive or distortion," the Model D manual, DR 0005), just driven by the note's own signal fed back rather than by the mixer's own input levels, which is exactly how the survey above (Sound on Sound: "mild overdrive … to complete screaming, uncontrollable mayhem") describes the difference |

Adding a fourth saturation site (beyond the three DR 0005 names — oscillator
`sat16`, mixer `sat16`, ladder `tanh` — plus the drum path's own, DR 0008)
was considered and rejected below; reusing the mixer's existing hard rail
keeps DR 0005's "no other nonlinearity in the path" claim true in spirit
(one new *signal path* into an existing clamp, not a new clamp).

### Digital internal return vs. physical external input (normative)

**A digital internal return is feasible with the current architecture and is
what this record specifies above:** `fb_gain`, `fb_delay`, and the new
mixer-accumulator term are pure register/logic additions to a digital
datapath that already computes `v` and already sums four terms into `acc`
every frame. No new pin, no ADC, no analog front end is needed.

**A physical external-audio input is not feasible with the current
architecture, and is an explicit non-goal for this chip generation:**

- `docs/minimoog-reference.md:572-574` states it directly under "What this
  chip does NOT have": "The external input / microphone preamp, the fifth
  mixer source … with its overload lamp (§2.12). No audio input pin."
  (Re-verified this session: lines 572–574 read exactly this in the current
  tree.)
- The I2S path is transmit-only. `rtl-sketch/i2s_tx.v` (re-verified this
  session, lines 24–32) has no audio-input port at all — its ports are
  `clk`, `rst_n`, `cyc`, `sample_valid`, `sample` in, and `bclk`, `lrclk`,
  `sdata` out; there is no `sdin`. `docs/ARCHITECTURE.md`'s pin table
  (re-verified this session, lines 356–373, specifically 366–369) lists
  `BCLK`, `LRCLK`, `SDATA` all as `out`; the module table entry at line 78
  calls `i2s_tx` "the contract's section 13 transmitter" — there is no
  receiver anywhere in the design. An I2S DAC on the far end of this link
  does not imply a codec with capture, and none is assumed.
- Building a physical external input would need, at minimum: an analog
  input pad, an ADC (or a 1-bit comparator plus decimation, mirroring the
  `DSD_OUT` idea already flagged reserved-and-unimplemented in
  `docs/ARCHITECTURE.md` line 369), and a receive-capable path into the
  mixer's sample timing — none of which exists today and none of which this
  record proposes.

**Recommendation: implement the digital internal return next; do not build a
physical external input for this chip generation.** The digital return
already reproduces the sound the survey describes (mixer overload driven by
the voice's own signal) without new silicon I/O; the physical input would
require new pads and an analog front end to recreate a feature whose whole
appeal, on every instrument surveyed, was economy (no separate feedback
oscillator or noise source — just the synth's own output, patched or wired
back).

## Alternatives considered

- **Tap pre-VCA / post-filter (`y_out`) instead of post-VCA (`v`).** Rejected
  above: no surveyed instrument patches this point, and it removes the
  guarantee (DR 0005) that a note ends, by adding a second energy source
  that ignores the gate.
- **Name the return term "external input" and reuse the vintage naming.**
  Rejected: `docs/minimoog-reference.md:572-574` and
  `spec/NUMERIC-CONTRACT.md` §7 already use "external input" to mean the
  *physical* fifth mixer source this chip explicitly does not have; reusing
  that name for a digital-only loop would contradict the chip's own stated
  omission and mislead a reader into thinking an input pin exists.
- **Zero-delay feedback (same-frame return).** Rejected: creates a
  combinational dependency cycle within one frame's fixed-latency pipeline,
  the exact class of problem DR 0001 designed around by choosing an
  explicit, non-iterative filter solver over an iterative one. At least one
  frame of register delay is mandatory, not optional.
- **A dedicated soft-saturation (tanh) stage for the feedback path,
  independent of the mixer's hard rail.** Considered, because a hard digital
  clip is a different character from the vintage circuit's likely soft
  overload (transistor mixer stages rarely clip as hard as a digital rail).
  Rejected for this record: no source found specifies what that curve should
  be (the survey found only "mild overdrive … to complete screaming," not a
  transfer function), and adding a fourth saturation site contradicts DR
  0005's stated policy of concentrating designed saturation at named points
  rather than distributing ad hoc clips through the signal chain. If a
  future measurement shows the hard rail is unmusical here, that is a
  separate, evidence-driven decision record, not a default assumption in
  this one.
- **Build the physical external input now, since the issue names it.**
  Rejected on the grounds above: no audio input pin exists, I2S is
  transmit-only, and reproducing the surveyed feature does not require new
  I/O — the digital return delivers the same effect for free.
- **Route feedback through the drum section's mix bus (DR 0008) instead of
  the voice mixer.** Not considered further: every source surveyed is a
  monosynth's own voice feeding its own mixer; the drum section is a
  separate instrument sharing only the output stage (contract §12), and
  nothing in the survey or the issue asks for drum-bus feedback.

## Consequences

- **No RTL or model code changes are made by this record.** It is a decision
  record only, per the issue's explicit scope. Implementing `fb_gain`,
  `fb_delay`, the new mixer-accumulator term, the reset-state entry (§14,
  pattern: zero, silent until written), and the corresponding tests in
  `model/test_voice_fx.py` (mirroring `test_mixer_saturates_instead_of_
  wrapping` and the DR 0003 gate/release tests, since the feedback level
  must be shown to decay to zero on release) is follow-on scope, to be filed
  as a separate issue once this record is ratified.
- Once implemented, `spec/NUMERIC-CONTRACT.md` §7 (mixer) gains a fifth
  accumulator term and two new registers, and §14 (reset) gains two more
  zero-reset entries; `rtl-sketch/voice_dp.v` gains a small delay-line
  register and one more multiply-accumulate on the already-shared
  multiplier (the same kind of addition DR 0005 made for the VCA multiply).
  Cycle-budget impact against the 256-cycle frame (DR 0001) is expected to
  be small but is not measured here — that measurement belongs to the
  follow-on implementation issue.
- The contract's distinction between "resonance" and "feedback" becomes
  citable rather than implicit: a future preset format, test, or bug report
  that says "the resonance is doing something odd" now has a named
  alternative control (`fb_gain`/`fb_delay`) to rule in or out, cross-linked
  here to DR 0001 (§11, the ladder model) and DR 0006 (§10.2, the
  resonance-compensation ROM) precisely so the boundary is concrete.
- Whether the reference host (`model/voice_fx.py`'s `KeyHost`, DR 0003)
  exposes `fb_gain`/`fb_delay` as a front-panel-style control at all is left
  open — the Minimoog reissue and the MiniBrute 2 both ship it as a single
  hidden or semi-hidden knob, not a headline control, and this record does
  not require the reference host to do more than register-level support.
- A physical external-audio input remains explicitly out of scope for this
  chip generation; a future chip generation that adds an ADC and a
  receive-capable I2S/PDM front end would need its own decision record,
  not an extension of this one — the tap/return/gain/delay decisions above
  are specific to a digital-only loop and do not presuppose captured audio.
