"""CLI commands for speaker identification."""

from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from src.db.models import VideoPeople, Videos
from src.db.session import get_session

app = typer.Typer(no_args_is_help=True)
console = Console()


@app.command("run")
def identify_run(
    video_id: Optional[str] = typer.Argument(None, help="YouTube video ID or partial UUID"),
    pending: bool = typer.Option(False, "--pending", help="Process all transcribed videos"),
    limit: int = typer.Option(10, "--limit", help="Max videos (with --pending)"),
    speaker: Optional[str] = typer.Option(None, help="Speaker label for manual override"),
    name: Optional[str] = typer.Option(None, help="Person name for manual override"),
):
    """Identify speakers in transcribed videos."""
    from src.pipeline.identification import identify_pending, identify_video, manual_identify

    session = get_session()
    try:
        if pending:
            console.print(f"[bold]Identifying speakers in up to {limit} videos...[/bold]\n")
            stats = identify_pending(session, limit=limit)

            console.print(f"\n[bold green]✓ Identification complete![/bold green]")
            console.print(f"  Processed:   {stats['processed']}")
            console.print(f"  Identified:  [green]{stats['identified']}[/green]")
            console.print(f"  Skipped:     [dim]{stats['skipped']}[/dim]")

            if stats["errors"]:
                console.print(f"\n[bold yellow]⚠ Errors:[/bold yellow]")
                for err in stats["errors"][:10]:
                    console.print(f"  • {err}")

        elif video_id and speaker and name:
            # Manual override
            video = _find_video(session, video_id)
            if not video:
                console.print(f"[bold red]✗ Video not found:[/bold red] {video_id}")
                raise typer.Exit(1)

            ok = manual_identify(session, video, speaker, name)
            if ok:
                console.print(f"[bold green]✓ Manual ID:[/bold green] {speaker} → {name}")
            else:
                console.print(f"[bold red]✗ Person not found:[/bold red] {name}")
                raise typer.Exit(1)

        elif video_id:
            video = _find_video(session, video_id)
            if not video:
                console.print(f"[bold red]✗ Video not found:[/bold red] {video_id}")
                raise typer.Exit(1)

            console.print(f"[bold]Identifying:[/bold] {video.title or video.youtube_video_id}\n")
            result = identify_video(session, video)

            console.print(f"  Method:    {result['method']}")
            console.print(f"  Matched:   [green]{result['matched']}[/green]")
            console.print(f"  Unmatched: {result['unmatched']}")
            if result.get("error"):
                console.print(f"  Error:     [yellow]{result['error']}[/yellow]")
            if result.get("upgrade_triggered"):
                console.print(f"  [bold yellow]⚡ Upgrade triggered:[/bold yellow] {result['upgrade_reason']}")

        else:
            console.print("[bold red]Provide a video ID or use --pending[/bold red]")
            raise typer.Exit(1)

    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"\n[bold red]✗ Error:[/bold red] {e}")
        raise typer.Exit(1)
    finally:
        session.close()


@app.command("status")
def identify_status():
    """Show identification pipeline stats."""
    from src.pipeline.identification import get_identify_status

    session = get_session()
    try:
        stats = get_identify_status(session)

        console.print(f"\n[bold]Videos:[/bold]")
        console.print(f"  Total:                    {stats['total_videos']}")
        console.print(f"  Identified:               [green]{stats['identified']}[/green]")
        console.print(f"  Skipped (no speakers):    [dim]{stats['skipped']}[/dim]")
        console.print(f"  Pending identification:   [yellow]{stats['pending_identification']}[/yellow]")
        console.print(f"  Pending transcription:    [dim]{stats['pending_transcription']}[/dim]")

        console.print(f"\n[bold]People in Videos:[/bold]")
        console.print(f"  Total records:      {stats['video_people_records']}")
        console.print(f"  Low confidence (<0.7): [yellow]{stats['low_confidence']}[/yellow]")
    finally:
        session.close()


@app.command("review")
def identify_review():
    """Show low-confidence identifications for manual review."""
    session = get_session()
    try:
        low = (
            session.query(VideoPeople)
            .filter(VideoPeople.confidence < 0.7)
            .all()
        )

        if not low:
            console.print("[green]No low-confidence identifications to review.[/green]")
            return

        table = Table(title="Low-Confidence Identifications", show_lines=True)
        table.add_column("Video", max_width=40)
        table.add_column("Person")
        table.add_column("Role")
        table.add_column("Confidence", justify="center")
        table.add_column("Via")

        for vp in low:
            table.add_row(
                vp.video.title[:40] if vp.video.title else vp.video.youtube_video_id,
                vp.person.name,
                vp.role or "",
                f"{float(vp.confidence):.2f}" if vp.confidence else "?",
                vp.identified_via or "",
            )

        console.print(table)
    finally:
        session.close()


def _find_video(session, video_id: str) -> Videos | None:
    """Find video by YouTube ID or partial UUID."""
    video = session.query(Videos).filter(Videos.youtube_video_id == video_id).first()
    if video:
        return video
    try:
        from uuid import UUID
        uid = UUID(video_id)
        return session.query(Videos).filter(Videos.id == uid).first()
    except ValueError:
        pass
    from sqlalchemy import Text as SAText, cast
    results = session.query(Videos).filter(cast(Videos.id, SAText).like(f"{video_id}%")).all()
    return results[0] if len(results) == 1 else None
