# SoundCloud M0 → M1

BLACKM treats SoundCloud as a remote system whose persisted state must be verified. M0 is read-only. M1 permits only explicitly planned metadata changes and certifies them with a fresh GET.

## Install

```bash
git clone https://github.com/Blackmvmba88/BLACKM.git
cd BLACKM
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

## Configure the existing SoundCloud app

The SoundCloud app must contain the exact callback URI used by BLACKM:

```text
http://127.0.0.1:8765/callback
```

The safest interactive setup does not expose the client secret in shell history:

```bash
bm soundcloud configure
```

It stores the application credentials outside the repository at:

```text
~/.config/blackm/soundcloud-app.json
```

BLACKM writes the file atomically with mode `0600`. Environment variables override the stored values when needed:

```bash
export SOUNDCLOUD_CLIENT_ID='...'
export SOUNDCLOUD_CLIENT_SECRET='...'
export SOUNDCLOUD_REDIRECT_URI='http://127.0.0.1:8765/callback'
```

Never commit real credentials, tokens, audit output, or evidence output.

## M0: authenticate and inventory

```bash
bm soundcloud auth
bm soundcloud me
bm soundcloud tracks --limit 0
bm soundcloud audit --limit 0
```

The login uses OAuth 2.1 authorization code flow with PKCE S256 and a CSRF `state` value. Access and rotating single-use refresh tokens are stored outside the repository at:

```text
~/.config/blackm/soundcloud.json
```

`tracks --limit 0` follows SoundCloud's returned `next_href` until it is absent. It refuses pagination URLs outside `https://api.soundcloud.com`, fails on loops or duplicate track identities, and writes the complete raw records to:

```text
reports/soundcloud-inventory.json
```

`audit --limit 0` writes both the raw inventory and the structured audit:

```text
reports/soundcloud-inventory.json
reports/soundcloud-audit.json
```

The audit checks:

- title
- description
- genre
- tag list, malformed quoting, and duplicate tags
- artwork
- explicit artist metadata and effective profile fallback
- label when applicable
- publisher metadata availability in the API response
- permalink shape
- release metadata completeness and date validity

Title, description, genre, tags, and artwork are the initial blocking completeness policy. Artist, label, publisher, permalink, and release metadata are conditional review fields so BLACKM does not invent business metadata that may not apply.

M0 succeeds only when:

```text
AUTH_OK = true
ME_OK = true
TRACK_INVENTORY_COMPLETE = true
METADATA_AUDIT_WRITTEN = true
REMOTE_MUTATIONS = 0
```

## M1: plan, dry-run, apply, certify

M1 implements:

```text
READ → PLAN → READ/DRY-RUN → WRITE → READ BACK → COMPARE → CERTIFY
```

Create a patch input. Every desired value must be explicit:

```json
{
  "patches": [
    {
      "track": "soundcloud:tracks:123456789",
      "changes": {
        "description": "Official description",
        "genre": "Reggae",
        "tag_list": "\"Iyari Gomez\" \"BlackMamba Records\" reggae dub",
        "metadata_artist": "Iyari Gomez",
        "label_name": "BlackMamba RECORDS",
        "release_date": "2026-09-03"
      }
    }
  ]
}
```

Build the plan from live SoundCloud state:

```bash
bm soundcloud metadata plan patches.json \
  --out reports/soundcloud-metadata-plan.json
```

Re-read every track. This produces a receipt only if the live state still matches every `before` value in the plan:

```bash
bm soundcloud metadata dry-run reports/soundcloud-metadata-plan.json \
  --receipt-out reports/soundcloud-metadata-dry-run.json
```

Apply requires both the matching receipt and an explicit acknowledgement:

```bash
bm soundcloud metadata apply reports/soundcloud-metadata-plan.json \
  --receipt reports/soundcloud-metadata-dry-run.json \
  --evidence-out reports/soundcloud-metadata-evidence.json \
  --yes
```

For each operation BLACKM:

1. reads the track again;
2. blocks if it drifted since planning;
3. sends only the planned JSON metadata fields;
4. ignores HTTP 200 as proof of persistence;
5. reads the track again;
6. compares every requested field;
7. writes evidence after each operation;
8. stops at the first drift or uncertified result.

Supported JSON repairs in this first M1 slice are `title`, `description`, `genre`, `tag_list`, `metadata_artist`, `label_name`, `release`, `release_date`, and `permalink`.

Artwork needs a separate multipart implementation and is intentionally not writable yet. Publisher metadata is audited but not written because it is not part of SoundCloud's documented track metadata update schema.

## Safety invariants

- no track deletion;
- no audio replacement;
- no automatic publishing or uploads;
- no writes without a plan, matching dry-run receipt, and `--yes`;
- no bulk continuation after drift or failed certification;
- no secrets in Git;
- generated inventories, plans, receipts, and evidence remain ignored by Git.
