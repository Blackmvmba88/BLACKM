from __future__ import annotations

import sqlite3
import tempfile
import unittest
import wave
from pathlib import Path

from blackm.constants import classify_asset
from blackm.db import open_database, save_scan
from blackm.scanner import scan_roots, sha256_file


class VaultScannerTests(unittest.TestCase):
    def test_classifies_supported_assets(self) -> None:
        self.assertEqual(classify_asset(Path("track.WAV")), "audio")
        self.assertEqual(classify_asset(Path("cover.webp")), "artwork")
        self.assertEqual(classify_asset(Path("lyrics.lrc")), "lyrics")
        self.assertEqual(classify_asset(Path("clip.mov")), "video")
        self.assertIsNone(classify_asset(Path("notes.pdf")))

    def test_scan_detects_exact_duplicate_and_reads_wav_header(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = root / "one.wav"
            duplicate = root / "copy.wav"
            ignored = root / "ignore.bin"

            with wave.open(str(first), "wb") as output:
                output.setnchannels(2)
                output.setsampwidth(2)
                output.setframerate(44_100)
                output.writeframes(b"\x00\x00\x00\x00" * 44_100)
            duplicate.write_bytes(first.read_bytes())
            ignored.write_bytes(b"not part of the catalog")

            result = scan_roots([root], workers=2)

            self.assertEqual(len(result.records), 2)
            self.assertEqual(len(result.warnings), 0)
            self.assertEqual(len(result.duplicate_groups), 1)
            self.assertEqual(sha256_file(first), sha256_file(duplicate))

            audio = result.records[0].audio
            self.assertIsNotNone(audio)
            assert audio is not None
            self.assertEqual(audio.sample_rate, 44_100)
            self.assertEqual(audio.channels, 2)
            self.assertEqual(audio.bit_depth, 16)
            self.assertAlmostEqual(audio.duration_seconds or 0, 1.0, places=2)

    def test_scan_snapshot_persists_in_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "music"
            root.mkdir()
            (root / "lyrics.txt").write_text("hello", encoding="utf-8")
            db_path = Path(temp) / "catalog.db"

            result = scan_roots([root], workers=1)
            connection = open_database(db_path)
            try:
                scan_id = save_scan(connection, [root], result)
                scan_count = connection.execute("SELECT COUNT(*) FROM scans").fetchone()[0]
                asset_count = connection.execute(
                    "SELECT COUNT(*) FROM assets WHERE scan_id = ?", (scan_id,)
                ).fetchone()[0]
            finally:
                connection.close()

            self.assertEqual(scan_count, 1)
            self.assertEqual(asset_count, 1)


if __name__ == "__main__":
    unittest.main()
