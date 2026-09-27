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
        rows.append(f"MATCH\t{n}\t{kind}\t{c}\t{exp}\t{a}\t{rx}")
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
    (lambda r: "\n".join(ln for ln in r.splitlines() if "u_uart/rx_q_reg\\[0\\]/D" not in ln),
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
