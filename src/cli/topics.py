"""CLI commands for managing the topic taxonomy."""

import typer
from rich.console import Console
from rich.table import Table

from src.db.models import Topics
from src.db.session import get_session

app = typer.Typer(no_args_is_help=True)
console = Console()


@app.command("list")
def list_topics(
    active_only: bool = typer.Option(True, "--active/--all", help="Show only active topics"),
):
    """List all topics in the taxonomy."""
    session = get_session()
    try:
        query = session.query(Topics)
        if active_only:
            query = query.filter(Topics.active == True)  # noqa: E712
        query = query.order_by(Topics.slug)
        topics = query.all()

        table = Table(title="Topic Taxonomy", show_lines=True)
        table.add_column("Slug", style="bold")
        table.add_column("Name")
        table.add_column("Active", justify="center")

        for t in topics:
            table.add_row(t.slug, t.name, "✓" if t.active else "✗")

        console.print(table)
        console.print(f"\nTotal: {len(topics)} topics")
    finally:
        session.close()


@app.command("add")
def add_topic(
    slug: str = typer.Option(..., prompt=True, help="Topic slug (e.g., 'quantum_computing')"),
    name: str = typer.Option(..., prompt=True, help="Human-readable name (e.g., 'Quantum Computing')"),
):
    """Add a new topic to the taxonomy."""
    session = get_session()
    try:
        existing = session.query(Topics).filter(Topics.slug == slug).first()
        if existing:
            console.print(f"[bold yellow]⚠ Topic already exists:[/bold yellow] {slug}")
            raise typer.Exit(1)

        topic = Topics(slug=slug, name=name)
        session.add(topic)
        session.commit()
        console.print(f"[bold green]✓ Added topic:[/bold green] {slug} ({name})")
    except typer.Exit:
        raise
    except Exception as e:
        session.rollback()
        console.print(f"[bold red]✗ Error:[/bold red] {e}")
        raise typer.Exit(1)
    finally:
        session.close()


@app.command("remove")
def remove_topic(
    slug: str = typer.Argument(help="Topic slug to remove"),
):
    """Soft-delete a topic (set active=false)."""
    session = get_session()
    try:
        topic = session.query(Topics).filter(Topics.slug == slug).first()
        if not topic:
            console.print(f"[bold red]✗ Topic not found:[/bold red] {slug}")
            raise typer.Exit(1)

        topic.active = False
        session.commit()
        console.print(f"[bold green]✓ Removed (soft-delete):[/bold green] {slug}")
    except typer.Exit:
        raise
    except Exception as e:
        session.rollback()
        console.print(f"[bold red]✗ Error:[/bold red] {e}")
        raise typer.Exit(1)
    finally:
        session.close()
