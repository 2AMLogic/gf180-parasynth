"""Tests for the die-area provenance guard (issue #245, AC2).

Two of these are grounded in files this repository actually ships rather than in
fixtures invented here, which is the distinction `docs/failure-modes.md` is
about: `synth_top/config.mk` mentions `CORE_UTILIZATION` only in a comment
saying it is deliberately unset, and `ladder_dp`/`synth_core` really do set it.
A parser that cannot tell those apart would either refuse everything or refuse
nothing.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import area_provenance as ap  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent


# ------------------------------------------------------------------- parsing

def test_a_commented_out_variable_is_not_an_assignment():
    text = ("# The die here is a FIXED INPUT. CORE_UTILIZATION is deliberately\n"
            "# NOT set: setting a target makes the die a restatement.\n"
            "export DIE_AREA          = 0 0 1860.88 1862.00\n")
    variables = ap.parse_config_mk(text)
    assert "CORE_UTILIZATION" not in variables
    assert variables["DIE_AREA"] == "0 0 1860.88 1862.00"


def test_an_inline_comment_is_stripped_from_the_value():
    # docs/pnr-first-run.md 1.1 finding 6: make keeps the whitespace, not the
    # comment. Either way the comment is not part of the value.
    assert ap.parse_config_mk("export CORNER = TC   # the typical corner\n") == {
        "CORNER": "TC"}


def test_the_shipped_synth_top_config_is_a_fixed_die():
    variables = ap.parse_config_mk((HERE / "synth_top/config.mk").read_text())
    assert ap.target_from_config_mk(variables) is None
    assert ap.fixed_die(variables)
    provenance = ap.classify([("config_mk", "synth_top/config.mk", variables)])
    assert (provenance.state, provenance.reason) == ("OK", "fixed-die")


@pytest.mark.parametrize("design", ["ladder_dp", "synth_core"])
def test_the_two_shipped_configs_that_do_set_a_target_are_refused(design):
    """This is the finding, pinned: both of these ship CORE_UTILIZATION = 50.

    If a future change gives one of them a fixed die, this test must be updated
    deliberately -- which is the point of pinning it.
    """
    variables = ap.parse_config_mk((HERE / design / "config.mk").read_text())
    assert ap.target_from_config_mk(variables) == 0.5
    provenance = ap.classify([("config_mk", f"{design}/config.mk", variables)])
    assert (provenance.state, provenance.reason) == ("REFUSED", "circular-die-area")
    assert provenance.utilisation_target == 0.5


def test_the_exact_shipped_par_request_is_recognised():
    request = ap.parse_par_request(json.dumps(ap.SHIPPED_PAR_REQUEST))
    assert ap.target_from_par_request(request) == 0.5
    provenance = ap.classify([("par_request", "par_request.json", request)])
    assert provenance.reason == "circular-die-area"
    assert "2.00" in provenance.detail        # 1 / 0.50, the input read back


def test_a_par_request_with_a_die_method_is_not_circular():
    request = {"method": "die_area", "die_area": [0, 0, 100, 100]}
    assert ap.target_from_par_request(request) is None
    assert ap.classify([("par_request", "p.json", request)]).state == "OK"


def test_unparseable_json_is_refused_not_treated_as_no_target():
    with pytest.raises(ValueError):
        ap.parse_par_request("{this is not json")


def test_no_artefact_at_all_is_refused_rather_than_assumed_fine():
    provenance = ap.classify([])
    assert (provenance.state, provenance.reason) == ("REFUSED", "no-provenance")


def test_a_config_with_neither_a_die_nor_a_target_is_refused():
    provenance = ap.classify([("config_mk", "c.mk", {"PLATFORM": "gf180"})])
    assert (provenance.state, provenance.reason) == ("REFUSED", "no-die-input")


# ------------------------------------------------------- the arithmetic is not
# ------------------------------------------------------- the trigger

def test_the_arithmetic_shows_a_consistent_run_recovering_its_own_input():
    text = ap.corroborate(0.5, synth_cell_area=1_000_000, core_area=2_000_000,
                          die_area=2_100_000)
    assert "the ratio IS the target read back" in text


def test_a_target_whose_numbers_do_not_agree_is_still_refused():
    """The trigger is the artefact. A gate that needs the numbers to agree goes
    quiet exactly when the provenance is most confused."""
    provenance = ap.die_area_provenance(
        work=None, design="ladder_dp", variant="base",
        synth_cell_area=1_000_000, core_area=1_200_000, die_area=1_300_000)
    assert provenance.state == "REFUSED"
    assert "do NOT agree" in provenance.corroboration


def test_missing_metrics_do_not_silently_become_a_clean_arithmetic_report():
    assert "cannot be shown" in ap.corroborate(0.5, None, None, None)


# ------------------------------------------------------------ artefact lookup

def test_a_run_local_artefact_wins_over_the_design_config(tmp_path):
    run = tmp_path / "logs/gf180/synth_top/base"
    run.mkdir(parents=True)
    (run / "par_request.json").write_text(json.dumps(ap.SHIPPED_PAR_REQUEST))
    (run / "config.mk").write_text("export DIE_AREA = 0 0 1 1\nexport CORE_AREA = 0 0 1 1\n")
    artefacts = ap.find_artefacts(tmp_path, "synth_top", "base")
    assert artefacts[0][0] == "par_request"
    assert ap.classify(artefacts).reason == "circular-die-area"


def test_an_unknown_design_with_no_run_local_artefact_is_refused(tmp_path):
    provenance = ap.die_area_provenance(tmp_path, "not_a_design", "base")
    assert (provenance.state, provenance.reason) == ("REFUSED", "no-provenance")


# --------------------------------------------------- the expectation contract

@pytest.mark.parametrize("state,quoted,refusal,expect,code", [
    ("OK", True, False, "ok", 0),
    ("REFUSED", False, True, "ok", 1),          # clean case refused: no discrimination
    ("REFUSED", False, True, "refused-circular", 0),
    ("OK", True, False, "refused-circular", 1),  # the control did not fire
    ("REFUSED", True, True, "refused-circular", 1),   # refused but quoted anyway
    ("REFUSED", False, False, "refused-circular", 2),  # exit 2 for another reason
    ("ERROR", False, False, "ok", 2),
])
def test_the_three_outcomes_are_distinguished(state, quoted, refusal, expect, code):
    result = {"state": state, "exit": {"OK": 0, "REFUSED": 2, "ERROR": 1}[state],
              "quoted_ratio": quoted, "printed_refusal": refusal}
    assert ap.check_expectation(result, expect)[0] == code


def test_an_unknown_expectation_is_no_verdict_not_a_pass():
    result = {"state": "OK", "exit": 0, "quoted_ratio": True, "printed_refusal": False}
    assert ap.check_expectation(result, "whatever")[0] == 2


# --------------------------------------------------------------- end to end

def test_the_staged_clean_run_summarises_and_quotes_a_ratio():
    result = ap.run_control(None)
    assert result["state"] == "OK"
    assert result["quoted_ratio"], "the clean baseline must PASS or the control " \
                                   "cannot discriminate (rule 5, condition 1)"


@pytest.mark.parametrize("name", sorted(ap.INJECTIONS))
def test_every_injection_makes_the_shipped_tool_refuse(name):
    result = ap.run_control(name)
    assert result["state"] == "REFUSED", result["output"][-800:]
    assert not result["quoted_ratio"], "a refusal that still prints the ratio is " \
                                       "not a refusal"
    assert result["printed_refusal"]


def test_the_control_refuses_when_the_injection_cannot_be_read_back(monkeypatch):
    """Condition 2: a control that did not inject must not report a verdict."""
    monkeypatch.setattr(ap, "run_summarize",
                        lambda work: (2, "### synth_top (base)\nREFUSED somewhere\n"))
    with pytest.raises(ap.ControlRefused, match="never named"):
        ap.run_control("UTILIZATION_TARGET")


def test_the_control_refuses_when_summarize_never_ran():
    orig = ap.run_summarize
    try:
        ap.run_summarize = lambda work: (127, "Traceback: REFUSED FAIL MISS\n")
        with pytest.raises(ap.ControlRefused, match="printed no report header"):
            ap.run_control(None)
    finally:
        ap.run_summarize = orig


def test_summarize_exits_two_and_withholds_the_ratio_under_the_injection(tmp_path):
    """The shipped CLI, not an in-process call: the thing that ships is the thing
    under test."""
    work = ap.stage(tmp_path / "work", ap.INJECTIONS["UTILIZATION_TARGET"])
    done = subprocess.run([sys.executable, str(HERE / "summarize.py"),
                           "synth_top", "base", "--work", str(work)],
                          capture_output=True, text=True)
    assert done.returncode == 2
    assert not ap.RATIO_CLAIM.search(done.stdout)
    assert "die area: **REFUSED**" in done.stdout
    # The measurements that do not depend on where the die came from still print.
    assert "routed wirelength" in done.stdout
