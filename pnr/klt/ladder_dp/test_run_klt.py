"""Tests for the ladder_dp klt driver (issue #765).

These run without klt, yosys or openroad on purpose. What they pin is the part
that decides -- the precondition gate, the pass/FAILED/REFUSED/NO-VERDICT
mapping, and whether the committed ``run.log`` carries the exit status at all.
The end-to-end run is not a test: it needs a 3.6 GB PDK and tens of minutes of
OpenROAD, and its record is ``run.log`` plus the two response files.

Two of these are grounded in files this repository actually ships rather than
in fixtures invented here:

* ``test_the_committed_requests_satisfy_the_gate`` runs the gate against the
  committed request pair. ``CLAUDE.md``: "Run a gate against the current state
  before committing it. An unsatisfiable gate is worse than no gate."
* ``test_the_committed_par_request_points_at_the_unpatched_netlist`` is the
  regression guard for this issue: the whole defect was a request pointing at a
  hand-edited sibling of the netlist klt emitted.

What defeats the netlist guard, stated rather than left to be discovered
(``docs/verification-rules.md`` rule 8): the ``par_request`` filename check
catches the *sibling-file* indirection that existed here, and nothing more. A
patch step that rewrote ``ladder_dp_synth.v`` **in place** would satisfy it
while violating its intent. That shape is why ``main()`` hashes the netlist
immediately after synthesize and again after place-and-route and reports
``MUTATED DURING RUN``; the pair is covered by
``test_an_in_place_netlist_edit_is_reported`` below. Neither catches a patch
applied *before* synthesize runs (i.e. to the RTL), which is a different claim
and not one this driver makes.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import run_klt  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
CELL_LIBRARY = "gf180mcu_fd_sc_mcu7t5v0"
PDK = "gf180mcuD"


def _which_all(name):
    """Stand-in for ``shutil.which`` where every required binary is present."""
    return f"/usr/bin/{name}" if name in run_klt.REQUIRED_BINARIES else None


def _fake_pdk(tmp_path: pathlib.Path) -> pathlib.Path:
    root = tmp_path / "pdk"
    (root / PDK / "libs.ref" / CELL_LIBRARY).mkdir(parents=True)
    return root


def _mirrored_here(tmp_path: pathlib.Path) -> pathlib.Path:
    """Copy the committed requests into a tmp tree shaped like the repo.

    The requests carry repo-relative paths (``../../../rtl-sketch/ladder_dp.v``
    and a repo-scoped netlist), so the gate can only be exercised against the
    real bytes inside a tree with the same shape.
    """
    here = tmp_path / "repo" / "pnr" / "klt" / "ladder_dp"
    here.mkdir(parents=True)
    for name in ("synth_request.json", "par_request.json"):
        shutil.copy(HERE / name, here / name)
    rtl = tmp_path / "repo" / "rtl-sketch"
    rtl.mkdir(parents=True)
    (rtl / "ladder_dp.v").write_text("// placeholder\n")
    return here


# --------------------------------------------------------------- the gate

def test_the_committed_requests_satisfy_the_gate(tmp_path):
    # An unsatisfiable gate trains everyone to ignore gates.
    problems = run_klt.check_preconditions(
        here=_mirrored_here(tmp_path),
        pdk_root=_fake_pdk(tmp_path),
        pdk=PDK,
        which=_which_all,
    )
    assert problems == []


def test_a_missing_binary_refuses_and_names_it(tmp_path):
    problems = run_klt.check_preconditions(
        here=_mirrored_here(tmp_path),
        pdk_root=_fake_pdk(tmp_path),
        pdk=PDK,
        which=lambda name: None if name == "openroad" else f"/usr/bin/{name}",
    )
    assert len(problems) == 1
    assert "openroad" in problems[0]


def test_no_pdk_root_refuses_rather_than_guessing_one(tmp_path):
    # The shell version defaulted PDK_ROOT to a sibling of the repository that
    # exists on no CI runner and no dispatch worker.
    problems = run_klt.check_preconditions(
        here=_mirrored_here(tmp_path), pdk_root=None, pdk=PDK, which=_which_all
    )
    assert any("--pdk-root" in p for p in problems)


def test_a_pdk_without_the_requested_cell_library_refuses(tmp_path):
    root = tmp_path / "pdk"
    (root / PDK / "libs.ref" / "gf180mcu_fd_sc_mcu9t5v0").mkdir(parents=True)
    problems = run_klt.check_preconditions(
        here=_mirrored_here(tmp_path), pdk_root=root, pdk=PDK, which=_which_all
    )
    assert any(CELL_LIBRARY in p for p in problems)


def test_a_patched_sibling_netlist_refuses(tmp_path):
    # The exact pathology this issue retired, written as the gate's input.
    here = _mirrored_here(tmp_path)
    request = json.loads((here / "par_request.json").read_text())
    request["netlist"] = ".klt/synthesize/ladder_dp_synth_unsigned.v"
    (here / "par_request.json").write_text(json.dumps(request))
    problems = run_klt.check_preconditions(
        here=here, pdk_root=_fake_pdk(tmp_path), pdk=PDK, which=_which_all
    )
    assert any("ladder_dp_synth_unsigned.v" in p for p in problems)


def test_a_populated_route_output_directory_refuses(tmp_path):
    here = _mirrored_here(tmp_path)
    out = here / ".klt" / "place-and-route"
    out.mkdir(parents=True)
    (out / "stale.def").write_text("")
    problems = run_klt.check_preconditions(
        here=here, pdk_root=_fake_pdk(tmp_path), pdk=PDK, which=_which_all
    )
    assert any("place-and-route" in p for p in problems)


def test_an_unparseable_request_refuses(tmp_path):
    here = _mirrored_here(tmp_path)
    (here / "par_request.json").write_text("{not json")
    problems = run_klt.check_preconditions(
        here=here, pdk_root=_fake_pdk(tmp_path), pdk=PDK, which=_which_all
    )
    assert any("parseable JSON" in p for p in problems)


# ----------------------------------------------------- the committed tree

def test_the_committed_par_request_points_at_the_unpatched_netlist():
    request = json.loads((HERE / "par_request.json").read_text())
    assert request["netlist"] == ".klt/synthesize/ladder_dp_synth.v"


def test_no_netlist_patch_survives_in_this_directory():
    # The three retired patches were a `sed` on `signed`, a tie-cell heredoc
    # and an all-x assign strip. None of them belongs here any more, in any
    # language -- including a re-introduced shell script.
    assert not (HERE / "run-klt.sh").exists()
    driver = (HERE / "run_klt.py").read_text()
    body = driver.split('"""', 2)[-1]  # the module docstring names them
    for forbidden in ("tiel", "tieh", "re.sub", "re.subn"):
        assert forbidden not in body, forbidden


# ------------------------------------------------------- step bookkeeping

class _FakeCompleted:
    def __init__(self, returncode, stdout, stderr):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _runner(returncode=0, stdout=b"", stderr=b""):
    def run(argv, **kwargs):
        return _FakeCompleted(returncode, stdout, stderr)
    return run


def test_a_zero_byte_response_on_exit_zero_is_a_failure(tmp_path):
    # pnr/klt/ladder_dp/par_response.json is 0 bytes on main and the finding
    # could not be filed because no exit status was recorded. An exit-0 run
    # that writes nothing must not read as ok.
    out = tmp_path / "par_response.json"
    step = run_klt.run_step(
        "place-and-route", ["klt", "place-and-route"], out, 60.0,
        runner=_runner(returncode=0, stdout=b"", stderr=b"boom\n"),
    )
    assert step.returncode == 0
    assert step.response_bytes == 0
    assert not step.ok
    assert "empty response" in step.verdict
    assert run_klt.verdict_exit_code([step]) == run_klt.EXIT_FAILED


def test_an_unparseable_response_on_exit_zero_is_a_failure(tmp_path):
    out = tmp_path / "par_response.json"
    step = run_klt.run_step(
        "place-and-route", ["klt", "place-and-route"], out, 60.0,
        runner=_runner(returncode=0, stdout=b"Traceback (most recent call"),
    )
    assert not step.ok
    assert "unparseable" in step.verdict


def test_a_nonzero_exit_is_a_failure_and_keeps_stderr(tmp_path):
    out = tmp_path / "synth_response.json"
    step = run_klt.run_step(
        "synthesize", ["klt", "synthesize"], out, 60.0,
        runner=_runner(returncode=1, stdout=b"{}", stderr=b"[ERROR STA-0171]\n"),
    )
    assert not step.ok
    assert step.verdict == "FAILED (exit 1)"
    assert "STA-0171" in step.stderr


def test_a_timeout_is_no_verdict_not_a_failure(tmp_path):
    def run(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 60.0, output=b"", stderr=b"")

    out = tmp_path / "par_response.json"
    step = run_klt.run_step(
        "place-and-route", ["klt", "place-and-route"], out, 60.0, runner=run
    )
    assert step.timed_out
    assert step.verdict == "NO-VERDICT (timeout)"
    assert run_klt.verdict_exit_code([step]) == run_klt.EXIT_NO_VERDICT


def test_a_good_pair_of_steps_exits_zero(tmp_path):
    steps = []
    for name in ("synthesize", "place-and-route"):
        steps.append(
            run_klt.run_step(
                name, ["klt", name], tmp_path / f"{name}.json", 60.0,
                runner=_runner(returncode=0, stdout=b'{"status": "ok"}'),
            )
        )
    assert all(s.ok for s in steps)
    assert run_klt.verdict_exit_code(steps) == run_klt.EXIT_OK


# --------------------------------------------------------------- the record

def test_the_run_log_records_every_step_exit_status(tmp_path):
    # The one thing whose absence made the 0-byte response unfilable.
    steps = [
        run_klt.run_step(
            "synthesize", ["klt", "synthesize"], tmp_path / "s.json", 60.0,
            runner=_runner(returncode=0, stdout=b'{"status": "ok"}'),
        ),
        run_klt.run_step(
            "place-and-route", ["klt", "place-and-route"], tmp_path / "p.json",
            60.0, runner=_runner(returncode=2, stdout=b"", stderr=b"DRT-0305\n"),
        ),
    ]
    text = run_klt.format_run_log(
        klt_version="klt 9.9.9",
        pdk_root=tmp_path / "pdk",
        pdk=PDK,
        steps=steps,
        netlist_note="netlist pnr/klt/ladder_dp/.klt/synthesize/ladder_dp_synth.v",
        exit_code=run_klt.EXIT_FAILED,
    )
    assert "klt 9.9.9" in text
    assert "[synthesize] exit=0" in text
    assert "[place-and-route] exit=2" in text
    assert "response_bytes=0" in text
    assert "DRT-0305" in text
    assert "exit 1 (FAILED)" in text


def test_an_in_place_netlist_edit_is_reported():
    # The guard the filename check cannot make: run_klt hashes the netlist
    # either side of place-and-route, so an in-place patch shows up in the
    # record instead of passing as "the netlist klt emitted".
    source = (HERE / "run_klt.py").read_text()
    assert "MUTATED DURING RUN" in source
    assert source.count("sha256_file(netlist)") == 2


def test_the_exit_codes_are_four_distinct_outcomes():
    codes = {
        run_klt.EXIT_OK,
        run_klt.EXIT_FAILED,
        run_klt.EXIT_REFUSED,
        run_klt.EXIT_NO_VERDICT,
    }
    assert len(codes) == 4
    assert run_klt.EXIT_REFUSED not in (run_klt.EXIT_OK, run_klt.EXIT_FAILED)
