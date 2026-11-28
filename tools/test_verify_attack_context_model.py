"""The stored-audio verifier must detect numerical and provenance mutations."""
import ast
import copy
import hashlib
import json
import re
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


@pytest.mark.parametrize("commit", ["abc", "not-a-commit", "1" * 41, "--output=x", "A" * 40])
def test_malformed_source_commit_refuses_before_git(monkeypatch, commit):
    """A value that is not a full 40-hex id is a malformed report, not a rebase:
    refused by shape with no git run, since `cat-file -e` exits 128 alike for an
    absent object and for a name it cannot parse."""
    calls = []
    real_run = verifier.subprocess.run

    def spy(*args, **kwargs):
        calls.append(args)
        return real_run(*args, **kwargs)

    monkeypatch.setattr(verifier.subprocess, "run", spy)
    with pytest.raises(verifier.Refused, match="not a full 40-hex commit id") as caught:
        verifier.require_reachable(commit, verifier.producer.ROOT)
    assert calls == []
    assert isinstance(caught.value, AssertionError)


def test_malformed_source_commit_invokes_no_git(tmp_path, monkeypatch):
    """Through verify(): the refusal fires before any git runs, so a `--`-leading
    value never reaches `git show <commit>:<path>`, where git would read it as an option."""
    def forbidden(entry):
        def fail(*args, **kwargs):
            pytest.fail(f"{entry} reached with a malformed source_commit: {args}")
        return fail
    monkeypatch.setattr(verifier.subprocess, "run", forbidden("subprocess.run"))
    monkeypatch.setattr(verifier.subprocess, "check_output", forbidden("subprocess.check_output"))
    with pytest.raises(verifier.Refused, match="not a full 40-hex commit id"):
        verifier.verify(_with_commit(tmp_path, "--output=x"))


def test_shape_gate_defeated_misdiagnoses_a_rebase(monkeypatch):
    """Injected-bug control: with only the shape gate disabled, git runs on a value
    that is not a commit id and its 128 is read as 'rebased away' again."""
    monkeypatch.setattr(verifier, "FULL_COMMIT_ID", re.compile(r".*", re.DOTALL))
    with pytest.raises(verifier.Refused, match="rebased away"):
        verifier.require_reachable("not-a-commit", verifier.producer.ROOT)


def test_refused_is_an_assertion_error():
    assert issubclass(verifier.Refused, AssertionError)
