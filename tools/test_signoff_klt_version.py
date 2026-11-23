"""The local half of `regrade`'s pinned-klt envelope gate (issues #1089, #1095).

The step. `.github/workflows/signoff.yml`, job `regrade`, step `evidence
envelopes were attested by the pinned klt`, is a `python3` heredoc: it reads
the job-level `$KLT`, takes `klayout-tools==X.Y.Z` out of it, and fails while
either `manifests/evidence/item-{9,10}.json` records a
`provenance.klt_version` other than that pin -- an envelope left behind by
the OLD pin when the tool was bumped (#1089). `klt signoff --check` cannot
see that: it recomputes content hashes and never reads the producer version.

Why this file exists. PR #1090 shipped the gate correct, and satisfied
verification-rules rule 8 in its form 2: the eight controls that showed it
were run from a judge's scratch file and committed nowhere, so the only way
to exercise the step was to push (#1095). This is form 1. It runs the
SHIPPED step -- located by name in the checked-out workflow, its `run:` text
handed to `bash --noprofile --norc -eo pipefail` (the shell and flags the
runner uses for a Linux `run:` block), `cwd` at the tree holding the
envelopes, `KLT` in the child environment taken from `jobs.regrade.env`. An
ambient `KLT` is removed first, so no caller-supplied pin is consulted in
either direction. Nothing here re-implements the step, so nothing here can
drift from it. Stdlib and pyyaml only; no klt, no network.

  green  the committed envelopes, in the repository itself, under the
         workflow's own pin: exit 0, both paths reported
  red    seven one-change cases on disposable copies -- a stale producer
         version in item 9 and then in item 10 (the diagnostic must name the
         path, the recorded version and the pinned one); a missing version
         field; a JSON null; an unversioned package; an absent `KLT`; a range
         pin (each must END on its own `REFUSED:` line). A nonzero exit alone
         is not a catch: the step's FINAL exception is what is asserted, so
         a crash for any other reason fails the test.

The harness REFUSES (`HarnessRefused`) instead of reporting when it cannot
speak for the gate: no step of that name, or two; a step carrying `if:` or
`continue-on-error:` (green here, never able to fail a run -- #304's shape,
which `tools/test_check_workflows.py` first measured); a step-level
`env.KLT` shadowing the job-level pin it reads; no bash. Those are this
file's own rule-8 inputs, and each is a committed control below, beside the
start-red stub (a no-op with the step's shape) that the stale control must
fail on for the one reason that the stub accepted.

Blind spots of the STEP, reproduced through `run_step` on disposable
copies at `cce13654` and not fixed here (editing signoff.yml means
re-attesting item 10; neither case passes silently): `provenance: null`
ends in `AttributeError: 'NoneType' object has no attribute 'get'` -- exit
1, but no `REFUSED:` line; a quoted pin, `--from "klayout-tools==0.9.2"`,
is read as `0.9.2"` and reported as a mismatch against a correct envelope
-- exit 1, on a false diagnosis. Out of scope by the issue: a hand-edited
producer version equal to the pin authenticates.
"""
from __future__ import annotations

import copy
import json
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import sys

import pytest
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from makefile_recipe import makefile_recipe                          # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "signoff.yml"
WORKFLOW_REL = ".github/workflows/signoff.yml"
JOB = "regrade"
STEP_NAME = "evidence envelopes were attested by the pinned klt"
ITEM9 = "manifests/evidence/item-9.json"
ITEM10 = "manifests/evidence/item-10.json"
ENVELOPES = (ITEM9, ITEM10)
TRACKED = (WORKFLOW_REL, *ENVELOPES)
THIS_FILE = "tools/test_signoff_klt_version.py"
STALE_VERSION = "0.8.0"
BASH = ("bash", "--noprofile", "--norc", "-eo", "pipefail")
STEP_TIMEOUT_S = 120


class HarnessRefused(Exception):
    """The harness cannot measure: a precondition of the measurement itself
    failed, so there is no verdict. Distinct from the step's own `REFUSED:`
    diagnostic, which IS a verdict about the envelopes."""


def _load(workflow) -> dict:
    if isinstance(workflow, (str, pathlib.Path)):
        path = pathlib.Path(workflow)
        try:
            workflow = yaml.safe_load(path.read_text())
        except yaml.YAMLError as e:
            raise HarnessRefused(f"cannot parse {path}: {e}") from None
    return workflow


def _locate(workflow) -> tuple[dict, str | None]:
    """The named step, and the job-level `KLT` it will see.

    REFUSES on anything that would let a green here mean something other
    than "the shipped gate passes on this tree"."""
    jobs = (_load(workflow) or {}).get("jobs") or {}
    if JOB not in jobs:
        raise HarnessRefused(f"no job named {JOB!r} in {WORKFLOW_REL}: nothing to exercise")
    job = jobs[JOB] or {}
    hits = [s for s in job.get("steps") or []
            if isinstance(s, dict) and s.get("name") == STEP_NAME]
    if len(hits) != 1:
        raise HarnessRefused(
            f"expected exactly one step named {STEP_NAME!r} in job {JOB!r}, "
            f"found {len(hits)}: a gate this harness cannot pin must be "
            f"loud, never a silent exemption")
    step = hits[0]
    if not isinstance(step.get("run"), str) or not step["run"].strip():
        raise HarnessRefused(f"step {STEP_NAME!r} has no run: text")
    if "if" in step:
        raise HarnessRefused(
            f"step {STEP_NAME!r} carries `if: {step['if']!r}`: a conditional "
            f"gate runs where its condition says, not on every run")
    if step.get("continue-on-error"):
        raise HarnessRefused(
            f"step {STEP_NAME!r} carries `continue-on-error: "
            f"{step['continue-on-error']!r}`: it would still run green here "
            f"while never blocking CI")
    if "KLT" in (step.get("env") or {}):
        raise HarnessRefused(
            f"step {STEP_NAME!r} sets its own env.KLT "
            f"{step['env']['KLT']!r}, which shadows the job-level pin this "
            f"harness reads from jobs.{JOB}.env")
    return step, (job.get("env") or {}).get("KLT")


def _pinned_version(klt: str | None) -> str:
    """The version the job's `KLT` pins, read by whitespace token rather than
    by the step's own regex, so the two parses are independent."""
    for token in (klt or "").split():
        if token.startswith("klayout-tools=="):
            return token[len("klayout-tools=="):]
    raise HarnessRefused(
        f"jobs.{JOB}.env.KLT {klt!r} pins no klayout-tools==X.Y.Z: the "
        f"clean case has no pin to pass under")


def run_step(workflow, cwd: pathlib.Path) -> subprocess.CompletedProcess:
    """Run the shipped step's `run:` text as the runner would."""
    step, klt = _locate(workflow)
    if shutil.which("bash") is None:
        raise HarnessRefused(
            "no bash on PATH: the step is a bash script and cannot be run as shipped")
    env = {k: v for k, v in os.environ.items() if k != "KLT"}
    if klt is not None:
        env["KLT"] = klt
    try:
        return subprocess.run([*BASH, "-c", step["run"]], cwd=cwd, env=env,
                              capture_output=True, text=True,
                              timeout=STEP_TIMEOUT_S)
    except subprocess.TimeoutExpired as e:
        raise HarnessRefused(
            f"the step did not finish within {STEP_TIMEOUT_S} s: it is no "
            f"longer an offline stdlib check") from e


def _report(res: subprocess.CompletedProcess) -> str:
    return (f"exit {res.returncode}\n--- stdout\n{res.stdout}"
            f"--- stderr\n{res.stderr}")


def _assert_rejected(res: subprocess.CompletedProcess, *needles: str) -> None:
    """The step failed, and its FINAL exception is the named diagnostic.

    A nonzero exit alone is not a catch (verification-rules rule 5, third
    condition: the intended assertion is the one that fails)."""
    assert res.returncode != 0, (
        f"the step ACCEPTED an envelope it should have refused\n{_report(res)}")
    start = res.stderr.rfind("Traceback (most recent call last):")
    final = res.stderr[start:] if start >= 0 else res.stderr
    for needle in needles:
        assert needle in final, (
            f"the step failed, but not with the expected diagnostic "
            f"(missing {needle!r})\n{_report(res)}")


def _set_version(version):
    def change(envelope: dict) -> None:
        envelope["provenance"]["klt_version"] = version
    return change


def _drop_version(envelope: dict) -> None:
    del envelope["provenance"]["klt_version"]


def _tree(tmp_path: pathlib.Path, rel: str | None = None, change=None) -> pathlib.Path:
    """A disposable tree holding byte-copies of both committed envelopes at
    their repo-relative paths. `change` is applied to the parsed envelope at
    `rel` ONLY, and the copies are checked to have moved exactly there."""
    root = tmp_path / "tree"
    for env in ENVELOPES:
        dst = root / env
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / env, dst)
    if rel is not None:
        path = root / rel
        envelope = json.loads(path.read_text())
        change(envelope)
        path.write_text(json.dumps(envelope))
        assert path.read_bytes() != (REPO / rel).read_bytes(), (
            f"the mutation of {rel} did not apply")
        for other in ENVELOPES:
            if other != rel:
                assert (root / other).read_bytes() == (REPO / other).read_bytes(), (
                    f"{other} moved: the case must change ONE input")
    return root


def _step(workflow: dict) -> dict:
    return next(s for s in workflow["jobs"][JOB]["steps"]
                if s.get("name") == STEP_NAME)


def _workflow(mutate=None) -> dict:
    """A disposable copy of the parsed shipped workflow."""
    workflow = _load(WORKFLOW)
    if mutate is not None:
        mutate(workflow)
    return workflow


def _with_job_klt(klt: str | None):
    def mutate(workflow: dict) -> None:
        env = workflow["jobs"][JOB]["env"]
        if klt is None:
            del env["KLT"]
        else:
            env["KLT"] = klt
    return mutate


def _pytest_words(recipe: str) -> set[str]:
    """Every word of every pytest job in a `$(RUN)` recipe. A recipe is
    `$(RUN) "job" "job" ...`, so each job is ONE quoted token and is split a
    second time before its words are visible. A path that is merely mentioned
    in a recipe (a comment, an echo, another tool's argument) is not among
    them."""
    words = set()
    for line in recipe.split("\n"):
        for job in shlex.split(line.rstrip("\\")):
            w = shlex.split(job)
            if "pytest" in w:
                words.update(w)
    return words


@pytest.fixture(scope="module", autouse=True)
def _tracked_inputs_untouched():
    """Every mutation in this module lands on a copy. Asserted, not assumed."""
    before = {rel: (REPO / rel).read_bytes() for rel in TRACKED}
    yield
    after = {rel: (REPO / rel).read_bytes() for rel in TRACKED}
    assert after == before, (
        f"tracked inputs moved: {[rel for rel in TRACKED if after[rel] != before[rel]]}")


# =============================================================================
# Start red (verification-rules rule 1), on this harness: a stub with the
# step's shape and none of its behaviour must be seen to ACCEPT a stale
# envelope, so every red below is known to be the step refusing.
# =============================================================================
def test_start_red_a_noop_step_is_seen_to_accept_a_stale_envelope(tmp_path):
    noop = "python3 - <<'PY'\npass\nPY\n"
    res = run_step(_workflow(lambda wf: _step(wf).__setitem__("run", noop)),
                   _tree(tmp_path, ITEM9, _set_version(STALE_VERSION)))
    assert res.returncode == 0 and res.stdout == "" and res.stderr == "", _report(res)
    with pytest.raises(AssertionError, match="ACCEPTED"):
        _assert_rejected(res, "was attested by klt")


@pytest.mark.parametrize("how", ["removed", "renamed", "duplicated"])
def test_start_red_a_step_this_harness_cannot_pin_fails_it(tmp_path, how):
    def mutate(workflow: dict) -> None:
        steps = workflow["jobs"][JOB]["steps"]
        step = _step(workflow)
        if how == "removed":
            steps.remove(step)
        elif how == "renamed":
            step["name"] = "envelopes were attested (renamed)"
        else:
            steps.insert(steps.index(step), copy.deepcopy(step))
    with pytest.raises(HarnessRefused, match=re.escape(STEP_NAME)):
        run_step(_workflow(mutate), _tree(tmp_path))


# =============================================================================
# This harness's own rule-8 inputs: a step that is green here and dead in CI.
# =============================================================================
@pytest.mark.parametrize("neutralise, key", [
    (lambda s: s.__setitem__("if", False), "if"),
    (lambda s: s.__setitem__("if", "github.event_name == 'push'"), "if"),
    (lambda s: s.__setitem__("continue-on-error", True), "continue-on-error"),
], ids=["if-false", "if-conditional", "continue-on-error-true"])
def test_a_step_that_cannot_block_ci_fails_the_harness(neutralise, key):
    with pytest.raises(HarnessRefused, match=re.escape(f"`{key}:")):
        run_step(_workflow(lambda wf: neutralise(_step(wf))), REPO)


def test_a_step_level_klt_shadowing_the_job_pin_fails_the_harness():
    def mutate(workflow: dict) -> None:
        _step(workflow)["env"] = {
            "KLT": f"uvx --from klayout-tools=={STALE_VERSION} klt"}
    with pytest.raises(HarnessRefused, match="shadows the job-level pin"):
        run_step(_workflow(mutate), REPO)


# =============================================================================
# The gate `make verify-fast` runs: clean on the committed tree.
# =============================================================================
def test_committed_envelopes_pass_under_the_workflows_own_pin():
    res = run_step(WORKFLOW, REPO)
    assert res.returncode == 0, _report(res)
    pinned = _pinned_version(_locate(WORKFLOW)[1])
    assert STALE_VERSION != pinned, "the stale control equals the live pin"
    for env in ENVELOPES:
        line = [ln for ln in res.stdout.splitlines() if env in ln]
        assert len(line) == 1 and pinned in line[0], _report(res)


def test_an_ambient_klt_is_never_the_pins_source(monkeypatch, tmp_path):
    """"With no caller-supplied KLT", in both directions: a supplied pin that
    would satisfy the step cannot rescue an absent one, and a wrong one
    cannot break a present one."""
    tree = _tree(tmp_path)
    absent = _workflow(_with_job_klt(None))
    monkeypatch.setenv("KLT", _locate(WORKFLOW)[1])
    _assert_rejected(run_step(absent, tree),
                     "REFUSED: cannot read the pinned version from $KLT", "''")
    monkeypatch.setenv("KLT", f"uvx --from klayout-tools=={STALE_VERSION} klt")
    assert run_step(WORKFLOW, tree).returncode == 0


# =============================================================================
# The #1089 shape: a pin bump landed and the envelope did not follow.
# =============================================================================
@pytest.mark.parametrize("rel", ENVELOPES, ids=["item-9", "item-10"])
def test_a_stale_producer_version_is_rejected_naming_path_recorded_and_pinned(rel, tmp_path):
    pinned = _pinned_version(_locate(WORKFLOW)[1])
    res = run_step(WORKFLOW, _tree(tmp_path, rel, _set_version(STALE_VERSION)))
    _assert_rejected(
        res, f"{rel} was attested by klt {STALE_VERSION}, not the pinned {pinned}")
    for env in ENVELOPES:
        accepted_before = ENVELOPES.index(env) < ENVELOPES.index(rel)
        assert (env in res.stdout) == accepted_before, _report(res)


# =============================================================================
# REFUSED: the step cannot read a producer version, or cannot read the pin.
# =============================================================================
@pytest.mark.parametrize("rel, change", [
    (ITEM9, _drop_version),
    (ITEM10, _set_version(None)),
], ids=["missing-version-field:item-9", "json-null-version:item-10"])
def test_an_envelope_recording_no_version_is_refused(rel, change, tmp_path):
    res = run_step(WORKFLOW, _tree(tmp_path, rel, change))
    _assert_rejected(res, f"REFUSED: {rel} records no provenance.klt_version")


@pytest.mark.parametrize("klt", [
    "uvx --with pyyaml --from klayout-tools klt",
    'uvx --with pyyaml --from "klayout-tools>=0.9.2" klt',
    None,
], ids=["unversioned-package", "range-pin", "absent-KLT"])
def test_a_pin_the_step_cannot_read_is_refused(klt, tmp_path):
    res = run_step(_workflow(_with_job_klt(klt)), _tree(tmp_path))
    _assert_rejected(res, "REFUSED: cannot read the pinned version from $KLT",
                     "''" if klt is None else klt)


# =============================================================================
# This file's own wiring (the shape tools/test_manifest.py pins): enumerated
# in the two targets that name files, and collected by directory in `verify`.
# =============================================================================
def test_this_file_runs_under_verify_fast_controls_and_verify():
    """Its own wiring, through the one shared Makefile parser (#855: a line
    commented out is not recipe text). Known blind spot, the collection
    inventory's: a `-k` that deselects every test here still passes this."""
    for target in ("verify-fast", "controls"):
        assert THIS_FILE in _pytest_words(makefile_recipe(target)), (
            f"{THIS_FILE} is not an argument of a pytest job in `make {target}`")
    assert "tools/" in _pytest_words(makefile_recipe("verify")), (
        "`make verify` no longer collects tools/ by directory, so it no "
        f"longer reaches {THIS_FILE}")


def test_a_mention_outside_a_pytest_job_is_not_wiring():
    """The defeater of the naive `path in recipe` form: the name is in the
    recipe, make never runs it."""
    recipe = ('\t@$(RUN) "python -m pytest tools/test_rungs_gate.py -q" \\\n'
              '\t  "echo tools/test_signoff_klt_version.py"')
    assert THIS_FILE in recipe, "the naive substring form would pass here"
    assert THIS_FILE not in _pytest_words(recipe)
