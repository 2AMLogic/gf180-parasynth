"""Controls for tools/check_cymbal_source_provenance.py (#1940).

Every case that judges a checkout builds a throwaway repository under tmp_path,
so each verdict is produced by a checkout that is really in that state rather
than by a fixture that claims to be. The cases are the issue's acceptance
criteria, one test each:

  * a clean commit reachable from the canonical main ref passes;
  * a commit that exists only locally fails, though `git cat-file` finds it;
  * a new unknown SHA cannot pass by copying a historical exception;
  * an absent main ref, or a shallow clone, is an explicit UNAVAILABLE.

The last five read the committed tree: the three unknowns and the control,
their annotations, their READMEs and the Makefile job.

The shallow case is the one to keep: a commit that IS on main, fetched as its
own shallow tip, makes `git merge-base --is-ancestor` answer "not an ancestor".
A tool that trusted that answer would report a true ancestor as unresolved.
Here it refuses -- and it was this control that turned the tool's own first
version red: it reused one variable for both `rev-parse` answers, so it never
saw a shallow clone and answered UNRESOLVED for that true ancestor.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_cymbal_source_provenance as ccsp     # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
FAMILY = ccsp.FAMILY
TOOL = ROOT / "tools" / "check_cymbal_source_provenance.py"
UNKNOWN_SHA = "deadbeef" * 5                       # well-formed, on no ref


# ---------------------------------------------------------------------------
# throwaway repositories
# ---------------------------------------------------------------------------
def _env():
    e = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    e.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
             GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    return e


def git(repo, *a, check=True):
    p = subprocess.run(["git", "-C", str(repo), *a], env=_env(), capture_output=True, text=True)
    if check:
        assert p.returncode == 0, (a, p.stderr)
    return p


def commit(repo, name, files=None):
    for rel, text in (files or {name: name + "\n"}).items():
        p = pathlib.Path(repo) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", name)
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def origin_clone(tmp_path, depth=None, prefix=""):
    """A bare `origin` holding two commits on main, and a clone of it through a
    file:// URL (a plain path would hardlink the object store and ignore depth).
    `prefix` makes the commits, and so the SHAs, distinct from another call's.
    Returns (clone, [older sha, tip sha])."""
    seed = tmp_path / "seed"
    tmp_path.mkdir(parents=True, exist_ok=True)
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(tmp_path / "origin.git"))
    git(tmp_path, "init", "-q", "-b", "main", str(seed))
    older = commit(seed, prefix + "first")
    tip = commit(seed, prefix + "second")
    git(seed, "remote", "add", "origin", str(tmp_path / "origin.git"))
    git(seed, "push", "-q", "origin", "main")
    url = (tmp_path / "origin.git").as_uri()
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", *(["--depth", str(depth)] if depth else []), url, str(clone))
    return clone, [older, tip]


def record(sha, dirty=False, **extra):
    return {"controls_pass": True, "commit": sha, "sources_dirty": dirty, **extra}


def put(repo, rel, obj):
    """Write one scoped record at FAMILY/rel and return its path."""
    p = pathlib.Path(repo) / FAMILY / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=1) + "\n")
    return p


def one(repo, rel, obj, main_ref="origin/main"):
    """Write one scoped record, alone in the family, and return the report."""
    put(repo, rel, obj)
    rep = ccsp.evaluate(str(repo), FAMILY, main_ref)
    assert len(rep.rows) == 1
    return rep


def row_for(rep, rel):
    rows = [r for r in rep.rows if r["path"] == f"{FAMILY}/{rel}"]
    assert len(rows) == 1
    return rows[0]


def pasted_annotation():
    """The annotation exactly as a copyist would lift it: out of the committed
    record it was written for."""
    d = json.loads((ROOT / FAMILY / "low-tail" / "low-tail.json").read_text())
    return d[ccsp.ANNOTATION]


LOW_TAIL, LOW_TAIL_SHA = "low-tail/low-tail.json", ccsp.HISTORICAL_UNKNOWN["low-tail/low-tail.json"]["commit"]


# ---------------------------------------------------------------------------
# what passes, and what must not
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("which", [1, 0], ids=["tip", "behind-the-tip"])
def test_a_clean_commit_reachable_from_the_canonical_main_ref_passes(tmp_path, which):
    clone, shas = origin_clone(tmp_path)
    rep = one(clone, "x/r.json", record(shas[which]))
    assert rep.rows[0]["verdict"] == ccsp.RESOLVED
    assert rep.exit_code == ccsp.ec.PASS and rep.state["main"] == shas[1]


def test_a_record_cites_the_full_sha_in_the_report(tmp_path):
    clone, shas = origin_clone(tmp_path)
    rep = one(clone, "x/r.json", record(shas[0]))
    assert rep.rows[0]["sha"] == shas[0] and shas[0] in "\n".join(rep.lines())


@pytest.mark.parametrize("where", ["side-branch", "unpushed-main"])
def test_a_commit_present_only_locally_fails_though_cat_file_finds_it(tmp_path, where):
    """The case the issue names: `sources_dirty: false`, a real commit object,
    and not on origin. A check that asked `git cat-file` would pass it."""
    clone, shas = origin_clone(tmp_path)
    git(clone, "checkout", "-q", "-b", "side", "main")
    if where == "unpushed-main":
        git(clone, "checkout", "-q", "main")
    local = commit(clone, "local-only")
    assert git(clone, "cat-file", "-e", local + "^{commit}", check=False).returncode == 0
    assert git(clone, "merge-base", "--is-ancestor", local, "origin/main", check=False).returncode == 1
    rep = one(clone, "x/r.json", record(local))
    assert rep.rows[0]["verdict"] == ccsp.UNRESOLVED
    assert "not on origin/main" in rep.rows[0]["detail"]
    assert rep.exit_code == ccsp.ec.FAIL


@pytest.mark.parametrize("sha", [UNKNOWN_SHA, "0" * 40, "f" * 40])
def test_a_sha_on_no_ref_fails(tmp_path, sha):
    clone, _ = origin_clone(tmp_path)
    rep = one(clone, "x/r.json", record(sha))
    assert rep.rows[0]["verdict"] == ccsp.UNRESOLVED and rep.exit_code == ccsp.ec.FAIL


def test_without_an_annotation_the_historical_entry_is_unresolved(tmp_path):
    """The state the three records were in before #1940, kept as a control."""
    clone, _ = origin_clone(tmp_path)
    rep = one(clone, LOW_TAIL, record(LOW_TAIL_SHA))
    assert rep.rows[0]["verdict"] == ccsp.UNRESOLVED
    assert "no source_provenance annotation accounts for it" in rep.rows[0]["detail"]
    assert rep.exit_code == ccsp.ec.FAIL


def test_a_historical_unknown_is_accepted_and_reported_as_an_unknown(tmp_path):
    clone, _ = origin_clone(tmp_path)
    rep = one(clone, LOW_TAIL, record(LOW_TAIL_SHA, **{ccsp.ANNOTATION: pasted_annotation()}))
    row = rep.rows[0]
    assert row["verdict"] == ccsp.HISTORICAL
    assert "not a verified source" in row["detail"]
    assert rep.counts()[ccsp.RESOLVED] == 0 and rep.exit_code == ccsp.ec.PASS
    assert "1 historical unknowns (annotated, never verified), 0 resolvable" in rep.lines()[0]
    assert "NOT a verified source" in ccsp.LIMIT


@pytest.mark.parametrize("rel, sha", [
    ("x/new.json", UNKNOWN_SHA),            # a new record, new SHA, pasted block
    ("low-tail/copy.json", LOW_TAIL_SHA),   # the table's SHA at a path that is not in it
    (LOW_TAIL, UNKNOWN_SHA),                # the table's path with a SHA that is not in it
], ids=["new-path-new-sha", "new-path-table-sha", "table-path-new-sha"])
def test_a_new_unknown_sha_cannot_pass_by_copying_a_historical_exception(tmp_path, rel, sha):
    clone, _ = origin_clone(tmp_path)
    rep = one(clone, rel, record(sha, **{ccsp.ANNOTATION: pasted_annotation()}))
    assert rep.rows[0]["verdict"] == ccsp.UNRESOLVED
    assert "not a recognised historical exception" in rep.rows[0]["detail"]
    assert rep.exit_code == ccsp.ec.FAIL


def _mutate(annotation, key, value):
    a = json.loads(json.dumps(annotation))
    if "." in key:
        outer, inner = key.split(".")
        a[outer][inner] = value
    else:
        a[key] = value
    return a


@pytest.mark.parametrize("key, value", [
    ("status", "verified"),
    ("status", "resolved"),
    ("recorded", "2026-09-28"),
    ("issue", 1869),
    ("recorded_commit", UNKNOWN_SHA),
    ("lookup.date", "2026-09-28"),
    ("lookup.is_ancestor", True),
    ("lookup.is_ancestor", None),
])
def test_an_annotation_may_state_a_failed_lookup_and_nothing_else(tmp_path, key, value):
    """Accepting the annotation must never turn into accepting the source. Each
    mutation is a way of saying the lookup succeeded, or of dating it to
    something nobody checked."""
    clone, _ = origin_clone(tmp_path)
    rep = one(clone, LOW_TAIL, record(LOW_TAIL_SHA, **{ccsp.ANNOTATION: _mutate(pasted_annotation(), key, value)}))
    assert rep.rows[0]["verdict"] == ccsp.DEFECT and rep.exit_code == ccsp.ec.FAIL


def test_an_annotation_that_is_not_an_object_is_a_defect(tmp_path):
    clone, _ = origin_clone(tmp_path)
    rep = one(clone, LOW_TAIL, record(LOW_TAIL_SHA, **{ccsp.ANNOTATION: "unresolved, see #1940"}))
    assert rep.rows[0]["verdict"] == ccsp.DEFECT and "not an object" in rep.rows[0]["detail"]


def test_an_annotated_record_whose_commit_now_resolves_fails(tmp_path):
    """Good news is still a wrong record: the annotation says the commit is not
    on main and it is."""
    clone, shas = origin_clone(tmp_path)
    a = _mutate(pasted_annotation(), "recorded_commit", shas[0])
    rep = one(clone, LOW_TAIL, record(shas[0], **{ccsp.ANNOTATION: a}))
    assert rep.rows[0]["verdict"] == ccsp.DEFECT
    assert "is an ancestor of origin/main" in rep.rows[0]["detail"] and rep.exit_code == ccsp.ec.FAIL


def test_an_unannotated_record_whose_commit_resolves_passes(tmp_path):
    clone, shas = origin_clone(tmp_path)
    assert one(clone, LOW_TAIL, record(shas[0])).exit_code == ccsp.ec.PASS


def test_dirty_sources_fail_even_when_the_commit_resolves(tmp_path):
    clone, shas = origin_clone(tmp_path)
    rep = one(clone, "x/r.json", record(shas[1], dirty=True))
    assert rep.rows[0]["verdict"] == ccsp.DEFECT and "sources_dirty" in rep.rows[0]["detail"]


@pytest.mark.parametrize("sha", ["1c8a941a", None, 123, "ZZ" * 20, "A" * 40])
def test_a_malformed_sha_is_a_defect_before_any_lookup(tmp_path, sha):
    """Short, absent, non-string, non-hex, upper-case: none names a commit."""
    clone, _ = origin_clone(tmp_path)
    rep = one(clone, "x/r.json", {"controls_pass": True, "commit": sha, "sources_dirty": False})
    assert rep.rows[0]["verdict"] == ccsp.DEFECT and "40-hex" in rep.rows[0]["detail"]
    assert rep.exit_code == ccsp.ec.FAIL


def test_a_record_without_a_commit_is_a_defect(tmp_path):
    clone, _ = origin_clone(tmp_path)
    rep = one(clone, "x/r.json", {"controls_pass": True, "sources_dirty": False})
    assert rep.rows[0]["verdict"] == ccsp.DEFECT and rep.exit_code == ccsp.ec.FAIL


# ---------------------------------------------------------------------------
# what cannot be answered is refused, never guessed
# ---------------------------------------------------------------------------
def test_an_absent_main_ref_is_unavailable_even_for_a_commit_on_local_main(tmp_path):
    """No `origin/main` in the clone, and the record cites the local tip. A
    check that fell back to HEAD would pass this; the canonical ref is what
    'on origin' means, and it is not here to ask."""
    repo = tmp_path / "plain"
    git(tmp_path, "init", "-q", "-b", "main", str(repo))
    tip = commit(repo, "only")
    put(repo, "x/r.json", record(tip))
    rep = ccsp.evaluate(str(repo), FAMILY, "origin/main")
    assert rep.rows[0]["verdict"] == ccsp.UNAVAILABLE
    assert "no origin/main in this clone" in rep.rows[0]["detail"]
    assert rep.exit_code == ccsp.ec.REFUSED


def test_a_shallow_clone_refuses_a_true_ancestor_rather_than_failing_it(tmp_path):
    clone, shas = origin_clone(tmp_path, depth=1)
    assert git(clone, "rev-parse", "--is-shallow-repository").stdout.strip() == "true"
    # the tip is inside the fetched history: a positive is sound at any depth
    assert one(clone, "tip.json", record(shas[1])).rows[0]["verdict"] == ccsp.RESOLVED
    # the older commit is a TRUE ancestor of origin/main, absent from the clone
    put(clone, "older.json", record(shas[0]))
    rep = ccsp.evaluate(str(clone), FAMILY, "origin/main")
    older = row_for(rep, "older.json")
    assert older["verdict"] == ccsp.UNAVAILABLE and "shallow" in older["detail"]
    assert rep.exit_code == ccsp.ec.REFUSED
    # ...and still a true ancestor once fetched as its own shallow tip, which
    # is where git's own answer turns false and a trusting tool would FAIL it
    git(clone, "fetch", "-q", "--depth", "1", "origin", shas[0])
    assert git(clone, "cat-file", "-e", shas[0] + "^{commit}", check=False).returncode == 0
    assert git(clone, "merge-base", "--is-ancestor", shas[0], "origin/main", check=False).returncode == 1
    assert ccsp.lookup(str(clone), shas[0], "origin/main", ccsp.repo_state(str(clone), "origin/main")) == "shallow"
    again = ccsp.evaluate(str(clone), FAMILY, "origin/main")
    assert row_for(again, "older.json")["verdict"] == ccsp.UNAVAILABLE and again.exit_code == ccsp.ec.REFUSED


def test_a_directory_that_is_not_a_repository_is_unavailable(tmp_path):
    """No history at all: the basis line says why, and every row refuses."""
    repo = tmp_path / "not-a-repo"
    put(repo, "x/r.json", record(UNKNOWN_SHA))
    rep = ccsp.evaluate(str(repo), FAMILY, "origin/main")
    assert rep.rows[0]["verdict"] == ccsp.UNAVAILABLE
    assert "not a git repository" in ccsp.basis(rep.state, "origin/main") and "not a git repository" in rep.rows[0]["detail"]
    assert rep.exit_code == ccsp.ec.REFUSED
    p = run_tool(repo)
    assert p.returncode == ccsp.ec.REFUSED and "against not a git repository" in p.stdout


def test_an_empty_scope_is_refused(tmp_path):
    clone, _ = origin_clone(tmp_path)
    put(clone, "notes.json", {"not": "a result record"})
    with pytest.raises(ccsp.Refused, match="no JSON with a top-level controls_pass"):
        ccsp.evaluate(str(clone), FAMILY, "origin/main")


def test_an_unreadable_scoped_file_is_refused(tmp_path):
    clone, _ = origin_clone(tmp_path)
    p = pathlib.Path(clone) / FAMILY / "x" / "broken.json"
    p.parent.mkdir(parents=True)
    p.write_text("{not json")
    with pytest.raises(ccsp.Refused, match="broken.json: not readable as JSON"):
        ccsp.evaluate(str(clone), FAMILY, "origin/main")


# ---------------------------------------------------------------------------
# the command, as make and a person run it
# ---------------------------------------------------------------------------
def run_tool(repo, *extra, main_ref=None):
    a = [sys.executable, str(TOOL), "--repo", str(repo), *extra]
    if main_ref:
        a += ["--main-ref", main_ref]
    return subprocess.run(a, capture_output=True, text=True, env=_env())


def test_the_command_names_each_unresolved_claim_by_path_and_sha(tmp_path):
    clone, shas = origin_clone(tmp_path)
    local = commit(clone, "local-only")
    put(clone, LOW_TAIL, record(LOW_TAIL_SHA))
    put(clone, "vca-clip/vca-clip.json", record(ccsp.HISTORICAL_UNKNOWN["vca-clip/vca-clip.json"]["commit"]))
    put(clone, "x/local.json", record(local))
    put(clone, "tone-knob/tone-knob.json", record(shas[0]))
    p = run_tool(clone)
    out = "\n".join(p.stdout.splitlines())
    assert p.returncode == ccsp.ec.FAIL, p.stdout + p.stderr
    for rel, sha in [(LOW_TAIL, LOW_TAIL_SHA), ("vca-clip/vca-clip.json", ccsp.HISTORICAL_UNKNOWN["vca-clip/vca-clip.json"]["commit"]),
                     ("x/local.json", local)]:
        assert f"UNRESOLVED" in out and f"{FAMILY}/{rel}  {sha}  " in out, (rel, p.stdout)
    assert f"RESOLVED            {FAMILY}/tone-knob/tone-knob.json  {shas[0]}" in out
    assert "3 unresolved, 0 defect, 0 unavailable" in out and out.rstrip().endswith("FAIL: a claim is unresolved or self-contradictory (see above)")


def test_the_command_exits_two_when_it_cannot_answer(tmp_path):
    repo = tmp_path / "plain"
    git(tmp_path, "init", "-q", "-b", "main", str(repo))
    put(repo, "x/r.json", record(commit(repo, "only")))
    p = run_tool(repo)
    assert p.returncode == ccsp.ec.REFUSED and "UNAVAILABLE" in p.stdout
    assert p.stdout.rstrip().endswith("REFUSED: this checkout cannot answer; no verdict was reached")


def test_the_command_refuses_a_directory_with_nothing_to_check(tmp_path):
    p = run_tool(tmp_path, "--family", "docs/nowhere")
    assert p.returncode == ccsp.ec.REFUSED and "REFUSED: no JSON" in p.stdout


def test_an_explicit_main_ref_is_honoured(tmp_path):
    """The canonical ref is a parameter so a checkout without a remote can name
    its main; the default is not quietly replaced."""
    clone, shas = origin_clone(tmp_path)
    put(clone, "x/r.json", record(shas[0]))
    git(clone, "branch", "-m", "main", "trunk")
    git(clone, "update-ref", "-d", "refs/remotes/origin/main")
    assert run_tool(clone).returncode == ccsp.ec.REFUSED
    ok = run_tool(clone, main_ref="trunk")
    assert ok.returncode == ccsp.ec.PASS and "against trunk@" in ok.stdout


def test_a_copied_git_dir_cannot_redirect_the_lookup(tmp_path):
    """`-C` names the repository; an inherited GIT_DIR would override it and
    answer for some other checkout. The tool drops it."""
    clone, shas = origin_clone(tmp_path)
    other, other_shas = origin_clone(tmp_path / "other", prefix="other-")
    assert other_shas[0] not in shas
    put(clone, "x/r.json", record(other_shas[0]))          # not in `clone` at all
    env = dict(_env(), GIT_DIR=str(other / ".git"))
    # git itself IS redirected by that variable: the same lookup, unmangled,
    # says the commit is on origin/main -- of the OTHER repository
    p = subprocess.run(["git", "-C", str(clone), "merge-base", "--is-ancestor", other_shas[0], "origin/main"],
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr
    p = subprocess.run([sys.executable, str(TOOL), "--repo", str(clone)], capture_output=True, text=True, env=env)
    assert p.returncode == ccsp.ec.FAIL, p.stdout            # judged against clone, not `other`
    assert "UNRESOLVED" in p.stdout and shas[1] in p.stdout and other_shas[1] not in p.stdout


def test_the_census_reports_every_claim_and_judges_none(tmp_path):
    clone, shas = origin_clone(tmp_path)
    base = pathlib.Path(clone) / "docs" / "scorecard"
    (base / "a").mkdir(parents=True)
    (base / "a" / "top.json").write_text(json.dumps({"commit": shas[1], "sources_dirty": False}))
    (base / "a" / "prov.json").write_text(json.dumps({"provenance": {"commit": UNKNOWN_SHA}}))
    (base / "b").mkdir(parents=True)
    (base / "b" / "short.json").write_text(json.dumps({"commit": "1c8a941a-dirty"}))
    (base / "b" / "scoped.json").write_text(json.dumps({"controls_pass": True, "commit": shas[0]}))
    (base / "b" / "ignored.json").write_text("{not json")
    p = run_tool(clone, "--scan")
    assert p.returncode == ccsp.ec.PASS, p.stdout + p.stderr
    assert "NOT A GATE" in p.stdout
    rows, state = ccsp.census(str(clone), "docs/scorecard", "origin/main")
    got = {(r[0].rsplit("/", 1)[1], r[1]): r[-1] for r in rows}
    assert got == {("top.json", "top"): "ancestor", ("prov.json", "provenance"): "absent",
                   ("short.json", "top"): "short-sha", ("scoped.json", "top"): "ancestor"}
    assert [r[4] for r in rows if r[0].endswith("scoped.json")] == [True]
    assert "4 claims in 4 files: 1 absent, 2 ancestor, 1 short-sha" in p.stdout
    assert "not on origin/main (outside-main or absent): 1 in 1 directories under docs/scorecard/: a 1" in p.stdout


# ---------------------------------------------------------------------------
# the committed records
# ---------------------------------------------------------------------------
def _committed_main_ref():
    """`origin/main` where the clone has it. A checkout with no remote-tracking
    ref (a bundle clone on the build box) falls back to HEAD, which carries the
    same four answers, and says so; a shallow clone cannot answer and skips."""
    for ref in ("origin/main", "HEAD"):
        state = ccsp.repo_state(str(ROOT), ref)
        if state["main"] is not None:
            if state["shallow"]:
                pytest.skip("REFUSED: shallow clone; reachability of the committed records cannot be answered")
            return ref, state
    pytest.skip(f"REFUSED: {state['why']}; reachability of the committed records cannot be answered")


def test_the_committed_records_are_three_unknowns_and_one_resolved():
    """The acceptance the issue states, on the tree that ships: three
    historical unknowns and one resolvable source. The fourth is the positive
    control -- a lookup that could not say YES would fail here, and one that
    could not say NO would turn the three into DEFECTs."""
    ref, state = _committed_main_ref()
    rep = ccsp.evaluate(str(ROOT), FAMILY, ref)
    by = {r["path"][len(FAMILY) + 1:]: r for r in rep.rows}
    assert sorted(by) == ["low-tail/low-tail.json", "low-tail/m-origin.json",
                          "tone-knob/tone-knob.json", "vca-clip/vca-clip.json"]
    for rel, entry in ccsp.HISTORICAL_UNKNOWN.items():
        assert by[rel]["sha"] == entry["commit"], rel
        assert by[rel]["verdict"] == ccsp.HISTORICAL, by[rel]
        assert "not a verified source" in by[rel]["detail"]
    assert by["tone-knob/tone-knob.json"]["verdict"] == ccsp.RESOLVED
    assert rep.counts() == {ccsp.HISTORICAL: 3, ccsp.RESOLVED: 1}
    assert rep.exit_code == ccsp.ec.PASS
    text = "\n".join(rep.lines())
    for entry in ccsp.HISTORICAL_UNKNOWN.values():
        assert entry["commit"] in text
    assert "3 historical unknowns (annotated, never verified), 1 resolvable, 0 unresolved, 0 defect, 0 unavailable" in text
    assert ref == "origin/main" or state["main"], state      # the fallback is never silent


@pytest.mark.parametrize("rel", sorted(ccsp.HISTORICAL_UNKNOWN))
def test_each_committed_annotation_states_the_failed_lookup_and_its_date(rel):
    """A reader of the JSON alone can tell the source is unresolved, which
    lookup said so and when -- and the annotation agrees with the table that
    lets it through the gate."""
    entry = ccsp.HISTORICAL_UNKNOWN[rel]
    d = json.loads((ROOT / FAMILY / rel).read_text())
    assert d["commit"] == entry["commit"] and d["sources_dirty"] is False and d["controls_pass"] is True
    assert ccsp.annotation_defect(d, entry) is None
    a = d[ccsp.ANNOTATION]
    assert a["recorded_commit"] == entry["commit"] and a["status"] == "unresolved"
    assert a["lookup"]["is_ancestor"] is False and a["lookup"]["github_http_status"] == 422
    assert a["recorded"] == a["lookup"]["date"] == ccsp.RECORDED
    assert entry["landed_by"] in a["landed_by"]


def test_the_control_record_is_untouched_by_annotation():
    d = json.loads((ROOT / FAMILY / "tone-knob" / "tone-knob.json").read_text())
    assert ccsp.ANNOTATION not in d and d["sources_dirty"] is False and d["controls_pass"] is True
    assert d["commit"] == "1916d36103267d8648445901e501dd5c99d814e4"


@pytest.mark.parametrize("rel, shas", [
    ("low-tail/README.md", ["e3c565ce", "2d4abdb1"]),
    ("vca-clip/README.md", ["ac9bf865"]),
])
def test_the_readmes_tell_a_reader_the_source_is_unresolved(rel, shas):
    """The claim those files made was 'carrying the commit they were produced
    at'. A reader of the README, like a reader of the JSON, can now see that
    the commit does not resolve, which lookup said so, and when."""
    text = " ".join((ROOT / FAMILY / rel).read_text().split())     # line-wrap independent
    for sha in shas:
        assert re.search(r"\b" + sha + r"\b", text), (rel, sha)
    assert ccsp.RECORDED in text
    assert "check_cymbal_source_provenance" in text
    assert "No commit found for SHA" in text
    assert "cannot be reproduced from a known tree" in text


def test_the_check_runs_in_the_full_history_verification_job():
    """The gate needs full history and a main ref, so it is a job in
    `verify-full` rather than in the quick set; a job nobody runs is a gate
    that cannot fail."""
    make = (ROOT / "Makefile").read_text()
    assert "tools/check_cymbal_source_provenance.py" in make
    full = make.split("verify-full:", 1)[1].split("\n\n", 2)[0]
    assert "tools/check_cymbal_source_provenance.py" in full
