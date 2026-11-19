#!/usr/bin/env python3
"""The commercial-reference suite registry (#1050, part of the voice-only
silicon ruling #1048).

The commercial reference for the voice becomes **Arturia Mini V4 (4.0.4.6366)**,
baselined as a NEW, separate suite. The existing Mini V3 records are FROZEN:
never regenerated, never edited, and never compared against V4 renders or vice
versa. This module is the single place that declares the two suites and the
one rule that keeps them apart, so a scorer and a renderer cannot quietly
disagree about which expectation a record belongs to.

    from reference_suites import suite_of_identity, require_suite

    suite_of_identity(manifest["identity"])          # -> "miniv3-frozen"
    require_suite(manifest, SUITE_V3_FROZEN)         # raises Refused on a mix

Suite derivation reads ONLY fields the frozen V3 records already carry
(`identity.name`, `identity.version`) -- adding a field to a frozen manifest
would itself be an edit of a frozen record. An identity that is not in the
registry is REFUSED, not guessed: a Mini V4 whose vendor ships 4.0.5 next week
is a new identity until an operator declares it here.

Every renderer writes the identity of what it produced at top-level
`identity` (the block `suite_of_identity` derives the suite from); every
scorer asserts the expectation's suite through `require_suite` before
measuring. Both import those two facts from here. The freeze that
protects the V3 records lives in
`docs/scorecard/reference-suite-freeze.sha256`, checked by
`tools/check_reference_freeze.py`; the V4 suite definition (the cases to be
rendered, with their settings) lives in `docs/scorecard/miniv4-suite/suite.json`.
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import refusal  # noqa: E402

SUITE_V3_FROZEN = "miniv3-frozen"
SUITE_V4 = "miniv4"
SUITE_OPEN_SOURCE = "open-source"

#: The version string the frozen V3 records carry in `identity.version`. A
#: test asserts this against all three frozen manifests, so the registry
#: cannot drift from the records it names.
V3_FROZEN_VERSION = "3.12.0.3422"
V3_BUNDLE_ID = "com.Arturia.Mini-V3.vst3"
V3_PLUGIN_NAME = "Mini V3"

#: The only Mini V4 version this suite baselines. The render script refuses
#: (REFUSED, not FAIL) anything else, per the apparatus-precondition rule:
#: a plugin update is a change of instrument, not a re-run.
V4_REQUIRED_VERSION = "4.0.4.6366"
V4_PLUGIN_NAME = "Mini V4"

V4_SUITE_MANIFEST = ROOT / "docs/scorecard/miniv4-suite/suite.json"
FREEZE_MANIFEST = ROOT / "docs/scorecard/reference-suite-freeze.sha256"

#: (identity.name, identity.version) -> suite. Entries are added by an
#: operator decision, never inferred: a version not listed here refuses.
_KNOWN_IDENTITIES = {
    (V3_PLUGIN_NAME, V3_FROZEN_VERSION): SUITE_V3_FROZEN,
    (V4_PLUGIN_NAME, V4_REQUIRED_VERSION): SUITE_V4,
}

#: Where each suite's records live (or will live). `miniv3-frozen` names the
#: three frozen record directories; `miniv4` names the suite definition that
#: lists every render still waiting.
SUITE_RECORDS = {
    SUITE_V3_FROZEN: (
        "docs/scorecard/mono-m1a-miniv3",
        "docs/scorecard/mono-m5a-miniv3",
        "docs/scorecard/mono-m5b-miniv3",
    ),
    SUITE_V4: ("docs/scorecard/miniv4-suite",),
}


class Refused(refusal.Refused, RuntimeError):
    pass


def suite_of_identity(identity: dict) -> str:
    """Which suite a record's `identity` block belongs to, or REFUSED.

    Reads only `name` and `version` -- the two fields every frozen V3
    manifest already carries. Anything unknown refuses: a rendered file
    whose producer this registry has never declared has no suite, and an
    unsuiteed record must never be scored."""
    if not isinstance(identity, dict):
        raise Refused("record has no identity block; its suite cannot be derived")
    name = identity.get("name")
    version = identity.get("version")
    key = (name, version)
    if key not in _KNOWN_IDENTITIES:
        known = ", ".join(f"{n} {v}" for n, v in sorted(_KNOWN_IDENTITIES))
        raise Refused(
            f"plugin identity {name!r} version {version!r} is in no reference "
            f"suite (known: {known}); refusing to guess -- declare it in "
            f"tools/reference_suites.py before scoring it")
    return _KNOWN_IDENTITIES[key]


def require_suite(manifest: dict, expected: str) -> str:
    """Assert a record manifest belongs to the expected suite, or REFUSED.

    This is the scoring-side gate: `tools/mono_m1a_score.py` and
    `tools/mono_m5a_score.py` call it with `SUITE_V3_FROZEN`, so a Mini V4
    render substituted into a V3 expectation is refused before any number is
    produced. The refusal names both suites -- the one the record declares
    and the one the scorer demands -- so the mix is diagnosable from the
    message alone."""
    got = suite_of_identity(manifest.get("identity") if isinstance(manifest, dict) else None)
    if got != expected:
        raise Refused(
            f"reference-suite mix: this record is from suite {got!r} but the "
            f"expectation requires {expected!r}. V4 renders are never graded "
            f"against V3 expectations, and vice versa (#1050)")
    return got


def load_v4_suite(path: pathlib.Path = V4_SUITE_MANIFEST) -> dict:
    """Load and structurally validate the Mini V4 suite definition.

    Refuses (not fails) when the manifest is missing or malformed: the
    renderer cannot know what to render and the tests cannot know what was
    declared, and neither may answer anyway."""
    p = pathlib.Path(path)
    if not p.exists():
        raise Refused(f"V4 suite manifest missing: {p}")
    try:
        suite = json.loads(p.read_text())
    except json.JSONDecodeError as exc:
        raise Refused(f"{p.name} is not valid JSON: {exc}") from exc
    for key in ("schema", "suite_id", "plugin", "render", "cases"):
        if key not in suite:
            raise Refused(f"{p.name} has no {key!r}")
    if suite["suite_id"] != SUITE_V4:
        raise Refused(f"{p.name} declares suite {suite['suite_id']!r}, not {SUITE_V4!r}")
    plugin = suite["plugin"]
    if not isinstance(plugin, dict) or plugin.get("required_version") != V4_REQUIRED_VERSION:
        raise Refused(
            f"{p.name} baselines plugin version "
            f"{plugin.get('required_version')!r}, but this suite is declared "
            f"for {V4_REQUIRED_VERSION!r}")
    if not suite["cases"]:
        raise Refused(f"{p.name} lists no cases")
    seen = set()
    #: Every case carries every key, explicitly -- a holdout's note set and a
    #: case with no V3-era record are JSON null, never an absent key.
    required_keys = ("case_id", "subject", "split", "batch", "base_midi_note",
                     "patch", "phrase_s", "velocity", "reference_before_v4",
                     "render_state")
    for case in suite["cases"]:
        cid = case.get("case_id", "<no id>")
        missing = [k for k in required_keys if k not in case]
        if missing:
            raise Refused(f"{p.name} case {cid} is missing {', '.join(missing)}")
        if case["base_midi_note"] is None and not str(case.get("sealing", "")).strip():
            raise Refused(
                f"{p.name} case {cid} has a sealed (null) note set but no "
                f"'sealing' statement of when the trajectories are fixed")
        if cid in seen:
            raise Refused(f"{p.name} lists case {cid} twice")
        seen.add(cid)
    return suite
