# refprofile — the frozen reference side

Reference audio rendered **once** through a qualified rig, cached, hashed, and
described in a file that is committed.

**The profile and frozen audio archive are committed.** `profile.json` holds the
hashes, the plugin's identity, every parameter the rig set, the rig's own
qualification verdict and the commit it was built at. The WAVs live in
`cache/`, which is gitignored. `frozen-cache.zip` contains the exact sixteen
previously frozen WAVs, recovered from the earlier verification worktree.
Restoration checks every file's size and SHA-256 against `profile.json` before
writing any clip. It needs no plugin and does not change the reference.

```sh
python tools/refprofile_restore.py      # restore the committed exact audio
python tools/refprofile.py              # verify the cache against the profile
python tools/refprofile.py --list       # what it holds, and which rigs it rejects
python tools/refprofile.py --render     # re-render it — an explicit act, a visible diff
python tools/refprofile_repro.py        # render it N times and compare the bytes
```

## Why a profile and not a render on demand

A reference that is re-rendered on demand is not a reference. It moves when the
plugin updates, when the host block rate changes, when a preset drifts — and it
moves *silently*, because the number coming out looks exactly the same. Every
comparison made against it before the move becomes a comparison against
something nobody can reconstruct.

So `refprofile.load_clip` **never renders**. A cache that is absent, short, at
the wrong rate, silent, or whose bytes do not hash to what `profile.json` says
is a stated refusal, and the case that wanted it is a no-verdict with the
reason on its record. Re-rendering is `--render` and nothing else, so it always
produces a diff that somebody reviews.

The audio exists because a plugin on an operator's machine produced it;
`profile.json` records that capture. The archive makes those frozen bytes
available to clean checkouts. Fresh plugin renders remain an explicit,
separate operation.

## Three outcomes, kept apart

| exit | | |
|---|---|---|
| 0 | **OK** | every clip is on disk and hashes correctly |
| 1 | **FAIL** | a clip is there and is *not* what the profile describes |
| 2 | **REFUSED** | a precondition is unmet, so nothing was attempted |

**REFUSED is not a failure of the profile and must never be read as one.** Most
hosts in this fleet have neither the plugins nor `dawdreamer`; they can restore
and use the frozen WAVs. Missing or corrupt archives and clips still refuse.

## What is in it, and what is not

One rig qualifies. Three do not, and the entries that say **no** are the
load-bearing ones — "we did not use Model D" and "Model D cannot be used" are
different facts and only the second one tells the next person not to try.

| rig | | why |
|---|---|---|
| **Surge XT 1.2.3** Type 2 | ✅ | open source; its LP Vintage Ladder subtype Type 2 is `sst-filters`' `VintageLadder::Huov` — Huovilainen's DAFx-04 model, the same paper DR 0001 implements. The **only** reference here whose cutoff is commanded in Hz and reads back in Hz |
| **Moog Model D** | ❌ **under dawdreamer only** | **renders exact silence under `dawdreamer` 0.9.0.** Measured, not inherited: peak 0.0 with oscillator 1 on at full level and the filter wide open, and peak 0.0 with the filter self-oscillating. The rig builds and its pins hold. **It is NOT silent under `pedalboard`** — peak 1.000, 8.57 % of samples at the rail, strongest partial 131.00 Hz for MIDI 60 (an octave down, the same default as Mini V3, which is itself the exact reverse: it sounds under dawdreamer and is silent under pedalboard). Per #123 this is a property of the host, not of the plugin — **re-derive per host before inheriting this verdict.** `profile.json` and `tools/run_case.py` still carry the unscoped wording; both are hashed inputs, so correcting them there is #129 and #101's re-run |
| **Arturia Mini V3** | ❌ | makes sound, and every parameter is a bare 0..1 with no units and no readback. Its cutoff can be calibrated against its own self-oscillation (`reference_compare.calibrate_knob`); its **envelope** knobs cannot, because nothing here maps a Mini V3 envelope knob to a time. Its Range control also defaults an octave down — note 48 reads 65.42 Hz until parameter 45 is written |
| **u-he Diva** | ❌ | found running unlicensed and inserting clicks (`docs/reference-integrity.md` §1), and is a general analogue-modelling synth rather than a Minimoog emulation |

Sixteen clips, all from Surge XT Type 2 at 48 kHz:

- one **wide open** (20 kHz) stepped-tone render — the passband reference that
  makes "low-band gain" a ratio *inside one instrument*
- ten at **250 Hz**, at resonance 0 and at each rung of
  `reference_compare.RES_GRID["surge"]`
- three **drive** clips: a steady 100 Hz tone at −12, −6 and 0 dBFS
- one each at **1 kHz** and **4 kHz**, resonance zero — the other two cutoff
  regions `docs/scorecard/cases.csv` states, read by F1B and F1C. Surge's own
  readback is exactly 1000.00 and 4000.00 Hz; the *corner* those produce is
  neither number and is read off the audio

Only resonance zero is rendered at the new regions, and that is a limit rather
than an omission. F1B and F1C are the only cases those clips can be read by.
F2B and F2C want a resonance ladder there and are blocked on a matched-drive
**definition**, not on audio — see `run_case.NOT_RUN["F2A"]` — so freezing ten
more rungs per region would cache audio no case can consume and move every
consumer's profile hash to do it. Whoever writes that definition renders the
ladder the definition asks for.

## Frozen, and *reproducibly* frozen — two different claims

A list of hashes says these are the bytes that were frozen. It says nothing
about whether the rig would make them again, and only the second makes the
profile a reference rather than a recording. `tools/refprofile_repro.py` asks
the second question: N separate `--render` **processes**, so the VST3 is
reloaded and the rig re-qualified every time, then a byte-for-byte comparison.

At `daf9e64`, macOS/arm64, Surge XT 1.2.3, dawdreamer 0.9.0, Python 3.14.7:

```
16/16 clips bit-identical across 4 independent renders
14 reproduced the committed sha256, 0 changed, 2 new, 0 dropped
```

The second line is a *different* claim from the first and is the load-bearing
one: a host that renders the same thing four times and something else than the
operator who froze the profile has a reproducible rig and a moved reference.
This host reproduced all fourteen, so the two new clips were frozen by the rig
that froze the rest — not merely by a rig that agrees with itself.

Asked again at `01e01e1`, on a host whose `cache/` was empty, after
`--render` rebuilt it from nothing. The verdict is committed as
[`repro-report.json`](repro-report.json) rather than quoted from a log:

```
16/16 clips bit-identical across 4 independent renders
16 reproduced the committed sha256, 0 changed, 0 new, 0 dropped   (exit 0)
```

Sixteen now, not fourteen of sixteen: the two clips #146 added are on their
second independent freeze and `profile.json` is byte-unchanged by the run.
**This is the precondition the filter cases are measured under, not a property
of the profile in general** — #164 had to leave every reference-side F1 number
as a no-verdict precisely because `cache/` is gitignored and that host could not
render it. A host can now recover the identical bytes from `frozen-cache.zip`;
the existing per-read hash guard still protects F1A/F1B/F1C's reference side.

The restored-cache check verified **16/16 exact clips**. Both previously
blocked controls now execute: missing and tampered references each change all
three valid clean F1 comparisons into refusals. No sound measurement was
revised during restoration (0/16 clip identities changed). Synthetic archive
controls also reject a missing clip, a corrupt clip, and an escaping path
before writing any audio. CI runs these controls in an independent job.

What it does **not** vary: the machine, the OS, the plugin build, the sample
rate, the block size. Those are pinned by the rig and recorded in
`profile.json`, and a claim about them needs a second machine, not a second
run. A clip that does not reproduce is not frozen, and the tool refuses to
install it.

## Pins after rendering: what the frozen profile does and does not show (#233)

**The committed `profile.json` makes no post-render pin claim, although its
`pins_held_after_render` field says `true`.** The renderer that built it
(builder `4bbd8e90…`, at `daf9e64`) checked the pinned Surge parameters once,
*before* any clip was rendered, and wrote that field as a constant. Nothing
re-read the pins after a clip. `profile.json` and the frozen audio are left
byte-unchanged here: rewriting the field would change the historical record,
and re-rendering the profile would add nothing a reproduction does not
already show. `tools/refprofile.py --list` says this about the profile, and
`refprofile.post_render_checked()` returns false for it.

Since #233, `--render` re-reads every pinned name and readback after **each**
clip render, before that clip is written, and refuses on any change. The one
name change it accepts is the Classic→Audio In rename that #231 measured at
**259, 260, 264 and 265**. The readback is still required to match exactly.
`pins_held_after_render` is now derived from those checks. The per-clip
results are recorded in `qualification.post_render_check` and in each clip's
`pins_after_render`.

**The builder hash changed, and nothing is re-pinned to it.** Any profile
rendered from now on records the new `builder_sha256`. The committed profile
still records `4bbd8e90…`. `tools/run_case.py` hashes `tools/refprofile.py` as
a model input, so a run_case result from before this change no longer covers
the tree. That is the intended effect of a hashed input.

At `9f106f7` (clean), same host, Surge XT 1.2.3 and dawdreamer 0.9.0 as above,
the new renderer checked every clip in two independent renders
([`post-233-check/`](post-233-check/)):

```
16/16 clips bit-identical across 2 independent renders
16 reproduced the committed sha256, 0 changed, 0 new, 0 dropped   (exit 0)
pins_held_after_render: true, derived; 16/16 clips checked in each run
name aliases observed on every clip: 259, 260, 264, 265 (readbacks unchanged)
```

The last line re-observes #231 independently. From the first clip onward,
Surge reports all four indices under their Audio In names. So the pre-#233
check, had it re-read the pins by exact name, would have refused every clip.
The frozen audio remains the same audio: the 16 committed hashes reproduce
under the new check. That reproduction is evidence about the *current* rig.
It does not show that the 2026-09-18 capture had a post-render check.
`profile.render1.json` in that folder is a run's output, kept for its
`qualification` and `pins_after_render` records. It is **not** the frozen
profile, and nothing reads clips through it.

## The probe level, and the floor it comes from

Every stepped-tone clip is at **−12.04 dBFS** (`amp = 0.25`), the level
`reference_compare.response_curve` has probed at since #87. That is not a level
chosen after seeing an answer, and it is not arbitrary — it is the only level
inside **our own side's** measured stability window:

| input | our corner at res 0 | our corner at res 1.20 |
|---|---|---|
| −60 dBFS | — | 55.7 Hz *(quantisation noise)* |
| −36 dBFS | — | 282.6 Hz |
| −24 dBFS | 125.2 Hz | — |
| −18 dBFS | 124.3 Hz | — |
| −12 dBFS | 117.8 Hz | 361.2 Hz |
| −6 dBFS | 105.0 Hz | — |

Below about −24 dBFS the stepped-tone probe on our 16-bit ladder is reading
truncation noise: at −60 dBFS `audio_measure.slope_db_oct` refuses every
resonant row and the corner moves by 300 Hz. Above −12 dBFS our own saturation
moves the corner. Surge is *level-independent* over the whole range — its
`thermal = 1/70` input scaling keeps it small-signal — so **the window is ours,
and it is stated as ours.** (Issue #92: a published floor that was not actually
constant withdrew a whole column of #61.)

## Rules

- **Expanded cache files remain ignored.** The one frozen archive is committed;
  new capture audio must have a reviewed profile identity before replacing it.
- **A result that used a clip names it**, by clip id and content hash.
  `tools/run_case.py` writes both into every record's `provenance.inputs`.
- **`profile.json` is generated by `--render`, not edited by hand.** It is also
  one of `tools/run_case.py`'s `DEPENDENCIES`: a batch whose profile differs
  from `origin/main` refuses, because a result measured against a different
  frozen reference is not comparable with one measured against this one.
- **Re-rendering is a decision, and its diff is the review.** If a clip's hash
  changes, something about the reference changed, and the diff says what.
