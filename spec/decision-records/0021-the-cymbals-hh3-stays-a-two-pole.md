# 0021: The cymbal's Hh3 stays a two-pole — a third pole is buildable, priced, measured, and rejected on a holdout

- **Status**: proposed
- **Date**: 2026-09-27
- **Decided by**: sound/measurement owner, for #102, under DR 0015

## Context

`docs/tr808-reference.md` §10 records Hh3 — the resonant high-pass after the
cymbal's short high-band VCA — as a **3rd-order Sallen-Key resonant at
≈10.5 kHz [verified: W14b §9]**. `model/drums_fx.py` realises it as a **single
two-pole** (`M_CYHI`, `CY_HI_HZ = 10500`, `CY_HI_Q = 2.5`, BP numerator).
#102 argued that the missing pole is what the fitted `Q 2.5 → 4.0` retune —
five-band cost 18.1 → 6.0 — is really standing in for, since +60 % on Q is
outside reference 1.7's ±50 % unit-to-unit normal.

Two things had to be settled before the structure could be: **#99**, which
measure judges (answered by DR 0015: the per-property scorecard, with the
five-band-energy cost **explicitly rejected** as a judge), and the absence of
any held-out cymbal recording, since every scorecard `/variation` case was
unscored.

The measurement is `tools/probes/hihat/hh_probe5.py`. It renders through the
fixed-point block — the path `hh_probe3`/`hh_probe4` established, never the
offline emulator `hh_probe4` withdrew — and every verdict comes from
`tools/scorecard.py`'s own `evaluate`/`compare`.

## Decision

**`M_CYHI` stays a single two-pole. No 17th mode, no 24th path, no `MW` 4 → 5,
no `N_NUMS` renumbering.** Reference 10's third pole is a *documented omission*,
recorded as such, in the same class as Hh1.

Two 3rd-order structures were built and measured — the existing 2-pole cascaded
with one real pole at the same corner (`a2 = 0`, i.e. half a biquad), in both
orientations, because "a 6 dB/octave skirt" does not say which side:

| structure | fitted on D14A | `Band energy` | `band decay` |
|---|---|--:|--:|
| **2-pole — the structure that ships** | Q 4.0, gain 0.80 | **0.098** | **0.159** |
| 3-pole, 1-pole BP (skirt below the corner) | Q 2.5, gain 1.00 | 0.258 | 0.190 |
| 3-pole, 1-pole RAW (skirt above the corner) | Q 4.0, gain 0.80 | 0.163 | 0.158 |

Property distances, `|error| / tolerance`. **Each structure was given its own
(Q, gain) fit against the development case only**, so this is each structure at
its own best, not the shipped setting against a tuned rival.

**Read that first row as the structure, not as a setting to ship.** What ships
is the two-pole at its *committed* `CY_HI_Q = 2.5`, `AMP_CY_HI = 1.0`, which
reads `Band energy` **0.501** and `band decay` **0.253** on D14A. The Q 4.0 /
gain 0.80 column is the two-pole's own best available fit, carried here so the
comparison is structure-against-structure; whether to ship it is a separate,
continuous retune, filed and not taken (see Alternatives).

The two-pole is better on the development case *and* the holdout rejects both
candidates. Every arm is evaluated on the holdout at the setting its **D14A**
fit chose, with the shipped setting carried alongside:

| holdout `D14B` = `cy8/CY2500.WAV` | setting | `Band energy` |
|---|---|--:|
| 2-pole, its D14A fit | Q 4.0, gain 0.80 | 3.794 |
| 3-pole BP, its D14A fit | Q 2.5, gain 1.00 | 3.767 |
| 3-pole RAW, its D14A fit | Q 4.0, gain 0.80 | 3.812 |
| **2-pole, shipped** | **Q 2.5, gain 1.00** | **3.566** |

The shipped two-pole is the best of the four on the holdout, and it is the row
`docs/scorecard/results/D14B.json` records, since that file is a record of what
ships. Every arm is 3.5–3.8 tolerances out, so the holdout separates nothing
about this structural question — it says the whole voice is wrong at this knob
setting, which is finding 5 below.

**What `scorecard.compare` actually returns is `INCOMPARABLE`, on all four
pairings, not `REJECT`** — `total decay` is a required component and is invalid
on the reference side of D14A and on our side of D14B, so neither case has a
verdict to compare (see Consequences). Read over the properties that *do* have
distances, under the same rule, it is **REJECT** for both candidates on both
cases: no required property improves by more than `DEFAULT_ALLOWANCE` (0.05) and
`Band energy` regresses beyond it on the development case. That indicative read
is what this decision rests on, and it is labelled INDICATIVE everywhere the
probe prints it.

### Five findings that stand on their own

1. **At 48 kHz, "a 6 dB/octave skirt" is only available on the low side.** A
   one-pole section at 10.5 kHz has `r = e^{-w0} = 0.253` — nearly at the
   origin — and only 1.19 octaves of spectrum remain above the corner. DERIVED,
   before any audio: the low-pass orientation delivers **2.0 dB/octave** above
   its corner, not 6. Only the high-pass orientation gives a genuine 6 dB/octave
   skirt, and it gives it *below* the corner, which is where a high-pass's skirt
   belongs and what reference 10 calls Hh3.

2. **The price is larger than #102 quoted, and the extra is not area.** A
   cascade in this bank is a mode TAP feeding another mode's excitation
   (contract 15.5), so it costs:
   - a **24th PATH** (`N_PATH` 23 → 24) as well as the 17th mode #102 prices;
   - **18.1 dB of level** thrown away by `TAP_SHIFT = 3`, which the
     destination's `amp` register **cannot recover** — `amp` is Q0.16 and
     saturates at 1.0. It has to come back from the path word's *second*
     envelope slot (`e1 + e2` with the same index is an exact ×2) and from the
     drive envelope's peak;
   - a 16-bit rail on a tap `M_CYHI` has never had to face;
   - and **the cascade order is forced**: `PATH.src` is 5 bits, so `SRC_TAP + m`
     only reaches `m ≤ 15`. **Mode 16 cannot be tapped at all.** The two-pole
     must be the first stage. The other order needs a 26-bit path word.

   None of this was re-measured in µm² because the structure lost on fidelity
   first; `docs/integration-area.md`'s MODES 16 → 17 figures are **not**
   re-verified by this record and must not be cited as if they were.

3. **A cascade costs 1.25 points of band share before it filters anything.** The
   tap is read before the bank steps, so the decay band arrives one sample late
   relative to the short and low bands and the three interfere differently.
   Measured with a cascade stage that is not a filter (`a1 = a2 = 0`, `y = x`):
   1.25 points of band share and −2.75 dB of peak, with per-band energy
   reproduced to **0.006 dB** once the other two bands are silenced. Any filter
   a cascade adds has to beat that, not the two-pole's own numbers.

4. **`AMP_CY_HI` is already at the register ceiling, so the gain axis is
   one-sided.** It is 1.0 and `amp` is Q0.16; every `gain > 1.0` in the grid
   renders identically to `gain = 1.0`. The cymbal's high band cannot be made
   louder without moving the drive.

5. **The holdout does not separate this structural question, and that is its
   most useful result.** All four arms read `Band energy` 3.566–3.812 on
   `cy8/CY2500.WAV` — a spread of 0.25 tolerances across a 3.5-tolerance error.
   A held-out case that answers "every candidate is equally wrong" has not
   failed; it has said the error under test is not the dominant error at this
   knob setting. The dominant one is the CY DECAY law: at knob 0.0 our render's
   T20 is ~830 ms against the recording's 456 ms. **A structural decision taken
   on the development case alone would have had no way to know that**, and it is
   why the criterion was "source a holdout", not "let the holdout pick the
   winner". It is also why no `µm²` was spent: the 17th mode was being priced
   against a 0.25-tolerance difference sitting inside a 3.5-tolerance one.

## Alternatives considered

- **Ship the `Q 2.5 → 4.0` retune instead** — rejected, and the measurement is
  the reason DR 0015 exists. That retune takes the five-band cost from 18.1 to
  6.0 and takes the scorecard's `Band energy` distance from **0.501 to 0.726**,
  i.e. the judge gets worse while the rejected measure improves threefold. The
  five-band cost is not a monotone surrogate for the board on this voice.
- **A second independently-tuned biquad on the decay band** — already measured
  and already worse (five-band cost 10.3, `hh_probe.py` STEP 1c). It is *not*
  the same experiment as a third-order Hh3, and #97 briefed an agent out of
  exactly that conflation in exactly this filter; the two are named apart in
  `hh_probe5.py` so the next reader cannot merge them.
- **Widen `PATH.src` to 6 bits so either cascade order is available** — not
  taken. It is a contract change (`path` 25 → 26 bits) bought for a structure
  that loses on fidelity.
- **Trim the high band's level rather than its Q** — `Band energy` 0.501 → 0.098
  comes from `gain 0.80` at Q 4.0, and Q alone at gain 1.00 makes it worse
  (0.726). So the lever on this property is the **level**, not the Q. Not taken
  here: it is a refit of an already-fitted parameter, supported by one
  development case, on a holdout that reads 3.8 for every candidate. Filed
  separately rather than smuggled in beside a structural decision.

## Consequences

- **Reference 10 §10 now has two documented omissions, not one.** Hh1 (2.5 kHz
  low-band, already recorded) and Hh3's third pole. Both are recorded in
  `drums_fx.py` beside `CY_HI_HZ` and neither is a defect to be fixed by adding
  a mode.
- **`docs/scorecard/results/D14B.json` now exists** — the cymbal's first
  held-out recording. It is a **no verdict**: `band decay` and `total decay` are
  invalid on *our* side because the committed CY DECAY law at knob 0.0 renders a
  T20 of ~830 ms against the recording's 456 ms. That number is the holdout
  doing its job, and it is a bigger error than anything this decision is about.
- **Neither cymbal case can produce a verdict at all today**, and that is a
  coverage hole this record makes explicit rather than closes: `total decay` is
  a *required* component of D14A and D14B, and it is invalid on the **reference**
  side of the anchor (`CY5025` ends before its own decay does) and on **our**
  side of the holdout. `scorecard.compare` therefore returns INCOMPARABLE for
  every pairing, correctly — "a state that is not a verdict cannot be improved
  upon or regressed from". The structural decision above rests on the property
  vector inside those no-verdicts, read under the same rule, and is labelled
  INDICATIVE everywhere it is printed.
- **A misaligned surrogate must not be allowed to choose the baseline.** This
  probe's first complete run reported ACCEPT for both 3-pole arms, because the
  shortlist was the surrogate's top two and the surrogate's favourite 2-pole
  (Q 4.0, gain 1.00, cost 6.0) is one of the worst settings in the grid on the
  scorecard. Adding the shipped setting to every shortlist flipped the verdict.
  DR 0015's "a surrogate proposes, the exact rule accepts" is necessary and not
  sufficient: the rule accepts a *comparison*, and the surrogate was proposing
  both sides of it.
- **Wrong-then-right, twice, and the second was caught by re-running rather than
  by reading.** The first is the surrogate-chose-the-baseline ACCEPT above. The
  second is in this document: its holdout table labelled the **3.794** row
  "2-pole, shipped Q 2.5 / gain 1.00". 3.794 is the two-pole at its *fitted*
  Q 4.0 / gain 0.80; the shipped setting reads **3.566**, and 3.566 is what the
  committed `D14B.json` records. Re-running `hh_probe5.py` at the commit this
  lands on and diffing its printout against the prose caught it; reading the
  prose did not, twice. The verdict does not move — the shipped two-pole is the
  *best* of the four on the holdout, so the corrected number strengthens the
  decision — but a table that mislabels which setting produced a number is how a
  later reader ships the wrong one. **Any number quoted here can be regenerated
  with `python3 tools/probes/hihat/hh_probe5.py --refs <corpus> --out <dir>`**,
  which prints the full grid, both fits, both cases and every verdict.
- **`cy8/CY5025.WAV` is TONE 5.0, DECAY 2.5 — not the chart condition.** Five
  places in this repository called it "TONE 5.0, DECAY 5.0, Roland's own chart
  condition". `tools/probe_new_voice_knobs.py` findings 2 and 4 established the
  correct decode and the correction was never carried back. Corrected here. No
  measurement moves — every CY figure was taken on that file and still is — but
  what the file *is* moves, and with it what a fit to it generalises to. The
  same decode fixes `drums_fx.CY_DECAY_T20`, whose five T20s were monotonic all
  along: the "out of order" file it excluded was knob 10.0 read as knob 2.5.
- **The 16 unread cymbal recordings are now the holdout pool**, and `CY2500` has
  been spent. `hh_probe5.py` lists the nine that prior fits have read, so the
  next agent can pick a fresh one instead of re-deriving the list.
