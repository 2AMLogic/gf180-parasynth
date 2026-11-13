#!/usr/bin/env python3
"""Is there a narrower `RATE_TOL_REL` at all? A feasibility probe for #775.

THIS IS NOT THE SWEEP #775 ASKS FOR. The sweep is a registered record under
`docs/sensitivity/` with a grid and a prediction committed before it runs
(`docs/sensitivity-sweeps.md`). This answers the prior question a curator
needed before enriching the issue: on the panel the shipped rate test
actually uses, does narrowing the dial trade anything measurable, and in
which direction? If the answer had been "the count is flat", the sweep would
have needed a different panel before it was worth writing.

Three measurements, all on the shipped module, nothing re-implemented except
the one-line tolerance `max()` -- `_rate_gaps` has no tolerance override to
call through, which is itself a finding this probe records (the sweep
instrument should add `tol_rel=None` beside the existing `taps=None`).

  [1] the headline: `jit.cyclecorr.seg0` steady vs 0.5 %-jittered, the
      effect, the jittered clip's own 44.1k->48k gap in the same column, and
      the tolerance the rate test applies to it.
  [2] the vector's width and unit breakdown, and on how many columns the
      RELATIVE term is the binding one rather than `RATE_TOL_ABS`.
  [3] the two-sided trade, as RATE_TOL_REL falls: how many columns go red on
      the SHIPPED fold (the cost -- a clean panel turning red) against how
      many go red on the integer fold #767 replaced (the benefit -- the
      honest injected control, `taps=-1`, which
      `test_the_integer_fold_this_replaced_is_still_red_on_this_panel`
      already uses).

Run:  python3 tools/probes/rate_tol_feasibility.py [repo-root]
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import time

CANDIDATES = (0.12, 0.10, 0.08, 0.06, 0.04, 0.03, 0.02, 0.0146, 0.01, 0.0)


def load(root: pathlib.Path):
    src = root / "model" / "discrimination_features.py"
    if not src.exists():                      # a wrong root is not a result
        raise SystemExit(f"REFUSED: no module at {src}")
    spec = importlib.util.spec_from_file_location("dfeat", src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def headline(m) -> None:
    sr = 44100
    read = lambda x, s=sr: dict(zip(*reversed(m.jitter_features(x, s))))  # noqa: E731
    col = "jit.cyclecorr.seg0"
    d = read(m._square(410.0, sr, jitter=0.0))
    j = read(m._square(410.0, sr, jitter=0.005, seed=3))
    j48 = read(m._resample(m._square(410.0, sr, jitter=0.005, seed=3), 44100, 48000), 48000)
    eff, gap = d[col] - j[col], abs(j[col] - j48[col])
    tol = max(m.RATE_TOL_REL * max(abs(j[col]), abs(j48[col])),
              m.RATE_TOL_ABS[m._unit_of(col)])
    print(f"[1] {col}: steady={d[col]:.4f} jittered={j[col]:.4f} "
          f"effect={eff:.4f} rate_gap={gap:.4f}")
    print(f"    tolerance applied to it = {tol:.4f} -> {tol / eff:.2f}x the effect, "
          f"{tol / max(gap, 1e-9):.0f}x the gap")


def width(m) -> None:
    v, names = m.extra_features(m._square(410.0, 44100, jitter=0.0), 44100)
    units: dict[str, int] = {}
    binds: dict[str, int] = {}
    for n, x in zip(names, v):
        u = m._unit_of(n)
        units[u] = units.get(u, 0) + 1
        if m.RATE_TOL_REL * abs(float(x)) > m.RATE_TOL_ABS[u]:
            binds[u] = binds.get(u, 0) + 1
    print(f"\n[2] width={len(names)} units={dict(sorted(units.items()))}")
    print(f"    the relative term binds on {sum(binds.values())}/{len(names)} columns "
          f"(steady 410 Hz square): {dict(sorted(binds.items()))}")


def _panel(m, taps):
    """Every (entry, column) pair of the SHIPPED rate panel as its gap and its
    peak, so the tolerance arithmetic can be re-run without re-running the
    features. `taps=-1` selects the integer fold #767 replaced."""
    rows = []
    for label, f0, jitter, seed, kind, build_sr in m.RATE_PANEL:
        a, sr_a, b, sr_b = m._rate_pair(label, f0, jitter, seed, kind, build_sr)
        va, names = m.extra_features(a, sr_a, taps)
        vb, _ = m.extra_features(b, sr_b, taps)
        rows += [(label, nm, m._unit_of(nm), abs(float(x - y)),
                  max(abs(float(x)), abs(float(y))))
                 for nm, x, y in zip(names, va, vb)]
    return rows


def trade(m) -> None:
    t0 = time.time()
    shipped, injected = _panel(m, None), _panel(m, -1)
    print(f"\n[3] two panel runs, {len(shipped)} (entry,column) pairs each, "
          f"{time.time() - t0:.1f}s. RATE_KNOWN_RED={m.RATE_KNOWN_RED}")

    def red(rows, tr, unit=None):
        return [r for r in rows if (unit is None or r[2] == unit)
                and r[3] > max(tr * r[4], m.RATE_TOL_ABS[r[2]])]

    print(f"    {'tol_rel':>7} | {'cost: clean panel red':>21} {'ratio':>6} | "
          f"{'benefit: integer fold red':>25} {'ratio':>6}")
    for tr in CANDIDATES:
        s, i = red(shipped, tr), red(injected, tr)
        mark = "  <- shipped" if tr == m.RATE_TOL_REL else ""
        print(f"    {tr:7} | {len(s):21d} {len(red(shipped, tr, 'ratio')):6d} | "
              f"{len(i):25d} {len(red(injected, tr, 'ratio')):6d}{mark}")

    reach = [r for r in shipped if r[3] > m.RATE_TOL_ABS[r[2]]]
    print(f"\n    {len(reach)} of {len(shipped)} pairs are over their ABSOLUTE floor at all, "
          f"so only those can be reached by tol_rel:")
    for r in sorted(reach, key=lambda r: -r[3] / r[4])[:12]:
        print(f"      crosses below tol_rel={r[3] / r[4]:.4f}  {r[0]:<18} "
              f"{r[1]:<26} unit={r[2]:<5} gap={r[3]:.4f} peak={r[4]:.4f}")


def main() -> int:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    m = load(root)
    import numpy as np
    import scipy
    print(f"{root}  python {sys.version.split()[0]} numpy {np.__version__} "
          f"scipy {scipy.__version__}")
    print(f"RATE_TOL_REL={m.RATE_TOL_REL} RATE_TOL_ABS={m.RATE_TOL_ABS}\n")
    headline(m)
    width(m)
    trade(m)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
