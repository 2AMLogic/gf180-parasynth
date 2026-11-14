#!/usr/bin/env python3
"""Find issues that a merged PR completed but left open (missing `Closes #N`).

Read-only. Applies guide.md's partial-increment exclusions BEFORE proposing any
closure: a `Part of #N` / `Contributes to #N` reference, or a
`loom:epic`/`loom:epic-phase` issue, is deliberately kept open across
increments and is NOT an orphan.
"""
import json
import re
import subprocess

LOOKBACK = 40  # most recent merged PRs to examine
CLOSE_KW = re.compile(
    r"\b(?:clos(?:e|es|ed)|fix(?:es|ed)?|resolv(?:e|es|ed))\s+#(?P<num>\d+)",
    re.IGNORECASE)
PARTIAL_KW = re.compile(r"\b(?:part of|contributes to)\s+#(?P<num>\d+)",
                        re.IGNORECASE)
BARE_REF = re.compile(r"(?:^|[^\w/])#(\d+)")
KEEP_OPEN_LABELS = {"loom:epic", "loom:epic-phase"}

_issue_cache = {}


def gh_json(args):
    r = subprocess.run(["gh"] + args, capture_output=True, text=True)
    if r.returncode != 0:
        return None
    return json.loads(r.stdout)


def issue_info(num):
    if num in _issue_cache:
        return _issue_cache[num]
    info = gh_json(["issue", "view", str(num), "--json",
                    "number,state,title,labels"])
    _issue_cache[num] = info
    return info


def main():
    prs = gh_json(["pr", "list", "--state", "merged", "--limit", str(LOOKBACK),
                   "--json", "number,title,body,mergedAt,headRefName"]) or []
    print(f"examined {len(prs)} most recently merged PRs\n")

    orphans, partials, auto_ok = [], [], 0
    for pr in prs:
        body = pr.get("body") or ""
        # Skip Guide's own docs-maintenance PRs: they close nothing.
        if (pr.get("headRefName") or "").startswith("docs/guide-update"):
            continue
        closed_refs = {int(m.group("num")) for m in CLOSE_KW.finditer(body)}
        partial_refs = {int(m.group("num")) for m in PARTIAL_KW.finditer(body)}
        # Any bare `#N` that is NOT already a closing or partial reference is a
        # candidate for the sloppy-syntax case guide.md describes.
        bare = {int(m.group(1)) for m in BARE_REF.finditer(body)}
        sloppy = bare - closed_refs - partial_refs

        for num in sorted(closed_refs):
            info = issue_info(num)
            if info and info["state"] == "OPEN":
                labels = {l["name"] for l in info["labels"]}
                tag = ("epic/family" if labels & KEEP_OPEN_LABELS else "OPEN")
                orphans.append((pr["number"], num, info["title"], tag,
                                "declared Closes but still open"))
            elif info:
                auto_ok += 1

        for num in sorted(partial_refs):
            info = issue_info(num)
            if info and info["state"] == "OPEN":
                partials.append((pr["number"], num, info["title"]))

        # Report sloppy bare refs only when the PR declared NO closing keyword
        # at all -- otherwise a bare mention is just prose, not a missed close.
        if not closed_refs and not partial_refs:
            for num in sorted(sloppy):
                info = issue_info(num)
                if not info or info["state"] != "OPEN":
                    continue
                labels = {l["name"] for l in info["labels"]}
                if labels & KEEP_OPEN_LABELS:
                    continue
                orphans.append((pr["number"], num, info["title"],
                                ",".join(sorted(labels)) or "-",
                                "bare #N only, no closing keyword"))

    print("=" * 72)
    print(f"POSSIBLE ORPHANED CLOSURES ({len(orphans)}) - verify before closing")
    print("=" * 72)
    for prn, num, title, labels, why in orphans:
        print(f"PR #{prn} -> issue #{num} [{labels}]")
        print(f"    {title[:68]}")
        print(f"    reason: {why}\n")

    print("=" * 72)
    print(f"INTENTIONAL PARTIAL INCREMENTS ({len(partials)}) - leave open")
    print("=" * 72)
    for prn, num, title in partials:
        print(f"PR #{prn} -> #{num} (Part of/Contributes to): {title[:55]}")

    print(f"\nclosing refs that resolved correctly: {auto_ok}")


if __name__ == "__main__":
    main()
