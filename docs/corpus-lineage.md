# The reference corpus by lineage: which machine, which session, which group

**One deliverable, issue #520 (part of #158): the recordings every acoustic
measurement here rests on, documented by SOURCE LINEAGE rather than by
filename, with the group each lineage may be read in and the licence that
bounds it.**

Manifest: [`corpus-lineage.json`](corpus-lineage.json).
Checker: [`tools/corpus_lineage.py`](../tools/corpus_lineage.py), validated by
[`tools/test_corpus_lineage.py`](../tools/test_corpus_lineage.py).

```
tools/corpus_lineage.py check     every rule; 1 on a violation
tools/corpus_lineage.py list      every pack, its lineage status and its groups
tools/corpus_lineage.py open      the fields an operator with the corpus must still fill in
```

**The one-paragraph answer.** Ten packs are reachable from this repository and
they are **not ten machines**. One documented TR-808 unit — Michael Fischer's
serial no. 103852, recorded in 1994 — supplies every scorecard verdict and
every perceptual-gate bar, and the `808*` directories in
tidalcycles/Dirt-Samples are **byte-identical re-pressings of it**. Three
commercial Samples From Mars packs descend from a *second* 808, but that is a
**vendor claim nothing has measured**: the check that would settle it,
`model/measure_harness.descent_test`, has never been run against any of them.
Two more 808 packs (Apple's "Boutique 808", Ableton's factory kit) have no
documented provenance at all; one of them does not even decode. On the Moog
side, two Samples From Mars packs and Legowelt's 222 files exist and **none of
them carries panel settings**, which is why capability nodes M3 and M4 are
still blocked. So: **one documented 808, one documented Minimoog, and a pile of
material that is weaker evidence than its file count suggests.**

> **This host had no corpus mounted.** The manifest was built on 2026-10-02
> from committed repository state only — `GF180_TR808_REFS` was unset and
> `~/dev/refs` did not exist. Every field is sourced to a committed file. A
> field that cannot be established that way carries the literal string
> `unknown -- operator with the corpus mounted must fill this in` and is listed
> by `tools/corpus_lineage.py open`; **five** such fields exist today — the unit
> and chain of `boutique-808` and `ableton-factory-808`, and the chain of
> `legowelt-minimoog-5529`. They are **declared gaps, not estimates**:
> `check --strict` exits 2 while any remains.

> **Wrong before it was right, once, and the control caught it.** The first
> version of rule R5 below banned every lineage that was not `documented` from
> both `threshold-calibration` and `held-out-validation`. Running the checker
> against the committed manifest — before committing it, per `CLAUDE.md` —
> turned it red on two packs, because the f0 discrimination band's floor really
> is taken from two *claimed* Samples From Mars editions, and legitimately so:
> a session-to-session difference is well defined without knowing which machine
> it is. **The rule was wrong, not the state.** R5 now has two tiers and the
> live case is permitted *with a written justification*. An unsatisfiable gate
> would have been worse than no gate, and the only reason this one is not in the
> tree is that it was run before it was committed.

## Contents

- [What this document is not](#what-this-document-is-not)
- [The packs, by lineage](#the-packs-by-lineage)
- [The three groups, and the rule that assigns them](#the-three-groups-and-the-rule-that-assigns-them)
- [The three targets, which are not the same question](#the-three-targets-which-are-not-the-same-question)
- [Adding a pack: the procedure](#adding-a-pack-the-procedure)
- [Where the corpus path comes from, and four places it disagrees with itself](#where-the-corpus-path-comes-from-and-four-places-it-disagrees-with-itself)
- [What is mechanical here and what is not](#what-is-mechanical-here-and-what-is-not)

## What this document is not

- **It is not audio and never carries any.** `*.wav` and `refaudio/cache/` are
  gitignored; the commercial packs below are not redistributable and nothing
  here quotes, excerpts, embeds, transcribes or otherwise re-identifies them.
  What is recorded is a name, an archive SHA-256 the vendor's own file already
  hashes to, a processing description, a licence, and how an operator loads it.
- **It is not a measurement set.** It says where recordings came from. What they
  *measure* is in `docs/drum-verification.md`, `docs/discrimination.md`,
  `docs/bd-repeatability-measurement.md` and the scorecard.
- **It does not cover software references.** `refprofile/` is plugin-rendered
  audio from four qualified rigs (`surge-type2`, `modeld`, `miniv3`, `diva`),
  where lineage is a plugin build and a licence state rather than a machine.
  That has its own apparatus document, `refprofile/README.md`, and its own
  disqualification record; mixing the two would blur exactly the boundary
  `docs/reference-integrity.md` was written to hold.

## The packs, by lineage

Pack ids are the manifest's. `tools/corpus_lineage.py check` fails if this table
and the manifest disagree, in either direction, including on order.

<!-- corpus-lineage:packs -->
| pack | instrument | unit | lineage | dry/processed | groups |
|---|---|---|---|---|---|
| `fischer-tr808-103852` | Roland TR-808 | **s/n 103852**, a real machine | documented | dry (individual voice outputs) | threshold-calibration, held-out-validation |
| `dirt-samples-808` | Roland TR-808 | s/n 103852 — **the same bytes** | documented | dry | *none — would double-count one lineage* |
| `808-from-mars` | Roland TR-808 | Samples From Mars's machine, serial unpublished | claimed | processed (**including the subset named "Clean"**) | analyzer-development, threshold-calibration |
| `808-from-mars-legacy` | Roland TR-808 | as above — a **second session**, years apart | claimed | processed | threshold-calibration |
| `808-loops-from-mars` | Roland TR-808 | as above | claimed | processed | *none — never read* |
| `boutique-808` | "a TR-808" | unknown | unknown | unknown | analyzer-development |
| `ableton-factory-808` | "a TR-808" | unknown | unknown | unknown | *none — does not decode* |
| `mini-from-mars` | Moog Minimoog | Samples From Mars's machine, serial unpublished | claimed | processed | *none — never read* |
| `micro-from-mars` | Moog Micromoog | Samples From Mars's machine, serial unpublished | claimed | processed | *none — never read* |
| `legowelt-minimoog-5529` | Moog Minimoog | **s/n 5529**, Legowelt's 1970s unit | documented | unknown | analyzer-development |
<!-- /corpus-lineage:packs -->

The manifest carries, per pack, the fields this table cannot: known settings,
the full processing chain, the relationships between files, the licence and
redistribution terms, how an operator loads it, whether `descent_test` has been
run, and the committed files each claim is sourced to. Four things are worth
pulling out of it, because they are the facts most likely to be misread.

**1. "An extra library does not establish an extra machine" is not a
hypothetical here — it has already happened once.** `dirt-samples-808` looks
like a second 808 library and is byte-identical to `fischer-tr808-103852`,
cross-correlating at **1.000** (`docs/discrimination.md` §7). Counting it would
have doubled the apparent evidence for free. It is in the manifest *so that a
future reader who finds it does not count it twice.*

**2. "Clean" does not mean dry.** `808-from-mars`'s Clean/Digital subset is the
machine through an **API 1608 console** at minimal gain into an **Apogee
Symphony MKII**, **group-normalised per voice** — the vendor's own About text in
`refaudio/catalog.json` says so. Clean/Tape adds an **Otari MTR-12**. "Color" is
1608 dynamics and EQ. There is no dry subset in the pack. The only dry material
in the whole corpus is the Fischer set's individual voice outputs.

**3. A filename is not a take index.** `refaudio/README.md` and issue #111 both
once stated that the 808 From Mars clean bass drum was "24 settings × 6 takes =
144 files … the only place in the corpus where the same machine plays the same
thing more than once." **It is not.** The trailing `01`…`06` is the **TONE
knob**; the grid is 2 chains × 2 accents × 6 DECAY × 6 TONE with **no take axis
at all**, and there are no Δ = 0 pairs anywhere in the pack. That correction is
`docs/bd-repeatability-measurement.md` §1, established from the recordings
before the file names — a high band climbing 3.97 dB monotonically in the index
while f0 and T20 sit still. **This is the single best argument for this
document existing**: a premise read off filenames survived into two places and
cost a measurement its headline.

**4. The one repeat axis the corpus has is a pair of *editions*, not a pair of
takes.** `808-from-mars` and `808-from-mars-legacy` are the vendor's current and
superseded editions of the same machine — two independent recording sessions
years apart. What that pair measures is **session-to-session** reproducibility,
which is strictly larger than take-to-take and strictly what anyone comparing
against a single recorded reference is exposed to.

### Not acquired, and why — including the one that hurts

- **kb6.de Roland content** — deleted from the site, and carried no explicit
  licence anyway.
- **archive.org `tr-808-samples`** — **excellent provenance** (early-revision
  unit, RME UFX) but **no licence metadata**, and two long continuous FLACs
  rather than per-voice one-shots. This is the painful one: it is the nearest
  thing to a *second documented unit* anybody has found, and it is the single
  acquisition that would move `hardware-variation-coverage` off zero.
- **archive.org `808-for-cmi`** — no licence metadata.

## The three groups, and the rule that assigns them

| group | what may be read from it | what a read there means |
|---|---|---|
| `analyzer-development` | an estimator, metric or probe may be inspected on it, tuned against it, debugged with it | nothing about whether the instrument is right |
| `threshold-calibration` | a tolerance, bar or pass threshold is **derived** from it | the number becomes part of the **apparatus** |
| `held-out-validation` | a **verdict** only — never a parameter | a read is a recorded act (`tools/holdout.py`) |

**The assignment rule**, in full, is in the manifest's `assignment_rule` array
so a tool can read it. In prose:

1. **Assignment is by source lineage, not by file or by pack.** Dry, normalised
   and saturated versions of one recording session of one unit are **one
   lineage** and take **one assignment together**. This is #158's requirement
   stated operationally: you cannot promote the saturated copy of a calibration
   recording into the held-out group.
2. **A lineage in more than one group must name the separation in force
   instead**, in `role_overlap_why`. Silence fails the checker (rule R6).
3. **`lineage_status` is one of `documented`, `claimed` or `unknown`** — the
   unit is identified from a cited source, asserted by a vendor, or neither.
4. **Weak evidence is confined, in two tiers** (checker rule R5) — the
   mechanical form of #158's "treat unknown lineage as weak evidence, not
   silently trusted":
   - **`unknown` ⇒ `analyzer-development` only.** Nothing is established, so
     nothing may rest on it.
   - **`claimed` ⇒ never `held-out-validation`.** A verdict read against a pack
     whose unit identity is unestablished is precisely "an extra library
     establishes an extra machine". It **may** enter `threshold-calibration`,
     but only carrying a non-empty `weak_evidence_why` that states **why the
     calibration does not depend on the unestablished fact**. The two live
     cases say it in one sentence: a session-to-session difference is well
     defined without knowing which machine it is.
5. **A candidate second machine is tested before it is called one.** Run
   `model/measure_harness.descent_test` against the incumbent corpus; a best
   peak-normalised cross-correlation at or above `MATCH_THRESHOLD` (**0.95**) is
   a re-pressing and carries no independent information. Record the outcome in
   `descent_tested`.
6. **An empty group is declared empty.** An empty `held-out-validation` group
   for a voice is a stated no-verdict, never a pass.

### Where the current corpus violates rule 1, and what holds instead

**The Fischer lineage is on both sides of the comparison it decides, and nothing
can change that while it is the only documented unit reachable.**
`tools/perceptual_gate.py`'s `bar_for()` builds each sound's pass bar out of
**Fischer's own adjacent-knob neighbours** — or, for the six no-knob sounds, a
resampling of the same take, labelled `WEAK` wherever it is used — and then
scores a candidate against a **Fischer target**. So one lineage is both the
`threshold-calibration` set and the `held-out-validation` set.

What holds instead is a separation **at setting level, not lineage level**:
`docs/scorecard/cases.csv` marks twenty cases `Holdout`, and `tools/holdout.py`
makes reading one a recorded act — the settings must be committed and clean
before the render, the record names the seal by content hash and commit, a read
is logged, and a second read at a different model state is REFUSED while the
seal is `sealed`. That is a real and checkable guarantee. **It is not the
guarantee rule 1 asks for**, and no claim in this repository should be read as
if it were. Writing that down is the point: the alternative is a clean-looking
three-way split that the corpus cannot support.

The one genuine lineage separation that *does* exist today: **thresholds floored
on Samples From Mars, verdicts scored against Fischer.** The f0 discrimination
band in `tools/run_case.py`'s `TOLERANCE_POLICY` takes its floor from the
session-to-session spread measured on `808-from-mars` / `808-from-mars-legacy`
(`docs/bd-repeatability-measurement.md`), while every scorecard verdict is read
against `fischer-tr808-103852`. Different lineage on each side. That is what
rule 1 looks like when the corpus allows it.

## The three targets, which are not the same question

#158's third requirement, and the one most easily lost: **do not widen a
tolerance to cover a miscellaneous collection and call the result hardware
fidelity.**

| target | the question | current work |
|---|---|---|
| `designated-recording-match` | does the instrument match **this recording, of this unit, at this setting**? | **the whole scorecard** (`tools/run_case.py`, `docs/scorecard/`) and `tools/perceptual_gate.py rank`/`prove`. Target *and* bar are Fischer s/n 103852 |
| `hardware-variation-coverage` | does it sit inside the spread **real units of this model** show? | **NONE.** No work here addresses it, because no second documented unit is reachable |
| `processed-pack-distribution-match` | does it sit inside the distribution of a **commercial pack**? | `docs/bd-repeatability-measurement.md` on the 808 From Mars grid and its two editions — used to **floor a tolerance**, which is legitimate, and not hardware fidelity |

**`designated-recording-match` is an engineering target and a good one.** It is
well posed, it is measurable, and its answer is a distance from one machine's
components, trimmers and 1994 afternoon. `docs/discrimination.md` §7 states the
consequence in capitals: *every number here is a distance from one machine, not
from the 808.*

**`hardware-variation-coverage` is at zero and the temptation is specific.**
`docs/tr808-reference.md` §1.7 gives ±10 % on an oscillator's f0 from ±20 %
capacitors and ±5 % resistors. That is a **unit-to-unit** figure: it says where a
randomly drawn 808's f0 may sit relative to the design value. It is the right
number for finding a real partial near a nominal frequency, and
`tools/run_case.py` says in so many words why it is the **wrong** number for a
scorecard tolerance: *matching a recording to ±10 % of a unit-to-unit spread
grants our model the whole population's variation as free credit against a
single member of it.* Measured cost: on the bass drum the 10 % tolerance was
5.06 Hz while everything the machine's own DECAY and TONE controls do to its f0
across the full grid is 3.66 Hz — the tolerance was **1.38× the travel of the
thing it scored**, so no two settings of the voice could be told apart. **A
population tolerance is not population coverage.** The only route to this target
is more documented units.

**`processed-pack-distribution-match` is a third thing, and flooring a tolerance
with it is the legitimate use.** A pack's spread is its console, its converter,
its per-voice group normalisation and its editor's trims as much as its machine
— `tools/measure_repeatability.py --self-test` control 3 measures precisely
that editing noise and calls it the floor, *"a machine spread beneath it is not
a measurement."* A floor derived there is honest. A fidelity claim derived there
is not.

## Adding a pack: the procedure

1. **Ask what lineage it is, before anything else.** Is it a new recording of a
   new unit, a new recording of a known unit, a new *edition* of a known
   recording, or a re-press? Those four have different evidentiary value and
   only the first is "another machine".
2. **Run the descent test.** `model/measure_harness.descent_test` against the
   incumbent corpus. ≥ 0.95 ⇒ a re-pressing; record it and stop calling it a
   second machine. `tools/measure_conga_body_spread.py --descent` is a worked
   caller, and `tools/probes/conga_harness_equivalence.py` carries its
   start-red control (a copy whose threshold was raised from 0.95 to 1.01
   reports NOT EQUIVALENT).
3. **Add a manifest entry** with every required field. If a field cannot be
   established, write the `unknown_marker` string — do **not** estimate, and do
   **not** leave it blank: `tools/corpus_lineage.py check` fails a blank and
   *reports* a marked unknown.
4. **Set `lineage_status` honestly.** A vendor's word is `claimed`, not
   `documented`. Marking a pack `documented` while its **unit** is still
   unknown is checker rule **R3**, and it is the specific input that defeats
   rule R2 — it was written because relabelling is the obvious way to smuggle
   weak material into a verdict. R3 is scoped to `unit` on purpose: a published
   serial number with an undocumented recording chain (`legowelt-minimoog-5529`)
   is a documented unit with an open field, not an undocumented lineage.

   **R3 itself was defeated on review, by a one-word variant of the input it
   was written to catch** (PR #523). Its established-ness test asked only
   whether `unit` held the verbose `unknown_marker`, so `unit: "unknown"` or
   `unit: "TBD"` with `lineage_status: "documented"` passed clean — and with
   `roles: ["held-out-validation"]` a pack whose machine nobody has
   established backed a verdict with the checker green. R3 now has two halves:

   - **R3a, deny** — `unit` may not be *any* recognised phrasing of "not
     established" (`corpus_lineage.UNESTABLISHED_TOKENS`: `unknown`, `TBD`,
     `?`, `not established`, … matched against the whole field, plus the
     canonical marker). A deny-list is always one synonym behind, which is why
     it is only half the rule.
   - **R3b, positive identity** — a `documented` unit must *identify a
     machine*: a serial number (what every documented pack here has — Fischer
     s/n 103852, Legowelt s/n 5529), or a written `unit_identity_why` saying
     what pins the identity instead. A positive assertion cannot be routed
     around by a phrasing the deny-list has not met yet.

     A serial is recognised only in an explicit form: `serial`, `serial no.`,
     `serial number`, `s/n` or `s.n.` (any case), then nothing but spaces or
     `: # -`, then at least three digits — e.g. `serial no. 103852`,
     `Serial Number: 103852`, `S/N 103852`. Any word in between ends the
     match, so `serial unknown, bought 1984` does **not** name a unit; before
     #527 it did, on its purchase year. A `.` is part of `no.` and `s.n.`
     and nowhere else, so `no serial. 1984 production` does not reach across
     the sentence boundary. Two more rejections: a negation in the word
     directly before the keyword (`no serial 1984`, `missing serial: 1984`,
     `unknown serial #1984`, `lost s/n 1984`, `n/a serial 1984`,
     `w/o serial 1984`, `no—serial 1984` — a *denial* of a serial; a slashed
     word is read whole and denied if it or any `/` part is a negation —
     `used / no serial 1984`; `yes/no serial` is thus over-rejected — and an
     en/em dash separates like `-`), and
     a serial that is one repeated digit (`s/n 0000`, `serial 1111` — a
     placeholder).

   These are the residual weaknesses, stated rather than hidden and each
   pinned by a test in `tools/test_corpus_lineage.py`:
   - a sentence in `unit_identity_why` satisfies R3b without a serial;
   - the grammar checks that a serial is *asserted*, not that it is *true* —
     `serial no. 1984` passes;
   - the negation check reads exactly one word, so a denial anywhere else
     passes — `no recorded serial 1984`, `missing the serial 1984`,
     `unknown, serial 1984`, `serial 1984 (not really: purchase year)`;
   - the deny-list is finite, so a negation word it does not contain passes
     even in that one slot — `undocumented serial 1984`, `unspecified serial
     1984`, `nonexistent serial 1984`.

   So the rule does **not** guarantee that a passing unit is a claim that a
   serial is known. It guarantees that passing takes text in the diff with a
   serial-shaped assertion (or a written `unit_identity_why`), not a blank, a
   placeholder, a stated unknown in the forms above, or a number that merely
   sits near the keyword. Prose that still denies the serial is left to the
   reviewer reading the diff. The same shape was then fixed one rule over —
   `weak_evidence_why`, `role_overlap_why`, `group_why` and a target's
   `current_work` were each satisfied by `"TBD"` and now are not.
5. **Assign a group, or state why none.** Then add the row to the table above.
6. **Never add the audio**, and never add anything from which the audio could be
   reconstructed or identified beyond its own published archive hash.

### If the lineage is unknown

Treat it as **weak evidence, not as a smaller version of good evidence**. In
concrete terms: `analyzer-development` only, never a threshold, never a verdict,
and the uncertainty travels with every number taken on it. `boutique-808` is the
worked example and it is instructive — it is used for exactly one question
(*can a sound that is not this exact Fischer unit pass the gate?*) and that
question is **meaningless if it turns out to be that unit re-pressed**, which
nobody has checked. A pack can be useful, currently used, and still carry an
open hole at the centre of its one job.

## Where the corpus path comes from, and four places it disagrees with itself

The canonical resolver is `tools/run_case.py`:

```python
REFS_ENV = "GF180_TR808_REFS"
REFS_DEFAULT = "/tmp/tr808-ref"

def configured_refs() -> pathlib.Path:
    """The one place the corpus location is decided: ${GF180_TR808_REFS}, else
    /tmp/tr808-ref. The CLI default and the tests both read it here."""
    return pathlib.Path(os.environ.get(REFS_ENV) or REFS_DEFAULT)
```

The layout the code assumes under that path is **the Fischer repository root**:
`<refs>/<voice>8/<NAME>.WAV`, e.g. `bd8/BD5050.WAV`, `cy8/CY5025.WAV`
(`model/drum_verify.py`'s `REF_MAIN`). Integrity is pinned: `PINNED_COMMIT =
"85fbecf"` in `tools/clap_d12a_probe.py`, whose `verify_manifest()` checks every
file against `tr808-fischer-85fbecf.sha256` and whose `basis()` **REFUSES** when
the corpus git head does not start with the pin. Set
`GF180_REQUIRE_TR808_REFS=1` where a missing corpus must fail rather than skip,
*"because a required job that goes green through skips has checked nothing."*

**"The one place the corpus location is decided" is not, today, the only place.**
Four readers resolve it differently, found by grepping every corpus-path
reference in the tree on 2026-10-02:

| reader | what it resolves | consequence |
|---|---|---|
| `tools/run_case.py` `configured_refs()` | `$GF180_TR808_REFS`, else `/tmp/tr808-ref` | the canonical convention |
| `tools/measure_promoted_bands.py` | same variable, but default `~/dev/refs/sounds-tr808-fischer` | a **different layout** is the default: one level deeper than what `fpga/reports/r2/settled/go.sh` exports (`GF180_TR808_REFS=/home/ubuntu/dev/refs`). Both cannot be the corpus root |
| `tools/measure_partial_balance.py` | `$TR808_REFS`, else `/tmp/tr808-ref` — **a different variable name** | setting `GF180_TR808_REFS` does not reach it. Three probes (`rs_guard_band`, `rs_mode_drive`, `balance_line_shape`) accept **both** names, which is how the divergence stayed invisible |
| `tools/probes/estimator_defects.py` | `rc.REFS_DEFAULT` **directly**, not `configured_refs()` | `$GF180_TR808_REFS` has **no effect** on this probe; it only ever looks in `/tmp/tr808-ref` |

This is a *preconditions assumed rather than asserted* defect of the shape
`CLAUDE.md` warns about — a correct instrument in a wrong state, where the wrong
state is "pointed at a path the operator thinks they configured". It is **not
fixed here**: this issue is documentation, the fix touches five modules and
needs its own controls, so it is filed as **#522**. What this document does is
stop it being invisible.

## What is mechanical here and what is not

**Mechanical** — `tools/corpus_lineage.py check`, run by `pytest tools/` and so
by `make verify`. Eleven rules, each with an injected-defect control in
`tools/test_corpus_lineage.py`:
<!-- claim: test=tools/test_corpus_lineage.py::test_the_committed_manifest_and_document_pass_every_rule -->

- required fields present and non-empty, with any recognised phrasing of "not
  established yet" reported OPEN rather than read as a value (R1); lineage
  vocabulary (R2); **`documented` with no established unit is FALSE — both as a
  placeholder (R3a) and as a unit that names no machine (R3b)** (R3); group vocabulary
  (R4); **weak evidence confined, in two tiers, with a claimed lineage barred
  from verdicts and justified before it calibrates** (R5); multi-group packs
  name their separation (R6); groupless packs say why (R7); **the table in this
  document and the manifest are the same packs in the same order** (R8); group
  and target names appear in both files (R9); exactly three groups and three
  targets, each with a definition and a `current_work` statement (R10); every
  `evidence` path exists (R11).

**Not mechanical, and nothing in this repository can make it so:**

- **What a probe actually reads.** A pack could be listed with no roles and
  still be loaded by a script. `roles` is a declaration of intent that a
  reviewer checks against the diff. R5 has teeth only because `roles` is also
  where a reader looks.
- **Whether a lineage claim is true.** `descent_tested` records whether the
  check has been *run*, not what it found; the finding goes in `relationships`
  with its citation. Six of ten packs say `no -- never run`.
- **Whether a group assignment is the right one.** The checker enforces that the
  rule was applied, not that the judgement was sound.
- **Every phrasing of "I have not established this."**
  `UNESTABLISHED_TOKENS` is a deny-list and a deny-list is always one synonym
  behind: a field whose value is an unestablished fact written as a sentence
  ("we never worked this out") still reads as established to R1. The one field
  where that mattered — the `unit` of a `documented` pack — is therefore
  *also* guarded positively by R3b, and `unit_identity_why` is R3b's own
  stated escape. For every other field, R1 reports the phrasings it knows and
  nothing more.
- **Whether a name the document mentions is still described correctly.** R9 is
  a substring test: a group or target name appearing anywhere in this file
  satisfies it, including inside a sentence saying the group is unused. It
  catches rename drift and nothing else.
- **Whether a written justification is a good one.** R5/R6/R7/R10 now reject a
  placeholder (`"TBD"`) as well as a blank, but any plausible sentence
  satisfies them. They enforce that somebody wrote a reason down where a
  reviewer reads it.
