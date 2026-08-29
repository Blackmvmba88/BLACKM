from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import ScanResult

SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scanned_at TEXT NOT NULL,
    roots_json TEXT NOT NULL,
    asset_count INTEGER NOT NULL,
    warning_count INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id INTEGER NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    source_root TEXT NOT NULL,
    path TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    filename TEXT NOT NULL,
    extension TEXT NOT NULL,
    kind TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    modified_ns INTEGER NOT NULL,
    created_ns INTEGER NOT NULL,
    duration_seconds REAL,
    sample_rate INTEGER,
    channels INTEGER,
    bit_depth INTEGER
);

CREATE INDEX IF NOT EXISTS idx_assets_scan ON assets(scan_id);
CREATE INDEX IF NOT EXISTS idx_assets_sha256 ON assets(sha256);
CREATE INDEX IF NOT EXISTS idx_assets_kind ON assets(kind);
CREATE INDEX IF NOT EXISTS idx_assets_path ON assets(path);

CREATE TABLE IF NOT EXISTS scan_warnings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id INTEGER NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    error TEXT NOT NULL
);
"""


def open_database(path: Path) -> sqlite3.Connection:
    path = path.expanduser().absolute()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    return connection


def save_scan(connection: sqlite3.Connection, roots: list[Path], result: ScanResult) -> int:
    scanned_at = datetime.now(timezone.utc).isoformat()
    roots_json = json.dumps([str(path.expanduser().absolute()) for path in roots], ensure_ascii=False)

    with connection:
        cursor = connection.execute(
            "INSERT INTO scans(scanned_at, roots_json, asset_count, warning_count) VALUES (?, ?, ?, ?)",
            (scanned_at, roots_json, len(result.records), len(result.warnings)),
        )
        scan_id = int(cursor.lastrowid)

        for record in result.records:
            audio = record.audio
            connection.execute(
                """
                INSERT INTO assets(
                    scan_id, source_root, path, relative_path, filename, extension, kind,
                    size_bytes, sha256, modified_ns, created_ns, duration_seconds,
                    sample_rate, channels, bit_depth
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scan_id,
                    record.source_root,
                    record.path,
                    record.relative_path,
                    record.filename,
                    record.extension,
                    record.kind,
                    record.size_bytes,
                    record.sha256,
                    record.modified_ns,
                    record.created_ns,
                    audio.duration_seconds if audio else None,
                    audio.sample_rate if audio else None,
                    audio.channels if audio else None,
                    audio.bit_depth if audio else None,
                ),
            )

        connection.executemany(
            "INSERT INTO scan_warnings(scan_id, path, error) VALUES (?, ?, ?)",
            [(scan_id, warning.path, warning.error) for warning in result.warnings],
        )

    return scan_id
