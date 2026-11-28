"""`tools/gen_rate_conv_2x.py` through its CLI: the `--out` destination in
write mode, and `--check` mode's own exit status (#2098, a slice of #2095)."""
from __future__ import annotations

import errno
import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import gen_rate_conv_2x as gen  # noqa: E402


@pytest.fixture
def generated(monkeypatch):
    """The real `generate`, counted, so a refusal can be shown to precede it."""
    calls = []
    real = gen.generate

    def count():
        calls.append(1)
        return real()

    monkeypatch.setattr(gen, "generate", count)
    return calls


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
# THE DEFECT, red-first on origin/main @ 8eb04e5b (2026-11-28): in write mode
# `main` wrote `args.out` with `write_text` and nothing made the parent
# directory, so `--out new-dir/rate_conv_2x.v` died on FileNotFoundError:
# exit 1 with a traceback -- the same status `--check` uses for "the generated
# RTL differs", which is a verdict.
#
# THE KNOWN ANSWER, from tools/exit_contract.py and not from the module under
# test: a destination that cannot be written is REFUSED (2), before the text
# is generated where the path says so itself.
#
# WHAT RUNS FOR REAL: the whole CLI, `generate()` included (scipy's firwin).
# Every test names `--out`: the default is the repository's own
# rtl-sketch/rate_conv_2x.v, which no test may write.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("target, reason", [
    ("{tmp}", "names an existing directory; name the file"),
    ("{tmp}/a-file/rate_conv_2x.v", "no file can be written there: [Errno 17] File exists"),
    ("nul\x00dir/rate_conv_2x.v", "embedded null"),
], ids=["a-directory", "parent-is-a-file", "parent-has-a-nul-byte"])
def test_an_unwritable_out_destination_is_refused_before_anything_is_generated(generated, tmp_path,
                                                                              capsys, target,
                                                                              reason):
    (tmp_path / "a-file").write_text("not a directory\n")
    rc = gen.main(["--out", target.format(tmp=tmp_path)])
    out, err = capsys.readouterr()
    assert rc == 2, (rc, out, err)
    assert "REFUSED" in err and reason in err, err
    assert generated == [], "the RTL was generated for a destination that could not be written"
    assert "generated" not in out, out


def test_an_out_file_under_a_missing_directory_is_created_and_written(generated, tmp_path, capsys):
    target = tmp_path / "no-such-dir" / "rate_conv_2x.v"
    rc = gen.main(["--out", str(target)])
    out, _ = capsys.readouterr()
    assert rc == 0, out
    assert generated == [1]
    text = target.read_text()
    assert text == gen.generate() and "module rate_conv_2x" in text
    assert f"rate_conv_2x: generated {target}" in out, out


def test_a_write_failure_after_generation_is_refused_not_generated(generated, tmp_path, capsys,
                                                                   monkeypatch):
    """This script has no measurement and no verdict line: the file IS the
    deliverable, so what must not be printed is the `generated` claim."""
    target = tmp_path / "rate_conv_2x.v"
    monkeypatch.setattr(pathlib.Path, "write_text", _full_disk(target))
    rc = gen.main(["--out", str(target)])
    out, err = capsys.readouterr()
    assert rc == 2, (rc, out, err)
    assert "REFUSED" in err and "not written" in err, err
    assert "No space left on device" in err and "Traceback" not in err, err
    assert "generated" not in out and "verified" not in out, out
    assert not target.exists()


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root writes into a read-only directory")
def test_a_read_only_parent_is_refused_and_leaves_no_file(generated, tmp_path, capsys):
    """Rule 8: the input that defeats the pre-flight. The parent exists, so
    `mkdir(exist_ok=True)` accepts it; only the guard around the write can
    refuse it."""
    parent = tmp_path / "read-only"
    parent.mkdir()
    parent.chmod(0o555)
    try:
        rc = gen.main(["--out", str(parent / "rate_conv_2x.v")])
        written = list(parent.iterdir())
    finally:
        parent.chmod(0o700)
    out, err = capsys.readouterr()
    assert rc == 2, (rc, out, err)
    assert "REFUSED" in err and "not written" in err, err
    assert "generated" not in out, out
    assert written == []


def test_check_mode_reads_the_out_path_and_keeps_its_own_exit_1(generated, tmp_path, capsys):
    """`--check` never writes `--out`; it reads it. A missing file there is
    the verdict `generated RTL differs` (exit 1), so the destination check
    must not run in that mode -- not refuse it as 2, and not create the
    directory (#2098 AC 3: the script's own exit-code convention is kept)."""
    target = tmp_path / "no-such-dir" / "rate_conv_2x.v"
    rc = gen.main(["--check", "--out", str(target)])
    out, err = capsys.readouterr()
    assert rc == 1, (rc, out, err)
    assert f"generated RTL differs: {target}" in out, out
    assert "REFUSED" not in err, err
    assert not target.parent.exists(), "--check created the directory it was only to read"


def test_check_mode_verifies_a_file_the_generator_wrote(generated, tmp_path, capsys):
    target = tmp_path / "rate_conv_2x.v"
    assert gen.main(["--out", str(target)]) == 0
    capsys.readouterr()
    assert gen.main(["--check", "--out", str(target)]) == 0
    assert f"rate_conv_2x: verified {target}" in capsys.readouterr().out
