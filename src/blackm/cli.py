from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

from .db import open_database, save_scan
from .scanner import scan_roots

DEFAULT_DB = Path("~/.blackm/catalog.db")


def _default_roots() -> list[Path]:
    configured = os.environ.get("BLACKM_SCAN_PATHS")
    if configured:
        return [Path(item) for item in configured.split(os.pathsep) if item.strip()]

    roots: list[Path] = []
    music = Path.home() / "Music"
    if music.exists():
        roots.append(music)

    volumes = Path("/Volumes")
    if volumes.exists():
        try:
            for candidate in sorted(volumes.iterdir()):
                try:
                    if candidate.is_dir() and candidate.resolve() != Path("/").resolve():
                        roots.append(candidate)
                except OSError:
                    continue
        except OSError:
            pass

    return roots


def _human_bytes(value: int) -> str:
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024 or unit == "TiB":
            return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} B"
        amount /= 1024
    return f"{value} B"


def _scan_payload(scan_id: int, roots: list[Path], result, db_path: Path) -> dict[str, object]:
    counts = Counter(record.kind for record in result.records)
    duplicate_groups = result.duplicate_groups
    return {
        "scan_id": scan_id,
        "database": str(db_path.expanduser().absolute()),
        "roots": [str(path.expanduser().absolute()) for path in roots],
        "asset_count": len(result.records),
        "total_bytes": sum(record.size_bytes for record in result.records),
        "by_kind": dict(sorted(counts.items())),
        "exact_duplicate_groups": len(duplicate_groups),
        "exact_duplicate_files": sum(len(items) for items in duplicate_groups.values()),
        "warnings": [warning.to_dict() for warning in result.warnings],
    }


def _print_scan(payload: dict[str, object]) -> None:
    print("BLACKM VAULT SCAN")
    print("=" * 56)
    for root in payload["roots"]:
        print(f"source  {root}")
    print(f"db      {payload['database']}")
    print("-" * 56)
    print(f"assets  {payload['asset_count']}")
    print(f"size    {_human_bytes(int(payload['total_bytes']))}")
    by_kind = payload["by_kind"]
    if isinstance(by_kind, dict):
        for kind, count in by_kind.items():
            print(f"{kind:<8}{count}")
    print("-" * 56)
    print(f"exact duplicate groups  {payload['exact_duplicate_groups']}")
    print(f"files in those groups   {payload['exact_duplicate_files']}")
    warnings = payload["warnings"]
    print(f"warnings                {len(warnings) if isinstance(warnings, list) else 0}")
    print(f"scan id                 {payload['scan_id']}")
    print("\nSource files were not moved, renamed, modified or deleted.")


def _run_vault_scan(args: argparse.Namespace) -> int:
    roots = [Path(value) for value in args.paths] if args.paths else _default_roots()
    if not roots:
        print(
            "No scan roots found. Pass paths explicitly, for example:\n"
            "  bm vault scan ~/Music /Volumes/<USB_NAME>\n"
            "or set BLACKM_SCAN_PATHS.",
            file=sys.stderr,
        )
        return 2

    result = scan_roots(roots, workers=args.workers)
    db_path = Path(args.db)
    connection = open_database(db_path)
    try:
        scan_id = save_scan(connection, roots, result)
    finally:
        connection.close()

    payload = _scan_payload(scan_id, roots, result, db_path)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_scan(payload)
    return 0 if not result.warnings else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bm", description="BlackMamba Music OS")
    subcommands = parser.add_subparsers(dest="domain", required=True)

    vault = subcommands.add_parser("vault", help="Local-first catalog vault operations")
    vault_commands = vault.add_subparsers(dest="action", required=True)

    scan = vault_commands.add_parser("scan", help="Read-only inventory of local music assets")
    scan.add_argument("paths", nargs="*", help="Folders/files to inventory")
    scan.add_argument("--db", default=str(DEFAULT_DB), help="SQLite catalog path")
    scan.add_argument("--workers", type=int, default=None, help="Hash worker count")
    scan.add_argument("--json", action="store_true", help="Machine-readable result")
    scan.set_defaults(handler=_run_vault_scan)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
