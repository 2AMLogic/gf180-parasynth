"""Every named-property figure quoted in prose, re-derived from the tool (#429).

WHY THIS FILE EXISTS. Step 10 (#369, PR #428) shipped three figures attributed
by name to properties of `tools/cymbal_tone_nodal.py`, and all three disagreed
with what that tool returns -- `nodal-grounded` written as 0.10 dB against a
computed 0.026, `fig9-window-blind` as 5.62 against 4.61, `one-register-set` as
0.00 against 0.171. The tool was byte-identical across both commits of that PR,
so these were never its output: they were transcribed, and prose does not
recompute. Every one erred in the self-flattering direction, and one of them sat
in `docs/tr808-reference.md` under a `[measured: ...]` provenance tag -- the
project's external-reference document stating a measurement its cited tool does
not make.

`tools/check_doc_claims.py` reported 44/44 ok while that was true, because no
marker covered any of the three lines. This file is the evidence those markers
now cite.

WHAT IT CHECKS, and what it deliberately does not. Each row below names a
document, a regex that must match EXACTLY ONCE, and the tool value the captured
number must equal when rounded to however many decimals the document prints. A
row that stops matching is a failure, not a skip: a quoted figure whose sentence
was reworded away is exactly as unchecked as one that was never marked, and the
whole point of this file is that "unchecked" must not look like "ok".

It does NOT check that the surrounding prose means what it says -- only that
every number presented as this tool's output is this tool's output.
"""
from __future__ import annotations

import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "model"), str(ROOT / "tools")]
import cymbal_tone_nodal as tn           # noqa: E402

README = ROOT / "docs" / "scorecard" / "cymbal-369" / "tone-render" / "README.md"
REFERENCE = ROOT / "docs" / "tr808-reference.md"
MODEL = ROOT / "model" / "cymbal_candidate.py"

_PROPS = None


def props() -> dict:
    global _PROPS
    if _PROPS is None:
        _PROPS = tn.properties(tn.config())
    return _PROPS


def flat(path: pathlib.Path) -> str:
    """The document as one line. Every site below is quoted across a line break
    in at least one of the three files, and a regex that has to know where the
    wrap falls would break on a reflow rather than on a wrong number."""
    return re.sub(r"\s+", " ", path.read_text())


def _decimals(s: str) -> int:
    return len(s.split(".")[1]) if "." in s else 0


# (document, the quantity, regex matched against the flattened document, a
# callable returning the tool value(s) the capture group(s) must equal)
SITES = [
    (README, "nodal-grounded: the cross-route agreement that grounds the target",
     r"the two routes agree to \*\*([\d.]+) dB\*\* \(`nodal-grounded`",
     lambda p: [p["nodal-grounded"]["value_db"]]),
    (README, "fig9-window-blind: the low-band disagreement that IS the finding",
     r"they disagree by \*\*([\d.]+) dB\*\* \(`fig9-window-blind`",
     lambda p: [p["fig9-window-blind"]["value_db"]]),
    (README, "fig9-window-blind.span_db: peak-to-peak over the same range",
     r"the two routes span \*\*([\d.]+) dB\*\* \(`fig9-window-blind\.span_db`\)",
     lambda p: [p["fig9-window-blind"]["span_db"]]),
    (README, "one-register-set: what fixing the poles at the anchor forgoes, and its bound",
     r"\*\*`one-register-set`: ([\d.]+) dB, against a ([\d.]+) dB bound\.\*\*",
     lambda p: [p["one-register-set"]["value_db"], p["one-register-set"]["bound_db"]]),
    (README, "one-register-set: the low band's fixed and tracking shape errors",
     r"the low band reads ([\d.]+) dB with its pole fixed against ([\d.]+) dB tracking",
     lambda p: [p["one-register-set"]["per_band"]["low"]["fixed_db"],
                p["one-register-set"]["per_band"]["low"]["tracking_db"]]),
    (README, "top_pole_hz: how far the top pole moves over the reachable codes",
     r"top pole moves \*\*([\d.]+) → ([\d.]+) Hz\*\* across the five TONE codes",
     lambda p: p["one-register-set"]["top_pole_hz"]["codes_hz"]),
    (REFERENCE, "nodal-grounded: the same agreement, in the external-reference document",
     r"the two routes agree to \*\*([\d.]+) dB\*\*",
     lambda p: [p["nodal-grounded"]["value_db"]]),
    (MODEL, "nodal-grounded: in the shipped model's own docstring",
     r"agree there to ([\d.]+) dB over its active range",
     lambda p: [p["nodal-grounded"]["value_db"]]),
    (MODEL, "fig9-window-blind: in the shipped model's own docstring",
     r"The low band's disagreement is ([\d.]+) dB",
     lambda p: [p["fig9-window-blind"]["value_db"]]),
    (MODEL, "one-register-set: the figure that justifies not realising the shape change",
     r"`one-register-set` property measures at ([\d.]+) dB",
     lambda p: [p["one-register-set"]["value_db"]]),
    (MODEL, "top_pole_hz: the pole movement quoted beside it",
     r"top pole moves ([\d.]+) -> ([\d.]+) Hz across the five TONE codes",
     lambda p: p["one-register-set"]["top_pole_hz"]["codes_hz"]),
]


def _site_failures(sites=None) -> list[str]:
    bad, p = [], props()
    for doc, what, pattern, want in (SITES if sites is None else sites):
        rel = doc.relative_to(ROOT) if doc.is_relative_to(ROOT) else doc
        hits = re.findall(pattern, flat(doc))
        if len(hits) != 1:
            bad.append(f"{rel}: expected exactly one site for {what}; found "
                       f"{len(hits)}. A site that moved is UNCHECKED, which is "
                       f"what this file exists to stop.")
            continue
        got = hits[0] if isinstance(hits[0], tuple) else (hits[0],)
        expect = want(p)
        if len(got) != len(expect):
            bad.append(f"{rel}: {what} captured {got}, expected {len(expect)} value(s)")
            continue
        for text, value in zip(got, expect):
            # the document is held to its own printed precision, so 5.59 backs a
            # computed 5.592 and 5.62 does not
            if abs(float(text) - value) > 0.5 * 10 ** -_decimals(text):
                bad.append(f"{rel}: says {text} for {what}; "
                           f"cymbal_tone_nodal.py computes {value}")
    return bad


def test_every_quoted_figure_is_the_tool_s_own_output():
    """One test, not twelve parametrised ones, because `check_doc_claims.py`
    resolves a claim marker's nodeid against pytest's collection and maps it
    back through junit-xml by name: a parametrised nodeid cited without its
    parameter is REFUSED, and a marker that cannot be evaluated is exactly the
    silent gap this file was written to close."""
    bad = _site_failures()
    assert not bad, "\n".join(bad)


def test_the_exact_figure_this_issue_was_filed_about_is_caught(tmp_path):
    """The injected-defect control. Restate #429's own defect -- the write-up
    quoting 0.10 dB for a property that computes 0.026 -- in a scratch document
    and require the checker to turn red on it. Without this, a regex that
    silently stopped comparing would look exactly like a document that is
    right."""
    doc = tmp_path / "wrong.md"
    doc.write_text("the two routes agree to **0.10 dB** (`nodal-grounded`, bound 0.5)\n")
    row = (doc, "nodal-grounded: the defect #429 reported",
           r"the two routes agree to \*\*([\d.]+) dB\*\* \(`nodal-grounded`",
           lambda p: [p["nodal-grounded"]["value_db"]])
    bad = _site_failures([row])
    assert len(bad) == 1 and "0.10" in bad[0] and "0.026" in bad[0], bad

    doc.write_text("the two routes agree to **0.026 dB** (`nodal-grounded`, bound 0.5)\n")
    assert _site_failures([row]) == []

    # ...and a site that was reworded away is a failure, not a pass
    doc.write_text("the two routes agree closely enough\n")
    assert len(_site_failures([row])) == 1


def test_the_span_and_the_maximum_are_different_numbers():
    """The control for the row above: if `span_db` happened to equal `value_db`,
    two of the site checks would pass for the wrong reason and the distinction
    the write-up got wrong would be untested."""
    p = props()["fig9-window-blind"]
    assert p["span_db"] > p["value_db"] + 0.5, p


def test_the_reachable_codes_and_the_ideal_rotation_are_different_numbers():
    """And the control for the pole-movement rows. The write-up's 4712 Hz is the
    top pole at TONE 100, where the wiper law clamps alpha to 0.999; the ideal
    alpha = 1 gives 4715.1 Hz and no TONE code selects it. Both are recorded so
    a document can say which it means -- which is only worth checking while the
    two really do differ."""
    t = props()["one-register-set"]["top_pole_hz"]
    assert t["codes_hz"][0] == pytest.approx(t["rotation_hz"][0], abs=0.2)
    assert t["rotation_hz"][1] - t["codes_hz"][1] > 1.0, t


def test_every_property_named_in_prose_still_exists_in_the_tool():
    """A renamed property would make every regex above match nothing, which the
    per-site assertion catches -- but it would catch it as twelve failures with
    no statement of the cause. This is that statement."""
    for name in ("nodal-grounded", "fig9-window-blind", "one-register-set"):
        assert name in props(), name
