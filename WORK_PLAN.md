# Work plan

Forge lifecycle snapshot maintained by Guide. The milestone is #282: a great-sounding mono Moog-like synth and complete 808 kit. Lifecycle labels describe workflow, not acoustic acceptance. Preserve the recorded pulse2x operator override and qualify improvements on untouched conditions before claiming delivery.

<!-- guide:plan-body:start -->
## Sound delivery

The model increments in PR #549 (cutoff correction) and PR #554 (cymbal coupling) have merged. RTL correction PR #555 remains in changes-requested; coupling tasks #552 and #553 remain open. No new measurement, RTL equivalence verdict, image or physical capture was produced by this Guide cycle.

No issue currently carries `loom:building`. Sound qualification needs a continuous owner; the unclaimed sound queue and capability holds need operator attention. Cymbal task #369's sweep relapsed into an insta-crash quarantine on 2026-10-08; the daemon recorded a 7200-second pause and an environment/configuration diagnosis to investigate. Its older dependency #432 is closed, but that does not release the quarantine. Building labels alone would not prove liveness; orphan recovery found no orphaned tasks.

## Operator Attention: Merge-Risk-Hold Pileup

- **PR #504**: Attack-context re-bind path; Judge-approved, operator hold.
- **PR #561**: Between-recording calibration and ranking; Judge-approved, operator hold. Calibration remains REFUSED in its proposal.

## Operator Priority

None currently labelled `loom:operator-priority`.

## Ready

Approved, unblocked issues excluding operator-only and building claims, and excluding #502 because its approved closing PR is awaiting merge. An existing implementation PR must be handled before commissioning duplicate work.

- **#107**: Differential partial decay; implementation PR #377 is blocked.
- **#247**: Above-Nyquist glide mismatch; model increment merged, RTL capability still needed.
- **#257**: Resonance-keyed cutoff correction; RTL PR #555 needs changes and hardware capability.
- **#306**: Held-note timing; PR #563 needs changes and live hardware capability.
- **#369**: Across-knob cymbal qualification; capability required.
- **#379**: All-sixteen-sound gate and ranking; partial PR #561 awaits an operator decision.
- **#426**: Deadline evidence binding; carries an operator label.
- **#521**: Detector coverage; PR #534 needs changes and reference capability.
- **#522**: Corpus-path resolver; PR #535 is blocked after the Doctor-cycle cap.

## In Progress

None currently labelled `loom:building`.

## PRs Awaiting Review

None currently labelled `loom:review-requested`.

## Approved (Awaiting Merge)

- **PR #504**: Attack-context re-bind path (operator hold).
- **PR #561**: Gate calibration and ranking (operator hold).

## Proposed

Curated work awaiting or already carrying separate lifecycle decisions: #33, #107, #124, #138, #152, #158, #162, #163, #205, #208, #220, #247, #257, #282, #283, #285, #288, #306, #310, #334, #335, #336, #337, #338, #353, #369, #379, #426, #502, #510, #521, #522, #557. This list does not confer approval or release a hold.

## Proposed (Architect / Hermit)

- **#564**: Collect currently undiscovered measurement tests.
- **#565**: Refuse incomplete nextpnr logs as routing evidence.
- **#568**: Refuse blank synthesis cell counts after yosys failure.
- **#569**: Consolidate duplicated file-hash helpers.

## Auditor findings

- **#567**: Python runtime capability request; validation remains unavailable on this host.
- **#570–#572**: Stash, checkpoint-write and Python-deletion guard findings.
- **#574**: Retain the guard that catches a literal file path used as a comment body.

These findings do not establish instrument defects or authorize guard changes.

## Epics

- **#282**: Mono Moog-like synth and complete 808 kit. Still open; model improvements and physical delivery are separate obligations.
- **#158**: Validate the measurement judge. Still open; its ground-truth and perturbation increments have landed, coverage PR #534 remains open.

No `loom:epic-phase` issues were returned; a phase-completion percentage cannot be derived from that label set.

## Backlog Balance

| Category | Count |
|---|---:|
| Open issues | 90 |
| Goal-advancing tier | 26 |
| Goal-supporting tier | 9 |
| Maintenance tier | 5 |
| Approved issues missing a tier | 0 |
| Ready by lifecycle labels, excluding approved closing PRs | 9 |
| Building claims | 0 |
| PRs awaiting Judge | 0 |
| Approved PRs awaiting merge | 2 |

Tier counts cover all open issues, including held and unapproved work; they are not counts of executable work.

## Dependency holds

- **#510**: #551 closed; #552 and #553 remain open. Keep blocked.
- **#369**: #432 closed; current daemon quarantine and reference/build capability requirements remain. Keep blocked.
- **#310**: Upstream rjwalters/loom#9160 remains open. Its park record does not yet name the cross-repo blocker in a parseable field.
- **#122**: Reference rig #124 remains open.
- **#33**: Deferred behind sound milestone #282.
- **#23**: Silicon work #33 remains open.
- **#220**: Corpus/listening capability needed for the remaining residual bounds.
- **#213**: Operator hold digest; no dependency release inferred.
- **PR #535**: Doctor-cycle cap requires human attention.
- **PR #377**: Build-box/corpus/Vivado qualification remains pending.

## Overlaps for Curator

The newer sound tasks #558 and #556 share voice families with #334 and #336 respectively. Keep the issues open for scope consolidation; shared voices alone do not establish duplication or completion.
<!-- guide:plan-body:end -->
