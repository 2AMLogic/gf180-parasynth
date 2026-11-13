#!/usr/bin/env python3
"""How many discrimination rows fall under the jit.dphase_ar1 floor (#768).

    tools/dphase_floor_census.py --refs /tmp/tr808-ref
    tools/dphase_floor_census.py --refs /tmp/tr808-ref --json /tmp/census.json

THE QUESTION. #763 put a physical floor under `jit.dphase_ar1.seg0`: below
`JIT_DPHASE_FLOOR_PPM` ppm of phase scatter the column REFUSES instead of
reporting the lag-1 autocorrelation of numerical noise. One of its acceptance
criteria -- how many rows of the published sixteen-voice run fall under that
floor, on the 44.1 kHz reference side and our 48 kHz render side SEPARATELY --
was REFUSED for want of the corpus. This answers it.

WHY THE SIDES ARE COUNTED SEPARATELY AND WHAT WOULD BE DAMNING. Our renders
come from a phase accumulator, which is the "no real period instability" case
by construction, so the render side is predicted to sit mostly under the floor.
If a large share of render rows were under it while reference rows were not,
then before #763 this column was partly separating the two sides on numerical
noise and `jit.stability`'s +0.008 attribution is partly an artefact. That is
a prediction, and the point of this file is that it is cheap to check and was
being argued about instead.

WHAT IS NOT COUNTED, AND THIS IS THE WHOLE DESIGN. Not `value == 0`.
`jitter_features` zeroes EVERY `jit.*` column when the window does not hold
JIT_MIN_CYCLES of the winning period, and zeroes this one alone when the floor
refuses it, so a row that was already invalid before #763 existed reads as a
zero and would be credited to the floor. The count comes from
`discrimination_features.dphase_row_state`, which reads the refusal decision
itself and separates the floor leg (`phasejit_ppm <= floor`) from the two
pre-existing divide-by-zero legs and from an invalid row. Its control --
including the inverted comparison, shown red -- is
`test_the_floor_census_counts_a_refusal_from_the_decision_not_the_zero`.

NO CLASSIFIER RUNS HERE. The counts need `jitter_features` on each clip and
nothing else, so this is a fraction of `model/discrimination_run.py`'s cost
and answers the first criterion on its own. The `jit.stability` A/B is the
separate, heavier half: two `discrimination_run.py` invocations at ONE commit
differing only in `--dphase-floor-ppm`, repeated over `--seed` for the spread.

PRECONDITIONS, ASSERTED AND REFUSED RATHER THAN ASSUMED. A census that answers
when it cannot is worse than one that is absent, because its output looks
exactly like data. Five are checked before any number is printed:

  1. the corpus resolves and holds WAVs at all;
  2. it holds EXP_N_WAV of them;
  3. its digest is EXPECT_CORPUS_SHA256 -- a different digest is a different
     corpus, and the counts would not be about the published run;
  4. the clip index hashes to EXPECT_SPLIT_HASH, so the rows counted are the
     published run's rows;
  5. every reference clip reads 44100 Hz and every render 48000 Hz.

(5) is there because it is the one thing (1)-(4) cannot catch and the whole
question is about a rate pair: a byte-identical corpus resampled in place
would satisfy every hash above while making "the 44.1 kHz side" a lie. That is
this guard's defeating input, and it is the reason the rates are read from the
data rather than taken from the filename.

Exit 0 answered, 2 REFUSED -- this repository's convention, where 2 means
"could not answer", which is not the same as "no problem".
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "audition"))

from model.discrimination_features import (  # noqa: E402
    JIT_DPHASE_FLOOR_PPM, ROW_FLOOR, ROW_INVALID, ROW_STATES, census_counts,
    dphase_row_state,
)

SR_REF, SR_RENDER = 44100, 48000

#: Read from docs/discrimination.md, NOT re-measured here -- this host has no
#: corpus, which is why #768 exists. Line 1490 of that file: 116 WAVs, digest
#: over sorted relative path then bytes (`model/condition_boundary.provenance`
#: computes it the same way, so the definition is shipped code rather than a
#: third one invented here). The OTHER digest quoted on that page,
#: e3ad2d77a79cda4a, is the same corpus under a walk-order-dependent
#: definition; it is deliberately not the one pinned.
EXP_N_WAV = 116
EXPECT_CORPUS_SHA256 = "6e6af82198eb5b46"
#: docs/discrimination.md line 65, the sixteen-sound split.
EXPECT_SPLIT_HASH = "ab381e78f9f4cc41"


# The remediation half of the missing-corpus refusal. Exported as a constant so
# the control in tools/test_dphase_floor_census.py can assert the refusal
# carries it WITHOUT spelling the corpus directory name itself: that literal in
# a module outside run_case is what tools/test_corpus_path_single_source.py
# exists to flag. Anchoring the control here instead also means the message and
# the assertion cannot drift apart.
CORPUS_HOWTO = (
    "The corpus is the public "
    "tidalcycles/sounds-tr808-fischer at 85fbecf: "
    "`git clone --depth 1 https://github.com/tidalcycles/"
    "sounds-tr808-fischer /tmp/tr808-ref`, then --refs that path "
    "(its ROOT, the directory holding bd8/, not bd8 itself)"
)


class Refused(Exception):
    """The census could not answer. Never reported as a count."""


# ---------------------------------------------------------------------------
def corpus_digest(refdir: str) -> tuple[str, int]:
    """(sha256[:16], n_wav), over sorted relative path then bytes. The same
    definition as `model/condition_boundary.provenance`, so a mismatch here
    and a match there would be a real disagreement rather than two spellings
    of a digest."""
    h = hashlib.sha256()
    wavs = []
    for root, _, fs in os.walk(refdir):
        for f in fs:
            if f.lower().endswith(".wav"):
                wavs.append(os.path.relpath(os.path.join(root, f), refdir))
    for rel in sorted(wavs):
        h.update(rel.encode())
        h.update((Path(refdir) / rel).read_bytes())
    return h.hexdigest()[:16], len(wavs)


def check_corpus(refdir: str, expect_sha: str, expect_split: str) -> dict:
    """Preconditions 1-4. Raises Refused with what was wanted and what was
    found, because "the corpus is wrong" is not an actionable message."""
    import test_discrimination as td
    from discrimination_run import split_hash

    p = Path(refdir)
    if not p.is_dir():
        raise Refused(f"{refdir} is not a directory. {CORPUS_HOWTO}")
    sha, n = corpus_digest(refdir)
    if n == 0:
        raise Refused(f"{refdir} holds no .WAV files -- wrong directory, or an "
                      "incomplete clone")
    if n != EXP_N_WAV:
        raise Refused(f"{refdir} holds {n} WAVs, not {EXP_N_WAV}: this is not the "
                      "corpus the published run used")
    if sha != expect_sha:
        raise Refused(
            f"corpus digest {sha}, expected {expect_sha} ({EXP_N_WAV} files, "
            "sorted relative path then bytes). A different corpus answers a "
            "different question; pass --expect-corpus-sha256 to state the one "
            "you mean, and say so wherever the counts are quoted")
    refs = td.ref_clips(refdir, include_unmodelled=True)
    got_split = split_hash(refs)
    if got_split != expect_split:
        raise Refused(
            f"split hash {got_split}, expected {expect_split}: the clip index "
            "is not the published sixteen-sound split, so these would not be "
            "that run's rows")
    return dict(refdir=str(p), corpus_sha256=sha, n_wav=n, split_hash=got_split,
                n_clips=len(refs))


# ---------------------------------------------------------------------------
def _ref_one(job):
    """One reference clip: read, condition exactly as the run does, classify."""
    import test_discrimination as td
    voice, knobs, path, floor = job
    x, sr = td.read_wav(path)
    if sr != SR_REF:
        return ("REFUSED", f"{path} reads {sr} Hz, not {SR_REF}")
    return ("ref", voice, knobs,
            dphase_row_state(td.condition(x, sr, True), sr, floor)[0])


def _render_one(job):
    import test_discrimination as td
    voice, knobs, laws, arm, floor = job
    x, sr = td.render(voice, knobs, laws, arm)
    if sr != SR_RENDER:
        return ("REFUSED", f"the {voice} render is {sr} Hz, not {SR_RENDER}")
    return ("render", voice, knobs,
            dphase_row_state(td.condition(x, sr, True), sr, floor)[0])


def census(refdir: str, floor: float | None, jobs: int, arm: str = "ours") -> dict:
    """Both sides, conditioned the way `discrimination_run._featurise`
    conditions them (`condition(x, sr, level_match=True)`) -- a census of a
    differently-conditioned signal would be a census of different rows."""
    from concurrent.futures import ProcessPoolExecutor

    import test_discrimination as td

    refs = td.ref_clips(refdir, include_unmodelled=True)
    laws = td.fit_laws(refdir, all_sounds=True)
    vs = td.ARM_VOICES.get(arm)
    work = [(c.voice, c.knobs, c.path, floor) for c in refs]
    rwork = [(c.voice, c.knobs, laws, arm, floor) for c in refs
             if vs is None or c.voice in vs]

    rows = []
    with ProcessPoolExecutor(max_workers=jobs) as ex:
        for r in ex.map(_ref_one, work, chunksize=4):
            rows.append(r)
        for r in ex.map(_render_one, rwork, chunksize=4):
            rows.append(r)
    bad = [r[1] for r in rows if r[0] == "REFUSED"]
    if bad:
        raise Refused(f"{len(bad)} clip(s) at the wrong sample rate; first: {bad[0]}")

    out = {}
    for side in ("ref", "render"):
        states = [r[3] for r in rows if r[0] == side]
        out[side] = census_counts(states)
        out[side]["per_voice"] = {
            v: census_counts([r[3] for r in rows if r[0] == side and r[1] == v])
            for v in sorted({r[1] for r in rows if r[0] == side})}
    out["arm"] = arm
    out["floor_ppm"] = JIT_DPHASE_FLOOR_PPM if floor is None else float(floor)
    out["floor_is_shipped"] = floor is None
    return out


# ---------------------------------------------------------------------------
def report(pre: dict, res: dict, out) -> None:
    def git(*a):
        try:
            return subprocess.check_output(["git", "-C", str(ROOT), *a],
                                           text=True).strip()
        except Exception:                                    # pragma: no cover
            return "?"
    print("How many rows of the sixteen-voice discrimination set fall under the",
          file=out)
    print("jit.dphase_ar1.seg0 floor, per side. #768.", file=out)
    print(f"commit {git('rev-parse', 'HEAD')[:12]}"
          f"{' DIRTY' if git('status', '--porcelain') else ''}; "
          f"numpy {np.__version__}", file=out)
    print(f"corpus {pre['refdir']}: {pre['n_wav']} WAVs, sha256 "
          f"{pre['corpus_sha256']}, split hash {pre['split_hash']}", file=out)
    which = (" (shipped JIT_DPHASE_FLOOR_PPM)" if res["floor_is_shipped"]
             else " (OVERRIDDEN)")
    print(f"floor {res['floor_ppm']:g} ppm{which}; arm {res['arm']}", file=out)
    print(file=out)
    print("A row is counted from the REFUSAL DECISION, never from a zero value:", file=out)
    print("  reported            the column answered", file=out)
    print("  floor_refused       phase scatter <= the floor -- what #763 added", file=out)
    print("  degenerate_refused  the pre-#763 divide-by-zero legs, NOT the floor", file=out)
    print("  row_invalid         the whole row refused, jit.valid = 0, before", file=out)
    print("                      the floor is reached at all", file=out)
    print(file=out)
    print(f"{'side':8} {'rate':>7} " + " ".join(f"{k:>19}" for k in ROW_STATES)
          + f" {'total':>6}", file=out)
    for side, sr in (("ref", SR_REF), ("render", SR_RENDER)):
        c = res[side]
        print(f"{side:8} {sr:>7} " + " ".join(f"{c[k]:>19d}" for k in ROW_STATES)
              + f" {c['total']:>6d}", file=out)
    print(file=out)
    for side in ("ref", "render"):
        c = res[side]
        print(f"{side} side: {c[ROW_FLOOR]} / {c['total']} refused by the floor, "
              f"{c[ROW_INVALID]} / {c['total']} already invalid", file=out)
    print(file=out)
    print("per voice (floor_refused / total), both sides", file=out)
    voices = sorted(set(res["ref"]["per_voice"]) | set(res["render"]["per_voice"]))
    print(f"{'voice':6} {'ref':>12} {'render':>12}", file=out)
    for v in voices:
        r = res["ref"]["per_voice"].get(v)
        o = res["render"]["per_voice"].get(v)
        f = lambda c: "-" if c is None else f"{c[ROW_FLOOR]}/{c['total']}"
        print(f"{v:6} {f(r):>12} {f(o):>12}", file=out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sys.path.insert(0, str(ROOT / "tools"))
    import run_case
    ap.add_argument("--refs", default=str(run_case.configured_refs()))
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 4) - 1))
    ap.add_argument("--arm", default="ours")
    ap.add_argument("--dphase-floor-ppm", type=float, default=None,
                    help="override the floor (0 = the pre-#763 behaviour). "
                         "Omitted = the shipped JIT_DPHASE_FLOOR_PPM")
    ap.add_argument("--expect-corpus-sha256", default=EXPECT_CORPUS_SHA256)
    ap.add_argument("--expect-split-hash", default=EXPECT_SPLIT_HASH)
    ap.add_argument("-o", "--out", type=Path, default=None)
    ap.add_argument("--json", type=Path, default=None)
    a = ap.parse_args(argv)
    try:
        pre = check_corpus(a.refs, a.expect_corpus_sha256, a.expect_split_hash)
        res = census(a.refs, a.dphase_floor_ppm, a.jobs, a.arm)
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    if a.json:
        a.json.write_text(json.dumps({"corpus": pre, "census": res}, indent=2))
    if a.out:
        with a.out.open("w") as fh:
            report(pre, res, fh)
        print(f"wrote {a.out}")
    else:
        report(pre, res, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
