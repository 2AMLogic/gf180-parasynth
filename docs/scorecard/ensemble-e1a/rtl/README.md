# E1A on the integrated RTL — what was measured, and what it does not say

`docs/scorecard/results/E1A.json` now carries `"engine": "integrated-rtl"`. This
directory holds the evidence that produced it: twelve per-part verification
reports from `rtl-sketch/verify_synth_top.py`, the pin-predicted register
schedule each part's onsets were measured against, the `DRUM_BUS_STALE` negative
control, the `fixed-model` twin the anchor replaced on the board, and the
cross-engine comparison between them.

Regenerate the whole thing with

```
tools/score_ensemble_i2s.py --case E1A --simulator verilator --jobs 2
```

about 27 minutes of Verilator on 8 cores, or 4 minutes with `--reuse` on a warm
`build/ensemble-E1A/`. The decoded I2S WAVs are 576 KB each and are **not**
committed — `E1A-parts-manifest.json` carries each one's sha256, every report
binds its own, and the mix WAV the record actually scores is kept as
`E1A-mix-i2s.wav`.

## Why an ensemble case, and why twelve runs

E1A is "mono bass with all available 808 parts". It is the only family on the
board where the voice datapath and the drum section carry signal **at the same
time**, for a whole phrase, through the master mix and out of the I2S
serialiser — and none of the master mix, the drum handshake or the serialiser
exists in the Python models the `fixed-model` result was computed from.

`run_case.render_ensemble` answers the case from one render by zeroing buses.
A chip cannot zero a bus, so each of those is its own run here:

| part | A_VOL | A_DVOL | A_BVOL | hits sent |
|---|---:|---:|---:|---|
| `mix` | 14746 | 14746 | 14746 | all 42 |
| `voice` | 14746 | 0 | 0 | all 42 |
| `drum-mix` | 0 | 14746 | 0 | all 42 |
| `body` | 0 | 0 | 14746 | all 42 |
| `stop:<NAME>` × 8 | 0 | 14746 | 14746 | that row only |

The four bus parts send a write stream that is identical apart from those three
words, so their writes land in the same frames and "is the output the sum of the
stems" stays a question about the master mix and nothing else.

**The stimulus is imported, not restated.** `ensemble_script` reads
`ENSEMBLE_CASES`, `PATTERN_DENSE_EXTRA` and `_bass_line` out of
`tools/run_case.py` and the patch out of `audition/patches.py`. A second copy of
the schedule here would be a bench measuring something adjacent to the case the
twin scored, which is the failure `docs/failure-modes.md` names. The four bus
parts are held to one write stream by a test, not by care.
<!-- claim: test=tools/test_score_ensemble_i2s.py::test_every_part_sends_the_same_stream_apart_from_the_three_gain_words -->

## The result

| property | integrated-rtl | fixed-model twin | tolerance |
|---|---:|---:|---:|
| Event timing | 1.9583 ms | 1.9583 ms | 10 ms |
| bus balance | 0.0 dB | 0.0 dB | 0.5 dB |
| output artifacts | 0.0 % of samples | 0.0 % | 0.01 % |

`pass`, worst 0.20, on both engines. All twelve parts were **bit-identical to
`model/synth_top_model.py`** over 288,033 decoded I2S periods each — decoded
from BCLK, LRCLK and SDATA, never from inside the DUT.

### Event timing is measured against the pin, not against the groove

Each stop's onset is compared with the frame that stop's `A_STOPS` raise
**landed in according to the CS_N pin**, plus contract 13's one period of I2S
delay — not with the frame the groove asked for. A write occupies about 1.45
frames on this link (measured: over `script()`'s 504 writes the landing frame
advances by 1 frame 277 times and by 2 frames 226 times), so a cluster of writes
is delivered a few hundred microseconds late, and folding the link's delivery
cost into an onset error would report a transport property as a mix property.
That cost is recorded separately:
`diagnostics.host_to_register_latency_frames`, worst **+34 frames (0.708 ms)**.

Both engines therefore answer the same question — "did the sound appear where
the register write was applied" — which is why the two columns above are
comparable at all.

### The worst stop agreeing exactly is one number, so eight were measured

`1.9583 == 1.9583` is one number matching one number, and two engines that place
every onset a constant number of frames after the write would produce that
agreement whether or not anything else was right. (That the estimator behind it
is not simply constant is a separate, cheaper check.)
<!-- claim: test=tools/test_score_ensemble_i2s.py::test_event_timing_is_not_inert -->

So `fixed_model_comparison.per_stop_event_timing` re-renders the model's eight
per-stop rows and compares all of them:

| stop | integrated-rtl | fixed-model | difference |
|---|---:|---:|---:|
| BD | 1.9583 ms | 1.9583 ms | 0.0000 |
| CB | 0.6458 | 0.7292 | −0.0834 |
| CH | 0.4583 | 0.5417 | −0.0834 |
| CP | 1.2083 | 0.8542 | **+0.3541** |
| HT | 0.3333 | 0.3333 | 0.0000 |
| LT | 0.0625 | 0.0625 | 0.0000 |
| OH | 0.6250 | 0.4167 | +0.2083 |
| SD | 1.3125 | 1.3125 | 0.0000 |

Worst disagreement **0.3541 ms**, 3.5 % of the tolerance, on the clap. So the
engines agree per stop, and the headline agreeing exactly is a coincidence of
which stop is worst rather than evidence of a copied number.

## Two things this measurement found that were not being looked for

**1. The output is NOT bit-exactly the sum of the stems, and the metric cannot
see it.** `diagnostics.stem_sum_minus_mix_samples` is **77,422 of 288,000
samples**, `stem_sum_minus_mix_max_abs` is **2 LSB**. This is not a defect: the
master mix is contract 12's *one* exact sum, *one* shift, *one* clamp, while
three separate single-bus runs each floor their own product — three floors
instead of one, differing by up to 2 LSB. The `bus balance` metric is an RMS
ratio at a 0.5 dB tolerance and is many orders of magnitude too coarse to see
it, and reads exactly 0.0 dB — it does move when the mix genuinely is not the
sum.
<!-- claim: test=tools/test_score_ensemble_i2s.py::test_bus_balance_sees_a_mix_that_is_not_the_sum -->

The number is on the record because "the stems sum to the output" is true to
0.5 dB and false to the bit, and only one of those
two statements was previously written down.

**2. `DRUM_BUS_STALE` is caught by the comparison the anchor rests on, and is
nearly invisible to all three case metrics.** Measured, in
`diagnostics.negative_control`:

| | clean | with `INJECT_BUG_DRUM_BUS_STALE` | tolerance |
|---|---:|---:|---:|
| periods differing from the model | 0 of 288,033 | **198,223 of 288,033** | 0 |
| worst sample difference | — | **12,781 LSB (0.39 FS)** | — |
| Event timing (BD) | 1.9583 ms | 1.9792 ms | 10 ms |
| bus balance (mix RMS shift) | 0.0 dB | −0.0007 dB | 0.5 dB |
| output artifacts | 0.0 % | 0.0 % | 0.01 % |

The mutation — the master mix reading the **previous** frame's drum buses, the
mix-timing hazard issues #82/#85 raised — changes 69 % of the samples on the
wire by up to 0.39 of full scale, and moves `Event timing` by **one frame**
(0.0209 ms) against a 10 ms tolerance, `bus balance` by 0.0007 dB of 0.5, and
`output artifacts` not at all. It shifts both drum buses, and the stems are
summed through the same shift, so an RMS ratio is close to blind to it by
construction.

**What that means for how this anchor should be read.** The three scorecard
metrics are tolerances on a *sound*; the discrimination comes from the bench's
bit-exact wire-versus-model comparison, which every part passed and which this
mutation fails on 198,223 periods. `tools/score_ensemble_i2s.py` REFUSES to
write a record at all if that control does not fire, and
`tools/compare_ensemble_candidate.py` REFUSES a candidate that does not carry a
`caught` control — because the alternative is an anchor whose metrics look like
they are guarding the hazard and are not.
<!-- claim: test=tools/test_compare_ensemble_candidate.py::test_rejects_a_candidate_with_no_caught_negative_control -->

The same comparison refuses a candidate that is not `integrated-rtl` at all: two
`fixed-model` records would certify the model against itself and report it as
instrument evidence.
<!-- claim: test=tools/test_compare_ensemble_candidate.py::test_rejects_a_candidate_that_is_not_integrated_rtl -->

A prediction was wrong here and the measurement corrected it: the expectation
going in was that a one-frame bus shift would be a *small* audio difference as
well as a small metric movement. It is a 0.39 FS difference on two thirds of
the phrase. The metric blindness was the right half of the guess; the
"therefore it barely changes the audio" half was not, and reasoning rather than
running the injected part would have put that in this file as a fact.

## Wrong-then-right rate for the work that produced this file

Three, all caught by a control or a test rather than by inspection:

1. The `DRUM_BUS_STALE` audio magnitude above — predicted small, measured
   12,781 LSB. Caught by running the injected part instead of asserting it.
2. The start-red test for `Event timing` first shifted the schedule by 20 ms and
   the estimator **refused** instead of reporting a large error: past about
   19.5 ms the first onset falls outside `worst_event_offset_ms`'s own pairing
   window. Corrected to 15 ms, and the refusal kept as its own test
   (`test_a_schedule_too_far_from_the_onsets_withholds_a_number`) — the
   refusal is correct behaviour, and a start-red test that passes because the
   estimator refused would have proved nothing about the metric moving.
3. The first edit of `verify_synth_top.py` landed in the main checkout rather
   than in the issue worktree. Caught by an import error, moved with `git
   apply`, and `check-main-clean.sh` confirmed main clean. Process, not
   measurement, but it is the same class of mistake.

## What is still `fixed-model`

E1B and E2A. This anchors **one** ensemble case, which is what issue #292 asked
for. E1B is the dense arrangement of the same patch and groove and needs no new
machinery — `tools/score_ensemble_i2s.py --case E1B` is the whole command; it is
another 27 minutes of simulation, which is the only reason it is not here too.
