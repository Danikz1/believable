"""CLI commands for LLM enrichment and claim extraction."""

from typing import Optional

import typer
from rich.console import Console

from src.db.models import Videos
from src.db.session import get_session

app = typer.Typer(no_args_is_help=True)
console = Console()


@app.command("run")
def enrich_run(
    video_id: Optional[str] = typer.Argument(None, help="YouTube video ID"),
    pending: bool = typer.Option(False, "--pending", help="Process all identified videos"),
    limit: int = typer.Option(5, "--limit", help="Max videos (with --pending)"),
    embed: bool = typer.Option(True, "--embed/--no-embed", help="Generate embeddings"),
    positions: bool = typer.Option(True, "--positions/--no-positions", help="Update positions"),
):
    """Extract claims from identified videos."""
    from src.pipeline.embeddings import embed_pending_claims
    from src.pipeline.enrichment import enrich_pending, enrich_video
    from src.pipeline.positions import update_positions_for_video

    session = get_session()
    try:
        if pending:
            console.print(f"[bold]Enriching up to {limit} identified videos...[/bold]\n")
            stats = enrich_pending(session, limit=limit)

            console.print(f"\n[bold green]✓ Enrichment complete![/bold green]")
            console.print(f"  Processed:    {stats['processed']}")
            console.print(f"  Total claims: [green]{stats['total_claims']}[/green]")

            if stats["errors"]:
                console.print(f"\n[bold yellow]⚠ Notes:[/bold yellow]")
                for err in stats["errors"][:10]:
                    console.print(f"  • {err}")

        elif video_id:
            video = session.query(Videos).filter(
                Videos.youtube_video_id == video_id
            ).first()

            if not video:
                console.print(f"[bold red]✗ Video not found:[/bold red] {video_id}")
                raise typer.Exit(1)

            console.print(f"[bold]Enriching:[/bold] {video.title or video_id}\n")
            result = enrich_video(session, video)

            console.print(f"  Claims:  [green]{result['claims_extracted']}[/green]")
            console.print(f"  People:  {result['people_processed']}")

            if result["errors"]:
                for err in result["errors"]:
                    console.print(f"  [yellow]⚠ {err}[/yellow]")

        else:
            console.print("[bold red]Provide a video ID or use --pending[/bold red]")
            raise typer.Exit(1)

        # Generate embeddings
        if embed:
            console.print(f"\n[dim]Generating embeddings...[/dim]")
            emb_stats = embed_pending_claims(session)
            console.print(f"  Embedded: {emb_stats['embedded']}/{emb_stats['processed']}")

        # Update positions
        if positions and video_id:
            video = session.query(Videos).filter(
                Videos.youtube_video_id == video_id
            ).first()
            if video:
                console.print(f"\n[dim]Updating positions...[/dim]")
                pos_stats = update_positions_for_video(session, video.id)
                console.print(f"  Positions: {pos_stats['positions_updated']}, Shifts: {pos_stats['shifts']}")

    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"\n[bold red]✗ Error:[/bold red] {e}")
        import traceback
        traceback.print_exc()
        raise typer.Exit(1)
    finally:
        session.close()


@app.command("status")
def enrich_status():
    """Show enrichment pipeline stats."""
    from src.pipeline.enrichment import get_enrich_status

    session = get_session()
    try:
        stats = get_enrich_status(session)

        console.print(f"\n[bold]Videos:[/bold]")
        console.print(f"  Total:                {stats['total_videos']}")
        console.print(f"  Enriched:             [green]{stats['enriched']}[/green]")
        console.print(f"  Pending enrichment:   [yellow]{stats['pending_enrichment']}[/yellow]")

        console.print(f"\n[bold]Claims:[/bold]")
        console.print(f"  Total:     {stats['total_claims']}")
        console.print(f"  Approved:  [green]{stats['approved_claims']}[/green]")
        console.print(f"  Pending:   [yellow]{stats['pending_claims']}[/yellow]")

        console.print(f"\n[bold]Evidence & Embeddings:[/bold]")
        console.print(f"  Evidence spans:  {stats['evidence_spans']}")
        console.print(f"  Embeddings:      {stats['embeddings']}")
        console.print(f"  Positions:       {stats['positions']}")
    finally:
        session.close()
