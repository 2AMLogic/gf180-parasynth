# #379: a perceptual gate for the 808 kit, its proof, and the 16 shipped sounds ranked

`tools/perceptual_gate.py` compares a whole hit, time-resolved, against the Fischer TR-808 take, and takes its pass
bar from the 808 itself. The known answers are in `tools/test_perceptual_gate.py` (23 tests). Every record here was
produced by commit `1e2ab5f` on the build box with a clean tree; `provenance` in each JSON says so.

**Headline.**
- The gate discriminates. The start-red stub fails all 16 sounds, and silence is REFUSED on all 16. Every target reads
  about 0 against itself through the candidate path. The shipped cymbal and the #374 candidate both FAIL on 7 of 8
  features, while coarse measures called the candidate close (H−L 8.64 dB against the 808's 8.16). All 121 seeded
  defects FAIL; 118 fail on the feature they were aimed at.
- **All 16 shipped sounds FAIL.**
- **A second real TR-808 also fails all 16, by similar factors** (§5). So the bar the issue specifies, "as close as the
  nearest other setting of the same machine", is much tighter than the difference between two real 808s. As it stands
  no model can be expected to pass it. The gate ranks and diagnoses; it cannot yet promote anything. The bar needs a
  decision before any fix is judged against it (§6).

## 1. What is compared (frozen once, identical for every signal)

| convention | rule |
|---|---|
| rate | 48 kHz; the 44.1 kHz takes are resampled up |
| bandwidth | a causal 20 Hz high-pass (AC coupling) and a zero-phase 20 kHz low-pass (the audible band, inside the recordings' own 22.05 kHz) |
| alignment | t = 0 is 1 ms before the first sample above 2 % of the signal's own peak. No cross-correlation search |
| level | BS.1770 K-weighted loudness, gated to within 20 dB of the loudest 50 ms block (the rule `tools/ab_808_loud.py` uses for listening packs), set equal |
| span | twice the target's −60 dB span + 50 ms, capped at 3 s, so a candidate that rings on is seen |
| floor | 60 dB under the target's loudest cell, the same on both sides |

| feature | what it reads | not averaged away because |
|---|---|---|
| `spec` | 25 overlapping critical bands, 10 ms hop; specific-loudness L1 over the hit | loudness-weighted cell by cell |
| `spec_peak` | the same on 2.7 ms hops: the worst frame | a max over frames |
| `centroid` | Bark centroid of specific loudness (sharpness weighting), as a trajectory | frame by frame, where both sides sound |
| `flatness` | spectral-flatness trajectory, 100 Hz–16 kHz | frame by frame |
| `impulse` | the worst sample's crest over its 5 ms RMS above 2 kHz, for the strike and for the body | a worst-sample statistic (for clicks) |
| `attack` | per band group (6), when the cumulative energy of the first 100 ms reaches 5/20/50 % | worst band group |
| `decay` | per band group, the Schroeder energy-decay curve while the target is above −40 dB | worst band group |
| `modulation` | per band group, the fluctuation spectrum of the log envelope, 10–640 Hz (beating, roughness) | worst band group |
| `pitch`, `pitch_shape` | pitched sounds (BD, toms, congas, CB, CL): the median offset in cents (tuning), and the worst 20 ms of the trajectory after removing it (slides) | worst 20 ms |

**Verdict.** PASS only if every applicable feature is within its bar. There is no combined score; the rank is by the
worst feature's ratio to its bar.

## 2. The bar

- **Multi-take sounds (BD, SD, CY, LT/MT/HT, LC/MC/HC, OH).** The nearest adjacent knob setting (one step on one knob),
  chosen once by `spec`. Its distance on every feature is the bar.
- **Single-take sounds (CB, CH, CL, CP, MA, RS): WEAK.** The same take played ×1.0628 faster, which moves every
  frequency and every time together. 1.0628 (105.4 cents) is the 808's own median TUNING step, measured by this tool's
  pitch feature on the 12 adjacent tom/conga pairs (95.8–133.2 cents). It is inside §1.7's ±10 % f0 tolerance. It is a
  resampled take, not a second machine, and the tables mark it WEAK.
- **Every bar** is measured through the same path as a candidate (48 kHz, another lead and gain, 16-bit), plus the
  apparatus's own floor (the target against itself through that path).
- **Floors that are perceptual limens, not fits:**
  - attack ≥ 1 ms, half the ~2 ms gap-detection threshold;
  - pitch offset ≥ 5 cents, the mid-frequency pure-tone difference limen. BD's TONE neighbour moves pitch by 0.1 cents,
    and a 0.1-cent bar is unsatisfiable;
  - impulse ≥ 3 dB, because the worst-sample statistic itself wobbles about 1 dB over a noise voice.

## 3. The proof (`prove.json`)

| check | result |
|---|---|
| **start red**: a 1 kHz decaying sine stub as every sound | FAIL 16/16 |
| silence | REFUSED 16/16 |
| **stays green**: the target through the candidate path | PASS 16/16, every feature near 0 |
| the nearest neighbour through the candidate path | PASS 10/10 multi-take sounds (by construction it sits at its own bar; this checks the path) |
| a half-size resample (×1.031) of the single-take sounds | PASS 6/6 |
| the farther neighbours | FAIL, as expected: they are farther than the nearest, which is how strict a nearest-neighbour bar is |
| **must go red**: shipped cymbal (`main`) vs CY5025 | **FAIL** on 7: spec 3.6, spec_peak 2.4, centroid 7.9, flatness 2.0, attack 6.8, decay 4.2, modulation 2.2 (× bar) |
| #374 candidate (`sound/cymbal-369-candidate` @ `cae5f75`) | **FAIL** on 7: spec 3.7, spec_peak 1.5, centroid 7.3, flatness 2.7, attack 4.1, decay 7.6, modulation 2.4 |

**Seeded defects**, applied to each 808 target, with magnitudes frozen before the first run:
- tilt ±6 dB/oct;
- decay time ×½ and ×2;
- a one-octave notch at the spectral peak;
- one sample at half the peak where the hit is 20 dB down;
- +2 semitones;
- a +2-semitone pitch envelope relaxing with τ = 30 ms.

Result: **121/121 FAIL; 118 on the intended feature.** The three that fail on other features:

| seed | intended feature | why | caught by |
|---|---|---|---|
| HC darker | centroid, 0.75 × bar | the conga's loudness is its fundamental | spec_peak, flatness, impulse |
| MA decay ×2 | decay | the maracas' envelope is a gate, not an exponential (fitted τ 3 ms), so "×2" is ill-posed | spec, centroid, attack |
| OH decay ×2 | decay, 0.83 × bar | OH's bar is a DECAY-knob step, which itself lengthens the decay about that much. This is correct under this bar | centroid, attack |

Rule 5: three of the apparatus bugs below are reinstated as injections (`pitch-rms`, `centroid-max-weights`,
`span-target-only`), and each turns a known answer red (`test_injection_*`).

## 4. The 16 shipped sounds, ranked (`rank.json`, the fixed model as it ships)

Ratio to the bar per feature; **bold** fails.

| rank | sound | worst | spec | spec_peak | centroid | flatness | impulse | attack | decay | modulation | pitch | pitch_shape | bar |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | MA | 35.5 (centroid) | **14.8** | **8.7** | **35.5** | **7.1** | 0.2 | **9.8** | **7.2** | – | – | – | WEAK |
| 2 | RS | 27.0 (flatness) | **7.7** | **7.1** | **25.0** | **27.0** | 0.9 | **2.9** | **3.6** | – | – | – | WEAK |
| 3 | BD | 22.2 (pitch_shape) | **1.6** | **2.9** | **1.0** | **9.3** | **4.9** | 0.6 | **1.7** | **5.5** | **1.7** | **22.2** | BD2550 |
| 4 | MC | 19.4 (pitch_shape) | **1.2** | **3.3** | **13.7** | **12.9** | **4.5** | 0.4 | 0.2 | – | 0.1 | **19.4** | MC25 |
| 5 | LC | 16.4 (pitch_shape) | **1.7** | **1.8** | **4.9** | **3.2** | **5.0** | 0.4 | **1.2** | **4.2** | **1.4** | **16.4** | LC25 |
| 6 | CP | 15.9 (decay) | **2.4** | **1.7** | **3.7** | **2.8** | 0.9 | **6.2** | **15.9** | **1.4** | – | – | WEAK |
| 7 | CL | 15.8 (pitch_shape) | **1.8** | **3.7** | **6.2** | **10.0** | 0.3 | 0.3 | **3.5** | – | 0.5 | **15.8** | WEAK |
| 8 | LT | 14.8 (modulation) | **2.7** | **2.9** | **8.5** | 0.6 | **4.8** | **1.2** | **6.8** | **14.8** | 0.4 | **3.0** | LT25 |
| 9 | CB | 11.1 (pitch_shape) | **2.4** | **4.1** | **1.6** | **4.8** | 0.7 | **5.4** | **4.5** | **4.7** | 0.5 | **11.1** | WEAK |
| 10 | MT | 10.5 (centroid) | **2.3** | **3.3** | **10.5** | **2.0** | **4.3** | **2.2** | **2.1** | **4.8** | 0.0 | 1.0 | MT25 |
| 11 | CH | 9.3 (flatness) | **2.5** | **3.5** | **3.2** | **9.3** | 0.6 | **1.5** | **2.9** | – | – | – | WEAK |
| 12 | HC | 8.7 (pitch_shape) | **1.0** | 0.9 | **1.6** | **2.5** | **3.8** | 0.6 | 0.3 | – | 0.4 | **8.7** | HC75 |
| 13 | HT | 8.0 (centroid) | **1.8** | **2.7** | **8.0** | **3.2** | **4.9** | 0.9 | **7.2** | – | 0.1 | **1.2** | HT25 |
| 14 | CY | 7.9 (centroid) | **3.7** | **2.4** | **7.9** | **2.0** | 0.7 | **6.8** | **4.5** | **2.2** | – | – | CY7525 |
| 15 | OH | 4.4 (centroid) | **1.3** | 0.6 | **4.4** | **3.7** | 0.1 | **1.8** | 0.7 | **1.5** | – | – | OH75 |
| 16 | SD | 2.1 (attack) | **1.1** | **1.0** | **1.6** | 0.8 | 0.7 | **2.1** | 0.6 | – | – | – | SD7550 |

Ratios under a WEAK bar and under a neighbour bar are not commensurate, so read the rank within each group.

**What the features localise.** These are pointers, not diagnoses.
- **BD's `pitch_shape`.** The 808 glides 58 → 50 Hz over the first ~50 ms. Ours starts at 50.3 Hz and sits at 49.4.
  That is about 230 cents of missing pitch drop.
- **Toms and congas.** `pitch_shape` and `impulse` fail throughout, and LT fails `modulation`.
- **MA and RS.** Centroid and flatness fail, so the brightness and noisiness trajectories are off.

## 5. A second real TR-808 against the same bars (`crosscheck-second-808.json`)

Apple Logic's "Boutique 808" (`GB_Tasty808_*`) is real-808 one-shots, not redistributable, and used here as a
**private** cross-check, as `docs/drum-verification.md` §1 allows. Only numbers are committed. Its knob positions and
processing are undocumented, **so no verdict rests on it.** The table gives each sound's most favourable sample.

| sound | ours, worst ratio | second 808, best sample, worst ratio |
|---|---|---|
| MA | 35.5 | 16.5 (centroid) |
| RS | 27.0 | 13.2 (flatness) |
| BD | 22.2 | 9.6 (modulation) |
| MC | 19.4 | 12.1 (pitch_shape) |
| LC | 16.4 | 17.7 (flatness) |
| CP | 15.9 | 7.0 (decay) |
| CL | 15.8 | 21.2 (pitch_shape) |
| LT | 14.8 | 14.9 (modulation) |
| CB | 11.1 | 15.0 (pitch_shape) |
| MT | 10.5 | 6.7 (pitch_shape) |
| CH | 9.3 | 2.0 (spec_peak) |
| HC | 8.7 | 6.4 (pitch_shape) |
| HT | 8.0 | 8.6 (attack) |
| CY | 7.9 | 22.4 (spec) |
| OH | 4.4 | 2.6 (flatness) |
| SD | 2.1 | 10.4 (attack) |

**The second 808 fails all 16.** That includes the knobless voices, where knob position cannot be the cause: CH 2.0,
CP 7.0, RS 13.2, CB 15.0, CL 21.2. Unit-to-unit spread (§1.7: ±10 % f0, ±50 % Q), the recording chain, or the set's
processing each moves these features more than one knob step on the Fischer unit does. So:

- **As a pass gate, the issue's bar is unsatisfiable in practice.** An unsatisfiable gate trains everyone to ignore
  gates (CLAUDE.md). This record does not claim that any of our sounds "must" pass it.
- **As a ranking and a localiser, it works.** It separates known-bad from real-808 neighbours, catches every seeded
  defect, and names the failing feature. Against the second 808, ours is clearly farther on CH (9.3 vs 2.0), CP, BD,
  MA, RS and OH, and not farther on SD, CL, CB or CY.

## 6. What this blocks, and the decision needed

Step 4 of #379 (fix the worst sound, pass the gate on untouched settings) cannot be met as written:
1. **Nothing, not even a second real 808, passes this bar.** The coordinator or the operator must choose one of:
   - (a) keep the same-unit bar and accept "improves the ratio on untouched settings, no preservation regression" as
     promotion;
   - (b) calibrate a between-unit bar from a second documented 808 corpus with knob positions. For example,
     archive.org `tr-808-samples` has excellent provenance but no licence, so it could be used for calibration only,
     never redistributed;
   - (c) something else.
2. **The cymbal's selection needs the knob-law render**, and #371 showed `kit_at` is not the instrument. Without the
   `kit_at` repair there is no candidate render at the 9 development or 16 confirmation settings.
3. **By this gate the cymbal is not the worst sound.** It ranks 14th. MA, RS and BD lead, and BD's missing pitch drop
   is a single, circuit-documented mechanism (reference §2).

## 7. Wrong-then-right: 18, all caught by the gate's own controls

Every one of these was caught by a control (self through the path, a half-size resample, a seed or a known answer),
not by inspection. Only #17 was found after our sounds had been measured, by a cross-rate check prompted by the first
ranking; no bar was changed to make anything pass.

1. **attack**: 2 ms RMS envelope in dB. It rippled on a 50 Hz wave: 4.4 dB for a 2 % resample of BD.
2. **attack**: the analytic envelope in dB against time. A 0.1 ms shift on a steep edge swung it tens of dB.
3. **attack**: using the 0 dB crossing. `argmax` flipped between near-equal peaks: 1.9 ms on RS.
4. **attack**: first-crossing times. They jumped between noise peaks on RS, CP and MA under a 3 % resample. Now
   cumulative energy.
5. **pitch**: the instantaneous frequency of a side that had died read 1,827 cents on BD5025. Now both sides must be
   live.
6. **pitch**: an RMS over the hit averaged a 30 ms slide away.
7. **pitch**: a worst-20 ms reading of the raw difference could not tell a slide from a tuning step. Now split into
   offset and shape.
8. **band floor**: relative to the group's own peak. An empty group's floor sat under 16-bit dither, and MT and HT
   failed against themselves.
9. **loudness**: linear, not zero, below the floor. Requantisation moved the centroid 13 %.
10. **rectangular critical bands**: a line crossing a band edge jumped a band, and a 3 % resample of CB read farther
    than a 6 % one.
11. **centroid with max() weights**: a silent frame read 0 Bark, which made the centroid a second decay detector (OH).
12. **span ending with the target**: a doubled MA decay was invisible.
13. **missing-band seed**: it notched the Bark band's centre (50 Hz) and missed LT's 89 Hz line.
14. **bar without the apparatus floor**: the nearest neighbour sat exactly on its bar, and rounding failed it.
15. **power-weighted centroid**: blind to a 6 dB/oct tilt on low toms.
16. **impulse**: its worst-sample statistic wobbles about 1 dB over noise, which failed MA's half-step check.
17. **no common bandwidth**: our CH carries 1.1 % of its power above 20 kHz, which no 44.1 kHz recording can. The
    self-check upsampled an 808 take and could not see it.
18. **pitch bar**: 0.1 cents from BD's TONE neighbour, which is unsatisfiable. Now floored at the 5-cent limen.

Two of the new tests were also wrong at first: the click test was not placed where the seed places it, and the "cut"
test used a sine starting at 0.

## 8. Between-recording calibration from the MARS 808 library: REFUSED by its own rule (`mars-calibration/`)

`tools/gate_calibrate.py` (6 tests in `tools/test_gate_calibrate.py`), run at commit `cdeaa19`. The archive
`808-from-mars.zip` came through `tools/refaudio_s3.py`, which checked it against the catalog SHA-256
(`f567c676...ca82`) and every extracted take against the index size (1,370 files fetched, 0 failed). No audio is
committed; `refaudio/cache/` is gitignored. Only derived per-take distances are (`tables.json`, 772 takes over 16 sounds).

**What the gate now says: nothing has changed.** The pre-registered selection rule found no calibrated bar, so the
existing same-unit / WEAK bar stays the ranking bar (§4). The calibrated bar is not acceptance authority because there
is none.

**Lineage, before any distance was read.** The pack's own About text describes one machine ("the 808") recorded
through an API 1608 to an Apogee converter (Digital) or via Otari tape (Tape), group-normalised per voice. The Fischer take
is a different recording. Neither documents a serial number, so:
- **"as close as another real unit" is REFUSED as a claim.** Several libraries are not several units; the legacy MARS
  edition is the same vendor and machine.
- What was measured is typed **cross-recording**: Fischer target against MARS Clean takes of the same voice, with the knob
  setting unmatched (nearest by `spec` stands in). It contains unit spread, chain, mastering and setting residual together.
- Only the chain is separable (a Digital take against its Tape twin). That table was not produced, so no chain floor is
  published (the `chain_floor` function exists but is not run by the CLI; not claimed).

**Frozen first.** Corpus: Clean takes only, "Combo" excluded. Groups: sha256 of the path with the chain removed, mod 3 =
development / calibration / untouched validation, so a Digital take and its Tape twin cannot straddle groups.
Adjacent knob settings can still straddle (stated, not removed). Bar: max over the calibration group's k nearest takes
plus the apparatus floor, with the gate's perceptual floors. k is the smallest of (1, 3, 5) whose development bar covers the
calibration group's nearest take on at least 80 % of sounds.

**Result.** Development -> calibration coverage over the 9 sounds that have both groups:

| k | 1 | 3 | 5 | 8 (diagnostic) | 12 (diagnostic) |
|---|---|---|---|---|---|
| covered | 0/9 | 3/9 | 5/9 | 5/9 | 7/9 |

No selectable k reaches 80 %, so the calibration is REFUSED with no bars, not a looser k. Widening to k = 12 would
still be 78 %. Missing data, by reason: RS, CL, CP, CB, OH, CH have no calibration take (2 to 5 path keys cannot be split
three ways), and MA has no untouched take. **Only 9 of 16 sounds could have been calibrated at all**; the six single-take
sounds stay on the WEAK bar whatever else happens.

**Diagnostic runs (labelled NOT acceptance authority; the rule was overridden by hand, k = 5 and 12).**

| gate | k = 5 | k = 12 |
|---|---|---|
| Q1 start red (stub FAIL, silence REFUSED) | 10/10, 10/10 | 10/10, 10/10 |
| Q2 seeded defects FAIL (need 90 %) | 70/77 | 68/77 (fails) |
| Q3 shipped cymbal FAILs | FAIL | FAIL |
| Q4 Fischer target stays green / untouched best take passes | 10/10 / **2/9 (fails)** | 10/10 / **4/9 (fails)** |
| Q5 corrupted corpus (every take seeded "darker") does not qualify | not qualified | not qualified |

Missed seeds at k = 5: SD wrong_pitch, LT slide, HT slide, MA decay_long, MA click, MA wrong_pitch, CY wrong_pitch.
k = 12 adds BD slide and MC wrong_pitch. So the loosened bar goes blind to pitch error and pitch slides on the sounds where
the old gate saw them. The untouched takes mostly fail the bars built from other takes: BD, SD, LC, MT, HT, HC and CY at k = 5 (7 of 9).
**Q5 under the pre-registered rule is vacuous** (the corrupted corpus was refused by the same rule that refused the clean
one), so it only means something in the two diagnostic runs. #374's candidate fixture was not rebuilt here, so
that control was not run; Q3 uses the shipped cymbal (rendered from this checkout) alone.

**Sound-relevant context against the existing bar (descriptive, nothing selected from it; `old_bar_context`).** No MARS take
passes the old bar: 0 of 772. The most favourable take (any group, so optimistic) against each of our sounds, as ratios to
the old bar at current `main`:

| sound | MARS best | ours | | sound | MARS best | ours |
|---|---|---|---|---|---|---|
| BD | 7.5 | 22.2 | | CY | 3.7 | 7.9 |
| LT | 5.6 | 14.8 | | OH | 1.1 | 4.4 |
| MT | 3.1 | 10.5 | | CH | 2.0 | 9.3 |
| HT | 4.7 | 8.0 | | RS | 2.3 | 20.6 |
| LC | 5.2 | 16.4 | | CL | 5.1 | 15.8 |
| MC | 4.3 | 19.4 | | CP | 5.1 | 15.9 |
| HC | 2.0 | 8.7 | | MA | 4.5 | 35.5 |
| SD | 2.9 | 2.1 | | CB | 10.7 | 11.1 |

Another real-808 recording sits 1.1 to 10.7 times the old bar, so the old bar remains unsatisfiable by a real recording
(§5 again, now on 772 takes). On this reading **SD and CB are already inside the range of real-recording spread**, and
BD, MT, LC, MC, HC, RS, CL, CP, MA, CH and OH are far outside it (our ratio at least 3 times the MARS best); LT (2.6x), CY (2.1x) and HT (1.7x) are in between. That ordering is an
indication for scoping repairs, not a verdict. The RS (27.0 to 20.6) and a few others differ from §4 because §4 was rendered
at an earlier `main`; the table above was re-rendered at this commit.

**Wrong-then-right, this calibration: 2.** (1) The first key rule paired no twin for BD, SD, toms, congas, CY and OH
(the Tape name carries " Tape" mid-name); caught by a per-sound orphan count, now `test_twin_pairing_on_the_real_index`
against the committed index. Tables are re-grouped from the path on read, so the stale groups were never used. (2) A
guessed key count in that test (400 against 386). The rule above was written before any distance was computed and was
not changed after seeing the REFUSED, which is the point of recording the k = 8 and 12 sweep.

**What remains.** A calibrated bar needs either more than one unit with documented provenance (the MARS pack cannot supply
it), or a different statistic than best-of-k nearest-by-`spec`: the within-recording setting spread dominates, and the pitch
trajectory of the toms and congas does not transfer between recordings. Scope each repair as its own issue from the ranking
above; the existing gate keeps ranking until a bar is qualified.

```sh
export REFAUDIO_S3=s3://2am-batch-jobs-221082181346/refaudio/samples-from-mars REFAUDIO_S3_PROFILE=batch-runner-submit
python3 tools/refaudio_s3.py --keep-archive --prefix 808-from-mars.zip "808 From Mars/WAV/01. Individual Hits/"
python3 tools/gate_calibrate.py measure --refs /tmp/tr808-fischer --out tables.json                 # ~6 min
python3 tools/gate_calibrate.py measure --refs /tmp/tr808-fischer --corrupt darker --out tables-corrupt-darker.json
python3 tools/gate_calibrate.py calibrate --refs /tmp/tr808-fischer --tables tables.json \
    --corrupt-tables tables-corrupt-darker.json --ours <dir of rendered <SOUND>.wav> [--force-k 5] --out calibration.json
```
`/tmp/tr808-fischer` is `tidalcycles/sounds-tr808-fischer` at `85fbecf`. The two `measure` runs were made at `b56af6f`
(the measure code is unchanged since); `calibrate` at `cdeaa19`.

## 9. Between-recording bar, rule v2: REFUSED again, with the reason isolated (`mars-calibration-v2/`)

`tools/gate_between.py` (8 tests in `tools/test_gate_between.py` at the time; 36 pass with the other two gate suites on the build box),
run at branch commit `3bdb7e9b` (base `2aceb8ce`; not a `main` commit), tables unchanged from section 8 (derived distances only; no MARS audio was read or written this time).
The operator's ruling (2026-10-02) asks for "as close as another real 808, measured across units or recordings". Section 8
refused under a rule with two defects. Rule v2 repairs those two and nothing else:

1. the validation was ONE hash split of 5-30 keys. v2 uses 200 seeded half-splits of the pool, both directions;
2. k was an absolute count (5 of 7 conga keys, 5 of 72 BD keys). v2's dial is a FRACTION p of the pool's keys.

**What was and was not frozen before the data.** Rule v2 was designed after an exploration over ALL keys (VAL included)
that showed coverage rising with the matched fraction. So VAL is untouched by v2's selection computation, and
`test_selection_never_reads_the_untouched_group` proves that, but VAL is not untouched by v2's design. Its pass rate
checks the procedure; it is not an independent test. The independent test is a second real 808, and the Boutique
samples were not on the host that ran this. Not run.

**Selection** (smallest p in 0.05-0.50 whose mean split coverage over the 9 sounds with >= 8 pool keys reaches 80 %):

| p (fraction of pool keys) | 0.05 | 0.10 | 0.15 | 0.20 | 0.30 | 0.50 | 0.75 (sweep) | 1.00 (sweep) |
|---|---|---|---|---|---|---|---|---|
| mean coverage | 0.07 | 0.12 | 0.32 | 0.36 | 0.52 | 0.72 | 0.82 | 0.93 |

No selectable p reaches 80 %, so **the calibration is REFUSED with no bars, and the existing gate keeps ranking fixes.**
The two p values past the selectable grid are recorded so the refusal not to widen stays visible.

**Diagnostic runs, labelled NOT acceptance authority (p overridden by hand).** The two requirements cross, which is the
finding:

| gate | p = 0.75 | p = 1.00 (every real setting in the pool) |
|---|---|---|
| Q1 start red (stub FAIL, silence REFUSED) | 16/16, 16/16 | 16/16, 16/16 |
| Q2 seeded defects FAIL (need 90 %) | 110/121 (90.9 %) | **105/121 (86.8 %) fails** |
| Q3 shipped cymbal FAILs | FAIL (spec_peak, centroid, modulation; 1.42x) | FAIL (centroid, modulation; 1.29x) |
| rejected #374 candidate FAILs | FAIL (centroid, modulation; 1.27x) | FAIL (centroid, modulation; 1.27x) |
| Q4 Fischer target stays green / untouched best take passes (need 80 %) | 16/16 / **7/9 (77.8 %) fails** | 16/16 / 9/9 |
| Q5 corrupted corpus (every take seeded "darker") does not qualify | not qualified | not qualified |

At p = 1.0 the bar is the envelope of every real recording of the voice, which still does not fit the shipped or the
rejected cymbal, but only by 1.27-1.29x, so that is a thin margin and not a separation. A bar loose enough to pass an
untouched real recording is loose enough to miss 13 % of the seeded defects (decay on BD, SD, MA and CY; slides on BD, LT, LC, MC and HT; wrong pitch on SD,
LT, MT, RS, MA and CY; SD brighter), and a bar tight enough to catch them fails untouched real
recordings. **No p satisfies Q2 and Q4 together.** The #374 fixture was regenerated from `cae5f75` on the box and
matches `prove.json`'s recorded hashes byte for byte (`ffc30d3da9c176ec`, `3078bf088ea5cbcf`), so the control is the
same signal.

**Why, from the held-out failures** (`top_failing` in `between.json`): LT fails `flatness` and `modulation`, MT and HT
`centroid`, HC `pitch_shape`, CY `attack`. These are the features whose spread between two recordings of one voice
exceeds the spread between neighbouring settings of one unit. Six of 16 sounds (RS, CL, CP, MA, CB, CH; OH has 5 keys)
carry two keys, so nothing can be held out: their v2 bar is `WEAK-UNVALIDATED` (max over the two nearest keys) and is
reported only in the diagnostics.

**What would change the answer** (it is data, not a tighter statistic): a setting-matched between-recording pair per
voice (the knob positions of the Fischer take reproduced on a second documented unit), or enough recordings of the six
knobless voices to hold something out. The MARS pack cannot supply either. This is filed as a follow-up.

### The shipped kit, ranked (`rank-current/rank.json` at branch commit `3bdb7e9b`, base `2aceb8ce`)

`3bdb7e9b` is a commit on this branch, not on `main`; its base is `2aceb8ce`. Since then `origin/main` changed the drum
model: #554 (the #551 coupling register) added `A_COUPLE` to `model/drums_fx.py`. `couple_en` resets to 0, and on
`origin/main` only `model/drums_fx.py` and its tests write `A_COUPLE`; no shipped program enables it. The ranking was
NOT re-run after the rebase. Its inputs were checked instead: `tools/drum_render_hashes.py` gives identical SHA-256 for
all 16 renders at `2aceb8ce` and at the rebased head `77962cd4` (on `origin/main` `468339aa`)
(`rank-current/render-hashes-rebase.json`). The gate code is unchanged by the rebase, so the table below holds there.

Section 4's ratios reproduce at `3bdb7e9b` except RS (27.0 to 20.6; section 8 already noted this). Ranked by how much
farther from the Fischer target our sound is than the nearest real MARS recording of the same voice. **Descriptive, not a
verdict:** the MARS side is the most favourable take across all groups (optimistic), no bar was calibrated, and the
ratios are against the old same-unit / WEAK bar, so a WEAK row and a neighbour row are not commensurate with each other.
`gate_between.py table between.json` regenerates this.

| rank | sound | ours / MARS best | ours (worst, vs old bar) | MARS best (worst) | MARS takes | bar |
|---|---|---|---|---|---|---|
| 1 | RS | **9.0x** | 20.6 (centroid) | 2.3 (decay) | 4 | WEAK-resampled-take |
| 2 | MA | **7.9x** | 35.5 (centroid) | 4.5 (attack) | 4 | WEAK-resampled-take |
| 3 | CH | **4.6x** | 9.3 (flatness) | 2.0 (spec_peak) | 4 | WEAK-resampled-take |
| 4 | MC | **4.5x** | 19.4 (pitch_shape) | 4.3 (flatness) | 44 | 808-neighbour |
| 5 | HC | **4.4x** | 8.7 (pitch_shape) | 2.0 (impulse) | 44 | 808-neighbour |
| 6 | OH | **4.2x** | 4.4 (centroid) | 1.1 (attack) | 10 | 808-neighbour |
| 7 | MT | **3.4x** | 10.5 (centroid) | 3.1 (decay) | 66 | 808-neighbour |
| 8 | LC | **3.2x** | 16.4 (pitch_shape) | 5.2 (flatness) | 44 | 808-neighbour |
| 9 | CP | **3.1x** | 15.9 (decay) | 5.1 (attack) | 4 | WEAK-resampled-take |
| 10 | CL | **3.1x** | 15.8 (pitch_shape) | 5.1 (flatness) | 4 | WEAK-resampled-take |
| 11 | BD | **3.0x** | 22.2 (pitch_shape) | 7.5 (pitch) | 144 | 808-neighbour |
| 12 | LT | **2.6x** | 14.8 (modulation) | 5.6 (decay) | 66 | 808-neighbour |
| 13 | CY | **2.1x** | 7.9 (centroid) | 3.7 (decay) | 48 | 808-neighbour |
| 14 | HT | **1.7x** | 8.0 (centroid) | 4.7 (decay) | 66 | 808-neighbour |
| 15 | CB | **1.0x** | 11.1 (pitch_shape) | 10.7 (pitch_shape) | 4 | WEAK-resampled-take |
| 16 | SD | **0.7x** | 2.1 (attack) | 2.9 (spec) | 216 | 808-neighbour |

Read it with section 4 (rank by the old bar: MA, RS, BD, MC, LC, CP, CL, LT, CB, MT, CH, HC, HT, CY, OH, SD). **Both
orderings put MA and RS at the top and SD last.** SD and CB are already inside the spread of real recordings (0.7x,
1.0x). The two orderings differ in the middle (BD is 3rd by the old bar and 11th relative to real recordings, because
real recordings are also far from the Fischer BD on pitch). The pre-registered rule selected neither ordering; the
follow-up issues are scoped from both.

### Wrong-then-right, this record: 2

1. My first v2 test fixture expected p = 0.05 to be chosen on a homogeneous pool. By symmetry the half that holds the best
   take fails half the time at a tiny p, so the smallest p reaching 80 % is larger; the test now asserts the rule, not a
   number.
2. The first validation fixture sat at 0.305 against a bar of 0.304 (a fixture rounding error); caught by the
   `VALIDATED` assertion.

**Not done here, and why.** The p dial is not registered in `docs/sensitivity/registry.json`: a record needs a
fixed-width evidence table and a prediction derived independently of the measurement, and the sweep here is a JSON
record of a gate-calibration statistic, not a shipped parameter. The sweep is committed (`between.json`) and registering
it is part of the follow-up if a bar is ever selected.

```sh
# on the build box; tables from section 8, Fischer at 85fbecf, no MARS audio needed
P=~/v379/bin/python; D=docs/scorecard/gate-379/mars-calibration
$P tools/perceptual_gate.py rank --refs ~/fischer --wavs ~/out/ours --out rank.json
$P tools/gate_between.py --refs ~/fischer --tables $D/tables.json --corrupt-tables $D/tables-corrupt-darker.json \
    --ours ~/out/ours [--fixtures <CY5025-candidate.wav dir> --force-p 0.75] --out between.json
$P tools/gate_between.py table between.json
```

## Reproduce (build box)

```sh
P=/home/ubuntu/work/venv/bin/python; R=/home/ubuntu/dev/refs/sounds-tr808-fischer
$P -m pytest -q tools/test_perceptual_gate.py
$P tools/perceptual_gate.py prove --refs $R --fixtures <dir with CY5025-shipped.wav, CY5025-candidate.wav> --out build/gate/prove.json
$P tools/perceptual_gate.py rank  --refs $R --wavs build/gate/ours --out build/gate/rank.json
$P tools/perceptual_gate.py crosscheck --refs $R --other <private Boutique 808 dir> --out build/gate/crosscheck.json
```

The #374 fixture is `tools/cymbal_candidate_eval.py --wavs` on `sound/cymbal-369-candidate` (`cae5f75`). Its sha256
prefixes are in `prove.json` → `provenance`.
