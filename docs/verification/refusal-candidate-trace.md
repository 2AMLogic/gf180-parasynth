# The refusal candidate trace (#1576)

`tools/refusal_sibling_scan.py` lists every call that can reach another
module's `Refused` with no guard that could catch it. The list is an
inventory, not a verdict: it is read off the AST, it does not model a guard in
an intermediate frame, and it cannot say whether any input reaches the call.
This is the trace of the 37 rows the four-root scanner added -- run, not read.
Each row was exercised through its entry point with a working input and a
known refusing input, and what the process did is in
`refusal-candidate-trace.json` beside it.

**Result at `ebba993d`** (`origin/main` when the trace began): of the 37 rows,
**12 let a refusal out as an exit-1 traceback**, 3 are caught by a handler the
scanner cannot see, 4 are shadowed by an earlier site, and 18 have no refusing
input on their entry point's input space. One of the twelve is #1569's known
defect, which PR #1574 repaired on `main` while this trace was being
written; the row is the tree before that repair. Seven repair issues are
filed, one per entry point: #1610, #1611, #1612, #1613, #1614, #1615, #1616.
Nothing was repaired here.

<!-- claim: test=tools/test_refusal_candidate_trace.py::test_the_committed_record_names_what_it_traced -->

## Reconciliation with a fresh scan

The filing snapshot was `python tools/refusal_sibling_scan.py --json` at
`origin/main` `09e1a041` with #1573 applied. Re-run on the traced tree it
reports the same rows: 90 `candidate-escape` rows over roots
`tools, model, pnr, fpga` -- 76 in the original three roots (70 in `tools/`,
6 in `model/`, none in `pnr/`) and 14 in `fpga/`.

<!-- claim: commit=ebba993d -->

| | filing snapshot | fresh scan | account |
|---|---|---|---|
| new in `tools/`, `model/`, `pnr/` | 23 | 23 | every one at the same path, line and callee |
| in `fpga/` | 14 | 14 | every one at the same path, line and callee |
| older candidates in the original roots | 53 | 53 | #1183's census; not traced here |

Nothing moved, nothing was removed and nothing became covered between the two
revisions. Three `fpga/` rows the filing lists as `uncovered=dx` read
`uncovered=drums_fx`, which the filing predicted: `uart_host` imports
`drums_fx as dx` inside the function that raises. One path in the filing text
is abbreviated: `fpga/r2_release.py` is `fpga/release/r2_release.py`. The two
calls at `fpga/verify_live_midi.py:107` are two rows here, `:107` and
`:107#2`, one per callee.

The record carries the scanner's revision, roots, classification counts and
all 90 candidate rows under `scanner`, so a reader can compare the traced rows
with the census they came from without re-deriving either. Each traced row
names its call by path and callee, and every call record names the enclosing
function it was made in (`calls[].caller.fn`).

**The tree, and what differs from `ebba993d`.** Every row in the committed
record was taken on a clean tree; `sessions` names the commit of each group.
Between `ebba993d` and those commits this PR's own files under `tools/` and
`docs/verification/` are the whole difference: no production file changed, so
a row's behaviour is `ebba993d`'s. The `toms` group is at a later commit than
the rest because its first run refused this PR's then-uncommitted edit to
`tools/` -- three probes assert a clean tree before they measure, which is
their precondition working -- and it was re-run after the commit.

## The twelve escapes

In each, the callee raised its own module's `Refused`, the frame the call was
made in left without entering a clause, and the process exited 1 with a
traceback -- a precondition reported as a FAIL.

| row(s) | entry point and input | raised | repair |
|---|---|---|---|
| `tools/probes/audio_distance_floor.py:687` | `--ref` a WAV cut into the strike, or a float WAV holding a NaN | `preparation_contract.Refused` | #1610 |
| `tools/probes/audio_distance_floor.py:705` | `--ref bd8/BD5050.WAV` (T20 refused by design) and a malformed `bd8/*.WAV` | same | #1610 |
| `tools/probes/audio_distance_floor.py:611`, `:612` | the full run with a malformed `bd8/BD5050.WAV` (`:611`) or `sd8/SD5050.WAV` (`:612`) | same | #1610 |
| `tools/probes/hihat/hh_probe5.py:531` | `--quick` with `cy8/CY5025.WAV` cut into the strike | same | #1611 |
| `tools/f1_rtl_filter_path.py:449` | any checkout that has not restored the frozen reference audio | `refprofile.Refused` | #1612 |
| `tools/measure_pulse2x_headroom.py:264` | `phrases --gains 0.0`, or a frozen M5A reference that no longer hashes | `mono_m5a_score.Refused` | #1613 |
| `fpga/midi_session.py:355` | `--image r1` with `r1-kit.json`'s sound table drifted; also through `fpga/verify_live_midi.py` | `drums_fx.KitRefused` | #1614 |
| `fpga/publish_binding_cases.py:116`, `:123` | an XDC with an unstated query (`:116`) or an unmodelled timing exception (`:123`) | `xdc_bindings.Refused` | #1615 |
| `fpga/release/r1_candidate.py:279` | PR #1574's control shape: `uart_host.main` returns 1 | `release_manifest.Refused` | #1616 |
| `fpga/release/r2_release.py:372` | the same control shape | same | #1569, PR #1574 (merged) |

What the trace adds to "it escapes" is *which clause was tried*:

- `audio_distance_floor.py:687`: none. `main`'s `except Refused` guards
  `load_ref` only; the call to `rc.prepare` is the next statement, outside the
  `try`. `hh_probe5.py:531` and `measure_pulse2x_headroom.py:264` have no
  clause anywhere on the path.
- `f1_rtl_filter_path.py:449`: `main:418 except Refused`, tried and not
  matched. The refusal is raised one frame deeper than the callee the scanner
  credits (`refprofile.load_clip`, through `run_case.load_filter_reference`):
  the scanner's blind spot, seen from the far side -- the scanner is right
  that the call is unguarded, and the refusing class is not even the one it
  names.
- `r1_candidate.py:279`: `commands:281 except dx.KitRefused` and
  `check:425 except Refused`, both tried and not matched -- the same mechanics
  as #1569, in the R1 checker, which PR #1574 does not touch.
- `r2_release.py:372`: `stale_control:395 except Refused`, tried and not
  matched, after the `with` at `:371` unwound. This is the known defect, and
  it is the calibration (below).

**Two inputs need saying plainly.**

`f1_rtl_filter_path.py:449` needs no constructed input: a fresh clone of the
traced revision is the known refusing input, because the frozen reference
audio is not committed. It is the only escape in this set that a new checkout
meets by itself.

`r1_candidate.py:279` was reached with a stub, not a file. The file-level way
to make the host CLI refuse a supported command is a drifted kit, and on that
input `kit_identity()` refuses first inside the same `build()`: the trace
shows `r1_candidate.py check` exiting 2 cleanly and never reaching
`commands()`. The row is an escape only for every OTHER reason a CLI command
exits non-zero, which is what the stub stands for. #1616 says so.

## After the trace: what `main` moved

The record is the tree it names. Synced with `origin/main` before this PR
opened (`d51d9d42`, 0 behind), a fresh scan reports 85 candidates against the
record's 90, and three of the moves are rows this document discusses:

- PR #1574 added an `import` to `fpga/release/r2_release.py`, so its rows
  moved by one line: `:110` is `:111` and `:302` is `:303`, both still
  candidates; `:372` is `:373` and is now `covered(base)` -- the repair. The
  committed calibration control is a differential and passes on the repaired
  tree.
- #1699 grew `tools/probes/audio_distance_floor.py` by 57 lines. Its nine
  rows above `main` moved by 11: `:441` is `:452`, `:446` `:457`, `:467`
  `:478`, `:489` `:500`, `:490` `:501`, `:540` `:551`, `:611` `:622`, `:612`
  `:623`, `:640` `:651`. Its two inside `main` moved by 45: `:687` is `:732`
  and `:705` is `:750`. Each is still the call or handler it was; `:687`
  reads `return exit_contract.REFUSED` -- the same status, by name.
- `tools/mono_m5a_score.py` changed under #1570 and #1586 (a shared harmonic
  summary; hashes bound to the bytes measured). The two `:264` inputs were
  re-run on the merged tree and escape exactly as recorded.
- Four OLDER candidates left the census, none of them traced here:
  `measure_m5a_pulse_duty.py:39` and `:112`, `measure_m5a_signal_path.py:56`
  and `:104`.

## Covered, though the census lists them

Three rows are `candidate-escape` in the census and handled in fact.

| row | raised | caught by |
|---|---|---|
| `fpga/pads_rom.py:110` | `drums_fx.KitRefused` | `main:403 except (Refused, dx.KitRefused)` -- exit 2 |
| `fpga/release/r2_release.py:110` | `r1_release.Refused` | `check:315 except Refused`, an exact alias -- exit 2 |
| `fpga/release/r2_release.py:302` | `r1_release.Refused` | same -- exit 2 |

The scanner cannot credit either shape: `dx.KitRefused` is not a family name
(it is a plain `RuntimeError`), and `check()` is an intermediate frame. Both
are the pessimistic direction the docstring already states, and they cost a
trace apiece.

## Shadowed by an earlier site

Four rows have a refusing precondition, and every input that reaches it is
refused one site earlier in the same run:

- `audio_distance_floor.py:640` prepares `bd8/BD5050.WAV`; `e7_ceiling` prepares
  the same file three experiments before `e9_noise_floor` does. The malformed
  file escapes at `:611`; `:640` is never reached with it. One boundary repair
  covers both.
- `verify_live_midi.py:107` (both calls) reads R1's kit and sound table after
  the session under test has read them: `run_session` builds `MidiSession`
  before `check()` builds the oracle, so a drifted kit refuses at
  `midi_session.py:352` and a drifted sound table at `midi_session.py:355`
  (which is in the twelve), each as an exit-1 traceback from
  `verify_live_midi.py`. The oracle's own read cannot refuse once it is
  reached.
- `r1_candidate.py:138` is reached from every entry point that calls
  `frozen_kit()` only after an earlier `uart_host.image_kit` read has accepted
  the kit (`kit_identity()` at `:257` from `r1_candidate.py check`,
  `MidiSession.__init__` at `:352` from `verify_live_midi.py`). A boundary
  script in a drifted clone calls `frozen_kit()` directly: `uh.r1_kit()`
  raises `KitRefused` there and `frozen_kit()`'s own `except dx.KitRefused`
  takes it and re-raises `r1_candidate.Refused`. The row cannot let a refusal
  out.

## No refusing input on the entry point's input space

For eighteen rows the callee's refusing arm needs an argument the caller never
passes, or a state the caller's own inputs exclude. For each, the working run
shows the call at the site executed and returned, and a boundary script calls
the callee with the arguments that DO reach its arm -- which shows the arm is
live and what it raises, and nothing about the caller; that is what the
entry-point run is for.

| rows | what excludes the refusal | the boundary run showed |
|---|---|---|
| `audio_distance_floor.py:441, :446, :467, :489, :490, :540`; `hh_probe5.py:532` | the argument is the integer model's render of one strike: digital silence until the hit | onset index 484 (`render_drum_solo("LT")`), 480 (hh_probe5's CY render); `prepare(render)` returns; `prepare(render[onset:])` refuses "begins at or above 2 % of its own peak"; with ONE sample of lead left it still returns |
| `tom_conga_gate.py:446`, `tom_distortion.py:1579`, `tom_pitch_drop_fischer.py:771` | the sound is a member of a literal tuple; no `inject`, `hit_frame`, `frames` | `render_drum_solo("LT")` returns; the cowbell control on LT, a strike at frame 1e9, `frames=100` and `"NO-SUCH-SOUND"` each raise `preparation_contract.Refused` |
| `measure_pulse2x_headroom.py:191` | no `inject`; engine `"pulse2x"` is a literal | `measure_point("pulse2x", 60, held_patch)` returns; `inject="no-such-injection"` raises |
| `performance_suite.py:629` | `engine` defaults to `"r1"`; the CLI refuses `--engine` for every backend but synthtop | `--backend model --engine pulse2x` is REFUSED by the tool, exit 2; `make_voice("no-such-engine")` raises |
| `verify_m5a_filter2x_i2s.py:50`; `selected_preset.py:26` | `"selected"` is a literal and a key of `ENGINE_PROFILES` | `engine_configuration("selected")` returns; `("no-such-engine")` raises |
| `selected_preset.py:28` | the wave is `"pulse"` or `"saw"` by an argparse choice; the pulse shape is the default | both return; `"triangle"` and `"pulse999"` raise |
| `bd_pedestal.py:483` | the calibration render is a BD strike at probe amp 0.02 | returns 0.0516; `probe_amp=0.0` raises "calibration render is silent" |
| `glide_boundary.py:242` | no `gate-off` marker and no `hold_frames`; every refusing arm of `plan_show` needs one | a gate-on write with no marker plans (what `wrapper_capture` passes); one marker with `hold_frames=2000` plans; hold 0, two markers, a marker with no gate-on write, a write after the marker each raise `uart_host.Refused` |
| `res_tuning.py:442` | see below | `runs([(1, 5.0), (2, 5.1)], 0.02)` groups; a column touching or crossing zero raises |

`res_tuning.py:442` is not excluded by an invariant, and is recorded as what
it is: **excluded by the committed plan's data at this revision.**
`sensitivity.runs` refuses a spread that touches or crosses zero unless the
column is an exact plateau, and the plan's two sweeps select on
`worst_abs_cents` and `mean_offset_travel_cents`. The sweep, run in a clone
(704 s), put every objective between 1.33 and 113.57 cents, with an exact
plateau at 12.322 cents (laws 3 and 4; entries 5, 9, 17 and 33) -- which
`runs()` accepts as a plateau. A candidate whose objective reached zero would refuse
here, through `main`'s `except Refused` (a sibling of `sensitivity.Refused`),
and the trace would not have predicted it from structure.

Three working runs exit non-zero by design, and the call at the site returned
in each: `audio_distance_floor.py:705` lives in the branch that refuses
`bd8/BD5050.WAV`'s T20 (exit 2); `r2_release.py:372` lives in `stale-control`,
whose verdict is STALE (exit 1); `verify_m5a_filter2x_i2s.py:50` executes
before the RTL child is launched, and the child was cut off by the tool's own
`--timeout 45` (its NO-VERDICT, exit 2) because the full RTL phrase is a
build-box run.

<!-- claim: test=tools/test_refusal_candidate_trace.py::test_three_working_runs_exit_nonzero_by_design_and_reach_their_site -->

## Calibration: the R2 host stale-control, both ways

The test plan names PR #1574's control: a `uart_host.main` that exits non-zero
makes `release_manifest._capture` refuse inside `r2_release.stale_control`,
whose broken handler lets it escape; the repaired handler returns exit 2 with
the capture's own reason. Run through the real CLI entry
(`fpga/release/r2_release.py stale-control host --out …`):

| tree | the tracer's record | exit |
|---|---|---|
| `ebba993d`, broken | `release_manifest.Refused` from `:372`; `with` at `:371` unwound; `:395 except Refused` tried, not matched; left `stale_control`, `main`, `<module>` | 1, traceback |
| PR #1574's head `4193ba57`, repaired | same refusal from `:373`; `:396 except refusal.Refused` entered | 2, `stale_control: REFUSED -- … uart_host: unsupported command` |

The second row was taken on a temporary clone of the PR's head and is not in
the committed record; the first is the record's `:372` row. The committed
control is a differential rather than a pin, so it holds on either tree: the
tracer's verdict must equal what a direct, in-process call of
`stale_control("host", …)` observes.

<!-- claim: test=tools/test_refusal_candidate_trace.py::test_r2_stale_control_tracer_agrees_with_a_direct_call -->

## The tracer, and what it got wrong first

`tools/refusal_candidate_trace.py` runs the entry point in a fresh interpreter
with the callee wrapped at import time. When a wrapped call raises a refusal
it tags that exception instance and installs a trace function on the frames
already on the stack -- each suspended on the call, so its next event says
what it did. The record per site: the call's arguments and outcome, the class
raised and whether it is in the `refusal.Refused` family, the stack to the
entry point, every frame the exception left, every clause it tried
(`tested`), every clause it entered (`handlers`), the exit status and the
traceback's last line. The plan is `tools/refusal_candidate_traces.py`.

<!-- claim: test=tools/test_refusal_candidate_trace.py::test_a_clause_that_does_not_match_is_tested_not_entered -->

It was started red against a synthetic tree whose answers are the fixtures'
own exit statuses, read by running each caller directly. The wrong-then-right
record, because the rate is how a reader calibrates any single row:

1. **every refusal read `RETURNED`.** CPython resumes a handling frame on the
   `except ...:` line, before the body's first statement; the clause lookup
   looked only at the body. Caught by the synthetic start-red.
2. **a `with` header read as a handler with no clause.** A frame unwinding
   through a `with` resumes on the header (`PUSH_EXC_INFO`) to call
   `__exit__`. Caught by the R2 calibration.
3. **a clause that was tried read as entered.** The header line fires whether
   or not it matches: `r2_release.py:395` was listed as handling the sibling
   it lets through. Caught by the R2 calibration -- the known defect showed
   the tracer agreeing with it.
4. **the tracer's own frame in the child's traceback.** The child printed a
   traceback the real CLI never would.
5. **a sandbox tree's paths named against the tracer's checkout,** so no run
   in a clone matched its site and the repaired branch read `RETURNED`. Caught
   by running the calibration on PR #1574's head.
6. **any `try` in a decorated function was invisible** to the clause lookup: a
   code object starts on its first decorator line, its AST node on `def`.
   Caught by a `with` fixture whose generator is decorated, as every
   `contextlib.contextmanager` is.
7. **a shared run's name served a second, different run** -- one entry's
   working run was replaced by another's tampered one. A name reused for a
   different run is now `UNRESOLVED`.
8. **the entry's own options were eaten by the tracer's argparse**, and the
   script's directory was missing from `sys.path`; both made entries fail on
   imports they normally resolve.
9. **"no call observed" read `RETURNED`.** It is `NOT-REACHED`.

And the inputs I got wrong, each visible in the record because a script prints
what happened instead of asserting it: "the render minus its lead" did not
refuse (the envelope's first samples are below the onset threshold -- strip to
the measured onset); `plan_show`'s note-off is a `"gate-off"` marker command,
not an `"event"`; `performance_suite`'s shortest phrase has no notes, so
`make_voice` is never called; its `--engine r2` is argparse's refusal, not the
tool's; `f1_rtl_filter_path.py`'s own default `--frames 20000` is refused
(#1617). The predictions in the record (`expect` beside each verdict) were
revised as each was found; the revision is the point of keeping them.

## What this trace cannot say

- The 53 older candidates in the original roots (#1183's census) are not in
  it, nor is anything in `rtl-sketch/` or `audition/`, outside the scanner's
  roots.
- A refusal raised one frame deeper than the wrapped callee is attributed to
  the wrapped call -- the site a repair would guard, and not the class the
  scanner names for it. `f1_rtl_filter_path.py:449` is that case.
- A callee bound by `from x import f` before the tracer's finder runs is not
  intercepted. No traced row is.
- A `return` written inside a `finally` discards an exception without a
  clause, and reads as the frame leaving. A one-line `except X: return` that
  returns `None` is the one shape that cannot be told from a non-match.
- `midi_session.py --port sim` refuses on a host whose one-minute load exceeds
  its CPU count. An earlier session of this trace fell on such a host and its
  session-CLI rows read `UNRESOLVED`; the committed one was taken below it. A
  re-run on a loaded host will say so rather than guess.
- The corpus fixtures need the Fischer corpus (`GF180_TR808_REFS`), the
  simulated session needs pyserial, and one working run needs the restored
  frozen reference audio; a missing prerequisite is an `UNRESOLVED` row, never
  a skip.
- `drums_fx.KitRefused` is not in the `refusal.Refused` family. Where it
  escapes, the record says so by class; whether it should be re-based is
  #1614's question, not this trace's.

## Running it

```
python tools/refusal_candidate_trace.py run --spec tools/refusal_candidate_traces.py \
    --out build/refusal-candidate-trace.json            # every row, every role
python tools/refusal_candidate_trace.py run --spec tools/refusal_candidate_traces.py \
    --only 'fpga/release/r2_release.py:372' --roles working,refusing
python tools/refusal_candidate_trace.py report build/refusal-candidate-trace.json
```

A tampered input is always a fresh clone of the traced revision under the
scratch directory, never the working tree. The whole plan is about an hour of
single-process time; split into six `--only` groups and run five at a time
through `tools/run_all.py`, it takes seventeen minutes.

## Follow-up #1964: the five files with no `except` clause

#1878's closing class search ran the scanner and found 68 `candidate-escape`
rows in 37 files; five of those files have no `except` clause anywhere, which
is #1878's file-level shape with other callees. The issue said none of the
seven rows had been traced. **Four of the five files had been** -- by this
document: `glide_boundary.py:242`, `selected_preset.py:26`, `:28` and
`bd_pedestal.py:483` are four of the eighteen rows in "No refusing input on the
entry point's input space" above, in `refusal-candidate-trace.json` at
`c9584c65`. The search read the census and the issue titles, not this record.
Three rows had never been traced: `tools/clap_burst_timing_qual.py:132` and
`tools/f1_filter_path.py:95`, `:98`.

`tools/refusal_candidate_traces_noexcept.py` re-runs the four #1576 rows
unchanged, by id from that plan, and adds the three. Its record is
`refusal-candidate-trace-noexcept.json`, taken on a clean tree at `8feeff61`
in 2 min 43 s. **Of the seven rows, none is a confirmed escape and none needs
a repair.** Every working run reaches its own site, the call there comes back,
and the process exits 0 with no traceback; no row has a refusing run, because
no input on its entry point's input space reaches the callee's refusing arm.
<!-- claim: test=tools/test_refusal_candidate_traces_noexcept.py::test_the_committed_record_names_what_it_traced -->

| row | entry point and working input | the boundary run drove out | what excludes it |
|---|---|---|---|
| `fpga/release/glide_boundary.py:242` | `--case accept-default --level wrapper` (64 s, the UART bridge PASS) | `uart_host.Refused`: hold 0, two markers, a marker with no gate-on write, a write after the marker | `wrapper_capture` builds no `gate-off` marker command and passes no `hold_frames`; every refusing arm of `plan_show` needs one of the two |
| `fpga/selected_preset.py:26` | `m5a-saw`, `m5a-pulse` | `mono_m5a_score.Refused` for `'no-such-engine'` | the argument is the literal `'selected'`, a key of `ENGINE_PROFILES` |
| `fpga/selected_preset.py:28` | same two runs | same class for `'triangle'`, `'pulse999'` | the wave is `'pulse'` or `'saw'` by an argparse choice; the pulse shape is the module default |
| `model/bd_pedestal.py:483` | `--out <tmp>` (53 s) | `bd_excitation_probe.Refused`: "calibration render is silent" at `probe_amp=0.0` | `main` passes the literals `calibrate_amp(mf.BP, 1.0, 0)`; that render is not silent (0.0516) |
| `tools/clap_burst_timing_qual.py:132` | the default `--n 24` (1296 calls at the site, all returned) | `preparation_contract.Refused`: cut to the onset, and one NaN sample | every signal `detect` hands `prepare` is `synth(...)`'s, which begins with 50 ms of exact digital silence and is finite by construction; `--n` and `--out` do not touch the signal |
| `tools/f1_filter_path.py:95`, `:98` | two caller CLIs: `tools/probes/holdout_f1d_preconditions.py` (builds the path with every default) and `tools/f1_rtl_filter_path.py` (the smoke run, 21 s, RTL vs model BIT-EXACT) | `mono_m5a_score.Refused`, raised AT the site by a path built with `profile='no-such-engine'` / `substitute_profile='no-such-engine'` | `profile` is never a CLI argument: every production construction leaves the default `'selected'` or passes `substitute_profile` as `None` or the literal `'legacy'`, both keys |

The three new invariants are checked against the tree rather than carried, and
each check ships with the input that defeats it. The clap lead: every signal
the CLI hands `prepare`, at every condition, sample rate and carrier, is
finite and begins with exactly 50 ms of zeros, and `prepare` returns for it;
<!-- claim: test=tools/test_refusal_candidate_traces_noexcept.py::test_every_signal_the_cli_hands_prepare_starts_with_exact_silence -->

the same signal cut to its onset, or with one NaN sample, is REFUSED.
<!-- claim: test=tools/test_refusal_candidate_traces_noexcept.py::test_the_two_arms_are_live_on_the_shapes_that_do_reach_them -->

The F1 profile: no production code names a profile `ENGINE_PROFILES` lacks --
the four construction sites and both `__init__` defaults are read off the
tracked tree, and a new caller turns the check red until it has been read.
<!-- claim: test=tools/test_refusal_candidate_traces_noexcept.py::test_no_production_code_names_a_profile_the_engine_lacks -->

A profile read off an argparse value, positional or keyword, through an
aliased import, and a substitute handed to `RtlFilterChainPath` are each
reported, by keyword and line.
<!-- claim: test=tools/test_refusal_candidate_traces_noexcept.py::test_the_ast_check_reports_the_constructions_that_would_defeat_it -->

The four older rows' invariants hold on this tree too (`selected_preset`'s
two rows in one test):
<!-- claim: test=tools/test_refusal_candidate_traces_noexcept.py::test_selected_preset_passes_only_names_the_callee_accepts -->
<!-- claim: test=tools/test_refusal_candidate_traces_noexcept.py::test_the_bd_calibration_render_is_not_silent -->
<!-- claim: test=tools/test_refusal_candidate_traces_noexcept.py::test_wrapper_capture_never_plans_a_gate_off_marker -->

**What the two library rows' boundary run says, and does not.** For
`f1_filter_path.py:95`, `:98` the boundary script builds the path with the
refusing name, so the tracer records the refusal raised AT the site and reads
the row `HANDLED` -- by the script's own `except Exception`, never by a
production frame; the record names the handler's file and the test pins it.
It shows the arm is live at the exact call a repair would guard. It is not a
refusing input for any CLI, and no CLI's boundary was observed catching
anything. The same script shows `substitute_profile='legacy'` -- the one
substitute `run_case.py:2665` ever passes -- is accepted by `engine_configuration`
and then refused by `f1_filter_path`'s OWN class at `:102` ("ladder is
LadderFx, not RateConvertedLadder"), which `run_case.py:2670` catches: a
designed control, not this row.

**Not run, stated.** `tools/run_case.py`'s F1 caller (`:2664`, the whole F1
train) and `tools/score_f1_rtl.py` were not traced. The first passes
`substitute_profile` only and the second builds the same path through
`RtlFilterChainPath.__init__` with the same defaults as the smoke run; the AST
check reads both off the tree. A change to WHAT `detect` hands `prepare` is
seen by re-running the trace, not by the lead test.

**#1674 and #1833 are other rows.** #1674 is `selected_preset.definition()`'s
own `ValueError` at `:25` (the reference-audio hash mismatch), the site's own
class and not a cross-module refusal; #1833 is PR #1756's blanket
`except ValueError`, which is not on `main`. Neither is `:26` or `:28`, so
there was nothing to drop and nothing to duplicate. PR #1678 adds
`except Refused` to that file; it will not cover `mono_m5a_score.Refused`, and
these two rows stay as recorded.

**The census is unchanged, because nothing was repaired.** The scanner reports
71 `candidate-escape` rows in 38 files at `e2d1bae7` (`origin/main` when this
began, 0 behind) and the same 71 in 38 at `8feeff61`: this PR adds no
production code, and the plan file it adds is scanned and holds no site. The
issue's 68 in 37 was `8f3d8ed2` plus #1878's fix, which is PR #1968, still
open; the three rows and one file between the two counts are
`tools/cymbal_m_origin.py`'s (`:178`, `:296`, `:336`). The seven rows stay
`candidate-escape` in the census by design: the scanner reads the AST and does
not model an argument's value. They now carry a traced verdict.

**The other 32 files** with `except` clauses are out of this issue: a handler
exists in each, and whether it covers every origin is the per-origin census
#1183 started, not a file-level question. No trace here bears on them.

**Two fixture mistakes before the record was clean**, each read off the record
rather than spotted in the plan, which is the wrong-then-right rate for this
document's newest rows: the first run gave two different runs one shared name
(`f1-callers`), and the tracer wrote both `UNRESOLVED` -- its own guard against
a name that stands for two runs; the second pointed the smoke run's `--json`
at a directory that did not exist, and the run exited 1 on a
`FileNotFoundError` traceback at `f1_rtl_filter_path.py:439` after the call at
`:95` had returned and the comparison had printed BIT-EXACT. The row read
`RETURNED` both times, correctly; the run was not clean, and a record of
non-escapes that carries a traceback of its own making is not one to commit.

```
python tools/refusal_candidate_trace.py run --spec tools/refusal_candidate_traces_noexcept.py \
    --out build/refusal-candidate-trace-noexcept.json
python tools/refusal_candidate_trace.py report build/refusal-candidate-trace-noexcept.json
```

The plan needs the restored frozen reference audio for its two F1 caller CLIs
(`python tools/refprofile_restore.py`) and iverilog on `PATH` for the glide and
F1 RTL runs; each is refused as an `unresolved` row rather than skipped.
