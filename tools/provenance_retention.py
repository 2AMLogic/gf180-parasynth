#!/usr/bin/env python3
"""Retention classes for the records `tools/manifest.py` retains, and the
grouping step that lets CI upload each class on its own clock.

    tools/provenance_retention.py --root build/provenance --out build/provenance-upload

WHY THIS IS A SCRIPT AND NOT THREE `path:` GLOBS IN THE WORKFLOW FILE
---------------------------------------------------------------------
Retention is a property of the RECORD, and the record is the only thing that
knows it. To `actions/upload-artifact`, a reference fixture and a per-push smoke
render are both "artifacts"; the failure this exists to prevent is **the audio a
bound was derived from expiring on the same seven-day clock as a render nobody
will re-read.** Once that WAV is gone the bound can only be re-trusted, never
re-derived, which is the whole thing issue #68 is about. Putting the number in
the YAML puts it two files away from the thing it describes, so the number lives
here and `test_provenance_retention.py` asserts the workflow agrees with it.

    <out>/<class>/runs/<render id>/...   the render records of that class
    <out>/<class>/jobs/<job id>/...      the analyse/accept records of that class
    <out>/<class>/RETENTION.txt          the days, and why the class exists
    <out>/summary.json                   what was grouped, for the log

REFUSES (exit 2) rather than uploading a guess. `REFUSED` is a first-class
outcome here (CLAUDE.md), because **an artifact bundle that silently dropped the
one record somebody will want is indistinguishable from a complete one.** The
four refusals:

  * a store with no `runs/` and no `jobs/` at all -- a run that produced no
    record and an upload that dropped every record look the same from here;
  * a record directory carrying no retention mark -- it would be grouped into
    no class, i.e. silently not uploaded;
  * a mark naming a class `RETENTION_DAYS` does not declare -- the days for it
    would have to be invented;
  * a mark that is not readable JSON, or whose recorded `days` disagrees with
    `RETENTION_DAYS`, which means the record was marked by a different version
    of this table than the one about to upload it.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# The single source of the numbers. `.github/workflows/provenance.yml` must
# agree with this table, and `test_provenance_retention.py` reads the YAML and
# asserts it does -- otherwise "the number lives in one place" is a comment.
RETENTION_DAYS = {
    "reference-fixture": 90,
    "release-evidence": 90,
    "smoke": 7,
}

WHY = {
    "reference-fixture": "the audio and traces a BOUND was derived from. If this "
                         "expires the bound can only be re-trusted, not re-derived, "
                         "which is the failure issue #68 exists to prevent.",
    "release-evidence": "what a claim in docs/ rests on. A document citing a number "
                        "whose evidence has expired is a claim, not evidence.",
    "smoke": "a per-push render nobody will re-read. Short on purpose: keeping "
             "these as long as a fixture is how a retention budget gets spent on "
             "the records that do not matter.",
}

# One file per record directory. A plain name rather than a dotfile because
# `actions/upload-artifact` and most archive tooling skip dotfiles by default,
# and a retention mark that does not survive the upload is worse than none.
MARK_NAME = "retention.json"


class Refused(Exception):
    """A precondition of the grouping step was not met. Distinct from "nothing
    to upload": this is "something is here and it cannot be placed honestly"."""


def mark(record_dir, retention_class: str) -> pathlib.Path:
    """Record which retention class one `runs/<id>/` or `jobs/<id>/` directory
    belongs to, at the point the record is written -- not later, from a path
    pattern. REFUSES a class this table does not declare rather than inventing
    a number of days for it."""
    if retention_class not in RETENTION_DAYS:
        raise Refused(
            f"{retention_class!r} is not a declared retention class "
            f"({', '.join(sorted(RETENTION_DAYS))}) -- refusing to mark a record "
            f"with a class whose retention nobody has stated")
    record_dir = pathlib.Path(record_dir)
    record_dir.mkdir(parents=True, exist_ok=True)
    p = record_dir / MARK_NAME
    p.write_text(json.dumps({"retention_class": retention_class,
                             "retention_days": RETENTION_DAYS[retention_class],
                             "why": WHY[retention_class],
                             "source": "tools/provenance_retention.py "
                                       "RETENTION_DAYS -- change it there, not in "
                                       "the workflow file"}, indent=1) + "\n")
    return p


def read_mark(record_dir) -> str | None:
    """The retention class of one record directory, or `None` if it carries no
    mark. `None` is deliberately not defaulted to `"smoke"` here: the caller
    that wants a default can say so, and `group()` treats an unmarked record as
    a refusal rather than quietly giving it the shortest clock."""
    p = pathlib.Path(record_dir) / MARK_NAME
    if not p.exists():
        return None
    try:
        rec = json.loads(p.read_text())
    except json.JSONDecodeError as e:
        raise Refused(f"{p} is not readable JSON ({e}) -- a record whose retention "
                       f"mark cannot be read cannot be uploaded on the right clock")
    cls = rec.get("retention_class")
    if cls not in RETENTION_DAYS:
        raise Refused(f"{p} names retention class {cls!r}, which is not one of "
                       f"{sorted(RETENTION_DAYS)}")
    if rec.get("retention_days") != RETENTION_DAYS[cls]:
        raise Refused(
            f"{p} records {rec.get('retention_days')!r} days for class {cls!r} and "
            f"this table says {RETENTION_DAYS[cls]} -- the record was marked by a "
            f"different version of RETENTION_DAYS than the one about to upload it")
    return cls


def _records(root: pathlib.Path):
    """Every record directory in the store, as `(subdir, id, path)`. `runs/`
    holds renders and `jobs/` holds analyses and their verdicts, which is
    `tools/manifest.py`'s own layout and issue #68's Scope note verbatim."""
    for sub in ("runs", "jobs"):
        d = root / sub
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            if p.is_dir():
                yield sub, p.name, p


def group(root, out) -> dict:
    """Copy each record under `<out>/<class>/<runs|jobs>/<id>/` and write each
    class's `RETENTION.txt`. Returns the summary; `refused` is set (and no
    partial bundle is claimed to be complete) if any record could not be
    placed."""
    root, out = pathlib.Path(root), pathlib.Path(out)
    records = list(_records(root))
    if not records:
        return {"root": str(root), "out": str(out), "classes": {}, "problems": [],
                "refused": f"no runs/ or jobs/ records under {root} -- a run that "
                           f"produced no record and an upload that dropped them "
                           f"all look the same from here"}
    problems, placed = [], {}
    for sub, ident, src in records:
        try:
            cls = read_mark(src)
        except Refused as e:
            problems.append(str(e))
            continue
        if cls is None:
            problems.append(
                f"{sub}/{ident} carries no {MARK_NAME}: it belongs to no retention "
                f"class, so it would be uploaded on no clock at all. Mark it at the "
                f"point the record is written (provenance_retention.mark)")
            continue
        dst = out / cls / sub / ident
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        placed.setdefault(cls, []).append(ident)
    for cls, ids in sorted(placed.items()):
        (out / cls / "RETENTION.txt").write_text(
            f"retention class: {cls}\n"
            f"retention days:  {RETENTION_DAYS[cls]}\n"
            f"why:             {WHY[cls]}\n"
            f"records:         {len(ids)}\n"
            f"source:          tools/provenance_retention.py RETENTION_DAYS -- "
            f"change it there, not in the workflow file\n")
    summary = {
        "root": str(root), "out": str(out),
        "records_seen": len(records),
        "classes": {c: {"records": len(ids), "retention_days": RETENTION_DAYS[c],
                        "why": WHY[c], "ids": sorted(ids)}
                    for c, ids in sorted(placed.items())},
        "problems": problems,
    }
    if problems:
        summary["refused"] = (
            f"{len(problems)} of {len(records)} record(s) could not be placed, so "
            f"the bundle would be silently incomplete -- which is exactly the state "
            f"this step exists to make visible")
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=str(ROOT / "build" / "provenance"),
                    help="the provenance store: <root>/runs/ and <root>/jobs/")
    ap.add_argument("--out", default=str(ROOT / "build" / "provenance-upload"),
                    help="where the per-class groups are written for upload")
    ap.add_argument("--classes", action="store_true",
                    help="print the retention table and exit")
    a = ap.parse_args(argv)
    if a.classes:
        for cls, days in sorted(RETENTION_DAYS.items()):
            print(f"  {cls:<20} {days:>3} days   {WHY[cls]}")
        return 0
    s = group(a.root, a.out)
    print(json.dumps(s, indent=1))
    if s.get("refused"):
        print(f"REFUSED: {s['refused']}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
