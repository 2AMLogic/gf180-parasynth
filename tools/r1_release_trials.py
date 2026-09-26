#!/usr/bin/env python3
"""Produce the R1 release trials' receipts (issue #278, Milestone A steps 7-9)
on real checkouts, and say whether each one came out as expected.

    python tools/r1_release_trials.py landed  --tree ~/work/r1-main  --venv ~/work/trials-venv --out DIR
    python tools/r1_release_trials.py release --tree ~/work/r1-branch --venv ~/work/trials-venv --out DIR

Build box only (CLAUDE.md): T-PLAY-DIGITAL simulates for ~45 minutes.

`--tree` must be a real clone (not a worktree: provenance needs a real .git)
with the pinned toolchain under build/oss-cad-pinned. Every trial runs with
THAT tree's tools/trial.py, so a receipt names the tree it judged.

landed    the landed #274+#255 code, as main has it -- nothing of this branch:
            T-PLAY-DIGITAL sim            baseline + its silent-image control
            T-PLAY-DIGITAL sim, candidate held-legacy-silent   -> FAIL
            T-DEADLINE reanalyse, sim     baseline + late160 control
            T-DEADLINE sim, candidate late160                  -> FAIL
            T-RELEASE-BOUND               NO VERDICT: main declares no control
release   this branch (the stale controls exist here):
            T-RELEASE-BOUND               baseline + both stale controls -> PASS
            candidate stale-manifest, candidate stale-binding  -> FAIL
          then, in an ISOLATED clone of the same commit (the real tree is
          never edited), evidence taken away:
            manifest deleted              -> NO VERDICT (preflight)
            manifest unreadable           -> NO VERDICT (checker REFUSED)
            bound binding record deleted  -> NO VERDICT (checker REFUSED)

Writes <out>/summary.json and exits 1 if any receipt is invalid or any
verdict is not the expected one. The summary reports; the receipts are the
evidence (`python tools/trial.py check-all <out>`).
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import tempfile

LANDED = {
    "play-sim": (["T-PLAY-DIGITAL", "--mode", "sim"], "PASS"),
    "play-cand-legacy": (["T-PLAY-DIGITAL", "--mode", "sim", "--as-candidate",
                          "held-legacy-silent"], "FAIL"),
    "deadline-reanalyse": (["T-DEADLINE", "--mode", "reanalyse"], "PASS"),
    "deadline-sim": (["T-DEADLINE", "--mode", "sim"], "PASS"),
    "deadline-cand-late160": (["T-DEADLINE", "--mode", "sim", "--as-candidate", "late160"], "FAIL"),
    "release-before": (["T-RELEASE-BOUND"], "NO VERDICT"),
}
RELEASE = {
    "release": (["T-RELEASE-BOUND"], "PASS"),
    "release-cand-stale-manifest": (["T-RELEASE-BOUND", "--as-candidate", "stale-manifest"], "FAIL"),
    "release-cand-stale-binding": (["T-RELEASE-BOUND", "--as-candidate", "stale-binding"], "FAIL"),
}
MANIFEST = "fpga/release/baseline-2025.1.json"
BOUND_RECORD = "fpga/reports/arty/drift-clean/verification.json"


def _truncate(p: pathlib.Path) -> None:
    data = p.read_bytes()
    p.write_bytes(data[: len(data) // 2])


# name -> (what is done to the isolated clone, expected verdict, expected execution)
FIXTURES = {
    "manifest-missing": (lambda t: (t / MANIFEST).unlink(), "NO VERDICT", "preflight-refused"),
    "manifest-unreadable": (lambda t: _truncate(t / MANIFEST), "NO VERDICT", "complete"),
    "binding-record-missing": (lambda t: (t / BOUND_RECORD).unlink(), "NO VERDICT", "complete"),
}


def git(tree: pathlib.Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(tree), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


def receipt_from(text: str) -> str | None:
    for line in reversed(text.splitlines()):
        if line.startswith("receipt: "):
            return line[len("receipt: "):].strip()
    return None


def check(tree: pathlib.Path, py: str, receipt: str | None) -> dict:
    """Check a receipt with the judged tree's own checker (not this one's)."""
    if not receipt:
        return {"valid": False, "verdict": None, "problems": ["no receipt was written"]}
    r = subprocess.run([py, str(tree / "tools/trial.py"), "check-receipt", receipt],
                       cwd=tree, capture_output=True, text=True)
    rec = json.loads(pathlib.Path(receipt).read_text())
    return {"valid": r.returncode == 0, "check": r.stdout.strip().splitlines()[-3:],
            "verdict": rec["verdict"], "execution": rec["execution"]["status"],
            "reasons": rec["verdict_reasons"][:4],
            "controls": {c["id"]: c.get("caught") for c in rec["controls"]},
            "head": rec["identities"]["source"]["head"],
            "dirty": rec["identities"]["source"].get("dirty"),
            "origin_main": rec["identities"]["integration"]["origin_main"]}


def run_batch(tree: pathlib.Path, py: str, out: pathlib.Path, jobs: dict) -> dict:
    """Start every job at once (they are independent), then wait for all."""
    logs = out / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    procs = {}
    for name, (args, _) in jobs.items():
        fh = open(logs / f"{name}.log", "w")
        procs[name] = (subprocess.Popen([py, str(tree / "tools/trial.py"), "run", *args,
                                         "--out", str(out)], cwd=tree, stdout=fh,
                                        stderr=subprocess.STDOUT), fh)
    got = {}
    for name, (p, fh) in procs.items():
        rc = p.wait()
        fh.close()
        text = (logs / f"{name}.log").read_text()
        got[name] = dict(check(tree, py, receipt_from(text)), rc=rc, expected=jobs[name][1])
    return got


def isolated_fixtures(tree: pathlib.Path, py: str, out: pathlib.Path) -> dict:
    """Take evidence away in a throwaway clone of the same commit, one fixture
    at a time, restoring from git between them. The real tree is not touched."""
    head = git(tree, "rev-parse", "HEAD")
    got = {}
    with tempfile.TemporaryDirectory(prefix="r1-fixture-") as d:
        iso = pathlib.Path(d) / "tree"
        subprocess.run(["git", "clone", "-q", str(tree), str(iso)], check=True)
        git(iso, "checkout", "-q", head)
        # the clone's origin/main is the judged tree's own origin/main
        main = git(tree, "rev-parse", "origin/main")
        git(iso, "update-ref", "refs/remotes/origin/main", main)
        for name, (damage, want, want_exec) in FIXTURES.items():
            damage(iso)
            dirty = git(iso, "status", "--porcelain")
            r = subprocess.run([py, str(iso / "tools/trial.py"), "run", "T-RELEASE-BOUND",
                                "--out", str(out / "fixtures" / name)], cwd=iso,
                               capture_output=True, text=True)
            (out / "logs").mkdir(parents=True, exist_ok=True)
            (out / "logs" / f"fixture-{name}.log").write_text(r.stdout + r.stderr)
            got[name] = dict(check(iso, py, receipt_from(r.stdout)), rc=r.returncode,
                             expected=want, expected_execution=want_exec, damage=dirty)
            git(iso, "checkout", "-q", "--", ".")
            if git(iso, "status", "--porcelain"):
                raise RuntimeError(f"the isolated clone did not restore after {name}")
    return got


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("phase", choices=("landed", "release"))
    ap.add_argument("--tree", type=pathlib.Path, required=True)
    ap.add_argument("--venv", type=pathlib.Path, required=True)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    a = ap.parse_args(argv)
    tree = a.tree.expanduser().resolve()
    py = str(a.venv.expanduser() / "bin" / "python")
    out = a.out.expanduser().resolve()
    if git(tree, "status", "--porcelain", "--untracked-files=no"):
        print(f"REFUSED: {tree} has uncommitted changes; a receipt must name a commit")
        return 2
    out.mkdir(parents=True, exist_ok=True)
    summary = {"phase": a.phase, "tree": str(tree), "head": git(tree, "rev-parse", "HEAD"),
               "origin_main": git(tree, "rev-parse", "origin/main")}
    summary["runs"] = run_batch(tree, py, out, LANDED if a.phase == "landed" else RELEASE)
    if a.phase == "release":
        summary["fixtures"] = isolated_fixtures(tree, py, out)
    bad = []
    for group in ("runs", "fixtures"):
        for name, r in summary.get(group, {}).items():
            if not r["valid"]:
                bad.append(f"{name}: receipt invalid {r.get('problems') or r.get('check')}")
            if r["verdict"] != r["expected"]:
                bad.append(f"{name}: {r['verdict']}, expected {r['expected']}")
            if "expected_execution" in r and r.get("execution") != r["expected_execution"]:
                bad.append(f"{name}: execution {r.get('execution')}, "
                           f"expected {r['expected_execution']}")
    summary["unexpected"] = bad
    (out / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    print(json.dumps(summary, indent=1))
    print("ALL AS EXPECTED" if not bad else f"UNEXPECTED ({len(bad)}): " + "; ".join(bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
