from __future__ import annotations

import json
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from blackm.soundcloud import (
    DEFAULT_REDIRECT_URI,
    SoundCloudClient,
    SoundCloudError,
    SoundCloudSettings,
    TokenStore,
    build_audit_report,
    build_authorize_url,
    build_inventory,
    exchange_code,
    generate_pkce,
    generate_state,
    validate_loopback_redirect_uri,
)
from blackm.soundcloud_metadata import (
    apply_plan,
    create_plan,
    dry_run_plan,
    load_patches,
    read_json,
    validate_plan,
    write_json,
)

app = typer.Typer(no_args_is_help=True, help="BLACKM — BlackMamba Music OS")
soundcloud_app = typer.Typer(no_args_is_help=True, help="SoundCloud adapter")
metadata_app = typer.Typer(no_args_is_help=True, help="Planned and certified metadata repairs")
soundcloud_app.add_typer(metadata_app, name="metadata")
app.add_typer(soundcloud_app, name="soundcloud")
console = Console()


def _die(exc: Exception) -> None:
    console.print(f"[bold red]ERROR[/bold red] {exc}")
    raise typer.Exit(code=1)


@soundcloud_app.command("configure")
def soundcloud_configure(
    redirect_uri: str = typer.Option(
        DEFAULT_REDIRECT_URI,
        "--redirect-uri",
        help="Exact loopback callback registered in the existing SoundCloud app.",
    ),
) -> None:
    """Store existing SoundCloud app credentials outside the repository."""
    try:
        validate_loopback_redirect_uri(redirect_uri)
        client_id = typer.prompt("SoundCloud client ID").strip()
        client_secret = typer.prompt(
            "SoundCloud client secret", hide_input=True
        ).strip()
        if not client_id or not client_secret:
            raise SoundCloudError("Client ID and client secret are required.")
        path = SoundCloudSettings(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
        ).save()
        console.print(f"[bold green]Configured[/bold green] {path} (mode 0600)")
    except (SoundCloudError, OSError) as exc:
        _die(exc)


@soundcloud_app.command("auth")
def soundcloud_auth(
    no_browser: bool = typer.Option(
        False,
        "--no-browser",
        help="Print the authorization URL instead of opening it.",
    ),
    timeout: int = typer.Option(
        300, min=30, help="Seconds to wait for the local OAuth callback."
    ),
) -> None:
    """Authenticate BLACKM using OAuth 2.1 authorization code + PKCE."""
    try:
        settings = SoundCloudSettings.load()
        redirect = validate_loopback_redirect_uri(settings.redirect_uri)
        pkce = generate_pkce()
        expected_state = generate_state()
        authorize_url = build_authorize_url(settings, pkce, expected_state)
        result: dict[str, str] = {}
        callback_path = redirect.path

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parsed = urllib.parse.urlsplit(self.path)
                if parsed.path != callback_path:
                    self.send_response(404)
                    self.end_headers()
                    return
                query = urllib.parse.parse_qs(parsed.query)
                result["code"] = query.get("code", [""])[0]
                result["state"] = query.get("state", [""])[0]
                result["error"] = query.get("error", [""])[0]
                result["error_description"] = query.get(
                    "error_description", [""]
                )[0]
                accepted = result["state"] == expected_state and (
                    bool(result["code"]) or bool(result["error"])
                )
                self.send_response(200 if accepted else 400)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                message = (
                    "BLACKM received the SoundCloud callback. Return to Terminal.\n"
                    if accepted
                    else "BLACKM rejected this callback. Return to Terminal.\n"
                )
                self.wfile.write(message.encode("utf-8"))

            def log_message(self, format: str, *args: Any) -> None:
                return

        server = HTTPServer((redirect.hostname or "127.0.0.1", redirect.port), Handler)
        server.timeout = 1
        try:
            if no_browser:
                console.print("[bold]Open this SoundCloud authorization URL:[/bold]")
                console.print(authorize_url)
            else:
                opened = webbrowser.open(authorize_url)
                console.print(
                    "Opened SoundCloud authorization in your browser."
                    if opened
                    else "Could not open a browser automatically; use --no-browser."
                )
            deadline = time.time() + timeout
            while (
                time.time() < deadline
                and "code" not in result
                and "error" not in result
            ):
                server.handle_request()
        finally:
            server.server_close()

        if result.get("state") != expected_state:
            raise SoundCloudError(
                "OAuth state mismatch. The callback was not accepted."
            )
        if result.get("error"):
            detail = result.get("error_description") or result["error"]
            raise SoundCloudError(f"Authorization rejected: {detail}")
        if not result.get("code"):
            raise SoundCloudError("Timed out waiting for SoundCloud authorization.")

        token = exchange_code(settings, result["code"], pkce.verifier)
        store = TokenStore()
        store.save(token)
        me = SoundCloudClient(settings, store).me()
        username = (
            me.get("username")
            or me.get("full_name")
            or me.get("urn")
            or "SoundCloud user"
        )
        console.print(f"[bold green]Authenticated[/bold green] as {username}")
        console.print(f"Token store: {store.path}")
    except (SoundCloudError, OSError, ValueError) as exc:
        _die(exc)


@soundcloud_app.command("logout")
def soundcloud_logout() -> None:
    """Delete BLACKM's local SoundCloud token file."""
    store = TokenStore()
    store.clear()
    console.print("[green]Local SoundCloud token removed.[/green]")


@soundcloud_app.command("me")
def soundcloud_me() -> None:
    """Show the authenticated SoundCloud account."""
    try:
        data = SoundCloudClient(SoundCloudSettings.load()).me()
        console.print_json(json.dumps(data))
    except SoundCloudError as exc:
        _die(exc)


@soundcloud_app.command("tracks")
def soundcloud_tracks(
    limit: int = typer.Option(
        50, min=0, help="Maximum tracks to fetch. Use 0 for all."
    ),
    json_out: Path = typer.Option(
        Path("reports/soundcloud-inventory.json"),
        "--json-out",
        help="Complete raw inventory output.",
    ),
) -> None:
    """Read the account's track inventory and preserve the raw API records."""
    try:
        client = SoundCloudClient(SoundCloudSettings.load())
        account = client.me()
        tracks = client.list_my_tracks(max_tracks=limit or None)
        inventory = build_inventory(tracks, account)
        write_json(json_out, inventory)

        table = Table(title=f"SoundCloud tracks ({len(tracks)})")
        table.add_column("URN / ID", overflow="fold")
        table.add_column("Title")
        table.add_column("Genre")
        table.add_column("Visibility")
        table.add_column("URL", overflow="fold")
        for track in tracks:
            table.add_row(
                str(track.get("urn") or track.get("id") or ""),
                str(track.get("title") or ""),
                str(track.get("genre") or ""),
                str(track.get("sharing") or track.get("access") or ""),
                str(track.get("permalink_url") or ""),
            )
        console.print(table)
        console.print(f"Inventory saved to: {json_out}")
    except (SoundCloudError, OSError, ValueError) as exc:
        _die(exc)


@soundcloud_app.command("audit")
def soundcloud_audit(
    limit: int = typer.Option(
        0, min=0, help="Maximum tracks to inspect. Use 0 for all."
    ),
    json_out: Path = typer.Option(
        Path("reports/soundcloud-audit.json"),
        "--json-out",
        help="Structured metadata audit output.",
    ),
    inventory_out: Path = typer.Option(
        Path("reports/soundcloud-inventory.json"),
        "--inventory-out",
        help="Complete raw catalog inventory output.",
    ),
) -> None:
    """Create a read-only catalog inventory and metadata radiography."""
    try:
        client = SoundCloudClient(SoundCloudSettings.load())
        account = client.me()
        tracks = client.list_my_tracks(max_tracks=limit or None)
        inventory = build_inventory(tracks, account)
        report = build_audit_report(tracks, account)
        write_json(inventory_out, inventory)
        write_json(json_out, report)

        review = [item for item in report["tracks"] if item["needs_review"]]
        table = Table(title="BLACKM SoundCloud metadata audit")
        table.add_column("Track")
        table.add_column("Missing / inconsistent")
        table.add_column("URL", overflow="fold")
        for item in review:
            findings = item["missing"] + item["inconsistent"]
            warning_fields = sorted(
                {
                    issue["field"]
                    for issue in item["issues"]
                    if issue["severity"] == "warning"
                }
            )
            table.add_row(
                str(item.get("title") or item.get("urn") or item.get("id") or ""),
                ", ".join(sorted(set(findings + warning_fields))) or "review",
                str(item.get("permalink_url") or ""),
            )
        console.print(table)
        summary = report["summary"]
        console.print(
            "Scanned: [bold]{scanned}[/bold] | Complete: [green]{complete}[/green] "
            "| Incomplete: [yellow]{incomplete}[/yellow] | Needs review: {needs_review}".format(
                **summary
            )
        )
        console.print(f"Inventory: {inventory_out}")
        console.print(f"Audit: {json_out}")
        console.print("[bold green]REMOTE MUTATIONS: 0[/bold green]")
    except (SoundCloudError, OSError, ValueError) as exc:
        _die(exc)


@metadata_app.command("plan")
def metadata_plan(
    patches: Path = typer.Argument(
        ..., exists=True, readable=True, help="JSON file containing desired patches."
    ),
    out: Path = typer.Option(
        Path("reports/soundcloud-metadata-plan.json"), "--out"
    ),
) -> None:
    """Read live tracks and create a preconditioned metadata plan."""
    try:
        client = SoundCloudClient(SoundCloudSettings.load())
        account = client.me()
        plan = create_plan(client, account, load_patches(patches))
        write_json(out, plan)
        console.print(
            f"[bold green]Planned[/bold green] {plan['operation_count']} operation(s): {out}"
        )
        console.print("No SoundCloud metadata was changed.")
    except (SoundCloudError, OSError, ValueError) as exc:
        _die(exc)


@metadata_app.command("dry-run")
def metadata_dry_run(
    plan_path: Path = typer.Argument(
        ..., exists=True, readable=True, help="Metadata plan produced by plan."
    ),
    receipt_out: Path = typer.Option(
        Path("reports/soundcloud-metadata-dry-run.json"), "--receipt-out"
    ),
) -> None:
    """Re-read every track and issue a receipt only for matching preconditions."""
    try:
        client = SoundCloudClient(SoundCloudSettings.load())
        plan = validate_plan(read_json(plan_path))
        receipt = dry_run_plan(client, plan)
        write_json(receipt_out, receipt)
        color = "green" if receipt["all_ready"] else "yellow"
        console.print(
            f"[bold {color}]Dry-run ready: {receipt['ready_count']}/"
            f"{receipt['operation_count']}[/bold {color}]"
        )
        console.print(f"Receipt: {receipt_out}")
        console.print("No SoundCloud metadata was changed.")
        if not receipt["all_ready"]:
            raise typer.Exit(code=2)
    except (SoundCloudError, OSError, ValueError) as exc:
        _die(exc)


@metadata_app.command("apply")
def metadata_apply(
    plan_path: Path = typer.Argument(
        ..., exists=True, readable=True, help="Metadata plan produced by plan."
    ),
    receipt: Path = typer.Option(
        ..., "--receipt", exists=True, readable=True, help="Matching dry-run receipt."
    ),
    evidence_out: Path = typer.Option(
        Path("reports/soundcloud-metadata-evidence.json"), "--evidence-out"
    ),
    yes: bool = typer.Option(
        False, "--yes", help="Required acknowledgement for remote metadata writes."
    ),
) -> None:
    """Apply a dry-run-approved plan, read back, compare, and certify."""
    if not yes:
        _die(
            SoundCloudError(
                "Refusing remote writes without --yes and a matching dry-run receipt."
            )
        )
    try:
        client = SoundCloudClient(SoundCloudSettings.load())
        plan = validate_plan(read_json(plan_path))
        evidence = apply_plan(client, plan, read_json(receipt), evidence_out)
        certified = evidence["summary"]["certified"]
        total = evidence["operation_count"]
        color = "green" if evidence["all_certified"] else "red"
        console.print(
            f"[bold {color}]Certified: {certified}/{total}[/bold {color}]"
        )
        console.print(f"Evidence: {evidence_out}")
        if not evidence["all_certified"]:
            raise typer.Exit(code=3)
    except (SoundCloudError, OSError, ValueError) as exc:
        _die(exc)


if __name__ == "__main__":
    app()
