#!/usr/bin/env python3
"""hh_probe5 with an arm whose every (Q, gain) point refuses: the run behind
issue #1936's A1.

    .venv/bin/python tools/probes/hihat/hh_probe5_deaf_arm_repro.py <mode> [--refs DIR]

`mode` picks the arms whose SWEEP points `cy_kit` refuses:

    one-candidate   3pole-bp alone -- the shape the issue read
    baseline        the 2-pole alone, which every later section reads
    every-arm       all three
    none            THE CONTROL: nothing refused; the real probe, unmodified

Everything else is the real `hh_probe5.main` under `--quick`: the real
corpus (`--refs`, else `run_case.configured_refs()`), the real renders, the
real fit and holdout. Only the sweep points of the named arms refuse. The
unity-cascade precondition's `cy_kit("control")` carries no `q` and still
gets the real kit: that precondition is not what is under test, and a
literally always-raising `cy_kit` raises there, before the sweep, as an
uncaught refusal (#1640).

Prints the probe's exit status, its `REFUSED:` line and what hh5.json holds,
then leaves with the probe's own status (1 when the probe raised), so the
run can be compared with the table in PR #2074.

Measured on origin/main @ e2d1bae7 (2026-11-28, Fischer corpus @ 85fbecf):
`one-candidate` rc 0 with the arm gone from hh5.json; `every-arm` and
`baseline` `KeyError: '2pole'`, a traceback. With the repair: 2, 2 and 2,
and `none` 0. The committed suite takes `main` to the sweep and no further
(it needs the corpus for what follows); this is the real end of the run.
"""
from __future__ import annotations

import contextlib
import io
import json
import pathlib
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))

import hh_probe5 as h                                              # noqa: E402
import run_case as rc                                              # noqa: E402

DEAF = {"one-candidate": {"3pole-bp"}, "baseline": {"2pole"},
        "every-arm": {"2pole", "3pole-bp", "3pole-raw"}, "none": set()}


def main(argv=None) -> int:
    mode = argv[1] if argv and len(argv) > 1 else ""
    if mode not in DEAF:
        print(__doc__.split("`mode` picks")[0])
        print(f"REFUSED: unknown mode {mode!r}; one of {', '.join(DEAF)}")
        return 2
    refs = argv[3] if len(argv) > 3 and argv[2] == "--refs" else str(rc.configured_refs())
    real_cy_kit = h.cy_kit
    deaf = DEAF[mode]

    def cy_kit(arm, **kw):
        if arm in deaf and "q" in kw:
            raise h.Refused(f"{arm}: stubbed refusal at the apparatus precondition")
        return real_cy_kit(arm, **kw)

    h.cy_kit = cy_kit
    out = pathlib.Path(tempfile.mkdtemp(prefix=f"hh5-{mode}-")) / "out"
    printed, t0 = io.StringIO(), time.time()
    try:
        with contextlib.redirect_stdout(printed):
            rc_ = h.main(["--refs", refs, "--out", str(out), "--quick"])
        print(f"[{mode}] rc={rc_}  ({time.time() - t0:.0f} s)")
    except Exception as e:                       # the pre-repair shapes left this way
        print(f"[{mode}] RAISED {type(e).__name__}: {e}  ({time.time() - t0:.0f} s)")
        rc_ = 1
    lines = printed.getvalue().splitlines()
    for ln in lines:
        if ln.startswith("REFUSED:"):
            print(f"    {ln}")
    for ln in [ln for ln in lines if "proposes:" in ln][:6]:
        print(f"    {ln}")
    record = out / "hh5.json"
    if record.exists():
        rec = json.loads(record.read_text())
        sweep = rec["surrogate_sweep"]
        print("    hh5.json: shortlist arms", sorted(rec.get("shortlist", [])),
              "| refused_arms", sorted(rec.get("refused_arms", [])),
              "| sweep points answered per arm",
              {arm: sum("cost" in r for r in rows) for arm, rows in sweep.items()})
    else:
        print("    hh5.json: not written")
    return rc_


if __name__ == "__main__":
    sys.exit(main(sys.argv))
