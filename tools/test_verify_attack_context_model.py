"""The stored-audio verifier must detect numerical and provenance mutations."""
import ast
import copy
import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

import verify_attack_context_model as verifier

REPORT = verifier.producer.ROOT / "docs/scorecard/mono-attack-context/model/report.json"


def test_stored_audio_reproduces_without_rendering(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("stored audio verification attempted a voice render")
    monkeypatch.setattr(verifier.producer.lead.vf, "render_mono_fx", forbidden)
    result = verifier.verify(REPORT)
    assert result["exact_timing_rows"] == 12
    assert result["history_contrasts"] == 6
    assert result["render_source_commit"] == json.loads(REPORT.read_text())["source_commit"]
    assert "historical" in result["evidence_basis"]


@pytest.mark.parametrize("mutation", ["timing", "history", "missing-row", "audio-hash", "invalid", "source-hash"])
def test_changed_evidence_refuses(tmp_path, mutation):
    record = copy.deepcopy(json.loads(REPORT.read_text()))
    for row in record["rows"]:
        audio = Path(row["audio"]).name
        (tmp_path / audio).symlink_to(REPORT.parent / audio)
    if mutation == "timing":
        record["rows"][0]["model_timing"]["attack_10_90_ms"] += 1 / 48
    elif mutation == "history":
        record["history_effect_ms"][0]["model_attack_ms"] += 1
    elif mutation == "missing-row":
        record["rows"].pop()
    elif mutation == "audio-hash":
        record["rows"][0]["sha256"] = "0" * 64
    elif mutation == "invalid":
        record["rows"][0]["model_timing"]["valid"] = False
    elif mutation == "source-hash":
        record["source_sha256"]["model/voice_fx.py"] = "0" * 64
    mutated = tmp_path / "report.json"
    mutated.write_text(json.dumps(record))
    with pytest.raises(AssertionError):
        verifier.verify(mutated)


def _serve_audio_measure(monkeypatch, edit):
    """Give the verifier `edit(current source)` as model/audio_measure.py.

    WRONG BEFORE IT WAS RIGHT: this used to patch only `Path.read_text`, but
    the verifier hashes sources through `ref.sha256`, which reads bytes via
    `Path.open("rb")` -- so the served text never reached the hash and the
    unrelated-edit control below passed only while the COMMITTED report's
    audio_measure digest happened to be stale (basis-stable drift since its
    generation). Regenerating the report (#815 re-based two of its bound
    sources) refreshed that digest and exposed the latent defect. Both read
    paths are now served the edited text, so the simulation is real and the
    control passes on a fresh report.
    """
    real = Path.read_text
    target = verifier.producer.ROOT / verifier.AUDIO_MEASURE
    monkeypatch.setattr(Path, "read_text",
                        lambda self, *a, **k: edit(real(self, *a, **k)) if self == target else real(self, *a, **k))
    ref = verifier.producer.context.ref
    real_sha = ref.sha256

    def served_sha256(path):
        if path != target:
            return real_sha(path)
        return hashlib.sha256(edit(real(target, encoding="utf-8")).encode()).hexdigest()

    monkeypatch.setattr(ref, "sha256", served_sha256)


def _mutate(old, new):
    def edit(src):
        assert old in src, "mutation target moved; the control would be vacuous"
        return src.replace(old, new, 1)
    return edit


def test_changed_analysis_basis_refuses(monkeypatch):
    """Injected defects in the executed basis must refuse: the envelope itself and its helper."""
    _serve_audio_measure(monkeypatch, _mutate("return e * math.sqrt(2.0)", "return e * 1.4"))
    with pytest.raises(AssertionError, match="analysis basis changed"):
        verifier.verify(REPORT)


def test_changed_basis_helper_refuses(monkeypatch):
    _serve_audio_measure(monkeypatch, _mutate("what makes this module's NaN boundary ONE line",
                                              "what makes this module's NaN boundary one line"))
    with pytest.raises(AssertionError, match="analysis basis changed"):
        verifier.verify(REPORT)


def test_unrelated_estimator_edit_does_not_refuse(monkeypatch):
    """The narrowing's point: an edit outside the executed basis is reported, not refused."""
    _serve_audio_measure(monkeypatch, lambda src: src + "\n\ndef _unrelated_probe():\n    return 1\n")
    result = verifier.verify(REPORT)
    assert verifier.AUDIO_MEASURE in result["current_source_differences"]


def test_rms_envelope_dependencies_are_pinned():
    """The pinned basis is closed: nothing in it reaches a module-level name outside it."""
    tree = ast.parse((verifier.producer.ROOT / verifier.AUDIO_MEASURE).read_text())
    defs = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
    module_names = set(defs) | {t.id for n in tree.body if isinstance(n, ast.Assign)
                                for t in n.targets if isinstance(t, ast.Name)}
    for name in verifier.AUDIO_MEASURE_BASIS:
        if name in defs:
            used = {n.id for n in ast.walk(defs[name]) if isinstance(n, ast.Name)} & module_names
            assert used <= set(verifier.AUDIO_MEASURE_BASIS) | {name}, (name, used - set(verifier.AUDIO_MEASURE_BASIS))


# --- #215: an unreachable source_commit must REFUSE, not surface git's exit 128 ---

def _with_commit(tmp_path, commit):
    record = json.loads(REPORT.read_text())
    record["source_commit"] = commit
    for row in record["rows"]:
        audio = Path(row["audio"]).name
        (tmp_path / audio).symlink_to(REPORT.parent / audio)
    out = tmp_path / "report.json"
    out.write_text(json.dumps(record))
    return out


def test_absent_source_commit_refuses_clearly(tmp_path):
    """A rebased-away SHA (object not present at all) names the cause and the remedy."""
    with pytest.raises(verifier.Refused, match="source_commit .* not reachable.*regenerate"):
        verifier.verify(_with_commit(tmp_path, "1" * 40))


def test_dangling_source_commit_refuses(tmp_path):
    """The input that defeats a bare existence check: the object IS in this clone's
    object database (as it is in the clone that made the pre-rebase commit) but is
    not an ancestor of HEAD, so a fresh CI checkout would not have it."""
    import subprocess
    root = verifier.producer.ROOT
    tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=root, text=True).strip()
    dangling = subprocess.check_output(["git", "commit-tree", tree, "-m", "dangling #215 control"],
                                       cwd=root, text=True,
                                       env={**__import__("os").environ, "GIT_AUTHOR_NAME": "t",
                                            "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                                            "GIT_COMMITTER_EMAIL": "t@t"}).strip()
    subprocess.check_call(["git", "cat-file", "-e", dangling], cwd=root)  # control: it exists
    with pytest.raises(verifier.Refused, match="not an ancestor of HEAD"):
        verifier.verify(_with_commit(tmp_path, dangling))


def test_guard_defeated_goes_red(tmp_path, monkeypatch):
    """Injected-bug control: with the guard disabled the old opaque failure returns."""
    import subprocess
    monkeypatch.setattr(verifier, "require_reachable", lambda *a, **k: None)
    with pytest.raises(subprocess.CalledProcessError):
        verifier.verify(_with_commit(tmp_path, "1" * 40))


def test_refused_is_an_assertion_error():
    assert issubclass(verifier.Refused, AssertionError)


# --- #2084: an absent commit and a malformed name are two refusals, not one ---

ABSENT_COMMIT = "3" * 40
MALFORMED_COMMITS = ["not-a-commit", "abc", "3" * 41]


def _git(*args):
    return subprocess.run(["git", *args], cwd=verifier.producer.ROOT, capture_output=True, text=True)


def _refusal(commit):
    """`require_reachable`'s refusal for `commit` in this checkout: real git, no report."""
    with pytest.raises(verifier.Refused) as refused:
        verifier.require_reachable(commit, verifier.producer.ROOT)
    return str(refused.value)


def _diagnosis(tmp_path, commit):
    """The refusal `verify` raises for a report that records `commit`."""
    with pytest.raises(verifier.Refused) as refused:
        verifier.verify(_with_commit(tmp_path, commit))
    return str(refused.value)


def _assert_diagnosed_malformed(message):
    """The malformed case's own assertion, on one refusal's text."""
    assert "malformed" in message, "not named malformed"
    assert "rebased away" not in message and "shallow clone" not in message, "given the absent refusal's text"
    assert "regenerate the report" not in message, "given the absent refusal's remedy"


def test_object_status_reads_gits_own_three_answers():
    """What the split rests on: `git cat-file -e -- <name>` exits 0 for an object
    the clone holds, 1 for a full object name it does not, and 128 for a name it
    will not resolve -- where the `^{commit}` peel it replaced exited 128 for the
    last two alike (measured on git 2.43.0 before the split was written). A git
    that changes the contract turns this red by name, not as a misdiagnosed
    refusal downstream."""
    root = verifier.producer.ROOT
    head = _git("rev-parse", "HEAD").stdout.strip()
    assert verifier.object_status(head, root) == (0, "")
    assert verifier.object_status(ABSENT_COMMIT, root) == (1, "")
    for commit in MALFORMED_COMMITS:
        status, reason = verifier.object_status(commit, root)
        assert status == 128, repr(commit)
        assert commit in reason, (repr(commit), reason)


def test_absent_source_commit_refuses_as_absent(tmp_path):
    """A full object name this clone does not hold keeps the absent refusal: the
    rebased-away-or-shallow-clone text and its regenerate-the-report remedy, and
    not the malformed record's."""
    message = _diagnosis(tmp_path, ABSENT_COMMIT)
    assert "rebased away, or a shallow clone" in message and "regenerate the report" in message
    assert "malformed" not in message


@pytest.mark.parametrize("commit", MALFORMED_COMMITS)
def test_malformed_source_commit_refuses_as_malformed(tmp_path, commit):
    """A `source_commit` git will not parse is refused as a malformed record: so
    named, carrying git's own reason, and without the absent refusal's text or
    its regenerate-on-the-rebased-branch remedy, which sent the operator after
    nothing (#2084)."""
    message = _diagnosis(tmp_path, commit)
    _assert_diagnosed_malformed(message)
    assert verifier.object_status(commit, verifier.producer.ROOT)[1] in message


def test_a_leading_dash_source_commit_is_a_name_not_an_option():
    """Rule 8's adversary for the unpeeled probe. `--` is what makes git read
    `--batch` as an object name, which it will not resolve (128, refused as
    malformed). Read as an option it is a usage error instead (129 on git
    2.43.0, for each of the dozen options tried) -- a status with no diagnosis,
    where the peel's `--batch^{commit}` was merely an unknown one."""
    assert verifier.object_status("--batch", verifier.producer.ROOT)[0] == 128
    assert "malformed" in _refusal("--batch")


def _present_non_ancestor(kind):
    """A real object this clone holds that is not an ancestor of HEAD."""
    if kind == "commit":
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        return subprocess.check_output(["git", "commit-tree", "HEAD^{tree}", "-m", "dangling #2084"],
                                       cwd=verifier.producer.ROOT, text=True, env=env).strip()
    what = "HEAD^{tree}" if kind == "tree" else f"HEAD:{verifier.AUDIO_MEASURE}"
    return _git("rev-parse", what).stdout.strip()


@pytest.mark.parametrize("kind", ["commit", "tree", "blob"])
def test_a_present_object_that_is_not_an_ancestor_is_not_reported_absent(kind):
    """The peel asserted commit-ness at the existence probe by reporting a tree
    or a blob as a commit the checkout lacks. The unpeeled probe passes all
    three (they are here) and the unchanged ancestor check refuses them:
    present, but not an ancestor of HEAD. The object's type is no longer
    diagnosed, which is stated here rather than hidden."""
    commit = _present_non_ancestor(kind)
    assert verifier.object_status(commit, verifier.producer.ROOT)[0] == 0
    message = _refusal(commit)
    assert "not an ancestor of HEAD" in message
    assert "no such commit in this checkout" not in message and "shallow clone" not in message


@pytest.mark.parametrize("commit", ["", None], ids=["empty", "null"])
def test_a_missing_source_commit_is_reported_missing(tmp_path, commit):
    """`None` and `""` never reach git: both are a report without a
    source_commit. `""` is otherwise a name git will not resolve (128), so
    without that guard it would be called a malformed record instead."""
    assert "source_commit is missing from the report" in _diagnosis(tmp_path, commit)


def test_a_status_git_does_not_document_refuses_without_a_diagnosis(tmp_path, monkeypatch):
    """Any other non-zero status refuses too, and names no cause it did not
    establish. Real git answered only 0, 1 and 128 for every name tried, so the
    branch is reached through a stand-in `git` first on PATH -- a real child of
    the real subprocess call, exiting 3, as a host's git wrapper might."""
    shim = tmp_path / "bin"
    shim.mkdir()
    (shim / "git").write_text("#!/bin/sh\necho 'stand-in git, refusing' >&2\nexit 3\n")
    (shim / "git").chmod(0o755)
    monkeypatch.setenv("PATH", f"{shim}{os.pathsep}{os.environ['PATH']}")
    message = _refusal("8eb04e5b")
    assert "exited 3" in message and "stand-in git, refusing" in message
    assert "rebased away" not in message and "shallow clone" not in message and "malformed" not in message


def test_control_the_peeled_probe_reports_a_malformed_record_as_absent(monkeypatch):
    """Rule 5's control: the defect as it shipped -- `git cat-file -e
    <commit>^{commit}` with every non-zero read as one cause -- reinstated one
    function down. Real git answers it non-zero for a malformed name and for an
    absent one alike, so the refusal that follows is the absent one, and the
    assertion that fails is the malformed case's own: its diagnosis. The clean
    probe makes that same assertion pass."""
    def peeled(commit, root):
        probe = subprocess.run(["git", "cat-file", "-e", f"{commit}^{{commit}}"], cwd=root, capture_output=True)
        return (0 if probe.returncode == 0 else 1), probe.stderr.decode(errors="replace").strip()

    monkeypatch.setattr(verifier, "object_status", peeled)
    for commit in MALFORMED_COMMITS:
        message = _refusal(commit)
        assert "rebased away, or a shallow clone" in message, message
        with pytest.raises(AssertionError, match="not named malformed") as failed:
            _assert_diagnosed_malformed(message)
        assert not isinstance(failed.value, verifier.Refused)
