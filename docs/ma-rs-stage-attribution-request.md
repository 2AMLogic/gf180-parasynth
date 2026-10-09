# Build-box request: MA / RS stage attribution (#556)

Host rules forbid render sweeps on the dispatch worker, and the Fischer corpus
(`GF180_TR808_REFS`, `~/dev/refs`) is not on it. What ran there: the instrument's
tests and a six-render probe (baseline, repeat shift, envelope / gate-tau, NL swap
for MA and RS). What is NOT done is below.

## Run (build box, one workload, `--jobs`-free, single process)

```
python tools/noise_stage_attribution.py MA --refs $GF180_TR808_REFS --out build/ma-attr.json
python tools/noise_stage_attribution.py RS --refs $GF180_TR808_REFS --out build/rs-attr.json
```

Each renders ~25 short hits (about 2 s each) in the model-only part, plus the
Fischer `ma8/MA.WAV` / `rs8/RS.WAV` comparison. The tool refuses (exit 2) without the
corpus manifest; the model-only part runs without `--refs`.

## What to read from the output

1. `stages.*.centroid_x_floor` / `flatness_x_floor`: how far each stage moves OUR
   trajectory, in units of the noise-realisation repeat floor (attribution needs > 3).
2. `vs_reference.perturbations[*].centroid_gain` / `flatness_gain`: how much each
   perturbation moves our trajectory TOWARD the Fischer take (positive = closer).
   Only this says which stage a repair should touch. The model-only sensitivity does
   not.
3. A second 808 recording of MA/RS (a different unit) is required before any repair
   is judged: there is one Fischer take. Report it unavailable if it is.

## Not tested and why

* MA's documented 18.2 ms rise then tau 2.65 ms (`docs/tr808-reference.md` MA
  envelope amendment): `env_writes` has no attack argument, so it is not a constant
  perturbation. It needs a model change (a rise stage) and is a separate experiment.
* Selection / confirmation split, seeds, accents, retriggers, CP/MA and CL/RS switching,
  RTL and I2S equivalence: nothing was changed in the model, so nothing needs them yet.
* Registration of any proposed parameter in `docs/sensitivity/registry.json`: none is
  proposed.
