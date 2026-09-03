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
    SoundCloudClient,
    SoundCloudError,
    SoundCloudSettings,
    TokenStore,
    audit_tracks,
    build_authorize_url,
    exchange_code,
    generate_pkce,
    generate_state,
)

app = typer.Typer(no_args_is_help=True, help="BLACKM — BlackMamba Music OS")
soundcloud_app = typer.Typer(no_args_is_help=True, help="SoundCloud adapter")
app.add_typer(soundcloud_app, name="soundcloud")
console = Console()


def _die(exc: Exception) -> None:
    console.print(f"[bold red]ERROR[/bold red] {exc}")
    raise typer.Exit(code=1)


@soundcloud_app.command("auth")
def soundcloud_auth(
    no_browser: bool = typer.Option(False, "--no-browser", help="Print the authorization URL instead of opening it."),
    timeout: int = typer.Option(300, min=30, help="Seconds to wait for the local OAuth callback."),
) -> None:
    """Authenticate BLACKM with your SoundCloud account using OAuth 2.1 + PKCE."""
    try:
        settings = SoundCloudSettings.from_env()
        redirect = urllib.parse.urlparse(settings.redirect_uri)
        if redirect.scheme != "http" or redirect.hostname not in {"127.0.0.1", "localhost"}:
            raise SoundCloudError(
                "For the local CLI login, SOUNDCLOUD_REDIRECT_URI must be an http://127.0.0.1 or http://localhost callback."
            )
        if not redirect.port:
            raise SoundCloudError("SOUNDCLOUD_REDIRECT_URI must include a local port.")

        pkce = generate_pkce()
        expected_state = generate_state()
        authorize_url = build_authorize_url(settings, pkce, expected_state)
        result: dict[str, str] = {}

        callback_path = redirect.path or "/"

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path != callback_path:
                    self.send_response(404)
                    self.end_headers()
                    return
                query = urllib.parse.parse_qs(parsed.query)
                result["code"] = query.get("code", [""])[0]
                result["state"] = query.get("state", [""])[0]
                result["error"] = query.get("error", [""])[0]
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"BLACKM authenticated. You can close this tab and return to Terminal.\n")

            def log_message(self, format: str, *args: Any) -> None:
                return

        server = HTTPServer((redirect.hostname, redirect.port), Handler)
        server.timeout = 1

        console.print("[bold]SoundCloud authorization[/bold]")
        console.print(authorize_url)
        if not no_browser:
            webbrowser.open(authorize_url)

        deadline = time.time() + timeout
        while time.time() < deadline and "code" not in result and "error" not in result:
            server.handle_request()
        server.server_close()

        if result.get("error"):
            raise SoundCloudError(f"Authorization rejected: {result['error']}")
        if not result.get("code"):
            raise SoundCloudError("Timed out waiting for SoundCloud authorization.")
        if result.get("state") != expected_state:
            raise SoundCloudError("OAuth state mismatch. Authorization was not accepted.")

        token = exchange_code(settings, result["code"], pkce.verifier)
        store = TokenStore()
        store.save(token)
        me = SoundCloudClient(settings, store).me()
        username = me.get("username") or me.get("full_name") or me.get("urn") or "SoundCloud user"
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
        data = SoundCloudClient(SoundCloudSettings.from_env()).me()
        console.print_json(json.dumps(data))
    except SoundCloudError as exc:
        _die(exc)


@soundcloud_app.command("tracks")
def soundcloud_tracks(
    limit: int = typer.Option(50, min=0, help="Maximum tracks to show. Use 0 for all."),
) -> None:
    """Read your SoundCloud track inventory."""
    try:
        client = SoundCloudClient(SoundCloudSettings.from_env())
        tracks = client.list_my_tracks(max_tracks=limit or None)
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
    except SoundCloudError as exc:
        _die(exc)


@soundcloud_app.command("audit")
def soundcloud_audit(
    limit: int = typer.Option(0, min=0, help="Maximum tracks to inspect. Use 0 for all."),
    json_out: Path | None = typer.Option(None, "--json-out", help="Optional path for the complete JSON audit."),
) -> None:
    """Audit missing metadata without changing anything on SoundCloud."""
    try:
        client = SoundCloudClient(SoundCloudSettings.from_env())
        tracks = client.list_my_tracks(max_tracks=limit or None)
        audit = audit_tracks(tracks)
        incomplete = [item for item in audit if not item["complete"]]

        table = Table(title="BLACKM SoundCloud metadata audit")
        table.add_column("Track")
        table.add_column("Missing")
        table.add_column("URL", overflow="fold")
        for item in incomplete:
            table.add_row(
                str(item.get("title") or item.get("urn") or item.get("id") or ""),
                ", ".join(item["missing"]),
                str(item.get("permalink_url") or ""),
            )
        console.print(table)
        console.print(
            f"Scanned: [bold]{len(audit)}[/bold] | Complete: [green]{len(audit) - len(incomplete)}[/green] | Incomplete: [yellow]{len(incomplete)}[/yellow]"
        )

        if json_out:
            json_out.parent.mkdir(parents=True, exist_ok=True)
            json_out.write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
            console.print(f"Audit saved to: {json_out}")
    except (SoundCloudError, OSError) as exc:
        _die(exc)


if __name__ == "__main__":
    app()
