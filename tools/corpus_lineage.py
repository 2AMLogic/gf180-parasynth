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

    R3 is tested two ways, because the first way was itself defeated by a
    one-word variant of the input it was written to catch (PR #523 review):
    the original rule asked only whether `unit` held the manifest's verbose
    `unknown_marker`, so `unit: "unknown"` or `unit: "TBD"` with
    `lineage_status: "documented"` passed clean -- and with
    `roles: ["held-out-validation"]` a pack with no established unit backed a
    verdict with the checker green. So:

      * R3a DENY -- `unit` may not be any recognised way of saying "I have not
        established this" (`UNESTABLISHED_TOKENS` below, plus the canonical
        marker). A deny-list is defeatable by the next synonym, which is why
        it is only half the rule; and
      * R3b POSITIVE IDENTITY -- a `documented` unit must actually IDENTIFY a
        machine: a serial number (the form every documented pack here uses),
        or, when identity is pinned some other way, a written
        `unit_identity_why` saying what pins it. A positive assertion cannot
        be routed around by a phrasing the deny-list has not met yet, which is
        the half that closes the hole.

    `unit_identity_why` is the residual weakness and it is stated rather than
    hidden: an author can write a sentence there and satisfy R3b without a
    serial. What the rule guarantees is that doing so is a visible, written
    claim in the diff, not a blank field that reads as established.
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
    (It is a substring test and nothing more: a name mentioned anywhere in the
    document satisfies it, including inside a sentence saying the group is
    unused. It catches rename drift, not staleness of what the document says
    about the name.)
R10 Exactly three groups and exactly three targets, with the expected names.
R11 Every pack's `evidence` list is non-empty and names paths that exist.

WHAT IT CANNOT DO, stated so nobody reads more into a green run
---------------------------------------------------------------
* It cannot tell what a probe actually READS. A pack could be listed with no
  roles and still be loaded by a script. Nothing in this repository can see
  that; the groups are a declaration of intent that a reviewer checks against
  the diff, and R5 only has teeth because `roles` is also where a reader looks.
* `UNESTABLISHED_TOKENS` is a deny-list and a deny-list is always one synonym
  behind. A field whose value is an unestablished fact phrased as a sentence
  ("we never worked this out") reads as established to R1. The one field where
  that mattered -- the `unit` of a `documented` pack -- is therefore ALSO
  guarded positively (R3b), and the honest statement about every other field
  is that R1 reports the phrasings it knows and nothing more.
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
    3   REFUSED: the apparatus could not run at all (manifest or document
        missing, malformed, or missing a top-level key). Distinct from 1 and 2
        on purpose -- "I cannot answer" is not "the corpus has gaps", and a
        caller branching on the exit code must be able to tell them apart.
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

#: Fields whose value comes from a CONTROLLED VOCABULARY, where a word that
#: looks like a placeholder is a legitimate value. `lineage_status: "unknown"`
#: is R2's own vocabulary saying "nothing about this lineage is established" --
#: a decision, not an unfilled field -- so R1 must not report it OPEN. R2
#: guards these; the unestablished-ness predicate does not apply.
VOCABULARY_FIELDS = frozenset({"lineage_status"})

#: The document's pack table, delimited so the parse cannot wander into another
#: table that happens to be nearby.
PACK_REGION = (re.compile(r"<!--\s*corpus-lineage:packs\s*-->"),
               re.compile(r"<!--\s*/corpus-lineage:packs\s*-->"))

#: Ways of writing "I have not established this" that are NOT the manifest's
#: canonical `unknown_marker`. The marker is the form `open` reports and the
#: form this document asks authors for; these are what an author reaches for
#: instead, and before PR #523's review every one of them read as an
#: established value. Matched against the WHOLE normalised field (and against
#: the head of a `--`-separated field, which is how the canonical marker is
#: built), never as a substring -- "None. Loops at 120-128 bpm" and
#: "no -- never run" are stated RESULTS and must stay established.
#:
#: Deliberately NOT in here: `none`, `n/a`, `not applicable`, `no`,
#: `not documented`, `undocumented`. Each is a statement about the world --
#: no settings ship, the vendor published no chain, the test does not apply --
#: and classifying them as unfilled fields would both be wrong and pressure
#: the next author into deleting an honest answer. `unit` is immune to that
#: judgement call either way, because R3b asks it a positive question.
UNESTABLISHED_TOKENS = frozenset({
    "unknown", "unknowns", "tbd", "tba", "todo", "to do", "fixme",
    "unestablished", "not established", "not yet established",
    "not determined", "undetermined", "not yet determined",
    "to be determined", "to be established", "to be confirmed",
    "not known", "not yet known", "no idea", "dunno",
    "placeholder", "fill this in", "fill in", "pending",
    "?", "??", "???", "-", "--", "---", "x", "xx", "xxx", "",
})

#: What a `documented` unit must actually carry (R3b): a serial number. Every
#: documented pack in this corpus is identified this way -- Fischer s/n 103852,
#: Legowelt s/n 5529 -- so the positive form is satisfiable on the committed
#: state, which is the test an unsatisfiable gate fails.
SERIAL_RE = re.compile(r"(?:serial|s\s*/\s*n|s\.\s*n\.)\D{0,20}(\d{3,})", re.I)


class Refused(Exception):
    """A precondition failed: say so instead of producing findings."""


def _normalise(value: object) -> str:
    """Lowercase, trimmed, stripped of quoting and trailing punctuation."""
    s = str(value if value is not None else "").strip()
    s = s.strip("`\"'*").strip()
    s = s.rstrip(".!:;,").strip()
    return " ".join(s.lower().split())


def is_unestablished(value: object, marker: str) -> bool:
    """True when `value` is any recognised way of saying "not established yet".

    The canonical `marker` (exactly, or as the head of a longer string, which
    is how the manifest writes it) plus `UNESTABLISHED_TOKENS`, matched whole
    rather than as a substring. The `--` head test is what makes
    `"TBD -- operator must fill this in"` behave like the canonical marker
    instead of like a sentence.
    """
    norm = _normalise(value)
    mark = _normalise(marker)
    if mark and (norm == mark or norm.startswith(mark)):
        return True
    if norm in UNESTABLISHED_TOKENS:
        return True
    head = _normalise(norm.split("--")[0])
    return head in UNESTABLISHED_TOKENS


def names_a_unit(unit: object) -> bool:
    """True when `unit` carries a serial number -- a positive identity (R3b)."""
    return bool(SERIAL_RE.search(str(unit or "")))


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


def _no_justification(pack: dict, key: str, marker: str) -> bool:
    """True when a required written justification is absent OR a placeholder.

    The same hole R3 had, one rule over: R5/R6/R7 each demand a sentence, and
    before PR #523's review `weak_evidence_why: "TBD"` satisfied all three.
    A placeholder is not a justification -- and unlike R1's fields this is
    FALSE, not OPEN: the field records a decision the author has already made
    by assigning the role, so there is nothing for an operator to fill in.
    """
    val = str(pack.get(key, "") or "").strip()
    return not val or is_unestablished(val, marker)


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
            val = str(t.get(key, "") or "").strip()
            if not val:
                out.append(_finding("R10", "FALSE", f"target {name} has no {key}"))
            elif is_unestablished(val, unknown):
                out.append(_finding("R10", "FALSE",
                                    f"target {name}: {key} is {val!r}, which is a placeholder, "
                                    f"not a statement of what the target is or what addresses it"))

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
            elif key in VOCABULARY_FIELDS:
                continue                      # R2's vocabulary, not a free-text field
            elif is_unestablished(val, unknown):
                marked_unknown.append(key)
                canonical = "" if (val == unknown or val.startswith(unknown)) \
                    else f" (written as {val!r}; the canonical form is the manifest's unknown_marker)"
                out.append(_finding("R1", "OPEN",
                                    f"{pid}: {key} is not established yet{canonical}"))

        # R2 -- lineage vocabulary
        status = p.get("lineage_status")
        if status not in LINEAGE_STATUS:
            out.append(_finding("R2", "FALSE",
                                f"{pid}: lineage_status {status!r} not in {LINEAGE_STATUS}"))

        # R3 -- the input that defeats R2: documented, with no established unit.
        # Two halves: R3a denies every recognised phrasing of "not established"
        # (not just the canonical marker, which is the hole PR #523's review
        # found), and R3b asks the positive question a deny-list cannot answer.
        if status == "documented":
            unit_val = str(p.get("unit", "") or "").strip()
            identity_why = str(p.get("unit_identity_why", "") or "").strip()
            if "unit" in marked_unknown:
                out.append(_finding("R3", "FALSE",
                                    f"{pid}: lineage_status is 'documented' but the unit itself "
                                    f"is not established (unit is {unit_val!r})"))
            elif not names_a_unit(unit_val) and (
                    not identity_why or is_unestablished(identity_why, unknown)):
                out.append(_finding("R3", "FALSE",
                                    f"{pid}: lineage_status is 'documented' but the unit names no "
                                    f"serial number ({unit_val!r}) and no unit_identity_why says "
                                    f"what pins the identity instead"))

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
                    and _no_justification(p, "weak_evidence_why", unknown)):
                out.append(_finding("R5", "FALSE",
                                    f"{pid}: lineage_status is 'claimed' and it is in "
                                    f"{CLAIMED_OK_WITH_REASON} with no weak_evidence_why saying "
                                    f"why the calibration does not depend on the unestablished unit"))
        if len(roles) > 1 and _no_justification(p, "role_overlap_why", unknown):
            out.append(_finding("R6", "FALSE",
                                f"{pid}: in {len(roles)} groups with no role_overlap_why naming "
                                f"the separation in force instead"))
        if not roles and _no_justification(p, "group_why", unknown):
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
        # 3, not 2: "the apparatus could not run" must be distinguishable from
        # "the corpus has gaps" by a caller that only sees the exit code.
        print(f"REFUSED: {e}")
        return 3

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
