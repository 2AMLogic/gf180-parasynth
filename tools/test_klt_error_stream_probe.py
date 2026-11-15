"""Tests for the klt error-stream probe (issue #765).

These run without klt. What they pin is `classify`, the pure function that
turns two raw streams into the claim `docs/tool-findings.md` makes -- including
the case the probe exists to distinguish, where the envelope is well-formed but
on the stream the caller is not capturing.
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import klt_error_stream_probe as probe  # noqa: E402

ENVELOPE = json.dumps(
    {"schema_version": 1, "error": {"command": "synthesize", "message": "boom"}}
).encode()


def test_an_envelope_on_stderr_leaves_a_zero_byte_redirect_artifact():
    result = probe.classify(1, b"", ENVELOPE)
    assert result["envelope_stream"] == "stderr"
    assert result["stdout_bytes"] == 0
    # The number that matters to a caller doing `--format json > report.json`.
    assert result["redirect_artifact_bytes"] == 0
    assert result["message"] == "boom"


def test_an_envelope_on_stdout_is_reported_as_stdout():
    result = probe.classify(1, ENVELOPE, b"")
    assert result["envelope_stream"] == "stdout"
    assert result["redirect_artifact_bytes"] == len(ENVELOPE)


def test_a_plain_text_error_is_not_an_envelope():
    result = probe.classify(1, b"", b"klt synthesize: boom\n")
    assert result["envelope_stream"] is None
    assert result["message"] is None


def test_json_that_is_not_an_error_envelope_is_not_counted_as_one():
    # A success report on stdout is JSON too; it is not an error envelope.
    result = probe.classify(0, b'{"status": "ok"}', b"")
    assert result["envelope_stream"] is None


def test_undecodable_bytes_do_not_crash_the_classifier():
    result = probe.classify(1, b"\xff\xfe\x00", b"\xff\xfe\x00")
    assert result["envelope_stream"] is None
    assert result["stdout_bytes"] == 3


def test_every_probed_request_names_an_input_that_cannot_exist():
    # The probe's claim depends on the verb failing for the stated reason, so
    # each request must name a file no checkout can accidentally contain.
    for verb, request in probe.REQUESTS.items():
        names = request.get("sources", []) + (
            [request["netlist"]] if "netlist" in request else []
        )
        assert names, verb
        for name in names:
            assert name.startswith("no_such_"), (verb, name)


def test_the_probe_refuses_rather_than_reporting_without_klt(monkeypatch, capsys):
    monkeypatch.setattr(probe.shutil, "which", lambda name: None)
    assert probe.main(["--pdk-root", "/"]) == probe.EXIT_REFUSED
    assert "REFUSED" in capsys.readouterr().err


def test_the_probe_refuses_an_absent_pdk_root(monkeypatch, capsys):
    monkeypatch.setattr(probe.shutil, "which", lambda name: "/usr/bin/klt")
    assert probe.main(["--pdk-root", "/nonexistent-pdk-root"]) == probe.EXIT_REFUSED
    assert "REFUSED" in capsys.readouterr().err
