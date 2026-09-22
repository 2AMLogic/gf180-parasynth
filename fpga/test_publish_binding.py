#!/usr/bin/env python3
"""Publication-binding regression tests (fpga/publish_binding_cases.py).

Every case there PASSED the pre-fix publisher -- binding-cases.pre-fix.json
records it; this file is what keeps them refused. The baseline case is the
satisfiability guard: an honest artifact must still publish, otherwise the
gates above would be unsatisfiable and would train everyone to ignore them.
"""

import sys

import pytest

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))

import publish_binding_cases as cases  # noqa: E402


def test_baseline_honest_artifact_still_publishes(tmp_path):
    """Satisfiability guard for every binding gate added to publish()."""
    res = cases.run_case("baseline", cases.case_baseline, tmp_path)
    assert res["published"], res
    assert res["exceptions"] == ["i2s_bclk"]


def test_extra_compiled_source_is_refused(tmp_path):
    res = cases.run_case("extra_compiled_source",
                         cases.case_extra_compiled_source, tmp_path)
    assert not res["published"], res
    assert "compiled input set differs" in res["error"]


def test_uart_wrapper_cannot_publish_with_the_clean_proof(tmp_path):
    res = cases.run_case("uart_top_with_clean_proof",
                         cases.case_uart_top_with_clean_proof, tmp_path)
    assert not res["published"], res
    # on this branch the UART wrapper has no bound evidence at all; the
    # refusal is the derivation, not a hardcoded path
    assert "verification" in res["error"]


def test_foreign_exception_port_is_refused(tmp_path):
    res = cases.run_case("foreign_exception", cases.case_foreign_exception,
                         tmp_path)
    assert not res["published"], res
    assert "permitted" in res["error"] and "i2s_bclk" in res["error"]


def test_xdc_value_drift_is_refused_at_publication(tmp_path):
    res = cases.run_case("xdc_value_drift", cases.case_xdc_value_drift,
                         tmp_path)
    assert not res["published"], res
    assert "value drift" in res["error"] and "8.2" in res["error"]


def test_uart_ports_without_disposition_are_refused(tmp_path):
    res = cases.run_case("uart_ports_without_disposition",
                         cases.case_uart_ports_without_disposition, tmp_path)
    assert not res["published"], res
    assert "uart_tx" in res["error"] and "disposition" in res["error"]
    assert "uart_rx" in res["error"]
