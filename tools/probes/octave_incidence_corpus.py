#!/usr/bin/env python3
"""How many corpus clips trip dominant_period's rate-pair disagreement? #778 AC 5.

THE QUESTION. #774's reproducing stimuli were synthetic squares (850/875/880 Hz,
3 of the sweep's 88 pairs). Nobody had checked whether any REAL corpus clip
trips the same condition, so neither "#774 moved the published importances"
nor "#774 is irrelevant to them" was a measured statement, and a re-derivation
of the jit.* / mpd.* figures could not be quoted as either. This probe turns
that assumption into a count on the run this issue already pays for.

METHOD. Every clip the discrimination run reads (`test_discrimination.ref_clips`
over the corpus, the suite's own `condition(..., level_match=True)`, WINDOW_S
cut) is handed to `fold_rate_sweep.octave_refused` twice -- at its native
44.1 kHz and at the `discrimination_features._resample` rendition at 48 kHz.
The predicate is IMPORTED, not restated, so this counter cannot drift from the
sweep's own definition of a disagreement -- the same rule
`tools/probes/octave_margin_sweep.py` states for its dial.

THREE OUTCOMES, DISTINCT ON PURPOSE:

  agree            both rates pick a period and they match within
                   OCTAVE_TOL_REL (5 % of the period, the sweep's resolution)
  DISAGREE         both rates pick and the picks differ -- the #774 condition
  no-pick          `dominant_period` returns 0 on either side (the
                   JIT_MIN_CYCLES gate): no measurement possible on this clip.
                   Counted, never folded into either of the above.

APPARATUS PRECONDITIONS, ASSERTED HERE RATHER THAN ASSUMED (REFUSED, not
reported): the corpus is present at the configured refs path, its git HEAD is
the pinned commit, and every clip reads at 44.1 kHz -- the corpus is a 44.1 kHz
recording and a corpus at any other rate makes the 44.1-vs-48 question
ill-posed. On any refusal this exits 3, prints no counts and writes no JSON.

THE SHIPPED CODE IS WHAT IS COUNTED. `octave_margin` defaults to None, which
is `dominant_period`'s shipped dial; `--margin 0` reproduces the bare argmax
that shipped before #774, and exists so the panel can be re-counted against
the defect as well as against the repair.

    tools/probes/octave_incidence_corpus.py                      # shipped code
    tools/probes/octave_incidence_corpus.py --margin 0           # pre-#774 code
    tools/probes/octave_incidence_corpus.py --sounds 8           # published set
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "probes"))

import fold_rate_sweep as FRS                           # noqa: E402
from model import discrimination_features as D          # noqa: E402
from model import test_discrimination as td             # noqa: E402
from tools import run_case                              # noqa: E402

#: The corpus revision every published figure here was derived from, the same
#: pin `tools/clap_d12a_probe.py` refuses on. A corpus at any other revision is
#: a different set of recordings, not this one.
PINNED_COMMIT = "85fbecf"
SR_CORPUS, SR_RENDER = FRS.SR_A, FRS.SR_B


def repo_state() -> dict:
    def git(*a: str) -> str:
        try:
            return subprocess.check_output(["git", "-C", str(ROOT), *a],
                                           text=True).strip()
        except Exception:
            return "?"
    return {"head": git("rev-parse", "HEAD"),
            "dirty_paths": [ln for ln in git("status", "--porcelain").splitlines() if ln]}


def classify_pair(a: np.ndarray, sr_a: int, b: np.ndarray, sr_b: int,
                  octave_margin: float | None = None) -> dict:
    """One clip at one rate pair, classified into the three outcomes above."""
    pa = D.dominant_period(a, sr_a, octave_margin=octave_margin)
    pb = D.dominant_period(b, sr_b, octave_margin=octave_margin)
    refused = FRS.octave_refused(a, sr_a, b, sr_b, octave_margin=octave_margin)
    if pa <= 0 or pb <= 0:
        label = "no-pick"
    elif refused:
        label = "disagree"
    else:
        label = "agree"
    return {"pa_samples": int(pa), "pb_samples": int(pb),
            # periods compared IN SECONDS, the unit octave_refused compares --
            # a sample ratio across two rates is meaningless (55 @ 48 kHz and
            # 51 @ 44.1 kHz are the same period).
            "ratio": round((pb / sr_b) / (pa / sr_a), 4)
            if pa > 0 and pb > 0 else None,
            "sweep_refused": int(refused), "label": label}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--refs", default=str(run_case.configured_refs()),
                    help="the unpacked sounds-tr808-fischer tree")
    ap.add_argument("--out", default="/tmp/octave-incidence-corpus.json")
    ap.add_argument("--sounds", choices=("8", "16"), default="16",
                    help="8 = the published study's voice set; 16 = every "
                         "sound the run reads")
    ap.add_argument("--margin", type=float, default=None,
                    help="override JIT_OCTAVE_MARGIN_PERMILLE; 0 = the bare "
                         "argmax that shipped before #774")
    ap.add_argument("--limit", type=int, default=None,
                    help="first N clips only (a smoke run; recorded in the JSON)")
    a = ap.parse_args(argv)

    refs = Path(a.refs).expanduser()
    if not refs.is_dir():
        print(f"REFUSED: corpus absent (no directory at {refs}; "
              f"set GF180_TR808_REFS)")
        return 3
    head = subprocess.run(["git", "-C", str(refs), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    if not head.startswith(PINNED_COMMIT):
        print(f"REFUSED: corpus at {head or 'unknown revision'}, "
              f"pinned {PINNED_COMMIT}")
        return 3
    clips = td.ref_clips(str(refs), include_unmodelled=(a.sounds == "16"))
    if not clips:
        print(f"REFUSED: no clips indexed under {refs}")
        return 3
    if a.limit is not None:
        clips = clips[:a.limit]

    rows = []
    for i, c in enumerate(clips):
        x, sr = td.read_wav(c.path)
        if sr != SR_CORPUS:
            print(f"REFUSED: {c.path} reads at {sr} Hz, corpus is "
                  f"{SR_CORPUS} Hz -- the 44.1-vs-48 question is ill-posed")
            return 3
        cond = td.condition(x, sr, level_match=True)
        other = D._resample(cond, sr, SR_RENDER)
        r = classify_pair(cond, sr, other, SR_RENDER, octave_margin=a.margin)
        rows.append({"voice": c.voice, "file": Path(c.path).name, **r})
        if (i + 1) % 16 == 0:
            print(f"  {i + 1}/{len(clips)} clips read")

    counts = {k: sum(r["label"] == k for r in rows)
              for k in ("agree", "disagree", "no-pick")}
    voices = sorted({r["voice"] for r in rows})
    print(f"\ndominant_period 44.1 kHz vs 48 kHz, {len(rows)} corpus clips, "
          f"margin={'shipped' if a.margin is None else a.margin}\n")
    print(f"{'voice':>6} {'agree':>7} {'DISAGREE':>9} {'no-pick':>8}")
    for v in voices:
        vr = [r for r in rows if r["voice"] == v]
        print(f"{v:>6} {sum(r['label'] == 'agree' for r in vr):>7} "
              f"{sum(r['label'] == 'disagree' for r in vr):>9} "
              f"{sum(r['label'] == 'no-pick' for r in vr):>8}")
    print(f"{'TOTAL':>6} {counts['agree']:>7} {counts['disagree']:>9} "
          f"{counts['no-pick']:>8}")
    for r in rows:
        if r["label"] == "disagree":
            print(f"  DISAGREE: {r['voice']:>3} {r['file']}: "
                  f"{r['pa_samples']} vs {r['pb_samples']} samples "
                  f"(ratio {r['ratio']})")

    Path(a.out).write_text(json.dumps({
        "probe": "tools/probes/octave_incidence_corpus.py",
        "repo": repo_state(), "corpus_head": head, "refs": str(refs),
        "sounds": a.sounds, "margin": a.margin, "limit": a.limit,
        "window_s": td.WINDOW_S, "octave_tol_rel": FRS.OCTAVE_TOL_REL,
        "counts": counts, "rows": rows}, indent=1))
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
