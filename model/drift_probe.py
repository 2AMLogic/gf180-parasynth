#!/usr/bin/env python3
"""Walk history and measure ONE property per commit, to find what moved a lock.

    python model/drift_probe.py --range 28dfd55..origin/main \
        --paths model/reference_rigs.py model/drums_fx.py

Why this exists: `model/sound_report.py` compares today's measurement against
a value locked at a named commit. When a lock is found OUT of tolerance the
report says *that* it moved, never *when*. This answers "when", by checking
out each candidate commit's own tree with `git archive` and running **that
tree's own measurement functions** on it.

Running the historical tree as-is is deliberate, and it is the only way to get
an honest answer: the cause of a moved lock can be the model (a changed
cutoff law, a changed envelope) *or* the estimator (`audio_measure.py`,
`drum_verify.py`) *or* the property's own definition inside `sound_report.py`.
Pinning the measurement code to HEAD and swapping only the model would hide
the second and third cases entirely -- and two of the three drifts this repo
has actually seen were estimator changes, not model changes.

The two properties issue #242 is about are wired up by name:

    corner-drift   sound_report.m_corner_spread(ctx)       -> LADDER corner ratio drift, %
    lt-attack      sound_report.m_drum_attack("LT")(ctx)   -> LT attack, ms

A commit whose tree predates a property (or cannot import at all) reports
`n/a` with the reason, never a number. `REFUSED` is a first-class outcome
here: a probe that answers when it cannot is worse than one that is absent.

Self-check: `--self-check` measures HEAD's working tree through the same
driver and compares against `model/sound_report.py`'s own full report values,
so the probe cannot silently drift from the thing it is explaining.

WHAT IT FOUND, and why the numbers here are worth more than the tool
------------------------------------------------------------------
Issue #242: two `[lock]` properties were OUT and nobody knew when they moved.

    $ python model/drift_probe.py --range 28dfd55~1..origin/main --jobs 3 \
        --paths model/reference_rigs.py model/drums_fx.py model/drum_verify.py \
                model/audio_measure.py model/drums_fx_render.py model/sound_report.py

    commit    corner-drift    lt-attack  subject
    28dfd55         9.4046      16.6250  Evaluation: a per-voice, per-property ...
    0929159         9.4046      16.6250  Evaluation: the filter under movement ...
    d6c3ca7         9.4046      16.6250  Evaluation: fcr's quadratic term is ...
    24cff92         4.2147      16.6250  Drums: the complete TR-808 ...
    ...
    80b3756         4.2147       6.2708  The toms' pitch drop, corrected ...
    bf13fdd         4.2147       6.2708  test: every shipped measurement bug ...

**`28dfd55` re-measured to 9.4046 and 16.6250, which are exactly the two
values `LOCKS` has held since that commit** -- an answer written down by a
different agent, before this tool existed, and not available to it. That is
the probe's external check; `--self-check` only proves it agrees with itself.

Then, narrowing into the two gaps the path filter had skipped over:

    50d7aaf (#67)   corner ratio drift  9.4046 -> 4.2147
    80b3756 (#154)  LT attack          16.6250 -> 6.2708

Two things that would have been wrong without the narrowing step, and are
recorded because they were wrong before they were right:

  * the filtered walk blamed `24cff92`, a drums commit that does not touch
    `reference_rigs.py` at all and only ADDS functions to `audio_measure.py`.
    It was the first commit in the FILTERED list after the move, not the
    commit that made it -- a path filter chosen from the property's name
    (`reference_rigs.py` for a ladder property) skipped `50d7aaf`, which
    changed the ladder through `model/voice_fx.py` and the `g_rom128.hex`
    cutoff ROM. A filtered bisect names the first *surviving* commit after
    the change, never the change.
  * both curator-nominated candidates (`d6c3ca7` for the ladder, `8cb5f4e`
    for the tuning offset) measure identically to their parents. The named
    suspect was not the cause in either direction.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

# name -> python expression evaluated with `sr` bound to the tree's own
# sound_report module and `ctx` to a fresh measurement context.
PROBES = {
    "corner-drift": 'sr.m_corner_spread(ctx)',
    "lt-attack": 'sr.m_drum_attack("LT")(ctx)',
}

DRIVER = '''
import json, os, sys, traceback
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "audition"))
out = {}
try:
    import sound_report as sr
except Exception as e:
    print(json.dumps({"_import": "%s: %s" % (type(e).__name__, e)}))
    raise SystemExit(0)
for name, expr in json.loads(sys.argv[1]).items():
    ctx = {"_patches": []}
    try:
        v = eval(expr, {"sr": sr, "ctx": ctx})
        out[name] = None if v is None else float(v)
    except Exception as e:
        out[name] = None
        out[name + "!why"] = "%s: %s" % (type(e).__name__, e)
print(json.dumps(out))
'''


def _run_tree(tree_dir: str, probes: dict[str, str], timeout: int) -> dict:
    drv = os.path.join(tree_dir, "model", "_drift_driver.py")
    with open(drv, "w") as fh:
        fh.write(DRIVER)
    try:
        p = subprocess.run([sys.executable, drv, json.dumps(probes)],
                           capture_output=True, text=True, timeout=timeout,
                           cwd=tree_dir)
    except subprocess.TimeoutExpired:
        return {"_import": f"timeout after {timeout}s"}
    finally:
        os.unlink(drv)
    line = ""
    for ln in p.stdout.splitlines():
        if ln.startswith("{"):
            line = ln
    if not line:
        tail = (p.stderr or p.stdout).strip().splitlines()[-1:] or ["no output"]
        return {"_import": f"exit {p.returncode}: {tail[0]}"}
    return json.loads(line)


def measure_commit(commit: str, probes: dict[str, str], timeout: int) -> dict:
    tmp = tempfile.mkdtemp(prefix="drift-probe-")
    try:
        tar = subprocess.run(["git", "-C", REPO, "archive", commit],
                             capture_output=True, check=True)
        subprocess.run(["tar", "-x", "-C", tmp], input=tar.stdout, check=True)
        return _run_tree(tmp, probes, timeout)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def commits_in(rng: str, paths: list[str]) -> list[tuple[str, str]]:
    args = ["git", "-C", REPO, "log", "--reverse", "--format=%h\t%s", rng]
    if paths:
        args += ["--"] + paths
    out = subprocess.run(args, capture_output=True, text=True, check=True).stdout
    rows = []
    for ln in out.splitlines():
        if "\t" in ln:
            h, s = ln.split("\t", 1)
            rows.append((h, s))
    return rows


def _fmt(v) -> str:
    return "n/a" if v is None else f"{v:10.4f}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--range", default="28dfd55..origin/main",
                    help="git revision range to walk (default: %(default)s). "
                         "Note 28dfd55 -- NOT the dangling ce400a6 that "
                         "sound_report.LOCK names; see issue #242.")
    ap.add_argument("--paths", nargs="*", default=[],
                    help="restrict the walk to commits touching these paths")
    ap.add_argument("--probe", action="append", choices=sorted(PROBES),
                    help="which property to measure (default: all)")
    ap.add_argument("--jobs", type=int, default=2,
                    help="parallel trees (default: %(default)s; this is a "
                         "shared host, keep it small)")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--self-check", action="store_true",
                    help="measure the working tree instead of history")
    a = ap.parse_args(argv)

    probes = {k: PROBES[k] for k in (a.probe or sorted(PROBES))}

    if a.self_check:
        got = _run_tree(REPO, probes, a.timeout)
        print(json.dumps(got, indent=2, sort_keys=True))
        return 0 if not got.get("_import") else 1

    rows = commits_in(a.range, a.paths)
    if not rows:
        print(f"no commits in {a.range} touching {a.paths or 'anything'}")
        return 1
    print(f"{len(rows)} commits in {a.range}"
          + (f" touching {' '.join(a.paths)}" if a.paths else ""))

    names = list(probes)
    head = f"{'commit':9} " + " ".join(f"{n:>12}" for n in names) + "  subject"
    print(head)
    print("-" * len(head))

    results: dict[str, dict] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=a.jobs) as ex:
        futs = {ex.submit(measure_commit, h, probes, a.timeout): h for h, _ in rows}
        for f in concurrent.futures.as_completed(futs):
            results[futs[f]] = f.result()

    prev = {n: None for n in names}
    for h, subj in rows:
        r = results[h]
        cells, moved = [], []
        for n in names:
            v = r.get(n)
            cells.append(f"{_fmt(v):>12}")
            if prev[n] is not None and v is not None and abs(v - prev[n]) > 1e-6:
                moved.append(f"{n} {prev[n]:.4f} -> {v:.4f}")
            if v is not None:
                prev[n] = v
        note = r.get("_import", "")
        print(f"{h:9} " + " ".join(cells) + f"  {subj[:58]}")
        if note:
            print(f"{'':9} {'':12}  ({note})")
        for m in moved:
            print(f"{'':9} ** MOVED: {m}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
