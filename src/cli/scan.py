"""CLI commands for YouTube video discovery scanning."""

from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from src.db.session import get_session

app = typer.Typer(no_args_is_help=True)
console = Console()


@app.command("run")
def scan_run(
    mode: str = typer.Option(
        "channel",
        help="Scan mode: 'channel' (yt-dlp feed) or 'search' (YouTube API gap-fill)",
    ),
    person: Optional[str] = typer.Option(None, help="Person name for targeted search"),
    channel: Optional[str] = typer.Option(None, help="Channel name to scan (channel mode only)"),
    days_back: int = typer.Option(7, help="Days to look back (search mode only)"),
):
    """Scan for new YouTube videos."""
    from src.pipeline.discovery import scan_channel_feeds, scan_search_gap_fill

    session = get_session()
    try:
        if mode == "channel":
            console.print("[bold]Scanning channel feeds via yt-dlp...[/bold]\n")
            result = scan_channel_feeds(session, channel_name=channel)
        elif mode == "search":
            console.print("[bold]Running gap-fill search via YouTube API...[/bold]\n")
            result = scan_search_gap_fill(session, person_name=person, days_back=days_back)
        else:
            console.print(f"[bold red]✗ Unknown mode:[/bold red] {mode}")
            raise typer.Exit(1)

        # Print results
        console.print(f"\n[bold green]✓ Scan complete![/bold green]")
        console.print(f"  Videos found:    [bold]{result.videos_found}[/bold]")
        console.print(f"  Already known:   {result.videos_skipped}")
        if mode == "channel":
            console.print(f"  Channels scanned: {result.channels_scanned}")
        else:
            console.print(f"  People searched:  {result.people_searched}")
            console.print(f"  API quota used:   {result.quota_used} units")

        if result.errors:
            console.print(f"\n[bold yellow]⚠ Errors ({len(result.errors)}):[/bold yellow]")
            for err in result.errors:
                console.print(f"  • {err}")

    except Exception as e:
        console.print(f"\n[bold red]✗ Scan failed:[/bold red] {e}")
        raise typer.Exit(1)
    finally:
        session.close()


@app.command("status")
def scan_status():
    """Show discovery pipeline stats."""
    from src.pipeline.discovery import get_scan_status

    session = get_session()
    try:
        stats = get_scan_status(session)

        console.print(f"\n[bold]Total videos:[/bold] {stats['total_videos']}\n")

        if stats["by_method"]:
            table = Table(title="By Discovery Method")
            table.add_column("Method", style="bold")
            table.add_column("Count", justify="right")
            for method, count in stats["by_method"].items():
                table.add_row(method, str(count))
            console.print(table)

        if stats["by_status"]:
            table = Table(title="By Status")
            table.add_column("Status", style="bold")
            table.add_column("Count", justify="right")
            for status, count in stats["by_status"].items():
                table.add_row(status, str(count))
            console.print(table)

        if not stats["total_videos"]:
            console.print("[dim]No videos discovered yet. Run 'bm scan run --mode channel' to start.[/dim]")
    finally:
        session.close()
