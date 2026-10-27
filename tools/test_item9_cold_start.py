"""Static checks on the item 9 cold-start list (the run itself is ~8 min, attested
by `klt signoff --attest 9`, not repeated here)."""
import json
import pathlib
import shlex

import item9_cold_start as m

ROOT = m.ROOT


def test_every_command_names_an_existing_script():
    for c in m.BENCHES + m.CONTROLS:
        assert (ROOT / shlex.split(c)[0]).is_file(), c


def test_every_control_inverts_its_verdict():
    # Without --expect-fail a control that is "caught" (exit 1) reads as a failure and
    # one that runs clean (exit 0) reads as a pass: the polarity is the whole point.
    for c in m.CONTROLS:
        assert "--expect-fail" in c, c


def test_no_bench_is_a_control_and_none_is_duplicated():
    assert not any("--inject" in c or "--expect-fail" in c for c in m.BENCHES)
    assert len(set(m.BENCHES + m.CONTROLS)) == len(m.BENCHES + m.CONTROLS)


def test_inventory_declares_this_command_and_lists_every_script_it_runs():
    inv = json.loads((ROOT / "manifests/inventories/item-9.json").read_text())
    assert inv["cold_start"]["argv"] == ["python3", "tools/item9_cold_start.py"]
    listed = set(inv["testbenches"])
    for c in m.BENCHES + m.CONTROLS:
        assert shlex.split(c)[0] in listed, c
