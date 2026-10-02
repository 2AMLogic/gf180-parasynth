#!/usr/bin/env python3
"""The reference corpus by SOURCE LINEAGE, and the rules that keeps honest.

    tools/corpus_lineage.py check          every rule below; 0 / 1, or 2 with --strict
    tools/corpus_lineage.py list           every pack, its lineage status and its groups
    tools/corpus_lineage.py open           only the fields an operator still has to fill in

WHY A CHECKER AND NOT JUST A DOCUMENT (issue #520, part of #158)
----------------------------------------------------------------
`docs/corpus-lineage.md` is prose, and `docs/failure-modes.md` mechanism 4 is
prose outliving its evidence. The two facts this repository most needs to not
drift are exactly the kind prose loses:

  * **which lineage a pack belongs to** -- "an extra library does not establish
    an extra machine", and three packs here are one machine re-pressed; and
  * **which group a lineage may be read in** -- a lineage whose unit identity is
    a vendor claim must not calibrate a threshold or back a verdict.

So the groups, the assignment rule and the per-pack lineage live in
`docs/corpus-lineage.json`, the document is checked against it, and the rules
are asserted rather than described. `tools/test_corpus_lineage.py` carries an
injected-defect control for every rule, because a checker's only failure mode
that matters is a false green.

THE RULES, AND WHAT EACH ONE IS FOR
-----------------------------------
R1  Every pack carries every required lineage field, non-empty.
R2  `lineage_status` is one of documented / claimed / unknown.
R3  A pack whose `lineage_status` is `documented` must have an established
    `unit`. This is the input that defeats R2 on its own: a pack can be
    declared documented while its unit says unknown, and then it passes the
    weak-evidence rule it should fail. (R3 deliberately does NOT require every
    other field to be established -- `legowelt-minimoog-5529` has a published
    serial number and an undocumented recording chain, which is a documented
    unit with an open field, not an undocumented lineage. Conflating the two
    would make the gate unsatisfiable on a pack whose identity is the best in
    the corpus.)
R4  Every `roles` entry is one of the three group names.
R5  WEAK EVIDENCE (#158), and it has TWO tiers because the corpus does:
      * `unknown` -- nothing is established. `analyzer-development` only.
      * `claimed` -- the vendor asserts the unit and nothing has measured it.
        **Never `held-out-validation`**: a verdict read against a pack whose
        unit identity is unestablished is precisely "an extra library
        establishes an extra machine". It MAY enter `threshold-calibration`,
        but only carrying a non-empty `weak_evidence_why` stating why the
        calibration does not depend on the unestablished fact.
    The second tier exists because the live case is legitimate and a flat ban
    would have been an unsatisfiable gate: the f0 discrimination band's floor
    is the SESSION-TO-SESSION spread between two editions of one Samples From
    Mars machine, and that number does not care which machine it is -- only
    that it is the same one twice.
R6  A lineage in more than one group carries a non-empty `role_overlap_why`
    naming the separation in force instead. Silence is not an option.
R7  A pack with no roles carries a non-empty `group_why`. "Unused" is a result;
    an unexplained blank is not.
R8  DOCUMENT AGREEMENT: the pack ids in the document's delimited pack table and
    the pack ids in the manifest are the same set, in the same order. Drift in
    either direction is a finding.
R9  Every group name and every target name appears verbatim in the document,
    so the three groups and the three measurement targets cannot be renamed in
    one file and left stale in the other.
R10 Exactly three groups and exactly three targets, with the expected names.
R11 Every pack's `evidence` list is non-empty and names paths that exist.

WHAT IT CANNOT DO, stated so nobody reads more into a green run
---------------------------------------------------------------
* It cannot tell what a probe actually READS. A pack could be listed with no
  roles and still be loaded by a script. Nothing in this repository can see
  that; the groups are a declaration of intent that a reviewer checks against
  the diff, and R5 only has teeth because `roles` is also where a reader looks.
* It cannot establish lineage. `descent_tested` records whether
  `model/measure_harness.descent_test` has been RUN, not what it found; the
  finding goes in `relationships` with its citation.
* It does not look at audio and never will. No commercial audio, and nothing
  that could re-identify it, belongs in this repository.

OUTCOMES
--------
    0   every rule holds (OPEN fields are reported, not failed: an explicitly
        marked unknown is the correct state for a corpus nobody has mounted)
    1   at least one rule is FALSE
    2   --strict only: no rule is FALSE but something is still OPEN. For an
        operator who HAS the corpus mounted and is closing the gaps out.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "docs" / "corpus-lineage.json"
DOC = ROOT / "docs" / "corpus-lineage.md"

GROUPS = ("analyzer-development", "threshold-calibration", "held-out-validation")
TARGETS = ("designated-recording-match", "hardware-variation-coverage",
           "processed-pack-distribution-match")
LINEAGE_STATUS = ("documented", "claimed", "unknown")

#: The group an `unknown` lineage may be read in, and nothing else.
WEAK_OK = "analyzer-development"
#: The group a `claimed` lineage may be read in with a written justification.
CLAIMED_OK_WITH_REASON = "threshold-calibration"
#: The group NO lineage may enter without `documented` status. A verdict is the
#: one read whose meaning depends entirely on which machine it was taken on.
VERDICT_GROUP = "held-out-validation"

REQUIRED_TEXT = ("name", "instrument", "unit", "lineage_status", "dry_or_processed",
                 "known_settings", "processing", "relationships", "licence",
                 "redistribution", "storage", "descent_tested")

#: The document's pack table, delimited so the parse cannot wander into another
#: table that happens to be nearby.
PACK_REGION = (re.compile(r"<!--\s*corpus-lineage:packs\s*-->"),
               re.compile(r"<!--\s*/corpus-lineage:packs\s*-->"))


class Refused(Exception):
    """A precondition failed: say so instead of producing findings."""


def load(manifest: pathlib.Path = MANIFEST) -> dict:
    if not manifest.exists():
        raise Refused(f"manifest missing: {manifest}")
    try:
        m = json.loads(manifest.read_text())
    except json.JSONDecodeError as e:
        raise Refused(f"{manifest.name} is not valid JSON: {e}") from e
    for key in ("schema", "groups", "assignment_rule", "targets", "packs", "unknown_marker"):
        if key not in m:
            raise Refused(f"{manifest.name} has no {key!r}")
    return m


def doc_pack_ids(doc: pathlib.Path = DOC) -> list[str]:
    """The first column of every table row inside the delimited pack region."""
    if not doc.exists():
        raise Refused(f"document missing: {doc}")
    text = doc.read_text()
    open_m, close_m = (p.search(text) for p in PACK_REGION)
    if not open_m or not close_m or close_m.start() < open_m.end():
        raise Refused(f"{doc.name} has no delimited <!-- corpus-lineage:packs --> region")
    ids = []
    for line in text[open_m.end():close_m.start()].splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cell = line.strip("|").split("|")[0].strip()
        if not cell or set(cell) <= set("-: "):          # header rule row
            continue
        hit = re.fullmatch(r"`([A-Za-z0-9][A-Za-z0-9._-]*)`", cell)
        if hit:
            ids.append(hit.group(1))
    return ids


def _finding(rule: str, status: str, what: str) -> dict:
    return {"rule": rule, "status": status, "what": what}


def check(manifest: pathlib.Path = MANIFEST, doc: pathlib.Path = DOC,
          root: pathlib.Path = ROOT) -> list[dict]:
    """Every rule in the module docstring. FALSE is a violation, OPEN is a
    field explicitly marked unknown, OK is a rule that held."""
    m = load(manifest)
    unknown = m["unknown_marker"]
    out: list[dict] = []

    # R10 -- the vocabularies themselves, before anything is checked against them
    if tuple(m["groups"]) != GROUPS:
        out.append(_finding("R10", "FALSE", f"groups are {tuple(m['groups'])}, expected {GROUPS}"))
    else:
        out.append(_finding("R10", "OK", f"three groups: {', '.join(GROUPS)}"))
    if tuple(m["targets"]) != TARGETS:
        out.append(_finding("R10", "FALSE", f"targets are {tuple(m['targets'])}, expected {TARGETS}"))
    else:
        out.append(_finding("R10", "OK", f"three targets: {', '.join(TARGETS)}"))
    for name, t in m["targets"].items():
        for key in ("definition", "current_work"):
            if not str(t.get(key, "")).strip():
                out.append(_finding("R10", "FALSE", f"target {name} has no {key}"))

    ids = [p.get("id", "<no id>") for p in m["packs"]]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        out.append(_finding("R1", "FALSE", f"duplicate pack ids: {dupes}"))

    for p in m["packs"]:
        pid = p.get("id", "<no id>")
        marked_unknown = []

        # R1 -- fields present and non-empty
        for key in REQUIRED_TEXT:
            val = str(p.get(key, "") or "").strip()
            if not val:
                out.append(_finding("R1", "FALSE", f"{pid}: {key} is missing or empty"))
            elif val == unknown or val.startswith(unknown):
                marked_unknown.append(key)
                out.append(_finding("R1", "OPEN", f"{pid}: {key} is not established yet"))

        # R2 -- lineage vocabulary
        status = p.get("lineage_status")
        if status not in LINEAGE_STATUS:
            out.append(_finding("R2", "FALSE",
                                f"{pid}: lineage_status {status!r} not in {LINEAGE_STATUS}"))

        # R3 -- the input that defeats R2: documented, with no established unit
        if status == "documented" and "unit" in marked_unknown:
            out.append(_finding("R3", "FALSE",
                                f"{pid}: lineage_status is 'documented' but the unit itself "
                                f"is not established"))

        # R4 / R5 / R6 / R7 -- groups
        roles = p.get("roles")
        if not isinstance(roles, list):
            out.append(_finding("R4", "FALSE", f"{pid}: roles must be a list"))
            roles = []
        bad = [r for r in roles if r not in GROUPS]
        if bad:
            out.append(_finding("R4", "FALSE", f"{pid}: roles {bad} are not group names"))
        if status == "unknown":
            over = [r for r in roles if r in GROUPS and r != WEAK_OK]
            if over:
                out.append(_finding("R5", "FALSE",
                                    f"{pid}: lineage_status is 'unknown', so it may only appear "
                                    f"in {WEAK_OK}; it also appears in {over}"))
        elif status == "claimed":
            if VERDICT_GROUP in roles:
                out.append(_finding("R5", "FALSE",
                                    f"{pid}: lineage_status is 'claimed' -- a vendor's word -- so "
                                    f"it may never enter {VERDICT_GROUP}"))
            if (CLAIMED_OK_WITH_REASON in roles
                    and not str(p.get("weak_evidence_why", "") or "").strip()):
                out.append(_finding("R5", "FALSE",
                                    f"{pid}: lineage_status is 'claimed' and it is in "
                                    f"{CLAIMED_OK_WITH_REASON} with no weak_evidence_why saying "
                                    f"why the calibration does not depend on the unestablished unit"))
        if len(roles) > 1 and not str(p.get("role_overlap_why", "") or "").strip():
            out.append(_finding("R6", "FALSE",
                                f"{pid}: in {len(roles)} groups with no role_overlap_why naming "
                                f"the separation in force instead"))
        if not roles and not str(p.get("group_why", "") or "").strip():
            out.append(_finding("R7", "FALSE", f"{pid}: in no group and no group_why"))

        # R11 -- evidence exists
        ev = p.get("evidence")
        if not isinstance(ev, list) or not ev:
            out.append(_finding("R11", "FALSE", f"{pid}: evidence is missing or empty"))
        else:
            for rel in ev:
                if not (root / rel).exists():
                    out.append(_finding("R11", "FALSE", f"{pid}: evidence path {rel} does not exist"))

    # R8 -- document agreement
    listed = doc_pack_ids(doc)
    if listed != ids:
        only_doc = [i for i in listed if i not in ids]
        only_man = [i for i in ids if i not in listed]
        detail = []
        if only_doc:
            detail.append(f"in the document only: {only_doc}")
        if only_man:
            detail.append(f"in the manifest only: {only_man}")
        if not detail:
            detail.append(f"same set, different order: document {listed} vs manifest {ids}")
        out.append(_finding("R8", "FALSE", "; ".join(detail)))
    else:
        out.append(_finding("R8", "OK", f"{len(ids)} packs, document and manifest agree"))

    # R9 -- names appear in the document
    text = doc.read_text()
    for name in GROUPS + TARGETS:
        if name not in text:
            out.append(_finding("R9", "FALSE", f"{doc.name} never mentions {name!r}"))

    if not any(f["status"] == "FALSE" for f in out):
        out.append(_finding("-", "OK", "every rule holds"))
    return out


def _print(findings: list[dict]) -> None:
    for f in findings:
        if f["status"] != "OK":
            print(f"{f['status']:<6} {f['rule']:<4} {f['what']}")
    n_false = sum(1 for f in findings if f["status"] == "FALSE")
    n_open = sum(1 for f in findings if f["status"] == "OPEN")
    print(f"\n{len(findings) - n_false - n_open} ok, {n_false} FALSE, {n_open} OPEN")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    ck = sub.add_parser("check", help="every rule; 1 on a violation, 2 with --strict on an open field")
    ck.add_argument("--strict", action="store_true",
                    help="for an operator WITH the corpus mounted: an unestablished field is an error")
    sub.add_parser("list", help="every pack, its lineage status and its groups")
    sub.add_parser("open", help="only the fields an operator still has to fill in")
    a = ap.parse_args(argv)

    try:
        if a.cmd == "list":
            m = load()
            for p in m["packs"]:
                print(f"{p['id']:<26} {p['lineage_status']:<11} "
                      f"{', '.join(p.get('roles') or ['(no group)'])}")
            return 0
        findings = check()
    except Refused as e:
        print(f"REFUSED: {e}")
        return 2

    if a.cmd == "open":
        rows = [f for f in findings if f["status"] == "OPEN"]
        for f in rows:
            print(f"OPEN   {f['what']}")
        print(f"\n{len(rows)} field(s) await an operator with the corpus mounted")
        return 0

    _print(findings)
    if any(f["status"] == "FALSE" for f in findings):
        return 1
    if a.strict and any(f["status"] == "OPEN" for f in findings):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
