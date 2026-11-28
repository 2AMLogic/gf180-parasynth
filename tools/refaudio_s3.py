#!/usr/bin/env python3
"""Fetch reference recordings from the private S3 working copy of the library.

    python tools/refaudio_s3.py <archive.zip> '<member path>' ['<member>' ...]
    python tools/refaudio_s3.py --prefix <archive.zip> '<folder prefix>'
    python tools/refaudio_s3.py --clean            delete everything fetched

Third route to the same bytes, beside `refaudio_fetch.py` (ssh, REFAUDIO_SSH)
and `refaudio_local.py` (a mounted copy, REFAUDIO_LOCAL). Needs, deliberately
not committed:

    REFAUDIO_S3          s3://2am-batch-jobs-221082181346/refaudio/samples-from-mars
    REFAUDIO_S3_PROFILE  AWS profile to use (e.g. batch-runner-submit); optional

THE MASTER. Issue #111's operator note says to add this to the master
`gf180-polysynth/refaudio/fetch.sh` and take it here by pinned reference
(REUSE.md rule 9). The master (read at gf180-polysynth 010efac) is ssh-only,
a bash script with no S3 route, and this repository carries no REUSE.md and no
pin of it. Everything here is also logic, which CLAUDE.md puts in Python. So it
is implemented here, in Python, reusing `refaudio_local`'s extraction and
SHA-256 machinery rather than copying it; porting it back to the master is a
follow-up for whoever owns that repo.

THREE OUTCOMES, the same as the other two routes:

    exit 0  FETCHED  every member is on disk, non-empty, and the whole ARCHIVE
                     hashed to the SHA-256 committed in refaudio/catalog.json
    exit 1  FAIL     the storage was reachable and the transfer went wrong
    exit 2  REFUSED  a precondition is not met (no REFAUDIO_S3, no `aws`, unknown
                     archive, no hash to vouch with, size or hash mismatch, a
                     `--prefix` nothing is under, a member name that is not a
                     relative path inside the archive), nothing used

A zip whose size or hash disagrees with the catalog is deleted and REFUSED,
never extracted. LICENCE: the library is single-user; the cache is gitignored
(refaudio/cache/) and `--clean` removes it. By default the multi-GB archive is
deleted as soon as the requested members are extracted (`--keep-archive` to keep).

WHAT IS CHECKED BEFORE THE DOWNLOAD (#1602). Everything the committed catalog
and index can answer: the archive is catalogued, the typed members and
prefixes are safe paths, the index is readable, and a `--prefix` on an indexed
pack has something under it. Only the archive itself cannot be vouched for
until it is here. `--prefix` on a pack with NO committed index (such as
808_loops_from_mars.zip) can only be enumerated from the archive's own
directory, so that one -- and the path check on every name it expands to --
happens after the download and before anything is written.
"""
from __future__ import annotations
import argparse, os, pathlib, shutil, subprocess, sys, zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import refaudio_local as rl                                       # noqa: E402

REPO, REFAUDIO = rl.REPO, rl.REFAUDIO
CACHE = REFAUDIO / "cache"
ARCHIVES = CACHE / "_archives"
FETCHED, FAIL, REFUSED = rl.FETCHED, rl.FAIL, rl.REFUSED
Refused = rl.Refused


def s3_root() -> str:
    root = os.environ.get("REFAUDIO_S3", "").rstrip("/")
    if not root:
        raise Refused("REFAUDIO_S3 is not set: this host has no S3 route to the reference audio. "
                      "Open a `reference-audio` issue instead.")
    if not root.startswith("s3://") or len(root) <= len("s3://"):
        raise Refused(f"REFAUDIO_S3={root!r} is not an s3:// URL")
    return root


def aws_cmd(*args: str) -> list[str]:
    cmd = ["aws"]
    prof = os.environ.get("REFAUDIO_S3_PROFILE", "")
    if prof:
        cmd += ["--profile", prof]
    return cmd + list(args)


def preconditions(archive: str) -> tuple[str, dict]:
    rec = rl.pack_record(archive, refaudio=REFAUDIO)               # Refused if not in catalog
    if not rec.get("archive_sha256"):
        raise Refused(f"catalog.json has no archive_sha256 for {archive}: cannot vouch for a download")
    root = s3_root()
    if shutil.which("aws") is None:
        raise Refused("`aws` CLI not found on PATH")
    return root, rec


def _is_catalogued(path: pathlib.Path, archive: str, rec: dict) -> bool:
    """Size first, then hash: both must be the catalog's."""
    return rl.size_mismatch(path, archive, rec) is None and rl.sha256_of(path) == rec["archive_sha256"]


def download(root: str, archive: str, rec: dict, keep_hash_ok: bool = True) -> pathlib.Path:
    """The verified archive path. Reuses a cached copy only if it is the catalogued
    size AND hashes right. A cached file that is not is replaced, not trusted; a
    download that is not is deleted and REFUSED, never handed on."""
    ARCHIVES.mkdir(parents=True, exist_ok=True)
    dest = ARCHIVES / archive
    if dest.is_file() and _is_catalogued(dest, archive, rec):
        return dest
    dest.unlink(missing_ok=True)
    part = dest.with_name(dest.name + ".part")
    try:
        proc = subprocess.run(aws_cmd("s3", "cp", f"{root}/packs/{archive}", str(part), "--only-show-errors"),
                              stderr=subprocess.PIPE, timeout=3600)
        if proc.returncode != 0:
            raise RuntimeError(f"aws s3 cp exited {proc.returncode}: "
                               f"{proc.stderr.decode('utf-8', 'replace').strip()[:300]}")
        why = rl.size_mismatch(part, archive, rec)
        if why:
            raise Refused(f"{why}; deleted, nothing extracted")
        got = rl.sha256_of(part)
        if got != rec["archive_sha256"]:
            raise Refused(f"{archive} from S3 hashes to {got}, catalog.json says "
                          f"{rec['archive_sha256']}: not the archive the catalogue describes; "
                          "deleted, nothing extracted")
        part.replace(dest)
        return dest
    finally:
        part.unlink(missing_ok=True)


def clean() -> int:
    n = sum(1 for p in CACHE.rglob("*") if p.is_file()) if CACHE.is_dir() else 0
    shutil.rmtree(CACHE, ignore_errors=True)
    print(f"CLEANED  removed {n} file(s) under refaudio/cache/")
    return FETCHED


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("archive", nargs="?")
    ap.add_argument("members", nargs="*")
    ap.add_argument("--prefix", action="store_true")
    ap.add_argument("--keep-archive", action="store_true")
    ap.add_argument("--clean", action="store_true")
    args = ap.parse_args(argv)
    if args.clean:
        return clean()
    try:
        if not args.archive or not args.members:
            raise Refused("need an archive and at least one member (or --prefix folder)")
        rl.validate_members(args.members)
        if not args.prefix:
            rl.refuse_part_collisions(args.members)
        root, rec = preconditions(args.archive)
        sizes = rl.load_indexed_sizes(args.archive, refaudio=REFAUDIO)
        if args.prefix and sizes is not None:
            # The committed index already knows the answer: refuse an empty
            # selection HERE, not after a multi-GB download (#1602).
            rl.resolve_prefix_from_index(args.archive, args.members, sizes)
        z = download(root, args.archive, rec)
    except Refused as why:
        print(f"REFUSED  {why}")
        return REFUSED
    except (RuntimeError, subprocess.TimeoutExpired) as why:
        print(f"FAIL     {why}")
        return FAIL

    try:
        with zipfile.ZipFile(z) as zf:
            return rl.extract_from_archive(zf, args.archive, args.members, sizes, CACHE,
                                           prefixes=args.members if args.prefix else None,
                                           unchecked="archive SHA-256 verified")
    except zipfile.BadZipFile as why:
        print(f"FAIL     {args.archive} hashes right but does not open as a zip: {why}")
        return FAIL
    except Refused as why:
        print(f"REFUSED  {why}")
        return REFUSED
    finally:
        if not args.keep_archive:
            z.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
