#!/usr/bin/env python3
"""Gate: each nightly slow-node DAG record is corroborated by the run's own evidence.

    tools/check_nightly_slow_records.py \
      --published published.json --at-run at-run.json \
      --log full-rtl.log --head-sha <40-hex> --run-id R [--job-id J]

WHY. #820's criterion (a) originally demanded every slow record's `sha` equal
the verifying nightly's `headSha`. #967's `record_unchanged()` makes that
unsatisfiable on a quiet night: when a re-run repeats the verdict and the
normalized detail and nothing the node covers has moved, the record is kept
byte-for-byte and `sha` stays the staleness anchor (91398a0c...) -- a
provenance anchor, never a last-verified stamp. Scheduled run 44344077192
(2026-11-18, job 134941613343 @ 4a6eda4d) measured exactly that: every slow
verifier re-ran and passed, every node printed `keep ... (unchanged since
91398a0c)`, the commit step reported the records unchanged -- and the old
clause read red on machinery that measured green (issue #1058).

This gate implements the amended per-node rule. A node passes only on
execution evidence from the run, never on the stored boolean alone:

  rewritten -- the record's `sha` equals the run's full `headSha` AND the log
      carries the node's own `PASS` line from the recording step;
  retained  -- the log carries the node's own
      `keep <node> <detail> (unchanged since <anchor>)` line, the anchor
      matches the record's `sha` prefix and the detail is byte-equal to the
      record's `detail`, AND the published record is identical to the record
      at the run's own checkout;
  either way the published record must carry `passed == true`.

A node with neither form of execution evidence fails even when its record
says `passed`: the branch content #818 shipped (six bare records at
91398a0c) still fails, which is the defeat the original clause existed to
close. A line from another run -- a foreign anchor or a foreign detail --
fails, so a green step cannot launder a record it did not measure.

The log is asserted to belong to the run it names, at the point of use:
`--head-sha` must equal what the checkout step itself reports -- the bare
sha on the line following its `git rev-parse refs/remotes/origin/main` or
`git log -1 --format=%H` command -- or the gate REFUSES naming what is
missing. Appear-anywhere is not attribution: run 44344077192's log also
names actions/checkout's own commit (11d5960a...) on its `Download action
repository` line, and that dependency commit satisfied the previous
any-line substring check -- a 6/6 PASS on a sha that is not this
repository's head (#1070). `--run-id` is required, not optional: on a
quiet night `origin/main` does not move, so two consecutive nightlies
share the same `headSha` and a previous night's log satisfies the
head-sha assertion -- measured in #1066, where a stale log with its run
id rewritten out passed 6/6 without it, and only the run id
discriminates two runs at the same head. The run id must EQUAL the
complete numeric run field of the URL in upload-artifact's own
`Artifact download URL:` record
(`/actions/runs/<id>/artifacts/<artifact>`) -- substring presence is not
attribution: the artifact id and a single space each appear in a real
log and passed a substring check (measured on run 44344077192's log,
#1066 review), so only exact field equality attributes it. The record
itself is the COMPLETE line after the log timestamp -- label, single
space, one URL token, end of line: a diagnostic naming another run's
artifact path (`Previous baseline path:
/actions/runs/<other>/artifacts/<n>`) attributed that foreign run while
every URL-bearing line was scanned, and a quoted warning embedding the
marker (`##[warning]Previous nightly reported: Artifact download URL:
<foreign URL>`) or free text wedged between label and URL
(`Artifact download URL: unavailable. Previous nightly: <foreign URL>`)
each fed the extractor's wildcard the same way (#1066 review) -- so
neither the marker mid-line nor a URL that does not IMMEDIATELY follow
the label attributes anything. A fetch that silently
failed -- or a previous night's log left at the same fixed path -- would
otherwise be attributed to tonight's run and answered in the voice of a
measurement. Measured on the two replayed nightlies (44344077192 job
134941613343 @ 4a6eda4d; 44128504549 job 134198792633): the checkout step
prints the full head sha on the line after both commands in each, and the
run id appears in each (upload-artifact's download URL), so both anchors
are stable enough to assert -- a workflow change that moves them makes
this gate REFUSE, never silently skip the check. Deliberately NOT
asserted, per verification-rules rule 8's stated-reason half:
`--job-id`, absent from both measured logs (a job's own stdout does not
carry its id); and `--published` / `--at-run`, plain JSON with no embedded
sha or run id, so their identity cannot be verified from content -- the
caller's fetch command is what pins them. The new check's own defeating
input is a log whose checkout output is forged (command and sha lines
crafted to name any sha asked about): no property of the file can
distinguish that from a real checkout's output, so the gate defends
against accident -- a stale or foreign log, a dependency's commit mistaken
for the head -- and the caller's fetch command (the job's log fetched by
URL) is what binds the file, the same stated reason given for
`--published` / `--at-run` above.

The six evidence lines this gate was first replayed against are pinned in
`tools/test_check_nightly_slow_records.py`, so the positive case survives
Actions' log retention.

Exit: 0 every node corroborated AND every requested control turned red,
      1 a node fails / the old rule rejects; in control mode, every control
        was caught but the positive verdict itself failed,
      3 REFUSED -- the apparatus cannot answer: required evidence missing or
        unusable, or the log does not name the run it is claimed to be from
        (a gate that answers anyway would look exactly like data),
      4 a requested control did not turn the verdict red (a control on a
        node the baseline already fails is uncaught too -- it cannot be
        credited),
      2 usage / unreadable input.

In control mode the exit is the worse of the two answers
(4 uncaught control > 1 verdict FAIL > 0), so a printed FAIL is never
returned as 0 (#1066): the verdict's own result is printed and kept, not
dropped in favour of the controls' result.

Controls (--control name=node, repeatable) mutate the evidence the way
#1058's test plan requires -- withhold the node's line, substitute a line
from another run, corrupt the anchor, falsify the stored verdict -- and each
must turn that node red. A gate whose controls pass is itself a defect. A
control on a node the baseline already fails is never credited: it writes
the value the node already has, so "caught" there demonstrates nothing
(#1066 review) -- the control is uncaught and the gate exits 4.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

import refusal

#: The nodes nightly.yml records with `compile_dag.py --run --slow`; every
#: reader of this gate reads the same six from docs/dag.json's `slow: true`.
SLOW_NODES = ("F1", "F2", "M1", "D1", "I1", "I2")

#: `<ts>  keep  F1  <detail> (unchanged since 91398a0c)` -- the recorder's
#: quiet-night line (compile_dag.py's keep branch). The detail is the OLD
#: record's stored detail printed verbatim, so byte equality with the
#: record's `detail` is the right test; the anchor is `sha[:8]`.
KEEP_RE = re.compile(
    r"keep\s+(\S+)\s+(.*?)\s+\(unchanged since ([0-9a-f]{8})\)\s*$")

#: `<ts>  PASS  F1  <detail>` -- the recorder's rewritten line
#: (`{mark:6s} {i:3s} {detail}`). Only a PASS proves the run executed the
#: verifier; FAIL/REFUSE lines are captured so a rewritten record carrying a
#: FAIL line fails with a reason that names it.
FRESH_RE = re.compile(r"(?:^|\s)(PASS|FAIL|REFUSE)\s+(\S+)\s+(.+?)\s*$")

FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

#: The checkout step reports the repository head by running one of these
#: commands (`[command]/usr/bin/git ...`, Actions' command echo) and
#: printing its output -- the bare sha -- on the NEXT line (measured on run
#: 44344077192's job log, lines 268-269 and 280-281, #1070). `--head-sha`
#: is asserted against that output line only: the same log names each
#: dependency action's commit on its `Download action repository` lines,
#: and one of those satisfied the previous any-line substring check
#: (#1070).
REPO_HEAD_COMMANDS = (
    "git rev-parse refs/remotes/origin/main",
    "git log -1 --format=%H",
)

#: Actions prefix each log line with an ISO-8601 timestamp; the checkout
#: output line is the bare sha after it.
LOG_TS_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z\s")

#: upload-artifact's own download-URL record: the COMPLETE line after the
#: log timestamp is `Artifact download URL: <url>` -- label, single space,
#: one URL token ending in `/actions/runs/<id>/artifacts/<n>`, end of line
#: -- the only shape a job log may name its own run from, and the run field
#: `--run-id` is asserted against WHOLE, not as a substring: the artifact
#: id (14445449921) and a single space each appear in run 44344077192's
#: log and passed an `in` check (#1066 review); a diagnostic naming
#: another run's artifact path ("Previous baseline path: /actions/runs/
#: <other>/artifacts/<n>", measured on the same log) attributed that
#: foreign run while every URL-bearing line was scanned; and a quoted
#: warning embedding the marker mid-line ("##[warning]Previous nightly
#: reported: Artifact download URL: <foreign URL>") or free text wedged
#: between label and URL ("Artifact download URL: unavailable. Previous
#: nightly: <foreign URL>") each fed the extractor's wildcard and gave the
#: foreign run 6/6 (#1066 review round 2) -- so the record is anchored at
#: the line start (only a timestamp may precede the label) and the URL
#: must follow the label immediately.
#:
#: Stated residual (rule 8, #1073): the URL prefix is `\S*`, so an
#: otherwise-exact record line whose URL is absolute on a foreign host
#: (`https://evil.example/actions/runs/<id>/artifacts/<n>`), host-less
#: (a bare path) or glued to a non-space prefix still attributes its run
#: id. Not observed in the reviewed logs (runs 44344077192 and
#: 44128504549) -- not shown unreachable, and a clean log cannot show
#: that: a line that exactly imitates the record, from a step that can
#: write the job log, is the forged-checkout class in the module
#: docstring, told apart by no property of the file. No host or scheme
#: is pinned: the genuine host is github.com today, and pinning it (or
#: requiring `https?://`) would turn GHES and any future host change
#: into false refusals -- a real refusal traded for a speculative false
#: pass, and the scheme requirement would still accept the foreign-host
#: line above. Defence: the caller's fetch command (the job's log
#: fetched by URL) is what binds the file, and the two-distinct-ids
#: refusal below fires when the genuine record is also present -- a
#: false attribution additionally requires the genuine upload record to
#: be absent from the fetched log. Pinned where a later tightening
#: deliberately flips it:
#: test_foreign_host_record_line_still_attributes.
ARTIFACT_RUN_ID_RE = re.compile(
    r"^(?:\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z )?"
    r"Artifact download URL: \S*/actions/runs/(\d+)/artifacts/\d+\s*$")

EXIT_OK, EXIT_FAIL, EXIT_REFUSED, EXIT_CONTROL = 0, 1, 3, 4


class Refused(refusal.Refused, RuntimeError):
    """The apparatus cannot answer; say what is missing, never guess."""


def parse_log(lines: list[str]) -> tuple[dict, dict]:
    """Recording-step evidence lines, keyed by node.

    Keep lines are consumed first: a keep line's detail may itself contain
    `PASS -- ` (every ladder/modal/drum/integration detail does), which the
    fresh-line pattern would otherwise misread as a rewritten verdict.
    """
    keeps: dict[str, list[tuple[str, str]]] = {}
    fresh: dict[str, list[tuple[str, str]]] = {}
    for line in lines:
        m = KEEP_RE.search(line)
        if m:
            keeps.setdefault(m.group(1), []).append((m.group(2), m.group(3)))
            continue
        m = FRESH_RE.search(line)
        if m and m.group(2) in SLOW_NODES:
            fresh.setdefault(m.group(2), []).append((m.group(1), m.group(3)))
    return keeps, fresh


def repo_head_evidence(lines: list[str]) -> list[tuple[int, str]]:
    """What the checkout step itself reported as the repository head.

    Returns (1-based line number, reported output) for each line that
    follows a checkout-step head command (`REPO_HEAD_COMMANDS`), timestamp
    stripped. The evidence for `--head-sha` is THESE lines only -- never
    "the sha anywhere in the log": the log also names dependency actions'
    commits, which are not this repository's head (#1070).
    """
    evidence: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        if not any(cmd in line for cmd in REPO_HEAD_COMMANDS):
            continue
        if i + 1 >= len(lines):
            continue
        out = LOG_TS_RE.sub("", lines[i + 1], count=1).strip()
        evidence.append((i + 2, out))
    return evidence


def load_json(path: pathlib.Path, what: str) -> dict:
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        raise Refused(f"{what} not found: {path}") from None
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise Refused(f"{what} is not readable JSON: {path}: {exc}") from None
    if not isinstance(data, dict) or not data:
        raise Refused(f"{what} is not a non-empty JSON object: {path}")
    return data


def check_node(node: str, rec: dict | None, at_run_rec, keeps, fresh,
               head_sha: str) -> tuple[bool, str]:
    """The amended per-node rule (#1058). (True, how) or (False, why)."""
    if not isinstance(rec, dict):
        return False, "no published record"
    if rec.get("passed") is not True:
        return False, (f"published record is not passed==true "
                       f"(passed={rec.get('passed')!r})")
    if rec.get("sha") == head_sha:
        # Rewritten tonight: the run's own execution must be in the log.
        if any(v == "PASS" and d == rec.get("detail")
               for v, d in fresh.get(node, [])):
            return True, "rewritten: sha == headSha with the node's PASS line"
        got = fresh.get(node)
        if got:
            return False, (f"record carries the run's headSha but the log's "
                           f"line(s) disagree: {got!r}")
        return False, ("record carries the run's headSha but the log carries "
                       "no PASS line for the node -- a green step cannot "
                       "supply a missing row, the recording command runs "
                       "`|| true`")
    # Retained (#967 quiet night): the run re-executed and kept the old
    # record, and the log line names the anchor and the detail.
    detail = rec.get("detail")
    anchor = rec.get("sha", "")
    if not isinstance(anchor, str) or len(anchor) < 8:
        return False, "record carries no usable sha anchor"
    matches = [(d, a) for d, a in keeps.get(node, [])
               if d == detail and a == anchor[:8]]
    if not matches:
        return False, ("no keep line for the node with matching detail and "
                       f"anchor {anchor[:8]} (have: {keeps.get(node)!r})")
    if at_run_rec != rec:
        return False, ("keep line corroborates but the published record "
                       "differs from the record at the run checkout")
    return True, ("retained: keep line matches anchor and detail; record "
                  "identical at the run checkout")


def evaluate(published: dict, at_run: dict, keeps: dict, fresh: dict,
             head_sha: str) -> dict[str, tuple[bool, str]]:
    return {n: check_node(n, published.get(n), at_run.get(n),
                          keeps, fresh, head_sha)
            for n in SLOW_NODES}


def old_rule(published: dict, head_sha: str) -> tuple[bool, int]:
    """#820's original predicate, kept so the replay can show it reject."""
    ok = [n for n in SLOW_NODES
          if isinstance(published.get(n), dict)
          and published[n].get("passed") is True
          and published[n].get("sha") == head_sha]
    return len(ok) == len(SLOW_NODES), len(ok)


# --- controls (#1058 test plan): each must turn its node red -----------------

def control_withhold(node, keeps, fresh, published, at_run):
    keeps.pop(node, None)
    fresh.pop(node, None)


def control_foreign(node, keeps, fresh, published, at_run):
    """Substitute ANOTHER SLOW node's keep line from the SAME night, renamed
    to `node` -- a line this node never printed. On a quiet night every slow
    node shares the anchor (91398a0c), so only the detail is foreign here;
    the foreign-anchor pathology is covered directly by
    test_substituted_line_from_another_run_fails_directly (F3's real
    4fe488f1)."""
    other = next((lines for n, lines in sorted(keeps.items()) if n != node),
                 None)
    if other:
        detail, anchor = other[0]
    else:
        detail, anchor = "substituted evidence line", "0" * 8
    keeps[node] = [(detail, anchor)]
    fresh.pop(node, None)


def control_wrong_anchor(node, keeps, fresh, published, at_run):
    """Keep the node's own detail but corrupt the anchor to c03c64e1 -- a
    real pre-#818 anchor that is not this record's."""
    if node not in keeps:
        raise Refused(f"control wrong-anchor={node}: the node has no keep "
                      "line to corrupt (rewritten night); replay it on a "
                      "quiet-night fixture")
    keeps[node] = [(d, "c03c64e1") for d, _a in keeps[node]]


def control_false_verdict(node, keeps, fresh, published, at_run):
    if not isinstance(published.get(node), dict):
        raise Refused(f"control false-verdict={node}: no published record "
                      "to falsify")
    published[node]["passed"] = False


CONTROLS = {
    "withhold": control_withhold,
    "foreign": control_foreign,
    "wrong-anchor": control_wrong_anchor,
    "false-verdict": control_false_verdict,
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--published", required=True, type=pathlib.Path,
                    help="records read from main: origin/main's tip on a "
                         "no-op night, the attributable [dag] records commit "
                         "on a push night")
    ap.add_argument("--at-run", required=True, type=pathlib.Path, dest="at_run",
                    help="records at the run's own checkout "
                         "(git show <headSha>:docs/dag-results.json)")
    ap.add_argument("--log", required=True, type=pathlib.Path,
                    help="the full-rtl job log (the recording step's lines)")
    ap.add_argument("--head-sha", required=True, dest="head_sha",
                    help="the run's full 40-hex headSha; asserted to equal "
                         "what the checkout step itself reports (the bare "
                         "sha line after its `git rev-parse "
                         "refs/remotes/origin/main` / `git log -1 "
                         "--format=%H` command), not merely to appear in "
                         "the log -- a dependency action's commit appears "
                         "too (#1070)")
    ap.add_argument("--run-id", required=True, dest="run_id",
                    help="the run's id; asserted to EQUAL the complete "
                         "numeric run field of the URL in the log's "
                         "upload-artifact `Artifact download URL:` record "
                         "-- the COMPLETE line after the log timestamp "
                         "with the URL immediately after the label (not "
                         "merely to appear in it -- the artifact id "
                         "and a single space each pass a substring check, "
                         "and a diagnostic line naming another artifact "
                         "path, a quoted warning embedding the marker, or "
                         "text wedged between marker and URL is not the "
                         "record) and echoed in the "
                         "summary -- required because at the same head "
                         "sha (a quiet night) it is the only anchor that "
                         "discriminates two runs (#1066)")
    ap.add_argument("--job-id", default="", dest="job_id",
                    help="echoed in the summary; deliberately NOT asserted -- "
                         "a job's own log does not carry its id (measured on "
                         "runs 44344077192 and 44128504549)")
    ap.add_argument("--old-rule", action="store_true",
                    help="evaluate #820's original sha-equality predicate "
                         "instead of the amended rule (replay evidence)")
    ap.add_argument("--control", action="append", default=[], dest="controls",
                    metavar="NAME=NODE",
                    help="apply a control and require it to turn the node "
                         "red: withhold=, foreign=, wrong-anchor=, "
                         "false-verdict=")
    args = ap.parse_args(argv)

    run_label = f"run {args.run_id}"
    job_label = f" job {args.job_id}" if args.job_id else ""

    try:
        if not FULL_SHA_RE.match(args.head_sha or ""):
            raise Refused(f"--head-sha must be a full 40-hex sha, got "
                          f"{args.head_sha!r}")
        if not args.run_id:
            # `required=True` alone still admits `--run-id ''`, which would
            # skip the log assertion through the anchor loop's truthiness
            # guard -- the defeating input walks back in through a side door
            # (verification-rules rule 8).
            raise Refused("--run-id must be a non-empty run id -- at the "
                          "same head sha (a quiet night) it is the only "
                          "anchor that discriminates two runs")
        published = load_json(args.published, "published records")
        at_run = load_json(args.at_run, "records at the run checkout")
        try:
            log_lines = args.log.read_text(errors="replace").splitlines()
        except FileNotFoundError:
            raise Refused(f"log not found: {args.log}") from None
        if not log_lines:
            raise Refused(f"log is empty: {args.log}")
        # The head sha is asserted against the checkout step's own output
        # (repo_head_evidence), never "anywhere in the log": run
        # 44344077192's log names actions/checkout's own commit on its
        # `Download action repository` line and that dependency sha passed
        # the old any-line check (#1070).
        evidence = repo_head_evidence(log_lines)
        if not evidence:
            raise Refused(
                "the log carries no repository-checkout output -- no `git "
                "rev-parse refs/remotes/origin/main` or `git log -1 "
                "--format=%H` command line followed by its bare-sha output "
                f"-- so the run's head sha ({args.head_sha}) cannot be "
                f"attributed to {run_label}{job_label} (head "
                f"{args.head_sha[:8]}); a sha appearing elsewhere in the "
                "log (a dependency action's commit, a stray mention) is "
                "not identity evidence (#1070)")
        wrong = [(n, out) for n, out in evidence if out != args.head_sha]
        if wrong:
            raise Refused(
                "the log's repository-checkout output does not report the "
                f"run's head sha: asked for {args.head_sha}, the checkout "
                f"step printed {wrong} -- a checkout of another commit "
                "(the previous night's log, another repository) cannot be "
                f"attributed to {run_label}{job_label} (head "
                f"{args.head_sha[:8]}) (#1070)")
        url_run_ids = sorted({m.group(1) for line in log_lines
                              for m in ARTIFACT_RUN_ID_RE.finditer(line)})
        if not url_run_ids:
            raise Refused(
                "the log carries no upload-artifact `Artifact download "
                "URL:` record naming the run id (no COMPLETE record line "
                "`<timestamp> Artifact download URL: <url ending in "
                "/actions/runs/<run id>/artifacts/<artifact>>`: a "
                "diagnostic, a quoted warning embedding the marker, or "
                "text between the marker and the URL is not the record) "
                "-- "
                "its execution evidence cannot be attributed to "
                f"{run_label}{job_label} (head {args.head_sha[:8]}); a "
                "stale or failed fetch leaves the previous night's log "
                "at the path, so re-fetch the right log rather than "
                "attribute this one")
        if len(url_run_ids) > 1:
            raise Refused(
                "the log carries upload-artifact download-URL records "
                "naming "
                f"{len(url_run_ids)} distinct runs "
                f"({', '.join(url_run_ids)}) -- ambiguous execution "
                f"evidence for {run_label}{job_label}; re-fetch the single "
                "run's log rather than attribute this one")
        if url_run_ids[0] != args.run_id:
            raise Refused(
                f"the log's `Artifact download URL:` record names run "
                f"{url_run_ids[0]}, not the supplied run id "
                f"{args.run_id!r} -- the comparison is against the "
                "complete numeric run field of the URL, exact and whole: "
                "the artifact id and a single space each "
                "appear in a real log and passed a substring check "
                "(measured on run 44344077192's log), so a partial match "
                "attributes nothing")
        keeps, fresh = parse_log(log_lines)
        if not fresh and not any(node in SLOW_NODES for node in keeps):
            raise Refused(
                "the log carries no recording-step lines (no `keep`, PASS, "
                "FAIL or REFUSE row naming a slow node) -- if this is not "
                "the full-rtl job's log, the execution evidence cannot be "
                "obtained from it; record REFUSED and name the log you have")

        if args.old_rule:
            if args.controls:
                ap.error("--old-rule takes no --control")
            ok, count = old_rule(published, args.head_sha)
            print(f"old rule (every sha == headSha): {count}/6 satisfy it "
                  f"--> {'PASS' if ok else 'FAIL'} ({run_label}"
                  f"{job_label}, head {args.head_sha[:8]})")
            return EXIT_OK if ok else EXIT_FAIL

        verdicts = evaluate(published, at_run, keeps, fresh, args.head_sha)
        width = max(len(n) for n in SLOW_NODES)
        for node in SLOW_NODES:
            ok, why = verdicts[node]
            print(f"  {'PASS' if ok else 'FAIL':4s} {node:{width}s}  {why}")
        npass = sum(1 for ok, _ in verdicts.values() if ok)
        print(f"(a) {npass}/6 slow nodes corroborated by {run_label}"
              f"{job_label} (head {args.head_sha[:8]}) --> "
              f"{'PASS' if npass == len(SLOW_NODES) else 'FAIL'}")

        if not args.controls:
            return EXIT_OK if npass == len(SLOW_NODES) else EXIT_FAIL

        # Control mode: every requested control must turn its node red FROM
        # A PASSING BASELINE, and the positive verdict keeps its own exit --
        # a printed FAIL must never be returned as 0 (#1066). A control on
        # a node the baseline already fails is not credited: it writes the
        # value the node already has, so "caught" there demonstrates
        # nothing (#1066 review). The worse of the two answers wins:
        # 4 (a control went uncaught) > 1 (verdict FAIL) > 0.
        controls_exit = EXIT_OK
        for spec in args.controls:
            name, _, node = spec.partition("=")
            if name not in CONTROLS:
                ap.error(f"unknown control {name!r}; known: "
                         f"{', '.join(sorted(CONTROLS))}")
            if node not in SLOW_NODES:
                ap.error(f"control node {node!r} is not one of "
                         f"{','.join(SLOW_NODES)}")
            keeps2 = {n: list(v) for n, v in keeps.items()}
            fresh2 = {n: list(v) for n, v in fresh.items()}
            pub2 = {n: dict(v) if isinstance(v, dict) else v
                    for n, v in published.items()}
            CONTROLS[name](node, keeps2, fresh2, pub2, at_run)
            v2 = evaluate(pub2, at_run, keeps2, fresh2, args.head_sha)
            caught = verdicts[node][0] and not v2[node][0]
            if caught:
                print(f"  CONTROL {spec}: caught (node fails: {v2[node][1]})")
            elif not verdicts[node][0]:
                print(f"  CONTROL {spec}: NOT CAUGHT -- {node} was already "
                      f"failing before the control ({verdicts[node][1]}); "
                      "a control that writes the value the node already "
                      "has demonstrates nothing")
                controls_exit = EXIT_CONTROL
            else:
                print(f"  CONTROL {spec}: NOT CAUGHT -- {v2[node][1]}")
                controls_exit = EXIT_CONTROL
        positive_exit = EXIT_OK if npass == len(SLOW_NODES) else EXIT_FAIL
        exit_code = (EXIT_CONTROL if controls_exit != EXIT_OK
                     else positive_exit)
        if controls_exit == EXIT_OK:
            print("all requested controls turned their node red")
        return exit_code
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main())
