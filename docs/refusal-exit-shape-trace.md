# Refusal exit shapes: the trace of every site #1807's scans found

A tool that prints `REFUSED` because a **precondition** failed and then
exits **1** is reported by `tools/run_all.py` as a RESULT -- evidence against
the claim it was pointed at -- because `tools/exit_contract.py` reserves 1
for a measurement that DISAGREED. This is the trace behind #1807: for every
site its scans found, what reaches it and the exit status it had on
`origin/main @ 03148399` **before** any change, measured 2026-11-28.

A site is this defect only if its `REFUSED` is reached by a precondition
failing. If a control **measured** a disagreement, 1 is right and the label
is the bug. "Measured" below means a control in this tree drove the path and
asserted the status; "read" means the source was read and no control drove
it. The instrument is `tools/refusal_exit_shape_scan.py`; its controls are
`tools/test_refusal_exit_shape_scan.py`.

## The count, reconciled

| scan | sites | what it saw |
|---|---|---|
| the issue's 6-line text window | 22 | 17 true print-then-exit-1 sites, 5 false positives |
| the Curator's re-run of that window | 23 | the same plus `tools/cymbal_tone_knob.py:1068`, which the issue's text called "fixed by #1786" while #1786 was still open |
| the committed scanner, before the repair | **48** | the 17 + 5 true sites, 0 of the 6 false positives, **25** `raise SystemExit("REFUSED ...")` sites and **6** more print sites the window could not reach |
| the committed scanner, after the repair | 28 | every one owned: 5 by #1786 / #1806 / #1769 and the census pin, 23 by #1841 |

The Curator's enhancement lists the in-scope sites as "16 sites in 8 files";
the enumerated list it gives is **18** in 8 files (9 + 2 + 4 + 3). All 18 are
traced below. The "23 vs 22" is `cymbal_tone_knob.py:1068`, as the Curator
found.

## The issue's 23 window sites

Exit status is the one the `REFUSED` path had on `origin/main @ 03148399`.

### Repaired here: 20 sites, every one a precondition

| site | what reaches it | before | now | measured by |
|---|---|---|---|---|
| `tools/dsp_dpreg_extract.py:423` | the box check failed | 1 | REFUSED | `test_dsp_dpreg_extract.py` (every step, both transports; and the real CLI in a subprocess) |
| `:434` | `drc.rpt` could not be fetched | 1 | REFUSED | same |
| `:451` | the Tcl could not be staged | 1 | REFUSED | same |
| `:466` | the Vivado batch failed | 1 | REFUSED | same |
| `:472` | the checkpoint digest changed or could not be re-checked | 1 | REFUSED | same |
| `:481` | the box manifest failed | 1 | REFUSED | same |
| `:487` | a file could not be pulled | 1 | REFUSED | same |
| `:502` | the local copies are not the box's bytes | 1 | REFUSED | same |
| `:509` | the fetched `drc.rpt` is not the box's | 1 | REFUSED | same |
| `tools/dsp_dpreg_extract.py:283, :300, :346, :349, :351, :354, :358, :367` | the analyser refused the DRC; an unknown instance shape; five binding checks; a different local `drc.rpt` | 1 (a SystemExit carrying a string) | REFUSED | `test_dsp_dpreg_extract.py`, `test_dsp_dpreg_analyse.py` (13 controls that had matched the message alone) |
| `tools/dsp_dpreg_sim.py:360` | the simulator run failed | 1 | REFUSED | `test_dsp_dpreg_sim_exit.py` |
| `tools/dsp_dpreg_sim.py:369` | a hash mismatched after the run | 1 | REFUSED | same |
| `model/alias_probe.py:806` | the estimator failed its known-answer validation | 1 | REFUSED | `model/test_alias_probe_refusal_exit.py` |

Every one of these paths exits `exit_contract.REFUSED` on the current tree.
<!-- claim: test=tools/test_dsp_dpreg_extract.py::test_a_failure_at_every_step_refuses_with_the_refused_status_identically -->
<!-- claim: test=tools/test_dsp_dpreg_extract.py::test_a_precondition_refusal_reaches_a_real_process_as_exit_refused -->
<!-- claim: test=tools/test_dsp_dpreg_sim_exit.py::test_a_hash_mismatch_is_refused -->
<!-- claim: test=model/test_alias_probe_refusal_exit.py::test_an_estimator_that_fails_its_known_answer_check_refuses -->

Why the extractor's eight helper sites are here at all: they are the same
defect in a second shape. `raise SystemExit("REFUSED: ...")` prints the string
and exits 1 -- Python's rule, run as a real subprocess by
`TestInterpreterKnownAnswers` -- and the issue's window looked only for a
`print`. The extractor now routes all seventeen through one `refuse()` that
prints the evidence on stderr and exits the contract's status; the nine
`main()` refusals move from stdout to stderr with it, and nothing in the tree
parsed that stream for `REFUSED`.

Why the simulator's repair is four sites and not two. Its two listed sites
were preconditions, but the file had the contract's two statuses swapped, and
repairing only the two would have left exit 2 meaning both "refused" and
"failed". On `origin/main`: the incomplete-trace path printed `NO VERDICT`
and exited **1**; the verdict line returned **2** for both halves of
`red1 and red2 and clean0`, so a clean run that FAILED under controls that
had fired -- a refutation of the DPREG-4 disposition -- was rendered
NO-VERDICT. The module's own docstring already stated the contract ("each
must turn the harness red ... or the run REFUSES"; mode 0 "must report 0 X
and 0 wrong-value cycles"). Now: an unfired control exits REFUSED (no
evidence), a failed clean run under fired controls exits FAIL (a result), and
both no-verdict paths exit REFUSED.
<!-- claim: test=tools/test_dsp_dpreg_sim_exit.py::test_a_clean_run_that_fails_under_fired_controls_is_a_result -->
<!-- claim: test=tools/test_dsp_dpreg_sim_exit.py::test_an_incomplete_trace_is_refused -->

`tools/test_dsp_dpreg_refusal_cases.py` had pinned that incomplete-trace
refusal at exit **1** -- the gate held the refusal refused, on the wrong
status. It reads both statuses from the contract now.

### Not this defect: 6 false positives of the window, each driven

| site | what the window saw | what is there | `REFUSED` path's exit | measured by |
|---|---|---|---|---|
| `tools/check_doc_claims.py:962` | `REFUSED` then `return 1` 3 lines on | the scope audit refused; the `return 1` is `n[STALE]`'s | 2 (1 when a claim is stale too -- a result) | `TestTheRealSitesBehindThoseShapes` |
| `tools/check_m1a_selection.py:197` | `REFUSED` then `return 1` 5 lines on | a record that will not load; the `return 1` is `problems`' | 2 | same |
| `tools/check_workflows.py:754` | `REFUSED` then `return 1` 5 lines on | the path scan refused; the `return 1` is `bad`'s | 2 | same |
| `tools/probes/preparation_contract_current_state.py:69` | `REFUSED` then `return 1` 6 lines on | the corpus is absent; the `return 1` is a contract-refused pair's | 2 | same |
| `tools/probes/check_doc_claims_789_marker_controls.py:135` | `return True`, which `== 1` | a predicate; its call site does `return 2` | 2 | same |
| `tools/probes/check_doc_claims_789_marker_controls.py:240` | `return True`, which `== 1` | a predicate; both call sites do `return 2` | 2 | same |

### Owned elsewhere: the 5 the issue excluded

| site | owner | before | read as |
|---|---|---|---|
| `tools/cymbal_m_origin.py:398` | #1806 (#1789 names the line) | 1 | precondition |
| `tools/cymbal_tone_knob.py:1068` | #1786 | 1 | precondition, behind one bool that also carries a measured disagreement |
| `tools/cymbal_vca_clip.py:1116` | #1769 (PR #1792) | 1 | precondition |
| `tools/cymbal_vca_drive.py:1525, :1531` | the pinned exception in `tools/refusal_exit_census.py` | 1 | a documented contract (#804 scoped it out) |

## Beyond the issue: 23 sites no scan had listed, filed as #1841

The committed scanner reads the exit from control flow, which found two
shapes the window could not: `raise SystemExit("REFUSED: ...")` (17 sites
outside the extractor) and a flag set beside the print and read eight lines
later (6 sites in `tools/probes/dc_t20_gate_qualification.py`). They are
pinned in the scanner's `EXPECTED` under #1841. **None was driven**; the
reading of each source, with the sites that look like measurements
(`tools/check_phrase_schedule.py:32`, likely `:60`), is in that issue. The
scan that finds them runs as a gate.
<!-- claim: grep="refusal_exit_shape_scan.py --check" in=Makefile -->
<!-- claim: test=tools/test_refusal_exit_shape_scan.py::TestTheRealTree::test_every_hit_in_the_tree_is_a_pinned_exception -->

## What the scanner cannot read: 27 UNREAD sites

A `REFUSED` print whose exit status the scan cannot resolve -- reported, never
counted as a hit and never as zero. Four were read for this trace and are
listed in #1841 as candidates (`tools/measure_promoted_bands.py:809` and
`tools/probes/permitted_differences.py:1540` count a refusal into a shared
`bad` counter; `tools/measure_conga_body_spread.py:672` returns a recorded
outcome code the contract documents as 3; `rtl-sketch/synth_count.py:82`
returns `e.code`). `tools/probes/f1_selected_path.py:322` is mixed by
design: `REFUSED` for a wrong ladder path, and 1 only when the probe's output
ALSO differs, with a `FAIL` line printed beside it. The rest are predicates,
report lines inside a larger report, or a conditional exit on a variable the
refusal's path did not set. The scanner lists them by reason on every run.

## Wrong before right

Recorded because each wrong result looked like an answer.

1. **The scanner's first draft found nothing.** It read the enclosing-block
   chain outermost-first, so for `if bad: print("REFUSED ..."); return 1` it
   read the function's trailing `return 0`. Its self-control -- a planted
   defect and a planted correction it must tell apart before answering --
   REFUSED on the first run, before any count was printed. The issue records
   the same failure in its own first scan (`ast.walk` is breadth-first).
2. **Every site was reported twice**, once as `<module>`: the module-level
   walk descended into `def` bodies that `_functions` visits again.
3. **A true positive sat in UNREAD.** `tools/dsp_dpreg_sim.py:369` sets
   `ok = False` inside a loop and exits at `if not ok: return 1`; a
   conditional exit was "unreadable" until the scan carried what the path
   assigned. Finding that also found `dc_t20_gate_qualification.py`.
4. **25 correct `return REFUSED` sites read as open questions.** The first
   sibling-module resolver read `NAME = <int>` only; this tree declares
   statuses by tuple (`OK, FAIL, REFUSED = 0, 1, 2`) and through other
   modules (`= rp.OK, rp.FAIL, rp.REFUSED`). UNREAD fell from 61 to 27.
5. **Two excluded sites were not found**, and one extractor site: the labels
   lead with `"\n"`, or are built with `"REFUSED: " + name`.
6. **A print falling through to a string SystemExit was UNREAD.** The
   interpreter's answer is known (exit 1); the scan now reads it, and a
   control pins both shapes on that path.
7. **My own display dropped a hit.** An `awk 'NR>2'` over the scan's table
   ate its first data row and `measure_harness.py:391` seemed to vanish
   between runs. The scanner was re-run three times: deterministic.
8. **A count was repeated, not counted.** #1841 was filed with "23 sites in
   11 files"; the scanner's own list is 23 sites in **10** files. Caught
   while checking the PR body's numbers against the scanner, corrected in
   a comment on #1841, and the per-file count is in that comment.

## The same-class search

Searched for the class, not the string, before calling this closed:

- **`sys.exit(1)` / `exit(1)` after a `REFUSED` print**: part of the
  scanner's `print-exit` shape (an exit statement is a `return`, a `raise
  SystemExit`, or an `exit()` call). No instance in the tree.
- **A `REFUSED` label with other refusal wording** (`REFUSE`, `cannot`,
  `could not`): the scanner reads a label that LEADS WITH `REFUSED`; a
  `print("cannot audit ...")` with no `REFUSED` is a different question --
  whether the exit status matches the outcome -- and is not this shape. Not
  searched; recorded as not searched.
- **The census's handlers**: `tools/refusal_exit_census.py --check` is green
  on this tree, so every refusal site inside an `except` handler or declared
  as a module `REFUSED =` constant sits inside `NO_EVIDENCE` except its one
  pinned exception.
- **A `NO VERDICT` label on an exit-1 path**: one instance, the simulator's
  incomplete trace, repaired here with its file. The other `NO VERDICT`
  printer, `tools/dsp_dpreg_analyse.py`, exits 3 (`REFUSED_ALTERNATE`, in
  `NO_EVIDENCE`).
- **The inverse defect, a failed measurement on a refusal status**: one
  instance, the simulator's verdict line, repaired here. Searched by reading
  every `return 2` / `return 3` in the three repaired files; the scanner
  does not detect this shape and that is a blind spot, not a zero.
- **Other refusal shapes that print no `REFUSED` label**: #1307 and #1780
  (uncaught cross-module `Refused` escapes) are open and are a different
  family; not touched.
