# M0 — Vault Scan

`bm vault scan` is the first executable milestone of BLACKM.

Its job is intentionally narrow: **inventory what already exists without touching source files**.

## Install for development

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

## First scan

Pass the Mac catalog folder and the mounted USB explicitly:

```bash
bm vault scan \
  ~/Music \
  /Volumes/<USB_NAME>
```

With no paths, BLACKM safely discovers `~/Music` plus mounted volumes that do not resolve to the root filesystem.

To define persistent shell defaults without editing BLACKM:

```bash
export BLACKM_SCAN_PATHS="$HOME/Music:/Volumes/<USB_NAME>"
bm vault scan
```

The default catalog database is:

```text
~/.blackm/catalog.db
```

A different database can be used for experiments:

```bash
bm vault scan ~/Music --db /tmp/blackm-test.db
```

Machine-readable output:

```bash
bm vault scan ~/Music --json
```

## What M0 records

For supported audio, artwork, lyrics, video and metadata assets:

- absolute source path;
- source root;
- relative path;
- extension and asset kind;
- file size;
- SHA-256 content checksum;
- creation timestamp when the platform exposes a genuine birth time;
- modification timestamp;
- WAV duration, sample rate, channel count and bit depth when readable.

Every scan creates a new immutable snapshot row in SQLite. M0 does not overwrite the previous snapshot.

## Exact duplicates

M0 reports duplicate groups only when files have the same SHA-256 checksum.

```text
same checksum = same bytes
```

A matching or similar filename is **never** considered sufficient evidence for deletion or consolidation.

Audio fingerprinting for alternate encodes is a later milestone.

## Safety contract

During `vault scan`, BLACKM MUST NOT:

- rename source files;
- move source files;
- delete source files;
- modify source metadata;
- rewrite audio;
- consolidate directories;
- perform remote deletes.

The only writes are BLACKM's own SQLite catalog and normal terminal output.

## Current command path

```bash
bm vault scan
```

Next planned path:

```text
bm vault scan
  -> bm vault identify
  -> bm vault dedupe --dry-run
  -> bm vault reconcile
  -> bm suno scan
  -> bm suno diff
  -> bm suno rescue --missing
```

M0 is complete when the scanner is stable on the real Mac + 1 TB USB catalog and its inventory can be trusted as the starting point for reconciliation.
