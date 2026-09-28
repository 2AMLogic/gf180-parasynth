#!/usr/bin/env python3
"""TEMPORARY -- the acceptance test for #404. This commit is meant to be RED.

#404's acceptance criterion is not "a `pytest tools/` step exists in the
workflow"; it is "a deliberately-failing test file added under tools/ turns CI
red with no further workflow edit". Those are different claims, and only the
second one is evidence, so this file is pushed once on the branch to make CI
answer it, read, and then removed in the next commit.

It has to fail the way a real test fails, not the way a broken file fails. An
import error or a syntax error would turn the job red having proved only that
Python rejects bad input -- the vacuous-control mistake rungs.yml's own
"wrong arithmetic, not just bad syntax" step was written against. So this file
imports cleanly, collects cleanly, and fails one arithmetic assertion.

The file is named test_zz_* so it sorts last: the point is to show the
DIRECTORY collected a file nothing named, not to interrupt the suite early.
"""
from __future__ import annotations

import numpy as np


def test_this_file_is_collected_by_the_directory_run_and_can_turn_ci_red():
    """Deliberately false. If CI is green with this file present, #404 is not fixed."""
    measured = float(np.sqrt(np.mean(np.array([3.0, 4.0]) ** 2)))
    assert measured == 0.0, (
        "#404 acceptance control: this assertion is deliberately false "
        f"(rms of [3, 4] is {measured:.4f}, not 0.0). Seeing this failure in "
        "the `tools` job means a test file that NO workflow names was still "
        "collected and still turned CI red -- which is the criterion. "
        "Remove this file."
    )
