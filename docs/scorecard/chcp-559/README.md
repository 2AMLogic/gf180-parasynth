# #559 CH and CP: DEV/CONFIRM record (Part of #282)

> **STATUS: CH result NOT CONFIRMED (provisional).** Every record in this
> directory fails its own provenance check (`python3 tools/probes/chcp_559.py
> audit`: 0/7 clean; labels in `provenance-status.json`). The CH figures below
> are readings from runs whose source tree cannot be recovered. They stay
> provisional until the build-box rerun at the end of this file replaces every
> record from one clean commit. The candidate stays DISABLED.

Ratios are to the #379 WEAK bar (1.0 = at the bar; lower is better). No calibrated
between-recording bar exists (gate-379 README section 9), so nothing here is a gate pass.

## CH (flatness): provisional
- Shipped: flatness 8.87 (DEV median), 8.07 (CONFIRM-set median).
- Selected on DEV (`ch-sweep-dev.*`, UNREPRODUCIBLE): CH high-pass Q 2.5 -> 0.5 (DEV 8.87 -> 5.21).
- Provisional CONFIRM-set reading (`ch-confirm-hpq05.*`, UNREPRODUCIBLE): 8.07 -> 4.75.
  The confirm rule's verdict in that record was "CONFIRMED". **This README does not
  adopt that verdict**, because the record cannot be tied to a source (next section).
  Denominators: the **median is over 8 conditions**, and **"better on 6/6" counts the
  6 accent-1 strikes only**. One per-condition disclosure: attack 1.01 -> 1.44 at [13651, 1.0].
- Stage 2 (adding hat band-pass Q 6 -> 3, `ch-confirm-hpq05-bpq3.*`, UNREPRODUCIBLE):
  flatness 2.43, but attack regresses 0.92 -> 1.72 (and CY worsens in the sweep). The rule
  rejected it, and it is not in the candidate.
- Still failing either way: 4.75 is about 4.7x the bar. The Qs are [inferred] reference readings.
- Status: `CANDIDATE_559 = {"CH_HP_Q_X10": 5}` in model/drums_fx.py, DISABLED (kit_808 unchanged).
  Reachable via `kit_808_candidate_559()` / `verify_drums.py --kit candidate-559`.

### What the CONFIRM set actually holds out
The 8 CONFIRM conditions are 6 strikes at accent 1 plus two accent variants of
strikes already in the 6 (`conditions("confirm")`). Two of those variants are
near-duplicates on every gate feature (checked from `ch-confirm-hpq05.json`'s
per-condition ratios):
- `[8356, 2.0]` equals `[8356, 1.0]` exactly, at recorded precision, for both kits.
- `[4355, 0.5]` differs from `[4355, 1.0]` by at most 0.04 in any ratio.

So the median double-counts two strikes, and the accent dimension is effectively
untested. The likely cause is that the gate features are level-invariant
[inferred, not tested]. **The real holdout is 6 strikes of one recording,
`ch8/CH.WAV`.** That is a narrower claim than "untouched conditions" suggests:
the result says nothing yet about other CH takes or about accent. The conditions
are frozen and are not changed after the fact. A wider holdout (another
recording, or an accent-sensitive feature) is a separate, pre-registered change.

### Provenance defects (why every record is labelled unreproducible)
| record | commit named | tree | model at start | model in JSON | defect |
|---|---|---|---|---|---|
| `ch-sweep-dev` | 460d0628 | dirty | -- | 730264ba (never committed) | no engine/probe/measurement hash |
| `ch-confirm-hpq05` | 460d0628 | dirty | 50d295f6 (log; never committed) | 136fbadc | **model changed mid-run; JSON silent** |
| `ch-confirm-hpq05-bpq3` | 460d0628 | dirty | 136fbadc | 136fbadc | probe moved mid-run (recorded) |
| `cp-sweep-dev`, `cpt-sweep-dev` | b10a6e8f / 460d0628 | dirty | -- | 76bf5af0 / 50d295f6 | no engine/probe/measurement hash |
| `check-fast` | -- | -- | -- | -- | no provenance at all |
| `cp-tail` | 23e49751 | clean | 136fbadc | 136fbadc | lacks only `measure_sha16` (field added later) |

"Never committed" was checked by hashing `model/drums_fx.py` at every commit on
every ref: 730264ba and 50d295f6 appear in none. 136fbadc first appears in
18b2f6c1, which was committed after these runs.

The earlier version of this README said "the records say so" about the model hash
change. That was wrong: only `ch-confirm-hpq05.log` says so, and the JSON does not.

**Wrong-then-right, this PR (3 so far):**
1. Provenance taken at the END of a run. Two sweeps and the headline confirm
   recorded the model as it was when they finished, not the model they imported.
   Fixed in 18b2f6c1 (start and end), after the headline run.
2. The confirm guard reconstructed the sweep's engine from its nominal commit
   (`engine_fingerprint_at`). That reads the committed model, not the dirty one that
   was swept, and today's `fixed.py`/`modal_fixed.py` for both sides. So it compared
   main's engine to main's engine. Removed (#595 review).
3. A run whose sources moved wrote its record and exited 0. It now exits 3 and
   writes `provenance_ok: false`.

What the probe does now (`tools/probes/chcp_559.py`, controls in
`tools/probes/test_chcp_559.py`):
- Provenance (model hash, engine+images fingerprint, probe hash, and a new
  `measure_sha16` over `perceptual_gate.py`/`run_case.py`/`clap_d12a_probe.py`) is
  taken at START and END. If they differ, the record carries both and
  `provenance_ok: false`, and the run exits 3.
- `confirm` refuses **before any render** in four cases: the sweep is dirty, moved,
  flagged, or missing a field; the confirming tree is dirty; or any of model,
  engine, probe or measurement hash differs from what the sweep recorded.
- `tables` refuses to derive from an unreproducible sweep.
- `audit` judges every record here by its own fields. `regen` ends with it.
- The defeating inputs are committed as tests: a model file edited mid-run (a real
  file edit on a copied tree), the committed `ch-sweep-dev.json` itself, an edited
  `fixed.py`, an edited `perceptual_gate.py`, and a refusal asserted to precede
  `sel.confirm`. What still defeats it: a record whose fields lie about the tree.
  Only a clean clone can rule that out, which is why the rerun runs from one.

The Judge's supplementary checks make the CH number *likely*: the shipped-kit
CONFIRM medians reproduce exactly in the later bpq3 run, and the current engine
fingerprint equals main's. Likely is not established, and that is the reason for
the rerun.

## CP (decay 15.9, attack 6.2-7.1)
These records are also UNREPRODUCIBLE (dirty trees) and are regenerated by the same rerun.
- Tail tau / level sweep (`cp-sweep-dev.*`, `cpt-sweep-dev.*`, frozen amendment CP-T):
  no programming-only change selects. tau 120 ms improves decay (9.1) but breaks the
  eligibility rules (spec/centroid regress); every CP-T candidate violates at least one guard.
  `SELECTED (DEV): None`. The CP defect is not repairable by the registered tail tunables;
  it needs a structural change (coordinate with #556 shared MA resources).
- `cp-tail.*` (clean commit; lacks only the new measurement hash): measured reference tail
  (amp tau ~83 ms 0.08-0.2 s, ~315 ms 0.3-1.2 s: two slopes, which a single-tau tail cannot match).

## Build-box rerun request (BLOCKING, OPEN)
One job. It needs the Fischer corpus, which is not on the developer laptop. A coordinator
runs it on the pinned box (CLAUDE.md, "Heavy work runs on the build box"). Subagents do not.

1. **Source.** Use the PR head that carries this README. Ship it as a `git bundle` and clone
   it, so `.git` is real and the dirty flags mean something. Do not edit `tools/` or `model/`
   in that clone.
2. **References.** Copy `~/dev/refs/` to the same path on the box. The default is
   `~/dev/refs/sounds-tr808-fischer`; otherwise set `GF180_TR808_REFS` to it. Check it
   with `python3 tools/probes/chcp_559.py refs`, which must exit 0 (exit 2 = REFUSED).
3. **Run** (Python 3.12 venv with numpy/scipy, from the clone root, under `nohup`):
   `python tools/probes/chcp_559.py regen > regen.log 2>&1`
   This one command does the following:
   - refuses on a dirty `tools/`/`model/`;
   - runs three streams in parallel (at most 3 cores), each stopping at its first nonzero step:
     - A: check-fast, cp-tail, baseline, cp-sweep, 2 CP frontier confirms;
     - B: cpt-sweep, CP-T frontier confirm;
     - C: **ch-sweep, ch-confirm-hpq05, ch-confirm-hpq05-bpq3**;
   - writes `docs/sensitivity/chcp559-sweeps.txt` (`tables`);
   - checks that sources did not move, then runs `audit`.
   Runtime has not been measured as one job. Leave the other 5 cores alone, or say how
   they are divided.
4. **Expected output.** Exit 0, and a last line of
   `REGEN OK at <head sha12>: streams {...}, tables exit 0, audit N/N clean at one commit`.
   Then `python tools/probes/chcp_559.py audit` exits 0 with every record CLEAN, naming
   that same commit. Anything else is NO-VERDICT: report the failing step's `.log`, and
   do not commit partial records.
5. **Commit** (to `feature/issue-559`) every regenerated file:
   - every `docs/scorecard/chcp-559/*.json` and `*.log` (new ones include `baseline.json`
     and `cp-confirm-frontier-*.json`);
   - `docs/sensitivity/chcp559-sweeps.txt`;
   - `regen.log` as `docs/scorecard/chcp-559/regen.log`.
6. **Then, by hand, in the same PR:**
   - Remove the entries in `provenance-status.json` that are now clean.
   - Update the CH and CP figures in this README and in `docs/sensitivity/ch-hp-q.json` /
     `hat-bp-q.json`, and delete their "PROVISIONAL (#595 review)" limit.
   - Restate CH as confirmed or not **from the new `ch-confirm-hpq05.json`**, with both
     denominators and the 6-strikes-of-one-recording scope.
   - If any figure moved, add it to the wrong-then-right list above with old and new values.
   - Update the PR body.

## Not done
- The clean rerun above (blocking).
- No RTL / I2S equivalence or deadline run for the candidate (needs the build box):
  `python rtl-sketch/verify_drums.py --kit candidate-559` then `make verify`.
- No image delivery claimed.
