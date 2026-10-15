# Reference-freshness snapshots

Destination for the four bounded response snapshots consumed by
`tools/reference_freshness.py` (nightly job `reference-freshness`, #622).
**This directory is intentionally empty of evidence until a provisioned host
publishes it; the check reports REFUSED until then.** Never fabricate or copy
frozen rows to turn CI green.

Required files (exactly these; peak files and Diva are out of scope):
`response-ours.json`, `response-surge-rk.json`, `response-surge-huov.json`,
`response-miniv3.json`.

## Publishing

On a reference host with Surge XT and Mini V3 provisioned (licence, readback
and silence assertions passing):

```
.venv/bin/python model/reference_compare.py --stage response --devices ours,surge-rk,surge-huov,miniv3 --out /tmp/reference-freshness
```

Inspect the output, copy the four `response-<device>.json` files here and
commit them. Do not modify `docs/reference-compare-results.json`: it is a frozen
historical corpus (its `ours` rows are a revision-8 anchor).

## What the check means

It measures **commit age** of the oldest of the four files (floor of elapsed UTC
days; 14 passes, 15 fails). A recent commit does not prove a recent measurement
or acoustic correctness; a reformat or copied rows would satisfy it. It reports
evidence availability only and does not validate the synth.
