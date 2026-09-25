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

`issue=`, `why=` and `note=` are annotations for a human reader and check
nothing; they exist so that a marker can say *which* tracked defect or *which*
open issue it refers to without that text being mistaken for a key typo.

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

## Running it

```bash
make claims                                  # every docs/*.md
python3 tools/check_doc_claims.py            # the same thing
python3 tools/check_doc_claims.py docs/failure-modes.md --quiet
```

`make claims` is also one of the jobs in `make verify`.

The checker's own tests are `tools/test_check_doc_claims.py`, which carry the
three cases that matter — a valid backed claim, a claim naming a test that does
not exist, and a claim whose backing test fails — as fixture documents with real
pytest runs behind them, plus the skip/xfail rows of the table above.
