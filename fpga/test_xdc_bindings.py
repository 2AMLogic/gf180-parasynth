"""fpga/test_xdc_bindings.py -- every XDC object query binds exactly its objects (#315)."""
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import xdc_bindings as xb                                  # noqa: E402

TEXT = xb.XDC.read_text()


def _report(xdc_text, counts=None, ar=None, end=None):
    rows, bad = [], 0
    for n, kind, rx, exp, async_reg in xb.object_queries(xdc_text):
        c = (counts or {}).get(n, exp)
        a = (ar or {}).get(n, exp) if async_reg else "-"
        bad += c != exp or (async_reg and a != exp)
        rows.append(f"MATCH\t{n}\t{kind}\t{c}\t{exp}\t{a}\t{xb.query_id(rx)}")
    return "\n".join(rows + [f"END\t{bad if end is None else end}"]) + "\n"


def test_the_xdc_queries_and_their_required_counts():
    q = [(n, kind, exp, ar) for n, kind, _rx, exp, ar in xb.object_queries(TEXT)]
    assert [(kind, exp, ar) for _n, kind, exp, ar in q] == [
        ("cells", 6, True), ("cells", 2, True),
        ("pins", 1, False), ("pins", 1, False), ("pins", 1, False), ("pins", 1, False)]


def test_the_uart_queries_name_the_generate_block_with_a_dot():
    uart = [rx for _n, _k, rx, _e, _a in xb.object_queries(TEXT) if "u_uart" in rx]
    assert uart == [r".*g_uart\.u_uart/rx_q_reg\[[01]\]", r".*g_uart\.u_uart/rx_q_reg\[0\]/D"]
    assert "g_uart/u_uart" not in TEXT.split("#315")[0] + "".join(
        ln for ln in TEXT.splitlines(True) if not ln.lstrip().startswith("#"))


def test_a_report_that_binds_everything_is_accepted():
    assert xb.check_report(_report(TEXT), TEXT) == []


def test_control_r1s_dead_uart_constraints_are_refused():
    """R1's XDC (the pre-#315 pattern), with the counts Vivado measured on R1's
    routed checkpoint: the UART lines matched 0 objects. Refused, naming both."""
    r1_xdc = subprocess.run(["git", "-C", str(HERE.parent), "show",
                             "6864435aa6eb:fpga/boards/arty-a7-100.xdc"],
                            capture_output=True, text=True, check=True).stdout
    assert "g_uart/u_uart" in r1_xdc
    lines = [n for n, _k, rx, _e, _a in xb.object_queries(r1_xdc) if "u_uart" in rx]
    rep = _report(r1_xdc, counts={n: 0 for n in lines}, ar={n: 0 for n in lines})
    probs = xb.check_report(rep, r1_xdc)
    assert any("line 42" in p and "matched 0" in p for p in probs), probs
    assert any("line 46" in p and "matched 0" in p for p in probs), probs


@pytest.mark.parametrize("mutate,why", [
    (lambda r: r.replace("END\t0", ""), "no END line"),
    (lambda r: r.replace("END\t0", "END\t1"), "records 1 refused"),
    (lambda r: "\n".join(ln for ln in r.splitlines()
                         if xb.query_id(r".*g_uart\.u_uart/rx_q_reg\[0\]/D") not in ln),
     "not in constraint_matches.rpt"),
])
def test_an_incomplete_or_refused_report_is_refused(mutate, why):
    assert any(why in p for p in xb.check_report(mutate(_report(TEXT)), TEXT))


def test_async_reg_missing_on_a_matched_cell_is_refused():
    n = next(n for n, _k, rx, _e, ar in xb.object_queries(TEXT) if ar and "u_uart" in rx)
    probs = xb.check_report(_report(TEXT, ar={n: 1}), TEXT)
    assert any("ASYNC_REG set on 1 of 2" in p for p in probs), probs


def test_a_query_without_a_stated_count_is_refused():
    with pytest.raises(xb.Refused, match="no required match count"):
        xb.object_queries(TEXT + "\nset_property ASYNC_REG TRUE "
                          "[get_cells -hier -regexp {.*u_new/q_reg}]\n")


def test_the_build_stops_on_a_mismatch():
    tcl = xb.tcl_assertions(TEXT, "/x/constraint_matches.rpt")
    assert tcl.count("set cm_objs [") == 6 and "exit 3" in tcl
    import build_arty as ba
    script = ba.tcl_script(Path("/OUT"), [])
    assert script.index("synth_design") < script.index("constraint_matches.rpt") \
        < script.index("opt_design")


# ---- the publisher (fpga/publish_arty.publish) ---------------------------------------
def _publishable(tmp_path):
    import publish_binding_cases as cases
    return cases.make_artifact(tmp_path)


def _rebind(art):
    import json
    import publish_binding_cases as cases
    rec = json.loads((art / "report.json").read_text())
    rec["artifact_sha256"][xb.REPORT] = cases.sha(art / xb.REPORT)
    rec["artifact_sha256"][xb.EXCEPTIONS] = cases.sha(art / xb.EXCEPTIONS)
    (art / "report.json").write_text(json.dumps(rec, indent=2) + "\n")


def test_publisher_refuses_a_build_without_the_constraint_report(tmp_path):
    import json
    import publish_arty as publish
    art = _publishable(tmp_path)
    rec = json.loads((art / "report.json").read_text())
    rec["artifact_sha256"].pop(xb.REPORT)
    (art / "report.json").write_text(json.dumps(rec, indent=2) + "\n")
    with pytest.raises(ValueError, match="constraints were never shown to bind"):
        publish.publish(art, tmp_path / "out")


def test_control_publisher_refuses_a_uart_constraint_that_bound_nothing(tmp_path):
    """The R1 defect at publication: the UART lines matched 0 objects."""
    import publish_arty as publish
    import publish_binding_cases as cases
    art = _publishable(tmp_path)
    uart = [n for n, _k, rx, _e, _a in xb.object_queries(TEXT) if "u_uart" in rx]
    cases.write_constraint_report(art, counts={n: 0 for n in uart})
    _rebind(art)
    with pytest.raises(ValueError, match="XDC constraints did not bind"):
        publish.publish(art, tmp_path / "out")


# ---- plan099: after route, the constraints do what they are for ----------------------
def test_the_xdc_false_paths_are_the_expected_exceptions():
    assert xb.expected_exceptions(TEXT) == [
        ("[get_ports spi_sck]", r"[get_pins -hier -regexp {.*u_spi/sck_q_reg\[0\]/D}]"),
        ("[get_ports spi_mosi]", r"[get_pins -hier -regexp {.*u_spi/mosi_q_reg\[0\]/D}]"),
        ("[get_ports spi_cs_n]", r"[get_pins -hier -regexp {.*u_spi/csn_q_reg\[0\]/D}]"),
        ("[get_ports uart_rxd]", r"[get_pins -hier -regexp {.*g_uart\.u_uart/rx_q_reg\[0\]/D}]"),
        ("[get_ports btn_reset]", "*")]


def test_the_route_tcl_checks_identity_exception_and_analysed_paths():
    tcl = xb.tcl_route_checks("/x/r.rpt", "/x/e.rpt")
    for name in xb.ROUTE_CHECKS:
        assert f"rc_put $rc_fh {name}" in tcl
    assert "all_fanout -from [get_ports uart_rxd]" in tcl and "report_exceptions" in tcl
    import build_arty as ba
    script = ba.tcl_script(Path("/OUT"), [])
    assert script.index("route_design") < script.index("uart_stage1") \
        < script.index("report_utilization")


@pytest.mark.parametrize("failed", [["uart_stage1"], ["uart_false"], ["uart_s1s2"], ["uart_down"]])
def test_control_publisher_refuses_a_failed_after_route_check(tmp_path, failed):
    import publish_arty as publish
    import publish_binding_cases as cases
    art = _publishable(tmp_path)
    cases.write_constraint_report(art, failed=failed)
    _rebind(art)
    with pytest.raises(ValueError, match=f"after-route check {failed[0]} failed"):
        publish.publish(art, tmp_path / "out")


def test_control_publisher_refuses_an_unintended_exception(tmp_path):
    import publish_arty as publish
    import publish_binding_cases as cases
    art = _publishable(tmp_path)
    cases.write_constraint_report(art, extra_exceptions=[("[get_ports spi_miso]", "*")])
    _rebind(art)
    with pytest.raises(ValueError, match="not exactly the XDC's set_false_path lines"):
        publish.publish(art, tmp_path / "out")


def test_control_r1s_exception_list_lacks_the_uart_false_path():
    """R1 as built: report_exceptions has 4 rows, no uart_rxd (measured on
    R1's routed checkpoint, fpga/reports/xdc-315/). Refused against the fix."""
    rows = [(f, t) for f, t in xb.expected_exceptions(TEXT) if "uart_rxd" not in f]
    rep = "--------\n" + "".join(f"{i}  {f}  *  {t}  false  false\n"
                                   for i, (f, t) in enumerate(rows, 2))
    ok = "".join(f"CHECK\t{c}\t1\tx\n" for c in xb.ROUTE_CHECKS) + "ROUTE_END\t0\n"
    assert any("not exactly the XDC" in p for p in xb.check_route(ok, rep, TEXT))


# ---- a refused XDC is REFUSED, never a "did not bind" problem (#1922) ------------
# object_queries refuses a query with no stated count; expected_exceptions
# refuses an exception kind it does not model. Neither is a measurement, so
# neither may come back in a problems list -- which every caller reads as "the
# constraints did not bind": publish_arty's sentence, r2_release's reason, the
# two builds' FAIL state.
UNCOUNTED = "\nset_property ASYNC_REG TRUE [get_cells -hier -regexp {.*u_new/q_reg}]\n"
UNMODELLED = "\nset_max_delay 5.0 -from [get_ports x] -to [get_ports y]\n"
NO_OP = "\n# a planted comment: nothing in it for either parser to read\n"
ROUTE_OK = "".join(f"CHECK\t{c}\t1\tfixture\n" for c in xb.ROUTE_CHECKS) + "ROUTE_END\t0\n"


def _exceptions(xdc_text):
    rows = xb.expected_exceptions(xdc_text)
    return "--------\n" + "".join(f"{i}  {f}  *  {t}  false  false\n"
                                 for i, (f, t) in enumerate(rows, 2))


def _failed(name):
    return "".join(f"CHECK\t{c}\t{0 if c == name else 1}\t{c}\n" for c in xb.ROUTE_CHECKS) \
        + "ROUTE_END\t1\n"


@pytest.mark.parametrize("plant,check,why", [
    (UNCOUNTED, lambda xdc: xb.check_report(_report(TEXT), xdc), "no required match count"),
    (UNMODELLED, lambda xdc: xb.check_route(ROUTE_OK, _exceptions(TEXT), xdc), "does not model"),
])
def test_a_refused_xdc_raises_instead_of_answering(plant, check, why):
    """Red-first control: each checker returned [str(exc)] here -- a non-empty
    problems list, the one thing every caller reads as "did not bind"."""
    with pytest.raises(xb.Refused, match=why):
        check(TEXT + plant)


def test_each_plant_refuses_exactly_one_checker():
    """UNCOUNTED is refused where check_report reads the XDC and nowhere else;
    UNMODELLED where check_route does. A refusal in one check leaves the other
    free to measure."""
    assert xb.check_route(ROUTE_OK, _exceptions(TEXT), TEXT + UNCOUNTED) == []
    assert xb.check_report(_report(TEXT), TEXT + UNMODELLED) == []


def test_control_a_planted_no_op_xdc_still_binds():
    xdc = TEXT + NO_OP
    assert xb.check_report(_report(xdc), xdc) == []
    assert xb.check_route(ROUTE_OK, _exceptions(xdc), xdc) == []
    assert xb.check_build(_report(xdc) + ROUTE_OK, _exceptions(xdc), xdc) == []


def test_a_measured_after_route_failure_outranks_a_refused_exceptions_list():
    """Precedence: a real failure next to a refusal is FAIL. check_route has
    already measured the after-route section when expected_exceptions refuses;
    that measurement is the result, and the refusal is not appended to it."""
    probs = xb.check_route(_failed("uart_false"), _exceptions(TEXT), TEXT + UNMODELLED)
    assert any("after-route check uart_false failed" in p for p in probs), probs
    assert not any("does not model" in p for p in probs), probs


@pytest.mark.parametrize("plant,why", [
    (UNCOUNTED, "no required match count"), (UNMODELLED, "does not model"),
    (UNCOUNTED + UNMODELLED, "no required match count"),
])
def test_check_build_refuses_a_refused_xdc_for_its_own_reason(plant, why):
    with pytest.raises(xb.Refused, match=why) as caught:
        xb.check_build(_report(TEXT) + ROUTE_OK, _exceptions(TEXT), TEXT + plant)
    assert "did not bind" not in str(caught.value)


def test_check_build_a_real_did_not_bind_next_to_a_refusal_is_fail():
    """Which wins when check_report binds wrongly but check_route refuses: the
    measured problem. And when check_report refuses but check_route measured a
    failure: still the measured problem. A refusal with nothing measured is
    REFUSED (the test above)."""
    uart = [n for n, _k, rx, _e, _a in xb.object_queries(TEXT) if "u_uart" in rx]
    probs = xb.check_build(_report(TEXT, counts={n: 0 for n in uart}) + ROUTE_OK,
                           _exceptions(TEXT), TEXT + UNMODELLED)
    assert any("matched 0" in p for p in probs) and not any("does not model" in p for p in probs)
    probs = xb.check_build(_report(TEXT) + _failed("uart_false"), _exceptions(TEXT), TEXT + UNCOUNTED)
    assert any("uart_false failed" in p for p in probs) \
        and not any("no required match count" in p for p in probs)


def _plant_snapshot_xdc(art, plant):
    """The XDC the build compiled is the snapshot under inputs/, which no digest
    covers (publish_binding_cases' xdc_value_drift edits it the same way), so
    the planted line is the ONLY defect in the artifact."""
    xdc = art / "inputs/fpga/boards/arty-a7-100.xdc"
    xdc.write_text(xdc.read_text() + plant)


@pytest.mark.parametrize("plant,why", [(UNCOUNTED, "no required match count"),
                                       (UNMODELLED, "does not model")])
def test_publisher_refuses_a_refused_xdc_rather_than_saying_it_did_not_bind(tmp_path, plant, why):
    """#1922: publish() raised ValueError('XDC constraints did not bind: <refusal>')
    here -- a refusal restated as a measured disagreement."""
    import publish_arty as publish
    art = _publishable(tmp_path)
    _plant_snapshot_xdc(art, plant)
    with pytest.raises(xb.Refused, match=why) as caught:
        publish.publish(art, tmp_path / "out")
    assert "did not bind" not in str(caught.value)


def test_control_publisher_still_fails_a_constraint_that_bound_nothing_beside_a_refusal(tmp_path):
    """A measured "did not bind" outranks the refusal beside it: FAIL, with the
    refusal kept out of the sentence."""
    import publish_arty as publish
    import publish_binding_cases as cases
    art = _publishable(tmp_path)
    uart = [n for n, _k, rx, _e, _a in xb.object_queries(TEXT) if "u_uart" in rx]
    cases.write_constraint_report(art, counts={n: 0 for n in uart})
    _rebind(art)
    _plant_snapshot_xdc(art, UNMODELLED)
    with pytest.raises(ValueError, match="XDC constraints did not bind") as caught:
        publish.publish(art, tmp_path / "out")
    assert not isinstance(caught.value, xb.Refused)
    assert "does not model" not in str(caught.value)


def test_publish_arty_cli_ends_refused_not_a_traceback(tmp_path):
    """The CLI is what a release runs (fpga/ARTY.md): with no handler a
    propagating Refused would be exit 1 and a traceback -- the shape of an
    implementation error, not of a stated missing precondition."""
    art = _publishable(tmp_path)
    _plant_snapshot_xdc(art, UNMODELLED)
    out = tmp_path / "publication"
    r = subprocess.run([sys.executable, str(HERE / "publish_arty.py"), str(art), "--out", str(out)],
                       capture_output=True, text=True, cwd=str(HERE.parent))
    assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
    assert "publish_arty: REFUSED --" in r.stdout and "does not model" in r.stdout, r.stdout
    assert "did not bind" not in r.stdout and "Traceback" not in r.stderr, (r.stdout, r.stderr)
    assert not (out / "publication.json").exists()
