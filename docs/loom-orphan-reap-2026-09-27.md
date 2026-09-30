# A sweep orphan nothing could kill: uid, not detection

Investigated **2026-09-27**, on `ip-172-31-74-176` (a shared 8-vCPU Loom
dispatch worker), against `loom-daemon 0.19.434` (commit `17b3556`, built
2026-09-27T00:12:21Z) and the `rjwalters/loom` checkout at `/home/ubuntu/GitHub/loom`
(`7259b24cb`, version `0.19.426`).

Filed upstream as **[rjwalters/loom#9160](https://github.com/rjwalters/loom/issues/9160)**.
Local issue: gf180-parasynth#310. Incident context: gf180-parasynth#33, PR #312.

This is a `rjwalters/loom` finding recorded here because this is where the
incident happened, in the shape `docs/tool-findings.md` established: exact
versions, exact error text, and the part that was **wrong before it was right**
kept rather than tidied away.

---

## What the incident report said, and why it was wrong

gf180-parasynth#310 was filed from a Builder session that found, by chance, an
`openroad … drt.tcl` at ~440 % CPU with no live session behind it. It concluded:

> **Nothing reaps it.** … **Nothing surfaces it.** The next Builder discovered it
> only because it happened to `ps` while orienting.

Both halves are false, and the curated root-cause hypothesis that followed from
them — "the orphaning sweep was probably a manual, non-registry-tracked
invocation, so `reap_orphaned_group` never ran" — is also false. This is the
repository's own failure mode from `docs/failure-modes.md`: a conclusion drawn
from the cheap observation (`ps`) rather than the expensive one (the daemon's
own log, which was one `grep` away and said something different).

**What actually happened, from `~/.loom/daemon.log`:**

| 2026-09-26 | event |
|---|---|
| 21:56:44 | `loom-daemon` dispatches `sweep-issue-33-1790459799` (`.loom/logs/sweep-issue-33.log` header; `LOOM_DISPATCH_MODE mode=bare-metal`) |
| 22:13:38 | the Builder's `pnr/shuttle/run_librelane.py full` starts container `d85f2b66bfe6` via `docker run --rm` |
| 23:22:04 | `Claude CLI exited with code 143` — the sweep is SIGTERMed |
| 23:35:32 | `orphan_process_reaper` attributes **6** processes to `.loom/worktrees/issue-33` (`protected live-sweep pids=[]`) |
| 23:35:37 | `reaped frozen=4 … killed=[1403608, 1982706, 1403517] survivors=[1403608, 1982706]` |
| 23:35:37 | `[1403608, 1982706] survived SIGKILL — they may be in uninterruptible I/O or owned by another user` |
| 23:50:46 | same pass, same two survivors |
| 00:05:50 | same pass, same two survivors — then no further report on this orphan |
| 01:07 (2026-09-27) | the container is still `Up 3 hours`; host load average 11.02 on 8 cores |

So detection worked. Attribution worked — `orphan_process_reaper`
(rjwalters/loom#5110) keys on **argv referencing the worktree**, and the
container's argv carries the host worktree path even though its cwd is inside
the container. Loom even wrote a WARN naming the right suspicion.

**The kill was refused by the kernel.**

```
$ ps -o pid,user,uid -p 1403608,1982706
    PID USER       UID
1403608 root         0
1982706 root         0
$ python3 -c "import os; os.kill(1403608, 0)"
PermissionError: [Errno 1] Operation not permitted
```

`docker run` hands the work to dockerd, which runs as root and started the
container's processes as root. Every Loom reaper signals as the unprivileged
daemon uid (1000 here). `kill(2)` returns **EPERM**. `reap_orphaned_group`'s
`kill(-pgid, …)`, and `orphan_process_reaper`'s freeze-first
SIGSTOP → SIGTERM → SIGKILL, are all the same uid and all equally powerless.

Three further mechanisms compound it, each independently verified:

- **The process group could never have reached it anyway.** `pgid` = `sid` =
  `1403608`, parent `containerd-shim-runc-v2` (itself PPID 1). Nothing links it
  to the sweep leader's process group.
- **The cgroup CPU budget does not contain it.** The sweep ran under
  `…/loom.slice/loom-agents.slice/loom-agent-<pid>-<hash>.scope` with
  `CPUQuota=100%` (one core, `LOOM_SWEEP_CPU_BUDGET_CORES=1`, rjwalters/loom#5111).
  The container sits in `/system.slice/docker-d85f2b66bfe6….scope` — dockerd's
  cgroup, not the sweep's. That is the mechanism behind #310's observation that
  "a per-sweep budget that only constrains the living session does not constrain
  the fleet": the budget is a cgroup, and `docker run` leaves the cgroup.
- **Killing the `docker run` client made it worse.** Pid 1403517 —
  `docker run --rm --platform linux/amd64 -v …issue-33:…` — *was* signalable and
  *was* killed at 23:35:37. That removed the only host-side handle on the
  container while `HostConfig.AutoRemove=true` left the container itself running.
  The one mechanism that would have worked is
  `loom-daemon/src/sweep_registry/reaper/container_stop.rs` (`docker stop` /
  `docker kill` through the socket, which is uid-blind) — and it is wired only
  into the explicit-cancel path (`begin_cancel` / `finish_cancel`,
  `reaper.rs:836`), not into the crash path or `orphan_process_reaper`; and its
  discovery filters on `label=loom.sweep.issue=<N>` + `label=loom.dispatch=container`,
  which an agent-launched container does not carry (`docker inspect … .Config.Labels`
  = `{}`).

**Not a deployment gap.** rjwalters/loom#8435/#8446 (`container_stop`) and
#8791 (its lock hardening) were both already in the binary at incident time
(`0.19.426`, the version the sweep log names). No daemon upgrade fixes this.

## The narrow statement of the defect

> Every Loom orphan-teardown path is a signal sent as the daemon's own uid, so it
> cannot terminate a process dockerd started as root. The one uid-blind teardown
> Loom owns — `container_stop` — is reachable only from an explicit cancel of a
> *Loom-labelled* container. An agent-launched `docker run` therefore produces an
> orphan that is **detected, logged at WARN, attacked three times, and unkillable**,
> and the reaper reports "survived SIGKILL" into a log no dispatch decision reads.

Two surfacing failures sit on top of it, and they are why this cost 75 minutes of
routing and three hours of a shared host:

- `loom-daemon status` renders in-flight sweeps and a `CTR` column, and
  `loom-daemon inflight` is a *different* thing entirely (a verification-command
  fingerprint registry, rjwalters/loom#8268 — `inflight list` said
  `no commands in flight` throughout). Neither shows a reaper survivor. #310's
  acceptance criterion "surfaced in `loom-daemon inflight`" names the wrong
  surface; `status` / `health` is the right one.
- `loom-daemon status` printed `host cpu (observed, **not a cap term since
  #4512**): 8 logical cores, 0% idle measured (≈8.0 cores consumed), 1m loadavg
  11.10` while dispatching **8** concurrent sweeps. The daemon measured the
  saturation, attributed it to nobody, and let it bind nothing.

## Disposition: surface, do not kill

#310 argues that reaping a found orphan is not obviously right, because in this
incident the orphan *was the work the issue needed*. That is correct here and it
generalises badly, so state the rule rather than the instance:

**An UNREACHABLE verdict must be surfaced to the dispatcher and must not be
silently retried.** A signal that returned EPERM will return EPERM on the next
tick; three identical failed passes produced three identical WARNs and no
escalation. The two useful responses are (a) escalate to the teardown that *can*
work (`docker stop`/`docker kill` by container id read from `/proc/<pid>/cgroup`,
no label required) when the operator wants the compute back, or (b) publish the
orphan against its issue so the next dispatch for that issue can adopt the run
instead of duplicating it. Killing by default would have discarded a routing run
that was ~75 minutes in; ignoring by default is what actually happened. Neither
default is acceptable, which is why the verdict has to reach a decision-maker.

This repository's local half of that is `tools/probe_orphan_reap.py`: it asks the
one question nothing was asking, on demand, and exits 1 when the answer is
UNREACHABLE.

<!-- claim: test=tools/test_probe_orphan_reap.py::test_eperm_process_is_reported_unreachable -->

```
$ python3 tools/probe_orphan_reap.py
UNREACHABLE  pid=1403608  uid=0  pgid=1403608  sid=1403608  container=d85f2b66bfe6  worktree=issue-33  argv=…librelane…
UNREACHABLE  pid=1982706  uid=0  pgid=1403608  sid=1403608  container=d85f2b66bfe6  worktree=issue-33  argv=…openroad -exit … drt.tcl
verdict   UNREACHABLE -- 2 of 6 attributed process(es) cannot be signalled by uid 1000 (kill(2) -> EPERM).
```

## Looked at and not filed

- **`.loom/sweep-checkpoint/issue-33.json` is not incident evidence.** #310 cites
  it as the artefact showing the sweep died at `curator-done`. The file is keyed
  by **issue**, not by sweep, and every re-dispatch overwrites it: by the time
  this pass read it, it carried `task_id sweep-20260927T005904Z-3166529-3bc05e4f`
  and a 01:00:06 timestamp — a later sweep entirely. Per-sweep incident state
  does not survive the next dispatch for the same issue. Not filed as a separate
  upstream issue because the daemon's own `sweep-outcomes.jsonl` and
  `sweep-issue-<N>.log` *are* append-only and did retain everything needed; the
  lesson is for readers of checkpoints, and it is recorded here.
- **`orphan_process_reaper` stopped reporting after 00:05:50** even though
  `worktree_reaper` ticks continued (00:59 in the log) and the orphan was still
  alive. Plausibly the pid-scoped live-sweep gate (rjwalters/loom#5135) once #33
  was re-dispatched, plausibly the 15-minute tick cadence interacting with
  re-dispatch. **Not filed:** distinguishing those needs a debug-level log this
  build does not emit, and the upstream issue already asks for the survivor state
  to be published rather than recomputed per tick, which subsumes it.
- **`reap_orphaned_group` returns silently when the group has no members**
  (`reaper.rs`: `if !group_has_members(pgid) { return false; }`, no log). "I
  looked and there was nothing in the group" is indistinguishable from "I never
  ran". Folded into the upstream issue as a one-line observability ask rather than
  filed separately.
- **`pnr/shuttle/run_librelane.py` stamps no labels on the container it starts.**
  Stamping `loom.sweep.issue=<N>` would let today's `container_stop` find it —
  but only on the explicit-cancel path, so it does not close the crash path, and
  it would make this repository depend on an undocumented upstream label
  contract. Recorded as a candidate mitigation, deliberately not taken here;
  the upstream issue asks for label-free discovery from `/proc/<pid>/cgroup`
  instead.

## What this pass changes about how findings get recorded

The incident report was written from `ps` and was wrong in its central claim. One
`grep orphan_process_reaper ~/.loom/daemon.log` would have inverted it. So:
**before asserting that an orchestration layer did nothing, grep the layer's own
log.** "Nothing reaps it" is a claim about a program's behaviour, and that program
keeps a record.
