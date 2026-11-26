"""Qualification controls for a cutoff sweep whose only output is a start click."""

import numpy as np
import pytest

import rig_qualification as rq
from test_rig_qualification import _SynthRig


@pytest.mark.parametrize("cutoff, expected", [
    ("normal", rq.PASS),
    ("dead", rq.FAIL),
    ("click", rq.REFUSED),
    ("one missing centroid", rq.REFUSED),
])
def test_cutoff_verdict_requires_a_measurable_centroid(cutoff, expected):
    rig = _SynthRig(cutoff="dead" if cutoff == "dead" else "normal")
    if expected == rq.REFUSED:
        click = np.zeros(24000)
        click[0] = 0.4
        # Windowing removes the only nonzero sample of this finite buffer.
        assert rq.check_sounding(click).ok
        assert not np.any(click * np.hanning(len(click)))
        normal_render = rig.render_cutoff

        def render(knob):
            if cutoff == "click" or knob == 0.5:
                return click.copy()
            return normal_render(knob)

        rig.render_cutoff = render

    qualification = rig.qualify()
    check = next(row for row in qualification.checks if row.name == "filter causality")
    assert all(row.ok for row in qualification.checks if row is not check)
    assert check.outcome == expected, (qualification.verdict, check.why)
    assert qualification.qualified == (expected == rq.PASS)
