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
import signal
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


@pytest.mark.parametrize(
    "netlist",
    [
        # The exact pathology this issue retired, written as the gate's input.
        ".klt/synthesize/ladder_dp_synth_unsigned.v",
        # And the honest-looking literal path that is still not provably this
        # run's netlist, because klt mints a fresh run_id per invocation.
        ".klt/synthesize/ladder_dp_synth.v",
    ],
)
def test_a_literal_netlist_path_refuses(tmp_path, netlist):
    here = _mirrored_here(tmp_path)
    request = json.loads((here / "par_request.json").read_text())
    request["netlist"] = netlist
    (here / "par_request.json").write_text(json.dumps(request))
    problems = run_klt.check_preconditions(
        here=here, pdk_root=_fake_pdk(tmp_path), pdk=PDK, which=_which_all
    )
    assert any(netlist in p for p in problems)


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

def test_the_committed_par_request_defers_its_netlist_to_the_response():
    request = json.loads((HERE / "par_request.json").read_text())
    assert request["netlist"] == run_klt.NETLIST_SENTINEL


def test_no_netlist_patch_survives_in_this_directory():
    # The three retired patches were a `sed` on `signed`, a tie-cell heredoc
    # and an all-x assign strip. None of them belongs here any more, in any
    # language -- including a re-introduced shell script.
    #
    # What this checks, and why it is not the cell name: the driver now
    # *counts* `__tiel`/`__tieh` instances (`audit_retired_patches`), so
    # "mentions the cell" stopped separating a patch from a measurement. A
    # patch is a netlist **mutation**, so that is what is forbidden -- the
    # `re.sub`/`re.subn` the heredoc used, the `klt_tie_` instance names it
    # minted, and any write to the netlist. The guard against a mutation
    # this static check cannot see is the runtime one: `main()` hashes the
    # netlist either side of place-and-route (see this module's docstring).
    assert not (HERE / "run-klt.sh").exists()
    driver = (HERE / "run_klt.py").read_text()
    body = driver.split('"""', 2)[-1]  # the module docstring names them
    for forbidden in ("re.sub", "re.subn", "klt_tie_", "netlist.write"):
        assert forbidden not in body, forbidden


# ------------------------------------------------------- run-time binding

def test_build_par_request_binds_the_reported_netlist(tmp_path):
    here = _mirrored_here(tmp_path)
    netlist = tmp_path / "run-deadbeef" / "ladder_dp_synth.v"
    netlist.parent.mkdir()
    netlist.write_text("module ladder_dp(); endmodule\n")
    out, reason = run_klt.build_par_request(
        here / "par_request.json", netlist, tmp_path / "derived" / "req.json"
    )
    assert reason is None and out is not None
    derived = json.loads(out.read_text())
    # Absolute, so the derived request works wherever it is written -- klt
    # resolves a relative netlist against the request file's own directory.
    assert derived["netlist"] == str(netlist.resolve())
    assert pathlib.Path(derived["netlist"]).is_absolute()
    # Everything else is the committed template, unchanged.
    template = json.loads((here / "par_request.json").read_text())
    assert {k: v for k, v in derived.items() if k != "netlist"} == {
        k: v for k, v in template.items() if k != "netlist"
    }


def test_build_par_request_refuses_a_template_without_the_sentinel(tmp_path):
    here = _mirrored_here(tmp_path)
    request = json.loads((here / "par_request.json").read_text())
    request["netlist"] = ".klt/synthesize/ladder_dp_synth_unsigned.v"
    (here / "par_request.json").write_text(json.dumps(request))
    out, reason = run_klt.build_par_request(
        here / "par_request.json", tmp_path / "n.v", tmp_path / "req.json"
    )
    assert out is None
    assert "ladder_dp_synth_unsigned.v" in reason


def test_the_derived_request_is_a_sibling_of_the_template_and_ignored():
    # klt derives its output directory from the request FILE's directory, so a
    # derived request written under `.klt/` routes into `.klt/.klt/place-and-
    # route/` -- observed, then fixed. The precondition check for a populated
    # output directory looks at `.klt/place-and-route`, so the two must agree.
    assert not run_klt.DERIVED_PAR_REQUEST_NAME.startswith(".klt")
    assert "/" not in run_klt.DERIVED_PAR_REQUEST_NAME
    gitignore = (HERE.parents[2] / ".gitignore").read_text()
    assert f"pnr/klt/*/{run_klt.DERIVED_PAR_REQUEST_NAME}" in gitignore


# ------------------------------------------------------- step bookkeeping

class _FakePopen:
    """Enough of ``subprocess.Popen`` for the verdict mapping.

    ``timeout`` raises ``TimeoutExpired`` from ``communicate`` the way the
    real one does, and records whether it was killed, so the orphan-reaping
    path is observable without starting a real process.
    """

    def __init__(self, returncode=0, stdout=b"", stderr=b"", timeout=False):
        self._returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self._timeout = timeout
        self.returncode = None
        self.pid = 4242
        self.killed = False
        self.waits = 0

    def communicate(self, timeout=None):
        if self._timeout:
            self._timeout = False  # the post-kill drain succeeds
            raise subprocess.TimeoutExpired(["klt"], timeout or 0.0)
        self.returncode = self._returncode
        return self._stdout, self._stderr

    def wait(self, timeout=None):
        self.waits += 1
        self.returncode = self._returncode
        return self.returncode

    def kill(self):
        self.killed = True


def _popen(returncode=0, stdout=b"", stderr=b"", timeout=False):
    made = {}

    def factory(argv, **kwargs):
        # The orphan fix is only real if klt gets its own process group.
        assert kwargs.get("start_new_session") is True, (
            "run_step must start klt in its own process group, or a timeout "
            "kills klt and leaves its grandchildren running (#765)"
        )
        made["process"] = _FakePopen(returncode, stdout, stderr, timeout)
        return made["process"]

    factory.made = made
    return factory


def _no_containers(here):
    return []


def test_a_zero_byte_response_on_exit_zero_is_a_failure(tmp_path):
    # pnr/klt/ladder_dp/par_response.json is 0 bytes on main and the finding
    # could not be filed because no exit status was recorded. An exit-0 run
    # that writes nothing must not read as ok.
    out = tmp_path / "par_response.json"
    step = run_klt.run_step(
        "place-and-route", ["klt", "place-and-route"], out, 60.0,
        popen=_popen(returncode=0, stdout=b"", stderr=b"boom\n"),
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
        popen=_popen(returncode=0, stdout=b"Traceback (most recent call"),
    )
    assert not step.ok
    assert "unparseable" in step.verdict


def test_a_nonzero_exit_is_a_failure_and_keeps_stderr(tmp_path):
    out = tmp_path / "synth_response.json"
    step = run_klt.run_step(
        "synthesize", ["klt", "synthesize"], out, 60.0,
        popen=_popen(returncode=1, stdout=b"{}", stderr=b"[ERROR STA-0171]\n"),
    )
    assert not step.ok
    assert step.verdict == "FAILED (exit 1)"
    assert "STA-0171" in step.stderr


def test_a_timeout_is_no_verdict_not_a_failure(tmp_path):
    out = tmp_path / "par_response.json"
    step = run_klt.run_step(
        "place-and-route", ["klt", "place-and-route"], out, 60.0,
        popen=_popen(timeout=True), container_reaper=_no_containers,
        grace_s=0.0,
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
                popen=_popen(returncode=0, stdout=b'{"status": "ok"}'),
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
            popen=_popen(returncode=0, stdout=b'{"status": "ok"}'),
        ),
        run_klt.run_step(
            "place-and-route", ["klt", "place-and-route"], tmp_path / "p.json",
            60.0, popen=_popen(returncode=2, stdout=b"", stderr=b"DRT-0305\n"),
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
    assert "before = sha256_file(netlist)" in source
    assert "after = sha256_file(netlist)" in source
    assert "if before != after:" in source


def test_the_exit_codes_are_four_distinct_outcomes():
    codes = {
        run_klt.EXIT_OK,
        run_klt.EXIT_FAILED,
        run_klt.EXIT_REFUSED,
        run_klt.EXIT_NO_VERDICT,
    }
    assert len(codes) == 4
    assert run_klt.EXIT_REFUSED not in (run_klt.EXIT_OK, run_klt.EXIT_FAILED)


# ------------------------------------------- the retirement, as a live check

#: A netlist carrying one instance of each shape the three retired patches
#: existed to fix. This is the input that must defeat the audit -- a guard
#: ships with it, or with a stated reason none exists (`docs/verification-
#: rules.md` rule 8). Lines 1-3 are transcribed from what klt 0.5.0 actually
#: emitted, as quoted in `git show b9930a4a:pnr/klt/ladder_dp/run-klt.sh`.
_PATCHED_ERA_NETLIST = """\
module ladder_dp(x_in, y_out);
  input signed [15:0] x_in;
  output signed [15:0] y_out;
  wire _0001_;
  assign zero_ = 1'h0;
  assign one_ = 1'b1;
  assign \\sat_s$func$rtl-sketch/ladder_dp.v:131$5.v  = 28'hxxxxxxx;
endmodule
"""

#: What klt 0.9.2 emits instead, reduced to the shapes the audit reads.
_CURRENT_NETLIST = """\
module ladder_dp(x_in, y_out);
  input [15:0] x_in;
  output [15:0] y_out;
  gf180mcu_fd_sc_mcu7t5v0__tiel klt_tie_lo_1 (.ZN(zero_));
  gf180mcu_fd_sc_mcu7t5v0__tieh klt_tie_hi_1 (.Z(one_));
  assign y_out[15] = _0001_;
endmodule
"""


def test_the_audit_turns_red_on_the_netlist_the_patches_were_written_for(
    tmp_path,
):
    # The injected-defect control for this whole issue: feed the audit the
    # era it retired and every gated count must come back non-zero, with the
    # patch named. A guard that cannot do this is decoration.
    netlist = tmp_path / "ladder_dp_synth.v"
    netlist.write_text(_PATCHED_ERA_NETLIST)
    counts, complaints = run_klt.audit_retired_patches(netlist)
    assert counts["patch 1 (sed, STA-0171)"] == 2
    assert counts["patch 2 (tie cells, DRT-0305)"] == 2
    assert counts["patch 3 (all-x assigns, DRT-0305)"] == 1
    assert len(complaints) == 3
    assert all("would still have work" in c for c in complaints)
    joined = " ".join(complaints)
    for label, _pattern, _what in run_klt.RETIRED_PATCHES:
        assert label in joined


def test_the_audit_is_green_on_what_klt_0_9_2_emits(tmp_path):
    netlist = tmp_path / "ladder_dp_synth.v"
    netlist.write_text(_CURRENT_NETLIST)
    counts, complaints = run_klt.audit_retired_patches(netlist)
    assert complaints == []
    assert counts["patch 1 (sed, STA-0171)"] == 0
    assert counts["patch 2 (tie cells, DRT-0305)"] == 0
    assert counts["patch 3 (all-x assigns, DRT-0305)"] == 0
    assert counts["klt's own tie-cell instances"] == 2


def test_the_tie_cell_count_is_reported_and_never_gates(tmp_path):
    # A netlist with no constants to tie legitimately has no tie cells. A
    # `> 0` gate here would be red on a correct netlist, which is worse than
    # no gate -- so this pins that it stays an observation.
    netlist = tmp_path / "ladder_dp_synth.v"
    netlist.write_text("module m(); assign a = b; endmodule\n")
    counts, complaints = run_klt.audit_retired_patches(netlist)
    assert complaints == []
    assert counts["klt's own tie-cell instances"] == 0


def test_a_wide_constant_assign_is_counted_but_not_gated(tmp_path):
    # Stated limitation, under test: patch 2 only ever matched 1-bit
    # constants, so a wider one is the same defect class in a shape that
    # patch never handled. It is reported next to the gated counts and does
    # not fail the run.
    netlist = tmp_path / "ladder_dp_synth.v"
    netlist.write_text("module m();\n  assign q = 4'h0;\nendmodule\n")
    counts, complaints = run_klt.audit_retired_patches(netlist)
    assert complaints == []
    assert counts["patch 2 (tie cells, DRT-0305)"] == 0
    assert counts["wider-than-1-bit constant assigns (reported, not gated)"] == 1


def test_the_audit_counts_reach_the_run_log(tmp_path):
    steps = [
        run_klt.run_step(
            "synthesize", ["klt", "synthesize"], tmp_path / "s.json", 60.0,
            popen=_popen(returncode=0, stdout=b'{"status": "ok"}'),
        )
    ]
    text = run_klt.format_run_log(
        klt_version="klt 9.9.9",
        pdk_root=tmp_path / "pdk",
        pdk=PDK,
        steps=steps,
        netlist_note="netlist x.v",
        exit_code=run_klt.EXIT_OK,
        patch_audit={"patch 1 (sed, STA-0171)": 0},
    )
    assert "retired-patch audit" in text
    assert "patch 1 (sed, STA-0171): 0" in text


# ------------------------------------------- nothing may outlive the caller

def test_a_timeout_kills_the_process_group_not_just_klt(tmp_path, monkeypatch):
    # subprocess.run(timeout=...) kills the direct child only, so a timed-out
    # klt leaves `openroad` running. That is how a routed DEF landed in this
    # directory five hours after the run that was supposed to produce it.
    signalled = []
    monkeypatch.setattr(run_klt.os, "getpgid", lambda pid: 9001)
    monkeypatch.setattr(
        run_klt.os, "killpg", lambda pgid, sig: signalled.append((pgid, sig))
    )
    factory = _popen(timeout=True)
    step = run_klt.run_step(
        "place-and-route", ["klt", "place-and-route"],
        tmp_path / "par_response.json", 60.0,
        popen=factory, container_reaper=_no_containers, grace_s=0.0,
    )
    assert step.timed_out
    assert step.group_killed
    assert (9001, signal.SIGTERM) in signalled


def test_a_timeout_also_stops_the_openroad_containers_of_this_run(tmp_path):
    # A container is in no process group of ours; killpg cannot reach it.
    reaped = []

    def reaper(here):
        reaped.append(here)
        return ["c0ffee1234567890"]

    step = run_klt.run_step(
        "place-and-route", ["klt", "place-and-route"],
        tmp_path / "par_response.json", 60.0,
        here=tmp_path, popen=_popen(timeout=True), container_reaper=reaper,
        grace_s=0.0,
    )
    assert reaped == [tmp_path]
    assert step.containers_stopped == ["c0ffee1234567890"]
    text = run_klt.format_run_log(
        klt_version="klt 9.9.9", pdk_root=tmp_path, pdk=PDK, steps=[step],
        netlist_note="netlist x.v", exit_code=run_klt.EXIT_NO_VERDICT,
    )
    assert "containers stopped=1" in text
    assert "c0ffee123456" in text


def test_a_successful_step_reaps_nothing(tmp_path):
    def reaper(here):  # pragma: no cover - must not be called
        raise AssertionError("a run that exited on its own has nothing to reap")

    step = run_klt.run_step(
        "synthesize", ["klt", "synthesize"], tmp_path / "s.json", 60.0,
        here=tmp_path, popen=_popen(returncode=0, stdout=b"{}"),
        container_reaper=reaper,
    )
    assert step.ok
    assert step.containers_stopped == []


def test_only_containers_holding_a_path_inside_this_run_are_ours(tmp_path):
    # The ownership test that matters: the host runs OpenROAD containers for
    # other repositories at the same time (two were 13 days old when this was
    # written). Killing one of those is not an option, so the test is the
    # narrow one -- an absolute path, inside `here`, in the container's argv.
    mine = tmp_path / "pnr" / "klt" / "ladder_dp"
    mine.mkdir(parents=True)
    others = {
        "aaaa": ["openroad", "-metrics", "/elsewhere/soc_top_route_metrics.json"],
        "bbbb": ["openroad", "-metrics", str(mine / ".klt" / "m.json")],
        "cccc": ["postgres"],
        # A parent of `here` is NOT ours: the main checkout contains every
        # worktree, so a mount- or prefix-of-parent test would kill a
        # sibling agent's run.
        "dddd": ["openroad", str(tmp_path / "other.tcl")],
    }

    def runner(argv, **kwargs):
        class R:
            returncode = 0
            stdout = ""
        if argv[1] == "ps":
            R.stdout = "\n".join(others) + "\n"
        else:
            R.stdout = json.dumps(others[argv[-1]])
        return R

    assert run_klt.own_openroad_containers(mine, runner=runner) == ["bbbb"]


def test_a_docker_that_is_absent_or_failing_reaps_nothing(tmp_path):
    def runner(argv, **kwargs):
        class R:
            returncode = 127
            stdout = ""
        return R

    assert run_klt.own_openroad_containers(tmp_path, runner=runner) == []
    assert run_klt.stop_own_openroad_containers(tmp_path, runner=runner) == []


# ------------------------------------------------------- the bounded run

def test_the_committed_request_still_asks_for_a_full_route():
    # --target-stage exists so a dispatch worker can produce an attributable
    # partial record. What this repository asks for must not become whatever
    # last fitted in a time budget, so the override lands in the derived
    # request only.
    request = json.loads((HERE / "par_request.json").read_text())
    assert request["target_stage"] == "route"


def test_a_bounded_target_stage_reaches_only_the_derived_request(tmp_path):
    here = _mirrored_here(tmp_path)
    netlist = tmp_path / "ladder_dp_synth.v"
    netlist.write_text(_CURRENT_NETLIST)
    out = tmp_path / "par_request.resolved.json"
    derived, reason = run_klt.build_par_request(
        here / "par_request.json", netlist, out, target_stage="cts"
    )
    assert reason is None
    assert json.loads(derived.read_text())["target_stage"] == "cts"
    template = json.loads((here / "par_request.json").read_text())
    assert template["target_stage"] == "route"


def test_a_bounded_run_log_says_what_it_did_not_attempt(tmp_path):
    text = run_klt.format_run_log(
        klt_version="klt 9.9.9", pdk_root=tmp_path, pdk=PDK, steps=[],
        netlist_note="netlist x.v", exit_code=run_klt.EXIT_OK,
        target_stage="cts",
    )
    assert "BOUNDED RUN" in text
    assert "says nothing" in text
    full = run_klt.format_run_log(
        klt_version="klt 9.9.9", pdk_root=tmp_path, pdk=PDK, steps=[],
        netlist_note="netlist x.v", exit_code=run_klt.EXIT_OK,
        target_stage="route",
    )
    assert "BOUNDED RUN" not in full
