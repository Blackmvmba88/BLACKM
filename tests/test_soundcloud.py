from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

import httpx

from blackm.soundcloud import (
    SoundCloudClient,
    SoundCloudError,
    SoundCloudSettings,
    TokenStore,
    audit_track,
    build_audit_report,
    generate_pkce,
    validate_loopback_redirect_uri,
)
from blackm.soundcloud_metadata import (
    apply_plan,
    canonical_json_sha256,
    create_plan,
    dry_run_plan,
)


class SoundCloudCoreTests(unittest.TestCase):
    def settings(self) -> SoundCloudSettings:
        return SoundCloudSettings("client", "secret")

    def token_store(self, root: Path) -> TokenStore:
        store = TokenStore(root / "token.json")
        store.save(
            {
                "access_token": "access",
                "refresh_token": "refresh",
                "expires_in": 3600,
            }
        )
        return store

    def test_pkce_is_s256_pair(self) -> None:
        pair = generate_pkce()
        self.assertGreaterEqual(len(pair.verifier), 43)
        self.assertLessEqual(len(pair.verifier), 128)
        self.assertNotIn("=", pair.challenge)

    def test_redirect_must_be_loopback_and_exact_shape(self) -> None:
        parsed = validate_loopback_redirect_uri(
            "http://127.0.0.1:8765/callback"
        )
        self.assertEqual(parsed.port, 8765)
        for value in (
            "https://127.0.0.1:8765/callback",
            "http://example.com:8765/callback",
            "http://127.0.0.1:8765/",
            "http://127.0.0.1:8765/callback?secret=x",
        ):
            with self.subTest(value=value):
                with self.assertRaises(SoundCloudError):
                    validate_loopback_redirect_uri(value)

    def test_linked_pagination_returns_every_unique_track(self) -> None:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            self.assertEqual(request.headers["Authorization"], "OAuth access")
            if request.url.path == "/me/tracks" and "cursor" not in request.url.params:
                return httpx.Response(
                    200,
                    json={
                        "collection": [{"id": 1, "urn": "soundcloud:tracks:1"}],
                        "next_href": "https://api.soundcloud.com/me/tracks?cursor=next",
                    },
                )
            return httpx.Response(
                200,
                json={
                    "collection": [{"id": 2, "urn": "soundcloud:tracks:2"}],
                    "next_href": None,
                },
            )

        with tempfile.TemporaryDirectory() as temp:
            client = SoundCloudClient(
                self.settings(),
                self.token_store(Path(temp)),
                transport=httpx.MockTransport(handler),
            )
            tracks = client.list_my_tracks()

        self.assertEqual([track["id"] for track in tracks], [1, 2])
        self.assertEqual(len(calls), 2)
        self.assertIn("linked_partitioning=true", calls[0])
        self.assertIn("limit=200", calls[0])

    def test_pagination_rejects_external_next_href(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "collection": [{"id": 1}],
                    "next_href": "https://attacker.example/steal",
                },
            )

        with tempfile.TemporaryDirectory() as temp:
            client = SoundCloudClient(
                self.settings(),
                self.token_store(Path(temp)),
                transport=httpx.MockTransport(handler),
            )
            with self.assertRaisesRegex(SoundCloudError, "outside"):
                client.list_my_tracks()

    def test_duplicate_track_makes_inventory_fail_closed(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if "cursor" not in request.url.params:
                return httpx.Response(
                    200,
                    json={
                        "collection": [{"id": 1}],
                        "next_href": "https://api.soundcloud.com/me/tracks?cursor=2",
                    },
                )
            return httpx.Response(
                200, json={"collection": [{"id": 1}], "next_href": None}
            )

        with tempfile.TemporaryDirectory() as temp:
            client = SoundCloudClient(
                self.settings(),
                self.token_store(Path(temp)),
                transport=httpx.MockTransport(handler),
            )
            with self.assertRaisesRegex(SoundCloudError, "Duplicate"):
                client.list_my_tracks()

    def test_audit_distinguishes_required_and_conditional_fields(self) -> None:
        track = {
            "id": 1,
            "title": "Song",
            "description": "",
            "genre": "Reggae",
            "tag_list": '"BlackMamba Records" reggae reggae',
            "artwork_url": None,
            "permalink_url": "https://soundcloud.com/iyari/song",
            "user": {"username": "Iyari Gomez"},
            "release_year": 2026,
        }
        item = audit_track(track)
        self.assertIn("description", item["missing"])
        self.assertIn("artwork", item["missing"])
        self.assertEqual(
            item["field_states"]["artist_metadata"], "profile_fallback"
        )
        self.assertEqual(item["field_states"]["label"], "not_set")
        codes = {issue["code"] for issue in item["issues"]}
        self.assertIn("duplicates", codes)
        self.assertIn("partial_date", codes)
        self.assertFalse(item["complete"])

    def test_audit_report_counts_every_track(self) -> None:
        complete = {
            "id": 1,
            "title": "Song",
            "description": "Description",
            "genre": "House",
            "tag_list": "house",
            "artwork_url": "https://i1.sndcdn.com/art.jpg",
            "metadata_artist": "Iyari Gomez",
            "permalink_url": "https://soundcloud.com/iyari/song",
        }
        report = build_audit_report([complete], {"username": "Iyari Gomez"})
        self.assertEqual(report["summary"]["scanned"], 1)
        self.assertEqual(report["summary"]["complete"], 1)


class FakeClient:
    def __init__(self, track: dict) -> None:
        self.track = dict(track)
        self.update_calls = 0

    def get_track(self, track_ref: str) -> dict:
        return json.loads(json.dumps(self.track))

    def update_track_metadata(self, track_ref: str, changes: dict) -> dict:
        self.update_calls += 1
        self.track.update(changes)
        return json.loads(json.dumps(self.track))


class SoundCloudMetadataTests(unittest.TestCase):
    def base_track(self) -> dict:
        return {
            "id": 7,
            "urn": "soundcloud:tracks:7",
            "title": "Original",
            "genre": "Unknown",
            "tag_list": "old",
            "permalink_url": "https://soundcloud.com/iyari/original",
            "user": {"id": 42, "username": "Iyari Gomez"},
        }

    def test_plan_dry_run_apply_and_read_back_certify(self) -> None:
        client = FakeClient(self.base_track())
        account = {"id": 42, "username": "Iyari Gomez"}
        plan = create_plan(
            client,
            account,
            [
                {
                    "track": "soundcloud:tracks:7",
                    "changes": {"genre": "Reggae", "tag_list": "reggae dub"},
                }
            ],
        )
        receipt = dry_run_plan(client, plan)
        self.assertTrue(receipt["all_ready"])
        self.assertEqual(receipt["plan_sha256"], canonical_json_sha256(plan))

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "evidence.json"
            evidence = apply_plan(client, plan, receipt, path)
            persisted = json.loads(path.read_text())

        self.assertEqual(client.update_calls, 1)
        self.assertTrue(evidence["all_certified"])
        self.assertTrue(persisted["results"][0]["certified"])

    def test_apply_blocks_when_track_changed_after_dry_run(self) -> None:
        client = FakeClient(self.base_track())
        plan = create_plan(
            client,
            {"id": 42},
            [{"track": 7, "changes": {"genre": "Reggae"}}],
        )
        receipt = dry_run_plan(client, plan)
        client.track["genre"] = "House"

        with tempfile.TemporaryDirectory() as temp:
            evidence = apply_plan(
                client, plan, receipt, Path(temp) / "evidence.json"
            )

        self.assertEqual(client.update_calls, 0)
        self.assertFalse(evidence["all_certified"])
        self.assertEqual(evidence["summary"]["blocked"], 1)

    def test_http_200_is_not_enough_to_certify(self) -> None:
        class DiscardingClient(FakeClient):
            def update_track_metadata(self, track_ref: str, changes: dict) -> dict:
                self.update_calls += 1
                response = dict(self.track)
                response.update(changes)
                return response

        client = DiscardingClient(self.base_track())
        plan = create_plan(
            client,
            {"id": 42},
            [{"track": 7, "changes": {"genre": "Reggae"}}],
        )
        receipt = dry_run_plan(client, plan)

        with tempfile.TemporaryDirectory() as temp:
            evidence = apply_plan(
                client, plan, receipt, Path(temp) / "evidence.json"
            )

        self.assertEqual(client.update_calls, 1)
        self.assertFalse(evidence["all_certified"])
        self.assertEqual(evidence["summary"]["failed"], 1)


if __name__ == "__main__":
    unittest.main()
