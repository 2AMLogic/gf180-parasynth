#!/usr/bin/env python3
"""Check the checking machinery. Workflow files are the one artifact nothing checks.

WHY. Four CI defects landed in one session, every one of them inside a workflow
file, every one from the same cause: the YAML was committed without ever being
executed.

  dag.yml       invalid YAML -- GitHub does not fail a PR for an unparseable
                workflow, it silently does not register it
  nightly.yml   scheduled only, so it had never run at all
  rungs.yml     a step written against `origin/main`, which the runner does
                not fetch
  nightly.yml   a step written against `sound_report.py --all --out`, an
                interface that did not exist

**An invalid workflow is indistinguishable from a workflow that passed.** That
is the same failure as a check that cannot run looking like a check that
passes -- rebuilt one level up, in the machinery that does the checking.

This parses every workflow, and asserts the properties that silently break one.
"""
from __future__ import annotations
import pathlib, re, sys

try:
    import yaml
except ImportError:
    print("pyyaml not installed; cannot check workflows", file=sys.stderr)
    raise SystemExit(2)          # no evidence, not "no problem"

WF = pathlib.Path(__file__).resolve().parent.parent / ".github" / "workflows"


# =============================================================================
# "the step exists" is not "the step can fail the job" -- issue #307.
#
# `test_manifest.py` and `test_check_surge_waveform_comment.py` each guard
# their own CI wiring by asserting a substring appears in a named job's
# `run:` strings. That catches a deleted or renamed step, but a step with
# `if: false` or `continue-on-error: true` still matches the substring and
# still never blocks a pull request -- both measured, during #304's review,
# to leave the guard green.
# =============================================================================
def _constant_false(value: object) -> bool:
    """Whether an `if:` value can never be true. Handles bare YAML `false`
    and the string forms GitHub accepts (`"false"`, `"${{ false }}"`) --
    not general expression evaluation, which no control here exercises."""
    if value is None:
        return False
    if isinstance(value, bool):
        return value is False
    if isinstance(value, str):
        s = value.strip()
        if s.startswith("${{") and s.endswith("}}"):
            s = s[3:-2].strip()
        return s.lower() == "false"
    return False


def _job_excludes_pull_request(job: dict) -> bool:
    """Whether a job-level `if:` rules out a `pull_request` run. Only the
    two forms this repository actually writes: constant-false, and an
    `event_name` allowlist/denylist that never names `pull_request` (e.g.
    rungs.yml's `rtl-full`: `workflow_dispatch' || ... == 'schedule'`)."""
    cond = job.get("if")
    if cond is None:
        return False
    if _constant_false(cond):
        return True
    if isinstance(cond, str):
        s = cond.strip()
        if s.startswith("${{") and s.endswith("}}"):
            s = s[3:-2].strip()
        if "event_name" in s and "pull_request" not in s:
            return True
    return False


def step_would_block_pull_request(workflow, job_name: str, substring: str):
    """Whether a step in `job_name` whose `run:` contains `substring` would
    actually fail a `pull_request` run -- not just whether it is present.

    `workflow` is an already-`yaml.safe_load`ed workflow mapping, or a path
    to a workflow file. Checks, in order: the job is not excluded from
    `pull_request` by its own `if:`; a step with a matching `run:` exists in
    it; that step's `if:` is not constant-false; that step does not set
    `continue-on-error: true`.

    Returns `(blocks, reason)` -- `reason` is always a specific sentence, so
    a caller's assertion message does not degrade to "found: False".
    """
    if isinstance(workflow, (str, pathlib.Path)):
        workflow = yaml.safe_load(pathlib.Path(workflow).read_text())
    jobs = (workflow or {}).get("jobs") or {}
    if job_name not in jobs:
        return False, f"no job named {job_name!r} in this workflow"
    job = jobs[job_name] or {}
    if _job_excludes_pull_request(job):
        return False, (f"job {job_name!r} has a job-level `if:` "
                        f"({job.get('if')!r}) that excludes pull_request, so "
                        f"no step in it can block a pull request")
    steps = [s for s in (job.get("steps") or []) if isinstance(s, dict)]
    matches = [s for s in steps if substring in str(s.get("run", ""))]
    if not matches:
        return False, (f"no step in job {job_name!r} runs a command "
                        f"containing {substring!r}")
    neutralised = []
    for s in matches:
        name = s.get("name", "<unnamed step>")
        if _constant_false(s.get("if")):
            neutralised.append(f"{name!r} has `if: {s.get('if')!r}`")
        elif s.get("continue-on-error") is True:
            neutralised.append(f"{name!r} sets continue-on-error: true")
        else:
            return True, f"step {name!r} in job {job_name!r} would block a pull_request run"
    return False, (f"every step in job {job_name!r} matching {substring!r} "
                    f"would not block a pull_request run: " + "; ".join(neutralised))


def main() -> int:
    files = sorted(WF.glob("*.yml")) + sorted(WF.glob("*.yaml"))
    if not files:
        print("no workflow files found", file=sys.stderr)
        return 2
    bad: list[str] = []

    for f in files:
        text = f.read_text()
        try:
            doc = yaml.safe_load(text)
        except yaml.YAMLError as e:
            bad.append(f"{f.name}: INVALID YAML -- {str(e).splitlines()[0]}")
            continue
        if not isinstance(doc, dict):
            bad.append(f"{f.name}: does not parse to a mapping")
            continue

        # `on:` is parsed by YAML 1.1 as the boolean True. Accept either.
        triggers = doc.get("on", doc.get(True))
        if triggers is None:
            bad.append(f"{f.name}: no triggers")
        elif isinstance(triggers, dict):
            # A workflow that ONLY runs on a schedule has never run when you
            # commit it, so it cannot have been tested. Require a manual
            # trigger so it can be exercised on demand.
            if set(triggers) <= {"schedule"}:
                bad.append(f"{f.name}: schedule-only -- add workflow_dispatch "
                           f"so it can be run before it is trusted")

        for jname, job in (doc.get("jobs") or {}).items():
            steps = (job or {}).get("steps") or []
            run_text = "\n".join(s.get("run", "") for s in steps if isinstance(s, dict))
            uses = [s.get("uses", "") for s in steps if isinstance(s, dict)]
            checkout = any(u.startswith("actions/checkout") for u in uses)

            # A step comparing against origin/<branch> needs the remote ref,
            # which a default shallow single-branch checkout does not provide.
            if re.search(r"\borigin/\w+", run_text):
                co = next((s for s in steps if isinstance(s, dict)
                           and s.get("uses", "").startswith("actions/checkout")), None)
                depth = ((co or {}).get("with") or {}).get("fetch-depth")
                if str(depth) != "0" and "git fetch" not in run_text:
                    bad.append(f"{f.name}:{jname}: uses origin/<ref> but the checkout "
                               f"is shallow and nothing fetches it")
            if run_text and not checkout:
                bad.append(f"{f.name}:{jname}: runs commands without actions/checkout")

    for b in bad:
        print(f"  {b}", file=sys.stderr)
    if bad:
        print(f"\n{len(bad)} workflow problem(s). These do not surface as a failed "
              f"build -- an unregistered workflow simply never runs.", file=sys.stderr)
        return 1
    print(f"{len(files)} workflow(s) parse and declare a usable trigger")
    return 0


if __name__ == "__main__":
    sys.exit(main())
