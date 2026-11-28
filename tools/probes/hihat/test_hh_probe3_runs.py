"""hh_probe3's CY T20 guard is live, and no render length satisfies it (#1965).

#1965 proposed rendering CY for 3.0 s instead of 2.0 s so that
`schroeder_t20(...).require(...)` would answer rather than refuse. Measured on
`origin/main` @ `b2762026` (Python 3.12.3, numpy 2.5.5, scipy 1.18.1), that
does not work, and no render length does:

    shipped CY, rendered 5.0 s    last nonzero sample at 2622.8 ms (+-1 LSB,
                                  -84.1 dB from a peak of 16052); exact zero
                                  for the remaining 2.4 s
    Schroeder -25 dB point        927 ms
    tail after -25 dB             1695.6 ms = 1.876 T20   (T20 fit 903.6 ms)
    MIN_TAIL_T20 x T20            1807.2 ms

The fixed-point block's CY output reaches its one-LSB floor 111.6 ms before the
guard's 2 x T20. #139 made the estimator read the record's SOUNDING extent, so
a longer render only appends the silence it already ignores: 3.0, 3.5 and 5.0 s
all return the same `after_hi_ms` and `needed_after_hi_ms`. The retuned kit
(`hh_probe3.retuned_cy()`) is short by 275.6 ms. The refusal is therefore a
property of the block, not of the render, and it is correct; `MIN_TAIL_T20`
is untouched.

What this file pins, each with the control that defeats it:
  * the guard is live at the shipped 2.0 s (the issue's rule-8 control);
  * the recommended 3.0 s is refused too, and `trailing_silence_ms > 0` shows
    the SOUND ended before the array did -- so no longer render can change it;
  * the probes' output path is under build/ (gitignored), is created on
    demand, and REFUSES by name when it cannot be created or written;
  * no host-specific scratch path remains under tools/probes/hihat/;
  * hh_probe4 propagates a refused T20 instead of writing None and crashing.

The two render tests take about 4 s and 12 s; hh_probe4's about 60 s (16
renders, the same work the probe itself does).
"""
from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import hh_probe                                   # noqa: E402  (sets sys.path)
import hh_probe2, hh_probe3, hh_probe4            # noqa: E402
import audio_measure as am                        # noqa: E402
import drums_fx as dx                             # noqa: E402
from dsp import SR                                # noqa: E402
from hh_probe import render                       # noqa: E402

ROOT = hh_probe._ROOT
# split literals: this file must not itself contain the strings it forbids
SCRATCH_RE = re.compile("/private/" "tmp|claude-" "501")
REFUSAL = "the record ends before the decay does"


# ------------------------------------------------------------ output path
def test_output_dir_is_under_build_and_gitignored():
    d = hh_probe.out_dir()
    assert d == ROOT / "build" / "hihat-probes"
    assert d.is_dir()
    r = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", str(d / "x.json")],
                       check=False)
    assert r.returncode == 0, f"{d} is not ignored; build/hihat-probes/*.json would be staged"


def test_output_dir_is_created_on_demand(tmp_path, monkeypatch):
    root = tmp_path / "wt"
    root.mkdir()
    assert not (root / "build").exists()
    monkeypatch.setattr(hh_probe, "_ROOT", root)
    d = hh_probe.out_dir()
    assert d == root / "build" / "hihat-probes" and d.is_dir()


def test_output_dir_refuses_by_name_when_it_cannot_be_created(tmp_path, monkeypatch):
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "hihat-probes").write_text("a file where the directory must go")
    monkeypatch.setattr(hh_probe, "_ROOT", tmp_path)
    with pytest.raises(SystemExit, match=r"^REFUSED: cannot create the output directory"):
        hh_probe.out_dir()


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes into a 0o500 directory")
def test_output_dir_refuses_by_name_when_not_writable(tmp_path, monkeypatch):
    d = tmp_path / "build" / "hihat-probes"
    d.mkdir(parents=True)
    d.chmod(0o500)
    monkeypatch.setattr(hh_probe, "_ROOT", tmp_path)
    try:
        with pytest.raises(SystemExit, match=r"^REFUSED: the output directory .* is not writable"):
            hh_probe.out_dir()
    finally:
        d.chmod(0o700)


def test_probes_1_to_4_share_one_output_dir_and_no_scratch():
    for m in (hh_probe, hh_probe2, hh_probe3, hh_probe4):
        assert m.out_dir is hh_probe.out_dir, m.__name__
        assert not hasattr(m, "SCRATCH"), m.__name__


def test_no_host_specific_scratch_path_remains_under_hihat():
    offenders = {p.name: SCRATCH_RE.search(p.read_text()).group(0)
                 for p in HERE.glob("*.py") if SCRATCH_RE.search(p.read_text())}
    assert offenders == {}, offenders
    # the control: the pattern catches the constant the probes shipped with
    assert SCRATCH_RE.search('SCRATCH = ("/private/' 'tmp/claude-' '501/-Users-') is not None


# ------------------------------------------------------ the T20 tail guard
def test_control_a_render_shorter_than_min_tail_t20_is_refused():
    _, x = render(dx.kit_with_sounds("CY"), 2.0)
    e = am.schroeder_t20(x, SR)
    assert not e.ok and e.reason.startswith(REFUSAL), (e.ok, e.reason)
    assert e.detail["tail_in_t20s"] < am.MIN_TAIL_T20
    with pytest.raises(am.InsufficientEvidence, match=REFUSAL):
        e.require("CY T20 shipped")


@pytest.mark.parametrize("name,kit", [
    ("shipped", dx.kit_with_sounds("CY")),
    ("retuned", hh_probe3.retuned_cy()),
])
def test_the_recommended_3s_render_is_refused_because_the_sound_ends(name, kit):
    _, x = render(kit, 3.0)
    e = am.schroeder_t20(x, SR)
    assert not e.ok and e.reason.startswith(REFUSAL), (name, e.ok, e.reason)
    d = e.detail
    # the render outlasted the sound, so the shortfall is the block's, and a
    # longer render can only add the trailing silence #139 already ignores
    assert d["trailing_silence_ms"] > 0, (name, d)
    assert d["tail_in_t20s"] < am.MIN_TAIL_T20, (name, d)
    assert d["after_hi_ms"] < d["needed_after_hi_ms"], (name, d)


# ------------------------------------------------------------- hh_probe4
def test_hh_probe4_propagates_a_refused_t20_instead_of_writing_none(capsys):
    # Before: every row printed `T20   nan ms`, `best` held None, and the
    # probe died after its 16 renders with
    # `TypeError: unsupported operand type(s) for *: 'NoneType' and 'float'`.
    with pytest.raises(am.InsufficientEvidence, match=REFUSAL):
        hh_probe4.main()
    out = capsys.readouterr().out
    assert "nan ms" not in out, out
    assert "T20 REFUSED: " + REFUSAL in out, out
    assert "TypeError" not in out
