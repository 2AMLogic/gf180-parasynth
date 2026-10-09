"""Fixtures and controls for tools/reference_freshness.py (#622).

Every case builds a temporary git repo with committer dates pinned, so the
verdict depends on commit time only. A do-nothing checker (always FRESH, or
presence-and-age only) must fail at least one of these.
"""
from __future__ import annotations
import json, os, subprocess, sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reference_freshness as rf                                  # noqa: E402

DAY = 86400
NOW = 1_800_000_000
D = "docs/reference-freshness"


def row(dev):  # a normal response row: note there is NO "ok" key
    return {"device": dev, "res": 0.2, "cut_hz": 800.0,
            "freqs": [100.0, 200.0, 400.0], "gain_db": [0.0, -0.5, -3.0]}


def git(repo, *a, when=None):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    if when is not None:
        env["GIT_COMMITTER_DATE"] = env["GIT_AUTHOR_DATE"] = f"{when} +0000"
    subprocess.run(["git", "-C", str(repo), *a], check=True, env=env,
                   capture_output=True)


def make(tmp_path, ages=None, content=None):
    """ages: {device: days old}; content: {device: object or raw str}."""
    git(tmp_path, "init", "-q")
    (tmp_path / D).mkdir(parents=True)
    ages = ages if ages is not None else {d: 1 for d in rf.DEVICES}
    for dev, days in ages.items():
        c = (content or {}).get(dev, [row(dev)])
        (tmp_path / D / f"response-{dev}.json").write_text(
            c if isinstance(c, str) else json.dumps(c))
        git(tmp_path, "add", "-A")
        git(tmp_path, "commit", "-qm", dev, when=NOW - days * DAY)
    return str(tmp_path)


def run(repo, now=NOW):
    return rf.evaluate(repo, D, now)[0]


def test_fresh_passes_without_ok_key(tmp_path):
    assert run(make(tmp_path)) == rf.FRESH


def test_absent_is_refused(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / "x").write_text("x"); git(tmp_path, "add", "-A"); git(tmp_path, "commit", "-qm", "x")
    assert run(str(tmp_path)) == rf.REFUSED


def test_one_of_four_absent_is_refused(tmp_path):
    ages = {d: 1 for d in rf.DEVICES[:3]}
    v, m = rf.evaluate(make(tmp_path, ages), D, NOW)
    assert v == rf.REFUSED and "miniv3" in m


@pytest.mark.parametrize("days,want", [(14, rf.FRESH), (15, rf.STALE), (40, rf.STALE)])
def test_boundary(tmp_path, days, want):
    assert run(make(tmp_path, {d: days for d in rf.DEVICES})) == want


def test_boundary_is_floor_not_round(tmp_path):
    repo = make(tmp_path, {d: 14 for d in rf.DEVICES})
    assert run(repo, NOW + DAY - 1) == rf.FRESH   # 14.99 days -> 14
    assert run(repo, NOW + DAY) == rf.STALE


def test_oldest_of_four_decides(tmp_path):
    ages = {d: 1 for d in rf.DEVICES}; ages["surge-huov"] = 30
    v, m = rf.evaluate(make(tmp_path, ages), D, NOW)
    assert v == rf.STALE and "surge-huov" in m


def test_future_timestamp_refused(tmp_path):
    assert run(make(tmp_path, {d: -3 for d in rf.DEVICES})) == rf.REFUSED


def test_shallow_clone_refused(tmp_path):
    src = tmp_path / "src"; src.mkdir()
    make(src)
    (src / "more").write_text("m"); git(src, "add", "-A"); git(src, "commit", "-qm", "m", when=NOW)
    dst = tmp_path / "dst"
    subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{src}", str(dst)], check=True,
                   capture_output=True)
    v, m = rf.evaluate(str(dst), D, NOW)
    assert v == rf.REFUSED and "shallow" in m


@pytest.mark.parametrize("name,bad,reason", [
    ("invalid-json", "{not json", "invalid JSON"),
    ("empty-list", [], "empty"),
    ("wrong-device", [row("diva")], "expected device"),
    ("stub", [{"device": "ours", "ok": False, "not_answerable": "no plugin"}], "not_answerable"),
    ("no-curve", [{"device": "ours"}], "usable"),
    ("nan-curve", [dict(row("ours"), gain_db=[0.0, float("nan"), 1.0])], "usable"),
])
def test_unusable_fresh_evidence_is_refused_for_the_named_reason(tmp_path, name, bad, reason):
    """A FRESH commit of unusable evidence must not pass (the guard's defeating input)."""
    v, m = rf.evaluate(make(tmp_path, content={"ours": bad}), D, NOW)
    assert v == rf.REFUSED and reason in m, (v, m)


def test_mtime_cannot_change_verdict(tmp_path):
    repo = make(tmp_path, {d: 30 for d in rf.DEVICES})
    for d in rf.DEVICES:
        os.utime(f"{repo}/{D}/response-{d}.json", (NOW, NOW))
    assert run(repo) == rf.STALE


def test_cli_exit_codes_and_limitation_text(tmp_path, capsys):
    repo = make(tmp_path)
    assert rf.main(["--repo", repo, "--now", str(NOW)]) == 0
    out = capsys.readouterr().out
    assert "COMMIT age" in out and "does not validate the synth" in out
    assert "devices=ours,surge-rk,surge-huov,miniv3" in out
    assert rf.main(["--repo", str(tmp_path / "nope"), "--now", str(NOW)]) == 2
    assert "reference_compare.py --stage response" in capsys.readouterr().out


def test_do_nothing_stub_is_caught(tmp_path, monkeypatch):
    """Control: an always-FRESH checker must fail the stale/absent expectations."""
    monkeypatch.setattr(rf, "evaluate", lambda *a: (rf.FRESH, "stub"))
    with pytest.raises(AssertionError):
        assert run(make(tmp_path, {d: 40 for d in rf.DEVICES})) == rf.STALE


def test_old_shell_check_wrongly_refused_a_valid_fixture(tmp_path):
    """Start-red record: the retired workflow step, run on a valid fresh snapshot."""
    repo = make(tmp_path)
    old = 'R=docs/reference-comparison-results.md; if [ ! -f "$R" ]; then exit 1; fi'
    assert subprocess.run(["bash", "-c", old], cwd=repo).returncode == 1
    assert run(repo) == rf.FRESH
