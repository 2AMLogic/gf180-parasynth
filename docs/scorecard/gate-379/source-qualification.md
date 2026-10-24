# #726: source qualification for a between-recording bar (#560, #379, #282)

**Documentary only.** No audio was rendered, downloaded, measured or committed to produce this report, and nothing
was purchased, installed or requested from a source owner. Nothing here loosens the gate or claims a pass. The gate
stays **ranking-only**.

**Verdict.** **No candidate qualifies today.** One qualifies for a named, finite follow-up (archive.org
`tr-808-samples`), and only after its licence is resolved by the operator. See the verdict at the end.

**Pinned inputs.** `origin/main` @ `1702c8ba` (2026-10-19). `docs/scorecard/gate-379/README.md` was read at that
revision; its last commit is `d0e6bbbc`. Every price, URL and availability fact below was checked on **2026-10-19**.

## 1. What #379 §8–9 actually said (read at the pin, not paraphrased from memory)

| kind of variation | measured so far? | where |
|---|---|---|
| **between-unit** (two different machines) | **No.** No second documented unit has been reachable. Fischer is s/n 103852; the MARS machine's serial is unpublished. | `docs/corpus-lineage.md`, target `hardware-variation-coverage` is at zero |
| **cross-recording** (Fischer vs MARS, setting unmatched) | Yes, and it mixes unit spread, chain, mastering and setting residual together. | README §8 |
| **setting** (adjacent knob step, one unit) | Yes, for the 10 multi-take sounds. This is the existing ranking bar. | README §2 |
| **repeatability** (same setting struck twice) | **No.** There are no Δ=0 pairs in the MARS pack. The trailing `01`–`06` on the BD is the TONE knob. | `docs/corpus-lineage.md` item 3 |

What was refused, kept as stated:
- §8: calibration **REFUSED** by its pre-registered rule (k = 1/3/5: 0/9, 3/9, 5/9 coverage; the 80 % requirement was
  never met). Only 9 of 16 sounds could have been calibrated at all.
- §9: rule v2 **REFUSED** (no selectable p reaches 80 %). Diagnostics were run at **p = 0.75 and p = 1.0 only**:
  Q2 (seeds) 110/121 and 105/121 against a 90 % need; Q4 (untouched best take) 7/9 at p = 0.75. These are
  diagnostics, not acceptance authority. **This report does not claim that every p has been disproven**; the other six
  fractions were swept for split coverage only.
- §9 says the VAL group was not untouched by v2's design, so the MARS data cannot serve as its independent test.
  Anything consulted during rule design (all MARS keys, via §8's exploration) is therefore **not** an independent
  held-out set.

## 2. Candidates (three at most; Fischer is the target, not a candidate)

Blank cells are not allowed. "unknown" means not established from committed or public evidence as of 2026-10-19.

| | **A. MARS 808 (current + legacy editions)** | **B. Apple Logic "Boutique 808"** | **C. archive.org `tr-808-samples`** |
|---|---|---|---|
| **Original provenance** | Samples From Mars product pages (`product_id` 616670 current, 77883969 legacy); catalog hash `f567c676…ca82` (current) | Apple Ultrabeat factory content, `GB_Tasty808_*.aif` | https://archive.org/details/tr-808-samples, https://archive.org/metadata/tr-808-samples. Item added 2026-03-02. Uploader's own account; their contact address is deliberately not copied here |
| **Unit identity / independence** | Serial unpublished. Both editions are the **same machine by vendor claim**; not established different from s/n 103852 (`descent_test` never run). The two editions are one unit, so they count as **one** | **unknown.** `descent_test` never run. Whether it is a re-pressing of Fischer is open, though §5's FAIL on all 16 shows it is not byte-identical to Fischer | Uploader says "my early revision TR-808", no serial. Independence of Fischer rests on the uploader's word alone and is **untested** (`descent_test` needs the audio) |
| **Revision** | unknown | unknown | "early revision" (uploader's words); exact revision unknown |
| **Chain / processing** | Current Clean/Digital: API 1608 at minimal gain → Apogee Symphony MKII, **group-normalised per voice**. Clean/Tape adds an Otari MTR-12. Legacy: undocumented (`about` empty; catalog basis is "indexer inference from folder names"). **Not dry** | unknown. 26 AIFFs, undocumented | Direct into an RME UFX (uploader). Level relationships between sounds said to be preserved, i.e. not per-voice normalised. Noisy pots and audible clock noted by the uploader |
| **Settings / accent** | No panel settings ship. Accent A/B(/C) from filenames. BD only: DECAY × TONE grid. Other voices carry no knob information | none | "Full accent" and "no accent" on one file; "all combinations of knob positions" on the other, **positions not stated**. Metadata has no licence and no knob list |
| **Repeated events** | none (no Δ=0 pairs). The 808 Loops pack has BD-only 4/4 loops, not indexed file-by-file and not the six thin voices. The two editions give a **session pair**, not a take pair | none known | unknown. Two continuous recordings; repeats not stated |
| **Per-voice coverage** | see §3 | see §3 | unknown per voice. Uploader says "each sound", **unverified** (not downloaded) |
| **Permission: calibration and publishing derived metrics** | **Granted, by #111's binding rules, as quoted there:** single-user licence; never commit audio; "measuring against it as a reference target is fine: publish the numbers". The vendor's original text is the library README on operator storage, not in this repo; I did not re-read it | **Unresolved.** Apple factory content "licensed for use in the user's productions, not redistributable" (`corpus-lineage.json`). Whether it covers calibration or publishing derived metrics is not established. Prior use: private cross-check, numbers only | **Unresolved, not granted.** `licenseurl` and `rights` are both empty on 2026-10-19. Public download is not permission |
| **Published acquisition price** | Already owned, no repurchase. Current list price unknown: third-party listings disagree ($8.08 to $39; one mirror, undated). Not relied on | Bundled with Logic Pro; stand-alone price unknown. Not needed if the licence is already held on an operator machine | No price. Free public download, 2026-10-19 |
| **Engineering effort (estimate)** | Already reusable (`refaudio_s3.py`, `gate_calibrate.py`, `gate_between.py`). Nothing new for what it can support | Low: `perceptual_gate.py crosscheck --other` already reads it. Needs a per-voice mapping of the 2 unassigned files, plus a descent test | Medium, roughly 2–3 builder sessions: fetch, FLAC decode, segment two long files into hits, descent test, setting assignment, a REFUSED-if-unsettled check. None of that exists |
| **Further access or operator action** | none | operator-side access to the Logic content, plus a read of the Apple terms | operator decision on licence; the only route is contacting the owner, which this task bars |

## 3. Setting-matched and held-out availability, per voice

Counts are files in the committed indexes. Tape/Digital twins are one recording, and filenames are not takes. "Held-out"
means never consulted in rule design.

| voice | A. MARS current Clean (Digital + Tape twins) | A. MARS legacy | B. Boutique (files mapped in `crosscheck-second-808.json`) | Setting-matched to Fischer? | Independent held-out? |
|---|---|---|---|---|---|
| RS | A, B (4 files, 2 keys) | 12, Tape or Re-Pitched, chain undocumented | 1 | no, no knob data | no (A consulted in §8/§9; B not setting-labelled) |
| CL | A, B (4 files, 2 keys) | 6, same | 1 | no | no, same |
| CP | A, B (4 files, 2 keys) | 33, Tape; unknown whether numbered files are repeats | 1 | no | no, same |
| MA | A, B (4 files, 2 keys) | 19 | 2 | no | no, same |
| CB | A, B (4 files, 2 keys) | 5 | 1 | no | no, same |
| CH | A, B (+ Combo, excluded) (6 files) | 48, 3 Clean | 3 | no | no, same |
| BD, SD, LT/MT/HT, LC/MC/HC, CY, OH | many files (BD 144 Clean); settings are filename-encoded only | present | BD 4, SD 3, CY 1, OH 1. **Toms (3) and congas (3) are one shared set across LT/MT/HT and LC/MC/HC**, so there is no per-voice coverage | BD only, by filename grid on A. Not on B | A: no (consulted). B: partly. §5 read it before rule v2, though no bar or selection was derived from it |
| all 16 on C | unknown | | | **cannot be said**: knob positions unstated | **unknown**, until downloaded and described |

**Missing takes vs missing metadata.** For the six thin voices (RS/CL/CP/MA/CB/CH) MARS and Boutique are missing
**takes** (two keys per voice; nothing to hold out) *and* missing **metadata** (no knob data on any). C is the only
candidate that could supply both, on its claim alone. That claim is unverified: no one has looked at the audio, and
its settings would have to be read off the recording, which is circular unless done independently of our model.

Boutique §5 caveat: its numbers show a second recording also fails all 16 against the Fischer bars, which is evidence
about the bars (§5, `crosscheck-second-808.json`), not a qualification of Boutique as a calibration source.

## 4. Reuse of existing tooling, and what is missing

| need | existing | verdict |
|---|---|---|
| fetch a licensed pack from operator storage | `tools/refaudio_fetch.py`, `refaudio_local.py`, `refaudio_s3.py` (three outcomes, REFUSED normal) | reusable for A; **not** for C: no public-URL fetcher, hash pin or FLAC support (grep of `tools/refaudio_*.py`, `perceptual_gate.py`, `gate_*.py` finds no FLAC or archive.org handling) |
| provenance and group assignment | `docs/corpus-lineage.{md,json}`, `tools/corpus_lineage.py check`; a new pack needs a manifest row with `unknown_marker` fields | reusable. C would enter as `lineage_status: unknown` (`analyzer-development` only) until a unit is documented |
| second-unit test | `model/measure_harness.descent_test` (≥ 0.95 ⇒ a re-pressing) | reusable; required **before** C is called a second machine; never run on B either |
| cross-check against Fischer bars | `perceptual_gate.py crosscheck --other` (reads `.aif/.aiff/.wav`) | reusable for B |
| bar from a pool | `gate_between.py`, `gate_calibrate.py` | reusable only if a pool has ≥ 3 keys per voice and matched settings |
| **missing** | segmentation of continuous recordings into one-shots; a setting-assignment protocol that is independent of our model | **named capability gaps**. Not built here (out of scope; they belong to a separately qualified experiment, not to this report) |

Not duplicated: #562 (diagnostic regeneration) and #682 (calibration dial).

## 5. Verdict and proposed follow-up

- **No candidate qualifies now.** A has the permission but cannot supply setting-matched or independent data for the
  six thin voices, and was consulted in rule design. B has no unit, setting or permission evidence. C has the
  provenance a second unit needs, but no licence, no knob list, and its independence is the uploader's word.
- **One finite follow-up is justified, and is gated on an operator action:** *if and only if the operator obtains a
  licence that permits calibration use and publishing derived metrics for C*, open one issue: fetch C to gitignored
  scratch, run `descent_test` against Fischer, segment, transcribe the knob positions from the item's own notes, and
  report per voice whether any setting-matched pairs exist. It must REFUSE rather than report if the licence record,
  the hash pin or the descent test is missing. It would not set a threshold. Absent that licence, the right answer is
  to keep ranking and stop.
- Not authorized or done here: purchases, hardware, credentials, source-owner contact, gate loosening, an absolute
  pass claim, closing #560 or #282, or any render.

## Wrong-then-right, this report

None found, but nothing here was measured, so this proves little. The risks I checked by hand rather than by control:
the tom/conga sharing in the Boutique cross-check (found by reading the JSON keys), and the legacy "Clean" assumption
(the legacy edition has no Clean folder for five of the six thin voices, only Tape and Re-Pitched; CH is the exception).

*Reviewer spot-checks (suggested):* the quoted #111 licence rules; the archive.org metadata fields `licenseurl` and
`rights` (empty); the 26-file count against the 24 distinct files named in `crosscheck-second-808.json`.
