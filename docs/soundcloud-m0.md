# SoundCloud M0 — Authenticate, inventory, audit

This milestone is intentionally read-only after authentication. It does not upload, edit, publish, or delete tracks.

## 1. Local install

```bash
git clone https://github.com/Blackmvmba88/BLACKM.git
cd BLACKM
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

## 2. SoundCloud app credentials

Configure the SoundCloud application with this callback URI (or another local callback that you also export below):

```text
http://127.0.0.1:8765/callback
```

Never commit real credentials.

```bash
export SOUNDCLOUD_CLIENT_ID='...'
export SOUNDCLOUD_CLIENT_SECRET='...'
export SOUNDCLOUD_REDIRECT_URI='http://127.0.0.1:8765/callback'
```

## 3. Authenticate

```bash
bm soundcloud auth
```

BLACKM generates a PKCE verifier/challenge and CSRF state, opens SoundCloud authorization in the browser, receives the authorization code on the local callback, exchanges it for tokens, and stores the tokens at:

```text
~/.config/blackm/soundcloud.json
```

The token file is kept outside the repository and BLACKM attempts to set mode `0600`.

## 4. Verify account

```bash
bm soundcloud me
```

## 5. Inspect track inventory

```bash
bm soundcloud tracks --limit 50
bm soundcloud tracks --limit 0
```

`--limit 0` means all tracks. Pagination follows SoundCloud's returned `next_href`; BLACKM does not synthesize offsets.

## 6. Audit metadata

```bash
bm soundcloud audit --limit 0
bm soundcloud audit --limit 0 --json-out reports/soundcloud-audit.json
```

M0 currently flags these fields when absent:

- title
- description
- genre
- artwork
- metadata artist
- tags

This is an audit policy, not a claim that every field is technically required by SoundCloud.

## M0 success gate

```text
AUTH_OK = true
ME_OK = true
TRACK_INVENTORY_COMPLETE = true
METADATA_AUDIT_WRITTEN = true
REMOTE_MUTATIONS = 0
```

Only after this gate should M1 add controlled metadata writes.

## M1 — next

Planned commands:

```bash
bm soundcloud metadata plan
bm soundcloud metadata apply --dry-run
bm soundcloud metadata apply --yes
```

M1 should generate a proposed patch first, preserve the original values as evidence, and only then permit writes.
