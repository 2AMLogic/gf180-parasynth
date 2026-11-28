#!/usr/bin/env python3
"""Does each cymbal-369 result record name a source tree a reader can check out? (#1940)

    python tools/check_cymbal_source_provenance.py             # the gate
    python tools/check_cymbal_source_provenance.py --scan      # a census, not a gate

A result record's `commit` with `sources_dirty: false` is read as "these numbers
came from exactly that tree". The four cymbal-369 records that carry
`controls_pass` all say so, and on 2026-11-28 three of the four cited a commit
that is on no ref of this repository: `low-tail.json` (e3c565ce),
`m-origin.json` (2d4abdb1) and `vca-clip.json` (ac9bf865). The fourth,
`tone-knob.json` (1916d361), resolved, and is the positive control. No test had
resolved those three: the one that resolves a record's commit covers
`tone-render.json` only, and the suite-wide dirty-tree test says in its own
docstring why it stops short of reachability.

WHAT THE GATE ASKS, per record under docs/scorecard/cymbal-369/ that carries a
top-level `controls_pass`:  is `<commit>` an ancestor of the canonical main
ref (origin/main)?

    RESOLVED            yes. The source tree is recoverable from main's history.
    HISTORICAL-UNKNOWN  no, and the record is one of the three named in
                        HISTORICAL_UNKNOWN below, carrying a matching
                        `source_provenance` annotation. ACCEPTED, and reported
                        as an unknown: this verdict never says the source was
                        found. It says the lookup failed and the record says so.
    UNRESOLVED          no, and nothing accounts for it. FAIL. This is the state
                        the three records were in before the annotation, and the
                        state of any new record whose commit never reached main.
    DEFECT              the record contradicts itself: dirty sources, a malformed
                        SHA, an annotation that claims a status this tool does
                        not recognise, an annotation on a record whose commit
                        resolves, or one that disagrees with the lookup. FAIL.
    UNAVAILABLE         the question cannot be answered from this checkout: no
                        main ref, or a shallow clone. REFUSED, exit 2.

WHY A SHALLOW CLONE REFUSES INSTEAD OF FAILING. `git merge-base --is-ancestor`
walks parents. In a shallow clone the walk stops at the boundary, so a commit
that IS on main can come back "not an ancestor" (a true ancestor fetched as its
own shallow tip does exactly that; tools/test_check_cymbal_source_provenance.py
reproduces it). A positive is sound at any depth; every other answer is
unavailable, never a failure.

WHY THE EXCEPTIONS LIVE HERE AND NOT IN THE RECORDS. An annotation is data, and
data can be copied. A new record with a new unknown SHA and the annotation block
pasted in must not pass, so the recognised exceptions are keyed by path AND full
SHA in this file, and a record is HISTORICAL-UNKNOWN only when its (path, sha)
is in that table and its annotation agrees with it. Pasted elsewhere, the same
block is UNRESOLVED.

WHAT THE LOOKUP IS BASED ON. This clone, only. No network: the gate has to run
in an ordinary test. So UNRESOLVED means "not reachable from <main ref>@<sha>
in this clone, which is not shallow". A clone that has not been fetched can be
behind a commit that has since merged; the basis is printed with every verdict,
and `git fetch` and a re-run settles a doubt. The three historical unknowns
were also looked up through the GitHub commits API on 2026-11-28 (422 for each,
200 for the control); that is what their annotations record.

WHY THEY ARE UNKNOWN IS NOT ESTABLISHED. All three records landed through
two-parent merge commits (#418, #423) whose branch commits are on main -- the
same landing put the control's `1916d361` there -- so a squash merge did not
discard these commits. They were never part of a pushed branch as pushed.
Whether each was amended, rebased away, or made in a worktree that was never
pushed is not recorded anywhere in this repository.

FOR NEW RECORDS IN THIS FAMILY. No annotation: measure on a clean tree at a
commit that is already on main, and let the plain `commit` field resolve. Run
this check on the record before publishing it, from a checkout with full
history and a main ref.

--scan is the census the issue asked for at closing: every JSON under
docs/scorecard that states a source commit (a top-level `commit`, or
`provenance.commit`), resolved the same way and reported without judging. It
exists so the search for the same assumption elsewhere is reproducible rather
than a claim. Exit 0 means the census ran; 2 means it could not.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import pathlib
import subprocess
import sys

import exit_contract as ec
import refusal

ROOT = pathlib.Path(__file__).resolve().parents[1]
FAMILY = "docs/scorecard/cymbal-369"
SCAN_ROOT = "docs/scorecard"
DEFAULT_MAIN_REF = "origin/main"
ISSUE = 1940
RECORDED = "2026-11-28"
ANNOTATION = "source_provenance"

#: The recognised historical unknowns, keyed by path under FAMILY and carrying
#: the full SHA, so an annotation copied to another record does not match.
HISTORICAL_UNKNOWN = {
    "low-tail/low-tail.json": {
        "commit": "e3c565ce138320c667f11092884b0cd5727eefa0",
        "landed_by": "1c8a941a", "pull": 418,
    },
    "low-tail/m-origin.json": {
        "commit": "2d4abdb11d92414fd8f702b453161d1e0eb2ae41",
        "landed_by": "1c8a941a", "pull": 418,
    },
    "vca-clip/vca-clip.json": {
        "commit": "ac9bf86526b1ebb64b632d8a2e024d0c265590ee",
        "landed_by": "cfc38fdd", "pull": 423,
    },
}

RESOLVED = "RESOLVED"
HISTORICAL = "HISTORICAL-UNKNOWN"
UNRESOLVED = "UNRESOLVED"
DEFECT = "DEFECT"
UNAVAILABLE = "UNAVAILABLE"

HEX40 = frozenset("0123456789abcdef")

LIMIT = ("NOTE: a resolvable commit says the source tree can be checked out. It does not "
         "say the measurement was right. A HISTORICAL-UNKNOWN is accepted and is NOT a "
         "verified source; its tree cannot be recovered from main's history.")
OUTCOME = {ec.PASS: "PASS: every claim resolves or is a recorded historical unknown",
           ec.FAIL: "FAIL: a claim is unresolved or self-contradictory (see above)",
           ec.REFUSED: "REFUSED: this checkout cannot answer; no verdict was reached"}


class Refused(refusal.Refused, Exception):
    """A precondition of the lookup failed; nothing was judged."""


def _git(repo, *args):
    """(rc, stdout, stderr) of `git -C repo ...`. GIT_DIR and GIT_WORK_TREE are
    dropped from the environment: either one overrides `-C` and would silently
    point the lookup at a repository other than the one named."""
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                       text=True, env=env, timeout=120)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def is_sha40(sha) -> bool:
    return isinstance(sha, str) and len(sha) == 40 and set(sha) <= HEX40


def repo_state(repo, main_ref) -> dict:
    """What this checkout can answer: `git` (is a repository), `shallow`, and
    `main`, the full SHA of the main ref or None when it is absent."""
    rc, shallow, err = _git(repo, "rev-parse", "--is-shallow-repository")
    if rc != 0:
        return {"git": False, "shallow": None, "main": None,
                "why": f"not a git repository ({err or f'git exit {rc}'})"}
    rc, head, _ = _git(repo, "rev-parse", "--verify", "--quiet", f"{main_ref}^{{commit}}")
    main = head if rc == 0 and is_sha40(head) else None
    return {"git": True, "shallow": shallow == "true", "main": main,
            "why": None if main else f"no {main_ref} in this clone"}


def lookup(repo, sha, main_ref, state) -> str:
    """Where `<sha>^{commit}` stands relative to the main ref: `ancestor`,
    `outside-main`, `absent`, `shallow` (not reachable, and the history is cut
    so that proves nothing) or `no-main`."""
    if state["main"] is None:
        return "no-main"
    rc, _, err = _git(repo, "merge-base", "--is-ancestor", f"{sha}^{{commit}}", main_ref)
    if rc == 0:
        return "ancestor"                      # sound at any depth
    if rc not in (1, 128):
        raise Refused(f"git merge-base failed on {sha[:8]} against {main_ref}: {err or rc}")
    if state["shallow"]:
        return "shallow"                       # the walk stopped at the boundary
    present, _, _ = _git(repo, "cat-file", "-e", f"{sha}^{{commit}}")
    return "outside-main" if present == 0 else "absent"


def _json_object(path: pathlib.Path):
    try:
        d = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def scoped_records(repo, family) -> list:
    """(path, record) for every JSON under `family` carrying a top-level
    `controls_pass`. REFUSES on an unreadable file or an empty population: both
    would otherwise read as 'nothing wrong here'."""
    base = pathlib.Path(repo) / family
    out = []
    for p in (sorted(base.rglob("*.json")) if base.is_dir() else []):
        rel = p.relative_to(repo).as_posix()
        try:
            d = json.loads(p.read_text())
        except (OSError, ValueError) as exc:
            raise Refused(f"{rel}: not readable as JSON ({exc})") from exc
        if isinstance(d, dict) and "controls_pass" in d:
            out.append((rel, d))
    if not out:
        raise Refused(f"no JSON with a top-level controls_pass under {family}/")
    return out


def annotation_defect(record, entry) -> str | None:
    """Why the record's annotation does not stand for the table entry, or None.
    An annotation may state a failed lookup with its date; nothing else."""
    a = record.get(ANNOTATION)
    if not isinstance(a, dict):
        return f"{ANNOTATION} is not an object"
    want = {"status": "unresolved", "recorded": RECORDED, "issue": ISSUE,
            "recorded_commit": entry["commit"]}
    for k, v in want.items():
        if a.get(k) != v:
            return f"{ANNOTATION}.{k} is {a.get(k)!r}; a historical unknown here must say {v!r}"
    lk = a.get("lookup")
    if not isinstance(lk, dict) or lk.get("date") != RECORDED or lk.get("is_ancestor") is not False:
        return (f"{ANNOTATION}.lookup must record date {RECORDED} and is_ancestor false; "
                "an annotation may state a failed lookup, never a verified source")
    return None


def judge(repo, path, record, main_ref, state) -> dict:
    """One scoped record -> {path, sha, verdict, detail}."""
    sha = record.get("commit")
    if not is_sha40(sha):
        return {"path": path, "sha": sha, "verdict": DEFECT,
                "detail": "commit is not a full 40-hex SHA"}
    if record.get("sources_dirty") is not False:
        return {"path": path, "sha": sha, "verdict": DEFECT,
                "detail": "sources_dirty is not false: the tree is not the commit's"}
    where = lookup(repo, sha, main_ref, state)
    entry = HISTORICAL_UNKNOWN.get(path[len(FAMILY) + 1:]) if path.startswith(FAMILY + "/") else None
    if where == "ancestor":
        a = record.get(ANNOTATION)
        if isinstance(a, dict) and a.get("status") == "unresolved":
            return {"path": path, "sha": sha, "verdict": DEFECT,
                    "detail": f"annotated unresolved, but {sha[:8]} is an ancestor of {main_ref}: update the record"}
        return {"path": path, "sha": sha, "verdict": RESOLVED,
                "detail": f"ancestor of {main_ref}"}
    if where == "no-main":
        return {"path": path, "sha": sha, "verdict": UNAVAILABLE,
                "detail": f"{state['why']}: cannot say whether {sha[:8]} is on main"}
    if where == "shallow":
        return {"path": path, "sha": sha, "verdict": UNAVAILABLE,
                "detail": (f"shallow clone: {sha[:8]} not reachable within the fetched history, "
                           "which does not show it is off main (need full history)")}
    how = ("on a ref of this clone but not on " + main_ref) if where == "outside-main" \
        else "in no ref of this clone"
    if entry is None or entry["commit"] != sha:
        note = (f"; its {ANNOTATION} annotation is not a recognised historical exception"
                if ANNOTATION in record else "")
        return {"path": path, "sha": sha, "verdict": UNRESOLVED, "detail": how + note}
    if ANNOTATION not in record:
        return {"path": path, "sha": sha, "verdict": UNRESOLVED,
                "detail": f"{how}, and no {ANNOTATION} annotation accounts for it"}
    defect = annotation_defect(record, entry)
    if defect:
        return {"path": path, "sha": sha, "verdict": DEFECT, "detail": defect}
    return {"path": path, "sha": sha, "verdict": HISTORICAL,
            "detail": f"{how}; annotated #{ISSUE}, recorded {RECORDED} -- an unknown, not a verified source"}


def basis(state, main_ref) -> str:
    if not state["git"]:
        return state["why"]
    at = f"@{state['main']}" if state["main"] else " ABSENT"
    depth = "" if state["shallow"] is None else (" shallow" if state["shallow"] else " full history")
    return f"{main_ref}{at}{depth}"


class Report:
    def __init__(self, rows, state, main_ref):
        self.rows, self.state, self.main_ref = rows, state, main_ref

    def counts(self) -> collections.Counter:
        return collections.Counter(r["verdict"] for r in self.rows)

    @property
    def exit_code(self) -> int:
        c = self.counts()
        if c[UNRESOLVED] or c[DEFECT]:
            return ec.FAIL
        return ec.REFUSED if c[UNAVAILABLE] else ec.PASS

    def lines(self) -> list:
        c = self.counts()
        head = (f"{c[HISTORICAL]} historical unknowns (annotated, never verified), "
                f"{c[RESOLVED]} resolvable, {c[UNRESOLVED]} unresolved, "
                f"{c[DEFECT]} defect, {c[UNAVAILABLE]} unavailable")
        return [head] + [f"  {r['verdict']:<19} {r['path']}  {r['sha']}  {r['detail']}"
                         for r in self.rows]


def evaluate(repo, family, main_ref) -> Report:
    state = repo_state(repo, main_ref)
    rows = [judge(repo, path, rec, main_ref, state) for path, rec in scoped_records(repo, family)]
    return Report(rows, state, main_ref)


def census(repo, scan_root, main_ref) -> tuple:
    """Every JSON under `scan_root` that states a source commit, resolved the
    same way. Returns (rows, state); each row is (path, where, sha, dirty,
    has_controls_pass, answer). Judges nothing."""
    state = repo_state(repo, main_ref)
    rows = []
    base = pathlib.Path(repo) / scan_root
    for p in (sorted(base.rglob("*.json")) if base.is_dir() else []):
        d = _json_object(p)
        if d is None:
            continue
        prov = d.get("provenance")
        claims = [("top", d.get("commit"), d.get("sources_dirty"))] if "commit" in d else []
        if isinstance(prov, dict) and "commit" in prov:
            claims.append(("provenance", prov.get("commit"), prov.get("sources_dirty")))
        for where, sha, dirty in claims:
            if not is_sha40(sha):
                answer = "short-sha"
            elif state["main"] is None:
                answer = "no-main"
            else:
                answer = lookup(repo, sha, main_ref, state)
            rows.append((p.relative_to(repo).as_posix(), where, sha, dirty, "controls_pass" in d, answer))
    return rows, state


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", default=str(ROOT))
    ap.add_argument("--family", default=FAMILY)
    ap.add_argument("--main-ref", default=DEFAULT_MAIN_REF,
                    help="the canonical main ref (default origin/main)")
    ap.add_argument("--scan", action="store_true",
                    help="census every source-commit claim under --scan-root; not a gate")
    ap.add_argument("--scan-root", default=SCAN_ROOT)
    a = ap.parse_args(argv)
    try:
        if a.scan:
            rows, state = census(a.repo, a.scan_root, a.main_ref)
            return _print_census(rows, state, a.scan_root, a.main_ref)
        rep = evaluate(a.repo, a.family, a.main_ref)
    except Refused as exc:
        print(f"REFUSED: {exc}")
        return ec.REFUSED
    print(f"source provenance of {a.family}/ records carrying controls_pass, against {basis(rep.state, a.main_ref)}")
    for line in rep.lines():
        print(line)
    print(LIMIT)
    print(OUTCOME[rep.exit_code])
    return rep.exit_code


def _print_census(rows, state, scan_root, main_ref) -> int:
    print(f"census of source-commit claims under {scan_root}/ against {basis(state, main_ref)} "
          "-- NOT A GATE: it reports, it does not judge")
    for path, where, sha, dirty, ctrl, ans in rows:
        print(f"  {ans:<12} {where:<9} controls_pass={str(ctrl):<5} dirty={str(dirty):<5} "
              f"{str(sha):<40} {path}")
    by = collections.Counter(r[-1] for r in rows)
    print(f"  {len(rows)} claims in {len({r[0] for r in rows})} files: "
          + ", ".join(f"{n} {k}" for k, n in sorted(by.items())))
    off = [r for r in rows if r[-1] in ("outside-main", "absent")]
    fam = collections.Counter(_under(r[0], scan_root).split("/")[0] for r in off)
    print(f"  not on {main_ref} (outside-main or absent): {len(off)} in {len(fam)} directories under {scan_root}/: "
          + ", ".join(f"{k} {v}" for k, v in sorted(fam.items())))
    return ec.PASS if state["main"] is not None and not state["shallow"] else ec.REFUSED


def _under(path, root):
    return path[len(root):].lstrip("/") if path.startswith(root) else path


if __name__ == "__main__":
    sys.exit(main())
