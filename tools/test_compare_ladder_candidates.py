"""#1975: `--only` without `shipped` REFUSES before anything is measured.

`compare(names=...)` builds `rep["candidates"]` only for the names it was
given, and `_verdict` then reads `rep["candidates"]["shipped"]` to score every
other candidate against it. So `--only null` ran `assert_apparatus`, the seven
stages and the controls, and died in `_verdict` with `KeyError: 'shipped'`:
exit 1 and a traceback, after the measurement. The refusal now sits in
`compare()` immediately after `names` is resolved, so a library caller gets
the same `Refused` the CLI does (`main` maps it to exit 2) and nothing runs
first.

HOW THE CONTROLS WORK. Each stubbed refusal control replaces
`lc.assert_apparatus` and every `stage_*` function with a stub that records
its name and raises `StageRan` -- the right ports and no behaviour
(`docs/verification-rules.md` rule 1) -- and asserts exit 2, the `REFUSED:`
line and an empty record. RED ON THE UNFIXED TREE (origin/main @ dd0449e0,
2026-11-28), all nine with `StageRan: assert_apparatus ran: compare() did not
refuse first`: the apparatus check was reached. That is the first half of the
defect, and all a stub can show honestly, because the second needs every
stage to return a real report. So the second half is covered twice.
`test_the_real_command_refuses_before_measuring` drives the command with no
stubs and was red with exit 1 and the `KeyError: 'shipped'` traceback from
`_verdict`; `test_the_verdict_needs_the_shipped_baseline` pins it where it
lives. The command runs under a deadline because a tool that starts
measuring instead of refusing must be reported as exactly that.

GREEN ON BOTH TREES, BY DESIGN, and not counted as controls: the invariance
pins. The accepted lists reach the apparatus check, and the first stage
receives `names` exactly as given -- nothing added, reordered or de-duplicated
-- which is what "`--only shipped` and the default list are unchanged" means
at the seam. What the real runs report is a before/after JSON diff for both
accepted lists, minutes a side, recorded in the PR rather than here.

RULE 8, ASKED AT WRITE TIME -- what satisfies `"shipped" in names` while
violating its intent? Nothing reachable. `compare` writes `rep["candidates"][n]`
for every `n in names` before it calls `_verdict`, so the check and the
precondition it guards are the same proposition. The inputs that come closest
are refused rather than crashed, and two are pinned below: `--only` directly
followed by another option (argparse yields an empty list) and a bare `str`
from a library caller (`list("shipped")` is seven letters). Not an adversary:
an object equal to `"shipped"` under `==` but hashed differently would pass the
check and miss the dict. The CLI cannot build one; it only produces `str`.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compare_ladder_candidates as cc  # noqa: E402

#: every function `compare()` calls once `names` is resolved, derived from the
#: module so that a stage added later is tripwired too. `_tripwire` asserts the
#: derivation still finds the ones that exist today, so it cannot go empty and
#: pass every "nothing ran" assertion vacuously.
STAGES = tuple(sorted(n for n in vars(cc) if n.startswith("stage_")))
KNOWN_STAGES = (
    "stage_bass",
    "stage_cleanliness",
    "stage_controls",
    "stage_cost",
    "stage_drive",
    "stage_linear",
    "stage_movement",
    "stage_resonance",
)

#: the unfixed tool measured for the better part of a minute before it
#: crashed; the refusal takes well under a second. Well above an import, well
#: below a measurement.
COMMAND_DEADLINE_S = 120


class StageRan(Exception):
    """`compare()` reached a stage it must not have reached.

    Deliberately not an `AssertionError` or a `Refused`: nothing in the tool
    catches this class, so it cannot be swallowed or relabelled on the way out
    and read as a refusal.
    """


def _tripwire(monkeypatch) -> list:
    """Replace the apparatus check and every stage; return the record."""
    assert set(KNOWN_STAGES) <= set(STAGES), (STAGES, KNOWN_STAGES)
    ran = []

    def stub(name):
        def _stub(*args, **kwargs):
            ran.append(name)
            raise StageRan(f"{name} ran: compare() did not refuse first")

        return _stub

    monkeypatch.setattr(cc.lc, "assert_apparatus", stub("assert_apparatus"))
    for name in STAGES:
        monkeypatch.setattr(cc, name, stub(name))
    return ran


# --- the controls: red on the unfixed tree ----------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["--quick", "--only", "null"],
        ["--quick", "--only"],
        ["--only", "--quick"],
        ["--quick", "--only", "zdf-explicit", "zdf-newton-2"],
    ],
)
def test_only_without_shipped_refuses_before_anything_runs(monkeypatch, capsys, argv):
    """Exit 2, `REFUSED:` on stderr naming `shipped` and why, nothing on
    stdout, and an empty record: not the apparatus check, not a stage."""
    ran = _tripwire(monkeypatch)
    rc = cc.main(argv)
    out, err = capsys.readouterr()
    assert rc == 2, f"exit {rc}; stderr: {err!r}"
    assert err.startswith("REFUSED:"), err
    assert "shipped" in err, err
    assert "difference" in err, err
    assert out == "", out
    assert ran == [], f"ran before refusing: {ran}"


@pytest.mark.parametrize(
    "names",
    [
        ["null"],
        [],
        ("zdf-explicit",),
        ["Shipped"],
        "shipped",
    ],
)
def test_compare_refuses_a_library_caller_too(monkeypatch, names):
    """The check lives in `compare()`, so `main()` is not the only caller
    that gets a `Refused` instead of a measurement followed by a crash."""
    ran = _tripwire(monkeypatch)
    with pytest.raises(cc.Refused, match="shipped"):
        cc.compare(quick=True, names=names)
    assert ran == [], f"ran before refusing: {ran}"


def test_the_real_command_refuses_before_measuring():
    """The thing that ships is the command, so the command is driven once with
    no stubs. Bounded: a tool that starts measuring instead of refusing is
    killed at the deadline and reported as that, not left to run on."""
    try:
        r = subprocess.run(
            [sys.executable, cc.__file__, "--quick", "--only", "null"],
            capture_output=True,
            text=True,
            timeout=COMMAND_DEADLINE_S,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(
            f"still running after {COMMAND_DEADLINE_S} s: the tool "
            f"started measuring instead of refusing "
            f"(stdout so far: {exc.stdout!r})"
        )
    assert r.returncode == 2, (r.returncode, r.stdout[-2000:], r.stderr[-2000:])
    assert r.stderr.startswith("REFUSED:"), r.stderr
    assert "shipped" in r.stderr, r.stderr
    assert r.stdout == "", r.stdout


# --- the premise the guard rests on: green on both trees ---------------------


def test_the_verdict_needs_the_shipped_baseline():
    """Why `shipped` is required: `_verdict` reads it before it looks at any
    other candidate. Should this stop holding -- `_verdict` learning to do
    without a baseline -- the guard in `compare()` is no longer justified and
    must be revisited with it, not left refusing on a stale reason."""
    with pytest.raises(KeyError, match="shipped"):
        cc._verdict({"candidates": {"null": {}}}, ["null"])


# --- invariance pins: green on both trees, by design -------------------------


@pytest.mark.parametrize(
    "argv, want_names",
    [
        (["--quick", "--only", "shipped"], ["shipped"]),
        (["--quick"], list(cc.lc.SHIPPABLE)),
        (["--quick", "--only", "null", "shipped"], ["null", "shipped"]),
        (["--quick", "--only", "shipped", "shipped"], ["shipped", "shipped"]),
    ],
)
def test_accepted_lists_reach_the_apparatus_unchanged(monkeypatch, argv, want_names):
    """`--only shipped` and the default list are what they were. The guard
    stays quiet, and the first stage receives `names` exactly as given: not
    de-duplicated, reordered, or handed `shipped` behind the caller's back,
    which is the alternative the issue rejected. The default row is also the
    gate's satisfiability: were `lc.SHIPPABLE` ever to lose `shipped`, this
    fails with the refusal rather than letting an unsatisfiable gate ship."""
    seen = {}

    def apparatus(quick=False):
        seen["quick"] = quick
        return {}

    def linear(names, *args, **kwargs):
        seen["names"] = list(names)
        raise StageRan("stage_linear ran: far enough")

    monkeypatch.setattr(cc.lc, "assert_apparatus", apparatus)
    monkeypatch.setattr(cc, "stage_linear", linear)
    with pytest.raises(StageRan):
        cc.main(argv)
    assert seen == {"quick": True, "names": want_names}
