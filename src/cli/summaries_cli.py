"""Summaries CLI — generate and view episode summaries."""

import typer
from rich.console import Console
from rich.table import Table
from uuid import UUID

app = typer.Typer(name="summaries", help="Generate and view episode summaries")
console = Console()


@app.command("generate")
def generate_summaries(
    video_id: str = typer.Argument(None, help="Specific video UUID"),
    pending: bool = typer.Option(False, "--pending", help="Generate for all pending favorites"),
):
    """Generate episode summaries."""
    from src.db.session import get_session

    session = get_session()
    try:
        if video_id:
            from src.pipeline.summaries import generate_episode_summary
            vid = UUID(video_id)
            console.print(f"[cyan]Generating summary for {video_id}...[/cyan]")
            result = generate_episode_summary(vid, "full_episode", session)
            if result:
                console.print(f"[green]✓ Generated: {result.watch_verdict}[/green]")
                console.print(f"  TL;DR: {result.tldr[:200]}")
            else:
                console.print("[yellow]No summary generated[/yellow]")
        elif pending:
            from src.pipeline.summaries import generate_pending_summaries
            console.print("[cyan]Generating summaries for all pending favorites...[/cyan]")
            stats = generate_pending_summaries(session)
            console.print(f"[green]✓ Generated {stats['generated']} summaries[/green]")
            if stats["errors"]:
                for err in stats["errors"]:
                    console.print(f"  [red]✗ {err}[/red]")
        else:
            console.print("[yellow]Specify --pending or a video_id[/yellow]")
    finally:
        session.close()


@app.command("feed")
def show_feed(
    limit: int = typer.Option(10, "--limit", "-n"),
):
    """Preview the feed in terminal."""
    from src.db.models import EpisodeSummaries
    from src.db.session import get_session

    session = get_session()
    summaries = (
        session.query(EpisodeSummaries)
        .order_by(EpisodeSummaries.generated_at.desc())
        .limit(limit)
        .all()
    )

    if not summaries:
        console.print("[yellow]No summaries yet. Run 'bm summaries generate --pending'[/yellow]")
        session.close()
        return

    for s in summaries:
        verdict_colors = {
            "essential": "bold red",
            "worth_skimming": "yellow",
            "skip_unless_fan": "dim",
        }
        vc = verdict_colors.get(s.watch_verdict, "white")
        console.print(f"\n[{vc}]{'━' * 60}[/{vc}]")
        channel = s.video.podcast_channel.name if s.video and s.video.podcast_channel else "?"
        date = s.video.published_at.strftime("%b %d, %Y") if s.video and s.video.published_at else "?"
        console.print(f"[cyan]📌 {channel} · {date}[/cyan]")
        console.print(f"[bold]{s.video.title if s.video else '?'}[/bold]")
        console.print(f"[{vc}][{s.watch_verdict.upper()}][/{vc}] {s.watch_verdict_reason}")
        console.print(f"\n{s.tldr}")
        if s.whats_new:
            console.print(f"\n[yellow]WHAT'S NEW:[/yellow] {s.whats_new}")

    session.close()
