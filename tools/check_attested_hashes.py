#!/usr/bin/env python3
"""Couple "edited an attested file" to "regenerate the attestation" (issue #884).

PR #865 edited `.github/workflows/rungs.yml`, a member of item-10's `hygiene`
set, and CI's `regrade` job went red as a bare exit 3: the step redirects klt's
report to `$RUNNER_TEMP` and GitHub's `bash -e` aborts before the `cat`, so the
drift JSON naming the moved hash never reached the log. Nothing before CI told
the Builder that touching an inventory-listed path requires regenerating the
evidence. (signoff.yml now prints the report before failing on it; this tool is
the local half, run by `make verify-fast`.)

WHAT IT CHECKS, per `manifests/inventories/item-*.json`:

  * every `attestation.checks[]` entry of the matching
    `manifests/evidence/item-*.json` that carries a `path`: a fresh
    sha256 **over the file's raw bytes** against the committed
    `content_hash`. Raw-bytes semantics validated against the committed
    evidence on `bcf53a6a`: 25/25 item-9 and 12/12 item-10 path checks
    recompute to `match` with `hashlib.sha256(open(p, 'rb').read())`.
    A pathed entry whose `content_hash` is not a non-empty string is
    REFUSED (#932): an entry that never committed a hash cannot be
    compared, and an uncompared check must not be counted as recomputed.
    Entries without a `path` (item-9's `local_only` cold_start, the pdk and
    tools pins; item-10's audit command) carry a committed outcome, not a
    hash commitment, and are NOT re-run -- hash comparison only.
  * the evidence population against the inventory's own file list (every
    top-level list-of-strings value: item-9 `testbenches`, item-10
    `hygiene`; `checks` is a list of objects and is excluded by
    construction). An evidence that silently drops a check is drift, not a
    smaller pass set.
  * the inventory's own bytes against the envelope's
    `provenance.input.content_hash` -- the hash `evidence.N.content_hash`
    in `manifests/gf180-parasynth.json` must be kept equal to, so an
    inventory edit without regeneration is caught too.

OUTCOMES (docs/failure-modes.md: REFUSED is a first-class outcome):

    exit 0   every committed hash recomputes to match
    exit 1   drift: a hash moved, a file is absent, or a check was dropped;
             the report names path, committed hash, fresh hash and the
             three-artefact regeneration recipe
    exit 2   REFUSED: missing evidence/inventory, unparseable JSON, missing
             provenance hash, a pathed check entry with no committed
             content_hash, or no inventories at all -- "cannot run" is
             never rendered as "pass"

A deleted-but-not-regenerated attested file is exit 1, not 2: the committed
hash is still a fact and absence is the largest possible drift. Exit 2 is
reserved for the instrument being unable to measure at all.

Stdlib only; no klt, no network -- safe inside `make verify-fast`.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent

RECIPE = """\
Regenerate (three artefacts move together -- the authoritative recipe is the
header of .github/workflows/signoff.yml):
  1. K="uvx --with pyyaml --from klayout-tools==0.8.0 klt"
     $K signoff --attest <item> --inventory manifests/inventories/<item>.json \\
         -o manifests/evidence/<item>.json
  2. set evidence.<item>.content_hash in manifests/gf180-parasynth.json to the
     envelope's provenance.input.content_hash
  3. $K signoff --manifest manifests/gf180-parasynth.json --no-color \\
         > manifests/verdict.txt     # exit 3 is normal: the block is not T1\
"""


def sha256_file(path: pathlib.Path) -> str:
    """klt's `content_hash` semantics, validated on the committed evidence:
    sha256 hexdigest over the file's raw bytes."""
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _rel(root: pathlib.Path, path: pathlib.Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _inventory_file_lists(inv: dict) -> list[str]:
    """Every top-level value that is a (non-empty) list of plain strings.

    item-9's `testbenches` and item-10's `hygiene` both take this shape;
    item-10's `checks` is a list of objects and is excluded by construction.
    A future inventory shape the evidence disagrees with surfaces as a
    population drift rather than as a silent empty population."""
    out: list[str] = []
    for value in inv.values():
        if isinstance(value, list) and value and all(isinstance(s, str) for s in value):
            out.extend(value)
    return out


def check_tree(root: pathlib.Path = REPO) -> tuple[int, str]:
    """Check every inventory/evidence pair under `root`.

    Returns `(exit_code, human_report)`. Exit 0 green, 1 drift, 2 REFUSED.
    """
    inv_dir = root / "manifests" / "inventories"
    ev_dir = root / "manifests" / "evidence"
    inventories = sorted(inv_dir.glob("item-*.json"))
    if not inventories:
        return 2, (f"REFUSED: no manifests/inventories/item-*.json under "
                   f"{root} -- nothing was measured, and no-evidence is not "
                   f"no-problem")

    drifts: list[str] = []
    nfiles = 0
    for inv_path in inventories:
        name = inv_path.name
        ev_path = ev_dir / name
        if not ev_path.exists():
            return 2, (f"REFUSED: {name} has no matching evidence envelope "
                       f"{_rel(root, ev_path)} -- cannot compare, and a "
                       f"missing envelope is not an empty pass set")
        try:
            inv = json.loads(inv_path.read_text())
            ev = json.loads(ev_path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            return 2, (f"REFUSED: cannot parse {_rel(root, inv_path)} or "
                       f"{_rel(root, ev_path)}: {type(e).__name__}: {e}")

        if not isinstance(inv, dict) or not isinstance(ev, dict):
            return 2, f"REFUSED: {name} pair is not a JSON object"

        checks = (ev.get("attestation") or {}).get("checks")
        if not isinstance(checks, list) or not checks:
            return 2, (f"REFUSED: {_rel(root, ev_path)} has no "
                       f"attestation.checks[] -- cannot compare")

        # -- the inventory's own bytes vs the envelope's input hash ---------
        committed_input = ((ev.get("provenance") or {}).get("input") or {})
        if not committed_input.get("content_hash"):
            return 2, (f"REFUSED: {_rel(root, ev_path)} has no "
                       f"provenance.input.content_hash")
        fresh_input = sha256_file(inv_path)
        if fresh_input != committed_input["content_hash"]:
            drifts.append(
                f"DRIFT {_rel(root, inv_path)}: the inventory's own bytes "
                f"moved without a regenerated envelope\n"
                f"  committed (provenance.input.content_hash): "
                f"{committed_input['content_hash']}\n"
                f"  fresh:     {fresh_input}")

        # -- population: the envelope must still cover what the inventory
        #    lists. An envelope that silently drops a check is drift, not a
        #    smaller pass set.
        listed = _inventory_file_lists(inv)
        pathed = [(c.get("path"), c.get("content_hash"))
                  for c in checks
                  if isinstance(c, dict) and c.get("path")]
        ev_paths = [p for p, _ in pathed]
        dropped = sorted(set(listed) - set(ev_paths))
        extra = sorted(set(ev_paths) - set(listed))
        if dropped:
            drifts.append(
                f"DRIFT {name}: the envelope stopped covering "
                f"{len(dropped)} listed file(s): {', '.join(dropped)}\n"
                f"  (no committed hash to compare -- the check entry was "
                f"removed from attestation.checks[])")
        if extra:
            drifts.append(
                f"DRIFT {name}: the envelope hashes file(s) the inventory "
                f"no longer lists: {', '.join(extra)}")

        # -- per-file hash comparison ----------------------------------------
        for path, committed in pathed:
            if not isinstance(committed, str) or not committed:
                return 2, (f"REFUSED: {name} attestation.checks[] entry for "
                           f"{path} carries a path but its content_hash is "
                           f"{committed!r} -- a check that never committed "
                           f"a hash cannot be compared, and an uncompared "
                           f"check is not a pass")
            attested = root / path
            if not attested.is_file():
                drifts.append(
                    f"DRIFT {name}: attested file {path} is absent from the "
                    f"working tree\n"
                    f"  committed: {committed}\n"
                    f"  fresh:     absent")
                continue
            fresh = sha256_file(attested)
            if fresh != committed:
                drifts.append(
                    f"DRIFT {name}: attested file {path}\n"
                    f"  committed: {committed}\n"
                    f"  fresh:     {fresh}")
            nfiles += 1

    if drifts:
        report = "\n\n".join(drifts)
        report += "\n\n" + RECIPE
        return 1, report
    return 0, (f"ok: {len(inventories)} inventory/evidence pair(s), "
               f"{nfiles} attested file(s) recomputed to match, inventory "
               f"self-hashes match")


def main(argv: list[str] | None = None) -> int:
    root = REPO
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        if args[0] in ("-h", "--help"):
            print(__doc__)
            return 0
        if args[0] == "--root":
            if len(args) < 2:
                print("REFUSED: --root needs a path", file=sys.stderr)
                return 2
            root = pathlib.Path(args[1])
        else:
            print(f"REFUSED: unknown argument {args[0]!r} "
                  f"(--root DIR is the only one)", file=sys.stderr)
            return 2
    code, report = check_tree(root)
    print(report)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
