"""`tools/check_phrase_schedule.py` through its CLI: the schedule it computes,
and its `--out` destination (#2098, a slice of the #2095 class)."""
from __future__ import annotations

import errno
import json
import os
import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import check_phrase_schedule as cps  # noqa: E402

#: Two notes at 0.50 s and 0.75 s in one 1.0 s segment, no silence after it.
MANIFEST = {"timeline": {"segments": [{"duration_s": 1.0, "midi_events": [
    {"on_s": 0.5, "gate_s": 0.25}, {"on_s": 0.75, "gate_s": 0.2}]}],
    "segment_silence_s": 0.0}}

#: The bench's write log: applied frame, flag, section, address, data, predicted.
#: One ordinary write first (the base), then the gates -- each applied exactly
#: where the pins predicted it, which is what this comparison requires.
LOG = """\
100 1 0 16 7 100
24005 1 0 32 1 24005
36000 1 0 33 0 36000
36003 1 0 32 1 36003
45600 1 0 33 0 45600
"""


@pytest.fixture
def phrase(tmp_path, monkeypatch):
    """A manifest and one run whose GATE_ONs land 5 and 3 frames late and whose
    GATE_OFFs 5 frames early, relative to the schedule with the first event
    aligned. The known answer is arithmetic on the numbers above, at 48 kHz:
    ON 24005 - 24000 = 5 (the base), so [0, -2]; OFF [36000, 45600] against
    [36000, 45600] less the base, so [-5, -5]."""
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(MANIFEST))
    run = tmp_path / "run"
    run.mkdir()
    (run / "top_wrs_a.txt").write_text(LOG)
    reads = []
    real_applied = cps.applied

    def applied(outdir):
        reads.append(outdir)
        return real_applied(outdir)

    monkeypatch.setattr(cps, "applied", applied)
    return manifest, run, reads


def _argv(phrase, *tail):
    manifest, run, _ = phrase
    return ["--manifest", str(manifest), f"a={run}", *tail]


def _full_disk(target):
    """`Path.write_text` that fails for `target` only."""
    real = pathlib.Path.write_text

    def write_text(self, data, *args, **kwargs):
        if self == target:
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(self, data, *args, **kwargs)

    return write_text


# ---------------------------------------------------------------------------
# the CLI's --out destination (#2098)
#
# THE DEFECT, red-first on origin/main @ 8eb04e5b (2026-11-28): `main` wrote
# `a.out` with `write_text` after every run's offsets had been computed and
# printed, and nothing made the parent directory. Under a missing one the
# write raised FileNotFoundError out of `main`: exit 1 with a traceback, for a
# comparison whose findings were on stdout, with the record lost.
#
# THE KNOWN ANSWER, from tools/exit_contract.py and not from the module under
# test: a destination that cannot be written is a precondition that was not
# met, so REFUSED (2), refused before a run is read where the path says so
# itself. This script's OWN input refusals exit 1 (`SystemExit("REFUSED: ...")`);
# that convention is kept as it was (#2098 AC 3) and pinned below so the fix
# cannot be read as having changed it.
#
# WHAT RUNS FOR REAL: the whole CLI -- `intended`, `applied` (counted, unmodified)
# and the offset arithmetic -- on a manifest and a write log written here.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("target, reason", [
    ("{tmp}", "names an existing directory"),
    ("{tmp}/a-file/schedule.json", "no record can be written there: [Errno 17] File exists"),
    ("nul\x00dir/schedule.json", "embedded null"),
], ids=["a-directory", "parent-is-a-file", "parent-has-a-nul-byte"])
def test_an_unwritable_out_destination_is_refused_before_any_run_is_read(phrase, tmp_path, capsys,
                                                                        target, reason):
    (tmp_path / "a-file").write_text("not a directory\n")
    _, _, reads = phrase
    rc = cps.main(_argv(phrase, "--out", target.format(tmp=tmp_path)))
    out, err = capsys.readouterr()
    assert rc == 2, (rc, out, err)
    assert "REFUSED" in err and reason in err, err
    assert reads == [], "a run was read for a destination that could not be written"
    assert "GATE_ON offsets" not in out, out


def test_an_out_record_under_a_missing_directory_is_created_and_written(phrase, tmp_path, capsys):
    target = tmp_path / "no-such-dir" / "schedule.json"
    rc = cps.main(_argv(phrase, "--out", str(target)))
    out, _ = capsys.readouterr()
    assert rc == 0, out
    assert "GATE_ON offsets (frames, vs the schedule, first event aligned): [0, -2]" in out, out
    assert json.loads(target.read_text()) == {
        "a": {"gate_on_offset_frames": [0, -2], "gate_off_offset_frames": [-5, -5],
              "worst_abs_frames": 5}}


def test_a_write_failure_after_the_offsets_is_refused_with_them_kept(phrase, tmp_path, capsys,
                                                                      monkeypatch):
    target = tmp_path / "schedule.json"
    monkeypatch.setattr(pathlib.Path, "write_text", _full_disk(target))
    rc = cps.main(_argv(phrase, "--out", str(target)))
    out, err = capsys.readouterr()
    assert rc == 2, (rc, out, err)
    assert "[0, -2]" in out and "[-5, -5]" in out, out
    assert "REFUSED" in err and "not written" in err, err
    assert "No space left on device" in err and "Traceback" not in err, err
    assert not target.exists()


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root writes into a read-only directory")
def test_a_read_only_parent_is_refused_and_leaves_no_record(phrase, tmp_path, capsys):
    """Rule 8: the input that defeats the pre-flight. The parent exists, so
    `mkdir(exist_ok=True)` accepts it; only the guard around the write can
    refuse it."""
    parent = tmp_path / "read-only"
    parent.mkdir()
    parent.chmod(0o555)
    try:
        rc = cps.main(_argv(phrase, "--out", str(parent / "schedule.json")))
        written = list(parent.iterdir())
    finally:
        parent.chmod(0o700)
    out, err = capsys.readouterr()
    assert rc == 2, (rc, out, err)
    assert "REFUSED" in err and "not written" in err, err
    assert "[0, -2]" in out, out
    assert written == []


def test_the_two_exit_statuses_as_a_process_sees_them(phrase, tmp_path):
    """Through `python tools/check_phrase_schedule.py`, not `main`: this
    script's input refusals exit 1 by its own `SystemExit(str)` convention and
    that is kept; the destination refusal this issue adds exits 2."""
    manifest, run, _ = phrase
    script = str(pathlib.Path(cps.__file__))
    refused_run = subprocess.run([sys.executable, script, "--manifest", str(manifest),
                                  f"a={tmp_path / 'no-such-run'}"],
                                 capture_output=True, text=True)
    assert refused_run.returncode == 1, refused_run
    assert refused_run.stderr.startswith("REFUSED:"), refused_run.stderr
    assert "expected one write log" in refused_run.stderr, refused_run.stderr

    refused_out = subprocess.run([sys.executable, script, "--manifest", str(manifest),
                                  f"a={run}", "--out", str(tmp_path)],
                                 capture_output=True, text=True)
    assert refused_out.returncode == 2, refused_out
    assert "REFUSED" in refused_out.stderr and "names an existing directory" in refused_out.stderr
    assert "GATE_ON offsets" not in refused_out.stdout, refused_out.stdout
