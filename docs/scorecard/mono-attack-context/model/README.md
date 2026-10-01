# Matched attack context: unchanged model versus Mini V3

All 12 model conditions completed in [Linux CI](https://github.com/2AMLogic/gf180-parasynth/actions/runs/35666927724), and were re-rendered byte-for-byte in [Linux CI](https://github.com/2AMLogic/gf180-parasynth/actions/runs/36660843540) when the evidence was re-bound (below). The 36 frozen Mini V3 renders came from #193. MIDI timing, target note, envelope calibration and sound controls are fixed; only preceding-note history varies within each matched-time pair.

| Wave | History before target | Model attack change | Reference attack change |
| --- | --- | ---: | ---: |
| saw | repeat84_gap3p4 vs delayed84_at4p1 | +0.000 ms | -5.188 ms |
| saw | from72_gap3p4 vs delayed84_at4p1 | -0.021 ms | -5.083 ms |
| saw | repeat84_gap5 vs delayed84_at5p7 | -0.208 ms | -4.979 ms |
| pulse | repeat84_gap3p4 vs delayed84_at4p1 | +0.000 ms | -5.083 ms |
| pulse | from72_gap3p4 vs delayed84_at4p1 | +0.000 ms | -5.104 ms |
| pulse | repeat84_gap5 vs delayed84_at5p7 | +0.042 ms | -4.938 ms |

A preceding note shortens the measured reference attack by 4.94–5.19 ms. The unchanged model changes by −0.21 to +0.04 ms. It does not reproduce this history dependence under the tested conditions. This supports investigating envelope restart/state behavior before making a global attack adjustment; it does not establish the plugin's internal cause.

The selected baseline uses saw 2×, causal reconstructed filter 2×, the 20 kHz saw setting and effective 47.9% pulse at base oscillator rate. **Pulse 2× from #192 is not selected.** No DSP setting or acceptance threshold changed. These are model measurements, not RTL or hardware playback.

Reproduce the measurements from the committed audio without rendering:

```sh
python3 tools/verify_attack_context_model.py
python3 -m pytest -q tools/test_verify_attack_context_model.py
```

`report.json` binds all 12 WAV hashes, production source commit and source hashes, reference report, envelope calibration and complete timing data. `ci-import.json` records publication checks. All attack/release crossings and six history contrasts reproduce exactly on macOS; five original local WAVs match Linux byte-for-byte. One held RMS value differs by 5.55e-17 between platforms; only that diagnostic allows 1e-14 relative rounding noise. Sound limits and timing reproduction remain unchanged.

The verifier refuses a one-sample timing mutation, changed contrast, missing row, changed audio digest, and invalid timing. **Wrong-then-right: zero sound measurement corrections; one publication precondition corrected** (exact dictionary equality rejected the one-ulp RMS difference). The prior reference qualification record remains in the parent report.

## Source identity: re-bound to a reachable commit

The WAVs were first rendered at `1ae5071b038d93df507efb50b76cb3c83eb6d228`, with pulse oversampling disabled. A later rebase orphaned that commit, so `git show` could not resolve it from any checkout, and the verifier's historical-hash check failed in CI (#403, root cause #215). The analysis module `model/audio_measure.py` had also changed since then (docstrings only), which the verifier correctly refuses.

The evidence is now bound to `85f46ec7f0ce831d12c5a652643f57692af823f8`, a merge commit on this branch that stays reachable because the branch is updated by merge, not rebase. [Linux CI](https://github.com/2AMLogic/gf180-parasynth/actions/runs/36660843540) re-ran `tools/compare_mono_attack_context.py` at that commit. It reproduced **all 12 WAVs byte-for-byte**, and every timing row and history contrast is identical to the original report. Only `source_commit` and `source_sha256` changed. Merging #192's engine changes did not change the selected baseline's audio: pulse 2× is still not selected. `ci-import.json` keeps the original run, commit and report hash under `rebound_from`.

The verifier still checks every recorded source hash against the bound Git commit and lists differences from today's checkout. It separately requires the audio-analysis modules to keep their recorded hashes: changed analysis refuses. It reproduces the audio measurements without rendering. No verifier check was loosened for the re-bind.

## Second re-bind: `a10a510`, because #134 changed the analysis basis

**The "changed analysis refuses" check above then fired for real.** #134 added
a finiteness assertion to `model/audio_measure.py` -- a NaN defeats every
threshold guard shaped `if bad: refuse`, because IEEE comparisons against NaN
are always False -- and the verifier correctly refused the new hash. It was the
only thing in the repository that noticed, and it noticed a *docstring-and-
assertion* change, which is the behaviour this record wants.

The evidence is now bound to `a10a51056de1bf55b8d37bbfc63eeafab9744cbd`.
`tools/compare_mono_attack_context.py` re-ran at that commit and **re-rendered
all 12 WAVs byte-identically** (`git status` reported only `report.json`
modified). Every attack and release crossing, every other `model_timing` field,
and all six history contrasts are identical. Six of the twelve `held_rms`
values differ in the last one or two ulp, **max 1.11e-16** against the
diagnostic's recorded 1e-14 relative tolerance. So `source_commit`,
`source_sha256` and those six values are the entire delta: the new finiteness
assertion is a no-op on finite audio, measured rather than asserted.

That one-ulp difference is worth a note. The first time it appeared it was
recorded as an ARM/x86 property; this re-run was **Linux x86_64 to Linux
x86_64**, so it is a summation-order or library-version property, not an
architecture one. The tolerance was already right; the explanation was too
narrow.

**This re-bind was produced locally, not in CI**, which the two before it were.
The agent's token could not dispatch `attack_context_model.yml`
(HTTP 403, `Resource not accessible by integration`). A maintainer with
`actions: write` can dispatch that workflow on this branch and replace
`report.json`; the re-render above is the evidence that the content does not
depend on which of the two it is. `produced_by` in `ci-import.json` records the
host, the command and the reason, and `workflow_url` is `null` rather than
carrying a stale link.

## How a Builder re-binds next time (#502)

Any change to `model/audio_measure.py`, `tools/measure_mono_m5a_reference.py` or
`tools/measure_mono_attack_context.py` makes the verifier refuse with
`analysis basis changed`. That gate stays as it is. An agent token has no
`actions: write`, so `gh workflow run attack_context_model.yml` returns HTTP 403.
Nothing here grants that permission; widening the token is an operator decision
and is not needed.

**Preferred path (CI-produced).** The workflow now also runs on `push` to
`feature/issue-*` when a path in its filter changes. That filter includes the
three analysis sources, `tools/compare_mono_attack_context.py`, `model/**` and
the workflow file. No dispatch is needed. Reading artifacts needs only read
access:

```sh
gh run list --workflow attack_context_model.yml --branch <branch> --status success \
  --json databaseId,headSha,url --limit 1
gh run download <run-id> -n attack-context-model -D docs/scorecard/mono-attack-context/model/
```

Use `--status success`: the workflow uploads its artifact with `if: always()`, so
a failed run still produces one, possibly with partly overwritten WAVs. Check
that the run's `headSha` equals the commit you are binding before downloading.
The checker's `source_commit` cross-check catches some mismatches, not all.

Then set `source_commit` in `ci-import.json` to the pushed commit, put the run
URL in `workflow_url`, keep the old binding under `rebound_from`, and commit.
The branch must stay reachable (merge, do not rebase it), as in the first
re-bind. The widened trigger is limited by branch pattern and by path, so
unrelated pushes do not render.

**Fallback (locally produced), used only when no CI run is available.** Run
`python3 tools/compare_mono_attack_context.py` on Linux, require `git status` to
show only `report.json` modified (all 12 WAVs byte-identical), and record in
`ci-import.json`: `workflow_url: null`, `produced_by.{host,command,why_not_ci}`,
`model_wavs_byte_identical_across_rebind` equal to `model_audio_files`, and
`rebound_from`. The byte-identical re-render is the evidence that the content
does not depend on the host. The gate cannot see the earlier render's bytes. It
checks that the record *declares* byte identity, that the declared count equals
the report's rows, and that the WAVs on disk match the report's digests. This
is a fallback, not the norm: a maintainer can later replace the report with a
CI-produced one.

**Gate.** `python3 tools/check_attack_context_rebind.py` prints `OK-CI`,
`OK-LOCAL`, or `REFUSED` (exit 2). It refuses for a missing or unreadable input,
a stale `report_sha256`, a `source_commit` mismatch, a missing `rebound_from`
(either path), or a run URL outside this repository's Actions. On the local
path it also refuses for absent `produced_by`, a byte-identical count short of
`model_audio_files`, a count that differs from the report's rows, or a WAV whose
digest differs from the report. `tools/test_check_attack_context_rebind.py`
carries one injected defect per refusal, each matched to its stated reason. It
asserts only that the current state is not refused, so a CI re-bind (`OK-CI`)
keeps it green.

**Verified on this branch.** The push trigger has fired. Workflow run
[36853415460](https://github.com/2AMLogic/gf180-parasynth/actions/runs/36853415460)
is `event: push` on `feature/issue-502` at `ca242fd`, conclusion `success`.
`gh run download 36853415460 -n attack-context-model` worked with the shared
agent account. All 12 WAVs in the artifact are byte-identical to the committed
ones, and the artifact's `report.json` carries `source_commit` = `ca242fd`. This
record was not re-bound to that run, because #502 does not change the analysis
basis. The agent token still lacks `actions: write`, so `gh workflow run` still
returns 403. The push trigger makes that permission unnecessary.
