"""Check the inventory against a writer we can actually run in a tiny Git tree."""
import json
from pathlib import Path
import re
import subprocess
import sys

import pytest

import audit_tracked_json_writers as audit


def _run_audit(script, dump):
    result = subprocess.run([sys.executable, str(script), "--dump", str(dump)],
                            capture_output=True, text=True, check=True)
    states = {Path(path).relative_to("data").as_posix(): row["state"]
              for path, row in json.loads(dump.read_text()).items()}
    return states, result.stdout.splitlines()[0]


def _audit(root):
    """Track a prepared tree in Git and run this checkout's audit script against it."""
    subprocess.run(["git", "init", "-q", root], check=True)
    subprocess.run(["git", "-C", root, "add", "."], check=True)
    script = root / "tools" / "audit_tracked_json_writers.py"
    script.parent.mkdir()
    script.write_bytes(Path(audit.__file__).read_bytes())
    return _run_audit(script, root / "inventory.json")


@pytest.fixture(scope="module")
def inventory(tmp_path_factory):
    root = tmp_path_factory.mktemp("writer-inventory")
    data = root / "data"
    data.mkdir()
    (data / "nested").mkdir()
    (data / "points").mkdir()
    records = (
        "prereg.json", "claim.json", "phase-report.json", "ledger.json",
        "oscillator-diagnosis.json", "rescore.json", "points/attack-1.json", "points/grid.json",
        "nested/oscillator-diagnosis.json", "possible.json", "exclusive.json",
    )
    for record in records:
        (data / record).write_text("null\n")
    (root / "locations.py").write_text('''
from pathlib import Path
ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data"
''')
    (root / "run_all.py").write_text('''
def write_json_atomic(path, obj):
    import json
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj))
    tmp.replace(path)
''')
    writer = root / "writer.py"
    writer.write_text('''
import json
import os
import locations as experiment
import run_all
from pathlib import Path
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
POINTS = DATA_DIR / "points"
PREREG = DATA_DIR / "prereg.json"

def write_json(path, obj):
    run_all.write_json_atomic(path, obj)
PHASE_REPORT = DATA_DIR / "phase-report.json"

with PREREG.open() as source, (DATA_DIR / "claim.json").open("w") as dest:
    json.dump({"source": str(PREREG), "value": json.load(source)}, fp=dest)
write_json(DATA_DIR / "ledger.json", {"input": str(PHASE_REPORT)})
(experiment.OUT / "oscillator-diagnosis.json").write_text(json.dumps(True))
with open(experiment.OUT / "rescore.json", "w") as dest:
    json.dump(True, dest)

def point_name(ms):
    return f"attack-{ms}"
(POINTS / f"{point_name(1)}.json").write_text(json.dumps(True))

def emit(directory):
    write_json(directory / "possible.json", {"source": str(PREREG)})


emit(DATA_DIR)
fd = os.open(DATA_DIR / "exclusive.json", os.O_CREAT | os.O_EXCL | os.O_WRONLY)
with os.fdopen(fd, "w") as dest:
    json.dump({"prereg": PREREG.read_text()}, dest)
''')
    # The exclusive create succeeds on the first run only. It cannot truncate
    # an existing record and so does not belong in the repair queue.
    (data / "exclusive.json").unlink()
    subprocess.run([sys.executable, str(writer)], check=True)
    changed = {record for record in records
               if (data / record).read_text() != "null\n"}
    states, header = _audit(root)
    assert set(states) == set(records)
    return states, changed, header


@pytest.fixture(scope="module")
def async_inventory(tmp_path_factory):
    """An executed tree; `changed` is the run's own account of what was written.
    The awaited writers run under asyncio.run(main()), the un-awaited and
    mis-awaited forms raise or build a coroutine, and the asyncio.run bridges
    run from module level. The dynamic-name helper writes under data/named so
    its candidate pattern leaves the flat records free to be no-writer."""
    root = tmp_path_factory.mktemp("async-writer-inventory")
    data = root / "data"
    data.mkdir()
    (data / "named").mkdir()
    records = ("awaited.json", "named/argument.json", "named/constant.json", "awaited-seam.json",
               "unawaited.json", "unawaited-seam.json", "await-sync-helper.json",
               "run-wrapped.json", "run-wrapped-seam.json")
    for record in records:
        (data / record).write_text("null\n")
    (root / "run_all.py").write_text('''
async def write_json_atomic(path, obj, default=None):
    import json
    from pathlib import Path
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(obj, default=default))
    tmp.replace(path)
''')
    writer = root / "writer.py"
    writer.write_text('''
import asyncio
import json
import run_all
from pathlib import Path
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
REPORT = "constant.json"

async def out_file():
    return DATA_DIR / "awaited.json"

async def named_file(REPORT):
    return DATA_DIR / "named" / REPORT

def sync_file(name):
    return DATA_DIR / name

async def run_file():
    return DATA_DIR / "run-wrapped.json"

async def write_json(path, obj, default=None):
    """Async seam: forwards its path."""
    await run_all.write_json_atomic(str(path), obj, default=default)

async def main():
    (await out_file()).write_text(json.dumps(True))
    with (await out_file()).open("w") as dest:
        json.dump(True, dest)
    (await named_file("argument.json")).write_text(json.dumps(True))
    await write_json(DATA_DIR / "awaited-seam.json", {"ok": 1})
    try:
        (out_file()).write_text(json.dumps(True))
    except AttributeError as exc:
        assert "write_text" in str(exc)
    write_json(DATA_DIR / "unawaited-seam.json", {"ok": 1})
    try:
        (await sync_file("await-sync-helper.json")).write_text(json.dumps(True))
    except TypeError as exc:
        assert "await" in str(exc)

asyncio.run(main())
(asyncio.run(run_file())).write_text(json.dumps(True))
asyncio.run(write_json(DATA_DIR / "run-wrapped-seam.json", {"ok": 1}))
''')
    subprocess.run([sys.executable, str(writer)], check=True)
    changed = {record for record in records
               if (data / record).read_text() != "null\n"}
    states, _ = _audit(root)
    assert set(states) == set(records)
    return states, changed, root


def test_only_the_output_argument_or_writable_handle_is_a_destination(inventory):
    states, changed, _ = inventory
    assert not changed.intersection({"prereg.json", "phase-report.json"})
    assert states["prereg.json"] == states["phase-report.json"] == "no-writer"
    assert {"claim.json", "ledger.json", "exclusive.json"} <= changed
    assert states["claim.json"] == "INLINE"
    assert states["ledger.json"] == "atomic"
    assert states["exclusive.json"] == "no-writer"


def test_qualified_constants_name_an_exact_path(inventory):
    states, changed, _ = inventory
    for record in ("oscillator-diagnosis.json", "rescore.json"):
        assert record in changed
        assert states[record] == "INLINE"
    assert states["nested/oscillator-diagnosis.json"] == "no-writer"


def test_dynamic_point_name_does_not_confirm_grid(inventory):
    states, changed, _ = inventory
    assert "points/attack-1.json" in changed and "points/grid.json" not in changed
    assert states["points/grid.json"] not in ("INLINE", "atomic")
    assert states["points/attack-1.json"] not in ("INLINE", "atomic")


def test_summary_partitions_the_dump_including_atomic_uncertainty(inventory):
    states, _, header = inventory
    assert states["possible.json"] == "atomic?"
    counts = {label: int(n) for label, n in
              re.findall(r"(atomic|INLINE|ambiguous|no-writer) (\d+)", header)}
    assert counts == {
        "atomic": sum(s == "atomic" for s in states.values()),
        "INLINE": sum(s == "INLINE" for s in states.values()),
        "ambiguous": sum(s.endswith("?") for s in states.values()),
        "no-writer": sum(s == "no-writer" for s in states.values()),
    }
    assert sum(counts.values()) == len(states)


def test_awaited_async_helper_resolves_its_inline_destination(async_inventory):
    """`await helper()` as a path expression, as a with-handle's target and with an
    argument resolves exactly as a sync helper's call does."""
    states, changed, _ = async_inventory
    assert {"awaited.json", "named/argument.json"} <= changed
    assert states["awaited.json"] == "INLINE"
    assert states["named/argument.json"] == "INLINE?"


def test_awaited_async_seam_is_atomic(async_inventory):
    states, changed, _ = async_inventory
    assert "awaited-seam.json" in changed
    assert states["awaited-seam.json"] == "atomic"


def test_awaited_helper_parameter_shadows_the_module_constant(async_inventory):
    states, changed, _ = async_inventory
    assert "named/constant.json" not in changed
    assert states["named/constant.json"] != "INLINE"


@pytest.mark.parametrize("record", ["unawaited.json", "unawaited-seam.json", "await-sync-helper.json"])
def test_coroutine_or_failed_await_never_confirms_a_writer(async_inventory, record):
    """Rule 8: a bare call to an async helper or seam yields a coroutine and `await`
    on a sync helper raises; neither reaches the write, so neither is a confirmed site."""
    states, changed, _ = async_inventory
    assert record not in changed
    assert states[record] == "no-writer"


def test_documented_gap_asyncio_run_wrapped_helper_is_unresolved(async_inventory):
    """Stated, not closed (rule 8): `asyncio.run(helper())` runs the coroutine and
    writes, but a helper or seam is followed only through `await`, so the exact
    path is UNKNOWN and the executed records read no-writer. Pinned so the
    optimistic miss stays visible; no production async def exists today, and the
    bridge is not modelled."""
    states, changed, _ = async_inventory
    assert {"run-wrapped.json", "run-wrapped-seam.json"} <= changed
    assert states["run-wrapped.json"] == states["run-wrapped-seam.json"] == "no-writer"


@pytest.mark.parametrize("old,new,dark,lit", [
    ("isinstance(stmt, FUNCTION_NODES) and len(stmt.body) == 1",
     "isinstance(stmt, ast.FunctionDef) and len(stmt.body) == 1", "awaited.json", "awaited-seam.json"),
    ("if isinstance(node, ast.Await):", "if False:", "awaited.json", "awaited-seam.json"),
    ("isinstance(stmt, FUNCTION_NODES) and stmt.name",
     "isinstance(stmt, ast.FunctionDef) and stmt.name", "awaited-seam.json", "awaited.json"),
])
def test_reverted_widening_hides_the_async_writer_again(async_inventory, old, new, dark, lit):
    """Injected-revert control, one per widened check: with only that check put back
    its executed writer is `no-writer` again, while the other one stays classified."""
    states, _, root = async_inventory
    script = Path(audit.__file__).read_text()
    assert script.count(old) == 1, "revert target moved; the control would be vacuous"
    mutant = root / "tools" / "reverted_audit.py"
    mutant.write_text(script.replace(old, new))
    reverted, _ = _run_audit(mutant, root / "reverted-inventory.json")
    assert reverted[dark] == "no-writer"
    assert reverted[lit] == states[lit] != "no-writer"
