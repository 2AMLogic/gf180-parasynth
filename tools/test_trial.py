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
import importlib.metadata
import json
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import textwrap
import time
import venv

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

    def registry(self, timeout=60, *, tid="T-X", mode="m"):
        reg = {"schema": 1, "environment": "spec/env.json", "trials": {tid: {
            "question": "q?", "criterion_version": "x/1", "scope": {"c": 1},
            "dag_nodes": ["X"], "modes": {mode: {
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


@pytest.fixture
def trial_cli(repo):
    # The CLI resolves its registry relative to its source tree at import time.
    tools = repo.root / "tools"
    tools.mkdir()
    for name in (
        "trial.py",
        "capture_commands.py",
        "provenance.py",
        "run_all.py",
        "exit_contract.py",
        "trial_env.py",
    ):
        shutil.copy(HERE / name, tools)

    def invoke(*args):
        return subprocess.run(
            [PY, tools / "trial.py", *args],
            cwd=repo.root,
            text=True,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

    return invoke


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
    rr = {"state": "PASS", "comparison": {"periods": 0, "periods_required": 257185,
                                          "writes_sent": 508, "writes_seen": 508},
          "control": {"caught": True}, "truncation_control": {"caught": True}}
    repo.child({"rc": 0, "files": {"verification.json": {
        "state": "PASS", "clean": clean, "controls": {}, "rtl": {"demo": rr}}}},
        interpret="rolling_record", fixtures=["demo"])
    _, rec = repo.run()
    assert rec["verdict"] == trial.NO_VERDICT
    assert any("compared 0 I2S periods" in r for r in rec["children"][0]["reasons"])


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
              "i2s_peak_lsb": 3, "min_peak_lsb": 1024, "verdict": "FAIL",
              "periods": 2, "periods_required": 2, "truncation_control": {"caught": True}}
    repo.child({"rc": 0, "files": {"held_note_audible.json": {
        "preset": "default", "note": 45, "legacy_image": False, "replay_status": 0,
        "i2s_peak_lsb": 12760, "min_peak_lsb": 1024, "verdict": "PASS",
        "periods": 2, "periods_required": 2, "truncation_control": {"caught": True}},
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
            "rtl": {"demo": {"state": "PASS", "control": {"caught": True},
                             "truncation_control": {"caught": True}, "comparison": {
                "periods": 257185, "periods_required": 257185, "writes_sent": 508,
                "writes_seen": 508}}}}
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


def test_check_all_rejects_altered_control_with_object_reason_and_checks_next(repo, capsys):
    source = _pass_run(repo)
    batch = repo.root / "batch"
    altered = shutil.copytree(source, batch / "a-altered")
    valid = shutil.copytree(source, batch / "b-valid")
    record = altered / "c2" / "record.json"
    data = json.loads(record.read_text())
    data.update(status="PASS", reasons=[{"detail": "unexpected schedule"}])
    record.write_text(json.dumps(data))

    assert trial.main(["check-all", str(batch)]) == 1
    out = capsys.readouterr().out
    assert "artifact altered: c2/record.json" in out
    assert "re-derives to PASS caught False" in out and "unexpected schedule" in out
    rejected, accepted = [line for line in out.splitlines() if line.startswith(("REJECTED", "VALID"))]
    assert rejected.startswith("REJECTED") and rejected.endswith(str(altered / "receipt.json"))
    assert accepted.startswith("VALID") and accepted.endswith(str(valid / "receipt.json"))
    assert out.splitlines()[-1] == "1/2 receipts valid"

    assert trial.main(["check-receipt", str(altered / "receipt.json")]) == 1
    assert "unexpected schedule" in capsys.readouterr().out


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


def test_committed_release_bound_can_pass_only_with_its_stale_controls_caught():
    """T-RELEASE-BOUND was NO VERDICT with every child BOUND while it declared no
    control; #278 added two STALE counterexamples (fpga/release/stale_controls.py,
    tested on the real tree in fpga/release/test_stale_controls.py). It PASSes only
    when both are caught; an uncaught one is NO VERDICT, never PASS."""
    mode = trial.load_registry()["trials"]["T-RELEASE-BOUND"]["modes"]["check"]
    assert {c["id"] for c in mode["controls"]} == {"stale-manifest", "stale-binding"}
    passing = [{"id": c["id"], "verdict": trial.PASS, "reasons": []} for c in mode["required"]]
    assert trial.composite(passing, [])[0] == trial.NO_VERDICT
    caught = [{"id": c["id"], "caught": True, "reasons": []} for c in mode["controls"]]
    assert trial.composite(passing, caught)[0] == trial.PASS
    caught[1]["caught"] = False
    assert trial.composite(passing, caught)[0] == trial.NO_VERDICT


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
@pytest.mark.parametrize("args", [("list",), ("run", "T-PLAY-DIGITAL")])
@pytest.mark.parametrize(
    "contents,reason",
    [
        ('{"trials":', "invalid JSON"),
        ("{}", "missing 'trials'"),
    ],
)
def test_cli_refuses_malformed_registry(repo, trial_cli, args, contents, reason):
    registry = repo.root / "docs/trials.json"
    shutil.copyfile(REAL_ROOT / "docs/trials.json", registry)
    shutil.copyfile(REAL_ROOT / "docs/dag.json", repo.root / "docs/dag.json")
    listed = trial_cli("list")
    assert listed.returncode == 0, listed.stdout
    assert any(
        line.startswith("T-PLAY-DIGITAL ") for line in listed.stdout.splitlines()
    )

    registry.write_text(contents)
    result = trial_cli(*args)
    assert result.returncode == 2, result.stdout
    (line,) = result.stdout.splitlines()
    assert line.startswith(f"REFUSED: {registry}: {reason}")
    assert not list(repo.root.rglob("receipt.json"))


@pytest.mark.parametrize(
    "args,reason",
    [
        (["run", "T-NO-SUCH-TRIAL"], "unknown trial 'T-NO-SUCH-TRIAL'"),
        (["run", "T-X", "--mode", "nope"], "T-X has no mode 'nope'"),
        (["run", "T-X", "--as-candidate", "nope"], "no control 'nope' in this mode"),
        (["list"], "T-X: missing 'criterion_version'"),
    ],
)
def test_cli_refuses_invalid_request(repo, trial_cli, args, reason):
    repo.child(ok_deadline())
    registry = repo.registry()
    if args == ["list"]:
        data = json.loads(registry.read_text())
        del data["trials"]["T-X"]["criterion_version"]
        registry.write_text(json.dumps(data))

    result = trial_cli(*args)
    assert result.returncode == 2, result.stdout
    (line,) = result.stdout.splitlines()
    assert line.startswith(f"REFUSED: {reason}")
    assert not (repo.root / "build/trials").exists()


@pytest.mark.parametrize(
    "verdict,rc", [(trial.PASS, 0), (trial.FAIL, 1), (trial.NO_VERDICT, 2)]
)
def test_cli_reports_receipt_for_executed_trial(repo, trial_cli, verdict, rc):
    if verdict == trial.NO_VERDICT:
        (repo.root / "tools/crash.py").write_text(
            "from trial import RegistryError\nraise RegistryError('child crashed')\n"
        )
        repo.child({}, checker="tools/crash.py")
    elif verdict == trial.FAIL:
        repo.child(_failing_late())
    else:
        repo.child(ok_deadline())
    repo.child(late_control(), role="control")
    repo.registry()

    result = trial_cli("run", "T-X")
    assert result.returncode == rc, result.stdout
    (receipt,) = (repo.root / "build/trials/T-X").glob("*/receipt.json")
    assert f"receipt: {receipt}" in result.stdout
    ok, problems, rec = trial.check_receipt(receipt, root=repo.root)
    assert ok, problems
    assert rec["verdict"] == verdict
    if verdict == trial.NO_VERDICT:
        assert (
            "RegistryError: child crashed"
            in (receipt.parent / "c1/log.txt").read_text()
        )


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
    # the three pilot trials, T-LIVE-MIDI (#281), T-RELEASE-BOUND-R1 (#280) and
    # T-PHYSICAL (#208) registered after them
    assert set(reg["trials"]) == {"T-RELEASE-BOUND", "T-DEADLINE", "T-PLAY-DIGITAL",
                                  "T-LIVE-MIDI", "T-RELEASE-BOUND-R1", "T-PHYSICAL",
                                  "T-RELEASE-BOUND-R2"}
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


# ---- symlinked interpreters: one binary, several package sets (#1582) --------
DIST = "zz-trial-env-symlink-fixture"


def _python(home: pathlib.Path) -> str:
    return str(home / "bin" / "python")


def _symlinked_venv(home: pathlib.Path, packages: dict) -> pathlib.Path:
    """A venv whose bin/python is a symlink to this process's own interpreter and
    which holds exactly `packages`, as .dist-info/METADATA. Returns `home`."""
    venv.EnvBuilder(symlinks=True, with_pip=False).create(home)
    py = home / "bin" / "python"
    assert py.is_symlink(), f"{py} was copied, not symlinked: not the #1582 shape"
    r = subprocess.run([py, "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
                       capture_output=True, text=True, check=True)
    for name, version in packages.items():
        info = pathlib.Path(r.stdout.strip()) / f"{name.replace('-', '_')}-{version}.dist-info"
        info.mkdir(parents=True)
        (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
    return home


@pytest.fixture
def twin_venvs(tmp_path):
    """Two venvs that are THIS interpreter by another path, with different
    packages, and a caller (this process) that has neither. Asserted, not
    assumed: against a copied venv every test below would pass for nothing."""
    a = _symlinked_venv(tmp_path / "a", {DIST: "1.0"})
    b = _symlinked_venv(tmp_path / "b", {DIST: "2.0"})
    assert [os.path.realpath(_python(v)) for v in (a, b)] == [os.path.realpath(sys.executable)] * 2
    with pytest.raises(importlib.metadata.PackageNotFoundError):
        importlib.metadata.version(DIST)
    return a, b


def _spec(packages: dict) -> dict:
    return {"version": "linked-env/1", "python": "%d.%d" % sys.version_info[:2],
            "packages": packages, "toolchain": {"bin": "nobin", "tools": {}}}


def test_the_named_symlinked_venv_is_judged_by_its_own_packages(twin_venvs):
    """`package_versions` and `preflight` answer for the interpreter they name,
    never for the one calling (#1582)."""
    a, b = twin_venvs
    spec = _spec({DIST: "2.0"})
    assert trial_env.package_versions(spec, _python(a)) == {DIST: "1.0"}
    assert trial_env.package_versions(spec, _python(b)) == {DIST: "2.0"}
    assert trial_env.package_versions(spec) == {DIST: None}
    problems, ident = trial_env.preflight(spec, tools=[], python=_python(b))
    assert problems == [] and ident["packages"] == {DIST: "2.0"}
    problems, _ = trial_env.preflight(spec, tools=[], python=_python(a))
    assert problems == [f"package {DIST} is 1.0; the spec pins 2.0"]


def test_from_one_symlinked_venv_another_is_judged_by_its_own_packages(twin_venvs):
    """The caller is a venv too, as when tools/item9_cold_start.py runs
    trial_env: under a, asked about b, both one realpath, the answer is b's."""
    a, b = twin_venvs
    code = textwrap.dedent("""
        import json, os, sys
        sys.path.insert(0, sys.argv[1])
        import trial_env
        spec = json.loads(sys.argv[2])
        assert os.path.realpath(sys.argv[3]) == os.path.realpath(sys.executable)
        problems, ident = trial_env.preflight(spec, tools=[], python=sys.argv[3])
        print(json.dumps([ident["packages"], problems]))
        """)
    r = subprocess.run([_python(a), "-c", code, str(HERE), json.dumps(_spec({DIST: "2.0"})),
                        _python(b)], capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == [{DIST: "2.0"}, []]


def test_bootstrap_from_outside_a_satisfied_symlinked_venv_plans_nothing(twin_venvs, tmp_path,
                                                                        capsys, monkeypatch):
    """The issue's reproduction: `bootstrap --venv V --no-toolchain`, run by an
    interpreter that lacks V's packages, plans nothing and says SATISFIED."""
    _, b = twin_venvs
    monkeypatch.setenv("PATH", str(tmp_path / "no-bin"))
    rc = trial_env.bootstrap(_spec({DIST: "2.0"}), venv=b, need_toolchain=False, root=tmp_path)
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "actions: []" in out and "SATISFIED" in out and "REFUSED" not in out


def test_package_versions_of_nothing_asked_and_of_a_target_that_cannot_answer(tmp_path):
    broken = tmp_path / "not-python"
    broken.write_text("#!/bin/sh\nexit 3\n")
    broken.chmod(0o755)
    assert trial_env.package_versions(_spec({})) == {}
    assert trial_env.package_versions(_spec({}), str(broken)) == {}
    assert trial_env.package_versions(_spec({DIST: "2.0"}), str(broken)) == {DIST: None}


def _pre_fix_package_versions(spec, python=None):
    """package_versions as it stood at 09e1a041: its in-process branch verbatim.
    The subprocess branch -- the correct one -- is replaced by a refusal, so
    this cannot come out right by falling through to it."""
    names = list(spec["packages"])
    if python is None or os.path.realpath(python) == os.path.realpath(sys.executable):
        from importlib import metadata
        out = {}
        for n in names:
            try:
                out[n] = metadata.version(n)
            except metadata.PackageNotFoundError:
                out[n] = None
        return out
    raise AssertionError("the pre-fix shortcut did not fire: not the #1582 shape")


def test_control_the_pre_fix_realpath_shortcut_answered_for_the_caller(twin_venvs):
    """The bug as it shipped, kept red (#1582; verification-rules rule 5): one
    realpath, so the pre-fix code read the caller -- which has neither package
    set -- for both targets, and bootstrap then planned an install into a venv
    that already carried the pin."""
    a, b = twin_venvs
    spec = _spec({DIST: "2.0"})
    for target in (a, b):
        assert _pre_fix_package_versions(spec, _python(target)) == {DIST: None}
    assert trial_env.package_versions(spec, _python(a)) == {DIST: "1.0"}
    assert trial_env.package_versions(spec, _python(b)) == {DIST: "2.0"}
    assert trial_env.plan_bootstrap(spec, python_version=spec["python"] + ".0",
                                    packages=_pre_fix_package_versions(spec, _python(b)),
                                    toolchain_ok=True, need_toolchain=False) == ["packages"]


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
LIVE_MIDI_ARCHIVE = REAL_ROOT / "fpga/reports/live-midi/full-kit-298/verification-rtl.json"


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
    spec = {"interpret": "live_midi_record", "id": "session-sim"}
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
    spec = {"interpret": "live_midi_record", "id": "session-rtl", "fixtures": ["coverage"]}
    d = _lm_record(tmp_path, rtl={"coverage": {"state": "PASS", "comparison": {"periods": 0}}})
    assert trial.interpret_live_midi_record(spec, {"rc": 0}, d, "required")[
        "verdict"] == trial.NO_VERDICT


@pytest.mark.parametrize("declaration", [{}, {"fixtures": []}, {"fixtures": None},
                                         {"fixtures": [" "]}],
                         ids=["omitted", "empty", "null", "blank-name"])
def test_live_midi_interpreter_requires_rtl_replay_declaration(tmp_path, declaration):
    spec = {"id": "session-rtl", **declaration}
    result = trial.interpret_live_midi_record(spec, {"rc": 0}, _lm_record(tmp_path), "required")
    assert result["verdict"] == trial.NO_VERDICT
    assert any("RTL replay fixtures" in reason for reason in result["reasons"])


@pytest.mark.parametrize("spec", [{}, {"id": "session-rlt"}, {"id": "candidate:"}],
                         ids=["missing", "unknown", "unnamed-candidate"])
def test_live_midi_host_evidence_requires_a_session_id(tmp_path, spec):
    result = trial.interpret_live_midi_record(spec, {"rc": 0}, _lm_record(tmp_path), "required")
    assert result["verdict"] == trial.NO_VERDICT
    assert any("session ID" in reason for reason in result["reasons"])


def test_live_midi_named_control_and_candidate(tmp_path):
    spec = {"interpret": "live_midi_record"}
    control = json.loads(LIVE_MIDI_ARCHIVE.read_text())["controls"]["DROP_NOTE_OFF"]
    rec = {"verdict": control["verdict"], "control": control}
    (tmp_path / "verification.json").write_text(json.dumps(rec))
    assert trial.interpret_live_midi_record(spec, {"rc": 0}, tmp_path, "control")["caught"]
    assert not trial.interpret_live_midi_record(spec, {"rc": 1}, tmp_path, "control")["caught"]

    # As a candidate the named control supplies no clean session scenarios.
    candidate = dict(spec, id="candidate:drop-note-off")
    result = trial.interpret_live_midi_record(candidate, {"rc": 1}, tmp_path, "required")
    assert result["verdict"] == trial.NO_VERDICT
    assert "scenario coverage did not run" in result["reasons"]


def _live_midi_registry(repo, mode, *, replays=False, declaration=None, session_mode=None):
    """Keep the shipped session arguments, host evidence and caught control fixed."""
    modes = json.loads(trial.REGISTRY.read_text())["trials"]["T-LIVE-MIDI"]["modes"]
    session, = modes[session_mode or mode]["required"]
    record = json.loads(LIVE_MIDI_ARCHIVE.read_text())
    if not replays:
        record["rtl"] = {}
    if declaration is None:
        declaration = {"fixtures": session["fixtures"]} if "fixtures" in session else {}
    repo.child({"rc": 0, "files": {"verification.json": record}}, id=session["id"],
               interpret=session["interpret"], **declaration)
    repo.children[0]["args"].extend(session["args"])
    repo.child(late_control(), role="control")
    return repo.registry(tid="T-LIVE-MIDI", mode=mode)


def _run_live_midi(repo, registry, mode):
    return trial.run_trial("T-LIVE-MIDI", mode=mode, root=repo.root, registry=registry,
                           dag=repo.root / "docs/dag.json")


@pytest.mark.parametrize("declaration", [{}, {"fixtures": []}, {"fixtures": [" "]}],
                         ids=["omitted", "empty", "blank-name"])
def test_live_midi_rtl_registry_requires_replay_fixtures(repo, declaration):
    registry = _live_midi_registry(repo, "rtl", declaration=declaration)
    diagnostic = r"T-LIVE-MIDI/rtl/session-rtl: .*fixtures.*RTL replay"
    with pytest.raises(trial.RegistryError, match=diagnostic):
        trial.load_registry(registry, repo.root / "docs/dag.json")


def test_live_midi_rtl_registry_rejects_sim_session(repo):
    registry = _live_midi_registry(repo, "rtl", session_mode="sim")
    with pytest.raises(trial.RegistryError, match="missing required child 'session-rtl'"):
        trial.load_registry(registry, repo.root / "docs/dag.json")


@pytest.mark.parametrize("session_mode, declaration, reason", [
    pytest.param("rtl", {}, "must declare non-empty 'fixtures'", id="omitted"),
    pytest.param("rtl", {"fixtures": []}, "must declare non-empty 'fixtures'", id="empty"),
    pytest.param("sim", None, "missing required child 'session-rtl'", id="sim-session"),
])
def test_live_midi_old_rtl_pass_requires_replay_fixtures(
        repo, session_mode, declaration, reason, monkeypatch, capsys):
    registry = _live_midi_registry(repo, "rtl", session_mode=session_mode, declaration=declaration)
    # The old writer accepted the registry and judged both sessions as host-only.
    # Keep the original child ID in the receipt while reproducing that judgment.
    with monkeypatch.context() as old:
        old.setattr(trial, "load_registry", lambda path, dag: json.loads(path.read_text()))
        old.setitem(trial.INTERPRETERS, "live_midi_record",
                    lambda spec, run, out, role: trial.interpret_live_midi_record(
                        dict(spec, id="session-sim"), run, out, role))
        run_dir, rec = _run_live_midi(repo, registry, "rtl")
    assert rec["verdict"] == trial.PASS and rec["controls"][0]["caught"]
    assert rec["children"][0]["coverage"]["observed"]["i2s_periods"] == {}
    if session_mode == "rtl":
        # Check interpretation independently of the registry membership rejection below.
        child = rec["children"][0]
        assert "id" not in child["interpreter"]
        result, error = trial._rederive(child, run_dir, cancelled=False)
        assert error is None
        assert result["verdict"] == trial.NO_VERDICT
        assert any("RTL replay fixtures" in msg for msg in result["reasons"])
    rc = trial.main(["check-receipt", str(run_dir / "receipt.json"), "--require", trial.PASS])
    out = capsys.readouterr().out
    assert rc == 1, out
    # Both membership and re-derivation reject the malformed RTL declaration.
    # A sim receipt in the RTL population fails membership alone.
    assert out.count("REJECTED:") == (2 if session_mode == "rtl" else 1), out
    assert "REJECTED: T-LIVE-MIDI/rtl" in out and "session-rtl" in out
    assert reason in out and "RTL replay" in out


@pytest.mark.parametrize("mode, replays, verdict", [
    ("rtl", True, trial.PASS), ("rtl", False, trial.NO_VERDICT), ("sim", False, trial.PASS),
])
def test_live_midi_archived_session_receipt(repo, capsys, mode, replays, verdict):
    registry = _live_midi_registry(repo, mode, replays=replays)
    run_dir, rec = _run_live_midi(repo, registry, mode)
    assert rec["controls"][0]["caught"]
    assert rec["verdict"] == verdict, rec["verdict_reasons"]
    if verdict == trial.NO_VERDICT:
        for fixture in ("coverage", "alternates", "pressure", "sustained"):
            assert f"no RTL replay recorded for {fixture}" in rec["children"][0]["reasons"]
    rc = trial.main(["check-receipt", str(run_dir / "receipt.json"), "--require", trial.PASS])
    out = capsys.readouterr().out
    assert f"VALID: T-LIVE-MIDI {verdict}" in out
    assert rc == (0 if verdict == trial.PASS else 1), out


# ---- T-PHYSICAL (#208): the capture record and its synthetic controls --------
PHYS_FIXTURES = ["silence", "held-m5a-saw", "held-default", "bar808-full", "demo"]


def _capture_rec(verdict, commands=PHYS_FIXTURES + ["demo"], **extra):
    return {"schema": "r0-capture-analysis/1", "verdict": verdict,
            "reasons": extra.pop("reasons", []),
            "takes": [{"id": f"t{i}", "command_id": c} for i, c in enumerate(commands)],
            "properties": {}, **extra}


def _controls_rec(all_caught=True, analyser="real"):
    return {"all_caught": all_caught, "analyser": analyser,
            "clean": {"verdict": "PASS", "ok": all_caught},
            "defects": {"dropout": {"intended": "dropout", "caught": all_caught,
                                    "outcome": "caught" if all_caught else "NOT caught"}}}


def _physical(repo, rec, rc, controls=None, crc=0):
    repo.child({"rc": rc, "files": {"analysis.json": rec}}, interpret="physical_capture_record",
               fixtures=PHYS_FIXTURES)
    repo.child({"rc": crc, "files": {"controls.json": controls or _controls_rec()}},
               interpret="capture_controls_record", role="control")
    return repo.run()


def test_physical_without_a_capture_session_is_operator_blocked_no_verdict(repo):
    rec = _capture_rec("REFUSED", commands=[], operator_blocked=True,
                       reasons=["operator-blocked: no capture session at captures/r0/session.json"])
    run_dir, r = _physical(repo, rec, 2)
    assert r["verdict"] == trial.NO_VERDICT
    assert "operator-blocked" in r["children"][0]["reasons"][0]
    assert r["controls"][0]["caught"] is True
    ok, problems, _ = trial.check_receipt(run_dir / "receipt.json")
    assert ok, problems


def test_physical_pass_needs_the_whole_diagnostic_set_and_a_repeat(repo):
    _, r = _physical(repo, _capture_rec("PASS"), 0)
    assert r["verdict"] == trial.PASS


@pytest.mark.parametrize("status,rc,verdict", [
    ("FAIL", 1, trial.FAIL), ("REFUSED", 2, trial.NO_VERDICT),
])
def test_physical_object_reason_survives_composite_and_recheck(repo, status, rc, verdict):
    run_dir, receipt = _physical(repo, _capture_rec(status, reasons=[{"detail": "bad take"}]), rc)
    assert receipt["verdict"] == verdict
    assert "bad take" in receipt["verdict_reasons"][0]
    ok, problems, _ = trial.check_receipt(run_dir / "receipt.json")
    assert ok, problems


@pytest.mark.parametrize("commands,gap", [
    (["silence", "held-default", "bar808-full", "demo", "demo"], "no take of held-m5a-saw"),
    (PHYS_FIXTURES, "no repeat take"),
])
def test_physical_pass_record_with_missing_coverage_is_no_verdict(repo, commands, gap):
    _, r = _physical(repo, _capture_rec("PASS", commands=commands), 0)
    assert r["verdict"] == trial.NO_VERDICT
    assert any(gap in x for x in r["children"][0]["reasons"])


def test_physical_fail_is_fail_and_exit_disagreement_is_no_verdict(repo, tmp_path):
    _, r = _physical(repo, _capture_rec("FAIL", reasons=["drums-1: dropout"]), 1)
    assert r["verdict"] == trial.FAIL
    (tmp_path / "b").mkdir()
    other = Repo(tmp_path / "b")
    _, r2 = _physical(other, _capture_rec("PASS"), 1)
    assert r2["verdict"] == trial.NO_VERDICT


@pytest.mark.parametrize("controls,crc", [
    (_controls_rec(all_caught=False), 1),
    (_controls_rec(analyser="stub"), 0),        # the start-red stub is never a catch
    (_controls_rec(), 2),                       # a record with a refusal exit
])
def test_physical_controls_are_caught_only_by_the_real_analyser(repo, controls, crc):
    _, r = _physical(repo, _capture_rec("PASS"), 0, controls=controls, crc=crc)
    assert r["controls"][0]["caught"] is False
    assert r["verdict"] == trial.NO_VERDICT


def test_physical_execution_error_exit_3_is_no_verdict(repo):
    """plan090: r0_capture.py exits 3 with an ERROR record on an unexpected
    exception -- an execution error, never a product verdict."""
    rec = _capture_rec("ERROR", reasons=["execution error (no verdict): KeyError: 'x'"])
    _, r = _physical(repo, rec, 3)
    assert r["children"][0]["verdict"] == trial.NO_VERDICT
    assert r["verdict"] == trial.NO_VERDICT


# ---- #1540: physical capture requires all five diagnostic commands declared --
def _physical_child_spec(image_id: str, declaration: dict) -> dict:
    spec = {"interpret": "physical_capture_record", "image_id": image_id}
    spec.update(declaration)
    return spec


def _write_analysis(out: pathlib.Path, rec: dict) -> pathlib.Path:
    out.mkdir(parents=True, exist_ok=True)
    (out / "analysis.json").write_text(json.dumps(rec))
    (out / "log.txt").write_text("")
    return out


def _rederive_physical(spec: dict, run_dir: pathlib.Path, child_dir: pathlib.Path, rc: int) -> dict:
    entry = {
        "id": f"{spec['image_id']}-capture",
        "role": "required",
        "dir": child_dir.name,
        "interpreter": dict(spec),
        "execution": {"state": "PASS" if rc == 0 else "FAIL", "rc": rc, "secs": 0.1},
    }
    got, err = trial._rederive(entry, run_dir, cancelled=False)
    assert err is None, err
    return got


@pytest.mark.parametrize("image_id", ["r0", "r1"])
@pytest.mark.parametrize(
    "declaration, missing_cmds, expected_cov",
    [
        pytest.param({}, PHYS_FIXTURES, [], id="omitted"),
        pytest.param({"fixtures": []}, PHYS_FIXTURES, [], id="empty"),
        pytest.param(
            {"fixtures": ["demo"]},
            ["silence", "held-m5a-saw", "held-default", "bar808-full"],
            ["demo"],
            id="demo-only",
        ),
        pytest.param(
            {"fixtures": ["demo", "demo", "held-m5a-saw", "held-default", "bar808-full"]},
            ["silence"],
            ["demo", "demo", "held-m5a-saw", "held-default", "bar808-full"],
            id="five-with-duplicate-demo",
        ),
    ],
)
@pytest.mark.parametrize(
    "takes_cmds",
    [
        pytest.param(["demo", "demo"], id="two-demo-takes"),
        pytest.param(PHYS_FIXTURES + ["demo"], id="complete-takes"),
    ],
)
def test_physical_incomplete_declaration_is_no_verdict_fresh_and_rederived(
    tmp_path, image_id, declaration, missing_cmds, expected_cov, takes_cmds
):
    """#1540: a required R0/R1 capture cannot PASS without declaring all five
    diagnostic commands, even when every take is present on disk."""
    spec = _physical_child_spec(image_id, declaration)
    rec = _capture_rec("PASS", commands=takes_cmds, image_id=image_id)
    child_dir = _write_analysis(tmp_path / f"{image_id}-capture", rec)
    run = {"state": "PASS", "rc": 0, "secs": 0.1, "out": ""}

    fresh = trial.judge_child(spec, run, child_dir, "required")
    rederived = _rederive_physical(spec, tmp_path, child_dir, rc=0)

    for res in (fresh, rederived):
        assert res["verdict"] == trial.NO_VERDICT, res
        assert any("fixture declaration" in r for r in res["reasons"]), res["reasons"]
        joined = " ".join(res["reasons"])
        for cmd in missing_cmds:
            assert cmd in joined, (cmd, res["reasons"])
        assert not any("no take of" in r for r in res["reasons"]), res["reasons"]
        assert res["coverage"]["expected"]["commands"] == expected_cov
        assert res["coverage"]["observed"]["commands"] == sorted(set(takes_cmds))
        assert res["coverage"]["observed"]["takes"] == len(takes_cmds)
    assert fresh == rederived


@pytest.mark.parametrize("image_id", ["r0", "r1"])
@pytest.mark.parametrize(
    "declaration, takes_cmds",
    [
        pytest.param({}, ["demo", "demo"], id="omitted-two-demos"),
        pytest.param({}, PHYS_FIXTURES + ["demo"], id="omitted-complete-takes"),
        pytest.param({"fixtures": []}, ["demo", "demo"], id="empty-two-demos"),
        pytest.param({"fixtures": ["demo"]}, ["demo", "demo"], id="demo-only-two-demos"),
        pytest.param(
            {"fixtures": ["demo", "demo", "held-m5a-saw", "held-default", "bar808-full"]},
            PHYS_FIXTURES + ["demo"],
            id="five-with-duplicate-demo-complete-takes",
        ),
    ],
)
def test_physical_forged_pass_receipt_with_incomplete_declaration_is_rejected(
    repo, image_id, declaration, takes_cmds
):
    """A pre-fix or forged PASS receipt whose interpreter spec omits or
    shortens fixtures must be rejected by check_receipt via re-derivation."""
    rec = _capture_rec("PASS", commands=takes_cmds, image_id=image_id)
    repo.child(
        {"rc": 0, "files": {"analysis.json": rec}},
        interpret="physical_capture_record",
        image_id=image_id,
        **declaration,
    )
    repo.child(
        {"rc": 0, "files": {"controls.json": _controls_rec()}},
        interpret="capture_controls_record",
        role="control",
    )
    run_dir, receipt = repo.run()
    assert receipt["verdict"] == trial.NO_VERDICT
    assert any("fixture declaration" in r for r in receipt["children"][0]["reasons"])
    ok, problems, _ = trial.check_receipt(run_dir / "receipt.json")
    assert ok, problems

    # Simulate a pre-fix receipt that sealed PASS for the same evidence and spec.
    forged = copy.deepcopy(receipt)
    forged["verdict"] = trial.PASS
    forged["verdict_reasons"] = []
    forged["children"][0]["verdict"] = trial.PASS
    forged["children"][0]["reasons"] = []
    ok, problems, _ = _forge(run_dir / "receipt.json", forged)
    assert not ok
    assert any("fixture declaration" in p for p in problems), problems


@pytest.mark.parametrize("image_id", ["r0", "r1"])
def test_physical_valid_declaration_order_independent_and_extra_command(tmp_path, image_id):
    """Order of the five required commands does not matter; any additional
    declared command still requires a recorded take."""
    reordered = list(reversed(PHYS_FIXTURES))
    complete_takes = PHYS_FIXTURES + ["demo"]
    run = {"state": "PASS", "rc": 0, "secs": 0.1, "out": ""}

    # Reordered five commands + complete takes -> PASS (fresh and re-derived).
    spec = _physical_child_spec(image_id, {"fixtures": reordered})
    cdir = _write_analysis(
        tmp_path / "reordered",
        _capture_rec("PASS", commands=complete_takes, image_id=image_id),
    )
    fresh = trial.judge_child(spec, run, cdir, "required")
    rederived = _rederive_physical(spec, tmp_path, cdir, rc=0)
    assert fresh["verdict"] == trial.PASS and rederived == fresh

    # Additional declared command without a take -> NO VERDICT naming it.
    spec_extra = _physical_child_spec(image_id, {"fixtures": PHYS_FIXTURES + ["held-m5a-pulse"]})
    fresh_miss = trial.judge_child(spec_extra, run, cdir, "required")
    rederived_miss = _rederive_physical(spec_extra, tmp_path, cdir, rc=0)
    assert fresh_miss["verdict"] == trial.NO_VERDICT
    assert any("no take of held-m5a-pulse" in r for r in fresh_miss["reasons"])
    assert rederived_miss == fresh_miss

    # Additional declared command with a take -> PASS.
    cdir_extra = _write_analysis(
        tmp_path / "with-extra",
        _capture_rec("PASS", commands=complete_takes + ["held-m5a-pulse"], image_id=image_id),
    )
    fresh_ok = trial.judge_child(spec_extra, run, cdir_extra, "required")
    rederived_ok = _rederive_physical(spec_extra, tmp_path, cdir_extra, rc=0)
    assert fresh_ok["verdict"] == trial.PASS and rederived_ok == fresh_ok


@pytest.mark.parametrize("image_id", ["r0", "r1"])
def test_physical_valid_declaration_take_coverage_and_existing_diagnostics(tmp_path, image_id):
    """With a valid declaration, two demos name all four missing commands,
    all five + second demo pass, repeating only silence fails repeat, and
    existing image/refusal/exit/fail diagnostics are preserved."""
    spec = _physical_child_spec(image_id, {"fixtures": PHYS_FIXTURES})
    other_image = "r1" if image_id == "r0" else "r0"

    # Two demo takes -> NO VERDICT naming all four missing commands.
    c_two = _write_analysis(
        tmp_path / "two-demo",
        _capture_rec("PASS", commands=["demo", "demo"], image_id=image_id),
    )
    r_two = trial.judge_child(spec, {"state": "PASS", "rc": 0, "secs": 0.1, "out": ""}, c_two, "required")
    assert r_two == _rederive_physical(spec, tmp_path, c_two, rc=0)
    assert r_two["verdict"] == trial.NO_VERDICT
    for missing in ("silence", "held-m5a-saw", "held-default", "bar808-full"):
        assert any(f"no take of {missing}" in reason for reason in r_two["reasons"]), r_two["reasons"]
    assert not any("fixture declaration" in reason for reason in r_two["reasons"])

    # Repeating only silence -> NO VERDICT, no repeat take.
    c_sil = _write_analysis(
        tmp_path / "silence-repeat",
        _capture_rec("PASS", commands=PHYS_FIXTURES + ["silence"], image_id=image_id),
    )
    r_sil = trial.judge_child(spec, {"state": "PASS", "rc": 0, "secs": 0.1, "out": ""}, c_sil, "required")
    assert r_sil == _rederive_physical(spec, tmp_path, c_sil, rc=0)
    assert r_sil["verdict"] == trial.NO_VERDICT
    assert any("no repeat take" in reason for reason in r_sil["reasons"])

    # All five + second demo -> PASS.
    c_pass = _write_analysis(
        tmp_path / "complete",
        _capture_rec("PASS", commands=PHYS_FIXTURES + ["demo"], image_id=image_id),
    )
    r_pass = trial.judge_child(spec, {"state": "PASS", "rc": 0, "secs": 0.1, "out": ""}, c_pass, "required")
    assert r_pass == _rederive_physical(spec, tmp_path, c_pass, rc=0)
    assert r_pass["verdict"] == trial.PASS

    # Image mismatch -> NO VERDICT naming the image.
    c_img = _write_analysis(
        tmp_path / "wrong-image",
        _capture_rec("PASS", commands=PHYS_FIXTURES + ["demo"], image_id=other_image),
    )
    r_img = trial.judge_child(spec, {"state": "PASS", "rc": 0, "secs": 0.1, "out": ""}, c_img, "required")
    assert r_img == _rederive_physical(spec, tmp_path, c_img, rc=0)
    assert r_img["verdict"] == trial.NO_VERDICT and "image" in r_img["reasons"][0]

    # Analyser REFUSED (operator-blocked) -> NO VERDICT with operator-blocked reason.
    c_ref = _write_analysis(
        tmp_path / "refused",
        _capture_rec(
            "REFUSED",
            commands=[],
            image_id=image_id,
            operator_blocked=True,
            reasons=[f"operator-blocked: no capture session at captures/{image_id}/session.json"],
        ),
    )
    r_ref = trial.judge_child(spec, {"state": "FAIL", "rc": 2, "secs": 0.1, "out": ""}, c_ref, "required")
    assert r_ref == _rederive_physical(spec, tmp_path, c_ref, rc=2)
    assert r_ref["verdict"] == trial.NO_VERDICT and "operator-blocked" in r_ref["reasons"][0]

    # Exit disagreement -> NO VERDICT with exit diagnostic.
    r_exit = trial.judge_child(spec, {"state": "FAIL", "rc": 1, "secs": 0.1, "out": ""}, c_pass, "required")
    assert r_exit == _rederive_physical(spec, tmp_path, c_pass, rc=1)
    assert r_exit["verdict"] == trial.NO_VERDICT and "exit status 1 disagrees" in r_exit["reasons"][0]

    # Genuine analyser FAIL -> FAIL with analyser reason.
    c_fail = _write_analysis(
        tmp_path / "fail",
        _capture_rec("FAIL", commands=PHYS_FIXTURES + ["demo"], image_id=image_id, reasons=["drums-1: dropout"]),
    )
    r_fail = trial.judge_child(spec, {"state": "FAIL", "rc": 1, "secs": 0.1, "out": ""}, c_fail, "required")
    assert r_fail == _rederive_physical(spec, tmp_path, c_fail, rc=1)
    assert r_fail["verdict"] == trial.FAIL and r_fail["reasons"] == ["drums-1: dropout"]


def test_physical_removing_declaration_guard_flips_controls_to_false_pass(tmp_path, monkeypatch):
    """Removing only the declaration guard causes the incomplete-declaration
    negative controls to revert to the pre-fix false PASS."""
    monkeypatch.setattr(trial, "_physical_fixture_problem", lambda _fixtures: None)
    run = {"state": "PASS", "rc": 0, "secs": 0.1, "out": ""}
    c_two = _write_analysis(
        tmp_path / "two-demo",
        _capture_rec("PASS", commands=["demo", "demo"], image_id="r0"),
    )
    c_all = _write_analysis(
        tmp_path / "complete",
        _capture_rec("PASS", commands=PHYS_FIXTURES + ["demo"], image_id="r0"),
    )
    for declaration, child_dir in (
        ({}, c_two),
        ({"fixtures": []}, c_two),
        ({"fixtures": ["demo"]}, c_two),
        ({}, c_all),
        ({"fixtures": ["demo", "demo", "held-m5a-saw", "held-default", "bar808-full"]}, c_all),
    ):
        spec = _physical_child_spec("r0", declaration)
        res = trial.judge_child(spec, run, child_dir, "required")
        assert res["verdict"] == trial.PASS, (declaration, res)


def test_physical_shipped_registry_satisfies_shared_required_commands():
    """Both shipped T-PHYSICAL modes declare the shared diagnostic command set."""
    import capture_commands

    assert list(capture_commands.REQUIRED_COMMANDS) == PHYS_FIXTURES
    reg = trial.load_registry()
    for mode_name in ("capture", "capture-r1"):
        child = reg["trials"]["T-PHYSICAL"]["modes"][mode_name]["required"][0]
        assert trial._physical_fixture_problem(child.get("fixtures")) is None, mode_name

# ---- R1 (#279, plan088): the image a record ran is the image the child declares
def _r1_rolling(image):
    return {"state": "PASS", "image": image, "clean": {"demo@0": {"ok": True}}, "controls": {},
            "rtl": {"demo": {"state": "PASS", "control": {"caught": True},
                             "truncation_control": {"caught": True}, "comparison": {
                "periods": 257427, "periods_required": 257427, "writes_sent": 510,
                "writes_seen": 510}}}}


def test_rolling_record_of_another_image_is_no_verdict(repo):
    """A PASS for the release kit cannot answer an R1 child."""
    repo.child({"rc": 0, "files": {"verification.json": _r1_rolling(
        {"sender": "release", "target": "release"})}}, interpret="rolling_record",
        fixtures=["demo"], image={"sender": "tree", "target": "tree"})
    _, rec = repo.run()
    assert rec["verdict"] == trial.NO_VERDICT
    assert "declares" in rec["children"][0]["reasons"][0]


def test_rolling_wrong_kit_control_is_caught_only_at_the_init_bytes(repo):
    repo.child({"rc": 0, "files": {"verification.json": _r1_rolling(
        {"sender": "tree", "target": "tree"})}}, interpret="rolling_record",
        fixtures=["demo"], image={"sender": "tree", "target": "tree"})
    ctl = {"state": "FAIL", "image": {"sender": "release", "target": "tree"}, "clean": {},
           "controls": {}, "expect_fail": {"caught": True, "init_check": {"demo@0": ["kit"]}}}
    repo.child({"rc": 0, "files": {"verification.json": ctl}}, interpret="rolling_record",
               role="control", image={"sender": "release", "target": "tree"})
    _, rec = repo.run()
    assert rec["verdict"] == trial.PASS and rec["controls"][0]["caught"]
    (repo.root / "second").mkdir()
    repo2 = Repo(repo.root / "second")
    repo2.child({"rc": 0, "files": {"verification.json": _r1_rolling(
        {"sender": "tree", "target": "tree"})}}, interpret="rolling_record", fixtures=["demo"])
    miss = dict(ctl, expect_fail={"caught": False})
    repo2.child({"rc": 1, "files": {"verification.json": miss}}, interpret="rolling_record",
                role="control")
    _, rec2 = repo2.run()
    assert rec2["verdict"] == trial.NO_VERDICT and not rec2["controls"][0]["caught"]


# ---- #300 review: coverage is the stimulus-derived count, and it must be complete
def test_rolling_partial_i2s_is_no_verdict_not_pass(repo):
    """[red on e2dc705] 50 of 257,427 periods compared equal was a PASS."""
    rec = _r1_rolling({"sender": "tree", "target": "tree"})
    rec["rtl"]["demo"]["comparison"].update(periods=50, periods_required=257427)
    repo.child({"rc": 0, "files": {"verification.json": rec}}, interpret="rolling_record",
               fixtures=["demo"])
    _, r = repo.run()
    assert r["verdict"] == trial.NO_VERDICT
    assert any("50 of 257427" in x for x in r["children"][0]["reasons"])
    assert r["children"][0]["coverage"]["expected"]["i2s_periods"] == {"demo": 257427}


def test_rolling_without_a_required_count_or_truncation_control_is_no_verdict(repo):
    rec = _r1_rolling({"sender": "tree", "target": "tree"})
    del rec["rtl"]["demo"]["comparison"]["periods_required"]
    rec["rtl"]["demo"]["truncation_control"] = {"caught": False}
    repo.child({"rc": 0, "files": {"verification.json": rec}}, interpret="rolling_record",
               fixtures=["demo"])
    _, r = repo.run()
    reasons = " ".join(r["children"][0]["reasons"])
    assert r["verdict"] == trial.NO_VERDICT
    assert "no stimulus-derived" in reasons and "truncation control" in reasons


def test_held_note_partial_i2s_is_no_verdict_not_pass(repo):
    """[red on e2dc705] the held note cut to 2026 of 4052 rows still passed."""
    repo.child({"rc": 0, "files": {"held_note_audible.json": {
        "preset": "default", "note": 45, "legacy_image": False, "replay_status": 0,
        "i2s_peak_lsb": 12760, "min_peak_lsb": 1024, "verdict": "PASS",
        "periods": 2026, "periods_required": 3820, "truncation_control": {"caught": True}},
        "default/uart_i2s.txt": "0 12760 12760\n"}}, interpret="held_note_record")
    repo.child({"rc": 0, "files": {"held_note_audible.json": {
        "preset": "default", "note": 45, "legacy_image": True, "replay_status": 0,
        "i2s_peak_lsb": 3, "min_peak_lsb": 1024, "verdict": "FAIL", "periods": 2,
        "periods_required": 2, "truncation_control": {"caught": True}},
        "default-legacy/uart_i2s.txt": "0 3 3\n"}}, interpret="held_note_record", role="control")
    _, r = repo.run()
    assert r["verdict"] == trial.NO_VERDICT
    assert "2026 of 3820" in r["children"][0]["reasons"][0]


# ---- #313: per-child RTL reuse in the receipt, validated by check-receipt ---------
def _reusing_rolling(rtl_run):
    rec = _r1_rolling({"sender": "tree", "target": "tree"})
    if rtl_run is not None:
        rec["rtl"]["demo"]["rtl_run"] = rtl_run
    return rec


SIM = {"iverilog": "Icarus Verilog version 14.0 (devel) (s20260301-476-g1a23d1ffd)",
       "vvp": "Icarus Verilog runtime version 14.0 (devel) (s20260301-476-g1a23d1ffd)"}


def _reuse_child(repo, rtl_run):
    repo.child({"rc": 0, "files": {"verification.json": _reusing_rolling(rtl_run)}},
               interpret="rolling_record", fixtures=["demo"],
               image={"sender": "tree", "target": "tree"})
    repo.children[-1]["args"].append(trial.REUSE_FLAG)


def test_the_receipt_lists_reuse_per_child_and_check_receipt_accepts_it(repo):
    _reuse_child(repo, {"reused": True, "asked": "auto", "why": "identical run on disk", **SIM})
    repo.child(late_control(), role="control")
    run_dir, rec = repo.run()
    assert rec["verdict"] == trial.PASS, rec["verdict_reasons"]
    assert rec["rtl_reuse"] == {"c1": {"demo": {"reused": True, "why": "identical run on disk",
                                                **SIM}}}
    assert rec["children"][0]["interpreter"]["rtl_reuse"] == "report"
    ok, problems, _ = trial.check_receipt(run_dir / "receipt.json")
    assert ok, problems


def test_a_reusing_child_that_does_not_report_reuse_is_no_verdict(repo):
    _reuse_child(repo, None)                         # the record says nothing about reuse
    _, rec = repo.run()
    assert rec["verdict"] == trial.NO_VERDICT
    assert any("does not report whether it was reused" in r for r in rec["children"][0]["reasons"])


def test_a_reused_run_naming_no_simulator_is_no_verdict(repo):
    _reuse_child(repo, {"reused": True, "asked": "auto", "why": "identical run on disk",
                        "iverilog": "", "vvp": ""})
    _, rec = repo.run()
    assert rec["verdict"] == trial.NO_VERDICT
    assert any("names no simulator" in r for r in rec["children"][0]["reasons"])


@pytest.mark.parametrize("where", ["child", "summary"])
def test_a_forged_reuse_status_is_rejected_even_when_resealed(repo, where):
    """A re-simulated child claimed as reused (or the reverse) in the receipt,
    re-sealed: its evidence re-derives the true status."""
    _reuse_child(repo, {"reused": False, "asked": "auto",
                        "why": "no identical run on disk: simulator", **SIM})
    run_dir, rec = repo.run()
    path = run_dir / "receipt.json"
    forged = json.loads(path.read_text())
    if where == "child":
        forged["children"][0]["metrics"]["rtl_reuse"]["demo"]["reused"] = True
    else:
        forged["rtl_reuse"]["c1"]["demo"]["reused"] = True
    ok, problems, _ = _forge(path, forged)
    assert not ok and any("reuse" in p for p in problems), problems


def test_a_child_without_the_reuse_flag_carries_no_reuse_requirement(repo):
    repo.child({"rc": 0, "files": {"verification.json": _reusing_rolling(None)}},
               interpret="rolling_record", fixtures=["demo"],
               image={"sender": "tree", "target": "tree"})
    repo.child(late_control(), role="control")
    run_dir, rec = repo.run()
    assert rec["verdict"] == trial.PASS and rec["rtl_reuse"] == {}
    assert trial.check_receipt(run_dir / "receipt.json")[0]


def test_judge_forged_removal_of_the_reuse_requirement(repo):
    """The Judge's reproduction on 5a88437: the child ran with
    --reuse-rtl-if-identical and its evidence records a reuse; the forger drops
    the interpreter's rtl_reuse key, the child's metrics.rtl_reuse and the
    summary, and re-seals. Before the fix: VALID, problems []."""
    _reuse_child(repo, {"reused": True, "asked": "auto", "why": "identical run on disk", **SIM})
    repo.child(late_control(), role="control")
    run_dir, rec = repo.run()
    path = run_dir / "receipt.json"
    forged = json.loads(path.read_text())
    c = forged["children"][0]
    c["interpreter"].pop("rtl_reuse", None)
    (c.get("metrics") or {}).pop("rtl_reuse", None)
    forged["rtl_reuse"] = {}
    ok, problems, _ = _forge(path, forged)
    assert not ok
    assert any("reuse" in p for p in problems), problems


# ---- #283: the receipt's population is bound to its recorded registry ---------
# A receipt is only as strong as its child LIST: deleting the failing child (and
# its directory) and re-sealing used to leave a receipt every other check
# accepted -- check_receipt walked whatever the receipt said it contained.

def _failing_late():
    return {"rc": 1, "files": {"record.json": copy.deepcopy(REAL_LATE)}}


def _forge_delete_child(run_dir, child_id):
    """Delete a required child (entry + directory), recompute the composite and
    re-seal: a self-consistent forgery, structurally well-formed."""
    path = run_dir / "receipt.json"
    forged = json.loads(path.read_text())
    forged["children"] = [c for c in forged["children"] if c["id"] != child_id]
    shutil.rmtree(run_dir / child_id)
    forged["verdict"], forged["verdict_reasons"] = trial.composite(
        forged["children"], forged["controls"])
    forged["coverage"]["required_children"] = len(forged["children"])
    return forged, path


def _failing_run(repo, extra_passing):
    """A mode whose c1 (and optionally c2) are required; the failing one is last."""
    if extra_passing:
        repo.child(ok_deadline())
    bad = repo.child(_failing_late())
    repo.child(late_control(), role="control")
    run_dir, rec = repo.run()
    assert rec["verdict"] == trial.FAIL, rec["verdict_reasons"]
    ok, problems, _ = trial.check_receipt(run_dir / "receipt.json")
    assert ok, problems                                # the clean fixture is VALID
    return run_dir, bad


def test_forgery_a_deleting_one_required_failing_child_is_rejected(repo):
    run_dir, bad = _failing_run(repo, extra_passing=True)
    forged, path = _forge_delete_child(run_dir, bad)
    assert forged["verdict"] == trial.PASS, "the forgery launders FAIL into PASS"
    ok, problems, _ = _forge(path, forged)
    assert not ok
    assert any("population" in p and bad in p for p in problems), problems


def test_forgery_b_deleting_the_sole_required_child_is_rejected(repo):
    run_dir, bad = _failing_run(repo, extra_passing=False)
    forged, path = _forge_delete_child(run_dir, bad)
    assert forged["children"] == []
    # the pre-fix composite read an empty required list as PASS; the forger
    # states that verdict regardless of what composite() now answers
    forged["verdict"] = trial.PASS
    ok, problems, _ = _forge(path, forged)
    assert not ok
    assert any("population" in p and bad in p for p in problems), problems


def test_composite_of_no_required_children_is_no_verdict():
    caught = [{"id": "k", "caught": True, "reasons": []}]
    verdict, reasons = trial.composite([], caught)
    assert verdict == trial.NO_VERDICT
    assert any("no required child" in r for r in reasons)


def test_required_child_moved_to_controls_changes_the_population(repo):
    run_dir, _ = _failing_run(repo, extra_passing=True)
    path = run_dir / "receipt.json"
    forged = json.loads(path.read_text())
    moved = forged["children"].pop(0)
    moved["role"] = "control"
    forged["controls"].append(moved)
    ok, problems, _ = _forge(path, forged)
    assert not ok and any("population" in p for p in problems), problems


@pytest.mark.parametrize("edit", ["drop_field", "change_field"])
def test_altered_interpreter_spec_is_rejected(repo, edit):
    run_dir, _ = _failing_run(repo, extra_passing=True)
    path = run_dir / "receipt.json"
    forged = json.loads(path.read_text())
    spec = forged["children"][0]["interpreter"]
    if edit == "drop_field":
        spec["token_prefix"] = "x"                      # a field the registry never had
    else:
        spec["interpret"] = "bound_text"
    ok, problems, _ = _forge(path, forged)
    assert not ok and any("population" in p and "interpreter" in p for p in problems), problems


def test_registry_drift_is_checked_against_the_recorded_registry(repo):
    """The working-tree registry changed after the run; the receipt is still
    VALID because the recorded registry is retrievable from git history."""
    def git(*a):
        subprocess.run(["git", "-C", str(repo.root), *a], check=True, capture_output=True)
    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    repo.child(ok_deadline())
    repo.child(late_control(), role="control")
    run_dir, rec = repo.run()
    git("add", "docs/trials.json")
    git("commit", "-qm", "registry")
    reg = repo.root / "docs/trials.json"
    reg.write_text(reg.read_text().replace('"x/1"', '"x/2"'))        # working tree drifts
    ok, problems, _ = trial.check_receipt(run_dir / "receipt.json", root=repo.root)
    assert ok, problems
    # ... and a forged deletion is still rejected against the history copy
    forged, path = _forge_delete_child(run_dir, "c1")
    ok, problems, _ = _forge(path, forged)
    assert not ok


def test_unresolvable_recorded_registry_is_unverifiable_never_valid(repo):
    run_dir, _ = _failing_run(repo, extra_passing=True)
    (repo.root / "docs/trials.json").unlink()            # no registry, no git history
    ok, problems, _ = trial.check_receipt(run_dir / "receipt.json", root=repo.root)
    assert not ok
    assert any(p.startswith("UNVERIFIABLE") for p in problems), problems
    assert not any("population" in p for p in problems), "unavailable is not forged"


# ---- #1479: required rolling_record children must declare RTL replay fixtures ---
def _empty_rtl_rolling_record():
    return {"state": "PASS", "clean": {"a": {"ok": True}},
            "controls": {"x": {"caught": True}}, "rtl": {}}


def _build_prefix_empty_fixtures_pass_receipt(repo, fixtures_spec):
    """Construct a properly hashed, population-consistent pre-fix PASS receipt
    whose required rolling_record child has `fixtures_spec` (None => omitted,
    [] => empty) and zero RTL replays. Membership passes so check_receipt is
    forced to decide via interpreter re-derivation."""
    repo.child({"rc": 0, "files": {"verification.json": _empty_rtl_rolling_record()}},
               interpret="rolling_record", fixtures=["demo"])
    repo.child(late_control(), role="control")
    run_dir, rec = repo.run()

    reg_path = repo.root / "docs/trials.json"
    reg = json.loads(reg_path.read_text())
    c_reg = reg["trials"]["T-X"]["modes"]["m"]["required"][0]
    if fixtures_spec is None:
        c_reg.pop("fixtures", None)
    else:
        c_reg["fixtures"] = fixtures_spec
    reg_path.write_text(json.dumps(reg))

    rec["identities"]["registry"]["sha256"] = trial.sha256_file(reg_path)
    child = rec["children"][0]
    if fixtures_spec is None:
        child["interpreter"].pop("fixtures", None)
    else:
        child["interpreter"]["fixtures"] = fixtures_spec
    child["verdict"] = trial.PASS
    child["reasons"] = []
    child["coverage"] = {"expected": {"rtl_fixtures": fixtures_spec or [], "i2s_periods": {}},
                         "observed": {"i2s_periods": {}, "clean_host_cases": 1}}
    rec["verdict"] = trial.PASS
    rec["verdict_reasons"] = []
    rec["coverage"]["with_valid_verdict"] = 1
    rec["coverage"]["passing"] = 1

    receipt_path = run_dir / "receipt.json"
    receipt_path.write_text(json.dumps(trial.seal(rec)))
    assert trial.membership_problems(rec, root=repo.root) == []
    return receipt_path


@pytest.mark.parametrize("fixtures_spec", [None, [], [""]])
def test_load_registry_rejects_required_rolling_child_without_fixtures(repo, fixtures_spec):
    extra = {} if fixtures_spec is None else {"fixtures": fixtures_spec}
    repo.child({"rc": 0, "files": {"verification.json": _empty_rtl_rolling_record()}},
               interpret="rolling_record", **extra)
    reg_path = repo.registry()
    with pytest.raises(trial.RegistryError, match=r"fixtures|RTL replay"):
        trial.load_registry(reg_path, repo.root / "docs/dag.json")


@pytest.mark.parametrize("fixtures_spec", [[], None])
def test_check_receipt_rejects_prefix_pass_with_empty_rolling_fixtures(repo, fixtures_spec, capsys):
    receipt_path = _build_prefix_empty_fixtures_pass_receipt(repo, fixtures_spec)
    ok, problems, _ = trial.check_receipt(receipt_path, root=repo.root)
    assert not ok, f"pre-fix PASS with fixtures={fixtures_spec!r} must be rejected"
    joined = " ".join(problems).lower()
    assert "no verdict" in joined
    assert "rtl replay" in joined or "fixtures" in joined, problems

    rc = trial.main(["check-receipt", str(receipt_path), "--require", trial.PASS])
    out = capsys.readouterr().out
    assert rc == 1, out
    assert "REJECTED:" in out
    assert "rtl replay" in out.lower() or "fixtures" in out.lower(), out


def test_rolling_positive_control_with_complete_rtl_evidence_passes_check_receipt(repo, capsys):
    repo.child({"rc": 0, "files": {"verification.json": _r1_rolling(
        {"sender": "release", "target": "release"})}},
        interpret="rolling_record", fixtures=["demo"])
    repo.child(late_control(), role="control")
    run_dir, rec = repo.run()
    assert rec["verdict"] == trial.PASS, rec["verdict_reasons"]
    receipt_path = run_dir / "receipt.json"
    ok, problems, _ = trial.check_receipt(receipt_path, root=repo.root)
    assert ok, problems
    rc = trial.main(["check-receipt", str(receipt_path), "--require", trial.PASS])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "VALID:" in out


def test_fixture_free_wrong_kit_control_works_as_control_and_as_candidate(repo):
    """Fixture-free wrong-kit controls stay accepted by load_registry, keep
    catching when run as controls, and preserve their intended host FAIL when
    run via --as-candidate (while refusing to false-PASS if host passes)."""
    repo.child({"rc": 0, "files": {"verification.json": _r1_rolling(
        {"sender": "r1", "target": "r1"})}}, interpret="rolling_record",
        fixtures=["demo"], image={"sender": "r1", "target": "r1"})
    host_fail = {
        "state": "FAIL",
        "image": {"sender": "release", "target": "r1"},
        "clean": {"demo@0": {"ok": False, "reasons": ["init_check: wrong kit"]}},
        "controls": {},
    }
    cid = repo.child({"rc": 1, "files": {"verification.json": host_fail}},
                     interpret="rolling_record", role="control",
                     image={"sender": "release", "target": "r1"})
    # load_registry accepts the fixture-free wrong-kit control
    reg_path = repo.registry()
    trial.load_registry(reg_path, repo.root / "docs/dag.json")

    # running that fixture-free control as candidate preserves its intended host FAIL
    run_dir, rec = repo.run(as_candidate=cid)
    assert rec["verdict"] == trial.FAIL, rec["verdict_reasons"]
    assert any("demo@0" in r for r in rec["children"][0]["reasons"])
    ok, problems, _ = trial.check_receipt(run_dir / "receipt.json", root=repo.root)
    assert ok, problems

    # if a fixture-free candidate somehow has all host cases pass, it cannot false-PASS
    (repo.root / "second").mkdir()
    repo2 = Repo(repo.root / "second")
    repo2.child({"rc": 0, "files": {"verification.json": _r1_rolling(
        {"sender": "r1", "target": "r1"})}}, interpret="rolling_record",
        fixtures=["demo"], image={"sender": "r1", "target": "r1"})
    cid2 = repo2.child({"rc": 0, "files": {"verification.json": _empty_rtl_rolling_record()}},
                       interpret="rolling_record", role="control")
    _, rec2 = repo2.run(as_candidate=cid2)
    assert rec2["verdict"] == trial.NO_VERDICT, rec2["verdict_reasons"]
    assert rec2["children"][0]["verdict"] == trial.NO_VERDICT
    assert any("rtl replay" in r.lower() or "fixtures" in r.lower()
               for r in rec2["children"][0]["reasons"])


@pytest.mark.parametrize("image_id", ["r0", "r1"])
@pytest.mark.parametrize("repeated", ["   ", "not-a-real-command"])
def test_physical_repeat_of_an_unidentified_take_is_not_a_repeat(tmp_path, image_id, repeated):
    """#1540 review: two takes of a blank or unknown command id are not a
    sounding repeat, fresh or re-derived."""
    spec = _physical_child_spec(image_id, {"fixtures": PHYS_FIXTURES})
    cdir = _write_analysis(
        tmp_path / "unknown-repeat",
        _capture_rec("PASS", commands=PHYS_FIXTURES + [repeated, repeated], image_id=image_id),
    )
    run = {"state": "PASS", "rc": 0, "secs": 0.1, "out": ""}
    fresh = trial.judge_child(spec, run, cdir, "required")
    assert fresh == _rederive_physical(spec, tmp_path, cdir, rc=0)
    assert fresh["verdict"] == trial.NO_VERDICT
    assert any("no repeat take" in r for r in fresh["reasons"]), fresh["reasons"]


@pytest.mark.parametrize("image_id", ["r0", "r1"])
@pytest.mark.parametrize(("repeated", "declare", "eligible_images"), [
    ("held-m5a-pulse", False, {"r0", "r1"}),
    ("not-a-real-command", True, set()),
    ("phrase-m5a", True, {"r0"}),
    ("run-m5a", True, {"r1"}),
    ("live-midi", True, set()),  # R1 host command without pinned playback bytes
])
def test_physical_repeat_uses_image_release_in_run_and_receipt(
    repo, image_id, repeated, declare, eligible_images
):
    fixtures = PHYS_FIXTURES + ([repeated] if declare else [])
    rec = _capture_rec("PASS", commands=PHYS_FIXTURES + [repeated, repeated], image_id=image_id)
    repo.child({"rc": 0, "files": {"analysis.json": rec}}, interpret="physical_capture_record",
               image_id=image_id, fixtures=fixtures)
    repo.child({"rc": 0, "files": {"controls.json": _controls_rec()}},
               interpret="capture_controls_record", role="control")
    run_dir, receipt = repo.run()
    path = run_dir / "receipt.json"
    child = receipt["children"][0]
    expected = trial.PASS if image_id in eligible_images else trial.NO_VERDICT
    assert receipt["controls"][0]["caught"]
    assert child["verdict"] == receipt["verdict"] == expected, child["reasons"]
    ok, problems, _ = trial.check_receipt(path)
    assert ok, problems
    if expected == trial.NO_VERDICT:
        assert any("no repeat take" in r for r in child["reasons"])
        # The old interpreter accepted this evidence when the repeated ID was declared.
        child.update(verdict=trial.PASS, reasons=[])
        receipt.update(verdict=trial.PASS, verdict_reasons=[])
        ok, problems, _ = _forge(path, receipt)
        assert not ok and any("no repeat take" in p for p in problems), problems


@pytest.mark.parametrize(("manifest", "reason"), [
    ("missing", "unreadable"),
    ("r1", "declares r1, expected r0"),
])
def test_physical_pass_needs_a_readable_image_matched_release(tmp_path, monkeypatch, manifest, reason):
    paths = trial.capture_commands.MANIFESTS
    monkeypatch.setitem(paths, "r0", tmp_path / "missing.json" if manifest == "missing" else paths["r1"])
    spec = _physical_child_spec("r0", {"fixtures": PHYS_FIXTURES})
    out = _write_analysis(tmp_path / "capture", _capture_rec("PASS", image_id="r0"))
    result = trial.judge_child(spec, {"state": "PASS", "rc": 0}, out, "required")
    assert result["verdict"] == trial.NO_VERDICT
    assert reason in result["reasons"][0]
    assert result == _rederive_physical(spec, tmp_path, out, rc=0)


@pytest.mark.parametrize("image_id", ["r0", "r1"])
@pytest.mark.parametrize("bad_role", ["delete", "unknown"])
def test_physical_receipt_with_malformed_child_role_is_rejected(repo, image_id, bad_role):
    """#1540 review: a resealed receipt whose required child lost or garbled its
    role must not skip the fixture check on re-derivation."""
    rec = _capture_rec("PASS", commands=["demo", "demo"], image_id=image_id)
    repo.child({"rc": 0, "files": {"analysis.json": rec}},
               interpret="physical_capture_record", image_id=image_id)
    repo.child({"rc": 0, "files": {"controls.json": _controls_rec()}},
               interpret="capture_controls_record", role="control")
    run_dir, receipt = repo.run()
    forged = copy.deepcopy(receipt)
    forged["verdict"] = trial.PASS
    forged["verdict_reasons"] = []
    forged["children"][0]["verdict"] = trial.PASS
    forged["children"][0]["reasons"] = []
    if bad_role == "delete":
        del forged["children"][0]["role"]
    else:
        forged["children"][0]["role"] = "unknown"
    ok, problems, _ = _forge(run_dir / "receipt.json", forged)
    assert not ok, problems
