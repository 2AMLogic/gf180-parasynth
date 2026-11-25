"""Run with GF180_TR808_REFS pointing to the pinned Fischer git checkout.

Exercise the provenance gate shared by the D12A and final-strike experiments.
All substitutions happen in a temporary clone. No audio rendering is needed.
"""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import clap_d12a_probe as clap
import dphase_floor_census as census

PIN = "85fbecf1bec32553395625ea659e2a56dfd7c0e1"


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    src = Path(os.environ["GF180_TR808_REFS"]).resolve()
    tmp = tmp_path_factory.mktemp("clap-corpus")
    refs = tmp / "refs"
    subprocess.run(["git", "clone", "-q", "--no-checkout", str(src), str(refs)], check=True)
    subprocess.run(["git", "-C", str(refs), "checkout", "-q", "--detach", PIN], check=True)
    rows = []
    for path in sorted(refs.rglob("*.WAV")):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        rows.append(f"{digest}  {path.relative_to(refs)}\n")
    manifest = tmp / "all.sha256"
    manifest.write_text("".join(rows))
    baseline = clap.basis(refs, manifest)["reference"]
    assert baseline["corpus_git_head"] == PIN
    assert baseline["files_checked"] == len(rows) == 116
    assert baseline["cp_sha256"] == "376429bb81cb48d1f392a11cd066c32ffb7883d445b2488704ea52e46bb08286"

    assert census.corpus_digest(str(refs)) == (census.EXPECT_CORPUS_SHA256, 116)
    shutil.copyfile(refs / "sd8/SD5050.WAV", refs / "cp8/CP.WAV")
    digest, count = census.corpus_digest(str(refs))
    assert count == 116 and digest != census.EXPECT_CORPUS_SHA256
    with pytest.raises(clap.Refused, match=r"1 of 116 .*cp8/CP.WAV"):
        clap.basis(refs, manifest)
    return refs, rows


@pytest.mark.parametrize("case", ["empty", "116-copies-of-another-file"])
def test_swapped_clap_must_be_refused(corpus, tmp_path, case):
    refs, rows = corpus
    manifest = tmp_path / "subset.sha256"
    other_row = next(row for row in rows if "sd8/SD5050.WAV" in row)
    manifest.write_text("" if case == "empty" else other_row * len(rows))
    try:
        reference = clap.basis(refs, manifest)["reference"]
    except clap.Refused:
        return
    pytest.fail(f"replaced clap accepted: {reference['files_checked']} checked, "
                f"{reference['files_mismatched']} mismatched, CP={reference['cp_sha256']}")
