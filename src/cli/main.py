"""Believable Minds CLI — main entry point."""

import typer

from src.cli.channels import app as channels_app
from src.cli.enrich import app as enrich_app
from src.cli.identify import app as identify_app
from src.cli.people import app as people_app
from src.cli.scan import app as scan_app
from src.cli.topics import app as topics_app
from src.cli.transcribe import app as transcribe_app
from src.cli.brief import brief_app

app = typer.Typer(
    name="bm",
    help="Believable Minds — track what credible minds say.",
    no_args_is_help=True,
)

app.add_typer(people_app, name="people", help="Manage tracked people")
app.add_typer(channels_app, name="channels", help="Manage podcast channels")
app.add_typer(topics_app, name="topics", help="Manage topic taxonomy")
app.add_typer(scan_app, name="scan", help="YouTube video discovery")
app.add_typer(transcribe_app, name="transcribe", help="Transcript extraction")
app.add_typer(identify_app, name="identify", help="Speaker identification")
app.add_typer(enrich_app, name="enrich", help="LLM enrichment & claims")
app.add_typer(brief_app, name="brief", help="Intelligence brief generation")


@app.command()
def seed():
    """Seed the database with initial people, channels, topics, and channel roles."""
    from rich.console import Console

    from src.db.seed import seed_all
    from src.db.session import get_session

    console = Console()
    session = get_session()
    try:
        counts = seed_all(session)
        console.print("\n[bold green]✓ Seed complete![/bold green]")
        for entity, count in counts.items():
            console.print(f"  {entity}: {count} new records")
    except Exception as e:
        session.rollback()
        console.print(f"\n[bold red]✗ Seed failed:[/bold red] {e}")
        raise typer.Exit(1)
    finally:
        session.close()


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", help="Host to bind to"),
    port: int = typer.Option(8000, help="Port to serve on"),
):
    """Start the Believable Minds API server."""
    import uvicorn
    from rich.console import Console

    console = Console()
    console.print(f"\n[bold green]🚀 Starting Believable Minds API[/bold green]")
    console.print(f"   http://{host}:{port}")
    console.print(f"   Docs: http://{host}:{port}/docs\n")

    uvicorn.run("src.api.app:app", host=host, port=port, reload=True)


if __name__ == "__main__":
    app()
