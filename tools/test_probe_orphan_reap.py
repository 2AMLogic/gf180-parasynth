#!/usr/bin/env python3
"""Tests for tools/probe_orphan_reap.py.

The probe's only failure mode that matters is a FALSE GREEN: reporting "all
clear" on a host that is holding a process no reaper can kill. So the central
case here is the EPERM one, driven through an injected kill probe rather than by
arranging a root-owned process -- the verdict logic must be checkable on a
machine where the incident cannot be reproduced.
"""

from __future__ import annotations

import errno
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import probe_orphan_reap as probe  # noqa: E402


def facts(pid: int, container: str | None = None) -> probe.ProcFacts:
    return probe.ProcFacts(
        pid=pid,
        uid=0,
        pgid=pid,
        sid=pid,
        argv=f"/bin/thing --config /repo/.loom/worktrees/issue-33/x.yaml",
        worktrees=["issue-33"],
        container=container,
    )


def test_eperm_process_is_reported_unreachable():
    """The incident's shape: the process exists and this uid may not signal it."""
    verdicts = probe.classify([facts(4242)], kill_probe=lambda _pid: probe.UNREACHABLE)
    assert [v.reachability for v in verdicts] == [probe.UNREACHABLE]
    assert len(probe.unreachable(verdicts)) == 1


def test_signalable_process_is_not_flagged():
    verdicts = probe.classify([facts(4242)], kill_probe=lambda _pid: probe.REACHABLE)
    assert probe.unreachable(verdicts) == []


def test_dead_process_is_dropped_not_reported():
    """A pid that vanished between the scan and the probe is not a finding."""
    assert probe.classify([facts(4242)], kill_probe=lambda _pid: probe.GONE) == []


def test_probe_kill_reports_reachable_for_our_own_pid():
    assert probe.probe_kill(os.getpid()) == probe.REACHABLE


def test_probe_kill_reports_gone_for_a_pid_that_cannot_exist():
    # pid 1 always exists; a pid above the host maximum never does.
    ceiling = int(Path("/proc/sys/kernel/pid_max").read_text().strip())
    assert probe.probe_kill(ceiling) == probe.GONE


def test_probe_kill_maps_eperm_to_unreachable(monkeypatch):
    def deny(_pid, _sig):
        raise PermissionError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(probe.os, "kill", deny)
    assert probe.probe_kill(4242) == probe.UNREACHABLE


def test_container_id_is_parsed_from_both_cgroup_layouts(tmp_path, monkeypatch):
    long_id = "d85f2b66bfe6dfd90c8581a4ad183904c4f125c7cb4c5ad47c59a32c60567dbf"
    for text, expected in (
        (f"0::/system.slice/docker-{long_id}.scope\n", long_id),
        (f"11:cpu:/docker/{long_id}\n", long_id),
        ("0::/user.slice/user-1000.slice/loom.slice/loom-agent-1-2.scope\n", None),
    ):
        monkeypatch.setattr(probe, "_read", lambda _p, t=text: t)
        assert probe._container_id(1) == expected


def test_worktree_dirs_only_returns_issue_dirs(tmp_path):
    base = tmp_path / ".loom" / "worktrees"
    (base / "issue-33").mkdir(parents=True)
    (base / "issue-310").mkdir()
    (base / "pr-9").mkdir()
    (base / "issue-notadir").write_text("x")
    assert [p.name for p in probe.worktree_dirs(tmp_path)] == ["issue-310", "issue-33"]


def test_worktree_dirs_is_empty_when_there_are_none(tmp_path):
    assert probe.worktree_dirs(tmp_path) == []


def test_collect_attributes_a_process_by_argv(tmp_path):
    """A real child whose argv names the worktree is attributed to it.

    This is the attribution rule the incident's LibreLane tree matched: its cwd
    was inside the container, but its argv carried the host worktree path.
    """
    wt = tmp_path / ".loom" / "worktrees" / "issue-33"
    wt.mkdir(parents=True)
    marker = str(wt.resolve()) + "/pnr/config.yaml"
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", marker])
    try:
        found = probe.collect(tmp_path, probe.worktree_dirs(tmp_path), os.getpid())
        pids = {f.pid for f in found}
        assert child.pid in pids, f"argv-attributed child {child.pid} missing from {pids}"
        mine = next(f for f in found if f.pid == child.pid)
        assert mine.worktrees == ["issue-33"]
        assert mine.uid == os.getuid()
    finally:
        child.kill()
        child.wait()


def test_collect_never_attributes_sibling_prefix_worktrees(tmp_path):
    """Issue numbers are unpadded decimal, so one worktree name is a string
    prefix of another whenever one number is a decimal prefix of the other
    (`issue-77` / `issue-775`). Attribution must match at a path-component
    boundary, in both the argv test and the cwd test, or `issue-775`'s work is
    filed under `issue-77` as well (issue #819).
    """
    wt77 = (tmp_path / ".loom" / "worktrees" / "issue-77").resolve()
    wt775 = (tmp_path / ".loom" / "worktrees" / "issue-775").resolve()
    wt77.mkdir(parents=True)
    wt775.mkdir(parents=True)
    argv_child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import time; time.sleep(30)",
            str(wt775 / "pnr" / "config.yaml"),
        ]
    )
    cwd_child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], cwd=str(wt775)
    )
    own_child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import time; time.sleep(30)",
            str(wt77 / "pnr" / "config.yaml"),
        ]
    )
    try:
        found = probe.collect(tmp_path, probe.worktree_dirs(tmp_path), os.getpid())
        by_pid = {f.pid: f for f in found}
        assert argv_child.pid in by_pid, (
            f"issue-775 argv child {argv_child.pid} missing from {sorted(by_pid)}"
        )
        assert by_pid[argv_child.pid].worktrees == ["issue-775"]
        assert cwd_child.pid in by_pid, (
            f"issue-775 cwd child {cwd_child.pid} missing from {sorted(by_pid)}"
        )
        assert by_pid[cwd_child.pid].worktrees == ["issue-775"]
        # The genuine issue-77 work is still attributed when the prefix
        # sibling exists.
        assert own_child.pid in by_pid, (
            f"issue-77 child {own_child.pid} missing from {sorted(by_pid)}"
        )
        assert by_pid[own_child.pid].worktrees == ["issue-77"]
    finally:
        for child in (argv_child, cwd_child, own_child):
            child.kill()
            child.wait()


def test_collect_attributes_paths_that_end_a_longer_token(tmp_path):
    """Shapes the #860 predicate drops must be attributed again (issue #870).

    `--out-dir=<path>` and `-v <path>:<path>` name the worktree only at the
    end of a longer token: no following separator, and not a whole token.
    Every child runs with cwd=/ so the cwd test cannot attribute it -- only
    the argv test decides, matching the measurement that found the loss.
    """
    wt = (tmp_path / ".loom" / "worktrees" / "issue-775").resolve()
    wt.mkdir(parents=True)
    w = str(wt)
    shapes = {
        "final bare token": [w],
        "mid token at a component boundary": [f"--metrics={w}/out.json"],
        "end of a longer token": [f"--out-dir={w}"],
        "trailing separator": [w + os.sep],
        "colon mount token": ["-v", f"{w}:{w}", "-w", f"{w}/pnr/runs"],
    }
    children = {
        label: subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"] + extra, cwd="/"
        )
        for label, extra in shapes.items()
    }
    try:
        found = {
            f.pid: f
            for f in probe.collect(tmp_path, probe.worktree_dirs(tmp_path), os.getpid())
        }
        for label, child in children.items():
            assert child.pid in found, f"{label}: child {child.pid} not attributed"
            assert found[child.pid].worktrees == ["issue-775"], label
    finally:
        for child in children.values():
            child.kill()
            child.wait()


def test_collect_rejects_name_extensions_in_the_longer_token_shapes(tmp_path):
    """The #819 name-extension false positives stay rejected when the path
    sits at the end of a longer token or before a colon (#870): `issue-77`
    must not be attributed from `issue-775`, `issue-77-b` or `issue-77.x`
    text, and `issue-775` not from `issue-775-b`.
    """
    wt77 = (tmp_path / ".loom" / "worktrees" / "issue-77").resolve()
    wt775 = (tmp_path / ".loom" / "worktrees" / "issue-775").resolve()
    wt77.mkdir(parents=True)
    wt775.mkdir(parents=True)
    w77, w775 = str(wt77), str(wt775)
    children = {
        "775, numeric extension of 77": subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)", f"--out-dir={w775}"],
            cwd="/",
        ),
        "77, hyphen extension": subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import time; time.sleep(30)",
                f"--config={w77}-b/thing.yaml",
            ],
            cwd="/",
        ),
        "77, dot extension": subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import time; time.sleep(30)",
                f"--junit-xml={w77}.x",
            ],
            cwd="/",
        ),
        "775, hyphen extension": subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)", f"--out-dir={w775}-b"],
            cwd="/",
        ),
    }
    try:
        found = {
            f.pid: f
            for f in probe.collect(tmp_path, probe.worktree_dirs(tmp_path), os.getpid())
        }
        for label, child in children.items():
            mine = found.get(child.pid)
            if mine is not None:
                assert mine.worktrees == ["issue-775"], (
                    f"{label}: attributed to {mine.worktrees}"
                )
    finally:
        for child in children.values():
            child.kill()
            child.wait()


def test_argv_names_matches_paths_that_end_a_longer_token():
    w = "/repo/.loom/worktrees/issue-775"
    for argv in (
        w,  # final bare token
        f"--out-dir={w}",  # end of a longer token
        f"-v {w}:{w}",  # colon mount (#870's lost shape)
        f"--metrics={w}/out.json",  # component boundary mid-token
        w + "/",  # trailing separator
        f"{w}\n--flag",  # newline boundary
        f"{w}\t--flag",  # tab boundary
    ):
        assert probe._argv_names(argv, w), argv


def test_argv_names_rejects_name_extensions_and_glued_text():
    w = "/repo/.loom/worktrees/issue-77"
    for argv in (
        "--out-dir=/repo/.loom/worktrees/issue-775",  # numeric extension
        f"--config={w}-b/thing.yaml",  # hyphen extension
        f"--junit-xml={w}.x",  # dot extension
        f"{w}5/a",  # glued digit mid-token
    ):
        assert not probe._argv_names(argv, w), argv


def test_argv_names_treats_the_path_as_a_literal_not_a_pattern():
    """A worktree path may contain regex metacharacters; an unescaped dot
    would match text the path does not name."""
    w = "/re.po/(wt)/worktrees+issue-7"
    assert probe._argv_names(f"--out-dir={w}", w)
    assert not probe._argv_names("/reXpo/(wt)/worktrees+issue-7", w)
    assert not probe._argv_names("/re.po/XwtX/worktrees+issue-7", w)


@pytest.mark.skipif(not sys.platform.startswith("linux") or not Path("/proc/self/cmdline").exists(),
                    reason="the empty-worktree observation requires Linux /proc")
def test_main_exits_zero_when_repo_has_no_worktrees(tmp_path, capsys):
    assert probe.main(["--repo-root", str(tmp_path)]) == 0
    assert "no issue worktrees" in capsys.readouterr().out


def test_main_refuses_rather_than_reporting_on_a_non_linux_host(monkeypatch, tmp_path, capsys):
    """REFUSED, not a comfortable zero, when the apparatus cannot answer."""
    monkeypatch.setattr(probe.sys, "platform", "darwin")
    assert probe.main(["--repo-root", str(tmp_path)]) == 3
    out = capsys.readouterr().out
    assert "REFUSED" in out
    assert "needs a Linux /proc" in out
    assert "not a pass" in out


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="needs /proc")
def test_main_runs_against_this_repo_and_returns_a_defined_status():
    """End to end on the real host: 0 or 1, never a crash and never REFUSED."""
    rc = probe.main([])
    assert rc in (0, 1)
