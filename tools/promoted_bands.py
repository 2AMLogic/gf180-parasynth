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
and `tools/measure_promoted_bands.py` measures it.

THE FLOOR IS NOT A HAND-ENTERED NUMBER ANY MORE, AND THAT IS THIS MODULE'S
WHOLE CHANGE UNDER #138's SECOND INCREMENT. It is read from the output of the
harness that already produces floors -- `tools/measure_repeatability.py`, #111
-- which as of this commit registers both estimators in its own `metrics()`
plan. So the floor appears here the first time that harness is re-run on a
host that has the reference packs, with its provenance, and nobody has to
transcribe a number into a table. Until then `floor_for` finds no entry and
`band_tolerance` REFUSES, which is the state the committed
`docs/bd-repeatability-results.json` is in today (pinned by
`test_the_shipped_record_still_has_no_floor_for_either_metric`).

TWO GUARDS, BOTH TAKEN FROM `measure_repeatability.verdicts` RATHER THAN
INVENTED HERE:

* **Dominated by the apparatus.** `verdicts` calls a spread with
  `machine_over_estimator < 1` a reading of the apparatus, not of the TR-808.
  A floor below the estimator's own editing noise is refused for that reason;
  using it would set a tolerance from measurement jitter.
* **Floor below ceiling.** A floor at or above the knob travel leaves no value
  that both spares the machine its own spread and separates two of its
  settings. Refused, not clamped.

THE THIRD METRIC, `lowband_onset_db`, IS THE SAME RATIO OVER AN 80 ms WINDOW
FROM THE ONSET rather than the whole clip (#138's third increment). It is here
for the same reason the other two are: the floor can only come from the #111
harness, so it is registered there too. It does NOT inherit
`lowband_level_db`'s floor and must never be given it -- a floor measured on a
240 ms window is a floor for a different quantity, and the whole point of this
metric is that the two readings differ (by +9.17 dB on the closed-form case
`measure_promoted_bands.onset_cases` uses). `band_tolerance` keys on the metric
name, so the two cannot be crossed by accident.

AND ITS FLOOR CANNOT COME FROM THE #111 PAIR AT ALL, which is a sharper gap
than the other two have. The bass drum's 50 Hz fundamental sits 10 Hz above the
40 Hz band edge; an 80 ms Hann resolves 25 Hz, so
`promoted_measures.EDGE_LEAK_MAX` refuses the reading rather than reporting a
straddle (measured 0.20 against a 0.05 threshold). Clearing it needs a window of
200 ms or more, which is no longer an onset window. So `lowband_onset_db` needs
a second recording of a voice whose lines sit clear of both band edges -- the
same material gap as SD's, reached by a different route, and NOT something a
re-run of the existing harness can supply.

ONE VOICE, AND IT IS NOT ONE OF THE INTERESTING ONES. The only second
recording session reachable from this repository is the 808-From-Mars current
/ legacy BASS DRUM pair (`measure_repeatability.cross_session`); the Fischer
corpus kept one take per setting (`docs/discrimination.md` 4) and nominally
different "808" packs cross-correlate at 1.000. So a floor can only ever
appear for BD here, and BD is exactly the voice `lowband_level_db` SATURATES
on (knob travel 0.25 dB, `docs/promoted-bands-results.json`). Getting SD --
where the -4.86..+6.79 dB finding is -- onto the board needs a second SD
recording that does not exist yet. That is the remaining gap, stated here
because this is where a reader looks for it.

Consequence, stated where it is read: these two metrics are NOT registered in
`run_case.DRUM_PLAN` and are not on the board. Registering them with a
refusing tolerance would turn every tuned-drum case invalid.
"""
from __future__ import annotations

import json
import math
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent

METRICS = ("lowband_level_db", "lowband_onset_db", "dominant_period_ms")

#: The #111 harness's committed output. `measure_repeatability.py --all --json
#: docs/bd-repeatability-results.json` rewrites it; it needs the reference
#: packs (REFAUDIO_LOCAL or an ssh route) and REFUSES without them.
REPEATABILITY_RESULTS = ROOT / "docs" / "bd-repeatability-results.json"

#: The only voice with a second recording session. See the module docstring.
FLOOR_VOICE = "BD"

#: (metric, voice) -> dict(value=..., source=...). An EXPLICIT override, for a
#: floor measured somewhere other than the #111 harness. Empty, and an entry
#: without a recording behind it is the failure this table exists to prevent.
MACHINE_FLOOR: dict = {}

BASIS = "measured band (floor from repeat takes, ceiling from knob travel)"


def _load(doc):
    """`doc` as given, else the committed #111 record, else None."""
    if doc is not None:
        return doc
    try:
        return json.loads(REPEATABILITY_RESULTS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def harness_floor(metric: str, voice: str, doc: dict | None = None):
    """The #111 session-to-session spread of `metric`, or None with a reason.

    Returns `(entry, None)` or `(None, reason)`. `entry["value"]` is
    `abs_diff_median`, which is what `measure_repeatability.verdicts` already
    uses as the machine floor for every other metric -- not `abs_diff_max`,
    so one outlying pair cannot widen a tolerance."""
    if voice != FLOOR_VOICE:
        return None, (f"no second recording session exists for {voice}; the only "
                      f"repeat pair here is the {FLOOR_VOICE} one of #111")
    d = _load(doc)
    if not isinstance(d, dict):
        return None, f"{REPEATABILITY_RESULTS.name} is missing or unreadable"
    m = (d.get("session_to_session") or {}).get("metrics") or {}
    if metric not in m:
        return None, (f"{metric} has no session-to-session entry in "
                      f"{REPEATABILITY_RESULTS.name}; re-run "
                      f"tools/measure_repeatability.py on a host with the packs")
    floor = m[metric].get("abs_diff_median")
    if floor is None or not math.isfinite(float(floor)) or float(floor) <= 0.0:
        return None, f"{metric}'s session-to-session spread is not a positive number"
    floor = float(floor)
    # `verdicts`' own rule: a spread smaller than the estimator's editing noise
    # is a reading of the apparatus, not of the machine.
    noise = ((d.get("self_test") or {}).get("editing_noise") or {}).get(metric)
    span = None if noise is None else noise.get("span")
    if span is None:
        return None, (f"{metric} has no editing-noise span in "
                      f"{REPEATABILITY_RESULTS.name}; the floor cannot be shown "
                      f"to be the machine rather than the apparatus")
    if not floor > float(span):
        return None, (f"{metric}'s session spread {floor:g} does not exceed the "
                      f"estimator's own editing noise {float(span):g}: that number "
                      f"describes the apparatus, not the TR-808")
    return dict(value=floor, source=(f"{REPEATABILITY_RESULTS.name} "
                                     f"session_to_session.metrics.{metric}."
                                     f"abs_diff_median (#111)"),
                apparatus_span=float(span)), None


def floor_for(metric: str, voice: str, doc: dict | None = None):
    """`(entry, reason)`: the explicit override if one is recorded, else the
    #111 harness's floor, else None and why."""
    fl = MACHINE_FLOOR.get((metric, voice))
    if fl is not None:
        return dict(fl), None
    return harness_floor(metric, voice, doc)


def band_tolerance(metric: str, voice: str, ceiling: float | None,
                   doc: dict | None = None):
    """(tolerance, basis) in the shape of `run_case` tolerance rules; the
    tolerance is NaN, and the basis says why, when no usable one exists.

    Refuses when: the metric is unknown; no machine floor is available (see
    `floor_for`); no ceiling was measured (a voice with no knob has no
    travel); or the floor does not sit below the ceiling, in which case no
    value both spares the machine its own repeat spread and separates two of
    its settings."""
    if metric not in METRICS:
        raise KeyError(f"unknown metric {metric!r}")
    fl, why = floor_for(metric, voice, doc)
    if fl is None:
        return math.nan, f"{BASIS} (REFUSED: {why})"
    floor = float(fl["value"])
    if ceiling is None or not math.isfinite(ceiling):
        return math.nan, f"{BASIS} (REFUSED: no knob travel measured for {voice} {metric})"
    if not (0.0 < floor < ceiling):
        return math.nan, (f"{BASIS} (REFUSED: {voice} {metric} floor {floor} does not sit "
                          f"below its knob travel {ceiling})")
    return math.sqrt(floor * ceiling), f"{BASIS} [floor {floor:g} from {fl['source']}]"
