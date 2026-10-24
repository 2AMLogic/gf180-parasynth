#!/usr/bin/env python3
"""Run the two corpus probes at two SHAs and tabulate what moved (#700).

#690 routed `measure_partial_balance.py` and `measure_conga_body_spread.py`
through `run_case.prepare`. This wrapper runs the EXISTING CLIs, unmodified, in
a throwaway worktree per SHA and records each job's own exit status. It adds no
measurement logic: re-implementing the probes would not test what ships.

Outcomes, kept distinct (a tool that answers when it cannot looks like data):

  0  both arms ran, controls held, verdicts recorded
  1  a job failed (exit status other than 0/2) -- a RESULT, reported
  2  REFUSED  corpus absent, arms ran on different corpora, or the BEFORE arm
              did not reproduce the historical headline (the apparatus is not
              known to work, so no delta is reported)

Heavy and corpus-dependent: run on the build box, not a loom worker.
"""
from __future__ import annotations
import argparse, hashlib, json, os, pathlib, re, subprocess, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_all as ra                                             # noqa: E402
import run_case as rc                                            # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
BEFORE_DEFAULT, AFTER_DEFAULT = "6fbd1bd1^1", "6fbd1bd1"    # #690 merge parents
HEADLINE = "6.07"      # docs/conga-body-spectrum-spread.md historical headline

REFUSED, FAILED, OK = "REFUSED", "FAILED", "OK"


class Refused(Exception):
    pass


def job_cmds(py: str, refs: pathlib.Path) -> dict[str, str]:
    r = str(refs)
    return {
        "partial_balance validate": f"{py} tools/measure_partial_balance.py validate",
        "partial_balance measure": f"{py} tools/measure_partial_balance.py measure",
        "conga_spread --check": f"{py} tools/measure_conga_body_spread.py --refs {r} --check",
        "conga_spread measure": f"{py} tools/measure_conga_body_spread.py --refs {r}",
    }


def corpus_manifest(refs: pathlib.Path) -> str:
    """Hash of (relative path, size, content hash) for every file: arms must
    see the identical corpus or a delta is not attributable."""
    if not refs.is_dir():
        raise Refused(f"no reference corpus at {refs}")
    h = hashlib.sha256()
    files = sorted(p for p in refs.rglob("*") if p.is_file())
    if not files:
        raise Refused(f"reference corpus at {refs} is empty")
    for p in files:
        h.update(str(p.relative_to(refs)).encode())
        h.update(hashlib.sha256(p.read_bytes()).digest())
    return "sha256:" + h.hexdigest()[:16]


def classify(result: dict) -> str:
    """Map run_all's state to this tool's vocabulary; never read the text."""
    if result["state"] == ra.PASS:
        return OK
    if result["state"] in (ra.NO_VERDICT, ra.LAUNCH_ERROR) or result["rc"] == 2:
        return REFUSED          # did not run / timed out: no evidence
    return FAILED


def before_control(results: dict[str, dict]) -> None:
    """The before arm is a known answer: it must reproduce the historical
    headline, or the apparatus is not shown to work and no delta may be
    reported. Raises Refused. Only OK jobs count (a REFUSED job's output proves
    nothing)."""
    for name, r in results.items():
        if classify(r) == OK and re.search(rf"(?<![\d.]){re.escape(HEADLINE)}(?!\d)", r["out"]):
            return
    raise Refused(f"before arm did not reproduce the historical {HEADLINE} dB "
                  f"headline in any job that ran; the apparatus is unverified")


def git(*a: str) -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), *a], text=True).strip()


def run_arm(sha: str, refs: pathlib.Path, timeout: float | None, py: str) -> dict:
    full = git("rev-parse", "--verify", sha + "^{commit}")
    with tempfile.TemporaryDirectory(prefix="prep-corpus-") as td:
        wt = pathlib.Path(td) / "wt"
        git("worktree", "add", "--detach", str(wt), full)
        try:
            env = dict(os.environ, TR808_REFS=str(refs), GF180_TR808_REFS=str(refs))
            res = {n: ra.run_one(c, timeout, env=env, cwd=str(wt))
                   for n, c in job_cmds(py, refs).items()}
        finally:
            git("worktree", "remove", "--force", str(wt))
    return dict(sha=full, jobs=res)


def table(before: dict, after: dict) -> str:
    lines = [f"{'job':28s} {'before':>8s} {'after':>8s}  rc(b/a)"]
    for n in before["jobs"]:
        b, a = before["jobs"][n], after["jobs"][n]
        lines.append(f"{n:28s} {classify(b):>8s} {classify(a):>8s}  {b['rc']}/{a['rc']}"
                     + ("" if b["out"] != a["out"] else "   output identical (did not move)"))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--before", default=BEFORE_DEFAULT)
    ap.add_argument("--after", default=AFTER_DEFAULT)
    ap.add_argument("--refs", type=pathlib.Path,
                    default=rc.configured_refs())
    ap.add_argument("--timeout", type=float, default=3600)
    ap.add_argument("--json", type=pathlib.Path)
    ap.add_argument("--python", default=sys.executable)
    a = ap.parse_args(argv)
    try:
        manifest = corpus_manifest(a.refs)
        before = run_arm(a.before, a.refs, a.timeout, a.python)
        after = run_arm(a.after, a.refs, a.timeout, a.python)
        if corpus_manifest(a.refs) != manifest:
            raise Refused("corpus changed between arms")
        before_control(before["jobs"])
    except Refused as e:
        print(f"REFUSED  {e}", file=sys.stderr)
        return 2
    print(f"corpus {a.refs} {manifest}\nbefore {before['sha']}\nafter  {after['sha']}\n")
    print(table(before, after))
    if a.json:
        a.json.write_text(json.dumps(dict(corpus=str(a.refs), manifest=manifest,
                                          before=before, after=after), indent=2))
    states = [classify(r) for arm in (before, after) for r in arm["jobs"].values()]
    return 1 if FAILED in states else (2 if REFUSED in states else 0)


if __name__ == "__main__":
    sys.exit(main())
