"""Exercise the conga pitch-drop report with copies of its measured input."""
from pathlib import Path
import json
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("keep", ["all", "only-LC-A", "none"])
def test_pitch_drop_report_requires_the_measured_conga_cells(tmp_path, keep):
    # Copy the entry point and input loader so their __file__-relative docs
    # paths resolve to the fixture. All model dependencies use the real code.
    model = tmp_path / "model"
    model.mkdir()
    for path in (ROOT / "model").glob("*.py"):
        if path.name in {"tom_drop_docs.py", "tom_drop_fit.py"}:
            shutil.copy(path, model / path.name)
        else:
            (model / path.name).symlink_to(path)
    (tmp_path / "tools").symlink_to(ROOT / "tools", target_is_directory=True)
    (tmp_path / "audition").symlink_to(ROOT / "audition", target_is_directory=True)
    docs = tmp_path / "docs"
    docs.mkdir()
    for name in ["tr808-reference.md", "drum-verification.md"]:
        shutil.copy(ROOT / "docs" / name, docs)
    name = "tom-pitch-drop-results.json"
    record = json.loads((ROOT / "docs" / name).read_text())
    if keep != "all":
        for voice in ["LC", "MC", "HC"]:
            for accent in ["A", "B"]:
                if keep == "none" or (voice, accent) != ("LC", "A"):
                    del record["voices"][voice][accent]
    (docs / name).write_text(json.dumps(record))

    output = tmp_path / "report.json"
    result = subprocess.run(
        [sys.executable, str(model / "tom_drop_docs.py"), "--json", str(output)],
        capture_output=True, text=True, check=False,
    )
    if keep == "all":
        assert result.returncode == 0, result.stdout + result.stderr
        report = json.loads(output.read_text())["congas"]
        expected = {f"{voice} {accent}" for voice in ["LC", "MC", "HC"] for accent in "AB"}
        assert set(report["cells"]) == expected
        assert report["verdict"] == "PASS"
    else:
        assert result.returncode == 2, result.stdout + result.stderr
        assert "REFUSED" in result.stdout
        assert not output.exists()
