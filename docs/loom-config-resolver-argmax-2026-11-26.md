# 2026-11-26: `config-resolver.sh` dies at `MAX_ARG_STRLEN` once `defaults.json` passes 128 KiB

**Status: not filed upstream — every read of `rjwalters/loom` from the dispatch
host is shed (`loom-shed`, the same operator step recorded in the #1110 row of
`tool-findings.md`), so the duplicate search cannot run and nothing is filed
blind. The ready-to-file text is below.**

## Exact environment

- Loom install: `0.19.1306`, commit `e7af83ea29897351d216cdfd4036ecf628cb96cc`
  (`.loom/install-metadata.json`)
- Host: the AWS dispatch worker (loom-worker), Linux, 4 KiB pages
- Failing input: `~/.local/share/loom/config/defaults.json` at
  **208,171 bytes** (mtime 2026-11-25 11:31; the file is host-provisioned and
  is refreshed, so it grew past the limit without anything changing in Loom)

## Exact error

```
/home/ubuntu/GitHub/gf180-parasynth/.loom/scripts/lib/config-resolver.sh: line 135: /usr/bin/jq: Argument list too long
```

Any script that sources `config-resolver.sh` — directly or through
`lib/forge-helpers.sh`, which most of them source — and reaches the
merged-config call dies before doing its work. Observed on **both**
`create-issue.sh` (issue filing) and `create-pr.sh` (PR opening); the latter
died *after* the push had already landed, which is exactly the
pushed-but-PR-less state `create-pr.sh` exists to prevent (#6074). Only the
`loom-daemon` binary is unaffected (it does not run the shell resolver;
`preflight` and `forge check-claim` were verified working on the same host).

## Root cause

`config-resolver.sh` passes the whole defaults tier as ONE argv element:

```bash
jq -c -n \
    --argjson a "$tier_defaults" \
    ...
```

Linux caps a single argument at `MAX_ARG_STRLEN` = 32 × PAGE_SIZE =
**131,072 bytes** (not the total argv budget — this is the per-string limit).
`defaults.json` at 208,171 bytes exceeds it, `execve` answers `E2BIG`, and bash
reports "Argument list too long". The failure is host-state-dependent: the same
Loom version works wherever `defaults.json` is under 128 KiB, which is why it
looks intermittent across the fleet.

## Workaround used

For the issue: `create-issue.sh`'s own first path is a plain `gh issue create`
(the script only adds a REST fallback for GraphQL exhaustion), so the issue was
filed with `gh issue create --title ... --body-file ...` directly. Same
semantics, no resolver involvement.

For the PR: `create-pr.sh`'s documented pre-script fallback is a plain
`gh pr create` (the script post-dates installs that had none). Its four
guardrails were run by hand first — adopt-first (`gh pr list --head <branch>`,
empty), superseded-target check (`gh issue view` — target open, own claim),
1:1 issue-to-PR (`loom-daemon forge check-open-pr <N>`, the daemon binary
unaffected), priority-label copy (the issue carries none) — and the label was
passed at creation, as the script would have.

An env-sanctioned shrink does not exist: `LOOM_CONFIG_DEFAULTS_FILE` redirects
the path but not the size, and a `jq -c`-compacted copy of the same JSON is
still 137,582 bytes, over the 131,072-byte limit.

## Suggested fix, for the upstream filing

Read the tiers as files (or stdin) instead of argv: `jq -s '.[0] * .[1] * ...'
file1 file2 ...` never builds a single argument larger than one filename, and
the merge semantics of `*` are unchanged. Empty/missing tiers need `null`
padding (`jq -s 'reduce .[] as $x ({}; . * $x)'` with `--argjson` replaced by
`--rawfile`, or `jq` given a `-n` filter with `--rawfile a path` which reads
the file itself and never goes through argv).

## Ready-to-file text

> **Title:** `config-resolver.sh`: merged-config `jq --argjson` fails with
> "Argument list too long" once `defaults.json` exceeds `MAX_ARG_STRLEN` (128 KiB)
>
> `defaults/scripts/lib/config-resolver.sh` passes the entire defaults tier as
> a single `--argjson` argv element. Linux caps one argument at
> `MAX_ARG_STRLEN` = 131,072 bytes, so a `defaults.json` larger than that —
> ours is 208,171 bytes on a dispatch worker running 0.19.1306
> (commit `e7af83ea29897351d216cdfd4036ecf628cb96cc`) — makes every script that
> sources the resolver die at the merged-config call:
>
> ```
> .loom/scripts/lib/config-resolver.sh: line 135: /usr/bin/jq: Argument list too long
> ```
>
> Observed on **both** `create-issue.sh` (issue filing) and `create-pr.sh` (PR
> opening — the latter died after the push had landed, the exact
> pushed-but-PR-less state #6074 exists to prevent). The resolver is sourced
> directly by `run-job.sh`, `spawn-claude.sh`, `spawn-codex.sh`,
> `validate-roles.sh`, `archive-transcripts.sh`, `check-ingest-key-file.sh`
> and transitively by everything sourcing `lib/forge-helpers.sh`. The
> `loom-daemon` binary is unaffected. The failure is host-state-dependent —
> the same version passes wherever `defaults.json` is under 128 KiB — which
> makes it look intermittent across a fleet.
>
> Fix: read the tiers as files instead of argv, e.g. `jq` with `--rawfile` or
> `jq -s 'reduce .[] as $x ({}; . * $x)' f1 f2 ...`, which preserves the `*`
> merge semantics with no per-tier size limit. Workaround in the field: call
> the underlying `gh` command directly, which is what `create-issue.sh`'s own
> first path does anyway.
