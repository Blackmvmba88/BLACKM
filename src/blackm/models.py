from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(slots=True)
class AudioInfo:
    duration_seconds: float | None = None
    sample_rate: int | None = None
    channels: int | None = None
    bit_depth: int | None = None

    def to_dict(self) -> dict[str, object | None]:
        return asdict(self)


@dataclass(slots=True)
class AssetRecord:
    source_root: str
    path: str
    relative_path: str
    filename: str
    extension: str
    kind: str
    size_bytes: int
    sha256: str
    modified_ns: int
    created_ns: int
    audio: AudioInfo | None = None

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        return data


@dataclass(slots=True)
class ScanWarning:
    path: str
    error: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(slots=True)
class ScanResult:
    records: list[AssetRecord]
    warnings: list[ScanWarning]

    @property
    def duplicate_groups(self) -> dict[str, list[AssetRecord]]:
        groups: dict[str, list[AssetRecord]] = {}
        for record in self.records:
            groups.setdefault(record.sha256, []).append(record)
        return {sha: items for sha, items in groups.items() if len(items) > 1}
