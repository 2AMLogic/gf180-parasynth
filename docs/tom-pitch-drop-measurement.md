# The toms' diode pitch drop, measured

**One deliverable: what the TR-808's tom pitch drop actually is, from recordings
of real hardware.** Not a fix, and no coefficient is changed here.

`spec/NUMERIC-CONTRACT.md` §15.7.1 ships the drop as a coefficient sequence —
f0 starting at **×1.7** and relaxing over **60 ms**, scaled by accent. The
*existence* of the drop is verified from the service notes.
`docs/tr808-reference.md:473` marks the magnitude **[inferred]**;
`docs/drum-verification.md:670` then carries `×1.7, accent-scaled, 60 ms`
forward as "verified in a source, §4". It was never measured. It is now.

---

## The answer

**The drop is real, it is accent-scaled, its shape is exponential and its
duration is roughly right. The ×1.7 is about three times too large at the
loudest hit in the corpus and eleven times too large at an unaccented one.**

Onset f0 ÷ settled f0, pooled over LT, MT and HT × 11 tuning positions
(`model/tom_drop_measure.py`, 99 clean-digital files):

| accent | n | median | mean ± sd | range | τ median (range) | contract's excess ÷ measured |
|---|--:|--:|--:|---|--:|--:|
| **A** — no accent | 23 | **×1.063** | 1.061 ± 0.016 | 1.040 – 1.094 | 13.0 ms (6 – 29) | **11.1 ×** too large |
| **B** — accent | 33 | **×1.140** | 1.150 ± 0.049 | 1.085 – 1.272 | 24.5 ms (17 – 48) | **5.0 ×** too large |
| **C** — more accent | 33 | **×1.236** | 1.246 ± 0.053 | 1.169 – 1.344 | 33.1 ms (22 – 64) | **3.0 ×** too large |

Per voice at the most-accented setting: **LT ×1.222 ± 0.050, MT ×1.222 ± 0.058,
HT ×1.262 ± 0.047.** The largest drop anywhere in the 99 tom files is **×1.344** (MT, *More
Accent*, tuning 11).

Quote it as **×1.24 ± 0.05 (settings) ± 0.05 (instrument)** at *More Accent*,
**×1.14** at *Accent*, **×1.06** unaccented. The shipped ×1.7 lies outside every
one of those, and outside the range of all 99 files.

**Read that ± 0.05 correctly: it is the spread across the eleven TUNING
positions, and it is physical.** It is **not** take-to-take repeatability, and
no number here is. The pack gives **exactly one take per (voice, accent,
tuning)**, so this corpus contains no repeated-measurement component at all and
cannot bound one. Anyone combining this figure with a take-to-take term must get
that term somewhere else.

### The four questions, answered separately

- **Ratio — wrong, by 3 to 11 ×.** Never ×1.7 in any file at any accent or tuning.
- **Duration — roughly right, if anything too short.** The contract's
  `exp(−3t/60 ms)` is τ = 20 ms. Measured τ is 24.5 ms at *Accent* — close —
  and 33 ms (up to 64 ms on LT) at *More Accent*, i.e. the real relaxation is
  **1.2–3× longer**, not shorter. **The 60 ms is the part of §15.7.1 that
  survives contact with the hardware.**
- **Shape — exponential, confirmed.** An exponential fits better than a linear
  ramp in **88 of 89** measured tom rows, decided by residual, not assertion.
- **Accent-scaling — confirmed, and this is the first direct evidence for it.**
  Monotone A < B < C in every voice. But the shipped law does
  `min(max(accent, 0), 1)`, so it **clamps at accent 1.0 and applies the full
  ×1.7 to the unaccented hit** — which is most of what an 808 plays, and where
  the machine does ×1.06.

### One thing nobody claimed, that the data shows anyway

**The drop depends on the TUNING pot, strongly.** LT at *More Accent* goes from
×1.169 at 82 Hz to ×1.325 at 101 Hz — the excess nearly doubles across the pot.
That is what §4's circuit reading predicts (the pot *is* R1, and the diode
shunt's leverage depends on where it sits) and the shipped sequence is
tuning-independent.

### The congas, on the same circuit

| voice | no accent | accent |
|---|--:|--:|
| LC | ×1.006 ± 0.003 | ×1.061 ± 0.039 |
| MC | ×1.004 ± 0.004 | ×1.064 ± 0.038 |
| HC | ×1.005 ± 0.004 | ×1.059 ± 0.035 |

Same mechanism, about **half the toms' magnitude**, and unaccented it is
essentially absent. §4's "the congas share the mechanism" is right; they do not
share the size.

---

## Why you should believe the instrument

`model/tom_pitch_probe.py` tracks f0 one **full period** at a time from
interpolated same-direction zero crossings behind a **Schmitt trigger**. Full
periods because a half-period estimator does not cancel a DC offset and produces
an alternating long/short artefact that reads exactly like a pitch drop. The
trigger because dither near a zero makes three crossings where there is one, and
the short "period" reads as 2 × f0 — which the sanity band has to admit, because
it must stay open to a genuine ×1.7. That defect put 180 Hz rows in a 90 Hz tom's
settled window and inflated its scatter to 20 Hz before it was caught.

**The probe does not filter.** `model/test_tom_pitch_probe.py` measured the
alternative: band-limiting around the fundamental costs **40 % of the excess**
(filtfilt pre-ringing lands on the first retained period) and biases the null
low by 0.036. The intuition was wrong and the validation decided. This also
answers the windowing artefact found in the drum path this session — there is no
filter on this path to manufacture an edge, and the two FFTs the probe runs (the
settled cross-check and the neighbour diagnostic) sit in the **settled** window,
not the onset.

### The gate: recover a known drop before measuring an unknown one

Synthetic toms are real 2-pole resonators kicked by a 1 ms pulse, with a dither
floor, 24-bit quantisation and the same hard onset trim the recordings have, so
the gate exercises the actual failure modes.

| what | result |
|---|---|
| recovers the contract's own ×1.7 / 60 ms | **0.41 %** of the excess |
| recovers ×1.05 – ×1.40, 60 ms drops | **≤ 0.46 %** |
| …the same over 25 ms drops | ≤ 5.6 %, worst corner LT, where τ = 8.3 ms is **shorter than one period** of the 90 Hz carrier |
| **invents no drop on a null (R = 1.000)** | **\|R − 1\| < 0.0002** |
| tells an exponential drop from a linear one | correct in every case |
| a −20 dB pink rumble (worse than any measured) | does not fake a drop |
| a coherent neighbour at −25/−30 dB | 5 of 12 refused; **worst survivor ±0.045** |
| silent / wrong-rate / clipped / too-short | refused |

### The floor, measured per file, not quoted as a constant

Issue #92's complaint is that `inharmonic_fraction_db` quotes a floor it does
not have. This probe measures its own: the **scatter of the per-period estimate
in that file's settled window**, in that file's own units. It is 0.25–0.75 Hz on
LT and MT (≈ 0.3 %) and 1.9–2.1 Hz on HT (≈ 1 %). A row whose peak excess is
under 3 σ of its own floor is reported `NO-DROP-ABOVE-FLOOR` and claims nothing
in either direction. A row whose fit runs past its data — onset excess more than
4 × the largest *observed* excess, or τ shorter than one period — is **REFUSED**:
an optimiser explaining one high point is not a measurement. Nine of the 33
accent-A rows were refused on exactly that ground (LT 7, MT 2), and a tenth
reported `NO-DROP-ABOVE-FLOOR`, and saying so is the result.

### Uncertainty budget for ×1.24

| source | contribution |
|---|--:|
| instrument, validated | ≤ 5.6 % of the excess → ± 0.013 |
| coherent-neighbour confound, worst survivor | ± 0.045 |
| onset definition (10 ms of leading silence) | ± 0.0001 at accent C (± 0.023 at A) |
| spread across the 11 tuning positions | ± 0.053 — **physical, not noise** |
| **take-to-take repeatability** | **not available: one take per setting** |

---

## Controls on the recordings

- **Folder semantics, checked not trusted.** Settled f0 moves **18.5 / 28.0 /
  39.1 Hz** across 01…11 and **0.4 – 7.3 Hz** across A/B/C. So 01…11 is the
  TUNING pot and A/B/C is not — which is what the pack's own notes say
  (*A = No Accent, B = Accent, C = More Accent*).
- **The control that carries the result.** Accent is, to first order, a gain on
  the trigger pulse, and the files are level-normalised to within 0.5 dB of each
  other. A **linear** system's normalised frequency trajectory does not change
  when its input is scaled, and neither does any artefact of a linear estimator
  on it. The trajectories differ, monotonically, with accent — at LT tuning 06
  the second period reads ×0.998 / ×1.078 / ×1.160 for A / B / C on the same
  drum at the same tuning through the same chain. **The mechanism is a real
  amplitude-dependent nonlinearity, and the accent-scaling assertion is now
  evidence rather than inference.**
- **Leading silence changes nothing.** Prepending 0 / 0.5 / 1 / 10 ms of digital
  silence moves the measured ratio by ≤ 0.0001 at accent C; worst case anywhere
  is 0.0226, on an accent-A row where the excess is barely above the floor.
- **These are distinct events, not re-pressings — and that check is not pro
  forma.** Another agent this session found nominally different "808" sets on
  this host cross-correlating at **1.000** against the Fischer material: not
  similar recordings, *literally the same events* re-pressed, so a spread
  computed across them would have been fiction. Here the largest
  \|cross-correlation\| between any two of the 33 files in a voice is **0.9968**
  (LT), 0.9922 (MT), 0.9944 (HT). Those maxima are between **adjacent tuning
  positions** — 82.44 Hz against 82.59 Hz, genuinely nearly the same sound — and
  every one is strictly below 1.000, on files of different lengths. They are
  distinct strikes.

  What that buys, and what it does not: it establishes the eleven tunings are
  eleven **events**, so the spread across them is real. It does **not** make
  them eleven independent draws of the same quantity — they are eleven different
  settings of the same machine, and adjacent ones are highly correlated by
  construction. **The spread reported here is across settings. There is no
  take-to-take component**, because the pack gives one take per (voice, accent,
  tuning).

---

## What this does *not* settle

- **Which recorded accent is the model's `accent = 1.0`.** The pack ships no
  panel settings. A/B/C are ordered but not calibrated, so "×1.24 at C" cannot
  be mapped to a scalar without a measurement the corpus cannot give. Every
  comparison above is therefore stated *per accent level*, and the honest
  summary is a range: **the machine's drop lies between ×1.06 and ×1.34**, and
  ×1.7 is outside it everywhere.
- **Why the scorecard's tom cases fail at 5.13 / 6.22 / 5.23.** That the three
  toms fail together by 5–6 × is consistent with this — the contract's excess is
  5.0 × the measured one at *Accent* — but this measurement does not run those
  cases and does not claim to close them.
- **One machine's worth of unit variation.** §12 calls ±50 % on Q normal between
  units; nothing here bounds unit-to-unit spread on the drop.

---

## Provenance

- Repository `217bd4e` (`origin/main`, PR #90), branch `measure/tom-pitch-drop`.
  Local `main` was three commits stale and did not contain `refaudio/`.
- Source: **`808-from-mars.zip`**, SHA-256
  `f567c6767e34734964007dda645834f5ddd3dc75eaad18d8e306638ab183ca82`, matching
  `refaudio/catalog.json` exactly; 244,593,867 bytes.
- Members: `808 From Mars/WAV/01. Individual Hits/{03. Low Tom, 04. Mid Tom,
  05. Hi Tom, 06. Low Conga, 07. Mid Conga, 08. Hi Conga}/Clean/Digital/{A,B,C}/*.wav`
  — 165 files, **every one byte-for-byte the size
  `refaudio/index/808-from-mars.tsv` lists**. `Clean/Digital` only: the pack's
  `Tape` and `Color` subsets are deliberately coloured and an Otari MTR-12's
  wow and flutter has no business inside a pitch measurement.
- Commands:

  ```sh
  REFAUDIO_LOCAL=<dir holding the pack zips> \
    python tools/refaudio_local.py --prefix 808-from-mars.zip \
    '808 From Mars/WAV/01. Individual Hits/03. Low Tom/Clean/Digital/' ...
  python model/test_tom_pitch_probe.py                       # the gate
  python model/tom_drop_measure.py --congas --controls \
    --json docs/tom-pitch-drop-results.json
  ```

- The 165 members are taken **by name** from the index above (#1427). The
  command REFUSES (exit 2, naming the group and the WAVs that are absent)
  before it measures anything when a requested group holds only some of them,
  so a rerun over a partly fetched cache cannot report a row over fewer than 11
  tunings. Without `--congas` only the 99 tom takes are required.
- Per-file results, refusal reasons and controls: `docs/tom-pitch-drop-results.json`.

### How the audio was reached, and the refusal that was honoured

**`tools/refaudio_fetch.py` REFUSED on this host** (exit 2 — `REFAUDIO_SSH` /
`REFAUDIO_ROOT` unset), and that refusal was recorded, not worked around. The
archive itself is mounted on this host, so `tools/refaudio_local.py` reads it
directly under the *same* three-outcome contract, plus one check the ssh path
cannot make: **it hashes the archive and refuses unless the SHA-256 matches
`catalog.json`.** Five refusal paths were demonstrated red before it was used —
no `REFAUDIO_LOCAL`, archive absent from the catalog, SHA-256/size mismatch
(a different zip renamed to the target name), a member that does not exist, and
a `../` traversal. Combined with the per-member size check that is end-to-end
provenance: these bytes are the bytes the committed index describes.

This was put to the coordinator before the numbers were relied on, and
**ruled on: the numbers stand and the local route is kept.** The reasoning,
recorded here because the next agent will hit the same exit 2:

> A refusal exists to protect a **requirement**, not a **transport**.
> `refaudio_fetch.py` exits 2 because `REFAUDIO_SSH` is unset — that is "this
> route is not configured", not "this data may not be used" and not "this data
> cannot be trusted". The requirement underneath it is *use reference audio
> whose provenance you can verify.* A second route that satisfies that
> requirement **more strongly than the route that refused** is not
> circumvention. Working around the refusal would have been reading the files
> with no integrity check and not saying so.

`tools/refaudio_fetch.py` is unmodified and still exits 2 here. Two other agents
refused spread measurements on the same `REFAUDIO_SSH` grounds; the local route
may unblock those questions too.

*Measured 2026-09-18. No file under `model/drums_fx.py`, `spec/`, or any shipped
coefficient was modified.*
