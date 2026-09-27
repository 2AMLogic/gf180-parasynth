#!/usr/bin/env python3
"""Find processes attributable to a Loom issue worktree that this uid CANNOT signal.

WHY THIS EXISTS. On 2026-09-26 a LibreLane place-and-route launched from
`.loom/worktrees/issue-33` outlived the sweep that started it by nearly three
hours on a shared 8-vCPU dispatch worker (gf180-parasynth#310). The incident
report said "nothing reaps it, nothing surfaces it". Both halves were wrong, and
the way they were wrong is the finding:

  * Loom's `orphan_process_reaper` (rjwalters/loom#5110) DID attribute the tree
    to the worktree by argv, and DID send SIGSTOP/SIGTERM/SIGKILL, three passes
    in a row -- 23:35:37, 23:50:46, 00:05:50 -- logging each time
    `[pid, pid] survived SIGKILL`.
  * The kills could never have worked. `docker run` had handed the work to
    dockerd, which started it as **root**; every Loom reaper runs as the
    unprivileged daemon uid, so `kill(2)` returned **EPERM**. The kernel refused,
    not the process.

So the signal that matters is not "is this process an orphan" -- Loom already
computes that -- but "is there a process here that no Loom reaper can ever
terminate". That is one `kill(pid, 0)` away, and nothing was asking.

WHAT IT REPORTS. Processes whose argv or cwd names a `.loom/worktrees/issue-<N>`
directory, each classified by whether this uid may signal it:

  REACHABLE     kill(pid, 0) succeeded -- an ordinary reaper can terminate it
  UNREACHABLE   kill(pid, 0) -> EPERM  -- no Loom reaper can ever terminate it

A `container=<id>` field is derived from `/proc/<pid>/cgroup` when the process
sits in a `docker-<id>.scope`, because that is the case where the *only* working
teardown is `docker stop`/`docker kill` through the socket rather than a signal.

This probe deliberately does NOT kill anything and does NOT decide whether the
owning sweep is alive. It answers one question whose answer is cheap and was
missing. Disposition is a human/orchestrator decision: gf180-parasynth#310
argues surfacing a live routing run to the next dispatch beats discarding 75
minutes of work, and an UNREACHABLE verdict is exactly the input that decision
needs.

PRECONDITIONS ARE ASSERTED, NOT ASSUMED (CLAUDE.md). Without a Linux `/proc`
this cannot answer, and a tool that answers when it cannot is worse than one
that is absent -- so it prints REFUSED and exits 3 rather than reporting a
comfortable zero.

EXIT STATUS
  0  no worktree-attributed process is unsignalable by this uid
  1  at least one UNREACHABLE process found
  2  usage error
  3  REFUSED -- preconditions for answering are not met
"""

from __future__ import annotations

import argparse
import errno
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# `0::/system.slice/docker-<64 hex>.scope` for a dockerd-managed container on
# cgroup v2; the `cgroup`-v1 form carries the same id under a `/docker/<id>`
# path. Both are matched, and only the id is taken.
_CONTAINER_ID = re.compile(r"docker[-/]([0-9a-f]{12,64})(?:\.scope)?")

REACHABLE = "REACHABLE"
UNREACHABLE = "UNREACHABLE"
GONE = "GONE"


@dataclass
class ProcFacts:
    """Everything about one process this probe needs, already read off /proc.

    Kept as plain data with no /proc access of its own so `classify` is
    testable against synthetic input -- the probe's own verdict logic must be
    checkable without arranging a root-owned container on the test host.
    """

    pid: int
    uid: int | None
    pgid: int | None
    sid: int | None
    argv: str
    worktrees: list[str] = field(default_factory=list)
    container: str | None = None


@dataclass
class Verdict:
    facts: ProcFacts
    reachability: str

    def render(self) -> str:
        bits = [
            f"{self.reachability:<11}",
            f"pid={self.facts.pid}",
            f"uid={self.facts.uid}",
            f"pgid={self.facts.pgid}",
            f"sid={self.facts.sid}",
        ]
        if self.facts.container:
            bits.append(f"container={self.facts.container[:12]}")
        bits.append("worktree=" + ",".join(self.facts.worktrees))
        bits.append("argv=" + self.facts.argv[:120])
        return "  ".join(bits)


def probe_kill(pid: int) -> str:
    """Classify whether THIS uid may signal `pid`, without signalling it.

    Signal 0 performs the permission and existence checks and delivers nothing.
    EPERM is the whole point: the process exists and we may not touch it.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return GONE
    except PermissionError:
        return UNREACHABLE
    except OSError as exc:  # pragma: no cover - defensive
        if exc.errno == errno.ESRCH:
            return GONE
        if exc.errno == errno.EPERM:
            return UNREACHABLE
        raise
    return REACHABLE


def classify(entries: list[ProcFacts], kill_probe=probe_kill) -> list[Verdict]:
    """Attach a reachability verdict to every attributed process.

    `kill_probe` is injected so tests can assert the EPERM path without needing
    a process they genuinely cannot signal.
    """
    verdicts = [Verdict(facts=e, reachability=kill_probe(e.pid)) for e in entries]
    return [v for v in verdicts if v.reachability != GONE]


def unreachable(verdicts: list[Verdict]) -> list[Verdict]:
    return [v for v in verdicts if v.reachability == UNREACHABLE]


def worktree_dirs(repo_root: Path) -> list[Path]:
    base = repo_root / ".loom" / "worktrees"
    if not base.is_dir():
        return []
    return sorted(p for p in base.iterdir() if p.is_dir() and p.name.startswith("issue-"))


def _read(path: Path) -> str | None:
    try:
        return path.read_text(errors="replace")
    except (OSError, UnicodeDecodeError):
        return None


def _proc_status(pid: int) -> tuple[int | None, int | None, int | None]:
    """(uid, pgid, sid) for `pid`, each None when unreadable."""
    uid = pgid = sid = None
    status = _read(Path("/proc") / str(pid) / "status")
    if status:
        for line in status.splitlines():
            if line.startswith("Uid:"):
                parts = line.split()
                if len(parts) > 1 and parts[1].isdigit():
                    uid = int(parts[1])
                break
    stat = _read(Path("/proc") / str(pid) / "stat")
    if stat and ")" in stat:
        # comm may contain spaces and parentheses; fields resume after the last ')'
        fields = stat[stat.rindex(")") + 1 :].split()
        # fields[0] is state; pgid is field 5 and sid field 6 of the full stat
        if len(fields) > 3:
            pgid = int(fields[2]) if fields[2].lstrip("-").isdigit() else None
            sid = int(fields[3]) if fields[3].lstrip("-").isdigit() else None
    return uid, pgid, sid


def _container_id(pid: int) -> str | None:
    cgroup = _read(Path("/proc") / str(pid) / "cgroup")
    if not cgroup:
        return None
    m = _CONTAINER_ID.search(cgroup)
    return m.group(1) if m else None


def collect(repo_root: Path, worktrees: list[Path], self_pid: int) -> list[ProcFacts]:
    """Attribute live processes to `worktrees` by argv text or cwd containment."""
    names = {str(w.resolve()): w.name for w in worktrees}
    found: list[ProcFacts] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == self_pid:
            continue
        raw = _read(entry / "cmdline")
        if raw is None:
            continue
        argv = raw.replace("\x00", " ").strip()
        if not argv:
            continue  # kernel thread
        hits = {label for path, label in names.items() if path in argv}
        try:
            cwd = os.readlink(entry / "cwd")
        except OSError:
            cwd = ""
        if cwd:
            hits |= {label for path, label in names.items() if cwd.startswith(path)}
        if not hits:
            continue
        uid, pgid, sid = _proc_status(pid)
        found.append(
            ProcFacts(
                pid=pid,
                uid=uid,
                pgid=pgid,
                sid=sid,
                argv=argv,
                worktrees=sorted(hits),
                container=_container_id(pid),
            )
        )
    return sorted(found, key=lambda f: f.pid)


def refuse(reason: str) -> int:
    print(f"REFUSED  {reason}")
    print("REFUSED is not a pass: this probe could not answer its question.")
    return 3


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT,
        help="repository whose .loom/worktrees/issue-* are attributed (default: this repo)",
    )
    args = ap.parse_args(argv)

    if not sys.platform.startswith("linux"):
        return refuse(f"needs a Linux /proc; platform is {sys.platform}")
    if not (Path("/proc") / "self" / "cmdline").exists():
        return refuse("/proc/self/cmdline is absent, so no process can be attributed")

    repo_root = args.repo_root.resolve()
    worktrees = worktree_dirs(repo_root)
    print(f"repo      {repo_root}")
    print(f"uid       {os.getuid()} (the uid every Loom reaper on this host signals as)")
    print(f"worktrees {len(worktrees)}: {', '.join(w.name for w in worktrees) or '(none)'}")
    if not worktrees:
        print("verdict   OK -- no issue worktrees, so nothing can be attributed to one")
        return 0

    verdicts = classify(collect(repo_root, worktrees, os.getpid()))
    for v in verdicts:
        print(v.render())

    bad = unreachable(verdicts)
    if not bad:
        print(
            f"verdict   OK -- {len(verdicts)} attributed process(es), all signalable by uid "
            f"{os.getuid()}"
        )
        return 0

    print(
        f"verdict   UNREACHABLE -- {len(bad)} of {len(verdicts)} attributed process(es) "
        f"cannot be signalled by uid {os.getuid()} (kill(2) -> EPERM)."
    )
    print(
        "          No Loom reaper can terminate these: reap_orphaned_group's kill(-pgid) and "
        "orphan_process_reaper's SIGSTOP/SIGTERM/SIGKILL all run as this uid."
    )
    if any(v.facts.container for v in bad):
        print(
            "          At least one sits in a docker scope -- teardown needs `docker stop`/"
            "`docker kill` through the socket, not a signal. See docs/loom-orphan-reap-2026-09-27.md."
        )
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
