"""Controls for `tools/check_surge_waveform_comment.py`.

The checker's only failure mode that matters is a false green: a comment cell
that contradicts `docs/surge-waveform-mapping.txt` and is reported OK anyway.
So the shipped comment passing is ONE test here, and every other test is a
defect deliberately injected into a copy of the block that must turn it red --
including the exact six cells issue #271 was filed about, kept as a fixture so
the historical wrong answer stays runnable rather than merely described.

The three outcomes are distinguished on purpose. `REFUSED` (exit 2) means the
comparison could not be made; it is asserted separately from `STALE` (exit 1),
because a checker that answers OK when it parsed nothing is the instrument this
repository keeps having to un-build.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import check_surge_waveform_comment as ck                           # noqa: E402
import check_workflows as cw                                        # noqa: E402

REPO = ck.REPO
RIGS = ck.RIGS
SWEEP = ck.SWEEP

SWEEP_TEXT = SWEEP.read_text()
BLOCK = ck.read_block(RIGS)


def _sub(lines, old, new):
    """The block with one substring replaced, asserting it was there. A control
    that silently edits nothing is a control that proves nothing."""
    joined = "\n".join(lines)
    assert joined.count(old) == 1, f"{old!r} appears {joined.count(old)} times"
    return joined.replace(old, new).split("\n")


# ---------------------------------------------------------------------------
# the shipped state
# ---------------------------------------------------------------------------

def test_the_shipped_comment_reproduces_the_sweep():
    assert ck.check(BLOCK, SWEEP_TEXT) == []


def test_the_shipped_comment_has_a_row_for_every_run_the_sweep_measured():
    """A cell count is not evidence of correctness, but a cell count that
    disagrees with the sweep's run count means the table is not the sweep."""
    assert set(ck.parse_comment(BLOCK)) == set(ck.parse_sweep(SWEEP_TEXT))
    assert len(ck.parse_comment(BLOCK)) == 18


def test_the_repository_no_longer_cites_the_file_that_never_existed():
    citing = set(ck.dead_citations(REPO)) - set(ck.DEAD_CITATION_EXEMPT)
    assert citing == set(), f"still citing {ck.DEAD_CITATION}: {sorted(citing)}"


def test_the_exemption_list_is_not_a_hole():
    """Every exempt path must exist and must still name the dead citation, or
    the exemption has outlived its reason and is just a blind spot."""
    for path in ck.DEAD_CITATION_EXEMPT:
        assert (REPO / path).exists(), path
        assert ck.DEAD_CITATION in (REPO / path).read_text(), path


def test_this_checker_is_invoked_by_a_ci_job_and_not_only_by_make():
    """"A gate nothing invokes fails exactly the same way" (rungs.yml), applied
    to this checker itself -- the same guard `tools/test_manifest.py` carries for
    its own wiring, for the same reason.

    As first written, this checker and these controls were reachable only through
    the broad `pytest ... tools/ ...` in `make verify` / `make verify-full` and
    through `make controls`, and **no workflow invokes any of those three**:
    `nightly.yml`'s controls job is hand-listed steps, not `make controls`. So
    the comment in `model/reference_rigs.py` asserted that the table and
    `docs/surge-waveform-mapping.txt` "cannot drift silently again" while nothing
    on a pull request re-derived it -- a claim without its apparatus, which is
    the defect issue #271 was itself filed about. This test fails if that wiring
    is removed, rather than letting the comment go quietly false again.
    """
    import yaml

    workflow = yaml.safe_load((REPO / ".github/workflows/rungs.yml").read_text())

    blocks, reason = cw.step_would_block_pull_request(
        workflow, "python", "python tools/check_surge_waveform_comment.py")
    assert blocks, (
        "rungs.yml's `python` job no longer runs "
        "tools/check_surge_waveform_comment.py in a way that can block a pull "
        f"request -- the reference_rigs.py comment's drift guarantee has no "
        f"CI apparatus, and `make verify` is invoked by no workflow: {reason}")

    # The CONTROLS are no longer named in rungs.yml, and that is the fix rather
    # than the regression (#404). Naming test files in a workflow made the set
    # CI runs and the set that exists two lists that could drift, and they had:
    # 7 named against 90 present, so 83 files -- controls included -- ran only
    # on a build box. The `tools` job now collects the whole directory, which is
    # a strictly wider guarantee than this assertion used to make.
    #
    # What must still hold is the property this test was written for: something
    # on the PULL REQUEST exercises these controls. So assert the directory run,
    # with enough of the command to be specific -- `pytest tools/ -q` as one
    # string does not match a single-file or a `-k`-narrowed invocation, which
    # would collect these controls without running them.
    blocks, reason = cw.step_would_block_pull_request(
        workflow, "tools", "python -m pytest tools/ -q")
    assert blocks, (
        "rungs.yml's `tools` job no longer collects tools/ as a directory in a "
        "way that can block a pull request -- an unexercised checker measures "
        f"the checker, not the comment: {reason}")


# ---------------------------------------------------------------------------
# the defect issue #271 was filed about, as a control
# ---------------------------------------------------------------------------

#: The six cells the pre-#271 comment got wrong, written in the table format
#: this checker reads. `74ce6a03` wrote them as prose in a two-column table;
#: what is preserved here is the CLAIM each cell made, which is what has to be
#: caught. The worst is 1.000 / 50 %: the comment named a waveform ("a saw at
#: 2*f0: the fundamental is cancelled") for the one run where the sweep found no
#: valid fundamental and identified nothing at all.
ISSUE_271_CELLS = {
    ("0.750", "50.00 %"): "8-midpoint-crossings",
    ("0.750", "25.00 %"): "6-midpoint-crossings",
    ("1.000", "25.00 %"): "6-midpoint-crossings",
    ("0.250", "25.00 %"): "pulse:25.1%",
    ("0.250", "50.00 %"): "rectangle+partial-saw",
    ("1.000", "50.00 %"): "saw-at-2f0",
}


def _issue_271_block():
    lines = list(BLOCK)
    for (shape, width), wrong in ISSUE_271_CELLS.items():
        row = [i for i, ln in enumerate(lines)
               if (m := ck.COMMENT_ROW.match(ln))
               and (m["shape"], m["width"]) == (shape, width)]
        assert len(row) == 1, (shape, width)
        i = row[0]
        m = ck.COMMENT_ROW.match(lines[i])
        lines[i] = lines[i][:m.start("verdict")] + wrong
    return lines


def test_the_issue_271_comment_is_caught():
    bad = ck.check(_issue_271_block(), SWEEP_TEXT, repo=None)
    assert len(bad) == len(ISSUE_271_CELLS), bad


def test_the_worst_issue_271_cell_is_caught_by_name():
    """1.000 / 50 %. The message must say the sweep named no waveform, not just
    that two strings differ -- that distinction is the whole finding."""
    bad = ck.check(_issue_271_block(), SWEEP_TEXT, repo=None)
    hit = [b for b in bad if b.startswith("shape 1.000 width 50.00 %")]
    assert len(hit) == 1, bad
    assert "NAMED NO WAVEFORM" in hit[0]
    assert "no component within 50 cents" in hit[0]


@pytest.mark.parametrize("cell", sorted(ISSUE_271_CELLS))
def test_each_issue_271_cell_is_caught_on_its_own(cell):
    """Not just the six together: each one alone must turn the check red, so a
    single silently-reverted cell cannot hide behind the others."""
    lines = list(BLOCK)
    row = [i for i, ln in enumerate(lines)
           if (m := ck.COMMENT_ROW.match(ln)) and (m["shape"], m["width"]) == cell]
    i = row[0]
    m = ck.COMMENT_ROW.match(lines[i])
    lines[i] = lines[i][:m.start("verdict")] + ISSUE_271_CELLS[cell]
    bad = ck.check(lines, SWEEP_TEXT, repo=None)
    assert len(bad) == 1, bad
    assert bad[0].startswith(f"shape {cell[0]} width {cell[1]}")


# ---------------------------------------------------------------------------
# injected defects, one per thing the checker claims to check
# ---------------------------------------------------------------------------

def test_naming_a_waveform_the_sweep_refused_is_caught():
    lines = _sub(BLOCK, "0.750    50.00 %   50.00 %    UNQUALIFIED",
                 "0.750    50.00 %   50.00 %    saw")
    bad = ck.check(lines, SWEEP_TEXT, repo=None)
    assert len(bad) == 1 and "NAMED NO WAVEFORM" in bad[0], bad


def test_refusing_a_waveform_the_sweep_identified_is_caught():
    lines = _sub(BLOCK, "0.500     0.00 %   50.00 %    saw",
                 "0.500     0.00 %   50.00 %    UNQUALIFIED")
    bad = ck.check(lines, SWEEP_TEXT, repo=None)
    assert len(bad) == 1 and "the comment refuses" in bad[0], bad


def test_a_wrong_duty_in_an_identified_pulse_is_caught():
    """`pulse:50.1%` at Shape 0.125 is not a typo for `pulse:50.0%`; the sweep
    measured a different duty and the comment may not round it."""
    lines = _sub(BLOCK, "0.125   -75.00 %   50.00 %    pulse:50.1%",
                 "0.125   -75.00 %   50.00 %    pulse:50.0%")
    bad = ck.check(lines, SWEEP_TEXT, repo=None)
    assert len(bad) == 1 and "pulse:50.1%" in bad[0], bad


def test_a_wrong_readback_string_is_caught():
    """The `reads` column is Surge's own text. A normalised Shape value means
    nothing without it, which is why it is compared and not ignored."""
    lines = _sub(BLOCK, "0.750    50.00 %   50.00 %    UNQUALIFIED",
                 "0.750   -50.00 %   50.00 %    UNQUALIFIED")
    bad = ck.check(lines, SWEEP_TEXT, repo=None)
    assert len(bad) == 1 and "Shape reads" in bad[0], bad


def test_an_omitted_row_is_caught():
    lines = [ln for ln in BLOCK
             if not ln.strip().startswith("#   1.000   100.00 %   50.00 %")]
    assert len(lines) == len(BLOCK) - 1
    bad = ck.check(lines, SWEEP_TEXT, repo=None)
    assert len(bad) == 1 and "the comment omits it" in bad[0], bad


def test_a_row_the_sweep_never_measured_is_caught():
    lines = _sub(BLOCK, "#   0.500     0.00 %   50.00 %    saw",
                 "#   0.500     0.00 %   50.00 %    saw\n"
                 "    #   0.437   -12.50 %   50.00 %    saw")
    bad = ck.check(lines, SWEEP_TEXT, repo=None)
    assert len(bad) == 1 and "never measured" in bad[0], bad


def test_a_duplicated_row_is_refused_not_compared():
    lines = _sub(BLOCK, "#   0.500     0.00 %   50.00 %    saw",
                 "#   0.500     0.00 %   50.00 %    saw\n"
                 "    #   0.500     0.00 %   50.00 %    UNQUALIFIED")
    with pytest.raises(ck.Refused, match="twice"):
        ck.check(lines, SWEEP_TEXT, repo=None)


def test_a_quoted_reason_that_is_not_in_the_sweep_is_caught():
    lines = _sub(BLOCK, '"harmonics above the fundamental at [2]"',
                 '"harmonics above the fundamental at [2, 3]"')
    bad = ck.check(lines, SWEEP_TEXT, repo=None)
    assert len(bad) == 1 and "which is not in" in bad[0], bad


def test_a_quote_wrapped_across_comment_lines_is_still_checked():
    """The un-wrapping that lets a long reason span lines must not become a
    blind spot: a wrong quote is caught wherever the line break falls."""
    lines = _sub(
        BLOCK,
        '"no component within 50 cents of the commanded 110.00 Hz\n'
        '    #                (the strongest nearby is 79.98 Hz, -551.7 cents)"',
        '"no component within 50 cents of the commanded 110.00 Hz\n'
        '    #                (the strongest nearby is 79.99 Hz, -551.7 cents)"')
    bad = ck.check(lines, SWEEP_TEXT, repo=None)
    assert len(bad) == 1 and "79.99 Hz" in bad[0], bad


def test_the_quote_check_is_file_scoped_not_row_scoped_and_says_so():
    """A KNOWN LIMIT, recorded because it was found by a control that expected
    the opposite. Swapping -551.7 for -551.8 in the dual-saw quote is NOT
    caught: -551.8 is the same refusal measured again in the file's controls
    section, so the edited quote is still verbatim FROM THE FILE -- just from a
    different line of it. The quote check answers `is this text in the sweep`,
    not `is this text on that run's line`; row-level accuracy is carried by the
    table, which is keyed by (Shape, Width). Do not read a green quote check as
    a claim about which row a reason came from."""
    lines = _sub(
        BLOCK,
        '"no component within 50 cents of the commanded 110.00 Hz\n'
        '    #                (the strongest nearby is 79.98 Hz, -551.7 cents)"',
        '"no component within 50 cents of the commanded 110.00 Hz\n'
        '    #                (the strongest nearby is 79.98 Hz, -551.8 cents)"')
    assert ck.check(lines, SWEEP_TEXT, repo=None) == []
    assert "-551.8 cents" in SWEEP_TEXT   # why it is not caught


def test_dropping_the_citation_is_caught():
    lines = _sub(BLOCK, "docs/surge-waveform-mapping.txt -- the stage's own output",
                 "the sweep -- the stage's own output")
    bad = ck.check(lines, SWEEP_TEXT, repo=None)
    assert len(bad) == 1 and "does not cite" in bad[0], bad


# ---------------------------------------------------------------------------
# REFUSED: the outcomes that must not look like a pass
# ---------------------------------------------------------------------------

def test_a_block_with_no_table_is_refused():
    lines = [ln for ln in BLOCK if not ck.COMMENT_ROW.match(ln)]
    with pytest.raises(ck.Refused, match="no parseable"):
        ck.check(lines, SWEEP_TEXT, repo=None)


def test_a_block_with_no_verbatim_marker_is_refused():
    lines = _sub(BLOCK, ck.VERBATIM_MARKER, "quoted from somewhere:")
    with pytest.raises(ck.Refused, match="verbatim section"):
        ck.check(lines, SWEEP_TEXT, repo=None)


def test_an_empty_verbatim_section_is_refused():
    at = [i for i, ln in enumerate(BLOCK) if ck.VERBATIM_MARKER in ln][0]
    lines = BLOCK[:at + 1] + ["    #"] + BLOCK[at + 1:]
    with pytest.raises(ck.Refused, match="quotes nothing"):
        ck.check(lines, SWEEP_TEXT, repo=None)


def test_an_unbalanced_quote_is_refused_not_paired_arbitrarily():
    lines = _sub(BLOCK, '"harmonics above the fundamental at [2]"',
                 '"harmonics above the fundamental at [2]')
    with pytest.raises(ck.Refused, match="odd number"):
        ck.check(lines, SWEEP_TEXT, repo=None)


def test_a_sweep_file_with_no_runs_is_refused():
    with pytest.raises(ck.Refused, match="nothing to check against"):
        ck.check(BLOCK, "SURGE'S WAVEFORM MAPPING\n\nno rows here at all\n",
                 repo=None)


def test_a_missing_block_marker_is_refused(tmp_path):
    p = tmp_path / "rigs.py"
    p.write_text("x = 1\n")
    with pytest.raises(ck.Refused, match="wanted 1"):
        ck.read_block(p)


def test_a_block_with_no_end_marker_is_refused(tmp_path):
    p = tmp_path / "rigs.py"
    p.write_text(f"# {ck.BLOCK_START}\n# table would go here\n")
    with pytest.raises(ck.Refused, match="no 'V_CLASSIC"):
        ck.read_block(p)


# ---------------------------------------------------------------------------
# the repository-wide search for the dead citation
# ---------------------------------------------------------------------------

def test_dead_citations_finds_a_tracked_file_that_cites_it(tmp_path):
    """The search is real, not vacuously empty: given a repository that does
    cite the dead file, it names it."""
    for cmd in (["git", "init", "-q"],
                ["git", "config", "user.email", "t@example.com"],
                ["git", "config", "user.name", "t"]):
        subprocess.run(cmd, cwd=tmp_path, check=True)
    (tmp_path / "a.py").write_text(f"# see docs/{ck.DEAD_CITATION}.txt\n")
    (tmp_path / "b.py").write_text("# nothing to see\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "x"], cwd=tmp_path, check=True)
    assert ck.dead_citations(tmp_path) == ["a.py"]


def test_dead_citations_refuses_outside_a_repository(tmp_path):
    with pytest.raises(ck.Refused):
        ck.dead_citations(tmp_path / "not-a-repo")


# ---------------------------------------------------------------------------
# exit codes, which are what CI reads
# ---------------------------------------------------------------------------

def _run(rigs=RIGS, sweep=SWEEP):
    r = subprocess.run([sys.executable, str(REPO / "tools" /
                                            "check_surge_waveform_comment.py"),
                        "--rigs", str(rigs), "--sweep", str(sweep)],
                       capture_output=True, text=True, cwd=REPO, timeout=120)
    return r.returncode, r.stdout


def test_cli_reports_ok_on_the_shipped_files():
    code, out = _run()
    assert code == 0, out
    assert out.startswith("OK")


def test_cli_reports_stale_on_an_injected_cell(tmp_path):
    p = tmp_path / "reference_rigs.py"
    p.write_text(RIGS.read_text().replace(
        "0.500     0.00 %   50.00 %    saw",
        "0.500     0.00 %   50.00 %    UNQUALIFIED", 1))
    code, out = _run(rigs=p)
    assert code == 1, out
    assert out.startswith("STALE")


def test_cli_refuses_a_missing_sweep_file(tmp_path):
    code, out = _run(sweep=tmp_path / "nope.txt")
    assert code == 2, out
    assert out.startswith("REFUSED")
