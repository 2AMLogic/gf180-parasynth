# Hi-hat and cymbal high-band probes

**These were rescued from a scratchpad.** The agent that wrote them reported an
excellent result and committed nothing; its branch has zero commits. The
findings survived only because they were transcribed by hand into a docstring
(PR #97). The scripts themselves were one `rm -rf /tmp` from gone.

They are committed here **verbatim**, with exactly one change: they were written
against a worktree at `/tmp/wt-hihat` that no longer exists, so that path now
resolves from `__file__`. Nothing else was touched — not the logic, not the
numbers, not the comments.

## What they established

The 808 cymbal's missing 9–13 kHz shoulder is **a mistuned filter, not a missing
one**. `M_CYHI` already sits at 10.5 kHz; raising its Q from 2.5 to 4.0 takes the
five-band cost from **18.1 to 6.0**. Restoring the filter that was *hypothesised*
to be missing (Hh1) closed **0.4 of the shoulder's 8.0 points** and moved 5–9 kHz
the wrong way — the hypothesis was refuted at the first step. Adding a second
2-pole reaches only 10.3, so **more filtering is not the lever**.

Whether that retune should ship is **not settled** — see #99, where it improves
one measure threefold while the scorecard score gets worse, and #101, where the
measurement path itself is shown to carry a 6 dB windowing artefact.

## The files

| file | what it does |
|---|---|
| `hh_probe.py` | the main sweep — every number labelled DERIVED (a formula on schematic component values) or MEASURED (a render). Prints a provenance block: commit, dirty flag, per-file SHA-256, numpy version |
| `hh_probe2.py` | Hh1 with its numerator actually enabled; the CY low-band residual; the same question for CH and OH |
| `hh_probe3.py` | confirms the offline sweep on the **real fixed-point block**, as ordinary register writes |
| `hh_probe4.py` | **the correction.** Part 2's "cost 7.2, every band green" used the offline emulator for a knob it was never validated for — the low band's envelope peak. The fixed-point run gave **30.6, not 7.2** |
| `combo.py`, `patch_q.py` | small harnesses for running the acceptance suite against a patched `CY_HI_Q` |

`hh_probe4.py` is the most valuable file here and the one most likely to have
been thrown away: it is the record of a result that looked good and was wrong.

## Status

These are still **probes**, numbered rather than named, but the general
methodology they share with the conga tool now lives in one tested module,
`model/measure_harness.py` (#104), next to `model/audio_measure.py`:
`validate_known_answer`, `floor_for_these_signals`, `windowed_alike`,
`descent_test` and `assert_precondition`, each with an injected-bug control in
`model/test_measure_harness.py`.

What was folded, and what was deliberately not:

- `hh_probe.py`'s three REFUSE-unless-it-agrees checks (reproducing
  `CY_FIT['shares']`, the offline emulator against the fixed-point render, the
  cached band-energy estimator against `audio_measure.band_energy`) and
  `hh_probe2.py`'s per-voice emulator check now call
  `measure_harness.assert_precondition`. The tolerances and the numbers they
  guard are unchanged; that is the only change to either file since the rescue
  described above.
- `hh_probe3.py`, `hh_probe4.py`, `combo.py` and `patch_q.py` had no helpers of
  their own that overlap the harness -- they import `hh_probe`'s -- so they are
  untouched.
- `hh_probe.py`'s renderer, filter derivations and `fast_band_energy` are
  hi-hat/cymbal apparatus, not validation methodology, and stay here.
- `hh_probe5.py` (added after #104 was filed) keeps its own `Refused`
  exception: its refusals carry case-specific explanations the generic
  message would lose. New probes should start from `measure_harness`.
