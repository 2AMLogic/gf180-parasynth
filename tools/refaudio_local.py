#!/usr/bin/env python3
"""Extract reference recordings from a LOCAL copy of a refaudio archive.

    python tools/refaudio_local.py <archive.zip> '<member path>' ['<member>' ...]
    python tools/refaudio_local.py --prefix <archive.zip> '<folder prefix>'

`tools/refaudio_fetch.py` reaches operator-side storage over ssh. On a host
where the archive is already mounted locally (REFAUDIO_LOCAL points at the
directory holding the pack zips) that route REFUSES for the wrong reason: the
bytes are here, there is simply no ssh to them. This tool is the local twin and
keeps the SAME three outcomes and the SAME refusal to hand back a file it
cannot vouch for:

    exit 0  FETCHED  every member is on disk, non-empty, and -- where
                     refaudio/index/ lists it -- exactly the size the index says
    exit 1  FAIL     the archive was readable and the extraction went wrong
    exit 2  REFUSED  a precondition is not met, so nothing was attempted

It checks one thing `refaudio_fetch.py` cannot: the archive's **SHA-256 against
catalog.json**. Over ssh the remote pack is assumed to be the indexed one; a
local file could be any zip with the right name, so it is hashed once per run
and a mismatch is a REFUSAL, not a warning. Combined with the per-member size
check that is end-to-end provenance: these bytes are the bytes the committed
index describes.

Audio lands in refaudio/cache/ (gitignored), same as the ssh tool.
"""
from __future__ import annotations
import argparse, csv, hashlib, json, os, pathlib, sys, zipfile
import refusal
import manifest as mf

REPO = pathlib.Path(__file__).resolve().parent.parent
REFAUDIO = REPO / "refaudio"
FETCHED, FAIL, REFUSED = 0, 1, 2


class Refused(refusal.Refused, Exception):
    """A precondition is not met; nothing was attempted."""


def pack_record(archive: str) -> dict:
    path = REFAUDIO / "catalog.json"
    cat = json.loads(path.read_text(encoding="utf-8"))
    mf.require_keys(cat, "packs", f"the reference-audio catalog at {path}", refused=Refused)
    for p in cat["packs"]:
        if p["archive"] == archive:
            return p
    raise Refused(f"{archive!r} is not an archive in refaudio/catalog.json")


def inventory_path_mode(path: pathlib.Path) -> int:
    """Stat an inventory path. Only a missing path counts as absent (mode 0)."""
    try:
        return path.stat().st_mode
    except (FileNotFoundError, NotADirectoryError):
        return 0


def read_indexed_sizes(tsv: pathlib.Path) -> dict[str, int] | None:
    """The rows of one `refaudio/index/*.tsv` as member path -> bytes, or None
    when there is no file at `tsv` -- the one reader of the index's sizes, so
    no tool carries a copy that can drift from it (#1551).

    Only a missing path is `None`; any other reason the file cannot be opened
    is raised. A row with no `path` or no `bytes` is not an entry: it raises
    `ValueError` naming the line and the field, rather than becoming a size
    for the empty path."""
    try:
        fh = tsv.open(encoding="utf-8", newline="")
    except (FileNotFoundError, NotADirectoryError):
        return None
    with fh:
        rows = csv.DictReader(fh, delimiter="\t")
        sizes = {}
        for row in rows:
            for field in ("path", "bytes"):
                if not row.get(field):
                    raise ValueError(f"line {rows.line_num}: missing {field}")
            sizes[row["path"]] = int(row["bytes"])
        return sizes


def indexed_sizes(archive: str) -> dict[str, int] | None:
    """member path -> bytes, or None when this pack has no committed index."""
    return read_indexed_sizes(REFAUDIO / "index" / (archive[:-4] + ".tsv"))


def sha256_of(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def locate(archive: str) -> pathlib.Path:
    root = os.environ.get("REFAUDIO_LOCAL", "")
    if not root:
        raise Refused("REFAUDIO_LOCAL is not set: point it at the directory holding the "
                      "pack zips, or use tools/refaudio_fetch.py where there is an ssh route.")
    d = pathlib.Path(root).expanduser()
    if not d.is_dir():
        raise Refused(f"REFAUDIO_LOCAL={root!r} is not a directory")
    z = d / archive
    if not z.is_file():
        raise Refused(f"{archive} is not in REFAUDIO_LOCAL ({d})")
    return z


def preconditions(archive: str, members: list[str]) -> tuple[pathlib.Path, dict]:
    rec = pack_record(archive)
    z = locate(archive)
    want_bytes = rec.get("archive_bytes")
    got_bytes = z.stat().st_size
    if want_bytes is not None and got_bytes != want_bytes:
        raise Refused(f"{archive} is {got_bytes} bytes, catalog.json says {want_bytes}: "
                      "not the archive the index describes")
    want_sha = rec.get("archive_sha256")
    if not want_sha:
        raise Refused(f"catalog.json has no archive_sha256 for {archive}: cannot vouch for a local copy")
    got_sha = sha256_of(z)
    if got_sha != want_sha:
        raise Refused(f"{archive} SHA-256 {got_sha} != catalog.json {want_sha}")
    for m in members:
        parts = pathlib.PurePosixPath(m).parts
        if m.startswith("/") or ".." in parts or not parts:
            raise Refused(f"member path must be a relative path inside the archive: {m!r}")
    return z, rec


def extract(zf: zipfile.ZipFile, archive: str, member: str, want: int | None,
            cache: pathlib.Path) -> str | None:
    """Returns None on success, or the reason it failed. Never leaves a partial file."""
    dest = cache / archive[:-4] / member
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    try:
        try:
            info = zf.getinfo(member)
        except KeyError:
            return "no such member in the archive"
        with zf.open(info) as src, part.open("wb") as out:
            while True:
                chunk = src.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
        got = part.stat().st_size
        if got == 0:
            return "member extracted to zero bytes"
        if want is not None and got != want:
            return f"size {got} != {want} in the index: not the file the index describes"
        part.replace(dest)
        return None
    finally:
        part.unlink(missing_ok=True)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("archive")
    ap.add_argument("members", nargs="+")
    ap.add_argument("--prefix", action="store_true",
                    help="treat each argument as a folder prefix and take every indexed member under it")
    args = ap.parse_args(argv)

    members = args.members
    try:
        if args.prefix:
            sizes0 = indexed_sizes(args.archive)
            if sizes0 is None:
                raise Refused(f"--prefix needs a committed index; {args.archive} has none")
            members = sorted(p for p in sizes0 if any(p.startswith(q) for q in args.members))
            if not members:
                raise Refused(f"no indexed member under any of {args.members!r}")
        z, _rec = preconditions(args.archive, members)
    except Refused as why:
        print(f"REFUSED  {why}")
        return REFUSED

    sizes = indexed_sizes(args.archive)
    if sizes is None:
        print(f"note     no committed index for {args.archive}: sizes will NOT be checked")
    cache, bad = REFAUDIO / "cache", 0
    with zipfile.ZipFile(z) as zf:
        for m in members:
            want = None if sizes is None else sizes.get(m)
            if sizes is not None and want is None:
                print(f"note     {m!r} is not in the index: size will NOT be checked")
            why = extract(zf, args.archive, m, want, cache)
            if why:
                bad += 1
                print(f"FAIL     {m}\n         {why}")
            else:
                checked = "size matches index" if want is not None else "size unchecked"
                print(f"FETCHED  refaudio/cache/{args.archive[:-4]}/{m}  ({checked})")
    print(f"{len(members) - bad} fetched, {bad} failed")
    return FAIL if bad else FETCHED


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
