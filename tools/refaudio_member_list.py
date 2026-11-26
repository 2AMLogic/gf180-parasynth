#!/usr/bin/env python3
"""List the WAVs one folder of a reference archive holds, read from the archive itself.

    python tools/refaudio_member_list.py <archive.zip> '<folder>' [--write PATH] [--keep-archive]

WHY. `refaudio/index/*.tsv` is the committed per-file index of four packs. A
fifth, `808_loops_from_mars.zip`, has no index in its catalogue entry, so
nothing committed says which files a folder of it holds. A reader of that
folder's cache could only enumerate what was on disk, and a partly fetched
folder enumerated short with nothing recording the loss (#1427). This
supplies the missing statement for ONE folder, without indexing the whole
pack: every immediate `.wav` member of the folder, from the archive's own
directory.

HOW. The archive is downloaded through `tools/refaudio_s3.py`'s own
preconditions and hash check -- the whole file must hash to the SHA-256 in
`refaudio/catalog.json` before one name is read -- so the names are the
catalogued archive's, not a look-alike's. No audio is extracted. The archive
is deleted afterwards unless `--keep-archive`.

WHAT IT PRINTS. The inventory file, on stdout or at `--write`: `#` comment
lines recording the archive, its SHA-256, the folder and the two commands --
this one, to regenerate the file, and the `refaudio_s3.py --prefix` one, to
fetch the members -- then one member name per line, sorted. Nothing else goes
to stdout (`--write` reports on stderr), so `> file` is the whole workflow and
a regenerated file diffs clean against the committed one.
`tools/measure_repeatability.py` reads this format (`expected_loops`).

THREE OUTCOMES, the same contract as the fetch tools:

    exit 0  LISTED   every name was read from a hash-verified archive
    exit 1  FAIL     the storage was reachable and the transfer went wrong
    exit 2  REFUSED  a precondition is not met, so nothing was listed: no
                     REFAUDIO_S3, unknown archive, no hash to vouch with,
                     hash mismatch, a folder that holds no WAV, a member name
                     that cannot be written one per line

Generated, never edited: `refaudio/README.md` -- "The index is generated from
the archives, not edited by hand."
"""
from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import refaudio_s3 as s3                                           # noqa: E402

LISTED, FAIL, REFUSED = s3.FETCHED, s3.FAIL, s3.REFUSED
Refused = s3.Refused


def immediate_wavs(names, folder: str) -> list[str]:
    """Names of `folder`'s immediate `.wav` members, sorted: nothing from a
    sub-folder, no other extension, and nothing from a folder that merely
    begins with `folder`'s name."""
    pre = folder.strip("/") + "/"
    return sorted(n[len(pre):] for n in names
                  if n.startswith(pre) and "/" not in n[len(pre):]
                  and n.lower().endswith(".wav"))


def inventory(archive: str, sha: str, folder: str, names: list[str], root: str,
              profile: str, wrote: str | None) -> str:
    """The inventory file's text: its header, then one member name per line."""
    env = f"REFAUDIO_S3={root}" + (f" REFAUDIO_S3_PROFILE={profile}" if profile else "")
    head = [
        f"# The {len(names)} WAVs this folder holds in {archive}, by name:",
        f"#   {folder}",
        f"# archive_sha256 {sha}",
        "# Read from the archive's own directory after the whole archive hashed to the",
        "# SHA-256 above, which is the one refaudio/catalog.json records. Generated,",
        "# never edited (refaudio/README.md); regenerate and diff:",
        f"#   python tools/refaudio_member_list.py {archive} '{folder}'"
        + (f" --write {wrote}" if wrote else ""),
        "# Fetch the members into refaudio/cache/ (hashes the archive again, extracts",
        "# only this folder):",
        f"#   {env} python tools/refaudio_s3.py --prefix {archive} '{folder}/'",
    ]
    return "\n".join(head + names) + "\n"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("archive")
    ap.add_argument("folder", help="one folder inside the archive, spelled as the archive spells it")
    ap.add_argument("--write", type=pathlib.Path,
                    help="write the inventory here instead of stdout")
    ap.add_argument("--keep-archive", action="store_true")
    args = ap.parse_args(argv)
    try:
        root, rec = s3.preconditions(args.archive)
        z = s3.download(root, args.archive, rec)
    except Refused as why:
        print(f"REFUSED  {why}")
        return REFUSED
    except (RuntimeError, subprocess.TimeoutExpired) as why:
        print(f"FAIL     {why}")
        return FAIL
    try:
        try:
            with zipfile.ZipFile(z) as zf:
                names = immediate_wavs(zf.namelist(), args.folder)
        except zipfile.BadZipFile as why:
            print(f"FAIL     {args.archive} hashed right but does not open as a zip: {why}")
            return FAIL
    finally:
        if not args.keep_archive:
            z.unlink(missing_ok=True)
    if not names:
        print(f"REFUSED  {args.folder!r} holds no WAV in {args.archive}: an empty "
              "inventory would leave a reader to enumerate whatever is on disk")
        return REFUSED
    if any(c in n for n in names for c in "\r\n"):
        print(f"REFUSED  a member name under {args.folder!r} holds a line break: the "
              "inventory is one name per line")
        return REFUSED
    text = inventory(args.archive, rec["archive_sha256"], args.folder, names, root,
                     os.environ.get("REFAUDIO_S3_PROFILE", ""),
                     str(args.write) if args.write else None)
    if args.write:
        args.write.write_text(text, encoding="utf-8")
        print(f"wrote     {args.write}  ({len(names)} members)", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return LISTED


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
