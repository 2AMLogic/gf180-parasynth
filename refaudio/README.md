# refaudio — recordings of real hardware this project can ask for

An index of recorded reference material — 89 packs of classic synthesizers,
samplers and drum machines, 172,182 WAV files, about 121 hours — available to
this project for **training and verifying the design**.

**The index is committed; the audio is not.** The recordings sit on
operator-side storage. What is here is the list of what exists, so that anyone
working in this repository can find a recording and ask for it by name.

## What this is, and what it is not

`docs/failure-modes.md` says the work here drifts toward checks against our
own model because external grounding is expensive. This makes one kind of
external grounding cheap: recordings of the real machines, made by someone who
has never seen our model.

**Which machine each pack is, and which group it may be read in, is
[`docs/corpus-lineage.md`](../docs/corpus-lineage.md)** — not this file. This
one is an index of *what exists*; that one is the lineage record: per pack the
unit, the recording chain, the licence and redistribution terms, the
relationships between files, and which of `analyzer-development` /
`threshold-calibration` / `held-out-validation` it belongs to. It exists because
a pack count is not a machine count: three of the packs indexed here descend
from one Samples From Mars 808, and the `808*` directories in
tidalcycles/Dirt-Samples are byte-identical to the Fischer set. Its machine-
readable half is `docs/corpus-lineage.json`, checked by
`tools/corpus_lineage.py check`.

It is **not** a measurement set, and it must not be cited as one:

- **No panel settings ship with any of it.** That is the same disqualifier
  `docs/moog-recording-protocol.md` gives for the Legowelt pack: without the
  settings a take is a sound, not a measurement. File names carry what the
  vendor chose to encode (`Decay A`, `Lo`/`Mid`/`Click`, a trailing knob
  index) and nothing else.
- **The recording chain is in every file.** Many subsets are deliberately
  coloured — console, tape, sampler, saturation. The packs usually separate a
  clean subset from the coloured ones; the 808's `Clean/Digital` is the closest
  thing here to the bare machine. Use the clean subset and say which you used.
- **Nothing in this repository has been compared against any of it yet.** No
  claim rests on these recordings. When one does, it belongs in a record that
  names the `archive` and `path` it used.
- Almost everything is **44.1 kHz** (154k files 24-bit, 18k 16-bit). Resampling
  is a decision to record, not something to let a loader do silently.

## What it does and does not unblock — checked against the index, not assumed

**The true discrimination floor (`docs/discrimination.md` §4) — no, and this
paragraph used to say yes.** That section says the floor needs multi-take
material and that `808 From Mars` "would supply it; it was **not purchased**."
It has now been purchased, and **it does not supply it.** This file previously
read the clean bass drum's 144 files as "24 settings × 6 takes"; the trailing
`01`…`06` is the **TONE knob**, not a take index, and the grid is 2 chains ×
2 accents × 6 decay × 6 tone with **no take axis at all**. Measured in
[`docs/bd-repeatability-measurement.md`](../docs/bd-repeatability-measurement.md)
and confirmed by the vendor's own notes in `catalog.json`; the voices with no
knob to sweep (Cowbell, Rim Shot, Claves) carry no trailing number at all.
**There are no Δ = 0 pairs anywhere in this pack.** The nearest thing reachable
is `808_loops_from_mars.zip`'s bass-drum-only 4/4 loops, in which one setting is
struck repeatedly inside one continuous take — that pack is indexed in
`catalog.json` but was not among the copies available when this was checked.
→ [`index/808-from-mars.tsv`](index/808-from-mars.tsv) (1,562 files). The
vendor's superseded earlier edition is indexed too, as a second recording
session of the same machine: [`index/808_from_mars_legacy.tsv`](index/808_from_mars_legacy.tsv).

**M3, ladder structure vs real hardware (`docs/capability-dag.md`) — no.** M3
needs one clip of a real Minimoog filter self-oscillating. The Minimoog pack
here is **patch multisamples** — named patches (`3 Stack Bass`, `Vibey Lead`, …)
sampled across the keyboard — and a search of all 3,669 file names finds
nothing labelled as self-oscillation, resonance, sine or sweep. It may still be
useful for the h5-vs-h3 probe's *rejection* statistics, and it is a far larger
Minimoog corpus than the 222 Legowelt files, but it does not supply the clip.
**M3 stays blocked on a recording made to the protocol.**
→ [`index/mini_from_mars.tsv`](index/mini_from_mars.tsv) (3,669 files), and a
second Moog ladder instrument, the Micromoog:
[`index/micro_from_mars.tsv`](index/micro_from_mars.tsv) (1,673 files).

## What is here

| Path | What it is |
|---|---|
| [`CATALOG.md`](CATALOG.md) | All 89 packs by category: source instrument, WAV count, hours, format, archive name |
| `catalog.json` | The same, machine-readable, plus per-pack folder breakdowns, sample-rate and bit-depth histograms, the pack's own notes on its naming convention, and the archive SHA-256 |
| `index/*.tsv` | Every WAV in the four packs above: `path, bytes, sample_rate, bits, channels, seconds`. Plain text, so it greps and diffs from a console |
| `cache/` | Where fetched audio lands. Gitignored |

Only four packs are indexed file-by-file, on purpose: the full index is 21 MB
of text, four times the size of this repository. The other 85 packs are
described in `catalog.json` down to folder level, which is enough to ask for
something. If another pack becomes central, index it then.

`source` in the catalog is taken from the pack's own notes where it has them
and is otherwise inferred from folder names; `source_basis` says which.

## Asking for a recording

On a host with a route to the storage (`REFAUDIO_SSH` and `REFAUDIO_ROOT` set
in the environment — they are not committed):

```sh
python tools/refaudio_fetch.py 808-from-mars.zip '808 From Mars/WAV/01. Individual Hits/01. Bass Drum/Clean/Digital/A/BD A 808 Decay A 01.wav'
```

The tool has three outcomes and keeps them apart: **FETCHED** (on disk,
non-empty, and the size the index says), **FAIL**, and **REFUSED** (a
precondition is unmet, nothing was attempted — exit 2). `REFUSED` is the normal
answer on most hosts and says nothing about the recording.
`tools/test_refaudio_fetch.py` covers the false-green cases, the zero-byte
`.wav` in particular.

Anywhere else: open an issue labelled `reference-audio` naming the `archive`
and `path`s, and say what you need from them. Asking for the *measurement*
rather than the file is usually the shorter route.

## Rules

- Audio is never committed. `*.wav` and `refaudio/cache/` are ignored; do not
  copy recordings elsewhere in the tree.
- A result that used a recording names it, by `archive` and `path`.
- The index is generated from the archives, not edited by hand.
