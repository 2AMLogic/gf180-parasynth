# Sealed holdout settings

One JSON per holdout case whose settings have been **committed before anything
rendered them**, plus `LEDGER.json`, which records every reading of one.

The policy these files enforce is in [`../README.md`](../README.md) → *"Both of
those sentences are now a mechanism"*; the tool is `tools/holdout.py`:

```
tools/holdout.py list                 every seal, its state and its reads
tools/holdout.py check                0 ok, 1 a seal or a read does not hold up, 2 unverifiable
tools/holdout.py seal F1D --spec <spec.json> --by <who>
tools/holdout.py open F1D --why <why> --guided-change <ref> --by <who>
```

**Do not edit a seal in place.** A seal whose settings change after it has been
read is what `check` reports as `STALE`, and it is the failure the mechanism
exists to make visible: the settings genuinely were committed first, and then
moved once the error was known. Replacing a seal on purpose is
`tools/holdout.py seal … --reseal --why …`, which bumps its generation and says
so on the record.

| file | state | what is sealed |
|---|---|---|
| `F1D.json` | sealed, never read | cutoff 500 Hz, resonance zero, at the frozen probe level — the cutoff region no case, fit or tolerance in this repository has read. Its reference clip (`surge-type2/lp-cut500-res0.00`) is **not rendered yet**, so F1D is a stated no-verdict until a host with Surge XT and dawdreamer renders it. That is the ordering the seal is for: the settings exist, the reference does not. |

Nineteen of the twenty `Holdout`-split cases in [`../cases.csv`](../cases.csv)
have no seal, and every one of them is REFUSED rather than scored until they do.
That is deliberate, and each reason is stated where a reader will meet it:
`tools/run_case.py`'s `NOT_RUN` entries for F2D / F3D / F5D (each blocked on what
its A/B/C rungs are blocked on, none of it sealing), and the sixteen Drums / Mono
/ Ensemble holdout cases the current milestone's development-set work has not
reached.
