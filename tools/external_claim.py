"""Validate external-tool claims (issue #123).

A claim about an external tool is a property of (host, version, binary hash,
block size, sample rate, licence state, preset), not of the tool's name. A
claim record missing any of those is REFUSED. A negative claim additionally
needs a second, different route before it is believed. Pitch, level and pin
readback are asserted, never assumed: plugin defaults are chosen for demos.

Records live in docs/external-tool-claims.json. Exit 0 ok, 1 fail, 2 refused.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ENV_KEYS = ("host", "host_version", "binary_sha256", "block_size",
            "sample_rate", "licence_state", "preset")
ROUTE_KEYS = ("host", "loader", "machine")
DATA = Path(__file__).resolve().parent.parent / "docs" / "external-tool-claims.json"


def check_env(env: dict) -> list[str]:
    """Problems with an environment tuple; empty means complete."""
    if not isinstance(env, dict):
        return ["environment tuple missing"]
    return [f"environment.{k} missing or empty" for k in ENV_KEYS
            if env.get(k) in (None, "", [])]


def check_claim(claim: dict) -> list[str]:
    """Problems with one claim record; empty means it may be believed.

    A negative claim ('does not work') is believed only with a second route
    whose environment differs from the first in host, loader or machine and
    is itself a complete tuple that also observed the negative.
    """
    problems = check_env(claim.get("environment"))
    if claim.get("polarity") not in ("positive", "negative"):
        problems.append("polarity must be 'positive' or 'negative'")
    if claim.get("polarity") == "negative":
        second = claim.get("second_route")
        if not second:
            problems.append("negative claim has no second_route")
        else:
            env2 = second.get("environment")
            problems += [f"second_route: {p}" for p in check_env(env2)]
            if isinstance(env2, dict) and isinstance(claim.get("environment"), dict) \
                    and not any(env2.get(k) != claim["environment"].get(k)
                                for k in ROUTE_KEYS):
                problems.append("second_route is not a different host/loader/machine")
            if second.get("result") != "negative":
                problems.append("second_route did not reproduce the negative")
    return problems


def assert_readback(*, expected_hz: float, measured_hz: float, pitch_tol_cents: float = 30.0,
                    level_dbfs: float | None = None, min_level_dbfs: float = -60.0,
                    pins_set: dict | None = None, pins_read: dict | None = None) -> list[str]:
    """Pitch, level and pin readback. Returns failures; empty means all held."""
    fails = []
    if not (measured_hz > 0 and expected_hz > 0):
        fails.append("pitch: no measurable fundamental")
    else:
        cents = 1200 * math.log2(measured_hz / expected_hz)
        if abs(cents) > pitch_tol_cents:
            fails.append(f"pitch: {measured_hz:.2f} Hz vs expected {expected_hz:.2f} Hz "
                         f"({cents:+.0f} cents)")
    if level_dbfs is None or level_dbfs < min_level_dbfs:
        fails.append(f"level: {level_dbfs} dBFS below {min_level_dbfs} (silence is not data)")
    for pin, want in (pins_set or {}).items():
        got = (pins_read or {}).get(pin)
        if got is None or abs(got - want) > 1e-6:
            fails.append(f"pin {pin}: set {want}, read back {got}")
    return fails


def main(path: Path = DATA) -> int:
    try:
        claims = json.loads(path.read_text())["claims"]
    except (OSError, ValueError, KeyError) as e:
        print(f"REFUSED: cannot read {path}: {e}")
        return 2
    bad = 0
    for c in claims:
        for p in check_claim(c):
            print(f"FAIL {c.get('id', '?')}: {p}")
            bad += 1
    print("FAIL" if bad else f"OK ({len(claims)} claims)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
