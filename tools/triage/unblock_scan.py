#!/usr/bin/env python3
"""Guide unblock scan: which loom:blocked items have fully resolved dependencies?

Read-only. Reports candidates and the reason each one is kept or skipped, so a
decision is auditable before any label is touched.

Implements the guide.md gates:
  - parse_dependencies      (body only; unchecked task items only)
  - has_permanent_block     (trusted comments / trusted body marker)
  - has_superseding_block   (#4634 + #7267: linked open PR blocking labels,
                             or CONFLICTING/DIRTY merge state)
  - was_previously_approved (#8742 approval archaeology, per-issue events)
"""
import json
import re
import subprocess
import sys

REPO = None
DEP_RE = re.compile(
    r"(Blocked by|Depends on|Requires|-\s\[ \])[*_:\s]*"
    r"(([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)?#[0-9]+)",
    re.IGNORECASE,
)
REF_RE = re.compile(r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)?#([0-9]+)")
PERM_RE = re.compile(r"(^|\n)[ \t]*<!-- loom:permanent-block -->[ \t\r]*(\n|$)")
BLOCKING_PR_LABELS = {"loom:changes-requested", "loom:blocked", "loom:operator"}

_state_cache = {}


def gh(args, check=True):
    r = subprocess.run(["gh"] + args, capture_output=True, text=True)
    if r.returncode != 0:
        if check:
            raise RuntimeError(f"gh {' '.join(args)}: {r.stderr.strip()}")
        return None
    return r.stdout


def parse_dependencies(body):
    """Every OWNER/REPO#N or #N on a dependency-declaring line of the body."""
    deps = set()
    for line in (body or "").splitlines():
        if not DEP_RE.search(line):
            continue
        # A checked task item records done work, not a gate (#7973).
        if re.match(r"\s*-\s\[[xX]\]", line):
            continue
        for repo, num in REF_RE.findall(line):
            deps.add(f"{repo}#{num}" if repo else f"#{num}")
    return sorted(deps)


def dep_state(ref):
    """CLOSED / MERGED / OPEN / UNKNOWN for an issue-or-PR reference."""
    if ref in _state_cache:
        return _state_cache[ref]
    repo, num = (ref.split("#", 1) if "/" in ref else (None, ref.lstrip("#")))
    num = num.lstrip("#")
    args = ["issue", "view", num, "--json", "state"]
    if repo:
        args += ["--repo", repo]
    out = gh(args, check=False)
    if out is None:
        # Not an issue -> may be a PR in this repo.
        args = ["pr", "view", num, "--json", "state"]
        if repo:
            args += ["--repo", repo]
        out = gh(args, check=False)
    state = json.loads(out)["state"] if out else "UNKNOWN"
    _state_cache[ref] = state
    return state


def has_permanent_block(number):
    """Unreadable => True (fail safe: never unblock what we cannot read)."""
    r = subprocess.run(
        ["loom-daemon", "forge", "trusted-comments", "--fetch", str(number),
         "--with-body"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return True
    try:
        items = json.loads(r.stdout)
    except json.JSONDecodeError:
        return True
    return any(PERM_RE.search(it.get("body") or "") for it in items)


def has_superseding_block(number):
    """An open linked PR that is held or cannot land supersedes the body deps."""
    out = gh(["issue", "view", str(number), "--json",
              "closedByPullRequestsReferences"], check=False)
    if out is None:
        return True, "could not read closes-graph"
    refs = json.loads(out).get("closedByPullRequestsReferences") or []
    for ref in refs:
        pr = ref["number"]
        pout = gh(["pr", "view", str(pr), "--json",
                   "state,labels,mergeable,mergeStateStatus"], check=False)
        if pout is None:
            continue
        p = json.loads(pout)
        if p.get("state") != "OPEN":
            continue
        names = {l["name"] for l in (p.get("labels") or [])}
        hit = names & BLOCKING_PR_LABELS
        if hit:
            return True, f"PR #{pr} open with {sorted(hit)}"
        if p.get("mergeable") == "CONFLICTING" or \
           p.get("mergeStateStatus") in ("DIRTY", "CONFLICTING"):
            return True, (f"PR #{pr} open, mergeable={p.get('mergeable')} "
                          f"mergeStateStatus={p.get('mergeStateStatus')}")
    return False, ""


def was_previously_approved(number):
    out = gh(["api", "--paginate",
              f"repos/{{owner}}/{{repo}}/issues/{number}/events?per_page=100",
              "--jq", '.[] | select(.event == "labeled" and '
                      '.label.name == "loom:issue") | 1'], check=False)
    return bool(out and out.strip())


def main():
    blocked = json.loads(gh([
        "issue", "list", "--label", "loom:blocked", "--search",
        "-label:loom:frozen", "--state", "open", "--limit", "1000",
        "--json", "number,title,body,labels",
    ]))
    print(f"scanned {len(blocked)} blocked issues\n")

    candidates, skipped, no_deps = [], [], []
    for iss in sorted(blocked, key=lambda i: i["number"]):
        n, title = iss["number"], iss["title"]
        deps = parse_dependencies(iss.get("body") or "")
        if not deps:
            no_deps.append((n, title))
            continue
        states = {d: dep_state(d) for d in deps}
        unresolved = {d: s for d, s in states.items()
                      if s not in ("CLOSED", "MERGED")}
        if unresolved:
            skipped.append((n, title, f"deps still open: "
                            f"{', '.join(f'{d}={s}' for d, s in unresolved.items())}"))
            continue
        if has_permanent_block(n):
            skipped.append((n, title, "permanent-block marker (or unreadable)"))
            continue
        sup, why = has_superseding_block(n)
        if sup:
            skipped.append((n, title, f"superseding block: {why}"))
            continue
        approved = was_previously_approved(n)
        candidates.append((n, title, sorted(states), approved))

    print("=" * 72)
    print(f"UNBLOCK CANDIDATES ({len(candidates)})")
    print("=" * 72)
    for n, title, deps, approved in candidates:
        action = ("restore loom:issue" if approved
                  else "remove loom:blocked ONLY (never approved)")
        print(f"#{n} {title[:70]}")
        print(f"    deps resolved: {', '.join(deps)}")
        print(f"    action: {action}\n")

    print("=" * 72)
    print(f"SKIPPED, dependency declared ({len(skipped)})")
    print("=" * 72)
    for n, title, why in skipped:
        print(f"#{n} {title[:60]}\n    {why}")

    print()
    print("=" * 72)
    print(f"NO PARSEABLE BODY DEPENDENCY ({len(no_deps)}) - manual review only")
    print("=" * 72)
    for n, title in no_deps:
        print(f"#{n} {title[:70]}")


if __name__ == "__main__":
    main()
