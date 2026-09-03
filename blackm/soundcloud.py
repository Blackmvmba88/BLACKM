from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import shlex
import tempfile
import time
import urllib.parse
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx

API_BASE = "https://api.soundcloud.com"
AUTH_BASE = "https://secure.soundcloud.com"
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8765/callback"
CONFIG_DIR = Path.home() / ".config" / "blackm"
APP_CONFIG_PATH = CONFIG_DIR / "soundcloud-app.json"
TOKEN_PATH = CONFIG_DIR / "soundcloud.json"
INVENTORY_SCHEMA = "blackm.soundcloud.inventory.v1"
AUDIT_SCHEMA = "blackm.soundcloud.audit.v1"

WRITABLE_METADATA_FIELDS = frozenset(
    {
        "title",
        "description",
        "genre",
        "tag_list",
        "metadata_artist",
        "label_name",
        "release",
        "release_date",
        "permalink",
    }
)


class SoundCloudError(RuntimeError):
    pass


def _atomic_private_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        path.chmod(0o600)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SoundCloudError(f"Cannot read {label} at {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SoundCloudError(f"Invalid {label} at {path}: expected a JSON object.")
    return value


def validate_loopback_redirect_uri(uri: str) -> urllib.parse.SplitResult:
    parsed = urllib.parse.urlsplit(uri)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise SoundCloudError(
            "The CLI callback must use http://127.0.0.1 or http://localhost."
        )
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise SoundCloudError(
            "The CLI callback cannot contain credentials, query parameters, or a fragment."
        )
    try:
        port = parsed.port
    except ValueError as exc:
        raise SoundCloudError(f"Invalid callback port: {exc}") from exc
    if not port:
        raise SoundCloudError("The CLI callback must include a local port.")
    if parsed.path in {"", "/"}:
        raise SoundCloudError("The CLI callback must include a path such as /callback.")
    return parsed


@dataclass(frozen=True)
class SoundCloudSettings:
    client_id: str
    client_secret: str
    redirect_uri: str = DEFAULT_REDIRECT_URI

    @classmethod
    def load(cls, config_path: Path | None = None) -> "SoundCloudSettings":
        path = config_path or APP_CONFIG_PATH
        stored: dict[str, Any] = {}
        if path.exists():
            stored = _read_json_object(path, "SoundCloud app configuration")

        client_id = os.getenv(
            "SOUNDCLOUD_CLIENT_ID", str(stored.get("client_id", ""))
        ).strip()
        client_secret = os.getenv(
            "SOUNDCLOUD_CLIENT_SECRET", str(stored.get("client_secret", ""))
        ).strip()
        redirect_uri = os.getenv(
            "SOUNDCLOUD_REDIRECT_URI",
            str(stored.get("redirect_uri", DEFAULT_REDIRECT_URI)),
        ).strip()

        missing = [
            name
            for name, value in {
                "SOUNDCLOUD_CLIENT_ID": client_id,
                "SOUNDCLOUD_CLIENT_SECRET": client_secret,
            }.items()
            if not value
        ]
        if missing:
            raise SoundCloudError(
                "Missing SoundCloud credentials: "
                + ", ".join(missing)
                + ". Run: bm soundcloud configure"
            )
        validate_loopback_redirect_uri(redirect_uri)
        return cls(client_id=client_id, client_secret=client_secret, redirect_uri=redirect_uri)

    @classmethod
    def from_env(cls) -> "SoundCloudSettings":
        return cls.load()

    def save(self, path: Path | None = None) -> Path:
        target = path or APP_CONFIG_PATH
        validate_loopback_redirect_uri(self.redirect_uri)
        _atomic_private_json(
            target,
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "redirect_uri": self.redirect_uri,
            },
        )
        return target


@dataclass(frozen=True)
class PKCEPair:
    verifier: str
    challenge: str


def generate_pkce() -> PKCEPair:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return PKCEPair(verifier=verifier, challenge=challenge)


def generate_state() -> str:
    return secrets.token_urlsafe(32)


def build_authorize_url(settings: SoundCloudSettings, pkce: PKCEPair, state: str) -> str:
    params = {
        "client_id": settings.client_id,
        "redirect_uri": settings.redirect_uri,
        "response_type": "code",
        "code_challenge": pkce.challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    return f"{AUTH_BASE}/authorize?{urllib.parse.urlencode(params)}"


class TokenStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or TOKEN_PATH

    def load(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        return _read_json_object(self.path, "SoundCloud token")

    def save(self, token: dict[str, Any]) -> dict[str, Any]:
        payload = dict(token)
        if not payload.get("access_token"):
            raise SoundCloudError("SoundCloud token response did not contain an access token.")
        try:
            expires_in = int(payload.get("expires_in", 3600))
        except (TypeError, ValueError) as exc:
            raise SoundCloudError("SoundCloud returned an invalid token expiry.") from exc
        payload["expires_at"] = time.time() + expires_in
        _atomic_private_json(self.path, payload)
        return payload

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()


def exchange_code(settings: SoundCloudSettings, code: str, verifier: str) -> dict[str, Any]:
    data = {
        "grant_type": "authorization_code",
        "client_id": settings.client_id,
        "client_secret": settings.client_secret,
        "redirect_uri": settings.redirect_uri,
        "code_verifier": verifier,
        "code": code,
    }
    with httpx.Client(timeout=30) as client:
        response = client.post(
            f"{AUTH_BASE}/oauth/token",
            headers={
                "accept": "application/json; charset=utf-8",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data=data,
        )
    _raise_for_response(response, "SoundCloud authorization code exchange failed")
    return _json_response(response, "SoundCloud token response")


def refresh_token(settings: SoundCloudSettings, refresh: str) -> dict[str, Any]:
    data = {
        "grant_type": "refresh_token",
        "client_id": settings.client_id,
        "client_secret": settings.client_secret,
        "refresh_token": refresh,
    }
    with httpx.Client(timeout=30) as client:
        response = client.post(
            f"{AUTH_BASE}/oauth/token",
            headers={
                "accept": "application/json; charset=utf-8",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data=data,
        )
    _raise_for_response(response, "SoundCloud token refresh failed")
    payload = _json_response(response, "SoundCloud refresh response")
    if not payload.get("refresh_token"):
        raise SoundCloudError(
            "SoundCloud did not return the required replacement refresh token; re-authenticate."
        )
    return payload


def _raise_for_response(response: httpx.Response, prefix: str) -> None:
    if response.is_success:
        return
    body = response.text[:1000].replace("\n", " ")
    raise SoundCloudError(f"{prefix}: HTTP {response.status_code}: {body}")


def _json_response(response: httpx.Response, label: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise SoundCloudError(f"{label} was not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise SoundCloudError(f"{label} was not a JSON object.")
    return payload


def _api_url(path_or_url: str) -> str:
    if path_or_url.startswith("/"):
        return f"{API_BASE}{path_or_url}"
    parsed = urllib.parse.urlsplit(path_or_url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "api.soundcloud.com"
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise SoundCloudError(
            "Rejected a SoundCloud pagination URL outside https://api.soundcloud.com."
        )
    return path_or_url


class SoundCloudClient:
    def __init__(
        self,
        settings: SoundCloudSettings,
        token_store: TokenStore | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.token_store = token_store or TokenStore()
        self.transport = transport

    def _refresh(self, refresh: str) -> dict[str, Any]:
        replacement = refresh_token(self.settings, refresh)
        return self.token_store.save(replacement)

    def _valid_token(self) -> dict[str, Any]:
        token = self.token_store.load()
        if not token:
            raise SoundCloudError("Not authenticated. Run: bm soundcloud auth")

        try:
            expires_at = float(token.get("expires_at", 0))
        except (TypeError, ValueError):
            expires_at = 0
        if expires_at > time.time() + 90 and token.get("access_token"):
            return token

        refresh = token.get("refresh_token")
        if not refresh:
            raise SoundCloudError(
                "Token expired and no refresh token is available. Re-authenticate."
            )
        return self._refresh(str(refresh))

    def request(
        self,
        method: str,
        path_or_url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        token = self._valid_token()
        url = _api_url(path_or_url)
        headers = {
            "accept": "application/json; charset=utf-8",
            "Authorization": f"OAuth {token['access_token']}",
        }

        def send() -> httpx.Response:
            with httpx.Client(
                timeout=60,
                follow_redirects=False,
                transport=self.transport,
            ) as client:
                return client.request(
                    method, url, headers=headers, params=params, json=json_body
                )

        response = send()
        if response.status_code == 401 and token.get("refresh_token"):
            token = self._refresh(str(token["refresh_token"]))
            headers["Authorization"] = f"OAuth {token['access_token']}"
            response = send()

        _raise_for_response(response, f"SoundCloud API request failed ({method} {url})")
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise SoundCloudError(
                f"SoundCloud API returned invalid JSON ({method} {url})."
            ) from exc

    def me(self) -> dict[str, Any]:
        payload = self.request("GET", "/me")
        if not isinstance(payload, dict):
            raise SoundCloudError("SoundCloud /me response was not an object.")
        return payload

    def get_track(self, track_ref: str | int) -> dict[str, Any]:
        encoded = urllib.parse.quote(str(track_ref), safe=":")
        payload = self.request("GET", f"/tracks/{encoded}")
        if not isinstance(payload, dict):
            raise SoundCloudError(f"Track {track_ref} response was not an object.")
        return payload

    def update_track_metadata(
        self, track_ref: str | int, changes: dict[str, Any]
    ) -> dict[str, Any]:
        unknown = sorted(set(changes) - WRITABLE_METADATA_FIELDS)
        if unknown:
            raise SoundCloudError(
                "Unsupported metadata fields: " + ", ".join(unknown)
            )
        encoded = urllib.parse.quote(str(track_ref), safe=":")
        payload = self.request(
            "PUT", f"/tracks/{encoded}", json_body={"track": changes}
        )
        if not isinstance(payload, dict):
            raise SoundCloudError(f"Track {track_ref} update response was not an object.")
        return payload

    def list_my_tracks(self, max_tracks: int | None = None) -> list[dict[str, Any]]:
        if max_tracks is not None and max_tracks < 1:
            raise ValueError("max_tracks must be positive or None.")

        tracks: list[dict[str, Any]] = []
        seen_pages: set[str] = set()
        seen_tracks: set[str] = set()
        next_url: str | None = f"{API_BASE}/me/tracks"
        params: dict[str, Any] | None = {"limit": 200, "linked_partitioning": "true"}

        while next_url:
            normalized_url = _api_url(next_url)
            if normalized_url in seen_pages:
                raise SoundCloudError("SoundCloud pagination loop detected.")
            seen_pages.add(normalized_url)

            payload = self.request("GET", normalized_url, params=params)
            params = None
            if isinstance(payload, list):
                page = payload
                next_url = None
            elif isinstance(payload, dict):
                page = payload.get("collection", [])
                next_url = payload.get("next_href")
            else:
                raise SoundCloudError("SoundCloud tracks page was not a list or object.")

            if not isinstance(page, list):
                raise SoundCloudError("SoundCloud tracks collection was not a list.")
            if next_url is not None and not isinstance(next_url, str):
                raise SoundCloudError("SoundCloud next_href was not a string.")

            for track in page:
                if not isinstance(track, dict):
                    raise SoundCloudError("SoundCloud tracks collection contained a non-object.")
                identity = str(track.get("urn") or track.get("id") or "")
                if not identity:
                    raise SoundCloudError("SoundCloud returned a track without an id or URN.")
                if identity in seen_tracks:
                    raise SoundCloudError(
                        f"Duplicate track {identity} encountered during pagination."
                    )
                seen_tracks.add(identity)
                tracks.append(track)
                if max_tracks is not None and len(tracks) >= max_tracks:
                    return tracks

        return tracks


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def build_inventory(
    tracks: list[dict[str, Any]], account: dict[str, Any]
) -> dict[str, Any]:
    return {
        "schema": INVENTORY_SCHEMA,
        "generated_at": utc_now(),
        "source": "GET /me/tracks with linked pagination",
        "account": {
            "id": account.get("id"),
            "urn": account.get("urn"),
            "username": account.get("username"),
            "permalink_url": account.get("permalink_url"),
        },
        "track_count": len(tracks),
        "tracks": tracks,
    }


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _issue(field: str, code: str, severity: str, message: str) -> dict[str, str]:
    return {
        "field": field,
        "code": code,
        "severity": severity,
        "message": message,
    }


def _tag_issues(value: Any) -> list[dict[str, str]]:
    if _blank(value):
        return [_issue("tag_list", "missing", "error", "No tags are set.")]
    if not isinstance(value, str):
        return [_issue("tag_list", "invalid_type", "error", "Tags are not a string.")]
    try:
        tags = shlex.split(value)
    except ValueError:
        return [
            _issue(
                "tag_list",
                "malformed_quotes",
                "warning",
                "Tag quotes are not balanced.",
            )
        ]
    lowered = [tag.casefold() for tag in tags]
    duplicates = sorted(tag for tag, count in Counter(lowered).items() if count > 1)
    if duplicates:
        return [
            _issue(
                "tag_list",
                "duplicates",
                "warning",
                "Duplicate tags: " + ", ".join(duplicates),
            )
        ]
    return []


def _release_state(track: dict[str, Any]) -> tuple[str, list[dict[str, str]]]:
    release = track.get("release")
    parts = (
        track.get("release_year"),
        track.get("release_month"),
        track.get("release_day"),
    )
    present_parts = [part is not None for part in parts]
    issues: list[dict[str, str]] = []
    if any(present_parts) and not all(present_parts):
        issues.append(
            _issue(
                "release_metadata",
                "partial_date",
                "warning",
                "Release year, month, and day are only partially populated.",
            )
        )
        return "inconsistent", issues
    if all(present_parts):
        try:
            date(int(parts[0]), int(parts[1]), int(parts[2]))
        except (TypeError, ValueError):
            issues.append(
                _issue(
                    "release_metadata",
                    "invalid_date",
                    "error",
                    "Release date components do not form a valid date.",
                )
            )
            return "inconsistent", issues
        return "present", issues
    if not _blank(release):
        return "present", issues
    return "not_set", issues


def audit_track(
    track: dict[str, Any], account: dict[str, Any] | None = None
) -> dict[str, Any]:
    account = account or {}
    issues: list[dict[str, str]] = []
    states: dict[str, str] = {}

    for field in ("title", "description", "genre"):
        value = track.get(field)
        if _blank(value):
            states[field] = "missing"
            issues.append(_issue(field, "missing", "error", f"{field} is empty."))
        else:
            states[field] = "present"
            if isinstance(value, str) and value != value.strip():
                issues.append(
                    _issue(
                        field,
                        "outer_whitespace",
                        "warning",
                        f"{field} has leading or trailing whitespace.",
                    )
                )

    tag_issues = _tag_issues(track.get("tag_list"))
    states["tag_list"] = "missing" if any(
        item["code"] == "missing" for item in tag_issues
    ) else "present"
    issues.extend(tag_issues)

    states["artwork"] = "missing" if _blank(track.get("artwork_url")) else "present"
    if states["artwork"] == "missing":
        issues.append(_issue("artwork", "missing", "error", "No artwork is set."))

    explicit_artist = track.get("metadata_artist")
    uploader = (track.get("user") or {}).get("username") if isinstance(
        track.get("user"), dict
    ) else account.get("username")
    if _blank(explicit_artist):
        states["artist_metadata"] = "profile_fallback" if uploader else "missing"
        issues.append(
            _issue(
                "artist_metadata",
                "profile_fallback" if uploader else "missing",
                "info" if uploader else "warning",
                f"metadata_artist is empty; effective artist is {uploader!r}."
                if uploader
                else "Neither metadata_artist nor uploader username is available.",
            )
        )
    else:
        states["artist_metadata"] = "explicit"

    states["label"] = "not_set" if _blank(track.get("label_name")) else "present"
    if states["label"] == "not_set":
        issues.append(
            _issue(
                "label",
                "not_set",
                "info",
                "No label is set; review only when a label applies.",
            )
        )

    publisher = track.get("publisher_metadata")
    if "publisher_metadata" not in track:
        states["publisher"] = "not_exposed_by_api"
    elif not publisher:
        states["publisher"] = "not_set"
    else:
        states["publisher"] = "present"

    permalink_url = track.get("permalink_url")
    if _blank(permalink_url):
        states["permalink"] = "missing"
        issues.append(
            _issue("permalink", "missing", "warning", "No permalink URL is available.")
        )
    else:
        parsed = urllib.parse.urlsplit(str(permalink_url))
        states["permalink"] = "present"
        if parsed.scheme != "https" or parsed.hostname not in {
            "soundcloud.com",
            "www.soundcloud.com",
        }:
            states["permalink"] = "inconsistent"
            issues.append(
                _issue(
                    "permalink",
                    "unexpected_url",
                    "warning",
                    "Permalink URL is not an HTTPS SoundCloud URL.",
                )
            )

    release_state, release_issues = _release_state(track)
    states["release_metadata"] = release_state
    issues.extend(release_issues)

    missing = sorted(
        field for field, status in states.items() if status == "missing"
    )
    inconsistent = sorted(
        field for field, status in states.items() if status == "inconsistent"
    )
    blocking = [item for item in issues if item["severity"] == "error"]
    warnings = [item for item in issues if item["severity"] == "warning"]

    return {
        "id": track.get("id"),
        "urn": track.get("urn"),
        "title": track.get("title"),
        "permalink_url": permalink_url,
        "field_states": states,
        "missing": missing,
        "inconsistent": inconsistent,
        "issues": issues,
        "complete": not blocking,
        "needs_review": bool(blocking or warnings),
        "repairable_fields": sorted(
            {
                item["field"]
                for item in blocking + warnings
                if item["field"] in WRITABLE_METADATA_FIELDS
            }
        ),
        "snapshot": {
            "title": track.get("title"),
            "description": track.get("description"),
            "genre": track.get("genre"),
            "tag_list": track.get("tag_list"),
            "artwork_url": track.get("artwork_url"),
            "metadata_artist": explicit_artist,
            "effective_artist": explicit_artist or uploader,
            "label_name": track.get("label_name"),
            "publisher_metadata": publisher,
            "permalink": track.get("permalink"),
            "permalink_url": permalink_url,
            "release": track.get("release"),
            "release_year": track.get("release_year"),
            "release_month": track.get("release_month"),
            "release_day": track.get("release_day"),
            "isrc": track.get("isrc"),
        },
    }


def audit_tracks(
    tracks: list[dict[str, Any]], account: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    return [audit_track(track, account) for track in tracks]


def build_audit_report(
    tracks: list[dict[str, Any]], account: dict[str, Any]
) -> dict[str, Any]:
    audited = audit_tracks(tracks, account)
    code_counts = Counter(
        f"{issue['field']}:{issue['code']}"
        for item in audited
        for issue in item["issues"]
    )
    complete = sum(1 for item in audited if item["complete"])
    needs_review = sum(1 for item in audited if item["needs_review"])
    return {
        "schema": AUDIT_SCHEMA,
        "generated_at": utc_now(),
        "policy": {
            "blocking_fields": [
                "title",
                "description",
                "genre",
                "tag_list",
                "artwork",
            ],
            "conditional_review": [
                "artist_metadata",
                "label",
                "publisher",
                "permalink",
                "release_metadata",
            ],
            "note": "Conditional fields are reported but are not automatically treated as missing.",
        },
        "account": {
            "id": account.get("id"),
            "urn": account.get("urn"),
            "username": account.get("username"),
            "permalink_url": account.get("permalink_url"),
        },
        "summary": {
            "scanned": len(audited),
            "complete": complete,
            "incomplete": len(audited) - complete,
            "needs_review": needs_review,
            "issue_counts": dict(sorted(code_counts.items())),
        },
        "tracks": audited,
    }
