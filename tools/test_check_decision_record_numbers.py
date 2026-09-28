"""Controls for `tools/check_decision_record_numbers.py`.

The checker's only failure mode that matters is a false green: two decision
records sharing a number, and an OK. So the shipped directory passing is ONE
test here, and every other test is a defect deliberately injected into a COPY of
the directory that must turn it red -- including issue #250's own collision (two
files both called `0017-*`), kept as a fixture so the historical wrong answer
stays runnable rather than merely described (`docs/verification-rules.md` rule 5).

The outcomes are distinguished on purpose. `REFUSED` (exit 2) means nothing was
checked; it is asserted separately from a defect (exit 1), because a checker that
answers OK over an empty directory -- where a uniqueness test passes
vacuously -- is the instrument this repository keeps having to un-build.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import check_decision_record_numbers as ck                           # noqa: E402

REPO = ck.REPO
RECORDS = ck.RECORDS

SCRIPT = pathlib.Path(ck.__file__).resolve()


def _stage(tmp_path: pathlib.Path) -> pathlib.Path:
    """A copy of the shipped directory. Nothing here ever edits the real tree."""
    return ck._stage(RECORDS, tmp_path / "decision-records")


def _run(directory: pathlib.Path) -> subprocess.CompletedProcess:
    """The checker as it ships -- a subprocess, so the exit code is exercised."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--dir", str(directory)],
        capture_output=True, text=True, check=False)


# ---------------------------------------------------------------------------
# the shipped state
# ---------------------------------------------------------------------------

def test_the_shipped_directory_allocates_every_number_once():
    code, word, lines = ck.verdict(RECORDS)
    assert (code, word) == (0, "OK"), "\n".join(lines)


def test_the_shipped_directory_has_no_duplicate_leading_numbers():
    """The acceptance criterion of issue #250, stated as the directory listing
    rather than as the checker's verdict -- so a checker bug cannot satisfy it."""
    numbers = [name[:4] for name in sorted(p.name for p in RECORDS.glob("*.md"))
               if ck.FILENAME_RE.match(name)]
    duplicated = sorted({n for n in numbers if numbers.count(n) > 1})
    assert duplicated == [], f"decision-record numbers allocated twice: {duplicated}"


def test_every_record_heads_itself_with_its_own_filename_number():
    assert ck.misnumbered(ck.read_records(RECORDS)) == []


def test_the_cowbell_record_was_renumbered_out_of_0017():
    """Issue #250's fix, pinned. `0017` belongs to the polyBLAMP DR, which dozens
    of live call-sites name as "DR 0017" (`spec/NUMERIC-CONTRACT.md`,
    `model/voice_fx.py`, `rtl-sketch/voice_dp.v`, ...); the metric-direction DR
    had no cross-references to its number and is the one that moved."""
    names = {p.name for p in RECORDS.glob("*.md")}
    blamp = [n for n in names if "blamp-on-the-shark" in n]
    metric = [n for n in names if "metric-direction" in n]
    assert blamp == ["0017-blamp-on-the-shark-tooths-corner.md"], blamp
    assert metric and not metric[0].startswith("0017-"), metric


# ---------------------------------------------------------------------------
# issue #250's own collision, as a control
# ---------------------------------------------------------------------------

#: The two filenames that were both on `origin/main` at 7dd30f0, from two PRs
#: that never saw one another (#241 and #244).
ISSUE_250_PAIR = ("0017-blamp-on-the-shark-tooths-corner.md",
                  "0017-metric-direction-is-part-of-its-definition.md")


def test_issue_250_the_0017_collision_that_shipped_is_caught(tmp_path):
    """Re-create the exact pre-fix state and require COLLISION. If this passes
    OK, the fix is cosmetic and the defect can walk back in."""
    staged = _stage(tmp_path)
    metric = next(p for p in staged.glob("*.md") if "metric-direction" in p.name)
    reverted = staged / ISSUE_250_PAIR[1]
    reverted.write_text(metric.read_text().replace(f"# {metric.name[:4]}:", "# 0017:", 1))
    metric.unlink()
    assert {p.name for p in staged.glob("0017-*")} == set(ISSUE_250_PAIR)

    proc = _run(staged)
    assert proc.returncode == 1, proc.stdout
    assert "COLLISION" in proc.stdout
    assert "0017 is allocated 2 times" in proc.stdout
    for name in ISSUE_250_PAIR:
        assert name in proc.stdout


# ---------------------------------------------------------------------------
# injected defects -- each must turn the checker red
# ---------------------------------------------------------------------------

def test_a_second_file_under_an_existing_number_is_a_collision(tmp_path):
    staged = _stage(tmp_path)
    what = ck.inject_duplicate_number(staged)
    proc = _run(staged)
    assert proc.returncode == 1, f"{what}\n{proc.stdout}"
    assert "COLLISION" in proc.stdout


def test_a_header_that_disagrees_with_its_filename_is_misnumbered(tmp_path):
    """The half of a renumber that a rename alone forgets."""
    staged = _stage(tmp_path)
    ck.inject_header_mismatch(staged)
    proc = _run(staged)
    assert proc.returncode == 1, proc.stdout
    assert "MISNUMBERED" in proc.stdout
    assert "0099" in proc.stdout


def test_three_files_on_one_number_are_all_named(tmp_path):
    """A collision is not always a pair. Reporting only two of three would send
    someone to renumber one file and leave the directory still ambiguous."""
    staged = _stage(tmp_path)
    number = sorted(staged.glob("[0-9][0-9][0-9][0-9]-*.md"))[0].name[:4]
    for slug in ("branch-b", "branch-c"):
        (staged / f"{number}-{slug}.md").write_text(f"# {number}: {slug}\n")
    proc = _run(staged)
    assert proc.returncode == 1
    assert f"{number} is allocated 3 times" in proc.stdout
    for slug in ("branch-b", "branch-c"):
        assert f"{number}-{slug}.md" in proc.stdout


# ---------------------------------------------------------------------------
# refusals -- nothing checked is not the same as nothing wrong
# ---------------------------------------------------------------------------

def test_a_file_whose_number_cannot_be_read_is_refused_not_skipped(tmp_path):
    staged = _stage(tmp_path)
    ck.inject_unnumbered_file(staged)
    proc = _run(staged)
    assert proc.returncode == 2, proc.stdout
    assert "REFUSED" in proc.stdout and "unparsed-file" in proc.stdout


def test_a_record_with_no_heading_number_is_refused(tmp_path):
    staged = _stage(tmp_path)
    victim = sorted(staged.glob("[0-9][0-9][0-9][0-9]-*.md"))[0]
    victim.write_text("A record that forgot its heading.\n")
    proc = _run(staged)
    assert proc.returncode == 2, proc.stdout
    assert "no-heading" in proc.stdout


def test_an_empty_directory_is_refused_because_uniqueness_passes_vacuously(tmp_path):
    staged = _stage(tmp_path)
    ck.inject_empty_directory(staged)
    proc = _run(staged)
    assert proc.returncode == 2, proc.stdout
    assert "no-records" in proc.stdout


def test_a_missing_directory_is_refused(tmp_path):
    proc = _run(tmp_path / "not-here")
    assert proc.returncode == 2, proc.stdout
    assert "no-directory" in proc.stdout


# ---------------------------------------------------------------------------
# things that are NOT defects
# ---------------------------------------------------------------------------

def test_a_gap_in_the_sequence_is_not_a_defect(tmp_path):
    """0020 was consumed by a PR that merged mid-fix, so the live tree already
    has gaps by construction. A gate that goes red for a reason nobody can fix
    trains everyone to ignore gates."""
    staged = _stage(tmp_path)
    sorted(staged.glob("[0-9][0-9][0-9][0-9]-*.md"))[1].unlink()
    proc = _run(staged)
    assert proc.returncode == 0, proc.stdout


def test_both_heading_spellings_in_use_here_are_accepted(tmp_path):
    """`# 0014: title` and `# DR 0014 -- title` both ship. A checker that failed
    on the prose style rather than the number would be routed around."""
    staged = _stage(tmp_path)
    victim = sorted(staged.glob("[0-9][0-9][0-9][0-9]-*.md"))[0]
    number = victim.name[:4]
    victim.write_text(f"# DR {number} — a heading in the other spelling\n")
    proc = _run(staged)
    assert proc.returncode == 0, proc.stdout
    assert ck.read_records(staged)[victim] == (int(number), int(number))


def test_the_template_is_not_treated_as_a_record():
    """TEMPLATE.md heads itself `# 0000:`; counting it would make 0000 a live
    number and the next template copy a collision."""
    assert (RECORDS / "TEMPLATE.md").exists()
    assert (RECORDS / "TEMPLATE.md") not in ck.read_records(RECORDS)


# ---------------------------------------------------------------------------
# the controls themselves -- a control that cannot fail is not a control
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("injection,expect", sorted(ck.EXPECTED.items()))
def test_each_cli_control_fires(injection, expect):
    """`--inject X --expect <its verdict>` must exit 0: the defect was caught."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--inject", injection, "--expect", expect],
        capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"--expect {expect}: met" in proc.stdout


def test_the_clean_control_passes_so_the_injections_discriminate():
    """Condition 1 of rule 5's three: the clean run passes. Without it, a red
    mutant proves only that the checker is broken for every input."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--expect", "ok"],
        capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stdout
    assert "--expect ok: met" in proc.stdout


def test_an_injection_paired_with_the_wrong_expectation_gives_no_verdict():
    """A control cannot be satisfied by any red outcome -- only by ITS red
    outcome. `--expect refused` on a collision is a NO VERDICT, not a pass:
    that is the `|| true` + grep-for-FAIL mistake this repository already made."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT),
         "--inject", "DUPLICATE_NUMBER", "--expect", "refused"],
        capture_output=True, text=True, check=False)
    assert proc.returncode == 2, proc.stdout
    assert "NO VERDICT" in proc.stdout


def test_the_controls_never_touch_the_shipped_directory(tmp_path):
    """Every injection runs against a staged copy. If one ever edited the real
    tree, the next `make verify` would inherit the defect."""
    before = {p.name: p.read_text() for p in RECORDS.glob("*.md")}
    for injection in sorted(ck.INJECTIONS):
        subprocess.run(
            [sys.executable, str(SCRIPT), "--inject", injection,
             "--expect", ck.EXPECTED[injection]],
            capture_output=True, text=True, check=False)
    after = {p.name: p.read_text() for p in RECORDS.glob("*.md")}
    assert before == after


# ---------------------------------------------------------------------------
# wiring -- a gate nothing invokes fails exactly the same way
# ---------------------------------------------------------------------------

def test_the_checker_is_a_job_in_make_verify():
    makefile = (REPO / "Makefile").read_text()
    verify = makefile.split("\nverify:", 1)[1].split("\n\n", 1)[0]
    assert "tools/check_decision_record_numbers.py" in verify, (
        "`make verify` no longer runs the decision-record numbering check")


def test_the_injections_are_jobs_in_make_controls():
    controls = (REPO / "Makefile").read_text().split("\ncontrols:", 1)[1]
    controls = controls.split("\n\n", 1)[0]
    for injection in sorted(ck.INJECTIONS):
        assert injection in controls, (
            f"`make controls` does not run --inject {injection}: an injection "
            f"nobody runs is a defect class nobody is checking")


def test_this_checker_is_invoked_by_a_ci_job_and_not_only_by_make():
    """"A gate nothing invokes fails exactly the same way" (rungs.yml). No
    workflow invokes `make verify` or `make controls`, so a check that lives only
    in the Makefile does not run on the pull request that introduces the
    collision -- which is the exact moment issue #250's defect is created.
    """
    import yaml

    import check_workflows as cw

    workflow = yaml.safe_load((REPO / ".github/workflows/rungs.yml").read_text())
    runs = [str(step.get("run", "")) for step in workflow["jobs"]["python"]["steps"]]

    assert any("tools/check_decision_record_numbers.py" in r and "pytest" not in r
               for r in runs), (
        "rungs.yml's `python` job no longer runs "
        "tools/check_decision_record_numbers.py -- two concurrent PRs can each "
        "allocate the same number again with nothing on either PR to say so")

    # The CONTROLS are no longer named in rungs.yml, and that is the fix rather
    # than the regression (#404): naming test files there made the set CI runs
    # and the set that exists two lists that could drift, and they had, by 83
    # files. The `tools` job now collects the whole directory, which subsumes
    # this file. The property this assertion was written for is unchanged --
    # something on the PULL REQUEST must still exercise the controls -- so it is
    # the directory run that is asserted now, with enough of the command to
    # exclude a single-file or `-k`-narrowed invocation that would collect these
    # controls without running them.
    blocks, reason = cw.step_would_block_pull_request(
        workflow, "tools", "python -m pytest tools/ -q")
    assert blocks, (
        "rungs.yml's `tools` job no longer collects tools/ as a directory in a "
        "way that can block a pull request -- an unexercised checker measures "
        f"the checker, not the directory: {reason}")
