#!/usr/bin/env python3
"""Regression gate for the DPREG-4 refusal cases.

Runs every case in tools/dsp_dpreg_refusal_cases.py against the current
analyser/simulator in a fresh subprocess and refuses to pass unless each
one is REFUSED (analyser: exit 3 "NO VERDICT"; simulator: exit 1
"NO VERDICT" for an incomplete trace). These cases all PASSED the pre-fix
apparatus -- that record is refusal-cases.pre-fix.json next to the
evidence; this test is what keeps them refused.
"""

import pathlib
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import dsp_dpreg_refusal_cases as cases  # noqa: E402

EXPECTED = {
    "analyse": (3, "NO VERDICT"),
    "sim": (1, "NO VERDICT"),
}


@pytest.mark.parametrize("name,builder,kind", cases.CASES,
                         ids=[c[0] for c in cases.CASES])
def test_bad_input_is_refused(name, builder, kind, tmp_path):
    exit_code, marker = EXPECTED[kind]
    res = cases.run_case(name, builder, kind, tmp_path)
    assert res["exit"] == exit_code, res
    assert marker in res["verdict"], res
