"""The trial dispatcher is verification infrastructure: its only failure that
matters is a false PASS. Each case below is plan085 section 5's acceptance list,
exercised through the real dispatcher (tools/trial.py) against stand-in
checkers that reproduce the real ones' evidence records and exit conventions.

The stand-ins emit the REAL shape: the deadline record is a committed one
(docs/deadline/reanalysis/prod-arty-uart.json) with single fields changed, so
a test cannot pass against a record format the real checker never writes.

The naive wrapper this replaces is "exit 0 means PASS". Every test marked
[naive-red] fails against that rule; that is the start-red evidence.
"""
from __future__ import annotations

import copy
import json
import os
import pathlib
import signal
import subprocess
import sys
import textwrap
import time

import pytest

HERE = pathlib.Path(__file__).resolve().parent
REAL_ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import trial                                                  # noqa: E402
import trial_env                                              # noqa: E402

PY = sys.executable
REAL_DEADLINE = json.loads((REAL_ROOT / "docs/deadline/reanalysis/prod-arty-uart.json").read_text())
REAL_LATE = json.loads((REAL_ROOT / "docs/deadline/reanalysis/ctl-arty-late160.json").read_text())

# A stand-in checker: behaves as the behaviour file says -- writes a record (or
# not), prints lines, sleeps, exits with a code. Only the dispatcher is under test.
FAKE = textwrap.dedent("""
    import json, sys, time, pathlib
    args = sys.argv[1:]
    b = json.loads(pathlib.Path(args[args.index("--behaviour") + 1]).read_text())
    out = pathlib.Path(args[args.index("--out") + 1])
    for line in b.get("print", []):
        print(line, flush=True)
    for rel, content in b.get("pre_files", {}).items():
        (out / rel).write_text(json.dumps(content))
    if b.get("sleep"):
        pathlib.Path(out, "started").write_text("1")
        time.sleep(b["sleep"])
    for rel, content in b.get("files", {}).items():
        p = out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content if isinstance(content, str) else json.dumps(content))
    sys.exit(b.get("rc", 0))
""")


class Repo:
    """A throwaway checkout: dag, registry, environment spec, stand-in checkers."""

    def __init__(self, root: pathlib.Path):
        self.root = root
        (root / "docs").mkdir()
        (root / "spec").mkdir()
        (root / "chk").mkdir()
        (root / "src.v").write_text("module m; endmodule\n")
        (root / "docs/dag.json").write_text(json.dumps(
            {"nodes": {"X": {"name": "x", "class": "implementation", "deps": [],
                             "covers": ["src.v"]}}}))
        self.env_spec = root / "spec/env.json"
        self.write_env({})
        (root / "chk/fake.py").write_text(FAKE)
        self.children, self.controls, self.requires, self.tools = [], [], [], []
        self.n = 0

    def write_env(self, tools: dict):
        self.env_spec.write_text(json.dumps({
            "version": "test-env/1", "python": "%d.%d" % sys.version_info[:2],
            "packages": {}, "toolchain": {"installer": "none", "bin": "nobin",
                                          "record": "none", "tools": tools}}))

    def child(self, behaviour: dict, interpret="deadline_record", role="required", **extra):
        self.n += 1
        bid = f"c{self.n}"
        bpath = self.root / "chk" / f"{bid}.json"
        bpath.write_text(json.dumps(behaviour))
        spec = {"id": bid, "property": "p", "checker": "chk/fake.py",
                "args": ["--behaviour", str(bpath), "--out", "{out}"],
                "interpret": interpret, **extra}
        (self.controls if role == "control" else self.children).append(spec)
        return bid

    def registry(self, timeout=60):
        reg = {"schema": 1, "environment": "spec/env.json", "trials": {"T-X": {
            "question": "q?", "criterion_version": "x/1", "scope": {"c": 1},
            "dag_nodes": ["X"], "modes": {"m": {
                "timeout_s": timeout, "tools": self.tools, "requires": self.requires,
                "required": self.children, "controls": self.controls}}}}}
        p = self.root / "docs/trials.json"
        p.write_text(json.dumps(reg))
        return p

    def run(self, timeout=60, **kw):
        reg = self.registry(timeout)
        return trial.run_trial("T-X", root=self.root, registry=reg,
                               dag=self.root / "docs/dag.json", env_spec=self.env_spec,
                               out_base=self.root / "build/trials", **kw)


@pytest.fixture
def repo(tmp_path):
    return Repo(tmp_path)


def deadline(**facts):
    rec = copy.deepcopy(REAL_DEADLINE)
    rec["facts"].update(facts)
    return rec


def ok_deadline():
    return {"rc": 0, "files": {"record.json": deadline()}}


def late_control():
    return {"rc": 0, "files": {"record.json": copy.deepcopy(REAL_LATE)}}


# ---- clean baseline ---------------------------------------------------------
def test_clean_baseline_with_a_caught_control_is_pass(repo):
    repo.child(ok_deadline())
    repo.child(late_control(), role="control")
    run_dir, rec = repo.run()
    assert rec["verdict"] == trial.PASS, rec["verdict_reasons"]
    assert rec["execution"]["status"] == "complete"
    assert rec["controls"][0]["caught"] is True
    ok, problems, _ = trial.check_receipt(run_dir / "receipt.json")
    assert ok, problems


def test_the_receipt_records_what_plan085_asks_for(repo):
    repo.child(ok_deadline())
    _, rec = repo.run()
    assert rec["trial"]["criterion_version"] == "x/1" and rec["trial"]["scope"] == {"c": 1}
    ids = rec["identities"]
    assert ids["dag_fingerprint"]["covers_sha256"]["src.v"] == trial.sha256_file(repo.root / "src.v")
    assert ids["checkers_sha256"]["chk/fake.py"] != "missing"
    assert ids["environment"]["spec"] == "test-env/1"
    c = rec["children"][0]
    assert c["command"].startswith(PY) and "chk/fake.py" in c["command"]
    assert c["execution"] == {"state": "PASS", "rc": 0, "secs": c["execution"]["secs"]}
    assert c["coverage"]["expected"]["i2s_periods"] == 3300
    assert c["coverage"]["observed"]["i2s_periods"] == 3300
    assert {a["path"].split("/")[-1] for a in c["artifacts"]} >= {"record.json", "log.txt"}
    assert all(len(a["sha256"]) == 64 for a in c["artifacts"])


# ---- missing asset -> NO VERDICT, before anything long runs -------------------
def test_missing_required_asset_is_no_verdict_and_runs_nothing(repo):
    """[naive-red] -- a naive wrapper runs the child, which exits 0."""
    repo.requires = ["fpga/release/baseline-2025.1.json"]
    repo.child({"rc": 0, "sleep": 30, "files": {"record.json": deadline()}})
    t0 = time.time()
    run_dir, rec = repo.run()
    assert time.time() - t0 < 10, "preflight must refuse before the long child starts"
    assert rec["verdict"] == trial.NO_VERDICT
    assert rec["execution"]["status"] == "preflight-refused"
    assert any("missing required asset: fpga/release/baseline-2025.1.json" in r
               for r in rec["verdict_reasons"])
    assert rec["children"] == []
    assert not list((run_dir).glob("c1*")), "no child directory: nothing ran"


def test_missing_tool_is_no_verdict(repo):
    repo.write_env({"no-such-simulator-xyz": {"version_arg": "-V", "version_contains": "14"}})
    repo.tools = ["no-such-simulator-xyz"]
    repo.child(ok_deadline())
    _, rec = repo.run()
    assert rec["verdict"] == trial.NO_VERDICT
    assert any("no-such-simulator-xyz" in r for r in rec["verdict_reasons"])


def test_wrong_tool_version_is_no_verdict(repo, tmp_path):
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    exe = fake_bin / "iverilog"
    exe.write_text("#!/bin/sh\necho 'Icarus Verilog version 12.0 (stable)'\n")
    exe.chmod(0o755)
    spec = {"version_arg": "-V", "version_contains": "Icarus Verilog version 14"}
    line, problem = trial_env.tool_identity("iverilog", spec, str(fake_bin))
    assert line.startswith("Icarus Verilog version 12") and "requires" in problem


def test_wrong_python_or_package_pin_refuses(repo):
    spec = json.loads(repo.env_spec.read_text())
    spec["python"] = "2.7"
    spec["packages"] = {"definitely-not-installed-pkg": "1.0"}
    problems, _ = trial_env.preflight(spec, tools=[])
    assert any("python" in p and "2.7" in p for p in problems)
    assert any("definitely-not-installed-pkg is not installed" in p for p in problems)


# ---- declared coverage failure cannot become PASS ---------------------------
def test_zero_i2s_periods_compared_cannot_pass(repo):
    """[naive-red] #248: an I2S verdict PASSed with 0 periods compared."""
    repo.child({"rc": 0, "files": {"record.json": deadline(periods=0, periods_required=0)}})
    _, rec = repo.run()
    assert rec["verdict"] == trial.NO_VERDICT
    assert any("nothing was compared" in r for r in rec["children"][0]["reasons"])


def test_truncated_i2s_cannot_pass(repo):
    """[naive-red] the checker says PASS, exit 0, but 1650 of 3300 periods."""
    repo.child({"rc": 0, "files": {"record.json": deadline(periods=1650)}})
    _, rec = repo.run()
    assert rec["verdict"] == trial.NO_VERDICT
    assert "I2S coverage 1650 of 3300" in rec["children"][0]["reasons"][0]
    assert rec["children"][0]["coverage"]["observed"]["i2s_periods"] == 1650


def test_held_note_with_no_decoded_i2s_is_no_verdict_not_fail(repo):
    """held_note_audible.py computes peak 0 from an empty I2S file and calls it
    FAIL; nothing was heard because nothing was decoded."""
    repo.child({"rc": 1, "files": {
        "held_note_audible.json": {"preset": "default", "note": 45, "legacy_image": False,
                                   "replay_status": 0, "i2s_peak_lsb": 0,
                                   "min_peak_lsb": 1024, "verdict": "FAIL"},
        "default/uart_i2s.txt": ""}}, interpret="held_note_record")
    _, rec = repo.run()
    assert rec["verdict"] == trial.NO_VERDICT
    assert "no decoded I2S" in rec["children"][0]["reasons"][0]


def test_rolling_rtl_with_zero_periods_is_no_verdict(repo):
    clean = {"demo@0": {"ok": True}}
    rr = {"state": "PASS", "comparison": {"periods": 0, "writes_sent": 508, "writes_seen": 508},
          "control": {"caught": True}}
    repo.child({"rc": 0, "files": {"verification.json": {
        "state": "PASS", "clean": clean, "controls": {}, "rtl": {"demo": rr}}}},
        interpret="rolling_record", fixtures=["demo"])
    _, rec = repo.run()
    assert rec["verdict"] == trial.NO_VERDICT
    assert "compared 0 I2S periods" in rec["children"][0]["reasons"][0]


# ---- a known valid DUT counterexample -> FAIL for the intended reason --------
def test_late_completion_counterexample_is_fail_for_the_deadline_reason(repo):
    """The real ctl-arty-late160 record, as the candidate: FAIL, deadline reason."""
    rec_late = copy.deepcopy(REAL_LATE)
    repo.child({"rc": 1, "files": {"record.json": rec_late}})
    _, rec = repo.run()
    assert rec["verdict"] == trial.FAIL
    c = rec["children"][0]
    assert c["metrics"]["deadline_failed"] is True
    assert c["metrics"]["missed"] > 0
    assert any("miss" in r.lower() or "overrun" in r.lower() for r in c["reasons"])


def test_as_candidate_runs_the_control_without_expect_fail(repo):
    repo.child(ok_deadline())
    cid = repo.child({"rc": 1, "files": {"record.json": copy.deepcopy(REAL_LATE)}},
                     role="control")
    repo.controls[0]["args"].append("--expect-fail")
    _, rec = repo.run(as_candidate=cid)
    assert rec["trial"]["candidate"] == f"control:{cid}"
    assert rec["verdict"] == trial.FAIL
    assert "--expect-fail" not in rec["children"][0]["command"]
    assert rec["controls"] == []


def test_control_failing_for_the_wrong_reason_is_not_caught(repo):
    """[naive-red] a control 'catches' only via its intended assertion: an I2S
    mismatch without a deadline miss does not kill the late-completion mutant."""
    wrong = copy.deepcopy(REAL_LATE)
    wrong["deadline_failed"] = False
    repo.child(ok_deadline())
    repo.child({"rc": 1, "files": {"record.json": wrong}}, role="control")
    _, rec = repo.run()
    assert rec["controls"][0]["caught"] is False
    assert rec["verdict"] == trial.NO_VERDICT
    assert "not caught" in rec["verdict_reasons"][0]


def test_control_that_crashed_is_not_caught(repo):
    """[naive-red] an import error is not a caught mutant (verification-rules 5)."""
    repo.child(ok_deadline())
    repo.child({"rc": 1, "print": ["Traceback: ImportError: no module named synth_ref"]},
               role="control")
    _, rec = repo.run()
    assert rec["controls"][0]["caught"] is False
    assert rec["verdict"] == trial.NO_VERDICT


def test_held_note_silent_control_caught_only_when_replay_bit_exact(repo):
    rec_ok = {"preset": "default", "note": 45, "legacy_image": True, "replay_status": 0,
              "i2s_peak_lsb": 3, "min_peak_lsb": 1024, "verdict": "FAIL"}
    repo.child({"rc": 0, "files": {"held_note_audible.json": {
        "preset": "default", "note": 45, "legacy_image": False, "replay_status": 0,
        "i2s_peak_lsb": 12760, "min_peak_lsb": 1024, "verdict": "PASS"},
        "default/uart_i2s.txt": "0 12760 12760\n1 5 5\n"}}, interpret="held_note_record")
    repo.child({"rc": 0, "files": {"held_note_audible.json": rec_ok,
                                   "default-legacy/uart_i2s.txt": "0 3 3\n"}},
               interpret="held_note_record", role="control")
    _, rec = repo.run()
    assert rec["verdict"] == trial.PASS, rec["verdict_reasons"]
    assert rec["controls"][0]["caught"] is True


# ---- exit status and evidence must agree ------------------------------------
def test_record_says_fail_but_exit_zero_is_no_verdict(repo):
    """[naive-red] exit 0 alone is not PASS."""
    rec_late = copy.deepcopy(REAL_LATE)
    repo.child({"rc": 0, "files": {"record.json": rec_late}})
    _, rec = repo.run()
    assert rec["verdict"] == trial.NO_VERDICT
    assert "disagrees" in rec["children"][0]["reasons"][0]


def test_exit_zero_with_no_record_is_no_verdict(repo):
    """[naive-red]"""
    repo.child({"rc": 0, "print": ["verify_deadline: PASS"]})
    _, rec = repo.run()
    assert rec["verdict"] == trial.NO_VERDICT
    assert "wrote no record" in rec["children"][0]["reasons"][0]


def test_traceback_exit_one_is_not_a_fail(repo):
    """A Python traceback exits 1 -- the same code as a real mismatch."""
    repo.child({"rc": 1, "print": ["Traceback (most recent call last):", "KeyError: 'x'"]},
               interpret="bound_text")
    _, rec = repo.run()
    assert rec["verdict"] == trial.NO_VERDICT


@pytest.mark.parametrize("line,rc,want", [
    ("release_manifest: BOUND -- equals a fresh derivation", 0, trial.PASS),
    ("release_manifest: STALE -- differs", 1, trial.FAIL),
    ("release_manifest: REFUSED -- missing input: arty.bit", 2, trial.NO_VERDICT),
    ("release_manifest: BOUND -- equals", 1, trial.NO_VERDICT),
    ("release_manifest: STALE -- differs", 0, trial.NO_VERDICT),
])
def test_bound_text_needs_token_and_exit_to_agree(repo, line, rc, want):
    repo.child({"rc": rc, "print": [line]}, interpret="bound_text",
               token_prefix="release_manifest: ")
    repo.child(late_control(), role="control")
    _, rec = repo.run()
    assert rec["verdict"] == want, rec["verdict_reasons"]


def test_binding_checker_format_is_read(repo):
    repo.child({"rc": 0, "print": ["BOUND: arty_a7_top -> fpga/reports/arty/drift-clean/"
                                   "verification.json covers every compiled source"]},
               interpret="bound_text", token_prefix="")
    repo.child(late_control(), role="control")
    _, rec = repo.run()
    assert rec["verdict"] == trial.PASS


def test_rolling_missed_control_is_no_verdict_but_a_real_mismatch_is_fail(repo):
    base = {"state": "FAIL", "clean": {"demo@0": {"ok": True}},
            "controls": {"corrupt-byte": {"caught": False}},
            "rtl": {"demo": {"state": "PASS", "control": {"caught": True}, "comparison": {
                "periods": 257185, "writes_sent": 508, "writes_seen": 508}}}}
    repo.child({"rc": 1, "files": {"verification.json": base}},
               interpret="rolling_record", fixtures=["demo"])
    _, rec = repo.run()
    assert rec["verdict"] == trial.NO_VERDICT, "a missed control is the apparatus, not the DUT"
    bad = copy.deepcopy(base)
    bad["controls"] = {}
    bad["rtl"]["demo"]["comparison"]["wire_mismatch"] = 122777
    (repo.root / "second").mkdir()
    repo2 = Repo(repo.root / "second")
    repo2.child({"rc": 1, "files": {"verification.json": bad}},
                interpret="rolling_record", fixtures=["demo"])
    _, rec2 = repo2.run()
    assert rec2["verdict"] == trial.FAIL
    assert "wire_mismatch" in rec2["children"][0]["reasons"][0]


# ---- child failure is not masked by a later success -------------------------
def test_child_failure_not_masked_by_later_success(repo):
    """[naive-red] the shell `a; b` reports b. Children here are separate processes."""
    repo.child({"rc": 1, "files": {"record.json": copy.deepcopy(REAL_LATE)}})
    repo.child(ok_deadline())
    _, rec = repo.run()
    assert [c["verdict"] for c in rec["children"]] == [trial.FAIL, trial.PASS]
    assert rec["verdict"] == trial.FAIL


def test_one_unknown_child_among_passes_is_no_verdict(repo):
    repo.child(ok_deadline())
    repo.child({"rc": 0, "files": {"record.json": deadline(periods=10)}})
    _, rec = repo.run()
    assert rec["verdict"] == trial.NO_VERDICT
    assert rec["coverage"]["with_valid_verdict"] == 1 and rec["coverage"]["passing"] == 1


# ---- timeout and cancellation leave no reusable PASS ------------------------
def test_timeout_is_no_verdict_even_if_a_pass_record_exists(repo):
    """[naive-red] the child wrote a PASS record, then hung. It is not read."""
    repo.child({"rc": 0, "pre_files": {"record.json": deadline()}, "sleep": 30})
    t0 = time.time()
    run_dir, rec = repo.run(timeout=2)
    assert time.time() - t0 < 20
    assert (run_dir / "c1" / "record.json").exists(), "the PASS record is on disk"
    assert rec["verdict"] == trial.NO_VERDICT
    assert rec["execution"]["status"] == "timeout"
    assert "timed out" in rec["children"][0]["reasons"][0]
    ok, problems, _ = trial.check_receipt(run_dir / "receipt.json")
    assert ok, problems                        # a valid receipt -- of NO VERDICT
    assert rec["verdict"] != trial.PASS


def test_cancel_kills_children_and_leaves_no_pass(repo):
    repo.child({"rc": 0, "sleep": 60, "files": {"record.json": deadline()}})
    reg = repo.registry(timeout=120)
    driver = textwrap.dedent(f"""
        import sys, signal, pathlib
        sys.path.insert(0, {str(HERE)!r})
        import trial
        c = trial._Cancel()
        signal.signal(signal.SIGTERM, c.handler)
        d, r = trial.run_trial("T-X", root=pathlib.Path({str(repo.root)!r}),
            registry=pathlib.Path({str(reg)!r}),
            dag=pathlib.Path({str(repo.root / 'docs/dag.json')!r}),
            env_spec=pathlib.Path({str(repo.env_spec)!r}),
            out_base=pathlib.Path({str(repo.root / 'build/trials')!r}), cancel=c)
        print(d)
    """)
    p = subprocess.Popen([PY, "-c", driver], stdout=subprocess.PIPE, text=True)
    deadline_t = time.time() + 20
    started = []
    while time.time() < deadline_t and not started:
        started = list((repo.root / "build/trials").rglob("started"))
        time.sleep(0.1)
    assert started, "the child never started"
    in_progress = json.loads(next((repo.root / "build/trials").rglob("receipt.json")).read_text())
    assert in_progress["verdict"] == trial.NO_VERDICT
    assert in_progress["execution"]["status"] == "in-progress"
    t0 = time.time()
    p.send_signal(signal.SIGTERM)
    out, _ = p.communicate(timeout=30)
    assert time.time() - t0 < 15, "cancellation must not wait for the 60 s child"
    run_dir = pathlib.Path(out.strip().splitlines()[-1])
    rec = json.loads((run_dir / "receipt.json").read_text())
    assert rec["verdict"] == trial.NO_VERDICT
    assert rec["execution"]["status"] == "cancelled"
    assert not (run_dir / "c1" / "record.json").exists(), "the child was killed before writing"


def test_killed_run_leaves_only_an_in_progress_no_verdict(repo):
    """SIGKILL cannot be handled; what is on disk must still not be a PASS."""
    repo.child({"rc": 0, "sleep": 60, "files": {"record.json": deadline()}})
    reg = repo.registry(timeout=120)
    driver = (f"import sys,pathlib; sys.path.insert(0,{str(HERE)!r}); import trial; "
              f"trial.run_trial('T-X', root=pathlib.Path({str(repo.root)!r}), "
              f"registry=pathlib.Path({str(reg)!r}), dag=pathlib.Path({str(repo.root / 'docs/dag.json')!r}), "
              f"env_spec=pathlib.Path({str(repo.env_spec)!r}), "
              f"out_base=pathlib.Path({str(repo.root / 'build/trials')!r}))")
    p = subprocess.Popen([PY, "-c", driver], start_new_session=True)
    t_end = time.time() + 20
    while time.time() < t_end and not list((repo.root / "build/trials").rglob("started")):
        time.sleep(0.1)
    os.killpg(p.pid, signal.SIGKILL)
    p.wait(timeout=10)
    receipt = next((repo.root / "build/trials").rglob("receipt.json"))
    ok, problems, rec = trial.check_receipt(receipt)
    assert rec["verdict"] == trial.NO_VERDICT and rec["execution"]["status"] == "in-progress"


# ---- altered evidence is rejected -------------------------------------------
def _pass_run(repo):
    repo.child(ok_deadline())
    repo.child(late_control(), role="control")
    run_dir, rec = repo.run()
    assert rec["verdict"] == trial.PASS
    return run_dir


def test_altered_artifact_is_rejected(repo):
    run_dir = _pass_run(repo)
    record = run_dir / "c1" / "record.json"
    data = json.loads(record.read_text())
    data["facts"]["wire_mismatch"] = 7
    record.write_text(json.dumps(data))
    ok, problems, _ = trial.check_receipt(run_dir / "receipt.json")
    assert not ok and any("artifact altered: c1/record.json" in p for p in problems)


def test_deleted_artifact_is_rejected(repo):
    run_dir = _pass_run(repo)
    (run_dir / "c1" / "log.txt").unlink()
    ok, problems, _ = trial.check_receipt(run_dir / "receipt.json")
    assert not ok and any("artifact missing" in p for p in problems)


def test_edited_verdict_is_rejected_even_when_resealed(repo):
    repo.child({"rc": 1, "files": {"record.json": copy.deepcopy(REAL_LATE)}})
    run_dir, rec = repo.run()
    assert rec["verdict"] == trial.FAIL
    path = run_dir / "receipt.json"
    edited = json.loads(path.read_text())
    edited["verdict"] = trial.PASS
    path.write_text(json.dumps(edited))
    ok, problems, _ = trial.check_receipt(path)
    assert not ok and any("edited" in p for p in problems)
    path.write_text(json.dumps(trial.seal(edited)))       # a forger who re-hashes
    ok, problems, _ = trial.check_receipt(path)
    assert not ok and any("not what its children imply" in p for p in problems)


def _forge(path, rec):
    path.write_text(json.dumps(trial.seal(rec)))          # a forger who re-hashes
    return trial.check_receipt(path)


def test_forged_child_verdict_is_rejected_even_when_resealed(repo):
    """The Judge's reproduction on 4eb4e72: the candidate wrote REAL_LATE and
    exited 1 (a real FAIL); the child's verdict AND the composite are flipped
    to PASS and re-sealed, artifacts untouched. Before the fix: VALID."""
    repo.child({"rc": 1, "files": {"record.json": copy.deepcopy(REAL_LATE)}})
    repo.child(late_control(), role="control")
    run_dir, rec = repo.run()
    assert rec["verdict"] == trial.FAIL and rec["controls"][0]["caught"] is True
    path = run_dir / "receipt.json"
    forged = json.loads(path.read_text())
    for c in forged["children"]:
        c["verdict"] = trial.PASS
    forged["verdict"] = trial.PASS
    assert trial.composite(forged["children"], forged["controls"])[0] == trial.PASS, \
        "the forgery is self-consistent: only re-derivation can see it"
    ok, problems, _ = _forge(path, forged)
    assert not ok
    assert any("c1: the receipt says verdict PASS" in p and "re-derives to FAIL" in p
               for p in problems), problems


def test_forged_control_catch_is_rejected_even_when_resealed(repo):
    """A control that was NOT caught (wrong reason) is marked caught and the
    composite flipped from NO VERDICT to PASS."""
    wrong = copy.deepcopy(REAL_LATE)
    wrong["deadline_failed"] = False
    repo.child(ok_deadline())
    repo.child({"rc": 1, "files": {"record.json": wrong}}, role="control")
    run_dir, rec = repo.run()
    assert rec["verdict"] == trial.NO_VERDICT
    path = run_dir / "receipt.json"
    forged = json.loads(path.read_text())
    forged["controls"][0]["caught"] = True
    forged["verdict"] = trial.PASS
    ok, problems, _ = _forge(path, forged)
    assert not ok and any("caught True" in p and "caught False" in p for p in problems), problems


def test_forged_record_added_where_none_was_written_is_rejected(repo):
    """A child that wrote no record (NO VERDICT) gets one planted afterwards:
    the planted file is not among the receipt's artifacts."""
    repo.child({"rc": 0, "print": ["verify_deadline: PASS"]})
    repo.child(late_control(), role="control")
    run_dir, rec = repo.run()
    assert rec["verdict"] == trial.NO_VERDICT
    (run_dir / "c1" / "record.json").write_text(json.dumps(deadline()))
    path = run_dir / "receipt.json"
    forged = json.loads(path.read_text())
    forged["children"][0]["verdict"] = trial.PASS
    forged["verdict"] = trial.PASS
    ok, problems, _ = _forge(path, forged)
    assert not ok and any("unlisted file in the run directory: c1/record.json" in p
                          for p in problems), problems


def test_failing_child_moved_into_controls_is_rejected(repo):
    repo.child({"rc": 1, "files": {"record.json": copy.deepcopy(REAL_LATE)}})
    repo.child(late_control(), role="control")
    run_dir, _ = repo.run()
    path = run_dir / "receipt.json"
    forged = json.loads(path.read_text())
    forged["controls"].append(forged["children"].pop(0))
    ok, problems, _ = _forge(path, forged)
    assert not ok and any("is listed under controls" in p for p in problems), problems


def test_receipt_without_a_recorded_interpreter_is_rejected(repo):
    """A trial-receipt/1 receipt (no interpreter spec) cannot be re-derived."""
    run_dir = _pass_run(repo)
    path = run_dir / "receipt.json"
    old = json.loads(path.read_text())
    del old["children"][0]["interpreter"]
    ok, problems, _ = _forge(path, old)
    assert not ok and any("cannot be re-derived" in p for p in problems), problems


def test_pass_from_an_incomplete_execution_is_rejected(repo):
    run_dir = _pass_run(repo)
    path = run_dir / "receipt.json"
    rec = json.loads(path.read_text())
    rec["execution"]["status"] = "timeout"
    path.write_text(json.dumps(trial.seal(rec)))
    ok, problems, _ = trial.check_receipt(path)
    assert not ok and any("did not complete" in p for p in problems)


def test_tilde_pass_is_not_a_verdict(repo):
    run_dir = _pass_run(repo)
    path = run_dir / "receipt.json"
    rec = json.loads(path.read_text())
    rec["verdict"] = "~PASS"
    path.write_text(json.dumps(trial.seal(rec)))
    ok, problems, _ = trial.check_receipt(path)
    assert not ok and any("is not one of" in p for p in problems)


def test_retained_evidence_altered_is_refused_before_the_checker_runs(repo):
    """The reanalyse mode's staging: a trace whose sha256 differs from the record
    that retained it is NO VERDICT, and the checker never sees it."""
    import gzip, hashlib
    traces = repo.root / "traces"
    traces.mkdir()
    good = b"0 1 1\n1 2 2\n"
    (traces / "uart_i2s.txt.gz").write_bytes(gzip.compress(good))
    hashes = repo.root / "hashes.json"
    hashes.write_text(json.dumps({"reanalysis": {"capture_sha256": {
        "uart_i2s.txt": hashlib.sha256(good).hexdigest()}}}))
    st = {"gunzip": "traces", "hashes": "hashes.json", "hash_key": ["reanalysis", "capture_sha256"],
          "into": "capture"}
    repo.child(ok_deadline(), stage=st)
    repo.child(late_control(), role="control")
    _, rec = repo.run()
    assert rec["verdict"] == trial.PASS
    (traces / "uart_i2s.txt.gz").write_bytes(gzip.compress(b"0 1 1\n"))   # truncated
    _, rec = repo.run()
    assert rec["verdict"] == trial.NO_VERDICT
    c = rec["children"][0]
    assert c["execution"]["state"] == "NOT-RUN"
    assert "retained evidence altered: uart_i2s.txt" in c["reasons"][0]


# ---- a mode with no control cannot PASS -------------------------------------
def test_no_control_declared_is_no_verdict_not_pass(repo):
    """[naive-red] 4eb4e72 PASSed a mode with `"controls": []` (T-RELEASE-BOUND):
    nothing showed the apparatus able to fail (verification-rules 2)."""
    repo.child(ok_deadline())
    run_dir, rec = repo.run()
    assert rec["children"][0]["verdict"] == trial.PASS
    assert rec["verdict"] == trial.NO_VERDICT
    assert "no control declared" in rec["verdict_reasons"][0]
    ok, problems, _ = trial.check_receipt(run_dir / "receipt.json")
    assert ok, problems                              # a valid receipt -- of NO VERDICT


def test_no_control_declared_still_reports_a_conclusive_fail(repo):
    repo.child({"rc": 1, "files": {"record.json": copy.deepcopy(REAL_LATE)}})
    _, rec = repo.run()
    assert rec["verdict"] == trial.FAIL


def test_committed_release_bound_is_no_verdict_until_it_has_a_control():
    """T-RELEASE-BOUND declares no control (none can exist until #255 lands
    release_manifest.py), so even with every required child BOUND it is NO VERDICT."""
    mode = trial.load_registry()["trials"]["T-RELEASE-BOUND"]["modes"]["check"]
    assert mode["controls"] == []
    passing = [{"id": c["id"], "verdict": trial.PASS, "reasons": []} for c in mode["required"]]
    assert trial.composite(passing, [])[0] == trial.NO_VERDICT


@pytest.mark.parametrize("line,rc,caught", [
    ("release_manifest: STALE -- differs", 1, True),
    ("release_manifest: BOUND -- equals", 0, False),       # the counterexample was not seen
    ("release_manifest: STALE -- differs", 0, False),      # printed and exit disagree
    ("release_manifest: REFUSED -- missing input", 2, False),
    ("Traceback (most recent call last):", 1, False),     # a crash is not a catch
])
def test_bound_text_control_is_caught_only_by_stale_with_exit_one(repo, line, rc, caught):
    """The control T-RELEASE-BOUND needs once #255 lands: a STALE fixture."""
    repo.child({"rc": 0, "print": ["release_manifest: BOUND -- equals"]}, interpret="bound_text",
               token_prefix="release_manifest: ")
    repo.child({"rc": rc, "print": [line]}, interpret="bound_text",
               token_prefix="release_manifest: ", role="control")
    run_dir, rec = repo.run()
    assert rec["controls"][0]["caught"] is caught
    assert rec["verdict"] == (trial.PASS if caught else trial.NO_VERDICT)
    ok, problems, _ = trial.check_receipt(run_dir / "receipt.json")
    assert ok, problems


# ---- cross-environment comparison -------------------------------------------
def test_compare_same_inputs_agree_and_a_moved_number_disagrees(repo):
    repo.child(ok_deadline())
    _, a = repo.run()
    _, b = repo.run()
    assert trial.compare(a, b) == []
    moved = copy.deepcopy(b)
    moved["children"][0]["metrics"]["worst_sample_slack"] = 13
    assert any("metrics" in d for d in trial.compare(a, moved))


# ---- the registry itself ----------------------------------------------------
def test_registry_refuses_copied_commands_and_unknown_dag_nodes(repo):
    repo.child(ok_deadline())
    reg = repo.registry()
    data = json.loads(reg.read_text())
    data["trials"]["T-X"]["modes"]["m"]["required"][0]["cmd"] = "python x.py"
    reg.write_text(json.dumps(data))
    with pytest.raises(trial.RegistryError, match="must not carry 'cmd'"):
        trial.load_registry(reg, repo.root / "docs/dag.json")
    del data["trials"]["T-X"]["modes"]["m"]["required"][0]["cmd"]
    data["trials"]["T-X"]["dag_nodes"] = ["NOPE"]
    reg.write_text(json.dumps(data))
    with pytest.raises(trial.RegistryError, match="NOPE"):
        trial.load_registry(reg, repo.root / "docs/dag.json")


def test_the_committed_registry_loads_and_names_the_three_pilot_trials():
    reg = trial.load_registry()
    # the three pilot trials, and T-LIVE-MIDI (#281) registered after them
    assert set(reg["trials"]) == {"T-RELEASE-BOUND", "T-DEADLINE", "T-PLAY-DIGITAL",
                                  "T-LIVE-MIDI"}
    for tid, t in reg["trials"].items():
        for mode in t["modes"].values():
            for c in mode["required"] + mode.get("controls", []):
                assert not pathlib.Path(c["checker"]).is_absolute()


def test_bootstrap_plans_nothing_when_satisfied():
    spec = trial_env.load_spec()
    pins = dict(spec["packages"])
    assert trial_env.plan_bootstrap(spec, python_version=spec["python"] + ".7", packages=pins,
                                    toolchain_ok=True, need_toolchain=True) == []
    assert trial_env.plan_bootstrap(spec, python_version=spec["python"] + ".7",
                                    packages=dict(pins, numpy="0.0"), toolchain_ok=False,
                                    need_toolchain=True) == ["packages", "toolchain"]


# ---- the control on this suite: the rule it replaces must be red here -------
def _naive_judge(spec, run, out, role, *, cancelled=False):
    """'exit 0 means PASS' -- the wrapper plan085 section 4 rules out."""
    rc = run["rc"]
    r = trial._result(trial.PASS if rc == 0 else trial.FAIL if rc == 1 else trial.NO_VERDICT, [])
    if role == "control":
        r["caught"] = rc == 0
    return r


@pytest.mark.parametrize("behaviour,interpret", [
    ({"rc": 0, "files": {"record.json": deadline(periods=0, periods_required=0)}}, "deadline_record"),
    ({"rc": 0, "files": {"record.json": deadline(periods=1650)}}, "deadline_record"),
    ({"rc": 0, "files": {"record.json": copy.deepcopy(REAL_LATE)}}, "deadline_record"),
    ({"rc": 0, "print": ["verify_deadline: PASS"]}, "deadline_record"),
    ({"rc": 0, "print": ["release_manifest: STALE -- differs"]}, "bound_text"),
])
def test_the_naive_exit_code_rule_would_certify_each_of_these(repo, monkeypatch, behaviour,
                                                               interpret):
    """Each case is a false PASS under the naive rule and not a PASS here, so the
    suite above discriminates rather than agreeing with whatever it wraps."""
    repo.child(behaviour, interpret=interpret, token_prefix="release_manifest: ")
    repo.child(late_control(), role="control")
    _, real = repo.run()
    assert real["verdict"] != trial.PASS
    monkeypatch.setattr(trial, "judge_child", _naive_judge)
    _, naive = repo.run()
    assert naive["verdict"] == trial.PASS


# ---- T-LIVE-MIDI's interpreter (fpga/verify_live_midi.py's record) -----------
def _lm_record(tmp_path, **over):
    lat = {"n": 905, "p50_ms": 16.0, "p95_ms": 16.7, "p99_ms": 17.0, "max_ms": 17.0}
    rec = {"verdict": "PASS",
           "clean": {n: {"verdict": "PASS", "expected": {"timed": 10},
                         **({"latency": lat} if n == "sustained" else {})}
                     for n in ("coverage", "pressure", "sustained")},
           "controls": {"DROP_NOTE_OFF": {"caught": True}}}
    rec.update(over)
    (tmp_path / "verification.json").write_text(json.dumps(rec))
    return tmp_path


def test_live_midi_pass_needs_every_scenario_and_a_caught_control(tmp_path):
    spec = {"interpret": "live_midi_record"}
    run = {"rc": 0}
    assert trial.interpret_live_midi_record(spec, run, _lm_record(tmp_path), "required")[
        "verdict"] == trial.PASS
    missed = _lm_record(tmp_path, controls={"DROP_NOTE_OFF": {"caught": False}})
    assert trial.interpret_live_midi_record(spec, run, missed, "required")[
        "verdict"] == trial.NO_VERDICT
    empty = _lm_record(tmp_path, clean={"coverage": {"verdict": "PASS", "expected": {"timed": 0}}})
    assert trial.interpret_live_midi_record(spec, run, empty, "required")[
        "verdict"] == trial.NO_VERDICT


def test_live_midi_rtl_with_zero_periods_is_no_verdict(tmp_path):
    spec = {"interpret": "live_midi_record", "fixtures": ["coverage"]}
    d = _lm_record(tmp_path, rtl={"coverage": {"state": "PASS", "comparison": {"periods": 0}}})
    assert trial.interpret_live_midi_record(spec, {"rc": 0}, d, "required")[
        "verdict"] == trial.NO_VERDICT


def test_live_midi_control_is_caught_only_with_its_exit_zero(tmp_path):
    spec = {"interpret": "live_midi_record"}
    rec = {"verdict": "FAIL", "control": {"control": "X", "caught": True, "verdict": "FAIL"}}
    (tmp_path / "verification.json").write_text(json.dumps(rec))
    assert trial.interpret_live_midi_record(spec, {"rc": 0}, tmp_path, "control")["caught"]
    assert not trial.interpret_live_midi_record(spec, {"rc": 1}, tmp_path, "control")["caught"]
