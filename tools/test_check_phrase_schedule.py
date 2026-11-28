"""tools/check_phrase_schedule.py: a manifest that cannot be read is REFUSED
(#1604, #1623).

`intended()` reads `m["timeline"]` with `[]`. Before #1623 a manifest without
it exited 1 by `KeyError` -- FAIL -- where `tools/exit_contract.py` says an
unmet precondition is exit 2, and `main()` had no handler for it. One control
per key through the tool's own `main()`; the refusal fires before any write
log is opened, so no bench run is needed.
"""
import json
import pathlib
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import check_phrase_schedule as cps  # noqa: E402

SHIPPED = HERE.parent / "docs" / "scorecard" / "mono-m5a-miniv3" / "manifest.json"


def _main(tmp_path, capsys, manifest):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    rc = cps.main(["--manifest", str(path), f"run={tmp_path / 'runs'}"])
    return rc, capsys.readouterr().out


def test_the_shipped_phrase_manifest_still_states_its_schedule():
    """The guard must be satisfiable against the manifest that ships."""
    on, off = cps.intended(SHIPPED)
    assert on and len(on) == len(off)


def test_a_manifest_without_timeline_is_refused(tmp_path, capsys):
    m = json.loads(SHIPPED.read_text())
    del m["timeline"]
    rc, out = _main(tmp_path, capsys, m)
    assert rc == 2 and "REFUSED" in out and "'timeline'" in out


def test_a_manifest_with_null_timeline_is_refused(tmp_path, capsys):
    m = json.loads(SHIPPED.read_text())
    m["timeline"] = None
    rc, out = _main(tmp_path, capsys, m)
    assert rc == 2 and "REFUSED" in out and "'timeline'" in out


@pytest.mark.parametrize("manifest, why", [({}, "'timeline'"), ([], "not a JSON object"),
                                           (None, "not a JSON object")])
def test_a_manifest_that_is_not_an_object_is_refused(tmp_path, capsys, manifest, why):
    rc, out = _main(tmp_path, capsys, manifest)
    assert rc == 2 and "REFUSED" in out and why in out
