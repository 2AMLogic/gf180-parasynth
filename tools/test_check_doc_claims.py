"""Controls for the claim-to-evidence checker.

A checker's only failure mode that matters is a false green -- `tools/run_all.py`
has its own suite for the same reason. So these are mostly injected defects: a
claim pointing at a test that does not exist, a claim whose backing test fails,
a tracked-defect claim whose defect has been fixed. Each must turn something
red, and the three colours must stay distinct.

THE FIXTURES ARE REAL. The backing tests are a real module that a real pytest
subprocess really collects and runs, written under `build/` (gitignored) so the
nodeids are repo-relative exactly as a marker's would be. Asserting against a
mocked pytest would test our idea of pytest rather than pytest -- which is
mechanism 3 in docs/failure-modes.md, the verified artefact not being the
shipped one.
"""
from __future__ import annotations

import pathlib
import shutil
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import check_doc_claims as cdc                                      # noqa: E402

ROOT = cdc.ROOT

# Backing tests for the fixture documents. One of each outcome the verdict
# table in docs/claim-markers.md has a row for.
FIXTURE_MODULE = '''\
import pytest


def test_fixture_passes():
    assert True


def test_fixture_fails():
    assert False, "this fixture fails on purpose -- it is a control"


@pytest.mark.skip(reason="fixture: apparatus deliberately unavailable")
def test_fixture_is_skipped():
    assert True


@pytest.mark.xfail(reason="fixture: a tracked defect", strict=False)
def test_fixture_xfails():
    assert False


@pytest.mark.parametrize("n", [1, 2, 3])
def test_fixture_is_parametrised(n):
    assert n > 0
'''


@pytest.fixture(scope="module")
def fixtures():
    """A directory under the repo root holding the backing test module.

    Under the ROOT because a marker's nodeid is repo-relative and pytest is
    invoked from the ROOT; a tmp_path outside the tree could not be named by
    one. Under `build/`, which is gitignored, so it cannot be committed by
    accident.
    """
    base = ROOT / "build"
    base.mkdir(exist_ok=True)
    d = pathlib.Path(tempfile.mkdtemp(prefix="claim-fixtures-", dir=base))
    (d / "test_claim_fixture_backing.py").write_text(FIXTURE_MODULE)
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def write_doc(d: pathlib.Path, markers: dict[str, str]) -> pathlib.Path:
    """A fixture document: one prose line plus one marker per entry."""
    lines: list[str] = ["# fixture document", ""]
    for label, body in markers.items():
        lines += [f"The claim called {label}.", f"<!-- claim: {body} note={label} -->", ""]
    p = d / "doc.md"
    p.write_text("\n".join(lines))
    return p


def verdicts(doc: pathlib.Path) -> dict[str, cdc.Claim]:
    """Run the real checker over one document, keyed by each claim's note=."""
    out: dict[str, cdc.Claim] = {}
    for c in cdc.check([doc], sys.executable):
        key = c.attrs.get("note") or f"unlabelled-{c.line}"
        out[key] = c
    return out


# --------------------------------------------------------------------------
# test= claims. One checker run covers every row of the verdict table; each
# named case below asserts one row, so a regression names itself.
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def claim_verdicts(fixtures):
    rel = (fixtures / "test_claim_fixture_backing.py").relative_to(ROOT).as_posix()
    doc = write_doc(fixtures, {
        "passing":        f"test={rel}::test_fixture_passes",
        "failing":        f"test={rel}::test_fixture_fails",
        "missing-file":   "test=tools/test_no_such_file_exists.py::test_x",
        "missing-node":   f"test={rel}::test_fixture_does_not_exist",
        "skipped":        f"test={rel}::test_fixture_is_skipped",
        "xfailed":        f"test={rel}::test_fixture_xfails",
        "parametrised":   f"test={rel}::test_fixture_is_parametrised",
        "defect-firing":  f"test={rel}::test_fixture_fails expect=fail",
        "defect-fixed":   f"test={rel}::test_fixture_passes expect=fail",
        "defect-xfailed": f"test={rel}::test_fixture_xfails expect=fail",
        "defect-skipped": f"test={rel}::test_fixture_is_skipped expect=fail",
    })
    return verdicts(doc)


def test_a_claim_backed_by_a_passing_test_is_ok(claim_verdicts):
    c = claim_verdicts["passing"]
    assert c.status == cdc.OK, c.detail
    assert "passed" in c.detail


def test_a_claim_whose_backing_test_fails_is_stale(claim_verdicts):
    """The headline case: the prose says a thing works and the tree disagrees."""
    c = claim_verdicts["failing"]
    assert c.status == cdc.STALE, c.detail
    assert "expects it to pass" in c.detail


def test_a_claim_naming_a_test_that_does_not_exist_is_refused(claim_verdicts):
    """REFUSED, not STALE. A deleted test file is not evidence the claim is
    false -- it is the absence of any evidence, and the two must not be
    reported in the same voice."""
    c = claim_verdicts["missing-file"]
    assert c.status == cdc.REFUSED, c.detail
    assert "does not exist" in c.detail


def test_a_nodeid_pytest_cannot_collect_is_refused_and_does_not_poison_the_rest(
        claim_verdicts):
    """pytest resolves every argument before running ANY of them, so one bad
    nodeid would make the whole invocation a usage error. The checker collects
    first for exactly this reason; if it stopped doing so, the claims below
    would all go REFUSED together and the repository would look broken."""
    assert claim_verdicts["missing-node"].status == cdc.REFUSED
    assert "does not collect" in claim_verdicts["missing-node"].detail
    assert claim_verdicts["passing"].status == cdc.OK


def test_a_skipped_test_backs_nothing(claim_verdicts):
    """The decision the issue asked to be made explicitly, both polarities.

    A skipped check looks exactly like a passing one. If a skip could back a
    claim, an apparatus that refused to run would silently certify every
    sentence citing it.
    """
    for key in ("skipped", "defect-skipped"):
        c = claim_verdicts[key]
        assert c.status == cdc.REFUSED, (key, c.detail)
        assert "did not run" in c.detail


def test_an_xfailed_test_backs_a_tracked_defect_and_contradicts_a_plain_claim(
        claim_verdicts):
    assert claim_verdicts["xfailed"].status == cdc.STALE
    assert claim_verdicts["defect-xfailed"].status == cdc.OK


def test_a_tracked_defect_claim_is_backed_by_a_failing_test(claim_verdicts):
    c = claim_verdicts["defect-firing"]
    assert c.status == cdc.OK, c.detail
    assert "failed" in c.detail


def test_a_tracked_defect_that_now_passes_is_stale(claim_verdicts):
    """"Passing-when-cited-as-failing" -- the case in this issue's title, and
    the one a checker that only ran tests for green would miss entirely."""
    c = claim_verdicts["defect-fixed"]
    assert c.status == cdc.STALE, c.detail
    assert "expects it to fail" in c.detail


def test_a_parametrised_nodeid_is_backed_only_if_every_case_ran(claim_verdicts):
    c = claim_verdicts["parametrised"]
    assert c.status == cdc.OK, c.detail
    assert "3 cases" in c.detail


# --------------------------------------------------------------------------
# grep= / absent=
# --------------------------------------------------------------------------

def test_grep_and_absent_claims_read_the_real_tree(fixtures):
    v = verdicts(write_doc(fixtures, {
        "present":  'grep="^srccheck:" in=fpga/Makefile',
        "gone":     'grep="a string that is certainly not in this makefile" in=fpga/Makefile',
        "still-absent": 'absent="zzz[q]qq_absent_symbol" in=tools/*.py',
        "now-done": 'absent="^def main" in=tools/check_doc_claims.py',
    }))
    assert v["present"].status == cdc.OK, v["present"].detail
    assert v["gone"].status == cdc.STALE, v["gone"].detail
    assert v["still-absent"].status == cdc.OK, v["still-absent"].detail
    # The motivating case: a "not yet" line whose thing now exists.
    assert v["now-done"].status == cdc.STALE, v["now-done"].detail
    assert "now EXISTS" in v["now-done"].detail


def test_a_glob_into_a_directory_that_does_not_exist_is_refused(fixtures):
    """The guard against the false green an `absent=` claim invites: a typo'd
    path matches nothing, and "nothing matched" is indistinguishable from a
    genuine absence unless the directory is known to exist."""
    v = verdicts(write_doc(fixtures, {
        "typo-absent": 'absent="anything" in=tolos/*.py',
        "typo-grep":   'grep="anything" in=tolos/*.py',
        "escape":      'grep="anything" in=/etc/*.conf',
    }))
    assert all(c.status == cdc.REFUSED for c in v.values()), {k: c.detail for k, c in v.items()}
    assert "does not exist" in v["typo-absent"].detail
    assert "repo-relative" in v["escape"].detail


def test_a_glob_matching_zero_files_is_refused_for_both_polarities(fixtures):
    """The directory check above is not enough: the typo can be in the FILENAME
    pattern rather than the directory, and then the base dir exists while the
    glob still matches nothing.

    `absent=` reported OK here -- a green verdict derived from reading zero
    files, which is the one outcome this tool exists to make impossible, and the
    polarity where it is least detectable: for an absence claim "nothing
    matched" and "nothing was examined" produce identical output. Both kinds
    must REFUSE identically on an empty file set.
    """
    v = verdicts(write_doc(fixtures, {
        "zero-absent": 'absent="anything" in=tools/*.pyy',
        "zero-grep":   'grep="anything" in=tools/*.pyy',
        "zero-multi":  'absent="anything" in=tools/*.pyy,docs/*.mdd',
    }))
    assert all(c.status == cdc.REFUSED for c in v.values()), {k: c.detail for k, c in v.items()}
    for k in v:
        assert "matched no files" in v[k].detail, v[k].detail


# --------------------------------------------------------------------------
# commit=
# --------------------------------------------------------------------------

def test_a_commit_claim_checks_ancestry_not_mere_existence(fixtures):
    head = cdc.git("rev-parse", "HEAD")
    assert head, "this control needs a git checkout"
    v = verdicts(write_doc(fixtures, {
        "ancestor": f"commit={head}",
        "nonesuch": "commit=0000000000000000000000000000000000000000",
    }))
    assert v["ancestor"].status == cdc.OK
    assert "ancestor" in v["ancestor"].detail
    assert v["nonesuch"].status == cdc.STALE
    assert "not in this repository" in v["nonesuch"].detail


# --------------------------------------------------------------------------
# covers= -- "does the claim predate the file it describes"
# --------------------------------------------------------------------------

def test_a_claim_older_than_the_file_it_covers_is_stale(monkeypatch):
    """Commit-time ordering, both directions, without depending on this
    repository's actual history: the ordering is the behaviour under test."""
    times = {}
    monkeypatch.setattr(cdc, "commit_time", lambda rel: times[rel])

    def verdict(doc_t: int, cov_t: int) -> cdc.Claim:
        c = cdc.Claim(doc=ROOT / "docs" / "failure-modes.md", line=1,
                      raw="", prose="")
        c.attrs = {"covers": "Makefile"}
        times["docs/failure-modes.md"], times["Makefile"] = doc_t, cov_t
        c.ok("evidence found")
        cdc.check_covers(c)
        return c

    fresh = verdict(doc_t=2000, cov_t=1000)
    assert fresh.status == cdc.OK

    stale = verdict(doc_t=1000, cov_t=2000)
    assert stale.status == cdc.STALE
    assert "changed after this claim" in stale.detail


def test_covers_refuses_rather_than_guesses_when_it_cannot_order(monkeypatch):
    monkeypatch.setattr(cdc, "commit_time", lambda rel: 0)
    c = cdc.Claim(doc=ROOT / "docs" / "failure-modes.md", line=1, raw="", prose="")
    c.attrs = {"covers": "Makefile"}
    c.ok("evidence found")
    cdc.check_covers(c)
    assert c.status == cdc.REFUSED and "cannot order" in c.detail


def test_covers_naming_a_path_that_does_not_exist_is_refused():
    c = cdc.Claim(doc=ROOT / "docs" / "failure-modes.md", line=1, raw="", prose="")
    c.attrs = {"covers": "no/such/file.txt"}
    c.ok("evidence found")
    cdc.check_covers(c)
    assert c.status == cdc.REFUSED


def test_an_edited_document_counts_as_changed_now(monkeypatch):
    """Otherwise the gate is unsatisfiable in exactly the working tree where
    someone is fixing a stale claim: the fresh edit is uncommitted, so the
    document reads as older than everything it covers. Three unsatisfiable
    gates were written here in one day; this is the check against a fourth."""
    monkeypatch.setattr(cdc, "git",
                        lambda *a: " M docs/x.md" if a[0] == "status" else "1")
    assert cdc.commit_time("docs/x.md") > 1_700_000_000


# --------------------------------------------------------------------------
# marker syntax -- an unreadable marker is REFUSED, never silently skipped
# --------------------------------------------------------------------------

@pytest.mark.parametrize("body,fragment", [
    ("",                                   "no claim kind"),
    ("issue=42",                           "no claim kind"),
    ("test=a.py::b grep=x in=Makefile",    "more than one claim kind"),
    ("test=",                              "is empty"),
    ("tset=a.py::b",                       "no claim kind"),
    ("test=a.py::b tset=1",                "unknown key"),
    ("grep=x",                             "needs in="),
    ("absent=x",                           "needs in="),
    ("grep=x in=Makefile expect=fail",     "applies only to test="),
    ("test=a.py::b expect=maybe",          "is not pass or fail"),
    ('test="unbalanced',                   "unparseable"),
])
def test_a_marker_that_cannot_be_understood_is_refused(body, fragment):
    c = cdc.Claim(doc=ROOT / "x.md", line=1, raw=body, prose="")
    cdc.validate(c)
    assert c.status == cdc.REFUSED, (body, c.detail)
    assert fragment in c.detail, (body, c.detail)


def test_a_marker_can_carry_a_value_with_spaces():
    c = cdc.Claim(doc=ROOT / "x.md", line=1, raw='grep="--inject SPI_ADDR7" in=Makefile',
                  prose="")
    cdc.validate(c)
    assert c.status == "" and c.value == "--inject SPI_ADDR7"


def test_a_marker_in_a_code_block_or_backticks_is_an_example_not_a_claim(tmp_path):
    """docs/claim-markers.md is nothing but examples. Without this the
    convention's own documentation would be checked as a list of assertions --
    and a checker that cannot read its own specification is not one to trust."""
    doc = tmp_path / "d.md"
    doc.write_text("\n".join([
        "A real claim.",
        "<!-- claim: commit=abc -->",
        "",
        "```markdown",
        "<!-- claim: test=not/a/real/file.py::nope -->",
        "```",
        "",
        "Inline: `<!-- claim: grep=x -->` is documentation.",
        "",
    ]))
    found = cdc.find_claims(doc)
    assert [c.raw for c in found] == ["commit=abc"]


def test_markers_are_found_with_the_prose_they_back(tmp_path):
    doc = tmp_path / "d.md"
    doc.write_text("\n".join([
        "Some heading",
        "",
        "- The estimator suite exists.",
        "  <!-- claim: test=a.py::b -->",
        "Trailing form.  <!-- claim: commit=abc -->",
        "",
    ]))
    found = cdc.find_claims(doc)
    assert [c.line for c in found] == [4, 5]
    assert found[0].prose == "The estimator suite exists."
    assert found[1].prose == "Trailing form."


# --------------------------------------------------------------------------
# the exit-code contract, which is what `make verify` actually reads
# --------------------------------------------------------------------------

def test_exit_codes_keep_stale_refused_and_clean_distinct(fixtures):
    """0 / 1 / 2, and 2 means no evidence rather than no problem."""
    import subprocess
    rel = (fixtures / "test_claim_fixture_backing.py").relative_to(ROOT).as_posix()

    def run(markers: dict[str, str]) -> int:
        doc = write_doc(fixtures, markers)
        return subprocess.run([sys.executable, str(ROOT / "tools" / "check_doc_claims.py"),
                               str(doc), "--quiet"],
                              cwd=ROOT, capture_output=True, text=True).returncode

    assert run({"a": f"test={rel}::test_fixture_passes"}) == 0
    assert run({"a": f"test={rel}::test_fixture_fails"}) == 1
    assert run({"a": f"test={rel}::test_fixture_is_skipped"}) == 2
    # A stale finding outranks a refusal: it is the actionable one. Both red.
    assert run({"a": f"test={rel}::test_fixture_fails",
                "b": f"test={rel}::test_fixture_is_skipped"}) == 1


def test_a_document_with_no_markers_at_all_is_refused_not_passed(fixtures):
    """A checker with nothing to check must not report a clean bill of health.
    That is the same shape as a check that cannot run looking like one that
    passed, which `tools/check_workflows.py` exists to prevent one level up."""
    import subprocess
    doc = fixtures / "empty.md"
    doc.write_text("# nothing to see here\n")
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "check_doc_claims.py"),
                        str(doc)], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 2
    assert "REFUSED" in r.stderr


# --------------------------------------------------------------------------
# and the document this was commissioned for
# --------------------------------------------------------------------------

def test_failure_modes_carries_markers_and_they_all_hold():
    """The concrete case from issue #223. If this goes red, either the tree
    moved or docs/failure-modes.md is stale again -- both are findings."""
    doc = ROOT / "docs" / "failure-modes.md"
    claims = cdc.check([doc], sys.executable)
    assert len(claims) >= 10, "the status list lost its markers"
    bad = [(c.where, c.status, c.detail) for c in claims if c.status != cdc.OK]
    assert not bad, bad
