"""Validate external-tool claims (issue #123).

A claim about an external tool is a property of (host, version, binary hash,
block size, sample rate, licence state, preset), not of the tool's name. A
claim record missing any of those is REFUSED. A negative claim additionally
needs a second, different route before it is believed. Pitch, level and pin
readback are asserted, never assumed: plugin defaults are chosen for demos.

Records live in docs/external-tool-claims.json. Exit 0 ok, 1 fail, 2 refused.

REFUSED (2) means the file cannot be judged: unreadable, or a claim record
without the fields that say what kind of claim it is (polarity, environment).
FAIL (1) means a judgeable record does not meet the rules.

An empty claims list is OK, deliberately: no claim recorded is no claim made.
What it must not do is let unbacked statements in elsewhere in the file, so
every host_per_plugin entry is either status "unverified" or names a claim id
that exists and passes. Consumers read that table through host_result(), which
refuses unverified entries rather than returning them as fact.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

# loader and machine are required so a second route cannot count as
# "different" merely by omitting them.
ENV_KEYS = ("host", "host_version", "loader", "machine", "binary_sha256",
            "block_size", "sample_rate", "licence_state", "preset")
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


class Unverified(ValueError):
    """A host_per_plugin entry was asked for as fact but is not backed."""


def check_host_table(table: dict, claims_by_id: dict) -> list[str]:
    """Problems with host_per_plugin: each entry unverified, or backed."""
    problems = []
    if not isinstance(table, dict):
        return ["host_per_plugin is not an object"]
    for plugin, hosts in table.items():
        if plugin == "note":
            continue
        if not isinstance(hosts, dict):
            problems.append(f"host_per_plugin.{plugin} is not an object")
            continue
        for host, entry in hosts.items():
            where = f"host_per_plugin.{plugin}.{host}"
            if not isinstance(entry, dict) or "observed" not in entry:
                problems.append(f"{where}: needs {{observed, status}}")
                continue
            status = entry.get("status")
            if status == "unverified":
                continue
            if status != "verified":
                problems.append(f"{where}: status must be 'verified' or 'unverified'")
                continue
            cid = entry.get("claim")
            if cid not in claims_by_id:
                problems.append(f"{where}: verified but claim {cid!r} not recorded")
            elif check_claim(claims_by_id[cid]):
                problems.append(f"{where}: backing claim {cid!r} does not pass")
    return problems


def host_result(plugin: str, host: str, path: Path = DATA) -> str:
    """The observed result for plugin under host, only if verified.

    Raises Unverified for an unverified or unbacked entry: an observation
    without its environment tuple is not a fact a consumer may act on.
    """
    data = json.loads(path.read_text())
    entry = data.get("host_per_plugin", {}).get(plugin, {}).get(host)
    if not isinstance(entry, dict):
        raise Unverified(f"{plugin} under {host}: no entry")
    claims_by_id = {c.get("id"): c for c in data.get("claims", []) if isinstance(c, dict)}
    if entry.get("status") != "verified" or check_host_table(
            {plugin: {host: entry}}, claims_by_id):
        raise Unverified(f"{plugin} under {host}: {entry.get('observed')!r} is unverified")
    return entry["observed"]


def main(path: Path = DATA) -> int:
    try:
        data = json.loads(path.read_text())
        claims = data["claims"]
        if not isinstance(claims, list):
            raise ValueError("claims is not a list")
    except (OSError, ValueError, KeyError, TypeError) as e:
        print(f"REFUSED: cannot read {path}: {e}")
        return 2
    refused = [i for i, c in enumerate(claims)
               if not isinstance(c, dict) or "polarity" not in c or "environment" not in c]
    if refused:
        for i in refused:
            c = claims[i]
            cid = c.get("id", f"#{i}") if isinstance(c, dict) else f"#{i}"
            print(f"REFUSED {cid}: record has no polarity or no environment; "
                  "cannot tell what is being claimed")
        return 2
    bad = 0
    for c in claims:
        for p in check_claim(c):
            print(f"FAIL {c.get('id', '?')}: {p}")
            bad += 1
    by_id = {c.get("id"): c for c in claims}
    for p in check_host_table(data.get("host_per_plugin", {}), by_id):
        print(f"FAIL {p}")
        bad += 1
    if bad:
        print("FAIL")
        return 1
    reason = "" if claims else ": none recorded, so nothing is claimed"
    print(f"OK ({len(claims)} claims{reason})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
