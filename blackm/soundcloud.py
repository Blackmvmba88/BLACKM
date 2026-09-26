from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

API_BASE = "https://api.soundcloud.com"
AUTH_BASE = "https://secure.soundcloud.com"
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8765/callback"


class SoundCloudError(RuntimeError):
    pass


@dataclass(frozen=True)
class SoundCloudSettings:
    client_id: str
    client_secret: str
    redirect_uri: str = DEFAULT_REDIRECT_URI

    @classmethod
    def from_env(cls) -> "SoundCloudSettings":
        client_id = os.getenv("SOUNDCLOUD_CLIENT_ID", "").strip()
        client_secret = os.getenv("SOUNDCLOUD_CLIENT_SECRET", "").strip()
        redirect_uri = os.getenv("SOUNDCLOUD_REDIRECT_URI", DEFAULT_REDIRECT_URI).strip()
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
                "Missing SoundCloud credentials: " + ", ".join(missing)
            )
        return cls(client_id=client_id, client_secret=client_secret, redirect_uri=redirect_uri)


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
        self.path = path or Path.home() / ".config" / "blackm" / "soundcloud.json"

    def load(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, token: dict[str, Any]) -> dict[str, Any]:
        payload = dict(token)
        expires_in = int(payload.get("expires_in", 3600))
        payload["expires_at"] = time.time() + expires_in
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        try:
            self.path.chmod(0o600)
        except OSError:
            pass
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
    return response.json()


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
    return response.json()


def _raise_for_response(response: httpx.Response, prefix: str) -> None:
    if response.is_success:
        return
    body = response.text[:1000]
    raise SoundCloudError(f"{prefix}: HTTP {response.status_code}: {body}")


class SoundCloudClient:
    def __init__(
        self,
        settings: SoundCloudSettings,
        token_store: TokenStore | None = None,
    ) -> None:
        self.settings = settings
        self.token_store = token_store or TokenStore()

    def _valid_token(self) -> dict[str, Any]:
        token = self.token_store.load()
        if not token:
            raise SoundCloudError("Not authenticated. Run: bm soundcloud auth")

        expires_at = float(token.get("expires_at", 0))
        if expires_at > time.time() + 90 and token.get("access_token"):
            return token

        refresh = token.get("refresh_token")
        if not refresh:
            raise SoundCloudError("Token expired and no refresh token is available. Re-authenticate.")

        # SoundCloud refresh tokens are single-use: persist the replacement immediately.
        return self.token_store.save(refresh_token(self.settings, refresh))

    def request(
        self,
        method: str,
        path_or_url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        token = self._valid_token()
        url = path_or_url if path_or_url.startswith("http") else f"{API_BASE}{path_or_url}"
        headers = {
            "accept": "application/json; charset=utf-8",
            "Authorization": f"OAuth {token['access_token']}",
        }
        with httpx.Client(timeout=60, follow_redirects=True) as client:
            response = client.request(method, url, headers=headers, params=params, json=json_body)

        if response.status_code == 401 and token.get("refresh_token"):
            # One refresh/retry only; never enter an auth retry loop.
            token = self.token_store.save(refresh_token(self.settings, token["refresh_token"]))
            headers["Authorization"] = f"OAuth {token['access_token']}"
            with httpx.Client(timeout=60, follow_redirects=True) as client:
                response = client.request(method, url, headers=headers, params=params, json=json_body)

        _raise_for_response(response, f"SoundCloud API request failed ({method} {url})")
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    def me(self) -> dict[str, Any]:
        return self.request("GET", "/me")

    def list_my_tracks(self, max_tracks: int | None = None) -> list[dict[str, Any]]:
        tracks: list[dict[str, Any]] = []
        next_url: str | None = f"{API_BASE}/me/tracks"
        params: dict[str, Any] | None = {"limit": 200, "linked_partitioning": "true"}

        while next_url:
            payload = self.request("GET", next_url, params=params)
            params = None
            if isinstance(payload, list):
                page = payload
                next_url = None
            else:
                page = payload.get("collection", [])
                next_url = payload.get("next_href")

            tracks.extend(page)
            if max_tracks and len(tracks) >= max_tracks:
                return tracks[:max_tracks]

        return tracks


def audit_track(track: dict[str, Any]) -> dict[str, Any]:
    missing: list[str] = []
    for field in ("title", "description", "genre", "artwork_url"):
        if not track.get(field):
            missing.append(field)

    if not track.get("metadata_artist"):
        missing.append("metadata_artist")

    tag_list = track.get("tag_list")
    if not tag_list:
        missing.append("tag_list")

    return {
        "id": track.get("id"),
        "urn": track.get("urn"),
        "title": track.get("title"),
        "permalink_url": track.get("permalink_url"),
        "missing": missing,
        "complete": not missing,
    }


def audit_tracks(tracks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [audit_track(track) for track in tracks]
