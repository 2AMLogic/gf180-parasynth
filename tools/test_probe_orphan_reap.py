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


def test_main_exits_zero_when_repo_has_no_worktrees(tmp_path, capsys):
    assert probe.main(["--repo-root", str(tmp_path)]) == 0
    assert "no issue worktrees" in capsys.readouterr().out


def test_main_refuses_rather_than_reporting_on_a_non_linux_host(monkeypatch, capsys):
    """REFUSED, not a comfortable zero, when the apparatus cannot answer."""
    monkeypatch.setattr(probe.sys, "platform", "darwin")
    assert probe.main([]) == 3
    out = capsys.readouterr().out
    assert "REFUSED" in out
    assert "not a pass" in out


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="needs /proc")
def test_main_runs_against_this_repo_and_returns_a_defined_status():
    """End to end on the real host: 0 or 1, never a crash and never REFUSED."""
    rc = probe.main([])
    assert rc in (0, 1)
