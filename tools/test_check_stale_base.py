#!/usr/bin/env python3
"""`tools/check_stale_base.py` must REFUSE (exit 2) when `git` cannot run.

#1742. The tool documents three exits -- 0 clean, 1 the merge would lose a
file, 2 it could not run -- and neither of its two `subprocess.run(["git",
...])` sites, the `git()` helper and the direct `merge-tree` call, caught the
executable being absent. A missing git therefore raised `FileNotFoundError`
out of `main()` and the process exited 1: a check that had not examined a
tree reported as the file-loss verdict to any caller branching on the status.
Reproduced on origin/main @ 3309dd59 (2026-11-28, Python 3.12.3, Linux) with
PATH set to an empty directory: stdout empty, stderr a traceback ending
`FileNotFoundError: [Errno 2] No such file or directory: 'git'`, exit 1.

Every case runs the REAL entry point as a subprocess -- an absolute
`sys.executable`, a PATH holding one controlled directory, and no other
variable inherited -- so what is exercised is the `__main__` a caller starts,
never `main()` in-process or a monkeypatched `subprocess`.

KNOWN ANSWERS, none of them read back from the tool:

  a check that could not run exits 2    the contract in the tool's own
                                         docstring; tools/exit_contract.py REFUSED
  a missing executable raises
    FileNotFoundError                    POSIX: execvp reports ENOENT
  a found but unexecutable file raises
    PermissionError                     POSIX: EACCES. The input that defeats
                                         a FileNotFoundError-only guard
                                         (docs/verification-rules.md rule 8),
                                         which is why the fix catches OSError,
                                         their common base
  a different file added on each side
    merges clean; the same line edited
    on both sides conflicts              the three-way merge, by construction

AN EXIT CODE OF 2 ALONE PROVES NOTHING HERE. It is also what the interpreter
returns when it cannot open the script, and what argparse returns for a
usage error. Every exit-2 assertion therefore pins the `REFUSED: cannot run`
line, and the clean-merge case pins exit 0, which no failure path produces.

WHAT THE CANNED-GIT CASE IS FOR. The file-loss verdict (exit 1, "THE MERGE
WOULD LOSE") is not reachable from a real merge: a three-way merge drops a
path only when the head deleted it, which the tool classifies as deliberate
-- the tool's own premise. That branch is therefore driven by a `git` shell
on PATH answering four fixed ways, and the test reads back which calls that
shell received, so a real git having answered instead cannot pass as the
canned one.

WRONG BEFORE IT WAS RIGHT. The first vanishing git called bare `rm`, which is
not on a PATH holding only the shim. It answered every call, the unfixed tool
printed `merge is clean` and exited 0, and the case would have passed for a
reason unrelated to the defect -- had it not read back that the shim was gone.
It does, and on the unfixed code that precondition is what failed first. The
`rm` is now resolved to an absolute path before the shim is written.

NOT CLOSED, STATED (verification-rules rule 8, form 2): a `git` that starts
but emits bytes Python cannot decode raises UnicodeDecodeError, which is not
an OSError, and the process still exits 1 (measured: a shim printing \\377\\376).
That is a different precondition -- the output could not be read, not the
executable started -- and closing it means deciding how an undecodable path is
compared, so it is left for its own decision rather than patched here.
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import textwrap

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CLI = ROOT / "tools" / "check_stale_base.py"
IDENTITY = ("-c", "user.name=t", "-c", "user.email=t@t")
BASE = "1" * 40
MERGED = "2" * 40


def _cli(path: pathlib.Path, cwd: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    """The real entry point: an absolute interpreter, a PATH of one directory,
    and nothing else inherited."""
    assert pathlib.Path(sys.executable).is_absolute(), sys.executable
    return subprocess.run([sys.executable, str(CLI), *args], cwd=cwd,
                          env={"PATH": str(path)}, capture_output=True, text=True)


def _real_git_dir() -> pathlib.Path:
    """The directory the real git was found in, after asserting it can perform
    the merge the tool performs. A merge check that cannot run is no evidence
    either way, so that skips and says why rather than reporting."""
    git = shutil.which("git")
    if git is None:
        pytest.skip("no git on PATH: the real-merge cases cannot run")
    usage = subprocess.run([git, "merge-tree", "-h"], capture_output=True, text=True)
    if "--write-tree" not in usage.stdout:
        pytest.skip("this git has no `merge-tree --write-tree` (needs 2.38): "
                    "the check under test cannot run here")
    return pathlib.Path(git).parent


def _git_shim(bindir: pathlib.Path, body: str) -> pathlib.Path:
    """A `git` that is a /bin/sh script answering however `body` says."""
    bindir.mkdir()
    shim = bindir / "git"
    shim.write_text("#!/bin/sh\n" + body)
    shim.chmod(0o755)
    return shim


def _repo(tmp_path: pathlib.Path, git_dir: pathlib.Path, *, same_line: bool = False):
    """`base` -> `feature` and `base` -> `main`, HEAD left on `feature`, with
    refs/remotes/origin/main at `main` so the CLI's DEFAULT arguments -- the
    rungs.yml invocation -- resolve. Returns (repo, git runner in that repo)."""
    repo = tmp_path / "repo"
    repo.mkdir()

    def g(*a: str) -> None:
        subprocess.run(["git", "-C", str(repo), *IDENTITY, *a], check=True,
                       capture_output=True, text=True, env={"PATH": str(git_dir)})

    def commit(name: str, text: str) -> None:
        (repo / name).write_text(text)
        g("add", "-A")
        g("commit", "-qm", name)

    edits = (("base.txt", "one\nFEATURE\nthree\n"), ("base.txt", "one\nMAIN\nthree\n")) \
        if same_line else (("feature.txt", "from feature\n"), ("main.txt", "from main\n"))
    g("init", "-q", "-b", "main")
    commit("base.txt", "one\ntwo\nthree\n")
    g("switch", "-qc", "feature")
    commit(*edits[0])
    g("switch", "-q", "main")
    commit(*edits[1])
    g("switch", "-q", "feature")
    g("update-ref", "refs/remotes/origin/main", "main")
    return repo, g


# ---------------------------------------------------------------------------
# the defect: git cannot run, at each of the two subprocess sites
# ---------------------------------------------------------------------------
def test_missing_git_is_refused_2_not_reported_as_file_loss(tmp_path):
    """The issue's reproduction: PATH is an empty directory, default args."""
    empty = tmp_path / "empty-path"
    empty.mkdir()
    assert list(empty.iterdir()) == []
    p = _cli(empty, tmp_path)
    assert p.returncode == 2, p.stderr
    assert p.stdout == ""
    assert "REFUSED: cannot run git merge-base origin/main HEAD" in p.stderr, p.stderr
    assert "No such file or directory: 'git'" in p.stderr, p.stderr
    assert "Traceback" not in p.stderr, p.stderr


def test_merge_tree_site_refuses_when_git_is_gone_by_the_second_call(tmp_path):
    """`git()` is the first site and the direct `merge-tree` call the second.
    A git that answers merge-base and then deletes itself leaves the second
    site with no executable at all, which a fix in `git()` alone cannot see.
    The shim's PATH is the shim's own directory, so `rm` is baked in absolute."""
    rm = shutil.which("rm")
    assert rm is not None, "no rm: the vanishing-git shim cannot be written"
    shim = _git_shim(tmp_path / "bin", f'{rm} -f "$0"\necho {BASE}\n')
    p = _cli(tmp_path / "bin", tmp_path)
    assert not shim.exists(), "the shim never ran, so the second site was not reached"
    assert p.returncode == 2, p.stderr
    assert p.stdout == ""
    assert "REFUSED: cannot run git merge-tree --write-tree origin/main HEAD" \
        in p.stderr, p.stderr


def test_a_git_that_cannot_be_executed_is_refused_2(tmp_path):
    """Rule 8's adversary for a FileNotFoundError-only fix: found on PATH, not
    executable. EACCES must refuse exactly as ENOENT does."""
    git = _git_shim(tmp_path / "bin", "exit 0\n")
    git.chmod(0o644)
    assert not git.stat().st_mode & 0o111
    p = _cli(tmp_path / "bin", tmp_path)
    assert p.returncode == 2, p.stderr
    assert p.stdout == ""
    assert "REFUSED: cannot run git merge-base origin/main HEAD" in p.stderr, p.stderr
    assert "Permission denied" in p.stderr, p.stderr


# ---------------------------------------------------------------------------
# what the fix must not change, against a REAL git
# ---------------------------------------------------------------------------
def test_a_real_clean_merge_still_exits_0(tmp_path):
    git_dir = _real_git_dir()
    repo, _ = _repo(tmp_path, git_dir)
    p = _cli(git_dir, repo)
    assert p.returncode == 0, p.stderr
    assert "merge is clean" in p.stdout
    assert "Traceback" not in p.stderr, p.stderr


def test_a_real_conflict_still_exits_1(tmp_path):
    git_dir = _real_git_dir()
    repo, _ = _repo(tmp_path, git_dir, same_line=True)
    p = _cli(git_dir, repo)
    assert p.returncode == 1, (p.returncode, p.stderr)
    assert "MERGE CONFLICT" in p.stderr
    assert p.stdout == ""


def test_histories_with_no_merge_base_are_still_refused_2(tmp_path):
    """Two roots: `git merge-base` exits nonzero having printed nothing. This
    is the nonzero-git path through `git()`, which the fix reroutes."""
    git_dir = _real_git_dir()
    repo, g = _repo(tmp_path, git_dir)
    g("checkout", "-q", "--orphan", "other")
    g("commit", "-qm", "a second root")
    p = _cli(git_dir, repo, "--head", "other")
    assert p.returncode == 2, (p.returncode, p.stderr)
    assert "git merge-base origin/main other failed" in p.stderr, p.stderr
    assert p.stdout == ""


# ---------------------------------------------------------------------------
# the verdict a real merge cannot produce
# ---------------------------------------------------------------------------
def test_the_file_loss_verdict_still_exits_1(tmp_path):
    """`lost.txt` is in base, in HEAD and at the merge base, and absent from
    the merge result: present in the base, missing from the result, deleted by
    nobody -- the one shape the tool calls loss. See the module docstring for
    why it is canned."""
    log = tmp_path / "git-calls.log"
    _git_shim(tmp_path / "bin", textwrap.dedent(f"""\
        echo "$*" >> "{log}"
        case "$1" in
          merge-base) echo {BASE} ;;
          merge-tree) echo {MERGED} ;;
          ls-tree)
            case "$4" in
              {MERGED}*) echo kept.txt ;;
              *) echo kept.txt; echo lost.txt ;;
            esac ;;
        esac
        """))
    p = _cli(tmp_path / "bin", tmp_path)
    calls = log.read_text().splitlines()
    assert "merge-tree --write-tree origin/main HEAD" in calls, calls
    assert f"ls-tree -r --name-only {MERGED}" in calls, calls
    assert p.returncode == 1, (p.returncode, p.stderr)
    assert "THE MERGE WOULD LOSE 1 file(s)" in p.stderr, p.stderr
    assert "lost.txt" in p.stderr
    assert p.stdout == ""
