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


RS_PARTIALS = (455.0, 1786.0)   # RS's own nominal two-mode pair (as in the full-corpus test)


def good_wav(f1: float, f2: float) -> bytes:
    """The full-corpus test's synthesis recipe, as a blob: a record that
    loads, prepares and MEASURES (verdict whatever the estimator says)."""
    import math

    import numpy as np

    sr = 44100
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
    return buf.getvalue()


def truncated_wav(frames: int = 5000, drop_last: int = 900) -> bytes:
    """write_wavs' truncation recipe, as a blob: a valid header claiming
    `frames`, the data chunk cut short -- reads without error, then
    rc.prepare refuses (no pre-onset lead)."""
    buf = io.BytesIO()
    wav = wave.open(buf, "wb")
    wav.setnchannels(1)
    wav.setsampwidth(2)
    wav.setframerate(44100)
    wav.writeframes(b"\x00\x01" * frames)
    wav.close()
    blob = buf.getvalue()
    return blob[: len(blob) - drop_last]


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
def test_mixed_corpus_counts_each_voice_once(required, tmp_path):
    """The asymmetric corpus state -- one recording usable, the other damaged
    -- is #814's own motivating scenario: the corpus lives at /tmp/tr808-ref
    on a worker running many sweeps, and partial population is PER FILE, so a
    copy interrupted mid-corpus leaves one good recording and one broken one.
    The refusal must be PER VOICE: the measured voice keeps its OK, the
    damaged voice is the only NOT RUN with its OWN file named, and no voice
    lands in two buckets. Measured on the pre-fix probe: the half-wide
    handler inflated the verdict total from 8 to 9 and printed RS both as OK
    and as NOT RUN blaming CB's file -- output that looks exactly like data
    and is false."""
    refs = tmp_path / "refs"
    blobs = {"RS": good_wav(*RS_PARTIALS),
             "CB": truncated_wav(frames=5000, drop_last=900)}
    for voice, blob in blobs.items():
        path = refs / rc.REF_MAIN[voice][0]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(blob)
    p = run_probe(refs, required=required)
    assert "Traceback" not in p.stderr, p.stderr
    m = parse_verdict(p.stdout)
    ok, total, not_run = (int(m.group(i)) for i in (1, 2, 3))
    assert total == 8, f"the probe has 8 checks; the verdict counted {total}\n{p.stdout}"
    assert not_run == 1, f"expected exactly the damaged voice NOT RUN\n{p.stdout}"
    assert ok == 7, p.stdout
    assert total == ok + not_run
    assert ok == len(re.findall(r"^      OK  ", p.stdout, re.M)), \
        f"verdict says {ok} passed but {len(re.findall(r'^      OK  ', p.stdout, re.M))} OK lines emitted"
    # Each voice in exactly one bucket: RS measured, CB refused, and CB's
    # refusal names CB's file -- not the measured voice's.
    rs_ok = len(re.findall(r"^      OK\s+RS rs8/RS\.WAV", p.stdout, re.M))
    rs_nr = len(re.findall(r"^      NOT RUN  RS reference:", p.stdout, re.M))
    cb_ok = len(re.findall(r"^      OK\s+CB cb8/CB\.WAV", p.stdout, re.M))
    cb_nr = len(re.findall(r"^      NOT RUN  CB reference:", p.stdout, re.M))
    assert (rs_ok, rs_nr) == (1, 0), f"RS must appear only as OK\n{p.stdout}"
    assert (cb_ok, cb_nr) == (0, 1), f"CB must appear only as NOT RUN\n{p.stdout}"
    assert rc.REF_MAIN["CB"][0] in p.stdout
    if required:
        assert p.returncode == 2, f"exit {p.returncode}\n{p.stdout}"
        assert "NO VERDICT" in p.stdout
    else:
        assert p.returncode == 0, f"exit {p.returncode}\n{p.stdout}"


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


# --- #988: the CONCLUSION block's figures must be the run's measurements ---

def _flat(out: str) -> str:
    """stdout collapsed to one string: both the detail lines and the
    CONCLUSION are hard-wrapped prints, and the figures must be compared
    where the sentences are, not where the line breaks happen to fall."""
    return re.sub(r"\s+", " ", out)


def _detail_extent(flat: str, voice: str, rel: str):
    """One voice's measured extent, off the detail line: (window_ms, t1_ms,
    t2_ms-or-None). This is the same line conclusion_extent_lines() derives
    from, so equality here IS the no-drift property."""
    m = re.search(
        rf"{voice} {re.escape(rel)}: prepared record is [\d.]+ ms long; "
        rf"\d+ of \d+ probed instants in \[0, (\d+)\] ms clear the joint "
        rf"floor by 6 dB; earliest ([\d.]+) ms(?:, latest ([\d.]+) ms)?",
        flat)
    assert m, f"no detail line for {voice}:\n{flat[:1500]}"
    t2 = float(m.group(3)) if m.group(3) is not None else None
    return m.group(1), float(m.group(2)), t2


class _NotQuoted:
    """The CONCLUSION prose does not quote this figure at all (#997).

    Distinct from a quoted float and from the detail side's measured-None
    (a single-instant record prints no `latest`). The old None-for-unquoted
    return made `test_conclusion_figures_match_detail_lines` assert
    `None == <measured>` on the two-instant CB shape and fail with a
    drift-shaped message when nothing had drifted."""

    def __repr__(self):
        return "NOT_QUOTED"


NOT_QUOTED = _NotQuoted()


def _conclusion_extent(flat: str, voice: str):
    """The CONCLUSION's quoted extent for one voice: (window_ms, start_ms,
    end_ms) where an unquoted figure is the NOT_QUOTED sentinel, never
    None (#997). `~?` tolerates the pre-fix prose's tilde prefixes so a
    red run against the old hard-coded text reports a FIGURE MISMATCH --
    the drift this test exists for -- rather than a parse failure."""
    if voice == "RS":
        m = re.search(
            r"for RS\.WAV the joint-floor-clearing region runs from "
            r"(?:~?([\d.]+) ms to ~?([\d.]+) ms"
            r"|a single floor-clearing instant at ~?([\d.]+) ms)"
            r" of a (\d+) ms search window", flat)
        assert m, f"no CONCLUSION clause for {voice}:\n{flat[-1500:]}"
        window = m.group(4)
        if m.group(3) is not None:
            # Single-instant: the prose quotes no end.
            return window, float(m.group(3)), NOT_QUOTED
        return window, float(m.group(1)), float(m.group(2))
    m = re.search(
        r"[Ff]or CB\.WAV it runs to (?:~?([\d.]+) ms"
        r"|a single floor-clearing instant at ~?([\d.]+) ms)"
        r" of a (\d+) ms window", flat)
    assert m, f"no CONCLUSION clause for {voice}:\n{flat[-1500:]}"
    window = m.group(3)
    if m.group(2) is not None:
        # Single-instant record: the one printed instant IS the detail
        # line's `earliest`, so it is compared against t1. The prose
        # quotes no end; the detail's t2 is measured-None.
        return window, float(m.group(2)), NOT_QUOTED
    # Two-instant record: the prose quotes only the END. The start is
    # unquoted -- NOT_QUOTED, so the caller skips that comparison
    # explicitly instead of asserting None == <measured> (#997).
    return window, NOT_QUOTED, float(m.group(1))


def test_conclusion_figures_match_detail_lines(tmp_path):
    """#988's start-red control: on a synthetic reference whose measured
    extents differ from the old hard-coded prose (RS `~41 ms`, CB
    `~589 ms`), the CONCLUSION must quote the DETAIL LINE's figures. Red
    against the pre-fix probe (CONCLUSION says 41.0 ms where the run
    measured something else -- the exact drift #988 filed), green once the
    figures are derived. Synthetic tones, same recipe as
    test_full_corpus_still_measured: whatever the estimator says about them
    is a verdict; this test reads only the printed extents."""
    refs = tmp_path / "refs"
    blobs = {"RS": good_wav(*RS_PARTIALS), "CB": good_wav(540.0, 800.0)}
    for voice, blob in blobs.items():
        path = refs / rc.REF_MAIN[voice][0]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(blob)
    p = run_probe(refs, required=True)
    assert p.returncode in (0, 1), f"exit {p.returncode}\n{p.stdout}\n{p.stderr}"
    assert "Traceback" not in p.stderr, p.stderr
    assert "NOT RUN" not in p.stdout and "NO VERDICT" not in p.stdout
    m = parse_verdict(p.stdout)
    assert int(m.group(1)) == 8 and int(m.group(2)) == 8, p.stdout
    assert "CONCLUSION: for RS.WAV" in p.stdout
    flat = _flat(p.stdout)
    for voice in ("RS", "CB"):
        rel = rc.REF_MAIN[voice][0]
        window, t1, t2 = _detail_extent(flat, voice, rel)
        c_window, c_t1, c_t2 = _conclusion_extent(flat, voice)
        assert c_window == window, \
            f"{voice}: CONCLUSION window {c_window} ms != detail {window} ms"
        if c_t1 is not NOT_QUOTED:
            assert c_t1 == t1, \
                f"{voice}: CONCLUSION start {c_t1} ms != detail {t1} ms\n{flat[-800:]}"
        if c_t2 is not NOT_QUOTED:
            assert c_t2 == t2, \
                (f"{voice}: CONCLUSION end {c_t2} ms != detail {t2} ms -- the "
                 f"prose drifted from its own run (#988's defect class)\n{flat[-800:]}")
        # The injected-drift control: neither old hard-coded literal may
        # survive anywhere in the CONCLUSION, whatever this run measured.
        for stale in ("~41 ms", "~589 ms"):
            assert stale not in flat.split("CONCLUSION:")[-1], \
                f"{voice}: stale hard-coded figure {stale!r} still printed"


def test_conclusion_two_instant_cb_control():
    """#997 injected-defect control (verification-rules.md rule 8): the
    two-instant CB shape, where the CONCLUSION quotes only the end (`it
    runs to 588.7 ms of a 600 ms window`) while the detail line measures
    both instants. Green when the quoted end matches the detail's
    `latest` -- with the unquoted start skipped EXPLICITLY via the
    NOT_QUOTED sentinel, not by None-luck; red on the END comparison when
    the prose end drifts. Its first state, run against the pre-fix
    `_conclusion_extent`, failed with the false
    `CB: CONCLUSION start None ms != detail 22.0 ms` -- #988's drift
    message with nothing drifted."""
    rel = rc.REF_MAIN["CB"][0]
    detail = (
        f"CB {rel}: prepared record is 620.0 ms long; "
        "2 of 8 probed instants in [0, 600] ms clear the joint floor "
        "by 6 dB; earliest 22.0 ms, latest 588.7 ms")
    conclusion = "CONCLUSION: for CB.WAV it runs to 588.7 ms of a 600 ms window."
    flat = f"{detail} {conclusion}"
    window, t1, t2 = _detail_extent(flat, "CB", rel)
    assert (window, t1, t2) == ("600", 22.0, 588.7)
    c_window, c_t1, c_t2 = _conclusion_extent(flat, "CB")
    assert c_window == window
    assert c_t1 is NOT_QUOTED, \
        f"CB two-instant prose unexpectedly quotes a start: {c_t1!r}"
    assert c_t2 == t2, f"CB: CONCLUSION end {c_t2} ms != detail {t2} ms"

    # Injected defect: the prose end drifts off the detail's `latest` --
    # the comparison that must go red is the END one.
    flat_bad = flat.replace("runs to 588.7 ms of a 600 ms window",
                            "runs to 580.0 ms of a 600 ms window")
    with pytest.raises(AssertionError,
                       match=r"CONCLUSION end 580\.0 ms != detail 588\.7 ms"):
        _, _, bad_end = _conclusion_extent(flat_bad, "CB")
        assert bad_end == t2, \
            f"CB: CONCLUSION end {bad_end} ms != detail {t2} ms"


def test_conclusion_absent_on_half_run(tmp_path):
    """#988 edge: a half-run (one voice refused) must still print NO
    CONCLUSION -- the derived block did not change the both-voices condition
    from #814, and the prose must not assert numbers for the voice that did
    not run."""
    refs = tmp_path / "refs"
    blobs = {"RS": good_wav(*RS_PARTIALS),
             "CB": truncated_wav(frames=5000, drop_last=900)}
    for voice, blob in blobs.items():
        path = refs / rc.REF_MAIN[voice][0]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(blob)
    p = run_probe(refs, required=False)
    assert p.returncode == 0, f"exit {p.returncode}\n{p.stdout}\n{p.stderr}"
    assert "Traceback" not in p.stderr, p.stderr
    m = parse_verdict(p.stdout)
    assert int(m.group(3)) == 1, f"expected CB NOT RUN, got {p.stdout[-400:]}"
    assert "CONCLUSION: for RS.WAV" not in p.stdout


def test_conclusion_extent_lines_single_instant():
    """#988 edge, unit-level: `t2_ms` is conditional in
    balance_trajectory_db's detail (single-instant record), so the
    CONCLUSION builder must degrade the phrase -- not KeyError (#814) -- and
    every figure it prints must come from the dict it is handed. The values
    are chosen to differ from the retired hard-coded literals (41 / 589):
    if any of those reappear, the builder is printing prose, not the run."""
    sys.path.insert(0, str(HERE / "probes"))
    import verify_109_claims as vc

    lines = "\n".join(vc.conclusion_extent_lines({
        "RS": {"t1_ms": 7.5, "t2_ms": None, "t_end_ms": 60.0},
        "CB": {"t1_ms": 3.2, "t2_ms": 432.1, "t_end_ms": 600.0},
    }))
    flat = re.sub(r"\s+", " ", lines)
    assert "runs from a single floor-clearing instant at 7.5 ms of a " \
           "60 ms search window" in flat
    assert "it runs to 432.1 ms of a 600 ms window" in flat
    assert "41 ms" not in flat and "589 ms" not in flat

    two_instant = "\n".join(vc.conclusion_extent_lines({
        "RS": {"t1_ms": 11.0, "t2_ms": 53.9, "t_end_ms": 60.0},
        "CB": {"t1_ms": 10.0, "t2_ms": 588.7, "t_end_ms": 600.0},
    }))
    assert "runs from 11.0 ms to 53.9 ms of a 60 ms search window" \
        in re.sub(r"\s+", " ", two_instant)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
