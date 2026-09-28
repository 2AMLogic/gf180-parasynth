#!/usr/bin/env python3
"""Does the Arty wrapper's bound digital proof still cover the tree?

    python3 tools/check_arty_evidence_binding.py          # 0 bound, 1 stale, 2 refused
    python3 tools/check_arty_evidence_binding.py --scope publication

fpga/publish_arty.py binds one committed verification record per wrapper
(VERIFICATION_BY_WRAPPER) and fpga/build_arty.py hash-checks it against the
LIVE compiled source set. That binding is correct and it is deliberately
strict -- but until this script existed, the only thing that reported it
breaking was `make verify`'s broad pytest job, 39 minutes in, as 23 failures
across three files whose common cause was one sentence:

    ValueError: verification source differs: rtl-sketch/voice_dp.v

This asks the same question in about a second, and names the file.

WHAT IT IS NOT. It is not a check that the evidence is *right* -- the bench
(fpga/verify_uart_bridge.py) is what establishes that, and re-running it is
the only way to refresh a stale binding. This only answers "does the bound
record cover these bytes", which is a precondition of the bench result
meaning anything about this tree.

AND IT DELIBERATELY IGNORES THE HISTORICAL RECORDS. Nearly every committed
record under fpga/reports names a voice_dp.v that is no longer the tree's,
and that is the correct, intended state: reports/arty/clean is the pre-uart
SPI run, reports/arty/uart-clean the pre-drift UART run the published
bitstream cites by hash, reports/selected/* the Lattice builds. Those are
history and must not be rewritten. Exactly one record is required to be
current -- the bound one -- and conflating the two is how a real red gets
lost in nineteen expected ones. `--list-historical` prints the others as
data, never as a failure.

TWO SCOPES, AND THE DEFAULT ONE DOES NOT COVER THE CONSTRAINTS (#421).

  verification (default)  sources() + roms()
  publication             sources() + roms() + [XDC]

The default is the set fpga/build_arty.py hands validate_verification, because
the UART digital bench (fpga/verify_uart_bridge.py) never drives a physical
pin: a digital bench result genuinely does not depend on pin constraints, and
no verification record this repository has ever produced records the XDC at
all. fpga/publish_arty.py takes the other view for a BITSTREAM, whose timing
does depend on its constraints, and records sources()+roms()+[XDC].

Until #421 this script only implemented the first scope and did not say so:
383f10b (#315) changed fpga/boards/arty-a7-100.xdc and this gate still printed
"covers every compiled source". It now names the constraint file on every run
-- and, since #436, the record that DOES answer for it and that record's state
-- while `--scope publication` asks the constraint question directly.

WHY PUBLICATION SCOPE IS NOT THE DEFAULT, AND ONE RECORD CANNOT ANSWER IT.
Until #436 this mode could only REFUSE (exit 2, NOT COVERED): the bound record
is a verification record, and the bench that produced it never hashed a
constraint file. Widening THAT record was the wrong fix -- a digital bench
record claiming coverage of bytes it never read is the "verified artifact is
not the shipped artifact" failure with the sign flipped.

So publication scope is answered by TWO records, each covering only what its
own bench actually read:

  fpga/reports/arty/rev14-clean/verification.json   sources() + roms()
      fpga/verify_uart_bridge.py, bound by publish_arty.VERIFICATION_BY_WRAPPER
  fpga/reports/arty/xdc-binding/binding.json        the XDC + sources()
      fpga/verify_xdc_binding.py, bound by its CONSTRAINT_BY_WRAPPER

A file is covered when every record that answers for it agrees with the live
bytes, and a file no record answers for is REFUSED rather than passed over --
otherwise dropping a file from both answer sets would make this gate greener.
The default (verification) scope is unchanged in question, verdict and output:
it consults ONE record, exactly the set build_arty.validate_verification
checks.

WHAT THE WIDER SCOPE ALREADY SHOWS, ON REAL EVIDENCE. R1
(reports/arty/r1-player-preview-2025.1, e0dd329) is the newest published image
and was built on a branch without 383f10b, so its ONLY divergence from this
tree is the constraint file. The default scope reports it as covering the tree
exactly. `--scope publication --list-historical` is where that shows up.

THREE STATES, NOT TWO. "the record does not mention these bytes" is not the
same finding as "the record mentions them and they moved", and conflating them
is how the constraint gap hid: a missing key silently read as drift.

  DIFFERS      recorded, and the live bytes have moved     -> STALE   (exit 1)
  NOT COVERED  never recorded; the record cannot answer    -> REFUSED (exit 2)
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]

# The bench that refreshes a stale digital record. A record is never rewritten
# in place: each run is its own directory, because a published image cites the
# superseded ones by hash.
UART_REFRESH = (
    "python3 fpga/verify_uart_bridge.py --scenario phrase --outdir build/uart-controls",
    "python3 fpga/verify_uart_bridge.py --start-red --outdir build/uart-startred",
)

VERIFICATION_SCOPE = "verification"
PUBLICATION_SCOPE = "publication"
SCOPES = (VERIFICATION_SCOPE, PUBLICATION_SCOPE)

DIFFERS = "DIFFERS"
NOT_COVERED = "NOT COVERED"


def _load():
    sys.path.insert(0, str(ROOT / "fpga"))
    import build_arty
    import publish_arty
    return build_arty, publish_arty


def _load_constraint():
    """fpga/verify_xdc_binding, imported ONLY for publication scope.

    The default scope must not depend on it: fpga/release/stale_controls.py
    runs this gate in an isolated copy of the checker's inputs, and an import
    error there would look like a verdict."""
    sys.path.insert(0, str(ROOT / "fpga"))
    import verify_xdc_binding
    return verify_xdc_binding


def bound_bindings():
    """[(wrapper, record path)] the publisher requires to cover this tree."""
    _, publish = _load()
    return sorted(publish.VERIFICATION_BY_WRAPPER.items())


class Evidence(NamedTuple):
    """One record the scope's question is answered by.

    label     what it is bound to, as printed
    record    the committed record
    files     the files THIS record's own bench read, and so the only ones it
              is entitled to answer for
    validate  the producing module's own check that the record is usable
              evidence (None where the check is build_arty's, already run by
              the coverage comparison itself)
    refresh   the commands that make a stale one current
    covers    how the green line describes what it covers
    """
    label: str
    record: Path
    files: list
    validate: object
    refresh: tuple
    covers: str


def bound_evidence(scope: str = VERIFICATION_SCOPE) -> list[Evidence]:
    """The records that together answer `scope`, each with the files its own
    bench read. Read off the producing modules -- never restated here -- so
    widening a bench widens this gate rather than silently diverging from
    it."""
    if scope not in SCOPES:
        raise ValueError(f"unknown scope {scope!r}; expected one of {SCOPES}")
    build, _ = _load()
    covered = build.sources() + build.roms()
    out = [Evidence(wrapper, path, covered, None, UART_REFRESH,
                    "covers every compiled source")
           for wrapper, path in bound_bindings()]
    if scope == PUBLICATION_SCOPE:
        constraint = _load_constraint()
        xdc = str(build.XDC.relative_to(build.ROOT))
        for wrapper, path in sorted(constraint.CONSTRAINT_BY_WRAPPER.items()):
            directory = (Path(path).parent.relative_to(ROOT)
                         if Path(path).is_relative_to(ROOT) else Path(path).parent)
            files = constraint.answered_files()
            out.append(Evidence(
                f"{wrapper} constraints", path, files,
                constraint.validate_record,
                (f"python3 fpga/verify_xdc_binding.py --outdir {directory}",),
                f"covers {xdc} and the {len(files) - 1} compiled source(s) its "
                f"names were resolved against"))
    return out


def scope_files(scope: str = VERIFICATION_SCOPE) -> list[Path]:
    """The files a given scope requires a record to cover.

    verification == exactly what fpga/build_arty.py hands
    validate_verification, so this gate cannot drift from the check it exists
    to pre-empt. publication == exactly what fpga/publish_arty.publish()
    builds its `expected` set from. Both are read off those modules rather
    than restated here, so widening either one there widens this."""
    if scope not in SCOPES:
        raise ValueError(f"unknown scope {scope!r}; expected one of {SCOPES}")
    build, _ = _load()
    files = build.sources() + build.roms()
    return files + [build.XDC] if scope == PUBLICATION_SCOPE else files


def coverage(record_path: Path,
             scope: str = VERIFICATION_SCOPE,
             files: list | None = None) -> list[tuple[str, str]]:
    """[(repo-relative path, DIFFERS | NOT COVERED)] for every file the record
    does not bind to the live bytes. Empty means bound.

    `files` defaults to the whole scope -- the one-record question, which is
    still what drift() and the default rung ask. main() passes the subset a
    given record answers for, because in publication scope the answer comes
    from two benches and neither is entitled to speak for the other's files.

    The two states are kept apart on purpose: a record that never hashed a
    file has not measured it moving, it has said nothing about it, and
    reporting that as drift is how the XDC gap stayed invisible (#421)."""
    build, _ = _load()
    record = json.loads(record_path.read_text())
    recorded = record.get("source_sha256")
    if not isinstance(recorded, dict):
        recorded = {}
    out = []
    for source in (scope_files(scope) if files is None else files):
        key = str(source.relative_to(build.ROOT))
        if not source.is_file():
            # An in-scope file this tree does not have is not drift and is not
            # coverage: nothing can be hashed, so REFUSE rather than answer.
            # fpga/release/stale_controls.py runs this gate in an isolated
            # copy that carries sources()+roms() and no constraint file, which
            # is exactly the case -- and in the default scope it never asks.
            raise ValueError(f"{key} is in {scope} scope but is not in this tree")
        if key not in recorded:
            out.append((key, NOT_COVERED))
        elif recorded[key] != build.sha(source):
            out.append((key, DIFFERS))
    return out


def constraint_state(record_path: Path) -> str:
    """DIFFERS | NOT COVERED | matches | ABSENT for the XDC in one record.

    Reported alongside the default scope's verdict, and never part of it: the
    point is that the reader learns what the rung did not check."""
    build, _ = _load()
    if not build.XDC.is_file():
        return "ABSENT"
    key = str(build.XDC.relative_to(build.ROOT))
    recorded = json.loads(record_path.read_text()).get("source_sha256")
    if not isinstance(recorded, dict) or key not in recorded:
        return NOT_COVERED
    return "matches" if recorded[key] == build.sha(build.XDC) else DIFFERS


def constraint_note() -> str:
    """What the default rung says about the file it does not check.

    Since #436 it names the record that DOES answer for the XDC and that
    record's state, so the reader learns where the question is answered rather
    than only that this rung does not answer it. A constraint record that
    cannot be reached is reported as such and never as the digital record's
    silence -- fpga/release/stale_controls.py runs this gate in an isolated
    copy, and 'absent' must not read like 'fine'."""
    build, _ = _load()
    xdc = str(build.XDC.relative_to(build.ROOT))
    try:
        bindings = sorted(_load_constraint().CONSTRAINT_BY_WRAPPER.items())
    except ImportError as exc:
        return f"constraint scope: {xdc} [NO RECORD REACHABLE: {exc}]"
    parts = []
    for _wrapper, path in bindings:
        path = Path(path)
        rel = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
        parts.append(f"{rel} [{constraint_state(path) if path.is_file() else 'ABSENT'}]")
    return (f"constraint scope: {xdc} is answered by "
            + (", ".join(parts) if parts else "NO BOUND RECORD"))


def drift(record_path: Path, scope: str = VERIFICATION_SCOPE) -> list[str]:
    """Repo-relative sources whose live bytes differ from the record's, or
    that the record does not cover at all. Empty means bound.

    The names only; coverage() carries which of the two it is."""
    return [key for key, _ in coverage(record_path, scope)]


def historical(scope: str = VERIFICATION_SCOPE) -> list[tuple[str, list[str]]]:
    """Committed records that name in-scope files and do NOT cover the tree.
    Data, not a verdict: these are superseded runs and stay as they are.

    Only DIFFERS counts here, in both scopes. A superseded record that never
    hashed a file is not evidence about that file, and listing every record
    that predates a source as "not covering" it would bury the real signal --
    the same conflation the per-record states exist to prevent."""
    build, _ = _load()
    live = {str(p.relative_to(build.ROOT)): build.sha(p)
            for p in scope_files(scope)}
    bound = {p for _, p in bound_bindings()}
    out = []
    for path in sorted(ROOT.glob("fpga/reports/**/*.json")):
        if path in bound:
            continue
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict):
            continue
        recorded = record.get("source_sha256")
        if not isinstance(recorded, dict):
            continue
        moved = sorted(k for k, v in recorded.items()
                       if k in live and v != live[k])
        if moved:
            out.append((str(path.relative_to(ROOT)), moved))
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list-historical", action="store_true",
                        help="also print superseded records, as data")
    parser.add_argument("--scope", choices=SCOPES, default=VERIFICATION_SCOPE,
                        help="verification (default) = sources+ROMs, what "
                             "build_arty.validate_verification checks; "
                             "publication = also the XDC, what publish_arty "
                             "records for a bitstream. See the module "
                             "docstring for why publication is not a rung.")
    args = parser.parse_args(argv)

    if not bound_bindings():
        print("REFUSED: publish_arty.VERIFICATION_BY_WRAPPER binds no wrapper; "
              "nothing is required to cover the tree and nothing can be checked")
        return 2
    build, _ = _load()
    xdc = str(build.XDC.relative_to(build.ROOT))
    stale = 0
    uncovered = 0
    evidence = bound_evidence(args.scope)
    answered = set()
    for wrapper, path, files, validate, refresh, covers in evidence:
        rel = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
        answered |= {str(p.relative_to(build.ROOT)) for p in files}
        if not path.is_file():
            print(f"REFUSED: {wrapper} binds {rel}, which does not exist")
            return 2
        if validate is not None:
            # a record's own preconditions, asserted at the point of use: an
            # INJECTED run, a run against other constraint bytes, or one whose
            # transcript has been edited is not evidence, and must not be able
            # to make this gate green by carrying the right hashes
            try:
                validate(path)
            except ValueError as exc:
                print(f"REFUSED: {wrapper} binds {rel}, which is not usable "
                      f"evidence: {exc}")
                return 2
        try:
            states = coverage(path, args.scope, files)
        except ValueError as exc:
            # an unreadable record, or an in-scope file this tree does not
            # have: either way the question cannot be answered, not answered no
            print(f"REFUSED: {wrapper} binds {rel}, and the check cannot be "
                  f"made: {exc}")
            return 2
        missing = [k for k, s in states if s == NOT_COVERED]
        if states:
            # REFUSED outranks STALE: if the record never hashed a file, the
            # honest verdict is that it cannot answer, not that it disagrees.
            label = "REFUSED" if missing else "STALE"
            uncovered += bool(missing)
            stale += not missing
            print(f"{label}: {wrapper} binds {rel}, which does not cover "
                  f"{len(states)} file(s) in {args.scope} scope:")
            for key, state in states:
                print(f"           {key}  [{state}]")
            print("       Re-run the bench on THIS tree and add its record as a "
                  "new directory (never rewrite a superseded one):")
            for command in refresh:
                print(f"           {command}")
            if missing:
                print("       A NOT COVERED file was never hashed by this "
                      "record; no bench re-run can make an old record cover "
                      "bytes it never read.")
        else:
            print(f"BOUND: {wrapper} -> {rel} {covers}")
        if args.scope == VERIFICATION_SCOPE:
            # #421: say what this scope does NOT answer, on every run, so
            # "covers every compiled source" is never read as "covers the
            # constraints". The verdict above deliberately does not depend
            # on this line.
            print(f"       {constraint_note()} -- not part of "
                  f"{VERIFICATION_SCOPE} scope, which is what this rung "
                  f"checks. Use --scope {PUBLICATION_SCOPE} to ask.")

    # A file no record answers for is not covered -- it is unasked. Without
    # this, removing a file from every bench's read set would make this gate
    # GREENER, which is the one direction a coverage check must never move in.
    unasked = sorted({str(p.relative_to(build.ROOT))
                      for p in scope_files(args.scope)} - answered)
    if unasked:
        print(f"REFUSED: no bound record answers for {len(unasked)} file(s) in "
              f"{args.scope} scope:")
        for key in unasked:
            print(f"           {key}  [{NOT_COVERED}]")
        print("       Every file a scope names must be read by some bench. A "
              "file no record answers for has no evidence at all, which is a "
              "wider gap than a stale one.")
        return 2

    if args.list_historical:
        records = historical(args.scope)
        print(f"\nsuperseded records that name a moved compiled source: "
              f"{len(records)} (expected, not a failure)")
        for rel, moved in records:
            print(f"  {rel}  ({', '.join(moved)})")
    if uncovered:
        return 2
    return 1 if stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
