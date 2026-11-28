#!/usr/bin/env python3
"""Publication-binding regression tests (fpga/publish_binding_cases.py).

Every case there PASSED the pre-fix publisher -- binding-cases.pre-fix.json
records it; this file is what keeps them refused. The baseline case is the
satisfiability guard: an honest artifact must still publish, otherwise the
gates above would be unsatisfiable and would train everyone to ignore gates.

The CLI-boundary controls (#1615) run the shipped script as `__main__` under
the real interpreter with one alteration: reading the tree's XDC returns the
file plus a constraint the binding checker refuses. Known answers, measured
red-first on main @ 0a6b0861 (2026-11-28): both alterations then exited 1
with an `xdc_bindings.Refused` traceback, because every case builds from that
XDC outside run_case's publish-scoped handler. They must exit 2 naming the
XDC's own reason; a plain ValueError/OSError raised by the same read must
still exit 1; the clean XDC must still exit 0. Every assertion pins the
message, not just the code: an exit code alone is satisfied by a
crashed-and-caught-anything handler.
"""

import json
import pathlib
import re
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
sys.path.append(str(pathlib.Path(__file__).resolve().parents[1] / "tools"))

import exit_contract  # noqa: E402
import publish_binding_cases as cases  # noqa: E402
import refusal  # noqa: E402
import xdc_bindings  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "fpga/publish_binding_cases.py"
QUERY_WITHOUT_REQUIRED_COUNT = \
    "set_property ASYNC_REG TRUE [get_cells -hier -regexp {u_extra.*}]\n"
UNMODELLED_EXCEPTION = "set_multicycle_path 2 -from [get_ports spi_sck]\n"
INJECTED_ERROR = "injected unrelated error at the XDC read"

_RUNNER = """
import pathlib, runpy, sys
sys.path.insert(0, {fpga!r})
import build_arty
_base = build_arty.XDC.read_text()
_orig = pathlib.Path.read_text
def _read_text(self, *a, **kw):
    if self == build_arty.XDC:
        {body}
    return _orig(self, *a, **kw)
pathlib.Path.read_text = _read_text
sys.argv = [{script!r}] + {argv!r}
runpy.run_path({script!r}, run_name="__main__")
"""


def _cli(tmp_path, alter="", error=None, args=("--case", "baseline")):
    """The shipped script as `__main__`: reading the tree's XDC returns the
    file plus `alter`, or raises a fresh `error`. The tracked XDC is never
    written and the record lands under tmp_path."""
    record = tmp_path / "record.json"
    body = (f"raise {error.__name__}({INJECTED_ERROR!r})" if error
            else f"return _base + {alter!r}")
    code = _RUNNER.format(fpga=str(SCRIPT.parent), body=body, script=str(SCRIPT),
                          argv=list(args) + ["--record", str(record)])
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, timeout=300, cwd=str(ROOT))
    return proc, record


def _read_text_altered(alter):
    base = xdc_bindings.XDC.read_text()
    orig = pathlib.Path.read_text

    def read_text(self, *a, **kw):
        if self == xdc_bindings.XDC:
            return base + alter
        return orig(self, *a, **kw)

    return read_text


# ---------------------------------------------------------------------------
# the CLI boundary: an XDC the binding checker refuses is REFUSED (2), never
# an exit-1 traceback
# ---------------------------------------------------------------------------
def test_cli_xdc_query_without_required_count_exits_refused_2(tmp_path):
    proc, record = _cli(tmp_path, alter=QUERY_WITHOUT_REQUIRED_COUNT)
    assert proc.returncode == exit_contract.REFUSED, (proc.returncode, proc.stderr[-400:])
    assert re.search(r"^REFUSED: XDC line \d+: cells query \{u_extra\.\*\} has no required "
                     r"match count in fpga/xdc_bindings\.EXPECT -- state it before adding "
                     r"the constraint$", proc.stderr, re.M), proc.stderr[-400:]
    assert "Traceback" not in proc.stderr and "Traceback" not in proc.stdout
    assert not record.exists(), "a refused run must not write --record"


def test_cli_xdc_unmodelled_exception_exits_refused_2(tmp_path):
    proc, record = _cli(tmp_path, alter=UNMODELLED_EXCEPTION)
    assert proc.returncode == exit_contract.REFUSED, (proc.returncode, proc.stderr[-400:])
    assert "REFUSED: the XDC holds an exception kind expected_exceptions() does not model" \
        in proc.stderr.splitlines(), proc.stderr[-400:]
    assert "Traceback" not in proc.stderr and "Traceback" not in proc.stdout
    assert not record.exists(), "a refused run must not write --record"


def test_cli_refusal_on_every_case_prints_no_case_line_and_no_record(tmp_path):
    proc, record = _cli(tmp_path, alter=QUERY_WITHOUT_REQUIRED_COUNT, args=())
    assert proc.returncode == exit_contract.REFUSED, (proc.returncode, proc.stderr[-400:])
    assert proc.stdout == "", "the first case must end the run before any case line"
    assert not record.exists()


# ---------------------------------------------------------------------------
# unrelated errors at the same read stay execution errors (exit 1): widening
# the handler must not relabel one as a refusal
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("error", [ValueError, OSError])
def test_cli_unrelated_error_at_the_xdc_read_still_exits_1(tmp_path, error):
    proc, record = _cli(tmp_path, error=error)
    assert proc.returncode == exit_contract.FAIL, (proc.returncode, proc.stderr[-400:])
    assert "Traceback (most recent call last)" in proc.stderr, proc.stderr[-400:]
    assert f"{error.__name__}: {INJECTED_ERROR}" in proc.stderr, \
        "the injected error must be the one that propagated"
    assert "REFUSED" not in proc.stderr and "REFUSED" not in proc.stdout
    assert not record.exists()


# ---------------------------------------------------------------------------
# the successful path: the clean XDC still publishes and records
# ---------------------------------------------------------------------------
def test_cli_clean_xdc_baseline_still_publishes_and_records(tmp_path):
    proc, record = _cli(tmp_path)
    assert proc.returncode == exit_contract.PASS, (proc.returncode, proc.stderr[-400:])
    assert "baseline".ljust(32) + " PUBLISHED" in proc.stdout.splitlines(), proc.stdout
    rows = json.loads(record.read_text())
    assert len(rows) == 1 and rows[0]["case"] == "baseline", rows
    assert rows[0]["published"] is True, rows


# ---------------------------------------------------------------------------
# the programmatic boundary: run_case keeps its handler scoped to publish(),
# so a fixture-build refusal reaches the caller instead of a reported row
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("alter, reason", [
    (QUERY_WITHOUT_REQUIRED_COUNT, "{u_extra.*}"),
    (UNMODELLED_EXCEPTION, "does not model"),
], ids=["query-without-count", "unmodelled-exception"])
def test_run_case_propagates_a_fixture_refusal_to_its_caller(tmp_path, monkeypatch,
                                                             alter, reason):
    monkeypatch.setattr(pathlib.Path, "read_text", _read_text_altered(alter))
    with pytest.raises(xdc_bindings.Refused) as caught:
        cases.run_case("baseline", cases.case_baseline, tmp_path)
    assert reason in str(caught.value)


def test_xdc_refusal_is_caught_by_the_base_and_is_a_value_error():
    assert issubclass(xdc_bindings.Refused, refusal.Refused)
    assert issubclass(xdc_bindings.Refused, ValueError)


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
    # no arty_a7_uart_top module exists in the integrated tree, so no
    # evidence is bound to that wrapper name; the refusal is the
    # derivation, not a hardcoded path
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


def test_uart_ports_without_constraints_are_refused(tmp_path):
    res = cases.run_case("uart_ports_without_disposition",
                         cases.case_uart_ports_without_disposition, tmp_path)
    assert not res["published"], res
    # the stripped evidence must each be named: TX output delay, RX
    # synchronizer ASYNC_REG, RX false path (port names are the wrapper's)
    assert "output-delay" in res["error"] and "uart_txd" in res["error"]
    assert "ASYNC_REG" in res["error"] and "uart_rxd" in res["error"]
    assert "false path" in res["error"]


def test_no_recorded_tx_disposition_is_refused(tmp_path, monkeypatch):
    # the TX receiver/baud disposition is recorded data (fpga/ext_io_timing.py
    # UART_TX_DISPOSITION); with it unrecorded, publication must refuse even
    # though the XDC constraints themselves are present
    import ext_io_timing
    monkeypatch.setattr(ext_io_timing, "UART_TX_DISPOSITION", None)
    res = cases.run_case("no_tx_disposition", cases.case_baseline, tmp_path)
    assert not res["published"], res
    assert "disposition" in res["error"] and "uart_txd" in res["error"]
