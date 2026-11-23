#!/usr/bin/env python3
"""How far did the #1129 normalisation move the within-rate numbers?

    .venv/bin/python tools/probes/trajectory_within_rate_drift.py
    .venv/bin/python -m pytest tools/probes/trajectory_within_rate_drift.py -q

#1129 changed `model/discrimination_trajectory.trajectory()` so a cell is a
band's share of the *clip's* energy. The old code scaled each window by a
CONSTANT, `len(seg)/sum(win**2)`; the new code scales it by the window's own
unwindowed energy over its windowed spectral total, `seg_e/wt`. For a
stationary window the two agree and the change is the single constant
`10*log10(n_seg/2)`. For a transient inside the window they do not, so the
change is NOT a pure offset and a within-rate ours-minus-machine difference
moves by an amount that depends on the clip.

`docs/excitation-energy-localisation.md` and `docs/discrimination-trajectory.txt`
quote numbers for this. They were first stated from two near-steady clips and
came out too small (0.34 dB max against 0.52 dB on a decaying drum pair), so
this file is the instrument behind the figures now published, with clip
definitions, and it states which clip class each figure bounds.

The old estimator is read from the merge base with `git show`, not re-typed,
so the comparison cannot drift from what actually shipped.

Wrong-then-right record: the first within-rate bound (0.34 dB max, 0.15 dB
mean) was measured on `tone`/`tone2`-class clips only. It was wrong as a bound
for the thing the map measures (drum hits) and was caught by the reviewer
re-running it on a transient pair.
"""
from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys
import tempfile

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "model"))
import discrimination_trajectory as new  # noqa: E402

# Last commit before #1129 changed trajectory()'s normalisation.
MERGE_BASE = "705177743c10686e24065e98cd4cc8cdbea2f6b7"
FLOOR = new.FLOOR_DB
RATES = (44100, 48000)
# Known-independent answer: in the stationary limit new - old = 10*log10(n_seg/2).
CLIP_S = 0.24


def load_old(rev: str = MERGE_BASE):
    """The pre-#1129 module, exported from git. REFUSES if it is unavailable
    (shallow clone, unknown rev): a missing baseline must not read as 'no drift'."""
    r = subprocess.run(
        ["git", "-C", str(ROOT), "show", f"{rev}:model/discrimination_trajectory.py"],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"REFUSED: cannot read merge-base estimator {rev[:8]}: {r.stderr.strip()}")
    d = tempfile.mkdtemp(prefix="dtj-old-")
    p = pathlib.Path(d) / "discrimination_trajectory_old.py"
    p.write_text(r.stdout)
    spec = importlib.util.spec_from_file_location("dtj_old", p)
    m = importlib.util.module_from_spec(spec)
    sys.modules["dtj_old"] = m
    spec.loader.exec_module(m)
    return m


def clip(sr: int, kind: str, rng) -> np.ndarray:
    n = int(round(CLIP_S * sr))
    t = np.arange(n) / sr
    if kind == "drum":       # noisy attack + decaying tone: strongly non-stationary
        return (np.exp(-t / 0.02) * rng.standard_normal(n) * 0.5
                + np.exp(-t / 0.08) * np.sin(2 * np.pi * 62.0 * t))
    if kind == "drum2":      # slower attack, brighter: a different transient
        return (np.exp(-t / 0.035) * rng.standard_normal(n) * 0.35
                + np.exp(-t / 0.06) * np.sin(2 * np.pi * 70.0 * t)
                + 0.1 * np.exp(-t / 0.01) * np.sin(2 * np.pi * 900.0 * t))
    if kind == "tone":      # near-steady
        return np.sin(2 * np.pi * 400.0 * t) * (1 + 0.3 * np.sin(2 * np.pi * 7 * t))
    if kind == "tone2":
        return np.sin(2 * np.pi * 410.0 * t) * np.exp(-t / 0.15)
    raise ValueError(kind)


def _live(mods, xs, sr):
    """Cells unclamped under EVERY estimator for EVERY clip."""
    live = None
    for m in mods:
        for x in xs:
            c = m.trajectory(x, sr)[0] > FLOOR + 1e-9
            live = c if live is None else live & c
    return live


def within_rate_moves(old, pairs, rates=RATES, seed=11):
    """{(sr, pair): |d_new - d_old| on cells live in both}, d = a - b at one rate."""
    rng = np.random.default_rng(seed)
    out = {}
    for sr in rates:
        for a, b in pairs:
            xa, xb = clip(sr, a, rng), clip(sr, b, rng)
            dn = new.trajectory(xa, sr)[0] - new.trajectory(xb, sr)[0]
            do = old.trajectory(xa, sr)[0] - old.trajectory(xb, sr)[0]
            live = _live((new, old), (xa, xb), sr)
            out[(sr, f"{a}/{b}")] = np.abs(dn - do)[live]
    return out


def absolute_offsets(old, kind, rates=RATES, seed=11):
    """{sr: (mean, min, max)} of new - old on unclamped cells of one clip."""
    rng = np.random.default_rng(seed)
    out = {}
    for sr in rates:
        x = clip(sr, kind, rng)
        A, B = new.trajectory(x, sr)[0], old.trajectory(x, sr)[0]
        live = (A > FLOOR + 1e-9) & (B > FLOOR + 1e-9)
        d = (A - B)[live]
        out[sr] = (float(d.mean()), float(d.min()), float(d.max()), int(live.sum()))
    return out


def stationary_offset_db(sr: int) -> float:
    """The stationary-limit offset 10*log10(n_seg/2), known without running either estimator."""
    n_seg = int(round(CLIP_S * sr)) // new.N_WIN
    return -10.0 * np.log10(n_seg / 2.0)


def report() -> None:
    old = load_old()
    for label, pairs in (("TRANSIENT (decaying noise + tone)", (("drum", "drum2"),)),
                         ("NEAR-STEADY tones", (("tone", "tone2"),))):
        mv = within_rate_moves(old, pairs)
        print(f"{label}: within-rate |move| of an a-minus-b difference")
        for (sr, pr), v in mv.items():
            print(f"  {sr:>6} {pr:>12} n={v.size:>4} max {v.max():.3f} dB  mean {v.mean():.3f} dB")
        allv = np.concatenate(list(mv.values()))
        print(f"  OVERALL max {allv.max():.3f} dB, mean {allv.mean():.3f} dB, n={allv.size}\n")
    print("absolute offset new - old on unclamped cells (drum clip):")
    for sr, (m, lo, hi, n) in absolute_offsets(old, "drum").items():
        print(f"  {sr}: mean {m:+.3f} dB, spread {lo:+.3f}..{hi:+.3f} (n={n}); "
              f"stationary limit {stationary_offset_db(sr):+.3f}")


# ---------------------------------------------------------------- self-tests

def test_the_old_estimator_is_the_merge_base_not_the_new_one():
    """Control: a probe that compared new with new would report zero drift and
    look like a pass. The two must differ by the ~28 dB unit change."""
    old = load_old()
    x = clip(44100, "tone", np.random.default_rng(0))
    d = new.trajectory(x, 44100)[0] - old.trajectory(x, 44100)[0]
    live = (new.trajectory(x, 44100)[0] > FLOOR + 1e-9) & (old.trajectory(x, 44100)[0] > FLOOR + 1e-9)
    assert live.any() and np.all(d[live] < -20.0), d[live][:5]


def test_injected_bug_new_against_itself_reads_zero_drift():
    """Control that must stay red-able: the harness reports exactly zero for
    new-vs-new, so a non-zero number in `report()` is the estimators, not it."""
    mv = within_rate_moves(new, (("drum", "drum2"),))
    assert all(float(v.max()) == 0.0 for v in mv.values() if v.size)


def test_a_stationary_window_moves_by_exactly_the_constant():
    """Independent known answer: for a stationary clip the offset is
    10*log10(n_seg/2) (-28.205 dB at 44.1 kHz, -28.573 at 48 kHz), per cell."""
    old = load_old()
    for sr in RATES:
        n = int(round(CLIP_S * sr)); t = np.arange(n) / sr
        x = np.sin(2 * np.pi * 400.0 * t)            # an exact-bin tone, same in every window
        A, B = new.trajectory(x, sr)[0], old.trajectory(x, sr)[0]
        live = (A > FLOOR + 1e-9) & (B > FLOOR + 1e-9)
        assert live.any()
        assert np.allclose((A - B)[live], stationary_offset_db(sr), atol=0.05), (sr, (A - B)[live])


def test_a_transient_clip_moves_more_than_a_steady_one():
    """The reason the bound is clip-dependent: it is why 0.34 dB (steady) was
    not a bound for drum hits. Asserted as the ordering, not a magic number."""
    old = load_old()
    t = np.concatenate(list(within_rate_moves(old, (("drum", "drum2"),), rates=(44100,)).values()))
    s = np.concatenate(list(within_rate_moves(old, (("tone", "tone2"),), rates=(44100,)).values()))
    assert t.max() > s.max(), (t.max(), s.max())


def test_refuses_when_the_baseline_is_unreadable():
    try:
        load_old("0" * 40)
    except SystemExit as e:
        assert "REFUSED" in str(e)
    else:
        raise AssertionError("an unreadable baseline must REFUSE, not report")


if __name__ == "__main__":
    report()
