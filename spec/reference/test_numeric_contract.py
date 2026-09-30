"""The contract's revision NUMBERS, checked the way its tables already are.

Twice in this repository two different normative changes were given the same
revision number, and both times the only thing that caught it was a person
reading the document: drift (DR 0019) and polyBLAMP (DR 0017) both merged
calling themselves "revision 12" (#293), and before that the clap's final
strike was written as "revision 11", colliding with the tom rebalance. A
duplicate number makes every later statement of the form "revision N moved
KIT808" ambiguous, and `fpga/uart_host.py`'s `IMAGE_REVISION` -- which decides
which kit a board is sent -- is keyed on exactly those numbers.

    .venv/bin/python -m pytest spec/reference/test_numeric_contract.py -q

What is checked, against `spec/NUMERIC-CONTRACT.md` as committed:

  * every section-18 entry has a distinct revision number;
  * the revision the header states (line 3) has a section-18 entry;
  * the header is not BEHIND section 18 -- a new entry with a stale header is
    the other half of the same drift.

Each of those has an injected-defect control below (rule 2 of
`docs/verification-rules.md`): a mutated copy of the real document is built
in memory and the check is required to go red on it, because a check nobody
has watched fail is not evidence. There is a fourth control for the failure
mode peculiar to a parser -- matching NOTHING and passing vacuously. The
parser REFUSES (raises `Refused`, which is neither pass nor fail) when it
cannot find section 18 or finds no entries in it, and the control renames the
heading and requires that refusal.
"""
import os
import re

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
CONTRACT = os.path.normpath(os.path.join(HERE, os.pardir, "NUMERIC-CONTRACT.md"))

# `## 18. Revision history` ... up to the next heading of any level (the
# appendices). Not "to end of file": Appendix G quotes register writes, and a
# line there must never be read as a revision entry.
_SECTION_18 = re.compile(r"^## 18\. Revision history[^\n]*\n(.*?)(?=^#)",
                         re.M | re.S)
# `- **Rev 14 (2026-09-26)** — ...`
_ENTRY = re.compile(r"^- \*\*Rev (\d+) \((\d{4}-\d{2}-\d{2})\)\*\*", re.M)
# line 3: `**Revision 14 — 2026-09-26 — status: PROPOSED. Not ratified.**`
_HEADER = re.compile(r"^\*\*Revision (\d+) [—-] (\d{4}-\d{2}-\d{2}) [—-] status:", re.M)

# The revisions section 18 documents today. Append-only: a revision that has
# been published must not silently lose its entry, and a RENUMBER of an
# existing one (which is what #293 had to do) must be a deliberate edit here
# rather than something the suite absorbs quietly.
HISTORICAL = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14}
# Revision 9 (DR 0011's tuning polynomial, DR 0012's EXP_ROM65) was referred to
# by the contract and by `test_tables.py` -- "against revision 9's pins" --
# through five later revisions without ever getting its own section-18
# paragraph. #301 wrote that paragraph from the evidence in those two places,
# so 9 joins HISTORICAL above and this set is now EMPTY: any gap at all in
# 1..max is a renumber that dropped a change on the floor.
KNOWN_MISSING = set()


class Refused(Exception):
    """The document is not in a state this check can read.

    Distinct from a failure: a renamed heading or a reformatted bullet means
    the check did not run, and saying so is the only honest answer. Returning
    "no duplicates" from a parser that matched nothing would be the exact
    false green `docs/verification-rules.md` rule 1 is about.
    """


def read_contract():
    with open(CONTRACT, encoding="utf-8") as fh:
        return fh.read()


def section_18(text):
    """The revision-history section's body, or REFUSE."""
    m = _SECTION_18.search(text)
    if not m:
        raise Refused("no `## 18. Revision history` section found")
    return m.group(1)


def revision_entries(text):
    """[(number, date), ...] for every section-18 entry, or REFUSE.

    The minimum is a precondition, not a style rule: this document has had a
    revision history since revision 2, so a parse that finds one or none means
    the format moved, not that the history shrank.
    """
    entries = [(int(n), d) for n, d in _ENTRY.findall(section_18(text))]
    if len(entries) < 2:
        raise Refused(
            "section 18 parsed to %d entries -- the `- **Rev N (date)**` form "
            "this check reads is gone, so nothing was checked" % len(entries))
    return entries


def header_revision(text):
    """(number, date) from the header's status line, or REFUSE."""
    m = _HEADER.search(text)
    if not m:
        raise Refused("no `**Revision N — date — status: ...**` header line")
    return int(m.group(1)), m.group(2)


def problems(text):
    """Every numbering defect in `text`, as messages. Empty list == clean."""
    entries = revision_entries(text)
    head, _ = header_revision(text)
    found = []

    seen = {}
    for number, date in entries:
        if number in seen:
            found.append(
                "duplicate revision number %d: `Rev %d (%s)` and `Rev %d (%s)` "
                "are two different changes with one number"
                % (number, number, seen[number], number, date))
        else:
            seen[number] = date

    if head not in seen:
        found.append(
            "the header states revision %d, which has no section-18 entry"
            % head)
    elif head < max(seen):
        found.append(
            "the header states revision %d but section 18 goes up to %d -- "
            "the header is stale" % (head, max(seen)))
    return found


def _duplicate_injected(text):
    """Give the polyBLAMP entry drift's number again -- the exact #293 defect."""
    out = text.replace("- **Rev 13 (2026-09-26)** — **polyBLAMP",
                       "- **Rev 12 (2026-09-26)** — **polyBLAMP", 1)
    assert out != text, "the injection did not apply; update the control"
    return out


def _header_mismatch_injected(text):
    """Claim a revision in the header that section 18 never records."""
    head, date = header_revision(read_contract())
    out = text.replace("**Revision %d — %s — status:" % (head, date),
                       "**Revision %d — %s — status:" % (head + 1, date), 1)
    assert out != text, "the injection did not apply; update the control"
    return out


def _stale_header_injected(text):
    """Add a newer entry to section 18 and leave the header where it was."""
    head, date = header_revision(read_contract())
    marker = "## 18. Revision history\n\n"
    out = text.replace(
        marker,
        marker + "- **Rev %d (%s)** — an entry the header does not know about.\n\n"
        % (head + 1, date), 1)
    assert out != text, "the injection did not apply; update the control"
    return out


def _unreadable_injected(text):
    """Rename the section heading, as a restructure of the document would."""
    out = text.replace("## 18. Revision history",
                       "## 18. History of revisions", 1)
    assert out != text, "the injection did not apply; update the control"
    return out


def test_the_check_reads_the_revision_history_it_claims_to_read():
    """Before any verdict: the parser must actually see the entries. A regex
    that matches nothing reports "no duplicates" forever."""
    entries = revision_entries(read_contract())
    numbers = [n for n, _ in entries]
    assert len(numbers) >= len(HISTORICAL)
    assert HISTORICAL <= set(numbers), (
        "a published revision lost or changed its section-18 entry: missing "
        "%s. If a renumber was deliberate, edit HISTORICAL in this file and "
        "say why in the revision entry." % sorted(HISTORICAL - set(numbers)))
    head, _ = header_revision(read_contract())
    assert head == max(numbers)


def test_section_18_gives_each_revision_a_number_of_its_own():
    """The defect #293 was filed for: two normative changes, one number."""
    text = read_contract()
    dupes = [p for p in problems(text) if p.startswith("duplicate")]
    assert dupes == []


def test_the_header_revision_is_the_one_section_18_ends_on():
    """The other half: a header that names a revision section 18 does not
    record, or that lags behind an entry someone added."""
    assert problems(read_contract()) == []


def test_control_an_injected_duplicate_revision_number_turns_this_red():
    """Rule 2. Clean input passes (asserted above); with polyBLAMP renumbered
    back to 12 -- literally what merged on main before #293 -- the check must
    name the collision."""
    text = read_contract()
    assert problems(text) == []
    found = problems(_duplicate_injected(text))
    assert any(p.startswith("duplicate revision number 12") for p in found), found


def test_control_a_header_revision_with_no_entry_turns_this_red():
    """Rule 2, second failure mode: the header bumped without a paragraph."""
    text = read_contract()
    head, _ = header_revision(text)
    found = problems(_header_mismatch_injected(text))
    assert any("header states revision %d, which has no section-18 entry"
               % (head + 1) in p for p in found), found


def test_control_a_stale_header_turns_this_red():
    """Rule 2, third failure mode: a new entry the header never caught up to."""
    found = problems(_stale_header_injected(read_contract()))
    assert any("the header is stale" in p for p in found), found


def test_control_an_unreadable_document_is_refused_not_passed():
    """The failure mode of a parser rather than of a document. With the
    heading renamed the check cannot run, and REFUSED is the answer -- a
    green here would mean the suite reports "no duplicates" about a section
    it never found."""
    with pytest.raises(Refused):
        problems(_unreadable_injected(read_contract()))
    # ... and the same for a section whose entries no longer parse.
    text = read_contract()
    gutted = text.replace(section_18(text), "Nothing here.\n\n", 1)
    with pytest.raises(Refused):
        problems(gutted)


def test_revision_9_is_the_only_revision_with_no_entry():
    """Numbers 1..max with no paragraph. Revision 9 (DR 0011, DR 0012) is one
    today -- the contract cites "revision 9's pins" but never introduces it.
    A second gap means a renumber dropped a change on the floor."""
    numbers = {n for n, _ in revision_entries(read_contract())}
    gaps = set(range(1, max(numbers) + 1)) - numbers
    assert gaps == KNOWN_MISSING, (
        "revision-history gaps changed: %s (known: %s)"
        % (sorted(gaps), sorted(KNOWN_MISSING)))
