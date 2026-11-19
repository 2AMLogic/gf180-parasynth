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

#: A fast node's line from the same log -- a REAL foreign anchor (4fe488f1,
#: another branch) with a foreign detail in one line. This is NOT what the
#: `foreign` control substitutes (another slow node's same-night line, whose
#: anchor every slow node shares on a quiet night -- see control_foreign's
#: docstring); it pins the foreign-anchor pathology directly in
#: test_substituted_line_from_another_run_fails_directly.
FOREIGN_LINE = ("2026-11-18T16:10:59.9911337Z   keep  F3  153 passed in 4.07s "
                "(unchanged since 4fe488f1)")

#: The log's identity anchors, verbatim from the same job log: the checkout
#: step's two head-reporting commands (rev-parse at 16:10:26, `git log -1
#: --format=%H` at 16:10:27) each followed by its bare-sha output line, and
#: upload-artifact's download URL carrying the run id. The gate asserts
#: --head-sha against the checkout step's own output and --run-id against
#: the log (review #1059, #1070): these lines are what make the pinned
#: evidence THIS run's. (Both sha occurrences the real log carries are
#: pinned.)
#: upload-artifact's download URL, verbatim from the same job log -- the
#: line that carries the run id.
ARTIFACT_URL_LINE = ("2026-11-18T16:31:13.8095034Z Artifact download URL: "
                     "https://github.com/2AMLogic/gf180-parasynth/actions/"
                     "runs/44344077192/artifacts/14445449921")

IDENTITY_LINES = [
    "2026-11-18T16:10:26.8792578Z [command]/usr/bin/git rev-parse refs/remotes/origin/main",
    "2026-11-18T16:10:26.8793193Z 4a6eda4d2df8a41bdb0e2b6110137dd5fb5478f2",
    "2026-11-18T16:10:27.5620308Z [command]/usr/bin/git log -1 --format=%H",
    "2026-11-18T16:10:27.5652306Z 4a6eda4d2df8a41bdb0e2b6110137dd5fb5478f2",
    ARTIFACT_URL_LINE,
]

#: A dependency action's resolved commit, verbatim from line 31 of the same
#: job log: `actions/checkout@v4`'s own SHA. A 40-hex string in the log but
#: NOT this repository's head -- the input that passed the old any-line
#: substring check and must be refused (#1070).
ACTION_SHA = "11d5960a326750d5838078e36cf38b85af677262"
ACTION_SHA_LINE = ("2026-11-18T16:10:22.9087306Z Download action repository "
                   "'actions/checkout@v4' "
                   "(SHA:11d5960a326750d5838078e36cf38b85af677262)")


@pytest.fixture()
def replay(tmp_path):
    """The recorded quiet night, materialised as gate inputs."""
    pub = tmp_path / "published.json"
    run = tmp_path / "at-run.json"
    log = tmp_path / "full-rtl.log"
    pub.write_text(json.dumps(RECORDED))
    run.write_text(json.dumps(RECORDED))
    log.write_text("\n".join(IDENTITY_LINES + RECORDED_LOG) + "\n")
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


def test_bare_records_without_recording_lines_are_refused(replay):
    """Six bare records at 91398a0c with no nightly line anywhere: the log
    at hand is not one this gate can use, so it REFUSES -- kept distinct
    from FAIL, which is the verdict when recording rows exist and a node's
    does not (test_green_step_cannot_supply_a_missing_row: bare records
    fail per-node there, the defeat the original clause existed to
    close)."""
    replay["log"].write_text("2026-11-18T16:31:12.5129144Z the records are unchanged\n")
    assert run_gate(replay) == cnsr.EXIT_REFUSED


def test_green_step_cannot_supply_a_missing_row(replay, capsys):
    """Every node keeps its line except one; the silent node must fail, not
    ride on the step's green exit (`|| true`)."""
    lines = [ln for ln in IDENTITY_LINES + RECORDED_LOG
             if "keep  I2  " not in ln]
    replay["log"].write_text("\n".join(lines) + "\n")
    assert run_gate(replay) == cnsr.EXIT_FAIL
    out = capsys.readouterr().out
    assert "FAIL I2" in out and "5/6" in out


# --- REFUSED: the apparatus answers only when it can -------------------------

def test_refused_on_empty_log(replay):
    replay["log"].write_text("")
    assert run_gate(replay) == cnsr.EXIT_REFUSED


def test_refused_on_log_without_recording_lines(replay):
    replay["log"].write_text("\n".join(IDENTITY_LINES)
                             + "\n2026-11-18T16:31:12Z some other step's output\n")
    assert run_gate(replay) == cnsr.EXIT_REFUSED


# --- the log's identity is asserted, not assumed ------------------------------

def test_refused_when_log_does_not_carry_head_sha(replay, capsys):
    """The measured attack from review: the six real keep rows with a
    different night's timestamps and ZERO occurrences of the head sha are
    not tonight's evidence -- REFUSED naming what is missing, never
    attributed to the run."""
    replay["log"].write_text("\n".join(RECORDED_LOG) + "\n")
    assert run_gate(replay) == cnsr.EXIT_REFUSED
    err = capsys.readouterr().err
    assert "head sha" in err and HEAD_SHA in err


def test_refused_when_log_does_not_carry_run_id(replay, capsys):
    """Every measured log carries the run id (the artifact download URL);
    a log without it is not that run's log."""
    identity = [ln for ln in IDENTITY_LINES if "Artifact download" not in ln]
    replay["log"].write_text("\n".join(identity + RECORDED_LOG) + "\n")
    assert run_gate(replay) == cnsr.EXIT_REFUSED
    err = capsys.readouterr().err
    assert "run id" in err and "44344077192" in err


def test_run_id_and_job_id_are_optional(replay):
    """Omitting --run-id/--job-id skips their assertions; the head sha is
    still required to appear in the log."""
    argv = ["--published", str(replay["published"]),
            "--at-run", str(replay["at_run"]),
            "--log", str(replay["log"]), "--head-sha", HEAD_SHA]
    assert cnsr.main(argv) == cnsr.EXIT_OK


# --- the head sha is the checkout's own output, not any line naming it -------
#     (#1070): the old `any(anchor in line for line in log_lines)` accepted
#     actions/checkout's own commit -- line 31 of the real job log -- as
#     head evidence and passed 6/6 on it.

def test_action_commit_sha_is_refused_as_head(replay, capsys):
    """#1070, the reported defect: the `Download action repository` line
    names a dependency's commit, and the old any-line check let it PASS the
    gate 6/6 on the unmodified log. It is not this repository's head."""
    replay["log"].write_text(
        "\n".join([ACTION_SHA_LINE] + IDENTITY_LINES + RECORDED_LOG) + "\n")
    assert run_gate(replay, "--head-sha", ACTION_SHA) == cnsr.EXIT_REFUSED
    err = capsys.readouterr().err
    assert "head sha" in err and ACTION_SHA in err


def test_refused_when_head_sha_appears_without_checkout_output(replay, capsys):
    """A sha stray in the file -- a mention, a dependency line -- with no
    checkout-step output leaves the gate unable to say whose checkout the
    log carries: REFUSED, never attributed. The artifact URL line is kept
    so the run-id assertion passes and the refusal is genuinely the head
    sha's."""
    stray = "2026-11-18T16:31:12Z a stray line naming " + HEAD_SHA
    replay["log"].write_text(
        "\n".join([stray, ARTIFACT_URL_LINE] + RECORDED_LOG) + "\n")
    assert run_gate(replay) == cnsr.EXIT_REFUSED
    err = capsys.readouterr().err
    assert "checkout" in err


def test_mismatched_checkout_output_is_refused(replay, capsys):
    """A log whose checkout step itself reports a different full sha -- the
    previous night's log, another checkout -- is refused naming what the
    checkout printed. The stray occurrence is kept so the old any-line
    check would have passed this (red-first)."""
    other = "b" * 40
    identity = [ln.replace(HEAD_SHA, other) for ln in IDENTITY_LINES]
    stray = "2026-11-18T16:31:12Z a stray line naming " + HEAD_SHA
    replay["log"].write_text(
        "\n".join([stray] + identity + RECORDED_LOG) + "\n")
    assert run_gate(replay) == cnsr.EXIT_REFUSED
    err = capsys.readouterr().err
    assert HEAD_SHA in err and other in err


def test_short_sha_checkout_output_is_refused(replay):
    """The checkout printing a short sha is not the full-sha identity the
    gate asserts: refused, never matched loosely."""
    identity = [ln.replace(HEAD_SHA, HEAD_SHA[:8]) for ln in IDENTITY_LINES]
    stray = "2026-11-18T16:31:12Z a stray line naming " + HEAD_SHA
    replay["log"].write_text(
        "\n".join([stray] + identity + RECORDED_LOG) + "\n")
    assert run_gate(replay) == cnsr.EXIT_REFUSED


def test_stray_head_sha_occurrences_change_nothing(replay):
    """Occurrences outside the checkout output are inert: extra copies of
    the right sha neither rescue a wrong identity nor disturb the right
    one."""
    stray = "2026-11-18T16:31:12Z a stray line naming " + HEAD_SHA
    replay["log"].write_text(
        "\n".join([stray] + IDENTITY_LINES + RECORDED_LOG) + "\n")
    assert run_gate(replay) == cnsr.EXIT_OK


def test_repo_head_evidence_is_scoped_to_checkout_output_lines():
    """The extractor reads only the lines following the checkout step's own
    head commands -- not the `Download action repository` line, which also
    carries a 40-hex sha (#1070)."""
    evidence = cnsr.repo_head_evidence(
        [ACTION_SHA_LINE] + IDENTITY_LINES + RECORDED_LOG)
    assert [out for _line, out in evidence] == [HEAD_SHA, HEAD_SHA]


def test_refused_on_log_with_only_fast_node_lines(replay, capsys):
    """A fast node's keep row alone is not the log this gate needs:
    REFUSED (no slow-node recording lines), not a 0/6 FAIL -- the shape
    #1058 was filed about."""
    replay["log"].write_text("\n".join(IDENTITY_LINES + [FOREIGN_LINE]) + "\n")
    assert run_gate(replay) == cnsr.EXIT_REFUSED
    assert "no recording-step lines" in capsys.readouterr().err


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
