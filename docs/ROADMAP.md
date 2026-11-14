# Roadmap

gf180-parasynth is a Minimoog-shaped paraphonic voice and a TR-808-style drum
section in one gf180mcu chip; this file says where it stands, what comes next,
and what is waiting on what.

Status as of 2026-11-13, `main` @ `4fee563b`.

This is the hand-written ordering of the work. It does not replace the three
generated views, which stay authoritative for their own facts: the capability
graph and acceptance board in [`README.md`](../README.md) (regenerated on every
push by `tools/compile_dag.py`), the issue lifecycle snapshot in
[`WORK_PLAN.md`](../WORK_PLAN.md), and the evidence-tier verdict in
[`manifests/verdict.txt`](../manifests/verdict.txt). Where this file and a
generated view disagree, the generated view is right and this file is stale.

## Where it stands

**The priority is sound, not silicon.** The current milestone is #282: a
great-sounding mono Moog-like synth and a complete 808-style kit. `CLAUDE.md`
("Current milestone priority") puts that ahead of tooling and release work, and
the tracker (#724, #33) pauses gf180mcu ASIC work behind it.

- **Acceptance board:** 19 of 100 cases have a valid measurement: 9 pass, 10
  fail, 7 no verdict, 74 not run ([`docs/scorecard/BOARD.md`](scorecard/BOARD.md)).
  Mono has 2 valid cases of 32; Drums 11 of 32.
- **RTL against the models:** the nightly `full-rtl` job ran the voice, drum
  kit, modal bank and whole-chip verifiers green on `54e51921` (2026-11-13); its
  only failing step was the strict "nothing is red or stale" check.
- **Capability graph:** F2 and M2 stamped; F3, M3, D2, S1, S3 green; D3 red
  (`model/sound_report.py` exits 1, the drum defects below); M4 and S2 blocked.
  F1, M1 and D1 read STALE and I1, I2 read "never run" although the nightly
  runs their verifiers: the result is uploaded as an artifact and never reaches
  `docs/dag-results.json` on `main`. M5 and D4 read TODO against issues that
  are closed (#48 completed, #22 not planned).
- **Nightly:** red five scheduled runs in a row (2026-11-09 to 11-13). Failing
  jobs on the last run: `sound-report` (a locked property moved or a target is
  missed), `full-rtl` (the strict staleness step only), `reference-freshness`
  (snapshot absent or stale). `controls` passes.
- **Physical flow:** `synth_top` with the real `drum_kit` routes in two quarter
  slots at 60.1 % utilisation with 0 router DRC errors
  ([`pnr-synth-top.md`](pnr-synth-top.md)); it does not fit one (#33). The
  half-slot padframe run has not finished detailed routing
  ([`pnr-shuttle-halfslot.md`](pnr-shuttle-halfslot.md)). No LVS, no sign-off
  DRC, no gate-level simulation.
- **Evidence tier:** `tier: none`, T1 2 of 11 items met (9 testbenches, #738;
  10 repo hygiene, #737). The gap tracker is #724.
- **Hardware:** the first qualified R0 physical I2S capture is still an open
  issue (#208).

## Now

Can start today. The first group is already claimed (`loom:building`) and is
listed so nobody duplicates it.

| What | Done when | Issue |
|---|---|---|
| Estimator defects: the two the exact-period fold repair found (#774, #775) and `repeat_period` (#780) | each bug has a failing-then-passing test on `main` | #774, #775 in progress; #780 awaiting curation |
| Count discrimination rows under the `jit.dphase_ar1` floor | the count is committed with its provenance | #768 in progress (PR #773 has changes requested) |
| Retire the three netlist patches in `pnr/klt/ladder_dp/run-klt.sh` | the script runs without them against current klayout-tools | #765 in progress |
| Remove stale estimator copies from `tools/measure_partial_balance.py` | the copies are gone and the suite passes | #772 in progress; PR #779 approved |
| Carry the nightly's slow evidence to `main` | after one nightly, `docs/dag-results.json` on `main` has records for F1, M1, D1, I1, I2 and the README table shows them | none; seed 1 |
| Give DAG nodes M5 and D4 evidence, or retire them | neither node cites a closed issue; each names a suite, verifier or a written reason | none; seed 2 |
| Correct `docs/milestones.md` milestone 3 and the README's T1 sentence | neither contradicts `rtl-sketch/synth_top.v` or `manifests/verdict.txt` | none; seed 3 |
| Aggregate characterization report from the committed route evidence (T1 item 8) | one report under `docs/` built by a script from `pnr/orfs/evidence/synth_top/joined-d1e5068/`, and the `klt signoff` result for item 8 recorded, met or refused | none; seed 4 |

## Next

Unblocked by a named item above or by a run on a host that has the corpus or
the build box.

| What | Done when | Issue | Waits on |
|---|---|---|---|
| Re-derive the `jit.stability` and `mpd.*` attributions with the exact-period fold | regenerated results committed | #778 | a host with the reference corpus |
| Corpus re-measurements after estimator fixes | each results file regenerated and committed | #705, #700, #676 | a host with the corpus; #676 after #533 |
| Build-box re-capture for drum contract rev 18 | deadline monitor, UART-bridge record and latency pin committed | #693, #453 | the build box; unblocks PR #662 (#656) |
| Cymbal DECAY off the Q0.16 rate floor | late-T20 qualified and monotone at DECAY 7.5 and 10 | #656 (PR #662) | #693 |
| Cymbal DC coupling through RTL to decoded I2S | bit-exact bench with mutants, paired qualification | #510, #552, #553 (PR #657) | #678 (post-R2 image key and verification directory) |
| Drum sound repairs the gate names | each `[sound]` case moves to pass on the board, confirmed on untouched conditions | #556, #557, #558, #559, #593, #162 | their `loom:blocked` holds; #588 is a gate defect behind the BD and tom/conga impulse readings, and #591 waits on #350 |
| Cowbell partial balance | case within tolerance on the board | #335, #107 | related PR #377 (per-partial envelopes), which is held |
| Mono coverage: 29 of 32 cases not run | cases carry a valid verdict | #337, #122, #497 | a qualified reference rig (#124, #497) |
| Judge validation | detector coverage matrix and first bounded experiment committed | #158, #521 (PR #534), #530 | review changes on PR #534 |

## Later

Direction; each needs its question answered first.

- **Resume the gf180mcu flow** (T1 items 2, 3, 4, 7, 11; #353 half-slot
  harvest). Question: two quarter slots, or cut something (#33)? It stays
  paused until #282 is met.
- **T1 items 1 and 5.** Question: what binds a committed gate-level netlist to
  the RTL, and which spec table is ratified? `spec/NUMERIC-CONTRACT.md` is
  proposed, not ratified.
- **M4, matches real hardware.** Question: where does one documented
  self-oscillation recording come from? 0 of 222 recordings in hand qualify.
- **First qualified physical capture** (#208, #325, #324). Question: who runs
  the bench session; the session sheet draft is PR #331.
- **Paraphonic four-note chords with the complete kit** (#23). Question: does
  it fit once #33 is decided?
- **Resonance-keyed cutoff correction** (#257, PR #555) and the above-Nyquist
  glide mismatch (#247): each needs a ruling on whether the case is in scope.

## Blocked and waiting

52 open issues carry `loom:blocked`, and 12 of the 20 open pull requests are
held or have changes requested; `WORK_PLAN.md` lists them. The causes group as:

- **Needs a host with the reference corpus or the reference rig:** #778, #768,
  #705, #700, #676, #497, #706 (PR #750), and the nightly `reference-freshness` job.
- **Needs the build box or the board:** #693, #453, #205, #208.
- **Needs an operator decision** (`loom:operator`): #152, #247, #324, #562,
  #124, #760, #762, and PR #750.
- **Needs review changes on a held PR:** #306 (PR #563), #372 (PR #647), #350
  (PR #642), #598 (PR #603), #559 (PR #595).
- **Upstream:** none open that this repo links; the klayout-tools bugs behind
  the `run-klt.sh` patches were fixed upstream (#765).

Dispatchable today: the four unfiled items under Now, and #780 once curated.
Everything else that is ready is already claimed.

## Not planned

- gf180mcu ASIC work while #282 is open (#724, #33).
- Deduplicating the BS.1770 loudness copies and the candidate comparators
  (#749, #732 closed as not planned).
- Widening a tolerance to make a failing case pass
  ([`plans/README.md`](plans/README.md)).
