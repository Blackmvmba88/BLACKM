from __future__ import annotations

import hashlib
import os
import wave
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .constants import SKIP_DIR_NAMES, classify_asset
from .models import AssetRecord, AudioInfo, ScanResult, ScanWarning

_HASH_CHUNK_BYTES = 4 * 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_wav(path: Path) -> AudioInfo | None:
    if path.suffix.lower() != ".wav":
        return None
    try:
        with wave.open(str(path), "rb") as stream:
            rate = stream.getframerate()
            frames = stream.getnframes()
            return AudioInfo(
                duration_seconds=(frames / rate) if rate else None,
                sample_rate=rate or None,
                channels=stream.getnchannels() or None,
                bit_depth=(stream.getsampwidth() * 8) or None,
            )
    except (wave.Error, EOFError, OSError):
        return AudioInfo()


def _created_ns(stat: os.stat_result) -> int:
    birth = getattr(stat, "st_birthtime", None)
    if birth is not None:
        return int(birth * 1_000_000_000)
    return stat.st_ctime_ns


def _discover(root: Path, warnings: list[ScanWarning]) -> list[tuple[Path, Path]]:
    found: list[tuple[Path, Path]] = []

    if not root.exists():
        warnings.append(ScanWarning(str(root), "source root does not exist"))
        return found

    if root.is_file():
        if classify_asset(root):
            found.append((root.parent, root))
        return found

    def onerror(error: OSError) -> None:
        warnings.append(ScanWarning(error.filename or str(root), str(error)))

    for current, dirnames, filenames in os.walk(root, topdown=True, followlinks=False, onerror=onerror):
        dirnames[:] = [name for name in dirnames if name not in SKIP_DIR_NAMES]
        current_path = Path(current)
        for filename in filenames:
            path = current_path / filename
            if classify_asset(path):
                found.append((root, path))
    return found


def _inspect_file(source_root: Path, path: Path) -> AssetRecord:
    kind = classify_asset(path)
    if kind is None:
        raise ValueError(f"unsupported asset: {path}")

    before = path.stat()
    checksum = sha256_file(path)
    audio = inspect_wav(path) if kind == "audio" else None
    after = path.stat()

    if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
        raise RuntimeError("file changed while being scanned; retry on next scan")

    try:
        relative = path.relative_to(source_root)
    except ValueError:
        relative = Path(path.name)

    return AssetRecord(
        source_root=str(source_root),
        path=str(path),
        relative_path=str(relative),
        filename=path.name,
        extension=path.suffix.lower(),
        kind=kind,
        size_bytes=after.st_size,
        sha256=checksum,
        modified_ns=after.st_mtime_ns,
        created_ns=_created_ns(after),
        audio=audio,
    )


def scan_roots(roots: list[Path], workers: int | None = None) -> ScanResult:
    warnings: list[ScanWarning] = []
    candidates: list[tuple[Path, Path]] = []

    normalized: list[Path] = []
    seen_roots: set[str] = set()
    for root in roots:
        expanded = root.expanduser().absolute()
        key = os.path.normcase(str(expanded))
        if key not in seen_roots:
            seen_roots.add(key)
            normalized.append(expanded)

    for root in normalized:
        candidates.extend(_discover(root, warnings))

    if workers is None:
        workers = min(8, (os.cpu_count() or 2) + 2)
    workers = max(1, workers)

    records: list[AssetRecord] = []
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="blackm-scan") as pool:
        futures = {
            pool.submit(_inspect_file, source_root, path): path
            for source_root, path in candidates
        }
        for future in as_completed(futures):
            path = futures[future]
            try:
                records.append(future.result())
            except (OSError, RuntimeError, ValueError) as error:
                warnings.append(ScanWarning(str(path), str(error)))

    records.sort(key=lambda item: item.path.casefold())
    warnings.sort(key=lambda item: item.path.casefold())
    return ScanResult(records=records, warnings=warnings)
