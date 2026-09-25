#!/usr/bin/env python3
"""Re-derive prose claims from evidence. A claim in a document is not a fact.

WHY THIS EXISTS. `docs/failure-modes.md` mechanism 4 -- "claims outliving their
evidence" -- asks for exactly this tool, and then demonstrated the need itself.
Its own "What is already mechanical, and what is not" section was written on
2026-09-18 15:55 (`693b4e6`) and listed five items as **"Not yet"**. Two of them
were delivered LATER THE SAME DAY (the implementation-versus-fidelity
classification, in `tools/compile_dag.py`, citing "Issue #45" in its own
comment; and report staleness, closed via #69/#93). The document `CLAUDE.md`
tells every session to read FIRST understated what the project had, for a week,
and nothing could notice because nothing was checking.

`tools/compile_dag.py` already does this for DAG *nodes*: status is derived from
evidence, never asserted. This does the same thing one level down, for a
sentence in a Markdown file.

THE MARKER. See `docs/claim-markers.md` for the full convention. In brief, an
HTML comment on the line after the claim it backs:

    <!-- claim: test=model/test_audio_measure.py::test_t20_is_ln10_times_tau -->
    <!-- claim: test=tools/test_x.py::test_known_defect expect=fail issue=123 -->
    <!-- claim: grep="^srccheck:" in=fpga/Makefile covers=fpga/Makefile -->
    <!-- claim: absent="sensitivity_sweep" in=tools/*.py -->
    <!-- claim: commit=693b4e6 -->

THREE OUTCOMES, AND THE THIRD IS THE POINT.

    OK        the evidence was found and says what the claim says
    STALE     the evidence was found and CONTRADICTS the claim
    REFUSED   the evidence could not be evaluated at all

REFUSED is not a soft failure and it is not a pass. A test that was skipped
does not back a claim -- a skipped check looks exactly like a passing one, which
is the failure mode this repository keeps re-discovering (docs/failure-modes.md,
"preconditions assumed rather than asserted"). So a claim whose backing test
did not run comes back REFUSED, never OK.

EXIT CODES follow this repository's convention, where 2 means no evidence
rather than no problem:

    0   every claim OK
    1   at least one STALE   (a genuine finding: prose contradicts the tree)
    2   no STALE, but at least one REFUSED (the run could not answer)

STALE outranks REFUSED because it is the more actionable of the two, but both
are non-zero: `make claims` is red either way, deliberately.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import shlex
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

ROOT = pathlib.Path(__file__).resolve().parent.parent

MARKER = re.compile(r"<!--\s*claim:\s*(?P<body>.*?)\s*-->")

KINDS = ("test", "grep", "absent", "commit")
# Keys that are documentation for a human and carry no check of their own. They
# are listed so that a TYPO in a real key cannot hide as free-form prose --
# every unrecognised key is REFUSED, not ignored.
ANNOTATION_KEYS = ("issue", "why", "note")
MODIFIER_KEYS = ("in", "expect", "covers")

OK, STALE, REFUSED = "OK", "STALE", "REFUSED"


def _rel(p: pathlib.Path) -> str:
    """Repo-relative if it is under the repo; absolute otherwise (fixtures)."""
    try:
        return p.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return p.as_posix()


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------

@dataclass(eq=False)
class Claim:
    doc: pathlib.Path
    line: int
    raw: str
    prose: str
    attrs: dict[str, str] = field(default_factory=dict)
    kind: str = ""
    value: str = ""
    status: str = REFUSED
    detail: str = "not checked"

    @property
    def where(self) -> str:
        return f"{_rel(self.doc)}:{self.line}"

    def refuse(self, why: str) -> None:
        self.status, self.detail = REFUSED, why

    def stale(self, why: str) -> None:
        self.status, self.detail = STALE, why

    def ok(self, why: str) -> None:
        self.status, self.detail = OK, why


def parse_attrs(body: str) -> tuple[dict[str, str], str | None]:
    """`key=value key="value with spaces"` -> dict. Returns (attrs, error)."""
    try:
        tokens = shlex.split(body)
    except ValueError as exc:
        return {}, f"unparseable marker ({exc})"
    attrs: dict[str, str] = {}
    for tok in tokens:
        if "=" not in tok:
            return {}, f"token {tok!r} is not key=value"
        k, _, v = tok.partition("=")
        if k in attrs:
            return {}, f"duplicate key {k!r}"
        attrs[k] = v
    return attrs, None


def find_claims(doc: pathlib.Path) -> list[Claim]:
    """Every marker in one document, with the nearest prose line above it.

    The prose is only ever used for reporting -- it is what a reader needs to
    see next to a STALE verdict. Nothing is inferred from it.
    """
    out: list[Claim] = []
    lines = doc.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines, start=1):
        m = MARKER.search(line)
        if not m:
            continue
        prose = ""
        for back in range(i - 1, max(0, i - 6), -1):
            cand = lines[back - 1].strip()
            if cand and not MARKER.search(cand):
                prose = cand.lstrip("-*> ").strip()
                break
        # The marker may trail the claim on the same line.
        same = line[: m.start()].strip().lstrip("-*> ").strip()
        if same:
            prose = same
        out.append(Claim(doc=doc, line=i, raw=m.group("body"), prose=prose[:100]))
    return out


def validate(c: Claim) -> None:
    """Syntax only. A marker that cannot be understood is REFUSED, not skipped."""
    attrs, err = parse_attrs(c.raw)
    if err:
        c.refuse(err)
        return
    c.attrs = attrs
    kinds = [k for k in KINDS if k in attrs]
    if not kinds:
        c.refuse(f"no claim kind; expected one of {', '.join(KINDS)}")
        return
    if len(kinds) > 1:
        c.refuse(f"more than one claim kind: {', '.join(kinds)}")
        return
    c.kind, c.value = kinds[0], attrs[kinds[0]]
    if not c.value:
        c.refuse(f"{c.kind}= is empty")
        return
    allowed = set(KINDS) | set(MODIFIER_KEYS) | set(ANNOTATION_KEYS)
    unknown = sorted(set(attrs) - allowed)
    if unknown:
        c.refuse(f"unknown key(s): {', '.join(unknown)}")
        return
    if c.kind in ("grep", "absent") and "in" not in attrs:
        c.refuse(f"{c.kind}= needs in=<glob[,glob...]>")
        return
    if "expect" in attrs:
        if c.kind != "test":
            c.refuse("expect= applies only to test= claims")
            return
        if attrs["expect"] not in ("pass", "fail"):
            c.refuse(f"expect={attrs['expect']!r} is not pass or fail")
            return
    c.status, c.detail = "", "pending"


# --------------------------------------------------------------------------
# git helpers -- the ancestor/ordering pattern from tools/compile_dag.py
# --------------------------------------------------------------------------

def git(*a: str) -> str:
    try:
        r = subprocess.run(["git", *a], cwd=ROOT, capture_output=True,
                           text=True, timeout=30)
    except Exception:
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def commit_time(rel: str) -> int:
    """Commit time of a path's last change; 0 if untracked or absent.

    A path with uncommitted modifications counts as changed NOW -- otherwise
    editing a document would make its own fresh claims read as older than the
    files they describe, and the gate would be unsatisfiable in exactly the
    working tree where it is being fixed.
    """
    if git("status", "--porcelain", "--", rel):
        return int(time.time())
    out = git("log", "-1", "--format=%ct", "--", rel)
    return int(out) if out.isdigit() else 0


def is_ancestor(rev: str) -> bool:
    sha = git("rev-list", "-n1", rev)
    if not sha:
        return False
    r = subprocess.run(["git", "merge-base", "--is-ancestor", sha, "HEAD"],
                       cwd=ROOT, capture_output=True)
    return r.returncode == 0


# --------------------------------------------------------------------------
# globbing
# --------------------------------------------------------------------------

def static_prefix(pattern: str) -> pathlib.Path:
    """The deepest directory of a glob that contains no wildcard."""
    parts: list[str] = []
    for part in pathlib.PurePosixPath(pattern).parts[:-1]:
        if any(ch in part for ch in "*?["):
            break
        parts.append(part)
    return ROOT.joinpath(*parts)


def expand(spec: str) -> tuple[list[pathlib.Path], str | None]:
    """Comma-separated globs -> files. Error if a glob's base dir is absent.

    The base-directory check is the guard against a silent false green on an
    `absent=` claim: a typo'd path matches nothing, and "nothing matched" is
    indistinguishable from "the thing really is absent" unless the directory
    the pattern points into is known to exist.
    """
    files: list[pathlib.Path] = []
    for pattern in [p.strip() for p in spec.split(",") if p.strip()]:
        if pattern.startswith("/") or ".." in pathlib.PurePosixPath(pattern).parts:
            return [], f"glob {pattern!r} must be repo-relative"
        base = static_prefix(pattern)
        if not base.is_dir():
            return [], f"glob {pattern!r} points into {base.relative_to(ROOT) if base != ROOT else '.'}/ which does not exist"
        files += [p for p in sorted(ROOT.glob(pattern)) if p.is_file()]
    return sorted(set(files)), None


# --------------------------------------------------------------------------
# pytest: collect, then run, then map junit back onto the markers
# --------------------------------------------------------------------------

def junit_key(nodeid: str) -> tuple[str, str]:
    """`a/b.py::Cls::test_f` -> ('a.b.Cls', 'test_f'), junit-xml's identity."""
    path, _, rest = nodeid.partition("::")
    mod = path[:-3] if path.endswith(".py") else path
    mod = mod.replace("/", ".")
    parts = [p for p in rest.split("::") if p]
    name = parts[-1] if parts else ""
    cls = ".".join([mod] + parts[:-1])
    return cls, name


def collect(files: list[str], python: str) -> tuple[set[str], str | None]:
    """Every nodeid pytest can actually see in these files.

    Collected FIRST, separately, because pytest resolves its arguments before
    running anything: one unresolvable nodeid makes the whole invocation a
    usage error and NOTHING runs. Without this pass a single typo'd marker
    would turn every other claim REFUSED and look like a broken repository.
    """
    r = subprocess.run([python, "-m", "pytest", "--collect-only", "-q",
                        "--no-header", "-p", "no:cacheprovider", *files],
                       cwd=ROOT, capture_output=True, text=True, timeout=900)
    ids = {ln.strip() for ln in r.stdout.splitlines()
           if "::" in ln and not ln.startswith(("ERROR", "E  ", " "))}
    if not ids:
        tail = (r.stdout + r.stderr).strip().splitlines()
        return set(), f"pytest collected nothing: {tail[-1][:120] if tail else 'no output'}"
    return ids, None


def outcome_of(case: ET.Element) -> str:
    if case.find("failure") is not None:
        return "failed"
    if case.find("error") is not None:
        return "error"
    sk = case.find("skipped")
    if sk is not None:
        return "xfailed" if (sk.get("type") or "").endswith("xfail") else "skipped"
    return "passed"


def run_tests(nodeids: list[str], python: str) -> tuple[dict[str, str], str | None]:
    """Run the given nodeids once and return {(class,name) joined: outcome}."""
    if not nodeids:
        return {}, None
    with tempfile.TemporaryDirectory() as td:
        xml = pathlib.Path(td) / "claims.xml"
        subprocess.run([python, "-m", "pytest", "-q", "--no-header", "--tb=no",
                        "-p", "no:cacheprovider", f"--junit-xml={xml}", *nodeids],
                       cwd=ROOT, capture_output=True, text=True, timeout=3600)
        if not xml.exists():
            return {}, "pytest wrote no junit-xml report"
        try:
            tree = ET.parse(xml)
        except ET.ParseError as exc:
            return {}, f"junit-xml is unreadable: {exc}"
    out: dict[str, str] = {}
    for case in tree.iter("testcase"):
        out[f"{case.get('classname', '')}::{case.get('name', '')}"] = outcome_of(case)
    return out, None


# The decision the issue asked to be made EXPLICITLY rather than left ambiguous.
#
# A claim is backed only by a test that RAN. `skipped` is therefore never OK
# for either polarity -- a skipped test is the absence of evidence, and the
# whole point of REFUSED is that the absence of evidence must not be reported
# in the same voice as evidence.
#
# `xfailed` means the test ran and failed, in a file that already knew it would.
# That backs a tracked-defect claim (expect=fail) exactly as a plain failure
# does, and contradicts a plain claim exactly as a plain failure does.
#
# junit-xml records a non-strict XPASS as `passed` and a strict XPASS as a
# failure; the checker reports what the report says. Cite a specific nodeid
# rather than an xfail-marked family if that distinction matters to the claim.
VERDICT = {
    #  outcome    expect=pass   expect=fail
    "passed":   (OK,            STALE),
    "failed":   (STALE,         OK),
    "error":    (STALE,         OK),
    "xfailed":  (STALE,         OK),
    "skipped":  (REFUSED,       REFUSED),
    "mixed":    (REFUSED,       REFUSED),
}


# --------------------------------------------------------------------------
# the checks
# --------------------------------------------------------------------------

def check_covers(c: Claim) -> bool:
    """Does this claim predate the file it describes? Returns False if stale.

    The ancestor/ordering idea is `tools/compile_dag.py`'s STALE state, applied
    to a sentence instead of a node: evidence that passed before the code it
    covers changed no longer describes that code.
    """
    covers = c.attrs.get("covers")
    if not covers:
        return True
    rel = covers.strip()
    if not (ROOT / rel).exists():
        c.refuse(f"covers={rel} does not exist")
        return False
    doc_rel = _rel(c.doc)
    doc_t, cov_t = commit_time(doc_rel), commit_time(rel)
    if doc_t == 0 or cov_t == 0:
        c.refuse(f"cannot order {doc_rel} against {rel} (untracked, or no git history)")
        return False
    if cov_t > doc_t:
        c.stale(f"{rel} changed after this claim was last touched "
                f"({_when(cov_t)} vs {_when(doc_t)}) -- re-read the claim")
        return False
    return True


def _when(ts: int) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def check_grep(c: Claim) -> None:
    files, err = expand(c.attrs["in"])
    if err:
        c.refuse(err)
        return
    try:
        rx = re.compile(c.value, re.MULTILINE)
    except re.error as exc:
        c.refuse(f"bad regex {c.value!r}: {exc}")
        return
    hits: list[str] = []
    unreadable: list[str] = []
    for f in files:
        try:
            body = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            unreadable.append(f.relative_to(ROOT).as_posix())
            continue
        for n, line in enumerate(body.splitlines(), start=1):
            if rx.search(line):
                hits.append(f"{f.relative_to(ROOT).as_posix()}:{n}")
                break
    if c.kind == "grep":
        if not files:
            c.refuse(f"in={c.attrs['in']} matched no files")
            return
        if unreadable and not hits:
            c.refuse(f"could not read {len(unreadable)} of {len(files)} file(s)")
            return
        if hits:
            c.ok(f"{c.value!r} found at {hits[0]}"
                 + (f" (+{len(hits) - 1} more)" if len(hits) > 1 else ""))
        else:
            c.stale(f"{c.value!r} is in none of the {len(files)} file(s) matching "
                    f"{c.attrs['in']}")
        return
    # absent=
    if unreadable:
        c.refuse(f"could not read {len(unreadable)} file(s) under {c.attrs['in']}; "
                 "cannot assert absence")
        return
    if hits:
        c.stale(f"{c.value!r} now EXISTS at {hits[0]} -- this 'not yet' claim is done")
    else:
        c.ok(f"{c.value!r} matches nothing in the {len(files)} file(s) under "
             f"{c.attrs['in']}")


def check_commit(c: Claim) -> None:
    if not git("rev-parse", "--git-dir"):
        c.refuse("not a git checkout, or git is unavailable")
        return
    if not git("rev-parse", "--verify", "--quiet", f"{c.value}^{{commit}}"):
        c.stale(f"commit {c.value} is not in this repository")
        return
    if is_ancestor(c.value):
        c.ok(f"{c.value} is an ancestor of HEAD")
    else:
        c.stale(f"{c.value} exists but is not an ancestor of HEAD -- "
                "the claim rests on history this branch does not have")


def check(docs: list[pathlib.Path], python: str) -> list[Claim]:
    claims: list[Claim] = []
    for d in docs:
        claims += find_claims(d)
    for c in claims:
        validate(c)

    live = [c for c in claims if c.status == ""]

    # test= claims, in one batch: collect first so a bad nodeid refuses alone.
    tests = [c for c in live if c.kind == "test"]
    if tests:
        wanted_files = sorted({c.value.partition("::")[0] for c in tests})
        missing = [f for f in wanted_files if not (ROOT / f).is_file()]
        present = [f for f in wanted_files if f not in missing]
        for c in tests:
            if c.value.partition("::")[0] in missing:
                c.refuse(f"{c.value.partition('::')[0]} does not exist -- "
                         "cannot tell whether this claim still holds")
        ids, err = (set(), None)
        if present:
            ids, err = collect(present, python)
        runnable: dict[Claim, list[str]] = {}
        for c in tests:
            if c.status:
                continue
            if err:
                c.refuse(err)
                continue
            matched = [i for i in ids
                       if i == c.value or i.startswith(c.value + "[")]
            if not matched:
                c.refuse(f"pytest does not collect {c.value}")
                continue
            runnable[c] = sorted(matched)
        results, rerr = run_tests(sorted({i for v in runnable.values() for i in v}),
                                  python)
        for c, nodes in runnable.items():
            if rerr:
                c.refuse(rerr)
                continue
            got = []
            for n in nodes:
                cls, name = junit_key(n)
                got.append(results.get(f"{cls}::{name}"))
            if any(g is None for g in got):
                c.refuse(f"{c.value} did not appear in the junit report")
                continue
            if "failed" in got or "error" in got:
                outcome = "failed"
            elif "skipped" in got:
                outcome = "skipped" if len(set(got)) == 1 else "mixed"
            elif "xfailed" in got:
                outcome = "xfailed"
            else:
                outcome = "passed"
            expect = c.attrs.get("expect", "pass")
            verdict = VERDICT[outcome][0 if expect == "pass" else 1]
            n_desc = c.value if len(nodes) == 1 else f"{c.value} ({len(nodes)} cases)"
            if verdict is OK:
                c.ok(f"{n_desc} {outcome}, as claimed")
            elif verdict is STALE:
                c.stale(f"{n_desc} {outcome} but the claim expects it to "
                        f"{expect}")
            else:
                c.refuse(f"{n_desc} {outcome} -- a test that did not run "
                         "backs nothing")

    for c in live:
        if c.status:
            continue
        if c.kind in ("grep", "absent"):
            check_grep(c)
        elif c.kind == "commit":
            check_commit(c)

    # covers= is a modifier on every kind, and can only downgrade.
    for c in live:
        if c.status == OK:
            check_covers(c)
    return claims


# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", default=None,
                    help="documents to check (default: docs/*.md)")
    ap.add_argument("--python", default=sys.executable,
                    help="interpreter used to run backing tests")
    ap.add_argument("--quiet", action="store_true",
                    help="print only claims that are not OK, plus the summary")
    a = ap.parse_args()

    if a.paths:
        docs = [pathlib.Path(p) if pathlib.Path(p).is_absolute() else ROOT / p
                for p in a.paths]
        absent = [d for d in docs if not d.is_file()]
        if absent:
            for d in absent:
                print(f"REFUSED  {d}: no such file", file=sys.stderr)
            return 2
    else:
        docs = sorted((ROOT / "docs").glob("*.md"))

    claims = check(docs, a.python)
    if not claims:
        print("check_doc_claims: REFUSED -- no claim markers found in "
              f"{len(docs)} document(s); see docs/claim-markers.md", file=sys.stderr)
        return 2

    width = max(len(c.where) for c in claims)
    for c in claims:
        if a.quiet and c.status == OK:
            continue
        print(f"{c.status:<8}{c.where:<{width}}  {c.detail}")
        if c.status != OK and c.prose:
            print(f"{'':<8}{'':<{width}}  claim: {c.prose}")

    n = {s: sum(1 for c in claims if c.status == s) for s in (OK, STALE, REFUSED)}
    print(f"\n{len(claims)} claim(s) in {len(docs)} document(s): "
          f"{n[OK]} ok, {n[STALE]} stale, {n[REFUSED]} refused")
    if n[STALE]:
        return 1
    if n[REFUSED]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
