"""The amended nightly slow-record gate is verified against the run it replays.

The positive case is not synthetic: the six records and six keep lines below
were read verbatim from scheduled run 44344077192 (2026-11-18T16:10Z, job
134941613343, checkout 4a6eda4d2df8a41bdb0e2b6110137dd5fb5478f2) -- the
quiet night on which #820's original sha-equality clause read red on
machinery that measured green (#1058). Pinning them here keeps that positive
case answerable after Actions' log retention expires; the recorded
`origin/main` and run-checkout records are byte-identical (verified at
replay), so one records dict stands for both in these fixtures.

Everything the test plan demands of a control is exercised: a withheld line,
a line from another run, a corrupted anchor and a falsified stored verdict
must each turn red -- and the old rule must reject the very rows the amended
rule accepts.
"""
from __future__ import annotations

import json, os, pathlib, sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_nightly_slow_records as cnsr        # noqa: E402

HEAD_SHA = "4a6eda4d2df8a41bdb0e2b6110137dd5fb5478f2"
ANCHOR = "91398a0c42acd38b3f4d26efc513a28c6951bb2f"

#: The six slow records at the run checkout, byte-identical to the records
#: origin/main carried after the run (the commit step wrote nothing).
RECORDED = {
    "F1": {"detail": "verify_ladder: PASS -- 28800 samples, RTL identical to model", "passed": True, "sha": ANCHOR},
    "F2": {"detail": "verify_modal: PASS -- 57600 samples, RTL identical to model", "passed": True, "sha": ANCHOR},
    "M1": {"detail": "verify_voice: deadline at launch cycle 8: worst strobe cycle 171 (slack 83), worst last-bu", "passed": True, "sha": ANCHOR},
    "D1": {"detail": "verify_drums: PASS -- 409998 samples, RTL identical to model", "passed": True, "sha": ANCHOR},
    "I1": {"detail": "verify_ctl: PASS -- all 209 writes reached the register port exactly as sent, every one in", "passed": True, "sha": ANCHOR},
    "I2": {"detail": "verify_synth_top: PASS -- 2851 I2S periods decoded from the wire, every one identical to t", "passed": True, "sha": ANCHOR},
}

#: The recording step's own lines, verbatim from the job log -- plus the
#: commit step's no-change message that binds the night as a no-op.
RECORDED_LOG = [
    "2026-11-18T16:10:50.6948400Z   keep  F1  verify_ladder: PASS -- 28800 samples, RTL identical to model (unchanged since 91398a0c)",
    "2026-11-18T16:10:56.8085653Z   keep  F2  verify_modal: PASS -- 57600 samples, RTL identical to model (unchanged since 91398a0c)",
    "2026-11-18T16:21:48.4792915Z   keep  M1  verify_voice: deadline at launch cycle 8: worst strobe cycle 171 (slack 83), worst last-bu (unchanged since 91398a0c)",
    "2026-11-18T16:26:28.4844617Z   keep  D1  verify_drums: PASS -- 409998 samples, RTL identical to model (unchanged since 91398a0c)",
    "2026-11-18T16:31:04.2209278Z   keep  I1  verify_ctl: PASS -- all 209 writes reached the register port exactly as sent, every one in (unchanged since 91398a0c)",
    "2026-11-18T16:31:11.9290277Z   keep  I2  verify_synth_top: PASS -- 2851 I2S periods decoded from the wire, every one identical to t (unchanged since 91398a0c)",
    "2026-11-18T16:31:12.5129144Z the records are unchanged",
]

#: A fast node's line from the same log -- the `foreign` control substitutes
#: it (renamed) as a line from another run: foreign anchor AND foreign detail.
FOREIGN_LINE = ("2026-11-18T16:10:59.9911337Z   keep  F3  153 passed in 4.07s "
                "(unchanged since 4fe488f1)")


@pytest.fixture()
def replay(tmp_path):
    """The recorded quiet night, materialised as gate inputs."""
    pub = tmp_path / "published.json"
    run = tmp_path / "at-run.json"
    log = tmp_path / "full-rtl.log"
    pub.write_text(json.dumps(RECORDED))
    run.write_text(json.dumps(RECORDED))
    log.write_text("\n".join(RECORDED_LOG) + "\n")
    return {"published": pub, "at_run": run, "log": log}


def run_gate(replay, *extra):
    argv = ["--published", str(replay["published"]),
            "--at-run", str(replay["at_run"]),
            "--log", str(replay["log"]),
            "--head-sha", HEAD_SHA,
            "--run-id", "44344077192", "--job-id", "134941613343",
            *extra]
    return cnsr.main(argv)


# --- the replay: old rule rejects what the amended rule accepts --------------

def test_replay_amended_rule_accepts_all_six(replay, capsys):
    assert run_gate(replay) == cnsr.EXIT_OK
    out = capsys.readouterr().out
    assert "(a) 6/6 slow nodes corroborated" in out
    assert "retained: keep line matches anchor and detail" in out
    assert "run 44344077192 job 134941613343 (head 4a6eda4d)" in out


def test_old_rule_rejects_every_recorded_row(replay, capsys):
    """#820's original predicate -- the quiet-night unsatisfiability, shown
    on the real rows rather than asserted."""
    assert run_gate(replay, "--old-rule") == cnsr.EXIT_FAIL
    assert "0/6 satisfy it --> FAIL" in capsys.readouterr().out


def test_old_rule_accepts_a_rewritten_night(tmp_path, replay, capsys):
    """The old clause is not wrong on every night -- only on quiet ones."""
    rewritten = {n: {"detail": r["detail"] + " (re-measured)",
                     "passed": True, "sha": HEAD_SHA}
                 for n, r in RECORDED.items()}
    log = replay["log"].read_text() + "".join(
        f"\n2026-11-19T07:00:00.0000000Z   PASS  {n}  {rewritten[n]['detail']}"
        for n in RECORDED)
    replay["published"].write_text(json.dumps(rewritten))
    replay["at_run"].write_text(json.dumps(RECORDED))
    replay["log"].write_text(log)
    assert run_gate(replay, "--old-rule") == cnsr.EXIT_OK
    assert run_gate(replay) == cnsr.EXIT_OK
    assert "rewritten: sha == headSha" in capsys.readouterr().out


def test_mixed_night_passes_per_node(tmp_path, replay, capsys):
    """One node re-anchors (its covered files moved), five keep: the mix must
    pass, which is why the rule is evaluated per node."""
    mixed = dict(RECORDED)
    mixed["D1"] = {"detail": RECORDED["D1"]["detail"] + " (re-measured)",
                   "passed": True, "sha": HEAD_SHA}
    log = replay["log"].read_text().replace(
        "keep  D1  verify_drums",
        "dropped  D1  verify_drums") + (
        "\n2026-11-19T07:00:00.0000000Z   PASS  D1  "
        + mixed["D1"]["detail"])
    replay["published"].write_text(json.dumps(mixed))
    replay["log"].write_text(log)
    assert run_gate(replay) == cnsr.EXIT_OK
    out = capsys.readouterr().out
    assert "D1  rewritten" in out or "rewritten: sha == headSha" in out


# --- the controls: each must turn its node red -------------------------------

@pytest.mark.parametrize("spec", [
    "withhold=F1", "foreign=F1", "wrong-anchor=F1", "false-verdict=F1"])
def test_each_control_prevents_a_pass(replay, spec, capsys):
    """#1058's test plan, run against the recorded rows."""
    assert run_gate(replay, "--control", spec) == cnsr.EXIT_OK
    out = capsys.readouterr().out
    assert f"CONTROL {spec}: caught" in out
    assert "NOT CAUGHT" not in out


def test_false_verdict_fails_even_with_matching_keep(replay, capsys):
    """The stored boolean alone must never be enough -- the control makes the
    base rule fail before the control machinery even runs."""
    bad = dict(RECORDED)
    bad["I2"] = dict(RECORDED["I2"], passed=False)
    replay["published"].write_text(json.dumps(bad))
    assert run_gate(replay) == cnsr.EXIT_FAIL
    assert "not passed==true" in capsys.readouterr().out


def test_substituted_line_from_another_run_fails_directly(replay, capsys):
    """A plausible foreign keep line (renamed, real anchor from another
    branch) must fail the node without the control machinery."""
    keeps, _fresh = cnsr.parse_log(RECORDED_LOG + [FOREIGN_LINE])
    verdicts = cnsr.evaluate(RECORDED, RECORDED,
                             {**keeps, "F1": keeps["F3"]}, _fresh, HEAD_SHA)
    ok, why = verdicts["F1"]
    assert not ok and "no keep line" in why


def test_branch_records_without_execution_evidence_fail(replay, capsys):
    """#818's branch content -- six bare records at 91398a0c, no nightly
    line anywhere -- must still fail. This is the defeat the original clause
    existed to close; the amended rule closes it too."""
    replay["log"].write_text("2026-11-18T16:31:12.5129144Z the records are unchanged\n")
    assert run_gate(replay) == cnsr.EXIT_REFUSED  # no recording lines: REFUSED


def test_green_step_cannot_supply_a_missing_row(replay, capsys):
    """Every node keeps its line except one; the silent node must fail, not
    ride on the step's green exit (`|| true`)."""
    lines = [ln for ln in RECORDED_LOG if "keep  I2  " not in ln]
    replay["log"].write_text("\n".join(lines) + "\n")
    assert run_gate(replay) == cnsr.EXIT_FAIL
    out = capsys.readouterr().out
    assert "FAIL I2" in out and "5/6" in out


# --- REFUSED: the apparatus answers only when it can -------------------------

def test_refused_on_empty_log(replay):
    replay["log"].write_text("")
    assert run_gate(replay) == cnsr.EXIT_REFUSED


def test_refused_on_log_without_recording_lines(replay):
    replay["log"].write_text("2026-11-18T16:31:12Z some other step's output\n")
    assert run_gate(replay) == cnsr.EXIT_REFUSED


def test_refused_on_short_head_sha(replay):
    assert run_gate(replay, "--head-sha", "4a6eda4d") == cnsr.EXIT_REFUSED


def test_refused_on_missing_records_file(replay, tmp_path):
    replay["published"].unlink()
    assert run_gate(replay) == cnsr.EXIT_REFUSED


def test_refused_on_unparseable_records(replay):
    replay["published"].write_text("{not json")
    assert run_gate(replay) == cnsr.EXIT_REFUSED


# --- parsing edges -----------------------------------------------------------

def test_keep_line_detail_containing_pass_is_not_a_fresh_line():
    """A keep line's detail contains ` PASS -- `; the fresh-line pattern must
    never misread it as a rewritten verdict."""
    keeps, fresh = cnsr.parse_log(RECORDED_LOG)
    assert fresh == {}
    assert keeps["F1"][0][0] == RECORDED["F1"]["detail"]
    assert keeps["F1"][0][1] == "91398a0c"


def test_failed_rewrite_line_is_captured_and_fails(tmp_path, replay, capsys):
    """A rewritten record carrying the run's headSha whose log line is FAIL
    must fail with a reason that names the disagreement."""
    rec = dict(RECORDED["F1"], passed=True, sha=HEAD_SHA,
               detail=RECORDED["F1"]["detail"])
    published = dict(RECORDED, F1=rec)
    log = replay["log"].read_text().replace("keep  F1  verify_ladder",
                                            "dropped  F1  verify_ladder") + (
        "\n2026-11-19T07:00:00.0000000Z   FAIL  F1  "
        + rec["detail"])
    replay["published"].write_text(json.dumps(published))
    replay["log"].write_text(log)
    assert run_gate(replay) == cnsr.EXIT_FAIL
    assert "line(s) disagree" in capsys.readouterr().out
