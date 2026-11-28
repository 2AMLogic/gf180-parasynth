#!/usr/bin/env python3
"""Fetch reference recordings named in refaudio/ into refaudio/cache/ (gitignored).

    python tools/refaudio_fetch.py <archive.zip> '<member path>' ['<member path>' ...]

The recordings live on operator-side storage, not in this repository; see
refaudio/README.md. This tool reaches them over ssh, and needs two things in
the environment that are deliberately not committed:

    REFAUDIO_SSH    user@host of the storage
    REFAUDIO_ROOT   the library directory on that host

THREE OUTCOMES, and they are kept apart on purpose:

    exit 0  FETCHED  every requested member is on disk, non-empty, and -- where
                     refaudio/index/ lists it -- exactly the size the index says
    exit 1  FAIL     the storage was reachable and the fetch went wrong
    exit 2  REFUSED  a precondition is not met, so nothing was attempted

REFUSED is not a failure of the recording and must never be read as one. A host
with no route to the storage is the normal case for most of the fleet: open a
`reference-audio` issue instead.

The defect this is built to not have: `unzip -p` on a member that does not
exist prints nothing, and a shell redirect of nothing is a zero-byte .wav that
every downstream tool will open. So status comes from the process, an empty
result is a FAIL, a partial file never survives, and a size that disagrees with
the index is a FAIL -- the file on disk must be the file the index describes.

The catalog lookup, the member path check and the index are `tools/refaudio_local.py`'s
(`pack_record`, `validate_members`, `load_indexed_sizes`), shared with the local
and S3 routes (#1602): a copy of each here had drifted from them. Only the
transfer is this tool's own.
"""
from __future__ import annotations
import os, pathlib, shlex, subprocess, sys
import refusal
import refaudio_local as rl

REPO = pathlib.Path(__file__).resolve().parent.parent
REFAUDIO = REPO / "refaudio"
FETCHED, FAIL, REFUSED = 0, 1, 2


class Refused(refusal.Refused, Exception):
    """A precondition is not met; nothing was attempted."""


def indexed_sizes(archive: str) -> dict[str, int] | None:
    """member path -> bytes, or None when this pack has no committed index."""
    return rl.read_indexed_sizes(REFAUDIO / "index" / (archive[:-4] + ".tsv"))


def preconditions(archive: str, members: list[str]) -> tuple[str, str]:
    ssh, root = os.environ.get("REFAUDIO_SSH", ""), os.environ.get("REFAUDIO_ROOT", "")
    if not ssh or not root:
        raise Refused("REFAUDIO_SSH / REFAUDIO_ROOT are not set: this host has no route to the "
                      "reference-audio storage. Open a `reference-audio` issue instead.")
    rl.pack_record(archive, refaudio=REFAUDIO)       # Refused if not in catalog
    if not members:
        raise Refused("no member paths given")
    rl.validate_members(members)
    try:
        rl.refuse_part_collisions(members)
    except rl.Refused as why:
        raise Refused(str(why)) from why
    return ssh, root


def unzip_literal(member: str) -> str:
    """unzip treats * ? [ as wildcards even in an exact name; escape them."""
    return "".join("\\" + c if c in "*?[\\" else c for c in member)


def fetch_one(ssh: str, root: str, archive: str, member: str, want: int | None, cache: pathlib.Path) -> str | None:
    """Returns None on success, or the reason it failed.

    As in `refaudio_local.extract()`: an `OSError` met while landing the member
    -- making its directory, writing its `.part`, moving it into place -- is this
    member's reason, naming the exception, never the run's traceback (#1684).
    The `.part` is removed only once it has been written."""
    dest = cache / archive[:-4] / member
    part = dest.with_name(dest.name + ".part")
    remote = " ".join(shlex.quote(a) for a in
                      ["unzip", "-p", f"{root}/packs/{archive}", unzip_literal(member)])
    written = False
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with part.open("wb") as out:
            written = True
            proc = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", ssh, remote],
                                  stdout=out, stderr=subprocess.PIPE, timeout=600)
        got = part.stat().st_size
        if proc.returncode != 0:
            return f"remote exited {proc.returncode}: {proc.stderr.decode('utf-8', 'replace').strip()[:200]}"
        if got == 0:
            return "remote exited 0 but sent no bytes (no such member?)"
        if want is not None and got != want:
            return f"size {got} != {want} in the index: not the file the index describes"
        part.replace(dest)
        return None
    except subprocess.TimeoutExpired:
        return "timed out after 600 s"
    except OSError as why:
        return f"not written ({type(why).__name__}): {why}"
    finally:
        if written:
            part.unlink(missing_ok=True)


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0] in ("-h", "--help"):
        print(__doc__)
        return REFUSED
    archive, members = argv[0], argv[1:]
    try:
        ssh, root = preconditions(archive, members)
        sizes = rl.load_indexed_sizes(archive, refaudio=REFAUDIO)   # Refused, never a traceback (#1602)
    except refusal.Refused as why:
        # The base class, not this module's Refused: the catalog and index
        # lookups raise refaudio_local's, a sibling of this one's (#1602).
        print(f"REFUSED  {why}")
        return REFUSED

    if sizes is None:
        print(f"note     no committed index for {archive}: sizes will NOT be checked")
    cache, bad = REFAUDIO / "cache", 0
    for m in members:
        want = None if sizes is None else sizes.get(m)
        if sizes is not None and want is None:
            print(f"note     {m!r} is not in the index: size will NOT be checked")
        why = fetch_one(ssh, root, archive, m, want, cache)
        if why:
            bad += 1
            print(f"FAIL     {m}\n         {why}")
        else:
            checked = "size matches index" if want is not None else "size unchecked"
            print(f"FETCHED  refaudio/cache/{archive[:-4]}/{m}  ({checked})")
    print(f"{len(members) - bad} fetched, {bad} failed")
    return FAIL if bad else FETCHED


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
