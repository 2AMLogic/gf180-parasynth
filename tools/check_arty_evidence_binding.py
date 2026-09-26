#!/usr/bin/env python3
"""Does the Arty wrapper's bound digital proof still cover the tree?

    python3 tools/check_arty_evidence_binding.py          # 0 bound, 1 stale, 2 refused

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
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def _load():
    sys.path.insert(0, str(ROOT / "fpga"))
    import build_arty
    import publish_arty
    return build_arty, publish_arty


def bound_bindings():
    """[(wrapper, record path)] the publisher requires to cover this tree."""
    _, publish = _load()
    return sorted(publish.VERIFICATION_BY_WRAPPER.items())


def drift(record_path: Path) -> list[str]:
    """Repo-relative sources whose live bytes differ from the record's, or
    that the record does not cover at all. Empty means bound."""
    build, _ = _load()
    record = json.loads(record_path.read_text())
    recorded = record.get("source_sha256", {})
    out = []
    for source in build.sources() + build.roms():
        key = str(source.relative_to(build.ROOT))
        if recorded.get(key) != build.sha(source):
            out.append(key)
    return out


def historical() -> list[tuple[str, list[str]]]:
    """Committed records that name compiled sources and do NOT cover the tree.
    Data, not a verdict: these are superseded runs and stay as they are."""
    build, _ = _load()
    live = {str(p.relative_to(build.ROOT)): build.sha(p)
            for p in build.sources() + build.roms()}
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
    args = parser.parse_args(argv)

    bindings = bound_bindings()
    if not bindings:
        print("REFUSED: publish_arty.VERIFICATION_BY_WRAPPER binds no wrapper; "
              "nothing is required to cover the tree and nothing can be checked")
        return 2
    stale = 0
    for wrapper, path in bindings:
        rel = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
        if not path.is_file():
            print(f"REFUSED: {wrapper} binds {rel}, which does not exist")
            return 2
        try:
            moved = drift(path)
        except ValueError as exc:
            print(f"REFUSED: {wrapper} binds {rel}, which is unreadable: {exc}")
            return 2
        if moved:
            stale += 1
            print(f"STALE: {wrapper} binds {rel}, which does not cover "
                  f"{len(moved)} compiled source(s):")
            for key in moved:
                print(f"           {key}")
            print("       Re-run the bench on THIS tree and add its record as a "
                  "new directory (never rewrite a superseded one):")
            print("           python3 fpga/verify_uart_bridge.py --scenario phrase "
                  "--outdir build/uart-controls")
            print("           python3 fpga/verify_uart_bridge.py --start-red "
                  "--outdir build/uart-startred")
        else:
            print(f"BOUND: {wrapper} -> {rel} covers every compiled source")

    if args.list_historical:
        records = historical()
        print(f"\nsuperseded records that name a moved compiled source: "
              f"{len(records)} (expected, not a failure)")
        for rel, moved in records:
            print(f"  {rel}  ({', '.join(moved)})")
    return 1 if stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
