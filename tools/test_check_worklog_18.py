"""Controls for `tools/check_worklog_18.py` (issue #2029).

The checker asks one question about eighteen WORK_LOG records that PR #1941
appended: does each number have exactly one record bullet, under its own day
section? Its only failure mode that matters is a false green, so the shipped
defect is kept as real bytes and every other control is a defect deliberately
injected into a copy that must turn the checker red *and name the number*.

The two fixtures are the `### 2026-11-25` section exactly as it stood at the
two pinned commits, so the control is the thing the tool produced, not a
lookalike (`docs/verification-rules.md` rule 5):

- `origin/main` `60bb3756`: #1317, #1326, #1222, #1227 have no record bullet.
- PR #1941 head `91cf3598`: the same four are present, and fourteen numbers
  that main already records as `- **Issue #N (closed)**:` appear a second time
  as `- **Issue #N** (closed):`.

`FAIL` (exit 1) and `REFUSED` (exit 2) are asserted separately: a checker that
answers over an input it never parsed is the instrument this repository keeps
having to un-build, and a Python that cannot even open the script also exits 2,
so every refusal asserts its marker and not just its code.
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

import pytest

TOOLS = pathlib.Path(__file__).resolve().parent
SCRIPT = TOOLS / "check_worklog_18.py"
MAIN_SECTION = TOOLS / "testdata" / "worklog_2026-11-25_main_60bb3756.md"
PR1941_SECTION = TOOLS / "testdata" / "worklog_2026-11-25_pr1941_91cf3598.md"

DUPLICATED = [1221, 1263, 1289, 1297, 1301, 1302, 1306, 1315, 1319, 1323,
              1325, 1329, 1331, 1338]
ABSENT = [1317, 1326, 1222, 1227]
PR_KIND = {1326, 1227}
NUMBERS = sorted(DUPLICATED + ABSENT)

_RECORD = re.compile(r"^[ \t]*- \*\*(?:Issue|PR) #(\d+)\b")
_READDED = re.compile(r"^- \*\*Issue #\d+\*\* \(closed\): ")
_FAIL_LINE = re.compile(r"^FAIL (duplicated|misfiled|absent) #(\d+)\b", re.MULTILINE)


def _run(path: pathlib.Path) -> tuple[int, str]:
    """The checker as it ships -- a subprocess, so the exit code is exercised."""
    r = subprocess.run([sys.executable, str(SCRIPT), str(path)],
                       capture_output=True, text=True, encoding="utf-8", check=False)
    return r.returncode, r.stdout + r.stderr


def _run_text(tmp_path: pathlib.Path, text: str) -> tuple[int, str]:
    p = tmp_path / "WORK_LOG.md"
    p.write_text(text, encoding="utf-8")
    return _run(p)


def _num(line: str) -> int | None:
    m = _RECORD.match(line)
    return int(m.group(1)) if m else None


def _failed(out: str) -> dict[int, str]:
    return {int(n): kind for kind, n in _FAIL_LINE.findall(out)}


def _repaired_text() -> str:
    """PR #1941's section with the fourteen re-added bullets dropped."""
    keep = [line for line in PR1941_SECTION.read_text(encoding="utf-8").splitlines(keepends=True)
            if not (_num(line) in DUPLICATED and _READDED.match(line))]
    assert len(keep) == 95, len(keep)
    return "".join(keep)


def _line_for(text: str, n: int) -> str:
    hits = [line for line in text.splitlines(keepends=True) if _num(line) == n]
    assert len(hits) == 1, (n, hits)
    return hits[0]


def _synthetic(fmt_a: bool) -> str:
    """All eighteen records, every issue in spelling A or every one in B."""
    lines = ["### 2026-11-25\n", "\n"]
    for n in NUMBERS:
        if n in PR_KIND:
            lines.append(f"- **PR #{n}**: title {n}\n")
        elif fmt_a:
            lines.append(f"- **Issue #{n} (closed)**: title {n}\n")
        else:
            lines.append(f"- **Issue #{n}** (closed): title {n}\n")
    return "".join(lines)


# ---------------------------------------------------------------------------
# the defect as it shipped: real bytes, must stay red
# ---------------------------------------------------------------------------

def test_pr1941_head_is_red_on_exactly_the_fourteen_duplicates():
    rc, out = _run(PR1941_SECTION)
    assert rc == 1, out
    assert _failed(out) == {n: "duplicated" for n in DUPLICATED}
    assert "14 duplicated, 0 misfiled, 0 absent, 4 ok" in out


def test_main_is_red_on_exactly_the_four_absent():
    rc, out = _run(MAIN_SECTION)
    assert rc == 1, out
    assert _failed(out) == {n: "absent" for n in ABSENT}
    assert "0 duplicated, 0 misfiled, 4 absent, 14 ok" in out


# ---------------------------------------------------------------------------
# the gate can turn green, or it would train everyone to ignore it
# ---------------------------------------------------------------------------

def test_pr1941_head_without_the_fourteen_readds_is_green(tmp_path):
    rc, out = _run_text(tmp_path, _repaired_text())
    assert rc == 0, out
    assert "OK: 18/18" in out


# ---------------------------------------------------------------------------
# injected defects on the green base: each must redden it and name the number
# ---------------------------------------------------------------------------

def test_one_of_the_fourteen_doubled_is_red(tmp_path):
    text = _repaired_text()
    doubled = text.replace(_line_for(text, 1302),
                           "- **Issue #1302** (closed): re-added\n" + _line_for(text, 1302))
    assert doubled != text
    rc, out = _run_text(tmp_path, doubled)
    assert rc == 1, out
    assert _failed(out) == {1302: "duplicated"}


def test_one_of_the_four_absent_is_red(tmp_path):
    text = _repaired_text()
    rc, out = _run_text(tmp_path, text.replace(_line_for(text, 1317), ""))
    assert rc == 1, out
    assert _failed(out) == {1317: "absent"}


def test_a_record_under_the_wrong_day_is_red(tmp_path):
    text = _repaired_text()
    line = _line_for(text, 1317)
    rc, out = _run_text(tmp_path, text.replace(line, "") + "### 2026-11-26\n\n" + line)
    assert rc == 1, out
    assert _failed(out) == {1317: "misfiled"}


def test_an_indented_duplicate_still_counts(tmp_path):
    rc, out = _run_text(tmp_path, _repaired_text() + "  - **Issue #1263** (closed): nested\n")
    assert rc == 1, out
    assert _failed(out) == {1263: "duplicated"}


def test_a_longer_number_is_not_that_number(tmp_path):
    text = _repaired_text()
    rc, out = _run_text(tmp_path, text + "- **Issue #13021** (closed): another issue\n")
    assert rc == 0, out
    rc, out = _run_text(tmp_path,
                        text.replace(_line_for(text, 1302),
                                     "- **Issue #13021** (closed): renumbered\n"))
    assert rc == 1, out
    assert _failed(out) == {1302: "absent"}


def test_a_mention_inside_another_title_is_not_a_record(tmp_path):
    text = _repaired_text()
    rc, out = _run_text(tmp_path,
                        text + "- **Issue #9** (closed): see Issue #1317 and PR #1326\n")
    assert rc == 0, out


# ---------------------------------------------------------------------------
# the edge the issue names: a record present in only one spelling is recorded
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fmt_a", [True, False], ids=["spelling-A-only", "spelling-B-only"])
def test_one_bullet_in_either_spelling_is_enough(tmp_path, fmt_a):
    rc, out = _run_text(tmp_path, _synthetic(fmt_a))
    assert rc == 0, out
    assert "OK: 18/18" in out


# ---------------------------------------------------------------------------
# REFUSED (2) is not FAIL (1): no evidence is not a defect
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", ["", "not a work log at all\n",
                                  "# WORK_LOG.md\n\n- **Issue #1317** (closed): x\n"],
                         ids=["empty", "no-header", "records-without-a-day-header"])
def test_an_input_with_no_day_section_is_refused(tmp_path, text):
    rc, out = _run_text(tmp_path, text)
    assert rc == 2, out
    assert "REFUSED" in out
    assert _failed(out) == {}


def test_a_missing_file_is_refused(tmp_path):
    rc, out = _run(tmp_path / "absent.md")
    assert rc == 2, out
    assert "REFUSED" in out


def test_a_non_utf8_file_is_refused(tmp_path):
    p = tmp_path / "WORK_LOG.md"
    p.write_bytes(b"### 2026-11-25\n\n- **Issue #1317** (closed): \xff\xfe\n")
    r = subprocess.run([sys.executable, str(SCRIPT), str(p)],
                       capture_output=True, check=False)
    out = (r.stdout + r.stderr).decode("utf-8", "replace")
    assert r.returncode == 2, out
    assert "REFUSED" in out
