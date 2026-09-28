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
"covers every compiled source". It now names the constraint file and its
coverage state on every run, and `--scope publication` asks the constraint
question directly.

WHY PUBLICATION SCOPE IS NOT THE DEFAULT, AND IS NOT A `make verify` RUNG.
The bound record is a verification record, so in publication scope it REFUSES
(exit 2, NOT COVERED) -- honestly, because it cannot answer: the bench that
produced it never hashed a constraint file. Making that the default would turn
the Makefile rung permanently red for a question no committed evidence can
answer, and an unsatisfiable gate is worse than no gate (CLAUDE.md). Making
publication scope *answerable* needs a bench or build record that covers the
XDC, which is a change to fpga/build_arty.py's evidence semantics and is
deliberately out of scope here.

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

ROOT = Path(__file__).resolve().parents[1]

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


def bound_bindings():
    """[(wrapper, record path)] the publisher requires to cover this tree."""
    _, publish = _load()
    return sorted(publish.VERIFICATION_BY_WRAPPER.items())


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
             scope: str = VERIFICATION_SCOPE) -> list[tuple[str, str]]:
    """[(repo-relative path, DIFFERS | NOT COVERED)] for every file in `scope`
    the record does not bind to the live bytes. Empty means bound.

    The two states are kept apart on purpose: a record that never hashed a
    file has not measured it moving, it has said nothing about it, and
    reporting that as drift is how the XDC gap stayed invisible (#421)."""
    build, _ = _load()
    record = json.loads(record_path.read_text())
    recorded = record.get("source_sha256")
    if not isinstance(recorded, dict):
        recorded = {}
    out = []
    for source in scope_files(scope):
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

    bindings = bound_bindings()
    if not bindings:
        print("REFUSED: publish_arty.VERIFICATION_BY_WRAPPER binds no wrapper; "
              "nothing is required to cover the tree and nothing can be checked")
        return 2
    build, _ = _load()
    xdc = str(build.XDC.relative_to(build.ROOT))
    stale = 0
    uncovered = 0
    for wrapper, path in bindings:
        rel = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
        if not path.is_file():
            print(f"REFUSED: {wrapper} binds {rel}, which does not exist")
            return 2
        try:
            states = coverage(path, args.scope)
        except ValueError as exc:
            print(f"REFUSED: {wrapper} binds {rel}, which is unreadable: {exc}")
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
            print("           python3 fpga/verify_uart_bridge.py --scenario phrase "
                  "--outdir build/uart-controls")
            print("           python3 fpga/verify_uart_bridge.py --start-red "
                  "--outdir build/uart-startred")
            if missing:
                print("       A NOT COVERED file was never hashed by this "
                      "record; no bench re-run can make an old record cover "
                      "bytes it never read.")
        else:
            print(f"BOUND: {wrapper} -> {rel} covers every compiled source")
        if args.scope == VERIFICATION_SCOPE:
            # #421: say what this scope does NOT answer, on every run, so
            # "covers every compiled source" is never read as "covers the
            # constraints". The verdict above deliberately does not depend
            # on this line.
            print(f"       constraint scope: {xdc} [{constraint_state(path)}] -- not part of "
                  f"{VERIFICATION_SCOPE} scope, which is what this rung "
                  f"checks. Use --scope {PUBLICATION_SCOPE} to ask.")

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
