# Measurement provenance: render, analyse, accept

**The question this answers: did the sound change, or only the measurement?**

This repository has withdrawn eight-plus measurements because the *method* was
wrong — a Hann-windowed 700 Hz split off by 15×, a moving average used as an
envelope on a 56 Hz carrier, window leakage reported as 25 dB of separation, an
amplitude-weighted centroid reading a quiet wideband floor as brightness. Each
time, every number that method had produced became uninterpretable. And when the
snare went **8.8 → 7.6 → 3.4**, part of that was a real fix and part was a wrong
TONE law in the measurement harness; the two were separated *that time*, by
hand, because someone thought to look.

Three activities, each separately identified and separately hashed:

```
render    source + config             →  raw WAV and numerical traces
analyse   retained WAV + reference    →  measurements
accept    measurements + criteria     →  verdict
```

| file | what it is |
|---|---|
| `tools/manifest.py` | the scheme: `render` / `load_render` / `measurement` / `analyse` / `accept` / `diagnose`, and every refusal |
| `tools/provenance.py` | the provenance primitives, extracted from `tools/run_case.py` rather than re-derived |
| `tools/stage_case.py` | the three stages on a real case — the BD's decay — with the demonstration and the injected controls |
| `tools/provenance_retention.py` | the retention classes, and the grouping step CI uploads |
| `model/sound_report.py` | `LOCK_CHANGELOG` / `check_lock_changelog`: when each bound moved and why |

```
tools/stage_case.py demo        the end-to-end demonstration
tools/stage_case.py controls    every injected control
tools/stage_case.py list        analysers, sound variants, injections, retention
model/sound_report.py --check-locks
```

`.github/workflows/provenance.yml` runs all of them on every push.

---

## 1. Why the ids are built the way they are

```
runs/<render_id>/manifest.json     what was rendered, and from what
runs/<render_id>/raw.wav           the retained audio
runs/<render_id>/traces/*.json     per-millisecond peak of each drum bus
runs/<render_id>/retention.json    which clock this record expires on
jobs/<job_id>/analysis.json        the measurements
jobs/<job_id>/traces/*.json        the fit region the verdict rests on
jobs/<job_id>/accept.json          the verdict, the bounds, and any bound change
```

| stage | what is in the id | what is deliberately NOT |
|---|---|---|
| `render` | the case, the exact config (including `model/drums_fx.py`'s hash and the injected variant), and the worktree state — commit **plus** a hash of the uncommitted tree | the clock |
| `analyse` | the **render id**, the analyser's own name and version, and this call's config (which carries the sha256 of the audio actually read) | the values it computed |
| `accept` | reuses the analysis's job id; the verdict lives beside the analysis it judges | — |

The exclusions are the mechanism. A re-analysis of the same retained audio by
the same analyser must land on the **same** id, or "the analysis id differs"
would mean nothing. So:

- render id differs, analyser the same → **the sound changed**
- analyser differs over an unchanged render id → **the measurement changed**
- both differ → **`diagnose()` REFUSES to attribute it**

The analysis id does not include the values. That is on purpose: the id
identifies the method and the input, not the answer, so "same method, same
bytes, different number" surfaces as a delta under a `no change` attribution —
which is a nondeterminism alarm, not an attribution.

The uncommitted-tree hash is in the render id because a clean commit SHA that
silently means "plus whatever was in the working tree" is worse than no SHA.
That block is `tools/run_case.py`'s own, reused through `tools/provenance.py`
rather than forked into a second format.

## 2. What a measurement must carry

`decay = 127.4` is not a measurement. `manifest.MEASUREMENT_FIELDS` is the
floor, every field is a keyword with **no default** (so an omission is a
`TypeError` at the call site, not a key quietly absent from a JSON file six
months later), and `validate_measurement` re-checks a record loaded from disk.

| field | the wrong answer it prevents |
|---|---|
| `name`, `units` | `CP decay τ 47 ms` was the `E_CPTAIL` **register**, not the voice's decay — and the voice was right all along |
| `analyser`, `analyser_version`, `method` | the Hann-windowed split off by 15×; the amplitude-weighted centroid |
| `selection.hit_index` / `onset_s` / `interval_basis`, `interval_s` | a window cut into the strike |
| `channel`, `resample_hz`, `filter`, `normalisation` | window leakage reported as 25 dB of separation |
| `fft.transform` / `window` / `nfft` / `hop` | the same, from the transform side |
| `fit_quality.uncertainty` / `uncertainty_basis` / `noise_floor_db` / `noise_floor_treatment` | a fit at R² 0.82 and one at 0.996 are not the same evidence — and the withdrawn estimator here fits at exactly 0.823 against the corrected one's 0.996 |
| `reference_identity` | the study that drove our snare with the wrong TONE law |
| the bound's `rationale` (at `accept`) | a test that went green because a tolerance moved |

Two conventions worth stating because they are easy to get backwards:

- **`fft` and `fit_quality` must still be PRESENT when they do not apply.** A
  threshold crossing takes no transform, so it records
  `"transform": "none: a -20 dB threshold crossing"` — not a missing key, and
  not `null`. A null where a transform's size belongs is indistinguishable from
  a transform nobody recorded.
- **`value = None` is a valid, explicit refusal** with a `why`. It is not the
  same as the key being absent, and `accept()` gives it `no verdict` rather than
  comparing it against a bound.

The reference identity distinguishes its two kinds in the string itself, because
they are not the same kind of comparand: `document:` for a figure
`docs/tr808-reference.md` states (carried by `model/drum_verify.py`'s `SPEC`,
with the document's own hash, and saying in as many words that **no recording is
read**), and `model-lock:` for what this model measured at a named commit.

## 3. The demonstration: two defects, same magnitude, opposite cause

`tools/stage_case.py demo`. The transcript, every manifest, **and the raw WAV**
are committed under `docs/provenance/bd-decay-demo/`.

The case is the bass drum out of `model/drums_fx.py`, driven through the
register interface, and both defects are real: each is a bug this project
actually shipped and already keeps as a permanent control
(`docs/verification-rules.md` rule 5) rather than one invented for a document.

| | what it is | BD T20 |
|---|---|---|
| the **measurement** defect | a 5 ms moving-average envelope on a 49 Hz kick — 0.28 of a cycle, so it measures its own ripple. `sound_report.py --inject bd-ma-envelope` | 308 → **207 ms** |
| the **sound** defect | the kick's body Q 40 % low, so it really does decay faster. `sound_report.py --inject bd-decay-short` | 308 → **186 ms** |

From the number alone, −101 ms and −122 ms are the same kind of thing. The
manifests tell them apart:

```
v1 -> v2 on the SAME retained audio
  => measurement changed
     fundamental     49.4084 -> 49.4084    delta     0.0000  tol    2.0  BLIND
     decay tau       149.315 -> 144.0924   delta    -5.2226  tol   36.0  BLIND
     T20            207.0417 -> 307.9792   delta   100.9375  tol   36.0  MOVED
     attack           9.9375 -> 14.5625    delta     4.6250  tol    3.0  MOVED

v2 on clean -> v2 on the broken render
  => sound changed
     fundamental     49.4084 -> 49.4091    delta     0.0007  tol    2.0  BLIND
     decay tau      144.0924 -> 86.4672    delta   -57.6252  tol   36.0  MOVED
     T20            307.9792 -> 186.1042   delta  -121.8750  tol   36.0  MOVED
     attack          14.5625 -> 14.2917    delta    -0.2708  tol    3.0  BLIND
```

The second analysis **re-reads the retained WAV**: `load_render` re-verifies that
the file still hashes to what the render manifest recorded *before any estimator
runs*, and `test_analyse_does_not_render` proves no render happened by making
`render_voice` raise and analysing anyway. A claim that an estimator was re-run
on last week's audio is worth exactly as much as the proof that no render
happened.

Per-metric `MOVED`/`BLIND` is `docs/verification-rules.md` rule 4 applied to a
delta. Four metrics; only two saw the measurement defect, and `decay tau` — the
metric whose *name* most sounds like the one that should have caught a decay
estimator — moves 149 → 144 ms, inside its own 36 ms tolerance. It could not
have caught it.

### The third comparison is the important one

```
v1 on clean -> v2 on the broken render
  => REFUSED: both the audio and the analyser differ between these two analyses
     fundamental     49.4084 -> 49.4091    delta     0.0007  tol    2.0  BLIND  (delta only; not attributed)
     decay tau       149.315 -> 86.4672    delta   -62.8478  tol   36.0  MOVED  (delta only; not attributed)
     T20            207.0417 -> 186.1042   delta   -20.9375  tol   36.0  BLIND  (delta only; not attributed)
     attack           9.9375 -> 14.2917    delta     4.3542  tol    3.0  MOVED  (delta only; not attributed)
```

**The two defects partly cancel.** −101 ms of wrong ruler and −122 ms of wrong
sound leave −21 ms, which is *inside* T20's 36 ms tolerance. A comparison that
attributed this pair would report the metric as unmoved while both halves of it
were broken. That is the snare's 8.8 → 7.6 → 3.4 in miniature, and it is why
refusing is the correct output rather than a cop-out.

Note that the delta is still **reported** under the refusal. REFUSED is about
attribution, not about arithmetic, and suppressing the number would hide the one
fact that makes refusing the right answer. It did, at first: `attribute()`
dropped the values whenever `diagnose()` refused, and the demo printed
`delta nan`. That is one wrong-then-right in this work, caught by running the
demo rather than by reading it.

## 4. accept is a separate stage, and bounds carry their history

A verdict fails differently from an analysis. An analysis is wrong when the
*method* is wrong; a verdict is wrong when the *bound* is wrong, and a bound can
be moved without touching a line of measurement code.

`model/sound_report.py` already separated a **target** (a document states it —
outside it the model is WRONG) from a **lock** (this model at a named commit —
outside it the model CHANGED, which may be the point of the commit). What it had
no room for was *when this one moved and why*: `LOCKS` was an overwrite.

`LOCK_CHANGELOG` is that room. Both of its events were recovered with
`git log -L` on the `LOCKS` block, not reconstructed from memory:

- **28dfd55** (#51) introduced the table at commit `ce400a6`; nothing was pinned
  before, so all 21 entries are first locks.
- **bf13fdd** (#251) added `("SD", "brightness (power centroid)")` so that
  `--inject sd-centroid-amp-weighted` had something to turn red.

`check_lock_changelog()` (`sound_report.py --check-locks` — milliseconds, no
rendering) asserts that the changelog's last recorded value for each lock *is*
the value `LOCKS` holds. A re-lock without an entry is then a detectable state
rather than an invisible one, and `--relock` prints a changelog skeleton beside
the new table so the workflow produces the record.

The target bounds' values live in `model/drum_verify.py`'s `SPEC`; their history
is in `tools/stage_case.py`'s `TARGET_HISTORY` — for the BD that is commit
**7be1490** (DR 0009, #14), which moved `tau_ms` 127.0 → 144.0 and `f0`
56.0 → 49.4 because the circuit's computed resonance replaced the chart figure.

`tools/stage_case.py` never copies a bound. It reads value, tolerance, units,
kind and rationale out of `sound_report.build_properties()`, folds the last
recorded change into the rationale, and **refuses** either to invent a bound for
a metric that table does not declare or to apply one whose history is empty.

### The ledger, and why it is committed

`accept()` records a bound that differs from the last one accepted **for the
same (case, metric) pair**, with both bounds, both rationales, and the job that
set the previous one. That ledger is `spec/acceptance-bounds-history.json` —
tracked, not under the gitignored `jobs/`, because a previous bound that does not
survive a fresh checkout detects nothing: on a clean clone or any CI run the
history would be empty and the gate would pass everything.

At run time `stage_case.py` uses a **scratch** copy *seeded* from the tracked
file: seeded so a bound change is detected in CI, scratch so parallel jobs cannot
lose each other's read-modify-write and so a verification run does not dirty a
tracked file with a new timestamp. `--record-bounds` copies it back, which is the
only thing that moves the committed record, and it shows up as a diff in review.

## 5. The controls

`tools/stage_case.py controls` — transcript committed as
`docs/provenance/bd-decay-demo/controls-transcript.txt`. The clean run's `pass`
is asserted first and the run aborts with `NO VERDICT` if it does not hold,
because a control only counts as caught if the clean run passes
(`docs/verification-rules.md` 5, condition 1).

| injection | what it is | refused at | by |
|---|---|---|---|
| `RENDER_SILENCE` | exact silence — CLAUDE.md's Model D rendering nothing | render | render precondition |
| `TAMPER_RETAINED_AUDIO` | one flipped sample in the retained WAV | analyse | retained WAV hash |
| `MEASUREMENT_NO_UNITS` | `decay = 127.4` with no statement of whether that is τ, T20 or a threshold | analyse | measurement field |
| `BOUND_NO_RATIONALE` | a bound with no recorded reason | accept | bound rationale |
| `BOUND_SILENT_CHANGE` | T20's bound moved onto 207.0 ms | accept | bound-change ledger |

Each prints as a row of a checks × defects matrix, so a check that never fires
is visible. Five injections, five guards, one `CAUGHT` per row and no shared
column: each defect is caught by the check written for it, which is condition 3
of the three (the *intended* assertion is the one that fails).

**`BOUND_SILENT_CHANGE` is the one to read.** 207.0 ms is exactly what the
withdrawn estimator reports, so the injection turns the v1 defect's T20 from a
fail into a **pass** without touching a line of measurement code, and only the
ledger notices. Two things about it were wrong before they were right, both
found by running it:

- it originally ran in a fresh store with no prior bound, so there was nothing
  for the move to differ *from*; it changed nothing, detected nothing, and
  reported `accepted: fail` — a control that had never activated. It now
  establishes the baseline accept first, and refuses unless that baseline
  **fails** and the whitewashed T20 then **passes**.
- whitewashing T20 does **not** buy a green run. `attack`, the other lock on
  this voice, still reads 9.94 against 14.56 ms, so the run comes back
  `fail (BOUND MOVED)`. That is rule 4 paying for itself: more than one property
  saw the defect, so moving one bound was not enough to hide it.

`outcome_of` treats a recorded bound change as its own non-zero regardless: a
verdict that moved because the ruler moved is not a verdict that passed, even
when every metric is inside its bound.

## 6. Where the files go, and for how long

Plain files. No service, no dashboard. `--root` defaults to `build/provenance/`
(gitignored).

Retention is a **class**, not a number in a workflow file, because a reference
fixture and a smoke-test render are both "artifacts" to `upload-artifact` and
must not expire together:

| class | days | what it is |
|---|---|---|
| `reference-fixture` | 90 | the audio and traces a **bound** was derived from |
| `release-evidence` | 90 | what a claim in `docs/` rests on |
| `smoke` | 7 | a per-push render nobody will re-read |

`tools/provenance_retention.py` owns those numbers and marks each record
directory at the point the record is written; the grouping step copies each
record under `<out>/<class>/` and CI uploads each group with the matching
`retention-days`. `tools/test_provenance_retention.py` reads the actual workflow
YAML and asserts the two agree, because otherwise "the number lives in one
place" is a comment rather than a property.

The failure this shape is for is quiet: **the WAV a bound was derived from
expiring on the same seven-day clock as a render nobody will read.** Nothing goes
red. The bound keeps issuing verdicts and simply becomes a number that can only
be re-trusted, never re-derived. So the grouping step REFUSES (exit 2) rather
than shipping a bundle it cannot vouch for — on an empty store, an unmarked
record, an undeclared class, or a mark written by a different version of the
table — and the fixture upload uses `if-no-files-found: error`, because "the
evidence was never produced" must not read as a green job with a note in the log.

### The retained WAV is 16-bit, and that is a real floor

`render()` writes int16 and records both the peak it was handed and the number of
samples that did not fit, because a hard clip is invisible in the WAV afterwards.
−96 dBFS is the floor; anything needing more resolution than that — a decay tail
into the noise floor in particular — should be retained as a float trace via
`traces=`, not read back out of the WAV.

## 7. Relation to `tools/run_case.py` and the scorecard

`tools/run_case.py` renders, measures and scores in **one** pass under **one**
id, and `docs/scorecard/results/*.json` is that pass's output. It is not forked
and not replaced:

- the provenance block is shared code (`tools/provenance.py`), not a second
  format;
- `run_case.py`'s exit-code convention is kept identical — 0 match, 1 mismatch,
  2 no evidence — so a REFUSED stage and a failing bound are different outcomes
  at the shell, not just in prose;
- `run_case.py` already carries `rubric_history` for the case where a *rubric*
  changed under a result; `LOCK_CHANGELOG` is the same idea one level down, on
  the individual bound.

Retrofitting the scorecard's hundred-odd cases onto the three stages is **not**
done here. What is done is the schema, the staging, the attribution, the
retention, and one real case demonstrated end to end with its evidence
committed.
