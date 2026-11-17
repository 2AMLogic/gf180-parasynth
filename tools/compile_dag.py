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
  REFUSED   the evidence cannot be judged at all -- a record whose commit
            left this history, evidence with no covered files to measure
            (#824), or a covered file that cannot be diffed against HEAD
            (#863); never rendered as current

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
import run_all as ra                                                # noqa: E402

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


def sha_is_ancestor(sha: str) -> bool:
    """A sha that is not an ancestor of HEAD cannot measure staleness (#824).

    Reads a RETURN CODE, not stdout -- exactly why `git()` cannot be used
    here: its stdout-only contract conflates "command failed" with "no
    output", which is the defect. And `--is-ancestor`, not `cat-file -e`:
    a sha still in the object store but no longer reachable (force-pushed
    away before gc) must refuse too, or the range query would silently
    answer about a divergent history.
    """
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
    """The recorded evidence, keyed by node. ABSENT means no evidence yet; a
    file that is present but unreadable or the wrong shape is REFUSED (#600).
    It used to read as {} -- every node unrun -- and `--run` would then write
    its partial results over the file, deleting the slow nodes' stamps."""
    if not RESULTS.exists():
        return {}
    try:
        doc = json.loads(RESULTS.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise SystemExit(f"REFUSED: {RESULTS} is present but unreadable "
                         f"({type(e).__name__}: {e}); not treating it as "
                         f"'no evidence yet'. Restore it from git.")
    if not isinstance(doc, dict):
        raise SystemExit(f"REFUSED: {RESULTS} holds a {type(doc).__name__}, "
                         f"not an object keyed by node")
    return doc


# #863, rule 8: the input that defeats the #824 fix through a new door.
# stale_against() answered "are the bytes this evidence was recorded against
# still the bytes at HEAD?" with `git log {at_sha}..HEAD -- <f>` -- a commit
# HISTORY walk answering a CONTENT question. The two coincide on a linear
# chain, which is why main's own board never showed it; they diverge on the
# merge commits a conflict round produces. The shape that fires (verified
# against both PR #818 rounds, a4ae3529 and b5c4f677): a merge whose covered
# file is TREESAME to a parent that lies OUTSIDE stamp..HEAD -- an ancestor
# of the record stamp -- while its other parent is in range with different
# content. Simplification cannot rewrite the merge to that TREESAME parent
# (it is uninteresting), so the merge itself is reported, and a node whose
# file never changed renders STALE. The obvious candidate repair is refuted
# by measurement: `--full-history` reports MORE commits on the real rounds
# (b5c4f677 went from 1 to 4), turns two benign merge shapes false-positive
# that the default walk answers correctly, and flips F2 STAMPED->STALE on
# main's own board -- a new cry of wolf, the exact outcome this defect
# produces. A content comparison (`git diff --name-only <stamp> HEAD --
# <f>`) is correct on all four synthetic shapes and both reported rounds,
# and changes 0 of 15 statuses on main. And the comparison must NOT go
# through `git()`: its stdout-only contract reads a failed command as "no
# output", which here would mean GREEN with nothing measured -- #824's hole
# through a new door. The return code is read, and "cannot measure" comes
# back as its own state, rendered REFUSED.
def stale_against(n: dict, at_sha: str) -> tuple[str, str]:
    """Has anything this node covers changed since the evidence was recorded?

    Compares CONTENT at `at_sha` against HEAD (#863), per `covers` entry --
    a file or a directory, both ordinary diff pathspecs. Returns
    ("changed", <first changed cover>), ("fresh", "") when every cover is
    byte-identical, or ("unmeasurable", <detail>) when git cannot answer.
    classify() renders the third REFUSED: not GREEN (no measurement was
    taken), and not STALE either (nothing observed changed).
    """
    if not at_sha:
        return "unmeasurable", "no commit to measure against"
    for f in n.get("covers", []):
        try:
            r = subprocess.run(["git", "diff", "--name-only", at_sha, "HEAD",
                                "--", f], cwd=ROOT, capture_output=True,
                               text=True, timeout=30)
        except Exception as exc:
            return "unmeasurable", f"{f}: {type(exc).__name__}"
        if r.returncode != 0:
            err = (r.stderr or "").strip().splitlines()
            why = f" ({err[0][:70]})" if err else ""
            return "unmeasurable", f"{f}: git diff failed{why}"
        if r.stdout.strip():
            return "changed", f
    return "fresh", ""


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
    # A FAILING record stays RED: the run's verdict is already stated, and
    # is more precise than a refusal. Hoisted ABOVE the empty-covers guard
    # below (#858): that guard's `or tag_is_ancestor(tag)` disjunct fired on
    # the tag alone, so a failing record on a covers-less tagged node
    # rendered REFUSED -- a known failure reported as a cannot-answer --
    # instead of the failure it is. The only checks this jumps are the
    # empty-covers guard itself and the `not rec`-gated ones below it, none
    # of which a failing record could answer more precisely.
    if rec and not rec.get("passed"):
        return "RED", rec.get("detail", "failed")
    # #824, rule 8: the input that defeats the sha guard. Staleness is
    # measured over `covers`; evidence with no covered files named -- a
    # passing record, or an in-history stamp -- has nothing to go stale, and
    # the loop would measure nothing and answer "not stale".
    if not n.get("covers", []) and ((rec and rec.get("passed"))
                                    or tag_is_ancestor(tag)):
        return "REFUSED", ("evidence exists but the node declares no "
                           "covered files -- staleness cannot be measured")
    if not rec and tag_is_ancestor(tag):
        tag_sha = git("rev-list", "-n1", tag)
        state, detail = stale_against(n, tag_sha)
        if state == "changed":
            return "STALE", f"{detail} changed since {tag} was cut"
        if state == "unmeasurable":
            return "REFUSED", (f"staleness against {tag} cannot be "
                               f"measured: {detail}")
        return "STAMPED", f"{tag} (not re-run; verifier is slow)"

    if not rec:
        return "TODO", "never run -- `tools/compile_dag.py --run`"

    # #824: staleness is measured against a sha in this history. A record
    # with no commit stamp, or a sha that left this history (squash-merged,
    # force-pushed, or a clone too shallow to contain it, where the range
    # query reads "failed" as "nothing changed"), cannot be checked at all
    # -- REFUSED, never rendered current. Matches the tag path, which has
    # refused unanchored tags through tag_is_ancestor all along.
    rec_sha = rec.get("sha", "")
    if not rec_sha:
        return "REFUSED", ("the record carries no commit sha -- staleness "
                           "cannot be measured")
    if not sha_is_ancestor(rec_sha):
        return "REFUSED", (f"recorded sha {rec_sha[:8]} is not in this "
                           f"history -- cannot tell whether "
                           f"{len(n.get('covers', []))} covered file(s) changed")

    state, detail = stale_against(n, rec_sha)
    if state == "changed":
        return "STALE", f"{detail} changed since this was last run"
    if state == "unmeasurable":
        return "REFUSED", f"staleness cannot be measured: {detail}"

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
      * only RED/BLOCKED deps propagate. TODO, STALE and REFUSED are not
        known-failing, and propagating them would turn most of the graph
        red on a stale stamp;
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
            "RED": "#8E2438,color:#fff", "TODO": "#5a6468,color:#fff",
            "REFUSED": "#5E4B8A,color:#fff"}
    out = ["```mermaid", "graph LR"]
    for g, label in GROUPS.items():
        ids = [i for i, n in nodes.items() if n.get("group") == g]
        if not ids:
            continue
        out.append(f'  subgraph {g}["{label}"]')
        for i in ids:
            st = status[i][0]
            mark = {"STAMPED": "✓", "GREEN": "·", "STALE": "!",
                    "BLOCKED": "✗", "RED": "✗", "TODO": "○",
                    "REFUSED": "?"}[st]
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
                    help="with --check, ALSO exit 1 if any node is RED, "
                         "STALE or REFUSED")
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
            try:
                ok, detail = run_evidence(i, n)
            except Exception as exc:
                # A raise here -- a verifier TimeoutExpired above all -- used
                # to escape the loop, so the single write after it never ran
                # and the run's verdicts were discarded (#823). A raising node
                # is RED evidence carrying the exception type, not a skip.
                # `except Exception` leaves KeyboardInterrupt and the REFUSED
                # SystemExit of load_results() loud (#600).
                ok = False
                detail = f"{type(exc).__name__}: {exc}"[:90]
            res[i] = {"passed": ok, "sha": sha, "detail": detail}
            print(f"  {'PASS' if ok else 'FAIL'}  {i:3s} {detail}", file=sys.stderr)
            # Written after EVERY node, not once after the loop: a job-level
            # kill (the nightly's timeout-minutes, a runner eviction, SIGKILL)
            # cannot be handled, only out-saved. What is already on disk
            # survives it; a handler alone would not. Atomic replace, not
            # write_text: a kill inside a truncate-then-write leaves truncated
            # JSON that load_results() REFUSES on until git restores it
            # (#842). fsync stays: os.replace alone covers the process-kill
            # threat named above, and the fsync adds machine-loss durability
            # on the persistent workspaces where this runs, for ~1 KB a node.
            ra.write_json_atomic(RESULTS, res, sort_keys=True)

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
    bad = [i for i in nodes if status[i][0] in ("STALE", "RED", "REFUSED")]
    for i in bad:
        print(f"{status[i][0]}: {i} -- {status[i][1]}", file=sys.stderr)
    for w in warn:
        print(f"note: {w}", file=sys.stderr)
    return 1 if (args.check and args.strict and bad) else 0


if __name__ == "__main__":
    sys.exit(main())
