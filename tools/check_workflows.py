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
    return 0


if __name__ == "__main__":
    sys.exit(main())
