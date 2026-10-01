# Claim markers: a sentence in a document carrying its own evidence

`tools/compile_dag.py` opens with the rule this file generalises: *a node is not
green because someone wrote that it is.* A **claim marker** applies the same
rule to a sentence of prose. It names the evidence that would make the sentence
true, and `tools/check_doc_claims.py` re-derives the verdict on every run.

**Why this exists.** `docs/failure-modes.md` mechanism 4 — "claims outliving
their evidence" — asks for this tool by name, and then furnished the worked
example. Its own "What is already mechanical, and what is not" section was
committed at 2026-09-18 15:55 (`693b4e6`) listing five items as **"Not yet"**.
Two of them were delivered *later the same day*: the
implementation-versus-fidelity classification (`tools/compile_dag.py`, whose
comment cites "Issue #45") and report staleness (#69, delivered in #93). The
document `CLAUDE.md` tells every session to read first understated the project
for a week. Nobody was careless; there was simply nothing in the loop that
could have noticed.

## The marker

An HTML comment, on the line **after** the claim it backs (or trailing it on the
same line). It is invisible in rendered Markdown.

```markdown
Ground-truth estimator suite.
<!-- claim: test=model/test_audio_measure.py::test_t20_is_ln10_times_tau -->
```

A marker inside a fenced code block or inline backticks is an **example**, not a
claim, and is skipped — otherwise this document, which is nothing but examples,
would be checked as a list of assertions.

Syntax is `<!-- claim: key=value key="value with spaces" ... -->`, tokenised
with `shlex`. **Exactly one** key must be a *kind*; the rest are modifiers or
annotations. An unrecognised key is `REFUSED`, never ignored — a typo in a key
must not be able to hide as free-form prose.

### Kinds

| kind | the claim asserts | checked by |
|---|---|---|
| `test=<pytest nodeid>` | a test demonstrates this | collecting and running that nodeid |
| `grep=<regex> in=<globs>` | this thing exists in the tree | regex search of the matching files |
| `absent=<regex> in=<globs>` | this thing does **not** exist yet | the same search finding nothing |
| `commit=<sha-or-tag>` | this history is behind us | `git merge-base --is-ancestor` against `HEAD` |

`in=` takes one or more comma-separated repo-relative globs (`tools/*.py`,
`model/*.py,spec/*.py`).

### Modifiers

| key | meaning |
|---|---|
| `expect=fail` | on a `test=` claim: this is a **tracked-defect** claim. The backing test must currently FAIL; if it starts passing, the claim is stale. |
| `covers=<path>` | the claim describes that file. If the file has been committed **more recently than the document**, the claim is `STALE` — it predates what it describes. |
| `mechanism=<status>` | this claim's prose is (or includes) a proposed **mechanism**, and `<status>` is its evidentiary status from a closed vocabulary. See "Mechanism claims" below. |

`issue=`, `why=` and `note=` are annotations for a human reader and check
nothing; they exist so that a marker can say *which* tracked defect or *which*
open issue it refers to without that text being mistaken for a key typo.

## Mechanism claims: a measured effect and its explanation are different claims

Issue #135: two renders of the same Diva patch differed in 1,763,954 of
1,764,000 samples — measured, and real. The conclusion drawn from it, that the
cause was an artefact inserted on a wall-clock timer and therefore Diva was a
valid positive control for a demo-artefact detector, was asserted and never
tested. Nothing here re-derives whether a mechanism is *true*; `mechanism=`
only forces whoever writes the sentence to say, in public and from a fixed
list, how much standing the explanation has earned — a **required-presence**
check, not a re-verification, the same distinction `covers=` already draws
between "predates" (checked) and "is correct" (not checked).

### The vocabulary is closed, and the first four are #114's

A `mechanism=` claim's evidentiary status is one of:

| status | what it means |
|---|---|
| `measured` | the mechanism itself was tested — not just the effect it is meant to explain |
| `derived` | follows from documented behaviour (a datasheet, a spec, a changelog, a known circuit) without a new measurement |
| `inferred` | a plausible reading of indirect evidence that does exist, but was not measured for this claim |
| `fitted` | chosen or tuned to match other data; it explains that data by construction, which is not independent support |
| `unverified` | asserted with zero evidence of any kind — weaker than `inferred`, which at least cites something indirect |

The first four are `model/drums_fx.py`'s constant-provenance vocabulary
(`PROV_MEASURED` / `PROV_DERIVED` / `PROV_INFERRED` / `PROV_FITTED`, #114),
reused rather than reinvented because issue #135 asks for exactly that:
*"mark the mechanism's evidence ... exactly as #114 asks for constants."*
`unverified` is new. #114's domain assumes some computation produced the
constant, even a bad one; a mechanism claim can be pure narration with no
computation behind it at all — the Diva "wall-clock timer" story above — and
that needs its own word rather than being folded into `inferred`.

**A missing or unrecognised status is `REFUSED`**, exactly like any other key
on this marker: `mechanism=` with no value, or a value outside the five above,
refuses with the closed list quoted back so the fix is one edit away.

### What this does and does not enforce

`mechanism=` checks that a status was **declared**, not that the declared
status is **true**. A claim marked `mechanism=measured` whose cited evidence
only shows the claim exists (a `grep=` match on the sentence itself, say) is
not caught by this modifier — the same limitation `covers=` already documents
for timestamp ordering, and `grep=` for "a string is present" versus "the code
does what the prose says." Pick `test=` over `grep=` wherever a real
measurement exists to cite.

**Whether a mechanism is load-bearing is not mechanically checked.**
`docs/verification-rules.md` rule 7 states the policy — a mechanism may not
decide a design call, a detector, or a go/no-go while its status is anything
other than `measured` — but enforcing that a *specific* downstream decision
actually obeyed the policy is a human review question, the same way `expect=`
does not stop someone from *acting on* a tracked defect's failing test; it
only keeps the claim about the test honest.

A worked example, from issue #135 itself:

Measured, and sound: two renders of the same Diva patch differ in 1,763,954 of 1,764,000 samples; Mini V3 is bit-identical across the same test.
<!-- claim: grep="1,763,954 of 1,764,000 samples" in=docs/claim-markers.md note="the effect -- re-derivable from this file" -->

Asserted, and never tested: that the cause is an artefact inserted on a wall-clock timer, therefore Diva is a positive control for a demo-artefact detector.
<!-- claim: grep="an artefact inserted on a wall-clock timer" in=docs/claim-markers.md mechanism=unverified issue=135 note="the mechanism -- zero measurement, so not load-bearing" -->

## The three outcomes

```
OK        the evidence was found and says what the claim says
STALE     the evidence was found and CONTRADICTS the claim
REFUSED   the evidence could not be evaluated at all
```

`REFUSED` is a first-class outcome here for the reason `CLAUDE.md` gives: *a
tool that answers when it cannot is worse than one that is absent, because its
output looks exactly like data.* A marker pointing at a deleted test file is
**not** evidence that the claim is false; it is evidence that the checker cannot
answer, and it says so in its own word.

Exit codes follow this repository's convention, where 2 means *no evidence*
rather than *no problem*:

| exit | meaning |
|---|---|
| 0 | every claim OK |
| 1 | at least one STALE — a genuine finding: prose contradicts the tree |
| 2 | no STALE, but at least one REFUSED — the run could not answer |

`STALE` outranks `REFUSED` because it is the more actionable, but both are
non-zero and `make claims` is red either way, deliberately.

## Skipped and xfailed tests: the decision, made explicitly

A claim is backed only by a test that **ran**.

| pytest outcome | plain claim | `expect=fail` claim |
|---|---|---|
| passed | `OK` | `STALE` — cited as failing, now passes |
| failed / error | `STALE` | `OK` — the tracked defect still fires |
| xfailed | `STALE` | `OK` |
| skipped | `REFUSED` | `REFUSED` |
| not collected | `REFUSED` | `REFUSED` |

**A skipped test is `REFUSED`, never `OK`.** A skipped check looks exactly like
a passing one, which is the failure this repository keeps re-discovering; if a
skip could back a claim, then an apparatus that refused to run would silently
certify every sentence that cited it.

**An xfailed test backs a tracked-defect claim** and contradicts a plain one —
it ran, and it failed, in a file that already knew it would. Note that
junit-xml collapses a non-strict XPASS into `passed` and a strict one into a
failure; cite a specific nodeid rather than an xfail-marked family when that
distinction carries the claim.

## What this cannot catch

Say the limits out loud, because a checker trusted beyond its reach is worse
than none.

- **An `absent=` claim is only as good as the regex and globs it names.** If the
  item is delivered under a name the regex does not match, the marker keeps
  reporting `OK` and the "not yet" line stays wrong. The mitigation is partial:
  the checker `REFUSED`s whenever the `in=` globs select **no files at all** —
  whether because the directory does not exist or because the filename pattern
  matches nothing inside one that does — so a typo'd path cannot masquerade as
  a genuine absence. A *correct* path with a too-narrow regex still can: the
  files are read, the regex simply does not match. **When you deliver a "not
  yet" item, move its line and rewrite its marker**; that is part of the work,
  not follow-up.
- **`grep=` proves a string is present, not that the code does what the prose
  says.** Prefer `test=` wherever a test exists. `grep=` is for claims about the
  *shape* of the tree ("a target exists", "a control is wired into the gate")
  where no test is the natural evidence.
- **`covers=` is coarse.** It compares commit timestamps of two paths, so it
  fires when the covered file changes for an unrelated reason. That is the
  intended bias — re-reading a claim is cheap — but it makes `covers=` wrong for
  a file under active development.
- **Nothing here checks that a claim has a marker at all.** Coverage is
  opt-in and currently partial (`docs/failure-modes.md` only, by design: the
  issue that commissioned this asked for one concrete case rather than a
  retrofit of thirty-six documents). An unmarked sentence is exactly as
  unchecked as it was before.
- **A marker in an excluded tree is still silent.** The list below is short and
  enumerated for that reason, but it is a real hole: put a claim under
  `.loom/` and nothing will tell you it is unchecked.

## Which documents are scanned

**This is not a detail.** Until #435 the default set was `docs/*.md` — one
directory, not even recursive — and eleven real markers sat outside it, in
`docs/scorecard/README.md`, `docs/scorecard/ensemble-e1a/rtl/README.md` and
decision record 0018. They were parsed by nothing, reported by nothing, and the
summary read `51 claim(s) in 48 document(s): 51 ok, 0 stale, 0 refused`. A
marker the scanner never reaches is **weaker than a skipped test**: a skip at
least produces a `REFUSED`.

The set is an explicit list in `tools/check_doc_claims.py`, not a bare glob.

**Scanned** (`DEFAULT_INCLUDES`), recursively:

| | |
|---|---|
| `CLAUDE.md`, `README.md` | the root documents. `AGENTS.md` is a symlink to `CLAUDE.md` and is counted once, not twice. |
| `docs/**` | including `docs/scorecard/**`, one directory too deep for the old glob |
| `fpga/**` | `ARTY.md`, `release/RELEASE.md`, `release/R1.md`, `reports/**/README.md` |
| `pnr/**`, `model/**`, `rtl-sketch/**`, `spec/**`, `tools/**` | including `spec/decision-records/**` |
| `refaudio/**`, `refprofile/**` | |

**Not scanned** (`EXCLUDED_PREFIXES`): `.git/`, `.github/`, `.claude/`,
`.loom/`, `.venv/`, `build/`, `node_modules/`. Every one is either vendored
(installed by another tool and replaced wholesale on update) or generated.
`.loom/` matters most: `.loom/worktrees/` holds whole second checkouts of this
repository, so a `**/*.md` sweep would count every claim once per live worktree
and report a stale one against a path that is not the tree you are looking at.

**A marker outside the scanned set is `REFUSED`, by name.** An include list is
just another thing that can silently fail to cover something, so the
no-argument run also reads every git-tracked Markdown file *outside* the set and
refuses on any that carries a marker, telling you to add its directory to
`DEFAULT_INCLUDES` — or to `EXCLUDED_PREFIXES`, deliberately. Widening the scope
without this would only move the boundary.

Naming documents on the command line still narrows deliberately, and skips the
audit: that is how fixture runs and one-off checks work. Only the no-argument
run — the one `make verify` reads — audits.

The red-first control for all of this is
`tools/probes/check_doc_claims_scope_control.py`: it injects a stale marker into
a file the old set did not reach, runs the checker at `origin/main` and in the
working tree, and requires the first to be green and silent while the second is
red and names the file. It refuses rather than reporting a result if the
`before` arm was already red for some other reason.

## Running it

```bash
make claims                                  # the whole scanned set, one run
python3 tools/check_doc_claims.py            # the same thing
python3 tools/check_doc_claims.py docs/failure-modes.md --quiet
python3 tools/probes/check_doc_claims_scope_control.py   # the scope control
```

`make claims` is also one of the jobs in `make verify`, and a job in
`.github/workflows/rungs.yml`. A whole-tree run is about two minutes: it is
dominated by the backing tests, which are deduplicated across claims (three
claims citing one nodeid run it once), so adding a claim that cites a test
already cited costs nothing.

The checker's own tests are `tools/test_check_doc_claims.py`, which carry the
three cases that matter — a valid backed claim, a claim naming a test that does
not exist, and a claim whose backing test fails — as fixture documents with real
pytest runs behind them, plus the skip/xfail rows of the table above, and the
document-set behaviour: that the include list reaches every directory that
carries a claim today, that a marker outside it is `REFUSED` by name, that the
audit refuses rather than reporting clean when git is unavailable, and — a live
assertion rather than a fixture — that nothing in this tree is currently
outside the scanned set.
