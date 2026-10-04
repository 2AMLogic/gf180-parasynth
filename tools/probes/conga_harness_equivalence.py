#!/usr/bin/env python3
"""Does moving the conga tool's methodology into `model/measure_harness.py`
(#104) change what `tools/measure_conga_body_spread.py` reports?

Runs two copies of the tool -- OLD (pre-extraction, e.g. the tool in a checkout
of main before #104) and NEW -- against the same Fischer corpus, for the full
report, `--check`, and `--descent`, and compares the JSON reports field by
field with `provenance` removed (it names the file and commit, so it differs by
construction). Exit 0 only if every report is identical and both copies
returned the same exit code.

The `--descent` candidates are CONSTRUCTED here, because the third-party
"808 conga" pack the original run used is not in this repository:

  Conga-808-Mid.wav  MC50.WAV resampled to 48 kHz -- a known re-pressing;
                     the original run found correlation 1.000 against MC50
  Conga-808-Hi.wav   HC75.WAV pitch-shifted by 100/97 -- a known re-pressing
                     that the resampling-ratio search must undo
  Conga-808-Low.wav  a cowbell file posing as a low conga -- the control that
                     must NOT be called a match

    python tools/probes/conga_harness_equivalence.py \\
        --old <main checkout>/tools/measure_conga_body_spread.py \\
        --new tools/measure_conga_body_spread.py --refs /tmp/tr808-ref

Needs `soundfile`. Refuses (exit 2) without the corpus.

Result on 2026-09-26 (branch feature/issue-104 vs main 72f7469, Python 3.12,
numpy 2.5.3, scipy 1.18.1, soundfile 0.14.0): identical for all three modes;
MC50 re-pressing 1.000 at x1.0000, HC75 re-pressing 1.000 at x0.9700, cowbell
control 0.022 ("no Fischer file matches it").

Start-red control, same day: a NEW copy whose descent verdict threshold was
raised from 0.95 to 1.01 reports `descent identical=False`, NOT EQUIVALENT,
exit 1. Wrong-then-right: the first injection tried (threshold 0.9999999) did
NOT turn it red -- correlations are rounded to 4 places, so the re-pressings
read exactly 1.0 and still cleared it. An injection has to be checked for
whether it can bite before its green means anything.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import tempfile


def _configured_refs() -> pathlib.Path:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    import run_case
    return run_case.configured_refs()


def make_candidates(refdir: pathlib.Path, out: pathlib.Path) -> None:
    import soundfile as sf
    from scipy.signal import resample_poly
    x, _ = sf.read(refdir / "mc8/MC50.WAV")
    sf.write(out / "Conga-808-Mid.wav", resample_poly(x, 160, 147), 48000)
    x, sr = sf.read(refdir / "hc8/HC75.WAV")
    sf.write(out / "Conga-808-Hi.wav", resample_poly(x, 100, 97), sr)
    x, sr = sf.read(sorted((refdir / "cb8").glob("*.WAV"))[0])
    sf.write(out / "Conga-808-Low.wav", x, sr)


def run(tool: pathlib.Path, refdir: pathlib.Path, tmp: pathlib.Path,
        tag: str, extra: list[str]) -> tuple[int, dict | None]:
    js = tmp / f"{tag}.json"
    p = subprocess.run([sys.executable, str(tool), "--refs", str(refdir),
                        "--json", str(js), *extra], capture_output=True, text=True)
    body = json.loads(js.read_text()) if js.exists() else None
    if body:
        body.pop("provenance", None)
    return p.returncode, body


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", type=pathlib.Path, required=True)
    ap.add_argument("--new", type=pathlib.Path, required=True)
    ap.add_argument("--refs", type=pathlib.Path, default=_configured_refs())
    a = ap.parse_args(argv)
    if not (a.refs / "mc8/MC50.WAV").exists():
        print(f"REFUSED: no Fischer corpus at {a.refs}", file=sys.stderr)
        return 2
    try:
        import soundfile  # noqa: F401
    except ImportError:
        print("REFUSED: soundfile is not installed", file=sys.stderr)
        return 2

    ok = True
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        cand = tmp / "candidates"
        cand.mkdir()
        make_candidates(a.refs, cand)
        for mode, extra in (("full", []), ("check", ["--check"]),
                            ("descent", ["--descent", str(cand)])):
            ro, jo = run(a.old, a.refs, tmp, f"old-{mode}", extra)
            rn, jn = run(a.new, a.refs, tmp, f"new-{mode}", extra)
            same = ro == rn and jo is not None and jo == jn
            ok &= same
            print(f"{mode:8s} rc old={ro} new={rn}  identical={same}")
            if not same:
                for k in sorted(set(jo or {}) | set(jn or {})):
                    if (jo or {}).get(k) != (jn or {}).get(k):
                        print(f"  DIFF {k}\n    old {json.dumps((jo or {}).get(k))[:600]}"
                              f"\n    new {json.dumps((jn or {}).get(k))[:600]}")
            if mode == "descent" and jn:
                for r in jn.get("descent", []):
                    print(f"  {r.get('file')}: {r.get('best_match')} "
                          f"r={r.get('best_correlation')} x{r.get('at_resample_ratio')} "
                          f"-- {r.get('verdict')}")
    print("EQUIVALENT" if ok else "NOT EQUIVALENT")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
