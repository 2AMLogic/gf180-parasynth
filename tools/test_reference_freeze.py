#!/usr/bin/env python3
"""`tools/check_reference_freeze.py`, against the real tree and against
defeated copies of it (#1050).

    python3 -m pytest tools/test_reference_freeze.py -q

The freeze is a guard, and verification-rules rule 8 says a guard ships with
the input that defeats it. The defeating inputs here are the four things a
freeze has to catch: a byte edited in a covered file, a covered file
deleted, a NEW file appearing inside a frozen directory (which the manifest
would silently not cover), and the freeze manifest itself going missing or
malformed. Each is built against a real copy of the frozen scope, the tool
is run over it with --root, and the verdict must name the offending path.

The first test is the gate itself: the committed tree passes.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import check_reference_freeze as cf                                # noqa: E402
import reference_suites as rs                                      # noqa: E402

MANIFEST = ROOT / "docs/scorecard/reference-suite-freeze.sha256"


@pytest.fixture()
def frozen_scope_copy(tmp_path_factory):
    """A full copy of everything the freeze covers, so mutants can be built
    without touching the frozen records.

    Function-scoped (#1053): each mutating test gets its own copy, and the
    copy is asserted to pass the freeze check BEFORE it is mutated — so a
    red verdict is attributable to this test's own defeat, not to damage a
    previous test left on a shared tree.
    """
    root = tmp_path_factory.mktemp("frozen-scope")
    for d in rs.SUITE_RECORDS[rs.SUITE_V3_FROZEN]:
        shutil.copytree(ROOT / d, root / d)
    for doc in cf.FREEZE_DOCS:
        shutil.copy2(ROOT / doc, root / doc)
    (root / cf.CORPUS_MANIFEST).write_text((ROOT / cf.CORPUS_MANIFEST).read_text())
    shutil.copy2(MANIFEST, root / MANIFEST.name)
    # The control: an unmutated copy must pass, or the test's verdict is
    # about the fixture, not the defeat it injects.
    assert cf.check(root, root / MANIFEST.name) == cf.OK
    return root


def test_the_committed_tree_passes_its_own_freeze():
    # The gate itself: run in-session, on the real records.
    assert cf.check(ROOT, MANIFEST) == cf.OK


def test_the_freeze_manifest_covers_the_declared_scope():
    lines = MANIFEST.read_text().splitlines()
    subjects = {line.split("  ", 2)[2] for line in lines}
    kinds = {line.split("  ", 2)[0] for line in lines}
    for d in rs.SUITE_RECORDS[rs.SUITE_V3_FROZEN]:
        tracked = subprocess.run(["git", "ls-files", d], cwd=ROOT,
                                 capture_output=True, text=True,
                                 check=True).stdout.split()
        for path in tracked:
            assert path in subjects, f"{path} is frozen scope but not covered"
    for doc in cf.FREEZE_DOCS:
        assert doc in subjects
    assert f"plugin_reference_suites.suites.miniv3-frozen" in subjects
    assert kinds == {"file", "canon"}


def test_one_edited_byte_in_a_frozen_file_is_named(frozen_scope_copy):
    victim = frozen_scope_copy / "docs/scorecard/mono-m5a-miniv3/m5a-miniv3-raw.wav"
    data = bytearray(victim.read_bytes())
    data[1000] ^= 0xFF
    victim.write_bytes(bytes(data))
    assert cf.check(frozen_scope_copy, frozen_scope_copy / MANIFEST.name) == cf.FAIL
    out = cf.check(frozen_scope_copy, frozen_scope_copy / MANIFEST.name)
    assert out == cf.FAIL
    # run through main() so the CLI's verdict text is checked too
    code = cf.main(["--root", str(frozen_scope_copy),
                    "--freeze-manifest", str(frozen_scope_copy / MANIFEST.name)])
    assert code == cf.FAIL


def test_a_deleted_frozen_file_is_a_broken_freeze(frozen_scope_copy):
    victim = (frozen_scope_copy / "docs/scorecard/mono-m5b-miniv3/"
              "legacy-score-v1.json")
    victim.unlink()
    assert cf.check(frozen_scope_copy, frozen_scope_copy / MANIFEST.name) == cf.FAIL


def test_a_new_file_inside_a_frozen_directory_is_flagged(frozen_scope_copy):
    # A freeze that only checks the files it listed would pass a tree where
    # a frozen record grew an unlisted sibling. The 'grew' check refuses to
    # be quietly incomplete.
    (frozen_scope_copy / "docs/scorecard/mono-m1a-miniv3/smuggled.json").write_text("{}")
    assert cf.check(frozen_scope_copy, frozen_scope_copy / MANIFEST.name) == cf.FAIL


def test_a_missing_freeze_manifest_is_refused_not_failed(frozen_scope_copy):
    (frozen_scope_copy / MANIFEST.name).unlink()
    assert cf.check(frozen_scope_copy, frozen_scope_copy / MANIFEST.name) == cf.REFUSED


def test_a_malformed_freeze_manifest_is_refused(frozen_scope_copy):
    p = frozen_scope_copy / MANIFEST.name
    p.write_text("this line is not the format\n")
    assert cf.check(frozen_scope_copy, p) == cf.REFUSED


def test_the_canonical_v3_suite_declaration_is_frozen_not_the_whole_corpus():
    # The V4 entry next to it must stay editable (renders will land), so the
    # canon hash covers ONLY the frozen suite's own declaration.
    entry = cf._resolve(ROOT, cf.FREEZE_CANON_KEYS[0])
    assert entry["plugin"] == "Arturia Mini V3"
    assert entry["version"] == rs.V3_FROZEN_VERSION
    assert "frozen" in entry["state"]
    import json
    corpus = json.loads((ROOT / cf.CORPUS_MANIFEST).read_text())
    v4 = corpus["plugin_reference_suites"]["suites"]["miniv4"]
    assert v4["required_version"] == rs.V4_REQUIRED_VERSION


# ---- a corpus manifest that cannot be read is REFUSED, not a traceback and
# not a broken freeze (#1604). The tool's own contract: "REFUSED 2 (... missing
# or malformed, so nothing could be checked)". Each case mutates the copy the
# fixture already proved passes, then runs main().
def _run(frozen_scope_copy, capsys):
    code = cf.main(["--root", str(frozen_scope_copy),
                    "--freeze-manifest", str(frozen_scope_copy / MANIFEST.name)])
    return code, capsys.readouterr().out


def _without(frozen_scope_copy, *path):
    """Rewrite the copy's corpus manifest with the key at `path` deleted."""
    p = frozen_scope_copy / cf.CORPUS_MANIFEST
    corpus = json.loads(p.read_text())
    parent = corpus
    for part in path[:-1]:
        parent = parent[part]
    del parent[path[-1]]
    p.write_text(json.dumps(corpus))


@pytest.mark.parametrize("path", [
    ("plugin_reference_suites",),
    ("plugin_reference_suites", "suites"),
    ("plugin_reference_suites", "suites", "miniv3-frozen"),
], ids=lambda path: ".".join(path))
def test_a_corpus_manifest_missing_a_frozen_key_is_refused(frozen_scope_copy, capsys, path):
    # one control per key of the frozen declaration's dotted path
    _without(frozen_scope_copy, *path)
    code, out = _run(frozen_scope_copy, capsys)
    assert code == cf.REFUSED
    assert out.startswith("REFUSED") and f"'{path[-1]}'" in out
    assert "FAIL" not in out and "PASS" not in out


@pytest.mark.parametrize("payload, named", [
    (b"null", "is null, not a JSON object"),
    (b"[]", "is a JSON list, not a JSON object"),
    (b'{"plugin_reference_suites": null}',
     "carries null for required key(s) 'plugin_reference_suites'"),
    (b"", "is not valid JSON"),
    (b'{"plugin_reference_suites": ', "is not valid JSON"),
], ids=["null", "array", "null-key", "empty", "truncated"])
def test_an_unreadable_corpus_manifest_is_refused(frozen_scope_copy, capsys, payload, named):
    (frozen_scope_copy / cf.CORPUS_MANIFEST).write_bytes(payload)
    code, out = _run(frozen_scope_copy, capsys)
    assert code == cf.REFUSED
    assert out.startswith("REFUSED") and named in out
    assert "FAIL" not in out and "PASS" not in out


def test_a_missing_corpus_manifest_is_refused(frozen_scope_copy, capsys):
    (frozen_scope_copy / cf.CORPUS_MANIFEST).unlink()
    code, out = _run(frozen_scope_copy, capsys)
    assert code == cf.REFUSED
    assert out.startswith("REFUSED") and "corpus manifest missing" in out
