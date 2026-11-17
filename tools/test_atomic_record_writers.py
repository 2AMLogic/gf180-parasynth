"""Injected-kill and truncated-reader controls for the tracked-JSON record
writers migrated to `run_all.write_json_atomic` (#882; class sweep remainder
of #881).

A tracked JSON written truncate-then-write is the #842 shape on an experiment
record: a kill landing inside the write leaves the file short, and a later
reader -- a gate, a scorecard, the tool's own next run -- strands on it until
a human restores the file. Every writer migrated below now replaces through
`run_all.write_json_atomic`; these controls pin both halves:

- the write half: BOTH kill hooks from the #881 ledger control
  (test_manifest.test_a_kill_during_the_ledger_write_leaves_the_previous_ledger_intact)
  -- a truncate-then-half-write-then-die on `Path.write_text` (the old shape:
  pre-migration this hook fired and corrupted the record, so the control is
  red until the migration lands) and a die-after-tmp-before-replace on
  `os.replace` (the new shape's residual risk). Post-migration the previous
  record survives both and no tmp sibling is left behind.
- the read half: a truncated, empty or non-UTF-8 record must REFUSE naming
  its path -- never a bare JSONDecodeError that reads as a bug in the reader.

The writer controls call each module's `write_json` seam directly: the same
function the tool's main() calls at its (formerly inline) write site, with no
render or reference corpus behind it, so a kill is simulated per writer
without running any of the pipelines the records describe.
"""
from __future__ import annotations

import importlib
import inspect
import json
import os
import pathlib
import re
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(HERE))


#: (module, writer attribute) -- one entry per migrated write site; refprofile
#: writes profile and notes through the same `write_json` seam, so one entry
#: covers both calls.
WRITERS = [
    ("condition_boundary", "write_json"),
    ("regen_m1a_units", "write_json"),
    ("refprofile", "write_json"),
    ("qualify_m1a_attack", "write_json"),
    ("measure_m1a_drive", "write_json"),
    ("phase_aware_m1a", "write_json"),
    ("analyse_m1a_phase_cycle", "write_json"),
    ("measure_m5a_attack_bias", "write_json"),
    ("measure_m5a_pulse_duty", "write_json"),
    ("clap_d12a_probe", "write_json"),
    ("clap_burst_timing_qual", "write_json"),
    ("clap_final_strike_experiment", "write_json"),
]


@pytest.mark.parametrize("modname,fn", WRITERS)
def test_a_kill_during_the_record_write_leaves_the_previous_record_intact(
        tmp_path, monkeypatch, modname, fn):
    """THE CONTROL (start red): failed against the bare `write_text` shape.

    Both hooks simulate the same physical event -- a kill inside the write --
    on whichever writer is live: truncate-then-half-write-then-die for the old
    `write_text(json.dumps(...))` shape, die-after-tmp-before-replace for
    `write_json_atomic`. Pre-migration the first hook fired and the record was
    left truncated; post-migration the previous record survives both."""
    writer = getattr(importlib.import_module(modname), fn)
    target = tmp_path / "record.json"
    previous = {"schema": "previous-record", "value": 3.14}
    target.write_text(json.dumps(previous, indent=2) + "\n")
    original = target.read_bytes()

    def kill_mid_truncate_write(self, data, *a, **k):
        with self.open("w") as fh:
            fh.write(data[: len(data) // 2])
        raise RuntimeError("injected: killed mid truncate-then-write")

    def kill_before_replace(src, dst):
        raise RuntimeError("injected: killed after tmp write, before replace")

    monkeypatch.setattr(pathlib.Path, "write_text", kill_mid_truncate_write)
    monkeypatch.setattr(os, "replace", kill_before_replace)

    with pytest.raises(RuntimeError):
        writer(target, {"schema": "previous-record", "value": 3.15, "moved": True})

    assert target.read_bytes() == original, (
        f"a kill during {modname}.{fn} corrupted the previous complete record")
    assert list(tmp_path.glob("*tmp*")) == [], (
        f"a tmp sibling survived the failed write in {modname}: "
        f"{list(tmp_path.glob('*tmp*'))}")


@pytest.mark.parametrize("modname,fn", WRITERS)
def test_no_module_writes_a_record_inline_any_more(modname, fn):
    """The routing half of the control above. The injected kill drives the
    `write_json` seam directly, so it stays green even if a module's write
    site stops calling the seam and writes inline again -- the exact
    regression this migration repaired (found by reverting one module to
    `RECORD.write_text(json.dumps(...))` and watching the kill control stay
    green). This guard reads each module's source and fails on ANY inline
    write -- `.write_text(` or `json.dump(`. Both are zero occurrences across
    the twelve migrated modules today; a new tracked-JSON write belongs
    through the shared seam, and a future plain-text `write_text` extends
    this list consciously rather than bypassing it."""
    module = importlib.import_module(modname)
    src = inspect.getsource(module)
    assert not re.search(r"\.write_text\(", src), (
        f"{modname} writes inline via write_text -- tracked records must go "
        f"through the {fn} seam (run_all.write_json_atomic)")
    assert not re.search(r"json\.dump\(", src), (
        f"{modname} serialises inline via json.dump -- tracked records must "
        f"go through the {fn} seam (run_all.write_json_atomic)")


# ---------------------------------------------------------------------------
# the read half: a damaged record refuses with its path
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("which", ["record", "candidate", "baseline"])
@pytest.mark.parametrize("damage", ["truncated", "empty"])
def test_a_damaged_m1a_input_is_refused_naming_that_file(
        tmp_path, monkeypatch, capsys, which, damage):
    """`check_m1a_selection` reads three tracked records (#882): the refusal
    must name the file that is unreadable, not print a bare
    `Expecting value: line 1 column 1` for any of the three."""
    import check_m1a_selection as sel

    paths = {"record": tmp_path / "M1A.json",
             "candidate": tmp_path / "candidate.json",
             "baseline": tmp_path / "baseline.json"}
    valid = json.dumps({"schema": "m1a-test", "value": 1})
    for name, p in paths.items():
        p.write_text(valid[:12] if name == which and damage == "truncated"
                     else ("" if name == which and damage == "empty" else valid))
    monkeypatch.setattr(sel, "CANDIDATE", paths["candidate"])
    monkeypatch.setattr(sel, "BASELINE", paths["baseline"])

    assert sel.main(["--record", str(paths["record"])]) == 2
    assert str(paths[which]) in capsys.readouterr().out


def test_a_truncated_f1_record_is_refused_with_its_path(tmp_path, capsys):
    """Regression pin for `check_f1_rtl_record`, whose refusal already names
    the path (#882 found it so); a discovery change that drops the path from
    the message must turn this red."""
    import check_f1_rtl_record as f1

    p = tmp_path / "f1-record.json"
    p.write_text('{"case_id": "F1"')
    assert f1.main([str(p)]) == 2
    assert str(p) in capsys.readouterr().err


def test_a_truncated_profile_is_refused_with_its_path(tmp_path, monkeypatch):
    """`refprofile.load_profile` on the #842 shape: a profile left short by a
    killed write must refuse with the path and the way to restore it, and the
    refusal must be this module's `Refused` -- the type every caller of
    `load_profile` already catches and prints as REFUSED."""
    import refprofile as rp

    pj = tmp_path / "profile.json"
    pj.write_text('{"schema": "refprofile/1", "clips": [')
    monkeypatch.setattr(rp, "PROFILE_JSON", pj)
    with pytest.raises(rp.Refused) as e:
        rp.load_profile()
    assert str(pj) in str(e.value)


def test_a_truncated_notes_sibling_is_refused_with_its_path(tmp_path, monkeypatch):
    """The notes file rides along with the profile (#129); a truncated one is
    refused with its own path, not the profile's."""
    import refprofile as rp

    pj = tmp_path / "profile.json"
    pj.write_text(json.dumps({"schema": "refprofile/1", "clips": []}))
    nj = rp.notes_path_for(pj)
    nj.write_text('{"note":')
    monkeypatch.setattr(rp, "PROFILE_JSON", pj)
    with pytest.raises(rp.Refused) as e:
        rp.load_profile()
    assert str(nj) in str(e.value)


def test_a_non_utf8_profile_is_refused_with_its_path(tmp_path, monkeypatch):
    """The #909 shape: a record cut mid-UTF-8-character dies in `read_text()`
    before json.loads ever sees it -- UnicodeDecodeError is not a
    JSONDecodeError, so it needs its own clause (manifest.load_json_refused
    carries one) and must refuse with the path all the same."""
    import refprofile as rp

    pj = tmp_path / "profile.json"
    pj.write_bytes(b'{"schema": "\xff\xfe", ')
    monkeypatch.setattr(rp, "PROFILE_JSON", pj)
    with pytest.raises(rp.Refused) as e:
        rp.load_profile()
    assert str(pj) in str(e.value)
