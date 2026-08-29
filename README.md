# BLACKM — BlackMamba Music OS

> **Listening is the primary human operation. Everything else is infrastructure.**

BLACKM is the local-first music operating system for **BlackMamba RECORDS / Iyari Gomez**.

It is designed around one hard requirement: the artist should spend human attention on the part that matters most — **listening and deciding** — while the system inventories, protects, prepares, verifies, packages and eventually distributes the catalog in parallel.

BLACKM is not just a player, downloader, uploader or catalog manager. It is the coordination layer between:

- local masters on macOS;
- external storage / USB archives;
- Suno generations;
- SoundCloud releases;
- artwork, lyrics, metadata and video;
- human curation;
- automated pipelines;
- catalog intelligence.

---

## Mission

BLACKM must answer four questions for every track:

1. **What is it?**
2. **Where is it?**
3. **Is every valuable asset safely under our control?**
4. **What should happen next?**

If music exists, BLACKM should know where it is.

If it matters, BLACKM should own a verified local copy.

---

# Core philosophy

## 1. Local first

Remote platforms are sources and distribution surfaces — **never the source of truth**.

Before downloading anything from the internet, BLACKM scans what already exists on:

- the Mac;
- the 1 TB USB archive;
- configured local folders;
- the consolidated BlackMamba catalog.

Only missing or incomplete material is recovered from remote platforms.

```text
MAC ---------\
              \
USB -----------+--> SCAN --> IDENTIFY --> RECONCILE --> MASTER CATALOG
              /                                      |
OTHER PATHS --/                                       v
                                                    SUNO
                                                      |
                                                ONLY MISSING
```

---

## 2. A track is a package, not an audio file

The atomic unit of BLACKM is the **Track Package**.

A complete track can contain:

```text
Track Name/
├── audio/
│   ├── master.wav
│   └── publish.mp3
├── artwork/
│   ├── original.jpg
│   ├── cover_square.jpg
│   ├── instagram.jpg
│   └── story.jpg
├── lyrics/
│   ├── lyrics.txt
│   └── lyrics.lrc
├── video/
│   ├── master.mp4
│   ├── reel.mp4
│   └── visualizer.mp4
├── metadata/
│   ├── manifest.json
│   └── source.json
└── provenance/
    ├── fingerprints.json
    └── checksums.sha256
```

Not every track needs every derivative immediately, but BLACKM must know whether an asset is:

- present;
- missing;
- optional;
- unavailable at source;
- invalid;
- pending generation.

---

# Phase 0 — Catalog Rescue

**No automation matters if the catalog can disappear first.**

Phase 0 consolidates the existing BlackMamba catalog before attempting aggressive publishing automation.

## Objectives

```text
REMOTE_ONLY_TRACKS = 0
UNKNOWN_LOCAL_AUDIO = 0
UNVERIFIED_VALUABLE_AUDIO = 0
UNRESOLVED_EXACT_DUPLICATES = 0
CATALOG_COVERAGE = 100%
```

## Phase 0 sequence

```bash
bm vault scan
bm vault identify
bm vault dedupe --dry-run
bm vault reconcile

bm suno scan
bm suno diff
bm suno rescue --missing

bm vault verify
bm vault consolidate
```

Eventually:

```bash
bm vault sync
```

`vault sync` is the safe macro for:

```text
scan
-> identify
-> reconcile
-> remote diff
-> rescue missing assets
-> verify
```

---

# Local inventory

`bm vault scan` begins **read-only**.

It must not move, rename or delete source files during discovery.

Example configured sources:

```text
/Volumes/<BLACKMAMBA_USB>
~/Music
~/Downloads
~/Documents
~/Desktop
<custom BlackMamba folders>
```

The scan records at minimum:

```text
path
filename
extension
size
checksum
duration
sample rate
channels
bit depth
created / modified timestamps
```

When possible, audio fingerprinting is added for identity matching beyond filenames.

---

# Identity: duplicate != version

BLACKM must never treat similar names as proof of duplication.

## Exact duplicate

Same content checksum:

```text
SAME BYTES -> EXACT DUPLICATE
```

## Likely same recording

Matching audio fingerprint with different encoding/container:

```text
SAME RECORDING -> ALTERNATE ENCODE
```

## Alternate version

Different musical/audio content:

```text
REMIX / REMASTER / EXTENDED / ALT GENERATION -> VERSION
```

Destructive deduplication is forbidden until identity has been verified.

---

# Suno Rescue

Suno is treated as a **remote source**, not archival storage.

## Recovery preference

```text
WAV available?
├── YES -> acquire WAV
└── NO  -> acquire MP3

then:
├── artwork
├── lyrics
├── source metadata
├── source ID / URL reference
└── provenance
```

BLACKM compares the remote Suno inventory against the local catalog before downloading.

```bash
bm suno scan
bm suno diff
```

Example output:

```text
SUNO TRACKS          2840
SAFE LOCAL           1937
MISSING AUDIO         482
MISSING ARTWORK       203
MISSING LYRICS        118
REMOTE ONLY           100
```

Recovery can then be targeted:

```bash
bm suno rescue --remote-only
bm suno rescue --missing-audio
bm suno rescue --missing-art
bm suno rescue --missing-lyrics
bm suno rescue --missing
```

No song should be deleted remotely merely because it appears to exist locally.

A destructive action requires a verified local package first.

---

# Verification

`downloaded = true` is not sufficient evidence.

A secured audio asset should satisfy checks such as:

```text
file exists
size > 0
decodes successfully
checksum recorded
identity linked to catalog
```

A complete Track Package may additionally require:

```text
artwork accounted for
lyrics accounted for
manifest present
provenance present
```

Only then can BLACKM mark:

```text
BACKUP_VERIFIED = TRUE
```

---

# The Player is the control surface

BLACKM is built around a simple observation:

> The human decision that remains essential is listening.

The system should therefore use listening time as processing time.

While a track is playing, BLACKM can simultaneously:

```text
identify track
verify local master
check remote/local differences
secure missing assets
normalize metadata
build artwork derivatives
prepare video derivatives
validate package
update catalog state
prepare publication jobs
```

Human operation stays simple:

```text
PLAY
  |
LISTEN
  |
RATE / DECIDE
  |
NEXT
```

---

# Five-star curation

BLACKM supports a first-class 1–5 star rating.

```text
★★★★★  masterpiece / highest-priority catalog
★★★★☆  strong release
★★★☆☆  useful / normal catalog
★★☆☆☆  hold / weak
★☆☆☆☆  reject candidate
```

Rating and publication status are separate concepts.

A track may be published and later receive a quality rating during a second-pass SoundCloud review.

Example commands:

```bash
bm listen --unrated
bm play --rating 5
bm catalog list --rating 5
bm catalog list --rating 5 --missing-video
```

---

# Two-stage listening model

BLACKM supports different listening contexts.

```text
SUNO
GENERATED
   |
FIRST AUDITION
   |
APPROVE / HOLD / REJECT
   |
RESCUE + PACKAGE
   |
SOUNDCLOUD
   |
SECOND AUDITION
   |
★ QUALITY RATING
```

This matters because SoundCloud listening can represent **quality control over already-filtered material**, not first-pass discovery.

---

# HUD — second layer over Suno / SoundCloud

BLACKM should not need to replace every existing player.

A browser HUD / extension can act as a second operational layer while Suno or SoundCloud continues playing the audio.

The HUD should show only actionable information:

```text
BLACKMAMBA HUD
--------------------------------
★★★★★

LOCAL MASTER     YES
AUDIO            WAV
ARTWORK          YES
LYRICS           YES
METADATA         VALID
BACKUP           VERIFIED

SUNO             RESCUED
SOUNDCLOUD       PUBLISHED

[ APPROVE ] [ HOLD ] [ REJECT ]
```

Responsibilities of the browser layer:

```text
detect platform
detect current track
show catalog state
capture rating
capture approve / hold / reject
send actions to local BLACKM agent
```

Heavy work remains in the local agent.

---

# Parallel pipeline

The pipeline runs concurrently with listening.

```text
                    CURRENT TRACK
                         |
             +-----------+-----------+
             |                       |
             v                       v
          PLAYER                   PIPELINE
             |                       |
             |          +------------+------------+
             |          |            |            |
             v          v            v            v
          LISTEN      AUDIO       ARTWORK       LYRICS
             |          \            |            /
             |           +-----------+-----------+
             |                       |
             v                       v
       HUMAN VERDICT              PACKAGE
             |                       |
             |                META / VIDEO
             |                       |
             +-----------+-----------+
                         |
                         v
                    RELEASE GATE
```

Preparing assets does **not** authorize publication.

Publication remains gated by catalog state and policy.

---

# Observer / Commissioning layer

The Observer does **not** invent the workflow by watching the user.

The workflow already has:

- a defined goal;
- a rigorous plan;
- a checklist;
- expected conditions;
- success criteria;
- known fallback branches.

The Observer watches **reality** and compares it with the specification.

```text
SPECIFICATION
     |
EXPECTED CONDITIONS
     |
REAL EXECUTION
     |
OBSERVE
     |
COMPARE
     |
KNOWN CASE? -------- YES --> HANDLE --> CONTINUE
     |
     NO
     v
PAUSE AFFECTED BRANCH
     |
RECONCILE
     |
VALIDATE
     |
CERTIFY NEW HANDLER
```

The system should become autonomous by eliminating unknown conditions, not by guessing.

## Workflow states

```text
DESIGNED
  -> PREFLIGHT
  -> COMMISSIONING
  -> RECONCILING
  -> CERTIFIED
  -> AUTONOMOUS
```

If a provider changes behavior:

```text
AUTONOMOUS
  -> ANOMALY
  -> RECOMMISSION AFFECTED STEP
  -> AUTONOMOUS
```

---

# Evidence-based checklists

A green check must have evidence.

Example:

```text
✓ Metadata injected

Evidence:
  title tag present
  artist tag present
  artwork embedded
  lyrics embedded / accounted for
  validated_at: <timestamp>
```

Planned commands:

```bash
bm task inspect <workflow>
bm task commission <workflow>
bm task status <workflow>
bm task diff <workflow>
bm task certify <workflow>
bm task run <workflow> --auto
bm why <step>
```

---

# Catalog state model

Suggested high-level states:

```text
DISCOVERED
LOCAL_FOUND
REMOTE_ONLY
INCOMPLETE
SECURED
UNHEARD
LISTENING
APPROVED
HELD
REJECTED
PACKAGED
PUBLISHED
RATED
ARCHIVED
ERROR
```

States should be explicit and auditable.

---

# Track manifest

Illustrative manifest:

```json
{
  "track_id": "blackm-uuid",
  "title": "My Light",
  "artist": "Iyari Gomez",
  "label": "BlackMamba RECORDS",
  "source": "suno",
  "source_id": "remote-id",
  "audio": {
    "master_format": "wav",
    "verified": true
  },
  "assets": {
    "artwork": true,
    "lyrics": true,
    "video": true
  },
  "curation": {
    "rating": 5,
    "decision": "approved"
  },
  "distribution": {
    "soundcloud": "published",
    "spotify": "pending"
  }
}
```

Schema details will evolve, but provenance and identity must remain stable.

---

# Command grammar

BLACKM uses a compact CLI grammar:

```text
bm <domain> <action> [options]
```

Primary domains:

```text
bm vault
bm catalog
bm suno
bm listen
bm play
bm music
bm art
bm video
bm social
bm metrics
bm task
bm pipeline
bm system
```

Examples:

```bash
bm vault scan
bm vault verify
bm suno diff
bm suno rescue --missing
bm listen --unrated
bm catalog list --rating 5
bm pipeline status
bm system doctor
```

A complex plan should eventually collapse into a short command path.

> If a repeated procedure cannot be expressed as a small set of commands, it is not encapsulated enough yet.

---

# Planned daily operation

The long-term target is intentionally boring:

```bash
bm listen
```

While the artist listens, BLACKM handles the infrastructure.

Typical human controls:

```text
SPACE    play / pause
A        approve
H        hold
X        reject
1..5     rating
M        mark hook / moment
NEXT     next track
```

Markers captured during listening can later feed automatic short-video extraction and promotion workflows.

---

# Metadata

BLACKM should preserve the original master and generate publishing derivatives as needed.

General rule:

```text
WAV = preserved master
MP3 = distribution derivative when appropriate
```

Metadata can include:

```text
title
artist
album / collection
label
artwork
lyrics
genre / subgenre
language
BPM / key when known
copyright
source provenance
ISRC when available
```

---

# Visual derivatives

One artwork asset can become many publishing assets:

```text
original cover
square cover
Instagram pair / double cover
9:16 story
thumbnail
animated cover
reel
short
visualizer
lyric video
```

The goal is **reuse**, not repetitive manual export work.

---

# Safety rules

BLACKM begins conservative.

## Non-destructive by default

The following operations must be safe/read-only until explicitly escalated:

```text
scan
identify
reconcile
diff
verify
rate
classify
```

## Destructive gates

Remote deletion, local deletion or irreversible cleanup requires:

```text
identity verified
AND local package verified
AND provenance recorded
AND explicit policy permits action
```

Rejected does not automatically mean deleted.

---

# Initial architecture

```text
BLACKM/
├── blackm/
│   ├── cli/
│   ├── catalog/
│   ├── vault/
│   ├── identity/
│   ├── observer/
│   ├── pipelines/
│   ├── adapters/
│   │   ├── suno/
│   │   └── soundcloud/
│   ├── metadata/
│   ├── artwork/
│   ├── video/
│   └── player/
├── extension/
│   └── hud/
├── schemas/
├── workflows/
├── tests/
├── docs/
├── pyproject.toml
└── README.md
```

The exact implementation can evolve while these boundaries remain stable.

---

# Proposed implementation stack

Initial direction:

```text
Python           orchestration / CLI / catalog
SQLite           local catalog database
FFmpeg           audio/video inspection and rendering
Mutagen          audio metadata
Pillow           image derivatives
Playwright       browser automation where allowed/needed
WebExtension     Suno / SoundCloud HUD
Audio fingerprinting  identity reconciliation
```

Adapters must isolate provider-specific behavior from the core catalog.

---

# Roadmap

## M0 — Genesis

- [x] Define mission
- [x] Define local-first principle
- [x] Define Track Package
- [x] Define Phase 0 rescue strategy
- [x] Define Player + HUD concept
- [x] Define five-star curation
- [x] Define Observer / commissioning philosophy
- [x] Define command grammar

## M1 — Vault scanner

- [ ] CLI skeleton
- [ ] Configured source paths
- [ ] Read-only recursive scanner
- [ ] Audio technical inspection
- [ ] Checksum index
- [ ] SQLite catalog
- [ ] `bm vault scan`
- [ ] `bm vault status`

## M2 — Identity + reconciliation

- [ ] Exact duplicate detection
- [ ] Audio fingerprint support
- [ ] Version model
- [ ] Asset association
- [ ] `bm vault identify`
- [ ] `bm vault reconcile`
- [ ] `bm vault dedupe --dry-run`

## M3 — Suno inventory + rescue

- [ ] Remote inventory adapter
- [ ] Local/remote diff
- [ ] WAV preference
- [ ] MP3 fallback
- [ ] Artwork recovery
- [ ] Lyrics recovery
- [ ] Manifest/provenance generation
- [ ] Verification

## M4 — Player + curation

- [ ] Local player queue
- [ ] 1–5 star rating
- [ ] approve / hold / reject
- [ ] unrated queue
- [ ] five-star queue
- [ ] marker timestamps

## M5 — Browser HUD

- [ ] SoundCloud track detection
- [ ] Suno track detection
- [ ] catalog lookup
- [ ] rating controls
- [ ] backup state
- [ ] pipeline status

## M6 — Parallel packaging

- [ ] metadata injection
- [ ] artwork derivatives
- [ ] animated artwork
- [ ] video templates
- [ ] SoundCloud package
- [ ] publication queue

## M7 — Commissioned autonomy

- [ ] workflow specs
- [ ] evidence-backed checklist
- [ ] mismatch detection
- [ ] known handlers
- [ ] commissioning state
- [ ] certified autonomous execution

---

# Definition of done for Phase 0

Phase 0 is complete when BLACKM can truthfully report:

```text
BLACKMAMBA CATALOG

Local sources scanned        ✓
Unique tracks identified     ✓
Versions preserved           ✓
Remote Suno catalog indexed  ✓
Remote-only tracks           0
Valuable audio verified      ✓
Artwork accounted for        ✓
Lyrics accounted for         ✓
Unknown local WAV files      0
Critical unknowns            0
```

At that point, even if a remote service disappears, the catalog survives.

---

# The end state

The desired experience is not a giant administration dashboard.

It is a player.

```text
YOU
listen -> decide -> rate -> next

BLACKM
everything else
```

That is the contract.
