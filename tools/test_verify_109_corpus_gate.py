"""#814's measured rows, as regression tests on verify_109_claims.py's half 2.

Half 2 of that probe is the only place in any automated rung where
`balance_trajectory_db` meets the real Fischer RS.WAV/CB.WAV. On the tree this
test landed on, three corpus states produced three outcomes, of which two were
dishonest:

- **corpus absent, not required** (row 2): exit 0 and the verdict line
  `All checks passed.` printed over a half that did not run -- 6 of 8 checks
  emitted, the other two vanished under a global pass. The runner is
  explicitly not going to scrape output to catch this
  (`tools/run_all.py` line 22 names the shape as a verifier defect), so the
  verifier itself must print a verdict line that counts.
- **corpus absent, GF180_REQUIRE_TR808_REFS=1**: the same skip and exit 0. A
  required gate does not pass by skipping (`tools/test_run_case.py`
  `fischer_refs`, the policy sentence verbatim).
- **corpus root present, WAVs absent** (row 3): an uncaught
  `preparation_contract.Refused` traceback, exit 1 -- a *mismatch* reported
  where the truth is *did not run*. `run_all.py` records that as FAIL; REFUSED
  is exit 2 / NO VERDICT by convention (`docs/capture-r1.md`).

The fixed probe must give: counted verdict lines in every state, exit 0 with
`NOT RUN` named per skipped check when the corpus is optional, exit 2 naming
the missing file when it is required, and no traceback from any partial or
corrupt corpus.

Guard-defeating inputs (docs/verification-rules.md rule 8), measured on this
tree, not assumed:

- a corpus whose RS.WAV/CB.WAV are **zero-length**: `scipy.io.wavfile.read`
  raises `ValueError` -- *not* `Refused` -- so a Refused-only catch would
  traceback again. The probe must route the read failure to NO VERDICT too.
- a corpus **truncated inside the data chunk** (valid header): reads without
  error, then `rc.prepare` raises `Refused` (no pre-onset lead) -- covered by
  the Refused catch. The test pins that path so a future `prepare` change
  cannot silently turn it back into a crash.
"""
from __future__ import annotations

import io
import os
import pathlib
import re
import subprocess
import sys
import wave

import pytest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "model"))

import run_case as rc  # noqa: E402

PROBE = HERE / "probes" / "verify_109_claims.py"


def run_probe(refs: pathlib.Path, required: bool) -> subprocess.CompletedProcess:
    """Run the probe against a controlled corpus location.

    The variable's NAME comes from run_case (the one place it is decided,
    #522); the alias is unset because an inherited alias would disagree with
    the controlled location rather than inherit it."""
    env = dict(os.environ)
    env[rc.REFS_ENV] = str(refs)
    env.pop(rc.REFS_ALIAS_ENV, None)
    if required:
        env[rc.REFS_REQUIRED_ENV] = "1"
    else:
        env.pop(rc.REFS_REQUIRED_ENV, None)
    return subprocess.run([sys.executable, str(PROBE)], env=env,
                          capture_output=True, text=True, timeout=300)


def write_wavs(root: pathlib.Path, frames: int = 5000, drop_last: int = 0,
               zero: bool = False) -> None:
    """Lay down the two WAVs half 2 loads, at the paths run_case's own
    REF_MAIN names -- the precondition under test must not know the layout
    more exactly than the loader it guards."""
    for voice in ("RS", "CB"):
        rel = rc.REF_MAIN[voice][0]
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if zero:
            path.write_bytes(b"")
            continue
        buf = io.BytesIO()
        wav = wave.open(buf, "wb")
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(44100)
        wav.writeframes(b"\x00\x01" * frames)
        wav.close()
        blob = buf.getvalue()
        path.write_bytes(blob[: len(blob) - drop_last])


VERDICT = re.compile(r"^\s*(\d+) of (\d+) checks passed(?:; (\d+) NOT RUN)?")


def parse_verdict(out: str) -> "re.Match[str]":
    lines = [l for l in out.splitlines() if "checks passed" in l]
    assert len(lines) == 1, f"expected exactly one verdict line, got {lines}"
    m = VERDICT.match(lines[0])
    assert m, f"verdict line does not match the counted format: {lines[0]!r}"
    return m


def test_corpus_absent_optional_exits_0_and_counts_the_not_run(tmp_path):
    """Row 2: optional corpus absent -- exit stays 0 (the deliberate decision,
    Makefile:130-134), but the verdict line must count what did not run and
    must never say all checks passed over a half that emitted nothing."""
    refs = tmp_path / "refs"
    refs.mkdir()
    p = run_probe(refs, required=False)
    assert p.returncode == 0, f"exit {p.returncode}\n{p.stdout}\n{p.stderr}"
    assert "All checks passed." not in p.stdout
    assert "Traceback" not in p.stderr, p.stderr
    m = parse_verdict(p.stdout)
    ok, total, not_run = (int(m.group(i)) for i in (1, 2, 3))
    assert not_run == 2, f"expected the two reference checks NOT RUN, got {not_run}"
    assert total == ok + not_run
    assert ok == len(re.findall(r"^      OK  ", p.stdout, re.M)), \
        f"verdict says {ok} passed but {len(re.findall(r'^      OK  ', p.stdout, re.M))} OK lines emitted"
    assert "FAIL" not in p.stdout


def test_corpus_root_absent_required_exits_2(tmp_path):
    """Row 2 under GF180_REQUIRE_TR808_REFS=1: a required gate does not pass
    by skipping -- it refuses, exit 2, naming where the corpus should be."""
    refs = tmp_path / "refs"          # never created: the root itself is absent
    p = run_probe(refs, required=True)
    assert p.returncode == 2, f"exit {p.returncode}\n{p.stdout}\n{p.stderr}"
    assert "NO VERDICT" in p.stdout
    assert rc.REFS_ENV in p.stdout or str(refs) in p.stdout
    assert "Traceback" not in p.stderr, p.stderr


def test_corpus_partial_required_exits_2_not_1(tmp_path):
    """Row 3: corpus root present, WAVs absent. The truth is *did not run*;
    it must never again be an uncaught Refused traceback on exit 1."""
    refs = tmp_path / "refs"
    refs.mkdir()
    p = run_probe(refs, required=True)
    assert p.returncode == 2, f"exit {p.returncode}\n{p.stdout}\n{p.stderr}"
    assert "NO VERDICT" in p.stdout
    # the missing recording is named, so the operator knows what to fetch
    assert rc.REF_MAIN["RS"][0] in p.stdout
    assert "Traceback" not in p.stderr, p.stderr


def test_corpus_partial_optional_exits_0_and_counts_the_not_run(tmp_path):
    """Row 3's optional twin: same state, no required gate -- exit 0 with the
    two reference checks counted as NOT RUN, not a traceback."""
    refs = tmp_path / "refs"
    refs.mkdir()
    p = run_probe(refs, required=False)
    assert p.returncode == 0, f"exit {p.returncode}\n{p.stdout}\n{p.stderr}"
    assert "Traceback" not in p.stderr, p.stderr
    m = parse_verdict(p.stdout)
    ok, total, not_run = (int(m.group(i)) for i in (1, 2, 3))
    assert not_run == 2
    assert total == ok + not_run


def test_zero_length_wavs_required_exits_2_not_1(tmp_path):
    """Guard-defeating input for the fixed precondition: the two WAVs EXIST
    but are zero-length, so a root-only or exists-only check passes them
    straight to the loader. Measured on this tree: scipy raises ValueError,
    not Refused, so the probe must route the read failure to NO VERDICT
    itself."""
    refs = tmp_path / "refs"
    write_wavs(refs, zero=True)
    p = run_probe(refs, required=True)
    assert p.returncode == 2, f"exit {p.returncode}\n{p.stdout}\n{p.stderr}"
    assert "NO VERDICT" in p.stdout
    assert rc.REF_MAIN["RS"][0] in p.stdout
    assert "Traceback" not in p.stderr, p.stderr


def test_zero_length_wavs_optional_exits_0_and_counts_the_not_run(tmp_path):
    refs = tmp_path / "refs"
    write_wavs(refs, zero=True)
    p = run_probe(refs, required=False)
    assert p.returncode == 0, f"exit {p.returncode}\n{p.stdout}\n{p.stderr}"
    assert "Traceback" not in p.stderr, p.stderr
    m = parse_verdict(p.stdout)
    assert int(m.group(3)) == 2


def test_truncated_wavs_required_exits_2_not_1(tmp_path):
    """Truncated INSIDE the data chunk (valid header): reads without error,
    then rc.prepare raises Refused -- no pre-onset lead. The Refused catch
    must turn that into NO VERDICT, not a crash; if a future prepare change
    makes it measurable instead, this test says so loudly rather than
    pinning the mechanism."""
    refs = tmp_path / "refs"
    write_wavs(refs, frames=5000, drop_last=900)
    p = run_probe(refs, required=True)
    assert p.returncode in (1, 2), f"exit {p.returncode}\n{p.stdout}\n{p.stderr}"
    assert "Traceback" not in p.stderr, p.stderr
    if p.returncode == 2:
        assert "NO VERDICT" in p.stdout


def test_full_corpus_still_measured(tmp_path):
    """A well-formed corpus must still be MEASURED, not refused: each voice
    emits a per-check result line and the verdict counts both as run. The
    fixtures are synthetic (each voice's own nominal two-mode pair, not
    Fischer audio), so this pins the plumbing -- load, prepare, measure,
    count -- and whatever the estimator says about synthetic tones is a
    verdict, not the test's concern."""
    import math

    import numpy as np

    refs = tmp_path / "refs"
    sr = 44100
    partials = {"RS": (455.0, 1786.0), "CB": (540.0, 800.0)}
    for voice in ("RS", "CB"):
        f1, f2 = partials[voice]
        rel = rc.REF_MAIN[voice][0]
        path = refs / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        t = np.arange(int(0.30 * sr)) / sr
        x = 0.6 * np.sin(2 * math.pi * f1 * t) * np.exp(-t / 0.006) \
            + 0.3 * np.sin(2 * math.pi * f2 * t) * np.exp(-t / 0.0015)
        lead = np.zeros(int(0.02 * sr))
        x = np.concatenate([lead, x])
        pcm = (x / np.abs(x).max() * 30000).astype("<i2")
        buf = io.BytesIO()
        wav = wave.open(buf, "wb")
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sr)
        wav.writeframes(pcm.tobytes())
        wav.close()
        path.write_bytes(buf.getvalue())
    p = run_probe(refs, required=True)
    assert p.returncode in (0, 1), f"exit {p.returncode}\n{p.stdout}\n{p.stderr}"
    assert "Traceback" not in p.stderr, p.stderr
    assert "NO VERDICT" not in p.stdout
    assert "NOT RUN" not in p.stdout
    m = parse_verdict(p.stdout)
    ok, total, not_run = (int(m.group(i)) if m.group(i) else 0 for i in (1, 2, 3))
    assert not_run == 0
    n_fail_lines = len(re.findall(r"^      FAIL", p.stdout, re.M))
    n_ok_lines = len(re.findall(r"^      OK  ", p.stdout, re.M))
    assert total == n_ok_lines + n_fail_lines
    assert ok == n_ok_lines
    out = p.stdout
    for voice in ("RS", "CB"):
        rel = rc.REF_MAIN[voice][0]
        assert f"{voice} {rel}" in out, f"voice {voice} produced no result line:\n{out}"
    assert "SKIPPED" not in out


@pytest.mark.parametrize("required", [False, True])
def test_no_corpus_state_leaves_a_traceback(required, tmp_path):
    """The umbrella property #814 exists for: no state of the corpus -- absent,
    partial, empty files, truncated files -- may produce an uncaught
    traceback. Exit 1 is reserved for a measured mismatch."""
    refs = tmp_path / "refs"
    refs.mkdir()
    (refs / "rs8").mkdir(parents=True, exist_ok=True)
    (refs / "rs8" / "RS.WAV").write_bytes(b"")
    (refs / "cb8").mkdir(parents=True, exist_ok=True)
    # CB.WAV deliberately absent: the mixed partial corpus
    p = run_probe(refs, required=required)
    assert "Traceback" not in p.stderr, p.stderr
    if required:
        assert p.returncode == 2
    else:
        assert p.returncode == 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
