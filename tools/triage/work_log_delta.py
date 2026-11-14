#!/usr/bin/env python3
"""Compute the pending WORK_LOG.md delta and apply guide.md's write gates.

Implements, from guide.md Step 2:
  - GUIDE_DOCS_PR_EXCLUDE   self-loop filter (#5454): this phase's own docs PRs
  - complete fetch          (#6097/#6144): search-API total_count, bisect the
                            date window rather than trusting a fixed --limit
  - work_log_has_pr/issue   (#5516/#5539, anchored per #6087): presence checks
                            against the committed file, never a number watermark
  - debounce                (#6133): min-entries escape hatch, else elapsed
                            since WORK_LOG.md was last written by a merged
                            docs-maintenance PR

Prints the delta and the verdict. Writes nothing; --emit dumps the new sections.
"""
import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

LOOKBACK_DAYS = 30
ROTATE_OLDER_THAN_DAYS = 60
ROTATE_MARGIN_DAYS = 15
SAFETY_CAP = 1000
DOCS_TITLE = "docs: Guide document maintenance update"
DOCS_BRANCH_PREFIX = "docs/guide-update"


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    return (r.stdout if r.returncode == 0 else None), r.returncode, r.stderr


def gh_json(args):
    out, rc, err = run(["gh"] + args)
    if out is None:
        print(f"  ! gh {' '.join(args[:3])} failed: {err.strip()[:160]}",
              file=sys.stderr)
        return None
    return json.loads(out)


def repo_nwo():
    out, _, _ = run(["git", "remote", "get-url", "origin"])
    if not out:
        return None
    s = out.strip()
    s = re.sub(r"^git@[^:]+:", "", s)
    s = re.sub(r"^https?://[^/]+/", "", s)
    return re.sub(r"\.git/?$", "", s)


def total_count(search_range, predicate):
    """Ground truth window size; -1 means unknown (never treat as zero)."""
    nwo = repo_nwo()
    if not nwo:
        return -1
    out, _, _ = run(["gh", "api", "-X", "GET", "search/issues",
                     "-f", f"q=repo:{nwo} {predicate} {search_range}",
                     "--jq", ".total_count"])
    try:
        return int(out.strip())
    except (AttributeError, ValueError):
        return -1


KINDS = {
    "pr": dict(date_field="merged", predicate="is:pr is:merged", noun="pr",
               state="merged", fields="number,title,mergedAt,headRefName"),
    "issue": dict(date_field="closed", predicate="is:issue is:closed",
                  noun="issue", state="closed",
                  fields="number,title,closedAt"),
}


def fetch_complete(kind, start, end="", depth=0):
    k = KINDS[kind]
    rng = (f"{k['date_field']}:{start}..{end}" if end
           else f"{k['date_field']}:>={start}")
    true_n = total_count(rng, k["predicate"])

    if true_n > SAFETY_CAP and depth < 10:
        end_res = end or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        s_ep = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        e_ep = datetime.strptime(end_res, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if (e_ep - s_ep).days >= 2:
            mid = (s_ep + (e_ep - s_ep) / 2).strftime("%Y-%m-%d")
            left = fetch_complete(kind, start, mid, depth + 1)
            right = fetch_complete(kind, mid, end, depth + 1)
            merged = {it["number"]: it for it in (left + right)}
            return list(merged.values())

    batch = gh_json([k["noun"], "list", "--state", k["state"], "--search", rng,
                     "--limit", str(SAFETY_CAP), "--json", k["fields"]]) or []
    if true_n >= 0 and len(batch) != true_n:
        print(f"  ! WARNING: {kind} window [{start},{end or 'now'}) returned "
              f"{len(batch)} of {true_n} (total_count) - possible truncation",
              file=sys.stderr)
    return batch


def is_docs_pr(pr):
    return ((pr.get("headRefName") or "").startswith(DOCS_BRANCH_PREFIX)
            or pr.get("title") == DOCS_TITLE)


def has_pr(text, n):
    return re.search(rf"^- \*\*PR #{n}\*\*", text, re.M) is not None


def has_issue(text, n):
    return re.search(rf"^- \*\*Issue #{n}\*\* ", text, re.M) is not None


def last_write_epoch(path_filter):
    prs = gh_json(["pr", "list", "--state", "merged", "--limit", "30",
                   "--json", "number,title,mergedAt,headRefName,files"]) or []
    best = 0
    for pr in prs:
        if not is_docs_pr(pr):
            continue
        paths = [f["path"] for f in (pr.get("files") or [])]
        if path_filter not in paths:
            continue
        ts = pr.get("mergedAt")
        if ts:
            ep = int(datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")
                     .replace(tzinfo=timezone.utc).timestamp())
            best = max(best, ep)
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work-log", required=True)
    ap.add_argument("--emit", action="store_true")
    a = ap.parse_args()

    text = open(a.work_log).read()
    since = (datetime.now(timezone.utc)
             - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    print(f"lookback since {since} ({LOOKBACK_DAYS}d)")

    prs_raw = fetch_complete("pr", since)
    prs = [p for p in prs_raw if not is_docs_pr(p)]
    print(f"merged PRs in window: {len(prs_raw)} fetched, "
          f"{len(prs_raw) - len(prs)} were Guide docs PRs (excluded)")
    new_prs = [p for p in prs if not has_pr(text, p["number"])]

    issues = fetch_complete("issue", since)
    print(f"closed issues in window: {len(issues)}")
    new_issues = [i for i in issues if not has_issue(text, i["number"])]

    total_new = len(new_prs) + len(new_issues)
    print(f"\npending: {len(new_prs)} new PR(s) + {len(new_issues)} new "
          f"issue(s) = {total_new}")

    for p in sorted(new_prs, key=lambda x: x["number"]):
        print(f"  + PR #{p['number']} ({p['mergedAt'][:10]}) {p['title'][:60]}")
    for i in sorted(new_issues, key=lambda x: x["number"]):
        print(f"  + Issue #{i['number']} ({i['closedAt'][:10]}) {i['title'][:56]}")

    if total_new == 0:
        print("\nVERDICT: WORK_LOG.md is current. No write.")
        return 1

    min_entries = 5
    debounce = 1800
    try:
        cfg = json.load(open(".loom/config.json"))
        debounce = int(cfg.get("guide", {}).get("docsMaintenance", {})
                       .get("workLogDebounceSecs", debounce))
    except Exception:
        pass

    if total_new < min_entries:
        last = last_write_epoch("WORK_LOG.md")
        now = int(datetime.now(timezone.utc).timestamp())
        elapsed = now - last
        print(f"\ndebounce: last WORK_LOG write {elapsed}s ago "
              f"(window {debounce}s, min-entries {min_entries})")
        if last > 0 and elapsed < debounce:
            print("VERDICT: batching for a later tick (under both gates).")
            return 1
    else:
        print(f"\ndebounce: {total_new} >= min-entries {min_entries} "
              f"-> write immediately")

    print("VERDICT: WRITE")

    if a.emit:
        groups = {}
        for p in new_prs:
            groups.setdefault(p["mergedAt"][:10], []).append(
                (p["number"], f"- **PR #{p['number']}**: {p['title']}"))
        for i in new_issues:
            groups.setdefault(i["closedAt"][:10], []).append(
                (i["number"], f"- **Issue #{i['number']}** (closed): {i['title']}"))
        with open("/tmp/work_log_new_sections.md", "w") as fh:
            for date in sorted(groups, reverse=True):
                fh.write(f"### {date}\n\n")
                for _, line in sorted(groups[date]):
                    fh.write(line + "\n")
                fh.write("\n")
        print("emitted /tmp/work_log_new_sections.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
