#!/usr/bin/env python3
"""Tolerance bands for the two metrics promoted by #138, and why none is live.

`tools/run_case.TOLERANCE_POLICY` (and `f0_discrimination_tolerance`, #127)
derive a tolerance from TWO measured bounds that point opposite ways:

    FLOOR    the same machine, recorded twice at one setting, differs by this
             much. A tolerance below it convicts the 808 of not being itself.
    CEILING  the machine's own knobs move the metric this much. A tolerance
             above it cannot tell two settings apart.

    tolerance = sqrt(floor * ceiling), the point equidistant from both failures.

The CEILING for every voice with a knob is measurable from the Fischer corpus
and `tools/measure_promoted_bands.py` measures it. The FLOOR is not, for any
voice, on any metric this module covers: docs/discrimination.md 4 ("What the
corpus cannot give") records that Fischer kept ONE take per setting, and
docs/bd-repeatability-measurement.md found the 808-From-Mars set has no take
axis either; its only repeat data is one BD session pair on other metrics.
`MACHINE_FLOOR` is therefore EMPTY, and `band_tolerance` REFUSES for every
(metric, voice) rather than inventing a floor. A tolerance picked by hand here
would be exactly the 10 % that #127 showed could not fail a kick rendered
anywhere in the voice's own range.

Consequence, stated where it is read: these two metrics are NOT registered in
`run_case.DRUM_PLAN` and are not on the board. Registering them with a refusing
tolerance would turn every tuned-drum case invalid. They are registered here
the day a floor is entered in `MACHINE_FLOOR`, with its source.
"""
from __future__ import annotations

import math

METRICS = ("lowband_level_db", "dominant_period_ms")

#: (metric, voice) -> dict(value=<floor in the metric's units>, source=<where
#: measured>). EMPTY: no repeat-take recording of any voice exists here for
#: either metric. Entering a number without a recording behind it is the
#: failure this table exists to prevent.
MACHINE_FLOOR: dict = {}

BASIS = "measured band (floor from repeat takes, ceiling from knob travel)"


def band_tolerance(metric: str, voice: str, ceiling: float | None):
    """(tolerance, basis) in the shape of `run_case` tolerance rules; the
    tolerance is NaN, and the basis says why, when no usable one exists.

    Refuses when: the metric is unknown; no machine floor is recorded; no
    ceiling was measured (a voice with no knob has no travel); or the floor does
    not sit below the ceiling, in which case no value both spares the machine
    its own repeat spread and separates two of its settings."""
    if metric not in METRICS:
        raise KeyError(f"unknown metric {metric!r}")
    fl = MACHINE_FLOOR.get((metric, voice))
    if fl is None:
        return math.nan, f"{BASIS} (REFUSED: no repeat-take floor measured for {voice} {metric})"
    floor = float(fl["value"])
    if ceiling is None or not math.isfinite(ceiling):
        return math.nan, f"{BASIS} (REFUSED: no knob travel measured for {voice} {metric})"
    if not (0.0 < floor < ceiling):
        return math.nan, (f"{BASIS} (REFUSED: {voice} {metric} floor {floor} does not sit "
                          f"below its knob travel {ceiling})")
    return math.sqrt(floor * ceiling), BASIS
