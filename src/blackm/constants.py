from __future__ import annotations

from pathlib import Path

ASSET_EXTENSIONS: dict[str, set[str]] = {
    "audio": {".wav", ".mp3", ".flac", ".m4a", ".aac", ".aiff", ".aif", ".ogg"},
    "artwork": {".jpg", ".jpeg", ".png", ".webp", ".heic", ".tif", ".tiff"},
    "lyrics": {".txt", ".lrc", ".srt", ".vtt"},
    "video": {".mp4", ".mov", ".m4v", ".webm", ".mkv"},
    "metadata": {".json", ".yaml", ".yml", ".toml"},
}

EXTENSION_TO_KIND = {
    extension: kind
    for kind, extensions in ASSET_EXTENSIONS.items()
    for extension in extensions
}

SKIP_DIR_NAMES = {
    ".git",
    ".svn",
    "__pycache__",
    "node_modules",
    ".Trash",
    ".Trashes",
    ".Spotlight-V100",
    ".fseventsd",
}


def classify_asset(path: Path) -> str | None:
    return EXTENSION_TO_KIND.get(path.suffix.lower())
