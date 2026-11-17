"""Controls for `check_attested_hashes.check_tree` (issue #884).

PR #865 edited an attested workflow and `regrade` went red as a bare exit 3
with klt's drift report swallowed; nothing local told the Builder that editing
a path in `manifests/inventories/item-*.json` requires regenerating the matching
`manifests/evidence/item-*.json`. The check recomputes sha256-over-raw-bytes
(validated: 37/37 committed checks match, as of `bcf53a6a`) and compares it to
the committed `content_hash`, so BOTH guard-defeat shapes go red:

  * edit an attested file and hand-edit nothing  -> drift (the #865 shape)
  * edit nothing and hand-edit the evidence hash -> drift (rule 8: the diff-shape
    check the issue rejected would pass this; the hash comparison must not)

plus the drop-a-check hole (evidence stops covering a listed file), the
inventory self-hash coupling, and the REFUSED outcomes (exit 2: missing or
malformed evidence -- "no evidence" is not "no problem").

`check_tree` runs against any root, so every control here builds a small
fixture tree in a temp dir except the last test, which pins the LIVE repo to
green -- that is the gate verify-fast actually runs.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import check_attested_hashes as cah                                    # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[1]


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _tree(tmp: pathlib.Path, files: dict[str, bytes]) -> pathlib.Path:
    """A minimal manifests/ tree: two attested files, an inventory listing
    both, and an evidence envelope whose hashes are correct for them."""
    root = tmp / "repo"
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    inv = root / "manifests" / "inventories"
    ev = root / "manifests" / "evidence"
    inv.mkdir(parents=True)
    ev.mkdir(parents=True)
    listed = sorted(files)
    (inv / "item-1.json").write_text(json.dumps({
        "schema_version": 1,
        "kind": "t1-attestation-inventory",
        "t1_item": 1,
        "hygiene": listed,
    }))
    checks = [{"check": "present", "role": "hygiene", "path": rel,
               "passed": True, "content_hash": _sha(files[rel])}
              for rel in listed]
    checks.append({"check": "command", "role": "audit", "path": None,
                   "passed": True, "content_hash": None})
    (ev / "item-1.json").write_text(json.dumps({
        "schema_version": 1,
        "kind": "t1-attestation-evidence",
        "t1_item": 1,
        "attestation": {"checks": checks},
        "provenance": {"input": {
            "path": {"path": "manifests/inventories/item-1.json", "scope": "repo"},
            "content_hash": _sha((inv / "item-1.json").read_bytes()),
        }},
    }))
    return root


def _read_evidence(root: pathlib.Path) -> dict:
    return json.loads(
        (root / "manifests" / "evidence" / "item-1.json").read_text())


def _write_evidence(root: pathlib.Path, ev: dict) -> None:
    (root / "manifests" / "evidence" / "item-1.json").write_text(json.dumps(ev))


# =============================================================================
# The two guard-defeat shapes, red first (docs/verification-rules.md rule 8).
# =============================================================================
def test_edited_attested_file_without_evidence_is_red(tmp_path):
    root = _tree(tmp_path, {"a.yml": b"on: push\n", "b.yml": b"steps: []\n"})
    (root / "a.yml").write_bytes(b"on: push\njobs: {regrade: {}}\n")  # the edit
    rc, report = cah.check_tree(root)
    assert rc == 1
    assert "a.yml" in report
    assert _sha(b"on: push\njobs: {regrade: {}}\n") in report   # fresh hash
    assert _sha(b"on: push\n") in report                        # committed hash
    assert "regenerate" in report.lower()
    assert "verdict.txt" in report          # all three artefacts named
    assert "gf180-parasynth.json" in report


def test_hand_edited_evidence_hash_is_red(tmp_path):
    root = _tree(tmp_path, {"a.yml": b"on: push\n", "b.yml": b"steps: []\n"})
    ev = _read_evidence(root)
    for c in ev["attestation"]["checks"]:
        if c.get("path") == "a.yml":
            c["content_hash"] = _sha(b"something else entirely")
    _write_evidence(root, ev)
    rc, report = cah.check_tree(root)
    assert rc == 1
    assert "a.yml" in report
    assert "something else" not in report    # it prints hashes, not contents
    assert _sha(b"on: push\n") in report     # the fresh (correct) hash shown


def test_evidence_dropping_a_check_is_red(tmp_path):
    """The hole a diff-shape check cannot see: the evidence silently stops
    covering a file the inventory still lists."""
    root = _tree(tmp_path, {"a.yml": b"on: push\n", "b.yml": b"steps: []\n"})
    ev = _read_evidence(root)
    ev["attestation"]["checks"] = [
        c for c in ev["attestation"]["checks"] if c.get("path") != "b.yml"]
    _write_evidence(root, ev)
    rc, report = cah.check_tree(root)
    assert rc == 1
    assert "b.yml" in report
    assert "no committed hash" in report.lower()


# =============================================================================
# The inventory self-hash coupling, and what must NOT be counted.
# =============================================================================
def test_edited_inventory_without_regenerated_evidence_is_red(tmp_path):
    root = _tree(tmp_path, {"a.yml": b"on: push\n", "b.yml": b"steps: []\n"})
    inv = root / "manifests" / "inventories" / "item-1.json"
    inv.write_text(inv.read_text().replace('"t1_item": 1', '"t1_item": 2'))
    rc, report = cah.check_tree(root)
    assert rc == 1
    assert "manifests/inventories/item-1.json" in report
    assert "provenance.input.content_hash" in report


def test_pathless_check_entries_are_not_hashed(tmp_path):
    """Item-9's cold_start / pdk / tools entries carry no path: `local_only`
    outcomes are carried, never re-run, and are not hash comparisons (#884)."""
    root = _tree(tmp_path, {"a.yml": b"on: push\n"})  # audit entry has no path
    rc, report = cah.check_tree(root)
    assert rc == 0
    assert "1 attested file" in report


# =============================================================================
# REFUSED (exit 2): a check that cannot run must not look like a pass.
# =============================================================================
def test_missing_evidence_file_is_refused(tmp_path):
    root = _tree(tmp_path, {"a.yml": b"on: push\n"})
    (root / "manifests" / "evidence" / "item-1.json").unlink()
    rc, report = cah.check_tree(root)
    assert rc == 2
    assert "item-1.json" in report


def test_malformed_evidence_json_is_refused(tmp_path):
    root = _tree(tmp_path, {"a.yml": b"on: push\n"})
    p = root / "manifests" / "evidence" / "item-1.json"
    p.write_text("{not json")
    rc, report = cah.check_tree(root)
    assert rc == 2
    assert "parse" in report.lower() or "malformed" in report.lower()


def test_evidence_without_provenance_hash_is_refused(tmp_path):
    root = _tree(tmp_path, {"a.yml": b"on: push\n"})
    ev = _read_evidence(root)
    del ev["provenance"]["input"]["content_hash"]
    _write_evidence(root, ev)
    rc, report = cah.check_tree(root)
    assert rc == 2


def test_no_inventories_at_all_is_refused(tmp_path):
    root = tmp_path / "empty"
    (root / "manifests" / "inventories").mkdir(parents=True)
    rc, report = cah.check_tree(root)
    assert rc == 2
    assert "refused" in report.lower()
    assert "item-*.json" in report


def test_missing_attested_file_is_drift_with_absent_hash(tmp_path):
    """A deleted-but-not-regenerated attested file is measurable drift (the
    committed hash is still a fact), not an instrument failure."""
    root = _tree(tmp_path, {"a.yml": b"on: push\n"})
    (root / "a.yml").unlink()
    rc, report = cah.check_tree(root)
    assert rc == 1
    assert "absent" in report.lower()


# =============================================================================
# The gate verify-fast actually runs: the LIVE tree must stay green. Editing
# any attested path without regenerating its evidence turns this red.
# =============================================================================
def test_current_repo_is_green():
    rc, report = cah.check_tree(REPO)
    assert rc == 0, report


def test_recipe_is_the_signoff_header_recipe(tmp_path):
    """The three artefacts and the pinned klt invocation, as the
    .github/workflows/signoff.yml header states them."""
    assert "klayout-tools==0.8.0" in cah.RECIPE
    assert "verdict.txt" in cah.RECIPE
    assert "evidence." in cah.RECIPE and "content_hash" in cah.RECIPE
