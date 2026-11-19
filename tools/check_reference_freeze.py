#!/usr/bin/env python3
"""Is the frozen Mini V3 reference still the one that was frozen? (#1050)

    python3 tools/check_reference_freeze.py            # check against the tree
    python3 tools/check_reference_freeze.py --generate # write the manifest (once)

The Mini V3 records are FROZEN (#1048/#1050): never regenerated, never edited,
and never compared against Mini V4 renders. "Frozen" is enforced here, not
asked for politely. The freeze manifest `docs/scorecard/reference-suite-freeze.sha256`
was generated from the tree at freeze time and is committed beside the records
it covers; this tool recomputes every hash and turns any drift into a red
`make verify`. The manifest is committed, not re-derived from the V3 files at
test time -- a checker that read its expected values from the thing it checks
would pass a rewritten record against itself.

Covered (each a statement that editing it changes what the V3 suite MEANS):

  * every tracked file under the three frozen record directories
    (`docs/scorecard/mono-m1a-miniv3/`, `mono-m5a-miniv3/`, `mono-m5b-miniv3/`)
    -- audio, manifests, measurements, reports;
  * `docs/minimoog-reference.md` and `docs/moog-recording-protocol.md` -- the
    primary-source documents the V3 records cite;
  * the `miniv3-frozen` entry of `docs/corpus-lineage.json`'s
    `plugin_reference_suites` section, hashed canonically (sorted keys) so the
    suite DECLARATION is frozen without freezing the whole corpus manifest --
    hardware packs must stay free to change around it.

Outcomes, per the exit contract: PASS 0 (every hash held), FAIL 1 (a file's
hash moved or a covered file is missing -- the freeze is broken, and the
offending path is named), REFUSED 2 (the freeze manifest is missing or
malformed, so nothing could be checked).

A mutation of one frozen file is a committed control for this tool:
`tools/test_reference_freeze.py` copies the tree, edits one byte, and requires
this tool to name it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import reference_suites as rs                                        # noqa: E402

OK, FAIL, REFUSED = 0, 1, 2

#: Documents the V3 records cite; editing one changes what the suite means.
FREEZE_DOCS = (
    "docs/minimoog-reference.md",
    "docs/moog-recording-protocol.md",
)

#: Canonical (sorted-keys JSON) hashes of objects inside files that must stay
#: editable as a whole. Key is the dotted path from the JSON root.
CORPUS_MANIFEST = "docs/corpus-lineage.json"
FREEZE_CANON_KEYS = (
    "plugin_reference_suites.suites.miniv3-frozen",
)


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_hash(obj) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, indent=1).encode("utf-8")).hexdigest()


def _resolve(root: pathlib.Path, dotted: str):
    obj = json.loads((root / CORPUS_MANIFEST).read_text())
    for part in dotted.split("."):
        obj = obj[part]
    return obj


def freeze_entries(root: pathlib.Path) -> list[tuple[str, str, str]]:
    """(kind, digest, subject) for everything the freeze covers."""
    entries: list[tuple[str, str, str]] = []
    for d in rs.SUITE_RECORDS[rs.SUITE_V3_FROZEN]:
        rel = subprocess.run(
            ["git", "ls-files", d], cwd=root, capture_output=True, text=True,
            check=True).stdout.split()
        for path in sorted(rel):
            entries.append(("file", _sha256(root / path), path))
    for path in FREEZE_DOCS:
        entries.append(("file", _sha256(root / path), path))
    manifest = json.loads((root / CORPUS_MANIFEST).read_text())
    for key in FREEZE_CANON_KEYS:
        obj = manifest
        for part in key.split("."):
            obj = obj[part]
        entries.append(("canon", _canonical_hash(obj), key))
    return entries


def write_manifest(root: pathlib.Path, path: pathlib.Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{kind}  {digest}  {subject}" for kind, digest, subject
             in freeze_entries(root)]
    path.write_text("\n".join(lines) + "\n")
    print(f"wrote {len(lines)} entries to {path.relative_to(ROOT)}")
    return OK


def check(root: pathlib.Path, path: pathlib.Path) -> int:
    if not path.exists():
        print(f"REFUSED  freeze manifest missing: {path.relative_to(ROOT)}")
        return REFUSED
    expected: list[tuple[str, str, str]] = []
    for n, line in enumerate(path.read_text().splitlines(), 1):
        parts = line.split("  ")
        if len(parts) != 3 or parts[0] not in ("file", "canon"):
            print(f"REFUSED  freeze manifest line {n} is malformed: {line!r}")
            return REFUSED
        expected.append((parts[0], parts[1], parts[2]))

    actual = {subject: (kind, digest) for kind, digest, subject
              in freeze_entries(root)}
    bad: list[str] = []
    for kind, digest, subject in expected:
        if subject not in actual:
            bad.append(f"{subject}: covered by the freeze manifest but MISSING "
                       f"from the tree (deleted or untracked)")
        elif actual[subject] != (kind, digest):
            bad.append(f"{subject}: sha256 {actual[subject][1][:12]}... != frozen "
                       f"{digest[:12]}... (edited since the freeze)")
    grew = sorted(set(actual) - {s for _k, _d, s in expected})
    for subject in grew:
        bad.append(f"{subject}: a frozen-scope file is NOT in the freeze "
                   f"manifest -- regenerate the manifest or exclude it with "
                   f"a stated reason")
    if bad:
        print(f"FAIL  the Mini V3 freeze is broken in {len(bad)} place(s):")
        for b in bad:
            print(f"  {b}")
        return FAIL
    print(f"PASS  {len(expected)} frozen entries hold "
          f"({sum(1 for k, _d, _s in expected if k == 'file')} files, "
          f"{sum(1 for k, _d, _s in expected if k == 'canon')} canonical)")
    return OK


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--generate", action="store_true",
                    help="write the freeze manifest from the current tree")
    ap.add_argument("--freeze-manifest", default=str(rs.FREEZE_MANIFEST),
                    metavar="PATH")
    ap.add_argument("--root", default=str(ROOT), metavar="PATH",
                    help="tree root to check (tests point this at a copy)")
    args = ap.parse_args(argv)
    root, path = pathlib.Path(args.root), pathlib.Path(args.freeze_manifest)
    if args.generate:
        return write_manifest(root, path)
    return check(root, path)


if __name__ == "__main__":
    sys.exit(main())
