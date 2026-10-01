#!/usr/bin/env python3
"""Compile the capability DAG from evidence, and render it into README.md.

A node is not green because someone wrote that it is. Every node in
docs/dag.json names the evidence that would make it green, and this script
checks that evidence exists and is not older than the files it covers.

This exists because docs/capability-dag.md twice carried findings that had
already been fixed -- the modal_dp exc hazard, and red on four repaired drum
circuits. Both were hand-maintained claims that outlived their evidence.
See docs/failure-modes.md, mechanism 4.

Status is derived, never asserted:

  STAMPED   the evidence PASSED, and an annotated tag exists whose commit is
            an ancestor of HEAD
  GREEN     the evidence RAN AND PASSED at a commit in this history
  STALE     the evidence passed, but a file the node `covers` has changed
            since -- the result no longer describes this code
  RED       the evidence ran and FAILED
  BLOCKED   the node declares what it is waiting for
  TODO      no evidence yet; an issue number if one is filed

A GREEN/STAMPED node above a RED or BLOCKED dependency is reported BLOCKED
(see classify_all).

Run with --check to fail when anything is STALE, which is the state that
matters: a green claim whose evidence has gone out from under it.
"""
from __future__ import annotations
import argparse, json, os, pathlib, re, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# The checkout-staleness banner is IMPORTED, not reimplemented (#155). It lives
# in tools/scorecard.py because that is the report it was written for (#98), and
# ten tools in this directory already import that module, so there is no "leaf"
# convention here for this import to violate. NB this is a different question
# from `stale_against()` below: that one asks whether a node's EVIDENCE is older
# than the files it covers; this one asks whether the whole TREE is older than
# the branch the reader will assume it describes.
sys.path.insert(0, str(ROOT / "tools"))
import scorecard as sc                                              # noqa: E402

DAG = ROOT / "docs" / "dag.json"
README = ROOT / "README.md"
RESULTS = ROOT / "docs" / "dag-results.json"
BEGIN, END = "<!-- DAG:BEGIN -->", "<!-- DAG:END -->"

GROUPS = {"foundation": "Foundation", "minimoog": "Minimoog voice",
          "drums": "TR-808 drums", "integration": "Integration", "silicon": "Silicon"}

# Only subsystems that MAKE A SOUND can be compared to an external reference.
# A control link or a routed die has no Minimoog to be measured against, so
# demanding fidelity evidence there would be noise that trains people to ignore
# the warning -- which is how a real UNVALIDATED goes unnoticed.
NEEDS_FIDELITY = {"minimoog", "drums"}


def git(*a: str) -> str:
    try:
        return subprocess.run(["git", *a], cwd=ROOT, capture_output=True,
                              text=True, timeout=30).stdout.strip()
    except Exception:
        return ""


def tag_is_ancestor(tag: str) -> bool:
    """A tag on a commit no longer in this history is not evidence."""
    if not tag or tag not in git("tag", "-l").split():
        return False
    sha = git("rev-list", "-n1", tag)
    if not sha:
        return False
    r = subprocess.run(["git", "merge-base", "--is-ancestor", sha, "HEAD"],
                       cwd=ROOT, capture_output=True)
    return r.returncode == 0


def run_evidence(nid: str, n: dict) -> tuple[bool, str]:
    """Actually execute this node's evidence. Returns (passed, detail).

    This is the difference between a status and a claim. An earlier version of
    this script called a node GREEN when its test FILE EXISTED -- so a suite
    with every assertion deleted would have rendered green, which is precisely
    the failure this tool was written to prevent.
    """
    if "suite" in n:
        r = subprocess.run([sys.executable, "-m", "pytest", n["suite"], "-q",
                            "--no-header", "-x"], cwd=ROOT, capture_output=True,
                           text=True, timeout=1800)
        last = [l for l in r.stdout.strip().splitlines() if l.strip()]
        return r.returncode == 0, (last[-1][:90] if last else "no output")
    if "verifier" in n:
        r = subprocess.run([sys.executable, n["verifier"]], cwd=ROOT,
                           capture_output=True, text=True, timeout=7200)
        last = [l for l in r.stdout.strip().splitlines() if l.strip()]
        return r.returncode == 0, (last[-1][:90] if last else "no output")
    if "evidence_file" in n:
        # The docstring above describes exactly this bug -- and until now it was
        # fixed for `suite` and left intact HERE. Existence is a precondition,
        # not a verdict: a JSON saying `"passed": false` rendered the node GREEN
        # because the file was on disk. Fixing the instance and not the class is
        # the repeating failure this file was written to catch.
        fp = ROOT / n["evidence_file"]
        if not fp.exists():
            return False, f"{n['evidence_file']} missing"
        # A node may declare what makes its evidence a PASS. Text evidence uses
        # `evidence_requires` (a substring); JSON evidence may name a key.
        need = n.get("evidence_requires")
        try:
            body = fp.read_text()
        except Exception as exc:
            return False, f"{n['evidence_file']} unreadable: {type(exc).__name__}"
        if need and need not in body:
            return False, f"{n['evidence_file']} lacks {need!r}"
        # If it is JSON and carries a verdict, HONOUR IT. A falsy verdict is a
        # failure no matter how present the file is.
        if fp.suffix == ".json":
            try:
                doc = json.loads(body)
            except Exception as exc:
                return False, f"{n['evidence_file']} is not valid JSON: {type(exc).__name__}"
            for key in ("passed", "ok", "success", "green"):
                if isinstance(doc, dict) and key in doc:
                    good = bool(doc[key])
                    return good, f"{n['evidence_file']} {key}={doc[key]!r}"
        if need:
            return True, f"{n['evidence_file']} contains {need!r}"
        # Nothing declared and no verdict found. Say so, rather than implying
        # the file was read and approved.
        return True, f"{n['evidence_file']} EXISTS ONLY -- no verdict declared"
    if "tool" in n:
        r = subprocess.run([sys.executable, n["tool"]], cwd=ROOT,
                           capture_output=True, text=True, timeout=3600)
        return r.returncode == 0, f"{n['tool']} exit {r.returncode}"
    return False, "no evidence declared"


def load_results() -> dict:
    if RESULTS.exists():
        try:
            return json.loads(RESULTS.read_text())
        except Exception:
            return {}
    return {}


def stale_against(n: dict, at_sha: str) -> str | None:
    """Has anything this node covers changed since the evidence was recorded?"""
    if not at_sha:
        return None
    for f in n.get("covers", []):
        newer = git("log", "--format=%H", f"{at_sha}..HEAD", "--", f)
        if newer.strip():
            return f
    return None


def classify(nid: str, n: dict) -> tuple[str, str]:
    """Return (status, note). Derived from evidence; never taken on trust."""
    if n.get("blocked"):
        return "BLOCKED", n["blocked"]

    # What would make this node true?
    sources = [n[k] for k in ("verifier", "suite", "tool", "evidence_file") if k in n]
    if not sources:
        iss = n.get("issue")
        return "TODO", (f"issue #{iss}" if iss else "no evidence declared")

    missing = [s for s in sources if not (ROOT / s).exists()]
    if missing:
        return "TODO", "missing: " + ", ".join(missing)

    rec = load_results().get(nid)

    # A tag IS evidence: it was cut after a verified run, at a known commit.
    # So a slow node -- one whose verifier takes hours and which the per-push
    # job skips -- carries its tag's result forward, and `covers` staleness is
    # measured against the TAG's commit. That is what makes a stamp decay
    # honestly: the moment a file the node covers changes, the stamp goes
    # STALE rather than sitting there looking verified.
    tag = n.get("tag", "")
    if not rec and tag_is_ancestor(tag):
        tag_sha = git("rev-list", "-n1", tag)
        changed = stale_against(n, tag_sha)
        if changed:
            return "STALE", f"{changed} changed since {tag} was cut"
        return "STAMPED", f"{tag} (not re-run; verifier is slow)"

    if not rec:
        return "TODO", "never run -- `tools/compile_dag.py --run`"
    if not rec.get("passed"):
        return "RED", rec.get("detail", "failed")

    changed = stale_against(n, rec.get("sha", ""))
    if changed:
        return "STALE", f"{changed} changed since this was last run"

    if tag_is_ancestor(n.get("tag", "")):
        return "STAMPED", n["tag"]
    return "GREEN", rec.get("detail", "passed")


def classify_all(nodes: dict) -> dict:
    """Status for every node: own evidence first, then dependency propagation.

    classify() judges a node on its OWN evidence only. A node that is GREEN or
    STAMPED above a RED or BLOCKED prerequisite is a claim resting on a failing
    foundation, so it is reported BLOCKED ("blocked by <dep>"), transitively,
    in dependency order. Deliberate limits:

      * only GREEN/STAMPED are downgraded; a node's own RED/BLOCKED/TODO/STALE
        is never overwritten (it is already not a green claim);
      * only RED/BLOCKED deps propagate. TODO and STALE are not known-failing,
        and propagating them would turn most of the graph red on a stale stamp;
      * a cycle or an unknown dep is REFUSED (BLOCKED with the reason), never
        guessed past.
    """
    own: dict[str, tuple[str, str]] = {}
    out: dict[str, tuple[str, str]] = {}
    state: dict[str, int] = {}          # 1 = in progress, 2 = done

    def resolve(i: str, path: tuple[str, ...]) -> None:
        if state.get(i) == 2:
            return
        state[i] = 1
        if i not in own:
            own[i] = classify(i, nodes[i])
        st, note = own[i]
        reason = None
        for d in nodes[i].get("deps", []):
            if d not in nodes:
                reason = f"unknown dependency {d!r}"
                break
            if state.get(d) == 1:
                cyc = " -> ".join(path[path.index(d):] + (d,)) if d in path else d
                reason = f"dependency cycle: {cyc}"
                break
            resolve(d, path + (d,))
            if st in ("GREEN", "STAMPED") and out[d][0] in ("RED", "BLOCKED"):
                dn = out[d][1]
                reason = reason or (dn if dn.startswith("dependency cycle") else
                                    f"blocked by dependency {d} ({out[d][0]})")
        if reason and (st in ("GREEN", "STAMPED") or reason.startswith(("unknown", "dependency cycle"))):
            st, note = "BLOCKED", reason
        out[i] = (st, note)
        state[i] = 2

    for i in nodes:
        resolve(i, (i,))
    return {i: out[i] for i in nodes}


def mermaid(nodes: dict, status: dict) -> str:
    fill = {"STAMPED": "#0E6B5E,color:#fff", "GREEN": "#3f8f5f,color:#fff",
            "STALE": "#9A6510,color:#fff", "BLOCKED": "#8E2438,color:#fff",
            "RED": "#8E2438,color:#fff", "TODO": "#5a6468,color:#fff"}
    out = ["```mermaid", "graph LR"]
    for g, label in GROUPS.items():
        ids = [i for i, n in nodes.items() if n.get("group") == g]
        if not ids:
            continue
        out.append(f'  subgraph {g}["{label}"]')
        for i in ids:
            st = status[i][0]
            mark = {"STAMPED": "✓", "GREEN": "·", "STALE": "!",
                    "BLOCKED": "✗", "RED": "✗", "TODO": "○"}[st]
            out.append(f'    {i}["{mark} {nodes[i]["name"]}"]')
        out.append("  end")
    for i, n in nodes.items():
        for d in n.get("deps", []):
            if d in nodes:
                out.append(f"  {d} --> {i}")
    for i in nodes:
        out.append(f"  style {i} fill:{fill[status[i][0]]}")
    out.append("```")
    return "\n".join(out)


def table(nodes: dict, status: dict) -> str:
    rows = ["| | node | status | evidence |", "|---|---|---|---|"]
    for i, n in nodes.items():
        st, note = status[i]
        cls = "" if n.get("class") == "implementation" else " **fidelity**"
        rows.append(f"| `{i}` | {n['name']}{cls} | **{st}** | {note} |")
    return "\n".join(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if README does not match the evidence")
    ap.add_argument("--strict", action="store_true",
                    help="with --check, ALSO exit 1 if any node is RED or STALE")
    ap.add_argument("--print", action="store_true", help="write to stdout, not README")
    ap.add_argument("--run", action="store_true",
                    help="execute each node's evidence and record the result")
    ap.add_argument("--slow", action="store_true",
                    help="with --run, also execute nodes marked slow (iverilog; hours)")
    args = ap.parse_args()

    # stderr UNCONDITIONALLY, unlike tools/scorecard.py's tty-conditional
    # mirroring: stdout here is the DOCUMENT (under --print it is piped), so the
    # banner cannot go there without contaminating the block, and it must not go
    # into the block itself for the reason the comment further down records.
    stale = sc.checkout_staleness()
    if stale:
        print(stale, file=sys.stderr)

    nodes = json.loads(DAG.read_text())["nodes"]

    if args.run:
        sha = git("rev-parse", "HEAD")
        res = load_results()
        for i, n in nodes.items():
            if n.get("blocked") or not any(k in n for k in
                                           ("suite", "verifier", "tool", "evidence_file")):
                continue
            if n.get("slow") and not args.slow:
                continue                       # nightly runs these
            ok, detail = run_evidence(i, n)
            res[i] = {"passed": ok, "sha": sha, "detail": detail}
            print(f"  {'PASS' if ok else 'FAIL'}  {i:3s} {detail}", file=sys.stderr)
        RESULTS.write_text(json.dumps(res, indent=2, sort_keys=True) + "\n")

    status = classify_all(nodes)

    # The fidelity audit: a subsystem whose only evidence is against our own
    # model is UNVALIDATED, however many tests pass. Issue #45.
    warn = []
    for g in NEEDS_FIDELITY:
        ids = [i for i, n in nodes.items() if n.get("group") == g]
        fid = [i for i in ids if nodes[i].get("class") == "fidelity"
               and status[i][0] in ("GREEN", "STAMPED")]
        if ids and not fid:
            warn.append(f"{GROUPS[g]}: no green fidelity evidence -- UNVALIDATED "
                        f"against anything external")

    body = [mermaid(nodes, status), "", table(nodes, status)]
    if warn:
        body += ["", "> **Unvalidated subsystems.** " + "  \n> ".join(warn)]
    # NB: no commit SHA here, deliberately. An earlier version embedded
    # `git rev-parse HEAD`, which made the README stale the instant anything
    # merged -- so --check could never pass on main. That is the third gate in
    # this project that demanded something the job could not satisfy, and an
    # unsatisfiable gate trains people to ignore it. Per-node commits live in
    # docs/dag-results.json, which is where they belong.
    body += ["", "<sub>Compiled from `docs/dag.json` by `tools/compile_dag.py`. "
                 "Status is derived from evidence, not asserted.</sub>"]
    block = "\n".join(body)

    if args.print:
        print(block)
    else:
        txt = README.read_text()
        if BEGIN not in txt:
            print(f"README.md has no {BEGIN} marker", file=sys.stderr)
            return 2
        new = re.sub(re.escape(BEGIN) + r".*?" + re.escape(END),
                     f"{BEGIN}\n{block}\n{END}", txt, flags=re.S)
        if args.check and new != txt:
            print("README.md is out of date -- run tools/compile_dag.py", file=sys.stderr)
            return 1
        README.write_text(new)

    # --check asserts that THE DOCUMENT IS TRUE, not that the project is
    # healthy. A RED node accurately reported is this tool working; blocking
    # every merge until the drums are fixed and the slow verifiers re-run --
    # which the per-push job cannot do -- would be a gate demanding something
    # the job cannot satisfy, and this project has built three of those
    # already. The nightly, which CAN refresh the slow evidence, uses --strict.
    bad = [i for i in nodes if status[i][0] in ("STALE", "RED")]
    for i in bad:
        print(f"{status[i][0]}: {i} -- {status[i][1]}", file=sys.stderr)
    for w in warn:
        print(f"note: {w}", file=sys.stderr)
    return 1 if (args.check and args.strict and bad) else 0


if __name__ == "__main__":
    sys.exit(main())
