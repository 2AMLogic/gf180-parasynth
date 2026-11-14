#!/usr/bin/env python3
"""Render WORK_PLAN.md's generated region exactly as guide.md's
render_plan_body() specifies, and apply the #5890/#5929 debounce.

Determinism notes carried over from the prompt:
  - #6993: every listing is sorted by number. GitHub's search-backed listings
    are not stably ordered, and the region is compared byte-for-byte, so an
    unsorted render makes reorder noise look like a label-state change.
  - #6457: `held` is DERIVED from the already-fetched loom:pr list, never a
    second query, so the two can never disagree.
  - #7083: `ready` excludes issues already closed by an open loom:pr PR.
"""
import json
import re
import subprocess
import sys
from datetime import datetime, timezone

DOCS_TITLE = "docs: Guide document maintenance update"
DOCS_BRANCH_PREFIX = "docs/guide-update"
START = "<!-- guide:plan-body:start -->"
END = "<!-- guide:plan-body:end -->"


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    return (r.stdout if r.returncode == 0 else None)


def gh_json(args):
    out = run(["gh"] + args)
    if out is None:
        sys.exit(f"gh {' '.join(args[:4])} failed")
    return json.loads(out)


def label_set(prop, fallback):
    out = run(["loom-daemon", "labels", "list", "--property", prop,
               "--with-retired"])
    return [l for l in (out or fallback).split("\n") if l.strip()]


def not_frozen(items):
    return [i for i in items
            if "loom:frozen" not in {l["name"] for l in (i.get("labels") or [])}]


def by_num(items):
    return sorted(items, key=lambda i: i["number"])


def bullets(items, suffix=""):
    return "\n".join(f"- **#{i['number']}**: {i['title']}{suffix}"
                     for i in by_num(items))


def section(heading, blurb, body):
    out = f"## {heading}\n"
    if blurb:
        out += f"\n{blurb}\n"
    out += f"\n{body if body else '_None._'}\n"
    return out


def count(body):
    return len([l for l in body.split("\n") if l.startswith("- ")]) if body else 0


def main():
    plan_path = sys.argv[1]
    hold = label_set("hold", "loom:blocked\nloom:operator\nloom:frozen")
    opgate = label_set("operator_gate", "loom:operator")
    hold_q = " ".join(f"-label:{h}" for h in hold)
    op_q = " ".join(f"-label:{o}" for o in opgate)

    vimp = not_frozen(gh_json(["issue", "list", "--label", "loom:very-important",
                               "--state", "open", "--limit", "200", "--json",
                               "number,title,labels"]))
    imp = not_frozen(gh_json(["issue", "list", "--label", "loom:important",
                              "--state", "open", "--limit", "200", "--json",
                              "number,title,labels"]))
    ready_json = gh_json(["issue", "list", "--label", "loom:issue", "--search",
                          f"-label:loom:building {hold_q}", "--state", "open",
                          "--limit", "200", "--json", "number,title"])
    building = gh_json(["issue", "list", "--label", "loom:building", "--search",
                        f"{op_q} -label:loom:frozen", "--state", "open",
                        "--limit", "200", "--json", "number,title"])
    review = not_frozen(gh_json(["pr", "list", "--label",
                                 "loom:review-requested", "--state", "open",
                                 "--limit", "200", "--json",
                                 "number,title,labels"]))
    approved_json = not_frozen(gh_json(
        ["pr", "list", "--label", "loom:pr", "--state", "open", "--limit",
         "200", "--json", "number,title,labels,closingIssuesReferences"]))
    approved_json = by_num(approved_json)
    held = [p for p in approved_json
            if {"loom:operator", "loom:blocked"} & {l["name"] for l in p["labels"]}]
    pr_linked = {r["number"] for p in approved_json
                 for r in (p.get("closingIssuesReferences") or [])}
    ready = [i for i in ready_json if i["number"] not in pr_linked]
    curated = not_frozen(gh_json(["issue", "list", "--label", "loom:curated",
                                  "--state", "open", "--limit", "200",
                                  "--json", "number,title,labels"]))
    architect = not_frozen(gh_json(["issue", "list", "--label", "loom:architect",
                                    "--state", "open", "--limit", "200",
                                    "--json", "number,title,labels"]))
    hermit = not_frozen(gh_json(["issue", "list", "--label", "loom:hermit",
                                 "--state", "open", "--limit", "200",
                                 "--json", "number,title,labels"]))
    epics = not_frozen(gh_json(["issue", "list", "--label", "loom:epic",
                                "--state", "open", "--limit", "200", "--json",
                                "number,title,labels"]))

    b_held, b_vimp, b_imp = bullets(held), bullets(vimp), bullets(imp)
    b_ready, b_building = bullets(ready), bullets(building)
    b_review, b_approved = bullets(review), bullets(approved_json)
    b_curated = bullets(curated, " *(curated)*")
    b_arch = bullets(architect, " *(architect)*")
    b_herm = bullets(hermit, " *(hermit)*")
    b_prop = b_arch + ("\n" if b_arch and b_herm else "") + b_herm
    b_epics = bullets(epics)

    parts = [
        section("Operator Attention: Merge-Risk-Hold Pileup",
                "Judge-approved PRs under a hold (`loom:blocked` or "
                "`loom:operator`) — only a human merge decision is missing.",
                b_held),
        section("Very Important",
                "`loom:very-important` (one counts per workspace); every stage "
                "takes these first.", b_vimp),
        section("Important",
                "`loom:important`; taken before unlabelled work.", b_imp),
        section("Ready",
                "Human-approved issues ready for implementation "
                "(`loom:issue`).", b_ready),
        section("In Progress",
                "Issues currently being built (`loom:building`).", b_building),
        section("PRs Awaiting Review",
                "PRs waiting on Judge (`loom:review-requested`).", b_review),
        section("Approved (Awaiting Merge)",
                "PRs that passed review and are queued for Champion auto-merge "
                "(`loom:pr`).", b_approved),
        section("Proposed", "Issues carrying `loom:curated`.", b_curated),
        section("Proposed (Architect / Hermit)", "", b_prop),
        section("Epics", "", b_epics),
        section("Backlog Balance", "", "\n".join([
            "| Bucket | Count |",
            "|------|-------|",
            f"| Operator merge-risk holds | {count(b_held)} |",
            f"| Very important | {count(b_vimp)} |",
            f"| Important | {count(b_imp)} |",
            f"| Ready (`loom:issue`) | {count(b_ready)} |",
            f"| In Progress (`loom:building`) | {count(b_building)} |",
            f"| PRs awaiting review | {count(b_review)} |",
            f"| Approved PRs awaiting merge | {count(b_approved)} |",
            f"| Curated | {count(b_curated)} |",
            f"| Architect / Hermit proposals | {count(b_prop)} |",
            f"| Active epics | {count(b_epics)} |",
        ])),
    ]
    new_body = "\n".join(parts).rstrip("\n")

    text = open(plan_path).read()
    m = re.search(re.escape(START) + r"\n(.*?)\n?" + re.escape(END), text,
                  re.S)
    if not m:
        sys.exit("markers not found in WORK_PLAN.md")
    old_body = m.group(1).rstrip("\n")

    if new_body == old_body:
        print("WORK_PLAN.md is current (no label changes detected).")
        return 1

    # #5890/#5929 debounce, anchored on the last merged docs PR that actually
    # touched WORK_PLAN.md.
    prs = gh_json(["pr", "list", "--state", "merged", "--limit", "30", "--json",
                   "number,title,mergedAt,headRefName,files"])
    last = 0
    for pr in prs:
        is_docs = ((pr.get("headRefName") or "").startswith(DOCS_BRANCH_PREFIX)
                   or pr.get("title") == DOCS_TITLE)
        if not is_docs:
            continue
        if "WORK_PLAN.md" not in [f["path"] for f in (pr.get("files") or [])]:
            continue
        ts = pr.get("mergedAt")
        if ts:
            last = max(last, int(datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")
                                 .replace(tzinfo=timezone.utc).timestamp()))
    debounce = 3600
    try:
        cfg = json.load(open(".loom/config.json"))
        debounce = int(cfg.get("guide", {}).get("docsMaintenance", {})
                       .get("workPlanDebounceSecs", debounce))
    except Exception:
        pass
    now = int(datetime.now(timezone.utc).timestamp())
    elapsed = now - last
    print(f"WORK_PLAN differs; last WORK_PLAN write {elapsed}s ago "
          f"(debounce {debounce}s)")
    if last > 0 and elapsed < debounce:
        print("VERDICT: suppressing this tick's rewrite (within debounce).")
        return 1

    # Show what changes before writing.
    import difflib
    diff = list(difflib.unified_diff(old_body.split("\n"), new_body.split("\n"),
                                     "committed", "rendered", lineterm="", n=1))
    print("\n".join(diff[:80]) or "(no textual diff?)")

    open(plan_path, "w").write(
        text[:m.start(1)] + new_body + "\n" + text[m.end(1):].lstrip("\n")
        if False else
        text[:m.start()] + START + "\n" + new_body + "\n" + END + text[m.end():])
    print("\nVERDICT: WRITTEN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
