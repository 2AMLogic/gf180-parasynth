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
import pathlib, re, subprocess, sys
from dataclasses import dataclass, field

try:
    import yaml
except ImportError:
    print("pyyaml not installed; cannot check workflows", file=sys.stderr)
    raise SystemExit(2)          # no evidence, not "no problem"

REPO = pathlib.Path(__file__).resolve().parent.parent
WF = REPO / ".github" / "workflows"


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


def job_neutralised_reason(job: dict, job_name: str) -> str | None:
    """A sentence saying why a job can never fail a `pull_request` run, or
    None. Covers a job-level `if:` that excludes `pull_request` and a
    job-level `continue-on-error: true` (which turns every step failure in
    the job green). Shared so harnesses need no second parser (#1143)."""
    if _job_excludes_pull_request(job):
        return (f"job {job_name!r} has a job-level `if:` "
                f"({job.get('if')!r}) that excludes pull_request, so "
                f"no step in it can block a pull request")
    if job.get("continue-on-error") is True:
        return (f"job {job_name!r} sets a job-level `continue-on-error: "
                f"True`, so no step in it can block a pull request")
    return None


def step_would_block_pull_request(workflow, job_name: str, substring: str):
    """Whether a step in `job_name` whose `run:` contains `substring` would
    actually fail a `pull_request` run -- not just whether it is present.

    `workflow` is an already-`yaml.safe_load`ed workflow mapping, or a path
    to a workflow file. Checks, in order: the job is not excluded from
    `pull_request` by its own `if:`; a step with a matching `run:` exists in
    it; the job does not set `continue-on-error: true`; that step's `if:` is not constant-false; that step does not set
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
    job_reason = job_neutralised_reason(job, job_name)
    if job_reason:
        return False, job_reason
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


# =============================================================================
# "the shipped step order" -- issue #940, the #906 class.
#
# #906's fix is an ORDERING inside two workflow files: `compile_dag.py --check`
# must judge the README as committed before any `--run` rewrites it. rungs.yml
# runs both commands inside one step ("the roadmap matches the evidence");
# nightly.yml gates the pair across two steps ("the committed roadmap matches
# the committed evidence" before "record the slow evidence for the roadmap").
# tools/test_rungs_gate.py pins the SEMANTICS by driving compile_dag.main()
# directly -- correct, but nothing read the YAML, so reverting the fix back to
# run-then-check left every test green while shipping the defeated gate. These
# functions read what ships. main() wires them so that a step renamed or
# deleted away is a finding, never a silent exemption: a guard that cannot
# find what it guards must say so (docs/failure-modes.md).
#
# Single property by construction (verification-rules rule 4): the property is
# "check precedes rewrite"; the injected defect is the reverted order, shipped
# as a mutation of the REAL files' parsed shape in test_check_workflows.py.
#
# Known blind spot, bounded on purpose: the flags are read from a shell-level
# token scan of each `run:` line with comments stripped -- not a shell parse.
# An invocation assembled from variables (`CMD="--check"; compile_dag.py
# $CMD`) is invisible here; no workflow writes one.
# =============================================================================
ROADMAP_STEP = "the roadmap matches the evidence"
ROADMAP_CHECK_STEP = "the committed roadmap matches the committed evidence"
ROADMAP_RECORD_STEP = "record the slow evidence for the roadmap"


def _compile_dag_invocations(run_text: str) -> list[frozenset[str]]:
    """Each `tools/compile_dag.py` invocation's long flags, in file order.
    Shell comments are stripped first, so an inline `# ... --run ...` cannot
    count as an invocation."""
    out: list[frozenset[str]] = []
    for raw in run_text.split("\n"):
        toks = raw.split("#", 1)[0].split()
        if any(t.endswith("compile_dag.py") for t in toks):
            out.append(frozenset(t for t in toks if t.startswith("--")))
    return out


def roadmap_check_precedes_run_in_step(workflow, step_name: str = ROADMAP_STEP):
    """(ok, reason): within the one step named `step_name`, a
    `tools/compile_dag.py` invocation carrying `--check` precedes the first
    carrying `--run`. A missing, ambiguous, check-less or run-less step is
    not-ok with a specific reason -- a gate that cannot find what it guards
    reports, never passes."""
    if isinstance(workflow, (str, pathlib.Path)):
        workflow = yaml.safe_load(pathlib.Path(workflow).read_text())
    found = []
    for jname, job in ((workflow or {}).get("jobs") or {}).items():
        for s in (job or {}).get("steps") or []:
            if isinstance(s, dict) and s.get("name") == step_name:
                found.append((jname, s))
    if not found:
        return False, (f"no step named {step_name!r} -- the order gate has "
                       f"nothing to guard")
    if len(found) > 1:
        return False, (f"{len(found)} steps named {step_name!r} "
                       f"({', '.join(j for j, _ in found)}) -- ambiguous")
    jname, step = found[0]
    invs = _compile_dag_invocations(str(step.get("run", "")))
    if not invs:
        return False, (f"step {step_name!r} (job {jname!r}) does not invoke "
                       f"tools/compile_dag.py -- the order gate has nothing "
                       f"to guard")
    first_run = next((i for i, f in enumerate(invs) if "--run" in f), None)
    if first_run is None:
        return False, (f"step {step_name!r} (job {jname!r}) carries no --run "
                       f"invocation, so there is no rewrite for --check to "
                       f"precede")
    first_check = next((i for i, f in enumerate(invs) if "--check" in f), None)
    if first_check is None:
        return False, (f"step {step_name!r} (job {jname!r}) carries --run but "
                       f"no --check: nothing judges the README as committed "
                       f"before it is rewritten (#906)")
    if first_check < first_run:
        return True, (f"in step {step_name!r} (job {jname!r}), --check "
                      f"(invocation {first_check}) precedes --run "
                      f"(invocation {first_run})")
    return False, (f"in step {step_name!r} (job {jname!r}), --run (invocation "
                   f"{first_run}) precedes --check (invocation {first_check}):"
                   f" --run rewrites README.md, so the later --check judges "
                   f"the step's own output, not the committed tree (#906)")


def roadmap_check_step_precedes_record_step(workflow,
                                             check_step: str = ROADMAP_CHECK_STEP,
                                             record_step: str = ROADMAP_RECORD_STEP):
    """(ok, reason): in some one job, the step named `check_step` precedes the
    step named `record_step`; the check step invokes `tools/compile_dag.py`
    with `--check` and never `--run` (a check that also rewrites judges
    nothing); the record step invokes it with `--run`."""
    if isinstance(workflow, (str, pathlib.Path)):
        workflow = yaml.safe_load(pathlib.Path(workflow).read_text())
    named: dict[str, tuple[str, int, dict]] = {}
    for jname, job in ((workflow or {}).get("jobs") or {}).items():
        for i, s in enumerate((job or {}).get("steps") or []):
            if isinstance(s, dict) and s.get("name") in (check_step, record_step):
                named[s["name"]] = (jname, i, s)
    if check_step not in named:
        return False, (f"no step named {check_step!r} -- the order gate has "
                       f"nothing to guard")
    if record_step not in named:
        return False, (f"no step named {record_step!r} -- the order gate has "
                       f"nothing to guard")
    cj, ci, cstep = named[check_step]
    rj, ri, rstep = named[record_step]
    if cj != rj:
        return False, (f"{check_step!r} is in job {cj!r} but {record_step!r} "
                       f"is in {rj!r} -- the pair must gate one job")
    cinvs = _compile_dag_invocations(str(cstep.get("run", "")))
    if not cinvs or not any("--check" in f for f in cinvs):
        return False, (f"step {check_step!r} (job {cj!r}) does not invoke "
                       f"tools/compile_dag.py with --check")
    if any("--run" in f for f in cinvs):
        return False, (f"step {check_step!r} (job {cj!r}) also carries --run: "
                       f"it would rewrite the README it is judging (#906)")
    if not any("--run" in f
               for f in _compile_dag_invocations(str(rstep.get("run", "")))):
        return False, (f"step {record_step!r} (job {rj!r}) does not invoke "
                       f"tools/compile_dag.py with --run")
    if ci < ri:
        return True, (f"in job {cj!r}, {check_step!r} (step {ci}) precedes "
                      f"{record_step!r} (step {ri})")
    return False, (f"in job {cj!r}, {record_step!r} (step {ri}) precedes "
                   f"{check_step!r} (step {ci}): the regen would run before "
                   f"anything judged the committed README (#906)")


# =============================================================================
# "the path a workflow names exists" -- issue #629, the #622 class.
#
# `nightly.yml` tested a docs results file that has never existed, so the
# `reference-freshness` job was red for 6+ days regardless of real freshness.
# CLAUDE.md calls this an unsatisfiable gate. This is the standing check.
#
# CONTRACT (bounded on purpose; a full shell parse is out of scope):
#   * populations: step-level `run:` scalars in .github/workflows/*.yml|yaml,
#     and TAB-led recipe lines of the root `Makefile` / `Makefile.nightly`.
#   * a candidate is a literal, repo-relative token containing a `/` whose
#     first component is a directory of `git ls-files` (so the prefix list is
#     derived, never hand-written). Absolute paths, URLs and `~` are out of scope.
#   * tokens containing `$`, `${{ }}` or glob metacharacters are SKIPPED, by
#     reason, and counted -- never counted as checked.
#   * generated outputs that do not exist at checkout time are skipped-generated:
#     the two repo-wide roots in GENERATED_ROOTS, plus the narrow literal
#     entries in GENERATED_ALLOW, each with its producer. No tracked source
#     tree (docs/, tools/, ...) may be exempted wholesale.
#   * REFUSED (exit 2) when `git ls-files` cannot run or when zero candidates
#     were extracted -- a PASS over an empty population is not a PASS.
#
# KNOWN BLIND SPOTS (reported as skips or not seen at all, never as checked):
#   a path assembled by a shell variable or glob (skipped, counted); a typo in
#   the FIRST component (`doc/x` is not under any tracked prefix, so it is not
#   a candidate); whole-line `#` comments are ignored but inline ones are not;
#   `cd dir && cmd file` resolves `file` against the repo root, not `dir`.
# =============================================================================
GENERATED_ROOTS = {
    "build/": "build output root, written by make/CI steps before it is read",
    ".tools/": "toolchain cache root, populated by tools/setup_ci_oss_cad.py",
}
# Narrow literal path (or directory prefix ending in `/`) -> producer/reason.
GENERATED_ALLOW: dict[str, str] = {}

_GLOB_CHARS = set("*?[")
_SPLIT = re.compile(r"""[\s'"`;|&<>=]+""")
_EXPR = re.compile(r"\$\{\{.*?\}\}", re.S)


@dataclass
class PathScan:
    checked: int = 0
    skipped_dynamic: int = 0
    skipped_glob: int = 0
    skipped_generated: int = 0
    missing: list[str] = field(default_factory=list)   # "file:line: path"

    @property
    def refused(self) -> str | None:
        if self.checked + self.skipped_generated == 0:
            return "no literal repo paths were extracted from any workflow/Makefile"
        return None


def tracked_files(repo: pathlib.Path = REPO) -> list[str]:
    """`git ls-files`, or raise RuntimeError -- the caller REFUSES, never guesses."""
    try:
        r = subprocess.run(["git", "-C", str(repo), "ls-files"], capture_output=True,
                           text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError(f"git ls-files could not run: {e}") from e
    if r.returncode != 0:
        raise RuntimeError(f"git ls-files exited {r.returncode}: {r.stderr.strip()}")
    files = [l for l in r.stdout.splitlines() if l]
    if not files:
        raise RuntimeError("git ls-files returned no files")
    return files


def _run_blocks(path: pathlib.Path):
    """Yield (line, text) for each step-level `run:` scalar. For `|` blocks the
    line is exact per content line; otherwise it is the scalar's first line."""
    try:
        root = yaml.compose(path.read_text())
    except yaml.YAMLError:
        return                                  # main() already reports INVALID YAML
    def get(node, key):
        if isinstance(node, yaml.MappingNode):
            for k, v in node.value:
                if isinstance(k, yaml.ScalarNode) and k.value == key:
                    return v
        return None
    jobs = get(root, "jobs")
    if not isinstance(jobs, yaml.MappingNode):
        return
    for _, job in jobs.value:
        steps = get(job, "steps")
        if not isinstance(steps, yaml.SequenceNode):
            continue
        for step in steps.value:
            run = get(step, "run")
            if isinstance(run, yaml.ScalarNode):
                base = run.start_mark.line + 1
                literal = run.style == "|"
                for i, text in enumerate(run.value.split("\n")):
                    yield (base + 1 + i if literal else base), text


def _makefile_lines(path: pathlib.Path):
    for n, text in enumerate(path.read_text().splitlines(), 1):
        if text.startswith("\t"):
            yield n, text


def _classify(token: str, prefixes: set[str], tracked: set[str],
              tracked_dirs: set[str], repo: pathlib.Path):
    """Return None (not a candidate), 'dynamic', 'glob', 'generated',
    'ok' or 'missing'."""
    token = token.strip("(){},:").rstrip(".").split("#", 1)[0]
    token = re.sub(r":\d+(:\d+)?$", "", token)
    if token.startswith("./"):
        token = token[2:]
    if (not token or token.startswith(("/", "~", "-")) or "://" in token
            or "/" not in token):
        return None
    if "$" in token or "\x01" in token:
        # Only worth counting if the literal part could be a repo path.
        return "dynamic"
    if token.startswith(tuple(GENERATED_ROOTS)):
        return "generated"
    if token.split("/", 1)[0] not in prefixes:
        return None
    if any(c in _GLOB_CHARS for c in token):
        return "glob"
    for entry in GENERATED_ALLOW:
        if token == entry or (entry.endswith("/") and token.startswith(entry)):
            return "generated"
    t = token.rstrip("/")
    if t in tracked or t in tracked_dirs or (repo / t).exists():
        return "ok"
    return "missing"


def check_referenced_paths(sources, tracked: list[str],
                           repo: pathlib.Path = REPO) -> PathScan:
    """`sources` is an iterable of (display_name, [(line, text), ...]).
    Pure apart from the existence probe under `repo`."""
    tset = set(tracked)
    prefixes = {t.split("/", 1)[0] for t in tracked if "/" in t}
    dirs = {"/".join(t.split("/")[:i]) for t in tracked for i in range(1, t.count("/") + 1)}
    scan = PathScan()
    for name, lines in sources:
        for line, text in lines:
            if text.lstrip().startswith("#"):
                continue
            text = _EXPR.sub("$\x01", text)
            for tok in _SPLIT.split(text):
                kind = _classify(tok, prefixes, tset, dirs, repo)
                if kind is None:
                    continue
                if kind == "dynamic":
                    scan.skipped_dynamic += 1
                elif kind == "glob":
                    scan.skipped_glob += 1
                elif kind == "generated":
                    scan.skipped_generated += 1
                elif kind == "ok":
                    scan.checked += 1
                else:
                    scan.checked += 1
                    scan.missing.append(f"{name}:{line}: {tok.strip('(){},:').rstrip('.')}")
    return scan


def gather_sources(repo: pathlib.Path = REPO):
    wf = repo / ".github" / "workflows"
    out = []
    for f in sorted(wf.glob("*.yml")) + sorted(wf.glob("*.yaml")):
        out.append((f".github/workflows/{f.name}", list(_run_blocks(f))))
    for m in ("Makefile", "Makefile.nightly"):
        if (repo / m).is_file():
            out.append((m, list(_makefile_lines(repo / m))))
    return out


def main() -> int:
    files = sorted(WF.glob("*.yml")) + sorted(WF.glob("*.yaml"))
    if not files:
        print("no workflow files found", file=sys.stderr)
        return 2
    bad: list[str] = []
    parsed: list[tuple[str, dict]] = []

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
        parsed.append((f.name, doc))

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

    # -- the shipped #906 order itself (#940): the gate must FIND what it
    #    guards somewhere in the tree. A rename that evaded both gates below
    #    is reported, not rewarded with a green run.
    guarded: dict[str, str] = {}
    for name, doc in parsed:
        step_names = [s.get("name")
                      for job in (doc.get("jobs") or {}).values()
                      for s in (job or {}).get("steps") or []
                      if isinstance(s, dict)]
        if ROADMAP_STEP in step_names:
            ok, reason = roadmap_check_precedes_run_in_step(doc, ROADMAP_STEP)
            if ok:
                guarded["in-step"] = reason
            else:
                bad.append(f"{name}: {reason}")
        if ROADMAP_CHECK_STEP in step_names and ROADMAP_RECORD_STEP in step_names:
            ok, reason = roadmap_check_step_precedes_record_step(doc)
            if ok:
                guarded["pair"] = reason
            else:
                bad.append(f"{name}: {reason}")
    for what, why in (
            ("in-step", f"no workflow carries a step named {ROADMAP_STEP!r}"),
            ("pair", f"no workflow carries the {ROADMAP_CHECK_STEP!r} / "
                     f"{ROADMAP_RECORD_STEP!r} step pair")):
        if what not in guarded:
            bad.append(f"{why} -- the #906 order gate has nothing to guard")

    refused = None
    try:
        scan = check_referenced_paths(gather_sources(), tracked_files())
    except RuntimeError as e:
        scan, refused = None, str(e)
    if scan is not None:
        refused = scan.refused
        bad.extend(f"{m} -- path does not exist" for m in scan.missing)
        print(f"referenced paths: {scan.checked} checked, "
              f"{scan.skipped_dynamic} skipped-dynamic, {scan.skipped_glob} skipped-glob, "
              f"{scan.skipped_generated} skipped-generated")

    for b in bad:
        print(f"  {b}", file=sys.stderr)
    if refused and not bad:
        print(f"REFUSED: {refused}", file=sys.stderr)
        return 2
    if bad:
        print(f"\n{len(bad)} workflow problem(s). These do not surface as a failed "
              f"build -- an unregistered workflow simply never runs.", file=sys.stderr)
        return 1
    print(f"{len(files)} workflow(s) parse and declare a usable trigger")
    for reason in guarded.values():
        print(f"  roadmap order: {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
