# Verifying the drum section against a real TR-808

What this is: every voice `model/drums_fx.py` renders, measured against a
recording of a real Roland TR-808 at the same panel setting, with the numbers
and the pictures behind each verdict.

> **Superseded in part, 2026-09-18 — read §8 first.** The body/air energy
> split used throughout §3 and §4 is invalid on a decaying one-shot and its
> numbers are withdrawn; so is "the first 4 ms peaks at 250 Hz". §8 gives the
> validated replacements and what was actually wrong. The structural findings
> — the cowbell's shared gate, the missing attack window, the snare's noise
> band — survive; the magnitudes do not.

**Headline.** Four of eight voices are right or nearly right. The snare is
badly wrong — ~~its noise is 16 dB too quiet~~ (withdrawn, §8.1: the level is
2.3 dB down, the *band* is the fault), so half the instrument is
missing. The cowbell is wrong in three ways. The bass drum works but is
thin, short and pitched high, which is the sponsor's "the kick is really
weak". **All four of the previously suspected defects are refuted**: they were
measurement artefacts, not design faults. The real faults are different ones,
listed below.

---

## 1. The reference audio

### Primary — Michael Fischer / Technopolis, CC0-1.0

| | |
|---|---|
| source | <https://github.com/tidalcycles/sounds-tr808-fischer> commit `85fbecf1bec32553395625ea659e2a56dfd7c0e1` |
| original | Michael Fischer / Technopolis, *"Roland TR-808 Rhythm Composer Sound Sample Set 1.0.0"*, 09/08/94 |
| licence | **CC0-1.0**, full legal code as `LICENSE` in the repo; each of the 16 `_soundmeta/*.json` independently declares `"license": "cc0"`. Public-domain dedication — redistribution permitted |
| machine | a real Roland TR-808, **serial no. 103852**, explicitly *not* samples of samples |
| capture | the **individual voice outputs**, not the master bus; 16-bit / 44.1 kHz, SoundEdit 16 on a Quadra 660AV |
| contents | 116 one-shots, one file per voice per knob position, all 16 voices |

The reason this set and not another: **every knob position is in the
filename**, on a 0–10 scale sampled at 0.0 / 2.5 / 5.0 / 7.5 / 10.0, tone or
tuning before decay or snappy. `BD5050.WAV` is the bass drum with TONE 5.0 and
DECAY 5.0 — which is exactly the "all knobs at 12 o'clock" condition of
Roland's own June-1981 tuning chart (`tr808-reference.md` §1.6), the chart
every preset in `kit_808()` is derived from. So the comparison is like for
like rather than against an unlabelled sample somebody normalised.

Two caveats the set states about itself, both honoured here:

- **LEVEL was pinned at maximum for every voice.** The relative loudness
  between voices in this set is not the machine's. Nothing below compares
  loudness across voices; every file is peak-normalised before measurement.
- The audio is CC0; "Roland" and "TR-808" remain Roland's trademarks.

Not committed to this repo — it is 12 MB of audio and `.gitignore` excludes
`*.wav`. Fetch it with the clone above; the script takes its path.

### Cross-check — Apple Logic Pro, "Boutique 808"

`/Library/Application Support/Logic/Ultrabeat Samples/Boutique 808/`,
26 uncompressed AIFFs (`GB_Tasty808_*`). Apple factory content: licensed for
use in the user's productions, **not redistributable**, so it is a private
cross-check only. Used to confirm the Fischer set is not idiosyncratic. It
agrees on every structural number: snare 176 Hz, rimshot 455 Hz, claves
2542 Hz, cowbell 856 Hz, and hats/cymbal all peaking at 7141–7173 Hz — the
7.1 kHz band-pass. Its provenance and processing are undocumented, so no
verdict here rests on it.

### Also on this machine, and unusable

`~/Music/Ableton/Factory Packs/Drum Machines/` has a complete 808 kit, one
file per voice, but every file is Ableton's proprietary compressed AIFF-C
(`FORM…AIFC…able`). Neither ffmpeg nor scipy decodes it. Noted so nobody
repeats the search.

### Candidates rejected

- **kb6.de** — the Roland content has been deleted from the site ("*13,049
  WAV samples … have been deleted from this list*"), and it carried no
  explicit licence anyway.
- **archive.org `tr-808-samples`** — excellent provenance (early-revision
  unit, RME UFX) but **no licence metadata**, and two long continuous FLACs
  rather than per-voice one-shots.
- **archive.org `808-for-cmi`** — no licence metadata.

---

## 2. How the measurements are taken

`model/drum_verify.py`. Run:

```sh
git clone https://github.com/tidalcycles/sounds-tr808-fischer /tmp/tr808-fischer
.venv/bin/python model/drum_verify.py \
    --refs /tmp/tr808-fischer \
    --ours <dir holding 00-solo-N-XX.wav> \
    --out docs/img/drum-verification
```

Four rules, because the obvious version of each is wrong:

1. **Each solo render holds three hits** (0.05 s at accent 1.0, 0.75 s at
   1.4, 1.45 s at 0.6). They are segmented and measured individually. The
   earlier report of a 700 ms attack on seven voices came from measuring
   first-onset to *global* peak across a whole file, which finds the second
   hit.
2. **Decay is a least-squares fit** of the log-envelope over −3 dB to −30 dB,
   reported with its R² and the dB span it covers, plus t(−20 dB) which is
   what Roland's chart column is comparable to. A first crossing of 1/e is
   useless here: the hats and the cowbell are sums of incommensurate squares
   whose envelope beats by 6–10 dB, and the first crossing reads a trough.
   This is what produced "BD decays in 3.4 ms".
3. **The envelope is a moving RMS** over several periods of the voice's own
   fundamental (12 ms for a 50 Hz kick, 3 ms for a hat), for the same reason.
4. **Nothing is resampled.** The references are 44.1 kHz and our renders
   48 kHz; every metric here is rate-independent.

Two numbers are reported for brightness, because one of them lies. The
**magnitude centroid** is the textbook definition and is what the earlier
report used — but it weights a wide, quiet noise floor heavily, so a voice
with 98 % of its energy below 700 Hz can still show a 6.5 kHz magnitude
centroid. The **power centroid** and the **body/air energy split** are
reported alongside and are the ones that track what you hear.

---

## 3. The four suspected defects — all four refuted

| # | reported | measured | verdict |
|---|---|---|---|
| 1 | BD decays in 3.4 ms against a spec of 15–540 ms | **τ = 127.4 ms**, R² 0.991 over a 27 dB span, and 127.4 / 127.4 / 128.0 ms on the three accents | **refuted.** The decay reaches the modal coefficients fine. A separate, real BD problem is in §4.1 |
| 2 | LT decays in 2.2 ms against 92 ms | **τ = 88.8 ms** against a spec of 88 and a real machine measuring **87.6 ms** | **refuted.** LT is one of the two best voices we have |
| 3 | CP centroid 4552 Hz against a 1070 Hz band-pass — band-pass not in the path, tail VCA missing | spectral peak **1050.5 Hz** against the real machine's **999.5 Hz**; tail present, τ 36.9 ms against the real 37.4 ms | **refuted, and the test was invalid.** A 2-pole band-pass at Q 1.6 has 6 dB/oct skirts, so white noise through it has a magnitude centroid of several kHz by construction. **The real TR-808 clap measures 3601 Hz.** Comparing a centroid against a band-pass centre is a category error |
| 4 | OH centroid 11019 Hz, +41 % — getting CH's 11.7 kHz corner | OH **10386 Hz** against the real machine's **9769 Hz**, i.e. **+6.3 %**. Our CH is 12387 Hz — 2 kHz above our OH | **refuted.** Energy above 13 kHz: our CH 47.0 %, our OH 5.5 %. If OH used CH's corner these would match; they differ by 8.5× |

None of the four was a design fault. Three of the four were artefacts of how
the measurement was taken; the fourth compared a statistic against a number
that statistic cannot equal.

---

## 4. Per-voice verdicts

Every row: real = Fischer s/n 103852 at the stated knobs, ours = hit 1 of the
solo render at accent 1.0.

| voice | verdict | f0 / peak | τ | worst single error |
|---|---|---|---|---|
| **BD** | **close — thin and short** (§8.3) | 56.0 vs **49.8** Hz — superseded, 50.70 ± 0.02 | 127 vs 230 ms — superseded, the kit's own f0 error (DR 0009) | no attack transient, no harmonics |
| **SD** | **wrong** (§8.1) | 173.0 vs 172.0 Hz ✓ | 29.1 vs 28.4 ms ✓ | ~~noise 1.2 % vs 47.3 %~~ withdrawn — 18.6 % vs 27.7 %, and the band is wrong |
| **LT** | **matches** | 90.0 vs 88.4 Hz (+1.8 %) | 88.8 vs 87.6 ms (+1.4 %) | pink noise absent (small) |
| **HT** | **matches** | 185.0 vs 187.5 Hz (−1.3 %) | 43.0 vs 41.7 ms (+3.1 %) | pink noise absent (small) |
| **CH** | **close — too bright** | 7200 vs 6814 Hz | 19.6 vs 16.0 ms (+23 %) | 47 % above 13 kHz vs 30 % |
| **OH** | **close — too narrow** | 7200 vs 6814 Hz | 158 vs 180 ms (−12 %) | 84 % in 6–9 kHz vs 67 % |
| **CP** | **close — flam smeared** | 1050 vs 999 Hz (+5.1 %) | 37.0 vs 37.4 ms ✓ | bursts sit on a plateau, not gaps |
| **CB** | **wrong** | 800 vs 823.6 Hz ✓ | 30.0 vs **98.0** ms | difference tone at 260 Hz that the machine has not got |

---

### 4.1 BD — close, and it really is weak

![BD](img/drum-verification/BD.png)

What is right: both envelopes are clean exponentials over 27 dB (R² 0.991
ours, 0.996 the machine). The **decay-knob range in §12 is
confirmed exactly** — the real machine sweeps τ from **15.2 ms** at DECAY 0.0
to **532 ms** at DECAY 10.0, against the document's "15 → 540 ms". Pitch does
not move with the decay knob (49.6 → 50.6 Hz across the whole range), which is
the reference's central claim about the bridged-T with feedback, confirmed on
hardware. Our τ is stable to 0.5 % across accents.

Three things are wrong, and together they are "the kick is really weak":

1. **Pitch is 12.4 % high.** Ours 56.0 Hz, the real machine 49.8 Hz, and
   49.6–50.6 Hz on all 25 of its bass-drum files. 56 Hz comes from Roland's
   chart ("18 ms period"); the hardware says 50. That is nearly two
   semitones, and the 808 kick is a pitch people know.
   **Fix:** `mode_writes(M_BD, 50.0, ...)` in `kit_808()`.
2. **Decay is 45 % short.** Ours τ = 127 ms; the real machine at the same
   12-o'clock DECAY is 230 ms, t(−20 dB) 459 ms against our 263 ms. Our
   preset sits at about knob 4.2 of 10. The ends of the range are right, so
   this is the mid-point mapping, not the mechanism.
   **Fix:** Q ≈ 40 rather than 22.3 (τ ∝ Q).
3. **There is no attack and no harmonic structure.** ~~The real bass drum's
   first 4 ms has its energy at **250 Hz**~~ — **withdrawn, an FFT-bin
   artefact; see §8.3 for the band-energy measurement that replaces it**; ours has no spectral peak above DC
   at all — the first 4 ms is a rising ramp. And the real one has a 2nd
   harmonic at **−43 dB** and a 3rd at −51 dB, where ours are at **−80 and
   −97 dB**: ours is a mathematically pure sine. On anything smaller than a
   subwoofer the harmonics *are* the kick. This is the largest contributor to
   "weak".
   **Fix:** the §14 "BD attack window" preset (130 Hz for 4 ms) is specified
   but is not in `kit_808()` — only `E_BDCLICK`, a 1 ms pulse leak at peak
   0.06, which produces a click with no pitch. Switch the attack preset in,
   and add a little asymmetric saturation on the body path to generate h2/h3.

Note also the real attack takes **14.0 ms** to peak against our **8.8 ms**
(and on a 4 ms envelope window, 17.4 against 4.5 — the measurement is
window-sensitive, so take the ratio, not the absolute). A two-pole resonator
struck by an impulse peaks in a quarter period, which at 56 Hz is 4.5 ms; the
real 808's excitation is not an impulse but a pulse shaped over ~10 ms, and
that is why its energy arrives as body rather than as a click.

### 4.2 SD — wrong. Half the instrument is missing

![SD](img/drum-verification/SD.png)

Look at the envelope panel: the two traces are superimposable. The tonal half
of this voice is excellent —

| | ours | real | spec |
|---|---|---|---|
| low mode | 173.0 Hz | 172.0 Hz | 173 |
| high mode | 336.1 Hz | 339.4 Hz | 336 |
| τ | 29.1 ms | 28.4 ms | 30 |
| t(−20 dB) | 62.3 ms | 57.7 ms | 60 |

The "later units, ≈173/336 Hz" branch of §3 is **confirmed on hardware**: this
machine measures 172.0 Hz on all 25 snare files. The 1981 chart's 238/476 Hz
is not what a 1980s-serial unit does.

Now the spectrum panel. The real machine's noise band sits at −20 dB from
1.5 to 10 kHz. Ours sits at −50 dB. Measured as energy:

> **This table is withdrawn (§8.1).** Both columns are the whole-span Hann
> split, which under-reports a fast-decaying noise by 5–9×. The validated
> figures are 27.7 % for the machine at SNAPPY 5.0 and 18.6 % for ours.

| | body (<700 Hz) | noise (>700 Hz) | power centroid |
|---|---|---|---|
| real, SNAPPY 5.0 | 48.5 % | **51.5 %** | **2513 Hz** |
| ours | 98.8 % | **1.2 %** | **282 Hz** |

~~The noise is **about 16 dB too quiet**~~ — a factor of 47 in power. Measured
against the machine's own SNAPPY law, our kit is sitting at roughly **2.5 on a
0–10 dial** while claiming to be the 12-o'clock reference:

| SNAPPY | 0.0 | 2.5 | 5.0 | 7.5 | 10.0 |
|---|---|---|---|---|---|
| noise share, real | 0.0 % | 1.1 % | 47.3 % | 79.3 % | 89.8 % |
| ours | | **1.2 %** | | | |

> Withdrawn (§8.1). The validated curve is 4.70 / 4.96 / 27.66 / 56.01 /
> 71.89 %, it is flat from 0 to 2.5, and ours interpolates to SNAPPY ≈ 4.4.

Second, the band is wrong. Ours is white noise through a 2-pole high-pass at
2.75 kHz, so it is flat to Nyquist. The real machine's snare noise is a hump:

| Hz | 0.7–1.5 k | 1.5–3 k | 3–5 k | 5–8 k | 8–12 k | 12–20 k |
|---|---|---|---|---|---|---|
| real | 2.0 % | 20.3 % | **34.9 %** | 28.3 % | 10.7 % | 3.8 % |

It peaks at 3–5 kHz and falls above. §3's "2-pole HP 2.75 kHz Q 0.7" is
therefore **incomplete** — something band-limits the noise above ~5 kHz that
the SD schematic walk-through does not account for. `tr808-reference.md` §3
should be amended.

**Fix:** raise `E_SDN`'s peak and `M_SDHP`'s amp until the noise carries
~50 % of the energy at accent 1.0, and add a low-pass (or a band-pass around
4 kHz) to the snappy path. Until then this does not sound like an 808 snare;
it sounds like a tuned tom.

### 4.3 LT and HT — match

![LT](img/drum-verification/LT.png)
![HT](img/drum-verification/HT.png)

The best two voices. The reference document's tom table is confirmed almost
exactly by the hardware, across the whole tuning knob:

| knob | 0.0 | 2.5 | 5.0 | 7.5 | 10.0 | §12 says | τ real | τ §12 | τ ours |
|---|---|---|---|---|---|---|---|---|---|
| LT | 81.4 | 83.7 | **88.4** | 93.1 | 99.3 | 80 / 90 / 100 | 87.6 ms | 92 | **88.8** |
| MT | 124.6 | 128.0 | **135.1** | 144.1 | 153.8 | 120 / 135 / 160 | 57.8 ms | 58 | — |
| HT | 170.4 | 176.2 | **187.5** | 201.3 | 212.2 | 165 / 185 / 220 | 41.7 ms | 44 | **43.0** |

Ours lands inside 2 % on pitch and 5 % on decay for both. Nothing to do here.

One gap, small but real: **the toms have no noise path.** §4 specifies pink
noise through a 1-pole low-pass at 400 Hz; `kit_808()` routes only
`SRC_PULSE` to `M_LT` and `M_HT`. Measured, the real machine has 0.04 % (LT)
and 0.14 % (HT) of its energy above the split frequency and ours has 0.00 %.
That is −28 dB on HT — small in energy, but it is the entire "air" of the
attack. Worth adding; not worth blocking on.

Attack is again fast: 5.6 ms against the real 12.9 ms (LT), 4.0 against 4.9
(HT). Same cause as the BD — we strike with an impulse where the machine uses
a shaped pulse.

### 4.4 CH and OH — close; the hats are genuinely square-oscillator hats

![hat line structure](img/drum-verification/hat-line-structure.png)

This is the diagnostic that matters most, and **we pass it**.

| | flatness, 5–15 kHz | lines ≥10 dB prominent |
|---|---|---|
| real TR-808 open hat | **−7.5 dB** | 558 |
| ours | **−13.3 dB** | 580 |
| white noise, same envelope (control) | −2.5 dB | 427 |

The real machine's hat is discrete, not broadband — which settles hypothesis 2
of §13 empirically rather than from the schematic. Ours is discrete too, and
on the same grid: both our render and a synthetic six-square control put lines
at 6283 and 6795 Hz, and the real machine has strong lines within a few Hz of
both. No noise-based emulation reaches −7.5 dB, let alone −13.3.

We are also *more* discrete than the machine (−13.3 vs −7.5 dB). The
difference is the floor between the lines: the real unit's sits at about
−35 dB, ours at −45 dB. That floor is oscillator jitter plus the avalanche
noise generator bleeding across the board, and it is part of the sound — a
hat with nothing between the partials reads as "cleaner than an 808", which
for this instrument is not a compliment. Worth a small dither on the
oscillator increments if it is cheap.

![CH](img/drum-verification/CH.png)
![OH](img/drum-verification/OH.png)

The two high-passes are distinct and in the right order — this is the
refutation of suspected defect 4:

| energy | 3–6 k | 6–9 k | 9–13 k | >13 k | magnitude centroid |
|---|---|---|---|---|---|
| real CH | 3.8 % | 30.1 % | 36.1 % | 30.0 % | 11734 Hz |
| ours CH | 1.1 % | 22.0 % | 29.8 % | **47.0 %** | 12387 Hz |
| real OH | 8.7 % | 66.9 % | 20.2 % | 4.2 % | 9769 Hz |
| ours OH | 3.3 % | **83.8 %** | 7.4 % | 5.5 % | 10386 Hz |

Both are about 6 % bright. The shape errors are in opposite directions and
are the same error: **our filters are too selective.** Our CH dumps 47 % of
its energy above 13 kHz where the machine puts 30 %; our OH crams 84 % into
6–9 kHz where the machine spreads 67 %. Both of ours have roughly a third of
the machine's 3–6 kHz spill. The real 2-pole high-passes leak more below
corner than ours do, and the real machine rolls off above 13 kHz where ours
does not.

Decay: CH τ 19.6 ms against the real 16.0 (spec 22); t(−20 dB) 40.8 against
37.1 — good. OH τ 158 against 180 (spec 196), −12 %. The machine's OH DECAY
knob runs 22 → 63 → 180 → 217 → 214 ms; note it **saturates above 7.5**,
which our linear decay control does not reproduce and probably should.

One unverified oddity: our CH's dominant partial is 7200 Hz at accent 1.0 but
14221 Hz at accent 1.4 and 14400 Hz at accent 0.6 — an octave jump, with
level. All three are harmonics of the same 800 Hz oscillator (the 9th and the
18th), so it is the swing VCA's saturation changing which line wins. The reference set has only one closed-hat sample, so **we cannot
tell whether the real machine does this.** Flagged, not judged.

### 4.5 CP — close; the flam is smeared

![clap](img/drum-verification/clap-bursts.png)

The structure is there. §7's burst hypothesis is confirmed on hardware: the
real clap has three clean bursts with the envelope falling to **0.02** between
them, at **0.7, 12.0 and 25.4 ms** — period ≈12.3 ms, the group spanning
~31 ms — and then a tail. Tail decay τ 37.4 ms real against our 36.9 ms;
§12's τ ≈ 47 ms is a little long but the right order.

Two defects:

1. **Burst period is 9.5 ms where the machine's is 12.3 ms** — about 25 %
   fast. `kit_808()` uses `period=480` frames at 48 kHz = 10.0 ms; ≈590 would
   match. §7's "≈10–12 ms" should be tightened to ≈12 ms.
2. **The tail has no attack, so the bursts do not stand clear.** Our envelope
   falls only to **0.25** between bursts; the real one falls to **0.02**. The
   real machine's tail *rises* — its overall peak is at 37 ms, after the
   third burst — whereas `E_CPTAIL` fires at full level at t = 0 and only
   decays, laying a plateau under the burst train. The gaps are the flam.
   Without them this is a buzz, and the clap is one of the three voices that
   say "808" (§16).
   **Fix:** give the tail envelope an attack, or charge it from the burst
   train rather than from the trigger.

Band-pass placement is fine: peak 1050 Hz against the real 999 Hz, power
centroid 1848 against 1612, body/air split 76.7/23.3 against 82.9/17.2.

### 4.6 CB — wrong in three ways, and the reference document's open item is now closed

![CB](img/drum-verification/CB.png)

Pitch is right: our 800.0 Hz against the machine's 823.6 Hz, and the machine's
second oscillator at 558 Hz against our 540 Hz. Both of those are the
**factory-trimmed** pair (TM1/TM2), so +2.9 % and +3.3 % is this unit's trim,
not our error. §13's hypothesis 3 — the cowbell is oscillators 5 and 6 — is
confirmed: every partial in the real cowbell is a harmonic of 558 or 824 Hz.

**(a) The tail is 3× too short.** The envelope panel is unambiguous. Fitted
over −3…−30 dB the real cowbell's τ is **98.0 ms** against our **30.0 ms**,
and its slow slope rings on past 700 ms where ours is dead at 240 ms
(t(−20 dB) 76.0 ms against our 50.9 ms). `E_CBB`'s 30 ms should be ≈100 ms.
An 808 cowbell that stops in a quarter of a second is not the sound.

**(b) The band-pass is in the wrong place — and we can now say where it
belongs.** §9 says of the cowbell filter: *"Treat the centre frequency as a
parameter to fit against a recording."* Fitting a 2-pole band-pass to 16
identified partials of the real cowbell, with the square's duty cycle and the
two gates' relative level free:

> **fc = 1100 Hz, Q = 2.8**, duty 0.452, the 558 Hz gate 1.9 dB below the
> 824 Hz gate — rms residual **2.8 dB** over 16 partials, worst 5.7 dB.

We ship 900 Hz Q 4.0. The consequence is measurable: our magnitude centroid is
1881 Hz against the machine's 2190 Hz, **−14 %**. This also settles two
loose ends in §9: the document's own inference of "0.9 kHz, Q 4–5, putting
540 Hz ≈15 dB down" is nearly right on the *level* (measured: 558 Hz sits
14.7 dB below 824 Hz) but the centre is ~200 Hz low and the Q too high; and
SOS-CB's "band-pass centred at 2.64 kHz" is **refuted** — 2.64 kHz fits the
data far worse than 1.1 kHz.

**(c) We generate intermodulation products the machine cannot.** Our spectrum
has a line at **260 Hz at −26 dB** and another at **1340 Hz at −14 dB**. These
are 800 − 540 and 800 + 540: difference and sum tones. The real machine is
**below −70 dB at 260 Hz** — a 44 dB discrepancy — and has nothing at
1340 Hz; its −14 dB line is at 1117 Hz, which is 2 × 558, a genuine harmonic.

The cause is structural. §9: *"Each oscillator has its own transistor gate
(Q15, Q14, 'exclusive gate (VCA)')"* — the machine gates the two squares
**separately** and mixes them afterwards, so they never multiply. `kit_808()`
routes `SRC_SQPAIR` (the *sum* of oscillators 5 and 6) through one
`NL_SWING` nonlinearity, which multiplies them. **Fix:** two paths, one per
oscillator, each with its own swing VCA, summed into `M_CBBP`.

---

## 5. Where we are better than the reference

The reference is one 40-year-old machine recorded in 1994, and some of what it
does is age and converters rather than design.

- **Reproducibility.** Our τ varies by less than 0.5 % across the three
  accents on every voice (BD 127.4 / 127.4 / 128.0 ms). The real machine's
  BD τ at a fixed DECAY 5.0 measures 138–230 ms across its five TONE files —
  knob positions that cannot affect decay. That is drift, and we do not have
  it.
- **Dynamic range.** Our BD envelope is a straight line to −70 dB. The
  reference recording floors out at −68 dB, and the clap recording at −40 dB,
  so the real tails are partly buried in 1994 converter noise. Our clap tail
  is clean 20 dB further down.
- **Snare mode stability.** Our two modes sit at 173.0 and 336.1 Hz on every
  hit. On the real machine `SD0010` reads its upper mode at 569 Hz instead of
  339 — a level-dependent shift in the second resonator we do not reproduce
  and should not want to.

The hats' flatness (§4.4) is the one place where "purer than the machine" is
probably a defect rather than a virtue.

---

## 6. What to change, in order

> **Status after contract revision 6 (§8):** 1 done (but the diagnosis was
> wrong — the level was 2.3 dB down, not 16 dB, and the band was the fault);
> 2 done, all three parts; 3 done for f0 and the attack window — the decay
> needed no change and the h2/h3 gap is the excitation shape, 17.20; 4, 5 and
> 6 not done. The priority order below is also superseded: a discrimination
> study over the whole kit puts the snare furthest from the machine and the
> bass drum closest, and puts **the excitation shape** above everything in
> this table.

| | voice | change | why |
|---|---|---|---|
| 1 | SD | raise `E_SDN` peak / `M_SDHP` amp by ≈16 dB; band-limit the snappy path around 4 kHz | half the instrument is missing; worst defect found |
| 2 | CB | `E_CBB` 30 ms → ≈100 ms; band-pass 900 Hz Q 4 → **1100 Hz Q 2.8**; split `SRC_SQPAIR` into two separately-gated paths | tail 3× short, centroid −14 %, 260 Hz tone the machine has not got |
| 3 | BD | f0 56 → **50 Hz**; Q 22.3 → ≈40; switch in the §14 attack preset; add h2/h3 | this is "the kick is really weak" |
| 4 | CP | burst period 480 → ≈590 frames; give `E_CPTAIL` an attack | the flam is the clap |
| 5 | CH/OH | broaden both high-passes; roll off our CH above 13 kHz | both ≈6 % bright, both too selective |
| 6 | LT/HT | add the pink-noise path (§4) | −28 dB of missing air; cosmetic next to the above |

## 7. Amendments this measurement suggests for `tr808-reference.md`

- **§2** — BD f0: the hardware says **49.6–50.6 Hz** across all 25 files and
  all knob positions. The "49–56" range is right but 56 is the chart's number,
  not a measurement; 50 is. The decay range "15 → 540 ms" is **confirmed
  exactly** (measured 15.2 → 532 ms). The BD's first 4 ms peaks at **250 Hz**,
  not the stated ≈130 Hz.
- **§3** — the snare's noise path needs a **low-pass or band-pass around
  4 kHz**; "2-pole HP 2.75 kHz Q 0.7" alone predicts energy flat to Nyquist
  and the machine measures a hump peaking at 3–5 kHz. The 173/336 Hz "later
  units" branch is **confirmed** (172.0 Hz measured).
- **§4** — the tom table is confirmed across the whole tuning range; no change.
- **§7** — the clap's burst period is **≈12.3 ms**, not "≈10–12 ms", and the
  tail envelope **rises** (peaks ~37 ms, after the third burst) rather than
  firing at full level.
- **§9 / §18** — the cowbell band-pass open item can be closed:
  **fc ≈ 1100 Hz, Q ≈ 2.8** by fit to a recording, 2.8 dB rms over 16
  partials. SOS-CB's 2.64 kHz is refuted.
- **§11** — the OH DECAY knob **saturates** above 7.5 (180 → 217 → 214 ms).
- **§13** — hypotheses 2, 3 and 4 are now confirmed by measurement of a real
  unit, not only from the schematic.

---

*Measured 2026-09-18 against `sounds-tr808-fischer` @ `85fbecf`, renders from
`model/drums_fx.py` as of the `drums` branch. Script:
`model/drum_verify.py`. Plots: `docs/img/drum-verification/`.*

---

## 8. Re-measured, 2026-09-18: one method withdrawn, three faults fixed

Everything above this section was measured with `model/drum_verify.py`. Four
of its findings do not survive re-measurement, and **the headline of §4.2 is
one of them**:

| | why |
|---|---|
| "the snare's noise is 16 dB too quiet" (§4.2) | the body/air split is invalid on a decaying one-shot (§8.0); the gap is 2.3 dB and the *band* is the fault (§8.1) |
| "the real bass drum's first 4 ms has its energy at 250 Hz" (§4.1) | an FFT-bin artefact; 4 ms at 44.1 kHz gives 250.6 Hz bins (§8.3) |
| "the BD decay is 45 % short" (§4.1) | not a decay fault: the kit's own f0 error propagating through τ = Q/(π f0) (§8.3) |
| "BD pitch, ours 56.0 vs real 49.8 Hz" (§4) | the direction is right, the number superseded — 50.70 ± 0.02 at the 12-o'clock condition (§8.3) |

The structural findings survive: the cowbell's shared gate, the snare's noise
band, the missing attack window. This section supersedes the rows it names;
the rest of the document stands. New measurements are `model/drum_fit.py`,
validated in `model/test_drum_fit.py`.

### 8.0 The method that failed, and how it was caught

`drum_verify.measure`'s **body/air energy split** — power above and below a
split frequency, from `drum_verify.spectrum` — applies a Hann window across
the whole analysis span. On a 500 ms span that weights t = 10 ms by **0.0039**
and t = 250 ms by **1.0**: a 48 dB tilt away from the attack and towards
whichever component decays slowest. On a decaying one-shot it therefore does
not measure energy; it measures the tail.

Caught by building the one case where the truth is exact. The snare's tonal
path and its noise path are separate paths into separate modes with linear
nonlinearities, so each renders alone and the two sum to the whole, and the
true noise share is arithmetic:

| our SD render, noise share | |
|---|---|
| **truth**, from the two separate renders | **18.55 %** |
| damped-mode subtraction (`drum_fit.noise_share`) | 18.90 % |
| the committed body/air split at 700 Hz | **1.25 %** |

The split is wrong by a factor of 15. On synthetic mixtures with a share set
by construction it under-reports by 5–9× whenever the noise decays faster than
the tone, which is exactly our snare's case. Both facts are now tests that
must keep failing for the old method
(`test_the_whole_span_hann_split_is_the_artefact_it_is_recorded_as`).

**The separator that replaces it** fits one damped sinusoid per body mode —
amplitude, frequency, τ and phase free, τ ≥ 5 ms so the shaped pulse cannot be
mistaken for a mode — and calls the residual noise, over a **stated 250 ms
window from onset**. Validated twice: against synthetic mixtures across
0–90 % (within 0.6 pp below a 30 % share, 1.5 pp above), and against the exact
share of our own render (18.90 % against 18.55 %).

This is the **fifth** measurement artefact in this voice's history, after the
four of §3, and the third of the same family: a window or a transform chosen
without checking what it does to a signal that decays.

### 8.1 SD — the noise level was never 16 dB down; the *shape* was the fault

**Withdrawn**: "noise 1.2 % of energy vs 47.3 %", "about 16 dB too quiet",
"our kit is sitting at roughly 2.5 on a 0–10 dial", and the §4.2 body/noise
table. All are the whole-span split.

The SNAPPY knob's transfer curve, from all 25 snare files, mean over the five
TONE positions, by damped-mode subtraction:

| SNAPPY | 0.0 | 2.5 | 5.0 | 7.5 | 10.0 |
|---|---:|---:|---:|---:|---:|
| noise share | 4.70 % | 4.96 % | **27.66 %** | 56.01 % | 71.89 % |
| noise/tone amplitude | 0.212 | 0.219 | **0.619** | 1.153 | 1.652 |
| dB | −13.5 | −13.2 | **−4.2** | +1.2 | +4.4 |

The curve is **flat from 0 to 2.5** — the pot's dead zone plus the separator's
own ≈4.7 % floor on this material — so a share below about 8 % cannot be
placed on the knob at all, and "ours sits at 2.5" was never readable from one
file. Ours measured a noise/tone amplitude of **0.477**, which interpolates to
**SNAPPY ≈ 4.4**, and the gap to the 12-o'clock setting the kit claims is
**2.3 dB**, not 16.

What *is* badly wrong is the band. The machine's snare noise, recovered as the
residual, against ours:

| % of noise energy | 0.7–1.5 k | 1.5–3 k | 3–5 k | 5–8 k | 8–12 k | 12–16 k |
|---|---:|---:|---:|---:|---:|---:|
| reference unit | 1.6 | 20.5 | **36.1** | 28.5 | 10.4 | 2.9 |
| ours (rev 5) | 0.1 | 2.6 | 15.7 | 22.8 | **30.2** | **28.6** |

**The shaping fix is one register** — the numerator. The level is the separate
SNAPPY setting above, and the three SD mode `amp`s are rebalanced with it so
the voice still peaks at its chart proportion.
`tr808-reference.md` §3 gives the snappy filter
as "2-pole HP 2.75 kHz Q 0.7". Keep that pole exactly and read it as a
**band-pass** instead of a high-pass and it fits the measured noise spectrum
to **1.9 dB weighted rms** against the high-pass's **5.2 dB** — as well as the
best unconstrained single mode (2938 Hz Q 0.75, 1.90 dB) and within 0.1 dB of
a two-mode cascade that would cost a spare mode and a path. So §3's numbers
were right and only the numerator was wrong; contract 17.22 records that the
schematic reading behind it is not settled.

### 8.2 CB — the difference tone, against the recording's own floor

§4.6(c) stands and is now bounded. Establishing the floor first, because
"70 dB below" means nothing above an unknown noise floor (0.5 s Hann,
2^18-point FFT, dB relative to the 824 Hz partial):

| | |
|---|---:|
| reference unit's difference tone, 264.96 Hz | **−67.8 dB** |
| recording's spectral floor, 230–290 Hz | median **−86.7 dB**, 90th pct −75.6 dB |
| ours (rev 5), 260.0 Hz | **−25.6 dB** |

The machine's line sits 18.9 dB above the median floor, so it is a real line
and the 42.2 dB discrepancy is above the floor by a wide margin. A second
sample set (undocumented provenance, so a cross-check only) agrees as a
*bound* rather than a measurement: nothing within ±10 Hz of its own difference
frequency rises above −69.7 dB. The
mechanism is demonstrated in isolation in DR 0010: separately gated squares
put the difference and sum tones at **−164 dB**, the arithmetic floor, where
gating the sum puts them at −8 dB.

### 8.3 BD — the pitch error, and the decay that followed from it

**The DECAY control is not a fault and has not been changed.** §4.1's "decay
is 45 % short" is withdrawn as a separate finding: the kit shipped
`tr808-reference.md` §2's decay table (Q = 22.3 at 12 o'clock) together with
Roland's chart's f0 = 56 Hz, and those two rows are incompatible. §2's τ
column satisfies τ = Q/(π f0) to 1.5 % at f0 = 49.4 Hz and only to 12.8 % at
56. Shipping that Q at 56 Hz gives τ = 127 ms where the same table says 144.
Fixing f0 to §2's own 49.4 Hz restores τ = 143.7 ms with **Q untouched**
(DR 0009).

Also withdrawn: "**the real bass drum's first 4 ms has its energy at 250 Hz**"
(§4.1.3). 4 ms at 44.1 kHz gives 250.6 Hz bins, so 251 Hz is bin index 1 and
the apparent peak tracks the bin spacing. The attack difference is real and is
established instead from **band energy over a stated interval**, filtered in
the time domain and then integrated, which has no such limit:

| % of energy in the first 4 ms | 20–80 | 80–150 | 150–300 | 300–600 Hz |
|---|---:|---:|---:|---:|
| reference unit (BD5050) | 0.6 | **41.2** | 52.3 | 5.8 |
| ours, rev 5 | 97.5 | 2.7 | 0.2 | 0.2 |
| ours, with §2's attack window | 73.4 | **22.3** | 4.2 | 0.1 |

and from the harmonics, measured over the whole hit at 0.08 Hz resolution:
the reference unit's h2 is **−44.0 dB** and h3 −52.8 dB against ours at −79.4
and −94.5. The attack window closes about half of the band-energy gap. The
rest is the **excitation shape** — the reference's pulse shaper, §2 — which
this revision does not implement and which contract 17.20 records as the next
thing to do.

Pitch, re-measured with a damped-sinusoid fit 20 ms after onset over a 300 ms
window rather than a peak of a windowed spectrum:

| DECAY | 0.0 | 2.5 | 5.0 | 7.5 | 10.0 |
|---|---:|---:|---:|---:|---:|
| f0 (Hz) | 51.48 | 51.18 | **50.70** | 50.95 | 51.62 |
| body mode τ (ms) | 14.5 | 47.3 | **178.0 ± 29.1** | 307.6 | 640.1 |

§4.1's 49.8 Hz is superseded by 50.70 ± 0.02 at the 12-o'clock condition. The
reference unit's τ is 23 % longer than the circuit table's 144 ms; that is
inside the ±50 % on Q that §12 calls normal between units, and the kit keeps
the reference's number with the gap tracked as contract 17.21 rather than
tuned away.

### 8.4 What changed in the kit, and what did not

| | rev 5 | rev 6 | authority |
|---|---|---|---|
| SD snappy filter | HP 2750 Hz Q 0.7 | **BP** 2750 Hz Q 0.7 | measured; §3's pole kept, numerator amended |
| SD snappy level | SNAPPY ≈ 4.4 | SNAPPY 5.0 on the measured curve | measured (knob curve) |
| CB oscillators | one gate on the sum | **one gate each** | verified in a source, §9 |
| CB band-pass | 900 Hz Q 4 (chosen) | **1100 Hz Q 2.8** (fitted) | measured; closes 17.16 |
| CB tail | τ 30 ms | **τ 100 ms** | measured (τ 98 ms) |
| BD f0 | 56 Hz (chart) | **49.4 Hz** (circuit) | verified in a source, §2 |
| BD Q / DECAY law | §2's table | **§2's table, unchanged** | — |
| BD attack window | absent | **130 Hz Q 6 for 4 ms** | verified in a source, §2 |
| tom pitch drop | absent | **×1.7, accent-scaled, 60 ms** | verified in a source, §4 |
| excitation shape | impulse | **impulse** — 17.20 | not done |

### 8.5 Amendments to `tr808-reference.md` that this section forces

Beyond §7's list, which stands:

- **§2** — the "What to implement (BD)" line says f0 = 56 Hz, which
  contradicts §2's own derivation of 49.4 Hz four paragraphs above it and is
  incompatible with §2's own decay table. Amended.
- **§3** — the snare's noise path is a **band-pass** on the stated pole, not
  a high-pass. §7 asked for "a low-pass or band-pass around 4 kHz" as an
  addition; it is not an addition, it is the numerator.
- **§9 / §18** — the cowbell band-pass open item is closed at 1100 Hz Q 2.8,
  and §9's "each oscillator has its own transistor gate" is now load-bearing
  rather than descriptive.

*Measured 2026-09-18 against `sounds-tr808-fischer` @ `85fbecf`, renders from
`model/drums_fx.py` at contract revision 6. Script: `model/drum_fit.py`,
validated by `model/test_drum_fit.py`.*

---

## 8.6 SD again — the burst's LENGTH and the partials' BALANCE (contract rev 7)

§8.1 fixed the snare's noise **band** and its **level**, and both still
measure right. This section is about the two quantities it did not touch. It
takes nothing from the withdrawn whole-span Hann split: the "3 % above 700 Hz
against a real machine's 51.5 %" that issue #26 quotes is *both halves* of
that artefact and is not used here, and re-measured honestly (rectangular
250 ms window from onset) ours was **29.4 %** against the machine's **32.3 %**
before any of the changes below.

### The two findings

| | ours, rev 6 | the machine, TONE 5.0 / SNAPPY 5.0 | ours, rev 7 |
|---|---:|---:|---:|
| snappy burst, T20 | **34.2 ms** | **69.3 ms** | 71.8 ms |
| upper partial over lower, a(336)/a(173) | **0.394 (−8.1 dB)** | **1.43 (+3.1 dB)** | 1.42 (+3.0 dB) |
| noise share (validated separator) | 30.9 % | 34.5 % | 28.0 % |
| body modes τ, low / high | 30.1 / 12.6 ms | 31.9 / 9.7 ms | 30.1 / 11.5 ms |
| noise band %, 0.7–1.5 / 1.5–3 / 3–5 / 5–8 / 8–12 / 12–16 k | 3.1 / 22.1 / 35.9 / 25.2 / 10.4 / 3.2 | 2.3 / 22.8 / 36.0 / 25.4 / 10.4 / 3.0 | unchanged |

**The burst.** The snappy envelope is the one part of this voice that really
is `docs/reduced-808-precedent.md`'s named hazard — an envelope into a VCA,
the volca beats' shortcut, "cut out digitally because the decay of the
bridged t-network would be too long". Here it was cut to half the machine's.
The bridged-T side is innocent: our two body modes already ring at the
machine's rates, which is the *opposite* of the cowbell's τ 26 ms against 98.

Measured as T20 of a **short-time RMS** (3 ms) of the hit band-limited to
1.5–8 kHz — a broadband voice, so an RMS envelope and not the analytic one —
per hit, from its own peak:

| file | TONE | SNAPPY | T20 | fitted τ | R² |
|---|---:|---:|---:|---:|---:|
| SD2550 | 2.5 | 5.0 | 63.2 ms | — | fit runs into the floor |
| SD5050 | 5.0 | 5.0 | 69.3 ms | — | 0.34 |
| SD5075 | 5.0 | 7.5 | 67.5 ms | — | 0.47 |
| SD7550 | 7.5 | 5.0 | 67.6 ms | **29.1 ms** | 0.98 |
| SD1050 | 10.0 | 5.0 | 67.4 ms | **30.0 ms** | 0.98 |
| SD5010 | 5.0 | 10.0 | 77.9 ms | **34.2 ms** | 0.99 |

T20 is the robust column and the one asserted; the fitted τ is quoted only
where the noise stands far enough above the 16-bit floor for the fit to mean
anything (R² ≥ 0.97), and those three agree with it. **τ ≈ 30 ms**, where
`tr808-reference.md` §3 infers 15.5 ms from R186 × C51 — which is the
**charge** path. §3's own prose already says the snap is "a 30–40 ms burst",
which the measurement agrees with and the RC does not. Contract 17.25 records
that we have not established whether the discharge path is a different
resistance or whether Q48's VCA law stretches the envelope.

**The balance.** Roland: "The output ratio of the two can be changed by VR8
(TONE)" (SN p.6). At the 12 o'clock condition every preset in `kit_808()`
claims, the machine puts the 336 Hz partial at **1.43×** the 173 Hz one with
the snappy path up and **1.41×** with it down — the same number, which is
what says the quantity belongs to the resonators and not to the noise. Rev 6
shipped **0.394**. Eleven decibels of the snare's upper partial were missing,
and that partial is what a listener calls the snare's front end — the thing
the T-8 review says you lose when you push the decay, and the thing the volca
workaround restores by layering a clap.

One methods note, because this voice's history earns it. The fitted partial
amplitudes depend on where the analysed window starts: hand `noise_share` a
render that begins exactly at the onset and it returns 0.505 for revision 6's
snare, hand it one with a few ms of silence in front — as every reference WAV
has, and as `trim_onset` then removes — and it returns 0.394, because the
optimiser lands in a different amplitude/phase trade-off for the fast mode.
**0.394 is the figure quoted throughout**, measured the same way on both
sides: leading silence present, `trim_onset` applied, 250 ms window. The gap
to the machine is 11.1 dB either way, but the two numbers are not
interchangeable and the acceptance test states which one it makes.

### The TONE law was read wrong, and that is half of the SD 8.8

`docs/discrimination.md` tabulated **SD TONE → "body ring, *not* pitch",
28.5 / 27.4 / 13.6 ms**. That is the fifth instance of this voice's recurring
error and the third of one family: **a single τ fitted to a sum of two modes
that decay at different rates.** Fitted separately, the machine's two modes
are 29–39 ms and 5–11 ms at *every* TONE position, and what moves across the
knob is their ratio — 0.0015 → 2.205 in energy, **31.7 dB**.

Constructed so the truth is exact — two damped sinusoids with decays **fixed**
at 30 and 9.7 ms and the amplitude ratio set to the machine's own 0.48 / 1.43
/ 10.1:

| a(336)/a(173) | one-τ fit of the sum | the machine measured |
|---:|---:|---:|
| 0.48 (TONE 0) | 30.1 ms | 28.5 ms |
| 1.43 (TONE 5) | 28.8 ms | 27.4 ms |
| 10.1 (TONE 10) | **11.4 ms** | 13.6 ms |

The whole of the old law is reproduced by a balance change with nothing
decaying differently. It is now
`test_discrimination.test_a_single_tau_on_two_modes_reads_a_balance_change_as_a_decay_change`,
and it matters beyond bookkeeping: the study's `kit_at` wrote that τ into
**both** of our body modes, so at every knob position our upper partial rang
up to three times too long while the balance — the thing the knob moves — sat
still. The discrimination study was driving our snare wrongly, so part of the
distance it reported was its own.

### What it is worth, on the study's own yardstick

Knob-equivalent separation: how far the machine's own knob must travel before
it looks this different from itself.

**The full study, run twice — once on `drums` as PR #14 has it, once on this
branch — same corpus, same split hash, same estimator, one arm (`ours`):**

| voice | PR #14 (rev 6) | this branch (rev 7) |
|---|---:|---:|
| **SD** | dist 26.0, **knob-equiv 7.6**, acc 1.000 | dist 18.4, **knob-equiv 3.4**, acc **0.938** |
| BD | dist 17.2, 2.5 | dist 17.2, 2.5 |
| LT | dist 30.9, 6.7 | dist 30.9, 6.7 |
| OH | dist 28.8, 6.9 | dist 28.8, 6.9 |
| HT | dist 32.0, 7.2 | dist 32.0, 7.2 |

Every other voice's distance is identical to the digit, which is the control
that says these changes touched the snare and nothing else. **The snare stops
being our worst voice**: at 3.4 it sits below LT, OH and HT and just above the
kick, where it was above all four. Its balanced accuracy comes off the ceiling
for the first time on this voice. (The bass drum's *accuracy* moves too,
1.000 → 0.844, although its distance does not: one classifier is fitted over
all the voices at once, so a voice that stops being trivially separable
changes the fit the others are scored under. The distance, which is what the
knob-equivalent is read off, is unmoved.)

**Note the baseline.** `docs/discrimination.md` publishes **SD 8.8**, and that
scorecard describes the *pre*-revision-6 kit; revision 6's band and level fix
had already moved it to 7.6 before this section began. 8.8 → 7.6 → 3.4.

Separating the two fixes needs an SD-only run (the z-space is built from the
clips in the run, so its distances are on their own scale and only
within-run comparisons are meaningful):

| arm, SD-only run | feature distance | knob-equivalent |
|---|---:|---:|
| the kit and law as PR #14 has them | 26.0 | 7.6 |
| kit fixed only (balance + burst) | 22.5 | 5.0 |
| law fixed only (TONE as the ratio) | 23.1 | 5.0 |
| **both** | **18.4** | **3.4** |

Neither fix is redundant and neither is the whole of it, and each alone is
worth about the same.

Two honest limits. The classifier still separates the snare at 1.000 on the
full run, so the verdict stays *known defect remains* — 5.0 is "closer", not
"indistinguishable", and this is a screening result about these evaluators,
not a listening test. What is left at 3.4 has not been identified; the effect
sizes after the fix are spread thinly across the mid bands and the late
segments rather than concentrated anywhere, which is what a residue looks
like rather than a sixth defect. And the study's per-voice percentage diagnostics
(`share_mid +38290 %`) are ratios against near-zero denominators at the
held-out SNAPPY positions; they are not a fourteen-thousand-fold error and
should not be read as one.

### What changed in the kit, and what did not

| | rev 6 | rev 7 | authority |
|---|---|---|---|
| SD upper partial amp | 0.00369 (ratio 0.394) | **0.008865** (ratio 1.42) | measured, 17.24 |
| SD lower partial amp | 0.0036 | **0.002673** | the pair scaled to hold the kit's 0.46 FS peak |
| SD snappy rate | τ 15 ms (§3's RC) | **τ 30 ms** (measured) | measured, 17.25 |
| SD snappy peak | 0.5 | **0.3046** | holds the machine's 27.7 % share at the new rate |
| SD snappy filter | BP 2750 Hz Q 0.7 | **unchanged** | rev 6's band still measures right |
| SD body τ | 30.1 / 12.6 ms | **unchanged** | already the machine's |
| everything else in the kit | | **unchanged** | |

*Measured 2026-09-18 against `sounds-tr808-fischer` @ `85fbecf`, renders from
`model/drums_fx.py` at contract revisions 6 and 7. Scripts: `model/drum_fit.py`
and `model/test_808_acceptance.py`'s `sd_noise_t20` / `sd_partial_amps`, each
with an injected-defect control that must reject the revision-6 value. Audio:
`model/drums_fx_render.py --only sdab` and `--only sdgroove`.*

---

## 9. PR #14's acceptance failures, classified

PR #14 was reported as carrying "14 acceptance failures". Reproduced, they are
what `main`'s copy of `model/test_808_acceptance.py` does when it is run
against the drum model on the `drums` branch — which is the comparison CI
could not make, because the suite lives on one branch and the model it
measures lives on the other. Exactly:

```sh
git show origin/main:model/test_808_acceptance.py > model/_mainacc_tmp.py
TR808_STRICT=1 .venv/bin/python -m pytest model/_mainacc_tmp.py -q   # 13 failed, 40 passed
             .venv/bin/python -m pytest model/_mainacc_tmp.py -q     # 13 failed, 37 passed, 3 xfailed
```

**13 in either mode, 16 distinct test ids across the two** — the strict run
shows the tracked defects failing, the default run shows the ones that have
started passing. Neither run alone shows all of them, which is worth knowing:
a suite with strict xfails has to be run both ways to be read.

**None of the sixteen is a live defect in the model.** Every one is either an
expectation the branch has since superseded or a tracked defect that has been
fixed, and the branch's own copy of the suite is 53/53 green under
`TR808_STRICT=1` (314/314 for `model` + `spec`).

| # | test | class | the ground truth now, and where it comes from |
|---|---|---|---|
| 1–3 | `test_control_bd_decay_coefficients_carry_the_intended_decay[0.1/0.5/0.9]` | outdated expectation | two changes at once: the DECAY knob is the panel's 0–10, not VR6's 0–1 (`tr808-reference.md` §2 tabulates VR6; `drums_fx.BD_DECAY_Q` is keyed on the panel), and f0 is the schematic's **49.4 Hz**, not the chart's 56 (DR 0009) |
| 4 | `test_control_body_presets_match_the_reference_table[BD…56.0…]` | outdated expectation | 49.4 Hz (DR 0009) |
| 5–7 | `test_bd_rendered_decay_at_each_setting[0.1/0.5/0.9]` | outdated expectation | same pair of changes; at knob 0.9 on the old scale the render is τ 143 ms against an expected 352 |
| 8 | `test_bd_decay_control_spans_roland_s_chart_range` | outdated expectation | same |
| 9–10 | `test_bd_attack_window_is_written_into_the_coefficients`, `…_is_audible_in_the_first_half_cycle` | **tracked defect CLOSED, and it was hiding** | contract 15.7.1 emits the 130 Hz / Q 6 / 4 ms window on every BD hit. See below — these two are the interesting ones |
| 11 | `test_sd_noise_balance_matches_a_real_machine` | outdated expectation — **method and target both withdrawn** | §8.0/§8.1: the 700 Hz whole-span Hann split is invalid on a decaying one-shot (1.25 % against an exact 18.55 %). Replaced by the validated separator; the machine at SNAPPY 5.0 carries **27.66 %** |
| 12 | `test_sd_noise_highpass_corner` | outdated expectation | `AttributeError: module 'drums_fx' has no attribute 'M_SDHP'`. The mode is `M_SDN` and its numerator is a **band-pass**, not a high-pass (§8.1, contract 17.22) |
| 13 | `test_meta_render_manifest_describes_what_was_played` | outdated expectation | asserts the manifest's BD f0 is 56.0; DR 0009 makes it 49.4 |
| 14–15 | `test_tom_pitch_falls_during_the_ring[LT/HT]` | **unexpectedly passing — good news** | contract 15.7.1 sweeps the tom f0 from ×1.7 over 60 ms, accent-scaled (`tr808-reference.md` §4). Removed from `KNOWN_DEFECTS` |
| 16 | `test_cowbell_decay_matches_a_real_machine` | **unexpectedly passing — good news** | `E_CBB` is the measured τ = 98 ms, not 30 (§4.6). Removed from `KNOWN_DEFECTS` |

**Zero real defects.** The reconciliation was already done, in
`drums@e7c9ae6`; this section is the audit of it, and the audit found the
tolerances were kept or tightened, not loosened — `56.0` became
`dx.BD_HZ` with the same ±1 %/±3 %/±10 % bands, and the SD filter test gained
two assertions and went from ±20 % on the corner to ±2 % on the pole.

### The one that is worth reading twice

Entries 9–10 were **`KNOWN_DEFECTS` entries that were still failing for a
different reason than the one recorded.** The defect they were written for —
"`kit_808()` writes one coefficient set per voice and never switches it, so
the bass drum has no attack window" — was fixed by 15.7.1. They stayed red
because the *same tests* also asserted the chart's 56 Hz, which DR 0009 had
made wrong. A strict xfail is supposed to go red the moment its defect is
fixed, and force the entry out of the table. Here a second, unrelated stale
expectation kept the xfail satisfied, and the closure would have stayed
invisible for as long as the two were fixed separately.

This is not hypothetical bookkeeping: it is the mechanism by which a defect
table stops being a defect table. A tracked-defect marker that can be
satisfied by *any* failure records "this test fails", not "this defect
exists". Worth a rule: when a `KNOWN_DEFECTS` entry fires, check that the
failure is the recorded one.

### One gap the audit found, now closed

`main`'s `BD_DECAY` table was three literals — `(0.1, 5.2, 0.029)` and its
pair — and the reconciliation replaced it with values derived from
`dx.bd_decay_q()` and `dx.BD_HZ`. That is right for the *control* test (it
round-trips the register encoding) but it leaves the rendered-decay tests
comparing the model against itself: change `BD_HZ` and the reference τ moves
with it, so nothing is left that can catch a wrong f0.

`tr808-reference.md` §2 tabulates the DECAY knob as a Q **and** as a τ, and
`τ = Q/(π f0)` closes those two columns to **1.5 % at 49.4 Hz** and is out by
up to **12.8 % at 56 Hz**. That is DR 0009's argument in one line of
arithmetic, and it was asserted nowhere. It is now
`test_bd_decay_table_is_self_consistent_at_the_schematic_s_f0`, five
parametrised cases, with
`test_meta_bd_tau_column_rejects_the_chart_s_f0` as the injected-bug control
that puts 56 Hz back and requires the table to stop closing.

---

## 10. The complete machine — all sixteen sounds (contract revision 10)

Revision 8 shipped **eight** circuits. This section is the evidence for the
three that were added — **MT/MC**, **CL/RS** and **CY** — and for the four
sounds that were already reachable on circuits we had and had never been
switched to: **LC**, **HC**, **MC** and **MA**.

The reference set is the same one §1 describes, and it has **all sixteen
voices**, not the eight §4 used. That turned out to matter more than the
schematic did: three of the reference document's statements about the cymbal do
not survive contact with a recording of a real one.

### 10.1 What was built

Sixteen sounds are **eleven circuits**: five carry two sounds each on a panel
switch and cannot sound together, so the second sound of a pair is a register
image on the circuit it shares — `drums_fx.preset_writes(name)` — and not
hardware. The block therefore grew:

| | revision 8 | revision 10 | why |
|---|---|---|---|
| stops | 8 | **11** | BD SD LT HT CH OH CP CB **MT CL CY**; the first eight keep their indices |
| modes | 12 | **16** | eight filters, eight bridged-T bodies |
| modes with a numerator (`NUMS`) | 6 | **11** | `modal_dp` gives a numerator only *below* `NUMS`, so this is the number that decides how many filters the bank can hold. Modes 8–10 are RAW bodies sitting in numerator-capable slots: three spare filters |
| envelopes | 12 | **18** | +1 mid-tom exciter, +2 RS/CL (exciter and gate), +3 cymbal VCAs |
| paths | 16 | **23** | +1 MT, +4 RS/CL, +3 cymbal |
| PATH register | 22 bits | **25 bits** | the envelope and destination fields went 4 bits → 5 |

The **PATH word had to grow, and it is not a nicety.** At four bits the
envelope field could address twelve envelopes, and `DEST_MIX` was 15 — which at
`MODES = 16` is also *mode 15*, so the last mode could never be a path's
destination. Both fields are five bits now; the sentinels moved with them
(envelope 31 reads full scale, destination 31 is the mix bus).

**The register map moved with it, and that was a latent defect rather than
tidiness.** Eighteen envelopes span `0x40..0x87` and would have collided with
`PATH` at `0x80`; sixteen modes based at `0xC0` span `0xC0..0xFF`, so **the last
mode's `num` register would have been `0xFF` — which is RESET.** `drum_regs.v`
decodes RESET as a continuous assign *outside* the write decoder, so this is not
a decode-priority question that ordering could fix: the address would have meant
both things at once and writing that coefficient would have reset the drum
section mid-kit. `PATH` moved to `0x90` and `MODE` to `0xB0`.
`test_808_acceptance.test_control_the_drum_page_has_no_address_that_means_two_things`
is the check, with the revision-8 map fed to the same checker as its injected
control, and `INJECT_BUG_DRUM_RESET_ALIAS` in `drum_regs.v` is the same defect
in RTL — `verify_synth_top.py --inject DRUM_RESET_ALIAS --expect-fail` catches
it at the chip's pins.

### 10.2 Starting red

`TR808_STUB=rev8` renders the **eleven-stop** block with only revision 8's
**eight** voices programmed: MT, CL and CY have their stops, modes, envelopes
and paths, and nothing is written to any of them. That is "the right ports and
no behaviour" for exactly the three new circuits, and it is a sharper control
than a silent block — a silent block fails everything and so proves nothing
about what a new test is sensitive to.

Run before the voices existed, **19 of the new assertions failed and 14 passed**,
and which 14 is the interesting part: the LC, HC and structural rows pass,
because those sounds ride on circuits revision 8 already had. So the stub could
not start those red, and they get their own injected control instead —
`test_meta_pair_tests_reject_the_other_half_of_the_pair` runs each pair's test
against the *other* half's image and requires it to fail. A test that renders
"LC" and measures 185 Hz proves nothing unless it would have rejected the tom
sitting in the same mode.

### 10.3 The cymbal — three of reference §10's claims do not survive measurement

Every independent source calls this the hard one, and io-808's author names the
cymbal and the rimshot as his two failures. So it was measured rather than
reasoned about. Two estimators were added for it, each checked against a signal
with a known answer before anything was quoted (`test_audio_measure`):

- **`schroeder_t20`** — the backward-integrated energy curve. For a single
  damped sinusoid it returns exactly `ln(10)·τ`, the same number `t20_from_tau`
  gives, with no fit and no shape assumption; the cymbal's envelope is three
  exponentials and has no single τ at all. It **refuses** a recording that was
  cut before it decayed — a truncated decay reads short by an amount that
  depends on where the cut is, which is indistinguishable from a real short
  decay. (The first reading of this section claimed `ma8`, `rs8` and `cl8` were
  truncated, from an envelope printed against the file's start instead of its
  onset. They are not: all three reach −96 dB or lower on the energy curve
  inside their 250 ms. They are short because the voices are short.)
- **`band_energy`** — the energy split by zero-phase filtering, *not* by summing
  FFT bins. `spectrum` applies a Hann window, so on a decaying voice it weights
  the middle of the file and reports the **tail's** spectrum. On the real
  cymbal the two disagree by a factor of four in 5–9 kHz: the windowed FFT says
  12 %, the energy split says 45 %, and both are "right" about different
  questions. They agree exactly on a stationary two-tone signal, which is how
  each was checked.

Measured on `cy8/CY5025.WAV` — TONE 5.0, DECAY 5.0, Roland's own chart
condition — against a render of exactly the same length (2.00 s, which matters:
the energy integral runs to the end of the array, so an unmatched window moves
the answer by 7 %):

| | <2 kHz | 2–5 k | 5–9 k | 9–13 k | >13 k | τ | t(−20 dB) | Schroeder T20 |
|---|---|---|---|---|---|---|---|---|
| a real TR-808 | 1.1 % | 10.3 % | 53.2 % | 23.3 % | 6.0 % | **256 ms** | **308 ms** | **798 ms** |
| ours | 1.3 % | 6.3 % | 58.4 % | 15.3 % | 6.7 % | **243 ms** | **318 ms** | **903 ms** |

and the machine's strongest spectral line is at **3153 Hz** — the *second*
band-pass, the one the hats do not use.

**Three decay numbers, because one of them can be matched while the voice is
still wrong.** An earlier fit matched the Schroeder T20 to 3 % (824 ms against
798) and its audible decay was still **46 % long** — τ 374 ms against the
machine's 256 — because the energy integral will trade an over-long bright band
against an under-weight low tail and report the sum as right. The shipped fit
searches all four at once.

**What that overturns in `tr808-reference.md` §10:**

1. **The long tail is the LOW band.** 2–5 kHz measures T20 1244 ms against
   745 ms at 5–9 kHz on the same file. §10 calls the low band "fixed, medium"
   and gives the DECAY knob to a *high* band.
2. **The DECAY knob moves every band**, not only the middle one: across the
   five files every band's T20 scales by ≈3× from knob 0 to knob 10.
3. **The ≈10.5 kHz stage behaves like a band-pass.** The machine has a 23 %
   shoulder at 9–13 kHz with only 6 % above 13 kHz. A two-pole *high*-pass with
   a `(1 − z⁻¹)²` numerator cannot make that shape — its response keeps rising
   toward Nyquist, and at an 11.7 kHz corner it puts **42 %** of its output
   above 13 kHz. A band-pass at 10.5 kHz does have the machine's shape.

So the model's three cymbal bands are: the 3.45 kHz band-pass → swing VCA →
straight to the mix bus (longest envelope); the 7.1 kHz band → swing VCA →
the 11.7 kHz high-pass (shortest); and the 7.1 kHz band → swing VCA → a
**10.5 kHz band-pass** (the DECAY envelope). The first two filters already
existed for the hats and are shared — a linear filter of a sum is the sum of
the filtered parts, so sharing is exact and costs nothing.

**What is still wrong, precisely.** The 9–13 kHz shoulder is about a third
short and the missing energy sits in 5–9 kHz instead. It wants a *second*
resonant post-filter for the high band, and with sixteen modes the bank has one
to spare for it, not two: reference §10 names three high-passes and the model
has two. **Hh1 — the 2.5 kHz stage on the low band — is the documented
omission**, and dropping it costs 1.7 % of the energy below 2 kHz where the
machine has 1.1 %. Keeping Hh1 instead and dropping the 10.5 kHz band fits the
machine's band split about **half as well** on the same objective, which is why
it went the way it did. A seventeenth mode would close
it; the bank's state arrays pad to a power of two, so the seventeenth is a
+31 % step and not a 6 % one.

**This is how close the cymbal got.** Its total decay is within 3 % of a real
machine, its long ring is on the right band-pass at the right frequency, its top
octave is right, and it is unmistakably not a long hi-hat. Its shimmer is
duller than the machine's, by a known amount, for a known reason.

### 10.4 The rimshot, the claves and the maracas

**RS** is two bridged-T networks (455 Hz Q 6.7 and 1786 Hz Q 13.5) summed into
the swing VCA — "the distortion is the sound; do not skip it". It measures
τ 4.30 ms and t(−20 dB) **9.60 ms** against the machine's **3.24** and
**9.00**, and Roland's chart's 10 ms.
`test_rimshot_is_distorted_and_that_is_the_sound` renders the same voice with
the nonlinearity set to LIN and nothing else changed, and requires the
harmonics above 455 Hz to be materially louder with the VCA than without it.

**One thing had to be traded against the other, and the measurement said
which.** Both taps go through the swing VCA, and driving the tanh into its rail
turns the resonators' ring into a flat-topped burst whose decay is then the
*gate's* 22 ms rather than the resonators' 4.7 and 2.4 ms. With the exciter at
the kit's usual 0.25 the rimshot measured τ 5.55 / t(−20 dB) 13.85 — **50 %
long**. Dropping the exciter to 0.06 and raising the gate's peak to hold the
level gives 4.30 / 9.60 with the distortion intact.

**One deviation, stated plainly.** The circuit sums the two resonators *before*
the VCA; this block's paths each carry one source, so it distorts them
separately — `nl(a) + nl(b)` where the machine makes `nl(a + b)`. The
intermodulation products the machine has are therefore absent. (The reverse
deviation is what DR 0010 fixed on the cowbell, where the machine gates its two
oscillators separately and rev 5 summed them first, putting a 260 Hz difference
tone 42 dB above the machine's.) Closing it needs one more mode as a
pass-through summing node, and that mode went to the cymbal instead.

**CL** is the same circuit with the second network retuned to 2500 Hz and wired
for high Q, stopped by Q74's ≈22 ms gate. It measures 2500 Hz, τ 11.8 ms and
t(−20 dB) 26.9 ms against the machine's **2422 Hz**, **9.61 ms** and
**22.6 ms**, and the chart's 2500 Hz / 25 ms — about 19 % long, and the
cleanest match of the three new circuits.

**MA** is the clap's circuit with SW12 thrown: the same noise source and the
same buffer, the 1071 Hz band-pass retuned to Q68's 10.6 kHz high-pass and the
three-burst envelope replaced by one ≈12 ms decay.

Its **total length is right and its shape is not**, and the recording says so
precisely. The machine's maracas **rises for 18.2 ms** and then falls with
τ 2.65 ms, reaching −20 dB 9.0 ms after its peak: about a 28 ms event.
Reference §8 has the attack network that does it — C135 0.1 µF with R344/R345.
Ours has an attack of 0.8 ms and τ 11.6 ms, t(−20 dB) 27.0 ms: also about a
28 ms event, and inside Roland's chart's 25–35 ms, but shaped the other way
round. **The envelope generator cannot ramp up.** It fires to a peak and
decays; its `hold` field tops out at 255 frames — 5.3 ms — which is not the
machine's 18. That is the one hardware feature the maracas wants and does not
have, and it is why the decay is asserted against the chart's total rather
than the machine's τ.

It is also **too bright**: our spectral peak is 11.8 kHz against the machine's
8.7 kHz, power centroid 12.9 kHz against 10.7 kHz. The cause is the same
digital artefact as the cymbal's missing shoulder — a two-pole high-pass with a
`(1 − z⁻¹)²` numerator keeps rising toward Nyquist, so the observed peak sits
*above* the 10.6 kHz pole rather than at it. §4 already recorded the same
effect on the closed hat (ours 12.4 kHz against the machine's 11.7).

### 10.5 The congas — reference §4's Q column is amended

The three **tom** rows of reference §4's component-value table land on a real
machine within 3 %. Its three **conga** rows are long by 12–30 %. The congas'
Q is therefore taken from the machine, at the chart's f0; reference §1.7
already says every high-Q figure is a ±50 % nominal, and Q is exactly the
quantity that moved.

| | reference §4 | a real TR-808 | error | kit ships |
|---|---|---|---|---|
| LT | τ 88.4 ms | 87.6 ms | +0.9 % | Q 25 (reference) |
| MT | τ 56.6 ms | 57.7 ms | −1.9 % | Q 24 (reference) |
| HT | τ 43.0 ms | 41.7 ms | +3.1 % | Q 25 (reference) |
| LC | τ 94.6 ms | **76.9 ms** | +23 % | **Q 44.7** (machine) |
| MC | τ 43.2 ms | **38.7 ms** | +12 % | **Q 34.0** (machine) |
| HC | τ 44.6 ms | **34.3 ms** | +30 % | **Q 43.1** (machine) |

All six measured with this file's own `decay_fit` over −3…−30 dB; the conga
fits are R² 0.999.

**One latent defect fell out of the congas.** `hit_writes` emitted the toms'
diode pitch sweep (contract 15.7.1) from **hard-coded** 90 Hz and 185 Hz. A
conga is the same circuit at a different tuning, so switching to one would have
swept the mode from a *tom's* frequency down to the conga's — a 1.5× downward
glide at the start of every mid conga — and it would also have silently
overwritten a host that had retuned a tom, which is the same fault
`bd_attack_writes` had to be fixed for once already. It now reads (f0, Q) back
out of the register image with `poles_from_regs`.

### 10.6 Two stale rows in `drum_verify.SPEC`

`SPEC` is what a per-voice regression compares a render against, and two of its
rows had outlived their evidence:

- **CB carried τ 22 ms** — Roland's chart 50 ms read as 2.3 τ. **DR 0010 moved
  the cowbell's tail to ≈100 ms on purpose**, because the real machine measures
  98 ms (§4.6: "the tail is 3× too short… `E_CBB`'s 30 ms should be ≈100 ms").
  The kit was fixed and this table was not, so it went on pointing at a number
  the project had already withdrawn — and a reporter reading it calls a
  *correct* cowbell 4× wrong. Re-measured here off `cb8/CB.WAV`: **92.7 ms**,
  R² 0.887. Set to the published 98.0.
- **CP carried τ 47 ms** — which is reference §7's `E_CPTAIL` RC, a *register*,
  not the voice's decay. The clap's envelope is three bursts and a tail and
  `decay_fit` measures the compound. `cp8/CP.WAV` measures **33.0 ms** (R² 0.919)
  against our 34.5, so the voice was right and the comparand was a different
  quantity. Set to 33.0; the 47 ms is kept in the row's `note`.

Both are the same failure — a claim outliving its evidence — so **every row now
carries a `source` field** saying where its number comes from, and rows whose
value is a hardware measurement name the file. The six rows left on a schematic
or chart figure are the ones no recording improves on: BD (DR 0009's computed
f0), SD, LT and HT (all already within 3 % of the machine) and the two hat rows,
whose τ is a knob position rather than a fixed value — `OH`'s 196 ms is the
chart at DECAY mid and the kit deliberately ships 150 ms.

`drum_verify.py` now takes `--voices` and covers all sixteen.

### 10.7 The run that closed it

The branch was harvested and committed by the coordinator before this run
landed, with the note "its final verification run had not landed when this was
committed". It has now, on the committed tree:

| | result |
|---|---|
| `model/test_808_acceptance.py`, `TR808_STRICT=1` | **102 passed**, `KNOWN_DEFECTS` empty |
| `test_drums_fx`, `test_audio_measure`, `test_drum_fit`, `test_modal_fixed` | **160 passed** |
| `spec/reference/gen_tables.py --check` | every table image and hash matches the model (Appendix G's kit re-pinned) |
| `rtl-sketch/verify_drums.py` | **bit-exact over 191,560 frames**, 117 clocks/frame of the 256 |
| `verify_drums` negative controls | 9 of 9 caught: `DRUM_ENV_FLOOR`, `DRUM_LEVEL_TRIG`, `DRUM_LFSR_TAP`, `DRUM_TAP_NOSAT`, `DRUM_LAST_PATH`, `DRUM_SQ_LONE`, `MODAL_NUM_HOLD`, `MODAL_EXC_NOCLEAR`, write jitter |
| `rtl-sketch/verify_ctl.py` | 202 writes reached the port as sent; the rev-1 link control caught |
| `rtl-sketch/verify_synth_top.py` | 2,118 I2S periods decoded from the wire, every one identical to the model |
| `verify_synth_top --inject DRUM_RESET_ALIAS` | **caught** — the 0xFF collision is detectable at the chip's pins |

**One of the meta tests had to be fixed before it was worth anything, and the
bug is worth naming.** `test_meta_rev8_stub_is_red_on_the_new_circuits_and_green_on_the_old_ones`
originally split the suite with a pytest `-k` expression, one of whose terms
was `hat_`. `-k` is a substring match, and `hat_` matches **"w[hat_]is"** and
**"t[hat_]is"** — so `test_cymbal_band_split_against_the_machine_and_what_is_still_missing`
and `test_rimshot_is_distorted_and_that_is_the_sound` were silently pulled into
the group that had to stay *green* under a stub that blanks their circuits. The
meta test failed for a reason that had nothing to do with what it was checking.
It now parses failing **node ids** out of one stub run and checks two things
against explicit lists: every test naming a circuit revision 8 did not have is
red, and nothing else is.

### 10.8 The pinned kit table moved — and a hash is not the evidence it is right

`KIT808` is pinned by SHA-256 in the contract and in
`spec/reference/test_tables.py`, and adding six sounds moved it:
**`7ea9a2e3…` → `feb8c6fd…`, 100 → 147 writes.** Re-pinning it is not
validation. A broken generator produces perfectly reproducible wrong tables, so
a matching hash proves the output is *stable*, never that it is *right*. Three
separate things were done instead, and only the third is evidence of
correctness:

**1. It regenerates from the model, not from disk.** `gen_tables.py` recomputes
every table by calling `drums_fx.kit_808()` and compares; deleting
`tables/kit808.hex` and regenerating reproduces the committed image byte for
byte at the same hash.

**2. Exactly one pinned table moved, and the diff is fully explained.** Checked
against revision 9's pins *before* accepting the new one —
`test_exactly_three_pinned_tables_have_ever_moved` now asserts this, so
re-pinning cannot hide a second table moving at the same time:

| | |
|---|---|
| moved | `KIT808` only |
| byte-identical | `NOTE_INC`, `SINE_Q256`, `SINE_FULL1024`, `TANH16`, `TANH16_ROM`, `NOISE64`, `EXP_ROM65`, **and `G_ROM128` / `K_ROM32`**, which revision 9 had just moved |

The 147-against-100 diff, keyed by **voice and path name** rather than by
address — because both the mode block and the path block moved, and an
address-keyed diff would have shown 31 "changes" that are only renumbering:

- **Nothing removed.** Every write revision 8 made, revision 10 still makes.
- **Every revision-8 mode is byte-identical** at its new index — all four
  registers of BD, SD-lo, SD-hi, LT, HT and the six filters.
- **Every revision-8 envelope is byte-identical.**
- **Every revision-8 path decodes to the identical meaning** — same source,
  same two envelopes, same nonlinearity, same attenuation, same destination —
  even though *every raw path word changed*, because the field layout went from
  22 bits to 25. That is the one place a silent change could have hidden, and
  it is the reason the comparison decodes the words instead of comparing them.
- **The 47 added writes are exactly** the 5 new modes, the 6 new envelopes and
  the 9 new paths, and nothing else.

**3. The validation is the acceptance suite, and the hash is only
tamper-evidence.** 102 tests under `TR808_STRICT=1` play the table and measure
what comes out — against Roland's chart, against the schematic and against a
real TR-808. The hash answers "did this change"; it can never answer "is this
correct". That distinction is the same one the CP row taught (§10.6): a
comparand can look authoritative and be measuring a different quantity.

---

## 11. The excitation shape — the mechanism is right, the arithmetic blocks it

§8.3 closed with the attack window and left a row open: *"the rest is the
**excitation shape** — the reference's pulse shaper, §2 — which this revision
does not implement and which contract 17.20 records as the next thing to do."*
§8.4's table still reads `excitation shape | impulse | impulse — 17.20 | not
done`. This section is the measurement that was supposed to decide it, and it
decides it against shipping anything to the kit — for a reason that is not
about the excitation at all.

Everything below is printed by `model/bd_excitation_probe.py`, whose own
preconditions are §8.3's two anchors: it REFUSES unless the reference unit
still measures 41.2 % and our kit still measures 22.3 %. Both reproduce to
0.05 pp. Held in place by `model/test_bd_excitation_probe.py`, which needs no
audio.

```
python3 model/bd_excitation_probe.py --refs /tmp/tr808-ref
```

### 11.1 The gap is structural, not a knob position

§8.3 quotes one file. The probe measures all 25 the corpus holds
(`bd8/BD*.WAV`, 5 TONE × 5 DECAY, `sounds-tr808-fischer` @ `85fbecf`):

| % of energy in the first 4 ms | 20–80 | 80–150 | 150–300 | 300–600 | 600–2000 Hz |
|---|---:|---:|---:|---:|---:|
| machine, min over 25 | 0.29 | 28.93 | 43.18 | 2.05 | 0.05 |
| machine, **median** | 0.76 | **41.16** | 52.59 | 5.60 | 0.18 |
| machine, max | 2.30 | 52.66 | 56.55 | 11.19 | 1.53 |
| **ours, shipped** | **73.38** | 22.30 | **4.15** | 0.11 | 0.06 |

§8.3's 41.2 % is the **median** of the machine's own knob range, not a
coincidence of one file. And the column that matters most is not the one the
issue names: ours puts **73.4 % below 80 Hz where the machine never exceeds
2.30 % at any setting it has**, and 4.15 % in 150–300 Hz where the machine
never drops below 43.18 %. Ours is outside the machine's own range on four of
the five bands. That is not a knob position.

### 11.2 The exciter envelope cannot close it, and it is not a tuning problem

`v = 32767 × (ENV(e1) + ENV(e2)) >> (15 + att)` is **non-negative by
construction**, so the excitation's spectrum is maximal at DC and no setting of
it can tilt the pulse upward in frequency relative to the near-impulse that
already ships. Measured, so it is not only an argument:

| E_BDX | 20–80 | 80–150 | 150–300 Hz |
|---|---:|---:|---:|
| τ 0.1 ms (shipped) | 73.38 | 22.30 | 4.15 |
| τ 1 ms | 80.69 | 15.10 | 4.13 |
| τ 4 ms | 49.34 | **34.27** | 16.29 |
| τ 8 ms | 65.59 | 17.24 | 17.06 |
| hold 48 frames | 76.70 | 17.86 | 5.22 |
| + a second envelope segment | 73.38 | 22.30 | 4.15 |

**τ = 4 ms is the trap.** It raises the 80–150 column to 34.3 % — most of the
way to the target the issue names — and it is not a pulse shape at all: 4 ms
is the attack window's own length, so what moved is the exciter still being on
when the coefficients step back to 49.4 Hz. Its low band is still 49.3 %,
**21× the machine's largest**. A criterion that only read the 80–150 column
would have accepted it.

Two things this costs that a register-level reading would miss: **all 18
envelopes are already read by a path**, so "add a second envelope segment" is
a 19th envelope, not a register change — and spending it moves nothing
(`test_adding_a_second_envelope_segment_costs_an_envelope_and_buys_nothing`).

### 11.3 A bipolar excitation does close it, and the block already has one

`M_BD` is mode 8 and `N_NUMS` is 11, so the bass drum's resonator carries a
**numerator register it does not use** (contract 15.6; `MODE_NUM[m]` at
`0xB3 + 4m`). `BP` injects `e[n] − e[n−2]` — an AC-coupled biphasic pulse,
which is what a capacitor-coupled pulse shaper delivers into a bridged-T. One
register write, no new hardware, `NUMS = 11` already synthesised
(`rtl-sketch/modal_dp.v`).

| | 20–80 | 80–150 | 150–300 | 300–600 | 600–2000 Hz | |
|---|---:|---:|---:|---:|---:|---|
| ours, shipped (RAW) | 73.38 | 22.30 | 4.15 | 0.11 | 0.06 | outside on 4 bands |
| ours, **BP**, peak 1.0, amp 0.0516 | 0.45 | 42.86 | 48.37 | 6.84 | 1.47 | **inside on all five** |
| machine, min…max | 0.29–2.30 | 28.9–52.7 | 43.2–56.6 | 2.05–11.19 | 0.05–1.53 | |

f0 stays 49.40 Hz and the fitted body τ 142.4 ms. One register write puts
every band inside the range the real machine covers across its whole knob.

### 11.4 And it cannot ship, for a reason in `modal_fixed`, not in the kit

`BP` has a zero at DC, so the 49.4 Hz ring it produces is **15.6× weaker** for
the same excitation. Excitation amplitude can only be raised 4× (`peak` 0.25 →
1.0); the rest has to come back through the mode's output `amp`, which does not
touch the **state**. So the resonator runs **23.9 dB smaller inside a biquad
that truncates** — `y = sat((acc >> CF) + x, SB)`, floor, at a pole of
r = 0.99993. A truncating biquad settles into a DC deadband of fixed **state**
size, so its level relative to the signal grows by exactly that 23.9 dB.

DC pedestal, dB below the render's own peak, over accent (the body bus, 1.5 s):

| | 0.50 | 0.70 | 1.00 | 1.40 | 2.00 | worst |
|---|---:|---:|---:|---:|---:|---:|
| RAW (shipped) | −40.2 | −81.2 | −46.3 | −62.2 | −52.4 | **−40.2** |
| BP, peak 1.0 | −78.3 | **−19.4** | −22.5 | −22.5 | −22.5 | **−19.4** |
| BP, peak 1.0, hold 8 | −78.3 | −26.6 | −84.3 | −84.3 | −84.3 | −26.6 |
| BP, peak 1.0, hold 64 | −38.8 | −41.8 | −84.3 | −84.3 | −84.3 | −38.8 |

Read the rows, not the best cell: **which accent lands in which deadband is not
smooth**, so no single accent measures this, and the shipped kit's own pedestal
already swings 41 dB across five accents. The BP arm's worst is 20.8 dB worse
than the shipped kit's worst, and its 20-ms decay envelope stops being
monotone (+5.9 dB where the shipped kit is −0.5). The arm whose pedestal is
tolerable (`hold 64`, −38.8 dB) is the one whose band split is not: 19.66 %
below 80 Hz.

**So the kit is unchanged by this section.** Shipping BP would trade a
measurement the reference can adjudicate for a defect it cannot, which is
precisely the "moved the number without being better" outcome issue #21 was
opened to avoid.

### 11.5 What the shape alone can do, as a bound

Replaying an arbitrary integer sequence into the same bank — more than any
source could emit, bit-exact against `DrumsFx` before any substitution — says
the mechanism is right and only the lever is wrong:

| excitation | 20–80 | 80–150 | 150–300 Hz |
|---|---:|---:|---:|
| shipped exponential, τ 0.1 ms | 69.87 | 25.40 | 4.68 |
| biphasic ±8 samples (0.33 ms) | **0.40** | **39.38** | **46.85** |
| biphasic ±32 samples | 9.49 | 47.67 | 38.94 |
| sine cycle, 256 samples (188 Hz) | 13.25 | 58.71 | 24.37 |
| machine, median | 0.76 | 41.16 | 52.59 |

A biphasic pulse a third of a millisecond wide lands on the machine's median —
**driven at full excitation amplitude, so it costs the resonator's state
nothing**, which is exactly what `BP` cannot do to a pulse that is already
0.1 ms long. That is the shape a source in 15.4 would be aiming at.

### 11.6 What this changes in §8.4's table

`excitation shape | impulse | impulse — 17.20 | not done` stands, and 17.20 is
now **specified rather than open**: a bipolar excitation ≈0.33 ms wide, whose
target is the five-band range of §11.1 and not a single number. Its blocker is
the truncating biquad of §11.4, which is a defect of `modal_fixed.py` /
`rtl-sketch/modal_dp.v` and is filed separately — the shipped kit already
carries it at −40.2 dB.

*Measured 2026-09-25 against `sounds-tr808-fischer` @ `85fbecf`, renders from
`model/drums_fx.py` at contract revision 10. Script:
`model/bd_excitation_probe.py`, validated by
`model/test_bd_excitation_probe.py`.*
