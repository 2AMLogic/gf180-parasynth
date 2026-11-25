"""Re-derives the 17/7 per-site via-pad gap split quoted in docs/tool-findings.md (#1023).

The split is a distribution over the 24 site entries of
``def_site_analysis.json``, so no single-line ``grep=`` marker can bind it.
The per-site minimum is the smallest ``gap_dbu`` over ``origin == "via_pad"``
entries of ``power_shapes_in_window`` (the record sorts ascending before its
truncation, so the minimum is exact).
"""
import json
from collections import Counter
from pathlib import Path

RECORD = Path(__file__).with_name("def_site_analysis.json")


def per_site_min_via_pad_gaps(record):
    gaps = []
    for site in record["sites"]:
        pads = [s["gap_dbu"] for s in site["power_shapes_in_window"]
                if s.get("origin") == "via_pad"]
        assert pads, "a site with no via_pad entry cannot be classified; refuse"
        gaps.append(min(pads))
    return gaps


def test_split_is_17_close_and_7_at_1168():
    record = json.loads(RECORD.read_text())
    gaps = per_site_min_via_pad_gaps(record)
    assert len(gaps) == 24
    assert sum(1 for g in gaps if g <= 48) == 17
    assert sum(1 for g in gaps if g == 1168) == 7
    assert Counter(gaps) == {0: 6, 48: 11, 1168: 7}


def test_CONTROL_a_swapped_site_changes_the_split():
    record = json.loads(RECORD.read_text())
    for s in record["sites"][0]["power_shapes_in_window"]:
        if s.get("origin") == "via_pad":
            s["gap_dbu"] = 1168
    gaps = per_site_min_via_pad_gaps(record)
    assert sum(1 for g in gaps if g <= 48) != 17
