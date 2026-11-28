"""#615: a provenance helper whose `git` returned "" on failure reported
`dirty=False` -- an unanswered git rendered as a clean tree.

#2043: a clean tree at a commit that is not on `origin/main` rendered the same
way. `worktree_state()` records `on_origin_main` beside `dirty`, and `dirty` is
True unless that answer is True -- so a record written on a local branch or an
unpushed worktree can never read clean. Every repo here is a real one; the
precondition each test depends on (clean tree, shallow, commit absent) is
asserted, not assumed."""
import hashlib
import pathlib
import subprocess
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import provenance                                                     # noqa: E402
import refprofile as rp                                               # noqa: E402

EMPTY_TREE = hashlib.sha256(b"").hexdigest()[:16]          # the diff hash of no diff


GIT_ID = ("-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false")


def _git(path: pathlib.Path, *args, check=True):
    return subprocess.run(["git", "-C", str(path), *GIT_ID, *args], check=check,
                          capture_output=True, text=True)


def _commit(path: pathlib.Path, message="c") -> None:
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", message)


def _repo(path: pathlib.Path) -> None:
    env = ["-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / "f").write_text("x")
    subprocess.run(["git", "-C", str(path), "add", "f"], check=True)
    subprocess.run(["git", "-C", str(path), *env, "commit", "-qm", "c"], check=True)


def _upstream(path: pathlib.Path) -> str:
    """A repository whose `main` holds one commit, so a clone of it has an
    `origin/main`. Returns that commit."""
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    (path / "f").write_text("x")
    _commit(path)
    return _git(path, "rev-parse", "HEAD").stdout.strip()


def _clone(src: pathlib.Path, dst: pathlib.Path) -> None:
    subprocess.run(["git", "clone", "-q", str(src), str(dst)], check=True)


def _clone_shallow(src: pathlib.Path, dst: pathlib.Path) -> None:
    """A real shallow clone: `--depth` is ignored on a plain path."""
    subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{src}", str(dst)], check=True)


def _branch_commit(work: pathlib.Path) -> str:
    """A clean commit on a branch of `work` that `origin` has never seen."""
    _git(work, "checkout", "-q", "-b", "feature")
    (work / "g").write_text("g")
    _commit(work, "feat")
    return _git(work, "rev-parse", "HEAD").stdout.strip()


def _assert_clean_tree(work: pathlib.Path) -> None:
    assert _git(work, "status", "--porcelain").stdout == ""


# ---- #615 ------------------------------------------------------------------

def test_provenance_worktree_state_is_not_clean_when_git_cannot_answer(tmp_path):
    w = provenance.worktree_state(root=tmp_path)          # not a repository
    assert w["commit"] == "?"
    assert w["dirty"] is True


def test_refprofile_worktree_state_is_not_clean_when_git_cannot_answer(tmp_path, monkeypatch):
    monkeypatch.setattr(rp, "ROOT", tmp_path)
    assert rp.worktree_state()["dirty"] is True


def test_refprofile_worktree_state_still_reports_a_real_clean_repo_clean(tmp_path, monkeypatch):
    _repo(tmp_path)
    monkeypatch.setattr(rp, "ROOT", tmp_path)
    assert rp.worktree_state()["dirty"] is False


def test_conga_provenance_is_not_clean_when_git_cannot_answer(tmp_path, monkeypatch):
    import measure_conga_body_spread as conga
    monkeypatch.setattr(conga, "ROOT", tmp_path)
    assert conga.provenance(tmp_path)["dirty"] is True


def test_conga_provenance_still_reports_a_real_clean_repo_clean(tmp_path, monkeypatch):
    import measure_conga_body_spread as conga
    _repo(tmp_path)
    monkeypatch.setattr(conga, "ROOT", tmp_path)
    assert conga.provenance(tmp_path)["dirty"] is False


def _tom_drop_compare(tmp_path, monkeypatch):
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "model"))
    import tom_drop_compare as tdc
    (tmp_path / "model").mkdir(exist_ok=True)
    (tmp_path / "model" / "drums_fx.py").write_text("x = 1\n")
    monkeypatch.setattr(tdc, "REPO", tmp_path)
    return tdc


def test_tom_drop_compare_provenance_is_not_clean_when_git_cannot_answer(tmp_path, monkeypatch):
    tdc = _tom_drop_compare(tmp_path, monkeypatch)
    assert tdc.provenance()["dirty"] is True


def test_tom_drop_compare_provenance_still_reports_a_real_clean_repo_clean(tmp_path, monkeypatch):
    tdc = _tom_drop_compare(tmp_path, monkeypatch)
    _repo(tmp_path)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "m"], check=True)
    assert tdc.provenance()["dirty"] is False


# ---- #2043 -----------------------------------------------------------------

def test_a_clean_clone_at_origin_main_reads_clean_and_on_main(tmp_path):
    """(a) A commit that IS on origin/main, in a clean tree. This is the
    defeating input for every dirty-always reading below: a `worktree_state`
    that simply said dirty would pass each of them."""
    up = tmp_path / "up"
    sha = _upstream(up)
    w = tmp_path / "work"
    _clone(up, w)
    _assert_clean_tree(w)
    assert provenance.commit_on_origin_main(sha, root=w) is True
    s = provenance.worktree_state(root=w)
    assert s["on_origin_main"] is True and s["dirty"] is False
    (w / "g").write_text("y")
    s = provenance.worktree_state(root=w)
    assert s["on_origin_main"] is True and s["dirty"] is True     # the tree, not the commit


def test_a_commit_on_a_branch_only_reads_dirty_in_an_otherwise_clean_tree(tmp_path):
    """(b), and the control #2043 asks for: the tree is clean and the commit
    is not on origin/main. `dirty: false` here is the record nobody can
    re-derive."""
    up = tmp_path / "up"
    _upstream(up)
    w = tmp_path / "work"
    _clone(up, w)
    sha = _branch_commit(w)
    _assert_clean_tree(w)
    assert provenance.commit_on_origin_main(sha, root=w) is False
    s = provenance.worktree_state(root=w)
    assert s["on_origin_main"] is False
    assert s["dirty"] is True
    assert s["untracked_files"] == 0 and s["uncommitted_sha256"] == EMPTY_TREE


def test_a_merge_commit_puts_a_branch_commit_on_origin_main_once_fetched(tmp_path):
    """The helper docstring's assumption, as a test. Refused on the branch,
    reachable after a merge-commit merge and a fetch -- with no fetch, the
    local origin/main still says False."""
    up = tmp_path / "up"
    _upstream(up)
    w = tmp_path / "work"
    _clone(up, w)
    sha = _branch_commit(w)
    assert provenance.commit_on_origin_main(sha, root=w) is False
    _git(w, "push", "-q", "origin", "feature")
    _git(up, "merge", "-q", "--no-ff", "feature")
    assert provenance.commit_on_origin_main(sha, root=w) is False      # not fetched yet
    _git(w, "fetch", "-q", "origin")
    assert provenance.commit_on_origin_main(sha, root=w) is True


def test_a_squash_merge_leaves_the_branch_commit_off_origin_main_for_good(tmp_path):
    """Not how this repository lands pull requests (504 of 504 landings at
    `60bb3756` were merge commits), but the same failure as a rebase in one
    step, and the cheapest one to show. The squash commit is on main; the
    commit the record cites never is, so the refusal never lifts."""
    up = tmp_path / "up"
    _upstream(up)
    w = tmp_path / "work"
    _clone(up, w)
    sha = _branch_commit(w)
    _git(w, "push", "-q", "origin", "feature")
    _git(up, "merge", "-q", "--squash", "feature")
    _commit(up, "squashed")
    _git(w, "fetch", "-q", "origin")
    assert _git(w, "rev-parse", "origin/main").stdout.strip() != sha
    assert provenance.commit_on_origin_main(sha, root=w) is False


def test_a_branch_rebased_after_the_record_is_written_leaves_its_commit_behind(tmp_path):
    """How most of the committed board records became unscorable, with the
    merge itself a true merge. The record cites the branch commit as written;
    main moves; the branch is rebased onto main and merges -- so the work
    lands under a NEW sha and the cited one is on no branch and no remote.
    In the checkout that wrote it the old object lingers, on nothing: False.
    Everywhere else it was never fetched: None once pruned here too."""
    up = tmp_path / "up"
    _upstream(up)
    w = tmp_path / "work"
    _clone(up, w)
    cited = _branch_commit(w)                       # the record is written here
    (up / "f").write_text("2")
    _commit(up, "c2")                               # main moves on
    _git(w, "fetch", "-q", "origin")
    _git(w, "rebase", "-q", "origin/main")          # ... and the branch is rewritten
    rewritten = _git(w, "rev-parse", "HEAD").stdout.strip()
    assert rewritten != cited                       # the precondition
    assert provenance.commit_on_origin_main(cited, root=w) is False
    _git(w, "push", "-q", "origin", "feature")
    _git(up, "merge", "-q", "--no-ff", "feature")
    _git(w, "fetch", "-q", "origin")
    assert provenance.commit_on_origin_main(rewritten, root=w) is True    # the work landed
    assert provenance.commit_on_origin_main(cited, root=w) is not True    # its sha did not
    _git(w, "reflog", "expire", "--expire=now", "--all")
    _git(w, "gc", "-q", "--prune=now")
    # ... and once pruned here, as every other clone has always seen it
    assert provenance.commit_on_origin_main(cited, root=w) is None


def test_no_origin_main_ref_is_none_and_reads_dirty_not_clean(tmp_path):
    """(c) Until #2043 this exact repo -- one commit, no remote -- was the
    test that read clean. Nothing can say its commit is anywhere but here."""
    _repo(tmp_path)
    sha = _git(tmp_path, "rev-parse", "HEAD").stdout.strip()
    assert _git(tmp_path, "rev-parse", "--verify", "-q", "origin/main",
                check=False).returncode != 0
    assert provenance.commit_on_origin_main(sha, root=tmp_path) is None
    s = provenance.worktree_state(root=tmp_path)
    assert s["on_origin_main"] is None
    assert s["dirty"] is True and s["untracked_files"] == 0


def test_a_shallow_clone_missing_the_commit_is_none(tmp_path):
    """(d) A shallow clone cannot resolve a commit it never had, and must not
    answer about it."""
    up = tmp_path / "up"
    first = _upstream(up)
    (up / "f").write_text("2")
    _commit(up, "c2")
    s = tmp_path / "shallow"
    _clone_shallow(up, s)
    assert _git(s, "rev-parse", "--is-shallow-repository").stdout.strip() == "true"
    assert _git(s, "cat-file", "-e", first, check=False).returncode != 0    # really absent
    assert provenance.commit_on_origin_main(first, root=s) is None


def test_a_shallow_clone_whose_head_is_origin_main_still_reads_clean(tmp_path):
    """The other half of (d): shallow is not itself a refusal. HEAD is the
    commit origin/main names, and git can say so without the history."""
    up = tmp_path / "up"
    _upstream(up)
    s = tmp_path / "shallow"
    _clone_shallow(up, s)
    _assert_clean_tree(s)
    st = provenance.worktree_state(root=s)
    assert st["on_origin_main"] is True and st["dirty"] is False


def test_a_shallow_clone_is_not_read_clean_on_an_answer_it_cannot_prove(tmp_path):
    """The graft. origin/main moves past HEAD and the clone fetches only the
    new tip, so HEAD truly is an ancestor -- and the clone cannot show it.
    git 2.43 answers 1 here, a confident False; the helper reports whatever it
    got as long as it is not True, and reads dirty. Observed to be False, and
    unshallowing turns it True, which is what makes the earlier answer a false
    refusal rather than a true one."""
    up = tmp_path / "up"
    tip = _upstream(up)
    s = tmp_path / "shallow"
    _clone_shallow(up, s)
    (up / "f").write_text("2")
    _commit(up, "c2")
    _git(s, "fetch", "-q", "--depth", "1", "origin")
    assert _git(s, "rev-parse", "HEAD").stdout.strip() == tip
    assert _git(s, "rev-parse", "origin/main").stdout.strip() != tip
    _assert_clean_tree(s)
    assert provenance.commit_on_origin_main(tip, root=s) is not True
    st = provenance.worktree_state(root=s)
    assert st["on_origin_main"] is not True and st["dirty"] is True
    _git(s, "fetch", "-q", "--unshallow", "origin")
    assert provenance.commit_on_origin_main(tip, root=s) is True


def test_anything_that_is_not_a_commit_id_is_none_even_where_git_resolves_it(tmp_path, monkeypatch):
    """`HEAD` and `origin/main` answer 0 against origin/main every time, so a
    record citing a moving name would pass this check while citing nothing a
    later reader can check out. Only an abbreviated or full hex id is asked
    of git; a leading `-` is not passed to it at all."""
    up = tmp_path / "up"
    sha = _upstream(up)
    w = tmp_path / "work"
    _clone(up, w)
    bad = ("HEAD", "origin/main", "main", "--all", "-a", "", "?", sha[:3], sha + "0",
           "zzzzzzz", "HEAD~1", "refs/heads/main")
    for name in bad:
        assert provenance.commit_on_origin_main(name, root=w) is None, name
    for name in (sha, sha[:7], sha[:4], sha.upper()):
        assert provenance.commit_on_origin_main(name, root=w) is True, name
    # Even a git build that answers 0 for `<40-hex>0` (observed on CI) cannot
    # produce a false acceptance: nothing outside 4..40 hex reaches git.
    yes = types.SimpleNamespace(run=lambda *a, **k: types.SimpleNamespace(returncode=0))
    monkeypatch.setattr(provenance, "subprocess", yes)
    for name in bad:
        assert provenance.commit_on_origin_main(name, root=w) is None, name


def test_the_helper_is_none_when_git_cannot_answer(tmp_path, monkeypatch):
    up = tmp_path / "up"
    sha = _upstream(up)
    (tmp_path / "plain").mkdir()
    assert provenance.commit_on_origin_main(sha, root=tmp_path / "plain") is None      # not a repo
    assert provenance.commit_on_origin_main(sha, root=tmp_path / "absent") is None    # no such dir

    def no_git(*a, **k):
        raise OSError("git: not found")

    def hung(*a, **k):
        raise subprocess.TimeoutExpired("git", 30)

    # Only the helper's own handle is replaced. Patching `subprocess.run` itself
    # would take `git()` -- and so the diff and the untracked listing -- down
    # with it, and the tree would read dirty for a reason that is not this one.
    for broken in (no_git, hung):
        monkeypatch.setattr(provenance, "subprocess", types.SimpleNamespace(run=broken))
        assert provenance.commit_on_origin_main(sha, root=up) is None
        s = provenance.worktree_state(root=up)
        assert s["on_origin_main"] is None and s["dirty"] is True
        assert s["untracked_files"] == 0 and s["uncommitted_sha256"] == EMPTY_TREE   # clean


def test_a_detached_head_is_judged_by_its_commit_not_by_a_branch_name(tmp_path):
    """The issue's edge list: detached HEAD, which is what a CI checkout is.
    `branch` reads `HEAD`; the answer is about the commit, as on a branch."""
    up = tmp_path / "up"
    _upstream(up)
    w = tmp_path / "work"
    _clone(up, w)
    off_main = _branch_commit(w)
    on_main = _git(w, "rev-parse", "main").stdout.strip()
    for commit, expected in ((on_main, True), (off_main, False)):
        _git(w, "checkout", "-q", "--detach", commit)
        assert _git(w, "rev-parse", "HEAD").stdout.strip() == commit     # detached, at `commit`
        s = provenance.worktree_state(root=w)
        assert s["branch"] == "HEAD" and s["on_origin_main"] is expected
        assert s["dirty"] is not expected


def test_a_linked_worktree_is_judged_like_any_other_checkout(tmp_path):
    """The issue's edge list: a worktree. Its `.git` is a file naming the
    shared repository; `git -C` follows it, and the answer is the clone's."""
    up = tmp_path / "up"
    _upstream(up)
    w = tmp_path / "work"
    _clone(up, w)
    off_main = _branch_commit(w)
    linked = tmp_path / "linked"
    _git(w, "worktree", "add", "-q", "--detach", str(linked), "feature")
    assert (linked / ".git").is_file()                                   # a linked worktree
    assert provenance.commit_on_origin_main(off_main, root=linked) is False
    s = provenance.worktree_state(root=linked)
    assert s["on_origin_main"] is False and s["dirty"] is True
