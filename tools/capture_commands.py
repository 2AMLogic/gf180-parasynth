"""Diagnostic minimum and release playback commands shared by capture and receipts."""
from __future__ import annotations

import hashlib
import json
import pathlib

REQUIRED_COMMANDS = ("silence", "held-m5a-saw", "held-default", "bar808-full", "demo")
_RELEASE = pathlib.Path(__file__).resolve().parents[1] / "fpga" / "release"
MANIFESTS = {"r0": _RELEASE / "baseline-2025.1.json", "r1": _RELEASE / "r1-2025.1.json"}
R0_MANIFEST_SCHEMA = "gf180-parasynth release manifest v1"
R1_MANIFEST_SCHEMA = "gf180-parasynth R1 release manifest v1"


def load_manifest(path, *, expected_image=None) -> dict:
    """Read either release format, binding its image to the schema in the file."""
    try:
        data = pathlib.Path(path).read_bytes()
        raw = json.loads(data)
    except (OSError, ValueError) as exc:
        raise ValueError(f"release manifest {path} unreadable: {exc}") from exc
    schema = raw.get("schema") if isinstance(raw, dict) else None
    if schema == R0_MANIFEST_SCHEMA:
        image = "r0"
        commands = raw.get("commands")
    elif schema == R1_MANIFEST_SCHEMA:
        image = "r1"
        host = raw.get("host")
        commands = host.get("commands") if isinstance(host, dict) else None
        if isinstance(commands, dict):
            # R1 also advertises live-midi, which has no pinned playback bytes.
            commands = {k: v for k, v in commands.items() if isinstance(v, dict) and v.get("cmds_sha256")}
    else:
        raise ValueError(f"release manifest {path}: schema {schema!r} is neither R0's nor R1's "
                         "(no image can be bound)")
    if expected_image is not None and image != expected_image:
        raise ValueError(f"release manifest {path} declares {image}, expected {expected_image}")
    if not isinstance(commands, dict) or not commands or "image" not in raw:
        raise ValueError(f"release manifest {path}: no image or no command with pinned bytes")
    return dict(raw, commands=commands, image_id=image, label=image.upper(),
                manifest_sha256=hashlib.sha256(data).hexdigest())
