#!/usr/bin/env python3
"""Sealed holdout cases: the settings are committed BEFORE the render that
reads them, and reading one is a recorded act.

    tools/holdout.py list                 every seal, its state and its reads
    tools/holdout.py check                the four assertions below; 0/1/2
    tools/holdout.py seal F1D --spec x.json --by "..."   write a new seal
    tools/holdout.py open F1D --why "..." --guided-change "#100"

WHAT THIS IS FOR
----------------

`docs/scorecard/cases.csv` marks twenty cases `Holdout`, and
`docs/scorecard/README.md` states the policy they exist for: *"Holdout settings
are chosen now"*, and *"once a holdout case's detailed errors have guided a
change, it has become development data."* Until this module, both sentences were
prose and **nothing enforced either**. `tools/run_case.py`'s own `NOT_RUN` table
said it plainly: an agent that "picks the setting, freezes the clip and reads the
error in one pass has produced a development case wearing a holdout's label, and
there is no way to tell afterwards which it was."

The pattern generalised here is not invented: `tools/probes/hihat/hh_probe5.py`
has a module-level `HOLDOUT` dict -- the setting, what had already seen it, and
why it was chosen -- written above the dev-set fitting loop that never reads it.
That works inside one probe and cannot be checked from outside it. A seal file is
the same dict, in the repository, where **git** can answer the question the
docstring could only assert.

THE FOUR THINGS THAT ARE ASSERTED, AND WHY EACH ONE
---------------------------------------------------

1. **The seal is committed and clean at render time.** `git` has to have seen
   the settings before the render, or "chosen before" and "chosen after" are the
   same state on disk. An untracked or locally-modified seal is REFUSED, never
   scored -- CLAUDE.md's rule, at the point of use.

2. **The record says which seal it was measured against**, by content hash and
   by the commit that last touched the seal file. So the ordering is checkable
   **after the fact, from git**, not from anybody's word:

       git merge-base --is-ancestor <holdout.seal_commit> <provenance.worktree.commit>

   `check` runs exactly that for every read in the ledger.

3. **A read is recorded** in `docs/scorecard/holdout/LEDGER.json`, with the
   model state it was read at. A seal edited after it has been read no longer
   hashes to what the ledger says, and `check` reports it as STALE. This is the
   edge case that cannot be caught by looking at the seal alone: the settings
   were genuinely committed first, and then changed once the error was known.

4. **A second read at a DIFFERENT model state is REFUSED while the seal is
   still `sealed`.** That is the operational meaning of README's "its detailed
   errors have guided a change": between the two reads, the thing being measured
   moved. `tools/holdout.py open` records the transition -- who, when, why, and
   which change it guided -- and after it the case's records carry
   `holdout.state = "opened"` and `holdout_claim = false`, because a case that
   has informed a change is development data and a fresh independent claim needs
   a fresh holdout case.

WHAT IT DOES NOT DO, stated so nobody reads more into a green `check`
--------------------------------------------------------------------

* It cannot tell whether the setting was a GOOD choice, only that it was
  committed first and has not moved since. `why` and `seen_by` in the seal are
  the author's argument; a reviewer still has to read them.
* It cannot detect a holdout read through some other tool that never calls
  `record_read`. Every path that scores a `Holdout`-split case goes through
  `tools/run_case.py`, which does call it; a new scorer must too.
* "A different model state" is a hash over `tools/run_case.py`'s `MODEL_INPUTS`.
  A change to a file outside that set is invisible here, exactly as it is
  invisible to every other result in `docs/scorecard/results/`.
* Sealing does not make a case measurable. F1D's seal names a reference clip
  that is not in `refprofile/profile.json`; the case is a stated no-verdict
  until somebody renders it, and that is the correct answer rather than a hole.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import provenance                                                    # noqa: E402

SEAL_DIR = ROOT / "docs" / "scorecard" / "holdout"
LEDGER = SEAL_DIR / "LEDGER.json"

SCHEMA = "holdout-seal/1"
LEDGER_SCHEMA = "holdout-ledger/1"

SEALED, OPENED = "sealed", "opened"

OK, FAIL, REFUSED = 0, 1, 2

#: The fields the seal's identity is made of. Only these are hashed, so
#: recording the `opened` transition (which appends to the same file) does not
#: invalidate a hash that was written to say what was measured.
CORE_FIELDS = ("schema", "case_id", "generation", "sealed_by", "sealed_utc",
               "sealed_at_head", "settings")

#: Everything a seal must carry to be readable at all. `why` and `seen_by` are
#: required because hh_probe5's HOLDOUT dict carried them and they are the half a
#: reviewer actually reads: a setting with no argument for why it is unseen is
#: not a holdout, it is a setting.
REQUIRED_FIELDS = CORE_FIELDS + ("state", "why", "seen_by")


class Refused(Exception):
    """A precondition of the sealing apparatus failed. REFUSED is a first-class
    outcome here, distinct from pass and from fail."""


def _git(*args: str) -> str:
    return provenance.git(*args, root=ROOT)


def _now() -> str:
    return provenance.now()


def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def seal_path(case_id: str) -> pathlib.Path:
    return SEAL_DIR / f"{case_id}.json"


def core_sha256(seal: dict) -> str:
    """The seal's identity: a hash over the settings and who sealed them, never
    over the whole file. A 16-hex prefix, the same width every other hash on a
    result record uses."""
    return hashlib.sha256(
        _canon({k: seal.get(k) for k in CORE_FIELDS}).encode()).hexdigest()[:16]


def load_seal(case_id: str) -> dict:
    """The committed settings for one holdout case, or a REFUSAL naming what is
    wrong with them. Never a partially-validated seal."""
    path = seal_path(case_id)
    rel = _rel(path)
    if not path.is_file():
        raise Refused(
            f"{case_id} is a Holdout-split case with no sealed settings at {rel}. "
            f"A holdout is REFUSED rather than scored until its settings are "
            f"committed before the render that reads them -- see "
            f"docs/scorecard/README.md and tools/holdout.py. Seal it with "
            f"`tools/holdout.py seal {case_id} --spec <spec.json> --by <who>`.")
    try:
        seal = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        raise Refused(f"the seal {rel} could not be read: {e}") from None
    if not isinstance(seal, dict):
        raise Refused(f"the seal {rel} is not a JSON object")
    if seal.get("schema") != SCHEMA:
        raise Refused(f"the seal {rel} declares schema {seal.get('schema')!r}, "
                      f"and this tool only reads {SCHEMA!r}")
    missing = [f for f in REQUIRED_FIELDS if f not in seal]
    if missing:
        raise Refused(f"the seal {rel} is missing {', '.join(missing)}")
    if seal["case_id"] != case_id:
        raise Refused(f"the seal {rel} names case {seal['case_id']!r}, not {case_id!r}")
    if seal["state"] not in (SEALED, OPENED):
        raise Refused(f"the seal {rel} is in state {seal['state']!r}, which is "
                      f"neither {SEALED!r} nor {OPENED!r}")
    if not isinstance(seal.get("settings"), dict) or not seal["settings"]:
        raise Refused(f"the seal {rel} carries no settings: there is nothing sealed")
    if not isinstance(seal.get("generation"), int) or seal["generation"] < 1:
        raise Refused(f"the seal {rel} has generation {seal.get('generation')!r}; "
                      f"a generation is an integer >= 1")
    if seal["state"] == OPENED and not isinstance(seal.get("opened"), dict):
        raise Refused(f"the seal {rel} says {OPENED!r} without an `opened` block "
                      f"saying who opened it, when, why, and which change it guided")
    return seal


def _rel(p) -> str:
    """A path for a human that CANNOT raise: every use of it here is inside a
    refusal message, and a formatter that raises turns a stated refusal into a
    traceback (the same guard `tools/refprofile.py` keeps)."""
    p = pathlib.Path(p)
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def seal_git_state(case_id: str) -> dict:
    """That the seal was committed BEFORE now, established by git and not by
    the file's own say-so. REFUSES when it cannot be established."""
    path, rel = seal_path(case_id), _rel(seal_path(case_id))
    head = _git("rev-parse", "HEAD").strip()
    if not head:
        raise Refused(
            f"no git history is readable at {_rel(ROOT)}, so nothing can establish "
            f"that {rel} was committed before this render. Refusing rather than "
            f"assuming it: an unverifiable seal is not a seal.")
    tracked = bool(_git("ls-files", "--", rel).strip())
    if not tracked:
        raise Refused(
            f"the seal {rel} is not committed -- git has never seen it. The whole "
            f"value of a holdout is that its settings existed in the repository "
            f"before the render that measures them, and an untracked seal is "
            f"indistinguishable from one written after reading the error.")
    if _git("diff", "--name-only", "HEAD", "--", rel).strip():
        raise Refused(
            f"the seal {rel} has uncommitted modifications. Commit them before "
            f"rendering: otherwise the setting and the error it produces come out "
            f"of the same pass, which is the one thing a seal exists to prevent.")
    log = _git("log", "-1", "--format=%H%x09%cI", "--", rel).strip()
    if not log:
        raise Refused(f"the seal {rel} is tracked but has no commit touching it, "
                      f"so the ordering cannot be established")
    commit, _, committed = log.partition("\t")
    return {"seal": rel, "seal_commit": commit, "seal_commit_short": commit[:12],
            "seal_committed_utc": committed, "head_at_render": head[:12]}


def load_ledger() -> dict:
    if not LEDGER.is_file():
        return {"schema": LEDGER_SCHEMA, "entries": []}
    try:
        led = json.loads(LEDGER.read_text())
    except (OSError, ValueError) as e:
        raise Refused(f"the holdout ledger {_rel(LEDGER)} could not be read: {e}") from None
    if led.get("schema") != LEDGER_SCHEMA:
        raise Refused(f"the holdout ledger {_rel(LEDGER)} declares schema "
                      f"{led.get('schema')!r}, and this tool only reads {LEDGER_SCHEMA!r}")
    led.setdefault("entries", [])
    return led


LEDGER_NOTE = ("Append-only. One entry per act that a holdout policy depends on: "
               "a READ (a number was taken off a sealed case) or an OPENED "
               "transition (the case has informed a change and is development "
               "data from here on). Written by tools/holdout.py; read by "
               "`tools/holdout.py check`, which is what catches a seal edited "
               "after it was read.")


def _append(entry: dict) -> dict:
    led = load_ledger()
    entry = {"n": len(led["entries"]) + 1, **entry}
    led["schema"] = LEDGER_SCHEMA
    led["note"] = LEDGER_NOTE
    led["entries"].append(entry)
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(led, indent=1, sort_keys=False) + "\n")
    return entry


def reads_of(case_id: str, generation: int | None = None) -> list[dict]:
    entries = [e for e in load_ledger()["entries"]
               if e.get("kind") == "read" and e.get("case_id") == case_id]
    if generation is not None:
        entries = [e for e in entries if e.get("generation") == generation]
    return entries


def model_state_sha256(inputs: dict) -> str:
    """One hash for "the state of the thing being measured", from the same input
    hashes the result record already carries. Two reads with the same value were
    taken off the same model; two with different values were not, and that is
    what turns a holdout into development data."""
    return hashlib.sha256(_canon(inputs).encode()).hexdigest()[:16]


HOW_TO_CHECK = ("git merge-base --is-ancestor <holdout.seal_commit> "
                "<provenance.worktree.commit> -- the seal was committed before "
                "the render, or this record is not a holdout reading")


def assert_readable(case_id: str, *, model_state: str) -> dict:
    """The block that goes on the record, or a REFUSAL. Called before a Holdout
    case is rendered, so a case with no valid seal costs nothing and produces no
    number."""
    seal = load_seal(case_id)
    git_state = seal_git_state(case_id)
    prior = reads_of(case_id, generation=seal["generation"])
    if seal["state"] == SEALED and prior:
        moved = [e for e in prior if e.get("model_state_sha256") != model_state]
        if moved:
            first = moved[0]
            raise Refused(
                f"{case_id} was already read at a DIFFERENT model state "
                f"({first.get('model_state_sha256')} on {first.get('utc')}, ledger "
                f"entry {first.get('n')}); this render is at {model_state}. Between "
                f"those two reads the thing being measured moved, which is what "
                f"docs/scorecard/README.md means by \"its detailed errors have "
                f"guided a change ... it has become development data\". Record the "
                f"transition first: `tools/holdout.py open {case_id} --why <why> "
                f"--guided-change <ref>`. After that this case still runs, and its "
                f"records say so -- what it cannot do is go on claiming to be a "
                f"holdout.")
    block = {
        "state": seal["state"],
        "holdout_claim": seal["state"] == SEALED,
        "generation": seal["generation"],
        "seal_core_sha256": core_sha256(seal),
        "sealed_by": seal["sealed_by"],
        "sealed_utc": seal["sealed_utc"],
        "sealed_at_head": seal["sealed_at_head"],
        "why": seal["why"],
        "seen_by": seal["seen_by"],
        "settings": seal["settings"],
        "reads_before_this_one": len(prior),
        "model_state_sha256": model_state,
        "how_to_check": HOW_TO_CHECK,
        **git_state,
    }
    if seal["state"] == OPENED:
        block["opened"] = seal["opened"]
        block["note"] = ("this case has already informed a change, so this reading is "
                         "development data. A fresh independent claim needs a fresh "
                         "holdout case (docs/scorecard/README.md).")
    return block


def was_read(res: dict) -> bool:
    """Whether a number was actually taken. A refusal reads nothing -- it
    carries no distance at all -- so it is not a read and must not consume the
    case's one sealed reading."""
    return any(bool(m.get("valid")) for m in (res.get("metrics") or {}).values()
               if isinstance(m, dict))


def record_read(case_id: str, block: dict, *, engine: str, state: str,
                worst: float | None, result_path: str, command: str) -> dict:
    """Append the read to the ledger. The model state comes off the block that
    was asserted before the render, so the entry cannot describe a different
    model from the one the record does."""
    wt = provenance.worktree_state(root=ROOT)
    return _append({
        "kind": "read", "case_id": case_id, "utc": _now(),
        "generation": block["generation"],
        "seal_core_sha256": block["seal_core_sha256"],
        "seal_commit": block["seal_commit"],
        "seal_state_at_read": block["state"],
        "holdout_claim": block["holdout_claim"],
        "model_state_sha256": block["model_state_sha256"],
        "render_commit": wt["commit"], "render_dirty": wt["dirty"],
        "render_uncommitted_sha256": wt["uncommitted_sha256"],
        "engine": engine, "board_state": state,
        "worst": None if worst is None else round(float(worst), 4),
        "result": result_path, "command": command,
    })


def open_case(case_id: str, *, why: str, guided_change: str, by: str) -> dict:
    """Record the transition the README describes in prose: this holdout's
    errors have guided a change, so it is development data from here on."""
    seal = load_seal(case_id)
    if seal["state"] == OPENED:
        raise Refused(f"{case_id} is already open: {seal['opened']}")
    prior = reads_of(case_id, generation=seal["generation"])
    if not prior:
        raise Refused(
            f"nothing has read {case_id} yet (no ledger entry for generation "
            f"{seal['generation']}), so there is no transition to record. A seal "
            f"that has never been read is still a holdout.")
    if not why.strip() or not guided_change.strip():
        raise Refused("opening a holdout requires --why and --guided-change: "
                      "an unexplained transition is the prose policy again")
    opened = {"utc": _now(), "by": by, "why": why, "guided_change": guided_change,
              "after_reads": [e["n"] for e in prior],
              "note": ("from here on, records for this case carry holdout_claim "
                       "false. A fresh independent claim needs a fresh holdout "
                       "case, not a re-read of this one.")}
    seal["state"] = OPENED
    seal["opened"] = opened
    seal_path(case_id).write_text(json.dumps(seal, indent=1, sort_keys=False) + "\n")
    _append({"kind": "opened", "case_id": case_id, "utc": opened["utc"],
             "generation": seal["generation"],
             "seal_core_sha256": core_sha256(seal),
             "by": by, "why": why, "guided_change": guided_change,
             "after_reads": opened["after_reads"]})
    return seal


def write_seal(case_id: str, spec: dict, *, by: str, generation: int = 1,
               resealed_from: str | None = None) -> dict:
    """Write a new seal. `sealed_at_head` is stamped from git here rather than
    typed in: it is the commit the settings were chosen on top of, and it is the
    anchor `check` verifies the seal's own commit against."""
    head = _git("rev-parse", "HEAD").strip()
    if not head:
        raise Refused(f"no git history at {_rel(ROOT)}: a seal needs a commit to "
                      f"be sealed on top of")
    for field in ("settings", "why", "seen_by"):
        if not spec.get(field):
            raise Refused(f"the spec for {case_id} carries no {field!r}; a setting "
                          f"with no argument for why it is unseen is not a holdout")
    seal = {"schema": SCHEMA, "case_id": case_id, "generation": generation,
            "state": SEALED, "sealed_by": by, "sealed_utc": _now(),
            "sealed_at_head": head[:12],
            "why": spec["why"], "seen_by": spec["seen_by"],
            "settings": spec["settings"]}
    for extra in ("chosen_from", "not_chosen_by", "reference_render_request", "note"):
        if spec.get(extra):
            seal[extra] = spec[extra]
    if resealed_from:
        seal["resealed_from"] = resealed_from
    SEAL_DIR.mkdir(parents=True, exist_ok=True)
    seal_path(case_id).write_text(json.dumps(seal, indent=1, sort_keys=False) + "\n")
    return seal


# ===========================================================================
# check: the assertions, run against the tree as it stands
# ===========================================================================
def _ancestry(a: str, b: str) -> bool | None:
    """Whether commit `a` is an ancestor of (or is) commit `b`. **None when it
    cannot be established in this clone**, which is the common case rather than
    an alarm: this repository squash-merges, so a render commit made on a
    feature branch does not exist on `main` at all. An unverifiable ordering is
    reported as a note and does NOT fail the gate -- an unsatisfiable gate is
    worse than no gate (CLAUDE.md), and the seal-hash check below is the one
    that is always answerable."""
    if not a or not b:
        return None
    if (_git("cat-file", "-t", a).strip() != "commit"
            or _git("cat-file", "-t", b).strip() != "commit"):
        return None
    base = _git("merge-base", a, b).strip()
    full_a = _git("rev-parse", a).strip()
    if not base or not full_a:
        return None
    return base == full_a


def check() -> tuple[int, list[str]]:
    """Exit 0 when every seal and every recorded read hold up, 1 when one does
    not, 2 when something cannot be established at all. Same convention as
    `tools/refprofile.py --verify` and the repository's verifiers."""
    lines: list[str] = []
    worst = OK
    try:
        led = load_ledger()
    except Refused as why:
        return REFUSED, [f"REFUSED  {why}"]

    seals: dict[str, dict] = {}
    for path in sorted(SEAL_DIR.glob("*.json")):
        if path.name == LEDGER.name:
            continue
        cid = path.stem
        try:
            seal = load_seal(cid)
            git_state = seal_git_state(cid)
        except Refused as why:
            worst = max(worst, FAIL)
            lines.append(f"FAIL     {cid}\n         {why}")
            continue
        seals[cid] = seal
        lines.append(f"OK       {cid} {seal['state']} gen {seal['generation']} "
                     f"core {core_sha256(seal)} sealed at {seal['sealed_at_head']} "
                     f"committed {git_state['seal_commit_short']}")
        order = _ancestry(seal["sealed_at_head"], git_state["seal_commit"])
        if order is False:
            worst = max(worst, FAIL)
            lines.append(f"FAIL     {cid}: sealed_at_head {seal['sealed_at_head']} is "
                         f"not an ancestor of the commit that added the seal "
                         f"({git_state['seal_commit_short']})")
        elif order is None:
            lines.append(f"note     {cid}: sealed_at_head {seal['sealed_at_head']} is "
                         f"not in this clone, so that half of the ordering cannot be "
                         f"checked here (squash merges do this)")
        if seal["state"] == OPENED:
            op = seal.get("opened") or {}
            if not op.get("why") or not op.get("guided_change"):
                worst = max(worst, FAIL)
                lines.append(f"FAIL     {cid}: opened without a why and a "
                             f"guided-change reference")

    for e in led["entries"]:
        cid = e.get("case_id")
        if e.get("kind") != "read":
            continue
        seal = seals.get(cid)
        if seal is None:
            worst = max(worst, FAIL)
            lines.append(f"FAIL     ledger entry {e.get('n')}: a read of {cid} with "
                         f"no readable seal for it")
            continue
        if e.get("generation") == seal["generation"] and \
                e.get("seal_core_sha256") != core_sha256(seal):
            worst = max(worst, FAIL)
            lines.append(
                f"STALE    ledger entry {e.get('n')}: {cid} was read against seal core "
                f"{e.get('seal_core_sha256')} and the committed seal now hashes to "
                f"{core_sha256(seal)}. The settings were changed AFTER they were read, "
                f"which is the failure a seal exists to make visible. Either restore "
                f"them, or reseal with a new generation "
                f"(`tools/holdout.py seal {cid} ... --reseal`).")
        order = _ancestry(e.get("seal_commit"), e.get("render_commit"))
        if order is False:
            worst = max(worst, FAIL)
            lines.append(
                f"FAIL     ledger entry {e.get('n')}: {cid}'s seal commit "
                f"{str(e.get('seal_commit'))[:12]} is NOT an ancestor of the render "
                f"commit {e.get('render_commit')}. The settings were not in the "
                f"repository when they were measured.")
        elif order is None:
            lines.append(f"note     ledger entry {e.get('n')}: {cid}'s seal or render "
                         f"commit is not in this clone, so the ordering cannot be "
                         f"re-checked here (squash merges do this); the seal hash "
                         f"above is what still binds the read to the settings")

    for cid, seal in sorted(seals.items()):
        if seal["state"] != SEALED:
            continue
        states = {e.get("model_state_sha256") for e in reads_of(cid, seal["generation"])}
        if len(states) > 1:
            worst = max(worst, FAIL)
            lines.append(
                f"FAIL     {cid} is still marked {SEALED!r} and has been read at "
                f"{len(states)} different model states. Reading a holdout again after "
                f"the model moved is what makes it development data; the transition "
                f"has to be recorded (`tools/holdout.py open {cid}`).")

    reads = [e for e in led["entries"] if e.get("kind") == "read"]
    opened = [e for e in led["entries"] if e.get("kind") == "opened"]
    lines.append(f"{len(seals)} seal(s), {len(reads)} read(s), "
                 f"{len(opened)} recorded transition(s)")
    if not seals and worst == OK:
        lines.append("no seal is committed yet: every Holdout-split case is REFUSED "
                     "by tools/run_case.py, which is the correct answer and not a "
                     "hole in the instrument")
    return worst, lines


# ===========================================================================
# CLI
# ===========================================================================
def cmd_list() -> int:
    led = load_ledger()
    print(f"{'case':<7}{'state':<9}{'gen':>4}  {'reads':>5}  seal core        sealed by")
    print("-" * 96)
    any_seal = False
    for path in sorted(SEAL_DIR.glob("*.json")):
        if path.name == LEDGER.name:
            continue
        any_seal = True
        cid = path.stem
        try:
            seal = load_seal(cid)
        except Refused as why:
            print(f"{cid:<7}REFUSED  {why}")
            continue
        n = len([e for e in led["entries"]
                 if e.get("kind") == "read" and e.get("case_id") == cid])
        print(f"{cid:<7}{seal['state']:<9}{seal['generation']:>4}  {n:>5}  "
              f"{core_sha256(seal)}  {seal['sealed_by'][:40]}")
    if not any_seal:
        print("(no seals)")
    print("-" * 96)
    for e in led["entries"]:
        print(f"  {e['n']:>3} {e.get('kind',''):<7} {e.get('case_id',''):<6} "
              f"{e.get('utc','')}  model {e.get('model_state_sha256','-')}  "
              f"{e.get('board_state', e.get('guided_change',''))}")
    return OK


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("list", help="every seal, its state and its reads")
    sub.add_parser("check", help="the four assertions; 0 ok, 1 failed, 2 unverifiable")
    s = sub.add_parser("seal", help="write a seal from a spec JSON")
    s.add_argument("case_id")
    s.add_argument("--spec", required=True,
                   help="JSON with settings / why / seen_by (and optional "
                        "chosen_from, not_chosen_by, reference_render_request, note)")
    s.add_argument("--by", required=True, help="who chose these settings")
    s.add_argument("--reseal", action="store_true",
                   help="bump the generation of an existing seal; requires --why")
    s.add_argument("--why", default="", help="why the seal is being replaced")
    o = sub.add_parser("open", help="record that this holdout has become development data")
    o.add_argument("case_id")
    o.add_argument("--why", required=True)
    o.add_argument("--guided-change", required=True, dest="guided_change")
    o.add_argument("--by", required=True)
    a = ap.parse_args(argv)

    try:
        if a.cmd in (None, "list"):
            return cmd_list()
        if a.cmd == "check":
            code, lines = check()
            print("\n".join(lines))
            return code
        if a.cmd == "seal":
            spec = json.loads(pathlib.Path(a.spec).read_text())
            generation, resealed_from = 1, None
            if seal_path(a.case_id).is_file():
                if not a.reseal:
                    raise Refused(f"{_rel(seal_path(a.case_id))} already exists; "
                                  f"--reseal bumps its generation on purpose")
                if not a.why.strip():
                    raise Refused("--reseal requires --why: replacing a seal is the "
                                  "act this tool exists to make visible")
                old = load_seal(a.case_id)
                generation = old["generation"] + 1
                resealed_from = (f"generation {old['generation']} core "
                                 f"{core_sha256(old)}: {a.why}")
            seal = write_seal(a.case_id, spec, by=a.by, generation=generation,
                              resealed_from=resealed_from)
            print(f"sealed {a.case_id} generation {seal['generation']} core "
                  f"{core_sha256(seal)} on top of {seal['sealed_at_head']}")
            print(f"COMMIT {_rel(seal_path(a.case_id))} BEFORE RENDERING IT: an "
                  f"uncommitted seal is REFUSED, on purpose.")
            return OK
        if a.cmd == "open":
            seal = open_case(a.case_id, why=a.why, guided_change=a.guided_change,
                             by=a.by)
            print(f"{a.case_id} is now {seal['state']}: {seal['opened']}")
            print(f"COMMIT {_rel(seal_path(a.case_id))} and {_rel(LEDGER)}.")
            return OK
    except Refused as why:
        print(f"REFUSED: {why}", file=sys.stderr)
        return REFUSED
    ap.error("unknown command")
    return REFUSED


if __name__ == "__main__":
    sys.exit(main())
