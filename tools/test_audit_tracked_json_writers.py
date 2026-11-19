"""Check the inventory against a writer we can actually run in a tiny Git tree."""
import json
from pathlib import Path
import re
import subprocess
import sys

import pytest

import audit_tracked_json_writers as audit


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
    subprocess.run(["git", "init", "-q", root], check=True)
    subprocess.run(["git", "-C", root, "add", "."], check=True)
    script = root / "tools" / "audit_tracked_json_writers.py"
    script.parent.mkdir()
    script.write_bytes(Path(audit.__file__).read_bytes())
    dump = root / "inventory.json"
    result = subprocess.run([sys.executable, str(script), "--dump", str(dump)],
                            capture_output=True, text=True, check=True)
    states = {Path(path).relative_to("data").as_posix(): row["state"]
              for path, row in json.loads(dump.read_text()).items()}
    assert set(states) == set(records)
    return states, changed, result.stdout.splitlines()[0]


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
