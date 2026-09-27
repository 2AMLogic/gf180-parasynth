#!/usr/bin/env python3
"""Assert that every decision record in `spec/decision-records/` owns its
number: no two files share a leading `NNNN`, and each file's own `# NNNN:`
header agrees with its filename.

WHY THIS EXISTS. Issue #250. Two independent PRs each allocated **0017** within
two minutes of each other, on branches that had never seen one another:

    spec/decision-records/0017-blamp-on-the-shark-tooths-corner.md        (#244)
    spec/decision-records/0017-metric-direction-is-part-of-its-definition.md (#241)

Nothing caught it, and nothing *could* have: the filenames differ, so every
merge was clean (`UNSTABLE`, not `DIRTY`), and no tool enumerated the directory.
The collision was predicted in the issue body on the 26th and then happened
again the same day, one PR later, exactly as predicted -- a number allocated
against a stale `origin/main` is invisible to both branches and to both
reviewers. It was fixed by hand and by hand it would come back, because the
defect is in the *allocation procedure*, not in anyone's attention: the next
free number cannot be read off your own branch.

So it is read off the directory, mechanically, by a check that runs on every
`make verify`. `0017-metric-direction-...` was renumbered to `0021-...` under
this issue (0020 had been taken by a PR that merged between the issue being
curated and the fix being written -- the drift is not hypothetical either).

WHAT IS CHECKED. Two properties, because a DR number is a name and a name has
to resolve to exactly one thing:

  1. **uniqueness** -- no two files share a leading four-digit number. This is
     the defect the issue was filed about.
  2. **self-agreement** -- the number in a file's first heading is the number in
     its filename. A renumbering is a rename *plus* a header edit, and a rename
     that forgot its header leaves two different answers to "which DR is this?"
     in one file: `git grep "DR 0017"` and `ls` would disagree. Cheap to check
     and it is the exact half of the fix a hurried renumber drops.

Both spellings this repository actually uses are accepted -- `# 0014: title` and
`# DR 0014 -- title` -- because a checker that fails on the prose style rather
than the number is a checker people route around.

Gaps are NOT an error. A number may be skipped (0020 was, in effect, consumed
by a PR in flight) and demanding a dense sequence would be a gate that goes red
for a reason nobody can fix.

THREE OUTCOMES, following this repository's convention
(`tools/check_doc_claims.py`, `pnr/orfs/area_provenance.py`) -- the third is the
point:

    exit 0  OK           every record owns its number
    exit 1  COLLISION    two files share a leading number
            MISNUMBERED  a file's header number is not its filename's
    exit 2  REFUSED      nothing was checked, and no verdict will be given:
                           `no-directory`   the directory is missing
                           `no-records`     zero numbered records were found
                           `unparsed-file`  a `.md` file whose number cannot be
                                            read, so the uniqueness claim would
                                            be silently partial
                           `no-heading`     a record with no `# NNNN` heading

REFUSED is not a pass. A checker that enumerates an empty directory, finds no
duplicates and reports OK is the instrument this repository keeps having to
un-build -- see `docs/verification-rules.md` rule 5's third condition.

THE CONTROLS. `--inject` stages a COPY of the real directory into a temporary
tree, applies one defect, and runs THIS SCRIPT as a subprocess against the copy,
requiring the verdict `--expect` names. The shipped tree is never touched.

    tools/check_decision_record_numbers.py
    tools/check_decision_record_numbers.py --inject DUPLICATE_NUMBER --expect collision
    tools/check_decision_record_numbers.py --inject HEADER_MISMATCH  --expect misnumbered
    tools/check_decision_record_numbers.py --inject UNNUMBERED_FILE  --expect refused
    tools/check_decision_record_numbers.py --inject EMPTY_DIRECTORY  --expect refused

`--expect` names the *reason*, not merely that something went wrong: without it
a crashing script would look like a control that fired, which is how this
repository once counted a traceback as a caught defect. `DUPLICATE_NUMBER`
reinstates issue #250's own defect -- it re-creates the second `0017` -- per
rule 5, "a bug is not closed until it is an injection".
"""
from __future__ import annotations

import argparse
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
RECORDS = REPO / "spec" / "decision-records"

#: Files in the directory that are not numbered records. TEMPLATE.md carries the
#: placeholder `# 0000:` heading and is deliberately not a record.
NOT_A_RECORD = frozenset({"TEMPLATE.md", "README.md", "index.md"})

#: `0017-blamp-on-the-shark-tooths-corner.md` -> `0017`
FILENAME_RE = re.compile(r"^(\d{4})-.+\.md$")

#: `# 0014: title` and `# DR 0014 -- title` are both in use here.
HEADING_RE = re.compile(r"^#\s+(?:DR\s+)?(\d{4})\b")

OK_EXIT = 0
DEFECT_EXIT = 1
REFUSED_EXIT = 2

#: Exit 3 is reserved for "the control did not fire": distinct from a real
#: defect (1) so a broken control cannot masquerade as a working one.
CONTROL_UNMET_EXIT = 3


class Refused(Exception):
    """Nothing was checked. Carries the reason token, not just a message."""

    def __init__(self, reason: str, detail: str):
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


# --------------------------------------------------------------------- reading

def read_records(directory: pathlib.Path) -> dict[pathlib.Path, tuple[int, int]]:
    """Map each record file to `(filename number, heading number)`.

    REFUSES rather than skipping anything it cannot parse: a partial
    enumeration would make the uniqueness claim quietly narrower than it reads.
    """
    if not directory.is_dir():
        raise Refused("no-directory", f"{directory} is not a directory")

    records: dict[pathlib.Path, tuple[int, int]] = {}
    for path in sorted(directory.glob("*.md")):
        if path.name in NOT_A_RECORD:
            continue
        m = FILENAME_RE.match(path.name)
        if not m:
            raise Refused(
                "unparsed-file",
                f"{path.name} is not NNNN-<slug>.md and is not one of the "
                f"non-records {sorted(NOT_A_RECORD)} -- its number cannot be "
                f"read, so uniqueness cannot be asserted over this directory")
        first = path.read_text().lstrip().splitlines()[:1]
        heading = HEADING_RE.match(first[0]) if first else None
        if not heading:
            raise Refused(
                "no-heading",
                f"{path.name} has no `# NNNN:` (or `# DR NNNN --`) first "
                f"heading, so its self-declared number cannot be read")
        records[path] = (int(m.group(1)), int(heading.group(1)))

    if not records:
        raise Refused(
            "no-records",
            f"no numbered records found in {directory} -- a uniqueness check "
            f"over zero files always passes and proves nothing")
    return records


# --------------------------------------------------------------------- checking

def collisions(records: dict[pathlib.Path, tuple[int, int]]) -> dict[int, list[str]]:
    """Leading numbers owned by more than one file."""
    by_number: dict[int, list[str]] = {}
    for path, (number, _heading) in records.items():
        by_number.setdefault(number, []).append(path.name)
    return {n: sorted(names) for n, names in by_number.items() if len(names) > 1}


def misnumbered(records: dict[pathlib.Path, tuple[int, int]]) -> list[tuple[str, int, int]]:
    """Files whose heading number is not their filename number."""
    return [(path.name, number, heading)
            for path, (number, heading) in sorted(records.items())
            if number != heading]


def verdict(directory: pathlib.Path) -> tuple[int, str, list[str]]:
    """`(exit code, verdict word, report lines)` for one directory."""
    try:
        records = read_records(directory)
    except Refused as exc:
        return (REFUSED_EXIT, "REFUSED",
                [f"REFUSED  {exc.reason}: {exc.detail}",
                 "         nothing was checked: this is not a pass"])
    except OSError as exc:
        return (REFUSED_EXIT, "REFUSED", [f"REFUSED  unreadable: {exc}"])

    lines: list[str] = []
    dup = collisions(records)
    bad_heading = misnumbered(records)

    if dup:
        for number, names in sorted(dup.items()):
            lines.append(f"COLLISION  {number:04d} is allocated {len(names)} times:")
            lines.extend(f"           {name}" for name in names)
        lines.append("           renumber all but one to the next free number "
                     "(issue #250)")
    for name, number, heading in bad_heading:
        lines.append(f"MISNUMBERED  {name} is filed as {number:04d} but its "
                     f"heading says {heading:04d}")

    if dup:
        return DEFECT_EXIT, "COLLISION", lines
    if bad_heading:
        return DEFECT_EXIT, "MISNUMBERED", lines
    numbers = sorted(n for n, _ in records.values())
    lines.append(f"OK       {len(records)} decision records, "
                 f"{numbers[0]:04d}-{numbers[-1]:04d}, every number allocated "
                 f"once and matching its own heading")
    return OK_EXIT, "OK", lines


# --------------------------------------------------------------------- controls

def _stage(directory: pathlib.Path, dest: pathlib.Path) -> pathlib.Path:
    shutil.copytree(directory, dest)
    return dest


def inject_duplicate_number(staged: pathlib.Path) -> str:
    """Issue #250's own defect: a second file under an already-used number."""
    victim = sorted(staged.glob("[0-9][0-9][0-9][0-9]-*.md"))[0]
    number = victim.name[:4]
    clone = staged / f"{number}-a-concurrent-branch-allocated-this-too.md"
    clone.write_text(f"# {number}: a decision record from another branch\n")
    return f"added {clone.name} beside {victim.name}"


def inject_header_mismatch(staged: pathlib.Path) -> str:
    """A rename that forgot its header -- the other half of a renumber."""
    victim = sorted(staged.glob("[0-9][0-9][0-9][0-9]-*.md"))[-1]
    text = victim.read_text()
    first, rest = text.split("\n", 1)
    victim.write_text(HEADING_RE.sub("# 0099", first, count=1) + "\n" + rest)
    return f"{victim.name} now heads itself 0099"


def inject_unnumbered_file(staged: pathlib.Path) -> str:
    """A record whose number cannot be read: the checker must REFUSE, not skip
    it. A silent skip is how a uniqueness claim becomes partial."""
    stray = staged / "notes-on-numbering.md"
    stray.write_text("# Notes\n")
    return f"added {stray.name}, which has no leading number"


def inject_empty_directory(staged: pathlib.Path) -> str:
    """Zero records: a uniqueness check over nothing always passes."""
    for path in staged.glob("*.md"):
        path.unlink()
    return "removed every .md file"


INJECTIONS = {
    "DUPLICATE_NUMBER": inject_duplicate_number,
    "HEADER_MISMATCH": inject_header_mismatch,
    "UNNUMBERED_FILE": inject_unnumbered_file,
    "EMPTY_DIRECTORY": inject_empty_directory,
}

#: Which verdict each injection must produce. `--expect` is checked against
#: this too, so an injection cannot be paired with the wrong expectation.
EXPECTED = {
    "DUPLICATE_NUMBER": "collision",
    "HEADER_MISMATCH": "misnumbered",
    "UNNUMBERED_FILE": "refused",
    "EMPTY_DIRECTORY": "refused",
}

VERDICT_WORD = {"ok": "OK", "collision": "COLLISION",
                "misnumbered": "MISNUMBERED", "refused": "REFUSED"}


def run_control(directory: pathlib.Path, injection: str | None,
                expect: str | None) -> tuple[int, list[str]]:
    """Stage a copy, apply the injection, run THIS script against the copy as a
    subprocess, and require the expected verdict.

    A subprocess and not an in-process call: the control must exercise the
    thing that ships, exit code included.
    """
    lines: list[str] = []
    with tempfile.TemporaryDirectory(prefix="dr-numbers-control-") as tmp:
        staged = _stage(directory, pathlib.Path(tmp) / "decision-records")
        what = "the shipped directory, unmodified"
        if injection:
            what = INJECTIONS[injection](staged)
        proc = subprocess.run(
            [sys.executable, str(pathlib.Path(__file__).resolve()),
             "--dir", str(staged)],
            capture_output=True, text=True, check=False)
        lines.append(f"control {injection or 'clean'}: {what}")
        lines.extend(f"  | {ln}" for ln in proc.stdout.splitlines())
        if proc.stderr.strip():
            lines.extend(f"  ! {ln}" for ln in proc.stderr.splitlines())

    if expect is None:
        return proc.returncode, lines

    if injection and EXPECTED[injection] != expect:
        lines.append(f"  --expect {expect}: NO VERDICT: {injection} is specified "
                     f"to produce {EXPECTED[injection]!r}")
        return REFUSED_EXIT, lines

    want_word = VERDICT_WORD[expect]
    want_code = {"ok": OK_EXIT, "refused": REFUSED_EXIT}.get(expect, DEFECT_EXIT)
    if proc.returncode != want_code:
        lines.append(f"  --expect {expect}: NOT MET: exited {proc.returncode}, "
                     f"expected {want_code}")
        return CONTROL_UNMET_EXIT, lines
    if want_word not in proc.stdout:
        lines.append(f"  --expect {expect}: NO VERDICT: exit {proc.returncode} "
                     f"with no {want_word} in the output -- that is not this "
                     f"verdict")
        return REFUSED_EXIT, lines
    lines.append(f"  --expect {expect}: met: {want_word}, exit {proc.returncode}")
    return OK_EXIT, lines


# ------------------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", type=pathlib.Path, default=RECORDS,
                    help="the decision-record directory to check")
    ap.add_argument("--inject", choices=sorted(INJECTIONS),
                    help="stage a copy with this defect and check that copy")
    ap.add_argument("--expect", choices=sorted(VERDICT_WORD),
                    help="require this verdict from the staged copy")
    args = ap.parse_args(argv)

    if args.inject or args.expect:
        code, lines = run_control(args.dir, args.inject, args.expect)
        print("\n".join(lines))
        return code

    code, _word, lines = verdict(args.dir)
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main())
