"""Exercise the M1A drive-report checker at its command-line entry point."""
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


ROOT = Path(__file__).resolve().parents[1]
REPORT = Path("docs/scorecard/mono-m1a-miniv3/drive-experiment/report.json")


class DriveReportPreservation(unittest.TestCase):
    def test_gain_preservation(self):
        for case in ("committed", "declared failure", "stale pass", "ungraded"):
            with self.subTest(case=case), TemporaryDirectory() as tmp:
                report = json.loads((ROOT / REPORT).read_text())
                candidate = report["candidates"]["0.25"]
                gain = candidate["properties"]["Gain"]
                self.assertTrue(gain["valid"])
                self.assertEqual(gain["tolerance"], 3.0)
                self.assertIs(candidate["property_passes"]["Gain"], True)
                if case in ("declared failure", "stale pass"):
                    gain.update(error=6.0, value=gain["reference"] + 6.0)
                    if case == "declared failure":
                        candidate["property_passes"]["Gain"] = False
                elif case == "ungraded":
                    gain.update(valid=False, value=None, reference=None, error=None)
                    del candidate["property_passes"]["Gain"]

                path = Path(tmp) / REPORT
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps(report))
                proc = subprocess.run(
                    [sys.executable, ROOT / "tools/check_m1a_drive_report.py"],
                    cwd=tmp, capture_output=True, text=True,
                )
                expected = (0, "consistent") if case == "committed" else (1, "INCONSISTENT")
                self.assertEqual((proc.returncode, proc.stdout.strip()), expected, proc.stderr)


if __name__ == "__main__":
    unittest.main()
