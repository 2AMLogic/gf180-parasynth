# Guide triage scans

Read-only scans the Guide role runs each cycle. They exist because the
equivalent logic in `guide.md` is specified as bash, and this repository's
CLAUDE.md requires anything with branching or arithmetic to be Python: the
bash original silently reported a status it had not measured.

Each script prints its basis, not just its verdict, so a reader can refute it.

| script | answers |
|---|---|
| `unblock_scan.py` | which `loom:blocked` issues have *every* declared dependency resolved, after the permanent-block, superseding-PR and approval-archaeology gates |
| `orphan_closures.py` | which merged PRs completed an issue but left it open, excluding `Part of`/`Contributes to` increments and epic/family issues |
| `work_log_delta.py` | the pending WORK_LOG.md delta, with the self-loop filter, complete-fetch (`total_count` bisection) and the #6133 debounce |
| `render_plan.py` | WORK_PLAN.md's generated region, sorted by number so reorder noise cannot look like a label change |

## Recorded results, 2026-11-14

- `unblock_scan.py`: 54 blocked issues scanned, **0** with all dependencies
  resolved. Every body-declared blocker was still open.
- `check-stale-blocked` flagged 6 as "STALE BLOCK" the same tick; all 6 were
  refuted by hand (4 had other refs still open, 1 carried a trusted
  `loom:permanent-block` marker, 1 was re-blocked by an operator ruling dated
  after its dependency closed). Filed upstream as rjwalters/loom#13889.
- `orphan_closures.py`: 40 merged PRs examined, **0** orphaned closures, 14
  closing refs resolved correctly, 1 intentional `Part of` increment.

**A wrong-then-right record, kept deliberately.** `orphan_closures.py`'s
closing-keyword regex first used numbered capture groups and crashed on
`int(None)`: the nested alternations in
`(clos(e|es|ed)|fix(es|ed)?|resolv(e|es|ed))` put the issue number in group 5,
not group 4. It is now a named group with a self-check over known shapes
(`Closes #123`, `Fixes #7 and resolves #9`, `Part of #5`, bare `see #99`) that
runs before the real scan. Had the group index happened to land on a digit-only
group, it would have silently scanned the wrong number instead of crashing —
which is the failure this repository's docs call an unknown rendered in the
place where a result belongs.

## Not wired into `make verify`

These read the forge, so they are neither hermetic nor fast, and a CI gate
built on them would be red whenever GitHub is slow. They are run by hand.
`unblock_scan.py` fails safe in one direction only: an unreadable comment
listing counts as a permanent block, so it under-reports candidates rather
than proposing an unpark it could not verify.
