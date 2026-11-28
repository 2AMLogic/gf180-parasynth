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

THE SHARED HELPERS. All three routes -- this one, `refaudio_fetch.py` (ssh) and
`refaudio_s3.py` (S3) -- read the catalog, check member paths, load the index
and run the zip loop through the functions below (#1602). They were copied
per tool after #1551 and the copies drifted: the S3 route checked the path of a
`--prefix` argument but never of the names it expanded to, so an archive member
named `Loops/WAV/../../../../escaped.wav` was written outside refaudio/cache/
with exit 0; all three turned a malformed index into a traceback (exit 1, read
as FAIL) instead of REFUSED; the S3 route skipped the `archive_bytes` check,
downloaded a multi-GB archive before refusing a `--prefix` the committed index
had already answered, and said nothing when a size went unchecked. One copy of
each check, used at the point where the thing it guards is about to happen.
"""
from __future__ import annotations
import argparse, csv, hashlib, json, os, pathlib, sys, zipfile, zlib
import refusal

REPO = pathlib.Path(__file__).resolve().parent.parent
REFAUDIO = REPO / "refaudio"
FETCHED, FAIL, REFUSED = 0, 1, 2


class Refused(refusal.Refused, Exception):
    """A precondition is not met; nothing was attempted."""


def pack_record(archive: str, *, refaudio: pathlib.Path | None = None) -> dict:
    """catalog.json's entry for `archive`, or Refused naming the catalog.

    `refaudio` is the library directory to read it from. It defaults to this
    module's; a caller that was redirected to another one (`refaudio_fetch.py`
    under test) passes its own, so the shared lookup follows the caller."""
    cat = json.loads(((refaudio if refaudio is not None else REFAUDIO) / "catalog.json")
                     .read_text(encoding="utf-8"))
    for p in cat["packs"]:
        if p["archive"] == archive:
            return p
    raise Refused(f"{archive!r} is not an archive in refaudio/catalog.json")


def validate_members(members: list[str]) -> None:
    """Refused unless every member is a relative path inside the archive.

    The one path check for the three routes. It runs on the names a `--prefix`
    expands to as well as on the ones typed: a benign prefix says nothing about
    the names beneath it, and these names become destinations (#1602)."""
    for m in members:
        parts = pathlib.PurePosixPath(m).parts
        if m.startswith("/") or ".." in parts or not parts:
            raise Refused(f"member path must be a relative path inside the archive: {m!r}")


def refuse_part_collisions(members: list[str]) -> None:
    """REFUSE a request in which one member's destination is another member's
    temporary path (#1697); the three routes' one check for it.

    A member is written to `<its path>.part` and renamed into place, so a
    member named `a/b.part` beside `a/b` makes the second one's temporary file
    the first one's finished file: asked `a/b.part` first, it is reported
    FETCHED, then truncated, renamed away, and the second is FETCHED too --
    exit 0, `2 fetched`, a file missing. The whole set is refused before
    anything is written, in either order, naming the pair.

    Paths are compared as the filesystem spells them, so `./a/b`, `a//b` and
    `a/b/` are `a/b`; a string compare lets each of those through. Call after
    the per-member path check: the empty path has no name to compare. BLIND to
    a case-insensitive or Unicode-normalising cache filesystem, where
    `a/B.part` and `a/b.part` are one file."""
    landed = {pathlib.PurePosixPath(m): m for m in members}
    for m in members:
        p = pathlib.PurePosixPath(m)
        part = p.with_name(p.name + ".part")
        if part in landed:
            raise Refused(f"member {landed[part]!r} is the temporary path of member {m!r}: "
                          "one request for both can report a file FETCHED and then remove it")


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


def load_indexed_sizes(archive: str, *, refaudio: pathlib.Path | None = None
                       ) -> dict[str, int] | None:
    """`read_indexed_sizes` as the fetchers need it at the point of use: None
    when the pack has no committed index, and Refused -- not a traceback, which
    every caller reads as FAIL -- when the index cannot be opened or is not an
    index (#1602). `read_indexed_sizes` keeps its ValueError / OSError for the
    model tools that catch them."""
    try:
        return read_indexed_sizes((refaudio if refaudio is not None else REFAUDIO)
                                  / "index" / (archive[:-4] + ".tsv"))
    except (ValueError, OSError, csv.Error) as why:
        raise Refused(f"the index for {archive} (refaudio/index/{archive[:-4]}.tsv) cannot be "
                      f"read ({type(why).__name__}: {why}): nothing committed says which "
                      "members it holds or how large they are") from why


def under_prefixes(names, prefixes: list[str]) -> list[str]:
    """The names under any of `prefixes`, sorted."""
    return sorted(p for p in names if any(p.startswith(q) for q in prefixes))


def resolve_prefix_from_index(archive: str, prefixes: list[str],
                              sizes: dict[str, int] | None) -> list[str]:
    """`--prefix` on an indexed pack: every indexed member under any prefix.

    Refused when the pack has no committed index -- only its archive could then
    say what it holds, and this route does not open one to find out -- or when
    nothing is under the prefixes. Called before the archive is hashed or
    downloaded, so either refusal costs neither (#1602)."""
    if sizes is None:
        raise Refused(f"--prefix needs a committed index; {archive} has none")
    members = under_prefixes(sizes, prefixes)
    if not members:
        raise Refused(f"no indexed member under any of {prefixes!r}")
    return members


def size_mismatch(z: pathlib.Path, archive: str, rec: dict) -> str | None:
    """None when `z` is the size catalog.json records for `archive` -- or it
    records none -- else why it is not. The cheap half of the archive identity,
    read before a hash of a multi-GB file."""
    want = rec.get("archive_bytes")
    got = z.stat().st_size
    if want is None or got == want:
        return None
    return f"{archive} is {got} bytes, catalog.json says {want}: not the archive the index describes"


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


def preconditions(archive: str, members: list[str], rec: dict | None = None) -> tuple[pathlib.Path, dict]:
    """The archive's path and catalog record, once every precondition holds:
    the cheap ones (catalog, member paths) before the archive is located, sized
    and hashed. `rec` is the record `main()` already looked up for its catalog
    gate; looked up here when not given."""
    if rec is None:
        rec = pack_record(archive)
    validate_members(members)
    refuse_part_collisions(members)
    z = locate(archive)
    why = size_mismatch(z, archive, rec)
    if why:
        raise Refused(why)
    want_sha = rec.get("archive_sha256")
    if not want_sha:
        raise Refused(f"catalog.json has no archive_sha256 for {archive}: cannot vouch for a local copy")
    got_sha = sha256_of(z)
    if got_sha != want_sha:
        raise Refused(f"{archive} SHA-256 {got_sha} != catalog.json {want_sha}")
    return z, rec


def extract(zf: zipfile.ZipFile, archive: str, member: str, want: int | None,
            cache: pathlib.Path) -> str | None:
    """Returns None on success, or the reason it failed. Never leaves a partial file.

    This is the last line before a file is opened, so the destination is checked
    here whatever asked for the member: it is inside refaudio/cache/<pack>/, or
    the member is not written (#1602). A member the archive cannot deliver --
    a corrupt stream, a CRC that disagrees -- is one member's FAIL, not the
    run's traceback.

    Landing the member is the filesystem's to refuse: a directory that cannot be
    made because a FILE already holds that name (`a` beside `a/b.wav`), a `.part`
    that cannot be written or moved into place. Each is this member's reason,
    naming the exception, never the run's traceback (#1684). The `.part` is
    removed only once it has been written: under a member that is a file the
    removal itself raises `NotADirectoryError`."""
    pack = cache / archive[:-4]
    dest = pack / member
    part = dest.with_name(dest.name + ".part")
    written = False
    try:
        if not dest.resolve().is_relative_to(pack.resolve()):
            return f"not written: {member!r} would land outside refaudio/cache/{archive[:-4]}/"
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            info = zf.getinfo(member)
        except KeyError:
            return "no such member in the archive"
        with zf.open(info) as src, part.open("wb") as out:
            written = True
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
    except (zipfile.BadZipFile, zlib.error, EOFError) as why:
        return f"cannot be read from the archive ({type(why).__name__}: {why})"
    except OSError as why:
        return f"not written ({type(why).__name__}): {why}"
    finally:
        if written:
            part.unlink(missing_ok=True)


def extract_from_archive(zf: zipfile.ZipFile, archive: str, members: list[str],
                         sizes: dict[str, int] | None, cache: pathlib.Path, *,
                         prefixes: list[str] | None = None,
                         unchecked: str = "size unchecked") -> int:
    """The zip loop the local and S3 routes share: every member extracted, one
    line each, then the tally. FETCHED (0) when every one landed, FAIL (1) when
    any did not.

    Under `prefixes` (`--prefix`) the members are those under the folder
    prefixes instead: from the committed index when the pack has one, else from
    the archive's own directory -- and in both cases every expanded name gets
    the same path check as a typed one BEFORE anything is written. Refused when
    a name is not a relative path inside the archive, or nothing is under the
    prefixes, so a refused run has written no file at all.

    A size that is not checked is said out loud, per pack and per member.
    `unchecked` is what a FETCHED line says in place of a size check; the S3
    route vouches with the whole archive's hash instead.

    Raises `zipfile.BadZipFile` when the archive does not open at all: the
    caller owns the archive and its deletion."""
    if prefixes is not None:
        if sizes is not None:
            members = resolve_prefix_from_index(archive, prefixes, sizes)
        else:
            members = under_prefixes((i.filename for i in zf.infolist() if not i.is_dir()), prefixes)
            if not members:
                raise Refused(f"no member under any of {prefixes!r}")
    validate_members(members)
    refuse_part_collisions(members)
    if sizes is None:
        print(f"note     no committed index for {archive}: sizes will NOT be checked")
    bad = 0
    for m in members:
        want = None if sizes is None else sizes.get(m)
        if sizes is not None and want is None:
            print(f"note     {m!r} is not in the index: size will NOT be checked")
        why = extract(zf, archive, m, want, cache)
        if why:
            bad += 1
            print(f"FAIL     {m}\n         {why}")
        else:
            checked = "size matches index" if want is not None else unchecked
            print(f"FETCHED  refaudio/cache/{archive[:-4]}/{m}  ({checked})")
    print(f"{len(members) - bad} fetched, {bad} failed")
    return FAIL if bad else FETCHED


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("archive")
    ap.add_argument("members", nargs="+")
    ap.add_argument("--prefix", action="store_true",
                    help="treat each argument as a folder prefix and take every indexed member under it")
    args = ap.parse_args(argv)

    try:
        # The catalog gate comes first, and the index is read once. Before #1602
        # `--prefix` joined the archive name onto refaudio/index/ before the
        # catalog was consulted, so `--prefix ../../outside.zip` parsed a file
        # outside refaudio/index/ and reported that file's defect instead.
        rec = pack_record(args.archive)
        sizes = load_indexed_sizes(args.archive)
        members = resolve_prefix_from_index(args.archive, args.members, sizes) if args.prefix else args.members
        z, _rec = preconditions(args.archive, members, rec)
    except Refused as why:
        print(f"REFUSED  {why}")
        return REFUSED

    try:
        with zipfile.ZipFile(z) as zf:
            return extract_from_archive(zf, args.archive, members, sizes, REFAUDIO / "cache")
    except zipfile.BadZipFile as why:
        print(f"FAIL     {args.archive} hashes right but does not open as a zip: {why}")
        return FAIL


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
