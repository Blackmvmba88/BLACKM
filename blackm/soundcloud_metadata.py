from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from blackm.soundcloud import (
    SoundCloudClient,
    SoundCloudError,
    WRITABLE_METADATA_FIELDS,
    utc_now,
)

PLAN_SCHEMA = "blackm.soundcloud.metadata-plan.v1"
RECEIPT_SCHEMA = "blackm.soundcloud.metadata-dry-run.v1"
EVIDENCE_SCHEMA = "blackm.soundcloud.metadata-evidence.v1"


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SoundCloudError(f"Cannot read JSON file {path}: {exc}") from exc


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def canonical_json_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _release_date(track: dict[str, Any]) -> str | None:
    parts = (
        track.get("release_year"),
        track.get("release_month"),
        track.get("release_day"),
    )
    if not all(part is not None for part in parts):
        return None
    try:
        return date(int(parts[0]), int(parts[1]), int(parts[2])).isoformat()
    except (TypeError, ValueError):
        return None


def read_field(track: dict[str, Any], field: str) -> Any:
    if field == "release_date":
        return _release_date(track)
    if field == "permalink":
        if track.get("permalink"):
            return track["permalink"]
        path = urlsplit(str(track.get("permalink_url") or "")).path.rstrip("/")
        return unquote(path.rsplit("/", 1)[-1]) if path else None
    return track.get(field)


def comparable(field: str, value: Any) -> Any:
    if value is None:
        return None
    if field == "tag_list" and isinstance(value, str):
        return " ".join(value.split())
    if field in WRITABLE_METADATA_FIELDS and isinstance(value, str):
        return value.strip()
    return value


def compare_fields(
    track: dict[str, Any], expected: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    return {
        field: {
            "expected": value,
            "actual": read_field(track, field),
            "matches": comparable(field, read_field(track, field))
            == comparable(field, value),
        }
        for field, value in expected.items()
    }


def _validate_changes(changes: Any) -> dict[str, Any]:
    if not isinstance(changes, dict) or not changes:
        raise SoundCloudError("Each patch must contain a non-empty changes object.")
    unknown = sorted(set(changes) - WRITABLE_METADATA_FIELDS)
    if unknown:
        raise SoundCloudError(
            "Unsupported metadata fields: "
            + ", ".join(unknown)
            + ". Artwork requires multipart support and is not enabled in M1."
        )
    normalized: dict[str, Any] = {}
    for field, value in changes.items():
        if not isinstance(value, str):
            raise SoundCloudError(f"{field} must be a string.")
        value = value.strip()
        if not value:
            raise SoundCloudError(f"{field} cannot be empty in an M1 repair plan.")
        if field == "release_date":
            try:
                date.fromisoformat(value)
            except ValueError as exc:
                raise SoundCloudError(
                    "release_date must use YYYY-MM-DD and form a valid date."
                ) from exc
        normalized[field] = value
    return normalized


def load_patches(path: Path) -> list[dict[str, Any]]:
    payload = read_json(path)
    patches = payload.get("patches") if isinstance(payload, dict) else payload
    if not isinstance(patches, list) or not patches:
        raise SoundCloudError(
            "Patch input must be a non-empty list or an object containing patches."
        )
    return patches


def create_plan(
    client: SoundCloudClient,
    account: dict[str, Any],
    patches: list[dict[str, Any]],
) -> dict[str, Any]:
    operations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for patch in patches:
        if not isinstance(patch, dict):
            raise SoundCloudError("Each patch must be a JSON object.")
        track_ref = patch.get("track") or patch.get("track_ref") or patch.get("urn") or patch.get("id")
        if track_ref is None or track_ref == "":
            raise SoundCloudError("Each patch must identify a track.")
        changes = _validate_changes(patch.get("changes"))
        live = client.get_track(track_ref)
        owner = live.get("user") if isinstance(live.get("user"), dict) else {}
        if (
            account.get("id") is not None
            and owner.get("id") != account.get("id")
        ):
            raise SoundCloudError(
                f"Track {track_ref} is not owned by the authenticated account."
            )
        identity = str(live.get("urn") or live.get("id") or track_ref)
        if identity in seen:
            raise SoundCloudError(f"Track {identity} appears more than once in the patch input.")
        seen.add(identity)
        before = {field: read_field(live, field) for field in changes}
        unchanged = sorted(
            field
            for field, value in changes.items()
            if comparable(field, before[field]) == comparable(field, value)
        )
        if unchanged:
            raise SoundCloudError(
                f"Track {identity} contains no-op fields: "
                + ", ".join(unchanged)
            )
        operations.append(
            {
                "track": identity,
                "id": live.get("id"),
                "urn": live.get("urn"),
                "title": live.get("title"),
                "permalink_url": live.get("permalink_url"),
                "before": before,
                "changes": changes,
                "state": "planned",
            }
        )
    return {
        "schema": PLAN_SCHEMA,
        "generated_at": utc_now(),
        "account": {
            "id": account.get("id"),
            "urn": account.get("urn"),
            "username": account.get("username"),
        },
        "operation_count": len(operations),
        "operations": operations,
    }


def validate_plan(plan: Any) -> dict[str, Any]:
    if not isinstance(plan, dict) or plan.get("schema") != PLAN_SCHEMA:
        raise SoundCloudError(f"Expected a {PLAN_SCHEMA} document.")
    operations = plan.get("operations")
    if not isinstance(operations, list) or not operations:
        raise SoundCloudError("The metadata plan has no operations.")
    if plan.get("operation_count") != len(operations):
        raise SoundCloudError("The metadata plan operation_count is inconsistent.")
    for operation in operations:
        if not isinstance(operation, dict) or not operation.get("track"):
            raise SoundCloudError("The metadata plan contains an invalid operation.")
        _validate_changes(operation.get("changes"))
        if not isinstance(operation.get("before"), dict):
            raise SoundCloudError("Each plan operation must contain before values.")
        if set(operation["before"]) != set(operation["changes"]):
            raise SoundCloudError("Plan before fields must exactly match change fields.")
    return plan


def dry_run_plan(
    client: SoundCloudClient, plan: dict[str, Any]
) -> dict[str, Any]:
    validate_plan(plan)
    checks: list[dict[str, Any]] = []
    for operation in plan["operations"]:
        live = client.get_track(operation["track"])
        comparisons = compare_fields(live, operation["before"])
        ready = all(item["matches"] for item in comparisons.values())
        checks.append(
            {
                "track": operation["track"],
                "title": live.get("title"),
                "comparisons": comparisons,
                "ready": ready,
                "status": "ready" if ready else "blocked_by_drift",
            }
        )
    ready_count = sum(1 for check in checks if check["ready"])
    return {
        "schema": RECEIPT_SCHEMA,
        "generated_at": utc_now(),
        "plan_sha256": canonical_json_sha256(plan),
        "operation_count": len(checks),
        "ready_count": ready_count,
        "all_ready": ready_count == len(checks),
        "checks": checks,
    }


def validate_receipt(plan: dict[str, Any], receipt: Any) -> dict[str, Any]:
    if not isinstance(receipt, dict) or receipt.get("schema") != RECEIPT_SCHEMA:
        raise SoundCloudError(f"Expected a {RECEIPT_SCHEMA} document.")
    if receipt.get("plan_sha256") != canonical_json_sha256(plan):
        raise SoundCloudError("Dry-run receipt does not match this plan.")
    if not receipt.get("all_ready"):
        raise SoundCloudError("Dry-run receipt is not ready for all operations.")
    if receipt.get("operation_count") != len(plan["operations"]):
        raise SoundCloudError("Dry-run receipt operation count does not match the plan.")
    return receipt


def apply_plan(
    client: SoundCloudClient,
    plan: dict[str, Any],
    receipt: dict[str, Any],
    evidence_path: Path,
) -> dict[str, Any]:
    validate_plan(plan)
    validate_receipt(plan, receipt)
    evidence: dict[str, Any] = {
        "schema": EVIDENCE_SCHEMA,
        "started_at": utc_now(),
        "plan_sha256": canonical_json_sha256(plan),
        "receipt_sha256": canonical_json_sha256(receipt),
        "operation_count": len(plan["operations"]),
        "results": [],
        "summary": {
            "certified": 0,
            "failed": 0,
            "blocked": 0,
        },
    }
    write_json(evidence_path, evidence)

    for operation in plan["operations"]:
        result: dict[str, Any] = {
            "track": operation["track"],
            "title": operation.get("title"),
            "changes": operation["changes"],
            "started_at": utc_now(),
        }
        live_before = client.get_track(operation["track"])
        preconditions = compare_fields(live_before, operation["before"])
        result["read_before"] = {
            field: read_field(live_before, field) for field in operation["changes"]
        }
        result["preconditions"] = preconditions

        if not all(item["matches"] for item in preconditions.values()):
            result["status"] = "blocked_by_drift"
            result["certified"] = False
            evidence["summary"]["blocked"] += 1
            evidence["results"].append(result)
            write_json(evidence_path, evidence)
            break

        write_error: str | None = None
        response: dict[str, Any] | None = None
        try:
            response = client.update_track_metadata(
                operation["track"], operation["changes"]
            )
        except SoundCloudError as exc:
            write_error = str(exc)

        try:
            live_after = client.get_track(operation["track"])
            comparisons = compare_fields(live_after, operation["changes"])
            result["read_back"] = {
                field: read_field(live_after, field)
                for field in operation["changes"]
            }
            result["comparisons"] = comparisons
            certified = all(item["matches"] for item in comparisons.values())
        except SoundCloudError as exc:
            result["read_back_error"] = str(exc)
            comparisons = {}
            certified = False

        result["write_response"] = (
            {
                field: read_field(response, field)
                for field in operation["changes"]
            }
            if response is not None
            else None
        )
        if write_error:
            result["write_error"] = write_error
        result["certified"] = certified
        if certified:
            result["status"] = (
                "certified_after_write_error" if write_error else "certified"
            )
            evidence["summary"]["certified"] += 1
        else:
            result["status"] = "not_certified"
            evidence["summary"]["failed"] += 1
        result["finished_at"] = utc_now()
        evidence["results"].append(result)
        write_json(evidence_path, evidence)
        if not certified:
            break

    evidence["finished_at"] = utc_now()
    evidence["all_certified"] = (
        evidence["summary"]["certified"] == evidence["operation_count"]
    )
    write_json(evidence_path, evidence)
    return evidence
