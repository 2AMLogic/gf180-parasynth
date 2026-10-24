# The conga body spectrum: what 1.04 and 1.05 actually measure

**Historical study, 2026-09-18, recovered on 2026-09-19.** The figures and
available-corpus inventory below describe the versions named in section 1,
before later estimator and tom-law corrections. They are not today's board
or reference inventory. The recovered probe retains that analysis definition;
its `--check` now refuses missing evidence and fails a changed known answer
or historical reference value before producing a new study.

**One deliverable: the spread of the real TR-808's conga body spectrum across
multiple recordings, and therefore whether D04A's 1.04 and D08A's 1.05 are a
genuine disagreement or inside the machine's own variation.**

**Headline, in one line: neither. Both numbers are set by a window-geometry
mismatch between the two sides of the comparison whose own size on these
signals reaches 6.07 dB — twice the 3.0 dB tolerance. Window both sides alike
and D04A reads 0.82 or 2.24 depending only on how many milliseconds of digital
silence precede the strike. The board should not be read as "the congas are
4 % off".**

And, separately: **the spread across machines cannot be measured on this host.
Every real-TR-808 conga recording reachable here descends from one unit** —
Michael Fischer's s/n 103852, one 1994 session. That question is REFUSED, with
the exact fetch that would answer it named in section 7.

---

## 1. The definition used, stated first

Anything measured differently is a different quantity and none of this applies
to it. This is `tools/run_case.py` @ `d0c1d59ee990` with
`model/audio_measure.py` @ `7386f4a9297f` — the pair recorded in the
`analysis_run` field of the D04A/D08A results that carry 1.04 and 1.05.

`body spectrum`, for LC and HC, is `_split_db(sound, 0.0, 0.150)`:

```
x       = wavfile.read(...) -> mono mean -> / 32768.0
prepare = pk = max|x| ; i = first index with |x| > 0.02*pk
          lead = max(0, i - 1 ms) ; if lead >= 5 ms: subtract mean(x[:lead])
          y = x[lead:] ; y /= max|y|
window  = y[0 : 0.150 s]                      <- 150 ms from the trim point
value   = 10*log10( E[split..2000] / E[20..split] )      dB
          E = audio_measure.band_energy: 4th-order Butterworth band-pass
          applied with sosfiltfilt (zero phase), sum of squares, each
          divided by the segment's total energy
          LC: split 700 Hz    HC: split 1300 Hz    (BAND/SPLIT_HZ, drum_verify)
refuse  if either side holds no energy, or |value| > 80 dB
```

Tolerance **3.0 dB flat**, `TOLERANCE_POLICY["energy ratio"]`: *"the half-power
convention. A stated convention, not a number derived from any error of ours."*
Case distance = `|ours − reference| / 3.0`.

Note what the band edges mean for these two voices. LC's f0 at the anchor
setting is 200 Hz and its "above" band is 700–2000 Hz; HC's f0 is 412 Hz and
its "above" band is 1300–2000 Hz. In both cases the numerator sits 1.7–1.8
octaves above the resonance, holds about **0.05 % of the segment's energy**,
and is fed mainly by the strike transient rather than by the resonator. It is a
small number divided by a large one, which is exactly the shape that makes it
fragile — see section 4.

`tools/measure_conga_body_spread.py` (new, this branch) re-implements the above
so it does not depend on another agent's working tree.

## 2. The estimator, validated before it was pointed at a conga

**Known-answer signals, nothing of ours involved.** Two steady sines either
side of the split, where the answer is `20·log10(a_hi/a_lo)` by construction:

| | expected | measured | error |
|---|---:|---:|---:|
| LC 100 Hz vs 1600 Hz @ 1.0 | 0.000 | −0.071 | −0.071 |
| LC @ 0.5 | −6.021 | −6.100 | −0.079 |
| LC @ 0.1 | −20.000 | −20.088 | −0.088 |
| LC @ 0.0316 | −30.006 | −30.094 | −0.088 |
| HC 150 Hz vs 1800 Hz @ 1.0 | 0.000 | −0.073 | −0.073 |
| HC @ 0.5 | −6.021 | −6.087 | −0.066 |
| HC @ 0.1 | −20.000 | −20.068 | −0.068 |
| HC @ 0.0316 | −30.006 | −30.076 | −0.070 |

Worst |error| **0.088 dB** over a 30 dB range. The estimator is correct on
stationary signals.

**And it reproduces the published numbers exactly**, which is the check that
says the definition matches rather than merely resembles:

| | published by D04A/D08A | recomputed here | Δ |
|---|---:|---:|---:|
| LC `lc8/LC50.WAV` | −33.5108 | −33.5108 | **+0.0000** |
| HC `hc8/HC50.WAV` | −31.2884 | −31.2884 | **−0.0000** |

**Neither check is calibrated on our model.** The first is closed form; the
second is a different implementation of the same published definition on the
same input file.

## 3. Every real-hardware conga recording on this host

All fifteen, individually. Mid conga is included because D06A passes at 0.36 on
the same circuit and the same estimator, and a metric's behaviour on the case
that passes is part of reading the two that do not.

| file | TUNING | f0 (Hz) | body spectrum (dB) | sha256 (16) |
|---|---:|---:|---:|---|
| `lc8/LC00.WAV` | 0.0 | 184.71 | **−35.973** | `a00777a5d2831b16` |
| `lc8/LC25.WAV` | 2.5 | 189.47 | **−34.855** | `fd6593c88a75de8a` |
| `lc8/LC50.WAV` | 5.0 | 200.42 | **−33.511** | `6591f57bbbdb8918` |
| `lc8/LC75.WAV` | 7.5 | 211.72 | **−34.151** | `442942b82e73a719` |
| `lc8/LC10.WAV` | 10.0 | 224.78 | **−31.302** | `8c8e0224afb3f2fd` |
| `mc8/MC00.WAV` | 0.0 | 259.18 | −32.673 | `3c4857ddc26e4d5d` |
| `mc8/MC25.WAV` | 2.5 | 265.11 | −32.171 | `a3ca42608916815e` |
| `mc8/MC50.WAV` | 5.0 | 281.09 | −32.410 | `8175efe26c67f5af` |
| `mc8/MC75.WAV` | 7.5 | 300.06 | −29.959 | `347afdc65ba86694` |
| `mc8/MC10.WAV` | 10.0 | 319.47 | −31.561 | `e0b10cfaac26f357` |
| `hc8/HC00.WAV` | 0.0 | 375.14 | **−32.830** | `e1c3328d5943e08b` |
| `hc8/HC25.WAV` | 2.5 | 387.44 | **−31.464** | `3476e81b5a43031d` |
| `hc8/HC50.WAV` | 5.0 | 412.43 | **−31.288** | `ee4354b1fb79126e` |
| `hc8/HC75.WAV` | 7.5 | 442.43 | **−31.885** | `660f956127198dc5` |
| `hc8/HC10.WAV` | 10.0 | 466.47 | **−25.561** | `21a3b7dfb1d87e8a` |

**Fischer's `10` is the knob at maximum, not at 1.0.** His README: the knob has
"11 uniformly spaced position marks … 0 through 10" and he "sampled the 808 at
five uniformly spaced positions" — 0, 2.5, 5, 7.5, 10. The measured f0 is
monotone in that order and only in that order, in all three voices. Anything
that reads `LC10` as TUNING 1.0 has the series out of order.

Spread across the five settings, per voice:

| voice | n | min | max | **range** | sd | IQR |
|---|---:|---:|---:|---:|---:|---:|
| LC | 5 | −35.973 | −31.302 | **4.671 dB** | 1.744 | 1.345 |
| MC | 5 | −32.673 | −29.959 | **2.713 dB** | 1.085 | 0.850 |
| HC | 5 | −32.830 | −25.561 | **7.269 dB** | 2.882 | 0.596 |

Restricted to the recordings whose f0 lies within **±10 % of the anchor's** —
`docs/tr808-reference.md` §1.7's own component tolerance on f0, i.e. the span
that separates two units built to the same schematic:

| voice | f0 window | n | body range | vs tolerance 3.0 dB |
|---|---|---:|---:|---|
| LC | 180–220 Hz | 4 | −35.973 … −33.511 = **2.46 dB** | 82 % of it |
| MC | 253–309 Hz | 4 | −32.673 … −29.959 = **2.71 dB** | 90 % of it |
| HC | 371–454 Hz | 4 | −32.830 … −31.288 = **1.54 dB** | 51 % of it |

**What this is and is not.** It is how far the number moves when *this*
machine's conga resonator is swept across the same f0 span that separates two
units. It is **not** a measurement of two units, and it is a **lower bound** on
unit-to-unit spread, not an estimate of it: §1.7 also puts **±50 % on Q**, and
the TUNING pot moves R1 in the bridged-T foot without moving Q at all. The
strike-pulse shaping, which is what actually feeds the 700–2000 Hz band, is
untested by the sweep too.

## 4. The apparatus term, and it is bigger than the tolerance

> **Pre-#132 figures (annotated in #636).** Every number in this section and the
> 6.07 dB headline below was measured with this tool's private `prepare()`, which
> clamped the lead (`max(0, onset - 1 ms)`, the #101 defect). The tool now calls
> `run_case.prepare`, which guarantees the lead, so the `+1 ms` / `+10 ms` lead
> columns are expected to read ~0 when re-run. **That re-run has NOT been done**:
> the reference corpus (`GF180_TR808_REFS`) is not on the host that made this
> change, so the tables below are unchanged and are the *historical* figures, not
> the current ones.

Each perturbation below leaves the machine and the strike untouched and changes
only the apparatus. Per signal and per perturbation, because **a floor that is
not constant must not be published as one** (#92; a constant floor that was not
constant withdrew a column of #61).

| file | +1 ms lead | +10 ms lead | 44.1k→48k | 16-bit | window ±10 ms |
|---|---:|---:|---:|---:|---:|
| LC00 | **+6.072** | +6.072 | +0.009 | +0.000 | 0.025 |
| LC25 | **+4.965** | +4.965 | +0.017 | +0.000 | 0.029 |
| LC50 | **+3.615** | +3.615 | +0.015 | +0.000 | 0.025 |
| LC75 | **+4.541** | +4.541 | +0.016 | +0.000 | 0.028 |
| LC10 | **+2.786** | +2.786 | +0.007 | +0.000 | 0.024 |
| HC00 | +1.297 | +1.297 | −0.271 | +0.000 | 0.001 |
| HC25 | −0.034 | −0.034 | −0.250 | +0.000 | 0.001 |
| HC50 | −0.185 | −0.185 | −0.272 | +0.000 | 0.001 |
| HC75 | +0.939 | +0.939 | −0.354 | +0.000 | 0.000 |
| HC10 | −0.042 | −0.042 | −0.149 | +0.000 | 0.000 |

Sample rate, bit depth and the 150 ms window choice are all negligible. **The
window-start term is not: up to 6.07 dB, against a 3.0 dB tolerance.**

### Why, and why it is the apparatus and not the sound

*Prepending exact digital silence cannot change what the machine did.* The
effect is identical at +1 ms and +10 ms, and saturates by +0.5 ms — the
signature of a fixed-length filter edge, not of any property of the recording.
Splitting the ratio confirms where it lands (LC50):

| | e_lo (20–700) | e_hi (700–2000) | ratio |
|---|---:|---:|---:|
| as recorded | 1.039732 | 4.6328e−4 | −33.511 |
| +1 ms zeros | 1.000907 | 1.0253e−3 | −29.895 |

The denominator moves by 4 %; **the numerator more than doubles.** `band_energy`
filters with `sosfiltfilt`, whose odd extension at a segment that *starts at
full amplitude* manufactures an edge. The high band holds ~0.05 % of the
energy, so that edge is a large relative error there and a negligible one below
the split. Filtering with real zero padding and cutting afterwards collapses
the LC50 gap from 3.62 dB to 0.73 dB, which identifies the mechanism.

### And the two sides of D04A/D08A are not windowed alike

| | onset index | `lead` | window opens |
|---|---:|---:|---|
| `lc8/LC50.WAV`, `hc8/HC50.WAV` | 7 (0.159 ms) | 0 | **0.159 ms** before the strike |
| our render, LC and HC | 481 (10.021 ms) | 433 | **1.000 ms** before the strike |

`prepare` trims to 1 ms before the onset *when there is 1 ms of signal there to
keep*. The Fischer files begin at the strike, so theirs comes out 0; our render
leads with 10 ms of exact digital silence, so ours comes out exactly 1.00 ms.
The comparison therefore puts one side on each side of a 3–6 dB apparatus step.

## 5. Where our model sits, and what the case is worth

Our side rendered here and now, `drums_fx.py` @ `5ea51e6cf91b53d6` — byte for
byte the file that produced the published results, and imported, never written.

| | ours | reference (`*50.WAV`) |
|---|---:|---:|
| LC | f0 192.21 Hz, body **−36.618 dB** | f0 200.42 Hz, body −33.511 dB |
| MC | f0 291.20 Hz, body −31.768 dB | f0 281.09 Hz, body −32.410 dB |
| HC | f0 415.83 Hz, body **−34.444 dB** | f0 412.43 Hz, body −31.288 dB |

Recomputed distance, as shipped: LC 3.107/3.0 = **1.036**, HC 3.155/3.0 =
**1.052**. Both match the board to the printed digit.

Now hold the window geometry constant across both sides — the only change —
and the verdict is not stable:

| | reference | ours | **worst** | verdict |
|---|---:|---:|---:|---|
| **LC** as shipped (ref 0.16 ms, ours 1.00 ms) | −33.511 | −36.618 | **1.036** | fail — *what the board shows* |
| LC, both at 1.00 ms of lead | −29.895 | −36.618 | **2.241** | fail, by a lot |
| LC, both opening at the strike | −33.511 | −35.978 | **0.822** | pass |
| **HC** as shipped | −31.288 | −34.444 | **1.052** | fail — *what the board shows* |
| HC, both at 1.00 ms of lead | −31.473 | −34.444 | **0.990** | pass |
| HC, both opening at the strike | −31.288 | −31.936 | **0.216** | pass |

**D04A spans 0.82 to 2.24 and D08A spans 0.22 to 1.05 with nothing changed but
how many milliseconds of digital silence precede the strike.** The shipped
configuration is not even the worst case — for LC it is the *middle* one, the
two mismatched biases partly cancelling. The number on the board is an
arithmetic accident of that cancellation.

## 6. Verdict

**Is 1.04 inside the machine's own variation? The question does not survive
contact with the measurement.** Answering in those terms would mean the
apparatus can resolve 3 dB on these signals, and it cannot: its own
window-start term reaches 6.07 dB on LC.

Of the brief's three possibilities the answer is a fourth: **the near-miss is an
artefact of the measurement, not a property of either the model or the
machine.** Concretely:

- **Not a real small defect that the board can see.** With both sides windowed
  at the strike, LC is 0.82 and HC 0.22 — comfortable passes. With both at 1 ms,
  LC is 2.24 — a clear fail. The apparatus, not the model, chooses.
- **The tolerance was justified** (a stated half-power convention, not fitted to
  our error) **but it is finer than this estimator's own floor on these
  signals,** which makes it a gate on noise regardless of its provenance.
- **It is at the same time plausible that 3 dB is finer than the machine's own
  spread.** The ±10 %-of-f0 read-off is 2.46 dB (LC), 2.71 (MC), 1.54 (HC) —
  51–90 % of the tolerance from the f0 term alone, before ±50 % on Q and before
  any strike-pulse tolerance. That is a *lower* bound and it is already most of
  the budget. But it is one machine's knob, not two machines, so it is
  suggestive and not established.

**No tolerance was changed and no model was touched.** The fix is not a looser
threshold. It is to make the two sides of the comparison the same measurement:
give both sides the same lead before `prepare`, or pad explicitly before
`band_energy` filters, or move the split so the numerator is not 0.05 % of the
energy. Any of the three, then re-run — the ranking of the sixteen drum cases
can move.

**This generalises, with one correction to the brief's example.** Every drum
result on the board carries at least one `energy ratio` metric — all nineteen
written so far — and each runs through the same `band_energy` call, and every
drum case compares a 44.1 kHz Fischer file that begins at the strike against a
render that leads with 10 ms of silence. The term found here is large wherever
the numerator band is small, so every `body/noise`, `early/body` and
`Partial balance` split is exposed to it and none has been checked.

**D15A's 1.58 is not one of them, though.** Its worst metric is `attack`
(3.0 ms against 14.24, tolerance 7.12) — a `time` estimator, not an energy
ratio, so nothing measured here transfers to it and it needs its own apparatus
check. What section 4 does reach on D15A is its *other* metric, `Band energy`
at 0.81 — inside the range an apparatus term of this size can move across 1.0.

**The transferable part is the method, not the number: perturb the apparatus in
a way that cannot change the sound, and require the metric to move by less than
its tolerance before believing any verdict within it.** No case on this board
has had that check, and near-misses are exactly where it decides the answer.

## 7. REFUSED: the spread across machines

**Every real-TR-808 conga recording reachable on this host descends from one
unit.** The claim in the brief that the index offers "many distinct sets" does
not hold for the congas, on this host, today.

- **The Fischer set is one machine, one session, and one take per setting.** Its
  README: *"taken DIRECTLY from a Roland TR-808 (SERIAL NO. 103852)"*, recorded
  1994 through SoundEdit 16 on a Quadra 660AV, and — decisive for take-to-take
  spread — *"I recorded many hits of the same sound, and picked the one that I
  felt best represented the average."* **The variation between strikes was
  deliberately removed before the set was published.** There are no accents: he
  pinned LEVEL at maximum and swept TUNING, and the congas have no other knob.
- **`808 From Mars` is the second machine and it is REFUSED here.** 22 Clean/
  Digital low-conga takes (2 settings × 11) and 22 hi-conga takes are indexed.
  `tools/refaudio_fetch.py` exits **2**: *"REFAUDIO_SSH / REFAUDIO_ROOT are not
  set: this host has no route to the reference-audio storage."* That is the
  tool behaving correctly and says nothing about the recordings.
- **Its `Legacy` edition is not a third machine** — `refaudio/README.md` calls it
  "a second recording session of the same machine".
- **The other "808 congas" on this host are re-pressings of Fischer.** Tested by
  peak-normalised cross-correlation of the first 150 ms, allowing a small
  resampling ratio:

  | candidate | best Fischer match | r | ratio | verdict |
  |---|---|---:|---:|---|
  | `Conga-808-Mid.aif` | `MC50.WAV` | **1.000** | ×1.0000 | the same recording |
  | `Conga-808-Low.aif` | `LC25.WAV` | **0.999** | ×1.0900 | pitch-shifted re-pressing |
  | `Conga-808-Hi.aif` | `HC75.WAV` | 0.948 | ×0.9525 | below the 0.95 bar; with its two siblings at 0.999 and 1.000 the natural reading is the same set with more processing. Not established as an independent machine either way. |

  *Different pressings are not different machines* — here, literally: one of
  them is bit-for-bit the same event.

### What would answer it

Two fetches, both already indexed, `archive` `808-from-mars.zip`:

```
808 From Mars/WAV/01. Individual Hits/06. Low Conga/Clean/Digital/{A,B}/Conga Low {A,B} 808 {01..11}.wav
808 From Mars/WAV/01. Individual Hits/08. Hi Conga/Clean/Digital/{A,B}/Conga Hi {A,B} 808 {01..11}.wav
```

44 files: a second machine, two settings, eleven takes each. They give **both**
missing numbers — the machine-to-machine difference *and*, from the repeated
takes at one setting, the within-machine take spread the Fischer set removed by
construction. Use the `Clean/Digital` subset only; `Clean/Tape`, `Color/
Saturated` and `Color/Various` carry a recording chain. Open a
`reference-audio` issue naming that archive and those paths.

Until then the honest narrow claim is the one in section 3 — **one machine,
five knob positions, a 2.46 dB (LC) and 1.54 dB (HC) range across ±10 % of
f0** — and the honest broad claim is that **section 4 makes the spread question
secondary**, because the apparatus cannot resolve 3 dB on these signals however
small the machine's true spread turns out to be.

## 8. Provenance

- **Branch** `measure/conga-body-spectrum`, based on `origin/main` **`217bd4e`**
  (`integration: the six joins the chip-level bench could not see (#90)`).
  The brief said `main`; the local `main` was seven commits stale and predates
  PR #88, so it has no `refaudio/` at all.
- Worktree clean apart from the two files this branch adds.
- Input hashes: `model/drums_fx.py` `sha256:5ea51e6cf91b53d6` ·
  `model/audio_measure.py` `sha256:7386f4a9297ff5f7` ·
  `model/drum_verify.py` `sha256:16421a20a229c753`. The first two are identical
  to those recorded in the D04A/D08A results, so our side is the same design.
- Reference corpus `/tmp/tr808-ref` = `tidalcycles/sounds-tr808-fischer`,
  CC0-1.0. Per-file sha256 in section 3.
- `refaudio` catalog ids: `808-from-mars.zip` (`index/808-from-mars.tsv`,
  1,562 files), `808_from_mars_legacy.zip`
  (`index/808_from_mars_legacy.tsv`) — **fetch REFUSED, exit 2, nothing
  downloaded.**
- Python 3.14.7 / numpy 2.5.3 / scipy 1.18.1.

Commands:

```sh
python tools/measure_conga_body_spread.py --refs /tmp/tr808-ref --check
python tools/measure_conga_body_spread.py --refs /tmp/tr808-ref \
       --descent '<directory of candidate 808 conga files>' --json out.json
python tools/refaudio_fetch.py 808-from-mars.zip \
  '808 From Mars/WAV/01. Individual Hits/06. Low Conga/Clean/Digital/A/Conga Low A 808 01.wav'
#   REFUSED  REFAUDIO_SSH / REFAUDIO_ROOT are not set   (exit 2)
```

### Wrong-then-right rate for this session

**One of four measurements published here was wrong before it was right.** The
first version of the section 4 floor table reported a "trim jitter ±1 ms" term
of 17–21 dB. That was the probe, not the estimator: deleting the leading
millisecond of a file that begins at the strike deletes the attack transient,
which is where the entire numerator band lives. It was caught by asking why a
1 ms shift could possibly be worth 20 dB, and replacing it with a perturbation
that adds silence rather than removing signal — which then found the real 3–6 dB
term. The two validations in section 2 and the section 5 numbers were right
first time.
