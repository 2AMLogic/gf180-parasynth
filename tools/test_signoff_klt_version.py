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
executed via a temporary script file under the workflow's effective shell
(`bash -e {0}` when `shell:` is omitted on Linux, matching the runner;
`bash --noprofile --norc -eo pipefail {0}` only when `shell: bash` is set
explicitly; `sh -e {0}` for `shell: sh`), `cwd` at the tree holding the
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
         pin (each must terminate on its own `AssertionError: REFUSED:` line).
         A nonzero exit alone is not a catch, and neither is an earlier
         warning on stderr followed by an unrelated error: the step's
         TERMINAL Python exception type and message are asserted, so a crash
         for any other reason fails the test.

The harness REFUSES (`HarnessRefused`) instead of reporting when it cannot
speak for the gate: no step of that name, or two; a step carrying `if:` or
`continue-on-error:` (green here, never able to fail a run -- #304's shape,
which `tools/test_check_workflows.py` first measured); a step-level
`env.KLT` shadowing the job-level pin it reads; an unsupported `shell:`; a
missing shell executable. Those are this file's own rule-8 inputs, and each is
a committed control below, beside the start-red stubs (a no-op with the step's
shape, and a piped heredoc under the default `bash -e` shell) that the stale
control must fail on for the one reason that the step accepted.

Two blind spots of the step were fixed in #1100: `provenance: null` (or any
non-object) ended in an `AttributeError` instead of the `REFUSED:` line, and a
quoted pin, `--from "klayout-tools==0.9.2"`, was read as `0.9.2"` -- a false
mismatch. The step now refuses non-object provenance by name and reads the
pin with `shlex.split`. The quoted-pin cases exercise only the version
comparison; they do not show that the earlier `$KLT signoff` steps accept
embedded quotes, so the production job keeps its unquoted spelling.
Out of scope by the issue: a hand-edited
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
import tempfile

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
DEFAULT_LINUX_SHELL = ("bash", "-e")
EXPLICIT_SHELLS = {
    "bash": ("bash", "--noprofile", "--norc", "-eo", "pipefail"),
    "sh": ("sh", "-e"),
}
STEP_TIMEOUT_S = 120
_RUN_VALUE_OPTS = frozenset({"--jobs", "--timeout", "--tail", "--json"})
_PYTEST_VALUE_OPTS = frozenset({
    "-c", "-k", "-m", "-n", "-o", "-p", "-W",
    "--basetemp", "--confcutdir", "--deselect", "--ignore", "--ignore-glob",
    "--junitxml", "--log-level", "--maxfail", "--override-ini", "--rootdir",
    "--tb",
})
_PYTEST_NO_RUN_OPTS = frozenset({"--collect-only", "--co", "--help", "-h", "--version"})


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


def _effective_shell(workflow: dict, job: dict, step: dict) -> tuple[str, ...]:
    """The shell command GitHub Actions invokes for a Linux `run:` step.

    Precedence: step `shell:` > job `defaults.run.shell` > workflow
    `defaults.run.shell` > Linux default (`bash -e {0}`, without `pipefail`).
    Explicit `shell: bash` enables `--noprofile --norc -eo pipefail`."""
    shell = None
    for candidate in (
        step.get("shell"),
        ((job.get("defaults") or {}).get("run") or {}).get("shell"),
        ((workflow.get("defaults") or {}).get("run") or {}).get("shell"),
    ):
        if candidate is not None:
            shell = candidate
            break
    if shell is None:
        return DEFAULT_LINUX_SHELL
    if isinstance(shell, str) and shell in EXPLICIT_SHELLS:
        return EXPLICIT_SHELLS[shell]
    raise HarnessRefused(
        f"step {STEP_NAME!r} uses unsupported effective shell {shell!r}: "
        f"cannot run it as shipped")


def _locate(workflow) -> tuple[dict, str | None, tuple[str, ...]]:
    """The named step, the job-level `KLT` it will see, and its effective shell.

    REFUSES on anything that would let a green here mean something other
    than "the shipped gate passes on this tree"."""
    wf = _load(workflow) or {}
    jobs = wf.get("jobs") or {}
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
    shell_cmd = _effective_shell(wf, job, step)
    return step, (job.get("env") or {}).get("KLT"), shell_cmd


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
    """Run the shipped step's `run:` text under the workflow's effective shell."""
    step, klt, shell_cmd = _locate(workflow)
    if shutil.which(shell_cmd[0]) is None:
        raise HarnessRefused(
            f"no {shell_cmd[0]} on PATH: the step cannot be run as shipped")
    env = {k: v for k, v in os.environ.items() if k not in ("KLT", "BASH_ENV")}
    if klt is not None:
        env["KLT"] = klt
    with tempfile.TemporaryDirectory() as tmpdir:
        script = pathlib.Path(tmpdir) / "step.sh"
        script.write_text(step["run"])
        try:
            return subprocess.run(
                [*shell_cmd, str(script)], cwd=cwd, env=env,
                capture_output=True, text=True, timeout=STEP_TIMEOUT_S)
        except subprocess.TimeoutExpired as e:
            raise HarnessRefused(
                f"the step did not finish within {STEP_TIMEOUT_S} s: it is no "
                f"longer an offline stdlib check") from e


def _report(res: subprocess.CompletedProcess) -> str:
    return (f"exit {res.returncode}\n--- stdout\n{res.stdout}"
            f"--- stderr\n{res.stderr}")


def _assert_rejected(
    res: subprocess.CompletedProcess,
    expected_message: str,
    *,
    exc_type: str = "AssertionError",
) -> None:
    """The step failed, and its TERMINAL failure is the expected Python
    exception type and message.

    A nonzero exit alone is not a catch (verification-rules rule 5, third
    condition: the intended assertion is the one that fails), and neither is
    an earlier warning on stderr followed by an unrelated error."""
    assert res.returncode != 0, (
        f"the step ACCEPTED an envelope it should have refused\n{_report(res)}")
    lines = [ln for ln in res.stderr.splitlines() if ln.strip()]
    tb_indices = [i for i, ln in enumerate(lines)
                  if ln == "Traceback (most recent call last):"]
    assert tb_indices, (
        f"the step failed without a Python traceback\n{_report(res)}")
    tail = lines[tb_indices[-1] + 1:]
    assert (len(tail) >= 2 and tail[0].startswith("  File ")
            and all(ln.startswith("  ") for ln in tail[:-1])), (
        f"the step's terminal stderr is not a Python exception traceback\n"
        f"{_report(res)}")
    expected = f"{exc_type}: {expected_message}"
    assert tail[-1] == expected, (
        f"the step failed, but its terminal exception was {tail[-1]!r}, "
        f"not {expected!r}\n{_report(res)}")


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


def _logical_commands(recipe: str) -> list[str]:
    """Join Make line continuations (trailing `\\`) into logical commands."""
    cmds: list[str] = []
    buf: list[str] = []
    for raw in recipe.split("\n"):
        line = raw.rstrip()
        if line.endswith("\\"):
            buf.append(line[:-1])
        else:
            buf.append(line)
            joined = " ".join(buf).strip()
            if joined:
                cmds.append(joined)
            buf = []
    if buf:
        joined = " ".join(buf).strip()
        if joined:
            cmds.append(joined)
    return cmds


def _recipe_jobs(recipe: str) -> list[list[str]]:
    """Return each shell job executed by a Make recipe as a token list.

    A `$(RUN)` invocation (`tools/run_all.py`) executes each positional
    argument string as a separate job; any other recipe line executes as one
    command directly (so `echo "$(PY) -m pytest ..."` stays `['echo', ...]`,
    not a pytest job)."""
    jobs: list[list[str]] = []
    for cmd in _logical_commands(recipe):
        tokens = shlex.split(cmd.lstrip("@-+ \t"))
        if not tokens:
            continue
        if tokens[0] == "$(RUN)":
            rest = tokens[1:]
        elif tokens[:2] == ["$(PY)", "tools/run_all.py"]:
            rest = tokens[2:]
        else:
            jobs.append(tokens)
            continue
        i = 0
        end_opts = False
        while i < len(rest):
            tok = rest[i]
            if not end_opts and tok == "--":
                end_opts = True
                i += 1
            elif not end_opts and tok in _RUN_VALUE_OPTS:
                i += 2
            elif not end_opts and tok.startswith("-"):
                i += 1
            else:
                jobs.append(shlex.split(tok))
                i += 1
    return jobs


def _pytest_args_of_job(words: list[str]) -> list[str]:
    """If `words` directly invokes pytest (`pytest ...` or `<python> -m pytest
    ...`, optionally preceded by `VAR=VALUE` assignments), return its argument
    list; otherwise return `[]`."""
    if any(tok in ("|", "||") for tok in words):
        return []
    i = 0
    while i < len(words) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", words[i]):
        i += 1
    if i >= len(words):
        return []
    head = words[i]
    name = pathlib.PurePosixPath(head).name
    if name == "pytest":
        return words[i + 1:]
    is_python = (
        head in ("$(PY)", "$(PYTHON)")
        or re.fullmatch(r"python(\d+(\.\d+)*)?", name) is not None
    )
    if is_python and words[i + 1 : i + 3] == ["-m", "pytest"]:
        return words[i + 3:]
    return []


def _pytest_targets(recipe: str) -> set[str]:
    """Every positional test target executed by a real pytest invocation in a
    Make recipe. Commands that merely print or wrap a pytest command string
    (e.g. `echo $(PY) -m pytest ...`), non-executing modes (`--collect-only`),
    and paths passed to pytest option flags (`--ignore`, `-k`) are excluded."""
    targets: set[str] = set()
    for job in _recipe_jobs(recipe):
        args = _pytest_args_of_job(job)
        if not args or any(tok in _PYTEST_NO_RUN_OPTS for tok in args):
            continue
        i = 0
        end_opts = False
        while i < len(args):
            tok = args[i]
            if not end_opts and tok == "--":
                end_opts = True
                i += 1
            elif not end_opts and tok in _PYTEST_VALUE_OPTS:
                i += 2
            elif not end_opts and tok.startswith("-"):
                i += 1
            else:
                targets.add(tok)
                i += 1
    return targets


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
    pinned = _pinned_version(_locate(WORKFLOW)[1])
    noop = "python3 - <<'PY'\npass\nPY\n"
    res = run_step(_workflow(lambda wf: _step(wf).__setitem__("run", noop)),
                   _tree(tmp_path, ITEM9, _set_version(STALE_VERSION)))
    assert res.returncode == 0 and res.stdout == "" and res.stderr == "", _report(res)
    with pytest.raises(AssertionError, match="ACCEPTED"):
        _assert_rejected(
            res,
            f"{ITEM9} was attested by klt {STALE_VERSION}, not the pinned {pinned}: "
            "a pin bump landed without re-attesting the envelope")


def test_start_red_a_piped_step_under_default_shell_is_seen_to_accept_a_stale_envelope(tmp_path):
    """Under the workflow's default Linux shell (`bash -e`, no pipefail),
    piping the heredoc into `cat` prints the stale-envelope assertion on stderr
    yet exits 0. The harness must run under that effective shell and fail the
    stale control rather than masking the bug with `pipefail`."""
    pinned = _pinned_version(_locate(WORKFLOW)[1])

    def pipe_to_cat(wf: dict) -> None:
        step = _step(wf)
        old = "python3 - <<'PY'"
        assert old in step["run"]
        step["run"] = step["run"].replace(old, f"{old} | cat", 1)

    res = run_step(_workflow(pipe_to_cat),
                   _tree(tmp_path, ITEM9, _set_version(STALE_VERSION)))
    assert res.returncode == 0, _report(res)
    assert f"{ITEM9} was attested by klt {STALE_VERSION}" in res.stderr, _report(res)
    with pytest.raises(AssertionError, match="ACCEPTED"):
        _assert_rejected(
            res,
            f"{ITEM9} was attested by klt {STALE_VERSION}, not the pinned {pinned}: "
            "a pin bump landed without re-attesting the envelope")


@pytest.mark.parametrize("where", ["step", "job-defaults", "workflow-defaults"])
def test_explicit_shell_bash_enables_pipefail_matching_runner(tmp_path, where):
    """When `shell: bash` is set explicitly at step, job, or workflow level,
    GitHub Actions runs `bash --noprofile --norc -eo pipefail {0}`, so the
    piped heredoc fails with exit 1 instead of masking the assertion."""
    pinned = _pinned_version(_locate(WORKFLOW)[1])

    def mutate(wf: dict) -> None:
        step = _step(wf)
        step["run"] = step["run"].replace("python3 - <<'PY'", "python3 - <<'PY' | cat", 1)
        if where == "step":
            step["shell"] = "bash"
        elif where == "job-defaults":
            wf["jobs"][JOB]["defaults"] = {"run": {"shell": "bash"}}
        else:
            wf["defaults"] = {"run": {"shell": "bash"}}

    res = run_step(_workflow(mutate),
                   _tree(tmp_path, ITEM9, _set_version(STALE_VERSION)))
    _assert_rejected(
        res,
        f"{ITEM9} was attested by klt {STALE_VERSION}, not the pinned {pinned}: "
        "a pin bump landed without re-attesting the envelope")


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
# This harness's own rule-8 inputs: a step that is green here and dead in CI,
# or an unrelated failure disguised by an earlier warning on stderr.
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


def test_an_unsupported_effective_shell_fails_the_harness():
    def mutate(workflow: dict) -> None:
        _step(workflow)["shell"] = "pwsh"
    with pytest.raises(HarnessRefused, match="unsupported effective shell"):
        run_step(_workflow(mutate), REPO)


@pytest.mark.parametrize("mode, match", [
    ("warning-then-shell-error", "without a Python traceback"),
    ("warning-then-unrelated-exception", "terminal exception"),
    ("wrong-exception-type", "terminal exception"),
    ("traceback-then-trailing-shell-error", "not a Python exception traceback"),
])
def test_warning_before_unrelated_error_does_not_satisfy_rejected(tmp_path, mode, match):
    """An expected diagnostic printed earlier on stderr (or raised and masked
    before an unrelated error) must NOT satisfy `_assert_rejected`."""
    expected = f"REFUSED: {ITEM9} records no provenance.klt_version"
    old_assert = 'assert got, f"REFUSED: {path} records no provenance.klt_version"'

    def mutate(wf: dict) -> None:
        step = _step(wf)
        assert old_assert in step["run"]
        if mode == "warning-then-shell-error":
            step["run"] = step["run"].replace(
                old_assert,
                'if not got:\n'
                '        print(f"REFUSED: {path} records no provenance.klt_version", '
                'file=__import__("sys").stderr)\n'
                '        continue',
                1,
            ) + "\ncat missing-input-file\n"
        elif mode == "warning-then-unrelated-exception":
            step["run"] = step["run"].replace(
                old_assert,
                'if not got:\n'
                '        print(f"REFUSED: {path} records no provenance.klt_version", '
                'file=__import__("sys").stderr)\n'
                '        raise RuntimeError("unrelated failure")',
                1,
            )
        elif mode == "wrong-exception-type":
            step["run"] = step["run"].replace(
                old_assert,
                'if not got:\n'
                '        raise ValueError(f"REFUSED: {path} records no provenance.klt_version")',
                1,
            )
        else:
            step["run"] = step["run"].replace(
                "python3 - <<'PY'", "python3 - <<'PY' || true", 1
            ) + "\ncat missing-input-file\n"

    res = run_step(_workflow(mutate), _tree(tmp_path, ITEM9, _drop_version))
    assert res.returncode != 0, _report(res)
    assert expected in res.stderr, _report(res)
    with pytest.raises(AssertionError, match=match):
        _assert_rejected(res, expected)


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
    _assert_rejected(
        run_step(absent, tree),
        "REFUSED: cannot read the pinned version from $KLT ''; "
        "expected klayout-tools==X.Y.Z")
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
        res,
        f"{rel} was attested by klt {STALE_VERSION}, not the pinned {pinned}: "
        "a pin bump landed without re-attesting the envelope")
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
    klt_val = "" if klt is None else klt
    _assert_rejected(
        res,
        f"REFUSED: cannot read the pinned version from $KLT {klt_val!r}; "
        "expected klayout-tools==X.Y.Z")


# =============================================================================
# #1100: malformed provenance and quoted pins. Both used to end the step in
# the wrong place (an AttributeError; a false mismatch on `0.9.2"`).
# =============================================================================
def _set_provenance(value):
    def change(envelope: dict) -> None:
        envelope["provenance"] = value
    return change


def _drop_provenance(envelope: dict) -> None:
    del envelope["provenance"]


MALFORMED_PROVENANCE = [
    ("absent", _drop_provenance),
    ("null", _set_provenance(None)),
    ("list", _set_provenance([1])),
    ("string", _set_provenance("x")),
]


def _recorded_version() -> str:
    """The producer version the clean fixtures record, read straight from the
    envelope JSON: independent of both the step's parse and `_pinned_version`."""
    versions = {json.loads((REPO / e).read_text())["provenance"]["klt_version"]
                for e in ENVELOPES}
    assert len(versions) == 1, versions
    return versions.pop()


@pytest.mark.parametrize("rel", ENVELOPES, ids=["item-9", "item-10"])
@pytest.mark.parametrize("kind, change", MALFORMED_PROVENANCE,
                         ids=[k for k, _ in MALFORMED_PROVENANCE])
def test_malformed_provenance_ends_in_the_refused_line_not_a_crash(
        rel, kind, change, tmp_path):
    res = run_step(WORKFLOW, _tree(tmp_path, rel, change))
    _assert_rejected(res, f"REFUSED: {rel} records no provenance.klt_version")
    # a malformed second envelope comes after a good first one
    assert (ITEM9 in res.stdout) == (rel == ITEM10), _report(res)


def _quoted_klt(version: str, quote: str) -> str:
    return (f"uvx --with pyyaml --from {quote}klayout-tools=={version}{quote} klt")


@pytest.mark.parametrize("quote", ["", '"', "'"], ids=["unquoted", "double", "single"])
def test_the_exact_pin_passes_unquoted_and_in_matching_quotes(quote, tmp_path):
    version = _recorded_version()
    res = run_step(_workflow(_with_job_klt(_quoted_klt(version, quote))),
                   _tree(tmp_path))
    assert res.returncode == 0, _report(res)
    for env in ENVELOPES:
        line = [ln for ln in res.stdout.splitlines() if env in ln]
        assert len(line) == 1 and f"== pinned {version}" in line[0], _report(res)
        assert '"' not in line[0] and "'" not in line[0], _report(res)


@pytest.mark.parametrize("quote", ['"', "'"], ids=["double", "single"])
def test_a_stale_envelope_under_a_quoted_pin_names_the_real_pin(quote, tmp_path):
    version = _recorded_version()
    res = run_step(_workflow(_with_job_klt(_quoted_klt(version, quote))),
                   _tree(tmp_path, ITEM10, _set_version(STALE_VERSION)))
    _assert_rejected(
        res,
        f"{ITEM10} was attested by klt {STALE_VERSION}, not the pinned {version}: "
        "a pin bump landed without re-attesting the envelope")


@pytest.mark.parametrize("klt", [
    'uvx --with pyyaml --from "klayout-tools==0.9.2 klt',
    "uvx --with pyyaml --from 'klayout-tools==0.9.2\" klt",
    'uvx --with pyyaml --from "klayout-tools==0.9.2\'" klt',
    'uvx --with pyyaml --from "klayout-tools==0.9.2,<1" klt',
    "uvx --with pyyaml klayout-tools==0.9.2 klt --from",
], ids=["unmatched-quote", "mismatched-quotes", "embedded-quote", "range-suffix",
        "dangling-from"])
def test_an_unreadable_quoted_pin_is_refused(klt, tmp_path):
    res = run_step(_workflow(_with_job_klt(klt)), _tree(tmp_path))
    _assert_rejected(
        res,
        f"REFUSED: cannot read the pinned version from $KLT {klt!r}; "
        "expected klayout-tools==X.Y.Z")


# =============================================================================
# This file's own wiring (the shape tools/test_manifest.py pins): enumerated
# in the two targets that name files, and collected by directory in `verify`.
# =============================================================================
def test_this_file_runs_under_verify_fast_controls_and_verify():
    """Its own wiring, through the one shared Makefile parser (#855: a line
    commented out is not recipe text). Known blind spot, the collection
    inventory's: a `-k` that deselects every test here still passes this."""
    for target in ("verify-fast", "controls"):
        assert THIS_FILE in _pytest_targets(makefile_recipe(target)), (
            f"{THIS_FILE} is not a target of a pytest invocation in `make {target}`")
    assert "tools/" in _pytest_targets(makefile_recipe("verify")), (
        "`make verify` no longer collects tools/ by directory, so it no "
        f"longer reaches {THIS_FILE}")


@pytest.mark.parametrize("recipe", [
    ('\t@$(RUN) "python -m pytest tools/test_rungs_gate.py -q" \\\n'
     '\t  "echo tools/test_signoff_klt_version.py"'),
    ('\t@$(RUN) "python -m pytest tools/test_rungs_gate.py -q" \\\n'
     '\t  "echo $(PY) -m pytest tools/test_signoff_klt_version.py -q"'),
    '\t@echo "$(PY) -m pytest tools/test_signoff_klt_version.py -q"',
    ('\t@$(RUN) "$(PY) -m pytest --ignore tools/test_signoff_klt_version.py '
     'tools/test_rungs_gate.py -q"'),
    '\t@$(RUN) "$(PY) -m pytest --collect-only tools/test_signoff_klt_version.py -q"',
], ids=[
    "echo-path-only",
    "echo-prefixed-pytest-job",
    "recipe-level-echo",
    "pytest-ignore-option",
    "pytest-collect-only",
])
def test_a_mention_outside_a_pytest_job_is_not_wiring(recipe):
    """Defeaters of naive substring or `'pytest' in w` checks: the path (and
    even `pytest`) is in the recipe, yet Make never executes this file."""
    assert THIS_FILE in recipe, "the naive substring form would pass here"
    assert THIS_FILE not in _pytest_targets(recipe)
