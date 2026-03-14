"""CLI commands for transcript extraction."""

from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from src.db.models import Videos
from src.db.session import get_session

app = typer.Typer(no_args_is_help=True)
console = Console()


@app.command("run")
def transcribe_run(
    video_id: Optional[str] = typer.Argument(None, help="YouTube video ID or partial DB UUID"),
    deep: bool = typer.Option(False, "--deep", help="Force deep path (Deepgram)"),
    pending: bool = typer.Option(False, "--pending", help="Process all pending videos"),
    limit: int = typer.Option(10, "--limit", help="Max videos to process (with --pending)"),
):
    """Transcribe videos (fast or deep path)."""
    from src.pipeline.transcription import transcribe_pending, transcribe_video

    session = get_session()
    try:
        if pending:
            console.print(f"[bold]Transcribing up to {limit} pending videos...[/bold]\n")
            stats = transcribe_pending(session, limit=limit)

            console.print(f"\n[bold green]✓ Transcription batch complete![/bold green]")
            console.print(f"  Processed:  {stats['processed']}")
            console.print(f"  Succeeded:  [green]{stats['succeeded']}[/green]")
            console.print(f"  Failed:     [red]{stats['failed']}[/red]")

            if stats["errors"]:
                console.print(f"\n[bold yellow]⚠ Errors:[/bold yellow]")
                for err in stats["errors"][:10]:
                    console.print(f"  • {err}")

        elif video_id:
            # Find by YouTube video ID or partial UUID
            video = _find_video(session, video_id)
            if not video:
                console.print(f"[bold red]✗ Video not found:[/bold red] {video_id}")
                raise typer.Exit(1)

            console.print(f"[bold]Transcribing:[/bold] {video.title or video.youtube_video_id}")
            path = "deep" if deep else "auto"
            console.print(f"  Path: {path}\n")

            result = transcribe_video(session, video, force_deep=deep)

            if result.error:
                console.print(f"\n[bold red]✗ Failed:[/bold red] {result.error}")
                raise typer.Exit(1)
            else:
                console.print(f"\n[bold green]✓ Done![/bold green] {len(result.segments)} segments stored")
                console.print(f"  Provider: {result.provider}")
                console.print(f"  Mode: {result.mode}")

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
def transcribe_status():
    """Show transcription pipeline stats."""
    from src.pipeline.transcription import get_transcribe_status

    session = get_session()
    try:
        stats = get_transcribe_status(session)

        console.print(f"\n[bold]Videos:[/bold]")
        console.print(f"  Total:       {stats['total_videos']}")
        console.print(f"  Transcribed: [green]{stats['transcribed']}[/green]")
        console.print(f"  Pending:     [yellow]{stats['pending']}[/yellow]")

        console.print(f"\n[bold]Transcript Runs:[/bold]")
        console.print(f"  Total:     {stats['runs_total']}")
        console.print(f"  Succeeded: [green]{stats['runs_succeeded']}[/green]")
        console.print(f"  Failed:    [red]{stats['runs_failed']}[/red]")

        console.print(f"\n[bold]Segments:[/bold] {stats['total_segments']}")
    finally:
        session.close()


def _find_video(session, video_id: str) -> Videos | None:
    """Find a video by YouTube video ID or partial UUID."""
    # Try YouTube ID first
    video = session.query(Videos).filter(Videos.youtube_video_id == video_id).first()
    if video:
        return video

    # Try partial UUID
    try:
        from uuid import UUID
        uid = UUID(video_id)
        return session.query(Videos).filter(Videos.id == uid).first()
    except ValueError:
        pass

    # Partial UUID match
    from sqlalchemy import Text as SAText, cast
    results = (
        session.query(Videos)
        .filter(cast(Videos.id, SAText).like(f"{video_id}%"))
        .all()
    )
    if len(results) == 1:
        return results[0]
    return None
